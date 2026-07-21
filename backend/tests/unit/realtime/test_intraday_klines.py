import asyncio
import logging
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from core.data import realtime as realtime_module
from core.data.market_data_service import MarketDataService
from core.data.realtime import RealtimeDataAdapter
from core.realtime_manager import RealTimeDataManager
from core.utils import time_utils
from models.kline import KLine
from models.tick import Tick


def _xt_ms(value: datetime) -> int:
  return int(time_utils.to_utc(value).timestamp() * 1000)


def _kline(stock_code: str, minute: datetime, close: float) -> KLine:
  return KLine(
    stock_code=stock_code,
    period="1m",
    time=minute,
    open=close,
    high=close,
    low=close,
    close=close,
    pre_close=close - 0.01,
    volume=100,
    amount=close * 100,
    settelement_price=0.0,
    open_interest=0,
    suspend_flag=0,
  )


def _tick(
  stock_code: str,
  value_time: datetime,
  price: float,
  volume: float,
  amount: float,
  tickvol: float,
) -> Tick:
  return Tick(
    stock_code=stock_code,
    period="tick",
    time=value_time,
    last_price=price,
    open=price,
    high=price,
    low=price,
    last_close=93.65,
    amount=amount,
    volume=volume,
    pvolume=volume,
    tickvol=tickvol,
    stock_status=0,
    open_int=15,
    last_settlement_price=0.0,
    settlement_price=0.0,
    transaction_num=1,
    ask_price=[0.0] * 5,
    bid_price=[0.0] * 5,
    ask_vol=[0.0] * 5,
    bid_vol=[0.0] * 5,
  )


class FakeTradingTimeService:
  async def get_previous_trading_day(self, market="SH", from_date=None):
    return from_date - timedelta(days=1)


class FakeDividFactorService:
  def __init__(self, factors=None):
    self.factors = list(factors or [])

  async def get_divid_factors(self, **kwargs):
    return self.factors


@pytest.mark.asyncio
async def test_realtime_manager_start_is_idempotent_for_same_loop():
  manager = RealTimeDataManager()
  calls = []

  class FakeSubscriptionManager:
    def set_main_loop(self, loop=None):
      calls.append(loop)

    async def unsubscribe_all(self, subscriber_id):
      return True

  manager.subscription_manager = FakeSubscriptionManager()

  await manager.start()
  await manager.start()

  assert len(calls) == 1


@pytest.mark.asyncio
async def test_subscribe_kline_rolls_back_when_underlying_subscription_fails():
  manager = RealTimeDataManager()

  class FailingSubscriptionManager:
    async def subscribe(self, **kwargs):
      return False

  manager.subscription_manager = FailingSubscriptionManager()

  async_iter = manager.subscribe_kline("600900.SH", "1m")
  try:
    with pytest.raises(RuntimeError, match="底层K线订阅失败"):
      await async_iter.__anext__()
  finally:
    await async_iter.aclose()

  assert manager.kline_subscribers == {}


@pytest.mark.asyncio
async def test_previous_trading_day_1m_query_does_not_schedule_download_when_cache_empty(
  monkeypatch,
):
  stock_code = "601318.SH"
  adapter = RealtimeDataAdapter()
  adapter.is_connected = True

  now = datetime(2026, 6, 2, 8, 30, 0)
  monkeypatch.setattr(realtime_module.time_utils, "now", lambda: now)

  class FakeXTDataManager:
    def get_market_data(self, **kwargs):
      assert kwargs["period"] == "1m"
      assert kwargs["start_time"] == "20260601000000"
      assert kwargs["end_time"] == "20260601235959"
      return {stock_code: pd.DataFrame()}

  from miniqmt.data import data_manager as data_manager_module

  monkeypatch.setattr(data_manager_module, "xt_data_manager", FakeXTDataManager())

  from core.data import intraday_warm_cache as warm_cache_module

  ensured = []

  async def fake_ensure_symbol(stock_code, source="chart"):
    ensured.append((stock_code, source))

  monkeypatch.setattr(
    warm_cache_module.intraday_warm_cache, "ensure_symbol", fake_ensure_symbol
  )
  monkeypatch.setattr(
    warm_cache_module.intraday_warm_cache, "get_klines", lambda *args, **kwargs: []
  )

  def fake_schedule(**kwargs):
    raise AssertionError("query path must not schedule 1m downloads")

  adapter._schedule_kline_gap_download = fake_schedule

  klines = await adapter.get_klines(
    instrument_code=stock_code,
    period="1m",
    start_time=datetime(2026, 6, 1, 0, 0),
    end_time=datetime(2026, 6, 1, 23, 59, 59),
    limit=None,
    order="asc",
  )

  assert klines == []
  assert ensured == [(stock_code, "chart_query")]


