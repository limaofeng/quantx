"""Idempotent convergence from the durable QMT Agent report inbox."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import md5, sha256
from typing import Any, Awaitable, Callable, Iterable, Mapping, Optional

from quantx_application.trading import (
  OWNER_ENVIRONMENT_CONFLICT,
  OWNER_TARGET_CONFLICT,
  OWNER_TARGET_NOT_FOUND,
  OwnerRuntimeEvent,
  OwnerRuntimeEventKind,
  OwnerRuntimeRegistry,
  OwnerRuntimeRouter,
  OwnerRuntimeRoutingError,
  OwnerRuntimeTarget,
)
from quantx_contracts import (
  PROTOCOL_VERSION,
  TERMINAL_ORDER_STATUSES,
  ExecutionEnvironment,
  ExecutionOwnerRef,
  ExecutionOwnerType,
  can_transition_order_status,
  normalize_order_status,
  snapshot_account_authority_is_authoritative,
)
from quantx_domain.brokers.base import (
  OrderRequest,
  OrderResponse,
  OrderStatus,
  OrderType,
  PriceType,
  TradeRecord,
)
from quantx_domain.clock import to_naive_utc, utcnow
from quantx_domain.trading.exit_plan import ExitPlan, ExitPlanBook
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.redis_pubsub import (
  AGENT_REPORT_WAKE_CHANNEL,
  RedisChannelSubscription,
  redis_pubsub,
)
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.account import Account
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  AccountExecutionControlEvent,
  AgentDevice,
  AgentReportInbox,
  OperationalAlert,
  OrderCorrelation,
  PendingTradeOrder,
  RuntimeComponentHeartbeat,
  StrategyRuntimeEvent,
  TradeCommandOutbox,
  TTradeBatch,
)
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.enums import AccountType
from quantx_infrastructure.models.order import Order
from quantx_infrastructure.models.trade import Trade
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.account_repository import AccountRepository
from quantx_infrastructure.services.account_execution_quarantine_service import (
  BROKER_EXECUTION_AFTER_RELEASE,
  QUARANTINE_CANCEL_REQUIRED_METADATA_KEY,
  QUARANTINE_REASON_METADATA_KEY,
  QUARANTINE_RECONCILE_REQUIRED_METADATA_KEY,
  QUARANTINE_REPAIR_REQUIRED_METADATA_KEY,
  AccountExecutionQuarantineService,
)
from quantx_infrastructure.services.agent_handover import converge_ready_agent
from quantx_infrastructure.services.agent_session_guard import (
  AGENT_SERVER_SESSION_PAYLOAD_KEY,
  QMT_ACCOUNT_MISMATCH,
  report_belongs_to_current_session,
)
from quantx_infrastructure.services.auto_exit_plan_service import (
  AutoExitPlanService,
)
from quantx_infrastructure.services.entry_plan_authorization_service import (
  EntryPlanAuthorizationService,
)
from quantx_infrastructure.services.exit_plan_zero_fill_safety import (
  ZERO_FILL_CONTRADICTION_ORDER_STATUSES,
  invalidate_exit_plan_zero_fill_proof,
  is_exact_finalized_exit_order_replay,
  runtime_zero_fill_invalidation,
)
from quantx_infrastructure.services.operational_alert_service import (
  OperationalAlertService,
)
from quantx_infrastructure.services.order_service import OrderService
from quantx_infrastructure.services.position_service import PositionService
from quantx_infrastructure.services.runtime_subscription_bridge import (
  TRADING_EVENT_CHANNEL,
)
from quantx_infrastructure.services.t_order_lifecycle_state import (
  t_order_lifecycle_pending,
)
from quantx_infrastructure.services.trade_command_service import (
  AgentUnavailableError,
  TradeCommandService,
)
from quantx_infrastructure.services.trade_intent_processor import (
  LOCAL_AGENT_PRE_EXECUTION_ZERO_FILL_SOURCE,
  LOCAL_OUTBOX_EXPIRED_ZERO_FILL_SOURCE,
)
from quantx_infrastructure.services.trade_service import TradeService
from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError, MultipleResultsFound
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.orm import aliased

from .t_trade_coordination import t_trade_account_coordination_lock

_MAX_TRADE_RUNTIME_AUTHORITY_SCAN = 4096
_MAX_SNAPSHOT_ACCOUNT_SCOPE = 4096

logger = logging.getLogger(__name__)

_DATABASE_CONTENTION_RETRY_SECONDS = 0.25
_DATABASE_CONTENTION_MAX_RETRY_SECONDS = 2.0
_RETRYABLE_DATABASE_SQLSTATES = frozenset({"55P03", "40P01", "40001"})

# Production owns a single Engine report consumer, while this lock also makes
# direct/test drain calls obey the same invariant. Recovery of PROCESSING rows
# is performed only while this lock is held, so it cannot reclaim an event that
# this process is still applying.
_runtime_event_drain_lock = asyncio.Lock()

# Runtime handlers for non-StrategyRun owners must use the same transaction as
# the event application marker.  The drain keeps the session in this context
# while calling the router; direct/unit callers get a short-lived session in
# ``_apply_runtime_event`` instead.
_runtime_event_db: ContextVar[Any | None] = ContextVar(
  "quantx_runtime_event_db",
  default=None,
)

_ORDER_STATUS_NAMES = {
  48: "PENDING",
  49: "SUBMITTED",
  50: "SUBMITTED",
  51: "SUBMITTED",
  52: "PARTIAL_FILLED",
  53: "CANCELLED",
  54: "CANCELLED",
  55: "PARTIAL_FILLED",
  56: "FILLED",
  57: "REJECTED",
  255: "PENDING",
}

_SNAPSHOT_PROMOTABLE_HEARTBEAT_STATUSES = {
  "RECONCILING",
  "RECONCILE_REQUIRED",
}
_AUTOMATIC_RECONCILIATION_KINDS = {
  BROKER_EXECUTION_AFTER_RELEASE,
  "CANCEL_REQUEST_PENDING",
  "MISSING_WORKING_ORDER",
  "PROTOCOL_1_2_REQUIRED",
  "PENDING_ORDER_RECONCILE_REQUIRED",
  "QUARANTINED_ORDER_REPAIR_REQUIRED",
  "QUARANTINE_REPAIR_AWAITING_FRESH_SNAPSHOT",
  "SNAPSHOT_COMPLETENESS_REQUIRED",
  "SNAPSHOT_IDENTITY_INVALID",
  "SNAPSHOT_NOT_NEWER_THAN_QUARANTINE",
  "SNAPSHOT_PROTOCOL_INVALID",
  "SNAPSHOT_SECTION_INCOMPLETE",
  "TERMINAL_ORDER_STILL_WORKING",
  "UNKNOWN_BROKER_ORDER",
  "UNKNOWN_BROKER_TRADE",
}
_SPECIAL_RUNTIME_ORDER_STATUSES = {
  "RECONCILE_REQUIRED",
  "RECONCILED_ZERO_FILL",
}
_RUNTIME_OWNER_METADATA_KEYS = frozenset(
  {
    "owner_type",
    "owner_id",
    "environment",
    "execution_environment",
    "execution_mode",
    "execution_owner",
    "execution_owner_type",
    "execution_owner_id",
    "execution_owner_environment",
    "source_execution_owner_type",
    "source_execution_owner_id",
    "source_execution_environment",
    "source_owner_type",
    "source_owner_id",
    "source_environment",
    "strategy_run_id",
    "run_id",
    "runtime_run_id",
    "client_order_id",
    "broker_order_id",
    "order_id",
    "execution_id",
    "traded_id",
    "trade_id",
    "strategy_order_id",
    "intent_id",
  }
)
_ZERO_FILL_RECONCILABLE_ORDER_STATUSES = {"CANCELLED", "EXPIRED"}
PROTOCOL_1_2_REQUIRED = "PROTOCOL_1_2_REQUIRED"


def _require_current_protocol(protocol_version: Any) -> None:
  """Allow only the current Agent report contract into Engine convergence."""

  if str(protocol_version or "") != PROTOCOL_VERSION:
    raise ValueError(PROTOCOL_1_2_REQUIRED)


def _durable_owner_triple(value: object) -> tuple[str, str, str] | None:
  """Read one canonical owner/environment triple from durable columns."""

  missing = object()
  source_owner_type = getattr(value, "source_execution_owner_type", missing)
  source_owner_id = getattr(value, "source_execution_owner_id", missing)
  source_environment = getattr(value, "source_execution_environment", missing)
  has_source_projection = any(
    item is not missing
    for item in (source_owner_type, source_owner_id, source_environment)
  )

  # TTradeBatch and AutoExitPlanRecord retain their source owner under the
  # explicit source_execution_* columns.  A source projection is therefore
  # all-or-nothing and canonical; never fill one missing source field from a
  # generic field, strategy_run_id, or JSON metadata.  Their direct
  # ``environment`` is an independent proof column and must agree with the
  # source environment rather than being silently mixed into the triple.
  if has_source_projection:
    if (
      source_owner_type is missing
      or source_owner_id is missing
      or source_environment is missing
      or source_owner_type is None
      or source_owner_id is None
      or source_environment is None
    ):
      return None
    owner_type = source_owner_type
    owner_id = source_owner_id
    environment = source_environment
    direct_environment = getattr(value, "environment", missing)
  else:
    owner_type = getattr(value, "owner_type", None)
    owner_id = getattr(value, "owner_id", None)
    environment = getattr(value, "environment", None)
    direct_environment = missing

  try:
    owner_type_value = ExecutionOwnerType(
      str(getattr(owner_type, "value", owner_type) or "").strip().upper()
    ).value
    environment_value = ExecutionEnvironment(
      str(getattr(environment, "value", environment) or "").strip().upper()
    ).value
    if direct_environment is not missing:
      if direct_environment is None:
        return None
      direct_environment_value = ExecutionEnvironment(
        str(getattr(direct_environment, "value", direct_environment) or "")
        .strip()
        .upper()
      ).value
      if direct_environment_value != environment_value:
        return None
  except (TypeError, ValueError):
    return None
  if not isinstance(owner_id, str) or not owner_id or owner_id != owner_id.strip():
    return None
  strategy_run_id = str(getattr(value, "strategy_run_id", "") or "").strip()
  if strategy_run_id and (
    owner_type_value != ExecutionOwnerType.STRATEGY_RUN.value
    or strategy_run_id != owner_id
  ):
    return None
  # ``strategy_run_id`` is an optional denormalized consistency witness.  The
  # typed owner columns remain authoritative, so a missing witness must not
  # make an otherwise complete STRATEGY_RUN triple unroutable.
  return owner_type_value, owner_id, environment_value


def _owner_chain_triple(*values: object) -> tuple[str, str, str] | None:
  """Prove exact owner/environment equality across durable projections."""

  triples: list[tuple[str, str, str]] = []
  for value in values:
    if value is None:
      continue
    triple = _durable_owner_triple(value)
    if triple is None:
      return None
    triples.append(triple)
  if not triples or any(triple != triples[0] for triple in triples[1:]):
    return None
  return triples[0]


def _durable_exit_plan_sell_binding(
  pending: PendingTradeOrder,
  correlation: OrderCorrelation | None = None,
) -> bool | None:
  """Classify an EXIT_PLAN sell from durable owner columns only.

  Request metadata is retained as an optional consistency witness.  It may
  reject a contradictory row, but it can never select an owner or authorize a
  terminal replay.  ``None`` denotes a durable/metadata conflict and callers
  must leave the row untouched.
  """

  pending_owner = _durable_owner_triple(pending)
  if pending_owner is None or pending_owner[0] != ExecutionOwnerType.EXIT_PLAN.value:
    return False
  if str(pending.side or "").upper() != "SELL":
    return False
  if correlation is not None and _durable_owner_triple(correlation) != pending_owner:
    return None
  metadata_values = [pending.request_metadata]
  if correlation is not None:
    metadata_values.append(getattr(correlation, "request_metadata", None))
  for raw_metadata in metadata_values:
    metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
    metadata_owner_type = str(metadata.get("owner_type") or "").strip().upper()
    metadata_owner_id = str(metadata.get("owner_id") or "").strip()
    metadata_plan_id = str(metadata.get("exit_plan_id") or "").strip()
    if metadata_owner_type and metadata_owner_type != pending_owner[0]:
      return None
    if metadata_owner_id and metadata_owner_id != pending_owner[1]:
      return None
    if metadata_plan_id and metadata_plan_id != pending_owner[1]:
      return None
  return True


def _canonical_order_side(value: object) -> str:
  normalized = str(getattr(value, "value", value) or "").strip().upper()
  if normalized in {"BUY", "23", "ORDER_BUY"}:
    return "BUY"
  if normalized in {"SELL", "24", "ORDER_SELL"}:
    return "SELL"
  return ""


def _exact_intent_binding(
  pending: PendingTradeOrder,
  correlation: OrderCorrelation,
  intent: TradeIntentRecord | None,
) -> bool:
  """Require the optional intent projection to be a complete order witness."""

  raw_pending_intent_id = str(pending.intent_id or "")
  raw_correlation_intent_id = str(correlation.intent_id or "")
  pending_intent_id = raw_pending_intent_id.strip()
  correlation_intent_id = raw_correlation_intent_id.strip()
  if (
    raw_pending_intent_id != pending_intent_id
    or raw_correlation_intent_id != correlation_intent_id
  ):
    return False
  if pending_intent_id != correlation_intent_id:
    return False
  owner = _owner_chain_triple(pending, correlation)
  if owner is None:
    return False
  # Manual commands may intentionally be represented by a pending/correlation
  # pair without a synthetic TradeIntentRecord.  Every other owner requires
  # the durable intent projection; its absence is not a routable state.
  if not correlation_intent_id:
    return owner[0] == ExecutionOwnerType.MANUAL_COMMAND.value and intent is None
  if intent is None or str(intent.id or "").strip() != correlation_intent_id:
    return False
  if _owner_chain_triple(pending, correlation, intent) != owner:
    return False
  if str(intent.account_id or "") != str(pending.account_id or ""):
    return False
  if (
    str(intent.instrument_code or "").strip().upper()
    != str(pending.instrument_code or "").strip().upper()
  ):
    return False
  pending_side = _canonical_order_side(pending.side)
  intent_direction = _canonical_order_side(intent.direction)
  return bool(pending_side and pending_side == intent_direction)


def _owner_ref_environment(value: object) -> tuple[ExecutionOwnerRef, ExecutionEnvironment] | None:
  triple = _durable_owner_triple(value)
  if triple is None:
    return None
  owner_type, owner_id, environment = triple
  try:
    return ExecutionOwnerRef(owner_type, owner_id), ExecutionEnvironment(environment)
  except (TypeError, ValueError):
    return None


def _wire_execution_mode(environment: object) -> str:
  try:
    return ExecutionEnvironment(
      str(getattr(environment, "value", environment) or "").strip().upper()
    ).value.lower()
  except (TypeError, ValueError):
    return ""


class RetryableReportError(RuntimeError):
  pass


def _database_sqlstate(error: DBAPIError) -> str:
  value = getattr(error.orig, "sqlstate", None)
  return value if isinstance(value, str) and len(value) == 5 else "UNKNOWN"


def _retryable_database_error(error: Exception) -> bool:
  return isinstance(error, SQLAlchemyTimeoutError) or (
    isinstance(error, DBAPIError)
    and _database_sqlstate(error) in _RETRYABLE_DATABASE_SQLSTATES
  )


def _report_error_text(error: Exception) -> str:
  # SQLAlchemy includes SQL and bound account data in str(DBAPIError).
  if isinstance(error, DBAPIError):
    return f"{type(error).__name__}: SQLSTATE={_database_sqlstate(error)}"
  if isinstance(error, SQLAlchemyTimeoutError):
    return "database connection pool timeout"
  return str(error)[:2000]


@dataclass(frozen=True)
class PendingOrderUpdate:
  """Outcome of monotonic PendingTradeOrder convergence for one report."""

  accepted: bool
  canonical_status: Optional[str] = None


def _snapshot_can_promote_heartbeat(status: Any) -> bool:
  """Only server-owned reconciliation states may be promoted by a snapshot.

  A delayed snapshot must never mask a newer runtime failure such as a lost
  XTTrading or XTData connection.  ``RECONCILE_REQUIRED`` is included because
  Engine itself assigns it after a blocked full snapshot; once an operator has
  repaired the discrepancy, a strictly newer clean full snapshot is the only
  proof allowed to restore the Agent to ``READY``.
  """
  return str(status or "").strip().upper() in _SNAPSHOT_PROMOTABLE_HEARTBEAT_STATUSES


def _body(payload: dict[str, Any], key: str) -> dict[str, Any]:
  nested = payload.get(key)
  return dict(nested) if isinstance(nested, dict) else dict(payload)


def _snapshot_hash_input(payload: dict[str, Any]) -> dict[str, Any]:
  return {
    key: value
    for key, value in payload.items()
    if key not in {"snapshot_hash", AGENT_SERVER_SESSION_PAYLOAD_KEY}
  }


def _report_account_ids(payload: dict[str, Any]) -> set[str]:
  """Return every funding account covered by one Agent report payload."""
  account_ids: set[str] = set()

  def add(value: Any) -> None:
    normalized = str(value or "").strip()
    if normalized:
      if (
        normalized not in account_ids
        and len(account_ids) >= _MAX_SNAPSHOT_ACCOUNT_SCOPE
      ):
        raise RetryableReportError(
          f"Agent snapshot account scope exceeds limit: {_MAX_SNAPSHOT_ACCOUNT_SCOPE}"
        )
      account_ids.add(normalized)

  add(payload.get("account_id"))
  for nested_name in ("order", "execution"):
    nested = payload.get(nested_name)
    if isinstance(nested, dict):
      add(nested.get("account_id"))
  for collection_name in (
    "accounts",
    "orders",
    "trades",
    "order_errors",
    "cancel_errors",
    "position_deltas",
    "positions",
  ):
    values = payload.get(collection_name) or []
    try:
      collection_count = len(values)
    except TypeError as exc:
      raise RetryableReportError(
        f"Agent snapshot {collection_name} scope不可有界复制"
      ) from exc
    if collection_count > _MAX_SNAPSHOT_ACCOUNT_SCOPE:
      raise RetryableReportError(
        f"Agent snapshot {collection_name} scope exceeds limit: "
        f"{_MAX_SNAPSHOT_ACCOUNT_SCOPE}"
      )
    for item in values:
      if isinstance(item, dict):
        add(item.get("account_id"))
  positions_by_account = payload.get("positions_by_account")
  if isinstance(positions_by_account, dict):
    if len(positions_by_account) > _MAX_SNAPSHOT_ACCOUNT_SCOPE:
      raise RetryableReportError(
        "Agent snapshot positions account scope exceeds limit: "
        f"{_MAX_SNAPSHOT_ACCOUNT_SCOPE}"
      )
    for account_id in positions_by_account:
      add(account_id)
  section_completeness = payload.get("section_completeness_by_account")
  if isinstance(section_completeness, dict):
    if len(section_completeness) > _MAX_SNAPSHOT_ACCOUNT_SCOPE:
      raise RetryableReportError(
        "Agent snapshot section account scope exceeds limit: "
        f"{_MAX_SNAPSHOT_ACCOUNT_SCOPE}"
      )
    for account_id in section_completeness:
      add(account_id)
  snapshot_authority = payload.get("snapshot_authority_by_account")
  if isinstance(snapshot_authority, dict):
    if len(snapshot_authority) > _MAX_SNAPSHOT_ACCOUNT_SCOPE:
      raise RetryableReportError(
        "Agent snapshot authority account scope exceeds limit: "
        f"{_MAX_SNAPSHOT_ACCOUNT_SCOPE}"
      )
    for account_id in snapshot_authority:
      add(account_id)
  unavailable_accounts = payload.get("unavailable_accounts") or []
  try:
    unavailable_count = len(unavailable_accounts)
  except TypeError as exc:
    raise RetryableReportError(
      "Agent snapshot unavailable account scope不可有界复制"
    ) from exc
  if unavailable_count > _MAX_SNAPSHOT_ACCOUNT_SCOPE:
    raise RetryableReportError(
      "Agent snapshot unavailable account scope exceeds limit: "
      f"{_MAX_SNAPSHOT_ACCOUNT_SCOPE}"
    )
  for account_id in unavailable_accounts:
    add(account_id)
  return account_ids


async def _invalidate_t_trade_entry_authority_for_account(
  account_id: str,
  *,
  reason: str,
) -> None:
  """Clear every in-memory V3 entry authority for one exact account.

  Report processing is an Engine concern, so it may reach the process-local
  StrategyManager.  Keep the lookup bounded to the executor's in-memory runs;
  durable state is not a substitute for the executor invalidation boundary.
  The caller owns the account coordination lock.  The executor boundary then
  acquires the per-runtime approval lock, preserving account -> approval order.
  """

  normalized_account = str(account_id or "").strip()
  if not normalized_account:
    raise ValueError("account_id is required for T-trade authority invalidation")

  # Lazy import avoids loading the Engine singleton during report module
  # initialization and keeps the authority boundary local to Engine.
  from .strategy_manager import strategy_manager

  executor = getattr(strategy_manager, "executor", None)
  runs = getattr(executor, "runs", None)
  if not hasattr(runs, "items"):
    raise RetryableReportError(
      "T-trade executor runtime registry unavailable; authority not cleared"
    )

  try:
    run_count = len(runs)
  except Exception as exc:
    raise RetryableReportError(
      "无法确定 T-trade runtime 数量，拒绝跳过账户 authority 清理"
    ) from exc
  if run_count > _MAX_TRADE_RUNTIME_AUTHORITY_SCAN:
    raise RetryableReportError(
      "T-trade runtime 数量超过 authority 清理上限: "
      f"{run_count}>{_MAX_TRADE_RUNTIME_AUTHORITY_SCAN}"
    )
  try:
    runtime_items = list(runs.items())
  except Exception as exc:
    raise RetryableReportError(
      "无法复制 T-trade runtime，拒绝跳过账户 authority 清理"
    ) from exc
  if len(runtime_items) > _MAX_TRADE_RUNTIME_AUTHORITY_SCAN:
    raise RetryableReportError(
      "T-trade runtime 复制结果超过 authority 清理上限: "
      f"{len(runtime_items)}>{_MAX_TRADE_RUNTIME_AUTHORITY_SCAN}"
    )

  uses_t_trade = getattr(executor, "_uses_t_trade_opportunity_runtime", None)
  invalidate = getattr(executor, "invalidate_t_trade_entry_authority", None)
  failures: list[str] = []
  for raw_run_id, runtime in runtime_items:
    context = getattr(runtime, "context", None)
    runtime_mode = getattr(getattr(context, "mode", None), "value", None)
    if str(runtime_mode or getattr(context, "mode", "") or "").upper() == ("BACKTEST"):
      # Broker snapshots are live account facts. A historical replay owns an
      # isolated BacktestBroker and must remain deterministic while a QMT
      # snapshot for the same account converges in this Engine process.
      continue
    parameters = dict(getattr(context, "parameters", {}) or {})
    runtime_account = str(parameters.get("account_id") or "").strip()
    if runtime_account != normalized_account:
      continue

    try:
      if callable(uses_t_trade):
        is_t_trade = bool(uses_t_trade(runtime))
      else:
        strategy = getattr(runtime, "strategy", None)
        strategy_class = getattr(runtime, "strategy_class", None)
        is_t_trade = bool(
          getattr(strategy, "USES_T_TRADE_OPPORTUNITY_PROFILE", False)
          or getattr(strategy_class, "USES_T_TRADE_OPPORTUNITY_PROFILE", False)
          or parameters.get("t_trade_opportunity_v3")
        )
    except Exception as exc:
      failures.append(f"{raw_run_id}: runtime classification failed: {exc}")
      continue
    if not is_t_trade:
      continue

    run_id = str(raw_run_id or getattr(runtime, "run_id", "")).strip()
    if not run_id or not callable(invalidate):
      failures.append(f"{run_id or raw_run_id}: invalidation boundary unavailable")
      continue
    try:
      invalidated = await invalidate(
        run_id,
        account_id=normalized_account,
        reason=reason,
      )
    except Exception as exc:
      failures.append(f"{run_id}: {exc}")
      continue
    if not bool(invalidated):
      failures.append(f"{run_id}: invalidation returned false")

  if failures:
    raise RetryableReportError(
      "T-trade entry authority invalidation failed: " + "; ".join(failures[:20])
    )


async def _run_account_snapshot_mutation(
  account_id: str,
  mutation: Callable[[], Awaitable[Any]],
  *,
  reason: str,
  affected_instrument_codes: Optional[Iterable[str]] = (),
) -> Any:
  """Linearize one durable snapshot mutation and authority invalidation."""

  normalized_account = str(account_id or "").strip()
  if not normalized_account:
    raise ValueError("account_id is required for snapshot mutation")

  async with t_trade_account_coordination_lock(normalized_account):
    mutation_error: Optional[Exception] = None
    result: Any = None
    try:
      result = await mutation()
    except Exception as exc:
      mutation_error = exc

    mutation_marker_error: Optional[Exception] = None
    if mutation_error is not None:
      try:
        marker = getattr(PositionService(), "mark_snapshot_failure", None)
        if not callable(marker):
          raise RuntimeError("PositionService.mark_snapshot_failure 不可用")
        await marker(
          normalized_account,
          f"{reason}:APPLY_FAILED",
        )
      except Exception as exc:
        mutation_marker_error = exc

    try:
      await _invalidate_t_trade_entry_authority_for_account(
        normalized_account,
        reason=reason,
      )
    except Exception as authority_error:
      detail = f"account={normalized_account}"
      if mutation_error is not None:
        detail += f", mutation={mutation_error!s}"
      raise RetryableReportError(
        "T-trade snapshot authority invalidation failed: " + detail
      ) from authority_error

    if mutation_marker_error is not None:
      raise RetryableReportError(
        "T-trade snapshot mutation failure marker failed: "
        f"account={normalized_account}, error={mutation_marker_error}"
      ) from mutation_marker_error
    if mutation_error is not None:
      raise mutation_error
    normalized_codes = (
      None
      if affected_instrument_codes is None
      else tuple(
        sorted(
          {
            str(code or "").strip().upper()
            for code in affected_instrument_codes
            if str(code or "").strip()
          }
        )
      )
    )
    if normalized_codes is None or normalized_codes:
      await _rederive_t_trade_exit_authorizations_after_position_update(
        normalized_account,
        instrument_codes=normalized_codes,
      )
    return result


async def _rederive_t_trade_exit_authorizations_after_position_update(
  account_id: str,
  *,
  instrument_codes: Optional[Iterable[str]],
) -> None:
  """Keep T-entry-derived LIVE exit grants convergent with broker positions."""

  try:
    await AutoExitPlanService().rederive_t_trade_exit_authorizations_after_position_update(
      account_id=account_id,
      instrument_codes=instrument_codes,
    )
  except Exception as exc:
    raise RetryableReportError(
      "做 T 自动退出授权未随持仓回报完成重算: "
      f"account={account_id}, error={exc}"
    ) from exc


_REQUIRED_SNAPSHOT_SECTIONS = ("account", "positions", "orders", "trades")


def _complete_snapshot_account_ids(
  payload: dict[str, Any],
) -> Optional[set[str]]:
  """Validate the unique protocol-1.2 full-snapshot completeness contract."""

  unavailable_accounts = payload.get("unavailable_accounts")
  section_completeness = payload.get("section_completeness_by_account")
  snapshot_authority = payload.get("snapshot_authority_by_account")
  accounts = payload.get("accounts")
  positions_by_account = payload.get("positions_by_account")
  if (
    not isinstance(unavailable_accounts, list)
    or unavailable_accounts
    or not isinstance(section_completeness, dict)
    or not isinstance(snapshot_authority, dict)
    or not isinstance(accounts, list)
    or not isinstance(positions_by_account, dict)
  ):
    return None
  scoped_values = (
    ("accounts", accounts),
    ("positions_by_account", positions_by_account),
    ("section_completeness_by_account", section_completeness),
    ("snapshot_authority_by_account", snapshot_authority),
    ("unavailable_accounts", unavailable_accounts),
  )
  for section_name, values in scoped_values:
    if len(values) > _MAX_SNAPSHOT_ACCOUNT_SCOPE:
      raise RetryableReportError(
        f"Agent snapshot {section_name} scope exceeds limit: "
        f"{_MAX_SNAPSHOT_ACCOUNT_SCOPE}"
      )
  account_record_ids = {
    str(item.get("account_id") or "").strip()
    for item in accounts
    if isinstance(item, dict) and str(item.get("account_id") or "").strip()
  }
  position_account_ids = {
    str(account_id).strip()
    for account_id in positions_by_account
    if str(account_id).strip()
  }
  section_account_ids = {
    str(account_id).strip()
    for account_id in section_completeness
    if str(account_id).strip()
  }
  authority_account_ids = {
    str(account_id).strip()
    for account_id in snapshot_authority
    if str(account_id).strip()
  }
  covered_accounts = _report_account_ids(payload)
  if (
    not covered_accounts
    or account_record_ids != covered_accounts
    or position_account_ids != covered_accounts
    or section_account_ids != covered_accounts
    or authority_account_ids != covered_accounts
  ):
    return None
  for account_id in covered_accounts:
    sections = section_completeness.get(account_id)
    if not isinstance(sections, dict) or not all(
      sections.get(section) is True for section in _REQUIRED_SNAPSHOT_SECTIONS
    ):
      return None
    if not snapshot_account_authority_is_authoritative(
      snapshot_authority.get(account_id)
    ):
      return None
  return covered_accounts


def _snapshot_authority_failure_reason(payload: dict[str, Any]) -> str:
  values = payload.get("snapshot_authority_by_account")
  if not isinstance(values, dict) or not values:
    return "ACCOUNT_STATUS_AUTHORITY_MISSING"
  reasons = sorted(
    {
      str(value.get("reason_code") or "ACCOUNT_STATUS_AUTHORITY_INVALID")
      for value in values.values()
      if isinstance(value, dict)
      and not snapshot_account_authority_is_authoritative(value)
    }
  )
  return ",".join(reasons) or "ACCOUNT_STATUS_AUTHORITY_VALID"


def _snapshot_section_is_complete(
  payload: dict[str, Any],
  account_id: str,
  section: str,
) -> bool:
  values = payload.get("section_completeness_by_account")
  if not isinstance(values, dict):
    return False
  account_values = values.get(account_id)
  return bool(isinstance(account_values, dict) and account_values.get(section) is True)


def _authoritative_full_snapshot_account_ids(
  payload: dict[str, Any],
  *,
  protocol_version: str,
) -> Optional[set[str]]:
  """Return covered accounts when a payload can promote a full snapshot."""

  if payload.get("is_complete") is not True or str(protocol_version) != PROTOCOL_VERSION:
    return None
  snapshot_id = str(payload.get("snapshot_id") or "").strip()
  snapshot_hash = str(payload.get("snapshot_hash") or "")
  if not snapshot_id or len(snapshot_hash) != 64:
    return None
  hash_input = _snapshot_hash_input(payload)
  expected_hash = sha256(
    json.dumps(
      hash_input,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode("utf-8")
  ).hexdigest()
  if expected_hash != snapshot_hash:
    return None
  return _complete_snapshot_account_ids(payload)


def _was_automatic_reconciliation_pause(reason: Any) -> bool:
  """Distinguish an Engine reconciliation pause from an operator pause."""
  if not isinstance(reason, str) or not reason.strip():
    return False
  try:
    items = json.loads(reason)
  except (TypeError, ValueError):
    return False
  return bool(
    isinstance(items, list)
    and items
    and all(
      isinstance(item, dict)
      and str(item.get("kind") or "") in _AUTOMATIC_RECONCILIATION_KINDS
      for item in items
    )
  )


def _broker_release_quarantine_boundary(
  reason: Any,
) -> tuple[str, int, Optional[datetime], str, str]:
  """Return the newest durable broker-release quarantine time, if present."""

  if not isinstance(reason, str) or not reason.strip():
    return "", 0, None, "", ""
  try:
    items = json.loads(reason)
  except (TypeError, ValueError):
    return "", 0, None, "", ""
  if not isinstance(items, list):
    return "", 0, None, "", ""
  matching = [
    item
    for item in items
    if isinstance(item, dict)
    and str(item.get("kind") or "")
    in {
      BROKER_EXECUTION_AFTER_RELEASE,
      "QUARANTINE_REPAIR_AWAITING_FRESH_SNAPSHOT",
    }
  ]
  if not matching:
    return "", 0, None, "", ""
  phase = (
    "UNREPAIRED_BROKER"
    if any(
      str(item.get("kind") or "") == BROKER_EXECUTION_AFTER_RELEASE
      for item in matching
    )
    else "REPAIR_AWAITING_FRESH_SNAPSHOT"
  )
  quarantine_sequence = max(
    (
      max(0, int(item.get("quarantineSourceSequence") or 0))
      for item in matching
    ),
    default=0,
  )
  parsed: list[datetime] = []
  for item in matching:
    value = str(item.get("quarantinedAt") or "").strip()
    if not value:
      continue
    try:
      parsed.append(to_naive_utc(datetime.fromisoformat(value.replace("Z", "+00:00"))))
    except (TypeError, ValueError, OverflowError):
      continue
  newest = matching[-1]
  return (
    phase,
    quarantine_sequence,
    max(parsed) if parsed else None,
    str(newest.get("repairSnapshotId") or ""),
    str(newest.get("repairSnapshotHash") or ""),
  )


async def _invalidate_monitor_snapshot_zero_fill_proof(
  db,
  pending: PendingTradeOrder,
  *,
  broker_order_id: str,
  evidence_status: str,
  source_sequence: int,
  execution_evidence: bool,
  cumulative_filled_volume: Optional[int] = None,
  evidence_key: str = "",
) -> None:
  """Fail closed one exact EXIT_PLAN binding contradicted by broker evidence."""

  pending_owner = _durable_owner_triple(pending)
  if _durable_exit_plan_sell_binding(pending) is not True or pending_owner is None:
    return
  # The persisted EXIT_PLAN owner id is authoritative.  Metadata can only
  # provide the optional conflict check performed above.
  plan_id = pending_owner[1]
  intent_id = str(pending.intent_id or "").strip()
  if (
    not plan_id
    or not intent_id
    or str(pending.side or "").upper() != "SELL"
  ):
    return
  try:
    evidence_sequence = max(0, int(source_sequence or 0))
  except (TypeError, ValueError, OverflowError):
    return
  normalized_status = str(evidence_status or "").upper()
  if not execution_evidence:
    if normalized_status not in ZERO_FILL_CONTRADICTION_ORDER_STATUSES:
      return
    stored_sequence = max(0, int(pending.last_source_sequence or 0))
    if evidence_sequence and evidence_sequence <= stored_sequence:
      return
  evidence_kind = "TRADE" if execution_evidence else "ORDER"
  stable_evidence_key = str(evidence_key or "").strip()
  if execution_evidence and not stable_evidence_key:
    # A trade without a durable execution identity cannot safely manufacture
    # a new broker fact from a snapshot/report sequence number.
    return
  if not stable_evidence_key:
    stable_evidence_key = (
      f"qmt-order:{pending.account_id}:{pending.client_order_id}:"
      f"{broker_order_id}:{normalized_status}"
    )
  await invalidate_exit_plan_zero_fill_proof(
    db,
    client_order_id=str(pending.client_order_id or ""),
    evidence_kind=evidence_kind,
    evidence_status=normalized_status,
    evidence_key=stable_evidence_key,
    broker_order_id=str(broker_order_id or ""),
    source_sequence=evidence_sequence,
    cumulative_filled_volume=cumulative_filled_volume,
  )


async def _update_pending(
  client_order_id: Optional[str],
  *,
  status: str,
  broker_order_id: Optional[str] = None,
  reason: Optional[str] = None,
  source_sequence: int = 0,
  source_event_at: Optional[datetime] = None,
  execution_evidence: bool = False,
  cumulative_filled_volume: Optional[int] = None,
  evidence_key: str = "",
) -> PendingOrderUpdate:
  if not client_order_id:
    return PendingOrderUpdate(False)
  async with AsyncSessionLocal() as db:
    pending = await db.get(PendingTradeOrder, client_order_id)
    if pending is None:
      return PendingOrderUpdate(False)
    # A client order id is the service-owned causal identity.  Prove the
    # broker fact against the durable correlation and owner chain before
    # touching pending status or cancellation state.  In particular, a
    # broker id supplied alongside the client id must match the correlation;
    # it is never allowed to select a different owner.
    correlation = await _correlation_for_report(
      db,
      client_order_id=str(client_order_id),
      broker_order_id=str(broker_order_id or ""),
      allow_broker_fallback=False,
    )
    if correlation is None:
      return PendingOrderUpdate(False)
    durable_exit_binding = _durable_exit_plan_sell_binding(pending, correlation)
    if durable_exit_binding is None:
      # A contradictory metadata witness or owner chain is a quarantine-only
      # condition.  It must not influence report routing or terminal replay
      # handling, so leave the pending row untouched.
      return PendingOrderUpdate(False)
    is_exit_plan_sell = durable_exit_binding
    if (
      not execution_evidence
      and is_exit_plan_sell
      and await is_exact_finalized_exit_order_replay(
        db,
        client_order_id=str(pending.client_order_id or ""),
        evidence_status=status,
        cumulative_filled_volume=cumulative_filled_volume,
      )
    ):
      sequence = max(0, int(source_sequence or 0))
      if sequence > max(0, int(pending.last_source_sequence or 0)):
        pending.last_source_sequence = sequence
        if source_event_at is not None:
          pending.last_source_event_at = to_naive_utc(source_event_at)
      pending.broker_order_id = broker_order_id or pending.broker_order_id
      pending.status_reason = "ignored finalized terminal replay"
      await db.commit()
      return PendingOrderUpdate(False)
    await _invalidate_monitor_snapshot_zero_fill_proof(
      db,
      pending,
      broker_order_id=str(broker_order_id or ""),
      evidence_status=status,
      source_sequence=source_sequence,
      execution_evidence=execution_evidence,
      cumulative_filled_volume=cumulative_filled_volume,
      evidence_key=evidence_key,
    )
    pending_metadata = dict(pending.request_metadata or {})
    sticky_reconcile_required = bool(
      pending_metadata.get(QUARANTINE_RECONCILE_REQUIRED_METADATA_KEY)
    )
    cancel_required_marker = bool(
      pending_metadata.get(QUARANTINE_CANCEL_REQUIRED_METADATA_KEY)
    )
    cancel_rejected = str(status or "").upper() == "CANCEL_REJECTED"
    cancel_requested = bool(
      str(pending.status or "").upper() == "CANCEL_REQUESTED"
      or cancel_required_marker
    )
    proposed_status = (
      str(pending.status or "PENDING")
      if cancel_rejected
      else _normalized_order_status(status)
    )
    proposed_terminal = proposed_status in TERMINAL_ORDER_STATUSES
    stored_sequence = int(pending.last_source_sequence or 0)
    sequence = max(0, int(source_sequence or 0))
    stale_sequence = bool(sequence and stored_sequence and sequence < stored_sequence)
    transition_allowed = not sticky_reconcile_required and not stale_sequence and (
      (cancel_requested and not proposed_terminal)
      or can_transition_order_status(pending.status, proposed_status)
    )
    if transition_allowed:
      pending.status = (
        "CANCEL_REQUESTED"
        if cancel_requested and not proposed_terminal
        else proposed_status[:24]
      )
      if sequence:
        pending.last_source_sequence = sequence
      if source_event_at is not None:
        pending.last_source_event_at = to_naive_utc(source_event_at)
      if proposed_terminal and cancel_required_marker:
        pending_metadata.pop(QUARANTINE_CANCEL_REQUIRED_METADATA_KEY, None)
        pending.request_metadata = pending_metadata
    elif sticky_reconcile_required and not stale_sequence:
      if sequence:
        pending.last_source_sequence = sequence
      if source_event_at is not None:
        pending.last_source_event_at = to_naive_utc(source_event_at)
    pending.broker_order_id = broker_order_id or pending.broker_order_id
    if stale_sequence:
      pending.status_reason = "ignored stale broker report"
    elif sticky_reconcile_required:
      pending.status = "RECONCILE_REQUIRED"
      pending.status_reason = str(
        pending.status_reason or "account quarantine requires explicit reconciliation"
      )[:256]
    elif cancel_rejected:
      pending.status_reason = (reason or "cancel rejected")[:256]
    elif cancel_requested and not proposed_terminal:
      pending.status_reason = str(pending.status_reason or "cancellation requested")[
        :256
      ]
    elif not transition_allowed:
      pending.status_reason = (f"ignored non-monotonic status {proposed_status}")[:256]
    else:
      pending.status_reason = (reason or "")[:256] or None
    if correlation is not None and broker_order_id:
      correlation.broker_order_id = broker_order_id
    if cancel_requested and not proposed_terminal and broker_order_id:
      try:
        owner_binding = _owner_ref_environment(pending)
        if owner_binding is None:
          raise AgentUnavailableError("取消命令缺少有效 owner/environment 绑定")
        await TradeCommandService(db).enqueue_cancel(
          user_id=str(pending.user_id),
          account_id=str(pending.account_id),
          broker_order_id=str(broker_order_id),
          idempotency_key=(f"entry-plan-cancel:{client_order_id}:{broker_order_id}"),
          execution_ref=owner_binding[0],
          environment=owner_binding[1],
          commit_transaction=False,
        )
      except AgentUnavailableError:
        # Keep the durable CANCEL_REQUESTED marker and account quarantine even
        # while no current Agent can route the command.  A later broker replay
        # retries this same stable business cancellation identity.
        pass
    await db.commit()
    return PendingOrderUpdate(
      accepted=bool(
        transition_allowed
        and not cancel_rejected
        and (not cancel_requested or proposed_terminal)
      ),
      canonical_status=(proposed_status if transition_allowed else None),
    )


async def _update_pending_by_broker(
  broker_order_id: Any,
  *,
  status: str,
  reason: str,
  source_sequence: int = 0,
  source_event_at: Optional[datetime] = None,
  execution_evidence: bool = False,
  cumulative_filled_volume: Optional[int] = None,
  evidence_key: str = "",
) -> PendingOrderUpdate:
  if broker_order_id is None:
    return PendingOrderUpdate(False)
  async with AsyncSessionLocal() as db:
    pending_candidates = (
      await db.execute(
        select(PendingTradeOrder).where(
          PendingTradeOrder.broker_order_id == str(broker_order_id)
        )
      )
    ).scalars().all()
    if len(pending_candidates) != 1:
      return PendingOrderUpdate(False)
    pending = pending_candidates[0]
    # Broker identity is a fallback only when the report has no client id.
    # Resolve the existing durable mapping and prove the exact owner chain
    # before applying any status transition.
    correlation = await _correlation_for_report(
      db,
      client_order_id="",
      broker_order_id=str(broker_order_id),
      allow_broker_fallback=True,
    )
    if (
      correlation is None
      or str(correlation.client_order_id or "")
      != str(pending.client_order_id or "")
    ):
      return PendingOrderUpdate(False)
    durable_exit_binding = _durable_exit_plan_sell_binding(pending, correlation)
    if durable_exit_binding is None:
      # Do not let a metadata-only owner witness select a different lifecycle
      # when the report arrived without its client order id.
      return PendingOrderUpdate(False)
    if (
      durable_exit_binding
      and not execution_evidence
      and await is_exact_finalized_exit_order_replay(
        db,
        client_order_id=str(pending.client_order_id or ""),
        evidence_status=status,
        cumulative_filled_volume=cumulative_filled_volume,
      )
    ):
      sequence = max(0, int(source_sequence or 0))
      if sequence > max(0, int(pending.last_source_sequence or 0)):
        pending.last_source_sequence = sequence
        if source_event_at is not None:
          pending.last_source_event_at = to_naive_utc(source_event_at)
      pending.status_reason = "ignored finalized terminal replay"
      await db.commit()
      return PendingOrderUpdate(False)
    await _invalidate_monitor_snapshot_zero_fill_proof(
      db,
      pending,
      broker_order_id=str(broker_order_id or ""),
      evidence_status=status,
      source_sequence=source_sequence,
      execution_evidence=execution_evidence,
      cumulative_filled_volume=cumulative_filled_volume,
      evidence_key=evidence_key,
    )
    pending_metadata = dict(pending.request_metadata or {})
    sticky_reconcile_required = bool(
      pending_metadata.get(QUARANTINE_RECONCILE_REQUIRED_METADATA_KEY)
    )
    cancel_required_marker = bool(
      pending_metadata.get(QUARANTINE_CANCEL_REQUIRED_METADATA_KEY)
    )
    proposed_status = _normalized_order_status(status)
    cancel_requested = bool(
      str(pending.status or "").upper() == "CANCEL_REQUESTED"
      or cancel_required_marker
    )
    proposed_terminal = proposed_status in TERMINAL_ORDER_STATUSES
    sequence = max(0, int(source_sequence or 0))
    stored_sequence = int(pending.last_source_sequence or 0)
    transition_allowed = not sticky_reconcile_required and (
      not sequence or not stored_sequence or sequence >= stored_sequence
    ) and (
      (cancel_requested and not proposed_terminal)
      or can_transition_order_status(pending.status, proposed_status)
    )
    if transition_allowed:
      pending.status = (
        "CANCEL_REQUESTED"
        if cancel_requested and not proposed_terminal
        else proposed_status[:24]
      )
      if sequence:
        pending.last_source_sequence = sequence
      if source_event_at is not None:
        pending.last_source_event_at = to_naive_utc(source_event_at)
      if proposed_terminal and cancel_required_marker:
        pending_metadata.pop(QUARANTINE_CANCEL_REQUIRED_METADATA_KEY, None)
        pending.request_metadata = pending_metadata
    if sticky_reconcile_required:
      pending.status = "RECONCILE_REQUIRED"
      pending.status_reason = str(
        pending.status_reason or "account quarantine requires explicit reconciliation"
      )[:256]
    else:
      pending.status_reason = (
        str(pending.status_reason or "cancellation requested")[:256]
        if cancel_requested and not proposed_terminal
        else reason[:256] or None
      )
    if cancel_requested and not proposed_terminal:
      try:
        owner_binding = _owner_ref_environment(pending)
        if owner_binding is None:
          raise AgentUnavailableError("取消命令缺少有效 owner/environment 绑定")
        await TradeCommandService(db).enqueue_cancel(
          user_id=str(pending.user_id),
          account_id=str(pending.account_id),
          broker_order_id=str(broker_order_id),
          idempotency_key=(
            f"entry-plan-cancel:{pending.client_order_id}:{broker_order_id}"
          ),
          execution_ref=owner_binding[0],
          environment=owner_binding[1],
          commit_transaction=False,
        )
      except AgentUnavailableError:
        pass
    await db.commit()
    return PendingOrderUpdate(
      accepted=bool(
        transition_allowed
        and proposed_status != "CANCEL_REJECTED"
        and (not cancel_requested or proposed_terminal)
      ),
      canonical_status=(proposed_status if transition_allowed else None),
    )


async def _process_order_report(
  payload: dict[str, Any],
  *,
  protocol_version: str = PROTOCOL_VERSION,
) -> None:
  _require_current_protocol(protocol_version)
  order = _body(payload, "order")
  cumulative_filled_volume = _reported_cumulative_fill(order)
  cumulative_fill_state = _reported_cumulative_fill_state(order)
  broker_order_id = order.get("order_id") or order.get("broker_order_id")
  if broker_order_id is None:
    raise ValueError("order_report 缺少 broker order id")
  order["order_id"] = int(broker_order_id)
  order.setdefault("order_sysid", str(broker_order_id)[-10:])
  order.setdefault("order_time", int(time_utils.now().timestamp()))
  order.setdefault("traded_volume", 0)
  order.setdefault("traded_price", 0)
  order.setdefault("order_status", 49)
  order.setdefault("price_type", 50)
  await OrderService(str(order.get("account_id", ""))).upsert_report(order)
  status = _normalized_order_status(
    order.get("effective_order_status")
    or order.get("status")
    or order.get("order_status")
    or "SUBMITTED"
  )
  client_order_id = str(payload.get("client_order_id") or "")
  source_sequence = int(payload.get("source_sequence") or 0)
  source_event_at = _parse_report_time(payload.get("source_event_at"))
  reason = str(
    order.get("effective_status_reason") or order.get("status_msg") or ""
  )
  if client_order_id:
    update_result = await _update_pending(
      client_order_id,
      status=status,
      broker_order_id=str(broker_order_id),
      reason=reason,
      source_sequence=source_sequence,
      source_event_at=source_event_at,
      cumulative_filled_volume=cumulative_filled_volume,
    )
  else:
    update_result = await _update_pending_by_broker(
      broker_order_id,
      status=status,
      reason=reason,
      source_sequence=source_sequence,
      source_event_at=source_event_at,
      cumulative_filled_volume=cumulative_filled_volume,
    )
  if update_result.accepted:
    await AutoExitPlanService().apply_order_event_for_report(
      client_order_id=client_order_id,
      broker_order_id=str(broker_order_id),
      status=str(update_result.canonical_status or status),
      source_sequence=source_sequence,
      cumulative_filled_volume=cumulative_filled_volume,
      cumulative_fill_state=cumulative_fill_state,
    )


async def _process_execution_report(
  payload: dict[str, Any],
  *,
  protocol_version: str = PROTOCOL_VERSION,
) -> None:
  _require_current_protocol(protocol_version)
  trade = _body(payload, "execution")
  broker_order_id = trade.get("order_id") or trade.get("broker_order_id")
  if broker_order_id is None:
    raise ValueError("execution_report 缺少 broker order id")
  order = await OrderService(str(trade.get("account_id", ""))).get_order_by_id(
    int(broker_order_id)
  )
  if order is None:
    raise RetryableReportError("对应 order_report 尚未收敛")
  trade["order_id"] = int(broker_order_id)
  trade.setdefault("traded_id", trade.get("execution_id"))
  trade.setdefault("order_sysid", order.sysid)
  trade.setdefault("order_type", int(order.type))
  trade.setdefault("traded_time", int(time_utils.now().timestamp()))
  trade.setdefault(
    "traded_amount",
    float(trade.get("traded_price") or 0) * int(trade.get("traded_volume") or 0),
  )
  if not trade.get("traded_id"):
    raise ValueError("execution_report 缺少 execution id")
  await TradeService(str(trade.get("account_id", ""))).upsert_report(trade)
  await _consume_exact_auto_entry_fill(payload, trade)
  client_order_id = str(payload.get("client_order_id") or "")
  status = str(payload.get("order_status") or "PARTIAL_FILLED")
  source_sequence = int(payload.get("source_sequence") or 0)
  source_event_at = _parse_report_time(payload.get("source_event_at"))
  execution_id = str(
    trade.get("execution_id")
    or trade.get("traded_id")
    or trade.get("trade_id")
    or ""
  ).strip()
  evidence_key = (
    f"qmt-trade:{trade.get('account_id')}:{execution_id}" if execution_id else ""
  )
  if client_order_id:
    update_result = await _update_pending(
      client_order_id,
      status=status,
      broker_order_id=str(broker_order_id),
      source_sequence=source_sequence,
      source_event_at=source_event_at,
      execution_evidence=True,
      evidence_key=evidence_key,
    )
  else:
    update_result = await _update_pending_by_broker(
      broker_order_id,
      status=status,
      reason="",
      source_sequence=source_sequence,
      source_event_at=source_event_at,
      execution_evidence=True,
      evidence_key=evidence_key,
    )
  if update_result.accepted:
    await AutoExitPlanService().apply_order_event_for_report(
      client_order_id=client_order_id,
      broker_order_id=str(broker_order_id),
      status=str(update_result.canonical_status or status),
      source_sequence=source_sequence,
    )
  await AutoExitPlanService().apply_execution_for_report(
    execution_id=str(trade.get("execution_id") or trade.get("traded_id") or ""),
    client_order_id=client_order_id,
    broker_order_id=str(broker_order_id),
    volume=int(trade.get("traded_volume") or 0),
    price=float(trade.get("traded_price") or 0.0),
  )


async def _consume_exact_auto_entry_fill(
  payload: dict[str, Any],
  trade: dict[str, Any],
) -> None:
  """Debit an exact managed-entry grant only for a durable LIVE BUY trade.

  Command acknowledgements and order reports never call this function.  The
  QMT execution id is the idempotency key, so inbox retries and full-snapshot
  replay cannot consume authorization twice.
  """

  client_order_id = str(
    payload.get("client_order_id") or trade.get("client_order_id") or ""
  )
  broker_order_id = str(trade.get("order_id") or trade.get("broker_order_id") or "")
  async with AsyncSessionLocal() as db:
    pending = (
      await db.get(PendingTradeOrder, client_order_id) if client_order_id else None
    )
    if pending is None and broker_order_id:
      pending = (
        await db.execute(
          select(PendingTradeOrder).where(
            PendingTradeOrder.broker_order_id == broker_order_id
          )
        )
      ).scalar_one_or_none()
    pending_owner = _durable_owner_triple(pending) if pending is not None else None
    if (
      pending is None
      or pending_owner is None
      or pending_owner[0] != ExecutionOwnerType.STRATEGY_RUN.value
      or pending_owner[2] != ExecutionEnvironment.LIVE.value
      or str(pending.side or "").upper() != "BUY"
    ):
      return
    reported_account_id = str(trade.get("account_id") or "")
    reported_instrument = str(
      trade.get("stock_code") or trade.get("instrument_code") or ""
    )
    if (reported_account_id and reported_account_id != str(pending.account_id)) or (
      reported_instrument and reported_instrument != str(pending.instrument_code)
    ):
      raise ValueError("LIVE 自动买入成交账户或标的与权威命令不匹配")
    metadata = dict(pending.request_metadata or {})
    plan_id = str(metadata.get("entry_plan_id") or "")
    run_id = pending_owner[1]
    grant_id = str(metadata.get("auto_entry_authorization_grant_id") or "")
    if not plan_id or not grant_id:
      return
    if not run_id or not bool(metadata.get("exact_auto_entry_authorized")):
      raise ValueError("LIVE 自动买入成交缺少已验证的计划授权关联")
    intent = await db.get(TradeIntentRecord, str(pending.intent_id or ""))
    intent_metadata = dict(intent.intent_metadata or {}) if intent is not None else {}
    if (
      intent is None
      or str(intent.strategy_run_id or "") != run_id
      or str(intent.direction or "").upper() != "BUY"
      or str(intent_metadata.get("entry_plan_id") or plan_id) != plan_id
      or str(intent_metadata.get("execution_mode") or "").upper() != "AUTO"
      or str(intent_metadata.get("auto_entry_authorization_grant_id") or "") != grant_id
    ):
      raise ValueError("LIVE 自动买入成交与权威意图授权不匹配")
    execution_id = str(
      trade.get("execution_id") or trade.get("traded_id") or ""
    ).strip()
    price = Decimal(str(trade.get("traded_price") or trade.get("price") or 0))
    volume = int(trade.get("traded_volume") or trade.get("volume") or 0)
    if not execution_id or not price.is_finite() or price <= 0 or volume <= 0:
      raise ValueError("LIVE 自动买入成交事实无效")
    filled_at = _parse_report_time(trade.get("traded_time") or trade.get("trade_time"))
    await EntryPlanAuthorizationService(db).consume_real_fill(
      grant_id=grant_id,
      trade_business_key=f"qmt-entry:{pending.account_id}:{execution_id}"[:160],
      filled_amount_cny=price * volume,
      filled_volume=volume,
      fill_price=price,
      filled_at=filled_at,
    )


async def _upsert_account(value: dict[str, Any]) -> None:
  account_id = str(value.get("account_id") or "")
  if not account_id:
    raise ValueError("账户快照缺少 account_id")
  raw_type = value.get("account_type", 2)
  account_type = (
    raw_type
    if isinstance(raw_type, AccountType)
    else AccountType.from_int(int(raw_type))
  )
  if account_type is None:
    account_type = AccountType.STOCK
  account = Account(
    id=md5(f"{account_id}:{account_type.value}".encode("utf-8")).hexdigest(),
    account_id=account_id,
    account_type=account_type,
    total_asset=value.get("total_asset", 0),
    cash=value.get("cash", 0),
    market_value=value.get("market_value", 0),
    frozen_cash=value.get("frozen_cash", 0),
  )
  async with AsyncSessionLocal() as db:
    await AccountRepository(db).save(account)


async def _fail_closed_incomplete_snapshot(
  device_id: str,
  payload: dict[str, Any],
  *,
  reported_at: datetime,
  failure_kind: str,
  failure_reason: str,
  account_ids_override: Optional[set[str]] = None,
) -> None:
  """Invalidate live authority after an attempted incomplete full snapshot."""

  scope_validation_failed = False
  if account_ids_override is not None:
    try:
      override_count = len(account_ids_override)
    except TypeError as exc:
      raise RetryableReportError("完整账户快照 override 账户范围不可有界复制") from exc
    if override_count > _MAX_SNAPSHOT_ACCOUNT_SCOPE:
      raise RetryableReportError(
        f"完整账户快照 override 账户范围超过限制: {_MAX_SNAPSHOT_ACCOUNT_SCOPE}"
      )
    account_ids = {
      str(value).strip() for value in account_ids_override if str(value).strip()
    }
  else:
    try:
      account_ids = _report_account_ids(payload)
    except RetryableReportError:
      # A malformed full report can fail scope discovery before the ordinary
      # device-scope fallback below. Preserve fail-closed behavior by using
      # the authenticated scope rather than abandoning the stale marker.
      account_ids = set()
      scope_validation_failed = True
  if not account_ids:
    # A malformed report may omit every account field.  The authenticated
    # device scope is the only safe fallback for the personal single-account
    # deployment; silently returning would leave the previous complete
    # snapshot and in-memory entry authority usable.
    async with AsyncSessionLocal() as lookup_db:
      try:
        device = await lookup_db.get(AgentDevice, device_id)
      except Exception as exc:
        raise RetryableReportError(
          "无法读取 Agent 授权账户，拒绝忽略无账户快照失败"
        ) from exc
      if device is not None and device.revoked_at is None:
        authorized_values = device.authorized_account_ids or []
        if isinstance(authorized_values, str):
          authorized_values = [authorized_values]
        try:
          authorized_count = len(authorized_values)
        except TypeError as exc:
          raise RetryableReportError("Agent 授权账户范围不可有界复制") from exc
        if authorized_count > _MAX_SNAPSHOT_ACCOUNT_SCOPE:
          raise RetryableReportError(
            f"Agent 授权账户范围超过限制: {_MAX_SNAPSHOT_ACCOUNT_SCOPE}"
          )
        account_ids = {
          str(value).strip() for value in list(authorized_values) if str(value).strip()
        }
    if not account_ids:
      raise RetryableReportError(
        "完整账户快照失败但无法从报告或 Agent 授权范围确定账户"
      )
  snapshot_id = str(payload.get("snapshot_id") or "").strip() or None
  raw_section_values = payload.get("section_completeness_by_account")
  section_values = (
    raw_section_values
    if isinstance(raw_section_values, dict)
    and len(raw_section_values) <= _MAX_SNAPSHOT_ACCOUNT_SCOPE
    else None
  )
  raw_unavailable_accounts = payload.get("unavailable_accounts") or []
  try:
    unavailable_count = len(raw_unavailable_accounts)
  except TypeError:
    unavailable_count = _MAX_SNAPSHOT_ACCOUNT_SCOPE + 1
  unavailable_accounts = (
    {str(value).strip() for value in raw_unavailable_accounts if str(value).strip()}
    if unavailable_count <= _MAX_SNAPSHOT_ACCOUNT_SCOPE
    else set()
  )
  blocked_accounts: list[str] = []
  authority_failures: list[str] = []
  snapshot_failures: list[str] = []
  position_service = PositionService()
  async with AsyncSessionLocal() as db:
    for account_id in sorted(account_ids):
      raw_sections = section_values.get(account_id) if section_values else None
      incomplete_sections = [
        section
        for section in _REQUIRED_SNAPSHOT_SECTIONS
        if not isinstance(raw_sections, dict) or raw_sections.get(section) is not True
      ]
      discrepancy = {
        "kind": failure_kind,
        "reason": failure_reason,
        "business_id": account_id,
      }
      if scope_validation_failed:
        discrepancy["scopeValidation"] = "BOUNDED_DEVICE_FALLBACK"
      if failure_kind == "SNAPSHOT_SECTION_INCOMPLETE":
        discrepancy.update(
          {
            "sections": incomplete_sections,
            "unavailable": account_id in unavailable_accounts,
          }
        )
      discrepancies = [discrepancy]
      # Account coordination is intentionally scoped to the snapshot/failure
      # authority boundary.  Order/trade convergence remains outside this
      # lock, while monitor/candidate/approval cannot observe the old complete
      # snapshot or old V3 entry authority during this transition.
      async with t_trade_account_coordination_lock(account_id):
        try:
          await position_service.mark_snapshot_failure(
            account_id,
            f"{failure_kind}:{failure_reason}",
          )
        except Exception as exc:
          snapshot_failures.append(f"{account_id}: {exc}")
        try:
          await _invalidate_t_trade_entry_authority_for_account(
            account_id,
            reason=f"{failure_kind}:{failure_reason}",
          )
        except Exception as exc:
          authority_failures.append(f"{account_id}: {exc}")

        rollout = await db.get(
          AccountExecutionControl,
          account_id,
          with_for_update=True,
        )
        if rollout is None:
          rollout = AccountExecutionControl(account_id=account_id)
          db.add(rollout)
        previous_state = str(rollout.authorization_state)
        window_was_active = bool(rollout.controlled_window_active)
        rollout.reconcile_status = "RECONCILE_REQUIRED"
        if rollout.authorization_state != "KILLED":
          rollout.authorization_state = "PAUSED"
        rollout.state_version = int(rollout.state_version or 0) + 1
        rollout.paused_reason = json.dumps(
          discrepancies,
          ensure_ascii=False,
          default=str,
        )[:2000]
        if window_was_active:
          rollout.controlled_window_active = False
          rollout.controlled_window_snapshot_id = None
          rollout.controlled_window_snapshot_hash = None
          rollout.controlled_window_started_at = None
          rollout.controlled_window_started_by_user_id = None
          rollout.controlled_window_external_order_ids = []
          rollout.controlled_window_external_trade_ids = []
        db.add(
          AccountExecutionControlEvent(
            event_id=str(uuid.uuid4()),
            account_id=account_id,
            event_type="SNAPSHOT_INCOMPLETE",
            previous_state=previous_state,
            next_state=str(rollout.authorization_state),
            snapshot_id=snapshot_id,
            details={
              "deviceId": device_id,
              "reportedAt": reported_at.isoformat(),
              "discrepancies": discrepancies,
              "controlledWindowInvalidated": window_was_active,
            },
            created_at=utcnow(),
          )
        )
        blocked_accounts.append(account_id)
        # Commit the durable fail-closed rollout marker before releasing this
        # account lock.  ``mark_snapshot_failure`` normally commits through a
        # separate PositionService session; if that write failed, this marker
        # is the remaining durable barrier against monitor re-authorization.
        await db.commit()

    heartbeat = await db.get(
      RuntimeComponentHeartbeat,
      f"qmt-agent:{device_id}",
    )
    if heartbeat is not None and report_belongs_to_current_session(
      payload,
      heartbeat,
      now=utcnow(),
    ):
      details = dict(heartbeat.details or {})
      details["incompleteSnapshotAccounts"] = blocked_accounts
      details["incompleteSnapshotAt"] = reported_at.isoformat()
      heartbeat.details = details
      if str(heartbeat.status or "").upper() in {"READY", "RECONCILING"}:
        heartbeat.status = "RECONCILE_REQUIRED"
    await db.commit()
  if snapshot_failures or authority_failures:
    details = [*snapshot_failures, *authority_failures]
    raise RetryableReportError(
      "完整账户快照失败边界未完全收敛: " + "; ".join(details[:20])
    )


async def _process_delta_report(
  device_id: str,
  payload: dict[str, Any],
  *,
  protocol_version: str = PROTOCOL_VERSION,
) -> None:
  _require_current_protocol(protocol_version)
  full_attempt_state: dict[str, set[str]] = {}
  try:
    await _process_delta_report_inner(
      device_id,
      payload,
      protocol_version=protocol_version,
      _full_attempt_state=full_attempt_state,
    )
  except Exception:
    # Once a protocol-1.2 full payload has passed identity/completeness
    # validation, *any* later failure (including order/trade convergence,
    # sequence conversion, position promotion, or rollout projection) must
    # invalidate the snapshot.  This closes the old-complete/new-complete
    # ambiguity before the report is retried or dead-lettered.
    full_snapshot_attempt = bool(
      payload.get("is_complete") is True
      or "section_completeness_by_account" in payload
      or "unavailable_accounts" in payload
    )
    scope_validation_failed = False
    try:
      authoritative_accounts = _authoritative_full_snapshot_account_ids(
        payload,
        protocol_version=protocol_version,
      )
    except Exception:
      # Scope validation itself may fail before the inner handler reaches its
      # normal invalid-full fail-close path (for example, a 4097-account
      # positions map).  Do not repeat the unbounded parser here.  Passing an
      # explicit empty override makes the failure helper derive the bounded,
      # authenticated device scope instead.
      authoritative_accounts = None
      scope_validation_failed = True
    if not scope_validation_failed and full_snapshot_attempt:
      try:
        _report_account_ids(payload)
      except RetryableReportError:
        # Identity validation can return ``None`` before it reaches the
        # account-scope parser (for example, a missing hash on an oversized
        # full payload).  Recheck the bounded scope independently so malformed
        # reports still take the authenticated-device fail-close path.
        scope_validation_failed = True
    stale_accounts = set(full_attempt_state.get("stale_accounts") or set())
    already_failed_accounts = set(
      full_attempt_state.get("already_failed_accounts") or set()
    )
    failure_accounts = (
      authoritative_accounts - stale_accounts - already_failed_accounts
      if authoritative_accounts is not None
      else set()
    )
    if failure_accounts or (scope_validation_failed and full_snapshot_attempt):
      await _fail_closed_incomplete_snapshot(
        device_id,
        payload,
        reported_at=_safe_snapshot_failure_time(payload.get("source_event_at")),
        failure_kind="SNAPSHOT_APPLY_FAILED",
        failure_reason="FULL_SNAPSHOT_APPLY_FAILED",
        account_ids_override=failure_accounts,
      )
    raise


async def _process_delta_report_inner(
  device_id: str,
  payload: dict[str, Any],
  *,
  protocol_version: str = PROTOCOL_VERSION,
  _full_attempt_state: Optional[dict[str, set[str]]] = None,
) -> None:
  _require_current_protocol(protocol_version)
  full_attempt_state = _full_attempt_state if _full_attempt_state is not None else {}
  stale_full_accounts: set[str] = set()
  full_attempt_state["stale_accounts"] = stale_full_accounts
  declared_complete = payload.get("is_complete") is True
  full_snapshot_attempt = bool(
    declared_complete
    or "section_completeness_by_account" in payload
    or "snapshot_authority_by_account" in payload
    or "unavailable_accounts" in payload
  )
  complete_account_ids = (
    _complete_snapshot_account_ids(payload) if declared_complete else None
  )
  snapshot_id = str(payload.get("snapshot_id") or "")
  snapshot_hash = str(payload.get("snapshot_hash") or "")
  snapshot_identity_error = ""
  identity_valid = False
  if declared_complete:
    if not snapshot_id or len(snapshot_hash) != 64:
      snapshot_identity_error = "完整账户快照缺少协议 1.2 身份"
    else:
      hash_input = _snapshot_hash_input(payload)
      expected_hash = sha256(
        json.dumps(
          hash_input,
          sort_keys=True,
          separators=(",", ":"),
          default=str,
        ).encode("utf-8")
      ).hexdigest()
      if expected_hash != snapshot_hash:
        snapshot_identity_error = "完整账户快照哈希校验失败"
      else:
        identity_valid = True
  authoritative = bool(
    declared_complete
    and protocol_version == PROTOCOL_VERSION
    and complete_account_ids is not None
    and identity_valid
  )
  reported_at = (
    _parse_authoritative_snapshot_time(payload.get("source_event_at"))
    if authoritative
    else (
      _safe_snapshot_failure_time(payload.get("source_event_at"))
      if full_snapshot_attempt
      else _parse_report_time(payload.get("source_event_at"))
    )
  )
  position_service = PositionService()
  full_snapshot_sequence: Optional[int] = None
  full_snapshot_groups: dict[str, list[Any]] = {}
  if authoritative:
    server_session = payload.get(AGENT_SERVER_SESSION_PAYLOAD_KEY)
    raw_authorized_account_ids = (
      server_session.get("authorizedAccountIds")
      if isinstance(server_session, dict)
      else None
    )
    authorized_account_ids = (
      {str(value).strip() for value in raw_authorized_account_ids if str(value).strip()}
      if isinstance(raw_authorized_account_ids, list)
      else None
    )
    reported_account_ids = set(complete_account_ids or set())
    if (
      authorized_account_ids is not None
      and reported_account_ids != authorized_account_ids
    ):
      full_attempt_state["already_failed_accounts"] = (
        set(authorized_account_ids) | reported_account_ids
      )
      await _fail_closed_incomplete_snapshot(
        device_id,
        payload,
        reported_at=reported_at,
        failure_kind="SNAPSHOT_ACCOUNT_MISMATCH",
        failure_reason=QMT_ACCOUNT_MISMATCH,
        account_ids_override=set(authorized_account_ids),
      )
      async with AsyncSessionLocal() as mismatch_db:
        heartbeat = await mismatch_db.get(
          RuntimeComponentHeartbeat,
          f"qmt-agent:{device_id}",
          with_for_update=True,
        )
        if heartbeat is not None and report_belongs_to_current_session(
          payload,
          heartbeat,
          now=utcnow(),
        ):
          details = dict(heartbeat.details or {})
          details.update(
            {
              "reasonCode": QMT_ACCOUNT_MISMATCH,
              "reportedAccountCount": len(reported_account_ids),
              "authorizedAccountCount": len(authorized_account_ids),
            }
          )
          heartbeat.status = QMT_ACCOUNT_MISMATCH
          heartbeat.details = details
          await mismatch_db.commit()
      raise ValueError(QMT_ACCOUNT_MISMATCH)
    # Parse and snapshot the account groups before any order/trade convergence.
    # A malformed sequence is an authoritative full-attempt failure and is
    # handled by the outer fail-closed boundary.
    full_snapshot_sequence = _parse_authoritative_snapshot_sequence(payload)
    groups_value = payload.get("positions_by_account")
    if isinstance(groups_value, dict):
      full_snapshot_groups = {
        str(account_id or "").strip(): list(positions or [])
        for account_id, positions in groups_value.items()
        if str(account_id or "").strip()
      }
    else:
      account_id = str(payload.get("account_id") or "").strip()
      if account_id:
        full_snapshot_groups[account_id] = list(payload.get("positions") or [])
  if full_snapshot_attempt and not authoritative:
    if protocol_version != PROTOCOL_VERSION:
      failure_kind = "SNAPSHOT_PROTOCOL_INVALID"
      failure_reason = "PROTOCOL_1_2_REQUIRED"
    elif snapshot_identity_error:
      failure_kind = "SNAPSHOT_IDENTITY_INVALID"
      failure_reason = (
        "SNAPSHOT_HASH_MISMATCH"
        if "哈希" in snapshot_identity_error
        else "SNAPSHOT_IDENTITY_MISSING"
      )
    else:
      authority_reason = _snapshot_authority_failure_reason(payload)
      if authority_reason not in {
        "ACCOUNT_STATUS_AUTHORITY_MISSING",
        "ACCOUNT_STATUS_AUTHORITY_VALID",
      }:
        failure_kind = "SNAPSHOT_ACCOUNT_STATUS_INVALID"
        failure_reason = authority_reason
      else:
        failure_kind = "SNAPSHOT_SECTION_INCOMPLETE"
        failure_reason = "SECTION_PROOF_MISSING_OR_INCOMPLETE"
    # Close the durable trading gate before processing any partial section.
    # A concurrent order enqueue must never observe the prior READY rollout.
    await _fail_closed_incomplete_snapshot(
      device_id,
      payload,
      reported_at=reported_at,
      failure_kind=failure_kind,
      failure_reason=failure_reason,
    )
    if snapshot_identity_error:
      raise ValueError(snapshot_identity_error)
    if declared_complete:
      raise ValueError(
        "完整账户快照缺少可接受的账户状态与分区权威证明"
      )
    # Expected unavailable/incomplete observations are durable failure
    # metadata, not partial account facts.  ACK them after closing the gate.
    return

  if authoritative:
    begin_full_snapshot_attempt = getattr(
      position_service,
      "begin_full_snapshot_attempt",
      None,
    )
    invalidate_reason = "SNAPSHOT_APPLY_IN_PROGRESS"
    # Full snapshots first CAS their observed generation into a durable
    # in-progress state.  This closes the old-complete window before any
    # order/trade convergence runs outside the account lock.  A duplicate
    # sequence is deliberately a no-op: the newer complete snapshot must
    # remain usable and must never be downgraded.
    if not callable(begin_full_snapshot_attempt):
      raise RetryableReportError("完整快照缺少持久化 begin attempt 边界")
    for account_id in sorted(full_snapshot_groups):
      async with t_trade_account_coordination_lock(account_id):
        result = await begin_full_snapshot_attempt(
          account_id=account_id,
          sequence=int(full_snapshot_sequence or 0),
          reported_at=reported_at,
          source="QMT_AGENT",
        )
        result_dict = dict(result) if isinstance(result, dict) else {}
        if result_dict.get("reason") == "STALE_SEQUENCE":
          stale_full_accounts.add(account_id)
          continue
        if not result_dict.get("applied", False):
          raise RetryableReportError(
            "完整快照 begin attempt 未完成: "
            + str(result_dict.get("reason") or "UNKNOWN")
          )
        await _invalidate_t_trade_entry_authority_for_account(
          account_id,
          reason=invalidate_reason,
        )

  all_authoritative_accounts = set(full_snapshot_groups)

  def skip_stale_full_item(
    value: Any,
    *,
    unknown_account_is_stale: bool = False,
  ) -> bool:
    if not authoritative or not stale_full_accounts:
      return False
    raw_account_id = value.get("account_id") if isinstance(value, dict) else ""
    account_id = str(raw_account_id or "").strip()
    return bool(
      account_id in stale_full_accounts
      or (not account_id and (unknown_account_is_stale or bool(stale_full_accounts)))
    )

  for order in payload.get("orders") or []:
    if skip_stale_full_item(order):
      continue
    await _process_order_report(
      {
        "client_order_id": order.get("client_order_id"),
        "source_sequence": order.get("source_sequence")
        or payload.get("source_sequence")
        or payload.get("sequence"),
        "source_event_at": order.get("source_event_at")
        or payload.get("source_event_at"),
        "order": dict(order),
      }
    )
  for trade in payload.get("trades") or []:
    if skip_stale_full_item(trade):
      continue
    await _process_execution_report(
      {
        "client_order_id": trade.get("client_order_id"),
        "source_sequence": trade.get("source_sequence")
        or payload.get("source_sequence")
        or payload.get("sequence"),
        "source_event_at": trade.get("source_event_at")
        or payload.get("source_event_at"),
        # A TRADE callback proves only this execution.  The authoritative
        # terminal lifecycle remains a separate ORDER report.
        "order_status": trade.get("order_status") or "PARTIAL_FILLED",
        "execution": dict(trade),
      }
    )
  for account in payload.get("accounts") or []:
    account_value = dict(account)
    account_id = str(account_value.get("account_id") or "").strip()
    if skip_stale_full_item(account_value):
      continue
    if full_snapshot_attempt and not _snapshot_section_is_complete(
      payload,
      account_id,
      "account",
    ):
      continue
    await _upsert_account(account_value)
  for error in payload.get("order_errors") or []:
    if skip_stale_full_item(error, unknown_account_is_stale=True):
      continue
    reason = str(error.get("error_msg") or error.get("reason") or "")
    terminal_status = (
      "EXPIRED"
      if str(error.get("reason") or "").strip().lower() == "command_expired"
      else "REJECTED"
    )
    client_order_id = str(error.get("client_order_id") or "")
    broker_order_id = str(error.get("order_id") or error.get("broker_order_id") or "")
    if client_order_id:
      update_result = await _update_pending(
        client_order_id,
        status=terminal_status,
        reason=reason,
        source_sequence=int(
          error.get("source_sequence")
          or payload.get("source_sequence")
          or payload.get("sequence")
          or 0
        ),
      )
    else:
      update_result = await _update_pending_by_broker(
        broker_order_id,
        status=terminal_status,
        reason=reason,
        source_sequence=int(
          error.get("source_sequence")
          or payload.get("source_sequence")
          or payload.get("sequence")
          or 0
        ),
      )
    if update_result.accepted:
      cumulative_filled_volume = _reported_cumulative_fill(error)
      await AutoExitPlanService().apply_order_event_for_report(
        client_order_id=client_order_id,
        broker_order_id=broker_order_id,
        status=str(update_result.canonical_status or terminal_status),
        source_sequence=int(
          error.get("source_sequence")
          or payload.get("source_sequence")
          or payload.get("sequence")
          or 0
        ),
        cumulative_filled_volume=cumulative_filled_volume,
        cumulative_fill_state=_reported_cumulative_fill_state(error),
      )
  for error in payload.get("cancel_errors") or []:
    if skip_stale_full_item(error, unknown_account_is_stale=True):
      continue
    reason = str(error.get("error_msg") or error.get("reason") or "")
    client_order_id = str(error.get("client_order_id") or "")
    if client_order_id:
      await _update_pending(
        client_order_id,
        status="CANCEL_REJECTED",
        reason=reason,
      )
    else:
      await _update_pending_by_broker(
        error.get("order_id") or error.get("broker_order_id"),
        status="CANCEL_REJECTED",
        reason=reason,
      )

  full_snapshot_results: dict[str, dict[str, Any]] = {}
  if not authoritative and not full_snapshot_attempt:
    default_account_id = str(payload.get("account_id") or "")
    deltas = payload.get("position_deltas")
    if deltas is None:
      deltas = payload.get("positions") or []
    for position in deltas:
      value = dict(position)
      account_id = str(value.get("account_id") or default_account_id)
      if not account_id:
        raise ValueError("持仓增量缺少 account_id")
      instrument_code = str(
        value.get("stock_code") or value.get("instrument_code") or ""
      ).strip().upper()
      if not instrument_code:
        raise ValueError("持仓增量缺少 instrument_code")
      await _run_account_snapshot_mutation(
        account_id,
        lambda value=value, account_id=account_id: (
          position_service.apply_position_delta(
            value,
            account_id,
          )
        ),
        reason="BROKER_POSITION_DELTA_APPLIED",
        affected_instrument_codes=(instrument_code,),
      )

  if authoritative:
    ready_accounts: list[str] = []
    blocked_accounts: list[str] = []
    reconciliation_accounts: dict[str, dict[str, Any]] = {}
    account_ids = {
      str(item.get("account_id") or "")
      for item in payload.get("accounts") or []
      if str(item.get("account_id") or "")
    }
    account_ids.update(
      str(value)
      for value in (payload.get("positions_by_account") or {}).keys()
      if str(value)
    )
    for account_id in sorted(account_ids):
      if account_id in stale_full_accounts:
        # A duplicate full report must not re-project an older snapshot over
        # the current rollout or heartbeat state.
        full_snapshot_results[account_id] = {"reason": "STALE_SEQUENCE"}
        continue
      async with t_trade_account_coordination_lock(account_id):
        (
          result,
          is_blocked,
          summary,
        ) = await _reconcile_authoritative_full_account_locked(
          account_id,
          payload,
          snapshot_id=snapshot_id,
          snapshot_hash=snapshot_hash,
          reported_at=reported_at,
          sequence=int(full_snapshot_sequence or 0),
          positions=full_snapshot_groups.get(account_id, []),
          position_service=position_service,
        )
        full_snapshot_results[account_id] = result
        if result.get("reason") == "STALE_SEQUENCE":
          stale_full_accounts.add(account_id)
          continue
        if is_blocked:
          blocked_accounts.append(account_id)
        else:
          ready_accounts.append(account_id)
        reconciliation_accounts[account_id] = summary

    if stale_full_accounts and stale_full_accounts == all_authoritative_accounts:
      return

    async with AsyncSessionLocal() as db:
      heartbeat = await db.get(
        RuntimeComponentHeartbeat,
        f"qmt-agent:{device_id}",
      )
      if heartbeat is not None and report_belongs_to_current_session(
        payload,
        heartbeat,
        now=utcnow(),
      ):
        details = dict(heartbeat.details or {})
        account_details = dict(details.get("accountReconciliation") or {})
        account_details.update(reconciliation_accounts)
        details.update(
          {
            "readyAccounts": ready_accounts,
            "blockedAccounts": blocked_accounts,
            "accountReconciliation": account_details,
          }
        )
        if not stale_full_accounts:
          details.update(
            {
              "snapshotId": snapshot_id,
              "snapshotHash": snapshot_hash,
              "snapshotAt": reported_at.isoformat(),
            }
          )
        observed_at = utcnow()
        heartbeat.details = details
        if not stale_full_accounts and _snapshot_can_promote_heartbeat(
          heartbeat.status
        ):
          next_status = "READY" if not blocked_accounts else "RECONCILE_REQUIRED"
          heartbeat.status = next_status
          if next_status == "READY":
            device = await db.get(AgentDevice, device_id)
            if device is not None and device.revoked_at is None:
              revoked_ids = await converge_ready_agent(
                db,
                device=device,
                observed_at=observed_at,
              )
              if revoked_ids:
                details = {
                  **details,
                  "completedHandoverDeviceIds": revoked_ids,
                  "completedHandoverAt": observed_at.isoformat(),
                }
                heartbeat.details = details
        await db.commit()


async def _reconcile_authoritative_full_account_locked(
  account_id: str,
  payload: dict[str, Any],
  *,
  snapshot_id: str,
  snapshot_hash: str,
  reported_at: datetime,
  sequence: int,
  positions: list[Any],
  position_service: PositionService,
) -> tuple[dict[str, Any], bool, dict[str, Any]]:
  """Prepare, project, and finalize one full account under its lock."""

  prepare = getattr(position_service, "prepare_full_snapshot", None)
  if not callable(prepare):
    raise RetryableReportError("完整快照缺少持久化 prepare 边界")
  result = await prepare(
    account_id=account_id,
    positions=list(positions),
    sequence=sequence,
    reported_at=reported_at,
    source="QMT_AGENT",
  )
  result_dict = dict(result) if isinstance(result, dict) else {}
  if result_dict.get("reason") == "STALE_SEQUENCE":
    # A concurrent writer should already be impossible under the shared
    # account lock.  Keep this defensive branch so a stale duplicate can never
    # rewrite rollout snapshot metadata or trigger a downgrade.
    return result_dict, False, {}

  async with AsyncSessionLocal() as db:
    existing_rollout = await db.get(AccountExecutionControl, account_id)
    initial_control_version = (
      int(existing_rollout.state_version or 0)
      if existing_rollout is not None
      else None
    )
    controlled_window_active = bool(
      existing_rollout and existing_rollout.controlled_window_active
    )
    acknowledged_external_order_ids = (
      {
        str(value)
        for value in list(existing_rollout.controlled_window_external_order_ids or [])
      }
      if existing_rollout
      else set()
    )
    acknowledged_external_trade_ids = (
      {
        str(value)
        for value in list(existing_rollout.controlled_window_external_trade_ids or [])
      }
      if existing_rollout
      else set()
    )
    allow_external_activity = bool(
      existing_rollout is None
      or (
        str(existing_rollout.authorization_state).upper() in {"DISABLED", "PAUSED"}
        and not controlled_window_active
      )
    )
  reconciliation = await _snapshot_discrepancies(
    account_id,
    payload,
    allow_external_activity=allow_external_activity,
    acknowledged_external_order_ids=acknowledged_external_order_ids,
    acknowledged_external_trade_ids=acknowledged_external_trade_ids,
  )
  discrepancies = list(reconciliation["blocking_discrepancies"])
  async with AsyncSessionLocal() as db:
    rollout = await db.get(
      AccountExecutionControl,
      account_id,
      with_for_update=True,
      populate_existing=True,
    )
    locked_control_version = (
      int(rollout.state_version or 0) if rollout is not None else None
    )
    control_changed_during_snapshot = (
      locked_control_version != initial_control_version
    )
    (
      quarantine_phase,
      quarantine_source_sequence,
      quarantined_at,
      repair_snapshot_id,
      repair_snapshot_hash,
    ) = _broker_release_quarantine_boundary(
      rollout.paused_reason if rollout is not None else None
    )
    normalized_reported_at = to_naive_utc(reported_at)
    snapshot_not_newer_than_quarantine = bool(
      quarantine_phase == "REPAIR_AWAITING_FRESH_SNAPSHOT"
      and (
        snapshot_id == repair_snapshot_id
        or snapshot_hash == repair_snapshot_hash
        or (
          max(0, int(sequence or 0)) <= quarantine_source_sequence
          if quarantine_source_sequence > 0
          else quarantined_at is None
          or normalized_reported_at <= quarantined_at
        )
      )
    )
    if control_changed_during_snapshot:
      # The discrepancy scan was computed against an older authorization
      # generation.  Preserve the concurrent state verbatim and force the next
      # authoritative snapshot to recompute instead of clearing a quarantine.
      discrepancies.append(
        {
          "kind": "ACCOUNT_CONTROL_CHANGED_DURING_SNAPSHOT",
          "business_id": account_id,
          "expected_state_version": initial_control_version,
          "actual_state_version": locked_control_version,
        }
      )
    elif quarantine_phase == "UNREPAIRED_BROKER":
      # A broker-release quarantine is sticky.  New snapshots update the
      # exact evidence offered to the explicit repair action, but no clean
      # snapshot can bypass that action or clear the original pause boundary.
      discrepancies.append(
        {
          "kind": "QUARANTINED_ORDER_REPAIR_REQUIRED",
          "business_id": account_id,
        }
      )
      if rollout is not None:
        rollout.last_snapshot_id = snapshot_id or None
        rollout.last_snapshot_hash = snapshot_hash or None
        rollout.last_snapshot_at = to_naive_utc(reported_at)
        rollout.reconcile_status = "RECONCILE_REQUIRED"
        if str(rollout.authorization_state or "").upper() != "KILLED":
          rollout.authorization_state = "PAUSED"
        rollout.state_version = int(rollout.state_version or 0) + 1
    elif snapshot_not_newer_than_quarantine:
      # An authoritative snapshot may be delivered after the quarantine while
      # still describing broker state from before (or from the same report).
      # Preserve the quarantine verbatim until a strictly newer snapshot is
      # recomputed; do not rewrite its timestamp-bearing pause reason.
      discrepancies.append(
        {
          "kind": "SNAPSHOT_NOT_NEWER_THAN_QUARANTINE",
          "business_id": account_id,
          "snapshot_reported_at": normalized_reported_at.isoformat(),
          "snapshot_source_sequence": max(0, int(sequence or 0)),
          "quarantine_source_sequence": quarantine_source_sequence,
          "quarantined_at": (
            quarantined_at.isoformat() if quarantined_at is not None else None
          ),
        }
      )
    else:
      if rollout is None:
        rollout = AccountExecutionControl(account_id=account_id)
        db.add(rollout)
      rollout.last_snapshot_id = snapshot_id or None
      rollout.last_snapshot_hash = snapshot_hash or None
      rollout.last_snapshot_at = to_naive_utc(reported_at)
      rollout.reconcile_status = "READY" if not discrepancies else "RECONCILE_REQUIRED"
      if discrepancies:
        window_was_active = bool(rollout.controlled_window_active)
        previous_state = str(rollout.authorization_state)
        if rollout.authorization_state != "KILLED":
          rollout.authorization_state = "PAUSED"
        rollout.state_version = int(rollout.state_version or 0) + 1
        rollout.paused_reason = json.dumps(
          discrepancies[:20],
          ensure_ascii=False,
          default=str,
        )[:2000]
        if window_was_active:
          rollout.controlled_window_active = False
          rollout.controlled_window_snapshot_id = None
          rollout.controlled_window_snapshot_hash = None
          rollout.controlled_window_started_at = None
          rollout.controlled_window_started_by_user_id = None
          rollout.controlled_window_external_order_ids = []
          rollout.controlled_window_external_trade_ids = []
          db.add(
            AccountExecutionControlEvent(
              event_id=str(uuid.uuid4()),
              account_id=account_id,
              event_type="CONTROLLED_WINDOW_INVALIDATED",
              previous_state=previous_state,
              next_state=str(rollout.authorization_state),
              snapshot_id=snapshot_id or None,
              details={"discrepancies": discrepancies[:20]},
              created_at=utcnow(),
            )
          )
      else:
        if bool(rollout.controlled_window_active):
          rollout.controlled_window_snapshot_id = snapshot_id or None
          rollout.controlled_window_snapshot_hash = snapshot_hash or None
        if str(
          rollout.authorization_state
        ).upper() == "PAUSED" and _was_automatic_reconciliation_pause(
          rollout.paused_reason
        ):
          # A recovered automatic pause returns to the read-only preparation
          # stage.  It never silently resumes CANARY/LIVE order authority.
          rollout.authorization_state = "DISABLED"
          rollout.state_version = int(rollout.state_version or 0) + 1
          rollout.paused_reason = None
    summary_status = (
      str(rollout.reconcile_status) if rollout is not None else "RECONCILE_REQUIRED"
    )
    summary_controlled_window_active = bool(
      rollout is not None and rollout.controlled_window_active
    )
    summary = {
      "snapshotId": snapshot_id,
      "snapshotAt": reported_at.isoformat(),
      "status": summary_status,
      "manualCoexistence": allow_external_activity,
      "externalOrderCount": len(reconciliation["external_orders"]),
      "externalTradeCount": len(reconciliation["external_trades"]),
      "newExternalOrderCount": sum(
        not bool(item.get("acknowledged")) for item in reconciliation["external_orders"]
      ),
      "newExternalTradeCount": sum(
        not bool(item.get("acknowledged")) for item in reconciliation["external_trades"]
      ),
      "workingExternalOrderCount": sum(
        str(item.get("status") or "") in {"PENDING", "SUBMITTED", "PARTIAL_FILLED"}
        for item in reconciliation["external_orders"]
      ),
      "controlledWindowActive": summary_controlled_window_active,
      "blockingDiscrepancyCount": len(discrepancies),
    }
    await db.commit()

  try:
    await _invalidate_t_trade_entry_authority_for_account(
      account_id,
      reason="BROKER_POSITION_SNAPSHOT_UPDATED",
    )
  except Exception as authority_error:
    # Keep the prepared broker marker incomplete and block rollout while the
    # same account lock is still held, before surfacing a retryable error.
    marker_error: Optional[Exception] = None
    try:
      await position_service.mark_snapshot_failure(
        account_id,
        "SNAPSHOT_AUTHORITY_INVALIDATION_FAILED",
      )
    except Exception as exc:
      marker_error = exc
    async with AsyncSessionLocal() as db:
      rollout = await db.get(
        AccountExecutionControl,
        account_id,
        with_for_update=True,
      )
      if rollout is None:
        rollout = AccountExecutionControl(account_id=account_id)
        db.add(rollout)
      rollout.reconcile_status = "RECONCILE_REQUIRED"
      if rollout.authorization_state != "KILLED":
        rollout.authorization_state = "PAUSED"
      rollout.state_version = int(rollout.state_version or 0) + 1
      rollout.paused_reason = json.dumps(
        [
          {
            "kind": "SNAPSHOT_AUTHORITY_INVALIDATION_FAILED",
            "reason": str(authority_error),
          }
        ],
        ensure_ascii=False,
        default=str,
      )[:2000]
      await db.commit()
    detail = str(authority_error)
    if marker_error is not None:
      detail += f"; marker={marker_error}"
    raise RetryableReportError(
      "完整快照 authority 失效边界未完成: " + detail
    ) from authority_error

  if not discrepancies:
    # Rebind exact T-exit authority while this prepared position generation is
    # still resumable.  If the process stops here, replaying the same full
    # report re-enters the prepared generation and retries the idempotent
    # derivation.  Finalizing first would leave a crash window where replay is
    # classified as a stale duplicate and never reaches the derivation hook.
    await _rederive_t_trade_exit_authorizations_after_position_update(
      account_id,
      instrument_codes=None,
    )
    finalize = getattr(position_service, "finalize_full_snapshot", None)
    if not callable(finalize):
      raise RetryableReportError("完整快照缺少持久化 finalize 边界")
    finalized = await finalize(
      account_id=account_id,
      sequence=sequence,
      reported_at=reported_at,
      source="QMT_AGENT",
    )
    if not isinstance(finalized, dict) or not finalized.get("applied", False):
      reason = finalized.get("reason") if isinstance(finalized, dict) else "UNKNOWN"
      if reason == "STALE_SEQUENCE" and isinstance(finalized, dict):
        # A direct/concurrent durable writer may have advanced or invalidated
        # this generation after prepare. Treat it as a stale duplicate so the
        # outer failure boundary cannot downgrade the newer current marker.
        return finalized, False, {}
      raise RetryableReportError(
        "完整快照 finalize 未完成: " + str(reason or "UNKNOWN")
      )
    result_dict = finalized

  return result_dict, bool(discrepancies), summary


async def _snapshot_discrepancies(
  account_id: str,
  payload: dict[str, Any],
  *,
  allow_external_activity: bool,
  acknowledged_external_order_ids: set[str] | None = None,
  acknowledged_external_trade_ids: set[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
  acknowledged_external_order_ids = acknowledged_external_order_ids or set()
  acknowledged_external_trade_ids = acknowledged_external_trade_ids or set()
  snapshot_orders = [
    dict(item)
    for item in payload.get("orders") or []
    if str(item.get("account_id") or "") == account_id
  ]
  snapshot_trades = [
    dict(item)
    for item in payload.get("trades") or []
    if str(item.get("account_id") or "") == account_id
  ]
  async with AsyncSessionLocal() as db:
    pending = (
      (
        await db.execute(
          select(PendingTradeOrder).where(
            PendingTradeOrder.account_id == account_id,
            PendingTradeOrder.environment == ExecutionEnvironment.LIVE.value,
          )
        )
      )
      .scalars()
      .all()
    )
    reconcile_required_place_commands = list(
      (
        await db.execute(
          select(TradeCommandOutbox).where(
            TradeCommandOutbox.account_id == account_id,
            TradeCommandOutbox.delivery_status == "RECONCILE_REQUIRED",
          )
        )
      )
      .scalars()
      .all()
    )
  by_client = {str(item.client_order_id): item for item in pending}
  by_broker = {
    str(item.broker_order_id): item for item in pending if item.broker_order_id
  }
  discrepancies: list[dict[str, str]] = []
  blocked_pending_ids: set[str] = set()
  external_orders: list[dict[str, Any]] = []
  external_trades: list[dict[str, Any]] = []
  seen_broker_ids: set[str] = set()
  for order in snapshot_orders:
    client_id = str(order.get("client_order_id") or "")
    broker_id = str(order.get("order_id") or order.get("broker_order_id") or "")
    matched_pending = by_client.get(client_id) or by_broker.get(broker_id)
    broker_status = _normalized_order_status(
      order.get("effective_order_status")
      or order.get("order_status", order.get("status"))
    )
    if broker_id:
      seen_broker_ids.add(broker_id)
    if matched_pending is None:
      observation = {
        "kind": "EXTERNAL_BROKER_ORDER",
        "business_id": broker_id or client_id or "unknown",
        "status": broker_status,
        "raw_status": _normalized_order_status(
          order.get("order_status", order.get("status"))
        ),
        "status_reason": str(order.get("effective_status_reason") or ""),
      }
      observation["acknowledged"] = (
        observation["business_id"] in acknowledged_external_order_ids
      )
      external_orders.append(observation)
      if not allow_external_activity and not observation["acknowledged"]:
        discrepancies.append(
          {
            "kind": "UNKNOWN_BROKER_ORDER",
            "business_id": observation["business_id"],
          }
        )
    elif (
      str(matched_pending.status or "").upper() in TERMINAL_ORDER_STATUSES
      and broker_status in {"PENDING", "SUBMITTED", "PARTIAL_FILLED"}
    ):
      discrepancies.append(
        {
          "kind": "TERMINAL_ORDER_STILL_WORKING",
          "business_id": str(matched_pending.client_order_id),
        }
      )
  for trade in snapshot_trades:
    client_id = str(trade.get("client_order_id") or "")
    broker_id = str(trade.get("order_id") or trade.get("broker_order_id") or "")
    if not by_client.get(client_id) and not by_broker.get(broker_id):
      observation = {
        "kind": "EXTERNAL_BROKER_TRADE",
        "business_id": str(
          trade.get("execution_id")
          or trade.get("traded_id")
          or trade.get("trade_id")
          or broker_id
          or "unknown"
        ),
        "status": "FILLED",
      }
      observation["acknowledged"] = (
        observation["business_id"] in acknowledged_external_trade_ids
      )
      external_trades.append(observation)
      if not allow_external_activity and not observation["acknowledged"]:
        discrepancies.append(
          {
            "kind": "UNKNOWN_BROKER_TRADE",
            "business_id": observation["business_id"],
          }
        )
  for item in pending:
    client_order_id = str(item.client_order_id)
    item_status = str(item.status or "").upper()
    item_metadata = dict(getattr(item, "request_metadata", None) or {})
    if item_metadata.get(QUARANTINE_REPAIR_REQUIRED_METADATA_KEY):
      discrepancies.append(
        {
          "kind": "QUARANTINED_ORDER_REPAIR_REQUIRED",
          "business_id": client_order_id,
        }
      )
      blocked_pending_ids.add(client_order_id)
      continue
    if item_metadata.get(QUARANTINE_RECONCILE_REQUIRED_METADATA_KEY):
      discrepancies.append(
        {
          "kind": "PENDING_ORDER_RECONCILE_REQUIRED",
          "business_id": client_order_id,
        }
      )
      blocked_pending_ids.add(client_order_id)
      continue
    if (
      item_status == "CANCEL_REQUESTED"
      or (
        item_metadata.get(QUARANTINE_CANCEL_REQUIRED_METADATA_KEY)
        and item_status not in TERMINAL_ORDER_STATUSES
      )
    ):
      discrepancies.append(
        {
          "kind": "CANCEL_REQUEST_PENDING",
          "business_id": client_order_id,
        }
      )
      blocked_pending_ids.add(client_order_id)
      continue
    if (
      item.broker_order_id
      and item_status in {"SUBMITTED", "PARTIAL_FILLED", "PENDING"}
      and str(item.broker_order_id) not in seen_broker_ids
    ):
      discrepancies.append(
        {
          "kind": "MISSING_WORKING_ORDER",
          "business_id": str(item.client_order_id),
        }
      )
  for command in reconcile_required_place_commands:
    raw_command_payload = getattr(command, "payload", None)
    if not isinstance(raw_command_payload, Mapping):
      continue
    command_payload = dict(raw_command_payload)
    client_order_id = str(command.client_order_id or "")
    command_pending = by_client.get(client_order_id)
    if (
      not client_order_id
      or client_order_id in blocked_pending_ids
      or str(command_payload.get("command_kind") or "").upper() != "PLACE_ORDER"
      or str(command_payload.get("execution_mode") or "").lower() != "live"
      or str(command_payload.get("account_id") or command.account_id or "")
      != account_id
      or (
        command_pending is not None
        and str(command_pending.status or "").upper() in TERMINAL_ORDER_STATUSES
      )
    ):
      continue
    discrepancies.append(
      {
        "kind": "PENDING_ORDER_RECONCILE_REQUIRED",
        "business_id": client_order_id,
      }
    )
    blocked_pending_ids.add(client_order_id)
  return {
    "blocking_discrepancies": discrepancies,
    "external_orders": external_orders,
    "external_trades": external_trades,
  }


async def _process(report: AgentReportInbox) -> None:
  _require_current_protocol(report.protocol_version)
  if report.message_type == "order_report":
    await _process_order_report(report.payload)
  elif report.message_type == "execution_report":
    await _process_execution_report(report.payload)
  elif report.message_type == "delta_report":
    await _process_delta_report(
      report.device_id,
      report.payload,
      protocol_version=str(report.protocol_version or ""),
    )
  else:
    raise ValueError(f"未知 Agent report 类型: {report.message_type}")


def _normalized_order_status(value: Any) -> str:
  if hasattr(value, "name"):
    value = value.name
  special_status = str(value or "").strip().upper()
  if special_status in _SPECIAL_RUNTIME_ORDER_STATUSES:
    return special_status
  try:
    return _ORDER_STATUS_NAMES[int(value)]
  except (TypeError, ValueError, KeyError):
    pass
  text = normalize_order_status(value)
  return text if text in OrderStatus.__members__ else "PENDING"


def _parse_report_time(value: Any) -> datetime:
  if isinstance(value, datetime):
    return value
  if isinstance(value, (int, float)):
    return datetime.fromtimestamp(float(value), tz=time_utils.now().tzinfo)
  if isinstance(value, str) and value:
    try:
      return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
      pass
  return time_utils.now()


def _strict_t_trade_report_time(value: Any) -> datetime | None:
  """Parse a broker lifecycle timestamp without inventing a receive time."""

  if isinstance(value, datetime):
    parsed = value
  elif isinstance(value, (int, float)) and not isinstance(value, bool):
    numeric = float(value)
    if not math.isfinite(numeric):
      return None
    if numeric > 10_000_000_000:
      numeric /= 1000.0
    try:
      parsed = datetime.fromtimestamp(numeric, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
      return None
  elif isinstance(value, str) and value.strip():
    text = value.strip()
    try:
      parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
      try:
        parsed = datetime.strptime(text, "%Y%m%d%H%M%S")
      except ValueError:
        return None
  else:
    return None
  normalized = to_naive_utc(parsed)
  if normalized > utcnow():
    return None
  return normalized


def _parse_authoritative_snapshot_time(value: Any) -> datetime:
  """Parse a full-snapshot timestamp without turning bad input into ``now``."""

  if value is None or value == "":
    return time_utils.now()
  if isinstance(value, datetime):
    return value
  if isinstance(value, (int, float)) and not isinstance(value, bool):
    numeric = float(value)
    if not math.isfinite(numeric):
      raise ValueError("完整账户快照 source_event_at 不是有限时间")
    try:
      return datetime.fromtimestamp(numeric, tz=time_utils.now().tzinfo)
    except (OverflowError, OSError, ValueError) as exc:
      raise ValueError("完整账户快照 source_event_at 不可解析") from exc
  if isinstance(value, str) and value.strip():
    try:
      return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
      raise ValueError("完整账户快照 source_event_at 不可解析") from exc
  raise ValueError("完整账户快照 source_event_at 类型无效")


def _safe_snapshot_failure_time(value: Any) -> datetime:
  """Return a local failure timestamp even when the report timestamp is bad."""

  try:
    return _parse_authoritative_snapshot_time(value)
  except (TypeError, ValueError, OverflowError, OSError):
    return time_utils.now()


def _parse_authoritative_snapshot_sequence(payload: dict[str, Any]) -> int:
  """Require an explicit positive integer generation for protocol-1.2 full data."""

  if "source_sequence" in payload:
    raw_value = payload.get("source_sequence")
  elif "sequence" in payload:
    raw_value = payload.get("sequence")
  else:
    raise ValueError("完整账户快照缺少正整数 sequence")
  if raw_value is None or isinstance(raw_value, bool):
    raise ValueError("完整账户快照 sequence 必须是正整数")
  if isinstance(raw_value, float) and not raw_value.is_integer():
    raise ValueError("完整账户快照 sequence 必须是正整数")
  if isinstance(raw_value, Decimal) and raw_value != raw_value.to_integral_value():
    raise ValueError("完整账户快照 sequence 必须是正整数")
  try:
    sequence = int(raw_value)
  except (TypeError, ValueError, OverflowError) as exc:
    raise ValueError("完整账户快照 sequence 必须是正整数") from exc
  if sequence <= 0:
    raise ValueError("完整账户快照 sequence 必须是正整数")
  return sequence


_FILL_TERMINAL_ORDER_STATUSES = {"FILLED", "REJECTED", "CANCELLED", "EXPIRED"}


def _reported_cumulative_fill(report: dict[str, Any]) -> Optional[int]:
  """Read every supplied cumulative-fill field without truthy short-circuiting."""

  values: list[int] = []
  for key in ("traded_volume", "filled_volume"):
    if key not in report:
      continue
    raw_value = report.get(key)
    if raw_value is None or isinstance(raw_value, bool):
      return None
    if isinstance(raw_value, float) and (
      not math.isfinite(raw_value) or not raw_value.is_integer()
    ):
      return None
    if isinstance(raw_value, Decimal) and (
      not raw_value.is_finite() or raw_value != raw_value.to_integral_value()
    ):
      return None
    try:
      value = int(raw_value)
    except (TypeError, ValueError, OverflowError):
      return None
    if value < 0:
      return None
    values.append(value)
  return max(values) if values else None


def _reported_cumulative_fill_state(report: Mapping[str, Any]) -> str:
  if not any(key in report for key in ("traded_volume", "filled_volume")):
    return "MISSING"
  return "VALUE" if _reported_cumulative_fill(dict(report)) is not None else "INVALID"


async def _terminal_order_fill_projection(
  db,
  correlation: OrderCorrelation,
  intent: TradeIntentRecord,
  *,
  current_order: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
  """Return the terminal order target and execution-report progress for one intent."""
  pending = await db.get(PendingTradeOrder, correlation.client_order_id)
  if current_order and current_order.get("t_order_lifecycle_finalized"):
    return {
      "status": _normalized_order_status(current_order.get("effective_order_status")),
      "expected": int(current_order.get("traded_volume") or 0),
      "received": max(0, int(intent.executed_volume or 0)),
      "role": str(correlation.t_trade_role or "").upper(),
    }
  terminal_reports: list[tuple[dict[str, Any], str]] = []
  if current_order is None:
    candidates = (
      await db.execute(
        select(StrategyRuntimeEvent)
        .where(
          StrategyRuntimeEvent.client_order_id == correlation.client_order_id,
          StrategyRuntimeEvent.event_type == "ORDER",
        )
        .order_by(
          StrategyRuntimeEvent.created_at.desc(),
          StrategyRuntimeEvent.event_id.desc(),
        )
        .limit(20)
      )
    ).scalars()
    for candidate in candidates:
      candidate_report = dict(dict(candidate.payload or {}).get("report") or {})
      if candidate_report.get("t_order_lifecycle_finalized"):
        continue
      candidate_status = _normalized_order_status(
        candidate_report.get("effective_order_status")
        or candidate_report.get("status")
        or candidate_report.get("order_status")
      )
      if candidate_status in _FILL_TERMINAL_ORDER_STATUSES:
        terminal_reports.append((candidate_report, candidate_status))
    if not terminal_reports and pending is not None:
      pending_status = _normalized_order_status(pending.status)
      if pending_status in _FILL_TERMINAL_ORDER_STATUSES:
        terminal_reports.append(({}, pending_status))
  else:
    report = dict(current_order)
    status = _normalized_order_status(
      report.get("effective_order_status")
      or report.get("status")
      or report.get("order_status")
    )
    if status in _FILL_TERMINAL_ORDER_STATUSES:
      terminal_reports.append((report, status))

  if not terminal_reports:
    return None
  received = max(0, int(intent.executed_volume or 0))
  if getattr(pending, "t_order_original_created_at", None) is not None:
    executions = (await db.execute(
      select(StrategyRuntimeEvent).where(
        StrategyRuntimeEvent.client_order_id == correlation.client_order_id,
        StrategyRuntimeEvent.event_type == "TRADE",
      )
    )).scalars()
    received = sum(
      max(0, int(dict(dict(event.payload or {}).get("report") or {}).get("traded_volume")
                 or dict(dict(event.payload or {}).get("report") or {}).get("volume") or 0))
      for event in executions
    )
  role = str(correlation.t_trade_role or "").strip().upper()
  selected: Optional[dict[str, Any]] = None
  for report, status in terminal_reports:
    requested = max(
      0,
      int(
        (pending.volume if pending is not None else None)
        or intent.target_volume
        or report.get("order_volume")
        or report.get("volume")
        or 0
      ),
    )
    reported = _reported_cumulative_fill(report)
    expected = (
      # Missing and malformed cumulative fill are both non-authoritative. A
      # cancellation-class terminal without an explicit zero must remain
      # fail-closed just like FILLED; only an actual numeric zero proves that
      # no TRADE report needs to catch up.
      max(1, requested)
      if reported is None
      else (
        int(reported)
        if int(reported) > 0
        else (max(1, requested) if status == "FILLED" else 0)
      )
    )
    projection = {
      "status": status,
      "expected": expected,
      "received": received,
      "role": role,
      "reason": str(
        report.get("effective_status_reason") or report.get("status_msg") or ""
      ),
    }
    if selected is None or expected > int(selected["expected"]):
      selected = projection
  return selected


def _fill_projection_note(projection: dict[str, Any]) -> str:
  role = str(projection.get("role") or "ORDER")
  return (
    f"AWAITING_{role}_EXECUTION_REPORT: "
    f"terminal={projection.get('status')}, "
    f"expected={int(projection.get('expected') or 0)}, "
    f"received={int(projection.get('received') or 0)}"
  )


def _report_items(report: AgentReportInbox) -> list[tuple[str, dict[str, Any]]]:
  payload = dict(report.payload or {})
  if report.message_type == "order_report":
    return [("ORDER", _body(payload, "order"))]
  if report.message_type == "execution_report":
    return [("TRADE", _body(payload, "execution"))]
  if report.message_type == "delta_report":
    rejected_orders = []
    for error in payload.get("order_errors") or []:
      terminal_status = (
        "EXPIRED"
        if str(error.get("reason") or "").strip().lower() == "command_expired"
        else "REJECTED"
      )
      rejected_orders.append(
        {
          **dict(error),
          "order_status": terminal_status,
          "status": terminal_status,
          "status_msg": error.get("error_msg") or error.get("reason") or "",
          "broker_order_id": error.get("broker_order_id") or error.get("order_id"),
        }
      )
    return [
      *(("ORDER", dict(item)) for item in payload.get("orders") or []),
      *(("TRADE", dict(item)) for item in payload.get("trades") or []),
      *(("ORDER", item) for item in rejected_orders),
    ]
  return []


def _authoritative_snapshot_identity(
  report: AgentReportInbox,
) -> Optional[tuple[str, str]]:
  """Return a verified protocol 1.2 full-snapshot identity.

  ``_process_delta_report`` performs the same validation before updating the
  account rollout.  Repeating it here prevents a direct or replayed staging
  call from manufacturing a zero-fill proof from an unverified payload.
  """

  payload = dict(report.payload or {})
  if (
    report.message_type != "delta_report"
    or str(report.protocol_version or "") != PROTOCOL_VERSION
    or payload.get("is_complete") is not True
  ):
    return None
  try:
    if _complete_snapshot_account_ids(payload) is None:
      return None
    _parse_authoritative_snapshot_sequence(payload)
    _parse_authoritative_snapshot_time(payload.get("source_event_at"))
  except (
    RetryableReportError,
    TypeError,
    ValueError,
    OverflowError,
    OSError,
  ):
    return None
  snapshot_id = str(payload.get("snapshot_id") or "").strip()
  snapshot_hash = str(payload.get("snapshot_hash") or "").strip().lower()
  if not snapshot_id or len(snapshot_hash) != 64:
    return None
  hash_input = _snapshot_hash_input(payload)
  expected_hash = sha256(
    json.dumps(
      hash_input,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode("utf-8")
  ).hexdigest()
  if expected_hash != snapshot_hash:
    return None
  return snapshot_id, snapshot_hash


def _snapshot_fully_covers_account(
  payload: dict[str, Any],
  account_id: str,
) -> bool:
  """Require explicit completeness for every authoritative account section."""

  account_ids = _complete_snapshot_account_ids(payload)
  return bool(account_ids is not None and account_id in account_ids)


def _snapshot_has_order_execution_detail(
  payload: dict[str, Any],
  *,
  account_id: str,
  instrument_code: str,
  client_order_id: str,
  broker_order_id: str,
) -> bool:
  """Conservatively detect a current-snapshot execution for one order."""

  for raw_trade in payload.get("trades") or []:
    if not isinstance(raw_trade, dict):
      continue
    trade = dict(raw_trade)
    if str(trade.get("account_id") or "") != account_id:
      continue
    trade_client_id = str(trade.get("client_order_id") or "")
    trade_broker_id = str(trade.get("order_id") or trade.get("broker_order_id") or "")
    if trade_client_id == client_order_id or trade_broker_id == broker_order_id:
      return True
    trade_instrument = str(
      trade.get("stock_code") or trade.get("instrument_code") or ""
    ).upper()
    if (
      trade_instrument == instrument_code
      and not trade_client_id
      and not trade_broker_id
    ):
      # An execution for the same account/instrument without an order identity
      # cannot safely be distinguished from this managed entry.
      return True
  return False


async def _full_snapshot_zero_fill_items(
  db,
  report: AgentReportInbox,
) -> list[tuple[str, dict[str, Any]]]:
  """Prove exact managed BUY or EXIT_PLAN SELL orders had no execution.

  A terminal order report alone is deliberately insufficient: QMT execution
  reports may arrive after it.  The proof is emitted only after a verified
  protocol 1.2 full snapshot has become the account's READY reconciliation
  checkpoint and both snapshot and durable execution stores are empty for the
  order.  The resulting synthetic ORDER event is replayable and auditable.
  """

  identity = _authoritative_snapshot_identity(report)
  if identity is None:
    return []
  snapshot_id, snapshot_hash = identity
  payload = dict(report.payload or {})
  try:
    snapshot_sequence = _parse_authoritative_snapshot_sequence(payload)
  except (TypeError, ValueError, OverflowError):
    return []

  results: list[tuple[str, dict[str, Any]]] = []
  seen_orders: set[tuple[str, str]] = set()
  checkpoint_by_account: dict[str, bool] = {}

  async def has_current_ready_checkpoint(account_id: str) -> bool:
    normalized_account = str(account_id or "").strip()
    if not normalized_account:
      return False
    if normalized_account not in checkpoint_by_account:
      rollout = await db.get(AccountExecutionControl, normalized_account)
      checkpoint_by_account[normalized_account] = bool(
        rollout is not None
        and str(rollout.last_snapshot_id or "") == snapshot_id
        and str(rollout.last_snapshot_hash or "") == snapshot_hash
        and str(rollout.reconcile_status or "").upper() == "READY"
      )
    return checkpoint_by_account[normalized_account]

  for raw_order in payload.get("orders") or []:
    if not isinstance(raw_order, dict):
      continue
    order = dict(raw_order)
    terminal_status = _normalized_order_status(
      order.get("effective_order_status")
      or order.get("status")
      or order.get("order_status")
    )
    if terminal_status not in _ZERO_FILL_RECONCILABLE_ORDER_STATUSES:
      continue
    reported_fill = _reported_cumulative_fill(order)
    if reported_fill is None:
      continue
    if reported_fill != 0:
      continue

    client_order_id = str(order.get("client_order_id") or "").strip()
    broker_order_id = str(
      order.get("order_id") or order.get("broker_order_id") or ""
    ).strip()
    account_id = str(order.get("account_id") or "").strip()
    instrument_code = (
      str(order.get("stock_code") or order.get("instrument_code") or "").strip().upper()
    )
    if (
      not client_order_id
      or not broker_order_id
      or not account_id
      or not instrument_code
      or (client_order_id, broker_order_id) in seen_orders
      or not _snapshot_fully_covers_account(payload, account_id)
      or not await has_current_ready_checkpoint(account_id)
    ):
      continue
    seen_orders.add((client_order_id, broker_order_id))

    rollout = await db.get(
      AccountExecutionControl,
      account_id,
      with_for_update=True,
      populate_existing=True,
    )
    if (
      rollout is None
      or str(rollout.reconcile_status or "").upper() != "READY"
      or str(rollout.last_snapshot_id or "") != snapshot_id
      or str(rollout.last_snapshot_hash or "").lower() != snapshot_hash
      or rollout.last_snapshot_at is None
    ):
      continue

    correlation = await _correlation_for_report(
      db,
      client_order_id=client_order_id,
      broker_order_id=broker_order_id,
      allow_broker_fallback=True,
    )
    if correlation is None:
      continue
    correlation = await db.get(
      OrderCorrelation,
      correlation.id,
      with_for_update=True,
      populate_existing=True,
    )
    pending = await db.get(
      PendingTradeOrder,
      client_order_id,
      with_for_update=True,
      populate_existing=True,
    )
    if pending is None:
      continue
    owner_triple = _owner_chain_triple(pending, correlation)
    if owner_triple is None:
      continue
    owner_type, owner_id, _environment = owner_triple
    pending_metadata = dict(pending.request_metadata or {})
    run_id = (
      owner_id if owner_type == ExecutionOwnerType.STRATEGY_RUN.value else ""
    )
    if (
      str(pending.account_id or "") != account_id
      or str(pending.instrument_code or "").upper() != instrument_code
      or str(pending.broker_order_id or "") != broker_order_id
      or _normalized_order_status(pending.status) != terminal_status
      or snapshot_sequence < max(0, int(pending.last_source_sequence or 0))
    ):
      continue
    if (
      pending.last_source_event_at is not None
      and to_naive_utc(pending.last_source_event_at) > rollout.last_snapshot_at
    ):
      continue

    intent_id = str(pending.intent_id or "").strip()
    if not intent_id:
      continue
    if (
      str(correlation.client_order_id or "") != client_order_id
      or str(correlation.broker_order_id or "") != broker_order_id
      or str(correlation.account_id or "") != account_id
      or str(correlation.intent_id or "") != intent_id
    ):
      continue
    intent = await db.get(
      TradeIntentRecord,
      intent_id,
      with_for_update=True,
      populate_existing=True,
    )
    intent_metadata = dict(intent.intent_metadata or {}) if intent is not None else {}
    try:
      executed_volume = int(intent.executed_volume or 0) if intent else -1
      executed_price = Decimal(str(intent.executed_price or 0)) if intent else None
    except (TypeError, ValueError, ArithmeticError):
      continue
    if (
      intent is None
      or str(intent.instrument_code or "").upper() != instrument_code
      or str(intent.account_id or "") != account_id
      or executed_volume != 0
      or executed_price is None
      or not executed_price.is_finite()
      or executed_price > 0
      or intent.executed_time is not None
      or _owner_chain_triple(pending, correlation, intent) != owner_triple
    ):
      continue

    # ``entry_plan_id`` is business evidence for the StrategyRun BUY path;
    # the owner itself was already proven by the durable triple above.
    entry_plan_id = str(intent_metadata.get("entry_plan_id") or "").strip()
    pending_entry_plan_id = str(pending_metadata.get("entry_plan_id") or "").strip()
    if pending_entry_plan_id and pending_entry_plan_id != entry_plan_id:
      continue
    managed_entry_owner = bool(
      owner_type == ExecutionOwnerType.STRATEGY_RUN.value
      and correlation is not None
      and entry_plan_id
      and run_id
      and str(intent.strategy_run_id or "") == run_id
      and str(pending.side or "").upper() == "BUY"
      and str(intent.direction or "").upper() == "BUY"
      and str(intent_metadata.get("entry_plan_id") or "") == entry_plan_id
    )

    exit_plan_record = None
    exit_owner_kind = ""
    # EXIT_PLAN ownership is selected only from the durable pending/
    # correlation triple.  Metadata exit_plan_id can reject a contradictory
    # projection, but it cannot supply the plan id or turn a StrategyRun into
    # an EXIT_PLAN owner.
    durable_exit_binding = _durable_exit_plan_sell_binding(pending, correlation)
    if durable_exit_binding is None:
      continue
    pending_exit_plan_id = (
      owner_id if owner_type == ExecutionOwnerType.EXIT_PLAN.value else ""
    )
    intent_exit_plan_id = str(intent_metadata.get("exit_plan_id") or "").strip()
    if (
      durable_exit_binding
      and pending_exit_plan_id
      and str(intent.direction or "").upper() == "SELL"
      and _durable_owner_triple(intent) == owner_triple
      and (
        not intent_exit_plan_id or intent_exit_plan_id == pending_exit_plan_id
      )
    ):
      exit_plan_record = await db.get(
        AutoExitPlanRecord,
        pending_exit_plan_id,
        with_for_update=True,
        populate_existing=True,
      )
      if exit_plan_record is not None:
        plan_state = dict(exit_plan_record.plan_state or {})
        template = dict(plan_state.get("template") or {})
        state_pending_intent_id = str(
          plan_state.get("pending_intent_id") or ""
        ).strip()
        state_pending_order_id = str(
          plan_state.get("pending_order_id") or ""
        ).strip()
        record_pending_order_id = str(
          exit_plan_record.pending_client_order_id or ""
        ).strip()
        plan_binding_exact = bool(
          str(exit_plan_record.account_id or "") == account_id
          and str(exit_plan_record.instrument_code or "").upper()
          == instrument_code
          and str(exit_plan_record.environment or "").upper()
          == owner_triple[2]
          and str(template.get("plan_id") or "") == pending_exit_plan_id
          and str(template.get("account_id") or "") == account_id
          and str(template.get("instrument_code") or "").upper()
          == instrument_code
          and state_pending_intent_id == intent_id
          and state_pending_order_id in {"", client_order_id}
          and record_pending_order_id in {"", client_order_id}
        )
        if plan_binding_exact:
          exit_owner_kind = "EXIT_PLAN"

    if not managed_entry_owner and not exit_owner_kind:
      continue
    if _snapshot_has_order_execution_detail(
      payload,
      account_id=account_id,
      instrument_code=instrument_code,
      client_order_id=client_order_id,
      broker_order_id=broker_order_id,
    ):
      continue

    durable_runtime_events = list(
      (
        await db.execute(
          select(StrategyRuntimeEvent)
          .where(
            StrategyRuntimeEvent.event_type.in_({"ORDER", "TRADE"}),
            or_(
              StrategyRuntimeEvent.client_order_id == client_order_id,
              StrategyRuntimeEvent.broker_order_id == broker_order_id,
            ),
          )
          .with_for_update()
        )
      )
      .scalars()
      .all()
    )
    execution_announced = False
    for runtime_event in durable_runtime_events:
      if runtime_event.event_type == "TRADE":
        execution_announced = True
        break
      historical_order = dict(dict(runtime_event.payload or {}).get("report") or {})
      historical_fill = _reported_cumulative_fill(historical_order)
      if historical_fill is None and any(
        key in historical_order for key in ("traded_volume", "filled_volume")
      ):
        execution_announced = True
        break
      if int(historical_fill or 0) > 0:
        execution_announced = True
        break
      if correlation is not None:
        historical_projection = await _terminal_order_fill_projection(
          db,
          correlation,
          intent,
          current_order=historical_order,
        )
        if historical_projection and int(historical_projection["expected"]) > 0:
          execution_announced = True
          break
    if execution_announced:
      continue
    try:
      numeric_broker_order_id = int(broker_order_id)
    except (TypeError, ValueError, OverflowError):
      continue
    durable_order = await db.get(Order, numeric_broker_order_id)
    if durable_order is not None and (
      str(durable_order.account_id or "") != account_id
      or str(durable_order.stock_code or "").upper() != instrument_code
      or int(durable_order.traded_volume or 0) != 0
    ):
      continue
    durable_trade = (
      await db.execute(
        select(Trade.id)
        .where(
          Trade.account_id == account_id,
          Trade.order_id == numeric_broker_order_id,
        )
        .limit(1)
      )
    ).scalar_one_or_none()
    if durable_trade is not None:
      continue

    audit = {
      "source": "QMT_PROTOCOL_1_2_FULL_SNAPSHOT",
      "snapshot_id": snapshot_id,
      "snapshot_hash": snapshot_hash,
      "snapshot_at": rollout.last_snapshot_at.isoformat(),
      "source_sequence": snapshot_sequence,
      "broker_terminal_status": terminal_status,
      "expected_filled_volume": 0,
      "received_execution_volume": 0,
      "reconciled_at": utcnow().isoformat(),
    }
    if exit_owner_kind:
      audit.update(
        {
          "exit_plan_id": pending_exit_plan_id,
          "intent_id": intent_id,
          "account_id": account_id,
          "instrument_code": instrument_code,
          "client_order_id": client_order_id,
          "broker_order_id": broker_order_id,
        }
      )
      if exit_owner_kind == "MONITOR":
        existing_audit = intent_metadata.get("qmt_zero_fill_reconciliation")
        if (
          isinstance(existing_audit, dict)
          and str(existing_audit.get("snapshot_id") or "") == snapshot_id
          and str(existing_audit.get("snapshot_hash") or "").lower()
          == snapshot_hash
        ):
          audit["reconciled_at"] = str(
            existing_audit.get("reconciled_at") or audit["reconciled_at"]
          )
        intent.status = "RECONCILED_ZERO_FILL"
        intent.notes = "QMT_FULL_SNAPSHOT_ZERO_FILL_RECONCILIATION"
        intent.intent_metadata = {
          **intent_metadata,
          "qmt_zero_fill_reconciliation": dict(audit),
        }
    results.append(
      (
        "ORDER",
        {
          **order,
          "effective_order_status": "RECONCILED_ZERO_FILL",
          "effective_status_reason": ("QMT_FULL_SNAPSHOT_ZERO_FILL_RECONCILIATION"),
          "traded_volume": 0,
          "filled_volume": 0,
          "zero_fill_reconciliation": audit,
        },
      )
    )
  return results


async def _correlation_for_report(
  db,
  *,
  client_order_id: str,
  broker_order_id: str,
  allow_broker_fallback: bool = False,
) -> Optional[OrderCorrelation]:
  normalized_client_order_id = str(client_order_id or "").strip()
  normalized_broker_order_id = str(broker_order_id or "").strip()
  if not normalized_client_order_id and not normalized_broker_order_id:
    return None

  # Client identity is the service-owned causal key.  A broker id is only a
  # fact-side fallback (for reports that omit client_order_id or an
  # authoritative reconciliation snapshot); it must never override a
  # supplied client identity.
  candidate = None
  if normalized_client_order_id:
    try:
      candidate = (
        await db.execute(
          select(OrderCorrelation).where(
            OrderCorrelation.client_order_id == normalized_client_order_id
          )
        )
      ).scalar_one_or_none()
    except MultipleResultsFound:
      # A corrupted client identity is not routable.  Do not choose an
      # arbitrary row from a duplicate durable mapping.
      return None
  if candidate is None and normalized_broker_order_id and (
    not normalized_client_order_id or allow_broker_fallback
  ):
    try:
      broker_candidates = (
        await db.execute(
          select(OrderCorrelation).where(
            OrderCorrelation.broker_order_id == normalized_broker_order_id
          )
        )
      ).scalars().all()
    except MultipleResultsFound:
      # Defensive for result adapters that still collapse duplicate scalar
      # rows through scalar_one_or_none().
      return None
    if len(broker_candidates) > 1:
      return None
    candidate = broker_candidates[0] if broker_candidates else None
  if candidate is None:
    return None
  raw_client_order_id = str(candidate.client_order_id or "")
  resolved_client_order_id = raw_client_order_id.strip()
  if not resolved_client_order_id:
    return None
  if raw_client_order_id != resolved_client_order_id:
    return None
  if normalized_client_order_id and resolved_client_order_id != normalized_client_order_id:
    return None
  if (
    normalized_broker_order_id
    and candidate.broker_order_id
    and str(candidate.broker_order_id) != normalized_broker_order_id
  ):
    return None
  await AccountExecutionQuarantineService(db).lock_client_order_for_lifecycle(
    client_order_id=resolved_client_order_id
  )
  # This establishes the shared account/outbox -> pending -> correlation
  # order before any expiry, intent, or plan projection helper can run.
  pending = await db.get(
    PendingTradeOrder,
    resolved_client_order_id,
    with_for_update=True,
    populate_existing=True,
  )
  correlation = await db.get(OrderCorrelation, candidate.id, with_for_update=True)
  intent = None
  if correlation is not None and correlation.intent_id:
    intent = await db.get(
      TradeIntentRecord,
      correlation.intent_id,
      with_for_update=True,
      populate_existing=True,
    )
  if (
    pending is None
    or correlation is None
    or str(correlation.client_order_id or "") != resolved_client_order_id
    or str(correlation.account_id or "") != str(pending.account_id or "")
    or str(correlation.intent_id or "") != str(pending.intent_id or "")
    or (
      intent is not None
      and str(intent.account_id or "") != str(pending.account_id or "")
    )
    or not _exact_intent_binding(pending, correlation, intent)
  ):
    return None
  return correlation


async def _command_expired_entry_zero_fill_reconciliation(
  db,
  correlation: OrderCorrelation,
  item: dict[str, Any],
) -> Optional[dict[str, Any]]:
  """Prove an Agent command-expiry error happened before Broker.execute()."""

  status = _normalized_order_status(
    item.get("effective_order_status") or item.get("status") or item.get("order_status")
  )
  reason = str(
    item.get("reason")
    or item.get("effective_status_reason")
    or item.get("status_msg")
    or ""
  ).strip()
  broker_order_id = str(
    item.get("order_id") or item.get("broker_order_id") or ""
  ).strip()
  if status != "EXPIRED" or reason.lower() != "command_expired" or broker_order_id:
    return None

  pending = await db.get(
    PendingTradeOrder,
    correlation.client_order_id,
    with_for_update=True,
  )
  intent = await db.get(
    TradeIntentRecord,
    correlation.intent_id,
    with_for_update=True,
  )
  if pending is None or intent is None:
    return None
  request_metadata = {
    **dict(pending.request_metadata or {}),
    **dict(correlation.request_metadata or {}),
  }
  intent_metadata = dict(intent.intent_metadata or {})
  plan_id = str(
    request_metadata.get("entry_plan_id") or intent_metadata.get("entry_plan_id") or ""
  ).strip()
  owner_triple = _owner_chain_triple(pending, correlation, intent)
  if owner_triple is None:
    return None
  owner_type, owner_id, _environment = owner_triple
  run_id = (
    owner_id if owner_type == ExecutionOwnerType.STRATEGY_RUN.value else ""
  )
  try:
    executed_volume = int(intent.executed_volume or 0)
    executed_price = Decimal(str(intent.executed_price or 0))
  except (TypeError, ValueError, ArithmeticError):
    return None
  if (
    not plan_id
    or owner_type != ExecutionOwnerType.STRATEGY_RUN.value
    or not run_id
    or str(pending.side or "").upper() != "BUY"
    or str(intent.direction or "").upper() != "BUY"
    or str(intent_metadata.get("entry_plan_id") or "") != plan_id
    or pending.broker_order_id
    or correlation.broker_order_id
    or str(pending.status or "").upper()
    not in {
      "QUEUED",
      "EXPIRED",
      "RECONCILE_REQUIRED",
      "RECONCILED_ZERO_FILL",
    }
    or executed_volume != 0
    or not executed_price.is_finite()
    or executed_price > 0
    or intent.executed_time is not None
  ):
    return None

  prior_events = list(
    (
      await db.execute(
        select(StrategyRuntimeEvent)
        .where(
          StrategyRuntimeEvent.client_order_id == correlation.client_order_id,
          StrategyRuntimeEvent.event_type.in_({"ORDER", "TRADE"}),
        )
        .with_for_update()
      )
    )
    .scalars()
    .all()
  )
  for prior_event in prior_events:
    if prior_event.event_type == "TRADE":
      return None
    prior_report = dict(dict(prior_event.payload or {}).get("report") or {})
    prior_fill = _reported_cumulative_fill(prior_report)
    if (
      prior_fill is None
      and any(key in prior_report for key in ("traded_volume", "filled_volume"))
    ) or int(prior_fill or 0) > 0:
      return None

  return {
    "source": "QMT_AGENT_COMMAND_EXPIRED_PRE_EXECUTION",
    "command_reason": reason,
    "command_message_id": str(
      dict(correlation.request_metadata or {}).get("command_message_id") or ""
    ),
    "expected_filled_volume": 0,
    "received_execution_volume": 0,
    "reconciled_at": utcnow().isoformat(),
  }


def _runtime_business_key(
  event_type: str,
  correlation: OrderCorrelation,
  item: dict[str, Any],
) -> str:
  owner_triple = _durable_owner_triple(correlation)
  if owner_triple is None:
    raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
  owner_type, owner_id, environment = owner_triple
  client_order_id = str(correlation.client_order_id or "")
  account_id = str(getattr(correlation, "account_id", "") or "")
  broker_order_id = str(item.get("order_id") or item.get("broker_order_id") or "")
  if event_type == "TRADE":
    execution_id = str(item.get("execution_id") or item.get("traded_id") or "")
    if not execution_id:
      execution_id = (
        f"{broker_order_id}:{item.get('traded_time')}:"
        f"{item.get('traded_price')}:{item.get('traded_volume')}"
      )
    canonical_components = (
      event_type,
      owner_type,
      owner_id,
      environment,
      account_id,
      client_order_id,
      broker_order_id,
      execution_id,
    )
    # Execution ids are broker-scoped facts, not globally unique across every
    # owner/account.  Hash the complete canonical tuple so long owner/client/
    # execution ids cannot be truncated into a business-key collision.
    canonical = json.dumps(
      [str(value) for value in canonical_components],
      ensure_ascii=False,
      separators=(",", ":"),
    )
    return f"trade:{sha256(canonical.encode('utf-8')).hexdigest()}"
  cumulative_fill = _reported_cumulative_fill(item)
  fill_field_present = any(key in item for key in ("traded_volume", "filled_volume"))
  fill_component = (
    ("INVALID" if fill_field_present else "MISSING")
    if cumulative_fill is None
    else str(int(cumulative_fill))
  )
  canonical_components = (
    event_type,
    owner_type,
    owner_id,
    environment,
    account_id,
    client_order_id,
    broker_order_id,
    _normalized_order_status(
      item.get("effective_order_status")
      or item.get("status")
      or item.get("order_status")
    ),
    fill_component,
  )
  canonical = json.dumps(
    [str(value) for value in canonical_components],
    ensure_ascii=False,
    separators=(",", ":"),
  )
  return f"order:{sha256(canonical.encode('utf-8')).hexdigest()}"


def _event_payload(
  correlation: OrderCorrelation,
  item: dict[str, Any],
  *,
  business_key: str,
) -> dict[str, Any]:
  owner_triple = _durable_owner_triple(correlation)
  if owner_triple is None:
    raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
  environment = owner_triple[2]
  # Owner identity is carried by StrategyRuntimeEvent's typed columns.  Do
  # not duplicate it into JSON metadata where a consumer could mistake a
  # stale/request-supplied value for routing authority.
  request_metadata = {
    key: value
    for key, value in dict(correlation.request_metadata or {}).items()
    if str(key).strip().lower() not in _RUNTIME_OWNER_METADATA_KEYS
  }
  metadata = {
    **request_metadata,
    "intent_id": getattr(correlation, "intent_id", None),
    "t_batch_id": correlation.batch_id or "",
    "bucket": correlation.bucket,
    "t_trade_role": str(correlation.t_trade_role or "").lower(),
    "risk_decision_id": correlation.risk_decision_id or "",
    "trace_id": correlation.trace_id,
    "substitution_plan": correlation.substitution_plan,
    "execution_mode": _wire_execution_mode(environment),
    "runtime_event_key": business_key,
  }
  if (
    str(getattr(correlation, "owner_type", "") or "").strip().upper()
    == ExecutionOwnerType.STRATEGY_RUN.value
  ):
    metadata["strategy_order_id"] = getattr(correlation, "strategy_order_id", None)
  zero_fill_reconciliation = item.get("zero_fill_reconciliation")
  if isinstance(zero_fill_reconciliation, dict):
    metadata["qmt_zero_fill_reconciliation"] = dict(zero_fill_reconciliation)
  zero_fill_invalidation = item.get("zero_fill_proof_invalidation")
  if isinstance(zero_fill_invalidation, dict):
    metadata["zero_fill_proof_invalidation"] = dict(zero_fill_invalidation)
  return {"report": item, "metadata": metadata}


async def _project_trade_intent_event(
  db,
  correlation: OrderCorrelation,
  *,
  event_type: str,
  item: dict[str, Any],
) -> Optional[dict[str, Any]]:
  """Project one uniquely staged broker event into durable intent audit truth."""
  # MANUAL_COMMAND correlations intentionally have no strategy intent/order
  # identity.  They still produce durable runtime events, but there is no
  # intent projection to mutate and no fabricated surrogate is permitted.
  if not correlation.intent_id:
    return None
  intent = await db.get(TradeIntentRecord, correlation.intent_id, with_for_update=True)
  if intent is None:
    return None
  lifecycle_pending = await db.get(PendingTradeOrder, correlation.client_order_id)
  lifecycle_open = t_order_lifecycle_pending(lifecycle_pending)
  previous_intent_status = str(intent.status or "")
  lifecycle_finished = bool(
    getattr(lifecycle_pending, "t_order_original_created_at", None) is not None
    and not lifecycle_open
  )
  if lifecycle_finished and event_type == "ORDER":
    return {"lifecycle_finalized_replay": True}
  intent_metadata = dict(intent.intent_metadata or {})
  if str(intent_metadata.get("execution_terminal_source") or "").upper() in {
    LOCAL_OUTBOX_EXPIRED_ZERO_FILL_SOURCE,
    LOCAL_AGENT_PRE_EXECUTION_ZERO_FILL_SOURCE,
  }:
    intent.intent_metadata = {
      key: value
      for key, value in intent_metadata.items()
      if key
      not in {
        "execution_terminal_source",
        "execution_terminal_reason",
        "execution_terminal_at",
        "command_lifecycle_status",
        "command_lifecycle_previous_status",
        "command_lifecycle_message_id",
      }
    }
  intent.order_id = correlation.strategy_order_id or intent.order_id
  if correlation.risk_decision_id:
    intent.risk_decision_id = correlation.risk_decision_id
  if event_type == "ORDER":
    order_status = _normalized_order_status(
      item.get("effective_order_status")
      or item.get("status")
      or item.get("order_status")
    )
    projection = await _terminal_order_fill_projection(
      db,
      correlation,
      intent,
      current_order=item,
    )
    historical_projection = await _terminal_order_fill_projection(
      db,
      correlation,
      intent,
    )
    if historical_projection and (
      projection is None
      or int(historical_projection["expected"]) > int(projection["expected"])
    ):
      projection = historical_projection
    if order_status == "RECONCILED_ZERO_FILL":
      reconciliation = item.get("zero_fill_reconciliation")
      if isinstance(reconciliation, dict):
        intent.intent_metadata = {
          **dict(intent.intent_metadata or {}),
          "qmt_zero_fill_reconciliation": dict(reconciliation),
        }
      intent.status = order_status
      intent.notes = str(
        item.get("effective_status_reason")
        or "QMT_FULL_SNAPSHOT_ZERO_FILL_RECONCILIATION"
      )
    elif projection and int(projection["expected"]) > int(projection["received"]):
      intent.status = "RECONCILE_REQUIRED"
      intent.notes = _fill_projection_note(projection)
    else:
      intent.status = order_status
      intent.notes = (
        str(item.get("effective_status_reason") or item.get("status_msg") or "") or None
      )
    if lifecycle_open:
      # Broker ORDER terminal is per attempt. Only the lifecycle coordinator
      # can close the original intent after every attempt's fills converge.
      if not (projection and int(projection["expected"]) > int(projection["received"])):
        intent.status = (
          previous_intent_status
          if previous_intent_status in {"PENDING", "APPROVED", "EXECUTION_READY", "EXECUTION_PENDING"}
          else "PARTIAL_FILLED" if int(intent.executed_volume or 0) else "QUEUED"
        )
      if projection is not None:
        projection["lifecycle_open"] = True
    return projection

  fill_volume = max(0, int(item.get("traded_volume") or item.get("volume") or 0))
  fill_price = float(item.get("traded_price") or item.get("price") or 0.0)
  if fill_volume <= 0 or fill_price <= 0:
    return None
  previous_volume = max(0, int(intent.executed_volume or 0))
  previous_price = float(intent.executed_price or 0.0)
  total_volume = previous_volume + fill_volume
  intent.executed_volume = total_volume
  intent.executed_price = (
    previous_price * previous_volume + fill_price * fill_volume
  ) / total_volume
  intent.executed_time = to_naive_utc(
    _parse_report_time(item.get("traded_time") or item.get("trade_time"))
  )
  if lifecycle_finished:
    intent.status = "RECONCILE_REQUIRED"
    intent.notes = "T_ORDER_EXECUTION_AFTER_LIFECYCLE_FINALIZED"
    return None
  pending = await db.get(PendingTradeOrder, correlation.client_order_id)
  requested_volume = max(
    0,
    int((pending.volume if pending is not None else None) or intent.target_volume or 0),
  )
  if getattr(pending, "t_order_original_created_at", None) is not None:
    requested_volume = max(0, int(intent.target_volume or requested_volume))
  invalidation = item.get("zero_fill_proof_invalidation")
  if isinstance(invalidation, Mapping) and str(
    invalidation.get("error_code") or ""
  ):
    # The execution itself remains authoritative and is accumulated above, but
    # it contradicts a plan state that already finalized or released this
    # intent.  Keep the audit row sticky until the account/plan is reconciled;
    # projecting the late fill as an ordinary PARTIAL_FILLED would silently
    # erase the fail-closed quarantine raised in the same transaction.
    intent.status = "RECONCILE_REQUIRED"
    intent.notes = str(invalidation["error_code"])
    return None
  projection = await _terminal_order_fill_projection(db, correlation, intent)
  if projection and int(projection["expected"]) > int(projection["received"]):
    intent.status = "RECONCILE_REQUIRED"
    intent.notes = _fill_projection_note(projection)
  elif projection:
    intent.status = str(projection["status"])
    intent.notes = str(projection.get("reason") or "") or None
  elif str(intent.status or "").upper() not in {"REJECTED", "CANCELLED", "EXPIRED"}:
    intent.status = (
      "FILLED"
      if requested_volume > 0 and total_volume >= requested_volume
      else "PARTIAL_FILLED"
    )
  if lifecycle_open and not (
    projection and int(projection["expected"]) > int(projection["received"])
  ):
    intent.status = (
      previous_intent_status
      if previous_intent_status in {"PENDING", "APPROVED", "EXECUTION_READY", "EXECUTION_PENDING"}
      else "PARTIAL_FILLED" if total_volume else "QUEUED"
    )
  return projection


async def finalize_t_order_lifecycle(db, pending: PendingTradeOrder) -> bool:
  """Close one original T intent only after all attempts have durable proof.

  The caller holds the account coordinator and commits this transaction. A
  StrategyRun receives one durable aggregate terminal notification; broker
  attempt reports themselves remain immutable, separate evidence.
  """
  if not t_order_lifecycle_pending(pending):
    return False
  intent = await db.get(TradeIntentRecord, pending.intent_id, with_for_update=True)
  if intent is None:
    return False
  if str(intent.status or "") in {"PENDING", "APPROVED", "EXECUTION_READY"}:
    return False
  attempts = list((await db.scalars(
    select(PendingTradeOrder).where(
      PendingTradeOrder.intent_id == pending.intent_id,
      PendingTradeOrder.account_id == pending.account_id,
    ).order_by(PendingTradeOrder.t_order_attempt).with_for_update()
  )).all())
  if not attempts or attempts[-1].client_order_id != pending.client_order_id:
    return False
  total = 0
  last_correlation = None
  seen_broker_ids: set[str] = set()
  for index, attempt in enumerate(attempts):
    if (
      int(attempt.t_order_attempt or 0) != index
      or _durable_owner_triple(attempt) != _durable_owner_triple(intent)
      or str(attempt.instrument_code) != str(pending.instrument_code)
      or str(attempt.t_trade_role) != str(pending.t_trade_role)
      or str(attempt.side) != str(pending.side)
      or str(attempt.bucket or "") != str(pending.bucket or "")
      or str(attempt.batch_id or "") != str(pending.batch_id or "")
      or attempt.t_order_original_created_at != attempts[0].t_order_original_created_at
      or str(attempt.status or "") not in _FILL_TERMINAL_ORDER_STATUSES | {"RECONCILED_ZERO_FILL"}
      or (index and attempt.t_order_parent_client_id != attempts[index - 1].client_order_id)
    ):
      return False
    correlation = await db.scalar(select(OrderCorrelation).where(
      OrderCorrelation.client_order_id == attempt.client_order_id,
    ))
    if correlation is None or not _exact_intent_binding(attempt, correlation, intent):
      return False
    last_correlation = correlation
    unapplied = await db.scalar(select(StrategyRuntimeEvent.event_id).where(
      StrategyRuntimeEvent.client_order_id == attempt.client_order_id,
      StrategyRuntimeEvent.application_status != "APPLIED",
    ).limit(1))
    if unapplied:
      return False
    broker_id = str(attempt.broker_order_id or "")
    if not broker_id:
      if correlation.broker_order_id:
        return False
      metadata = dict(attempt.request_metadata or {})
      source = str(metadata.get("execution_terminal_source") or "")
      if (
        str(attempt.status or "") not in {"EXPIRED", "REJECTED", "CANCELLED", "RECONCILED_ZERO_FILL"}
        or source not in {
          LOCAL_OUTBOX_EXPIRED_ZERO_FILL_SOURCE,
          LOCAL_AGENT_PRE_EXECUTION_ZERO_FILL_SOURCE,
          "LOCAL_OUTBOX_CANCEL",
        }
      ):
        return False
      command_id = str(metadata.get("command_lifecycle_message_id") or "")
      command = await db.get(TradeCommandOutbox, command_id) if command_id else None
      if (
        command is None
        or str(command.client_order_id) != str(attempt.client_order_id)
        or str(command.account_id) != str(attempt.account_id)
        or _durable_owner_triple(command) != _durable_owner_triple(attempt)
        or str(command.delivery_status or "") != str(attempt.status or "")
      ):
        return False
      payload = dict(command.payload or {})
      if (
        str(payload.get("command_kind") or "") != "PLACE_ORDER"
        or str(payload.get("client_order_id") or "") != str(attempt.client_order_id)
        or str(payload.get("account_id") or "") != str(attempt.account_id)
        or str(payload.get("instrument_code") or "") != str(attempt.instrument_code)
        or str(payload.get("side") or "") != str(attempt.side)
        or int(payload.get("volume") or 0) != int(attempt.volume or 0)
      ):
        return False
      if source == LOCAL_AGENT_PRE_EXECUTION_ZERO_FILL_SOURCE:
        if (
          str(command.delivery_status) not in {"EXPIRED", "REJECTED"}
          or
          command.acknowledged_at is None
          or str(command.last_error or "") != str(metadata.get("execution_terminal_reason") or "")
          or not str(command.last_error or "")
        ):
          return False
      elif command.delivered_at is not None or command.acknowledged_at is not None:
        return False
      elif source == LOCAL_OUTBOX_EXPIRED_ZERO_FILL_SOURCE and (
        str(command.delivery_status) != "EXPIRED"
        or command.expires_at is None
        or to_naive_utc(command.expires_at) > to_naive_utc(utcnow())
      ):
        return False
      elif source == "LOCAL_OUTBOX_CANCEL" and str(command.delivery_status) != "CANCELLED":
        return False
      continue
    if not broker_id.isdecimal() or broker_id in seen_broker_ids:
      return False
    seen_broker_ids.add(broker_id)
    order = await db.get(Order, int(broker_id))
    trades = list((await db.scalars(select(Trade).where(
      Trade.order_id == int(broker_id),
      Trade.account_id == attempt.account_id,
      Trade.stock_code == attempt.instrument_code,
    ))).all())
    filled = sum(max(0, int(trade.volume or 0)) for trade in trades)
    if (
      order is None
      or str(correlation.broker_order_id or "") != broker_id
      or str(order.account_id) != str(attempt.account_id)
      or str(order.stock_code) != str(attempt.instrument_code)
      or int(order.volume or 0) != int(attempt.volume or 0)
      or _normalized_order_status(order.status) not in _FILL_TERMINAL_ORDER_STATUSES
      or int(order.traded_volume or 0) != filled
      or (filled == 0 and str(attempt.status) != "RECONCILED_ZERO_FILL")
    ):
      return False
    total += filled
  if total != int(intent.executed_volume or 0):
    return False
  requested = int(attempts[0].volume or 0)
  if total > requested:
    return False
  status = "RECONCILED_ZERO_FILL" if total == 0 else "FILLED" if total == requested else "CANCELLED"
  owner_type, owner_id, environment = _durable_owner_triple(pending)
  if owner_type == ExecutionOwnerType.EXIT_PLAN.value:
    record = await db.get(AutoExitPlanRecord, owner_id, with_for_update=True)
    if record is None:
      return False
    if (
      str(record.account_id) != str(pending.account_id)
      or str(record.instrument_code) != str(pending.instrument_code)
      or str(record.environment) != environment
    ):
      return False
    plan = ExitPlan.from_dict(dict(record.plan_state or {}))
    if plan.pending_intent_id != pending.intent_id or int(plan.pending_filled_volume or 0) != total:
      return False
    ExitPlanBook([plan]).apply_order_event(
      plan_id=owner_id, intent_id=pending.intent_id,
      status=status, cumulative_filled_volume=total,
    )
    AutoExitPlanService._sync_record(record, plan)
    await AutoExitPlanService()._append_event(
      db, business_key=f"t-order-lifecycle:{pending.intent_id}",
      plan_id=owner_id, event_type="ORDER_LIFECYCLE_FINALIZED",
      payload={"intent_id": pending.intent_id, "status": status, "filled_volume": total},
    )
  elif owner_type == ExecutionOwnerType.STRATEGY_RUN.value:
    business_key = f"t-order-lifecycle:{pending.intent_id}"
    report = {
      "effective_order_status": status,
      "traded_volume": total,
      "order_volume": requested,
      "t_order_lifecycle_finalized": True,
    }
    db.add(StrategyRuntimeEvent(
      event_id=str(uuid.uuid4()), business_key=business_key,
      owner_type=owner_type, owner_id=owner_id, environment=environment,
      strategy_run_id=owner_id, client_order_id=pending.client_order_id,
      broker_order_id=pending.broker_order_id, event_type="ORDER",
      payload=_event_payload(last_correlation, report, business_key=business_key),
      application_status="PENDING", application_attempts=0, created_at=utcnow(),
    ))
  else:
    return False
  intent.status = status
  intent.notes = "T_ORDER_LIFECYCLE_FINALIZED"
  for attempt in attempts:
    attempt.request_metadata = {
      **dict(attempt.request_metadata or {}), "t_order_lifecycle_finished": True,
    }
  return True


async def _project_t_trade_event(
  batch: TTradeBatch,
  *,
  event_type: str,
  role: str,
  item: dict[str, Any],
  terminal_projection: Optional[dict[str, Any]] = None,
) -> None:
  if terminal_projection and terminal_projection.get("lifecycle_finalized_replay"):
    return
  previous_projection = (
    batch.status,
    batch.exception_reason,
    batch.entry_broker_order_id,
    batch.exit_broker_order_id,
    int(batch.entry_filled_volume or 0),
    float(batch.entry_avg_price or 0.0),
    int(batch.exit_filled_volume or 0),
    float(batch.exit_avg_price or 0.0),
    batch.entry_filled_at,
    batch.last_exit_filled_at,
    batch.closed_at,
    batch.terminal_at,
  )
  broker_order_id = str(item.get("order_id") or item.get("broker_order_id") or "")
  if event_type == "ORDER":
    status = _normalized_order_status(
      item.get("effective_order_status")
      or item.get("status")
      or item.get("order_status")
    )
    if role == "ENTRY":
      batch.entry_broker_order_id = broker_order_id or batch.entry_broker_order_id
    elif role == "EXIT":
      batch.exit_broker_order_id = broker_order_id or batch.exit_broker_order_id
    if terminal_projection and terminal_projection.get("lifecycle_open"):
      status = "PARTIAL_FILLED" if int(terminal_projection["received"]) else "SUBMITTED"
    if status == "RECONCILE_REQUIRED":
      batch.status = "RECONCILE_REQUIRED"
      batch.exception_reason = str(
        item.get("effective_status_reason")
        or item.get("status_msg")
        or "RECONCILE_REQUIRED"
      )
    elif terminal_projection and int(terminal_projection["expected"]) > int(
      terminal_projection["received"]
    ):
      batch.status = "RECONCILE_REQUIRED"
      batch.exception_reason = _fill_projection_note(terminal_projection)
    elif role == "ENTRY":
      batch.status = {
        "PENDING": "ENTRY_QUEUED",
        "SUBMITTED": "ENTRY_SUBMITTED",
        "PARTIAL_FILLED": "ENTRY_PARTIAL",
        "FILLED": "OPEN",
        "REJECTED": "ENTRY_REJECTED",
        "CANCELLED": "ENTRY_REJECTED",
        "EXPIRED": "ENTRY_EXPIRED",
      }.get(status, batch.status)
      batch.exception_reason = (
        str(item.get("effective_status_reason") or item.get("status_msg") or "") or None
      )
      if (
        batch.status in {"ENTRY_REJECTED", "ENTRY_EXPIRED"}
        and int(batch.entry_filled_volume or 0) == 0
      ):
        # A zero-fill entry has no TRADE close.  Keep the broker ORDER terminal
        # boundary separate from ``closed_at``, which is reserved for fills.
        terminal_at = _strict_t_trade_report_time(
          item.get("order_time") or item.get("reported_at")
        )
        if terminal_at is not None:
          batch.terminal_at = (
            max(batch.terminal_at, terminal_at)
            if batch.terminal_at is not None
            else terminal_at
          )
    elif role == "EXIT":
      batch.status = {
        "PENDING": "EXIT_TRIGGERED",
        "SUBMITTED": "EXIT_SUBMITTED",
        "PARTIAL_FILLED": "EXIT_PARTIAL",
        "FILLED": "CLOSED",
        "REJECTED": "EXIT_REJECTED",
        "CANCELLED": "EXIT_REJECTED",
        "EXPIRED": "EXIT_REJECTED",
      }.get(status, batch.status)
      if status in {"REJECTED", "CANCELLED", "EXPIRED"}:
        batch.exception_reason = str(
          item.get("effective_status_reason") or item.get("status_msg") or status
        )
      else:
        batch.exception_reason = None
  else:
    volume = max(0, int(item.get("traded_volume") or item.get("volume") or 0))
    price = float(item.get("traded_price") or item.get("price") or 0.0)
    traded_at = _strict_t_trade_report_time(
      item.get("traded_time") or item.get("trade_time")
    )
    if role == "ENTRY":
      previous = int(batch.entry_filled_volume or 0)
      total = previous + volume
      if total:
        batch.entry_avg_price = (
          float(batch.entry_avg_price or 0.0) * previous + price * volume
        ) / total
      batch.entry_filled_volume = total
      if volume > 0 and price > 0 and traded_at is not None:
        batch.entry_filled_at = (
          min(batch.entry_filled_at, traded_at)
          if batch.entry_filled_at is not None
          else traded_at
        )
      if terminal_projection and int(terminal_projection["expected"]) > int(
        terminal_projection["received"]
      ):
        batch.status = "RECONCILE_REQUIRED"
        batch.exception_reason = _fill_projection_note(terminal_projection)
      else:
        batch.status = (
          "OPEN" if total >= int(batch.target_volume or total) else "ENTRY_PARTIAL"
        )
        batch.exception_reason = None
    elif role == "EXIT":
      previous = int(batch.exit_filled_volume or 0)
      total = previous + volume
      if total:
        batch.exit_avg_price = (
          float(batch.exit_avg_price or 0.0) * previous + price * volume
        ) / total
      batch.exit_filled_volume = total
      if volume > 0 and price > 0 and traded_at is not None:
        batch.last_exit_filled_at = (
          max(batch.last_exit_filled_at, traded_at)
          if batch.last_exit_filled_at is not None
          else traded_at
        )
      if terminal_projection and int(terminal_projection["expected"]) > int(
        terminal_projection["received"]
      ):
        batch.status = "RECONCILE_REQUIRED"
        batch.exception_reason = _fill_projection_note(terminal_projection)
      else:
        batch.status = (
          "CLOSED"
          if total >= int(batch.entry_filled_volume or total)
          else "EXIT_PARTIAL"
        )
        batch.exception_reason = None
        if batch.status == "CLOSED" and batch.last_exit_filled_at is not None:
          batch.closed_at = (
            max(batch.closed_at, batch.last_exit_filled_at)
            if batch.closed_at is not None
            else batch.last_exit_filled_at
          )
          batch.terminal_at = (
            max(batch.terminal_at, batch.last_exit_filled_at)
            if batch.terminal_at is not None
            else batch.last_exit_filled_at
          )
  current_projection = (
    batch.status,
    batch.exception_reason,
    batch.entry_broker_order_id,
    batch.exit_broker_order_id,
    int(batch.entry_filled_volume or 0),
    float(batch.entry_avg_price or 0.0),
    int(batch.exit_filled_volume or 0),
    float(batch.exit_avg_price or 0.0),
    batch.entry_filled_at,
    batch.last_exit_filled_at,
    batch.closed_at,
    batch.terminal_at,
  )
  if current_projection != previous_projection:
    batch.version = int(batch.version or 0) + 1


async def _reconcile_t_trade_batch_after_runtime_event(
  db,
  event: StrategyRuntimeEvent,
) -> None:
  """Restore the staged batch projection after a transient apply failure."""
  payload = dict(event.payload or {})
  metadata = dict(payload.get("metadata") or {})
  report = dict(payload.get("report") or {})
  batch_id = str(metadata.get("t_batch_id") or metadata.get("batch_id") or "")
  role = str(metadata.get("t_trade_role") or "").strip().upper()
  if not batch_id or role not in {"ENTRY", "EXIT"}:
    return
  batch = await db.get(TTradeBatch, batch_id)
  if batch is None:
    return
  event_owner = _durable_owner_triple(event)
  batch_owner = _durable_owner_triple(batch)
  if event_owner is None or batch_owner != event_owner:
    # A T-trade projection is a separate owner/environment obligation.  A
    # mismatched batch must remain untouched even when the runtime event is
    # otherwise routable.
    return
  correlation = (
    await db.execute(
      select(OrderCorrelation).where(
        OrderCorrelation.client_order_id == event.client_order_id
      )
    )
  ).scalar_one_or_none()
  terminal_projection = None
  if correlation is None or _durable_owner_triple(correlation) != event_owner:
    return
  if not correlation.intent_id:
    correlation = None
  if correlation is not None:
    intent = await db.get(TradeIntentRecord, correlation.intent_id)
    if intent is not None:
      terminal_projection = await _terminal_order_fill_projection(
        db,
        correlation,
        intent,
        current_order=report if event.event_type == "ORDER" else None,
      )

  previous = (batch.status, batch.exception_reason)
  order_status = (
    _normalized_order_status(
      report.get("effective_order_status")
      or report.get("status")
      or report.get("order_status")
    )
    if event.event_type == "ORDER"
    else ""
  )
  if order_status == "RECONCILE_REQUIRED":
    batch.status = "RECONCILE_REQUIRED"
    batch.exception_reason = str(
      report.get("effective_status_reason")
      or report.get("status_msg")
      or metadata.get("approval_reason")
      or "RECONCILE_REQUIRED"
    )
  elif terminal_projection and int(terminal_projection["expected"]) > int(
    terminal_projection["received"]
  ):
    batch.status = "RECONCILE_REQUIRED"
    batch.exception_reason = _fill_projection_note(terminal_projection)
  elif role == "ENTRY":
    filled = max(0, int(batch.entry_filled_volume or 0))
    target = max(0, int(batch.target_volume or 0))
    if filled > 0:
      batch.status = "OPEN" if filled >= (target or filled) else "ENTRY_PARTIAL"
      batch.exception_reason = None
    elif event.event_type == "ORDER":
      batch.status = {
        "PENDING": "ENTRY_QUEUED",
        "SUBMITTED": "ENTRY_SUBMITTED",
        "PARTIAL_FILLED": "ENTRY_PARTIAL",
        "FILLED": "OPEN",
        "REJECTED": "ENTRY_REJECTED",
        "CANCELLED": "ENTRY_REJECTED",
        "EXPIRED": "ENTRY_EXPIRED",
      }.get(order_status, batch.status)
      batch.exception_reason = (
        str(report.get("effective_status_reason") or report.get("status_msg") or "")
        or None
      )
  else:
    exited = max(0, int(batch.exit_filled_volume or 0))
    entered = max(0, int(batch.entry_filled_volume or 0))
    if exited > 0:
      batch.status = "CLOSED" if exited >= (entered or exited) else "EXIT_PARTIAL"
      batch.exception_reason = None
    elif event.event_type == "ORDER":
      batch.status = {
        "PENDING": "EXIT_TRIGGERED",
        "SUBMITTED": "EXIT_SUBMITTED",
        "PARTIAL_FILLED": "EXIT_PARTIAL",
        "FILLED": "CLOSED",
        "REJECTED": "EXIT_REJECTED",
        "CANCELLED": "EXIT_REJECTED",
        "EXPIRED": "EXIT_REJECTED",
      }.get(order_status, batch.status)
      batch.exception_reason = (
        str(report.get("effective_status_reason") or report.get("status_msg") or "")
        or None
      )
  if previous != (batch.status, batch.exception_reason):
    batch.version = int(batch.version or 0) + 1


async def _insert_runtime_event(db, event: StrategyRuntimeEvent) -> None:
  async with db.begin_nested():
    db.add(event)
    await db.flush()


async def _stage_runtime_events(report: AgentReportInbox) -> None:
  _require_current_protocol(report.protocol_version)
  from .strategy_manager import strategy_manager

  executor = strategy_manager.executor
  arm_barrier = getattr(
    executor,
    "arm_durable_event_barrier",
    None,
  )
  refresh_barrier = getattr(
    executor,
    "refresh_durable_event_barrier",
    None,
  )
  barrier_checked_runs: set[str] = set()
  affected_run_ids: set[str] = set()
  payload = dict(report.payload or {})
  try:
    db = AsyncSessionLocal()
    try:
      # Establish the outer transaction before any SAVEPOINT. Without this,
      # SQLite can release the first nested savepoint as a standalone commit,
      # breaking the event+projection atomicity exercised by local tests.
      await db.begin()
      runtime_items = _report_items(report)
      authoritative_identity = _authoritative_snapshot_identity(report)
      if authoritative_identity is not None:
        snapshot_id, snapshot_hash = authoritative_identity
        checkpoint_by_account: dict[str, bool] = {}
        filtered_runtime_items: list[tuple[str, dict[str, Any]]] = []
        for event_type, item in runtime_items:
          account_id = str(item.get("account_id") or "").strip()
          if not account_id:
            # A full snapshot item without an account cannot be safely tied to
            # a current account after a mixed-generation replay.
            continue
          if account_id not in checkpoint_by_account:
            rollout = await db.get(AccountExecutionControl, account_id)
            checkpoint_by_account[account_id] = bool(
              rollout is not None
              and str(rollout.last_snapshot_id or "") == snapshot_id
              and str(rollout.last_snapshot_hash or "") == snapshot_hash
            )
          if checkpoint_by_account[account_id]:
            filtered_runtime_items.append((event_type, item))
        runtime_items = filtered_runtime_items
      runtime_items.extend(await _full_snapshot_zero_fill_items(db, report))
      last_staged_at: Optional[datetime] = None
      for event_type, raw_item in runtime_items:
        item = dict(raw_item)
        broker_order_id = str(item.get("order_id") or item.get("broker_order_id") or "")
        client_order_id = str(
          item.get("client_order_id")
          or report.client_order_id
          or payload.get("client_order_id")
          or ""
        )
        correlation = await _correlation_for_report(
          db,
          client_order_id=client_order_id,
          broker_order_id=broker_order_id,
          allow_broker_fallback=(
            not bool(client_order_id) or authoritative_identity is not None
          ),
        )
        if correlation is None:
          continue
        command_expiry_reconciliation = (
          await _command_expired_entry_zero_fill_reconciliation(
            db,
            correlation,
            item,
          )
        )
        if command_expiry_reconciliation is not None:
          item.update(
            {
              "effective_order_status": "RECONCILED_ZERO_FILL",
              "effective_status_reason": "command_expired",
              "traded_volume": 0,
              "filled_volume": 0,
              "zero_fill_reconciliation": command_expiry_reconciliation,
            }
          )
        owner_triple = _durable_owner_triple(correlation)
        if owner_triple is None:
          # The report is already durable in AgentReportInbox.  A malformed
          # owner binding remains reconcile-only and must not be projected by
          # guessing from strategy_run_id or payload metadata.
          continue
        owner_type, owner_id, environment = owner_triple
        run_id = (
          owner_id if owner_type == ExecutionOwnerType.STRATEGY_RUN.value else ""
        )
        if run_id:
          affected_run_ids.add(run_id)
        if arm_barrier is not None and run_id and run_id not in barrier_checked_runs:
          earliest_backlog_key = (
            await db.execute(
              select(StrategyRuntimeEvent.business_key)
              .where(
                StrategyRuntimeEvent.owner_type == owner_type,
                StrategyRuntimeEvent.owner_id == owner_id,
                StrategyRuntimeEvent.environment == environment,
                StrategyRuntimeEvent.application_status != "APPLIED",
              )
              .order_by(
                StrategyRuntimeEvent.created_at,
                StrategyRuntimeEvent.event_id,
              )
              .limit(1)
            )
          ).scalar_one_or_none()
          if earliest_backlog_key:
            arm_barrier(run_id, earliest_backlog_key)
          barrier_checked_runs.add(run_id)

        if event_type == "ORDER":
          proposed_status = _normalized_order_status(
            item.get("effective_order_status")
            or item.get("status")
            or item.get("order_status")
          )
          if await is_exact_finalized_exit_order_replay(
            db,
            client_order_id=correlation.client_order_id,
            evidence_status=proposed_status,
            cumulative_filled_volume=_reported_cumulative_fill(item),
          ):
            continue
          if proposed_status in ZERO_FILL_CONTRADICTION_ORDER_STATUSES:
            zero_fill_invalidation = await runtime_zero_fill_invalidation(
              db,
              correlation=correlation,
            )
            if zero_fill_invalidation is not None:
              item["zero_fill_proof_invalidation"] = zero_fill_invalidation
              item["zero_fill_contradicted_order_status"] = proposed_status
              item["effective_status_reason"] = (
                str(zero_fill_invalidation.get("error_code") or "")
                or "EXIT_PLAN_BROKER_FACT_CONTRADICTED_RELEASED_INTENT"
              )
              proposed_status = "RECONCILE_REQUIRED"
          if proposed_status not in _SPECIAL_RUNTIME_ORDER_STATUSES:
            pending = await db.get(
              PendingTradeOrder,
              correlation.client_order_id,
              with_for_update=True,
            )
            if pending is not None:
              source_sequence = max(
                0,
                int(
                  item.get("source_sequence")
                  or payload.get("source_sequence")
                  or payload.get("sequence")
                  or 0
                ),
              )
              stored_sequence = max(0, int(pending.last_source_sequence or 0))
              if (
                source_sequence
                and stored_sequence
                and source_sequence < stored_sequence
              ) or not can_transition_order_status(
                pending.status,
                proposed_status,
              ):
                continue
          item["effective_order_status"] = proposed_status
        elif event_type == "TRADE":
          zero_fill_invalidation = await runtime_zero_fill_invalidation(
            db,
            correlation=correlation,
          )
          if zero_fill_invalidation is not None:
            item["zero_fill_proof_invalidation"] = zero_fill_invalidation

        if broker_order_id and not correlation.broker_order_id:
          correlation.broker_order_id = broker_order_id
        business_key = _runtime_business_key(event_type, correlation, item)
        existing = (
          await db.execute(
            select(StrategyRuntimeEvent).where(
              StrategyRuntimeEvent.business_key == business_key
            )
          )
        ).scalar_one_or_none()
        if existing is not None:
          if (
            existing.application_status != "APPLIED"
            and arm_barrier is not None
            and owner_type == ExecutionOwnerType.STRATEGY_RUN.value
          ):
            arm_barrier(owner_id, existing.business_key)
          continue
        created_at = utcnow()
        if last_staged_at is not None and created_at <= last_staged_at:
          created_at = last_staged_at + timedelta(microseconds=1)
        last_staged_at = created_at
        runtime_event = StrategyRuntimeEvent(
          event_id=str(uuid.uuid4()),
          business_key=business_key,
          owner_type=owner_type,
          owner_id=owner_id,
          environment=environment,
          strategy_run_id=run_id or None,
          client_order_id=correlation.client_order_id,
          broker_order_id=broker_order_id or None,
          event_type=event_type,
          payload=_event_payload(
            correlation,
            item,
            business_key=business_key,
          ),
          application_status="PENDING",
          application_attempts=0,
          created_at=created_at,
        )
        if (
          arm_barrier is not None
          and owner_type == ExecutionOwnerType.STRATEGY_RUN.value
        ):
          arm_barrier(owner_id, business_key)
        try:
          await _insert_runtime_event(db, runtime_event)
        except IntegrityError:
          # Another producer won the durable business-key race. Its event and
          # projections are authoritative; do not apply this report twice.
          if (
            arm_barrier is not None
            and owner_type == ExecutionOwnerType.STRATEGY_RUN.value
          ):
            arm_barrier(owner_id, business_key)
          continue
        terminal_projection = await _project_trade_intent_event(
          db,
          correlation,
          event_type=event_type,
          item=item,
        )
        if correlation.batch_id:
          batch = await db.get(TTradeBatch, correlation.batch_id)
          if batch is not None and _durable_owner_triple(batch) == owner_triple:
            await _project_t_trade_event(
              batch,
              event_type=event_type,
              role=str(correlation.t_trade_role or "").upper(),
              item=item,
              terminal_projection=terminal_projection,
            )
      await db.commit()
    except Exception:
      await db.rollback()
      raise
    finally:
      await db.close()
  except Exception as exc:
    if refresh_barrier is not None:
      for run_id in affected_run_ids:
        try:
          await refresh_barrier(run_id)
        except Exception as refresh_exc:
          logger.error(
            "Failed to reconcile durable barrier after staging rollback: "
            "run_id=%s error=%s",
            run_id,
            refresh_exc,
          )
    raise RetryableReportError(str(exc)) from exc

  if refresh_barrier is not None:
    for run_id in affected_run_ids:
      await refresh_barrier(run_id)


def _runtime_event_report(event: OwnerRuntimeEvent) -> dict[str, Any]:
  payload = event.payload
  raw_report = payload.get("report") if isinstance(payload, Mapping) else None
  return dict(raw_report) if isinstance(raw_report, Mapping) else {}


def _runtime_event_client_order_id(event: OwnerRuntimeEvent) -> str:
  """Read the staged model's direct client identity, never owner metadata."""

  payload = event.payload
  if not isinstance(payload, Mapping):
    return ""
  return str(payload.get("client_order_id") or "").strip()


