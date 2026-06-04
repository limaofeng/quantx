from datetime import date, datetime
from enum import Enum
from typing import List, Optional

import strawberry

from models.kline import KLine
from models.market_depth import MarketDepth as DomainMarketDepth
from models.realtime_price import RealTimePrice as DomainRealTimePrice
from models.tick import Tick
from .common_types import PageInfo


@strawberry.enum(description="K线周期")
class KLinePeriod(str, Enum):
  """K线周期枚举"""

  MIN_1 = "1m"
  MIN_5 = "5m"
  MIN_15 = "15m"
  MIN_30 = "30m"
  MIN_60 = "60m"
  HOUR_1 = "1h"
  DAY_1 = "1d"
  WEEK_1 = "1w"
  MONTH_1 = "1mon"
  QUARTER_1 = "1q"
  HALF_YEAR_1 = "1hy"
  YEAR_1 = "1y"


@strawberry.enum(description="复权类型")
class DividendType(str, Enum):
  """复权类型枚举"""

  NONE = "none"
  FRONT = "front"
  BACK = "back"
  FRONT_RATIO = "front_ratio"
  BACK_RATIO = "back_ratio"


@strawberry.enum(description="时间序列分页方向")
class PageDirection(str, Enum):
  """时间序列分页方向"""

  PREV = "PREV"
  NEXT = "NEXT"


@strawberry.input(description="K线分页参数")
class KLinePageInput:
  stock_code: str = strawberry.field(description="股票代码")
  period: KLinePeriod = strawberry.field(
    default=KLinePeriod.MIN_1, description="K线周期"
  )
  dividend_type: DividendType = strawberry.field(
    default=DividendType.NONE, description="复权类型"
  )
  cursor: Optional[datetime] = strawberry.field(
    default=None, description="游标时间(不包含)"
  )
  direction: PageDirection = strawberry.field(
    default=PageDirection.PREV, description="分页方向"
  )
  order: str = strawberry.field(
    default="desc", description="返回排序方向，可选 asc 或 desc"
  )
  limit: int = strawberry.field(default=200, description="每页数量")


@strawberry.type(description="逐笔数据")
class TickData:
  stock_code: str = strawberry.field(description="股票代码")
  period: str = strawberry.field(description="周期")
  time: datetime = strawberry.field(description="时间")
  last_price: float = strawberry.field(description="最新价")
  open: float = strawberry.field(description="开盘价")
  high: float = strawberry.field(description="最高价")
  low: float = strawberry.field(description="最低价")
  pre_close: float = strawberry.field(description="昨收价")
  volume: int = strawberry.field(description="成交量")
  amount: float = strawberry.field(description="成交额")

  @staticmethod
  def from_tick(tick: Tick) -> "TickData":
    """从 Tick 数据转换为 TickData"""
    return TickData(
      stock_code=tick.stock_code,
      period=tick.period,
      time=tick.time,
      last_price=tick.last_price,
      open=tick.open,
      high=tick.high,
      low=tick.low,
      pre_close=tick.last_close,
      volume=tick.volume,
      amount=tick.amount,
    )


@strawberry.type(description="K线数据")
class KLineData:
  stock_code: str = strawberry.field(description="股票代码")
  period: str = strawberry.field(description="K线周期")
  time: datetime = strawberry.field(description="时间戳")
  open: float = strawberry.field(description="开盘价")
  high: float = strawberry.field(description="最高价")
  low: float = strawberry.field(description="最低价")
  close: float = strawberry.field(description="收盘价")
  pre_close: float = strawberry.field(description="前收盘价")
  volume: int = strawberry.field(description="成交量")
  amount: float = strawberry.field(description="成交额")

  @staticmethod
  def from_kline(kline: KLine) -> "KLineData":
    """从 KLine 领域模型转换为 GraphQL KLineData"""
    return KLineData(
      stock_code=kline.stock_code,
      period=kline.period,
      time=kline.time,
      open=kline.open,
      high=kline.high,
      low=kline.low,
      close=kline.close,
      pre_close=getattr(kline, "pre_close", 0.0),
      volume=int(kline.volume),
      amount=kline.amount,
    )




@strawberry.type(description="K线分页结果")
class KLinePage:
  items: List[KLineData] = strawberry.field(description="K线列表(按 order 排序)")
  page_info: PageInfo = strawberry.field(description="分页信息")


@strawberry.type(description="日内热缓存状态")
class IntradayWarmCacheStatus:
  stock_code: str = strawberry.field(description="股票代码")
  sources: List[str] = strawberry.field(description="热池来源")
  tick_subscribed: bool = strawberry.field(description="是否已订阅 tick")
  kline_subscribed: bool = strawberry.field(description="是否已订阅 1m K线")
  initialized_date: Optional[date] = strawberry.field(
    default=None, description="首次初始化下载所属交易日"
  )
  initializing: bool = strawberry.field(description="是否正在初始化下载")
  initialization_error: Optional[str] = strawberry.field(
    default=None, description="初始化下载错误"
  )
  last_tick_at: Optional[datetime] = strawberry.field(
    default=None, description="最近 tick 时间"
  )
  last_kline_at: Optional[datetime] = strawberry.field(
    default=None, description="最近 1m K线时间"
  )
  tick_count: int = strawberry.field(description="缓存 tick 数量")
  kline_count: int = strawberry.field(description="缓存 1m K线数量")

  @staticmethod
  def from_status(row: dict) -> "IntradayWarmCacheStatus":
    return IntradayWarmCacheStatus(
      stock_code=row["stock_code"],
      sources=list(row.get("sources") or []),
      tick_subscribed=bool(row.get("tick_subscribed")),
      kline_subscribed=bool(row.get("kline_subscribed")),
      initialized_date=row.get("initialized_date"),
      initializing=bool(row.get("initializing")),
      initialization_error=row.get("initialization_error"),
      last_tick_at=row.get("last_tick_at"),
      last_kline_at=row.get("last_kline_at"),
      tick_count=int(row.get("tick_count") or 0),
      kline_count=int(row.get("kline_count") or 0),
    )


