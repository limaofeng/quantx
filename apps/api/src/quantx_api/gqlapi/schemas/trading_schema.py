from datetime import date
from decimal import Decimal
from typing import List, Optional

import strawberry
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.enums import OrderStatus, OrderType, PriceType
from quantx_infrastructure.services.order_service import OrderService
from quantx_infrastructure.services.trade_command_service import TradeCommandService

from ..manual_order import (
  ManualOrderChallengeService,
  normalize_manual_order_request,
)
from ..resolvers.orders import OrderResolver
from ..security import authorized_account_id, principal_from_context
from ..trade_approval import TradeApprovalChallengeError
from ..types import (
  CancelOrderInput,
  CancelOrderResult,
  ManualOrderConfirmationInput,
  ManualOrderConfirmationResult,
  ManualOrderPreview,
  ManualOrderPreviewInput,
  ManualOrderPreviewResult,
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
  @strawberry.mutation(description="预览移动端手动委托并签发一次性确认挑战")
  async def preview_manual_order(
    self,
    info: strawberry.types.Info,
    input: ManualOrderPreviewInput,
  ) -> ManualOrderPreviewResult:
    try:
      principal = principal_from_context(info.context)
      account_id = await _resolve_account_id(info, input.account_id)
      request = normalize_manual_order_request(
        account_id=account_id,
        instrument_code=input.instrument_code,
        side=input.side,
        price_type=input.price_type,
        volume=input.volume,
        limit_price=input.limit_price,
        idempotency_key=input.idempotency_key,
      )
      issued = await ManualOrderChallengeService.issue(
        principal=principal,
        request=request,
      )
      return ManualOrderPreviewResult(
        success=True,
        code="PREVIEW_READY",
        message="请核对委托、行情时间和风险提示后进行本机生物确认",
        preview=ManualOrderPreview(
          challenge_id=issued.challenge_id,
          confirmation_token=issued.confirmation_token,
          account_id=request.account_id,
          instrument_code=request.instrument_code,
          side=input.side,
          price_type=input.price_type,
          volume=request.volume,
          requested_volume=issued.preflight.requested_volume,
          final_volume=issued.preflight.final_volume,
          limit_price=request.limit_price,
          reference_price=issued.preflight.reference_price,
          estimated_amount=issued.preflight.estimated_amount,
          estimated_fees=issued.preflight.estimated_fees,
          available_cash=issued.preflight.available_cash,
          available_volume=issued.preflight.available_volume,
          idempotency_key=request.idempotency_key,
          execution_mode="LIVE",
          quote_timestamp=issued.preflight.quote_timestamp,
          challenge_expires_at=issued.challenge_expires_at,
          risk_decision_id=issued.preflight.risk_decision_id,
          risk_action=issued.preflight.risk_action,
          risk_reason_code=issued.preflight.risk_reason_code,
          risk_reason_detail=issued.preflight.risk_reason_detail,
          warnings=issued.preflight.warnings,
        ),
      )
    except TradeApprovalChallengeError as exc:
      return ManualOrderPreviewResult(
        success=False,
        code=exc.code,
        message=exc.message,
        preview=None,
      )

  @strawberry.mutation(description="消费一次性挑战并排队移动端手动委托")
  async def confirm_manual_order(
    self,
    info: strawberry.types.Info,
    input: ManualOrderConfirmationInput,
  ) -> ManualOrderConfirmationResult:
    try:
      result = await ManualOrderChallengeService.confirm(
        principal=principal_from_context(info.context),
        challenge_id=input.challenge_id,
        confirmation_token=input.confirmation_token,
      )
      return ManualOrderConfirmationResult(
        success=True,
        code="MANUAL_ORDER_QUEUED",
        message="交易命令已排队；请等待 QMT Agent 券商回报",
        challenge_id=result.challenge_id,
        client_order_id=result.client_order_id,
        status=result.status,
      )
    except TradeApprovalChallengeError as exc:
      return ManualOrderConfirmationResult(
        success=False,
        code=exc.code,
        message=exc.message,
      )
    except Exception:
      return ManualOrderConfirmationResult(
        success=False,
        code="MANUAL_ORDER_REJECTED",
        message="手动委托未能进入交易命令队列，请检查交易就绪状态",
      )

  @strawberry.mutation(description="下单")
  async def place_order(
    self, info: strawberry.types.Info, input: OrderInput
  ) -> OrderMutationResult:
    try:
      account_id = await _resolve_account_id(info, input.account_id)
      order_type = _parse_order_type(input.type)
      price_type = _parse_price_type(input.price_type)
      principal = principal_from_context(info.context)
      async with AsyncSessionLocal() as db:
        queued = await TradeCommandService(db).enqueue_order(
          user_id=principal.user_id,
          account_id=account_id,
          instrument_code=input.stock_code,
          side=order_type.name,
          order_type=price_type.name,
          limit_price=Decimal(str(input.price or 0)),
          volume=input.volume,
          strategy_name=input.strategy_name or "",
          order_remark=input.order_remark or "",
          idempotency_key=input.idempotency_key or "",
        )
      return OrderMutationResult(
        success=True,
        message="交易命令已排队，等待 QMT Agent 回报",
        order_id=None,
        client_order_id=queued.client_order_id,
        status=queued.status,
        order=None,
      )
    except Exception as exc:
      return OrderMutationResult(
        success=False,
        message=str(exc),
        order_id=None,
        client_order_id=None,
        status="REJECTED",
        order=None,
      )

  @strawberry.mutation(description="撤单")
  async def cancel_order(
    self, info: strawberry.types.Info, input: CancelOrderInput
  ) -> CancelOrderResult:
    try:
      account_id = await _resolve_account_id(info, input.account_id)
      order = await OrderService(account_id).get_order_by_id(input.order_id)
      if not order:
        return CancelOrderResult(
          success=False,
          message=f"订单 {input.order_id} 不存在",
          order_id=None,
          client_order_id=None,
          status="REJECTED",
        )
      status = order.status
      if status not in {OrderStatus.REPORTED, OrderStatus.PART_SUCC}:
        return CancelOrderResult(
          success=False,
          message=f"订单状态 {status.name} 不允许撤单",
          order_id=None,
          client_order_id=None,
          status="REJECTED",
        )
      principal = principal_from_context(info.context)
      async with AsyncSessionLocal() as db:
        queued = await TradeCommandService(db).enqueue_cancel(
          user_id=principal.user_id,
          account_id=account_id,
          broker_order_id=str(input.order_id),
          idempotency_key=input.idempotency_key or "",
          execution_mode="live",
        )
      return CancelOrderResult(
        success=True,
        message="撤单命令已排队；请等待 QMT Agent 券商回报",
        order_id=input.order_id,
        client_order_id=queued.client_order_id,
        status=queued.status,
      )
    except Exception as exc:
      return CancelOrderResult(
        success=False,
        message=str(exc),
        order_id=None,
        client_order_id=None,
        status="REJECTED",
      )
