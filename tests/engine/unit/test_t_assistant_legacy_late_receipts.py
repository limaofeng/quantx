"""Actual runtime-event staging after legacy retirement; isolated durable stores."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from quantx_engine import report_processor
from quantx_engine import strategy_manager as manager_module
from quantx_engine.strategy_executor import StrategyExecutor
from quantx_engine.t_assistant_legacy_completion import complete_legacy_t_drain
from quantx_infrastructure.models.agent_runtime import (
  OrderCorrelation,
  StrategyRuntimeEvent,
  TTradeRolloutEvent,
)
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from sqlalchemy import select

from tests.engine.test_entry_plan_broker_zero_fill_reconciliation import (
  _late_exit_trade_report,
  _terminal_report,
)
from tests.engine.unit.test_t_assistant_legacy_completion import (
  complete,
  seed_completion,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["ORDER", "TRADE", "NEW_TRADE"])
async def test_retired_source_receipts_keep_original_owner_and_proof(monkeypatch, kind):
  engine, sessions, clock = await seed_completion(monkeypatch)
  executor = StrategyExecutor.__new__(StrategyExecutor)
  executor.runs = {}
  monkeypatch.setattr(
    manager_module, "strategy_manager", SimpleNamespace(executor=executor)
  )
  monkeypatch.setattr(report_processor, "AsyncSessionLocal", sessions)
  monkeypatch.setattr(report_processor, "utcnow", lambda: clock.replace(tzinfo=None))
  try:
    async with sessions() as db, db.begin():
      correlation = await db.get(OrderCorrelation, "correlation-1")
      # The base completion fixture uses short business keys for readability.
      # Here persist the real production keys before certifying the source cut.
      for event in (await db.scalars(select(StrategyRuntimeEvent))).all():
        item = event.payload["report"]
        event.business_key = report_processor._runtime_business_key(
          event.event_type, correlation, item
        )
        event.payload = report_processor._event_payload(
          correlation, item, business_key=event.business_key
        )
      original = await complete(db, clock)
      intent = await db.get(TradeIntentRecord, "intent-1")
      intent_before = (intent.status, intent.executed_volume)
      plan_before = deepcopy((await db.get(AutoExitPlanRecord, "exit-1")).plan_state)
    if kind == "ORDER":
      report = _terminal_report("CANCELLED")
      report.payload["order"]["traded_volume"] = 40
    else:
      report = _late_exit_trade_report(
        execution_id="trade-2" if kind == "NEW_TRADE" else "trade-1",
        volume=10 if kind == "NEW_TRADE" else 40,
      )
      report.payload["execution"].update(order_type=23, traded_price=10)
    await report_processor._stage_runtime_events(report)
    await report_processor._drain_runtime_events()
    async with sessions() as db, db.begin():
      events = (await db.scalars(select(StrategyRuntimeEvent))).all()
      assert len(events) == (3 if kind == "NEW_TRADE" else 2)
      assert all(
        (e.owner_type, e.owner_id, e.environment) == ("STRATEGY_RUN", "plan-1", "LIVE")
        for e in events
      )
      pending = [e for e in events if e.application_status != "APPLIED"]
      assert len(pending) == int(kind == "NEW_TRADE")
      cut = await db.get(TTradeRolloutEvent, "legacy-t-completed:plan-1")
      assert cut.details == original
      plan = await db.get(AutoExitPlanRecord, "exit-1")
      assert plan.plan_state == plan_before
      assert plan.source_execution_owner_id == "plan-1"
      if kind != "NEW_TRADE":
        intent = await db.get(TradeIntentRecord, "intent-1")
        assert (intent.status, intent.executed_volume) == intent_before
        assert (
          await complete_legacy_t_drain(
            db,
            **original["request"],
            actor_id="user-1",
            now=clock,
            revalidate_completed=True,
          )
          == original
        )
      else:
        with pytest.raises(ValueError, match="LEGACY_T_SETTLEMENT_RUNTIME_BACKLOG"):
          await complete_legacy_t_drain(
            db,
            **original["request"],
            actor_id="user-1",
            now=clock,
            revalidate_completed=True,
          )
    assert executor.runs == {}
  finally:
    await engine.dispose()
