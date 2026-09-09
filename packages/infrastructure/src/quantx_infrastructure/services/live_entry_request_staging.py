"""Atomically record a LIVE review and a public account-queue request."""

from dataclasses import dataclass
from datetime import UTC
from decimal import Decimal

from quantx_domain.trading.t_assistant_execution import (
  TAssistantExecutionEvent,
  stable_manifest_hash,
)
from quantx_domain.trading.t_order_policy import TEntryOrderPolicy
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantExecutionEventRecord,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from quantx_infrastructure.services.live_entry_execution_review import (
  LiveEntryExecutionReview,
)
from quantx_infrastructure.services.t_allocation_serialization import (
  allocation_evidence,
)
from quantx_infrastructure.services.trade_command_service import TradeCommandService


@dataclass(frozen=True)
class LiveEntryStageResult:
  status: str
  intent_id: str
  event_key: str


async def stage_live_entry_request(
  db,
  *,
  execution_id,
  intent_id,
  gate_input,
  market_data,
  market_mark_reader,
  now,
  validate_market,
):
  """Caller commits. A synchronous witness check fences all newly staged writes."""
  if not db.in_transaction():
    raise ValueError("LIVE_ENTRY_STAGE_TRANSACTION_REQUIRED")
  validate_market()
  async with db.begin_nested():
    result = await LiveEntryExecutionReview(db).review(
      execution_id=execution_id,
      intent_id=intent_id,
      gate_input=gate_input,
      market_data=market_data,
      market_mark_reader=market_mark_reader,
      now=now,
    )
    intent = await db.get(TradeIntentRecord, intent_id)
    if (
      intent is None or intent.owner_id != execution_id or intent.environment != "LIVE"
    ):
      raise ValueError("LIVE_ENTRY_STAGE_INTENT_SCOPE_INVALID")
    gate = allocation_evidence(gate_input)
    gate["quality_fields"] = sorted(gate_input.quality_fields)
    gate["capabilities"]["required_fields"] = sorted(
      gate_input.capabilities.required_fields
    )
    gate["capabilities"]["optional_fields"] = sorted(
      gate_input.capabilities.optional_fields
    )
    input_evidence = {
      "gate": gate,
      "market": allocation_evidence(market_data),
      "now": allocation_evidence(now),
      "allocation_decision_id": intent.allocation_decision_id,
      "allocation_version": intent.allocation_version,
      "review": {
        "outcome": result.outcome,
        "reason_codes": list(result.reason_codes),
        "price": result.request.price if result.request else None,
        "volume": result.request.volume if result.request else None,
        "portfolio_input_fingerprint": result.request.metadata.get(
          "portfolio_input_fingerprint"
        )
        if result.request
        else None,
      },
    }
    event_key = f"live-entry-review:{intent_id}:{stable_manifest_hash(input_evidence)}"
    existing = await db.scalar(
      select(TAssistantExecutionEventRecord).where(
        TAssistantExecutionEventRecord.execution_id == execution_id,
        TAssistantExecutionEventRecord.event_key == event_key,
      )
    )
    if existing is not None:
      recorded_request = existing.payload.get("request")
      if recorded_request != dict(intent.intent_metadata or {}).get(
        "risk_increase_order_request"
      ):
        raise ValueError("LIVE_ENTRY_STAGE_REPLAY_CONFLICT")
      validate_market()
      return LiveEntryStageResult("ALREADY_RECORDED", intent_id, event_key)
    if (
      dict(intent.intent_metadata or {}).get("risk_increase_order_request") is not None
    ):
      raise ValueError("LIVE_ENTRY_STAGE_REQUEST_ALREADY_EXISTS")
    request = None
    if result.outcome == "REVIEWED":
      metadata = dict(intent.intent_metadata or {})
      # This field is the public config revision, separate from T's named order policy.
      execution = await TAssistantExecutionRepository(db).get(execution_id)
      intent.updated_at = now.astimezone(UTC).replace(tzinfo=None)
      flag_modified(intent, "updated_at")
      await TradeCommandService(db)._stage_risk_increase_order_request(
        accepted_intent=intent,
        business_idempotency_key=f"t-entry:{intent.id}",
        user_id=result.user_id,
        account_id=intent.account_id,
        instrument_code=intent.instrument_code,
        owner_type="T_ASSISTANT_EXECUTION",
        owner_id=execution_id,
        environment="LIVE",
        idempotency_key=f"t-entry:{intent.id}",
        trace_id=str(intent.trace_id or ""),
        strategy_run_id="",
        strategy_order_id="",
        batch_id=metadata["t_batch_id"],
        bucket=intent.bucket,
        t_trade_role="ENTRY",
        risk_decision_id=result.risk.risk_decision_id,
        substitution_plan=result.risk.substitution_plan,
        policy_version=execution.frozen_config_version,
        order_type="FIX_PRICE",
        limit_price=Decimal(str(result.request.price)),
        volume=result.request.volume,
        request_metadata={
          **metadata,
          **result.request.metadata,
          "live_entry_review_event_key": event_key,
          "t_entry_order_policy_version": TEntryOrderPolicy().version,
          "t_order_reference_price": str(market_data.price),
          "t_order_price_tick": str(market_data.price_tick),
          "t_order_limit_up": market_data.limit_up,
          "t_order_limit_down": market_data.limit_down,
        },
        commit=False,
      )
      request = intent.intent_metadata["risk_increase_order_request"]
    await TAssistantExecutionRepository(db).append_event(
      TAssistantExecutionEvent(
        execution_id,
        event_key,
        "LIVE_ENTRY_REVIEWED",
        now,
        {
          "intent_id": intent_id,
          "input": input_evidence,
          "outcome": result.outcome,
          "reason_codes": list(result.reason_codes),
          "sizing": allocation_evidence(result.sizing),
          "risk": allocation_evidence(result.risk),
          "request": request,
        },
      )
    )
    validate_market()
    await db.flush()
    return LiveEntryStageResult(
      "STAGED" if request else result.outcome, intent_id, event_key
    )
