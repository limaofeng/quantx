"""RULE_ONLY replay through real entry, PAPER matching and public ExitPlan runtime."""

import os
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from quantx_engine.exit_plan_runtime import ExitPlanRuntime
from quantx_engine.paper_market_runtime import PaperMarketRuntime
from quantx_engine.t_assistant_paper_entry_runtime import TAssistantPaperEntryRuntime
from quantx_infrastructure.models.agent_runtime import TTradeBatch
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.paper_execution import (
  PaperExecutionAccountRecord,
  PaperExecutionEventRecord,
  PaperExecutionFillRecord,
  PaperExecutionOrderRecord,
)
from quantx_infrastructure.models.t_allocation import TAllocationDecisionRecord
from quantx_infrastructure.models.t_assistant_execution import TAssistantExecutionRecord
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from sqlalchemy import event, select

from tests.engine.unit.test_t_assistant_paper_entry_runtime import AT, CODES, seeded
from tests.infrastructure.test_t_candidate_evidence import (
  allocation_sessions,
  base_sessions,
  frozen_config,
  ledger_sessions,
  sessions,
)

_FIXTURES = (
  allocation_sessions,
  base_sessions,
  frozen_config,
  ledger_sessions,
  sessions,
)
SHANGHAI = ZoneInfo("Asia/Shanghai")


@pytest.mark.skipif(
  os.getenv("QUANTX_RUN_MIGRATION_GATE") != "true",
  reason="explicit isolated PostgreSQL gate required",
)
async def test_postgresql_two_symbol_rule_only_replay(frozen_config, monkeypatch):
  from tests.infrastructure.test_p4_allocation_postgresql import _sessions

  async with _sessions(head="20260907_0056") as isolated:
    await test_two_symbol_rule_only_replay_closes_both_public_batches_after_restart(
      isolated,
      frozen_config,
      monkeypatch,
    )


class ReplayTransport:
  """External hub readiness only; all evaluated prices come from PAPER facts."""

  is_running = True

  def __init__(self, clock):
    self.clock = clock
    self.hub = SimpleNamespace(is_ready=True, is_trading_session=self.session_open)

  async def session_open(self):
    local = self.clock["now"].astimezone(SHANGHAI)
    return local.hour == 9 and local.minute >= 30

  def touch(self):
    pass

  def snapshot_states(self):
    return {}  # T PAPER runtime must read actual checkpoint, never this LIVE cache.


def raw_books(source, step, *, price=99.32, depth=400):
  source_at = source.now + timedelta(seconds=step)
  return {
    code: {
      "market_stream_id": "stream-1",
      "continuity_generation": 7,
      "market_stream_sequence": 12 + step * 2 + index,
      "tick_ordinal": 6 + step,
      "source_time_ms": int(source_at.timestamp() * 1000),
      "lastPrice": price,
      "priceTick": 0.01,
      "upStopPrice": 110.0,
      "downStopPrice": 90.0,
      "stockStatus": 0,
      "volume": 11500 + step * 100,
      "amount": 1150000 + step * 10000,
      "bidPrice": [round(price - 0.01 - index * 0.01, 2) for index in range(5)],
      "askPrice": [round(price + index * 0.01, 2) for index in range(5)],
      "bidVol": [depth] * 5,
      "askVol": [depth] * 5,
    }
    for index, code in enumerate(CODES)
  }


