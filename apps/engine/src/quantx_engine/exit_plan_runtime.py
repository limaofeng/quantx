"""Single authoritative Engine runtime for every PAPER/LIVE ExitPlan."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

from quantx_contracts import ExecutionEnvironment, ExecutionOwnerType
from quantx_domain.trading.exit_plan import ExitEvaluationContext
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.repositories.auto_exit_plan_repository import (
  AutoExitPlanRepository,
)
from quantx_infrastructure.repositories.position_repository import PositionRepository
from quantx_infrastructure.services.auto_exit_plan_service import (
  AutoExitPlanService,
)
from quantx_infrastructure.services.exit_plan_execution_owner import (
  durable_exit_plan_source_binding,
  is_public_exit_plan_runtime_eligible,
)
from quantx_infrastructure.services.exit_plan_scope_lock import (
  lock_exit_plan_scope_for_plan,
)
from quantx_infrastructure.services.intraday_volume_scanner import (
  intraday_volume_scanner,
)

logger = logging.getLogger(__name__)


class ExitPlanRuntime:
  """Schedule plans by their own EXIT_PLAN identity, never by source runtime."""

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
    self.plan_evaluation_failures = 0
    self._market_gate_blocked = False

  @property
  def is_running(self) -> bool:
    return bool(self._task and not self._task.done())

  async def start(self) -> None:
    if self._task and not self._task.done():
      return
    # This is the startup barrier for legacy manual ownership and conditional
    # plans. PAPER/LIVE strategy plans are restored only from auto_exit_plans;
    # StrategyRun custom_state is never imported as a competing truth source.
    migrated = await AutoExitPlanService().migrate_legacy_plan_state()
    if any(migrated.values()):
      logger.info("统一退出计划历史状态迁移完成: %s", migrated)
    await self.scanner.start()
    self._stopping = asyncio.Event()
    self._task = asyncio.create_task(self._run(), name="ExitPlanRuntime")
    logger.info("公共 ExitPlanRuntime 已启动")

  async def stop(self) -> None:
    self._stopping.set()
    if self._task is None:
      return
    self._task.cancel()
    try:
      await self._task
    except asyncio.CancelledError:
      pass
    logger.info("公共 ExitPlanRuntime 已停止")

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
        plans = (
          [plan]
          if plan is not None
          and plan.enabled
          and is_public_exit_plan_runtime_eligible(plan)
          else []
        )
      else:
        plans = await repo.find_active(
          account_id=account_id,
          instrument_code=instrument_code,
        )
        plans = [plan for plan in plans if is_public_exit_plan_runtime_eligible(plan)]
    plans.sort(key=self._plan_priority_key)
    if not plans:
      return []
    if not self.scanner.is_running:
      await self.scanner.start()
    self.scanner.touch()
    market_session_open = await self.scanner.hub.is_trading_session()
    states = self._ready_states()
    results: list[dict] = []
    service = AutoExitPlanService()
    for record in plans:
      try:
        position, context = await self._evaluation_inputs(record, states=states)
        result = await service.evaluate_and_submit(
          plan_id=record.plan_id,
          context=context,
          position=position,
          market_session_open=market_session_open,
          market_ready=self._market_data_ready,
        )
        results.append(
          {
            "plan_id": record.plan_id,
            "submitted": bool(result and result.get("success")),
            "result": result,
          }
        )
      except asyncio.CancelledError:
        raise
      except Exception as exc:
        self.plan_evaluation_failures += 1
        logger.exception(
          "公共退出计划单票评估失败，继续后续计划: plan_id=%s",
          record.plan_id,
        )
        try:
          await service.record_runtime_evaluation_failure(
            plan_id=str(record.plan_id),
            error=exc,
          )
        except asyncio.CancelledError:
          raise
        except Exception:
          logger.exception(
            "公共退出计划失败审计落库失败: plan_id=%s",
            record.plan_id,
          )
        results.append(
          {
            "plan_id": record.plan_id,
            "submitted": False,
            "result": {
              "success": False,
              "error": "EXIT_PLAN_RUNTIME_EVALUATION_FAILED",
              "error_type": type(exc).__name__,
            },
          }
        )
    return results

  async def confirm_exit_intent(
    self,
    *,
    plan_id: str,
    intent_id: str,
    approval_audit: Optional[dict] = None,
  ) -> dict:
    async with AsyncSessionLocal() as db:
      record = await AutoExitPlanRepository(db).find_by_id(plan_id)
      if record is None:
        raise ValueError("退出计划不存在")
      if not is_public_exit_plan_runtime_eligible(record):
        raise ValueError("EXIT_PLAN_OWNER_CHANGED")
    if not self.scanner.is_running:
      await self.scanner.start()
    self.scanner.touch()
    market_session_open = await self.scanner.hub.is_trading_session()
    states = self._ready_states()
    position, context = await self._evaluation_inputs(record, states=states)
    return await AutoExitPlanService().confirm_exit_intent(
      plan_id=plan_id,
      intent_id=intent_id,
      context=context,
      position=position,
      market_session_open=market_session_open,
      market_ready=self._market_data_ready,
      approval_audit=approval_audit,
    )

  async def _evaluation_inputs(self, record, *, states):
    binding = durable_exit_plan_source_binding(record)
    if binding is None:
      raise ValueError("EXIT_PLAN_OWNER_CHANGED")
    owner, environment = binding
    now = time_utils.now()
    if owner.owner_type is ExecutionOwnerType.T_ASSISTANT_EXECUTION:
      if environment is not ExecutionEnvironment.PAPER:
        raise ValueError("T_ASSISTANT_EXIT_ENVIRONMENT_UNSUPPORTED")
      from quantx_infrastructure.services.paper_exit_execution import (
        read_paper_exit_market,
      )

      async with AsyncSessionLocal() as db:
        scope = await lock_exit_plan_scope_for_plan(db, record.plan_id)
        if (
          scope.target_plan is None
          or durable_exit_plan_source_binding(scope.target_plan) != binding
        ):
          raise ValueError("EXIT_PLAN_OWNER_CHANGED")
        market = await read_paper_exit_market(
          db,
          execution_id=owner.owner_id,
          instrument_code=record.instrument_code,
          now=time_utils.to_utc(now),
        )
        position = scope.position
      if not self._market_data_ready():
        return position, self.context_from_state(None, now=now)
      return position, self.context_from_paper_market(market, now=now)
    async with AsyncSessionLocal() as db:
      position = await PositionRepository(db).find_by_stock_code(
        record.instrument_code,
        account_id=record.account_id,
      )
    return position, self.context_from_state(
      states.get(record.instrument_code) if self._market_data_ready() else None,
      now=now,
    )

  @staticmethod
  def context_from_paper_market(market, *, now):
    timestamp = time_utils.to_shanghai(market.timestamp)
    age = (time_utils.to_shanghai(now) - timestamp).total_seconds()
    if age < 0:
      raise ValueError("PAPER_EXIT_QUOTE_FROM_FUTURE")
    bid_volume, ask_volume = sum(market.bid_vol), sum(market.ask_vol)
    total = bid_volume + ask_volume
    return ExitEvaluationContext(
      timestamp=time_utils.to_shanghai(now),
      current_price=market.price,
      bid_price=market.bid_price[0],
      ask_price=market.ask_price[0],
      limit_up=market.limit_up,
      limit_down=market.limit_down,
      price_tick=market.price_tick,
      cumulative_volume=market.volume,
      cumulative_amount=market.amount,
      depth_imbalance_5=(bid_volume - ask_volume) / total if total else None,
      market_data_age_seconds=age,
      volume_data_age_seconds=age,
      source="PAPER_ACCEPTED_QUOTE",
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
  def _plan_priority_key(record) -> tuple:
    state = dict(getattr(record, "plan_state", None) or {})
    template = dict(state.get("template") or {})
    rules = list(template.get("rules") or [])
    highest_priority = max(
      (
        int(rule.get("priority", 0) or 0)
        for rule in rules
        if isinstance(rule, dict) and bool(rule.get("enabled", True))
      ),
      default=0,
    )
    return (
      str(getattr(record, "account_id", "") or ""),
      str(getattr(record, "instrument_code", "") or ""),
      -highest_priority,
      getattr(record, "created_at", None) or datetime.min,
      str(getattr(record, "plan_id", "") or ""),
    )

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


exit_plan_runtime = ExitPlanRuntime()
