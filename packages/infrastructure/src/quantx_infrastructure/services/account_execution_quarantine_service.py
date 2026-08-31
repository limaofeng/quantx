"""Account-wide quarantine after broker evidence contradicts a released exit.

The exact exit-plan invalidation owns the plan and intent mutation.  This
service owns the separate account/outbox boundary that must commit in the same
transaction: close the account execution window, stop a never-delivered
replacement SELL locally, or preserve delivery evidence and enqueue one exact
broker cancellation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from quantx_contracts import snapshot_account_authority_is_authoritative
from quantx_domain.clock import to_naive_utc, utcnow
from quantx_domain.trading.exit_plan import (
  ExitPlan,
  ExitPlanBook,
  ExitPlanStatus,
  is_sticky_exit_plan_error,
)
from sqlalchemy import select

from quantx_infrastructure.models.agent_runtime import (
  AGENT_REPORT_SNAPSHOT_ID,
  AccountExecutionControl,
  AccountExecutionControlEvent,
  AgentReportInbox,
  PendingTradeOrder,
  StrategyOrderCorrelation,
  TradeCommandOutbox,
)
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.agent_session_guard import (
  AGENT_SERVER_SESSION_PAYLOAD_KEY,
)
from quantx_infrastructure.services.exit_plan_authorization_service import (
  clear_exact_auto_exit_authorization,
)
from quantx_infrastructure.services.exit_plan_execution_owner import (
  INVALID_OWNER,
  MANAGED_EXIT_STRATEGY_OWNER,
  MONITOR_OWNER,
  RUNTIME_BOOK_OWNER,
  durable_exit_plan_owner_kind,
)

BROKER_EXECUTION_AFTER_RELEASE = "BROKER_EXECUTION_AFTER_RELEASE"
QUARANTINE_CANCEL_REQUIRED_METADATA_KEY = (
  "account_execution_quarantine_cancel_required"
)
QUARANTINE_RECONCILE_REQUIRED_METADATA_KEY = (
  "account_execution_quarantine_reconcile_required"
)
QUARANTINE_REASON_METADATA_KEY = "account_execution_quarantine_reason"
QUARANTINE_REPAIR_REQUIRED_METADATA_KEY = (
  "account_execution_quarantine_repair_required"
)
_QUARANTINE_EVENT_PREFIX = "broker-release-quarantine:"
_QUARANTINE_CANCEL_REASON = "account quarantined after broker execution"
_LOCAL_OUTBOX_CANCEL_SOURCE = "LOCAL_OUTBOX_CANCEL"
_LOCAL_OUTBOX_CANCEL_REASON = "EXIT_PLAN_QUARANTINE_CANCELLED_BEFORE_AGENT_DELIVERY"
QUARANTINED_ORDER_REPAIRED = "QUARANTINED_ORDER_REPAIRED"
LIVE_PLACE_PHYSICAL_GATE_REJECTED = "LIVE_PLACE_PHYSICAL_GATE_REJECTED"
_REPAIRABLE_QUARANTINE_REASONS = frozenset(
  {
    "BINDING_MISMATCH",
    "ACCOUNT_WIDE_STALE_SELL",
    BROKER_EXECUTION_AFTER_RELEASE,
    "PLACE_ORDER_BINDING_MISSING",
    "PLAN_PENDING_RELEASE_FAILED",
    "PHYSICAL_DELIVERY_GATE_REJECTED",
  }
)
_BROKER_TERMINAL_STATUSES = frozenset(
  {"FILLED", "CANCELLED", "CANCELED", "REJECTED", "EXPIRED"}
)
_OUTBOX_TERMINAL_STATUSES = frozenset(
  {"CANCELLED", "CANCELLED_KILL", "EXPIRED", "RECONCILED_TERMINAL"}
)
_PENDING_TERMINAL_STATUSES = frozenset(
  {
    "FILLED",
    "CANCELLED",
    "CANCELED",
    "REJECTED",
    "EXPIRED",
    "RECONCILED_ZERO_FILL",
  }
)
_REPAIR_ZERO_FILL_SOURCE = "EXPLICIT_QUARANTINE_REPAIR_ZERO_FILL"


@dataclass(frozen=True)
class AccountExecutionQuarantineResult:
  applied: bool
  event_id: str
  account_id: str
  plan_id: str
  current_pending_intent_id: str = ""
  released_zero_fill_intent_id: str = ""
  locally_cancelled_client_order_ids: tuple[str, ...] = ()
  reconcile_required_client_order_ids: tuple[str, ...] = ()
  cancel_message_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class TradeCommandDeliveryLock:
  """Result of the account-first lock used immediately before dispatch."""

  command: TradeCommandOutbox | None
  blocked_reason: str = ""
  commit_required: bool = False


@dataclass(frozen=True)
class QuarantinedOrderRepairResult:
  applied: bool
  event_id: str
  account_id: str
  client_order_id: str
  plan_id: str
  intent_id: str
  snapshot_id: str
  broker_terminal_status: str
  cumulative_filled_volume: int


def _event_id(*, account_id: str, plan_id: str, evidence_key: str) -> str:
  digest = hashlib.sha256(
    f"{account_id}\0{plan_id}\0{evidence_key}".encode("utf-8")
  ).hexdigest()
  return f"{_QUARANTINE_EVENT_PREFIX}{digest}"


def _is_exact_current_plan_order(
  pending: PendingTradeOrder,
  *,
  plan: AutoExitPlanRecord,
  current_pending_intent_id: str,
) -> bool:
  metadata = dict(pending.request_metadata or {})
  return bool(
    str(pending.intent_id or "") == current_pending_intent_id
    and str(pending.account_id or "") == str(plan.account_id or "")
    and str(pending.instrument_code or "").strip().upper()
    == str(plan.instrument_code or "").strip().upper()
    and str(pending.side or "").strip().upper() == "SELL"
    and str(pending.execution_mode or "").strip().lower() == "live"
    and str(metadata.get("owner_type") or "").strip().upper() == "EXIT_PLAN"
    and str(metadata.get("owner_id") or "").strip() == str(plan.plan_id or "")
    and str(metadata.get("exit_plan_id") or "").strip() == str(plan.plan_id or "")
  )


def _is_exact_place_order(
  outbox: TradeCommandOutbox,
  *,
  pending: PendingTradeOrder,
) -> bool:
  payload = dict(outbox.payload or {})
  metadata = dict(payload.get("request_metadata") or {})
  pending_metadata = dict(pending.request_metadata or {})
  return bool(
    str(outbox.client_order_id or "") == str(pending.client_order_id or "")
    and str(outbox.account_id or "") == str(pending.account_id or "")
    and str(payload.get("command_kind") or "").strip().upper() == "PLACE_ORDER"
    and str(payload.get("execution_mode") or "").strip().lower() == "live"
    and str(payload.get("account_id") or "") == str(pending.account_id or "")
    and str(payload.get("intent_id") or "") == str(pending.intent_id or "")
    and str(payload.get("side") or "").strip().upper() == "SELL"
    and str(metadata.get("owner_type") or "").strip().upper() == "EXIT_PLAN"
    and str(metadata.get("owner_id") or "").strip()
    == str(pending_metadata.get("owner_id") or "").strip()
    and str(metadata.get("exit_plan_id") or "").strip()
    == str(pending_metadata.get("exit_plan_id") or "").strip()
  )


def _is_live_place_sell(outbox: TradeCommandOutbox) -> bool:
  payload = dict(outbox.payload or {})
  return bool(
    str(payload.get("command_kind") or "").strip().upper() == "PLACE_ORDER"
    and str(payload.get("execution_mode") or "").strip().lower() == "live"
    and str(payload.get("side") or "").strip().upper() == "SELL"
    and str(outbox.delivery_status or "").strip().upper()
    not in _OUTBOX_TERMINAL_STATUSES
  )


def _pending_owner(pending: PendingTradeOrder) -> tuple[str, str]:
  metadata = dict(pending.request_metadata or {})
  return (
    str(metadata.get("exit_plan_id") or metadata.get("owner_id") or "").strip(),
    str(pending.intent_id or "").strip(),
  )


def _never_delivered(
  outbox: TradeCommandOutbox,
  pending: PendingTradeOrder,
) -> bool:
  return bool(
    str(outbox.delivery_status or "").strip().upper() == "QUEUED"
    and outbox.delivered_at is None
    and outbox.acknowledged_at is None
    and int(outbox.attempts or 0) == 0
    and str(pending.status or "").strip().upper() in {"QUEUED", "PENDING"}
    and not str(pending.broker_order_id or "").strip()
    and int(pending.last_source_sequence or 0) == 0
    and pending.last_source_event_at is None
  )


def _clear_controlled_window(control: AccountExecutionControl) -> None:
  control.controlled_window_active = False
  control.controlled_window_snapshot_id = None
  control.controlled_window_snapshot_hash = None
  control.controlled_window_started_at = None
  control.controlled_window_started_by_user_id = None
  control.controlled_window_external_order_ids = []
  control.controlled_window_external_trade_ids = []


def _normalized_broker_status(value: Any) -> str:
  names = {
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
  try:
    return names[int(value)]
  except (TypeError, ValueError, KeyError):
    normalized = str(value or "").strip().upper()
    return {
      "REPORTED": "SUBMITTED",
      "ACCEPTED": "SUBMITTED",
      "WORKING": "SUBMITTED",
      "PARTIAL": "PARTIAL_FILLED",
      "PARTIALLY_FILLED": "PARTIAL_FILLED",
      "CANCELED": "CANCELLED",
    }.get(normalized, normalized or "PENDING")


def _snapshot_hash(payload: Mapping[str, Any]) -> str:
  canonical = {
    key: value
    for key, value in dict(payload or {}).items()
    if key not in {"snapshot_hash", AGENT_SERVER_SESSION_PAYLOAD_KEY}
  }
  return hashlib.sha256(
    json.dumps(
      canonical,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode("utf-8")
  ).hexdigest()


def _snapshot_sequence(payload: Mapping[str, Any]) -> int:
  try:
    return max(0, int(payload.get("source_sequence") or payload.get("sequence") or 0))
  except (TypeError, ValueError, OverflowError):
    return 0


def _quarantine_disposition_reason(
  event: AccountExecutionControlEvent,
  *,
  client_order_id: str,
) -> str:
  details = dict(event.details or {})
  for raw in list(details.get("commandDispositions") or []):
    item = dict(raw or {})
    if str(item.get("clientOrderId") or "") == client_order_id:
      if item.get("repairedAt"):
        return ""
      reason = str(
        item.get("repairReason") or item.get("disposition") or ""
      ).strip().upper()
      return reason if reason in _REPAIRABLE_QUARANTINE_REASONS else ""
  return ""


def _result_from_event(
  event: AccountExecutionControlEvent,
) -> AccountExecutionQuarantineResult:
  details = dict(event.details or {})
  return AccountExecutionQuarantineResult(
    applied=False,
    event_id=str(event.event_id),
    account_id=str(event.account_id),
    plan_id=str(details.get("triggerPlanId") or details.get("planId") or ""),
    current_pending_intent_id=str(
      details.get("triggerIntentId")
      or details.get("currentPendingIntentId")
      or ""
    ),
    released_zero_fill_intent_id=str(details.get("releasedZeroFillIntentId") or ""),
    locally_cancelled_client_order_ids=tuple(
      str(value) for value in details.get("locallyCancelledClientOrderIds") or []
    ),
    reconcile_required_client_order_ids=tuple(
      str(value) for value in details.get("reconcileRequiredClientOrderIds") or []
    ),
    cancel_message_ids=tuple(
      str(value) for value in details.get("cancelMessageIds") or []
    ),
  )


def _release_exact_locally_cancelled_pending(
  record: AutoExitPlanRecord,
  *,
  intent_id: str,
  client_order_id: str,
) -> bool:
  """Release one proven-local pending order without changing its sticky error.

  The invalidation transaction already owns the plan row.  This projection is
  intentionally confined to that row: locking the replacement TradeIntent
  here would invert the enqueue path's current-intent -> plan order.
  """

  try:
    domain_plan = ExitPlan.from_dict(dict(record.plan_state or {}))
  except (KeyError, TypeError, ValueError):
    return False
  sticky_status = str(record.status or "").strip().upper()
  sticky_error = str(record.last_error or "")
  sticky_state_version = record.state_version
  if not (
    domain_plan.plan_id == str(record.plan_id or "")
    and str(domain_plan.template.account_id or "") == str(record.account_id or "")
    and str(domain_plan.template.instrument_code or "").strip().upper()
    == str(record.instrument_code or "").strip().upper()
    and str(domain_plan.pending_intent_id or "") == str(intent_id or "")
    and (
      not str(domain_plan.pending_order_id or "")
      or str(domain_plan.pending_order_id or "") == str(client_order_id or "")
    )
    and domain_plan.status == ExitPlanStatus.ERROR
    and str(domain_plan.error_message or "") == sticky_error
    and sticky_status == ExitPlanStatus.ERROR.value
    and not bool(record.enabled)
  ):
    return False

  released = ExitPlanBook([domain_plan]).apply_order_event(
    plan_id=domain_plan.plan_id,
    intent_id=str(intent_id or ""),
    status="RECONCILED_ZERO_FILL",
  )
  if (
    released is None
    or str(released.pending_intent_id or "")
    or str(released.pending_order_id or "")
    or released.status != ExitPlanStatus.ERROR
    or str(released.error_message or "") != sticky_error
  ):
    return False

  record.plan_state = released.to_dict()
  record.pending_client_order_id = None
  record.status = sticky_status
  record.enabled = False
  record.last_error = sticky_error
  # The caller already advanced the version for this atomic invalidation.  Do
  # not manufacture a second revision while finishing that same transaction.
  record.state_version = sticky_state_version
  return True


def _release_account_wide_locally_cancelled_pending(
  record: AutoExitPlanRecord,
  *,
  pending: PendingTradeOrder,
  intent: TradeIntentRecord,
  correlation: StrategyOrderCorrelation | None,
) -> bool:
  """Release another plan's exact command proven not to have left the API."""

  plan_id, intent_id = _pending_owner(pending)
  if not plan_id or not intent_id:
    return False
  try:
    domain_plan = ExitPlan.from_dict(dict(record.plan_state or {}))
  except (KeyError, TypeError, ValueError):
    return False
  metadata = dict(pending.request_metadata or {})
  intent_metadata = dict(intent.intent_metadata or {})
  owner_kind = durable_exit_plan_owner_kind(record)
  record_run_id = str(record.strategy_run_id or "")
  pending_run_id = str(pending.strategy_run_id or "")
  intent_run_id = str(intent.strategy_run_id or "")
  correlation_run_id = (
    str(correlation.strategy_run_id or "") if correlation is not None else ""
  )
  runtime_binding_valid = bool(
    owner_kind == MONITOR_OWNER
    and not record_run_id
    and not pending_run_id
    and not intent_run_id
    and correlation is None
  ) or bool(
    owner_kind in {RUNTIME_BOOK_OWNER, MANAGED_EXIT_STRATEGY_OWNER}
    and record_run_id
    and pending_run_id == record_run_id
    and intent_run_id == record_run_id
    and correlation is not None
    and correlation_run_id == record_run_id
    and str(metadata.get("strategy_run_id") or "") == record_run_id
    and str(intent_metadata.get("strategy_run_id") or "") == record_run_id
    and str(dict(correlation.request_metadata or {}).get("strategy_run_id") or "")
    == record_run_id
  )
  if not (
    owner_kind != INVALID_OWNER
    and runtime_binding_valid
    and
    str(record.plan_id or "") == plan_id
    and str(record.account_id or "") == str(pending.account_id or "")
    and str(record.instrument_code or "").strip().upper()
    == str(pending.instrument_code or "").strip().upper()
    and str(record.execution_mode or "").strip().lower() == "live"
    and domain_plan.plan_id == plan_id
    and str(domain_plan.template.account_id or "")
    == str(pending.account_id or "")
    and str(domain_plan.template.instrument_code or "").strip().upper()
    == str(pending.instrument_code or "").strip().upper()
    and str(domain_plan.pending_intent_id or "") == intent_id
    and (
      not str(domain_plan.pending_order_id or "")
      or str(domain_plan.pending_order_id or "")
      == str(pending.client_order_id or "")
    )
    and str(intent.id or "") == intent_id
    and str(intent.owner_type or "").strip().upper() == "EXIT_PLAN"
    and str(intent.owner_id or "") == plan_id
    and str(intent.account_id or "") == str(pending.account_id or "")
    and str(intent.instrument_code or "").strip().upper()
    == str(pending.instrument_code or "").strip().upper()
    and str(intent.direction or "").strip().upper() == "SELL"
    and max(0, int(intent.executed_volume or 0)) == 0
    and str(metadata.get("owner_type") or "").strip().upper() == "EXIT_PLAN"
    and str(metadata.get("owner_id") or "") == plan_id
    and str(metadata.get("exit_plan_id") or "") == plan_id
    and str(intent_metadata.get("owner_type") or "").strip().upper()
    == "EXIT_PLAN"
    and str(intent_metadata.get("owner_id") or "") == plan_id
    and str(intent_metadata.get("exit_plan_id") or "") == plan_id
    and (
      correlation is None
      or (
        str(correlation.client_order_id or "")
        == str(pending.client_order_id or "")
        and str(correlation.account_id or "") == str(pending.account_id or "")
        and str(correlation.intent_id or "") == intent_id
        and str(correlation.execution_mode or "").strip().lower() == "live"
        and str(dict(correlation.request_metadata or {}).get("owner_type") or "")
        .strip()
        .upper()
        == "EXIT_PLAN"
        and str(dict(correlation.request_metadata or {}).get("owner_id") or "")
        == plan_id
        and str(
          dict(correlation.request_metadata or {}).get("exit_plan_id") or ""
        )
        == plan_id
      )
    )
  ):
    return False

  released = ExitPlanBook([domain_plan]).apply_order_event(
    plan_id=plan_id,
    intent_id=intent_id,
    status="RECONCILED_ZERO_FILL",
  )
  if (
    released is None
    or str(released.pending_intent_id or "")
    or str(released.pending_order_id or "")
  ):
    return False
  sticky_error = f"ACCOUNT_WIDE_STALE_SELL:{intent_id}"
  released.status = ExitPlanStatus.ERROR
  released.error_message = sticky_error
  released.template = type(released.template).from_dict(
    {**released.template.to_dict(), "auto_exit_authorized": False}
  )

  record.plan_state = released.to_dict()
  record.pending_client_order_id = None
  record.status = ExitPlanStatus.ERROR.value
  record.enabled = False
  record.last_error = sticky_error
  record.state_version = max(1, int(record.state_version or 1)) + 1
  clear_exact_auto_exit_authorization(record, bump_state_version=False)
  intent.intent_metadata = {
    **intent_metadata,
    "execution_terminal_source": _LOCAL_OUTBOX_CANCEL_SOURCE,
    "execution_terminal_reason": _LOCAL_OUTBOX_CANCEL_REASON,
    "reconciled_zero_fill_intent_id": intent_id,
  }
  intent.status = "RECONCILED_ZERO_FILL"
  intent.notes = _LOCAL_OUTBOX_CANCEL_REASON
  return True


