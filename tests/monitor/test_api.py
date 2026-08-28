from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from quantx_monitor.api import build_router
from quantx_monitor.config import MonitorSettings
from quantx_monitor.models import MonitorStatus, ProbeResult
from quantx_monitor.storage import MonitorStorage
from quantx_monitor.targets import TARGETS


@pytest.mark.asyncio
async def test_public_api_is_sanitized_and_rejects_unknown_targets(tmp_path):
  storage = MonitorStorage(tmp_path / "monitor.sqlite3")
  await storage.open(target.target_id for target in TARGETS)
  now = datetime.now(timezone.utc)
  await storage.record_results(
    [
      ProbeResult(
        target_id="postgresql",
        checked_at=now,
        observed_status=MonitorStatus.HEALTHY,
        latency_ms=3.25,
      )
    ]
  )
  settings = MonitorSettings(
    MONITOR_DATABASE_PATH=tmp_path / "monitor.sqlite3",
    MONITOR_CHECK_INTERVAL_SECONDS=30,
    DATABASE_URL="postgresql+asyncpg://secret-user:secret-password@db/private",
    REDIS_PASSWORD="redis-secret",
  )
  runtime = SimpleNamespace(
    settings=settings,
    storage=storage,
    scheduler=SimpleNamespace(
      running=True,
      last_persist_error=None,
      last_cycle_at=now.timestamp(),
    ),
  )
  app = FastAPI()
  app.include_router(build_router(runtime))
  try:
    async with httpx.AsyncClient(
      transport=httpx.ASGITransport(app=app),
      base_url="http://test",
    ) as client:
      ready = await client.get("/monitor/health/ready")
      summary = await client.get("/monitor/api/v1/summary?window=24h")
      unknown = await client.get(
        "/monitor/api/v1/targets/not-a-target/history?range=24h"
      )

    assert ready.status_code == 200
    assert summary.status_code == 200
    payload = summary.json()
    postgresql = next(
      target for target in payload["targets"] if target["id"] == "postgresql"
    )
    assert postgresql["latencyMs"] == 3.25
    assert postgresql["status"] == "healthy"
    assert postgresql["probeKind"] == "direct"
    assert "derived" not in postgresql
    qmt = next(target for target in payload["targets"] if target["id"] == "qmt-agent")
    assert qmt["probeKind"] == "composite"
    assert unknown.status_code == 404
    serialized = summary.text
    assert "secret-user" not in serialized
    assert "secret-password" not in serialized
    assert "redis-secret" not in serialized
    assert "postgresql+asyncpg" not in serialized
    assert settings.qmt_agent_health_url not in serialized
  finally:
    await storage.close()
