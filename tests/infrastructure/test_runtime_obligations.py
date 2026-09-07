from datetime import datetime

import pytest
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
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
  "kind", ["order", "report", "intent", "reservation"]
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
      owner_type="STRATEGY_RUN",
      owner_id="run",
      environment="LIVE",
      strategy_order_id="strategy-order-1",
      intent_id="intent-1",
    ),
    "report": lambda: StrategyRuntimeEvent(
      event_id="event",
      business_key="trade:1",
      owner_type="STRATEGY_RUN",
      owner_id="run",
      environment="LIVE",
      client_order_id="order",
      event_type="TRADE",
      payload={},
      application_status="FAILED",
      created_at=datetime.now(),
    ),
    "intent": lambda: TradeIntentRecord(
      id="intent",
      owner_type="STRATEGY_RUN",
      owner_id="run",
      environment="LIVE",
      instrument_code="600000.SH",
      direction="BUY",
      idempotency_key="intent-1",
      status="AWAITING_APPROVAL",
    ),
    "protection": lambda: AutoExitPlanRecord(
      plan_id="exit",
      account_id="account",
      instrument_code="600000.SH",
      strategy_run_id="run",
      source_execution_owner_type="STRATEGY_RUN",
      source_execution_owner_id="run",
      source_execution_environment="LIVE",
      environment="LIVE",
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
      source_execution_owner_type="STRATEGY_RUN",
      source_execution_owner_id="run",
      source_execution_environment="LIVE",
      environment="LIVE",
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
    owner = ExecutionOwnerRef.strategy_run("run")
    other_owner = ExecutionOwnerRef.strategy_run("other-run")
    assert await runtime_obligation_blocker(db, owner, ExecutionEnvironment.LIVE)
    assert (
      await runtime_obligation_blocker(db, other_owner, ExecutionEnvironment.LIVE)
      is None
    )


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
        owner_type="STRATEGY_RUN",
        owner_id="run",
        environment="LIVE",
        strategy_order_id="strategy-order-1",
        intent_id="intent-1",
      )
    )
    await db.commit()
    assert (
      await runtime_obligation_blocker(
        db,
        ExecutionOwnerRef.strategy_run("run"),
        ExecutionEnvironment.LIVE,
      )
      is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["protection", "batch"])
async def test_downstream_exit_obligation_does_not_hold_source_runtime_open(
  obligations,
  kind,
):
  records = {
    "protection": AutoExitPlanRecord(
      plan_id="exit",
      account_id="account",
      instrument_code="600000.SH",
      strategy_run_id="run",
      source_execution_owner_type="STRATEGY_RUN",
      source_execution_owner_id="run",
      source_execution_environment="LIVE",
      environment="LIVE",
      source_type="T_TRADE_BATCH",
      source_id="batch",
      status="ERROR",
      enabled=False,
      remaining_volume=100,
      protected_volume=100,
      entry_avg_price=10,
    ),
    "batch": TTradeBatch(
      batch_id="batch",
      strategy_run_id="run",
      source_execution_owner_type="STRATEGY_RUN",
      source_execution_owner_id="run",
      source_execution_environment="LIVE",
      environment="LIVE",
      account_id="account",
      instrument_code="600000.SH",
      status="ERROR",
      entry_filled_volume=100,
      exit_filled_volume=0,
    ),
  }
  async with obligations() as db:
    db.add(records[kind])
    await db.commit()
    assert (
      await runtime_obligation_blocker(
        db,
        ExecutionOwnerRef.strategy_run("run"),
        ExecutionEnvironment.LIVE,
      )
      is None
    )
