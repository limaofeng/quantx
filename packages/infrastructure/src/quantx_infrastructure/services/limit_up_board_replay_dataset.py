"""Immutable, content-addressed inputs for account-level board replay."""

from __future__ import annotations

import bisect
import gzip
import hashlib
import json
import logging
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from datetime import time as dt_time
from decimal import Decimal
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Optional, Sequence

from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.repositories.first_board_promotion_repository import (
  FirstBoardPromotionRepository,
)
from quantx_infrastructure.repositories.limit_up_board_replay_repository import (
  LimitUpBoardReplayRepository,
)
from quantx_infrastructure.services.historical_market_data_service import (
  HistoricalMarketDataService,
)

logger = logging.getLogger(__name__)

REPLAY_DATASET_SCHEMA_VERSION = 1
REPLAY_DATASET_ROOT = Path("data/backtests/limit-up-board-replays")
REPLAY_UNIVERSE_ARTIFACT = "candidate-universe.v1.jsonl.gz"
REPLAY_TICK_ARTIFACT = "candidate-ticks.v1.jsonl.gz"
REPLAY_INPUT_MANIFEST = "input-manifest.v1.json"
MAX_SAFE_FRAME_GAP_SECONDS = 15.0
MAX_SAFE_TICK_AGE_SECONDS = 15.0
REQUIRED_BOOK_LEVELS = 5
_SHANGHAI = timezone(timedelta(hours=8))
_CONTINUOUS_SESSIONS = (
  ("MORNING", dt_time(9, 30), dt_time(11, 30)),
  ("AFTERNOON", dt_time(13, 0), dt_time(15, 0)),
)

_TICK_SCALAR_FIELDS = (
  "last_price",
  "open",
  "high",
  "low",
  "last_close",
  "amount",
  "volume",
  "pvolume",
  "tickvol",
  "open_int",
  "last_settlement_price",
  "settlement_price",
  "transaction_num",
)
_TICK_BOOK_FIELDS = ("ask_price", "bid_price", "ask_vol", "bid_vol")


@dataclass(frozen=True)
class LimitUpBoardReplayDataset:
  """Canonical in-memory input before immutable artifact materialization."""

  events: tuple[dict[str, Any], ...]
  ticks: tuple[dict[str, Any], ...]
  instruments: tuple[str, ...]
  dataset_fingerprint: str
  config_fingerprint: str
  input_manifest: dict[str, Any]
  data_quality: dict[str, Any]


@dataclass(frozen=True)
class LimitUpBoardReplayMaterialization:
  """Paths and authoritative manifest produced by one materialization."""

  manifest_path: str
  dataset_fingerprint: str
  config_fingerprint: str
  input_manifest: dict[str, Any]
  data_quality: dict[str, Any]


class LimitUpBoardReplayDatasetService:
  def __init__(
    self,
    market_data_service: Optional[HistoricalMarketDataService] = None,
  ) -> None:
    self.market_data_service = market_data_service or HistoricalMarketDataService()

  async def prepare(
    self,
    *,
    start_time: datetime,
    end_time: datetime,
    settings: Mapping[str, Any],
    expected_trading_dates: Sequence[date],
    ticks_by_instrument: Optional[Mapping[str, Sequence[Any]]] = None,
  ) -> LimitUpBoardReplayDataset:
    """Read point-in-time frames and raw, unadjusted ticks for their code union."""

    if _datetime_gt(start_time, end_time):
      raise ValueError("打板回放开始时间不能晚于结束时间")

    frames: list[Any] = []
    async for db in get_async_db():
      repo = LimitUpBoardReplayRepository(db)
      async for row in repo.iter_universe_snapshots(start_time, end_time):
        frames.append(row)
      break

    source = "POINT_IN_TIME_UNIVERSE_V1"
    if not frames:
      # Sparse facts explain why an old date cannot be replayed.  They must
      # never silently become an executable dataset.
      async for db in get_async_db():
        facts = await FirstBoardPromotionRepository(db).list_replay_facts(
          start_time,
          end_time,
        )
        break
      else:
        facts = []
      frames = _legacy_sparse_frames(facts)
      source = "LEGACY_SPARSE_PROMOTION_FACTS"

    codes = _candidate_code_union(frames)
    tick_load_errors: dict[str, str] = {}
    raw_ticks: list[Any] = []
    if ticks_by_instrument is not None:
      for code in sorted(codes):
        raw_ticks.extend(list(ticks_by_instrument.get(code, ()) or ()))
    else:
      for code in sorted(codes):
        try:
          raw_ticks.extend(
            await self.market_data_service.get_tick_data(
              code,
              start_time=start_time,
              end_time=end_time,
              dividend_type="none",
              as_frame=False,
              limit=None,
              order="asc",
            )
          )
        except Exception as exc:
          tick_load_errors[code] = exc.__class__.__name__
          logger.warning(
            "Board replay raw tick load failed: code=%s error=%s",
            code,
            exc.__class__.__name__,
          )

    return build_replay_dataset(
      frames,
      ticks=raw_ticks,
      settings=settings,
      expected_trading_dates=expected_trading_dates,
      source=source,
      requested_start_time=start_time,
      requested_end_time=end_time,
      tick_load_errors=tick_load_errors,
    )

  async def prepare_and_persist(
    self,
    *,
    job_id: str,
    start_time: datetime,
    end_time: datetime,
    settings: Mapping[str, Any],
    expected_trading_dates: Sequence[date],
    ticks_by_instrument: Optional[Mapping[str, Sequence[Any]]] = None,
  ) -> LimitUpBoardReplayMaterialization:
    dataset = await self.prepare(
      start_time=start_time,
      end_time=end_time,
      settings=settings,
      expected_trading_dates=expected_trading_dates,
      ticks_by_instrument=ticks_by_instrument,
    )
    return self.persist_artifact(dataset, job_id=job_id)

  @staticmethod
  def persist_artifact(
    dataset: LimitUpBoardReplayDataset,
    *,
    job_id: str,
  ) -> LimitUpBoardReplayMaterialization:
    """Atomically write two deterministic gzip JSONL files and manifest."""

    normalized_job_id = str(job_id or "").strip()
    if not normalized_job_id or normalized_job_id in {".", ".."}:
      raise ValueError("打板回放任务 ID 不能为空")
    if any(char in normalized_job_id for char in ("/", "\\")):
      raise ValueError("打板回放任务 ID 不能包含路径分隔符")

    base_dir = REPLAY_DATASET_ROOT / normalized_job_id
    base_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = base_dir / REPLAY_INPUT_MANIFEST
    if manifest_path.exists():
      loaded = load_replay_dataset_artifact(str(manifest_path))
      if (
        loaded["dataset_fingerprint"] != dataset.dataset_fingerprint
        or loaded["config_fingerprint"] != dataset.config_fingerprint
      ):
        raise ValueError("回放任务的不可变输入已存在且指纹不一致")
      return _materialization(manifest_path, loaded)

    universe_descriptor = _write_jsonl_gzip_once(
      base_dir / REPLAY_UNIVERSE_ARTIFACT,
      dataset.events,
    )
    tick_descriptor = _write_jsonl_gzip_once(
      base_dir / REPLAY_TICK_ARTIFACT,
      dataset.ticks,
    )
    expected_artifacts = dict(dataset.input_manifest.get("artifacts") or {})
    for key, descriptor in (
      ("candidate_universe", universe_descriptor),
      ("raw_ticks", tick_descriptor),
    ):
      expected = dict(expected_artifacts.get(key) or {})
      if (
        expected.get("content_sha256") != descriptor["content_sha256"]
        or int(expected.get("row_count", -1)) != descriptor["row_count"]
      ):
        raise ValueError(f"{key} 物化内容与数据集指纹不一致")

    manifest = _json_value(dict(dataset.input_manifest))
    manifest["artifacts"] = {
      "candidate_universe": {
        **universe_descriptor,
        "path": REPLAY_UNIVERSE_ARTIFACT,
        "format": "jsonl",
        "compression": "gzip",
      },
      "raw_ticks": {
        **tick_descriptor,
        "path": REPLAY_TICK_ARTIFACT,
        "format": "jsonl",
        "compression": "gzip",
      },
    }
    manifest["manifest_sha256"] = _manifest_fingerprint(manifest)
    _write_json_once(manifest_path, manifest)

    loaded = load_replay_dataset_artifact(str(manifest_path))
    if loaded["dataset_fingerprint"] != dataset.dataset_fingerprint:
      raise ValueError("物化后的打板回放数据集指纹不一致")
    return _materialization(manifest_path, loaded)


