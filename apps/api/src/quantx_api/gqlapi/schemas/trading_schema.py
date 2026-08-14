from datetime import date
from decimal import Decimal
from typing import List, Optional

import strawberry
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models import Instrument, PendingTradeOrder
from quantx_infrastructure.models.enums import OrderStatus, OrderType, PriceType
from quantx_infrastructure.models.order import Order as OrderModel
from quantx_infrastructure.services.order_service import OrderService
from quantx_infrastructure.services.t_trade_operations_service import (
  TTradeOperationsService,
)
from quantx_infrastructure.services.trade_command_service import TradeCommandService
from sqlalchemy import select

from quantx_api.auth.errors import AuthError
from quantx_api.auth.service import AuthService

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
from ..types.trading_types import (
  ManualOrderExecutionMode,
  ManualOrderPriceType,
  ManualOrderSide,
  OrderEntryCapabilities,
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
  @strawberry.field(description="查询当前账户与标的的移动端下单能力")
  async def order_entry_capabilities(
    self,
    info: strawberry.types.Info,
    instrument_code: str,
    account_id: Optional[str] = None,
  ) -> OrderEntryCapabilities:
    principal = principal_from_context(info.context)
    resolved_account_id = await _resolve_account_id(info, account_id)
    normalized_code = str(instrument_code or "").strip().upper()
    valid_code = (
      len(normalized_code) == 9
      and normalized_code[:6].isdigit()
      and normalized_code[6:] in {".SH", ".SZ", ".BJ"}
    )
    instrument = None
    if valid_code:
      async with AsyncSessionLocal() as db:
        instrument = await db.get(Instrument, normalized_code)
    instrument_available = bool(
      instrument is not None and getattr(instrument, "is_trading", True) is not False
    )
    has_manual_scope = "trade:manual" in principal.permissions
    can_manual_trade = bool(valid_code and instrument_available and has_manual_scope)
    execution_modes = (
      [ManualOrderExecutionMode.PAPER] if can_manual_trade else []
    )
    live_ready = False
    live_blocked_reasons: List[str] = []
    if can_manual_trade:
      try:
        readiness = await TTradeOperationsService().readiness(
          resolved_account_id
        )
        relevant_checks = [
          item
          for item in list(readiness.get("checks") or [])
          if str(item.get("code") or "") != "T_TRADE_LIVE_ENABLED"
        ]
        live_blocked_reasons = [
          str(item.get("message") or item.get("code") or "实盘未就绪")
          for item in relevant_checks
          if not bool(item.get("passed"))
        ]
        if int(readiness.get("ready_live_agent_count") or 0) != 1:
          live_blocked_reasons.append("实盘要求当前账户恰好一个 READY live Agent")
        live_ready = not live_blocked_reasons
      except Exception:
        live_blocked_reasons = ["实盘安全状态暂不可用"]
      if live_ready:
        execution_modes.append(ManualOrderExecutionMode.LIVE)
    elif not has_manual_scope:
      live_blocked_reasons = ["当前设备会话未获授 trade:manual"]
    elif not valid_code:
      live_blocked_reasons = ["证券代码格式无效"]
    else:
      live_blocked_reasons = ["证券主数据不存在或当前不可交易"]
    supported_price_types = (
      [ManualOrderPriceType.LIMIT, ManualOrderPriceType.BEST]
      if normalized_code.endswith((".SH", ".SZ"))
      else [ManualOrderPriceType.LIMIT]
    )
    return OrderEntryCapabilities(
      account_id=resolved_account_id,
      instrument_code=normalized_code,
      can_manual_trade=can_manual_trade,
      default_execution_mode=ManualOrderExecutionMode.PAPER,
      execution_modes=execution_modes,
      supported_sides=[ManualOrderSide.BUY, ManualOrderSide.SELL],
      supported_price_types=supported_price_types,
      live_ready=live_ready,
      live_blocked_reasons=list(dict.fromkeys(live_blocked_reasons)),
      warnings=[
        "能力只决定可展示的票据选项；每次预览和确认仍重新执行服务端风控",
        "北交所暂不提供 BEST；沪深 BEST 仅映射对手方最优价",
      ],
    )

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
        execution_mode=input.execution_mode,
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
          execution_mode=request.execution_mode,
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
      principal = principal_from_context(info.context)
      idempotency_key = str(input.idempotency_key or "").strip()
      if principal.active_account_id is not None and (
        not idempotency_key or len(idempotency_key) > 128
      ):
        return CancelOrderResult(
          success=False,
          message="原生移动端撤单必须提供不超过 128 个字符的幂等键",
          order_id=input.order_id,
          status="REJECTED",
        )
      async with AsyncSessionLocal() as db:
        try:
          current = await AuthService(db).lock_and_validate_session(
            principal,
            required_permission="trade:manual",
            account_id=account_id,
          )
        except AuthError as exc:
          return CancelOrderResult(
            success=False,
            message=exc.message,
            order_id=input.order_id,
            status="REJECTED",
          )
        order = (
          await db.execute(
            select(OrderModel)
            .where(
              OrderModel.id == input.order_id,
              OrderModel.account_id == account_id,
            )
            .with_for_update()
          )
        ).scalar_one_or_none()
        if order is None:
          return CancelOrderResult(
            success=False,
            message=f"订单 {input.order_id} 不存在",
            order_id=input.order_id,
            status="REJECTED",
          )
        status = order.status
        if status not in {OrderStatus.REPORTED, OrderStatus.PART_SUCC}:
          return CancelOrderResult(
            success=False,
            message=f"订单状态 {status.name} 不允许撤单",
            order_id=input.order_id,
            status="REJECTED",
          )
        pending = (
          await db.execute(
            select(PendingTradeOrder)
            .where(
              PendingTradeOrder.account_id == account_id,
              PendingTradeOrder.broker_order_id == str(input.order_id),
            )
            .order_by(PendingTradeOrder.updated_at.desc())
            .limit(1)
            .with_for_update()
          )
        ).scalar_one_or_none()
        if principal.active_account_id is not None and pending is None:
          return CancelOrderResult(
            success=False,
            message="订单缺少 QuantX 命令关联，原生端拒绝撤销未追踪委托",
            order_id=input.order_id,
            status="REJECTED",
          )
        execution_mode = str(
          getattr(pending, "execution_mode", None) or "live"
        ).lower()
        queued = await TradeCommandService(db).enqueue_cancel(
          user_id=current.user_id,
          account_id=account_id,
          broker_order_id=str(input.order_id),
          idempotency_key=idempotency_key,
          execution_mode=execution_mode,
          commit_transaction=False,
        )
        await db.commit()
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
