"""
清仓管理服务
提供一键清仓、个股清仓和已清仓股票赎回等功能
"""

import logging
from typing import Any, Dict, List, Optional

from database.connection import get_async_db
from miniqmt.trading.trading_manager import OrderType
from models.enums import AccountType, PriceType
from models.position import Position
from repositories.position_repository import PositionRepository
from services.position_service import PositionService
from services.trading_service import TradingService

logger = logging.getLogger(__name__)


class LiquidationError(Exception):
  """清仓异常基类"""

  pass


class InsufficientLiquidatablePositionError(LiquidationError):
  """可清仓持仓不足异常"""

  pass


class LiquidationResult:
  """清仓结果"""

  def __init__(self):
    self.success = False
    self.total_positions = 0
    self.liquidated_positions = 0
    self.failed_positions = 0
    self.orders = []
    self.errors = []
    self.message = ""

  def to_dict(self):
    return {
      "success": self.success,
      "total_positions": self.total_positions,
      "liquidated_positions": self.liquidated_positions,
      "failed_positions": self.failed_positions,
      "orders": self.orders,
      "errors": self.errors,
      "message": self.message,
    }


class LiquidationService:
  """清仓管理服务类"""

  def __init__(self):
    self.trading_service = TradingService()
    self.position_service = PositionService()
    self.account_id = "300000013250"
    self.account_type = AccountType.STOCK

  async def liquidate_all_positions(
    self, confirm: bool = False, max_retry: int = 3
  ) -> LiquidationResult:
    """
    一键清仓 - 清空所有持仓

    Args:
        confirm: 风险确认标志，必须为True才能执行
        max_retry: 最大重试次数

    Returns:
        LiquidationResult: 清仓结果
    """
    result = LiquidationResult()

    try:
      # 风险确认检查
      if not confirm:
        raise LiquidationError("必须确认风险才能执行一键清仓操作")

      # 获取所有可清仓持仓
      liquidatable_positions = await self._get_liquidatable_positions()
      result.total_positions = len(liquidatable_positions)

      if not liquidatable_positions:
        result.success = True
        result.message = "没有可清仓的持仓"
        return result

      logger.info(f"开始一键清仓，共{result.total_positions}个持仓")

      # 批量清仓
      for position in liquidatable_positions:
        try:
          liquidation_result = await self._liquidate_single_position(
            position, max_retry
          )

          if liquidation_result["success"]:
            result.liquidated_positions += 1
            result.orders.extend(liquidation_result.get("orders", []))
          else:
            result.failed_positions += 1
            result.errors.append(
              {
                "stock_code": position.stock_code,
                "error": liquidation_result.get("error", "未知错误"),
              }
            )

        except Exception as e:
          result.failed_positions += 1
          result.errors.append({"stock_code": position.stock_code, "error": str(e)})
          logger.error(f"清仓失败 - 股票: {position.stock_code}, 错误: {str(e)}")

      # 设置最终结果
      result.success = result.failed_positions == 0
      result.message = f"清仓完成: 成功{result.liquidated_positions}个, 失败{result.failed_positions}个"

      logger.info(f"一键清仓完成: {result.message}")
      return result

    except Exception as e:
      result.success = False
      result.message = f"一键清仓失败: {str(e)}"
      logger.error(result.message)
      return result

  async def liquidate_position(
    self, stock_code: str, confirm: bool = False, max_retry: int = 3
  ) -> Dict[str, Any]:
    """
    个股清仓 - 清空指定股票的持仓

    Args:
        stock_code: 股票代码
        confirm: 风险确认标志
        max_retry: 最大重试次数

    Returns:
        Dict: 清仓结果
    """
    try:
      # 风险确认检查
      if not confirm:
        raise LiquidationError("必须确认风险才能执行清仓操作")

      # 获取持仓信息
      position = await self._get_position(stock_code)
      if not position:
        raise LiquidationError(f"未找到股票 {stock_code} 的持仓")

      # 检查是否有可清仓数量
      if position.can_use_volume <= 0:
        raise InsufficientLiquidatablePositionError(
          f"股票 {stock_code} 没有可清仓的持仓数量"
        )

      logger.info(f"开始清仓股票: {stock_code}, 数量: {position.can_use_volume}")

      # 执行清仓
      return await self._liquidate_single_position(position, max_retry)

    except Exception as e:
      error_msg = f"个股清仓失败 - 股票: {stock_code}, 错误: {str(e)}"
      logger.error(error_msg)
      return {
        "success": False,
        "stock_code": stock_code,
        "error": str(e),
        "message": error_msg,
      }

  async def redeem_cleared_position(
    self, stock_code: str, amount: Optional[float] = None
  ) -> Dict[str, Any]:
    """
    已清仓股票的赎回操作

    Args:
        stock_code: 股票代码
        amount: 赎回金额，如果为None则赎回全部

    Returns:
        Dict: 赎回结果
    """
    try:
      # 验证股票是否已清仓
      position = await self._get_position(stock_code)
      if position and position.volume > 0:
        raise LiquidationError(f"股票 {stock_code} 仍有持仓，无法执行赎回")

      # 计算可赎回金额
      redeemable_amount = await self._calculate_redeemable_amount(stock_code)
      if redeemable_amount <= 0:
        raise LiquidationError(f"股票 {stock_code} 没有可赎回的资金")

      # 确定赎回金额
      redeem_amount = amount if amount else redeemable_amount
      if redeem_amount > redeemable_amount:
        raise LiquidationError(
          f"赎回金额 {redeem_amount} 超过可赎回金额 {redeemable_amount}"
        )

      # 执行赎回操作
      logger.info(f"开始赎回资金: 股票{stock_code}, 金额: {redeem_amount}")

      # 这里需要根据具体的赎回接口实现
      # 暂时返回模拟结果
      return {
        "success": True,
        "stock_code": stock_code,
        "redeemed_amount": redeem_amount,
        "remaining_amount": redeemable_amount - redeem_amount,
        "message": f"资金赎回成功: {redeem_amount}元",
      }

    except Exception as e:
      error_msg = f"资金赎回失败 - 股票: {stock_code}, 错误: {str(e)}"
      logger.error(error_msg)
      return {
        "success": False,
        "stock_code": stock_code,
        "error": str(e),
        "message": error_msg,
      }

  async def get_liquidation_summary(self) -> Dict[str, Any]:
    """
    获取清仓概况

    Returns:
        Dict: 清仓概况信息
    """
    try:
      # 获取所有持仓
      all_positions = await self.position_service.get_positions()

      # 筛选可清仓持仓
      liquidatable_positions = [pos for pos in all_positions if pos.can_use_volume > 0]

      # 计算总市值
      total_market_value = sum(pos.market_value or 0 for pos in liquidatable_positions)

      # 计算可清仓股票数
      liquidatable_count = len(liquidatable_positions)

      return {
        "total_positions": len(all_positions),
        "liquidatable_positions": liquidatable_count,
        "total_market_value": float(total_market_value),
        "positions": [
          {
            "stock_code": pos.stock_code,
            "instrument_name": pos.instrument_name,
            "volume": pos.volume,
            "can_use_volume": pos.can_use_volume,
            "market_value": float(pos.market_value) if pos.market_value else 0,
            "avg_price": float(pos.avg_price) if pos.avg_price else 0,
          }
          for pos in liquidatable_positions
        ],
      }

    except Exception as e:
      logger.error(f"获取清仓概况失败: {str(e)}")
      raise

  # 私有方法
  async def _get_liquidatable_positions(self) -> List[Position]:
    """获取所有可清仓的持仓"""
    all_positions = await self.position_service.get_positions()
    return [pos for pos in all_positions if pos.can_use_volume > 0]

  async def _get_position(self, stock_code: str) -> Optional[Position]:
    """获取指定股票的持仓"""
    async for db in get_async_db():
      position_repo = PositionRepository(db)
      return await position_repo.find_by_stock_code(
        self.account_id, self.account_type, stock_code
      )

  async def _liquidate_single_position(
    self, position: Position, max_retry: int = 3
  ) -> Dict[str, Any]:
    """
    清仓单个持仓

    Args:
        position: 持仓对象
        max_retry: 最大重试次数

    Returns:
        Dict: 清仓结果
    """
    retry_count = 0
    last_error = None

    while retry_count < max_retry:
      try:
        # 下卖出订单
        order_result = await self.trading_service.place_order(
          stock_code=position.stock_code,
          order_type=OrderType.SELL,
          order_volume=position.can_use_volume,
          price_type=PriceType.MARKET_CONVERT_5_LIMIT,
          price=0,  # 市价单
          strategy_name="清仓操作",
          order_remark=f"清仓: {position.stock_code}",
        )

        if order_result["success"]:
          return {
            "success": True,
            "stock_code": position.stock_code,
            "volume": position.can_use_volume,
            "order_id": order_result.get("order_id"),
            "orders": [order_result],
            "message": f"清仓成功: {position.stock_code}",
          }
        else:
          last_error = order_result.get("error", "下单失败")

      except Exception as e:
        last_error = str(e)

      retry_count += 1
      if retry_count < max_retry:
        logger.warning(
          f"清仓重试 {retry_count}/{max_retry} - 股票: {position.stock_code}, 错误: {last_error}"
        )

    # 所有重试都失败
    return {
      "success": False,
      "stock_code": position.stock_code,
      "error": last_error,
      "message": f"清仓失败: {position.stock_code}, 已重试{max_retry}次",
    }

  async def _calculate_redeemable_amount(self, stock_code: str) -> float:
    """计算可赎回金额"""
    # 这里需要根据具体业务逻辑实现
    # 可能需要查询交易记录、计算已实现盈亏等
    # 暂时返回模拟值
    return 0.0

  async def _validate_liquidation_risk(self, positions: List[Position]) -> None:
    """验证清仓风险"""
    total_market_value = sum(pos.market_value or 0 for pos in positions)

    # 设置风险阈值，比如单次清仓金额不超过100万
    max_liquidation_value = 1000000

    if total_market_value > max_liquidation_value:
      raise LiquidationError(
        f"清仓金额 {total_market_value} 超过风险阈值 {max_liquidation_value}"
      )
