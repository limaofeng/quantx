import math
from datetime import date
from types import SimpleNamespace

import pytest
from quantx_api.gqlapi.resolvers.stock_screening import StockScreeningResolver
from quantx_api.gqlapi.types import (
  StockScreenInput,
  StockScreenSortDirection,
  StockScreenSortField,
  StockScreenSortInput,
  StockScreenUniverse,
)


async def fake_db_factory():
  yield object()


async def natural_expected_snapshot_date(today):
  return today


class EmptySnapshotRepo:
  def __init__(self, db):
    self.db = db

  async def get_latest_snapshot_date(self):
    return None


class FailedRunRepo:
  def __init__(self, db):
    self.db = db

  async def find_latest_completed(self, snapshot_date=None):
    return None

  async def find_latest(self, snapshot_date=None):
    return SimpleNamespace(
      snapshot_date=date(2026, 5, 19),
      signal_version="daily-signal-v2:2026-05-19",
      score_version="score-v1",
      status="failed",
      completed_at=None,
      warnings="未保存任何日级信号快照; 批量拉取 K 线失败",
    )


class SnapshotRepoWithNonFiniteValues:
  def __init__(self, db):
    self.db = db

  async def get_latest_snapshot_date(self):
    return date(2026, 5, 20)

  async def get_latest_calculated_at(self, snapshot_date):
    return None

  async def screen_snapshots(
    self,
    snapshot_date,
    signal_codes=None,
    field_conditions=None,
    include_industries=None,
    exclude_industries=None,
    sort=None,
    min_roe=None,
    min_net_profit_growth=None,
    min_yoy_growth=None,
    limit=200,
    offset=0,
    universe="stock",
    exclude_st=True,
  ):
    return [
      SimpleNamespace(
        code="000001.SZ",
        name="平安银行",
        current_price=math.nan,
        open_price=10.0,
        change_pct=float("inf"),
        volume=1000.0,
        volume_ratio=math.nan,
        avg_volume_20=None,
        peak_price=12.0,
        days_since_peak=math.nan,
        price_drop_pct=math.nan,
        low_price_252=float("-inf"),
        days_since_low=3,
        price_rise_pct=8.5,
        consecutive_down_days=None,
        consecutive_down_pct=math.nan,
        kdj_k=math.nan,
        kdj_d=50.0,
        kdj_j=float("inf"),
        rsi6=math.nan,
        rsi12=45.0,
        rsi24=None,
        boll_upper=12.0,
        boll_mid=math.nan,
        boll_lower=8.0,
        ma5=math.nan,
        ma10=9.8,
        ma20=None,
        ma5_prev=math.nan,
        ma10_prev=float("inf"),
        matched_signals=["强势股"],
      )
    ], 1

  async def find_industry_names_by_codes(self, codes):
    return {"000001.SZ": "银行"}


class CompletedRunRepo:
  def __init__(self, db):
    self.db = db

  async def find_latest_completed(self, snapshot_date=None):
    return SimpleNamespace(
      snapshot_date=date(2026, 5, 20),
      signal_version="daily-signal-v2:2026-05-20",
      score_version="score-v1",
      status="success",
      completed_at=None,
      warnings=None,
    )

  async def find_latest(self, snapshot_date=None):
    return await self.find_latest_completed(snapshot_date)


class CompletedRunRepoForWeekend:
  def __init__(self, db):
    self.db = db

  async def find_latest_completed(self, snapshot_date=None):
    if snapshot_date is not None and snapshot_date != date(2026, 5, 22):
      return None
    return SimpleNamespace(
      snapshot_date=date(2026, 5, 22),
      signal_version="daily-signal-v2:2026-05-22",
      score_version="score-v1",
      status="success",
      completed_at=None,
      warnings=None,
    )

  async def find_latest(self, snapshot_date=None):
    return await self.find_latest_completed(snapshot_date)


class SnapshotRepoForWeekend(SnapshotRepoWithNonFiniteValues):
  async def get_latest_snapshot_date(self):
    return date(2026, 5, 22)


