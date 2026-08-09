from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from quantx_api import agent_api
from quantx_contracts import AgentEnvelope, AgentMessageType
from quantx_domain.clock import utcnow
from quantx_engine import report_processor
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  AgentDevice,
  AgentReportInbox,
  PendingTradeOrder,
  RuntimeComponentHeartbeat,
  StrategyOrderCorrelation,
  TradeCommandOutbox,
)
from quantx_infrastructure.models.auth import AuthUser
from quantx_infrastructure.models.order import Order
from quantx_infrastructure.models.trade import Trade
from quantx_infrastructure.services import order_service, trade_service
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
) -> tuple[async_sessionmaker[AsyncSession], object]:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
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
async def test_rejected_order_and_cancel_ack_only_change_delivery_state(
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
    assert pending is not None and pending.status == "QUEUED"
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
    assert pending is not None and pending.status == "QUEUED"
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
    assert outbox.last_error == "command_expired"
    assert pending is not None and pending.status == "QUEUED"
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