@pytest.mark.asyncio
async def test_realtime_1m_query_returns_cached_data_without_scheduling_gap(
  monkeypatch,
):
  stock_code = "002594.SZ"
  adapter = RealtimeDataAdapter()
  adapter.is_connected = True

  now = datetime(2026, 6, 1, 13, 34, 15)
  monkeypatch.setattr(realtime_module.time_utils, "now", lambda: now)

  class FakeXTDataManager:
    def get_market_data(self, **kwargs):
      assert kwargs["period"] == "1m"
      return {
        stock_code: pd.DataFrame(
          [
            {
              "time": _xt_ms(datetime(2026, 6, 1, 9, 30)),
              "open": 96.18,
              "high": 96.18,
              "low": 96.18,
              "close": 96.18,
              "preClose": 96.18,
              "volume": 6109,
              "amount": 58756362.0,
            },
            {
              "time": _xt_ms(datetime(2026, 6, 1, 10, 36)),
              "open": 94.81,
              "high": 94.82,
              "low": 94.78,
              "close": 94.78,
              "preClose": 94.81,
              "volume": 1645,
              "amount": 15592560.0,
            },
          ]
        )
      }

  from miniqmt.data import data_manager as data_manager_module

  monkeypatch.setattr(data_manager_module, "xt_data_manager", FakeXTDataManager())

  from core.data import intraday_warm_cache as warm_cache_module

  ensured = []

  async def fake_ensure_symbol(stock_code, source="chart"):
    ensured.append((stock_code, source))

  monkeypatch.setattr(
    warm_cache_module.intraday_warm_cache, "ensure_symbol", fake_ensure_symbol
  )
  monkeypatch.setattr(
    warm_cache_module.intraday_warm_cache, "get_klines", lambda *args, **kwargs: []
  )
  scheduled = {}

  def fake_schedule(**kwargs):
    scheduled.update(kwargs)

  adapter._schedule_kline_gap_download = fake_schedule

  klines = await adapter.get_realtime_klines_range(
    instrument_code=stock_code,
    period="1m",
    start_time=datetime(2026, 6, 1, 0, 0),
    end_time=now,
    limit=None,
    order="asc",
  )

  assert [kline.time for kline in klines] == [
    datetime(2026, 6, 1, 9, 30),
    datetime(2026, 6, 1, 10, 36),
  ]
  assert scheduled == {}
  assert ensured == [(stock_code, "chart_query")]


@pytest.mark.asyncio
async def test_realtime_tick_query_merges_warm_cache_without_scheduling_download(
  monkeypatch,
):
  stock_code = "600900.SH"
  adapter = RealtimeDataAdapter()
  adapter.is_connected = True

  now = datetime(2026, 6, 1, 10, 0, 0)
  monkeypatch.setattr(realtime_module.time_utils, "now", lambda: now)

  class FakeXTDataManager:
    def get_market_data(self, **kwargs):
      assert kwargs["period"] == "tick"
      return {
        stock_code: pd.DataFrame(
          [
            {
              "time": _xt_ms(datetime(2026, 6, 1, 9, 31, 0)),
              "lastPrice": 27.8,
              "open": 27.8,
              "high": 27.8,
              "low": 27.8,
              "lastClose": 27.89,
              "volume": 100,
              "amount": 2780.0,
              "tickvol": 100,
            }
          ]
        )
      }

    def get_full_tick(self, stock_list):
      return {
        stock_code: {
          "time": _xt_ms(datetime(2026, 6, 1, 9, 33, 0)),
          "lastPrice": 27.82,
          "open": 27.8,
          "high": 27.82,
          "low": 27.78,
          "lastClose": 27.89,
          "volume": 300,
          "amount": 8346.0,
          "tickvol": 100,
        }
      }

  from miniqmt.data import data_manager as data_manager_module

  monkeypatch.setattr(data_manager_module, "xt_data_manager", FakeXTDataManager())

  from core.data import intraday_warm_cache as warm_cache_module

  warm_tick = _tick(
    stock_code=stock_code,
    value_time=datetime(2026, 6, 1, 9, 32, 0),
    price=27.81,
    volume=200,
    amount=5562.0,
    tickvol=100,
  )
  ensured = []

  async def fake_ensure_symbol(stock_code, source="chart"):
    ensured.append((stock_code, source))

  monkeypatch.setattr(
    warm_cache_module.intraday_warm_cache, "ensure_symbol", fake_ensure_symbol
  )
  monkeypatch.setattr(
    warm_cache_module.intraday_warm_cache,
    "get_ticks",
    lambda *args, **kwargs: [warm_tick],
  )

  def fake_schedule(**kwargs):
    raise AssertionError("query path must not schedule tick downloads")

  adapter._schedule_tick_gap_download = fake_schedule

  ticks = await adapter.get_realtime_ticks_range(
    instrument_code=stock_code,
    start_time=datetime(2026, 6, 1, 9, 30),
    end_time=now,
    limit=None,
    order="asc",
  )

  assert [tick.time for tick in ticks] == [
    datetime(2026, 6, 1, 9, 31),
    datetime(2026, 6, 1, 9, 32),
    datetime(2026, 6, 1, 9, 33),
  ]
  assert ensured == [(stock_code, "tick_query")]


