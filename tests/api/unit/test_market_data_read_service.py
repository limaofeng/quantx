from datetime import datetime, timezone

import pytest
from quantx_api import market_data_read_service as module
from quantx_infrastructure.models.kline import KLine
from quantx_infrastructure.models.tick import Tick


def _kline(time, close):
  return KLine(
    stock_code="600000.SH",
    period="1m",
    time=time,
    open=close,
    high=close,
    low=close,
    close=close,
    pre_close=10,
    volume=100,
    amount=1000,
    settelement_price=0,
    open_interest=0,
    suspend_flag=0,
  )


@pytest.mark.asyncio
async def test_api_market_read_merges_engine_warm_data_over_database(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  first = datetime(2026, 7, 26, 1, 30, tzinfo=timezone.utc)
  second = datetime(2026, 7, 26, 1, 31, tzinfo=timezone.utc)

  class FakeHistorical:
    async def get_kline_data(self, **_):
      return [_kline(first, 10.1)]

  async def query(operation, _payload):
    assert operation == "warm_klines"
    return {
      "items": [
        vars(_kline(first, 10.2)),
        vars(_kline(second, 10.3)),
      ]
    }

  service = module.ApiMarketDataReadService()
  service.historical = FakeHistorical()
  monkeypatch.setattr(module.runtime_market_query_bridge, "query", query)

  values = await service.get_klines(
    stock_code="600000.SH",
    period="1m",
    order="asc",
  )

  assert [item.close for item in values] == [10.2, 10.3]


@pytest.mark.asyncio
async def test_api_latest_price_reads_engine_quote_cache_without_influx(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  tick = Tick(
    stock_code="600000.SH",
    period="tick",
    time=datetime(2026, 7, 26, 1, 30, tzinfo=timezone.utc),
    last_price=10.5,
  )

  async def get_ticks(codes):
    assert codes == ["600000.SH"]
    return [tick]

  service = module.ApiMarketDataReadService()
  monkeypatch.setattr(module.latest_market_quote_cache, "get_ticks", get_ticks)

  assert await service.get_latest_price("600000.SH") is tick
