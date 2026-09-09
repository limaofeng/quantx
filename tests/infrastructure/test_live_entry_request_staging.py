"""Atomic public request and audit staging; no broker or account dispatch."""

from unittest.mock import AsyncMock

import pytest
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_domain.brokers.base import OrderRequest, OrderType, PriceType
from quantx_domain.trading.risk_checker import OrderRiskDecision
from quantx_infrastructure.models.agent_runtime import PendingTradeOrder
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantExecutionEventRecord,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services import live_entry_request_staging as staging
from quantx_infrastructure.services.live_entry_execution_review import (
  LiveEntryReviewResult,
)
from quantx_infrastructure.services.trade_command_service import TradeCommandService
from sqlalchemy import func, select

from tests.application.test_t_entry_execution_gate import gate_input as _gate_input
from tests.infrastructure.test_live_portfolio_snapshot import CODE, NOW, add_ready
from tests.infrastructure.test_live_portfolio_snapshot import base_db as _base_db
from tests.infrastructure.test_live_portfolio_snapshot import (
  capacity_db as _capacity_db,
)
from tests.infrastructure.test_live_portfolio_snapshot import db as _db
from tests.infrastructure.test_paper_execution_ledger import quote

base_db = _base_db
capacity_db = _capacity_db
db = _db
gate_input = _gate_input


@pytest.mark.parametrize("fault", [None, "market", "audit", "reject", "changed"])
async def test_stage_is_atomic_and_replays_exact_review(
  db, gate_input, monkeypatch, fault
):
  async with db.begin():
    await add_ready(db, "own")
  request = OrderRequest(
    instrument_code=CODE,
    order_type=OrderType.BUY,
    price_type=PriceType.LIMIT,
    volume=100,
    price=9.9,
    execution_ref=ExecutionOwnerRef("T_ASSISTANT_EXECUTION", "new-source"),
    environment=ExecutionEnvironment.LIVE,
    metadata={"portfolio_input_fingerprint": "a" * 64},
  )
  result = LiveEntryReviewResult(
    "REVIEWED", (), "user", request, None, OrderRiskDecision.allow(request)
  )
  if fault == "reject":
    result = LiveEntryReviewResult("REJECT", ("T_ACCOUNT_ENTRY_DISABLED",))
  reviewer = AsyncMock(return_value=result)
  monkeypatch.setattr(staging.LiveEntryExecutionReview, "review", reviewer)
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

  args = dict(
    execution_id="new-source",
    intent_id="own",
    gate_input=gate_input,
    market_data=quote(0),
    market_mark_reader=None,
    now=NOW,
    validate_market=witness,
  )
  async with db.begin():
    if fault in {"market", "audit"}:
      with pytest.raises((ValueError, RuntimeError), match="market-lost|audit-failed"):
        await staging.stage_live_entry_request(db, **args)
    else:
      first = await staging.stage_live_entry_request(db, **args)
      assert first.status == ("REJECT" if fault == "reject" else "STAGED")
      if fault == "changed":
        request.metadata["portfolio_input_fingerprint"] = "b" * 64
        with pytest.raises(ValueError, match="REQUEST_ALREADY_EXISTS"):
          await staging.stage_live_entry_request(db, **args)
      else:
        replay = await staging.stage_live_entry_request(db, **args)
        assert (
          replay.status == "ALREADY_RECORDED" and replay.event_key == first.event_key
        )
  async with db.begin():
    row = await db.get(TradeIntentRecord, "own", populate_existing=True)
    events = list(
      await db.scalars(
        select(TAssistantExecutionEventRecord).where(
          TAssistantExecutionEventRecord.event_type == "LIVE_ENTRY_REVIEWED"
        )
      )
    )
    if fault in {"market", "audit"}:
      assert not events and "risk_increase_order_request" not in row.intent_metadata
    elif fault == "reject":
      assert len(events) == 1 and events[0].payload["outcome"] == "REJECT"
      assert "risk_increase_order_request" not in row.intent_metadata
    else:
      assert len(events) == 1
      parsed = TradeCommandService._ready_order_request(row)
      assert parsed["volume"] == 100 and parsed["strategy_run_id"] == ""
      from quantx_infrastructure.services import trade_command_service

      monkeypatch.setattr(trade_command_service.time_utils, "now", lambda: NOW)
      policy = TradeCommandService._require_t_order_new_policy(
        role=parsed["t_trade_role"],
        order_type=parsed["order_type"],
        limit_price=parsed["limit_price"],
        intent=row,
        request_metadata=parsed["request_metadata"],
      )
      assert policy["t_entry_order_policy_version"] == "TEntryOrderPolicy.v1"
      assert parsed["execution_ref"].owner_id == "new-source"
      assert (
        events[0].payload["request"]
        == row.intent_metadata["risk_increase_order_request"]
      )
    assert await db.scalar(select(func.count()).select_from(PendingTradeOrder)) == 0
