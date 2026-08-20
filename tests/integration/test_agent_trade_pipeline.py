from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from quantx_api import agent_api
from quantx_contracts import AgentEnvelope, AgentMessageType
from quantx_domain.clock import utcnow
from quantx_domain.strategies.ashare_intraday_t_assistant import (
  AshareIntradayTAssistantStrategy,
)
from quantx_domain.strategies.base import StrategyContext, StrategyRunMode
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
  AgentDevice,
  AgentReportInbox,
  PendingTradeOrder,
  RuntimeComponentHeartbeat,
  StrategyOrderCorrelation,
  StrategyRuntimeEvent,
  TradeCommandOutbox,
  TTradeBatch,
)
from quantx_infrastructure.models.auth import AuthUser
from quantx_infrastructure.models.order import Order
from quantx_infrastructure.models.trade import Trade
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services import (
  auto_exit_plan_service,
  order_service,
  trade_service,
)
from quantx_infrastructure.services.trade_command_service import (
  TradeCommandService,
)
from quantx_qmt_agent.broker import SimulatorBroker
from quantx_qmt_agent.credentials import DeviceConfiguration
from quantx_qmt_agent.journal import LocalJournal
from quantx_qmt_agent.runtime import AgentRuntime
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
  AsyncSession,
  async_sessionmaker,
  create_async_engine,
)

TABLES = [
  AuthUser.__table__,
  AgentDevice.__table__,
  PendingTradeOrder.__table__,
  StrategyOrderCorrelation.__table__,
  TradeCommandOutbox.__table__,
  StrategyRuntimeEvent.__table__,
  TTradeBatch.__table__,
  TradeIntentRecord.__table__,
  AgentReportInbox.__table__,
  RuntimeComponentHeartbeat.__table__,
  Order.__table__,
  Trade.__table__,
]


class CapturingSocket:
  def __init__(self) -> None:
    self.sent: list[str] = []

  async def send(self, value: str) -> None:
    self.sent.append(value)


async def _database(
  monkeypatch: pytest.MonkeyPatch,
  database_path: Path | None = None,
) -> tuple[async_sessionmaker[AsyncSession], object]:
  database_url = (
    f"sqlite+aiosqlite:///{database_path.as_posix()}"
    if database_path is not None
    else "sqlite+aiosqlite:///:memory:"
  )
  engine = create_async_engine(
    database_url,
    connect_args={"timeout": 10},
  )
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=TABLES,
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)

  async def get_test_db():
    async with session_factory() as db:
      yield db

  monkeypatch.setattr(agent_api, "AsyncSessionLocal", session_factory)
  monkeypatch.setattr(report_processor, "AsyncSessionLocal", session_factory)

  async def ignore_runtime_wakeup() -> None:
    return None

  monkeypatch.setattr(
    agent_api,
    "_wake_runtime_event_consumer",
    ignore_runtime_wakeup,
  )
  monkeypatch.setattr(
    auto_exit_plan_service,
    "AsyncSessionLocal",
    session_factory,
  )
  monkeypatch.setattr(order_service, "get_async_db", get_test_db)
  monkeypatch.setattr(trade_service, "get_async_db", get_test_db)
  async with session_factory() as db:
    db.add(
      AuthUser(
        id="user-1",
        username="integration",
        display_name="Integration",
        password_hash="unused",
        permissions=[],
      )
    )
    db.add(
      AgentDevice(
        id="device-1",
        user_id="user-1",
        name="paper-agent",
        secret_hash="x" * 64,
        authorized_account_ids=["account-1"],
        capabilities=["paper"],
      )
    )
    db.add(
      RuntimeComponentHeartbeat(
        component="qmt-agent:device-1",
        instance_id="device-1",
        status="READY",
        details={},
        updated_at=utcnow(),
      )
    )
    await db.commit()
  return session_factory, engine


async def _enqueue_order(
  session_factory: async_sessionmaker[AsyncSession],
):
  async with session_factory() as db:
    return await TradeCommandService(db).enqueue_order(
      user_id="user-1",
      account_id="account-1",
      instrument_code="600000.SH",
      side="BUY",
      order_type="FIX_PRICE",
      limit_price=Decimal("10.50"),
      volume=100,
      idempotency_key="pipeline-request-1",
    )


async def _enqueue_strategy_order(
  session_factory: async_sessionmaker[AsyncSession],
  *,
  idempotency_key: str,
  managed_entry: bool = False,
):
  intent_id = f"intent-{idempotency_key}"
  batch_id = None if managed_entry else f"batch-{idempotency_key}"
  strategy_run_id = "run-1"
  request_metadata = (
    {
      "instrument_code": "600000.SH",
      "entry_plan_id": strategy_run_id,
      "execution_mode": "AUTO",
    }
    if managed_entry
    else {"instrument_code": "600000.SH"}
  )
  async with session_factory() as db:
    db.add(
      TradeIntentRecord(
        id=intent_id,
        strategy_run_id=strategy_run_id if managed_entry else None,
        owner_type="STRATEGY_RUN",
        owner_id=strategy_run_id,
        account_id="account-1",
        strategy_id=("ashare_managed_entry_plan" if managed_entry else "t-trade"),
        instrument_code="600000.SH",
        direction="BUY",
        bucket="core" if managed_entry else "swing",
        reason=(
          "MANAGED_ENTRY"
          if managed_entry
          else "T_TRADE_PULLBACK_REBOUND_ENTRY"
        ),
        status="PENDING",
        target_volume=100,
        limit_price_hint=10.5,
        executed_volume=0,
        intent_metadata=(
          {
            "entry_plan_id": strategy_run_id,
            "execution_mode": "AUTO",
          }
          if managed_entry
          else {"t_trade_role": "entry"}
        ),
      )
    )
    await db.flush()
    queued = await TradeCommandService(db).enqueue_order(
      user_id="user-1",
      account_id="account-1",
      instrument_code="600000.SH",
      side="BUY",
      order_type="FIX_PRICE",
      limit_price=Decimal("10.50"),
      volume=100,
      strategy_name=("ashare_managed_entry_plan" if managed_entry else "t-trade"),
      trace_id=f"trace-{idempotency_key}",
      idempotency_key=idempotency_key,
      execution_mode="paper",
      strategy_run_id=strategy_run_id,
      strategy_order_id=f"strategy-order-{idempotency_key}",
      intent_id=intent_id,
      batch_id=batch_id or "",
      bucket="core" if managed_entry else "swing",
      t_trade_role="" if managed_entry else "ENTRY",
      request_metadata=request_metadata,
    )
  return queued, intent_id, batch_id


