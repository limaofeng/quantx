from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
from pydantic import ValidationError
from quantx_contracts import (
  QmtAgentControlConnectionStatus,
  QmtAgentDependencyStatus,
  QmtAgentHealthReason,
  QmtAgentHealthSnapshot,
  QmtAgentHealthStatus,
  QmtAgentMarketStreamStatus,
  QmtAgentMode,
  QmtAgentReconciliationStatus,
)
from quantx_monitor.config import MonitorSettings
from quantx_monitor.models import MonitorStatus, ProbeResult
from quantx_monitor.probes.qmt_agent import (
  QMT_HEALTH_CONNECT_ERROR,
  QMT_HEALTH_HTTP_STATUS,
  QMT_HEALTH_PROTOCOL_ERROR,
  QMT_HEALTH_SCHEMA_MISMATCH,
  QMT_HEALTH_TIMEOUT,
  QmtAgentHealthProbe,
  combine_qmt_agent_probe,
)
from quantx_monitor.scheduler import MonitorScheduler


def health_payload(
  status: QmtAgentHealthStatus = QmtAgentHealthStatus.READY,
  reason: QmtAgentHealthReason | None = None,
) -> dict[str, object]:
  xtdata_status = QmtAgentDependencyStatus.CONNECTED
  if reason is QmtAgentHealthReason.XTDATA_UNAVAILABLE:
    xtdata_status = QmtAgentDependencyStatus.DISCONNECTED
  return QmtAgentHealthSnapshot(
    status=status,
    reason_code=reason,
    agent_version="0.1.0",
    mode=QmtAgentMode.LIVE,
    uptime_seconds=12.5,
    control_connection_status=QmtAgentControlConnectionStatus.CONNECTED,
    reconciliation_status=(
      QmtAgentReconciliationStatus.READY
      if status is QmtAgentHealthStatus.READY
      else QmtAgentReconciliationStatus.RECONCILING
    ),
    xtdata_status=xtdata_status,
    xttrading_status=QmtAgentDependencyStatus.CONNECTED,
    market_stream_status=QmtAgentMarketStreamStatus.READY,
    observed_at=datetime.now(timezone.utc),
  ).model_dump(mode="json")


async def run_probe(handler) -> ProbeResult:
  async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
    return await QmtAgentHealthProbe(
      "http://windows-agent:18084",
      timeout_seconds=1,
    ).run(client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("status_code", "health_status", "reason", "expected"),
  [
    (200, QmtAgentHealthStatus.READY, None, MonitorStatus.HEALTHY),
    (
      503,
      QmtAgentHealthStatus.DEGRADED,
      QmtAgentHealthReason.TRADING_RECONCILING,
      MonitorStatus.DEGRADED,
    ),
    (
      503,
      QmtAgentHealthStatus.UNAVAILABLE,
      QmtAgentHealthReason.XTDATA_UNAVAILABLE,
      MonitorStatus.UNAVAILABLE,
    ),
  ],
)
async def test_qmt_probe_records_rtt_for_valid_200_and_503(
  status_code: int,
  health_status: QmtAgentHealthStatus,
  reason: QmtAgentHealthReason | None,
  expected: MonitorStatus,
) -> None:
  result = await run_probe(
    lambda request: httpx.Response(
      status_code,
      request=request,
      json=health_payload(health_status, reason),
    )
  )

  assert result.observed_status is expected
  assert result.latency_ms is not None
  assert result.latency_ms >= 0
  assert result.status_code == status_code
  assert result.reason_code == (reason.value if reason else None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("error", "expected_reason"),
  [
    (httpx.ConnectError("offline"), QMT_HEALTH_CONNECT_ERROR),
    (httpx.ReadTimeout("slow"), QMT_HEALTH_TIMEOUT),
  ],
)
async def test_qmt_probe_transport_failure_has_no_fake_latency(
  error: Exception,
  expected_reason: str,
) -> None:
  def fail(request: httpx.Request) -> httpx.Response:
    raise error.__class__(str(error), request=request)

  result = await run_probe(fail)

  assert result.observed_status is MonitorStatus.UNAVAILABLE
  assert result.latency_ms is None
  assert result.status_code is None
  assert result.reason_code == expected_reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
  ("response", "expected_reason"),
  [
    (lambda request: httpx.Response(404, request=request), QMT_HEALTH_HTTP_STATUS),
    (
      lambda request: httpx.Response(200, request=request, content=b"not-json"),
      QMT_HEALTH_PROTOCOL_ERROR,
    ),
    (
      lambda request: httpx.Response(
        200,
        request=request,
        json={**health_payload(), "schema_version": 2},
      ),
      QMT_HEALTH_SCHEMA_MISMATCH,
    ),
    (
      lambda request: httpx.Response(
        200,
        request=request,
        json={**health_payload(), "status": "degraded", "reason_code": None},
      ),
      QMT_HEALTH_PROTOCOL_ERROR,
    ),
  ],
)
async def test_qmt_probe_maps_http_and_protocol_failures(
  response,
  expected_reason: str,
) -> None:
  result = await run_probe(response)

  assert result.observed_status is MonitorStatus.UNAVAILABLE
  assert result.latency_ms is None
  assert result.reason_code == expected_reason