@pytest.mark.asyncio
async def test_warm_cache_initializes_symbol_once_per_trading_day(monkeypatch):
  from core.data import intraday_warm_cache as warm_cache_module

  service = warm_cache_module.IntradayWarmCacheService()
  subscribe_calls = []
  download_calls = []

  class FakeSubscriptionManager:
    def set_main_loop(self, loop=None):
      pass

    async def subscribe(self, **kwargs):
      subscribe_calls.append((kwargs["stock_code"], kwargs["period"]))
      return True

    async def unsubscribe(self, *args, **kwargs):
      return True

    async def unsubscribe_all(self, *args, **kwargs):
      return True

  service.subscription_manager = FakeSubscriptionManager()
  monkeypatch.setattr(
    warm_cache_module.time_utils, "today", lambda: date(2026, 6, 1)
  )

  async def fake_run_initial_download(stock_code, trading_date):
    download_calls.append((stock_code, trading_date))

  monkeypatch.setattr(service, "_run_initial_download", fake_run_initial_download)

  await service.ensure_symbol("600900.SH", source="watchlist")
  await service.ensure_symbol("600900.SH", source="watchlist")
  await asyncio.sleep(0)

  assert subscribe_calls == [("600900.SH", "tick"), ("600900.SH", "1m")]
  assert download_calls == [("600900.SH", date(2026, 6, 1))]


@pytest.mark.asyncio
async def test_warm_cache_query_sources_do_not_start_initial_download(monkeypatch):
  from core.data import intraday_warm_cache as warm_cache_module

  service = warm_cache_module.IntradayWarmCacheService()
  subscribe_calls = []
  download_calls = []

  class FakeSubscriptionManager:
    def set_main_loop(self, loop=None):
      pass

    async def subscribe(self, **kwargs):
      subscribe_calls.append((kwargs["stock_code"], kwargs["period"]))
      return True

    async def unsubscribe(self, *args, **kwargs):
      return True

    async def unsubscribe_all(self, *args, **kwargs):
      return True

  service.subscription_manager = FakeSubscriptionManager()
  monkeypatch.setattr(
    warm_cache_module.time_utils, "today", lambda: date(2026, 6, 1)
  )

  async def fake_run_initial_download(stock_code, trading_date):
    download_calls.append((stock_code, trading_date))

  monkeypatch.setattr(service, "_run_initial_download", fake_run_initial_download)

  await service.ensure_symbol("600900.SH", source="chart_query")
  await service.ensure_symbol("600900.SH", source="tick_query")
  await asyncio.sleep(0)

  assert subscribe_calls == [("600900.SH", "tick"), ("600900.SH", "1m")]
  assert download_calls == []


@pytest.mark.asyncio
async def test_publish_kline_backfill_pushes_missing_bars_in_order():
  manager = RealTimeDataManager()
  queue = manager._create_optimized_queue()
  manager.kline_subscribers["002594.SZ_1m"] = {queue}

  delivered = await manager.publish_kline_backfill(
    "002594.SZ",
    "1m",
    [
      _kline("002594.SZ", datetime(2026, 6, 1, 10, 38), 94.85),
      _kline("002594.SZ", datetime(2026, 6, 1, 10, 37), 94.93),
    ],
  )

  first = queue.get_nowait()
  second = queue.get_nowait()

  assert delivered == 2
  assert first.time == datetime(2026, 6, 1, 10, 37)
  assert second.time == datetime(2026, 6, 1, 10, 38)