def _runtime(
  journal_path: Path,
  broker,
  *,
  allowed_accounts: set[str] | None = None,
) -> AgentRuntime:
  return AgentRuntime(
    configuration=DeviceConfiguration(
      api_url="http://127.0.0.1:8080",
      device_id="device-1",
    ),
    device_secret="unused",
    mode="paper",
    allowed_accounts=(
      {"account-1"} if allowed_accounts is None else allowed_accounts
    ),
    broker=broker,
    journal=LocalJournal(journal_path),
  )


@pytest.mark.asyncio
async def test_fake_broker_pipeline_is_durable_idempotent_and_recovers_ordering(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path: Path,
) -> None:
  session_factory, engine = await _database(monkeypatch)
  queued = await _enqueue_order(session_factory)

  command = await agent_api._next_command("device-1")
  assert command is not None
  assert command.message_id == queued.message_id
  assert command.sent_at.tzinfo is not None

  socket = CapturingSocket()
  runtime = _runtime(
    tmp_path / "journal.sqlite3",
    SimulatorBroker({"account-1"}, data_only=False),
  )
  await runtime._handle_command(socket, command)
  outbound = [
    AgentEnvelope.model_validate_json(serialized) for serialized in socket.sent
  ]
  command_ack = next(
    item
    for item in outbound
    if item.message_type is AgentMessageType.COMMAND_ACK
  )
  reports = [
    item
    for item in outbound
    if item.message_type
    in {
      AgentMessageType.ORDER_REPORT,
      AgentMessageType.EXECUTION_REPORT,
    }
  ]
  assert [item.message_type for item in reports] == [
    AgentMessageType.ORDER_REPORT,
    AgentMessageType.EXECUTION_REPORT,
  ]

  await agent_api._record_command_ack("device-1", command_ack.payload)
  acknowledgements = [
    await agent_api._record_report("device-1", report) for report in reports
  ]
  assert all(item.accepted and not item.duplicate for item in acknowledgements)

  execution_report = reports[1]
  duplicate = await agent_api._record_report(
    "device-1",
    execution_report.model_copy(
      update={"message_id": "00000000-0000-4000-8000-000000000099"}
    ),
  )
  assert duplicate.accepted and duplicate.duplicate

  async with session_factory() as db:
    outbox = await db.get(TradeCommandOutbox, queued.message_id)
    pending = await db.get(PendingTradeOrder, queued.client_order_id)
    inbox_count = await db.scalar(
      select(func.count()).select_from(AgentReportInbox)
    )
    assert outbox is not None and outbox.delivery_status == "ACKNOWLEDGED"
    assert pending is not None and pending.status == "QUEUED"
    assert inbox_count == 2
    stored = (
      await db.execute(
        select(AgentReportInbox).order_by(AgentReportInbox.received_at)
      )
    ).scalars().all()
  order_inbox = next(item for item in stored if item.message_type == "order_report")
  execution_inbox = next(
    item for item in stored if item.message_type == "execution_report"
  )

  execution_inbox.processing_attempts = 1
  with pytest.raises(report_processor.RetryableReportError):
    await report_processor._process(execution_inbox)
  await report_processor._finish(
    execution_inbox.message_id,
    error=report_processor.RetryableReportError(
      "对应 order_report 尚未收敛"
    ),
  )
  async with session_factory() as db:
    retry = await db.get(AgentReportInbox, execution_inbox.message_id)
    assert retry is not None
    assert retry.processing_status == "PENDING"
    assert retry.next_attempt_at is not None

  await report_processor._process(order_inbox)
  await report_processor._finish(order_inbox.message_id)
  await report_processor._process(execution_inbox)
  await report_processor._finish(execution_inbox.message_id)

  async with session_factory() as db:
    persisted_order = (
      await db.execute(select(Order).where(Order.account_id == "account-1"))
    ).scalar_one()
    persisted_trade = (
      await db.execute(select(Trade).where(Trade.account_id == "account-1"))
    ).scalar_one()
    pending = await db.get(PendingTradeOrder, queued.client_order_id)
    processed = (
      await db.execute(select(AgentReportInbox.processing_status))
    ).scalars().all()
    assert persisted_trade.order_id == persisted_order.id
    assert persisted_trade.volume == 100
    assert pending is not None and pending.status == "FILLED"
    assert processed == ["PROCESSED", "PROCESSED"]

  await engine.dispose()


