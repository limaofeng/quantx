"""Fail-closed V3 T-trade replay acceptance and pressure-baseline runner.

This module deliberately has no broker, QMT, PAPER, or LIVE entrypoint.  It
only reads persisted account snapshots and historical Tick rows until an
operator explicitly asks it to run an isolated ``BACKTEST`` after every held
instrument/day has passed the same Tick-quality gate used by the Engine.

The report has two independent verdicts:

* the 20-trading-day causal-replay gate; and
* an optional, explicitly non-gating, all-holdings pressure baseline.

The latter is useful for freezing local SLO observations when the former is
blocked by historical-data evidence.  It must never be represented as the
20-day acceptance result.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import inspect
import json
import os
import platform
import sys
import time as wall_clock
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as clock_time
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from quantx_domain.strategies.ashare_intraday_t_assistant import (
  AshareIntradayTAssistantStrategy,
)
from quantx_domain.trading.t_trade_opportunity_engine import (
  OPPORTUNITY_FEATURE_SCHEMA_VERSION,
  OPPORTUNITY_POLICY_VERSION,
)
from quantx_infrastructure.core.runtime_state_manager import RuntimeStateManager
from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.strategy_backtest import StrategyBacktest
from quantx_infrastructure.models.strategy_run import StrategyRun
from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
  T_TRADE_EVALUATION_KIND_DIAGNOSTIC,
  T_TRADE_EVALUATION_KIND_MATERIAL,
  TTradeInstrumentProfile,
  TTradeOpportunityEvaluation,
)
from quantx_infrastructure.models.tick import Tick
from quantx_infrastructure.repositories.daily_asset_snapshot_repository import (
  DailyAssetPositionSnapshotRepository,
  DailyAssetSnapshotRepository,
)
from quantx_infrastructure.repositories.instrument_repository import (
  InstrumentRepository,
)
from quantx_infrastructure.repositories.strategy_run_state_repository import (
  StrategyRunPositionRepository,
  StrategyRunStateRepository,
)
from quantx_infrastructure.services.canonical_tick_archive import (
  CanonicalTickArchive,
  CanonicalTickArchiveError,
  CanonicalTickArchiveReader,
)
from quantx_infrastructure.services.historical_market_data_service import (
  HistoricalMarketDataService,
  HistoricalTickPaginationError,
)
from quantx_infrastructure.services.market_data_request_service import (
  load_completed_empty_tick_days,
)
from quantx_infrastructure.services.t_trade_replay_projection_service import (
  TTradeReplayUpdateKind,
  t_trade_replay_projection_service,
)
from quantx_infrastructure.services.t_trade_replay_service import (
  _INTERNAL_V3_PRESSURE_RUNTIME_STATE_PERSISTENCE_KEY,
  TTradeReplayService,
  _v3_pressure_runtime_state_persistence_capability,
)
from quantx_infrastructure.services.trading_time_service import TradingDateHelper
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_engine.strategy_executor import StrategyExecutor
from quantx_engine.strategy_manager import StrategyManager

DEFAULT_TRADING_DAYS = 20
DEFAULT_AUDIT_CONCURRENCY = 4
DEFAULT_PRESSURE_TIMEOUT_SECONDS = 1_800.0
DEFAULT_FORMAL_REPORT_PATH = Path("docs/reports/t-trade-v3-acceptance.md")
DEFAULT_RECENT_COMPLETED_DIAGNOSTIC_REPORT_PATH = Path(
  "docs/reports/t-trade-v3-recent-completed-diagnostic.md"
)
REPORT_SCHEMA_VERSION = 1
PRESSURE_BASELINE_SCHEMA_VERSION = 1
OPERATIONAL_EVIDENCE_SCHEMA_VERSION = 1
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_CALENDAR_MAX_DAYS_PER_REQUEST = 240

_PRESSURE_REPLAY_REPORT_FIELDS = (
  "status",
  "progress_pct",
  "processed_until",
  "start_time",
  "end_time",
  "replay_start_time",
  "replay_end_time",
  "created_at",
  "updated_at",
)
_PRESSURE_RUN_PARAMETER_FIELDS = (
  "t_trade_replay",
  "replay_acceptance",
  _INTERNAL_V3_PRESSURE_RUNTIME_STATE_PERSISTENCE_KEY,
  "replay_start_time",
  "replay_end_time",
)

PERFORMANCE_REMEDIATION_MICROBENCHMARK = {
  "status": "MICROBENCHMARK_NON_GATING",
  "implementation": (
    "packages/infrastructure/src/quantx_infrastructure/services/"
    "t_trade_opportunity_runtime_service.py: batch already-closed diagnostic "
    "windows in one owned session/atomic commit"
  ),
  "correctness_tests": [
    "8 closed diagnostics preserve 8 rows with 1 session / 1 commit",
    "batch failure rolls back and requeues every closed window (no partial write)",
  ],
  "batch_transaction_microtest": {
    "closed_diagnostics": 8,
    "preserved_rows": 8,
    "owned_sessions": 1,
    "commits": 1,
  },
  "sqlite_in_memory_microbenchmark": {
    "workload": "320 rows / 8 streams",
    "before": {"commits": 320, "elapsed_ms": 1016.579},
    "after": {"commits": 40, "elapsed_ms": 551.746},
    "commit_reduction_pct": 87.5,
    "elapsed_reduction_pct": 45.725,
  },
  "focused_validation": "43 passed focused service + V3 Engine runtime/observability tests; ruff passed",
  "full_9600_replayed_after_patch": False,
  "slo_status": "BLOCKED",
  "scope_limit": (
    "isolated SQLite persistence microbenchmark only; no new full Engine "
    "pressure run was performed after the patch, so it cannot pass/freeze SLO"
  ),
}


def _performance_remediation_evidence(
  pressure_baseline: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
  """Describe whether the post-remediation full fixture really ran.

  The microbenchmark is intentionally retained as historical context, but it
  must never claim that its SQLite result is a substitute for the full Engine
  load.  Only a completed, current 9,600-Tick fixture can change this evidence.
  """

  evidence = copy.deepcopy(PERFORMANCE_REMEDIATION_MICROBENCHMARK)
  pressure = dict(pressure_baseline or {})
  fixture = dict(pressure.get("fixture") or {})
  boundary = dict(pressure.get("execution_boundary") or {})
  terminal = dict(pressure.get("terminal_convergence") or {})
  run_evidence = dict(pressure.get("run_evidence") or {})
  parameters = _json_object(
    run_evidence.get("parameters"), context="PRESSURE_REMEDIATION_PARAMETERS"
  )
  full_completed = (
    str(pressure.get("status") or "").upper()
    == "EXECUTED_SYNTHETIC_NON_HISTORICAL"
    and int(fixture.get("tick_count") or 0) == 9_600
    and str(dict(pressure.get("replay") or {}).get("status") or "").upper()
    == "COMPLETED"
    and str(terminal.get("status") or "").upper() == "TERMINAL"
    and bool(pressure.get("isolated_backtest"))
    and bool(pressure.get("no_live_or_paper_broker"))
    and str(boundary.get("strategy_run_mode") or "").upper() == "BACKTEST"
    and bool(boundary.get("runtime_state_persist_enabled"))
    and not bool(boundary.get("qmt_invocation"))
    and not bool(boundary.get("paper_or_live_command"))
    and str(run_evidence.get("mode") or "").upper() == "BACKTEST"
    and str(run_evidence.get("status") or "").upper() == "COMPLETED"
    and parameters.get("replay_acceptance") == "V3_PRESSURE_BASELINE"
    and bool(parameters.get(_INTERNAL_V3_PRESSURE_RUNTIME_STATE_PERSISTENCE_KEY))
  )
  evidence["full_9600_replayed_after_patch"] = full_completed
  if full_completed:
    evidence["slo_status"] = "FROZEN_FIRST_LOCAL_SYNTHETIC_BASELINE"
    evidence["scope_limit"] = (
      "completed current-production-path 9,600-Tick synthetic baseline; it "
      "freezes only this machine's local synthetic SLO and does not replace "
      "the formal 20-day causal-replay gate"
    )
  return evidence


class AcceptanceBlockedError(RuntimeError):
  """Raised when an operator asks to run a gate that audit evidence blocks."""

  def __init__(self, message: str, *, evidence: Optional[Mapping[str, Any]] = None):
    super().__init__(message)
    self.evidence = dict(evidence or {})


def _digest(value: str) -> str:
  return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _json_default(value: Any) -> Any:
  if isinstance(value, (datetime, date)):
    return value.isoformat()
  if isinstance(value, Path):
    return str(value)
  if isinstance(value, set):
    return sorted(value)
  raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _stable_hash(value: Mapping[str, Any]) -> str:
  encoded = json.dumps(
    value,
    sort_keys=True,
    ensure_ascii=False,
    separators=(",", ":"),
    default=_json_default,
  ).encode("utf-8")
  return hashlib.sha256(encoded).hexdigest()


def _json_object(value: Any, *, context: str) -> dict[str, Any]:
  """Normalize a JSON/JSONB value while rejecting malformed run evidence."""

  if value is None:
    return {}
  parsed = json.loads(value) if isinstance(value, str) else value
  if not isinstance(parsed, Mapping):
    raise RuntimeError(f"{context}_NOT_JSON_OBJECT")
  return dict(parsed)


def _safe_pressure_replay_evidence(replay: Mapping[str, Any]) -> dict[str, Any]:
  """Return only report-safe replay facts, never account or portfolio data."""

  return {
    field: replay[field]
    for field in _PRESSURE_REPLAY_REPORT_FIELDS
    if field in replay
  }


def _safe_pressure_run_evidence(run_evidence: Mapping[str, Any]) -> dict[str, Any]:
  """Keep the pressure proof while excluding account and position payloads."""

  raw_parameters = _json_object(
    run_evidence.get("parameters"), context="PRESSURE_REPORT_PARAMETERS"
  )
  safe_parameters = {
    field: raw_parameters[field]
    for field in _PRESSURE_RUN_PARAMETER_FIELDS
    if field in raw_parameters
  }
  return {
    "run_id": run_evidence.get("run_id"),
    "mode": run_evidence.get("mode"),
    "status": run_evidence.get("status"),
    "parameters_sha256": run_evidence.get("parameters_sha256"),
    "parameters": safe_parameters,
    "opportunity_policy_version": run_evidence.get(
      "opportunity_policy_version"
    ),
    "opportunity_feature_schema_version": run_evidence.get(
      "opportunity_feature_schema_version"
    ),
    "evaluations": dict(run_evidence.get("evaluations") or {}),
    "instrument_profiles": [
      dict(item)
      for item in list(run_evidence.get("instrument_profiles") or [])
      if isinstance(item, Mapping)
    ],
  }


def _sanitize_pressure_baseline_for_report(
  pressure_baseline: Mapping[str, Any],
) -> dict[str, Any]:
  """Strip runtime-only account facts before a pressure result enters a report."""

  sanitized = copy.deepcopy(dict(pressure_baseline))
  replay = sanitized.get("replay")
  if isinstance(replay, Mapping):
    sanitized["replay"] = _safe_pressure_replay_evidence(replay)
  run_evidence = sanitized.get("run_evidence")
  if isinstance(run_evidence, Mapping):
    sanitized["run_evidence"] = _safe_pressure_run_evidence(run_evidence)
  return sanitized


def _require_mapping_field(
  payload: Mapping[str, Any], *, field: str, context: str
) -> dict[str, Any]:
  value = payload.get(field)
  if not isinstance(value, Mapping):
    raise AcceptanceBlockedError(f"{context}_{field.upper()}_MISSING")
  return dict(value)


def _reject_sensitive_evidence_fields(value: Any) -> None:
  """Do not let an operational addendum reintroduce account/device identifiers."""

  if isinstance(value, Mapping):
    for key, nested in value.items():
      normalized = str(key).lower().replace("_", "").replace("-", "")
      if normalized in {"accountid", "deviceid"}:
        raise AcceptanceBlockedError("OPERATIONAL_EVIDENCE_SENSITIVE_IDENTIFIER")
      _reject_sensitive_evidence_fields(nested)
  elif isinstance(value, (list, tuple)):
    for nested in value:
      _reject_sensitive_evidence_fields(nested)


def _load_operational_evidence(path: Path) -> dict[str, Any]:
  """Load a bounded, report-safe operational-evidence addendum.

  The acceptance runner cannot infer transfer and restore verification from a
  synthetic replay.  This explicit artifact records those independently
  verified facts while validating the minimum fail-closed rollout boundary.
  """

  try:
    loaded = json.loads(path.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError) as exc:
    raise AcceptanceBlockedError("OPERATIONAL_EVIDENCE_UNREADABLE") from exc
  if not isinstance(loaded, Mapping):
    raise AcceptanceBlockedError("OPERATIONAL_EVIDENCE_NOT_OBJECT")
  evidence = dict(loaded)
  _reject_sensitive_evidence_fields(evidence)
  if evidence.get("schema_version") != OPERATIONAL_EVIDENCE_SCHEMA_VERSION:
    raise AcceptanceBlockedError("OPERATIONAL_EVIDENCE_SCHEMA_UNSUPPORTED")

  transfer = _require_mapping_field(
    evidence, field="historical_tick_transfer", context="OPERATIONAL_EVIDENCE"
  )
  cumulative = _require_mapping_field(
    transfer, field="cumulative_records", context="HISTORICAL_TRANSFER"
  )
  counts = [
    cumulative.get("received"),
    cumulative.get("saved"),
    cumulative.get("verified"),
  ]
  if (
    any(not isinstance(value, int) or value < 0 for value in counts)
    or len(set(counts)) != 1
  ):
    raise AcceptanceBlockedError("HISTORICAL_TRANSFER_COUNTS_INVALID")
  coverage = _require_mapping_field(
    transfer, field="strict_coverage", context="HISTORICAL_TRANSFER"
  )
  complete = coverage.get("complete_instrument_days")
  expected = coverage.get("expected_instrument_days")
  if (
    not isinstance(complete, int)
    or not isinstance(expected, int)
    or complete < 0
    or expected <= 0
    or complete > expected
  ):
    raise AcceptanceBlockedError("HISTORICAL_TRANSFER_COVERAGE_INVALID")
  identity = _require_mapping_field(
    transfer, field="source_identity", context="HISTORICAL_TRANSFER"
  )
  failures = identity.get("failed_instrument_days")
  if not isinstance(failures, int) or failures < 0:
    raise AcceptanceBlockedError("HISTORICAL_TRANSFER_IDENTITY_INVALID")

  formal = _require_mapping_field(
    evidence, field="formal_causal_replay", context="OPERATIONAL_EVIDENCE"
  )
  completed_days = formal.get("completed_trading_days")
  requested_days = formal.get("requested_trading_days")
  if (
    not isinstance(completed_days, int)
    or not isinstance(requested_days, int)
    or completed_days < 0
    or requested_days <= 0
    or completed_days > requested_days
  ):
    raise AcceptanceBlockedError("FORMAL_CAUSAL_REPLAY_COUNTS_INVALID")

  restore = _require_mapping_field(
    evidence, field="restore_verify", context="OPERATIONAL_EVIDENCE"
  )
  if (
    str(restore.get("status") or "").upper() != "PASSED"
    or restore.get("isolated_scratch_database") is not True
    or restore.get("production_database_restored") is not False
    or restore.get("forward_migration_only") is not True
    or restore.get("scratch_cleanup_verified") is not True
    or restore.get("qmt_journal_integrity_passed") is not True
  ):
    raise AcceptanceBlockedError("RESTORE_VERIFY_BOUNDARY_INVALID")

  rollout = _require_mapping_field(
    evidence, field="rollout", context="OPERATIONAL_EVIDENCE"
  )
  for stage in ("paper", "canary", "live"):
    _require_mapping_field(rollout, field=stage, context="ROLLOUT")
  return evidence


def _load_completed_pressure_baseline(path: Path) -> dict[str, Any]:
  """Import only a terminal full-fixture pressure result without rerunning it."""

  try:
    loaded = json.loads(path.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError) as exc:
    raise AcceptanceBlockedError("COMPLETED_PRESSURE_REPORT_UNREADABLE") from exc
  if not isinstance(loaded, Mapping):
    raise AcceptanceBlockedError("COMPLETED_PRESSURE_REPORT_NOT_OBJECT")
  pressure = _require_mapping_field(
    loaded, field="pressure_baseline", context="COMPLETED_PRESSURE_REPORT"
  )
  fixture = _require_mapping_field(
    pressure, field="fixture", context="COMPLETED_PRESSURE"
  )
  replay = _require_mapping_field(
    pressure, field="replay", context="COMPLETED_PRESSURE"
  )
  boundary = _require_mapping_field(
    pressure, field="execution_boundary", context="COMPLETED_PRESSURE"
  )
  terminal = _require_mapping_field(
    pressure, field="terminal_convergence", context="COMPLETED_PRESSURE"
  )
  throughput = _require_mapping_field(
    pressure, field="throughput", context="COMPLETED_PRESSURE"
  )
  latency = _require_mapping_field(
    pressure, field="latency", context="COMPLETED_PRESSURE"
  )
  cas = _require_mapping_field(pressure, field="cas", context="COMPLETED_PRESSURE")
  database_writes = _require_mapping_field(
    pressure, field="database_write_activity", context="COMPLETED_PRESSURE"
  )
  run_evidence = _require_mapping_field(
    pressure, field="run_evidence", context="COMPLETED_PRESSURE"
  )
  parameters = _json_object(
    run_evidence.get("parameters"), context="COMPLETED_PRESSURE_PARAMETERS"
  )
  effective_ticks = throughput.get("engine_ticks_processed")
  checkpoint_attempts = cas.get("checkpoint_attempts")
  raw_evaluation_counts = run_evidence.get("evaluations")
  if not isinstance(raw_evaluation_counts, Mapping):
    raise AcceptanceBlockedError("COMPLETED_PRESSURE_EVIDENCE_INVALID")
  evaluation_counts = dict(raw_evaluation_counts)
  material_rows = evaluation_counts.get("material_rows")
  diagnostic_logical_events = evaluation_counts.get(
    "diagnostic_logical_events"
  )
  if any(
    isinstance(value, bool) or not isinstance(value, int) or value < 0
    for value in (material_rows, diagnostic_logical_events)
  ):
    raise AcceptanceBlockedError("COMPLETED_PRESSURE_EVIDENCE_INVALID")
  evaluation_logical_events = material_rows + diagnostic_logical_events
  state_writes = _require_mapping_field(
    database_writes, field="runtime_state", context="COMPLETED_PRESSURE"
  )
  tick_accounting = _pressure_tick_accounting(pressure)
  if (
    str(pressure.get("status") or "").upper()
    != "EXECUTED_SYNTHETIC_NON_HISTORICAL"
    or int(fixture.get("tick_count") or 0) != 9_600
    or str(replay.get("status") or "").upper() != "COMPLETED"
    or str(terminal.get("status") or "").upper() != "TERMINAL"
    or str(run_evidence.get("mode") or "").upper() != "BACKTEST"
    or str(run_evidence.get("status") or "").upper() != "COMPLETED"
    or not bool(pressure.get("isolated_backtest"))
    or not bool(pressure.get("no_live_or_paper_broker"))
    or str(boundary.get("strategy_run_mode") or "").upper() != "BACKTEST"
    or not bool(boundary.get("runtime_state_persist_enabled"))
    or bool(boundary.get("qmt_invocation"))
    or bool(boundary.get("paper_or_live_command"))
    or parameters.get("replay_acceptance") != "V3_PRESSURE_BASELINE"
    or not parameters.get(_INTERNAL_V3_PRESSURE_RUNTIME_STATE_PERSISTENCE_KEY)
    or not isinstance(effective_ticks, int)
    or effective_ticks <= 0
    or not isinstance(checkpoint_attempts, int)
    or checkpoint_attempts <= 0
    or not bool(tick_accounting.get("accounting_passed"))
    or int(dict(latency.get("engine_tick") or {}).get("sample_count") or 0)
    != effective_ticks
    or evaluation_logical_events != effective_ticks
    or not isinstance(state_writes.get("state_upsert_attempts"), int)
    or state_writes["state_upsert_attempts"] <= 0
    or int(state_writes.get("snapshot_save_failures") or 0) != 0
    or not isinstance(latency.get("engine_tick"), Mapping)
  ):
    raise AcceptanceBlockedError("COMPLETED_PRESSURE_EVIDENCE_INVALID")
  imported = copy.deepcopy(pressure)
  imported["tick_accounting"] = tick_accounting
  return _sanitize_pressure_baseline_for_report(imported)


def _value_as_str(value: Any) -> str:
  return str(value or "").strip()


@dataclass(frozen=True)
class HeldInstrument:
  """One aggregated, actual held instrument at a D-1 account snapshot."""

  instrument_code: str
  volume: int
  available_volume: int
  replayable: bool
  reason: str = ""
  last_price: float = 0.0

  def to_dict(self) -> dict[str, Any]:
    return {
      "instrument_code": self.instrument_code,
      "volume": self.volume,
      "available_volume": self.available_volume,
      "replayable": self.replayable,
      "reason": self.reason or None,
    }


@dataclass(frozen=True)
class SnapshotPortfolio:
  """An immutable account D-1 snapshot and every positive holding in it."""

  account_id: str
  snapshot_id: str
  snapshot_date: date
  source: str
  data_quality: str
  holdings: tuple[HeldInstrument, ...]

  @property
  def instrument_codes(self) -> tuple[str, ...]:
    return tuple(item.instrument_code for item in self.holdings)

  @property
  def non_replayable(self) -> tuple[HeldInstrument, ...]:
    return tuple(item for item in self.holdings if not item.replayable)

  def to_dict(self) -> dict[str, Any]:
    return {
      "account_sha256_16": _digest(self.account_id),
      "snapshot_sha256_16": _digest(self.snapshot_id),
      "snapshot_date": self.snapshot_date.isoformat(),
      "source": self.source or None,
      "data_quality": self.data_quality or None,
      "holding_count": len(self.holdings),
      "holdings": [item.to_dict() for item in self.holdings],
    }


@dataclass(frozen=True)
class ReplayWindow:
  """The next up-to-N real trading dates after one D-1 snapshot."""

  snapshot: SnapshotPortfolio
  trading_dates: tuple[date, ...]
  requested_trading_days: int = DEFAULT_TRADING_DAYS

  def to_dict(self) -> dict[str, Any]:
    return {
      "snapshot": self.snapshot.to_dict(),
      "requested_trading_days": self.requested_trading_days,
      "trading_dates": [item.isoformat() for item in self.trading_dates],
    }


@dataclass(frozen=True)
class TickDayInspection:
  """One Engine-equivalent strict Tick quality inspection."""

  instrument_code: str
  trading_date: date
  complete: bool
  classification: str
  reason_codes: tuple[str, ...]
  statistics: Mapping[str, Any]
  message: str = ""

  @classmethod
  def from_engine_result(
    cls,
    *,
    instrument_code: str,
    trading_date: date,
    result: Mapping[str, Any],
  ) -> "TickDayInspection":
    return cls(
      instrument_code=instrument_code,
      trading_date=trading_date,
      complete=bool(result.get("complete")),
      classification=_value_as_str(result.get("classification")) or "UNAVAILABLE",
      reason_codes=tuple(str(item) for item in result.get("reason_codes") or []),
      statistics=dict(result.get("statistics") or {}),
      message=_value_as_str(result.get("message")),
    )

  def to_dict(self) -> dict[str, Any]:
    return {
      "instrument_code": self.instrument_code,
      "trade_date": self.trading_date.isoformat(),
      "complete": self.complete,
      "classification": self.classification,
      "reason_codes": list(self.reason_codes),
      "statistics": dict(self.statistics),
      "message": self.message or None,
    }


@dataclass(frozen=True)
class WindowAudit:
  """Coverage evidence for every held stock/day in one proposed replay window."""

  window: ReplayWindow
  inspections: Mapping[tuple[str, date], TickDayInspection]

  @property
  def expected_pair_count(self) -> int:
    return len(self.window.snapshot.holdings) * len(self.window.trading_dates)

  @property
  def completed_pair_count(self) -> int:
    return sum(item.complete for item in self.inspections.values())

  @property
  def full_shared_dates(self) -> tuple[date, ...]:
    codes = self.window.snapshot.instrument_codes
    return tuple(
      day
      for day in self.window.trading_dates
      if codes
      and all(self.inspections[(code, day)].complete for code in codes)
    )

  @property
  def contiguous_shared_prefix_dates(self) -> tuple[date, ...]:
    full_dates = set(self.full_shared_dates)
    prefix: list[date] = []
    for day in self.window.trading_dates:
      if day not in full_dates:
        break
      prefix.append(day)
    return tuple(prefix)

  @property
  def missing(self) -> tuple[TickDayInspection, ...]:
    return tuple(
      self.inspections[(code, day)]
      for day in self.window.trading_dates
      for code in self.window.snapshot.instrument_codes
      if not self.inspections[(code, day)].complete
    )

  @property
  def coverage_complete(self) -> bool:
    return (
      bool(self.window.snapshot.holdings)
      and len(self.window.trading_dates) == self.window.requested_trading_days
      and self.completed_pair_count == self.expected_pair_count
    )

  @property
  def replayable_holdings_complete(self) -> bool:
    return not self.window.snapshot.non_replayable

  def blockers(self, *, abnormal_dates: Iterable[date] = ()) -> list[str]:
    blockers: list[str] = []
    if not self.window.snapshot.holdings:
      blockers.append("NO_POSITIVE_HOLDINGS")
    if len(self.window.trading_dates) != self.window.requested_trading_days:
      blockers.append("TRADING_CALENDAR_WINDOW_INCOMPLETE")
    if self.window.snapshot.non_replayable:
      blockers.append("HELD_INSTRUMENT_NOT_REPLAYABLE")
    if self.completed_pair_count != self.expected_pair_count:
      blockers.append("ALL_HOLDINGS_TICK_COVERAGE_INCOMPLETE")
    declared_abnormal_dates = set(abnormal_dates)
    if not declared_abnormal_dates:
      blockers.append("ABNORMAL_DAY_EVIDENCE_NOT_DECLARED")
    elif not declared_abnormal_dates.intersection(self.window.trading_dates):
      blockers.append("DECLARED_ABNORMAL_DAY_OUTSIDE_WINDOW")
    return blockers

  def to_dict(self, *, abnormal_dates: Iterable[date] = ()) -> dict[str, Any]:
    reason_counts = Counter(
      reason for item in self.missing for reason in item.reason_codes
    )
    return {
      **self.window.to_dict(),
      "coverage": {
        "expected_instrument_days": self.expected_pair_count,
        "complete_instrument_days": self.completed_pair_count,
        "coverage_ratio": (
          self.completed_pair_count / self.expected_pair_count
          if self.expected_pair_count
          else None
        ),
        "full_shared_dates": [item.isoformat() for item in self.full_shared_dates],
        "contiguous_shared_prefix_dates": [
          item.isoformat() for item in self.contiguous_shared_prefix_dates
        ],
        "reason_counts": dict(sorted(reason_counts.items())),
      },
      "formal_gate_blockers": self.blockers(abnormal_dates=abnormal_dates),
      "missing_instrument_days": [item.to_dict() for item in self.missing],
    }


@dataclass(frozen=True)
class SourceIdentityAudit:
  """Evidence that every selected Tick can be paged by strict source identity."""

  passed: bool
  records_read: int
  pages_read: int
  per_instrument_day: Mapping[str, Mapping[str, Any]]
  failure: Optional[Mapping[str, Any]] = None
  source: str = "HISTORICAL_MARKET_DATA_SERVICE"

  def to_dict(self) -> dict[str, Any]:
    return {
      "passed": self.passed,
      "records_read": self.records_read,
      "pages_read": self.pages_read,
      "pagination": "STRICT_SOURCE_IDENTITY_KEYSET",
      "source": self.source,
      "engine_global_order_key": (
        "(continuity_generation, source_time_ms, tick_ordinal, "
        "event_type, instrument_code, period)"
      ),
      "per_instrument_day": {
        key: dict(value) for key, value in sorted(self.per_instrument_day.items())
      },
      "failure": dict(self.failure) if self.failure else None,
    }


@dataclass(frozen=True)
class SyntheticPressureFixture:
  """Deterministic, non-historical Tick load for a machine SLO baseline.

  The fixture borrows only the actual D-1 holdings universe.  Its prices,
  source identities, and market timestamps are generated and therefore cannot
  be interpreted as market-history evidence.
  """

  snapshot_date: date
  trading_dates: tuple[date, ...]
  instrument_codes: tuple[str, ...]
  ticks_per_instrument_day: int
  fixture_sha256: str
  ticks_by_instrument: Mapping[str, tuple[Tick, ...]]

  @property
  def tick_count(self) -> int:
    return sum(len(items) for items in self.ticks_by_instrument.values())

  def to_dict(self) -> dict[str, Any]:
    return {
      "kind": "SYNTHETIC_NON_HISTORICAL",
      "schema_version": 1,
      "fixture_sha256": self.fixture_sha256,
      "snapshot_date": self.snapshot_date.isoformat(),
      "trading_dates": [item.isoformat() for item in self.trading_dates],
      "held_instruments": list(self.instrument_codes),
      "ticks_per_instrument_day": self.ticks_per_instrument_day,
      "tick_count": self.tick_count,
      "market_time_policy": (
        "Shanghai continuous sessions only: 09:30-11:30 and 13:00-15:00"
      ),
      "source_identity_policy": (
        "explicit (continuity_generation=1, source_time_ms, tick_ordinal) "
        "with globally deterministic ordinal per instrument"
      ),
      "not_historical_market_data": True,
    }


def _synthetic_session_timestamps(
  trading_date: date,
  count: int,
) -> list[datetime]:
  if count <= 0:
    return []
  morning_count = count // 2
  afternoon_count = count - morning_count
  sessions = (
    (clock_time(9, 30), clock_time(11, 29, 59), morning_count),
    (clock_time(13, 0), clock_time(14, 59, 59), afternoon_count),
  )
  timestamps: list[datetime] = []
  shanghai = ZoneInfo("Asia/Shanghai")
  for start, end, session_count in sessions:
    if session_count <= 0:
      continue
    lower = datetime.combine(trading_date, start, tzinfo=shanghai)
    upper = datetime.combine(trading_date, end, tzinfo=shanghai)
    if session_count == 1:
      timestamps.append(lower)
      continue
    span_seconds = int((upper - lower).total_seconds())
    timestamps.extend(
      lower + timedelta(seconds=round(index * span_seconds / (session_count - 1)))
      for index in range(session_count)
    )
  return timestamps


def build_synthetic_pressure_fixture(
  audit: WindowAudit,
  trading_dates: Sequence[date],
  *,
  ticks_per_instrument_day: int = 600,
) -> SyntheticPressureFixture:
  """Create explicit causal identities on legal intraday timestamps.

  This function is intentionally deterministic.  Given the same D-1 holdings,
  trading dates, and sample size it emits byte-for-byte equivalent identity and
  price fields, then records a fixture hash in the report.
  """

  if ticks_per_instrument_day < 2:
    raise ValueError("ticks_per_instrument_day must be at least 2")
  selected_dates = tuple(trading_dates)
  if not selected_dates:
    raise AcceptanceBlockedError("SYNTHETIC_PRESSURE_NO_TRADING_DATES")
  held = audit.window.snapshot.holdings
  if not held:
    raise AcceptanceBlockedError("SYNTHETIC_PRESSURE_NO_HELD_INSTRUMENTS")
  fixture_hash = hashlib.sha256()
  fixture_hash.update(b"quantx-v3-synthetic-pressure-v1\n")
  fixture_hash.update(audit.window.snapshot.snapshot_date.isoformat().encode("ascii"))
  fixture_hash.update(b"\n")
  fixture_hash.update(
    ",".join(item.isoformat() for item in selected_dates).encode("ascii")
  )
  fixture_hash.update(f"\n{ticks_per_instrument_day}\n".encode("ascii"))
  ticks_by_instrument: dict[str, list[Tick]] = {
    item.instrument_code: [] for item in held
  }
  stream_sequence = 0
  for trading_date in selected_dates:
    timestamps = _synthetic_session_timestamps(
      trading_date, ticks_per_instrument_day
    )
    for point_index, timestamp in enumerate(timestamps):
      source_time_ms = int(timestamp.timestamp() * 1000)
      for instrument_index, item in enumerate(held):
        # The deterministic movement remains strictly inside the supplied
        # synthetic price limits and has no claim to forecast or recreate a
        # market path.
        base_price = item.last_price if item.last_price > 0 else 10.0 + instrument_index
        movement = ((point_index % 17) - 8) * 0.00025
        price = round(max(0.01, base_price * (1.0 + movement)), 4)
        tick = Tick(
          stock_code=item.instrument_code,
          period="tick",
          time=timestamp,
          last_price=price,
          open=round(base_price, 4),
          high=round(max(base_price, price) * 1.0005, 4),
          low=round(min(base_price, price) * 0.9995, 4),
          last_close=round(base_price, 4),
          amount=round(price * (point_index + 1) * 100, 4),
          volume=float((point_index + 1) * 100),
          pvolume=float((point_index + 1) * 100),
          tickvol=100.0,
          stock_status=0,
          open_int=0,
          last_settlement_price=0.0,
          settlement_price=0.0,
          transaction_num=stream_sequence + 1,
          price_tick=0.01,
          up_stop_price=round(base_price * 1.1, 4),
          down_stop_price=round(base_price * 0.9, 4),
          ask_price=[round(price + 0.01 * (level + 1), 4) for level in range(5)],
          bid_price=[round(max(0.01, price - 0.01 * (level + 1)), 4) for level in range(5)],
          ask_vol=[1_000.0] * 5,
          bid_vol=[1_000.0] * 5,
          source_time_ms=source_time_ms,
          tick_ordinal=instrument_index,
          continuity_generation=1,
          market_stream_id="v3-synthetic-pressure-v1",
          market_stream_sequence=stream_sequence,
          market_stream_reset=False,
        )
        ticks_by_instrument[item.instrument_code].append(tick)
        fixture_hash.update(
          (
            f"{item.instrument_code}|{timestamp.isoformat()}|{source_time_ms}|"
            f"{instrument_index}|{price:.4f}|{stream_sequence}\n"
          ).encode("utf-8")
        )
        stream_sequence += 1
  return SyntheticPressureFixture(
    snapshot_date=audit.window.snapshot.snapshot_date,
    trading_dates=selected_dates,
    instrument_codes=tuple(item.instrument_code for item in held),
    ticks_per_instrument_day=ticks_per_instrument_day,
    fixture_sha256=fixture_hash.hexdigest(),
    ticks_by_instrument={
      code: tuple(items) for code, items in ticks_by_instrument.items()
    },
  )


def _pressure_tick_accounting(pressure: Mapping[str, Any]) -> dict[str, Any]:
  """Explain the deterministic close-window filter in a synthetic fixture.

  The backtest executor permits the afternoon continuous session only while
  ``local_time < 14:57``.  The fixture intentionally extends to 14:59:59 to
  prove that boundary, so those closing points are policy-filtered rather than
  silently treated as a dropped workload.
  """

  fixture = dict(pressure.get("fixture") or {})
  requested_ticks = fixture.get("tick_count")
  effective_ticks = dict(pressure.get("throughput") or {}).get(
    "engine_ticks_processed"
  )
  tick_count_per_instrument_day = fixture.get("ticks_per_instrument_day")
  raw_dates = list(fixture.get("trading_dates") or [])
  instrument_count = len(list(fixture.get("held_instruments") or []))
  if (
    not isinstance(requested_ticks, int)
    or not isinstance(effective_ticks, int)
    or not isinstance(tick_count_per_instrument_day, int)
    or tick_count_per_instrument_day < 2
    or not raw_dates
    or instrument_count <= 0
  ):
    return {}
  try:
    trading_dates = tuple(date.fromisoformat(str(item)) for item in raw_dates)
  except ValueError:
    return {}
  policy_filtered_timestamps = [
    timestamp
    for timestamp in _synthetic_session_timestamps(
      trading_dates[0], tick_count_per_instrument_day
    )
    if timestamp.timetz().replace(tzinfo=None) >= clock_time(14, 57)
  ]
  policy_filtered_per_instrument_day = len(policy_filtered_timestamps)
  instrument_day_count = instrument_count * len(trading_dates)
  expected_policy_filtered_ticks = (
    policy_filtered_per_instrument_day * instrument_day_count
  )
  policy_filtered_ticks = requested_ticks - effective_ticks
  return {
    "requested_fixture_ticks": requested_ticks,
    "engine_ticks_processed": effective_ticks,
    "policy_filtered_ticks": policy_filtered_ticks,
    "policy_filtered_per_instrument_day": policy_filtered_per_instrument_day,
    "instrument_day_count": instrument_day_count,
    "expected_policy_filtered_ticks": expected_policy_filtered_ticks,
    "continuous_pm_policy": "13:00 <= local_time < 14:57",
    "policy_filtered_time_range": (
      "{}..{}".format(
        policy_filtered_timestamps[0].strftime("%H:%M:%S"),
        policy_filtered_timestamps[-1].strftime("%H:%M:%S"),
      )
      if policy_filtered_timestamps
      else None
    ),
    "accounting_passed": (
      requested_ticks == instrument_day_count * tick_count_per_instrument_day
      and policy_filtered_ticks == expected_policy_filtered_ticks
    ),
  }


@dataclass
class LatencyAccumulator:
  """Bounded exact latency recorder; reports incomplete precision if capped."""

  max_samples: int = 2_000_000
  values_ns: list[int] = field(default_factory=list)
  dropped_samples: int = 0

  def observe(self, elapsed_ns: int) -> None:
    if len(self.values_ns) < self.max_samples:
      self.values_ns.append(max(0, int(elapsed_ns)))
    else:
      self.dropped_samples += 1

  @staticmethod
  def _percentile(values: Sequence[int], quantile: float) -> Optional[float]:
    if not values:
      return None
    ordered = sorted(values)
    if len(ordered) == 1:
      return float(ordered[0])
    index = (len(ordered) - 1) * quantile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction

  def to_dict(self) -> dict[str, Any]:
    def milliseconds(quantile: float) -> Optional[float]:
      value = self._percentile(self.values_ns, quantile)
      return round(value / 1_000_000, 6) if value is not None else None

    return {
      "sample_count": len(self.values_ns),
      "dropped_samples": self.dropped_samples,
      "quantiles_exact": self.dropped_samples == 0,
      "unit": "milliseconds",
      "p50": milliseconds(0.50),
      "p95": milliseconds(0.95),
      "p99": milliseconds(0.99),
      "max": (
        round(max(self.values_ns) / 1_000_000, 6) if self.values_ns else None
      ),
    }


@dataclass
class DatabaseWriteCounters:
  """Session-level write activity observed only inside the benchmark process."""

  commits: int = 0
  flushes: int = 0
  dml_executes: int = 0
  commit_latency: LatencyAccumulator = field(default_factory=LatencyAccumulator)
  flush_latency: LatencyAccumulator = field(default_factory=LatencyAccumulator)
  dml_execute_latency: LatencyAccumulator = field(
    default_factory=LatencyAccumulator
  )
  commit_call_sites: Counter[str] = field(default_factory=Counter)
  flush_call_sites: Counter[str] = field(default_factory=Counter)
  dml_execute_call_sites: Counter[str] = field(default_factory=Counter)

  def to_dict(self) -> dict[str, Any]:
    return {
      "commit_calls": self.commits,
      "flush_calls": self.flushes,
      "dml_execute_calls": self.dml_executes,
      "latency": {
        "commit": self.commit_latency.to_dict(),
        "flush": self.flush_latency.to_dict(),
        "dml_execute": self.dml_execute_latency.to_dict(),
      },
      "call_sites": {
        "commit": dict(sorted(self.commit_call_sites.items())),
        "flush": dict(sorted(self.flush_call_sites.items())),
        "dml_execute": dict(sorted(self.dml_execute_call_sites.items())),
      },
    }


@dataclass
class RuntimeStateDatabaseCounters:
  """Durable state/position operations observed in the pressure process."""

  snapshot_save_calls: int = 0
  snapshot_save_failures: int = 0
  state_upsert_attempts: int = 0
  state_upsert_rejected: int = 0
  position_replace_snapshot_calls: int = 0
  position_update_existing_snapshot_calls: int = 0
  position_rows_submitted: int = 0
  state_upsert_latency: LatencyAccumulator = field(
    default_factory=LatencyAccumulator
  )
  position_replace_snapshot_latency: LatencyAccumulator = field(
    default_factory=LatencyAccumulator
  )
  position_update_existing_snapshot_latency: LatencyAccumulator = field(
    default_factory=LatencyAccumulator
  )

  def to_dict(self) -> dict[str, Any]:
    return {
      "snapshot_save_calls": self.snapshot_save_calls,
      "snapshot_save_failures": self.snapshot_save_failures,
      "state_upsert_attempts": self.state_upsert_attempts,
      "state_upsert_rejected": self.state_upsert_rejected,
      "position_replace_snapshot_calls": self.position_replace_snapshot_calls,
      "position_update_existing_snapshot_calls": (
        self.position_update_existing_snapshot_calls
      ),
      "position_snapshot_calls": (
        self.position_replace_snapshot_calls
        + self.position_update_existing_snapshot_calls
      ),
      "position_rows_submitted": self.position_rows_submitted,
      "latency": {
        "state_upsert": self.state_upsert_latency.to_dict(),
        "position_replace_snapshot": (
          self.position_replace_snapshot_latency.to_dict()
        ),
        "position_update_existing_snapshot": (
          self.position_update_existing_snapshot_latency.to_dict()
        ),
      },
    }


class BenchmarkInstrumentation:
  """Temporarily collect offline BACKTEST timings without changing Engine code."""

  def __init__(self) -> None:
    self.engine_tick = LatencyAccumulator()
    self.strategy_evaluation = LatencyAccumulator()
    self.state_checkpoint = LatencyAccumulator()
    self.state_snapshot = LatencyAccumulator()
    self.db_writes = DatabaseWriteCounters()
    self.runtime_state_db = RuntimeStateDatabaseCounters()
    self._originals: dict[str, Any] = {}

  @staticmethod
  def _database_call_site() -> str:
    """Return the nearest project call site without collecting full stacks.

    This runs only inside the explicitly invoked offline acceptance process.
    It gives bounded attribution for the otherwise opaque AsyncSession commit
    fan-out without changing production control flow or persistence behavior.
    """
    frame = inspect.currentframe()
    current = frame.f_back if frame is not None else None
    try:
      while current is not None:
        filename = Path(current.f_code.co_filename)
        function = current.f_code.co_name
        path = filename.as_posix()
        if (
          filename.name == Path(__file__).name
          and function
          in {
            "_database_call_site",
            "commit_wrapper",
            "flush_wrapper",
            "execute_wrapper",
          }
        ):
          current = current.f_back
          continue
        if "site-packages/sqlalchemy" in path.replace("\\", "/"):
          current = current.f_back
          continue
        return f"{filename.name}:{function}"
    finally:
      del frame
      del current
    return "unknown"

  async def __aenter__(self) -> "BenchmarkInstrumentation":
    self._originals["process_tick"] = StrategyExecutor._process_tick
    self._originals["strategy_step"] = AshareIntradayTAssistantStrategy.step
    self._originals["checkpoint"] = RuntimeStateManager.checkpoint_strategy_state_changes
    self._originals["save_snapshot"] = RuntimeStateManager.save_snapshot
    self._originals["state_upsert"] = StrategyRunStateRepository.upsert_state
    self._originals["position_replace"] = (
      StrategyRunPositionRepository.replace_positions_snapshot
    )
    self._originals["position_update_existing"] = (
      StrategyRunPositionRepository.update_existing_positions_snapshot
    )
    self._originals["commit"] = AsyncSession.commit
    self._originals["flush"] = AsyncSession.flush
    self._originals["execute"] = AsyncSession.execute

    original_process_tick = self._originals["process_tick"]
    original_strategy_step = self._originals["strategy_step"]
    original_checkpoint = self._originals["checkpoint"]
    original_save_snapshot = self._originals["save_snapshot"]
    original_state_upsert = self._originals["state_upsert"]
    original_position_replace = self._originals["position_replace"]
    original_position_update_existing = self._originals["position_update_existing"]
    original_commit = self._originals["commit"]
    original_flush = self._originals["flush"]
    original_execute = self._originals["execute"]

    async def process_tick_wrapper(executor: Any, runtime: Any, tick: Any) -> Any:
      started = wall_clock.perf_counter_ns()
      try:
        return await original_process_tick(executor, runtime, tick)
      finally:
        self.engine_tick.observe(wall_clock.perf_counter_ns() - started)

    async def strategy_step_wrapper(strategy: Any, strategy_input: Any) -> Any:
      started = wall_clock.perf_counter_ns()
      try:
        return await original_strategy_step(strategy, strategy_input)
      finally:
        self.strategy_evaluation.observe(wall_clock.perf_counter_ns() - started)

    async def checkpoint_wrapper(state: Any, *args: Any, **kwargs: Any) -> Any:
      started = wall_clock.perf_counter_ns()
      try:
        return await original_checkpoint(state, *args, **kwargs)
      finally:
        self.state_checkpoint.observe(wall_clock.perf_counter_ns() - started)

    async def save_snapshot_wrapper(state: Any, *args: Any, **kwargs: Any) -> Any:
      self.runtime_state_db.snapshot_save_calls += 1
      started = wall_clock.perf_counter_ns()
      try:
        result = await original_save_snapshot(state, *args, **kwargs)
        if result is False:
          self.runtime_state_db.snapshot_save_failures += 1
        return result
      finally:
        self.state_snapshot.observe(wall_clock.perf_counter_ns() - started)

    async def state_upsert_wrapper(repo: Any, *args: Any, **kwargs: Any) -> Any:
      self.runtime_state_db.state_upsert_attempts += 1
      started = wall_clock.perf_counter_ns()
      try:
        result = await original_state_upsert(repo, *args, **kwargs)
        if result is False:
          self.runtime_state_db.state_upsert_rejected += 1
        return result
      finally:
        self.runtime_state_db.state_upsert_latency.observe(
          wall_clock.perf_counter_ns() - started
        )

    def submitted_positions(args: tuple[Any, ...], kwargs: Mapping[str, Any]) -> int:
      positions = kwargs.get("positions")
      if positions is None and len(args) >= 2:
        positions = args[1]
      return len(dict(positions or {}))

    async def position_replace_wrapper(repo: Any, *args: Any, **kwargs: Any) -> Any:
      self.runtime_state_db.position_replace_snapshot_calls += 1
      self.runtime_state_db.position_rows_submitted += submitted_positions(
        args,
        kwargs,
      )
      started = wall_clock.perf_counter_ns()
      try:
        return await original_position_replace(repo, *args, **kwargs)
      finally:
        self.runtime_state_db.position_replace_snapshot_latency.observe(
          wall_clock.perf_counter_ns() - started
        )

    async def position_update_existing_wrapper(
      repo: Any,
      *args: Any,
      **kwargs: Any,
    ) -> Any:
      self.runtime_state_db.position_update_existing_snapshot_calls += 1
      self.runtime_state_db.position_rows_submitted += submitted_positions(
        args,
        kwargs,
      )
      started = wall_clock.perf_counter_ns()
      try:
        return await original_position_update_existing(repo, *args, **kwargs)
      finally:
        self.runtime_state_db.position_update_existing_snapshot_latency.observe(
          wall_clock.perf_counter_ns() - started
        )

    async def commit_wrapper(session: Any, *args: Any, **kwargs: Any) -> Any:
      source = self._database_call_site()
      self.db_writes.commits += 1
      self.db_writes.commit_call_sites[source] += 1
      started = wall_clock.perf_counter_ns()
      try:
        return await original_commit(session, *args, **kwargs)
      finally:
        self.db_writes.commit_latency.observe(
          wall_clock.perf_counter_ns() - started
        )

    async def flush_wrapper(session: Any, *args: Any, **kwargs: Any) -> Any:
      source = self._database_call_site()
      self.db_writes.flushes += 1
      self.db_writes.flush_call_sites[source] += 1
      started = wall_clock.perf_counter_ns()
      try:
        return await original_flush(session, *args, **kwargs)
      finally:
        self.db_writes.flush_latency.observe(
          wall_clock.perf_counter_ns() - started
        )

    async def execute_wrapper(session: Any, statement: Any, *args: Any, **kwargs: Any) -> Any:
      is_dml = bool(getattr(statement, "is_dml", False))
      if is_dml:
        source = self._database_call_site()
        self.db_writes.dml_executes += 1
        self.db_writes.dml_execute_call_sites[source] += 1
        started = wall_clock.perf_counter_ns()
        try:
          return await original_execute(session, statement, *args, **kwargs)
        finally:
          self.db_writes.dml_execute_latency.observe(
            wall_clock.perf_counter_ns() - started
          )
      return await original_execute(session, statement, *args, **kwargs)

    StrategyExecutor._process_tick = process_tick_wrapper
    AshareIntradayTAssistantStrategy.step = strategy_step_wrapper
    RuntimeStateManager.checkpoint_strategy_state_changes = checkpoint_wrapper
    RuntimeStateManager.save_snapshot = save_snapshot_wrapper
    StrategyRunStateRepository.upsert_state = state_upsert_wrapper
    StrategyRunPositionRepository.replace_positions_snapshot = position_replace_wrapper
    StrategyRunPositionRepository.update_existing_positions_snapshot = (
      position_update_existing_wrapper
    )
    AsyncSession.commit = commit_wrapper
    AsyncSession.flush = flush_wrapper
    AsyncSession.execute = execute_wrapper
    return self

  async def __aexit__(self, *_: Any) -> None:
    StrategyExecutor._process_tick = self._originals["process_tick"]
    AshareIntradayTAssistantStrategy.step = self._originals["strategy_step"]
    RuntimeStateManager.checkpoint_strategy_state_changes = self._originals[
      "checkpoint"
    ]
    RuntimeStateManager.save_snapshot = self._originals["save_snapshot"]
    StrategyRunStateRepository.upsert_state = self._originals["state_upsert"]
    StrategyRunPositionRepository.replace_positions_snapshot = self._originals[
      "position_replace"
    ]
    StrategyRunPositionRepository.update_existing_positions_snapshot = self._originals[
      "position_update_existing"
    ]
    AsyncSession.commit = self._originals["commit"]
    AsyncSession.flush = self._originals["flush"]
    AsyncSession.execute = self._originals["execute"]


async def load_snapshot_portfolios(
  *,
  account_id: Optional[str] = None,
) -> list[SnapshotPortfolio]:
  """Load every persisted ACCOUNT D-1 snapshot for one unambiguous account.

  We intentionally retain non-replayable holdings in the audit instead of
  silently copying ``TTradeReplayService``'s historical skip list.  A pressure
  baseline may only run when that snapshot has no such holdings.
  """

  async for db in get_async_db():
    snapshot_repo = DailyAssetSnapshotRepository(db)
    position_repo = DailyAssetPositionSnapshotRepository(db)
    snapshots = await snapshot_repo.find_range(scope_type="ACCOUNT", limit=2_000)
    accounts = sorted(
      {
        _value_as_str(item.account_id)
        for item in snapshots
        if _value_as_str(item.account_id)
      }
    )
    selected_account = _value_as_str(account_id)
    if not selected_account:
      if len(accounts) != 1:
        raise AcceptanceBlockedError(
          "ACCOUNT_ID_AMBIGUOUS: specify --account-id when ACCOUNT snapshots "
          f"belong to {len(accounts)} accounts"
        )
      selected_account = accounts[0]
    if selected_account not in accounts:
      raise AcceptanceBlockedError("ACCOUNT_SNAPSHOTS_NOT_FOUND")

    selected = [
      item
      for item in snapshots
      if _value_as_str(item.account_id) == selected_account
      and _value_as_str(item.scope_type).upper() == "ACCOUNT"
    ]
    positions_by_snapshot: dict[str, list[dict[str, Any]]] = {}
    instrument_codes: set[str] = set()
    for snapshot in selected:
      positions = TTradeReplayService._aggregate_snapshot_positions(
        await position_repo.find_by_snapshot(snapshot.id)
      )
      positions_by_snapshot[snapshot.id] = positions
      instrument_codes.update(
        _value_as_str(item.get("stock_code")).upper()
        for item in positions
        if _value_as_str(item.get("stock_code"))
      )

    references = {
      _value_as_str(item.id).upper(): item
      for item in await InstrumentRepository(db).find_by_ids(sorted(instrument_codes))
    }
    portfolios: list[SnapshotPortfolio] = []
    for snapshot in sorted(selected, key=lambda item: (item.trade_date, item.id)):
      held: list[HeldInstrument] = []
      for position in positions_by_snapshot[snapshot.id]:
        code = _value_as_str(position.get("stock_code")).upper()
        if not code:
          continue
        volume = max(0, int(position.get("volume", 0) or 0))
        available_volume = min(
          volume,
          max(0, int(position.get("available_volume", 0) or 0)),
        )
        if volume <= 0:
          continue
        reference = references.get(code)
        instrument_name = _value_as_str(
          position.get("instrument_name") or getattr(reference, "name", "")
        )
        lifecycle_complete = bool(
          reference is not None
          and getattr(reference, "open_date", None) is not None
          and getattr(reference, "expire_date", None) is not None
          and instrument_name
        )
        if volume < 100 or available_volume < 100:
          reason = "YESTERDAY_AVAILABLE_VOLUME_LT_ONE_LOT"
        elif not lifecycle_complete:
          reason = "INSTRUMENT_LIFECYCLE_REFERENCE_INCOMPLETE"
        else:
          reason = ""
        held.append(
          HeldInstrument(
            instrument_code=code,
          volume=volume,
          available_volume=available_volume,
          replayable=not reason,
          reason=reason,
          last_price=max(0.0, float(position.get("last_price", 0.0) or 0.0)),
        )
        )
      portfolios.append(
        SnapshotPortfolio(
          account_id=selected_account,
          snapshot_id=str(snapshot.id),
          snapshot_date=snapshot.trade_date,
          source=_value_as_str(snapshot.source),
          data_quality=_value_as_str(snapshot.data_quality),
          holdings=tuple(sorted(held, key=lambda item: item.instrument_code)),
        )
      )
    return portfolios
  raise AcceptanceBlockedError("DATABASE_SESSION_UNAVAILABLE")


async def resolve_completed_trading_dates(
  *,
  requested_days: int,
  end_date: Optional[date] = None,
  start_after: Optional[date] = None,
  as_of_date: Optional[date] = None,
  require_exact: bool = True,
  calendar_fetcher: Optional[
    Callable[[date, date], Awaitable[Sequence[date]]]
  ] = None,
) -> tuple[date, ...]:
  """Resolve only completed SH trading dates from one bounded calendar query.

  ``as_of_date`` is always excluded, even if it is a trading day after the
  close.  With ``start_after`` the function retains the first N completed
  dates after a D-1 snapshot (the formal causal direction); otherwise it
  retains the most recent N completed dates ending at ``end_date``.  The
  latter is the shared rolling 5/20-day policy.
  """

  if type(requested_days) is not int or requested_days <= 0:
    raise ValueError("requested_days must be positive")
  current = as_of_date or datetime.now(_SHANGHAI).date()
  if not isinstance(current, date) or isinstance(current, datetime):
    raise ValueError("as_of_date must be a date")
  upper = min(end_date or current, current - timedelta(days=1))
  if start_after is not None:
    if not isinstance(start_after, date) or isinstance(start_after, datetime):
      raise ValueError("start_after must be a date")
    lower = start_after + timedelta(days=1)
    upper = min(upper, start_after + timedelta(days=_CALENDAR_MAX_DAYS_PER_REQUEST))
  else:
    lower = upper - timedelta(days=_CALENDAR_MAX_DAYS_PER_REQUEST)
  if upper < lower:
    selected: tuple[date, ...] = ()
  else:
    if calendar_fetcher is None:
      helper = TradingDateHelper()

      async def calendar_fetcher(start: date, end: date) -> Sequence[date]:
        return await helper.get_trading_calendar(
          market="SH", start_date=start, end_date=end
        )

    calendar = await calendar_fetcher(lower, upper)
    completed = tuple(
      sorted(
        {
          item
          for item in calendar
          if isinstance(item, date)
          and not isinstance(item, datetime)
          and lower <= item <= upper
          and item < current
          and (start_after is None or item > start_after)
        }
      )
    )
    selected = (
      completed[:requested_days] if start_after is not None else completed[-requested_days:]
    )
  if require_exact and len(selected) != requested_days:
    raise AcceptanceBlockedError("COMPLETED_TRADING_DAYS_INSUFFICIENT")
  return selected


async def build_replay_windows(
  snapshots: Sequence[SnapshotPortfolio],
  *,
  requested_trading_days: int = DEFAULT_TRADING_DAYS,
  as_of_date: Optional[date] = None,
) -> list[ReplayWindow]:
  """Enumerate each D-1 snapshot's next up-to-N actual SH trading dates."""

  if requested_trading_days <= 0:
    raise ValueError("requested_trading_days must be positive")
  if not snapshots:
    return []
  windows: list[ReplayWindow] = []
  for snapshot in snapshots:
    future_dates = await resolve_completed_trading_dates(
      requested_days=requested_trading_days,
      start_after=snapshot.snapshot_date,
      as_of_date=as_of_date,
      require_exact=False,
    )
    windows.append(
      ReplayWindow(
        snapshot=snapshot,
        trading_dates=future_dates,
        requested_trading_days=requested_trading_days,
      )
    )
  return windows