class SnapshotRepoWithSortableRows(SnapshotRepoWithNonFiniteValues):
  last_sort = None
  last_universe = None
  last_exclude_st = None
  last_min_roe = None
  last_min_net_profit_growth = None
  last_min_yoy_growth = None

  async def find_instrument_types_by_codes(self, codes):
    return {code: "etf" if code == "000001.SZ" else "stock" for code in codes}

  async def screen_snapshots(
    self,
    snapshot_date,
    signal_codes=None,
    field_conditions=None,
    include_industries=None,
    exclude_industries=None,
    sort=None,
    min_roe=None,
    min_net_profit_growth=None,
    min_yoy_growth=None,
    limit=200,
    offset=0,
    universe="stock",
    exclude_st=True,
  ):
    SnapshotRepoWithSortableRows.last_sort = sort
    SnapshotRepoWithSortableRows.last_universe = universe
    SnapshotRepoWithSortableRows.last_exclude_st = exclude_st
    SnapshotRepoWithSortableRows.last_min_roe = min_roe
    SnapshotRepoWithSortableRows.last_min_net_profit_growth = min_net_profit_growth
    SnapshotRepoWithSortableRows.last_min_yoy_growth = min_yoy_growth
    return [
      SimpleNamespace(
        code="000002.SZ",
        name="低分样本",
        current_price=8.0,
        open_price=7.9,
        change_pct=1.0,
        volume=1000.0,
        volume_ratio=0.5,
        avg_volume_20=2000.0,
        peak_price=10.0,
        days_since_peak=20,
        price_drop_pct=-1.0,
        low_price_252=7.0,
        days_since_low=2,
        price_rise_pct=1.0,
        consecutive_down_days=0,
        consecutive_down_pct=0.0,
        kdj_k=30.0,
        kdj_d=20.0,
        kdj_j=40.0,
        rsi6=45.0,
        rsi12=48.0,
        rsi24=50.0,
        boll_upper=10.0,
        boll_mid=8.0,
        boll_lower=6.0,
        ma5=8.0,
        ma10=7.8,
        ma20=7.6,
        ma5_prev=7.8,
        ma10_prev=7.7,
        matched_signals=[],
      ),
      SimpleNamespace(
        code="000001.SZ",
        name="高分样本",
        current_price=12.0,
        open_price=11.0,
        change_pct=2.0,
        volume=5000.0,
        volume_ratio=3.0,
        avg_volume_20=1600.0,
        peak_price=16.0,
        days_since_peak=5,
        price_drop_pct=-20.0,
        low_price_252=9.0,
        days_since_low=10,
        price_rise_pct=15.0,
        consecutive_down_days=0,
        consecutive_down_pct=0.0,
        kdj_k=70.0,
        kdj_d=50.0,
        kdj_j=90.0,
        rsi6=75.0,
        rsi12=72.0,
        rsi24=65.0,
        boll_upper=13.0,
        boll_mid=11.0,
        boll_lower=9.0,
        ma5=12.0,
        ma10=11.0,
        ma20=10.0,
        ma5_prev=11.0,
        ma10_prev=10.5,
        financial_metric=SimpleNamespace(
          roe_ttm=12.345,
          net_profit_quarter_growth_pct=18.9,
          revenue_quarter_growth_pct=7.6,
          net_profit_growth_pct=28.1,
          revenue_growth_pct=16.2,
          report_date=date(2025, 12, 31),
          announce_date=date(2026, 4, 20),
          quality_flags=["valid"],
        ),
        matched_signals=["强势股", "放量突破"],
      ),
    ], 2


@pytest.mark.asyncio
async def test_stock_screen_reports_latest_failed_snapshot_run(monkeypatch):
  import quantx_api.gqlapi.resolvers.stock_screening as stock_screening_module

  monkeypatch.setattr(stock_screening_module, "get_async_db", fake_db_factory)
  monkeypatch.setattr(
    stock_screening_module,
    "IndicatorSnapshotRepository",
    EmptySnapshotRepo,
  )
  monkeypatch.setattr(
    stock_screening_module,
    "DailySignalRunRepository",
    FailedRunRepo,
  )
  monkeypatch.setattr(
    StockScreeningResolver,
    "_expected_snapshot_date",
    staticmethod(natural_expected_snapshot_date),
  )

  result = await StockScreeningResolver.stock_screen(StockScreenInput())

  assert result.total == 0
  assert result.snapshot_date is None
  assert result.is_complete is False
  assert "最近日级信号快照运行未成功" in result.warnings[0]
  assert "批量拉取 K 线失败" in result.warnings[0]


