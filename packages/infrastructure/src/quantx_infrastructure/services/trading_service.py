"""Trading application facade backed by durable TradeCommand messages."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List

from sqlalchemy import select

from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.account import Account
from quantx_infrastructure.models.agent_runtime import (
  PendingTradeOrder,
  TradeCommandOutbox,
)
from quantx_infrastructure.models.enums import (
  AccountType,
  InstrumentType,
  OrderType,
  PriceType,
)
from quantx_infrastructure.models.instrument import Instrument
from quantx_infrastructure.repositories.account_repository import AccountRepository
from quantx_infrastructure.services.order_service import OrderService
from quantx_infrastructure.services.position_service import PositionService
from quantx_infrastructure.services.trade_command_service import TradeCommandService

logger = logging.getLogger(__name__)
DEFAULT_ACCOUNT_ID = "300000013250"
DEFAULT_COMMISSION_RATE = Decimal("0.0003")
DEFAULT_MIN_COMMISSION = Decimal("5")
DEFAULT_STAMP_TAX_RATE = Decimal("0.0005")
DEFAULT_TRANSFER_FEE_RATE = Decimal("0.00001")


class TradingError(Exception):
  pass


class InvalidOrderError(TradingError):
  pass


class TradingService:
  """Queues broker commands and reads reconciled account state from PostgreSQL."""

  def __init__(
    self,
    account_id: str = DEFAULT_ACCOUNT_ID,
    account_type: AccountType = AccountType.STOCK,
    execution_mode: str = "paper",
  ) -> None:
    self.account_id = account_id or DEFAULT_ACCOUNT_ID
    self.account_type = account_type
    self.execution_mode = execution_mode.strip().lower()
    self.order_service = OrderService(self.account_id)
    self.position_service = PositionService()

  async def get_account_info(self, realtime: bool = False) -> Account:
    del realtime
    async for db in get_async_db():
      account = await AccountRepository(db).find_by_account_id(
        self.account_id,
        self.account_type,
      )
      if account is None:
        raise TradingError("未找到账户快照")
      return account
    raise TradingError("账户数据库不可用")

  async def place_order(
    self,
    stock_code: str,
    order_type: OrderType = OrderType.BUY,
    order_volume: int = 100,
    price_type: PriceType = PriceType.MARKET_CONVERT_5_LIMIT,
    price: float = 0,
    strategy_name: str = "",
    order_remark: str = "",
    close_position: bool = False,
    idempotency_key: str = "",
    execution_context: Dict[str, Any] | None = None,
  ) -> Dict[str, Any]:
    del close_position
    if order_type not in (OrderType.BUY, OrderType.SELL):
      raise InvalidOrderError("订单类型必须是 BUY 或 SELL")
    if order_volume <= 0:
      raise InvalidOrderError("订单数量必须大于 0")
    if price_type == PriceType.FIX_PRICE and price <= 0:
      raise InvalidOrderError("限价委托必须指定有效价格")
    context = dict(execution_context or {})
    async with AsyncSessionLocal() as db:
      queued = await TradeCommandService(db).enqueue_order_for_account(
        account_id=self.account_id,
        instrument_code=stock_code,
        side=order_type.name,
        order_type=price_type.name,
        limit_price=Decimal(str(price)),
        volume=order_volume,
        strategy_name=strategy_name,
        order_remark=order_remark,
        trace_id=str(context.get("trace_id") or ""),
        idempotency_key=idempotency_key,
        execution_mode=self.execution_mode,
        strategy_run_id=str(context.get("strategy_run_id") or ""),
        strategy_order_id=str(context.get("strategy_order_id") or ""),
        intent_id=str(context.get("intent_id") or ""),
        batch_id=str(
          context.get("t_batch_id") or context.get("batch_id") or ""
        ),
        bucket=str(context.get("bucket") or "manual"),
        t_trade_role=str(context.get("t_trade_role") or ""),
        risk_decision_id=str(context.get("risk_decision_id") or ""),
        substitution_plan=(
          dict(context["substitution_plan"])
          if isinstance(context.get("substitution_plan"), dict)
          else None
        ),
        policy_version=int(
          context.get("exit_policy_version")
          or context.get("config_version")
          or 0
        ),
        request_metadata=context,
        require_risk_reducing_live_authorization=bool(
          context.get("exact_auto_exit_authorized")
        ),
        authorization_user_id=str(
          context.get("auto_exit_authorization_user_id") or ""
        ),
      )
    return {
      "success": True,
      "order_id": None,
      "client_order_id": queued.client_order_id,
      "status": queued.status,
      "message": "交易命令已排队",
    }

  async def cancel_order(
    self,
    user_id: str,
    order_id: int,
    idempotency_key: str = "",
  ) -> Dict[str, Any]:
    del user_id
    async with AsyncSessionLocal() as db:
      queued = await TradeCommandService(db).enqueue_cancel_for_account(
        account_id=self.account_id,
        broker_order_id=str(order_id),
        idempotency_key=idempotency_key,
        execution_mode=self.execution_mode,
      )
    return {
      "success": True,
      "order_id": order_id,
      "client_order_id": queued.client_order_id,
      "status": queued.status,
      "message": "撤单命令已排队",
    }

  async def cancel_pending_order(
    self,
    *,
    client_order_id: str,
  ) -> Dict[str, Any]:
    """Cancel a queued command or enqueue broker cancellation after convergence."""
    async with AsyncSessionLocal() as db:
      pending = await db.get(PendingTradeOrder, client_order_id)
      if pending is None or pending.account_id != self.account_id:
        return {"success": False, "message": "找不到待处理交易命令"}
      if pending.broker_order_id:
        queued = await TradeCommandService(db).enqueue_cancel_for_account(
          account_id=self.account_id,
          broker_order_id=pending.broker_order_id,
          idempotency_key=(
            f"cancel-client-order:{client_order_id}:{pending.broker_order_id}"
          ),
          execution_mode=self.execution_mode,
        )
        return {
          "success": True,
          "client_order_id": queued.client_order_id,
          "status": queued.status,
          "message": "撤单命令已排队",
        }

      outbox = (
        await db.execute(
          select(TradeCommandOutbox)
          .where(TradeCommandOutbox.client_order_id == client_order_id)
          .with_for_update()
        )
      ).scalar_one_or_none()
      if outbox is not None and outbox.delivery_status == "QUEUED":
        outbox.delivery_status = "CANCELLED"
        pending.status = "CANCELLED"
        pending.status_reason = "cancelled before Agent delivery"
        await db.commit()
        return {
          "success": True,
          "client_order_id": client_order_id,
          "status": "CANCELLED",
          "message": "尚未投递的交易命令已取消",
        }
      return {
        "success": False,
        "client_order_id": client_order_id,
        "status": pending.status,
        "message": "命令已投递，等待券商委托回报后再撤单",
      }

  async def order_for_client_order(
    self,
    client_order_id: str,
  ) -> Any:
    async with AsyncSessionLocal() as db:
      pending = await db.get(PendingTradeOrder, client_order_id)
      broker_order_id = pending.broker_order_id if pending else None
    if not broker_order_id:
      return None
    try:
      return await self.order_service.get_order_by_id(int(broker_order_id))
    except (TypeError, ValueError):
      return None

  async def check_order_status(
    self,
    order_id: int,
    wait_time: int = 5,
  ) -> Dict[str, Any]:
    del wait_time
    order = await self.order_service.get_order_by_id(order_id)
    if order is None:
      return {"success": False, "message": f"订单 {order_id} 不存在"}
    return {
      "success": True,
      "order_id": order.id,
      "status": order.status,
      "traded_volume": order.traded_volume,
      "remaining_volume": max(0, order.volume - order.traded_volume),
      "message": "订单状态来自已收敛的券商回报",
    }

  async def execute_strategy_orders(
    self,
    strategy_id: str,
    orders: List[Dict[str, Any]],
  ) -> Dict[str, Any]:
    results = []
    for index, order in enumerate(orders):
      result = await self.place_order(
        stock_code=order["stock_code"],
        order_type=order["order_type"],
        order_volume=order["quantity"],
        price_type=order.get("price_type", PriceType.FIX_PRICE),
        price=order.get("price", 0),
        strategy_name=strategy_id,
        idempotency_key=str(
          order.get("idempotency_key")
          or f"strategy:{strategy_id}:{index}:{order['stock_code']}"
        ),
      )
      results.append(result)
    success_count = sum(1 for result in results if result["success"])
    return {
      "strategy_id": strategy_id,
      "total_orders": len(results),
      "success_count": success_count,
      "failed_count": len(results) - success_count,
      "results": results,
    }

  def _validate_order_volume(
    self,
    volume: int,
    stock_info: Instrument,
    *,
    order_type: OrderType = None,
    close_position: bool = False,
  ) -> bool:
    min_order_volume = stock_info.min_market_order_volume
    max_order_volume = stock_info.max_market_order_volume
    lot_size = 10 if stock_info.type == InstrumentType.TRR else 100
    if min_order_volume == 1:
      min_order_volume = 10
    if order_type == OrderType.SELL and close_position:
      return 0 < volume <= max_order_volume
    return min_order_volume <= volume <= max_order_volume and volume % lot_size == 0

  def _calculate_commission(
    self,
    amount: Decimal,
    order_type: OrderType,
  ) -> Decimal:
    commission = max(amount * DEFAULT_COMMISSION_RATE, DEFAULT_MIN_COMMISSION)
    stamp_tax = (
      amount * DEFAULT_STAMP_TAX_RATE
      if order_type == OrderType.SELL
      else Decimal("0")
    )
    return commission + stamp_tax + amount * DEFAULT_TRANSFER_FEE_RATE

  def _is_in_trading_hours(
    self,
    hour: int,
    minute: int,
    stock_info: Instrument,
  ) -> bool:
    current = hour * 60 + minute
    afternoon_end = 15 * 60 + (30 if stock_info.type == InstrumentType.TRR else 0)
    return 9 * 60 + 30 <= current <= 11 * 60 + 30 or (
      13 * 60 <= current <= afternoon_end
    )