@pytest.mark.asyncio
async def test_partial_fill_converges_before_final_fill(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path: Path,
) -> None:
  class PartialFillBroker:
    def execute(self, payload):
      simulated = SimulatorBroker(
        {"account-1"},
        data_only=False,
      ).execute(payload)
      first_order = deepcopy(simulated["reports"][0][1])
      first_execution = deepcopy(simulated["reports"][1][1])
      final_order = deepcopy(first_order)
      final_execution = deepcopy(first_execution)

      first_order["order"]["order_status"] = 55
      first_order["order"]["traded_volume"] = 40
      first_order["order"]["traded_price"] = 10.5
      first_execution["order_status"] = "PARTIAL_FILLED"
      first_execution["execution"]["execution_id"] += "-1"
      first_execution["execution"]["traded_volume"] = 40
      first_execution["execution"]["traded_amount"] = 420

      final_order["order"]["order_status"] = 56
      final_order["order"]["traded_volume"] = 100
      final_order["order"]["traded_price"] = 10.5
      final_execution["order_status"] = "FILLED"
      final_execution["execution"]["execution_id"] += "-2"
      final_execution["execution"]["traded_volume"] = 60
      final_execution["execution"]["traded_amount"] = 630
      return {
        "accepted": True,
        "reason": "",
        "reports": [
          ("order_report", first_order),
          ("execution_report", first_execution),
          ("order_report", final_order),
          ("execution_report", final_execution),
        ],
      }

  session_factory, engine = await _database(monkeypatch)
  queued = await _enqueue_order(session_factory)
  command = await agent_api._next_command("device-1")
  assert command is not None
  socket = CapturingSocket()
  await _runtime(
    tmp_path / "partial.sqlite3",
    PartialFillBroker(),
  )._handle_command(socket, command)
  outbound = [
    AgentEnvelope.model_validate_json(serialized) for serialized in socket.sent
  ]
  ack = next(
    item
    for item in outbound
    if item.message_type is AgentMessageType.COMMAND_ACK
  )
  reports = [
    item
    for item in outbound
    if item.message_type
    in {
      AgentMessageType.ORDER_REPORT,
      AgentMessageType.EXECUTION_REPORT,
    }
  ]
  await agent_api._record_command_ack("device-1", ack.payload)
  for report in reports:
    assert (await agent_api._record_report("device-1", report)).accepted

  for report in reports[:2]:
    async with session_factory() as db:
      inbox = await db.get(AgentReportInbox, report.message_id)
    assert inbox is not None
    await report_processor._process(inbox)
    await report_processor._finish(report.message_id)
  async with session_factory() as db:
    pending = await db.get(PendingTradeOrder, queued.client_order_id)
    trade_count = await db.scalar(select(func.count()).select_from(Trade))
    assert pending is not None and pending.status == "PARTIAL_FILLED"
    assert trade_count == 1

  for report in reports[2:]:
    async with session_factory() as db:
      inbox = await db.get(AgentReportInbox, report.message_id)
    assert inbox is not None
    await report_processor._process(inbox)
    await report_processor._finish(report.message_id)
  async with session_factory() as db:
    pending = await db.get(PendingTradeOrder, queued.client_order_id)
    persisted_order = (await db.execute(select(Order))).scalar_one()
    trades = (await db.execute(select(Trade))).scalars().all()
    assert pending is not None and pending.status == "FILLED"
    assert persisted_order.traded_volume == 100
    assert sum(item.volume for item in trades) == 100
  await engine.dispose()


@pytest.mark.asyncio
async def test_pre_execution_rejection_closes_pending_order_and_cancel_ack_is_delivery_only(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path: Path,
) -> None:
  class BrokerSpy:
    def __init__(self) -> None:
      self.execute_calls = 0

    def execute(self, payload):
      del payload
      self.execute_calls += 1
      raise AssertionError("account mismatch must not reach broker")

  session_factory, engine = await _database(monkeypatch)
  queued = await _enqueue_order(session_factory)
  command = await agent_api._next_command("device-1")
  assert command is not None
  broker = BrokerSpy()
  rejected_socket = CapturingSocket()
  await _runtime(
    tmp_path / "rejected.sqlite3",
    broker,
    allowed_accounts=set(),
  )._handle_command(rejected_socket, command)
  rejected_messages = [
    AgentEnvelope.model_validate_json(item) for item in rejected_socket.sent
  ]
  rejected_ack = next(
    item
    for item in rejected_messages
    if item.message_type is AgentMessageType.COMMAND_ACK
  )
  assert rejected_ack.payload["reason"] == "account_not_whitelisted"
  await agent_api._record_command_ack("device-1", rejected_ack.payload)

  async with session_factory() as db:
    order_outbox = await db.get(TradeCommandOutbox, queued.message_id)
    pending = await db.get(PendingTradeOrder, queued.client_order_id)
    cancel = await TradeCommandService(db).enqueue_cancel(
      user_id="user-1",
      account_id="account-1",
      broker_order_id="broker-order-1",
      idempotency_key="cancel-request-1",
    )
    assert order_outbox is not None
    assert order_outbox.delivery_status == "REJECTED"
    assert pending is not None and pending.status == "REJECTED"
    assert pending.status_reason == "account_not_whitelisted"
  assert broker.execute_calls == 0

  cancel_command = await agent_api._next_command("device-1")
  assert cancel_command is not None
  assert cancel_command.message_type is AgentMessageType.CANCEL_COMMAND
  cancel_socket = CapturingSocket()
  await _runtime(
    tmp_path / "cancel.sqlite3",
    SimulatorBroker({"account-1"}, data_only=False),
  )._handle_command(cancel_socket, cancel_command)
  cancel_messages = [
    AgentEnvelope.model_validate_json(item) for item in cancel_socket.sent
  ]
  cancel_ack = next(
    item
    for item in cancel_messages
    if item.message_type is AgentMessageType.COMMAND_ACK
  )
  await agent_api._record_command_ack("device-1", cancel_ack.payload)
  assert not any(
    item.message_type in {
      AgentMessageType.ORDER_REPORT,
      AgentMessageType.EXECUTION_REPORT,
    }
    for item in cancel_messages
  )
  async with session_factory() as db:
    cancel_outbox = await db.get(TradeCommandOutbox, cancel.message_id)
    pending = await db.get(PendingTradeOrder, queued.client_order_id)
    assert cancel_outbox is not None
    assert cancel_outbox.delivery_status == "ACKNOWLEDGED"
    assert pending is not None and pending.status == "REJECTED"
  await engine.dispose()


