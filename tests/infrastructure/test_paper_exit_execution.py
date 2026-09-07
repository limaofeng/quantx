"""Public exit routing/recovery over real SQLite PAPER orders and receipts."""

import os
import re
from dataclasses import asdict, replace
from datetime import UTC, datetime

import pytest
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_domain.trading.exit_plan import (
  ExitDecision,
  ExitEvaluationContext,
  ExitPlan,
  ExitPlanBook,
)
from quantx_infrastructure.models.agent_runtime import TTradeBatch
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.paper_execution import (
  PaperExecutionAccountRecord,
  PaperExecutionOrderRecord,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.auto_exit_plan_service import AutoExitPlanService
from quantx_infrastructure.services.paper_execution_ledger import PaperExecutionLedger
from quantx_infrastructure.services.paper_receipt_convergence import (
  PaperReceiptConvergence,
)
from quantx_infrastructure.services.trade_intent_processor import TradeIntentProcessor
from sqlalchemy import event, func, select

from tests.infrastructure import test_paper_receipt_convergence as receipt_tests
from tests.infrastructure.test_paper_receipt_convergence import (
  enrich_intent,
  quote,
  setup,
)

allocation_sessions = receipt_tests.allocation_sessions
base_sessions = receipt_tests.base_sessions
ledger_sessions = receipt_tests.ledger_sessions
sessions = receipt_tests.sessions


@pytest.fixture(autouse=True)
def local_sessions(monkeypatch, sessions):
  from quantx_infrastructure.services import (
    auto_exit_plan_service,
    trade_intent_processor,
  )

  monkeypatch.setattr(auto_exit_plan_service, "AsyncSessionLocal", sessions)
  monkeypatch.setattr(trade_intent_processor, "AsyncSessionLocal", sessions)
  from tests.infrastructure import (
    test_paper_execution_ledger,
    test_t_allocation_repository,
    test_t_assistant_runtime_repository,
    test_t_intent_atomic_intake,
  )

  for module in (
    test_paper_execution_ledger,
    test_t_allocation_repository,
    test_t_assistant_runtime_repository,
    test_t_intent_atomic_intake,
  ):
    monkeypatch.setattr(module, "NOW", datetime(2026, 9, 3, 1, 30, tzinfo=UTC))
  from quantx_domain.trading.t_assistant_execution import TAssistantConfigVersion
  from quantx_infrastructure.models.t_assistant_execution import (
    TAssistantConfigVersionRecord,
  )

  options = {"protected_core_volume": 0}
  original_version = test_t_allocation_repository._version

  def frozen_version(config_id):
    values = asdict(original_version(config_id))
    values.pop("config_snapshot_hash")
    values["canonical_payload"]["t_trading_envelope_policy"] = {
      "version": "exit-envelope-v1",
      "protected_core_volume": options["protected_core_volume"],
      "max_symbol_t_amount": "10000",
      "max_entry_volume": 1000,
    }
    return TAssistantConfigVersion.create(**values)

  monkeypatch.setattr(test_t_allocation_repository, "_version", frozen_version)

  def historical_availability(_mapper, _connection, target):
    value = datetime(2026, 9, 2, tzinfo=UTC)
    target.created_at = (
      value
      if target.__table__.c.created_at.type.timezone
      else value.replace(tzinfo=None)
    )

  event.listen(TAssistantConfigVersionRecord, "before_insert", historical_availability)
  try:
    yield options
  finally:
    event.remove(
      TAssistantConfigVersionRecord, "before_insert", historical_availability
    )


async def prepared(sessions, *, stopped=False, reserve=True, bucket="swing"):
  sink = PaperReceiptConvergence()

  def triggered_template(intent):
    intent.bucket = bucket
    enrich_intent(intent)
    intent.metadata["exit_plan_template"]["bucket"] = bucket
    intent.metadata["exit_plan_template"]["rules"][0]["parameters"]["stop_price"] = 10

  from tests.infrastructure.test_paper_execution_ledger import seed_values

  seed = seed_values()
  state = seed["bucket_checkpoint"]["instruments"]["600000.SH"].pop("swing")
  state["bucket"] = bucket
  seed["bucket_checkpoint"]["instruments"]["600000.SH"][bucket] = state
  scope, args = await setup(
    sessions,
    sink,
    enrich_intent=triggered_template,
    allocation_at=quote(0).timestamp,
    admission_at=quote(0).timestamp,
    initial_seed=seed,
  )
  args["request"].metadata["bucket"] = bucket
  decision = ExitDecision("paper-plan", "stop", "HARD_STOP", "test exit", 100, 1)
  async with sessions() as db, db.begin():
    ledger = PaperExecutionLedger(db, receipt_sink=sink)
    await ledger.place_order(execution_id=scope, **args)
    await ledger.process_quote(
      execution_id=scope, event_key="buy-fill", quote=quote(1, depth=400)
    )
    record = await db.get(AutoExitPlanRecord, "paper-plan")
    plan = ExitPlan.from_dict(record.plan_state)
    if not reserve:
      return scope, decision
    ExitPlanBook([plan]).mark_intent(decision, "sell-intent")
    await AutoExitPlanService().persist_execution_plan_state(
      execution_ref=ExecutionOwnerRef("T_ASSISTANT_EXECUTION", scope),
      environment=ExecutionEnvironment.PAPER,
      plan_state=plan.to_dict(),
      expected_state_version=record.state_version,
      evaluated_at=quote(2).timestamp,
      event_type="EXIT_INTENT_CREATED",
      event_business_key="reserve-sell",
      db=db,
      commit=False,
    )
    await TradeIntentProcessor.reserve_exit_intent(
      db,
      plan=record,
      decision=decision,
      intent_id="sell-intent",
      limit_price=9.85,
      created_at=quote(2).timestamp,
    )
    if stopped:
      from quantx_application.t_trade_v3.execution_use_cases import (
        TAssistantExecutionLifecycle,
      )
      from quantx_domain.trading.t_assistant_execution import TAssistantExecutionStatus
      from quantx_infrastructure.repositories.t_assistant_execution_repository import (
        TAssistantExecutionRepository,
      )

      repository = TAssistantExecutionRepository(db)
      execution = await repository.get_domain(scope)
      for status in (
        TAssistantExecutionStatus.DRAINING,
        TAssistantExecutionStatus.STOPPED,
      ):
        execution = await TAssistantExecutionLifecycle(repository).transition(
          execution,
          target=status,
          at=quote(2).timestamp,
          has_unsettled_buy_work=False,
          event_type=f"TEST_{status.value}",
          payload={"reason": "SOURCE_STOPPED"},
        )
  return scope, decision


def context(second=2):
  return ExitEvaluationContext(
    timestamp=quote(second).timestamp,
    current_price=9.9,
    bid_price=9.9,
    limit_up=11,
    limit_down=9,
    source="paper-test",
  )


async def route(sessions, decision, *, at=2):
  async with sessions() as db:
    record = await db.get(AutoExitPlanRecord, "paper-plan")
  return await AutoExitPlanService()._route_reserved_exit_intent(
    plan_id=record.plan_id,
    record=record,
    decision=decision,
    intent_id="sell-intent",
    context=context(at),
    position=None,
    price=1.0,
    market_ready=lambda: True,
  )


@pytest.mark.parametrize("stopped", [False, True])
async def test_public_route_recovers_and_fills_without_live_reads(sessions, stopped):
  scope, decision = await prepared(sessions, stopped=stopped)

  def guard(_connection, _cursor, statement, _parameters, _context, _many):
    assert not re.search(
      r"\b(accounts|positions|account_execution_controls|pending_trade_orders|trade_command_outbox|agent_report_inbox|strategy_runs)\b",
      statement.lower(),
    )

  engine = sessions.kw["bind"].sync_engine
  event.listen(engine, "before_cursor_execute", guard)
  try:
    result = await route(sessions, decision)
    async with sessions() as db:
      failure = (await db.get(TradeIntentRecord, "sell-intent")).notes
    assert result and result["success"] and not result["duplicate"], failure
    async with sessions() as db:
      order = await db.get(PaperExecutionOrderRecord, result["order_id"])
      assert order.limit_price > 9 and order.side == "SELL"
      assert order.sizing_evidence["sized_volume"] == 100
      plan = await db.get(AutoExitPlanRecord, "paper-plan")
      state_version = plan.state_version
      assert plan.plan_state["pending_order_id"] == order.order_id
      assert (await db.get(TradeIntentRecord, "sell-intent")).status == "ROUTED"
    retry = await route(sessions, decision, at=60)
    assert retry["duplicate"]
    async with sessions() as db:
      plan = await db.get(AutoExitPlanRecord, "paper-plan")
      assert plan.state_version == state_version
    # Restart public runtime recovery: it must use PaperOrder, never PendingTradeOrder.
    assert (
      await AutoExitPlanService().evaluate_and_submit(
        plan_id="paper-plan",
        context=context(2),
        position=None,
        market_session_open=True,
        market_ready=lambda: True,
      )
      is None
    )
    async with sessions() as db, db.begin():
      await PaperExecutionLedger(
        db, receipt_sink=PaperReceiptConvergence()
      ).process_quote(
        execution_id=scope,
        event_key="sell-fill",
        quote=quote(3),
      )
      assert (await db.get(AutoExitPlanRecord, "paper-plan")).status == "COMPLETED"
      assert (await db.get(TTradeBatch, "paper-batch")).status == "CLOSED"
  finally:
    event.remove(engine, "before_cursor_execute", guard)


async def test_reserved_public_runtime_recovery_submits_the_same_intent(sessions):
  scope, _ = await prepared(sessions)
  result = await AutoExitPlanService().evaluate_and_submit(
    plan_id="paper-plan",
    context=context(),
    position=None,
    market_session_open=True,
    market_ready=lambda: True,
  )
  assert result and result["success"]
  async with sessions() as db:
    assert (await db.get(PaperExecutionAccountRecord, scope)).revision == 3
    assert (await db.get(TradeIntentRecord, "sell-intent")).order_id == result[
      "order_id"
    ]


@pytest.mark.parametrize("at", [0, 12])
async def test_future_or_stale_checkpoint_quote_releases_only_unaccepted_reservation(
  sessions, at
):
  scope, decision = await prepared(sessions)
  assert await route(sessions, decision, at=at) is None
  async with sessions() as db:
    assert (await db.get(PaperExecutionAccountRecord, scope)).revision == 2
    assert (
      await db.scalar(select(func.count()).select_from(PaperExecutionOrderRecord)) == 1
    )
    intent = await db.get(TradeIntentRecord, "sell-intent")
    assert intent.status == "REJECTED"
    assert "TIME_INVALID" in intent.notes
    assert not (await db.get(AutoExitPlanRecord, "paper-plan")).plan_state[
      "pending_intent_id"
    ]


async def test_sink_failure_rolls_back_order_then_public_release_preserves_protection(
  sessions, monkeypatch, *, bucket="swing"
):
  scope, decision = await prepared(sessions, bucket=bucket)
  original = PaperReceiptConvergence.__call__
  delivered = []

  async def fail(self, db, execution_id, result):
    await original(self, db, execution_id, result)
    delivered.append(
      (
        result.orders[0].request.volume,
        result.orders[0].request.metadata["protected_core_floor"],
      )
    )
    raise ValueError("sink failure after plan write")

  monkeypatch.setattr(PaperReceiptConvergence, "__call__", fail)
  assert await route(sessions, decision) is None
  assert len(delivered) == 1 and delivered[0][0] == 100
  if bucket == "core":
    assert delivered[0][1] == 900
  async with sessions() as db:
    assert (await db.get(PaperExecutionAccountRecord, scope)).revision == 2
    assert (
      await db.scalar(select(func.count()).select_from(PaperExecutionOrderRecord)) == 1
    )
    plan = await db.get(AutoExitPlanRecord, "paper-plan")
    assert plan.remaining_volume == 100 and plan.status == "ACTIVE"


async def test_first_public_rule_evaluation_reserves_and_routes_real_sell(sessions):
  from quantx_infrastructure.services.exit_plan_scope_lock import (
    lock_exit_plan_scope_for_plan,
  )

  scope, _ = await prepared(sessions, reserve=False)
  async with sessions() as db:
    position = (await lock_exit_plan_scope_for_plan(db, "paper-plan")).position
  result = await AutoExitPlanService().evaluate_and_submit(
    plan_id="paper-plan",
    context=context(),
    position=position,
    market_session_open=True,
    market_ready=lambda: True,
  )
  assert result and result["success"]
  async with sessions() as db:
    assert (await db.get(PaperExecutionAccountRecord, scope)).revision == 3
    record = await db.get(AutoExitPlanRecord, "paper-plan")
    assert record.plan_state["pending_order_id"] == result["order_id"]


@pytest.mark.parametrize("market_change", [{"suspended": True}, {"is_trading": False}])
async def test_actual_risk_rejects_non_trading_checkpoint(sessions, market_change):
  scope, decision = await prepared(sessions)
  async with sessions() as db, db.begin():
    await PaperExecutionLedger(
      db, receipt_sink=PaperReceiptConvergence()
    ).process_quote(
      execution_id=scope,
      event_key="blocked-market",
      quote=replace(quote(2), **market_change),
    )
  assert await route(sessions, decision, at=3) is None
  async with sessions() as db:
    assert (await db.get(PaperExecutionAccountRecord, scope)).revision == 3
    intent = await db.get(TradeIntentRecord, "sell-intent")
    assert intent.status == "REJECTED" and "PAPER_EXIT_RISK" in intent.notes


async def test_quote_helper_requires_full_immutable_market_event(sessions):
  from quantx_infrastructure.models.paper_execution import PaperExecutionEventRecord
  from quantx_infrastructure.services.paper_exit_execution import read_paper_exit_market

  scope, _ = await prepared(sessions)
  async with sessions() as db:
    market = await read_paper_exit_market(
      db, execution_id=scope, instrument_code="600000.SH", now=quote(2).timestamp
    )
    assert market.bid_price == quote(1).bid_price
    witness = await db.scalar(
      select(PaperExecutionEventRecord).where(
        PaperExecutionEventRecord.event_type == "QUOTE"
      )
    )
    witness.input_hash = "f" * 64
    await db.flush()
    with pytest.raises(ValueError, match="MARKET_EVIDENCE_CONFLICT"):
      await read_paper_exit_market(
        db, execution_id=scope, instrument_code="600000.SH", now=quote(2).timestamp
      )


async def test_reserved_recovery_obeys_closed_session_without_losing_reservation(
  sessions,
):
  from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanEvent

  scope, _ = await prepared(sessions)
  for second in (2, 3):
    assert (
      await AutoExitPlanService().evaluate_and_submit(
        plan_id="paper-plan",
        context=context(second),
        position=None,
        market_session_open=False,
        market_ready=lambda: True,
      )
      is None
    )
  async with sessions() as db:
    assert (await db.get(TradeIntentRecord, "sell-intent")).status == "RESERVED"
    assert (await db.get(PaperExecutionAccountRecord, scope)).revision == 2
    events = list(
      (
        await db.scalars(
          select(AutoExitPlanEvent).where(
            AutoExitPlanEvent.plan_id == "paper-plan",
            AutoExitPlanEvent.event_type == "EXIT_INTENT_DEFERRED",
          )
        )
      ).all()
    )
    assert len(events) == 1
    assert events[0].payload == {
      "intent_id": "sell-intent",
      "reason_code": "MARKET_SESSION_CLOSED",
    }
    assert (
      await db.scalar(
        select(func.count())
        .select_from(PaperExecutionOrderRecord)
        .where(
          PaperExecutionOrderRecord.intent_id == "sell-intent",
        )
      )
      == 0
    )
    assert (await db.get(AutoExitPlanRecord, "paper-plan")).remaining_volume == 100


@pytest.mark.skipif(
  os.getenv("QUANTX_RUN_MIGRATION_GATE") != "true",
  reason="explicit isolated PostgreSQL gate required",
)
async def test_postgresql_public_paper_exit_route_recovery_and_rollback(
  monkeypatch, local_sessions
):
  from quantx_infrastructure.services import (
    auto_exit_plan_service,
    trade_intent_processor,
  )

  from tests.infrastructure.test_p4_allocation_postgresql import _sessions

  # Each closure has its own random schema; both install the actual 0054 head.
  async with _sessions(head="20260907_0054") as pg_sessions:
    monkeypatch.setattr(auto_exit_plan_service, "AsyncSessionLocal", pg_sessions)
    monkeypatch.setattr(trade_intent_processor, "AsyncSessionLocal", pg_sessions)
    await test_public_route_recovers_and_fills_without_live_reads(pg_sessions, True)
  local_sessions["protected_core_volume"] = 900
  async with _sessions(head="20260907_0054") as pg_sessions:
    monkeypatch.setattr(auto_exit_plan_service, "AsyncSessionLocal", pg_sessions)
    monkeypatch.setattr(trade_intent_processor, "AsyncSessionLocal", pg_sessions)
    await test_sink_failure_rolls_back_order_then_public_release_preserves_protection(
      pg_sessions, monkeypatch, bucket="core"
    )


@pytest.mark.parametrize("floor,expected", [(900, 100), (950, 0), (1000, 0)])
async def test_stopped_core_exit_respects_frozen_protected_floor(
  sessions, local_sessions, floor, expected
):
  local_sessions["protected_core_volume"] = floor
  scope, decision = await prepared(sessions, stopped=True, bucket="core")
  result = await route(sessions, decision)
  async with sessions() as db:
    if expected:
      assert result and result["volume"] == expected
      order = await db.get(PaperExecutionOrderRecord, result["order_id"])
      assert order.request_payload["metadata"]["protected_core_floor"] == floor
      assert (
        order.request_payload["metadata"]["paper_exit_envelope_policy_version"]
        == "exit-envelope-v1"
      )
    else:
      assert result is None
      assert (await db.get(PaperExecutionAccountRecord, scope)).revision == 2
      assert (
        "ZERO_SIZED_VOLUME" in (await db.get(TradeIntentRecord, "sell-intent")).notes
      )
