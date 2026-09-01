from datetime import date
from decimal import Decimal
from typing import List, Optional

import strawberry
from quantx_contracts import LIVE_ORDER_MAX_QUOTE_AGE_SECONDS
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models import (
  Instrument,
  PendingTradeOrder,
  TradeCommandOutbox,
)
from quantx_infrastructure.models.enums import OrderStatus, OrderType, PriceType
from quantx_infrastructure.models.order import Order as OrderModel
from quantx_infrastructure.services.account_execution_safety_service import (
  AccountExecutionSafetyService,
)
from quantx_infrastructure.services.order_service import OrderService
from quantx_infrastructure.services.trade_command_service import TradeCommandService
from sqlalchemy import select

from quantx_api.auth.errors import AuthError
from quantx_api.auth.service import AuthService
from quantx_api.manual_order_runtime import configured_manual_order_execution_mode

from ..account_execution_control import (
  AccountExecutionControlChallengeService,
  normalize_account_execution_control_request,
)
from ..manual_order import (
  ManualOrderChallengeService,
  normalize_manual_order_request,
)
from ..resolvers.orders import OrderResolver
from ..resolvers.trading_safety import AccountExecutionSafetyResolver
from ..security import authorized_account_id, principal_from_context
from ..trade_approval import TradeApprovalChallengeError
from ..types import (
  CancelOrderInput,
  CancelOrderResult,
  ManualOrderAttempt,
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
from ..types.trading_safety_types import (
  AccountExecutionControlConfirmationInput,
  AccountExecutionControlConfirmationResult,
  AccountExecutionControlPreview,
  AccountExecutionControlPreviewInput,
  AccountExecutionControlPreviewResult,
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


def _manual_order_attempt_message(
  *,
  broker_order_id: Optional[str],
  delivery_status: str,
  status: str,
  status_reason: Optional[str],
) -> str:
  if broker_order_id:
    return f"券商委托已生成：{broker_order_id}；最终状态以券商回报为准"

  normalized_status = str(status or "").upper()
  normalized_delivery = str(delivery_status or "").upper()
  reason = str(status_reason or "").strip()
  if normalized_status == "REJECTED" or normalized_delivery == "REJECTED":
    reason_messages = {
      "stale live quote": (
        f"QMT Agent 下单前行情已超过 {LIVE_ORDER_MAX_QUOTE_AGE_SECONDS} 秒"
      ),
      "outside trading session": "QMT Agent 下单前发现当前不在交易时段",
      "insufficient available volume": "QMT Agent 下单前发现可卖数量不足",
      "insufficient cash": "QMT Agent 下单前发现可用资金不足",
      "instrument suspended": "QMT Agent 下单前发现证券已停牌",
    }
    detail = reason_messages.get(reason.lower(), reason or "QMT Agent 下单前拒绝")
    return f"{detail}，未向券商提交"
  if normalized_status == "EXPIRED" or normalized_delivery == "EXPIRED":
    return "下单命令已过期，未向券商提交"
  if normalized_status == "KILL_SWITCHED" or normalized_delivery == "KILL_SWITCHED":
    return "下单命令已被实盘安全开关拦截，未向券商提交"
  if (
    normalized_status == "RECONCILE_REQUIRED"
    or normalized_delivery == "RECONCILE_REQUIRED"
  ):
    return "下单结果暂不确定，请先核对券商端，切勿重复提交"
  if normalized_delivery == "ACKNOWLEDGED":
    return "QMT Agent 已接收，尚未取得券商委托回报，请勿重复提交"
  if normalized_delivery == "DELIVERED":
    return "下单请求已送达 QMT Agent，尚未生成券商委托"
  return "下单请求已进入可靠队列，尚未生成券商委托"


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
  @strawberry.field(description="查询当前账户与标的的手工下单能力")
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
    instrument_available = instrument is not None
    has_manual_scope = "trade:manual" in principal.permissions
    can_manual_trade = bool(valid_code and instrument_available and has_manual_scope)
    configured_execution_mode = ManualOrderExecutionMode(
      configured_manual_order_execution_mode(resolved_account_id)
    )
    execution_modes = [configured_execution_mode] if can_manual_trade else []
    live_ready = False
    can_live_sell = False
    live_blocked_reasons: List[str] = []
    if can_manual_trade:
      if configured_execution_mode == ManualOrderExecutionMode.LIVE:
        try:
          safety = await AccountExecutionSafetyService().status(resolved_account_id)
          live_blocked_reasons = list(safety.get("blocked_reasons") or [])
          live_ready = bool(safety.get("can_increase_risk"))
          can_live_sell = bool(safety.get("can_reduce_risk"))
        except Exception:
          live_blocked_reasons = ["实盘安全状态暂不可用"]
    elif not has_manual_scope:
      live_blocked_reasons = ["当前会话未获授 trade:manual"]
    elif not valid_code:
      live_blocked_reasons = ["证券代码格式无效"]
    else:
      live_blocked_reasons = ["证券主数据不存在"]
    supported_price_types = (
      [ManualOrderPriceType.LIMIT, ManualOrderPriceType.BEST]
      if normalized_code.endswith((".SH", ".SZ"))
      else [ManualOrderPriceType.LIMIT]
    )
    return OrderEntryCapabilities(
      account_id=resolved_account_id,
      instrument_code=normalized_code,
      can_manual_trade=can_manual_trade,
      default_execution_mode=configured_execution_mode,
      execution_modes=execution_modes,
      supported_sides=[ManualOrderSide.BUY, ManualOrderSide.SELL],
      supported_price_types=supported_price_types,
      can_live_buy=live_ready,
      can_live_sell=can_live_sell,
      live_ready=live_ready,
      live_blocked_reasons=list(dict.fromkeys(live_blocked_reasons)),
      warnings=[
        "执行模式跟随 liveTrading 配置；实盘门禁失败只会阻止下单，不会降级到 PAPER",
        "每次预览和确认仍重新执行服务端风控",
        "停牌与实时可交易状态以最新行情和 QMT 下单前检查为准",
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

  @strawberry.field(description="查询一次手动委托排队后的 Agent 与券商回报状态")
  async def manual_order_attempt(
    self,
    info: strawberry.types.Info,
    client_order_id: str,
    account_id: Optional[str] = None,
  ) -> Optional[ManualOrderAttempt]:
    normalized_client_order_id = str(client_order_id or "").strip()
    if not normalized_client_order_id or len(normalized_client_order_id) > 128:
      raise ValueError("手动委托客户端订单号无效")
    principal = principal_from_context(info.context)
    resolved_account_id = await _resolve_account_id(info, account_id)
    async with AsyncSessionLocal() as db:
      row = (
        await db.execute(
          select(PendingTradeOrder, TradeCommandOutbox)
          .outerjoin(
            TradeCommandOutbox,
            TradeCommandOutbox.client_order_id
            == PendingTradeOrder.client_order_id,
          )
          .where(
            PendingTradeOrder.client_order_id == normalized_client_order_id,
            PendingTradeOrder.account_id == resolved_account_id,
            PendingTradeOrder.user_id == principal.user_id,
            PendingTradeOrder.bucket == "manual",
          )
          .limit(1)
        )
      ).one_or_none()
    if row is None:
      return None

    pending, outbox = row
    delivery_status = str(
      getattr(outbox, "delivery_status", None) or pending.status or "QUEUED"
    ).upper()
    status = str(pending.status or delivery_status).upper()
    status_reason = str(
      pending.status_reason or getattr(outbox, "last_error", None) or ""
    ).strip() or None
    broker_order_id = str(pending.broker_order_id or "").strip() or None
    return ManualOrderAttempt(
      account_id=resolved_account_id,
      client_order_id=str(pending.client_order_id),
      broker_order_id=broker_order_id,
      instrument_code=str(pending.instrument_code),
      side=ManualOrderSide(str(pending.side).upper()),
      volume=int(pending.volume or 0),
      status=status,
      delivery_status=delivery_status,
      status_reason=status_reason,
      message=_manual_order_attempt_message(
        broker_order_id=broker_order_id,
        delivery_status=delivery_status,
        status=status,
        status_reason=status_reason,
      ),
      execution_mode=ManualOrderExecutionMode(
        str(pending.execution_mode or "paper").upper()
      ),
      created_at=pending.created_at,
      updated_at=pending.updated_at,
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
  @strawberry.mutation(description="预览账户级实盘执行控制并签发一次性确认挑战")
  async def preview_account_execution_control(
    self,
    info: strawberry.types.Info,
    input: AccountExecutionControlPreviewInput,
  ) -> AccountExecutionControlPreviewResult:
    try:
      account_id = await _resolve_account_id(info, input.account_id)
      request = normalize_account_execution_control_request(
        account_id=account_id,
        action=input.action,
        state_version=input.state_version,
        snapshot_id=input.snapshot_id,
        reason=input.reason,
        idempotency_key=input.idempotency_key,
        client_order_id=input.client_order_id,
        quarantine_reason=input.quarantine_reason,
      )
      issued = await AccountExecutionControlChallengeService.issue(
        principal=principal_from_context(info.context),
        request=request,
      )
      return AccountExecutionControlPreviewResult(
        success=True,
        code="PREVIEW_READY",
        message="请核对账户执行状态和风险提示后确认",
        preview=AccountExecutionControlPreview(
          challenge_id=strawberry.ID(issued.challenge_id),
          confirmation_token=issued.confirmation_token,
          token_issued=issued.token_issued,
          account_id=request.account_id,
          action=request.action,
          state_version=request.state_version,
          snapshot_id=request.snapshot_id,
          reason=request.reason,
          client_order_id=request.client_order_id,
          quarantine_reason=request.quarantine_reason,
          challenge_expires_at=issued.challenge_expires_at,
          challenge_status=issued.challenge_status,
          operation_status=issued.operation_status,
          safety=AccountExecutionSafetyResolver.from_payload(issued.safety),
        ),
      )
    except TradeApprovalChallengeError as exc:
      return AccountExecutionControlPreviewResult(
        success=False,
        code=exc.code,
        message=exc.message,
      )

  @strawberry.mutation(description="消费一次性挑战并应用账户级实盘执行控制")
  async def confirm_account_execution_control(
    self,
    info: strawberry.types.Info,
    input: AccountExecutionControlConfirmationInput,
  ) -> AccountExecutionControlConfirmationResult:
    try:
      result = await AccountExecutionControlChallengeService.confirm(
        principal=principal_from_context(info.context),
        challenge_id=str(input.challenge_id),
        confirmation_token=input.confirmation_token,
      )
      return AccountExecutionControlConfirmationResult(
        success=result.operation_status == "APPLIED",
        code=result.operation_code,
        message=result.message,
        challenge_id=strawberry.ID(result.challenge_id),
        action=result.action,
        operation_status=result.operation_status,
        safety=(
          AccountExecutionSafetyResolver.from_payload(result.safety)
          if result.safety is not None
          else None
        ),
        event_id=(
          strawberry.ID(str(result.repair_result.get("event_id")))
          if result.repair_result and result.repair_result.get("event_id")
          else None
        ),
        client_order_id=(
          str(result.repair_result.get("client_order_id"))
          if result.repair_result
          else None
        ),
        plan_id=(
          str(result.repair_result.get("plan_id"))
          if result.repair_result
          else None
        ),
        intent_id=(
          str(result.repair_result.get("intent_id"))
          if result.repair_result
          else None
        ),
        snapshot_id=(
          str(result.repair_result.get("snapshot_id"))
          if result.repair_result
          else None
        ),
        broker_terminal_status=(
          str(result.repair_result.get("broker_terminal_status"))
          if result.repair_result
          else None
        ),
        cumulative_filled_volume=(
          int(result.repair_result.get("cumulative_filled_volume") or 0)
          if result.repair_result
          else None
        ),
      )
    except TradeApprovalChallengeError as exc:
      return AccountExecutionControlConfirmationResult(
        success=False,
        code=exc.code,
        message=exc.message,
      )

  @strawberry.mutation(description="预览手动委托并签发一次性确认挑战")
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
        message="请核对委托、行情时间和风险提示后确认",
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

  @strawberry.mutation(description="消费一次性挑战并排队手动委托")
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
        message="下单请求已进入可靠队列，尚未生成券商委托；正在等待 QMT Agent 下单前复核和券商回报",
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

  @strawberry.mutation(
    description=("创建手工交易命令并返回排队状态；成功不表示已报、成交或持仓已变化")
  )
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

  @strawberry.mutation(
    description=("创建撤单命令并返回受理结果；最终撤单状态以 QMT 委托回报收敛结果为准")
  )
  async def cancel_order(
    self, info: strawberry.types.Info, input: CancelOrderInput
  ) -> CancelOrderResult:
    try:
      account_id = await _resolve_account_id(info, input.account_id)
      principal = principal_from_context(info.context)
      idempotency_key = str(input.idempotency_key or "").strip()
      if principal.is_native_session and (
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
        if principal.is_native_session and pending is None:
          return CancelOrderResult(
            success=False,
            message="订单缺少 QuantX 命令关联，原生端拒绝撤销未追踪委托",
            order_id=input.order_id,
            status="REJECTED",
          )
        execution_mode = str(getattr(pending, "execution_mode", None) or "live").lower()
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
