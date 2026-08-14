"""Persistent orchestration for Engine-owned automatic exit plans."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from typing import Any, Mapping, Optional

from quantx_domain.trading.exit_plan import (
  EXIT_PLAN_BOOK_STATE_KEY,
  ExitDecision,
  ExitEvaluationContext,
  ExitExecutionPolicy,
  ExitPlan,
  ExitPlanBook,
  ExitPlanStatus,
  ExitPlanTemplate,
  ExitPriceReference,
  ExitRuleSpec,
  ExitRuleType,
  ExitSizingMode,
  ExitSizingPolicy,
  ExitT1Policy,
)
from sqlalchemy import select

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import PendingTradeOrder
from quantx_infrastructure.models.auto_exit_plan import (
  AutoExitPlanEvent,
  AutoExitPlanRecord,
)
from quantx_infrastructure.models.liquidation import (
  ConditionalLiquidationOrder,
  ConditionalLiquidationSellMode,
  ConditionalLiquidationStatus,
)
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.strategy_run import StrategyRun
from quantx_infrastructure.models.strategy_run_state import StrategyRunState
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.auto_exit_plan_repository import (
  AutoExitPlanRepository,
)
from quantx_infrastructure.repositories.position_repository import PositionRepository
from quantx_infrastructure.services.trade_intent_processor import TradeIntentProcessor

ADAPTIVE_RULE_ID_SUFFIX = "adaptive-volume-price"
ACTIVE_ORDER_STATUSES = {"QUEUED", "PENDING", "SUBMITTED", "PARTIAL_FILLED"}
MANUAL_PLAN_SOURCE = "MANUAL_POSITION"
MANUAL_LIQUIDATION_SOURCE = "MANUAL_LIQUIDATION"
AVAILABLE_NOW = "AVAILABLE_NOW"
UNTIL_SNAPSHOT_CLEARED = "UNTIL_SNAPSHOT_CLEARED"
UNALLOCATED_ONLY = "UNALLOCATED_ONLY"
REPLACE_CANCELLABLE = "REPLACE_CANCELLABLE"
TERMINAL_PLAN_STATUSES = {"COMPLETED", "CANCELLED"}

BALANCED_DYNAMIC_POLICY: dict[str, Any] = {
  "base_floor_pct": 0.5,
  "initial_gap_pct": 1.5,
  "gap_slope": 0.25,
  "max_gap_pct": 3.0,
  "weak_drawdown_pct": 0.6,
  "weak_return_15s_pct": 0.25,
  "stagnation_volume_velocity": 1.5,
  "stagnation_return_60s_pct": 0.1,
  "weak_depth_imbalance": -0.2,
  "new_high_bonus_seconds": 10.0,
  "strong_return_15s_pct": 0.25,
  "strong_volume_velocity": 1.2,
  "confirm_score": 3,
  "confirm_observations": 2,
  "immediate_drawdown_pct": 1.2,
  "immediate_return_15s_pct": 0.8,
  "immediate_volume_velocity": 2.0,
  "max_slippage_bps": 30.0,
}


def normalize_dynamic_policy(value: Optional[Mapping[str, Any]]) -> dict[str, Any]:
  policy = dict(BALANCED_DYNAMIC_POLICY)
  policy.update(dict(value or {}))
  return policy


class AutoExitPlanService:
  async def migrate_legacy_plan_state(self) -> dict[str, int]:
    """Idempotently import persisted runtime books and active legacy conditions."""

    strategy_candidates: list[tuple[str, dict[str, Any], str]] = []
    condition_candidates: list[tuple[ConditionalLiquidationOrder, Position, int]] = []
    linked_conditions = 0
    async with AsyncSessionLocal() as db:
      strategy_rows = (
        await db.execute(
          select(StrategyRunState, StrategyRun).join(
            StrategyRun, StrategyRun.id == StrategyRunState.run_id
          )
        )
      ).all()
      for state, run in strategy_rows:
        mode = getattr(run.mode, "value", run.mode)
        if str(mode or "").lower() == "backtest":
          continue
        custom_state = dict(state.custom_state or {})
        book_state = custom_state.get(EXIT_PLAN_BOOK_STATE_KEY)
        if isinstance(book_state, Mapping) and book_state.get("plans"):
          strategy_candidates.append((str(run.id), dict(book_state), str(mode)))

      condition_rows = (
        await db.execute(
          select(ConditionalLiquidationOrder, Position)
          .join(
            Position,
            (Position.account_id == ConditionalLiquidationOrder.account_id)
            & (Position.stock_code == ConditionalLiquidationOrder.stock_code),
          )
          .where(
            ConditionalLiquidationOrder.status.in_(
              (
                ConditionalLiquidationStatus.ACTIVE,
                ConditionalLiquidationStatus.FAILED,
              )
            )
          )
          .where(ConditionalLiquidationOrder.submitted_order_id.is_(None))
          .where(Position.volume > 0)
        )
      ).all()
      repo = AutoExitPlanRepository(db)
      for order, position in condition_rows:
        existing = await repo.find_by_source(MANUAL_PLAN_SOURCE, str(order.id))
        if existing is not None:
          if order.exit_plan_id != existing.plan_id:
            order.exit_plan_id = existing.plan_id
            linked_conditions += 1
          continue
        protected_volume = self._legacy_condition_volume(order, position)
        if protected_volume > 0:
          condition_candidates.append((order, position, protected_volume))
      if linked_conditions:
        await db.commit()

    strategy_plans = 0
    for run_id, book_state, execution_mode in strategy_candidates:
      strategy_plans += await self.sync_strategy_plan_book(
        strategy_run_id=run_id,
        book_state=book_state,
        execution_mode=execution_mode,
      )

    conditional_plans = 0
    for order, position, protected_volume in condition_candidates:
      try:
        record = await self.create_or_update_manual_plan(
          order=order,
          position=position,
          protected_volume=protected_volume,
        )
      except ValueError:
        # A concurrent command may have claimed the same capacity after the scan.
        continue
      async with AsyncSessionLocal() as db:
        stored_order = await db.scalar(
          select(ConditionalLiquidationOrder)
          .where(ConditionalLiquidationOrder.id == order.id)
          .with_for_update()
        )
        if stored_order is not None and stored_order.exit_plan_id != record.plan_id:
          stored_order.exit_plan_id = record.plan_id
          stored_order.last_error = None
          await db.commit()
      conditional_plans += 1

    return {
      "strategy_plans": strategy_plans,
      "conditional_plans": conditional_plans,
      "linked_conditions": linked_conditions,
    }

  async def sync_strategy_plan_book(
    self,
    *,
    strategy_run_id: str,
    book_state: Mapping[str, Any],
    execution_mode: str,
  ) -> int:
    """Idempotently hand paper/live strategy plans to the persistent monitor."""

    book = ExitPlanBook.from_dict(book_state)
    synced = 0
    async with AsyncSessionLocal() as db:
      repo = AutoExitPlanRepository(db)
      for plan in book.plans.values():
        template = plan.template
        if not template.account_id or plan.status in {
          ExitPlanStatus.CANCELLED,
          ExitPlanStatus.COMPLETED,
        }:
          continue
        record = await repo.find_by_id(plan.plan_id, for_update=True)
        if record is not None:
          if int(record.config_version or 0) >= int(template.config_version or 0):
            continue
          persistent_plan = ExitPlan.from_dict(dict(record.plan_state or {}))
          persistent_plan.apply_template(template)
          record.config_version = int(template.config_version)
          record.auto_exit_authorized = bool(template.auto_exit_authorized)
          self._sync_record(record, persistent_plan)
          event_type = "STRATEGY_PLAN_POLICY_UPDATED"
        else:
          record = AutoExitPlanRecord(
            plan_id=plan.plan_id,
            account_id=template.account_id,
            instrument_code=template.instrument_code,
            bucket=template.bucket,
            source_type=template.source_type,
            source_id=template.source_id or plan.plan_id,
            strategy_run_id=strategy_run_id,
            enabled=plan.status != ExitPlanStatus.PAUSED,
            status=plan.status.value,
            execution_mode=self._execution_mode(execution_mode),
            auto_exit_authorized=bool(template.auto_exit_authorized),
            config_version=int(template.config_version),
            protected_volume=int(plan.entry_filled_volume or 0),
            exited_volume=int(plan.exited_volume or 0),
            remaining_volume=int(plan.remaining_volume or 0),
            entry_avg_price=float(plan.entry_avg_price or 0.0),
            plan_state=plan.to_dict(),
          )
          self._sync_record(record, plan)
          db.add(record)
          event_type = "STRATEGY_PLAN_PERSISTED"
        await self._append_event(
          db,
          business_key=(
            f"strategy-plan-sync:{plan.plan_id}:{template.config_version}"
          ),
          plan_id=plan.plan_id,
          event_type=event_type,
          payload={
            "strategy_run_id": strategy_run_id,
            "source_type": template.source_type,
            "config_version": template.config_version,
          },
        )
        synced += 1
      await db.commit()
    return synced

  @staticmethod
  def _legacy_condition_volume(
    order: ConditionalLiquidationOrder,
    position: Position,
  ) -> int:
    available = max(0, int(position.can_use_volume or 0))
    if available <= 0:
      return 0
    sell_mode = str(
      order.sell_mode or ConditionalLiquidationSellMode.ALL_AVAILABLE
    ).upper()
    if sell_mode == ConditionalLiquidationSellMode.PERCENT_AVAILABLE:
      sizing = ExitSizingPolicy(
        mode=ExitSizingMode.PERCENT_REMAINING,
        value=float(order.sell_ratio_pct or 0.0),
        allow_odd_lot_full_exit=False,
      )
    elif sell_mode == ConditionalLiquidationSellMode.FIXED_VOLUME:
      sizing = ExitSizingPolicy(
        mode=ExitSizingMode.FIXED_VOLUME,
        value=int(order.sell_volume or 0),
        allow_odd_lot_full_exit=False,
      )
    else:
      sizing = ExitSizingPolicy(mode=ExitSizingMode.ALL_REMAINING)
    return sizing.calculate(available)

  async def create_manual_exit_plan(
    self,
    payload: Mapping[str, Any],
  ) -> AutoExitPlanRecord:
    """Create an operator-owned plan while atomically claiming holding capacity."""

    account_id = str(payload.get("account_id") or "").strip()
    instrument_code = str(payload.get("instrument_code") or "").strip().upper()
    if not account_id or not instrument_code:
      raise ValueError("人工计划必须指定账户和股票")
    async with AsyncSessionLocal() as db:
      position = await db.scalar(
        select(Position)
        .where(Position.account_id == account_id)
        .where(Position.stock_code == instrument_code)
        .with_for_update()
      )
      if position is None or int(position.volume or 0) <= 0:
        raise ValueError(f"未找到 {instrument_code} 的有效持仓")
      repo = AutoExitPlanRepository(db)
      reserving = await repo.find_reserving(
        account_id=account_id,
        instrument_code=instrument_code,
        for_update=True,
      )
      reserved = sum(max(0, int(item.remaining_volume or 0)) for item in reserving)
      unallocated = max(0, int(position.volume or 0) - reserved)
      requested = int(payload.get("protected_volume") or unallocated)
      if requested <= 0 or requested > unallocated:
        raise ValueError(
          f"可认领数量不足：未分配 {unallocated} 股，申请 {requested} 股"
        )
      plan_id = str(payload.get("plan_id") or f"manual-position:{uuid.uuid4()}")
      rules = self._rules_from_payload(plan_id, payload.get("rules"))
      template = self._template(
        plan_id=plan_id,
        source_type=MANUAL_PLAN_SOURCE,
        source_id=str(payload.get("source_id") or plan_id),
        account_id=account_id,
        instrument_code=instrument_code,
        bucket=str(payload.get("bucket") or "manual"),
        rules=rules,
        config_version=1,
        metadata={
          "created_manually": True,
          "position_volume_snapshot": int(position.volume or 0),
          "available_volume_snapshot": int(position.can_use_volume or 0),
          "remark": str(payload.get("remark") or ""),
        },
        auto_exit_authorized=bool(payload.get("auto_exit_authorized", False)),
      )
      plan = ExitPlanBook().register_entry_fill(
        template,
        volume=requested,
        price=float(position.avg_price or 0.0),
        trade_time=getattr(position, "created_at", None),
      )
      record = AutoExitPlanRecord(
        plan_id=plan_id,
        account_id=account_id,
        instrument_code=instrument_code,
        bucket=template.bucket,
        source_type=MANUAL_PLAN_SOURCE,
        source_id=template.source_id,
        enabled=bool(payload.get("enabled", True)),
        execution_mode=self._execution_mode(payload.get("execution_mode")),
        auto_exit_authorized=bool(payload.get("auto_exit_authorized", False)),
        config_version=1,
        protected_volume=requested,
        exited_volume=0,
        remaining_volume=requested,
        entry_avg_price=float(position.avg_price or 0.0),
        plan_state={},
      )
      if not record.enabled:
        plan.status = ExitPlanStatus.PAUSED
      self._sync_record(record, plan)
      db.add(record)
      await self._append_event(
        db,
        business_key=f"plan-created:{plan_id}:1",
        plan_id=plan_id,
        event_type="PLAN_CREATED",
        payload={"source_type": MANUAL_PLAN_SOURCE, "protected_volume": requested},
      )
      await db.commit()
      await db.refresh(record)
      return record

  async def update_manual_exit_plan(
    self,
    payload: Mapping[str, Any],
  ) -> AutoExitPlanRecord:
    plan_id = str(payload.get("plan_id") or "")
    expected_version = int(payload.get("config_version") or 0)
    async with AsyncSessionLocal() as db:
      repo = AutoExitPlanRepository(db)
      record = await repo.find_by_id(plan_id)
      if record is None:
        raise ValueError("退出计划不存在")
      requested_account_id = str(payload.get("account_id") or "").strip()
      if requested_account_id and requested_account_id != record.account_id:
        raise ValueError("退出计划不属于当前账户")
      if record.source_type != MANUAL_PLAN_SOURCE:
        raise ValueError("业务来源计划请返回原业务页面修改规则")

      position = await db.scalar(
        select(Position)
        .where(Position.account_id == record.account_id)
        .where(Position.stock_code == record.instrument_code)
        .with_for_update()
      )
      reserving = await repo.find_reserving(
        account_id=record.account_id,
        instrument_code=record.instrument_code,
        for_update=True,
      )
      record = next((item for item in reserving if item.plan_id == plan_id), record)
      if expected_version <= 0 or int(record.config_version) != expected_version:
        raise ValueError(
          f"CONFIG_VERSION_CONFLICT: current={record.config_version}"
        )
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      if plan.status == ExitPlanStatus.EXIT_PENDING or plan.pending_order_id:
        raise ValueError("已有卖出委托待成交，暂不能修改计划")
      if plan.status in {ExitPlanStatus.COMPLETED, ExitPlanStatus.CANCELLED}:
        raise ValueError("已完成或已取消的计划不能修改")

      protected_volume = int(
        payload.get("protected_volume", record.protected_volume)
        or record.protected_volume
        or 0
      )
      if protected_volume < int(plan.exited_volume or 0):
        raise ValueError(
          f"保护数量不能小于已卖数量 {int(plan.exited_volume or 0)} 股"
        )
      desired_remaining = protected_volume - int(plan.exited_volume or 0)
      other_reserved = sum(
        max(0, int(item.remaining_volume or 0))
        for item in reserving
        if item.plan_id != plan_id
      )
      holding_volume = int(position.volume or 0) if position is not None else 0
      available_to_plan = max(0, holding_volume - other_reserved)
      if desired_remaining <= 0 or desired_remaining > available_to_plan:
        raise ValueError(
          "可认领数量不足："
          f"当前计划最多可保护 {available_to_plan + int(plan.exited_volume or 0)} 股，"
          f"申请 {protected_volume} 股"
        )
      rules = self._rules_from_payload(plan_id, payload.get("rules"))
      next_version = int(record.config_version) + 1
      template = self._template(
        plan_id=plan_id,
        source_type=record.source_type,
        source_id=record.source_id,
        account_id=record.account_id,
        instrument_code=record.instrument_code,
        bucket=record.bucket,
        rules=rules,
        config_version=next_version,
        metadata={
          **dict(plan.template.metadata or {}),
          "remark": str(payload.get("remark") or ""),
        },
        auto_exit_authorized=bool(
          payload.get("auto_exit_authorized", record.auto_exit_authorized)
        ),
      )
      plan.apply_template(template)
      plan.entry_filled_volume = protected_volume
      record.config_version = next_version
      record.protected_volume = protected_volume
      record.remaining_volume = desired_remaining
      record.execution_mode = self._execution_mode(
        payload.get("execution_mode", record.execution_mode)
      )
      record.auto_exit_authorized = template.auto_exit_authorized
      self._sync_record(record, plan)
      await self._append_event(
        db,
        business_key=f"plan-updated:{plan_id}:{next_version}",
        plan_id=plan_id,
        event_type="PLAN_UPDATED",
        payload={
          "config_version": next_version,
          "protected_volume": protected_volume,
        },
      )
      await db.commit()
      await db.refresh(record)
      return record

  async def create_liquidation_group(
    self,
    payload: Mapping[str, Any],
  ) -> dict[str, Any]:
    """Create one already-triggered plan per selected holding."""

    account_id = str(payload.get("account_id") or "").strip()
    completion = str(payload.get("completion_strategy") or "").upper()
    conflict_strategy = str(payload.get("conflict_strategy") or "").upper()
    if completion not in {AVAILABLE_NOW, UNTIL_SNAPSHOT_CLEARED}:
      raise ValueError("必须显式选择清仓完成策略")
    if conflict_strategy not in {UNALLOCATED_ONLY, REPLACE_CANCELLABLE}:
      raise ValueError("必须显式选择计划冲突处理策略")
    if not bool(payload.get("confirm")):
      raise ValueError("必须确认卖出风险")
    selected = {
      str(item or "").strip().upper()
      for item in list(payload.get("instrument_codes") or [])
      if str(item or "").strip()
    }
    scope = str(payload.get("scope") or "SELECTED").upper()
    if scope == "SELECTED" and not selected:
      raise ValueError("请选择至少一只持仓")
    group_id = str(uuid.uuid4())
    results: list[dict[str, Any]] = []
    async with AsyncSessionLocal() as db:
      position_stmt = (
        select(Position)
        .where(Position.account_id == account_id)
        .where(Position.volume > 0)
        .order_by(Position.stock_code)
        .with_for_update()
      )
      if scope == "SELECTED":
        position_stmt = position_stmt.where(Position.stock_code.in_(selected))
      positions = list((await db.execute(position_stmt)).scalars().all())
      found = {item.stock_code for item in positions}
      for missing in sorted(selected - found):
        results.append(
          {"instrument_code": missing, "success": False, "error": "未找到持仓"}
        )
      repo = AutoExitPlanRepository(db)
      for position in positions:
        code = str(position.stock_code)
        reserving = await repo.find_reserving(
          account_id=account_id,
          instrument_code=code,
          for_update=True,
        )
        pending = [
          item
          for item in reserving
          if item.status == ExitPlanStatus.EXIT_PENDING.value
          or item.pending_client_order_id
        ]
        if pending:
          results.append(
            {
              "instrument_code": code,
              "success": False,
              "error": "存在待成交卖单，必须先等待回报或撤单",
              "conflict_plan_ids": [item.plan_id for item in pending],
            }
          )
          continue
        if conflict_strategy == REPLACE_CANCELLABLE:
          for existing in reserving:
            old_plan = ExitPlan.from_dict(dict(existing.plan_state or {}))
            old_plan.status = ExitPlanStatus.CANCELLED
            old_plan.error_message = f"REPLACED_BY_LIQUIDATION_GROUP:{group_id}"
            existing.enabled = False
            self._sync_record(existing, old_plan)
            await self._append_event(
              db,
              business_key=f"plan-replaced:{existing.plan_id}:{group_id}",
              plan_id=existing.plan_id,
              event_type="PLAN_CANCELLED",
              payload={"replacement_group_id": group_id},
            )
          reserving = []
        reserved = sum(max(0, int(item.remaining_volume or 0)) for item in reserving)
        snapshot_target = (
          int(position.can_use_volume or 0)
          if completion == AVAILABLE_NOW
          else int(position.volume or 0)
        )
        target = max(0, min(snapshot_target, int(position.volume or 0) - reserved))
        if target <= 0:
          results.append(
            {
              "instrument_code": code,
              "success": False,
              "error": "持仓数量已被其他退出计划保护",
              "conflict_plan_ids": [item.plan_id for item in reserving],
            }
          )
          continue
        plan_id = f"manual-liquidation:{group_id}:{code}"
        rule = ExitRuleSpec(
          rule_id=f"{plan_id}:manual-trigger",
          strategy=ExitRuleType.MANUAL_TRIGGER,
          priority=1000,
          sizing=ExitSizingPolicy(mode=ExitSizingMode.ALL_REMAINING),
          parameters={"reason": "MANUAL_LIQUIDATION"},
        )
        template = self._template(
          plan_id=plan_id,
          source_type=MANUAL_LIQUIDATION_SOURCE,
          source_id=plan_id,
          account_id=account_id,
          instrument_code=code,
          bucket="manual",
          rules=[rule],
          config_version=1,
          metadata={
            "group_id": group_id,
            "completion_strategy": completion,
            "conflict_strategy": conflict_strategy,
            "position_volume_snapshot": int(position.volume or 0),
            "available_volume_snapshot": int(position.can_use_volume or 0),
          },
          auto_exit_authorized=bool(payload.get("auto_exit_authorized", False)),
        )
        plan = ExitPlanBook().register_entry_fill(
          template,
          volume=target,
          price=float(position.avg_price or 0.0),
          trade_time=getattr(position, "created_at", None),
        )
        record = AutoExitPlanRecord(
          plan_id=plan_id,
          group_id=group_id,
          account_id=account_id,
          instrument_code=code,
          bucket="manual",
          source_type=MANUAL_LIQUIDATION_SOURCE,
          source_id=plan_id,
          enabled=True,
          execution_mode=self._execution_mode(payload.get("execution_mode")),
          auto_exit_authorized=bool(payload.get("auto_exit_authorized", False)),
          config_version=1,
          completion_strategy=completion,
          protected_volume=target,
          exited_volume=0,
          remaining_volume=target,
          entry_avg_price=float(position.avg_price or 0.0),
          plan_state={},
        )
        self._sync_record(record, plan)
        db.add(record)
        await self._append_event(
          db,
          business_key=f"liquidation-plan-created:{plan_id}",
          plan_id=plan_id,
          event_type="LIQUIDATION_PLAN_CREATED",
          payload={
            "group_id": group_id,
            "completion_strategy": completion,
            "protected_volume": target,
            "conflict_plan_ids": [item.plan_id for item in reserving],
          },
        )
        results.append(
          {
            "instrument_code": code,
            "success": True,
            "plan_id": plan_id,
            "protected_volume": target,
            "conflict_plan_ids": [item.plan_id for item in reserving],
          }
        )
      await db.commit()
    return {
      "group_id": group_id,
      "success": bool(results) and all(item.get("success") for item in results),
      "items": results,
    }

  async def create_or_update_manual_plan(
    self,
    *,
    order: ConditionalLiquidationOrder,
    position: Position,
    protected_volume: int,
  ) -> AutoExitPlanRecord:
    volume = max(0, int(protected_volume or 0))
    entry_price = float(getattr(position, "avg_price", 0.0) or 0.0)
    if volume <= 0 or entry_price <= 0:
      raise ValueError("动态止盈需要有效的固定保护数量和持仓成本")
    policy = normalize_dynamic_policy(order.dynamic_policy)
    plan_id = str(order.exit_plan_id or f"manual-position:{order.id}")
    async with AsyncSessionLocal() as db:
      repo = AutoExitPlanRepository(db)
      await db.scalar(
        select(Position)
        .where(Position.account_id == order.account_id)
        .where(Position.stock_code == order.stock_code)
        .with_for_update()
      )
      record = await repo.find_by_source("MANUAL_POSITION", str(order.id))
      reserving = await repo.find_reserving(
        account_id=order.account_id,
        instrument_code=order.stock_code,
        for_update=True,
      )
      others = [item for item in reserving if item.plan_id != plan_id]
      if any(
        item.status == ExitPlanStatus.EXIT_PENDING.value
        or item.pending_client_order_id
        for item in others
      ):
        raise ValueError("该持仓存在待成交卖单，不能重复认领数量")
      other_reserved = sum(
        max(0, int(item.remaining_volume or 0)) for item in others
      )
      unallocated = max(0, int(getattr(position, "volume", 0) or 0) - other_reserved)
      if volume > unallocated:
        raise ValueError(
          f"可认领数量不足：未分配 {unallocated} 股，申请 {volume} 股"
        )
      config_version = int(record.config_version or 0) + 1 if record else 1
      template = self._manual_template(
        order,
        plan_id=plan_id,
        config_version=config_version,
        policy=policy,
      )
      if record:
        plan = ExitPlan.from_dict(dict(record.plan_state or {}))
        if plan.status == ExitPlanStatus.EXIT_PENDING:
          raise ValueError("已有动态止盈委托待成交，不能修改计划")
        if plan.exited_volume > 0:
          raise ValueError("已部分成交的动态止盈计划不能修改，请新建计划")
        plan.apply_template(template)
        plan.entry_filled_volume = volume
        plan.entry_avg_price = entry_price
        plan.status = ExitPlanStatus.ACTIVE if order.enabled else ExitPlanStatus.PAUSED
      else:
        book = ExitPlanBook()
        plan = book.register_entry_fill(
          template,
          volume=volume,
          price=entry_price,
          trade_time=getattr(position, "created_at", None),
        )
        if not order.enabled:
          plan.status = ExitPlanStatus.PAUSED
        record = AutoExitPlanRecord(
          plan_id=plan_id,
          account_id=order.account_id,
          instrument_code=order.stock_code,
          bucket="manual",
          source_type="MANUAL_POSITION",
          source_id=str(order.id),
          protected_volume=volume,
          exited_volume=0,
          remaining_volume=volume,
          entry_avg_price=entry_price,
          plan_state={},
        )
      record.plan_id = plan_id
      record.account_id = order.account_id
      record.instrument_code = order.stock_code
      record.enabled = bool(order.enabled)
      record.execution_mode = str(order.execution_mode or "paper").lower()
      record.auto_exit_authorized = bool(order.auto_exit_authorized)
      record.config_version = config_version
      record.protected_volume = volume
      record.entry_avg_price = entry_price
      self._sync_record(record, plan)
      db.add(record)
      await db.commit()
      await db.refresh(record)
      return record

  async def set_enabled(
    self,
    plan_id: str,
    enabled: bool,
    *,
    account_id: Optional[str] = None,
    config_version: Optional[int] = None,
  ) -> Optional[AutoExitPlanRecord]:
    if not plan_id:
      return
    async with AsyncSessionLocal() as db:
      record = await AutoExitPlanRepository(db).find_by_id(plan_id, for_update=True)
      if record is None:
        return None
      if account_id and record.account_id != account_id:
        raise ValueError("退出计划不属于当前账户")
      if config_version is not None and int(record.config_version) != int(config_version):
        raise ValueError(
          f"CONFIG_VERSION_CONFLICT: current={record.config_version}"
        )
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      if plan.status in {ExitPlanStatus.COMPLETED, ExitPlanStatus.CANCELLED}:
        raise ValueError("终态退出计划不能启停")
      if enabled and plan.remaining_volume > 0:
        plan.status = ExitPlanStatus.ACTIVE
      elif not enabled:
        if plan.status == ExitPlanStatus.EXIT_PENDING or plan.pending_order_id:
          raise ValueError("已有卖出委托待成交，暂不能暂停")
        plan.status = ExitPlanStatus.PAUSED
      record.enabled = bool(enabled)
      record.config_version = int(record.config_version) + 1
      plan.template = ExitPlanTemplate.from_dict(
        {**plan.template.to_dict(), "config_version": record.config_version}
      )
      self._sync_record(record, plan)
      await self._append_event(
        db,
        business_key=(
          f"plan-enabled:{plan_id}:{record.config_version}:{int(bool(enabled))}"
        ),
        plan_id=plan_id,
        event_type="PLAN_RESUMED" if enabled else "PLAN_PAUSED",
        payload={"config_version": record.config_version},
      )
      await db.commit()
      await db.refresh(record)
      return record

  async def cancel(
    self,
    plan_id: str,
    reason: str = "USER_CANCELLED",
    *,
    account_id: Optional[str] = None,
    config_version: Optional[int] = None,
  ) -> Optional[AutoExitPlanRecord]:
    if not plan_id:
      return
    async with AsyncSessionLocal() as db:
      record = await AutoExitPlanRepository(db).find_by_id(plan_id, for_update=True)
      if record is None:
        return None
      if account_id and record.account_id != account_id:
        raise ValueError("退出计划不属于当前账户")
      if config_version is not None and int(record.config_version) != int(config_version):
        raise ValueError(
          f"CONFIG_VERSION_CONFLICT: current={record.config_version}"
        )
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      if plan.status == ExitPlanStatus.EXIT_PENDING or plan.pending_order_id:
        raise ValueError("存在待成交卖单，必须先等待回报或撤单")
      plan.status = ExitPlanStatus.CANCELLED
      plan.error_message = reason
      record.enabled = False
      record.config_version = int(record.config_version) + 1
      self._sync_record(record, plan)
      await self._append_event(
        db,
        business_key=f"plan-cancelled:{plan_id}:{record.config_version}",
        plan_id=plan_id,
        event_type="PLAN_CANCELLED",
        payload={"reason": reason, "config_version": record.config_version},
      )
      await db.commit()
      await db.refresh(record)
      return record

  async def evaluate_and_submit(
    self,
    *,
    plan_id: str,
    context: ExitEvaluationContext,
    position: Optional[Position],
  ) -> Optional[dict[str, Any]]:
    async with AsyncSessionLocal() as db:
      record = await AutoExitPlanRepository(db).find_by_id(plan_id, for_update=True)
      if record is None or not record.enabled:
        return None
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      market_age = float(context.market_data_age_seconds or 0.0)
      if market_age > 3.0:
        record.data_quality = "MARKET_DATA_STALE"
        record.last_error = "market_data_stale"
        self._sync_record(record, plan, evaluated_at=context.timestamp)
        await self._sync_source_order(
          db,
          record,
          plan,
          checked_at=context.timestamp,
        )
        await db.commit()
        return None
      record.data_quality = "GOOD"
      if plan.pending_intent_id and not plan.pending_order_id:
        recovered = await self._recover_pending_submission(db, record, plan)
        self._sync_record(record, plan, evaluated_at=context.timestamp)
        await self._sync_source_order(
          db,
          record,
          plan,
          checked_at=context.timestamp,
        )
        await db.commit()
        if recovered or plan.pending_intent_id:
          return None
      decision = ExitPlanBook([plan]).evaluator.evaluate(plan, context)
      self._sync_record(record, plan, evaluated_at=context.timestamp)
      await self._sync_source_order(db, record, plan, checked_at=context.timestamp)
      await db.commit()
    if decision is None:
      return None
    return await self._submit_decision(
      plan_id=plan_id,
      decision=decision,
      context=context,
      position=position,
    )

  async def apply_order_event_for_report(
    self,
    *,
    client_order_id: str = "",
    broker_order_id: str = "",
    status: str,
    source_sequence: int = 0,
  ) -> None:
    async with AsyncSessionLocal() as db:
      pending = await self._pending_order(
        db,
        client_order_id=client_order_id,
        broker_order_id=broker_order_id,
      )
      plan_id = str((pending.request_metadata or {}).get("exit_plan_id") or "") if pending else ""
      if not plan_id:
        return
      record = await AutoExitPlanRepository(db).find_by_id(plan_id, for_update=True)
      if record is None:
        return
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      ExitPlanBook([plan]).apply_order_event(
        plan_id=plan_id,
        intent_id=str((pending.request_metadata or {}).get("intent_id") or ""),
        status=status,
        order_id=broker_order_id or client_order_id,
        timestamp_ms=int(time_utils.now().timestamp() * 1000),
      )
      self._sync_record(record, plan)
      event_key = (
        f"order:{plan_id}:{client_order_id or broker_order_id}:"
        f"{source_sequence}:{str(status).upper()}"
      )
      await self._append_event(
        db,
        business_key=event_key,
        plan_id=plan_id,
        event_type="ORDER_STATE",
        payload={"status": status, "broker_order_id": broker_order_id},
      )
      await self._sync_source_order(db, record, plan)
      await db.commit()

  async def apply_execution_for_report(
    self,
    *,
    execution_id: str,
    client_order_id: str = "",
    broker_order_id: str = "",
    volume: int,
    price: float,
  ) -> None:
    if not execution_id:
      return
    async with AsyncSessionLocal() as db:
      pending = await self._pending_order(
        db,
        client_order_id=client_order_id,
        broker_order_id=broker_order_id,
      )
      plan_id = str((pending.request_metadata or {}).get("exit_plan_id") or "") if pending else ""
      if not plan_id:
        return
      business_key = f"execution:{plan_id}:{execution_id}"
      existing = (
        await db.execute(
          select(AutoExitPlanEvent).where(
            AutoExitPlanEvent.business_key == business_key
          )
        )
      ).scalar_one_or_none()
      if existing is not None:
        return
      record = await AutoExitPlanRepository(db).find_by_id(plan_id, for_update=True)
      if record is None:
        return
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      ExitPlanBook([plan]).apply_exit_fill(
        plan_id=plan_id,
        volume=volume,
        price=price,
        rule_id=str((pending.request_metadata or {}).get("exit_rule_id") or ""),
      )
      self._sync_record(record, plan)
      await self._append_event(
        db,
        business_key=business_key,
        plan_id=plan_id,
        event_type="EXECUTION_FILL",
        payload={
          "execution_id": execution_id,
          "volume": int(volume),
          "price": float(price),
        },
      )
      await self._sync_source_order(db, record, plan)
      await db.commit()

  async def confirm_exit_intent(
    self,
    *,
    plan_id: str,
    intent_id: str,
    context: ExitEvaluationContext,
    position: Optional[Position],
  ) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
      record = await AutoExitPlanRepository(db).find_by_id(plan_id, for_update=True)
      intent = await db.get(TradeIntentRecord, intent_id)
      if record is None or intent is None:
        raise ValueError("退出计划或卖出意图不存在")
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      if plan.pending_intent_id != intent_id:
        raise ValueError("卖出意图已变化，请刷新后重试")
      limit_price = self._protected_sell_price(context, record)
    result = await TradeIntentProcessor().process_approved_exit_intent(
      plan=record,
      record=intent,
      context=context,
      position=position,
      limit_price=limit_price,
    )
    if not result.get("success"):
      await self._release_failed_submission(
        plan_id,
        intent_id,
        str(result.get("error") or "exit_intent_rejected"),
      )
      return result
    client_order_id = str(
      result.get("client_order_id") or result.get("order_id") or ""
    )
    async with AsyncSessionLocal() as db:
      stored = await AutoExitPlanRepository(db).find_by_id(plan_id, for_update=True)
      if stored is None:
        return result
      stored_plan = ExitPlan.from_dict(dict(stored.plan_state or {}))
      ExitPlanBook([stored_plan]).apply_order_event(
        plan_id=plan_id,
        intent_id=intent_id,
        status="PENDING",
        order_id=client_order_id,
        timestamp_ms=context.timestamp_ms,
      )
      stored.pending_client_order_id = client_order_id
      stored.last_error = None
      self._sync_record(stored, stored_plan, evaluated_at=context.timestamp)
      await self._append_event(
        db,
        business_key=f"intent-confirmed:{plan_id}:{intent_id}",
        plan_id=plan_id,
        event_type="EXIT_INTENT_CONFIRMED",
        payload={
          "intent_id": intent_id,
          "client_order_id": client_order_id,
          "requested_volume": result.get("volume"),
        },
      )
      await db.commit()
    return result

  async def reject_exit_intent(
    self,
    *,
    plan_id: str,
    intent_id: str,
    reason: str = "USER_REJECTED",
  ) -> None:
    async with AsyncSessionLocal() as db:
      record = await AutoExitPlanRepository(db).find_by_id(plan_id, for_update=True)
      intent = await db.get(TradeIntentRecord, intent_id)
      if record is None or intent is None:
        raise ValueError("退出计划或卖出意图不存在")
      if intent.owner_type != "EXIT_PLAN" or intent.owner_id != plan_id:
        raise ValueError("卖出意图不属于该退出计划")
      if intent.status != "AWAITING_APPROVAL":
        raise ValueError("卖出意图已处理或不再等待确认")
      intent.status = "REJECTED"
      intent.notes = reason
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      ExitPlanBook([plan]).apply_order_event(
        plan_id=plan_id,
        intent_id=intent_id,
        status="REJECTED",
      )
      record.last_error = reason
      self._sync_record(record, plan)
      await self._append_event(
        db,
        business_key=f"intent-rejected:{plan_id}:{intent_id}",
        plan_id=plan_id,
        event_type="EXIT_INTENT_REJECTED",
        payload={"intent_id": intent_id, "reason": reason},
      )
      await db.commit()

  async def _submit_decision(
    self,
    *,
    plan_id: str,
    decision: ExitDecision,
    context: ExitEvaluationContext,
    position: Optional[Position],
  ) -> Optional[dict[str, Any]]:
    available = max(0, int(getattr(position, "can_use_volume", 0) or 0))
    total_position = max(0, int(getattr(position, "volume", 0) or 0))
    requested = min(int(decision.volume), available)
    if requested <= 0:
      await self._record_error(plan_id, "no_legal_sell_volume")
      return None
    allow_odd_lot = bool(requested >= total_position > 0)
    requested = ExitSizingPolicy(
      mode=ExitSizingMode.FIXED_VOLUME,
      value=requested,
      allow_odd_lot_full_exit=allow_odd_lot,
    ).calculate(available)
    if requested <= 0:
      await self._record_error(plan_id, "no_legal_sell_volume")
      return None

    async with AsyncSessionLocal() as db:
      record = await AutoExitPlanRepository(db).find_by_id(plan_id, for_update=True)
      if record is None:
        return None
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      intent_id = str(uuid.uuid4())
      decision = ExitDecision(
        plan_id=decision.plan_id,
        rule_id=decision.rule_id,
        rule_type=decision.rule_type,
        reason=decision.reason,
        volume=requested,
        priority=decision.priority,
        metrics=dict(decision.metrics or {}),
      )
      ExitPlanBook([plan]).mark_intent(decision, intent_id)
      plan.rule_state.setdefault("__runtime__", {})["pending_marked_at"] = (
        context.timestamp.isoformat()
      )
      self._sync_record(record, plan)
      await db.commit()

    price = self._protected_sell_price(context, record)
    try:
      result = await TradeIntentProcessor().process_exit_decision(
        plan=record,
        decision=decision,
        intent_id=intent_id,
        context=context,
        position=position,
        limit_price=price,
      )
    except Exception as exc:
      await self._release_failed_submission(plan_id, intent_id, str(exc))
      return None

    if result.get("awaiting_approval"):
      async with AsyncSessionLocal() as db:
        stored = await AutoExitPlanRepository(db).find_by_id(
          plan_id, for_update=True
        )
        if stored is not None:
          stored.last_error = "exit_intent_awaiting_approval"
          await self._append_event(
            db,
            business_key=f"intent-awaiting-approval:{plan_id}:{intent_id}",
            plan_id=plan_id,
            event_type="EXIT_INTENT_AWAITING_APPROVAL",
            payload={"intent_id": intent_id, "requested_volume": requested},
          )
          await db.commit()
      return result

    if not result.get("success"):
      await self._release_failed_submission(
        plan_id,
        intent_id,
        str(result.get("error") or "exit_intent_rejected"),
      )
      return result

    client_order_id = str(
      result.get("client_order_id") or result.get("order_id") or ""
    )
    async with AsyncSessionLocal() as db:
      stored = await AutoExitPlanRepository(db).find_by_id(plan_id, for_update=True)
      if stored is None:
        return result
      plan = ExitPlan.from_dict(dict(stored.plan_state or {}))
      ExitPlanBook([plan]).apply_order_event(
        plan_id=plan_id,
        intent_id=intent_id,
        status="PENDING",
        order_id=client_order_id,
        timestamp_ms=context.timestamp_ms,
      )
      stored.pending_client_order_id = client_order_id
      stored.last_error = None
      self._sync_record(stored, plan, evaluated_at=context.timestamp)
      source_order = await db.get(ConditionalLiquidationOrder, stored.source_id)
      if source_order is not None:
        source_order.enabled = False
        source_order.status = ConditionalLiquidationStatus.SUBMITTED
        source_order.triggered_at = context.timestamp
        source_order.triggered_price = context.bid_price or context.current_price
        source_order.triggered_profit_pct = plan.last_net_profit_pct
        source_order.submitted_order_id = client_order_id
        source_order.submitted_volume = requested
        source_order.last_error = None
      await self._append_event(
        db,
        business_key=f"intent:{plan_id}:{intent_id}",
        plan_id=plan_id,
        event_type="EXIT_INTENT_QUEUED",
        payload={
          "intent_id": intent_id,
          "client_order_id": client_order_id,
          "requested_volume": requested,
          "limit_price": price,
        },
      )
      await db.commit()
    return result

  @staticmethod
  async def _recover_pending_submission(db, record, plan: ExitPlan) -> bool:
    pending = (
      await db.execute(
        select(PendingTradeOrder)
        .where(PendingTradeOrder.account_id == record.account_id)
        .where(PendingTradeOrder.intent_id == plan.pending_intent_id)
        .limit(1)
      )
    ).scalar_one_or_none()
    if pending is not None:
      ExitPlanBook([plan]).apply_order_event(
        plan_id=plan.plan_id,
        intent_id=plan.pending_intent_id,
        status=pending.status,
        order_id=pending.client_order_id,
      )
      record.last_error = None
      return True

    intent = (
      await db.get(TradeIntentRecord, plan.pending_intent_id)
      if hasattr(db, "get")
      else None
    )
    if intent is not None and intent.status == "AWAITING_APPROVAL":
      record.last_error = "exit_intent_awaiting_approval"
      return True

    runtime_state = dict(plan.rule_state.get("__runtime__") or {})
    marked_at = _optional_datetime(runtime_state.get("pending_marked_at"))
    if marked_at is None or (time_utils.now() - marked_at).total_seconds() >= 10:
      ExitPlanBook([plan]).apply_order_event(
        plan_id=plan.plan_id,
        intent_id=plan.pending_intent_id,
        status="REJECTED",
      )
      record.last_error = "orphaned_exit_intent_released"
    return False

  async def _release_failed_submission(
    self, plan_id: str, intent_id: str, error: str
  ) -> None:
    async with AsyncSessionLocal() as db:
      record = await AutoExitPlanRepository(db).find_by_id(plan_id, for_update=True)
      if record is None:
        return
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      ExitPlanBook([plan]).apply_order_event(
        plan_id=plan_id,
        intent_id=intent_id,
        status="REJECTED",
      )
      record.last_error = error[:2000]
      self._sync_record(record, plan)
      await self._sync_source_order(db, record, plan)
      await db.commit()

  async def _record_error(self, plan_id: str, error: str) -> None:
    async with AsyncSessionLocal() as db:
      record = await AutoExitPlanRepository(db).find_by_id(plan_id, for_update=True)
      if record is not None:
        plan = ExitPlan.from_dict(dict(record.plan_state or {}))
        if record.completion_strategy == UNTIL_SNAPSHOT_CLEARED:
          record.last_error = "waiting_for_t1_sellable_volume"
        elif record.completion_strategy == AVAILABLE_NOW:
          plan.status = ExitPlanStatus.ERROR
          record.enabled = False
          record.last_error = error
          self._sync_record(record, plan)
        else:
          record.last_error = error
        await db.commit()

  @staticmethod
  def _execution_mode(value: Any) -> str:
    mode = str(value or "paper").strip().lower()
    if mode not in {"paper", "live"}:
      raise ValueError("执行模式只支持 paper 或 live")
    return mode

  @staticmethod
  def _rules_from_payload(
    plan_id: str,
    raw_rules: Any,
  ) -> list[ExitRuleSpec]:
    rules: list[ExitRuleSpec] = []
    for index, value in enumerate(list(raw_rules or [])):
      raw = dict(value or {})
      if "ruleId" in raw and "rule_id" not in raw:
        raw["rule_id"] = raw.pop("ruleId")
      if "type" in raw and "strategy" not in raw:
        raw["strategy"] = raw.pop("type")
      sizing = dict(raw.get("sizing") or {})
      if "lotSize" in sizing and "lot_size" not in sizing:
        sizing["lot_size"] = sizing.pop("lotSize")
      if "allowOddLotFullExit" in sizing and "allow_odd_lot_full_exit" not in sizing:
        sizing["allow_odd_lot_full_exit"] = sizing.pop("allowOddLotFullExit")
      raw["sizing"] = sizing
      raw.setdefault("rule_id", f"{plan_id}:rule:{index + 1}")
      strategy = str(raw.get("strategy") or "").upper()
      try:
        ExitRuleType(strategy)
      except ValueError as exc:
        raise ValueError(f"不支持的退出规则: {strategy}") from exc
      rules.append(ExitRuleSpec.from_dict(raw))
    if not rules:
      raise ValueError("退出计划至少需要一条规则")
    if not any(rule.enabled for rule in rules):
      raise ValueError("退出计划至少需要一条启用的规则")
    return rules

  @staticmethod
  def _template(
    *,
    plan_id: str,
    source_type: str,
    source_id: str,
    account_id: str,
    instrument_code: str,
    bucket: str,
    rules: list[ExitRuleSpec],
    config_version: int,
    metadata: Mapping[str, Any],
    auto_exit_authorized: bool,
  ) -> ExitPlanTemplate:
    return ExitPlanTemplate(
      plan_id=plan_id,
      source_type=source_type,
      source_id=source_id,
      account_id=account_id,
      instrument_code=instrument_code,
      bucket=bucket,
      rules=rules,
      config_version=config_version,
      t1_policy=ExitT1Policy.WAIT_UNTIL_SELLABLE,
      execution=ExitExecutionPolicy(
        price_reference=ExitPriceReference.BID,
        price_type="LIMIT",
        protected_limit=True,
        max_slippage_bps=30.0,
        urgency="PROTECTIVE_EXIT",
        execution_mode="AUTO",
      ),
      metadata=dict(metadata or {}),
      auto_exit_authorized=auto_exit_authorized,
    )

  @staticmethod
  def _manual_template(
    order: ConditionalLiquidationOrder,
    *,
    plan_id: str,
    config_version: int,
    policy: Mapping[str, Any],
  ) -> ExitPlanTemplate:
    rules: list[ExitRuleSpec] = []
    if str(getattr(order, "strategy", "") or "").upper() == (
      ExitRuleType.ADAPTIVE_VOLUME_PRICE_TRAILING.value
    ):
      parameters = dict(policy)
      if order.target_profit_pct is not None:
        parameters["arm_target_profit_pct"] = float(order.target_profit_pct)
      if order.target_price is not None:
        parameters["arm_target_price"] = float(order.target_price)
      rules.append(
        ExitRuleSpec(
          rule_id=f"{plan_id}:{ADAPTIVE_RULE_ID_SUFFIX}",
          strategy=ExitRuleType.ADAPTIVE_VOLUME_PRICE_TRAILING,
          priority=750,
          sizing=ExitSizingPolicy(mode=ExitSizingMode.ALL_REMAINING),
          parameters=parameters,
        )
      )
    else:
      if order.target_price is not None:
        rules.append(
          ExitRuleSpec(
            rule_id=f"{plan_id}:target-price",
            strategy=ExitRuleType.TARGET_PRICE,
            priority=600,
            sizing=ExitSizingPolicy(mode=ExitSizingMode.ALL_REMAINING),
            parameters={"target_price": float(order.target_price)},
          )
        )
      if order.target_profit_pct is not None:
        rules.append(
          ExitRuleSpec(
            rule_id=f"{plan_id}:gross-profit",
            strategy=ExitRuleType.GROSS_TAKE_PROFIT,
            priority=590,
            sizing=ExitSizingPolicy(mode=ExitSizingMode.ALL_REMAINING),
            parameters={
              "target_profit_pct": float(order.target_profit_pct)
            },
          )
        )
    return ExitPlanTemplate(
      plan_id=plan_id,
      source_type="MANUAL_POSITION",
      source_id=str(order.id),
      account_id=order.account_id,
      instrument_code=order.stock_code,
      bucket="manual",
      rules=rules,
      config_version=config_version,
      t1_policy=ExitT1Policy.WAIT_UNTIL_SELLABLE,
      execution=ExitExecutionPolicy(
        price_reference=ExitPriceReference.BID,
        price_type="LIMIT",
        protected_limit=True,
        max_slippage_bps=float(policy.get("max_slippage_bps", 30.0) or 30.0),
        urgency="PROTECTIVE_EXIT",
        execution_mode="AUTO",
      ),
      metadata={"conditional_order_id": str(order.id), "policy": dict(policy)},
      auto_exit_authorized=bool(order.auto_exit_authorized),
    )

  @staticmethod
  def _sync_record(
    record: AutoExitPlanRecord,
    plan: ExitPlan,
    *,
    evaluated_at: Optional[datetime] = None,
  ) -> None:
    adaptive_state = next(
      (
        dict(value or {})
        for key, value in plan.rule_state.items()
        if str(key).endswith(ADAPTIVE_RULE_ID_SUFFIX)
      ),
      {},
    )
    record.plan_state = plan.to_dict()
    record.status = plan.status.value
    record.exited_volume = int(plan.exited_volume)
    record.remaining_volume = int(plan.remaining_volume)
    record.peak_price = float(plan.peak_price or 0.0)
    record.trailing_floor_pct = plan.trailing_floor_pct
    record.phase = str(adaptive_state.get("phase", record.phase or "WAITING_ARM"))
    record.data_quality = str(
      adaptive_state.get("data_quality", record.data_quality or "PRICE_UNAVAILABLE")
    )
    record.last_decision = str(adaptive_state.get("last_decision", "") or "") or None
    record.peak_drawdown_pct = float(
      adaptive_state.get("peak_drawdown_pct", 0.0) or 0.0
    )
    record.volume_velocity = _optional_float(adaptive_state.get("volume_velocity"))
    record.weak_score = int(adaptive_state.get("weak_score", 0) or 0)
    record.pending_client_order_id = plan.pending_order_id or None
    if evaluated_at is not None:
      record.last_evaluated_at = evaluated_at.replace(tzinfo=None)

  @staticmethod
  async def _sync_source_order(
    db,
    record: AutoExitPlanRecord,
    plan: ExitPlan,
    *,
    checked_at: Optional[datetime] = None,
  ) -> None:
    if record.source_type != "MANUAL_POSITION":
      return
    order = await db.get(ConditionalLiquidationOrder, record.source_id)
    if order is None:
      return
    if checked_at is not None:
      order.last_checked_at = checked_at.replace(tzinfo=None)
    order.last_error = record.last_error
    if plan.status == ExitPlanStatus.COMPLETED:
      order.enabled = False
      order.status = ConditionalLiquidationStatus.COMPLETED
    elif plan.status == ExitPlanStatus.PARTIALLY_EXITED:
      order.enabled = True
      order.status = ConditionalLiquidationStatus.PARTIALLY_EXITED
      order.submitted_order_id = None
    elif plan.status in {ExitPlanStatus.ACTIVE, ExitPlanStatus.PAUSED}:
      order.enabled = plan.status == ExitPlanStatus.ACTIVE
      order.status = ConditionalLiquidationStatus.ACTIVE
      order.submitted_order_id = None
    elif plan.status == ExitPlanStatus.CANCELLED:
      order.enabled = False
      order.status = ConditionalLiquidationStatus.CANCELLED

  @staticmethod
  async def _pending_order(
    db,
    *,
    client_order_id: str,
    broker_order_id: str,
  ) -> Optional[PendingTradeOrder]:
    if client_order_id:
      pending = await db.get(PendingTradeOrder, client_order_id)
      if pending is not None:
        return pending
    if broker_order_id:
      return (
        await db.execute(
          select(PendingTradeOrder).where(
            PendingTradeOrder.broker_order_id == broker_order_id
          )
        )
      ).scalar_one_or_none()
    return None

  @staticmethod
  async def _append_event(
    db,
    *,
    business_key: str,
    plan_id: str,
    event_type: str,
    payload: Mapping[str, Any],
  ) -> None:
    existing = (
      await db.execute(
        select(AutoExitPlanEvent).where(
          AutoExitPlanEvent.business_key == business_key
        )
      )
    ).scalar_one_or_none()
    if existing is None:
      db.add(
        AutoExitPlanEvent(
          event_id=str(uuid.uuid4()),
          business_key=business_key,
          plan_id=plan_id,
          event_type=event_type,
          payload=dict(payload),
          created_at=time_utils.now().replace(tzinfo=None),
        )
      )

  @staticmethod
  def _protected_sell_price(
    context: ExitEvaluationContext, record: AutoExitPlanRecord
  ) -> float:
    bid = float(context.bid_price or context.current_price or 0.0)
    tick = max(float(context.price_tick or 0.01), 1e-8)
    plan = ExitPlan.from_dict(dict(record.plan_state or {}))
    slippage_bps = float(plan.template.execution.max_slippage_bps or 0.0)
    raw = bid * (1.0 - slippage_bps / 10_000.0)
    if context.limit_down > 0:
      raw = max(raw, float(context.limit_down))
    ticks = (Decimal(str(raw)) / Decimal(str(tick))).to_integral_value(
      rounding=ROUND_FLOOR
    )
    return float(ticks * Decimal(str(tick)))


def _optional_float(value: Any) -> Optional[float]:
  try:
    if value is None:
      return None
    return float(value)
  except (TypeError, ValueError):
    return None


def _optional_datetime(value: Any) -> Optional[datetime]:
  if isinstance(value, datetime):
    return value.replace(tzinfo=None)
  if isinstance(value, str) and value:
    try:
      return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(
        tzinfo=None
      )
    except ValueError:
      return None
  return None
