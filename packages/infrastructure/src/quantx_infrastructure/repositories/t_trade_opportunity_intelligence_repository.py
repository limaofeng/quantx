"""Persistence boundary for stateful T-trade evaluation evidence and profiles."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
  T_TRADE_EVALUATION_KIND_DIAGNOSTIC,
  T_TRADE_EVALUATION_KIND_MATERIAL,
  TTradeInstrumentProfile,
  TTradeOpportunityEvaluation,
)


class TTradeOpportunityEvaluationRepository:
  """Append and page immutable opportunity evaluations.

  The repository deliberately exposes no update or delete operation. A caller may
  pass ``commit=False`` to include the append in a wider application unit of work.
  """

  def __init__(self, db: AsyncSession) -> None:
    self.db = db

  async def get_by_event_key(
    self,
    event_key: str,
  ) -> Optional[TTradeOpportunityEvaluation]:
    normalized_key = _required_text(event_key, "评估事件键", 160)
    return await self._get_by_event_key(normalized_key)

  async def append_material(
    self,
    *,
    event_key: str,
    account_id: str,
    strategy_run_id: str,
    instrument_code: str,
    evaluated_at: datetime,
    event_type: str,
    policy_version: str,
    schema_version: str,
    payload: dict[str, Any],
    metrics: Optional[dict[str, Any]] = None,
    commit: bool = True,
  ) -> TTradeOpportunityEvaluation:
    return await self._append(
      event_key=event_key,
      account_id=account_id,
      strategy_run_id=strategy_run_id,
      instrument_code=instrument_code,
      evaluated_at=evaluated_at,
      record_kind=T_TRADE_EVALUATION_KIND_MATERIAL,
      event_type=event_type,
      window_started_at=None,
      window_ended_at=None,
      coalesced_count=1,
      policy_version=policy_version,
      schema_version=schema_version,
      payload=payload,
      metrics=metrics,
      commit=commit,
    )

  async def append_coalesced_diagnostic(
    self,
    *,
    event_key: str,
    account_id: str,
    strategy_run_id: str,
    instrument_code: str,
    evaluated_at: datetime,
    event_type: str,
    window_started_at: datetime,
    window_ended_at: datetime,
    coalesced_count: int,
    policy_version: str,
    schema_version: str,
    payload: dict[str, Any],
    metrics: dict[str, Any],
    commit: bool = True,
  ) -> TTradeOpportunityEvaluation:
    return await self._append(
      event_key=event_key,
      account_id=account_id,
      strategy_run_id=strategy_run_id,
      instrument_code=instrument_code,
      evaluated_at=evaluated_at,
      record_kind=T_TRADE_EVALUATION_KIND_DIAGNOSTIC,
      event_type=event_type,
      window_started_at=window_started_at,
      window_ended_at=window_ended_at,
      coalesced_count=coalesced_count,
      policy_version=policy_version,
      schema_version=schema_version,
      payload=payload,
      metrics=metrics,
      commit=commit,
    )

  async def list_evaluations(
    self,
    *,
    account_id: str,
    limit: int = 100,
    instrument_code: Optional[str] = None,
    strategy_run_id: Optional[str] = None,
    record_kind: Optional[str] = None,
    started_at: Optional[datetime] = None,
    ended_at: Optional[datetime] = None,
    cursor_evaluated_at: Optional[datetime] = None,
    cursor_id: Optional[str] = None,
  ) -> list[TTradeOpportunityEvaluation]:
    """Return newest-first results using ``(evaluated_at, id)`` keyset paging."""

    normalized_account_id = _required_text(account_id, "证券账户", 50)
    normalized_limit = int(limit)
    if normalized_limit < 1 or normalized_limit > 500:
      raise ValueError("评估查询条数必须在 1 到 500 之间")
    if (cursor_evaluated_at is None) != (cursor_id is None):
      raise ValueError("评估游标必须同时包含 evaluated_at 和 id")

    conditions = [TTradeOpportunityEvaluation.account_id == normalized_account_id]
    if instrument_code is not None:
      conditions.append(
        TTradeOpportunityEvaluation.instrument_code == _instrument_code(instrument_code)
      )
    if strategy_run_id is not None:
      conditions.append(
        TTradeOpportunityEvaluation.strategy_run_id
        == _required_text(strategy_run_id, "策略运行标识", 36)
      )
    if record_kind is not None:
      normalized_kind = _evaluation_kind(record_kind)
      conditions.append(TTradeOpportunityEvaluation.record_kind == normalized_kind)
    if started_at is not None:
      conditions.append(
        TTradeOpportunityEvaluation.evaluated_at >= _storage_time(started_at)
      )
    if ended_at is not None:
      conditions.append(
        TTradeOpportunityEvaluation.evaluated_at <= _storage_time(ended_at)
      )
    if cursor_evaluated_at is not None and cursor_id is not None:
      normalized_cursor_at = _storage_time(cursor_evaluated_at)
      normalized_cursor_id = _required_text(cursor_id, "评估游标标识", 36)
      conditions.append(
        or_(
          TTradeOpportunityEvaluation.evaluated_at < normalized_cursor_at,
          and_(
            TTradeOpportunityEvaluation.evaluated_at == normalized_cursor_at,
            TTradeOpportunityEvaluation.id < normalized_cursor_id,
          ),
        )
      )

    result = await self.db.execute(
      select(TTradeOpportunityEvaluation)
      .where(*conditions)
      .order_by(
        TTradeOpportunityEvaluation.evaluated_at.desc(),
        TTradeOpportunityEvaluation.id.desc(),
      )
      .limit(normalized_limit)
    )
    return list(result.scalars().all())

  async def _append(
    self,
    *,
    event_key: str,
    account_id: str,
    strategy_run_id: str,
    instrument_code: str,
    evaluated_at: datetime,
    record_kind: str,
    event_type: str,
    window_started_at: Optional[datetime],
    window_ended_at: Optional[datetime],
    coalesced_count: int,
    policy_version: str,
    schema_version: str,
    payload: dict[str, Any],
    metrics: Optional[dict[str, Any]],
    commit: bool,
  ) -> TTradeOpportunityEvaluation:
    normalized_key = _required_text(event_key, "评估事件键", 160)
    normalized_account_id = _required_text(account_id, "证券账户", 50)
    normalized_run_id = _required_text(strategy_run_id, "策略运行标识", 36)
    normalized_instrument = _instrument_code(instrument_code)
    normalized_evaluated_at = _storage_time(evaluated_at)
    normalized_kind = _evaluation_kind(record_kind)
    normalized_event_type = _required_text(event_type, "评估事件类型", 64)
    normalized_policy_version = _required_text(
      policy_version,
      "评估策略版本",
      64,
    )
    normalized_schema_version = _required_text(
      schema_version,
      "评估结构版本",
      32,
    )
    normalized_payload = _json_object(payload, "评估载荷")
    normalized_candidate_id = (
      _evaluation_candidate_id(normalized_payload)
      if normalized_kind == T_TRADE_EVALUATION_KIND_MATERIAL
      else None
    )
    normalized_metrics = _json_object(metrics or {}, "评估指标")
    normalized_count = int(coalesced_count)
    normalized_window_started_at = (
      _storage_time(window_started_at) if window_started_at is not None else None
    )
    normalized_window_ended_at = (
      _storage_time(window_ended_at) if window_ended_at is not None else None
    )
    _validate_evaluation_window(
      record_kind=normalized_kind,
      evaluated_at=normalized_evaluated_at,
      window_started_at=normalized_window_started_at,
      window_ended_at=normalized_window_ended_at,
      coalesced_count=normalized_count,
    )

    fingerprint = _sha256(
      {
        "account_id": normalized_account_id,
        "strategy_run_id": normalized_run_id,
        "instrument_code": normalized_instrument,
        "candidate_id": normalized_candidate_id,
        "evaluated_at": normalized_evaluated_at,
        "record_kind": normalized_kind,
        "event_type": normalized_event_type,
        "window_started_at": normalized_window_started_at,
        "window_ended_at": normalized_window_ended_at,
        "coalesced_count": normalized_count,
        "policy_version": normalized_policy_version,
        "schema_version": normalized_schema_version,
        "payload": normalized_payload,
        "metrics": normalized_metrics,
      }
    )
    existing = await self._get_by_event_key(normalized_key)
    if existing is not None:
      return _same_evaluation_or_raise(existing, fingerprint)

    row = TTradeOpportunityEvaluation(
      event_key=normalized_key,
      account_id=normalized_account_id,
      strategy_run_id=normalized_run_id,
      instrument_code=normalized_instrument,
      candidate_id=normalized_candidate_id,
      evaluated_at=normalized_evaluated_at,
      record_kind=normalized_kind,
      event_type=normalized_event_type,
      window_started_at=normalized_window_started_at,
      window_ended_at=normalized_window_ended_at,
      coalesced_count=normalized_count,
      policy_version=normalized_policy_version,
      schema_version=normalized_schema_version,
      content_fingerprint=fingerprint,
      payload=normalized_payload,
      metrics=normalized_metrics,
    )
    self.db.add(row)
    if not commit:
      await self.db.flush()
      return row

    try:
      await self.db.commit()
    except IntegrityError:
      await self.db.rollback()
      existing = await self._get_by_event_key(normalized_key)
      if existing is None:
        raise
      return _same_evaluation_or_raise(existing, fingerprint)

    await self.db.refresh(row)
    return row

  async def _get_by_event_key(
    self,
    event_key: str,
  ) -> Optional[TTradeOpportunityEvaluation]:
    result = await self.db.execute(
      select(TTradeOpportunityEvaluation).where(
        TTradeOpportunityEvaluation.event_key == event_key
      )
    )
    return result.scalar_one_or_none()


class TTradeInstrumentProfileRepository:
  """Append and retrieve immutable point-in-time instrument profiles."""

  def __init__(self, db: AsyncSession) -> None:
    self.db = db

  async def save_profile(
    self,
    *,
    instrument_code: str,
    as_of: datetime,
    profile: dict[str, Any],
    schema_version: str,
    version: str,
    fingerprint: str,
    metrics: dict[str, Any],
    data_manifest: dict[str, Any],
    commit: bool = True,
  ) -> TTradeInstrumentProfile:
    normalized_instrument = _instrument_code(instrument_code)
    normalized_as_of = _storage_time(as_of)
    normalized_profile = _json_object(profile, "标的画像")
    normalized_schema_version = _required_text(
      schema_version,
      "画像结构版本",
      32,
    )
    normalized_version = _required_text(version, "画像版本", 64)
    normalized_fingerprint = _sha256_text(fingerprint, "画像指纹")
    normalized_metrics = _json_object(metrics, "画像指标")
    normalized_manifest = _json_object(data_manifest, "画像数据清单")
    _validate_manifest_as_of(normalized_manifest, normalized_as_of)

    existing = await self._get_by_fingerprint(
      normalized_instrument,
      normalized_fingerprint,
    )
    if existing is not None:
      return _same_profile_or_raise(
        existing,
        as_of=normalized_as_of,
        profile=normalized_profile,
        schema_version=normalized_schema_version,
        version=normalized_version,
        metrics=normalized_metrics,
        data_manifest=normalized_manifest,
      )

    coordinate = await self._get_by_coordinate(
      instrument_code=normalized_instrument,
      as_of=normalized_as_of,
      schema_version=normalized_schema_version,
      version=normalized_version,
    )
    if coordinate is not None:
      raise ValueError("标的画像时点版本碰撞且指纹不一致")

    row = TTradeInstrumentProfile(
      instrument_code=normalized_instrument,
      as_of=normalized_as_of,
      profile=normalized_profile,
      schema_version=normalized_schema_version,
      version=normalized_version,
      fingerprint=normalized_fingerprint,
      metrics=normalized_metrics,
      data_manifest=normalized_manifest,
    )
    self.db.add(row)
    if not commit:
      await self.db.flush()
      return row

    try:
      await self.db.commit()
    except IntegrityError:
      await self.db.rollback()
      existing = await self._get_by_fingerprint(
        normalized_instrument,
        normalized_fingerprint,
      )
      if existing is not None:
        return _same_profile_or_raise(
          existing,
          as_of=normalized_as_of,
          profile=normalized_profile,
          schema_version=normalized_schema_version,
          version=normalized_version,
          metrics=normalized_metrics,
          data_manifest=normalized_manifest,
        )
      coordinate = await self._get_by_coordinate(
        instrument_code=normalized_instrument,
        as_of=normalized_as_of,
        schema_version=normalized_schema_version,
        version=normalized_version,
      )
      if coordinate is not None:
        raise ValueError("标的画像时点版本碰撞且指纹不一致")
      raise

    await self.db.refresh(row)
    return row

  async def get_by_fingerprint(
    self,
    *,
    instrument_code: str,
    fingerprint: str,
  ) -> Optional[TTradeInstrumentProfile]:
    return await self._get_by_fingerprint(
      _instrument_code(instrument_code),
      _sha256_text(fingerprint, "画像指纹"),
    )

  async def latest_at_or_before(
    self,
    *,
    instrument_code: str,
    as_of: datetime,
    schema_version: str,
    version: Optional[str] = None,
  ) -> Optional[TTradeInstrumentProfile]:
    conditions = [
      TTradeInstrumentProfile.instrument_code == _instrument_code(instrument_code),
      TTradeInstrumentProfile.as_of <= _storage_time(as_of),
      TTradeInstrumentProfile.schema_version
      == _required_text(schema_version, "画像结构版本", 32),
    ]
    if version is not None:
      conditions.append(
        TTradeInstrumentProfile.version == _required_text(version, "画像版本", 64)
      )
    result = await self.db.execute(
      select(TTradeInstrumentProfile)
      .where(*conditions)
      .order_by(
        TTradeInstrumentProfile.as_of.desc(),
        TTradeInstrumentProfile.created_at.desc(),
        TTradeInstrumentProfile.id.desc(),
      )
      .limit(1)
    )
    return result.scalar_one_or_none()

  async def _get_by_fingerprint(
    self,
    instrument_code: str,
    fingerprint: str,
  ) -> Optional[TTradeInstrumentProfile]:
    result = await self.db.execute(
      select(TTradeInstrumentProfile).where(
        TTradeInstrumentProfile.instrument_code == instrument_code,
        TTradeInstrumentProfile.fingerprint == fingerprint,
      )
    )
    return result.scalar_one_or_none()

  async def _get_by_coordinate(
    self,
    *,
    instrument_code: str,
    as_of: datetime,
    schema_version: str,
    version: str,
  ) -> Optional[TTradeInstrumentProfile]:
    result = await self.db.execute(
      select(TTradeInstrumentProfile).where(
        TTradeInstrumentProfile.instrument_code == instrument_code,
        TTradeInstrumentProfile.as_of == as_of,
        TTradeInstrumentProfile.schema_version == schema_version,
        TTradeInstrumentProfile.version == version,
      )
    )
    return result.scalar_one_or_none()


def _same_evaluation_or_raise(
  existing: TTradeOpportunityEvaluation,
  fingerprint: str,
) -> TTradeOpportunityEvaluation:
  if existing.content_fingerprint != fingerprint:
    raise ValueError("做 T 机会评估事件键碰撞且内容不一致")
  return existing


def _evaluation_candidate_id(payload: dict[str, Any]) -> str | None:
  raw_snapshot = payload.get("signal_snapshot")
  snapshot = raw_snapshot if isinstance(raw_snapshot, dict) else {}
  normalized = str(snapshot.get("candidate_id") or "").strip()
  if not normalized:
    return None
  if len(normalized) > 128:
    raise ValueError("做 T 候选标识长度不能超过 128")
  return normalized


def _same_profile_or_raise(
  existing: TTradeInstrumentProfile,
  *,
  as_of: datetime,
  profile: dict[str, Any],
  schema_version: str,
  version: str,
  metrics: dict[str, Any],
  data_manifest: dict[str, Any],
) -> TTradeInstrumentProfile:
  expected = _canonical_json(
    {
      "as_of": as_of,
      "profile": profile,
      "schema_version": schema_version,
      "version": version,
      "metrics": metrics,
      "data_manifest": data_manifest,
    }
  )
  actual = _canonical_json(
    {
      "as_of": existing.as_of,
      "profile": existing.profile,
      "schema_version": existing.schema_version,
      "version": existing.version,
      "metrics": existing.metrics,
      "data_manifest": existing.data_manifest,
    }
  )
  if actual != expected:
    raise ValueError("标的画像指纹碰撞且内容不一致")
  return existing


def _validate_evaluation_window(
  *,
  record_kind: str,
  evaluated_at: datetime,
  window_started_at: Optional[datetime],
  window_ended_at: Optional[datetime],
  coalesced_count: int,
) -> None:
  if coalesced_count < 1:
    raise ValueError("评估合并数量必须大于零")
  if record_kind == T_TRADE_EVALUATION_KIND_MATERIAL:
    if (
      coalesced_count != 1
      or window_started_at is not None
      or window_ended_at is not None
    ):
      raise ValueError("物化评估不能携带诊断合并窗口")
    return
  if window_started_at is None or window_ended_at is None:
    raise ValueError("合并诊断必须携带完整时间窗口")
  if window_started_at > window_ended_at:
    raise ValueError("合并诊断开始时间不能晚于结束时间")
  if window_ended_at > evaluated_at:
    raise ValueError("合并诊断窗口不能晚于评估时间")


def _validate_manifest_as_of(
  data_manifest: dict[str, Any],
  as_of: datetime,
) -> None:
  source_max_at = data_manifest.get("source_max_at")
  if source_max_at is None or source_max_at == "":
    return
  parsed = _parse_manifest_datetime(source_max_at)
  if _storage_time(parsed) > as_of:
    raise ValueError("标的画像数据截止时间不能晚于画像时点")


def _parse_manifest_datetime(value: Any) -> datetime:
  if isinstance(value, datetime):
    return value
  try:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
  except ValueError as exc:
    raise ValueError("标的画像数据截止时间格式无效") from exc


def _evaluation_kind(value: str) -> str:
  normalized = _required_text(value, "评估记录类型", 24).upper()
  if normalized not in {
    T_TRADE_EVALUATION_KIND_MATERIAL,
    T_TRADE_EVALUATION_KIND_DIAGNOSTIC,
  }:
    raise ValueError("评估记录类型无效")
  return normalized


def _instrument_code(value: str) -> str:
  return _required_text(value, "证券代码", 20).upper()


def _required_text(value: Any, label: str, maximum: int) -> str:
  normalized = str(value or "").strip()
  if not normalized:
    raise ValueError(f"{label}不能为空")
  if len(normalized) > maximum:
    raise ValueError(f"{label}长度不能超过 {maximum}")
  return normalized


def _sha256_text(value: str, label: str) -> str:
  normalized = _required_text(value, label, 64).lower()
  if len(normalized) != 64 or any(
    character not in "0123456789abcdef" for character in normalized
  ):
    raise ValueError(f"{label}必须是 64 位 SHA-256 十六进制值")
  return normalized


def _storage_time(value: datetime) -> datetime:
  if not isinstance(value, datetime):
    raise ValueError("时间字段必须是 datetime")
  return time_utils.to_shanghai(value)


def _json_object(value: Any, label: str) -> dict[str, Any]:
  if not isinstance(value, dict):
    raise ValueError(f"{label}必须是 JSON 对象")
  normalized = dict(value)
  _canonical_json(normalized, label=label, allow_datetime=False)
  return normalized


def _sha256(value: Any) -> str:
  encoded = _canonical_json(value).encode("utf-8")
  return hashlib.sha256(encoded).hexdigest()


def _canonical_json(
  value: Any,
  *,
  label: str = "内容",
  allow_datetime: bool = True,
) -> str:
  def _default(item: Any) -> Any:
    if allow_datetime and isinstance(item, datetime):
      return _storage_time(item).isoformat(timespec="microseconds")
    raise TypeError(f"unsupported type: {type(item).__name__}")

  try:
    return json.dumps(
      value,
      ensure_ascii=False,
      sort_keys=True,
      separators=(",", ":"),
      allow_nan=False,
      default=_default,
    )
  except (TypeError, ValueError) as exc:
    raise ValueError(f"{label}不是有效的 JSON 数据") from exc
