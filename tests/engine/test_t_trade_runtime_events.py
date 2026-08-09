from __future__ import annotations

from decimal import Decimal

import pytest
from quantx_domain.clock import utcnow
from quantx_engine import report_processor
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  AgentReportInbox,
  PendingTradeOrder,
  StrategyOrderCorrelation,
  StrategyRuntimeEvent,
  TTradeBatch,
)
from quantx_infrastructure.models.auth import AuthUser
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_strategy_report_events_are_durable_and_applied_once(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  tables = [
    AuthUser.__table__,
    PendingTradeOrder.__table__,
    StrategyOrderCorrelation.__table__,
    StrategyRuntimeEvent.__table__,
    TTradeBatch.__table__,
  ]
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=tables,
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  monkeypatch.setattr(report_processor, "AsyncSessionLocal", sessions)

  async with sessions() as db:
    db.add(
      AuthUser(
        id="user-1",
        username="runtime-events",
        display_name="Runtime Events",
        password_hash="unused",
        permissions=[],
      )
    )
    db.add(
      PendingTradeOrder(
        client_order_id="client-1",
        user_id="user-1",
        account_id="account-1",
        instrument_code="600000.SH",
        side="BUY",
        order_type="FIX_PRICE",
        limit_price=str(Decimal("10.50")),
        volume=100,
        status="QUEUED",
        execution_mode="live",
        strategy_run_id="run-1",
        strategy_order_id="order-1",
        intent_id="intent-1",
        batch_id="batch-1",
        bucket="swing",
        t_trade_role="ENTRY",
        request_metadata={"instrument_code": "600000.SH"},
      )
    )
    db.add(
      StrategyOrderCorrelation(
        id="correlation-1",
        client_order_id="client-1",
        account_id="account-1",
        strategy_run_id="run-1",
        strategy_order_id="order-1",
        intent_id="intent-1",
        batch_id="batch-1",
        bucket="swing",
        t_trade_role="ENTRY",
        execution_mode="live",
        trace_id="trace-1",
        request_metadata={"instrument_code": "600000.SH"},
      )
    )
    db.add(
      TTradeBatch(
        batch_id="batch-1",
        account_id="account-1",
        instrument_code="600000.SH",
        strategy_run_id="run-1",
        target_volume=100,
        status="ENTRY_QUEUED",
      )
    )
    await db.commit()

  order_report = AgentReportInbox(
    message_id="report-order",
    device_id="device-1",
    message_type="order_report",
    client_order_id="client-1",
    raw_payload_hash="hash-order",
    business_idempotency_key="business-order",
    payload={
      "client_order_id": "client-1",
      "order": {
        "account_id": "account-1",
        "order_id": 123,
        "stock_code": "600000.SH",
        "order_type": 23,
        "order_status": 50,
        "order_volume": 100,
        "traded_volume": 0,
        "price": 10.5,
      },
    },
    received_at=utcnow(),
    processing_status="PROCESSING",
  )
  trade_report = AgentReportInbox(
    message_id="report-trade",
    device_id="device-1",
    message_type="execution_report",
    client_order_id="client-1",
    raw_payload_hash="hash-trade",
    business_idempotency_key="business-trade",
    payload={
      "client_order_id": "client-1",
      "execution": {
        "account_id": "account-1",
        "order_id": 123,
        "execution_id": "execution-1",
        "stock_code": "600000.SH",
        "order_type": 23,
        "traded_volume": 100,
        "traded_price": 10.5,
      },
    },
    received_at=utcnow(),
    processing_status="PROCESSING",
  )

  await report_processor._stage_runtime_events(order_report)
  await report_processor._stage_runtime_events(trade_report)
  await report_processor._stage_runtime_events(trade_report)

  applied: list[tuple[str, str]] = []

  async def capture(event: StrategyRuntimeEvent) -> None:
    applied.append((event.event_type, event.business_key))

  monkeypatch.setattr(report_processor, "_apply_runtime_event", capture)
  await report_processor._drain_runtime_events()

  async with sessions() as db:
    assert (
      await db.scalar(select(func.count()).select_from(StrategyRuntimeEvent))
      == 2
    )
    assert (
      await db.scalar(
        select(func.count())
        .select_from(StrategyRuntimeEvent)
        .where(StrategyRuntimeEvent.application_status == "APPLIED")
      )
      == 2
    )
    batch = await db.get(TTradeBatch, "batch-1")
    assert batch.entry_filled_volume == 100
    assert batch.entry_avg_price == pytest.approx(10.5)
    assert batch.status == "OPEN"

  assert [item[0] for item in applied] == ["ORDER", "TRADE"]
  await engine.dispose()
