"""Engine-owned monitor for every persistent ExitPlanBook plan."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from quantx_domain.trading.exit_plan import ExitEvaluationContext
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.repositories.auto_exit_plan_repository import (
  AutoExitPlanRepository,
)
from quantx_infrastructure.repositories.position_repository import PositionRepository
from quantx_infrastructure.services.auto_exit_plan_service import AutoExitPlanService
from quantx_infrastructure.services.intraday_volume_scanner import (
  intraday_volume_scanner,
)

logger = logging.getLogger(__name__)


class ExitPlanMonitor:
  """Evaluate persisted plans independently from their originating feature."""

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

  @property
  def is_running(self) -> bool:
    return bool(self._task and not self._task.done())

  async def start(self) -> None:
    if self._task and not self._task.done():
      return
    try:
      migrated = await AutoExitPlanService().migrate_legacy_plan_state()
      if any(migrated.values()):
        logger.info("统一退出计划历史状态迁移完成: %s", migrated)
    except Exception as exc:
      logger.warning("统一退出计划历史状态迁移失败，将在下次启动重试: %s", exc)
    await self.scanner.start()
    self._stopping = asyncio.Event()
    self._task = asyncio.create_task(self._run(), name="ExitPlanMonitor")
    logger.info("统一退出计划监控器已启动")

  async def stop(self) -> None:
    self._stopping.set()
    if self._task is None:
      return
    self._task.cancel()
    try:
      await self._task
    except asyncio.CancelledError:
      pass
    logger.info("统一退出计划监控器已停止")

  async def _run(self) -> None:
    while not self._stopping.is_set():
      try:
        await self.evaluate_all_active_plans()
      except asyncio.CancelledError:
        raise
      except Exception as exc:
        logger.warning("统一退出计划扫描失败: %s", exc)
      try:
        await asyncio.wait_for(self._stopping.wait(), timeout=self.interval_seconds)
      except asyncio.TimeoutError:
        continue

  async def evaluate_all_active_plans(
    self,
    *,
    account_id: Optional[str] = None,
    instrument_code: Optional[str] = None,
    plan_id: Optional[str] = None,
  ) -> list[dict]:
    async with AsyncSessionLocal() as db:
      repo = AutoExitPlanRepository(db)
      if plan_id:
        plan = await repo.find_by_id(plan_id)
        plans = [plan] if plan is not None and plan.enabled else []
      else:
        plans = await repo.find_active(
          account_id=account_id,
          instrument_code=instrument_code,
        )
    if not plans:
      return []
    if not self.scanner.is_running:
      await self.scanner.start()
    self.scanner.touch()
    states = self._ready_states()
    results: list[dict] = []
    service = AutoExitPlanService()
    for record in plans:
      async with AsyncSessionLocal() as db:
        position = await PositionRepository(db).find_by_stock_code(
          record.instrument_code,
          account_id=record.account_id,
        )
      context = self.context_from_state(
        (
          states.get(record.instrument_code)
          if self._market_data_ready()
          else None
        ),
        now=time_utils.now(),
      )
      result = await service.evaluate_and_submit(
        plan_id=record.plan_id,
        context=context,
        position=position,
        market_ready=self._market_data_ready,
      )
      results.append(
        {
          "plan_id": record.plan_id,
          "submitted": bool(result and result.get("success")),
          "result": result,
        }
      )
    return results

  async def confirm_exit_intent(
    self,
    *,
    plan_id: str,
    intent_id: str,
  ) -> dict:
    async with AsyncSessionLocal() as db:
      record = await AutoExitPlanRepository(db).find_by_id(plan_id)
      if record is None:
        raise ValueError("退出计划不存在")
      position = await PositionRepository(db).find_by_stock_code(
        record.instrument_code,
        account_id=record.account_id,
      )
    if not self.scanner.is_running:
      await self.scanner.start()
    self.scanner.touch()
    states = self._ready_states()
    context = self.context_from_state(
      (
        states.get(record.instrument_code)
        if self._market_data_ready()
        else None
      ),
      now=time_utils.now(),
    )
    return await AutoExitPlanService().confirm_exit_intent(
      plan_id=plan_id,
      intent_id=intent_id,
      context=context,
      position=position,
      market_ready=self._market_data_ready,
    )

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
        "WholeQuoteHub 非 READY，退出计划使用不可用行情上下文: status=%s",
        status,
      )
      self._market_gate_blocked = True
    return {}

  @staticmethod
  def context_from_state(state, *, now: datetime) -> ExitEvaluationContext:
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


exit_plan_monitor = ExitPlanMonitor()