def load_replay_dataset_artifact(path: str) -> dict[str, Any]:
  """Strictly verify manifest, compressed files and canonical content hashes."""

  manifest_path = Path(path)
  if manifest_path.is_dir():
    manifest_path = manifest_path / REPLAY_INPUT_MANIFEST
  if manifest_path.name != REPLAY_INPUT_MANIFEST or not manifest_path.is_file():
    raise ValueError("打板回放输入 manifest 不存在或文件名不受支持")
  try:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
  except (OSError, UnicodeError, json.JSONDecodeError) as exc:
    raise ValueError("打板回放输入 manifest 无法读取") from exc
  if int(manifest.get("schema_version", 0) or 0) != REPLAY_DATASET_SCHEMA_VERSION:
    raise ValueError("打板回放输入 manifest 版本不受支持")
  expected_manifest_sha = str(manifest.get("manifest_sha256") or "")
  if not expected_manifest_sha or expected_manifest_sha != _manifest_fingerprint(
    manifest
  ):
    raise ValueError("打板回放输入 manifest 指纹校验失败")

  artifact_rows: dict[str, list[dict[str, Any]]] = {}
  for key in ("candidate_universe", "raw_ticks"):
    descriptor = dict((manifest.get("artifacts") or {}).get(key) or {})
    artifact_path = _safe_artifact_path(manifest_path.parent, descriptor.get("path"))
    artifact_rows[key] = _load_and_verify_jsonl_gzip(artifact_path, descriptor)

  actual_dataset_fingerprint = _dataset_fingerprint_from_manifest(manifest)
  if (
    not manifest.get("dataset_fingerprint")
    or manifest["dataset_fingerprint"] != actual_dataset_fingerprint
  ):
    raise ValueError("打板回放数据集指纹校验失败")
  config_fingerprint = str(manifest.get("config_fingerprint") or "")
  if (
    len(config_fingerprint) != 64
    or config_fingerprint != _fingerprint(dict(manifest.get("settings") or {}))
  ):
    raise ValueError("打板回放配置指纹无效")

  return {
    **manifest,
    "events": artifact_rows["candidate_universe"],
    "ticks": artifact_rows["raw_ticks"],
  }


