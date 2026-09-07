"""Real PAPER cancellation receipts determine whether source BUY drain is done."""

import pytest
from quantx_engine.t_assistant_paper_drain import drain_paper_entry_work
from quantx_infrastructure.models.agent_runtime import TTradeBatch
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.paper_execution import (
  PaperExecutionAccountRecord,
  PaperExecutionEventRecord,
  PaperExecutionOrderRecord,
)
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantExecutionEventRecord,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.paper_execution_ledger import PaperExecutionLedger
from quantx_infrastructure.services.paper_receipt_convergence import (
  PaperReceiptConvergence,
)
from sqlalchemy import event, func, select

from tests.infrastructure import test_paper_receipt_convergence as receipt_tests
from tests.infrastructure.test_paper_portfolio_snapshot import frozen_config
from tests.infrastructure.test_t_allocation_repository import _seed

allocation_sessions = receipt_tests.allocation_sessions
base_sessions = receipt_tests.base_sessions
ledger_sessions = receipt_tests.ledger_sessions
sessions = receipt_tests.sessions
_FIXTURES = (frozen_config,)
setup, quote, enrich_intent = (
  receipt_tests.setup,
  receipt_tests.quote,
  receipt_tests.enrich_intent,
)


@pytest.fixture
def exit_context(monkeypatch, sessions):
  from tests.infrastructure.test_paper_exit_execution import local_sessions

  yield from local_sessions.__wrapped__(monkeypatch, sessions)


async def test_existing_protective_sell_and_plan_are_unchanged(sessions, exit_context):
  from quantx_infrastructure.services.paper_exit_execution import execute_paper_exit

  from tests.infrastructure.test_paper_exit_execution import prepared

  scope, _ = await prepared(sessions)
  async with sessions() as db, db.begin():
    await execute_paper_exit(
      db, plan_id="paper-plan", intent_id="sell-intent", now=quote(2).timestamp
    )
    sell = await db.scalar(
      select(PaperExecutionOrderRecord).where(PaperExecutionOrderRecord.side == "SELL")
    )
    assert sell.status == "SUBMITTED"
    before_plan = (await db.get(AutoExitPlanRecord, "paper-plan")).plan_state
    before_revision = (await db.get(PaperExecutionAccountRecord, scope)).revision
    before_order = sell.response_payload
    result = await drain_paper_entry_work(
      db, execution_id=scope, now=quote(3).timestamp, reason="SOURCE_DISABLED"
    )
    assert not result.has_unsettled_buy_work
    assert not result.receipt_event_ids
    assert sell.response_payload == before_order and sell.status == "SUBMITTED"
    assert (
      await db.get(PaperExecutionAccountRecord, scope)
    ).revision == before_revision
    assert (await db.get(AutoExitPlanRecord, "paper-plan")).plan_state == before_plan


@pytest.mark.parametrize("awaiting_approval", [False, True])
async def test_unsubmitted_pending_needs_no_seed_and_retry_keeps_original_audit(
  sessions,
  awaiting_approval,
):
  snapshot, candidates = await _seed(
    sessions, authorization="MANUAL_CONFIRM", enrich_intent=enrich_intent
  )
  scope = snapshot.cut.execution_ref.owner_id
  if awaiting_approval:
    from quantx_infrastructure.repositories.t_allocation_repository import (
      TAllocationRepository,
    )

    from tests.infrastructure.test_t_allocation_repository import _claim, _prepared

    async with sessions() as db, db.begin():
      repository = TAllocationRepository(db)
      batch = await _prepared(repository, snapshot, candidates)
      claim = await _claim(repository, batch, snapshot, candidates)
      await repository.commit(
        claim=claim, snapshot=snapshot, candidates=candidates, now=quote(0).timestamp
      )
      assert (await db.get(TradeIntentRecord, "intent-0")).status == "AWAITING_APPROVAL"
  async with sessions() as db, db.begin():
    result = await drain_paper_entry_work(
      db, execution_id=scope, now=quote(1).timestamp, reason="CONFIG_DISABLED"
    )
    assert not result.has_unsettled_buy_work
    assert result.cancelled_intent_ids == ("intent-0",)
    assert (await db.get(TradeIntentRecord, "intent-0")).status == "CANCELLED"
    assert await db.get(PaperExecutionAccountRecord, scope) is None
  async with sessions() as db, db.begin():
    result = await drain_paper_entry_work(
      db, execution_id=scope, now=quote(2).timestamp, reason="RETRY_OTHER_REASON"
    )
    assert not result.has_unsettled_buy_work and not result.cancelled_intent_ids
    audits = list(
      (
        await db.scalars(
          select(TAssistantExecutionEventRecord).where(
            TAssistantExecutionEventRecord.event_type == "PAPER_ENTRY_DRAINED"
          )
        )
      ).all()
    )
    assert len(audits) == 1 and audits[0].payload["reason"] == "CONFIG_DISABLED"


