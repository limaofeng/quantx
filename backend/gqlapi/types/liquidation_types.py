"""
清仓管理相关的GraphQL类型定义
"""

from typing import List, Optional

import strawberry


@strawberry.type(description="清仓结果")
class LiquidationResult:
  success: bool = strawberry.field(description="是否成功")
  total_positions: int = strawberry.field(description="总持仓数量")
  liquidated_positions: int = strawberry.field(description="已清仓数量")
  failed_positions: int = strawberry.field(description="失败数量")
  message: str = strawberry.field(description="结果消息")

  @strawberry.field(description="订单列表")
  def orders(self) -> List[str]:
    return []

  @strawberry.field(description="错误列表")
  def errors(self) -> List["LiquidationError"]:
    return []


@strawberry.type(description="清仓错误")
class LiquidationError:
  stock_code: str = strawberry.field(description="股票代码")
  error: str = strawberry.field(description="错误信息")


@strawberry.type(description="个股清仓结果")
class PositionLiquidationResult:
  success: bool = strawberry.field(description="是否成功")
  stock_code: str = strawberry.field(description="股票代码")
  volume: Optional[int] = strawberry.field(description="清仓数量")
  order_id: Optional[str] = strawberry.field(description="订单ID")
  message: str = strawberry.field(description="结果消息")
  error: Optional[str] = strawberry.field(description="错误信息")


@strawberry.type(description="资金赎回结果")
class RedemptionResult:
  success: bool = strawberry.field(description="是否成功")
  stock_code: str = strawberry.field(description="股票代码")
  redeemed_amount: Optional[float] = strawberry.field(description="赎回金额")
  remaining_amount: Optional[float] = strawberry.field(description="剩余金额")
  message: str = strawberry.field(description="结果消息")
  error: Optional[str] = strawberry.field(description="错误信息")


@strawberry.type(description="清仓概况")
class LiquidationSummary:
  total_positions: int = strawberry.field(description="总持仓数量")
  liquidatable_positions: int = strawberry.field(description="可清仓持仓数量")
  total_market_value: float = strawberry.field(description="总市值")

  @strawberry.field(description="持仓列表")
  def positions(self) -> List["LiquidatablePosition"]:
    return []


@strawberry.type(description="可清仓持仓")
class LiquidatablePosition:
  stock_code: str = strawberry.field(description="股票代码")
  instrument_name: Optional[str] = strawberry.field(description="证券名称")
  volume: int = strawberry.field(description="持仓数量")
  can_use_volume: int = strawberry.field(description="可用数量")
  market_value: float = strawberry.field(description="市值")
  avg_price: Optional[float] = strawberry.field(description="平均成本价")


@strawberry.type(description="清仓订单")
class LiquidationOrder:
  id: str = strawberry.field(description="清仓订单ID")
  account_id: str = strawberry.field(description="资金账号")
  liquidation_type: str = strawberry.field(description="清仓类型")
  status: str = strawberry.field(description="清仓状态")
  stock_code: Optional[str] = strawberry.field(description="股票代码")
  instrument_name: Optional[str] = strawberry.field(description="证券名称")
  target_volume: Optional[int] = strawberry.field(description="目标清仓数量")
  completed_volume: int = strawberry.field(description="已完成数量")
  target_amount: Optional[float] = strawberry.field(description="目标清仓金额")
  completed_amount: float = strawberry.field(description="已完成金额")
  start_time: Optional[str] = strawberry.field(description="开始执行时间")
  end_time: Optional[str] = strawberry.field(description="结束时间")
  retry_count: int = strawberry.field(description="重试次数")
  remark: Optional[str] = strawberry.field(description="备注信息")
  error_message: Optional[str] = strawberry.field(description="错误信息")
  created_at: Optional[str] = strawberry.field(description="创建时间")


@strawberry.type(description="赎回记录")
class RedemptionRecord:
  id: str = strawberry.field(description="赎回记录ID")
  account_id: str = strawberry.field(description="资金账号")
  stock_code: str = strawberry.field(description="股票代码")
  instrument_name: Optional[str] = strawberry.field(description="证券名称")
  redemption_amount: float = strawberry.field(description="赎回金额")
  available_amount: Optional[float] = strawberry.field(description="可赎回金额")
  redeemed_amount: float = strawberry.field(description="已赎回金额")
  status: str = strawberry.field(description="赎回状态")
  redemption_date: Optional[str] = strawberry.field(description="赎回日期")
  expected_arrival_date: Optional[str] = strawberry.field(description="预计到账日期")
  actual_arrival_date: Optional[str] = strawberry.field(description="实际到账日期")
  redemption_fee: float = strawberry.field(description="赎回费用")
  remark: Optional[str] = strawberry.field(description="备注信息")
  created_at: Optional[str] = strawberry.field(description="创建时间")


# 输入类型
@strawberry.input(description="一键清仓输入")
class LiquidateAllPositionsInput:
  confirm: bool = strawberry.field(description="风险确认")
  max_retry: int = strawberry.field(default=3, description="最大重试次数")


@strawberry.input(description="个股清仓输入")
class LiquidatePositionInput:
  stock_code: str = strawberry.field(description="股票代码")
  confirm: bool = strawberry.field(description="风险确认")
  max_retry: int = strawberry.field(default=3, description="最大重试次数")


@strawberry.input(description="资金赎回输入")
class RedeemPositionInput:
  stock_code: str = strawberry.field(description="股票代码")
  amount: Optional[float] = strawberry.field(
    default=None, description="赎回金额，为空则赎回全部"
  )
