import pytest
from quantx_api.gqlapi.schemas import realtime_schema
from quantx_api.gqlapi.schemas.realtime_schema import RealtimeSubscription


@pytest.mark.asyncio
async def test_system_alerts_emit_real_component_failure_and_recovery(monkeypatch):
  snapshots = [
    {"engine": {"status": "stale"}},
    {"engine": {"status": "ready"}},
  ]

  async def fake_component_status():
    return snapshots.pop(0)

  async def no_sleep(_seconds):
    return None

  monkeypatch.setattr(realtime_schema, "component_status", fake_component_status)
  monkeypatch.setattr(realtime_schema, "required_components", lambda: ("engine",))
  monkeypatch.setattr(realtime_schema.asyncio, "sleep", no_sleep)

  stream = RealtimeSubscription().system_alerts()
  failed = await stream.__anext__()
  recovered = await stream.__anext__()
  await stream.aclose()

  assert failed.source == "engine"
  assert failed.severity == "critical"
  assert failed.resolved is False
  assert "stale" in failed.message
  assert recovered.source == "engine"
  assert recovered.severity == "info"
  assert recovered.resolved is True
  assert "ready" in recovered.message


@pytest.mark.asyncio
async def test_system_alerts_respect_severity_filter(monkeypatch):
  async def fake_component_status():
    return {"worker": {"status": "offline"}}

  monkeypatch.setattr(realtime_schema, "component_status", fake_component_status)
  monkeypatch.setattr(realtime_schema, "required_components", lambda: ("worker",))

  stream = RealtimeSubscription().system_alerts(severity_level="error")
  alert = await stream.__anext__()
  await stream.aclose()

  assert alert.source == "worker"
  assert alert.severity == "error"