@pytest.mark.parametrize("partial", [False, True])
async def test_real_buy_cancel_releases_remaining_and_preserves_fill_protection(
  sessions, partial
):
  scope, args = await setup(
    sessions, PaperReceiptConvergence(), enrich_intent=enrich_intent
  )
  async with sessions() as db, db.begin():
    ledger = PaperExecutionLedger(db, receipt_sink=PaperReceiptConvergence())
    await ledger.place_order(execution_id=scope, **args)
    if partial:
      await ledger.process_quote(
        execution_id=scope,
        event_key="partial",
        quote=quote(1),
        accepted_at=quote(1).timestamp,
      )
    before = await ledger.get_snapshot(execution_id=scope)
    plan_before = (
      (await db.get(AutoExitPlanRecord, "paper-plan")).plan_state if partial else None
    )
  observed = []

  def observe(_connection, _cursor, statement, _parameters, _context, _many):
    observed.append(statement.lower())

  engine = sessions.kw["bind"].sync_engine
  event.listen(engine, "before_cursor_execute", observe)
  try:
    async with sessions() as db, db.begin():
      result = await drain_paper_entry_work(
        db, execution_id=scope, now=quote(2).timestamp, reason="SOURCE_STOP"
      )
      assert not result.has_unsettled_buy_work
      assert result.cancelled_order_ids == (args["order_id"],)
      order = await db.get(PaperExecutionOrderRecord, args["order_id"])
      assert order.status == "CANCELLED"
      assert order.filled_volume == (50 if partial else 0)
      intent = await db.get(TradeIntentRecord, args["intent_id"])
      assert (
        intent.status == "CANCELLED" and intent.executed_volume == order.filled_volume
      )
      account = await db.get(PaperExecutionAccountRecord, scope)
      assert not account.bucket_checkpoint["pending_orders"]
      assert (
        account.broker_checkpoint["material"]["state"]["cash"]
        == before["broker_checkpoint"]["material"]["state"]["cash"]
      )
      if partial:
        plan = await db.get(AutoExitPlanRecord, "paper-plan")
        assert plan.protected_volume == 50 and plan.plan_state == plan_before
        assert (await db.get(TTradeBatch, "paper-batch")).status == "OPEN"
      else:
        assert await db.get(AutoExitPlanRecord, "paper-plan") is None
      revision = account.revision
    async with sessions() as db, db.begin():
      repeated = await drain_paper_entry_work(
        db, execution_id=scope, now=quote(3).timestamp, reason="SOURCE_STOP"
      )
      assert not repeated.has_unsettled_buy_work and not repeated.receipt_event_ids
      assert (await db.get(PaperExecutionAccountRecord, scope)).revision == revision
  finally:
    event.remove(engine, "before_cursor_execute", observe)
  for forbidden in (
    "positions",
    "account_execution_controls",
    "pending_trade_orders",
    "agent_command_outbox",
  ):
    assert not any(
      f"from {forbidden}" in sql or f"join {forbidden}" in sql for sql in observed
    )


async def test_ready_without_order_is_cancelled_without_broker_fact(sessions):
  scope, args = await setup(
    sessions, PaperReceiptConvergence(), enrich_intent=enrich_intent
  )
  async with sessions() as db, db.begin():
    result = await drain_paper_entry_work(
      db, execution_id=scope, now=quote(1).timestamp, reason="DISABLED"
    )
    assert not result.has_unsettled_buy_work
    assert result.cancelled_intent_ids == (args["intent_id"],)
    assert (
      await db.scalar(select(func.count()).select_from(PaperExecutionEventRecord)) == 0
    )


