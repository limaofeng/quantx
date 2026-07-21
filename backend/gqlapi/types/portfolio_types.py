from datetime import datetime
from datetime import date as Date
from typing import Annotated, List, Optional

import strawberry
from strawberry.dataloader import DataLoader
from strawberry.scalars import JSON

from gqlapi.types.market_data_types import StockQuote
from models.daily_asset_snapshot import DailyAssetSnapshot as DailyAssetSnapshotModel
from models.position import Position as PositionModel
from models.closed_position_cycle import ClosedPositionCycle as ClosedPositionCycleModel

from .instrument_types import Instrument


@strawberry.type(description="账户信息")
class Account:
  id: str = strawberry.field(description="账户ID")
  account_name: str = strawberry.field(description="账户名称")
  account_type: str = strawberry.field(description="账户类型")
  total_asset: float = strawberry.field(description="总资产")
  cash: float = strawberry.field(description="可用余额")
  frozen_cash: float = strawberry.field(description="冻结资金")
  market_value: float = strawberry.field(description="持仓市值")
  total_profit_loss: Optional[float] = strawberry.field(description="总盈亏")
  profit_loss_percent: Optional[float] = strawberry.field(description="盈亏百分比")
  create_time: datetime = strawberry.field(description="创建时间")
  update_time: datetime = strawberry.field(description="更新时间")


@strawberry.type(description="持仓信息")
class Position:
  id: str = strawberry.field(description="持仓ID")
  account_id: str = strawberry.field(description="资金账号")
  account_type: Optional[str] = strawberry.field(description="账户类型")
  stock_code: str = strawberry.field(description="证券代码")
  instrument_name: Optional[str] = strawberry.field(description="证券名称")
  volume: int = strawberry.field(
    description="持仓数量,股票以'股'为单位, 债券以'张'为单位"
  )
  can_use_volume: int = strawberry.field(
    description="可用数量, 股票以'股'为单位, 债券以'张'为单位"
  )
  frozen_volume: int = strawberry.field(description="冻结数量")
  on_road_volume: int = strawberry.field(description="在途股份")
  yesterday_volume: int = strawberry.field(description="昨夜拥股")
  open_price: Optional[float] = strawberry.field(description="开仓价")
  avg_price: Optional[float] = strawberry.field(description="成本价")
  market_value: Optional[float] = strawberry.field(description="市值")
  direction: Optional[int] = strawberry.field(description="多空, 股票不需要")
  created_at: Optional[datetime] = strawberry.field(description="创建时间")
  updated_at: Optional[datetime] = strawberry.field(description="更新时间")

  # 计算字段
  last_price: Optional[float] = strawberry.field(description="当前价")
  profit_rate: Optional[float] = strawberry.field(description="盈亏比例")
  profit_loss: Optional[float] = strawberry.field(description="盈亏金额")
  market_value_percent: Optional[float] = strawberry.field(
    description="市值占比（%）- 仅在汇总查询中有值", default=None
  )

  @strawberry.field(description="获取股票最新实时行情")
  async def quote(
    self,
    info: strawberry.types.Info,
  ) -> Optional[Annotated["StockQuote", strawberry.lazy(".market_data_types")]]:
    """延迟加载股票实时行情数据，使用 DataLoader 批量加载避免 N+1 查询"""
    loader: DataLoader[str, StockQuote] = info.context["quote_loader"]
    return await loader.load(self.stock_code)

  @strawberry.field(description="证券产品详细信息")
  async def instrument(self) -> Optional[Instrument]:
    """延迟加载证券产品信息"""
    from services.instrument_service import InstrumentService

    instrument_service = InstrumentService()
    instrument_model = await instrument_service.find_by_id(self.stock_code)

    if instrument_model:
      return Instrument.from_model(instrument_model)
    return None

  @staticmethod
  def from_model(
    model: PositionModel,
    last_price: Optional[float] = None,
    market_value_percent: Optional[float] = None,
  ) -> "Position":
    """从数据库 Model 转换为 GraphQL 类型"""
    # 计算盈亏相关字段
    profit_loss = None
    profit_rate = None

    last_price = last_price or model.last_price

    if last_price and model.avg_price and model.volume:
      profit_loss = (last_price - float(model.avg_price)) * model.volume
      if model.avg_price > 0:
        profit_rate = ((last_price / float(model.avg_price)) - 1) * 100

    return Position(
      id=model.id,
      account_id=model.account_id,
      account_type=model.account_type.value if model.account_type else None,
      stock_code=model.stock_code,
      instrument_name=model.instrument_name,
      volume=model.volume or 0,
      can_use_volume=model.can_use_volume or 0,
      frozen_volume=model.frozen_volume or 0,
      on_road_volume=model.on_road_volume or 0,
      yesterday_volume=model.yesterday_volume or 0,
      open_price=float(model.open_price) if model.open_price else None,
      avg_price=float(model.avg_price) if model.avg_price else None,
      market_value=float(model.market_value) if model.market_value else None,
      direction=model.direction,
      created_at=model.created_at,
      updated_at=model.updated_at,
      last_price=last_price,
      profit_rate=profit_rate,
      profit_loss=profit_loss,
      market_value_percent=market_value_percent,
    )