def _runtime_event_broker_order_id(event: OwnerRuntimeEvent) -> str:
  payload = event.payload
  if not isinstance(payload, Mapping):
    return ""
  return str(payload.get("broker_order_id") or "").strip()


async def _exact_runtime_order_binding(
  db,
  *,
  target: OwnerRuntimeTarget,
  event: OwnerRuntimeEvent,
) -> tuple[PendingTradeOrder, OrderCorrelation]:
  """Load one client-primary pending/correlation owner chain.

  Broker ids are facts attached to an already selected client order.  They
  cannot select a second order or owner, even for a report that contains both
  ids.
  """

  client_order_id = _runtime_event_client_order_id(event)
  if not client_order_id:
    raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
  # ``OrderCorrelation.id`` is an opaque UUID.  The client order id is a
  # unique causal key, but it is not the ORM primary key and must be resolved
  # explicitly before proving the owner chain.
  try:
    correlation = (
      await db.execute(
        select(OrderCorrelation)
        .where(OrderCorrelation.client_order_id == client_order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
      )
    ).scalar_one_or_none()
  except MultipleResultsFound as exc:
    raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT) from exc
  if correlation is None:
    raise OwnerRuntimeRoutingError(OWNER_TARGET_NOT_FOUND)
  pending = await db.get(
    PendingTradeOrder,
    client_order_id,
    with_for_update=True,
    populate_existing=True,
  )
  intent = None
  if correlation.intent_id:
    intent = await db.get(
      TradeIntentRecord,
      correlation.intent_id,
      with_for_update=True,
      populate_existing=True,
    )
  if pending is None:
    raise OwnerRuntimeRoutingError(OWNER_TARGET_NOT_FOUND)
  owner = (
    target.execution_ref.owner_type.value,
    target.execution_ref.owner_id,
    target.environment.value,
  )
  if (
    _durable_owner_triple(correlation) != owner
    or _durable_owner_triple(pending) != owner
    or str(correlation.account_id or "") != str(pending.account_id or "")
    or str(correlation.client_order_id or "") != client_order_id
    or str(correlation.intent_id or "") != str(pending.intent_id or "")
    or (
      correlation.broker_order_id
      and pending.broker_order_id
      and str(correlation.broker_order_id)
      != str(pending.broker_order_id)
    )
    or not _exact_intent_binding(pending, correlation, intent)
  ):
    if correlation.intent_id and intent is None:
      raise OwnerRuntimeRoutingError(OWNER_TARGET_NOT_FOUND)
    raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
  broker_order_id = _runtime_event_broker_order_id(event)
  if (
    broker_order_id
    and correlation.broker_order_id
    and str(correlation.broker_order_id) != broker_order_id
  ):
    raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
  if (
    broker_order_id
    and pending.broker_order_id
    and str(pending.broker_order_id) != broker_order_id
  ):
    raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
  report = _runtime_event_report(event)
  reported_client_order_id = str(report.get("client_order_id") or "").strip()
  if reported_client_order_id and reported_client_order_id != client_order_id:
    raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
  reported_broker_order_id = str(
    report.get("order_id") or report.get("broker_order_id") or ""
  ).strip()
  if (
    reported_broker_order_id
    and broker_order_id
    and reported_broker_order_id != broker_order_id
  ):
    raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
  if (
    reported_broker_order_id
    and correlation.broker_order_id
    and str(correlation.broker_order_id) != reported_broker_order_id
  ):
    raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
  if (
    reported_broker_order_id
    and pending.broker_order_id
    and str(pending.broker_order_id) != reported_broker_order_id
  ):
    raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
  reported_account = str(report.get("account_id") or "").strip()
  if reported_account and reported_account != str(pending.account_id or ""):
    raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
  reported_instrument = str(
    report.get("stock_code") or report.get("instrument_code") or ""
  ).strip().upper()
  if (
    reported_instrument
    and reported_instrument != str(pending.instrument_code or "").strip().upper()
  ):
    raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
  return pending, correlation