async def audit_tick_coverage(
  windows: Sequence[ReplayWindow],
  *,
  max_concurrency: int = DEFAULT_AUDIT_CONCURRENCY,
) -> list[WindowAudit]:
  """Run the Engine's strict Tick completeness check for every unique pair."""

  if max_concurrency <= 0:
    raise ValueError("max_concurrency must be positive")
  pairs = sorted(
    {
      (code, trading_date)
      for window in windows
      for code in window.snapshot.instrument_codes
      for trading_date in window.trading_dates
    },
    key=lambda item: (item[1], item[0]),
  )
  if not pairs:
    return [WindowAudit(window=window, inspections={}) for window in windows]
  manager = StrategyManager()
  market_data = HistoricalMarketDataService()
  semaphore = asyncio.Semaphore(max_concurrency)

  async def inspect_pair(
    code: str,
    trading_date: date,
  ) -> tuple[tuple[str, date], TickDayInspection]:
    async with semaphore:
      raw = await asyncio.to_thread(
        manager._inspect_t_trade_replay_tick_day,
        market_data,
        code,
        trading_date,
      )
      # Keep formal acceptance aligned with StrategyManager's strict replay
      # gate: a missing Tick day is usable only when completed, verified,
      # *single-day* Tick and 1d XT_DATA_NO_ROWS transfers jointly prove it
      # empty. Any lookup failure leaves the raw inspection incomplete, so the
      # audit fails closed rather than treating absent ticks as a
      # suspension/holiday.
      if (
        not raw.get("complete")
        and str(raw.get("classification") or "").upper() == "MISSING"
      ):
        try:
          confirmed_empty_dates = await load_completed_empty_tick_days(
            instrument_code=code,
            trading_dates=[trading_date],
          )
        except Exception:
          confirmed_empty_dates = set()
        if trading_date in confirmed_empty_dates:
          raw = {
            **raw,
            "complete": True,
            "classification": "CONFIRMED_EMPTY",
            "reason_codes": [],
            "statistics": {
              **dict(raw.get("statistics") or {}),
              "completed_empty_tick_day": True,
              "completed_empty_daily_day": True,
            },
            "message": "已完成的单日 Tick 与 1d XT_DATA_NO_ROWS 传输共同证明该日为空",
          }
    return (
      (code, trading_date),
      TickDayInspection.from_engine_result(
        instrument_code=code,
        trading_date=trading_date,
        result=raw,
      ),
    )

  inspected = await asyncio.gather(*(inspect_pair(*pair) for pair in pairs))
  inspection_by_pair = dict(inspected)
  return [
    WindowAudit(
      window=window,
      inspections={
        (code, trading_date): inspection_by_pair[(code, trading_date)]
        for code in window.snapshot.instrument_codes
        for trading_date in window.trading_dates
      },
    )
    for window in windows
  ]


