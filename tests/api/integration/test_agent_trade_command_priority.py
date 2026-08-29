from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from quantx_api import agent_api
from quantx_infrastructure.services.trade_command_service import TradeCommandService
from quantx_contracts import AgentMessageType
from quantx_infrastructure.models.agent_runtime import (
  AgentDevice,
  RuntimeComponentHeartbeat,
  TradeCommandOutbox,
)
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