class _ExitPlanRuntimeHandler:
  """Verify the single durable ExitPlan projection before marking owner apply.

  ORDER/TRADE plan CAS is applied exactly once by AutoExitPlanService before
  routing.  This handler only proves the resulting plan/order binding, so the
  owner event marker cannot apply the same fill a second time.
  """

  def __init__(self, db) -> None:
    self._db = db

  async def resolve(
    self,
    execution_ref: ExecutionOwnerRef,
  ) -> OwnerRuntimeTarget | None:
    record = await self._db.get(AutoExitPlanRecord, execution_ref.owner_id)
    if record is None:
      return None
    source_owner = _durable_owner_triple(record)
    if source_owner is None:
      raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
    try:
      environment = ExecutionEnvironment(
        str(getattr(record.environment, "value", record.environment) or "")
        .strip()
        .upper()
      )
    except (TypeError, ValueError) as exc:
      raise OwnerRuntimeRoutingError(OWNER_ENVIRONMENT_CONFLICT) from exc
    if source_owner[2] != environment.value:
      raise OwnerRuntimeRoutingError(OWNER_ENVIRONMENT_CONFLICT)
    try:
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
    except (KeyError, TypeError, ValueError) as exc:
      raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT) from exc
    if (
      plan.plan_id != execution_ref.owner_id
      or str(plan.template.account_id or "") != str(record.account_id or "")
      or str(plan.template.instrument_code or "").upper()
      != str(record.instrument_code or "").upper()
      or str(plan.template.bucket or "") != str(record.bucket or "")
      or (
        str(record.strategy_run_id or "").strip()
        and str(plan.template.run_id or "").strip()
        != str(record.strategy_run_id or "").strip()
      )
    ):
      raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
    return OwnerRuntimeTarget(execution_ref, environment)

  async def apply(
    self,
    target: OwnerRuntimeTarget,
    event: OwnerRuntimeEvent,
  ) -> None:
    pending, correlation = await _exact_runtime_order_binding(
      self._db,
      target=target,
      event=event,
    )
    record = await self._db.get(
      AutoExitPlanRecord,
      target.execution_ref.owner_id,
      with_for_update=True,
      populate_existing=True,
    )
    if record is None:
      raise OwnerRuntimeRoutingError(OWNER_TARGET_NOT_FOUND)
    source_owner = _durable_owner_triple(record)
    owner = (
      target.execution_ref.owner_type.value,
      target.execution_ref.owner_id,
      target.environment.value,
    )
    if (
      source_owner is None
      or source_owner[2] != target.environment.value
      or str(record.account_id or "") != str(pending.account_id or "")
      or str(record.instrument_code or "").upper()
      != str(pending.instrument_code or "").upper()
      or _durable_exit_plan_sell_binding(pending, correlation) is not True
    ):
      raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
    try:
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      projected_status = str(
        getattr(plan.status, "value", plan.status) or ""
      ).upper()
      projected_exited_volume = int(plan.exited_volume or 0)
      projected_remaining_volume = int(plan.remaining_volume)
      record_exited_volume = int(record.exited_volume or 0)
      record_remaining_volume = int(record.remaining_volume or 0)
    except (TypeError, ValueError, OverflowError) as exc:
      raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT) from exc
    pending_order_ids = {
      str(pending.client_order_id or "").strip(),
      str(pending.broker_order_id or "").strip(),
    }
    requested_volume = int(pending.volume or 0)
    if getattr(pending, "t_order_original_created_at", None) is not None:
      attempts = list((await self._db.scalars(select(PendingTradeOrder).where(
        PendingTradeOrder.intent_id == pending.intent_id,
        PendingTradeOrder.account_id == pending.account_id,
      ).order_by(PendingTradeOrder.t_order_attempt))).all())
      if not attempts or any(_durable_owner_triple(row) != owner for row in attempts):
        raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
      requested_volume = int(attempts[0].volume or 0)
      for attempt in attempts:
        pending_order_ids.update({
          str(attempt.client_order_id or ""), str(attempt.broker_order_id or ""),
        })
    if (
      plan.plan_id != target.execution_ref.owner_id
      or str(plan.template.account_id or "") != str(record.account_id or "")
      or str(plan.template.instrument_code or "").upper()
      != str(record.instrument_code or "").upper()
      or str(plan.template.bucket or "") != str(record.bucket or "")
      or (
        str(record.strategy_run_id or "").strip()
        and str(plan.template.run_id or "").strip()
        != str(record.strategy_run_id or "").strip()
      )
      or str(record.status or "").upper() != projected_status
      or record_exited_volume != projected_exited_volume
      or record_remaining_volume != projected_remaining_volume
      or (
        plan.pending_intent_id
        and str(plan.pending_intent_id) != str(pending.intent_id or "")
      )
      or (
        plan.pending_order_id
        and str(plan.pending_order_id).strip() not in pending_order_ids
      )
      or (
        record.pending_client_order_id
        and str(record.pending_client_order_id).strip() not in pending_order_ids
      )
      or (
        int(plan.pending_requested_volume or 0) > 0
        and int(plan.pending_requested_volume or 0) != requested_volume
      )
    ):
      raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
    # The direct owner id comes from the event/ref and the pending/correlation
    # rows.  A plan's source owner is an origin fact and is intentionally not
    # compared with the EXIT_PLAN execution owner.
    if _durable_owner_triple(correlation) != owner:
      raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
    report = _runtime_event_report(event)
    if event.event_kind is OwnerRuntimeEventKind.ORDER:
      status = _normalized_order_status(
        report.get("effective_order_status")
        or report.get("status")
        or report.get("order_status")
      )
      if not status:
        raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
    elif event.event_kind is OwnerRuntimeEventKind.TRADE:
      if not str(
        report.get("execution_id")
        or report.get("traded_id")
        or report.get("trade_id")
        or ""
      ).strip():
        raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
    # No state is changed here: public report projections already ran from the inbox and are guarded
    # by their own durable business keys; the runtime event marker is the
    # idempotency boundary for this owner route.