@pytest.mark.asyncio
async def test_expired_command_is_closed_without_delivery(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  session_factory, engine = await _database(monkeypatch)
  queued = await _enqueue_order(session_factory)
  async with session_factory() as db:
    outbox = await db.get(TradeCommandOutbox, queued.message_id)
    assert outbox is not None
    outbox.expires_at = utcnow() - timedelta(seconds=1)
    await db.commit()

  assert await agent_api._next_command("device-1") is None
  async with session_factory() as db:
    outbox = await db.get(TradeCommandOutbox, queued.message_id)
    pending = await db.get(PendingTradeOrder, queued.client_order_id)
    assert outbox is not None and outbox.delivery_status == "EXPIRED"
    assert outbox.last_error == "command_expired_before_delivery"
    assert pending is not None and pending.status == "EXPIRED"
    assert pending.status_reason == "command_expired_before_delivery"
  await engine.dispose()


@pytest.mark.asyncio
async def test_expiry_sweeper_closes_disconnected_command_and_restart_is_idempotent(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  session_factory, engine = await _database(monkeypatch)
  queued, intent_id, batch_id = await _enqueue_strategy_order(
    session_factory,
    idempotency_key="disconnected-expiry-sweep",
  )
  async with session_factory() as db:
    outbox = await db.get(TradeCommandOutbox, queued.message_id)
    assert outbox is not None
    outbox.expires_at = utcnow() - timedelta(seconds=1)
    await db.commit()

  # No Agent command pull occurs: the API-owned startup/periodic sweep is the
  # only actor converging this never-delivered command.
  assert await agent_api.sweep_expired_trade_commands() == 1
  assert await agent_api.sweep_expired_trade_commands() == 0

  async with session_factory() as db:
    outbox = await db.get(TradeCommandOutbox, queued.message_id)
    pending = await db.get(PendingTradeOrder, queued.client_order_id)
    intent = await db.get(TradeIntentRecord, intent_id)
    batch = await db.get(TTradeBatch, batch_id)
    events = (await db.execute(select(StrategyRuntimeEvent))).scalars().all()
    assert outbox is not None and outbox.delivery_status == "EXPIRED"
    assert pending is not None and pending.status == "EXPIRED"
    assert intent is not None and intent.status == "EXPIRED"
    assert batch is not None and batch.status == "ENTRY_EXPIRED"
    assert [event.business_key for event in events] == [
      f"order:{queued.client_order_id}::EXPIRED:0"
    ]
  await engine.dispose()


@pytest.mark.asyncio
async def test_managed_entry_queued_expiry_proves_zero_fill(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  session_factory, engine = await _database(monkeypatch)
  queued, intent_id, batch_id = await _enqueue_strategy_order(
    session_factory,
    idempotency_key="managed-entry-queued-expiry",
    managed_entry=True,
  )
  assert batch_id is None
  async with session_factory() as db:
    outbox = await db.get(TradeCommandOutbox, queued.message_id)
    assert outbox is not None
    outbox.expires_at = utcnow() - timedelta(seconds=1)
    await db.commit()

  assert await agent_api.sweep_expired_trade_commands() == 1
  assert await agent_api.sweep_expired_trade_commands() == 0

  async with session_factory() as db:
    outbox = await db.get(TradeCommandOutbox, queued.message_id)
    pending = await db.get(PendingTradeOrder, queued.client_order_id)
    intent = await db.get(TradeIntentRecord, intent_id)
    [event] = list(
      (await db.execute(select(StrategyRuntimeEvent))).scalars().all()
    )
    assert outbox is not None and outbox.delivery_status == "EXPIRED"
    assert outbox.last_error == "command_expired_before_delivery"
    assert pending is not None and pending.status == "EXPIRED"
    assert intent is not None and intent.status == "RECONCILED_ZERO_FILL"
    assert intent.intent_metadata["execution_terminal_source"] == (
      "AGENT_COMMAND_LIFECYCLE"
    )
    assert event.payload["report"]["status"] == "RECONCILED_ZERO_FILL"
    assert event.payload["metadata"]["command_lifecycle_status"] == "EXPIRED"
  await engine.dispose()


@pytest.mark.asyncio
async def test_managed_entry_agent_expiry_ack_and_error_report_replay_one_zero_fill(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  session_factory, engine = await _database(monkeypatch)
  queued, intent_id, _ = await _enqueue_strategy_order(
    session_factory,
    idempotency_key="managed-entry-agent-expiry",
    managed_entry=True,
  )
  assert await agent_api._next_command("device-1") is not None
  ack = {
    "command_message_id": queued.message_id,
    "client_order_id": queued.client_order_id,
    "accepted": False,
    "reason": "command_expired",
  }
  await agent_api._record_command_ack("device-1", ack)
  await agent_api._record_command_ack("device-1", ack)

  error_report = AgentEnvelope(
    message_type=AgentMessageType.DELTA_REPORT,
    payload={
      "order_errors": [
        {
          "client_order_id": queued.client_order_id,
          "account_id": "account-1",
          "reason": "command_expired",
          "error_msg": "command_expired",
        }
      ],
      "sequence": 20,
      "is_complete": False,
    },
  )
  assert (await agent_api._record_report("device-1", error_report)).accepted
  async with session_factory() as db:
    inbox = await db.get(AgentReportInbox, error_report.message_id)
  assert inbox is not None
  await report_processor._process(inbox)
  await report_processor._stage_runtime_events(inbox)
  await report_processor._stage_runtime_events(inbox)

  async with session_factory() as db:
    outbox = await db.get(TradeCommandOutbox, queued.message_id)
    pending = await db.get(PendingTradeOrder, queued.client_order_id)
    intent = await db.get(TradeIntentRecord, intent_id)
    events = list(
      (await db.execute(select(StrategyRuntimeEvent))).scalars().all()
    )
    assert outbox is not None and outbox.delivery_status == "EXPIRED"
    assert outbox.last_error == "command_expired"
    assert pending is not None and pending.status == "EXPIRED"
    assert intent is not None and intent.status == "RECONCILED_ZERO_FILL"
    assert len(events) == 1
    assert events[0].business_key == (
      f"order:{queued.client_order_id}::RECONCILED_ZERO_FILL:0"
    )
    assert (
      events[0].payload["report"]["status"] == "RECONCILED_ZERO_FILL"
    )
  await engine.dispose()


@pytest.mark.asyncio
async def test_managed_entry_reconnect_expiry_closes_prior_reconcile_gate(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  session_factory, engine = await _database(monkeypatch)
  queued, intent_id, _ = await _enqueue_strategy_order(
    session_factory,
    idempotency_key="managed-entry-reconnect-expiry",
    managed_entry=True,
  )
  assert await agent_api._next_command("device-1") is not None
  async with session_factory() as db:
    outbox = await db.get(TradeCommandOutbox, queued.message_id)
    assert outbox is not None and outbox.delivery_status == "DELIVERED"
    outbox.expires_at = utcnow() - timedelta(seconds=1)
    await db.commit()

  assert await agent_api.sweep_expired_trade_commands() == 1
  async with session_factory() as db:
    outbox = await db.get(TradeCommandOutbox, queued.message_id)
    pending = await db.get(PendingTradeOrder, queued.client_order_id)
    intent = await db.get(TradeIntentRecord, intent_id)
    assert outbox is not None and outbox.delivery_status == "RECONCILE_REQUIRED"
    assert pending is not None and pending.status == "RECONCILE_REQUIRED"
    assert intent is not None and intent.status == "RECONCILE_REQUIRED"

  ack = {
    "command_message_id": queued.message_id,
    "client_order_id": queued.client_order_id,
    "accepted": False,
    "reason": "command_expired",
  }
  await agent_api._record_command_ack("device-1", ack)
  error_report = AgentEnvelope(
    message_type=AgentMessageType.DELTA_REPORT,
    payload={
      "order_errors": [
        {
          "client_order_id": queued.client_order_id,
          "account_id": "account-1",
          "reason": "command_expired",
          "error_msg": "command_expired",
        }
      ],
      "sequence": 30,
      "is_complete": False,
    },
  )
  assert (await agent_api._record_report("device-1", error_report)).accepted
  async with session_factory() as db:
    inbox = await db.get(AgentReportInbox, error_report.message_id)
  assert inbox is not None
  await report_processor._process(inbox)
  await report_processor._stage_runtime_events(inbox)

  async with session_factory() as db:
    outbox = await db.get(TradeCommandOutbox, queued.message_id)
    pending = await db.get(PendingTradeOrder, queued.client_order_id)
    intent = await db.get(TradeIntentRecord, intent_id)
    events = list(
      (
        await db.execute(
          select(StrategyRuntimeEvent).order_by(
            StrategyRuntimeEvent.created_at,
            StrategyRuntimeEvent.event_id,
          )
        )
      )
      .scalars()
      .all()
    )
    assert outbox is not None and outbox.delivery_status == "EXPIRED"
    assert pending is not None and pending.status == "EXPIRED"
    assert intent is not None and intent.status == "RECONCILED_ZERO_FILL"
    assert [
      event.payload["report"]["status"] for event in events
    ] == ["RECONCILE_REQUIRED", "RECONCILED_ZERO_FILL"]
  await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_expiry_sweepers_stage_one_runtime_event(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path: Path,
) -> None:
  session_factory, engine = await _database(
    monkeypatch,
    tmp_path / "concurrent-expiry.db",
  )
  queued, intent_id, _batch_id = await _enqueue_strategy_order(
    session_factory,
    idempotency_key="concurrent-expiry-sweep",
  )
  async with session_factory() as db:
    outbox = await db.get(TradeCommandOutbox, queued.message_id)
    assert outbox is not None
    outbox.expires_at = utcnow() - timedelta(seconds=1)
    await db.commit()

  await asyncio.gather(
    agent_api.sweep_expired_trade_commands(),
    agent_api.sweep_expired_trade_commands(),
  )

  async with session_factory() as db:
    outbox = await db.get(TradeCommandOutbox, queued.message_id)
    pending = await db.get(PendingTradeOrder, queued.client_order_id)
    intent = await db.get(TradeIntentRecord, intent_id)
    event_count = await db.scalar(
      select(func.count()).select_from(StrategyRuntimeEvent)
    )
    assert outbox is not None and outbox.delivery_status == "EXPIRED"
    assert pending is not None and pending.status == "EXPIRED"
    assert intent is not None and intent.status == "EXPIRED"
    assert event_count == 1
  await engine.dispose()


@pytest.mark.asyncio
async def test_expiry_sweeper_runs_immediately_on_startup(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  stopped = asyncio.Event()
  calls = 0

  async def sweep_once() -> int:
    nonlocal calls
    calls += 1
    stopped.set()
    return 0

  monkeypatch.setattr(agent_api, "sweep_expired_trade_commands", sweep_once)
  await agent_api.run_trade_command_expiry_sweeper(
    stopped,
    interval_seconds=60.0,
  )
  assert calls == 1


@pytest.mark.asyncio
async def test_expired_queued_strategy_command_atomically_closes_gate_and_callbacks_once(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path: Path,
) -> None:
  session_factory, engine = await _database(monkeypatch)
  queued, intent_id, batch_id = await _enqueue_strategy_order(
    session_factory,
    idempotency_key="strategy-expiry",
  )
  async with session_factory() as db:
    outbox = await db.get(TradeCommandOutbox, queued.message_id)
    assert outbox is not None
    outbox.expires_at = utcnow() - timedelta(seconds=1)
    await db.commit()

  assert await agent_api._next_command("device-1") is None
  assert await agent_api._next_command("device-1") is None
  async with session_factory() as db:
    outbox = await db.get(TradeCommandOutbox, queued.message_id)
    pending = await db.get(PendingTradeOrder, queued.client_order_id)
    intent = await db.get(TradeIntentRecord, intent_id)
    batch = await db.get(TTradeBatch, batch_id)
    events = (await db.execute(select(StrategyRuntimeEvent))).scalars().all()
    assert outbox is not None and outbox.delivery_status == "EXPIRED"
    assert pending is not None and pending.status == "EXPIRED"
    assert intent is not None and intent.status == "EXPIRED"
    assert batch is not None and batch.status == "ENTRY_EXPIRED"
    assert len(events) == 1
    assert events[0].business_key == (
      f"order:{queued.client_order_id}::EXPIRED:0"
    )
    assert events[0].payload["metadata"]["runtime_event_key"] == (
      events[0].business_key
    )

  context = StrategyContext(
    run_id="run-1",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={"account_id": "account-1"},
  )
  strategy = AshareIntradayTAssistantStrategy(context)
  strategy.state.update(
    {
      "instrument_states": {
        "600000.SH": {
          "status": "ENTRY_SUBMITTED",
          "pending_entry_intent_id": intent_id,
          "entry_order_status": "PENDING",
          "current_signal": {"intent_id": intent_id},
          "batch_id": batch_id,
          "requested_entry_volume": 100,
          "entry_filled_volume": 0,
          "entry_avg_price": 0.0,
          "exit_filled_volume": 0,
        }
      }
    }
  )
  executor = StrategyExecutor()
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="command-expiry-gate",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
    strategy=strategy,
    status=ExecutionStatus.RUNNING,
  )
  runtime.state_manager = RuntimeStateManager(
    run_id=context.run_id,
    persist_enabled=False,
    log_dir=str(tmp_path / "runtime-state"),
  )
  executor.runs[runtime.run_id] = runtime
  runtime.event_task = asyncio.create_task(executor._process_event_queue(runtime))
  monkeypatch.setattr(strategy_manager, "executor", executor)
  try:
    await report_processor._drain_runtime_events()
    await report_processor._drain_runtime_events()
    state = strategy.state["instrument_states"]["600000.SH"]
    assert state["status"] == "OBSERVING"
    assert state["pending_entry_intent_id"] == ""
    assert state["entry_order_status"] == "EXPIRED"
    assert state["batch_id"] == ""
    async with session_factory() as db:
      event = (await db.execute(select(StrategyRuntimeEvent))).scalar_one()
      assert event.application_status == "APPLIED"
      assert runtime.state_manager.has_applied_runtime_event(event.business_key)
  finally:
    runtime.status = ExecutionStatus.STOPPED
    if runtime.event_task:
      runtime.event_task.cancel()
      await asyncio.gather(runtime.event_task, return_exceptions=True)
  await engine.dispose()


@pytest.mark.asyncio
async def test_expired_delivered_strategy_command_fails_closed_for_reconciliation(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  session_factory, engine = await _database(monkeypatch)
  queued, intent_id, batch_id = await _enqueue_strategy_order(
    session_factory,
    idempotency_key="delivered-expiry",
  )
  assert await agent_api._next_command("device-1") is not None
  async with session_factory() as db:
    outbox = await db.get(TradeCommandOutbox, queued.message_id)
    assert outbox is not None and outbox.delivery_status == "DELIVERED"
    outbox.expires_at = utcnow() - timedelta(seconds=1)
    await db.commit()

  assert await agent_api._next_command("device-1") is None
  async with session_factory() as db:
    outbox = await db.get(TradeCommandOutbox, queued.message_id)
    pending = await db.get(PendingTradeOrder, queued.client_order_id)
    intent = await db.get(TradeIntentRecord, intent_id)
    batch = await db.get(TTradeBatch, batch_id)
    event = (await db.execute(select(StrategyRuntimeEvent))).scalar_one()
    assert outbox is not None
    assert outbox.delivery_status == "RECONCILE_REQUIRED"
    assert outbox.last_error == "delivered_command_expired_without_ack"
    assert pending is not None and pending.status == "RECONCILE_REQUIRED"
    assert intent is not None and intent.status == "RECONCILE_REQUIRED"
    assert batch is not None and batch.status == "RECONCILE_REQUIRED"
    assert event.business_key == (
      f"command:{queued.client_order_id}:RECONCILE_REQUIRED"
    )

  received = []

  class CapturingExecutor:
    async def apply_durable_order_report(self, run_id, order) -> None:
      received.append((run_id, order))

  monkeypatch.setattr(strategy_manager, "executor", CapturingExecutor())
  await report_processor._drain_runtime_events()
  assert len(received) == 1
  assert received[0][0] == "run-1"
  assert received[0][1].status == "RECONCILE_REQUIRED"
  await engine.dispose()


@pytest.mark.asyncio
async def test_queued_expiry_with_broker_evidence_cannot_be_declared_unexecuted(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  session_factory, engine = await _database(monkeypatch)
  queued, intent_id, _ = await _enqueue_strategy_order(
    session_factory,
    idempotency_key="queued-contradiction",
  )
  async with session_factory() as db:
    outbox = await db.get(TradeCommandOutbox, queued.message_id)
    pending = await db.get(PendingTradeOrder, queued.client_order_id)
    correlation = await db.scalar(
      select(StrategyOrderCorrelation).where(
        StrategyOrderCorrelation.client_order_id == queued.client_order_id
      )
    )
    assert outbox is not None and pending is not None and correlation is not None
    outbox.expires_at = utcnow() - timedelta(seconds=1)
    pending.broker_order_id = "broker-contradiction"
    correlation.broker_order_id = "broker-contradiction"
    await db.commit()

  assert await agent_api._next_command("device-1") is None
  async with session_factory() as db:
    outbox = await db.get(TradeCommandOutbox, queued.message_id)
    pending = await db.get(PendingTradeOrder, queued.client_order_id)
    intent = await db.get(TradeIntentRecord, intent_id)
    assert outbox is not None
    assert outbox.delivery_status == "RECONCILE_REQUIRED"
    assert "durable_pre_execution_proof_missing" in str(outbox.last_error)
    assert pending is not None and pending.status == "RECONCILE_REQUIRED"
    assert pending.broker_order_id == "broker-contradiction"
    assert intent is not None and intent.status == "RECONCILE_REQUIRED"
  await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_row", ["correlation", "pending"])
async def test_missing_durable_command_link_never_clears_strategy_gate(
  monkeypatch: pytest.MonkeyPatch,
  missing_row: str,
) -> None:
  session_factory, engine = await _database(monkeypatch)
  queued, intent_id, batch_id = await _enqueue_strategy_order(
    session_factory,
    idempotency_key=f"missing-{missing_row}",
  )
  async with session_factory() as db:
    outbox = await db.get(TradeCommandOutbox, queued.message_id)
    pending = await db.get(PendingTradeOrder, queued.client_order_id)
    correlation = await db.scalar(
      select(StrategyOrderCorrelation).where(
        StrategyOrderCorrelation.client_order_id == queued.client_order_id
      )
    )
    assert outbox is not None and pending is not None and correlation is not None
    outbox.expires_at = utcnow() - timedelta(seconds=1)
    await db.delete(correlation)
    if missing_row == "pending":
      await db.delete(pending)
    await db.commit()

  assert await agent_api._next_command("device-1") is None
  async with session_factory() as db:
    outbox = await db.get(TradeCommandOutbox, queued.message_id)
    pending = await db.get(PendingTradeOrder, queued.client_order_id)
    intent = await db.get(TradeIntentRecord, intent_id)
    batch = await db.get(TTradeBatch, batch_id)
    event_count = await db.scalar(
      select(func.count()).select_from(StrategyRuntimeEvent)
    )
    assert outbox is not None
    assert outbox.delivery_status == "RECONCILE_REQUIRED"
    assert "durable_pre_execution_proof_missing" in str(outbox.last_error)
    assert event_count == 0
    if missing_row == "correlation":
      assert pending is not None and pending.status == "RECONCILE_REQUIRED"
      assert intent is not None and intent.status == "RECONCILE_REQUIRED"
      assert batch is not None and batch.status == "RECONCILE_REQUIRED"
    else:
      assert pending is None
      assert intent is not None and intent.status == "PENDING"
      assert batch is not None and batch.status == "ENTRY_QUEUED"
  await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("case_id", "reason", "expected_status"),
  [
    ("precheck", "account_not_whitelisted", "REJECTED"),
    ("expired", "command_expired", "EXPIRED"),
    ("local-gap", "local_reconciliation_required", "RECONCILE_REQUIRED"),
    ("broker-gap", "下单异常: connection reset", "RECONCILE_REQUIRED"),
  ],
)
async def test_rejected_ack_classification_is_fail_closed_and_idempotent(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path: Path,
  case_id: str,
  reason: str,
  expected_status: str,
) -> None:
  session_factory, engine = await _database(
    monkeypatch,
    tmp_path / f"{case_id}.sqlite3",
  )
  queued, intent_id, _ = await _enqueue_strategy_order(
    session_factory,
    idempotency_key=f"ack-{case_id}",
  )
  assert await agent_api._next_command("device-1") is not None
  ack = {
    "command_message_id": queued.message_id,
    "client_order_id": queued.client_order_id,
    "accepted": False,
    "reason": reason,
  }
  await asyncio.gather(
    agent_api._record_command_ack("device-1", ack),
    agent_api._record_command_ack("device-1", ack),
  )

  async with session_factory() as db:
    outbox = await db.get(TradeCommandOutbox, queued.message_id)
    pending = await db.get(PendingTradeOrder, queued.client_order_id)
    intent = await db.get(TradeIntentRecord, intent_id)
    event_count = await db.scalar(
      select(func.count()).select_from(StrategyRuntimeEvent)
    )
    assert outbox is not None and outbox.delivery_status == expected_status
    assert pending is not None and pending.status == expected_status
    assert intent is not None and intent.status == expected_status
    assert event_count == 1
  await engine.dispose()


@pytest.mark.asyncio
async def test_real_broker_reports_override_reconcile_gate_and_restore_monitoring(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path: Path,
) -> None:
  session_factory, engine = await _database(monkeypatch)
  queued, intent_id, batch_id = await _enqueue_strategy_order(
    session_factory,
    idempotency_key="reconcile-then-report",
  )
  command = await agent_api._next_command("device-1")
  assert command is not None
  await agent_api._record_command_ack(
    "device-1",
    {
      "command_message_id": queued.message_id,
      "client_order_id": queued.client_order_id,
      "accepted": False,
      "reason": "local_reconciliation_required",
    },
  )

  simulated = SimulatorBroker({"account-1"}, data_only=False).execute(
    command.payload
  )
  for message_type, report_payload in simulated["reports"]:
    envelope = AgentEnvelope(
      message_type=AgentMessageType(message_type),
      payload=report_payload,
    )
    assert (await agent_api._record_report("device-1", envelope)).accepted
    async with session_factory() as db:
      inbox = await db.get(AgentReportInbox, envelope.message_id)
    assert inbox is not None
    await report_processor._process(inbox)
    await report_processor._stage_runtime_events(inbox)

  final_order_payload = deepcopy(simulated["reports"][0][1])
  final_order_payload["order"]["order_status"] = 56
  final_order_payload["order"]["traded_volume"] = 100
  final_order_payload["order"]["traded_price"] = 10.5
  final_order = AgentEnvelope(
    message_type=AgentMessageType.ORDER_REPORT,
    payload=final_order_payload,
  )
  assert (await agent_api._record_report("device-1", final_order)).accepted
  async with session_factory() as db:
    final_order_inbox = await db.get(AgentReportInbox, final_order.message_id)
  assert final_order_inbox is not None
  await report_processor._process(final_order_inbox)
  await report_processor._stage_runtime_events(final_order_inbox)

  async with session_factory() as db:
    pending = await db.get(PendingTradeOrder, queued.client_order_id)
    intent = await db.get(TradeIntentRecord, intent_id)
    batch = await db.get(TTradeBatch, batch_id)
    assert pending is not None and pending.status == "FILLED"
    assert pending.broker_order_id
    assert intent is not None and intent.status == "FILLED"
    assert intent.executed_volume == 100
    assert batch is not None and batch.status == "OPEN"
    assert batch.entry_filled_volume == 100

  context = StrategyContext(
    run_id="run-1",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={"account_id": "account-1"},
  )
  strategy = AshareIntradayTAssistantStrategy(context)
  strategy.state.update(
    {
      "instrument_states": {
        "600000.SH": {
          "status": "ENTRY_SUBMITTED",
          "pending_entry_intent_id": intent_id,
          "entry_order_status": "PENDING",
          "current_signal": {"intent_id": intent_id},
          "batch_id": batch_id,
          "requested_entry_volume": 100,
          "entry_filled_volume": 0,
          "entry_avg_price": 0.0,
          "exit_filled_volume": 0,
        }
      }
    }
  )
  executor = StrategyExecutor()
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="reconcile-then-report",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
    strategy=strategy,
    status=ExecutionStatus.RUNNING,
  )
  runtime.state_manager = RuntimeStateManager(
    run_id=context.run_id,
    persist_enabled=False,
    log_dir=str(tmp_path / "reconcile-runtime-state"),
  )
  executor.runs[runtime.run_id] = runtime
  runtime.event_task = asyncio.create_task(executor._process_event_queue(runtime))
  monkeypatch.setattr(strategy_manager, "executor", executor)
  try:
    await report_processor._drain_runtime_events()
    state = strategy.state["instrument_states"]["600000.SH"]
    assert state["status"] == "MONITORING"
    assert state["pending_entry_intent_id"] == ""
    assert state["entry_filled_volume"] == 100
    assert state["entry_avg_price"] == pytest.approx(10.5)
    assert state["batch_id"] == batch_id
  finally:
    runtime.status = ExecutionStatus.STOPPED
    if runtime.event_task:
      runtime.event_task.cancel()
      await asyncio.gather(runtime.event_task, return_exceptions=True)
  await engine.dispose()


@pytest.mark.asyncio
async def test_command_waits_for_reconnect_snapshot_convergence(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  session_factory, engine = await _database(monkeypatch)
  queued = await _enqueue_order(session_factory)
  async with session_factory() as db:
    heartbeat = await db.get(
      RuntimeComponentHeartbeat,
      "qmt-agent:device-1",
    )
    assert heartbeat is not None
    heartbeat.status = "RECONCILING"
    await db.commit()

  assert await agent_api._next_command("device-1") is None

  async with session_factory() as db:
    outbox = await db.get(TradeCommandOutbox, queued.message_id)
    assert outbox is not None
    assert outbox.delivery_status == "QUEUED"
    heartbeat = await db.get(
      RuntimeComponentHeartbeat,
      "qmt-agent:device-1",
    )
    assert heartbeat is not None
    heartbeat.status = "READY"
    await db.commit()

  assert await agent_api._next_command("device-1") is not None
  await engine.dispose()


@pytest.mark.asyncio
async def test_reconnect_snapshot_and_rejected_command_are_replay_safe(
  tmp_path: Path,
) -> None:
  class RejectIfCalledBroker:
    def __init__(self) -> None:
      self.execute_calls = 0

    def execute(self, payload):
      del payload
      self.execute_calls += 1
      raise AssertionError("expired command must not reach the broker")

    def full_snapshot(self):
      return {
        "accounts": [],
        "positions_by_account": {"account-1": []},
        "sequence": 42,
        "is_complete": True,
      }

  broker = RejectIfCalledBroker()
  journal_path = tmp_path / "reconnect.sqlite3"
  runtime = _runtime(journal_path, broker)
  expired = AgentEnvelope(
    message_type=AgentMessageType.COMMAND,
    payload={
      "command_kind": "PLACE_ORDER",
      "client_order_id": "client-expired",
      "instance_id": "manual",
      "account_id": "account-1",
      "instrument_code": "600000.SH",
      "side": "BUY",
      "order_type": "FIX_PRICE",
      "limit_price": "10.50",
      "volume": 100,
      "bucket": "manual",
      "risk_decision_id": "risk-1",
      "trace_id": "trace-1",
      "expires_at": (utcnow() - timedelta(seconds=1)).isoformat() + "Z",
      "reason_tags": [],
      "substitution_plan": None,
      "strategy_name": "",
      "order_remark": "",
    },
  )
  first_socket = CapturingSocket()
  await runtime._handle_command(first_socket, expired)
  await runtime._queue_full_snapshot()
  pending_before_restart = runtime.journal.pending_reports()
  assert len(pending_before_restart) == 2

  restarted = _runtime(journal_path, broker)
  second_socket = CapturingSocket()
  await restarted._handle_command(second_socket, expired)
  await restarted._flush_reports(second_socket)
  second_messages = [
    AgentEnvelope.model_validate_json(item) for item in second_socket.sent
  ]
  duplicate_ack = next(
    item
    for item in second_messages
    if item.message_type is AgentMessageType.COMMAND_ACK
  )
  assert duplicate_ack.payload["accepted"] is False
  assert duplicate_ack.payload["reason"] == "command_expired"
  assert any(
    item.message_type is AgentMessageType.DELTA_REPORT
    for item in second_messages
  )
  assert broker.execute_calls == 0
