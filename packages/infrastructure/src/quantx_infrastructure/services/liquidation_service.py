"""
清仓管理服务
提供一键清仓、个股清仓和已清仓股票赎回等功能
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from quantx_domain.trading.exit_plan import (
  ExitEvaluationContext,
  ExitPlanBook,
  ExitPlanTemplate,
  ExitRuleSpec,
  ExitRuleType,
  ExitSizingMode,
  ExitSizingPolicy,
  ExitT1Policy,
)
from quantx_domain.trading.market_rules import AShareMarketRules

from quantx_infrastructure.core.data import market_data_service
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.enums import AccountType, OrderType, PriceType
from quantx_infrastructure.models.liquidation import (
  ConditionalLiquidationOrder,
  ConditionalLiquidationSellMode,
  ConditionalLiquidationStatus,
)
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.repositories.conditional_liquidation_order_repository import (
  ConditionalLiquidationOrderRepository,
)
from quantx_infrastructure.repositories.position_repository import PositionRepository
from quantx_infrastructure.services.position_service import PositionService
from quantx_infrastructure.services.trading_service import TradingService

logger = logging.getLogger(__name__)
DEFAULT_ACCOUNT_ID = "300000013250"


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


@dataclass
class ConditionalLiquidationEvaluation:
  """条件清仓单单次评估结果。"""

  order: ConditionalLiquidationOrder
  triggered: bool
  submitted: bool = False
  message: str = ""
  sell_volume: int = 0
  order_id: Optional[str] = None
  latest_price: Optional[float] = None
  profit_pct: Optional[float] = None
  error: Optional[str] = None

  def to_dict(self) -> Dict[str, Any]:
    return {
      "order": self.order.to_dict(),
      "triggered": self.triggered,
      "submitted": self.submitted,
      "message": self.message,
      "sell_volume": self.sell_volume,
      "order_id": self.order_id,
      "latest_price": self.latest_price,
      "profit_pct": self.profit_pct,
      "error": self.error,
    }


class LiquidationService:
  """清仓管理服务类"""

  def __init__(
    self,
    account_id: Optional[str] = None,
    account_type: AccountType = AccountType.STOCK,
  ):
    self.account_id = account_id or DEFAULT_ACCOUNT_ID
    self.account_type = account_type
    self.trading_service = TradingService(
      account_id=self.account_id,
      account_type=self.account_type,
    )
    self.position_service = PositionService()
    self.market_rules = AShareMarketRules()

  async def list_conditional_liquidation_orders(
    self,
    stock_code: Optional[str] = None,
    *,
    include_cancelled: bool = False,
  ) -> List[ConditionalLiquidationOrder]:
    """查询条件清仓单。"""
    normalized_stock_code = self._normalize_stock_code(stock_code)
    async for db in get_async_db():
      repo = ConditionalLiquidationOrderRepository(db)
      return await repo.find_all(
        account_id=str(getattr(self, "account_id", "") or ""),
        stock_code=normalized_stock_code or None,
        include_cancelled=include_cancelled,
      )
    return []

  async def upsert_conditional_liquidation_order(
    self,
    *,
    stock_code: str,
    target_profit_pct: Optional[float] = None,
    target_price: Optional[float] = None,
    sell_mode: str = ConditionalLiquidationSellMode.ALL_AVAILABLE,
    sell_ratio_pct: Optional[float] = None,
    sell_volume: Optional[int] = None,
    enabled: bool = True,
    order_id: Optional[str] = None,
    instrument_name: Optional[str] = None,
    remark: Optional[str] = None,
  ) -> ConditionalLiquidationOrder:
    """创建或更新某只持仓的条件清仓单。"""
    normalized_stock_code = self._normalize_stock_code(stock_code)
    normalized_sell_mode = self._normalize_sell_mode(sell_mode)
    self._validate_conditional_order_payload(
      stock_code=normalized_stock_code,
      target_profit_pct=target_profit_pct,
      target_price=target_price,
      sell_mode=normalized_sell_mode,
      sell_ratio_pct=sell_ratio_pct,
      sell_volume=sell_volume,
    )

    now = time_utils.now()
    payload = {
      "account_id": self.account_id,
      "account_type": self.account_type,
      "stock_code": normalized_stock_code,
      "instrument_name": instrument_name,
      "enabled": bool(enabled),
      "status": ConditionalLiquidationStatus.ACTIVE,
      "target_profit_pct": target_profit_pct,
      "target_price": target_price,
      "sell_mode": normalized_sell_mode,
      "sell_ratio_pct": sell_ratio_pct,
      "sell_volume": int(sell_volume) if sell_volume is not None else None,
      "triggered_at": None,
      "triggered_price": None,
      "triggered_profit_pct": None,
      "submitted_order_id": None,
      "submitted_volume": None,
      "last_error": None,
      "remark": remark,
    }

    async for db in get_async_db():
      repo = ConditionalLiquidationOrderRepository(db)
      order = await repo.find_by_id(order_id) if order_id else None
      if order is not None and order.account_id != self.account_id:
        raise LiquidationError("条件清仓单不属于当前资金账户")
      if order is None:
        order = await repo.find_latest_for_position(
          account_id=self.account_id,
          stock_code=normalized_stock_code,
        )

      if order is not None:
        if order.status == ConditionalLiquidationStatus.SUBMITTED:
          raise LiquidationError("已提交的条件清仓单不可修改，请重新创建")
        payload["last_checked_at"] = order.last_checked_at
        return await repo.update_order(order.id, payload)

      return await repo.create_order(
        ConditionalLiquidationOrder(
          id=str(uuid.uuid4()),
          created_at=now,
          updated_at=now,
          **payload,
        )
      )
    raise LiquidationError("条件清仓单保存失败")

  async def set_conditional_liquidation_order_enabled(
    self, order_id: str, enabled: bool
  ) -> Optional[ConditionalLiquidationOrder]:
    """启用或停用条件清仓单。"""
    async for db in get_async_db():
      repo = ConditionalLiquidationOrderRepository(db)
      order = await repo.find_by_id(order_id)
      if not order:
        return None
      if order.account_id != self.account_id:
        raise LiquidationError("条件清仓单不属于当前资金账户")
      if order.status in {
        ConditionalLiquidationStatus.SUBMITTED,
        ConditionalLiquidationStatus.CANCELLED,
      }:
        raise LiquidationError("已提交或已取消的条件清仓单不可启停")
      return await repo.update_order(
        order_id,
        {
          "enabled": bool(enabled),
          "status": ConditionalLiquidationStatus.ACTIVE,
          "last_error": None if enabled else order.last_error,
        },
      )
    return None

  async def cancel_conditional_liquidation_order(
    self, order_id: str
  ) -> Optional[ConditionalLiquidationOrder]:
    """取消条件清仓单。"""
    async for db in get_async_db():
      repo = ConditionalLiquidationOrderRepository(db)
      order = await repo.find_by_id(order_id)
      if not order:
        return None
      if order.account_id != self.account_id:
        raise LiquidationError("条件清仓单不属于当前资金账户")
      return await repo.update_order(
        order_id,
        {
          "enabled": False,
          "status": ConditionalLiquidationStatus.CANCELLED,
          "last_error": None,
        },
      )
    return None

  async def evaluate_conditional_liquidation_orders(
    self,
    *,
    stock_code: Optional[str] = None,
  ) -> List[ConditionalLiquidationEvaluation]:
    """扫描并评估启用的条件清仓单。"""
    normalized_stock_code = self._normalize_stock_code(stock_code)
    async for db in get_async_db():
      repo = ConditionalLiquidationOrderRepository(db)
      orders = await repo.find_active(
        account_id=str(getattr(self, "account_id", "") or ""),
        stock_code=normalized_stock_code or None,
      )
      break
    else:
      orders = []

    results = []
    for order in orders:
      results.append(await self.evaluate_conditional_liquidation_order(order))
    return results

  async def evaluate_conditional_liquidation_order(
    self,
    order: ConditionalLiquidationOrder,
  ) -> ConditionalLiquidationEvaluation:
    """评估单个条件清仓单，触发时提交一次卖出委托。"""
    now = time_utils.now()
    position = await self._get_position_for_condition(order.stock_code)
    latest_price = self._resolve_latest_price(position)
    triggered, reason, profit_pct = self.is_conditional_order_triggered(
      order,
      position,
      latest_price,
    )
    if not triggered:
      await self._update_conditional_order(
        order.id,
        {
          "last_checked_at": now,
          "last_error": reason if reason.startswith("missing_") else None,
        },
      )
      return ConditionalLiquidationEvaluation(
        order=order,
        triggered=False,
        latest_price=latest_price,
        profit_pct=profit_pct,
        message=reason,
        error=reason if reason.startswith("missing_") else None,
      )

    sell_volume = self.calculate_conditional_sell_volume(order, position)
    if sell_volume <= 0:
      await self._update_conditional_order(
        order.id,
        {
          "last_checked_at": now,
          "last_error": "no_legal_sell_volume",
        },
      )
      return ConditionalLiquidationEvaluation(
        order=order,
        triggered=True,
        submitted=False,
        latest_price=latest_price,
        profit_pct=profit_pct,
        message="no_legal_sell_volume",
        error="no_legal_sell_volume",
      )

    # 先停用，避免并发或下一轮监控重复提交。
    await self._update_conditional_order(
      order.id,
      {
        "enabled": False,
        "status": ConditionalLiquidationStatus.SUBMITTED,
        "triggered_at": now,
        "triggered_price": latest_price,
        "triggered_profit_pct": profit_pct,
        "submitted_volume": sell_volume,
        "last_checked_at": now,
        "last_error": None,
      },
    )

    close_position = sell_volume >= int(getattr(position, "can_use_volume", 0) or 0)
    result = await self._liquidate_single_position(
      position,
      max_retry=1,
      target_volume=sell_volume,
      close_position=close_position,
      strategy_name="条件清仓单",
      order_remark=f"条件清仓: {order.stock_code}",
    )
    if result.get("success"):
      order_id = result.get("order_id")
      await self._update_conditional_order(
        order.id,
        {
          "submitted_order_id": str(order_id) if order_id is not None else None,
          "last_error": None,
        },
      )
      return ConditionalLiquidationEvaluation(
        order=order,
        triggered=True,
        submitted=True,
        sell_volume=sell_volume,
        order_id=str(order_id) if order_id is not None else None,
        latest_price=latest_price,
        profit_pct=profit_pct,
        message=result.get("message", "条件清仓委托已提交"),
      )

    error = result.get("error") or result.get("message") or "conditional_order_failed"
    await self._update_conditional_order(
      order.id,
      {
        "status": ConditionalLiquidationStatus.FAILED,
        "last_error": error,
      },
    )
    return ConditionalLiquidationEvaluation(
      order=order,
      triggered=True,
      submitted=False,
      sell_volume=sell_volume,
      latest_price=latest_price,
      profit_pct=profit_pct,
      message=result.get("message", "条件清仓提交失败"),
      error=error,
    )

  def is_conditional_order_triggered(
    self,
    order: ConditionalLiquidationOrder,
    position: Optional[Position],
    latest_price: Optional[float],
  ) -> tuple[bool, str, Optional[float]]:
    """判断条件清仓单是否触发。"""
    if not position or int(getattr(position, "volume", 0) or 0) <= 0:
      return False, "missing_position", None
    if latest_price is None or latest_price <= 0:
      return False, "missing_latest_price", None

    avg_price = self._optional_float(getattr(position, "avg_price", None))
    target_profit_pct = self._optional_float(order.target_profit_pct)
    profit_pct = (
      (float(latest_price) / avg_price - 1.0) * 100.0
      if avg_price and avg_price > 0
      else None
    )
    target_price = self._optional_float(order.target_price)
    if avg_price is None and target_profit_pct is not None and target_price is None:
      return False, "missing_avg_price", None

    rules = []
    if target_profit_pct is not None and avg_price and avg_price > 0:
      rules.append(
        ExitRuleSpec(
          rule_id=f"{order.id}:profit",
          strategy=ExitRuleType.GROSS_TAKE_PROFIT,
          priority=500,
          parameters={"target_profit_pct": target_profit_pct},
        )
      )
    if target_price is not None:
      rules.append(
        ExitRuleSpec(
          rule_id=f"{order.id}:price",
          strategy=ExitRuleType.TARGET_PRICE,
          priority=500,
          parameters={"target_price": target_price},
        )
      )
    if not rules:
      return False, "not_triggered", profit_pct
    plan_book = ExitPlanBook()
    plan_book.register_entry_fill(
      ExitPlanTemplate(
        plan_id=f"conditional-liquidation:{order.id}",
        source_type="MANUAL_POSITION",
        source_id=str(order.id),
        account_id=str(getattr(self, "account_id", "") or ""),
        instrument_code=order.stock_code,
        bucket="default",
        rules=rules,
        t1_policy=ExitT1Policy.WAIT_UNTIL_SELLABLE,
        auto_exit_authorized=True,
      ),
      volume=max(
        1,
        int(
          getattr(position, "volume", 0)
          or getattr(position, "can_use_volume", 0)
          or 0
        ),
      ),
      price=float(avg_price or latest_price),
      trade_time=(
        getattr(position, "created_at", None)
        if isinstance(getattr(position, "created_at", None), datetime)
        else None
      ),
    )
    decisions = plan_book.evaluate(
      order.stock_code,
      ExitEvaluationContext(
        timestamp=time_utils.now(),
        current_price=float(latest_price),
      ),
    )
    if not decisions:
      return False, "not_triggered", profit_pct
    decision = decisions[0]
    reason = (
      "target_profit_pct_reached"
      if decision.rule_id.endswith(":profit")
      else "target_price_reached"
    )
    return True, reason, profit_pct

  def calculate_conditional_sell_volume(
    self,
    order: ConditionalLiquidationOrder,
    position: Optional[Position],
  ) -> int:
    """计算条件清仓单最终卖出数量。"""
    if not position:
      return 0
    available = max(0, int(getattr(position, "can_use_volume", 0) or 0))
    if available <= 0:
      return 0

    sell_mode = self._normalize_sell_mode(order.sell_mode)
    if sell_mode == ConditionalLiquidationSellMode.ALL_AVAILABLE:
      sizing = ExitSizingPolicy(mode=ExitSizingMode.ALL_REMAINING)
    elif sell_mode == ConditionalLiquidationSellMode.PERCENT_AVAILABLE:
      sizing = ExitSizingPolicy(
        mode=ExitSizingMode.PERCENT_REMAINING,
        value=self._optional_float(order.sell_ratio_pct) or 0.0,
        allow_odd_lot_full_exit=False,
      )
    else:
      sizing = ExitSizingPolicy(
        mode=ExitSizingMode.FIXED_VOLUME,
        value=int(order.sell_volume or 0),
        allow_odd_lot_full_exit=False,
      )
    return sizing.calculate(available)

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
          liquidation_result = await self._liquidate_single_position(position, max_retry)

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
      result.message = (
        f"清仓委托提交完成: 成功{result.liquidated_positions}个, "
        f"失败{result.failed_positions}个"
      )

      logger.info(f"一键清仓委托提交完成: {result.message}")
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
      if int(position.can_use_volume or 0) <= 0:
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

      # 股票清仓后的现金已经属于证券账户可用资金。当前系统没有独立的
      # 资金赎回通道，不能把一次未发生的外部资金划转伪装成成功。
      raise LiquidationError(
        f"资金赎回通道尚未配置，未执行 {redeem_amount} 元资金划转"
      )

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
      # 获取当前账户所有持仓
      all_positions = await self._get_all_positions()

      # 筛选可清仓持仓
      liquidatable_positions = [
        pos for pos in all_positions if int(pos.can_use_volume or 0) > 0
      ]

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
    all_positions = await self._get_all_positions()
    return [pos for pos in all_positions if int(pos.can_use_volume or 0) > 0]

  async def _get_all_positions(self) -> List[Position]:
    """获取当前账户所有持仓"""
    async for db in get_async_db():
      position_repo = PositionRepository(db)
      return await position_repo.find_all(account_id=self.account_id)
    return []

  async def _get_position(self, stock_code: str) -> Optional[Position]:
    """获取指定股票的持仓"""
    async for db in get_async_db():
      position_repo = PositionRepository(db)
      return await position_repo.find_by_stock_code(
        stock_code,
        account_id=self.account_id,
        account_type=self.account_type,
      )
    return None

  async def _get_position_for_condition(self, stock_code: str) -> Optional[Position]:
    """获取带最新价的持仓用于条件清仓单评估。"""
    position = await self._get_position(stock_code)
    if not position:
      return None
    try:
      latest_tick = await market_data_service.get_latest_price(stock_code)
      latest_price = self._optional_float(getattr(latest_tick, "last_price", None))
      if latest_price is not None and latest_price > 0:
        position.last_price = latest_price
    except Exception as exc:
      logger.warning("条件清仓获取最新价失败: %s, %s", stock_code, exc)
    return position

  async def _liquidate_single_position(
    self,
    position: Position,
    max_retry: int = 3,
    *,
    target_volume: Optional[int] = None,
    close_position: Optional[bool] = None,
    strategy_name: str = "清仓操作",
    order_remark: Optional[str] = None,
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
        available_volume = int(position.can_use_volume or 0)
        close_volume = int(target_volume or available_volume)
        close_volume = min(close_volume, available_volume)
        is_close_position = (
          close_volume >= available_volume if close_position is None else close_position
        )
        # 下卖出订单
        order_result = await self.trading_service.place_order(
          stock_code=position.stock_code,
          order_type=OrderType.SELL,
          order_volume=close_volume,
          price_type=PriceType.MARKET_CONVERT_5_LIMIT,
          price=0,  # 市价单
          strategy_name=strategy_name,
          order_remark=order_remark or f"清仓: {position.stock_code}",
          close_position=is_close_position,
        )

        if order_result["success"]:
          order_id = order_result.get("order_id") or order_result.get(
            "client_order_id"
          )
          return {
            "success": True,
            "stock_code": position.stock_code,
            "volume": close_volume,
            "order_id": str(order_id) if order_id is not None else None,
            "orders": [order_result],
            "message": f"清仓委托已提交: {position.stock_code}",
          }
        else:
          last_error = order_result.get("error") or order_result.get(
            "message", "下单失败"
          )

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

  async def _update_conditional_order(
    self, order_id: str, updates: Dict[str, Any]
  ) -> Optional[ConditionalLiquidationOrder]:
    async for db in get_async_db():
      repo = ConditionalLiquidationOrderRepository(db)
      return await repo.update_order(order_id, updates)
    return None

  def _resolve_latest_price(self, position: Optional[Position]) -> Optional[float]:
    if not position:
      return None
    latest_price = self._optional_float(getattr(position, "last_price", None))
    return latest_price if latest_price and latest_price > 0 else None

  def _validate_conditional_order_payload(
    self,
    *,
    stock_code: str,
    target_profit_pct: Optional[float],
    target_price: Optional[float],
    sell_mode: str,
    sell_ratio_pct: Optional[float],
    sell_volume: Optional[int],
  ) -> None:
    if not stock_code:
      raise LiquidationError("条件清仓单必须绑定股票代码")
    if target_profit_pct is None and target_price is None:
      raise LiquidationError("条件清仓单至少需要目标收益率或目标价")
    if target_profit_pct is not None and float(target_profit_pct) < 0:
      raise LiquidationError("目标收益率不能为负数")
    if target_price is not None and float(target_price) <= 0:
      raise LiquidationError("目标价必须大于0")
    if sell_mode == ConditionalLiquidationSellMode.PERCENT_AVAILABLE:
      if sell_ratio_pct is None or float(sell_ratio_pct) <= 0:
        raise LiquidationError("按比例卖出必须填写卖出比例")
      if float(sell_ratio_pct) > 100:
        raise LiquidationError("卖出比例不能超过100%")
    if sell_mode == ConditionalLiquidationSellMode.FIXED_VOLUME:
      if sell_volume is None or int(sell_volume) <= 0:
        raise LiquidationError("固定股数卖出必须填写有效股数")

  def _normalize_sell_mode(self, value: str) -> str:
    text = str(value or ConditionalLiquidationSellMode.ALL_AVAILABLE).upper()
    if text not in {
      ConditionalLiquidationSellMode.ALL_AVAILABLE,
      ConditionalLiquidationSellMode.PERCENT_AVAILABLE,
      ConditionalLiquidationSellMode.FIXED_VOLUME,
    }:
      raise LiquidationError(f"不支持的条件清仓卖出模式: {value}")
    return text

  def _normalize_stock_code(self, value: Optional[str]) -> str:
    return str(value or "").strip().upper()

  def _optional_float(self, value: Any) -> Optional[float]:
    try:
      if value is None:
        return None
      return float(value)
    except (TypeError, ValueError):
      return None
