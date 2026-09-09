"""Actual LIVE capacity/Sizer/risk, with separately tested gate/approval boundaries."""

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_application.t_trade_v3.daily_t_valuation import TValuationMark
from quantx_contracts import ExecutionEnvironment
from quantx_infrastructure.models.agent_runtime import PendingTradeOrder
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services import live_entry_execution_review as review_module
from sqlalchemy import func, select

from tests.application.test_t_entry_execution_gate import gate_input as _gate_input
from tests.infrastructure.test_live_portfolio_snapshot import CODE, NOW, add_ready
from tests.infrastructure.test_live_portfolio_snapshot import base_db as _base_db
from tests.infrastructure.test_live_portfolio_snapshot import (
  capacity_db as _capacity_db,
)
from tests.infrastructure.test_live_portfolio_snapshot import db as _db
from tests.infrastructure.test_paper_execution_ledger import quote

gate_input = _gate_input
base_db = _base_db
capacity_db = _capacity_db
db = _db


@pytest.mark.parametrize(
  "fault",
  [None, "other", "empty_book", "limit_missing", "market_mismatch", "approval", "zero"],
)
async def test_live_review_sizes_against_current_allocation_and_preserves_no_order_boundary(
  db, gate_input, monkeypatch, fault
):
  async with db.begin():
    await add_ready(db, "own")
    intent = await db.get(TradeIntentRecord, "own")
    intent.reason = "T_ENTRY_CANDIDATE"
    intent.target_amount = 2000
    intent.limit_price_hint = 9.9
    intent.intent_metadata = {
      **intent.intent_metadata,
      "intent_created_at": NOW.isoformat(),
    }
    from sqlalchemy.orm.attributes import flag_modified

    intent.updated_at = NOW
    flag_modified(intent, "updated_at")
    if fault == "other":
      await add_ready(db, "other")
    if fault == "zero":
      intent.target_amount = 10
    market = replace(quote(0), timestamp=NOW)
    ms = int(NOW.timestamp() * 1000)
    sample = replace(
      gate_input.latest_tick.sample,
      source_time_ms=ms,
      price=market.price,
      bid_price=market.bid_price[0],
      ask_price=market.ask_price[0],
      bid_volume=market.bid_vol[0],
      ask_volume=market.ask_vol[0],
    )
    gate_input = replace(
      gate_input,
      execution_environment=ExecutionEnvironment.LIVE,
      latest_tick=replace(gate_input.latest_tick, sample=sample, received_at_ms=ms),
    )
    if fault == "empty_book":
      market = replace(market, ask_price=[])
    elif fault == "limit_missing":
      market = replace(market, limit_up=None)
    elif fault == "market_mismatch":
      market = replace(market, price=10)
    gate = AsyncMock(
      return_value=SimpleNamespace(
        outcome="ALLOW", reason_codes=(), candidate=gate_input.candidate
      )
    )
    authority = AsyncMock(return_value="user")
    if fault == "approval":
      authority.side_effect = ValueError("T_ENTRY_CONSUMED_CHALLENGE_REQUIRED")
    monkeypatch.setattr(review_module, "review_t_entry_gate", gate)
    monkeypatch.setattr(review_module, "authorize_live_entry", authority)

    class Marks:
      async def read(self, **kwargs):
        return SimpleNamespace(
          as_of=kwargs["as_of"],
          current={CODE: TValuationMark(CODE, Decimal("9.9"), NOW, "quote")},
          opening={},
        )

    call = review_module.LiveEntryExecutionReview(db).review(
      execution_id="new-source",
      intent_id="own",
      gate_input=gate_input,
      market_data=market,
      market_mark_reader=Marks(),
      now=NOW,
    )
    if fault == "approval":
      with pytest.raises(ValueError, match="T_ENTRY_CONSUMED_CHALLENGE_REQUIRED"):
        await call
    else:
      result = await call
      if fault:
        assert result.outcome != "REVIEWED", result
        authority.assert_not_awaited()
      else:
        assert result.outcome == "REVIEWED", result.reason_codes
        assert (
          result.request.volume == 100
          and result.request.environment is ExecutionEnvironment.LIVE
        )
        assert result.user_id == "user" and result.risk.allowed
        assert result.sizing.metadata["allocated_amount_cap"] == "1000.00000000"
        assert authority.await_args.kwargs["volume"] == 100
    assert await db.scalar(select(func.count()).select_from(PendingTradeOrder)) == 0