@pytest.mark.asyncio
async def test_warm_cache_initial_tick_push_uses_initialization_log(caplog):
  manager = RealTimeDataManager()
  queue = manager._create_optimized_queue()
  manager.tick_subscribers["002594.SZ"] = {queue}

  with caplog.at_level(logging.INFO, logger="core.realtime_manager"):
    delivered = await manager.publish_tick_backfill(
      "002594.SZ",
      [
        _tick(
          "002594.SZ",
          datetime(2026, 6, 1, 9, 31),
          93.9,
          100,
          9390,
          100,
        )
      ],
      source="warm_cache_initial",
    )

  assert delivered == 1
  assert queue.get_nowait().time == datetime(2026, 6, 1, 9, 31)
  assert "推送tick热缓存初始化数据" in caplog.text
  assert "推送tick补全数据" not in caplog.text


@pytest.mark.asyncio
async def test_tick_pre_close_uses_native_when_database_previous_close_missing(
  monkeypatch,
):
  stock_code = "603118.SH"
  manager = RealTimeDataManager()
  manager.previous_daily_close_cache = {}
  manager.trading_time_service = FakeTradingTimeService()
  manager.divid_factor_service = FakeDividFactorService()

  class FakeHistoricalMarketDataService:
    async def get_kline_data(self, **kwargs):
      assert kwargs["stock_code"] == stock_code
      assert kwargs["period"] == "1d"
      assert kwargs["start_time"].date() == date(2026, 6, 3)
      return []

  monkeypatch.setattr(
    manager, "historical_market_data_service", FakeHistoricalMarketDataService()
  )

  tick = _tick(
    stock_code=stock_code,
    value_time=datetime(2026, 6, 4, 9, 35),
    price=13.31,
    volume=1000,
    amount=13310,
    tickvol=20,
  )
  tick.last_close = 12.68

  normalized = await manager._normalize_tick_pre_close(stock_code, tick)

  assert normalized.last_close == 12.68


@pytest.mark.asyncio
async def test_tick_pre_close_caches_native_fallback_and_logs_once(
  monkeypatch,
  caplog,
):
  stock_code = "603118.SH"
  manager = RealTimeDataManager()
  manager.previous_daily_close_cache = {}
  manager.trading_time_service = FakeTradingTimeService()
  manager.divid_factor_service = FakeDividFactorService()
  calls = {"count": 0}

  class FakeHistoricalMarketDataService:
    async def get_kline_data(self, **kwargs):
      calls["count"] += 1
      assert kwargs["stock_code"] == stock_code
      assert kwargs["period"] == "1d"
      return []

  monkeypatch.setattr(
    manager, "historical_market_data_service", FakeHistoricalMarketDataService()
  )

  first_tick = _tick(
    stock_code=stock_code,
    value_time=datetime(2026, 6, 4, 9, 35),
    price=13.31,
    volume=1000,
    amount=13310,
    tickvol=20,
  )
  first_tick.last_close = 12.68
  second_tick = _tick(
    stock_code=stock_code,
    value_time=datetime(2026, 6, 4, 9, 36),
    price=13.32,
    volume=1200,
    amount=15984,
    tickvol=10,
  )
  second_tick.last_close = 12.68

  with caplog.at_level(logging.INFO, logger="core.realtime_manager"):
    first_normalized = await manager._normalize_tick_pre_close(
      stock_code, first_tick
    )
    second_normalized = await manager._normalize_tick_pre_close(
      stock_code, second_tick
    )

  cache_key = manager._previous_daily_close_cache_key(stock_code, first_tick.time)
  assert calls["count"] == 1
  assert first_normalized.last_close == 12.68
  assert second_normalized.last_close == 12.68
  assert manager.previous_daily_close_cache[cache_key]["source"] == "tick"
  assert caplog.text.count("昨日收盘价使用tick兜底") == 1