class _ManualCommandRuntimeHandler:
  """Consume manual-command report events after pending reconciliation."""

  def __init__(self, db) -> None:
    self._db = db

  async def resolve(
    self,
    execution_ref: ExecutionOwnerRef,
  ) -> OwnerRuntimeTarget | None:
    rows = (
      await self._db.execute(
        select(OrderCorrelation).where(
          OrderCorrelation.owner_type == execution_ref.owner_type.value,
          OrderCorrelation.owner_id == execution_ref.owner_id,
        )
      )
    ).scalars().all()
    if not rows:
      return None
    triples = {_durable_owner_triple(row) for row in rows}
    if None in triples or len(triples) != 1:
      raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
    _owner_type, _owner_id, environment = next(iter(triples))
    try:
      canonical_environment = ExecutionEnvironment(environment)
    except (TypeError, ValueError) as exc:
      raise OwnerRuntimeRoutingError(OWNER_ENVIRONMENT_CONFLICT) from exc
    return OwnerRuntimeTarget(execution_ref, canonical_environment)

  async def apply(
    self,
    target: OwnerRuntimeTarget,
    event: OwnerRuntimeEvent,
  ) -> None:
    await _exact_runtime_order_binding(
      self._db,
      target=target,
      event=event,
    )
    if event.event_kind is OwnerRuntimeEventKind.TRADE:
      report = _runtime_event_report(event)
      if not str(
        report.get("execution_id")
        or report.get("traded_id")
        or report.get("trade_id")
        or ""
      ).strip():
        raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)