@pytest.mark.asyncio
async def test_stock_screen_sanitizes_non_finite_snapshot_numbers(monkeypatch):
  import quantx_api.gqlapi.resolvers.stock_screening as stock_screening_module

  monkeypatch.setattr(stock_screening_module, "get_async_db", fake_db_factory)
  monkeypatch.setattr(
    stock_screening_module,
    "IndicatorSnapshotRepository",
    SnapshotRepoWithNonFiniteValues,
  )
  monkeypatch.setattr(
    stock_screening_module,
    "DailySignalRunRepository",
    CompletedRunRepo,
  )
  monkeypatch.setattr(
    StockScreeningResolver,
    "_today",
    staticmethod(lambda: date(2026, 5, 20)),
  )
  monkeypatch.setattr(
    StockScreeningResolver,
    "_expected_snapshot_date",
    staticmethod(natural_expected_snapshot_date),
  )

  result = await StockScreeningResolver.stock_screen(StockScreenInput())

  assert result.total == 1
  item = result.items[0]
  assert item.current_price == 0.0
  assert item.change_pct == 0.0
  assert item.volume_ratio == 0.0
  assert item.days_since_peak == 0
  assert item.low_price == 0.0
  assert item.ma5_prev is None
  assert item.ma10_prev is None
  assert math.isfinite(item.score)