def build_replay_dataset(
  frames: Iterable[Any],
  *,
  ticks: Iterable[Any] = (),
  settings: Mapping[str, Any],
  expected_trading_dates: Sequence[date],
  source: str,
  requested_start_time: Optional[datetime] = None,
  requested_end_time: Optional[datetime] = None,
  tick_load_errors: Optional[Mapping[str, str]] = None,
) -> LimitUpBoardReplayDataset:
  """Canonicalize frames and raw ticks, then produce conservative quality gates."""

  normalized_settings = _json_value(dict(settings or {}))
  config_fingerprint = _fingerprint(normalized_settings)
  events: list[dict[str, Any]] = []
  candidate_codes: set[str] = set()
  snapshot_refs: list[dict[str, Any]] = []
  observed_dates: set[str] = set()
  candidate_observations: list[tuple[str, int]] = []
  promotion_eligible_observations = 0
  future_violations = 0
  count_mismatches = 0
  scanner_stopped_frames = 0
  frame_gaps_over_limit = 0
  max_gap_seconds = 0.0
  last_by_session: dict[tuple[str, str], datetime] = {}
  frames_by_session: dict[tuple[str, str], list[datetime]] = {}
  score_versions: set[str] = set()
  feature_versions: set[str] = set()
  model_versions: set[str] = set()
  exit_policy_versions: set[str] = set()

  ordered_frames = sorted(
    list(frames),
    key=lambda row: (_datetime_sort_key(_observed_at(row)), _row_id(row)),
  )
  for row in ordered_frames:
    observed_at = _aware(_observed_at(row))
    source_max_at = _parse_datetime(_row_value(row, "source_max_at"))
    payload = _payload(row)
    payload_candidates = [
      _json_value(dict(item or {})) for item in payload.get("candidates") or []
    ]
    candidates = sorted(
      payload_candidates,
      key=lambda item: (
        int(item.get("rank_ordinal") or 2**31 - 1),
        str(item.get("code") or ""),
      ),
    )
    observed_date = observed_at.astimezone(_SHANGHAI).date().isoformat()
    observed_dates.add(observed_date)
    session_name = _continuous_session_name(observed_at)
    if session_name is not None:
      session_key = (observed_date, session_name)
      previous = last_by_session.get(session_key)
      if previous is not None:
        gap = max(0.0, (observed_at - previous).total_seconds())
        max_gap_seconds = max(max_gap_seconds, gap)
        if gap > MAX_SAFE_FRAME_GAP_SECONDS:
          frame_gaps_over_limit += 1
      last_by_session[session_key] = observed_at
      frames_by_session.setdefault(session_key, []).append(observed_at)
    if not bool(payload.get("scanner_running", True)):
      scanner_stopped_frames += 1
    if source_max_at is not None and _datetime_gt(source_max_at, observed_at):
      future_violations += 1

    eligible_count = sum(bool(item.get("promotion_eligible")) for item in candidates)
    persisted_candidate_count = _row_value(row, "candidate_count", len(candidates))
    persisted_eligible_count = _row_value(row, "eligible_count", eligible_count)
    if (
      int(persisted_candidate_count or 0) != len(candidates)
      or int(persisted_eligible_count or 0) != eligible_count
    ):
      count_mismatches += 1

    for candidate in candidates:
      code = str(candidate.get("code") or "").upper()
      if code:
        candidate_codes.add(code)
        candidate_observations.append((code, _epoch_ms(observed_at)))
      candidate_at = _parse_datetime(candidate.get("updated_at"))
      if candidate_at is not None and _datetime_gt(candidate_at, observed_at):
        future_violations += 1
      _add_version(model_versions, candidate.get("promotion_model_version"))
      _add_version(exit_policy_versions, candidate.get("exit_policy_version"))

    score_version = str(_row_value(row, "score_version", "") or "")
    feature_version = str(_row_value(row, "feature_version", "") or "")
    model_version = str(_row_value(row, "model_version", "") or "")
    exit_policy_version = str(
      _row_value(row, "exit_policy_version", "") or ""
    )
    _add_version(score_versions, score_version)
    _add_version(feature_versions, feature_version)
    _add_version(model_versions, model_version)
    _add_version(exit_policy_versions, exit_policy_version)

    promotion_eligible_observations += eligible_count
    snapshot_id = _row_id(row)
    snapshot_version = str(_row_value(row, "snapshot_version", "") or "")
    event = {
      "event_type": "UNIVERSE_SNAPSHOT",
      "timestamp": _canonical_datetime(observed_at),
      "observed_at": _canonical_datetime(observed_at),
      "source_max_at": _canonical_datetime(source_max_at),
      "snapshot_id": snapshot_id,
      "snapshot_key": str(_row_value(row, "snapshot_key", "") or ""),
      "snapshot_version": snapshot_version,
      "schema_version": int(_row_value(row, "schema_version", 1) or 1),
      "score_version": score_version,
      "feature_version": feature_version,
      "model_version": model_version,
      "exit_policy_version": exit_policy_version,
      "scanner_running": bool(payload.get("scanner_running", True)),
      "summary": _json_value(dict(payload.get("summary") or {})),
      "chain_snapshot_version": str(
        payload.get("chain_snapshot_version") or ""
      ),
      "warnings": _json_value(list(payload.get("warnings") or [])),
      "candidates": candidates,
      "candidate_count": len(candidates),
      "eligible_count": eligible_count,
    }
    events.append(event)
    snapshot_refs.append(
      {
        "id": snapshot_id,
        "version": snapshot_version,
        "observed_at": event["observed_at"],
      }
    )

  normalized_ticks, tick_quality = _normalize_ticks(ticks)
  tick_times_by_code: dict[str, list[int]] = {}
  for tick in normalized_ticks:
    code = str(tick.get("stock_code") or "")
    source_time_ms = tick.get("source_time_ms")
    if code and isinstance(source_time_ms, int) and source_time_ms > 0:
      tick_times_by_code.setdefault(code, []).append(source_time_ms)
  for values in tick_times_by_code.values():
    values.sort()

  covered_observations = 0
  stale_or_missing_tick_observations = 0
  max_tick_age_seconds = 0.0
  for code, observed_ms in candidate_observations:
    values = tick_times_by_code.get(code, [])
    index = bisect.bisect_right(values, observed_ms) - 1
    if index < 0:
      stale_or_missing_tick_observations += 1
      continue
    age_seconds = max(0.0, (observed_ms - values[index]) / 1000.0)
    max_tick_age_seconds = max(max_tick_age_seconds, age_seconds)
    if age_seconds <= MAX_SAFE_TICK_AGE_SECONDS:
      covered_observations += 1
    else:
      stale_or_missing_tick_observations += 1

  expected_dates = {value.isoformat() for value in expected_trading_dates}
  missing_dates = sorted(expected_dates - observed_dates)
  expected_session_windows = _expected_session_windows(
    expected_trading_dates,
    requested_start_time=requested_start_time,
    requested_end_time=requested_end_time,
  )
  missing_continuous_sessions: list[str] = []
  session_boundary_gaps = 0
  for session_key, (window_start, window_end) in expected_session_windows.items():
    session_frames = frames_by_session.get(session_key, [])
    if not session_frames:
      missing_continuous_sessions.append(":".join(session_key))
      continue
    opening_gap = max(0.0, (session_frames[0] - window_start).total_seconds())
    closing_gap = max(0.0, (window_end - session_frames[-1]).total_seconds())
    max_gap_seconds = max(max_gap_seconds, opening_gap, closing_gap)
    if opening_gap > MAX_SAFE_FRAME_GAP_SECONDS:
      session_boundary_gaps += 1
    if closing_gap > MAX_SAFE_FRAME_GAP_SECONDS:
      session_boundary_gaps += 1
  missing_tick_instruments = sorted(candidate_codes - set(tick_times_by_code))
  load_errors = {
    str(key): str(value) for key, value in sorted((tick_load_errors or {}).items())
  }
  blockers: list[str] = []
  warnings: list[str] = []
  if not events:
    blockers.append("NO_POINT_IN_TIME_CANDIDATE_FRAMES")
  if source != "POINT_IN_TIME_UNIVERSE_V1":
    blockers.append("LEGACY_SPARSE_CANDIDATE_FACTS")
  if future_violations:
    blockers.append("CANDIDATE_TIMESTAMP_AFTER_OBSERVATION")
  if count_mismatches:
    blockers.append("CANDIDATE_FRAME_COUNT_MISMATCH")
  if len(score_versions) > 1:
    blockers.append("MIXED_RADAR_SCORE_VERSIONS")
  if len(feature_versions) > 1:
    blockers.append("MIXED_FEATURE_VERSIONS")
  if len(model_versions) > 1:
    blockers.append("MIXED_PROMOTION_MODEL_VERSIONS")
  if len(exit_policy_versions) > 1:
    blockers.append("MIXED_EXIT_POLICY_VERSIONS")
  if missing_dates:
    blockers.append("MISSING_TRADING_DATES")
  if frame_gaps_over_limit:
    blockers.append("CANDIDATE_FRAME_GAPS_OVER_15_SECONDS")
  if missing_continuous_sessions or session_boundary_gaps:
    blockers.append("INCOMPLETE_CONTINUOUS_TRADING_SESSION_COVERAGE")
  if scanner_stopped_frames:
    blockers.append("SCANNER_NOT_RUNNING_IN_CAPTURED_FRAMES")
  if load_errors:
    blockers.append("RAW_TICK_LOAD_FAILED")
  if missing_tick_instruments:
    blockers.append("CANDIDATE_INSTRUMENTS_WITHOUT_RAW_TICKS")
  if stale_or_missing_tick_observations:
    blockers.append("CANDIDATE_OBSERVATIONS_WITHOUT_FRESH_RAW_TICK")
  for blocker in tick_quality["blockers"]:
    if blocker not in blockers:
      blockers.append(blocker)
  warnings.extend(tick_quality["warnings"])
  if not candidate_codes:
    warnings.append("NO_CANDIDATE_INSTRUMENTS")

  coverage_pct = (
    covered_observations / len(candidate_observations) * 100.0
    if candidate_observations
    else 100.0
  )
  valid_source_times = [
    row["source_time_ms"]
    for row in normalized_ticks
    if isinstance(row.get("source_time_ms"), int)
  ]
  coverage = {
    "frame_count": len(events),
    "candidate_observations": len(candidate_observations),
    "promotion_eligible_observations": promotion_eligible_observations,
    "candidate_instrument_count": len(candidate_codes),
    "covered_trading_dates": sorted(observed_dates),
    "expected_trading_dates": sorted(expected_dates),
    "missing_trading_dates": missing_dates,
    "first_observed_at": events[0]["observed_at"] if events else None,
    "last_observed_at": events[-1]["observed_at"] if events else None,
    "max_frame_gap_seconds": round(max_gap_seconds, 3),
    "frame_gaps_over_15_seconds": frame_gaps_over_limit,
    "missing_continuous_sessions": missing_continuous_sessions,
    "session_boundary_gaps_over_15_seconds": session_boundary_gaps,
    "scanner_stopped_frames": scanner_stopped_frames,
    "raw_tick_count": len(normalized_ticks),
    "raw_tick_instrument_count": len(tick_times_by_code),
    "missing_tick_instruments": missing_tick_instruments,
    "candidate_fresh_tick_coverage_pct": round(coverage_pct, 6),
    "candidate_observations_without_fresh_tick": stale_or_missing_tick_observations,
    "max_candidate_tick_age_seconds": round(max_tick_age_seconds, 3),
    "first_tick_source_time_ms": min(valid_source_times, default=None),
    "last_tick_source_time_ms": max(valid_source_times, default=None),
  }
  quality_status = "BLOCKED" if blockers else ("DEGRADED" if warnings else "OK")
  quality = {
    "status": quality_status,
    "executable": not blockers,
    "source": source,
    "coverage": coverage,
    "future_data_violations": future_violations,
    "candidate_frame_count_mismatches": count_mismatches,
    "score_versions": sorted(score_versions),
    "feature_versions": sorted(feature_versions),
    "model_versions": sorted(model_versions),
    "exit_policy_versions": sorted(exit_policy_versions),
    "tick_field_quality": tick_quality,
    "tick_load_errors": load_errors,
    "blockers": blockers,
    "warnings": sorted(set(warnings)),
  }

  universe_content = _jsonl_descriptor(events)
  tick_content = _jsonl_descriptor(normalized_ticks)
  manifest = {
    "schema_version": REPLAY_DATASET_SCHEMA_VERSION,
    "source": source,
    "requested_range": {
      "start_time": _canonical_datetime(requested_start_time),
      "end_time": _canonical_datetime(requested_end_time),
      "timezone": "Asia/Shanghai",
    },
    "config_fingerprint": config_fingerprint,
    "settings": normalized_settings,
    "snapshot_refs_fingerprint": _fingerprint(snapshot_refs),
    "versions": {
      "score": sorted(score_versions),
      "feature": sorted(feature_versions),
      "promotion_model": sorted(model_versions),
      "exit_policy": sorted(exit_policy_versions),
    },
    "coverage": coverage,
    "data_quality": quality,
    "artifacts": {
      "candidate_universe": {
        "path": REPLAY_UNIVERSE_ARTIFACT,
        "format": "jsonl",
        "compression": "gzip",
        **universe_content,
      },
      "raw_ticks": {
        "path": REPLAY_TICK_ARTIFACT,
        "format": "jsonl",
        "compression": "gzip",
        **tick_content,
      },
    },
  }
  manifest["dataset_fingerprint"] = _dataset_fingerprint_from_manifest(manifest)
  return LimitUpBoardReplayDataset(
    events=tuple(events),
    ticks=tuple(normalized_ticks),
    instruments=tuple(sorted(candidate_codes)),
    dataset_fingerprint=manifest["dataset_fingerprint"],
    config_fingerprint=config_fingerprint,
    input_manifest=manifest,
    data_quality=quality,
  )


