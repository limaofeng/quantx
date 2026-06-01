"""
清仓管理相关的GraphQL查询和变更定义
"""

from typing import List

import strawberry

from ..resolvers.liquidation import LiquidationResolver
from ..types import MessageResponse
from ..types.liquidation_types import (
  LiquidateAllPositionsInput,
  LiquidatePositionInput,
  LiquidationOrder,
  LiquidationResult,
  LiquidationSummary,
  PositionLiquidationResult,
  RedeemPositionInput,
  RedemptionRecord,
  RedemptionResult,
)


@strawberry.type(description="清仓管理相关查询")
class LiquidationQuery:
  @strawberry.field(description="获取清仓概况")
  async def liquidation_summary(self) -> LiquidationSummary:
    return await LiquidationResolver.get_liquidation_summary()

  @strawberry.field(description="获取清仓订单列表")
  async def liquidation_orders(
    self, account_id: str = "300000013250", limit: int = 20, offset: int = 0
  ) -> List[LiquidationOrder]:
    return await LiquidationResolver.get_liquidation_orders(account_id, limit, offset)

  @strawberry.field(description="获取单个清仓订单")
  async def liquidation_order(
    self, order_id: str, account_id: str = "300000013250"
  ) -> LiquidationOrder:
    return await LiquidationResolver.get_liquidation_order(order_id, account_id)

  @strawberry.field(description="获取赎回记录列表")
  async def redemption_records(
    self,
    account_id: str = "300000013250",
    stock_code: str = None,
    limit: int = 20,
    offset: int = 0,
  ) -> List[RedemptionRecord]:
    return await LiquidationResolver.get_redemption_records(
      account_id, stock_code, limit, offset
    )


@strawberry.type(description="清仓管理相关变更")
class LiquidationMutation:
  @strawberry.mutation(description="一键清仓")
  async def liquidate_all_positions(
    self, input: LiquidateAllPositionsInput
  ) -> LiquidationResult:
    return await LiquidationResolver.liquidate_all_positions(input)

  @strawberry.mutation(description="个股清仓")
  async def liquidate_position(
    self, input: LiquidatePositionInput
  ) -> PositionLiquidationResult:
    return await LiquidationResolver.liquidate_position(input)

  @strawberry.mutation(description="已清仓股票资金赎回")
  async def redeem_cleared_position(
    self, input: RedeemPositionInput
  ) -> RedemptionResult:
    return await LiquidationResolver.redeem_cleared_position(input)

  @strawberry.mutation(description="取消清仓订单")
  async def cancel_liquidation_order(
    self, order_id: str, account_id: str = "300000013250"
  ) -> MessageResponse:
    return await LiquidationResolver.cancel_liquidation_order(order_id, account_id)