class _StrategyRunRuntimeHandler:
  """Explicit adapter for the existing StrategyExecutor consumer only."""

  def __init__(self, executor: Any, db) -> None:
    self._executor = executor
    self._db = db

  async def resolve(
    self,
    execution_ref: ExecutionOwnerRef,
  ) -> OwnerRuntimeTarget | None:
    run_id = execution_ref.owner_id
    runtime = getattr(self._executor, "runs", {}).get(run_id)
    if runtime is None:
      # A missing StrategyRun is a stable routing failure, not a reason to
      # redirect the event to another run or to a generic fallback consumer.
      return None
    runtime = self._executor.require_durable_event_consumer(run_id)
    mode = getattr(getattr(runtime, "context", None), "mode", None)
    try:
      environment = ExecutionEnvironment(
        str(getattr(mode, "value", mode) or "").strip().upper()
      )
    except (TypeError, ValueError) as exc:
      raise OwnerRuntimeRoutingError(OWNER_ENVIRONMENT_CONFLICT) from exc
    return OwnerRuntimeTarget(execution_ref, environment)

  async def apply(
    self,
    target: OwnerRuntimeTarget,
    event: OwnerRuntimeEvent,
  ) -> None:
    pending, correlation = await _exact_runtime_order_binding(
      self._db,
      target=target,
      event=event,
    )
    owner = (
      target.execution_ref.owner_type.value,
      target.execution_ref.owner_id,
      target.environment.value,
    )
    if (
      _durable_owner_triple(pending) != owner
      or _durable_owner_triple(correlation) != owner
    ):
      raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
    strategy_order_id = str(correlation.strategy_order_id or "").strip()
    if (
      not strategy_order_id
      or str(pending.strategy_order_id or "").strip() != strategy_order_id
      or not str(correlation.intent_id or "").strip()
      or str(pending.intent_id or "").strip()
      != str(correlation.intent_id or "").strip()
    ):
      raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
    if event.event_kind is OwnerRuntimeEventKind.TRADE:
      report = _runtime_event_report(event)
      if not str(
        report.get("execution_id")
        or report.get("traded_id")
        or report.get("trade_id")
        or ""
      ).strip():
        raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
    elif event.event_kind is not OwnerRuntimeEventKind.ORDER:
      raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
    await _apply_strategy_run_runtime_event(
      self._executor,
      target.execution_ref.owner_id,
      event,
      pending=pending,
      correlation=correlation,
    )