@pytest.mark.asyncio
async def test_stock_screen_uses_previous_trading_day_on_non_trading_day(monkeypatch):
  import quantx_api.gqlapi.resolvers.stock_screening as stock_screening_module

  async def expected_previous_trading_day(today):
    assert today == date(2026, 5, 23)
    return date(2026, 5, 22)

  monkeypatch.setattr(stock_screening_module, "get_async_db", fake_db_factory)
  monkeypatch.setattr(
    stock_screening_module,
    "IndicatorSnapshotRepository",
    SnapshotRepoForWeekend,
  )
  monkeypatch.setattr(
    stock_screening_module,
    "DailySignalRunRepository",
    CompletedRunRepoForWeekend,
  )
  monkeypatch.setattr(
    StockScreeningResolver,
    "_today",
    staticmethod(lambda: date(2026, 5, 23)),
  )
  monkeypatch.setattr(
    StockScreeningResolver,
    "_expected_snapshot_date",
    staticmethod(expected_previous_trading_day),
  )

  result = await StockScreeningResolver.stock_screen(
    StockScreenInput(min_roe=5.0)
  )

  assert result.snapshot_date == date(2026, 5, 22)
  assert result.has_stale_data is False
  assert not any("今日快照未完成" in warning for warning in result.warnings)
  assert not any("财务筛选字段尚未进入日级信号快照" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_stock_screen_passes_financial_filters_and_maps_financial_metrics(monkeypatch):
  import quantx_api.gqlapi.resolvers.stock_screening as stock_screening_module

  SnapshotRepoWithSortableRows.last_min_roe = None
  SnapshotRepoWithSortableRows.last_min_net_profit_growth = None
  SnapshotRepoWithSortableRows.last_min_yoy_growth = None
  monkeypatch.setattr(stock_screening_module, "get_async_db", fake_db_factory)
  monkeypatch.setattr(
    stock_screening_module,
    "IndicatorSnapshotRepository",
    SnapshotRepoWithSortableRows,
  )
  monkeypatch.setattr(
    stock_screening_module,
    "DailySignalRunRepository",
    CompletedRunRepo,
  )
  monkeypatch.setattr(
    StockScreeningResolver,
    "_today",
    staticmethod(lambda: date(2026, 5, 20)),
  )
  monkeypatch.setattr(
    StockScreeningResolver,
    "_expected_snapshot_date",
    staticmethod(natural_expected_snapshot_date),
  )

  result = await StockScreeningResolver.stock_screen(
    StockScreenInput(
      min_roe=5.0,
      min_net_profit_growth=10.0,
      min_yoy_growth=3.0,
    )
  )

  assert SnapshotRepoWithSortableRows.last_min_roe == 5.0
  assert SnapshotRepoWithSortableRows.last_min_net_profit_growth == 10.0
  assert SnapshotRepoWithSortableRows.last_min_yoy_growth == 3.0
  item = result.items[0]
  assert item.roe == 12.345
  assert item.net_profit_growth == 18.9
  assert item.yoy_growth == 7.6
  assert item.net_profit_accum_growth == 28.1
  assert item.revenue_accum_growth == 16.2
  assert item.financial_report_date == date(2025, 12, 31)
  assert item.financial_announce_date == date(2026, 4, 20)
  assert item.financial_quality_flags == ["valid"]


@pytest.mark.asyncio
async def test_stock_screen_passes_sort_and_preserves_sorted_repository_order(monkeypatch):
  import quantx_api.gqlapi.resolvers.stock_screening as stock_screening_module

  SnapshotRepoWithSortableRows.last_sort = None
  monkeypatch.setattr(stock_screening_module, "get_async_db", fake_db_factory)
  monkeypatch.setattr(
    stock_screening_module,
    "IndicatorSnapshotRepository",
    SnapshotRepoWithSortableRows,
  )
  monkeypatch.setattr(
    stock_screening_module,
    "DailySignalRunRepository",
    CompletedRunRepo,
  )
  monkeypatch.setattr(
    StockScreeningResolver,
    "_today",
    staticmethod(lambda: date(2026, 5, 20)),
  )
  monkeypatch.setattr(
    StockScreeningResolver,
    "_expected_snapshot_date",
    staticmethod(natural_expected_snapshot_date),
  )

  result = await StockScreeningResolver.stock_screen(
    StockScreenInput(
      sort=StockScreenSortInput(
        field=StockScreenSortField.CHANGE_PCT,
        direction=StockScreenSortDirection.ASC,
      )
    )
  )

  assert SnapshotRepoWithSortableRows.last_sort == {
    "field": "change_pct",
    "direction": "asc",
  }
  assert [item.code for item in result.items] == ["000002.SZ", "000001.SZ"]


@pytest.mark.asyncio
async def test_stock_screen_keeps_default_score_order_without_explicit_sort(monkeypatch):
  import quantx_api.gqlapi.resolvers.stock_screening as stock_screening_module

  SnapshotRepoWithSortableRows.last_sort = None
  SnapshotRepoWithSortableRows.last_exclude_st = None
  monkeypatch.setattr(stock_screening_module, "get_async_db", fake_db_factory)
  monkeypatch.setattr(
    stock_screening_module,
    "IndicatorSnapshotRepository",
    SnapshotRepoWithSortableRows,
  )
  monkeypatch.setattr(
    stock_screening_module,
    "DailySignalRunRepository",
    CompletedRunRepo,
  )
  monkeypatch.setattr(
    StockScreeningResolver,
    "_today",
    staticmethod(lambda: date(2026, 5, 20)),
  )
  monkeypatch.setattr(
    StockScreeningResolver,
    "_expected_snapshot_date",
    staticmethod(natural_expected_snapshot_date),
  )

  result = await StockScreeningResolver.stock_screen(StockScreenInput())

  assert SnapshotRepoWithSortableRows.last_sort is None
  assert SnapshotRepoWithSortableRows.last_universe == "stock"
  assert SnapshotRepoWithSortableRows.last_exclude_st is True
  assert [item.code for item in result.items] == ["000001.SZ", "000002.SZ"]


@pytest.mark.asyncio
async def test_stock_screen_passes_exclude_st_false(monkeypatch):
  import quantx_api.gqlapi.resolvers.stock_screening as stock_screening_module

  SnapshotRepoWithSortableRows.last_exclude_st = None
  monkeypatch.setattr(stock_screening_module, "get_async_db", fake_db_factory)
  monkeypatch.setattr(
    stock_screening_module,
    "IndicatorSnapshotRepository",
    SnapshotRepoWithSortableRows,
  )
  monkeypatch.setattr(
    stock_screening_module,
    "DailySignalRunRepository",
    CompletedRunRepo,
  )
  monkeypatch.setattr(
    StockScreeningResolver,
    "_today",
    staticmethod(lambda: date(2026, 5, 20)),
  )
  monkeypatch.setattr(
    StockScreeningResolver,
    "_expected_snapshot_date",
    staticmethod(natural_expected_snapshot_date),
  )

  await StockScreeningResolver.stock_screen(StockScreenInput(exclude_st=False))

  assert SnapshotRepoWithSortableRows.last_exclude_st is False


@pytest.mark.asyncio
async def test_stock_screen_passes_etf_universe(monkeypatch):
  import quantx_api.gqlapi.resolvers.stock_screening as stock_screening_module

  SnapshotRepoWithSortableRows.last_sort = None
  SnapshotRepoWithSortableRows.last_universe = None
  monkeypatch.setattr(stock_screening_module, "get_async_db", fake_db_factory)
  monkeypatch.setattr(
    stock_screening_module,
    "IndicatorSnapshotRepository",
    SnapshotRepoWithSortableRows,
  )
  monkeypatch.setattr(
    stock_screening_module,
    "DailySignalRunRepository",
    CompletedRunRepo,
  )
  monkeypatch.setattr(
    StockScreeningResolver,
    "_today",
    staticmethod(lambda: date(2026, 5, 20)),
  )
  monkeypatch.setattr(
    StockScreeningResolver,
    "_expected_snapshot_date",
    staticmethod(natural_expected_snapshot_date),
  )

  result = await StockScreeningResolver.stock_screen(
    StockScreenInput(universe=StockScreenUniverse.ETF)
  )

  assert SnapshotRepoWithSortableRows.last_universe == "etf"
  assert result.items[0].instrument_type == "etf"


@pytest.mark.asyncio
async def test_expected_snapshot_date_uses_1535_cutoff(monkeypatch):
  import quantx_api.gqlapi.resolvers.stock_screening as stock_screening_module

  class FakeTradingTimeService:
    async def is_trading_day(self, market, check_date):
      return True

    async def get_previous_trading_day(self, market, from_date):
      return date(2026, 7, 28)

  monkeypatch.setattr(
    stock_screening_module,
    "TradingTimeService",
    FakeTradingTimeService,
  )
  monkeypatch.setattr(
    StockScreeningResolver,
    "_now",
    staticmethod(lambda: stock_screening_module.datetime(2026, 7, 29, 15, 34)),
  )
  before = await StockScreeningResolver._expected_snapshot_date(
    date(2026, 7, 29)
  )
  monkeypatch.setattr(
    StockScreeningResolver,
    "_now",
    staticmethod(lambda: stock_screening_module.datetime(2026, 7, 29, 15, 35)),
  )
  after = await StockScreeningResolver._expected_snapshot_date(
    date(2026, 7, 29)
  )

  assert before == date(2026, 7, 28)
  assert after == date(2026, 7, 29)


@pytest.mark.asyncio
async def test_snapshot_status_returns_only_dates_after_latest_complete(
  monkeypatch,
):
  import quantx_api.gqlapi.resolvers.stock_screening as stock_screening_module

  class StatusSnapshotRepo:
    def __init__(self, db):
      self.db = db

    async def get_latest_snapshot_date(self):
      return date(2026, 7, 22)

    async def find_snapshot_dates(self, start_date, end_date):
      return [date(2026, 7, 22)]

    async def get_latest_calculated_at(self, snapshot_date):
      return None

  class StatusRunRepo:
    def __init__(self, db):
      self.db = db

    async def find_latest(self, snapshot_date=None):
      return SimpleNamespace(
        snapshot_date=date(2026, 7, 22),
        status="success",
        completed_at=None,
        warnings=None,
      )

    async def find_latest_completed(self, snapshot_date=None):
      if snapshot_date == date(2026, 7, 22):
        return await self.find_latest(snapshot_date)
      return None

    async def find_completed_dates(self, start_date, end_date):
      return [date(2026, 7, 22)]

  class FakeTradingDates:
    async def get_trading_calendar(self, market, start_date, end_date):
      current = start_date
      result = []
      while current <= end_date:
        if current.weekday() < 5:
          result.append(current)
        current = date.fromordinal(current.toordinal() + 1)
      return result

  monkeypatch.setattr(stock_screening_module, "get_async_db", fake_db_factory)
  monkeypatch.setattr(
    stock_screening_module,
    "IndicatorSnapshotRepository",
    StatusSnapshotRepo,
  )
  monkeypatch.setattr(
    stock_screening_module,
    "DailySignalRunRepository",
    StatusRunRepo,
  )
  monkeypatch.setattr(
    stock_screening_module,
    "TradingDateHelper",
    FakeTradingDates,
  )
  monkeypatch.setattr(
    StockScreeningResolver,
    "_expected_snapshot_date",
    staticmethod(lambda today: _async_value(date(2026, 7, 29))),
  )

  result = await StockScreeningResolver.stock_screen_snapshot_status(30)

  assert result.latest_snapshot_date == date(2026, 7, 22)
  assert result.missing_snapshot_dates == [
    date(2026, 7, 23),
    date(2026, 7, 24),
    date(2026, 7, 27),
    date(2026, 7, 28),
    date(2026, 7, 29),
  ]
  assert result.is_complete is False


async def _async_value(value):
  return value


@pytest.mark.asyncio
async def test_stale_successful_run_is_not_complete(monkeypatch):
  import quantx_api.gqlapi.resolvers.stock_screening as stock_screening_module

  monkeypatch.setattr(stock_screening_module, "get_async_db", fake_db_factory)
  monkeypatch.setattr(
    stock_screening_module,
    "IndicatorSnapshotRepository",
    SnapshotRepoWithNonFiniteValues,
  )
  monkeypatch.setattr(
    stock_screening_module,
    "DailySignalRunRepository",
    CompletedRunRepo,
  )
  monkeypatch.setattr(
    StockScreeningResolver,
    "_today",
    staticmethod(lambda: date(2026, 5, 21)),
  )
  monkeypatch.setattr(
    StockScreeningResolver,
    "_expected_snapshot_date",
    staticmethod(lambda today: _async_value(date(2026, 5, 21))),
  )

  result = await StockScreeningResolver.stock_screen(StockScreenInput())

  assert result.snapshot_date == date(2026, 5, 20)
  assert result.has_stale_data is True
  assert result.is_complete is False
