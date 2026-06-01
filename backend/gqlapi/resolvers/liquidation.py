"""
清仓管理相关的GraphQL解析器
"""

from typing import List, Optional

from services.liquidation_service import LiquidationService

from ..types.liquidation_types import (
  LiquidatablePosition,
  LiquidateAllPositionsInput,
  LiquidatePositionInput,
  LiquidationError,
  LiquidationOrder,
  LiquidationResult,
  LiquidationSummary,
  PositionLiquidationResult,
  RedeemPositionInput,
  RedemptionRecord,
  RedemptionResult,
)


class LiquidationResolver:
  """清仓管理解析器"""

  @staticmethod
  async def get_liquidation_summary() -> LiquidationSummary:
    """获取清仓概况"""
    liquidation_service = LiquidationService()
    summary_data = await liquidation_service.get_liquidation_summary()

    # 转换持仓数据
    positions = [
      LiquidatablePosition(
        stock_code=pos["stock_code"],
        instrument_name=pos.get("instrument_name"),
        volume=pos["volume"],
        can_use_volume=pos["can_use_volume"],
        market_value=pos["market_value"],
        avg_price=pos.get("avg_price"),
      )
      for pos in summary_data["positions"]
    ]

    summary = LiquidationSummary(
      total_positions=summary_data["total_positions"],
      liquidatable_positions=summary_data["liquidatable_positions"],
      total_market_value=summary_data["total_market_value"],
    )
    summary.positions = lambda: positions

    return summary

  @staticmethod
  async def liquidate_all_positions(
    input: LiquidateAllPositionsInput,
  ) -> LiquidationResult:
    """一键清仓"""
    liquidation_service = LiquidationService()

    # 执行一键清仓
    result_data = await liquidation_service.liquidate_all_positions(
      confirm=input.confirm, max_retry=input.max_retry
    )

    # 转换错误信息
    errors = [
      LiquidationError(stock_code=error["stock_code"], error=error["error"])
      for error in result_data.errors
    ]

    result = LiquidationResult(
      success=result_data.success,
      total_positions=result_data.total_positions,
      liquidated_positions=result_data.liquidated_positions,
      failed_positions=result_data.failed_positions,
      message=result_data.message,
    )
    result.errors = lambda: errors
    result.orders = lambda: []  # 这里可以根据需要返回订单信息

    return result

  @staticmethod
  async def liquidate_position(
    input: LiquidatePositionInput,
  ) -> PositionLiquidationResult:
    """个股清仓"""
    liquidation_service = LiquidationService()

    # 执行个股清仓
    result_data = await liquidation_service.liquidate_position(
      stock_code=input.stock_code, confirm=input.confirm, max_retry=input.max_retry
    )

    return PositionLiquidationResult(
      success=result_data["success"],
      stock_code=result_data["stock_code"],
      volume=result_data.get("volume"),
      order_id=result_data.get("order_id"),
      message=result_data["message"],
      error=result_data.get("error"),
    )

  @staticmethod
  async def redeem_cleared_position(input: RedeemPositionInput) -> RedemptionResult:
    """已清仓股票资金赎回"""
    liquidation_service = LiquidationService()

    # 执行资金赎回
    result_data = await liquidation_service.redeem_cleared_position(
      stock_code=input.stock_code, amount=input.amount
    )

    return RedemptionResult(
      success=result_data["success"],
      stock_code=result_data["stock_code"],
      redeemed_amount=result_data.get("redeemed_amount"),
      remaining_amount=result_data.get("remaining_amount"),
      message=result_data["message"],
      error=result_data.get("error"),
    )

  @staticmethod
  async def get_liquidation_orders(
    account_id: str, limit: int = 20, offset: int = 0
  ) -> List[LiquidationOrder]:
    """获取清仓订单列表"""
    # 这里需要实现数据库查询逻辑
    # 暂时返回空列表
    return []

  @staticmethod
  async def get_liquidation_order(
    order_id: str, account_id: str
  ) -> Optional[LiquidationOrder]:
    """获取单个清仓订单"""
    # 这里需要实现数据库查询逻辑
    # 暂时返回None
    return None

  @staticmethod
  async def get_redemption_records(
    account_id: str, stock_code: Optional[str] = None, limit: int = 20, offset: int = 0
  ) -> List[RedemptionRecord]:
    """获取赎回记录列表"""
    # 这里需要实现数据库查询逻辑
    # 暂时返回空列表
    return []

  @staticmethod
  async def cancel_liquidation_order(order_id: str, account_id: str) -> dict:
    """取消清仓订单"""
    # 这里需要实现取消订单的逻辑
    # 暂时返回成功结果
    return {"success": True, "message": f"清仓订单 {order_id} 已取消"}