async def _apply_strategy_run_runtime_event(
  executor: Any,
  run_id: str,
  event: OwnerRuntimeEvent,
  *,
  pending: PendingTradeOrder,
  correlation: OrderCorrelation,
) -> None:
  payload = dict(event.payload or {})
  report = _runtime_event_report(event)
  raw_metadata = payload.get("metadata")
  event_metadata = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
  metadata = {
    key: value
    for key, value in event_metadata.items()
    if str(key).strip().lower() not in _RUNTIME_OWNER_METADATA_KEYS
  }
  correlation_owner = _durable_owner_triple(correlation)
  if correlation_owner is None:
    raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
  order_id = str(correlation.strategy_order_id or "").strip()
  intent_id = str(correlation.intent_id or "").strip()
  instrument_code = str(pending.instrument_code or "").strip().upper()
  try:
    requested_volume = int(pending.volume or 0)
  except (TypeError, ValueError, OverflowError) as exc:
    raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT) from exc
  if not order_id or not intent_id or not instrument_code or requested_volume <= 0:
    raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)

  durable_side = str(pending.side or "").strip().upper()
  if durable_side in {"BUY", "23", "ORDER_BUY"}:
    order_type = OrderType.BUY
  elif durable_side in {"SELL", "24", "ORDER_SELL"}:
    order_type = OrderType.SELL
  else:
    raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
  reported_side = str(
    report.get("side") or report.get("order_type") or ""
  ).strip().upper()
  if reported_side:
    reported_order_type = (
      OrderType.BUY
      if reported_side in {"BUY", "23", "ORDER_BUY"}
      else OrderType.SELL
      if reported_side in {"SELL", "24", "ORDER_SELL"}
      else None
    )
    if reported_order_type is None or reported_order_type is not order_type:
      raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)

  metadata.update(
    {
      # ``strategy_run_id`` is retained only as a callback scope fact for the
      # existing T-trade paper-fill validator, not as a routing identity.
      "strategy_run_id": run_id,
      "account_id": str(pending.account_id or ""),
      "strategy_order_id": order_id,
      "intent_id": intent_id,
      "instrument_code": instrument_code,
      "side": durable_side,
      "t_batch_id": correlation.batch_id or "",
      "bucket": correlation.bucket,
      "t_trade_role": str(correlation.t_trade_role or "").lower(),
      "risk_decision_id": correlation.risk_decision_id or "",
      "trace_id": correlation.trace_id,
      "substitution_plan": correlation.substitution_plan,
      "execution_mode": _wire_execution_mode(correlation_owner[2]),
      "runtime_event_key": str(
        payload.get("business_key") or event.event_id
      ).strip()
      or event.event_id,
    }
  )

  def report_number(
    keys: tuple[str, ...],
    *,
    default: int | float,
  ) -> int | float:
    integer = all(
      key in {"order_volume", "traded_volume", "filled_volume", "volume"}
      for key in keys
    )
    raw_value: Any = default
    for key in keys:
      if key in report and report.get(key) is not None:
        raw_value = report.get(key)
        break
    if integer and isinstance(raw_value, bool):
      raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
    try:
      number = float(raw_value)
    except (TypeError, ValueError, OverflowError) as exc:
      raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT) from exc
    if not math.isfinite(number) or number < 0:
      raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
    if integer and not number.is_integer():
      raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
    return int(number) if integer else number

  if event.event_kind == OwnerRuntimeEventKind.ORDER:
    authoritative_cumulative_fill = _reported_cumulative_fill(report)
    order_volume = int(
      report_number(
        ("order_volume", "volume"),
        default=requested_volume,
      )
    )
    order_price = float(
      report_number(
        ("price", "limit_price"),
        default=float(pending.limit_price or 0.0),
      )
    )
    traded_price = float(
      report_number(
        ("traded_price",),
        default=order_price,
      )
    )
    request = OrderRequest(
      instrument_code=instrument_code,
      order_type=order_type,
      price_type=PriceType.LIMIT,
      volume=order_volume,
      execution_ref=event.execution_ref,
      environment=event.environment,
      price=order_price,
      metadata=metadata,
    )
    status_name = _normalized_order_status(
      report.get("effective_order_status")
      or report.get("status")
      or report.get("order_status")
    )
    if getattr(pending, "t_order_original_created_at", None) is not None:
      is_finalization = bool(report.get("t_order_lifecycle_finalized"))
      if not t_order_lifecycle_pending(pending) and not is_finalization:
        # A delayed old-attempt ORDER cannot re-open or close the run again.
        return
      if not is_finalization:
        status_name = "SUBMITTED"
        metadata["t_order_attempt"] = int(pending.t_order_attempt or 0)
    order_status: OrderStatus | str = (
      status_name
      if status_name in _SPECIAL_RUNTIME_ORDER_STATUSES
      else OrderStatus[status_name]
    )
    order = OrderResponse(
      order_id=order_id,
      request=request,
      status=order_status,
      submit_time=_parse_report_time(
        report.get("order_time") or report.get("submit_time")
      ),
      # ``None`` preserves missing/invalid cumulative fill.  It must not be
      # confused with an explicit broker zero when the strategy/ExitPlanBook
      # decides whether a terminal order can release its pending intent.
      filled_volume=authoritative_cumulative_fill,
      filled_amount=traded_price * int(authoritative_cumulative_fill or 0),
      avg_price=traded_price,
      error_message=str(report.get("status_msg") or ""),
      last_update_time=_parse_report_time(
        report.get("updated_at") or report.get("order_time")
      ),
    )
    await executor.apply_durable_order_report(run_id, order)
    return

  if event.event_kind is not OwnerRuntimeEventKind.TRADE:
    raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
  price = float(
    report_number(
      ("traded_price", "price"),
      default=float(pending.limit_price or 0.0),
    )
  )
  volume = int(
    report_number(
      ("traded_volume", "volume"),
      default=0,
    )
  )
  trade = TradeRecord(
    trade_id=str(
      report.get("execution_id")
      or report.get("traded_id")
      or report.get("trade_id")
      or payload.get("business_key")
      or event.event_id
    ),
    order_id=order_id,
    instrument_code=instrument_code,
    trade_type=order_type,
    price=price,
    volume=volume,
    amount=price * volume,
    commission=0.0,
    trade_time=_parse_report_time(
      report.get("traded_time") or report.get("trade_time")
    ),
    metadata=metadata,
    execution_ref=event.execution_ref,
    environment=event.environment,
  )
  await executor.apply_durable_trade_report(run_id, trade)


