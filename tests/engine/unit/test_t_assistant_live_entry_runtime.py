"""Real request/audit staging under Engine dispatch; review is an explicit test seam."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_domain.brokers.base import OrderRequest, OrderType, PriceType
from quantx_domain.trading.risk_checker import OrderRiskDecision
from quantx_engine.t_assistant_live_entry_runtime import TAssistantLiveEntryRuntime
from quantx_infrastructure.models.agent_runtime import (
  PendingTradeOrder,
  TradeCommandOutbox,
)
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantExecutionEventRecord,
  TAssistantExecutionRecord,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services import live_entry_request_staging as staging
from quantx_infrastructure.services.live_entry_execution_review import (
  LiveEntryReviewResult,
)
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.application.test_t_entry_execution_gate import gate_input as _gate_input
from tests.infrastructure.test_live_entry_dispatch_review import at_now
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


@pytest.mark.parametrize(
  "fault",
  [None, "reject", "delay", "expired", "missing", "changed", "pending", "blocked"],
)
async def test_dispatch_stages_or_terminalizes_once_and_rolls_back_lost_witness(
  db, gate_input, monkeypatch, fault
):
  # SQLite legacy mode otherwise releases SAVEPOINT as an independent commit.
  event.listen(
    db.bind.sync_engine,
    "begin",
    lambda connection: connection.exec_driver_sql("BEGIN"),
  )
  async with db.begin():
    await db.run_sync(
      lambda session: TradeCommandOutbox.__table__.create(
        session.connection(), checkfirst=True
      )
    )
    await add_ready(db, "own")
    if fault == "pending":
      db.add(
        PendingTradeOrder(
          client_order_id="pending",
          user_id="user",
          account_id="account",
          owner_type="T_ASSISTANT_EXECUTION",
          owner_id="new-source",
          environment="LIVE",
          instrument_code=CODE,
          side="BUY",
          order_type="FIX_PRICE",
          limit_price="10",
          volume=100,
          status="UNKNOWN",
          intent_id="own",
          created_at=NOW,
          updated_at=NOW,
        )
      )
    if fault == "blocked":
      source = await db.get(TAssistantExecutionRecord, "new-source")
      source.entry_readiness = "DEGRADED"
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
  reviewed = LiveEntryReviewResult(
    "REVIEWED", (), "user", request, None, OrderRiskDecision.allow(request)
  )
  if fault in {"reject", "delay", "expired"}:
    reviewed = LiveEntryReviewResult(
      "DELAY" if fault == "delay" else "REJECT",
      ("T_ENTRY_EXPIRED" if fault == "expired" else "T_GATE_BLOCKED",),
    )
  reviewer = AsyncMock(return_value=reviewed)
  monkeypatch.setattr(staging.LiveEntryExecutionReview, "review", reviewer)
  calls = 0

  def validate():
    nonlocal calls
    calls += 1
    if fault == "changed" and calls == 3:
      raise ValueError("LIVE_ENTRY_MARKET_WITNESS_CHANGED")

  prepare = AsyncMock(
    return_value=SimpleNamespace(
      gate_input=at_now(gate_input),
      market_data=quote(0),
      now=NOW,
      validate_market=validate,
    )
  )
  if fault == "missing":
    prepare.side_effect = ValueError("LIVE_ENTRY_LATEST_MARKET_REQUIRED")
  runtime = TAssistantLiveEntryRuntime(
    session_factory=async_sessionmaker(db.bind, expire_on_commit=False),
    clock=lambda: NOW,
    review_adapter_factory=lambda session: SimpleNamespace(
      prepare=prepare, market_mark_reader=None
    ),
  )
  if fault == "changed":
    with pytest.raises(ValueError, match="LIVE_ENTRY_MARKET_WITNESS_CHANGED"):
      await runtime.dispatch(execution_id="new-source", validate_market=lambda: None)
  else:
    result = await runtime.dispatch(
      execution_id="new-source", validate_market=lambda: None
    )
    assert result.staged == (("own",) if fault is None else ())
    assert result.terminalized == (
      ("own",) if fault in {"reject", "delay", "expired", "missing"} else ()
    )
    repeated = await runtime.dispatch(
      execution_id="new-source", validate_market=lambda: None
    )
    assert repeated.status == ("BLOCKED" if fault == "blocked" else "IDLE")
  async with db.begin():
    row = await db.get(TradeIntentRecord, "own", populate_existing=True)
    assert row.status == {
      "reject": "REJECTED",
      "delay": "CANCELLED",
      "expired": "EXPIRED",
      "missing": "CANCELLED",
    }.get(fault, "EXECUTION_READY")
    assert ("risk_increase_order_request" in row.intent_metadata) == (fault is None)
    events = list(
      await db.scalars(
        select(TAssistantExecutionEventRecord).where(
          TAssistantExecutionEventRecord.event_type.in_(
            ["LIVE_ENTRY_REVIEWED", "LIVE_ENTRY_REVIEW_BLOCKED"]
          )
        )
      )
    )
    assert len(events) == (0 if fault in {"changed", "pending", "blocked"} else 1)
    assert await db.scalar(select(func.count()).select_from(PendingTradeOrder)) == (
      1 if fault == "pending" else 0
    )
  if fault in {"pending", "blocked"}:
    prepare.assert_not_awaited()
