"""The critical supervisor keeps durable PAPER exits alive without a producer."""

from quantx_engine.paper_market_runtime import PaperMarketRuntime
from quantx_engine.t_assistant_paper_shadow_supervisor import (
  TAssistantPaperShadowSupervisor,
)
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.paper_execution import (
  PaperExecutionEventRecord,
  PaperExecutionFillRecord,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from sqlalchemy import func, select

from tests.infrastructure import test_paper_exit_execution as exit_tests

allocation_sessions = exit_tests.allocation_sessions
base_sessions = exit_tests.base_sessions
ledger_sessions = exit_tests.ledger_sessions
sessions = exit_tests.sessions
local_sessions = exit_tests.local_sessions


def raw_quote(second):
  quote = exit_tests.quote(second, depth=400)
  return {
    "market_stream_id": "stream",
    "continuity_generation": 1,
    "market_stream_sequence": second,
    "tick_ordinal": second,
    "source_time_ms": int(quote.timestamp.timestamp() * 1000),
    "lastPrice": quote.price,
    "priceTick": quote.price_tick,
    "upStopPrice": quote.limit_up,
    "downStopPrice": quote.limit_down,
    "stockStatus": 0,
    "volume": 10000 + second * 100,
    "amount": 100000 + second * 1000,
    "bidPrice": quote.bid_price,
    "askPrice": quote.ask_price,
    "bidVol": quote.bid_vol,
    "askVol": quote.ask_vol,
  }


async def test_unbound_stopped_source_still_receives_quotes_and_closes_exit(
  sessions, local_sessions
):
  _, decision = await exit_tests.prepared(sessions, stopped=True)
  clock = [exit_tests.quote(2).timestamp]
  supervisor = TAssistantPaperShadowSupervisor(
    session_factory=sessions, clock=lambda: clock[0]
  )
  assert not supervisor._bindings
  await supervisor._on_quote_batch({"600000.SH": raw_quote(2)})
  accepted = await exit_tests.route(sessions, decision)
  async with sessions() as db:
    reason = (await db.get(TradeIntentRecord, "sell-intent")).notes
  assert accepted and accepted["success"], reason
  clock[0] = exit_tests.quote(3).timestamp
  data = {"600000.SH": raw_quote(3)}
  await supervisor._on_quote_batch(data)
  async with sessions() as db:
    assert (await db.get(AutoExitPlanRecord, "paper-plan")).status == "COMPLETED"
    count = await db.scalar(select(func.count()).select_from(PaperExecutionEventRecord))
  await supervisor._on_quote_batch(data)
  async with sessions() as db:
    assert (
      await db.scalar(select(func.count()).select_from(PaperExecutionEventRecord))
      == count
    )


async def test_missing_book_does_not_fabricate_paper_quote(sessions, local_sessions):
  await exit_tests.prepared(sessions, stopped=True)
  supervisor = TAssistantPaperShadowSupervisor(
    session_factory=sessions, clock=lambda: exit_tests.quote(2).timestamp
  )
  async with sessions() as db:
    count = await db.scalar(select(func.count()).select_from(PaperExecutionEventRecord))
  raw = raw_quote(2)
  raw["askVol"] = [400]
  await supervisor._on_quote_batch({"600000.SH": raw})
  async with sessions() as db:
    assert (
      await db.scalar(select(func.count()).select_from(PaperExecutionEventRecord))
      == count
    )


async def test_same_millisecond_new_hub_batch_cannot_fill_twice(
  sessions, local_sessions
):
  _, decision = await exit_tests.prepared(sessions, stopped=True)
  clock = [exit_tests.quote(2).timestamp]
  supervisor = TAssistantPaperShadowSupervisor(
    session_factory=sessions, clock=lambda: clock[0]
  )
  await supervisor._on_quote_batch({"600000.SH": raw_quote(2)})
  assert (await exit_tests.route(sessions, decision))["success"]
  clock[0] = exit_tests.quote(3).timestamp
  first = raw_quote(3)
  first["bidVol"] = [200, 0, 0, 0, 0]
  await supervisor._on_quote_batch({"600000.SH": first})
  async with sessions() as db:
    before = await db.scalar(select(func.count()).select_from(PaperExecutionFillRecord))
    assert (await db.get(AutoExitPlanRecord, "paper-plan")).remaining_volume > 0
  same_source = raw_quote(3)
  same_source["market_stream_sequence"] = 4
  same_source["tick_ordinal"] = 4
  await supervisor._on_quote_batch({"600000.SH": same_source})
  async with sessions() as db:
    assert (
      await db.scalar(select(func.count()).select_from(PaperExecutionFillRecord))
      == before
    )
    latest = await db.scalar(
      select(PaperExecutionEventRecord)
      .order_by(PaperExecutionEventRecord.revision.desc())
      .limit(1)
    )
    assert latest.result_payload["reason_codes"] == [
      "PAPER_QUOTE_SAME_SOURCE_NO_NEW_LIQUIDITY"
    ]
  clock[0] = exit_tests.quote(4).timestamp
  next_source = raw_quote(4)
  next_source["market_stream_sequence"] = 5
  next_source["tick_ordinal"] = 5
  await supervisor._on_quote_batch({"600000.SH": next_source})
  async with sessions() as db:
    assert (await db.get(AutoExitPlanRecord, "paper-plan")).status == "COMPLETED"


async def test_pump_uses_acceptance_clock_after_lock_not_batch_capture(sessions):
  await exit_tests.prepared(sessions, stopped=True)
  captured, accepted = exit_tests.quote(2).timestamp, exit_tests.quote(3).timestamp
  await PaperMarketRuntime(
    session_factory=sessions, clock=lambda: accepted
  ).on_quote_batch(
    {"600000.SH": raw_quote(2)},
    active_instruments={},
    now=captured,
  )
  from quantx_infrastructure.services.paper_execution_ledger import _stored_time

  async with sessions() as db:
    latest = await db.scalar(
      select(PaperExecutionEventRecord)
      .order_by(PaperExecutionEventRecord.revision.desc())
      .limit(1)
    )
    assert _stored_time(latest.quote_source_at) == captured
    assert _stored_time(latest.occurred_at) == accepted
