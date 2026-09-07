"""Real PAPER ledger delivery into public intent, batch and ExitPlan tables."""

import pytest
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_domain.brokers.base import OrderRequest, OrderType, PriceType
from quantx_domain.strategies.base import ExitPlanIntentOrigin, TradeIntent
from quantx_domain.trading.exit_plan import (
  ExitDecision,
  ExitPlan,
  ExitPlanBook,
  ExitPlanTemplate,
  ExitRuleSpec,
  ExitRuleType,
)
from quantx_domain.trading.order_sizer import OrderSizer
from quantx_domain.trading.risk_checker import TradingRiskChecker
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import TTradeBatch
from quantx_infrastructure.models.auto_exit_plan import (
  AutoExitPlanEvent,
  AutoExitPlanRecord,
)
from quantx_infrastructure.models.paper_execution import (
  PaperExecutionAccountRecord,
  PaperExecutionFillRecord,
  PaperExecutionOrderRecord,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.trade_intent_repository import (
  TradeIntentRepository,
)
from quantx_infrastructure.services.auto_exit_plan_service import AutoExitPlanService
from quantx_infrastructure.services.paper_execution_ledger import PaperExecutionLedger
from quantx_infrastructure.services.paper_receipt_convergence import (
  PaperReceiptConvergence,
)
from quantx_infrastructure.services.trade_intent_intake import trade_intent_record_data
from sqlalchemy import func, select

from tests.infrastructure.test_paper_execution_ledger import (
  allocation_sessions as _allocation_sessions,
)
from tests.infrastructure.test_paper_execution_ledger import (
  base_sessions as _base_sessions,
)
from tests.infrastructure.test_paper_execution_ledger import quote, setup
from tests.infrastructure.test_paper_execution_ledger import (
  sessions as _ledger_sessions,
)

allocation_sessions = _allocation_sessions
base_sessions = _base_sessions
ledger_sessions = _ledger_sessions


@pytest.fixture
async def sessions(ledger_sessions):
  async with ledger_sessions.kw["bind"].begin() as connection:
    await connection.run_sync(
      lambda sync: Base.metadata.create_all(
        sync,
        tables=[
          TTradeBatch.__table__,
          AutoExitPlanEvent.__table__,
        ],
      )
    )
  return ledger_sessions


def enrich_intent(intent):
  template = ExitPlanTemplate(
    plan_id="paper-plan",
    source_type="T_TRADE_BATCH",
    source_id="paper-batch",
    account_id="account-1",
    instrument_code=intent.instrument_code,
    bucket="swing",
    rules=[
      ExitRuleSpec(
        strategy=ExitRuleType.HARD_STOP, rule_id="stop", parameters={"stop_price": 9}
      )
    ],
    metadata={"source_execution_ref": intent.execution_ref.to_dict()},
  )
  intent.metadata.update(
    t_trade_role="entry",
    t_batch_id=template.source_id,
    exit_plan_id=template.plan_id,
    exit_plan_template=template.to_dict(),
  )


async def test_real_entry_fills_create_public_plan_and_replay_without_duplication(
  sessions,
):
  sink = PaperReceiptConvergence()
  execution_id, args = await setup(sessions, sink, enrich_intent=enrich_intent)
  async with sessions() as db, db.begin():
    await PaperExecutionLedger(db, receipt_sink=sink).place_order(
      execution_id=execution_id, **args
    )
    assert await db.get(AutoExitPlanRecord, "paper-plan") is None
    assert (await db.get(TradeIntentRecord, args["intent_id"])).status == "ROUTED"
  for second, expected in ((1, 50), (2, 100)):
    async with sessions() as db, db.begin():
      await PaperExecutionLedger(db, receipt_sink=sink).process_quote(
        execution_id=execution_id,
        event_key=f"quote-{second}",
        accepted_at=(quote(second)).timestamp,
        quote=quote(second),
      )
      plan = await db.get(AutoExitPlanRecord, "paper-plan")
      batch = await db.get(TTradeBatch, "paper-batch")
      intent = await db.get(TradeIntentRecord, args["intent_id"])
      assert (
        plan.protected_volume
        == batch.entry_filled_volume
        == intent.executed_volume
        == expected
      )
      assert plan.strategy_run_id is None and batch.strategy_run_id is None
      assert (
        plan.source_execution_owner_id
        == batch.source_execution_owner_id
        == execution_id
      )
      assert plan.environment == batch.environment == "PAPER"
      assert batch.status == ("ENTRY_PARTIAL" if second == 1 else "OPEN")
  async with sessions() as db, db.begin():
    count = await db.scalar(select(func.count()).select_from(AutoExitPlanEvent))
    version = (await db.get(AutoExitPlanRecord, "paper-plan")).state_version
    assert (
      await PaperExecutionLedger(db, receipt_sink=sink).process_quote(
        execution_id=execution_id,
        event_key="quote-1",
        accepted_at=(quote(1)).timestamp,
        quote=quote(1),
      )
    ).duplicate
    assert await db.scalar(select(func.count()).select_from(AutoExitPlanEvent)) == count
    assert (await db.get(AutoExitPlanRecord, "paper-plan")).state_version == version


async def test_partial_entry_cancel_keeps_protection_and_open_batch(sessions):
  sink = PaperReceiptConvergence()
  execution_id, args = await setup(sessions, sink, enrich_intent=enrich_intent)
  async with sessions() as db, db.begin():
    ledger = PaperExecutionLedger(db, receipt_sink=sink)
    await ledger.place_order(execution_id=execution_id, **args)
    await ledger.process_quote(
      execution_id=execution_id,
      event_key="partial",
      accepted_at=(quote(1)).timestamp,
      quote=quote(1),
    )
    await ledger.cancel(
      execution_id=execution_id,
      event_key="cancel",
      order_id=args["order_id"],
      now=quote(2).timestamp,
    )
    batch = await db.get(TTradeBatch, "paper-batch")
    plan = await db.get(AutoExitPlanRecord, "paper-plan")
    assert batch.status == "OPEN" and batch.entry_filled_volume == 50
    assert plan.remaining_volume == 50 and plan.status == "ACTIVE"


async def test_public_plan_failure_rolls_back_fill_and_all_projections(sessions):
  sink = PaperReceiptConvergence()
  execution_id, args = await setup(sessions, sink, enrich_intent=enrich_intent)
  async with sessions() as db, db.begin():
    await PaperExecutionLedger(db, receipt_sink=sink).place_order(
      execution_id=execution_id, **args
    )

  async def failed(db, scope, result):
    await sink(db, scope, result)
    assert (await db.get(AutoExitPlanRecord, "paper-plan")).protected_volume == 50
    raise RuntimeError("public projection failure")

  async with sessions() as db, db.begin():
    with pytest.raises(RuntimeError, match="public projection failure"):
      await PaperExecutionLedger(db, receipt_sink=failed).process_quote(
        execution_id=execution_id,
        event_key="partial",
        accepted_at=(quote(1)).timestamp,
        quote=quote(1),
      )
  async with sessions() as db:
    assert await db.get(AutoExitPlanRecord, "paper-plan") is None
    assert await db.scalar(select(func.count()).select_from(AutoExitPlanEvent)) == 0
    assert (
      await db.scalar(select(func.count()).select_from(PaperExecutionFillRecord)) == 0
    )
    assert (await db.get(PaperExecutionAccountRecord, execution_id)).revision == 1
    assert (
      await db.get(PaperExecutionOrderRecord, args["order_id"])
    ).filled_volume == 0
    assert (await db.get(TTradeBatch, "paper-batch")).entry_filled_volume == 0
    assert (await db.get(TradeIntentRecord, args["intent_id"])).executed_volume == 0


async def test_entry_costs_must_match_the_frozen_matching_policy(sessions):
  def nonmatching(intent):
    enrich_intent(intent)
    intent.metadata["exit_plan_template"]["costs"]["commission_rate"] = 0.001

  sink = PaperReceiptConvergence()
  execution_id, args = await setup(sessions, sink, enrich_intent=nonmatching)
  async with sessions() as db, db.begin():
    with pytest.raises(ValueError, match="MATCHING_COST_POLICY_CONFLICT"):
      await PaperExecutionLedger(db, receipt_sink=sink).place_order(
        execution_id=execution_id, **args
      )
    assert await db.get(TTradeBatch, "paper-batch") is None
    assert await db.get(PaperExecutionOrderRecord, args["order_id"]) is None
    assert (await db.get(PaperExecutionAccountRecord, execution_id)).revision == 0


async def test_inconsistent_fill_amount_cannot_change_public_protection(sessions):
  sink = PaperReceiptConvergence()
  execution_id, args = await setup(sessions, sink, enrich_intent=enrich_intent)
  async with sessions() as db, db.begin():
    await PaperExecutionLedger(db, receipt_sink=sink).place_order(
      execution_id=execution_id, **args
    )

  async def inconsistent(db, scope, result):
    fill = await db.get(PaperExecutionFillRecord, result.trades[0].trade_id)
    fill.price = 1
    await db.flush()
    await sink(db, scope, result)

  async with sessions() as db, db.begin():
    with pytest.raises(ValueError, match="PAPER_RECEIPT_FILL_CONFLICT"):
      await PaperExecutionLedger(db, receipt_sink=inconsistent).process_quote(
        execution_id=execution_id,
        event_key="partial",
        accepted_at=(quote(1)).timestamp,
        quote=quote(1),
      )
    assert await db.get(AutoExitPlanRecord, "paper-plan") is None
    assert (await db.get(PaperExecutionAccountRecord, execution_id)).revision == 1


async def test_public_plan_sell_receipt_closes_batch_without_pending_mismatch(sessions):
  sink = PaperReceiptConvergence()
  execution_id, args = await setup(sessions, sink, enrich_intent=enrich_intent)
  source = ExecutionOwnerRef("T_ASSISTANT_EXECUTION", execution_id)
  async with sessions() as db, db.begin():
    ledger = PaperExecutionLedger(db, receipt_sink=sink)
    await ledger.place_order(execution_id=execution_id, **args)
    await ledger.process_quote(
      execution_id=execution_id,
      event_key="buy-filled",
      accepted_at=(quote(1, depth=400)).timestamp,
      quote=quote(1, depth=400),
    )
    record = await db.get(AutoExitPlanRecord, "paper-plan")
    plan = ExitPlan.from_dict(record.plan_state)
    book = ExitPlanBook([plan])
    decision = ExitDecision(plan.plan_id, "stop", "HARD_STOP", "test exit", 100, 1)
    book.mark_intent(decision, "paper-exit-intent")
    await AutoExitPlanService().persist_execution_plan_state(
      execution_ref=source,
      environment=ExecutionEnvironment.PAPER,
      plan_state=plan.to_dict(),
      expected_state_version=record.state_version,
      evaluated_at=quote(2).timestamp,
      event_type="EXIT_INTENT_CREATED",
      event_business_key="exit-reserved",
      db=db,
      commit=False,
    )
    intent = TradeIntent(
      strategy_id="",
      instrument_code="600000.SH",
      direction="SELL",
      bucket="swing",
      reason="test exit",
      target_volume=100,
      intent_id="paper-exit-intent",
      created_at=quote(2).timestamp,
      execution_ref=ExecutionOwnerRef("EXIT_PLAN", plan.plan_id),
      origin=ExitPlanIntentOrigin(plan.plan_id, source),
      metadata={"bucket": "swing"},
    )
    values = trade_intent_record_data(
      intent, status="EXECUTION_READY", environment=ExecutionEnvironment.PAPER
    )
    values["account_id"] = "account-1"
    await TradeIntentRepository(db).accept_intents_idempotent([values])
    account = {"cash": 99000.0, "total_asset": 110000.0}
    position = {
      "long_volume": 1100,
      "available_volume": 1000,
      "swing_available_volume": 1000,
    }
    draft = OrderSizer().draft_intent(intent, OrderType.SELL, 9.85, account, position)
    request = OrderRequest(
      instrument_code="600000.SH",
      order_type=OrderType.SELL,
      price_type=PriceType.LIMIT,
      volume=draft.sized_volume,
      price=9.85,
      execution_ref=intent.execution_ref,
      environment=ExecutionEnvironment.PAPER,
      metadata={
        "bucket": "swing",
        "intent_id": intent.intent_id,
        "order_expire_at_ms": int(quote(60).timestamp.timestamp() * 1000),
      },
    )
    risk = await TradingRiskChecker(
      strict_market_data=True, strict_limit_data=True
    ).evaluate_order(
      request,
      account=account,
      position=position,
      market_data=quote(2),
      current_time=quote(2).timestamp,
    )
    state = await ledger.get_snapshot(execution_id=execution_id)
    await ledger.place_order(
      execution_id=execution_id,
      event_key="sell-order",
      order_id="paper-sell",
      intent_id=intent.intent_id,
      order_attempt=0,
      request=request,
      sizing_evidence=draft,
      risk_evidence=risk,
      expected_revision=state["revision"],
      expected_snapshot_hash=state["snapshot_hash"],
      now=quote(2).timestamp,
    )
  async with sessions() as db, db.begin():
    await PaperExecutionLedger(db, receipt_sink=sink).process_quote(
      execution_id=execution_id,
      event_key="sell-filled",
      accepted_at=(quote(3)).timestamp,
      quote=quote(3),
    )
    plan = await db.get(AutoExitPlanRecord, "paper-plan")
    batch = await db.get(TTradeBatch, "paper-batch")
    assert plan.status == "COMPLETED" and plan.remaining_volume == 0
    assert batch.status == "CLOSED" and batch.exit_filled_volume == 100
    assert not plan.plan_state["pending_intent_id"]
    assert (await db.get(TradeIntentRecord, "paper-exit-intent")).status == "FILLED"
