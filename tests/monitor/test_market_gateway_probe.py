from datetime import datetime, timedelta, timezone

import httpx
import pytest
from quantx_contracts.market_health import MarketGatewayHealth
from quantx_monitor.models import MonitorStatus, ProbeKind, ProbeResult
from quantx_monitor.probes.http import HttpProbe, market_gateway_status
from quantx_monitor.probes.runtime_snapshot import COMPONENT_TARGETS
from quantx_monitor.storage import MonitorStorage
from quantx_monitor.targets import TARGET_BY_ID


def health_payload(ready=True):
  return MarketGatewayHealth(
    status="ready" if ready else "not_ready",
    reason_code=None if ready else "MARKET_STREAM_STALE",
    connected_devices=1,
    sequence=10,
    instrument_count=100,
    universe_count=100,
    stream_age_seconds=0.5,
    trading_session=True,
  ).model_dump(mode="json", by_alias=True)


@pytest.mark.parametrize("ready", [True, False])
async def test_gateway_http_probe_records_rtt_and_supply_reason(ready):
  async with httpx.AsyncClient(
    transport=httpx.MockTransport(
      lambda request: httpx.Response(200 if ready else 503, json=health_payload(ready))
    )
  ) as client:
    result = await HttpProbe(
      "market-gateway",
      "http://gateway/health/ready",
      timeout_seconds=1,
      evaluator=market_gateway_status,
    ).run(client)
  assert result.observed_status is (
    MonitorStatus.HEALTHY if ready else MonitorStatus.UNAVAILABLE
  )
  assert result.latency_ms is not None
  assert result.status_code == (200 if ready else 503)
  assert result.reason_code == (None if ready else "MARKET_STREAM_STALE")


@pytest.mark.parametrize(
  "status,payload",
  [
    (200, {"status": "ready"}),
    (200, health_payload(False)),
    (503, health_payload()),
    (200, {**health_payload(), "reasonCode": "raw-secret"}),
    (200, {**health_payload(), "connectedDevices": 0}),
  ],
)
def test_gateway_protocol_is_strict_and_does_not_expose_arbitrary_reasons(
  status, payload
):
  result = market_gateway_status(httpx.Response(status), payload)
  assert result == (MonitorStatus.UNAVAILABLE, "PROTOCOL_ERROR")


async def test_gateway_connection_failure_has_no_http_latency():
  def failed(request):
    raise httpx.ConnectError("not connected", request=request)

  async with httpx.AsyncClient(transport=httpx.MockTransport(failed)) as client:
    result = await HttpProbe(
      "market-gateway",
      "http://gateway/health/ready",
      timeout_seconds=1,
      evaluator=market_gateway_status,
    ).run(client)
  assert result.observed_status is MonitorStatus.UNAVAILABLE
  assert result.latency_ms is None


def test_supply_has_one_direct_target_and_no_derived_duplicate():
  assert TARGET_BY_ID["market-gateway"].probe_kind is ProbeKind.DIRECT
  assert TARGET_BY_ID["market-gateway"].name == "行情服务（Market Gateway）"
  assert "market-data" not in TARGET_BY_ID
  assert "marketData" not in COMPONENT_TARGETS


async def test_gateway_http_samples_produce_percentiles(tmp_path):
  storage = MonitorStorage(tmp_path / "latency.sqlite3")
  await storage.open(["market-gateway"])
  now = datetime.now(timezone.utc)
  try:
    for step, latency in enumerate([20.0, 60.0, None]):
      await storage.record_results(
        [
          ProbeResult(
            target_id="market-gateway",
            checked_at=now + timedelta(seconds=step),
            observed_status=MonitorStatus.HEALTHY
            if latency
            else MonitorStatus.UNAVAILABLE,
            latency_ms=latency,
          )
        ]
      )
    metrics = await storage.window_metrics(
      since=now.timestamp() - 1, now=now.timestamp() + 3, interval_seconds=1
    )
    assert metrics["market-gateway"]["latencyP50Ms"] == 40.0
    assert metrics["market-gateway"]["latencyP95Ms"] == 58.0
  finally:
    await storage.close()


async def test_retired_target_history_is_preserved_but_excluded_from_incidents(
  tmp_path,
):
  path = tmp_path / "monitor.sqlite3"
  storage = MonitorStorage(path)
  await storage.open(["market-data", "market-gateway"])
  now = datetime.now(timezone.utc)
  try:
    for step in (0, 1):
      await storage.record_results(
        [
          ProbeResult(
            target_id=target,
            checked_at=now + timedelta(seconds=step),
            observed_status=MonitorStatus.UNAVAILABLE,
          )
          for target in ("market-data", "market-gateway")
        ]
      )
  finally:
    await storage.close()
  await storage.open(["market-gateway"])
  try:
    total, _, rows = await storage.incidents(
      since=now.timestamp() - 1, now=now.timestamp() + 2
    )
    assert total == 1
    assert [row["target_id"] for row in rows] == ["market-gateway"]
    assert "market-data" in await storage.target_states()
  finally:
    await storage.close()