async def _apply_runtime_event(event: StrategyRuntimeEvent) -> None:
  """Route a durable event through the explicit owner registry boundary."""

  from .strategy_manager import strategy_manager

  owner_identity = _owner_ref_environment(event)
  if owner_identity is None:
    raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT)
  execution_ref, environment = owner_identity
  try:
    event_kind = OwnerRuntimeEventKind(event.event_type)
  except (TypeError, ValueError) as exc:
    raise OwnerRuntimeRoutingError(OWNER_TARGET_CONFLICT) from exc
  event_payload = dict(event.payload or {})
  # ``StrategyRuntimeEvent.client_order_id`` is the durable causal identity.
  # Carry it as a direct event fact for every handler; never ask a handler to
  # discover an order from owner metadata.
  event_payload["client_order_id"] = str(event.client_order_id or "")
  event_payload["broker_order_id"] = str(event.broker_order_id or "")
  # The durable business key is another direct event fact.  It lets the
  # StrategyExecutor retain its audit/idempotency context without trusting a
  # JSON metadata copy that may have been supplied by the request producer.
  event_payload["business_key"] = str(getattr(event, "business_key", "") or "")
  runtime_event = OwnerRuntimeEvent(
    event_id=event.event_id,
    event_kind=event_kind,
    execution_ref=execution_ref,
    environment=environment,
    payload=event_payload,
  )

  async def route(db) -> None:
    registry = OwnerRuntimeRegistry()
    registry.register(
      ExecutionOwnerType.STRATEGY_RUN,
      _StrategyRunRuntimeHandler(strategy_manager.executor, db),
    )
    registry.register(
      ExecutionOwnerType.EXIT_PLAN,
      _ExitPlanRuntimeHandler(db),
    )
    registry.register(
      ExecutionOwnerType.MANUAL_COMMAND,
      _ManualCommandRuntimeHandler(db),
    )
    await OwnerRuntimeRouter(registry).route(runtime_event)

  db = _runtime_event_db.get()
  if db is None:
    async with AsyncSessionLocal() as owned_db:
      try:
        await route(owned_db)
        await owned_db.commit()
      except Exception:
        await owned_db.rollback()
        raise
    return
  await route(db)


async def _drain_runtime_events() -> None:
  async with _runtime_event_drain_lock:
    # The previous drain may have applied/checkpointed an event and then failed
    # to open the compensating session that returns it to PENDING. Reclaim such
    # rows on every active/idle pass rather than only after an Engine restart.
    await _recover_stuck_runtime_events(
      application_error="recovered before Engine runtime-event drain"
    )
    await _drain_runtime_events_locked()


