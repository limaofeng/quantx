from datetime import date
from typing import List, Optional

import strawberry

from miniqmt.manager_registry import XTTradingManagerRegistry
from models.enums import OrderStatus, OrderType, PriceType
from services.order_service import OrderService
from services.trading_service import TradingService

from ..resolvers.orders import OrderResolver
from ..security import authorized_account_id
from ..types import (
  CancelOrderInput,
  CancelOrderResult,
  Order,
  OrderInput,
  OrderMutationResult,
  Trade,
)

_PRICE_TYPE_ALIASES = {
  "LIMIT": PriceType.FIX_PRICE,
  "FIX_PRICE": PriceType.FIX_PRICE,
  "MARKET": PriceType.MARKET_CONVERT_5_LIMIT,
  "MARKET_CONVERT_5_LIMIT": PriceType.MARKET_CONVERT_5_LIMIT,
  "LATEST": PriceType.LATEST_PRICE,
  "LATEST_PRICE": PriceType.LATEST_PRICE,
  "BEST": PriceType.MARKET_PEER_PRICE_FIRST,
  "MARKET_PEER_PRICE_FIRST": PriceType.MARKET_PEER_PRICE_FIRST,
  "MARKET_MINE_PRICE_FIRST": PriceType.MARKET_MINE_PRICE_FIRST,
}


async def _resolve_account_id(
  info: strawberry.types.Info, account_id: Optional[str]
) -> str:
  return authorized_account_id(info, account_id)


def _validate_history_range(start_date: str, end_date: str) -> None:
  start = date.fromisoformat(start_date)
  end = date.fromisoformat(end_date)
  if start > end:
    raise ValueError("开始日期不能晚于结束日期")
  if (end - start).days > 365:
    raise ValueError("历史委托和成交最多查询 365 日")


def _parse_order_type(value: str) -> OrderType:
  if isinstance(value, OrderType):
    return value
  if isinstance(value, str):
    key = value.strip().upper()
    if key in OrderType.__members__:
      return OrderType[key]
    if key.isdigit():
      return OrderType(int(key))
  raise ValueError("委托类型必须是 BUY 或 SELL")


def _parse_price_type(value: str) -> PriceType:
  if isinstance(value, PriceType):
    return value
  if isinstance(value, str):
    key = value.strip().upper()
    if key in _PRICE_TYPE_ALIASES:
      return _PRICE_TYPE_ALIASES[key]
  raise ValueError("报价类型无效")


async def _fetch_order(order_id: int, account_id: str) -> Optional[Order]:
  service = OrderService(account_id)
  model_order = await service.get_order_by_id(order_id)
  if not model_order:
    return None

  return Order(
    id=str(model_order.id),
    sysid=model_order.sysid or "",
    stock_code=model_order.stock_code,
    stock_name=model_order.instrument_name or "",
    type=model_order.type,
    volume=model_order.volume or 0,
    price_type=model_order.price_type,
    price=float(model_order.price or 0),
    traded_volume=model_order.traded_volume or 0,
    traded_price=float(model_order.traded_price or 0),
    status=model_order.status or OrderStatus.UNKNOWN,
    status_msg=model_order.status_msg,
    strategy_name=model_order.strategy_name,
    order_remark=model_order.remark,
    time=model_order.time,
  )