@pytest.mark.asyncio
async def test_tick_pre_close_uses_database_previous_daily_close(monkeypatch):
  stock_code = "603118.SH"
  manager = RealTimeDataManager()
  manager.previous_daily_close_cache = {}
  manager.trading_time_service = FakeTradingTimeService()
  manager.divid_factor_service = FakeDividFactorService()

  class FakeHistoricalMarketDataService:
    async def get_kline_data(self, **kwargs):
      assert kwargs["stock_code"] == stock_code
      assert kwargs["period"] == "1d"
      assert kwargs["start_time"].date() == date(2026, 6, 3)
      return [
        KLine(
          stock_code=stock_code,
          period="1d",
          time=datetime(2026, 6, 4, 0, 0),
          open=13.24,
          high=13.37,
          low=13.23,
          close=13.31,
          pre_close=12.98,
          volume=100,
          amount=1331.0,
          settelement_price=0.0,
          open_interest=0,
          suspend_flag=0,
        ),
        KLine(
          stock_code=stock_code,
          period="1d",
          time=datetime(2026, 6, 3, 0, 0),
          open=13.17,
          high=13.49,
          low=12.81,
          close=12.98,
          pre_close=12.68,
          volume=100,
          amount=1298.0,
          settelement_price=0.0,
          open_interest=0,
          suspend_flag=0,
        ),
      ]

  monkeypatch.setattr(
    manager, "historical_market_data_service", FakeHistoricalMarketDataService()
  )

  tick = _tick(
    stock_code=stock_code,
    value_time=datetime(2026, 6, 4, 9, 35),
    price=13.31,
    volume=1000,
    amount=13310,
    tickvol=20,
  )
  tick.last_close = 12.68

  normalized = await manager._normalize_tick_pre_close(stock_code, tick)

  assert normalized.last_close == 12.98


@pytest.mark.asyncio
async def test_tick_pre_close_uses_database_yesterday_close_not_today_pre_close(
  monkeypatch,
):
  stock_code = "002216.SZ"
  manager = RealTimeDataManager()
  manager.previous_daily_close_cache = {}
  manager.trading_time_service = FakeTradingTimeService()
  manager.divid_factor_service = FakeDividFactorService(
    [SimpleNamespace(time=datetime(2026, 6, 5), dr=1.047894)]
  )

  class FakeHistoricalMarketDataService:
    async def get_kline_data(self, **kwargs):
      assert kwargs["stock_code"] == stock_code
      assert kwargs["period"] == "1d"
      assert kwargs["start_time"].date() == date(2026, 6, 4)
      return [
        KLine(
          stock_code=stock_code,
          period="1d",
          time=datetime(2026, 6, 5, 0, 0),
          open=12.26,
          high=12.66,
          low=12.13,
          close=12.34,
          pre_close=12.12,
          volume=100,
          amount=1234.0,
          settelement_price=0.0,
          open_interest=0,
          suspend_flag=0,
        ),
        KLine(
          stock_code=stock_code,
          period="1d",
          time=datetime(2026, 6, 4, 0, 0),
          open=12.8,
          high=12.93,
          low=12.54,
          close=12.69,
          pre_close=12.8,
          volume=100,
          amount=1269.0,
          settelement_price=0.0,
          open_interest=0,
          suspend_flag=0,
        ),
      ]

  monkeypatch.setattr(
    manager, "historical_market_data_service", FakeHistoricalMarketDataService()
  )

  tick = _tick(
    stock_code=stock_code,
    value_time=datetime(2026, 6, 5, 9, 35),
    price=12.28,
    volume=1000,
    amount=12280,
    tickvol=20,
  )
  tick.last_close = 12.12

  normalized = await manager._normalize_tick_pre_close(stock_code, tick)

  assert normalized.last_close == pytest.approx(12.69 / 1.047894)


@pytest.mark.asyncio
async def test_tick_pre_close_does_not_use_stale_older_daily_close(monkeypatch):
  stock_code = "605499.SH"
  manager = RealTimeDataManager()
  manager.previous_daily_close_cache = {}
  manager.trading_time_service = FakeTradingTimeService()
  manager.divid_factor_service = FakeDividFactorService()

  class FakeHistoricalMarketDataService:
    async def get_kline_data(self, **kwargs):
      assert kwargs["stock_code"] == stock_code
      assert kwargs["period"] == "1d"
      assert kwargs["start_time"].date() == date(2026, 6, 4)
      return [
        KLine(
          stock_code=stock_code,
          period="1d",
          time=datetime(2026, 6, 3, 0, 0),
          open=148.35,
          high=148.39,
          low=142.5,
          close=143.96,
          pre_close=149.6,
          volume=100,
          amount=14396.0,
          settelement_price=0.0,
          open_interest=0,
          suspend_flag=0,
        )
      ]

  monkeypatch.setattr(
    manager, "historical_market_data_service", FakeHistoricalMarketDataService()
  )

  tick = _tick(
    stock_code=stock_code,
    value_time=datetime(2026, 6, 5, 9, 35),
    price=138.53,
    volume=1000,
    amount=138530,
    tickvol=20,
  )
  tick.last_close = 139.43

  normalized = await manager._normalize_tick_pre_close(stock_code, tick)

  assert normalized.last_close == 139.43