def probe_result(
  status: MonitorStatus,
  *,
  latency_ms: float | None = 8.5,
  reason_code: str | None = None,
) -> ProbeResult:
  return ProbeResult(
    target_id="qmt-agent",
    checked_at=datetime.now(timezone.utc),
    observed_status=status,
    latency_ms=latency_ms,
    status_code=200,
    reason_code=reason_code,
  )


@pytest.mark.parametrize(
  ("direct", "semantic", "expected_status", "expected_latency"),
  [
    (
      probe_result(
        MonitorStatus.UNAVAILABLE,
        latency_ms=None,
        reason_code=QMT_HEALTH_CONNECT_ERROR,
      ),
      probe_result(MonitorStatus.HEALTHY, latency_ms=None),
      MonitorStatus.UNAVAILABLE,
      None,
    ),
    (
      probe_result(
        MonitorStatus.UNAVAILABLE,
        reason_code="XTDATA_UNAVAILABLE",
      ),
      probe_result(MonitorStatus.HEALTHY, latency_ms=None),
      MonitorStatus.UNAVAILABLE,
      8.5,
    ),
    (
      probe_result(MonitorStatus.DEGRADED, reason_code="TRADING_RECONCILING"),
      probe_result(MonitorStatus.HEALTHY, latency_ms=None),
      MonitorStatus.DEGRADED,
      8.5,
    ),
    (
      probe_result(MonitorStatus.HEALTHY),
      probe_result(
        MonitorStatus.DEGRADED,
        latency_ms=None,
        reason_code="REMOTE_AGENT_NOT_RECONCILED",
      ),
      MonitorStatus.DEGRADED,
      8.5,
    ),
    (
      probe_result(MonitorStatus.HEALTHY),
      probe_result(
        MonitorStatus.UNAVAILABLE,
        latency_ms=None,
        reason_code="REMOTE_AGENT_SESSION_STALE",
      ),
      MonitorStatus.UNAVAILABLE,
      8.5,
    ),
    (
      probe_result(MonitorStatus.HEALTHY),
      probe_result(MonitorStatus.UNKNOWN, latency_ms=None),
      MonitorStatus.UNAVAILABLE,
      8.5,
    ),
    (
      probe_result(MonitorStatus.HEALTHY),
      probe_result(MonitorStatus.HEALTHY, latency_ms=None),
      MonitorStatus.HEALTHY,
      8.5,
    ),
  ],
)
def test_qmt_probe_combines_the_full_status_matrix(
  direct: ProbeResult,
  semantic: ProbeResult,
  expected_status: MonitorStatus,
  expected_latency: float | None,
) -> None:
  combined = combine_qmt_agent_probe(direct, semantic)

  assert combined.observed_status is expected_status
  assert combined.latency_ms == expected_latency


