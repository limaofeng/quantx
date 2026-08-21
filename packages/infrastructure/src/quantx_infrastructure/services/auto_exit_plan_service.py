"""Persistent orchestration for Engine-owned automatic exit plans."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from math import isfinite
from typing import Any, Callable, Mapping, Optional

from quantx_domain.trading.exit_plan import (
  EXIT_PLAN_BOOK_STATE_KEY,
  ExitBuyFeeTreatment,
  ExitCostBasisMode,
  ExitCostBasisOrderSnapshot,
  ExitCostBasisSnapshot,
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
  TradingCostPolicy,
  estimate_buy_fee_cny,
)
from sqlalchemy import select

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import PendingTradeOrder
from quantx_infrastructure.models.auto_exit_plan import (
  AutoExitPlanEvent,
  AutoExitPlanRecord,
)
from quantx_infrastructure.models.enums import OrderType
from quantx_infrastructure.models.liquidation import (
  ConditionalLiquidationOrder,
  ConditionalLiquidationSellMode,
  ConditionalLiquidationStatus,
)
from quantx_infrastructure.models.order import Order
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.strategy_run import StrategyRun
from quantx_infrastructure.models.strategy_run_state import StrategyRunState
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.auto_exit_plan_repository import (
  AutoExitPlanRepository,
)
from quantx_infrastructure.services.exit_plan_authorization_service import (
  authorization_expiry_for_challenge,
  build_exit_plan_authorization_snapshot,
  clear_exact_auto_exit_authorization,
  grant_exact_auto_exit_authorization,
  validate_exact_auto_exit_authorization,
)
from quantx_infrastructure.services.exit_plan_notifications import (
  install_exit_plan_notification_hooks,
)
from quantx_infrastructure.services.exit_plan_scope_lock import (
  LockedExitPlanScope,
  lock_exit_plan_scope,
  lock_exit_plan_scope_for_plan,
)
from quantx_infrastructure.services.trade_command_service import TradeCommandService
from quantx_infrastructure.services.trade_intent_processor import (
  MARKET_DATA_STREAM_NOT_READY,
  TradeIntentProcessor,
)

install_exit_plan_notification_hooks()

MARKET_DATA_CONTEXT_STALE_SECONDS = 10.0

ADAPTIVE_RULE_ID_SUFFIX = "adaptive-volume-price"
ACTIVE_ORDER_STATUSES = {
  "QUEUED",
  "PENDING",
  "SUBMITTED",
  "REPORTED",
  "PARTIAL_FILLED",
}
MANUAL_PLAN_SOURCE = "MANUAL_POSITION"
MANUAL_LIQUIDATION_SOURCE = "MANUAL_LIQUIDATION"
AVAILABLE_NOW = "AVAILABLE_NOW"
UNTIL_SNAPSHOT_CLEARED = "UNTIL_SNAPSHOT_CLEARED"
UNALLOCATED_ONLY = "UNALLOCATED_ONLY"
REPLACE_CANCELLABLE = "REPLACE_CANCELLABLE"
TERMINAL_PLAN_STATUSES = {"COMPLETED", "CANCELLED"}
CAPACITY_READY = "READY"
CAPACITY_RECONCILE_REQUIRED = "RECONCILE_REQUIRED"

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
  async def list_cost_basis_candidates(
    self,
    *,
    account_id: str,
    instrument_code: str,
    limit: int = 100,
  ) -> list[dict[str, Any]]:
    """Return persisted completed BUY orders eligible as cost evidence."""

    code = str(instrument_code or "").strip().upper()
    async with AsyncSessionLocal() as db:
      reserving = await AutoExitPlanRepository(db).find_reserving(
        account_id=account_id,
        instrument_code=code,
      )
      claimed_order_ids = self._claimed_cost_basis_order_ids(reserving)
      orders = list(
        (
          await db.execute(
            select(Order)
            .where(Order.account_id == account_id)
            .where(Order.stock_code == code)
            .where(Order.type == OrderType.BUY)
            .where(Order.traded_volume > 0)
            .where(Order.traded_price > 0)
            .order_by(Order.time.desc(), Order.id.desc())
            .limit(max(1, min(int(limit or 100), 200)))
          )
        )
        .scalars()
        .all()
      )
      costs = TradingCostPolicy()
      return [
        {
          "order_id": str(order.id),
          "traded_volume": int(order.traded_volume or 0),
          "traded_price": float(order.traded_price or 0.0),
          "estimated_buy_fee_cny": estimate_buy_fee_cny(
            price=float(order.traded_price or 0.0),
            volume=int(order.traded_volume or 0),
            costs=costs,
          ),
          "order_time": order.time,
          "strategy_name": order.strategy_name,
          "remark": order.remark,
        }
        for order in orders
        if str(order.id) not in claimed_order_ids
      ]

  async def reconcile_holding_capacity(
    self,
    *,
    account_id: str,
    instrument_code: str,
  ) -> dict[str, Any]:
    """Recheck logical plan claims against the latest position snapshot."""

    async with AsyncSessionLocal() as db:
      result = await self._reconcile_capacity_locked(
        db,
        account_id=account_id,
        instrument_code=str(instrument_code or "").strip().upper(),
        allow_restore=True,
      )
      await db.commit()
      return result

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
      for plan in sorted(book.plans.values(), key=lambda item: item.plan_id):
        template = ExitPlanTemplate.from_dict(
          {
            **plan.template.to_dict(),
            # Strategy templates may request automation, but a LIVE plan only
            # receives that authority from a durable device challenge.
            "auto_exit_authorized": False,
          }
        )
        if not template.account_id:
          continue
        record = await repo.find_by_id(plan.plan_id, for_update=True)
        if record is not None:
          record_version = int(record.config_version or 0)
          template_version = int(template.config_version or 0)
          persistent_plan = ExitPlan.from_dict(dict(record.plan_state or {}))
          self._require_strategy_entry_sync_binding(
            record=record,
            persistent_plan=persistent_plan,
            incoming_plan=plan,
            strategy_run_id=strategy_run_id,
          )
          expansion = self._merge_strategy_entry_snapshot(
            persistent_plan=persistent_plan,
            incoming_plan=plan,
          )
          if expansion is None and plan.status in {
            ExitPlanStatus.CANCELLED,
            ExitPlanStatus.COMPLETED,
          }:
            continue
          if record_version >= template_version:
            if expansion is None:
              continue
            persistent_plan.template = ExitPlanTemplate.from_dict(
              {
                **persistent_plan.template.to_dict(),
                "auto_exit_authorized": False,
              }
            )
            clear_exact_auto_exit_authorization(record)
            event_type = "STRATEGY_PLAN_ENTRY_EXPANDED"
            business_key = (
              f"strategy-plan-entry-expanded:{plan.plan_id}:"
              f"{record_version}:{expansion['entry_filled_volume']}"
            )
            event_payload = {
              "strategy_run_id": strategy_run_id,
              "source_type": persistent_plan.template.source_type,
              "config_version": record_version,
              "incoming_template_version": template_version,
              **expansion,
            }
          else:
            persistent_plan.apply_template(template)
            record.config_version = template_version
            clear_exact_auto_exit_authorization(record)
            event_type = "STRATEGY_PLAN_POLICY_UPDATED"
            business_key = f"strategy-plan-sync:{plan.plan_id}:{template_version}"
            event_payload = {
              "strategy_run_id": strategy_run_id,
              "source_type": template.source_type,
              "config_version": template_version,
            }
            if expansion is not None:
              event_payload.update(expansion)
          if expansion is not None and bool(expansion["reactivated_from_completed"]):
            record.enabled = True
          self._sync_record(record, persistent_plan)
          if expansion is not None:
            record.protected_volume = int(persistent_plan.entry_filled_volume)
            record.entry_avg_price = float(persistent_plan.entry_avg_price)
            event_payload.update(
              {
                "status": persistent_plan.status.value,
                "monitor_enabled": bool(record.enabled),
                "unprotected_terminal": (
                  persistent_plan.status == ExitPlanStatus.CANCELLED
                ),
              }
            )
        else:
          if plan.status in {ExitPlanStatus.CANCELLED, ExitPlanStatus.COMPLETED}:
            continue
          persistent_plan = ExitPlan.from_dict(plan.to_dict())
          persistent_plan.apply_template(template)
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
            auto_exit_authorized=False,
            config_version=int(template.config_version),
            protected_volume=int(plan.entry_filled_volume or 0),
            exited_volume=int(plan.exited_volume or 0),
            remaining_volume=int(plan.remaining_volume or 0),
            entry_avg_price=float(plan.entry_avg_price or 0.0),
            plan_state=persistent_plan.to_dict(),
          )
          self._sync_record(record, persistent_plan)
          db.add(record)
          event_type = "STRATEGY_PLAN_PERSISTED"
          business_key = f"strategy-plan-sync:{plan.plan_id}:{template.config_version}"
          event_payload = {
            "strategy_run_id": strategy_run_id,
            "source_type": template.source_type,
            "config_version": template.config_version,
          }
        await self._append_event(
          db,
          business_key=business_key,
          plan_id=plan.plan_id,
          event_type=event_type,
          payload=event_payload,
        )
        synced += 1
      await db.commit()
    return synced

  @staticmethod
  def _require_strategy_entry_sync_binding(
    *,
    record: AutoExitPlanRecord,
    persistent_plan: ExitPlan,
    incoming_plan: ExitPlan,
    strategy_run_id: str,
  ) -> None:
    persistent = persistent_plan.template
    incoming = incoming_plan.template
    bindings = {
      "plan_id": (record.plan_id, persistent.plan_id, incoming.plan_id),
      "account_id": (
        record.account_id,
        persistent.account_id,
        incoming.account_id,
      ),
      "instrument_code": (
        record.instrument_code,
        persistent.instrument_code,
        incoming.instrument_code,
      ),
      "bucket": (record.bucket, persistent.bucket, incoming.bucket),
      "source_type": (
        record.source_type,
        persistent.source_type,
        incoming.source_type,
      ),
      "source_id": (
        record.source_id,
        persistent.source_id,
        incoming.source_id,
      ),
      "strategy_run_id": (
        record.strategy_run_id,
        persistent.run_id,
        incoming.run_id,
        strategy_run_id,
      ),
    }
    for field, values in bindings.items():
      normalized = [str(value or "").strip() for value in values]
      if not normalized[0] or any(value != normalized[0] for value in normalized):
        raise ValueError(f"strategy exit-plan {field} binding mismatch")

  @staticmethod
  def _merge_strategy_entry_snapshot(
    *,
    persistent_plan: ExitPlan,
    incoming_plan: ExitPlan,
  ) -> Optional[dict[str, Any]]:
    """Monotonically merge cumulative entry fills without replacing runtime facts."""

    previous_volume = max(0, int(persistent_plan.entry_filled_volume or 0))
    incoming_volume = max(0, int(incoming_plan.entry_filled_volume or 0))
    if incoming_volume <= previous_volume:
      return None

    incoming_avg_price = float(incoming_plan.entry_avg_price or 0.0)
    if not isfinite(incoming_avg_price) or incoming_avg_price <= 0:
      raise ValueError(
        "strategy exit-plan entry snapshot grew without a valid average price"
      )
    previous_avg_price = float(persistent_plan.entry_avg_price or 0.0)
    if not isfinite(previous_avg_price) or previous_avg_price < 0:
      raise ValueError(
        "persistent strategy exit-plan has an invalid average entry price"
      )
    previous_notional = previous_avg_price * previous_volume
    incoming_notional = incoming_avg_price * incoming_volume
    incremental_volume = incoming_volume - previous_volume
    incremental_notional = incoming_notional - previous_notional
    if previous_volume > 0 and incremental_notional <= 0:
      raise ValueError(
        "strategy exit-plan cumulative entry snapshot regressed its notional"
      )
    incremental_avg_price = incremental_notional / incremental_volume
    if not isfinite(incremental_avg_price) or incremental_avg_price <= 0:
      raise ValueError(
        "strategy exit-plan incremental entry snapshot has an invalid price"
      )
    merged_avg_price = (
      previous_notional + incremental_avg_price * incremental_volume
    ) / incoming_volume

    reactivated_from_completed = persistent_plan.status == ExitPlanStatus.COMPLETED
    persistent_plan.entry_filled_volume = incoming_volume
    persistent_plan.entry_avg_price = merged_avg_price
    if not persistent_plan.entry_trade_date and incoming_plan.entry_trade_date:
      persistent_plan.entry_trade_date = incoming_plan.entry_trade_date
    if persistent_plan.status == ExitPlanStatus.COMPLETED:
      persistent_plan.status = (
        ExitPlanStatus.PARTIALLY_EXITED
        if int(persistent_plan.exited_volume or 0) > 0
        else ExitPlanStatus.ACTIVE
      )

    return {
      "previous_entry_filled_volume": previous_volume,
      "entry_filled_volume": incoming_volume,
      "previous_entry_avg_price": previous_avg_price,
      "entry_avg_price": merged_avg_price,
      "reactivated_from_completed": reactivated_from_completed,
    }

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

    if bool(payload.get("auto_exit_authorized", False)):
      raise ValueError(
        "AUTO_EXIT_AUTHORIZATION_REQUIRES_CHALLENGE: 布尔字段不能开启自动实盘退出"
      )
    account_id = str(payload.get("account_id") or "").strip()
    instrument_code = str(payload.get("instrument_code") or "").strip().upper()
    if not account_id or not instrument_code:
      raise ValueError("人工计划必须指定账户和股票")
    async with AsyncSessionLocal() as db:
      scope = await lock_exit_plan_scope(
        db,
        account_id=account_id,
        instrument_code=instrument_code,
      )
      position = scope.position
      if position is None or int(position.volume or 0) <= 0:
        raise ValueError(f"未找到 {instrument_code} 的有效持仓")
      reserving = scope.plans
      reserved = sum(max(0, int(item.remaining_volume or 0)) for item in reserving)
      unallocated = max(0, int(position.volume or 0) - reserved)
      requested = int(payload.get("protected_volume") or unallocated)
      if requested <= 0 or requested > unallocated:
        raise ValueError(
          f"可认领数量不足：未分配 {unallocated} 股，申请 {requested} 股"
        )
      cost_basis = await self._resolve_manual_cost_basis(
        db,
        payload=payload,
        account_id=account_id,
        instrument_code=instrument_code,
        requested_volume=requested,
        reserving_plans=reserving,
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
          "cost_basis": cost_basis.to_dict(),
        },
        auto_exit_authorized=False,
      )
      plan = ExitPlanBook().register_entry_fill(
        template,
        volume=requested,
        price=cost_basis.unit_cost_cny,
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
        auto_exit_authorized=False,
        config_version=1,
        protected_volume=requested,
        exited_volume=0,
        remaining_volume=requested,
        entry_avg_price=cost_basis.unit_cost_cny,
        cost_basis_mode=cost_basis.mode.value,
        cost_basis_snapshot=cost_basis.to_dict(),
        capacity_status=CAPACITY_READY,
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
        payload={
          "source_type": MANUAL_PLAN_SOURCE,
          "protected_volume": requested,
          "cost_basis": cost_basis.to_dict(),
        },
      )
      await self._append_event(
        db,
        business_key=f"cost-basis-frozen:{plan_id}:1",
        plan_id=plan_id,
        event_type="COST_BASIS_FROZEN",
        payload=cost_basis.to_dict(),
      )
      await db.commit()
      await db.refresh(record)
      return record

  async def update_manual_exit_plan(
    self,
    payload: Mapping[str, Any],
  ) -> AutoExitPlanRecord:
    if bool(payload.get("auto_exit_authorized", False)):
      raise ValueError(
        "AUTO_EXIT_AUTHORIZATION_REQUIRES_CHALLENGE: 布尔字段不能开启自动实盘退出"
      )
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

      scope = await lock_exit_plan_scope(
        db,
        account_id=record.account_id,
        instrument_code=record.instrument_code,
        target_plan_id=plan_id,
      )
      position = scope.position
      reserving = scope.plans
      record = scope.plan(plan_id) or record
      if expected_version <= 0 or int(record.config_version) != expected_version:
        raise ValueError(f"CONFIG_VERSION_CONFLICT: current={record.config_version}")
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
        raise ValueError(f"保护数量不能小于已卖数量 {int(plan.exited_volume or 0)} 股")
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
      cost_basis = plan.cost_basis
      if (
        cost_basis.mode == ExitCostBasisMode.BROKER_BUY_ORDERS
        and protected_volume > cost_basis.basis_volume
      ):
        raise ValueError(
          f"计划卖出数量不能超过已选成交委托数量 {cost_basis.basis_volume} 股"
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
        auto_exit_authorized=False,
      )
      plan.apply_template(template)
      plan.entry_filled_volume = protected_volume
      record.config_version = next_version
      record.protected_volume = protected_volume
      record.remaining_volume = desired_remaining
      record.execution_mode = self._execution_mode(
        payload.get("execution_mode", record.execution_mode)
      )
      clear_exact_auto_exit_authorization(record)
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
    execution_mode = self._execution_mode(payload.get("execution_mode"))
    auto_exit_authorization_requested = bool(payload.get("auto_exit_authorized", False))
    auto_exit_authorized = bool(
      execution_mode == "live" and auto_exit_authorization_requested
    )
    selected = {
      str(item or "").strip().upper()
      for item in list(payload.get("instrument_codes") or [])
      if str(item or "").strip()
    }
    scope = str(payload.get("scope") or "SELECTED").upper()
    if scope == "SELECTED" and not selected:
      raise ValueError("请选择至少一只持仓")
    requested_group_id = str(payload.get("group_id") or "").strip()
    if requested_group_id:
      try:
        group_id = str(uuid.UUID(requested_group_id))
      except ValueError as exc:
        raise ValueError("清仓组 ID 无效") from exc
    else:
      group_id = str(uuid.uuid4())
    expected_items = {
      str(item.get("instrument_code") or "").strip().upper(): dict(item)
      for item in list(payload.get("expected_items") or [])
      if isinstance(item, Mapping) and str(item.get("instrument_code") or "").strip()
    }
    snapshot_version = str(payload.get("authorization_snapshot_version") or "").strip()
    authorization_challenge_id = str(
      payload.get("authorization_challenge_id") or ""
    ).strip()
    native_confirmation = bool(expected_items or authorization_challenge_id)
    if native_confirmation and (
      not expected_items or not snapshot_version or not authorization_challenge_id
    ):
      raise ValueError("移动端清仓命令缺少完整快照授权")
    if not native_confirmation and (
      execution_mode != "paper" or auto_exit_authorization_requested
    ):
      raise ValueError(
        "LEGACY_LIQUIDATION_UNSAFE_MODE: "
        "未携带移动端清仓确认挑战，只允许 PAPER 且禁止自动卖出授权"
      )
    results: list[dict[str, Any]] = []
    async with AsyncSessionLocal() as db:
      if native_confirmation:
        existing_group = list(
          (
            await db.execute(
              select(AutoExitPlanRecord)
              .where(AutoExitPlanRecord.group_id == group_id)
              .order_by(AutoExitPlanRecord.instrument_code)
            )
          )
          .scalars()
          .all()
        )
        if existing_group:
          existing_by_code = {
            str(record.instrument_code): record for record in existing_group
          }
          replay_items: list[dict[str, Any]] = []
          for code, expected in expected_items.items():
            record = existing_by_code.get(code)
            if record is not None:
              replay_items.append(
                {
                  "instrument_code": record.instrument_code,
                  "success": True,
                  "plan_id": record.plan_id,
                  "protected_volume": int(record.protected_volume or 0),
                  "conflict_plan_ids": [],
                }
              )
            else:
              replay_items.append(
                {
                  "instrument_code": code,
                  "success": False,
                  "error": str(
                    expected.get("reason_detail") or "首次处理未创建该证券的清仓计划"
                  ),
                  "conflict_plan_ids": [
                    str(item.get("plan_id") or "")
                    for item in list(expected.get("conflicts") or [])
                    if str(item.get("plan_id") or "")
                  ],
                }
              )
          return {
            "group_id": group_id,
            "success": all(item.get("success") for item in replay_items),
            "items": replay_items,
          }
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
      pending_sell_by_code: dict[str, list[PendingTradeOrder]] = {}
      if native_confirmation:
        pending_sell_rows = list(
          (
            await db.execute(
              select(PendingTradeOrder)
              .where(PendingTradeOrder.account_id == account_id)
              .where(PendingTradeOrder.instrument_code.in_(selected))
              .where(PendingTradeOrder.side == "SELL")
              .where(PendingTradeOrder.status.in_(ACTIVE_ORDER_STATUSES))
              .with_for_update()
            )
          )
          .scalars()
          .all()
        )
        for order in pending_sell_rows:
          pending_sell_by_code.setdefault(
            str(order.instrument_code).upper(), []
          ).append(order)
      for position in positions:
        code = str(position.stock_code)
        expected = expected_items.get(code) if native_confirmation else None
        if native_confirmation and expected is None:
          results.append(
            {
              "instrument_code": code,
              "success": False,
              "error": "证券不在已确认的固定清仓快照中",
            }
          )
          continue
        if expected is not None and not bool(expected.get("included")):
          results.append(
            {
              "instrument_code": code,
              "success": False,
              "error": str(expected.get("reason_detail") or "预览已跳过该持仓"),
              "conflict_plan_ids": [
                str(item.get("plan_id") or "")
                for item in list(expected.get("conflicts") or [])
                if str(item.get("plan_id") or "")
              ],
            }
          )
          continue
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
        direct_pending = pending_sell_by_code.get(code, [])
        if pending or direct_pending:
          results.append(
            {
              "instrument_code": code,
              "success": False,
              "error": "存在待成交卖单，必须先等待回报或撤单",
              "conflict_plan_ids": [item.plan_id for item in pending],
            }
          )
          continue
        if expected is not None:
          current_conflicts = [
            {
              "plan_id": str(item.plan_id),
              "source_type": str(item.source_type),
              "status": str(item.status),
              "remaining_volume": max(0, int(item.remaining_volume or 0)),
              "config_version": max(0, int(item.config_version or 0)),
              "pending": bool(
                str(item.status or "").upper() == "EXIT_PENDING"
                or item.pending_client_order_id
              ),
            }
            for item in reserving
          ]
          expected_conflicts = [
            {
              "plan_id": str(item.get("plan_id") or ""),
              "source_type": str(item.get("source_type") or ""),
              "status": str(item.get("status") or ""),
              "remaining_volume": max(0, int(item.get("remaining_volume") or 0)),
              "config_version": max(0, int(item.get("config_version") or 0)),
              "pending": bool(item.get("pending")),
            }
            for item in list(expected.get("conflicts") or [])
          ]
          if current_conflicts != expected_conflicts:
            results.append(
              {
                "instrument_code": code,
                "success": False,
                "error": "退出计划冲突在确认排队后发生变化",
                "conflict_plan_ids": [item.plan_id for item in reserving],
              }
            )
            continue
        conflict_plan_ids = [item.plan_id for item in reserving]
        reserved = (
          0
          if conflict_strategy == REPLACE_CANCELLABLE
          else sum(max(0, int(item.remaining_volume or 0)) for item in reserving)
        )
        snapshot_target = (
          int(position.can_use_volume or 0)
          if completion == AVAILABLE_NOW
          else int(position.volume or 0)
        )
        target = max(0, min(snapshot_target, int(position.volume or 0) - reserved))
        if expected is not None:
          target = min(target, max(0, int(expected.get("max_protected_volume") or 0)))
        if target <= 0:
          results.append(
            {
              "instrument_code": code,
              "success": False,
              "error": "持仓数量已被其他退出计划保护",
              "conflict_plan_ids": conflict_plan_ids,
            }
          )
          continue
        # Never remove existing protection until a positive, bounded
        # replacement can be created in this same transaction.
        if conflict_strategy == REPLACE_CANCELLABLE:
          for existing in reserving:
            old_plan = ExitPlan.from_dict(dict(existing.plan_state or {}))
            old_plan.status = ExitPlanStatus.CANCELLED
            old_plan.error_message = f"REPLACED_BY_LIQUIDATION_GROUP:{group_id}"
            existing.enabled = False
            existing.config_version = int(existing.config_version or 0) + 1
            old_plan.template = ExitPlanTemplate.from_dict(
              {
                **old_plan.template.to_dict(),
                "config_version": existing.config_version,
                "auto_exit_authorized": False,
              }
            )
            clear_exact_auto_exit_authorization(existing)
            self._sync_record(existing, old_plan)
            await self._append_event(
              db,
              business_key=f"plan-replaced:{existing.plan_id}:{group_id}",
              plan_id=existing.plan_id,
              event_type="PLAN_CANCELLED",
              payload={"replacement_group_id": group_id},
            )
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
            "authorization_challenge_id": authorization_challenge_id or None,
            "authorization_snapshot_version": snapshot_version or None,
            "authorized_max_protected_volume": (
              int(expected.get("max_protected_volume") or 0)
              if expected is not None
              else None
            ),
          },
          auto_exit_authorized=False,
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
          execution_mode=execution_mode,
          auto_exit_authorized=False,
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
        if auto_exit_authorized:
          await self._grant_liquidation_group_authorization(
            db,
            record=record,
            challenge_id=authorization_challenge_id,
            snapshot_version=snapshot_version,
            group_id=group_id,
          )
        await self._append_event(
          db,
          business_key=f"liquidation-plan-created:{plan_id}",
          plan_id=plan_id,
          event_type="LIQUIDATION_PLAN_CREATED",
          payload={
            "group_id": group_id,
            "completion_strategy": completion,
            "protected_volume": target,
            "conflict_plan_ids": conflict_plan_ids,
          },
        )
        results.append(
          {
            "instrument_code": code,
            "success": True,
            "plan_id": plan_id,
            "protected_volume": target,
            "conflict_plan_ids": conflict_plan_ids,
          }
        )
      await db.commit()
    return {
      "group_id": group_id,
      "success": bool(results) and all(item.get("success") for item in results),
      "items": results,
    }

  async def _grant_liquidation_group_authorization(
    self,
    db,
    *,
    record: AutoExitPlanRecord,
    challenge_id: str,
    snapshot_version: str,
    group_id: str,
  ) -> None:
    """Carry an already-consumed native liquidation challenge into the plan."""

    challenge = await db.get(TradeConfirmationChallenge, challenge_id)
    payload = dict(challenge.payload or {}) if challenge is not None else {}
    signed_snapshot = dict(payload.get("snapshot") or {})
    if (
      challenge is None
      or challenge.consumed_at is None
      or str(challenge.action) != "LIQUIDATION_GROUP"
      or str(challenge.account_id) != str(record.account_id)
      or str(payload.get("group_id") or "") != group_id
      or str(signed_snapshot.get("snapshot_version") or "") != snapshot_version
    ):
      raise ValueError("清仓计划缺少已消费且精确匹配的设备确认挑战")
    command_service = TradeCommandService(db)
    await command_service._require_manual_live_authorization(
      record.account_id,
      risk_reducing=True,
    )
    await command_service._require_live_authorization(
      record.account_id,
      risk_reducing=True,
    )
    await command_service._device_for(
      user_id=str(challenge.user_id),
      account_id=record.account_id,
      execution_mode="live",
    )
    authorization_snapshot = await build_exit_plan_authorization_snapshot(
      db,
      record,
      lock_mutable_rows=True,
    )
    authorization_expires_at = authorization_expiry_for_challenge(challenge.expires_at)
    grant_exact_auto_exit_authorization(
      record,
      fingerprint=authorization_snapshot.fingerprint,
      challenge_id=str(challenge.id),
      user_id=str(challenge.user_id),
      device_session_id=str(challenge.device_session_id),
      authorized_at=time_utils.now(),
      authorization_expires_at=authorization_expires_at,
    )
    validation = await validate_exact_auto_exit_authorization(
      db,
      record,
      lock_mutable_rows=True,
    )
    if not validation.valid:
      raise ValueError(f"清仓计划自动退出授权已失效：{validation.code}")
    await self._append_event(
      db,
      business_key=f"auto-exit-authorized:{record.plan_id}:{challenge.id}",
      plan_id=record.plan_id,
      event_type="AUTO_EXIT_AUTHORIZED",
      payload={
        "actor_user_id": str(challenge.user_id),
        "device_session_id": str(challenge.device_session_id),
        "challenge_id": str(challenge.id),
        "plan_id": str(record.plan_id),
        "config_version": int(record.config_version or 0),
        "authorization_fingerprint": authorization_snapshot.fingerprint,
        "authorization_expires_at": authorization_expires_at.isoformat(),
      },
    )

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
      scope = await lock_exit_plan_scope(
        db,
        account_id=order.account_id,
        instrument_code=order.stock_code,
        target_plan_id=plan_id,
      )
      position = scope.position or position
      record = scope.plan(plan_id)
      if record is None:
        record = await repo.find_by_source("MANUAL_POSITION", str(order.id))
      reserving = scope.plans
      others = [item for item in reserving if item.plan_id != plan_id]
      if any(
        item.status == ExitPlanStatus.EXIT_PENDING.value or item.pending_client_order_id
        for item in others
      ):
        raise ValueError("该持仓存在待成交卖单，不能重复认领数量")
      other_reserved = sum(max(0, int(item.remaining_volume or 0)) for item in others)
      unallocated = max(0, int(getattr(position, "volume", 0) or 0) - other_reserved)
      if volume > unallocated:
        raise ValueError(f"可认领数量不足：未分配 {unallocated} 股，申请 {volume} 股")
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
        clear_exact_auto_exit_authorization(record)
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
      record.auto_exit_authorized = False
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
      if config_version is not None and int(record.config_version) != int(
        config_version
      ):
        raise ValueError(f"CONFIG_VERSION_CONFLICT: current={record.config_version}")
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
        {
          **plan.template.to_dict(),
          "config_version": record.config_version,
          "auto_exit_authorized": False,
        }
      )
      clear_exact_auto_exit_authorization(record)
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
      if config_version is not None and int(record.config_version) != int(
        config_version
      ):
        raise ValueError(f"CONFIG_VERSION_CONFLICT: current={record.config_version}")
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      if plan.status == ExitPlanStatus.EXIT_PENDING or plan.pending_order_id:
        raise ValueError("存在待成交卖单，必须先等待回报或撤单")
      plan.status = ExitPlanStatus.CANCELLED
      plan.error_message = reason
      record.enabled = False
      record.config_version = int(record.config_version) + 1
      plan.template = ExitPlanTemplate.from_dict(
        {
          **plan.template.to_dict(),
          "config_version": record.config_version,
          "auto_exit_authorized": False,
        }
      )
      clear_exact_auto_exit_authorization(record)
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
    market_ready: Optional[Callable[[], bool]] = None,
  ) -> Optional[dict[str, Any]]:
    async with AsyncSessionLocal() as db:
      scope = await lock_exit_plan_scope_for_plan(db, plan_id)
      record = scope.plan(plan_id)
      if record is None or not record.enabled:
        return None
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      market_error = self._market_context_error(context, market_ready)
      if market_error:
        await self._persist_market_data_stale(
          db,
          record,
          plan,
          evaluated_at=context.timestamp,
          error=market_error,
        )
        return None
      capacity = await self._reconcile_capacity_locked(
        db,
        account_id=record.account_id,
        instrument_code=record.instrument_code,
        locked_scope=scope,
      )
      if not capacity["ready"]:
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
      market_ready=market_ready,
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
      plan_id = (
        str((pending.request_metadata or {}).get("exit_plan_id") or "")
        if pending
        else ""
      )
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
      plan_id = (
        str((pending.request_metadata or {}).get("exit_plan_id") or "")
        if pending
        else ""
      )
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
    market_ready: Optional[Callable[[], bool]] = None,
  ) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
      scope = await lock_exit_plan_scope_for_plan(db, plan_id)
      record = scope.plan(plan_id)
      intent = await db.get(TradeIntentRecord, intent_id)
      if record is None or intent is None:
        raise ValueError("退出计划或卖出意图不存在")
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      if plan.pending_intent_id != intent_id:
        raise ValueError("卖出意图已变化，请刷新后重试")
      market_error = self._market_context_error(context, market_ready)
      if market_error:
        intent.status = "REJECTED"
        intent.notes = market_error
        intent.intent_metadata = {
          **dict(intent.intent_metadata or {}),
          "market_data_gate": market_error,
        }
        ExitPlanBook([plan]).apply_order_event(
          plan_id=plan_id,
          intent_id=intent_id,
          status="REJECTED",
        )
        await self._persist_market_data_stale(
          db,
          record,
          plan,
          evaluated_at=context.timestamp,
          error=market_error,
        )
        return {
          "success": False,
          "code": (
            MARKET_DATA_STREAM_NOT_READY
            if market_error == MARKET_DATA_STREAM_NOT_READY
            else "MARKET_DATA_STALE"
          ),
          "error": market_error,
        }
      capacity = await self._reconcile_capacity_locked(
        db,
        account_id=record.account_id,
        instrument_code=record.instrument_code,
        locked_scope=scope,
      )
      if not capacity["ready"]:
        await db.commit()
        raise ValueError("持仓少于计划认领数量，请先完成持仓对账")
      limit_price = self._protected_sell_price(context, record)
    result = await TradeIntentProcessor().process_approved_exit_intent(
      plan=record,
      record=intent,
      context=context,
      position=position,
      limit_price=limit_price,
      market_ready=market_ready,
    )
    if not result.get("success"):
      await self._release_failed_submission(
        plan_id,
        intent_id,
        str(result.get("error") or "exit_intent_rejected"),
      )
      return result
    client_order_id = str(result.get("client_order_id") or result.get("order_id") or "")
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

  @staticmethod
  def _market_context_error(
    context: ExitEvaluationContext,
    market_ready: Optional[Callable[[], bool]],
  ) -> str:
    if market_ready is not None:
      try:
        if not bool(market_ready()):
          return MARKET_DATA_STREAM_NOT_READY
      except Exception:
        return MARKET_DATA_STREAM_NOT_READY
    # miniQMT whole-quote callbacks normally arrive on an approximately
    # three-second cadence. A three-second cutoff rejects healthy data during
    # ordinary scheduling jitter; keep this aligned with the authoritative
    # WholeQuoteHub trading-session freshness window.
    if (
      float(context.market_data_age_seconds or 0.0) > MARKET_DATA_CONTEXT_STALE_SECONDS
    ):
      return "market_data_stale"
    return ""

  async def _persist_market_data_stale(
    self,
    db,
    record: AutoExitPlanRecord,
    plan: ExitPlan,
    *,
    evaluated_at: datetime,
    error: str,
  ) -> None:
    self._sync_record(record, plan, evaluated_at=evaluated_at)
    # The persisted stream gate is authoritative over adaptive rule projections.
    record.data_quality = "MARKET_DATA_STALE"
    record.last_error = error
    await self._sync_source_order(
      db,
      record,
      plan,
      checked_at=evaluated_at,
    )
    await db.commit()

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
    market_ready: Optional[Callable[[], bool]] = None,
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
      scope = await lock_exit_plan_scope_for_plan(db, plan_id)
      record = scope.plan(plan_id)
      if record is None:
        return None
      capacity = await self._reconcile_capacity_locked(
        db,
        account_id=record.account_id,
        instrument_code=record.instrument_code,
        locked_scope=scope,
      )
      if not capacity["ready"]:
        await db.commit()
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
        market_ready=market_ready,
      )
    except Exception as exc:
      await self._release_failed_submission(plan_id, intent_id, str(exc))
      return None

    if result.get("awaiting_approval"):
      async with AsyncSessionLocal() as db:
        stored = await AutoExitPlanRepository(db).find_by_id(plan_id, for_update=True)
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

    client_order_id = str(result.get("client_order_id") or result.get("order_id") or "")
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
      self._sync_record(record, plan)
      record.last_error = error[:2000]
      if error == MARKET_DATA_STREAM_NOT_READY:
        record.data_quality = "MARKET_DATA_STALE"
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
          plan.template = ExitPlanTemplate.from_dict(
            {
              **plan.template.to_dict(),
              "auto_exit_authorized": False,
            }
          )
          clear_exact_auto_exit_authorization(record)
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
            parameters={"target_profit_pct": float(order.target_profit_pct)},
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
      auto_exit_authorized=False,
    )

  @staticmethod
  def _sync_record(
    record: AutoExitPlanRecord,
    plan: ExitPlan,
    *,
    evaluated_at: Optional[datetime] = None,
  ) -> None:
    if bool(record.auto_exit_authorized) and (
      int(record.exited_volume or 0) != int(plan.exited_volume or 0)
      or int(record.remaining_volume or 0) != int(plan.remaining_volume or 0)
    ):
      # A fill changes the exact quantity/account facts covered by the grant.
      # Persist the fill, keep the plan alive, and require a fresh grant before
      # another autonomous LIVE order.
      plan.template = ExitPlanTemplate.from_dict(
        {
          **plan.template.to_dict(),
          "auto_exit_authorized": False,
        }
      )
      clear_exact_auto_exit_authorization(record)
    adaptive_state = next(
      (
        dict(value or {})
        for key, value in plan.rule_state.items()
        if str(key).endswith(ADAPTIVE_RULE_ID_SUFFIX)
      ),
      {},
    )
    record.plan_state = plan.to_dict()
    cost_basis = plan.cost_basis
    record.cost_basis_mode = cost_basis.mode.value
    record.cost_basis_snapshot = cost_basis.to_dict()
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
  async def _resolve_manual_cost_basis(
    db,
    *,
    payload: Mapping[str, Any],
    account_id: str,
    instrument_code: str,
    requested_volume: int,
    reserving_plans: Optional[list[AutoExitPlanRecord]] = None,
  ) -> ExitCostBasisSnapshot:
    raw = dict(payload.get("cost_basis") or {})
    try:
      mode = ExitCostBasisMode(str(raw.get("mode") or "").upper())
    except ValueError as exc:
      raise ValueError("请选择成交委托或手工成本价作为成本依据") from exc
    costs = TradingCostPolicy()
    frozen_at = time_utils.now().isoformat()
    if mode == ExitCostBasisMode.MANUAL_UNIT_COST:
      unit_cost = float(raw.get("unit_cost_cny") or 0.0)
      if not isfinite(unit_cost) or unit_cost <= 0:
        raise ValueError("手工成本价必须大于 0")
      if list(raw.get("order_ids") or []):
        raise ValueError("手工成本价不能同时选择成交委托")
      return ExitCostBasisSnapshot(
        mode=mode,
        unit_cost_cny=unit_cost,
        basis_volume=requested_volume,
        buy_fee_treatment=ExitBuyFeeTreatment.INCLUDED,
        cost_policy=costs,
        frozen_at=frozen_at,
      )
    if mode != ExitCostBasisMode.BROKER_BUY_ORDERS:
      raise ValueError("新建人工计划只支持成交委托或手工成本价")
    try:
      order_ids = {int(item) for item in list(raw.get("order_ids") or [])}
    except (TypeError, ValueError) as exc:
      raise ValueError("成交委托编号无效") from exc
    if not order_ids:
      raise ValueError("请至少选择一笔已成交买入委托")
    claimed_order_ids = AutoExitPlanService._claimed_cost_basis_order_ids(
      reserving_plans or []
    )
    overlapping = sorted(
      str(item) for item in order_ids if str(item) in claimed_order_ids
    )
    if overlapping:
      raise ValueError(
        "所选买入委托已被其他有效卖出计划作为成本依据：" + "、".join(overlapping)
      )
    orders = list(
      (
        await db.execute(
          select(Order)
          .where(Order.id.in_(order_ids))
          .where(Order.account_id == account_id)
          .where(Order.stock_code == instrument_code)
          .where(Order.type == OrderType.BUY)
          .where(Order.traded_volume > 0)
          .where(Order.traded_price > 0)
          .with_for_update()
        )
      )
      .scalars()
      .all()
    )
    if len(orders) != len(order_ids):
      raise ValueError("所选委托包含不存在、非买入或未成交记录，请刷新后重选")
    snapshots: list[ExitCostBasisOrderSnapshot] = []
    total_volume = 0
    total_cost = 0.0
    for order in sorted(orders, key=lambda item: (item.time, item.id)):
      volume = int(order.traded_volume or 0)
      price = float(order.traded_price or 0.0)
      fee = estimate_buy_fee_cny(price=price, volume=volume, costs=costs)
      total_volume += volume
      total_cost += price * volume + fee
      snapshots.append(
        ExitCostBasisOrderSnapshot(
          order_id=str(order.id),
          traded_volume=volume,
          traded_price=price,
          estimated_buy_fee_cny=fee,
          order_time=order.time.isoformat() if order.time else "",
        )
      )
    if total_volume < requested_volume:
      raise ValueError(
        f"所选买入成交共 {total_volume} 股，少于计划卖出 {requested_volume} 股"
      )
    return ExitCostBasisSnapshot(
      mode=mode,
      unit_cost_cny=total_cost / total_volume,
      basis_volume=total_volume,
      buy_fee_treatment=ExitBuyFeeTreatment.ESTIMATED,
      selected_orders=snapshots,
      cost_policy=costs,
      frozen_at=frozen_at,
    )

  @staticmethod
  def _claimed_cost_basis_order_ids(
    plans: list[AutoExitPlanRecord],
  ) -> set[str]:
    claimed: set[str] = set()
    for item in plans:
      snapshot = dict(item.cost_basis_snapshot or {})
      if not snapshot:
        state = dict(item.plan_state or {})
        template = dict(state.get("template") or {})
        metadata = dict(template.get("metadata") or {})
        snapshot = dict(metadata.get("cost_basis") or {})
      if str(snapshot.get("mode") or "").upper() != "BROKER_BUY_ORDERS":
        continue
      for order in list(snapshot.get("selected_orders") or []):
        order_id = str(dict(order or {}).get("order_id") or "").strip()
        if order_id:
          claimed.add(order_id)
    return claimed

  async def _reconcile_capacity_locked(
    self,
    db,
    *,
    account_id: str,
    instrument_code: str,
    allow_restore: bool = False,
    locked_scope: Optional[LockedExitPlanScope] = None,
  ) -> dict[str, Any]:
    scope = locked_scope or await lock_exit_plan_scope(
      db,
      account_id=account_id,
      instrument_code=instrument_code,
    )
    position = scope.position
    plans = scope.plans
    total_volume = max(0, int(getattr(position, "volume", 0) or 0))
    protected_volume = sum(max(0, int(item.remaining_volume or 0)) for item in plans)
    capacity_sufficient = protected_volume <= total_volume
    reconciliation_pending = any(
      str(item.capacity_status or CAPACITY_READY) == CAPACITY_RECONCILE_REQUIRED
      for item in plans
    )
    ready = capacity_sufficient and (allow_restore or not reconciliation_pending)
    next_status = CAPACITY_READY if ready else CAPACITY_RECONCILE_REQUIRED
    error = None
    if not capacity_sufficient:
      error = (
        f"持仓 {total_volume} 股少于计划合计认领 {protected_volume} 股；"
        "已阻止新的卖出并撤销自动实盘授权"
      )
    elif reconciliation_pending and not allow_restore:
      error = "持仓容量已恢复，仍需显式重新对账后才能继续卖出"
    snapshot_token = (
      getattr(position, "updated_at", None).isoformat()
      if position is not None and getattr(position, "updated_at", None)
      else "missing"
    )
    for item in plans:
      previous = str(item.capacity_status or CAPACITY_READY)
      item.capacity_status = next_status
      item.capacity_error = error
      if not capacity_sufficient:
        clear_exact_auto_exit_authorization(item)
      if previous != next_status:
        await self._append_event(
          db,
          business_key=(
            f"capacity:{item.plan_id}:{next_status}:{snapshot_token}:"
            f"{total_volume}:{protected_volume}"
          ),
          plan_id=item.plan_id,
          event_type=(
            "HOLDING_CAPACITY_RECONCILIATION_REQUIRED"
            if not ready
            else "HOLDING_CAPACITY_RECONCILED"
          ),
          payload={
            "total_volume": total_volume,
            "protected_volume": protected_volume,
            "capacity_status": next_status,
          },
        )
    return {
      "ready": ready,
      "capacity_status": next_status,
      "capacity_error": error,
      "total_volume": total_volume,
      "protected_volume": protected_volume,
      "plan_ids": [item.plan_id for item in plans],
    }

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
        select(AutoExitPlanEvent).where(AutoExitPlanEvent.business_key == business_key)
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
      return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
      return None
  return None