@pytest.mark.asyncio
async def test_market_data_service_tick_pre_close_uses_native_when_database_missing():
  stock_code = "603118.SH"
  service = MarketDataService()
  service.previous_daily_close_cache = {}
  service.trading_time_service = FakeTradingTimeService()
  service.divid_factor_service = FakeDividFactorService()

  class FakeHistoricalAdapter:
    async def get_klines(self, **kwargs):
      assert kwargs["instrument_code"] == stock_code
      assert kwargs["period"] == "1d"
      assert kwargs["start_time"].date() == date(2026, 6, 3)
      return []

  class FakeAdapterManager:
    historical_adapter = FakeHistoricalAdapter()

  service.adapter_manager = FakeAdapterManager()
  tick = _tick(
    stock_code=stock_code,
    value_time=datetime(2026, 6, 4, 9, 35),
    price=13.31,
    volume=1000,
    amount=13310,
    tickvol=20,
  )
  tick.last_close = 12.68

  normalized = await service._normalize_tick_pre_close(stock_code, tick)

  assert normalized.last_close == 12.68


@pytest.mark.asyncio
async def test_market_data_service_tick_pre_close_caches_native_fallback_and_logs_once(
  caplog,
):
  stock_code = "603118.SH"
  service = MarketDataService()
  service.previous_daily_close_cache = {}
  service.trading_time_service = FakeTradingTimeService()
  service.divid_factor_service = FakeDividFactorService()
  calls = {"count": 0}

  class FakeHistoricalAdapter:
    async def get_klines(self, **kwargs):
      calls["count"] += 1
      assert kwargs["instrument_code"] == stock_code
      assert kwargs["period"] == "1d"
      return []

  class FakeAdapterManager:
    historical_adapter = FakeHistoricalAdapter()

  service.adapter_manager = FakeAdapterManager()
  first_tick = _tick(
    stock_code=stock_code,
    value_time=datetime(2026, 6, 4, 9, 35),
    price=13.31,
    volume=1000,
    amount=13310,
    tickvol=20,
  )
  first_tick.last_close = 12.68
  second_tick = _tick(
    stock_code=stock_code,
    value_time=datetime(2026, 6, 4, 9, 36),
    price=13.32,
    volume=1200,
    amount=15984,
    tickvol=10,
  )
  second_tick.last_close = 12.68

  with caplog.at_level(logging.INFO, logger="core.data.market_data_service"):
    first_normalized = await service._normalize_tick_pre_close(
      stock_code, first_tick
    )
    second_normalized = await service._normalize_tick_pre_close(
      stock_code, second_tick
    )

  cache_key = service._previous_daily_close_cache_key(stock_code, first_tick.time)
  assert calls["count"] == 1
  assert first_normalized.last_close == 12.68
  assert second_normalized.last_close == 12.68
  assert service.previous_daily_close_cache[cache_key]["source"] == "tick"
  assert caplog.text.count("昨日收盘价使用tick兜底") == 1


@pytest.mark.asyncio
async def test_market_data_service_tick_pre_close_uses_database_previous_daily_close():
  stock_code = "603118.SH"
  service = MarketDataService()
  service.previous_daily_close_cache = {}
  service.trading_time_service = FakeTradingTimeService()
  service.divid_factor_service = FakeDividFactorService()

  class FakeHistoricalAdapter:
    async def get_klines(self, **kwargs):
      assert kwargs["instrument_code"] == stock_code
      assert kwargs["period"] == "1d"
      assert kwargs["start_time"].date() == date(2026, 6, 3)
      return [
        KLine(
          stock_code=stock_code,
          period="1d",
          time=datetime(2026, 6, 4, 0, 0),
          open=13.24,
          high=13.37,
          low=13.23,
          close=13.31,
          pre_close=12.98,
          volume=100,
          amount=1331.0,
          settelement_price=0.0,
          open_interest=0,
          suspend_flag=0,
        ),
        KLine(
          stock_code=stock_code,
          period="1d",
          time=datetime(2026, 6, 3, 0, 0),
          open=13.17,
          high=13.49,
          low=12.81,
          close=12.98,
          pre_close=12.68,
          volume=100,
          amount=1298.0,
          settelement_price=0.0,
          open_interest=0,
          suspend_flag=0,
        ),
      ]

  class FakeAdapterManager:
    historical_adapter = FakeHistoricalAdapter()

  service.adapter_manager = FakeAdapterManager()
  tick = _tick(
    stock_code=stock_code,
    value_time=datetime(2026, 6, 4, 9, 35),
    price=13.31,
    volume=1000,
    amount=13310,
    tickvol=20,
  )
  tick.last_close = 12.68

  normalized = await service._normalize_tick_pre_close(stock_code, tick)

  assert normalized.last_close == 12.98