@strawberry.type(description="每日收盘资产快照")
class DailyAssetSnapshot:
  id: str = strawberry.field(description="快照ID")
  scope_type: str = strawberry.field(description="快照层级：ACCOUNT 或 STRATEGY")
  scope_key: str = strawberry.field(description="快照层级唯一键")
  account_id: Optional[str] = strawberry.field(description="资金账号")
  account_type: Optional[str] = strawberry.field(description="账户类型")
  strategy_run_id: Optional[str] = strawberry.field(description="策略运行实例ID")
  trade_date: Date = strawberry.field(description="交易日")
  snapshot_at: datetime = strawberry.field(description="快照时间")
  source: str = strawberry.field(description="数据来源")
  total_asset_cny: float = strawberry.field(description="总资产")
  cash_available_cny: float = strawberry.field(description="可用资金")
  cash_frozen_cny: float = strawberry.field(description="冻结资金")
  market_value_cny: float = strawberry.field(description="持仓市值")
  gross_asset_delta_cny: Optional[float] = strawberry.field(description="总资产变动")
  net_capital_flow_cny: float = strawberry.field(description="出入金等资本流净额")
  daily_pnl_cny: Optional[float] = strawberry.field(description="当日盈亏")
  daily_return_pct: Optional[float] = strawberry.field(description="当日收益率百分比")
  previous_snapshot_id: Optional[str] = strawberry.field(description="上一日快照ID")
  data_quality: str = strawberry.field(description="数据质量")
  metadata: JSON = strawberry.field(description="快照元数据")

  @staticmethod
  def from_model(model: DailyAssetSnapshotModel) -> "DailyAssetSnapshot":
    return DailyAssetSnapshot(
      id=model.id,
      scope_type=model.scope_type,
      scope_key=model.scope_key,
      account_id=model.account_id,
      account_type=model.account_type.value if model.account_type else None,
      strategy_run_id=model.strategy_run_id,
      trade_date=model.trade_date,
      snapshot_at=model.snapshot_at,
      source=model.source,
      total_asset_cny=float(model.total_asset_cny or 0.0),
      cash_available_cny=float(model.cash_available_cny or 0.0),
      cash_frozen_cny=float(model.cash_frozen_cny or 0.0),
      market_value_cny=float(model.market_value_cny or 0.0),
      gross_asset_delta_cny=float(model.gross_asset_delta_cny)
      if model.gross_asset_delta_cny is not None
      else None,
      net_capital_flow_cny=float(model.net_capital_flow_cny or 0.0),
      daily_pnl_cny=float(model.daily_pnl_cny)
      if model.daily_pnl_cny is not None
      else None,
      daily_return_pct=float(model.daily_return_pct)
      if model.daily_return_pct is not None
      else None,
      previous_snapshot_id=model.previous_snapshot_id,
      data_quality=model.data_quality,
      metadata=model.snapshot_metadata or {},
    )


@strawberry.type(description="持仓表现汇总")
class PortfolioSummary:
  account_id: str = strawberry.field(description="账户ID")
  account_name: str = strawberry.field(description="账户名称")

  # 资产汇总
  total_asset: float = strawberry.field(description="总资产")
  total_market_value: float = strawberry.field(description="总持仓市值")
  cash: float = strawberry.field(description="可用现金")
  cash_ratio: float = strawberry.field(description="现金占比（%）")

  # 盈亏汇总
  total_profit_loss: float = strawberry.field(description="总盈亏")
  total_profit_loss_percent: float = strawberry.field(description="总盈亏比例（%）")
  today_profit_loss: Optional[float] = strawberry.field(description="当日盈亏")
  today_profit_loss_percent: Optional[float] = strawberry.field(
    description="当日盈亏比例（%）"
  )

  # 持仓统计
  position_count: int = strawberry.field(description="持仓品种数量")
  profit_position_count: int = strawberry.field(description="盈利品种数量")
  loss_position_count: int = strawberry.field(description="亏损品种数量")

  # 重要持仓
  top_holdings: List[Position] = strawberry.field(description="前10大持仓")

  # 更新时间
  update_time: datetime = strawberry.field(description="数据更新时间")


@strawberry.type(description="已清仓持仓周期")
class ClosedPositionCycle:
  id: str
  account_id: str
  account_type: Optional[str]
  stock_code: str
  instrument_name: Optional[str]
  opened_at: Optional[datetime]
  closed_at: datetime
  buy_volume: int
  sell_volume: int
  average_buy_price: Optional[float]
  average_sell_price: Optional[float]
  gross_buy_amount: float
  gross_sell_amount: float
  gross_realized_pnl: Optional[float]
  gross_realized_pnl_percent: Optional[float]
  related_trade_ids: List[str]
  source: str
  pnl_quality: str
  quality_flags: List[str]

  @staticmethod
  def from_model(model: ClosedPositionCycleModel) -> "ClosedPositionCycle":
    return ClosedPositionCycle(
      id=model.id,
      account_id=model.account_id,
      account_type=model.account_type,
      stock_code=model.stock_code,
      instrument_name=model.instrument_name,
      opened_at=model.opened_at,
      closed_at=model.closed_at,
      buy_volume=int(model.buy_volume or 0),
      sell_volume=int(model.sell_volume or 0),
      average_buy_price=float(model.average_buy_price)
      if model.average_buy_price is not None
      else None,
      average_sell_price=float(model.average_sell_price)
      if model.average_sell_price is not None
      else None,
      gross_buy_amount=float(model.gross_buy_amount or 0),
      gross_sell_amount=float(model.gross_sell_amount or 0),
      gross_realized_pnl=float(model.gross_realized_pnl)
      if model.gross_realized_pnl is not None
      else None,
      gross_realized_pnl_percent=float(model.gross_realized_pnl_percent)
      if model.gross_realized_pnl_percent is not None
      else None,
      related_trade_ids=list(model.related_trade_ids or []),
      source=model.source,
      pnl_quality=model.pnl_quality,
      quality_flags=list(model.quality_flags or []),
    )


@strawberry.type(description="已清仓持仓周期分页")
class ClosedPositionCyclePage:
  items: List[ClosedPositionCycle]
  total_count: int
  has_more: bool
