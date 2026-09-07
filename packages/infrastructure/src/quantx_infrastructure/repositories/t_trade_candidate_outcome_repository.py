"""Persistence contract for causal T-trade candidate outcome aggregates."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Mapping, Optional

from quantx_contracts import (
  ExecutionEnvironment,
  ExecutionOwnerRef,
  ExecutionOwnerType,
)
from quantx_domain.trading.t_trade_candidate_outcome import (
  CANDIDATE_OUTCOME_SCHEMA_VERSION,
  CandidateOutcomeState,
  CandidateOutcomeStatus,
)
from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.strategy_run import StrategyRun
from quantx_infrastructure.models.t_assistant_execution import TAssistantExecutionRecord
from quantx_infrastructure.models.t_trade_candidate_outcome import (
  TTradeCandidateOutcome,
)


class CandidateOutcomeConcurrencyError(RuntimeError):
  pass


_MAX_UNFINALIZED_PAGE_SIZE = 256


class TTradeCandidateOutcomeRepository:
  """Create once and advance by optimistic compare-and-swap."""

  def __init__(self, db: AsyncSession) -> None:
    self.db = db

  async def delete_for_run(
    self,
    strategy_run_id: str,
    *,
    commit: bool = True,
  ) -> int:
    """Delete run-local derived outcomes before replaying a new version."""

    normalized_run_id = str(strategy_run_id or "").strip()
    if not normalized_run_id:
      raise ValueError("策略运行标识不能为空")
    result = await self.db.execute(
      delete(TTradeCandidateOutcome).where(
        TTradeCandidateOutcome.owner_type == ExecutionOwnerType.STRATEGY_RUN.value,
        TTradeCandidateOutcome.strategy_run_id == normalized_run_id
      )
    )
    if commit:
      await self.db.commit()
    return int(result.rowcount or 0)

  async def get(
    self,
    *,
    strategy_run_id: str,
    candidate_id: str,
  ) -> Optional[TTradeCandidateOutcome]:
    result = await self.db.execute(
      select(TTradeCandidateOutcome).where(
        TTradeCandidateOutcome.owner_type == ExecutionOwnerType.STRATEGY_RUN.value,
        TTradeCandidateOutcome.strategy_run_id == strategy_run_id,
        TTradeCandidateOutcome.candidate_id == candidate_id,
      )
    )
    return result.scalar_one_or_none()

  async def get_for_owner(
    self,
    *,
    execution_ref: ExecutionOwnerRef | Mapping[str, object],
    execution_environment: ExecutionEnvironment | str,
    candidate_id: str,
  ) -> Optional[TTradeCandidateOutcome]:
    owner = _owner_ref(execution_ref)
    environment = _environment(execution_environment, owner=owner)
    result = await self.db.execute(
      select(TTradeCandidateOutcome).where(
        TTradeCandidateOutcome.owner_type == owner.owner_type.value,
        TTradeCandidateOutcome.owner_id == owner.owner_id,
        TTradeCandidateOutcome.environment == environment,
        TTradeCandidateOutcome.candidate_id == str(candidate_id).strip(),
      )
    )
    return result.scalar_one_or_none()

  async def list_observing(
    self,
    *,
    strategy_run_id: str,
    instrument_code: str | None = None,
  ) -> list[TTradeCandidateOutcome]:
    statement = select(TTradeCandidateOutcome).where(
      TTradeCandidateOutcome.owner_type == ExecutionOwnerType.STRATEGY_RUN.value,
      TTradeCandidateOutcome.strategy_run_id == strategy_run_id,
      or_(
        TTradeCandidateOutcome.status == CandidateOutcomeStatus.OBSERVING.value,
        TTradeCandidateOutcome.post_fill_status == "OBSERVING",
      ),
    )
    if instrument_code is not None:
      statement = statement.where(
        TTradeCandidateOutcome.instrument_code == instrument_code
      )
    result = await self.db.execute(
      statement.order_by(
        TTradeCandidateOutcome.source_time_ms,
        TTradeCandidateOutcome.tick_ordinal,
      )
    )
    return list(result.scalars().all())

  async def list_observing_for_owner(
    self,
    *,
    execution_ref: ExecutionOwnerRef | Mapping[str, object],
    execution_environment: ExecutionEnvironment | str,
    instrument_code: str | None = None,
  ) -> list[TTradeCandidateOutcome]:
    owner = _owner_ref(execution_ref)
    environment = _environment(execution_environment, owner=owner)
    statement = select(TTradeCandidateOutcome).where(
      TTradeCandidateOutcome.owner_type == owner.owner_type.value,
      TTradeCandidateOutcome.owner_id == owner.owner_id,
      TTradeCandidateOutcome.environment == environment,
      or_(
        TTradeCandidateOutcome.status == CandidateOutcomeStatus.OBSERVING.value,
        TTradeCandidateOutcome.post_fill_status == "OBSERVING",
      ),
    )
    if instrument_code is not None:
      statement = statement.where(
        TTradeCandidateOutcome.instrument_code
        == str(instrument_code).strip().upper()
      )
    result = await self.db.execute(
      statement.order_by(
        TTradeCandidateOutcome.source_time_ms,
        TTradeCandidateOutcome.tick_ordinal,
      )
    )
    return list(result.scalars().all())

  async def list_unfinalized(
    self,
    *,
    strategy_run_id: str,
    after_candidate_id: str | None,
    limit: int,
  ) -> list[TTradeCandidateOutcome]:
    """Read one bounded keyset page of outcome rows still needing closure.

    ``candidate_id`` is immutable and unique within a strategy run, so the
    existing run/candidate unique index is also the stable seek key.  Callers
    must advance ``after_candidate_id`` from the last row in each page; this
    keeps both the database result and application memory bounded even for a
    run with a large number of candidates.
    """

    normalized_run_id = str(strategy_run_id or "").strip()
    if not normalized_run_id:
      raise ValueError("策略运行标识不能为空")
    normalized_limit = int(limit)
    if not 1 <= normalized_limit <= _MAX_UNFINALIZED_PAGE_SIZE:
      raise ValueError(
        f"候选结果终态分页大小必须在 1..{_MAX_UNFINALIZED_PAGE_SIZE} 之间"
      )
    conditions = [
      TTradeCandidateOutcome.owner_type == ExecutionOwnerType.STRATEGY_RUN.value,
      TTradeCandidateOutcome.strategy_run_id == normalized_run_id,
      or_(
        TTradeCandidateOutcome.status == CandidateOutcomeStatus.OBSERVING.value,
        TTradeCandidateOutcome.post_fill_status.in_(("WAITING_ENTRY", "OBSERVING")),
      ),
    ]
    normalized_cursor = str(after_candidate_id or "").strip()
    if normalized_cursor:
      conditions.append(TTradeCandidateOutcome.candidate_id > normalized_cursor)
    result = await self.db.execute(
      select(TTradeCandidateOutcome)
      .where(*conditions)
      .order_by(TTradeCandidateOutcome.candidate_id)
      .limit(normalized_limit)
    )
    return list(result.scalars().all())

  async def list_unfinalized_for_owner(
    self,
    *,
    execution_ref: ExecutionOwnerRef | Mapping[str, object],
    execution_environment: ExecutionEnvironment | str,
    after_candidate_id: str | None,
    limit: int,
  ) -> list[TTradeCandidateOutcome]:
    owner = _owner_ref(execution_ref)
    environment = _environment(execution_environment, owner=owner)
    normalized_limit = int(limit)
    if not 1 <= normalized_limit <= _MAX_UNFINALIZED_PAGE_SIZE:
      raise ValueError(
        f"候选结果终态分页大小必须在 1..{_MAX_UNFINALIZED_PAGE_SIZE} 之间"
      )
    conditions = [
      TTradeCandidateOutcome.owner_type == owner.owner_type.value,
      TTradeCandidateOutcome.owner_id == owner.owner_id,
      TTradeCandidateOutcome.environment == environment,
      or_(
        TTradeCandidateOutcome.status == CandidateOutcomeStatus.OBSERVING.value,
        TTradeCandidateOutcome.post_fill_status.in_(("WAITING_ENTRY", "OBSERVING")),
      ),
    ]
    normalized_cursor = str(after_candidate_id or "").strip()
    if normalized_cursor:
      conditions.append(TTradeCandidateOutcome.candidate_id > normalized_cursor)
    result = await self.db.execute(
      select(TTradeCandidateOutcome)
      .where(*conditions)
      .order_by(TTradeCandidateOutcome.candidate_id)
      .limit(normalized_limit)
    )
    return list(result.scalars().all())

  async def list_for_scope(
    self,
    *,
    account_id: str,
    started_at: datetime,
    ended_at: datetime,
    strategy_run_id: str | None = None,
    instrument_code: str | None = None,
    limit: int = 50_001,
  ) -> list[TTradeCandidateOutcome]:
    """Read bounded aggregates for diagnostics without exposing raw Tick facts."""

    normalized_account = str(account_id or "").strip()
    if not normalized_account:
      raise ValueError("候选结果诊断缺少证券账户")
    start = time_utils.to_shanghai(started_at)
    end = time_utils.to_shanghai(ended_at)
    if start >= end:
      raise ValueError("候选结果诊断开始时间必须早于结束时间")
    normalized_limit = int(limit)
    if normalized_limit < 1 or normalized_limit > 50_001:
      raise ValueError("候选结果诊断条数必须在 1 到 50001 之间")
    conditions = [
      TTradeCandidateOutcome.account_id == normalized_account,
      TTradeCandidateOutcome.owner_type == "STRATEGY_RUN",
      TTradeCandidateOutcome.candidate_at >= start,
      TTradeCandidateOutcome.candidate_at <= end,
    ]
    if strategy_run_id:
      conditions.append(
        TTradeCandidateOutcome.strategy_run_id == str(strategy_run_id).strip()
      )
    if instrument_code:
      conditions.append(
        TTradeCandidateOutcome.instrument_code == str(instrument_code).strip().upper()
      )
    result = await self.db.execute(
      select(TTradeCandidateOutcome)
      .where(*conditions)
      .order_by(
        TTradeCandidateOutcome.candidate_at,
        TTradeCandidateOutcome.source_time_ms,
        TTradeCandidateOutcome.tick_ordinal,
        TTradeCandidateOutcome.id,
      )
      .limit(normalized_limit)
    )
    return list(result.scalars().all())

  async def create_or_get(
    self,
    *,
    account_id: str,
    state: CandidateOutcomeState,
    execution_environment: str | None = None,
    commit: bool = True,
  ) -> TTradeCandidateOutcome:
    normalized_account = str(account_id or "").strip()
    if not normalized_account:
      raise ValueError("候选结果缺少证券账户")
    definition = state.definition
    owner, environment = await self._resolve_definition_scope(
      definition,
      explicit_environment=execution_environment,
      account_id=normalized_account,
    )
    existing = await self.get_for_owner(
      execution_ref=owner,
      execution_environment=environment,
      candidate_id=state.definition.candidate_id,
    )
    payload = state.to_dict()
    fingerprint = _fingerprint(payload)
    if existing is not None:
      if str(existing.account_id or "").strip() != normalized_account:
        raise ValueError("同一候选标识对应的证券账户不一致")
      _verify_frozen_identity(existing, state)
      return existing
    row = TTradeCandidateOutcome(
      account_id=normalized_account,
      owner_type=owner.owner_type.value,
      owner_id=owner.owner_id,
      environment=environment,
      strategy_run_id=definition.strategy_run_id,
      instrument_code=definition.instrument_code,
      candidate_id=definition.candidate_id,
      candidate_fingerprint=definition.candidate_fingerprint,
      candidate_at=_from_ms(definition.source_time_ms),
      source_time_ms=definition.source_time_ms,
      tick_ordinal=definition.tick_ordinal,
      continuity_generation=definition.continuity_generation,
      reference_price=definition.reference_price,
      policy_version=definition.policy_version,
      feature_schema_version=definition.feature_schema_version,
      profile_version=definition.profile_version,
      profile_fingerprint=definition.profile_fingerprint,
      outcome_schema_version=CANDIDATE_OUTCOME_SCHEMA_VERSION,
      status=state.status.value,
      post_fill_status=state.post_fill.status.value,
      unavailable_reason=None,
      state=payload,
      content_fingerprint=fingerprint,
      state_version=1,
      finalized_at=None,
    )
    self.db.add(row)
    if not commit:
      await self.db.flush()
      return row
    try:
      await self.db.commit()
    except IntegrityError:
      await self.db.rollback()
      existing = await self.get_for_owner(
        execution_ref=owner,
        execution_environment=environment,
        candidate_id=definition.candidate_id,
      )
      if existing is None:
        raise
      if str(existing.account_id or "").strip() != normalized_account:
        raise ValueError("同一候选标识对应的证券账户不一致")
      _verify_frozen_identity(existing, state)
      return existing
    await self.db.refresh(row)
    return row

  async def save(
    self,
    *,
    state: CandidateOutcomeState,
    expected_version: int,
    commit: bool = True,
  ) -> TTradeCandidateOutcome:
    owner, environment = await self._resolve_definition_scope(
      state.definition,
      explicit_environment=state.definition.execution_environment,
    )
    payload = state.to_dict()
    fingerprint = _fingerprint(payload)
    values = {
      "state": payload,
      "content_fingerprint": fingerprint,
      "status": state.status.value,
      "post_fill_status": state.post_fill.status.value,
      "unavailable_reason": (
        state.unavailable_reason.value if state.unavailable_reason else None
      ),
      "state_version": expected_version + 1,
      "finalized_at": (
        _from_ms(state.finalized_at_ms) if state.finalized_at_ms is not None else None
      ),
      "updated_at": time_utils.now(),
    }
    result = await self.db.execute(
      update(TTradeCandidateOutcome)
      .where(
        TTradeCandidateOutcome.owner_type == owner.owner_type.value,
        TTradeCandidateOutcome.owner_id == owner.owner_id,
        TTradeCandidateOutcome.environment == environment,
        TTradeCandidateOutcome.candidate_id == state.definition.candidate_id,
        TTradeCandidateOutcome.state_version == expected_version,
      )
      .values(**values)
    )
    if result.rowcount != 1:
      if commit:
        await self.db.rollback()
      current = await self.get_for_owner(
        execution_ref=owner,
        execution_environment=environment,
        candidate_id=state.definition.candidate_id,
      )
      if current is not None and current.content_fingerprint == fingerprint:
        return current
      raise CandidateOutcomeConcurrencyError(
        "候选结果状态版本冲突，必须重新读取后重放事实"
      )
    if commit:
      await self.db.commit()
    current = await self.get_for_owner(
      execution_ref=owner,
      execution_environment=environment,
      candidate_id=state.definition.candidate_id,
    )
    if current is None:
      raise RuntimeError("候选结果更新后无法重新读取")
    return current

  @staticmethod
  def state_from_row(row: TTradeCandidateOutcome) -> CandidateOutcomeState:
    payload = dict(row.state)
    definition = dict(payload.get("definition") or {})
    definition.update(
      execution_ref={
        "owner_type": str(row.owner_type),
        "owner_id": str(row.owner_id),
      },
      execution_environment=str(row.environment),
      strategy_run_id=(str(row.strategy_run_id) if row.strategy_run_id else None),
    )
    payload["definition"] = definition
    return CandidateOutcomeState.from_dict(payload)

  async def _resolve_definition_scope(
    self,
    definition: object,
    *,
    explicit_environment: ExecutionEnvironment | str | None,
    account_id: str | None = None,
  ) -> tuple[ExecutionOwnerRef, str]:
    owner = _owner_ref(getattr(definition, "execution_ref", None))
    definition_environment = getattr(definition, "execution_environment", None)
    if (
      explicit_environment is not None
      and definition_environment is not None
      and ExecutionEnvironment(explicit_environment)
      is not ExecutionEnvironment(definition_environment)
    ):
      raise ValueError("候选结果 execution environment 与冻结定义不一致")
    raw_environment = explicit_environment or definition_environment
    if owner.owner_type is ExecutionOwnerType.STRATEGY_RUN:
      if raw_environment is not None:
        environment = _environment(raw_environment, owner=owner)
      else:
        result = await self.db.execute(
          select(TTradeCandidateOutcome.environment).where(
            TTradeCandidateOutcome.owner_type
            == ExecutionOwnerType.STRATEGY_RUN.value,
            TTradeCandidateOutcome.owner_id == owner.owner_id,
            TTradeCandidateOutcome.candidate_id
            == str(getattr(definition, "candidate_id", "")),
          )
        )
        persisted_environment = result.scalar_one_or_none()
        environment = (
          _environment(persisted_environment, owner=owner)
          if persisted_environment is not None
          else await self._strategy_run_environment(owner.owner_id)
        )
    else:
      environment = _environment(raw_environment, owner=owner)
      result = await self.db.execute(
        select(
          TAssistantExecutionRecord.account_id,
          TAssistantExecutionRecord.environment,
        ).where(TAssistantExecutionRecord.execution_id == owner.owner_id)
      )
      persisted = result.one_or_none()
      if persisted is None:
        raise ValueError("T assistant execution 不存在")
      persisted_account, persisted_environment = persisted
      if str(persisted_environment).upper() != environment or (
        account_id is not None and str(persisted_account) != account_id
      ):
        raise ValueError("T assistant execution 与候选结果账户或 environment 不一致")
    return owner, environment

  async def _strategy_run_environment(self, strategy_run_id: str) -> str:
    result = await self.db.execute(
      select(StrategyRun.mode).where(StrategyRun.id == strategy_run_id)
    )
    mode = result.scalar_one_or_none()
    environment = str(getattr(mode, "value", mode) or "").upper()
    if environment not in {"PAPER", "LIVE", "BACKTEST"}:
      raise ValueError("策略运行无法证明候选结果 execution environment")
    return environment


def _verify_frozen_identity(
  row: TTradeCandidateOutcome,
  state: CandidateOutcomeState,
) -> None:
  definition = state.definition
  expected = (
    definition.execution_ref.owner_type.value,
    definition.execution_ref.owner_id,
    (
      definition.execution_environment.value
      if definition.execution_environment is not None
      else row.environment
    ),
    definition.strategy_run_id,
    definition.candidate_fingerprint,
    definition.instrument_code,
    definition.source_time_ms,
    definition.tick_ordinal,
    definition.continuity_generation,
    definition.reference_price,
    definition.policy_version,
    definition.feature_schema_version,
    definition.profile_version,
    definition.profile_fingerprint,
  )
  actual = (
    row.owner_type,
    row.owner_id,
    row.environment,
    row.strategy_run_id,
    row.candidate_fingerprint,
    row.instrument_code,
    row.source_time_ms,
    row.tick_ordinal,
    row.continuity_generation,
    row.reference_price,
    row.policy_version,
    row.feature_schema_version,
    row.profile_version,
    row.profile_fingerprint,
  )
  if actual != expected:
    raise ValueError("同一候选标识对应的冻结身份不一致")


def _owner_ref(
  value: ExecutionOwnerRef | Mapping[str, object] | None,
) -> ExecutionOwnerRef:
  if isinstance(value, ExecutionOwnerRef):
    owner = value
  elif isinstance(value, Mapping):
    owner = ExecutionOwnerRef.from_mapping(value)
  else:
    raise ValueError("候选结果缺少 execution_ref")
  if owner.owner_type not in {
    ExecutionOwnerType.STRATEGY_RUN,
    ExecutionOwnerType.T_ASSISTANT_EXECUTION,
  }:
    raise ValueError("候选结果 owner_type 无效")
  return owner


def _environment(
  value: ExecutionEnvironment | str | None,
  *,
  owner: ExecutionOwnerRef,
) -> str:
  try:
    environment = ExecutionEnvironment(value)
  except (TypeError, ValueError) as exc:
    raise ValueError("候选结果 execution environment 无效") from exc
  if (
    owner.owner_type is ExecutionOwnerType.T_ASSISTANT_EXECUTION
    and environment is not ExecutionEnvironment.PAPER
  ):
    raise ValueError("P3 T assistant 候选结果只能写入 PAPER namespace")
  return environment.value


def _fingerprint(payload: dict) -> str:
  canonical = json.dumps(
    payload,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
  )
  return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _from_ms(value: int) -> datetime:
  aware = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
  return time_utils.to_shanghai(aware)
