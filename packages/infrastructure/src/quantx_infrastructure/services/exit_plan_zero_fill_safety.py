"""Fail-closed convergence for contradicted exit-plan zero-fill proofs."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from quantx_contracts import (
  ExecutionEnvironment,
  ExecutionOwnerRef,
  ExecutionOwnerType,
)
from quantx_domain.clock import utcnow
from quantx_domain.trading.exit_plan import ExitPlan, ExitPlanStatus, ExitPlanTemplate
from sqlalchemy import select
from sqlalchemy.exc import MultipleResultsFound

from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  OrderCorrelation,
  PendingTradeOrder,
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
  QUARANTINE_RECONCILE_REQUIRED_METADATA_KEY,
  QUARANTINE_REPAIR_REQUIRED_METADATA_KEY,
  AccountExecutionQuarantineService,
)
from quantx_infrastructure.services.exit_plan_authorization_service import (
  clear_exact_auto_exit_authorization,
)
from quantx_infrastructure.services.exit_plan_execution_owner import (
  durable_exit_plan_source_binding,
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
  correlation: Optional[OrderCorrelation]


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


def _typed_owner_environment(value: Any) -> Optional[tuple[str, str, str]]:
  """Return one durable owner/environment triple, or reject it.

  This helper deliberately reads only typed persistence columns.  In
  particular, request metadata is not an identity source for zero-fill
  invalidation: metadata can be retained as an audit witness, but it cannot
  select a plan or make an otherwise incomplete binding exact.
  """

  try:
    owner = ExecutionOwnerRef(
      getattr(value, "owner_type", None),
      getattr(value, "owner_id", None),
    )
    environment = ExecutionEnvironment(getattr(value, "environment", None))
  except (TypeError, ValueError):
    return None
  return owner.owner_type.value, owner.owner_id, environment.value


def _typed_source_execution(
  record: AutoExitPlanRecord,
) -> Optional[tuple[str, str, str, str]]:
  """Validate a plan's durable source owner and nullable run witness.

  ``strategy_run_id`` is a nullable witness, not a source-owner fallback.  A
  non-null witness must agree with a STRATEGY_RUN source triple; when it is
  null, the source triple still has to be complete and is never inferred from
  the plan template or request metadata.
  """

  binding = durable_exit_plan_source_binding(record)
  if binding is None:
    return None
  source_owner, environment = binding
  strategy_run_id = str(getattr(record, "strategy_run_id", None) or "")
  return (
    source_owner.owner_type.value,
    source_owner.owner_id,
    environment.value,
    strategy_run_id,
  )


_PENDING_LIFECYCLE_METADATA_KEYS = frozenset(
  {
    QUARANTINE_CANCEL_REQUIRED_METADATA_KEY,
    QUARANTINE_RECONCILE_REQUIRED_METADATA_KEY,
    QUARANTINE_REPAIR_REQUIRED_METADATA_KEY,
    QUARANTINE_REASON_METADATA_KEY,
    "account_execution_quarantine_repaired",
    "execution_terminal_source",
    "execution_terminal_reason",
    "execution_terminal_at",
    "reconciled_zero_fill_intent_id",
    "command_lifecycle_status",
    "command_lifecycle_previous_status",
    "command_lifecycle_message_id",
  }
)


def _immutable_request_metadata(value: Any) -> dict[str, Any]:
  """Project the immutable request witness from a mutable pending row."""

  if not isinstance(value, Mapping):
    return {}
  return {
    key: item
    for key, item in dict(value).items()
    if key not in _PENDING_LIFECYCLE_METADATA_KEYS
  }


async def _locked_exact_binding(
  db: Any,
  *,
  client_order_id: str,
) -> Optional[_LockedExitPlanBinding]:
  requested_client_order_id = str(client_order_id or "")
  if (
    not requested_client_order_id
    or requested_client_order_id != requested_client_order_id.strip()
  ):
    return None

  # The pending order is the first durable link keyed by client_order_id.  Its
  # typed EXIT_PLAN owner supplies the plan key; request metadata is never
  # consulted to choose that owner.
  pending_result = await db.execute(
    select(PendingTradeOrder)
    .where(PendingTradeOrder.client_order_id == requested_client_order_id)
    .with_for_update()
    .execution_options(populate_existing=True)
  )
  pending = pending_result.scalar_one_or_none()
  if pending is None:
    return None
  pending_owner = _typed_owner_environment(pending)
  if pending_owner is None:
    return None
  pending_owner_type, plan_id, pending_environment = pending_owner
  if (
    pending_owner_type != ExecutionOwnerType.EXIT_PLAN.value
    or str(pending.client_order_id or "") != requested_client_order_id
    or str(pending.side or "") != "SELL"
    or not str(pending.account_id or "")
    or not str(pending.instrument_code or "")
    or not str(pending.intent_id or "")
    or pending.strategy_order_id is not None
    or pending.strategy_run_id is not None
  ):
    return None
  intent_id = str(pending.intent_id)

  # Delivered/contradictory evidence is valid only when the exact durable
  # correlation exists.  Query by its unique client_order_id, not by its UUID
  # primary key, and fail closed on corrupted duplicate rows.
  try:
    correlation = (
      await db.execute(
        select(OrderCorrelation)
        .where(OrderCorrelation.client_order_id == requested_client_order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
      )
    ).scalar_one_or_none()
  except MultipleResultsFound:
    return None
  if correlation is None:
    return None
  correlation_owner = _typed_owner_environment(correlation)
  if correlation_owner is None:
    return None

  # LIVE binding also locks the account gate and any durable PLACE command.
  # The outbox row is optional for already persisted/replayed evidence, but if
  # present it must carry the same typed identity.
  control = None
  place_outbox = None
  if pending_environment == ExecutionEnvironment.LIVE.value:
    control = await db.get(
      AccountExecutionControl,
      str(pending.account_id),
      with_for_update=True,
      populate_existing=True,
    )
    if control is None or str(control.account_id or "") != str(pending.account_id):
      return None
    try:
      place_outbox = (
        await db.execute(
          select(TradeCommandOutbox)
          .where(TradeCommandOutbox.client_order_id == requested_client_order_id)
          .with_for_update()
          .execution_options(populate_existing=True)
        )
      ).scalar_one_or_none()
    except MultipleResultsFound:
      return None

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
  source_execution = _typed_source_execution(record)
  if source_execution is None:
    return None
  _, _, record_environment, _ = source_execution
  if record_environment != pending_environment:
    return None

  intent_owner = _typed_owner_environment(intent)
  if intent_owner is None:
    return None
  try:
    plan = ExitPlan.from_dict(dict(record.plan_state or {}))
  except (KeyError, TypeError, ValueError):
    return None
  template = plan.template
  if str(template.bucket or "") != str(record.bucket or ""):
    return None

  expected_owner = (
    ExecutionOwnerType.EXIT_PLAN.value,
    plan_id,
    pending_environment,
  )
  if (
    pending_owner != expected_owner
    or correlation_owner != expected_owner
    or intent_owner != expected_owner
    or str(correlation.client_order_id or "") != requested_client_order_id
    or str(correlation.account_id or "") != str(pending.account_id or "")
    or str(correlation.intent_id or "") != intent_id
    or str(correlation.strategy_order_id or "")
    != str(pending.strategy_order_id or "")
    or str(correlation.batch_id or "") != str(pending.batch_id or "")
    or str(correlation.bucket or "") != str(pending.bucket or "")
    or str(correlation.t_trade_role or "") != str(pending.t_trade_role or "")
    or str(correlation.risk_decision_id or "")
    != str(pending.risk_decision_id or "")
    or str(correlation.trace_id or "") != str(pending.trace_id or "")
    or correlation.substitution_plan != pending.substitution_plan
    or _immutable_request_metadata(correlation.request_metadata)
    != _immutable_request_metadata(pending.request_metadata)
    or correlation.strategy_run_id is not None
    or correlation.strategy_order_id is not None
    or intent.strategy_run_id is not None
    or str(record.plan_id or "") != plan_id
    or plan.plan_id != plan_id
    or str(record.account_id or "") != str(pending.account_id or "")
    or str(record.instrument_code or "") != str(pending.instrument_code or "")
    or str(template.account_id or "") != str(pending.account_id or "")
    or str(template.instrument_code or "") != str(pending.instrument_code or "")
    or str(intent.id or "") != intent_id
    or str(intent.account_id or "") != str(pending.account_id or "")
    or str(intent.instrument_code or "") != str(pending.instrument_code or "")
    or str(intent.direction or "") != "SELL"
  ):
    return None

  pending_broker_order_id = str(pending.broker_order_id or "")
  correlation_broker_order_id = str(correlation.broker_order_id or "")
  # ACK/early lifecycle rows may durably know the broker id on only one side;
  # a pair of present ids, however, is an immutable exact fact and must agree.
  if (
    pending_broker_order_id
    and correlation_broker_order_id
    and pending_broker_order_id != correlation_broker_order_id
  ):
    return None
  if place_outbox is not None:
    outbox_owner = _typed_owner_environment(place_outbox)
    if (
      outbox_owner != expected_owner
      or str(place_outbox.client_order_id or "") != requested_client_order_id
      or str(place_outbox.account_id or "") != str(pending.account_id or "")
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
      execution_ref=ExecutionOwnerRef(
        str(pending.owner_type or ""),
        str(pending.owner_id or ""),
      ),
      environment=ExecutionEnvironment(
        str(pending.environment or "").strip().upper()
      ),
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
  if released and str(record.environment or "").strip().upper() == "LIVE":
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
    if str(record.environment or "").strip().upper() == "LIVE":
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
  correlation: OrderCorrelation,
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
  correlation: OrderCorrelation,
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
