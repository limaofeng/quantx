from datetime import datetime, timezone

import pytest
from quantx_monitor.config import MonitorSettings
from quantx_monitor.models import MonitorStatus, ProbeKind, ProbeResult
from quantx_monitor.scheduler import MonitorScheduler
from quantx_monitor.storage import MonitorStorage
from quantx_monitor.targets import TARGET_BY_ID


def test_market_data_service_is_a_direct_monitor_target() -> None:
  target = TARGET_BY_ID["market-data-service"]

  assert target.name == "行情服务"
  assert target.probe_kind is ProbeKind.DIRECT
  assert "market-data" not in TARGET_BY_ID
  assert "market-gateway" not in TARGET_BY_ID


@pytest.mark.asyncio
async def test_scheduler_persists_first_cycle_before_becoming_ready(
  tmp_path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  settings = MonitorSettings(
    MONITOR_DATABASE_PATH=tmp_path / "monitor.sqlite3",
    MONITOR_CHECK_INTERVAL_SECONDS=300,
  )
  storage = MonitorStorage(settings.database_path)
  await storage.open(["market-data-service"])
  scheduler = MonitorScheduler(settings, storage)
  first_cycle_complete = False

  async def first_cycle() -> None:
    nonlocal first_cycle_complete
    await storage.record_results(
      [
        ProbeResult(
          target_id="market-data-service",
          checked_at=datetime.now(timezone.utc),
          observed_status=MonitorStatus.HEALTHY,
          latency_ms=1.25,
        )
      ]
    )
    scheduler.last_cycle_at = datetime.now(timezone.utc).timestamp()
    first_cycle_complete = True

  monkeypatch.setattr(scheduler, "run_cycle", first_cycle)

  try:
    await scheduler.start()

    state = (await storage.target_states())["market-data-service"]
    assert first_cycle_complete is True
    assert scheduler.running is True
    assert scheduler.last_cycle_at is not None
    assert state["effective_status"] == "healthy"
    assert state["latency_ms"] == 1.25
  finally:
    await scheduler.stop()
    await storage.close()
