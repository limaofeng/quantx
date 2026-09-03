"""
实盘 Broker - 对接 XTQuant 真实交易
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_domain.brokers.base import (
  AccountInfo,
  BrokerBase,
  OrderRequest,
  OrderResponse,
  OrderStatus,
  OrderType,
  Position,
  PriceType,
  TradeRecord,
)

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.services.trade_command_service import (
  AgentUnavailableError,
  require_stable_command_key,
)
from quantx_infrastructure.services.trade_intent_processor import (
  local_pre_broker_zero_fill_metadata,
)
from quantx_infrastructure.services.trading_service import InvalidOrderError

_WIRE_IDENTITY_METADATA_KEYS = frozenset(
  {
    "owner_type",
    "owner_id",
    "environment",
    "execution_environment",
    "execution_owner_type",
    "execution_owner_id",
    "source_execution_owner_type",
    "source_execution_owner_id",
    "source_execution_environment",
    "strategy_run_id",
    "exit_plan_id",
    "strategy_name",
    "remark",
    "order_remark",
    "idempotency_key",
    "trace_id",
  }
)


class LiveBroker(BrokerBase):
  """实盘交易 Broker - 对接真实交易系统"""

  def __init__(
    self,
    account_id: str,
    initial_capital: float = 1000000.0,
    enable_risk_control: bool = True,
    max_order_amount: float = 100000.0,  # 单笔最大金额
    max_position_value: float = 500000.0,  # 单个标的最大持仓
  ):
    super().__init__(account_id, float(initial_capital))

    # 风控参数
    self.enable_risk_control = enable_risk_control
    self.max_order_amount = max_order_amount
    self.max_position_value = max_position_value

    # 交易服务
    self.trading_service: Optional[Any] = None

    # 订单映射（内部ID -> 外部ID）
    self.order_id_mapping: Dict[str, str] = {}
    self.external_to_internal: Dict[str, str] = {}

    # 连接状态
    self.is_connected = False

    self.logger = logging.getLogger("LiveBroker")

  async def connect(self) -> bool:
    """连接到真实交易系统"""
    try:
      from quantx_infrastructure.services.trading_service import TradingService

      self.trading_service = TradingService(
        account_id=self.account_id,
        execution_mode="live",
      )

      # 验证账户
      account = await self.trading_service.get_account_info(realtime=True)
      if not account:
        self.logger.error("无法获取账户信息")
        return False

      self.is_connected = True
      self.logger.info(
        f"实盘 Broker 连接成功，账户: {account.account_id}, "
        f"可用资金: {account.cash:.2f}"
      )

      return True

    except Exception as e:
      self.logger.error(f"连接失败: {e}")
      return False

  async def disconnect(self) -> None:
    """断开连接"""
    self.is_connected = False
    self.logger.info("实盘 Broker 断开连接")

  async def place_order(self, request: OrderRequest) -> OrderResponse:
    """下单"""
    if not self.is_connected:
      return self._create_rejected_order(request, "未连接到交易系统")

    try:
      _, _, stable_idempotency_key = require_stable_command_key(
        (request.metadata or {}).get("idempotency_key"),
        (request.metadata or {}).get("trace_id"),
      )
    except AgentUnavailableError as exc:
      return self._create_rejected_order(request, str(exc))

    # 风控检查
    if self.enable_risk_control:
      try:
        risk_check = await self._risk_check(request)
      except Exception as exc:
        self.logger.error("本地风控检查异常: %s", exc)
        return self._create_rejected_order(request, str(exc))
      if not risk_check["passed"]:
        return self._create_rejected_order(request, risk_check["reason"])

    if self.trading_service is None:
      return self._create_rejected_order(request, "交易服务尚未初始化")

    try:
      # These conversions and all checks above happen before the durable
      # command enqueue boundary, so a failure here is authoritative zero-fill.
      xt_order_type = self._convert_order_type(request.order_type)
      xt_price_type = self._convert_price_type(request.price_type)
    except Exception as exc:
      self.logger.error("下单前转换失败: %s", exc)
      return self._create_rejected_order(request, str(exc))

    # 创建内部订单
    internal_order_id = self.generate_order_id()
    order = OrderResponse(
      order_id=internal_order_id,
      request=request,
      status=OrderStatus.PENDING,
      submit_time=time_utils.now(),
    )

    try:
      if not isinstance(request.execution_ref, ExecutionOwnerRef):
        raise AgentUnavailableError("EXECUTION_OWNER_REQUIRED")
      if not isinstance(request.environment, ExecutionEnvironment):
        raise AgentUnavailableError("EXECUTION_ENVIRONMENT_REQUIRED")
      if request.environment is not ExecutionEnvironment.LIVE:
        raise AgentUnavailableError("LIVE_BROKER_REQUIRES_LIVE_ENVIRONMENT")
      # Crossing this call means a database commit may already have succeeded
      # even if the caller later observes an exception. Only typed validation
      # failures below are guaranteed to have rolled the transaction back.
      result = await self.trading_service.place_order(
        stock_code=request.instrument_code,
        order_type=xt_order_type,
        order_volume=request.volume,
        price_type=xt_price_type,
        price=request.price,
        idempotency_key=stable_idempotency_key,
        execution_context={
          **{
            key: value
            for key, value in dict(request.metadata or {}).items()
            if str(key).strip().lower() not in _WIRE_IDENTITY_METADATA_KEYS
          },
          "strategy_order_id": internal_order_id,
        },
        # The request is the sole dispatch authority.  A LiveBroker instance
        # is intentionally not bound to any strategy/run owner because one
        # account broker serves strategy, manual, and managed-exit commands.
        execution_ref=request.execution_ref,
        environment=request.environment,
      )
      if not isinstance(result, dict) or not result.get("success"):
        message = result.get("message") if isinstance(result, dict) else "下单失败"
        raise RuntimeError(message or "下单失败")
      client_order_id = str(result.get("client_order_id") or "")
      if not client_order_id:
        raise RuntimeError("交易服务未返回 client_order_id")

      # 保存订单映射
      self.order_id_mapping[internal_order_id] = client_order_id
      self.external_to_internal[client_order_id] = internal_order_id

      # 排队和投递均不等于券商已报；等待 Engine 消费真实 order_report。
      order.status = OrderStatus.PENDING
      order.last_update_time = time_utils.now()

      self.orders[internal_order_id] = order
      await self.emit_order_update(order)

      self.logger.info(
        f"下单成功: {request.instrument_code} {request.order_type.value} "
        f"{request.volume}股 @ {request.price:.2f}, "
        f"订单ID: {internal_order_id} -> client:{client_order_id}"
      )

      return order

    except (AgentUnavailableError, InvalidOrderError) as exc:
      self.logger.warning("下单在持久化前被拒绝: %s", exc)
      return self._create_rejected_order(
        request,
        str(exc),
        order_id=internal_order_id,
      )
    except Exception as e:
      # Do not fabricate a zero-fill rejection after entering TradingService:
      # the enqueue commit may have reached PostgreSQL. The executor will mark
      # this intent RECONCILE_REQUIRED and retain its reservation/pending gate.
      self.logger.error("交易命令持久化结果不确定: %s", e)
      raise

  async def cancel_order(self, order_id: str) -> bool:
    """撤单"""
    if not self.is_connected:
      return False

    client_order_id = self.order_id_mapping.get(order_id)
    if not client_order_id:
      self.logger.error(f"找不到订单映射: {order_id}")
      return False

    try:
      result = await self.trading_service.cancel_pending_order(
        client_order_id=client_order_id,
      )

      if result.get("success"):
        self.logger.info(
          "撤单请求已接受: %s -> client:%s",
          order_id,
          result.get("client_order_id") or client_order_id,
        )
        return True
      self.logger.warning(
        "撤单请求失败: %s -> client:%s (%s)",
        order_id,
        client_order_id,
        result.get("message", ""),
      )
      return False

    except Exception as e:
      self.logger.error(f"撤单异常: {e}")
      return False

  async def get_order(self, order_id: str) -> Optional[OrderResponse]:
    """查询订单"""
    # 先查本地缓存
    if order_id in self.orders:
      return self.orders[order_id]

    # 查询外部系统
    if not self.is_connected:
      return None

    client_order_id = self.order_id_mapping.get(order_id)
    if client_order_id:
      try:
        external_order = await self.trading_service.order_for_client_order(
          client_order_id
        )
        if external_order:
          return self._convert_external_order(external_order, order_id)
      except Exception as e:
        self.logger.error(f"查询订单失败: {e}")

    return None

  async def get_portfolio_snapshot(self) -> tuple[AccountInfo, set[str]]:
    """Read cash, positions and reservation coverage from one complete snapshot."""
    from quantx_domain.clock import to_naive_utc, utcnow
    from sqlalchemy import select

    from quantx_infrastructure.database import AsyncSessionLocal
    from quantx_infrastructure.models.agent_runtime import (
      AccountExecutionControl,
      PendingTradeOrder,
    )
    from quantx_infrastructure.services.account_capacity_service import (
      load_authoritative_account_snapshot,
    )

    if not self.is_connected:
      raise AgentUnavailableError("LIVE_ACCOUNT_SNAPSHOT_DISCONNECTED")
    async with AsyncSessionLocal() as db:
      control = await db.get(AccountExecutionControl, self.account_id)
      if control is None or control.last_snapshot_at is None:
        raise AgentUnavailableError("LIVE_ACCOUNT_SNAPSHOT_UNAVAILABLE")
      age = (utcnow() - to_naive_utc(control.last_snapshot_at)).total_seconds()
      if control.reconcile_status != "READY" or not 0 <= age <= 90:
        raise AgentUnavailableError("LIVE_ACCOUNT_SNAPSHOT_NOT_READY")
      payload = await load_authoritative_account_snapshot(db, control)
      observed = {
        str(item.get("order_id") or item.get("broker_order_id") or "")
        for item in payload.get("orders", [])
        if item.get("account_id") == self.account_id
      } - {""}
      observed_clients = {
        str(item.get("client_order_id") or "")
        for item in payload.get("orders", [])
        if item.get("account_id") == self.account_id
      } - {""}
      pending = list((await db.scalars(select(PendingTradeOrder).where(
        PendingTradeOrder.account_id == self.account_id,
        PendingTradeOrder.environment == "LIVE",
        (PendingTradeOrder.broker_order_id.in_(observed)
         | PendingTradeOrder.client_order_id.in_(observed_clients)),
      ))).all())
      covered_ids = {
        str(value) for item in pending
        for value in (item.strategy_order_id, item.client_order_id, item.broker_order_id)
        if value
      }
      snapshot_at = control.last_snapshot_at
    raw_account = next(item for item in payload["accounts"] if item["account_id"] == self.account_id)
    positions = {}
    for item in payload["positions_by_account"][self.account_id]:
      code = str(item["stock_code"])
      volume = max(0, int(item.get("volume") or 0))
      if volume == 0:
        continue
      available = max(0, int(item.get("can_use_volume") or 0))
      average = float(item.get("avg_price") or 0)
      market_value = float(item.get("market_value") or 0)
      last_price = float(item.get("last_price") or market_value / volume)
      positions[code] = Position(
        instrument_code=code, long_volume=volume, available_volume=available,
        frozen_volume=max(0, int(item.get("frozen_volume") or 0)),
        today_buy_volume=max(0, volume - int(item.get("yesterday_volume") or 0)),
        long_avg_price=average, last_price=last_price, market_value=market_value,
        pnl=(last_price - average) * volume,
      )
    total_asset = float(raw_account["total_asset"])
    return AccountInfo(
      account_id=self.account_id, total_asset=total_asset,
      cash=float(raw_account["cash"]), frozen_cash=float(raw_account.get("frozen_cash") or 0),
      market_value=float(raw_account.get("market_value") or 0),
      total_pnl=total_asset - self.initial_capital, daily_pnl=0,
      positions=positions, last_update_time=snapshot_at,
    ), covered_ids

  async def get_position(self, instrument_code: str = None) -> Dict[str, Position]:
    account, _covered = await self.get_portfolio_snapshot()
    return {
      code: position for code, position in account.positions.items()
      if instrument_code is None or code == instrument_code
    }

  async def get_account(self) -> AccountInfo:
    account, _covered = await self.get_portfolio_snapshot()
    return account

  async def get_trades(
    self, start_time: Optional[datetime] = None, end_time: Optional[datetime] = None
  ) -> List[TradeRecord]:
    """查询成交记录"""
    if not self.is_connected:
      return []

    # 暂时返回本地缓存的成交记录
    trades = self.trades

    if start_time:
      trades = [t for t in trades if t.trade_time >= start_time]
    if end_time:
      trades = [t for t in trades if t.trade_time <= end_time]

    return trades

  async def _risk_check(self, request: OrderRequest) -> Dict[str, Any]:
    """风控检查"""
    # 计算订单金额
    order_amount = request.price * request.volume

    # 检查单笔金额限制
    if order_amount > self.max_order_amount:
      return {
        "passed": False,
        "reason": f"单笔金额 {order_amount:.2f} 超过限制 {self.max_order_amount:.2f}",
      }

    # 检查持仓限制
    if request.order_type in [OrderType.BUY, OrderType.BUY_TO_COVER]:
      positions = await self.get_position(request.instrument_code)
      current_position = positions.get(request.instrument_code)

      if current_position:
        new_value = current_position.market_value + order_amount
        if new_value > self.max_position_value:
          return {
            "passed": False,
            "reason": f"持仓市值 {new_value:.2f} 超过限制 {self.max_position_value:.2f}",
          }

    # 检查账户资金
    account = await self.get_account()
    if request.order_type in [OrderType.BUY, OrderType.BUY_TO_COVER]:
      required_cash = order_amount * 1.003  # 包含手续费
      if required_cash > account.cash:
        return {
          "passed": False,
          "reason": f"可用资金不足: 需要 {required_cash:.2f}, 可用 {account.cash:.2f}",
        }

    if request.order_type == OrderType.SELL:
      positions = await self.get_position(request.instrument_code)
      current_position = positions.get(request.instrument_code)
      available_volume = current_position.available_volume if current_position else 0
      if available_volume < request.volume:
        return {
          "passed": False,
          "reason": f"可用持仓不足: {available_volume} < {request.volume}",
        }

    return {"passed": True, "reason": ""}

  def _convert_order_type(self, order_type: OrderType) -> Any:
    """转换订单类型到 XTQuant"""
    from quantx_infrastructure.models.enums import OrderType as XTOrderType

    mapping = {
      OrderType.BUY: XTOrderType.BUY,
      OrderType.SELL: XTOrderType.SELL,
    }
    return mapping.get(order_type, XTOrderType.BUY)

  def _convert_price_type(self, price_type: PriceType) -> Any:
    """Convert the sole supported price type to XTQuant."""
    from quantx_infrastructure.models.enums import PriceType as XTPriceType

    if price_type is not PriceType.LIMIT:
      raise ValueError("实盘仅支持固定限价委托")
    return XTPriceType.FIX_PRICE

  def _convert_order_status(self, external_status: Any) -> OrderStatus:
    """转换外部订单状态"""
    from quantx_infrastructure.models.enums import OrderStatus as ExternalOrderStatus

    if hasattr(external_status, "name"):
      status_name = str(external_status.name)
    else:
      try:
        status_name = ExternalOrderStatus(external_status).name
      except (TypeError, ValueError):
        status_name = str(external_status or "").split(".")[-1].upper()
    status_mapping = {
      "UNREPORTED": OrderStatus.PENDING,
      "WAIT_REPORTING": OrderStatus.SUBMITTED,
      "REPORTED": OrderStatus.SUBMITTED,
      "REPORTED_CANCEL": OrderStatus.SUBMITTED,
      "PARTSUCC_CANCEL": OrderStatus.PARTIAL_FILLED,
      "PART_SUCC": OrderStatus.PARTIAL_FILLED,
      "PART_CANCEL": OrderStatus.CANCELLED,
      "CANCELED": OrderStatus.CANCELLED,
      "SUCCEEDED": OrderStatus.FILLED,
      "JUNK": OrderStatus.REJECTED,
      "UNKNOWN": OrderStatus.PENDING,
    }
    return status_mapping.get(status_name, OrderStatus.PENDING)

  def _convert_external_order(
    self, external_order: Any, internal_id: str
  ) -> OrderResponse:
    """转换外部订单到内部格式"""
    from quantx_infrastructure.models.enums import OrderType as ExternalOrderType

    # An external lookup has no safe way to reconstruct ownership.  The local
    # order created at enqueue time is the only typed source; callers should
    # never receive a fabricated/default StrategyRun request here.
    original = self.orders.get(internal_id)
    if original is None:
      raise AgentUnavailableError("ORDER_OWNER_UNAVAILABLE")

    order_type = (
      OrderType.BUY
      if external_order.type == ExternalOrderType.BUY
      else OrderType.SELL
    )
    return OrderResponse(
      order_id=internal_id,
      request=OrderRequest(
        instrument_code=external_order.stock_code,
        order_type=order_type,
        price_type=PriceType.LIMIT,
        volume=external_order.volume,
        execution_ref=original.request.execution_ref,
        environment=original.request.environment,
        price=external_order.price,
      ),
      status=self._convert_order_status(external_order.status),
      submit_time=external_order.time,
      filled_volume=external_order.traded_volume,
      filled_amount=float(external_order.traded_price or 0.0)
      * int(external_order.traded_volume or 0),
      avg_price=external_order.traded_price,
      last_update_time=getattr(external_order, "updated_at", None)
      or external_order.time,
    )

  def _create_rejected_order(
    self,
    request: OrderRequest,
    reason: str,
    *,
    order_id: Optional[str] = None,
  ) -> OrderResponse:
    """创建有本地零成交证明的拒单。"""
    request.metadata = local_pre_broker_zero_fill_metadata(
      request.metadata,
      reason=reason,
    )
    order = OrderResponse(
      order_id=order_id or self.generate_order_id(),
      request=request,
      status=OrderStatus.REJECTED,
      submit_time=time_utils.now(),
      error_message=reason,
    )
    self.orders[order.order_id] = order
    return order
