"""
清仓管理相关的GraphQL解析器
"""

import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import List, Optional

from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.liquidation import (
  ConditionalLiquidationOrder as ConditionalLiquidationOrderModel,
)
from quantx_infrastructure.models.liquidation import (
  LiquidationOrder as LiquidationOrderModel,
)
from quantx_infrastructure.models.liquidation import (
  LiquidationStatus,
)
from quantx_infrastructure.models.liquidation import (
  RedemptionRecord as RedemptionRecordModel,
)
from quantx_infrastructure.services.engine_command_service import engine_command_service
from quantx_infrastructure.services.liquidation_service import LiquidationService
from sqlalchemy import desc, select

from ..types import MessageResponse
from ..types.liquidation_types import (
  ConditionalLiquidationEvaluationResult,
  ConditionalLiquidationOrder,
  ConditionalLiquidationOrderInput,
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
  def _liquidation_order(model: LiquidationOrderModel) -> LiquidationOrder:
    return LiquidationOrder(
      id=model.id,
      account_id=model.account_id,
      liquidation_type=model.liquidation_type,
      status=model.status,
      stock_code=model.stock_code,
      instrument_name=model.instrument_name,
      target_volume=model.target_volume,
      completed_volume=int(model.completed_volume or 0),
      target_amount=float(model.target_amount)
      if model.target_amount is not None
      else None,
      completed_amount=float(model.completed_amount or 0),
      start_time=model.start_time.isoformat() if model.start_time else None,
      end_time=model.end_time.isoformat() if model.end_time else None,
      retry_count=int(model.retry_count or 0),
      remark=model.remark,
      error_message=model.error_message,
      created_at=model.created_at.isoformat() if model.created_at else None,
    )

  @staticmethod
  def _redemption_record(model: RedemptionRecordModel) -> RedemptionRecord:
    return RedemptionRecord(
      id=model.id,
      account_id=model.account_id,
      stock_code=model.stock_code,
      instrument_name=model.instrument_name,
      redemption_amount=float(model.redemption_amount),
      available_amount=float(model.available_amount)
      if model.available_amount is not None
      else None,
      redeemed_amount=float(model.redeemed_amount or 0),
      status=model.status,
      redemption_date=model.redemption_date.isoformat()
      if model.redemption_date
      else None,
      expected_arrival_date=model.expected_arrival_date.isoformat()
      if model.expected_arrival_date
      else None,
      actual_arrival_date=model.actual_arrival_date.isoformat()
      if model.actual_arrival_date
      else None,
      redemption_fee=float(model.redemption_fee or 0),
      remark=model.remark,
      created_at=model.created_at.isoformat() if model.created_at else None,
    )

  @staticmethod
  def _conditional_evaluation_result(
    result,
  ) -> ConditionalLiquidationEvaluationResult:
    if isinstance(result, dict):
      order_data = dict(result.get("order") or {})
      for field in (
        "triggered_at",
        "last_checked_at",
        "created_at",
        "updated_at",
      ):
        value = order_data.get(field)
        if isinstance(value, str):
          order_data[field] = datetime.fromisoformat(value.replace("Z", "+00:00"))
      result = SimpleNamespace(
        **{
          **result,
          "order": SimpleNamespace(**order_data),
        }
      )
    return ConditionalLiquidationEvaluationResult(
      order=ConditionalLiquidationOrder.from_model(result.order),
      triggered=result.triggered,
      submitted=result.submitted,
      message=result.message,
      sell_volume=result.sell_volume,
      order_id=result.order_id,
      latest_price=result.latest_price,
      profit_pct=result.profit_pct,
      error=result.error,
    )

  @staticmethod
  async def get_liquidation_summary(
    account_id: Optional[str] = None,
  ) -> LiquidationSummary:
    """获取清仓概况"""
    liquidation_service = LiquidationService(account_id=account_id)
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
  async def get_conditional_liquidation_orders(
    account_id: Optional[str] = None,
    stock_code: Optional[str] = None,
    include_cancelled: bool = False,
  ) -> List[ConditionalLiquidationOrder]:
    service = LiquidationService(account_id=account_id)
    orders = await service.list_conditional_liquidation_orders(
      stock_code=stock_code,
      include_cancelled=include_cancelled,
    )
    return [ConditionalLiquidationOrder.from_model(order) for order in orders]

  @staticmethod
  async def liquidate_all_positions(
    input: LiquidateAllPositionsInput,
    account_id: str,
  ) -> LiquidationResult:
    """一键清仓"""
    liquidation_service = LiquidationService(account_id=account_id)

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
    order_ids = [
      str(order.get("order_id"))
      for order in result_data.orders
      if order.get("order_id") is not None
    ]
    result.orders = lambda: order_ids

    return result

  @staticmethod
  async def liquidate_position(
    input: LiquidatePositionInput,
    account_id: str,
  ) -> PositionLiquidationResult:
    """个股清仓"""
    liquidation_service = LiquidationService(account_id=account_id)

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
  async def upsert_conditional_liquidation_order(
    input: ConditionalLiquidationOrderInput,
    account_id: str,
  ) -> ConditionalLiquidationOrder:
    service = LiquidationService(account_id=account_id)
    order = await service.upsert_conditional_liquidation_order(
      order_id=input.id,
      stock_code=input.stock_code,
      instrument_name=input.instrument_name,
      enabled=input.enabled,
      target_profit_pct=input.target_profit_pct,
      target_price=input.target_price,
      sell_mode=input.sell_mode,
      sell_ratio_pct=input.sell_ratio_pct,
      sell_volume=input.sell_volume,
      remark=input.remark,
    )
    return ConditionalLiquidationOrder.from_model(order)

  @staticmethod
  async def set_conditional_liquidation_order_enabled(
    order_id: str,
    enabled: bool,
    account_id: str,
  ) -> Optional[ConditionalLiquidationOrder]:
    service = LiquidationService(account_id=account_id)
    order = await service.set_conditional_liquidation_order_enabled(
      order_id,
      enabled,
    )
    return ConditionalLiquidationOrder.from_model(order) if order else None

  @staticmethod
  async def cancel_conditional_liquidation_order(
    order_id: str,
    account_id: str,
  ) -> Optional[ConditionalLiquidationOrder]:
    service = LiquidationService(account_id=account_id)
    order = await service.cancel_conditional_liquidation_order(order_id)
    return ConditionalLiquidationOrder.from_model(order) if order else None

  @staticmethod
  async def evaluate_conditional_liquidation_orders(
    account_id: Optional[str] = None,
    stock_code: Optional[str] = None,
  ) -> List[ConditionalLiquidationEvaluationResult]:
    aggregate_id = account_id or "all"
    receipt = await engine_command_service.request(
      "LIQUIDATION_EVALUATE",
      {
        "account_id": account_id,
        "stock_code": stock_code,
      },
      aggregate_id=aggregate_id,
      idempotency_key=f"liquidation-evaluate:{aggregate_id}:{uuid.uuid4()}",
    )
    if receipt.status == "FAILED":
      raise RuntimeError(receipt.error or "条件清仓评估失败")
    if receipt.status != "SUCCEEDED":
      raise RuntimeError(
        f"条件清仓评估已排队但 Engine 尚未确认: {receipt.message_id}"
      )
    results = list((receipt.result or {}).get("items") or [])
    return [LiquidationResolver._conditional_evaluation_result(item) for item in results]

  @staticmethod
  async def redeem_cleared_position(
    input: RedeemPositionInput,
    account_id: str,
  ) -> RedemptionResult:
    """已清仓股票资金赎回"""
    liquidation_service = LiquidationService(account_id=account_id)

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
    safe_limit = max(1, min(int(limit or 20), 200))
    safe_offset = max(0, int(offset or 0))
    async for db in get_async_db():
      result = await db.execute(
        select(LiquidationOrderModel)
        .filter(LiquidationOrderModel.account_id == account_id)
        .order_by(desc(LiquidationOrderModel.created_at))
        .limit(safe_limit)
        .offset(safe_offset)
      )
      return [
        LiquidationResolver._liquidation_order(model)
        for model in result.scalars().all()
      ]
    return []

  @staticmethod
  async def get_liquidation_order(
    order_id: str, account_id: str
  ) -> Optional[LiquidationOrder]:
    """获取单个清仓订单"""
    async for db in get_async_db():
      result = await db.execute(
        select(LiquidationOrderModel).filter(
          LiquidationOrderModel.id == order_id,
          LiquidationOrderModel.account_id == account_id,
        )
      )
      model = result.scalar_one_or_none()
      return LiquidationResolver._liquidation_order(model) if model else None
    return None

  @staticmethod
  async def get_redemption_records(
    account_id: str, stock_code: Optional[str] = None, limit: int = 20, offset: int = 0
  ) -> List[RedemptionRecord]:
    """获取赎回记录列表"""
    safe_limit = max(1, min(int(limit or 20), 200))
    safe_offset = max(0, int(offset or 0))
    statement = select(RedemptionRecordModel).filter(
      RedemptionRecordModel.account_id == account_id
    )
    if stock_code:
      statement = statement.filter(
        RedemptionRecordModel.stock_code == stock_code.strip().upper()
      )
    statement = (
      statement.order_by(desc(RedemptionRecordModel.created_at))
      .limit(safe_limit)
      .offset(safe_offset)
    )
    async for db in get_async_db():
      result = await db.execute(statement)
      return [
        LiquidationResolver._redemption_record(model)
        for model in result.scalars().all()
      ]
    return []

  @staticmethod
  async def conditional_order_account_id(order_id: str) -> Optional[str]:
    async for db in get_async_db():
      result = await db.execute(
        select(ConditionalLiquidationOrderModel.account_id).filter(
          ConditionalLiquidationOrderModel.id == order_id
        )
      )
      return result.scalar_one_or_none()
    return None

  @staticmethod
  async def cancel_liquidation_order(
    order_id: str,
    account_id: str,
  ) -> MessageResponse:
    """取消清仓订单"""
    async for db in get_async_db():
      result = await db.execute(
        select(LiquidationOrderModel)
        .filter(
          LiquidationOrderModel.id == order_id,
          LiquidationOrderModel.account_id == account_id,
        )
        .with_for_update()
      )
      order = result.scalar_one_or_none()
      if order is None:
        return MessageResponse(success=False, message="清仓订单不存在")
      if order.status != LiquidationStatus.PENDING:
        return MessageResponse(
          success=False,
          message=f"状态为 {order.status} 的清仓订单不可取消",
        )
      order.status = LiquidationStatus.CANCELLED
      order.end_time = datetime.now()
      await db.commit()
      return MessageResponse(success=True, message=f"清仓订单 {order_id} 已取消")
    return MessageResponse(success=False, message="数据库连接不可用")
