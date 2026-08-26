from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal

import pytest
from quantx_domain.clock import utcnow
from quantx_domain.strategies.ashare_intraday_t_assistant import (
  AshareIntradayTAssistantStrategy,
)
from quantx_domain.strategies.base import StrategyContext, StrategyRunMode
from quantx_domain.trading import EXIT_PLAN_BOOK_STATE_KEY, ExitPlanBook
from quantx_engine import report_processor
from quantx_engine.strategy_executor import (
  ExecutionStatus,
  StrategyExecutor,
  StrategyRuntime,
)
from quantx_engine.strategy_manager import strategy_manager
from quantx_infrastructure.core.runtime_state_manager import RuntimeStateManager
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  AgentReportInbox,
  PendingTradeOrder,
  StrategyOrderCorrelation,
  StrategyRuntimeEvent,
  TTradeBatch,
)
from quantx_infrastructure.models.auth import AuthUser
from quantx_infrastructure.models.strategy_run_state import (
  StrategyRunPosition,
  StrategyRunState,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def _reconcile_trade_fixture(run_id: str):
  context = StrategyContext(
    run_id=run_id,
    mode=StrategyRunMode.LIVE,
    instruments=["600000.SH"],
    parameters={"account_id": "account-1"},
  )
  strategy = AshareIntradayTAssistantStrategy(context)
  batch_id = f"batch-{run_id}"
  intent_id = f"intent-{run_id}"
  plan_id = f"t-exit-{batch_id}"
  exit_plan_template = strategy.build_exit_plan_template(
    instrument_code="600000.SH",
    batch_id=batch_id,
    plan_id=plan_id,
  ).to_dict()
  metadata = {
    "instrument_code": "600000.SH",
    "strategy_order_id": f"order-{run_id}",
    "intent_id": intent_id,
    "t_batch_id": batch_id,
    "bucket": "swing",
    "t_trade_role": "entry",
    "exit_plan_id": plan_id,
    "exit_plan_template": exit_plan_template,
    "requested_entry_volume": 200,
  }
  strategy.state.update(
    {
      "instrument_states": {
        "600000.SH": {
          "status": "RECONCILE_REQUIRED",
          "pending_entry_intent_id": intent_id,
          "entry_order_status": "RECONCILE_REQUIRED",
          "reconciliation_reason": "DURABLE_REPORT_PENDING",
          "batch_id": batch_id,
          "exit_plan_id": plan_id,
          "entry_filled_volume": 0,
          "entry_avg_price": 0.0,
          "exit_filled_volume": 0,
        }
      }
    }
  )
  event = StrategyRuntimeEvent(
    event_id=f"runtime-event-{run_id}",
    business_key=f"trade:execution-{run_id}",
    strategy_run_id=run_id,
    client_order_id=f"client-{run_id}",
    broker_order_id="456",
    event_type="TRADE",
    payload={
      "report": {
        "stock_code": "600000.SH",
        "order_type": 23,
        "execution_id": f"execution-{run_id}",
        "traded_volume": 100,
        "traded_price": 10.0,
        "traded_time": datetime(2026, 8, 14, 10, 0).isoformat(),
      },
      "metadata": metadata,
    },
    application_status="PENDING",
    application_attempts=0,
    created_at=utcnow(),
  )
  return context, strategy, event, batch_id, plan_id


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
    TradeIntentRecord.__table__,
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
      TradeIntentRecord(
        id="intent-1",
        owner_type="STRATEGY_RUN",
        owner_id="run-1",
        account_id="account-1",
        instrument_code="600000.SH",
        direction="BUY",
        bucket="swing",
        reason="T_ENTRY",
        target_volume=100,
        status="PENDING",
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
  monkeypatch.setattr(
    strategy_manager.executor,
    "require_durable_event_consumer",
    lambda _run_id: None,
  )
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
    intent = await db.get(TradeIntentRecord, "intent-1")
    assert intent.executed_volume == 100
    assert intent.executed_price == pytest.approx(10.5)
    assert intent.status == "FILLED"

  assert [item[0] for item in applied] == ["ORDER", "TRADE"]
  await engine.dispose()


@pytest.mark.asyncio
async def test_terminal_order_projection_waits_for_trade_volume_before_final_state(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  tables = [
    AuthUser.__table__,
    PendingTradeOrder.__table__,
    StrategyOrderCorrelation.__table__,
    StrategyRuntimeEvent.__table__,
    TTradeBatch.__table__,
    TradeIntentRecord.__table__,
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

  class ProjectionExecutor:
    def arm_durable_event_barrier(self, _run_id: str, _event_key: str) -> None:
      return None

    async def refresh_durable_event_barrier(self, _run_id: str) -> None:
      return None

  monkeypatch.setattr(strategy_manager, "executor", ProjectionExecutor())
  async with sessions() as db:
    db.add(
      AuthUser(
        id="user-projection",
        username="runtime-projection",
        display_name="Runtime Projection",
        password_hash="unused",
        permissions=[],
      )
    )
    for role, client_id, intent_id, batch_id, side, terminal_status in (
      ("ENTRY", "client-entry-gap", "intent-entry-gap", "batch-entry-gap", "BUY", "FILLED"),
      ("EXIT", "client-exit-gap", "intent-exit-gap", "batch-exit-gap", "SELL", "CANCELLED"),
    ):
      db.add(
        TradeIntentRecord(
          id=intent_id,
          owner_type="STRATEGY_RUN",
          owner_id=f"run-{role.lower()}-gap",
          account_id="account-1",
          instrument_code="600000.SH",
          direction=side,
          bucket="swing",
          reason=f"T_{role}",
          target_volume=100,
          status=terminal_status,
        )
      )
      db.add(
        PendingTradeOrder(
          client_order_id=client_id,
          user_id="user-projection",
          account_id="account-1",
          instrument_code="600000.SH",
          side=side,
          order_type="FIX_PRICE",
          limit_price=str(Decimal("10.00")),
          volume=100,
          status=terminal_status,
          execution_mode="live",
          strategy_run_id=f"run-{role.lower()}-gap",
          strategy_order_id=f"order-{role.lower()}-gap",
          intent_id=intent_id,
          batch_id=batch_id,
          bucket="swing",
          t_trade_role=role,
          request_metadata={"instrument_code": "600000.SH"},
        )
      )
      db.add(
        StrategyOrderCorrelation(
          id=f"correlation-{role.lower()}-gap",
          client_order_id=client_id,
          account_id="account-1",
          strategy_run_id=f"run-{role.lower()}-gap",
          strategy_order_id=f"order-{role.lower()}-gap",
          intent_id=intent_id,
          batch_id=batch_id,
          bucket="swing",
          t_trade_role=role,
          execution_mode="live",
          trace_id=f"trace-{role.lower()}-gap",
          request_metadata={"instrument_code": "600000.SH"},
        )
      )
      db.add(
        TTradeBatch(
          batch_id=batch_id,
          account_id="account-1",
          instrument_code="600000.SH",
          strategy_run_id=f"run-{role.lower()}-gap",
          target_volume=100,
          entry_filled_volume=100 if role == "EXIT" else 0,
          entry_avg_price=10.0 if role == "EXIT" else 0.0,
          status="EXIT_SUBMITTED" if role == "EXIT" else "ENTRY_SUBMITTED",
        )
      )
    await db.commit()

  def order_report(
    *,
    role: str,
    client_id: str,
    broker_id: int,
    status: str,
    traded_volume: int,
  ) -> AgentReportInbox:
    return AgentReportInbox(
      message_id=f"report-{role.lower()}-terminal",
      device_id="device-1",
      message_type="order_report",
      client_order_id=client_id,
      raw_payload_hash=f"hash-{role.lower()}-terminal",
      business_idempotency_key=f"business-{role.lower()}-terminal",
      payload={
        "client_order_id": client_id,
        "order": {
          "client_order_id": client_id,
          "account_id": "account-1",
          "order_id": broker_id,
          "stock_code": "600000.SH",
          "order_type": 23 if role == "ENTRY" else 24,
          "order_status": status,
          "order_volume": 100,
          "traded_volume": traded_volume,
          "traded_price": 10.0,
          "price": 10.0,
        },
      },
      received_at=utcnow(),
      processing_status="PROCESSING",
    )

  def trade_report(
    *,
    role: str,
    client_id: str,
    broker_id: int,
    execution_id: str,
    volume: int,
  ) -> AgentReportInbox:
    return AgentReportInbox(
      message_id=f"report-{execution_id}",
      device_id="device-1",
      message_type="execution_report",
      client_order_id=client_id,
      raw_payload_hash=f"hash-{execution_id}",
      business_idempotency_key=f"business-{execution_id}",
      payload={
        "client_order_id": client_id,
        "execution": {
          "client_order_id": client_id,
          "account_id": "account-1",
          "order_id": broker_id,
          "execution_id": execution_id,
          "stock_code": "600000.SH",
          "order_type": 23 if role == "ENTRY" else 24,
          "traded_volume": volume,
          "traded_price": 10.0,
        },
      },
      received_at=utcnow(),
      processing_status="PROCESSING",
    )

  await report_processor._stage_runtime_events(
    order_report(
      role="ENTRY",
      client_id="client-entry-gap",
      broker_id=111,
      status="FILLED",
      traded_volume=0,
    )
  )
  await report_processor._stage_runtime_events(
    order_report(
      role="EXIT",
      client_id="client-exit-gap",
      broker_id=222,
      status="CANCELLED",
      traded_volume=40,
    )
  )
  async with sessions() as db:
    entry_intent = await db.get(TradeIntentRecord, "intent-entry-gap")
    exit_intent = await db.get(TradeIntentRecord, "intent-exit-gap")
    entry_batch = await db.get(TTradeBatch, "batch-entry-gap")
    exit_batch = await db.get(TTradeBatch, "batch-exit-gap")
    assert entry_intent.status == "RECONCILE_REQUIRED"
    assert "expected=100, received=0" in entry_intent.notes
    assert exit_intent.status == "RECONCILE_REQUIRED"
    assert "expected=40, received=0" in exit_intent.notes
    assert entry_batch.status == "RECONCILE_REQUIRED"
    assert exit_batch.status == "RECONCILE_REQUIRED"

  await report_processor._stage_runtime_events(
    trade_report(
      role="ENTRY",
      client_id="client-entry-gap",
      broker_id=111,
      execution_id="entry-gap-1",
      volume=40,
    )
  )
  await report_processor._stage_runtime_events(
    trade_report(
      role="EXIT",
      client_id="client-exit-gap",
      broker_id=222,
      execution_id="exit-gap-1",
      volume=20,
    )
  )
  async with sessions() as db:
    assert (await db.get(TradeIntentRecord, "intent-entry-gap")).status == (
      "RECONCILE_REQUIRED"
    )
    assert (await db.get(TradeIntentRecord, "intent-exit-gap")).status == (
      "RECONCILE_REQUIRED"
    )
    assert (await db.get(TTradeBatch, "batch-entry-gap")).status == (
      "RECONCILE_REQUIRED"
    )
    assert (await db.get(TTradeBatch, "batch-exit-gap")).status == (
      "RECONCILE_REQUIRED"
    )

  await report_processor._stage_runtime_events(
    trade_report(
      role="ENTRY",
      client_id="client-entry-gap",
      broker_id=111,
      execution_id="entry-gap-2",
      volume=60,
    )
  )
  await report_processor._stage_runtime_events(
    trade_report(
      role="EXIT",
      client_id="client-exit-gap",
      broker_id=222,
      execution_id="exit-gap-2",
      volume=20,
    )
  )
  async with sessions() as db:
    entry_intent = await db.get(TradeIntentRecord, "intent-entry-gap")
    exit_intent = await db.get(TradeIntentRecord, "intent-exit-gap")
    entry_batch = await db.get(TTradeBatch, "batch-entry-gap")
    exit_batch = await db.get(TTradeBatch, "batch-exit-gap")
    assert entry_intent.status == "FILLED"
    assert entry_intent.executed_volume == 100
    assert exit_intent.status == "CANCELLED"
    assert exit_intent.executed_volume == 40
    assert entry_batch.status == "OPEN"
    assert entry_batch.entry_filled_volume == 100
    assert exit_batch.status == "EXIT_PARTIAL"
    assert exit_batch.exit_filled_volume == 40
  await engine.dispose()


@pytest.mark.asyncio
async def test_cancelled_partial_fill_replays_order_then_trade_into_real_strategy(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path,
) -> None:
  database_path = (tmp_path / "t-trade-runtime-events.sqlite3").as_posix()
  engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
  tables = [
    AuthUser.__table__,
    PendingTradeOrder.__table__,
    StrategyOrderCorrelation.__table__,
    StrategyRuntimeEvent.__table__,
    TTradeBatch.__table__,
    TradeIntentRecord.__table__,
    StrategyRunState.__table__,
    StrategyRunPosition.__table__,
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
  from quantx_infrastructure.database import connection as connection_module

  async def get_test_db():
    async with sessions() as db:
      yield db

  monkeypatch.setattr(connection_module, "get_async_db", get_test_db)

  context = StrategyContext(
    run_id="run-reconcile-partial",
    mode=StrategyRunMode.LIVE,
    instruments=["600000.SH"],
    parameters={"account_id": "account-1"},
  )
  strategy = AshareIntradayTAssistantStrategy(context)
  batch_id = "batch-reconcile-partial"
  intent_id = "intent-reconcile-partial"
  plan_id = f"t-exit-{batch_id}"
  exit_plan_template = strategy.build_exit_plan_template(
    instrument_code="600000.SH",
    batch_id=batch_id,
    plan_id=plan_id,
  ).to_dict()
  request_metadata = {
    "instrument_code": "600000.SH",
    "intent_id": intent_id,
    "t_batch_id": batch_id,
    "exit_plan_id": plan_id,
    "exit_plan_template": exit_plan_template,
    "requested_entry_volume": 200,
  }
  strategy.state.update(
    {
      "instrument_states": {
        "600000.SH": {
          "status": "RECONCILE_REQUIRED",
          "pending_entry_intent_id": intent_id,
          "entry_order_status": "RECONCILE_REQUIRED",
          "reconciliation_reason": "DURABLE_FILL_AWAITS_IDEMPOTENT_INBOX_REPLAY",
          "batch_id": batch_id,
          "exit_plan_id": plan_id,
          "entry_filled_volume": 0,
          "entry_avg_price": 0.0,
          "exit_filled_volume": 0,
        }
      }
    }
  )

  transitions: list[tuple[str, dict]] = []
  original_on_order = strategy.on_order
  original_on_trade = strategy.on_trade

  async def track_order(event):
    patch = await original_on_order(event)
    transitions.append(
      ("ORDER", dict(strategy.state["instrument_states"]["600000.SH"]))
    )
    return patch

  async def track_trade(event):
    patch = await original_on_trade(event)
    transitions.append(
      ("TRADE", dict(strategy.state["instrument_states"]["600000.SH"]))
    )
    return patch

  monkeypatch.setattr(strategy, "on_order", track_order)
  monkeypatch.setattr(strategy, "on_trade", track_trade)

  executor = StrategyExecutor()
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="reconcile-partial",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
    strategy=strategy,
    status=ExecutionStatus.RUNNING,
  )
  runtime.state_manager = RuntimeStateManager(
    run_id=context.run_id,
    persist_enabled=True,
    log_dir=str(tmp_path / "runtime-state"),
  )
  runtime.state_manager._state["version"] = 1
  executor.runs[runtime.run_id] = runtime
  runtime.event_task = asyncio.create_task(executor._process_event_queue(runtime))
  monkeypatch.setattr(strategy_manager, "executor", executor)

  async with sessions() as db:
    db.add(
      AuthUser(
        id="user-partial",
        username="runtime-partial",
        display_name="Runtime Partial",
        password_hash="unused",
        permissions=[],
      )
    )
    db.add(
      StrategyRunState(
        run_id=runtime.run_id,
        cash=0.0,
        frozen_cash=0.0,
        total_asset=0.0,
        custom_state={},
        version=1,
      )
    )
    db.add(
      TradeIntentRecord(
        id=intent_id,
        owner_type="STRATEGY_RUN",
        owner_id=runtime.run_id,
        account_id="account-1",
        instrument_code="600000.SH",
        direction="BUY",
        bucket="swing",
        reason="T_ENTRY",
        target_volume=200,
        status="PENDING",
      )
    )
    db.add(
      PendingTradeOrder(
        client_order_id="client-partial",
        user_id="user-partial",
        account_id="account-1",
        instrument_code="600000.SH",
        side="BUY",
        order_type="FIX_PRICE",
        limit_price=str(Decimal("10.00")),
        volume=200,
        status="QUEUED",
        execution_mode="live",
        strategy_run_id=runtime.run_id,
        strategy_order_id="strategy-order-partial",
        intent_id=intent_id,
        batch_id=batch_id,
        bucket="swing",
        t_trade_role="ENTRY",
        request_metadata=request_metadata,
      )
    )
    db.add(
      StrategyOrderCorrelation(
        id="correlation-partial",
        client_order_id="client-partial",
        account_id="account-1",
        strategy_run_id=runtime.run_id,
        strategy_order_id="strategy-order-partial",
        intent_id=intent_id,
        batch_id=batch_id,
        bucket="swing",
        t_trade_role="ENTRY",
        execution_mode="live",
        trace_id="trace-partial",
        request_metadata=request_metadata,
      )
    )
    db.add(
      TTradeBatch(
        batch_id=batch_id,
        account_id="account-1",
        instrument_code="600000.SH",
        strategy_run_id=runtime.run_id,
        target_volume=200,
        status="ENTRY_QUEUED",
      )
    )
    await db.commit()

  report = AgentReportInbox(
    message_id="report-cancelled-partial",
    device_id="device-1",
    message_type="delta_report",
    client_order_id="client-partial",
    raw_payload_hash="hash-cancelled-partial",
    business_idempotency_key="business-cancelled-partial",
    payload={
      "orders": [
        {
          "client_order_id": "client-partial",
          "account_id": "account-1",
          "order_id": 456,
          "stock_code": "600000.SH",
          "order_type": 23,
          "order_status": 54,
          "order_volume": 200,
          "traded_volume": 100,
          "traded_price": 10.0,
          "price": 10.0,
        }
      ],
      "trades": [
        {
          "client_order_id": "client-partial",
          "account_id": "account-1",
          "order_id": 456,
          "execution_id": "execution-partial-1",
          "stock_code": "600000.SH",
          "order_type": 23,
          "traded_volume": 100,
          "traded_price": 10.0,
          "traded_time": datetime(2026, 8, 14, 10, 0).isoformat(),
        }
      ],
    },
    received_at=utcnow(),
    processing_status="PROCESSING",
  )

  fresh_runtime = None
  processed_ticks = 0

  async def count_startup_tick(_runtime, _tick):
    nonlocal processed_ticks
    processed_ticks += 1

  monkeypatch.setattr(executor, "_process_tick", count_startup_tick)
  try:
    await report_processor._stage_runtime_events(report)
    await report_processor._stage_runtime_events(report)
    earliest_event_key = (
      await runtime.state_manager.get_earliest_unapplied_runtime_event_key()
    )
    assert runtime.durable_event_barrier_key == earliest_event_key
    assert runtime.durable_startup_barrier is True

    # A restarted executor reconstructs the same fail-closed barrier before
    # subscriptions can deliver the first ready tick.
    runtime.durable_event_barrier_key = None
    runtime.durable_startup_barrier = False
    runtime.durable_event_barrier_key = (
      await runtime.state_manager.get_earliest_unapplied_runtime_event_key()
    )
    runtime.durable_startup_barrier = bool(runtime.durable_event_barrier_key)
    assert runtime.durable_startup_barrier is True

    await runtime.event_queue.put(("tick", object()))
    await asyncio.wait_for(runtime.event_queue.join(), timeout=0.2)
    assert processed_ticks == 0

    # Simulate a steady-state consumer with no startup barrier. Drain must arm
    # the whole ORDER -> TRADE backlog and never expose the partial transition.
    runtime.durable_event_barrier_key = None
    runtime.durable_startup_barrier = False
    interposed_ticks: list[str] = []
    original_apply_runtime_event = report_processor._apply_runtime_event

    async def apply_with_interposed_ticks(runtime_event):
      if runtime_event.event_type == "TRADE":
        assert runtime.durable_event_barrier_key == runtime_event.business_key
        await runtime.event_queue.put(("tick", object()))
        await asyncio.wait_for(runtime.event_queue.join(), timeout=0.2)
        assert processed_ticks == 0
        interposed_ticks.append("before_trade")
      await original_apply_runtime_event(runtime_event)
      if runtime_event.event_type == "ORDER":
        assert runtime.durable_event_barrier_key == runtime_event.business_key
        await runtime.event_queue.put(("tick", object()))
        await asyncio.wait_for(runtime.event_queue.join(), timeout=0.2)
        assert processed_ticks == 0
        interposed_ticks.append("after_order_callback")

    monkeypatch.setattr(
      report_processor,
      "_apply_runtime_event",
      apply_with_interposed_ticks,
    )
    await report_processor._drain_runtime_events()
    monkeypatch.setattr(
      report_processor,
      "_apply_runtime_event",
      original_apply_runtime_event,
    )
    assert interposed_ticks == ["after_order_callback", "before_trade"]
    assert runtime.durable_event_barrier_key is None
    assert runtime.durable_startup_barrier is False

    await runtime.event_queue.put(("tick", object()))
    await asyncio.wait_for(runtime.event_queue.join(), timeout=0.2)
    assert processed_ticks == 1

    state = strategy.state["instrument_states"]["600000.SH"]
    assert [kind for kind, _state in transitions] == ["ORDER", "TRADE"]
    assert transitions[0][1]["status"] == "RECONCILE_REQUIRED"
    assert transitions[0][1]["pending_entry_intent_id"] == intent_id
    assert transitions[0][1]["entry_expected_fill_volume"] == 100
    assert transitions[0][1]["batch_id"] == batch_id
    assert state["status"] == "MONITORING"
    assert state["pending_entry_intent_id"] == ""
    assert state["entry_order_status"] == "CANCELLED"
    assert state["reconciliation_reason"] == ""
    assert state["batch_id"] == batch_id
    assert state["exit_plan_id"] == plan_id
    assert state["entry_filled_volume"] == 100
    assert state["entry_avg_price"] == pytest.approx(10.0)
    assert runtime.exit_plan_book.plans[plan_id].entry_filled_volume == 100

    async with sessions() as db:
      assert (
        await db.scalar(select(func.count()).select_from(StrategyRuntimeEvent))
        == 2
      )
      intent = await db.get(TradeIntentRecord, intent_id)
      assert intent.executed_volume == 100
      assert intent.executed_price == pytest.approx(10.0)
      assert intent.status == "CANCELLED"

      trade_event = await db.scalar(
        select(StrategyRuntimeEvent).where(
          StrategyRuntimeEvent.event_type == "TRADE"
        )
      )
      assert trade_event.application_status == "APPLIED"

    failed_report = AgentReportInbox(
      message_id="report-stage-commit-failure",
      device_id="device-1",
      message_type="order_report",
      client_order_id="client-partial",
      raw_payload_hash="hash-stage-commit-failure",
      business_idempotency_key="business-stage-commit-failure",
      payload={
        "client_order_id": "client-partial",
        "order": {
          "client_order_id": "client-partial",
          "account_id": "account-1",
          "order_id": 789,
          "stock_code": "600000.SH",
          "order_type": 23,
          "order_status": "SUBMITTED",
          "order_volume": 200,
          "traded_volume": 0,
          "price": 10.0,
        },
      },
      received_at=utcnow(),
      processing_status="PROCESSING",
    )

    original_insert_runtime_event = report_processor._insert_runtime_event
    original_refresh_barrier = executor.refresh_durable_event_barrier
    refresh_attempts = 0

    async def fail_staged_flush(*_args, **_kwargs):
      raise RuntimeError("stage flush failed")

    async def fail_first_barrier_refresh(run_id):
      nonlocal refresh_attempts
      refresh_attempts += 1
      if refresh_attempts == 1:
        raise RuntimeError("barrier database temporarily unavailable")
      return await original_refresh_barrier(run_id)

    monkeypatch.setattr(
      report_processor,
      "_insert_runtime_event",
      fail_staged_flush,
    )
    monkeypatch.setattr(
      executor,
      "refresh_durable_event_barrier",
      fail_first_barrier_refresh,
    )
    with pytest.raises(report_processor.RetryableReportError, match="stage flush failed"):
      await report_processor._stage_runtime_events(failed_report)
    monkeypatch.setattr(
      report_processor,
      "_insert_runtime_event",
      original_insert_runtime_event,
    )
    assert runtime.durable_event_barrier_key == (
      "order:client-partial:789:SUBMITTED:0"
    )
    await report_processor._refresh_runtime_event_barriers()
    assert refresh_attempts == 2
    assert runtime.durable_event_barrier_key is None
    assert runtime.durable_startup_barrier is False
    async with sessions() as db:
      assert (
        await db.scalar(select(func.count()).select_from(StrategyRuntimeEvent))
        == 2
      )

    async with sessions() as db:
      pending = await db.get(PendingTradeOrder, "client-partial")
      pending.status = "FILLED"
      pending.last_source_sequence = 20
      batch_before_late_order = await db.get(TTradeBatch, batch_id)
      intent_before_late_order = await db.get(TradeIntentRecord, intent_id)
      batch_status_before_late_order = batch_before_late_order.status
      intent_status_before_late_order = intent_before_late_order.status
      await db.commit()
    strategy_state_before_late_order = dict(
      strategy.state["instrument_states"]["600000.SH"]
    )
    late_submitted_report = AgentReportInbox(
      message_id="report-late-submitted",
      device_id="device-1",
      message_type="order_report",
      client_order_id="client-partial",
      raw_payload_hash="hash-late-submitted",
      business_idempotency_key="business-late-submitted",
      payload={
        "client_order_id": "client-partial",
        "source_sequence": 10,
        "order": {
          "client_order_id": "client-partial",
          "account_id": "account-1",
          "order_id": 456,
          "stock_code": "600000.SH",
          "order_type": 23,
          "order_status": "SUBMITTED",
          "order_volume": 200,
          "traded_volume": 0,
          "price": 10.0,
        },
      },
      received_at=utcnow(),
      processing_status="PROCESSING",
    )
    await report_processor._stage_runtime_events(late_submitted_report)
    await report_processor._drain_runtime_events()
    assert runtime.durable_event_barrier_key is None
    async with sessions() as db:
      assert (
        await db.scalar(select(func.count()).select_from(StrategyRuntimeEvent))
        == 2
      )
      assert (await db.get(TTradeBatch, batch_id)).status == (
        batch_status_before_late_order
      )
      assert (await db.get(TradeIntentRecord, intent_id)).status == (
        intent_status_before_late_order
      )
    assert strategy.state["instrument_states"]["600000.SH"] == (
      strategy_state_before_late_order
    )

    restored_manager = RuntimeStateManager(
      run_id=context.run_id,
      persist_enabled=True,
      log_dir=str(tmp_path / "restored-runtime-state"),
    )
    restored = (await restored_manager.restore()).state
    assert restored_manager.has_applied_runtime_event(trade_event.business_key)

    fresh_strategy = AshareIntradayTAssistantStrategy(context)
    strategy_snapshot = dict(restored.get("custom") or {})
    exit_plan_snapshot = strategy_snapshot.pop(EXIT_PLAN_BOOK_STATE_KEY, None)
    fresh_strategy.apply_state_snapshot(strategy_snapshot)
    replayed_trades = 0
    original_fresh_on_trade = fresh_strategy.on_trade

    async def count_fresh_trade(event):
      nonlocal replayed_trades
      replayed_trades += 1
      return await original_fresh_on_trade(event)

    monkeypatch.setattr(fresh_strategy, "on_trade", count_fresh_trade)
    fresh_executor = StrategyExecutor()
    fresh_runtime = StrategyRuntime(
      run_id=context.run_id,
      name="reconcile-partial-restored",
      strategy_id=1,
      strategy_class=AshareIntradayTAssistantStrategy,
      context=context,
      strategy=fresh_strategy,
      status=ExecutionStatus.RUNNING,
    )
    fresh_runtime.state_manager = restored_manager
    fresh_runtime.exit_plan_book = ExitPlanBook.from_dict(exit_plan_snapshot)
    fresh_executor.runs[fresh_runtime.run_id] = fresh_runtime
    fresh_runtime.event_task = asyncio.create_task(
      fresh_executor._process_event_queue(fresh_runtime)
    )
    monkeypatch.setattr(strategy_manager, "executor", fresh_executor)

    await report_processor._apply_runtime_event(trade_event)

    fresh_state = fresh_strategy.state["instrument_states"]["600000.SH"]
    assert replayed_trades == 0
    assert fresh_state["entry_filled_volume"] == 100
    assert fresh_runtime.exit_plan_book.plans[plan_id].entry_filled_volume == 100
    assert restored_manager.get_position("600000.SH")["long_volume"] == 100
  finally:
    for active_runtime in (runtime, fresh_runtime):
      if active_runtime is None:
        continue
      active_runtime.status = ExecutionStatus.STOPPED
      if active_runtime.event_task:
        active_runtime.event_task.cancel()
        await asyncio.gather(active_runtime.event_task, return_exceptions=True)
    await engine.dispose()


@pytest.mark.asyncio
async def test_same_timestamp_runtime_events_follow_event_id_barrier_order(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path,
) -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[
          StrategyRuntimeEvent.__table__,
          StrategyRunState.__table__,
          StrategyRunPosition.__table__,
        ],
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  monkeypatch.setattr(report_processor, "AsyncSessionLocal", sessions)
  from quantx_infrastructure.database import connection as connection_module

  async def get_test_db():
    async with sessions() as db:
      yield db

  monkeypatch.setattr(connection_module, "get_async_db", get_test_db)
  context = StrategyContext(
    run_id="run-tied-runtime-events",
    mode=StrategyRunMode.LIVE,
    instruments=["600000.SH"],
    parameters={"account_id": "account-1"},
  )
  strategy = AshareIntradayTAssistantStrategy(context)
  executor = StrategyExecutor()
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="tied-runtime-events",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
    strategy=strategy,
    status=ExecutionStatus.RUNNING,
  )
  runtime.state_manager = RuntimeStateManager(
    run_id=context.run_id,
    persist_enabled=True,
    log_dir=str(tmp_path / "tied-runtime-events"),
  )
  runtime.state_manager._state["version"] = 1
  executor.runs[runtime.run_id] = runtime
  applied_keys: list[str] = []

  async def track_order(event):
    applied_keys.append(str(event.metadata["runtime_event_key"]))
    return None

  monkeypatch.setattr(strategy, "on_order", track_order)
  monkeypatch.setattr(strategy_manager, "executor", executor)
  created_at = utcnow()

  def make_event(event_id: str, business_key: str) -> StrategyRuntimeEvent:
    return StrategyRuntimeEvent(
      event_id=event_id,
      business_key=business_key,
      strategy_run_id=context.run_id,
      client_order_id="client-tied",
      broker_order_id=event_id,
      event_type="ORDER",
      payload={
        "report": {
          "stock_code": "600000.SH",
          "order_type": 23,
          "order_status": "SUBMITTED",
          "order_volume": 100,
          "traded_volume": 0,
          "price": 10.0,
        },
        "metadata": {
          "instrument_code": "600000.SH",
          "strategy_order_id": f"strategy-{event_id}",
          "runtime_event_key": business_key,
        },
      },
      application_status="PENDING",
      application_attempts=0,
      created_at=created_at,
    )

  lexically_later = make_event("z-inserted-first", "order:tied:z")
  lexically_earlier = make_event("a-inserted-second", "order:tied:a")
  async with sessions() as db:
    db.add(
      StrategyRunState(
        run_id=context.run_id,
        cash=0.0,
        frozen_cash=0.0,
        total_asset=0.0,
        custom_state={},
        version=1,
      )
    )
    db.add_all([lexically_later, lexically_earlier])
    await db.commit()

  runtime.durable_event_barrier_key = (
    await runtime.state_manager.get_earliest_unapplied_runtime_event_key()
  )
  runtime.durable_startup_barrier = True
  assert runtime.durable_event_barrier_key == lexically_earlier.business_key
  runtime.event_task = asyncio.create_task(executor._process_event_queue(runtime))
  try:
    await report_processor._drain_runtime_events()
    await asyncio.wait_for(runtime.event_queue.join(), timeout=0.2)
    assert applied_keys == [
      lexically_earlier.business_key,
      lexically_later.business_key,
    ]
    assert runtime.durable_event_barrier_key is None
    async with sessions() as db:
      statuses = (
        await db.execute(
          select(StrategyRuntimeEvent.application_status).order_by(
            StrategyRuntimeEvent.event_id
          )
        )
      ).scalars().all()
      assert statuses == ["APPLIED", "APPLIED"]
  finally:
    runtime.status = ExecutionStatus.STOPPED
    runtime.event_task.cancel()
    await asyncio.gather(runtime.event_task, return_exceptions=True)
    await engine.dispose()


@pytest.mark.asyncio
async def test_durable_callback_failure_rolls_back_and_balances_queue(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  context, strategy, event, _batch_id, plan_id = _reconcile_trade_fixture(
    "callback-rollback"
  )
  executor = StrategyExecutor()
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="callback-rollback",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
    strategy=strategy,
    status=ExecutionStatus.RUNNING,
  )
  runtime.state_manager = RuntimeStateManager(
    run_id=context.run_id,
    persist_enabled=False,
  )
  runtime.state_manager.update_account(cash=10_000.0, total_asset=10_000.0)
  executor.runs[runtime.run_id] = runtime
  runtime.event_task = asyncio.create_task(executor._process_event_queue(runtime))
  monkeypatch.setattr(strategy_manager, "executor", executor)
  original_on_trade = strategy.on_trade
  processed_ticks = 0

  async def mutate_then_fail(_event):
    strategy.state.set("callback_poison", True)
    raise RuntimeError("durable callback exploded")

  async def count_tick(_runtime, _tick):
    nonlocal processed_ticks
    processed_ticks += 1

  monkeypatch.setattr(strategy, "on_trade", mutate_then_fail)
  monkeypatch.setattr(executor, "_process_tick", count_tick)

  try:
    with pytest.raises(RuntimeError, match="durable callback exploded"):
      await report_processor._apply_runtime_event(event)
    await asyncio.wait_for(runtime.event_queue.join(), timeout=0.2)

    assert strategy.state.get("callback_poison") is None
    assert runtime.state_manager.get_position("600000.SH") is None
    assert plan_id not in runtime.exit_plan_book.plans
    assert not runtime.state_manager.has_applied_runtime_event(event.business_key)
    assert runtime.durable_event_barrier_key == event.business_key

    await runtime.event_queue.put(("tick", object()))
    await asyncio.wait_for(runtime.event_queue.join(), timeout=0.2)
    assert processed_ticks == 0

    monkeypatch.setattr(strategy, "on_trade", original_on_trade)
    await report_processor._apply_runtime_event(event)
    await asyncio.wait_for(runtime.event_queue.join(), timeout=0.2)

    state = strategy.state["instrument_states"]["600000.SH"]
    assert state["entry_filled_volume"] == 100
    assert runtime.state_manager.get_position("600000.SH")["long_volume"] == 100
    assert runtime.exit_plan_book.plans[plan_id].entry_filled_volume == 100
    assert runtime.state_manager.has_applied_runtime_event(event.business_key)
    assert runtime.durable_event_barrier_key is None

    await runtime.event_queue.put(("tick", object()))
    await asyncio.wait_for(runtime.event_queue.join(), timeout=0.2)
    assert processed_ticks == 1
  finally:
    runtime.status = ExecutionStatus.STOPPED
    runtime.event_task.cancel()
    await asyncio.gather(runtime.event_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_durable_order_callback_failure_restores_entry_reservation(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  context, strategy, trade_event, _batch_id, _plan_id = _reconcile_trade_fixture(
    "order-reservation-rollback"
  )
  metadata = dict(trade_event.payload["metadata"])
  intent_id = str(metadata["intent_id"])
  order_event = StrategyRuntimeEvent(
    event_id="runtime-event-order-reservation-rollback",
    business_key="order:client-order-reservation-rollback:456:CANCELLED:0",
    strategy_run_id=context.run_id,
    client_order_id="client-order-reservation-rollback",
    broker_order_id="456",
    event_type="ORDER",
    payload={
      "report": {
        "stock_code": "600000.SH",
        "order_type": 23,
        "order_status": "CANCELLED",
        "order_volume": 200,
        "traded_volume": 0,
        "price": 10.0,
      },
      "metadata": metadata,
    },
    application_status="PENDING",
    application_attempts=0,
    created_at=utcnow(),
  )
  executor = StrategyExecutor()
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="order-reservation-rollback",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
    strategy=strategy,
    status=ExecutionStatus.RUNNING,
  )
  runtime.state_manager = RuntimeStateManager(
    run_id=context.run_id,
    persist_enabled=False,
  )
  original_reservation = {
    "instrument_code": "600000.SH",
    "batch_id": metadata["t_batch_id"],
    "requested_volume": 200,
    "volume": 200,
    "price": 10.0,
    "amount": 2_000.0,
  }
  runtime.t_trade_entry_reservations[intent_id] = dict(original_reservation)
  executor.runs[runtime.run_id] = runtime
  runtime.event_task = asyncio.create_task(executor._process_event_queue(runtime))
  monkeypatch.setattr(strategy_manager, "executor", executor)
  callback_saw_released_reservation = False

  async def fail_after_reservation_release(_event):
    nonlocal callback_saw_released_reservation
    callback_saw_released_reservation = (
      intent_id not in runtime.t_trade_entry_reservations
    )
    raise RuntimeError("order callback exploded")

  monkeypatch.setattr(strategy, "on_order", fail_after_reservation_release)
  try:
    with pytest.raises(RuntimeError, match="order callback exploded"):
      await report_processor._apply_runtime_event(order_event)
    await asyncio.wait_for(runtime.event_queue.join(), timeout=0.2)

    assert callback_saw_released_reservation is True
    assert runtime.t_trade_entry_reservations == {
      intent_id: original_reservation
    }
    assert not runtime.state_manager.has_applied_runtime_event(
      order_event.business_key
    )
    assert runtime.durable_event_barrier_key == order_event.business_key
  finally:
    runtime.status = ExecutionStatus.STOPPED
    runtime.event_task.cancel()
    await asyncio.gather(runtime.event_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_durable_checkpoint_failure_retries_without_reapplying_callback(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path,
) -> None:
  context, strategy, event, _batch_id, plan_id = _reconcile_trade_fixture(
    "checkpoint-retry"
  )
  executor = StrategyExecutor()
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="checkpoint-retry",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
    strategy=strategy,
    status=ExecutionStatus.RUNNING,
  )
  state_manager = RuntimeStateManager(
    run_id=context.run_id,
    persist_enabled=True,
    log_dir=str(tmp_path / "checkpoint-retry"),
  )
  state_manager.update_account(cash=10_000.0, total_asset=10_000.0)
  runtime.state_manager = state_manager
  executor.runs[runtime.run_id] = runtime
  runtime.event_task = asyncio.create_task(executor._process_event_queue(runtime))
  monkeypatch.setattr(strategy_manager, "executor", executor)

  callback_calls = 0
  save_calls = 0
  processed_ticks = 0
  original_on_trade = strategy.on_trade

  async def count_trade(event_data):
    nonlocal callback_calls
    callback_calls += 1
    return await original_on_trade(event_data)

  async def fail_then_save():
    nonlocal save_calls
    save_calls += 1
    if save_calls <= 2:
      return False
    state_manager._dirty = False
    return True

  async def count_tick(_runtime, _tick):
    nonlocal processed_ticks
    processed_ticks += 1

  async def marker_not_yet_committed(_event_key):
    return False

  async def skip_unrelated_session_checkpoint(_runtime):
    return None

  monkeypatch.setattr(strategy, "on_trade", count_trade)
  monkeypatch.setattr(state_manager, "save_snapshot", fail_then_save)
  monkeypatch.setattr(
    state_manager,
    "_adopt_committed_runtime_event",
    marker_not_yet_committed,
  )
  monkeypatch.setattr(executor, "_process_tick", count_tick)
  monkeypatch.setattr(
    executor,
    "_maybe_coordinate_session_checkpoints",
    skip_unrelated_session_checkpoint,
  )
  later_event = StrategyRuntimeEvent(
    event_id="runtime-event-after-barrier",
    business_key="order:client-after-barrier::SUBMITTED:0",
    strategy_run_id=context.run_id,
    client_order_id="client-after-barrier",
    broker_order_id=None,
    event_type="ORDER",
    payload={
      "report": {
        "stock_code": "600000.SH",
        "order_type": 23,
        "status": "SUBMITTED",
        "order_volume": 200,
        "price": 10.0,
      },
      "metadata": dict(event.payload["metadata"]),
    },
    application_status="PENDING",
    application_attempts=0,
    created_at=utcnow(),
  )

  try:
    with pytest.raises(RuntimeError, match="原子快照失败"):
      await report_processor._apply_runtime_event(event)
    await asyncio.wait_for(runtime.event_queue.join(), timeout=0.2)

    assert state_manager.has_applied_runtime_event(event.business_key)
    assert callback_calls == 1
    assert state_manager.get_position("600000.SH")["long_volume"] == 100
    assert runtime.durable_event_barrier_key == event.business_key

    await runtime.event_queue.put(("tick", object()))
    await asyncio.wait_for(runtime.event_queue.join(), timeout=0.2)
    assert processed_ticks == 0

    with pytest.raises(RuntimeError, match="屏障仍在等待"):
      await report_processor._apply_runtime_event(later_event)
    assert not state_manager.has_applied_runtime_event(later_event.business_key)

    with pytest.raises(RuntimeError, match="快照重试失败"):
      await report_processor._apply_runtime_event(event)
    await asyncio.wait_for(runtime.event_queue.join(), timeout=0.2)
    assert runtime.durable_event_barrier_key == event.business_key
    assert callback_calls == 1

    await runtime.event_queue.put(("tick", object()))
    await asyncio.wait_for(runtime.event_queue.join(), timeout=0.2)
    assert processed_ticks == 0

    await report_processor._apply_runtime_event(event)
    await asyncio.wait_for(runtime.event_queue.join(), timeout=0.2)

    assert runtime.durable_event_barrier_key is None
    await report_processor._apply_runtime_event(later_event)
    assert state_manager.has_applied_runtime_event(later_event.business_key)
    await runtime.event_queue.put(("tick", object()))
    await asyncio.wait_for(runtime.event_queue.join(), timeout=0.2)

    state = strategy.state["instrument_states"]["600000.SH"]
    assert save_calls == 4
    assert callback_calls == 1
    assert processed_ticks == 1
    assert state["entry_filled_volume"] == 100
    assert state_manager.get_position("600000.SH")["long_volume"] == 100
    assert runtime.exit_plan_book.plans[plan_id].entry_filled_volume == 100
  finally:
    runtime.status = ExecutionStatus.STOPPED
    runtime.event_task.cancel()
    await asyncio.gather(runtime.event_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_runtime_event_checkpoint_failure_returns_event_to_pending(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path,
) -> None:
  context, strategy, event, batch_id, _plan_id = _reconcile_trade_fixture(
    "drain-retry"
  )
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[
          StrategyOrderCorrelation.__table__,
          StrategyRuntimeEvent.__table__,
          TTradeBatch.__table__,
        ],
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  monkeypatch.setattr(report_processor, "AsyncSessionLocal", sessions)
  from quantx_infrastructure.database import connection as connection_module

  async def get_test_db():
    async with sessions() as db:
      yield db

  monkeypatch.setattr(connection_module, "get_async_db", get_test_db)

  executor = StrategyExecutor()
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="drain-retry",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
    strategy=strategy,
    status=ExecutionStatus.RUNNING,
  )
  state_manager = RuntimeStateManager(
    run_id=context.run_id,
    persist_enabled=True,
    log_dir=str(tmp_path / "drain-retry"),
  )
  runtime.state_manager = state_manager
  executor.runs[runtime.run_id] = runtime
  runtime.event_task = asyncio.create_task(executor._process_event_queue(runtime))
  monkeypatch.setattr(strategy_manager, "executor", executor)
  save_calls = 0

  async def fail_save():
    nonlocal save_calls
    save_calls += 1
    if save_calls == 1:
      return False
    state_manager._dirty = False
    return True

  async def marker_not_committed(_event_key):
    return False

  monkeypatch.setattr(state_manager, "save_snapshot", fail_save)
  monkeypatch.setattr(
    state_manager,
    "_adopt_committed_runtime_event",
    marker_not_committed,
  )

  async with sessions() as db:
    db.add(event)
    db.add(
      TTradeBatch(
        batch_id=batch_id,
        account_id="account-1",
        instrument_code="600000.SH",
        strategy_run_id=context.run_id,
        target_volume=200,
        entry_filled_volume=100,
        entry_avg_price=10.0,
        status="ENTRY_PARTIAL",
      )
    )
    await db.commit()

  try:
    with pytest.raises(report_processor.RetryableReportError):
      await report_processor._drain_runtime_events()
    await asyncio.wait_for(runtime.event_queue.join(), timeout=0.2)

    async with sessions() as db:
      stored_event = await db.get(StrategyRuntimeEvent, event.event_id)
      stored_batch = await db.get(TTradeBatch, batch_id)
      assert stored_event.application_status == "PENDING"
      assert "快照失败" in stored_event.application_error
      assert stored_batch.status == "RECONCILE_REQUIRED"
    assert runtime.durable_event_barrier_key == event.business_key

    await report_processor._drain_runtime_events()
    await asyncio.wait_for(runtime.event_queue.join(), timeout=0.2)

    async with sessions() as db:
      stored_event = await db.get(StrategyRuntimeEvent, event.event_id)
      stored_batch = await db.get(TTradeBatch, batch_id)
      assert stored_event.application_status == "APPLIED"
      assert stored_event.application_error is None
      assert stored_batch.status == "ENTRY_PARTIAL"
      assert stored_batch.exception_reason is None
      assert stored_batch.entry_filled_volume == 100
    strategy_state = strategy.state["instrument_states"]["600000.SH"]
    assert strategy_state["status"] == "MONITORING"
    assert strategy_state["entry_filled_volume"] == 100
    assert runtime.durable_event_barrier_key is None
  finally:
    runtime.status = ExecutionStatus.STOPPED
    runtime.event_task.cancel()
    await asyncio.gather(runtime.event_task, return_exceptions=True)
    await engine.dispose()
