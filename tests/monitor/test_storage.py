from datetime import datetime, timedelta, timezone

import pytest
from quantx_contracts import (
  ACCOUNT_EXECUTION_SAFETY_CHECK_CODES,
  AccountSafetyCheckObservation,
  AccountSafetyCheckStatus,
  AccountSafetyObservationSnapshot,
)
from quantx_monitor.models import (
  AccountSafetyProbeOutcome,
  MonitorStatus,
  ProbeResult,
)
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


def safety_outcome(
  checked_at: datetime,
  status: AccountSafetyCheckStatus | None,
) -> AccountSafetyProbeOutcome:
  source_status = (
    MonitorStatus.HEALTHY if status is not None else MonitorStatus.UNAVAILABLE
  )
  snapshot = None
  if status is not None:
    snapshot = AccountSafetyObservationSnapshot(
      status="ready",
      observed_at=checked_at,
      checks=[
        AccountSafetyCheckObservation(
          code=code,
          status=(status if code == "MARKET_STREAM_READY" else AccountSafetyCheckStatus.PASSED),
          scope="INCREASE_RISK",
          reason_code=(
            None
            if code != "MARKET_STREAM_READY" or status is AccountSafetyCheckStatus.PASSED
            else "MARKET_CLOSED_STANDBY"
            if status is AccountSafetyCheckStatus.STANDBY
            else "MARKET_STREAM_READY_FAILED"
          ),
          public_message=(
            ""
            if code != "MARKET_STREAM_READY" or status is AccountSafetyCheckStatus.PASSED
            else "当前休市"
            if status is AccountSafetyCheckStatus.STANDBY
            else "行情链路未收敛"
          ),
        )
        for code in ACCOUNT_EXECUTION_SAFETY_CHECK_CODES
      ],
    )
  return AccountSafetyProbeOutcome(
    source=ProbeResult(
      target_id="account-safety-observer",
      checked_at=checked_at,
      observed_status=source_status,
      reason_code=None if snapshot else "ACCOUNT_SAFETY_CONNECT_ERROR",
    ),
    snapshot=snapshot,
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


@pytest.mark.asyncio
async def test_valid_unavailable_http_rtt_contributes_to_latency_metrics(tmp_path):
  storage = MonitorStorage(tmp_path / "monitor.sqlite3")
  await storage.open(["qmt-agent"])
  checked_at = datetime(2026, 8, 27, 3, 0, tzinfo=timezone.utc)
  try:
    await storage.record_results(
      [
        ProbeResult(
          target_id="qmt-agent",
          checked_at=checked_at,
          observed_status=MonitorStatus.UNAVAILABLE,
          latency_ms=42.0,
          status_code=503,
          reason_code="XTDATA_UNAVAILABLE",
        )
      ]
    )
    metrics = await storage.window_metrics(
      since=checked_at.timestamp(),
      now=checked_at.timestamp() + 30,
      interval_seconds=30,
    )

    assert metrics["qmt-agent"]["latencyP50Ms"] == 42.0
    assert metrics["qmt-agent"]["latencyP95Ms"] == 42.0
  finally:
    await storage.close()


@pytest.mark.asyncio
async def test_unavailable_latency_exception_is_scoped_to_qmt_agent(tmp_path):
  storage = MonitorStorage(tmp_path / "monitor.sqlite3")
  await storage.open(["api-public"])
  checked_at = datetime(2026, 8, 27, 3, 0, tzinfo=timezone.utc)
  try:
    await storage.record_results(
      [
        ProbeResult(
          target_id="api-public",
          checked_at=checked_at,
          observed_status=MonitorStatus.UNAVAILABLE,
          latency_ms=42.0,
          status_code=503,
          reason_code="HTTP_STATUS",
        )
      ]
    )
    metrics = await storage.window_metrics(
      since=checked_at.timestamp(),
      now=checked_at.timestamp() + 30,
      interval_seconds=30,
    )

    assert metrics["api-public"]["latencyP50Ms"] is None
    assert metrics["api-public"]["latencyP95Ms"] is None
  finally:
    await storage.close()


@pytest.mark.asyncio
async def test_safety_failed_opens_immediately_unknown_preserves_and_standby_resolves(
  tmp_path,
):
  storage = MonitorStorage(tmp_path / "monitor.sqlite3")
  await storage.open(["account-safety-observer"])
  started = datetime(2026, 8, 27, 4, 0, tzinfo=timezone.utc)
  try:
    await storage.record_cycle(
      [],
      safety_outcome(started, AccountSafetyCheckStatus.FAILED),
    )
    failed_state = (await storage.account_safety_states())["MARKET_STREAM_READY"]
    assert failed_state["status"] == "failed"
    assert failed_state["active_incident_id"] is not None

    await storage.record_cycle(
      [],
      safety_outcome(started + timedelta(seconds=30), None),
    )
    unknown_state = (await storage.account_safety_states())["MARKET_STREAM_READY"]
    assert unknown_state["status"] == "unknown"
    assert unknown_state["active_incident_id"] == failed_state["active_incident_id"]

    await storage.record_cycle(
      [],
      safety_outcome(
        started + timedelta(seconds=60),
        AccountSafetyCheckStatus.STANDBY,
      ),
    )
    standby_state = (await storage.account_safety_states())["MARKET_STREAM_READY"]
    assert standby_state["status"] == "standby"
    assert standby_state["active_incident_id"] is None

    incidents = await storage.account_safety_incidents(
      since=started.timestamp() - 1,
      now=(started + timedelta(seconds=90)).timestamp(),
    )
    assert len(incidents) == 1
    assert incidents[0]["resolved_at"] == pytest.approx(
      (started + timedelta(seconds=60)).timestamp()
    )
  finally:
    await storage.close()