def _normalize_ticks(ticks: Iterable[Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
  normalized: list[dict[str, Any]] = []
  missing_native_limits = 0
  missing_stock_status = 0
  missing_price_tick = 0
  missing_book = 0
  missing_price_fields = 0
  invalid_identity = 0
  derived_source_time = 0

  for raw in ticks:
    code_value, _ = _raw_value(raw, ("stock_code", "instrument_code", "code"))
    code = str(code_value or "").upper()
    period_value, _ = _raw_value(raw, ("period",))
    time_value, time_present = _raw_value(raw, ("time", "timestamp"))
    parsed_time = _parse_datetime(time_value)
    source_value, source_present = _raw_value(raw, ("source_time_ms",))
    try:
      source_time_ms = int(source_value) if source_present else 0
    except (TypeError, ValueError):
      source_time_ms = 0
    if source_time_ms <= 0 and parsed_time is not None:
      source_time_ms = _epoch_ms(_aware(parsed_time))
      derived_source_time += 1
    ordinal_value, ordinal_present = _raw_value(raw, ("tick_ordinal",))
    try:
      tick_ordinal = int(ordinal_value) if ordinal_present else 0
    except (TypeError, ValueError):
      tick_ordinal = -1
    if not code or source_time_ms <= 0 or tick_ordinal < 0:
      invalid_identity += 1

    price_tick_value, price_tick_present = _raw_value(
      raw, ("price_tick", "priceTick", "PriceTick")
    )
    up_limit_value, up_limit_present = _raw_value(
      raw,
      ("up_stop_price", "limit_up", "upperLimit", "upStopPrice", "UpStopPrice"),
    )
    down_limit_value, down_limit_present = _raw_value(
      raw,
      (
        "down_stop_price",
        "limit_down",
        "lowerLimit",
        "downStopPrice",
        "DownStopPrice",
      ),
    )
    stock_status_value, stock_status_present = _raw_value(
      raw, ("stock_status", "stockStatus")
    )
    if not price_tick_present or not _positive_finite(price_tick_value):
      missing_price_tick += 1
    if (
      not up_limit_present
      or not down_limit_present
      or not _positive_finite(up_limit_value)
      or not _positive_finite(down_limit_value)
    ):
      missing_native_limits += 1
    if not stock_status_present or not _finite_number(stock_status_value):
      missing_stock_status += 1

    book: dict[str, Any] = {}
    book_complete = True
    for field in _TICK_BOOK_FIELDS:
      aliases = {
        "ask_price": ("ask_price", "askPrice"),
        "bid_price": ("bid_price", "bidPrice"),
        "ask_vol": ("ask_vol", "askVol"),
        "bid_vol": ("bid_vol", "bidVol"),
      }[field]
      value, present = _raw_value(raw, aliases)
      values = _sequence_or_none(value) if present else None
      if (
        values is None
        or len(values) < REQUIRED_BOOK_LEVELS
        or not all(_finite_number(item) for item in values[:REQUIRED_BOOK_LEVELS])
      ):
        book_complete = False
      book[field] = (
        [_json_value(item) for item in values[:REQUIRED_BOOK_LEVELS]]
        if values is not None
        else None
      )
    if not book_complete:
      missing_book += 1

    scalar_values: dict[str, Any] = {}
    required_prices_complete = True
    for field in _TICK_SCALAR_FIELDS:
      aliases = {
        "last_close": ("last_close", "lastClose"),
        "pvolume": ("pvolume",),
        "tickvol": ("tickvol",),
        "open_int": ("open_int", "openInt"),
        "last_settlement_price": (
          "last_settlement_price",
          "lastSettlementPrice",
        ),
        "settlement_price": ("settlement_price", "settlementPrice"),
        "transaction_num": ("transaction_num", "transactionNum"),
      }.get(field, (field,))
      value, present = _raw_value(raw, aliases)
      scalar_values[field] = _json_value(value) if present else None
      if field in {"last_price", "open", "high", "low", "last_close"} and (
        not present or not _finite_number(value)
      ):
        required_prices_complete = False
    if not required_prices_complete:
      missing_price_fields += 1

    normalized.append(
      {
        "stock_code": code,
        "period": str(period_value or "tick"),
        "time": _canonical_datetime(parsed_time) if time_present else None,
        "source_time_ms": source_time_ms if source_time_ms > 0 else None,
        "tick_ordinal": tick_ordinal if tick_ordinal >= 0 else None,
        **scalar_values,
        "stock_status": (
          _json_value(stock_status_value) if stock_status_present else None
        ),
        "price_tick": _json_value(price_tick_value) if price_tick_present else None,
        "up_stop_price": _json_value(up_limit_value) if up_limit_present else None,
        "down_stop_price": (
          _json_value(down_limit_value) if down_limit_present else None
        ),
        **book,
      }
    )

  normalized.sort(
    key=lambda row: (
      int(row.get("source_time_ms") or 0),
      int(row.get("tick_ordinal") or 0),
      str(row.get("stock_code") or ""),
      _canonical_json(row),
    )
  )
  identities: dict[tuple[str, Any, Any], str] = {}
  duplicate_identities = 0
  conflicting_identities = 0
  for row in normalized:
    identity = (
      str(row.get("stock_code") or ""),
      row.get("source_time_ms"),
      row.get("tick_ordinal"),
    )
    row_fingerprint = _fingerprint(row)
    previous = identities.get(identity)
    if previous is not None:
      duplicate_identities += 1
      if previous != row_fingerprint:
        conflicting_identities += 1
    else:
      identities[identity] = row_fingerprint

  blockers: list[str] = []
  warnings: list[str] = []
  if invalid_identity:
    blockers.append("RAW_TICKS_WITH_INVALID_IDENTITY")
  if missing_native_limits:
    blockers.append("RAW_TICKS_MISSING_NATIVE_PRICE_LIMITS")
  if missing_stock_status:
    blockers.append("RAW_TICKS_MISSING_STOCK_STATUS")
  if missing_price_tick:
    blockers.append("RAW_TICKS_MISSING_PRICE_TICK")
  if missing_book:
    blockers.append("RAW_TICKS_MISSING_FIVE_LEVEL_BOOK")
  if missing_price_fields:
    blockers.append("RAW_TICKS_MISSING_PRICE_FIELDS")
  if duplicate_identities:
    blockers.append("DUPLICATE_RAW_TICK_IDENTITIES")
  if conflicting_identities:
    blockers.append("CONFLICTING_RAW_TICK_IDENTITIES")
  if derived_source_time:
    warnings.append("DERIVED_SOURCE_TIME_MS_FROM_TICK_TIME")
  return normalized, {
    "tick_count": len(normalized),
    "invalid_identity_count": invalid_identity,
    "derived_source_time_count": derived_source_time,
    "missing_native_price_limits_count": missing_native_limits,
    "missing_stock_status_count": missing_stock_status,
    "missing_price_tick_count": missing_price_tick,
    "missing_five_level_book_count": missing_book,
    "missing_price_fields_count": missing_price_fields,
    "duplicate_identity_count": duplicate_identities,
    "conflicting_identity_count": conflicting_identities,
    "blockers": blockers,
    "warnings": warnings,
  }


def _legacy_sparse_frames(facts: Sequence[tuple[Any, Any]]) -> list[dict[str, Any]]:
  latest: dict[str, dict[str, Any]] = {}
  frames: list[dict[str, Any]] = []
  for lifecycle, assessment in facts:
    item = {
      **dict(getattr(lifecycle, "payload", {}) or {}),
      **dict(getattr(assessment, "payload", {}) or {}),
      "code": str(getattr(lifecycle, "instrument_code", "") or ""),
      "stage": str(getattr(lifecycle, "stage", "") or ""),
      "updated_at": getattr(lifecycle, "as_of").isoformat(),
      "is_stale": False,
      "promotion_eligible": bool(getattr(assessment, "eligible", False)),
      "promotion_score": float(getattr(assessment, "rank_score", 0.0) or 0.0),
      "promotion_snapshot_version": str(
        getattr(lifecycle, "snapshot_version", "") or ""
      ),
      "promotion_model_version": str(
        getattr(assessment, "model_version", "") or ""
      ),
      "exit_policy_version": str(
        getattr(assessment, "exit_policy_version", "") or ""
      ),
      "board_segment": str(getattr(assessment, "segment", "") or ""),
      "cvar95_loss_pct": float(
        getattr(assessment, "cvar95_loss_pct", 0.0) or 0.0
      ),
      "expected_net_return_pct": float(
        getattr(assessment, "expected_net_return_pct", 0.0) or 0.0
      ),
      "high_position_type": str(
        getattr(assessment, "high_position_type", "") or ""
      ),
      "blocked_reasons": list(getattr(assessment, "veto_reasons", []) or []),
    }
    latest[item["code"]] = item
    observed_at = getattr(lifecycle, "as_of")
    frames.append(
      {
        "id": str(getattr(lifecycle, "id", "") or ""),
        "observed_at": observed_at,
        "snapshot_version": str(
          getattr(lifecycle, "snapshot_version", "") or ""
        ),
        "payload": {
          "scanner_running": True,
          "candidates": list(latest.values()),
        },
      }
    )
  return frames


def _candidate_code_union(frames: Iterable[Any]) -> set[str]:
  result: set[str] = set()
  for row in frames:
    for candidate in _payload(row).get("candidates") or []:
      code = str(dict(candidate or {}).get("code") or "").upper()
      if code:
        result.add(code)
  return result


def _row_value(row: Any, key: str, default: Any = None) -> Any:
  if isinstance(row, Mapping):
    return row.get(key, default)
  return getattr(row, key, default)


def _row_id(row: Any) -> str:
  return str(_row_value(row, "id", "") or _row_value(row, "snapshot_key", ""))


def _payload(row: Any) -> dict[str, Any]:
  return dict(_row_value(row, "payload", {}) or {})


def _observed_at(row: Any) -> datetime:
  value = _row_value(row, "observed_at") or _row_value(row, "as_of")
  parsed = _parse_datetime(value)
  if parsed is None:
    raise ValueError("候选池快照缺少 observed_at")
  return parsed


def _raw_value(raw: Any, aliases: Sequence[str]) -> tuple[Any, bool]:
  if isinstance(raw, Mapping):
    for alias in aliases:
      if alias in raw:
        return raw[alias], True
    return None, False
  for alias in aliases:
    if hasattr(raw, alias):
      return getattr(raw, alias), True
  return None, False


def _parse_datetime(value: Any) -> datetime | None:
  if isinstance(value, datetime):
    return value
  if not value:
    return None
  try:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
  except ValueError:
    return None


def _aware(value: datetime) -> datetime:
  return value.replace(tzinfo=_SHANGHAI) if value.tzinfo is None else value


def _datetime_sort_key(value: datetime) -> float:
  return _aware(value).astimezone(timezone.utc).timestamp()


def _datetime_gt(left: datetime, right: datetime) -> bool:
  return _datetime_sort_key(left) > _datetime_sort_key(right)


def _continuous_session_name(value: datetime) -> Optional[str]:
  local_time = _aware(value).astimezone(_SHANGHAI).time().replace(tzinfo=None)
  for name, session_start, session_end in _CONTINUOUS_SESSIONS:
    if session_start <= local_time <= session_end:
      return name
  return None


def _expected_session_windows(
  trading_dates: Sequence[date],
  *,
  requested_start_time: Optional[datetime],
  requested_end_time: Optional[datetime],
) -> dict[tuple[str, str], tuple[datetime, datetime]]:
  requested_start = _aware(requested_start_time) if requested_start_time else None
  requested_end = _aware(requested_end_time) if requested_end_time else None
  result: dict[tuple[str, str], tuple[datetime, datetime]] = {}
  for trading_date in trading_dates:
    for name, session_start, session_end in _CONTINUOUS_SESSIONS:
      start = datetime.combine(trading_date, session_start, tzinfo=_SHANGHAI)
      end = datetime.combine(trading_date, session_end, tzinfo=_SHANGHAI)
      if requested_start is not None:
        start = max(start, requested_start.astimezone(_SHANGHAI))
      if requested_end is not None:
        end = min(end, requested_end.astimezone(_SHANGHAI))
      if start <= end:
        result[(trading_date.isoformat(), name)] = (start, end)
  return result


def _canonical_datetime(value: Optional[datetime]) -> Optional[str]:
  if value is None:
    return None
  return _aware(value).astimezone(_SHANGHAI).isoformat(timespec="microseconds")


def _epoch_ms(value: datetime) -> int:
  return int(round(_aware(value).astimezone(timezone.utc).timestamp() * 1000))


def _add_version(target: set[str], value: Any) -> None:
  normalized = str(value or "").strip()
  if normalized:
    target.add(normalized)


def _sequence_or_none(value: Any) -> Optional[list[Any]]:
  if value is None or isinstance(value, (str, bytes, bytearray)):
    return None
  try:
    return list(value)
  except TypeError:
    return None


def _finite_number(value: Any) -> bool:
  if isinstance(value, bool) or value is None:
    return False
  try:
    return math.isfinite(float(value))
  except (TypeError, ValueError, OverflowError):
    return False


def _positive_finite(value: Any) -> bool:
  return _finite_number(value) and float(value) > 0


def _json_value(value: Any) -> Any:
  if value is None or isinstance(value, (str, bool, int)):
    return value
  if isinstance(value, Enum):
    return _json_value(value.value)
  if isinstance(value, datetime):
    return _canonical_datetime(value)
  if isinstance(value, date):
    return value.isoformat()
  if isinstance(value, Decimal):
    if not value.is_finite():
      raise ValueError("回放输入包含非有限 Decimal")
    return int(value) if value == value.to_integral() else float(value)
  if isinstance(value, float):
    if not math.isfinite(value):
      raise ValueError("回放输入包含 NaN 或无穷大")
    return value
  if isinstance(value, Mapping):
    return {
      str(key): _json_value(item)
      for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
    }
  if isinstance(value, (set, frozenset)):
    values = [_json_value(item) for item in value]
    return sorted(values, key=_canonical_json)
  if isinstance(value, (list, tuple)):
    return [_json_value(item) for item in value]

  scalar_item = getattr(value, "item", None)
  if callable(scalar_item):
    try:
      converted = scalar_item()
    except (TypeError, ValueError):
      converted = value
    if converted is not value:
      return _json_value(converted)
  raise TypeError(f"打板回放输入包含不支持的值类型: {type(value).__name__}")


def _canonical_json(value: Any) -> str:
  return json.dumps(
    _json_value(value),
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
  )


def _fingerprint(value: Any) -> str:
  return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _jsonl_descriptor(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
  digest = hashlib.sha256()
  for row in rows:
    digest.update((_canonical_json(row) + "\n").encode("utf-8"))
  return {"row_count": len(rows), "content_sha256": digest.hexdigest()}


def _dataset_fingerprint_from_manifest(manifest: Mapping[str, Any]) -> str:
  artifacts = dict(manifest.get("artifacts") or {})
  return _fingerprint(
    {
      "schema_version": int(manifest.get("schema_version", 0) or 0),
      "source": str(manifest.get("source") or ""),
      "requested_range": dict(manifest.get("requested_range") or {}),
      "snapshot_refs_fingerprint": str(
        manifest.get("snapshot_refs_fingerprint") or ""
      ),
      "versions": dict(manifest.get("versions") or {}),
      "coverage": dict(manifest.get("coverage") or {}),
      "data_quality": dict(manifest.get("data_quality") or {}),
      "artifacts": {
        key: {
          "format": str(dict(artifacts.get(key) or {}).get("format") or ""),
          "compression": str(
            dict(artifacts.get(key) or {}).get("compression") or ""
          ),
          "row_count": int(
            dict(artifacts.get(key) or {}).get("row_count", -1)
          ),
          "content_sha256": str(
            dict(artifacts.get(key) or {}).get("content_sha256") or ""
          ),
        }
        for key in ("candidate_universe", "raw_ticks")
      },
    }
  )


def _manifest_fingerprint(manifest: Mapping[str, Any]) -> str:
  payload = dict(manifest)
  payload.pop("manifest_sha256", None)
  return _fingerprint(payload)


def _write_jsonl_gzip_once(
  path: Path,
  rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
  path.parent.mkdir(parents=True, exist_ok=True)
  handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
  os.close(handle)
  temp_path = Path(temp_name)
  try:
    with temp_path.open("wb") as raw_stream:
      with gzip.GzipFile(
        filename="",
        mode="wb",
        fileobj=raw_stream,
        compresslevel=6,
        mtime=0,
      ) as gzip_stream:
        for row in rows:
          gzip_stream.write((_canonical_json(row) + "\n").encode("utf-8"))
    descriptor = {
      **_jsonl_descriptor(rows),
      "file_sha256": _file_sha256(temp_path),
      "compressed_size_bytes": temp_path.stat().st_size,
    }
    if path.exists():
      existing_descriptor = _inspect_jsonl_gzip(path)
      if existing_descriptor != descriptor:
        raise ValueError(f"不可变回放产物已存在且内容不同: {path.name}")
      return existing_descriptor
    os.replace(temp_path, path)
    return descriptor
  finally:
    if temp_path.exists():
      temp_path.unlink()


def _write_json_once(path: Path, payload: Mapping[str, Any]) -> None:
  encoded = (_canonical_json(payload) + "\n").encode("utf-8")
  handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
  os.close(handle)
  temp_path = Path(temp_name)
  try:
    temp_path.write_bytes(encoded)
    if path.exists():
      if path.read_bytes() != encoded:
        raise ValueError("不可变回放输入 manifest 已存在且内容不同")
      return
    os.replace(temp_path, path)
  finally:
    if temp_path.exists():
      temp_path.unlink()


def _inspect_jsonl_gzip(path: Path) -> dict[str, Any]:
  rows = _load_jsonl_gzip_rows(path)
  return {
    **_jsonl_descriptor(rows),
    "file_sha256": _file_sha256(path),
    "compressed_size_bytes": path.stat().st_size,
  }


def _load_and_verify_jsonl_gzip(
  path: Path,
  descriptor: Mapping[str, Any],
) -> list[dict[str, Any]]:
  if not path.is_file():
    raise ValueError(f"打板回放输入产物不存在: {path.name}")
  expected_file_sha = str(descriptor.get("file_sha256") or "")
  if not expected_file_sha or _file_sha256(path) != expected_file_sha:
    raise ValueError(f"打板回放输入文件 SHA256 校验失败: {path.name}")
  rows = _load_jsonl_gzip_rows(path, require_canonical=True)
  actual_content = _jsonl_descriptor(rows)
  if (
    actual_content["content_sha256"] != descriptor.get("content_sha256")
    or actual_content["row_count"] != int(descriptor.get("row_count", -1))
  ):
    raise ValueError(f"打板回放输入内容 SHA256 校验失败: {path.name}")
  if path.stat().st_size != int(descriptor.get("compressed_size_bytes", -1)):
    raise ValueError(f"打板回放输入文件长度校验失败: {path.name}")
  return rows


def _load_jsonl_gzip_rows(
  path: Path,
  *,
  require_canonical: bool = False,
) -> list[dict[str, Any]]:
  rows: list[dict[str, Any]] = []
  try:
    with gzip.open(path, "rb") as stream:
      for line_number, raw_line in enumerate(stream, start=1):
        if not raw_line.endswith(b"\n"):
          raise ValueError(f"JSONL 第 {line_number} 行缺少换行符")
        try:
          row = json.loads(raw_line.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
          raise ValueError(f"JSONL 第 {line_number} 行无效") from exc
        if not isinstance(row, dict):
          raise ValueError(f"JSONL 第 {line_number} 行必须是对象")
        if require_canonical and raw_line != (
          _canonical_json(row) + "\n"
        ).encode("utf-8"):
          raise ValueError(f"JSONL 第 {line_number} 行不是规范编码")
        rows.append(row)
  except (OSError, EOFError, gzip.BadGzipFile) as exc:
    raise ValueError(f"打板回放 gzip 产物无法读取: {path.name}") from exc
  return rows


def _safe_artifact_path(base_dir: Path, raw_path: Any) -> Path:
  relative = PurePosixPath(str(raw_path or ""))
  if not relative.parts or relative.is_absolute() or ".." in relative.parts:
    raise ValueError("打板回放输入 manifest 包含不安全的产物路径")
  candidate = (base_dir / Path(*relative.parts)).resolve()
  base = base_dir.resolve()
  try:
    candidate.relative_to(base)
  except ValueError as exc:
    raise ValueError("打板回放输入产物路径越界") from exc
  return candidate


def _file_sha256(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb") as stream:
    while chunk := stream.read(1024 * 1024):
      digest.update(chunk)
  return digest.hexdigest()


def _materialization(
  manifest_path: Path,
  loaded: Mapping[str, Any],
) -> LimitUpBoardReplayMaterialization:
  return LimitUpBoardReplayMaterialization(
    manifest_path=str(manifest_path),
    dataset_fingerprint=str(loaded["dataset_fingerprint"]),
    config_fingerprint=str(loaded["config_fingerprint"]),
    input_manifest={
      key: value for key, value in loaded.items() if key not in {"events", "ticks"}
    },
    data_quality=dict(loaded.get("data_quality") or {}),
  )


__all__ = [
  "LimitUpBoardReplayDataset",
  "LimitUpBoardReplayDatasetService",
  "LimitUpBoardReplayMaterialization",
  "build_replay_dataset",
  "load_replay_dataset_artifact",
]
