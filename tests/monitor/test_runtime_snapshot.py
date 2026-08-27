import httpx
import pytest
from quantx_monitor.models import MonitorStatus
from quantx_monitor.probes.runtime_snapshot import (
  RuntimeSnapshotProbe,
  normalize_component_status,
)


@pytest.mark.parametrize(
  ("value", "expected"),
  [
    ("ready", MonitorStatus.HEALTHY),
    ("stale", MonitorStatus.DEGRADED),
    ("disabled", MonitorStatus.DISABLED),
    ("blocked", MonitorStatus.UNAVAILABLE),
    (None, MonitorStatus.UNKNOWN),
  ],
)
def test_normalize_component_status(value, expected):
  assert normalize_component_status(value) is expected


@pytest.mark.asyncio
async def test_runtime_snapshot_uses_one_request_for_all_derived_targets():
  requests = 0

  def handle(request: httpx.Request) -> httpx.Response:
    nonlocal requests
    requests += 1
    return httpx.Response(
      200,
      request=request,
      json={
        "components": {
          "engine": {"status": "ready"},
          "worker": {"status": "stale"},
          "qmtAgent": {
            "status": "blocked",
            "reasonCode": "REMOTE_AGENT_SESSION_STALE",
          },
          "aiRuntime": {"status": "disabled"},
        }
      },
    )

  async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
    results = await RuntimeSnapshotProbe(
      "http://api/health/components",
      timeout_seconds=1,
    ).run(client)

  assert requests == 1
  assert {result.target_id: result.observed_status for result in results} == {
    "engine": MonitorStatus.HEALTHY,
    "worker": MonitorStatus.DEGRADED,
    "qmt-agent": MonitorStatus.UNAVAILABLE,
    "ai-runtime": MonitorStatus.DISABLED,
  }
  assert all(result.latency_ms is None for result in results)
  qmt = next(result for result in results if result.target_id == "qmt-agent")
  assert qmt.reason_code == "REMOTE_AGENT_SESSION_STALE"
