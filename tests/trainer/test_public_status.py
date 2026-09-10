from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import RuntimeComponentHeartbeat
from quantx_infrastructure.repositories.trainer_status_repository import TrainerStatusRepository
from quantx_trainer import public_status, training_flow


@pytest.mark.asyncio
async def test_protected_flow_publishes_service_and_queue_without_cpu_gpu_certificate(tmp_path, monkeypatch):
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  try:
    async with engine.begin() as connection:
      await connection.run_sync(lambda db: Base.metadata.create_all(db, tables=[RuntimeComponentHeartbeat.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session(path):
      async with sessions() as db:
        yield db

    config = tmp_path / "trainer.toml"
    config.write_text("fixture")
    observed = datetime.now(timezone.utc)
    reason = "TRADING_OR_POST_CLOSE_CRITICAL_WINDOW"
    monkeypatch.setattr(training_flow, "training_session", session)
    monkeypatch.setattr(training_flow, "_host_admission_reason", lambda: reason)
    probe = Mock(side_effect=AssertionError("protected flow probed GPU"))
    monkeypatch.setattr(training_flow, "_probe_capability", probe)
    monkeypatch.setattr(public_status, "current_config", lambda: SimpleNamespace(state_root=tmp_path))
    monkeypatch.setattr(public_status, "service_status", lambda *a: {"service": "ALIVE", "phase": "WORKER_LOOP", "instance_id": "a" * 32})
    monkeypatch.setattr(public_status, "admission_status", lambda *a: {"admission": "DRAINING"})
    monkeypatch.setattr(public_status, "read_dispatch_status", lambda *a: {
      "state": "FRESH", "observed_at": observed.timestamp(),
      "decision": {"status": "QUEUED", "reason": reason},
    })
    assert await training_flow.stock_selection_training_capability_flow.fn(str(config)) == {"status": "BLOCKED", "reason": reason}
    probe.assert_not_called()
    async with sessions() as db:
      repo = TrainerStatusRepository(db)
      current = await repo.read()
      assert current["fresh"] and current["service"] == "ALIVE"
      assert current["admission"] == "DRAINING"
      assert current["training"]["reason"] == reason
      assert await db.get(RuntimeComponentHeartbeat, "stock-selection-training") is None
      stale = await repo.read(now=observed + timedelta(seconds=95))
      assert stale["fresh"] is False and stale["service"] == "UNKNOWN"
      assert stale["resource_reason"] is None and stale["training"]["reason"] is None
      row = await db.get(RuntimeComponentHeartbeat, "trainer")
      row.details = {**row.details, "training": {
        **row.details["training"], "observed_at": (observed - timedelta(seconds=100)).timestamp(),
      }}
      await db.commit()
      old_dispatch = await repo.read()
      assert old_dispatch["fresh"] is True
      assert old_dispatch["training"]["state"] == "STALE"
      assert old_dispatch["training"]["reason"] is None
      row.details = {"service": "ALIVE", "resource_reason": "password=private"}
      await db.commit()
      invalid = await repo.read()
      assert invalid["fresh"] is False and invalid["service"] == "UNKNOWN"
  finally:
    await engine.dispose()