async def _drain_runtime_events_locked() -> None:
  from .strategy_executor import RuntimeConsumerUnavailable
  from .strategy_manager import strategy_manager

  blocked_owner_keys: set[tuple[str, str, str]] = set()
  first_retry_error: Optional[RetryableReportError] = None
  while True:
    async with AsyncSessionLocal() as db:
      earlier_event = aliased(StrategyRuntimeEvent)
      unapplied_earlier_event = (
        select(earlier_event.event_id)
        .where(
          earlier_event.owner_type == StrategyRuntimeEvent.owner_type,
          earlier_event.owner_id == StrategyRuntimeEvent.owner_id,
          earlier_event.environment == StrategyRuntimeEvent.environment,
          earlier_event.application_status != "APPLIED",
          or_(
            earlier_event.created_at < StrategyRuntimeEvent.created_at,
            and_(
              earlier_event.created_at == StrategyRuntimeEvent.created_at,
              earlier_event.event_id < StrategyRuntimeEvent.event_id,
            ),
          ),
        )
        .exists()
      )
      statement = select(StrategyRuntimeEvent).where(
        StrategyRuntimeEvent.application_status == "PENDING",
        ~unapplied_earlier_event,
      )
      if blocked_owner_keys:
        statement = statement.where(
          and_(
            *(
              or_(
                StrategyRuntimeEvent.owner_type != owner_type,
                StrategyRuntimeEvent.owner_id != owner_id,
                StrategyRuntimeEvent.environment != environment,
              )
              for owner_type, owner_id, environment in blocked_owner_keys
            )
          )
        )
      event = (
        await db.execute(
          statement.order_by(
            StrategyRuntimeEvent.created_at,
            StrategyRuntimeEvent.event_id,
          )
          .limit(1)
          .with_for_update(skip_locked=True)
        )
      ).scalar_one_or_none()
      if event is None:
        if first_retry_error is not None:
          raise first_retry_error
        return
      owner_identity = _owner_ref_environment(event)
      event_owner_key = (
        (
          owner_identity[0].owner_type.value,
          owner_identity[0].owner_id,
          owner_identity[1].value,
        )
        if owner_identity is not None
        else (
          str(getattr(event, "owner_type", "") or ""),
          str(getattr(event, "owner_id", "") or ""),
          str(getattr(event, "environment", "") or ""),
        )
      )
      if (
        owner_identity is not None
        and owner_identity[0].owner_type == ExecutionOwnerType.STRATEGY_RUN
      ):
        require_consumer = getattr(
          strategy_manager.executor,
          "require_durable_event_consumer",
          None,
        )
        if require_consumer is not None:
          try:
            require_consumer(owner_identity[0].owner_id)
          except RuntimeConsumerUnavailable:
            blocked_owner_keys.add(event_owner_key)
            continue
      prior_attempts = int(event.application_attempts or 0)
      prior_error = event.application_error
      event.application_status = "PROCESSING"
      event.application_attempts = prior_attempts + 1
      await db.commit()
      event_id = event.event_id
    try:
      async with AsyncSessionLocal() as db:
        event = await db.get(StrategyRuntimeEvent, event_id)
        if event is None:
          continue
        arm_barrier = getattr(
          strategy_manager.executor,
          "arm_durable_event_barrier",
          None,
        )
        owner_identity = _owner_ref_environment(event)
        if (
          owner_identity is not None
          and owner_identity[0].owner_type == ExecutionOwnerType.STRATEGY_RUN
          and arm_barrier is not None
        ):
          arm_barrier(owner_identity[0].owner_id, event.business_key)
        runtime_db_token = _runtime_event_db.set(db)
        try:
          await _apply_runtime_event(event)
        finally:
          _runtime_event_db.reset(runtime_db_token)
        if (
          owner_identity is not None
          and owner_identity[0].owner_type == ExecutionOwnerType.STRATEGY_RUN
        ):
          await _reconcile_t_trade_batch_after_runtime_event(db, event)
        event.application_status = "APPLIED"
        event.applied_at = utcnow()
        event.application_error = None
        await db.commit()
        advance_barrier = getattr(
          strategy_manager.executor,
          "advance_durable_event_barrier",
          None,
        )
        if (
          advance_barrier is not None
          and owner_identity is not None
          and owner_identity[0].owner_type == ExecutionOwnerType.STRATEGY_RUN
        ):
          await advance_barrier(
            owner_identity[0].owner_id,
            event.business_key,
          )
    except RuntimeConsumerUnavailable:
      # Pause/stop/startup gaps are expected availability states, not failed
      # applications. Leave the event untouched for resume and keep draining
      # other runs without violating this run's event order.
      async with AsyncSessionLocal() as db:
        event = await db.get(StrategyRuntimeEvent, event_id)
        if event is not None:
          event.application_status = "PENDING"
          event.application_attempts = prior_attempts
          event.application_error = prior_error
          await db.commit()
      blocked_owner_keys.add(event_owner_key)
      continue
    except Exception as exc:
      async with AsyncSessionLocal() as db:
        event = await db.get(StrategyRuntimeEvent, event_id)
        if event is not None:
          event.application_status = "PENDING"
          event.application_error = str(exc)[:2000]
          event_metadata = dict(dict(event.payload or {}).get("metadata") or {})
          batch_id = str(
            event_metadata.get("t_batch_id") or event_metadata.get("batch_id") or ""
          )
          event_owner = _durable_owner_triple(event)
          if batch_id and event_owner is not None:
            batch = await db.get(TTradeBatch, batch_id)
            if (
              batch is not None
              and _durable_owner_triple(batch) == event_owner
              and batch.status != "CLOSED"
            ):
              batch.status = "RECONCILE_REQUIRED"
              batch.exception_reason = f"策略运行时事件应用失败：{str(exc)[:1000]}"
          await db.commit()
      blocked_owner_keys.add(event_owner_key)
      if first_retry_error is None:
        first_retry_error = RetryableReportError(str(exc))
        first_retry_error.__cause__ = exc
      continue


def _broker_order_ids(report: AgentReportInbox) -> list[int]:
  """Return the persisted orders touched by a successfully converged report."""
  payload = report.payload or {}
  values: list[Any] = []
  if report.message_type == "order_report":
    values.append(_body(payload, "order").get("order_id"))
    values.append(_body(payload, "order").get("broker_order_id"))
  elif report.message_type == "execution_report":
    values.append(_body(payload, "execution").get("order_id"))
    values.append(_body(payload, "execution").get("broker_order_id"))
  elif report.message_type == "delta_report":
    for item in [*(payload.get("orders") or []), *(payload.get("trades") or [])]:
      values.extend([item.get("order_id"), item.get("broker_order_id")])

  order_ids: list[int] = []
  for value in values:
    if value is None:
      continue
    try:
      order_id = int(value)
    except (TypeError, ValueError):
      continue
    if order_id not in order_ids:
      order_ids.append(order_id)
  return order_ids


async def _supersede_obsolete_pending_full_snapshots(
  db,
  oldest: AgentReportInbox,
) -> int:
  """Coalesce queued full snapshots while preserving interleaved deltas.

  A newer structurally verified full snapshot subsumes older full snapshots
  from the same personal-account Agent. Only full snapshots are superseded;
  order, execution, and position deltas retain FIFO processing before the
  newer snapshot is claimed.
  """

  payload = dict(oldest.payload or {})
  if (
    oldest.message_type != "delta_report"
    or str(oldest.protocol_version or "") != PROTOCOL_VERSION
    or payload.get("is_complete") is not True
    or _authoritative_snapshot_identity(oldest) is None
  ):
    return 0

  newer = await db.scalar(
    select(AgentReportInbox)
    .where(
      AgentReportInbox.device_id == oldest.device_id,
      AgentReportInbox.message_type == "delta_report",
      AgentReportInbox.protocol_version == PROTOCOL_VERSION,
      AgentReportInbox.processing_status == "PENDING",
      AgentReportInbox.received_at > oldest.received_at,
      or_(
        AgentReportInbox.next_attempt_at.is_(None),
        AgentReportInbox.next_attempt_at <= utcnow(),
      ),
      AgentReportInbox.payload["is_complete"].as_boolean().is_(True),
    )
    .order_by(AgentReportInbox.received_at.desc())
    .limit(1)
  )
  if newer is None or _authoritative_snapshot_identity(newer) is None:
    return 0

  superseded_at = utcnow()
  result = await db.execute(
    update(AgentReportInbox)
    .where(
      AgentReportInbox.device_id == oldest.device_id,
      AgentReportInbox.message_type == "delta_report",
      AgentReportInbox.protocol_version == PROTOCOL_VERSION,
      AgentReportInbox.processing_status == "PENDING",
      AgentReportInbox.received_at < newer.received_at,
      AgentReportInbox.payload["is_complete"].as_boolean().is_(True),
    )
    .values(
      processing_status="SUPERSEDED",
      processing_error="superseded by newer verified complete snapshot",
      processed_at=superseded_at,
      next_attempt_at=None,
    )
  )
  superseded_count = int(result.rowcount or 0)
  if superseded_count:
    await db.commit()
    logger.info(
      "Coalesced obsolete Agent full snapshots: device_id=%s count=%s "
      "newest_message_id=%s",
      oldest.device_id,
      superseded_count,
      newer.message_id,
    )
  return superseded_count


def _report_client_order_ids(payload: Mapping[str, Any]) -> set[str]:
  """Collect bounded client ids for legacy quarantine bookkeeping only."""

  client_order_ids: set[str] = set()

  def add(value: Any) -> None:
    normalized = str(value or "").strip()
    if normalized and len(normalized) <= 128:
      client_order_ids.add(normalized)

  add(payload.get("client_order_id"))
  for nested_name in ("order", "execution"):
    nested = payload.get(nested_name)
    if isinstance(nested, Mapping):
      add(nested.get("client_order_id"))
  for collection_name in (
    "orders",
    "trades",
    "order_errors",
    "cancel_errors",
  ):
    values = payload.get(collection_name)
    if not isinstance(values, list):
      continue
    for item in values:
      if isinstance(item, Mapping):
        add(item.get("client_order_id"))
  return client_order_ids


async def _quarantine_unsupported_protocol_report(
  db: Any,
  report: AgentReportInbox,
) -> None:
  """Dead-letter one legacy report and seal its durable execution scope."""

  protocol_version = str(report.protocol_version or "")
  error = (
    f"{PROTOCOL_1_2_REQUIRED}: unsupported Agent report protocol "
    f"{protocol_version or 'MISSING'}"
  )
  quarantined_at = utcnow()
  report.processing_status = "FAILED"
  report.processing_attempts = max(1, int(report.processing_attempts or 0))
  report.processing_error = error
  report.processed_at = quarantined_at
  report.next_attempt_at = None

  payload = dict(report.payload or {})
  try:
    account_ids = {
      account_id
      for account_id in _report_account_ids(payload)
      if len(account_id) <= 50
    }
  except (RetryableReportError, TypeError, ValueError):
    account_ids = set()

  for client_order_id in _report_client_order_ids(payload):
    pending = await db.get(
      PendingTradeOrder,
      client_order_id,
      with_for_update=True,
      populate_existing=True,
    )
    if pending is None:
      continue
    pending_account_id = str(pending.account_id or "").strip()
    if pending_account_id and len(pending_account_id) <= 50:
      account_ids.add(pending_account_id)
    pending_metadata = dict(pending.request_metadata or {})
    pending_metadata.update(
      {
        QUARANTINE_RECONCILE_REQUIRED_METADATA_KEY: True,
        QUARANTINE_REPAIR_REQUIRED_METADATA_KEY: True,
        QUARANTINE_REASON_METADATA_KEY: PROTOCOL_1_2_REQUIRED,
      }
    )
    pending.request_metadata = pending_metadata
    pending.status = "RECONCILE_REQUIRED"
    pending.status_reason = error[:256]
    outbox = (
      await db.execute(
        select(TradeCommandOutbox)
        .where(TradeCommandOutbox.client_order_id == client_order_id)
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
      )
    ).scalar_one_or_none()
    if outbox is not None:
      outbox.delivery_status = "RECONCILE_REQUIRED"
      outbox.last_error = error[:256]

  for account_id in sorted(account_ids):
    control = await db.get(
      AccountExecutionControl,
      account_id,
      with_for_update=True,
      populate_existing=True,
    )
    if control is None:
      control = AccountExecutionControl(account_id=account_id)
      db.add(control)
      await db.flush()
    previous_state = str(control.authorization_state or "DISABLED")
    if previous_state != "KILLED":
      control.authorization_state = "PAUSED"
    control.reconcile_status = "RECONCILE_REQUIRED"
    control.state_version = max(1, int(control.state_version or 1)) + 1
    control.paused_reason = json.dumps(
      [
        {
          "kind": PROTOCOL_1_2_REQUIRED,
          "messageId": str(report.message_id or ""),
          "protocolVersion": protocol_version or None,
          "quarantinedAt": quarantined_at.isoformat(),
        }
      ],
      ensure_ascii=False,
      separators=(",", ":"),
    )[:2000]
    event_id = f"protocol-rejected:{report.message_id}:{account_id}"[:128]
    existing_event = await db.get(AccountExecutionControlEvent, event_id)
    if existing_event is None:
      db.add(
        AccountExecutionControlEvent(
          event_id=event_id,
          account_id=account_id,
          event_type=PROTOCOL_1_2_REQUIRED,
          previous_state=previous_state,
          next_state=str(control.authorization_state or ""),
          details={
            "messageId": str(report.message_id or ""),
            "messageType": str(report.message_type or ""),
            "protocolVersion": protocol_version or None,
          },
          created_at=quarantined_at,
        )
      )

  for account_id in sorted(account_ids) or [None]:
    await OperationalAlertService(db).raise_alert(
      severity="SEV2",
      source="ENGINE",
      code="AGENT_REPORT_DEAD_LETTER",
      account_id=account_id,
      business_id=str(report.message_id or "") or None,
      message=(
        f"Agent report protocol rejected: {report.message_type} / "
        f"{protocol_version or 'MISSING'}"
      ),
      details={
        "message_id": str(report.message_id or ""),
        "message_type": str(report.message_type or ""),
        "protocol_version": protocol_version or None,
        "reason_code": PROTOCOL_1_2_REQUIRED,
      },
      commit=False,
    )
  await db.commit()


async def _claim() -> Optional[str]:
  now = utcnow()
  async with AsyncSessionLocal() as db:
    while True:
      legacy_result = await db.execute(
        select(AgentReportInbox)
        .where(
          AgentReportInbox.processing_status == "PENDING",
          or_(
            AgentReportInbox.protocol_version != PROTOCOL_VERSION,
            AgentReportInbox.protocol_version.is_(None),
          ),
        )
        .order_by(AgentReportInbox.received_at)
        .limit(1)
        .with_for_update(skip_locked=True)
      )
      legacy_report = legacy_result.scalar_one_or_none()
      if legacy_report is not None:
        await _quarantine_unsupported_protocol_report(db, legacy_report)
        now = utcnow()
        continue
      result = await db.execute(
        select(AgentReportInbox)
        .where(
          AgentReportInbox.processing_status == "PENDING",
          AgentReportInbox.protocol_version == PROTOCOL_VERSION,
          or_(
            AgentReportInbox.next_attempt_at.is_(None),
            AgentReportInbox.next_attempt_at <= now,
          ),
        )
        .order_by(AgentReportInbox.received_at)
        .limit(1)
        .with_for_update(skip_locked=True)
      )
      report = result.scalar_one_or_none()
      if report is None:
        return None
      if await _supersede_obsolete_pending_full_snapshots(db, report):
        now = utcnow()
        continue
      report.processing_status = "PROCESSING"
      report.processing_attempts = (report.processing_attempts or 0) + 1
      await db.commit()
      return report.message_id


async def _recover_stuck_reports() -> None:
  """Return all interrupted claims after this Engine acquires its singleton lease."""
  async with AsyncSessionLocal() as db:
    await db.execute(
      update(AgentReportInbox)
      .where(AgentReportInbox.processing_status == "PROCESSING")
      .values(
        processing_status="PENDING",
        next_attempt_at=utcnow(),
        processing_error="recovered after Engine restart",
      )
    )
    await db.commit()


async def _recover_stuck_runtime_events(
  *,
  application_error: str = "recovered after Engine restart",
) -> None:
  async with AsyncSessionLocal() as db:
    await db.execute(
      update(StrategyRuntimeEvent)
      .where(StrategyRuntimeEvent.application_status == "PROCESSING")
      .values(
        application_status="PENDING",
        application_error=application_error,
      )
    )
    await db.commit()


def _obsolete_unavailable_snapshot(
  payload: dict[str, Any],
  *,
  covered_accounts: set[str],
  current_payload: dict[str, Any],
) -> bool:
  """Recognize an older failed status observation with no broker facts."""

  fact_fields = {
    "accounts",
    "positions_by_account",
    "positions",
    "position_deltas",
    "orders",
    "trades",
    "order_errors",
    "cancel_errors",
  }
  observation_fields = {
    "account_id",
    "sequence",
    "source_sequence",
    "source_event_at",
    "snapshot_id",
    "snapshot_hash",
    "report_id",
    "is_complete",
    "mode",
    "unavailable_accounts",
    "section_completeness_by_account",
    "snapshot_authority_by_account",
    AGENT_SERVER_SESSION_PAYLOAD_KEY,
  }
  if (
    payload.get("is_complete") is not False
    or not set(payload).issubset(fact_fields | observation_fields)
    or any(payload.get(key) not in (None, [], {}) for key in fact_fields)
  ):
    return False
  authorities = payload.get("snapshot_authority_by_account")
  sections = payload.get("section_completeness_by_account")
  unavailable = payload.get("unavailable_accounts")
  if (
    not isinstance(authorities, dict)
    or not isinstance(sections, dict)
    or not isinstance(unavailable, list)
    or any(not isinstance(value, str) for value in unavailable)
    or set(unavailable) != covered_accounts
    or set(authorities) != covered_accounts
    or set(sections) != covered_accounts
  ):
    return False
  for account_id in covered_accounts:
    authority = authorities[account_id]
    account_sections = sections[account_id]
    if (
      not isinstance(authority, dict)
      or authority.get("snapshot_eligible") is not False
      or not isinstance(account_sections, dict)
      or any(
        account_sections.get(section) is not False
        for section in _REQUIRED_SNAPSHOT_SECTIONS
      )
    ):
      return False
  if not payload.get("source_event_at") or not current_payload.get("source_event_at"):
    return False
  try:
    failed_at = to_naive_utc(
      _parse_authoritative_snapshot_time(payload["source_event_at"])
    )
    current_at = to_naive_utc(
      _parse_authoritative_snapshot_time(current_payload["source_event_at"])
    )
    return (
      failed_at < current_at <= utcnow()
      and _parse_authoritative_snapshot_sequence(payload)
      < _parse_authoritative_snapshot_sequence(current_payload)
    )
  except (TypeError, ValueError, OverflowError, OSError):
    return False


async def _supersede_prior_snapshot_failures(
  db,
  report: AgentReportInbox,
  *,
  resolved_at: datetime,
) -> int:
  """Close obsolete snapshot dead letters after newer state converges.

  Complete protocol 1.2 snapshots are authoritative account-state checkpoints.
  Once a newer checkpoint for the same device and accounts succeeds, an older
  failed complete snapshot or fact-free unavailable observation no longer
  represents an unresolved state gap. Partial broker facts and incremental
  reports are never discarded. Original reports and errors remain in audit.
  """
  payload = dict(report.payload or {})
  current_accounts = _report_account_ids(payload)
  if (
    report.message_type != "delta_report"
    or str(report.protocol_version or "") != PROTOCOL_VERSION
    or not bool(payload.get("is_complete"))
    or _authoritative_snapshot_identity(report) is None
    or not current_accounts
  ):
    return 0
  failures = list(
    (
      await db.execute(
        select(AgentReportInbox).where(
          AgentReportInbox.device_id == report.device_id,
          AgentReportInbox.message_id != report.message_id,
          AgentReportInbox.message_type == "delta_report",
          AgentReportInbox.protocol_version == PROTOCOL_VERSION,
          AgentReportInbox.processing_status == "FAILED",
          AgentReportInbox.received_at <= report.received_at,
        )
      )
    )
    .scalars()
    .all()
  )
  superseded = []
  for item in failures:
    failed_payload = dict(item.payload or {})
    try:
      covered_accounts = _report_account_ids(failed_payload)
    except RetryableReportError:
      continue
    if not covered_accounts or not covered_accounts.issubset(current_accounts):
      continue
    if failed_payload.get("is_complete") is True or _obsolete_unavailable_snapshot(
      failed_payload,
      covered_accounts=covered_accounts,
      current_payload=payload,
    ):
      superseded.append(item)
  if not superseded:
    return 0
  message_ids = [item.message_id for item in superseded]
  for item in superseded:
    item.processing_status = "SUPERSEDED"
    item.processed_at = resolved_at
    item.next_attempt_at = None
  await db.execute(
    update(OperationalAlert)
    .where(
      OperationalAlert.code == "AGENT_REPORT_DEAD_LETTER",
      OperationalAlert.business_id.in_(message_ids),
      OperationalAlert.status != "RESOLVED",
    )
    .values(
      status="RESOLVED",
      resolved_by="SYSTEM_RECONCILIATION",
      resolved_at=resolved_at,
      resolution=(
        "后续协议 1.2 完整账户快照已成功收敛；旧失败快照或无交易事实的不可用观测"
        f"已由权威状态取代，原始失败记录保留。快照：{payload['snapshot_id']}"
      ),
    )
  )
  return len(superseded)


async def _finish(
  message_id: str,
  *,
  error: Optional[Exception] = None,
) -> None:
  async with AsyncSessionLocal() as db:
    report = await db.get(AgentReportInbox, message_id)
    if report is None:
      return
    if error is None:
      finished_at = utcnow()
      report.processing_status = "PROCESSED"
      report.processed_at = finished_at
      report.processing_error = None
      report.next_attempt_at = None
      superseded_count = await _supersede_prior_snapshot_failures(
        db,
        report,
        resolved_at=finished_at,
      )
      if superseded_count:
        logger.info(
          "Authoritative Agent snapshot superseded %s prior dead letters",
          superseded_count,
        )
    else:
      attempts = int(report.processing_attempts or 1)
      report.processing_error = _report_error_text(error)
      retryable = isinstance(error, RetryableReportError) or _retryable_database_error(
        error
      )
      if attempts >= 10 or not retryable:
        report.processing_status = "FAILED"
        account_ids = sorted(_report_account_ids(dict(report.payload or {})))
        for account_id in account_ids or [None]:
          await OperationalAlertService(db).raise_alert(
            severity="SEV2",
            source="ENGINE",
            code="AGENT_REPORT_DEAD_LETTER",
            account_id=account_id,
            business_id=report.message_id,
            message=(
              f"Agent report 永久失败：{report.message_type} / "
              f"{error.__class__.__name__}"
            ),
            details={
              "message_id": report.message_id,
              "message_type": report.message_type,
              "protocol_version": report.protocol_version,
              "payload_hash": report.raw_payload_hash,
              "attempts": attempts,
              "error_class": error.__class__.__name__,
              "error": _report_error_text(error),
            },
            commit=False,
          )
      else:
        report.processing_status = "PENDING"
        report.next_attempt_at = utcnow() + timedelta(seconds=min(60, 2**attempts))
    await db.commit()


async def _wait_for_database_retry(
  stopped: asyncio.Event,
  *,
  delay: float,
) -> bool:
  """Return true when shutdown was requested while waiting to retry."""

  try:
    await asyncio.wait_for(stopped.wait(), timeout=delay)
  except asyncio.TimeoutError:
    return False
  return True


async def _recover_consumer_state(stopped: asyncio.Event) -> bool:
  """Recover interrupted inbox work without exiting on pool contention."""

  delay = _DATABASE_CONTENTION_RETRY_SECONDS
  while not stopped.is_set():
    try:
      await _recover_stuck_reports()
      await _recover_stuck_runtime_events()
      return True
    except (SQLAlchemyTimeoutError, DBAPIError) as exc:
      if not _retryable_database_error(exc):
        raise
      logger.warning(
        "Engine report recovery deferred by database contention: "
        "retry_in=%.2fs error=%s",
        delay,
        _report_error_text(exc),
      )
      if await _wait_for_database_retry(stopped, delay=delay):
        return False
      delay = min(delay * 2, _DATABASE_CONTENTION_MAX_RETRY_SECONDS)
  return False


async def _finish_with_database_retry(
  stopped: asyncio.Event,
  message_id: str,
  *,
  error: Optional[Exception] = None,
) -> bool:
  """Persist report completion without crashing on a busy shared pool."""

  delay = _DATABASE_CONTENTION_RETRY_SECONDS
  while not stopped.is_set():
    try:
      await _finish(message_id, error=error)
      return True
    except (SQLAlchemyTimeoutError, DBAPIError) as exc:
      if not _retryable_database_error(exc):
        raise
      logger.warning(
        "Engine report completion deferred by database contention: "
        "message_id=%s retry_in=%.2fs error=%s",
        message_id,
        delay,
        _report_error_text(exc),
      )
      if await _wait_for_database_retry(stopped, delay=delay):
        return False
      delay = min(delay * 2, _DATABASE_CONTENTION_MAX_RETRY_SECONDS)
  return False


async def _open_wakeup_subscription() -> Optional[RedisChannelSubscription]:
  try:
    return await redis_pubsub.open_subscription(AGENT_REPORT_WAKE_CHANNEL)
  except Exception as exc:
    logger.debug(
      "Agent report Redis wake-up unavailable; using database polling: %s",
      exc.__class__.__name__,
    )
    return None


async def _wait_for_work(
  stopped: asyncio.Event,
  subscription: Optional[RedisChannelSubscription],
) -> Optional[RedisChannelSubscription]:
  if subscription is None:
    try:
      await asyncio.wait_for(stopped.wait(), timeout=1.0)
    except asyncio.TimeoutError:
      pass
    return await _open_wakeup_subscription()
  try:
    await subscription.wait_for_message(timeout=1.0)
    return subscription
  except Exception as exc:
    logger.debug(
      "Agent report Redis wake-up interrupted; using database polling: %s",
      exc.__class__.__name__,
    )
    try:
      await subscription.close()
    except Exception:
      pass
    return None


async def _refresh_runtime_event_barriers() -> None:
  from .strategy_manager import strategy_manager

  refresh = getattr(
    strategy_manager.executor,
    "refresh_armed_durable_event_barriers",
    None,
  )
  if refresh is not None:
    await refresh()


async def run_report_consumer(stopped: asyncio.Event) -> None:
  if not await _recover_consumer_state(stopped):
    return
  subscription = await _open_wakeup_subscription()
  retry_delay = _DATABASE_CONTENTION_RETRY_SECONDS
  try:
    while not stopped.is_set():
      try:
        message_id = await _claim()
      except (SQLAlchemyTimeoutError, DBAPIError) as exc:
        if not _retryable_database_error(exc):
          raise
        logger.warning(
          "Engine report claim deferred by database contention: "
          "retry_in=%.2fs error=%s",
          retry_delay,
          _report_error_text(exc),
        )
        if await _wait_for_database_retry(stopped, delay=retry_delay):
          return
        retry_delay = min(
          retry_delay * 2,
          _DATABASE_CONTENTION_MAX_RETRY_SECONDS,
        )
        continue
      retry_delay = _DATABASE_CONTENTION_RETRY_SECONDS
      if message_id is None:
        try:
          await _refresh_runtime_event_barriers()
          await _drain_runtime_events()
        except Exception as exc:
          logger.debug(
            "Durable runtime barrier refresh/drain deferred: %s",
            exc,
          )
          pass
        subscription = await _wait_for_work(stopped, subscription)
        continue
      try:
        async with AsyncSessionLocal() as db:
          report = await db.get(AgentReportInbox, message_id)
          if report is None:
            continue
          db.expunge(report)
        # Do not pin an inbox read transaction while projections perform their
        # own database work. The detached row contains only eagerly loaded
        # scalar columns and is immutable for this processing attempt.
        await _process(report)
        await _stage_runtime_events(report)
        await _drain_runtime_events()
        events = [
          {
            "message_id": report.message_id,
            "message_type": report.message_type,
            "client_order_id": report.client_order_id,
            "broker_order_id": order_id,
          }
          for order_id in _broker_order_ids(report)
        ]
        if not await _finish_with_database_retry(stopped, message_id):
          return
        for event in events:
          try:
            await redis_pubsub.publish(TRADING_EVENT_CHANNEL, event)
          except Exception as exc:
            logger.debug("Redis wake-up failed: %s", exc.__class__.__name__)
      except Exception as exc:
        error = (
          RetryableReportError(
            "database contention while applying Agent report: "
            + _report_error_text(exc)
          )
          if _retryable_database_error(exc)
          else exc
        )
        logger.warning(
          "Agent report processing failed: message_id=%s error=%s",
          message_id,
          _report_error_text(error),
        )
        if not await _finish_with_database_retry(
          stopped,
          message_id,
          error=error,
        ):
          return
  finally:
    if subscription is not None:
      try:
        await subscription.close()
      except Exception:
        pass
