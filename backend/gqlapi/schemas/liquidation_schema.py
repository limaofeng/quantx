"""
清仓管理相关的GraphQL查询和变更定义
"""

from typing import List, Optional

import strawberry

from ..resolvers.liquidation import LiquidationResolver
from ..types import MessageResponse
from ..types.liquidation_types import (
  ConditionalLiquidationEvaluationResult,
  ConditionalLiquidationOrder,
  ConditionalLiquidationOrderInput,
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
  async def liquidation_summary(
    self, account_id: Optional[str] = None
  ) -> LiquidationSummary:
    return await LiquidationResolver.get_liquidation_summary(account_id)

  @strawberry.field(description="获取条件清仓单列表")
  async def conditional_liquidation_orders(
    self,
    account_id: Optional[str] = None,
    stock_code: Optional[str] = None,
    include_cancelled: bool = False,
  ) -> List[ConditionalLiquidationOrder]:
    return await LiquidationResolver.get_conditional_liquidation_orders(
      account_id,
      stock_code,
      include_cancelled,
    )

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

  @strawberry.mutation(description="创建或更新条件清仓单")
  async def upsert_conditional_liquidation_order(
    self,
    input: ConditionalLiquidationOrderInput,
  ) -> ConditionalLiquidationOrder:
    return await LiquidationResolver.upsert_conditional_liquidation_order(input)

  @strawberry.mutation(description="启用或停用条件清仓单")
  async def set_conditional_liquidation_order_enabled(
    self,
    order_id: str,
    enabled: bool,
  ) -> Optional[ConditionalLiquidationOrder]:
    return await LiquidationResolver.set_conditional_liquidation_order_enabled(
      order_id,
      enabled,
    )

  @strawberry.mutation(description="取消条件清仓单")
  async def cancel_conditional_liquidation_order(
    self,
    order_id: str,
  ) -> Optional[ConditionalLiquidationOrder]:
    return await LiquidationResolver.cancel_conditional_liquidation_order(order_id)

  @strawberry.mutation(description="立即评估条件清仓单")
  async def evaluate_conditional_liquidation_orders(
    self,
    account_id: Optional[str] = None,
    stock_code: Optional[str] = None,
  ) -> List[ConditionalLiquidationEvaluationResult]:
    return await LiquidationResolver.evaluate_conditional_liquidation_orders(
      account_id,
      stock_code,
    )

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