@strawberry.type(description="实时价格数据")
class RealTimePrice:
  stock_code: str = strawberry.field(description="股票代码")
  current_price: float = strawberry.field(description="当前价格")
  change: Optional[float] = strawberry.field(description="涨跌额")
  change_percent: Optional[float] = strawberry.field(description="涨跌幅")
  volume: float = strawberry.field(description="成交量")
  amount: float = strawberry.field(description="成交额")
  time: datetime = strawberry.field(description="时间戳")
  bid_price: float = strawberry.field(description="买一价")
  ask_price: float = strawberry.field(description="卖一价")
  bid_volume: int = strawberry.field(description="买一量")
  ask_volume: int = strawberry.field(description="卖一量")
  high: float = strawberry.field(description="最高价")
  low: float = strawberry.field(description="最低价")
  open: float = strawberry.field(description="开盘价")
  pre_close: Optional[float] = strawberry.field(description="前收盘价")

  @staticmethod
  def from_domain_realtime_price(price: DomainRealTimePrice) -> "RealTimePrice":
    """从领域模型 RealTimePrice 转换为 GraphQL RealTimePrice"""
    return RealTimePrice(
      stock_code=price.stock_code,
      current_price=price.current_price,
      change=price.change,
      change_percent=price.change_percent,
      volume=price.volume,
      amount=price.amount,
      time=price.time,
      bid_price=price.bid_price,
      ask_price=price.ask_price,
      bid_volume=price.bid_volume,
      ask_volume=price.ask_volume,
      high=price.high,
      low=price.low,
      open=price.open,
      pre_close=price.pre_close,
    )


@strawberry.type(description="市场深度档位")
class MarketDepthLevel:
  price: float = strawberry.field(description="价格")
  volume: int = strawberry.field(description="数量")


@strawberry.type(description="市场深度数据")
class MarketDepth:
  stock_code: str = strawberry.field(description="股票代码")
  time: datetime = strawberry.field(description="时间戳")
  bids: List[MarketDepthLevel] = strawberry.field(description="买盘数据")
  asks: List[MarketDepthLevel] = strawberry.field(description="卖盘数据")

  @staticmethod
  def from_domain_market_depth(depth: DomainMarketDepth) -> "MarketDepth":
    """从领域模型 MarketDepth 转换为 GraphQL MarketDepth"""
    return MarketDepth(
      stock_code=depth.stock_code,
      time=depth.time,
      bids=[
        MarketDepthLevel(price=level.price, volume=level.volume)
        for level in depth.bid_levels
      ],
      asks=[
        MarketDepthLevel(price=level.price, volume=level.volume)
        for level in depth.ask_levels
      ],
    )


@strawberry.type(description="股票行情报价")
class StockQuote:
  stock_code: str = strawberry.field(description="股票代码")
  time: datetime = strawberry.field(description="行情时间")
  last_price: float = strawberry.field(description="最新价")
  open: float = strawberry.field(description="开盘价")
  high: float = strawberry.field(description="最高价")
  low: float = strawberry.field(description="最低价")
  pre_close: float = strawberry.field(description="前收盘价")
  change: Optional[float] = strawberry.field(description="涨跌额")
  change_percent: Optional[float] = strawberry.field(description="涨跌幅（%）")
  volume: float = strawberry.field(description="成交量")
  amount: float = strawberry.field(description="成交额")
  turnover_rate: Optional[float] = strawberry.field(description="换手率（%）")

  @staticmethod
  def from_tick(tick: Tick) -> "StockQuote":
    """从 Tick 数据转换为股票行情"""
    # 计算涨跌额和涨跌幅
    change = None
    change_percent = None

    if tick.last_close and tick.last_close > 0:
      change = tick.last_price - tick.last_close
      change_percent = (change / tick.last_close) * 100

    return StockQuote(
      stock_code=tick.stock_code,
      time=tick.time,
      last_price=tick.last_price,
      open=tick.open,
      high=tick.high,
      low=tick.low,
      pre_close=tick.last_close,
      change=change,
      change_percent=change_percent,
      volume=tick.volume,
      amount=tick.amount,
      turnover_rate=None,  # 需要额外计算，暂时为空
    )


@strawberry.type(description="策略状态信息")
class StrategyStatusInfo:
  strategy_id: int = strawberry.field(description="策略ID")
  name: str = strawberry.field(description="策略名称")
  status: str = strawberry.field(description="运行状态")
  time: datetime = strawberry.field(description="状态更新时间")
  message: Optional[str] = strawberry.field(description="状态消息")
  performance: Optional[float] = strawberry.field(description="收益率")


@strawberry.type(description="系统告警")
class SystemAlert:
  alert_id: str = strawberry.field(description="告警ID")
  severity: str = strawberry.field(description="告警级别")
  title: str = strawberry.field(description="告警标题")
  message: str = strawberry.field(description="告警消息")
  time: datetime = strawberry.field(description="告警时间")
  source: str = strawberry.field(description="告警源")
  resolved: bool = strawberry.field(description="是否已解决")
