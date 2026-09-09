"""Public replacement staging/decoding and replay, with an explicit risk-review seam."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_domain.brokers.base import OrderRequest, OrderType, PriceType
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_domain.trading.risk_checker import OrderRiskDecision
from quantx_infrastructure.models.agent_runtime import PendingTradeOrder
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantExecutionEventRecord,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services import live_entry_replacement_staging as staging
from quantx_infrastructure.services.live_entry_dispatch_review import (
  revalidate_live_entry_dispatch,
)
from quantx_infrastructure.services.live_entry_replacement_execution_review import (
  LiveEntryReplacementReviewResult,
)
from quantx_infrastructure.services.live_entry_replacement_review import (
  LiveEntryReplacementPreflight,
)
from quantx_infrastructure.services.trade_command_service import TradeCommandService
from sqlalchemy import func, select

from tests.infrastructure import test_live_portfolio_snapshot as portfolio

capacity_db = portfolio.capacity_db
base_db = portfolio.base_db
db = portfolio.db
NOW, CODE = portfolio.NOW, portfolio.CODE


@pytest.mark.parametrize(
  "fault",
  [
    None,
    "market",
    "audit",
    "reject",
    "tamper",
    "expired",
    "filled",
    "refresh",
    "wrong_fresh_parent",
  ],
)
async def test_stage_replacement_is_atomic_and_has_exact_dispatch_evidence(
  db, monkeypatch, fault
):
  original = NOW - timedelta(seconds=31)
  expires = NOW + timedelta(seconds=29)
  previous = {
    "version": "risk-increase-order-request.v1",
    "idempotency_key": "t-entry:own",
  }
  async with db.begin():
    await portfolio.add_ready(db, "own")
    intent = await db.get(TradeIntentRecord, "own")
    intent.status = "PARTIAL_FILLED"
    intent.executed_volume = 100
    intent.intent_metadata = {
      **intent.intent_metadata,
      "risk_increase_order_request": previous,
    }
    intent.updated_at = NOW
    db.add(
      PendingTradeOrder(
        client_order_id="parent",
        user_id="user",
        account_id="account",
        instrument_code=CODE,
        owner_type="T_ASSISTANT_EXECUTION",
        owner_id="new-source",
        environment="LIVE",
        side="BUY",
        order_type="FIX_PRICE",
        volume=200,
        limit_price="9.9",
        status="CANCELLED",
        intent_id="own",
        batch_id="batch-own",
        bucket="swing",
        t_trade_role="ENTRY",
        t_order_attempt=0,
        t_order_original_created_at=original,
        trace_id="original-trace",
        created_at=original,
        updated_at=NOW,
      )
    )
  proof = LiveEntryReplacementPreflight(
    "parent",
    "own",
    "new-source",
    "user",
    100,
    100,
    Decimal("9.9"),
    original,
    expires,
    1,
  )
  request = OrderRequest(
    instrument_code=CODE,
    order_type=OrderType.BUY,
    price_type=PriceType.LIMIT,
    volume=100,
    price=9.9,
    execution_ref=ExecutionOwnerRef("T_ASSISTANT_EXECUTION", "new-source"),
    environment=ExecutionEnvironment.LIVE,
    metadata={
      "portfolio_input_fingerprint": "a" * 64,
      "order_expire_at_ms": int(expires.timestamp() * 1000),
    },
  )
  result = LiveEntryReplacementReviewResult(
    "REVIEWED",
    (),
    "user",
    request,
    None,
    OrderRiskDecision.allow(request),
    proof,
    "a" * 64,
  )
  if fault == "reject":
    result = LiveEntryReplacementReviewResult(
      "REJECT", ("T_ACCOUNT_ENTRY_DISABLED",), preflight=proof
    )
  reviewer = AsyncMock(return_value=result)
  monkeypatch.setattr(staging.LiveEntryReplacementExecutionReview, "review", reviewer)
  if fault == "audit":
    monkeypatch.setattr(
      staging.TAssistantExecutionRepository,
      "append_event",
      AsyncMock(side_effect=RuntimeError("audit-failed")),
    )
  calls = 0

  def witness():
    nonlocal calls
    calls += 1
    if fault == "market" and calls == 2:
      raise ValueError("market-lost")

  market = MarketDataSnapshot(
    CODE,
    timestamp=NOW,
    price=9.88,
    price_tick=0.01,
    limit_up=11,
    limit_down=9,
    ask_price=[9.88],
  )
  args = dict(
    client_order_id="parent",
    market_data=market,
    market_mark_reader=None,
    now=NOW,
    validate_market=witness,
  )
  async with db.begin():
    if fault in {"market", "audit"}:
      with pytest.raises((ValueError, RuntimeError), match="market-lost|audit-failed"):
        await staging.stage_live_entry_replacement(db, **args)
    else:
      first = await staging.stage_live_entry_replacement(db, **args)
      assert first.status == ("REJECT" if fault == "reject" else "STAGED")
      again = await staging.stage_live_entry_replacement(db, **args)
      assert again.status == "ALREADY_RECORDED" and again.event_key == first.event_key
  async with db.begin():
    row = await db.get(TradeIntentRecord, "own", populate_existing=True)
    events = list(
      await db.scalars(
        select(TAssistantExecutionEventRecord).where(
          TAssistantExecutionEventRecord.event_type == "LIVE_ENTRY_REPLACEMENT_REVIEWED"
        )
      )
    )
    if fault in {"market", "audit"}:
      assert not events and row.status == "PARTIAL_FILLED"
      assert row.intent_metadata["risk_increase_order_request"] == previous
      return
    if fault == "reject":
      assert row.status == "PARTIAL_FILLED"
      assert row.intent_metadata["risk_increase_order_request"] == previous
      return
    assert len(events) == 1
    assert events[0].payload["previous_request"] == previous
    assert row.status == "EXECUTION_READY" and row.executed_volume == 100
    decoded = TradeCommandService._ready_order_request(row)
    assert decoded["_t_order_parent_client_id"] == "parent"
    assert decoded["trace_id"] == "original-trace"
    assert decoded["idempotency_key"] == "t-order:own:replace:1"
    assert decoded["volume"] == 100
    current = NOW
    if fault == "tamper":
      raw = dict(row.intent_metadata["risk_increase_order_request"])
      raw["t_order_parent_client_id"] = "other"
      row.intent_metadata = {**row.intent_metadata, "risk_increase_order_request": raw}
    elif fault == "filled":
      row.executed_volume = 101
    elif fault == "expired":
      current += timedelta(seconds=3)
    elif fault == "refresh":
      fresh_args = {
        **args,
        "now": NOW + timedelta(seconds=1),
        "market_data": replace(market, timestamp=NOW + timedelta(seconds=1)),
      }
      refreshed = await staging.stage_live_entry_replacement(db, **fresh_args)
      assert refreshed.status == "STAGED" and refreshed.event_key != first.event_key
      current += timedelta(seconds=1)
    fresh = AsyncMock(
      return_value=replace(
        result, preflight=replace(proof, parent_client_order_id="other")
      )
      if fault == "wrong_fresh_parent"
      else result
    )
    check = revalidate_live_entry_dispatch(
      db,
      intent=row,
      volume=100,
      limit_price=Decimal("9.9"),
      now=current,
      fresh_review=fresh,
    )
    if fault in {"tamper", "filled", "expired", "wrong_fresh_parent"}:
      with pytest.raises(ValueError, match="LIVE_REPLACEMENT_"):
        await check
    else:
      assert await check == result
    assert await db.scalar(select(func.count()).select_from(PendingTradeOrder)) == 1
