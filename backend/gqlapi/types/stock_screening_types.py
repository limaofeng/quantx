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
