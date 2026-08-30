from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_api import agent_api
from quantx_contracts import AgentMessageType
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  AgentDevice,
  RuntimeComponentHeartbeat,
  TradeCommandOutbox,
)
from quantx_infrastructure.services.account_execution_quarantine_service import (
  AccountExecutionQuarantineService,
)
from quantx_infrastructure.services.trade_command_service import TradeCommandService
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

DEVICE_ID = "22222222-2222-4222-8222-222222222222"


@asynccontextmanager
async def _trade_command_database():
  engine = create_async_engine(
    "sqlite+aiosqlite:///:memory:",
    poolclass=StaticPool,
  )
  tables = (
    AgentDevice.__table__,
    AccountExecutionControl.__table__,
    RuntimeComponentHeartbeat.__table__,
    TradeCommandOutbox.__table__,
  )
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: AgentDevice.metadata.create_all(
        sync_connection,
        tables=tables,
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  try:
    yield sessions
  finally:
    await engine.dispose()


def _control_session(now) -> agent_api.AgentControlSession:
  return agent_api.AgentControlSession(
    device_id=DEVICE_ID,
    capabilities={"market-data", "live"},
    authorized_account_ids=frozenset({"account-1"}),
    queue=asyncio.Queue(),
    api_instance_id="api-instance-1",
    agent_session_id="agent-session-1",
    server_connected_at=now,
    remote_address_summary="10.0.0.*",
    revoked=asyncio.Event(),
  )


def _command(
  *,
  message_id: str,
  client_order_id: str,
  kind: str,
  created_at,
  status: str = "QUEUED",
  delivered_at=None,
) -> TradeCommandOutbox:
  return TradeCommandOutbox(
    message_id=message_id,
    client_order_id=client_order_id,
    idempotency_key=f"key:{client_order_id}",
    device_id=DEVICE_ID,
    account_id="account-1",
    payload={
      "command_kind": kind,
      "client_order_id": client_order_id,
      "account_id": "account-1",
      "execution_mode": "live",
      "expires_at": (created_at + timedelta(minutes=5)).isoformat() + "Z",
    },
    delivery_status=status,
    delivered_at=delivered_at,
    expires_at=created_at + timedelta(minutes=5),
    attempts=1 if delivered_at is not None else 0,
    created_at=created_at,
    updated_at=created_at,
  )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cancel_dispatch_bypasses_fresh_unacknowledged_order(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  now = agent_api.utcnow()
  old_order_id = "11111111-1111-4111-8111-111111111111"
  normal_order_id = "33333333-3333-4333-8333-333333333333"
  cancel_id = "44444444-4444-4444-8444-444444444444"
  async with _trade_command_database() as sessions:
    monkeypatch.setattr(agent_api, "AsyncSessionLocal", sessions)
    async with sessions() as db:
      db.add(
        AgentDevice(
          id=DEVICE_ID,
          user_id="user-1",
          name="priority-agent",
          secret_hash="0" * 64,
          authorized_account_ids=["account-1"],
          capabilities=["live", "market-data"],
          created_at=now,
          updated_at=now,
        )
      )
      db.add(
        RuntimeComponentHeartbeat(
          component=f"qmt-agent:{DEVICE_ID}",
          instance_id=DEVICE_ID,
          status="READY",
          details={
            "apiInstanceId": "api-instance-1",
            "agentSessionId": "agent-session-1",
            "serverReceivedAt": now.isoformat(),
            "agentSentAt": now.isoformat(),
            "sessionActive": True,
          },
          updated_at=now,
        )
      )
      db.add(
        AccountExecutionControl(
          account_id="account-1",
          authorization_state="DISABLED",
          reconcile_status="READY",
        )
      )
      db.add_all(
        [
          _command(
            message_id=old_order_id,
            client_order_id="old-order",
            kind="PLACE_ORDER",
            created_at=now - timedelta(seconds=3),
            status="DELIVERED",
            delivered_at=now,
          ),
          _command(
            message_id=normal_order_id,
            client_order_id="normal-order",
            kind="PLACE_ORDER",
            created_at=now - timedelta(seconds=2),
          ),
          _command(
            message_id=cancel_id,
            client_order_id="cancel-order",
            kind="CANCEL_ORDER",
            created_at=now - timedelta(seconds=1),
          ),
        ]
      )
      await db.commit()

    session = _control_session(now)
    cancel = await agent_api._next_command(session)
    blocked_normal = await agent_api._next_command(session)

    assert cancel is not None
    assert cancel.message_id == cancel_id
    assert cancel.message_type is AgentMessageType.CANCEL_COMMAND
    assert blocked_normal is None

    async with sessions() as db:
      active = await db.get(TradeCommandOutbox, old_order_id)
      delivered_cancel = await db.get(TradeCommandOutbox, cancel_id)
      assert active is not None and delivered_cancel is not None
      active.delivery_status = "ACKNOWLEDGED"
      delivered_cancel.delivery_status = "ACKNOWLEDGED"
      await db.commit()

    normal = await agent_api._next_command(session)
    assert normal is not None and normal.message_id == normal_order_id
    assert await agent_api._next_command(session) is None

    async with sessions() as db:
      delivered_normal = await db.get(TradeCommandOutbox, normal_order_id)
      stale = await db.get(TradeCommandOutbox, old_order_id)
      assert delivered_normal is not None and stale is not None
      delivered_normal.delivery_status = "ACKNOWLEDGED"
      stale.delivery_status = "DELIVERED"
      stale.delivered_at = now - timedelta(
        seconds=agent_api.TRADE_COMMAND_REDELIVERY_SECONDS + 1
      )
      await db.commit()

    redelivered = await agent_api._next_command(session)
    assert redelivered is not None and redelivered.message_id == old_order_id


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cancel_is_created_selected_and_revalidated_while_reconciling(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  now = agent_api.utcnow()
  async with _trade_command_database() as sessions:
    monkeypatch.setattr(agent_api, "AsyncSessionLocal", sessions)
    async with sessions() as db:
      db.add_all(
        [
          AgentDevice(
            id=DEVICE_ID,
            user_id="user-1",
            name="reconciling-agent",
            secret_hash="0" * 64,
            authorized_account_ids=["account-1"],
            capabilities=["live", "market-data"],
            created_at=now,
            updated_at=now,
          ),
          RuntimeComponentHeartbeat(
            component=f"qmt-agent:{DEVICE_ID}",
            instance_id=DEVICE_ID,
            status="RECONCILING",
            details={
              "apiInstanceId": "api-instance-1",
              "agentSessionId": "agent-session-1",
              "serverReceivedAt": now.isoformat(),
              "agentSentAt": now.isoformat(),
              "sessionActive": True,
            },
            updated_at=now,
          ),
        ]
      )
      await db.commit()
      queued = await TradeCommandService(db).enqueue_cancel(
        user_id="user-1",
        account_id="account-1",
        broker_order_id="broker-order-1",
        execution_mode="live",
      )

    session = _control_session(now)
    envelope = await agent_api._next_command(session)

    assert envelope is not None
    assert envelope.message_id == queued.message_id
    assert envelope.message_type is AgentMessageType.CANCEL_COMMAND
    await agent_api._assert_trade_delivery_session(session, envelope)


def test_emergency_command_has_cancel_level_outbound_priority() -> None:
  emergency = agent_api.AgentEnvelope(
    message_type=AgentMessageType.COMMAND,
    payload={"command_kind": "EMERGENCY_STOP"},
  )

  assert agent_api._outbound_priority(emergency) == (1, False)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_live_sell_is_not_claimed_until_account_reconciliation_is_clean(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  now = agent_api.utcnow()
  message_id = "55555555-5555-4555-8555-555555555555"
  async with _trade_command_database() as sessions:
    monkeypatch.setattr(agent_api, "AsyncSessionLocal", sessions)
    async with sessions() as db:
      db.add_all(
        [
          AgentDevice(
            id=DEVICE_ID,
            user_id="user-1",
            name="quarantined-agent",
            secret_hash="0" * 64,
            authorized_account_ids=["account-1"],
            capabilities=["live", "market-data"],
            created_at=now,
            updated_at=now,
          ),
          RuntimeComponentHeartbeat(
            component=f"qmt-agent:{DEVICE_ID}",
            instance_id=DEVICE_ID,
            status="READY",
            details={
              "apiInstanceId": "api-instance-1",
              "agentSessionId": "agent-session-1",
              "serverReceivedAt": now.isoformat(),
              "agentSentAt": now.isoformat(),
              "sessionActive": True,
            },
            updated_at=now,
          ),
          AccountExecutionControl(
            account_id="account-1",
            authorization_state="PAUSED",
            reconcile_status="RECONCILE_REQUIRED",
            paused_reason=('[{"kind":"BROKER_EXECUTION_AFTER_RELEASE"}]'),
          ),
          _command(
            message_id=message_id,
            client_order_id="other-live-sell",
            kind="PLACE_ORDER",
            created_at=now,
          ),
        ]
      )
      command = await db.get(TradeCommandOutbox, message_id)
      assert command is not None
      command.payload = {**dict(command.payload), "side": "SELL"}
      await db.commit()

    session = _control_session(now)
    assert await agent_api._next_command(session) is None
    async with sessions() as db:
      queued = await db.get(TradeCommandOutbox, message_id)
      assert queued is not None and queued.delivery_status == "QUEUED"
      control = await db.get(AccountExecutionControl, "account-1")
      assert control is not None
      control.reconcile_status = "READY"
      control.authorization_state = "DISABLED"
      control.paused_reason = None
      await db.commit()

    delivered = await agent_api._next_command(session)
    assert delivered is not None and delivered.message_id == message_id


@pytest.mark.asyncio
@pytest.mark.integration
async def test_live_buy_physical_gate_keeps_account_lock_without_sell_validation(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  now = agent_api.utcnow()
  message_id = "physical-send-live-buy"
  async with _trade_command_database() as sessions:
    monkeypatch.setattr(agent_api, "AsyncSessionLocal", sessions)
    async with sessions() as db:
      command = _command(
        message_id=message_id,
        client_order_id="physical-send-live-buy-order",
        kind="PLACE_ORDER",
        created_at=now,
        status="DELIVERED",
        delivered_at=now,
      )
      command.payload = {**dict(command.payload), "side": "BUY"}
      db.add_all(
        [
          AgentDevice(
            id=DEVICE_ID,
            user_id="user-1",
            name="physical-buy-agent",
            secret_hash="0" * 64,
            authorized_account_ids=["account-1"],
            capabilities=["live", "market-data"],
            created_at=now,
            updated_at=now,
          ),
          AccountExecutionControl(
            account_id="account-1",
            authorization_state="ENABLED",
            reconcile_status="READY",
          ),
          command,
        ]
      )
      await db.commit()

    async with sessions() as db:
      delivery = await AccountExecutionQuarantineService(
        db
      ).lock_command_for_physical_send(
        message_id=message_id,
        expected_payload=command.payload,
      )
      assert delivery.command is not None
      assert delivery.command.message_id == message_id
      assert not delivery.blocked_reason
      await db.rollback()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_claimed_live_sell_is_rechecked_after_quarantine_before_socket_send(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  now = agent_api.utcnow()
  message_id = "physical-send-quarantine-message"
  async with _trade_command_database() as sessions:
    monkeypatch.setattr(agent_api, "AsyncSessionLocal", sessions)
    async with sessions() as db:
      db.add_all(
        [
          AgentDevice(
            id=DEVICE_ID,
            user_id="user-1",
            name="physical-send-agent",
            secret_hash="0" * 64,
            authorized_account_ids=["account-1"],
            capabilities=["live", "market-data"],
            created_at=now,
            updated_at=now,
          ),
          RuntimeComponentHeartbeat(
            component=f"qmt-agent:{DEVICE_ID}",
            instance_id=DEVICE_ID,
            status="READY",
            details={
              "apiInstanceId": "api-instance-1",
              "agentSessionId": "agent-session-1",
              "serverReceivedAt": now.isoformat(),
              "agentSentAt": now.isoformat(),
              "sessionActive": True,
            },
            updated_at=now,
          ),
          AccountExecutionControl(
            account_id="account-1",
            authorization_state="DISABLED",
            reconcile_status="READY",
          ),
          _command(
            message_id=message_id,
            client_order_id="physical-send-live-sell",
            kind="PLACE_ORDER",
            created_at=now,
          ),
        ]
      )
      command = await db.get(TradeCommandOutbox, message_id)
      assert command is not None
      command.payload = {**dict(command.payload), "side": "SELL"}
      await db.commit()

    control_session = _control_session(now)
    envelope = await agent_api._next_command(control_session)
    assert envelope is not None and envelope.message_id == message_id

    # A distinct transaction wins the Account -> Outbox lifecycle boundary
    # after claim but before the physical writer reaches send_text.
    async with sessions() as quarantine_db:
      control = await quarantine_db.get(
        AccountExecutionControl,
        "account-1",
        with_for_update=True,
        populate_existing=True,
      )
      command = await quarantine_db.get(
        TradeCommandOutbox,
        message_id,
        with_for_update=True,
        populate_existing=True,
      )
      assert control is not None and command is not None
      control.authorization_state = "PAUSED"
      control.reconcile_status = "RECONCILE_REQUIRED"
      control.paused_reason = '[{"kind":"BROKER_EXECUTION_AFTER_RELEASE"}]'
      command.delivery_status = "RECONCILE_REQUIRED"
      command.last_error = "broker_execution_after_released_exit"
      await quarantine_db.commit()

    checked = asyncio.Event()
    original_lock = AccountExecutionQuarantineService.lock_command_for_physical_send

    async def observed_lock(service, **kwargs):
      result = await original_lock(service, **kwargs)
      checked.set()
      return result

    monkeypatch.setattr(
      AccountExecutionQuarantineService,
      "lock_command_for_physical_send",
      observed_lock,
    )
    monkeypatch.setattr(
      agent_api.agent_connection_hub,
      "is_connected",
      AsyncMock(return_value=True),
    )
    websocket = SimpleNamespace(send_text=AsyncMock())
    outbound = agent_api._AgentOutboundBuffer()
    await agent_api._enqueue_agent_outbound(DEVICE_ID, outbound, envelope)
    writer_reentered_queue = asyncio.Event()
    original_get = outbound.get
    get_calls = 0

    async def observed_get():
      nonlocal get_calls
      get_calls += 1
      if get_calls == 2:
        writer_reentered_queue.set()
      return await original_get()

    outbound.get = observed_get
    writer = asyncio.create_task(
      agent_api._send_agent_control_messages(
        websocket,
        control_session=control_session,
        outbound=outbound,
      )
    )
    await asyncio.wait_for(checked.wait(), timeout=1)
    await asyncio.wait_for(writer_reentered_queue.wait(), timeout=1)
    writer.cancel()
    await asyncio.gather(writer, return_exceptions=True)

    websocket.send_text.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("quarantined_status", ["CANCELLED", "RECONCILE_REQUIRED"])
async def test_quarantined_place_order_is_not_returned_after_clean_reenable(
  monkeypatch: pytest.MonkeyPatch,
  quarantined_status: str,
) -> None:
  now = agent_api.utcnow()
  message_id = "66666666-6666-4666-8666-666666666666"
  async with _trade_command_database() as sessions:
    monkeypatch.setattr(agent_api, "AsyncSessionLocal", sessions)
    async with sessions() as db:
      db.add_all(
        [
          AgentDevice(
            id=DEVICE_ID,
            user_id="user-1",
            name="clean-agent",
            secret_hash="0" * 64,
            authorized_account_ids=["account-1"],
            capabilities=["live", "market-data"],
            created_at=now,
            updated_at=now,
          ),
          RuntimeComponentHeartbeat(
            component=f"qmt-agent:{DEVICE_ID}",
            instance_id=DEVICE_ID,
            status="READY",
            details={
              "apiInstanceId": "api-instance-1",
              "agentSessionId": "agent-session-1",
              "serverReceivedAt": now.isoformat(),
              "agentSentAt": now.isoformat(),
              "sessionActive": True,
            },
            updated_at=now,
          ),
          AccountExecutionControl(
            account_id="account-1",
            authorization_state="ENABLED",
            reconcile_status="READY",
          ),
          _command(
            message_id=message_id,
            client_order_id="quarantined-old-sell",
            kind="PLACE_ORDER",
            created_at=now,
            status=quarantined_status,
          ),
        ]
      )
      command = await db.get(TradeCommandOutbox, message_id)
      assert command is not None
      command.payload = {**dict(command.payload), "side": "SELL"}
      await db.commit()

    assert await agent_api._next_command(_control_session(now)) is None
    async with sessions() as db:
      unchanged = await db.get(TradeCommandOutbox, message_id)
      assert unchanged is not None
      assert unchanged.delivery_status == quarantined_status


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(
  ("canonical_status", "canonical_error"),
  [
    ("RECONCILE_REQUIRED", "broker_execution_after_released_exit"),
    ("RECONCILE_REQUIRED", "quarantine_current_intent_binding_mismatch"),
    ("RECONCILE_REQUIRED", "quarantine_plan_pending_release_failed"),
    ("CANCELLED", "account_quarantined_before_agent_delivery"),
    ("ACKNOWLEDGED", "previous_acknowledgement"),
    ("REJECTED", "pre_execution_rejection"),
    ("EXPIRED", "command_expired_before_delivery"),
  ],
)
async def test_late_processing_ack_never_revives_quarantined_or_terminal_place(
  monkeypatch: pytest.MonkeyPatch,
  canonical_status: str,
  canonical_error: str,
) -> None:
  now = agent_api.utcnow()
  message_id = "88888888-8888-4888-8888-888888888888"
  stale_delivery = now - timedelta(
    seconds=agent_api.TRADE_COMMAND_REDELIVERY_SECONDS + 1
  )
  async with _trade_command_database() as sessions:
    monkeypatch.setattr(agent_api, "AsyncSessionLocal", sessions)
    async with sessions() as db:
      db.add_all(
        [
          AgentDevice(
            id=DEVICE_ID,
            user_id="user-1",
            name="late-ack-agent",
            secret_hash="0" * 64,
            authorized_account_ids=["account-1"],
            capabilities=["live", "market-data"],
            created_at=now,
            updated_at=now,
          ),
          RuntimeComponentHeartbeat(
            component=f"qmt-agent:{DEVICE_ID}",
            instance_id=DEVICE_ID,
            status="READY",
            details={
              "apiInstanceId": "api-instance-1",
              "agentSessionId": "agent-session-1",
              "serverReceivedAt": now.isoformat(),
              "agentSentAt": now.isoformat(),
              "sessionActive": True,
            },
            updated_at=now,
          ),
          AccountExecutionControl(
            account_id="account-1",
            authorization_state="PAUSED",
            reconcile_status="RECONCILE_REQUIRED",
            paused_reason='[{"kind":"BROKER_EXECUTION_AFTER_RELEASE"}]',
          ),
          _command(
            message_id=message_id,
            client_order_id="late-ack-old-sell",
            kind="PLACE_ORDER",
            created_at=now,
            status=canonical_status,
            delivered_at=stale_delivery,
          ),
        ]
      )
      command = await db.get(TradeCommandOutbox, message_id)
      assert command is not None
      command.payload = {**dict(command.payload), "side": "SELL"}
      command.last_error = canonical_error
      await db.commit()

    await agent_api._record_command_ack(
      DEVICE_ID,
      {
        "command_message_id": message_id,
        "client_order_id": "late-ack-old-sell",
        "accepted": False,
        "reason": "command_processing",
      },
    )
    async with sessions() as db:
      preserved = await db.get(TradeCommandOutbox, message_id)
      assert preserved is not None
      assert preserved.delivery_status == canonical_status
      assert preserved.last_error == canonical_error

    if canonical_status == "RECONCILE_REQUIRED":
      await agent_api._record_command_ack(
        DEVICE_ID,
        {
          "command_message_id": message_id,
          "client_order_id": "late-ack-old-sell",
          "accepted": True,
          "reason": "journal_replay",
        },
      )
      async with sessions() as db:
        preserved = await db.get(TradeCommandOutbox, message_id)
        assert preserved is not None
        assert preserved.delivery_status == "RECONCILE_REQUIRED"
        assert preserved.last_error == canonical_error

    async with sessions() as db:
      control = await db.get(AccountExecutionControl, "account-1")
      assert control is not None
      control.reconcile_status = "READY"
      control.authorization_state = "ENABLED"
      control.paused_reason = None
      await db.commit()

    assert await agent_api._next_command(_control_session(now)) is None
    async with sessions() as db:
      preserved = await db.get(TradeCommandOutbox, message_id)
      assert preserved is not None
      assert preserved.delivery_status == canonical_status
      assert preserved.last_error == canonical_error


@pytest.mark.asyncio
async def test_live_place_ack_locks_account_before_outbox(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  events: list[str] = []
  command = SimpleNamespace(
    message_id="77777777-7777-4777-8777-777777777777",
    client_order_id="ack-live-sell",
    account_id="account-1",
    payload={
      "command_kind": "PLACE_ORDER",
      "account_id": "account-1",
      "execution_mode": "live",
      "side": "SELL",
    },
    delivery_status="ACKNOWLEDGED",
    acknowledged_at=None,
    last_error=None,
  )
  control = SimpleNamespace(reconcile_status="READY")

  class CandidateResult:
    @staticmethod
    def one_or_none():
      events.append("candidate")
      return (command.account_id, command.payload)

  class FakeSession:
    async def execute(self, _query):
      return CandidateResult()

    async def get(self, model, key, **kwargs):
      assert kwargs == {
        "with_for_update": True,
        "populate_existing": True,
      }
      if model is AccountExecutionControl:
        events.append("account")
        assert key == "account-1"
        return control
      assert model is TradeCommandOutbox
      events.append("outbox")
      assert key == command.message_id
      return command

    async def commit(self):
      events.append("commit")

  @asynccontextmanager
  async def session_local():
    yield FakeSession()

  monkeypatch.setattr(agent_api, "AsyncSessionLocal", session_local)
  await agent_api._record_command_ack(
    DEVICE_ID,
    {
      "command_message_id": command.message_id,
      "client_order_id": command.client_order_id,
      "accepted": True,
      "reason": "journal_replay",
    },
  )

  assert events == ["candidate", "account", "outbox", "commit"]
