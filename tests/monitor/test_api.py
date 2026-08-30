from datetime import datetime, timedelta, timezone
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
      safety_history = await client.get(
        "/monitor/internal/api/v1/account-safety/history?range=24h"
      )
      unknown = await client.get(
        "/monitor/api/v1/targets/not-a-target/history?range=24h"
      )

    assert ready.status_code == 200
    assert summary.status_code == 200
    assert safety_history.status_code == 200
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
    safety_payload = safety_history.json()
    assert len(safety_payload["checks"]) == 18
    assert all(
      check["currentStatus"] == "unknown" for check in safety_payload["checks"]
    )
    assert "accountId" not in safety_history.text
    assert "300000013250" not in safety_history.text
  finally:
    await storage.close()


@pytest.mark.asyncio
async def test_incident_pagination_covers_all_records_and_overlapping_incidents(
  tmp_path,
):
  storage = MonitorStorage(tmp_path / "history.sqlite3")
  await storage.open(target.target_id for target in TARGETS)
  now = datetime.now(timezone.utc)
  epoch = now.timestamp()
  try:
    assert storage._db is not None
    await storage._db.executemany(
      "INSERT INTO incidents (target_id, opened_at, resolved_at, opened_reason_code) VALUES (?, ?, ?, ?)",
      [("qmt-agent", epoch - 100, epoch - 50, "CONNECT_ERROR") for _ in range(205)]
      + [
        ("qmt-agent", epoch - 2 * 86400, None, "QMT_AGENT_NOT_RECONCILED"),
        ("qmt-agent", epoch - 2 * 86400, epoch - 30, "XTTRADING_UNAVAILABLE"),
        ("qmt-agent", epoch - 2 * 86400, epoch - 86401, "TIMEOUT"),
        ("engine", epoch - 100, None, "TIMEOUT"),
        ("qmt-agent", epoch + 86400, None, "TIMEOUT"),
      ],
    )
    await storage._db.commit()
    runtime = SimpleNamespace(storage=storage)
    app = FastAPI()
    app.include_router(build_router(runtime))
    async with httpx.AsyncClient(
      transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
      params = {"targetId": "qmt-agent", "range": "24h", "pageSize": 100}
      first = (await client.get("/monitor/api/v1/incidents", params=params)).json()
      assert first["total"] == 207
      assert first["page"] == 1
      assert first["pageSize"] == 100
      assert len(first["incidents"]) == 100
      # An incident created after page one must not move older records across pages.
      cutoff = datetime.fromisoformat(first["asOf"].replace("Z", "+00:00")).timestamp()
      await storage._db.execute(
        "INSERT INTO incidents (target_id, opened_at) VALUES (?, ?)",
        ("qmt-agent", cutoff + 0.001),
      )
      await storage._db.commit()
      params["asOf"] = first["asOf"]
      second = (
        await client.get("/monitor/api/v1/incidents", params={**params, "page": 2})
      ).json()
      third = (
        await client.get("/monitor/api/v1/incidents", params={**params, "page": 3})
      ).json()
      assert second["total"] == third["total"] == 207
      ids = [
        item["id"]
        for payload in (first, second, third)
        for item in payload["incidents"]
      ]
      assert len(set(ids)) == len(ids) == 207
      assert ids[:205] == list(range(205, 0, -1))
      assert ids[-2:] == [207, 206]
      assert third["incidents"][-1]["active"] is True
      beyond = (
        await client.get("/monitor/api/v1/incidents", params={**params, "page": 4})
      ).json()
      assert beyond["incidents"] == []
      empty = (
        await client.get(
          "/monitor/api/v1/incidents", params={**params, "targetId": "redis"}
        )
      ).json()
      assert empty["total"] == 0
      for invalid in (
        {"page": 0},
        {"pageSize": 0},
        {"pageSize": 101},
        {"range": "all"},
        {"asOf": "invalid"},
        {"asOf": "2026-01-01T00:00:00"},
        {"asOf": (now + timedelta(days=2)).isoformat()},
      ):
        response = await client.get(
          "/monitor/api/v1/incidents", params={**params, **invalid}
        )
        assert response.status_code == 422
      unknown = await client.get(
        "/monitor/api/v1/incidents", params={**params, "targetId": "missing"}
      )
      assert unknown.status_code == 404
  finally:
    await storage.close()