@strawberry.type(description="订单交易相关查询")
class TradingQuery:
  @strawberry.field(description="获取当日委托列表")
  async def today_orders(
    self, info: strawberry.types.Info, account_id: Optional[str] = None
  ) -> List[Order]:
    return await OrderResolver.get_today_orders(
      await _resolve_account_id(info, account_id)
    )

  @strawberry.field(description="获取历史委托列表")
  async def history_orders(
    self,
    info: strawberry.types.Info,
    start_date: str,
    end_date: str,
    account_id: Optional[str] = None,
  ) -> List[Order]:
    _validate_history_range(start_date, end_date)
    resolved_account_id = await _resolve_account_id(info, account_id)
    return await OrderResolver.get_history_orders(
      resolved_account_id, start_date, end_date
    )

  @strawberry.field(description="获取单个委托")
  async def order(
    self,
    info: strawberry.types.Info,
    order_id: int,
    account_id: Optional[str] = None,
  ) -> Optional[Order]:
    return await OrderResolver.get_order(
      order_id, await _resolve_account_id(info, account_id)
    )

  @strawberry.field(description="获取当日成交列表")
  async def today_trades(
    self,
    info: strawberry.types.Info,
    account_id: Optional[str] = None,
  ) -> List[Trade]:
    return await OrderResolver.get_today_trades(
      await _resolve_account_id(info, account_id)
    )

  @strawberry.field(description="获取历史成交列表")
  async def history_trades(
    self,
    info: strawberry.types.Info,
    account_id: str,
    start_date: str,
    end_date: str,
  ) -> List[Trade]:
    _validate_history_range(start_date, end_date)
    return await OrderResolver.get_history_trades(
      await _resolve_account_id(info, account_id), start_date, end_date
    )

  @strawberry.field(description="获取单个成交记录")
  async def trade(
    self,
    info: strawberry.types.Info,
    trade_id: str,
    account_id: Optional[str] = None,
  ) -> Optional[Trade]:
    return await OrderResolver.get_trade(
      trade_id, await _resolve_account_id(info, account_id)
    )


@strawberry.type(description="订单交易相关变更")
class TradingMutation:
  @strawberry.mutation(description="下单")
  async def place_order(
    self, info: strawberry.types.Info, input: OrderInput
  ) -> OrderMutationResult:
    try:
      account_id = await _resolve_account_id(info, input.account_id)
      order_type = _parse_order_type(input.type)
      price_type = _parse_price_type(input.price_type)
      price = input.price or 0
      service = TradingService(account_id=account_id)
      result = await service.place_order(
        stock_code=input.stock_code,
        order_type=order_type,
        order_volume=input.volume,
        price_type=price_type,
        price=price,
        strategy_name=input.strategy_name or "",
        order_remark=input.order_remark or "",
      )
      order_id = result.get("order_id")
      order = None
      if result.get("success") and order_id:
        order = await _fetch_order(order_id, account_id)
      return OrderMutationResult(
        success=bool(result.get("success")),
        message=result.get("message", ""),
        order_id=order_id if result.get("success") else None,
        order=order,
      )
    except Exception as exc:
      return OrderMutationResult(
        success=False,
        message=str(exc),
        order_id=None,
        order=None,
      )

  @strawberry.mutation(description="撤单")
  async def cancel_order(
    self, info: strawberry.types.Info, input: CancelOrderInput
  ) -> CancelOrderResult:
    try:
      account_id = await _resolve_account_id(info, input.account_id)
      registry = XTTradingManagerRegistry()
      trading_manager = registry.get_manager(account_id)
      order = trading_manager.get_order(input.order_id)
      if not order:
        return CancelOrderResult(
          success=False,
          message=f"订单 {input.order_id} 不存在",
          order_id=None,
        )
      status = OrderStatus(order.order_status)
      if status in [
        OrderStatus.CANCELED,
        OrderStatus.SUCCEEDED,
        OrderStatus.JUNK,
        OrderStatus.UNKNOWN,
      ]:
        return CancelOrderResult(
          success=False,
          message=f"订单状态 {status.name} 不允许撤单",
          order_id=None,
        )
      cancelled = trading_manager.cancel_order(input.order_id)
      if not cancelled:
        return CancelOrderResult(
          success=False,
          message=f"订单 {input.order_id} 撤单失败",
          order_id=None,
        )
      return CancelOrderResult(
        success=True,
        message="撤单成功",
        order_id=input.order_id,
      )
    except Exception as exc:
      return CancelOrderResult(
        success=False,
        message=str(exc),
        order_id=None,
      )
