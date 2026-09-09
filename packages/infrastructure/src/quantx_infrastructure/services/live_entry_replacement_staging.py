"""Atomic replacement request/evidence staging; caller owns the transaction."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from quantx_domain.trading.t_assistant_execution import (
  TAssistantExecutionEvent,
  stable_manifest_hash,
)
from quantx_domain.trading.t_order_policy import TEntryOrderPolicy
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from quantx_infrastructure.models.agent_runtime import PendingTradeOrder
from quantx_infrastructure.models.risk_increase_admission import (
  AccountRiskIncreaseAdmissionBatch,
)
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantExecutionEventRecord,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from quantx_infrastructure.services.live_entry_replacement_execution_review import (
  LiveEntryReplacementExecutionReview,
)
from quantx_infrastructure.services.live_entry_request_staging import (
  LiveEntryStageResult,
)
from quantx_infrastructure.services.t_allocation_serialization import (
  allocation_evidence,
  allocation_time,
)
from quantx_infrastructure.services.trade_command_service import TradeCommandService


async def validate_staged_live_entry_replacement(
  db, *, intent, volume, limit_price, now, require_fresh=True
):
  if not db.in_transaction() or now.tzinfo is None or now.utcoffset() is None:
    raise ValueError("LIVE_REPLACEMENT_TRANSACTION_AND_AWARE_TIME_REQUIRED")
  request = (intent.intent_metadata or {}).get("risk_increase_order_request")
  if not isinstance(request, dict):
    raise ValueError("LIVE_REPLACEMENT_STAGED_REQUEST_REQUIRED")
  metadata = request.get("request_metadata") or {}
  key = metadata.get("live_entry_replacement_review_event_key")
  event = await db.scalar(
    select(TAssistantExecutionEventRecord).where(
      TAssistantExecutionEventRecord.execution_id == intent.owner_id,
      TAssistantExecutionEventRecord.event_key == key,
    )
  )
  payload = event.payload if event else {}
  evidence = payload.get("input")
  proof = evidence.get("preflight", {}) if isinstance(evidence, dict) else {}
  parent_id = request.get("t_order_parent_client_id")
  parent = await db.get(PendingTradeOrder, parent_id) if parent_id else None
  if (
    event is None
    or event.event_type != "LIVE_ENTRY_REPLACEMENT_REVIEWED"
    or payload.get("outcome") != "REVIEWED"
    or payload.get("intent_id") != intent.id
    or payload.get("request") != request
    or not isinstance(evidence, dict)
    or key
    != f"live-entry-replacement-review:{intent.id}:{stable_manifest_hash(evidence)}"
    or parent is None
    or parent.intent_id != intent.id
    or parent.owner_id != intent.owner_id
    or parent.owner_type != "T_ASSISTANT_EXECUTION"
    or parent.environment != "LIVE"
    or parent.account_id != intent.account_id
    or parent.instrument_code != intent.instrument_code
    or parent.batch_id != request.get("batch_id")
    or parent.bucket != request.get("bucket")
    or parent.t_order_attempt != 0
    or parent.t_order_parent_client_id
    or parent.t_trade_role != "ENTRY"
    or parent.side != "BUY"
    or proof.get("parent_client_order_id") != parent_id
    or proof.get("intent_id") != intent.id
    or proof.get("execution_id") != intent.owner_id
    or proof.get("user_id") != request.get("user_id")
    or parent.user_id != request.get("user_id")
    or proof.get("allocation_version") != intent.allocation_version
    or proof.get("filled_volume") != int(intent.executed_volume or 0)
    or evidence.get("allocation_decision_id") != intent.allocation_decision_id
    or evidence.get("allocation_version") != intent.allocation_version
    or request.get("intent_id") != intent.id
    or request.get("owner_id") != intent.owner_id
    or request.get("owner_type") != "T_ASSISTANT_EXECUTION"
    or request.get("environment") != "LIVE"
    or request.get("account_id") != intent.account_id
    or request.get("instrument_code") != intent.instrument_code
    or request.get("t_trade_role") != "ENTRY"
    or request.get("order_type") != "FIX_PRICE"
    or request.get("idempotency_key") != f"t-order:{intent.id}:replace:1"
    or request.get("volume") != volume
    or type(volume) is not int
    or volume <= 0
    or Decimal(str(request.get("limit_price"))) != limit_price
  ):
    raise ValueError("LIVE_REPLACEMENT_STAGED_REVIEW_CONFLICT")
  try:
    quote = datetime.fromisoformat(evidence["market"]["timestamp"])
    reviewed = datetime.fromisoformat(evidence["now"])
    original = datetime.fromisoformat(proof["original_created_at"])
    lifecycle_end = datetime.fromisoformat(proof["expires_at"])
    expiry_ms = metadata["order_expire_at_ms"]
    if (
      any(v.tzinfo is None for v in (quote, reviewed, original, lifecycle_end))
      or type(expiry_ms) is not int
      or type(proof["remaining_volume"]) is not int
      or volume > proof["remaining_volume"]
      or proof["remaining_volume"] + proof["filled_volume"] != parent.volume
      or lifecycle_end != original + timedelta(seconds=TEntryOrderPolicy().total_ttl_seconds)
      or Decimal(proof["limit_price"]) != limit_price
      or original != allocation_time(parent.t_order_original_created_at)
      or expiry_ms > int(lifecycle_end.timestamp() * 1000)
    ):
      raise ValueError("invalid clocks or volume")
  except (KeyError, TypeError, ValueError) as exc:
    raise ValueError("LIVE_REPLACEMENT_STAGED_REVIEW_INVALID") from exc
  if max(quote, reviewed, allocation_time(event.occurred_at)) > now:
    raise ValueError("LIVE_REPLACEMENT_STAGED_REVIEW_FUTURE")
  if now >= lifecycle_end or int(now.timestamp() * 1000) >= expiry_ms:
    raise ValueError("LIVE_REPLACEMENT_STAGED_REVIEW_EXPIRED")
  if require_fresh and (now - min(quote, reviewed)).total_seconds() > 2:
    raise ValueError("LIVE_REPLACEMENT_STAGED_REVIEW_EXPIRED")
  return request


async def stage_live_entry_replacement(
  db, *, client_order_id, market_data, market_mark_reader, now, validate_market
):
  if not db.in_transaction():
    raise ValueError("LIVE_ENTRY_STAGE_TRANSACTION_REQUIRED")
  market_data = deepcopy(market_data)
  validate_market()
  async with db.begin_nested():
    result = await LiveEntryReplacementExecutionReview(db).review(
      client_order_id=client_order_id,
      market_data=market_data,
      market_mark_reader=market_mark_reader,
      now=now,
    )
    parent = await db.get(PendingTradeOrder, client_order_id)
    if (
      parent is None
      or parent.owner_type != "T_ASSISTANT_EXECUTION"
      or parent.environment != "LIVE"
    ):
      raise ValueError("LIVE_REPLACEMENT_STAGE_SCOPE_INVALID")
    intent = await db.get(TradeIntentRecord, parent.intent_id)
    if intent is None or intent.owner_id != parent.owner_id:
      raise ValueError("LIVE_REPLACEMENT_STAGE_SCOPE_INVALID")
    evidence = dict(
      market=allocation_evidence(market_data),
      now=allocation_evidence(now),
      preflight=allocation_evidence(result.preflight),
      allocation_decision_id=intent.allocation_decision_id,
      allocation_version=intent.allocation_version,
      review=dict(
        outcome=result.outcome,
        reason_codes=list(result.reason_codes),
        request=allocation_evidence(result.request),
        portfolio_input_fingerprint=result.portfolio_input_fingerprint,
      ),
    )
    key = f"live-entry-replacement-review:{intent.id}:{stable_manifest_hash(evidence)}"
    existing = await db.scalar(
      select(TAssistantExecutionEventRecord).where(
        TAssistantExecutionEventRecord.execution_id == parent.owner_id,
        TAssistantExecutionEventRecord.event_key == key,
      )
    )
    previous = deepcopy(
      (intent.intent_metadata or {}).get("risk_increase_order_request")
    )
    if existing is not None:
      if existing.payload.get("request") is not None:
        await validate_staged_live_entry_replacement(
          db,
          intent=intent,
          volume=result.request.volume,
          limit_price=Decimal(str(result.request.price)),
          now=now,
        )
      validate_market()
      return LiveEntryStageResult("ALREADY_RECORDED", intent.id, key)
    request = None
    if result.outcome == "REVIEWED":
      proof = result.preflight
      if (
        proof is None
        or proof.parent_client_order_id != client_order_id
        or proof.intent_id != intent.id
      ):
        raise ValueError("LIVE_REPLACEMENT_STAGE_PROOF_REQUIRED")
      if intent.status not in {
        "EXECUTION_PENDING",
        "PARTIAL_FILLED",
        "EXECUTION_READY",
      }:
        raise ValueError("LIVE_REPLACEMENT_STAGE_INTENT_RELEASED")
      execution = await TAssistantExecutionRepository(db).get(parent.owner_id)
      metadata = dict(intent.intent_metadata or {})
      restaging = (
        isinstance(previous, dict)
        and previous.get("t_order_parent_client_id") == client_order_id
      )
      if not restaging:
        if intent.status == "EXECUTION_READY":
          raise ValueError("LIVE_REPLACEMENT_STAGE_PARENT_REQUEST_REQUIRED")
        if intent.admission_batch_id:
          admission = await db.get(
            AccountRiskIncreaseAdmissionBatch,
            intent.admission_batch_id,
            with_for_update=True,
          )
          if admission is None or admission.status != "COMMITTED":
            raise ValueError("LIVE_REPLACEMENT_STAGE_PARENT_ADMISSION_UNFINISHED")
        intent.admission_batch_id = intent.admission_rank = None
        intent.admission_policy_version = intent.admission_input_fingerprint = None
        metadata.pop("risk_increase_order_request", None)
        intent.intent_metadata = metadata
        intent.status = "APPROVED"
      intent.updated_at = now.astimezone(UTC).replace(tzinfo=None)
      flag_modified(intent, "updated_at")
      business_key = f"t-order:{intent.id}:replace:1"
      await TradeCommandService(db)._stage_risk_increase_order_request(
        accepted_intent=intent,
        business_idempotency_key=business_key,
        user_id=result.user_id,
        account_id=intent.account_id,
        instrument_code=intent.instrument_code,
        owner_type="T_ASSISTANT_EXECUTION",
        owner_id=parent.owner_id,
        environment="LIVE",
        idempotency_key=business_key,
        trace_id=parent.trace_id or "",
        strategy_run_id="",
        strategy_order_id="",
        batch_id=parent.batch_id,
        bucket=intent.bucket,
        t_trade_role="ENTRY",
        risk_decision_id=result.risk.risk_decision_id,
        substitution_plan=result.risk.substitution_plan,
        policy_version=execution.frozen_config_version,
        order_type="FIX_PRICE",
        limit_price=Decimal(str(result.request.price)),
        volume=result.request.volume,
        t_order_parent_client_id=client_order_id,
        commit=False,
        request_metadata={
          **{
            k: metadata[k]
            for k in (
              "exit_plan_template",
              "commission_rate",
              "minimum_commission",
              "min_commission",
              "stamp_tax_rate",
              "transfer_fee_rate",
            )
            if k in metadata
          },
          "live_entry_replacement_review_event_key": key,
          "portfolio_input_fingerprint": result.portfolio_input_fingerprint,
          "order_expire_at_ms": result.request.metadata["order_expire_at_ms"],
          "quote_timestamp": market_data.timestamp.isoformat(),
          "t_entry_order_policy_version": TEntryOrderPolicy().version,
          "t_order_reference_price": str(market_data.ask_price[0]),
          "t_order_price_tick": str(market_data.price_tick),
          "t_order_limit_up": market_data.limit_up,
          "t_order_limit_down": market_data.limit_down,
        },
      )
      request = intent.intent_metadata["risk_increase_order_request"]
    await TAssistantExecutionRepository(db).append_event(
      TAssistantExecutionEvent(
        parent.owner_id,
        key,
        "LIVE_ENTRY_REPLACEMENT_REVIEWED",
        now,
        dict(
          intent_id=intent.id,
          input=evidence,
          outcome=result.outcome,
          reason_codes=list(result.reason_codes),
          request=request,
          previous_request=previous,
          sizing=allocation_evidence(result.sizing),
          risk=allocation_evidence(result.risk),
        ),
      )
    )
    await db.flush()
    validate_market()
    return LiveEntryStageResult("STAGED" if request else result.outcome, intent.id, key)