async def audit_canonical_tick_coverage(
  windows: Sequence[ReplayWindow],
  *,
  reader: CanonicalTickArchiveReader,
) -> list[WindowAudit]:
  """Audit an explicit archive without constructing an Influx service.

  Only the cutover's exact D-1/all-holdings/20-day scope is inspectable.
  Other candidate snapshots remain visible as fail-closed coverage rows so a
  token cannot be used to promote an arbitrary or partial window.
  """

  result: list[WindowAudit] = []
  scope = reader.cutover.formal_scope
  for window in windows:
    matches_scope = (
      window.snapshot.snapshot_date == scope.snapshot_date
      and tuple(window.snapshot.instrument_codes) == scope.instrument_codes
      and tuple(window.trading_dates) == scope.trading_dates
      and window.requested_trading_days == DEFAULT_TRADING_DAYS
    )
    inspections: dict[tuple[str, date], TickDayInspection] = {}
    for trading_date in window.trading_dates:
      for code in window.snapshot.instrument_codes:
        if not matches_scope:
          raw = {
            "complete": False,
            "classification": "UNAVAILABLE",
            "reason_codes": ["ARCHIVE_FORMAL_SCOPE_MISMATCH"],
            "statistics": {},
            "message": "selected canonical archive token does not cover this exact formal scope",
          }
        else:
          try:
            raw = reader.inspect_tick_day(
              instrument_code=code,
              trading_date=trading_date,
            )
          except CanonicalTickArchiveError as exc:
            raw = {
              "complete": False,
              "classification": "UNAVAILABLE",
              "reason_codes": ["ARCHIVE_OBJECT_VERIFICATION_FAILED"],
              "statistics": {},
              "message": str(exc),
            }
        inspections[(code, trading_date)] = TickDayInspection.from_engine_result(
          instrument_code=code,
          trading_date=trading_date,
          result=raw,
        )
    result.append(WindowAudit(window=window, inspections=inspections))
  return result


