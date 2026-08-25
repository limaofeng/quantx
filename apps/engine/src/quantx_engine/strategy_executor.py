"""
Engine 策略执行器 - 专注于策略运行的并发执行和资源管理

职责：
1. 管理策略运行实例的并发执行
2. 线程池/协程池资源管理
3. 实时状态监控和心跳管理
4. 异常处理和资源清理

不负责：
- 策略发现和协调（StrategyManager）
- API 层交互（StrategyManager）
- 持久化策略模板（StrategyManager）
"""

import asyncio
import copy
import hashlib
import inspect
import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import AsyncExitStack, nullcontext
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from enum import Enum
from math import isfinite
from time import monotonic
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Mapping, Optional, Type

from quantx_application.t_trade_v3 import (
  D1ProfileReadReason,
  D1ProfileReadRequest,
  EvaluateIntentEmissionGate,
  EvaluationMaterializationError,
  IntentEmissionGateInput,
  MaterializeEvaluationAfterCAS,
  PostCasEvaluationInput,
  ReadD1ReferenceProfile,
  TTradeAccountFacts,
  compute_t_trade_account_facts,
)
from quantx_contracts.market_stream import (
  MARKET_STREAM_MAX_CAPTURE_AGE_SECONDS,
  MARKET_STREAM_MAX_FUTURE_SKEW_SECONDS,
)
from quantx_domain.brokers.backtest import BacktestBroker
from quantx_domain.brokers.base import (
  BrokerBase,
  OrderRequest,
  OrderStatus,
  Position,
  TradeRecord,
)
from quantx_domain.brokers.base import OrderType as BrokerOrderType
from quantx_domain.brokers.simulator import SimulatorBroker
from quantx_domain.strategies.base import (
  ManualApprovalRecoveryCandidate,
  MarketDataContext,
  MarketDataSession,
  OrderStateEvent,
  StrategyBase,
  StrategyCadence,
  StrategyContext,
  StrategyInput,
  StrategyOutput,
  StrategyRunMode,
  TradeExecutionEvent,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentExecutionMode,
  TradeIntentPriority,
  validate_runtime_state_patch_contents,
)
from quantx_domain.trading import (
  EXIT_PLAN_BOOK_STATE_KEY,
  AshareDataContextProvider,
  AShareMarketRules,
  ContextRiskLayer,
  DecisionTrace,
  EntryPlanStatus,
  ExitDecision,
  ExitEvaluationContext,
  ExitPlanBook,
  ExitPlanEvaluator,
  ExitPlanTemplate,
  ExitPriceReference,
  ExitRuleType,
  ExitStrategyRegistry,
  ExitT1Policy,
  ManagedEntryPlanState,
  MarketDataSnapshot,
  OpportunityPolicy,
  OrderRiskDecision,
  OrderSizer,
  PortfolioOrchestrationLayer,
  PositionAdjustmentLayer,
  RiskAction,
  TradingRiskChecker,
  resolve_ashare_daily_limit_rate,
)
from quantx_domain.trading.decision_trace import (
  summarize_intent,
  summarize_strategy_input,
)
from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.core.brokers.live import LiveBroker
from quantx_infrastructure.core.data import (
  DataAdapter,
  HistoricalDataAdapter,
  adapter_manager,
  whole_quote_hub,
)
from quantx_infrastructure.core.data.tick_identity import (
  normalize_ticks_losslessly,
  tick_page_content_identity,
  tick_snapshot_identity,
  tick_source_time_ms,
)
from quantx_infrastructure.core.market_data_manager import MarketDataManager
from quantx_infrastructure.core.runtime_log_manager import RuntimeLogManager
from quantx_infrastructure.core.strategy_performance import (
  StrategyPerformanceRecorder,
  StrategyPerformanceService,
)
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models import ExecutionMetrics, KLine
from quantx_infrastructure.models.agent_runtime import (
  PendingTradeOrder,
  StrategyOrderCorrelation,
  StrategyRuntimeEvent,
  TradeCommandOutbox,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.auto_exit_plan_service import AutoExitPlanService
from quantx_infrastructure.services.entry_plan_authorization_service import (
  EntryPlanAuthorizationError,
  EntryPlanAuthorizationService,
  scope_from_managed_entry_config,
)
from quantx_infrastructure.services.exit_plan_replay_projection_service import (
  ExitPlanReplayUpdateKind,
  exit_plan_replay_projection_service,
)
from quantx_infrastructure.services.t_trade_candidate_outcome_service import (
  TTradeCandidateOutcomePersistenceFacade,
)
from quantx_infrastructure.services.t_trade_monitor_projection_service import (
  TTradeMonitorProjectionService,
  t_trade_monitor_projection_service,
)
from quantx_infrastructure.services.t_trade_opportunity_runtime_service import (
  T_TRADE_OPPORTUNITY_EVALUATION_EVENT,
  TTradeOpportunityRuntimeService,
  t_trade_opportunity_runtime_service,
)
from quantx_infrastructure.services.t_trade_replay_projection_service import (
  TTradeReplayUpdateKind,
  t_trade_replay_projection_service,
)
from quantx_infrastructure.services.t_trade_signal_diagnostics_service import (
  TTradeSignalDiagnosticsService,
)
from quantx_infrastructure.services.trade_command_service import TradeCommandService
from quantx_infrastructure.services.trading_time_service import TradingDateHelper
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, OperationalError

from .replay_clock import ReplayClock
from .t_trade_coordination import t_trade_account_coordination_lock
from .t_trade_observability import (
  TTradeRuntimeObservability,
  t_trade_runtime_observability,
)
from .t_trade_phase_one_baseline import TTradePhaseOneBaselineAccumulator

if TYPE_CHECKING:
  from quantx_infrastructure.core.market_data_manager import MarketDataManager
  from quantx_infrastructure.core.runtime_log_manager import RuntimeLogManager
  from quantx_infrastructure.core.runtime_state_manager import RuntimeStateManager


_T_TRADE_REPLAY_TICK_PAGE_SIZE = 6_000
_T_TRADE_REPLAY_MAX_TICK_PAGES_PER_WINDOW = 100
_DURABLE_EVENT_APPLY_TIMEOUT_SECONDS = 10.0
_RUNTIME_MARKET_EVENT_QUEUE_CAPACITY = 256
_T_TRADE_DEFAULT_EXECUTION_QUOTE_MAX_AGE_SECONDS = 3.0
_T_TRADE_PROFILE_LOOKUP_RETRY_SECONDS = 30.0
# Runtime-state persistence is intentionally policy-driven rather than tied to
# one replay/benchmark capability.  BACKTEST seals once per virtual day;
# PAPER/LIVE seal only at proven session boundaries.  Broker reports, orders,
# fills, approvals, and material candidates retain their explicit immediate
# durability boundaries below.
RUNTIME_STATE_CHECKPOINT_POLICY_DAY_BATCH = "DAY_BATCH"
RUNTIME_STATE_CHECKPOINT_POLICY_SESSION_BOUNDARY = "SESSION_BOUNDARY"
_SESSION_CHECKPOINT_MAX_RETRIES = 60
_SESSION_CHECKPOINT_RETRY_SECONDS = 5.0
_SESSION_CHECKPOINT_SPECS = (
  ("AM", time(11, 30), time(11, 35)),
  ("PM", time(15, 0), time(15, 5)),
)
_CHECKPOINT_EVALUATION_OUTBOX_MAX_EVENTS = 8192
_TRACE_AUDIT_PATCH_FORMAT = "CONTENT_ADDRESSED_RUNTIME_STATE_PATCH_V1"
_TRACE_AUDIT_INLINE_STRING_MAX_BYTES = 256
_T_TRADE_TRACE_PROJECTION_FORMAT = "T_TRADE_DECISION_TRACE_CAUSAL_INDEX_V3"
_T_TRADE_TRACE_MARKER_LIST_LIMIT = 32
_T_TRADE_TRACE_SCALAR_MAX_BYTES = 256
_T_TRADE_EVALUATION_VERSION_KEYS = (
  "config_version",
  "policy_version",
  "feature_schema_version",
  "state_schema_version",
  "profile_version",
  "profile_fingerprint",
)
_T_TRADE_TRACE_PAYLOAD_KEYS = frozenset(
  {
    "reason",
    "candidate_id",
    "candidate_fingerprint",
    "accepted",
    "ignored",
    "active_volume",
    "exit_plan_id",
    "exit_plan_status",
    "net_profit_pct",
    "peak_net_profit_pct",
    "trailing_floor_pct",
    "time_exit_mode",
    "holding_trading_days",
    "instrument_count",
    "rewarmed_instrument_count",
    "signal_snapshot",
    "source_identity",
    "added",
    "removed",
  }
)
_T_TRADE_TRACE_PAYLOAD_SCALAR_KEYS = tuple(
  sorted(
    _T_TRADE_TRACE_PAYLOAD_KEYS
    - {"signal_snapshot", "source_identity", "added", "removed"}
  )
)
_T_TRADE_EXECUTION_STATE_SCALAR_KEYS = (
  "status",
  "draining",
  "pending_entry_intent_id",
  "pending_exit_intent_id",
  "entry_order_status",
  "exit_order_status",
  "entry_terminal_order_status",
  "exit_terminal_order_status",
  "entry_filled_volume",
  "exit_filled_volume",
  "profit_armed",
  "cooldown_until_ms",
  "batch_id",
  "exit_plan_id",
  "reconciliation_reason",
)
_T_TRADE_TOP_LEVEL_AWAITING_KEYS = frozenset(
  {"instrument_code", "candidate_id", "intent_id", "source_time_ms"}
)
_T_TRADE_STATE_PATCH_ROOT_KEYS = frozenset(
  {
    "instrument_states",
    "universe_revision",
    "opportunity",
    "awaiting",
    *_T_TRADE_EXECUTION_STATE_SCALAR_KEYS,
  }
)
_T_TRADE_INSTRUMENT_STATE_KEYS = frozenset(
  {
    "status",
    "requested_entry_amount",
    "draining",
    "opportunity",
    "pending_entry_intent_id",
    "pending_exit_intent_id",
    "entry_order_status",
    "exit_order_status",
    "entry_terminal_order_status",
    "exit_terminal_order_status",
    "entry_expected_fill_volume",
    "exit_expected_fill_volume",
    "entry_pending_fill_base",
    "exit_pending_fill_base",
    "entry_filled_volume",
    "entry_avg_price",
    "exit_filled_volume",
    "exit_avg_price",
    "last_price",
    "last_net_profit_pct",
    "peak_net_profit_pct",
    "trailing_floor_pct",
    "profit_armed",
    "last_exit_reason",
    "cooldown_until_ms",
    "completed_cycles",
    "batch_id",
    "exit_plan_id",
    "batch_started_trade_date",
    "last_holding_trade_date",
    "holding_trading_days",
    "exit_policy_snapshot",
    "reconciliation_reason",
  }
)
_T_TRADE_OPPORTUNITY_STATE_KEYS = frozenset(
  {
    "schema_version",
    "instrument_code",
    "trade_date",
    "continuity_generation",
    "data_health",
    "health_reasons",
    "samples",
    "pullback",
    "momentum",
    "candidate",
    "candidate_status",
    "candidate_suppressed",
    "candidate_awaiting_approval",
    "rearm_started_at_ms",
    "latest_evaluation",
    "preview_score",
    "candidate_score",
    "revalidate_score",
    "rearm_score",
    "thresholds",
    "state_version",
    "feature_schema_version",
    "policy_version",
    "config_version",
    "profile_fingerprint",
    "event_cursor",
    "last_policy_rewarm_identity",
  }
)
_T_TRADE_CANDIDATE_STATE_KEYS = frozenset(
  {
    "candidate_id",
    "fingerprint",
    "episode_id",
    "path",
    "latched_at_ms",
    "expires_at_ms",
    "source_time_ms",
    "tick_ordinal",
    "price",
    "score",
    "policy_version",
    "feature_schema_version",
    "reference_profile_version",
    "reference_profile_schema_version",
  }
)
_T_TRADE_INTENT_METADATA_SCALAR_KEYS = frozenset(
  {
    "t_trade_role",
    "account_id",
    "strategy_run_id",
    "instrument_code",
    "opportunity_schema_version",
    "signal_version",
    "candidate_id",
    "candidate_fingerprint",
    "candidate_state_version",
    "candidate_status",
    "config_version",
    "policy_version",
    "feature_schema_version",
    "profile_version",
    "profile_fingerprint",
    "source_time_ms",
    "tick_ordinal",
    "continuity_generation",
    "opportunity_score",
    "requested_entry_amount",
    "target_trade_amount",
    "max_trade_amount",
    "t_batch_id",
    "exit_plan_id",
    "global_monitor_id",
  }
)
_T_TRADE_INTENT_METADATA_REFERENCE_KEYS = frozenset({"exit_plan_template"})
# Profile snapshots are process-local decision inputs.  Keep their cardinality
# bounded independently from the eligibility snapshot so a long-running
# account-level runtime cannot grow forever as its universe rotates.
_T_TRADE_PROFILE_CACHE_MAX_ENTRIES = 4096
# The eligibility map is a point-in-time runtime input, not an unbounded
# holding/session cache.  A single account does not need more entries than
# this; rejecting a larger reconcile keeps the fail-closed boundary bounded.
_T_TRADE_INTENT_EMISSION_MAX_INSTRUMENTS = 4096

_TRACE_AUDIT_JSON_SCALAR_TYPES = frozenset(
  {type(None), bool, int, float, str}
)


def _trace_audit_json_default(value: Any) -> Any:
  """Encode the small non-JSON values accepted in strategy state patches."""

  if isinstance(value, Decimal):
    return format(value, "f")
  if isinstance(value, (datetime, date)):
    return value.isoformat()
  if isinstance(value, Enum):
    enum_value = value.value
    if _trace_audit_requires_normalization(enum_value):
      return _trace_audit_json_value(enum_value)
    return enum_value
  if isinstance(value, tuple):
    return list(value)
  if isinstance(value, Mapping):
    return _trace_audit_json_value(value)
  raise TypeError(
    f"Object of type {type(value).__name__} is not JSON serializable"
  )


def _trace_audit_requires_normalization(value: Any) -> bool:
  """Return whether JSON needs the deterministic Mapping/key fallback.

  The hot path consists of exact ``dict``/``list``/``tuple`` containers with
  string keys.  Scan that graph without allocating a normalized copy; the C
  JSON encoder then traverses and serializes it.  An arbitrary Mapping or a
  non-string key keeps the historic string-key normalization fallback.
  """

  value_type = type(value)
  if value_type in _TRACE_AUDIT_JSON_SCALAR_TYPES:
    return False
  if (
    value_type is not dict
    and value_type is not list
    and value_type is not tuple
  ):
    return isinstance(value, Mapping)

  stack = [value]
  visited: set[int] = set()
  while stack:
    current = stack.pop()
    current_type = type(current)
    if current_type is dict:
      identity = id(current)
      if identity in visited:
        continue
      visited.add(identity)
      for key, child in current.items():
        if type(key) is not str:
          return True
        child_type = type(child)
        if (
          child_type is dict
          or child_type is list
          or child_type is tuple
        ):
          stack.append(child)
        elif (
          child_type not in _TRACE_AUDIT_JSON_SCALAR_TYPES
          and isinstance(child, Mapping)
        ):
          return True
    elif current_type is list or current_type is tuple:
      identity = id(current)
      if identity in visited:
        continue
      visited.add(identity)
      for child in current:
        child_type = type(child)
        if (
          child_type is dict
          or child_type is list
          or child_type is tuple
        ):
          stack.append(child)
        elif (
          child_type not in _TRACE_AUDIT_JSON_SCALAR_TYPES
          and isinstance(child, Mapping)
        ):
          return True
    elif isinstance(current, Mapping):
      return True
  return False


def _trace_audit_scalar_value(value: Any) -> Any:
  """Return the JSON scalar representation without normalizing containers."""

  if isinstance(value, Decimal):
    return format(value, "f")
  if isinstance(value, (datetime, date)):
    return value.isoformat()
  if isinstance(value, Enum):
    return _trace_audit_scalar_value(value.value)
  return value


def _trace_audit_json_value(value: Any) -> Any:
  """Normalize non-standard mapping/key payloads for deterministic JSON."""

  if isinstance(value, Mapping):
    return {
      str(key): _trace_audit_json_value(item)
      for key, item in value.items()
    }
  if isinstance(value, (list, tuple)):
    return [_trace_audit_json_value(item) for item in value]
  if isinstance(value, (datetime, date)):
    return value.isoformat()
  if isinstance(value, Decimal):
    return format(value, "f")
  if isinstance(value, Enum):
    return _trace_audit_json_value(value.value)
  return value


def _canonical_trace_audit_bytes(
  value: Any,
  *,
  requires_normalization: Optional[bool] = None,
) -> bytes:
  """Return the single stable byte representation used by audit hashes."""

  if requires_normalization is None:
    requires_normalization = _trace_audit_requires_normalization(value)
  if requires_normalization:
    value = _trace_audit_json_value(value)
  return json.dumps(
    value,
    ensure_ascii=True,
    sort_keys=True,
    separators=(",", ":"),
    allow_nan=False,
    default=_trace_audit_json_default,
  ).encode("utf-8")


def _trace_audit_value_type(value: Any) -> str:
  value = _trace_audit_scalar_value(value)
  if value is None:
    return "null"
  if isinstance(value, bool):
    return "boolean"
  if isinstance(value, (int, float)):
    return "number"
  if isinstance(value, str):
    return "string"
  if isinstance(value, Mapping):
    return "object"
  if isinstance(value, (list, tuple)):
    return "array"
  return type(value).__name__


def _trace_audit_value_summary(
  value: Any,
  *,
  requires_normalization: Optional[bool] = None,
) -> Dict[str, Any]:
  """Describe one patch value without retaining its possibly hot payload."""

  serialized = _canonical_trace_audit_bytes(
    value,
    requires_normalization=requires_normalization,
  )
  scalar_value = _trace_audit_scalar_value(value)
  summary: Dict[str, Any] = {
    "sha256": hashlib.sha256(serialized).hexdigest(),
    "json_bytes": len(serialized),
    "type": _trace_audit_value_type(scalar_value),
    "cardinality": (
      len(scalar_value)
      if isinstance(scalar_value, (Mapping, list, tuple, str))
      else None
    ),
  }
  if scalar_value is None or isinstance(scalar_value, (bool, int, float)):
    summary["value"] = scalar_value
  elif (
    isinstance(scalar_value, str)
    and len(scalar_value.encode("utf-8")) <= _TRACE_AUDIT_INLINE_STRING_MAX_BYTES
  ):
    summary["value"] = scalar_value
  return summary


def _trace_audit_event_identity_value(value: Any) -> Any:
  """Keep only scalar event identity fields in an audit summary."""

  scalar_value = _trace_audit_scalar_value(value)
  if scalar_value is None or isinstance(scalar_value, (bool, int, float, str)):
    return scalar_value
  return None


def _compact_runtime_state_patch_for_audit(
  patch: Any,
  *,
  instrument_code: str,
) -> Dict[str, Any]:
  """Content-address a strategy patch without duplicating runtime hot state."""

  raw_patch = {
    "set": getattr(patch, "set", {}) or {},
    "unset": getattr(patch, "unset", []) or [],
    "append_events": getattr(patch, "append_events", []) or [],
  }
  requires_normalization = _trace_audit_requires_normalization(raw_patch)
  audit_patch = (
    _trace_audit_json_value(raw_patch)
    if requires_normalization
    else raw_patch
  )
  set_values = audit_patch["set"] or {}
  append_events = audit_patch["append_events"] or []
  full_patch = _canonical_trace_audit_bytes(
    audit_patch,
    requires_normalization=False,
  )
  set_summary = {
    key: _trace_audit_value_summary(
      set_values[key],
      requires_normalization=False,
    )
    for key in sorted(set_values)
  }

  instrument_states = set_values.get("instrument_states")
  if isinstance(instrument_states, Mapping):
    current_instrument = str(instrument_code or "")
    current_branch = {
      "instrument_code": current_instrument,
      "present": current_instrument in instrument_states,
    }
    current_branch.update(
      _trace_audit_value_summary(
        instrument_states.get(current_instrument),
        requires_normalization=False,
      )
    )
    set_summary["instrument_states"]["current_instrument"] = current_branch

  event_summaries = []
  for event in append_events:
    event_payload = _canonical_trace_audit_bytes(
      event,
      requires_normalization=False,
    )
    event_mapping = event if isinstance(event, Mapping) else {}
    event_summaries.append(
      {
        "event_key": _trace_audit_event_identity_value(
          event_mapping.get("event_key")
        ),
        "type": _trace_audit_event_identity_value(event_mapping.get("type")),
        "record_kind": _trace_audit_event_identity_value(
          event_mapping.get("record_kind")
        ),
        "event_type": _trace_audit_event_identity_value(
          event_mapping.get("event_type")
        ),
        "sha256": hashlib.sha256(event_payload).hexdigest(),
        "json_bytes": len(event_payload),
      }
    )

  return {
    "format": _TRACE_AUDIT_PATCH_FORMAT,
    "set_keys": sorted(set_values),
    "set": set_summary,
    "unset": list(audit_patch["unset"] or []),
    "append_events": event_summaries,
    "full_patch_sha256": hashlib.sha256(full_patch).hexdigest(),
    "full_patch_json_bytes": len(full_patch),
  }


def _t_trade_trace_bounded_scalar(value: Any, *, field_name: str) -> Any:
  """Return one finite, bounded scalar for the T-trade causal index.

  Unlike the former content-addressed trace projection, this function must
  never traverse or hash an arbitrary subtree.  A new structured field is
  therefore either explicitly projected below or rejected before the output
  can cross the durable audit boundary.
  """

  scalar = _trace_audit_scalar_value(value)
  if scalar is None or isinstance(scalar, bool) or isinstance(scalar, int):
    return scalar
  if isinstance(scalar, float):
    if not isfinite(scalar):
      raise ValueError(f"T-trade trace {field_name} contains a non-finite float")
    return scalar
  if isinstance(scalar, str):
    if len(scalar.encode("utf-8")) > _T_TRADE_TRACE_SCALAR_MAX_BYTES:
      raise ValueError(f"T-trade trace {field_name} exceeds scalar size limit")
    return scalar
  raise ValueError(f"T-trade trace {field_name} must be a scalar")


def _t_trade_trace_require_allowed_keys(
  source: Mapping[str, Any],
  *,
  allowed_keys: frozenset[str],
  field_name: str,
) -> None:
  """Reject a new structured root before it can reach durable trace JSON.

  T-trade's detailed evaluation is retained once by its dedicated evidence
  records.  The per-Tick trace is intentionally an allow-listed causal index,
  so accepting a new object here would silently recreate the repeated-root
  write that this projection removes.
  """

  invalid_keys = [key for key in source if not isinstance(key, str) or not key]
  if invalid_keys:
    raise ValueError(f"T-trade trace {field_name} has an invalid key")
  unknown_keys = sorted(set(source) - allowed_keys)
  if unknown_keys:
    raise ValueError(
      f"T-trade trace {field_name} contains unprojectable fields: "
      + ",".join(unknown_keys)
    )


def _t_trade_trace_scalar_marker(
  source: Mapping[str, Any],
  keys: Iterable[str],
  *,
  include_none: bool = False,
  field_name: str = "marker",
) -> Dict[str, Any]:
  """Copy only explicitly allowed, bounded scalar decision facts."""

  marker: Dict[str, Any] = {}
  for key in keys:
    if key not in source:
      continue
    value = _t_trade_trace_bounded_scalar(
      source[key],
      field_name=f"{field_name}.{key}",
    )
    if value is None and not include_none:
      continue
    marker[key] = value
  return marker


def _t_trade_trace_text_marker_list(value: Any, *, field_name: str) -> List[str]:
  """Keep a bounded, order-stable list of auditable reason/tag strings."""

  if value is None:
    return []
  if not isinstance(value, (list, tuple)):
    raise ValueError(f"T-trade trace {field_name} must be a string list")
  if len(value) > _T_TRADE_TRACE_MARKER_LIST_LIMIT:
    raise ValueError(f"T-trade trace {field_name} exceeds list size limit")
  values: List[str] = []
  seen: set[str] = set()
  for index, raw_value in enumerate(value):
    item = _t_trade_trace_bounded_scalar(
      raw_value,
      field_name=f"{field_name}[{index}]",
    )
    if item is None:
      continue
    if not isinstance(item, str):
      raise ValueError(f"T-trade trace {field_name} must contain strings")
    normalized = item.strip()
    if normalized and normalized not in seen:
      seen.add(normalized)
      values.append(normalized)
  return values


def _t_trade_trace_source_identity_marker(
  input_snapshot: StrategyInput,
) -> Dict[str, Any]:
  """Return only source identity not already present in relational columns."""

  context = getattr(input_snapshot, "market_data_context", None)
  if context is None:
    return {}
  raw = {
    "continuity_generation": getattr(context, "continuity_generation", None),
    "source_time_ms": getattr(context, "source_time_ms", None),
    "tick_ordinal": getattr(context, "tick_ordinal", None),
  }
  return _t_trade_trace_scalar_marker(
    raw,
    raw.keys(),
    field_name="source_identity",
  )


def _t_trade_trace_evidence_reference(event: Mapping[str, Any]) -> Dict[str, Any]:
  """Return the authority reference for one known T-trade durable event.

  The event/outbox and opportunity-evaluation relations own the detailed
  payload. The per-Tick decision trace carries only the stable reference that
  permits audit reconstruction, never a second candidate/FSM/signal tree.
  """

  event_type = _t_trade_trace_bounded_scalar(
    event.get("type"),
    field_name="append_event.type",
  )
  if not isinstance(event_type, str) or not event_type:
    raise ValueError("T-trade trace append event requires a type")
  if event_type == T_TRADE_OPPORTUNITY_EVALUATION_EVENT:
    _t_trade_trace_require_allowed_keys(
      event,
      allowed_keys=frozenset(
        {
          "type",
          "event_key",
          "record_kind",
          "event_type",
          "instrument_code",
          "evaluated_at_ms",
          "signal_snapshot",
          "transition",
          "intent_link",
          "external_blockers",
          "metrics",
          "window_started_at_ms",
          "window_ended_at_ms",
          "coalesced_count",
        }
      ),
      field_name="append_event",
    )
    event_key = _t_trade_trace_bounded_scalar(
      event.get("event_key"),
      field_name="append_event.event_key",
    )
    if not isinstance(event_key, str) or not event_key:
      raise ValueError("T-trade opportunity event requires an event_key")
    record_kind = _t_trade_trace_bounded_scalar(
      event.get("record_kind"),
      field_name="append_event.record_kind",
    )
    if record_kind != "MATERIAL":
      raise ValueError("T-trade trace only permits MATERIAL opportunity events")
    snapshot = event.get("signal_snapshot")
    if not isinstance(snapshot, Mapping):
      raise ValueError("T-trade opportunity event requires a signal_snapshot")
    marker = _t_trade_trace_scalar_marker(
      event,
      ("record_kind", "event_type"),
      field_name="append_event",
    )
    marker["evaluation_event_key"] = event_key
    return marker
  if event_type == "T_TRADE_EXTERNAL_ENTRY_IMPORTED":
    _t_trade_trace_require_allowed_keys(
      event,
      allowed_keys=frozenset(
        {
          "type",
          "instrument_code",
          "batch_id",
          "volume",
          "price",
          "source_trade_id",
        }
      ),
      field_name="append_event.external_entry",
    )
    return _t_trade_trace_scalar_marker(
      event,
      (
        "type",
        "batch_id",
        "source_trade_id",
      ),
      field_name="append_event.external_entry",
    )
  if event_type == "T_TRADE_EXIT_POLICY_UPDATED":
    _t_trade_trace_require_allowed_keys(
      event,
      allowed_keys=frozenset(
        {
          "type",
          "instrument_code",
          "batch_id",
          "changed_at",
          "previous_config_version",
          "config_version",
          "previous_policy",
          "policy",
          "previous_time_exit_mode",
          "time_exit_mode",
          "previous_hard_stop_enabled",
          "hard_stop_enabled",
        }
      ),
      field_name="append_event.exit_policy",
    )
    return _t_trade_trace_scalar_marker(
      event,
      (
        "type",
        "batch_id",
        "config_version",
      ),
      field_name="append_event.exit_policy",
    )
  raise ValueError(f"T-trade trace append event type is not projectable: {event_type}")


def _t_trade_trace_state_marker(
  state: Mapping[str, Any],
) -> Dict[str, Any]:
  """Index a material durable mutation without copying evaluation state."""

  _t_trade_trace_require_allowed_keys(
    state,
    allowed_keys=_T_TRADE_INSTRUMENT_STATE_KEYS,
    field_name="state_patch.instrument_state",
  )
  marker: Dict[str, Any] = {}
  execution = _t_trade_trace_scalar_marker(
    state,
    _T_TRADE_EXECUTION_STATE_SCALAR_KEYS,
    field_name="state_patch.instrument_state",
  )
  if execution:
    marker["execution"] = execution
  opportunity = state.get("opportunity")
  if opportunity is None:
    return marker
  opportunity_marker = _t_trade_trace_opportunity_marker(opportunity)
  if opportunity_marker:
    marker["opportunity"] = opportunity_marker
  return marker


def _t_trade_trace_opportunity_marker(opportunity: Any) -> Dict[str, Any]:
  """Project one material opportunity mutation without walking hot roots."""

  if not isinstance(opportunity, Mapping):
    raise ValueError("T-trade instrument opportunity state must be a mapping")
  _t_trade_trace_require_allowed_keys(
    opportunity,
    allowed_keys=_T_TRADE_OPPORTUNITY_STATE_KEYS,
    field_name="state_patch.opportunity",
  )
  candidate = opportunity.get("candidate")
  if candidate is not None:
    if not isinstance(candidate, Mapping):
      raise ValueError("T-trade candidate state must be a mapping")
    _t_trade_trace_require_allowed_keys(
      candidate,
      allowed_keys=_T_TRADE_CANDIDATE_STATE_KEYS,
      field_name="state_patch.opportunity.candidate",
    )
  opportunity_marker = _t_trade_trace_scalar_marker(
    opportunity,
    (
      "state_version",
      "candidate_status",
      "candidate_suppressed",
      "candidate_awaiting_approval",
    ),
    include_none=True,
    field_name="state_patch.opportunity",
  )
  if opportunity_marker:
    return opportunity_marker
  return {}


def _t_trade_trace_awaiting_marker(awaiting: Any) -> Dict[str, Any]:
  """Index the small approval hand-off state used by top-level T strategies."""

  if not isinstance(awaiting, Mapping):
    raise ValueError("T-trade awaiting state must be a mapping")
  _t_trade_trace_require_allowed_keys(
    awaiting,
    allowed_keys=_T_TRADE_TOP_LEVEL_AWAITING_KEYS,
    field_name="state_patch.awaiting",
  )
  if not awaiting:
    return {"cleared": True}
  return _t_trade_trace_scalar_marker(
    awaiting,
    sorted(_T_TRADE_TOP_LEVEL_AWAITING_KEYS),
    field_name="state_patch.awaiting",
  )


def _compact_t_trade_runtime_state_patch_for_audit(
  patch: Any,
  *,
  instrument_code: str,
) -> tuple[Dict[str, Any], List[Dict[str, Any]]]:
  """Return a material mutation index plus authority evidence references.

  Ordinary T ticks update hot observation state only. Their trace index is
  deliberately empty after validation; a material event is the boundary that
  earns a compact durable mutation marker.
  """

  raw_set = getattr(patch, "set", {}) or {}
  raw_unset = getattr(patch, "unset", []) or []
  raw_events = getattr(patch, "append_events", []) or []
  if not isinstance(raw_set, Mapping):
    raise ValueError("T-trade trace patch.set must be a mapping")
  if not isinstance(raw_unset, (list, tuple)):
    raise ValueError("T-trade trace patch.unset must be a list")
  if not isinstance(raw_events, (list, tuple)):
    raise ValueError("T-trade trace patch.append_events must be a list")
  if len(raw_unset) > _T_TRADE_TRACE_MARKER_LIST_LIMIT:
    raise ValueError("T-trade trace patch.unset exceeds list size limit")
  if len(raw_events) > _T_TRADE_TRACE_MARKER_LIST_LIMIT:
    raise ValueError("T-trade trace patch.append_events exceeds list size limit")

  normalized_code = str(instrument_code or "").strip().upper()
  scalar_set: Dict[str, Any] = {}
  current_state_marker: Optional[Dict[str, Any]] = None
  top_level_opportunity_marker: Optional[Dict[str, Any]] = None
  top_level_awaiting_marker: Optional[Dict[str, Any]] = None
  for raw_key, raw_value in raw_set.items():
    if not isinstance(raw_key, str) or not raw_key:
      raise ValueError("T-trade trace patch.set has an invalid key")
    if raw_key not in _T_TRADE_STATE_PATCH_ROOT_KEYS:
      raise ValueError(
        f"T-trade trace patch.set contains unprojectable field: {raw_key}"
      )
    if raw_key == "instrument_states":
      if not isinstance(raw_value, Mapping):
        raise ValueError("T-trade trace instrument_states must be a mapping")
      if normalized_code and normalized_code in raw_value:
        current_state = raw_value[normalized_code]
        if not isinstance(current_state, Mapping):
          raise ValueError("T-trade current instrument state must be a mapping")
        # Validate the recognized state shape even for ordinary ticks. This
        # remains scalar-only and never traverses samples/evaluation roots.
        current_state_marker = _t_trade_trace_state_marker(current_state)
      continue
    if raw_key == "opportunity":
      top_level_opportunity_marker = _t_trade_trace_opportunity_marker(raw_value)
      continue
    if raw_key == "awaiting":
      top_level_awaiting_marker = _t_trade_trace_awaiting_marker(raw_value)
      continue
    scalar_set[raw_key] = _t_trade_trace_bounded_scalar(
      raw_value,
      field_name=f"state_patch.set.{raw_key}",
    )

  unset = []
  for index, raw_key in enumerate(raw_unset):
    value = _t_trade_trace_bounded_scalar(
      raw_key,
      field_name=f"state_patch.unset[{index}]",
    )
    if not isinstance(value, str) or not value:
      raise ValueError("T-trade trace patch.unset requires non-empty strings")
    unset.append(value)
  evidence_references: List[Dict[str, Any]] = []
  for index, raw_event in enumerate(raw_events):
    if not isinstance(raw_event, Mapping):
      raise ValueError(f"T-trade trace append_events[{index}] must be a mapping")
    evidence_references.append(_t_trade_trace_evidence_reference(raw_event))

  # This helper is reached only after the caller has decided that an output is
  # material.  A diagnostic evaluation is forbidden at that boundary rather
  # than being turned into another durable observation format.
  if any(
    reference.get("record_kind") == "COALESCED_DIAGNOSTIC"
    for reference in evidence_references
  ):
    raise ValueError("T-trade ordinary diagnostic must not enter a trace")

  mutation: Dict[str, Any] = {}
  if current_state_marker:
    mutation["current_state"] = current_state_marker
  if top_level_opportunity_marker:
    mutation["opportunity"] = top_level_opportunity_marker
  if top_level_awaiting_marker:
    mutation["awaiting"] = top_level_awaiting_marker
  if scalar_set:
    mutation["set"] = scalar_set
  if unset:
    mutation["unset"] = unset
  return mutation, evidence_references


def _t_trade_evaluation_snapshot_for_trace(
  trace_payload: Mapping[str, Any],
  *,
  patch: Any,
  instrument_code: str,
) -> Optional[Mapping[str, Any]]:
  """Read declared version scalars without serializing an evaluation root."""

  raw_snapshot = trace_payload.get("signal_snapshot")
  if raw_snapshot is not None:
    if not isinstance(raw_snapshot, Mapping):
      raise ValueError("T-trade trace signal_snapshot must be a mapping")
    return raw_snapshot
  if patch is None:
    return None
  raw_set = getattr(patch, "set", {}) or {}
  if not isinstance(raw_set, Mapping):
    raise ValueError("T-trade trace patch.set must be a mapping")
  states = raw_set.get("instrument_states")
  if states is None:
    return None
  if not isinstance(states, Mapping):
    raise ValueError("T-trade trace instrument_states must be a mapping")
  state = states.get(str(instrument_code or "").strip().upper())
  if state is None:
    return None
  if not isinstance(state, Mapping):
    raise ValueError("T-trade current instrument state must be a mapping")
  opportunity = state.get("opportunity")
  if opportunity is None:
    return None
  if not isinstance(opportunity, Mapping):
    raise ValueError("T-trade instrument opportunity state must be a mapping")
  snapshot = opportunity.get("latest_evaluation")
  if snapshot is None:
    return None
  if not isinstance(snapshot, Mapping):
    raise ValueError("T-trade latest_evaluation must be a mapping")
  return snapshot


def _t_trade_trace_versions(
  trace_payload: Mapping[str, Any],
  *,
  patch: Any,
  instrument_code: str,
) -> Dict[str, Any]:
  """Return version facts needed to replay a source identity deterministically."""

  snapshot = _t_trade_evaluation_snapshot_for_trace(
    trace_payload,
    patch=patch,
    instrument_code=instrument_code,
  )
  if snapshot is None:
    return {}
  return _t_trade_trace_scalar_marker(
    snapshot,
    _T_TRADE_EVALUATION_VERSION_KEYS,
    field_name="signal_snapshot.versions",
  )


def _validate_t_trade_trace_payload(trace_payload: Mapping[str, Any]) -> None:
  """Fail closed for undeclared payload roots without copying known evidence."""

  _t_trade_trace_require_allowed_keys(
    trace_payload,
    allowed_keys=_T_TRADE_TRACE_PAYLOAD_KEYS,
    field_name="trace_payload",
  )
  for key in _T_TRADE_TRACE_PAYLOAD_SCALAR_KEYS:
    if key in trace_payload:
      _t_trade_trace_bounded_scalar(
        trace_payload[key],
        field_name=f"trace_payload.{key}",
      )
  for key in ("added", "removed"):
    if key in trace_payload:
      _t_trade_trace_text_marker_list(
        trace_payload[key],
        field_name=f"trace_payload.{key}",
      )
  raw_identity = trace_payload.get("source_identity")
  if raw_identity is not None:
    if not isinstance(raw_identity, Mapping):
      raise ValueError("T-trade trace payload source_identity must be a mapping")
    _t_trade_trace_require_allowed_keys(
      raw_identity,
      allowed_keys=frozenset(
        {"continuity_generation", "source_time_ms", "tick_ordinal"}
      ),
      field_name="trace_payload.source_identity",
    )
    _t_trade_trace_scalar_marker(
      raw_identity,
      ("continuity_generation", "source_time_ms", "tick_ordinal"),
      field_name="trace_payload.source_identity",
    )


def _summarize_t_trade_strategy_input_for_audit(
  input_snapshot: StrategyInput,
) -> Dict[str, Any]:
  """Build a T-trade causal input index without copying input roots."""

  compact = _t_trade_trace_scalar_marker(
    {
      "input_id": getattr(input_snapshot, "input_id", None),
      "cadence": getattr(
        getattr(input_snapshot, "cadence", None),
        "value",
        getattr(input_snapshot, "cadence", None),
      ),
    },
    ("input_id", "cadence"),
    field_name="input_summary",
  )
  source_identity = _t_trade_trace_source_identity_marker(input_snapshot)
  if source_identity:
    compact["source_identity"] = source_identity
  return compact


def _t_trade_trace_reason(
  trace_payload: Mapping[str, Any],
  *,
  trade_intent_count: int,
) -> str:
  """Return the one durable reason field for the immutable trace header."""

  raw_reason = trace_payload.get("reason")
  if raw_reason is not None:
    reason = _t_trade_trace_bounded_scalar(
      raw_reason,
      field_name="trace_payload.reason",
    )
    if not isinstance(reason, str):
      raise ValueError("T-trade trace reason must be a string")
    if reason:
      return reason
  return "TRADE_INTENT_GENERATED" if trade_intent_count else "NO_TRADE_INTENT"


def _summarize_t_trade_intent_for_audit(intent: TradeIntent) -> Dict[str, Any]:
  """Keep the causal intent fields and reference the authoritative intent row.

  The intent lifecycle owns its complete immutable metadata.  A trace carries
  its stable intent ID plus decision-relevant scalar metadata only, never an
  embedded exit-plan template or arbitrary metadata tree.
  """

  raw_intent_id = _t_trade_trace_bounded_scalar(
    getattr(intent, "intent_id", None),
    field_name="trade_intent.intent_id",
  )
  if not isinstance(raw_intent_id, str) or not raw_intent_id:
    raise ValueError("T-trade trace intent requires an intent_id")
  raw = {
    "direction": getattr(intent, "direction", None),
    "bucket": getattr(intent, "bucket", None),
    "reason": getattr(intent, "reason", None),
    "priority": getattr(intent, "priority", None),
    "target_amount": getattr(intent, "target_amount", None),
    "target_position_pct": getattr(intent, "target_position_pct", None),
    "target_volume": getattr(intent, "target_volume", None),
    "limit_price_hint": getattr(intent, "limit_price_hint", None),
    "execution_mode": getattr(intent, "execution_mode", None),
    "approval_ttl_ms": getattr(intent, "approval_ttl_ms", None),
    "max_price_deviation_bps": getattr(intent, "max_price_deviation_bps", None),
  }
  summary = {"intent_id": raw_intent_id}
  summary.update(
    _t_trade_trace_scalar_marker(
      raw,
      raw.keys(),
      field_name="trade_intent",
    )
  )
  metadata = getattr(intent, "metadata", None) or {}
  if not isinstance(metadata, Mapping):
    raise ValueError("T-trade trace intent metadata must be a mapping")
  invalid_metadata_keys = [
    key for key in metadata if not isinstance(key, str) or not key
  ]
  if invalid_metadata_keys:
    raise ValueError("T-trade trace intent metadata has an invalid key")
  unknown_metadata_keys = sorted(
    set(metadata)
    - _T_TRADE_INTENT_METADATA_SCALAR_KEYS
    - _T_TRADE_INTENT_METADATA_REFERENCE_KEYS
  )
  if unknown_metadata_keys:
    raise ValueError(
      "T-trade trace intent metadata contains unprojectable fields: "
      + ",".join(unknown_metadata_keys)
    )
  for key in _T_TRADE_INTENT_METADATA_REFERENCE_KEYS:
    if key in metadata and metadata[key] is not None and not isinstance(
      metadata[key], Mapping
    ):
      raise ValueError(f"T-trade trace intent metadata.{key} must be a mapping")
  metadata_marker = _t_trade_trace_scalar_marker(
    metadata,
    sorted(_T_TRADE_INTENT_METADATA_SCALAR_KEYS),
    field_name="trade_intent.metadata",
  )
  if metadata_marker:
    summary["metadata"] = metadata_marker
  return summary


def _build_t_trade_decision_trace_projection(
  *,
  input_snapshot: StrategyInput,
  output: StrategyOutput,
) -> Dict[str, Any]:
  """Build the complete minimal causal index for one T-trade output.

  The generic path intentionally remains unchanged. This branch must never
  call generic input/intent summarizers or content-address arbitrary roots:
  those operations would serialize repeated hot state on every Tick.
  """

  raw_payload = output.trace_payload or {}
  if not isinstance(raw_payload, Mapping):
    raise ValueError("T-trade trace payload must be a mapping")
  _t_trade_trace_require_allowed_keys(
    raw_payload,
    allowed_keys=_T_TRADE_TRACE_PAYLOAD_KEYS,
    field_name="trace_payload",
  )
  raw_tags = output.decision_tags or []
  tags = _t_trade_trace_text_marker_list(
    raw_tags,
    field_name="decision_tags",
  )
  raw_intents = output.trade_intents or []
  if not isinstance(raw_intents, (list, tuple)):
    raise ValueError("T-trade trace trade_intents must be a list")
  if len(raw_intents) > _T_TRADE_TRACE_MARKER_LIST_LIMIT:
    raise ValueError("T-trade trace trade_intents exceeds list size limit")
  intents = [
    _summarize_t_trade_intent_for_audit(intent)
    for intent in raw_intents
  ]
  state_patch, evidence_references = (
    _compact_t_trade_runtime_state_patch_for_audit(
      output.runtime_state_patch,
      instrument_code=input_snapshot.instrument_code,
    )
    if output.runtime_state_patch
    else ({}, [])
  )
  versions = _t_trade_trace_versions(
    raw_payload,
    patch=output.runtime_state_patch,
    instrument_code=input_snapshot.instrument_code,
  )
  input_summary = _summarize_t_trade_strategy_input_for_audit(input_snapshot)
  if versions:
    input_summary["versions"] = versions
  output_summary: Dict[str, Any] = {
    "format": _T_TRADE_TRACE_PROJECTION_FORMAT,
    "record_kind": "MATERIAL",
  }
  if evidence_references:
    output_summary["evaluation_references"] = evidence_references
  return {
    "input_summary": input_summary,
    # Detailed roots are materialized by T opportunity evidence, not copied
    # into the supplemental decision-trace JSON for every Tick.
    "environment": {},
    "risk_caps": {},
    "position_profile": {},
    "execution_profile": {},
    "output_summary": output_summary,
    "state_patch": state_patch,
    "trade_intents": intents,
    "tags": ["strategy_output", *tags],
    "reason": _t_trade_trace_reason(
      raw_payload,
      trade_intent_count=len(intents),
    ),
  }


def _t_trade_output_requires_material_trace(output: StrategyOutput) -> bool:
  """Return whether a T output has a durable fact that earns one trace row.

  Ordinary observations deliberately have no substitute trace/outbox payload:
  their audit path is the authoritative market source identity, processed
  checkpoint watermark, and versioned deterministic replay.  Unknown event
  shapes are rejected so a plugin cannot silently reintroduce a large durable
  per-Tick tree.
  """

  intents = output.trade_intents or []
  if intents:
    return True
  if output.exit_plan_commands:
    return True
  patch = output.runtime_state_patch
  if patch is None:
    return False
  events = getattr(patch, "append_events", []) or []
  if not isinstance(events, (list, tuple)):
    raise ValueError("T-trade trace patch.append_events must be a list")
  for raw_event in events:
    if not isinstance(raw_event, Mapping):
      raise ValueError("T-trade trace append event must be a mapping")
    event_type = str(raw_event.get("type") or "").strip()
    if event_type == T_TRADE_OPPORTUNITY_EVALUATION_EVENT:
      record_kind = str(raw_event.get("record_kind") or "").upper()
      if record_kind == "MATERIAL":
        return True
      if record_kind == "COALESCED_DIAGNOSTIC":
        continue
      raise ValueError("T-trade opportunity event requires MATERIAL record_kind")
    if event_type in {
      "T_TRADE_EXTERNAL_ENTRY_IMPORTED",
      "T_TRADE_EXIT_POLICY_UPDATED",
    }:
      return True
    raise ValueError(
      "T-trade durable event type is not a material fact: " + (event_type or "-")
    )
  return False


class RuntimeConsumerUnavailable(RuntimeError):
  """A durable report cannot currently reach a live serial consumer."""


class _PendingApprovalStatusPersistenceError(RuntimeError):
  """A manual intent terminal status did not reach its durable truth."""


class ExecutionStatus(Enum):
  """执行状态"""

  PENDING = "PENDING"
  STARTING = "STARTING"
  RUNNING = "RUNNING"
  STOPPING = "STOPPING"
  STOPPED = "STOPPED"
  COMPLETED = "COMPLETED"
  ERROR = "ERROR"
  PAUSED = "PAUSED"


@dataclass(frozen=True)
class RuntimeMarketEvent:
  """A discardable market event with queue-age provenance."""

  event_type: str
  data: Any
  enqueued_at: float


@dataclass
class StrategyRuntime:
  """策略运行时对象"""

  #: 运行实例ID
  run_id: str
  #: 运行实例名称
  name: str
  #: 策略模板ID
  strategy_id: int
  #: 策略类
  strategy_class: Type[StrategyBase]
  #: 运行上下文（参数、模式、标的、时间范围）
  context: StrategyContext
  #: 策略实例
  strategy: Optional[StrategyBase] = None
  #: Broker 实例
  broker: Optional[BrokerBase] = None
  #: 数据适配器
  data_adapter: Optional[DataAdapter] = None
  #: 市场数据管理器（统一订阅与历史查询）
  market_data_manager: Optional["MarketDataManager"] = None
  performance_recorder: Optional[StrategyPerformanceRecorder] = None
  #: 当前执行状态
  status: ExecutionStatus = ExecutionStatus.PENDING
  #: 运行指标
  metrics: Optional[ExecutionMetrics] = None
  #: 错误信息
  error_message: Optional[str] = None
  #: 运行主任务
  task: Optional[asyncio.Task] = None
  #: 串行事件处理任务；回测撮合后用它完成订单/成交回报 barrier。
  event_task: Optional[asyncio.Task] = field(default=None, repr=False)
  #: 不可丢弃的控制、委托与成交事件队列。
  event_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
  #: 可丢弃但有界的实时行情队列；不得承载控制或券商回报。
  market_event_queue: asyncio.Queue = field(
    default_factory=lambda: asyncio.Queue(maxsize=_RUNTIME_MARKET_EVENT_QUEUE_CAPACITY),
    repr=False,
  )
  _event_queue_wakeup: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
  _pending_market_invalidations: Dict[str, str] = field(
    default_factory=dict,
    repr=False,
  )
  _active_market_continuity_losses: Dict[str, str] = field(
    default_factory=dict,
    repr=False,
  )
  _market_fail_closed_codes: Dict[str, str] = field(
    default_factory=dict,
    repr=False,
  )
  _market_continuity_generations: Dict[str, int] = field(
    default_factory=dict,
    repr=False,
  )
  #: Last authority identity received from WholeQuoteHub. Routing epochs above
  #: remain process-local guards; strategy contexts use this transport truth.
  _market_transport_generation: int = field(default=0, repr=False)
  _market_transport_stream_id: str = field(default="", repr=False)
  _market_transport_sequences: Dict[str, int] = field(
    default_factory=dict,
    repr=False,
  )
  _market_transport_reset_token: str = field(default="", repr=False)
  _restored_market_windows_unverified: set[str] = field(
    default_factory=set,
    repr=False,
  )
  _processing_market_events: Dict[str, tuple[int, float]] = field(
    default_factory=dict,
    repr=False,
  )
  _market_invalidation_checkpoints: Dict[str, int] = field(
    default_factory=dict,
    repr=False,
  )
  _handled_market_invalidations: Dict[str, int] = field(
    default_factory=dict,
    repr=False,
  )
  market_events_enqueued: int = 0
  market_events_processed: int = 0
  market_events_dropped: int = 0
  market_events_expired: int = 0
  market_tick_source_rejections: int = 0
  market_event_overflows: int = 0
  market_window_invalidations: int = 0
  market_queue_high_watermark: int = 0
  #: Last successfully processed WholeQuoteHub authority watermark.  Its
  #: per-instrument entries are audit data only; session completeness is proved
  #: against the hub's global stream/generation/sequence fence.
  _checkpoint_processed_watermark: Dict[str, Any] = field(
    default_factory=dict,
    repr=False,
  )
  _checkpoint_instrument_watermarks: Dict[str, Dict[str, Any]] = field(
    default_factory=dict,
    repr=False,
  )
  #: In-memory-only coalesced diagnostics.  These never create a hot-path DB
  #: write; a complete session/day/terminal seal supplies them as one batch.
  _checkpoint_diagnostic_summaries: Dict[str, Dict[str, Any]] = field(
    default_factory=dict,
    repr=False,
  )
  _checkpoint_virtual_trade_date: Optional[date] = field(default=None, repr=False)
  _checkpoint_virtual_sequence: int = field(default=0, repr=False)
  #: Exact accepted market-event count for the current runtime generation.
  #: This is persisted next to the authority watermark at checkpoint seals;
  #: it is not derived from DecisionTrace/evaluation row counts.
  _checkpoint_processed_tick_count: int = field(default=0, repr=False)
  #: Publicly observable coordinator state keyed by ``YYYY-MM-DD:AM|PM|TERMINAL``
  #: or ``YYYY-MM-DD:DAY``.  It deliberately remains process-local until a
  #: manager-owned complete checkpoint has been sealed.
  checkpoint_status: Dict[str, Dict[str, Any]] = field(
    default_factory=dict,
    repr=False,
  )
  #: Durable report whose effects exist only in memory until checkpoint retry.
  durable_event_barrier_key: Optional[str] = field(default=None, repr=False)
  #: DB-backlog barriers are released only after every event row is APPLIED.
  durable_startup_barrier: bool = field(default=False, repr=False)
  #: 仅 BACKTEST 使用的运行实例局部历史时钟。
  replay_clock: Optional[ReplayClock] = field(default=None, repr=False)
  #: 运行进程ID
  pid: int = field(default_factory=os.getpid)
  #: 运行主机名
  host: str = field(
    default_factory=lambda: (
      os.uname().nodename
      if os.name != "nt"
      else os.environ.get("COMPUTERNAME", "unknown")
    )
  )

  # === 订阅广播相关字段 ===
  #: 回测模式数据广播节流间隔（毫秒）
  broadcast_throttle_ms: int = 100
  #: 上次广播时间戳（用于节流）
  _last_broadcast_time: Optional[datetime] = field(default=None, repr=False)
  #: 状态管理器（用于持久化日志、持仓、订单等）
  state_manager: Optional["RuntimeStateManager"] = field(default=None, repr=False)
  #: 日志管理器（统一日志缓存与订阅）
  log_manager: Optional["RuntimeLogManager"] = field(default=None, repr=False)
  #: 最新行情快照（用于下单风控和回测撮合）
  latest_market_data: Dict[str, MarketDataSnapshot] = field(
    default_factory=dict, repr=False
  )
  #: 最近订单回报时间（用于 broker 健康状态）
  last_order_report_at: Optional[datetime] = field(default=None, repr=False)
  #: 最近成交回报时间（用于 broker 健康状态）
  last_trade_report_at: Optional[datetime] = field(default=None, repr=False)
  #: 最近任意 broker 回报时间（用于 broker 健康状态）
  last_broker_report_at: Optional[datetime] = field(default=None, repr=False)
  #: 等待人工确认的交易意图，仅在运行进程内保留完整对象
  pending_approvals: Dict[str, TradeIntent] = field(default_factory=dict, repr=False)
  #: 审批串行锁，避免重复点击导致重复下单
  approval_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
  #: 实时订阅按标的归组，支持运行中动态增删标的。
  realtime_subscription_ids: Dict[str, List[str]] = field(
    default_factory=dict, repr=False
  )
  #: 动态订阅变更锁，避免持仓同步与停机清理互相穿透。
  realtime_subscription_lock: asyncio.Lock = field(
    default_factory=asyncio.Lock, repr=False
  )
  #: Serializes start/stop ownership without reporting in-flight work as success.
  lifecycle_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
  _lifecycle_operation_task: Optional[asyncio.Task] = field(
    default=None,
    repr=False,
  )
  _lifecycle_operation_kind: Optional[str] = field(default=None, repr=False)
  #: AdapterManager reference owned by this runtime. It must be released once.
  _adapter_ref_acquired: bool = field(default=False, repr=False)
  #: Failed startup and terminal-error cleanup have different snapshot semantics.
  _startup_abort_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
  _startup_abort_complete: bool = field(default=False, repr=False)
  _startup_abort_task: Optional[asyncio.Task] = field(default=None, repr=False)
  _terminal_cleanup_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
  _terminal_cleanup_complete: bool = field(default=False, repr=False)
  _terminal_cleanup_task: Optional[asyncio.Task] = field(default=None, repr=False)
  #: 已确认但尚未完全由成交持仓接管的 T 入场金额预留。
  t_trade_entry_reservations: Dict[str, Dict[str, Any]] = field(
    default_factory=dict, repr=False
  )
  #: Engine-owned automatic exit plans, persisted outside strategy-owned state.
  exit_plan_book: ExitPlanBook = field(default_factory=ExitPlanBook, repr=False)
  _last_replay_projection_at: float = field(default=0.0, repr=False)
  _last_replay_progress_pct: float = field(default=0.0, repr=False)
  _last_t_trade_replay_projection_trade_date: Optional[str] = field(
    default=None,
    repr=False,
  )
  #: Engine-owned, bounded point-in-time eligibility authority for V3 entry
  #: emission.  Values are stamped with the runtime/account scope so a stale
  #: or accidentally shared snapshot can never authorize a Tick.
  t_trade_intent_emission_by_instrument: Dict[str, Dict[str, Any]] = field(
    default_factory=dict,
    repr=False,
  )
  #: Strict T-trade replay-only shadow evidence; never strategy input.
  t_trade_phase_one_baseline: Optional[TTradePhaseOneBaselineAccumulator] = field(
    default=None,
    repr=False,
  )
  #: Point-in-time profiles are loaded once per instrument and source trade day.
  _t_trade_opportunity_profiles: Dict[tuple[str, str], Optional[Dict[str, Any]]] = (
    field(default_factory=dict, repr=False)
  )
  _t_trade_opportunity_profile_errors: Dict[tuple[str, str], str] = field(
    default_factory=dict,
    repr=False,
  )
  _t_trade_opportunity_profile_retry_after: Dict[tuple[str, str], float] = field(
    default_factory=dict,
    repr=False,
  )
  _t_trade_opportunity_failures: Dict[str, Dict[str, Any]] = field(
    default_factory=dict,
    repr=False,
  )

  @property
  def mode(self) -> StrategyRunMode:
    """获取运行模式"""
    return self.context.mode

  @property
  def instruments(self) -> List[str]:
    """便捷访问标的列表"""
    return self.context.instruments

  @property
  def parameters(self) -> Dict[str, Any]:
    """便捷访问策略参数"""
    return self.context.parameters

  @property
  def start_time(self) -> Optional[datetime]:
    """运行实例开始时间"""
    return self.metrics.start_time if self.metrics else None

  @property
  def stop_time(self) -> Optional[datetime]:
    """运行实例结束时间"""
    return self.metrics.end_time if self.metrics else None

  def get_metrics(self) -> Dict[str, Any]:
    """Return JSON-serializable runtime metrics for persistence."""
    if self.metrics is None:
      return {}

    self.metrics.end_time = self.metrics.end_time or time_utils.now()
    if self.broker and hasattr(self.broker, "get_performance_metrics"):
      perf_metrics = self.broker.get_performance_metrics()
      if isinstance(perf_metrics, dict):
        self.metrics.performance = perf_metrics
        self.metrics.max_drawdown = perf_metrics.get("max_drawdown", 0.0)
        self.metrics.max_drawdown_pct = perf_metrics.get("max_drawdown_pct", 0.0)
        self.metrics.win_rate = perf_metrics.get("win_rate", 0.0)
        self.metrics.win_rate_pct = perf_metrics.get("win_rate_pct", 0.0)
        self.metrics.sharpe_ratio = perf_metrics.get("sharpe_ratio", 0.0)
        self.metrics.total_return_pct = perf_metrics.get("total_return_pct", 0.0)
        self.metrics.total_pnl = (
          perf_metrics.get("final_equity", self.metrics.initial_capital)
          - self.metrics.initial_capital
        )
        self.metrics.current_capital = perf_metrics.get(
          "final_equity", self.metrics.initial_capital
        )
        self.metrics.trades_executed = perf_metrics.get("total_trades", 0)

    return self.metrics.model_dump(mode="json")

  def should_broadcast_data(self) -> bool:
    """判断是否应该广播数据（用于节流）"""
    # 实时模式不节流
    if self.context.mode != StrategyRunMode.BACKTEST:
      return True

    # 回测模式：检查节流间隔
    now = time_utils.now()
    if self._last_broadcast_time is None:
      return True

    elapsed_ms = (now - self._last_broadcast_time).total_seconds() * 1000
    return elapsed_ms >= self.broadcast_throttle_ms

  def subscribe_data(
    self, data_type: str = "all", *, include_recent: bool = True
  ) -> asyncio.Queue:
    """订阅市场数据，返回一个独立的队列

    Args:
        data_type: 订阅类型，"tick", "kline", 或 "all"
        include_recent: 是否推送最近缓存的数据

    Returns:
        订阅者专属队列
    """
    if not self.market_data_manager:
      return asyncio.Queue(maxsize=1000)
    return self.market_data_manager.subscribe(
      run_id=self.run_id,
      data_type=data_type,
      maxsize=1000,
      include_recent=include_recent,
    )

  def unsubscribe_data(self, queue: asyncio.Queue) -> None:
    """取消市场数据订阅"""
    if not self.market_data_manager:
      return
    self.market_data_manager.unsubscribe(run_id=self.run_id, queue=queue)

  def subscribe_logs(self, include_history: bool = True) -> asyncio.Queue:
    """订阅日志，返回一个独立的队列"""
    if not self.log_manager:
      return asyncio.Queue(maxsize=500)
    return self.log_manager.subscribe(
      run_id=self.run_id,
      maxsize=500,
      include_history=include_history,
    )

  def unsubscribe_logs(self, queue: asyncio.Queue) -> None:
    """取消日志订阅"""
    if not self.log_manager:
      return
    self.log_manager.unsubscribe(run_id=self.run_id, queue=queue)

  def broadcast_tick(self, tick) -> None:
    """广播 Tick 数据到所有订阅者"""
    if not self.should_broadcast_data():
      return

    self._last_broadcast_time = time_utils.now()
    # 广播到所有订阅了 tick 或 all 的订阅者
    if self.market_data_manager:
      self.market_data_manager.publish_tick(self.run_id, tick)

  def broadcast_kline(self, kline) -> None:
    """广播 K线 数据到所有订阅者"""
    if not self.should_broadcast_data():
      return

    self._last_broadcast_time = time_utils.now()
    # 广播到所有订阅了 kline 或 all 的订阅者
    if self.market_data_manager:
      self.market_data_manager.publish_kline(self.run_id, kline)

  def broadcast_log(self, level: str, message: str, source: str = "strategy") -> None:
    """广播日志到所有订阅者"""
    if not self.log_manager:
      return
    self.log_manager.append(
      run_id=self.run_id,
      level=level,
      message=message,
      source=source,
    )


@dataclass
class ExecutionContextSnapshot:
  """Executor-built domain context shared by strategy input and order routing."""

  account: Dict[str, Any]
  positions: Dict[str, Any]
  bucket_ledger: Dict[str, Any]
  portfolio_state: Dict[str, Any]
  open_orders: List[Dict[str, Any]]
  market_context: Dict[str, Any]
  risk_caps: Dict[str, Any]
  position_profile: Dict[str, Any]
  runtime_state: Dict[str, Any]
  parameters: Dict[str, Any]


class StrategyExecutor:
  """
  策略执行器 - 专注于策略运行的并发执行和资源管理

  职责：
  - 创建和管理策略运行实例
  - 并发执行控制（线程池）
  - 资源分配和回收（Broker、DataAdapter）
  - 实时状态监控和心跳
  - 异常处理和恢复

  特点：
  - 可创建多个 Executor 实例
  - 支持资源隔离
  - 不负责持久化（由调用方处理）
  """

  def __init__(
    self,
    max_workers: int = 10,
    *,
    exit_strategy_registry: Optional[ExitStrategyRegistry] = None,
    opportunity_runtime_service: Optional[TTradeOpportunityRuntimeService] = None,
    opportunity_update_service: Optional[TTradeMonitorProjectionService] = None,
    opportunity_diagnostics_service: Optional[TTradeSignalDiagnosticsService] = None,
    candidate_outcome_facade: Optional[TTradeCandidateOutcomePersistenceFacade] = None,
    opportunity_observability: Optional[TTradeRuntimeObservability] = None,
  ):
    """
    初始化策略执行器

    Args:
        max_workers: 最大并发执行数量
    """
    self.max_workers = max_workers
    self.runs: Dict[str, StrategyRuntime] = {}
    self.thread_pool = ThreadPoolExecutor(max_workers=max_workers)
    self.logger = logging.getLogger("StrategyExecutor")
    self._shutdown_event = asyncio.Event()
    self._trading_date_helper = TradingDateHelper()
    self.log_manager = RuntimeLogManager()
    self.market_data_manager = MarketDataManager()
    self.exit_strategy_registry = (
      exit_strategy_registry or ExitStrategyRegistry.builtins()
    )
    self.opportunity_runtime_service = (
      opportunity_runtime_service or t_trade_opportunity_runtime_service
    )
    # Application use cases own the V3 boundaries.  The concrete runtime
    # service remains the adapter, while this composition root supplies the
    # Shanghai-causal profile read, post-CAS materialization and external
    # emission gate used by the existing Engine path.
    self._d1_profile_reader = ReadD1ReferenceProfile(
      self.opportunity_runtime_service
    )
    self._evaluation_materializer = MaterializeEvaluationAfterCAS(
      self.opportunity_runtime_service
    )
    self._intent_emission_gate = EvaluateIntentEmissionGate()
    self.opportunity_update_service = (
      opportunity_update_service or t_trade_monitor_projection_service
    )
    self.opportunity_diagnostics_service = (
      opportunity_diagnostics_service or TTradeSignalDiagnosticsService()
    )
    self.candidate_outcome_facade = candidate_outcome_facade
    self._candidate_outcome_activity: Dict[tuple[str, str], bool] = {}
    self._candidate_outcome_reconciled_runs: set[str] = set()
    self._candidate_outcome_repair_attempts: Dict[str, int] = {}
    self._candidate_outcome_repair_retry_at_ms: Dict[str, int] = {}
    self.opportunity_observability = (
      opportunity_observability or t_trade_runtime_observability
    )

  def register_exit_strategy(self, strategy: str, evaluator: Any) -> None:
    """Register a sell trigger once for new and restored runtime plans."""

    self.exit_strategy_registry.register(strategy, evaluator)

  def _runtime_log(
    self,
    runtime: StrategyRuntime,
    level: str,
    message: str,
    source: str = "executor",
  ) -> None:
    """Write a run-scoped execution log and mirror it to the executor logger."""
    normalized_level = str(level or "INFO").upper()
    logger_message = f"[{runtime.run_id}] {message}"
    if normalized_level == "ERROR":
      self.logger.error(logger_message)
    elif normalized_level == "WARNING":
      self.logger.warning(logger_message)
    elif normalized_level == "DEBUG":
      self.logger.debug(logger_message)
    else:
      self.logger.info(logger_message)

    try:
      runtime.broadcast_log(normalized_level, message, source=source)
    except Exception as exc:
      self.logger.debug("写入运行执行日志失败: %s", exc)

  def create(
    self,
    run_id: str,
    name: Optional[str] = None,
    strategy_id: Optional[int] = None,
    strategy_class: Optional[Type[StrategyBase]] = None,
    context: Optional[StrategyContext] = None,
  ) -> StrategyRuntime:
    """
    创建策略运行实例（纯内存操作）

    Args:
        run_id: 运行实例ID（由调用方生成）
        strategy_id: 策略模板ID
        strategy_class: 策略类
        context: 策略上下文

    Returns:
        StrategyRuntime: 运行时对象

    Note:
        - 不负责参数验证（由 StrategyManager 完成）
        - 不负责持久化（由 StrategyManager 完成）
        - 仅创建运行时对象并加入管理
    """
    if strategy_id is None or strategy_class is None or context is None:
      raise TypeError("strategy_id, strategy_class and context are required")

    runtime_name = name or f"Strategy-{strategy_id}"

    # 创建策略运行时对象
    strategy_runtime = StrategyRuntime(
      run_id=run_id,
      name=runtime_name,
      strategy_id=strategy_id,
      strategy_class=strategy_class,
      context=context,
      metrics=ExecutionMetrics(
        start_time=time_utils.now(),
        last_heartbeat=time_utils.now(),
        initial_capital=context.initial_capital,
        current_capital=context.initial_capital,
      ),
      log_manager=self.log_manager,
      market_data_manager=self.market_data_manager,
    )
    strategy_runtime.exit_plan_book = ExitPlanBook(
      evaluator=ExitPlanEvaluator(self.exit_strategy_registry)
    )

    self.runs[run_id] = strategy_runtime

    self.logger.info(f"创建策略运行时: {run_id}")
    return strategy_runtime

  async def _run_lifecycle_operation(
    self,
    runtime: StrategyRuntime,
    operation: str,
    operation_factory,
  ) -> bool:
    """Run one transition and make duplicate callers await its exact result."""

    waiter_task = asyncio.current_task()
    if waiter_task is None:
      raise RuntimeError("策略生命周期操作必须运行在 asyncio Task 中")

    while True:
      async with runtime.lifecycle_lock:
        existing_task = runtime._lifecycle_operation_task
        existing_kind = runtime._lifecycle_operation_kind
        if existing_task is None or existing_task.done():
          operation_task = asyncio.create_task(
            operation_factory(),
            name=f"strategy-{operation}:{runtime.run_id}",
          )
          runtime._lifecycle_operation_task = operation_task
          runtime._lifecycle_operation_kind = operation
          owns_operation = True
          same_operation = True
        else:
          operation_task = existing_task
          owns_operation = False
          same_operation = existing_kind == operation

      try:
        result = (
          await operation_task
          if owns_operation
          else await asyncio.shield(operation_task)
        )
      except asyncio.CancelledError:
        if waiter_task.cancelling():
          raise
        if same_operation:
          return False
        continue
      except Exception:
        if same_operation:
          return False
        continue
      if same_operation:
        return bool(result)

  @staticmethod
  def _reset_runtime_generation_transients(runtime: StrategyRuntime) -> None:
    """Drop process-local state; durable restore is the only restart source."""

    runtime.pending_approvals.clear()
    runtime.t_trade_entry_reservations.clear()
    runtime.latest_market_data.clear()
    runtime.realtime_subscription_ids.clear()
    runtime.durable_event_barrier_key = None
    runtime.durable_startup_barrier = False
    runtime.last_order_report_at = None
    runtime.last_trade_report_at = None
    runtime.last_broker_report_at = None
    runtime._pending_market_invalidations.clear()
    runtime._active_market_continuity_losses.clear()
    runtime._market_fail_closed_codes.clear()
    runtime._market_continuity_generations.clear()
    runtime._market_transport_generation = 0
    runtime._market_transport_stream_id = ""
    runtime._market_transport_sequences.clear()
    runtime._market_transport_reset_token = ""
    runtime._restored_market_windows_unverified.clear()
    runtime._processing_market_events.clear()
    runtime._market_invalidation_checkpoints.clear()
    runtime._handled_market_invalidations.clear()
    runtime._checkpoint_processed_watermark.clear()
    runtime._checkpoint_instrument_watermarks.clear()
    runtime._checkpoint_diagnostic_summaries.clear()
    runtime._checkpoint_virtual_trade_date = None
    runtime._checkpoint_virtual_sequence = 0
    runtime._checkpoint_processed_tick_count = 0
    runtime.checkpoint_status.clear()
    runtime._t_trade_opportunity_profiles.clear()
    runtime._t_trade_opportunity_profile_errors.clear()
    runtime._t_trade_opportunity_profile_retry_after.clear()
    runtime._t_trade_opportunity_failures.clear()
    runtime.t_trade_intent_emission_by_instrument.clear()
    runtime.t_trade_phase_one_baseline = None
    runtime.event_queue = asyncio.Queue()
    runtime.market_event_queue = asyncio.Queue(
      maxsize=_RUNTIME_MARKET_EVENT_QUEUE_CAPACITY
    )
    runtime._event_queue_wakeup = asyncio.Event()
    for attribute in ("account_info", "positions"):
      if hasattr(runtime.context, attribute):
        delattr(runtime.context, attribute)

  @staticmethod
  def _restored_causal_market_window_codes(runtime: StrategyRuntime) -> set[str]:
    """Return restored V3 windows whose live transport continuity is unknown."""

    if runtime.context.mode == StrategyRunMode.BACKTEST or runtime.strategy is None:
      return set()
    raw_states = runtime.strategy.state.get("instrument_states", {})
    states = dict(raw_states) if isinstance(raw_states, Mapping) else {}
    restored: set[str] = set()
    for raw_code, raw_state in states.items():
      state = dict(raw_state) if isinstance(raw_state, Mapping) else {}
      opportunity = dict(state.get("opportunity") or {})
      compact_window_requires_rewarm = (
        opportunity.get("sample_window_persisted") is False
        or opportunity.get("sample_window_restore_required") is True
      )
      if (
        opportunity.get("samples")
        or opportunity.get("candidate")
        or compact_window_requires_rewarm
      ):
        code = str(raw_code or "").strip().upper()
        if code:
          restored.add(code)
    return restored

  def _replay_restored_market_continuity_gates(
    self,
    runtime: StrategyRuntime,
  ) -> None:
    """Rebuild strategy windows before clearing any restored durable gate."""

    state_manager = runtime.state_manager
    strategy = runtime.strategy
    get_gates = getattr(state_manager, "market_continuity_reconciliation", None)
    if not callable(get_gates):
      return
    restored_gates = {
      str(code or "").strip().upper(): str(reason or "MARKET_DATA_CONTINUITY_LOST")
      for code, reason in dict(get_gates() or {}).items()
      if str(code or "").strip()
    }
    if not restored_gates:
      return

    invalidate = getattr(strategy, "invalidate_realtime_market_window", None)
    clear_gate = getattr(
      state_manager,
      "clear_market_continuity_reconciliation",
      None,
    )
    for code, reason in restored_gates.items():
      handled = False
      try:
        handled = bool(
          invalidate(code, reason=reason) if callable(invalidate) else False
        )
      except Exception:
        self.logger.exception(
          "恢复行情连续性门禁时策略失效钩子失败: run_id=%s instrument=%s",
          runtime.run_id,
          code,
        )
      if not handled:
        runtime._market_fail_closed_codes[code] = reason
        continue

      # No snapshot task exists during startup.  Merge the invalidated window
      # first, then clear the manager-owned gate; the first startup checkpoint
      # persists both changes atomically before RUNNING.
      self._checkpoint_restored_strategy_state(runtime)
      if callable(clear_gate):
        clear_gate(code)

  @staticmethod
  def _runtime_state_persistence_enabled(runtime: StrategyRuntime) -> bool:
    """Enable persistence only for one of the two authoritative policies."""

    return StrategyExecutor._runtime_state_checkpoint_policy(runtime) in {
      RUNTIME_STATE_CHECKPOINT_POLICY_DAY_BATCH,
      RUNTIME_STATE_CHECKPOINT_POLICY_SESSION_BOUNDARY,
    }

  @staticmethod
  def _runtime_state_checkpoint_policy(runtime: StrategyRuntime) -> str:
    """Return the sole authoritative hot-state persistence policy for a run."""

    if runtime.context.mode == StrategyRunMode.BACKTEST:
      return RUNTIME_STATE_CHECKPOINT_POLICY_DAY_BATCH
    if runtime.context.mode in {StrategyRunMode.PAPER, StrategyRunMode.LIVE}:
      return RUNTIME_STATE_CHECKPOINT_POLICY_SESSION_BOUNDARY
    return ""

  @staticmethod
  def _requires_startup_runtime_state_checkpoint(runtime: StrategyRuntime) -> bool:
    """Return the modes that must durably checkpoint before their loop starts."""

    return StrategyExecutor._runtime_state_persistence_enabled(runtime)

  @staticmethod
  def _checkpoint_status_key(
    trade_date: date,
    session: Optional[str],
  ) -> str:
    return f"{trade_date.isoformat()}:{session or 'DAY'}"

  @staticmethod
  def _checkpoint_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
      return value
    if isinstance(value, str):
      try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
      except ValueError:
        return None
    return None

  @staticmethod
  def _checkpoint_local_time(value: datetime) -> datetime:
    return time_utils.to_shanghai(value) if value.tzinfo else value

  def _set_checkpoint_status(
    self,
    runtime: StrategyRuntime,
    *,
    trade_date: date,
    session: Optional[str],
    status: str,
    reason: str,
    attempts: Optional[int] = None,
    **details: Any,
  ) -> Dict[str, Any]:
    key = self._checkpoint_status_key(trade_date, session)
    previous = dict(runtime.checkpoint_status.get(key) or {})
    record = {
      "policy": self._runtime_state_checkpoint_policy(runtime),
      "trade_date": trade_date.isoformat(),
      "session": session,
      "status": status,
      "reason": reason,
      "attempts": int(
        attempts if attempts is not None else previous.get("attempts", 0) or 0
      ),
      "updated_at": time_utils.now().isoformat(),
      **details,
    }
    if status not in {"COMPLETE", "SKIPPED"}:
      retry_at = record.get("next_retry_at")
      if retry_at is None:
        retry_at = time_utils.now() + timedelta(
          seconds=_SESSION_CHECKPOINT_RETRY_SECONDS
        )
      if isinstance(retry_at, datetime):
        retry_at = self._checkpoint_local_time(retry_at).isoformat()
      record["next_retry_at"] = str(retry_at)
    runtime.checkpoint_status[key] = record
    return record

  @staticmethod
  def _runtime_checkpoint_queues_drained(
    runtime: StrategyRuntime,
    *,
    allow_current_market_event: bool = False,
  ) -> bool:
    """Return whether no pre-boundary work remains in either runtime queue.

    Virtual-day sealing is invoked before processing the first event of the
    next day.  Historical replay delivers that acquired event through the
    serial control queue, while a real-time market event uses the market
    queue.  It is explicitly outside the prior-day fence; permitting exactly
    one acquired item avoids assigning it to the wrong day without admitting
    any additional queued or in-flight work.
    """

    event_unfinished = int(
      getattr(runtime.event_queue, "_unfinished_tasks", 0) or 0
    )
    if (
      not runtime.event_queue.empty()
      or not runtime.market_event_queue.empty()
    ):
      return False
    market_unfinished = int(
      getattr(runtime.market_event_queue, "_unfinished_tasks", 0) or 0
    )
    active_market_events = len(runtime._processing_market_events)
    if allow_current_market_event:
      return (
        event_unfinished + market_unfinished <= 1
        and active_market_events <= 1
      )
    return (
      event_unfinished == 0
      and market_unfinished == 0
      and active_market_events == 0
    )

  @staticmethod
  def _checkpoint_source_time_ms(value: Any) -> int:
    if isinstance(value, datetime):
      return int(value.timestamp() * 1000)
    try:
      return int(value or 0)
    except (TypeError, ValueError, OverflowError):
      return 0

  def _whole_quote_checkpoint_fence(
    self,
    *,
    boundary_source_time: datetime,
  ) -> tuple[Optional[Dict[str, Any]], str]:
    """Read a global, ready WholeQuoteHub fence; never infer one from a clock."""

    try:
      snapshot = dict(whole_quote_hub.status_snapshot() or {})
    except Exception as exc:
      return None, f"WHOLE_QUOTE_FENCE_UNAVAILABLE:{exc.__class__.__name__}"
    if str(snapshot.get("status") or "").upper() != "READY":
      return None, "WHOLE_QUOTE_NOT_READY"
    stream_id = str(snapshot.get("stream_id") or "").strip()
    try:
      generation = int(snapshot.get("generation") or 0)
      sequence = int(snapshot.get("sequence") or 0)
      queue_depth = int(snapshot.get("queue_depth") or 0)
      lagging_consumers = int(snapshot.get("lagging_consumers") or 0)
    except (TypeError, ValueError, OverflowError):
      return None, "WHOLE_QUOTE_INVALID_FENCE"
    captured_at = self._checkpoint_datetime(snapshot.get("captured_at"))
    if not stream_id or generation <= 0 or sequence <= 0 or captured_at is None:
      return None, "WHOLE_QUOTE_INCOMPLETE_FENCE"
    captured_local = self._checkpoint_local_time(captured_at)
    boundary_local = self._checkpoint_local_time(boundary_source_time)
    if captured_local < boundary_local:
      return None, "WHOLE_QUOTE_FENCE_BEFORE_BOUNDARY"
    if queue_depth != 0:
      return None, "WHOLE_QUOTE_QUEUE_NOT_DRAINED"
    if lagging_consumers != 0:
      return None, "WHOLE_QUOTE_CONSUMER_LAGGING"
    return {
      "stream_id": stream_id,
      "generation": generation,
      "sequence": sequence,
      "source_time_ms": self._checkpoint_source_time_ms(captured_at),
      "captured_at": captured_at.isoformat(),
      "queue_depth": queue_depth,
      "lagging_consumers": lagging_consumers,
    }, ""

  def _record_processed_market_watermark(
    self,
    runtime: StrategyRuntime,
    event: Any,
    *,
    instrument_code: str,
  ) -> None:
    """Advance only after a serial consumer has fully processed a market event."""

    runtime._checkpoint_processed_tick_count += 1
    code = str(instrument_code or "").strip().upper()
    stream_id = str(self._get_value(event, "market_stream_id") or "").strip()
    generation = self._safe_non_negative_int(
      self._get_value(event, "continuity_generation"),
      default=0,
    )
    sequence = self._safe_non_negative_int(
      self._get_value(event, "market_stream_sequence"),
      default=0,
    )
    event_time = self._get_value(event, "time")
    source_time_ms = self._safe_non_negative_int(
      self._get_value(event, "source_time_ms"),
      default=self._checkpoint_source_time_ms(event_time),
    )
    if not stream_id or generation <= 0 or sequence <= 0:
      return
    current = dict(runtime._checkpoint_processed_watermark or {})
    same_lineage = (
      current.get("stream_id") == stream_id
      and int(current.get("generation") or 0) == generation
    )
    if not same_lineage or sequence >= int(current.get("sequence") or 0):
      runtime._checkpoint_processed_watermark = {
        "stream_id": stream_id,
        "generation": generation,
        "sequence": sequence,
        "source_time_ms": source_time_ms,
        "processed_tick_count": runtime._checkpoint_processed_tick_count,
      }
    elif current:
      # The authority fence remains the highest observed sequence, while the
      # accepted-consumer count still advances for this fully processed event.
      current["processed_tick_count"] = runtime._checkpoint_processed_tick_count
      runtime._checkpoint_processed_watermark = current
    if code:
      runtime._checkpoint_instrument_watermarks[code] = {
        "stream_id": stream_id,
        "generation": generation,
        "sequence": sequence,
        "source_time_ms": source_time_ms,
        "processed_tick_count": runtime._checkpoint_processed_tick_count,
      }

  def _record_backtest_market_watermark(
    self,
    runtime: StrategyRuntime,
    event: Any,
    *,
    instrument_code: str,
  ) -> None:
    timestamp = self._get_value(event, "time")
    if not isinstance(timestamp, datetime):
      return
    local_time = self._checkpoint_local_time(timestamp)
    runtime._checkpoint_virtual_sequence += 1
    runtime._checkpoint_processed_tick_count += 1
    runtime._checkpoint_virtual_trade_date = local_time.date()
    runtime._checkpoint_processed_watermark = {
      "stream_id": f"backtest:{runtime.run_id}",
      "generation": 1,
      "sequence": runtime._checkpoint_virtual_sequence,
      "source_time_ms": self._checkpoint_source_time_ms(timestamp),
      "processed_tick_count": runtime._checkpoint_processed_tick_count,
    }
    code = str(instrument_code or "").strip().upper()
    if code:
      runtime._checkpoint_instrument_watermarks[code] = dict(
        runtime._checkpoint_processed_watermark
      )

  def _freeze_checkpoint_diagnostic_segments(
    self,
    runtime: StrategyRuntime,
    *,
    instrument_codes: Iterable[str],
    boundary_event_key: str,
    boundary_kind: str,
  ) -> None:
    """Close active diagnostic aggregates before a MATERIAL/action boundary.

    A hot diagnostic aggregate represents a contiguous source-time segment.
    It must not absorb a later diagnostic after a pure MATERIAL transition or
    an immediately durable action.  The frozen event keeps its original
    identity; only its in-memory map key changes, so the explicit checkpoint
    still hands one idempotent event to the durable outbox.
    """

    normalized_codes = {
      str(code or "").strip().upper()
      for code in instrument_codes
      if str(code or "").strip()
    }
    summaries = runtime._checkpoint_diagnostic_summaries
    if normalized_codes:
      candidate_keys = [code for code in normalized_codes if code in summaries]
    else:
      # A non-evaluation action without an instrument binding is unusual, but
      # it still cannot leave an open aggregate spanning its source-time
      # boundary.  Freeze every currently-open diagnostic rather than silently
      # coalescing across the fact.
      candidate_keys = [
        key
        for key, event in summaries.items()
        if not key.startswith(("MATERIAL:", "DIAGNOSTIC:"))
        and str(event.get("record_kind") or "").upper()
        == "COALESCED_DIAGNOSTIC"
      ]

    normalized_boundary_key = str(boundary_event_key or "").strip()
    if not normalized_boundary_key:
      raise RuntimeError("做 T 诊断分段缺少稳定边界键")
    for key in candidate_keys:
      current = summaries.get(key)
      if current is None:
        continue
      if str(current.get("record_kind") or "").upper() != "COALESCED_DIAGNOSTIC":
        continue
      event_key = str(current.get("event_key") or "").strip()
      if not event_key:
        raise RuntimeError("做 T 诊断分段缺少稳定 event_key")
      frozen_key = f"DIAGNOSTIC:{event_key}"
      frozen = dict(current)
      frozen["checkpoint_segment_closed_by_event_key"] = normalized_boundary_key
      frozen["checkpoint_segment_boundary"] = str(boundary_kind or "MATERIAL")
      existing = summaries.get(frozen_key)
      if existing is not None and existing != frozen:
        raise RuntimeError("做 T 诊断分段 event_key 冲突")
      summaries.pop(key, None)
      summaries.setdefault(frozen_key, frozen)

  def _defer_checkpoint_diagnostics(
    self,
    runtime: StrategyRuntime,
    events: Iterable[Mapping[str, Any]],
  ) -> None:
    """Keep deferred evaluations in memory until their explicit boundary.

    Coalesced diagnostics retain one aggregate per instrument.  A pure
    MATERIAL state transition is not interchangeable with a heartbeat, so it
    retains its stable event key separately and is never silently collapsed.
    The shared manager outbox has an 8192-item daily safety limit; matching it here
    fails closed before a later PREPARED handoff could overflow.
    """

    for raw_event in events:
      event = dict(raw_event)
      code = str(event.get("instrument_code") or "").strip().upper()
      event_key = str(event.get("event_key") or "").strip()
      if not code and not event_key:
        raise RuntimeError("做 T 诊断事件缺少证券代码或稳定键")
      if str(event.get("record_kind") or "").upper() == "MATERIAL":
        if not event_key:
          raise RuntimeError("做 T MATERIAL 状态评估缺少稳定 event_key")
        # A pure MATERIAL evaluation is source-order significant.  Freeze the
        # preceding aggregate for this instrument before retaining the
        # MATERIAL separately; diagnostics after it will open a new segment.
        self._freeze_checkpoint_diagnostic_segments(
          runtime,
          instrument_codes=[code],
          boundary_event_key=event_key,
          boundary_kind="MATERIAL",
        )
        key = f"MATERIAL:{event_key}"
        previous = runtime._checkpoint_diagnostic_summaries.get(key)
        if previous is not None and previous != event:
          raise RuntimeError("做 T MATERIAL 状态评估 event_key 冲突")
        runtime._checkpoint_diagnostic_summaries.setdefault(key, event)
        continue
      key = code or event_key
      previous = runtime._checkpoint_diagnostic_summaries.get(key)
      try:
        count = int(event.get("coalesced_count") or 1)
      except (TypeError, ValueError, OverflowError):
        count = 1
      if previous is not None:
        count += int(previous.get("checkpoint_coalesced_count") or 0)
        event["checkpoint_window_started_at_ms"] = previous.get(
          "checkpoint_window_started_at_ms",
          previous.get("window_started_at_ms"),
        )
      else:
        event["checkpoint_window_started_at_ms"] = event.get(
          "window_started_at_ms",
          event.get("evaluated_at_ms"),
        )
      event["checkpoint_window_ended_at_ms"] = event.get("evaluated_at_ms")
      event["checkpoint_coalesced_count"] = max(1, count)
      event["coalesced_count"] = max(1, count)
      runtime._checkpoint_diagnostic_summaries[key] = event
    if (
      len(runtime._checkpoint_diagnostic_summaries)
      > _CHECKPOINT_EVALUATION_OUTBOX_MAX_EVENTS
    ):
      raise RuntimeError("做 T 诊断内存汇总超过安全标的上限")

  async def _flush_checkpoint_diagnostic_summaries(
    self,
    runtime: StrategyRuntime,
    *,
    captured_events: Mapping[str, Mapping[str, Any]],
  ) -> set[str]:
    """Materialize exactly the summaries captured by the preceding seal."""

    if not captured_events:
      return set()
    account_id = str(runtime.context.parameters.get("account_id") or "").strip()
    if not account_id:
      raise RuntimeError("做 T 诊断批量缺少唯一证券账户绑定")
    events = self._ordered_t_trade_diagnostic_events(captured_events.values())
    materialized = await self._materialize_t_trade_checkpoint_batch_with_retry(
      events=events,
      account_id=account_id,
      strategy_run_id=runtime.run_id,
    )
    flush_with_receipt = getattr(
      self.opportunity_runtime_service,
      "flush_diagnostics_with_receipt",
      None,
    )
    if not callable(flush_with_receipt):
      raise RuntimeError("做 T 诊断批量缺少终态 receipt 边界")
    receipt = await flush_with_receipt(
      account_id=account_id,
      strategy_run_id=runtime.run_id,
    )
    expected = set(captured_events)
    receipt_keys = {
      str(item or "").strip()
      for item in getattr(receipt, "persisted_event_keys", ())
      if str(item or "").strip()
    }
    confirmed = expected & (set(materialized) | receipt_keys)
    if confirmed != expected:
      raise RuntimeError("做 T 诊断批量 receipt 未覆盖当前检查点摘要")
    return confirmed

  def _capture_checkpoint_diagnostic_summaries(
    self,
    runtime: StrategyRuntime,
  ) -> Dict[str, Dict[str, Any]]:
    """Capture bounded hot summaries; the manager stages them atomically.

    ``prepare_checkpoint`` owns the only durable outbox mutation.  Keeping
    this method pure is important: a failed PREPARED CAS must not leave an
    executor-side enqueue that no immutable checkpoint owns.
    """

    captured: Dict[str, Dict[str, Any]] = {}
    for summary in runtime._checkpoint_diagnostic_summaries.values():
      event = dict(summary)
      event_key = str(event.get("event_key") or "").strip()
      if not event_key:
        raise RuntimeError("做 T 诊断检查点摘要缺少稳定 event_key")
      existing = captured.get(event_key)
      if existing is not None and existing != event:
        raise RuntimeError("做 T 诊断检查点摘要 event_key 冲突")
      captured.setdefault(event_key, event)
    return captured

  async def _finalize_prepared_runtime_checkpoint(
    self,
    runtime: StrategyRuntime,
    *,
    prepared: Any,
    trade_date: date,
    session: Optional[str],
    attempts: int,
  ) -> bool:
    """Materialize one immutable PREPARED outbox then atomically finalize it."""

    manager = runtime.state_manager
    checkpoint_id = str(getattr(prepared, "checkpoint_id", "") or "").strip()
    prepared_events_loader = getattr(
      manager,
      "prepared_t_trade_diagnostic_events",
      None,
    )
    finalize = getattr(manager, "finalize_prepared_checkpoint", None)
    if (
      not checkpoint_id
      or not callable(prepared_events_loader)
      or not callable(finalize)
    ):
      self._set_checkpoint_status(
        runtime,
        trade_date=trade_date,
        session=session,
        status="BLOCKED",
        reason="PREPARED_CHECKPOINT_PROTOCOL_UNAVAILABLE",
        attempts=attempts,
        prepared_checkpoint_id=checkpoint_id,
      )
      return False
    try:
      prepared_events = prepared_events_loader(checkpoint_id)
      if prepared_events is None:
        self._set_checkpoint_status(
          runtime,
          trade_date=trade_date,
          session=session,
          status="BLOCKED",
          reason="PREPARED_OUTBOX_MISMATCH",
          attempts=attempts,
          prepared_checkpoint_id=checkpoint_id,
        )
        return False
      pending_by_key = {
        str(event.get("event_key") or "").strip(): dict(event)
        for event in list(prepared_events)
        if str(event.get("event_key") or "").strip()
      }
    except Exception as exc:
      self._set_checkpoint_status(
        runtime,
        trade_date=trade_date,
        session=session,
        status="BLOCKED",
        reason=f"PREPARED_OUTBOX_INSPECTION_FAILED:{exc.__class__.__name__}",
        attempts=attempts,
        prepared_checkpoint_id=checkpoint_id,
      )
      return False
    expected_keys = set(pending_by_key)
    captured_events = {
      key: pending_by_key[key] for key in sorted(expected_keys)
    }
    try:
      confirmed_keys = await self._flush_checkpoint_diagnostic_summaries(
        runtime,
        captured_events=captured_events,
      )
    except Exception as exc:
      self._set_checkpoint_status(
        runtime,
        trade_date=trade_date,
        session=session,
        status="PREPARED",
        reason="DIAGNOSTIC_MATERIALIZATION_BLOCKED",
        attempts=attempts,
        prepared_checkpoint_id=checkpoint_id,
        diagnostic_error=exc.__class__.__name__,
      )
      return False
    if set(confirmed_keys) != expected_keys:
      self._set_checkpoint_status(
        runtime,
        trade_date=trade_date,
        session=session,
        status="PREPARED",
        reason="DIAGNOSTIC_RECEIPT_MISMATCH",
        attempts=attempts,
        prepared_checkpoint_id=checkpoint_id,
        expected_event_keys=sorted(expected_keys),
        confirmed_event_keys=sorted(confirmed_keys),
      )
      return False
    try:
      finalized = finalize(
        prepared_checkpoint_id=checkpoint_id,
        materialization_event_keys=sorted(expected_keys),
      )
      if inspect.isawaitable(finalized):
        finalized = await finalized
    except Exception as exc:
      self._set_checkpoint_status(
        runtime,
        trade_date=trade_date,
        session=session,
        status="PREPARED",
        reason=f"PREPARED_FINALIZE_FAILED:{exc.__class__.__name__}",
        attempts=attempts,
        prepared_checkpoint_id=checkpoint_id,
      )
      return False
    if finalized is None:
      self._set_checkpoint_status(
        runtime,
        trade_date=trade_date,
        session=session,
        status="PREPARED",
        reason="PREPARED_FINALIZE_REJECTED",
        attempts=attempts,
        prepared_checkpoint_id=checkpoint_id,
      )
      return False
    for summary_key, event in list(runtime._checkpoint_diagnostic_summaries.items()):
      if str(event.get("event_key") or "").strip() in expected_keys:
        runtime._checkpoint_diagnostic_summaries.pop(summary_key, None)
    self._set_checkpoint_status(
      runtime,
      trade_date=trade_date,
      session=session,
      status="COMPLETE",
      reason="SEALED",
      attempts=attempts,
      checkpoint_id=str(getattr(finalized, "checkpoint_id", "") or ""),
      processed_watermark=copy.deepcopy(
        getattr(finalized, "processed_watermark", {}) or {}
      ),
    )
    return True

  async def _seal_runtime_checkpoint(
    self,
    runtime: StrategyRuntime,
    *,
    trade_date: date,
    session: Optional[str],
    boundary_source_time: datetime,
    processed_watermark: Mapping[str, Any],
    continuity_generation: str | int,
    completeness: Mapping[str, Any],
    force: bool = False,
    allow_current_market_event: bool = False,
  ) -> bool:
    """Run the only legal PREPARED -> materialize -> FINALIZE checkpoint flow."""

    key = self._checkpoint_status_key(trade_date, session)
    previous = dict(runtime.checkpoint_status.get(key) or {})
    if previous.get("status") == "COMPLETE":
      return True
    attempts = int(previous.get("attempts", 0) or 0)
    if not force and attempts >= _SESSION_CHECKPOINT_MAX_RETRIES:
      return False
    if not self._runtime_checkpoint_queues_drained(
      runtime,
      allow_current_market_event=allow_current_market_event,
    ):
      self._set_checkpoint_status(
        runtime,
        trade_date=trade_date,
        session=session,
        status="DELAYED",
        reason="RUNTIME_QUEUES_NOT_DRAINED",
        attempts=attempts + 1,
      )
      return False
    manager = runtime.state_manager
    prepare = getattr(manager, "prepare_checkpoint", None)
    latest_prepared = getattr(manager, "latest_prepared_checkpoint", None)
    has_prepared = getattr(manager, "has_prepared_checkpoint", None)
    if not callable(prepare) or not callable(latest_prepared):
      self._set_checkpoint_status(
        runtime,
        trade_date=trade_date,
        session=session,
        status="BLOCKED",
        reason="PREPARED_CHECKPOINT_PROTOCOL_UNAVAILABLE",
        attempts=attempts + 1,
      )
      return False
    drain = getattr(manager, "drain_strategy_state_changes", None)
    if callable(drain):
      try:
        # Queue proof here must not eagerly clone the complete strategy state.
        # ``prepare_checkpoint`` performs the single authoritative capture
        # immediately before its CAS.
        drained = drain(capture_state=False)
        if inspect.isawaitable(drained):
          drained = await drained
      except Exception as exc:
        drained = False
        drain_reason = f"STATE_DRAIN_FAILED:{exc.__class__.__name__}"
      else:
        drain_reason = "STATE_DRAIN_REJECTED"
      if not drained:
        self._set_checkpoint_status(
          runtime,
          trade_date=trade_date,
          session=session,
          status="DELAYED",
          reason=drain_reason,
          attempts=attempts + 1,
        )
        return False
    if not self._runtime_checkpoint_queues_drained(
      runtime,
      allow_current_market_event=allow_current_market_event,
    ):
      self._set_checkpoint_status(
        runtime,
        trade_date=trade_date,
        session=session,
        status="DELAYED",
        reason="RUNTIME_QUEUES_ARRIVED_DURING_DRAIN",
        attempts=attempts + 1,
    )
      return False
    complete = dict(completeness)
    complete["complete"] = bool(complete.get("complete") is True)
    if not complete["complete"]:
      self._set_checkpoint_status(
        runtime,
        trade_date=trade_date,
        session=session,
        status="BLOCKED",
        reason=str(complete.get("reason") or "COMPLETENESS_UNPROVEN"),
        attempts=attempts + 1,
      )
      return False
    try:
      prepared = latest_prepared()
      if inspect.isawaitable(prepared):
        prepared = await prepared
    except Exception as exc:
      self._set_checkpoint_status(
        runtime,
        trade_date=trade_date,
        session=session,
        status="BLOCKED",
        reason=f"PREPARED_CHECKPOINT_INSPECTION_FAILED:{exc.__class__.__name__}",
        attempts=attempts + 1,
      )
      return False
    if prepared is not None:
      try:
        prepared_trade_date = date.fromisoformat(
          str(getattr(prepared, "trade_date", "") or "")
        )
      except ValueError:
        self._set_checkpoint_status(
          runtime,
          trade_date=trade_date,
          session=session,
          status="BLOCKED",
          reason="PREPARED_CHECKPOINT_INVALID_TRADE_DATE",
          attempts=attempts + 1,
          prepared_checkpoint_id=str(
            getattr(prepared, "checkpoint_id", "") or ""
          ),
        )
        return False
      prepared_session = getattr(prepared, "session", None)
      return await self._finalize_prepared_runtime_checkpoint(
        runtime,
        prepared=prepared,
        trade_date=prepared_trade_date,
        session=(str(prepared_session) if prepared_session is not None else None),
        attempts=attempts + 1,
      )
    if callable(has_prepared):
      try:
        prepared_exists = has_prepared()
        if inspect.isawaitable(prepared_exists):
          prepared_exists = await prepared_exists
      except Exception as exc:
        prepared_exists = True
        prepared_reason = f"PREPARED_CHECKPOINT_INSPECTION_FAILED:{exc.__class__.__name__}"
      else:
        prepared_reason = "PREPARED_CHECKPOINT_CORRUPT"
      if prepared_exists:
        self._set_checkpoint_status(
          runtime,
          trade_date=trade_date,
          session=session,
          status="BLOCKED",
          reason=prepared_reason,
          attempts=attempts + 1,
        )
        return False
    pending_diagnostics = getattr(manager, "pending_t_trade_diagnostic_events", None)
    if not callable(pending_diagnostics):
      self._set_checkpoint_status(
        runtime,
        trade_date=trade_date,
        session=session,
        status="BLOCKED",
        reason="DIAGNOSTIC_OUTBOX_UNAVAILABLE",
        attempts=attempts + 1,
      )
      return False
    try:
      pending_count = len(list(pending_diagnostics() or []))
    except Exception as exc:
      self._set_checkpoint_status(
        runtime,
        trade_date=trade_date,
        session=session,
        status="BLOCKED",
        reason=f"DIAGNOSTIC_OUTBOX_INSPECTION_FAILED:{exc.__class__.__name__}",
        attempts=attempts + 1,
      )
      return False
    if pending_count:
      self._set_checkpoint_status(
        runtime,
        trade_date=trade_date,
        session=session,
        status="BLOCKED",
        reason="ORPHAN_DIAGNOSTIC_OUTBOX",
        attempts=attempts + 1,
        durable_diagnostic_count=pending_count,
      )
      return False
    try:
      captured_events = self._capture_checkpoint_diagnostic_summaries(runtime)
      prepared = prepare(
        trade_date=trade_date,
        session=session,
        boundary_source_time=boundary_source_time,
        processed_watermark=dict(processed_watermark),
        continuity_generation=continuity_generation,
        completeness=complete,
        materialization_events=self._ordered_t_trade_diagnostic_events(
          captured_events.values()
        ),
      )
      if inspect.isawaitable(prepared):
        prepared = await prepared
    except Exception as exc:
      self._set_checkpoint_status(
        runtime,
        trade_date=trade_date,
        session=session,
        status="BLOCKED",
        reason=f"CHECKPOINT_PREPARE_FAILED:{exc.__class__.__name__}",
        attempts=attempts + 1,
      )
      return False
    if prepared is None:
      self._set_checkpoint_status(
        runtime,
        trade_date=trade_date,
        session=session,
        status="BLOCKED",
        reason="CHECKPOINT_PREPARE_REJECTED",
        attempts=attempts + 1,
      )
      return False
    self._set_checkpoint_status(
      runtime,
      trade_date=trade_date,
      session=session,
      status="PREPARED",
      reason="PREPARED",
      attempts=attempts + 1,
      prepared_checkpoint_id=str(getattr(prepared, "checkpoint_id", "") or ""),
    )
    return await self._finalize_prepared_runtime_checkpoint(
      runtime,
      prepared=prepared,
      trade_date=trade_date,
      session=session,
      attempts=attempts + 1,
    )

  async def _maybe_coordinate_session_checkpoints(
    self,
    runtime: StrategyRuntime,
    *,
    now: Optional[datetime] = None,
  ) -> None:
    if self._runtime_state_checkpoint_policy(runtime) != (
      RUNTIME_STATE_CHECKPOINT_POLICY_SESSION_BOUNDARY
    ):
      return
    state_manager = getattr(runtime, "state_manager", None)
    if state_manager is None or not bool(
      getattr(state_manager, "persist_enabled", False)
    ):
      return
    current = self._checkpoint_local_time(now or time_utils.now())
    eligible_specs = [
      spec
      for spec in _SESSION_CHECKPOINT_SPECS
      if current.time() >= spec[2]
    ]
    if not eligible_specs:
      return
    trade_date = current.date()

    # This runs from the event-loop finally block.  Most ticks after a
    # completed/temporarily blocked boundary must not touch the trading-day
    # calendar (which can be a database-backed service).
    due_specs: list[tuple[str, time, time, Dict[str, Any]]] = []
    for session, boundary_time, eligible_time in eligible_specs:
      previous = dict(
        runtime.checkpoint_status.get(
          self._checkpoint_status_key(trade_date, session)
        )
        or {}
      )
      if previous.get("status") in {"COMPLETE", "SKIPPED"}:
        continue
      if int(previous.get("attempts", 0) or 0) >= _SESSION_CHECKPOINT_MAX_RETRIES:
        continue
      next_retry_at = self._checkpoint_datetime(previous.get("next_retry_at"))
      if next_retry_at is not None and self._checkpoint_local_time(
        next_retry_at
      ) > current:
        continue
      due_specs.append((session, boundary_time, eligible_time, previous))
    if not due_specs:
      return

    try:
      trading_date = self._trading_date_helper.is_trading_date("SH", trade_date)
      if inspect.isawaitable(trading_date):
        trading_date = await trading_date
    except Exception as exc:
      for session, _boundary_time, _eligible_time, previous in due_specs:
        self._set_checkpoint_status(
          runtime,
          trade_date=trade_date,
          session=session,
          status="BLOCKED",
          reason=f"SH_TRADING_CALENDAR_UNAVAILABLE:{exc.__class__.__name__}",
          attempts=int(previous.get("attempts", 0) or 0) + 1,
          next_retry_at=current + timedelta(
            seconds=_SESSION_CHECKPOINT_RETRY_SECONDS
          ),
        )
      return
    if not trading_date:
      for session, _boundary_time, _eligible_time, previous in due_specs:
        self._set_checkpoint_status(
          runtime,
          trade_date=trade_date,
          session=session,
          status="SKIPPED",
          reason="SH_NON_TRADING_DAY",
          attempts=int(previous.get("attempts", 0) or 0),
        )
      return

    for session, boundary_time, _eligible_time, previous in due_specs:
      boundary = datetime.combine(trade_date, boundary_time)
      first_fence, reason = self._whole_quote_checkpoint_fence(
        boundary_source_time=boundary
      )
      if first_fence is None:
        self._set_checkpoint_status(
          runtime,
          trade_date=trade_date,
          session=session,
          status="BLOCKED",
          reason=reason,
          attempts=int(previous.get("attempts", 0) or 0) + 1,
        )
        continue
      if not self._runtime_checkpoint_queues_drained(runtime):
        self._set_checkpoint_status(
          runtime,
          trade_date=trade_date,
          session=session,
          status="DELAYED",
          reason="RUNTIME_QUEUES_NOT_DRAINED",
          attempts=int(previous.get("attempts", 0) or 0) + 1,
          fence=first_fence,
        )
        continue
      drain = getattr(runtime.state_manager, "drain_strategy_state_changes", None)
      if not callable(drain):
        self._set_checkpoint_status(
          runtime,
          trade_date=trade_date,
          session=session,
          status="BLOCKED",
          reason="CHECKPOINT_STATE_DRAIN_UNAVAILABLE",
          attempts=int(previous.get("attempts", 0) or 0) + 1,
          fence=first_fence,
        )
        continue
      try:
        # This pre-fence drain only establishes quiescence.  The subsequent
        # coordinator prepare captures the full strategy state exactly once.
        drained = drain(capture_state=False)
        if inspect.isawaitable(drained):
          drained = await drained
      except Exception as exc:
        drained = False
        drain_reason = f"STATE_DRAIN_FAILED:{exc.__class__.__name__}"
      else:
        drain_reason = "STATE_DRAIN_REJECTED"
      if not drained or not self._runtime_checkpoint_queues_drained(runtime):
        self._set_checkpoint_status(
          runtime,
          trade_date=trade_date,
          session=session,
          status="DELAYED",
          reason=(
            drain_reason
            if not drained
            else "RUNTIME_QUEUES_ARRIVED_DURING_DRAIN"
          ),
          attempts=int(previous.get("attempts", 0) or 0) + 1,
          fence=first_fence,
        )
        continue
      second_fence, reason = self._whole_quote_checkpoint_fence(
        boundary_source_time=boundary
      )
      if second_fence is None:
        self._set_checkpoint_status(
          runtime,
          trade_date=trade_date,
          session=session,
          status="BLOCKED",
          reason=reason,
          attempts=int(previous.get("attempts", 0) or 0) + 1,
          fence=first_fence,
        )
        continue
      if any(
        first_fence[field] != second_fence[field]
        for field in ("stream_id", "generation", "sequence")
      ):
        self._set_checkpoint_status(
          runtime,
          trade_date=trade_date,
          session=session,
          status="DELAYED",
          reason="WHOLE_QUOTE_FENCE_CHANGED_DURING_DRAIN",
          attempts=int(previous.get("attempts", 0) or 0) + 1,
          first_fence=first_fence,
          second_fence=second_fence,
        )
        continue
      continuity_ok = not (
        runtime._pending_market_invalidations
        or runtime._active_market_continuity_losses
        or runtime._market_fail_closed_codes
        or runtime._processing_market_events
      )
      if not continuity_ok or not self._runtime_checkpoint_queues_drained(runtime):
        self._set_checkpoint_status(
          runtime,
          trade_date=trade_date,
          session=session,
          status="BLOCKED" if not continuity_ok else "DELAYED",
          reason=(
            "MARKET_CONTINUITY_UNPROVEN"
            if not continuity_ok
            else "RUNTIME_QUEUES_ARRIVED_AFTER_FENCE"
          ),
          attempts=int(previous.get("attempts", 0) or 0) + 1,
          fence=second_fence,
        )
        continue
      await self._seal_runtime_checkpoint(
        runtime,
        trade_date=trade_date,
        session=session,
        boundary_source_time=boundary,
        # The global, drained WholeQuoteHub fence is the completion watermark.
        # Per-instrument watermarks remain audit information only.
        processed_watermark=second_fence,
        continuity_generation=(
          f"{second_fence['stream_id']}:{second_fence['generation']}"
        ),
        completeness={
          "complete": True,
          "event_queue_drained": True,
          "market_queue_drained": True,
          "whole_quote_fence": second_fence,
          "instrument_watermarks_audit": dict(
            runtime._checkpoint_instrument_watermarks
          ),
        },
      )

  async def _coordinate_terminal_session_checkpoint(
    self,
    runtime: StrategyRuntime,
    *,
    cause: str,
  ) -> None:
    """Seal remaining P/L hot diagnostics before terminal state persistence.

    ``TERMINAL`` is deliberately distinct from an official AM/PM boundary.
    It never invents a WholeQuoteHub fence or wall-clock session completion:
    the executor proves only that its serial queues are quiesced, market
    continuity remains intact, and a previously processed stream watermark
    identifies the final source-time prefix.  The normal PREPARED -> receipt
    -> FINALIZE protocol then transfers the remaining bounded hot summaries.
    """

    if self._runtime_state_checkpoint_policy(runtime) != (
      RUNTIME_STATE_CHECKPOINT_POLICY_SESSION_BOUNDARY
    ):
      return
    if not runtime._checkpoint_diagnostic_summaries:
      return
    watermark = dict(runtime._checkpoint_processed_watermark or {})
    stream_id = str(watermark.get("stream_id") or "").strip()
    generation = self._safe_non_negative_int(watermark.get("generation"), default=0)
    sequence = self._safe_non_negative_int(watermark.get("sequence"), default=0)
    source_time_ms = self._safe_non_negative_int(
      watermark.get("source_time_ms"),
      default=0,
    )
    if not stream_id or generation <= 0 or sequence <= 0 or source_time_ms <= 0:
      raise RuntimeError("TERMINAL_SESSION_CHECKPOINT_WATERMARK_UNPROVEN")
    if not self._runtime_checkpoint_queues_drained(runtime):
      raise RuntimeError("TERMINAL_SESSION_CHECKPOINT_QUEUES_NOT_DRAINED")
    continuity_failures = {
      "pending_invalidations": sorted(runtime._pending_market_invalidations),
      "active_losses": sorted(runtime._active_market_continuity_losses),
      "fail_closed": sorted(runtime._market_fail_closed_codes),
    }
    if any(continuity_failures.values()):
      raise RuntimeError("TERMINAL_SESSION_CHECKPOINT_CONTINUITY_UNPROVEN")
    try:
      boundary = self._checkpoint_local_time(
        datetime.fromtimestamp(source_time_ms / 1000.0, tz=timezone.utc)
      )
    except (OverflowError, OSError, ValueError) as exc:
      raise RuntimeError("TERMINAL_SESSION_CHECKPOINT_SOURCE_TIME_INVALID") from exc
    trade_date = boundary.date()
    sealed = await self._seal_runtime_checkpoint(
      runtime,
      trade_date=trade_date,
      session="TERMINAL",
      boundary_source_time=boundary,
      processed_watermark=watermark,
      continuity_generation=f"{stream_id}:{generation}",
      completeness={
        "complete": True,
        "terminal": True,
        "terminal_cause": str(cause or "TERMINAL"),
        "event_queue_drained": True,
        "market_queue_drained": True,
        "continuity_failures": continuity_failures,
      },
      force=True,
    )
    terminal_key = self._checkpoint_status_key(trade_date, "TERMINAL")
    terminal_complete = (
      runtime.checkpoint_status.get(terminal_key, {}).get("status") == "COMPLETE"
    )
    # A previously durable PREPARED checkpoint can be finalized first.  If it
    # contained the exact hot summaries, they are already safe and no terminal
    # prefix is needed; otherwise refuse to let the later generic stop snapshot
    # overwrite unsealed diagnostics.
    if terminal_complete or (sealed and not runtime._checkpoint_diagnostic_summaries):
      return
    raise RuntimeError("TERMINAL_SESSION_CHECKPOINT_BLOCKED")

  async def _coordinate_backtest_virtual_day_before_event(
    self,
    runtime: StrategyRuntime,
    timestamp: Any,
  ) -> None:
    if self._runtime_state_checkpoint_policy(runtime) != (
      RUNTIME_STATE_CHECKPOINT_POLICY_DAY_BATCH
    ) or not isinstance(timestamp, datetime):
      return
    event_date = self._checkpoint_local_time(timestamp).date()
    previous_date = runtime._checkpoint_virtual_trade_date
    if previous_date is None or event_date <= previous_date:
      return
    watermark = dict(runtime._checkpoint_processed_watermark or {})
    sealed = await self._seal_runtime_checkpoint(
      runtime,
      trade_date=previous_date,
      session=None,
      boundary_source_time=datetime.combine(previous_date, time.max),
      processed_watermark=watermark,
      continuity_generation=runtime._checkpoint_virtual_sequence,
      completeness={
        "complete": self._runtime_checkpoint_queues_drained(
          runtime,
          allow_current_market_event=True,
        ),
        "reason": "VIRTUAL_DAY_QUEUE_NOT_DRAINED",
        "virtual_day_transition_to": event_date.isoformat(),
      },
      allow_current_market_event=True,
    )
    if not sealed:
      raise RuntimeError("BACKTEST_VIRTUAL_DAY_CHECKPOINT_BLOCKED")

  async def _coordinate_backtest_terminal_checkpoint(
    self,
    runtime: StrategyRuntime,
    *,
    cause: str,
  ) -> None:
    if self._runtime_state_checkpoint_policy(runtime) != (
      RUNTIME_STATE_CHECKPOINT_POLICY_DAY_BATCH
    ):
      return
    # A never-started BACKTEST has neither mutable runtime state nor a
    # manager-owned durable boundary to seal.  Its cancellation must not turn
    # a harmless lifecycle cleanup into a synthetic failed day checkpoint.
    if runtime.state_manager is None:
      return
    trade_date = runtime._checkpoint_virtual_trade_date
    if trade_date is None:
      # There is no virtual business day until a replay market event advances
      # the serial watermark.  Do not invent one from wall clock during a
      # start/stop-only BACKTEST lifecycle.
      return
    current_time = runtime.context.current_time or datetime.combine(trade_date, time.max)
    boundary = (
      current_time
      if isinstance(current_time, datetime)
      else datetime.combine(trade_date, time.max)
    )
    sealed = await self._seal_runtime_checkpoint(
      runtime,
      trade_date=trade_date,
      session=None,
      boundary_source_time=boundary,
      processed_watermark=dict(runtime._checkpoint_processed_watermark or {}),
      continuity_generation=runtime._checkpoint_virtual_sequence,
      completeness={
        "complete": self._runtime_checkpoint_queues_drained(runtime),
        "reason": f"TERMINAL_QUEUES_NOT_DRAINED:{cause}",
        "terminal_cause": cause,
      },
      force=True,
    )
    if not sealed:
      raise RuntimeError(f"BACKTEST_TERMINAL_CHECKPOINT_BLOCKED:{cause}")

  async def start(
    self,
    run_id: str,
    *,
    t_trade_account_coordination_held: bool = False,
  ) -> bool:
    if run_id not in self.runs:
      self.logger.error(f"策略运行不存在: {run_id}")
      return False
    runtime = self.runs[run_id]
    return await self._run_lifecycle_operation(
      runtime,
      "start",
      lambda: self._start_runtime(
        run_id,
        t_trade_account_coordination_held=t_trade_account_coordination_held,
      ),
    )

  async def _start_runtime(
    self,
    run_id: str,
    *,
    t_trade_account_coordination_held: bool = False,
  ) -> bool:
    """
    启动策略运行

    Args:
        run_id: 运行实例ID

    Returns:
        bool: 是否启动成功

    Note:
        状态变更由调用方（StrategyManager）负责持久化
    """
    if run_id not in self.runs:
      self.logger.error(f"策略运行不存在: {run_id}")
      return False

    runtime = self.runs[run_id]

    if runtime.status == ExecutionStatus.RUNNING:
      self.logger.warning(f"策略运行已在运行: {run_id}")
      return True
    if runtime.status == ExecutionStatus.STARTING:
      self.logger.error("策略启动状态无活动 owner，拒绝提前报告成功: %s", run_id)
      return False

    if runtime.status == ExecutionStatus.ERROR:
      if not await self._retry_previous_cleanup_before_start(runtime):
        self.logger.error(
          "上一次运行资源尚未收敛，拒绝创建新的启动代次: %s",
          run_id,
        )
        return False
    elif runtime._adapter_ref_acquired:
      self.logger.error("运行仍持有旧数据适配器引用，拒绝重新启动: %s", run_id)
      return False

    runtime._startup_abort_complete = False
    runtime._terminal_cleanup_complete = False
    runtime._adapter_ref_acquired = False
    runtime._startup_abort_task = None
    runtime._terminal_cleanup_task = None
    runtime.task = None
    runtime.event_task = None
    runtime.broker = None
    runtime.data_adapter = None
    runtime.performance_recorder = None
    runtime.error_message = None
    self._reset_runtime_generation_transients(runtime)

    try:
      self._initialize_t_trade_phase_one_baseline(runtime)
      # 更新状态
      runtime.status = ExecutionStatus.STARTING
      if runtime.context.mode == StrategyRunMode.BACKTEST:
        replay_start = (
          runtime.context.backtest_start_time
          or runtime.context.current_time
          or time_utils.now()
        )
        runtime.replay_clock = ReplayClock(replay_start)
        runtime.context.current_time = replay_start

      # 创建策略对象
      runtime.strategy = runtime.strategy_class(runtime.context)

      # Every run has one durable policy.  BACKTEST is explicitly marked here
      # even without a Backtest row so RuntimeStateManager can enforce its
      # day-boundary contract and never start a periodic hot-state writer.
      from quantx_infrastructure.core.runtime_state_manager import (
        RUNTIME_CHECKPOINTS_KEY,
        RuntimeStateManager,
        RuntimeStateRestoreStatus,
      )

      enable_reserve = bool(runtime.context.parameters.get("enable_reserve", True))
      runtime.state_manager = RuntimeStateManager(
        run_id=run_id,
        persist_enabled=self._runtime_state_persistence_enabled(runtime),
        log_dir=os.path.join("logs", "strategy", runtime.context.mode.value),
        enable_reserve=enable_reserve,
        is_backtest=(runtime.context.mode == StrategyRunMode.BACKTEST),
      )

      if runtime.context.mode == StrategyRunMode.BACKTEST:
        # 回测模式：配置为文件存储
        if runtime.context.backtest_id:
          runtime.state_manager.set_backtest_mode(
            runtime.context.backtest_id,
            backtest_version=runtime.context.backtest_version,
          )

      # 附加日志广播 Handler，并把每个运行实例绑定到独立日志文件。
      if runtime.log_manager:
        runtime.log_manager.configure_file(
          run_id=runtime.run_id,
          file_path=(
            runtime.state_manager.get_log_file_path() if runtime.state_manager else None
          ),
        )
        runtime.log_manager.attach_handler(
          run_id=runtime.run_id,
          logger=runtime.strategy.logger,
          source=getattr(runtime.strategy, "name", "strategy"),
        )
        self._runtime_log(
          runtime,
          "INFO",
          (
            f"策略运行启动准备: mode={runtime.context.mode.value}, "
            f"backtest_id={runtime.context.backtest_id or '-'}, "
            f"backtest_version={runtime.context.backtest_version or '-'}"
          ),
        )

      # 恢复之前的状态（如果有）
      restore_result = await runtime.state_manager.restore()
      restored_state = restore_result.state
      if restore_result.status == RuntimeStateRestoreStatus.NOT_FOUND:
        self.logger.info("未找到持久化运行状态，按新运行初始化: %s", run_id)
      latest_prepared = getattr(
        runtime.state_manager,
        "latest_prepared_checkpoint",
        None,
      )
      has_prepared = getattr(
        runtime.state_manager,
        "has_prepared_checkpoint",
        None,
      )
      if not callable(latest_prepared) or not callable(has_prepared):
        raise RuntimeError("运行状态管理器缺少 PREPARED 检查点恢复协议")
      prepared_checkpoint = latest_prepared()
      if inspect.isawaitable(prepared_checkpoint):
        prepared_checkpoint = await prepared_checkpoint
      prepared_exists = has_prepared()
      if inspect.isawaitable(prepared_exists):
        prepared_exists = await prepared_exists
      if prepared_checkpoint is None and prepared_exists:
        corrupt_trade_date = self._checkpoint_local_time(time_utils.now()).date()
        self._set_checkpoint_status(
          runtime,
          trade_date=corrupt_trade_date,
          session=None,
          status="BLOCKED",
          reason="PREPARED_CHECKPOINT_CORRUPT_RECONCILIATION_REQUIRED",
          attempts=1,
        )
        self.logger.warning(
          "PREPARED 检查点损坏，拒绝恢复当前运行状态: run_id=%s",
          run_id,
        )
        raise RuntimeError(
          "PREPARED_CHECKPOINT_CORRUPT_RECONCILIATION_REQUIRED"
        )
      elif prepared_checkpoint is not None:
        try:
          prepared_trade_date = date.fromisoformat(
            str(getattr(prepared_checkpoint, "trade_date", "") or "")
          )
        except ValueError as exc:
          raise RuntimeError("PREPARED_CHECKPOINT_INVALID_TRADE_DATE") from exc
        prepared_session = getattr(prepared_checkpoint, "session", None)
        finalized = await self._finalize_prepared_runtime_checkpoint(
          runtime,
          prepared=prepared_checkpoint,
          trade_date=prepared_trade_date,
          session=(str(prepared_session) if prepared_session is not None else None),
          attempts=1,
        )
        if not finalized:
          raise RuntimeError("PREPARED_CHECKPOINT_FINALIZATION_BLOCKED")
        restored_state = copy.deepcopy(runtime.state_manager._state)
      raw_checkpoint_metadata = (runtime.state_manager._state.get("custom") or {}).get(
        RUNTIME_CHECKPOINTS_KEY
      )
      if raw_checkpoint_metadata is not None:
        latest_complete = getattr(
          runtime.state_manager,
          "latest_complete_checkpoint",
          None,
        )
        if not callable(latest_complete):
          raise RuntimeError("运行状态管理器缺少 SEALED 检查点恢复协议")
        complete_checkpoint = latest_complete()
        if inspect.isawaitable(complete_checkpoint):
          complete_checkpoint = await complete_checkpoint
        if complete_checkpoint is None:
          checkpoint_trade_date = self._checkpoint_local_time(time_utils.now()).date()
          self._set_checkpoint_status(
            runtime,
            trade_date=checkpoint_trade_date,
            session=None,
            status="BLOCKED",
            reason="COMPLETE_CHECKPOINT_STATE_MISMATCH",
            attempts=1,
          )
          raise RuntimeError("COMPLETE_CHECKPOINT_STATE_MISMATCH")
      runtime.exit_plan_book = ExitPlanBook.from_dict(
        (restored_state.get("custom") or {}).get(EXIT_PLAN_BOOK_STATE_KEY)
        if restored_state
        else None,
        evaluator=ExitPlanEvaluator(self.exit_strategy_registry),
      )
      if runtime.strategy and hasattr(runtime.strategy, "apply_state_snapshot"):
        strategy_snapshot_loader = getattr(
          runtime.state_manager,
          "get_strategy_custom_state",
          None,
        )
        strategy_snapshot = (
          dict(strategy_snapshot_loader() or {})
          if callable(strategy_snapshot_loader)
          else dict((restored_state or {}).get("custom") or {})
        )
        strategy_snapshot.pop(EXIT_PLAN_BOOK_STATE_KEY, None)
        runtime.strategy.apply_state_snapshot(strategy_snapshot)
      runtime._restored_market_windows_unverified.update(
        self._restored_causal_market_window_codes(runtime)
      )
      runtime.durable_event_barrier_key = (
        await runtime.state_manager.get_earliest_unapplied_runtime_event_key()
        if runtime.context.mode == StrategyRunMode.LIVE
        else None
      )
      runtime.durable_startup_barrier = bool(runtime.durable_event_barrier_key)
      if restored_state.get("positions"):
        self.logger.info(f"恢复持仓: {len(restored_state['positions'])} 个")
      if restored_state.get("active_orders"):
        self.logger.info(f"恢复活动订单: {len(restored_state['active_orders'])} 个")

      # 初始化策略额度（新运行实例）
      if runtime.state_manager:
        account = runtime.state_manager.get_account()
        positions = runtime.state_manager.get_all_positions()
        initialize_account = (
          account.get("cash", 0.0) <= 0
          and account.get("frozen_cash", 0.0) <= 0
          and account.get("total_asset", 0.0) <= 0
          and not positions
        )
        is_portfolio_replay = bool(
          runtime.context.mode == StrategyRunMode.BACKTEST
          and (
            runtime.context.parameters.get("t_trade_replay")
            or runtime.context.parameters.get("exit_plan_replay")
          )
        )
        if initialize_account and not is_portfolio_replay:
          runtime.state_manager.update_account(
            cash=runtime.context.initial_capital,
            frozen_cash=0.0,
            total_asset=runtime.context.initial_capital,
          )
        if not positions:
          initial_metadata = dict(
            runtime.context.parameters.get("initial_portfolio_metadata")
            or runtime.context.parameters.get("initial_instrument_metadata")
            or {}
          )
          if initial_metadata:
            self._sync_dynamic_holding_inventory(runtime, initial_metadata)
          else:
            self._seed_bucket_ledger_from_parameters(runtime)
        if initialize_account and is_portfolio_replay:
          initial_cash_value = runtime.context.parameters.get("initial_cash")
          initial_cash = (
            runtime.context.initial_capital
            if initial_cash_value is None
            else float(initial_cash_value)
          )
          seeded_market_value = sum(
            max(0.0, float(position.get("market_value", 0.0) or 0.0))
            for position in runtime.state_manager.get_all_positions().values()
          )
          non_trading_asset = max(
            0.0,
            runtime.context.initial_capital - initial_cash - seeded_market_value,
          )
          runtime.state_manager.update_account(
            cash=initial_cash,
            frozen_cash=0.0,
            total_asset=runtime.context.initial_capital,
            non_trading_asset=non_trading_asset,
          )

      # Strategy initialization is part of the foreground startup contract.
      # Returning success before it completes would let StrategyManager persist
      # RUNNING while a background task can still fail and leak broker/state
      # resources.
      await runtime.strategy.initialize()
      self._replay_restored_market_continuity_gates(runtime)
      await self._restore_pending_manual_approvals(
        runtime,
        t_trade_account_coordination_held=t_trade_account_coordination_held,
      )
      self._restore_t_trade_entry_reservations(runtime)
      invalidated_intent_ids = set(runtime.strategy.invalidated_manual_intent_ids())
      for intent_id in invalidated_intent_ids:
        intent = runtime.pending_approvals.get(intent_id)
        if intent is None:
          continue
        await self._reject_pending_approval(
          runtime,
          intent,
          status="EXPIRED",
          reason="MARKET_DATA_CONTINUITY_LOST",
          message="恢复的行情观察窗无法验证连续性，旧信号已失效",
        )
      await runtime.strategy.start()
      self._checkpoint_restored_strategy_state(runtime)

      # 根据模式创建 Broker 和 DataAdapter
      await self._setup_broker_and_data(runtime)
      self._seed_simulated_broker_positions(runtime)
      runtime.performance_recorder = StrategyPerformanceRecorder(
        run_id=run_id,
        mode=runtime.context.mode,
        backtest_id=runtime.context.backtest_id,
        initial_capital=runtime.context.initial_capital,
      )

      # 启动策略执行任务
      if runtime.state_manager and runtime.strategy:
        # Durable truth has already been restored successfully. Starting this
        # loop earlier can overwrite PostgreSQL with an empty default snapshot
        # when the restore query itself fails.
        await runtime.state_manager.start()
        await runtime.state_manager.start_state_sync(runtime.strategy)
        if self._requires_startup_runtime_state_checkpoint(runtime):
          checkpointed = await runtime.state_manager.checkpoint_strategy_state_changes()
          if not checkpointed:
            raise RuntimeError("策略启动状态安全快照失败，拒绝进入实时执行循环")
      runtime.task = asyncio.create_task(self._run_strategy_loop(runtime))

      # 启动事件处理循环
      runtime.event_task = asyncio.create_task(self._process_event_queue(runtime))

      # 更新状态
      runtime.status = ExecutionStatus.RUNNING

      self._runtime_log(runtime, "SUCCESS", f"策略运行启动成功: {run_id}")
      return True

    except asyncio.CancelledError:
      runtime.status = ExecutionStatus.ERROR
      runtime.error_message = "策略启动任务被取消"
      self._runtime_log(runtime, "ERROR", f"策略启动任务被取消: {run_id}")
      try:
        await self._ensure_startup_abort(runtime)
      except asyncio.CancelledError:
        # The shielded cleanup task remains owned by the runtime and continues.
        pass
      raise
    except Exception as e:
      runtime.status = ExecutionStatus.ERROR
      runtime.error_message = str(e)
      self._runtime_log(runtime, "ERROR", f"启动策略运行失败: {run_id}, 错误: {e}")
      await self._ensure_startup_abort(runtime)
      return False

  def _seed_bucket_ledger_from_parameters(self, runtime) -> None:
    """Initialize core/swing bucket attribution from strategy parameters."""
    if not runtime or not runtime.state_manager:
      return

    params = dict(getattr(runtime.context, "parameters", {}) or {})
    total_shares = int(params.get("position_shares", 0) or 0)
    available_shares = max(
      0,
      min(
        total_shares,
        int(params.get("position_available_shares", total_shares) or 0),
      ),
    )
    locked_core_shares = max(0, int(params.get("locked_core_shares", 0) or 0))
    swing_shares = max(0, int(params.get("swing_shares", 0) or 0))
    raw_core_shares = params.get("core_shares")
    core_shares = (
      max(0, int(raw_core_shares or 0))
      if raw_core_shares is not None
      else max(0, total_shares - locked_core_shares - swing_shares)
    )
    attributed_total = locked_core_shares + core_shares + swing_shares
    if attributed_total <= 0:
      return

    instrument_code = str(params.get("instrument_code", "") or "")
    if not instrument_code:
      stock_codes = params.get("stockCodes", params.get("stock_codes", ""))
      if isinstance(stock_codes, list):
        instrument_code = str(stock_codes[0] if stock_codes else "")
      else:
        instrument_code = str(stock_codes or "").split(",")[0].strip()
    if not instrument_code:
      return

    avg_price = float(params.get("avg_cost", params.get("base_price", 0.0)) or 0.0)
    last_price = float(params.get("base_price", avg_price) or avg_price)
    position_payload = {
      "long_volume": attributed_total,
      "available_volume": min(available_shares, attributed_total),
      "frozen_volume": 0,
      "today_buy_volume": 0,
      "long_avg_price": avg_price,
      "avg_price": avg_price,
      "last_price": last_price,
      "market_value": attributed_total * (last_price or avg_price),
    }
    runtime.state_manager.update_position(instrument_code, **position_payload)

    remaining_available = min(available_shares, attributed_total)

    def bucket_payload(volume):
      nonlocal remaining_available
      volume = max(0, int(volume or 0))
      bucket_available = min(volume, remaining_available)
      remaining_available -= bucket_available
      return {
        "total_volume": volume,
        "available_volume": bucket_available,
        "frozen_volume": 0,
        "today_buy_volume": max(0, volume - bucket_available),
        "avg_price": avg_price,
        "last_price": last_price,
        "market_value": volume * (last_price or avg_price),
      }

    runtime.state_manager.seed_bucket_positions(
      instrument_code,
      {
        "locked_core": bucket_payload(locked_core_shares),
        "core": bucket_payload(core_shares),
        "swing": bucket_payload(swing_shares),
      },
    )

  def _seed_simulated_broker_positions(self, runtime) -> None:
    """Seed backtest/paper brokers with configured initial holdings."""
    if (
      not runtime
      or runtime.context.mode not in {StrategyRunMode.BACKTEST, StrategyRunMode.PAPER}
      or not runtime.broker
      or not hasattr(runtime.broker, "positions")
      or not runtime.state_manager
    ):
      return

    positions = runtime.state_manager.get_all_positions()

    seeded = 0
    for instrument_code, pos in positions.items():
      if not instrument_code:
        continue
      long_volume = int(pos.get("long_volume", pos.get("available_volume", 0)) or 0)
      available_volume = int(pos.get("available_volume", long_volume) or 0)
      if long_volume <= 0 and available_volume <= 0:
        continue
      last_price = float(
        pos.get(
          "last_price",
          pos.get("avg_price", pos.get("long_avg_price", 0.0)),
        )
        or 0.0
      )
      avg_price = float(
        pos.get("long_avg_price", pos.get("avg_price", last_price)) or 0.0
      )
      runtime.broker.positions[instrument_code] = Position(
        instrument_code=instrument_code,
        long_volume=long_volume,
        available_volume=min(available_volume, long_volume),
        frozen_volume=int(pos.get("frozen_volume", 0) or 0),
        today_buy_volume=int(pos.get("today_buy_volume", 0) or 0),
        long_avg_price=avg_price,
        market_value=float(pos.get("market_value", long_volume * last_price) or 0.0),
        pnl=float(pos.get("pnl", 0.0) or 0.0),
        last_price=last_price,
      )
      seeded += 1

    if seeded:
      self.logger.info(
        f"{runtime.context.mode.value} Broker 初始持仓已注入: {seeded} 个标的"
      )
    configure_initial_portfolio = getattr(
      type(runtime.broker),
      "configure_initial_portfolio",
      None,
    )
    if runtime.context.mode == StrategyRunMode.BACKTEST and callable(
      configure_initial_portfolio
    ):
      params = dict(runtime.context.parameters or {})
      runtime.broker.configure_initial_portfolio(
        cash=float(params.get("initial_cash", runtime.context.initial_capital) or 0.0),
        total_asset=float(
          params.get("initial_total_asset", runtime.context.initial_capital) or 0.0
        ),
        positions=dict(runtime.broker.positions or {}),
      )

  def _sync_dynamic_holding_inventory(
    self,
    runtime: StrategyRuntime,
    instrument_metadata: Optional[Dict[str, Dict[str, Any]]],
  ) -> None:
    """Seed idle dynamic-holdings symbols as core inventory for T+1 substitution."""

    if not runtime.state_manager or not instrument_metadata:
      return
    instrument_states = (
      dict(runtime.strategy.state.get("instrument_states", {}) or {})
      if runtime.strategy
      else {}
    )
    for raw_code, raw_metadata in instrument_metadata.items():
      code = str(raw_code or "").strip().upper()
      metadata = dict(raw_metadata or {})
      if not code or "position_shares" not in metadata:
        continue
      state = dict(instrument_states.get(code, {}) or {})
      active_volume = max(
        0,
        int(state.get("entry_filled_volume", 0) or 0)
        - int(state.get("exit_filled_volume", 0) or 0),
      )
      if (
        active_volume > 0
        or state.get("batch_id")
        or state.get("pending_entry_intent_id")
        or state.get("pending_exit_intent_id")
      ):
        continue

      total_volume = max(0, int(metadata.get("position_shares", 0) or 0))
      available_volume = min(
        total_volume,
        max(0, int(metadata.get("position_available_shares", 0) or 0)),
      )
      frozen_volume = min(
        max(0, total_volume - available_volume),
        max(0, int(metadata.get("position_frozen_shares", 0) or 0)),
      )
      today_buy_volume = max(0, total_volume - available_volume - frozen_volume)
      avg_price = max(0.0, float(metadata.get("position_avg_price", 0.0) or 0.0))
      market_value = max(0.0, float(metadata.get("position_market_value", 0.0) or 0.0))
      last_price = (
        market_value / total_volume
        if total_volume > 0 and market_value > 0
        else avg_price
      )
      position_payload = {
        "long_volume": total_volume,
        "available_volume": available_volume,
        "frozen_volume": frozen_volume,
        "today_buy_volume": today_buy_volume,
        "long_avg_price": avg_price,
        "last_price": last_price,
        "market_value": market_value or total_volume * last_price,
      }
      runtime.state_manager.update_position(code, **position_payload)
      runtime.state_manager.seed_bucket_positions(
        code,
        {
          "locked_core": {},
          "core": {
            "total_volume": total_volume,
            "available_volume": available_volume,
            "frozen_volume": frozen_volume,
            "today_buy_volume": today_buy_volume,
            "avg_price": avg_price,
            "last_price": last_price,
            "market_value": position_payload["market_value"],
          },
          "swing": {},
        },
      )
      if (
        runtime.context.mode == StrategyRunMode.PAPER
        and runtime.broker
        and hasattr(runtime.broker, "positions")
      ):
        if total_volume <= 0:
          runtime.broker.positions.pop(code, None)
        else:
          runtime.broker.positions[code] = Position(
            instrument_code=code,
            long_volume=total_volume,
            available_volume=available_volume,
            frozen_volume=frozen_volume,
            today_buy_volume=today_buy_volume,
            long_avg_price=avg_price,
            market_value=position_payload["market_value"],
            pnl=0.0,
            last_price=last_price,
          )

  def _seed_backtest_broker_positions(self, runtime) -> None:
    """Backward-compatible wrapper for tests and older callers."""
    self._seed_simulated_broker_positions(runtime)

  @staticmethod
  def _runtime_lifecycle_blocker(runtime: StrategyRuntime) -> Optional[str]:
    """Return the first lifecycle item that must converge before pause/stop."""
    if runtime.durable_event_barrier_key:
      return f"持久化券商回报尚未收敛: {runtime.durable_event_barrier_key}"
    if runtime.pending_approvals:
      return f"仍有 {len(runtime.pending_approvals)} 个交易信号等待确认"
    if runtime.t_trade_entry_reservations:
      return "仍有做 T 入场委托等待券商回报"

    state_manager = runtime.state_manager
    if state_manager is not None:
      cash_reservations = getattr(state_manager, "_reservations", {})
      if isinstance(cash_reservations, dict) and cash_reservations:
        return "仍有买入委托资金冻结等待券商回报"
      position_reservations = getattr(state_manager, "_position_reservations", {})
      if isinstance(position_reservations, dict) and position_reservations:
        return "仍有卖出委托持仓冻结等待券商回报"

    broker = runtime.broker
    raw_orders = getattr(broker, "orders", {})
    orders = raw_orders if isinstance(raw_orders, dict) else {}
    active_statuses = {
      OrderStatus.PENDING.value,
      OrderStatus.SUBMITTED.value,
      OrderStatus.PARTIAL_FILLED.value,
    }
    if any(
      str(
        getattr(
          getattr(order, "status", ""),
          "value",
          getattr(order, "status", ""),
        )
      ).upper()
      in active_statuses
      for order in orders.values()
    ):
      return "仍有活动委托等待券商终态回报"

    strategy = runtime.strategy
    if strategy is not None:
      try:
        instrument_states = dict(
          strategy.state.to_dict().get("instrument_states", {}) or {}
        )
      except (AttributeError, TypeError, ValueError):
        instrument_states = {}
      if any(
        bool(
          raw_state.get("pending_entry_intent_id")
          or raw_state.get("pending_exit_intent_id")
        )
        for raw_state in instrument_states.values()
        if isinstance(raw_state, dict)
      ):
        return "仍有策略交易意图等待券商回报"
    return None

  @staticmethod
  def _accepts_non_durable_output(runtime: StrategyRuntime) -> bool:
    return runtime.status == ExecutionStatus.RUNNING

  @staticmethod
  def _runtime_state_reconciliation_failure(
    runtime: StrategyRuntime,
  ) -> Optional[tuple[str, str]]:
    """Return the fail-closed gate installed by durable state recovery."""
    state_manager = runtime.state_manager
    continuity_checker = getattr(
      state_manager,
      "market_continuity_reconciliation",
      None,
    )
    if callable(continuity_checker):
      try:
        continuity_gates = dict(continuity_checker() or {})
      except Exception:
        return (
          "RUNTIME_RECONCILIATION_STATUS_UNAVAILABLE",
          "行情连续性对账状态不可确认，已暂停新的交易决策",
        )
      if continuity_gates:
        return (
          "MARKET_CONTINUITY_RECONCILE_REQUIRED",
          "行情连续性失效且策略无法安全重建观察窗，需显式权威处置",
        )
    checker = getattr(state_manager, "requires_reconciliation", None)
    if not callable(checker):
      return None
    try:
      required = bool(checker())
    except Exception:
      return (
        "RUNTIME_RECONCILIATION_STATUS_UNAVAILABLE",
        "运行时对账状态不可确认，已暂停新的交易决策",
      )
    if not required:
      return None
    return (
      "RUNTIME_RECONCILE_REQUIRED",
      "持仓与 Bucket 账本不一致，等待权威对账后才能继续交易",
    )

  @staticmethod
  async def _put_runtime_control_event(
    runtime: StrategyRuntime,
    item: tuple[str, Any],
  ) -> None:
    await runtime.event_queue.put(item)
    runtime._event_queue_wakeup.set()

  @staticmethod
  def _put_runtime_control_event_nowait(
    runtime: StrategyRuntime,
    item: tuple[str, Any],
  ) -> None:
    runtime.event_queue.put_nowait(item)
    runtime._event_queue_wakeup.set()

  @staticmethod
  def _runtime_market_event_code(data: Any) -> str:
    return (
      str(
        getattr(data, "stock_code", None)
        or getattr(data, "instrument_code", None)
        or ""
      )
      .strip()
      .upper()
    )

  @staticmethod
  def _runtime_tick_source_age_seconds(data: Any) -> Optional[float]:
    timestamp = getattr(data, "time", None)
    if hasattr(timestamp, "to_pydatetime"):
      timestamp = timestamp.to_pydatetime()
    if not isinstance(timestamp, datetime):
      return None
    return (time_utils.now() - time_utils.to_shanghai(timestamp)).total_seconds()

  def _runtime_market_transport_lineage(
    self,
    data: Any,
  ) -> tuple[int, str, int, bool] | None:
    try:
      generation = int(self._get_value(data, "continuity_generation") or 0)
      stream_id = str(self._get_value(data, "market_stream_id") or "").strip()
      sequence = int(
        self._get_value(data, "market_stream_sequence")
        or self._get_value(data, "source_sequence")
        or 0
      )
    except (TypeError, ValueError, OverflowError):
      return None
    if generation <= 0 or not stream_id or sequence <= 0:
      return None
    return (
      generation,
      stream_id,
      sequence,
      self._coerce_bool(self._get_value(data, "market_stream_reset")),
    )

  def _observe_runtime_market_transport(
    self,
    runtime: StrategyRuntime,
    data: Any,
  ) -> bool:
    """Install a fail-closed gate before queuing a changed live lineage."""

    if runtime.context.mode == StrategyRunMode.BACKTEST:
      return True
    code = self._runtime_market_event_code(data)
    lineage = self._runtime_market_transport_lineage(data)
    if lineage is None:
      if code in runtime._market_fail_closed_codes:
        runtime.market_tick_source_rejections += 1
        runtime.market_events_dropped += 1
        return False
      _dropped, affected = self._drain_runtime_market_queue(runtime)
      if code:
        affected.add(code)
      self._mark_runtime_market_continuity_lost(
        runtime,
        affected,
        reason="MARKET_TRANSPORT_LINEAGE_UNAVAILABLE",
      )
      runtime._restored_market_windows_unverified.clear()
      runtime.market_tick_source_rejections += 1
      runtime.market_events_dropped += 1
      return False

    generation, stream_id, sequence, reset_requested = lineage
    current_generation = runtime._market_transport_generation
    current_stream_id = runtime._market_transport_stream_id
    if current_generation > 0 and generation < current_generation:
      runtime.market_events_dropped += 1
      return False

    identity_changed = bool(
      current_generation > 0
      and current_stream_id
      and (generation != current_generation or stream_id != current_stream_id)
    )
    if identity_changed:
      runtime._market_transport_sequences.clear()
    if current_generation <= 0 or not current_stream_id or identity_changed:
      runtime._market_transport_generation = generation
      runtime._market_transport_stream_id = stream_id

    reset_token = f"{generation}:{stream_id}:{sequence}"
    explicit_reset = bool(
      reset_requested and reset_token != runtime._market_transport_reset_token
    )
    restart_unverified = bool(runtime._restored_market_windows_unverified)
    if identity_changed or explicit_reset or restart_unverified:
      _dropped, affected = self._drain_runtime_market_queue(runtime)
      affected.update(runtime.context.instruments or [])
      affected.update(runtime._restored_market_windows_unverified)
      reason = (
        "MARKET_TRANSPORT_IDENTITY_CHANGED"
        if identity_changed
        else "MARKET_STREAM_RESYNC"
        if explicit_reset
        else "RUNTIME_RESTART_CONTINUITY_UNPROVEN"
      )
      self._mark_runtime_market_continuity_lost(
        runtime,
        affected,
        reason=reason,
      )
      runtime._restored_market_windows_unverified.clear()
    if reset_requested:
      runtime._market_transport_reset_token = reset_token

    previous_sequence = runtime._market_transport_sequences.get(code, 0)
    if previous_sequence and sequence <= previous_sequence:
      runtime.market_events_dropped += 1
      return False
    runtime._market_transport_sequences[code] = sequence
    return True

  def _mark_runtime_market_continuity_lost(
    self,
    runtime: StrategyRuntime,
    instrument_codes: Any,
    *,
    reason: str,
  ) -> None:
    codes = {
      str(code or "").strip().upper()
      for code in instrument_codes
      if str(code or "").strip()
    }
    if not codes:
      codes = {
        str(code or "").strip().upper()
        for code in list(runtime.context.instruments or [])
        if str(code or "").strip()
      }
    for code in codes:
      runtime.latest_market_data.pop(code, None)
      runtime._market_continuity_generations[code] = (
        runtime._market_continuity_generations.get(code, 0) + 1
      )
      generation = runtime._market_continuity_generations[code]
      runtime._active_market_continuity_losses[code] = str(reason)
      runtime._pending_market_invalidations[code] = str(reason)
      runtime._market_invalidation_checkpoints[code] = generation
      runtime._handled_market_invalidations.pop(code, None)
      # Install the routing gate synchronously. The strategy invalidation hook is
      # deliberately deferred to the serial consumer, but no in-flight Tick may
      # route an intent in the meantime.
      runtime._market_fail_closed_codes[code] = str(reason)
      install_durable_gate = getattr(
        runtime.state_manager,
        "require_market_continuity_reconciliation",
        None,
      )
      if callable(install_durable_gate):
        try:
          install_durable_gate(code, str(reason))
        except Exception:
          self.logger.exception(
            "持久化行情连续性门禁安装失败: run_id=%s instrument=%s",
            runtime.run_id,
            code,
          )

  @staticmethod
  def _drain_runtime_market_queue(
    runtime: StrategyRuntime,
  ) -> tuple[int, set[str]]:
    dropped = 0
    affected: set[str] = set()
    while True:
      try:
        queued = runtime.market_event_queue.get_nowait()
      except asyncio.QueueEmpty:
        break
      try:
        dropped += 1
        data = queued.data if isinstance(queued, RuntimeMarketEvent) else queued[1]
        code = StrategyExecutor._runtime_market_event_code(data)
        if code:
          affected.add(code)
      finally:
        runtime.market_event_queue.task_done()
    runtime.market_events_dropped += dropped
    return dropped, affected

  @staticmethod
  def _drain_runtime_control_queue_after_fail_stop(
    runtime: StrategyRuntime,
    *,
    reason: str,
  ) -> None:
    """Balance queued work after a fatal serial-consumer durability failure."""

    while True:
      try:
        event_type, data = runtime.event_queue.get_nowait()
      except asyncio.QueueEmpty:
        break
      try:
        completion = None
        if event_type in {"durable_order", "durable_trade"}:
          _payload, completion = data
        elif event_type == "universe" and isinstance(data, dict):
          completion = data.get("future")
        if completion is not None and not completion.done():
          completion.set_exception(RuntimeConsumerUnavailable(reason))
      finally:
        runtime.event_queue.task_done()
    StrategyExecutor._drain_runtime_market_queue(runtime)

  def _enqueue_runtime_market_event(
    self,
    runtime: StrategyRuntime,
    event_type: str,
    data: Any,
  ) -> None:
    # Historical replays are lossless and already drive their own serial clock.
    if runtime.context.mode == StrategyRunMode.BACKTEST:
      self._put_runtime_control_event_nowait(runtime, (event_type, data))
      return

    if event_type == "tick" and not self._observe_runtime_market_transport(
      runtime,
      data,
    ):
      return

    code = self._runtime_market_event_code(data)
    if runtime.market_event_queue.full():
      dropped, affected = self._drain_runtime_market_queue(runtime)
      if code:
        affected.add(code)
      runtime.market_event_overflows += 1
      self._mark_runtime_market_continuity_lost(
        runtime,
        affected,
        reason="MARKET_EVENT_QUEUE_OVERFLOW",
      )
      self.logger.warning(
        "策略实时行情队列过载，已清空积压并失效观察窗: "
        "run_id=%s dropped=%s instruments=%s",
        runtime.run_id,
        dropped,
        sorted(affected),
      )

    queued = RuntimeMarketEvent(
      event_type=event_type,
      data=data,
      enqueued_at=monotonic(),
    )
    try:
      runtime.market_event_queue.put_nowait(queued)
    except asyncio.QueueFull:
      runtime.market_events_dropped += 1
      self._mark_runtime_market_continuity_lost(
        runtime,
        [code],
        reason="MARKET_EVENT_QUEUE_OVERFLOW",
      )
      return
    runtime.market_events_enqueued += 1
    runtime.market_queue_high_watermark = max(
      runtime.market_queue_high_watermark,
      runtime.market_event_queue.qsize(),
    )
    runtime._event_queue_wakeup.set()

  async def _apply_pending_runtime_market_invalidations(
    self,
    runtime: StrategyRuntime,
  ) -> None:
    pending = dict(runtime._pending_market_invalidations)
    runtime._pending_market_invalidations.clear()
    strategy = runtime.strategy
    hook = getattr(strategy, "invalidate_realtime_market_window", None)
    for code, reason in pending.items():
      generation = runtime._market_continuity_generations.get(code, 0)
      handled = False
      try:
        handled = bool(hook(code, reason=reason)) if callable(hook) else False
      except Exception:
        self.logger.exception(
          "策略行情观察窗失效钩子执行失败: run_id=%s instrument=%s",
          runtime.run_id,
          code,
        )
      if handled:
        first_checkpoint_attempt = (
          runtime._handled_market_invalidations.get(code) != generation
        )
        runtime._handled_market_invalidations[code] = generation
        if first_checkpoint_attempt:
          runtime.market_window_invalidations += 1
        for intent in list(runtime.pending_approvals.values()):
          metadata = dict(intent.metadata or {})
          if (
            intent.direction != TradeIntentDirection.BUY
            or intent.execution_mode != TradeIntentExecutionMode.MANUAL_CONFIRM
            or str(metadata.get("t_trade_role") or "").lower() != "entry"
            or str(intent.instrument_code or "").strip().upper() != code
          ):
            continue
          try:
            await self._reject_pending_approval(
              runtime,
              intent,
              status="EXPIRED",
              reason="MARKET_DATA_CONTINUITY_LOST",
              message="信号行情连续性已失效，请等待观察窗重新预热后的新信号",
            )
          except Exception:
            runtime._pending_market_invalidations[code] = reason
            self.logger.exception(
              "做 T 待确认信号失效收敛失败，保持行情门禁: "
              "run_id=%s instrument=%s intent_id=%s",
              runtime.run_id,
              code,
              intent.intent_id,
            )
      else:
        runtime._market_fail_closed_codes[code] = reason

    if not runtime._market_invalidation_checkpoints:
      return
    checkpoint = getattr(
      runtime.state_manager,
      "checkpoint_strategy_state_changes",
      None,
    )
    saved = False
    if callable(checkpoint):
      try:
        saved = bool(await checkpoint())
      except Exception:
        self.logger.exception(
          "策略行情观察窗失效快照保存失败: run_id=%s",
          runtime.run_id,
        )
    if not saved:
      self.logger.error(
        "策略行情观察窗失效尚未持久化，保持交易门禁: run_id=%s instruments=%s",
        runtime.run_id,
        sorted(runtime._market_invalidation_checkpoints),
      )
      return

    clear_candidates: List[tuple[str, int]] = []
    for code, generation in list(runtime._market_invalidation_checkpoints.items()):
      if (
        runtime._market_continuity_generations.get(code, 0) == generation
        and code not in runtime._pending_market_invalidations
      ):
        if runtime._handled_market_invalidations.get(code) == generation:
          clear_candidates.append((code, generation))
        else:
          # A continuity-blind strategy can never prove a rebuilt observation
          # window.  Its durable and process-local gates intentionally remain,
          # but no repeated checkpoint attempt is useful in this generation.
          runtime._market_invalidation_checkpoints.pop(code, None)

    if not clear_candidates:
      return

    clear_durable_gate = getattr(
      runtime.state_manager,
      "clear_market_continuity_reconciliation",
      None,
    )
    try:
      if callable(clear_durable_gate):
        for code, _generation in clear_candidates:
          clear_durable_gate(code)
    except Exception:
      self.logger.exception(
        "清除持久化行情连续性门禁失败，保持运行时门禁: run_id=%s",
        runtime.run_id,
      )
      return

    # Phase two publishes the gate removal only after phase one durably stored
    # the cleared sample window/rewarm marker while the gate was still present.
    # A crash or commit-unknown result therefore always restores at least one
    # safe barrier.
    clear_saved = False
    force_save = getattr(runtime.state_manager, "force_save", None)
    if callable(force_save):
      try:
        clear_saved = bool(await force_save())
      except Exception:
        self.logger.exception(
          "行情连续性门禁清除快照保存失败: run_id=%s",
          runtime.run_id,
        )
    if not clear_saved:
      self.logger.error(
        "行情连续性门禁清除尚未持久化，保持运行时门禁: run_id=%s instruments=%s",
        runtime.run_id,
        sorted(code for code, _generation in clear_candidates),
      )
      return

    for code, generation in clear_candidates:
      if (
        runtime._market_continuity_generations.get(code, 0) == generation
        and code not in runtime._pending_market_invalidations
        and runtime._handled_market_invalidations.get(code) == generation
      ):
        runtime._market_invalidation_checkpoints.pop(code, None)
        runtime._market_fail_closed_codes.pop(code, None)
        runtime._handled_market_invalidations.pop(code, None)

  def _runtime_market_continuity_failure(
    self,
    runtime: StrategyRuntime,
    instrument_code: str,
  ) -> Optional[tuple[str, str]]:
    """Return the fail-closed reason for an intent's market-data lineage."""

    if runtime.context.mode == StrategyRunMode.BACKTEST:
      return None
    code = str(instrument_code or "").strip().upper()
    if not code:
      return None

    processing = runtime._processing_market_events.get(code)
    if processing is not None:
      expected_generation, enqueued_at = processing
      current_generation = runtime._market_continuity_generations.get(code, 0)
      if current_generation != expected_generation:
        reason = runtime._active_market_continuity_losses.get(
          code,
          "MARKET_DATA_CONTINUITY_GENERATION_CHANGED",
        )
        return "MARKET_DATA_CONTINUITY_LOST", (
          f"{code} 行情连续性已变化（{reason}），旧行情不得生成或执行交易意图"
        )
      processing_age = max(0.0, monotonic() - enqueued_at)
      if processing_age > MARKET_STREAM_MAX_CAPTURE_AGE_SECONDS:
        runtime.market_events_expired += 1
        runtime.market_events_dropped += 1
        self._mark_runtime_market_continuity_lost(
          runtime,
          [code],
          reason="MARKET_EVENT_PROCESSING_EXPIRED",
        )

    reason = runtime._pending_market_invalidations.get(code)
    if reason is None:
      reason = runtime._market_fail_closed_codes.get(code)
    if reason is None:
      reason = runtime._active_market_continuity_losses.get(code)
    if reason is None:
      return None
    return "MARKET_DATA_CONTINUITY_LOST", (
      f"{code} 行情连续性失效（{reason}），等待观察窗完整预热"
    )

  async def _next_runtime_event(
    self,
    runtime: StrategyRuntime,
    *,
    timeout: float = 1.0,
  ) -> Optional[tuple[asyncio.Queue, str, Any, Optional[float]]]:
    """Get one event while always checking durable/control work first."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, timeout)
    while True:
      if (
        runtime.status not in {ExecutionStatus.RUNNING, ExecutionStatus.PAUSED}
        or self._shutdown_event.is_set()
      ):
        return None
      try:
        event_type, data = runtime.event_queue.get_nowait()
        return runtime.event_queue, event_type, data, None
      except asyncio.QueueEmpty:
        pass
      try:
        queued = runtime.market_event_queue.get_nowait()
        if isinstance(queued, RuntimeMarketEvent):
          return (
            runtime.market_event_queue,
            queued.event_type,
            queued.data,
            queued.enqueued_at,
          )
        event_type, data = queued
        return runtime.market_event_queue, event_type, data, None
      except asyncio.QueueEmpty:
        pass

      remaining = deadline - loop.time()
      if remaining <= 0:
        return None
      runtime._event_queue_wakeup.clear()
      if not runtime.event_queue.empty() or not runtime.market_event_queue.empty():
        continue
      try:
        await asyncio.wait_for(
          runtime._event_queue_wakeup.wait(),
          timeout=min(remaining, 0.05),
        )
      except asyncio.TimeoutError:
        continue

  async def _quiesce_runtime_tasks(
    self,
    runtime: StrategyRuntime,
    *,
    owner_task: Optional[asyncio.Task] = None,
  ) -> None:
    """Stop producers, then let the serial consumer finish its current item."""
    current_task = asyncio.current_task()
    if (
      runtime.task is not None
      and runtime.task is not current_task
      and runtime.task is not owner_task
      and not runtime.task.done()
    ):
      runtime.task.cancel()
      done, pending = await asyncio.wait({runtime.task}, timeout=5.0)
      if pending:
        self.logger.warning("策略任务 %s 停止超时,再次执行取消", runtime.run_id)
        runtime.task.cancel()
        done, pending = await asyncio.wait({runtime.task}, timeout=1.0)
      if pending:
        raise RuntimeError(f"策略任务未能收敛: {runtime.run_id}")
      await asyncio.gather(*done, return_exceptions=True)

    event_task = runtime.event_task
    if (
      event_task is not None
      and event_task is not current_task
      and event_task is not owner_task
      and not event_task.done()
    ):
      # A consumer blocked on queue.get() observes STOPPING at its one-second
      # timeout. A consumer already processing an item exits at the next loop
      # boundary, after balancing task_done for the acquired item.
      done, pending = await asyncio.wait({event_task}, timeout=5.0)
      if pending:
        self.logger.warning(
          "策略事件任务 %s 未在停止窗口内收敛,执行取消", runtime.run_id
        )
        event_task.cancel()
        done, pending = await asyncio.wait({event_task}, timeout=1.0)
      if pending:
        raise RuntimeError(f"策略事件任务未能收敛: {runtime.run_id}")
      await asyncio.gather(*done, return_exceptions=True)

    if event_task is not None and (
      event_task is current_task or event_task is owner_task
    ):
      return
    while True:
      try:
        event_type, data = runtime.event_queue.get_nowait()
      except asyncio.QueueEmpty:
        break
      try:
        completion = None
        if event_type in {"durable_order", "durable_trade"}:
          _payload, completion = data
        elif event_type == "universe" and isinstance(data, dict):
          completion = data.get("future")
        if completion is not None and not completion.done():
          completion.set_exception(
            RuntimeConsumerUnavailable(f"策略运行已停止消费事件: {runtime.run_id}")
          )
      finally:
        runtime.event_queue.task_done()
    self._drain_runtime_market_queue(runtime)

  async def _release_runtime_adapter(self, runtime: StrategyRuntime) -> None:
    """Release exactly the AdapterManager reference owned by this runtime."""

    if not runtime._adapter_ref_acquired:
      return
    await adapter_manager.release_adapter_for_mode(runtime.context.mode.value.lower())
    # AdapterManager keeps the final reference until disconnect succeeds, so
    # this flag remains set when the awaited release raises and can be retried.
    runtime._adapter_ref_acquired = False

  async def _retry_previous_cleanup_before_start(
    self,
    runtime: StrategyRuntime,
  ) -> bool:
    """Finish the previous ERROR generation before any ownership flag resets."""

    try:
      if runtime._startup_abort_task is not None or runtime.task is None:
        await self._ensure_startup_abort(runtime)
        cleanup_complete = runtime._startup_abort_complete
      else:
        await self._ensure_terminal_cleanup(runtime)
        cleanup_complete = runtime._terminal_cleanup_complete
    except asyncio.CancelledError:
      raise
    except Exception as exc:
      self.logger.error("重试旧运行资源清理失败: %s, %s", runtime.run_id, exc)
      return False

    return self._runtime_cleanup_converged(runtime, cleanup_complete)

  @staticmethod
  def _runtime_cleanup_converged(
    runtime: StrategyRuntime,
    cleanup_complete: bool,
  ) -> bool:
    """Verify that a cleanup flag corresponds to actually closed resources."""

    state_manager = runtime.state_manager
    state_tasks_stopped = bool(
      state_manager is None
      or (
        not state_manager._running
        and (
          state_manager._snapshot_task is None or state_manager._snapshot_task.done()
        )
        and (
          state_manager._state_sync_task is None
          or state_manager._state_sync_task.done()
        )
        and state_manager._state_queue is None
      )
    )
    broker_connected = getattr(runtime.broker, "is_connected", False) is True
    runtime_task_stopped = runtime.task is None or runtime.task.done()
    event_task_stopped = runtime.event_task is None or runtime.event_task.done()
    control_queue_drained = bool(
      runtime.event_queue.empty()
      and int(getattr(runtime.event_queue, "_unfinished_tasks", 0) or 0) == 0
    )
    market_queue_drained = bool(
      runtime.market_event_queue.empty()
      and int(getattr(runtime.market_event_queue, "_unfinished_tasks", 0) or 0) == 0
    )
    return bool(
      cleanup_complete
      and state_tasks_stopped
      and runtime_task_stopped
      and event_task_stopped
      and not runtime._adapter_ref_acquired
      and not broker_connected
      and not runtime.realtime_subscription_ids
      and control_queue_drained
      and market_queue_drained
    )

  async def _ensure_startup_abort(self, runtime: StrategyRuntime) -> None:
    """Run startup cleanup in an owned task so caller cancellation cannot kill it."""

    task = runtime._startup_abort_task
    if task is None or task.done():
      task = asyncio.create_task(
        self._abort_failed_start(runtime),
        name=f"strategy-startup-abort:{runtime.run_id}",
      )
      runtime._startup_abort_task = task
    await asyncio.shield(task)

  async def _abort_failed_start(self, runtime: StrategyRuntime) -> None:
    """Roll back a partially acquired startup without persisting half-state."""

    async with runtime._startup_abort_lock:
      if runtime._startup_abort_complete:
        if self._runtime_cleanup_converged(runtime, True):
          return
        runtime._startup_abort_complete = False
      runtime.status = ExecutionStatus.ERROR
      cleanup_errors: List[str] = []

      try:
        await self._quiesce_runtime_tasks(runtime)
      except Exception as exc:
        cleanup_errors.append("tasks")
        self.logger.error("启动回滚停止任务失败: %s, %s", runtime.run_id, exc)
      try:
        await self._clear_realtime_subscriptions(runtime)
      except Exception as exc:
        cleanup_errors.append("subscriptions")
        self.logger.error("启动回滚取消行情订阅失败: %s, %s", runtime.run_id, exc)
      try:
        if runtime.state_manager:
          await runtime.state_manager.abort_without_final_snapshot(runtime.strategy)
      except Exception as exc:
        cleanup_errors.append("state_manager")
        self.logger.error("启动回滚中止状态管理器失败: %s, %s", runtime.run_id, exc)
      try:
        if runtime.broker:
          await runtime.broker.disconnect()
      except Exception as exc:
        cleanup_errors.append("broker")
        self.logger.error("启动回滚断开 Broker 失败: %s, %s", runtime.run_id, exc)
      try:
        if runtime.strategy:
          await runtime.strategy.stop()
      except Exception as exc:
        cleanup_errors.append("strategy")
        self.logger.error("启动回滚停止策略失败: %s, %s", runtime.run_id, exc)
      try:
        await self._release_runtime_adapter(runtime)
      except Exception as exc:
        cleanup_errors.append("adapter")
        self.logger.error("启动回滚释放数据适配器失败: %s, %s", runtime.run_id, exc)
      try:
        if runtime.log_manager and runtime.strategy:
          runtime.log_manager.detach_handler(
            run_id=runtime.run_id,
            logger=runtime.strategy.logger,
          )
      except Exception as exc:
        cleanup_errors.append("log_handler")
        self.logger.error("启动回滚移除日志 Handler 失败: %s, %s", runtime.run_id, exc)
      try:
        if runtime.log_manager:
          await runtime.log_manager.flush(runtime.run_id)
      except Exception as exc:
        cleanup_errors.append("log_flush")
        self.logger.error("启动回滚刷新日志失败: %s, %s", runtime.run_id, exc)

      # A callback racing with disconnect may have queued one last item after
      # the first drain. No consumer exists after a failed startup.
      try:
        await self._quiesce_runtime_tasks(runtime)
      except Exception as exc:
        cleanup_errors.append("final_drain")
        self.logger.error("启动回滚最终排空任务失败: %s, %s", runtime.run_id, exc)
      runtime._startup_abort_complete = not cleanup_errors
      if cleanup_errors:
        self.logger.error(
          "启动回滚尚未完全收敛，可安全重试: %s, pending=%s",
          runtime.run_id,
          sorted(set(cleanup_errors)),
        )
      runtime.status = ExecutionStatus.ERROR

  async def _ensure_terminal_cleanup(self, runtime: StrategyRuntime) -> None:
    """Run ERROR teardown independently from the failing strategy task."""

    task = runtime._terminal_cleanup_task
    if task is None or task.done():
      current_task = asyncio.current_task()
      owner_task = runtime.task if current_task is runtime.task else None
      task = asyncio.create_task(
        self._cleanup_runtime_after_error(runtime, owner_task=owner_task),
        name=f"strategy-error-cleanup:{runtime.run_id}",
      )
      runtime._terminal_cleanup_task = task
    await asyncio.shield(task)

  async def _cleanup_runtime_after_error(
    self,
    runtime: StrategyRuntime,
    *,
    owner_task: Optional[asyncio.Task] = None,
  ) -> None:
    """Release an operational runtime while preserving its ERROR state."""

    async with runtime._terminal_cleanup_lock:
      if runtime._terminal_cleanup_complete:
        if self._runtime_cleanup_converged(runtime, True):
          runtime.status = ExecutionStatus.ERROR
          return
        runtime._terminal_cleanup_complete = False
      if runtime._startup_abort_complete:
        if self._runtime_cleanup_converged(runtime, True):
          runtime.status = ExecutionStatus.ERROR
          return
        # A late callback can invalidate a previously completed startup abort.
        # Retry the no-snapshot cleanup rather than entering terminal snapshot
        # semantics for a generation that never became operational.
        runtime._startup_abort_complete = False
        await self._abort_failed_start(runtime)
        runtime.status = ExecutionStatus.ERROR
        return
      runtime.status = ExecutionStatus.ERROR
      cleanup_errors: List[str] = []
      final_snapshot_ready = True

      # An approval that passed the RUNNING gate owns this lock through its
      # durable status transition and routing attempt. Let it converge before
      # tearing down the consumer or taking the final snapshot.
      async with runtime.approval_lock:
        pass

      try:
        await self._quiesce_runtime_tasks(runtime, owner_task=owner_task)
      except Exception as exc:
        cleanup_errors.append("tasks")
        final_snapshot_ready = False
        self.logger.error("异常终止停止任务失败: %s, %s", runtime.run_id, exc)
      try:
        await self._clear_realtime_subscriptions(runtime)
      except Exception as exc:
        cleanup_errors.append("subscriptions")
        final_snapshot_ready = False
        self.logger.error("异常终止取消行情订阅失败: %s, %s", runtime.run_id, exc)
      try:
        # Disconnecting subscriptions may race with one last callback. Drain it
        # before declaring the final durable snapshot authoritative.
        await self._quiesce_runtime_tasks(runtime, owner_task=owner_task)
      except Exception as exc:
        cleanup_errors.append("post_subscription_tasks")
        final_snapshot_ready = False
        self.logger.error("异常终止排空末尾事件失败: %s, %s", runtime.run_id, exc)
      try:
        if runtime.strategy:
          await runtime.strategy.stop()
      except Exception as exc:
        cleanup_errors.append("strategy")
        final_snapshot_ready = False
        self.logger.error("异常终止停止策略失败: %s, %s", runtime.run_id, exc)
      try:
        await self._coordinate_terminal_session_checkpoint(
          runtime,
          cause="ERROR",
        )
        await self._coordinate_backtest_terminal_checkpoint(
          runtime,
          cause="ERROR",
        )
        await self._flush_t_trade_opportunity_diagnostics(runtime)
      except Exception as exc:
        cleanup_errors.append("t_trade_opportunity_diagnostics")
        # Do not release the runtime or let a later generic final snapshot
        # imply success when a durable diagnostic batch could not cross its
        # commit -> materialize -> acknowledgement boundary.
        final_snapshot_ready = False
        self.logger.error(
          "异常终止刷新做 T 机会诊断失败: %s, %s",
          runtime.run_id,
          exc,
        )
      try:
        # The terminal diagnostic batch needs the sync consumer alive so its
        # final CAS can include the last strategy state delta.
        if runtime.state_manager:
          await runtime.state_manager.stop_state_sync(runtime.strategy)
      except Exception as exc:
        cleanup_errors.append("state_sync")
        final_snapshot_ready = False
        self.logger.error("异常终止停止状态同步失败: %s, %s", runtime.run_id, exc)
      try:
        if runtime.performance_recorder:
          await runtime.performance_recorder.flush()
      except Exception as exc:
        cleanup_errors.append("performance")
        self.logger.error("异常终止刷新绩效失败: %s, %s", runtime.run_id, exc)

      final_snapshot_saved = bool(
        final_snapshot_ready and runtime.state_manager is None
      )
      if final_snapshot_ready and runtime.state_manager:
        try:
          await runtime.state_manager.stop()
          final_snapshot_saved = True
        except Exception as exc:
          cleanup_errors.append("state_manager")
          self.logger.error("异常终止保存最终状态失败: %s, %s", runtime.run_id, exc)
      elif not final_snapshot_ready:
        cleanup_errors.append("final_snapshot_deferred")
        self.logger.error(
          "异常终止前置资源未收敛，延后最终状态快照: %s",
          runtime.run_id,
        )

      if final_snapshot_saved:
        try:
          if runtime.broker:
            await runtime.broker.disconnect()
        except Exception as exc:
          cleanup_errors.append("broker")
          self.logger.error("异常终止断开 Broker 失败: %s, %s", runtime.run_id, exc)
        try:
          await self._release_runtime_adapter(runtime)
        except Exception as exc:
          cleanup_errors.append("adapter")
          self.logger.error("异常终止释放数据适配器失败: %s, %s", runtime.run_id, exc)
        try:
          # Broker/adapter disconnect may synchronously emit one last callback.
          # Never carry that control or market event into a later generation.
          await self._quiesce_runtime_tasks(runtime, owner_task=owner_task)
        except Exception as exc:
          cleanup_errors.append("post_disconnect_tasks")
          self.logger.error(
            "异常终止断开资源后排空事件失败: %s, %s",
            runtime.run_id,
            exc,
          )
      else:
        self.logger.error(
          "最终状态尚未形成权威快照，保留 Broker/Adapter 所有权: %s",
          runtime.run_id,
        )
      try:
        if runtime.log_manager and runtime.strategy:
          runtime.log_manager.detach_handler(
            run_id=runtime.run_id,
            logger=runtime.strategy.logger,
          )
      except Exception as exc:
        cleanup_errors.append("log_handler")
        self.logger.error("异常终止移除日志 Handler 失败: %s, %s", runtime.run_id, exc)
      try:
        if runtime.log_manager:
          await runtime.log_manager.flush(runtime.run_id)
      except Exception as exc:
        cleanup_errors.append("log_flush")
        self.logger.error("异常终止刷新日志失败: %s, %s", runtime.run_id, exc)

      runtime._terminal_cleanup_complete = not cleanup_errors
      if cleanup_errors:
        self.logger.error(
          "异常终止资源尚未完全收敛，可安全重试: %s, pending=%s",
          runtime.run_id,
          sorted(set(cleanup_errors)),
        )
      runtime.status = ExecutionStatus.ERROR

  async def stop(self, run_id: str, *, force: bool = False) -> bool:
    if run_id not in self.runs:
      return False
    runtime = self.runs[run_id]
    return await self._run_lifecycle_operation(
      runtime,
      "stop",
      lambda: self._stop_runtime(run_id, force=force),
    )

  async def _stop_runtime(self, run_id: str, *, force: bool = False) -> bool:
    """
    停止策略运行并清理资源

    Args:
        run_id: 运行实例ID

    Returns:
        bool: 是否停止成功

    Note:
        - 负责资源清理（Broker、DataAdapter、Task）
        - 收集最终指标
        - 指标持久化由调用方负责
    """
    if run_id not in self.runs:
      return False

    runtime = self.runs[run_id]

    previous_status = runtime.status

    if runtime.status == ExecutionStatus.STOPPED:
      self.opportunity_observability.forget_run(run_id)
      return True
    if runtime.status == ExecutionStatus.STOPPING:
      self.logger.error("策略停止状态无活动 owner，拒绝提前报告成功: %s", run_id)
      return False
    if previous_status == ExecutionStatus.ERROR and (
      runtime._startup_abort_task is not None or runtime._startup_abort_complete
    ):
      try:
        await self._ensure_startup_abort(runtime)
      except asyncio.CancelledError:
        raise
      except Exception as exc:
        self.logger.error("停止失败启动代时清理异常: %s, %s", run_id, exc)
        return False
      if not self._runtime_cleanup_converged(
        runtime,
        runtime._startup_abort_complete,
      ):
        self.logger.error("失败启动代资源尚未收敛，拒绝标记已停止: %s", run_id)
        return False
      runtime.status = ExecutionStatus.STOPPED
      self.opportunity_observability.forget_run(run_id)
      self.logger.info("失败启动代已安全停止（未写最终快照）: %s", run_id)
      return True
    active_exit_plans = runtime.exit_plan_book.active_plans()
    if active_exit_plans and not force:
      self._runtime_log(
        runtime,
        "WARNING",
        "仍有自动退出计划保护未退出仓位，运行保持监控；请先进入 DRAINING",
      )
      return False
    lifecycle_blocker = self._runtime_lifecycle_blocker(runtime)
    if lifecycle_blocker and not force:
      self._runtime_log(
        runtime,
        "WARNING",
        f"运行仍有未完成交易生命周期，拒绝停止: {lifecycle_blocker}",
      )
      return False

    try:
      async with runtime.approval_lock:
        if not force:
          active_exit_plans = runtime.exit_plan_book.active_plans()
          if active_exit_plans:
            self._runtime_log(
              runtime,
              "WARNING",
              "审批并发期间出现自动退出计划，拒绝停止运行",
            )
            return False
          lifecycle_blocker = self._runtime_lifecycle_blocker(runtime)
          if lifecycle_blocker:
            self._runtime_log(
              runtime,
              "WARNING",
              f"审批并发期间新增交易生命周期，拒绝停止: {lifecycle_blocker}",
            )
            return False
        runtime.status = ExecutionStatus.STOPPING
      await self._quiesce_runtime_tasks(runtime)
      await self._clear_realtime_subscriptions(runtime)
      await self._finalize_t_trade_candidate_outcomes(runtime)

      # 停止策略
      if runtime.strategy:
        # 移除日志广播 Handler
        if runtime.log_manager:
          runtime.log_manager.detach_handler(
            run_id=runtime.run_id,
            logger=runtime.strategy.logger,
          )
        await runtime.strategy.stop()

      await self._coordinate_terminal_session_checkpoint(
        runtime,
        cause="CANCELLED",
      )
      await self._coordinate_backtest_terminal_checkpoint(
        runtime,
        cause="CANCELLED",
      )
      await self._flush_t_trade_opportunity_diagnostics(runtime)

      # The terminal diagnostic batch must drain the final strategy delta
      # before the state-sync consumer is stopped.
      if runtime.state_manager:
        await runtime.state_manager.stop_state_sync(runtime.strategy)

      # 更新指标
      if runtime.metrics and runtime.broker:
        runtime.metrics.end_time = time_utils.now()
        # 从 broker 获取性能指标
        if hasattr(runtime.broker, "get_performance_metrics"):
          perf_metrics = runtime.broker.get_performance_metrics()
          if inspect.isawaitable(perf_metrics):
            perf_metrics = await perf_metrics
          if not isinstance(perf_metrics, dict):
            perf_metrics = {}
          runtime.metrics.max_drawdown = perf_metrics.get("max_drawdown", 0.0)
          runtime.metrics.win_rate = perf_metrics.get("win_rate", 0.0)
          runtime.metrics.sharpe_ratio = perf_metrics.get("sharpe_ratio", 0.0)
          runtime.metrics.total_pnl = (
            perf_metrics.get("final_equity", runtime.metrics.initial_capital)
            - runtime.metrics.initial_capital
          )
          runtime.metrics.current_capital = perf_metrics.get(
            "final_equity", runtime.metrics.initial_capital
          )
          runtime.metrics.trades_executed = perf_metrics.get("total_trades", 0)

      # 停止状态管理器
      if runtime.performance_recorder:
        await runtime.performance_recorder.flush()
      if runtime.state_manager:
        await runtime.state_manager.stop()

      # Final snapshot is authoritative; only then release the broker and data
      # adapter so no callback can mutate state after the persisted stop point.
      if runtime.broker:
        await runtime.broker.disconnect()
      await self._release_runtime_adapter(runtime)
      await self._quiesce_runtime_tasks(runtime)

      if runtime.context.parameters.get(
        "limit_up_board_replay"
      ) and previous_status not in {ExecutionStatus.COMPLETED, ExecutionStatus.ERROR}:
        await self._persist_limit_up_board_replay_terminal(
          runtime,
          status="CANCELLED",
          error_message="REPLAY_CANCELLED",
        )

      runtime.status = ExecutionStatus.STOPPED
      self.opportunity_observability.forget_run(run_id)

      self.logger.info(f"策略运行停止成功: {run_id}")
      return True

    except asyncio.CancelledError:
      runtime.status = ExecutionStatus.ERROR
      runtime.error_message = "策略停止任务被取消"
      self.logger.error("停止策略运行被取消: %s", run_id)
      try:
        await self._ensure_terminal_cleanup(runtime)
      except asyncio.CancelledError:
        # The runtime-owned cleanup task is shielded and continues independently.
        pass
      raise
    except Exception as e:
      runtime.status = ExecutionStatus.ERROR
      runtime.error_message = str(e)
      self.logger.error(f"停止策略运行失败: {run_id}, 错误: {e}")
      await self._ensure_terminal_cleanup(runtime)
      return False

  async def _flush_t_trade_opportunity_diagnostics(
    self,
    runtime: StrategyRuntime,
  ) -> None:
    if not self._uses_t_trade_opportunity_runtime(runtime):
      return
    # This is shared by normal completion, error cleanup, and explicit
    # cancellation.  It may flush a service-local buffer, but it must never
    # acknowledge the RuntimeState outbox: only coordinator FINALIZE does
    # that atomically with the SEALED checkpoint.
    account_id = str(runtime.context.parameters.get("account_id") or "").strip()
    if not account_id:
      return
    errors: list[Exception] = []
    if self._runtime_state_checkpoint_policy(runtime) != (
      RUNTIME_STATE_CHECKPOINT_POLICY_DAY_BATCH
    ):
      flush_diagnostics = getattr(
        self.opportunity_runtime_service,
        "flush_diagnostics",
        None,
      )
      if not callable(flush_diagnostics):
        flush_diagnostics = getattr(
          self.opportunity_runtime_service,
          "flush_diagnostics_with_receipt",
          None,
        )
      if callable(flush_diagnostics):
        try:
          await flush_diagnostics(
            account_id=account_id,
            strategy_run_id=runtime.run_id,
          )
        except Exception as exc:
          errors.append(exc)
    flush_notices = getattr(
      self.opportunity_update_service,
      "flush_opportunity_notices",
      None,
    )
    if callable(flush_notices):
      try:
        await flush_notices(
          account_id=account_id,
          strategy_run_id=runtime.run_id,
        )
      except Exception as exc:
        errors.append(exc)
    if errors:
      raise RuntimeError("做 T 机会诊断或更新通知收尾失败") from errors[0]

  async def pause(self, run_id: str) -> bool:
    """
    暂停策略运行

    Args:
        run_id: 运行实例ID

    Returns:
        bool: 是否暂停成功
    """
    if run_id not in self.runs:
      return False

    runtime = self.runs[run_id]

    async with runtime.approval_lock:
      if runtime.status != ExecutionStatus.RUNNING:
        return False
      if runtime.exit_plan_book.active_plans():
        self._runtime_log(
          runtime,
          "WARNING",
          "仍有自动退出计划保护未退出仓位，不能暂停行情监控",
        )
        return False
      lifecycle_blocker = self._runtime_lifecycle_blocker(runtime)
      if lifecycle_blocker:
        self._runtime_log(
          runtime,
          "WARNING",
          f"运行仍有未完成交易生命周期，拒绝暂停: {lifecycle_blocker}",
        )
        return False
      runtime.status = ExecutionStatus.PAUSED
    self.logger.info(f"策略运行已暂停: {run_id}")
    return True

  async def resume(self, run_id: str) -> bool:
    """
    恢复策略运行

    Args:
        run_id: 运行实例ID

    Returns:
        bool: 是否恢复成功
    """
    if run_id not in self.runs:
      return False

    runtime = self.runs[run_id]

    if runtime.status != ExecutionStatus.PAUSED:
      return False

    runtime.status = ExecutionStatus.RUNNING
    if runtime.event_task is None or runtime.event_task.done():
      if runtime.state_manager is None:
        runtime.status = ExecutionStatus.PAUSED
        self.logger.warning("策略运行缺少状态管理器，无法恢复事件消费: %s", run_id)
        return False
      runtime.event_task = asyncio.create_task(self._process_event_queue(runtime))
    self.logger.info(f"策略运行已恢复: {run_id}")
    return True

  async def delete(self, run_id: str) -> bool:
    """删除策略运行"""
    if run_id not in self.runs:
      return False

    # 先停止实例
    if not await self.stop(run_id):
      return False

    # 从内存中删除
    del self.runs[run_id]

    self.logger.info(f"策略运行已删除: {run_id}")
    return True

  def get(self, run_id: str) -> Optional[StrategyRuntime]:
    """获取策略运行"""
    return self.runs.get(run_id)

  def get_all(self) -> List[StrategyRuntime]:
    """获取所有策略运行"""
    return list(self.runs.values())

  async def reconcile_instruments(
    self,
    run_id: str,
    instruments: List[str],
    *,
    instrument_metadata: Optional[Dict[str, Dict[str, Any]]] = None,
    parameters: Optional[Dict[str, Any]] = None,
    configuration_changed: bool = False,
  ) -> Dict[str, List[str]]:
    """串行调整运行中策略的标的池和实时行情订阅。"""

    runtime = self.runs.get(run_id)
    if runtime is None:
      raise ValueError(f"策略运行不存在: {run_id}")
    if (
      runtime.context.mode == StrategyRunMode.BACKTEST
      and not runtime.context.parameters.get("limit_up_board_replay")
    ):
      raise ValueError("仅账户级打板历史回放支持动态修改标的池")

    normalized = []
    for raw in instruments or []:
      code = str(raw or "").strip().upper()
      if code and code not in normalized:
        normalized.append(code)

    if runtime.context.mode != StrategyRunMode.BACKTEST and runtime.status in {
      ExecutionStatus.RUNNING,
      ExecutionStatus.PAUSED,
    }:
      future = asyncio.get_running_loop().create_future()
      await self._put_runtime_control_event(
        runtime,
        (
          "universe",
          {
            "instruments": normalized,
            "instrument_metadata": dict(instrument_metadata or {}),
            "parameters": dict(parameters) if parameters is not None else None,
            "configuration_changed": bool(configuration_changed),
            "future": future,
          },
        ),
      )
      return await future

    if runtime.context.mode == StrategyRunMode.BACKTEST:
      return await self._apply_backtest_instrument_reconcile(
        runtime,
        normalized,
        instrument_metadata=dict(instrument_metadata or {}),
      )

    current = list(runtime.context.instruments or [])
    previous_parameters = dict(runtime.context.parameters or {})
    if parameters is not None:
      runtime.context.parameters = dict(parameters)
    runtime.context.instruments = normalized
    if self._uses_t_trade_opportunity_runtime(runtime):
      try:
        staged_emission = self._build_t_trade_intent_emission_snapshot(
          runtime,
          normalized,
          instrument_metadata,
        )
      except Exception:
        runtime.context.parameters = previous_parameters
        runtime.context.instruments = current
        self._clear_t_trade_intent_emission_snapshot(runtime)
        raise
      self._publish_t_trade_intent_emission_snapshot(runtime, staged_emission)
    return {
      "added": [code for code in normalized if code not in current],
      "removed": [code for code in current if code not in normalized],
      "instruments": normalized,
    }

  def get_running(self) -> List[StrategyRuntime]:
    """获取运行中的策略运行"""
    return [
      runtime
      for runtime in self.runs.values()
      if runtime.status == ExecutionStatus.RUNNING
    ]

  async def stop_all_runs(self, timeout: float = 10.0) -> None:
    """停止所有策略运行

    Args:
        timeout: 总超时时间(秒),默认10秒
    """
    stop_tasks = {
      run_id: asyncio.create_task(
        self.stop(run_id, force=True),
        name=f"strategy-shutdown-stop:{run_id}",
      )
      for run_id in list(self.runs)
    }
    if not stop_tasks:
      return

    done, pending = await asyncio.wait(
      set(stop_tasks.values()),
      timeout=max(0.1, timeout),
    )
    for task in pending:
      task.cancel()
    if pending:
      # Let cancellation handlers install their runtime-owned cleanup tasks
      # before taking the task snapshot below.
      await asyncio.gather(*pending, return_exceptions=True)

    stop_outcomes: Dict[str, str] = {}
    for run_id, task in stop_tasks.items():
      if task not in done:
        stop_outcomes[run_id] = "stop timeout"
        continue
      try:
        stopped = bool(task.result())
      except BaseException as exc:
        stop_outcomes[run_id] = f"stop raised {type(exc).__name__}: {exc}"
        continue
      if not stopped:
        stop_outcomes[run_id] = "stop returned false"

    owned_cleanup_tasks = {
      task
      for runtime in self.runs.values()
      for task in (
        runtime._lifecycle_operation_task,
        runtime._startup_abort_task,
        runtime._terminal_cleanup_task,
      )
      if task is not None and not task.done()
    }
    failures: List[str] = []
    if owned_cleanup_tasks:
      cleanup_done, cleanup_pending = await asyncio.wait(
        owned_cleanup_tasks,
        timeout=max(0.1, timeout),
      )
      if cleanup_done:
        await asyncio.gather(*cleanup_done, return_exceptions=True)
      if cleanup_pending:
        failures.append(
          "owned lifecycle/cleanup timeout: "
          + ",".join(sorted(task.get_name() for task in cleanup_pending))
        )

    for run_id, runtime in self.runs.items():
      cleanup_complete = bool(
        runtime.status == ExecutionStatus.STOPPED
        or runtime._startup_abort_complete
        or runtime._terminal_cleanup_complete
      )
      if self._runtime_cleanup_converged(runtime, cleanup_complete):
        continue
      outcome = stop_outcomes.get(run_id)
      details = [f"status={runtime.status.value}", "resources not converged"]
      if outcome:
        details.insert(0, outcome)
      failures.append(f"{run_id}: " + "; ".join(details))

    if failures:
      raise RuntimeError("策略执行器关闭失败: " + "; ".join(failures))

  async def shutdown(self) -> None:
    """关闭执行器"""
    self._shutdown_event.set()
    await self.stop_all_runs()
    self.thread_pool.shutdown(wait=False)
    self.logger.info("策略执行器已关闭")

  async def _setup_broker_and_data(self, runtime: StrategyRuntime) -> None:
    """设置 Broker 和 DataAdapter"""
    mode = runtime.context.mode

    # 创建 Broker
    if mode == StrategyRunMode.BACKTEST:
      is_strict_tick_replay = bool(
        runtime.context.parameters.get("limit_up_board_replay")
        or runtime.context.parameters.get("t_trade_replay")
        or runtime.context.parameters.get("exit_plan_replay")
      )
      runtime.broker = BacktestBroker(
        account_id=runtime.run_id,
        initial_capital=runtime.context.initial_capital,
        commission_rate=float(
          runtime.context.parameters.get("commission_rate", 0.0003) or 0.0
        ),
        min_commission=float(
          runtime.context.parameters.get("minimum_commission", 5.0) or 0.0
        ),
        stamp_tax_rate=float(
          runtime.context.parameters.get("stamp_tax_rate", 0.0005) or 0.0
        ),
        transfer_fee_rate=float(
          runtime.context.parameters.get("transfer_fee_rate", 0.00001) or 0.0
        ),
        slippage_rate=float(
          runtime.context.parameters.get("slippage_rate", 0.0001) or 0.0
        ),
        participation_cap_pct=float(
          runtime.context.parameters.get("participation_cap_pct", 0.05) or 0.05
        ),
        book_depth_participation_pct=float(
          runtime.context.parameters.get(
            "book_depth_participation_pct",
            0.25,
          )
          or 0.25
        ),
        strict_book_depth=is_strict_tick_replay,
        no_queue_credit=is_strict_tick_replay,
        defer_new_orders_until_next_quote=is_strict_tick_replay,
      )
    elif mode == StrategyRunMode.PAPER:
      runtime.broker = SimulatorBroker(
        account_id=runtime.run_id,
        initial_capital=runtime.context.initial_capital,
      )
    else:  # LIVE
      runtime.broker = LiveBroker(
        account_id=str(runtime.context.parameters.get("account_id") or runtime.run_id),
        initial_capital=runtime.context.initial_capital,
      )

    # 使用 AdapterManager 获取数据适配器
    runtime.data_adapter = await adapter_manager.get_adapter_for_mode(mode)
    runtime._adapter_ref_acquired = True

    # 连接 Broker 和 DataAdapter（适配器可能已连接，会自动处理）
    broker_connected = await runtime.broker.connect()
    if not broker_connected:
      raise RuntimeError(f"Broker 连接失败: mode={mode.value}")
    adapter_connected = await adapter_manager.ensure_adapter_connected_for_mode(
      mode,
      runtime.data_adapter,
    )
    if not adapter_connected:
      raise RuntimeError(f"DataAdapter 连接失败: mode={mode.value}")

    # 订阅订单和成交回调
    order_subscription = runtime.broker.subscribe_order_updates(
      lambda order: self._put_runtime_control_event_nowait(runtime, ("order", order))
    )
    if inspect.isawaitable(order_subscription):
      await order_subscription
    trade_subscription = runtime.broker.subscribe_trade_updates(
      lambda trade: self._put_runtime_control_event_nowait(runtime, ("trade", trade))
    )
    if inspect.isawaitable(trade_subscription):
      await trade_subscription

    self.logger.info(f"Broker 和 DataAdapter 已设置: {mode.value}")

  async def _initialize_backtest_dynamic_universe(
    self, runtime: StrategyRuntime
  ) -> None:
    """Apply the account-holdings universe snapshot before historical replay."""

    if runtime.context.mode != StrategyRunMode.BACKTEST or not runtime.strategy:
      return
    metadata = dict(runtime.context.parameters.get("initial_instrument_metadata") or {})
    desired = list(runtime.context.instruments or [])
    if not metadata and not self._uses_t_trade_opportunity_runtime(runtime):
      return
    staged_emission: Optional[Dict[str, Dict[str, Any]]] = None
    if self._uses_t_trade_opportunity_runtime(runtime):
      try:
        staged_emission = self._build_t_trade_intent_emission_snapshot(
          runtime,
          desired,
          metadata,
        )
      except Exception:
        self._clear_t_trade_intent_emission_snapshot(runtime)
        raise
    state = runtime.strategy.state.to_dict()
    account = runtime.state_manager.get_account_quota() if runtime.state_manager else {}
    positions = (
      runtime.state_manager.get_all_positions() if runtime.state_manager else {}
    )
    reconcile_input = StrategyInput(
      run_id=runtime.run_id,
      strategy_id=str(runtime.strategy_id),
      timestamp=runtime.context.backtest_start_time or time_utils.now(),
      cadence=StrategyCadence.RECONCILE,
      instrument_code="",
      event={
        "added": desired,
        "removed": [],
        "instruments": desired,
        "instrument_metadata": metadata,
      },
      portfolio_state={"account": account, "positions": positions},
      strategy_state=state,
      parameters=dict(runtime.context.parameters or {}),
    )
    try:
      output = await runtime.strategy.step(reconcile_input)
      await self._process_strategy_output(runtime, output, reconcile_input)
    except Exception:
      if staged_emission is not None:
        self._clear_t_trade_intent_emission_snapshot(runtime)
      raise
    if staged_emission is not None:
      self._publish_t_trade_intent_emission_snapshot(runtime, staged_emission)

  async def _run_strategy_loop(self, runtime: StrategyRuntime) -> None:
    """策略运行循环"""
    strategy = runtime.strategy
    strategy_stopped = False

    try:
      await self._initialize_backtest_dynamic_universe(runtime)
      self._runtime_log(runtime, "INFO", "策略初始化完成，进入执行循环")

      # 根据模式运行不同的逻辑（回测模式需要回放数据）
      if runtime.context.mode == StrategyRunMode.BACKTEST:
        self._runtime_log(runtime, "INFO", "回测执行开始")
        await self._run_backtest_loop(runtime)
        self._finalize_t_trade_phase_one_baseline(runtime)
        await self._finalize_t_trade_replay(runtime)
        await self._finalize_t_trade_candidate_outcomes(runtime)
      else:
        # 实时模式下，_run_realtime_loop 主要负责状态更新和心跳
        # 事件处理在 _process_event_queue 中进行
        await self._run_realtime_loop(runtime)

      # 如果正常结束且未被停止，标记为完成
      if runtime.status == ExecutionStatus.RUNNING:
        # A normal terminal mutation belongs inside the final explicit
        # checkpoint, not in ``finally`` after it.  The DAY_BATCH coordinator
        # therefore observes the post-stop StrategyState.
        if strategy:
          await strategy.stop()
          strategy_stopped = True
        if self._runtime_state_checkpoint_policy(runtime) == (
          RUNTIME_STATE_CHECKPOINT_POLICY_DAY_BATCH
        ):
          if runtime._checkpoint_virtual_trade_date is None:
            checkpoint = getattr(
              runtime.state_manager,
              "checkpoint_strategy_state_changes",
              None,
            )
            if not callable(checkpoint) or not await checkpoint():
              raise RuntimeError("BACKTEST_TERMINAL_STATE_CHECKPOINT_BLOCKED")
          else:
            await self._coordinate_backtest_terminal_checkpoint(
              runtime,
              cause="COMPLETED",
            )
          await self._flush_t_trade_opportunity_diagnostics(runtime)
        elif runtime.state_manager:
          await self._coordinate_terminal_session_checkpoint(
            runtime,
            cause="COMPLETED",
          )
          checkpoint = getattr(
            runtime.state_manager,
            "checkpoint_strategy_state_changes",
            None,
          )
          if not callable(checkpoint) or not await checkpoint():
            raise RuntimeError("RUNTIME_TERMINAL_STATE_CHECKPOINT_BLOCKED")
        runtime.status = ExecutionStatus.COMPLETED
        self._runtime_log(runtime, "SUCCESS", f"策略运行完成: {runtime.run_id}")

        # 回测模式：写入结果文件并更新数据库记录
        if runtime.context.mode == StrategyRunMode.BACKTEST and runtime.state_manager:
          self._runtime_log(runtime, "INFO", "回测结果文件写入开始")
          if runtime.log_manager:
            await runtime.log_manager.flush(runtime.run_id)
          final_grid_book_snapshot = (
            runtime.state_manager.get_latest_backtest_grid_book_snapshot()
          )
          grid_book_snapshot_count = (
            runtime.state_manager.get_backtest_grid_book_snapshot_count()
          )
          grid_book_observed_count = (
            runtime.state_manager.get_backtest_grid_book_observed_count()
          )
          result_path = await runtime.state_manager.finalize_backtest()
          self._runtime_log(runtime, "SUCCESS", f"回测结果文件写入完成: {result_path}")

          # 更新 StrategyBacktest 记录
          if runtime.context.backtest_id:
            from quantx_infrastructure.database.connection import get_async_db
            from quantx_infrastructure.models.enums import StrategyRunStatus
            from quantx_infrastructure.repositories.backtest_repository import (
              BacktestRepository,
            )
            from quantx_infrastructure.repositories.strategy_grid_book_snapshot_repository import (
              StrategyGridBookSnapshotRepository,
            )
            from quantx_infrastructure.repositories.strategy_run_repository import (
              StrategyRunRepository,
            )

            metrics = runtime.get_metrics()
            if runtime.context.parameters.get("t_trade_replay"):
              from quantx_infrastructure.core.t_trade_replay_metrics import (
                build_t_trade_replay_metrics,
              )
              from quantx_infrastructure.core.t_trade_replay_report import (
                write_t_trade_replay_report,
              )

              opportunity_diagnostics = (
                await self._load_t_trade_replay_opportunity_diagnostics(runtime)
              )
              replay_metrics = build_t_trade_replay_metrics(
                runtime,
                opportunity_diagnostics=opportunity_diagnostics,
              )
              try:
                replay_metrics["report"] = write_t_trade_replay_report(
                  result_path,
                  replay_metrics,
                  run_id=runtime.run_id,
                  backtest_id=runtime.context.backtest_id,
                  start_time=runtime.context.backtest_start_time,
                  end_time=runtime.context.backtest_end_time,
                )
              except Exception:
                self.logger.exception("做 T 回放报告生成失败")
                replay_metrics["report"] = {
                  "status": "FAILED",
                  "schema_version": 2,
                  "generated_at": None,
                  "conclusion_code": "REPORT_GENERATION_FAILED",
                  "conclusion": "回放已完成，但报告文件生成失败，请检查 Engine 日志。",
                  "html_artifact": "",
                  "json_artifact": "",
                }
              metrics["t_trade_replay"] = replay_metrics
            if runtime.context.parameters.get("exit_plan_replay"):
              from quantx_infrastructure.core.exit_plan_replay_metrics import (
                build_exit_plan_replay_metrics,
              )
              from quantx_infrastructure.core.exit_plan_replay_report import (
                write_exit_plan_replay_report,
              )

              replay_metrics = build_exit_plan_replay_metrics(runtime)
              try:
                replay_metrics["report"] = write_exit_plan_replay_report(
                  result_path,
                  replay_metrics,
                  run_id=runtime.run_id,
                  backtest_id=runtime.context.backtest_id,
                )
              except Exception:
                self.logger.exception("卖出计划回放报告生成失败")
                replay_metrics["report"] = {
                  "status": "FAILED",
                  "schema_version": 1,
                  "generated_at": None,
                  "conclusion_code": "REPORT_GENERATION_FAILED",
                  "conclusion": "回放已完成，但报告文件生成失败。",
                  "html_artifact": "",
                  "json_artifact": "",
                }
              metrics["exit_plan_replay"] = replay_metrics
            if runtime.context.parameters.get("limit_up_board_replay"):
              from quantx_infrastructure.core.limit_up_board_replay_metrics import (
                build_limit_up_board_replay_metrics,
              )

              metrics["limit_up_board_replay"] = build_limit_up_board_replay_metrics(
                runtime
              )
            if runtime.performance_recorder:
              try:
                await runtime.performance_recorder.flush()
                (
                  performance_path,
                  performance_view,
                ) = await StrategyPerformanceService.finalize_backtest_snapshot(
                  run_id=runtime.run_id,
                  backtest_id=runtime.context.backtest_id,
                  mode=runtime.context.mode,
                  metrics=metrics,
                )
                metrics["performance_snapshot_path"] = performance_path
                metrics["performance_summary"] = performance_view.get("summary")
              except Exception as exc:
                self.logger.error(f"回测绩效快照生成失败: {exc}")

            async for db in get_async_db():
              backtest_repo = BacktestRepository(db)
              run_repo = StrategyRunRepository(db)
              backtest = await backtest_repo.update_backtest_status(
                backtest_id=runtime.context.backtest_id,
                status="COMPLETED",
                metrics=metrics,
                end_time=time_utils.now(),
              )
              await run_repo.update_run(
                runtime.run_id,
                {
                  "status": StrategyRunStatus.COMPLETED,
                  "metrics": metrics,
                  "error_message": None,
                  "stop_time": time_utils.now(),
                },
              )
              if backtest and final_grid_book_snapshot:
                snapshot_repo = StrategyGridBookSnapshotRepository(db)
                await snapshot_repo.upsert_backtest_final(
                  strategy_run_id=runtime.run_id,
                  backtest_id=runtime.context.backtest_id,
                  backtest_version=int(getattr(backtest, "version", 0) or 0),
                  snapshot=final_grid_book_snapshot,
                  source_path=result_path,
                  snapshot_count=grid_book_snapshot_count,
                  observed_count=grid_book_observed_count,
                )
              break
            self._runtime_log(
              runtime,
              "SUCCESS",
              f"回测记录已更新: {runtime.context.backtest_id}",
            )
            if runtime.context.parameters.get("limit_up_board_replay"):
              await self._update_limit_up_board_replay_projection(
                runtime,
                status="COMPLETED",
                progress_pct=100.0,
                result_ready=True,
              )

    except asyncio.CancelledError:
      if runtime.status != ExecutionStatus.STOPPING:
        runtime.status = ExecutionStatus.ERROR
        runtime.error_message = "策略运行任务被意外取消"
        self._runtime_log(
          runtime,
          "ERROR",
          f"策略运行任务被意外取消: {runtime.run_id}",
        )
      raise
    except Exception as e:
      runtime.status = ExecutionStatus.ERROR
      runtime.error_message = str(e)
      self._runtime_log(
        runtime,
        "ERROR",
        f"策略运行循环异常: {runtime.run_id}, 错误: {e}",
      )
      if runtime.context.parameters.get("limit_up_board_replay"):
        try:
          await self._persist_limit_up_board_replay_terminal(
            runtime,
            status="ERROR",
            error_message=str(e),
          )
        except Exception as persist_exc:
          self.logger.error(
            "异常回放终态持久化失败: %s, %s",
            runtime.run_id,
            persist_exc,
          )
    finally:
      if runtime.status == ExecutionStatus.ERROR:
        try:
          await self._ensure_terminal_cleanup(runtime)
        except asyncio.CancelledError:
          # The owned cleanup task continues even if the failing loop is
          # cancelled again while unwinding.
          pass
      else:
        if runtime.performance_recorder:
          try:
            await runtime.performance_recorder.flush()
          except Exception as e:
            self.logger.error(f"绩效采样刷新失败: {e}")
        # Explicit stop owns the final lifecycle ordering: event consumer first,
        # then one strategy stop, state snapshot, and broker disconnect.
        if (
          strategy
          and not strategy_stopped
          and runtime.status != ExecutionStatus.STOPPING
        ):
          try:
            await strategy.stop()
          except Exception as e:
            self._runtime_log(runtime, "ERROR", f"策略停止异常: {e}")
        if runtime.log_manager:
          await runtime.log_manager.flush(runtime.run_id)

  async def _run_backtest_loop(self, runtime: StrategyRuntime) -> None:
    """运行回测循环 - 支持tick和K线双数据流"""
    data_adapter = runtime.data_adapter

    # 使用运行时上下文的回测时间范围（来自 StrategyManager.run_strategy）
    end_time = runtime.context.backtest_end_time or time_utils.now()
    start_time = runtime.context.backtest_start_time or (end_time - timedelta(days=30))

    if runtime.context.parameters.get("limit_up_board_replay"):
      await self._run_limit_up_board_replay(runtime, start_time, end_time)
      return

    # 读取策略声明的数据需求
    requirements = runtime.strategy_class.get_data_requirements()
    use_tick_data = bool(requirements.get("use_tick_data", False))
    periods = [
      p.lower()
      for p in (requirements.get("periods") or [])
      if p and p.lower() != "tick"
    ]
    self._runtime_log(
      runtime,
      "INFO",
      (
        f"回测数据回放准备: {start_time} -> {end_time}, "
        f"use_tick_data={use_tick_data}, periods={periods or ['tick']}, "
        f"instruments={runtime.context.instruments}"
      ),
    )

    if (
      isinstance(data_adapter, HistoricalDataAdapter)
      and len(runtime.context.instruments) > 1
    ):
      await self._run_backtest_multi_instrument_timeline(
        runtime,
        list(runtime.context.instruments),
        periods,
        start_time,
        end_time,
        use_tick_data=use_tick_data,
      )
      return

    for instrument_code in runtime.context.instruments:
      self._runtime_log(runtime, "INFO", f"回测标的开始回放: {instrument_code}")
      if isinstance(data_adapter, HistoricalDataAdapter):
        if use_tick_data:
          await self._run_backtest_timeline_with_ticks(
            runtime, instrument_code, periods, start_time, end_time
          )
        else:
          await self._run_backtest_timeline_with_klines(
            runtime, instrument_code, periods, start_time, end_time
          )
        self._runtime_log(runtime, "SUCCESS", f"回测标的回放完成: {instrument_code}")
        continue

      if use_tick_data:
        # 双数据流模式：订阅tick和K线
        await data_adapter.subscribe_tick(
          instrument_code,
          lambda tick: self._enqueue_runtime_market_event(runtime, "tick", tick),
        )

        for period in periods:
          await data_adapter.subscribe_kline(
            instrument_code,
            period,
            lambda kline: self._enqueue_runtime_market_event(runtime, "kline", kline),
          )
      else:
        # 仅K线模式 - 支持多周期
        for period in periods:
          await data_adapter.subscribe_kline(
            instrument_code,
            period,
            lambda kline: self._enqueue_runtime_market_event(runtime, "kline", kline),
          )

  async def _run_limit_up_board_replay(
    self,
    runtime: StrategyRuntime,
    start_time: datetime,
    end_time: datetime,
  ) -> None:
    """Load the verified immutable inputs and run one account scenario."""

    from .limit_up_board_replay import (
      LimitUpBoardReplayRunner,
      ReplayDelayScenario,
      load_limit_up_board_replay_dataset,
    )

    parameters = runtime.context.parameters
    await self._update_limit_up_board_replay_projection(
      runtime,
      status="RUNNING",
      progress_pct=0.0,
      result_ready=False,
    )
    manifest_path = str(parameters.get("replay_input_manifest_path") or "").strip()
    if not manifest_path:
      raise ValueError("打板历史回放缺少 replay_input_manifest_path")
    payload = load_limit_up_board_replay_dataset(manifest_path)
    expected_fingerprint = str(
      parameters.get("limit_up_board_replay_dataset_fingerprint") or ""
    )
    actual_fingerprint = str(payload.get("dataset_fingerprint") or "")
    if expected_fingerprint and expected_fingerprint != actual_fingerprint:
      raise ValueError("打板历史回放输入指纹与创建任务时不一致")
    expected_config_fingerprint = str(
      parameters.get("limit_up_board_replay_config_fingerprint") or ""
    )
    actual_config_fingerprint = str(payload.get("config_fingerprint") or "")
    if (
      expected_config_fingerprint
      and expected_config_fingerprint != actual_config_fingerprint
    ):
      raise ValueError("打板历史回放配置指纹与创建任务时不一致")
    data_quality = dict(payload.get("data_quality") or {})
    blockers = list(data_quality.get("blockers") or [])
    if blockers or data_quality.get("executable") is False:
      raise ValueError("打板历史回放输入数据质量不允许执行")
    events = list(payload.get("events") or [])
    ticks = list(payload.get("ticks") or [])
    if not events:
      raise ValueError("打板历史回放候选事件为空")
    if not ticks:
      raise ValueError("打板历史回放 Tick 事件为空")

    scenario = ReplayDelayScenario.from_runtime_parameters(parameters)
    runner = LimitUpBoardReplayRunner(
      self,
      scenario,
      selection_settings=dict(payload.get("settings") or {}),
    )
    result = await runner.run(
      runtime,
      universe_events=events,
      ticks=ticks,
      start_time=start_time,
      end_time=end_time,
    )
    self._runtime_log(
      runtime,
      "SUCCESS",
      "打板历史回放场景完成: "
      f"scenario={scenario.scenario_id}, ticks={result.processed_ticks}, "
      f"frames={result.processed_universe_snapshots}, "
      f"open_positions={len(result.open_positions)}",
    )

  async def _update_limit_up_board_replay_projection(
    self,
    runtime: StrategyRuntime,
    *,
    status: str,
    progress_pct: Optional[float] = None,
    result_ready: bool,
    progress_update: bool = False,
    error_message: Optional[str] = None,
  ) -> None:
    backtest_id = str(runtime.context.backtest_id or "")
    if not backtest_id:
      return
    from quantx_infrastructure.services.limit_up_board_replay_projection_service import (
      LimitUpBoardReplayUpdateKind,
      limit_up_board_replay_projection_service,
    )

    try:
      await limit_up_board_replay_projection_service.update_scenario(
        backtest_id=backtest_id,
        status=status,
        progress_pct=progress_pct,
        processed_until=runtime.context.current_time,
        error_message=error_message,
        kind=(
          LimitUpBoardReplayUpdateKind.RESULT_READY
          if result_ready
          else (
            LimitUpBoardReplayUpdateKind.PROGRESS
            if progress_update
            else LimitUpBoardReplayUpdateKind.STATUS_CHANGED
          )
        ),
      )
    except Exception:
      self.logger.exception(
        "更新打板历史回放场景投影失败: backtest_id=%s status=%s",
        backtest_id,
        status,
      )

  async def _persist_limit_up_board_replay_terminal(
    self,
    runtime: StrategyRuntime,
    *,
    status: str,
    error_message: Optional[str] = None,
  ) -> None:
    """Persist partial results for cancellation/error without liquidating."""

    from quantx_infrastructure.core.limit_up_board_replay_metrics import (
      build_limit_up_board_replay_metrics,
    )

    normalized_status = str(status or "ERROR").upper()
    replay_metrics = build_limit_up_board_replay_metrics(runtime)
    metrics = runtime.get_metrics()
    metrics["limit_up_board_replay"] = replay_metrics
    if runtime.context.backtest_id:
      try:
        from quantx_infrastructure.database.connection import get_async_db
        from quantx_infrastructure.models.enums import StrategyRunStatus
        from quantx_infrastructure.repositories.backtest_repository import (
          BacktestRepository,
        )
        from quantx_infrastructure.repositories.strategy_run_repository import (
          StrategyRunRepository,
        )

        async for db in get_async_db():
          await BacktestRepository(db).update_backtest_status(
            runtime.context.backtest_id,
            normalized_status,
            metrics=metrics,
            error_message=error_message,
            end_time=time_utils.now(),
          )
          await StrategyRunRepository(db).update_run(
            runtime.run_id,
            {
              "status": (
                StrategyRunStatus.ERROR
                if normalized_status == "ERROR"
                else StrategyRunStatus.STOPPED
              ),
              "metrics": metrics,
              "error_message": error_message,
              "stop_time": time_utils.now(),
            },
          )
          break
      except Exception:
        self.logger.exception(
          "持久化打板历史回放终态失败: run_id=%s status=%s",
          runtime.run_id,
          normalized_status,
        )
    await self._update_limit_up_board_replay_projection(
      runtime,
      status=normalized_status,
      progress_pct=(100.0 if normalized_status == "COMPLETED" else None),
      result_ready=True,
      error_message=error_message,
    )

  async def _wait_for_backtest_reports(
    self,
    runtime: StrategyRuntime,
    *,
    timeout_seconds: float = 30.0,
  ) -> None:
    """Wait until simulated broker reports have reached runtime state."""

    try:
      await asyncio.wait_for(
        runtime.event_queue.join(),
        timeout=max(1.0, float(timeout_seconds)),
      )
    except asyncio.TimeoutError as exc:
      raise RuntimeError("回测 Broker 回报未在结束前完成收敛") from exc

  @staticmethod
  def _runtime_now(runtime: StrategyRuntime) -> datetime:
    """Return this runtime's execution time without changing global clocks."""

    if runtime.context.mode == StrategyRunMode.BACKTEST:
      if runtime.replay_clock is not None:
        return runtime.replay_clock.now()
      if runtime.context.current_time is not None:
        return runtime.context.current_time
    return time_utils.now()

  @staticmethod
  def _advance_runtime_replay_clock(
    runtime: StrategyRuntime,
    timestamp: datetime,
  ) -> datetime:
    if runtime.context.mode != StrategyRunMode.BACKTEST:
      raise ValueError("Replay clock is only available in BACKTEST mode")
    if runtime.replay_clock is None:
      runtime.replay_clock = ReplayClock(timestamp)
    else:
      runtime.replay_clock.advance_to(timestamp)
    runtime.context.current_time = timestamp
    return timestamp

  async def advance_replay_time(
    self,
    runtime: StrategyRuntime,
    timestamp: datetime,
  ) -> None:
    """Advance a historical runtime between quotes without granting a fill."""

    self._advance_runtime_replay_clock(runtime, timestamp)
    if isinstance(runtime.data_adapter, HistoricalDataAdapter):
      runtime.data_adapter.current_time = timestamp
    if isinstance(runtime.broker, BacktestBroker):
      await runtime.broker.advance_time(timestamp)
    if runtime.state_manager:
      runtime.state_manager.settle_trading_day(timestamp.date())
    await self._expire_pending_approvals(runtime)

  async def process_replay_tick(self, runtime: StrategyRuntime, tick: Any) -> None:
    if not self._uses_strict_board_replay(runtime):
      raise ValueError("Tick replay port is only available to board replay runs")
    await self._process_tick(runtime, tick)

  async def reconcile_replay_universe(
    self,
    runtime: StrategyRuntime,
    instruments: List[str],
    instrument_metadata: Dict[str, Dict[str, Any]],
  ) -> Dict[str, List[str]]:
    return await self._apply_backtest_instrument_reconcile(
      runtime,
      instruments,
      instrument_metadata=instrument_metadata,
    )

  async def approve_replay_intent(
    self,
    runtime: StrategyRuntime,
    intent_id: str,
  ) -> Dict[str, Any]:
    if self.runs.get(runtime.run_id) is not runtime:
      raise ValueError("Replay runtime is not registered in this executor")
    intent = runtime.pending_approvals.get(intent_id)
    return await self.approve_trade_intent(
      runtime.run_id,
      intent_id,
      approval_expectation=(
        self._v3_t_trade_expectation_from_intent(intent) if intent is not None else None
      ),
    )

  async def reject_replay_intent(
    self,
    runtime: StrategyRuntime,
    intent_id: str,
    reason: str,
  ) -> Dict[str, Any]:
    if self.runs.get(runtime.run_id) is not runtime:
      raise ValueError("Replay runtime is not registered in this executor")
    return await self.reject_trade_intent(runtime.run_id, intent_id, reason)

  async def cancel_replay_open_buy_orders(
    self,
    runtime: StrategyRuntime,
    reason: str,
  ) -> int:
    if self.runs.get(runtime.run_id) is not runtime:
      raise ValueError("Replay runtime is not registered in this executor")
    return await self.cancel_open_buy_orders(runtime.run_id, reason)

  async def wait_replay_reports(self, runtime: StrategyRuntime) -> None:
    await self._board_replay_report_barrier(runtime)

  def replay_sticky_instruments(self, runtime: StrategyRuntime) -> set[str]:
    return self._board_replay_sticky_instruments(runtime)

  async def report_replay_progress(
    self,
    runtime: StrategyRuntime,
    processed_until: datetime,
  ) -> None:
    start_time = runtime.context.backtest_start_time
    end_time = runtime.context.backtest_end_time
    if start_time is None or end_time is None or end_time <= start_time:
      return
    now = monotonic()
    if now - runtime._last_replay_projection_at < 1.0:
      return
    progress_pct = max(
      0.0,
      min(
        99.9,
        (processed_until - start_time).total_seconds()
        / (end_time - start_time).total_seconds()
        * 100.0,
      ),
    )
    if progress_pct <= runtime._last_replay_progress_pct:
      return
    await self._update_limit_up_board_replay_projection(
      runtime,
      status="RUNNING",
      progress_pct=progress_pct,
      result_ready=False,
      progress_update=True,
    )
    runtime._last_replay_projection_at = now
    runtime._last_replay_progress_pct = progress_pct

  @staticmethod
  def _uses_strict_board_replay(runtime: StrategyRuntime) -> bool:
    return bool(
      runtime.context.mode == StrategyRunMode.BACKTEST
      and runtime.context.parameters.get("limit_up_board_replay")
    )

  @staticmethod
  def _requires_replay_event_integrity(runtime: StrategyRuntime) -> bool:
    """Return whether one failed market event must fail the whole replay."""

    if runtime.context.mode != StrategyRunMode.BACKTEST:
      return False
    parameters = runtime.context.parameters
    return bool(
      parameters.get("limit_up_board_replay")
      or parameters.get("t_trade_replay")
      or parameters.get("exit_plan_replay")
    )

  async def _board_replay_report_barrier(
    self,
    runtime: StrategyRuntime,
  ) -> None:
    if not self._uses_strict_board_replay(runtime):
      return
    if runtime.event_task is asyncio.current_task():
      raise RuntimeError("回放撮合不能在 Broker 回报事件任务内等待自身")
    await self._wait_for_backtest_reports(runtime)

  async def _cancel_backtest_pending_orders(self, runtime: StrategyRuntime) -> None:
    broker = runtime.broker
    if not isinstance(broker, BacktestBroker):
      return
    active_statuses = {
      OrderStatus.PENDING,
      OrderStatus.SUBMITTED,
      OrderStatus.PARTIAL_FILLED,
    }
    for order in list((broker.orders or {}).values()):
      if getattr(order, "status", None) not in active_statuses:
        continue
      await broker.cancel_order(str(order.order_id))

  def _build_backtest_end_exit_intent(
    self,
    runtime: StrategyRuntime,
    plan: Any,
    market_data: MarketDataSnapshot,
  ) -> tuple[ExitDecision, TradeIntent]:
    bids = list(getattr(market_data, "bid_price", []) or [])
    current_price = float(
      getattr(market_data, "price", 0.0) or getattr(market_data, "close", 0.0) or 0.0
    )
    price_hint = float(bids[0] if bids and bids[0] else current_price)
    batch_id = str(plan.template.metadata.get("t_batch_id", "") or "")
    rule_id = f"backtest-end-force-close:{plan.plan_id}"
    decision = ExitDecision(
      plan_id=plan.plan_id,
      rule_id=rule_id,
      rule_type="BACKTEST_END_FORCE_CLOSE",
      reason="BACKTEST_END_FORCE_CLOSE",
      volume=int(plan.remaining_volume),
      priority=10_000,
      metrics={
        "backtest_end": True,
        "current_price": current_price,
        "remaining_volume": int(plan.remaining_volume),
      },
    )
    intent = TradeIntent(
      strategy_id=plan.template.strategy_id or str(runtime.strategy_id),
      run_id=plan.template.run_id or runtime.run_id,
      instrument_code=plan.template.instrument_code,
      direction=TradeIntentDirection.SELL,
      bucket=plan.template.bucket,
      reason="BACKTEST_END_FORCE_CLOSE",
      priority=TradeIntentPriority.URGENT,
      target_volume=int(plan.remaining_volume),
      limit_price_hint=price_hint,
      execution_mode=TradeIntentExecutionMode.AUTO,
      max_price_deviation_bps=plan.template.execution.max_slippage_bps,
      metadata={
        **dict(plan.template.metadata or {}),
        "t_batch_id": batch_id,
        "exit_plan_id": plan.plan_id,
        "exit_rule_id": rule_id,
        "exit_rule_type": "BACKTEST_END_FORCE_CLOSE",
        "exit_reason": "BACKTEST_END_FORCE_CLOSE",
        "exit_plan_source_type": plan.template.source_type,
        "exit_plan_source_id": plan.template.source_id,
        "exit_plan_config_version": plan.template.config_version,
        "price_type": "MARKET",
        "price_reference": ExitPriceReference.BID.value,
        "protected_limit": False,
        "max_exit_slippage_bps": plan.template.execution.max_slippage_bps,
        "execution_urgency": "URGENT",
        "t1_policy": plan.template.t1_policy.value,
        "allow_t1_substitution": True,
        "t1_insufficient_action": "REJECT",
        "backtest_forced_close": True,
        "exit_metrics": dict(decision.metrics),
      },
    )
    return decision, intent

  async def _load_t_trade_replay_opportunity_diagnostics(
    self,
    runtime: StrategyRuntime,
  ) -> Dict[str, Any]:
    """Load only the immutable V3 evidence belonging to this replay run."""

    params = dict(runtime.context.parameters or {})
    account_id = str(params.get("account_id") or "").strip()

    def parse_window(value: Any) -> Optional[datetime]:
      if isinstance(value, datetime):
        return value
      if isinstance(value, str) and value.strip():
        try:
          return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
          return None
      return None

    start_time = parse_window(runtime.context.backtest_start_time) or parse_window(
      params.get("replay_start_time")
    )
    end_time = parse_window(runtime.context.backtest_end_time) or parse_window(
      params.get("replay_end_time")
    )
    if (
      not account_id or start_time is None or end_time is None or start_time >= end_time
    ):
      return {
        "available": False,
        "reason_code": "DIAGNOSTICS_SCOPE_UNAVAILABLE",
        "reason": "回放缺少账户或有效时间范围，无法加载 V3 机会诊断。",
      }
    try:
      async with AsyncSessionLocal() as db:
        return await self.opportunity_diagnostics_service.signal_diagnostics(
          account_id,
          stock_code=None,
          start_time=start_time,
          end_time=end_time,
          db=db,
          strategy_run_id=runtime.run_id,
        )
    except Exception as exc:
      self.logger.exception(
        "加载做 T V3 回放诊断失败: run_id=%s",
        runtime.run_id,
      )
      return {
        "available": False,
        "reason_code": "DIAGNOSTICS_LOAD_FAILED",
        "reason": f"V3 机会诊断加载失败: {exc}",
      }

  async def _finalize_t_trade_replay(self, runtime: StrategyRuntime) -> None:
    """Close replay-created T batches on the final tradable quote."""

    if (
      runtime.context.mode != StrategyRunMode.BACKTEST
      or not runtime.context.parameters.get("t_trade_replay")
      or not isinstance(runtime.broker, BacktestBroker)
    ):
      return

    await self._wait_for_backtest_reports(runtime)
    await self._cancel_backtest_pending_orders(runtime)
    await self._wait_for_backtest_reports(runtime)

    attempts: List[Dict[str, Any]] = []
    plans = sorted(
      list(runtime.exit_plan_book.plans.values()),
      key=lambda item: (item.template.instrument_code, item.plan_id),
    )
    for plan in plans:
      if plan.remaining_volume <= 0:
        continue
      attempt = {
        "plan_id": plan.plan_id,
        "batch_id": str(plan.template.metadata.get("t_batch_id", "") or ""),
        "stock_code": plan.template.instrument_code,
        "requested_volume": int(plan.remaining_volume),
        "status": "PENDING",
        "remaining_volume": int(plan.remaining_volume),
      }
      attempts.append(attempt)
      market_data = runtime.latest_market_data.get(plan.template.instrument_code)
      if market_data is None:
        attempt.update(status="FAILED_NO_FINAL_QUOTE")
        continue
      if plan.pending_intent_id:
        attempt.update(status="FAILED_PENDING_EXIT_NOT_RELEASED")
        continue

      decision, intent = self._build_backtest_end_exit_intent(
        runtime,
        plan,
        market_data,
      )
      runtime.exit_plan_book.mark_intent(decision, intent.intent_id)
      self._persist_exit_plan_book(runtime)
      await self._process_strategy_output(
        runtime,
        StrategyOutput(
          trade_intents=[intent],
          decision_tags=["backtest_end_force_close"],
          trace_payload={
            "exit_plan_id": plan.plan_id,
            "t_batch_id": attempt["batch_id"],
            "requested_volume": attempt["requested_volume"],
          },
        ),
      )
      await self._wait_for_backtest_reports(runtime)
      attempt["remaining_volume"] = int(plan.remaining_volume)
      attempt["status"] = (
        "FILLED" if plan.remaining_volume <= 0 else "FAILED_NOT_FULLY_LIQUIDATED"
      )

    await runtime.broker.refresh_performance_snapshot()
    liquidation = {
      "attempted_cycles": len(attempts),
      "closed_cycles": sum(item["status"] == "FILLED" for item in attempts),
      "failed_cycles": sum(item["status"] != "FILLED" for item in attempts),
      "attempts": attempts,
    }
    runtime.context.parameters["replay_forced_liquidation"] = liquidation
    level = "SUCCESS" if liquidation["failed_cycles"] == 0 else "WARNING"
    self._runtime_log(
      runtime,
      level,
      "做 T 回放期末清算完成: "
      f"attempted={liquidation['attempted_cycles']}, "
      f"closed={liquidation['closed_cycles']}, "
      f"failed={liquidation['failed_cycles']}",
    )

  async def _run_backtest_multi_instrument_timeline(
    self,
    runtime: StrategyRuntime,
    instrument_codes: List[str],
    periods: List[str],
    start_time: datetime,
    end_time: datetime,
    *,
    use_tick_data: bool,
  ) -> None:
    """Replay all instruments on one chronological event timeline."""
    data_adapter = runtime.data_adapter
    if not isinstance(data_adapter, HistoricalDataAdapter):
      return
    for code in instrument_codes:
      await self._run_backtest_warmup_klines(runtime, code, periods, start_time)

    trading_dates = await TradingDateHelper().get_trading_calendar(
      market="SH",
      start_date=start_time.date(),
      end_date=end_time.date(),
    )
    if not trading_dates:
      self._runtime_log(runtime, "WARNING", "多标的回测区间无交易日")
      return

    window_hours = self._get_backtest_window_hours()
    last_tick_time: Dict[str, Optional[datetime]] = {
      code: None for code in instrument_codes
    }
    last_kline_time: Dict[tuple[str, str], Optional[datetime]] = {
      (code, period): None for code in instrument_codes for period in periods
    }
    totals = {"tick": 0, "kline": 0}
    alignment = str(
      runtime.context.parameters.get("kline_time_alignment", "end") or "end"
    ).lower()
    for trading_date in trading_dates:
      if runtime.status != ExecutionStatus.RUNNING:
        break
      day_start = max(start_time, datetime.combine(trading_date, time(9, 30)))
      day_end = min(end_time, datetime.combine(trading_date, time(15, 30)))
      if day_end < day_start:
        continue
      for window_start, window_end in self._iter_backtest_windows(
        day_start, day_end, window_hours
      ):
        events: List[tuple[datetime, int, tuple[int, int, int], str, str, Any]] = []
        for code in instrument_codes:
          if use_tick_data:
            ticks = await self._load_backtest_ticks(
              runtime,
              data_adapter,
              instrument_code=code,
              start_time=window_start,
              end_time=window_end,
            )
            previous_tick = last_tick_time.get(code)
            filtered_ticks = [
              tick
              for tick in (ticks or [])
              if tick is not None
              and tick.time is not None
              and (previous_tick is None or tick.time > previous_tick)
            ]
            for tick in self._filter_backtest_continuous_session_events(filtered_ticks):
              events.append(
                (
                  tick.time,
                  0,
                  self._backtest_tick_source_identity(tick),
                  code,
                  "tick",
                  tick,
                )
              )
          for period in periods:
            klines = await data_adapter.get_klines(
              instrument_code=code,
              period=period,
              start_time=window_start,
              end_time=window_end,
            )
            previous_kline = last_kline_time.get((code, period))
            filtered_klines = [
              kline
              for kline in (klines or [])
              if kline is not None
              and kline.time is not None
              and (previous_kline is None or kline.time > previous_kline)
            ]
            if self._is_backtest_intraday_period(period):
              filtered_klines = self._filter_backtest_continuous_session_events(
                filtered_klines
              )
            for kline in filtered_klines:
              event_time = self._get_kline_end_time(kline, period, alignment=alignment)
              events.append(
                (
                  event_time,
                  1,
                  (0, int(event_time.timestamp() * 1000), 0),
                  code,
                  period,
                  kline,
                )
              )

        if use_tick_data and not periods:
          # V3 Tick replay is ordered by the immutable source identity, not by
          # the per-instrument fetch order or an arbitrary stock-code tie-break.
          events.sort(key=lambda item: (item[2], item[1], item[3], item[4]))
        else:
          events.sort(key=lambda item: (item[0], item[1], item[3], item[4]))
        for _, event_type, _, code, period, event in events:
          if runtime.status != ExecutionStatus.RUNNING:
            break
          if event_type == 0:
            await self._process_tick(runtime, event)
            last_tick_time[code] = event.time
            totals["tick"] += 1
          else:
            await self._process_kline(runtime, event)
            last_kline_time[(code, period)] = event.time
            totals["kline"] += 1
        self._runtime_log(
          runtime,
          "INFO",
          f"多标的回测窗口完成: {window_start} -> {window_end}, events={len(events)}",
        )
      if runtime.status == ExecutionStatus.RUNNING:
        await self._report_t_trade_replay_progress(
          runtime,
          processed_until=day_end,
          force=True,
        )
    self._runtime_log(
      runtime,
      "SUCCESS",
      f"多标的全局时间线回测完成: instruments={len(instrument_codes)}, "
      f"tick={totals['tick']}, kline={totals['kline']}",
    )

  def _backtest_tick_source_identity(
    self,
    tick: Any,
  ) -> tuple[int, int, int]:
    """Return the causal identity used to merge a replay Tick globally."""

    generation = self._safe_non_negative_int(
      self._get_value(tick, "continuity_generation"),
      default=0,
    )
    try:
      source_time_ms = tick_source_time_ms(tick)
    except (TypeError, ValueError, OverflowError):
      timestamp = self._get_value(tick, "time")
      source_time_ms = (
        int(time_utils.to_utc(timestamp).timestamp() * 1000)
        if isinstance(timestamp, datetime)
        else 0
      )
    tick_ordinal = self._safe_non_negative_int(
      self._get_value(tick, "tick_ordinal"),
      default=self._safe_non_negative_int(
        self._get_value(tick, "transaction_num"),
        default=0,
      ),
    )
    return generation, source_time_ms, tick_ordinal

  async def _load_backtest_ticks(
    self,
    runtime: StrategyRuntime,
    data_adapter: HistoricalDataAdapter,
    *,
    instrument_code: str,
    start_time: datetime,
    end_time: datetime,
  ) -> List[Any]:
    """Load a replay window without silently accepting a backend row limit."""

    if not (
      runtime.context.parameters.get("t_trade_replay")
      or runtime.context.parameters.get("exit_plan_replay")
    ):
      return await data_adapter.get_ticks(
        instrument_code=instrument_code,
        start_time=start_time,
        end_time=end_time,
        dividend_type="front",
        limit=6_000,
      )
    return await self._load_t_trade_replay_ticks_paginated(
      runtime,
      data_adapter,
      instrument_code=instrument_code,
      start_time=start_time,
      end_time=end_time,
    )

  async def _load_t_trade_replay_ticks_paginated(
    self,
    runtime: StrategyRuntime,
    data_adapter: HistoricalDataAdapter,
    *,
    instrument_code: str,
    start_time: datetime,
    end_time: datetime,
  ) -> List[Any]:
    page_size = max(1, int(_T_TRADE_REPLAY_TICK_PAGE_SIZE))
    max_pages = max(1, int(_T_TRADE_REPLAY_MAX_TICK_PAGES_PER_WINDOW))
    ticks: List[Any] = []
    offset = 0
    queries = 0
    nonempty_pages = 0
    # One entry per accepted page; the safety limit below caps this at
    # max_pages while retaining enough history to catch A-B-A repeats.
    seen_page_signatures: set[tuple[Any, ...]] = set()
    previous_last_time: Optional[datetime] = None

    while True:
      queries += 1
      try:
        market_data_service = getattr(data_adapter, "market_data_service", None)
        if market_data_service is not None:
          page = await market_data_service.get_tick_data(
            stock_code=instrument_code,
            start_time=start_time,
            end_time=end_time,
            dividend_type="front",
            as_frame=False,
            limit=page_size,
            offset=offset,
            order="asc",
          )
        else:
          page = await data_adapter.get_ticks(
            instrument_code=instrument_code,
            start_time=start_time,
            end_time=end_time,
            dividend_type="front",
            limit=page_size,
            order="asc",
            offset=offset,
          )
      except Exception as exc:
        self._record_t_trade_replay_tick_read_issue(
          runtime,
          instrument_code=instrument_code,
          start_time=start_time,
          end_time=end_time,
          reason_code="TICK_PAGE_QUERY_FAILED",
          message="历史 Tick 分页查询失败，拒绝生成不完整绩效",
          details={
            "offset": offset,
            "query_error_type": type(exc).__name__,
          },
        )
        raise RuntimeError(
          "DATA_PARTIAL: 历史 Tick 分页查询失败，无法证明回放输入完整 "
          f"({instrument_code}, {start_time.isoformat()}~{end_time.isoformat()}, "
          f"offset={offset})"
        ) from exc

      page_items = list(page or [])
      if len(page_items) > page_size:
        self._raise_t_trade_replay_tick_data_partial(
          runtime,
          instrument_code=instrument_code,
          start_time=start_time,
          end_time=end_time,
          reason_code="TICK_PAGE_SIZE_EXCEEDED",
          message="历史 Tick 查询返回条数超过请求页大小",
          details={"offset": offset, "returned": len(page_items), "limit": page_size},
        )
      if not page_items:
        break

      page_times: List[datetime] = []
      page_source_times: List[Optional[int]] = []
      for item in page_items:
        item_time = getattr(item, "time", None)
        if hasattr(item_time, "to_pydatetime"):
          item_time = item_time.to_pydatetime()
        if not isinstance(item_time, datetime):
          self._raise_t_trade_replay_tick_data_partial(
            runtime,
            instrument_code=instrument_code,
            start_time=start_time,
            end_time=end_time,
            reason_code="INVALID_TICK_PAGE_TIMESTAMP",
            message="历史 Tick 分页结果包含无效时间戳",
            details={"offset": offset},
          )
        page_times.append(time_utils.to_shanghai(item_time))

        explicit_source_time = getattr(item, "source_time_ms", None)
        try:
          explicit_source_number = float(explicit_source_time)
        except (TypeError, ValueError, OverflowError):
          explicit_source_number = None
        if (
          explicit_source_number is not None
          and isfinite(explicit_source_number)
          and explicit_source_number < 0
        ):
          self._raise_t_trade_replay_tick_data_partial(
            runtime,
            instrument_code=instrument_code,
            start_time=start_time,
            end_time=end_time,
            reason_code="INVALID_TICK_SOURCE_TIME",
            message="历史 Tick 包含负数 source_time_ms",
            details={"offset": offset},
          )
        try:
          page_source_times.append(tick_source_time_ms(item))
        except Exception:
          # Keep malformed non-negative source values on the existing
          # post-pagination normalization path so invalid legacy codecs still
          # report TICK_IDENTITY_NORMALIZATION_FAILED after all pages are read.
          page_source_times.append(None)

      if any(
        current < previous for previous, current in zip(page_times, page_times[1:])
      ):
        self._raise_t_trade_replay_tick_data_partial(
          runtime,
          instrument_code=instrument_code,
          start_time=start_time,
          end_time=end_time,
          reason_code="TICK_PAGE_NOT_ORDERED",
          message="历史 Tick 分页结果未按时间升序返回",
          details={"offset": offset},
        )
      try:
        page_identity_digest = hashlib.sha256(
          "\x1f".join(
            "\x1e".join(
              (
                page_time.isoformat(),
                str(
                  source_time_ms
                  if source_time_ms is not None
                  else getattr(item, "source_time_ms", None)
                ),
                str(getattr(item, "tick_ordinal", None)),
                tick_snapshot_identity(item),
                tick_page_content_identity(
                  item,
                  normalized_source_time_ms=source_time_ms,
                ),
              )
            )
            for item, page_time, source_time_ms in zip(
              page_items,
              page_times,
              page_source_times,
            )
          ).encode("utf-8")
        ).hexdigest()
      except Exception as exc:
        self._raise_t_trade_replay_tick_data_partial(
          runtime,
          instrument_code=instrument_code,
          start_time=start_time,
          end_time=end_time,
          reason_code="TICK_PAGE_SIGNATURE_FAILED",
          message="历史 Tick 分页结果无法生成确定性内容签名",
          details={"offset": offset, "error_type": type(exc).__name__},
        )
      page_signature = (
        len(page_items),
        page_times[0].isoformat(),
        page_times[-1].isoformat(),
        page_identity_digest,
      )
      if page_signature in seen_page_signatures:
        self._raise_t_trade_replay_tick_data_partial(
          runtime,
          instrument_code=instrument_code,
          start_time=start_time,
          end_time=end_time,
          reason_code="TICK_PAGINATION_DID_NOT_ADVANCE",
          message="历史 Tick 数据源未执行分页偏移",
          details={"offset": offset, "page_signature": list(page_signature)},
        )
      if previous_last_time is not None and page_times[0] < previous_last_time:
        self._raise_t_trade_replay_tick_data_partial(
          runtime,
          instrument_code=instrument_code,
          start_time=start_time,
          end_time=end_time,
          reason_code="TICK_PAGE_ORDER_REGRESSION",
          message="历史 Tick 跨页时间顺序倒退",
          details={"offset": offset},
        )

      ticks.extend(page_items)
      nonempty_pages += 1
      seen_page_signatures.add(page_signature)
      previous_last_time = page_times[-1]
      offset += len(page_items)
      if len(page_items) < page_size:
        break
      if nonempty_pages >= max_pages:
        self._raise_t_trade_replay_tick_data_partial(
          runtime,
          instrument_code=instrument_code,
          start_time=start_time,
          end_time=end_time,
          reason_code="TICK_PAGINATION_SAFETY_LIMIT",
          message="历史 Tick 分页达到安全上限，拒绝静默截断",
          details={
            "offset": offset,
            "page_size": page_size,
            "maximum_pages": max_pages,
          },
        )

    try:
      # Normalize only after every page has been read.  Per-page normalization
      # would restart same-millisecond ordinals at each offset and could make
      # cross-page source identities collide in the global replay timeline.
      ticks = normalize_ticks_losslessly(ticks)
    except Exception as exc:
      self._raise_t_trade_replay_tick_data_partial(
        runtime,
        instrument_code=instrument_code,
        start_time=start_time,
        end_time=end_time,
        reason_code="TICK_IDENTITY_NORMALIZATION_FAILED",
        message="历史 Tick source identity 规范化失败，拒绝生成不完整绩效",
        details={"error_type": type(exc).__name__},
      )

    self._record_t_trade_replay_tick_read_success(
      runtime,
      record_count=len(ticks),
      nonempty_pages=nonempty_pages,
      query_count=queries,
      hit_page_boundary=bool(nonempty_pages and len(ticks) % page_size == 0),
    )
    if nonempty_pages > 1 or (nonempty_pages and len(ticks) % page_size == 0):
      self._runtime_log(
        runtime,
        "INFO",
        "做 T 回放 Tick 分页读取完成: "
        f"instrument={instrument_code}, window={start_time}~{end_time}, "
        f"records={len(ticks)}, pages={nonempty_pages}, queries={queries}",
      )
    return ticks

  def _record_t_trade_replay_tick_read_success(
    self,
    runtime: StrategyRuntime,
    *,
    record_count: int,
    nonempty_pages: int,
    query_count: int,
    hit_page_boundary: bool,
  ) -> None:
    params = runtime.context.parameters
    audit = dict(params.get("replay_tick_read_audit") or {})
    audit.setdefault("schema_version", 1)
    audit["policy"] = "OFFSET_PAGINATION_FAIL_CLOSED"
    audit["page_size"] = max(1, int(_T_TRADE_REPLAY_TICK_PAGE_SIZE))
    audit["verified_windows"] = int(audit.get("verified_windows") or 0) + 1
    audit["records_read"] = int(audit.get("records_read") or 0) + record_count
    audit["pages_read"] = int(audit.get("pages_read") or 0) + nonempty_pages
    audit["queries"] = int(audit.get("queries") or 0) + query_count
    if nonempty_pages > 1:
      audit["paginated_windows"] = int(audit.get("paginated_windows") or 0) + 1
    if hit_page_boundary:
      audit["boundary_probe_windows"] = (
        int(audit.get("boundary_probe_windows") or 0) + 1
      )
    audit.setdefault("issues", [])
    params["replay_tick_read_audit"] = audit

  def _record_t_trade_replay_tick_read_issue(
    self,
    runtime: StrategyRuntime,
    *,
    instrument_code: str,
    start_time: datetime,
    end_time: datetime,
    reason_code: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
  ) -> None:
    params = runtime.context.parameters
    audit = dict(params.get("replay_tick_read_audit") or {})
    audit.setdefault("schema_version", 1)
    audit["policy"] = "OFFSET_PAGINATION_FAIL_CLOSED"
    audit["page_size"] = max(1, int(_T_TRADE_REPLAY_TICK_PAGE_SIZE))
    issues = list(audit.get("issues") or [])
    issues.append(
      {
        "instrument_code": instrument_code,
        "window_start": start_time.isoformat(),
        "window_end": end_time.isoformat(),
        "reason_code": reason_code,
        "message": message,
        "details": dict(details or {}),
      }
    )
    audit["issues"] = issues[-20:]
    params["replay_tick_read_audit"] = audit
    self._runtime_log(
      runtime,
      "ERROR",
      f"{message}: instrument={instrument_code}, window={start_time}~{end_time}, "
      f"reason={reason_code}, details={details or {}}",
    )

  def _raise_t_trade_replay_tick_data_partial(
    self,
    runtime: StrategyRuntime,
    *,
    instrument_code: str,
    start_time: datetime,
    end_time: datetime,
    reason_code: str,
    message: str,
    details: Optional[Dict[str, Any]] = None,
  ) -> None:
    self._record_t_trade_replay_tick_read_issue(
      runtime,
      instrument_code=instrument_code,
      start_time=start_time,
      end_time=end_time,
      reason_code=reason_code,
      message=message,
      details=details,
    )
    raise RuntimeError(
      f"DATA_PARTIAL: {message} "
      f"({instrument_code}, {start_time.isoformat()}~{end_time.isoformat()}, "
      f"reason={reason_code})"
    )

  def _get_backtest_window_hours(self) -> int:
    """获取回测回放窗口大小（小时）"""
    try:
      window_hours = int(getattr(settings, "backtest_replay_window_hours", 12))
    except Exception:
      window_hours = 12
    return max(1, window_hours)

  def _iter_backtest_windows(
    self, start_time: datetime, end_time: datetime, window_hours: int
  ):
    """生成回测时间窗口"""
    if end_time < start_time:
      return
    window_hours = max(1, int(window_hours))
    window_delta = timedelta(hours=window_hours)
    current = start_time
    while current <= end_time:
      window_end = min(end_time, current + window_delta)
      yield current, window_end
      current = window_end + timedelta(microseconds=1)

  def _get_backtest_warmup_bars(self, runtime: StrategyRuntime, period: str) -> int:
    """Return how many bars to preload before the formal backtest window."""
    params = dict(runtime.context.parameters or {})
    period_key = (period or "").lower().replace(" ", "")
    specific_keys = [
      f"backtest_warmup_bars_{period_key}",
      f"warmup_bars_{period_key}",
    ]
    for key in specific_keys + ["backtest_warmup_bars", "warmup_bars"]:
      if params.get(key) is not None:
        try:
          return max(0, min(2000, int(params.get(key) or 0)))
        except (TypeError, ValueError):
          continue

    candidates = [20]
    if period_key == "1d":
      for key in ("box_window_daily", "box_window", "atr_period"):
        if params.get(key) is not None:
          try:
            candidates.append(int(params.get(key) or 0))
          except (TypeError, ValueError):
            pass
    elif "60" in period_key or "1h" in period_key:
      for key in ("box_window_60m", "box_window"):
        if params.get(key) is not None:
          try:
            candidates.append(int(params.get(key) or 0))
          except (TypeError, ValueError):
            pass

    return max(0, min(2000, max(candidates)))

  def _get_backtest_warmup_start_time(
    self, start_time: datetime, period: str, warmup_bars: int
  ) -> datetime:
    period_key = (period or "").lower().replace(" ", "")
    if period_key == "1d":
      return start_time - timedelta(days=max(30, warmup_bars * 3))
    if period_key in {"1w", "week", "1week"}:
      return start_time - timedelta(days=max(70, warmup_bars * 10))
    if "60" in period_key or "1h" in period_key:
      return start_time - timedelta(days=max(10, warmup_bars // 4 + 5))
    if period_key.endswith("m"):
      try:
        minutes = max(1, int(period_key[:-1]))
      except (TypeError, ValueError):
        minutes = 1
      return start_time - timedelta(minutes=warmup_bars * minutes * 3)
    return start_time - timedelta(days=max(30, warmup_bars))

  def _is_backtest_intraday_period(self, period: str) -> bool:
    period_key = (period or "").lower().replace(" ", "")
    return period_key.endswith("m") or period_key.endswith("h")

  def _is_ashare_continuous_trading_time(self, timestamp: datetime) -> bool:
    """Return True only for A-share continuous auction sessions."""
    if not timestamp:
      return False
    local_time = (
      time_utils.to_shanghai(timestamp).time() if timestamp.tzinfo else timestamp.time()
    )
    return time(9, 30) <= local_time <= time(11, 30) or time(
      13, 0
    ) <= local_time < time(14, 57)

  def _filter_backtest_continuous_session_events(self, events: List[Any]) -> List[Any]:
    """Drop call-auction events from backtest replay."""
    return [
      event
      for event in (events or [])
      if getattr(event, "time", None)
      and self._is_ashare_continuous_trading_time(event.time)
    ]

  async def _run_backtest_warmup_klines(
    self,
    runtime: StrategyRuntime,
    instrument_code: str,
    periods: List[str],
    start_time: datetime,
  ) -> None:
    data_adapter = runtime.data_adapter
    strategy = runtime.strategy
    if not isinstance(data_adapter, HistoricalDataAdapter) or not strategy:
      return

    warmup_events: List[KLine] = []
    warmup_end = start_time - timedelta(microseconds=1)
    dividend_type = str(
      (runtime.context.parameters or {}).get("dividend_type", "none") or "none"
    )
    for period in periods:
      warmup_bars = self._get_backtest_warmup_bars(runtime, period)
      if warmup_bars <= 0:
        continue
      warmup_start = self._get_backtest_warmup_start_time(
        start_time, period, warmup_bars
      )
      klines = await data_adapter.get_klines(
        instrument_code=instrument_code,
        period=period,
        start_time=warmup_start,
        end_time=warmup_end,
        limit=warmup_bars,
        order="desc",
        dividend_type=dividend_type,
      )
      if self._is_backtest_intraday_period(period):
        klines = self._filter_backtest_continuous_session_events(klines)
      warmup_events.extend(
        k for k in (klines or []) if k is not None and k.time is not None
      )

    if not warmup_events:
      return

    warmup_events.sort(key=lambda kline: kline.time)
    for kline in warmup_events:
      if runtime.status != ExecutionStatus.RUNNING:
        break
      await self._process_warmup_kline(runtime, kline)

    self._runtime_log(
      runtime,
      "INFO",
      f"回测预热完成: {instrument_code}, start={start_time}, bars={len(warmup_events)}",
    )

  async def _process_warmup_kline(self, runtime: StrategyRuntime, kline: KLine) -> None:
    strategy = runtime.strategy
    if not strategy:
      return

    runtime.context.current_time = kline.time
    if isinstance(runtime.data_adapter, HistoricalDataAdapter):
      runtime.data_adapter.current_time = kline.time
    market_snapshot = MarketDataSnapshot.from_kline(
      kline,
      limit_rate=self._backtest_limit_rate(
        runtime,
        instrument_code=kline.stock_code,
        timestamp=kline.time,
      ),
    )
    runtime.latest_market_data[kline.stock_code] = market_snapshot
    strategy_input = self._build_strategy_input(
      runtime,
      cadence=StrategyCadence.BAR,
      instrument_code=kline.stock_code,
      timestamp=kline.time,
      market_data=market_snapshot,
      event=kline,
    )
    output = await strategy.warmup(strategy_input)
    if output and getattr(output, "runtime_state_patch", None):
      self._apply_runtime_state_patch(runtime, output.runtime_state_patch)

  def _persist_exit_plan_book(self, runtime: StrategyRuntime) -> None:
    if runtime.state_manager:
      runtime.state_manager.set_custom(
        EXIT_PLAN_BOOK_STATE_KEY,
        runtime.exit_plan_book.to_dict(),
      )

  def register_external_exit_plan(
    self,
    run_id: str,
    template: ExitPlanTemplate | Dict[str, Any],
    *,
    volume: int,
    price: float,
    trade_time: Optional[datetime] = None,
  ) -> Dict[str, Any]:
    """Register an audited fill that did not originate from this executor."""

    runtime = self.runs.get(run_id)
    if runtime is None:
      raise ValueError("策略运行不存在或尚未启动")
    plan = runtime.exit_plan_book.register_entry_fill(
      template,
      volume=volume,
      price=price,
      trade_time=trade_time,
    )
    runtime.exit_plan_book.prune_terminal(
      int(runtime.context.parameters.get("exit_plan_history_limit", 200) or 200)
    )
    self._persist_exit_plan_book(runtime)
    return plan.projection()

  async def _process_auto_exit_plans(
    self,
    runtime: StrategyRuntime,
    *,
    instrument_code: str,
    timestamp: datetime,
    market_data: MarketDataSnapshot,
  ) -> None:
    """Evaluate Engine-owned exit plans and route resulting SELL intents."""

    if not any(
      plan.template.instrument_code == instrument_code and plan.remaining_volume > 0
      for plan in runtime.exit_plan_book.plans.values()
    ):
      return
    from quantx_engine.exit_plan_monitor import exit_plan_monitor

    if (
      runtime.context.mode != StrategyRunMode.BACKTEST and exit_plan_monitor.is_running
    ):
      await AutoExitPlanService().sync_strategy_plan_book(
        strategy_run_id=runtime.run_id,
        book_state=runtime.exit_plan_book.to_dict(),
        execution_mode=runtime.context.mode.value,
      )
      return
    bids = list(getattr(market_data, "bid_price", []) or [])
    asks = list(getattr(market_data, "ask_price", []) or [])
    if runtime.context.parameters.get("exit_plan_replay"):
      requires_depth = any(
        any(
          rule.enabled and rule.strategy == "ADAPTIVE_VOLUME_PRICE_TRAILING"
          for rule in plan.template.rules
        )
        for plan in runtime.exit_plan_book.active_plans()
        if plan.template.instrument_code == instrument_code
      )
      if requires_depth and (
        not bids
        or not asks
        or not list(getattr(market_data, "bid_vol", []) or [])
        or not list(getattr(market_data, "ask_vol", []) or [])
      ):
        raise RuntimeError(
          f"EXIT_PLAN_REPLAY_DEPTH_DATA_MISSING:{instrument_code}:{timestamp.isoformat()}"
        )
    current_price = float(
      getattr(market_data, "price", 0.0) or getattr(market_data, "close", 0.0) or 0.0
    )
    configured_instrument = (
      runtime.context.parameters.get("instrument")
      or runtime.context.parameters.get("instrument_master")
      or {}
    )
    if isinstance(configured_instrument, dict):
      configured_limit_up = configured_instrument.get(
        "limit_up"
      ) or configured_instrument.get("up_stop_price")
      configured_limit_down = configured_instrument.get(
        "limit_down"
      ) or configured_instrument.get("down_stop_price")
      configured_price_tick = configured_instrument.get("price_tick")
    else:
      configured_limit_up = getattr(configured_instrument, "limit_up", None) or getattr(
        configured_instrument, "up_stop_price", None
      )
      configured_limit_down = getattr(
        configured_instrument, "limit_down", None
      ) or getattr(configured_instrument, "down_stop_price", None)
      configured_price_tick = getattr(configured_instrument, "price_tick", None)
    bid_depth = sum(list(getattr(market_data, "bid_vol", []) or [])[:5])
    ask_depth = sum(list(getattr(market_data, "ask_vol", []) or [])[:5])
    context = ExitEvaluationContext(
      timestamp=timestamp,
      current_price=current_price,
      bid_price=float(bids[0] if bids and bids[0] else 0.0),
      ask_price=float(asks[0] if asks and asks[0] else 0.0),
      limit_up=float(
        getattr(market_data, "limit_up", 0.0) or configured_limit_up or 0.0
      ),
      limit_down=float(
        getattr(market_data, "limit_down", 0.0) or configured_limit_down or 0.0
      ),
      price_tick=float(
        getattr(market_data, "price_tick", 0.0) or configured_price_tick or 0.01
      ),
      cumulative_volume=getattr(market_data, "volume", None),
      cumulative_amount=getattr(market_data, "amount", None),
      depth_imbalance_5=(
        (bid_depth - ask_depth) / (bid_depth + ask_depth)
        if bid_depth + ask_depth > 0
        else None
      ),
      source=str(getattr(market_data, "source", "") or ""),
    )
    decisions = runtime.exit_plan_book.evaluate(instrument_code, context)
    if not decisions:
      self._persist_exit_plan_book(runtime)
      return

    intents: List[TradeIntent] = []
    for decision in decisions:
      plan = runtime.exit_plan_book.plans.get(decision.plan_id)
      if plan is None:
        continue
      execution = plan.template.execution
      if execution.price_reference == ExitPriceReference.BID:
        price_hint = context.bid_price or context.current_price
      elif execution.price_reference == ExitPriceReference.ASK:
        price_hint = context.ask_price or context.current_price
      else:
        price_hint = context.current_price
      execution_mode = TradeIntentExecutionMode(
        str(execution.execution_mode or "AUTO").upper()
      )
      if runtime.context.mode == StrategyRunMode.LIVE:
        # LIVE automation is granted only to the persisted plan version by
        # ExitPlanAuthorizationChallengeService.  This in-memory fallback has
        # no durable challenge envelope to validate, so it must fail closed
        # even when an old strategy template contains the legacy boolean.
        execution_mode = TradeIntentExecutionMode.MANUAL_CONFIRM
      priority = (
        TradeIntentPriority.URGENT
        if decision.rule_type
        in {
          ExitRuleType.HARD_STOP.value,
          ExitRuleType.LIMIT_UP_TOUCH.value,
          ExitRuleType.LIMIT_UP_BREAK.value,
          ExitRuleType.RAPID_PROFIT_REVERSAL.value,
          ExitRuleType.STOP_PRICE.value,
        }
        else TradeIntentPriority.RISK_REDUCTION
      )
      intent = TradeIntent(
        strategy_id=plan.template.strategy_id or str(runtime.strategy_id),
        run_id=plan.template.run_id or runtime.run_id,
        instrument_code=plan.template.instrument_code,
        direction=TradeIntentDirection.SELL,
        bucket=plan.template.bucket,
        reason=f"AUTO_EXIT_{decision.reason}",
        priority=priority,
        target_volume=decision.volume,
        limit_price_hint=price_hint,
        execution_mode=execution_mode,
        max_price_deviation_bps=execution.max_slippage_bps,
        metadata={
          **dict(plan.template.metadata or {}),
          "exit_plan_id": plan.plan_id,
          "exit_rule_id": decision.rule_id,
          "exit_rule_type": decision.rule_type,
          "exit_reason": decision.reason,
          "exit_plan_source_type": plan.template.source_type,
          "exit_plan_source_id": plan.template.source_id,
          "exit_plan_config_version": plan.template.config_version,
          "price_type": execution.price_type,
          "price_reference": execution.price_reference.value,
          "protected_limit": execution.protected_limit,
          "max_exit_slippage_bps": execution.max_slippage_bps,
          "execution_urgency": execution.urgency,
          "t1_policy": plan.template.t1_policy.value,
          "allow_t1_substitution": (
            plan.template.t1_policy == ExitT1Policy.ALLOW_SAME_INSTRUMENT_SUBSTITUTION
          ),
          "t1_insufficient_action": (
            "REJECT"
            if plan.template.t1_policy == ExitT1Policy.REJECT_IF_UNSELLABLE
            else "DELAY"
          ),
          "exit_metrics": dict(decision.metrics or {}),
        },
      )
      runtime.exit_plan_book.mark_intent(decision, intent.intent_id)
      intents.append(intent)

    self._persist_exit_plan_book(runtime)
    if intents:
      await self._process_strategy_output(
        runtime,
        StrategyOutput(
          trade_intents=intents,
          decision_tags=["auto_exit_plan_triggered"],
          trace_payload={
            "exit_plan_ids": [
              str(intent.metadata.get("exit_plan_id") or "") for intent in intents
            ]
          },
        ),
      )

  def _apply_exit_plan_order_event(
    self, runtime: StrategyRuntime, event: OrderStateEvent
  ) -> None:
    metadata = dict(event.metadata or {})
    plan_id = str(metadata.get("exit_plan_id", "") or "")
    if not plan_id:
      return
    effective_status = event.status
    plan = runtime.exit_plan_book.plans.get(plan_id)
    normalized_status = str(event.status or "").upper()
    if (
      plan is not None
      and normalized_status in {"FILLED", "REJECTED", "CANCELLED", "EXPIRED"}
      and int(event.filled_volume or 0) > int(plan.pending_filled_volume or 0)
    ):
      # The terminal order projection can lead its execution report. Keep the
      # plan pending until fills catch up; otherwise the next tick can emit a
      # duplicate exit. A partial cancel's reported fill is the terminal target.
      if normalized_status != "FILLED":
        plan.pending_requested_volume = int(event.filled_volume or 0)
      effective_status = "FILLED"
    event_time = event.timestamp or runtime.context.current_time or time_utils.now()
    runtime.exit_plan_book.apply_order_event(
      plan_id=plan_id,
      intent_id=str(metadata.get("intent_id", "") or ""),
      status=effective_status,
      order_id=str(event.order_id or ""),
      risk_action=str(metadata.get("risk_action", "") or ""),
      timestamp_ms=int(event_time.timestamp() * 1000),
    )
    self._persist_exit_plan_book(runtime)

  def _apply_exit_plan_trade_event(
    self, runtime: StrategyRuntime, event: TradeExecutionEvent
  ) -> None:
    metadata = dict(event.metadata or {})
    trade_type = str(event.trade_type or "").upper()
    changed = False
    if trade_type == "BUY" and metadata.get("exit_plan_template"):
      runtime.exit_plan_book.register_entry_fill(
        metadata["exit_plan_template"],
        volume=event.volume,
        price=event.price,
        trade_time=event.trade_time or runtime.context.current_time,
      )
      changed = True
    elif trade_type == "SELL" and metadata.get("exit_plan_id"):
      runtime.exit_plan_book.apply_exit_fill(
        plan_id=str(metadata["exit_plan_id"]),
        volume=event.volume,
        price=event.price,
        rule_id=str(metadata.get("exit_rule_id", "") or ""),
      )
      changed = True
    if changed:
      runtime.exit_plan_book.prune_terminal(
        int(runtime.context.parameters.get("exit_plan_history_limit", 200) or 200)
      )
      self._persist_exit_plan_book(runtime)

  async def _run_backtest_timeline_with_ticks(
    self,
    runtime: StrategyRuntime,
    instrument_code: str,
    periods: List[str],
    start_time: datetime,
    end_time: datetime,
  ) -> None:
    """统一时间线回放：tick 驱动 + 触发多周期K线"""
    data_adapter = runtime.data_adapter
    if not isinstance(data_adapter, HistoricalDataAdapter):
      return

    total_ticks = 0
    total_klines_by_period = {period: 0 for period in periods}

    await self._run_backtest_warmup_klines(
      runtime, instrument_code, periods, start_time
    )

    market = "SH"  # 沪深市场的交易时间是一致的，所以使用 SH 就可以了

    trading_helper = TradingDateHelper()
    trading_dates = await trading_helper.get_trading_calendar(
      market=market,
      start_date=start_time.date(),
      end_date=end_time.date(),
    )
    if not trading_dates:
      self._runtime_log(
        runtime,
        "WARNING",
        f"回测区间无交易日: {instrument_code}, {start_time.date()} -> {end_time.date()}",
      )
      return

    window_hours = self._get_backtest_window_hours()
    last_tick_time: Optional[datetime] = None
    last_kline_time: Dict[str, Optional[datetime]] = {
      period: None for period in periods
    }

    session_start = time(9, 30)
    session_end = time(15, 30)

    for trading_date in trading_dates:
      if runtime.status != ExecutionStatus.RUNNING:
        break

      day_start = datetime.combine(trading_date, session_start)
      day_end = datetime.combine(trading_date, session_end)
      day_window_start = max(start_time, day_start)
      day_window_end = min(end_time, day_end)

      if day_window_end < day_window_start:
        continue

      for window_start, window_end in self._iter_backtest_windows(
        day_window_start, day_window_end, window_hours
      ):
        if runtime.status != ExecutionStatus.RUNNING:
          break

        self._runtime_log(
          runtime,
          "INFO",
          f"回测窗口开始: {instrument_code}, {window_start} -> {window_end}",
        )
        self.logger.info(
          "回测tick查询窗口: %s, local=%s~%s, utc=%s~%s",
          instrument_code,
          window_start,
          window_end,
          time_utils.to_utc(window_start),
          time_utils.to_utc(window_end),
        )

        ticks = await self._load_backtest_ticks(
          runtime,
          data_adapter,
          instrument_code=instrument_code,
          start_time=window_start,
          end_time=window_end,
        )
        if ticks:
          ticks = [
            t
            for t in ticks
            if t is not None
            and t.time is not None
            and (last_tick_time is None or t.time > last_tick_time)
          ]
          ticks = self._filter_backtest_continuous_session_events(ticks)
        else:
          ticks = []

        self._runtime_log(
          runtime,
          "INFO",
          f"回测tick查询结果: {instrument_code}, {window_start.date()}, ticks={len(ticks)}",
        )

        all_klines: Dict[str, List[KLine]] = {}
        for period in periods:
          period_lower = period.lower()
          is_intraday = period_lower.endswith("m") or period_lower.endswith("h")
          if is_intraday:
            kline_start = window_start
            kline_end = window_end
          else:
            kline_start = datetime.combine(trading_date, time(0, 0))
            kline_end = datetime.combine(trading_date, time(23, 59, 59))

          klines = await data_adapter.get_klines(
            instrument_code=instrument_code,
            period=period,
            start_time=kline_start,
            end_time=kline_end,
          )
          if klines:
            last_time = last_kline_time.get(period)
            if last_time is not None:
              klines = [
                k
                for k in klines
                if k is not None and k.time is not None and k.time > last_time
              ]
            else:
              klines = [k for k in klines if k is not None and k.time is not None]
            if self._is_backtest_intraday_period(period):
              klines = self._filter_backtest_continuous_session_events(klines)
          else:
            klines = []

          all_klines[period] = klines

        if not ticks and all(not v for v in all_klines.values()):
          self._runtime_log(
            runtime, "INFO", f"回测窗口无数据: {instrument_code}, {window_start.date()}"
          )
          continue

        tick_idx = 0
        kline_indices = {period: 0 for period in periods}
        kline_end_times: Dict[str, datetime] = {}

        kline_time_alignment = (
          runtime.context.parameters.get("kline_time_alignment", "end") or "end"
        ).lower()

        for period, klines in all_klines.items():
          if klines:
            kline_end_times[period] = self._get_kline_end_time(
              klines[0], period, alignment=kline_time_alignment
            )

        def has_more_data() -> bool:
          return tick_idx < len(ticks) or any(
            kline_indices[p] < len(all_klines[p]) for p in periods if p in all_klines
          )

        while has_more_data():
          if runtime.status != ExecutionStatus.RUNNING:
            break

          if tick_idx < len(ticks):
            tick = ticks[tick_idx]
            await self._process_tick(runtime, tick)
            if tick.time and (last_tick_time is None or tick.time > last_tick_time):
              last_tick_time = tick.time

            # tick 驱动 K 线触发（可能跨越多根K线）
            for period in periods:
              klines = all_klines.get(period, [])
              kline_idx = kline_indices[period]
              while (
                kline_idx < len(klines)
                and period in kline_end_times
                and tick.time >= kline_end_times[period]
              ):
                kline = klines[kline_idx]
                await self._process_kline(runtime, kline)
                if kline.time and (
                  last_kline_time.get(period) is None
                  or kline.time > last_kline_time[period]
                ):
                  last_kline_time[period] = kline.time
                kline_idx += 1
                if kline_idx < len(klines):
                  kline_end_times[period] = self._get_kline_end_time(
                    klines[kline_idx], period, alignment=kline_time_alignment
                  )

              kline_indices[period] = kline_idx

            tick_idx += 1
            continue

          # 无 tick 时，按时间顺序处理剩余K线
          next_period = None
          next_time = None
          for period in periods:
            kline_idx = kline_indices[period]
            klines = all_klines.get(period, [])
            if kline_idx < len(klines):
              kline_time = klines[kline_idx].time
              if next_time is None or kline_time < next_time:
                next_time = kline_time
                next_period = period

          if not next_period:
            break

          kline = all_klines[next_period][kline_indices[next_period]]
          await self._process_kline(runtime, kline)
          if kline.time and (
            last_kline_time.get(next_period) is None
            or kline.time > last_kline_time[next_period]
          ):
            last_kline_time[next_period] = kline.time
          kline_indices[next_period] += 1
          if kline_indices[next_period] < len(all_klines[next_period]):
            kline_end_times[next_period] = self._get_kline_end_time(
              all_klines[next_period][kline_indices[next_period]],
              next_period,
              alignment=kline_time_alignment,
            )

        total_ticks += tick_idx
        for period in periods:
          total_klines_by_period[period] += kline_indices.get(period, 0)

        if periods:
          per_period_summary = ", ".join(
            f"{period}:{kline_indices.get(period, 0)}" for period in periods
          )
        else:
          per_period_summary = "none"

        self._runtime_log(
          runtime,
          "INFO",
          f"回测窗口完成: {instrument_code}, {window_start} -> {window_end}, "
          f"tick={tick_idx}, kline={per_period_summary}",
        )
      if runtime.status == ExecutionStatus.RUNNING:
        await self._report_t_trade_replay_progress(
          runtime,
          processed_until=day_window_end,
          force=True,
        )

    total_klines = sum(total_klines_by_period.values())
    self._runtime_log(
      runtime,
      "SUCCESS",
      f"统一时间线回测完成: {instrument_code}, "
      f"处理了 {total_ticks} 个tick和 {total_klines} 根K线",
    )

  async def _run_backtest_timeline_with_klines(
    self,
    runtime: StrategyRuntime,
    instrument_code: str,
    periods: List[str],
    start_time: datetime,
    end_time: datetime,
  ) -> None:
    """统一时间线回放：多周期K线按时间顺序回放"""
    data_adapter = runtime.data_adapter
    if not isinstance(data_adapter, HistoricalDataAdapter):
      return

    market = runtime.context.parameters.get("market")
    if not market:
      market = "SZ" if instrument_code.endswith(".SZ") else "SH"

    trading_helper = TradingDateHelper()
    trading_dates = await trading_helper.get_trading_calendar(
      market=market,
      start_date=start_time.date(),
      end_date=end_time.date(),
    )
    if not trading_dates:
      self._runtime_log(
        runtime,
        "WARNING",
        f"回测区间无交易日: {instrument_code}, {start_time.date()} -> {end_time.date()}",
      )
      return

    total_klines_by_period = {period: 0 for period in periods}
    window_hours = self._get_backtest_window_hours()
    last_kline_time: Dict[str, Optional[datetime]] = {
      period: None for period in periods
    }

    await self._run_backtest_warmup_klines(
      runtime, instrument_code, periods, start_time
    )

    for trading_date in trading_dates:
      if runtime.status != ExecutionStatus.RUNNING:
        break

      day_window_start = max(start_time, datetime.combine(trading_date, time(0, 0)))
      day_window_end = min(end_time, datetime.combine(trading_date, time(23, 59, 59)))

      if day_window_end < day_window_start:
        continue

      for window_start, window_end in self._iter_backtest_windows(
        day_window_start, day_window_end, window_hours
      ):
        if runtime.status != ExecutionStatus.RUNNING:
          break

        all_klines: Dict[str, List[KLine]] = {}
        for period in periods:
          klines = await data_adapter.get_klines(
            instrument_code=instrument_code,
            period=period,
            start_time=window_start,
            end_time=window_end,
          )
          if klines:
            last_time = last_kline_time.get(period)
            if last_time is not None:
              klines = [
                k
                for k in klines
                if k is not None and k.time is not None and k.time > last_time
              ]
            else:
              klines = [k for k in klines if k is not None and k.time is not None]
            if self._is_backtest_intraday_period(period):
              klines = self._filter_backtest_continuous_session_events(klines)
          else:
            klines = []
          all_klines[period] = klines

        if all(not v for v in all_klines.values()):
          self._runtime_log(
            runtime,
            "INFO",
            f"回测窗口无数据: {instrument_code}, {window_start} -> {window_end}",
          )
          continue

        kline_indices = {period: 0 for period in periods}

        def has_more_klines() -> bool:
          return any(
            kline_indices[p] < len(all_klines[p]) for p in periods if p in all_klines
          )

        while has_more_klines():
          if runtime.status != ExecutionStatus.RUNNING:
            break

          next_period = None
          next_time = None
          for period in periods:
            kline_idx = kline_indices[period]
            klines = all_klines.get(period, [])
            if kline_idx < len(klines):
              kline_time = klines[kline_idx].time
              if next_time is None or kline_time < next_time:
                next_time = kline_time
                next_period = period

          if not next_period:
            break

          kline = all_klines[next_period][kline_indices[next_period]]
          await self._process_kline(runtime, kline)
          if kline.time and (
            last_kline_time.get(next_period) is None
            or kline.time > last_kline_time[next_period]
          ):
            last_kline_time[next_period] = kline.time
          kline_indices[next_period] += 1

        for period in periods:
          total_klines_by_period[period] += kline_indices.get(period, 0)

        if periods:
          per_period_summary = ", ".join(
            f"{period}:{kline_indices.get(period, 0)}" for period in periods
          )
        else:
          per_period_summary = "none"

        self._runtime_log(
          runtime,
          "INFO",
          f"回测窗口完成: {instrument_code}, {window_start} -> {window_end}, "
          f"kline={per_period_summary}",
        )
      if runtime.status == ExecutionStatus.RUNNING:
        await self._report_t_trade_replay_progress(
          runtime,
          processed_until=day_window_end,
          force=True,
        )

    total_klines = sum(total_klines_by_period.values())
    self._runtime_log(
      runtime,
      "SUCCESS",
      f"统一时间线回测完成: {instrument_code}, 处理了 {total_klines} 根K线",
    )

  def _get_kline_end_time(
    self,
    kline: KLine,
    period: str,
    alignment: str = "end",
  ) -> datetime:
    """获取K线结束时间

    alignment:
      - "end": kline.time 表示该K线结束时间（更常见）
      - "start": kline.time 表示该K线开始时间
    """
    period_map = {
      "1m": timedelta(minutes=1),
      "5m": timedelta(minutes=5),
      "15m": timedelta(minutes=15),
      "30m": timedelta(minutes=30),
      "60m": timedelta(hours=1),
      "1h": timedelta(hours=1),
      "1d": timedelta(days=1),
      "1w": timedelta(days=7),
    }
    alignment = (alignment or "end").lower()
    if alignment == "start":
      return kline.time + period_map.get(period, timedelta(minutes=1))
    return kline.time

  async def _notify_strategy_order(
    self,
    runtime: StrategyRuntime,
    event: OrderStateEvent,
    *,
    raise_on_error: bool = False,
  ) -> Any:
    """Notify strategy about an order event and consume any returned state patch."""
    if not runtime.strategy:
      return None
    try:
      self._apply_exit_plan_order_event(runtime, event)
      self._update_t_trade_entry_reservation(runtime, event)
      result = runtime.strategy.on_order(event)
      patch = await result if inspect.isawaitable(result) else result
      if patch:
        self._apply_runtime_state_patch(runtime, patch)
      self._refresh_t_trade_entry_reservation(runtime, event)
      return patch
    except Exception as exc:
      if runtime.metrics:
        runtime.metrics.error_count += 1
      self._runtime_log(runtime, "ERROR", f"策略订单回调失败: {exc}")
      if raise_on_error:
        raise
      return None

  async def _notify_strategy_trade(
    self,
    runtime: StrategyRuntime,
    event: TradeExecutionEvent,
    *,
    raise_on_error: bool = False,
  ) -> Any:
    """Notify strategy about a trade event and consume any returned state patch."""
    if not runtime.strategy:
      return None
    try:
      self._apply_exit_plan_trade_event(runtime, event)
      result = runtime.strategy.on_trade(event)
      patch = await result if inspect.isawaitable(result) else result
      if patch:
        self._apply_runtime_state_patch(runtime, patch)
      self._refresh_t_trade_entry_reservation(runtime, event)
      return patch
    except Exception as exc:
      if runtime.metrics:
        runtime.metrics.error_count += 1
      self._runtime_log(runtime, "ERROR", f"策略成交回调失败: {exc}")
      if raise_on_error:
        raise
      return None

  def _update_broker_report_health(
    self,
    runtime: StrategyRuntime,
    report_type: str,
    report: Any,
  ) -> None:
    reported_at = self._extract_report_time(report) or runtime.context.current_time
    reported_at = reported_at or time_utils.now()
    if report_type == "order":
      runtime.last_order_report_at = reported_at
    elif report_type == "trade":
      runtime.last_trade_report_at = reported_at
    runtime.last_broker_report_at = reported_at

  def _extract_report_time(self, report: Any) -> Optional[datetime]:
    for key in ("last_update_time", "trade_time", "submit_time", "timestamp"):
      value = self._get_value(report, key)
      if isinstance(value, datetime):
        return value
    return None

  def _build_open_order_snapshots(
    self,
    runtime: StrategyRuntime,
  ) -> List[Dict[str, Any]]:
    broker = runtime.broker
    if not broker:
      return []

    orders_by_id: Dict[str, Any] = {}
    raw_orders = getattr(broker, "orders", None)
    if isinstance(raw_orders, dict):
      for order_id, order in raw_orders.items():
        orders_by_id[str(order_id)] = order

    pending_orders = getattr(broker, "pending_orders", None)
    if isinstance(pending_orders, list):
      for order in pending_orders:
        order_id = str(self._get_value(order, "order_id", "") or "")
        if order_id:
          orders_by_id[order_id] = order

    snapshots: List[Dict[str, Any]] = []
    for order in orders_by_id.values():
      status = str(self._enum_value(self._get_value(order, "status", "")) or "").upper()
      if status not in {"PENDING", "SUBMITTED", "PARTIAL_FILLED"}:
        continue
      snapshots.append(self._summarize_open_order(order))

    return sorted(
      snapshots,
      key=lambda item: (
        str(item.get("submit_time") or ""),
        str(item.get("order_id") or ""),
      ),
    )

  def _summarize_open_order(self, order: Any) -> Dict[str, Any]:
    request = self._get_value(order, "request", {}) or {}
    volume = int(self._get_value(request, "volume", 0) or 0)
    filled_volume = int(self._get_value(order, "filled_volume", 0) or 0)
    return {
      "order_id": str(self._get_value(order, "order_id", "") or ""),
      "status": self._enum_value(self._get_value(order, "status", "")),
      "instrument_code": str(self._get_value(request, "instrument_code", "") or ""),
      "order_type": self._enum_value(self._get_value(request, "order_type", "")),
      "price_type": self._enum_value(self._get_value(request, "price_type", "")),
      "price": float(self._get_value(request, "price", 0.0) or 0.0),
      "volume": volume,
      "filled_volume": filled_volume,
      "remaining_volume": max(0, volume - filled_volume),
      "submit_time": self._serialize_datetime(self._get_value(order, "submit_time")),
      "last_update_time": self._serialize_datetime(
        self._get_value(order, "last_update_time")
      ),
      "metadata": dict(self._get_value(request, "metadata", {}) or {}),
    }

  def _build_order_state(self, open_orders: List[Dict[str, Any]]) -> Dict[str, Any]:
    status_counts: Dict[str, int] = {}
    order_type_counts: Dict[str, int] = {}
    buy_count = 0
    sell_count = 0
    oldest_open_order_at: Optional[str] = None
    for order in open_orders:
      status = str(order.get("status") or "").upper()
      order_type = str(order.get("order_type") or "").upper()
      status_counts[status] = status_counts.get(status, 0) + 1
      order_type_counts[order_type] = order_type_counts.get(order_type, 0) + 1
      if order_type in {"BUY", "BUY_TO_COVER"}:
        buy_count += 1
      elif order_type in {"SELL", "SELL_SHORT"}:
        sell_count += 1
      submit_time = order.get("submit_time")
      if submit_time and (
        oldest_open_order_at is None or str(submit_time) < oldest_open_order_at
      ):
        oldest_open_order_at = str(submit_time)
    return {
      "open_order_count": len(open_orders),
      "buy_open_order_count": buy_count,
      "sell_open_order_count": sell_count,
      "open_order_status_counts": status_counts,
      "open_order_type_counts": order_type_counts,
      "oldest_open_order_at": oldest_open_order_at,
    }

  def _build_broker_report(self, runtime: StrategyRuntime) -> Dict[str, Any]:
    last_report_at = runtime.last_broker_report_at
    if not last_report_at:
      return {}
    reference_time = runtime.context.current_time or time_utils.now()
    report_lag_seconds = max(
      0.0,
      (reference_time - last_report_at).total_seconds(),
    )
    return {
      "last_order_report_at": self._serialize_datetime(runtime.last_order_report_at),
      "last_trade_report_at": self._serialize_datetime(runtime.last_trade_report_at),
      "last_report_at": self._serialize_datetime(last_report_at),
      "report_lag_seconds": report_lag_seconds,
    }

  def _order_risk_strict_flags(self, runtime: StrategyRuntime) -> tuple[bool, bool]:
    params = dict(runtime.context.parameters or {})
    strict_market_default = runtime.context.mode in {
      StrategyRunMode.LIVE,
      StrategyRunMode.BACKTEST,
      StrategyRunMode.PAPER,
    }
    strict_limit_default = runtime.context.mode in {
      StrategyRunMode.LIVE,
      StrategyRunMode.BACKTEST,
    }
    return (
      self._bool_parameter(params, "strict_market_data", strict_market_default),
      self._bool_parameter(params, "strict_limit_data", strict_limit_default),
    )

  @staticmethod
  def _backtest_limit_rate(
    runtime: StrategyRuntime,
    *,
    instrument_code: str = "",
    timestamp: Optional[datetime] = None,
  ) -> Optional[float]:
    """Resolve a strict backtest-only daily limit-rate fallback.

    Explicit strategy configuration remains authoritative for generic
    backtests. T-assistant replays may instead use stable instrument lifecycle
    facts plus the event date. Ambiguous lifecycle/status inputs return no rate
    and therefore remain rejected by strict order risk.
    """

    if runtime.context.mode != StrategyRunMode.BACKTEST:
      return None
    try:
      rate = float(runtime.context.parameters.get("backtest_limit_rate", 0) or 0)
    except (TypeError, ValueError):
      rate = 0.0
    if 0 < rate < 1:
      return rate

    parameters = dict(runtime.context.parameters or {})
    if not parameters.get("t_trade_replay"):
      return None
    code = str(instrument_code or "").strip().upper()
    event_time = timestamp or runtime.context.current_time
    if not code or event_time is None:
      return None
    metadata = dict(parameters.get("initial_instrument_metadata") or {})
    instrument = dict(metadata.get(code) or {})
    raw_is_st = instrument.get("is_st")
    is_st = raw_is_st if isinstance(raw_is_st, bool) else None
    return resolve_ashare_daily_limit_rate(
      code,
      event_time,
      instrument_name=str(instrument.get("instrument_name") or ""),
      status_as_of_date=instrument.get("instrument_status_as_of"),
      listing_date=instrument.get("listing_date"),
      expiry_date=instrument.get("expiry_date"),
      is_st=is_st,
    )

  @staticmethod
  def _record_t_trade_replay_price_limit_source(
    runtime: StrategyRuntime,
    market_snapshot: MarketDataSnapshot,
  ) -> None:
    parameters = runtime.context.parameters
    if not parameters.get("t_trade_replay"):
      return
    source = str(market_snapshot.source or "").lower()
    event_kind = "KLINE" if source.startswith("kline") else "TICK"
    if source.endswith("_derived_limits"):
      limit_kind = "DERIVED"
    elif (
      market_snapshot.limit_up is not None and market_snapshot.limit_down is not None
    ):
      limit_kind = "NATIVE"
    else:
      limit_kind = "MISSING"
    counts = parameters.setdefault("replay_price_limit_source_counts", {})
    key = f"{limit_kind}_{event_kind}"
    counts[key] = int(counts.get(key, 0) or 0) + 1

  def _bool_parameter(
    self,
    params: Dict[str, Any],
    key: str,
    default: bool,
  ) -> bool:
    value = params.get(key)
    if value is None:
      return default
    if isinstance(value, bool):
      return value
    if isinstance(value, str):
      text = value.strip().lower()
      if text in {"1", "true", "yes", "y", "on"}:
        return True
      if text in {"0", "false", "no", "n", "off"}:
        return False
    return bool(value)

  def _serialize_datetime(self, value: Any) -> Optional[str]:
    if value is None:
      return None
    if isinstance(value, datetime):
      return value.isoformat()
    return str(value)

  def _enum_value(self, value: Any) -> Any:
    return getattr(value, "value", value)

  def _get_value(self, source: Any, key: str, default: Any = None) -> Any:
    if source is None:
      return default
    if isinstance(source, dict):
      return source.get(key, default)
    return getattr(source, key, default)

  def _durable_runtime_event_key(self, data: Any) -> str:
    request = self._get_value(data, "request")
    metadata = dict(self._get_value(data, "metadata", {}) or {})
    if not metadata:
      metadata = dict(self._get_value(request, "metadata", {}) or {})
    return str(metadata.get("runtime_event_key") or "").strip()

  def _capture_durable_runtime_state(self, runtime: StrategyRuntime) -> dict[str, Any]:
    """Capture all in-memory truth touched before a durable checkpoint."""
    state_manager = runtime.state_manager
    return {
      "manager_state": copy.deepcopy(state_manager._state),
      "manager_dirty": state_manager._dirty,
      "manager_dirty_revision": state_manager._dirty_revision,
      "manager_last_snapshot_attempt_revision": (
        state_manager._last_snapshot_attempt_revision
      ),
      "manager_reservations": copy.deepcopy(state_manager._reservations),
      "manager_position_reservations": copy.deepcopy(
        state_manager._position_reservations
      ),
      "manager_bucket_ledger": copy.deepcopy(state_manager._bucket_ledger),
      "t_trade_entry_reservations": copy.deepcopy(runtime.t_trade_entry_reservations),
      "strategy_state": (
        copy.deepcopy(runtime.strategy.state.to_dict())
        if runtime.strategy is not None
        else None
      ),
      "exit_plan_book": copy.deepcopy(runtime.exit_plan_book.to_dict()),
      "metrics": copy.deepcopy(runtime.metrics),
      "last_order_report_at": runtime.last_order_report_at,
      "last_trade_report_at": runtime.last_trade_report_at,
      "last_broker_report_at": runtime.last_broker_report_at,
    }

  def _restore_durable_runtime_state(
    self,
    runtime: StrategyRuntime,
    snapshot: dict[str, Any],
  ) -> None:
    """Rollback a durable callback that failed before installing its marker."""
    state_manager = runtime.state_manager
    state_manager._state = copy.deepcopy(snapshot["manager_state"])
    state_manager._dirty = bool(snapshot["manager_dirty"])
    state_manager._dirty_revision = int(snapshot["manager_dirty_revision"])
    state_manager._last_snapshot_attempt_revision = int(
      snapshot["manager_last_snapshot_attempt_revision"]
    )
    state_manager._reservations = copy.deepcopy(snapshot["manager_reservations"])
    state_manager._position_reservations = copy.deepcopy(
      snapshot["manager_position_reservations"]
    )
    state_manager._bucket_ledger = copy.deepcopy(snapshot["manager_bucket_ledger"])
    runtime.t_trade_entry_reservations = copy.deepcopy(
      snapshot["t_trade_entry_reservations"]
    )
    if runtime.strategy is not None and snapshot["strategy_state"] is not None:
      runtime.strategy.state.replace(
        copy.deepcopy(snapshot["strategy_state"]),
        notify=False,
      )
    runtime.exit_plan_book = ExitPlanBook.from_dict(
      copy.deepcopy(snapshot["exit_plan_book"]),
      evaluator=ExitPlanEvaluator(self.exit_strategy_registry),
    )
    runtime.metrics = copy.deepcopy(snapshot["metrics"])
    runtime.last_order_report_at = snapshot["last_order_report_at"]
    runtime.last_trade_report_at = snapshot["last_trade_report_at"]
    runtime.last_broker_report_at = snapshot["last_broker_report_at"]

  async def _process_event_queue(self, runtime: StrategyRuntime) -> None:
    """串行处理事件队列"""
    while (
      runtime.status in [ExecutionStatus.RUNNING, ExecutionStatus.PAUSED]
      and not self._shutdown_event.is_set()
    ):
      acquired_queue: Optional[asyncio.Queue] = None
      market_processing_context: Optional[tuple[str, int, float]] = None
      durable_rollback_snapshot = None
      durable_event_key = ""
      durable_strategy_patch = None
      paper_fill_fact: Optional[Dict[str, Any]] = None
      paper_candidate_trade = False
      try:
        completion = None
        next_event = await self._next_runtime_event(runtime)
        if next_event is None:
          await self._maybe_coordinate_session_checkpoints(runtime)
          continue
        acquired_queue, event_type, data, enqueued_at = next_event
        market_event = acquired_queue is runtime.market_event_queue
        market_event_code = self._runtime_market_event_code(data)
        if market_event:
          queued_at = monotonic() if enqueued_at is None else float(enqueued_at)
          queue_age = max(0.0, monotonic() - queued_at)
          if queue_age > MARKET_STREAM_MAX_CAPTURE_AGE_SECONDS:
            runtime.market_events_expired += 1
            runtime.market_events_dropped += 1
            dropped, affected = self._drain_runtime_market_queue(runtime)
            if market_event_code:
              affected.add(market_event_code)
            self._mark_runtime_market_continuity_lost(
              runtime,
              affected,
              reason="MARKET_EVENT_PROCESSING_EXPIRED",
            )
            await self._apply_pending_runtime_market_invalidations(runtime)
            self.logger.warning(
              "策略实时行情处理超时，已清空积压并失效观察窗: "
              "run_id=%s age=%.3fs additionally_dropped=%s instruments=%s",
              runtime.run_id,
              queue_age,
              dropped,
              sorted(affected),
            )
            continue
          if event_type == "tick":
            source_age = self._runtime_tick_source_age_seconds(data)
            if (
              source_age is None
              or source_age > MARKET_STREAM_MAX_CAPTURE_AGE_SECONDS
              or source_age < -MARKET_STREAM_MAX_FUTURE_SKEW_SECONDS
            ):
              runtime.market_tick_source_rejections += 1
              runtime.market_events_dropped += 1
              dropped, affected = self._drain_runtime_market_queue(runtime)
              if market_event_code:
                affected.add(market_event_code)
              reason = (
                "MARKET_TICK_SOURCE_TIMESTAMP_INVALID"
                if source_age is None
                else "MARKET_TICK_SOURCE_STALE"
              )
              self._mark_runtime_market_continuity_lost(
                runtime,
                affected,
                reason=reason,
              )
              await self._apply_pending_runtime_market_invalidations(runtime)
              self.logger.warning(
                "策略实时 Tick 源时间不新鲜，已清空积压并失效观察窗: "
                "run_id=%s source_age=%s additionally_dropped=%s instruments=%s",
                runtime.run_id,
                source_age,
                dropped,
                sorted(affected),
              )
              continue
          await self._apply_pending_runtime_market_invalidations(runtime)
          if market_event_code in runtime._market_fail_closed_codes:
            runtime.market_events_dropped += 1
            continue

        durable_event = event_type in {"durable_order", "durable_trade"}
        if runtime.durable_event_barrier_key and not durable_event:
          if event_type in {"tick", "kline"}:
            _dropped, affected = self._drain_runtime_market_queue(runtime)
            if market_event:
              runtime.market_events_dropped += 1
            if market_event_code:
              affected.add(market_event_code)
            self._mark_runtime_market_continuity_lost(
              runtime,
              affected,
              reason="DURABLE_EVENT_BARRIER",
            )
            await self._apply_pending_runtime_market_invalidations(runtime)
          if event_type == "universe" and isinstance(data, dict):
            future = data.get("future")
            if future is not None and not future.done():
              future.set_exception(
                RuntimeError("持久化回报尚未完成快照，暂不执行标的池变更")
              )
          continue
        if durable_event:
          data, completion = data
          event_type = event_type.removeprefix("durable_")
          durable_event_key = self._durable_runtime_event_key(data)
          if not durable_event_key:
            raise RuntimeError("持久化运行时事件缺少稳定业务键")
          if runtime.state_manager is None:
            raise RuntimeError("持久化运行时事件缺少状态管理器")
          if (
            runtime.durable_event_barrier_key
            and durable_event_key != runtime.durable_event_barrier_key
          ):
            raise RuntimeError(
              f"持久化运行时事件屏障仍在等待: {runtime.durable_event_barrier_key}"
            )
          if runtime.state_manager.has_applied_runtime_event(durable_event_key):
            checkpointed = await runtime.state_manager.checkpoint_durable_runtime_event(
              durable_event_key
            )
            if not checkpointed:
              raise RuntimeError(f"持久化运行时事件快照重试失败: {durable_event_key}")
            if (
              runtime.durable_event_barrier_key == durable_event_key
              and not runtime.durable_startup_barrier
            ):
              runtime.durable_event_barrier_key = None
            if event_type == "trade":
              await self._record_t_trade_candidate_fill(
                runtime,
                data,
                durable_event=True,
              )
            if completion is not None and not completion.done():
              completion.set_result(True)
            continue
          durable_rollback_snapshot = self._capture_durable_runtime_state(runtime)

        if runtime.status == ExecutionStatus.PAUSED and event_type not in [
          "order",
          "trade",
          "universe",
        ]:
          if event_type in {"tick", "kline"}:
            _dropped, affected = self._drain_runtime_market_queue(runtime)
            if market_event:
              runtime.market_events_dropped += 1
            if market_event_code:
              affected.add(market_event_code)
            self._mark_runtime_market_continuity_lost(
              runtime,
              affected,
              reason="RUNTIME_PAUSED",
            )
            await self._apply_pending_runtime_market_invalidations(runtime)
          continue

        if market_event and event_type in {"tick", "kline"}:
          processing_enqueued_at = (
            monotonic() if enqueued_at is None else float(enqueued_at)
          )
          processing_generation = runtime._market_continuity_generations.get(
            market_event_code,
            0,
          )
          market_processing_context = (
            market_event_code,
            processing_generation,
            processing_enqueued_at,
          )
          runtime._processing_market_events[market_event_code] = (
            processing_generation,
            processing_enqueued_at,
          )

        # 根据事件类型分发
        if event_type == "kline":
          await self._process_kline(runtime, data)
          if market_event:
            if runtime._pending_market_invalidations:
              await self._apply_pending_runtime_market_invalidations(runtime)
            if (
              market_processing_context is not None
              and runtime._market_continuity_generations.get(
                market_event_code,
                0,
              )
              == market_processing_context[1]
            ):
              runtime.market_events_processed += 1
              self._record_processed_market_watermark(
                runtime,
                data,
                instrument_code=market_event_code,
              )
        elif event_type == "tick":
          await self._process_tick(runtime, data)
          if market_event:
            if runtime._pending_market_invalidations:
              await self._apply_pending_runtime_market_invalidations(runtime)
            if (
              market_processing_context is not None
              and runtime._market_continuity_generations.get(
                market_event_code,
                0,
              )
              == market_processing_context[1]
            ):
              runtime.market_events_processed += 1
              self._record_processed_market_watermark(
                runtime,
                data,
                instrument_code=market_event_code,
              )
        elif event_type == "entry_plan_evaluate":
          await self._process_entry_plan_evaluate(runtime, data)
        elif event_type == "order":
          self._update_broker_report_health(runtime, "order", data)
          if runtime.state_manager and hasattr(data, "status"):
            status = data.status
            request = getattr(data, "request", None)
            metadata = dict(getattr(request, "metadata", {}) or {})
            if not durable_event:
              await runtime.state_manager.update_trade_intent_status(
                metadata.get("intent_id"),
                getattr(status, "value", str(status)),
                order_id=getattr(data, "order_id", None),
                risk_decision_id=metadata.get("risk_decision_id"),
              )
            if status in [
              OrderStatus.CANCELLED,
              OrderStatus.REJECTED,
              OrderStatus.EXPIRED,
            ]:
              runtime.state_manager.release_order_resources(data.order_id)
              if runtime.metrics:
                if status == OrderStatus.CANCELLED:
                  runtime.metrics.cancelled_orders += 1
                else:
                  runtime.metrics.rejected_orders += 1

          if durable_event and runtime.strategy is not None:
            with runtime.strategy.state.silent(
              persist=False,
              notify=False,
              flush_on_exit=False,
            ):
              durable_strategy_patch = await self._notify_strategy_order(
                runtime,
                OrderStateEvent.from_raw(data),
                raise_on_error=True,
              )
          else:
            await self._notify_strategy_order(
              runtime,
              OrderStateEvent.from_raw(data),
            )
          if runtime.performance_recorder and not durable_event:
            await runtime.performance_recorder.record(runtime, "order", data)

        elif event_type == "trade":
          trade_metadata = dict(getattr(data, "metadata", {}) or {})
          paper_candidate_trade = bool(
            runtime.context.mode == StrategyRunMode.PAPER
            and not durable_event
            and str(trade_metadata.get("candidate_id") or "").strip()
          )
          if paper_candidate_trade:
            # Validate/freeze the complete fact before any account, strategy or
            # intent state is advanced.  A malformed simulator callback can
            # therefore fail-stop without requiring a best-effort rollback.
            paper_fill_fact = await self._build_t_trade_paper_fill_fact(runtime, data)
            if paper_fill_fact is None:
              raise RuntimeError("做 T PAPER 候选成交未生成 durable fact")
            durable_rollback_snapshot = self._capture_durable_runtime_state(runtime)
          self._update_broker_report_health(runtime, "trade", data)
          # 持久化成交记录（不再支持，如有独立成交表可在此处保存）
          # 但交易信号是独立表，如果这里能关联到信号，可以更新信号状态

          if runtime.state_manager:
            runtime.state_manager.apply_trade(data)
            metadata = trade_metadata
            trade_status = "FILLED"
            try:
              order = await runtime.broker.get_order(getattr(data, "order_id", ""))
              if order and order.status == OrderStatus.PARTIAL_FILLED:
                trade_status = "PARTIAL_FILLED"
            except Exception:
              trade_status = "FILLED"
            if not durable_event:
              await runtime.state_manager.update_trade_intent_status(
                metadata.get("intent_id"),
                trade_status,
                order_id=getattr(data, "order_id", None),
                executed_price=float(getattr(data, "price", 0.0) or 0.0),
                executed_volume=int(getattr(data, "volume", 0) or 0),
                executed_time=getattr(data, "trade_time", None),
                accumulate_executed_volume=True,
              )

          if durable_event and runtime.strategy is not None:
            with runtime.strategy.state.silent(
              persist=False,
              notify=False,
              flush_on_exit=False,
            ):
              durable_strategy_patch = await self._notify_strategy_trade(
                runtime,
                TradeExecutionEvent.from_raw(data),
                raise_on_error=True,
              )
          else:
            await self._notify_strategy_trade(
              runtime,
              TradeExecutionEvent.from_raw(data),
              raise_on_error=paper_candidate_trade,
            )
          if runtime.performance_recorder and not durable_event:
            await runtime.performance_recorder.record(runtime, "trade", data)
          if paper_candidate_trade:
            enqueue_paper_fill = getattr(
              runtime.state_manager,
              "enqueue_t_trade_paper_fill_fact",
              None,
            )
            if not callable(enqueue_paper_fill):
              raise RuntimeError("V3 做 T PAPER 运行缺少成交 durable outbox")
            enqueue_paper_fill(paper_fill_fact)

        elif event_type == "universe":
          future = data.get("future")
          try:
            configuration_changed = bool(data.get("configuration_changed"))
            if configuration_changed:
              async with runtime.approval_lock:
                await self._expire_v3_t_trade_candidates_for_config_change(runtime)
                result = await self._apply_realtime_instrument_reconcile(
                  runtime,
                  list(data.get("instruments") or []),
                  instrument_metadata=dict(data.get("instrument_metadata") or {}),
                  parameters=data.get("parameters"),
                  configuration_changed=True,
                )
            elif runtime.context.mode == StrategyRunMode.BACKTEST:
              result = await self._apply_backtest_instrument_reconcile(
                runtime,
                list(data.get("instruments") or []),
                instrument_metadata=dict(data.get("instrument_metadata") or {}),
              )
            else:
              result = await self._apply_realtime_instrument_reconcile(
                runtime,
                list(data.get("instruments") or []),
                instrument_metadata=dict(data.get("instrument_metadata") or {}),
                parameters=data.get("parameters"),
              )
            if future and not future.done():
              future.set_result(result)
          except Exception as exc:
            if future and not future.done():
              future.set_exception(exc)
            raise

        if market_processing_context is not None:
          code, generation, _queued_at = market_processing_context
          if (
            runtime._market_continuity_generations.get(code, 0) == generation
            and code not in runtime._pending_market_invalidations
            and code not in runtime._market_fail_closed_codes
          ):
            runtime._active_market_continuity_losses.pop(code, None)

        if durable_event:
          custom_updates = (
            runtime.strategy.state.to_dict() if runtime.strategy is not None else {}
          )
          custom_updates[EXIT_PLAN_BOOK_STATE_KEY] = runtime.exit_plan_book.to_dict()
          checkpointed = await runtime.state_manager.checkpoint_durable_runtime_event(
            durable_event_key,
            custom_updates=custom_updates,
            strategy_updates=(
              dict(getattr(durable_strategy_patch, "set", {}) or {})
              if durable_strategy_patch is not None
              else None
            ),
            strategy_unsets=(
              list(getattr(durable_strategy_patch, "unset", []) or [])
              if durable_strategy_patch is not None
              else None
            ),
          )
          if not checkpointed:
            raise RuntimeError(f"持久化运行时事件原子快照失败: {durable_event_key}")
          if (
            runtime.durable_event_barrier_key == durable_event_key
            and not runtime.durable_startup_barrier
          ):
            runtime.durable_event_barrier_key = None
        if event_type == "trade" and (
          durable_event
          or runtime.context.mode
          in {StrategyRunMode.BACKTEST, StrategyRunMode.PAPER}
        ):
          trade_metadata = dict(getattr(data, "metadata", {}) or {})
          if (
            runtime.context.mode == StrategyRunMode.PAPER
            and not durable_event
            and trade_metadata.get("candidate_id")
          ):
            if paper_fill_fact is None:
              raise RuntimeError("做 T PAPER 候选成交未生成 durable fact")
            checkpointed = bool(
              runtime.state_manager
              and await runtime.state_manager.checkpoint_strategy_state_changes()
            )
            if not checkpointed:
              message = (
                "做 T PAPER 候选成交状态与 outbox 未完成权威检查点: "
                f"run_id={runtime.run_id} trade_id={getattr(data, 'trade_id', None)}"
              )
              raise RuntimeError(message)
            await self._replay_pending_t_trade_paper_fill_facts(runtime)
          elif (
            runtime.context.mode == StrategyRunMode.BACKTEST
            and not durable_event
            and trade_metadata.get("candidate_id")
          ):
            checkpointed = bool(
              runtime.state_manager
              and await runtime.state_manager.checkpoint_strategy_state_changes()
            )
            if not checkpointed:
              message = (
                "做 T 回放候选成交状态未完成权威检查点，跳过结果归集: "
                f"run_id={runtime.run_id} trade_id={getattr(data, 'trade_id', None)}"
              )
              if self._requires_replay_event_integrity(runtime):
                runtime.status = ExecutionStatus.ERROR
                runtime.error_message = (
                  "T_TRADE_CANDIDATE_OUTCOME_FILL_CHECKPOINT_FAILED"
                )
                raise RuntimeError(message)
              self.logger.error(message)
            else:
              await self._record_t_trade_candidate_fill(
                runtime,
                data,
                durable_event=False,
              )
          else:
            await self._record_t_trade_candidate_fill(
              runtime,
              data,
              durable_event=durable_event,
            )
        if completion is not None and not completion.done():
          completion.set_result(True)

      except Exception as e:
        has_applied_runtime_event = getattr(
          runtime.state_manager,
          "has_applied_runtime_event",
          None,
        )
        marker_installed = bool(
          durable_event_key
          and durable_rollback_snapshot is not None
          and callable(has_applied_runtime_event)
          and has_applied_runtime_event(durable_event_key)
        )
        cas_conflict = bool(
          str(
            getattr(runtime.state_manager, "last_snapshot_failure_code", "") or ""
          )
          == "CAS_CONFLICT"
        )
        if (
          durable_rollback_snapshot is not None
          and runtime.state_manager is not None
          and not marker_installed
          and not (paper_candidate_trade and cas_conflict)
        ):
          self._restore_durable_runtime_state(
            runtime,
            durable_rollback_snapshot,
          )
        if durable_event_key and (
          runtime.durable_event_barrier_key in {None, durable_event_key}
        ):
          runtime.durable_event_barrier_key = durable_event_key
        if completion is not None and not completion.done():
          completion.set_exception(e)
        if paper_candidate_trade:
          runtime.status = ExecutionStatus.ERROR
          runtime.error_message = (
            "T_TRADE_RUNTIME_STATE_CAS_CONFLICT"
            if cas_conflict
            else "T_TRADE_PAPER_FILL_DURABILITY_FAILED"
          )
          self._drain_runtime_control_queue_after_fail_stop(
            runtime,
            reason=(
              "做 T PAPER 候选成交未完成持久化收敛，运行已停止: "
              f"{runtime.run_id}"
            ),
          )
        self.logger.error(f"处理事件失败: {e}")
        if runtime.metrics:
          runtime.metrics.error_count += 1
      finally:
        if market_processing_context is not None:
          code, generation, queued_at = market_processing_context
          if runtime._processing_market_events.get(code) == (
            generation,
            queued_at,
          ):
            runtime._processing_market_events.pop(code, None)
        if acquired_queue is not None:
          acquired_queue.task_done()
        try:
          await self._maybe_coordinate_session_checkpoints(runtime)
        except Exception:
          self.logger.exception(
            "策略会话检查点协调器异常: run_id=%s",
            runtime.run_id,
          )

  async def apply_durable_order_report(
    self,
    run_id: str,
    order: Any,
  ) -> None:
    """Apply a persisted order report on the runtime's serial event queue."""
    runtime = self.require_durable_event_consumer(run_id)
    future = asyncio.get_running_loop().create_future()
    await self._put_runtime_control_event(
      runtime,
      ("durable_order", (order, future)),
    )
    await self._await_durable_event_completion(runtime, future)

  def require_durable_event_consumer(self, run_id: str) -> StrategyRuntime:
    """Return a running durable consumer or raise a deferrable exception."""
    runtime = self.runs.get(run_id)
    if runtime is None:
      raise RuntimeConsumerUnavailable(f"策略运行尚未恢复: {run_id}")
    if runtime.status != ExecutionStatus.RUNNING:
      raise RuntimeConsumerUnavailable(
        f"策略运行当前不消费持久化回报: {run_id} ({runtime.status.value})"
      )
    if self._shutdown_event.is_set():
      raise RuntimeConsumerUnavailable("策略执行器正在关闭，暂缓持久化回报")
    if runtime.state_manager is None:
      raise RuntimeConsumerUnavailable(f"策略运行缺少状态管理器: {run_id}")
    if runtime.event_task is None or runtime.event_task.done():
      raise RuntimeConsumerUnavailable(f"策略运行事件消费者未运行: {run_id}")
    return runtime

  async def _await_durable_event_completion(
    self,
    runtime: StrategyRuntime,
    future: asyncio.Future,
  ) -> None:
    try:
      await asyncio.wait_for(
        asyncio.shield(future),
        timeout=_DURABLE_EVENT_APPLY_TIMEOUT_SECONDS,
      )
    except asyncio.TimeoutError as exc:
      future.cancel()
      raise RuntimeConsumerUnavailable(
        f"策略运行事件消费者未在时限内确认持久化回报: {runtime.run_id}"
      ) from exc

  def arm_durable_event_barrier(
    self,
    run_id: str,
    event_key: str,
  ) -> None:
    """Fail closed from durable staging until the whole DB backlog is APPLIED."""
    runtime = self.runs.get(run_id)
    if runtime is None or not event_key:
      return
    if runtime.durable_event_barrier_key is None:
      runtime.durable_event_barrier_key = event_key
    # A different key is an already armed earlier backlog item. Its APPLIED
    # transition will query and advance to this event in authoritative order.
    runtime.durable_startup_barrier = True

  async def refresh_durable_event_barrier(self, run_id: str) -> Optional[str]:
    """Reconcile a runtime barrier to the authoritative unapplied DB backlog."""
    runtime = self.runs.get(run_id)
    if runtime is None or runtime.state_manager is None:
      return None
    next_key = await runtime.state_manager.get_earliest_unapplied_runtime_event_key()
    runtime.durable_event_barrier_key = next_key
    runtime.durable_startup_barrier = bool(next_key)
    return next_key

  async def refresh_armed_durable_event_barriers(self) -> None:
    """Reconcile fail-closed barriers after transient staging/database errors."""
    for run_id, runtime in list(self.runs.items()):
      if runtime.durable_event_barrier_key and runtime.state_manager is not None:
        await self.refresh_durable_event_barrier(run_id)

  async def advance_durable_event_barrier(
    self,
    run_id: str,
    applied_event_key: str,
  ) -> None:
    """Advance a durable backlog barrier only after its DB event is APPLIED."""
    runtime = self.runs.get(run_id)
    if (
      runtime is None
      or not runtime.durable_startup_barrier
      or runtime.durable_event_barrier_key != applied_event_key
      or runtime.state_manager is None
    ):
      return
    await self.refresh_durable_event_barrier(run_id)

  async def apply_durable_trade_report(
    self,
    run_id: str,
    trade: Any,
  ) -> None:
    """Apply a persisted execution report on the runtime's serial event queue."""
    runtime = self.require_durable_event_consumer(run_id)
    future = asyncio.get_running_loop().create_future()
    await self._put_runtime_control_event(
      runtime,
      ("durable_trade", (trade, future)),
    )
    await self._await_durable_event_completion(runtime, future)

  async def _process_tick(self, runtime: StrategyRuntime, tick) -> None:
    """处理Tick数据"""
    strategy = runtime.strategy
    broker = runtime.broker
    metrics = runtime.metrics

    try:
      await self._coordinate_backtest_virtual_day_before_event(
        runtime,
        getattr(tick, "time", None),
      )
      if tick.stock_code not in set(runtime.context.instruments or []):
        self.logger.debug("忽略已移出标的池的迟到 Tick: %s", tick.stock_code)
        return
      # 更新策略上下文时间
      if runtime.context.mode == StrategyRunMode.BACKTEST:
        self._advance_runtime_replay_clock(runtime, tick.time)
      else:
        runtime.context.current_time = tick.time
      if isinstance(runtime.data_adapter, HistoricalDataAdapter):
        runtime.data_adapter.current_time = tick.time
      market_snapshot = MarketDataSnapshot.from_tick(
        tick,
        limit_rate=self._backtest_limit_rate(
          runtime,
          instrument_code=tick.stock_code,
          timestamp=tick.time,
        ),
      )
      runtime.latest_market_data[tick.stock_code] = market_snapshot
      self._record_t_trade_replay_price_limit_source(runtime, market_snapshot)
      if runtime.state_manager:
        runtime.state_manager.settle_trading_day(tick.time.date())
      await self._expire_pending_approvals(runtime)
      await self._cancel_expired_strategy_orders(runtime, tick.time)

      # 更新回测 Broker 的市场数据
      if isinstance(broker, BacktestBroker):
        await broker.update_market_data(
          tick.stock_code,
          tick.last_price,
          tick.time,
          market_data=market_snapshot,
        )
        await self._board_replay_report_barrier(runtime)

      # 广播 Tick 数据到订阅者
      runtime.broadcast_tick(tick)

      await self._process_auto_exit_plans(
        runtime,
        instrument_code=tick.stock_code,
        timestamp=tick.time,
        market_data=market_snapshot,
      )
      await self._board_replay_report_barrier(runtime)

      await self._ensure_t_trade_opportunity_profile(
        runtime,
        instrument_code=tick.stock_code,
        evaluated_at=tick.time,
      )
      strategy_input = self._build_strategy_input(
        runtime,
        cadence=StrategyCadence.TICK,
        instrument_code=tick.stock_code,
        timestamp=tick.time,
        market_data=market_snapshot,
        event=tick,
      )
      output = await strategy.step(strategy_input)
      await self._process_strategy_output(runtime, output, strategy_input)
      self._observe_t_trade_phase_one_baseline(runtime, strategy_input)
      await self._observe_t_trade_candidate_outcomes(
        runtime,
        input_snapshot=strategy_input,
        market_data=market_snapshot,
      )
      await self._board_replay_report_barrier(runtime)
      if runtime.performance_recorder:
        await runtime.performance_recorder.record(runtime, "tick", tick)
      await self._report_t_trade_replay_progress(runtime)
      if runtime.context.mode == StrategyRunMode.BACKTEST:
        self._record_backtest_market_watermark(
          runtime,
          tick,
          instrument_code=tick.stock_code,
        )

    except Exception as e:
      if metrics:
        metrics.error_count += 1
      self.logger.error(f"处理Tick数据失败: {e}")
      if self._requires_replay_event_integrity(runtime):
        raise

  async def _process_entry_plan_evaluate(
    self,
    runtime: StrategyRuntime,
    payload: Any,
  ) -> None:
    """Evaluate an EntryPlan action on the runtime's serial queue."""

    if runtime.strategy is None or not isinstance(payload, dict):
      raise ValueError("人工建仓触发缺少运行策略或结构化事件")
    instrument_code = str(payload.get("instrument_code") or "").upper()
    if instrument_code not in set(runtime.context.instruments or []):
      raise ValueError("人工建仓触发标的与固定策略运行不匹配")
    market_data = payload.get("market_data")
    if not isinstance(market_data, MarketDataSnapshot):
      raise ValueError("人工建仓触发缺少最新权威行情快照")
    timestamp = market_data.timestamp or runtime.context.current_time
    if not isinstance(timestamp, datetime):
      raise ValueError("人工建仓触发行情时间无效")
    strategy_input = self._build_strategy_input(
      runtime,
      cadence=StrategyCadence.RECONCILE,
      instrument_code=instrument_code,
      timestamp=timestamp,
      market_data=market_data,
      event=payload,
    )
    output = await runtime.strategy.step(strategy_input)
    await self._process_strategy_output(runtime, output, strategy_input)

  async def _process_kline(self, runtime: StrategyRuntime, kline: KLine) -> None:
    """处理K线数据"""
    strategy = runtime.strategy
    broker = runtime.broker
    metrics = runtime.metrics

    try:
      await self._coordinate_backtest_virtual_day_before_event(
        runtime,
        getattr(kline, "time", None),
      )
      if kline.stock_code not in set(runtime.context.instruments or []):
        self.logger.debug("忽略已移出标的池的迟到 K 线: %s", kline.stock_code)
        return
      # 更新策略上下文时间
      if runtime.context.mode == StrategyRunMode.BACKTEST:
        self._advance_runtime_replay_clock(runtime, kline.time)
      else:
        runtime.context.current_time = kline.time
      if isinstance(runtime.data_adapter, HistoricalDataAdapter):
        runtime.data_adapter.current_time = kline.time
      market_snapshot = MarketDataSnapshot.from_kline(
        kline,
        limit_rate=self._backtest_limit_rate(
          runtime,
          instrument_code=kline.stock_code,
          timestamp=kline.time,
        ),
      )
      runtime.latest_market_data[kline.stock_code] = market_snapshot
      self._record_t_trade_replay_price_limit_source(runtime, market_snapshot)
      if runtime.state_manager:
        runtime.state_manager.settle_trading_day(kline.time.date())
      await self._expire_pending_approvals(runtime)
      await self._cancel_expired_strategy_orders(runtime, kline.time)

      # 更新回测 Broker 的市场数据
      if isinstance(broker, BacktestBroker):
        await broker.update_market_data(
          kline.stock_code,
          kline.close,
          kline.time,
          market_data=market_snapshot,
        )
        await self._board_replay_report_barrier(runtime)

      # 广播 K线 数据到订阅者
      runtime.broadcast_kline(kline)

      await self._process_auto_exit_plans(
        runtime,
        instrument_code=kline.stock_code,
        timestamp=kline.time,
        market_data=market_snapshot,
      )
      await self._board_replay_report_barrier(runtime)

      strategy_input = self._build_strategy_input(
        runtime,
        cadence=StrategyCadence.BAR,
        instrument_code=kline.stock_code,
        timestamp=kline.time,
        market_data=market_snapshot,
        event=kline,
      )
      output = await strategy.step(strategy_input)
      await self._process_strategy_output(runtime, output, strategy_input)
      await self._board_replay_report_barrier(runtime)
      if runtime.performance_recorder:
        await runtime.performance_recorder.record(runtime, "bar", kline)
      await self._report_t_trade_replay_progress(runtime)
      if runtime.context.mode == StrategyRunMode.BACKTEST:
        self._record_backtest_market_watermark(
          runtime,
          kline,
          instrument_code=kline.stock_code,
        )

    except Exception as e:
      if metrics:
        metrics.error_count += 1
      self.logger.error(f"处理K线数据失败: {e}")
      if self._requires_replay_event_integrity(runtime):
        raise

  async def _report_t_trade_replay_progress(
    self,
    runtime: StrategyRuntime,
    *,
    processed_until: Optional[datetime] = None,
    force: bool = False,
  ) -> None:
    """Persist replay progress only at explicit virtual-day boundaries."""

    is_backtest = runtime.context.mode == StrategyRunMode.BACKTEST
    if is_backtest and not force:
      # Tick/Kline calls retain their in-memory replay progress but must not
      # create a projection write. The owning day runner emits the one force
      # call after every virtual trading day instead.
      return
    parameters = dict(runtime.context.parameters or {})
    current_time = processed_until or runtime.context.current_time
    start_time = runtime.context.backtest_start_time
    end_time = runtime.context.backtest_end_time
    if (
      not (
        parameters.get("t_trade_replay")
        or parameters.get("exit_plan_replay")
      )
      or current_time is None
      or start_time is None
      or end_time is None
      or end_time <= start_time
    ):
      return
    current_time = (
      time_utils.to_shanghai(current_time) if current_time.tzinfo else current_time
    )
    start_time = time_utils.to_shanghai(start_time) if start_time.tzinfo else start_time
    end_time = time_utils.to_shanghai(end_time) if end_time.tzinfo else end_time
    projection_trade_date = current_time.date().isoformat()
    now = 0.0
    if is_backtest:
      if runtime._last_t_trade_replay_projection_trade_date == projection_trade_date:
        return
    else:
      now = monotonic()
      if not force and now - runtime._last_replay_projection_at < 1.0:
        return
    progress_pct = max(
      0.0,
      min(
        99.9,
        (current_time - start_time).total_seconds()
        / (end_time - start_time).total_seconds()
        * 100.0,
      ),
    )
    if not force and progress_pct <= runtime._last_replay_progress_pct:
      return
    account_id = str(parameters.get("account_id") or "").strip()
    if not account_id:
      return
    try:
      if parameters.get("exit_plan_replay"):
        await exit_plan_replay_projection_service.update(
          run_id=runtime.run_id,
          account_id=account_id,
          status="RUNNING",
          progress_pct=progress_pct,
          processed_until=current_time,
          kind=ExitPlanReplayUpdateKind.PROGRESS,
        )
      else:
        await t_trade_replay_projection_service.update(
          run_id=runtime.run_id,
          account_id=account_id,
          status="RUNNING",
          progress_pct=progress_pct,
          processed_until=current_time,
          kind=TTradeReplayUpdateKind.PROGRESS,
        )
      runtime._last_replay_progress_pct = max(
        runtime._last_replay_progress_pct,
        progress_pct,
      )
      if is_backtest:
        runtime._last_t_trade_replay_projection_trade_date = projection_trade_date
      else:
        runtime._last_replay_projection_at = now
    except Exception:
      self.logger.exception("更新历史回放进度投影失败: %s", runtime.run_id)

  def _build_execution_context_snapshot(
    self,
    runtime: StrategyRuntime,
    *,
    instrument_code: str,
    market_data: Optional[MarketDataSnapshot] = None,
    event: Any = None,
    account: Optional[Dict[str, Any]] = None,
    positions: Optional[Dict[str, Any]] = None,
    bucket_ledger: Optional[Dict[str, Any]] = None,
  ) -> ExecutionContextSnapshot:
    if account is None or positions is None or bucket_ledger is None:
      state_account: Dict[str, Any] = {}
      state_positions: Dict[str, Any] = {}
      state_bucket_ledger: Dict[str, Any] = {}
      if runtime.state_manager:
        state_account = runtime.state_manager.get_account_quota()
        state_positions = runtime.state_manager.get_all_positions()
        state_bucket_ledger = runtime.state_manager.get_bucket_ledger_snapshot()
      account = state_account if account is None else account
      positions = state_positions if positions is None else positions
      bucket_ledger = state_bucket_ledger if bucket_ledger is None else bucket_ledger

    account = dict(account or {})
    positions = dict(positions or {})
    bucket_ledger = dict(bucket_ledger or {})
    runtime_state = runtime.strategy.state.to_dict() if runtime.strategy else {}
    parameters = dict(runtime.context.parameters or {})
    open_orders = self._build_open_order_snapshots(runtime)
    order_state = self._build_order_state(open_orders)
    broker_report = self._build_broker_report(runtime)
    market_context = self._build_market_context(runtime, market_data, event)
    risk_caps = self._build_risk_caps(
      runtime,
      account,
      positions,
      instrument_code,
      market_context=market_context,
      order_state=order_state,
      broker_report=broker_report,
      runtime_state=runtime_state,
      parameters=parameters,
    )
    managed_entry = dict(parameters.get("managed_entry_plan") or {})
    pacing = dict(managed_entry.get("pacing_policy") or {})
    try:
      plan_cash_buffer = float(pacing.get("cash_buffer_pct", 0.0) or 0.0)
    except (TypeError, ValueError):
      plan_cash_buffer = -1.0
    if plan_cash_buffer == plan_cash_buffer and 0 <= plan_cash_buffer < 1:
      try:
        existing_cash_buffer = float(risk_caps.get("min_cash_buffer_pct", 0.0) or 0.0)
      except (TypeError, ValueError):
        existing_cash_buffer = 0.0
      risk_caps["min_cash_buffer_pct"] = max(
        0.0,
        existing_cash_buffer,
        plan_cash_buffer,
      )
    portfolio_state = {"account": account, "positions": positions}
    position_profile = self._build_position_profile(
      runtime,
      portfolio_state=portfolio_state,
      market_context=market_context,
      risk_caps=risk_caps,
      bucket_ledger=bucket_ledger,
      instrument_code=instrument_code,
      runtime_state=runtime_state,
      parameters=parameters,
    )
    return ExecutionContextSnapshot(
      account=account,
      positions=positions,
      bucket_ledger=bucket_ledger,
      portfolio_state=portfolio_state,
      open_orders=open_orders,
      market_context=market_context,
      risk_caps=risk_caps,
      position_profile=position_profile,
      runtime_state=runtime_state,
      parameters=parameters,
    )

  async def _ensure_t_trade_opportunity_profile(
    self,
    runtime: StrategyRuntime,
    *,
    instrument_code: str,
    evaluated_at: datetime,
  ) -> None:
    """Load one strictly prior profile for the first source Tick of a day."""

    if not self._uses_t_trade_opportunity_runtime(runtime):
      return
    code = str(instrument_code or "").strip().upper()
    source_evaluated_at = time_utils.to_shanghai(evaluated_at)
    trade_date = source_evaluated_at.date().isoformat()
    cache_key = (code, trade_date)
    is_backtest = runtime.context.mode == StrategyRunMode.BACKTEST
    retry_policy = (
      "本交易日固定失败关闭，不重试"
      if is_backtest
      else "短暂保守门禁并定时重试"
    )
    if cache_key in runtime._t_trade_opportunity_profiles:
      cached = runtime._t_trade_opportunity_profiles[cache_key]
      if is_backtest or cached is not None:
        return
      retry_after = runtime._t_trade_opportunity_profile_retry_after.get(
        cache_key,
        0.0,
      )
      if monotonic() < retry_after:
        return

    required_version = (
      str(runtime.context.parameters.get("t_trade_profile_version") or "").strip()
      or None
    )
    try:
      profile_result = await self._d1_profile_reader.execute(
        D1ProfileReadRequest(
          instrument_code=code,
          # The application contract compares the profile's trade date against
          # this normalized Shanghai evaluation date.  Passing the normalized
          # value avoids an aware-UTC midnight crossing changing the D-1 set.
          evaluated_at=source_evaluated_at,
          required_version=required_version,
        )
      )
      profile = (
        profile_result.profile.to_dict()
        if profile_result.available and profile_result.profile is not None
        else None
      )
      if profile is not None and profile_result.profile_fingerprint:
        profile["profile_fingerprint"] = profile_result.profile_fingerprint
      runtime._t_trade_opportunity_profiles[cache_key] = (
        copy.deepcopy(profile) if profile is not None else None
      )
      if profile is None:
        if is_backtest:
          runtime._t_trade_opportunity_profile_retry_after.pop(cache_key, None)
        else:
          runtime._t_trade_opportunity_profile_retry_after[cache_key] = (
            monotonic() + _T_TRADE_PROFILE_LOOKUP_RETRY_SECONDS
          )
      else:
        runtime._t_trade_opportunity_profile_retry_after.pop(cache_key, None)
      if profile_result.reason is D1ProfileReadReason.READ_FAILED:
        runtime._t_trade_opportunity_profile_errors[cache_key] = (
          "PROFILE_LOOKUP_FAILED"
        )
        self._runtime_log(
          runtime,
          "ERROR",
          f"做 T 标的画像读取失败，{retry_policy}: "
          f"instrument={code} trade_date={trade_date} "
          f"error={profile_result.error_type or profile_result.reason.value}",
        )
      else:
        # NOT_FOUND and the adapter's invalid/non-causal/version-filtered
        # results historically became an unavailable profile, not a storage
        # failure.  Keep the existing audit and retry classification intact.
        runtime._t_trade_opportunity_profile_errors.pop(cache_key, None)
    except Exception as exc:
      # Storage unavailability is not permission to reuse yesterday's cached
      # value. The strategy receives None and therefore remains INSUFFICIENT.
      runtime._t_trade_opportunity_profiles[cache_key] = None
      if is_backtest:
        runtime._t_trade_opportunity_profile_retry_after.pop(cache_key, None)
      else:
        runtime._t_trade_opportunity_profile_retry_after[cache_key] = (
          monotonic() + _T_TRADE_PROFILE_LOOKUP_RETRY_SECONDS
        )
      runtime._t_trade_opportunity_profile_errors[cache_key] = "PROFILE_LOOKUP_FAILED"
      self._runtime_log(
        runtime,
        "ERROR",
        f"做 T 标的画像读取失败，{retry_policy}: "
        f"instrument={code} trade_date={trade_date} error={exc}",
      )

    # A long-running account-level runtime only needs the active trade day's
    # point-in-time image. Pruning prevents process-local growth without
    # changing replay decisions.
    for key in list(runtime._t_trade_opportunity_profiles):
      if key[0] == code and key != cache_key:
        runtime._t_trade_opportunity_profiles.pop(key, None)
        runtime._t_trade_opportunity_profile_errors.pop(key, None)
        runtime._t_trade_opportunity_profile_retry_after.pop(key, None)
    self._prune_t_trade_opportunity_profile_cache(
      runtime,
      keep_key=cache_key,
    )

  @staticmethod
  def _prune_t_trade_opportunity_profile_cache(
    runtime: StrategyRuntime,
    *,
    removed_instruments: Iterable[str] = (),
    keep_key: Optional[tuple[str, str]] = None,
  ) -> None:
    """Bound all profile-cache partitions and evict departed instruments."""

    removed = {
      str(instrument_code or "").strip().upper()
      for instrument_code in removed_instruments
      if str(instrument_code or "").strip()
    }
    profiles = runtime._t_trade_opportunity_profiles
    errors = runtime._t_trade_opportunity_profile_errors
    retry_after = runtime._t_trade_opportunity_profile_retry_after

    for key in set(profiles) | set(errors) | set(retry_after):
      if key[0] in removed:
        profiles.pop(key, None)
        errors.pop(key, None)
        retry_after.pop(key, None)

    limit = max(0, int(_T_TRADE_PROFILE_CACHE_MAX_ENTRIES))
    while len(profiles) > limit:
      eviction_key = next(
        (key for key in profiles if key != keep_key),
        next(iter(profiles)),
      )
      profiles.pop(eviction_key, None)
      errors.pop(eviction_key, None)
      retry_after.pop(eviction_key, None)

    # Error and retry maps may outlive a partially restored or interrupted
    # lookup.  They are never meaningful without the matching profile slot.
    for key in set(errors) | set(retry_after):
      if key not in profiles:
        errors.pop(key, None)
        retry_after.pop(key, None)

  @staticmethod
  def _uses_t_trade_opportunity_runtime(runtime: StrategyRuntime) -> bool:
    strategy = runtime.strategy
    strategy_class = getattr(runtime, "strategy_class", None)
    return bool(
      getattr(strategy, "USES_T_TRADE_OPPORTUNITY_PROFILE", False)
      or getattr(strategy_class, "USES_T_TRADE_OPPORTUNITY_PROFILE", False)
      or runtime.context.parameters.get("t_trade_opportunity_v3")
    )

  @staticmethod
  def _t_trade_runtime_scope(runtime: StrategyRuntime) -> tuple[str, str, str]:
    """Return the only scope that may authorize a strategy Tick.

    ``StrategyRuntime.run_id`` is the Engine owner identity.  The context is
    checked as a second independent value because a restored or hand-built
    context must not silently borrow another run's eligibility snapshot.
    """

    runtime_run_id = str(runtime.run_id or "").strip()
    context_run_id = str(getattr(runtime.context, "run_id", "") or "").strip()
    account_id = str(
      dict(runtime.context.parameters or {}).get("account_id") or ""
    ).strip()
    return runtime_run_id, context_run_id, account_id

  @staticmethod
  def _t_trade_metadata_scope(
    metadata: Mapping[str, Any],
  ) -> tuple[str, str]:
    """Read optional producer scope without requiring it from old metadata."""

    nested_scope = metadata.get("scope")
    scope = nested_scope if isinstance(nested_scope, Mapping) else metadata
    account_id = str(scope.get("account_id") or "").strip()
    run_id = str(
      scope.get("run_id") or scope.get("strategy_run_id") or ""
    ).strip()
    return account_id, run_id

  @staticmethod
  def _t_trade_unique_blockers(values: Any) -> list[str]:
    blockers: list[str] = []
    raw_values = values if isinstance(values, (list, tuple, set)) else [values]
    for value in raw_values:
      normalized = str(value or "").strip()
      if normalized and normalized not in blockers:
        blockers.append(normalized)
    return blockers

  def _build_t_trade_intent_emission_snapshot(
    self,
    runtime: StrategyRuntime,
    instruments: List[str],
    instrument_metadata: Optional[Mapping[str, Any]],
  ) -> Dict[str, Dict[str, Any]]:
    """Build a complete, bounded eligibility snapshot before publishing it.

    The returned mapping is deliberately independent from ``context`` and
    strategy state.  Missing metadata is represented as a blocked entry so a
    successful universe reconcile still yields a deterministic strategy
    context; entries not in the desired universe are absent altogether.
    """

    desired: list[str] = []
    for raw_code in instruments or []:
      code = str(raw_code or "").strip().upper()
      if code and code not in desired:
        desired.append(code)
    if len(desired) > _T_TRADE_INTENT_EMISSION_MAX_INSTRUMENTS:
      raise ValueError(
        "做 T 意图发射资格快照超过有界标的上限: "
        f"{len(desired)} > {_T_TRADE_INTENT_EMISSION_MAX_INSTRUMENTS}"
      )

    if instrument_metadata is not None and not isinstance(
      instrument_metadata, Mapping
    ):
      raise ValueError("做 T 意图发射元数据必须是映射")
    if (
      instrument_metadata is not None
      and len(instrument_metadata) > _T_TRADE_INTENT_EMISSION_MAX_INSTRUMENTS
    ):
      raise ValueError(
        "做 T 意图发射元数据超过有界标的上限: "
        f"{len(instrument_metadata)} > {_T_TRADE_INTENT_EMISSION_MAX_INSTRUMENTS}"
      )

    metadata_by_code: Dict[str, Mapping[str, Any]] = {}
    for raw_code, raw_value in (instrument_metadata or {}).items():
      code = str(raw_code or "").strip().upper()
      if not code or not isinstance(raw_value, Mapping):
        continue
      metadata_by_code[code] = raw_value

    runtime_run_id, context_run_id, account_id = self._t_trade_runtime_scope(runtime)
    scope_blockers: list[str] = []
    if not runtime_run_id or not context_run_id:
      scope_blockers.extend(
        [
          "T_TRADE_INTENT_EMISSION_SCOPE_UNAVAILABLE",
          "T_TRADE_INTENT_EMISSION_RUN_SCOPE_UNAVAILABLE",
        ]
      )
    elif runtime_run_id != context_run_id:
      scope_blockers.extend(
        [
          "T_TRADE_INTENT_EMISSION_SCOPE_MISMATCH",
          "T_TRADE_INTENT_EMISSION_RUN_SCOPE_MISMATCH",
        ]
      )
    if not account_id:
      scope_blockers.extend(
        [
          "T_TRADE_INTENT_EMISSION_SCOPE_UNAVAILABLE",
          "T_TRADE_INTENT_EMISSION_ACCOUNT_SCOPE_UNAVAILABLE",
        ]
      )
    scope_blockers = self._t_trade_unique_blockers(scope_blockers)

    snapshot: Dict[str, Dict[str, Any]] = {}
    for code in desired:
      item = metadata_by_code.get(code)
      blockers = list(scope_blockers)
      eligible = False
      draining = False
      metadata_account_id = ""
      metadata_run_id = ""
      if item is None:
        blockers.append("UNIVERSE_ELIGIBILITY_UNAVAILABLE")
      else:
        metadata_account_id, metadata_run_id = self._t_trade_metadata_scope(item)
        if metadata_account_id and metadata_account_id != account_id:
          blockers.extend(
            [
              "T_TRADE_INTENT_EMISSION_SCOPE_MISMATCH",
              "T_TRADE_INTENT_EMISSION_ACCOUNT_SCOPE_MISMATCH",
            ]
          )
        if metadata_run_id and metadata_run_id != runtime_run_id:
          blockers.extend(
            [
              "T_TRADE_INTENT_EMISSION_SCOPE_MISMATCH",
              "T_TRADE_INTENT_EMISSION_RUN_SCOPE_MISMATCH",
            ]
          )
        eligible = item.get("eligible") is True
        draining = item.get("draining") is True
        blockers.extend(self._t_trade_unique_blockers(item.get("blockers")))
        if draining:
          blockers.append("INSTRUMENT_DRAINING")
        if not eligible and not blockers:
          blockers.append(str(item.get("reason") or "POSITION_NOT_ELIGIBLE"))

      blockers = self._t_trade_unique_blockers(blockers)
      snapshot[code] = {
        "instrument_code": code,
        "run_id": runtime_run_id,
        "account_id": account_id,
        "eligible": eligible,
        "draining": draining,
        # Dynamic account facts are deliberately absent here.  They are read
        # afresh for every Tick so a point-in-time universe snapshot cannot
        # accidentally authorize a later account state.
        "allowed": bool(eligible and not blockers),
        "blockers": blockers,
      }
    return snapshot

  @staticmethod
  def _publish_t_trade_intent_emission_snapshot(
    runtime: StrategyRuntime,
    snapshot: Dict[str, Dict[str, Any]],
  ) -> None:
    """Publish one fully built map; assignment is the atomic visibility point."""

    runtime.t_trade_intent_emission_by_instrument = {
      str(code): {
        **dict(value),
        "blockers": list(value.get("blockers") or []),
      }
      for code, value in snapshot.items()
    }

  @staticmethod
  def _clear_t_trade_intent_emission_snapshot(runtime: StrategyRuntime) -> None:
    """Drop all prior authorization after any failed reconcile boundary."""

    runtime.t_trade_intent_emission_by_instrument = {}

  def _t_trade_intent_emission_context(
    self,
    runtime: StrategyRuntime,
    instrument_code: str,
    *,
    requested_amount: Any = None,
    current_intent_id: Optional[str] = None,
    check_coordination_lock: bool = True,
  ) -> Dict[str, Any]:
    """Return a scope-checked strategy context, always fail-closed."""

    code = str(instrument_code or "").strip().upper()
    entry = runtime.t_trade_intent_emission_by_instrument.get(code)
    runtime_run_id, context_run_id, account_id = self._t_trade_runtime_scope(runtime)
    if entry is None:
      entry = None
    elif not isinstance(entry, Mapping):
      return {
        "allowed": False,
        "blockers": ["T_TRADE_INTENT_EMISSION_CONTEXT_INVALID"],
      }

    parameters = dict(runtime.context.parameters or {})
    if requested_amount is None:
      requested_amount = parameters.get(
        "target_trade_amount",
        parameters.get("target_amount"),
      )
    facts = self._t_trade_account_facts(
      runtime,
      instrument_code=code,
      requested_amount=requested_amount,
      current_intent_id=current_intent_id,
    )
    gate_result = self._intent_emission_gate.execute(
      IntentEmissionGateInput(
        account_id=account_id,
        runtime_run_id=runtime_run_id,
        context_run_id=context_run_id,
        instrument_code=code,
        universe_entry=entry,
        **facts.to_gate_facts(),
      )
    )
    gate_blockers = list(gate_result.blockers)
    if facts.blockers:
      # The pure account snapshot result already carries the stable, actionable
      # reason.  Do not dilute it with the four derivative tri-state UNKNOWN
      # codes produced from the same missing snapshot; unrelated scope and
      # universe blockers remain visible.
      unknown_fact_codes = {
        "T_TRADE_RECONCILIATION_STATUS_UNKNOWN",
        "T_TRADE_ACCOUNT_CONCURRENT_BATCH_LIMIT_UNKNOWN",
        "T_TRADE_ACCOUNT_TOTAL_EXPOSURE_LIMIT_UNKNOWN",
        "T_TRADE_SAME_INSTRUMENT_PENDING_INTENT_UNKNOWN",
      }
      gate_blockers = [
        blocker for blocker in gate_blockers if blocker not in unknown_fact_codes
      ]
    blockers = self._t_trade_unique_blockers([*facts.blockers, *gate_blockers])
    if account_id and check_coordination_lock:
      coordination_lock = t_trade_account_coordination_lock(account_id)
      if coordination_lock.locked():
        blockers.append("T_TRADE_ACCOUNT_COORDINATION_IN_PROGRESS")
    blockers = self._t_trade_unique_blockers(blockers)
    return {
      "allowed": not blockers,
      "blockers": blockers,
    }

  def _t_trade_account_facts(
    self,
    runtime: StrategyRuntime,
    *,
    instrument_code: str,
    requested_amount: Any,
    current_intent_id: Optional[str] = None,
  ) -> TTradeAccountFacts:
    """Read and normalize the current account facts without mutating state."""

    strategy_states = None
    if runtime.strategy is not None:
      raw_states = runtime.strategy.state.get("instrument_states")
      strategy_states = raw_states if isinstance(raw_states, Mapping) else None
    state_manager = runtime.state_manager
    account_quota = None
    get_account_quota = getattr(state_manager, "get_account_quota", None)
    if callable(get_account_quota):
      try:
        account_quota = get_account_quota()
      except Exception:
        account_quota = None
    parameters = dict(runtime.context.parameters or {})
    return compute_t_trade_account_facts(
      strategy_states,
      runtime.t_trade_entry_reservations,
      account_quota,
      requested_amount,
      instrument_code=instrument_code,
      current_intent_id=current_intent_id,
      max_concurrent_batches=parameters.get("max_concurrent_batches"),
      max_total_exposure_pct=parameters.get("max_total_t_exposure_pct"),
    )

  async def invalidate_t_trade_entry_authority(
    self,
    run_id: str,
    *,
    account_id: Optional[str] = None,
    reason: str = "T_TRADE_ENTRY_AUTHORITY_INVALIDATED",
  ) -> bool:
    """Clear only new-entry authority while retaining exit state and batches."""

    runtime = self.runs.get(str(run_id or "").strip())
    if runtime is None:
      return False
    expected_account = str(account_id or "").strip()
    actual_account = str(
      dict(runtime.context.parameters or {}).get("account_id") or ""
    ).strip()
    if expected_account and expected_account != actual_account:
      return False
    # The caller owns the account coordination lock.  Acquire the runtime
    # approval lock so an in-flight candidate cannot publish an AWAITING
    # transition after this invalidation becomes visible.
    await runtime.approval_lock.acquire()
    try:
      self._clear_t_trade_intent_emission_snapshot(runtime)
      self._runtime_log(
        runtime,
        "WARNING",
        f"做 T 新入场 authority 已失败关闭: reason={reason}",
      )
      return True
    finally:
      runtime.approval_lock.release()

  def _build_strategy_input(
    self,
    runtime: StrategyRuntime,
    *,
    cadence: StrategyCadence,
    instrument_code: str,
    timestamp: datetime,
    market_data: Optional[MarketDataSnapshot] = None,
    event: Any = None,
  ) -> StrategyInput:
    snapshot = self._build_execution_context_snapshot(
      runtime,
      instrument_code=instrument_code,
      market_data=market_data,
      event=event,
    )

    return StrategyInput(
      run_id=runtime.run_id,
      strategy_id=str(runtime.strategy_id),
      timestamp=timestamp,
      cadence=cadence,
      instrument_code=instrument_code,
      market_data=market_data,
      market_data_context=self._build_market_data_context(
        runtime,
        cadence=cadence,
        instrument_code=instrument_code,
        timestamp=timestamp,
        event=event,
      ),
      event=event,
      portfolio_state=snapshot.portfolio_state,
      bucket_ledger=snapshot.bucket_ledger,
      market_context=snapshot.market_context,
      risk_caps=snapshot.risk_caps,
      position_profile=snapshot.position_profile,
      execution_profile=self._build_execution_profile(
        runtime=runtime,
        account=snapshot.account,
        positions=snapshot.positions,
        risk_caps=snapshot.risk_caps,
        position_profile=snapshot.position_profile,
        market_context=snapshot.market_context,
        runtime_state=snapshot.runtime_state,
        parameters=snapshot.parameters,
        instrument_code=instrument_code,
      ),
      exit_plans=runtime.exit_plan_book.projections(instrument_code),
      open_orders=snapshot.open_orders,
      strategy_state=snapshot.runtime_state,
      parameters=snapshot.parameters,
    )

  def _build_market_data_context(
    self,
    runtime: StrategyRuntime,
    *,
    cadence: StrategyCadence,
    instrument_code: str,
    timestamp: datetime,
    event: Any,
  ) -> MarketDataContext:
    """Expose Engine-owned market lineage without leaking transport objects."""

    code = str(instrument_code or "").strip().upper()
    source_time_ms = self._safe_non_negative_int(
      self._get_value(event, "source_time_ms"),
      default=int(timestamp.timestamp() * 1000),
    )
    tick_ordinal = self._safe_non_negative_int(
      self._get_value(event, "tick_ordinal"),
      default=self._safe_non_negative_int(
        self._get_value(event, "transaction_num"),
        default=0,
      ),
    )
    source_sequence = self._safe_non_negative_int(
      self._get_value(event, "market_stream_sequence"),
      default=self._safe_non_negative_int(
        self._get_value(event, "source_sequence"),
        default=0,
      ),
    )
    transport_generation = self._safe_non_negative_int(
      self._get_value(event, "continuity_generation"),
      default=0,
    )
    is_replay = runtime.context.mode == StrategyRunMode.BACKTEST
    generation = (
      transport_generation
      if transport_generation > 0
      else max(
        0,
        int(
          (
            runtime._market_transport_generation
            if not is_replay
            else runtime._market_continuity_generations.get(code, 0)
          )
          or 0
        ),
      )
    )
    received_at_ms = (
      source_time_ms if is_replay else int(time_utils.now().timestamp() * 1000)
    )
    source = (
      "REPLAY"
      if is_replay
      else "REALTIME"
      if cadence in {StrategyCadence.TICK, StrategyCadence.BAR}
      else "CONTROL"
    )
    source_timestamp = time_utils.to_shanghai(
      datetime.fromtimestamp(source_time_ms / 1000, timezone.utc)
    )
    explicit_trade_date = self._get_value(event, "trade_date")
    if isinstance(explicit_trade_date, datetime):
      trade_date = explicit_trade_date.date()
    elif isinstance(explicit_trade_date, date):
      trade_date = explicit_trade_date
    elif isinstance(explicit_trade_date, str) and explicit_trade_date:
      try:
        trade_date = datetime.fromisoformat(explicit_trade_date).date()
      except ValueError:
        trade_date = source_timestamp.date()
    else:
      trade_date = source_timestamp.date()

    explicit_session = self._get_value(event, "session")
    try:
      session = (
        explicit_session
        if isinstance(explicit_session, MarketDataSession)
        else MarketDataSession(str(explicit_session))
      )
    except (TypeError, ValueError):
      session = self._classify_market_data_session(source_timestamp)

    explicit_stale = self._get_value(event, "quote_stale")
    quote_stale = False if explicit_stale is None else self._coerce_bool(explicit_stale)
    return MarketDataContext(
      source=source,
      stream_id=(
        str(self._get_value(event, "market_stream_id") or "").strip()
        or (
          runtime._market_transport_stream_id
          if not is_replay
          else f"{runtime.run_id}:replay"
        )
      ),
      continuity_generation=generation,
      source_sequence=source_sequence,
      source_time_ms=source_time_ms,
      tick_ordinal=tick_ordinal,
      received_at_ms=received_at_ms,
      quote_stale=quote_stale,
      session=session,
      trade_date=trade_date,
    )

  @staticmethod
  def _classify_market_data_session(value: datetime) -> MarketDataSession:
    """Classify source time once in Engine; strategies never consult a clock."""

    current = value.time()
    if current < time(9, 15):
      return MarketDataSession.PRE_OPEN
    if current < time(9, 30):
      return MarketDataSession.OPENING_AUCTION
    if current <= time(11, 30):
      return MarketDataSession.CONTINUOUS_AM
    if current < time(13, 0):
      return MarketDataSession.LUNCH_BREAK
    if current < time(14, 57):
      return MarketDataSession.CONTINUOUS_PM
    if current <= time(15, 0):
      return MarketDataSession.CLOSING_AUCTION
    return MarketDataSession.CLOSED

  @staticmethod
  def _coerce_bool(value: Any) -> bool:
    if isinstance(value, str):
      return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)

  @staticmethod
  def _safe_non_negative_int(value: Any, *, default: int) -> int:
    try:
      parsed = int(value)
    except (TypeError, ValueError, OverflowError):
      parsed = int(default)
    return max(0, parsed)

  def _build_execution_profile(
    self,
    runtime: StrategyRuntime,
    *,
    account: Dict[str, Any],
    positions: Dict[str, Any],
    risk_caps: Dict[str, Any],
    position_profile: Dict[str, Any],
    market_context: Dict[str, Any],
    runtime_state: Dict[str, Any],
    parameters: Dict[str, Any],
    instrument_code: str,
  ) -> Dict[str, Any]:
    """Build strategy-facing execution profile for orchestration layer."""
    profile = PortfolioOrchestrationLayer().build_profile(
      market_context=market_context or {},
      risk_caps=risk_caps or {},
      position_profile=position_profile or {},
      portfolio_state={"account": account, "positions": positions},
      runtime_state=runtime_state or {},
      parameters=parameters or {},
      instrument_code=instrument_code,
    )
    return profile.to_dict()

  def _build_market_context(
    self,
    runtime: StrategyRuntime,
    market_data: Optional[MarketDataSnapshot],
    event: Any,
  ) -> Dict[str, Any]:
    params = dict(runtime.context.parameters or {})
    params.setdefault(
      "require_market_index",
      runtime.context.mode == StrategyRunMode.LIVE,
    )
    previous_market_context = params.get("previous_market_context")
    if previous_market_context is None and runtime.strategy:
      try:
        previous_market_context = runtime.strategy.state.get("last_market_context")
      except Exception:
        previous_market_context = None
    instrument_code = (
      market_data.instrument_code
      if market_data and market_data.instrument_code
      else (getattr(event, "stock_code", None) or getattr(event, "code", None) or "")
    )
    evaluated_at = (
      market_data.timestamp
      if market_data and market_data.timestamp
      else runtime.context.current_time
    )
    data_context = AshareDataContextProvider().build_context(
      instrument_code=instrument_code,
      timestamp=(evaluated_at),
      market_data=market_data,
      event=event,
      parameters=params,
      previous_market_context=previous_market_context,
    )
    market_context = dict(data_context.market_context or {})
    if self._uses_t_trade_opportunity_runtime(runtime):
      market_context["t_trade_intent_emission"] = (
        self._t_trade_intent_emission_context(runtime, instrument_code)
      )
    if self._uses_t_trade_opportunity_runtime(runtime) and isinstance(
      evaluated_at, datetime
    ):
      cache_key = (
        str(instrument_code or "").strip().upper(),
        time_utils.to_shanghai(evaluated_at).date().isoformat(),
      )
      market_context["t_trade_instrument_profile"] = copy.deepcopy(
        runtime._t_trade_opportunity_profiles.get(cache_key)
      )
      profile_error = runtime._t_trade_opportunity_profile_errors.get(cache_key)
      if profile_error:
        market_context["t_trade_instrument_profile_error"] = profile_error
      runtime_failure = runtime._t_trade_opportunity_failures.get(cache_key[0])
      if runtime_failure:
        market_context["t_trade_opportunity_runtime_error"] = copy.deepcopy(
          runtime_failure
        )
    return market_context

  def _build_risk_caps(
    self,
    runtime: StrategyRuntime,
    account: Dict[str, Any],
    positions: Dict[str, Any],
    instrument_code: str,
    market_context: Optional[Dict[str, Any]] = None,
    order_state: Optional[Dict[str, Any]] = None,
    broker_report: Optional[Dict[str, Any]] = None,
    runtime_state: Optional[Dict[str, Any]] = None,
    parameters: Optional[Dict[str, Any]] = None,
  ) -> Dict[str, Any]:
    """Build deterministic pre-risk caps from run parameters and portfolio snapshot."""
    params = dict(
      parameters if parameters is not None else runtime.context.parameters or {}
    )
    state = dict(
      runtime_state
      if runtime_state is not None
      else (runtime.strategy.state.to_dict() if runtime.strategy else {})
    )
    return (
      ContextRiskLayer()
      .build_caps(
        portfolio_state={"account": account, "positions": positions},
        market_context=market_context or {},
        order_state=order_state or {},
        broker_report=broker_report or {},
        runtime_state=state,
        parameters=params,
        instrument_code=instrument_code,
      )
      .to_dict()
    )

  def _build_position_profile(
    self,
    runtime: StrategyRuntime,
    *,
    portfolio_state: Dict[str, Any],
    market_context: Dict[str, Any],
    risk_caps: Dict[str, Any],
    bucket_ledger: Dict[str, Any],
    instrument_code: str,
    runtime_state: Optional[Dict[str, Any]] = None,
    parameters: Optional[Dict[str, Any]] = None,
  ) -> Dict[str, Any]:
    params = dict(
      parameters if parameters is not None else runtime.context.parameters or {}
    )
    if params.get("position_profile") and not params.get("position_profile_overrides"):
      params["position_profile_overrides"] = dict(params.get("position_profile") or {})
    state = dict(
      runtime_state
      if runtime_state is not None
      else (runtime.strategy.state.to_dict() if runtime.strategy else {})
    )
    base_profile = (
      PositionAdjustmentLayer()
      .build_profile(
        market_context=market_context,
        risk_caps=risk_caps,
        portfolio_state=portfolio_state,
        bucket_ledger=bucket_ledger,
        runtime_state=state,
        parameters=params,
        instrument_code=instrument_code,
      )
      .to_dict()
    )
    base_profile.setdefault("instrument_code", instrument_code)
    return base_profile

  async def _process_strategy_output(
    self,
    runtime: StrategyRuntime,
    output: Optional[StrategyOutput],
    input_snapshot: Optional[StrategyInput] = None,
  ) -> None:
    if not output:
      return
    try:
      self.opportunity_observability.observe_output(
        run_id=runtime.run_id,
        output=output,
      )
    except Exception as exc:
      # Metrics are deliberately non-authoritative and must never interrupt a
      # strategy decision or its durability boundary.
      self.logger.warning(
        "做 T 运行指标采集降级: run_id=%s error=%s",
        runtime.run_id,
        exc.__class__.__name__,
      )
    opportunity_events = self._t_trade_opportunity_evaluation_events(output)
    if opportunity_events:
      await self._process_t_trade_opportunity_output(
        runtime,
        output,
        opportunity_events=opportunity_events,
        input_snapshot=input_snapshot,
      )
      return
    intents = output.trade_intents or []
    if runtime._checkpoint_diagnostic_summaries and (
      intents or output.runtime_state_patch or output.exit_plan_commands
    ):
      # Immediate facts are allowed to cross a hot diagnostic window.  Keep
      # the earlier diagnostics deferred, but close their aggregates so a
      # later diagnostic cannot be merged across the durable action.
      source_time_ms = (
        int(input_snapshot.market_data_context.source_time_ms)
        if input_snapshot is not None
        else 0
      )
      instrument_codes = [
        str(intent.instrument_code or "").strip().upper() for intent in intents
      ]
      if input_snapshot is not None:
        instrument_codes.append(
          str(input_snapshot.instrument_code or "").strip().upper()
        )
      boundary_key = next(
        (
          f"INTENT:{intent.intent_id}"
          for intent in intents
          if str(intent.intent_id or "").strip()
        ),
        "",
      )
      if not boundary_key:
        boundary_key = (
          f"NON_EVALUATION:{source_time_ms}:"
          f"{','.join(sorted(code for code in instrument_codes if code))}"
        )
      self._freeze_checkpoint_diagnostic_segments(
        runtime,
        instrument_codes=instrument_codes,
        boundary_event_key=boundary_key,
        boundary_kind="IMMEDIATE_ACTION",
      )
    if not self._accepts_non_durable_output(runtime):
      self._runtime_log(
        runtime,
        "INFO",
        f"运行状态 {runtime.status.value}，忽略尚未进入下单链路的策略输出",
      )
      return
    reconciliation_failure = self._runtime_state_reconciliation_failure(runtime)
    if reconciliation_failure is not None and output.trade_intents:
      self._runtime_log(
        runtime,
        "WARNING",
        f"{reconciliation_failure[0]}: {reconciliation_failure[1]}",
      )
      return
    input_continuity_failure = (
      self._runtime_market_continuity_failure(
        runtime,
        input_snapshot.instrument_code,
      )
      if input_snapshot is not None
      else None
    )
    continuity_failures: list[tuple[TradeIntent, tuple[str, str]]] = []
    if input_continuity_failure is not None:
      continuity_failures = [(intent, input_continuity_failure) for intent in intents]
    else:
      for intent in intents:
        failure = self._runtime_market_continuity_failure(
          runtime,
          intent.instrument_code,
        )
        if failure is not None:
          continuity_failures.append((intent, failure))
    if input_continuity_failure is not None or continuity_failures:
      self._record_strategy_output_trace(runtime, output, input_snapshot)
      for intent, failure in continuity_failures:
        await self._reject_intent_for_market_continuity(
          runtime,
          intent,
          failure=failure,
        )
      return
    if output.runtime_state_patch:
      self._apply_runtime_state_patch(runtime, output.runtime_state_patch)
    if output.exit_plan_commands:
      for command in output.exit_plan_commands:
        runtime.exit_plan_book.apply_command(command)
      self._persist_exit_plan_book(runtime)
    if runtime.context.mode == StrategyRunMode.BACKTEST:
      created_at = self._runtime_now(runtime)
      for intent in intents:
        intent.created_at = created_at
    if runtime.metrics:
      runtime.metrics.trade_intents_generated += len(intents)
    if runtime.state_manager:
      for intent in intents:
        status = (
          "AWAITING_APPROVAL"
          if intent.execution_mode == TradeIntentExecutionMode.MANUAL_CONFIRM
          else "PENDING"
        )
        await runtime.state_manager.record_trade_intent(intent, status=status)
    if runtime.strategy:
      for intent in intents:
        runtime.strategy.record_trade_intent(intent)
    self._record_strategy_output_trace(runtime, output, input_snapshot)
    if not self._accepts_non_durable_output(runtime):
      for intent in intents:
        await self._reject_intent_during_runtime_transition(runtime, intent)
      return
    for intent in intents:
      if intent.execution_mode == TradeIntentExecutionMode.MANUAL_CONFIRM:
        runtime.pending_approvals[intent.intent_id] = intent
        if (
          runtime.context.mode == StrategyRunMode.BACKTEST
          and runtime.context.parameters.get("auto_approve_manual_intents")
          and not runtime.context.parameters.get("limit_up_board_replay")
        ):
          result = await self.approve_trade_intent(
            runtime.run_id,
            intent.intent_id,
            approval_expectation=self._v3_t_trade_expectation_from_intent(intent),
          )
          self._runtime_log(
            runtime,
            "INFO" if result.get("success") else "WARNING",
            f"回放测试自动确认交易信号: intent_id={intent.intent_id}, "
            f"result={result.get('code')}",
          )
          continue
        self._runtime_log(
          runtime,
          "INFO",
          f"交易信号等待人工确认: {intent.instrument_code} {intent.direction.value} "
          f"intent_id={intent.intent_id}",
        )
        continue
      await self._process_trade_intent(runtime, intent)

  @staticmethod
  def _t_trade_opportunity_evaluation_events(
    output: StrategyOutput,
  ) -> List[Dict[str, Any]]:
    patch = output.runtime_state_patch
    if patch is None:
      return []
    events = list(getattr(patch, "append_events", []) or [])
    material_events: List[Dict[str, Any]] = []
    for raw_event in events:
      if not isinstance(raw_event, Mapping):
        continue
      if raw_event.get("type") != T_TRADE_OPPORTUNITY_EVALUATION_EVENT:
        continue
      record_kind = str(raw_event.get("record_kind") or "").upper()
      if record_kind == "MATERIAL":
        material_events.append(dict(raw_event))
        continue
      if record_kind == "COALESCED_DIAGNOSTIC":
        # Legacy normal-observation output is deliberately handled by the
        # generic no-op state path below, where it is also stripped from the
        # durable RuntimeState ring.  It must not enter checkpoint staging or
        # evaluation materialization.
        continue
      raise ValueError("T-trade opportunity event requires MATERIAL record_kind")
    return material_events

  @staticmethod
  def _t_trade_observability_labels(
    events: List[Dict[str, Any]],
  ) -> Dict[str, str]:
    snapshot = next(
      (
        dict(event.get("signal_snapshot") or {})
        for event in reversed(events)
        if isinstance(event.get("signal_snapshot"), Mapping)
      ),
      {},
    )
    return {
      "path": str(snapshot.get("selected_path") or "NONE"),
      "health": str(snapshot.get("data_health") or "UNKNOWN"),
      "policy_version": str(snapshot.get("policy_version") or "UNKNOWN"),
    }

  @staticmethod
  def _is_transient_evaluation_materialization_error(exc: Exception) -> bool:
    if isinstance(exc, (asyncio.TimeoutError, ConnectionError, OperationalError)):
      return True
    if isinstance(exc, DBAPIError):
      return bool(getattr(exc, "connection_invalidated", False))
    return exc.__class__.__name__ in {
      "CannotConnectNowError",
      "ConnectionDoesNotExistError",
      "ConnectionFailureError",
      "InterfaceError",
      "TooManyConnectionsError",
    }

  async def _materialize_t_trade_evaluation_with_retry(
    self,
    *,
    event: Dict[str, Any],
    account_id: str,
    strategy_run_id: str,
    labels: Dict[str, str],
    cas_committed: bool,
  ) -> Any:
    """Retry post-CAS appends; event_key keeps retries idempotent.

    Every caller supplies the successful RuntimeState checkpoint fact.  The
    application use case remains the final guard so a future caller cannot
    accidentally materialize an event before its CAS boundary.
    """

    maximum_attempts = 3
    for attempt in range(1, maximum_attempts + 1):
      try:
        materialized = await self._evaluation_materializer.execute(
          PostCasEvaluationInput(
            event=event,
            account_id=account_id,
            strategy_run_id=strategy_run_id,
            cas_committed=cas_committed,
          )
        )
        if not materialized.materialized:
          raise RuntimeError(materialized.reason or "RUNTIME_STATE_CAS_NOT_COMMITTED")
        self.opportunity_observability.record_operation(
          "evaluation_materialization_attempts_total",
          detail="SUCCESS",
          **labels,
        )
        return materialized.record
      except Exception as exc:
        classification_error = (
          exc.cause
          if isinstance(exc, EvaluationMaterializationError)
          else exc
        )
        transient = self._is_transient_evaluation_materialization_error(
          classification_error
        )
        self.opportunity_observability.record_operation(
          "evaluation_materialization_attempts_total",
          detail=("TRANSIENT_FAILURE" if transient else "PERMANENT_FAILURE"),
          **labels,
        )
        if not transient or attempt >= maximum_attempts:
          raise classification_error
        self.opportunity_observability.record_operation(
          "evaluation_materialization_retries_total",
          detail=f"RETRY_{attempt}",
          **labels,
        )
        await asyncio.sleep(0.05 * (2 ** (attempt - 1)))
    raise RuntimeError("unreachable T-trade evaluation materialization retry state")

  @staticmethod
  def _uses_deferred_diagnostic_checkpointing(
    runtime: StrategyRuntime,
  ) -> bool:
    return bool(
      runtime.context.mode
      in {StrategyRunMode.BACKTEST, StrategyRunMode.PAPER, StrategyRunMode.LIVE}
      and runtime.state_manager is not None
      and getattr(runtime.state_manager, "persist_enabled", False)
    )

  @classmethod
  def _is_deferred_checkpoint_evaluation_output(
    cls,
    runtime: StrategyRuntime,
    output: StrategyOutput,
    *,
    intents: List[TradeIntent],
    opportunity_events: List[Dict[str, Any]],
  ) -> bool:
    """Return whether an evaluation has no immediate business consequence."""

    return bool(
      cls._uses_deferred_diagnostic_checkpointing(runtime)
      and not intents
      and not output.exit_plan_commands
      and opportunity_events
      and all(
        str(event.get("record_kind") or "").upper()
        in {"COALESCED_DIAGNOSTIC", "MATERIAL"}
        for event in opportunity_events
      )
    )

  @staticmethod
  def _has_immediate_actionable_t_trade_output(
    output: StrategyOutput,
    *,
    intents: List[TradeIntent],
  ) -> bool:
    """Identify the narrow P/L MATERIAL recovery boundary.

    A MATERIAL evaluation alone is a state-transition audit and is transferred
    only by the explicit day/session coordinator.  The legacy MATERIAL outbox
    remains solely for an output that can immediately expose a candidate,
    TradeIntent, or exit command in PAPER/LIVE.
    """

    return bool(intents or output.exit_plan_commands)

  @staticmethod
  def _ordered_t_trade_diagnostic_events(
    events: Iterable[Mapping[str, Any]],
  ) -> list[Dict[str, Any]]:
    """Recover a deterministic source-time order from a durable outbox."""

    def _int(value: Any) -> int:
      try:
        return int(value)
      except (TypeError, ValueError, OverflowError):
        return 0

    def _key(event: Mapping[str, Any]) -> tuple[int, int, int, str]:
      snapshot = event.get("signal_snapshot")
      data = dict(snapshot) if isinstance(snapshot, Mapping) else {}
      return (
        _int(event.get("evaluated_at_ms")),
        _int(data.get("source_time_ms")),
        _int(data.get("tick_ordinal")),
        str(event.get("event_key") or ""),
      )

    return [dict(event) for event in sorted(events, key=_key)]

  async def _materialize_t_trade_checkpoint_batch_with_retry(
    self,
    *,
    events: List[Dict[str, Any]],
    account_id: str,
    strategy_run_id: str,
  ) -> frozenset[str]:
    """Append one committed diagnostic/pure-MATERIAL checkpoint batch."""

    if not events:
      return frozenset()
    labels = self._t_trade_observability_labels(events)
    requests = [
      PostCasEvaluationInput(
        event=event,
        account_id=account_id,
        strategy_run_id=strategy_run_id,
        cas_committed=True,
      )
      for event in events
    ]
    maximum_attempts = 3
    for attempt in range(1, maximum_attempts + 1):
      try:
        persisted_event_keys = await (
          self._evaluation_materializer.execute_checkpoint_batch(requests)
        )
        self.opportunity_observability.record_operation(
          "evaluation_materialization_attempts_total",
          detail="BATCH_SUCCESS",
          **labels,
        )
        return frozenset(persisted_event_keys)
      except Exception as exc:
        classification_error = (
          exc.cause
          if isinstance(exc, EvaluationMaterializationError)
          else exc
        )
        transient = self._is_transient_evaluation_materialization_error(
          classification_error
        )
        self.opportunity_observability.record_operation(
          "evaluation_materialization_attempts_total",
          detail=("BATCH_TRANSIENT_FAILURE" if transient else "BATCH_PERMANENT_FAILURE"),
          **labels,
        )
        if not transient or attempt >= maximum_attempts:
          raise classification_error
        self.opportunity_observability.record_operation(
          "evaluation_materialization_retries_total",
          detail=f"BATCH_RETRY_{attempt}",
          **labels,
        )
        await asyncio.sleep(0.05 * (2 ** (attempt - 1)))

  async def _seed_t_trade_candidate_outcome(
    self,
    runtime: StrategyRuntime,
    *,
    account_id: str,
    event: Mapping[str, Any],
    strict: bool = False,
  ) -> bool:
    facade = self.candidate_outcome_facade
    if facade is None:
      return True
    try:
      state = await facade.seed_material_event(
        account_id=account_id,
        strategy_run_id=runtime.run_id,
        event=event,
      )
      if state is not None and self._candidate_outcome_state_is_active(state):
        self._candidate_outcome_activity[
          (runtime.run_id, state.definition.instrument_code)
        ] = True
      return True
    except Exception:
      self._candidate_outcome_reconciled_runs.discard(runtime.run_id)
      self._candidate_outcome_repair_attempts.pop(runtime.run_id, None)
      self._candidate_outcome_repair_retry_at_ms.pop(runtime.run_id, None)
      self.logger.exception(
        "做 T 候选结果初始化失败: run_id=%s event_key=%s",
        runtime.run_id,
        event.get("event_key"),
      )
      if strict or self._requires_replay_event_integrity(runtime):
        runtime.status = ExecutionStatus.ERROR
        runtime.error_message = "T_TRADE_CANDIDATE_OUTCOME_SEED_FAILED"
        raise
      return False

  async def _acknowledge_t_trade_actionable_material_events(
    self,
    runtime: StrategyRuntime,
    events: List[Mapping[str, Any]],
  ) -> None:
    """Ack only PAPER/LIVE actionable MATERIAL recovery records."""

    if runtime.context.mode not in {StrategyRunMode.PAPER, StrategyRunMode.LIVE}:
      return
    keys = [
      str(event.get("event_key") or "").strip()
      for event in events
      if str(event.get("record_kind") or "").upper() == "MATERIAL"
      and str(event.get("event_key") or "").strip()
    ]
    if not keys:
      return
    acknowledge = getattr(
      runtime.state_manager,
      "acknowledge_t_trade_material_events",
      None,
    )
    force_save = getattr(runtime.state_manager, "force_save", None)
    if not callable(acknowledge) or not callable(force_save):
      raise RuntimeError("V3 做 T 运行缺少 MATERIAL outbox 确认边界")
    acknowledge(keys)
    try:
      saved = bool(await force_save())
    except Exception:
      saved = False
      save_error = True
    else:
      save_error = False
    if saved:
      return

    # A non-CAS persistence failure only removed the entries in memory. Put
    # them back before another background snapshot can accidentally erase the
    # still-pending durable rows. A CAS loser has already adopted the external
    # winner and must never resurrect an event that the winner may have acked.
    if (
      str(getattr(runtime.state_manager, "last_snapshot_failure_code", "") or "")
      != "CAS_CONFLICT"
    ):
      enqueue = getattr(
        runtime.state_manager,
        "enqueue_t_trade_material_events",
        None,
      )
      if callable(enqueue):
        enqueue([dict(event) for event in events])
    if save_error:
      raise RuntimeError("V3 做 T MATERIAL outbox 确认保存异常")
    raise RuntimeError("V3 做 T MATERIAL outbox 确认保存失败")

  async def _replay_pending_actionable_t_trade_material_events(
    self,
    runtime: StrategyRuntime,
  ) -> None:
    """Recover the narrow P/L actionable-MATERIAL outbox after a restart."""

    if runtime.context.mode not in {StrategyRunMode.PAPER, StrategyRunMode.LIVE}:
      return
    pending_loader = getattr(
      runtime.state_manager,
      "pending_t_trade_material_events",
      None,
    )
    if not callable(pending_loader):
      raise RuntimeError("V3 做 T 运行缺少 MATERIAL durable outbox")
    pending = [dict(event) for event in list(pending_loader() or [])]
    if not pending:
      return
    account_id = str(runtime.context.parameters.get("account_id") or "").strip()
    if not account_id:
      raise RuntimeError("V3 做 T MATERIAL outbox 重放缺少账户绑定")
    acknowledged: List[Mapping[str, Any]] = []
    for event in pending:
      labels = self._t_trade_observability_labels([event])
      await self._materialize_t_trade_evaluation_with_retry(
        event=event,
        account_id=account_id,
        strategy_run_id=runtime.run_id,
        labels=labels,
        cas_committed=True,
      )
      seeded = await self._seed_t_trade_candidate_outcome(
        runtime,
        account_id=account_id,
        event=event,
        strict=True,
      )
      if not seeded:
        raise RuntimeError("V3 做 T MATERIAL outbox 候选结果初始化失败")
      acknowledged.append(event)
    await self._acknowledge_t_trade_actionable_material_events(runtime, acknowledged)

  @staticmethod
  def _initialize_t_trade_phase_one_baseline(runtime: StrategyRuntime) -> None:
    runtime.t_trade_phase_one_baseline = None
    if (
      runtime.context.mode == StrategyRunMode.BACKTEST
      and runtime.context.parameters.get("t_trade_replay")
    ):
      runtime.t_trade_phase_one_baseline = TTradePhaseOneBaselineAccumulator(
        runtime.run_id
      )

  @staticmethod
  def _observe_t_trade_phase_one_baseline(
    runtime: StrategyRuntime,
    strategy_input: StrategyInput,
  ) -> None:
    baseline = runtime.t_trade_phase_one_baseline
    if baseline is not None:
      v3_data_ready, v3_candidate_path = (
        StrategyExecutor._t_trade_phase_one_v3_comparison_fact(
          runtime,
          strategy_input,
        )
      )
      baseline.observe(
        strategy_input,
        v3_data_ready=v3_data_ready,
        v3_candidate_path=v3_candidate_path,
      )

  @staticmethod
  def _t_trade_phase_one_v3_comparison_fact(
    runtime: StrategyRuntime,
    strategy_input: StrategyInput,
  ) -> tuple[Optional[bool], Optional[str]]:
    strategy = runtime.strategy
    state_container = getattr(strategy, "state", None)
    if strategy is None or state_container is None:
      return None, None
    raw_states = state_container.get("instrument_states", {})
    states = dict(raw_states) if isinstance(raw_states, Mapping) else {}
    raw_state = states.get(str(strategy_input.instrument_code or "").upper())
    state = dict(raw_state) if isinstance(raw_state, Mapping) else {}
    opportunity = dict(state.get("opportunity") or {})
    evaluation = dict(opportunity.get("latest_evaluation") or {})
    context = strategy_input.market_data_context
    try:
      same_source = bool(
        int(evaluation.get("source_time_ms") or -1) == int(context.source_time_ms)
        and int(evaluation.get("tick_ordinal") or -1) == int(context.tick_ordinal)
        and str(evaluation.get("continuity_generation") or "")
        == str(context.continuity_generation)
      )
    except (TypeError, ValueError, OverflowError):
      same_source = False
    if not same_source:
      return None, None
    data_ready = str(evaluation.get("data_health") or "").upper() == "READY"
    candidate_path: Optional[str] = None
    try:
      candidate_created_on_source = bool(
        evaluation.get("candidate_id")
        and int(evaluation.get("candidate_created_at_ms") or -1)
        == int(context.source_time_ms)
      )
    except (TypeError, ValueError, OverflowError):
      candidate_created_on_source = False
    if candidate_created_on_source:
      candidate_path = str(evaluation.get("selected_path") or "").upper() or None
    return data_ready, candidate_path

  def _finalize_t_trade_phase_one_baseline(
    self,
    runtime: StrategyRuntime,
  ) -> None:
    baseline = runtime.t_trade_phase_one_baseline
    if baseline is None:
      return
    finalized_at_ms = int(
      time_utils.to_utc(self._runtime_now(runtime)).timestamp() * 1000
    )
    baseline.finalize(finalized_at_ms)

  async def _observe_t_trade_candidate_outcomes(
    self,
    runtime: StrategyRuntime,
    *,
    input_snapshot: StrategyInput,
    market_data: MarketDataSnapshot,
  ) -> None:
    facade = self.candidate_outcome_facade
    context = input_snapshot.market_data_context
    if (
      facade is None
      or context is None
      or not self._uses_t_trade_opportunity_runtime(runtime)
    ):
      return
    if runtime.context.mode == StrategyRunMode.PAPER:
      try:
        await self._replay_pending_t_trade_paper_fill_facts(runtime)
      except Exception:
        self.logger.exception(
          "做 T PAPER 候选结果 outbox 重放失败，保留事实等待后续 Tick: "
          "run_id=%s instrument=%s",
          runtime.run_id,
          input_snapshot.instrument_code,
        )
        return
    activity_key = (
      runtime.run_id,
      str(input_snapshot.instrument_code or "").upper(),
    )
    if (
      runtime.context.mode == StrategyRunMode.LIVE
      and runtime.run_id not in self._candidate_outcome_reconciled_runs
      and int(context.source_time_ms)
      >= self._candidate_outcome_repair_retry_at_ms.get(runtime.run_id, 0)
    ):
      try:
        repair = await facade.reconcile_applied_trade_events(
          strategy_run_id=runtime.run_id,
        )
        self._candidate_outcome_repair_attempts.pop(runtime.run_id, None)
        self._candidate_outcome_repair_retry_at_ms.pop(runtime.run_id, None)
        if repair.complete:
          self._candidate_outcome_reconciled_runs.add(runtime.run_id)
        elif repair.deferred_count:
          self._candidate_outcome_repair_retry_at_ms[runtime.run_id] = (
            int(context.source_time_ms) + 5_000
          )
        if repair.quarantined_count:
          self.logger.warning(
            "做 T 候选结果隔离无效 APPLIED 成交: run_id=%s instrument=%s "
            "quarantined=%s issues=%s",
            runtime.run_id,
            input_snapshot.instrument_code,
            repair.quarantined_count,
            dict(repair.issue_counts),
          )
        if repair.states:
          active_instruments = {
            str(state.definition.instrument_code or "").strip().upper()
            for state in repair.states
            if self._candidate_outcome_state_is_active(state)
            and str(state.definition.instrument_code or "").strip()
          }
          for instrument_code in active_instruments:
            self._candidate_outcome_activity[
              (runtime.run_id, instrument_code)
            ] = True
      except Exception:
        attempt = min(
          self._candidate_outcome_repair_attempts.get(runtime.run_id, 0) + 1,
          6,
        )
        self._candidate_outcome_repair_attempts[runtime.run_id] = attempt
        delay_ms = min(60_000, 5_000 * (2 ** (attempt - 1)))
        self._candidate_outcome_repair_retry_at_ms[runtime.run_id] = (
          int(context.source_time_ms) + delay_ms
        )
        self.logger.exception(
          "做 T 候选结果持久化成交重放失败: run_id=%s instrument=%s retry_in_ms=%s",
          runtime.run_id,
          input_snapshot.instrument_code,
          delay_ms,
        )
    if self._candidate_outcome_activity.get(activity_key) is False:
      return
    try:
      states = await facade.observe_tick(
        strategy_run_id=runtime.run_id,
        instrument_code=str(input_snapshot.instrument_code or "").upper(),
        source_time_ms=int(context.source_time_ms),
        tick_ordinal=int(context.tick_ordinal),
        continuity_generation=str(context.continuity_generation),
        price=float(market_data.price),
        trading_halted=bool(market_data.suspended),
      )
      self._candidate_outcome_activity[activity_key] = any(
        self._candidate_outcome_state_is_active(state) for state in (states or [])
      )
    except Exception:
      self.logger.exception(
        "做 T 候选结果 Tick 成熟失败: run_id=%s instrument=%s",
        runtime.run_id,
        input_snapshot.instrument_code,
      )
      if self._requires_replay_event_integrity(runtime):
        runtime.status = ExecutionStatus.ERROR
        runtime.error_message = "T_TRADE_CANDIDATE_OUTCOME_TICK_FAILED"
        raise

  async def _build_t_trade_paper_fill_fact(
    self,
    runtime: StrategyRuntime,
    trade: Any,
  ) -> Optional[Dict[str, Any]]:
    """Freeze one simulator fill before the strategy snapshot is committed."""

    if runtime.context.mode != StrategyRunMode.PAPER:
      return None
    metadata = dict(getattr(trade, "metadata", {}) or {})
    candidate_id = str(metadata.get("candidate_id") or "").strip()
    if not candidate_id:
      return None
    account_id = str(runtime.context.parameters.get("account_id") or "").strip()
    instrument_code = str(getattr(trade, "instrument_code", "") or "").strip().upper()
    trade_id = str(getattr(trade, "trade_id", "") or "").strip()
    order_id = str(getattr(trade, "order_id", "") or "").strip()
    role = str(metadata.get("t_trade_role") or "").strip().upper()
    candidate_fingerprint = str(
      metadata.get("candidate_fingerprint") or ""
    ).strip()
    policy_version = str(metadata.get("policy_version") or "").strip()
    intent_id = str(metadata.get("intent_id") or "").strip()
    if (
      not account_id
      or not instrument_code
      or not trade_id
      or not order_id
      or role not in {"ENTRY", "EXIT"}
      or not candidate_fingerprint
      or not policy_version
      or not intent_id
      or str(metadata.get("strategy_run_id") or "").strip() != runtime.run_id
      or str(metadata.get("account_id") or "").strip() != account_id
      or str(metadata.get("instrument_code") or "").strip().upper()
      != instrument_code
    ):
      raise ValueError("做 T PAPER 成交缺少完整且匹配的候选作用域")

    trade_time = getattr(trade, "trade_time", None)
    if not isinstance(trade_time, datetime):
      raise ValueError("做 T PAPER 成交缺少权威成交时间")
    trade_type = str(
      getattr(getattr(trade, "trade_type", None), "value", None)
      or getattr(trade, "trade_type", "")
      or ""
    ).strip().upper()
    if trade_type not in {item.value for item in BrokerOrderType}:
      raise ValueError("做 T PAPER 成交方向无效")

    price = float(getattr(trade, "price", 0.0) or 0.0)
    volume = int(getattr(trade, "volume", 0) or 0)
    amount = float(getattr(trade, "amount", 0.0) or 0.0)
    commission = float(getattr(trade, "commission", 0.0) or 0.0)
    if (
      not isfinite(price)
      or price <= 0
      or volume <= 0
      or not isfinite(amount)
      or amount < 0
      or not isfinite(commission)
      or commission < 0
    ):
      raise ValueError("做 T PAPER 成交数值无效")

    entry_complete: Optional[bool] = None
    entry_target_volume: Optional[int] = None
    if role == "ENTRY":
      order = await runtime.broker.get_order(order_id)
      requested_volume = int(
        getattr(getattr(order, "request", None), "volume", 0) or 0
      )
      if requested_volume <= 0:
        raise ValueError("做 T PAPER 入场成交缺少委托目标数量")
      entry_target_volume = requested_volume
      entry_complete = bool(
        order is not None
        and order.status == OrderStatus.FILLED
        and int(getattr(order, "filled_volume", 0) or 0) >= requested_volume
      )

    persisted_metadata = {
      "strategy_run_id": runtime.run_id,
      "account_id": account_id,
      "instrument_code": instrument_code,
      "candidate_id": candidate_id,
      "candidate_fingerprint": candidate_fingerprint,
      "policy_version": policy_version,
      "t_trade_role": role.lower(),
      "intent_id": intent_id,
    }
    return {
      "schema_version": 1,
      "fact_key": f"paper-fill:{runtime.run_id}:{trade_id}",
      "trade_id": trade_id,
      "order_id": order_id,
      "instrument_code": instrument_code,
      "trade_type": trade_type,
      "price": price,
      "volume": volume,
      "amount": amount,
      "commission": commission,
      "trade_time": trade_time.isoformat(),
      "metadata": persisted_metadata,
      "entry_complete": entry_complete,
      "entry_target_volume": entry_target_volume,
    }

  @staticmethod
  def _trade_from_t_trade_paper_fill_fact(
    runtime: StrategyRuntime,
    fact: Mapping[str, Any],
  ) -> tuple[TradeRecord, Optional[bool], Optional[int]]:
    """Strictly restore a bounded PAPER fill fact from RuntimeState."""

    if int(fact.get("schema_version") or 0) != 1:
      raise ValueError("做 T PAPER 成交 outbox schema 不受支持")
    metadata = dict(fact.get("metadata") or {})
    trade_id = str(fact.get("trade_id") or "").strip()
    expected_key = f"paper-fill:{runtime.run_id}:{trade_id}"
    if str(fact.get("fact_key") or "").strip() != expected_key:
      raise ValueError("做 T PAPER 成交 outbox 业务键不匹配")
    account_id = str(runtime.context.parameters.get("account_id") or "").strip()
    instrument_code = str(fact.get("instrument_code") or "").strip().upper()
    order_id = str(fact.get("order_id") or "").strip()
    candidate_id = str(metadata.get("candidate_id") or "").strip()
    candidate_fingerprint = str(
      metadata.get("candidate_fingerprint") or ""
    ).strip()
    policy_version = str(metadata.get("policy_version") or "").strip()
    intent_id = str(metadata.get("intent_id") or "").strip()
    missing_identity_fields = [
      field_name
      for field_name, value in (
        ("trade_id", trade_id),
        ("account_id", account_id),
        ("instrument_code", instrument_code),
        ("order_id", order_id),
        ("candidate_id", candidate_id),
        ("candidate_fingerprint", candidate_fingerprint),
        ("policy_version", policy_version),
        ("intent_id", intent_id),
      )
      if not value
    ]
    if missing_identity_fields:
      raise ValueError(
        "做 T PAPER 成交 outbox 缺少候选身份字段: "
        + ",".join(missing_identity_fields)
      )
    if (
      str(metadata.get("strategy_run_id") or "").strip() != runtime.run_id
      or str(metadata.get("account_id") or "").strip() != account_id
      or str(metadata.get("instrument_code") or "").strip().upper()
      != instrument_code
    ):
      raise ValueError("做 T PAPER 成交 outbox 作用域不匹配")
    try:
      trade_time = datetime.fromisoformat(str(fact.get("trade_time") or ""))
      trade_type = BrokerOrderType(str(fact.get("trade_type") or "").upper())
      price = float(fact.get("price"))
      volume = int(fact.get("volume"))
      amount = float(fact.get("amount"))
      commission = float(fact.get("commission"))
    except (TypeError, ValueError, OverflowError) as exc:
      raise ValueError("做 T PAPER 成交 outbox 载荷无效") from exc
    if (
      not isfinite(price)
      or price <= 0
      or volume <= 0
      or not isfinite(amount)
      or amount < 0
      or not isfinite(commission)
      or commission < 0
    ):
      raise ValueError("做 T PAPER 成交 outbox 数值无效")
    role = str(metadata.get("t_trade_role") or "").strip().upper()
    raw_complete = fact.get("entry_complete")
    raw_target = fact.get("entry_target_volume")
    if role == "ENTRY":
      if not isinstance(raw_complete, bool):
        raise ValueError("做 T PAPER 入场成交 outbox 缺少完成态")
      try:
        entry_target_volume = int(raw_target)
      except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("做 T PAPER 入场成交 outbox 缺少目标数量") from exc
      if entry_target_volume <= 0:
        raise ValueError("做 T PAPER 入场成交 outbox 目标数量无效")
      entry_complete: Optional[bool] = raw_complete
    elif role == "EXIT":
      if raw_complete is not None or raw_target is not None:
        raise ValueError("做 T PAPER 出场成交 outbox 包含非法入场字段")
      entry_complete = None
      entry_target_volume = None
    else:
      raise ValueError("做 T PAPER 成交 outbox 角色无效")
    return (
      TradeRecord(
        trade_id=trade_id,
        order_id=order_id,
        instrument_code=instrument_code,
        trade_type=trade_type,
        price=price,
        volume=volume,
        amount=amount,
        commission=commission,
        trade_time=trade_time,
        metadata=metadata,
      ),
      entry_complete,
      entry_target_volume,
    )

  async def _acknowledge_t_trade_paper_fill_facts(
    self,
    runtime: StrategyRuntime,
    facts: List[Mapping[str, Any]],
  ) -> None:
    if not facts:
      return
    acknowledge = getattr(
      runtime.state_manager,
      "acknowledge_t_trade_paper_fill_facts",
      None,
    )
    force_save = getattr(runtime.state_manager, "force_save", None)
    keys = [str(fact.get("fact_key") or "").strip() for fact in facts]
    if not callable(acknowledge) or not callable(force_save) or not all(keys):
      raise RuntimeError("V3 做 T PAPER 成交缺少 outbox 确认边界")
    acknowledge(keys)
    try:
      saved = bool(await force_save())
    except Exception:
      saved = False
      save_error = True
    else:
      save_error = False
    if saved:
      return
    if (
      str(getattr(runtime.state_manager, "last_snapshot_failure_code", "") or "")
      != "CAS_CONFLICT"
    ):
      enqueue = getattr(
        runtime.state_manager,
        "enqueue_t_trade_paper_fill_fact",
        None,
      )
      if callable(enqueue):
        for fact in facts:
          enqueue(dict(fact))
    if save_error:
      raise RuntimeError("V3 做 T PAPER 成交 outbox 确认保存异常")
    raise RuntimeError("V3 做 T PAPER 成交 outbox 确认保存失败")

  async def _replay_pending_t_trade_paper_fill_facts(
    self,
    runtime: StrategyRuntime,
  ) -> None:
    if runtime.context.mode != StrategyRunMode.PAPER:
      return
    pending_loader = getattr(
      runtime.state_manager,
      "pending_t_trade_paper_fill_facts",
      None,
    )
    if not callable(pending_loader):
      raise RuntimeError("V3 做 T PAPER 运行缺少成交 durable outbox")
    pending = [dict(fact) for fact in list(pending_loader() or [])]
    if not pending:
      return
    restored: List[
      tuple[Mapping[str, Any], TradeRecord, Optional[bool], Optional[int]]
    ] = []
    for fact in pending:
      trade, entry_complete, entry_target_volume = (
        self._trade_from_t_trade_paper_fill_fact(runtime, fact)
      )
      restored.append((fact, trade, entry_complete, entry_target_volume))
    # JSON/JSONB object member order is not a causal guarantee.  Keep each
    # candidate's entry fills ahead of its exits, then retain simulator time and
    # the stable fact key as deterministic tie-breakers.  A lone exit remains
    # valid when its entry was already acknowledged in an earlier checkpoint.
    restored.sort(
      key=lambda item: (
        str(item[1].metadata.get("candidate_id") or ""),
        0
        if str(item[1].metadata.get("t_trade_role") or "").upper() == "ENTRY"
        else 1,
        time_utils.to_utc(item[1].trade_time).timestamp(),
        str(item[0].get("fact_key") or ""),
      )
    )
    acknowledged: List[Mapping[str, Any]] = []
    for fact, trade, entry_complete, entry_target_volume in restored:
      recorded = await self._record_t_trade_candidate_fill(
        runtime,
        trade,
        durable_event=False,
        entry_complete=entry_complete,
        entry_target_volume=entry_target_volume,
        strict=True,
      )
      if not recorded:
        raise RuntimeError("V3 做 T PAPER 成交结果尚未收敛")
      acknowledged.append(fact)
    await self._acknowledge_t_trade_paper_fill_facts(runtime, acknowledged)

  async def _record_t_trade_candidate_fill(
    self,
    runtime: StrategyRuntime,
    trade: Any,
    *,
    durable_event: bool,
    entry_complete: Optional[bool] = None,
    entry_target_volume: Optional[int] = None,
    strict: bool = False,
  ) -> bool:
    facade = self.candidate_outcome_facade
    metadata = dict(getattr(trade, "metadata", {}) or {})
    if (
      not metadata.get("candidate_id")
      or runtime.context.mode
      not in {
        StrategyRunMode.BACKTEST,
        StrategyRunMode.PAPER,
        StrategyRunMode.LIVE,
      }
      or (runtime.context.mode == StrategyRunMode.LIVE and not durable_event)
    ):
      return True
    if facade is None:
      if strict:
        raise RuntimeError("做 T 候选结果持久化边界不可用")
      self.logger.error(
        "做 T 候选结果持久化边界不可用: run_id=%s trade_id=%s",
        runtime.run_id,
        getattr(trade, "trade_id", None),
      )
      return False
    if (
      runtime.context.mode in {StrategyRunMode.BACKTEST, StrategyRunMode.PAPER}
      and str(metadata.get("t_trade_role") or "").lower() == "entry"
      and entry_complete is None
      and entry_target_volume is None
    ):
      order = await runtime.broker.get_order(str(getattr(trade, "order_id", "") or ""))
      requested_volume = int(getattr(getattr(order, "request", None), "volume", 0) or 0)
      entry_target_volume = requested_volume or None
      entry_complete = bool(
        order is not None
        and order.status == OrderStatus.FILLED
        and requested_volume > 0
        and int(getattr(order, "filled_volume", 0) or 0) >= requested_volume
      )
    simulated_fee = (
      float(getattr(trade, "commission", 0.0) or 0.0)
      if runtime.context.mode in {StrategyRunMode.BACKTEST, StrategyRunMode.PAPER}
      else None
    )
    try:
      state = await facade.record_trade_fact(
        strategy_run_id=runtime.run_id,
        trade=trade,
        entry_complete=entry_complete,
        authoritative_fee=simulated_fee,
        fee_is_authoritative=runtime.context.mode
        in {StrategyRunMode.BACKTEST, StrategyRunMode.PAPER},
        entry_target_volume=entry_target_volume,
      )
      if state is not None:
        if self._candidate_outcome_state_is_active(state):
          self._candidate_outcome_activity[
            (runtime.run_id, state.definition.instrument_code)
          ] = True
        return True
      elif runtime.context.mode == StrategyRunMode.LIVE:
        self._candidate_outcome_reconciled_runs.discard(runtime.run_id)
        self._candidate_outcome_repair_attempts.pop(runtime.run_id, None)
        self._candidate_outcome_repair_retry_at_ms.pop(runtime.run_id, None)
        self.logger.error(
          "做 T 候选结果成交缺少 seed，等待从 APPLIED 成交真源修复: "
          "run_id=%s trade_id=%s",
          runtime.run_id,
          getattr(trade, "trade_id", None),
        )
      else:
        self.logger.error(
          "做 T 候选结果成交缺少 seed，保留 PAPER outbox 等待重放: "
          "run_id=%s trade_id=%s",
          runtime.run_id,
          getattr(trade, "trade_id", None),
        )
      if strict:
        raise RuntimeError("做 T 候选结果成交缺少 MATERIAL seed")
      return False
    except Exception:
      self._candidate_outcome_reconciled_runs.discard(runtime.run_id)
      self._candidate_outcome_repair_attempts.pop(runtime.run_id, None)
      self._candidate_outcome_repair_retry_at_ms.pop(runtime.run_id, None)
      self.logger.exception(
        "做 T 候选结果成交归集失败: run_id=%s trade_id=%s",
        runtime.run_id,
        getattr(trade, "trade_id", None),
      )
      if strict or self._requires_replay_event_integrity(runtime):
        runtime.status = ExecutionStatus.ERROR
        runtime.error_message = "T_TRADE_CANDIDATE_OUTCOME_FILL_FAILED"
        raise
      return False

  async def _finalize_t_trade_candidate_outcomes(
    self,
    runtime: StrategyRuntime,
  ) -> None:
    facade = self.candidate_outcome_facade
    if facade is None or not self._uses_t_trade_opportunity_runtime(runtime):
      return
    finalized_at = self._runtime_now(runtime)
    finalized_at_ms = int(time_utils.to_utc(finalized_at).timestamp() * 1000)
    try:
      await facade.finalize_run(
        strategy_run_id=runtime.run_id,
        finalized_at_ms=finalized_at_ms,
      )
      for key in list(self._candidate_outcome_activity):
        if key[0] == runtime.run_id:
          self._candidate_outcome_activity.pop(key, None)
      self._candidate_outcome_reconciled_runs.discard(runtime.run_id)
      self._candidate_outcome_repair_attempts.pop(runtime.run_id, None)
      self._candidate_outcome_repair_retry_at_ms.pop(runtime.run_id, None)
    except Exception:
      self.logger.exception(
        "做 T 候选结果终态归集失败: run_id=%s",
        runtime.run_id,
      )
      if self._requires_replay_event_integrity(runtime):
        runtime.status = ExecutionStatus.ERROR
        runtime.error_message = "T_TRADE_CANDIDATE_OUTCOME_FINALIZE_FAILED"
        raise

  @staticmethod
  def _candidate_outcome_state_is_active(state: Any) -> bool:
    status = str(getattr(getattr(state, "status", None), "value", ""))
    post_fill_status = str(
      getattr(getattr(getattr(state, "post_fill", None), "status", None), "value", "")
    )
    return status == "OBSERVING" or post_fill_status == "OBSERVING"

  async def _notify_t_trade_opportunity_update(
    self,
    runtime: StrategyRuntime,
    *,
    instrument_code: str,
    source_time_ms: int,
    events: List[Dict[str, Any]],
    immediate: Optional[bool] = None,
  ) -> None:
    """Wake clients only after the corresponding RuntimeState is durable."""

    if runtime.context.mode == StrategyRunMode.BACKTEST:
      return
    notify = getattr(self.opportunity_update_service, "notify_opportunity", None)
    account_id = str(runtime.context.parameters.get("account_id") or "").strip()
    normalized_code = str(instrument_code or "").strip().upper()
    if not account_id or not normalized_code or not callable(notify):
      return
    normalized_source_time_ms = int(source_time_ms or 0)
    if normalized_source_time_ms <= 0:
      normalized_source_time_ms = max(
        (int(event.get("evaluated_at_ms", 0) or 0) for event in events),
        default=0,
      )
    state_version = 0
    state: Dict[str, Any] = {}
    opportunity: Dict[str, Any] = {}
    if runtime.strategy is not None:
      raw_states = runtime.strategy.state.get("instrument_states", {})
      states = dict(raw_states) if isinstance(raw_states, Mapping) else {}
      raw_state = states.get(normalized_code)
      state = dict(raw_state) if isinstance(raw_state, Mapping) else {}
      if not state and hasattr(runtime.strategy.state, "to_dict"):
        state = dict(runtime.strategy.state.to_dict())
      opportunity = dict(state.get("opportunity") or {})
      state_version = int(opportunity.get("state_version", 0) or 0)
    signal_snapshot = opportunity.get("latest_evaluation")
    if not isinstance(signal_snapshot, Mapping):
      signal_snapshot = next(
        (
          event.get("signal_snapshot")
          for event in reversed(events)
          if isinstance(event.get("signal_snapshot"), Mapping)
        ),
        None,
      )
    if not isinstance(signal_snapshot, Mapping):
      self.logger.warning(
        "做 T 机会状态已落盘但缺少可投影快照: run_id=%s instrument=%s",
        runtime.run_id,
        normalized_code,
      )
      return
    is_material = (
      bool(immediate)
      if immediate is not None
      else any(
        str(event.get("record_kind") or "").upper() == "MATERIAL" for event in events
      )
    )
    version = (
      f"v3:{runtime.run_id}:{normalized_code}:"
      f"{normalized_source_time_ms}:{state_version}"
    )
    labels = self._t_trade_observability_labels(events)
    published = False
    try:
      publish_result = await notify(
        account_id=account_id,
        strategy_run_id=runtime.run_id,
        instrument_code=normalized_code,
        version=version,
        immediate=is_material,
        session_patch={
          "signal_snapshot": dict(signal_snapshot),
          "pending_entry_intent_id": (
            str(state.get("pending_entry_intent_id") or "") or None
          ),
          "entry_order_status": str(state.get("entry_order_status") or ""),
        },
      )
      published = publish_result is not False
    except Exception as exc:
      self.logger.warning(
        "做 T 机会状态已落盘但更新通知失败: run_id=%s instrument=%s error=%s",
        runtime.run_id,
        normalized_code,
        exc,
      )
    finally:
      observed_at_ms = int(time_utils.now().timestamp() * 1000)
      self.opportunity_observability.record_projection(
        lag_seconds=(
          max(0, observed_at_ms - normalized_source_time_ms) / 1000.0
          if normalized_source_time_ms > 0
          else 0.0
        ),
        published=published,
        coalesced=not is_material,
        **labels,
      )

  @staticmethod
  def _is_v3_t_trade_manual_intent(intent: TradeIntent) -> bool:
    metadata = dict(intent.metadata or {})
    try:
      schema_version = int(metadata.get("opportunity_schema_version") or 0)
    except (TypeError, ValueError, OverflowError):
      schema_version = 0
    return bool(
      intent.direction == TradeIntentDirection.BUY
      and intent.execution_mode == TradeIntentExecutionMode.MANUAL_CONFIRM
      and str(metadata.get("t_trade_role") or "").lower() == "entry"
      and schema_version >= 3
    )

  async def _process_t_trade_opportunity_output(
    self,
    runtime: StrategyRuntime,
    output: StrategyOutput,
    *,
    opportunity_events: List[Dict[str, Any]],
    input_snapshot: Optional[StrategyInput],
  ) -> None:
    """Commit V3 candidate truth before any manual approval is observable."""

    intents = list(output.trade_intents or [])
    v3_manual_intents = [
      intent for intent in intents if self._is_v3_t_trade_manual_intent(intent)
    ]
    source_time_ms = (
      int(input_snapshot.market_data_context.source_time_ms)
      if input_snapshot is not None
      else 0
    )
    instrument_code = (
      str(input_snapshot.instrument_code or "").strip().upper()
      if input_snapshot is not None
      else str(opportunity_events[0].get("instrument_code") or "").strip().upper()
    )
    observability_labels = self._t_trade_observability_labels(opportunity_events)

    if len(v3_manual_intents) > 1:
      self._reject_t_trade_opportunity_output(
        runtime,
        instrument_code=instrument_code,
        source_time_ms=source_time_ms,
        intents=intents,
        code="T_TRADE_MULTIPLE_CANDIDATE_INTENTS",
        message="同一机会评估最多允许一个 V3 入场意图",
      )
      return

    if not self._accepts_non_durable_output(runtime):
      self._reject_t_trade_opportunity_output(
        runtime,
        instrument_code=instrument_code,
        source_time_ms=source_time_ms,
        intents=intents,
        code="RUNTIME_NOT_RUNNING",
        message="策略运行不在 RUNNING 状态，机会输出未进入持久化链",
      )
      return
    if runtime.state_manager is None or runtime.strategy is None:
      self._reject_t_trade_opportunity_output(
        runtime,
        instrument_code=instrument_code,
        source_time_ms=source_time_ms,
        intents=intents,
        code="T_TRADE_RUNTIME_STATE_UNAVAILABLE",
        message="做 T 机会状态持久化边界不可用",
      )
      return
    deferred_diagnostic_batch = self._is_deferred_checkpoint_evaluation_output(
      runtime,
      output,
      intents=intents,
      opportunity_events=opportunity_events,
    )
    immediate_actionable_material = bool(
      not deferred_diagnostic_batch
      and self._has_immediate_actionable_t_trade_output(
        output,
        intents=intents,
      )
    )
    if immediate_actionable_material and runtime._checkpoint_diagnostic_summaries:
      # The actionable candidate/intent is durable immediately.  Its prior
      # ordinary diagnostics remain hot until their official session/day seal,
      # but must be frozen as an earlier segment so later diagnostics cannot
      # collapse across this durable fact.
      boundary_event_key = next(
        (
          str(event.get("event_key") or "").strip()
          for event in opportunity_events
          if str(event.get("record_kind") or "").upper() == "MATERIAL"
          and str(event.get("event_key") or "").strip()
        ),
        "",
      )
      if not boundary_event_key:
        boundary_event_key = next(
          (
            f"INTENT:{intent.intent_id}"
            for intent in intents
            if str(intent.intent_id or "").strip()
          ),
          f"ACTION:{instrument_code}:{source_time_ms}",
        )
      self._freeze_checkpoint_diagnostic_segments(
        runtime,
        instrument_codes=[instrument_code],
        boundary_event_key=boundary_event_key,
        boundary_kind="IMMEDIATE_ACTION",
      )
    if immediate_actionable_material:
      try:
        await self._replay_pending_actionable_t_trade_material_events(runtime)
      except Exception as exc:
        cas_conflict = (
          str(
            getattr(runtime.state_manager, "last_snapshot_failure_code", "") or ""
          )
          == "CAS_CONFLICT"
        )
        if cas_conflict:
          runtime.status = ExecutionStatus.ERROR
          runtime.error_message = "T_TRADE_RUNTIME_STATE_CAS_CONFLICT"
        self._reject_t_trade_opportunity_output(
          runtime,
          instrument_code=instrument_code,
          source_time_ms=source_time_ms,
          intents=intents,
          code=(
            "T_TRADE_RUNTIME_STATE_CAS_CONFLICT"
            if cas_conflict
            else "T_TRADE_MATERIAL_OUTBOX_REPLAY_FAILED"
          ),
          message=f"做 T 待补审计事件尚未收敛: {exc}",
        )
        return
    reconciliation_failure = self._runtime_state_reconciliation_failure(runtime)
    if reconciliation_failure is not None and intents:
      self._reject_t_trade_opportunity_output(
        runtime,
        instrument_code=instrument_code,
        source_time_ms=source_time_ms,
        intents=intents,
        code=reconciliation_failure[0],
        message=reconciliation_failure[1],
      )
      return
    continuity_failure = self._runtime_market_continuity_failure(
      runtime,
      instrument_code,
    )
    if continuity_failure is not None and intents:
      self._reject_t_trade_opportunity_output(
        runtime,
        instrument_code=instrument_code,
        source_time_ms=source_time_ms,
        intents=intents,
        code=continuity_failure[0],
        message=continuity_failure[1],
      )
      return
    if v3_manual_intents and not any(
      str(event.get("record_kind") or "").upper() == "MATERIAL"
      for event in opportunity_events
    ):
      self._reject_t_trade_opportunity_output(
        runtime,
        instrument_code=instrument_code,
        source_time_ms=source_time_ms,
        intents=intents,
        code="T_TRADE_CANDIDATE_EVALUATION_REQUIRED",
        message="V3 待确认候选缺少 MATERIAL 评估证据",
      )
      return

    if runtime.context.mode == StrategyRunMode.BACKTEST:
      created_at = self._runtime_now(runtime)
      for intent in intents:
        intent.created_at = created_at

    checkpoint_failure_recorded = False
    try:
      if output.runtime_state_patch:
        self._apply_runtime_state_patch(
          runtime,
          output.runtime_state_patch,
          stage_actionable_material_events=immediate_actionable_material,
        )
      if output.exit_plan_commands:
        for command in output.exit_plan_commands:
          runtime.exit_plan_book.apply_command(command)
        self._persist_exit_plan_book(runtime)
      # Keep the StrategyInput -> StrategyOutput audit in the same durable
      # transaction as this causally-required RuntimeState CAS.  The runtime
      # manager keeps the immutable trace pending through a failed or
      # commit-unknown snapshot, so it cannot be dropped merely to reduce
      # pressure-run database fan-out.
      self._record_strategy_output_trace(runtime, output, input_snapshot)
      if deferred_diagnostic_batch:
        drain = getattr(
          runtime.state_manager,
          "drain_strategy_state_changes",
          None,
        )
        if not callable(drain):
          raise RuntimeError("运行检查点缺少策略状态 drain 边界")
        # Ordinary diagnostic ticks only need ordered in-memory staging.  A
        # complete strategy snapshot is captured once at their explicit
        # session/day/terminal checkpoint, not for every hot output.
        if not await drain(capture_state=False):
          raise RuntimeError("T_TRADE_DIAGNOSTIC_BATCH_STATE_DRAIN_FAILED")
        # No generic checkpoint or outbox write occurs on a hot diagnostic
        # Tick.  The session/day coordinator transfers this bounded summary to
        # the durable outbox immediately before its proven seal.
        self._defer_checkpoint_diagnostics(runtime, opportunity_events)
        return
      if not await runtime.state_manager.checkpoint_strategy_state_changes():
        checkpoint_code = str(
          getattr(runtime.state_manager, "last_snapshot_failure_code", "")
          or "CHECKPOINT_REJECTED"
        )
        self.opportunity_observability.record_operation(
          "runtime_state_checkpoint_failures_total",
          detail=checkpoint_code,
          **observability_labels,
        )
        if checkpoint_code == "CAS_CONFLICT":
          self.opportunity_observability.record_operation(
            "runtime_state_cas_conflicts_total",
            detail="CAS_CONFLICT",
            **observability_labels,
          )
        checkpoint_failure_recorded = True
        raise RuntimeError("T_TRADE_STATE_CHECKPOINT_FAILED")
      self.opportunity_observability.record_operation(
        "runtime_state_checkpoints_total",
        detail="SUCCESS",
        **observability_labels,
      )
    except Exception as exc:
      if (
        str(
          getattr(runtime.state_manager, "last_snapshot_failure_code", "") or ""
        )
        == "CAS_CONFLICT"
      ):
        # RuntimeStateManager has adopted the concurrent winner in full.  Any
        # compensation derived from this stale StrategyOutput would be another
        # write based on the losing snapshot, so stop this generation without
        # producing or checkpointing a second patch.
        runtime.status = ExecutionStatus.ERROR
        runtime.error_message = "T_TRADE_RUNTIME_STATE_CAS_CONFLICT"
        self._reject_t_trade_opportunity_output(
          runtime,
          instrument_code=instrument_code,
          source_time_ms=source_time_ms,
          intents=intents,
          code="T_TRADE_RUNTIME_STATE_CAS_CONFLICT",
          message="做 T 机会状态发生并发版本冲突；当前运行已停止并等待权威重启",
        )
        return
      if not checkpoint_failure_recorded:
        self.opportunity_observability.record_operation(
          "runtime_state_checkpoint_failures_total",
          detail=exc.__class__.__name__,
          **observability_labels,
        )
      compensations = await self._compensate_failed_t_trade_candidates(
        runtime,
        v3_manual_intents,
        code="T_TRADE_STATE_CHECKPOINT_FAILED",
        source_time_ms=source_time_ms,
      )
      self._reject_t_trade_opportunity_output(
        runtime,
        instrument_code=instrument_code,
        source_time_ms=source_time_ms,
        intents=intents,
        code="T_TRADE_STATE_CHECKPOINT_FAILED",
        message=f"做 T 机会状态 CAS 检查点失败: {exc}",
      )
      runtime._t_trade_opportunity_failures[instrument_code]["compensation"] = (
        compensations
      )
      return

    account_id = str(runtime.context.parameters.get("account_id") or "").strip()
    if not account_id:
      compensations = await self._compensate_failed_t_trade_candidates(
        runtime,
        v3_manual_intents,
        code="T_TRADE_ACCOUNT_REQUIRED",
        source_time_ms=source_time_ms,
      )
      self._reject_t_trade_opportunity_output(
        runtime,
        instrument_code=instrument_code,
        source_time_ms=source_time_ms,
        intents=intents,
        code="T_TRADE_ACCOUNT_REQUIRED",
        message="做 T 机会评估缺少唯一证券账户绑定",
      )
      runtime._t_trade_opportunity_failures[instrument_code]["compensation"] = (
        compensations
      )
      return
    material_events_to_ack: List[Mapping[str, Any]] = []
    try:
      for event in opportunity_events:
        await self._materialize_t_trade_evaluation_with_retry(
          event=event,
          account_id=account_id,
          strategy_run_id=runtime.run_id,
          labels=observability_labels,
          cas_committed=True,
        )
        seeded = await self._seed_t_trade_candidate_outcome(
          runtime,
          account_id=account_id,
          event=event,
        )
        if seeded and str(event.get("record_kind") or "").upper() == "MATERIAL":
          material_events_to_ack.append(event)
    except Exception as exc:
      compensations = await self._compensate_failed_t_trade_candidates(
        runtime,
        v3_manual_intents,
        code="T_TRADE_EVALUATION_PERSIST_FAILED",
        source_time_ms=source_time_ms,
      )
      self._reject_t_trade_opportunity_output(
        runtime,
        instrument_code=instrument_code,
        source_time_ms=source_time_ms,
        intents=intents,
        code="T_TRADE_EVALUATION_PERSIST_FAILED",
        message=f"做 T 机会评估物化失败: {exc}",
      )
      runtime._t_trade_opportunity_failures[instrument_code]["compensation"] = (
        compensations
      )
      return
    try:
      await self._acknowledge_t_trade_actionable_material_events(
        runtime,
        material_events_to_ack,
      )
    except Exception as exc:
      if (
        str(
          getattr(runtime.state_manager, "last_snapshot_failure_code", "") or ""
        )
        == "CAS_CONFLICT"
      ):
        runtime.status = ExecutionStatus.ERROR
        runtime.error_message = "T_TRADE_RUNTIME_STATE_CAS_CONFLICT"
        self._reject_t_trade_opportunity_output(
          runtime,
          instrument_code=instrument_code,
          source_time_ms=source_time_ms,
          intents=intents,
          code="T_TRADE_RUNTIME_STATE_CAS_CONFLICT",
          message="做 T MATERIAL outbox 确认发生版本冲突；当前运行已停止",
        )
        return
      # The evaluation is already durable and the prior checkpoint still
      # contains the outbox item.  A restart will replay it idempotently, so an
      # acknowledgement write failure must not create a ghost candidate.
      self.logger.warning(
        "做 T MATERIAL outbox 确认暂未保存，将在后续或重启重放: "
        "run_id=%s instrument=%s error=%s",
        runtime.run_id,
        instrument_code,
        exc,
      )

    # V3 entry candidates have one account-scoped linearization point.  The
    # helper keeps the runtime approval lock through the strict intent row,
    # candidate LATCHED->AWAITING CAS, durable status, and pending-approval
    # visibility.  Compensation is deliberately performed after it releases
    # both locks so an OrderStateEvent/MATERIAL path cannot re-enter them.
    if v3_manual_intents:
      transition_failure, persisted_intents = (
        await self._persist_v3_candidate_transition_under_locks(
          runtime,
          intents=intents,
          v3_manual_intents=v3_manual_intents,
          output=output,
          input_snapshot=input_snapshot,
          source_time_ms=source_time_ms,
        )
      )
      if transition_failure is not None:
        failure_code, failure_message = transition_failure
        if failure_code == "T_TRADE_RUNTIME_STATE_CAS_CONFLICT":
          runtime.status = ExecutionStatus.ERROR
          runtime.error_message = "T_TRADE_RUNTIME_STATE_CAS_CONFLICT"
          self._reject_t_trade_opportunity_output(
            runtime,
            instrument_code=instrument_code,
            source_time_ms=source_time_ms,
            intents=intents,
            code=failure_code,
            message=failure_message,
          )
          return
        for persisted_intent in persisted_intents:
          await self._reject_persisted_t_trade_intent(
            runtime,
            persisted_intent,
            code=failure_code,
          )
        compensations = await self._compensate_failed_t_trade_candidates(
          runtime,
          v3_manual_intents,
          code=failure_code,
          source_time_ms=source_time_ms,
        )
        self._reject_t_trade_opportunity_output(
          runtime,
          instrument_code=instrument_code,
          source_time_ms=source_time_ms,
          intents=intents,
          code=failure_code,
          message=failure_message,
        )
        runtime._t_trade_opportunity_failures[instrument_code]["compensation"] = (
          compensations
        )
        return

      # Pending approvals are now visible while the approval lock was held;
      # only the client wake-up and non-V3 dispatch happen after release.
      await self._notify_t_trade_opportunity_update(
        runtime,
        instrument_code=instrument_code,
        source_time_ms=source_time_ms,
        events=opportunity_events,
      )
      v3_ids = {intent.intent_id for intent in v3_manual_intents}
      for intent in intents:
        if intent.intent_id in v3_ids:
          if (
            runtime.context.mode == StrategyRunMode.BACKTEST
            and runtime.context.parameters.get("auto_approve_manual_intents")
            and not runtime.context.parameters.get("limit_up_board_replay")
          ):
            result = await self.approve_trade_intent(
              runtime.run_id,
              intent.intent_id,
              approval_expectation=self._v3_t_trade_expectation_from_intent(intent),
            )
            self._runtime_log(
              runtime,
              "INFO" if result.get("success") else "WARNING",
              "回放测试自动确认 V3 做 T 候选: "
              f"intent_id={intent.intent_id}, result={result.get('code')}",
            )
          else:
            self._runtime_log(
              runtime,
              "INFO",
              "V3 做 T 候选已完成状态、评估和意图持久化，等待人工确认: "
              f"instrument={intent.instrument_code} intent_id={intent.intent_id}",
            )
          continue
        if intent.execution_mode == TradeIntentExecutionMode.MANUAL_CONFIRM:
          runtime.pending_approvals[intent.intent_id] = intent
        else:
          await self._process_trade_intent(runtime, intent)
      return

    strict_recorder = getattr(
      runtime.state_manager,
      "record_trade_intent_strict",
      None,
    )
    if intents and not callable(strict_recorder):
      compensations = await self._compensate_failed_t_trade_candidates(
        runtime,
        v3_manual_intents,
        code="T_TRADE_STRICT_INTENT_PERSISTENCE_UNAVAILABLE",
        source_time_ms=source_time_ms,
      )
      self._reject_t_trade_opportunity_output(
        runtime,
        instrument_code=instrument_code,
        source_time_ms=source_time_ms,
        intents=intents,
        code="T_TRADE_STRICT_INTENT_PERSISTENCE_UNAVAILABLE",
        message="做 T 候选缺少严格 TradeIntent 持久化边界",
      )
      runtime._t_trade_opportunity_failures[instrument_code]["compensation"] = (
        compensations
      )
      return
    persisted_intents: List[TradeIntent] = []
    try:
      for intent in intents:
        status = (
          "PENDING"
          if intent.intent_id
          in {candidate.intent_id for candidate in v3_manual_intents}
          else "AWAITING_APPROVAL"
          if intent.execution_mode == TradeIntentExecutionMode.MANUAL_CONFIRM
          else "PENDING"
        )
        await strict_recorder(intent, status=status)
        persisted_intents.append(intent)
    except Exception as exc:
      for persisted_intent in persisted_intents:
        await self._reject_persisted_t_trade_intent(
          runtime,
          persisted_intent,
          code="T_TRADE_INTENT_PERSIST_FAILED",
        )
      compensations = await self._compensate_failed_t_trade_candidates(
        runtime,
        v3_manual_intents,
        code="T_TRADE_INTENT_PERSIST_FAILED",
        source_time_ms=source_time_ms,
      )
      self._reject_t_trade_opportunity_output(
        runtime,
        instrument_code=instrument_code,
        source_time_ms=source_time_ms,
        intents=intents,
        code="T_TRADE_INTENT_PERSIST_FAILED",
        message=f"做 T TradeIntent 持久化失败: {exc}",
      )
      runtime._t_trade_opportunity_failures[instrument_code]["compensation"] = (
        compensations
      )
      return

    if runtime.metrics:
      runtime.metrics.trade_intents_generated += len(intents)
    for intent in intents:
      runtime.strategy.record_trade_intent(intent)

    for intent in v3_manual_intents:
      failure = await self._mark_t_trade_candidate_awaiting_approval(
        runtime,
        intent,
        source_time_ms=source_time_ms,
      )
      if failure is not None:
        if failure[0] == "T_TRADE_RUNTIME_STATE_CAS_CONFLICT":
          # RuntimeStateManager has already adopted the authoritative winner.
          # The strategy object still represents the losing generation, so no
          # rejection, compensation, audit materialization or intent status
          # update may be derived from it.
          runtime.status = ExecutionStatus.ERROR
          runtime.error_message = "T_TRADE_RUNTIME_STATE_CAS_CONFLICT"
          self._reject_t_trade_opportunity_output(
            runtime,
            instrument_code=intent.instrument_code,
            source_time_ms=source_time_ms,
            intents=[intent],
            code=failure[0],
            message=failure[1],
          )
          return
        await self._reject_persisted_t_trade_intent(
          runtime,
          intent,
          code=failure[0],
        )
        compensation = await self._compensate_failed_t_trade_candidate(
          runtime,
          intent,
          code=failure[0],
          source_time_ms=source_time_ms,
        )
        self._reject_t_trade_opportunity_output(
          runtime,
          instrument_code=intent.instrument_code,
          source_time_ms=source_time_ms,
          intents=[intent],
          code=failure[0],
          message=failure[1],
        )
        runtime._t_trade_opportunity_failures[
          str(intent.instrument_code or "").strip().upper()
        ]["compensation"] = compensation
        return

    strict_status_updater = getattr(
      runtime.state_manager,
      "update_trade_intent_status_strict",
      None,
    )
    if v3_manual_intents and not callable(strict_status_updater):
      compensations: Dict[str, Dict[str, Any]] = {}
      for intent in v3_manual_intents:
        await self._reject_persisted_t_trade_intent(
          runtime,
          intent,
          code="T_TRADE_STRICT_INTENT_STATUS_UNAVAILABLE",
        )
        compensations[
          intent.intent_id
        ] = await self._compensate_failed_t_trade_candidate(
          runtime,
          intent,
          code="T_TRADE_STRICT_INTENT_STATUS_UNAVAILABLE",
          source_time_ms=source_time_ms,
        )
      self._reject_t_trade_opportunity_output(
        runtime,
        instrument_code=instrument_code,
        source_time_ms=source_time_ms,
        intents=v3_manual_intents,
        code="T_TRADE_STRICT_INTENT_STATUS_UNAVAILABLE",
        message="做 T 候选缺少严格的待确认状态持久化边界",
      )
      runtime._t_trade_opportunity_failures[instrument_code]["compensation"] = (
        compensations
      )
      return
    try:
      for intent in v3_manual_intents:
        await strict_status_updater(
          intent.intent_id,
          "AWAITING_APPROVAL",
          metadata=dict(intent.metadata or {}),
          notes="T_TRADE_CANDIDATE_AWAITING_DURABLE",
        )
    except Exception as exc:
      compensations = {}
      for intent in v3_manual_intents:
        await self._reject_persisted_t_trade_intent(
          runtime,
          intent,
          code="T_TRADE_INTENT_STATUS_PERSIST_FAILED",
        )
        compensations[
          intent.intent_id
        ] = await self._compensate_failed_t_trade_candidate(
          runtime,
          intent,
          code="T_TRADE_INTENT_STATUS_PERSIST_FAILED",
          source_time_ms=source_time_ms,
        )
      self._reject_t_trade_opportunity_output(
        runtime,
        instrument_code=instrument_code,
        source_time_ms=source_time_ms,
        intents=v3_manual_intents,
        code="T_TRADE_INTENT_STATUS_PERSIST_FAILED",
        message=f"做 T 候选待确认状态持久化失败: {exc}",
      )
      runtime._t_trade_opportunity_failures[instrument_code]["compensation"] = (
        compensations
      )
      return

    if not self._accepts_non_durable_output(runtime):
      v3_ids = {intent.intent_id for intent in v3_manual_intents}
      for intent in intents:
        if intent.intent_id in v3_ids:
          await self._reject_persisted_t_trade_intent(
            runtime,
            intent,
            code=f"RUNTIME_{runtime.status.value}",
          )
          await self._compensate_failed_t_trade_candidate(
            runtime,
            intent,
            code=f"RUNTIME_{runtime.status.value}",
            source_time_ms=source_time_ms,
          )
        else:
          await self._reject_intent_during_runtime_transition(runtime, intent)
      return

    v3_ids = {intent.intent_id for intent in v3_manual_intents}
    runtime._t_trade_opportunity_failures.pop(instrument_code, None)
    for intent in v3_manual_intents:
      runtime.pending_approvals[intent.intent_id] = intent
    await self._notify_t_trade_opportunity_update(
      runtime,
      instrument_code=instrument_code,
      source_time_ms=source_time_ms,
      events=opportunity_events,
    )
    for intent in intents:
      if intent.intent_id in v3_ids:
        if (
          runtime.context.mode == StrategyRunMode.BACKTEST
          and runtime.context.parameters.get("auto_approve_manual_intents")
          and not runtime.context.parameters.get("limit_up_board_replay")
        ):
          result = await self.approve_trade_intent(
            runtime.run_id,
            intent.intent_id,
            approval_expectation=self._v3_t_trade_expectation_from_intent(intent),
          )
          self._runtime_log(
            runtime,
            "INFO" if result.get("success") else "WARNING",
            "回放测试自动确认 V3 做 T 候选: "
            f"intent_id={intent.intent_id}, result={result.get('code')}",
          )
          continue
        self._runtime_log(
          runtime,
          "INFO",
          "V3 做 T 候选已完成状态、评估和意图持久化，等待人工确认: "
          f"instrument={intent.instrument_code} intent_id={intent.intent_id}",
        )
        continue
      if intent.execution_mode == TradeIntentExecutionMode.MANUAL_CONFIRM:
        runtime.pending_approvals[intent.intent_id] = intent
      else:
        await self._process_trade_intent(runtime, intent)

  async def _persist_v3_candidate_transition_under_locks(
    self,
    runtime: StrategyRuntime,
    *,
    intents: List[TradeIntent],
    v3_manual_intents: List[TradeIntent],
    output: StrategyOutput,
    input_snapshot: Optional[StrategyInput],
    source_time_ms: int,
  ) -> tuple[Optional[tuple[str, str]], List[TradeIntent]]:
    """Recheck and publish V3 candidates under account -> approval locks.

    The account lock is checked before its uncontended acquire because this
    method runs on the market-event loop. Global reconciliation may hold the
    lock while waiting for this runtime's event turn; in that case this path
    fails closed immediately. Approval remains held through every durable
    candidate transition.
    """

    account_id = str(runtime.context.parameters.get("account_id") or "").strip()
    coordination_lock = t_trade_account_coordination_lock(account_id)
    if not coordination_lock.try_acquire():
      return (
        (
          "T_TRADE_ACCOUNT_COORDINATION_IN_PROGRESS",
          "账户做 T 协调正在更新，候选已保守抑制，请等待下一次信号",
        ),
        [],
      )
    try:
      # The synchronous account try-acquire above prevents a market-event
      # deadlock with reconciliation.  No account waiter is ever created here.
      if runtime.approval_lock.locked():
        return (
          (
            "T_TRADE_APPROVAL_COORDINATION_IN_PROGRESS",
            "做 T 审批正在收敛，候选已保守抑制，请等待下一次信号",
          ),
          [],
        )
      await runtime.approval_lock.acquire()
      try:
        for intent in v3_manual_intents:
          gate = self._t_trade_intent_emission_context(
            runtime,
            intent.instrument_code,
            requested_amount=intent.target_amount,
            current_intent_id=intent.intent_id,
            check_coordination_lock=False,
          )
          if not gate.get("allowed"):
            blockers = self._t_trade_unique_blockers(gate.get("blockers"))
            return (
              (
                blockers[0] if blockers else "T_TRADE_INTENT_EMISSION_BLOCKED",
                "候选在严格意图持久化前未通过最新账户与标的门禁："
                + (", ".join(blockers) if blockers else "UNKNOWN"),
              ),
              [],
            )

        if not self._accepts_non_durable_output(runtime):
          return (
            (
              "RUNTIME_NOT_RUNNING",
              "策略运行不在 RUNNING 状态，候选未进入严格意图持久化",
            ),
            [],
          )
        strict_recorder = getattr(
          runtime.state_manager,
          "record_trade_intent_strict",
          None,
        )
        if not callable(strict_recorder):
          return (
            (
              "T_TRADE_STRICT_INTENT_PERSISTENCE_UNAVAILABLE",
              "做 T 候选缺少严格 TradeIntent 持久化边界",
            ),
            [],
          )
        strict_status_updater = getattr(
          runtime.state_manager,
          "update_trade_intent_status_strict",
          None,
        )
        if v3_manual_intents and not callable(strict_status_updater):
          return (
            (
              "T_TRADE_STRICT_INTENT_STATUS_UNAVAILABLE",
              "做 T 候选缺少严格的待确认状态持久化边界",
            ),
            [],
          )

        persisted: List[TradeIntent] = []
        try:
          for intent in intents:
            status = (
              "PENDING"
              if intent.intent_id in {candidate.intent_id for candidate in v3_manual_intents}
              else "AWAITING_APPROVAL"
              if intent.execution_mode == TradeIntentExecutionMode.MANUAL_CONFIRM
              else "PENDING"
            )
            await strict_recorder(intent, status=status)
            persisted.append(intent)
        except Exception as exc:
          return (
            (
              "T_TRADE_INTENT_PERSIST_FAILED",
              f"做 T TradeIntent 持久化失败: {exc}",
            ),
            persisted,
          )

        if runtime.metrics:
          runtime.metrics.trade_intents_generated += len(intents)
        for intent in intents:
          runtime.strategy.record_trade_intent(intent)

        for intent in v3_manual_intents:
          failure = await self._mark_t_trade_candidate_awaiting_approval(
            runtime,
            intent,
            source_time_ms=source_time_ms,
          )
          if failure is not None:
            return failure, persisted

        try:
          for intent in v3_manual_intents:
            await strict_status_updater(
              intent.intent_id,
              "AWAITING_APPROVAL",
              metadata=dict(intent.metadata or {}),
              notes="T_TRADE_CANDIDATE_AWAITING_DURABLE",
            )
        except Exception as exc:
          return (
            (
              "T_TRADE_INTENT_STATUS_PERSIST_FAILED",
              f"做 T 候选待确认状态持久化失败: {exc}",
            ),
            persisted,
          )

        if not self._accepts_non_durable_output(runtime):
          return (
            (
              f"RUNTIME_{runtime.status.value}",
              f"策略运行已进入 {runtime.status.value}，候选未开放确认",
            ),
            persisted,
          )
        runtime._t_trade_opportunity_failures.pop(
          str(v3_manual_intents[0].instrument_code or "").strip().upper(),
          None,
        )
        for intent in v3_manual_intents:
          runtime.pending_approvals[intent.intent_id] = intent
        return None, persisted
      finally:
        runtime.approval_lock.release()
    finally:
      coordination_lock.release()

  async def _mark_t_trade_candidate_awaiting_approval(
    self,
    runtime: StrategyRuntime,
    intent: TradeIntent,
    *,
    source_time_ms: int,
  ) -> Optional[tuple[str, str]]:
    hook = getattr(
      runtime.strategy,
      "mark_candidate_awaiting_approval",
      None,
    )
    candidate_id = str((intent.metadata or {}).get("candidate_id") or "").strip()
    if not callable(hook) or not candidate_id:
      return (
        "T_TRADE_CANDIDATE_AWAITING_HOOK_UNAVAILABLE",
        "V3 候选无法建立持久化的待确认关联",
      )
    try:
      result = hook(
        intent.instrument_code,
        candidate_id,
        intent.intent_id,
        source_time_ms=source_time_ms,
      )
      patch = await result if inspect.isawaitable(result) else result
      if patch is None:
        return (
          "T_TRADE_CANDIDATE_AWAITING_PATCH_MISSING",
          "V3 候选待确认关联未返回状态补丁",
        )
      self._apply_runtime_state_patch(runtime, patch)
      if not await runtime.state_manager.checkpoint_strategy_state_changes():
        if (
          str(
            getattr(runtime.state_manager, "last_snapshot_failure_code", "") or ""
          )
          == "CAS_CONFLICT"
        ):
          runtime.status = ExecutionStatus.ERROR
          runtime.error_message = "T_TRADE_RUNTIME_STATE_CAS_CONFLICT"
          return (
            "T_TRADE_RUNTIME_STATE_CAS_CONFLICT",
            "做 T 候选待确认状态发生并发版本冲突；当前运行已停止并等待权威重启",
          )
        return (
          "T_TRADE_CANDIDATE_AWAITING_CHECKPOINT_FAILED",
          "V3 候选待确认关联状态保存失败",
        )
    except Exception as exc:
      if (
        str(
          getattr(runtime.state_manager, "last_snapshot_failure_code", "") or ""
        )
        == "CAS_CONFLICT"
      ):
        runtime.status = ExecutionStatus.ERROR
        runtime.error_message = "T_TRADE_RUNTIME_STATE_CAS_CONFLICT"
        return (
          "T_TRADE_RUNTIME_STATE_CAS_CONFLICT",
          "做 T 候选待确认状态发生并发版本冲突；当前运行已停止并等待权威重启",
        )
      return (
        "T_TRADE_CANDIDATE_AWAITING_CHECKPOINT_FAILED",
        f"V3 候选待确认关联状态保存失败: {exc}",
      )
    account_id = str(runtime.context.parameters.get("account_id") or "").strip()
    linked_events = [
      dict(event)
      for event in list(getattr(patch, "append_events", []) or [])
      if isinstance(event, Mapping)
      and event.get("type") == T_TRADE_OPPORTUNITY_EVALUATION_EVENT
    ]
    try:
      acknowledged: List[Mapping[str, Any]] = []
      for event in linked_events:
        await self._materialize_t_trade_evaluation_with_retry(
          event=event,
          account_id=account_id,
          strategy_run_id=runtime.run_id,
          labels=self._t_trade_observability_labels([event]),
          cas_committed=True,
        )
        seeded = await self._seed_t_trade_candidate_outcome(
          runtime,
          account_id=account_id,
          event=event,
          strict=True,
        )
        if not seeded:
          raise RuntimeError("V3 做 T 候选结果初始化失败")
        acknowledged.append(event)
      await self._acknowledge_t_trade_actionable_material_events(runtime, acknowledged)
    except Exception as exc:
      if (
        str(
          getattr(runtime.state_manager, "last_snapshot_failure_code", "") or ""
        )
        == "CAS_CONFLICT"
      ):
        runtime.status = ExecutionStatus.ERROR
        runtime.error_message = "T_TRADE_RUNTIME_STATE_CAS_CONFLICT"
        return (
          "T_TRADE_RUNTIME_STATE_CAS_CONFLICT",
          "做 T MATERIAL 确认发生并发版本冲突；当前运行已停止并等待权威重启",
        )
      return (
        "T_TRADE_EVALUATION_PERSIST_FAILED",
        f"V3 候选意图关联评估物化失败: {exc}",
      )
    return None

  async def _reject_persisted_t_trade_intent(
    self,
    runtime: StrategyRuntime,
    intent: TradeIntent,
    *,
    code: str,
  ) -> None:
    updates = {
      "metadata": {**dict(intent.metadata or {}), "runtime_gate": code},
      "notes": code,
    }
    strict_updater = getattr(
      runtime.state_manager,
      "update_trade_intent_status_strict",
      None,
    )
    try:
      if callable(strict_updater):
        await strict_updater(intent.intent_id, "REJECTED", **updates)
      else:
        await runtime.state_manager.update_trade_intent_status(
          intent.intent_id,
          "REJECTED",
          **updates,
        )
    except Exception:
      # The initial V3 record remains PENDING, never AWAITING_APPROVAL, so a
      # failed rejection update still cannot be restored or exposed for approval.
      self.logger.exception(
        "做 T 候选持久化拒绝状态收敛失败: run_id=%s intent_id=%s",
        runtime.run_id,
        intent.intent_id,
      )

  async def _compensate_failed_t_trade_candidate(
    self,
    runtime: StrategyRuntime,
    intent: TradeIntent,
    *,
    code: str,
    source_time_ms: int,
  ) -> Dict[str, Any]:
    """Suppress the exact pre-routing candidate after a guarded-chain failure."""

    result: Dict[str, Any] = {
      "state_compensated": False,
      "checkpointed": False,
      "evaluation_materialized": False,
    }
    runtime.pending_approvals.pop(intent.intent_id, None)
    candidate_id = str((intent.metadata or {}).get("candidate_id") or "").strip()
    candidate_status, current_candidate_id, _pending_intent_id = (
      self._t_trade_candidate_projection(runtime, intent)
    )
    if not candidate_id or current_candidate_id != candidate_id:
      result["error"] = "CANDIDATE_IDENTITY_MISMATCH"
      return result
    if candidate_status not in {"LATCHED", "AWAITING_APPROVAL"}:
      result["state_compensated"] = candidate_status in {
        "NONE",
        "SUPPRESSED",
        "REARMING",
      }
      if not result["state_compensated"]:
        result["error"] = f"CANDIDATE_STATUS_UNEXPECTED: {candidate_status}"
      return result
    timestamp = (
      datetime.fromtimestamp(source_time_ms / 1000, timezone.utc)
      if source_time_ms > 0
      else None
    )
    try:
      patch = await self._notify_strategy_order(
        runtime,
        OrderStateEvent(
          order_id=None,
          status="REJECTED",
          filled_volume=0,
          error_message="做 T 候选持久化链失败，未创建券商委托",
          timestamp=timestamp,
          metadata={
            **dict(intent.metadata or {}),
            "intent_id": intent.intent_id,
            "instrument_code": intent.instrument_code,
            "approval_reason": code,
            "source_time_ms": source_time_ms,
          },
        ),
        raise_on_error=True,
      )
    except Exception as exc:
      result["error"] = f"ORDER_COMPENSATION_FAILED: {exc}"
      return result

    result["state_compensated"] = self._t_trade_candidate_is_suppressed(
      runtime,
      intent,
    )
    if not result["state_compensated"]:
      result["error"] = "CANDIDATE_REMAINS_ACTIONABLE"
      return result
    checkpoint = getattr(
      runtime.state_manager,
      "checkpoint_strategy_state_changes",
      None,
    )
    try:
      result["checkpointed"] = bool(callable(checkpoint) and await checkpoint())
    except Exception as exc:
      result["error"] = f"SUPPRESSION_CHECKPOINT_FAILED: {exc}"
      return result
    if not result["checkpointed"]:
      result["error"] = "SUPPRESSION_CHECKPOINT_FAILED"
      return result

    suppression_events = [
      dict(event)
      for event in list(getattr(patch, "append_events", []) or [])
      if isinstance(event, Mapping)
      and event.get("type") == T_TRADE_OPPORTUNITY_EVALUATION_EVENT
      and str(event.get("record_kind") or "").upper() == "MATERIAL"
      and str(event.get("event_type") or "").upper() == "CANDIDATE_SUPPRESSED"
    ]
    if not suppression_events:
      # The exact candidate is already suppressed. State is authoritative and
      # no duplicate material event is necessary for an idempotent retry.
      result["evaluation_materialized"] = True
      await self._notify_t_trade_opportunity_update(
        runtime,
        instrument_code=intent.instrument_code,
        source_time_ms=source_time_ms,
        events=[],
        immediate=True,
      )
      return result
    try:
      account_id = str(runtime.context.parameters.get("account_id") or "").strip()
      acknowledged: List[Mapping[str, Any]] = []
      for event in suppression_events:
        await self._materialize_t_trade_evaluation_with_retry(
          event=event,
          account_id=account_id,
          strategy_run_id=runtime.run_id,
          labels=self._t_trade_observability_labels([event]),
          cas_committed=True,
        )
        seeded = await self._seed_t_trade_candidate_outcome(
          runtime,
          account_id=account_id,
          event=event,
          strict=True,
        )
        if not seeded:
          raise RuntimeError("V3 做 T 抑制事件候选结果初始化失败")
        acknowledged.append(event)
      await self._acknowledge_t_trade_actionable_material_events(runtime, acknowledged)
      result["evaluation_materialized"] = True
    except Exception as exc:
      # The durable suppressed state and its outbox item remain fail-closed;
      # startup or the next evaluation replays the same stable event key.
      result["error"] = f"SUPPRESSION_EVALUATION_FAILED: {exc}"
      self.logger.exception(
        "做 T 候选抑制评估物化失败，但状态已保守落盘: run_id=%s intent_id=%s",
        runtime.run_id,
        intent.intent_id,
      )
    await self._notify_t_trade_opportunity_update(
      runtime,
      instrument_code=intent.instrument_code,
      source_time_ms=source_time_ms,
      events=(suppression_events if result["evaluation_materialized"] else []),
      immediate=True,
    )
    return result

  async def _compensate_failed_t_trade_candidates(
    self,
    runtime: StrategyRuntime,
    intents: List[TradeIntent],
    *,
    code: str,
    source_time_ms: int,
  ) -> Dict[str, Dict[str, Any]]:
    return {
      intent.intent_id: await self._compensate_failed_t_trade_candidate(
        runtime,
        intent,
        code=code,
        source_time_ms=source_time_ms,
      )
      for intent in intents
    }

  @staticmethod
  def _t_trade_candidate_projection(
    runtime: StrategyRuntime,
    intent: TradeIntent,
  ) -> tuple[str, str, str]:
    strategy = runtime.strategy
    if strategy is None:
      return "", "", ""
    raw_states = strategy.state.get("instrument_states", {})
    states = dict(raw_states) if isinstance(raw_states, Mapping) else {}
    raw_state = states.get(intent.instrument_code) or states.get(
      str(intent.instrument_code or "").strip().upper()
    )
    state = dict(raw_state) if isinstance(raw_state, Mapping) else {}
    if not state:
      # Lightweight strategy protocol fakes may expose the same keys at root.
      state = strategy.state.to_dict() if hasattr(strategy.state, "to_dict") else {}
    opportunity = dict(state.get("opportunity") or {})
    candidate = dict(opportunity.get("candidate") or {})
    latest_evaluation = dict(opportunity.get("latest_evaluation") or {})
    awaiting = dict(state.get("awaiting") or {})
    return (
      str(
        opportunity.get("candidate_status") or state.get("candidate_status") or ""
      ).upper(),
      str(
        candidate.get("candidate_id")
        or latest_evaluation.get("candidate_id")
        or opportunity.get("candidate_id")
        or awaiting.get("candidate_id")
        or ""
      ),
      str(state.get("pending_entry_intent_id") or ""),
    )

  @classmethod
  def _t_trade_candidate_is_suppressed(
    cls,
    runtime: StrategyRuntime,
    intent: TradeIntent,
  ) -> bool:
    candidate_status, current_candidate_id, pending_intent_id = (
      cls._t_trade_candidate_projection(runtime, intent)
    )
    expected_candidate_id = str(
      (intent.metadata or {}).get("candidate_id") or ""
    ).strip()
    return bool(
      current_candidate_id == expected_candidate_id
      and candidate_status in {"NONE", "SUPPRESSED", "REARMING"}
      and pending_intent_id != intent.intent_id
    )

  def _reject_t_trade_opportunity_output(
    self,
    runtime: StrategyRuntime,
    *,
    instrument_code: str,
    source_time_ms: int,
    intents: List[TradeIntent],
    code: str,
    message: str,
  ) -> None:
    normalized_code = str(instrument_code or "").strip().upper()
    runtime._t_trade_opportunity_failures[normalized_code] = {
      "code": str(code),
      "message": str(message),
      "source_time_ms": int(source_time_ms or 0),
      "intent_ids": [intent.intent_id for intent in intents],
    }
    for intent in intents:
      runtime.pending_approvals.pop(intent.intent_id, None)
    if runtime.metrics:
      runtime.metrics.error_count += 1
    self._runtime_log(
      runtime,
      "ERROR",
      "做 T 机会输出已保守拒绝: "
      f"code={code} instrument={normalized_code} message={message}",
    )

  async def approve_trade_intent(
    self,
    run_id: str,
    intent_id: str,
    *,
    approval_expectation: Optional[Mapping[str, Any]] = None,
    approval_audit: Optional[Mapping[str, Any]] = None,
  ) -> Dict[str, Any]:
    """Approve one manual-confirm intent after an in-lock fail-closed recheck."""

    runtime = self.runs.get(run_id)
    if runtime is None:
      return {"success": False, "code": "RUN_NOT_FOUND", "message": "策略运行不存在"}
    async with AsyncExitStack() as approval_stack:
      parameters = dict(runtime.context.parameters or {})
      account_id = str(parameters.get("account_id") or "").strip()
      if account_id and str(parameters.get("global_monitor_id") or "").strip():
        await approval_stack.enter_async_context(
          t_trade_account_coordination_lock(account_id)
        )
      await approval_stack.enter_async_context(runtime.approval_lock)
      if not self._accepts_non_durable_output(runtime):
        return {
          "success": False,
          "code": "RUNTIME_NOT_RUNNING",
          "message": "策略运行不在 RUNNING 状态，不能确认交易信号",
        }
      intent = runtime.pending_approvals.get(intent_id)
      if intent is None:
        return {
          "success": False,
          "code": "INTENT_NOT_AWAITING_APPROVAL",
          "message": "信号不存在、已处理或已过期",
        }
      challenge_failure = await self._managed_entry_approval_challenge_failure(
        runtime,
        intent,
        approval_audit=approval_audit,
      )
      if challenge_failure is not None:
        return {
          "success": False,
          "code": challenge_failure[0],
          "message": challenge_failure[1],
        }
      if runtime.durable_event_barrier_key:
        return {
          "success": False,
          "code": "DURABLE_RECONCILIATION_REQUIRED",
          "message": "成交回报状态正在安全落盘，请稍后重试确认",
        }
      reconciliation_failure = self._runtime_state_reconciliation_failure(runtime)
      if reconciliation_failure is not None:
        return {
          "success": False,
          "code": reconciliation_failure[0],
          "message": reconciliation_failure[1],
        }

      candidate_failure = self._v3_t_trade_candidate_approval_failure(
        runtime,
        intent,
        approval_expectation=approval_expectation,
      )
      if candidate_failure is not None:
        code, message, invalidate_intent = candidate_failure
        if invalidate_intent:
          persistence_failure = await self._terminalize_pending_approval_for_request(
            runtime,
            intent,
            status="EXPIRED",
            reason=code,
            message=message,
          )
          if persistence_failure is not None:
            return persistence_failure
        return {"success": False, "code": code, "message": message}

      durable_config_failure = await self._v3_t_trade_durable_config_failure(
        runtime,
        intent,
      )
      if durable_config_failure is not None:
        code, message, invalidate_intent = durable_config_failure
        if invalidate_intent:
          persistence_failure = await self._terminalize_pending_approval_for_request(
            runtime,
            intent,
            status="EXPIRED",
            reason=code,
            message=message,
          )
          if persistence_failure is not None:
            return persistence_failure
        return {"success": False, "code": code, "message": message}

      failure = self._approval_failure(runtime, intent)
      if failure is not None:
        persistence_failure = await self._terminalize_pending_approval_for_request(
          runtime,
          intent,
          status="EXPIRED",
          reason=failure[0],
          message=failure[1],
        )
        if persistence_failure is not None:
          return persistence_failure
        return {"success": False, "code": failure[0], "message": failure[1]}

      portfolio_failure = self._t_trade_portfolio_approval_failure(runtime, intent)
      if portfolio_failure is not None:
        return {
          "success": False,
          "code": portfolio_failure[0],
          "message": portfolio_failure[1],
        }

      if runtime.state_manager:
        strict_status_update = getattr(
          runtime.state_manager,
          "update_trade_intent_status_strict",
          None,
        )
        if not callable(strict_status_update):
          if self._is_v3_t_trade_manual_intent(intent):
            return {
              "success": False,
              "code": "T_TRADE_APPROVAL_STATUS_PERSISTENCE_UNAVAILABLE",
              "message": "确认状态缺少严格持久化边界，信号仍保持待确认",
            }
          strict_status_update = getattr(
            runtime.state_manager,
            "update_trade_intent_status",
            None,
          )
        if not callable(strict_status_update):
          return {
            "success": False,
            "code": "APPROVAL_STATUS_PERSISTENCE_UNAVAILABLE",
            "message": "确认状态无法持久化，信号仍保持待确认",
          }
        try:
          await strict_status_update(
            intent_id,
            "APPROVED",
            notes="MANUAL_APPROVAL_ACCEPTED",
          )
        except Exception as exc:
          if runtime.metrics:
            runtime.metrics.error_count += 1
          self._runtime_log(
            runtime,
            "ERROR",
            "人工确认状态持久化失败，未占用敞口且未进入订单路由: "
            f"intent_id={intent_id} error={exc}",
          )
          return {
            "success": False,
            "code": (
              "T_TRADE_APPROVAL_STATUS_PERSIST_FAILED"
              if self._is_v3_t_trade_manual_intent(intent)
              else "APPROVAL_STATUS_PERSIST_FAILED"
            ),
            "message": "确认状态保存失败，信号仍保持待确认，请稍后重试",
          }
      # The durable status write above yields to the runtime event loop. Recheck
      # every mutable gate before the synchronous strategy PENDING transition so
      # a newer tick, continuity barrier, or account snapshot cannot slip through
      # the approval window.
      late_failure: Optional[tuple[str, str]] = None
      if runtime.durable_event_barrier_key:
        late_failure = (
          "DURABLE_RECONCILIATION_REQUIRED",
          "成交回报状态正在安全落盘，请稍后重试确认",
        )
      if late_failure is None:
        late_failure = self._runtime_state_reconciliation_failure(runtime)
      if late_failure is None:
        late_candidate_failure = self._v3_t_trade_candidate_approval_failure(
          runtime,
          intent,
          approval_expectation=approval_expectation,
        )
        if late_candidate_failure is not None:
          late_failure = late_candidate_failure[:2]
      if late_failure is None:
        late_durable_config_failure = await self._v3_t_trade_durable_config_failure(
          runtime, intent
        )
        if late_durable_config_failure is not None:
          late_failure = late_durable_config_failure[:2]
      if late_failure is None:
        late_failure = self._approval_failure(runtime, intent)
      if late_failure is None:
        late_failure = self._t_trade_portfolio_approval_failure(runtime, intent)
      if late_failure is not None:
        persistence_failure = await self._terminalize_pending_approval_for_request(
          runtime,
          intent,
          status="EXPIRED",
          reason=late_failure[0],
          message=late_failure[1],
          strict_persistence=True,
        )
        if persistence_failure is not None:
          return persistence_failure
        return {
          "success": False,
          "code": late_failure[0],
          "message": late_failure[1],
        }
      self._reserve_t_trade_entry_exposure(runtime, intent)
      runtime.pending_approvals.pop(intent_id, None)
      await self._notify_strategy_order(
        runtime,
        OrderStateEvent(
          order_id=None,
          status=OrderStatus.PENDING.value,
          metadata={
            **dict(intent.metadata or {}),
            "intent_id": intent.intent_id,
            "instrument_code": intent.instrument_code,
          },
        ),
      )
      await self._process_trade_intent(runtime, intent)
      return {
        "success": True,
        "code": "APPROVED",
        "message": "信号已确认并进入下单风控",
      }

  async def _managed_entry_approval_challenge_failure(
    self,
    runtime: StrategyRuntime,
    intent: TradeIntent,
    *,
    approval_audit: Optional[Mapping[str, Any]],
  ) -> Optional[tuple[str, str]]:
    if (
      getattr(runtime.strategy_class, "__name__", "")
      != "AshareManagedEntryPlanStrategy"
      or intent.direction != TradeIntentDirection.BUY
    ):
      return None

    failure = (
      "ENTRY_PLAN_DEVICE_CHALLENGE_REQUIRED",
      "托管买入必须使用 confirmEntryIntent 完成设备挑战后确认",
    )
    audit = dict(approval_audit or {})
    challenge_id = str(audit.get("challenge_id") or "")
    actor_id = str(audit.get("actor_id") or "")
    device_session_id = str(audit.get("device_session_id") or "")
    channel = str(audit.get("channel") or "")
    if (
      not challenge_id
      or not actor_id
      or not device_session_id
      or channel not in {"ENTRY_PLAN_DEVICE_CHALLENGE", "IOS_BIOMETRIC"}
    ):
      return failure

    async with AsyncSessionLocal() as db:
      record = await db.get(TradeIntentRecord, intent.intent_id)
    if (
      record is None
      or str(record.strategy_run_id or "") != runtime.run_id
      or str(record.direction or "").upper() != "BUY"
      or str(record.status or "").upper() != "AWAITING_APPROVAL"
    ):
      return failure
    challenge = dict(
      dict(record.intent_metadata or {}).get(
        "mobile_trade_approval_challenge_v1",
        {},
      )
      or {}
    )
    expected = {
      "challenge_id": challenge_id,
      "action": "STRATEGY_TRADE_INTENT_APPROVAL",
      "user_id": actor_id,
      "device_session_id": device_session_id,
      "run_id": runtime.run_id,
      "intent_id": intent.intent_id,
    }
    account_id = str(runtime.context.parameters.get("account_id") or "")
    if account_id:
      expected["account_id"] = account_id
    if not challenge.get("consumed_at") or any(
      str(challenge.get(key) or "") != str(value) for key, value in expected.items()
    ):
      return failure
    return None

  async def _restore_pending_manual_approvals(
    self,
    runtime: StrategyRuntime,
    *,
    t_trade_account_coordination_held: bool = False,
  ) -> None:
    """Restore only strategy-declared manual intents, preserving TTL semantics."""
    if not runtime.strategy or not runtime.state_manager:
      return
    if self._uses_t_trade_opportunity_runtime(runtime):
      # An intact generic PREPARED handoff was finalized before strategy
      # initialization.  What remains here is the separate, P/L-only
      # actionable-candidate recovery boundary; pure MATERIAL audits never
      # enter this outbox.
      await self._replay_pending_actionable_t_trade_material_events(runtime)
      await self._replay_pending_t_trade_paper_fill_facts(runtime)
      await self._converge_v3_t_trade_startup_candidates(
        runtime,
        t_trade_account_coordination_held=t_trade_account_coordination_held,
      )
    inspected_intent_ids: set[str] = set()
    for intent_id in runtime.strategy.pending_manual_intent_ids():
      inspected_intent_ids.add(intent_id)
      intent = await runtime.state_manager.restore_manual_trade_intent(intent_id)
      if intent is None:
        await self._converge_restored_manual_intent_status(runtime, intent_id)
        continue
      strategy_failure = runtime.strategy.validate_manual_approval(intent, None)
      metadata = dict(intent.metadata or {})
      # A restored T entry has no verifiable quote-stream epoch.  Even when an
      # older snapshot omitted both the rolling window and rewarm marker, it
      # must expire before the runtime is exposed as RUNNING; on_init will then
      # install the all-instrument rewarm gate for subsequent observations.
      if (
        strategy_failure is None
        and runtime.context.mode in {StrategyRunMode.PAPER, StrategyRunMode.LIVE}
        and intent.direction == TradeIntentDirection.BUY
        and intent.execution_mode == TradeIntentExecutionMode.MANUAL_CONFIRM
        and str(metadata.get("t_trade_role") or "").lower() == "entry"
      ):
        strategy_failure = (
          "APPROVAL_SIGNAL_INVALIDATED",
          "重启后无法验证旧信号行情连续性，请等待观察窗重新预热",
        )
      failure = self._approval_failure(runtime, intent)
      invalidation = strategy_failure or failure
      if invalidation and invalidation[0] in {
        "APPROVAL_SIGNAL_INVALIDATED",
        "APPROVAL_TTL_EXPIRED",
      }:
        await self._reject_pending_approval(
          runtime,
          intent,
          status="EXPIRED",
          reason=invalidation[0],
          message=invalidation[1],
          strict_persistence=True,
        )
        self._checkpoint_restored_strategy_state(runtime)
        force_save = getattr(runtime.state_manager, "force_save", None)
        if callable(force_save) and not await force_save():
          raise RuntimeError("恢复的待确认信号失效状态保存失败，拒绝启动策略运行")
        continue
      runtime.pending_approvals[intent.intent_id] = intent
      self._runtime_log(
        runtime,
        "INFO",
        f"已恢复待人工确认交易信号: intent_id={intent.intent_id}",
      )
    managed_state = ManagedEntryPlanState.from_dict(
      dict(runtime.strategy.state.get("managed_entry_plan", {}) or {})
    )
    if (
      managed_state.pending_intent_id
      and managed_state.pending_intent_id not in inspected_intent_ids
      and managed_state.phase
      in {
        EntryPlanStatus.AWAITING_APPROVAL,
        EntryPlanStatus.ENTRY_PENDING,
        EntryPlanStatus.DRAINING,
      }
    ):
      await self._converge_restored_managed_entry_intent(
        runtime,
        intent_id=managed_state.pending_intent_id,
        state=managed_state,
      )

  async def _converge_v3_t_trade_startup_candidates(
    self,
    runtime: StrategyRuntime,
    *,
    t_trade_account_coordination_held: bool = False,
  ) -> None:
    """Fail closed across every V3 candidate persistence crash window.

    The normal write chain intentionally uses small durable boundaries.  A
    process can therefore stop after the LATCHED checkpoint, after the initial
    PENDING intent append, or after linking state to the intent but before the
    row advances to AWAITING_APPROVAL.  Startup never resumes those ambiguous
    candidates and never routes them: it terminates mismatched rows, asks the
    strategy to suppress state through ``OrderStateEvent``, checkpoints that
    state together with the stable audit event in the manager-owned outbox,
    then materializes and acknowledges the event idempotently.
    """

    strategy = runtime.strategy
    state_manager = runtime.state_manager
    if strategy is None or state_manager is None:
      return
    recovery_projection = strategy.manual_approval_recovery_candidates()
    if recovery_projection is None:
      return
    if not bool(getattr(state_manager, "persist_enabled", True)):
      return

    account_id = str(runtime.context.parameters.get("account_id") or "").strip()
    if not account_id:
      raise RuntimeError("V3 候选启动收敛缺少唯一账户绑定")
    loader = getattr(state_manager, "restore_v3_manual_candidate_intents", None)
    strict_updater = getattr(state_manager, "update_trade_intent_status_strict", None)
    force_save = getattr(state_manager, "force_save", None)
    if not callable(loader) or not callable(strict_updater) or not callable(force_save):
      raise RuntimeError("V3 候选启动收敛缺少严格持久化边界")

    # The global monitor owns this account lock across configuration
    # reconciliation and delegates runtime startup to a lifecycle task.  That
    # child task must not enqueue behind its awaiting parent.  Other startup
    # paths still acquire the lock here and preserve the normal serialization.
    coordination_context = (
      nullcontext()
      if t_trade_account_coordination_held
      else t_trade_account_coordination_lock(account_id)
    )
    async with coordination_context:
      candidates = list(recovery_projection)
      durable_rows = list(
        await loader(
          account_id=account_id,
          linked_intent_ids=[
            candidate.pending_intent_id
            for candidate in candidates
            if candidate.pending_intent_id
          ],
        )
        or []
      )
      self._validate_v3_startup_recovery_scope(
        runtime,
        account_id=account_id,
        durable_rows=durable_rows,
        candidates=candidates,
      )

      consistent_intent_ids: set[str] = set()
      for restored in durable_rows:
        intent = restored.intent
        durable_status = str(restored.durable_status or "").strip().upper()
        state = self._matching_v3_recovery_candidate(candidates, intent)
        if (
          durable_status == "AWAITING_APPROVAL"
          and state is not None
          and self._is_consistent_v3_awaiting_pair(state, intent)
        ):
          consistent_intent_ids.add(intent.intent_id)
          continue
        if durable_status not in {"PENDING", "AWAITING_APPROVAL"}:
          continue
        terminal_status = "REJECTED" if durable_status == "PENDING" else "EXPIRED"
        reason = (
          "T_TRADE_STARTUP_ORPHAN_PENDING_INTENT"
          if durable_status == "PENDING"
          else "T_TRADE_STARTUP_APPROVAL_STATE_MISMATCH"
        )
        await strict_updater(
          intent.intent_id,
          terminal_status,
          metadata={
            **dict(intent.metadata or {}),
            "runtime_gate": reason,
            "startup_recovery": True,
          },
          notes=reason,
        )
        runtime.pending_approvals.pop(intent.intent_id, None)

      suppressed_events: List[Dict[str, Any]] = []
      suppressed_instruments: set[str] = set()
      for candidate in candidates:
        if candidate.pending_intent_id in consistent_intent_ids:
          continue
        matching_rows = [
          restored
          for restored in durable_rows
          if self._v3_recovery_candidate_matches_intent(
            candidate,
            restored.intent,
          )
        ]
        linked = next(
          (
            restored
            for restored in matching_rows
            if restored.intent.intent_id == candidate.pending_intent_id
          ),
          matching_rows[0] if matching_rows else None,
        )
        if (
          candidate.candidate_status == "AWAITING_APPROVAL"
          and linked is not None
          and linked.intent.intent_id == candidate.pending_intent_id
          and str(linked.durable_status or "").strip().upper()
          not in {"PENDING", "AWAITING_APPROVAL"}
          and dict(linked.intent.metadata or {}).get("startup_recovery") is not True
        ):
          # The intent advanced beyond manual approval before the last runtime
          # snapshot.  Existing durable order/report reconciliation owns this
          # case and must not be collapsed into an orphan-candidate rejection.
          continue
        intent_id = (
          linked.intent.intent_id if linked is not None else candidate.pending_intent_id
        )
        status = (
          "EXPIRED" if candidate.candidate_status == "AWAITING_APPROVAL" else "REJECTED"
        )
        reason = (
          "T_TRADE_STARTUP_APPROVAL_STATE_MISMATCH"
          if candidate.candidate_status == "AWAITING_APPROVAL"
          else "T_TRADE_STARTUP_ORPHAN_PENDING_INTENT"
          if matching_rows
          else "T_TRADE_STARTUP_ORPHAN_LATCHED_CANDIDATE"
        )
        metadata = {
          **(dict(linked.intent.metadata or {}) if linked is not None else {}),
          "t_trade_role": "entry",
          "account_id": account_id,
          "instrument_code": candidate.instrument_code,
          "candidate_id": candidate.candidate_id,
          "candidate_fingerprint": candidate.candidate_fingerprint,
          "candidate_state_version": candidate.candidate_state_version,
          "intent_id": intent_id,
          "source_time_ms": candidate.source_time_ms,
          "approval_reason": reason,
          "startup_recovery": True,
        }
        timestamp = (
          datetime.fromtimestamp(candidate.source_time_ms / 1000, timezone.utc)
          if candidate.source_time_ms > 0
          else None
        )
        patch = await self._notify_strategy_order(
          runtime,
          OrderStateEvent(
            order_id=None,
            status=status,
            filled_volume=0,
            error_message="做 T 候选持久化在重启时发现不完整，已禁止恢复下单",
            timestamp=timestamp,
            metadata=metadata,
          ),
          raise_on_error=True,
        )
        remaining = list(strategy.manual_approval_recovery_candidates() or [])
        if any(
          item.instrument_code == candidate.instrument_code
          and item.candidate_id == candidate.candidate_id
          for item in remaining
        ):
          raise RuntimeError(
            "V3 候选启动收敛后仍可执行: "
            f"run_id={runtime.run_id}, candidate_id={candidate.candidate_id}"
          )
        material_events = [
          dict(event)
          for event in list(getattr(patch, "append_events", []) or [])
          if isinstance(event, Mapping)
          and event.get("type") == T_TRADE_OPPORTUNITY_EVALUATION_EVENT
          and str(event.get("record_kind") or "").upper() == "MATERIAL"
          and str(event.get("event_type") or "").upper() == "CANDIDATE_SUPPRESSED"
        ]
        if not material_events:
          raise RuntimeError(
            "V3 候选启动抑制缺少 MATERIAL 审计: "
            f"run_id={runtime.run_id}, candidate_id={candidate.candidate_id}"
          )
        suppressed_events.extend(material_events)
        suppressed_instruments.add(candidate.instrument_code)

      if suppressed_events:
        # The suppression state and stable events must share the first durable
        # boundary. If the process stops afterwards, startup replays the outbox
        # and cannot accidentally restore an actionable candidate.
        self._checkpoint_restored_strategy_state(runtime)
        if not await force_save():
          raise RuntimeError("V3 候选启动抑制状态保存失败，拒绝启动策略运行")
        acknowledged: List[Mapping[str, Any]] = []
        for event in suppressed_events:
          await self._materialize_t_trade_evaluation_with_retry(
            event=event,
            account_id=account_id,
            strategy_run_id=runtime.run_id,
            labels=self._t_trade_observability_labels([event]),
            cas_committed=True,
          )
          seeded = await self._seed_t_trade_candidate_outcome(
            runtime,
            account_id=account_id,
            event=event,
            strict=True,
          )
          if not seeded:
            raise RuntimeError("V3 候选启动抑制结果初始化失败")
          acknowledged.append(event)
        await self._acknowledge_t_trade_actionable_material_events(runtime, acknowledged)
        for instrument_code in sorted(suppressed_instruments):
          await self._notify_t_trade_opportunity_update(
            runtime,
            instrument_code=instrument_code,
            source_time_ms=max(
              (
                item.source_time_ms
                for item in candidates
                if item.instrument_code == instrument_code
              ),
              default=0,
            ),
            events=[
              event
              for event in suppressed_events
              if str(event.get("instrument_code") or "").strip().upper()
              == instrument_code
            ],
            immediate=True,
          )
        self._runtime_log(
          runtime,
          "WARNING",
          "已收敛做 T 候选跨事务崩溃窗口，所有不完整候选均禁止恢复下单: "
          f"candidate_count={len(suppressed_events)}",
        )

  @staticmethod
  def _validate_v3_startup_recovery_scope(
    runtime: StrategyRuntime,
    *,
    account_id: str,
    durable_rows: List[Any],
    candidates: List[ManualApprovalRecoveryCandidate],
  ) -> None:
    candidate_keys: set[tuple[str, str]] = set()
    for candidate in candidates:
      key = (candidate.instrument_code, candidate.candidate_id)
      if (
        not candidate.instrument_code
        or not candidate.candidate_id
        or key in candidate_keys
      ):
        raise RuntimeError("V3 候选启动收敛投影身份无效或重复")
      candidate_keys.add(key)
    for restored in durable_rows:
      intent = restored.intent
      metadata = dict(intent.metadata or {})
      if (
        intent.run_id != runtime.run_id
        or str(metadata.get("account_id") or "").strip() != account_id
        or intent.direction != TradeIntentDirection.BUY
        or intent.execution_mode != TradeIntentExecutionMode.MANUAL_CONFIRM
        or str(metadata.get("t_trade_role") or "").strip().lower() != "entry"
      ):
        raise RuntimeError(
          "V3 候选启动收敛意图作用域无效: "
          f"run_id={runtime.run_id}, intent_id={intent.intent_id}"
        )

  @classmethod
  def _matching_v3_recovery_candidate(
    cls,
    candidates: List[ManualApprovalRecoveryCandidate],
    intent: TradeIntent,
  ) -> Optional[ManualApprovalRecoveryCandidate]:
    return next(
      (
        candidate
        for candidate in candidates
        if cls._v3_recovery_candidate_matches_intent(candidate, intent)
      ),
      None,
    )

  @staticmethod
  def _v3_recovery_candidate_matches_intent(
    candidate: ManualApprovalRecoveryCandidate,
    intent: TradeIntent,
  ) -> bool:
    metadata = dict(intent.metadata or {})
    return bool(
      candidate.instrument_code == str(intent.instrument_code or "").strip().upper()
      and candidate.candidate_id == str(metadata.get("candidate_id") or "").strip()
      and candidate.candidate_fingerprint
      == str(metadata.get("candidate_fingerprint") or "").strip()
    )

  @classmethod
  def _is_consistent_v3_awaiting_pair(
    cls,
    candidate: ManualApprovalRecoveryCandidate,
    intent: TradeIntent,
  ) -> bool:
    metadata = dict(intent.metadata or {})
    try:
      intent_state_version = int(metadata.get("candidate_state_version") or 0)
    except (TypeError, ValueError, OverflowError):
      return False
    return bool(
      cls._v3_recovery_candidate_matches_intent(candidate, intent)
      and candidate.candidate_status == "AWAITING_APPROVAL"
      and candidate.pending_intent_id == intent.intent_id
      and candidate.order_status == "AWAITING_APPROVAL"
      and candidate.candidate_state_version == intent_state_version
    )

  async def _converge_restored_manual_intent_status(
    self,
    runtime: StrategyRuntime,
    intent_id: str,
  ) -> None:
    """Converge a stale strategy snapshot against durable intent truth.

    A crash can persist the strategy's AWAITING_APPROVAL marker after the intent
    row has already advanced. Only zero-fill terminal truth is replayed directly.
    Ambiguous pre-order gaps and any recorded fill remain fail-closed until the
    idempotent durable report inbox replays the broker lifecycle.
    """

    managed_entry_state = ManagedEntryPlanState.from_dict(
      dict(runtime.strategy.state.get("managed_entry_plan", {}) or {})
    )
    if managed_entry_state.pending_intent_id == intent_id:
      await self._converge_restored_managed_entry_intent(
        runtime,
        intent_id=intent_id,
        state=managed_entry_state,
      )
      return

    instrument_states = dict(runtime.strategy.state.get("instrument_states", {}) or {})
    matched_entry = next(
      (
        (str(code), dict(raw_state or {}))
        for code, raw_state in instrument_states.items()
        if str(dict(raw_state or {}).get("pending_entry_intent_id", "") or "")
        == intent_id
      ),
      None,
    )
    if matched_entry is None:
      return
    instrument_code, _entry_state = matched_entry

    snapshot_reader = getattr(runtime.state_manager, "get_trade_intent_snapshot", None)
    if not callable(snapshot_reader):
      return
    durable = await snapshot_reader(intent_id)
    if not isinstance(durable, dict):
      return
    durable_status = str(durable.get("status") or "").strip().upper()
    if not durable_status or durable_status == "AWAITING_APPROVAL":
      return
    if durable_status == "PARTIALLY_FILLED":
      durable_status = "PARTIAL_FILLED"

    durable_code = str(durable.get("instrument_code") or "").strip().upper()
    if durable_code and durable_code != instrument_code.upper():
      self._runtime_log(
        runtime,
        "ERROR",
        "待确认信号快照与数据库标的不一致，保持保守门控: "
        f"intent_id={intent_id}, snapshot_code={instrument_code}, "
        f"durable_code={durable_code}",
      )
      return

    order_id = str(durable.get("order_id") or "").strip()
    raw_metadata = durable.get("metadata")
    metadata = {
      **(dict(raw_metadata) if isinstance(raw_metadata, dict) else {}),
      "t_trade_role": "entry",
      "intent_id": intent_id,
      "instrument_code": instrument_code,
      "approval_reason": "RESTORED_DURABLE_INTENT_STATUS",
    }
    try:
      executed_volume = max(0, int(durable.get("executed_volume") or 0))
    except (TypeError, ValueError, OverflowError):
      executed_volume = 0

    if executed_volume > 0:
      await self._mark_restored_intent_reconcile_required(
        runtime,
        intent_id=intent_id,
        instrument_code=instrument_code,
        order_id=order_id,
        metadata=metadata,
        reason=(
          "DURABLE_FILL_AWAITS_IDEMPOTENT_INBOX_REPLAY: "
          f"status={durable_status}, executed_volume={executed_volume}"
        ),
      )
      return

    if durable_status == "FILLED":
      await self._mark_restored_intent_reconcile_required(
        runtime,
        intent_id=intent_id,
        instrument_code=instrument_code,
        order_id=order_id,
        metadata=metadata,
        reason="FILLED_INTENT_MISSING_DURABLE_EXECUTION_DETAILS",
      )
      return

    active_statuses = {
      "APPROVED": "PENDING",
      "PENDING": "PENDING",
      "ROUTED": "PENDING",
      "SIZED": "PENDING",
      "ORDER_RISK_ALLOWED": "PENDING",
      "DELAYED": "PENDING",
      "SUBMITTED": "SUBMITTED",
      "ACCEPTED": "ACCEPTED",
      "PARTIAL_FILLED": "PARTIAL_FILLED",
    }
    terminal_statuses = {
      "REJECTED": "REJECTED",
      "CANCELLED": "CANCELLED",
      "CANCELED": "CANCELLED",
      "EXPIRED": "EXPIRED",
      "RECONCILED_ZERO_FILL": "RECONCILED_ZERO_FILL",
    }
    callback_status = terminal_statuses.get(durable_status)
    if callback_status is None and durable_status in active_statuses:
      if order_id:
        callback_status = active_statuses[durable_status]
      else:
        await self._mark_restored_intent_reconcile_required(
          runtime,
          intent_id=intent_id,
          instrument_code=instrument_code,
          order_id="",
          metadata=metadata,
          reason=f"{durable_status}_WITHOUT_DURABLE_ORDER_CORRELATION",
        )
        return
    if callback_status is None:
      await self._mark_restored_intent_reconcile_required(
        runtime,
        intent_id=intent_id,
        instrument_code=instrument_code,
        order_id=order_id,
        metadata=metadata,
        reason=f"UNKNOWN_DURABLE_INTENT_STATUS:{durable_status}",
      )
      return

    await self._notify_strategy_order(
      runtime,
      OrderStateEvent(
        order_id=order_id or None,
        status=callback_status,
        filled_volume=executed_volume,
        metadata=metadata,
      ),
    )
    self._checkpoint_restored_strategy_state(runtime)
    self._runtime_log(
      runtime,
      "INFO",
      "已按数据库真源收敛待确认信号快照: "
      f"intent_id={intent_id}, durable_status={durable_status}, "
      f"order_id={order_id or '-'}, "
      f"callback_status={callback_status}",
    )

  async def _converge_restored_managed_entry_intent(
    self,
    runtime: StrategyRuntime,
    *,
    intent_id: str,
    state: ManagedEntryPlanState,
  ) -> None:
    """Converge an approved managed BUY without ever replaying the order."""

    configured = dict(runtime.context.parameters.get("managed_entry_plan") or {})
    instrument_code = str(configured.get("instrument_code") or "").upper()
    truth = await self._managed_entry_restore_truth(
      runtime,
      intent_id=intent_id,
      instrument_code=instrument_code,
    )
    kind = str(truth.get("kind") or "RECONCILE_REQUIRED")
    if kind == "RECONCILED_ZERO_FILL":
      metadata = {
        **dict(truth.get("metadata") or {}),
        "entry_plan_id": runtime.run_id,
        "entry_stage_id": state.pending_stage_id,
        "entry_rule_id": state.pending_rule_id,
        "intent_id": intent_id,
        "instrument_code": instrument_code,
        "approval_reason": str(truth.get("reason") or "APPROVED_WITHOUT_DURABLE_ORDER"),
      }
      runtime.pending_approvals.pop(intent_id, None)
      if runtime.state_manager:
        release_order_resources = getattr(
          runtime.state_manager,
          "release_order_resources",
          None,
        )
        if callable(release_order_resources):
          release_order_resources(intent_id)
      await self._notify_strategy_order(
        runtime,
        OrderStateEvent(
          order_id=None,
          status="RECONCILED_ZERO_FILL",
          timestamp=self._runtime_now(runtime),
          metadata=metadata,
        ),
        raise_on_error=True,
      )
      self._checkpoint_restored_strategy_state(runtime)
      self._runtime_log(
        runtime,
        "WARNING",
        "已确认买入在崩溃前未形成任何持久订单，按零成交安全释放；"
        f"不会重下单: intent_id={intent_id}",
      )
      return

    state.phase = (
      EntryPlanStatus.DRAINING
      if state.terminal_requested is not None
      else EntryPlanStatus.ENTRY_PENDING
    )
    if kind != "ORDER_PENDING":
      state.data_quality = "RECONCILE_REQUIRED"
    state.last_decision = {
      **dict(state.last_decision or {}),
      "reason": str(truth.get("reason") or kind),
      "durable_status": str(truth.get("durable_status") or ""),
      "order_status": str(truth.get("order_status") or ""),
      "client_order_id": str(truth.get("client_order_id") or ""),
      "broker_order_id": str(truth.get("broker_order_id") or ""),
    }
    snapshot = state.to_dict()
    runtime.strategy.state.set(
      "managed_entry_plan",
      snapshot,
      persist=False,
      notify=False,
    )
    self._checkpoint_restored_strategy_state(runtime)
    self._runtime_log(
      runtime,
      "INFO" if kind == "ORDER_PENDING" else "WARNING",
      "已按持久订单真源恢复托管买入状态: "
      f"intent_id={intent_id}, kind={kind}, "
      f"order_status={truth.get('order_status') or '-'}",
    )

  @staticmethod
  async def _managed_entry_restore_truth(
    runtime: StrategyRuntime,
    *,
    intent_id: str,
    instrument_code: str,
  ) -> Dict[str, Any]:
    """Lock every durable order artifact before proving an approved intent is empty."""

    account_id = str(runtime.context.parameters.get("account_id") or "")
    async with AsyncSessionLocal() as db:
      intent = await db.get(
        TradeIntentRecord,
        intent_id,
        with_for_update=True,
      )
      if intent is None:
        return {
          "kind": "RECONCILE_REQUIRED",
          "reason": "MANAGED_ENTRY_INTENT_RECORD_MISSING",
        }
      durable_status = str(intent.status or "").strip().upper()
      metadata = dict(intent.intent_metadata or {})
      execution_mode = str(metadata.get("execution_mode") or "").strip().upper()
      if (
        str(intent.strategy_run_id or "") != runtime.run_id
        or str(intent.direction or "").upper() != "BUY"
        or str(metadata.get("entry_plan_id") or "") != runtime.run_id
        or execution_mode not in {"AUTO", "MANUAL_CONFIRM"}
        or not instrument_code
        or str(intent.instrument_code or "").upper() != instrument_code
        or (intent.account_id and str(intent.account_id) != account_id)
      ):
        return {
          "kind": "RECONCILE_REQUIRED",
          "durable_status": durable_status,
          "reason": "MANAGED_ENTRY_INTENT_BINDING_MISMATCH",
          "metadata": metadata,
        }
      if durable_status == "RECONCILED_ZERO_FILL":
        return {
          "kind": "RECONCILED_ZERO_FILL",
          "durable_status": durable_status,
          "reason": str(intent.notes or "APPROVED_WITHOUT_DURABLE_ORDER"),
          "metadata": metadata,
        }

      pending_orders = list(
        (
          await db.execute(
            select(PendingTradeOrder)
            .where(
              PendingTradeOrder.strategy_run_id == runtime.run_id,
              PendingTradeOrder.intent_id == intent_id,
            )
            .with_for_update()
          )
        )
        .scalars()
        .all()
      )
      correlations = list(
        (
          await db.execute(
            select(StrategyOrderCorrelation)
            .where(
              StrategyOrderCorrelation.strategy_run_id == runtime.run_id,
              StrategyOrderCorrelation.intent_id == intent_id,
            )
            .with_for_update()
          )
        )
        .scalars()
        .all()
      )
      outboxes = list(
        (
          await db.execute(
            select(TradeCommandOutbox)
            .where(
              TradeCommandOutbox.account_id == account_id,
              TradeCommandOutbox.payload["strategy_run_id"].as_string()
              == runtime.run_id,
              TradeCommandOutbox.payload["intent_id"].as_string() == intent_id,
            )
            .with_for_update()
          )
        )
        .scalars()
        .all()
      )
      runtime_events = list(
        (
          await db.execute(
            select(StrategyRuntimeEvent)
            .where(
              StrategyRuntimeEvent.strategy_run_id == runtime.run_id,
              StrategyRuntimeEvent.payload["metadata"]["intent_id"].as_string()
              == intent_id,
            )
            .with_for_update()
          )
        )
        .scalars()
        .all()
      )

      order_id = str(intent.order_id or "").strip()
      try:
        executed_volume = int(intent.executed_volume or 0)
        executed_volume_valid = executed_volume >= 0
      except (TypeError, ValueError, OverflowError):
        executed_volume = 0
        executed_volume_valid = False
      try:
        executed_price = float(intent.executed_price or 0.0)
        executed_price_valid = isfinite(executed_price)
      except (TypeError, ValueError, OverflowError):
        executed_price = 0.0
        executed_price_valid = False
      zero_execution_proof = bool(
        executed_volume_valid
        and executed_volume == 0
        and executed_price_valid
        and executed_price <= 0
        and intent.executed_time is None
      )
      has_execution_fact = bool(
        not zero_execution_proof
        or executed_volume
        or executed_price > 0
        or intent.executed_time is not None
      )
      has_artifact = bool(
        order_id
        or has_execution_fact
        or pending_orders
        or correlations
        or outboxes
        or runtime_events
      )
      zero_order_crash_gap = bool(
        (execution_mode == "MANUAL_CONFIRM" and durable_status == "APPROVED")
        or (execution_mode == "AUTO" and durable_status == "PENDING")
      )
      if zero_order_crash_gap and zero_execution_proof and not has_artifact:
        reason = (
          "APPROVED_WITHOUT_DURABLE_ORDER_RECONCILED_ZERO_FILL"
          if execution_mode == "MANUAL_CONFIRM"
          else "AUTO_PENDING_WITHOUT_DURABLE_ORDER_RECONCILED_ZERO_FILL"
        )
        intent.status = "RECONCILED_ZERO_FILL"
        intent.notes = reason
        intent.intent_metadata = {
          **metadata,
          "managed_entry_restore": {
            "reason": reason,
            "reconciled_at": time_utils.now().isoformat(),
            "zero_order_proof": {
              "order_id_empty": True,
              "executed_volume": 0,
              "executed_price_non_positive": True,
              "executed_time_empty": True,
              "pending_order_count": 0,
              "outbox_count": 0,
              "correlation_count": 0,
              "runtime_event_count": 0,
            },
          },
        }
        await db.commit()
        return {
          "kind": "RECONCILED_ZERO_FILL",
          "durable_status": "RECONCILED_ZERO_FILL",
          "reason": reason,
          "metadata": dict(intent.intent_metadata or {}),
        }

      active_order_statuses = {
        "PENDING",
        "QUEUED",
        "DELIVERED",
        "SUBMITTED",
        "ACCEPTED",
        "PARTIAL_FILLED",
        "PARTIALLY_FILLED",
        "CANCEL_REQUESTED",
      }
      if len(pending_orders) == 1 and len(correlations) == 1:
        pending = pending_orders[0]
        pending_status = str(pending.status or "").strip().upper()
        if pending_status in active_order_statuses:
          return {
            "kind": "ORDER_PENDING",
            "durable_status": durable_status,
            "order_status": pending_status,
            "client_order_id": str(pending.client_order_id or ""),
            "broker_order_id": str(pending.broker_order_id or ""),
            "metadata": metadata,
          }

      reason = (
        "MANAGED_ENTRY_DURABLE_ORDER_RECONCILIATION_REQUIRED:"
        f"pending={len(pending_orders)},outbox={len(outboxes)},"
        f"correlation={len(correlations)},runtime_event={len(runtime_events)},"
        f"executed_volume={executed_volume},order_id={int(bool(order_id))}"
      )
      if durable_status != "RECONCILE_REQUIRED":
        intent.status = "RECONCILE_REQUIRED"
        intent.notes = reason
        intent.intent_metadata = {
          **metadata,
          "managed_entry_restore": {
            "reason": reason,
            "reconciled_at": time_utils.now().isoformat(),
          },
        }
        await db.commit()
      return {
        "kind": "RECONCILE_REQUIRED",
        "durable_status": "RECONCILE_REQUIRED",
        "reason": reason,
        "metadata": dict(intent.intent_metadata or {}),
      }

  async def _mark_restored_intent_reconcile_required(
    self,
    runtime: StrategyRuntime,
    *,
    intent_id: str,
    instrument_code: str,
    order_id: str,
    metadata: Dict[str, Any],
    reason: str,
  ) -> None:
    """Keep the entry gate closed until durable broker reports are replayed."""

    reconcile_metadata = {
      **metadata,
      "approval_reason": reason,
    }
    await self._notify_strategy_order(
      runtime,
      OrderStateEvent(
        order_id=order_id or None,
        status="RECONCILE_REQUIRED",
        metadata=reconcile_metadata,
      ),
    )
    self._checkpoint_restored_strategy_state(runtime)
    self._runtime_log(
      runtime,
      "WARNING",
      "待确认信号需要等待持久化券商回报收敛，保持禁止新单: "
      f"intent_id={intent_id}, order_id={order_id or '-'}, reason={reason}",
    )

  @staticmethod
  def _checkpoint_restored_strategy_state(runtime: StrategyRuntime) -> None:
    """Mirror startup callback changes before state-sync subscription begins."""

    if not runtime.strategy or not runtime.state_manager:
      return
    capture_for_persistence = getattr(
      runtime.state_manager,
      "capture_strategy_state_for_persistence",
      None,
    )
    if callable(capture_for_persistence):
      # The pre-subscription startup path is still a durable boundary.  The
      # manager must retain the full in-memory source and separately stage the
      # compact projection, otherwise a recovery callback can reintroduce the
      # T-trade hot sample window before normal state sync starts.
      capture_for_persistence(runtime.strategy)
      return
    snapshot = runtime.strategy.state.to_dict()
    update_strategy_custom_state = getattr(
      runtime.state_manager,
      "update_strategy_custom_state",
      None,
    )
    if callable(update_strategy_custom_state):
      update_strategy_custom_state(
        snapshot,
        full_snapshot=True,
      )
      return
    update_custom_state = getattr(
      runtime.state_manager,
      "update_custom_state",
      None,
    )
    if callable(update_custom_state):
      update_custom_state(snapshot)

  def _restore_t_trade_entry_reservations(self, runtime: StrategyRuntime) -> None:
    """Rebuild approved-but-unfinished T entry exposure after a restart."""
    if not runtime.strategy:
      return
    states = dict(runtime.strategy.state.get("instrument_states", {}) or {})
    for code, raw_state in states.items():
      state = dict(raw_state or {})
      status = str(state.get("entry_order_status", "") or "").upper()
      intent_id = str(state.get("pending_entry_intent_id", "") or "")
      if not intent_id or status not in {
        "PENDING",
        "SUBMITTED",
        "ACCEPTED",
        "PARTIAL_FILLED",
        "RECONCILE_REQUIRED",
      }:
        continue
      requested_amount = float(state.get("requested_entry_amount", 0.0) or 0.0)
      if requested_amount <= 0:
        continue
      opportunity = dict(state.get("opportunity", {}) or {})
      evaluation = dict(opportunity.get("latest_evaluation", {}) or {})
      features = dict(evaluation.get("features", {}) or {})
      price = float(
        features.get("price", 0.0)
        or state.get("last_price", 0.0)
        or state.get("entry_avg_price", 0.0)
        or 0.0
      )
      runtime.t_trade_entry_reservations[intent_id] = {
        "instrument_code": str(code),
        "batch_id": state.get("batch_id"),
        "requested_volume": 0,
        "volume": 0,
        "requested_amount": requested_amount,
        "price": price,
        "amount": requested_amount,
      }

  async def reject_trade_intent(
    self, run_id: str, intent_id: str, reason: str = "USER_REJECTED"
  ) -> Dict[str, Any]:
    """Reject one manual-confirm intent without creating any broker order."""

    runtime = self.runs.get(run_id)
    if runtime is None:
      return {"success": False, "code": "RUN_NOT_FOUND", "message": "策略运行不存在"}
    async with runtime.approval_lock:
      intent = runtime.pending_approvals.get(intent_id)
      if intent is None:
        return {
          "success": False,
          "code": "INTENT_NOT_AWAITING_APPROVAL",
          "message": "信号不存在、已处理或已过期",
        }
      persistence_failure = await self._terminalize_pending_approval_for_request(
        runtime,
        intent,
        status="REJECTED",
        reason=reason,
        message="用户已忽略本次交易信号",
      )
      if persistence_failure is not None:
        return persistence_failure
      return {"success": True, "code": "REJECTED", "message": "信号已忽略"}

  async def cancel_open_buy_orders(self, run_id: str, reason: str) -> int:
    """Request durable cancellation while preserving late broker fills."""

    async with AsyncSessionLocal() as db:
      requests = await TradeCommandService(db).request_strategy_buy_cancellations(
        strategy_run_id=run_id,
        reason=reason,
      )
    runtime = self.runs.get(run_id)
    if runtime is None:
      return len(requests)
    async with runtime.approval_lock:
      for cancellation in requests:
        if not cancellation.local_terminal:
          continue
        order_id = cancellation.strategy_order_id or cancellation.client_order_id
        cancellation_metadata = dict(cancellation.request_metadata or {})
        is_managed_entry = (
          str(cancellation_metadata.get("entry_plan_id") or "") == run_id
        )
        if runtime.state_manager:
          runtime.state_manager.release_order_resources(order_id)
          if cancellation.intent_id:
            if is_managed_entry:
              terminal_reason = str(
                cancellation_metadata.get("execution_terminal_reason")
                or "ENTRY_PLAN_CANCELLED_BEFORE_AGENT_DELIVERY"
              )
              await runtime.state_manager.update_trade_intent_status(
                cancellation.intent_id,
                "RECONCILED_ZERO_FILL",
                metadata=cancellation_metadata,
                notes=terminal_reason,
              )
            else:
              await runtime.state_manager.update_trade_intent_status(
                cancellation.intent_id,
                "CANCELLED",
                order_id=order_id,
                notes=reason,
              )
        await self._notify_strategy_order(
          runtime,
          OrderStateEvent(
            order_id=order_id,
            status="RECONCILED_ZERO_FILL",
            metadata={
              **cancellation_metadata,
              "intent_id": cancellation.intent_id,
            },
          ),
        )
      return len(requests)

  def _v3_t_trade_candidate_approval_failure(
    self,
    runtime: StrategyRuntime,
    intent: TradeIntent,
    *,
    approval_expectation: Optional[Mapping[str, Any]],
  ) -> Optional[tuple[str, str, bool]]:
    """Validate the V3 candidate CAS token and current opportunity snapshot.

    The boolean in the return value indicates whether durable intent truth must
    also be expired. A stale client token does not invalidate the authoritative
    candidate; a mismatch against current strategy state does.
    """

    metadata = dict(intent.metadata or {})
    is_v3_t_entry = bool(
      intent.direction == TradeIntentDirection.BUY
      and str(metadata.get("t_trade_role") or "").lower() == "entry"
      and self._safe_non_negative_int(
        metadata.get("opportunity_schema_version"), default=0
      )
      >= 3
    )
    if not is_v3_t_entry:
      return None

    expected = dict(approval_expectation or {})
    expected_signal_version = self._safe_non_negative_int(
      expected.get("signal_version"), default=0
    )
    expected_candidate_id = str(expected.get("candidate_id") or "").strip()
    expected_fingerprint = str(expected.get("candidate_fingerprint") or "").strip()
    expected_state_version = self._safe_non_negative_int(
      expected.get("candidate_state_version"), default=0
    )
    expected_config_version_raw = expected.get("config_version")
    expected_policy_version = str(expected.get("policy_version") or "").strip()
    if (
      expected_signal_version <= 0
      or not expected_candidate_id
      or not expected_fingerprint
      or expected_state_version <= 0
      or expected_config_version_raw is None
      or not expected_policy_version
    ):
      return (
        "T_TRADE_APPROVAL_EXPECTATION_REQUIRED",
        "确认请求缺少完整的候选指纹与版本，请刷新最新信号后重试",
        False,
      )
    expected_config_version = self._safe_non_negative_int(
      expected_config_version_raw, default=0
    )

    actual_signal_version = self._safe_non_negative_int(
      metadata.get("signal_version"), default=0
    )
    actual_candidate_id = str(metadata.get("candidate_id") or "").strip()
    actual_fingerprint = str(metadata.get("candidate_fingerprint") or "").strip()
    actual_state_version = self._safe_non_negative_int(
      metadata.get("candidate_state_version"), default=0
    )
    actual_config_version = self._safe_non_negative_int(
      metadata.get("config_version"), default=0
    )
    actual_policy_version = str(metadata.get("policy_version") or "").strip()
    if (
      actual_signal_version <= 0
      or not actual_candidate_id
      or not actual_fingerprint
      or actual_state_version <= 0
      or "config_version" not in metadata
      or not actual_policy_version
      or str(metadata.get("candidate_status") or "").upper() != "AWAITING_APPROVAL"
    ):
      return (
        "T_TRADE_CANDIDATE_IDENTITY_INVALID",
        "待确认意图缺少可验证的 V3 候选身份，已保守失效",
        True,
      )

    client_comparisons = (
      (
        expected_signal_version == actual_signal_version,
        "T_TRADE_SIGNAL_VERSION_MISMATCH",
        "信号版本已变化，请刷新后确认最新候选",
      ),
      (
        expected_candidate_id == actual_candidate_id,
        "T_TRADE_CANDIDATE_ID_MISMATCH",
        "候选已变化，请刷新后确认最新候选",
      ),
      (
        expected_fingerprint == actual_fingerprint,
        "T_TRADE_CANDIDATE_FINGERPRINT_MISMATCH",
        "候选指纹不一致，请刷新后确认最新候选",
      ),
      (
        expected_state_version == actual_state_version,
        "T_TRADE_CANDIDATE_STATE_VERSION_MISMATCH",
        "候选状态版本已变化，请刷新后重试",
      ),
      (
        expected_config_version == actual_config_version,
        "T_TRADE_CONFIG_VERSION_MISMATCH",
        "做 T 参数版本已变化，请刷新最新信号",
      ),
      (
        expected_policy_version == actual_policy_version,
        "T_TRADE_POLICY_VERSION_MISMATCH",
        "机会策略版本已变化，请刷新最新信号",
      ),
    )
    for matched, code, message in client_comparisons:
      if not matched:
        return code, message, False

    if runtime.strategy is None:
      return (
        "T_TRADE_CANDIDATE_STATE_MISSING",
        "策略候选状态不可用，已保守失效",
        True,
      )
    raw_instrument_states = runtime.strategy.state.get("instrument_states", {})
    instrument_states = (
      dict(raw_instrument_states) if isinstance(raw_instrument_states, Mapping) else {}
    )
    raw_instrument_state = instrument_states.get(intent.instrument_code)
    instrument_state = (
      dict(raw_instrument_state) if isinstance(raw_instrument_state, Mapping) else {}
    )
    raw_opportunity = instrument_state.get("opportunity")
    opportunity = dict(raw_opportunity) if isinstance(raw_opportunity, Mapping) else {}
    raw_candidate = opportunity.get("candidate")
    candidate = dict(raw_candidate) if isinstance(raw_candidate, Mapping) else {}
    if not opportunity or not candidate:
      return (
        "T_TRADE_CANDIDATE_STATE_MISSING",
        "当前机会候选状态不可用，已保守失效",
        True,
      )

    if (
      str(instrument_state.get("pending_entry_intent_id") or "") != intent.intent_id
      or str(instrument_state.get("entry_order_status") or "").upper()
      != "AWAITING_APPROVAL"
      or str(opportunity.get("candidate_status") or "").upper() != "AWAITING_APPROVAL"
      or opportunity.get("candidate_awaiting_approval") is not True
    ):
      return (
        "T_TRADE_CANDIDATE_NOT_AWAITING_APPROVAL",
        "该意图已不是当前等待确认的机会候选，已保守失效",
        True,
      )

    if (
      str(candidate.get("candidate_id") or "") != actual_candidate_id
      or str(candidate.get("fingerprint") or "") != actual_fingerprint
      or self._safe_non_negative_int(opportunity.get("state_version"), default=0)
      != actual_state_version
    ):
      return (
        "T_TRADE_CANDIDATE_NOT_LATEST",
        "待确认意图与当前最新候选不一致，已保守失效",
        True,
      )
    if (
      "config_version" not in opportunity
      or self._safe_non_negative_int(opportunity.get("config_version"), default=0)
      != actual_config_version
    ):
      return (
        "T_TRADE_CONFIG_VERSION_CHANGED",
        "当前做 T 参数版本已变化，旧候选已保守失效",
        True,
      )
    if str(opportunity.get("policy_version") or "") != actual_policy_version:
      return (
        "T_TRADE_POLICY_VERSION_CHANGED",
        "当前机会策略版本已变化，旧候选已保守失效",
        True,
      )

    raw_evaluation = opportunity.get("latest_evaluation")
    evaluation = dict(raw_evaluation) if isinstance(raw_evaluation, Mapping) else {}
    if not evaluation:
      return (
        "T_TRADE_REVALIDATION_UNAVAILABLE",
        "当前机会重验快照不可用，已保守失效",
        True,
      )
    if (
      str(evaluation.get("candidate_id") or "") != actual_candidate_id
      or str(evaluation.get("candidate_fingerprint") or "") != actual_fingerprint
      or str(evaluation.get("policy_version") or "") != actual_policy_version
    ):
      return (
        "T_TRADE_CANDIDATE_NOT_LATEST",
        "当前重验快照不属于待确认候选，已保守失效",
        True,
      )
    candidate_path = str(candidate.get("path") or "").strip().upper()
    evaluation_path = str(evaluation.get("selected_path") or "").strip().upper()
    if not candidate_path or evaluation_path != candidate_path:
      return (
        "T_TRADE_CANDIDATE_PATH_MISMATCH",
        "当前重验路径与待确认候选路径不一致，候选已保守失效",
        True,
      )
    if str(evaluation.get("data_health") or "").upper() != "READY":
      return (
        "T_TRADE_DATA_HEALTH_NOT_READY",
        "当前行情数据健康状态不是 READY，候选已保守失效",
        True,
      )

    try:
      score = float(evaluation.get("opportunity_score"))
      revalidate_score = float(opportunity.get("revalidate_score"))
    except (TypeError, ValueError, OverflowError):
      score = float("nan")
      revalidate_score = float("nan")
    if not isfinite(score) or not isfinite(revalidate_score):
      return (
        "T_TRADE_REVALIDATE_SCORE_UNAVAILABLE",
        "当前机会分数或重验阈值不可用，候选已保守失效",
        True,
      )
    if score < revalidate_score:
      return (
        "T_TRADE_REVALIDATE_SCORE_BELOW_FLOOR",
        "当前机会分数已低于确认阈值，候选已保守失效",
        True,
      )

    hard_gates = evaluation.get("hard_gates")
    if not isinstance(hard_gates, (list, tuple)) or not hard_gates:
      return (
        "T_TRADE_HARD_GATES_UNAVAILABLE",
        "当前硬门禁快照不可用，候选已保守失效",
        True,
      )
    failed_gate_codes = []
    for gate in hard_gates:
      if not isinstance(gate, Mapping):
        failed_gate_codes.append("UNKNOWN_GATE")
        continue
      item = dict(gate)
      if item.get("passed") is not True:
        failed_gate_codes.append(str(item.get("code") or "UNKNOWN_GATE"))
    if failed_gate_codes:
      return (
        "T_TRADE_HARD_GATE_BLOCKED",
        f"当前硬门禁未通过：{', '.join(failed_gate_codes)}",
        True,
      )
    blockers = evaluation.get("blockers")
    if not isinstance(blockers, (list, tuple)):
      return (
        "T_TRADE_REVALIDATION_UNAVAILABLE",
        "当前机会阻断项快照不可用，候选已保守失效",
        True,
      )
    normalized_blockers = [str(item) for item in blockers if str(item)]
    if normalized_blockers:
      return (
        "T_TRADE_REVALIDATION_BLOCKED",
        f"当前机会已被阻断：{', '.join(normalized_blockers)}",
        True,
      )
    return None

  async def _v3_t_trade_durable_config_failure(
    self,
    runtime: StrategyRuntime,
    intent: TradeIntent,
  ) -> Optional[tuple[str, str, bool]]:
    """Compare a global-monitor candidate with current durable configuration.

    Runtime candidate metadata alone cannot close the interval between a
    successful config commit and the serial runtime rewarm.  The shared account
    lock linearizes normal saves with approval; this database check is the
    fail-closed guard for reconcile failures and process recovery.
    """

    parameters = dict(runtime.context.parameters or {})
    monitor_id = str(parameters.get("global_monitor_id") or "").strip()
    account_id = str(parameters.get("account_id") or "").strip()
    metadata = dict(intent.metadata or {})
    try:
      schema_version = int(metadata.get("opportunity_schema_version") or 0)
      candidate_config_version = int(metadata.get("config_version") or 0)
    except (TypeError, ValueError, OverflowError):
      schema_version = 0
      candidate_config_version = -1
    is_global_v3_entry = bool(
      monitor_id
      and account_id
      and schema_version >= 3
      and intent.direction == TradeIntentDirection.BUY
      and str(metadata.get("t_trade_role") or "").lower() == "entry"
    )
    if not is_global_v3_entry or runtime.context.mode == StrategyRunMode.BACKTEST:
      return None

    try:
      async with AsyncSessionLocal() as db:
        result = await db.execute(
          select(TTradeGlobalConfig).where(TTradeGlobalConfig.id == monitor_id)
        )
        config = result.scalar_one_or_none()
    except Exception as exc:
      self._runtime_log(
        runtime,
        "ERROR",
        f"做 T 审批无法读取权威配置，已保守阻断: account={account_id} error={exc}",
      )
      return (
        "T_TRADE_DURABLE_CONFIG_UNAVAILABLE",
        "当前无法校验做 T 权威参数版本，请稍后重试",
        False,
      )

    if config is None or str(config.account_id or "").strip() != account_id:
      return (
        "T_TRADE_DURABLE_CONFIG_MISSING",
        "做 T 权威配置不存在或账户不匹配，旧候选已保守失效",
        True,
      )
    if not bool(config.enabled):
      return (
        "T_TRADE_GLOBAL_MONITOR_DISABLED",
        "做 T 全局监控已关闭，旧候选已保守失效",
        True,
      )
    if str(config.strategy_run_id or "") != runtime.run_id:
      return (
        "T_TRADE_GLOBAL_RUN_CHANGED",
        "做 T 权威运行实例已变化，旧候选已保守失效",
        True,
      )
    if str(config.mode or "").lower() != runtime.context.mode.value.lower():
      return (
        "T_TRADE_GLOBAL_MODE_CHANGED",
        "做 T 运行模式已变化，旧候选已保守失效",
        True,
      )
    if int(config.config_version or 0) != candidate_config_version:
      return (
        "T_TRADE_CONFIG_VERSION_CHANGED",
        "当前做 T 参数版本已变化，旧候选已保守失效",
        True,
      )
    return None

  @staticmethod
  def _v3_t_trade_expectation_from_intent(
    intent: TradeIntent,
  ) -> Optional[Dict[str, Any]]:
    """Build the same CAS token for deterministic internal backtest approval."""

    metadata = dict(intent.metadata or {})
    try:
      schema_version = int(metadata.get("opportunity_schema_version") or 0)
    except (TypeError, ValueError, OverflowError):
      schema_version = 0
    if (
      intent.direction != TradeIntentDirection.BUY
      or str(metadata.get("t_trade_role") or "").lower() != "entry"
      or schema_version < 3
    ):
      return None
    return {
      "signal_version": metadata.get("signal_version"),
      "candidate_id": metadata.get("candidate_id"),
      "candidate_fingerprint": metadata.get("candidate_fingerprint"),
      "candidate_state_version": metadata.get("candidate_state_version"),
      "config_version": metadata.get("config_version"),
      "policy_version": metadata.get("policy_version"),
    }

  @staticmethod
  def _execution_quote_max_age_seconds(
    runtime: StrategyRuntime,
    intent: TradeIntent,
  ) -> Optional[float]:
    metadata = dict(intent.metadata or {})
    is_t_manual_entry = bool(
      intent.direction == TradeIntentDirection.BUY
      and intent.execution_mode == TradeIntentExecutionMode.MANUAL_CONFIRM
      and str(metadata.get("t_trade_role") or "").lower() == "entry"
    )
    try:
      opportunity_schema_version = int(metadata.get("opportunity_schema_version") or 0)
    except (TypeError, ValueError, OverflowError):
      opportunity_schema_version = 0
    if is_t_manual_entry and opportunity_schema_version >= 3:
      raw_policy = dict(runtime.context.parameters or {}).get("signal_policy")
      if not isinstance(raw_policy, Mapping):
        return None
      try:
        policy = OpportunityPolicy.from_dict(raw_policy)
      except (TypeError, ValueError):
        return None
      candidate_policy_version = str(metadata.get("policy_version") or "").strip()
      if (
        not candidate_policy_version
        or candidate_policy_version != policy.policy_version
      ):
        return None
      return policy.max_quote_age_ms / 1000.0
    default = (
      _T_TRADE_DEFAULT_EXECUTION_QUOTE_MAX_AGE_SECONDS if is_t_manual_entry else 0.0
    )
    raw_value = dict(runtime.context.parameters or {}).get(
      "execution_quote_max_age_seconds",
      default,
    )
    try:
      value = float(raw_value or 0.0)
    except (TypeError, ValueError, OverflowError):
      value = 0.0
    if is_t_manual_entry and not value > 0:
      return _T_TRADE_DEFAULT_EXECUTION_QUOTE_MAX_AGE_SECONDS
    return max(0.0, value)

  def _approval_failure(
    self, runtime: StrategyRuntime, intent: TradeIntent
  ) -> Optional[tuple[str, str]]:
    approval_at = self._runtime_now(runtime)
    metadata = dict(intent.metadata or {})
    continuity_failure = self._runtime_market_continuity_failure(
      runtime,
      intent.instrument_code,
    )
    if continuity_failure is not None:
      return continuity_failure
    if str(metadata.get("entry_plan_id") or "") == runtime.run_id:
      parameters = dict(runtime.context.parameters or {})
      if parameters.get("entry_plan_enabled") is not True:
        return "ENTRY_PLAN_PAUSED", "买入计划已暂停或取消，不能确认旧意图"
      managed_entry = dict(parameters.get("managed_entry_plan") or {})
      if int(metadata.get("entry_config_version") or 0) != int(
        managed_entry.get("config_version") or 0
      ):
        return "ENTRY_PLAN_CONFIG_CHANGED", "计划配置已变化，不能确认旧意图"
    expiry_policy = dict(intent.expiry_policy or {})
    try:
      expire_at_ms = int(expiry_policy.get("expire_at_ms", 0) or 0)
    except (TypeError, ValueError):
      expire_at_ms = 0
    if expire_at_ms > 0 and int(approval_at.timestamp() * 1000) >= expire_at_ms:
      return "APPROVAL_TTL_EXPIRED", "信号已超过确认有效期，请等待新信号"

    ttl_ms = int(intent.approval_ttl_ms or 0)
    if ttl_ms > 0 and expire_at_ms <= 0:
      created_at = intent.created_at
      if created_at.tzinfo is None and approval_at.tzinfo is not None:
        created_at = created_at.replace(tzinfo=approval_at.tzinfo)
      if created_at.tzinfo is not None and approval_at.tzinfo is None:
        approval_at = approval_at.replace(tzinfo=created_at.tzinfo)
      elapsed_ms = max(
        0.0,
        (approval_at - created_at).total_seconds() * 1000,
      )
      if elapsed_ms >= ttl_ms:
        return "APPROVAL_TTL_EXPIRED", "信号已超过确认有效期，请等待新信号"

    market_data = runtime.latest_market_data.get(intent.instrument_code)
    quote_max_age = self._execution_quote_max_age_seconds(runtime, intent)
    if quote_max_age is None:
      return (
        "T_TRADE_SIGNAL_POLICY_INVALID",
        "当前做 T 信号策略缺失、无效或版本不匹配，不能确认该候选",
      )
    if intent.direction == TradeIntentDirection.BUY and quote_max_age > 0:
      if market_data is None:
        return "APPROVAL_QUOTE_MISSING", "确认时缺少最新执行行情，请等待新信号"
      quote_at = getattr(market_data, "timestamp", None)
      if not isinstance(quote_at, datetime):
        try:
          quote_at = datetime.fromisoformat(str(quote_at).replace("Z", "+00:00"))
        except (TypeError, ValueError):
          return "APPROVAL_QUOTE_STALE", "确认时执行行情时间无效，请等待新信号"
      if quote_at.tzinfo is None and approval_at.tzinfo is not None:
        quote_at = quote_at.replace(tzinfo=approval_at.tzinfo)
      if quote_at.tzinfo is not None and approval_at.tzinfo is None:
        approval_at = approval_at.replace(tzinfo=quote_at.tzinfo)
      quote_age = (approval_at - quote_at).total_seconds()
      if quote_age < -MARKET_STREAM_MAX_FUTURE_SKEW_SECONDS:
        return (
          "APPROVAL_QUOTE_TIME_INVALID",
          "确认时执行行情时间晚于运行时钟，请等待行情恢复",
        )
      if quote_age > quote_max_age:
        return "APPROVAL_QUOTE_STALE", "确认时执行行情已超过有效期，请等待新信号"
    reference_price = float(intent.limit_price_hint or 0.0)
    current_price = float(getattr(market_data, "price", 0.0) or 0.0)
    if intent.direction == TradeIntentDirection.BUY and market_data:
      asks = list(getattr(market_data, "ask_price", []) or [])
      current_price = float(asks[0] if asks and asks[0] else current_price)
    if intent.direction == TradeIntentDirection.SELL and market_data:
      bids = list(getattr(market_data, "bid_price", []) or [])
      current_price = float(bids[0] if bids and bids[0] else current_price)
    max_deviation_bps = float(intent.max_price_deviation_bps or 0.0)
    if reference_price > 0 and current_price > 0 and max_deviation_bps > 0:
      deviation_bps = abs(current_price - reference_price) / reference_price * 10000
      if deviation_bps > max_deviation_bps:
        return "PRICE_DEVIATION_EXCEEDED", "价格已偏离信号价，请等待新信号"
    if runtime.strategy is not None:
      strategy_failure = runtime.strategy.validate_manual_approval(
        intent,
        market_data,
      )
      if strategy_failure is not None:
        return strategy_failure
    return None

  def _t_trade_portfolio_approval_failure(
    self, runtime: StrategyRuntime, intent: TradeIntent
  ) -> Optional[tuple[str, str]]:
    metadata = dict(intent.metadata or {})
    if (
      intent.direction != TradeIntentDirection.BUY
      or metadata.get("t_trade_role") != "entry"
    ):
      return None

    params = dict(runtime.context.parameters or {})
    market_data = runtime.latest_market_data.get(intent.instrument_code)
    asks = list(getattr(market_data, "ask_price", []) or []) if market_data else []
    current_price = float(
      (asks[0] if asks and asks[0] else 0.0)
      or getattr(market_data, "price", 0.0)
      or intent.limit_price_hint
      or 0.0
    )
    requested_volume = int(intent.target_volume or 0)
    requested_amount = float(intent.target_amount or 0.0)
    if requested_amount <= 0 and requested_volume > 0:
      requested_amount = current_price * requested_volume
    max_trade_amount = float(params.get("max_trade_amount", 12_000.0) or 12_000.0)
    if not isfinite(current_price) or current_price <= 0:
      return (
        "T_TRADE_PORTFOLIO_SNAPSHOT_STALE",
        "最新可执行价格不可用，暂不允许确认新批次",
      )
    if not isfinite(requested_amount) or requested_amount <= 0:
      return (
        "T_TRADE_PORTFOLIO_SNAPSHOT_STALE",
        "目标金额不可用，暂不允许确认新批次",
      )
    if requested_amount > max_trade_amount + 1e-6:
      return (
        "T_TRADE_SINGLE_AMOUNT_LIMIT",
        f"按最新卖一价计算将超过单次金额硬上限 ¥{max_trade_amount:,.2f}",
      )
    # Rebuild the full current emission decision instead of trusting the
    # candidate's historical external-blocker snapshot.  This includes the
    # latest universe/ignore/holding eligibility entry and all four account
    # facts, while excluding this intent from its own pending check.
    emission_gate = self._t_trade_intent_emission_context(
      runtime,
      intent.instrument_code,
      requested_amount=requested_amount,
      current_intent_id=intent.intent_id,
      check_coordination_lock=False,
    )
    if not emission_gate.get("allowed"):
      blockers = self._t_trade_unique_blockers(emission_gate.get("blockers"))
      code = blockers[0] if blockers else "T_TRADE_INTENT_EMISSION_BLOCKED"
      return (
        code,
        "当前做 T 入场门禁未通过："
        + (", ".join(blockers) if blockers else code),
      )
    facts = self._t_trade_account_facts(
      runtime,
      instrument_code=intent.instrument_code,
      requested_amount=requested_amount,
      current_intent_id=intent.intent_id,
    )
    if facts.blockers:
      return (
        facts.blockers[0],
        facts.message or "账户做 T 快照不可用，暂不允许确认新批次",
      )
    if facts.reconciliation_required:
      return (
        "T_TRADE_RECONCILIATION_REQUIRED",
        "存在尚未由持久化券商回报收敛的做 T 委托，暂不允许确认新批次",
      )
    if facts.same_instrument_pending_intent_exists:
      return (
        "T_TRADE_SAME_INSTRUMENT_PENDING_INTENT_EXISTS",
        "同一标的已有待处理做 T 入场意图，暂不允许重复确认",
      )
    if facts.account_concurrent_batch_limit_reached:
      max_batches = max(1, int(params.get("max_concurrent_batches", 3) or 3))
      return (
        "T_TRADE_ACCOUNT_CONCURRENT_BATCH_LIMIT_REACHED",
        f"账户级做 T 批次已达到上限（{max_batches} 个），信号仍保留至过期",
      )
    if facts.account_total_exposure_limit_reached:
      max_exposure_pct = float(
        params.get("max_total_t_exposure_pct", 0.1) or 0.1
      )
      return (
        "T_TRADE_ACCOUNT_TOTAL_EXPOSURE_LIMIT_REACHED",
        f"确认后将超过账户总资产 {max_exposure_pct * 100:g}% 的做 T 敞口上限",
      )
    return None

  def _reserve_t_trade_entry_exposure(
    self, runtime: StrategyRuntime, intent: TradeIntent
  ) -> None:
    metadata = dict(intent.metadata or {})
    if (
      intent.direction != TradeIntentDirection.BUY
      or metadata.get("t_trade_role") != "entry"
    ):
      return
    market_data = runtime.latest_market_data.get(intent.instrument_code)
    asks = list(getattr(market_data, "ask_price", []) or []) if market_data else []
    price = float(
      (asks[0] if asks and asks[0] else 0.0)
      or getattr(market_data, "price", 0.0)
      or intent.limit_price_hint
      or 0.0
    )
    runtime.t_trade_entry_reservations[intent.intent_id] = {
      "instrument_code": intent.instrument_code,
      "batch_id": metadata.get("t_batch_id"),
      "requested_volume": int(intent.target_volume or 0),
      "volume": int(intent.target_volume or 0),
      "requested_amount": float(intent.target_amount or 0.0),
      "price": price,
      "amount": float(intent.target_amount or 0.0)
      or price * int(intent.target_volume or 0),
    }

  def _update_t_trade_entry_reservation(
    self, runtime: StrategyRuntime, order: Any
  ) -> None:
    request = self._get_value(order, "request")
    metadata = dict(self._get_value(order, "metadata", {}) or {})
    if not metadata:
      metadata = dict(self._get_value(request, "metadata", {}) or {})
    if metadata.get("t_trade_role") != "entry":
      return
    intent_id = str(metadata.get("intent_id", "") or "")
    if not intent_id or intent_id not in runtime.t_trade_entry_reservations:
      return
    reservation = runtime.t_trade_entry_reservations[intent_id]
    requested_volume = int(
      reservation.get("requested_volume", 0)
      or self._get_value(request, "volume", 0)
      or 0
    )
    if requested_volume > 0:
      reservation["requested_volume"] = requested_volume
      reservation["volume"] = requested_volume
      reservation["amount"] = requested_volume * float(
        reservation.get("price", 0.0) or 0.0
      )
    raw_status = self._get_value(order, "status", "")
    status = str(getattr(raw_status, "value", raw_status)).upper()
    if status not in {"FILLED", "REJECTED", "CANCELLED", "EXPIRED"}:
      return
    filled_volume = int(self._get_value(order, "filled_volume", 0) or 0)
    terminal_volume = requested_volume if status == "FILLED" else filled_volume
    if terminal_volume <= 0:
      runtime.t_trade_entry_reservations.pop(intent_id, None)
      return
    reservation["terminal_status"] = status
    reservation["terminal_filled_volume"] = terminal_volume

  def _refresh_t_trade_entry_reservation(
    self, runtime: StrategyRuntime, report: Any
  ) -> None:
    """Keep exposure reserved until trade details are reflected in strategy state."""

    request = self._get_value(report, "request")
    metadata = dict(self._get_value(report, "metadata", {}) or {})
    if not metadata:
      metadata = dict(self._get_value(request, "metadata", {}) or {})
    if metadata.get("t_trade_role") != "entry":
      return
    intent_id = str(metadata.get("intent_id", "") or "")
    reservation = runtime.t_trade_entry_reservations.get(intent_id)
    if reservation is None:
      batch_id = str(metadata.get("t_batch_id", "") or "")
      instrument_code = str(
        self._get_value(report, "instrument_code", "")
        or metadata.get("instrument_code", "")
        or self._get_value(request, "instrument_code", "")
        or ""
      )
      for candidate_id, candidate in runtime.t_trade_entry_reservations.items():
        if batch_id and str(candidate.get("batch_id", "") or "") == batch_id:
          intent_id, reservation = candidate_id, candidate
          break
        candidate_code = str(candidate.get("instrument_code", "") or "")
        if instrument_code and candidate_code == instrument_code:
          intent_id, reservation = candidate_id, candidate
          break
    if reservation is None or not runtime.strategy:
      return

    code = str(reservation.get("instrument_code", "") or "")
    state = dict(
      dict(runtime.strategy.state.get("instrument_states", {}) or {}).get(code, {})
      or {}
    )
    reflected_volume = max(0, int(state.get("entry_filled_volume", 0) or 0))
    requested_volume = max(
      0,
      int(
        reservation.get("requested_volume", 0)
        or self._get_value(request, "volume", 0)
        or reservation.get("volume", 0)
        or 0
      ),
    )
    terminal_status = str(reservation.get("terminal_status", "") or "")
    terminal_volume = max(
      0,
      int(reservation.get("terminal_filled_volume", 0) or 0),
    )
    if terminal_status and reflected_volume >= terminal_volume:
      runtime.t_trade_entry_reservations.pop(intent_id, None)
      return
    if requested_volume <= 0 and not terminal_status:
      # V3 reserves target_amount before OrderSizer derives a legal lot count.
      return
    remaining = max(
      0,
      (terminal_volume if terminal_status else requested_volume) - reflected_volume,
    )
    reservation["volume"] = remaining
    reservation["amount"] = remaining * float(reservation.get("price", 0.0) or 0.0)

  async def _expire_pending_approvals(self, runtime: StrategyRuntime) -> None:
    for intent in list(runtime.pending_approvals.values()):
      failure = self._approval_failure(runtime, intent)
      if failure and failure[0] == "APPROVAL_TTL_EXPIRED":
        await self._reject_pending_approval(
          runtime,
          intent,
          status="EXPIRED",
          reason=failure[0],
          message=failure[1],
        )

  async def _cancel_expired_strategy_orders(
    self,
    runtime: StrategyRuntime,
    _timestamp: datetime,
  ) -> None:
    """Request cancellation for live/paper orders whose strategy TTL elapsed.

    BacktestBroker applies the same rule inside its deterministic market update
    so the expiry event is ordered before any fill on the next quote.
    """

    broker = runtime.broker
    if not broker or runtime.context.mode == StrategyRunMode.BACKTEST:
      return
    now_ms = int(time_utils.now().timestamp() * 1000)
    for order_id, order in list(getattr(broker, "orders", {}).items()):
      raw_status = getattr(order, "status", "")
      status = str(getattr(raw_status, "value", raw_status)).upper()
      if status not in {
        "PENDING",
        "SUBMITTED",
        "ACCEPTED",
        "PARTIAL_FILLED",
      }:
        continue
      request = getattr(order, "request", None)
      metadata = dict(getattr(request, "metadata", {}) or {})
      try:
        expire_at_ms = int(metadata.get("order_expire_at_ms", 0) or 0)
      except (TypeError, ValueError):
        expire_at_ms = 0
      if (
        expire_at_ms <= 0
        or now_ms < expire_at_ms
        or metadata.get("expiry_cancel_requested")
      ):
        continue
      cancelled = await broker.cancel_order(order_id)
      if not cancelled:
        continue
      getattr(request, "metadata", {})["expiry_cancel_requested"] = True
      self._runtime_log(
        runtime,
        "INFO",
        f"策略委托已超过有效期，已请求撤单: order_id={order_id}",
      )

  async def _reject_pending_approval(
    self,
    runtime: StrategyRuntime,
    intent: TradeIntent,
    *,
    status: str,
    reason: str,
    message: str,
    strict_persistence: bool = False,
  ) -> None:
    requires_strict = strict_persistence or self._is_v3_t_trade_manual_intent(intent)
    if runtime.state_manager is None:
      if requires_strict:
        raise _PendingApprovalStatusPersistenceError(
          "待确认意图缺少状态持久化管理器"
        )
    else:
      updater = getattr(
        runtime.state_manager,
        (
          "update_trade_intent_status_strict"
          if requires_strict
          else "update_trade_intent_status"
        ),
        None,
      )
      if not callable(updater):
        error = "待确认意图缺少严格状态持久化边界"
        if requires_strict:
          raise _PendingApprovalStatusPersistenceError(error)
        raise RuntimeError(error)
      try:
        await updater(
          intent.intent_id,
          status,
          notes=reason,
        )
      except Exception as exc:
        if requires_strict:
          raise _PendingApprovalStatusPersistenceError(
            "待确认意图终结状态持久化失败"
          ) from exc
        raise
    runtime.pending_approvals.pop(intent.intent_id, None)
    await self._notify_strategy_order(
      runtime,
      OrderStateEvent(
        order_id=None,
        status=status,
        error_message=message,
        metadata={
          **dict(intent.metadata or {}),
          "intent_id": intent.intent_id,
          "approval_reason": reason,
        },
      ),
    )

  async def _terminalize_pending_approval_for_request(
    self,
    runtime: StrategyRuntime,
    intent: TradeIntent,
    *,
    status: str,
    reason: str,
    message: str,
    strict_persistence: bool = False,
  ) -> Optional[Dict[str, Any]]:
    """Return a stable request failure while preserving an uncommitted intent."""

    try:
      await self._reject_pending_approval(
        runtime,
        intent,
        status=status,
        reason=reason,
        message=message,
        strict_persistence=strict_persistence,
      )
    except _PendingApprovalStatusPersistenceError as exc:
      if runtime.metrics:
        runtime.metrics.error_count += 1
      self._runtime_log(
        runtime,
        "ERROR",
        "待确认信号终结状态持久化失败，保留待确认状态: "
        f"intent_id={intent.intent_id} status={status} error={exc}",
      )
      is_v3 = self._is_v3_t_trade_manual_intent(intent)
      return {
        "success": False,
        "code": (
          "T_TRADE_APPROVAL_STATUS_PERSIST_FAILED"
          if is_v3
          else "APPROVAL_STATUS_PERSIST_FAILED"
        ),
        "message": "信号状态保存失败，信号仍保持待确认，请稍后重试",
      }
    return None

  def _record_strategy_output_trace(
    self,
    runtime: StrategyRuntime,
    output: StrategyOutput,
    input_snapshot: Optional[StrategyInput],
  ) -> None:
    if not runtime.state_manager or input_snapshot is None:
      return
    patch = output.runtime_state_patch
    is_t_trade_opportunity = bool(
      getattr(runtime.strategy, "USES_T_TRADE_OPPORTUNITY_PROFILE", False)
    )
    if is_t_trade_opportunity:
      if not _t_trade_output_requires_material_trace(output):
        # A normal T Tick is reconstructible from the authoritative market
        # cache/history source plus the durable processed watermark.  Do not
        # create a DecisionTrace, canonical JSON, hash, or alternate diagnostic
        # payload for it.
        return
      # T opportunity evidence has a dedicated relational materialization
      # path. Build its bounded causal index directly; do not construct the
      # generic full-root summaries or content hashes first.
      projected_trace = _build_t_trade_decision_trace_projection(
        input_snapshot=input_snapshot,
        output=output,
      )
      input_summary = projected_trace["input_summary"]
      environment = projected_trace["environment"]
      risk_caps = projected_trace["risk_caps"]
      position_profile = projected_trace["position_profile"]
      execution_profile = projected_trace["execution_profile"]
      output_summary = projected_trace["output_summary"]
      state_patch = projected_trace["state_patch"]
      intents = projected_trace["trade_intents"]
      trace_tags = projected_trace["tags"]
      trace_reason = projected_trace["reason"]
    else:
      state_patch = (
        _compact_runtime_state_patch_for_audit(
          patch,
          instrument_code=input_snapshot.instrument_code,
        )
        if patch
        else {}
      )
      intents = [summarize_intent(intent) for intent in output.trade_intents or []]
      input_summary = summarize_strategy_input(input_snapshot)
      environment = dict(input_snapshot.market_context or {})
      risk_caps = dict(input_snapshot.risk_caps or {})
      position_profile = dict(input_snapshot.position_profile or {})
      execution_profile = dict(input_snapshot.execution_profile or {})
      output_summary = {
        "trade_intent_count": len(intents),
        "decision_tags": list(output.decision_tags or []),
        "trace_payload": dict(output.trace_payload or {}),
      }
      trace_tags = ["strategy_output", *list(output.decision_tags or [])]
      trace_reason = (
        str((output.trace_payload or {}).get("reason") or "")
        or ("NO_TRADE_INTENT" if not intents else "TRADE_INTENT_GENERATED")
      )
    trace = DecisionTrace.from_decision(
      run_id=runtime.run_id,
      strategy_id=str(runtime.strategy_id),
      instrument_code=input_snapshot.instrument_code,
      input_summary=input_summary,
      environment=environment,
      risk_caps=risk_caps,
      position_profile=position_profile,
      execution_profile=execution_profile,
      output_summary=output_summary,
      state_patch=state_patch,
      trade_intents=intents,
      trace_id=input_snapshot.trace_id,
      tags=trace_tags,
      reason=trace_reason,
    )
    runtime.state_manager.record_decision_trace(trace)

  def _apply_runtime_state_patch(
    self,
    runtime: StrategyRuntime,
    patch,
    *,
    stage_actionable_material_events: bool = True,
  ) -> None:
    if not runtime.strategy or not patch:
      return
    raw_updates = getattr(patch, "set", None)
    raw_unset = getattr(patch, "unset", None)
    raw_events = getattr(patch, "append_events", None)
    updates_source = {} if raw_updates is None else raw_updates
    unset_source = [] if raw_unset is None else raw_unset
    events_source = [] if raw_events is None else raw_events

    # RuntimeStatePatch.__post_init__ is not a sufficient trust boundary: a
    # plugin can return a duck-typed object, and a valid dataclass remains
    # mutable after construction.  Validate every payload before the first
    # StrategyStateProxy write so rejection is atomic and fail-closed.
    validate_runtime_state_patch_contents(
      set_values=updates_source,
      append_events=events_source,
    )
    if not isinstance(unset_source, (list, tuple)) or any(
      not isinstance(key, str) for key in unset_source
    ):
      raise ValueError("RuntimeStatePatch.unset must be a list of strings")

    updates = dict(updates_source)
    unset = list(unset_source)
    events = [dict(event) for event in events_source]
    material_events = [
      event
      for event in events
      if event.get("type") == T_TRADE_OPPORTUNITY_EVALUATION_EVENT
      and str(event.get("record_kind") or "").upper() == "MATERIAL"
    ]
    if bool(getattr(runtime.strategy, "USES_T_TRADE_OPPORTUNITY_PROFILE", False)):
      # Dedicated evaluation/trace records and the PREPARED material outbox own
      # every opportunity evaluation.  Keep neither ordinary nor MATERIAL
      # evaluation payloads in the RuntimeState event ring.
      events = [
        event
        for event in events
        if event.get("type") != T_TRADE_OPPORTUNITY_EVALUATION_EVENT
      ]
    if (
      stage_actionable_material_events
      and material_events
      and runtime.context.mode in {
        StrategyRunMode.PAPER,
        StrategyRunMode.LIVE,
      }
    ):
      # Only a caller that is about to expose a candidate/intent/exit may stage
      # this legacy P/L recovery outbox.  A pure MATERIAL evaluation passes
      # ``False`` and remains in the session/day coordinator's hot memory.
      # This method does not await, so no state delta can overtake its immediate
      # business-fact recovery boundary.
      enqueue = getattr(
        runtime.state_manager,
        "enqueue_t_trade_material_events",
        None,
      )
      if not callable(enqueue):
        runtime.status = ExecutionStatus.ERROR
        runtime.error_message = "T_TRADE_MATERIAL_OUTBOX_UNAVAILABLE"
        raise RuntimeError("V3 做 T 运行缺少 MATERIAL durable outbox")
      try:
        enqueue(material_events)
      except Exception:
        runtime.status = ExecutionStatus.ERROR
        runtime.error_message = "T_TRADE_MATERIAL_OUTBOX_ENQUEUE_FAILED"
        raise
    if updates:
      runtime.strategy.state.update(updates)
    if unset:
      state = runtime.strategy.state.to_dict()
      for key in unset:
        state.pop(key, None)
      runtime.strategy.state.replace(state, notify=True)
    if events:
      existing = list(runtime.strategy.state.get("runtime_events", []) or [])
      existing.extend(events)
      runtime.strategy.state.runtime_events = existing[-200:]

  def apply_external_state_patch(self, run_id: str, patch) -> None:
    """Apply a state patch produced by an explicit, audited external action."""
    runtime = self.runs.get(run_id)
    if runtime is None or runtime.strategy is None:
      raise ValueError("策略运行不存在或尚未启动")
    self._apply_runtime_state_patch(runtime, patch)

  async def _reject_intent_for_market_continuity(
    self,
    runtime: StrategyRuntime,
    intent: TradeIntent,
    *,
    failure: tuple[str, str],
    reservation_key: Optional[str] = None,
  ) -> None:
    """Audit and converge an intent whose source market window is invalid."""

    code, message = failure
    if runtime.state_manager:
      if reservation_key:
        runtime.state_manager.release_order_resources(reservation_key)
      await runtime.state_manager.update_trade_intent_status(
        intent.intent_id,
        "REJECTED",
        metadata={
          **dict(intent.metadata or {}),
          "market_data_gate": code,
        },
        notes=code,
      )
    runtime.pending_approvals.pop(intent.intent_id, None)
    runtime.t_trade_entry_reservations.pop(intent.intent_id, None)
    await self._notify_strategy_order(
      runtime,
      OrderStateEvent(
        order_id=None,
        status=OrderStatus.REJECTED.value,
        error_message=message,
        metadata={
          **dict(intent.metadata or {}),
          "intent_id": intent.intent_id,
          "instrument_code": intent.instrument_code,
          "market_data_gate": code,
        },
      ),
    )
    self._runtime_log(
      runtime,
      "WARNING",
      f"行情连续性门禁拒绝交易意图: {intent.instrument_code} {code}",
    )

  async def _reject_intent_during_runtime_transition(
    self,
    runtime: StrategyRuntime,
    intent: TradeIntent,
    *,
    reservation_key: Optional[str] = None,
  ) -> None:
    """Converge an intent that lost the race with pause/stop before routing."""
    if runtime.state_manager:
      if reservation_key:
        runtime.state_manager.release_order_resources(reservation_key)
      await runtime.state_manager.update_trade_intent_status(
        intent.intent_id,
        "REJECTED",
        metadata={
          **dict(intent.metadata or {}),
          "runtime_gate": runtime.status.value,
        },
        notes=f"RUNTIME_{runtime.status.value}",
      )
    runtime.t_trade_entry_reservations.pop(intent.intent_id, None)
    await self._notify_strategy_order(
      runtime,
      OrderStateEvent(
        order_id=None,
        status=OrderStatus.REJECTED.value,
        error_message=f"策略运行已进入 {runtime.status.value}，未向券商提交委托",
        metadata={
          **dict(intent.metadata or {}),
          "intent_id": intent.intent_id,
          "instrument_code": intent.instrument_code,
          "runtime_gate": runtime.status.value,
        },
      ),
    )

  @staticmethod
  def _is_live_auto_managed_entry(
    runtime: StrategyRuntime,
    intent: TradeIntent,
  ) -> bool:
    metadata = dict(intent.metadata or {})
    return bool(
      runtime.context.mode == StrategyRunMode.LIVE
      and intent.direction == TradeIntentDirection.BUY
      and intent.execution_mode == TradeIntentExecutionMode.AUTO
      and str(metadata.get("entry_plan_id") or "") == runtime.run_id
      and str(metadata.get("owner_type") or "") == "STRATEGY_RUN"
      and str(metadata.get("owner_id") or "") == runtime.run_id
    )

  @staticmethod
  def _authorization_decimal(value: Any) -> Optional[Decimal]:
    try:
      normalized = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
      return None
    return normalized if normalized.is_finite() else None

  async def _authorize_live_auto_managed_entry(
    self,
    runtime: StrategyRuntime,
    intent: TradeIntent,
    request: OrderRequest,
    *,
    account: Dict[str, Any],
    position: Dict[str, Any],
  ) -> Optional[tuple[str, str]]:
    """Validate exact entry authority after final sizing and before routing.

    The metadata written here is only an audited correlation handle.  The
    durable command layer locks and validates the grant, plan, intent, account
    snapshot, position and global gate again before it creates an outbox row.
    """

    if not self._is_live_auto_managed_entry(runtime, intent):
      return None
    account_id = str(runtime.context.parameters.get("account_id") or "").strip()
    if not account_id:
      return ("ENTRY_ACCOUNT_REQUIRED", "实盘自动买入缺少唯一账户绑定")
    try:
      scope = scope_from_managed_entry_config(
        plan_id=runtime.run_id,
        config=runtime.context.parameters,
      )
    except EntryPlanAuthorizationError as exc:
      return (exc.code, exc.message)

    price = self._authorization_decimal(request.price)
    if price is None or price <= 0 or int(request.volume or 0) <= 0:
      return ("INVALID_BUY_ORDER", "自动买入最终订单价格或数量无效")
    amount = price * int(request.volume)
    total_asset = self._authorization_decimal(
      account.get("total_asset")
      or account.get("total_asset_cny")
      or account.get("total_equity_cny")
      or account.get("cash_total")
    )
    if total_asset is None or total_asset <= 0:
      return ("ACCOUNT_SNAPSHOT_UNAVAILABLE", "账户总资产快照不可用")
    market_value = self._authorization_decimal(
      position.get("market_value") or position.get("market_value_cny") or 0
    )
    if market_value is None or market_value < 0:
      return ("POSITION_SNAPSHOT_UNAVAILABLE", "当前持仓市值快照不可用")
    if market_value == 0:
      position_volume = max(
        0,
        int(
          position.get("long_volume")
          or position.get("total_volume")
          or position.get("volume")
          or 0
        ),
      )
      market_value = price * position_volume
    resulting_position_pct = (market_value + amount) / total_asset

    protected_price = self._authorization_decimal(
      (intent.metadata or {}).get("protected_limit_price") or intent.limit_price_hint
    )
    if protected_price is None or protected_price <= 0:
      return ("PROTECTED_PRICE_REQUIRED", "自动买入缺少受保护的决策价格")
    upward_slippage_bps = max(
      Decimal("0"),
      (price - protected_price) / protected_price * Decimal("10000"),
    )
    price_deviation_bps = (
      abs(price - protected_price) / protected_price * Decimal("10000")
    )
    try:
      async with AsyncSessionLocal() as db:
        validation = await EntryPlanAuthorizationService(db).validate_or_invalidate(
          plan_id=runtime.run_id,
          current_scope=scope,
          account_id=account_id,
          proposed_amount_cny=amount,
          proposed_buy_price=price,
          proposed_slippage_bps=int(
            upward_slippage_bps.to_integral_value(rounding=ROUND_CEILING)
          ),
          proposed_price_deviation_bps=int(
            price_deviation_bps.to_integral_value(rounding=ROUND_CEILING)
          ),
          resulting_position_pct=resulting_position_pct,
        )
    except EntryPlanAuthorizationError as exc:
      return (exc.code, exc.message)
    except Exception:
      self.logger.exception(
        "实盘自动买入授权校验失败: run_id=%s intent_id=%s",
        runtime.run_id,
        intent.intent_id,
      )
      return ("ENTRY_AUTHORIZATION_UNAVAILABLE", "自动买入授权服务暂不可用")
    if not validation.valid or validation.balance is None:
      return (validation.code, validation.message)

    authorization_metadata = {
      "exact_auto_entry_authorized": True,
      "auto_entry_authorization_grant_id": validation.balance.grant_id,
      "auto_entry_authorization_code": validation.code,
      "auto_entry_plan_fingerprint": scope.plan_fingerprint,
      "auto_entry_rule_fingerprint": scope.rule_fingerprint,
      "auto_entry_account_snapshot_version": scope.account_snapshot_version,
      "intent_execution_mode": intent.execution_mode.value,
      # One business intent must never create two durable broker commands.
      "idempotency_key": f"entry-plan:{runtime.run_id}:{intent.intent_id}",
    }
    intent.metadata.update(authorization_metadata)
    request.metadata.update(authorization_metadata)
    if runtime.state_manager:
      await runtime.state_manager.update_trade_intent_status(
        intent.intent_id,
        "PENDING",
        metadata=dict(intent.metadata or {}),
        notes="ENTRY_EXACT_AUTO_AUTHORIZED",
      )
    return None

  async def _reject_live_auto_managed_entry(
    self,
    runtime: StrategyRuntime,
    intent: TradeIntent,
    request: OrderRequest,
    *,
    code: str,
    message: str,
    risk_decision_id: str,
  ) -> None:
    """Fail closed by requiring an explicit per-order confirmation.

    An expired or narrowed AUTO grant must never create an outbox command, but
    it also should not make the strategy emit and reject the same deterministic
    intent on every tick.  Persisting the existing intent as manual approval
    keeps the exact order subject to the normal TTL, quote-drift and full
    re-risk path.
    """

    metadata = {
      **dict(intent.metadata or {}),
      **dict(request.metadata or {}),
      "exact_auto_entry_authorized": False,
      "auto_entry_authorization_code": str(code),
      "execution_mode": TradeIntentExecutionMode.MANUAL_CONFIRM.value,
    }
    intent.execution_mode = TradeIntentExecutionMode.MANUAL_CONFIRM
    intent.metadata.update(metadata)
    request.metadata.update(metadata)
    if runtime.state_manager:
      await runtime.state_manager.update_trade_intent_status(
        intent.intent_id,
        "AWAITING_APPROVAL",
        risk_decision_id=risk_decision_id,
        metadata=metadata,
        notes=str(code),
      )
    runtime.pending_approvals[intent.intent_id] = intent
    if runtime.strategy is not None:
      entry_state = dict(runtime.strategy.state.get("managed_entry_plan", {}) or {})
      entry_state["phase"] = "AWAITING_APPROVAL"
      runtime.strategy.state.set(
        "managed_entry_plan",
        entry_state,
        persist=False,
        notify=False,
      )
      self._checkpoint_restored_strategy_state(runtime)
    self._runtime_log(
      runtime,
      "WARNING",
      "实盘自动买入授权失效，已降级为逐笔确认且未创建券商命令: "
      f"intent_id={intent.intent_id} code={code} message={message}",
    )

  async def _process_trade_intent(
    self, runtime: StrategyRuntime, intent: TradeIntent
  ) -> None:
    """处理策略交易意图"""
    broker = runtime.broker
    metrics = runtime.metrics

    if not self._accepts_non_durable_output(runtime):
      await self._reject_intent_during_runtime_transition(runtime, intent)
      return
    continuity_failure = self._runtime_market_continuity_failure(
      runtime,
      intent.instrument_code,
    )
    if continuity_failure is not None:
      await self._reject_intent_for_market_continuity(
        runtime,
        intent,
        failure=continuity_failure,
      )
      return
    reconciliation_failure = self._runtime_state_reconciliation_failure(runtime)
    if reconciliation_failure is not None:
      self._runtime_log(
        runtime,
        "WARNING",
        f"{reconciliation_failure[0]}: {reconciliation_failure[1]}",
      )
      return

    def market_stream_ready() -> bool:
      if runtime.context.mode == StrategyRunMode.BACKTEST:
        return True
      manager = getattr(
        getattr(runtime, "data_adapter", None),
        "subscription_manager",
        None,
      )
      hub = getattr(manager, "hub", None)
      # Production paper/live adapters always expose WholeQuoteHub. Missing
      # wiring is itself unsafe and must fail closed; backtests are the only
      # mode that intentionally has no live market gate.
      return hub is not None and bool(getattr(hub, "is_ready", False))

    async def reject_stale_market_stream() -> None:
      reason = "MARKET_DATA_STREAM_NOT_READY"
      if runtime.state_manager:
        await runtime.state_manager.update_trade_intent_status(
          intent.intent_id,
          "REJECTED",
          metadata={
            **dict(intent.metadata or {}),
            "market_data_gate": reason,
          },
          notes=reason,
        )
      await self._notify_strategy_order(
        runtime,
        OrderStateEvent(
          order_id=None,
          status=OrderStatus.REJECTED.value,
          error_message="权威行情链路未就绪，已拒绝本次交易意图",
          metadata={
            **dict(intent.metadata or {}),
            "intent_id": intent.intent_id,
            "market_data_gate": reason,
          },
        ),
      )
      self._runtime_log(
        runtime,
        "WARNING",
        f"行情门禁拒绝交易意图: {intent.instrument_code} {reason}",
      )

    try:
      from quantx_domain.brokers.base import PriceType

      if not market_stream_ready():
        await reject_stale_market_stream()
        return

      if intent.direction == TradeIntentDirection.BUY:
        order_type = BrokerOrderType.BUY
      elif intent.direction == TradeIntentDirection.SELL:
        order_type = BrokerOrderType.SELL
      else:
        return

      market_data = runtime.latest_market_data.get(intent.instrument_code)
      strict_market_data, strict_limit_data = self._order_risk_strict_flags(runtime)
      if market_data is None and not strict_market_data:
        market_data = MarketDataSnapshot(
          instrument_code=intent.instrument_code,
          timestamp=runtime.context.current_time,
          price=float(intent.limit_price_hint or 0.0),
          close=float(intent.limit_price_hint or 0.0),
          source="intent",
        )

      rules = AShareMarketRules()
      price_source = intent.limit_price_hint or (
        market_data.price if market_data else 0.0
      )
      price_tick = market_data.price_tick if market_data else None
      price = rules.normalize_price(price_source, price_tick)

      account = {}
      position = {}
      if runtime.state_manager:
        account = runtime.state_manager.get_account_quota()
        position = runtime.state_manager.get_position(intent.instrument_code) or {}
      elif broker:
        account_info = await broker.get_account()
        account = {
          "available_cash": account_info.cash,
          "frozen_cash": account_info.frozen_cash,
          "cash_total": account_info.cash + account_info.frozen_cash,
          "total_asset": account_info.total_asset,
        }
        positions = await broker.get_position(intent.instrument_code)
        broker_position = positions.get(intent.instrument_code)
        if broker_position:
          position = {
            "long_volume": broker_position.long_volume,
            "available_volume": broker_position.available_volume
            or broker_position.long_volume,
          }

      context_snapshot = self._build_execution_context_snapshot(
        runtime,
        instrument_code=intent.instrument_code,
        market_data=market_data,
        account=account,
        positions={intent.instrument_code: position},
      )
      sizer = OrderSizer(rules)
      draft = sizer.draft_intent(intent, order_type, price, account, position)
      if draft.sized_volume <= 0:
        size_reasons = list(getattr(draft, "size_reason_codes", []) or [])
        rejection_reason = (
          "MIN_LOT_EXCEEDS_RISK_BUDGET"
          if "MIN_LOT_EXCEEDS_RISK_BUDGET" in size_reasons
          else "ZERO_SIZED_VOLUME"
        )
        if runtime.state_manager:
          await runtime.state_manager.update_trade_intent_status(
            intent.intent_id,
            "REJECTED",
            metadata={
              **dict(intent.metadata or {}),
              "order_draft_id": getattr(draft, "draft_id", None),
              "order_draft_size_reasons": size_reasons,
              "sized_volume": getattr(draft, "sized_volume", None),
            },
            notes=rejection_reason,
          )
        self._record_decision_trace(
          runtime,
          intent=intent,
          market_context=context_snapshot.market_context,
          risk_caps=context_snapshot.risk_caps,
          position_profile=context_snapshot.position_profile,
          order_draft=draft,
          tags=[rejection_reason.lower()],
          reason=rejection_reason,
        )
        await self._notify_strategy_order(
          runtime,
          OrderStateEvent(
            order_id=None,
            status=OrderStatus.REJECTED.value,
            request={
              "instrument_code": intent.instrument_code,
              "order_type": order_type,
              "metadata": {
                **(intent.metadata or {}),
                "intent_id": intent.intent_id,
                "order_draft": draft.__dict__,
              },
            },
            error_message="交易意图无法转换为合法订单数量",
            metadata={
              **(intent.metadata or {}),
              "intent_id": intent.intent_id,
              "order_draft": draft.__dict__,
            },
          ),
        )
        self.logger.warning(
          f"交易意图无法转换为合法订单数量: {intent.instrument_code} {order_type.value}"
        )
        return

      try:
        order_ttl_ms = max(
          0,
          int((intent.metadata or {}).get("order_ttl_ms", 0) or 0),
        )
      except (TypeError, ValueError):
        order_ttl_ms = 0
      order_created_at = (
        runtime.context.current_time
        if runtime.context.mode == StrategyRunMode.BACKTEST
        else time_utils.now()
      ) or time_utils.now()
      order_expire_at_ms = (
        int(order_created_at.timestamp() * 1000) + order_ttl_ms
        if order_ttl_ms > 0
        else 0
      )
      request = OrderRequest(
        instrument_code=intent.instrument_code,
        order_type=order_type,
        price_type=(
          PriceType.MARKET
          if str((intent.metadata or {}).get("price_type", "LIMIT")).upper() == "MARKET"
          else PriceType.LIMIT
        ),
        volume=draft.sized_volume,
        price=price,
        strategy_id=str(runtime.strategy_id),
        metadata={
          **(intent.metadata or {}),
          "strategy_run_id": runtime.run_id,
          "strategy_order_id": "",
          "execution_mode": runtime.context.mode.value,
          "intent_id": intent.intent_id,
          "order_draft_id": draft.draft_id,
          "order_draft_size_reasons": draft.size_reason_codes,
          "bucket": intent.bucket,
          "reason": intent.reason,
          "priority": intent.priority.value,
          "expiry_policy": dict(intent.expiry_policy or {}),
          "approval_ttl_ms": intent.approval_ttl_ms,
          "order_ttl_ms": order_ttl_ms,
          "order_expire_at_ms": order_expire_at_ms,
        },
      )

      checker = TradingRiskChecker(
        rules,
        commission_rate=getattr(broker, "commission_rate", 0.0003),
        min_commission=getattr(broker, "min_commission", 5.0),
        strict_market_data=strict_market_data,
        strict_limit_data=strict_limit_data,
        enforce_trading_hours=bool(
          runtime.context.parameters.get(
            "enforce_trading_hours",
            runtime.context.mode == StrategyRunMode.LIVE,
          )
        ),
        market=runtime.context.parameters.get("market", "SH"),
      )
      decision: OrderRiskDecision = await checker.evaluate_order(
        request,
        account=account,
        position=position,
        market_data=market_data,
        current_time=runtime.context.current_time,
        risk_caps=context_snapshot.risk_caps,
      )
      request.metadata.update(
        {
          "risk_decision_id": decision.risk_decision_id,
          "risk_action": decision.action.value,
          "risk_reason_code": decision.reason_code,
          "risk_tags": decision.risk_tags,
          "substitution_plan": decision.substitution_plan,
        }
      )
      if not decision.allowed:
        if runtime.state_manager:
          await runtime.state_manager.update_trade_intent_status(
            intent.intent_id,
            "DELAYED" if decision.action == RiskAction.DELAY else "REJECTED",
            risk_decision_id=decision.risk_decision_id,
            metadata={
              **dict(intent.metadata or {}),
              "order_draft_id": draft.draft_id,
              "order_draft_size_reasons": draft.size_reason_codes,
              "sized_volume": draft.sized_volume,
              "risk_reason_code": decision.reason_code,
              "risk_action": decision.action.value,
              "risk_tags": decision.risk_tags,
            },
            notes=decision.reason_detail,
          )
        self._record_decision_trace(
          runtime,
          intent=intent,
          market_context=context_snapshot.market_context,
          risk_caps=context_snapshot.risk_caps,
          position_profile=context_snapshot.position_profile,
          order_draft=draft,
          order_request=request,
          risk_decision=decision,
          tags=["risk_blocked", decision.action.value],
          reason=decision.reason_code,
        )
        status = (
          OrderStatus.PENDING.value
          if decision.action == RiskAction.DELAY
          else OrderStatus.REJECTED.value
        )
        await self._notify_strategy_order(
          runtime,
          OrderStateEvent(
            order_id=None,
            status=status,
            request=request,
            error_message=decision.reason_detail,
            metadata=request.metadata,
          ),
        )
        self.logger.warning(
          f"下单前校验失败: {intent.instrument_code} {order_type.value} "
          f"{request.volume}股, 原因: {decision.reason_code} {decision.reason_detail}"
        )
        return
      if decision.final_volume != request.volume:
        request.volume = decision.final_volume
      if str(request.metadata.get("t_trade_role") or "").lower() == "entry":
        # V3 emits an amount target and OrderSizer is the first authority that
        # can derive a legal A-share lot count. Persist that execution fact in
        # the order/report lineage; candidate analytics must never infer it
        # from target_amount or the fill observed so far.
        request.metadata["requested_entry_volume"] = int(request.volume)

      authorization_failure = await self._authorize_live_auto_managed_entry(
        runtime,
        intent,
        request,
        account=account,
        position=position,
      )
      if authorization_failure is not None:
        await self._reject_live_auto_managed_entry(
          runtime,
          intent,
          request,
          code=authorization_failure[0],
          message=authorization_failure[1],
          risk_decision_id=decision.risk_decision_id,
        )
        self._record_decision_trace(
          runtime,
          intent=intent,
          market_context=context_snapshot.market_context,
          risk_caps=context_snapshot.risk_caps,
          position_profile=context_snapshot.position_profile,
          order_draft=draft,
          order_request=request,
          risk_decision=decision,
          tags=["entry_auto_authorization_blocked", authorization_failure[0]],
          reason=authorization_failure[0],
        )
        return

      if not self._accepts_non_durable_output(runtime):
        await self._reject_intent_during_runtime_transition(runtime, intent)
        return

      reservation_key = intent.intent_id
      reserved = await self._reserve_order_resources(runtime, reservation_key, request)
      if not reserved:
        if runtime.state_manager:
          await runtime.state_manager.update_trade_intent_status(
            intent.intent_id,
            "REJECTED",
            risk_decision_id=decision.risk_decision_id,
            metadata={
              **dict(intent.metadata or {}),
              **dict(request.metadata or {}),
              "sized_volume": request.volume,
            },
            notes="RESERVE_FAILED",
          )
        self._record_decision_trace(
          runtime,
          intent=intent,
          market_context=context_snapshot.market_context,
          risk_caps=context_snapshot.risk_caps,
          position_profile=context_snapshot.position_profile,
          order_draft=draft,
          order_request=request,
          risk_decision=decision,
          tags=["reserve_failed"],
          reason="RESERVE_FAILED",
        )
        await self._notify_strategy_order(
          runtime,
          OrderStateEvent(
            order_id=None,
            status=OrderStatus.REJECTED.value,
            request=request,
            error_message="订单资源冻结失败",
            metadata=request.metadata,
          ),
        )
        self.logger.warning(
          f"订单资源冻结失败: {intent.instrument_code} {order_type.value} "
          f"{request.volume}股"
        )
        return

      if not self._accepts_non_durable_output(runtime):
        await self._reject_intent_during_runtime_transition(
          runtime,
          intent,
          reservation_key=reservation_key,
        )
        return

      continuity_failure = self._runtime_market_continuity_failure(
        runtime,
        intent.instrument_code,
      )
      if continuity_failure is not None:
        await self._reject_intent_for_market_continuity(
          runtime,
          intent,
          failure=continuity_failure,
          reservation_key=reservation_key,
        )
        return

      # 下单
      if not market_stream_ready():
        if runtime.state_manager:
          runtime.state_manager.release_order_resources(reservation_key)
        await reject_stale_market_stream()
        return
      try:
        order = await broker.place_order(request)
      except Exception:
        if runtime.state_manager:
          runtime.state_manager.release_order_resources(reservation_key)
        raise

      if runtime.state_manager:
        runtime.state_manager.transfer_reservation(reservation_key, order.order_id)
        await runtime.state_manager.update_trade_intent_status(
          intent.intent_id,
          order.status.value,
          order_id=order.order_id,
          risk_decision_id=decision.risk_decision_id,
          metadata={
            **dict(intent.metadata or {}),
            **dict(request.metadata or {}),
            "sized_volume": request.volume,
            "broker_status": order.status.value,
          },
        )
        if order.status in [
          OrderStatus.REJECTED,
          OrderStatus.CANCELLED,
          OrderStatus.EXPIRED,
        ]:
          runtime.state_manager.release_order_resources(order.order_id)

      self._record_decision_trace(
        runtime,
        intent=intent,
        market_context=context_snapshot.market_context,
        risk_caps=context_snapshot.risk_caps,
        position_profile=context_snapshot.position_profile,
        order_draft=draft,
        order_request=request,
        risk_decision=decision,
        broker_report={
          "order_id": order.order_id,
          "status": order.status.value,
          "filled_volume": order.filled_volume,
          "error_message": order.error_message,
        },
        tags=["broker_report", order.status.value],
        reason=decision.reason_code,
      )

      if order.status == OrderStatus.REJECTED:
        if runtime.metrics:
          runtime.metrics.rejected_orders += 1
        await self._notify_strategy_order(runtime, OrderStateEvent.from_raw(order))
        self._runtime_log(
          runtime,
          "WARNING",
          f"Broker拒单: {intent.instrument_code} {order_type.value}, "
          f"原因: {order.error_message}",
        )
        return

      metrics.orders_placed += 1

      self._runtime_log(
        runtime,
        "INFO",
        f"下单: {intent.instrument_code} {order_type.value} "
        f"{request.volume}股 @ {request.price:.2f}",
      )

    except Exception as e:
      runtime.t_trade_entry_reservations.pop(intent.intent_id, None)
      if metrics:
        metrics.error_count += 1
      await self._notify_strategy_order(
        runtime,
        OrderStateEvent(
          order_id=None,
          status=OrderStatus.REJECTED.value,
          request={
            "instrument_code": intent.instrument_code,
            "metadata": {
              **dict(intent.metadata or {}),
              "intent_id": intent.intent_id,
            },
          },
          error_message=str(e),
          metadata={
            **dict(intent.metadata or {}),
            "intent_id": intent.intent_id,
          },
        ),
      )
      self._runtime_log(runtime, "ERROR", f"处理交易意图失败: {e}")

  async def _reserve_order_resources(
    self,
    runtime: StrategyRuntime,
    reservation_key: str,
    request: OrderRequest,
  ) -> bool:
    if not runtime.state_manager or not runtime.state_manager.enable_reserve:
      if runtime.state_manager and hasattr(
        runtime.state_manager, "reserve_bucket_order"
      ):
        return runtime.state_manager.reserve_bucket_order(reservation_key, request)
      return True

    if request.order_type in [BrokerOrderType.BUY, BrokerOrderType.BUY_TO_COVER]:
      est_cost = self._estimate_order_cost(runtime, request)
      cash_reserved = bool(
        est_cost and runtime.state_manager.reserve_cash(reservation_key, est_cost)
      )
      if not cash_reserved:
        return False
      if hasattr(runtime.state_manager, "reserve_bucket_order"):
        if not runtime.state_manager.reserve_bucket_order(reservation_key, request):
          runtime.state_manager.release_order_resources(reservation_key)
          return False
      return True
    if request.order_type == BrokerOrderType.SELL:
      uses_substitution = bool((request.metadata or {}).get("substitution_plan"))
      if not uses_substitution:
        if not runtime.state_manager.reserve_position(
          reservation_key, request.instrument_code, request.volume
        ):
          return False
      if hasattr(runtime.state_manager, "reserve_bucket_order"):
        if not runtime.state_manager.reserve_bucket_order(reservation_key, request):
          runtime.state_manager.release_order_resources(reservation_key)
          return False
      return True
    return False

  def _record_decision_trace(
    self,
    runtime: StrategyRuntime,
    *,
    intent: TradeIntent,
    market_context: Dict[str, Any],
    risk_caps: Dict[str, Any],
    position_profile: Dict[str, Any],
    order_draft: Any = None,
    order_request: Optional[OrderRequest] = None,
    risk_decision: Optional[OrderRiskDecision] = None,
    broker_report: Optional[Dict[str, Any]] = None,
    tags: Optional[List[str]] = None,
    reason: str = "",
  ) -> None:
    if not runtime.state_manager:
      return
    draft_summary = {}
    if order_draft is not None:
      draft_summary = {
        "draft_id": getattr(order_draft, "draft_id", None),
        "intent_id": getattr(order_draft, "intent_id", None),
        "side": getattr(
          getattr(order_draft, "side", None),
          "value",
          getattr(order_draft, "side", None),
        ),
        "instrument_code": getattr(order_draft, "instrument_code", None),
        "bucket": getattr(order_draft, "bucket", None),
        "limit_price": getattr(order_draft, "limit_price", None),
        "raw_target_amount": getattr(order_draft, "raw_target_amount", None),
        "raw_target_volume": getattr(order_draft, "raw_target_volume", None),
        "sized_amount": getattr(order_draft, "sized_amount", None),
        "sized_volume": getattr(order_draft, "sized_volume", None),
        "size_reason_codes": list(getattr(order_draft, "size_reason_codes", []) or []),
        "metadata": dict(getattr(order_draft, "metadata", {}) or {}),
      }
    request_summary = {}
    if order_request is not None:
      request_summary = {
        "instrument_code": order_request.instrument_code,
        "order_type": order_request.order_type.value,
        "price_type": order_request.price_type.value,
        "volume": order_request.volume,
        "price": order_request.price,
        "metadata": dict(order_request.metadata or {}),
      }
    decision_summary = {}
    if risk_decision is not None:
      decision_summary = {
        "risk_decision_id": risk_decision.risk_decision_id,
        "action": risk_decision.action.value,
        "allowed": risk_decision.allowed,
        "original_volume": risk_decision.original_volume,
        "final_volume": risk_decision.final_volume,
        "reason_code": risk_decision.reason_code,
        "reason_detail": risk_decision.reason_detail,
        "risk_tags": list(risk_decision.risk_tags or []),
        "metadata": dict(risk_decision.metadata or {}),
        "substitution_plan": risk_decision.substitution_plan,
      }
    trace = DecisionTrace.from_decision(
      run_id=runtime.run_id,
      strategy_id=str(runtime.strategy_id),
      instrument_code=intent.instrument_code,
      environment=market_context,
      risk_caps=risk_caps,
      position_profile=position_profile,
      trade_intents=[summarize_intent(intent)],
      order_draft=draft_summary,
      order_request=request_summary,
      risk_decision=decision_summary,
      broker_report=broker_report or {},
      trace_id=intent.trace_id,
      tags=tags,
      reason=reason,
    )
    runtime.state_manager.record_decision_trace(trace)

  def _estimate_order_price(
    self, runtime: StrategyRuntime, request: OrderRequest
  ) -> Optional[float]:
    if request.price and request.price > 0:
      return request.price

    broker = runtime.broker
    if broker and hasattr(broker, "current_prices"):
      price = broker.current_prices.get(request.instrument_code)
      if price:
        return float(price)

    return None

  def _estimate_order_cost(
    self, runtime: StrategyRuntime, request: OrderRequest
  ) -> Optional[float]:
    price = self._estimate_order_price(runtime, request)
    if price is None or price <= 0:
      return None

    amount = price * request.volume
    commission = 0.0
    if runtime.broker:
      commission = runtime.broker.calculate_commission(
        amount,
        rate=getattr(runtime.broker, "commission_rate", 0.0003),
      )
    return amount + commission

  def _resolve_realtime_instruments(self, runtime: StrategyRuntime) -> List[str]:
    """Resolve realtime subscriptions from context first, then legacy parameters."""
    instruments = [
      str(item or "").strip()
      for item in list(getattr(runtime.context, "instruments", []) or [])
      if str(item or "").strip()
    ]
    if instruments:
      return instruments

    params = dict(getattr(runtime.context, "parameters", {}) or {})
    raw = (
      params.get("instruments")
      or params.get("stockCodes")
      or params.get("stock_codes")
      or params.get("instrument_code")
      or params.get("instrumentCode")
    )
    if isinstance(raw, list):
      candidates = raw
    else:
      candidates = str(raw or "").split(",")
    return [str(item or "").strip() for item in candidates if str(item or "").strip()]

  def _realtime_data_requirements(
    self, runtime: StrategyRuntime
  ) -> tuple[bool, List[str]]:
    strategy_class = getattr(runtime, "strategy_class", None)
    requirements = (
      strategy_class.get_data_requirements()
      if strategy_class and hasattr(strategy_class, "get_data_requirements")
      else {
        "use_tick_data": False,
        "periods": [runtime.context.parameters.get("period", "1m")],
      }
    )
    use_tick_data = bool(requirements.get("use_tick_data", False))
    periods = [
      str(period).lower()
      for period in list(requirements.get("periods") or [])
      if period and str(period).lower() != "tick"
    ]
    if not use_tick_data and not periods:
      periods = [str(runtime.context.parameters.get("period", "1m"))]
    return use_tick_data, periods

  async def _subscribe_realtime_instrument(
    self, runtime: StrategyRuntime, instrument: str
  ) -> List[str]:
    data_adapter = runtime.data_adapter
    if data_adapter is None:
      return []
    use_tick_data, periods = self._realtime_data_requirements(runtime)
    subscription_ids: List[str] = []
    try:
      if use_tick_data:
        subscription_ids.append(
          await data_adapter.subscribe_tick(
            instrument_code=instrument,
            callback=lambda tick: self._enqueue_runtime_market_event(
              runtime, "tick", tick
            ),
          )
        )
      for period in periods:
        subscription_ids.append(
          await data_adapter.subscribe_kline(
            instrument_code=instrument,
            period=period,
            callback=lambda kline: self._enqueue_runtime_market_event(
              runtime, "kline", kline
            ),
          )
        )
    except Exception:
      for subscription_id in subscription_ids:
        await data_adapter.unsubscribe(subscription_id)
      raise
    self.logger.info(
      "订阅实时数据: %s, tick=%s, periods=%s",
      instrument,
      use_tick_data,
      periods,
    )
    return subscription_ids

  async def _expire_v3_t_trade_candidates_for_config_change(
    self,
    runtime: StrategyRuntime,
  ) -> None:
    """Expire every authoritative unapproved V3 entry before policy rewarm."""

    candidates = list(runtime.pending_approvals.values())
    for intent in candidates:
      metadata = dict(intent.metadata or {})
      try:
        schema_version = int(metadata.get("opportunity_schema_version") or 0)
      except (TypeError, ValueError, OverflowError):
        schema_version = 0
      if not (
        schema_version >= 3
        and intent.direction == TradeIntentDirection.BUY
        and str(metadata.get("t_trade_role") or "").lower() == "entry"
      ):
        continue
      await self._reject_pending_approval(
        runtime,
        intent,
        status="EXPIRED",
        reason="GLOBAL_CONFIG_CHANGED",
        message="做 T 参数已更新，旧候选已失效并重新预热",
      )

  async def _apply_realtime_instrument_reconcile(
    self,
    runtime: StrategyRuntime,
    instruments: List[str],
    *,
    instrument_metadata: Optional[Dict[str, Dict[str, Any]]] = None,
    parameters: Optional[Mapping[str, Any]] = None,
    configuration_changed: bool = False,
  ) -> Dict[str, List[str]]:
    """在运行事件队列内安全调整标的池和订阅。"""

    desired = []
    for raw in instruments or []:
      code = str(raw or "").strip().upper()
      if code and code not in desired:
        desired.append(code)
    previous = list(runtime.context.instruments or [])
    membership_added = [code for code in desired if code not in previous]
    membership_removed = [code for code in previous if code not in desired]
    previous_parameters = dict(runtime.context.parameters or {})
    if parameters is not None:
      runtime.context.parameters = dict(parameters)

    staged_emission: Optional[Dict[str, Dict[str, Any]]] = None
    if self._uses_t_trade_opportunity_runtime(runtime):
      try:
        staged_emission = self._build_t_trade_intent_emission_snapshot(
          runtime,
          desired,
          instrument_metadata,
        )
      except Exception:
        runtime.context.parameters = previous_parameters
        self._clear_t_trade_intent_emission_snapshot(runtime)
        raise

    async with runtime.realtime_subscription_lock:
      subscribed = set(runtime.realtime_subscription_ids)
      to_subscribe = [code for code in desired if code not in subscribed]
      to_unsubscribe = [code for code in subscribed if code not in desired]
      created: List[str] = []
      runtime.context.instruments = desired
      try:
        for code in to_subscribe:
          runtime.realtime_subscription_ids[
            code
          ] = await self._subscribe_realtime_instrument(runtime, code)
          created.append(code)
      except Exception:
        runtime.context.instruments = previous
        runtime.context.parameters = previous_parameters
        if self._uses_t_trade_opportunity_runtime(runtime):
          self._clear_t_trade_intent_emission_snapshot(runtime)
        for code in created:
          for subscription_id in runtime.realtime_subscription_ids.pop(code, []):
            await runtime.data_adapter.unsubscribe(subscription_id)
        raise

      try:
        for code in to_unsubscribe:
          for subscription_id in runtime.realtime_subscription_ids.pop(code, []):
            await runtime.data_adapter.unsubscribe(subscription_id)
          runtime.latest_market_data.pop(code, None)
          self.logger.info("取消已移出标的池的实时订阅: %s", code)
      except Exception:
        if self._uses_t_trade_opportunity_runtime(runtime):
          self._clear_t_trade_intent_emission_snapshot(runtime)
        raise

    if runtime.strategy:
      try:
        self._sync_dynamic_holding_inventory(runtime, instrument_metadata)
        state = runtime.strategy.state.to_dict()
        account = (
          runtime.state_manager.get_account_quota() if runtime.state_manager else {}
        )
        positions = (
          runtime.state_manager.get_all_positions() if runtime.state_manager else {}
        )
        reconcile_input = StrategyInput(
          run_id=runtime.run_id,
          strategy_id=str(runtime.strategy_id),
          timestamp=runtime.context.current_time or time_utils.now(),
          cadence=StrategyCadence.RECONCILE,
          instrument_code="",
          event={
            "added": membership_added,
            "removed": membership_removed,
            "instruments": desired,
            "instrument_metadata": dict(instrument_metadata or {}),
            "configuration_changed": bool(configuration_changed),
          },
          portfolio_state={"account": account, "positions": positions},
          strategy_state=state,
          parameters=dict(runtime.context.parameters or {}),
        )
        output = await runtime.strategy.step(reconcile_input)
        await self._process_strategy_output(runtime, output, reconcile_input)
      except Exception:
        if self._uses_t_trade_opportunity_runtime(runtime):
          self._clear_t_trade_intent_emission_snapshot(runtime)
        raise

    if staged_emission is not None:
      self._publish_t_trade_intent_emission_snapshot(runtime, staged_emission)
    if self._uses_t_trade_opportunity_runtime(runtime):
      self._prune_t_trade_opportunity_profile_cache(
        runtime,
        removed_instruments=membership_removed,
      )

    return {
      "added": membership_added,
      "removed": membership_removed,
      "instruments": desired,
    }

  async def _apply_backtest_instrument_reconcile(
    self,
    runtime: StrategyRuntime,
    instruments: List[str],
    *,
    instrument_metadata: Optional[Dict[str, Dict[str, Any]]] = None,
  ) -> Dict[str, List[str]]:
    """Apply one point-in-time universe snapshot without live subscriptions."""

    if not self._uses_strict_board_replay(runtime):
      raise ValueError("动态历史标的池仅供账户级打板回放使用")
    metadata = {
      str(code or "").strip().upper(): dict(value or {})
      for code, value in dict(instrument_metadata or {}).items()
      if str(code or "").strip()
    }
    desired: List[str] = []
    for raw in instruments or []:
      code = str(raw or "").strip().upper()
      if code and code not in desired:
        desired.append(code)
    for code in sorted(self._board_replay_sticky_instruments(runtime)):
      if code not in desired:
        desired.append(code)
      if (
        code not in metadata
        or str(metadata[code].get("source") or "").upper() == "DRAINING"
      ):
        metadata[code] = self._board_replay_draining_metadata(runtime, code)
    previous = list(runtime.context.instruments or [])
    membership_added = [code for code in desired if code not in previous]
    membership_removed = [code for code in previous if code not in desired]
    staged_emission: Optional[Dict[str, Dict[str, Any]]] = None
    if self._uses_t_trade_opportunity_runtime(runtime):
      try:
        staged_emission = self._build_t_trade_intent_emission_snapshot(
          runtime,
          desired,
          metadata,
        )
      except Exception:
        self._clear_t_trade_intent_emission_snapshot(runtime)
        raise
    runtime.context.instruments = desired
    for code in membership_removed:
      runtime.latest_market_data.pop(code, None)

    if runtime.strategy:
      try:
        self._sync_dynamic_holding_inventory(runtime, metadata)
        state = runtime.strategy.state.to_dict()
        account = (
          runtime.state_manager.get_account_quota() if runtime.state_manager else {}
        )
        positions = (
          runtime.state_manager.get_all_positions() if runtime.state_manager else {}
        )
        reconcile_input = StrategyInput(
          run_id=runtime.run_id,
          strategy_id=str(runtime.strategy_id),
          timestamp=self._runtime_now(runtime),
          cadence=StrategyCadence.RECONCILE,
          instrument_code="",
          event={
            "added": membership_added,
            "removed": membership_removed,
            "instruments": desired,
            "instrument_metadata": metadata,
          },
          portfolio_state={"account": account, "positions": positions},
          strategy_state=state,
          parameters=dict(runtime.context.parameters or {}),
        )
        output = await runtime.strategy.step(reconcile_input)
        await self._process_strategy_output(runtime, output, reconcile_input)
        await self._board_replay_report_barrier(runtime)
      except Exception:
        if self._uses_t_trade_opportunity_runtime(runtime):
          self._clear_t_trade_intent_emission_snapshot(runtime)
        raise

    if staged_emission is not None:
      self._publish_t_trade_intent_emission_snapshot(runtime, staged_emission)
    if self._uses_t_trade_opportunity_runtime(runtime):
      self._prune_t_trade_opportunity_profile_cache(
        runtime,
        removed_instruments=membership_removed,
      )

    return {
      "added": membership_added,
      "removed": membership_removed,
      "instruments": desired,
    }

  @staticmethod
  def _board_replay_sticky_instruments(runtime: StrategyRuntime) -> set[str]:
    """Keep every symbol with unfinished account work on the replay feed."""

    sticky = {
      str(intent.instrument_code or "").strip().upper()
      for intent in runtime.pending_approvals.values()
      if str(intent.instrument_code or "").strip()
    }
    broker = runtime.broker
    active_order_statuses = {
      "PENDING",
      "SUBMITTED",
      "ACCEPTED",
      "PARTIAL_FILLED",
    }
    for order in dict(getattr(broker, "orders", {}) or {}).values():
      status = str(
        getattr(getattr(order, "status", ""), "value", getattr(order, "status", ""))
      ).upper()
      request = getattr(order, "request", None)
      code = str(getattr(request, "instrument_code", "") or "").strip().upper()
      if code and status in active_order_statuses:
        sticky.add(code)
    for code, position in dict(getattr(broker, "positions", {}) or {}).items():
      if int(getattr(position, "long_volume", 0) or 0) > 0:
        sticky.add(str(code).strip().upper())
    if runtime.state_manager:
      for code, position in runtime.state_manager.get_all_positions().items():
        if int(dict(position or {}).get("long_volume", 0) or 0) > 0:
          sticky.add(str(code).strip().upper())
    for plan in runtime.exit_plan_book.active_plans():
      code = str(plan.template.instrument_code or "").strip().upper()
      if code:
        sticky.add(code)
    return sticky

  @staticmethod
  def _board_replay_draining_metadata(
    runtime: StrategyRuntime,
    instrument_code: str,
  ) -> Dict[str, Any]:
    states = (
      dict(runtime.strategy.state.get("instrument_states", {}) or {})
      if runtime.strategy
      else {}
    )
    state = dict(states.get(instrument_code) or {})
    return {
      "eligible": False,
      "reason": "DRAINING_EXISTING_WORK",
      "source": str(state.get("candidate_source") or "DRAINING"),
      "draining": True,
      "arm_version": int(state.get("last_arm_version", 0) or 0),
      "radar_score": float(state.get("radar_score", 0.0) or 0.0),
      "radar_stage": str(state.get("radar_stage") or ""),
      "radar_updated_at": str(state.get("radar_updated_at") or ""),
      "radar_is_stale": bool(state.get("radar_is_stale", False)),
      "promotion_eligible": bool(state.get("promotion_eligible", False)),
      "promotion_score": float(state.get("promotion_score", 0.0) or 0.0),
      "promotion_snapshot_version": str(state.get("promotion_snapshot_version") or ""),
      "promotion_model_version": str(state.get("promotion_model_version") or ""),
      "exit_policy_version": str(state.get("exit_policy_version") or ""),
      "board_segment": str(state.get("board_segment") or ""),
      "cvar95_loss_pct": float(state.get("cvar95_loss_pct", 0.0) or 0.0),
      "expected_net_return_pct": float(
        state.get("expected_net_return_pct", 0.0) or 0.0
      ),
      "target_position_pct": 0.0,
      "liquidity_cap_amount": 0.0,
      "high_position_type": str(state.get("high_position_type") or ""),
    }

  async def _clear_realtime_subscriptions(self, runtime: StrategyRuntime) -> None:
    data_adapter = runtime.data_adapter
    if data_adapter is None:
      return
    async with runtime.realtime_subscription_lock:
      failures: List[tuple[str, Exception]] = []
      for instrument, subscription_ids in list(
        runtime.realtime_subscription_ids.items()
      ):
        remaining: List[str] = []
        for subscription_id in subscription_ids:
          try:
            removed = await data_adapter.unsubscribe(subscription_id)
            if removed is not True:
              raise RuntimeError("数据适配器未确认订阅已取消")
          except Exception as exc:
            remaining.append(subscription_id)
            failures.append((subscription_id, exc))
        if remaining:
          runtime.realtime_subscription_ids[instrument] = remaining
        else:
          runtime.realtime_subscription_ids.pop(instrument, None)
      if failures:
        failed_ids = ", ".join(item[0] for item in failures)
        raise RuntimeError(f"实时订阅取消失败: {failed_ids}") from failures[0][1]

  async def _run_realtime_loop(self, runtime: StrategyRuntime) -> None:
    """运行实时交易循环"""
    metrics = runtime.metrics
    broker = runtime.broker

    instruments = self._resolve_realtime_instruments(runtime)
    await self._apply_realtime_instrument_reconcile(runtime, instruments)

    # 运行直到停止
    while (
      runtime.status in [ExecutionStatus.RUNNING, ExecutionStatus.PAUSED]
      and not self._shutdown_event.is_set()
    ):
      if runtime.status == ExecutionStatus.PAUSED:
        await asyncio.sleep(1)
        continue

      # 更新心跳
      metrics.last_heartbeat = time_utils.now()
      # 每10次心跳同步一次指标到数据库
      heartbeat_count = getattr(self, f"heartbeat_count_{runtime.run_id}", 0)
      heartbeat_count += 1
      setattr(self, f"heartbeat_count_{runtime.run_id}", heartbeat_count)

      # 检查持仓
      positions_result = await broker.get_position()
      positions = positions_result if isinstance(positions_result, dict) else {}
      runtime.context.positions = positions

      # 检查账户
      account = await broker.get_account()
      runtime.context.account_info = {
        "cash": account.cash,
        "total_value": account.total_asset,
        "buying_power": account.cash,
        "frozen_cash": account.frozen_cash,
        "market_value": account.market_value,
        "total_pnl": account.total_pnl,
        "daily_pnl": account.daily_pnl,
      }
      state_manager = getattr(runtime, "state_manager", None)
      if state_manager:
        state_manager.update_account(
          cash=account.cash,
          frozen_cash=account.frozen_cash,
          total_asset=account.total_asset,
        )
        for instrument_code, position in positions.items():
          state_manager.update_position(
            instrument_code,
            long_volume=position.long_volume,
            available_volume=position.available_volume,
            frozen_volume=position.frozen_volume,
            today_buy_volume=position.today_buy_volume,
            long_avg_price=position.long_avg_price,
            last_price=position.last_price,
            market_value=position.market_value,
            pnl=position.pnl,
          )

      await asyncio.sleep(1)

    await self._clear_realtime_subscriptions(runtime)

  def get_statistics(self) -> Dict[str, Any]:
    """获取执行器统计信息"""
    status_counts = {}
    for status in ExecutionStatus:
      status_counts[status.value] = sum(
        1 for runtime in self.runs.values() if runtime.status == status
      )

    return {
      "total_runs": len(self.runs),
      "max_workers": self.max_workers,
      "status_distribution": status_counts,
      "running_runs": len(self.get_running()),
      "market_event_queues": {
        runtime.run_id: {
          "capacity": runtime.market_event_queue.maxsize,
          "depth": runtime.market_event_queue.qsize(),
          "high_watermark": runtime.market_queue_high_watermark,
          "enqueued": runtime.market_events_enqueued,
          "processed": runtime.market_events_processed,
          "dropped": runtime.market_events_dropped,
          "expired": runtime.market_events_expired,
          "tick_source_rejections": runtime.market_tick_source_rejections,
          "overflows": runtime.market_event_overflows,
          "window_invalidations": runtime.market_window_invalidations,
          "fail_closed_instruments": sorted(runtime._market_fail_closed_codes),
        }
        for runtime in self.runs.values()
      },
      "runtime_checkpoints": {
        runtime.run_id: copy.deepcopy(runtime.checkpoint_status)
        for runtime in self.runs.values()
      },
    }
