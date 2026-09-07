"""Final capacity from actual PAPER ledger + public receipt convergence."""

from decimal import Decimal

import pytest
from quantx_contracts import ExecutionEnvironment
from quantx_infrastructure.models.paper_execution import (
  PaperExecutionAccountRecord,
  PaperExecutionOrderRecord,
)
from quantx_infrastructure.services.account_capacity_service import (
  AccountCapacityService,
  paper_pending_buy_cash,
)
from quantx_infrastructure.services.paper_execution_ledger import PaperExecutionLedger
from quantx_infrastructure.services.paper_receipt_convergence import (
  PaperReceiptConvergence,
)

from tests.infrastructure.test_paper_receipt_convergence import (
  allocation_sessions as _allocation_sessions,
)
from tests.infrastructure.test_paper_receipt_convergence import (
  base_sessions as _base_sessions,
)
from tests.infrastructure.test_paper_receipt_convergence import (
  enrich_intent,
  quote,
  setup,
)
from tests.infrastructure.test_paper_receipt_convergence import (
  ledger_sessions as _ledger_sessions,
)
from tests.infrastructure.test_paper_receipt_convergence import (
  sessions as _sessions,
)

allocation_sessions = _allocation_sessions
base_sessions = _base_sessions
ledger_sessions = _ledger_sessions
sessions = _sessions


async def capacity(db, execution_id, **kwargs):
  return await AccountCapacityService(db).read(
    instrument_code="600000.SH",
    environment=ExecutionEnvironment.PAPER,
    paper_execution_id=execution_id,
    account_id="account-1",
    **kwargs,
  )


async def test_partial_buy_cancel_cash_and_single_public_protection(sessions):
  sink = PaperReceiptConvergence()
  scope, args = await setup(sessions, sink, enrich_intent=enrich_intent)
  async with sessions() as db, db.begin():
    before = await capacity(db, scope)
    assert before.available_cash == Decimal("100000")
    assert before.unclaimed_volume == 1000  # READY planning caps are separate.
    ledger = PaperExecutionLedger(db, receipt_sink=sink)
    await ledger.place_order(execution_id=scope, **args)
    ordered = await capacity(db, scope)
    order = await db.get(PaperExecutionOrderRecord, args["order_id"])
    assert ordered.available_cash == Decimal("100000") - paper_pending_buy_cash(order)
    assert ordered.unclaimed_volume == 900
    assert ordered.obligation_watermark != before.obligation_watermark
    await ledger.process_quote(
      execution_id=scope,
      event_key="partial",
      accepted_at=(quote(1)).timestamp,
      quote=quote(1),
    )
    partial = await capacity(db, scope)
    account = await db.get(PaperExecutionAccountRecord, scope)
    assert partial.unclaimed_volume == 900  # 50 filled + 50 pending, not +plan 50.
    assert partial.available_volume == 1000
    assert partial.available_cash == Decimal(
      str(account.broker_checkpoint["material"]["state"]["cash"])
    ) - paper_pending_buy_cash(order)
    assert paper_pending_buy_cash(order) == Decimal("495.00495000")
    await ledger.cancel(
      execution_id=scope,
      event_key="cancel",
      order_id=args["order_id"],
      now=quote(2).timestamp,
    )
    cancelled = await capacity(db, scope)
    assert cancelled.unclaimed_volume == 950
    assert cancelled.available_cash == Decimal(
      str(account.broker_checkpoint["material"]["state"]["cash"])
    )
    own = await capacity(db, scope, own_plan_id="paper-plan")
    assert own.unclaimed_volume == 1000


async def test_scope_snapshot_and_corrupt_checkpoint_fail_closed(sessions):
  sink = PaperReceiptConvergence()
  scope, _ = await setup(sessions, sink, enrich_intent=enrich_intent)
  async with sessions() as db, db.begin():
    with pytest.raises(ValueError, match="SCOPE"):
      await AccountCapacityService(db).read(
        instrument_code="600000.SH",
        environment=ExecutionEnvironment.PAPER,
        paper_execution_id=scope,
        account_id="different-account",
      )
    with pytest.raises(ValueError, match="SNAPSHOT"):
      await capacity(db, scope, expected_snapshot_hash="0" * 64)
    account = await db.get(PaperExecutionAccountRecord, scope)
    account.snapshot_hash = "f" * 64
    await db.flush()
    with pytest.raises(ValueError, match="SNAPSHOT"):
      await capacity(db, scope)


