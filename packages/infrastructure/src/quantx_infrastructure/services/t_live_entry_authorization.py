"""Final manual T entry authority; caller holds the admission transaction."""

from decimal import Decimal

from quantx_infrastructure.models.t_allocation import (
  TAllocationBatchRecord,
  TAllocationDecisionRecord,
)
from quantx_infrastructure.models.t_assistant_execution import TAssistantExecutionRecord
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.services.exit_plan_authorization_service import (
  build_t_trade_entry_exit_authorization_envelope,
)
from quantx_infrastructure.services.t_allocation_serialization import allocation_time
from quantx_infrastructure.services.t_entry_confirmation import (
  entry_confirmation_event,
  validate_entry_challenge,
)


async def authorize_live_entry(
  db, *, intent, account_id, instrument_code, volume, limit_price, now
):
  return await _authorize_live_entry(
    db,
    intent=intent,
    account_id=account_id,
    instrument_code=instrument_code,
    volume=volume,
    limit_price=limit_price,
    now=now,
    allowed_statuses={"EXECUTION_READY"},
  )


async def _authorize_live_entry(
  db,
  *,
  intent,
  account_id,
  instrument_code,
  volume,
  limit_price,
  now,
  allowed_statuses,
):
  if not db.in_transaction():
    raise ValueError("T_ENTRY_CALLER_TRANSACTION_REQUIRED")
  if now.tzinfo is None or now.utcoffset() is None:
    raise ValueError("T_ENTRY_AWARE_TIME_REQUIRED")
  metadata = dict(intent.intent_metadata or {})
  if (
    intent.owner_type != "T_ASSISTANT_EXECUTION"
    or intent.environment != "LIVE"
    or intent.strategy_run_id
    or intent.direction != "BUY"
    or intent.account_id != account_id
    or intent.instrument_code != instrument_code
    or intent.status not in allowed_statuses
    or str(metadata.get("t_trade_role") or "").upper() != "ENTRY"
  ):
    raise ValueError("T_ENTRY_AUTHORIZATION_SCOPE_INVALID")
  probe = await db.get(TAssistantExecutionRecord, intent.owner_id)
  if probe is None:
    raise ValueError("T_ENTRY_SOURCE_NOT_READY")
  head = await db.get(
    TTradeGlobalConfig, probe.config_id, with_for_update=True, populate_existing=True
  )
  source = await db.get(
    TAssistantExecutionRecord,
    intent.owner_id,
    with_for_update=True,
    populate_existing=True,
  )
  if (
    source is None
    or head is None
    or not head.enabled
    or head.strategy_run_id
    or head.account_id != account_id
    or head.desired_environment != "LIVE"
    or head.active_config_version_id != source.config_version_id
    or source.account_id != account_id
    or source.environment != "LIVE"
    or source.entry_authorization != "MANUAL_CONFIRM"
    or source.status != "RUNNING"
    or source.entry_readiness != "READY"
    or source.entry_readiness_as_of is None
    or allocation_time(source.entry_readiness_as_of) > now
  ):
    raise ValueError("T_ENTRY_SOURCE_NOT_READY")
  event = await entry_confirmation_event(db, intent)
  if (
    event is None
    or event.event_type != "LIVE_ENTRY_CONFIRMED"
    or event.payload.get("intent_id") != intent.id
    or type(event.payload.get("allocation_version")) is not int
    or event.payload["allocation_version"] >= intent.allocation_version
    or allocation_time(event.occurred_at) > now
  ):
    raise ValueError("T_ENTRY_POST_CONFIRMATION_ALLOCATION_REQUIRED")
  challenge = await db.get(
    TradeConfirmationChallenge, event.payload.get("challenge_id")
  )
  consumed_at = validate_entry_challenge(challenge, intent, now=now)
  if allocation_time(event.occurred_at) < consumed_at:
    raise ValueError("T_ENTRY_CONFIRMATION_EVIDENCE_INVALID")
  decision = await db.get(TAllocationDecisionRecord, intent.allocation_decision_id)
  batch = (
    await db.get(TAllocationBatchRecord, decision.allocation_batch_id)
    if decision
    else None
  )
  if (
    decision is None
    or batch is None
    or batch.status != "COMMITTED"
    or batch.environment != "LIVE"
    or batch.execution_id != intent.owner_id
    or batch.cycle_id != intent.allocation_cycle_id
    or decision.intent_id != intent.id
    or decision.instrument_code != instrument_code
    or decision.intent_version + 1 != intent.allocation_version
    or decision.action not in {"ALLOW", "CAP"}
    or not allocation_time(event.occurred_at)
    <= allocation_time(batch.created_at)
    <= allocation_time(batch.committed_at)
    <= now
    or not allocation_time(decision.created_at)
    <= now
    < allocation_time(decision.expires_at)
  ):
    raise ValueError("T_ENTRY_POST_CONFIRMATION_ALLOCATION_REQUIRED")
  subject = build_t_trade_entry_exit_authorization_envelope(intent).subject
  filled = int(intent.executed_volume or 0)
  if (
    type(volume) is not int
    or volume <= 0
    or filled < 0
    or filled + volume > int(subject["max_protected_volume"])
  ):
    raise ValueError("T_ENTRY_AUTHORIZED_VOLUME_EXCEEDED")
  reference = Decimal(str(subject["entry_reference_price"]))
  deviation = Decimal(str(subject["entry_max_price_deviation_bps"]))
  if (
    not limit_price.is_finite()
    or limit_price <= 0
    or limit_price > reference * (1 + deviation / Decimal(10000))
    or limit_price * (filled + volume) > decision.allocated_amount_cap
  ):
    raise ValueError("T_ENTRY_AUTHORIZED_PRICE_OR_AMOUNT_EXCEEDED")
  return challenge.user_id
