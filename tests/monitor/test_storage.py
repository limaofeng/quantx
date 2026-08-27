from datetime import datetime, timedelta, timezone

import pytest
from quantx_monitor.models import MonitorStatus, ProbeResult
from quantx_monitor.storage import MonitorStorage


def result(
  status: MonitorStatus,
  checked_at: datetime,
  *,
  latency_ms: float | None = None,
  reason_code: str | None = None,
) -> ProbeResult:
  return ProbeResult(
    target_id="postgresql",
    checked_at=checked_at,
    observed_status=status,
    latency_ms=latency_ms,
    reason_code=reason_code,
  )


@pytest.mark.asyncio
async def test_two_failures_open_and_two_successes_close_an_incident(tmp_path):
  storage = MonitorStorage(tmp_path / "monitor.sqlite3")
  await storage.open(["postgresql"])
  started = datetime(2026, 8, 27, 1, 0, tzinfo=timezone.utc)
  try:
    await storage.record_results(
      [result(MonitorStatus.UNAVAILABLE, started, reason_code="TIMEOUT")]
    )
    state = (await storage.target_states())["postgresql"]
    assert state["effective_status"] == "degraded"
    assert state["active_incident_id"] is None

    await storage.record_results(
      [
        result(
          MonitorStatus.UNAVAILABLE,
          started + timedelta(seconds=30),
          reason_code="TIMEOUT",
        )
      ]
    )
    state = (await storage.target_states())["postgresql"]
    assert state["effective_status"] == "unavailable"
    assert state["active_incident_id"] is not None

    await storage.record_results(
      [
        result(
          MonitorStatus.HEALTHY,
          started + timedelta(seconds=60),
          latency_ms=2.5,
        )
      ]
    )
    state = (await storage.target_states())["postgresql"]
    assert state["effective_status"] == "degraded"
    assert state["active_incident_id"] is not None

    await storage.record_results(
      [
        result(
          MonitorStatus.HEALTHY,
          started + timedelta(seconds=90),
          latency_ms=2.0,
        )
      ]
    )
    state = (await storage.target_states())["postgresql"]
    assert state["effective_status"] == "healthy"
    assert state["active_incident_id"] is None

    incidents = await storage.incidents(
      since=started.timestamp() - 1,
      target_id="postgresql",
    )
    assert len(incidents) == 1
    assert incidents[0]["opened_reason_code"] == "TIMEOUT"
    assert incidents[0]["resolved_at"] == pytest.approx(
      (started + timedelta(seconds=90)).timestamp()
    )
  finally:
    await storage.close()


@pytest.mark.asyncio
async def test_history_and_window_metrics_persist_latency(tmp_path):
  storage = MonitorStorage(tmp_path / "monitor.sqlite3")
  await storage.open(["postgresql"])
  started = datetime(2026, 8, 27, 2, 0, tzinfo=timezone.utc)
  try:
    await storage.record_results(
      [
        result(MonitorStatus.HEALTHY, started, latency_ms=10),
        result(
          MonitorStatus.DEGRADED,
          started + timedelta(seconds=30),
          latency_ms=30,
          reason_code="SLOW_RESPONSE",
        ),
      ]
    )
    now = (started + timedelta(seconds=60)).timestamp()
    metrics = await storage.window_metrics(
      since=started.timestamp(),
      now=now,
      interval_seconds=30,
    )
    assert metrics["postgresql"] == {
      "sampleCount": 2,
      "availabilityPct": 100.0,
      "healthyPct": 50.0,
      "coveragePct": pytest.approx(66.6666666667),
      "latencyP50Ms": 20.0,
      "latencyP95Ms": 29.0,
    }

    history = await storage.history(
      "postgresql",
      since=started.timestamp(),
      now=now,
      bucket_seconds=60,
      use_rollups=False,
    )
    assert len(history) == 1
    assert history[0]["status"] == "degraded"
    assert history[0]["latencyP50Ms"] == 20.0
    assert history[0]["latencyP95Ms"] == 29.0
  finally:
    await storage.close()