def test_qmt_probe_reason_priority_is_transport_then_semantic_then_local() -> None:
  transport = combine_qmt_agent_probe(
    probe_result(
      MonitorStatus.UNAVAILABLE,
      latency_ms=None,
      reason_code=QMT_HEALTH_TIMEOUT,
    ),
    probe_result(
      MonitorStatus.UNAVAILABLE,
      latency_ms=None,
      reason_code="REMOTE_AGENT_OFFLINE",
    ),
  )
  semantic = combine_qmt_agent_probe(
    probe_result(
      MonitorStatus.DEGRADED,
      reason_code="MARKET_STREAM_NOT_READY",
    ),
    probe_result(
      MonitorStatus.DEGRADED,
      latency_ms=None,
      reason_code="REMOTE_AGENT_NOT_RECONCILED",
    ),
  )

  assert transport.reason_code == QMT_HEALTH_TIMEOUT
  assert semantic.reason_code == "REMOTE_AGENT_NOT_RECONCILED"


@pytest.mark.parametrize(
  "value",
  [
    "windows-agent:18084",
    "ftp://windows-agent:18084",
    "http://user:secret@windows-agent:18084",
    "http://windows-agent:18084/path",
    "http://windows-agent:18084?target=other",
    "http://windows-agent:18084#fragment",
  ],
)
def test_monitor_rejects_invalid_qmt_health_roots(value: str) -> None:
  with pytest.raises(ValidationError):
    MonitorSettings(MONITOR_QMT_AGENT_HEALTH_URL=value)


def test_monitor_qmt_health_url_validation_does_not_echo_credentials() -> None:
  secret = "monitor-health-secret"

  with pytest.raises(ValidationError) as caught:
    MonitorSettings(
      MONITOR_QMT_AGENT_HEALTH_URL=(f"http://user:{secret}@windows-agent:18084")
    )

  assert secret not in str(caught.value)


@pytest.mark.asyncio
async def test_scheduler_persists_exactly_one_composite_qmt_sample(
  monkeypatch: pytest.MonkeyPatch,
  tmp_path,
) -> None:
  captured: list[ProbeResult] = []

  class Storage:
    @staticmethod
    async def record_results(results) -> None:
      captured.extend(results)

  class DirectProbe:
    def __init__(self, target_id="direct", *_args, **_kwargs) -> None:
      self.target_id = target_id

    async def run(self, *_args) -> ProbeResult:
      return ProbeResult(
        target_id=self.target_id,
        checked_at=datetime.now(timezone.utc),
        observed_status=MonitorStatus.HEALTHY,
        latency_ms=1.0,
      )

  class SnapshotProbe:
    def __init__(self, *_args, **_kwargs) -> None:
      pass

    @staticmethod
    async def run(_client) -> list[ProbeResult]:
      return [
        probe_result(MonitorStatus.HEALTHY, latency_ms=None),
        ProbeResult(
          target_id="engine",
          checked_at=datetime.now(timezone.utc),
          observed_status=MonitorStatus.HEALTHY,
        ),
      ]

  class QmtProbe:
    def __init__(self, *_args, **_kwargs) -> None:
      pass

    @staticmethod
    async def run(_client) -> ProbeResult:
      return probe_result(MonitorStatus.HEALTHY)

  import quantx_monitor.scheduler as scheduler_module

  monkeypatch.setattr(scheduler_module, "PostgreSQLProbe", DirectProbe)
  monkeypatch.setattr(scheduler_module, "RedisProbe", DirectProbe)
  monkeypatch.setattr(scheduler_module, "HttpProbe", DirectProbe)
  monkeypatch.setattr(scheduler_module, "RuntimeSnapshotProbe", SnapshotProbe)
  monkeypatch.setattr(scheduler_module, "QmtAgentHealthProbe", QmtProbe)
  scheduler = MonitorScheduler(
    MonitorSettings(MONITOR_DATABASE_PATH=tmp_path / "monitor.sqlite3"),
    Storage(),
  )
  scheduler._client = httpx.AsyncClient(trust_env=False)
  try:
    await scheduler.run_cycle()
  finally:
    await scheduler._client.aclose()

  qmt_results = [result for result in captured if result.target_id == "qmt-agent"]
  assert len(qmt_results) == 1
  assert qmt_results[0].observed_status is MonitorStatus.HEALTHY
