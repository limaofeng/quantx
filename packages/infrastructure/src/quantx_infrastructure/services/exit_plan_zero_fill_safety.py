"""Fail-closed convergence for contradicted exit-plan zero-fill proofs."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from quantx_domain.clock import utcnow
from quantx_domain.trading.exit_plan import ExitPlan, ExitPlanStatus, ExitPlanTemplate
from sqlalchemy import select

from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  PendingTradeOrder,
  StrategyOrderCorrelation,
  TradeCommandOutbox,
)
from quantx_infrastructure.models.auto_exit_plan import (
  AutoExitPlanEvent,
  AutoExitPlanRecord,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.account_execution_quarantine_service import (
  QUARANTINE_CANCEL_REQUIRED_METADATA_KEY,
  QUARANTINE_REASON_METADATA_KEY,
  AccountExecutionQuarantineService,
)
from quantx_infrastructure.services.exit_plan_authorization_service import (
  clear_exact_auto_exit_authorization,
)
from quantx_infrastructure.services.trade_intent_processor import (
  LOCAL_AGENT_PRE_EXECUTION_ZERO_FILL_SOURCE,
  LOCAL_OUTBOX_EXPIRED_ZERO_FILL_SOURCE,
)

ZERO_FILL_PROOF_INVALIDATED_PREFIX = "ZERO_FILL_PROOF_INVALIDATED_AFTER_RELEASE:"
EXIT_FILL_INTENT_MISMATCH_PREFIX = "EXIT_FILL_INTENT_MISMATCH:"
ZERO_FILL_PROOF_INVALIDATION_METADATA_KEY = "zero_fill_proof_invalidation"
ZERO_FILL_CONTRADICTION_ORDER_STATUSES = frozenset(
  {
    "PENDING",
    "REPORTED",
    "SUBMITTED",
    "ACCEPTED",
    "WORKING",
    "PARTIAL",
    "PARTIAL_FILLED",
    "FILLED",
    "RECONCILE_REQUIRED",
  }
)
_LOCAL_ZERO_FILL_SOURCES = frozenset(
  {
    LOCAL_OUTBOX_EXPIRED_ZERO_FILL_SOURCE,
    LOCAL_AGENT_PRE_EXECUTION_ZERO_FILL_SOURCE,
  }
)
_AUTHORITATIVE_BROKER_TERMINAL_STATUSES = frozenset(
  {"FILLED", "CANCELLED", "CANCELED", "REJECTED", "EXPIRED"}
)


@dataclass(frozen=True)
class ExitPlanZeroFillInvalidation:
  exact_binding: bool = False
  proof_invalidated: bool = False
  released: bool = False
  benign_terminal_replay: bool = False
  plan_id: str = ""
  intent_id: str = ""
  strategy_run_id: str = ""
  error_code: str = ""


@dataclass(frozen=True)
class _LockedExitPlanBinding:
  control: Optional[AccountExecutionControl]
  place_outbox: Optional[TradeCommandOutbox]
  record: AutoExitPlanRecord
  plan: ExitPlan
  pending: PendingTradeOrder
  intent: TradeIntentRecord
  correlation: Optional[StrategyOrderCorrelation]


def zero_fill_invalidation_error(intent_id: str) -> str:
  return f"{ZERO_FILL_PROOF_INVALIDATED_PREFIX}{str(intent_id or 'MISSING')}"


def exit_fill_intent_mismatch_error(intent_id: str, current_intent_id: str) -> str:
  return (
    f"{EXIT_FILL_INTENT_MISMATCH_PREFIX}{str(intent_id or 'MISSING')}:"
    f"CURRENT_PENDING:{str(current_intent_id or 'NONE')}"
  )


def exit_plan_runtime_event_marker_key(plan_id: str, runtime_event_key: str) -> str:
  identity = f"{str(plan_id or '')}\0{str(runtime_event_key or '')}".encode("utf-8")
  return f"strategy-exit-runtime-event:{hashlib.sha256(identity).hexdigest()}"


async def _locked_exact_binding(
  db: Any,
  *,
  client_order_id: str,
) -> Optional[_LockedExitPlanBinding]:
  candidate = (
    await db.execute(
      select(
        PendingTradeOrder.account_id,
        PendingTradeOrder.execution_mode,
        PendingTradeOrder.side,
        PendingTradeOrder.intent_id,
        PendingTradeOrder.request_metadata,
      ).where(
        PendingTradeOrder.client_order_id == str(client_order_id or "")
      )
    )
  ).one_or_none()
  if candidate is None:
    return None
  account_id, execution_mode, side, intent_id, request_metadata = candidate
  candidate_metadata = dict(request_metadata or {})
  candidate_plan_id = str(candidate_metadata.get("exit_plan_id") or "").strip()
  if (
    not candidate_plan_id
    or not str(intent_id or "").strip()
    or str(side or "").upper() != "SELL"
    or str(candidate_metadata.get("owner_type") or "").upper() != "EXIT_PLAN"
    or str(candidate_metadata.get("owner_id") or "") != candidate_plan_id
  ):
    return None
  live_order = str(execution_mode or "").strip().lower() == "live"
  control = None
  place_outbox = None
  if live_order:
    control = await db.get(
      AccountExecutionControl,
      str(account_id or ""),
      with_for_update=True,
      populate_existing=True,
    )
    if control is None:
      return None
    place_outbox = (
      await db.execute(
        select(TradeCommandOutbox)
        .where(TradeCommandOutbox.client_order_id == str(client_order_id or ""))
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
      )
    ).scalar_one_or_none()
  pending = await db.get(
    PendingTradeOrder,
    str(client_order_id or ""),
    with_for_update=True,
    populate_existing=True,
  )
  if pending is None:
    return None
  pending_metadata = dict(pending.request_metadata or {})
  plan_id = str(pending_metadata.get("exit_plan_id") or "").strip()
  intent_id = str(pending.intent_id or "").strip()
  if (
    not plan_id
    or not intent_id
    or str(pending.side or "").upper() != "SELL"
    or str(pending_metadata.get("owner_type") or "").upper() != "EXIT_PLAN"
    or str(pending_metadata.get("owner_id") or "") != plan_id
  ):
    return None

  correlation = (
    await db.execute(
      select(StrategyOrderCorrelation)
      .where(StrategyOrderCorrelation.client_order_id == pending.client_order_id)
      .limit(1)
      .with_for_update()
      .execution_options(populate_existing=True)
    )
  ).scalar_one_or_none()
  intent = await db.get(
    TradeIntentRecord,
    intent_id,
    with_for_update=True,
    populate_existing=True,
  )
  record = await db.get(
    AutoExitPlanRecord,
    plan_id,
    with_for_update=True,
    populate_existing=True,
  )
  if intent is None or record is None:
    return None
  try:
    plan = ExitPlan.from_dict(dict(record.plan_state or {}))
  except (KeyError, TypeError, ValueError):
    return None
  template = plan.template
  intent_metadata = dict(intent.intent_metadata or {})
  run_id = str(record.strategy_run_id or "").strip()
  if not (
    plan.plan_id == plan_id
    and str(template.account_id or "") == str(record.account_id or "")
    and str(template.instrument_code or "").upper()
    == str(record.instrument_code or "").upper()
    and str(template.run_id or "").strip() == run_id
    and str(record.account_id or "") == str(pending.account_id or "")
    and str(record.instrument_code or "").upper()
    == str(pending.instrument_code or "").upper()
    and str(intent.id or "") == intent_id
    and str(intent.owner_type or "").upper() == "EXIT_PLAN"
    and str(intent.owner_id or "") == plan_id
    and str(intent.account_id or "") == str(pending.account_id or "")
    and str(intent.instrument_code or "").upper()
    == str(pending.instrument_code or "").upper()
    and str(intent.direction or "").upper() == "SELL"
    and str(intent_metadata.get("owner_type") or "").upper() == "EXIT_PLAN"
    and str(intent_metadata.get("owner_id") or "") == plan_id
    and str(intent_metadata.get("exit_plan_id") or "") == plan_id
  ):
    return None
  if run_id:
    correlation_metadata = (
      dict(correlation.request_metadata or {}) if correlation is not None else {}
    )
    if not (
      correlation is not None
      and str(pending.strategy_run_id or "") == run_id
      and str(intent.strategy_run_id or "") == run_id
      and str(correlation.strategy_run_id or "") == run_id
      and str(correlation.intent_id or "") == intent_id
      and str(correlation.account_id or "") == str(record.account_id or "")
      and str(correlation.client_order_id or "")
      == str(pending.client_order_id or "")
      and str(correlation_metadata.get("owner_type") or "").upper()
      == "EXIT_PLAN"
      and str(correlation_metadata.get("owner_id") or "") == plan_id
      and str(correlation_metadata.get("exit_plan_id") or "") == plan_id
    ):
      return None
  elif correlation is not None or str(pending.strategy_run_id or "") or str(
    intent.strategy_run_id or ""
  ):
    return None
  return _LockedExitPlanBinding(
    control=control,
    place_outbox=place_outbox,
    record=record,
    plan=plan,
    pending=pending,
    intent=intent,
    correlation=correlation,
  )


def has_exit_plan_zero_fill_proof_metadata(metadata: Mapping[str, Any]) -> bool:
  qmt_proof = metadata.get("qmt_zero_fill_reconciliation")
  invalidation = metadata.get(ZERO_FILL_PROOF_INVALIDATION_METADATA_KEY)
  source = str(metadata.get("execution_terminal_source") or "").strip().upper()
  return bool(
    isinstance(qmt_proof, Mapping)
    or source in _LOCAL_ZERO_FILL_SOURCES
    or isinstance(invalidation, Mapping)
  )


def _proof_metadata(intent: TradeIntentRecord) -> tuple[dict[str, Any], bool]:
  metadata = dict(intent.intent_metadata or {})
  has_proof = has_exit_plan_zero_fill_proof_metadata(metadata)
  return metadata, has_proof


def _is_benign_finalized_order_replay(
  binding: _LockedExitPlanBinding,
  *,
  evidence_status: str,
  cumulative_filled_volume: Optional[int],
) -> bool:
  if str(evidence_status or "").strip().upper() != "FILLED":
    return False
  intent_id = str(binding.intent.id or "")
  if not intent_id or intent_id == str(binding.plan.pending_intent_id or ""):
    return False
  if str(binding.pending.status or "").strip().upper() != "FILLED":
    return False
  try:
    cumulative = int(cumulative_filled_volume)  # type: ignore[arg-type]
  except (TypeError, ValueError, OverflowError):
    return False
  durable_executed = max(0, int(binding.intent.executed_volume or 0))
  return bool(
    durable_executed > 0
    and 0 <= cumulative <= durable_executed
    and cumulative <= max(0, int(binding.pending.volume or 0))
  )


def _released_evidence_may_still_be_working(
  binding: _LockedExitPlanBinding,
  *,
  evidence_kind: str,
  evidence_status: str,
  proof_metadata: Mapping[str, Any],
) -> bool:
  if evidence_kind == "ORDER":
    # Reaching this helper already proves the ORDER sequence is newer than the
    # terminal zero-fill proof.  A newer working lifecycle therefore
    # contradicts that old proof and must not be suppressed by it.
    return evidence_status not in _AUTHORITATIVE_BROKER_TERMINAL_STATUSES
  if evidence_kind == "TRADE":
    # A real execution after a terminal proof also means the old order may
    # retain a working remainder; cancel conservatively.
    return True
  qmt_proof = proof_metadata.get("qmt_zero_fill_reconciliation")
  if isinstance(qmt_proof, Mapping):
    proof_status = str(qmt_proof.get("broker_terminal_status") or "").upper()
    proof_broker_order_id = str(qmt_proof.get("broker_order_id") or "")
    if (
      proof_status in _AUTHORITATIVE_BROKER_TERMINAL_STATUSES
      and proof_broker_order_id
      and proof_broker_order_id == str(binding.pending.broker_order_id or "")
    ):
      return False
  # Any other fact that contradicts a released zero-fill outcome may mean the
  # native PLACE_ORDER left the Agent.  In particular, COMMAND_ACK has no
  # broker id yet, so persist the cancellation obligation now and let the
  # first exact ORDER report bind the broker id and enqueue the cancel.
  return True


async def _queue_released_evidence_cancel(
  db: Any,
  *,
  binding: _LockedExitPlanBinding,
  evidence_kind: str,
  evidence_status: str,
  broker_order_id: str,
  proof_metadata: Mapping[str, Any],
) -> str:
  if not _released_evidence_may_still_be_working(
    binding,
    evidence_kind=evidence_kind,
    evidence_status=evidence_status,
    proof_metadata=proof_metadata,
  ):
    return ""
  pending = binding.pending
  pending_metadata = {
    **dict(pending.request_metadata or {}),
    QUARANTINE_CANCEL_REQUIRED_METADATA_KEY: True,
    QUARANTINE_REASON_METADATA_KEY: "RELEASED_EVIDENCE_ORDER_CANCEL_REQUIRED",
  }
  pending.request_metadata = pending_metadata
  pending.status = "CANCEL_REQUESTED"
  pending.status_reason = "cancel released order contradicted by broker evidence"
  durable_broker_order_id = str(
    broker_order_id or pending.broker_order_id or ""
  ).strip()
  if not durable_broker_order_id:
    return ""

  from quantx_infrastructure.services.trade_command_service import (
    AgentUnavailableError,
    TradeCommandService,
  )

  try:
    cancel = await TradeCommandService(db).enqueue_cancel(
      user_id=str(pending.user_id or ""),
      account_id=str(pending.account_id or ""),
      broker_order_id=durable_broker_order_id,
      idempotency_key=(
        f"entry-plan-cancel:{pending.client_order_id}:{durable_broker_order_id}"
      ),
      execution_mode="live",
      commit_transaction=False,
    )
  except AgentUnavailableError:
    # The account quarantine and durable pending cancellation obligation must
    # still commit while the Agent is offline.  A later ORDER/full-snapshot
    # replay retries the same stable business cancellation identity.
    return ""
  return str(cancel.message_id or "")


async def is_exact_finalized_exit_order_replay(
  db: Any,
  *,
  client_order_id: str,
  evidence_status: str,
  cumulative_filled_volume: Optional[int],
) -> bool:
  binding = await _locked_exact_binding(db, client_order_id=client_order_id)
  return bool(
    binding is not None
    and _is_benign_finalized_order_replay(
      binding,
      evidence_status=evidence_status,
      cumulative_filled_volume=cumulative_filled_volume,
    )
  )


async def invalidate_exit_plan_zero_fill_proof(
  db: Any,
  *,
  client_order_id: str,
  evidence_kind: str,
  evidence_status: str,
  evidence_key: str,
  broker_order_id: str = "",
  source_sequence: int = 0,
  cumulative_filled_volume: Optional[int] = None,
) -> ExitPlanZeroFillInvalidation:
  """Invalidate one exact proof and burn a released plan in the same transaction."""

  binding = await _locked_exact_binding(db, client_order_id=client_order_id)
  if binding is None:
    return ExitPlanZeroFillInvalidation()
  record = binding.record
  plan = binding.plan
  intent = binding.intent
  intent_id = str(intent.id or "")
  normalized_kind = str(evidence_kind or "UNKNOWN").strip().upper()
  normalized_status = str(evidence_status or "UNKNOWN").strip().upper()
  normalized_source_sequence = max(0, int(source_sequence or 0))
  if normalized_kind == "ORDER":
    if normalized_status not in ZERO_FILL_CONTRADICTION_ORDER_STATUSES:
      return ExitPlanZeroFillInvalidation(
        exact_binding=True,
        plan_id=plan.plan_id,
        intent_id=intent_id,
        strategy_run_id=str(record.strategy_run_id or ""),
      )
    stored_sequence = max(0, int(binding.pending.last_source_sequence or 0))
    if (
      normalized_source_sequence
      and stored_sequence
      and normalized_source_sequence <= stored_sequence
    ):
      return ExitPlanZeroFillInvalidation(
        exact_binding=True,
        plan_id=plan.plan_id,
        intent_id=intent_id,
        strategy_run_id=str(record.strategy_run_id or ""),
      )
  if normalized_kind == "ORDER" and _is_benign_finalized_order_replay(
    binding,
    evidence_status=normalized_status,
    cumulative_filled_volume=cumulative_filled_volume,
  ):
    return ExitPlanZeroFillInvalidation(
      exact_binding=True,
      benign_terminal_replay=True,
      plan_id=plan.plan_id,
      intent_id=intent_id,
      strategy_run_id=str(record.strategy_run_id or ""),
    )
  metadata, has_proof = _proof_metadata(intent)
  qmt_proof_metadata = metadata.get("qmt_zero_fill_reconciliation")
  if isinstance(qmt_proof_metadata, Mapping):
    proof_sequence = max(0, int(qmt_proof_metadata.get("source_sequence") or 0))
    proof_matches_binding = bool(
      str(qmt_proof_metadata.get("exit_plan_id") or "") == plan.plan_id
      and str(qmt_proof_metadata.get("intent_id") or "") == intent_id
      and str(qmt_proof_metadata.get("account_id") or "")
      == str(record.account_id or "")
      and str(qmt_proof_metadata.get("instrument_code") or "").upper()
      == str(record.instrument_code or "").upper()
      and str(qmt_proof_metadata.get("client_order_id") or "")
      == str(binding.pending.client_order_id or "")
      and (
        not str(broker_order_id or "")
        or str(qmt_proof_metadata.get("broker_order_id") or "")
        == str(broker_order_id or "")
      )
    )
    if not proof_matches_binding or (
      normalized_kind == "ORDER"
      and normalized_source_sequence
      and proof_sequence
      and normalized_source_sequence <= proof_sequence
    ):
      return ExitPlanZeroFillInvalidation(
        exact_binding=True,
        plan_id=plan.plan_id,
        intent_id=intent_id,
        strategy_run_id=str(record.strategy_run_id or ""),
      )
  remembered_zero_fill_release = intent_id in {
    str(value) for value in list(plan.reconciled_zero_fill_intent_ids or [])
  }
  current_intent_id = str(plan.pending_intent_id or "")
  released_zero_fill = bool(
    remembered_zero_fill_release
    or (has_proof and intent_id and intent_id != current_intent_id)
  )
  broker_intent_mismatch = bool(
    intent_id
    and intent_id != current_intent_id
    and (
      normalized_kind == "TRADE"
      or (
        normalized_kind == "ORDER"
        and normalized_status in ZERO_FILL_CONTRADICTION_ORDER_STATUSES
      )
    )
  )
  released = bool(released_zero_fill or broker_intent_mismatch)
  if not has_proof and not broker_intent_mismatch:
    return ExitPlanZeroFillInvalidation(
      exact_binding=True,
      plan_id=plan.plan_id,
      intent_id=intent_id,
      strategy_run_id=str(record.strategy_run_id or ""),
    )

  normalized_evidence_key = str(evidence_key or "").strip()
  if not normalized_evidence_key:
    return ExitPlanZeroFillInvalidation(
      exact_binding=True,
      plan_id=plan.plan_id,
      intent_id=intent_id,
      strategy_run_id=str(record.strategy_run_id or ""),
    )
  audit_identity = hashlib.sha256(
    f"{plan.plan_id}\0{intent_id}\0{normalized_evidence_key}".encode("utf-8")
  ).hexdigest()
  business_key = f"zero-fill-proof-invalidated:{audit_identity}"
  existing_event = await db.scalar(
    select(AutoExitPlanEvent)
    .where(AutoExitPlanEvent.business_key == business_key)
    .limit(1)
  )
  if existing_event is not None:
    existing_payload = dict(existing_event.payload or {})
    return ExitPlanZeroFillInvalidation(
      exact_binding=True,
      released=bool(existing_payload.get("released")),
      plan_id=plan.plan_id,
      intent_id=intent_id,
      strategy_run_id=str(record.strategy_run_id or ""),
      error_code=str(existing_payload.get("error_code") or ""),
    )

  evidence_cancel_message_id = ""
  if released and str(record.execution_mode or "").strip().lower() == "live":
    evidence_cancel_message_id = await _queue_released_evidence_cancel(
      db,
      binding=binding,
      evidence_kind=normalized_kind,
      evidence_status=normalized_status,
      broker_order_id=str(broker_order_id or ""),
      proof_metadata=metadata,
    )

  evidence_at = utcnow()
  qmt_proof = metadata.pop("qmt_zero_fill_reconciliation", None)
  error_code = (
    zero_fill_invalidation_error(intent_id)
    if released_zero_fill
    else exit_fill_intent_mismatch_error(intent_id, current_intent_id)
    if broker_intent_mismatch
    else ""
  )
  invalidation = {
    "invalidated_at": evidence_at.isoformat(),
    "evidence_kind": normalized_kind,
    "evidence_status": normalized_status,
    "evidence_source_sequence": normalized_source_sequence,
    "reported_broker_order_id": str(broker_order_id or ""),
    "released": bool(released),
    "release_kind": (
      "ZERO_FILL_PROOF"
      if released_zero_fill
      else "FINALIZED_OR_REPLACED_INTENT"
      if broker_intent_mismatch
      else "NOT_RELEASED"
    ),
    "plan_id": plan.plan_id,
    "intent_id": intent_id,
    "client_order_id": str(binding.pending.client_order_id or ""),
    "strategy_run_id": str(record.strategy_run_id or ""),
    "error_code": error_code,
    "evidence_cancel_message_id": evidence_cancel_message_id or None,
  }
  if isinstance(qmt_proof, Mapping):
    qmt_invalidation = {**dict(qmt_proof), **invalidation}
    metadata["qmt_zero_fill_reconciliation_invalidated"] = qmt_invalidation
  metadata[ZERO_FILL_PROOF_INVALIDATION_METADATA_KEY] = invalidation
  intent.intent_metadata = metadata
  intent.status = "RECONCILE_REQUIRED"
  intent.notes = (
    "QMT_ZERO_FILL_PROOF_CONTRADICTED_BY_BROKER_EXECUTION"
    if normalized_kind == "TRADE"
    else "QMT_ZERO_FILL_PROOF_CONTRADICTED_BY_BROKER_ORDER"
    if normalized_kind == "ORDER"
    else "ZERO_FILL_PROOF_CONTRADICTED_BY_ACCEPTED_ACK"
  )

  if released:
    previous_state = dict(record.plan_state or {})
    clear_exact_auto_exit_authorization(record, bump_state_version=False)
    plan = ExitPlan.from_dict(dict(record.plan_state or {}))
    if (
      released_zero_fill
      and intent_id not in plan.reconciled_zero_fill_intent_ids
    ):
      # The durable intent proof plus a changed/cleared pending owner is the
      # authority.  Retain it in the domain state too so an independent
      # StrategyRun replay converges to the same sticky error.
      plan.reconciled_zero_fill_intent_ids.append(intent_id)
    plan.status = ExitPlanStatus.ERROR
    plan.error_message = error_code
    plan.template = ExitPlanTemplate.from_dict(
      {**plan.template.to_dict(), "auto_exit_authorized": False}
    )
    next_state = plan.to_dict()
    if next_state != previous_state:
      record.state_version = max(1, int(record.state_version or 1)) + 1
    record.plan_state = next_state
    record.status = ExitPlanStatus.ERROR.value
    record.enabled = False
    record.last_error = error_code
    if str(record.execution_mode or "").strip().lower() == "live":
      await AccountExecutionQuarantineService(
        db
      ).quarantine_released_exit_plan(
        plan=record,
        invalidated_intent_id=intent_id,
        evidence_key=str(evidence_key or ""),
        evidence_kind=normalized_kind,
        evidence_status=normalized_status,
        evidence_client_order_id=str(binding.pending.client_order_id or ""),
        broker_order_id=str(broker_order_id or ""),
        source_sequence=normalized_source_sequence,
      )

  db.add(
    AutoExitPlanEvent(
      event_id=str(uuid.uuid4()),
      business_key=business_key,
      plan_id=plan.plan_id,
      event_type="ZERO_FILL_PROOF_INVALIDATED",
      payload={
        **invalidation,
        "error_code": error_code or None,
        "evidence_key": normalized_evidence_key,
      },
      created_at=evidence_at,
    )
  )
  return ExitPlanZeroFillInvalidation(
    exact_binding=True,
    proof_invalidated=True,
    released=released,
    plan_id=plan.plan_id,
    intent_id=intent_id,
    strategy_run_id=str(record.strategy_run_id or ""),
    error_code=error_code,
  )


async def runtime_zero_fill_invalidation(
  db: Any,
  *,
  correlation: StrategyOrderCorrelation,
) -> Optional[dict[str, Any]]:
  """Return an exact invalidation marker eligible for runtime reconciliation."""

  binding = await _locked_exact_binding(
    db,
    client_order_id=str(correlation.client_order_id or ""),
  )
  if binding is None or binding.correlation is None:
    return None
  metadata = dict(binding.intent.intent_metadata or {})
  invalidation = metadata.get(ZERO_FILL_PROOF_INVALIDATION_METADATA_KEY)
  if not isinstance(invalidation, Mapping):
    return None
  if (
    str(invalidation.get("plan_id") or "") != binding.plan.plan_id
    or str(invalidation.get("intent_id") or "") != str(binding.intent.id or "")
    or str(invalidation.get("client_order_id") or "")
    != str(binding.pending.client_order_id or "")
  ):
    return None
  return dict(invalidation)


async def mark_released_zero_fill_runtime_order_canonicalized(
  db: Any,
  *,
  correlation: StrategyOrderCorrelation,
  runtime_event_key: str,
) -> bool:
  """Mark a staged ORDER already committed into the canonical ERROR aggregate."""

  invalidation = await runtime_zero_fill_invalidation(db, correlation=correlation)
  if not invalidation or invalidation.get("released") is not True:
    return False
  plan_id = str(invalidation.get("plan_id") or "")
  intent_id = str(invalidation.get("intent_id") or "")
  record = await db.get(AutoExitPlanRecord, plan_id, with_for_update=True)
  if record is None:
    return False
  error_code = zero_fill_invalidation_error(intent_id)
  try:
    plan = ExitPlan.from_dict(dict(record.plan_state or {}))
  except (KeyError, TypeError, ValueError):
    return False
  if (
    str(record.status or "").upper() != ExitPlanStatus.ERROR.value
    or plan.status != ExitPlanStatus.ERROR
    or str(plan.error_message or "") != error_code
    or str(record.last_error or "") != error_code
  ):
    return False
  marker_key = exit_plan_runtime_event_marker_key(plan_id, runtime_event_key)
  existing = await db.scalar(
    select(AutoExitPlanEvent.event_id)
    .where(AutoExitPlanEvent.business_key == marker_key)
    .limit(1)
  )
  if existing is None:
    db.add(
      AutoExitPlanEvent(
        event_id=str(uuid.uuid4()),
        business_key=marker_key,
        plan_id=plan_id,
        event_type="ZERO_FILL_RUNTIME_ORDER_CANONICALIZED",
        payload={
          "intent_id": intent_id,
          "strategy_run_id": str(record.strategy_run_id or ""),
          "runtime_event_key": str(runtime_event_key or ""),
          "error_code": error_code,
        },
        created_at=utcnow(),
      )
    )
  return True
