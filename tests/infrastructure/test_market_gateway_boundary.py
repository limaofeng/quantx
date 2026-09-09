"""Process/package and route ownership after Gateway extraction."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from quantx_infrastructure.services import market_lease_reader as leases

ROOT = Path(__file__).resolve().parents[2]


def test_gateway_and_data_api_import_without_business_api_package():
  code = """
import importlib.abc
import json
import sys
sys.path = json.loads(sys.argv[1]) + sys.path
class RejectBusinessAPI(importlib.abc.MetaPathFinder):
  def find_spec(self, fullname, path=None, target=None):
    if fullname == "quantx_api" or fullname.startswith("quantx_api."):
      raise AssertionError("business API import: " + fullname)
sys.meta_path.insert(0, RejectBusinessAPI())
from quantx_market_data.gateway import app as gateway
from quantx_market_data.api import app as history
assert gateway is not history
"""
  result = subprocess.run(
    [sys.executable, "-c", code, json.dumps(sys.path)],
    cwd=ROOT,
    capture_output=True,
    text=True,
    timeout=30,
  )
  assert result.returncode == 0, result.stderr


def test_history_routes_are_served_only_by_data_api():
  from fastapi.testclient import TestClient
  from quantx_market_data.api import app as history
  from quantx_market_data.gateway import app as gateway
  from starlette.websockets import WebSocketDisconnect

  history_client, gateway_client = TestClient(history), TestClient(gateway)
  for path in (
    "/market-data/v1/history/unknown",
    "/market-data/v1/reference/000001.SZ?as_of=2026-09-01",
    "/market-data/v1/calendar/2026",
  ):
    assert history_client.get(path).status_code == 403
    assert gateway_client.get(path).status_code == 404
  for path, code in (("/ws/agent/market", 4406), ("/market-data/v1/stream", 1008)):
    with pytest.raises(WebSocketDisconnect) as error:
      with gateway_client.websocket_connect(path):
        pass
    assert error.value.code == code
    with pytest.raises(WebSocketDisconnect) as error:
      with history_client.websocket_connect(path):
        pass
    assert error.value.code == 1000


@pytest.mark.parametrize(
  "value", [None, "broken", "[]", "null", "{}", '{"device_id":"other"}']
)
async def test_lease_reader_rejects_absent_or_invalid_shared_identity(
  monkeypatch, value
):
  redis = type("Redis", (), {"get": AsyncMock(return_value=value)})()
  monkeypatch.setattr(leases.redis_pubsub, "get_redis", AsyncMock(return_value=redis))
  reader = leases.MarketLeaseReader()
  assert await reader.market_lease("device") is None
  diagnostic = await reader.market_lease_diagnostic("device")
  assert diagnostic["controlSessionRegistered"] is False
  assert diagnostic["redisLeaseValid"] is False


async def test_lease_reader_checks_exact_cross_process_generation(monkeypatch):
  redis = type(
    "Redis",
    (),
    {
      "get": AsyncMock(
        return_value=json.dumps(
          {
            "device_id": "device",
            "api_instance_id": "api",
            "agent_session_id": "session",
          }
        )
      )
    },
  )()
  monkeypatch.setattr(leases.redis_pubsub, "get_redis", AsyncMock(return_value=redis))
  reader = leases.MarketLeaseReader()
  assert await reader.is_market_session(
    leases.MarketSessionLease("device", "api", "session")
  )
  assert not await reader.is_market_session(
    leases.MarketSessionLease("device", "other", "session")
  )
