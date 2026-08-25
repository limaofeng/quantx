import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
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


def _tick(
  time: datetime,
  *,
  transaction_num: int,
  volume: float,
  amount: float,
  source_time_ms: int | None = None,
  tick_ordinal: int | None = None,
) -> Tick:
  values = {
    "stock_code": "600000.SH",
    "period": "tick",
    "time": time,
    "last_price": 10.5,
    "open": 10.0,
    "high": 10.8,
    "low": 9.9,
    "last_close": 10.1,
    "amount": amount,
    "volume": volume,
    "pvolume": volume,
    "tickvol": 1.0,
    "stock_status": 0,
    "open_int": 0,
    "last_settlement_price": 0.0,
    "settlement_price": 0.0,
    "transaction_num": transaction_num,
    "ask_price": [10.6, 10.7],
    "bid_price": [10.4, 10.3],
    "ask_vol": [100.0, 200.0],
    "bid_vol": [120.0, 220.0],
  }
  if source_time_ms is not None:
    values["source_time_ms"] = source_time_ms
  if tick_ordinal is not None:
    values["tick_ordinal"] = tick_ordinal
  return Tick(**values)


@pytest.mark.asyncio
async def test_api_market_read_starts_historical_and_warm_kline_reads_concurrently(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  historical_started = asyncio.Event()
  warm_started = asyncio.Event()

  class FakeHistorical:
    async def get_kline_data(self, **_):
      historical_started.set()
      await asyncio.wait_for(warm_started.wait(), timeout=1)
      return []

  async def query(operation, _payload):
    assert operation == "warm_klines"
    warm_started.set()
    await asyncio.wait_for(historical_started.wait(), timeout=1)
    return {"items": []}

  service = module.ApiMarketDataReadService()
  service.historical = FakeHistorical()
  monkeypatch.setattr(module.runtime_market_query_bridge, "query", query)

  assert await service.get_klines(stock_code="600000.SH", period="1m") == []


@pytest.mark.asyncio
async def test_api_market_read_starts_historical_and_warm_tick_reads_concurrently(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  historical_started = asyncio.Event()
  warm_started = asyncio.Event()

  class FakeHistorical:
    async def get_tick_data(self, **_):
      historical_started.set()
      await asyncio.wait_for(warm_started.wait(), timeout=1)
      return []

  async def query(operation, _payload):
    assert operation == "warm_ticks"
    warm_started.set()
    await asyncio.wait_for(historical_started.wait(), timeout=1)
    return {"items": []}

  service = module.ApiMarketDataReadService()
  service.historical = FakeHistorical()
  monkeypatch.setattr(module.runtime_market_query_bridge, "query", query)

  assert await service.get_ticks(stock_code="600000.SH") == []


@pytest.mark.asyncio
async def test_api_market_read_keeps_historical_data_when_warm_query_fails(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  timestamp = datetime(2026, 8, 20, 1, 30, tzinfo=timezone.utc)

  class FakeHistorical:
    async def get_kline_data(self, **_):
      return [_kline(timestamp, 10.1)]

  async def query(_operation, _payload):
    raise RuntimeError("engine unavailable")

  service = module.ApiMarketDataReadService()
  service.historical = FakeHistorical()
  monkeypatch.setattr(module.runtime_market_query_bridge, "query", query)

  values = await service.get_klines(
    stock_code="600000.SH",
    period="1m",
    order="asc",
  )

  assert [item.close for item in values] == [10.1]


@pytest.mark.asyncio
async def test_api_market_read_still_propagates_historical_query_failure(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  failure = RuntimeError("influx unavailable")

  class FakeHistorical:
    async def get_kline_data(self, **_):
      raise failure

  async def query(_operation, _payload):
    return {"items": []}

  service = module.ApiMarketDataReadService()
  service.historical = FakeHistorical()
  monkeypatch.setattr(module.runtime_market_query_bridge, "query", query)

  with pytest.raises(RuntimeError) as exc_info:
    await service.get_klines(stock_code="600000.SH", period="1m")

  assert exc_info.value is failure


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
async def test_api_tick_merge_preserves_same_millisecond_history_and_deduplicates_warm(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  raw_time = datetime(2026, 8, 19, 9, 30)
  source_time_ms = int(
    datetime(2026, 8, 19, 1, 30, tzinfo=timezone.utc).timestamp() * 1000
  )
  first = _tick(
    raw_time,
    transaction_num=100,
    volume=1000,
    amount=10_000,
    source_time_ms=source_time_ms,
    tick_ordinal=0,
  )
  second = _tick(
    raw_time + timedelta(microseconds=1),
    transaction_num=101,
    volume=1001,
    amount=10_010,
    source_time_ms=source_time_ms,
    tick_ordinal=1,
  )

  class FakeHistorical:
    async def get_tick_data(self, **_):
      return [first, second]

  async def query(operation, _payload):
    assert operation == "warm_ticks"
    return {
      "items": [
        vars(
          _tick(
            raw_time,
            transaction_num=101,
            volume=1001,
            amount=10_010,
          )
        )
      ]
    }

  service = module.ApiMarketDataReadService()
  service.historical = FakeHistorical()
  monkeypatch.setattr(module.runtime_market_query_bridge, "query", query)

  values = await service.get_ticks(
    stock_code="600000.SH",
    order="asc",
    limit=None,
  )

  assert [item.transaction_num for item in values] == [100, 101]
  assert [item.source_time_ms for item in values] == [source_time_ms] * 2
  assert [item.tick_ordinal for item in values] == [0, 1]
  assert [item.time for item in values] == [
    raw_time,
    raw_time + timedelta(microseconds=1),
  ]


@pytest.mark.asyncio
async def test_api_tick_merge_keeps_new_warm_snapshot_and_applies_order_and_limit(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  raw_time = datetime(2026, 8, 19, 9, 30)
  source_time_ms = int(
    datetime(2026, 8, 19, 1, 30, tzinfo=timezone.utc).timestamp() * 1000
  )
  historical = [
    _tick(
      raw_time,
      transaction_num=100,
      volume=1000,
      amount=10_000,
      source_time_ms=source_time_ms,
      tick_ordinal=0,
    ),
    _tick(
      raw_time + timedelta(microseconds=1),
      transaction_num=101,
      volume=1001,
      amount=10_010,
      source_time_ms=source_time_ms,
      tick_ordinal=1,
    ),
  ]

  class FakeHistorical:
    async def get_tick_data(self, **_):
      return historical

  async def query(operation, _payload):
    assert operation == "warm_ticks"
    return {
      "items": [
        vars(
          _tick(
            raw_time,
            transaction_num=102,
            volume=1002,
            amount=10_020,
          )
        )
      ]
    }

  service = module.ApiMarketDataReadService()
  service.historical = FakeHistorical()
  monkeypatch.setattr(module.runtime_market_query_bridge, "query", query)

  values = await service.get_ticks(
    stock_code="600000.SH",
    order="desc",
    limit=2,
  )

  assert [item.transaction_num for item in values] == [102, 101]
  assert [item.source_time_ms for item in values] == [source_time_ms] * 2
  assert [item.tick_ordinal for item in values] == [2, 1]
  assert [item.time for item in values] == [
    raw_time + timedelta(microseconds=2),
    raw_time + timedelta(microseconds=1),
  ]


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


@pytest.mark.asyncio
async def test_market_index_snapshots_batch_cache_and_daily_reads_once(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  tick = Tick(
    stock_code="000001.SH",
    period="tick",
    time=datetime(2026, 8, 26, 1, 30, tzinfo=timezone.utc),
    last_price=3888.0,
  )
  cache_calls = []

  async def get_ticks(codes):
    cache_calls.append(tuple(codes))
    return [tick]

  class FakeKlineRepository:
    def __init__(self) -> None:
      self.calls = []

    def find_daily_batch(self, stock_codes, start, end, *, use_cache):
      self.calls.append((tuple(stock_codes), start, end, use_cache))
      return {
        "000001.SH": pd.DataFrame(
          [
            {
              "stock_code": "000001.SH",
              "period": "1d",
              "time": datetime(2026, 8, 25, tzinfo=timezone.utc),
              "open": 3800.0,
              "high": 3900.0,
              "low": 3790.0,
              "close": 3880.0,
              "pre_close": 3810.0,
              "volume": 100.0,
              "amount": 1_000.0,
            }
          ]
        )
      }

  repository = FakeKlineRepository()
  service = module.ApiMarketDataReadService()
  service.historical = SimpleNamespace(kline_repo=repository)
  monkeypatch.setattr(module.latest_market_quote_cache, "get_ticks", get_ticks)

  rows = await service.get_market_index_snapshots(
    ["000001.sh", "399001.SZ", "000001.SH"]
  )

  assert cache_calls == [("000001.SH", "399001.SZ")]
  assert len(repository.calls) == 1
  assert repository.calls[0][0] == ("000001.SH", "399001.SZ")
  assert repository.calls[0][3] is False
  assert rows[0][0] == "000001.SH"
  assert rows[0][1] is tick
  assert rows[0][2] is not None and rows[0][2].close == 3880.0
  assert rows[1] == ("399001.SZ", None, None)
