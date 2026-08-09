from decimal import Decimal

import pytest
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  AgentDevice,
  PendingTradeOrder,
  StrategyOrderCorrelation,
  TradeCommandOutbox,
  TTradeBatch,
)
from quantx_infrastructure.models.auth import AuthUser
from quantx_infrastructure.services.trade_command_service import (
  AgentUnavailableError,
  TradeCommandService,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

TABLES = [
  AuthUser.__table__,
  AgentDevice.__table__,
  PendingTradeOrder.__table__,
  StrategyOrderCorrelation.__table__,
  TTradeBatch.__table__,
  TradeCommandOutbox.__table__,
]


@pytest.mark.asyncio
async def test_trade_command_business_key_is_deduplicated() -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=TABLES,
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  async with session_factory() as db:
    db.add(
      AgentDevice(
        id="device-1",
        user_id="user-1",
        name="test",
        secret_hash="x" * 64,
        authorized_account_ids=["account-1"],
        capabilities=["paper"],
      )
    )
    await db.commit()
    service = TradeCommandService(db)
    first = await service.enqueue_order(
      user_id="user-1",
      account_id="account-1",
      instrument_code="600000.SH",
      side="BUY",
      order_type="FIX_PRICE",
      limit_price=Decimal("10"),
      volume=100,
      idempotency_key="ui-request-1",
    )
    second = await service.enqueue_order(
      user_id="user-1",
      account_id="account-1",
      instrument_code="600000.SH",
      side="BUY",
      order_type="FIX_PRICE",
      limit_price=Decimal("10"),
      volume=100,
      idempotency_key="ui-request-1",
    )

    assert second == first
    assert (
      await db.scalar(select(func.count()).select_from(TradeCommandOutbox))
      == 1
    )
    assert (
      await db.scalar(select(func.count()).select_from(PendingTradeOrder))
      == 1
    )
  await engine.dispose()


@pytest.mark.asyncio
async def test_cancel_command_business_key_is_deduplicated() -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=TABLES,
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  async with session_factory() as db:
    db.add(
      AgentDevice(
        id="device-1",
        user_id="user-1",
        name="test",
        secret_hash="x" * 64,
        authorized_account_ids=["account-1"],
        capabilities=["paper"],
      )
    )
    await db.commit()
    service = TradeCommandService(db)
    first = await service.enqueue_cancel(
      user_id="user-1",
      account_id="account-1",
      broker_order_id="broker-order-1",
    )
    second = await service.enqueue_cancel(
      user_id="user-1",
      account_id="account-1",
      broker_order_id="broker-order-1",
    )

    assert second == first
    assert (
      await db.scalar(select(func.count()).select_from(TradeCommandOutbox))
      == 1
    )
  await engine.dispose()


@pytest.mark.asyncio
async def test_data_only_agent_cannot_receive_trade_commands() -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=TABLES,
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  async with session_factory() as db:
    db.add(
      AgentDevice(
        id="device-data",
        user_id="user-1",
        name="data-only",
        secret_hash="x" * 64,
        authorized_account_ids=["account-1"],
        capabilities=["market-data", "data-only"],
      )
    )
    await db.commit()

    with pytest.raises(AgentUnavailableError, match="具备交易能力"):
      await TradeCommandService(db).enqueue_order(
        user_id="user-1",
        account_id="account-1",
        instrument_code="600000.SH",
        side="BUY",
        order_type="FIX_PRICE",
        limit_price=Decimal("10"),
        volume=100,
      )

  await engine.dispose()


@pytest.mark.asyncio
async def test_strategy_order_context_is_preserved_without_manual_bucket() -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=TABLES,
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  async with session_factory() as db:
    db.add(
      AgentDevice(
        id="paper-device",
        user_id="user-1",
        name="paper",
        secret_hash="x" * 64,
        authorized_account_ids=["account-1"],
        capabilities=["paper"],
      )
    )
    await db.commit()
    queued = await TradeCommandService(db).enqueue_order(
      user_id="user-1",
      account_id="account-1",
      instrument_code="600000.SH",
      side="BUY",
      order_type="FIX_PRICE",
      limit_price=Decimal("10.50"),
      volume=100,
      strategy_run_id="run-1",
      strategy_order_id="strategy-order-1",
      intent_id="intent-1",
      batch_id="batch-1",
      bucket="swing",
      t_trade_role="entry",
      risk_decision_id="risk-1",
      substitution_plan={"source_bucket": "core", "volume": 100},
      request_metadata={"instrument_code": "600000.SH", "config_version": 3},
    )

    outbox = await db.get(TradeCommandOutbox, queued.message_id)
    pending = await db.get(PendingTradeOrder, queued.client_order_id)
    correlation = (
      await db.execute(
        select(StrategyOrderCorrelation).where(
          StrategyOrderCorrelation.client_order_id == queued.client_order_id
        )
      )
    ).scalar_one()
    batch = await db.get(TTradeBatch, "batch-1")

    assert outbox.payload["execution_mode"] == "paper"
    assert outbox.payload["bucket"] == "swing"
    assert outbox.payload["batch_id"] == "batch-1"
    assert outbox.payload["substitution_plan"]["source_bucket"] == "core"
    assert pending.strategy_run_id == "run-1"
    assert correlation.strategy_order_id == "strategy-order-1"
    assert correlation.t_trade_role == "ENTRY"
    assert batch.status == "ENTRY_QUEUED"
  await engine.dispose()


@pytest.mark.asyncio
async def test_paper_command_never_routes_to_live_only_agent() -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=TABLES,
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  async with session_factory() as db:
    db.add(
      AgentDevice(
        id="live-device",
        user_id="user-1",
        name="live",
        secret_hash="x" * 64,
        authorized_account_ids=["account-1"],
        capabilities=["live"],
      )
    )
    await db.commit()
    with pytest.raises(AgentUnavailableError, match="paper"):
      await TradeCommandService(db).enqueue_order(
        user_id="user-1",
        account_id="account-1",
        instrument_code="600000.SH",
        side="BUY",
        order_type="FIX_PRICE",
        limit_price=Decimal("10"),
        volume=100,
        execution_mode="paper",
      )
  await engine.dispose()
