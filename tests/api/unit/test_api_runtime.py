from __future__ import annotations

from datetime import timedelta

import pytest
from quantx_api import api_runtime
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import RuntimeComponentHeartbeat
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_api_heartbeat_persists_process_generation_and_stop_state(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[RuntimeComponentHeartbeat.__table__],
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  monkeypatch.setattr(api_runtime, "AsyncSessionLocal", sessions)

  await api_runtime.record_api_heartbeat()
  async with sessions() as db:
    heartbeat = await db.get(RuntimeComponentHeartbeat, "api")
    assert heartbeat is not None
    assert heartbeat.instance_id == api_runtime.API_INSTANCE_ID
    assert heartbeat.status == "READY"
    assert heartbeat.details["apiInstanceId"] == api_runtime.API_INSTANCE_ID
    assert heartbeat.details["serverStartedAt"].endswith("Z")

  await api_runtime.record_api_heartbeat(status="STOPPED")
  async with sessions() as db:
    heartbeat = await db.get(RuntimeComponentHeartbeat, "api")
    assert heartbeat is not None
    assert heartbeat.instance_id == api_runtime.API_INSTANCE_ID
    assert heartbeat.status == "STOPPED"

  await engine.dispose()


@pytest.mark.asyncio
async def test_superseded_api_process_cannot_overwrite_newer_generation(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync_connection: Base.metadata.create_all(
        sync_connection,
        tables=[RuntimeComponentHeartbeat.__table__],
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  monkeypatch.setattr(api_runtime, "AsyncSessionLocal", sessions)
  original_started_at = api_runtime.API_STARTED_AT

  monkeypatch.setattr(api_runtime, "API_INSTANCE_ID", "api-old")
  monkeypatch.setattr(api_runtime, "API_STARTED_AT", original_started_at)
  await api_runtime.record_api_heartbeat()

  monkeypatch.setattr(api_runtime, "API_INSTANCE_ID", "api-new")
  monkeypatch.setattr(
    api_runtime,
    "API_STARTED_AT",
    original_started_at + timedelta(seconds=1),
  )
  await api_runtime.record_api_heartbeat()

  monkeypatch.setattr(api_runtime, "API_INSTANCE_ID", "api-old")
  monkeypatch.setattr(api_runtime, "API_STARTED_AT", original_started_at)
  await api_runtime.record_api_heartbeat()
  await api_runtime.record_api_heartbeat(status="STOPPED")

  async with sessions() as db:
    heartbeat = await db.get(RuntimeComponentHeartbeat, "api")
    assert heartbeat is not None
    assert heartbeat.instance_id == "api-new"
    assert heartbeat.status == "READY"

  await engine.dispose()
