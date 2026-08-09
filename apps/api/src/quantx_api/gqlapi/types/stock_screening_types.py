from datetime import date, datetime
from enum import Enum
from typing import List, Optional

import strawberry


@strawberry.input(description="选股字段条件")
class StockFieldConditionInput:
  field: str = strawberry.field(description="快照字段名")
  operator: str = strawberry.field(default="gte", description="操作符: gte/lte/gt/lt/eq/between")
  value: float = strawberry.field(description="比较值")
  value_to: Optional[float] = strawberry.field(default=None, description="区间结束值")


@strawberry.input(description="选股信号条件")
class StockSignalConditionInput:
  signal_code: str = strawberry.field(description="信号码")
  required: bool = strawberry.field(default=True, description="是否必须命中")


@strawberry.input(description="选股评分规则")
class StockSignalWeightInput:
  signal_code: str = strawberry.field(description="信号码")
  weight: float = strawberry.field(default=1.0, description="命中权重")


@strawberry.enum(description="条件选股排序字段")
class StockScreenSortField(Enum):
  CODE = "code"
  NAME = "name"
  CURRENT_PRICE = "current_price"
  CHANGE_PCT = "change_pct"
  SIGNAL_COUNT = "signal_count"
  KDJ_J = "kdj_j"
  RSI12 = "rsi12"
  VOLUME_RATIO = "volume_ratio"
  VOLUME_RATIO_5 = "volume_ratio_5"
  AMOUNT_RATIO_20 = "amount_ratio_20"
  TURNOVER_RATE = "turnover_rate_pct"
  VOLUME_PERCENTILE_60 = "volume_percentile_60"
  AMOUNT_PERCENTILE_60 = "amount_percentile_60"
  PRICE_DROP_PCT = "price_drop_pct"
  DAYS_SINCE_PEAK = "days_since_peak"
  ROE = "roe_ttm"
  NET_PROFIT_GROWTH = "net_profit_growth_pct"
  YOY_GROWTH = "revenue_growth_pct"


@strawberry.enum(description="条件选股排序方向")
class StockScreenSortDirection(Enum):
  ASC = "asc"
  DESC = "desc"


@strawberry.enum(description="条件选股标的范围")
class StockScreenUniverse(Enum):
  STOCK = "stock"
  ETF = "etf"
  STOCK_AND_ETF = "stock_and_etf"


@strawberry.input(description="条件选股排序输入")
class StockScreenSortInput:
  field: StockScreenSortField = strawberry.field(description="排序字段")
  direction: StockScreenSortDirection = strawberry.field(
    default=StockScreenSortDirection.DESC,
    description="排序方向",
  )


@strawberry.input(description="条件选股输入")
class StockScreenInput:
  include_industries: Optional[List[str]] = strawberry.field(default=None, description="包含行业")
  exclude_industries: Optional[List[str]] = strawberry.field(default=None, description="排除行业")
  field_conditions: Optional[List[StockFieldConditionInput]] = strawberry.field(
    default=None, description="基础字段条件"
  )
  signal_conditions: Optional[List[StockSignalConditionInput]] = strawberry.field(
    default=None, description="信号条件"
  )
  score_rules: Optional[List[StockSignalWeightInput]] = strawberry.field(
    default=None, description="评分规则"
  )
  universe: StockScreenUniverse = strawberry.field(
    default=StockScreenUniverse.STOCK,
    description="标的范围：默认股票，可切换 ETF 或股票+ETF",
  )
  exclude_st: bool = strawberry.field(
    default=True,
    description="是否排除 ST/*ST 风险警示股票",
  )
  require_fresh: bool = strawberry.field(default=False, description="是否要求当日信号完成")
  sort: Optional[StockScreenSortInput] = strawberry.field(default=None, description="排序配置")
  limit: int = strawberry.field(default=200, description="每页数量，最大200")
  offset: int = strawberry.field(default=0, description="偏移量")
  min_roe: Optional[float] = strawberry.field(default=None, description="最小TTM归母ROE")
  min_net_profit_growth: Optional[float] = strawberry.field(default=None, description="最小归母净利润单季同比增速")
  min_yoy_growth: Optional[float] = strawberry.field(default=None, description="最小营收单季同比增速")


