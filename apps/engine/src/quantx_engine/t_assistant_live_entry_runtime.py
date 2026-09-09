"""Stage reviewed LIVE READY intents for the shared account dispatcher."""

from dataclasses import dataclass
from datetime import UTC

from quantx_application.t_trade_v3.portfolio_reference import aware_time
from quantx_domain.trading.t_assistant_execution import TAssistantExecutionEvent
from quantx_infrastructure.models.agent_runtime import (
  OrderCorrelation,
  PendingTradeOrder,
)
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantExecutionEventRecord,
  TAssistantExecutionRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from quantx_infrastructure.services.live_entry_request_staging import (
  stage_live_entry_request,
)
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from quantx_engine.t_assistant_live_entry_recovery import (
  assert_live_entry_order_bindings,
)


@dataclass(frozen=True)
class LiveEntryDispatchResult:
  status: str
  staged: tuple[str, ...] = ()
  terminalized: tuple[str, ...] = ()


class TAssistantLiveEntryRuntime:
  def __init__(self, *, session_factory, clock, review_adapter_factory):
    self.sessions, self.clock, self.adapters = (
      session_factory,
      clock,
      review_adapter_factory,
    )

  async def dispatch(self, *, execution_id, validate_market):
    validate_market()
    async with self.sessions() as db, db.begin():
      probe = await db.get(TAssistantExecutionRecord, execution_id)
      if probe is None or probe.environment != "LIVE":
        return LiveEntryDispatchResult("BLOCKED")
      head = await db.get(
        TTradeGlobalConfig,
        probe.config_id,
        with_for_update=True,
        populate_existing=True,
      )
      source = await db.get(
        TAssistantExecutionRecord,
        execution_id,
        with_for_update=True,
        populate_existing=True,
      )
      if (
        head is None
        or not head.enabled
        or head.strategy_run_id
        or head.account_id != source.account_id
        or head.desired_environment != "LIVE"
        or head.active_config_version_id != source.config_version_id
        or source.status != "RUNNING"
        or source.entry_readiness != "READY"
        or source.entry_authorization != "MANUAL_CONFIRM"
        or source.scorer_mode != "RULE_ONLY"
      ):
        return LiveEntryDispatchResult("BLOCKED")
      intents = list(
        await db.scalars(
          select(TradeIntentRecord)
          .where(
            TradeIntentRecord.owner_type == "T_ASSISTANT_EXECUTION",
            TradeIntentRecord.owner_id == execution_id,
            TradeIntentRecord.environment == "LIVE",
            TradeIntentRecord.account_id == source.account_id,
            TradeIntentRecord.direction == "BUY",
            TradeIntentRecord.status == "EXECUTION_READY",
          )
          .order_by(TradeIntentRecord.created_at, TradeIntentRecord.id)
          .with_for_update()
          .execution_options(populate_existing=True)
        )
      )
      intents = [
        intent
        for intent in intents
        if dict(intent.intent_metadata or {}).get("risk_increase_order_request") is None
      ]
      if intents:
        await assert_live_entry_order_bindings(
          db, execution_id=execution_id, account_id=source.account_id
        )
      adapter = self.adapters(db)
      staged, terminalized, validators = [], [], []
      for intent in intents:
        has_order = bool(intent.order_id or intent.executed_volume)
        for model in (PendingTradeOrder, OrderCorrelation):
          has_order = (
            has_order
            or await db.scalar(
              select(model).where(model.intent_id == intent.id).limit(1)
            )
            is not None
          )
        if has_order:
          continue
        now = aware_time(self.clock())
        try:
          inputs = await adapter.prepare(
            execution_id=execution_id, intent_id=intent.id, now=now
          )
        except ValueError as exc:
          if str(exc) != "LIVE_ENTRY_LATEST_MARKET_REQUIRED":
            raise
          intent.status = "CANCELLED"
          intent.updated_at = now.astimezone(UTC).replace(tzinfo=None)
          flag_modified(intent, "updated_at")
          await TAssistantExecutionRepository(db).append_event(
            TAssistantExecutionEvent(
              execution_id,
              f"live-entry-market-blocked:{intent.id}",
              "LIVE_ENTRY_REVIEW_BLOCKED",
              now,
              {
                "intent_id": intent.id,
                "outcome": "CANCELLED",
                "reason_codes": [str(exc)],
                "follow_up": "REBUILD_CANDIDATE",
              },
            )
          )
          terminalized.append(intent.id)
          continue
        result = await stage_live_entry_request(
          db,
          execution_id=execution_id,
          intent_id=intent.id,
          gate_input=inputs.gate_input,
          market_data=inputs.market_data,
          market_mark_reader=adapter.market_mark_reader,
          now=inputs.now,
          validate_market=inputs.validate_market,
        )
        validators.append(inputs.validate_market)
        if (
          dict(intent.intent_metadata or {}).get("risk_increase_order_request")
          is not None
        ):
          staged.append(intent.id)
          continue
        event = await db.scalar(
          select(TAssistantExecutionEventRecord).where(
            TAssistantExecutionEventRecord.execution_id == execution_id,
            TAssistantExecutionEventRecord.event_key == result.event_key,
          )
        )
        if (
          event is None
          or event.event_type != "LIVE_ENTRY_REVIEWED"
          or event.payload.get("intent_id") != intent.id
        ):
          raise ValueError("LIVE_ENTRY_REVIEW_AUDIT_REQUIRED")
        outcome = event.payload["outcome"]
        if outcome not in {"REJECT", "DELAY", "KILL_SWITCH"}:
          raise ValueError("LIVE_ENTRY_REVIEW_OUTCOME_INVALID")
        intent.status = (
          "CANCELLED"
          if outcome == "DELAY"
          else "EXPIRED"
          if any("EXPIRED" in code for code in event.payload["reason_codes"])
          else "REJECTED"
        )
        intent.updated_at = inputs.now.astimezone(UTC).replace(tzinfo=None)
        flag_modified(intent, "updated_at")
        terminalized.append(intent.id)
      await db.flush()
      for validator in validators:
        validator()
      validate_market()
    return LiveEntryDispatchResult(
      "PROCESSED" if staged or terminalized else "IDLE",
      tuple(staged),
      tuple(terminalized),
    )