def _mark_account_wide_pending_sticky(
  record: AutoExitPlanRecord,
  *,
  pending: PendingTradeOrder,
  intent: TradeIntentRecord,
  correlation: StrategyOrderCorrelation | None,
) -> bool:
  """Fail-close another plan whose LIVE SELL may already have left the API."""

  plan_id, intent_id = _pending_owner(pending)
  if not plan_id or not intent_id:
    return False
  try:
    domain_plan = ExitPlan.from_dict(dict(record.plan_state or {}))
  except (KeyError, TypeError, ValueError):
    return False
  metadata = dict(pending.request_metadata or {})
  intent_metadata = dict(intent.intent_metadata or {})
  correlation_metadata = (
    dict(correlation.request_metadata or {}) if correlation is not None else {}
  )
  owner_kind = durable_exit_plan_owner_kind(record)
  record_run_id = str(record.strategy_run_id or "")
  pending_run_id = str(pending.strategy_run_id or "")
  intent_run_id = str(intent.strategy_run_id or "")
  correlation_run_id = (
    str(correlation.strategy_run_id or "") if correlation is not None else ""
  )
  runtime_binding_valid = bool(
    owner_kind == MONITOR_OWNER
    and not record_run_id
    and not pending_run_id
    and not intent_run_id
    and correlation is None
  ) or bool(
    owner_kind in {RUNTIME_BOOK_OWNER, MANAGED_EXIT_STRATEGY_OWNER}
    and record_run_id
    and pending_run_id == record_run_id
    and intent_run_id == record_run_id
    and correlation is not None
    and correlation_run_id == record_run_id
    and str(metadata.get("strategy_run_id") or "") == record_run_id
    and str(intent_metadata.get("strategy_run_id") or "") == record_run_id
    and str(correlation_metadata.get("strategy_run_id") or "") == record_run_id
  )
  if not (
    owner_kind != INVALID_OWNER
    and runtime_binding_valid
    and str(record.plan_id or "") == plan_id
    and str(record.account_id or "") == str(pending.account_id or "")
    and str(record.instrument_code or "").strip().upper()
    == str(pending.instrument_code or "").strip().upper()
    and str(record.execution_mode or "").strip().lower() == "live"
    and domain_plan.plan_id == plan_id
    and str(domain_plan.template.account_id or "")
    == str(pending.account_id or "")
    and str(domain_plan.template.instrument_code or "").strip().upper()
    == str(pending.instrument_code or "").strip().upper()
    and str(domain_plan.pending_intent_id or "") == intent_id
    and (
      not str(domain_plan.pending_order_id or "")
      or str(domain_plan.pending_order_id or "")
      == str(pending.client_order_id or "")
    )
    and str(intent.id or "") == intent_id
    and str(intent.owner_type or "").strip().upper() == "EXIT_PLAN"
    and str(intent.owner_id or "") == plan_id
    and str(intent.account_id or "") == str(pending.account_id or "")
    and str(intent.instrument_code or "").strip().upper()
    == str(pending.instrument_code or "").strip().upper()
    and str(intent.direction or "").strip().upper() == "SELL"
    and str(metadata.get("owner_type") or "").strip().upper() == "EXIT_PLAN"
    and str(metadata.get("owner_id") or "") == plan_id
    and str(metadata.get("exit_plan_id") or "") == plan_id
    and str(intent_metadata.get("owner_type") or "").strip().upper()
    == "EXIT_PLAN"
    and str(intent_metadata.get("owner_id") or "") == plan_id
    and str(intent_metadata.get("exit_plan_id") or "") == plan_id
    and (
      correlation is None
      or (
        str(correlation.client_order_id or "")
        == str(pending.client_order_id or "")
        and str(correlation.account_id or "") == str(pending.account_id or "")
        and str(correlation.intent_id or "") == intent_id
        and str(correlation.execution_mode or "").strip().lower() == "live"
        and str(correlation_metadata.get("owner_type") or "").strip().upper()
        == "EXIT_PLAN"
        and str(correlation_metadata.get("owner_id") or "") == plan_id
        and str(correlation_metadata.get("exit_plan_id") or "") == plan_id
      )
    )
  ):
    return False

  sticky_error = f"ACCOUNT_WIDE_STALE_SELL:{intent_id}"
  domain_plan.status = ExitPlanStatus.ERROR
  domain_plan.error_message = sticky_error
  domain_plan.template = type(domain_plan.template).from_dict(
    {**domain_plan.template.to_dict(), "auto_exit_authorized": False}
  )
  record.plan_state = domain_plan.to_dict()
  record.status = ExitPlanStatus.ERROR.value
  record.enabled = False
  record.last_error = sticky_error
  record.state_version = max(1, int(record.state_version or 1)) + 1
  clear_exact_auto_exit_authorization(record, bump_state_version=False)
  return True