@pytest.mark.asyncio
async def test_market_data_service_tick_pre_close_uses_yesterday_close_not_today_pre_close():
  stock_code = "002216.SZ"
  service = MarketDataService()
  service.previous_daily_close_cache = {}
  service.trading_time_service = FakeTradingTimeService()
  service.divid_factor_service = FakeDividFactorService(
    [SimpleNamespace(time=datetime(2026, 6, 5), dr=1.047894)]
  )

  class FakeHistoricalAdapter:
    async def get_klines(self, **kwargs):
      assert kwargs["instrument_code"] == stock_code
      assert kwargs["period"] == "1d"
      assert kwargs["start_time"].date() == date(2026, 6, 4)
      return [
        KLine(
          stock_code=stock_code,
          period="1d",
          time=datetime(2026, 6, 5, 0, 0),
          open=12.26,
          high=12.66,
          low=12.13,
          close=12.34,
          pre_close=12.12,
          volume=100,
          amount=1234.0,
          settelement_price=0.0,
          open_interest=0,
          suspend_flag=0,
        ),
        KLine(
          stock_code=stock_code,
          period="1d",
          time=datetime(2026, 6, 4, 0, 0),
          open=12.8,
          high=12.93,
          low=12.54,
          close=12.69,
          pre_close=12.8,
          volume=100,
          amount=1269.0,
          settelement_price=0.0,
          open_interest=0,
          suspend_flag=0,
        ),
      ]

  class FakeAdapterManager:
    historical_adapter = FakeHistoricalAdapter()

  service.adapter_manager = FakeAdapterManager()
  tick = _tick(
    stock_code=stock_code,
    value_time=datetime(2026, 6, 5, 9, 35),
    price=12.28,
    volume=1000,
    amount=12280,
    tickvol=20,
  )
  tick.last_close = 12.12

  normalized = await service._normalize_tick_pre_close(stock_code, tick)

  assert normalized.last_close == pytest.approx(12.69 / 1.047894)


@pytest.mark.asyncio
async def test_market_data_service_tick_pre_close_does_not_use_stale_older_daily_close():
  stock_code = "605499.SH"
  service = MarketDataService()
  service.previous_daily_close_cache = {}
  service.trading_time_service = FakeTradingTimeService()
  service.divid_factor_service = FakeDividFactorService()

  class FakeHistoricalAdapter:
    async def get_klines(self, **kwargs):
      assert kwargs["instrument_code"] == stock_code
      assert kwargs["period"] == "1d"
      assert kwargs["start_time"].date() == date(2026, 6, 4)
      return [
        KLine(
          stock_code=stock_code,
          period="1d",
          time=datetime(2026, 6, 3, 0, 0),
          open=148.35,
          high=148.39,
          low=142.5,
          close=143.96,
          pre_close=149.6,
          volume=100,
          amount=14396.0,
          settelement_price=0.0,
          open_interest=0,
          suspend_flag=0,
        )
      ]

  class FakeAdapterManager:
    historical_adapter = FakeHistoricalAdapter()

  service.adapter_manager = FakeAdapterManager()
  tick = _tick(
    stock_code=stock_code,
    value_time=datetime(2026, 6, 5, 9, 35),
    price=138.53,
    volume=1000,
    amount=138530,
    tickvol=20,
  )
  tick.last_close = 139.43

  normalized = await service._normalize_tick_pre_close(stock_code, tick)

  assert normalized.last_close == 139.43