def select_formal_window(
  audits: Sequence[WindowAudit],
  *,
  abnormal_dates: Iterable[date] = (),
) -> Optional[WindowAudit]:
  """Choose only a completely covered, all-held-instrument 20-day window."""

  declared_abnormal = set(abnormal_dates)
  eligible = [
    audit
    for audit in audits
    if (
      audit.window.requested_trading_days == DEFAULT_TRADING_DAYS
      and len(audit.window.trading_dates) == DEFAULT_TRADING_DAYS
      and not audit.blockers(abnormal_dates=declared_abnormal)
    )
  ]
  if not eligible:
    return None
  return max(
    eligible,
    key=lambda item: (
      item.window.snapshot.snapshot_date,
      len(item.window.snapshot.holdings),
    ),
  )


def select_pressure_window(
  audits: Sequence[WindowAudit],
  *,
  snapshot_date: date,
) -> tuple[WindowAudit, tuple[date, ...]]:
  """Choose the contiguous shared-complete prefix for one explicit D-1 date."""

  selected = next(
    (
      audit
      for audit in audits
      if audit.window.snapshot.snapshot_date == snapshot_date
    ),
    None,
  )
  if selected is None:
    raise AcceptanceBlockedError("PRESSURE_SNAPSHOT_NOT_FOUND")
  if selected.window.snapshot.non_replayable:
    raise AcceptanceBlockedError("PRESSURE_HELD_INSTRUMENT_NOT_REPLAYABLE")
  prefix = selected.contiguous_shared_prefix_dates
  if not prefix:
    raise AcceptanceBlockedError("PRESSURE_NO_CONTIGUOUS_ALL_HOLDINGS_TICK_PREFIX")
  return selected, prefix


async def audit_source_identity(
  audit: WindowAudit,
  trading_dates: Sequence[date],
  *,
  archive_reader: Optional[CanonicalTickArchiveReader] = None,
) -> SourceIdentityAudit:
  """Fail closed unless every selected day is fully keyset-paginatable.

  The Engine owns the actual global merge.  This preflight proves that its
  input rows have immutable source identities and that no offset/page limit is
  being mistaken for end-of-history.
  """

  market_data = None if archive_reader is not None else HistoricalMarketDataService()
  records_read = 0
  pages_read = 0
  details: dict[str, dict[str, Any]] = {}
  failures: list[dict[str, Any]] = []
  for trading_date in trading_dates:
    for code in audit.window.snapshot.instrument_codes:
      key = f"{trading_date.isoformat()}:{code}"
      day_records = 0
      day_pages = 0
      try:
        start_time = datetime.combine(
          trading_date, clock_time(9, 25), tzinfo=_SHANGHAI
        )
        end_time = datetime.combine(
          trading_date, clock_time(15, 5), tzinfo=_SHANGHAI
        )
        if archive_reader is not None:
          for page in archive_reader.iter_tick_pages(
            instrument_code=code,
            start_time=start_time,
            end_time=end_time,
          ):
            day_pages += 1
            day_records += len(page)
        else:
          assert market_data is not None
          async for page in market_data.iter_tick_pages(
            stock_code=code,
            start_time=start_time,
            end_time=end_time,
          ):
            day_pages += 1
            day_records += len(page)
        details[key] = {
          "passed": True,
          "records": day_records,
          "pages": day_pages,
        }
        records_read += day_records
        pages_read += day_pages
      except (
        CanonicalTickArchiveError,
        HistoricalTickPaginationError,
        ValueError,
        RuntimeError,
      ) as exc:
        evidence = {
          "error_type": type(exc).__name__,
          "message": str(exc),
        }
        details[key] = {
          "passed": False,
          "records": day_records,
          "pages": day_pages,
          "failure": evidence,
        }
        failures.append({"instrument_day": key, **evidence})
  if failures:
    return SourceIdentityAudit(
      passed=False,
      records_read=records_read,
      pages_read=pages_read,
      per_instrument_day=details,
      failure={"failures": failures},
      source=(
        "IMMUTABLE_CANONICAL_TICK_ARCHIVE"
        if archive_reader is not None
        else "HISTORICAL_MARKET_DATA_SERVICE"
      ),
    )
  return SourceIdentityAudit(
    passed=True,
    records_read=records_read,
    pages_read=pages_read,
    per_instrument_day=details,
    source=(
      "IMMUTABLE_CANONICAL_TICK_ARCHIVE"
      if archive_reader is not None
      else "HISTORICAL_MARKET_DATA_SERVICE"
    ),
  )


async def _load_run_evidence(run_id: str) -> dict[str, Any]:
  """Read only run-scoped persisted evidence after an isolated BACKTEST."""

  async for db in get_async_db():
    run = await db.get(StrategyRun, run_id)
    if run is None:
      raise RuntimeError("BACKTEST_RUN_NOT_PERSISTED")
    rows = await db.execute(
      select(
        TTradeOpportunityEvaluation.record_kind,
        func.count(TTradeOpportunityEvaluation.id),
        func.coalesce(func.sum(TTradeOpportunityEvaluation.coalesced_count), 0),
      )
      .where(TTradeOpportunityEvaluation.strategy_run_id == run_id)
      .group_by(TTradeOpportunityEvaluation.record_kind)
    )
    evaluation_by_kind = {
      str(kind): {"rows": int(row_count), "logical_events": int(logical_count)}
      for kind, row_count, logical_count in rows.all()
    }
    diagnostic = evaluation_by_kind.get(
      T_TRADE_EVALUATION_KIND_DIAGNOSTIC,
      {"rows": 0, "logical_events": 0},
    )
    logical_events = int(diagnostic["logical_events"])
    diagnostic_rows = int(diagnostic["rows"])
    profile_rows = await db.execute(
      select(
        TTradeInstrumentProfile.instrument_code,
        TTradeInstrumentProfile.as_of,
        TTradeInstrumentProfile.version,
        TTradeInstrumentProfile.schema_version,
        TTradeInstrumentProfile.fingerprint,
      )
      .where(TTradeInstrumentProfile.instrument_code.in_(list(run.instruments or [])))
      .order_by(
        TTradeInstrumentProfile.instrument_code.asc(),
        TTradeInstrumentProfile.as_of.asc(),
        TTradeInstrumentProfile.id.asc(),
      )
    )
    parameters = _json_object(run.parameters, context="BACKTEST_PARAMETERS")
    return {
      "run_id": run_id,
      "mode": str(getattr(run.mode, "value", run.mode) or ""),
      "status": str(getattr(run.status, "value", run.status) or ""),
      "error_message": _value_as_str(run.error_message) or None,
      "parameters_sha256": _stable_hash(parameters),
      "parameters": parameters,
      "opportunity_policy_version": OPPORTUNITY_POLICY_VERSION,
      "opportunity_feature_schema_version": OPPORTUNITY_FEATURE_SCHEMA_VERSION,
      "evaluations": {
        "by_record_kind": evaluation_by_kind,
        "material_rows": int(
          evaluation_by_kind.get(T_TRADE_EVALUATION_KIND_MATERIAL, {}).get(
            "rows", 0
          )
        ),
        "diagnostic_rows": diagnostic_rows,
        "diagnostic_logical_events": logical_events,
        "diagnostic_merge_ratio": (
          round((logical_events - diagnostic_rows) / logical_events, 8)
          if logical_events
          else None
        ),
      },
      "instrument_profiles": [
        {
          "instrument_code": str(code),
          "as_of": as_of.isoformat() if as_of else None,
          "version": str(version),
          "schema_version": str(schema_version),
          "fingerprint": str(fingerprint),
        }
        for code, as_of, version, schema_version, fingerprint in profile_rows.all()
      ],
    }
  raise RuntimeError("DATABASE_SESSION_UNAVAILABLE")


