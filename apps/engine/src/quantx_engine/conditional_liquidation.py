"""Engine-owned conditional liquidation monitor."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from quantx_domain.trading.exit_plan import ExitEvaluationContext
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.enums import AccountType
from quantx_infrastructure.models.liquidation import (
  ConditionalLiquidationStatus,
)
from quantx_infrastructure.repositories.conditional_liquidation_order_repository import (
  ConditionalLiquidationOrderRepository,
)
from quantx_infrastructure.services.auto_exit_plan_service import AutoExitPlanService
from quantx_infrastructure.services.intraday_volume_scanner import (
  intraday_volume_scanner,
)
from quantx_infrastructure.services.liquidation_service import (
  ConditionalLiquidationEvaluation,
  LiquidationService,
)

logger = logging.getLogger(__name__)


class ConditionalLiquidationMonitor:
  def __init__(
    self,
    interval_seconds: float = 1.0,
    *,
    scanner=intraday_volume_scanner,
  ):
    self.interval_seconds = max(0.5, float(interval_seconds or 1.0))
    self.scanner = scanner
    self._task: Optional[asyncio.Task] = None
    self._stopping = asyncio.Event()
    self.market_data_gate_rejections = 0
    self._market_gate_blocked = False

  async def start(self) -> None:
    if self._task and not self._task.done():
      return
    await self.scanner.start()
    self._stopping = asyncio.Event()
    self._task = asyncio.create_task(self._run(), name="ConditionalLiquidationMonitor")
    logger.info("条件清仓单监控器已启动")

  async def stop(self) -> None:
    self._stopping.set()
    if not self._task:
      return
    self._task.cancel()
    try:
      await self._task
    except asyncio.CancelledError:
      pass
    logger.info("条件清仓单监控器已停止")

  async def _run(self) -> None:
    while not self._stopping.is_set():
      try:
        await self.evaluate_all_active_orders()
      except asyncio.CancelledError:
        raise
      except Exception as exc:
        logger.warning("条件清仓单监控扫描失败: %s", exc)
      try:
        await asyncio.wait_for(
          self._stopping.wait(),
          timeout=self.interval_seconds,
        )
      except asyncio.TimeoutError:
        continue

  async def evaluate_all_active_orders(
    self,
    *,
    account_id: Optional[str] = None,
    stock_code: Optional[str] = None,
  ) -> list:
    async for db in get_async_db():
      repo = ConditionalLiquidationOrderRepository(db)
      orders = await repo.find_active(
        account_id=account_id,
        stock_code=stock_code,
      )
      break
    else:
      orders = []

    results = []
    if orders:
      if not self.scanner.is_running:
        await self.scanner.start()
      self.scanner.touch()
    states = self._ready_states() if orders else {}
    market_session_open = (
      await self.scanner.hub.is_trading_session() if orders else False
    )
    for order in orders:
      account_type = getattr(order, "account_type", None) or AccountType.STOCK
      if isinstance(account_type, str):
        account_type = AccountType.__members__.get(account_type, AccountType.STOCK)
      service = LiquidationService(
        account_id=order.account_id,
        account_type=account_type,
      )
      if not order.exit_plan_id:
        try:
          order = await service.upsert_conditional_liquidation_order(
            order_id=order.id,
            stock_code=order.stock_code,
            instrument_name=order.instrument_name,
            enabled=bool(order.enabled),
            target_profit_pct=order.target_profit_pct,
            target_price=order.target_price,
            strategy=order.strategy,
            dynamic_policy=dict(order.dynamic_policy or {}),
            execution_mode=order.execution_mode,
            # Legacy rows cannot carry autonomous LIVE authority into the
            # unified monitor without a device-bound exact challenge.
            auto_exit_authorized=False,
            sell_mode=order.sell_mode,
            sell_ratio_pct=order.sell_ratio_pct,
            sell_volume=order.sell_volume,
            remark=order.remark,
          )
        except Exception as exc:
          logger.warning("旧条件清仓单迁移失败 %s: %s", order.id, exc)
      if order.exit_plan_id:
        result = await self._evaluate_adaptive_order(
          order,
          service=service,
          market_session_open=market_session_open,
          state=(
            states.get(order.stock_code)
            if self._market_data_ready()
            else None
          ),
        )
      else:
        result = ConditionalLiquidationEvaluation(
          order=order,
          triggered=False,
          submitted=False,
          message="legacy_exit_plan_migration_required",
          error="旧条件单尚未迁移到 ExitPlanBook，未执行卖出",
        )
      results.append(result)
    return results

  def _market_data_ready(self) -> bool:
    return bool(getattr(getattr(self.scanner, "hub", None), "is_ready", False))

  def _ready_states(self) -> dict:
    if self._market_data_ready():
      self._market_gate_blocked = False
      return self.scanner.snapshot_states()
    self.market_data_gate_rejections += 1
    if not self._market_gate_blocked:
      status = getattr(
        getattr(getattr(self.scanner, "hub", None), "status", None),
        "value",
        "OFFLINE",
      )
      logger.warning(
        "WholeQuoteHub 非 READY，条件清仓使用不可用行情上下文: status=%s",
        status,
      )
      self._market_gate_blocked = True
    return {}

  async def _evaluate_adaptive_order(
    self,
    order,
    *,
    service,
    state,
    market_session_open: bool,
  ):
    now = time_utils.now()
    position = await service._get_position_for_condition(order.stock_code)
    if not order.exit_plan_id:
      await service._update_conditional_order(
        order.id,
        {"last_checked_at": now, "last_error": "missing_exit_plan"},
      )
      return ConditionalLiquidationEvaluation(
        order=order,
        triggered=False,
        message="missing_exit_plan",
        error="missing_exit_plan",
      )

    context = self._adaptive_context(state, now=now)
    submit_result = await AutoExitPlanService().evaluate_and_submit(
      plan_id=order.exit_plan_id,
      context=context,
      position=position,
      market_session_open=market_session_open,
      market_ready=self._market_data_ready,
    )
    async for db in get_async_db():
      refreshed = await ConditionalLiquidationOrderRepository(db).find_by_id(order.id)
      break
    else:
      refreshed = order
    current_order = refreshed or order
    avg_price = float(getattr(position, "avg_price", 0.0) or 0.0)
    latest_price = float(context.bid_price or context.current_price or 0.0) or None
    profit_pct = (
      (latest_price - avg_price) / avg_price * 100.0
      if latest_price is not None and avg_price > 0
      else None
    )
    submitted = bool(submit_result and submit_result.get("success"))
    return ConditionalLiquidationEvaluation(
      order=current_order,
      triggered=(
        submitted
        or current_order.status == ConditionalLiquidationStatus.SUBMITTED
      ),
      submitted=submitted,
      sell_volume=int(current_order.submitted_volume or 0),
      order_id=current_order.submitted_order_id,
      latest_price=latest_price,
      profit_pct=profit_pct,
      message="adaptive_exit_submitted" if submitted else "adaptive_exit_following",
      error=current_order.last_error,
    )

  @staticmethod
  def _adaptive_context(state, *, now: datetime) -> ExitEvaluationContext:
    if state is None or state.updated_at is None:
      return ExitEvaluationContext(
        timestamp=now,
        current_price=0.0,
        market_data_age_seconds=999.0,
        volume_data_age_seconds=999.0,
        source="WHOLE_QUOTE_UNAVAILABLE",
      )
    age_seconds = max(0.0, (now - state.updated_at).total_seconds())
    bid_price = next((float(value) for value in state.bid_price if value > 0), 0.0)
    ask_price = next((float(value) for value in state.ask_price if value > 0), 0.0)
    bid_volume = sum(float(value) for value in state.bid_vol[:5] if value > 0)
    ask_volume = sum(float(value) for value in state.ask_vol[:5] if value > 0)
    depth_total = bid_volume + ask_volume
    depth_imbalance = (
      (bid_volume - ask_volume) / depth_total if depth_total > 0 else None
    )
    return ExitEvaluationContext(
      timestamp=state.updated_at,
      current_price=float(state.current_price or 0.0),
      bid_price=bid_price,
      ask_price=ask_price,
      limit_up=float(state.up_stop_price or 0.0),
      limit_down=float(state.down_stop_price or 0.0),
      price_tick=float(state.price_tick or 0.01),
      cumulative_volume=float(state.volume),
      cumulative_amount=float(state.amount),
      depth_imbalance_5=depth_imbalance,
      market_data_age_seconds=age_seconds,
      volume_data_age_seconds=age_seconds,
      source="QMT_WHOLE_QUOTE",
    )


conditional_liquidation_monitor = ConditionalLiquidationMonitor()