@pytest.mark.asyncio
async def test_tick_stream_generates_saves_and_pushes_current_1m_bar(
  monkeypatch,
):
  stock_code = "002594.SZ"
  manager = RealTimeDataManager()
  queue = manager._create_optimized_queue()
  manager.kline_subscribers[f"{stock_code}_1m"] = {queue}
  saved = []

  async def fake_save(kline):
    saved.append(
      KLine(
        stock_code=kline.stock_code,
        period=kline.period,
        time=kline.time,
        open=kline.open,
        high=kline.high,
        low=kline.low,
        close=kline.close,
        pre_close=kline.pre_close,
        volume=kline.volume,
        amount=kline.amount,
        settelement_price=kline.settelement_price,
        open_interest=kline.open_interest,
        suspend_flag=kline.suspend_flag,
      )
    )

  monkeypatch.setattr(manager, "_safe_save_tick_generated_kline", fake_save)
  from core.data import intraday_warm_cache as warm_cache_module

  with warm_cache_module.intraday_warm_cache._lock:
    warm_cache_module.intraday_warm_cache._klines.pop(stock_code, None)

  await manager._handle_tick_generated_1m(
    stock_code,
    _tick(
      stock_code=stock_code,
      value_time=datetime(2026, 6, 2, 9, 30, 3),
      price=95.0,
      volume=1000,
      amount=95000,
      tickvol=20,
    ),
  )
  await manager._handle_tick_generated_1m(
    stock_code,
    _tick(
      stock_code=stock_code,
      value_time=datetime(2026, 6, 2, 9, 30, 42),
      price=95.2,
      volume=1035,
      amount=98332,
      tickvol=35,
    ),
  )
  await manager._handle_tick_generated_1m(
    stock_code,
    _tick(
      stock_code=stock_code,
      value_time=datetime(2026, 6, 2, 9, 30, 55),
      price=95.1,
      volume=1060,
      amount=100807,
      tickvol=25,
    ),
  )

  first = queue.get_nowait()
  second = queue.get_nowait()
  third = queue.get_nowait()

  assert first.time == datetime(2026, 6, 2, 9, 30)
  assert first.open == 95.0
  assert first.close == 95.0
  assert first.volume == 20
  assert second.time == datetime(2026, 6, 2, 9, 30)
  assert second.open == 95.0
  assert second.high == 95.2
  assert second.close == 95.2
  assert second.volume == 55
  assert second.amount == pytest.approx(5232)
  assert third.time == datetime(2026, 6, 2, 9, 30)
  assert third.high == 95.2
  assert third.close == 95.1
  assert third.volume == 80
  assert len(saved) == 3
  assert saved[-1].close == 95.1
  assert saved[-1].volume == 80
  cached = warm_cache_module.intraday_warm_cache.get_klines(stock_code)
  assert cached[-1].close == 95.1
  assert cached[-1].volume == 80


@pytest.mark.asyncio
async def test_tick_generated_1m_save_is_throttled_within_same_minute(
  monkeypatch,
):
  stock_code = "600900.SH"
  manager = RealTimeDataManager()
  saved = []

  async def fake_save(kline):
    saved.append(
      KLine(
        stock_code=kline.stock_code,
        period=kline.period,
        time=kline.time,
        open=kline.open,
        high=kline.high,
        low=kline.low,
        close=kline.close,
        pre_close=kline.pre_close,
        volume=kline.volume,
        amount=kline.amount,
        settelement_price=kline.settelement_price,
        open_interest=kline.open_interest,
        suspend_flag=kline.suspend_flag,
      )
    )

  monkeypatch.setattr(manager, "_safe_save_tick_generated_kline", fake_save)

  await manager._handle_tick_generated_1m(
    stock_code,
    _tick(
      stock_code=stock_code,
      value_time=datetime(2026, 6, 2, 13, 51, 3),
      price=65.0,
      volume=1000,
      amount=65000,
      tickvol=20,
    ),
  )
  await manager._handle_tick_generated_1m(
    stock_code,
    _tick(
      stock_code=stock_code,
      value_time=datetime(2026, 6, 2, 13, 51, 8),
      price=65.2,
      volume=1035,
      amount=67282,
      tickvol=35,
    ),
  )
  await manager._handle_tick_generated_1m(
    stock_code,
    _tick(
      stock_code=stock_code,
      value_time=datetime(2026, 6, 2, 13, 52, 1),
      price=65.1,
      volume=1060,
      amount=68909,
      tickvol=25,
    ),
  )

  assert [item.time for item in saved] == [
    datetime(2026, 6, 2, 13, 51),
    datetime(2026, 6, 2, 13, 51),
    datetime(2026, 6, 2, 13, 52),
  ]
  assert saved[0].close == 65.0
  assert saved[1].close == 65.2
  assert saved[2].close == 65.1