class AccountExecutionQuarantineService:
  """Own the durable account and command quarantine boundary."""

  def __init__(self, db: Any) -> None:
    self.db = db

  async def _lock_command_account_first(
    self,
    *,
    message_id: str,
    device_id: str = "",
  ) -> tuple[
    TradeCommandOutbox | None,
    AccountExecutionControl | None,
    bool,
    str,
  ]:
    """Discover a command, then lock account (when needed) before outbox."""

    candidate_query = select(
      TradeCommandOutbox.account_id,
      TradeCommandOutbox.payload,
    ).where(TradeCommandOutbox.message_id == str(message_id or ""))
    if str(device_id or ""):
      candidate_query = candidate_query.where(
        TradeCommandOutbox.device_id == str(device_id)
      )
    candidate = (await self.db.execute(candidate_query)).one_or_none()
    if candidate is None:
      return None, None, False, "COMMAND_NOT_FOUND"
    account_id = str(candidate[0] or "")
    payload = dict(candidate[1] or {})
    live_place_order = bool(
      str(payload.get("command_kind") or "").strip().upper() == "PLACE_ORDER"
      and str(payload.get("execution_mode") or "").strip().lower() == "live"
    )
    control = None
    if live_place_order:
      control = await self.db.get(
        AccountExecutionControl,
        account_id,
        with_for_update=True,
        populate_existing=True,
      )

    command = await self.db.get(
      TradeCommandOutbox,
      str(message_id or ""),
      with_for_update=True,
      populate_existing=True,
    )
    if command is None:
      return None, control, live_place_order, "COMMAND_NOT_FOUND"
    if (
      str(command.account_id or "") != account_id
      or dict(command.payload or {}) != payload
    ):
      return None, control, live_place_order, "COMMAND_CHANGED"
    return command, control, live_place_order, ""

  async def lock_command_for_lifecycle(
    self,
    *,
    message_id: str,
    device_id: str = "",
  ) -> TradeCommandDeliveryLock:
    """Lock a command for ACK/expiry mutation without outbox -> account inversion."""

    (
      command,
      _control,
      _live_place_order,
      reason,
    ) = await self._lock_command_account_first(
      message_id=message_id,
      device_id=device_id,
    )
    return TradeCommandDeliveryLock(command, reason)

  async def lock_command_for_physical_send(
    self,
    *,
    message_id: str,
    expected_payload: Mapping[str, Any],
  ) -> TradeCommandDeliveryLock:
    """Linearize one LIVE PLACE frame with account quarantine.

    The caller keeps this transaction open only for the bounded socket write.
    If quarantine committed first, the canonical command/account state blocks
    the frame.  If this lock wins first, quarantine subsequently observes the
    DELIVERED evidence and preserves a broker-cancellation obligation.
    """

    command, control, live_place_order, reason = (
      await self._lock_command_account_first(message_id=message_id)
    )
    if command is None:
      return TradeCommandDeliveryLock(None, reason)
    payload = dict(command.payload or {})
    if not live_place_order or payload != dict(expected_payload or {}):
      return TradeCommandDeliveryLock(None, "COMMAND_CHANGED")
    if str(command.delivery_status or "").strip().upper() != "DELIVERED":
      return TradeCommandDeliveryLock(None, "COMMAND_NOT_DELIVERABLE")
    if (
      control is None
      or str(control.reconcile_status or "").strip().upper() != "READY"
    ):
      return TradeCommandDeliveryLock(None, "ACCOUNT_RECONCILE_REQUIRED")
    side = str(payload.get("side") or "").strip().upper()
    if side == "BUY":
      # LIVE buys share the account/quarantine linearization point, while
      # their market and risk-increase gates are rechecked by the API control
      # session immediately before this lock. Exit-plan ownership and current
      # sellable-volume validation apply only to SELL commands.
      return TradeCommandDeliveryLock(command)
    if side != "SELL":
      return TradeCommandDeliveryLock(None, "COMMAND_CHANGED")
    from quantx_infrastructure.services.trade_command_service import (
      AgentUnavailableError,
      TradeCommandService,
    )

    try:
      await TradeCommandService(
        self.db
      ).validate_locked_live_exit_plan_place_for_delivery(command)
    except AgentUnavailableError as exc:
      pending = await self.db.get(
        PendingTradeOrder,
        str(command.client_order_id or ""),
        with_for_update=True,
        populate_existing=True,
      )
      command.delivery_status = "RECONCILE_REQUIRED"
      command.last_error = "physical_delivery_exit_plan_final_gate_rejected"
      cancel_message_id = ""
      plan_id = ""
      intent_id = ""
      if pending is not None:
        plan_id, intent_id = _pending_owner(pending)
        pending.request_metadata = {
          **dict(pending.request_metadata or {}),
          QUARANTINE_CANCEL_REQUIRED_METADATA_KEY: True,
          QUARANTINE_REPAIR_REQUIRED_METADATA_KEY: True,
          QUARANTINE_REASON_METADATA_KEY: "PHYSICAL_DELIVERY_GATE_REJECTED",
        }
        pending.status = "CANCEL_REQUESTED"
        pending.status_reason = "physical delivery final gate rejected"
        broker_order_id = str(pending.broker_order_id or "").strip()
        if broker_order_id:
          try:
            cancel = await TradeCommandService(self.db).enqueue_cancel(
              user_id=str(pending.user_id or ""),
              account_id=str(pending.account_id or ""),
              broker_order_id=broker_order_id,
              idempotency_key=(
                f"entry-plan-cancel:{pending.client_order_id}:{broker_order_id}"
              ),
              execution_mode="live",
              commit_transaction=False,
            )
          except AgentUnavailableError:
            cancel = None
          if cancel is not None:
            cancel_message_id = str(cancel.message_id or "")
      previous_state = str(control.authorization_state or "DISABLED")
      control.reconcile_status = "RECONCILE_REQUIRED"
      if previous_state != "KILLED":
        control.authorization_state = "PAUSED"
      _clear_controlled_window(control)
      control.state_version = max(0, int(control.state_version or 0)) + 1
      blocked_at = utcnow()
      control.paused_reason = json.dumps(
        [
          {
            "kind": "QUARANTINED_ORDER_REPAIR_REQUIRED",
            "business_id": str(command.client_order_id or ""),
            "reason": "PHYSICAL_DELIVERY_GATE_REJECTED",
            "quarantinedAt": blocked_at.isoformat(),
          }
        ],
        ensure_ascii=False,
        separators=(",", ":"),
      )[:2000]
      event_id = f"physical-delivery-quarantine:{command.message_id}"
      existing_event = await self.db.get(AccountExecutionControlEvent, event_id)
      if existing_event is None:
        self.db.add(
          AccountExecutionControlEvent(
            event_id=event_id,
            account_id=str(command.account_id or ""),
            event_type=LIVE_PLACE_PHYSICAL_GATE_REJECTED,
            previous_state=previous_state,
            next_state=str(control.authorization_state or ""),
            snapshot_id=control.last_snapshot_id,
            details={
              "triggerPlanId": plan_id,
              "triggerIntentId": intent_id,
              "reason": str(exc),
              "commandDispositions": [
                {
                  "clientOrderId": str(command.client_order_id or ""),
                  "messageId": str(command.message_id or ""),
                  "planId": plan_id,
                  "intentId": intent_id,
                  "ownerKind": "UNKNOWN",
                  "disposition": "PHYSICAL_DELIVERY_GATE_REJECTED",
                  "repairReason": "PHYSICAL_DELIVERY_GATE_REJECTED",
                  "cancelMessageId": cancel_message_id or None,
                }
              ],
            },
            created_at=blocked_at,
          )
        )
      await self.db.flush()
      return TradeCommandDeliveryLock(
        None,
        "EXIT_PLAN_FINAL_GATE_REJECTED",
        commit_required=True,
      )
    return TradeCommandDeliveryLock(command)

  async def lock_client_order_for_lifecycle(
    self,
    *,
    client_order_id: str,
  ) -> TradeCommandDeliveryLock:
    """Lock an existing PLACE lifecycle before pending/intent projection.

    Report staging knows a client order before it knows the command message.
    Candidate discovery remains unlocked; LIVE rows then take the same
    account -> PLACE outbox order as delivery, ACK, expiry, and quarantine.
    The caller may lock pending/correlation/intent only after this returns.
    """

    normalized_client_order_id = str(client_order_id or "").strip()
    if not normalized_client_order_id:
      return TradeCommandDeliveryLock(None, "COMMAND_NOT_FOUND")
    pending_candidate = (
      await self.db.execute(
        select(
          PendingTradeOrder.account_id,
          PendingTradeOrder.execution_mode,
        ).where(
          PendingTradeOrder.client_order_id == normalized_client_order_id
        )
      )
    ).one_or_none()
    outbox_candidate = (
      await self.db.execute(
        select(
          TradeCommandOutbox.message_id,
          TradeCommandOutbox.account_id,
          TradeCommandOutbox.payload,
        )
        .where(TradeCommandOutbox.client_order_id == normalized_client_order_id)
        .limit(1)
      )
    ).one_or_none()
    if pending_candidate is None and outbox_candidate is None:
      return TradeCommandDeliveryLock(None, "COMMAND_NOT_FOUND")

    pending_account_id = (
      str(pending_candidate[0] or "") if pending_candidate is not None else ""
    )
    pending_execution_mode = (
      str(pending_candidate[1] or "").lower()
      if pending_candidate is not None
      else ""
    )
    outbox_account_id = (
      str(outbox_candidate[1] or "") if outbox_candidate is not None else ""
    )
    candidate_payload = (
      dict(outbox_candidate[2] or {}) if outbox_candidate is not None else {}
    )
    account_id = pending_account_id or outbox_account_id
    live_place = bool(
      pending_execution_mode == "live"
      or (
        str(candidate_payload.get("command_kind") or "").upper() == "PLACE_ORDER"
        and str(candidate_payload.get("execution_mode") or "").lower() == "live"
      )
    )
    if live_place:
      await self.db.get(
        AccountExecutionControl,
        account_id,
        with_for_update=True,
        populate_existing=True,
      )

    if outbox_candidate is None:
      return TradeCommandDeliveryLock(None, "COMMAND_NOT_FOUND")
    message_id = str(outbox_candidate[0] or "")
    command = await self.db.get(
      TradeCommandOutbox,
      message_id,
      with_for_update=True,
      populate_existing=True,
    )
    if command is None:
      return TradeCommandDeliveryLock(None, "COMMAND_NOT_FOUND")
    if (
      str(command.client_order_id or "") != normalized_client_order_id
      or str(command.account_id or "") != outbox_account_id
      or dict(command.payload or {}) != candidate_payload
      or (pending_account_id and str(command.account_id or "") != pending_account_id)
    ):
      return TradeCommandDeliveryLock(None, "COMMAND_CHANGED")
    return TradeCommandDeliveryLock(command)

  async def _latest_full_snapshot_payload(
    self,
    *,
    account_id: str,
    snapshot_id: str,
    snapshot_hash: str,
  ) -> dict[str, Any]:
    reports = list(
      (
        await self.db.execute(
          select(AgentReportInbox)
          .where(
            AgentReportInbox.message_type == "delta_report",
            AgentReportInbox.protocol_version == "1.1",
            AGENT_REPORT_SNAPSHOT_ID == snapshot_id,
          )
          .order_by(AgentReportInbox.received_at.desc())
        )
      )
      .scalars()
      .all()
    )
    for report in reports:
      payload = dict(report.payload or {})
      if (
        payload.get("is_complete") is not True
        or str(payload.get("snapshot_id") or "") != snapshot_id
        or str(payload.get("snapshot_hash") or "").lower()
        != str(snapshot_hash or "").lower()
        or _snapshot_hash(payload) != str(snapshot_hash or "").lower()
      ):
        continue
      account_ids = {
        str(item.get("account_id") or "")
        for item in list(payload.get("accounts") or [])
        if isinstance(item, Mapping)
      }
      sections = dict(
        dict(payload.get("section_completeness_by_account") or {}).get(account_id)
        or {}
      )
      authority = dict(
        dict(payload.get("snapshot_authority_by_account") or {}).get(account_id)
        or {}
      )
      unavailable = {
        str(value.get("account_id") if isinstance(value, Mapping) else value)
        for value in list(payload.get("unavailable_accounts") or [])
      }
      if (
        account_id in account_ids
        and account_id in dict(payload.get("positions_by_account") or {})
        and account_id not in unavailable
        and snapshot_account_authority_is_authoritative(authority)
        and all(
          sections.get(section) is True
          for section in ("account", "positions", "orders", "trades")
        )
      ):
        return payload
    raise ValueError("最新权威完整快照原文不可用，请等待 QMT Agent 再次上报")

  @staticmethod
  def _repair_observation(
    *,
    payload: Mapping[str, Any],
    account_id: str,
    client_order_id: str,
    broker_order_id: str,
    pending: PendingTradeOrder | None,
    outbox: TradeCommandOutbox | None,
    intent: TradeIntentRecord | None,
    previous_delivery_status: str = "",
  ) -> tuple[str, int]:
    client_order_id = str(client_order_id or "")
    broker_order_id = str(broker_order_id or "")

    def exact_item(raw: Any) -> bool:
      if not isinstance(raw, Mapping):
        return False
      item = dict(raw)
      if str(item.get("account_id") or "") != account_id:
        return False
      item_client = str(item.get("client_order_id") or "")
      item_broker = str(item.get("order_id") or item.get("broker_order_id") or "")
      return bool(
        item_client == client_order_id
        or (broker_order_id and item_broker == broker_order_id)
      )

    orders = [dict(item) for item in payload.get("orders") or [] if exact_item(item)]
    trades = [dict(item) for item in payload.get("trades") or [] if exact_item(item)]
    if len(orders) > 1:
      raise ValueError("最新完整快照包含多个匹配委托，无法安全修复隔离绑定")
    try:
      durable_execution = max(0, int(intent.executed_volume or 0)) if intent else 0
      snapshot_execution = sum(
        max(0, int(item.get("traded_volume") or item.get("volume") or 0))
        for item in trades
      )
    except (TypeError, ValueError, OverflowError) as exc:
      raise ValueError("最新完整快照成交数量无效") from exc
    if snapshot_execution != durable_execution:
      raise ValueError("最新完整快照成交与 durable intent 尚未收敛")

    if orders:
      order = orders[0]
      status = _normalized_broker_status(
        order.get("effective_order_status")
        or order.get("order_status")
        or order.get("status")
      )
      if status not in _BROKER_TERMINAL_STATUSES:
        raise ValueError("最新完整快照仍显示该委托工作中，必须先完成撤单")
      if not any(key in order for key in ("traded_volume", "filled_volume")):
        raise ValueError("最新完整快照缺少委托累计成交，无法证明执行已收敛")
      try:
        cumulative = max(
          0,
          int(order.get("traded_volume") or order.get("filled_volume") or 0),
        )
      except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("最新完整快照委托累计成交无效") from exc
      if cumulative != durable_execution:
        raise ValueError("委托累计成交与 durable intent 尚未收敛")
      if status == "FILLED" and cumulative == 0:
        raise ValueError("FILLED 委托不能作为零成交隔离修复证明")
      return status, cumulative

    may_have_left = bool(
      str(previous_delivery_status or "").strip().upper()
      in {"DELIVERED", "ACKNOWLEDGED", "RECONCILE_REQUIRED"}
      or (
        outbox is not None
        and (
          outbox.delivered_at is not None
          or outbox.acknowledged_at is not None
          or int(outbox.attempts or 0) > 0
        )
      )
    )
    if may_have_left or broker_order_id:
      raise ValueError("可能已出站的委托尚无权威 broker 终态")
    if durable_execution or trades:
      raise ValueError("委托缺少 broker 终态且存在成交证据")
    return "RECONCILED_ZERO_FILL", 0

  async def _quarantine_event_for_repair(
    self,
    *,
    account_id: str,
    client_order_id: str,
    quarantine_reason: str,
  ) -> AccountExecutionControlEvent:
    events = list(
      (
        await self.db.execute(
          select(AccountExecutionControlEvent)
          .where(
            AccountExecutionControlEvent.account_id == account_id,
            AccountExecutionControlEvent.event_type.in_(
              (BROKER_EXECUTION_AFTER_RELEASE, LIVE_PLACE_PHYSICAL_GATE_REJECTED)
            ),
          )
          .order_by(AccountExecutionControlEvent.created_at.desc())
        )
      )
      .scalars()
      .all()
    )
    for event in events:
      if _quarantine_disposition_reason(
        event,
        client_order_id=client_order_id,
      ) == quarantine_reason:
        return event
    raise ValueError("未找到与请求精确匹配的未修复账户隔离审计")

  async def list_quarantined_orders(
    self,
    *,
    account_id: str,
    control: AccountExecutionControl | None = None,
  ) -> list[dict[str, Any]]:
    """Project every active explicit-repair candidate for account status."""

    normalized_account_id = str(account_id or "").strip()
    if not normalized_account_id:
      return []
    if control is None:
      control = await self.db.get(AccountExecutionControl, normalized_account_id)
    snapshot_sequence = 0
    snapshot_evidence_error = ""
    snapshot_payload: dict[str, Any] | None = None
    snapshot_loaded = False
    events = list(
      (
        await self.db.execute(
          select(AccountExecutionControlEvent)
          .where(
            AccountExecutionControlEvent.account_id == normalized_account_id,
            AccountExecutionControlEvent.event_type.in_(
              (BROKER_EXECUTION_AFTER_RELEASE, LIVE_PLACE_PHYSICAL_GATE_REJECTED)
            ),
          )
          .order_by(AccountExecutionControlEvent.created_at.desc())
        )
      )
      .scalars()
      .all()
    )
    candidates: list[dict[str, Any]] = []
    seen_clients: set[str] = set()
    for event in events:
      details = dict(event.details or {})
      for raw in list(details.get("commandDispositions") or []):
        disposition = dict(raw or {})
        client_order_id = str(disposition.get("clientOrderId") or "")
        reason = str(
          disposition.get("repairReason")
          or disposition.get("disposition")
          or ""
        ).strip().upper()
        if (
          not client_order_id
          or client_order_id in seen_clients
          or reason not in _REPAIRABLE_QUARANTINE_REASONS
        ):
          continue
        seen_clients.add(client_order_id)
        pending = await self.db.get(PendingTradeOrder, client_order_id)
        message_id = str(disposition.get("messageId") or "")
        outbox = (
          await self.db.get(TradeCommandOutbox, message_id)
          if message_id
          else None
        )
        if pending is not None:
          pending_metadata = dict(pending.request_metadata or {})
          if (
            str(pending.account_id or "") != normalized_account_id
            or str(pending.execution_mode or "").lower() != "live"
            or str(pending_metadata.get(QUARANTINE_REASON_METADATA_KEY) or "")
            != reason
            or not pending_metadata.get(QUARANTINE_REPAIR_REQUIRED_METADATA_KEY)
          ):
            continue
        elif not (
          outbox is not None
          and str(outbox.account_id or "") == normalized_account_id
          and str(outbox.delivery_status or "").upper() == "RECONCILE_REQUIRED"
        ):
          continue
        if not snapshot_loaded:
          snapshot_loaded = True
          if (
            control is not None
            and str(control.last_snapshot_id or "")
            and str(control.last_snapshot_hash or "")
            and control.last_snapshot_at is not None
          ):
            try:
              snapshot_payload = await self._latest_full_snapshot_payload(
                account_id=normalized_account_id,
                snapshot_id=str(control.last_snapshot_id or ""),
                snapshot_hash=str(control.last_snapshot_hash or ""),
              )
            except ValueError:
              snapshot_evidence_error = "LATEST_FULL_SNAPSHOT_EVIDENCE_UNAVAILABLE"
            else:
              snapshot_sequence = _snapshot_sequence(snapshot_payload)
        blocked_reason = ""
        quarantine_sequence = max(
          0,
          int(
            details.get("quarantineSourceSequence")
            or details.get("sourceSequence")
            or 0
          ),
        )
        if (
          control is None
          or not str(control.last_snapshot_id or "")
          or not str(control.last_snapshot_hash or "")
          or control.last_snapshot_at is None
        ):
          blocked_reason = "LATEST_FULL_SNAPSHOT_REQUIRED"
        elif snapshot_evidence_error:
          blocked_reason = snapshot_evidence_error
        elif quarantine_sequence > 0 and snapshot_sequence <= quarantine_sequence:
          blocked_reason = "SNAPSHOT_SEQUENCE_NOT_NEWER_THAN_QUARANTINE"
        elif quarantine_sequence <= 0 and to_naive_utc(
          control.last_snapshot_at
        ) <= to_naive_utc(event.created_at):
          blocked_reason = "SNAPSHOT_NOT_NEWER_THAN_QUARANTINE"
        if not blocked_reason and snapshot_payload is not None:
          plan_id = str(disposition.get("planId") or "").strip()
          intent_id = str(disposition.get("intentId") or "").strip()
          record = (
            await self.db.get(AutoExitPlanRecord, plan_id) if plan_id else None
          )
          intent = (
            await self.db.get(TradeIntentRecord, intent_id) if intent_id else None
          )
          payload = dict(outbox.payload or {}) if outbox is not None else {}
          payload_metadata = dict(payload.get("request_metadata") or {})
          binding_exact = bool(
            record is not None
            and intent is not None
            and str(record.account_id or "") == normalized_account_id
            and str(record.execution_mode or "").lower() == "live"
            and durable_exit_plan_owner_kind(record) != INVALID_OWNER
            and str(intent.id or "") == intent_id
            and str(intent.account_id or "") == normalized_account_id
            and str(intent.owner_type or "").upper() == "EXIT_PLAN"
            and str(intent.owner_id or "") == plan_id
            and (
              outbox is None
              or (
                str(payload.get("command_kind") or "").upper() == "PLACE_ORDER"
                and str(payload.get("execution_mode") or "").lower() == "live"
                and str(payload.get("side") or "").upper() == "SELL"
                and str(payload.get("account_id") or outbox.account_id or "")
                == normalized_account_id
                and str(
                  payload.get("client_order_id") or outbox.client_order_id or ""
                )
                == client_order_id
                and str(payload.get("intent_id") or "") == intent_id
                and str(
                  payload_metadata.get("exit_plan_id")
                  or payload_metadata.get("owner_id")
                  or ""
                )
                == plan_id
              )
            )
          )
          if not binding_exact:
            blocked_reason = "DURABLE_BINDING_UNPROVEN"
          else:
            broker_order_id = str(
              (pending.broker_order_id if pending is not None else None)
              or disposition.get("brokerOrderId")
              or ""
            )
            try:
              self._repair_observation(
                payload=snapshot_payload,
                account_id=normalized_account_id,
                client_order_id=client_order_id,
                broker_order_id=broker_order_id,
                pending=pending,
                outbox=outbox,
                intent=intent,
                previous_delivery_status=str(
                  disposition.get("previousDeliveryStatus") or ""
                ),
              )
            except ValueError:
              blocked_reason = "BROKER_TERMINAL_EVIDENCE_REQUIRED"
        candidates.append(
          {
            "client_order_id": client_order_id,
            "plan_id": str(disposition.get("planId") or ""),
            "intent_id": str(disposition.get("intentId") or ""),
            "quarantine_reason": reason,
            "broker_order_id": str(
              (pending.broker_order_id if pending is not None else None)
              or disposition.get("brokerOrderId")
              or ""
            ),
            "repairable": not blocked_reason,
            "blocked_reason": blocked_reason,
            "quarantined_at": to_naive_utc(event.created_at),
            "source_sequence": max(
              0,
              int(
                details.get("quarantineSourceSequence")
                or details.get("sourceSequence")
                or 0
              ),
            ),
          }
        )
    return candidates

  async def repair_quarantined_order(
    self,
    *,
    account_id: str,
    client_order_id: str,
    quarantine_reason: str,
    snapshot_id: str,
    expected_state_version: int,
    actor_id: str,
    operator_reason: str,
    operation_id: str,
  ) -> QuarantinedOrderRepairResult:
    """Explicitly repair one exact, snapshot-proven quarantine candidate."""

    normalized_account_id = str(account_id or "").strip()
    normalized_client_order_id = str(client_order_id or "").strip()
    normalized_reason = str(quarantine_reason or "").strip().upper()
    normalized_snapshot_id = str(snapshot_id or "").strip()
    normalized_actor_id = str(actor_id or "").strip()
    normalized_operator_reason = str(operator_reason or "").strip()
    normalized_operation_id = str(operation_id or "").strip()
    if not (
      normalized_account_id
      and normalized_client_order_id
      and normalized_snapshot_id
      and normalized_actor_id
      and normalized_operator_reason
      and normalized_operation_id
      and normalized_reason in _REPAIRABLE_QUARANTINE_REASONS
    ):
      raise ValueError("隔离修复请求缺少精确账户、委托、原因、快照或操作人绑定")

    repair_identity = hashlib.sha256(
      (
        f"{normalized_account_id}\0{normalized_client_order_id}\0"
        f"{normalized_reason}\0{normalized_snapshot_id}\0"
        f"{max(0, int(expected_state_version))}"
      ).encode("utf-8")
    ).hexdigest()
    repair_event_id = f"quarantined-order-repair:{repair_identity}"
    replay = await self.db.get(AccountExecutionControlEvent, repair_event_id)
    if replay is not None:
      replay_details = dict(replay.details or {})
      return QuarantinedOrderRepairResult(
        applied=False,
        event_id=repair_event_id,
        account_id=normalized_account_id,
        client_order_id=normalized_client_order_id,
        plan_id=str(replay_details.get("planId") or ""),
        intent_id=str(replay_details.get("intentId") or ""),
        snapshot_id=normalized_snapshot_id,
        broker_terminal_status=str(
          replay_details.get("brokerTerminalStatus") or ""
        ),
        cumulative_filled_volume=max(
          0,
          int(replay_details.get("cumulativeFilledVolume") or 0),
        ),
      )

    source_event = await self._quarantine_event_for_repair(
      account_id=normalized_account_id,
      client_order_id=normalized_client_order_id,
      quarantine_reason=normalized_reason,
    )
    source_details = dict(source_event.details or {})
    source_dispositions = [
      dict(raw or {})
      for raw in list(source_details.get("commandDispositions") or [])
    ]
    disposition = next(
      (
        item
        for item in source_dispositions
        if str(item.get("clientOrderId") or "")
        == normalized_client_order_id
        and not item.get("repairedAt")
        and str(
          item.get("repairReason") or item.get("disposition") or ""
        )
        .strip()
        .upper()
        == normalized_reason
      ),
      None,
    )
    if disposition is None:
      raise ValueError("隔离修复目标已变化或已经修复")

    control = await self.db.get(
      AccountExecutionControl,
      normalized_account_id,
      with_for_update=True,
      populate_existing=True,
    )
    if control is None:
      raise ValueError("账户执行控制不存在")
    if int(control.state_version or 0) != max(0, int(expected_state_version)):
      raise ValueError("账户执行控制版本已变化，请重新预览隔离修复")
    if not (
      str(control.authorization_state or "").upper() == "PAUSED"
      and str(control.reconcile_status or "").upper() == "RECONCILE_REQUIRED"
    ):
      raise ValueError("账户不再处于隔离待修复状态")
    if str(control.last_snapshot_id or "") != normalized_snapshot_id:
      raise ValueError("隔离修复必须绑定当前最新完整快照")
    snapshot_hash = str(control.last_snapshot_hash or "")
    if not snapshot_hash or control.last_snapshot_at is None:
      raise ValueError("账户当前缺少可验证的完整快照")

    message_id = str(disposition.get("messageId") or "")
    outbox = (
      await self.db.get(
        TradeCommandOutbox,
        message_id,
        with_for_update=True,
        populate_existing=True,
      )
      if message_id
      else None
    )
    if outbox is not None and not (
      str(outbox.client_order_id or "") == normalized_client_order_id
      and str(outbox.account_id or "") == normalized_account_id
    ):
      raise ValueError("隔离 PLACE outbox 绑定已变化")
    pending = await self.db.get(
      PendingTradeOrder,
      normalized_client_order_id,
      with_for_update=True,
      populate_existing=True,
    )
    correlation = (
      await self.db.execute(
        select(StrategyOrderCorrelation)
        .where(
          StrategyOrderCorrelation.client_order_id
          == normalized_client_order_id
        )
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
      )
    ).scalar_one_or_none()
    plan_id = str(disposition.get("planId") or "").strip()
    intent_id = str(disposition.get("intentId") or "").strip()
    intent = (
      await self.db.get(
        TradeIntentRecord,
        intent_id,
        with_for_update=True,
        populate_existing=True,
      )
      if intent_id
      else None
    )
    record = (
      await self.db.get(
        AutoExitPlanRecord,
        plan_id,
        with_for_update=True,
        populate_existing=True,
      )
      if plan_id
      else None
    )
    if record is None or intent is None:
      raise ValueError("隔离修复缺少事件固化的 durable plan/intent")
    if not (
      str(record.account_id or "") == normalized_account_id
      and str(record.execution_mode or "").lower() == "live"
      and str(intent.id or "") == intent_id
      and str(intent.account_id or "") == normalized_account_id
      and str(intent.owner_type or "").upper() == "EXIT_PLAN"
      and str(intent.owner_id or "") == plan_id
      and durable_exit_plan_owner_kind(record) != INVALID_OWNER
    ):
      raise ValueError("隔离修复的 durable owner 绑定不可证明")
    payload = dict(outbox.payload or {}) if outbox is not None else {}
    payload_metadata = dict(payload.get("request_metadata") or {})
    if outbox is not None and not (
      str(payload.get("command_kind") or "").upper() == "PLACE_ORDER"
      and str(payload.get("execution_mode") or "").lower() == "live"
      and str(payload.get("side") or "").upper() == "SELL"
      and str(payload.get("account_id") or outbox.account_id or "")
      == normalized_account_id
      and str(payload.get("client_order_id") or outbox.client_order_id or "")
      == normalized_client_order_id
      and str(payload.get("intent_id") or "") == intent_id
      and str(
        payload_metadata.get("exit_plan_id")
        or payload_metadata.get("owner_id")
        or ""
      )
      == plan_id
    ):
      raise ValueError("隔离修复的 PLACE payload owner 绑定不可证明")

    if pending is not None:
      pending_metadata = dict(pending.request_metadata or {})
      if not (
        str(pending.account_id or "") == normalized_account_id
        and str(pending.execution_mode or "").lower() == "live"
        and str(pending.side or "").upper() == "SELL"
        and str(pending_metadata.get(QUARANTINE_REASON_METADATA_KEY) or "")
        == normalized_reason
        and pending_metadata.get(QUARANTINE_REPAIR_REQUIRED_METADATA_KEY)
      ):
        raise ValueError("隔离 Pending marker 已变化")
    elif outbox is None:
      raise ValueError("隔离修复目标已不存在")

    snapshot_payload = await self._latest_full_snapshot_payload(
      account_id=normalized_account_id,
      snapshot_id=normalized_snapshot_id,
      snapshot_hash=snapshot_hash,
    )
    snapshot_sequence = _snapshot_sequence(snapshot_payload)
    quarantine_sequence = max(
      0,
      int(
        source_details.get("quarantineSourceSequence")
        or source_details.get("sourceSequence")
        or 0
      ),
    )
    snapshot_is_not_newer = bool(
      snapshot_sequence <= quarantine_sequence
      if quarantine_sequence > 0
      else to_naive_utc(control.last_snapshot_at)
      <= to_naive_utc(source_event.created_at)
    )
    if snapshot_is_not_newer:
      raise ValueError("隔离修复要求严格更新于隔离事实的完整快照")

    broker_order_id = str(
      (pending.broker_order_id if pending is not None else None)
      or disposition.get("brokerOrderId")
      or ""
    )
    broker_terminal_status, cumulative_filled_volume = self._repair_observation(
      payload=snapshot_payload,
      account_id=normalized_account_id,
      client_order_id=normalized_client_order_id,
      broker_order_id=broker_order_id,
      pending=pending,
      outbox=outbox,
      intent=intent,
      previous_delivery_status=str(
        disposition.get("previousDeliveryStatus") or ""
      ),
    )
    repaired_as_zero_fill = bool(
      broker_terminal_status == "RECONCILED_ZERO_FILL"
      or (
        cumulative_filled_volume == 0
        and broker_terminal_status
        in {"CANCELLED", "CANCELED", "REJECTED", "EXPIRED"}
      )
    )

    try:
      domain_plan = ExitPlan.from_dict(dict(record.plan_state or {}))
    except (KeyError, TypeError, ValueError) as exc:
      raise ValueError("隔离修复的计划状态不可解析") from exc
    previous_error = str(record.last_error or domain_plan.error_message or "")
    sticky_error = (
      previous_error
      if is_sticky_exit_plan_error(previous_error)
      else f"QUARANTINE_REPAIRED:{intent_id}"
    )
    repaired_plan = domain_plan
    if str(domain_plan.pending_intent_id or "") == intent_id:
      domain_status = (
        "RECONCILED_ZERO_FILL"
        if repaired_as_zero_fill
        else broker_terminal_status
      )
      repaired_plan = ExitPlanBook([domain_plan]).apply_order_event(
        plan_id=plan_id,
        intent_id=intent_id,
        status=domain_status,
        cumulative_filled_volume=cumulative_filled_volume,
      )
      if repaired_plan is None:
        raise ValueError("隔离修复无法收敛计划 pending")
      if str(repaired_plan.pending_intent_id or "") or str(
        repaired_plan.pending_order_id or ""
      ):
        raise ValueError("隔离修复未能释放计划 pending")
      record.pending_client_order_id = None
    repaired_plan.status = ExitPlanStatus.ERROR
    repaired_plan.error_message = sticky_error
    repaired_plan.template = type(repaired_plan.template).from_dict(
      {**repaired_plan.template.to_dict(), "auto_exit_authorized": False}
    )
    record.plan_state = repaired_plan.to_dict()
    if repaired_as_zero_fill:
      intent.intent_metadata = {
        **dict(intent.intent_metadata or {}),
        "execution_terminal_source": _REPAIR_ZERO_FILL_SOURCE,
        "execution_terminal_reason": normalized_reason,
        "reconciled_zero_fill_intent_id": intent_id,
      }
      intent.status = "RECONCILED_ZERO_FILL"
      intent.notes = _REPAIR_ZERO_FILL_SOURCE
    record.status = ExitPlanStatus.ERROR.value
    record.enabled = False
    record.last_error = sticky_error
    clear_exact_auto_exit_authorization(record, bump_state_version=False)
    record.state_version = max(1, int(record.state_version or 1)) + 1

    if pending is not None:
      pending_metadata = dict(pending.request_metadata or {})
      pending_metadata.pop(QUARANTINE_CANCEL_REQUIRED_METADATA_KEY, None)
      pending_metadata.pop(QUARANTINE_RECONCILE_REQUIRED_METADATA_KEY, None)
      pending_metadata.pop(QUARANTINE_REPAIR_REQUIRED_METADATA_KEY, None)
      pending_metadata.pop(QUARANTINE_REASON_METADATA_KEY, None)
      pending_metadata["account_execution_quarantine_repaired"] = {
        "snapshot_id": normalized_snapshot_id,
        "snapshot_sequence": snapshot_sequence,
        "actor_id": normalized_actor_id,
        "reason": normalized_reason,
      }
      pending.request_metadata = pending_metadata
      pending.account_id = normalized_account_id
      pending.instrument_code = str(record.instrument_code or "")
      pending.intent_id = intent_id
      pending.side = "SELL"
      pending.execution_mode = "live"
      if broker_terminal_status == "RECONCILED_ZERO_FILL":
        pending.status = "CANCELLED"
        pending.status_reason = _REPAIR_ZERO_FILL_SOURCE
      else:
        pending.status = broker_terminal_status[:24]
        pending.status_reason = "explicit quarantine repair from authoritative snapshot"
    if correlation is not None:
      correlation.account_id = normalized_account_id
      correlation.intent_id = intent_id
      correlation.execution_mode = "live"
    if outbox is not None:
      outbox.delivery_status = (
        "CANCELLED"
        if broker_terminal_status == "RECONCILED_ZERO_FILL"
        else "RECONCILED_TERMINAL"
      )
      outbox.last_error = "quarantine_repaired_never_redeliver_place"

    repaired_at = utcnow()
    disposition.update(
      {
        "repairedAt": repaired_at.isoformat(),
        "repairSnapshotId": normalized_snapshot_id,
        "repairSnapshotSequence": snapshot_sequence,
        "repairActorId": normalized_actor_id,
        "brokerTerminalStatus": broker_terminal_status,
        "cumulativeFilledVolume": cumulative_filled_volume,
      }
    )
    source_details["commandDispositions"] = source_dispositions
    source_event.details = source_details

    remaining_repairs = False
    active_events = list(
      (
        await self.db.execute(
          select(AccountExecutionControlEvent).where(
            AccountExecutionControlEvent.account_id == normalized_account_id,
            AccountExecutionControlEvent.event_type.in_(
              (BROKER_EXECUTION_AFTER_RELEASE, LIVE_PLACE_PHYSICAL_GATE_REJECTED)
            ),
          )
        )
      )
      .scalars()
      .all()
    )
    for active_event in active_events:
      active_details = (
        source_details
        if str(active_event.event_id) == str(source_event.event_id)
        else dict(active_event.details or {})
      )
      if any(
        not dict(raw or {}).get("repairedAt")
        and str(
          dict(raw or {}).get("repairReason")
          or dict(raw or {}).get("disposition")
          or ""
        )
        .strip()
        .upper()
        in _REPAIRABLE_QUARANTINE_REASONS
        for raw in list(active_details.get("commandDispositions") or [])
      ):
        remaining_repairs = True
        break

    control.reconcile_status = "RECONCILE_REQUIRED"
    if str(control.authorization_state or "").upper() != "KILLED":
      control.authorization_state = "PAUSED"
    _clear_controlled_window(control)
    control.state_version = max(0, int(control.state_version or 0)) + 1
    if not remaining_repairs:
      control.paused_reason = json.dumps(
        [
          {
            "kind": "QUARANTINE_REPAIR_AWAITING_FRESH_SNAPSHOT",
            "repairSnapshotId": normalized_snapshot_id,
            "repairSnapshotHash": snapshot_hash,
            "quarantineSourceSequence": snapshot_sequence,
            "quarantinedAt": to_naive_utc(control.last_snapshot_at).isoformat(),
          }
        ],
        ensure_ascii=False,
        separators=(",", ":"),
      )[:2000]

    self.db.add(
      AccountExecutionControlEvent(
        event_id=repair_event_id,
        account_id=normalized_account_id,
        event_type=QUARANTINED_ORDER_REPAIRED,
        previous_state="PAUSED",
        next_state=str(control.authorization_state or ""),
        snapshot_id=normalized_snapshot_id,
        details={
          "sourceEventId": str(source_event.event_id),
          "clientOrderId": normalized_client_order_id,
          "planId": plan_id,
          "intentId": intent_id,
          "quarantineReason": normalized_reason,
          "brokerTerminalStatus": broker_terminal_status,
          "cumulativeFilledVolume": cumulative_filled_volume,
          "snapshotSequence": snapshot_sequence,
          "actorId": normalized_actor_id,
          "operatorReason": normalized_operator_reason,
          "operationId": normalized_operation_id,
          "remainingRepairs": remaining_repairs,
        },
        created_at=repaired_at,
      )
    )
    await self.db.flush()
    return QuarantinedOrderRepairResult(
      applied=True,
      event_id=repair_event_id,
      account_id=normalized_account_id,
      client_order_id=normalized_client_order_id,
      plan_id=plan_id,
      intent_id=intent_id,
      snapshot_id=normalized_snapshot_id,
      broker_terminal_status=broker_terminal_status,
      cumulative_filled_volume=cumulative_filled_volume,
    )

  async def quarantine_released_exit_plan(
    self,
    *,
    plan: AutoExitPlanRecord,
    invalidated_intent_id: str,
    evidence_key: str,
    evidence_kind: str,
    evidence_status: str,
    evidence_client_order_id: str = "",
    broker_order_id: str = "",
    source_sequence: int = 0,
  ) -> AccountExecutionQuarantineResult:
    """Quarantine an exact released plan inside its invalidation transaction.

    The caller must already hold and invalidate ``plan``.  Locking the account
    next serializes this boundary with delivery-time PLACE_ORDER claims and
    with every account execution-control mutation.
    """

    account_id = str(plan.account_id or "").strip()
    plan_id = str(plan.plan_id or "").strip()
    normalized_evidence_key = str(evidence_key or "").strip()
    if not account_id or not plan_id or not normalized_evidence_key:
      raise ValueError("账户隔离缺少账户、计划或 broker 证据身份")
    if str(plan.execution_mode or "").strip().lower() != "live":
      raise ValueError("broker 释放反证账户隔离只适用于 LIVE 退出计划")
    if str(plan.status or "").strip().upper() != "ERROR" or bool(plan.enabled):
      raise ValueError("账户隔离要求退出计划已完成精确失效并进入 ERROR")

    event_id = _event_id(
      account_id=account_id,
      plan_id=plan_id,
      evidence_key=normalized_evidence_key,
    )
    control = await self.db.get(
      AccountExecutionControl,
      account_id,
      with_for_update=True,
      populate_existing=True,
    )
    if control is None:
      control = AccountExecutionControl(account_id=account_id)
      self.db.add(control)
      await self.db.flush()

    existing_event = await self.db.get(AccountExecutionControlEvent, event_id)
    if existing_event is not None:
      if (
        str(existing_event.account_id or "") != account_id
        or str(existing_event.event_type or "") != BROKER_EXECUTION_AFTER_RELEASE
        or str(dict(existing_event.details or {}).get("evidenceKey") or "")
        != normalized_evidence_key
      ):
        raise ValueError("账户隔离审计身份已绑定其他 broker 证据")
      return _result_from_event(existing_event)

    plan_state = dict(plan.plan_state or {})
    current_pending_intent_id = str(plan_state.get("pending_intent_id") or "").strip()
    trigger_pending_client_ids = {
      str(value)
      for value in (
        await self.db.execute(
          select(PendingTradeOrder.client_order_id).where(
            PendingTradeOrder.account_id == account_id,
            PendingTradeOrder.intent_id == current_pending_intent_id,
            PendingTradeOrder.side == "SELL",
            PendingTradeOrder.execution_mode == "live",
          )
        )
      )
      .scalars()
      .all()
    } if current_pending_intent_id else set()
    required_client_order_ids = trigger_pending_client_ids | {
      str(evidence_client_order_id or "").strip()
    }
    required_client_order_ids.discard("")

    # BROKER_EXECUTION_AFTER_RELEASE means the account position truth may have
    # drifted, not only the triggering plan.  Discover every unfinished LIVE
    # PLACE SELL while the account lock is held.  Enqueue also takes the
    # account lock first, so no new SELL can slip between discovery and this
    # quarantine transaction.
    outbox_candidates = list(
      (
        await self.db.execute(
          select(
            TradeCommandOutbox.message_id,
            TradeCommandOutbox.client_order_id,
            TradeCommandOutbox.delivery_status,
            TradeCommandOutbox.payload,
          ).where(TradeCommandOutbox.account_id == account_id)
        )
      ).all()
    )
    outbox_message_ids = sorted(
      {
        str(row[0])
        for row in outbox_candidates
        if str(row[1] or "") in required_client_order_ids
        or (
          str(dict(row[3] or {}).get("command_kind") or "").strip().upper()
          == "PLACE_ORDER"
          and str(dict(row[3] or {}).get("execution_mode") or "")
          .strip()
          .lower()
          == "live"
          and str(dict(row[3] or {}).get("side") or "").strip().upper()
          == "SELL"
          and str(row[2] or "").strip().upper()
          not in _OUTBOX_TERMINAL_STATUSES
        )
      }
    )

    # Global lifecycle order for this account is Account -> every PLACE
    # Outbox -> every Pending -> Correlation -> Intent -> Plan.  Deterministic
    # ordering within each type prevents cross-plan lock cycles.
    outboxes: list[TradeCommandOutbox] = []
    if outbox_message_ids:
      outboxes = list(
        (
          await self.db.execute(
            select(TradeCommandOutbox)
            .where(TradeCommandOutbox.message_id.in_(outbox_message_ids))
            .order_by(TradeCommandOutbox.message_id)
            .with_for_update()
            .execution_options(populate_existing=True)
          )
        )
        .scalars()
        .all()
      )
    active_outboxes = [
      item
      for item in outboxes
      if _is_live_place_sell(item)
      or str(item.client_order_id or "") in required_client_order_ids
    ]
    pending_client_order_ids = sorted(
      required_client_order_ids
      | {
        str(item.client_order_id or "")
        for item in active_outboxes
        if str(item.client_order_id or "")
      }
    )
    pending_orders: list[PendingTradeOrder] = []
    if pending_client_order_ids:
      pending_orders = list(
        (
          await self.db.execute(
            select(PendingTradeOrder)
            .where(PendingTradeOrder.client_order_id.in_(pending_client_order_ids))
            .order_by(PendingTradeOrder.client_order_id)
            .with_for_update()
            .execution_options(populate_existing=True)
          )
        )
        .scalars()
        .all()
      )
    correlations = list(
      (
        await self.db.execute(
          select(StrategyOrderCorrelation)
          .where(
            StrategyOrderCorrelation.client_order_id.in_(
              pending_client_order_ids or ["__none__"]
            )
          )
          .order_by(StrategyOrderCorrelation.client_order_id)
          .with_for_update()
          .execution_options(populate_existing=True)
        )
      )
      .scalars()
      .all()
    )
    intent_ids = sorted(
      {str(item.intent_id or "") for item in pending_orders if str(item.intent_id or "")}
    )
    intents: list[TradeIntentRecord] = []
    if intent_ids:
      intents = list(
        (
          await self.db.execute(
            select(TradeIntentRecord)
            .where(TradeIntentRecord.id.in_(intent_ids))
            .order_by(TradeIntentRecord.id)
            .with_for_update()
            .execution_options(populate_existing=True)
          )
        )
        .scalars()
        .all()
      )
    pending_plan_ids = sorted(
      {
        owner_plan_id
        for item in pending_orders
        if (owner_plan_id := _pending_owner(item)[0]) and owner_plan_id != plan_id
      }
    )
    other_plans: list[AutoExitPlanRecord] = []
    if pending_plan_ids:
      other_plans = list(
        (
          await self.db.execute(
            select(AutoExitPlanRecord)
            .where(AutoExitPlanRecord.plan_id.in_(pending_plan_ids))
            .order_by(AutoExitPlanRecord.plan_id)
            .with_for_update()
            .execution_options(populate_existing=True)
          )
        )
        .scalars()
        .all()
      )

    outbox_by_client = {
      str(item.client_order_id): item for item in active_outboxes
    }
    pending_by_client = {
      str(item.client_order_id): item for item in pending_orders
    }
    correlation_by_client = {
      str(item.client_order_id): item for item in correlations
    }
    intent_by_id = {str(item.id): item for item in intents}
    plan_by_id = {plan_id: plan, **{str(item.plan_id): item for item in other_plans}}
    locally_cancelled: list[str] = []
    reconcile_required: list[str] = []
    cancel_message_ids: list[str] = []
    command_dispositions: list[dict[str, Any]] = []
    now = utcnow()
    from quantx_infrastructure.services.trade_command_service import (
      AgentUnavailableError,
      TradeCommandService,
    )

    async def enqueue_exact_cancel(pending: PendingTradeOrder) -> str:
      durable_broker_order_id = str(pending.broker_order_id or "").strip()
      if not durable_broker_order_id:
        return ""
      try:
        cancel = await TradeCommandService(self.db).enqueue_cancel(
          user_id=str(pending.user_id or ""),
          account_id=account_id,
          broker_order_id=durable_broker_order_id,
          idempotency_key=(
            f"entry-plan-cancel:{pending.client_order_id}:"
            f"{durable_broker_order_id}"
          ),
          execution_mode="live",
          commit_transaction=False,
        )
      except AgentUnavailableError:
        return ""
      message_id = str(cancel.message_id or "")
      if message_id and message_id not in cancel_message_ids:
        cancel_message_ids.append(message_id)
      return message_id

    # A live PLACE without its Pending projection is still an outbound hazard.
    # Seal it durably; the event carries the payload owner so explicit repair
    # can diagnose the broken binding without ever making the frame deliverable.
    for outbox in active_outboxes:
      client_order_id = str(outbox.client_order_id or "")
      if client_order_id in pending_by_client:
        continue
      payload = dict(outbox.payload or {})
      metadata = dict(payload.get("request_metadata") or {})
      previous_delivery_status = str(outbox.delivery_status or "").upper()
      outbox.delivery_status = "RECONCILE_REQUIRED"
      outbox.last_error = "quarantine_place_order_pending_missing"
      reconcile_required.append(client_order_id)
      command_dispositions.append(
        {
          "clientOrderId": client_order_id,
          "messageId": str(outbox.message_id),
          "planId": str(
            metadata.get("exit_plan_id") or metadata.get("owner_id") or ""
          ),
          "intentId": str(payload.get("intent_id") or ""),
          "ownerKind": INVALID_OWNER,
          "previousDeliveryStatus": previous_delivery_status,
          "disposition": "PLACE_ORDER_BINDING_MISSING",
        }
      )

    for pending in pending_orders:
      client_order_id = str(pending.client_order_id or "")
      outbox = outbox_by_client.get(client_order_id)
      owner_plan_id, owner_intent_id = _pending_owner(pending)
      owner_record = plan_by_id.get(owner_plan_id)
      owner_kind = (
        durable_exit_plan_owner_kind(owner_record)
        if owner_record is not None
        else INVALID_OWNER
      )
      disposition_binding = {
        "clientOrderId": client_order_id,
        "messageId": str(outbox.message_id) if outbox is not None else None,
        "planId": owner_plan_id,
        "intentId": owner_intent_id,
        "ownerKind": owner_kind,
      }
      trigger_candidate = bool(
        owner_plan_id == plan_id and owner_intent_id == current_pending_intent_id
      )
      exact_trigger = bool(
        trigger_candidate
        and _is_exact_current_plan_order(
          pending,
          plan=plan,
          current_pending_intent_id=current_pending_intent_id,
        )
      )
      evidence_candidate = bool(
        client_order_id == str(evidence_client_order_id or "").strip()
      )
      existing_pending_metadata = dict(pending.request_metadata or {})
      historically_terminal = bool(
        str(pending.status or "").strip().upper() in _PENDING_TERMINAL_STATUSES
        and not existing_pending_metadata.get(
          QUARANTINE_RECONCILE_REQUIRED_METADATA_KEY
        )
        and not existing_pending_metadata.get(QUARANTINE_CANCEL_REQUIRED_METADATA_KEY)
        and not existing_pending_metadata.get(QUARANTINE_REPAIR_REQUIRED_METADATA_KEY)
      )
      if historically_terminal and not trigger_candidate and not evidence_candidate:
        # Outbox delivery state records API/Agent transport, not broker
        # lifecycle.  Historical broker-terminal rows remain immutable and
        # must not become fresh cancellation obligations.
        continue

      if trigger_candidate and not exact_trigger:
        pending.request_metadata = {
          **dict(pending.request_metadata or {}),
          QUARANTINE_RECONCILE_REQUIRED_METADATA_KEY: True,
          QUARANTINE_REPAIR_REQUIRED_METADATA_KEY: True,
          QUARANTINE_REASON_METADATA_KEY: "BINDING_MISMATCH",
        }
        pending.status = "RECONCILE_REQUIRED"
        pending.status_reason = "quarantine current intent binding mismatch"
        if outbox is not None:
          outbox.delivery_status = "RECONCILE_REQUIRED"
          outbox.last_error = "quarantine_current_intent_binding_mismatch"
        reconcile_required.append(client_order_id)
        command_dispositions.append(
          {
            **disposition_binding,
            "disposition": "BINDING_MISMATCH",
          }
        )
        continue

      if not trigger_candidate:
        if evidence_candidate:
          owner_intent = intent_by_id.get(owner_intent_id)
          plan_sticky = bool(
            owner_record is not None
            and owner_intent is not None
            and _mark_account_wide_pending_sticky(
              owner_record,
              pending=pending,
              intent=owner_intent,
              correlation=correlation_by_client.get(client_order_id),
            )
          )
          existing_metadata = dict(pending.request_metadata or {})
          cancel_required = bool(
            existing_metadata.get(QUARANTINE_CANCEL_REQUIRED_METADATA_KEY)
            or str(pending.status or "").strip().upper() == "CANCEL_REQUESTED"
          )
          if outbox is not None:
            previous_delivery_status = str(outbox.delivery_status or "").upper()
            outbox.delivery_status = "RECONCILE_REQUIRED"
            outbox.last_error = "broker_execution_after_released_exit"
          else:
            previous_delivery_status = "MISSING"
          pending.request_metadata = {
            **existing_metadata,
            QUARANTINE_REPAIR_REQUIRED_METADATA_KEY: True,
            QUARANTINE_REASON_METADATA_KEY: BROKER_EXECUTION_AFTER_RELEASE,
          }
          cancel_message_id = ""
          if cancel_required:
            pending.request_metadata = {
              **dict(pending.request_metadata or {}),
              QUARANTINE_CANCEL_REQUIRED_METADATA_KEY: True,
            }
            pending.status = "CANCEL_REQUESTED"
            pending.status_reason = _QUARANTINE_CANCEL_REASON
            cancel_message_id = await enqueue_exact_cancel(pending)
          reconcile_required.append(client_order_id)
          command_dispositions.append(
            {
              **disposition_binding,
              "previousDeliveryStatus": previous_delivery_status,
              "brokerOrderId": str(pending.broker_order_id or "") or None,
              "cancelMessageId": cancel_message_id or None,
              "disposition": BROKER_EXECUTION_AFTER_RELEASE,
              "repairReason": BROKER_EXECUTION_AFTER_RELEASE,
              "planSticky": plan_sticky,
              "brokerTerminalEvidence": bool(
                str(evidence_kind or "").strip().upper() == "ORDER"
                and str(evidence_status or "").strip().upper()
                in _BROKER_TERMINAL_STATUSES
              ),
            }
          )
          continue

        exact_cross_plan_outbox = bool(
          outbox is not None and _is_exact_place_order(outbox, pending=pending)
        )
        if exact_cross_plan_outbox and _never_delivered(outbox, pending):
          outbox.delivery_status = "CANCELLED"
          outbox.last_error = "account_quarantined_before_agent_delivery"
          pending.status = "CANCELLED"
          pending.status_reason = "account quarantined before Agent delivery"
          owner_intent = intent_by_id.get(owner_intent_id)
          released = bool(
            owner_record is not None
            and owner_intent is not None
            and _release_account_wide_locally_cancelled_pending(
              owner_record,
              pending=pending,
              intent=owner_intent,
              correlation=correlation_by_client.get(client_order_id),
            )
          )
          if released:
            pending.request_metadata = {
              **dict(pending.request_metadata or {}),
              QUARANTINE_REPAIR_REQUIRED_METADATA_KEY: True,
              QUARANTINE_REASON_METADATA_KEY: "ACCOUNT_WIDE_STALE_SELL",
              "execution_terminal_source": _LOCAL_OUTBOX_CANCEL_SOURCE,
              "execution_terminal_reason": _LOCAL_OUTBOX_CANCEL_REASON,
              "reconciled_zero_fill_intent_id": owner_intent_id,
            }
            pending.status_reason = _LOCAL_OUTBOX_CANCEL_REASON
            locally_cancelled.append(client_order_id)
            command_dispositions.append(
              {
                **disposition_binding,
                "disposition": "ACCOUNT_WIDE_STALE_SELL",
                "repairReason": "ACCOUNT_WIDE_STALE_SELL",
                "localDisposition": "CANCELLED_BEFORE_AGENT_DELIVERY",
                "executionTerminalSource": _LOCAL_OUTBOX_CANCEL_SOURCE,
                "executionTerminalReason": _LOCAL_OUTBOX_CANCEL_REASON,
                "planPendingReleased": True,
              }
            )
            continue

          # A malformed owner projection cannot be locally called zero fill.
          # Keep both rows non-deliverable until explicit bounded repair.
          outbox.delivery_status = "RECONCILE_REQUIRED"
          outbox.last_error = "quarantine_account_wide_plan_release_failed"
          pending.request_metadata = {
            **dict(pending.request_metadata or {}),
            QUARANTINE_RECONCILE_REQUIRED_METADATA_KEY: True,
            QUARANTINE_REPAIR_REQUIRED_METADATA_KEY: True,
            QUARANTINE_REASON_METADATA_KEY: "ACCOUNT_WIDE_STALE_SELL",
          }
          pending.status = "RECONCILE_REQUIRED"
          pending.status_reason = (
            "account-wide local cancellation could not release exact plan pending"
          )
          reconcile_required.append(client_order_id)
          command_dispositions.append(
            {
              **disposition_binding,
              "disposition": "ACCOUNT_WIDE_STALE_SELL",
              "planPendingReleased": False,
            }
          )
          continue

        # Any other account SELL may have left the process.  Never synthesize
        # local terminal history: seal PLACE, retain a durable cancel marker,
        # and enqueue an exact broker cancel as soon as an id is available.
        owner_intent = intent_by_id.get(owner_intent_id)
        plan_sticky = bool(
          owner_record is not None
          and owner_intent is not None
          and _mark_account_wide_pending_sticky(
            owner_record,
            pending=pending,
            intent=owner_intent,
            correlation=correlation_by_client.get(client_order_id),
          )
        )
        if outbox is not None:
          previous_delivery_status = str(outbox.delivery_status or "").upper()
          outbox.delivery_status = "RECONCILE_REQUIRED"
          outbox.last_error = "account_wide_sell_invalidated_by_quarantine"
        else:
          previous_delivery_status = "MISSING"
        pending.request_metadata = {
          **dict(pending.request_metadata or {}),
          QUARANTINE_CANCEL_REQUIRED_METADATA_KEY: True,
          QUARANTINE_REPAIR_REQUIRED_METADATA_KEY: True,
          QUARANTINE_REASON_METADATA_KEY: "ACCOUNT_WIDE_STALE_SELL",
        }
        pending.status = "CANCEL_REQUESTED"
        pending.status_reason = (
          "account-wide quarantine requires exact broker cancellation"
        )
        cancel_message_id = await enqueue_exact_cancel(pending)
        reconcile_required.append(client_order_id)
        command_dispositions.append(
          {
            **disposition_binding,
            "previousDeliveryStatus": previous_delivery_status,
            "brokerOrderId": str(pending.broker_order_id or "") or None,
            "cancelMessageId": cancel_message_id or None,
            "disposition": "ACCOUNT_WIDE_STALE_SELL",
            "planSticky": plan_sticky,
          }
        )
        continue

      if outbox is None or not _is_exact_place_order(outbox, pending=pending):
        pending.request_metadata = {
          **dict(pending.request_metadata or {}),
          QUARANTINE_CANCEL_REQUIRED_METADATA_KEY: True,
          QUARANTINE_REPAIR_REQUIRED_METADATA_KEY: True,
          QUARANTINE_REASON_METADATA_KEY: "PLACE_ORDER_BINDING_MISSING",
        }
        pending.status = "CANCEL_REQUESTED"
        pending.status_reason = (
          "quarantine cancellation awaiting exact broker order identity"
        )
        if outbox is not None:
          outbox.delivery_status = "RECONCILE_REQUIRED"
          outbox.last_error = "quarantine_place_order_binding_missing"
        await enqueue_exact_cancel(pending)
        reconcile_required.append(client_order_id)
        command_dispositions.append(
          {
            **disposition_binding,
            "disposition": "PLACE_ORDER_BINDING_MISSING",
          }
        )
        continue

      if _never_delivered(outbox, pending):
        outbox.delivery_status = "CANCELLED"
        outbox.last_error = "account_quarantined_before_agent_delivery"
        pending.status = "CANCELLED"
        pending.status_reason = "account quarantined before Agent delivery"
        locally_cancelled.append(client_order_id)
        command_dispositions.append(
          {
            **disposition_binding,
            "disposition": "CANCELLED_BEFORE_AGENT_DELIVERY",
          }
        )
        continue

      # Preserve proof that PLACE_ORDER may already have left the server.  A
      # terminal-looking local cancellation would be false broker history.
      previous_delivery_status = str(outbox.delivery_status or "").upper()
      outbox.delivery_status = "RECONCILE_REQUIRED"
      outbox.last_error = "broker_execution_after_released_exit"
      durable_broker_order_id = str(pending.broker_order_id or "").strip()
      if durable_broker_order_id:
        pending.request_metadata = {
          **dict(pending.request_metadata or {}),
          QUARANTINE_CANCEL_REQUIRED_METADATA_KEY: True,
          QUARANTINE_REPAIR_REQUIRED_METADATA_KEY: True,
          QUARANTINE_REASON_METADATA_KEY: BROKER_EXECUTION_AFTER_RELEASE,
        }
        pending.status = "CANCEL_REQUESTED"
        pending.status_reason = _QUARANTINE_CANCEL_REASON
        await enqueue_exact_cancel(pending)
        disposition = "BROKER_CANCEL_QUEUED"
      else:
        # The command may already have left the server.  Persist the broker
        # cancellation intent now so the first later ORDER carrying its broker
        # id enters the existing exact/idempotent enqueue-cancel hook.
        pending.request_metadata = {
          **dict(pending.request_metadata or {}),
          QUARANTINE_CANCEL_REQUIRED_METADATA_KEY: True,
          QUARANTINE_REPAIR_REQUIRED_METADATA_KEY: True,
          QUARANTINE_REASON_METADATA_KEY: BROKER_EXECUTION_AFTER_RELEASE,
        }
        pending.status = "CANCEL_REQUESTED"
        pending.status_reason = "waiting for broker order id after account quarantine"
        disposition = "DELIVERED_AWAITING_BROKER_ID"
      reconcile_required.append(client_order_id)
      command_dispositions.append(
        {
          **disposition_binding,
          "previousDeliveryStatus": previous_delivery_status,
          "brokerOrderId": durable_broker_order_id or None,
          "disposition": disposition,
        }
      )

    released_zero_fill_intent_id = ""
    trigger_pending_orders = [
      item
      for item in pending_orders
      if _pending_owner(item) == (plan_id, current_pending_intent_id)
    ]
    trigger_locally_cancelled = [
      client_order_id
      for client_order_id in locally_cancelled
      if (
        (item := pending_by_client.get(client_order_id)) is not None
        and _pending_owner(item) == (plan_id, current_pending_intent_id)
      )
    ]
    if trigger_locally_cancelled:
      exact_local_release = bool(
        len(trigger_pending_orders) == 1
        and len(trigger_locally_cancelled) == 1
        and _release_exact_locally_cancelled_pending(
          plan,
          intent_id=current_pending_intent_id,
          client_order_id=trigger_locally_cancelled[0],
        )
      )
      if exact_local_release:
        released_zero_fill_intent_id = current_pending_intent_id
        local_pending = trigger_pending_orders[0]
        local_pending.request_metadata = {
          **dict(local_pending.request_metadata or {}),
          "execution_terminal_source": _LOCAL_OUTBOX_CANCEL_SOURCE,
          "execution_terminal_reason": _LOCAL_OUTBOX_CANCEL_REASON,
          "reconciled_zero_fill_intent_id": current_pending_intent_id,
        }
        local_pending.status_reason = _LOCAL_OUTBOX_CANCEL_REASON
        for disposition in command_dispositions:
          if disposition.get("clientOrderId") == str(
            local_pending.client_order_id or ""
          ):
            disposition.update(
              {
                "executionTerminalSource": _LOCAL_OUTBOX_CANCEL_SOURCE,
                "executionTerminalReason": _LOCAL_OUTBOX_CANCEL_REASON,
                "planPendingReleased": True,
              }
            )
      else:
        # Never turn a malformed/multiply-bound plan into a local zero-fill
        # fact.  The command remains permanently non-deliverable, but the plan
        # retains its pending gate for explicit reconciliation.
        for client_order_id in tuple(trigger_locally_cancelled):
          local_outbox = outbox_by_client.get(client_order_id)
          local_pending = next(
            (
              item
              for item in pending_orders
              if str(item.client_order_id or "") == client_order_id
            ),
            None,
          )
          if local_outbox is not None:
            local_outbox.delivery_status = "RECONCILE_REQUIRED"
            local_outbox.last_error = "quarantine_plan_pending_release_failed"
          if local_pending is not None:
            local_pending.request_metadata = {
              **dict(local_pending.request_metadata or {}),
              QUARANTINE_RECONCILE_REQUIRED_METADATA_KEY: True,
              QUARANTINE_REPAIR_REQUIRED_METADATA_KEY: True,
              QUARANTINE_REASON_METADATA_KEY: "PLAN_PENDING_RELEASE_FAILED",
            }
            local_pending.status = "RECONCILE_REQUIRED"
            local_pending.status_reason = (
              "local cancellation could not release exact exit-plan pending state"
            )
          if client_order_id not in reconcile_required:
            reconcile_required.append(client_order_id)
          for disposition in command_dispositions:
            if disposition.get("clientOrderId") == client_order_id:
              disposition.update(
                {
                  "disposition": "PLAN_PENDING_RELEASE_FAILED",
                  "planPendingReleased": False,
                }
              )
          locally_cancelled.remove(client_order_id)

    previous_state = str(control.authorization_state or "DISABLED").upper()
    previous_paused_reason = str(control.paused_reason or "")
    control.reconcile_status = "RECONCILE_REQUIRED"
    if previous_state != "KILLED":
      control.authorization_state = "PAUSED"
    _clear_controlled_window(control)
    pause_details = {
      "kind": BROKER_EXECUTION_AFTER_RELEASE,
      "reason": "broker evidence contradicted a released exit-plan proof",
      "quarantinedAt": now.isoformat(),
      "quarantineSourceSequence": max(0, int(source_sequence or 0)),
      "planId": plan_id,
      "invalidatedIntentId": str(invalidated_intent_id or ""),
      "currentPendingIntentId": current_pending_intent_id,
      "releasedZeroFillIntentId": released_zero_fill_intent_id or None,
      "evidenceKind": str(evidence_kind or "UNKNOWN").strip().upper(),
      "evidenceStatus": str(evidence_status or "UNKNOWN").strip().upper(),
      "brokerOrderId": str(broker_order_id or "") or None,
      "sourceSequence": max(0, int(source_sequence or 0)),
    }
    control.paused_reason = json.dumps(
      [pause_details],
      ensure_ascii=False,
      separators=(",", ":"),
    )[:2000]
    control.state_version = max(0, int(control.state_version or 0)) + 1

    event_details = {
      **{
        key: value
        for key, value in pause_details.items()
        if key not in {"planId", "currentPendingIntentId"}
      },
      "triggerPlanId": plan_id,
      "triggerIntentId": current_pending_intent_id,
      "evidenceKey": normalized_evidence_key,
      "previousPausedReason": previous_paused_reason or None,
      "locallyCancelledClientOrderIds": locally_cancelled,
      "reconcileRequiredClientOrderIds": reconcile_required,
      "cancelMessageIds": cancel_message_ids,
      "commandDispositions": command_dispositions,
    }
    self.db.add(
      AccountExecutionControlEvent(
        event_id=event_id,
        account_id=account_id,
        event_type=BROKER_EXECUTION_AFTER_RELEASE,
        previous_state=previous_state,
        next_state=str(control.authorization_state or ""),
        snapshot_id=control.last_snapshot_id,
        details=event_details,
        created_at=now,
      )
    )
    await self.db.flush()
    return AccountExecutionQuarantineResult(
      applied=True,
      event_id=event_id,
      account_id=account_id,
      plan_id=plan_id,
      current_pending_intent_id=current_pending_intent_id,
      released_zero_fill_intent_id=released_zero_fill_intent_id,
      locally_cancelled_client_order_ids=tuple(locally_cancelled),
      reconcile_required_client_order_ids=tuple(reconcile_required),
      cancel_message_ids=tuple(cancel_message_ids),
    )

  async def lock_command_for_delivery(
    self,
    *,
    message_id: str,
    now: datetime,
    redelivery_before: datetime,
  ) -> TradeCommandDeliveryLock:
    """Lock a candidate in account -> outbox order and apply the LIVE gate.

    Candidate discovery is deliberately unlocked.  The command is revalidated
    only after the account lock is held, so a concurrent exact invalidation
    either observes a delivered command or prevents this claim from returning
    a new LIVE PLACE_ORDER.
    """

    command, control, live_place_order, reason = await self._lock_command_account_first(
      message_id=message_id
    )
    if command is None:
      return TradeCommandDeliveryLock(None, reason)
    if live_place_order and (
      control is None or str(control.reconcile_status or "").strip().upper() != "READY"
    ):
      return TradeCommandDeliveryLock(None, "ACCOUNT_RECONCILE_REQUIRED")
    if command.expires_at <= now:
      return TradeCommandDeliveryLock(None, "COMMAND_CHANGED")
    delivery_status = str(command.delivery_status or "").strip().upper()
    eligible = bool(
      delivery_status == "QUEUED"
      or (
        delivery_status == "DELIVERED"
        and (command.delivered_at is None or command.delivered_at <= redelivery_before)
      )
    )
    if not eligible:
      return TradeCommandDeliveryLock(None, "COMMAND_NOT_DELIVERABLE")
    return TradeCommandDeliveryLock(command)


__all__ = [
  "BROKER_EXECUTION_AFTER_RELEASE",
  "QUARANTINE_CANCEL_REQUIRED_METADATA_KEY",
  "QUARANTINE_REASON_METADATA_KEY",
  "QUARANTINE_RECONCILE_REQUIRED_METADATA_KEY",
  "QUARANTINE_REPAIR_REQUIRED_METADATA_KEY",
  "QUARANTINED_ORDER_REPAIRED",
  "AccountExecutionQuarantineResult",
  "AccountExecutionQuarantineService",
  "QuarantinedOrderRepairResult",
  "TradeCommandDeliveryLock",
]
