import asyncio

import pytest
from quantx_api import market_data_service


class FakeRedis:
  def __init__(self, result: bool = True) -> None:
    self.result = result

  async def ping(self) -> bool:
    return self.result


@pytest.mark.asyncio
async def test_market_data_service_ready_checks_redis(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  async def fake_redis() -> FakeRedis:
    return FakeRedis()

  monkeypatch.setattr(market_data_service.market_stream_store, "redis", fake_redis)

  response = await market_data_service.health_ready()

  assert response.status_code == 200
  assert response.body == (
    b'{"status":"ready","component":"market-data-service",'
    b'"dependencies":{"redis":"ready"}}'
  )


@pytest.mark.asyncio
async def test_market_data_service_not_ready_when_redis_fails(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  async def fake_redis() -> FakeRedis:
    raise ConnectionError("Redis is unavailable")

  monkeypatch.setattr(market_data_service.market_stream_store, "redis", fake_redis)

  response = await market_data_service.health_ready()

  assert response.status_code == 503
  assert b'"status":"not_ready"' in response.body
  assert b'"redis":"unavailable"' in response.body


@pytest.mark.asyncio
async def test_market_data_service_not_ready_when_redis_ping_times_out(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  class SlowRedis:
    async def ping(self) -> bool:
      await asyncio.sleep(1)
      return True

  async def fake_redis() -> SlowRedis:
    return SlowRedis()

  monkeypatch.setattr(market_data_service.market_stream_store, "redis", fake_redis)
  monkeypatch.setattr(
    market_data_service,
    "MARKET_DATA_SERVICE_READINESS_TIMEOUT_SECONDS",
    0.001,
  )

  response = await market_data_service.health_ready()

  assert response.status_code == 503
  assert b'"error":"TimeoutError"' in response.body