@strawberry.type(description="条件选股结果项")
class StockScreenItem:
  code: str
  name: str
  industry: Optional[str]
  instrument_type: str
  current_price: float
  open_price: float
  change_pct: float
  volume: float
  volume_ratio: float
  avg_volume_20: float
  avg_volume_5: float
  volume_ratio_5: float
  avg_amount_20: float
  amount_ratio_20: float
  turnover_rate_pct: Optional[float]
  volume_percentile_60: float
  amount_percentile_60: float
  is_bullish: bool
  peak_price: float
  days_since_peak: int
  price_drop_pct: float
  low_price: float
  days_since_low: int
  price_rise_pct: float
  consecutive_down_days: int
  consecutive_down_pct: float
  k: float
  d: float
  j: float
  rsi6: float
  rsi12: float
  rsi24: float
  upper_band: float
  middle_band: float
  lower_band: float
  ma5: float
  ma10: float
  ma20: float
  ma5_prev: Optional[float]
  ma10_prev: Optional[float]
  roe: Optional[float]
  net_profit_growth: Optional[float]
  yoy_growth: Optional[float]
  net_profit_accum_growth: Optional[float]
  revenue_accum_growth: Optional[float]
  financial_report_date: Optional[date]
  financial_announce_date: Optional[date]
  financial_quality_flags: List[str]
  matched_strategies: List[str]
  score: float
  score_version: str
  signal_version: str
  calculated_at: Optional[datetime]
  has_stale_data: bool
  signal_missing: bool
  missing_signals: List[str]


@strawberry.type(description="条件选股分页结果")
class StockScreenPage:
  items: List[StockScreenItem]
  total: int
  limit: int
  offset: int
  snapshot_date: Optional[date]
  score_version: str
  signal_version: str
  calculated_at: Optional[datetime]
  has_stale_data: bool
  is_complete: bool
  warnings: List[str]


@strawberry.type(description="选股日级快照完整性状态")
class StockScreenSnapshotStatus:
  latest_snapshot_date: Optional[date]
  expected_snapshot_date: date
  missing_snapshot_dates: List[date]
  is_complete: bool
  latest_run_status: Optional[str]
  latest_calculated_at: Optional[datetime]
  warnings: List[str]


@strawberry.input(description="盘中全市场量能筛选输入")
class IntradayVolumeScreenInput:
  universe: StockScreenUniverse = strawberry.field(
    default=StockScreenUniverse.STOCK,
    description="标的范围：默认股票，可切换 ETF 或股票+ETF",
  )
  include_industries: Optional[List[str]] = strawberry.field(default=None, description="包含行业")
  exclude_industries: Optional[List[str]] = strawberry.field(default=None, description="排除行业")
  exclude_st: bool = strawberry.field(default=True, description="是否排除 ST/*ST 风险警示股票")
  min_volume_pace_ratio: Optional[float] = strawberry.field(default=None, description="最小盘中量能进度倍数")
  min_amount_pace_ratio: Optional[float] = strawberry.field(default=None, description="最小盘中成交额进度倍数")
  min_last_5m_volume_ratio: Optional[float] = strawberry.field(default=None, description="最小近5分钟放量倍数")
  min_intraday_turnover_rate: Optional[float] = strawberry.field(default=None, description="最小盘中换手率 %")
  min_depth_imbalance_5: Optional[float] = strawberry.field(default=None, description="最小五档盘口量失衡")
  stale_after_seconds: int = strawberry.field(default=10, description="超过该秒数未更新标记为 stale")
  limit: int = strawberry.field(default=200, description="每页数量，最大200")
  offset: int = strawberry.field(default=0, description="偏移量")


@strawberry.type(description="盘中全市场量能筛选结果项")
class IntradayVolumeScreenItem:
  code: str
  name: str
  industry: Optional[str]
  instrument_type: str
  current_price: float
  change_pct: float
  volume: float
  amount: float
  volume_ratio: float
  amount_ratio: float
  volume_pace_ratio: float
  amount_pace_ratio: float
  last_5m_volume_ratio: float
  intraday_turnover_rate_pct: Optional[float]
  depth_imbalance_5: float
  avg_trade_amount_proxy: Optional[float]
  matched_signals: List[str]
  updated_at: datetime
  is_stale: bool


@strawberry.type(description="盘中全市场量能筛选分页结果")
class IntradayVolumeScreenPage:
  items: List[IntradayVolumeScreenItem]
  total: int
  limit: int
  offset: int
  updated_at: Optional[datetime]
  is_scanner_running: bool
  warnings: List[str]


@strawberry.type(description="日级信号元信息")
class SignalMeta:
  signal_code: str
  display_name: str
  category: str
  description: str
  max_window: int
  signal_version: str
  calculated_at: Optional[datetime]
  available_snapshot_date: Optional[date]
  enabled: bool