async def test_bucket_floor_and_never_locked_core(sessions):
  from tests.infrastructure.test_paper_execution_ledger import seed_values

  initial = seed_values()
  states = initial["bucket_checkpoint"]["instruments"]["600000.SH"]
  original = states.pop("swing")
  for name, qty in (("swing", 50), ("core", 450), ("locked_core", 500)):
    states[name] = {
      **original,
      "bucket": name,
      "total_volume": qty,
      "available_volume": qty,
      "market_value": qty * 10.0,
    }
  sink = PaperReceiptConvergence()
  scope, args = await setup(
    sessions, sink, enrich_intent=enrich_intent, initial_seed=initial
  )
  async with sessions() as db, db.begin():
    await PaperExecutionLedger(db, receipt_sink=sink).place_order(
      execution_id=scope, **args
    )
    result = await capacity(db, scope, allow_core_claim=True, protected_core_floor=400)
    assert result.unclaimed_volume == 0
    assert result.protected_old_position_floor == 900
    assert result.old_inventory_claim_allocation == {
      "swing": 50,
      "core": 50,
      "locked_core": 0,
    }
    with pytest.raises(ValueError, match="BUCKET_CAPACITY"):
      await capacity(db, scope, allow_core_claim=False)


async def test_real_sell_reservation_is_not_claimed_again_and_stopped_source_can_exit(
  sessions, monkeypatch
):
  from quantx_domain.brokers.base import OrderType
  from quantx_domain.trading.t_assistant_execution import TAssistantExecutionEvent
  from quantx_infrastructure.repositories.t_assistant_execution_repository import (
    TAssistantExecutionRepository,
  )

  from tests.infrastructure.test_paper_receipt_convergence import (
    test_public_plan_sell_receipt_closes_batch_without_pending_mismatch,
  )

  original_place = PaperExecutionLedger.place_order
  original_quote = PaperExecutionLedger.process_quote
  seen = []

  async def place(ledger, **kwargs):
    if kwargs["request"].order_type is OrderType.SELL:
      repository = TAssistantExecutionRepository(ledger.db)
      current = await repository.get_domain(kwargs["execution_id"])
      for status in ("DRAINING", "STOPPED"):
        revised = current.transition(
          status, at=kwargs["now"], has_unsettled_buy_work=False
        )
        await repository.save_transition_with_event(
          revised,
          expected_state_version=current.state_version,
          event=TAssistantExecutionEvent(
            kwargs["execution_id"], status, status, kwargs["now"], {}
          ),
        )
        current = revised
    result = await original_place(ledger, **kwargs)
    if kwargs["request"].order_type is OrderType.SELL:
      value = await capacity(ledger.db, kwargs["execution_id"])
      assert value.available_volume == 900
      assert value.unclaimed_volume == 900
      assert sum(value.old_inventory_claim_allocation.values()) == 0
      seen.append("reserved")
    return result

  async def process(ledger, **kwargs):
    result = await original_quote(ledger, **kwargs)
    if kwargs["event_key"] == "sell-filled":
      value = await capacity(ledger.db, kwargs["execution_id"])
      assert value.available_volume == value.unclaimed_volume == 900
      seen.append("filled")
    return result

  monkeypatch.setattr(PaperExecutionLedger, "place_order", place)
  monkeypatch.setattr(PaperExecutionLedger, "process_quote", process)
  await test_public_plan_sell_receipt_closes_batch_without_pending_mismatch(sessions)
  assert seen == ["reserved", "filled"]


@pytest.mark.parametrize("corruption", ["lineage", "price"])
async def test_missing_public_lineage_fails_closed(sessions, corruption):
  from quantx_infrastructure.models.agent_runtime import TTradeBatch

  sink = PaperReceiptConvergence()
  scope, args = await setup(sessions, sink, enrich_intent=enrich_intent)
  async with sessions() as db, db.begin():
    await PaperExecutionLedger(db, receipt_sink=sink).place_order(
      execution_id=scope, **args
    )
    if corruption == "lineage":
      batch = await db.get(TTradeBatch, "paper-batch")
      batch.entry_intent_id = "wrong-intent"
    else:
      order = await db.get(PaperExecutionOrderRecord, args["order_id"])
      order.limit_price = Decimal("0.01")
    await db.flush()
    with pytest.raises(ValueError, match="LINEAGE|MATERIAL"):
      await capacity(db, scope)


async def test_repeat_read_watermark_and_no_live_queries(sessions):
  import re

  from sqlalchemy import event

  sink = PaperReceiptConvergence()
  scope, _ = await setup(sessions, sink, enrich_intent=enrich_intent)

  def guard(_connection, _cursor, statement, _parameters, _context, _many):
    assert not re.search(
      r"\b(?:positions|account_execution_controls|pending_trade_orders|trade_command_outbox|agent_report_inbox)\b",
      statement.lower(),
    )

  engine = sessions.kw["bind"].sync_engine
  event.listen(engine, "before_cursor_execute", guard)
  try:
    async with sessions() as db, db.begin():
      first = await capacity(db, scope)
      second = await capacity(
        db, scope, lock_rows=False, expected_snapshot_id=first.snapshot_id
      )
      assert first == second
  finally:
    event.remove(engine, "before_cursor_execute", guard)