async def test_sink_failure_rolls_back_cancel_and_public_projection(
  sessions, monkeypatch
):
  scope, args = await setup(
    sessions, PaperReceiptConvergence(), enrich_intent=enrich_intent
  )
  async with sessions() as db, db.begin():
    await PaperExecutionLedger(db, receipt_sink=PaperReceiptConvergence()).place_order(
      execution_id=scope, **args
    )
    revision = (await db.get(PaperExecutionAccountRecord, scope)).revision
  original = PaperReceiptConvergence.__call__

  async def fail(self, *args, **kwargs):
    await original(self, *args, **kwargs)
    raise RuntimeError("late sink failed")

  monkeypatch.setattr(PaperReceiptConvergence, "__call__", fail)
  async with sessions() as db, db.begin():
    with pytest.raises(RuntimeError, match="late sink failed"):
      await drain_paper_entry_work(
        db, execution_id=scope, now=quote(1).timestamp, reason="DISABLED"
      )
    assert (await db.get(PaperExecutionAccountRecord, scope)).revision == revision
    assert (
      await db.get(PaperExecutionOrderRecord, args["order_id"])
    ).status == "SUBMITTED"
    assert (await db.get(TradeIntentRecord, args["intent_id"])).status == "ROUTED"
    assert not list(
      (
        await db.scalars(
          select(TAssistantExecutionEventRecord).where(
            TAssistantExecutionEventRecord.event_type == "PAPER_ENTRY_DRAINED"
          )
        )
      ).all()
    )


async def test_caller_rollback_includes_unsubmitted_terminalization(sessions):
  snapshot, _ = await _seed(sessions, authorization="AUTO", enrich_intent=enrich_intent)
  async with sessions() as db:
    with pytest.raises(RuntimeError, match="outer"):
      async with db.begin():
        await drain_paper_entry_work(
          db,
          execution_id=snapshot.cut.execution_ref.owner_id,
          now=quote(1).timestamp,
          reason="DISABLED",
        )
        raise RuntimeError("outer")
  async with sessions() as db:
    assert (await db.get(TradeIntentRecord, "intent-0")).status == "ALLOCATION_PENDING"


async def test_unknown_nonterminal_buy_is_unsettled_not_fabricated_cancel(sessions):
  scope, args = await setup(
    sessions, PaperReceiptConvergence(), enrich_intent=enrich_intent
  )
  async with sessions() as db, db.begin():
    # Missing order for ROUTED is a reconciliation case, not local zero-fill proof.
    (await db.get(TradeIntentRecord, args["intent_id"])).status = "ROUTED"
  async with sessions() as db, db.begin():
    result = await drain_paper_entry_work(
      db, execution_id=scope, now=quote(1).timestamp, reason="DISABLED"
    )
    assert result.has_unsettled_buy_work
    assert result.unsettled_intent_ids == (args["intent_id"],)
    assert not result.cancelled_intent_ids


async def test_one_clock_advance_expires_multiple_real_buy_orders_once(
  sessions, frozen_config
):
  from datetime import timedelta

  from quantx_engine.t_assistant_paper_entry_runtime import TAssistantPaperEntryRuntime

  from tests.engine.unit.test_t_assistant_paper_entry_runtime import seeded

  source, witnesses = await seeded(sessions)

  async def provider(code):
    return witnesses[code]

  accepted = await TAssistantPaperEntryRuntime(
    session_factory=sessions, clock=lambda: source.now
  ).dispatch(execution_id=source.execution_id, market_witness_provider=provider)
  assert len(accepted.order_ids) == 2
  async with sessions() as db, db.begin():
    result = await drain_paper_entry_work(
      db,
      execution_id=source.execution_id,
      now=source.now + timedelta(minutes=5),
      reason="SOURCE_STOP",
    )
    assert not result.has_unsettled_buy_work
    assert set(result.cancelled_order_ids) == set(accepted.order_ids)
    assert len(result.receipt_event_ids) == 1
    assert all(
      row.status == "EXPIRED"
      for row in (await db.scalars(select(PaperExecutionOrderRecord))).all()
    )