async def _await_synthetic_replay_terminal(
  service: TTradeReplayService,
  run_id: str,
  *,
  callback_grace_seconds: float = 5.0,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
  """Wait for the manager callback that owns replay terminal projection.

  ``StrategyManager`` publishes run completion from a task done callback. A
  short-lived CLI must keep its loop alive until that callback commits; two
  ``sleep(0)`` turns are not sufficient when the callback is waiting on the
  database. After the bounded grace period, this runner may repair only its
  own proven-completed isolated BACKTEST projection from durable run truth.
  It never marks an active, stopped, PAPER, or LIVE run complete.
  """

  if callback_grace_seconds < 0:
    raise ValueError("CALLBACK_GRACE_SECONDS_MUST_NOT_BE_NEGATIVE")
  deadline = wall_clock.perf_counter() + callback_grace_seconds
  repaired = False
  last_run_status = ""
  last_replay_status = ""
  while True:
    replay = await service.get(run_id)
    run_evidence = await _load_run_evidence(run_id)
    last_run_status = str(run_evidence.get("status") or "").upper()
    last_replay_status = str((replay or {}).get("status") or "").upper()
    if last_replay_status in {"COMPLETED", "CANCELLED", "FAILED", "ERROR"}:
      return (
        dict(replay or {}),
        run_evidence,
        {
          "status": "TERMINAL",
          "projection_repaired": repaired,
          "run_status": last_run_status,
          "replay_status": last_replay_status,
        },
      )
    if wall_clock.perf_counter() >= deadline:
      # The callback normally performs this update. Repair is restricted to a
      # durable completed V3 synthetic BACKTEST so a CLI shutdown can never
      # strand its own completed replay as an active account-level replay.
      parameters = _json_object(
        run_evidence.get("parameters"),
        context="SYNTHETIC_TERMINAL_PARAMETERS",
      )
      account_id = _value_as_str(parameters.get("account_id"))
      if (
        last_run_status == "COMPLETED"
        and last_replay_status == "RUNNING"
        and str(run_evidence.get("mode") or "").upper() == "BACKTEST"
        and bool(parameters.get("t_trade_replay"))
        and parameters.get("replay_acceptance") == "V3_PRESSURE_BASELINE"
        and account_id
      ):
        raw_end = parameters.get("replay_end_time")
        try:
          processed_until = datetime.fromisoformat(str(raw_end))
        except (TypeError, ValueError) as exc:
          raise RuntimeError(
            "SYNTHETIC_COMPLETED_REPLAY_END_TIME_INVALID"
          ) from exc
        await t_trade_replay_projection_service.update(
          run_id=run_id,
          account_id=account_id,
          status="COMPLETED",
          progress_pct=100.0,
          processed_until=processed_until,
          kind=TTradeReplayUpdateKind.RESULT_READY,
        )
        repaired = True
        continue
      raise RuntimeError(
        "SYNTHETIC_PRESSURE_TERMINAL_PROJECTION_NOT_CONVERGED: "
        f"run={last_run_status or '-'} replay={last_replay_status or '-'}"
      )
    await asyncio.sleep(0.05)


async def load_cancelled_full_pressure_attempt(
  run_id: str,
  *,
  cancellation_reason: str,
  fixture: SyntheticPressureFixture,
) -> dict[str, Any]:
  """Read immutable evidence for the intentionally stopped full-load attempt."""

  async for db in get_async_db():
    run = await db.get(StrategyRun, run_id)
    if run is None:
      raise AcceptanceBlockedError("CANCELLED_PRESSURE_RUN_NOT_FOUND")
    parameters = _json_object(run.parameters, context="CANCELLED_BACKTEST_PARAMETERS")
    if str(parameters.get("replay_acceptance") or "") != "V3_PRESSURE_BASELINE":
      raise AcceptanceBlockedError("CANCELLED_RUN_IS_NOT_PRESSURE_BASELINE")
    runtime_state_persisted = bool(
      parameters.get(_INTERNAL_V3_PRESSURE_RUNTIME_STATE_PERSISTENCE_KEY)
    )
    backtest = (
      await db.execute(
        select(StrategyBacktest)
        .where(StrategyBacktest.strategy_run_id == run_id)
        .order_by(StrategyBacktest.version.desc())
        .limit(1)
      )
    ).scalar_one_or_none()
    evaluation_rows = await db.execute(
      select(
        TTradeOpportunityEvaluation.record_kind,
        func.count(TTradeOpportunityEvaluation.id),
        func.coalesce(func.sum(TTradeOpportunityEvaluation.coalesced_count), 0),
      )
      .where(TTradeOpportunityEvaluation.strategy_run_id == run_id)
      .group_by(TTradeOpportunityEvaluation.record_kind)
    )
    evaluations = {
      str(kind): {"rows": int(row_count), "logical_events": int(logical_count)}
      for kind, row_count, logical_count in evaluation_rows.all()
    }
    replay = await TTradeReplayService().get(run_id)
    run_status = str(getattr(run.status, "value", run.status) or "").upper()
    replay_status = str((replay or {}).get("status") or "").upper()
    if run_status != "STOPPED" or replay_status != "CANCELLED":
      raise AcceptanceBlockedError("CANCELLED_PRESSURE_RUN_NOT_TERMINAL")
    actual_start = getattr(backtest, "start_time", None) or run.created_at
    actual_end = getattr(backtest, "end_time", None) or run.updated_at
    elapsed_seconds = (
      round((actual_end - actual_start).total_seconds(), 6)
      if isinstance(actual_start, datetime) and isinstance(actual_end, datetime)
      else None
    )
    logical_events = sum(item["logical_events"] for item in evaluations.values())
    return {
      "status": "CANCELLED_BLOCKED_FULL_SYNTHETIC_PRESSURE",
      "not_a_completed_slo": True,
      "fixture": fixture.to_dict(),
      "run_id": run_id,
      "run_sha256_16": _digest(run_id),
      "cancellation_reason": cancellation_reason,
      "requested_replay_start": parameters.get("replay_start_time"),
      "requested_replay_end": parameters.get("replay_end_time"),
      "actual_started_at": actual_start.isoformat() if actual_start else None,
      "actual_ended_at": actual_end.isoformat() if actual_end else None,
      "wall_elapsed_seconds": elapsed_seconds,
      "processed_until": (
        replay.get("processed_until").isoformat()
        if replay and replay.get("processed_until")
        else None
      ),
      "progress_pct": replay.get("progress_pct") if replay else None,
      "run_status": run_status,
      "replay_status": replay_status,
      "runtime_state_persistence": {
        "enabled": runtime_state_persisted,
        "evidence": (
          "sealed V3 pressure runtime-state marker present in durable run parameters"
          if runtime_state_persisted
          else "sealed V3 pressure runtime-state marker absent; this historical run did not exercise durable RuntimeState CAS/position writes"
        ),
      },
      "evaluations": evaluations,
      "partial_materialization_logical_events_per_second": (
        round(logical_events / elapsed_seconds, 6)
        if elapsed_seconds and elapsed_seconds > 0
        else None
      ),
      "unavailable_metrics": {
        "engine_tick_latency_p50_p95_p99": "N/A: process cancellation releases in-memory samples",
        "engine_tick_throughput": "N/A: completed Tick count was not durably checkpointed",
        "cas_conflict_rate": "N/A: in-memory counter lost at cancellation",
        "database_commit_calls": "N/A: in-process counter lost at cancellation",
      },
      "primary_observed_boundary": (
        "production evaluation/materialization path with a nonpersistent BACKTEST "
        "runtime-state checkpoint; this cancelled historical run did not exercise "
        "durable RuntimeState CAS/position writes and made only partial progress "
        "within the allowed wall-time budget"
      ),
    }
  raise RuntimeError("DATABASE_SESSION_UNAVAILABLE")


async def load_completed_diagnostic_pressure_attempt(
  run_id: str,
  *,
  fixture: SyntheticPressureFixture,
) -> dict[str, Any]:
  """Recover durable evidence for a completed, non-gating diagnostic run.

  Latency and process-local DB counters are intentionally not reconstructed:
  they were never persisted by the first runner process.  This makes a stale
  diagnostic useful for auditability without upgrading it into a benchmark.
  """

  run_evidence = await _load_run_evidence(run_id)
  parameters = _json_object(
    run_evidence.get("parameters"), context="DIAGNOSTIC_BACKTEST_PARAMETERS"
  )
  expected_start = datetime.combine(fixture.trading_dates[0], clock_time(9, 30))
  expected_end = datetime.combine(fixture.trading_dates[-1], clock_time(15, 0))
  expected_codes = tuple(str(item).upper() for item in fixture.instrument_codes)
  if (
    parameters.get("replay_acceptance") != "V3_PRESSURE_BASELINE"
    or parameters.get("replay_start_time") != expected_start.isoformat()
    or parameters.get("replay_end_time") != expected_end.isoformat()
  ):
    raise AcceptanceBlockedError("DIAGNOSTIC_RUN_PARAMETERS_DO_NOT_MATCH_FIXTURE")

  replay = await TTradeReplayService().get(run_id)
  replay_status = str((replay or {}).get("status") or "").upper()
  if (
    str(run_evidence.get("mode") or "").upper() != "BACKTEST"
    or str(run_evidence.get("status") or "").upper() != "COMPLETED"
    or replay_status != "COMPLETED"
  ):
    raise AcceptanceBlockedError("DIAGNOSTIC_RUN_NOT_COMPLETED_BACKTEST")
  persisted_codes = tuple(
    str(item.get("stock_code") if isinstance(item, Mapping) else item).upper()
    for item in list((replay or {}).get("instruments") or [])
  )
  if persisted_codes and persisted_codes != expected_codes:
    raise AcceptanceBlockedError("DIAGNOSTIC_RUN_HOLDINGS_DO_NOT_MATCH_FIXTURE")

  evaluations = dict(run_evidence.get("evaluations") or {})
  material_rows = int(evaluations.get("material_rows") or 0)
  diagnostic_rows = int(evaluations.get("diagnostic_rows") or 0)
  result = {
    "schema_version": PRESSURE_BASELINE_SCHEMA_VERSION,
    "status": "EXECUTED_DIAGNOSTIC_NON_GATING_VERSION_STALE",
    "non_gating": True,
    "diagnostic_non_gating": True,
    "not_historical_replay": True,
    "not_a_completed_slo": True,
    "isolated_backtest": True,
    "no_live_or_paper_broker": True,
    "fixture": fixture.to_dict(),
    "run_id": run_id,
    "run_sha256_16": _digest(run_id),
    "replay": replay,
    "run_evidence": run_evidence,
    "runtime_state_persistence": {
      "enabled": bool(
        parameters.get(_INTERNAL_V3_PRESSURE_RUNTIME_STATE_PERSISTENCE_KEY)
      ),
      "evidence": (
        "sealed V3 pressure runtime-state marker present in durable run parameters"
        if parameters.get(_INTERNAL_V3_PRESSURE_RUNTIME_STATE_PERSISTENCE_KEY)
        else "sealed V3 pressure runtime-state marker absent from durable run parameters"
      ),
    },
    "version_stale_reason": (
      "the diagnostic runner imported the pre-batch-diagnostics implementation; "
      "it completed before its process-local instrumentation could be persisted"
    ),
    "latency": {},
    "throughput": {
      "engine_ticks_processed": None,
      "engine_ticks_per_second": None,
      "unavailable_reason": "N/A: completed Tick count/timing were process-local",
    },
    "cas": {
      "snapshot_conflicts": None,
      "checkpoint_attempts": None,
      "conflict_rate": None,
      "unavailable_reason": "N/A: CAS counters were process-local",
    },
    "database_write_activity": {
      "status": "N/A: commit/flush counters were process-local",
    },
    "resources": {
      "status": "N/A: runner resource samples were process-local",
    },
    "production_path_coverage": {
      "strategy_executor_global_source_order": (
        "VERSION_STALE_NOT_REMEASURED: execution path was not re-instrumented"
      ),
      "strategy_evaluator": material_rows + diagnostic_rows > 0,
      "runtime_state_checkpoint": "N/A: not durably measured",
      "post_cas_evaluation_materialization": material_rows + diagnostic_rows > 0,
    },
    "frozen_local_slo": None,
  }
  return _normalize_nonpersistent_diagnostic_pressure_attempt(result)


def _normalize_nonpersistent_diagnostic_pressure_attempt(
  pressure_baseline: Optional[Mapping[str, Any]],
) -> Optional[dict[str, Any]]:
  """Label a completed pre-capability diagnostic as explicitly non-gating.

  Earlier short runs had ``checkpoint`` call timings, but normal BACKTEST
  intentionally had ``persist_enabled=False``.  Their latency is retained for
  diagnosis only; the absence of the sealed durable marker prevents them from
  being misread as CAS/position-path coverage.
  """

  if not isinstance(pressure_baseline, Mapping):
    return None
  normalized = copy.deepcopy(dict(pressure_baseline))
  if not bool(normalized.get("diagnostic_non_gating")):
    return normalized
  run_evidence = dict(normalized.get("run_evidence") or {})
  parameters = _json_object(
    run_evidence.get("parameters"),
    context="DIAGNOSTIC_PRESSURE_PARAMETERS",
  )
  if parameters.get(_INTERNAL_V3_PRESSURE_RUNTIME_STATE_PERSISTENCE_KEY):
    return normalized
  normalized["status"] = "EXECUTED_DIAGNOSTIC_NON_GATING_NONPERSISTENT"
  normalized["non_gating"] = True
  normalized["diagnostic_non_gating"] = True
  normalized["not_a_completed_slo"] = True
  normalized["runtime_state_persistence"] = {
    "enabled": False,
    "evidence": (
      "sealed V3 pressure runtime-state marker absent from durable run "
      "parameters; ordinary BACKTEST persist_enabled=False"
    ),
  }
  coverage = dict(normalized.get("production_path_coverage") or {})
  coverage["runtime_state_checkpoint"] = "NOT_PERSISTENT_NON_GATING"
  coverage["runtime_state_cas_position"] = "NOT_PERSISTENT_NON_GATING"
  normalized["production_path_coverage"] = coverage
  return normalized


def _is_nonpersistent_diagnostic_pressure_attempt(
  pressure_baseline: Optional[Mapping[str, Any]],
) -> bool:
  """Return whether a retained diagnostic is truly the old non-durable path.

  The report keeps this historical calibration separately so its timings cannot
  be mistaken for the sealed CAS/position workload.  Do not infer the label
  from the field name alone: an earlier report refresh could have left a later
  sealed run in that slot.
  """

  if not isinstance(pressure_baseline, Mapping):
    return False
  if not bool(pressure_baseline.get("diagnostic_non_gating")):
    return False
  run_evidence = dict(pressure_baseline.get("run_evidence") or {})
  parameters = _json_object(
    run_evidence.get("parameters"),
    context="RETAINED_DIAGNOSTIC_PRESSURE_PARAMETERS",
  )
  return not bool(
    parameters.get(_INTERNAL_V3_PRESSURE_RUNTIME_STATE_PERSISTENCE_KEY)
  )


def _resource_start() -> dict[str, Any]:
  """Return platform/process information without requiring an optional package."""

  evidence: dict[str, Any] = {
    "platform": platform.platform(),
    "python": sys.version.split()[0],
    "cpu_count": os.cpu_count(),
  }
  try:
    import psutil

    process = psutil.Process()
    evidence["psutil_available"] = True
    evidence["rss_bytes_start"] = int(process.memory_info().rss)
    evidence["cpu_seconds_start"] = round(sum(process.cpu_times()[:2]), 6)
  except (ImportError, OSError):
    evidence["psutil_available"] = False
  return evidence


def _resource_finish(evidence: Mapping[str, Any]) -> dict[str, Any]:
  result = dict(evidence)
  if not result.get("psutil_available"):
    return result
  try:
    import psutil

    process = psutil.Process()
    rss_end = int(process.memory_info().rss)
    cpu_end = round(sum(process.cpu_times()[:2]), 6)
    result["rss_bytes_end"] = rss_end
    result["rss_bytes_delta"] = rss_end - int(result["rss_bytes_start"])
    result["cpu_seconds_end"] = cpu_end
    result["cpu_seconds_delta"] = round(
      cpu_end - float(result["cpu_seconds_start"]), 6
    )
  except OSError as exc:
    result["resource_read_error"] = type(exc).__name__
  return result


async def execute_isolated_backtest(
  audit: WindowAudit,
  trading_dates: Sequence[date],
  *,
  formal_gate: bool,
  abnormal_dates: Iterable[date] = (),
  archive_reader: Optional[CanonicalTickArchiveReader] = None,
  archive_root: Optional[Path] = None,
) -> dict[str, Any]:
  """Run one preflighted isolated BACKTEST and capture a local SLO baseline.

  ``formal_gate`` identifies report semantics only.  Both paths are isolated
  BACKTESTs; callers may use ``formal_gate=False`` solely for an explicitly
  marked short pressure baseline.
  """

  if not trading_dates:
    raise AcceptanceBlockedError("BACKTEST_NO_TRADING_DATES")
  full_dates = set(audit.full_shared_dates)
  if any(item not in full_dates for item in trading_dates):
    raise AcceptanceBlockedError("BACKTEST_TICK_COVERAGE_NOT_COMPLETE")
  if audit.window.snapshot.non_replayable:
    raise AcceptanceBlockedError("BACKTEST_HELD_INSTRUMENT_NOT_REPLAYABLE")

  if archive_reader is not None:
    if not formal_gate or archive_root is None:
      raise AcceptanceBlockedError("CANONICAL_ARCHIVE_FORMAL_EXECUTION_ONLY")
    try:
      archive_reader.validate_formal_scope(
        snapshot_date=audit.window.snapshot.snapshot_date,
        instrument_codes=audit.window.snapshot.instrument_codes,
        trading_dates=trading_dates,
      )
    except CanonicalTickArchiveError as exc:
      raise AcceptanceBlockedError("CANONICAL_ARCHIVE_FORMAL_SCOPE_MISMATCH") from exc
  identity = await audit_source_identity(
    audit,
    trading_dates,
    archive_reader=archive_reader,
  )
  if not identity.passed:
    raise AcceptanceBlockedError("BACKTEST_SOURCE_IDENTITY_NOT_PROVEN")

  manager = StrategyManager()
  original_supplement = manager._queue_missing_backtest_data_supplement
  original_sync = manager._sync_missing_backtest_data
  supplement_attempts = 0

  async def forbid_supplement(*_: Any, **__: Any) -> dict[str, Any]:
    nonlocal supplement_attempts
    supplement_attempts += 1
    raise RuntimeError("V3_ACCEPTANCE_FORBIDS_MARKET_DATA_SUPPLEMENT")

  # A correct preflight never enters this function.  The interceptor provides
  # defense in depth: this offline runner must not command a QMT Agent even if
  # storage changes between preflight and the Engine's own data check.
  manager._queue_missing_backtest_data_supplement = forbid_supplement
  manager._sync_missing_backtest_data = forbid_supplement
  request_id = str(uuid.uuid4())
  start_time = datetime.combine(trading_dates[0], clock_time(9, 30))
  end_time = datetime.combine(trading_dates[-1], clock_time(15, 0))
  payload = {
    "account_id": audit.window.snapshot.account_id,
    "start_time": start_time,
    "end_time": end_time,
    "replay_acceptance": "V3_CAUSAL_20D" if formal_gate else "V3_PRESSURE_BASELINE",
    "replay_abnormal_dates": [
      item.isoformat() for item in sorted(set(abnormal_dates)) if item in trading_dates
    ],
  }
  archive_evidence: Optional[dict[str, Any]] = None
  if archive_reader is not None:
    scope = archive_reader.cutover.formal_scope
    archive_evidence = {
      "schema_version": 1,
      "archive_root": str(archive_root.absolute()),
      "cutover_token": archive_reader.cutover.token,
      "manifest_fingerprint": archive_reader.cutover.manifest_fingerprint,
      "source_manifest_sha256": archive_reader.source_manifest_sha256,
      "formal_scope_fingerprint": scope.scope_fingerprint,
      "snapshot_date": scope.snapshot_date.isoformat(),
      "instrument_codes": list(scope.instrument_codes),
      "trading_dates": [item.isoformat() for item in scope.trading_dates],
    }
    payload["canonical_tick_archive"] = archive_evidence
  resource_start = _resource_start()
  started = wall_clock.perf_counter()
  runtime_state: Any = None
  try:
    async with BenchmarkInstrumentation() as instrumentation:
      service = TTradeReplayService(manager)
      created = await service.start(payload, request_id=request_id)
      run_id = str(created["run_id"])
      runtime = manager.get_run(run_id)
      if runtime is None or runtime.task is None:
        raise RuntimeError("BACKTEST_RUNTIME_TASK_NOT_CREATED")
      try:
        await runtime.task
      except asyncio.CancelledError:
        raise
      except Exception:
        # Engine terminal status and persisted replay projection are the
        # authoritative detailed evidence; collect them below before failing.
        pass
      await asyncio.sleep(0)
      await asyncio.sleep(0)
      runtime_state = runtime.state_manager
      replay = await service.get(run_id)
      run_evidence = await _load_run_evidence(run_id)
    elapsed_seconds = wall_clock.perf_counter() - started
  finally:
    manager._queue_missing_backtest_data_supplement = original_supplement
    manager._sync_missing_backtest_data = original_sync

  cas_conflicts = int(
    getattr(runtime_state, "snapshot_cas_conflicts", 0) or 0
  )
  checkpoint = instrumentation.state_checkpoint.to_dict()
  checkpoint_attempts = int(checkpoint["sample_count"])
  cas_denominator = checkpoint_attempts + cas_conflicts
  return {
    "schema_version": PRESSURE_BASELINE_SCHEMA_VERSION,
    "status": (
      "FORMAL_BACKTEST_EXECUTED" if formal_gate else "EXECUTED_NON_GATING"
    ),
    "isolated_backtest": True,
    "no_live_or_paper_broker": True,
    "market_data_supplement_forbidden": True,
    "market_data_supplement_attempts": supplement_attempts,
    "snapshot_date": audit.window.snapshot.snapshot_date.isoformat(),
    "trading_dates": [item.isoformat() for item in trading_dates],
    "held_instruments": list(audit.window.snapshot.instrument_codes),
    "source_identity": identity.to_dict(),
    "canonical_tick_archive": (
      {
        "token": archive_reader.cutover.token,
        "manifest_fingerprint": archive_reader.cutover.manifest_fingerprint,
        "source_manifest_sha256": archive_reader.source_manifest_sha256,
        "formal_scope_fingerprint": archive_reader.cutover.formal_scope.scope_fingerprint,
        "execution_source": "CANONICAL_ARCHIVE_ONLY_NO_INFLUX_OR_QMT_FALLBACK",
      }
      if archive_reader is not None
      else None
    ),
    "elapsed_seconds": round(elapsed_seconds, 6),
    "throughput": {
      "engine_ticks_processed": int(instrumentation.engine_tick.to_dict()["sample_count"]),
      "engine_ticks_per_second": (
        round(
          int(instrumentation.engine_tick.to_dict()["sample_count"]) / elapsed_seconds,
          6,
        )
        if elapsed_seconds > 0
        else None
      ),
    },
    "latency": {
      "engine_tick": instrumentation.engine_tick.to_dict(),
      "strategy_evaluation": instrumentation.strategy_evaluation.to_dict(),
      "state_checkpoint": checkpoint,
    },
    "cas": {
      "snapshot_conflicts": cas_conflicts,
      "checkpoint_attempts": checkpoint_attempts,
      "conflict_rate": (
        round(cas_conflicts / cas_denominator, 8) if cas_denominator else None
      ),
    },
    "database_write_activity": instrumentation.db_writes.to_dict(),
    "resources": _resource_finish(resource_start),
    "replay": replay,
    "run_evidence": run_evidence,
  }


def _freeze_first_local_slo(
  *,
  latency: Mapping[str, Any],
  throughput: Mapping[str, Any],
  cas: Mapping[str, Any],
  database_write_activity: Mapping[str, Any],
) -> dict[str, Any]:
  """Freeze transparent first-run guardrails without calling them a release gate."""

  def latency_limit(name: str) -> Optional[float]:
    value = dict(latency.get("engine_tick") or {}).get(name)
    if value is None:
      return None
    return round(float(value) * 1.5, 6)

  ticks_per_second = throughput.get("engine_ticks_per_second")
  throughput_floor = (
    round(float(ticks_per_second) * 0.8, 6)
    if ticks_per_second is not None
    else None
  )
  observed_cas_rate = cas.get("conflict_rate")
  return {
    "status": "FROZEN_FIRST_LOCAL_SYNTHETIC_BASELINE",
    "not_a_formal_replay_gate": True,
    "policy": (
      "first local synthetic baseline; latency upper bounds = observed × 1.5; "
      "throughput floor = observed × 0.8; values require re-baselining on "
      "hardware, runtime, or workload change"
    ),
    "limits": {
      "engine_tick_p50_ms_max": latency_limit("p50"),
      "engine_tick_p95_ms_max": latency_limit("p95"),
      "engine_tick_p99_ms_max": latency_limit("p99"),
      "engine_ticks_per_second_min": throughput_floor,
      "cas_conflict_rate_max": (
        max(float(observed_cas_rate), 0.001)
        if observed_cas_rate is not None
        else None
      ),
      "database_commit_calls_max": (
        int(database_write_activity.get("commit_calls") or 0) * 2
        if database_write_activity
        else None
      ),
    },
  }


async def execute_synthetic_pressure_baseline(
  audit: WindowAudit,
  trading_dates: Sequence[date],
  *,
  ticks_per_instrument_day: int = 600,
  diagnostic: bool = False,
  timeout_seconds: float = DEFAULT_PRESSURE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
  """Exercise the production Engine with a deterministic non-historical load.

  The real D-1 account snapshot supplies the all-holdings universe and initial
  position state.  A temporary in-process loader replaces only historical Tick
  reads; strategy evaluation, post-CAS state checkpointing, and evaluation
  materialization remain the production code paths.
  """

  if timeout_seconds <= 0:
    raise ValueError("PRESSURE_TIMEOUT_SECONDS_MUST_BE_POSITIVE")

  fixture = build_synthetic_pressure_fixture(
    audit,
    trading_dates,
    ticks_per_instrument_day=ticks_per_instrument_day,
  )
  manager = StrategyManager()
  original_data_check = manager._ensure_backtest_data_available
  original_loader = StrategyExecutor._load_backtest_ticks
  original_supplement = manager._queue_missing_backtest_data_supplement
  supplement_attempts = 0

  async def synthetic_data_check(
    _: Any,
    *,
    canonical_archive_adapter: Any = None,
  ) -> None:
    # Historical data is intentionally not consulted for this declared
    # synthetic benchmark.  The loader below is the sole Tick source.  This
    # isolated pressure path also must never inherit the formal archive
    # adapter/fallback selection used by V3 causal acceptance.
    if canonical_archive_adapter is not None:
      raise RuntimeError("V3_SYNTHETIC_PRESSURE_FORBIDS_CANONICAL_ARCHIVE")
    return None

  async def synthetic_loader(
    _: Any,
    __: Any,
    ___: Any,
    *,
    instrument_code: str,
    start_time: datetime,
    end_time: datetime,
  ) -> list[Tick]:
    selected: list[Tick] = []
    for tick in fixture.ticks_by_instrument.get(instrument_code, ()):
      tick_time = tick.time.replace(tzinfo=None) if tick.time.tzinfo else tick.time
      if start_time <= tick_time <= end_time:
        selected.append(tick)
    return selected

  async def forbid_supplement(*_: Any, **__: Any) -> dict[str, Any]:
    nonlocal supplement_attempts
    supplement_attempts += 1
    raise RuntimeError("V3_SYNTHETIC_PRESSURE_FORBIDS_MARKET_DATA_SUPPLEMENT")

  manager._ensure_backtest_data_available = synthetic_data_check
  manager._queue_missing_backtest_data_supplement = forbid_supplement
  StrategyExecutor._load_backtest_ticks = synthetic_loader
  request_id = str(uuid.uuid4())
  start_time = datetime.combine(trading_dates[0], clock_time(9, 30))
  end_time = datetime.combine(trading_dates[-1], clock_time(15, 0))
  payload = {
    "account_id": audit.window.snapshot.account_id,
    "start_time": start_time,
    "end_time": end_time,
    "replay_acceptance": "V3_PRESSURE_BASELINE",
  }
  resource_start = _resource_start()
  started = wall_clock.perf_counter()
  runtime_state: Any = None
  run_id = ""
  timed_out = False
  cancellation: Optional[dict[str, Any]] = None
  terminal_convergence: Optional[dict[str, Any]] = None
  execution_boundary: Optional[dict[str, Any]] = None
  try:
    async with BenchmarkInstrumentation() as instrumentation:
      service = TTradeReplayService(manager)
      created = await service.start(
        payload,
        request_id=request_id,
        _runtime_state_persistence_capability=(
          _v3_pressure_runtime_state_persistence_capability()
        ),
      )
      run_id = str(created["run_id"])
      runtime = manager.get_run(run_id)
      if runtime is None or runtime.task is None:
        raise RuntimeError("SYNTHETIC_PRESSURE_RUNTIME_TASK_NOT_CREATED")
      runtime_state = runtime.state_manager
      runtime_mode = str(
        getattr(getattr(runtime, "context", None), "mode", "")
      ).upper()
      if runtime_mode.endswith(".BACKTEST"):
        runtime_mode = "BACKTEST"
      broker_class = type(getattr(runtime, "broker", None)).__name__
      if (
        runtime_mode != "BACKTEST"
        or broker_class != "BacktestBroker"
        or runtime_state is None
        or not bool(getattr(runtime_state, "persist_enabled", False))
      ):
        # The task has already been created, so converge it through the
        # service-owned isolated BACKTEST cancellation boundary before raising.
        await service.cancel(run_id)
        raise RuntimeError("SYNTHETIC_PRESSURE_EXECUTION_BOUNDARY_INVALID")
      execution_boundary = {
        "strategy_run_mode": runtime_mode,
        "broker_class": broker_class,
        "runtime_state_persist_enabled": True,
        "runtime_state_capability": "V3_PRESSURE_BASELINE_INTERNAL_ONLY",
        "qmt_invocation": False,
        "paper_or_live_command": False,
      }
      try:
        # Shield the task first: on deadline we must use the replay service's
        # isolated BACKTEST cancellation path, which flushes durable evidence
        # and marks the run terminal.  Letting wait_for cancel it directly
        # would bypass that safety boundary and could strand an active replay.
        await asyncio.wait_for(
          asyncio.shield(runtime.task),
          timeout=float(timeout_seconds),
        )
      except asyncio.TimeoutError:
        timed_out = True
        try:
          cancelled = await service.cancel(run_id)
          cancellation = {
            "status": str(cancelled.get("status") or ""),
            "progress_pct": cancelled.get("progress_pct"),
            "processed_until": (
              cancelled.get("processed_until").isoformat()
              if isinstance(cancelled.get("processed_until"), datetime)
              else cancelled.get("processed_until")
            ),
          }
        except Exception as exc:
          # A race with normal terminal completion is acceptable only when the
          # authoritative projection already says terminal.  Anything still
          # active remains an explicit failure rather than an orphaned run.
          latest = await service.get(run_id)
          latest_status = str((latest or {}).get("status") or "").upper()
          if latest_status not in {"COMPLETED", "CANCELLED", "FAILED", "ERROR"}:
            raise RuntimeError("PRESSURE_TIMEOUT_CANCELLATION_NOT_CONVERGED") from exc
          cancellation = {
            "status": latest_status,
            "raced_terminal_state": True,
            "cancel_error_type": type(exc).__name__,
          }
        try:
          await asyncio.wait_for(asyncio.shield(runtime.task), timeout=10.0)
        except asyncio.CancelledError:
          # The service-owned cancellation is the expected terminal result.
          pass
        except asyncio.TimeoutError as exc:
          raise RuntimeError("PRESSURE_TIMEOUT_RUNTIME_TASK_NOT_CONVERGED") from exc
      except asyncio.CancelledError:
        raise
      except Exception:
        # Preserve the terminal result and run-scoped DB evidence below.
        pass
      replay, run_evidence, terminal_convergence = (
        await _await_synthetic_replay_terminal(service, run_id)
      )
    elapsed_seconds = wall_clock.perf_counter() - started
  finally:
    manager._ensure_backtest_data_available = original_data_check
    manager._queue_missing_backtest_data_supplement = original_supplement
    StrategyExecutor._load_backtest_ticks = original_loader

  engine_tick_latency = instrumentation.engine_tick.to_dict()
  checkpoint_latency = instrumentation.state_checkpoint.to_dict()
  ticks_processed = int(engine_tick_latency["sample_count"])
  cas_conflicts = int(
    getattr(runtime_state, "snapshot_cas_conflicts", 0) or 0
  )
  checkpoint_attempts = int(checkpoint_latency["sample_count"])
  cas_denominator = checkpoint_attempts + cas_conflicts
  throughput = {
    "engine_ticks_processed": ticks_processed,
    "engine_ticks_per_second": (
      round(ticks_processed / elapsed_seconds, 6) if elapsed_seconds > 0 else None
    ),
  }
  tick_accounting = _pressure_tick_accounting(
    {"fixture": fixture.to_dict(), "throughput": throughput}
  )
  cas = {
    "snapshot_conflicts": cas_conflicts,
    "checkpoint_attempts": checkpoint_attempts,
    "state_upsert_attempts": instrumentation.runtime_state_db.state_upsert_attempts,
    "state_upsert_rejected": instrumentation.runtime_state_db.state_upsert_rejected,
    "conflict_rate": (
      round(cas_conflicts / cas_denominator, 8) if cas_denominator else None
    ),
  }
  database_write_activity = {
    **instrumentation.db_writes.to_dict(),
    "runtime_state": instrumentation.runtime_state_db.to_dict(),
  }
  persisted_parameters = _json_object(
    run_evidence.get("parameters"), context="SYNTHETIC_PRESSURE_PARAMETERS"
  )
  if (
    persisted_parameters.get("replay_acceptance") != "V3_PRESSURE_BASELINE"
    or not persisted_parameters.get("t_trade_replay")
    or not persisted_parameters.get(
      _INTERNAL_V3_PRESSURE_RUNTIME_STATE_PERSISTENCE_KEY
    )
  ):
    raise RuntimeError("SYNTHETIC_PRESSURE_DURABLE_RUNTIME_STATE_NOT_PROVEN")
  latency = {
    "engine_tick": engine_tick_latency,
    "strategy_evaluation": instrumentation.strategy_evaluation.to_dict(),
    "state_checkpoint": checkpoint_latency,
    "state_snapshot": instrumentation.state_snapshot.to_dict(),
  }
  completed = str(replay.get("status") or "").upper() == "COMPLETED"
  # A short calibration is useful to predict the full-run duration, but it is
  # not the fixed 8 holdings × 2 days × 600 Tick acceptance fixture. Keep its
  # telemetry without allowing it to manufacture a local SLO baseline.
  completed_full_slo_fixture = (
    completed
    and not diagnostic
    and int(fixture.tick_count) == 9_600
    and bool(
      dict(execution_boundary or {}).get("runtime_state_persist_enabled")
    )
    and bool(tick_accounting.get("accounting_passed"))
  )
  if timed_out:
    status = (
      "CANCELLED_BLOCKED_FULL_SYNTHETIC_PRESSURE"
      if str(replay.get("status") or "").upper() == "CANCELLED"
      else "FAIL"
    )
  elif completed:
    status = (
      "EXECUTED_DIAGNOSTIC_NON_GATING"
      if diagnostic
      else "EXECUTED_SYNTHETIC_NON_HISTORICAL"
    )
  else:
    status = "FAIL"
  return {
    "schema_version": PRESSURE_BASELINE_SCHEMA_VERSION,
    "status": status,
    "non_gating": True,
    "diagnostic_non_gating": diagnostic,
    "not_historical_replay": True,
    "not_a_completed_slo": not completed_full_slo_fixture,
    "isolated_backtest": True,
    "no_live_or_paper_broker": True,
    "execution_boundary": execution_boundary,
    "production_path_coverage": {
      "strategy_executor_global_source_order": True,
      "strategy_evaluator": ticks_processed > 0,
      "runtime_state_checkpoint": bool(
        execution_boundary
        and execution_boundary["runtime_state_persist_enabled"]
        and checkpoint_attempts > 0
      ),
      "post_cas_evaluation_materialization": bool(
        run_evidence["evaluations"]["material_rows"]
        or run_evidence["evaluations"]["diagnostic_rows"]
      ),
    },
    "fixture": fixture.to_dict(),
    "market_data_supplement_forbidden": True,
    "market_data_supplement_attempts": supplement_attempts,
    "timeout_seconds": float(timeout_seconds),
    "timed_out": timed_out,
    "cancellation": cancellation,
    "terminal_convergence": terminal_convergence,
    "elapsed_seconds": round(elapsed_seconds, 6),
    "throughput": throughput,
    "tick_accounting": tick_accounting,
    "latency": latency,
    "cas": cas,
    "database_write_activity": database_write_activity,
    "resources": _resource_finish(resource_start),
    "replay": replay,
    "run_evidence": run_evidence,
    "frozen_local_slo": (
      None
      if not completed_full_slo_fixture
      else _freeze_first_local_slo(
        latency=latency,
        throughput=throughput,
        cas=cas,
        database_write_activity=database_write_activity,
      )
    ),
  }


def _short_window_coverage_diagnostic(
  audits: Sequence[WindowAudit],
  *,
  requested_trading_days: int,
) -> dict[str, Any]:
  """Expose short-window coverage without borrowing formal-gate semantics."""

  coverage_windows: list[dict[str, Any]] = []
  for audit in audits:
    coverage_window = audit.to_dict()
    coverage_window.pop("formal_gate_blockers", None)
    coverage_windows.append(coverage_window)
  return {
    "status": "COVERAGE_DIAGNOSTIC_NON_GATING",
    "requested_trading_days": requested_trading_days,
    "candidate_count": len(coverage_windows),
    "complete_candidate_count": sum(
      audit.coverage_complete for audit in audits
    ),
    "message": "仅展示覆盖完整性；不产生回放、审批或上线结论。",
    "coverage_windows": coverage_windows,
  }


def build_report_document(
  audits: Sequence[WindowAudit],
  *,
  requested_trading_days: int,
  abnormal_dates: Iterable[date] = (),
  formal_execution: Optional[Mapping[str, Any]] = None,
  historical_short_window_preflight: Optional[Mapping[str, Any]] = None,
  full_pressure_attempt: Optional[Mapping[str, Any]] = None,
  pressure_baseline: Optional[Mapping[str, Any]] = None,
  operational_evidence: Optional[Mapping[str, Any]] = None,
  recent_completed_diagnostic: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
  """Create a self-contained report object before rendering it to Markdown."""

  declared_abnormal = tuple(sorted(set(abnormal_dates)))
  is_formal_20_day_request = (
    requested_trading_days == DEFAULT_TRADING_DAYS
    and recent_completed_diagnostic is None
  )
  if not is_formal_20_day_request and formal_execution is not None:
    raise AcceptanceBlockedError(
      "FORMAL_EXECUTION_REQUIRES_EXACTLY_20_TRADING_DAYS"
    )
  formal_window = (
    select_formal_window(audits, abnormal_dates=declared_abnormal)
    if is_formal_20_day_request
    else None
  )
  if formal_execution is not None and formal_window is None:
    raise AcceptanceBlockedError(
      "FORMAL_EXECUTION_REQUIRES_READY_20_TRADING_DAY_WINDOW"
    )
  if not is_formal_20_day_request:
    formal_status = "BLOCKED"
    formal_blocker = (
      "RECENT_COMPLETED_DIAGNOSTIC_NON_CAUSAL"
      if recent_completed_diagnostic is not None
      else "FORMAL_20_TRADING_DAYS_REQUIRED"
    )
  elif formal_execution:
    formal_status = (
      "PASS"
      if str(formal_execution.get("replay", {}).get("status", "")).upper()
      == "COMPLETED"
      else "FAIL"
    )
    formal_blocker = None
  elif formal_window is not None:
    formal_status = "READY_NOT_EXECUTED"
    formal_blocker = None
  else:
    formal_status = "BLOCKED"
    formal_blocker = "NO_ALL_HOLDINGS_20_TRADING_DAY_WINDOW"
  canonical_execution = bool(
    formal_execution
    and isinstance(formal_execution.get("canonical_tick_archive"), Mapping)
  )
  return {
    "schema_version": REPORT_SCHEMA_VERSION,
    "generated_at": datetime.now().astimezone().isoformat(),
    "scope": {
      "requested_trading_days": requested_trading_days,
      "calendar": "SH via TradingDateHelper",
      "causality": (
        "NON_GATING_NON_CAUSAL: current/latest holding snapshot projected over recent completed dates; never formal/PAPER evidence"
        if recent_completed_diagnostic
        else "D-1 snapshot only; no future account state"
      ),
      "all_holdings_policy": "no stock/day is silently excluded",
      "tick_quality": (
        "CanonicalTickArchiveReader.inspect_tick_day"
        if canonical_execution
        else "StrategyManager._inspect_t_trade_replay_tick_day"
      ),
      "source_identity": (
        "CanonicalTickArchiveReader.iter_tick_pages"
        if canonical_execution
        else "HistoricalMarketDataService.iter_tick_pages"
      ),
      "execution": "isolated BACKTEST only; no QMT/PAPER/LIVE commands",
      "recent_completed_diagnostic": (
        dict(recent_completed_diagnostic)
        if recent_completed_diagnostic
        else None
      ),
    },
    "declared_abnormal_dates": [item.isoformat() for item in declared_abnormal],
    "formal_20_trading_day": {
      "status": formal_status,
      "selected_snapshot_date": (
        formal_window.window.snapshot.snapshot_date.isoformat()
        if formal_window
        else None
      ),
      "execution": (
        dict(formal_execution)
        if is_formal_20_day_request and formal_execution
        else None
      ),
      "blocker": formal_blocker,
    },
    "short_window_coverage_diagnostic": (
      None
      if is_formal_20_day_request
      else _short_window_coverage_diagnostic(
        audits,
        requested_trading_days=requested_trading_days,
      )
    ),
    "historical_short_window_source_identity_preflight": (
      dict(historical_short_window_preflight)
      if historical_short_window_preflight
      else None
    ),
    "full_pressure_attempt": (
      dict(full_pressure_attempt) if full_pressure_attempt else None
    ),
    "pressure_baseline": (
      _sanitize_pressure_baseline_for_report(pressure_baseline)
      if pressure_baseline
      else None
    ),
    "operational_evidence": (
      dict(operational_evidence) if operational_evidence else None
    ),
    "performance_remediation_microbenchmark": _performance_remediation_evidence(
      pressure_baseline
    ),
    "candidate_windows": [
      audit.to_dict(abnormal_dates=declared_abnormal) for audit in audits
    ],
  }


def _replace_reused_candidate_window(
  report: dict[str, Any],
  refreshed_audit: WindowAudit,
  *,
  abnormal_dates: Iterable[date] = (),
) -> None:
  """Atomically replace a stale reuse-report window with current audit facts."""

  snapshot_date = refreshed_audit.window.snapshot.snapshot_date.isoformat()
  refreshed = refreshed_audit.to_dict(abnormal_dates=abnormal_dates)
  existing_windows = list(report.get("candidate_windows") or [])
  matching_indexes = [
    index
    for index, item in enumerate(existing_windows)
    if isinstance(item, Mapping)
    and str(dict(item.get("snapshot") or {}).get("snapshot_date") or "")
    == snapshot_date
  ]
  if len(matching_indexes) != 1:
    raise AcceptanceBlockedError("REUSE_AUDIT_WINDOW_REPLACEMENT_AMBIGUOUS")
  existing_windows[matching_indexes[0]] = refreshed
  report["candidate_windows"] = existing_windows

  # A one-window refresh can only prove that this candidate remains blocked;
  # it must never promote the 20-day formal gate from stale sibling windows.
  if refreshed_audit.blockers(abnormal_dates=abnormal_dates):
    report["formal_20_trading_day"] = {
      "status": "BLOCKED",
      "selected_snapshot_date": None,
      "execution": None,
      "blocker": "NO_ALL_HOLDINGS_20_TRADING_DAY_WINDOW",
    }


def _assert_operational_evidence_matches_refreshed_window(
  operational_evidence: Mapping[str, Any],
  refreshed_audit: WindowAudit,
  source_identity: Mapping[str, Any],
) -> None:
  """Reject a report if its addendum disagrees with its refreshed candidate."""

  transfer = _require_mapping_field(
    operational_evidence,
    field="historical_tick_transfer",
    context="OPERATIONAL_EVIDENCE",
  )
  scope = _require_mapping_field(
    transfer, field="scope", context="HISTORICAL_TRANSFER"
  )
  coverage = _require_mapping_field(
    transfer, field="strict_coverage", context="HISTORICAL_TRANSFER"
  )
  identity = _require_mapping_field(
    transfer, field="source_identity", context="HISTORICAL_TRANSFER"
  )
  if (
    str(scope.get("snapshot_date") or "")
    != refreshed_audit.window.snapshot.snapshot_date.isoformat()
    or coverage.get("complete_instrument_days")
    != refreshed_audit.completed_pair_count
    or coverage.get("expected_instrument_days")
    != refreshed_audit.expected_pair_count
    or identity.get("failed_instrument_days") != 0
    or not bool(source_identity.get("passed"))
    or list(dict(source_identity.get("failure") or {}).get("failures") or [])
  ):
    raise AcceptanceBlockedError("OPERATIONAL_EVIDENCE_REFRESH_MISMATCH")


def render_markdown(report: Mapping[str, Any], *, json_name: str) -> str:
  """Render a concise human report; JSON retains every stock/day evidence row."""

  formal = dict(report["formal_20_trading_day"])
  pressure = dict(report.get("pressure_baseline") or {})
  diagnostic_raw = report.get("short_window_coverage_diagnostic")
  short_diagnostic = (
    dict(diagnostic_raw) if isinstance(diagnostic_raw, Mapping) else None
  )
  lines = [
    "# 做 T V3 历史回放与全持仓压力验收",
    "",
    f"- 生成时间：`{report['generated_at']}`",
    f"- 正式 20 交易日门禁：**{formal['status']}**",
    "- 因果口径：D-1 账户日结快照；按 SH 真实交易日；所有正持仓均纳入，绝不自动剔除。",
    "- Tick 口径：Engine 同款严格连续交易时段检查；正式/压力执行前另以严格 source-identity keyset 分页验证。",
    "- 执行边界：仅隔离 `BACKTEST`；本工具不启动 QMT、不发送 PAPER/LIVE 指令、不补数。",
    f"- 完整机读证据：[JSON]({json_name})",
    "",
    "## 20 个交易日严格因果回放",
    "",
  ]
  if formal["status"] == "BLOCKED":
    if short_diagnostic is not None:
      lines.extend(
        [
          "**BLOCKED**：本次仅请求 {} 个交易日；20 日正式回放不具备输入，"
          "因此没有启动。".format(
            short_diagnostic.get("requested_trading_days")
          ),
          "",
        ]
      )
    else:
      lines.extend(
        [
          "**BLOCKED**：没有任一 D-1 快照形成全持仓 20/20 Tick 完整窗口；因此没有启动正式回放。",
          "",
        ]
      )
  elif formal["status"] == "READY_NOT_EXECUTED":
    lines.extend(["**READY_NOT_EXECUTED**：覆盖已通过，等待显式 `--execute`。", ""])
  else:
    lines.extend([f"**{formal['status']}**：详见 JSON 的 execution。", ""])

  if short_diagnostic is not None:
    coverage_windows = [
      dict(candidate)
      for candidate in list(short_diagnostic.get("coverage_windows") or [])
      if isinstance(candidate, Mapping)
    ]
    lines.extend(
      [
        "## 短窗口覆盖诊断（NON_GATING）",
        "",
        "**{}**：请求 {} 个交易日；候选窗口 {} 个，完整覆盖 {} 个。".format(
          short_diagnostic.get("status"),
          short_diagnostic.get("requested_trading_days"),
          short_diagnostic.get("candidate_count"),
          short_diagnostic.get("complete_candidate_count"),
        ),
        "- {}".format(short_diagnostic.get("message")),
        "",
        "| D-1 快照 | 持仓 | 窗口 | 完整 instrument-day | 共同完整日 | 连续共同前缀 | 缺失 instrument-day |",
        "| --- | ---: | --- | ---: | --- | --- | ---: |",
      ]
    )
    for candidate in coverage_windows:
      snapshot = dict(candidate.get("snapshot") or {})
      coverage = dict(candidate.get("coverage") or {})
      dates = list(candidate.get("trading_dates") or [])
      window = f"{dates[0]}~{dates[-1]}" if dates else "-"
      lines.append(
        "| {snapshot} | {holdings} | {window} | {complete}/{expected} | {shared} | {prefix} | {missing} |".format(
          snapshot=snapshot.get("snapshot_date"),
          holdings=snapshot.get("holding_count"),
          window=window,
          complete=coverage.get("complete_instrument_days"),
          expected=coverage.get("expected_instrument_days"),
          shared=",".join(coverage.get("full_shared_dates") or []) or "-",
          prefix=(
            ",".join(coverage.get("contiguous_shared_prefix_dates") or [])
            or "-"
          ),
          missing=len(list(candidate.get("missing_instrument_days") or [])),
        )
      )
  else:
    lines.extend(
      [
        "| D-1 快照 | 持仓 | 窗口 | 完整 instrument-day | 共同完整日 | 连续共同前缀 | 门禁阻塞 |",
        "| --- | ---: | --- | ---: | --- | --- | --- |",
      ]
    )
    for candidate in report["candidate_windows"]:
      snapshot = candidate["snapshot"]
      coverage = candidate["coverage"]
      dates = candidate["trading_dates"]
      blockers = candidate["formal_gate_blockers"]
      window = f"{dates[0]}~{dates[-1]}" if dates else "-"
      lines.append(
        "| {snapshot} | {holdings} | {window} | {complete}/{expected} | {shared} | {prefix} | {blockers} |".format(
          snapshot=snapshot["snapshot_date"],
          holdings=snapshot["holding_count"],
          window=window,
          complete=coverage["complete_instrument_days"],
          expected=coverage["expected_instrument_days"],
          shared=",".join(coverage["full_shared_dates"]) or "-",
          prefix=",".join(coverage["contiguous_shared_prefix_dates"]) or "-",
          blockers=",".join(blockers) or "-",
        )
      )

  lines.extend(["", "## 真实短窗口 source identity 预检（非 20 日门禁）", ""])
  historical_preflight = report.get("historical_short_window_source_identity_preflight")
  if historical_preflight:
    source = dict(historical_preflight)
    failure = dict(source.get("failure") or {})
    failures = list(failure.get("failures") or [])
    failure_counts = Counter(
      str(item.get("message") or "UNKNOWN") for item in failures
    )
    lines.extend(
      [
        "**{}**：{} 个 instrument-day 严格 source-identity keyset 检查失败；"
        "完整逐日证据在 JSON。".format(
          "PASS" if source.get("passed") else "BLOCKED",
          len(failures),
        ),
        "- 失败原因：{}".format(
          "；".join(
            f"{reason} × {count}" for reason, count in sorted(failure_counts.items())
          )
          or "-"
        ),
      ]
    )
    raw_storage_audit = source.get("raw_storage_identity_audit")
    if isinstance(raw_storage_audit, Mapping):
      source_column = dict(raw_storage_audit.get("source_time_ms") or {})
      ordinal_column = dict(raw_storage_audit.get("tick_ordinal") or {})
      continuity = dict(raw_storage_audit.get("continuity_generation") or {})
      storage_time = dict(raw_storage_audit.get("storage_time") or {})
      lines.append(
        "- 原始存储审计：{} 个 instrument-day / {} Tick；"
        "source_time_ms={}（非空 {}/{}），tick_ordinal={}（非空 {}/{}）；"
        "continuity_generation 字段存在={}; 存储 time 严格递增={}、重复={}; "
        "结论={}。".format(
          raw_storage_audit.get("instrument_day_count"),
          raw_storage_audit.get("row_count"),
          source_column.get("arrow_type"),
          source_column.get("non_null_count"),
          source_column.get("null_count"),
          ordinal_column.get("arrow_type"),
          ordinal_column.get("non_null_count"),
          ordinal_column.get("null_count"),
          continuity.get("field_present"),
          storage_time.get("all_instrument_days_strictly_increasing"),
          storage_time.get("duplicate_count"),
          raw_storage_audit.get("conclusion"),
        )
      )
  else:
    lines.append("**NOT_RUN**：未选择真实短窗口进行 source identity 预检。")

  operational = report.get("operational_evidence")
  if isinstance(operational, Mapping):
    transfer = dict(operational.get("historical_tick_transfer") or {})
    cumulative = dict(transfer.get("cumulative_records") or {})
    strict_coverage = dict(transfer.get("strict_coverage") or {})
    identity = dict(transfer.get("source_identity") or {})
    formal_replay = dict(operational.get("formal_causal_replay") or {})
    restore = dict(operational.get("restore_verify") or {})
    rollout = dict(operational.get("rollout") or {})
    paper = dict(rollout.get("paper") or {})
    canary = dict(rollout.get("canary") or {})
    live = dict(rollout.get("live") or {})
    lines.extend(
      [
        "",
        "## 本轮数据、恢复与上线门禁补充",
        "",
        "- 历史 Tick 传输：received/saved/verified={}/{}/{}；状态={}。".format(
          cumulative.get("received"),
          cumulative.get("saved"),
          cumulative.get("verified"),
          transfer.get("status"),
        ),
        "- 严格覆盖：{}/{} instrument-day；source identity 严格 keyset 失败={}。"
        "该覆盖不足以启动正式 20 日回放。".format(
          strict_coverage.get("complete_instrument_days"),
          strict_coverage.get("expected_instrument_days"),
          identity.get("failed_instrument_days"),
        ),
        "- 正式因果回放：{}/{}；stage={}；未以合成压力替代历史回放。".format(
          formal_replay.get("completed_trading_days"),
          formal_replay.get("requested_trading_days"),
          formal_replay.get("stage"),
        ),
        "- restore-verify：{}；备份 schema {} 在隔离 scratch DB 前向升级到 {} 并通过；"
        "production database restored={}。".format(
          restore.get("status"),
          restore.get("source_schema_revision"),
          restore.get("target_schema_revision"),
          restore.get("production_database_restored"),
        ),
        "- 上线门禁：PAPER {}（连续交易日 {}/{}，完成候选生命周期 {}/{}）；"
        "CANARY={}；LIVE={}；operator_review={}。".format(
          paper.get("status"),
          paper.get("consecutive_trading_days"),
          paper.get("required_consecutive_trading_days"),
          paper.get("completed_candidate_lifecycles"),
          paper.get("required_candidate_lifecycles"),
          canary.get("status"),
          live.get("status"),
          rollout.get("operator_review"),
        ),
      ]
    )

  lines.extend(["", "## 9,600 Tick 全持仓合成压力尝试", ""])
  full_pressure_attempt = report.get("full_pressure_attempt")
  pressure_fixture = dict(pressure.get("fixture") or {})
  current_full_pressure = int(pressure_fixture.get("tick_count") or 0) == 9_600
  replay = dict(pressure.get("replay") or {})
  run_evidence = dict(pressure.get("run_evidence") or {})
  terminal_convergence = dict(pressure.get("terminal_convergence") or {})
  completed_full_pressure = (
    str(pressure.get("status") or "").upper()
    == "EXECUTED_SYNTHETIC_NON_HISTORICAL"
    and str(replay.get("status") or "").upper() == "COMPLETED"
    and str(terminal_convergence.get("status") or "").upper() == "TERMINAL"
  )
  if current_full_pressure and completed_full_pressure:
    throughput = dict(pressure.get("throughput") or {})
    latency = dict(pressure.get("latency") or {})
    tick_accounting = dict(pressure.get("tick_accounting") or {})
    lines.extend(
      [
        "**{}**：当前生产 Engine 路径已完成固定全持仓全量合成负载；"
        "该结果只冻结本机合成 SLO，绝不替代 20 日历史回放。".format(
          pressure.get("status")
        ),
        "- runId=`{}`；请求区间={}~{}；处理至={}；进度={}%；wall={}s。".format(
          run_evidence.get("run_id"),
          replay.get("start_time") or replay.get("replay_start_time"),
          replay.get("end_time") or replay.get("replay_end_time"),
          replay.get("processed_until"),
          replay.get("progress_pct"),
          pressure.get("elapsed_seconds"),
        ),
        "- 请求/有效处理：{} / {} engine ticks；采样 engine tick={}，checkpoint={}。".format(
          pressure_fixture.get("tick_count"),
          throughput.get("engine_ticks_processed"),
          dict(latency.get("engine_tick") or {}).get("sample_count"),
          dict(latency.get("state_checkpoint") or {}).get("sample_count"),
        ),
        "- fixture：`SYNTHETIC_NON_HISTORICAL`，sha256={}，{} ticks，{} instruments，合法交易时段={}。".format(
          pressure_fixture.get("fixture_sha256"),
          pressure_fixture.get("tick_count"),
          len(list(pressure_fixture.get("held_instruments") or [])),
          pressure_fixture.get("market_time_policy"),
        ),
        "- deadline：{}s；timed_out={}；隔离 BACKTEST cancellation={}".format(
          pressure.get("timeout_seconds"),
          pressure.get("timed_out"),
          pressure.get("cancellation"),
        ),
      ]
    )
    if tick_accounting:
      lines.append(
        "- Tick 口径核对：请求={}，实际评估={}，策略过滤={}；{} 个 "
        "instrument-day 每个过滤 {} 条（{}，{}），不是丢失或未处理 Tick。".format(
          tick_accounting.get("requested_fixture_ticks"),
          tick_accounting.get("engine_ticks_processed"),
          tick_accounting.get("policy_filtered_ticks"),
          tick_accounting.get("instrument_day_count"),
          tick_accounting.get("policy_filtered_per_instrument_day"),
          tick_accounting.get("continuous_pm_policy"),
          tick_accounting.get("policy_filtered_time_range"),
        )
      )
    if full_pressure_attempt:
      lines.append(
        "- 历史取消尝试保留在 JSON 的 `full_pressure_attempt`，不作为当前 SLO 结果。"
      )
  elif current_full_pressure:
    lines.extend(
      [
        "**{}**：固定 9,600 Tick 合成压力未以可接受终态完成；SLO 仍为 "
        "**BLOCKED/FAIL**。".format(pressure.get("status")),
        "- replay={}；terminal={}；processed={}；progress={}%；wall={}s。".format(
          replay.get("status"),
          terminal_convergence.get("status"),
          dict(pressure.get("throughput") or {}).get("engine_ticks_processed"),
          replay.get("progress_pct"),
          pressure.get("elapsed_seconds"),
        ),
      ]
    )
  elif full_pressure_attempt:
    full_attempt = dict(full_pressure_attempt)
    full_fixture = dict(full_attempt.get("fixture") or {})
    lines.extend(
      [
        "**{}**：本轮全负载没有完成，SLO 判定为 **BLOCKED/FAIL**，不得以小样本替代。".format(
          full_attempt.get("status")
        ),
        "- runId=`{}`；请求区间={}~{}；处理至={}；进度={}%。".format(
          full_attempt.get("run_id"),
          full_attempt.get("requested_replay_start"),
          full_attempt.get("requested_replay_end"),
          full_attempt.get("processed_until"),
          full_attempt.get("progress_pct"),
        ),
        "- 取消原因：{}；局部 materialization logical events/s={}。".format(
          full_attempt.get("cancellation_reason"),
          full_attempt.get("partial_materialization_logical_events_per_second"),
        ),
        "- fixture：`SYNTHETIC_NON_HISTORICAL`，sha256={}，{} ticks，{} instruments，合法交易时段={}。".format(
          full_fixture.get("fixture_sha256"),
          full_fixture.get("tick_count"),
          len(list(full_fixture.get("held_instruments") or [])),
          full_fixture.get("market_time_policy"),
        ),
        "- 观察到的主要耗时边界：{}。".format(
          full_attempt.get("primary_observed_boundary")
        ),
        "- 未测项：{}。".format(full_attempt.get("unavailable_metrics")),
      ]
    )
  else:
    lines.append("**NOT_RUN**：未记录全 9,600 Tick 合成压力尝试。")

  lines.extend(["", "## 全持仓合成压力基线 / 首次本机 SLO", ""])
  latency: dict[str, Any] = {}
  if not pressure:
    lines.append("**NOT_RUN**：未请求可选的合成压力基线。")
  else:
    status = str(pressure.get("status") or "UNKNOWN")
    lines.append(
      f"**{status}**：此结果为合成负载，不是历史回放，且不替代 20 交易日门禁。"
    )
    fixture = dict(pressure.get("fixture") or {})
    if fixture:
      lines.append(
        "- fixture：sha256={}, {} ticks，{} instruments，合法交易时段={}。".format(
          fixture.get("fixture_sha256"),
          fixture.get("tick_count"),
          len(list(fixture.get("held_instruments") or [])),
          fixture.get("market_time_policy"),
        )
      )
    if pressure.get("version_stale_reason"):
      lines.append(
        "- 版本可比性：`VERSION_STALE`；{}。".format(
          pressure.get("version_stale_reason")
        )
      )
    latency = dict(pressure.get("latency") or {})
    throughput = dict(pressure.get("throughput") or {})
    cas = dict(pressure.get("cas") or {})
    lines.extend(
      [
        "- Engine tick 延迟（ms）：p50={p50}, p95={p95}, p99={p99}".format(
          **dict(latency.get("engine_tick") or {})
        )
        if latency.get("engine_tick")
        else "- Engine tick 延迟：无样本",
        "- 策略评估延迟（ms）：p50={p50}, p95={p95}, p99={p99}".format(
          **dict(latency.get("strategy_evaluation") or {})
        )
        if latency.get("strategy_evaluation")
        else "- 策略评估延迟：无样本",
        "- 吞吐：{} engine ticks/s（{} ticks）".format(
          throughput.get("engine_ticks_per_second"),
          throughput.get("engine_ticks_processed"),
        ),
        "- CAS：{} conflicts / {} checkpoint attempts，rate={}".format(
          cas.get("snapshot_conflicts"),
          cas.get("checkpoint_attempts"),
          cas.get("conflict_rate"),
        ),
        "- DB 写活动：{}；评估：{}".format(
          pressure.get("database_write_activity"),
          dict(pressure.get("run_evidence") or {}).get("evaluations"),
        ),
        "- 生产路径覆盖边界：{}。".format(
          pressure.get("production_path_coverage")
        ),
        "- 冻结 SLO：{}".format(
          pressure.get("frozen_local_slo")
          or "N/A（只有完成固定 9,600 Tick 全量夹具才可冻结/判定 SLO）"
        ),
    ]
  )
  run_evidence = dict(pressure.get("run_evidence") or {})
  execution_boundary = dict(pressure.get("execution_boundary") or {})
  if run_evidence:
    terminal = dict(pressure.get("terminal_convergence") or {})
    lines.append(
      "- 隔离执行证据：runId=`{}`；terminal={}；sealed durable RuntimeState={}；"
      "QMT={}，PAPER/LIVE command={}.".format(
        run_evidence.get("run_id") or pressure.get("run_id"),
        terminal.get("status") or "N/A",
        execution_boundary.get("runtime_state_persist_enabled"),
        execution_boundary.get("qmt_invocation"),
        execution_boundary.get("paper_or_live_command"),
      )
    )
  state_checkpoint = dict(latency.get("state_checkpoint") or {})
  state_snapshot = dict(latency.get("state_snapshot") or {})
  if state_checkpoint or state_snapshot:
    lines.append(
      "- Durable RuntimeState latency（ms）：checkpoint p50/p95/p99={}/{}/{}；"
      "snapshot p50/p95/p99={}/{}/{}。".format(
        state_checkpoint.get("p50"),
        state_checkpoint.get("p95"),
        state_checkpoint.get("p99"),
        state_snapshot.get("p50"),
        state_snapshot.get("p95"),
        state_snapshot.get("p99"),
      )
    )
  runtime_state_writes = dict(
    dict(pressure.get("database_write_activity") or {}).get("runtime_state")
    or {}
  )
  if runtime_state_writes:
    lines.append(
      "- Position DB writes：replace={}，same-code update={}，rows={}；"
      "每 Tick 的 state CAS/upsert 仍保留（attempts={}）。".format(
        runtime_state_writes.get("position_replace_snapshot_calls"),
        runtime_state_writes.get("position_update_existing_snapshot_calls"),
        runtime_state_writes.get("position_rows_submitted"),
        runtime_state_writes.get("state_upsert_attempts"),
      )
    )
  if state_checkpoint and state_snapshot and latency.get("strategy_evaluation"):
    strategy_latency = dict(latency.get("strategy_evaluation") or {})
    completed_slo = bool(
      dict(report.get("performance_remediation_microbenchmark") or {}).get(
        "full_9600_replayed_after_patch"
      )
    )
    completion_note = (
      "固定 9,600 Tick 已完成；本机合成 SLO 仅按本机和本工作负载冻结，"
      "不改变正式 20 日历史门禁。"
      if completed_slo
      else "未完成固定 9,600 Tick，SLO 继续 BLOCKED。"
    )
    lines.append(
      "- 性能判读（仅诊断）：strategy p95={}ms，而 checkpoint/snapshot p95={}/{}ms；"
      "长尾位于外部数据库持久化边界。{}".format(
        strategy_latency.get("p95"),
        state_checkpoint.get("p95"),
        state_snapshot.get("p95"),
        completion_note,
      )
    )
  nonpersistent_calibration = report.get("nonpersistent_calibration_attempt")
  if _is_nonpersistent_diagnostic_pressure_attempt(nonpersistent_calibration):
    historical = dict(nonpersistent_calibration)
    historical_run = dict(historical.get("run_evidence") or {})
    persistence = dict(historical.get("runtime_state_persistence") or {})
    lines.extend(
      [
        "",
        "### 历史非持久 480 Tick 校准（NON_GATING）",
        "",
        "**{}**：runId=`{}`；sealed durable marker={}。该运行的普通 BACKTEST "
        "`persist_enabled=False`，所以其 checkpoint/CAS/position 指标不可用于持久 "
        "生产路径或 SLO。".format(
          historical.get("status"),
          historical_run.get("run_id") or historical.get("run_id"),
          persistence.get("enabled"),
        ),
      ]
    )
  performance_microbenchmark = report.get("performance_remediation_microbenchmark")
  if performance_microbenchmark:
    micro = dict(performance_microbenchmark)
    before = dict(
      dict(micro.get("sqlite_in_memory_microbenchmark") or {}).get("before") or {}
    )
    after = dict(
      dict(micro.get("sqlite_in_memory_microbenchmark") or {}).get("after") or {}
    )
    lines.extend(
      [
        "",
        "## 后续性能修复微基准（非压力验收）",
        "",
        "**{}**：{}。".format(micro.get("status"), micro.get("implementation")),
        "- 正确性微测：{}。".format(
          "；".join(str(item) for item in micro.get("correctness_tests") or [])
        ),
        "- SQLite 内存微基准（{}）：{} commits / {} ms → {} commits / {} ms；"
        "commit 减少 {}%，耗时减少 {}%。".format(
          dict(micro.get("sqlite_in_memory_microbenchmark") or {}).get("workload"),
          before.get("commits"),
          before.get("elapsed_ms"),
          after.get("commits"),
          after.get("elapsed_ms"),
          dict(micro.get("sqlite_in_memory_microbenchmark") or {}).get(
            "commit_reduction_pct"
          ),
          dict(micro.get("sqlite_in_memory_microbenchmark") or {}).get(
            "elapsed_reduction_pct"
          ),
        ),
        "- 验证：{}。".format(micro.get("focused_validation")),
        (
          "- 门禁：**已使用性能补丁后的完整 9,600 Tick 运行；本机合成 SLO 为 {}。**".format(
            micro.get("slo_status")
          )
          if micro.get("full_9600_replayed_after_patch")
          else "- 门禁：**未重跑 9,600 Tick；SLO 仍为 {}。**".format(
            micro.get("slo_status")
          )
        ),
        "- 边界：{}。".format(micro.get("scope_limit")),
      ]
    )
  lines.extend(
    [
      "",
      "## 判定说明",
      "",
      "- `PASS` 只代表完成了已验证的正式 20 日 BACKTEST；不表示 PAPER、Canary 或 LIVE 验收。",
      "- `BLOCKED` 是数据/输入证据不足，绝不以合成单测或短窗口替代真实 20 日/交易时段证据。",
      "- `EXECUTED_SYNTHETIC_NON_HISTORICAL` 是全持仓、合成 Tick 的本机压力基线；它只能冻结机器 SLO，不能升级真实历史或正式门禁。",
      "- `EXECUTED_DIAGNOSTIC_NON_GATING` 仅用于定位量化延迟与写入边界；即使完成，也不得替代或通过全负载 SLO。",
      "",
    ]
  )
  return "\n".join(lines)


def write_report(report: Mapping[str, Any], path: Path) -> tuple[Path, Path]:
  """Write Markdown and full JSON evidence together using atomic replacement."""

  path.parent.mkdir(parents=True, exist_ok=True)
  json_path = path.with_suffix(".json")
  markdown = render_markdown(report, json_name=json_path.name)
  json_payload = json.dumps(
    report, ensure_ascii=False, indent=2, sort_keys=True, default=_json_default
  )
  for target, content in ((path, markdown), (json_path, json_payload + "\n")):
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(target)
  return path, json_path


def _parse_date(value: str) -> date:
  try:
    return date.fromisoformat(value)
  except ValueError as exc:
    raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def _positive_seconds(value: str) -> float:
  try:
    seconds = float(value)
  except ValueError as exc:
    raise argparse.ArgumentTypeError("expected a positive number of seconds") from exc
  if seconds <= 0:
    raise argparse.ArgumentTypeError("expected a positive number of seconds")
  return seconds


def build_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--account-id", help="account id; omitted only when unique")
  parser.add_argument(
    "--report",
    type=Path,
    default=DEFAULT_FORMAL_REPORT_PATH,
    help="Markdown report path; matching JSON evidence is written beside it",
  )
  parser.add_argument(
    "--trading-days",
    type=int,
    default=DEFAULT_TRADING_DAYS,
    choices=range(1, 21),
    help="1-19 only produce a non-gating coverage diagnostic; 20 is required for formal execution",
  )
  parser.add_argument(
    "--recent-completed-trading-days",
    type=int,
    choices=range(1, 21),
    help=(
      "non-gating/non-causal diagnostic: inspect the most recent N completed "
      "SH trading days only (today is always excluded); cannot execute a replay"
    ),
  )
  parser.add_argument(
    "--abnormal-date",
    action="append",
    type=_parse_date,
    default=[],
    help="declared abnormal trading day evidence required for formal gate",
  )
  parser.add_argument(
    "--max-concurrency", type=int, default=DEFAULT_AUDIT_CONCURRENCY
  )
  parser.add_argument(
    "--execute",
    action="store_true",
    help="execute only an already-complete formal causal window",
  )
  parser.add_argument(
    "--canonical-tick-archive-root",
    type=Path,
    help=(
      "absolute immutable canonical Tick archive root; requires "
      "--canonical-tick-cutover-token and is formal-20d execute only"
    ),
  )
  parser.add_argument(
    "--canonical-tick-cutover-token",
    help=(
      "content-addressed canonical Tick cutover token; archive mode has no "
      "Influx/QMT fallback"
    ),
  )
  parser.add_argument(
    "--synthetic-pressure",
    action="store_true",
    help=(
      "run a deterministic, non-historical all-holdings Engine pressure "
      "baseline; never a formal replay gate"
    ),
  )
  parser.add_argument(
    "--pressure-snapshot-date",
    type=_parse_date,
    help="required D-1 snapshot date for --synthetic-pressure",
  )
  parser.add_argument(
    "--synthetic-ticks-per-instrument-day",
    type=int,
    default=600,
    help="deterministic synthetic load per held instrument and trading day",
  )
  parser.add_argument(
    "--pressure-timeout-seconds",
    type=_positive_seconds,
    default=DEFAULT_PRESSURE_TIMEOUT_SECONDS,
    help=(
      "bounded wall-clock deadline for an isolated synthetic BACKTEST; on "
      "expiry the runner cancels only that replay through its durable "
      "BACKTEST cancellation path (default: 1800)"
    ),
  )
  parser.add_argument(
    "--reuse-audit-report",
    type=Path,
    help=(
      "reuse an existing JSON audit report for a full or diagnostic synthetic "
      "pressure rerun; the historical coverage evidence is not recalculated"
    ),
  )
  parser.add_argument(
    "--completed-pressure-report",
    type=Path,
    help=(
      "import one already-terminal, sealed 9,600-Tick pressure report without "
      "starting another replay"
    ),
  )
  parser.add_argument(
    "--operational-evidence",
    type=Path,
    help=(
      "bounded JSON addendum for independently verified historical transfer, "
      "restore-verify, and rollout-gate facts"
    ),
  )
  parser.add_argument(
    "--cancelled-full-pressure-run-id",
    help="terminal isolated full-pressure BACKTEST run id to preserve in report",
  )
  parser.add_argument(
    "--diagnostic-synthetic-pressure",
    action="store_true",
    help="run a small synthetic quantile diagnostic; never freeze or pass SLO",
  )
  parser.add_argument(
    "--completed-diagnostic-pressure-run-id",
    help=(
      "recover durable evidence from a completed small diagnostic without "
      "running it again; process-local quantiles remain N/A"
    ),
  )
  parser.add_argument(
    "--diagnostic-ticks-per-instrument-day",
    type=int,
    default=30,
    help="small diagnostic synthetic load per held instrument and day",
  )
  return parser


def _reused_report_has_authoritative_formal_20_window(
  report: Mapping[str, Any],
) -> bool:
  """Require an exact 20-day selected window before retaining a reused claim."""

  scope = report.get("scope")
  if not isinstance(scope, Mapping):
    return False
  requested_days = scope.get("requested_trading_days")
  if type(requested_days) is not int or requested_days != DEFAULT_TRADING_DAYS:
    return False
  formal = report.get("formal_20_trading_day")
  if not isinstance(formal, Mapping):
    return False
  selected_snapshot_date = str(formal.get("selected_snapshot_date") or "")
  if not selected_snapshot_date:
    return False
  for candidate in list(report.get("candidate_windows") or []):
    if not isinstance(candidate, Mapping):
      continue
    snapshot = candidate.get("snapshot")
    if not isinstance(snapshot, Mapping):
      continue
    if str(snapshot.get("snapshot_date") or "") != selected_snapshot_date:
      continue
    candidate_requested_days = candidate.get("requested_trading_days")
    candidate_dates = candidate.get("trading_dates")
    if (
      type(candidate_requested_days) is int
      and candidate_requested_days == DEFAULT_TRADING_DAYS
      and isinstance(candidate_dates, list)
      and len(candidate_dates) == DEFAULT_TRADING_DAYS
    ):
      return True
  return False


def _fail_closed_reused_formal_gate(report: dict[str, Any]) -> None:
  """Strip a stale short-window formal claim while retaining diagnostic reuse."""

  formal = report.get("formal_20_trading_day")
  formal_status = (
    str(formal.get("status") or "").upper()
    if isinstance(formal, Mapping)
    else ""
  )
  scope = report.get("scope")
  requested_days = scope.get("requested_trading_days") if isinstance(scope, Mapping) else None
  if type(requested_days) is not int or requested_days != DEFAULT_TRADING_DAYS:
    blocker = "FORMAL_20_TRADING_DAYS_REQUIRED"
  elif formal_status == "BLOCKED":
    return
  elif _reused_report_has_authoritative_formal_20_window(report):
    return
  else:
    blocker = "REUSE_AUDIT_FORMAL_20_SCOPE_UNVERIFIED"
  report["formal_20_trading_day"] = {
    "status": "BLOCKED",
    "selected_snapshot_date": None,
    "execution": None,
    "blocker": blocker,
  }


async def _run_reuse_audit_cli(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
  """Rerun declared synthetic pressure without repeating the historical audit."""

  report_path = Path(args.reuse_audit_report)
  json_path = report_path.with_suffix(".json")
  try:
    existing = json.loads(json_path.read_text(encoding="utf-8"))
  except (OSError, json.JSONDecodeError) as exc:
    raise AcceptanceBlockedError("REUSE_AUDIT_REPORT_UNREADABLE") from exc
  if not isinstance(existing, dict) or not isinstance(
    existing.get("candidate_windows"), list
  ):
    raise AcceptanceBlockedError("REUSE_AUDIT_REPORT_INVALID")
  _fail_closed_reused_formal_gate(existing)
  if args.pressure_snapshot_date is None:
    raise ValueError("--reuse-audit-report requires --pressure-snapshot-date")
  if args.synthetic_pressure and args.diagnostic_synthetic_pressure:
    raise ValueError(
      "--synthetic-pressure and --diagnostic-synthetic-pressure are mutually exclusive"
    )
  if args.completed_pressure_report and (
    args.synthetic_pressure
    or args.diagnostic_synthetic_pressure
    or args.completed_diagnostic_pressure_run_id
  ):
    raise ValueError(
      "--completed-pressure-report cannot be combined with a pressure execution"
    )
  snapshots = await load_snapshot_portfolios(account_id=args.account_id)
  snapshot = next(
    (
      item
      for item in snapshots
      if item.snapshot_date == args.pressure_snapshot_date
    ),
    None,
  )
  if snapshot is None:
    raise AcceptanceBlockedError("REUSE_AUDIT_SNAPSHOT_NOT_FOUND")
  candidate = next(
    (
      item
      for item in existing["candidate_windows"]
      if str(dict(item.get("snapshot") or {}).get("snapshot_date") or "")
      == args.pressure_snapshot_date.isoformat()
    ),
    None,
  )
  if not isinstance(candidate, Mapping):
    raise AcceptanceBlockedError("REUSE_AUDIT_WINDOW_NOT_FOUND")
  reported_codes = tuple(
    str(item.get("instrument_code") or "").upper()
    for item in list(dict(candidate.get("snapshot") or {}).get("holdings") or [])
  )
  if reported_codes != snapshot.instrument_codes:
    raise AcceptanceBlockedError("REUSE_AUDIT_HOLDINGS_MISMATCH")
  if args.completed_pressure_report:
    # Importing a terminal full-pressure result must not retain a stale
    # candidate window just because the former pressure execution reused it.
    # Refresh this exact D-1 snapshot with the Engine's current strict audit,
    # then page every refreshed instrument-day by source identity before the
    # report is allowed to carry the new pressure evidence.
    refreshed_windows = await build_replay_windows(
      [snapshot], requested_trading_days=DEFAULT_TRADING_DAYS
    )
    if len(refreshed_windows) != 1:
      raise AcceptanceBlockedError("REUSE_AUDIT_REFRESH_WINDOW_INVALID")
    refreshed_audits = await audit_tick_coverage(
      refreshed_windows, max_concurrency=args.max_concurrency
    )
    if len(refreshed_audits) != 1:
      raise AcceptanceBlockedError("REUSE_AUDIT_REFRESH_AUDIT_INVALID")
    audit = refreshed_audits[0]
    pressure_dates = audit.window.trading_dates
    historical_preflight = (
      await audit_source_identity(audit, pressure_dates)
    ).to_dict()
    _replace_reused_candidate_window(existing, audit)
  else:
    raw_prefix = list(
      dict(candidate.get("coverage") or {}).get(
        "contiguous_shared_prefix_dates"
      )
      or []
    )
    try:
      pressure_dates = tuple(date.fromisoformat(str(item)) for item in raw_prefix)
    except ValueError as exc:
      raise AcceptanceBlockedError("REUSE_AUDIT_PREFIX_INVALID") from exc
    if len(pressure_dates) < 2:
      raise AcceptanceBlockedError("REUSE_AUDIT_PRESSURE_PREFIX_TOO_SHORT")
    audit = WindowAudit(
      window=ReplayWindow(
        snapshot=snapshot,
        trading_dates=pressure_dates,
        requested_trading_days=DEFAULT_TRADING_DAYS,
      ),
      inspections={},
    )
    reused_preflight = existing.get("historical_short_window_source_identity_preflight")
    historical_preflight = (
      dict(reused_preflight)
      if isinstance(reused_preflight, Mapping)
      else (await audit_source_identity(audit, pressure_dates)).to_dict()
    )
  full_pressure_attempt = existing.get("full_pressure_attempt")
  if args.cancelled_full_pressure_run_id:
    full_fixture = build_synthetic_pressure_fixture(
      audit,
      pressure_dates,
      ticks_per_instrument_day=600,
    )
    full_pressure_attempt = await load_cancelled_full_pressure_attempt(
      args.cancelled_full_pressure_run_id,
      cancellation_reason=(
        "FULL_9600_TICK_SYNTHETIC_PRESSURE_EXCEEDED_REASONABLE_RUNTIME; "
        "operator-authorized cancellation of this isolated BACKTEST only"
      ),
      fixture=full_fixture,
    )
  # A later report refresh (for example, after locating a cancelled full run)
  # must not erase a completed diagnostic evidence block merely because this
  # invocation does not request a second diagnostic execution.  In particular,
  # retain older pre-capability 480 runs as explicit NON_GATING evidence rather
  # than letting their checkpoint timings masquerade as durable CAS coverage.
  previous_pressure = _normalize_nonpersistent_diagnostic_pressure_attempt(
    existing.get("pressure_baseline")
  )
  retained_calibration = _normalize_nonpersistent_diagnostic_pressure_attempt(
    existing.get("nonpersistent_calibration_attempt")
  )
  nonpersistent_calibration_attempt = (
    retained_calibration
    if _is_nonpersistent_diagnostic_pressure_attempt(retained_calibration)
    else None
  )
  if (
    isinstance(previous_pressure, Mapping)
    and _is_nonpersistent_diagnostic_pressure_attempt(previous_pressure)
  ):
    nonpersistent_calibration_attempt = previous_pressure
  pressure_baseline = previous_pressure
  if (
    args.diagnostic_synthetic_pressure
    and args.completed_diagnostic_pressure_run_id
  ):
    raise ValueError(
      "--diagnostic-synthetic-pressure and "
      "--completed-diagnostic-pressure-run-id are mutually exclusive"
    )
  if args.synthetic_pressure and args.completed_diagnostic_pressure_run_id:
    raise ValueError(
      "--synthetic-pressure and --completed-diagnostic-pressure-run-id are mutually exclusive"
    )
  if args.synthetic_pressure:
    try:
      pressure_baseline = await execute_synthetic_pressure_baseline(
        audit,
        pressure_dates,
        ticks_per_instrument_day=args.synthetic_ticks_per_instrument_day,
        timeout_seconds=args.pressure_timeout_seconds,
      )
    except Exception as exc:
      pressure_baseline = {
        "schema_version": PRESSURE_BASELINE_SCHEMA_VERSION,
        "status": "FAIL",
        "non_gating": True,
        "not_historical_replay": True,
        "isolated_backtest": True,
        "no_live_or_paper_broker": True,
        "timeout_seconds": args.pressure_timeout_seconds,
        "error_type": type(exc).__name__,
        "error_message": str(exc),
      }
  elif args.completed_diagnostic_pressure_run_id:
    diagnostic_fixture = build_synthetic_pressure_fixture(
      audit,
      pressure_dates,
      ticks_per_instrument_day=args.diagnostic_ticks_per_instrument_day,
    )
    pressure_baseline = await load_completed_diagnostic_pressure_attempt(
      args.completed_diagnostic_pressure_run_id,
      fixture=diagnostic_fixture,
    )
    if not bool(
      dict(pressure_baseline.get("runtime_state_persistence") or {}).get("enabled")
    ):
      nonpersistent_calibration_attempt = pressure_baseline
  elif args.diagnostic_synthetic_pressure:
    try:
      pressure_baseline = await execute_synthetic_pressure_baseline(
        audit,
        pressure_dates,
        ticks_per_instrument_day=args.diagnostic_ticks_per_instrument_day,
        diagnostic=True,
        timeout_seconds=args.pressure_timeout_seconds,
      )
    except Exception as exc:
      pressure_baseline = {
        "schema_version": PRESSURE_BASELINE_SCHEMA_VERSION,
        "status": "FAIL",
        "non_gating": True,
        "diagnostic_non_gating": True,
        "error_type": type(exc).__name__,
        "error_message": str(exc),
      }
  elif args.completed_pressure_report:
    pressure_baseline = _load_completed_pressure_baseline(
      args.completed_pressure_report
    )
  if args.operational_evidence:
    operational_evidence = _load_operational_evidence(args.operational_evidence)
    if args.completed_pressure_report:
      _assert_operational_evidence_matches_refreshed_window(
        operational_evidence,
        audit,
        historical_preflight,
      )
    existing["operational_evidence"] = operational_evidence
  existing["generated_at"] = datetime.now().astimezone().isoformat()
  scope = dict(existing.get("scope") or {})
  scope["coverage_evidence_reused_from"] = str(json_path)
  existing["scope"] = scope
  existing["historical_short_window_source_identity_preflight"] = historical_preflight
  existing["full_pressure_attempt"] = full_pressure_attempt
  existing["pressure_baseline"] = (
    _sanitize_pressure_baseline_for_report(pressure_baseline)
    if pressure_baseline
    else None
  )
  existing["nonpersistent_calibration_attempt"] = (
    _sanitize_pressure_baseline_for_report(nonpersistent_calibration_attempt)
    if nonpersistent_calibration_attempt
    else None
  )
  existing["performance_remediation_microbenchmark"] = _performance_remediation_evidence(
    pressure_baseline
  )
  formal_status = str(
    dict(existing.get("formal_20_trading_day") or {}).get("status") or "BLOCKED"
  )
  return existing, 0 if formal_status == "PASS" else 2


def _open_canonical_archive_from_args(
  args: argparse.Namespace,
) -> tuple[Optional[CanonicalTickArchiveReader], Optional[Path]]:
  root = getattr(args, "canonical_tick_archive_root", None)
  token = getattr(args, "canonical_tick_cutover_token", None)
  if (root is None) != (token is None):
    raise ValueError(
      "--canonical-tick-archive-root and --canonical-tick-cutover-token must be used together"
    )
  if root is None:
    return None, None
  if not bool(getattr(args, "execute", False)):
    raise AcceptanceBlockedError("CANONICAL_ARCHIVE_REQUIRES_EXPLICIT_EXECUTE")
  if int(getattr(args, "trading_days", 0)) != DEFAULT_TRADING_DAYS:
    raise AcceptanceBlockedError("CANONICAL_ARCHIVE_REQUIRES_EXACTLY_20_TRADING_DAYS")
  root_path = Path(root)
  if not root_path.is_absolute():
    raise ValueError("canonical Tick archive root must be an absolute path")
  try:
    reader = CanonicalTickArchive(root_path, create=False).open(str(token))
  except CanonicalTickArchiveError as exc:
    raise AcceptanceBlockedError("CANONICAL_ARCHIVE_OPEN_FAILED") from exc
  return reader, root_path


def _apply_recent_completed_diagnostic_report_path(args: argparse.Namespace) -> Path:
  """Keep non-causal rolling diagnostics out of the formal acceptance report."""

  report = Path(args.report)
  if (
    getattr(args, "recent_completed_trading_days", None) is not None
    and report == DEFAULT_FORMAL_REPORT_PATH
  ):
    args.report = DEFAULT_RECENT_COMPLETED_DIAGNOSTIC_REPORT_PATH
  return Path(args.report)


async def run_cli(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
  recent_completed_days = getattr(args, "recent_completed_trading_days", None)
  _apply_recent_completed_diagnostic_report_path(args)
  if bool(os.environ.get("ENABLE_REAL_TRADING", "").strip().lower() == "true"):
    raise AcceptanceBlockedError("REAL_TRADING_ENVIRONMENT_NOT_ALLOWED")
  if recent_completed_days is not None and args.execute:
    raise AcceptanceBlockedError("RECENT_COMPLETED_DIAGNOSTIC_CANNOT_EXECUTE")
  if args.execute and args.trading_days != DEFAULT_TRADING_DAYS:
    raise AcceptanceBlockedError(
      "FORMAL_EXECUTION_REQUIRES_EXACTLY_20_TRADING_DAYS"
    )
  archive_reader, archive_root = _open_canonical_archive_from_args(args)
  if recent_completed_days is not None and archive_reader is not None:
    raise AcceptanceBlockedError("RECENT_COMPLETED_DIAGNOSTIC_CANNOT_USE_ARCHIVE_EXECUTION")
  if archive_reader is not None and (
    args.reuse_audit_report
    or args.synthetic_pressure
    or args.diagnostic_synthetic_pressure
    or args.completed_pressure_report
    or args.completed_diagnostic_pressure_run_id
  ):
    raise ValueError(
      "canonical Tick archive formal execution cannot be combined with reuse or pressure modes"
    )
  if args.reuse_audit_report:
    return await _run_reuse_audit_cli(args)
  if args.execute and args.synthetic_pressure:
    raise ValueError("--execute and --synthetic-pressure are mutually exclusive")
  if args.completed_pressure_report and (
    args.synthetic_pressure
    or args.diagnostic_synthetic_pressure
    or args.completed_diagnostic_pressure_run_id
  ):
    raise ValueError(
      "--completed-pressure-report cannot be combined with a pressure execution"
    )
  if args.completed_pressure_report and args.pressure_snapshot_date is None:
    raise ValueError(
      "--completed-pressure-report requires --pressure-snapshot-date for "
      "source-identity verification"
    )
  if args.synthetic_pressure and args.pressure_snapshot_date is None:
    raise ValueError("--synthetic-pressure requires --pressure-snapshot-date")
  if recent_completed_days is not None and (
    args.reuse_audit_report
    or args.synthetic_pressure
    or args.diagnostic_synthetic_pressure
    or args.completed_pressure_report
    or args.completed_diagnostic_pressure_run_id
  ):
    raise ValueError(
      "recent completed trading-day diagnostic cannot be combined with reuse or pressure modes"
    )

  snapshots = await load_snapshot_portfolios(account_id=args.account_id)
  recent_completed_diagnostic: Optional[dict[str, Any]] = None
  if recent_completed_days is not None:
    if not snapshots:
      raise AcceptanceBlockedError("RECENT_COMPLETED_DIAGNOSTIC_SNAPSHOT_MISSING")
    selected_snapshot = max(snapshots, key=lambda item: item.snapshot_date)
    recent_dates = await resolve_completed_trading_dates(
      requested_days=int(recent_completed_days),
      require_exact=True,
    )
    windows = [
      ReplayWindow(
        snapshot=selected_snapshot,
        trading_dates=recent_dates,
        requested_trading_days=int(recent_completed_days),
      )
    ]
    recent_completed_diagnostic = {
      "status": "NON_GATING_NON_CAUSAL",
      "policy": "MOST_RECENT_COMPLETED_SH_TRADING_DAYS_ONLY",
      "as_of_date_excluded": datetime.now(_SHANGHAI).date().isoformat(),
      "trading_dates": [item.isoformat() for item in recent_dates],
      "snapshot_date": selected_snapshot.snapshot_date.isoformat(),
      "formal_or_paper_evidence": False,
    }
    requested_trading_days = int(recent_completed_days)
  else:
    windows = await build_replay_windows(
      snapshots, requested_trading_days=args.trading_days
    )
    requested_trading_days = args.trading_days
  audits = (
    await audit_canonical_tick_coverage(windows, reader=archive_reader)
    if archive_reader is not None
    else await audit_tick_coverage(windows, max_concurrency=args.max_concurrency)
  )
  formal_window = (
    None
    if recent_completed_diagnostic is not None
    else select_formal_window(audits, abnormal_dates=args.abnormal_date)
  )
  formal_execution: Optional[dict[str, Any]] = None
  historical_short_window_preflight: Optional[dict[str, Any]] = None
  pressure_baseline: Optional[dict[str, Any]] = None
  if recent_completed_diagnostic is not None:
    historical_short_window_preflight = (
      await audit_source_identity(audits[0], audits[0].window.trading_dates)
    ).to_dict()
  if args.execute:
    if formal_window is None:
      raise AcceptanceBlockedError("FORMAL_20_TRADING_DAY_GATE_BLOCKED")
    formal_execution = await execute_isolated_backtest(
      formal_window,
      formal_window.window.trading_dates,
      formal_gate=True,
      abnormal_dates=args.abnormal_date,
      archive_reader=archive_reader,
      archive_root=archive_root,
    )
  if args.synthetic_pressure or args.completed_pressure_report:
    pressure_audit, pressure_dates = select_pressure_window(
      audits,
      snapshot_date=args.pressure_snapshot_date,
    )
    historical_short_window_preflight = (
      await audit_source_identity(pressure_audit, pressure_dates)
    ).to_dict()
    if args.completed_pressure_report:
      pressure_baseline = _load_completed_pressure_baseline(
        args.completed_pressure_report
      )
    else:
      try:
        pressure_baseline = await execute_synthetic_pressure_baseline(
          pressure_audit,
          pressure_dates,
          ticks_per_instrument_day=args.synthetic_ticks_per_instrument_day,
          timeout_seconds=args.pressure_timeout_seconds,
        )
      except Exception as exc:
        pressure_baseline = {
          "schema_version": PRESSURE_BASELINE_SCHEMA_VERSION,
          "status": "FAIL",
          "snapshot_date": args.pressure_snapshot_date.isoformat(),
          "error_type": type(exc).__name__,
          "error_message": str(exc),
          "non_gating": True,
        }
  operational_evidence = (
    _load_operational_evidence(args.operational_evidence)
    if args.operational_evidence
    else None
  )
  if operational_evidence and args.completed_pressure_report:
    _assert_operational_evidence_matches_refreshed_window(
      operational_evidence,
      pressure_audit,
      historical_short_window_preflight,
    )
  report = build_report_document(
    audits,
    requested_trading_days=requested_trading_days,
    abnormal_dates=args.abnormal_date,
    formal_execution=formal_execution,
    historical_short_window_preflight=historical_short_window_preflight,
    pressure_baseline=pressure_baseline,
    operational_evidence=operational_evidence,
    recent_completed_diagnostic=recent_completed_diagnostic,
  )
  # Formal status governs the process exit: a short baseline never returns a
  # false-zero exit code for a blocked 20-day acceptance gate.
  formal_status = report["formal_20_trading_day"]["status"]
  return report, 0 if formal_status == "PASS" else 2


def main(argv: Optional[Sequence[str]] = None) -> int:
  parser = build_parser()
  args = parser.parse_args(argv)
  try:
    report, result_code = asyncio.run(run_cli(args))
  except (AcceptanceBlockedError, ValueError) as exc:
    parser.error(str(exc))
    return 2
  markdown_path, json_path = write_report(report, args.report)
  print(f"formal_20_day={report['formal_20_trading_day']['status']}")
  print(f"markdown={markdown_path}")
  print(f"json={json_path}")
  return result_code


if __name__ == "__main__":
  raise SystemExit(main())