async def test_two_symbol_rule_only_replay_closes_both_public_batches_after_restart(
  sessions, frozen_config, monkeypatch
):
  from quantx_engine import exit_plan_runtime
  from quantx_infrastructure.core.utils import time_utils
  from quantx_infrastructure.services import (
    auto_exit_plan_service,
    trade_intent_processor,
  )

  clock = {"now": AT}
  for module in (exit_plan_runtime, auto_exit_plan_service, trade_intent_processor):
    monkeypatch.setattr(module, "AsyncSessionLocal", sessions)
  monkeypatch.setattr(
    time_utils, "now", lambda: clock["now"].astimezone(SHANGHAI).replace(tzinfo=None)
  )
  monkeypatch.setattr(
    time_utils, "now_aware", lambda: clock["now"].astimezone(SHANGHAI)
  )
  statements = []

  def guard(_connection, _cursor, statement, _parameters, _context, _many):
    sql = statement.lower()
    statements.append(sql)
    for table in (
      "positions",
      "account_execution_controls",
      "pending_trade_orders",
      "trade_command_outbox",
      "agent_report_inbox",
      "order_correlations",
    ):
      assert not any(
        fragment in sql
        for fragment in (
          f"from {table}",
          f"join {table}",
          f"into {table}",
          f"update {table}",
        )
      ), sql

  engine = sessions.kw["bind"].sync_engine
  event.listen(engine, "before_cursor_execute", guard)
  try:
    source, witnesses = await seeded(sessions)
    clock["now"] = source.now

    async def provider(code):
      return witnesses[code]

    entry = await TAssistantPaperEntryRuntime(
      session_factory=sessions, clock=lambda: clock["now"]
    ).dispatch(execution_id=source.execution_id, market_witness_provider=provider)
    assert len(entry.order_ids) == 2
    async with sessions() as db:
      assert (
        await db.get(TAssistantExecutionRecord, source.execution_id)
      ).scorer_mode == "RULE_ONLY"
      intents = list(
        (
          await db.scalars(
            select(TradeIntentRecord).order_by(TradeIntentRecord.admission_rank)
          )
        ).all()
      )
      decisions = list(
        (
          await db.scalars(
            select(TAllocationDecisionRecord).order_by(TAllocationDecisionRecord.rank)
          )
        ).all()
      )
      assert [row.instrument_code for row in intents] == list(CODES)
      assert [row.id for row in intents] == [row.intent_id for row in decisions]
      plan_ids = {
        row.instrument_code: row.intent_metadata["exit_plan_id"] for row in intents
      }
      original_templates = {
        row.instrument_code: row.intent_metadata["exit_plan_template"]
        for row in intents
      }
      assert all(row.status == "ROUTED" for row in intents)
    # Each quote enters a newly constructed runtime/session and survives a
    # repeated dispatch plus a delayed duplicate delivery after partial fill.
    for step, expected in ((1, 50), (2, 100)):
      clock["now"] = source.now + timedelta(seconds=step, milliseconds=100)
      raw = raw_books(source, step, depth=200 if step == 1 else 400)
      market = PaperMarketRuntime(session_factory=sessions, clock=lambda: clock["now"])
      assert (
        await market.on_quote_batch(raw, active_instruments={}, now=clock["now"]) == 2
      )
      async with sessions() as db:
        plans = list((await db.scalars(select(AutoExitPlanRecord))).all())
        assert len(plans) == 2
        assert {plan.protected_volume for plan in plans} == {expected}
        assert {plan.instrument_code for plan in plans} == set(CODES)
        assert all(plan.strategy_run_id is None for plan in plans)
        for plan in plans:
          assert (
            plan.plan_state["template"]["rules"]
            == original_templates[plan.instrument_code]["rules"]
          )
        revision = (
          await db.get(PaperExecutionAccountRecord, source.execution_id)
        ).revision
      assert (
        await PaperMarketRuntime(
          session_factory=sessions, clock=lambda: clock["now"]
        ).on_quote_batch(
          raw, active_instruments={}, now=clock["now"] + timedelta(milliseconds=100)
        )
        == 0
      )
      repeated = await TAssistantPaperEntryRuntime(
        session_factory=sessions, clock=lambda: clock["now"]
      ).dispatch(execution_id=source.execution_id, market_witness_provider=provider)
      assert repeated.status == "IDLE"
      async with sessions() as db:
        assert (
          await db.get(PaperExecutionAccountRecord, source.execution_id)
        ).revision == revision
    # Arm the original trailing profit rule using an in-limit five-level book,
    # then trigger its frozen floor on a later pullback after runtime restart.
    clock["now"] = source.now + timedelta(seconds=3, milliseconds=100)
    assert (
      await PaperMarketRuntime(
        session_factory=sessions, clock=lambda: clock["now"]
      ).on_quote_batch(
        raw_books(source, 3, price=105.0), active_instruments={}, now=clock["now"]
      )
      == 2
    )
    armed = await ExitPlanRuntime(
      scanner=ReplayTransport(clock)
    ).evaluate_all_active_plans(account_id="account-1")
    assert len(armed) == 2 and not any(item["submitted"] for item in armed), armed
    clock["now"] = source.now + timedelta(seconds=4, milliseconds=100)
    assert (
      await PaperMarketRuntime(
        session_factory=sessions, clock=lambda: clock["now"]
      ).on_quote_batch(
        raw_books(source, 4, price=102.0), active_instruments={}, now=clock["now"]
      )
      == 2
    )
    exits = await ExitPlanRuntime(
      scanner=ReplayTransport(clock)
    ).evaluate_all_active_plans(account_id="account-1")
    async with sessions() as db:
      diagnostics = [
        (row.status, row.last_error)
        for row in (await db.scalars(select(AutoExitPlanRecord))).all()
      ]
    assert len(exits) == 2 and all(item["submitted"] for item in exits), (
      exits,
      diagnostics,
    )
    async with sessions() as db:
      sell_orders = list(
        (
          await db.scalars(
            select(PaperExecutionOrderRecord).where(
              PaperExecutionOrderRecord.side == "SELL"
            )
          )
        ).all()
      )
      assert len(sell_orders) == 2
      assert {row.owner_id for row in sell_orders} == set(plan_ids.values())
      assert all(
        row.owner_type == "EXIT_PLAN" and row.status == "SUBMITTED"
        for row in sell_orders
      )
      assert all(row.volume == 100 for row in sell_orders)
      plans = list((await db.scalars(select(AutoExitPlanRecord))).all())
      assert all(
        plan.plan_state["pending_rule_id"].endswith(":trailing-profit")
        for plan in plans
      )
    # Runtime restart while SELL is outstanding must recover those reservations,
    # not manufacture a second exit intent or order.
    await ExitPlanRuntime(scanner=ReplayTransport(clock)).evaluate_all_active_plans(
      account_id="account-1"
    )
    clock["now"] = source.now + timedelta(seconds=5, milliseconds=100)
    sell_books = raw_books(source, 5, price=102.0)
    assert (
      await PaperMarketRuntime(
        session_factory=sessions, clock=lambda: clock["now"]
      ).on_quote_batch(sell_books, active_instruments={}, now=clock["now"])
      == 2
    )
    assert (
      await PaperMarketRuntime(
        session_factory=sessions, clock=lambda: clock["now"]
      ).on_quote_batch(
        sell_books,
        active_instruments={source.execution_id: CODES},
        now=clock["now"] + timedelta(seconds=1),
      )
      == 0
    )
    async with sessions() as db:
      plans = list((await db.scalars(select(AutoExitPlanRecord))).all())
      batches = list((await db.scalars(select(TTradeBatch))).all())
      orders = list((await db.scalars(select(PaperExecutionOrderRecord))).all())
      fills = list((await db.scalars(select(PaperExecutionFillRecord))).all())
      events = {
        row.event_id: row
        for row in (await db.scalars(select(PaperExecutionEventRecord))).all()
      }
      assert len(plans) == len(batches) == 2
      assert all(
        plan.status == "COMPLETED" and plan.remaining_volume == 0 for plan in plans
      )
      assert all(
        batch.status == "CLOSED"
        and batch.entry_filled_volume == batch.exit_filled_volume == 100
        for batch in batches
      )
      assert len(orders) == 4 and all(order.status == "FILLED" for order in orders)
      assert len(fills) == 6  # Two partial BUY fills and one SELL fill per symbol.
      by_id = {row.order_id: row for row in orders}
      from quantx_infrastructure.services.paper_execution_ledger import _stored_time

      for fill in fills:
        fact, order = events[fill.event_id], by_id[fill.order_id]
        assert (
          _stored_time(order.submitted_at)
          < _stored_time(fact.quote_source_at)
          <= _stored_time(fill.occurred_at)
        )
        assert _stored_time(fill.occurred_at) == _stored_time(fact.occurred_at)
      account = await db.get(PaperExecutionAccountRecord, source.execution_id)
      assert not account.broker_checkpoint["material"]["orders"]
      assert not account.bucket_checkpoint["pending_orders"]
      assert all(
        position["long_volume"] == 1000
        and position["available_volume"] == 900
        and position["today_buy_volume"] == 100
        for position in account.broker_checkpoint["material"]["positions"].values()
      )
      cash_change = sum(
        (
          (1 if by_id[fill.order_id].side == "SELL" else -1) * fill.price * fill.volume
          - fill.fee
          for fill in fills
        ),
        Decimal(0),
      )
      assert account.broker_checkpoint["material"]["state"]["cash"] == pytest.approx(
        float(Decimal(100000) + cash_change)
      )
    assert (
      await ExitPlanRuntime(scanner=ReplayTransport(clock)).evaluate_all_active_plans(
        account_id="account-1"
      )
      == []
    )
    assert statements
  finally:
    event.remove(engine, "before_cursor_execute", guard)
