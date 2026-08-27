"""Persist user trade intent and enqueue delivery to a registered QMT agent."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Any, Mapping

from quantx_domain.clock import to_naive_utc, utcnow
from quantx_domain.strategies.ashare_managed_entry_plan import (
  ENTRY_PLAN_ENABLED_KEY,
)
from quantx_domain.trading.entry_plan import (
  EntryAuthorizationMode,
  EntryEnvironment,
  EntryTargetMode,
  ManagedEntryPlanConfig,
)
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.account import Account
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  AgentDevice,
  PendingTradeOrder,
  RuntimeComponentHeartbeat,
  StrategyOrderCorrelation,
  StrategyRuntimeEvent,
  TradeCommandOutbox,
  TTradeBatch,
)
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.entry_plan_authorization import (
  EntryPlanAuthorizationGrant,
)
from quantx_infrastructure.models.enums import AccountType
from quantx_infrastructure.models.enums import OrderStatus as PersistedOrderStatus
from quantx_infrastructure.models.enums import OrderType as PersistedOrderType
from quantx_infrastructure.models.liquidation import (
  ConditionalLiquidationOrder,
  ConditionalLiquidationStatus,
)
from quantx_infrastructure.models.order import Order as PersistedOrder
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.strategy import Strategy
from quantx_infrastructure.models.strategy_run import StrategyRun
from quantx_infrastructure.models.strategy_run_state import StrategyRunState
from quantx_infrastructure.models.trade import Trade
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.agent_session_guard import (
  API_HEARTBEAT_COMPONENT,
  evaluate_agent_session,
)
from quantx_infrastructure.services.entry_plan_authorization_service import (
  EntryPlanAuthorizationService,
  scope_from_managed_entry_config,
)
from quantx_infrastructure.services.exit_plan_authorization_service import (
  validate_exact_auto_exit_authorization,
)


class AgentUnavailableError(RuntimeError):
  pass


_AUTHORITATIVE_ENTRY_WORKING_STATUSES = (
  PersistedOrderStatus.UNREPORTED,
  PersistedOrderStatus.WAIT_REPORTING,
  PersistedOrderStatus.REPORTED,
  PersistedOrderStatus.REPORTED_CANCEL,
  PersistedOrderStatus.PARTSUCC_CANCEL,
  PersistedOrderStatus.PART_SUCC,
  PersistedOrderStatus.UNKNOWN,
)

_AUTHORITATIVE_ENTRY_TERMINAL_STATUSES = {
  int(PersistedOrderStatus.PART_CANCEL),
  int(PersistedOrderStatus.CANCELED),
  int(PersistedOrderStatus.SUCCEEDED),
  int(PersistedOrderStatus.JUNK),
}


@dataclass(frozen=True)
class QueuedTradeCommand:
  client_order_id: str
  message_id: str
  status: str


@dataclass(frozen=True)
class StrategyOrderCancelRequest:
  client_order_id: str
  strategy_order_id: str
  intent_id: str
  broker_order_id: str
  status: str
  request_metadata: dict[str, Any]
  local_terminal: bool = False


class TradeCommandService:
  MANUAL_RECONCILIATION_MAX_AGE_SECONDS = 90

  def __init__(self, db: AsyncSession) -> None:
    self.db = db

  @staticmethod
  def order_idempotency_digest(
    *, user_id: str, account_id: str, idempotency_key: str
  ) -> str:
    """Return the persisted business key used to recover queued order results."""

    return hashlib.sha256(
      f"order:{user_id}:{account_id}:{idempotency_key.strip()}".encode("utf-8")
    ).hexdigest()

  @staticmethod
  def _heartbeat_fresh(
    heartbeat: RuntimeComponentHeartbeat,
    api_heartbeat: RuntimeComponentHeartbeat | None,
    *,
    acceptable_statuses: set[str] | None = None,
  ) -> bool:
    return evaluate_agent_session(
      heartbeat,
      api_heartbeat,
      now=utcnow(),
      acceptable_statuses=acceptable_statuses or {"READY"},
    ).current

  async def _require_live_authorization(
    self,
    account_id: str,
    *,
    risk_reducing: bool = False,
  ) -> None:
    if not settings.enable_real_trading:
      raise AgentUnavailableError("服务端真实交易总开关未启用")
    if account_id not in set(settings.real_trading_account_allowlist or []):
      raise AgentUnavailableError("账户不在服务端真实交易白名单")
    control = await self.db.get(AccountExecutionControl, account_id)
    if control is None:
      raise AgentUnavailableError("账户尚未配置独立执行控制")
    state = str(control.authorization_state or "DISABLED").upper()
    if not risk_reducing and state == "KILLED":
      raise AgentUnavailableError("账户交易 kill switch 已触发")
    if not risk_reducing and state != "ENABLED":
      raise AgentUnavailableError("账户买入权限未启用")
    if control.reconcile_status != "READY":
      raise AgentUnavailableError("账户快照或仓位对账未就绪")

  async def _require_live_market_stream_ready(self, device: AgentDevice) -> None:
    heartbeat = await self.db.get(
      RuntimeComponentHeartbeat,
      f"qmt-agent:{device.id}",
    )
    details = dict(heartbeat.details or {}) if heartbeat is not None else {}
    if str(details.get("marketStreamStatus") or "").upper() != "READY":
      raise AgentUnavailableError("全市场行情尚未完成远程三阶段同步")

  async def _require_manual_live_authorization(
    self,
    account_id: str,
    *,
    risk_reducing: bool,
  ) -> AccountExecutionControl:
    """Lock and validate the account gate for a confirmed manual live order.

    The account control row is the first mutable trading row locked by both
    this path and ``AccountExecutionSafetyService.set_authorization_state``.
    If enqueue wins, a hard kill subsequently scans and cancels the new pending
    command; if the hard kill wins, a BUY observes the killed state here and is
    rejected before any outbox row is created.

    Risk-reducing SELL orders deliberately do not require an active controlled
    window, CANARY/LIVE enablement, or policy acknowledgement.  This preserves
    an escape path while paused or killed, but still requires a current,
    authoritative reconciliation snapshot and a ready live device.
    """

    if not settings.enable_real_trading:
      raise AgentUnavailableError("服务端真实交易总开关未启用")
    if account_id not in set(settings.real_trading_account_allowlist or []):
      raise AgentUnavailableError("账户不在服务端真实交易白名单")
    control = await self.db.get(
      AccountExecutionControl,
      account_id,
      with_for_update=True,
    )
    if control is None:
      raise AgentUnavailableError("账户尚未配置独立执行控制与对账状态")

    state = str(control.authorization_state or "DISABLED").upper()
    if not risk_reducing and state == "KILLED":
      raise AgentUnavailableError("账户交易 kill switch 已触发，禁止买入或加仓")
    if not risk_reducing and state != "ENABLED":
      raise AgentUnavailableError("账户买入权限未启用")
    if str(control.reconcile_status or "").upper() != "READY":
      raise AgentUnavailableError("账户资金、持仓、委托和成交快照尚未完成对账")

    snapshot_id = str(control.last_snapshot_id or "")
    snapshot_hash = str(control.last_snapshot_hash or "")
    snapshot_at = (
      to_naive_utc(control.last_snapshot_at)
      if control.last_snapshot_at is not None
      else None
    )
    snapshot_age = (
      (utcnow() - snapshot_at).total_seconds() if snapshot_at is not None else None
    )
    if (
      not snapshot_id
      or not snapshot_hash
      or snapshot_age is None
      or snapshot_age < 0
      or snapshot_age > self.MANUAL_RECONCILIATION_MAX_AGE_SECONDS
    ):
      raise AgentUnavailableError("账户完整对账快照缺失或已超过 90 秒")

    if risk_reducing:
      return control

    if not bool(control.controlled_window_active):
      raise AgentUnavailableError("手动买入需要基于最新快照建立账户实盘窗口")
    controlled_snapshot_id = str(control.controlled_window_snapshot_id or "")
    controlled_snapshot_hash = str(control.controlled_window_snapshot_hash or "")
    if (
      controlled_snapshot_id != snapshot_id or controlled_snapshot_hash != snapshot_hash
    ):
      raise AgentUnavailableError("账户实盘窗口快照与最新完整快照不一致")
    return control

  @staticmethod
  def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()

  @staticmethod
  def _managed_entry_state(
    plan_state: StrategyRunState | None,
    *,
    intent_id: str,
  ) -> dict[str, Any]:
    if plan_state is None:
      raise AgentUnavailableError("建仓计划权威状态快照缺失")
    managed_state = dict(
      dict(plan_state.custom_state or {}).get("managed_entry_plan") or {}
    )
    phase = str(managed_state.get("phase") or "").upper()
    pending_intent_id = str(managed_state.get("pending_intent_id") or "")
    if (
      not managed_state
      or phase not in {"AWAITING_APPROVAL", "ENTRY_PENDING"}
      or pending_intent_id != str(intent_id or "")
    ):
      raise AgentUnavailableError("建仓计划当前状态不允许买入")
    return managed_state

  @staticmethod
  def _require_managed_entry_capacity(
    *,
    plan_id: str,
    intent_id: str,
    config: ManagedEntryPlanConfig,
    managed_state: dict[str, Any],
    account: Account,
    position: Position | None,
    active_pending: list[PendingTradeOrder],
    executed_volumes: dict[str, int],
    requested_price: Decimal,
    requested_volume: int,
  ) -> None:
    """Recompute the exact remaining EntryPlan target at the outbox boundary."""

    total_asset = Decimal(str(account.total_asset or 0))
    requested_amount = requested_price * int(requested_volume)
    if total_asset <= 0 or requested_amount <= 0:
      raise AgentUnavailableError("建仓计划目标缺口所需账户数据无效")

    instrument_code = config.instrument_code
    other_orders = [
      order
      for order in active_pending
      if str(order.intent_id or "") != str(intent_id or "")
      and str(order.instrument_code or "") == instrument_code
    ]
    instrument_pending_amount = sum(
      (
        Decimal(str(order.limit_price or 0))
        * TradeCommandService._remaining_order_volume(order, executed_volumes)
        for order in other_orders
      ),
      Decimal("0"),
    )
    instrument_pending_volume = sum(
      TradeCommandService._remaining_order_volume(order, executed_volumes)
      for order in other_orders
    )
    plan_pending_orders = [
      order
      for order in other_orders
      if str(order.strategy_run_id or "") == plan_id
      or str(dict(order.request_metadata or {}).get("entry_plan_id") or "") == plan_id
    ]
    plan_pending_amount = sum(
      (
        Decimal(str(order.limit_price or 0))
        * TradeCommandService._remaining_order_volume(order, executed_volumes)
        for order in plan_pending_orders
      ),
      Decimal("0"),
    )

    try:
      filled_amount = max(
        Decimal("0"),
        Decimal(str(managed_state.get("filled_amount_cny", 0) or 0)),
      )
      filled_volume = max(
        0,
        int(managed_state.get("filled_volume", 0) or 0),
      )
    except (TypeError, ValueError, ArithmeticError) as exc:
      raise AgentUnavailableError("建仓计划累计成交状态无效") from exc
    if not filled_amount.is_finite():
      raise AgentUnavailableError("建仓计划累计成交状态无效")

    current_volume = max(0, int(getattr(position, "volume", 0) or 0))
    current_market_value = Decimal(str(getattr(position, "market_value", 0) or 0))
    if current_market_value <= 0 and current_volume > 0:
      current_market_value = requested_price * current_volume
    current_market_value = max(Decimal("0"), current_market_value)

    policy = config.target_policy
    baseline = policy.baseline_snapshot
    external_or_plan_amount = max(
      filled_amount,
      Decimal(max(0, current_volume - int(baseline.position_volume))) * requested_price,
      max(
        Decimal("0"),
        current_market_value - Decimal(str(baseline.market_value_cny)),
      ),
    )
    external_or_plan_volume = max(
      filled_volume,
      max(0, current_volume - int(baseline.position_volume)),
    )
    plan_budget_remaining = max(
      Decimal("0"),
      Decimal(str(policy.max_total_amount_cny)) - filled_amount - plan_pending_amount,
    )
    position_cap_remaining = max(
      Decimal("0"),
      total_asset * Decimal(str(policy.max_position_pct))
      - current_market_value
      - instrument_pending_amount,
    )

    if policy.mode == EntryTargetMode.TARGET_POSITION_PCT:
      target_remaining_amount = max(
        Decimal("0"),
        total_asset * Decimal(str(policy.target_position_pct or 0))
        - current_market_value
        - instrument_pending_amount,
      )
      target_remaining_volume: int | None = None
    elif policy.mode == EntryTargetMode.INCREMENTAL_AMOUNT_CNY:
      target_remaining_amount = max(
        Decimal("0"),
        Decimal(str(policy.incremental_amount_cny or 0))
        - external_or_plan_amount
        - instrument_pending_amount,
      )
      target_remaining_volume = None
    else:
      target_remaining_volume = max(
        0,
        int(policy.additional_volume or 0)
        - external_or_plan_volume
        - instrument_pending_volume,
      )
      target_remaining_amount = requested_price * target_remaining_volume

    amount_capacity = min(
      plan_budget_remaining,
      position_cap_remaining,
      target_remaining_amount,
    )
    if requested_amount > amount_capacity or (
      target_remaining_volume is not None
      and int(requested_volume) > target_remaining_volume
    ):
      raise AgentUnavailableError("买入委托超过建仓计划当前剩余目标或总预算")

  @staticmethod
  def _remaining_order_volume(
    order: PendingTradeOrder,
    executed_volumes: dict[str, int],
  ) -> int:
    order_key = str(
      getattr(order, "client_order_id", "") or getattr(order, "intent_id", "") or ""
    )
    return max(
      0,
      int(order.volume or 0) - max(0, int(executed_volumes.get(order_key, 0) or 0)),
    )

  async def _executed_volumes_for_orders(
    self,
    orders: list[PendingTradeOrder],
  ) -> dict[str, int]:
    broker_to_client: dict[int, str] = {}
    for order in orders:
      broker_order_id = str(order.broker_order_id or "").strip()
      if not broker_order_id:
        continue
      try:
        normalized_broker_id = int(broker_order_id)
      except (TypeError, ValueError):
        # QMT A-share broker order ids are integers.  Unknown ids cannot be
        # proven filled and therefore retain the conservative full reserve.
        continue
      broker_to_client[normalized_broker_id] = str(order.client_order_id)
    if not broker_to_client:
      return {}
    trades = list(
      (
        await self.db.execute(
          select(Trade).where(Trade.order_id.in_(tuple(broker_to_client)))
        )
      )
      .scalars()
      .all()
    )
    executed: dict[str, int] = {}
    for trade in trades:
      client_order_id = broker_to_client.get(int(trade.order_id))
      if not client_order_id:
        continue
      executed[client_order_id] = executed.get(client_order_id, 0) + max(
        0,
        int(trade.volume or 0),
      )
    return executed

  async def _require_no_conflicting_entry_exit(
    self,
    *,
    plan_id: str,
    strategy_run_id: str = "",
    account_id: str,
    instrument_code: str,
    working_orders: list[PendingTradeOrder],
    invalidate_auto_external_buy: bool = False,
  ) -> None:
    strategy_run_id = str(strategy_run_id or plan_id)
    instrument_orders = [
      order
      for order in working_orders
      if str(order.instrument_code or "") == instrument_code
    ]
    for order in instrument_orders:
      side = str(order.side or "").upper()
      status = str(order.status or "").upper()
      metadata = dict(getattr(order, "request_metadata", None) or {})
      belongs_to_plan = (
        str(getattr(order, "strategy_run_id", "") or "") == strategy_run_id
        or str(metadata.get("entry_plan_id") or "") == plan_id
      )
      if side == "SELL" or status == "RECONCILE_REQUIRED":
        raise AgentUnavailableError("同标的存在卖单或待对账委托，禁止继续买入")
      if side != "BUY":
        raise AgentUnavailableError("同标的存在方向不明的工作委托，禁止继续买入")
      if not belongs_to_plan:
        if invalidate_auto_external_buy:
          await EntryPlanAuthorizationService(self.db).invalidate(
            plan_id=plan_id,
            reason="ENTRY_EXTERNAL_WORKING_BUY",
            commit=True,
          )
          raise AgentUnavailableError(
            "同标的存在外部或其他策略工作买单，自动授权已失效"
          )
        raise AgentUnavailableError(
          "同标的存在外部或其他策略工作买单，请先完成对账后再买入"
        )
    reconcile_intent = await self.db.scalar(
      select(TradeIntentRecord.id)
      .where(
        TradeIntentRecord.strategy_run_id == strategy_run_id,
        TradeIntentRecord.instrument_code == instrument_code,
        TradeIntentRecord.status == "RECONCILE_REQUIRED",
      )
      .with_for_update()
      .limit(1)
    )
    if reconcile_intent is not None:
      raise AgentUnavailableError("建仓计划存在未收敛成交意图，禁止继续买入")
    liquidation = await self.db.scalar(
      select(ConditionalLiquidationOrder.id)
      .where(
        ConditionalLiquidationOrder.account_id == account_id,
        ConditionalLiquidationOrder.stock_code == instrument_code,
        ConditionalLiquidationOrder.status.in_(
          (
            ConditionalLiquidationStatus.SUBMITTED,
            ConditionalLiquidationStatus.PARTIALLY_EXITED,
          )
        ),
      )
      .with_for_update()
      .limit(1)
    )
    exit_plan = await self.db.scalar(
      select(AutoExitPlanRecord.plan_id)
      .where(
        AutoExitPlanRecord.account_id == account_id,
        AutoExitPlanRecord.instrument_code == instrument_code,
        or_(
          AutoExitPlanRecord.status == "EXIT_PENDING",
          AutoExitPlanRecord.pending_client_order_id.is_not(None),
        ),
      )
      .with_for_update()
      .limit(1)
    )
    if liquidation is not None or exit_plan is not None:
      raise AgentUnavailableError("同标的正在持续清仓，禁止继续买入")

  @staticmethod
  def _persisted_order_enum_int(value: Any) -> int | None:
    try:
      return int(getattr(value, "value", value))
    except (TypeError, ValueError):
      return None

  async def _require_no_authoritative_entry_order_conflict(
    self,
    *,
    plan_id: str,
    strategy_run_id: str = "",
    account_id: str,
    instrument_code: str,
    working_orders: list[PendingTradeOrder],
    invalidate_auto_external_buy: bool,
  ) -> None:
    """Fail closed on broker working orders absent from the command ledger.

    ``orders`` is the latest authoritative QMT snapshot.  A normal working row
    already represented by an active ``PendingTradeOrder`` is evaluated by the
    durable command-ledger checks and must not be counted or rejected twice.
    An UNKNOWN broker status is never trusted, even when represented locally.
    Any uncovered row requires a unique correlation to this exact EntryPlan;
    because the active pending row is missing, even that case is held for
    reconciliation rather than allowing a second broker order.
    """

    strategy_run_id = str(strategy_run_id or plan_id)
    trading_day_start = datetime.combine(time_utils.today(), datetime.min.time())
    trading_day_end = trading_day_start + timedelta(days=1)
    authoritative_orders = list(
      (
        await self.db.execute(
          select(PersistedOrder)
          .where(
            PersistedOrder.account_id == account_id,
            PersistedOrder.time >= trading_day_start,
            PersistedOrder.time < trading_day_end,
            PersistedOrder.status.in_(_AUTHORITATIVE_ENTRY_WORKING_STATUSES),
          )
          .with_for_update()
        )
      )
      .scalars()
      .all()
    )
    if not authoritative_orders:
      return

    pending_by_broker_id = {
      str(order.broker_order_id).strip(): order
      for order in working_orders
      if str(getattr(order, "broker_order_id", "") or "").strip()
    }
    uncovered_orders: list[PersistedOrder] = []
    represented_unknown: list[tuple[PersistedOrder, PendingTradeOrder]] = []
    for order in authoritative_orders:
      order_time = getattr(order, "time", None)
      if isinstance(order_time, datetime):
        normalized_order_time = time_utils.to_shanghai(order_time)
        if not trading_day_start <= normalized_order_time < trading_day_end:
          continue
      status_code = self._persisted_order_enum_int(order.status)
      # Defensive filtering keeps stale/incorrect repository fakes and future
      # enum additions from turning a terminal broker row into a false block.
      if status_code in _AUTHORITATIVE_ENTRY_TERMINAL_STATUSES:
        continue
      broker_order_id = str(order.id)
      pending = pending_by_broker_id.get(broker_order_id)
      if pending is not None:
        if status_code == int(PersistedOrderStatus.UNKNOWN):
          order_type = self._persisted_order_enum_int(order.type)
          if str(order.stock_code or "") == instrument_code or order_type == int(
            PersistedOrderType.BUY
          ):
            represented_unknown.append((order, pending))
        continue
      order_type = self._persisted_order_enum_int(order.type)
      if str(order.stock_code or "") == instrument_code or order_type == int(
        PersistedOrderType.BUY
      ):
        uncovered_orders.append(order)

    if not uncovered_orders and not represented_unknown:
      return

    correlations_by_broker_id: dict[str, list[StrategyOrderCorrelation]] = {}
    if uncovered_orders:
      broker_order_ids = tuple(str(order.id) for order in uncovered_orders)
      correlations = list(
        (
          await self.db.execute(
            select(StrategyOrderCorrelation)
            .where(
              StrategyOrderCorrelation.account_id == account_id,
              StrategyOrderCorrelation.broker_order_id.in_(broker_order_ids),
            )
            .with_for_update()
          )
        )
        .scalars()
        .all()
      )
      for correlation in correlations:
        correlations_by_broker_id.setdefault(
          str(correlation.broker_order_id), []
        ).append(correlation)

    conflicts: list[tuple[PersistedOrder, bool]] = []
    for order, pending in represented_unknown:
      metadata = dict(getattr(pending, "request_metadata", None) or {})
      belongs_to_plan = (
        str(getattr(pending, "strategy_run_id", "") or "") == strategy_run_id
        or str(metadata.get("entry_plan_id") or "") == plan_id
      )
      conflicts.append((order, not belongs_to_plan))
    for order in uncovered_orders:
      correlations = correlations_by_broker_id.get(str(order.id), [])
      belongs_to_plan = False
      if len(correlations) == 1:
        correlation = correlations[0]
        metadata = dict(correlation.request_metadata or {})
        belongs_to_plan = (
          str(order.stock_code or "") == instrument_code
          and str(correlation.strategy_run_id or "") == strategy_run_id
          and str(metadata.get("entry_plan_id") or "") == plan_id
        )
      conflicts.append((order, not belongs_to_plan))

    if not conflicts:
      return
    conflict_order, external = next(
      (
        (order, is_external)
        for order, is_external in conflicts
        if invalidate_auto_external_buy
        and is_external
        and self._persisted_order_enum_int(order.type) == int(PersistedOrderType.BUY)
      ),
      next(
        ((order, is_external) for order, is_external in conflicts if is_external),
        conflicts[0],
      ),
    )
    order_type = self._persisted_order_enum_int(conflict_order.type)
    is_buy = order_type == int(PersistedOrderType.BUY)
    if invalidate_auto_external_buy and external and is_buy:
      await EntryPlanAuthorizationService(self.db).invalidate(
        plan_id=plan_id,
        reason="ENTRY_EXTERNAL_WORKING_BUY",
        commit=True,
      )
      raise AgentUnavailableError("检测到外部或其他策略工作买单，自动授权已失效")
    if external:
      raise AgentUnavailableError(
        "检测到外部、其他策略或方向不明的工作委托，请处理后重新确认"
      )
    raise AgentUnavailableError(
      "本计划存在仅见于 Broker 的未完成委托，需先对账收敛后再买入"
    )

  async def _require_no_unattributed_buy_trades(
    self,
    *,
    plan_id: str,
    strategy_run_id: str = "",
    account_id: str,
    instrument_code: str,
    grant: EntryPlanAuthorizationGrant,
  ) -> None:
    strategy_run_id = str(strategy_run_id or plan_id)
    authorized_at = getattr(grant, "authorized_at", None)
    if authorized_at is None:
      raise AgentUnavailableError("自动买入授权缺少生效时间")
    buy_trades = list(
      (
        await self.db.execute(
          select(Trade).where(
            Trade.account_id == account_id,
            Trade.stock_code == instrument_code,
            Trade.order_type == int(PersistedOrderType.BUY),
            Trade.time >= authorized_at,
          )
        )
      )
      .scalars()
      .all()
    )
    if not buy_trades:
      return
    broker_order_ids = {str(trade.order_id) for trade in buy_trades}
    correlations = list(
      (
        await self.db.execute(
          select(StrategyOrderCorrelation).where(
            StrategyOrderCorrelation.broker_order_id.in_(broker_order_ids)
          )
        )
      )
      .scalars()
      .all()
    )
    attributed_order_ids = {
      str(item.broker_order_id)
      for item in correlations
      if str(item.strategy_run_id or "") == strategy_run_id
    }
    if broker_order_ids - attributed_order_ids:
      await EntryPlanAuthorizationService(self.db).invalidate(
        plan_id=plan_id,
        reason="ENTRY_UNATTRIBUTED_REAL_BUY",
        commit=True,
      )
      raise AgentUnavailableError("检测到授权后的未归因真实买入，自动授权已失效")

  @classmethod
  def _working_buy_cash_reserve(
    cls,
    orders: list[PendingTradeOrder],
    *,
    intent_id: str,
    executed_volumes: dict[str, int],
  ) -> Decimal:
    return sum(
      (
        Decimal(str(order.limit_price or 0))
        * cls._remaining_order_volume(order, executed_volumes)
        for order in orders
        if str(order.intent_id or "") != str(intent_id or "")
        and str(order.side or "").upper() == "BUY"
      ),
      Decimal("0"),
    )

  async def _require_auto_entry_live_snapshot(
    self,
    account_id: str,
  ) -> AccountExecutionControl:
    """Lock the live account gate without requiring a manual trade window."""

    if not settings.enable_real_trading:
      raise AgentUnavailableError("服务端真实交易总开关未启用")
    if account_id not in set(settings.real_trading_account_allowlist or []):
      raise AgentUnavailableError("账户不在服务端真实交易白名单")
    control = await self.db.get(
      AccountExecutionControl,
      account_id,
      with_for_update=True,
    )
    if control is None:
      raise AgentUnavailableError("账户尚未配置独立执行控制与对账状态")
    state = str(control.authorization_state or "DISABLED").upper()
    if state == "KILLED":
      raise AgentUnavailableError("账户交易 kill switch 已触发，禁止自动买入")
    if state != "ENABLED":
      raise AgentUnavailableError("账户买入权限未启用")
    if str(control.reconcile_status or "").upper() != "READY":
      raise AgentUnavailableError("账户资金、持仓、委托和成交快照尚未完成对账")
    snapshot_at = (
      to_naive_utc(control.last_snapshot_at)
      if control.last_snapshot_at is not None
      else None
    )
    snapshot_age = (
      (utcnow() - snapshot_at).total_seconds() if snapshot_at is not None else None
    )
    if (
      not str(control.last_snapshot_id or "")
      or len(str(control.last_snapshot_hash or "")) != 64
      or snapshot_age is None
      or snapshot_age < 0
      or snapshot_age > self.MANUAL_RECONCILIATION_MAX_AGE_SECONDS
    ):
      raise AgentUnavailableError("账户完整对账快照缺失或已超过 90 秒")
    return control

  async def _exact_auto_entry_device(
    self,
    *,
    account_id: str,
    instrument_code: str,
    side: str,
    limit_price: Decimal,
    volume: int,
    strategy_run_id: str,
    intent_id: str,
    bucket: str,
    policy_version: int,
    request_metadata: dict[str, Any],
  ) -> AgentDevice:
    """Perform the atomic, authoritative second gate for one managed BUY."""

    if str(side or "").upper() != "BUY":
      raise AgentUnavailableError("精确自动建仓门禁只能用于 LIVE BUY")
    await self._require_auto_entry_live_snapshot(account_id)
    plan_id = str(request_metadata.get("entry_plan_id") or "").strip()
    grant_id = str(
      request_metadata.get("auto_entry_authorization_grant_id") or ""
    ).strip()
    if not plan_id or not str(strategy_run_id or "").strip() or not grant_id:
      raise AgentUnavailableError("自动买入命令缺少精确计划与 grant 绑定")

    run_row = (
      await self.db.execute(
        select(StrategyRun, Strategy)
        .join(Strategy, Strategy.id == StrategyRun.strategy_id)
        .where(StrategyRun.id == strategy_run_id)
        .with_for_update()
      )
    ).one_or_none()
    if run_row is None:
      raise AgentUnavailableError("自动买入对应建仓计划不存在")
    run, strategy = run_row
    parameters = dict(run.parameters or {})
    bound_plan_id = str(getattr(run, "plan_id", "") or "").strip()
    if not bound_plan_id:
      binding = parameters.get("_managed_plan_binding")
      if isinstance(binding, Mapping):
        bound_plan_id = str(binding.get("plan_id") or "").strip()
    bound_plan_id = bound_plan_id or str(getattr(run, "id", strategy_run_id))
    if (
      strategy.class_name != "AshareManagedEntryPlanStrategy"
      or self._enum_value(run.mode) != "live"
      or self._enum_value(run.status) != "running"
      or parameters.get(ENTRY_PLAN_ENABLED_KEY) is not True
      or list(run.instruments or []) != [instrument_code]
      or bound_plan_id != plan_id
    ):
      raise AgentUnavailableError("建仓计划已暂停、终止或不再绑定当前标的")
    try:
      config = ManagedEntryPlanConfig.from_dict(
        dict(parameters.get("managed_entry_plan") or {})
      )
      scope = scope_from_managed_entry_config(
        plan_id=plan_id,
        config=config,
        run_id=strategy_run_id,
      )
    except (TypeError, ValueError) as exc:
      raise AgentUnavailableError("建仓计划权威配置无效") from exc
    if (
      config.execution_policy.environment != EntryEnvironment.LIVE
      or config.execution_policy.authorization_mode != EntryAuthorizationMode.AUTO
      or config.instrument_code != instrument_code
      or config.bucket != str(bucket or "").lower()
      or int(config.config_version) != int(policy_version or 0)
    ):
      raise AgentUnavailableError("建仓计划环境、授权模式、仓位桶或版本不匹配")
    intent = await self.db.get(
      TradeIntentRecord,
      str(intent_id or ""),
      with_for_update=True,
    )
    intent_metadata = dict(intent.intent_metadata or {}) if intent is not None else {}
    if (
      intent is None
      or str(intent.strategy_run_id or "") != strategy_run_id
      or str(intent.instrument_code or "") != instrument_code
      or str(intent.direction or "").upper() != "BUY"
      or str(intent.bucket or "").lower() != config.bucket
      or str(intent.status or "").upper() != "PENDING"
      or str(intent_metadata.get("execution_mode") or "").upper() != "AUTO"
      or str(intent_metadata.get("entry_plan_id") or "") != plan_id
      or int(intent_metadata.get("entry_config_version") or 0) != config.config_version
      or str(intent_metadata.get("auto_entry_authorization_grant_id") or "") != grant_id
      or not bool(intent_metadata.get("exact_auto_entry_authorized"))
      or str(intent_metadata.get("auto_entry_plan_fingerprint") or "")
      != scope.plan_fingerprint
      or str(intent_metadata.get("auto_entry_rule_fingerprint") or "")
      != scope.rule_fingerprint
      or str(request_metadata.get("auto_entry_plan_fingerprint") or "")
      != scope.plan_fingerprint
      or str(request_metadata.get("auto_entry_rule_fingerprint") or "")
      != scope.rule_fingerprint
    ):
      raise AgentUnavailableError("自动买入意图与当前计划或精确授权不匹配")

    plan_state = (
      await self.db.execute(
        select(StrategyRunState)
        .where(StrategyRunState.run_id == strategy_run_id)
        .with_for_update()
      )
    ).scalar_one_or_none()
    managed_state = self._managed_entry_state(
      plan_state,
      intent_id=intent_id,
    )

    price = Decimal(str(limit_price))
    requested_amount = price * int(volume)
    if not price.is_finite() or price <= 0 or requested_amount <= 0:
      raise AgentUnavailableError("自动买入委托价格或金额无效")
    if intent.target_volume is not None and int(volume) > int(intent.target_volume):
      raise AgentUnavailableError("自动买入委托超过意图目标数量")
    if intent.target_amount is not None and requested_amount > Decimal(
      str(intent.target_amount)
    ):
      raise AgentUnavailableError("自动买入委托超过意图目标金额")

    account = (
      await self.db.execute(
        select(Account)
        .where(
          Account.account_id == account_id,
          Account.account_type == AccountType.STOCK,
        )
        .with_for_update()
      )
    ).scalar_one_or_none()
    if account is None or Decimal(str(account.total_asset or 0)) <= 0:
      raise AgentUnavailableError("账户资产快照不可用")
    position = await self.db.scalar(
      select(Position)
      .where(
        Position.account_id == account_id,
        Position.stock_code == instrument_code,
      )
      .with_for_update()
    )
    position_market_value = Decimal(str(getattr(position, "market_value", 0) or 0))
    if position is not None and position_market_value <= 0:
      position_price = Decimal(str(getattr(position, "last_price", 0) or limit_price))
      position_market_value = position_price * int(position.volume or 0)
    active_pending = list(
      (
        await self.db.execute(
          select(PendingTradeOrder)
          .where(
            PendingTradeOrder.account_id == account_id,
            PendingTradeOrder.status.in_(
              (
                "QUEUED",
                "PENDING",
                "DELIVERED",
                "SUBMITTED",
                "ACCEPTED",
                "PARTIAL_FILLED",
                "PARTIALLY_FILLED",
                "RECONCILE_REQUIRED",
                "CANCEL_REQUESTED",
              )
            ),
          )
          .with_for_update()
        )
      )
      .scalars()
      .all()
    )
    grant = await self.db.get(
      EntryPlanAuthorizationGrant,
      grant_id,
      with_for_update=True,
    )
    if grant is None:
      raise AgentUnavailableError("自动买入精确授权不存在")
    await self._require_no_conflicting_entry_exit(
      plan_id=plan_id,
      strategy_run_id=strategy_run_id,
      account_id=account_id,
      instrument_code=instrument_code,
      working_orders=active_pending,
      invalidate_auto_external_buy=True,
    )
    await self._require_no_authoritative_entry_order_conflict(
      plan_id=plan_id,
      strategy_run_id=strategy_run_id,
      account_id=account_id,
      instrument_code=instrument_code,
      working_orders=active_pending,
      invalidate_auto_external_buy=True,
    )
    executed_volumes = await self._executed_volumes_for_orders(active_pending)
    self._require_managed_entry_capacity(
      plan_id=plan_id,
      intent_id=intent_id,
      config=config,
      managed_state=managed_state,
      account=account,
      position=position,
      active_pending=active_pending,
      executed_volumes=executed_volumes,
      requested_price=price,
      requested_volume=volume,
    )
    if any(
      str(dict(item.request_metadata or {}).get("entry_plan_id") or "") == plan_id
      and str(item.intent_id or "") != str(intent_id)
      and str(item.side or "").upper() == "BUY"
      for item in active_pending
    ):
      raise AgentUnavailableError("建仓计划已有未完成买单，禁止并发路由")
    pending_amount = sum(
      (
        Decimal(str(item.limit_price))
        * self._remaining_order_volume(item, executed_volumes)
        for item in active_pending
        if str(item.intent_id or "") != str(intent_id)
        and str(item.instrument_code or "") == instrument_code
        and str(item.side or "").upper() == "BUY"
      ),
      Decimal("0"),
    )
    working_cash_reserve = self._working_buy_cash_reserve(
      active_pending,
      intent_id=intent_id,
      executed_volumes=executed_volumes,
    )
    available_cash = Decimal(str(account.cash or 0))
    if requested_amount + working_cash_reserve > available_cash:
      raise AgentUnavailableError("自动买入超过当前权威可用资金")
    cash_buffer = Decimal(str(account.total_asset)) * Decimal(
      str(config.pacing_policy.cash_buffer_pct)
    )
    if available_cash - working_cash_reserve - requested_amount < cash_buffer:
      raise AgentUnavailableError("自动买入会突破计划绑定的最低现金缓冲")
    resulting_position_pct = (
      position_market_value + pending_amount + requested_amount
    ) / Decimal(str(account.total_asset))

    protected_price = Decimal(str(intent_metadata.get("protected_limit_price") or 0))
    if not protected_price.is_finite() or protected_price <= 0:
      raise AgentUnavailableError("自动买入意图缺少受保护的决策价格")
    proposed_slippage_bps = int(
      max(
        Decimal("0"),
        (price - protected_price) / protected_price * Decimal("10000"),
      ).to_integral_value(rounding=ROUND_CEILING)
    )
    proposed_price_deviation_bps = int(
      (
        abs(price - protected_price) / protected_price * Decimal("10000")
      ).to_integral_value(rounding=ROUND_CEILING)
    )
    validation = await EntryPlanAuthorizationService(self.db).validate_or_invalidate(
      plan_id=plan_id,
      current_scope=scope,
      account_id=account_id,
      proposed_amount_cny=requested_amount,
      proposed_buy_price=price,
      proposed_slippage_bps=proposed_slippage_bps,
      proposed_price_deviation_bps=proposed_price_deviation_bps,
      resulting_position_pct=resulting_position_pct,
      commit=False,
    )
    if (
      not validation.valid
      or validation.balance is None
      or validation.balance.grant_id != grant_id
    ):
      raise AgentUnavailableError(f"自动买入精确授权已失效：{validation.code}")
    await self._require_no_unattributed_buy_trades(
      plan_id=plan_id,
      strategy_run_id=strategy_run_id,
      account_id=account_id,
      instrument_code=instrument_code,
      grant=grant,
    )
    current_position_volume = max(0, int(getattr(position, "volume", 0) or 0))
    explained_position_volume = max(
      0,
      int(config.target_policy.baseline_snapshot.position_volume)
      + int(getattr(grant, "consumed_total_volume", 0) or 0),
    )
    if current_position_volume > explained_position_volume:
      await EntryPlanAuthorizationService(self.db).invalidate(
        plan_id=plan_id,
        reason="ENTRY_UNEXPLAINED_POSITION_INCREASE",
        commit=True,
      )
      raise AgentUnavailableError("检测到未归属于本计划的外部增仓，自动授权已失效")
    device = await self._device_for(
      user_id=str(grant.subject_user_id),
      account_id=account_id,
      execution_mode="live",
    )
    heartbeat = await self.db.get(
      RuntimeComponentHeartbeat,
      f"qmt-agent:{device.id}",
    )
    details = dict(heartbeat.details or {}) if heartbeat is not None else {}
    capabilities = {
      str(value).strip().lower()
      for value in list(details.get("capabilities") or [])
      if str(value).strip()
    }
    if (
      heartbeat is None
      or str(heartbeat.status or "").upper() != "READY"
      or "live" not in capabilities
      or str(details.get("protocolVersion") or "") != "1.1"
    ):
      raise AgentUnavailableError("自动买入要求唯一 READY、live、协议 1.1 的 QMT Agent")
    return device

  async def _managed_manual_entry_device(
    self,
    *,
    account_id: str,
    instrument_code: str,
    limit_price: Decimal,
    volume: int,
    strategy_run_id: str,
    intent_id: str,
    bucket: str,
    policy_version: int,
    intent: TradeIntentRecord,
  ) -> AgentDevice:
    """Atomically recheck a device-confirmed managed BUY before outbox insert."""

    await self._require_manual_live_authorization(
      account_id,
      risk_reducing=False,
    )
    locked_intent = await self.db.get(
      TradeIntentRecord,
      str(intent_id or ""),
      with_for_update=True,
    )
    if locked_intent is None or str(locked_intent.id) != str(intent.id):
      raise AgentUnavailableError("逐笔确认意图不存在或已变化")
    intent = locked_intent
    row = (
      await self.db.execute(
        select(StrategyRun, Strategy)
        .join(Strategy, Strategy.id == StrategyRun.strategy_id)
        .where(StrategyRun.id == strategy_run_id)
        .with_for_update()
      )
    ).one_or_none()
    if row is None:
      raise AgentUnavailableError("逐笔确认对应建仓计划不存在")
    run, strategy = row
    parameters = dict(run.parameters or {})
    bound_plan_id = str(getattr(run, "plan_id", "") or "").strip()
    if not bound_plan_id:
      binding = parameters.get("_managed_plan_binding")
      if isinstance(binding, Mapping):
        bound_plan_id = str(binding.get("plan_id") or "").strip()
    bound_plan_id = bound_plan_id or str(getattr(run, "id", strategy_run_id))
    if (
      strategy.class_name != "AshareManagedEntryPlanStrategy"
      or self._enum_value(run.mode) != "live"
      or self._enum_value(run.status) != "running"
      or parameters.get("entry_plan_enabled") is not True
      or list(run.instruments or []) != [instrument_code]
    ):
      raise AgentUnavailableError("建仓计划已暂停、终止或不再绑定当前标的")
    try:
      config = ManagedEntryPlanConfig.from_dict(
        dict(parameters.get("managed_entry_plan") or {})
      )
    except (TypeError, ValueError) as exc:
      raise AgentUnavailableError("建仓计划权威配置无效") from exc
    intent_metadata = dict(intent.intent_metadata or {})
    if (
      config.execution_policy.environment != EntryEnvironment.LIVE
      or config.instrument_code != instrument_code
      or config.bucket != str(bucket or "").lower()
      or config.config_version != int(policy_version or 0)
      or str(intent.strategy_run_id or "") != strategy_run_id
      or str(intent.instrument_code or "") != instrument_code
      or str(intent.direction or "").upper() != "BUY"
      or str(intent.status or "").upper() != "APPROVED"
      or str(intent_metadata.get("entry_plan_id") or "") != bound_plan_id
      or int(intent_metadata.get("entry_config_version") or 0) != config.config_version
      or str(intent_metadata.get("execution_mode") or "").upper() != "MANUAL_CONFIRM"
    ):
      raise AgentUnavailableError("逐笔确认意图与当前建仓计划不匹配")
    plan_state = (
      await self.db.execute(
        select(StrategyRunState)
        .where(StrategyRunState.run_id == strategy_run_id)
        .with_for_update()
      )
    ).scalar_one_or_none()
    managed_state = self._managed_entry_state(
      plan_state,
      intent_id=intent_id,
    )

    price = Decimal(str(limit_price))
    requested_amount = price * int(volume)
    if (
      not price.is_finite()
      or price <= 0
      or int(volume) <= 0
      or price > Decimal(str(config.completion_policy.max_buy_price))
      or requested_amount
      > Decimal(str(config.pacing_policy.max_single_intent_amount_cny))
    ):
      raise AgentUnavailableError("逐笔确认买单超过价格或单笔风险上限")
    if intent.target_volume is not None and int(volume) > int(intent.target_volume):
      raise AgentUnavailableError("逐笔确认买单超过已确认意图数量")
    if intent.target_amount is not None and requested_amount > Decimal(
      str(intent.target_amount)
    ):
      raise AgentUnavailableError("逐笔确认买单超过已确认意图金额")
    account = (
      await self.db.execute(
        select(Account)
        .where(
          Account.account_id == account_id,
          Account.account_type == AccountType.STOCK,
        )
        .with_for_update()
      )
    ).scalar_one_or_none()
    if account is None or Decimal(str(account.total_asset or 0)) <= 0:
      raise AgentUnavailableError("账户资产快照不可用")
    pending_orders = list(
      (
        await self.db.execute(
          select(PendingTradeOrder)
          .where(
            PendingTradeOrder.account_id == account_id,
            PendingTradeOrder.status.in_(
              (
                "QUEUED",
                "PENDING",
                "DELIVERED",
                "SUBMITTED",
                "ACCEPTED",
                "PARTIAL_FILLED",
                "PARTIALLY_FILLED",
                "RECONCILE_REQUIRED",
                "CANCEL_REQUESTED",
              )
            ),
          )
          .with_for_update()
        )
      )
      .scalars()
      .all()
    )
    await self._require_no_conflicting_entry_exit(
      plan_id=bound_plan_id,
      strategy_run_id=strategy_run_id,
      account_id=account_id,
      instrument_code=instrument_code,
      working_orders=pending_orders,
    )
    await self._require_no_authoritative_entry_order_conflict(
      plan_id=bound_plan_id,
      strategy_run_id=strategy_run_id,
      account_id=account_id,
      instrument_code=instrument_code,
      working_orders=pending_orders,
      invalidate_auto_external_buy=False,
    )
    executed_volumes = await self._executed_volumes_for_orders(pending_orders)
    working_cash_reserve = self._working_buy_cash_reserve(
      pending_orders,
      intent_id=intent_id,
      executed_volumes=executed_volumes,
    )
    available_cash = Decimal(str(account.cash or 0))
    cash_buffer = Decimal(str(account.total_asset)) * Decimal(
      str(config.pacing_policy.cash_buffer_pct)
    )
    if available_cash - working_cash_reserve - requested_amount < cash_buffer:
      raise AgentUnavailableError("逐笔确认买入会突破计划绑定的最低现金缓冲")

    position = await self.db.scalar(
      select(Position)
      .where(
        Position.account_id == account_id,
        Position.stock_code == instrument_code,
      )
      .with_for_update()
    )
    current_market_value = Decimal(str(getattr(position, "market_value", 0) or 0))
    if position is not None and current_market_value <= 0:
      current_market_value = price * int(position.volume or 0)
    self._require_managed_entry_capacity(
      plan_id=bound_plan_id,
      intent_id=intent_id,
      config=config,
      managed_state=managed_state,
      account=account,
      position=position,
      active_pending=pending_orders,
      executed_volumes=executed_volumes,
      requested_price=price,
      requested_volume=volume,
    )
    instrument_pending = sum(
      (
        Decimal(str(order.limit_price))
        * self._remaining_order_volume(order, executed_volumes)
        for order in pending_orders
        if str(order.instrument_code or "") == instrument_code
        and str(order.intent_id or "") != intent_id
        and str(order.side or "").upper() == "BUY"
      ),
      Decimal("0"),
    )
    resulting_position_pct = (
      current_market_value + instrument_pending + requested_amount
    ) / Decimal(str(account.total_asset))
    if resulting_position_pct > Decimal(str(config.target_policy.max_position_pct)):
      raise AgentUnavailableError("逐笔确认买入会突破计划仓位上限")

    device = await self._device_for_account(account_id, "live")
    heartbeat = await self.db.get(
      RuntimeComponentHeartbeat,
      f"qmt-agent:{device.id}",
    )
    details = dict(heartbeat.details or {}) if heartbeat is not None else {}
    if str(details.get("protocolVersion") or "") != "1.1":
      raise AgentUnavailableError("逐笔确认买入要求协议 1.1 的就绪 QMT Agent")
    return device

  async def _device_for(
    self,
    *,
    user_id: str,
    account_id: str,
    execution_mode: str,
    allow_degraded_cancel: bool = False,
  ) -> AgentDevice:
    result = await self.db.execute(
      select(AgentDevice).where(
        AgentDevice.user_id == user_id,
        AgentDevice.revoked_at.is_(None),
      )
    )
    devices = result.scalars().all()
    api_heartbeat = (
      await self.db.get(
        RuntimeComponentHeartbeat,
        API_HEARTBEAT_COMPONENT,
      )
      if execution_mode == "live"
      else None
    )
    eligible: list[AgentDevice] = []
    for device in devices:
      allowed = list(device.authorized_account_ids or [])
      capabilities = {
        str(capability).lower() for capability in list(device.capabilities or [])
      }
      if account_id not in allowed or execution_mode not in capabilities:
        continue
      if execution_mode == "live":
        heartbeat = await self.db.get(
          RuntimeComponentHeartbeat,
          f"qmt-agent:{device.id}",
        )
        acceptable_statuses = (
          {"READY", "EMERGENCY_STOP", "RECONCILE_REQUIRED"}
          if allow_degraded_cancel
          else {"READY"}
        )
        if (
          heartbeat is None or str(heartbeat.status).upper() not in acceptable_statuses
        ):
          continue
        if not self._heartbeat_fresh(
          heartbeat,
          api_heartbeat,
          acceptable_statuses=acceptable_statuses,
        ):
          continue
      eligible.append(device)
    if execution_mode == "live" and len(eligible) > 1:
      raise AgentUnavailableError(
        "同一账户检测到多个就绪 live QMT Agent，已拒绝路由交易命令"
      )
    if eligible:
      return eligible[0]
    raise AgentUnavailableError(
      f"没有已登记、就绪且具备交易能力（{execution_mode}）的 QMT Agent"
    )

  async def _device_for_account(
    self,
    account_id: str,
    execution_mode: str,
  ) -> AgentDevice:
    result = await self.db.execute(
      select(AgentDevice).where(AgentDevice.revoked_at.is_(None))
    )
    api_heartbeat = (
      await self.db.get(
        RuntimeComponentHeartbeat,
        API_HEARTBEAT_COMPONENT,
      )
      if execution_mode == "live"
      else None
    )
    eligible: list[AgentDevice] = []
    for device in result.scalars().all():
      capabilities = {
        str(capability).lower() for capability in list(device.capabilities or [])
      }
      if (
        account_id in list(device.authorized_account_ids or [])
        and execution_mode in capabilities
      ):
        if execution_mode == "live":
          heartbeat = await self.db.get(
            RuntimeComponentHeartbeat,
            f"qmt-agent:{device.id}",
          )
          if (
            heartbeat is None
            or str(heartbeat.status).upper() != "READY"
            or not self._heartbeat_fresh(heartbeat, api_heartbeat)
          ):
            continue
        eligible.append(device)
    if execution_mode == "live" and len(eligible) > 1:
      raise AgentUnavailableError(
        "同一账户检测到多个就绪 live QMT Agent，已拒绝路由交易命令"
      )
    if eligible:
      return eligible[0]
    raise AgentUnavailableError(
      f"没有已登记、就绪且具备交易能力（{execution_mode}）的 QMT Agent"
    )

  async def enqueue_order(
    self,
    *,
    user_id: str,
    account_id: str,
    instrument_code: str,
    side: str,
    order_type: str,
    limit_price: Decimal,
    volume: int,
    strategy_name: str = "",
    order_remark: str = "",
    trace_id: str = "",
    idempotency_key: str = "",
    execution_mode: str = "paper",
    strategy_run_id: str = "",
    strategy_order_id: str = "",
    intent_id: str = "",
    batch_id: str = "",
    bucket: str = "manual",
    t_trade_role: str = "",
    risk_decision_id: str = "",
    substitution_plan: dict[str, Any] | None = None,
    policy_version: int = 0,
    request_metadata: dict[str, Any] | None = None,
    manual_live: bool = False,
    reason_tags: list[str] | None = None,
    commit_transaction: bool = True,
  ) -> QueuedTradeCommand:
    if volume <= 0:
      raise ValueError("委托数量必须大于 0")
    normalized_mode = execution_mode.strip().lower()
    if normalized_mode not in {"paper", "live"}:
      raise ValueError("交易命令 execution_mode 必须是 paper 或 live")
    normalized_role = t_trade_role.strip().upper()
    if normalized_role not in {"", "ENTRY", "EXIT"}:
      raise ValueError("做 T 订单角色必须是 ENTRY 或 EXIT")
    immutable_metadata = dict(request_metadata or {})
    if manual_live and normalized_mode != "live":
      raise ValueError("手动实盘授权只能用于 live 交易命令")
    risk_reducing = normalized_role == "EXIT" or side.upper() == "SELL"
    if manual_live:
      # This lock must precede the outbox lookup/insert to match the account
      # hard-kill control -> pending/outbox lock order.
      await self._require_manual_live_authorization(
        account_id,
        risk_reducing=risk_reducing,
      )

    raw_idempotency_key = idempotency_key.strip() or trace_id.strip()
    if raw_idempotency_key:
      business_idempotency_key = self.order_idempotency_digest(
        user_id=user_id,
        account_id=account_id,
        idempotency_key=raw_idempotency_key,
      )
      existing = (
        await self.db.execute(
          select(TradeCommandOutbox).where(
            TradeCommandOutbox.idempotency_key == business_idempotency_key
          )
        )
      ).scalar_one_or_none()
      if existing is not None:
        return QueuedTradeCommand(
          existing.client_order_id,
          existing.message_id,
          existing.delivery_status,
        )
    else:
      business_idempotency_key = f"generated:{uuid.uuid4()}"

    if normalized_mode == "live" and not manual_live:
      await self._require_live_authorization(
        account_id,
        risk_reducing=risk_reducing,
      )
    device = await self._device_for(
      user_id=user_id,
      account_id=account_id,
      execution_mode=normalized_mode,
    )
    if normalized_mode == "live" and not risk_reducing:
      await self._require_live_market_stream_ready(device)
    now = utcnow()
    client_order_id = str(uuid.uuid4())
    message_id = str(uuid.uuid4())
    expires_at = now + timedelta(minutes=2)
    payload: dict[str, Any] = {
      "command_kind": "PLACE_ORDER",
      "client_order_id": client_order_id,
      "account_id": account_id,
      "execution_mode": normalized_mode,
      "instance_id": strategy_name or "manual",
      "instrument_code": instrument_code,
      "side": side,
      "order_type": order_type,
      "limit_price": str(limit_price),
      "volume": volume,
      "strategy_name": strategy_name,
      "bucket": bucket or "manual",
      "order_remark": order_remark,
      "trace_id": trace_id or message_id,
      "risk_decision_id": risk_decision_id or trace_id or message_id,
      "reason_tags": sorted(
        {
          str(value).strip()
          for value in (
            list(reason_tags) if reason_tags is not None else ["queued-command"]
          )
          if str(value).strip()
        }
      ),
      "substitution_plan": substitution_plan,
      "strategy_run_id": strategy_run_id,
      "strategy_order_id": strategy_order_id,
      "intent_id": intent_id,
      "batch_id": batch_id,
      "t_trade_role": normalized_role,
      "policy_version": max(0, int(policy_version or 0)),
      "request_metadata": immutable_metadata,
      "expires_at": expires_at.isoformat() + "Z",
    }
    self.db.add(
      PendingTradeOrder(
        client_order_id=client_order_id,
        user_id=user_id,
        account_id=account_id,
        instrument_code=instrument_code,
        side=side,
        order_type=order_type,
        limit_price=str(limit_price),
        volume=volume,
        status="QUEUED",
        execution_mode=normalized_mode,
        strategy_run_id=strategy_run_id or None,
        strategy_order_id=strategy_order_id or None,
        intent_id=intent_id or None,
        batch_id=batch_id or None,
        bucket=bucket or "manual",
        t_trade_role=normalized_role or None,
        risk_decision_id=risk_decision_id or None,
        trace_id=trace_id or message_id,
        substitution_plan=substitution_plan,
        request_metadata=immutable_metadata,
      )
    )
    if strategy_run_id and strategy_order_id and intent_id:
      self.db.add(
        StrategyOrderCorrelation(
          id=str(uuid.uuid4()),
          client_order_id=client_order_id,
          account_id=account_id,
          strategy_run_id=strategy_run_id,
          strategy_order_id=strategy_order_id,
          intent_id=intent_id,
          batch_id=batch_id or None,
          bucket=bucket or "manual",
          t_trade_role=normalized_role or None,
          execution_mode=normalized_mode,
          risk_decision_id=risk_decision_id or None,
          trace_id=trace_id or message_id,
          substitution_plan=substitution_plan,
          request_metadata=immutable_metadata,
        )
      )
      if batch_id:
        batch = await self.db.get(TTradeBatch, batch_id)
        if batch is None:
          batch = TTradeBatch(
            batch_id=batch_id,
            account_id=account_id,
            instrument_code=instrument_code,
            strategy_run_id=strategy_run_id,
            target_volume=volume,
            policy_version=max(0, int(policy_version or 0)),
          )
          self.db.add(batch)
        if normalized_role == "ENTRY":
          batch.entry_intent_id = intent_id
          batch.entry_client_order_id = client_order_id
          batch.status = "ENTRY_QUEUED"
        elif normalized_role == "EXIT":
          batch.exit_intent_id = intent_id
          batch.exit_client_order_id = client_order_id
          batch.status = "EXIT_TRIGGERED"
    self.db.add(
      TradeCommandOutbox(
        message_id=message_id,
        client_order_id=client_order_id,
        idempotency_key=business_idempotency_key,
        device_id=device.id,
        account_id=account_id,
        payload=payload,
        delivery_status="QUEUED",
        expires_at=expires_at,
        attempts=0,
      )
    )
    if not commit_transaction:
      # The caller owns one atomic transaction spanning its authorization
      # record and the pending/outbox rows.  Integrity failures propagate so
      # the caller can roll back the entire unit rather than half-commit it.
      await self.db.flush()
      return QueuedTradeCommand(client_order_id, message_id, "QUEUED")
    try:
      await self.db.commit()
    except IntegrityError:
      await self.db.rollback()
      existing = (
        await self.db.execute(
          select(TradeCommandOutbox).where(
            TradeCommandOutbox.idempotency_key == business_idempotency_key
          )
        )
      ).scalar_one_or_none()
      if existing is None:
        raise
      return QueuedTradeCommand(
        existing.client_order_id,
        existing.message_id,
        existing.delivery_status,
      )
    return QueuedTradeCommand(client_order_id, message_id, "QUEUED")

  async def enqueue_order_for_account(
    self,
    *,
    account_id: str,
    instrument_code: str,
    side: str,
    order_type: str,
    limit_price: Decimal,
    volume: int,
    strategy_name: str = "",
    order_remark: str = "",
    trace_id: str = "",
    idempotency_key: str = "",
    execution_mode: str = "paper",
    strategy_run_id: str = "",
    strategy_order_id: str = "",
    intent_id: str = "",
    batch_id: str = "",
    bucket: str = "manual",
    t_trade_role: str = "",
    risk_decision_id: str = "",
    substitution_plan: dict[str, Any] | None = None,
    policy_version: int = 0,
    request_metadata: dict[str, Any] | None = None,
    require_risk_reducing_live_authorization: bool = False,
    authorization_user_id: str = "",
  ) -> QueuedTradeCommand:
    normalized_execution_mode = str(execution_mode or "paper").lower()
    metadata = dict(request_metadata or {})
    if require_risk_reducing_live_authorization:
      authorization_plan_id = str(metadata.get("exit_plan_id") or "").strip()
      authorization_fingerprint = str(
        metadata.get("auto_exit_authorization_fingerprint") or ""
      ).strip()
      if normalized_execution_mode != "live" or str(side or "").upper() != "SELL":
        raise AgentUnavailableError("精确自动退出门禁只能用于 LIVE 风险降低卖单")
      if not str(authorization_user_id or "").strip():
        raise AgentUnavailableError("自动退出授权缺少确认用户绑定")
      if not authorization_plan_id or not authorization_fingerprint:
        raise AgentUnavailableError("自动退出命令缺少精确计划授权绑定")
      plan = (
        await self.db.execute(
          select(AutoExitPlanRecord)
          .where(AutoExitPlanRecord.plan_id == authorization_plan_id)
          .with_for_update()
        )
      ).scalar_one_or_none()
      if (
        plan is None
        or str(plan.account_id) != str(account_id)
        or str(plan.instrument_code) != str(instrument_code)
        or int(plan.config_version or 0) != int(policy_version or 0)
        or str(plan.auto_exit_authorization_user_id or "") != str(authorization_user_id)
        or str(plan.auto_exit_authorization_fingerprint or "")
        != authorization_fingerprint
      ):
        raise AgentUnavailableError("自动退出计划、版本、标的或授权人绑定不匹配")
      validation = await validate_exact_auto_exit_authorization(
        self.db,
        plan,
        lock_mutable_rows=True,
      )
      if not validation.valid:
        raise AgentUnavailableError(f"自动退出授权已失效：{validation.code}")
      intent = await self.db.get(
        TradeIntentRecord,
        str(intent_id or ""),
        with_for_update=True,
      )
      intent_metadata = dict(intent.intent_metadata or {}) if intent is not None else {}
      plan_state = dict(plan.plan_state or {})
      if (
        intent is None
        or str(intent.owner_type or "") != "EXIT_PLAN"
        or str(intent.owner_id or "") != authorization_plan_id
        or str(intent.account_id or "") != str(account_id)
        or str(intent.instrument_code or "") != str(instrument_code)
        or str(intent.direction or "").upper() != "SELL"
        or str(intent.status or "").upper() != "PENDING"
        or str(plan_state.get("pending_intent_id") or "") != str(intent_id or "")
        or not bool(intent_metadata.get("exact_auto_exit_authorized"))
        or str(intent_metadata.get("auto_exit_authorization_fingerprint") or "")
        != authorization_fingerprint
        or str(intent_metadata.get("auto_exit_authorization_user_id") or "")
        != str(authorization_user_id)
        or int(intent.target_volume or 0) < int(volume)
      ):
        raise AgentUnavailableError("自动退出意图与精确计划授权不匹配")
      position = await self.db.scalar(
        select(Position)
        .where(
          Position.account_id == account_id,
          Position.stock_code == instrument_code,
        )
        .with_for_update()
      )
      if (
        int(volume) > int(plan.remaining_volume or 0)
        or position is None
        or int(volume) > int(position.can_use_volume or 0)
      ):
        raise AgentUnavailableError("自动退出委托超过当前计划剩余量或实时可卖量")
      await self._require_manual_live_authorization(
        account_id,
        risk_reducing=True,
      )
      device = await self._device_for(
        user_id=str(authorization_user_id),
        account_id=account_id,
        execution_mode="live",
      )
      heartbeat = await self.db.get(
        RuntimeComponentHeartbeat,
        f"qmt-agent:{device.id}",
      )
      details = dict(heartbeat.details or {}) if heartbeat is not None else {}
      capabilities = {
        str(value).strip().lower()
        for value in list(details.get("capabilities") or [])
        if str(value).strip()
      }
      if (
        heartbeat is None
        or str(heartbeat.status or "").upper() != "READY"
        or "live" not in capabilities
        or str(details.get("protocolVersion") or "") != "1.1"
      ):
        raise AgentUnavailableError(
          "自动退出要求唯一 READY、live、协议 1.1 的 QMT Agent"
        )
    else:
      persisted_intent = (
        await self.db.get(TradeIntentRecord, str(intent_id or ""))
        if normalized_execution_mode == "live"
        and str(side or "").upper() == "BUY"
        and str(intent_id or "")
        else None
      )
      persisted_metadata = (
        dict(persisted_intent.intent_metadata or {})
        if persisted_intent is not None
        else {}
      )
      is_managed_auto_entry = bool(
        persisted_intent is not None
        and str(persisted_intent.direction or "").upper() == "BUY"
        and str(persisted_metadata.get("execution_mode") or "").upper() == "AUTO"
        and str(persisted_metadata.get("entry_plan_id") or "")
      )
      is_managed_manual_entry = bool(
        persisted_intent is not None
        and str(persisted_intent.direction or "").upper() == "BUY"
        and str(persisted_metadata.get("execution_mode") or "").upper()
        == "MANUAL_CONFIRM"
        and str(persisted_metadata.get("entry_plan_id") or "")
      )
      if is_managed_auto_entry:
        device = await self._exact_auto_entry_device(
          account_id=account_id,
          instrument_code=instrument_code,
          side=side,
          limit_price=limit_price,
          volume=volume,
          strategy_run_id=strategy_run_id,
          intent_id=intent_id,
          bucket=bucket,
          policy_version=policy_version,
          request_metadata=metadata,
        )
      elif is_managed_manual_entry:
        device = await self._managed_manual_entry_device(
          account_id=account_id,
          instrument_code=instrument_code,
          limit_price=limit_price,
          volume=volume,
          strategy_run_id=strategy_run_id,
          intent_id=intent_id,
          bucket=bucket,
          policy_version=policy_version,
          intent=persisted_intent,
        )
      else:
        device = await self._device_for_account(account_id, normalized_execution_mode)
    return await self.enqueue_order(
      user_id=device.user_id,
      account_id=account_id,
      instrument_code=instrument_code,
      side=side,
      order_type=order_type,
      limit_price=limit_price,
      volume=volume,
      strategy_name=strategy_name,
      order_remark=order_remark,
      trace_id=trace_id,
      idempotency_key=idempotency_key,
      execution_mode=normalized_execution_mode,
      strategy_run_id=strategy_run_id,
      strategy_order_id=strategy_order_id,
      intent_id=intent_id,
      batch_id=batch_id,
      bucket=bucket,
      t_trade_role=t_trade_role,
      risk_decision_id=risk_decision_id,
      substitution_plan=substitution_plan,
      policy_version=policy_version,
      request_metadata=request_metadata,
    )

  async def enqueue_cancel(
    self,
    *,
    user_id: str,
    account_id: str,
    broker_order_id: str,
    idempotency_key: str = "",
    execution_mode: str = "paper",
    commit_transaction: bool = True,
  ) -> QueuedTradeCommand:
    business_idempotency_key = hashlib.sha256(
      (
        f"cancel:{user_id}:{account_id}:{idempotency_key.strip() or broker_order_id}"
      ).encode("utf-8")
    ).hexdigest()
    existing = (
      await self.db.execute(
        select(TradeCommandOutbox).where(
          TradeCommandOutbox.idempotency_key == business_idempotency_key
        )
      )
    ).scalar_one_or_none()
    if existing is not None:
      return QueuedTradeCommand(
        existing.client_order_id,
        existing.message_id,
        existing.delivery_status,
      )
    device = await self._device_for(
      user_id=user_id,
      account_id=account_id,
      execution_mode=execution_mode,
      allow_degraded_cancel=True,
    )
    now = utcnow()
    client_order_id = f"cancel:{uuid.uuid4()}"
    message_id = str(uuid.uuid4())
    expires_at = now + timedelta(minutes=2)
    payload = {
      "command_kind": "CANCEL_ORDER",
      "client_order_id": client_order_id,
      "account_id": account_id,
      "execution_mode": execution_mode,
      "broker_order_id": str(broker_order_id),
      "trace_id": message_id,
      "expires_at": expires_at.isoformat() + "Z",
    }
    self.db.add(
      TradeCommandOutbox(
        message_id=message_id,
        client_order_id=client_order_id,
        idempotency_key=business_idempotency_key,
        device_id=device.id,
        account_id=account_id,
        payload=payload,
        delivery_status="QUEUED",
        expires_at=expires_at,
        attempts=0,
      )
    )
    if commit_transaction:
      try:
        await self.db.commit()
      except IntegrityError:
        await self.db.rollback()
        existing = (
          await self.db.execute(
            select(TradeCommandOutbox).where(
              TradeCommandOutbox.idempotency_key == business_idempotency_key
            )
          )
        ).scalar_one_or_none()
        if existing is None:
          raise
        return QueuedTradeCommand(
          existing.client_order_id,
          existing.message_id,
          existing.delivery_status,
        )
    else:
      await self.db.flush()
    return QueuedTradeCommand(client_order_id, message_id, "QUEUED")

  async def request_strategy_buy_cancellations(
    self,
    *,
    strategy_run_id: str,
    reason: str,
  ) -> list[StrategyOrderCancelRequest]:
    """Persist cancel intent without treating command delivery as terminal.

    A command that has never left the durable outbox can be cancelled locally.
    Once delivery may have happened, the pending order stays
    ``CANCEL_REQUESTED`` until an authoritative broker terminal report arrives.
    """

    orders = list(
      (
        await self.db.execute(
          select(PendingTradeOrder)
          .where(
            PendingTradeOrder.strategy_run_id == strategy_run_id,
            PendingTradeOrder.side.in_(("BUY", "BUY_TO_COVER")),
            PendingTradeOrder.status.in_(
              (
                "QUEUED",
                "PENDING",
                "DELIVERED",
                "SUBMITTED",
                "ACCEPTED",
                "PARTIAL_FILLED",
                "PARTIALLY_FILLED",
                "RECONCILE_REQUIRED",
                "CANCEL_REQUESTED",
              )
            ),
          )
          .with_for_update()
        )
      )
      .scalars()
      .all()
    )
    if not orders:
      return []

    client_order_ids = [str(order.client_order_id) for order in orders]
    outboxes = list(
      (
        await self.db.execute(
          select(TradeCommandOutbox)
          .where(TradeCommandOutbox.client_order_id.in_(client_order_ids))
          .with_for_update()
        )
      )
      .scalars()
      .all()
    )
    outbox_by_client = {str(row.client_order_id): row for row in outboxes}
    local_candidate_ids = [
      str(order.client_order_id)
      for order in orders
      if not str(order.broker_order_id or "").strip()
      and (
        (outbox := outbox_by_client.get(str(order.client_order_id))) is not None
        and str(outbox.delivery_status or "") == "QUEUED"
      )
    ]
    runtime_event_client_ids: set[str] = set()
    if local_candidate_ids:
      runtime_event_client_ids = {
        str(client_order_id)
        for client_order_id in (
          await self.db.execute(
            select(StrategyRuntimeEvent.client_order_id)
            .where(
              StrategyRuntimeEvent.client_order_id.in_(local_candidate_ids),
              StrategyRuntimeEvent.event_type.in_(("ORDER", "TRADE")),
            )
            .with_for_update()
          )
        )
        .scalars()
        .all()
      }
    results: list[StrategyOrderCancelRequest] = []
    for order in orders:
      client_order_id = str(order.client_order_id)
      broker_order_id = str(order.broker_order_id or "")
      local_terminal = False
      result_request_metadata = dict(order.request_metadata or {})
      if broker_order_id:
        order.status = "CANCEL_REQUESTED"
        order.status_reason = str(reason or "entry plan cancellation requested")[:256]
        await self.enqueue_cancel(
          user_id=str(order.user_id),
          account_id=str(order.account_id),
          broker_order_id=broker_order_id,
          idempotency_key=(f"entry-plan-cancel:{client_order_id}:{broker_order_id}"),
          execution_mode=str(order.execution_mode or "paper").lower(),
          commit_transaction=False,
        )
      else:
        outbox = outbox_by_client.get(client_order_id)
        if (
          outbox is not None
          and str(outbox.delivery_status or "") == "QUEUED"
          and client_order_id not in runtime_event_client_ids
        ):
          intent_id = str(order.intent_id or "").strip()
          intent = None
          intent_metadata: dict[str, Any] = {}
          if intent_id:
            intent = await self.db.get(
              TradeIntentRecord,
              intent_id,
              with_for_update=True,
            )
            intent_metadata = (
              dict(intent.intent_metadata or {}) if intent is not None else {}
            )
            if intent is not None:
              result_request_metadata = intent_metadata
          pending_metadata = dict(order.request_metadata or {})
          has_managed_entry_marker = bool(
            str(pending_metadata.get("entry_plan_id") or "").strip()
            or str(intent_metadata.get("entry_plan_id") or "").strip()
          )
          is_bound_managed_entry = bool(
            intent is not None
            and str(intent.strategy_run_id or "") == strategy_run_id
            and str(intent.direction or "").upper() == "BUY"
            and str(intent_metadata.get("entry_plan_id") or "").strip()
            == str(pending_metadata.get("entry_plan_id") or "").strip()
          )
          try:
            executed_volume = int(intent.executed_volume or 0) if intent else 0
            last_source_sequence = int(getattr(order, "last_source_sequence", 0) or 0)
            executed_price = (
              Decimal(str(intent.executed_price or 0))
              if intent is not None
              else Decimal("0")
            )
          except (TypeError, ValueError, ArithmeticError):
            executed_volume = -1
            last_source_sequence = -1
            executed_price = Decimal("NaN")
          proven_zero_fill = bool(
            (intent is not None or not has_managed_entry_marker)
            and executed_volume == 0
            and (intent is None or intent.executed_time is None)
            and executed_price.is_finite()
            and executed_price <= 0
            and last_source_sequence == 0
            and getattr(order, "last_source_event_at", None) is None
            and (
              intent is None
              or str(intent.status or "").upper()
              not in {"FILLED", "PARTIAL_FILLED", "PARTIALLY_FILLED"}
            )
          )
          if not proven_zero_fill or (
            has_managed_entry_marker and not is_bound_managed_entry
          ):
            order.status = "RECONCILE_REQUIRED"
            order.status_reason = "local cancel could not prove zero broker execution"
          else:
            outbox.delivery_status = "CANCELLED"
            order.status = "CANCELLED"
            order.status_reason = "cancelled before Agent delivery"
            local_terminal = True
            if is_bound_managed_entry:
              reason_code = "ENTRY_PLAN_CANCELLED_BEFORE_AGENT_DELIVERY"
              intent.status = "RECONCILED_ZERO_FILL"
              intent.notes = reason_code
              intent_metadata["execution_terminal_reason"] = reason_code
              intent_metadata["execution_terminal_source"] = "LOCAL_OUTBOX_CANCEL"
              intent.intent_metadata = intent_metadata
              result_request_metadata = intent_metadata
        elif client_order_id in runtime_event_client_ids:
          order.status = "RECONCILE_REQUIRED"
          order.status_reason = "durable broker runtime event blocks local cancel"
        else:
          order.status = "CANCEL_REQUESTED"
          order.status_reason = str(
            reason or "waiting for broker order id before cancellation"
          )[:256]
      results.append(
        StrategyOrderCancelRequest(
          client_order_id=client_order_id,
          strategy_order_id=str(order.strategy_order_id or ""),
          intent_id=str(order.intent_id or ""),
          broker_order_id=broker_order_id,
          status=str(order.status or ""),
          request_metadata=result_request_metadata,
          local_terminal=local_terminal,
        )
      )
    await self.db.commit()
    return results

  async def enqueue_cancel_for_account(
    self,
    *,
    account_id: str,
    broker_order_id: str,
    idempotency_key: str = "",
    execution_mode: str = "paper",
  ) -> QueuedTradeCommand:
    device = await self._device_for_account(account_id, execution_mode)
    return await self.enqueue_cancel(
      user_id=device.user_id,
      account_id=account_id,
      broker_order_id=broker_order_id,
      idempotency_key=idempotency_key,
      execution_mode=execution_mode,
    )
