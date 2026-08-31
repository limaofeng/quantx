from datetime import datetime

import pytest
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  PendingTradeOrder,
  StrategyRuntimeEvent,
  TTradeBatch,
)
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.strategy_run_state import StrategyRunState
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.runtime_obligations import (
  runtime_obligation_blocker,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.fixture
async def obligations():
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda db: Base.metadata.create_all(
        db,
        tables=[
          PendingTradeOrder.__table__,
          StrategyRuntimeEvent.__table__,
          TTradeBatch.__table__,
          AutoExitPlanRecord.__table__,
          StrategyRunState.__table__,
          TradeIntentRecord.__table__,
        ],
      )
    )
  yield async_sessionmaker(engine, expire_on_commit=False)
  await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "kind", ["order", "report", "intent", "protection", "batch", "reservation"]
)
async def test_persisted_obligation_survives_missing_runtime(obligations, kind):
  records = {
    "order": lambda: PendingTradeOrder(
      client_order_id="order",
      user_id="user",
      account_id="account",
      instrument_code="600000.SH",
      side="BUY",
      order_type="FIX_PRICE",
      limit_price="10",
      volume=100,
      status="RECONCILE_REQUIRED",
      execution_mode="live",
      strategy_run_id="run",
    ),
    "report": lambda: StrategyRuntimeEvent(
      event_id="event",
      business_key="trade:1",
      strategy_run_id="run",
      client_order_id="order",
      event_type="TRADE",
      payload={},
      application_status="FAILED",
      created_at=datetime.now(),
    ),
    "intent": lambda: TradeIntentRecord(
      id="intent",
      strategy_run_id="run",
      instrument_code="600000.SH",
      direction="BUY",
      status="AWAITING_APPROVAL",
    ),
    "protection": lambda: AutoExitPlanRecord(
      plan_id="exit",
      account_id="account",
      instrument_code="600000.SH",
      strategy_run_id="run",
      source_type="STRATEGY",
      source_id="run",
      status="ERROR",
      enabled=False,
      remaining_volume=100,
      protected_volume=100,
      entry_avg_price=10,
    ),
    "batch": lambda: TTradeBatch(
      batch_id="batch",
      strategy_run_id="run",
      account_id="account",
      instrument_code="600000.SH",
      status="ERROR",
      entry_filled_volume=100,
      exit_filled_volume=0,
    ),
    "reservation": lambda: StrategyRunState(
      run_id="run",
      custom_state={"order_cash_reservations": {"order": 1000}},
    ),
  }
  async with obligations() as db:
    db.add(records[kind]())
    await db.commit()
    assert await runtime_obligation_blocker(db, "run")
    assert await runtime_obligation_blocker(db, "other-run") is None


@pytest.mark.asyncio
async def test_settled_orders_do_not_block_stop(obligations):
  async with obligations() as db:
    db.add(
      PendingTradeOrder(
        client_order_id="order",
        user_id="user",
        account_id="account",
        instrument_code="600000.SH",
        side="BUY",
        order_type="FIX_PRICE",
        limit_price="10",
        volume=100,
        status="CANCELLED",
        execution_mode="live",
        strategy_run_id="run",
      )
    )
    await db.commit()
    assert await runtime_obligation_blocker(db, "run") is None
