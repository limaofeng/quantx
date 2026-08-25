import pytest
from quantx_api import agent_api
from quantx_contracts import AgentEnvelope, AgentMessageType
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import AgentReportInbox
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_same_execution_with_new_message_id_is_acknowledged_once(
  monkeypatch,
) -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[AgentReportInbox.__table__],
      )
    )
  session_factory = async_sessionmaker(engine, expire_on_commit=False)
  monkeypatch.setattr(agent_api, "AsyncSessionLocal", session_factory)
  wakeups = []

  async def record_wakeup(channel, message):
    wakeups.append((channel, message))
    return 1

  monkeypatch.setattr(agent_api.redis_pubsub, "publish", record_wakeup)
  payload = {
    "client_order_id": "client-1",
    "execution": {
      "account_id": "account-1",
      "execution_id": "execution-1",
      "order_id": 123,
      "traded_volume": 100,
      "traded_price": 10,
    },
  }
  first = await agent_api._record_report(
    "device-1",
    AgentEnvelope(
      message_id="00000000-0000-4000-8000-000000000001",
      message_type=AgentMessageType.EXECUTION_REPORT,
      payload=payload,
    ),
    received_at=agent_api.utcnow(),
  )
  duplicate = await agent_api._record_report(
    "device-1",
    AgentEnvelope(
      message_id="00000000-0000-4000-8000-000000000002",
      message_type=AgentMessageType.EXECUTION_REPORT,
      payload=payload,
    ),
    received_at=agent_api.utcnow(),
  )

  async with session_factory() as db:
    count = await db.scalar(select(func.count()).select_from(AgentReportInbox))
  assert first.accepted and not first.duplicate
  assert duplicate.accepted and duplicate.duplicate
  assert count == 1
  assert [channel for channel, _ in wakeups] == [
    agent_api.AGENT_REPORT_WAKE_CHANNEL,
    agent_api.AGENT_REPORT_WAKE_CHANNEL,
  ]
  await engine.dispose()
