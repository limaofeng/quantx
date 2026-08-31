"""Persistent orchestration for Engine-owned automatic exit plans."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal
from math import isfinite
from typing import Any, Callable, Iterable, Mapping, Optional

from quantx_domain.strategies.ashare_managed_exit_plan import (
  EXIT_PLAN_ENABLED_KEY,
  MANAGED_EXIT_PLAN_KEY,
  MANAGED_EXIT_RUNTIME_KEY,
  AshareManagedExitPlanStrategy,
)
from quantx_domain.trading.exit_plan import (
  ExitBuyFeeTreatment,
  ExitCostBasisMode,
  ExitCostBasisOrderSnapshot,
  ExitCostBasisSnapshot,
  ExitDecision,
  ExitEvaluationContext,
  ExitExecutionPolicy,
  ExitPlan,
  ExitPlanBook,
  ExitPlanCommand,
  ExitPlanCommandType,
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
  is_sticky_exit_plan_error,
)
from sqlalchemy import func, or_, select

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  PendingTradeOrder,
  TTradeBatch,
)
from quantx_infrastructure.models.auto_exit_plan import (
  AutoExitPlanEvent,
  AutoExitPlanRecord,
)
from quantx_infrastructure.models.enums import (
  OrderType,
  StrategyRunMode,
  StrategyRunStatus,
)
from quantx_infrastructure.models.liquidation import (
  ConditionalLiquidationOrder,
  ConditionalLiquidationSellMode,
  ConditionalLiquidationStatus,
)
from quantx_infrastructure.models.managed_plan import ManagedPlanRecord
from quantx_infrastructure.models.order import Order
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.strategy_run import StrategyRun
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.auto_exit_plan_repository import (
  AutoExitPlanConcurrencyError,
  AutoExitPlanRepository,
)
from quantx_infrastructure.repositories.strategy_repository import StrategyRepository
from quantx_infrastructure.services.exit_plan_authorization_service import (
  T_TRADE_EXIT_AUTHORIZATION_BINDING_KEY,
  TTradeExitAuthorizationDerivation,
  authorization_expiry_for_challenge,
  build_exit_plan_authorization_snapshot,
  clear_exact_auto_exit_authorization,
  derive_exact_auto_exit_authorization_from_t_trade_entry,
  grant_exact_auto_exit_authorization,
  validate_consumed_exit_plan_sell_challenge,
  validate_exact_auto_exit_authorization,
)
from quantx_infrastructure.services.exit_plan_execution_owner import (
  INVALID_OWNER,
  MANAGED_EXIT_STRATEGY_OWNER,
  MANAGED_RUNTIME_BINDING_PENDING,
  MANAGED_RUNTIME_COMMAND_ID_KEY,
  MANAGED_RUNTIME_ENABLE_PENDING,
  MANUAL_LIQUIDATION_SOURCE,
  MANUAL_PLAN_SOURCE,
  MONITOR_EXIT_PLAN_SOURCE_TYPES,
  MONITOR_OWNER,
  RUNTIME_BOOK_OWNER,
  RUNTIME_EXIT_PLAN_SOURCE_TYPES,
  durable_exit_plan_owner_kind,
  has_managed_runtime_command_marker,
  managed_runtime_command_id,
)
from quantx_infrastructure.services.exit_plan_notifications import (
  install_exit_plan_notification_hooks,
)
from quantx_infrastructure.services.exit_plan_scope_lock import (
  LockedExitPlanScope,
  lock_exit_plan_scope,
  lock_exit_plan_scope_for_plan,
)
from quantx_infrastructure.services.exit_plan_zero_fill_safety import (
  EXIT_FILL_INTENT_MISMATCH_PREFIX,
  ZERO_FILL_PROOF_INVALIDATED_PREFIX,
)
from quantx_infrastructure.services.managed_plan_runtime_service import (
  ManagedPlanRuntimeService,
  managed_runtime_has_live_consumer,
)
from quantx_infrastructure.services.trade_command_service import TradeCommandService
from quantx_infrastructure.services.trade_intent_processor import (
  LOCAL_AGENT_PRE_EXECUTION_ZERO_FILL_SOURCE,
  LOCAL_OUTBOX_EXPIRED_ZERO_FILL_SOURCE,
  LOCAL_PRE_BROKER_ZERO_FILL_SOURCE,
  MARKET_DATA_STREAM_NOT_READY,
  TradeIntentProcessor,
  local_pre_broker_zero_fill_metadata,
)

install_exit_plan_notification_hooks()

MARKET_DATA_CONTEXT_STALE_SECONDS = 10.0
MARKET_SESSION_CLOSED = "MARKET_CLOSED"
MARKET_GATE_ERROR_CODES = frozenset(
  {
    MARKET_SESSION_CLOSED,
    "MARKET_DATA_STALE",
    MARKET_DATA_STREAM_NOT_READY,
  }
)

ADAPTIVE_RULE_ID_SUFFIX = "adaptive-volume-price"
ACTIVE_ORDER_STATUSES = {
  "QUEUED",
  "PENDING",
  "SUBMITTED",
  "REPORTED",
  "PARTIAL_FILLED",
}
T_TRADE_BATCH_SOURCE = "T_TRADE_BATCH"
MANUAL_COMMAND_ID_KEY = "manual_exit_command_id"
MANUAL_COMMAND_FINGERPRINT_KEY = "manual_exit_command_fingerprint"
MANUAL_COMMAND_KIND_KEY = "manual_exit_command_kind"
MANUAL_COMMAND_PREVIOUS_CONFIG_VERSION_KEY = (
  "manual_exit_command_previous_config_version"
)
MANAGED_EXIT_STRATEGY_CLASS_NAME = "AshareManagedExitPlanStrategy"
MANAGED_RUNTIME_COMMAND_FINGERPRINT_KEY = "managed_runtime_command_fingerprint"
MANAGED_RUNTIME_COMMAND_KIND_KEY = "managed_runtime_command_kind"
MANAGED_RUNTIME_PREVIOUS_CONFIG_VERSION_KEY = (
  "managed_runtime_previous_config_version"
)
MANAGED_RUNTIME_DESIRED_ENABLED_KEY = "managed_runtime_desired_enabled"
AVAILABLE_NOW = "AVAILABLE_NOW"
UNTIL_SNAPSHOT_CLEARED = "UNTIL_SNAPSHOT_CLEARED"
UNALLOCATED_ONLY = "UNALLOCATED_ONLY"
REPLACE_CANCELLABLE = "REPLACE_CANCELLABLE"
TERMINAL_PLAN_STATUSES = {"COMPLETED", "CANCELLED"}
CAPACITY_READY = "READY"
CAPACITY_RECONCILE_REQUIRED = "RECONCILE_REQUIRED"
TERMINAL_PENDING_ORDER_LIFECYCLE_STATUSES = frozenset(
  {"REJECTED", "CANCELLED", "EXPIRED", "RECONCILED_ZERO_FILL"}
)
_T_TRADE_AUTHORIZATION_REDERIVATION_LIMIT = 500
_T_TRADE_AUTHORIZATION_DEFERRED_CODES = frozenset(
  {
    "EXIT_PLAN_CAPACITY_RECONCILIATION_REQUIRED",
    "T_TRADE_ENTRY_FILL_SCOPE_MISMATCH",
    "T_TRADE_EXIT_COMPETING_SELL_EXISTS",
    "T_TRADE_EXIT_SAFETY_SNAPSHOT_UNAVAILABLE",
  }
)


@dataclass(frozen=True)
class ActiveRuntimeExitPlanOwnerAuditFailure:
  """One durable plan whose exact Engine execution owner is unavailable."""

  plan_id: str
  strategy_run_id: str
  account_id: str
  owner_kind: str
  reason_code: str
  message: str
  stage: str

  def to_dict(self) -> dict[str, str]:
    return {
      "planId": self.plan_id,
      "strategyRunId": self.strategy_run_id,
      "accountId": self.account_id,
      "ownerKind": self.owner_kind,
      "reasonCode": self.reason_code,
      "message": self.message,
      "stage": self.stage,
    }


class ActiveRuntimeExitPlanOwnerAuditError(RuntimeError):
  """Structured fail-closed owner-audit failure consumed by Engine supervision."""

  code = "ACTIVE_RUNTIME_EXIT_PLAN_OWNER_AUDIT_FAILED"

  def __init__(
    self,
    failures: Iterable[ActiveRuntimeExitPlanOwnerAuditFailure],
  ) -> None:
    self.failures = tuple(failures)
    if not self.failures:
      raise ValueError("owner audit error requires at least one failure")
    summary = "; ".join(
      (
        f"{item.plan_id}:{item.strategy_run_id}:"
        f"{item.reason_code}:{item.message}"
      )
      for item in self.failures
    )
    super().__init__(f"{self.code}: {summary}")

  @property
  def account_ids(self) -> tuple[str, ...]:
    return tuple(
      dict.fromkeys(item.account_id for item in self.failures if item.account_id)
    )

  def to_dict(self) -> dict[str, Any]:
    return {
      "reasonCode": self.code,
      "failures": [item.to_dict() for item in self.failures],
      "accountIds": list(self.account_ids),
    }


def _is_sticky_exit_plan_error(value: Any) -> bool:
  return is_sticky_exit_plan_error(value)

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


def is_monitor_owned_exit_plan(record: AutoExitPlanRecord) -> bool:
  """Return whether the plan is positively assigned to the global monitor."""

  return durable_exit_plan_owner_kind(record) == MONITOR_OWNER


def _has_authoritative_zero_fill_proof(
  status: str,
  metadata: Mapping[str, Any],
) -> bool:
  source = str(metadata.get("execution_terminal_source") or "").strip().upper()
  normalized_status = str(status or "").strip().upper()
  if source in {
    LOCAL_OUTBOX_EXPIRED_ZERO_FILL_SOURCE,
    LOCAL_AGENT_PRE_EXECUTION_ZERO_FILL_SOURCE,
  }:
    return normalized_status == "RECONCILED_ZERO_FILL"
  if source in {LOCAL_PRE_BROKER_ZERO_FILL_SOURCE, "LOCAL_OUTBOX_CANCEL"}:
    return True
  return normalized_status == "RECONCILED_ZERO_FILL" and isinstance(
    metadata.get("qmt_zero_fill_reconciliation"), Mapping
  )


def _requires_terminal_pending_order(metadata: Mapping[str, Any]) -> bool:
  return str(metadata.get("execution_terminal_source") or "").strip().upper() in {
    LOCAL_OUTBOX_EXPIRED_ZERO_FILL_SOURCE,
    LOCAL_AGENT_PRE_EXECUTION_ZERO_FILL_SOURCE,
  }


def _terminal_pending_order_supports_zero_fill(
  pending: PendingTradeOrder,
  metadata: Mapping[str, Any],
) -> bool:
  status = str(pending.status or "").strip().upper()
  source = str(metadata.get("execution_terminal_source") or "").strip().upper()
  if source == LOCAL_OUTBOX_EXPIRED_ZERO_FILL_SOURCE:
    return status == "EXPIRED" and not str(pending.broker_order_id or "")
  if source == LOCAL_AGENT_PRE_EXECUTION_ZERO_FILL_SOURCE:
    return status in {"REJECTED", "EXPIRED"} and not str(
      pending.broker_order_id or ""
    )
  return status in TERMINAL_PENDING_ORDER_LIFECYCLE_STATUSES


def _is_exact_exit_plan_intent(
  record: AutoExitPlanRecord,
  intent: TradeIntentRecord,
  *,
  expected_intent_id: str,
  expected_strategy_run_id: str,
) -> bool:
  """Verify that one durable SELL belongs to exactly one exit plan owner."""

  plan_id = str(record.plan_id or "")
  metadata = dict(intent.intent_metadata or {})
  return bool(
    str(intent.id or "") == str(expected_intent_id or "")
    and str(intent.owner_type or "").upper() == "EXIT_PLAN"
    and str(intent.owner_id or "") == plan_id
    and str(metadata.get("owner_type") or "").upper() == "EXIT_PLAN"
    and str(metadata.get("owner_id") or "") == plan_id
    and str(metadata.get("exit_plan_id") or "") == plan_id
    and str(intent.strategy_run_id or "") == str(expected_strategy_run_id or "")
    and str(intent.account_id or "") == str(record.account_id or "")
    and str(intent.instrument_code or "").upper()
    == str(record.instrument_code or "").upper()
    and str(intent.direction or "").upper() == "SELL"
  )


def normalize_dynamic_policy(value: Optional[Mapping[str, Any]]) -> dict[str, Any]:
  policy = dict(BALANCED_DYNAMIC_POLICY)
  policy.update(dict(value or {}))
  return policy


class AutoExitPlanService:
  def __init__(self, runtime_manager: Any = None) -> None:
    self._runtime_manager = runtime_manager
    self._managed_runtime = (
      ManagedPlanRuntimeService(runtime_manager)
      if runtime_manager is not None
      else None
    )

  def _strategy_owner_kind(self, record: AutoExitPlanRecord) -> str:
    """Resolve an execution owner from the live runtime and durable source."""

    run_id = str(getattr(record, "strategy_run_id", None) or "").strip()
    source_type = str(getattr(record, "source_type", None) or "").strip().upper()
    durable_owner = durable_exit_plan_owner_kind(record)
    if durable_owner == INVALID_OWNER:
      if not run_id and source_type in RUNTIME_EXIT_PLAN_SOURCE_TYPES:
        raise RuntimeError("运行内退出计划缺少 StrategyRun 所有者")
      if run_id and source_type in MONITOR_EXIT_PLAN_SOURCE_TYPES:
        raise RuntimeError("人工计划仍绑定旧 StrategyRun，必须先完成迁移")
      if (
        run_id
        and source_type in RUNTIME_EXIT_PLAN_SOURCE_TYPES
        and has_managed_runtime_command_marker(record)
      ):
        raise RuntimeError("运行内退出计划不得携带独立卖出托管命令")
      raise RuntimeError("退出计划持久化身份不一致或没有合法执行所有者")
    if durable_owner == MONITOR_OWNER:
      return MONITOR_OWNER
    if self._runtime_manager is None:
      raise RuntimeError("退出计划所属 StrategyRun 当前不可用")
    runtime = self._runtime_manager.get_run(run_id)
    if runtime is None:
      raise RuntimeError("退出计划所属 StrategyRun 尚未恢复")
    strategy_class = getattr(runtime, "strategy_class", None)
    owns_runtime_book = bool(
      getattr(runtime.strategy, "OWNS_RUNTIME_EXIT_PLAN_BOOK", False)
      if getattr(runtime, "strategy", None) is not None
      else False
    ) or bool(getattr(strategy_class, "OWNS_RUNTIME_EXIT_PLAN_BOOK", False))
    if durable_owner == RUNTIME_BOOK_OWNER:
      if not owns_runtime_book:
        raise RuntimeError("退出计划绑定的 StrategyRun 不拥有 Engine ExitPlanBook")
      if source_type not in RUNTIME_EXIT_PLAN_SOURCE_TYPES:
        raise RuntimeError("Engine ExitPlanBook 收到不受支持的计划来源")
      return RUNTIME_BOOK_OWNER
    raise RuntimeError("退出计划绑定了未知执行所有者")

  async def validate_exit_plan_sell_approval(
    self,
    *,
    plan_id: str,
    intent_id: str,
    account_id: str,
    approval_audit: Optional[Mapping[str, Any]],
  ) -> str:
    async with AsyncSessionLocal() as db:
      return await validate_consumed_exit_plan_sell_challenge(
        db,
        plan_id=plan_id,
        intent_id=intent_id,
        account_id=account_id,
        approval_audit=approval_audit,
      )

  async def _set_managed_runtime_enabled(
    self,
    record: AutoExitPlanRecord,
    enabled: bool,
  ) -> None:
    if self._strategy_owner_kind(record) != "MANAGED_EXIT_STRATEGY":
      raise RuntimeError("退出计划不属于独立卖出策略")
    run_id = str(record.strategy_run_id)
    runtime = self._runtime_manager.get_run(run_id)
    parameters = {
      **dict(runtime.context.parameters or {}),
      EXIT_PLAN_ENABLED_KEY: bool(enabled),
    }
    await self._runtime_manager.update_run_parameters(run_id, parameters)
    if enabled:
      status = str(
        getattr(
          getattr(runtime, "status", None),
          "value",
          getattr(runtime, "status", ""),
        )
        or ""
      ).upper()
      if not managed_runtime_has_live_consumer(runtime):
        progressed = (
          bool(await self._runtime_manager.resume_strategy(run_id))
          if status == "PAUSED"
          else bool(await self._runtime_manager.start_strategy(run_id))
        )
        runtime = self._runtime_manager.get_run(run_id)
        if not progressed or not managed_runtime_has_live_consumer(runtime):
          raise RuntimeError("卖出计划 StrategyRun 恢复后缺少活动消费任务")
      if self._managed_runtime is not None:
        await self._managed_runtime.set_status(record.plan_id, "RUNNING")
    else:
      status = str(
        getattr(
          getattr(runtime, "status", None),
          "value",
          getattr(runtime, "status", ""),
        )
        or ""
      ).upper()
      if status == "RUNNING" and not await self._runtime_manager.pause_strategy(
        run_id
      ):
        raise RuntimeError("卖出计划 StrategyRun 暂停失败")
      if status not in {"RUNNING", "PENDING", "PAUSED"}:
        raise RuntimeError("卖出计划 StrategyRun 暂停前状态不一致")
      if self._managed_runtime is not None:
        await self._managed_runtime.set_status(record.plan_id, "PAUSED")

  async def _strategy_template_id(self) -> int:
    async with AsyncSessionLocal() as db:
      strategy = await StrategyRepository(db).find_by_class_name(
        MANAGED_EXIT_STRATEGY_CLASS_NAME
      )
      if strategy is None:
        raise ValueError("卖出托管策略尚未注册，请重启 Engine 后重试")
      return int(strategy.id)

  @staticmethod
  def _managed_config_snapshot(plan: ExitPlan) -> dict[str, Any]:
    """Return user configuration without the derived StrategyRun owner."""

    return {**plan.template.to_dict(), "run_id": ""}

  @staticmethod
  def _bind_plan_to_strategy_run(plan: ExitPlan, run_id: str) -> ExitPlan:
    bound = ExitPlan.from_dict(plan.to_dict())
    bound.template = ExitPlanTemplate.from_dict(
      {**bound.template.to_dict(), "run_id": str(run_id or "").strip()}
    )
    return bound

  @staticmethod
  def _managed_parameters(
    record: AutoExitPlanRecord,
    plan: ExitPlan,
  ) -> dict[str, Any]:
    return {
      MANAGED_EXIT_PLAN_KEY: AutoExitPlanService._managed_config_snapshot(plan),
      EXIT_PLAN_ENABLED_KEY: bool(record.enabled),
      "account_id": str(record.account_id),
      "instrument_code": str(record.instrument_code),
      "initial_protected_volume": int(plan.entry_filled_volume or 0),
      "initial_entry_avg_price": float(plan.entry_avg_price or 0.0),
      "initial_entry_time": str(plan.entry_trade_date or ""),
    }

  async def _create_managed_runtime(
    self,
    record: AutoExitPlanRecord,
    plan: ExitPlan,
    *,
    command_id: str = "",
  ) -> None:
    if self._managed_runtime is None:
      return
    strategy_id = await self._strategy_template_id()
    desired_enabled = bool(record.enabled)
    paused_plan = ExitPlan.from_dict(plan.to_dict())
    if paused_plan.status not in {ExitPlanStatus.COMPLETED, ExitPlanStatus.CANCELLED}:
      paused_plan.status = ExitPlanStatus.PAUSED
    run_id, _ = await self._managed_runtime.create(
      plan_id=str(record.plan_id),
      plan_kind="EXIT",
      account_id=str(record.account_id),
      instrument_code=str(record.instrument_code),
      config_snapshot=self._managed_config_snapshot(plan),
      parameters=self._managed_parameters(record, plan),
      strategy_id=strategy_id,
      strategy_class=AshareManagedExitPlanStrategy,
      mode=(
        StrategyRunMode.LIVE
        if str(record.execution_mode or "").lower() == "live"
        else StrategyRunMode.PAPER
      ),
      name=f"卖出托管-{record.instrument_code}",
      # Publish the durable owner before the runtime is allowed to consume a
      # tick.  Starting here would expose a create -> bind window in which a
      # dedicated strategy could route a SELL while the plan still looked
      # monitor-owned.
      start_immediately=False,
      state_migration_policy="INITIAL_EXIT_PLAN_STATE",
      initial_state={MANAGED_EXIT_RUNTIME_KEY: plan.to_dict()},
      command_id=str(command_id or "") or None,
    )
    bound_plan = self._bind_plan_to_strategy_run(plan, run_id)
    bound_paused_plan = self._bind_plan_to_strategy_run(paused_plan, run_id)
    bind_kwargs: dict[str, Any] = {
      "plan": bound_plan if desired_enabled else bound_paused_plan,
      "enabled": desired_enabled,
    }
    if command_id:
      bind_kwargs["command_id"] = command_id
    await self._bind_strategy_run(record.plan_id, run_id, **bind_kwargs)
    record.strategy_run_id = run_id
    record.enabled = desired_enabled
    record.plan_state = (
      bound_plan if desired_enabled else bound_paused_plan
    ).to_dict()
    try:
      if desired_enabled:
        await self._set_managed_runtime_enabled(record, True)
      await self._finalize_managed_runtime_binding(
        record.plan_id,
        run_id,
        plan=bound_plan if desired_enabled else bound_paused_plan,
        enabled=desired_enabled,
        command_id=command_id,
      )
    except Exception as exc:
      await self._mark_managed_runtime_failed(
        record.plan_id,
        run_id,
        plan=bound_paused_plan,
        error=str(exc),
      )
      raise

  async def _revise_managed_runtime(
    self,
    record: AutoExitPlanRecord,
    plan: ExitPlan,
    *,
    expected_version: int,
    command_id: str = "",
  ) -> None:
    if self._managed_runtime is None:
      return
    strategy_id = await self._strategy_template_id()
    desired_enabled = bool(record.enabled)
    paused_plan = ExitPlan.from_dict(plan.to_dict())
    if paused_plan.status not in {ExitPlanStatus.COMPLETED, ExitPlanStatus.CANCELLED}:
      paused_plan.status = ExitPlanStatus.PAUSED
    run_id, _, _ = await self._managed_runtime.revise(
      plan_id=str(record.plan_id),
      expected_version=expected_version,
      config_snapshot=self._managed_config_snapshot(plan),
      parameters=self._managed_parameters(record, plan),
      strategy_id=strategy_id,
      strategy_class=AshareManagedExitPlanStrategy,
      mode=(
        StrategyRunMode.LIVE
        if str(record.execution_mode or "").lower() == "live"
        else StrategyRunMode.PAPER
      ),
      name=f"卖出托管-{record.instrument_code}-v{plan.template.config_version}",
      # The new StrategyRun must not start until AutoExitPlan publishes the
      # new owner.  Otherwise startup cannot load its canonical plan and a
      # tick could cross the old-run/new-run ownership gap.
      start_immediately=False,
      state_migration_policy="CARRY_EXIT_ALGORITHM_STATE",
      initial_state={MANAGED_EXIT_RUNTIME_KEY: plan.to_dict()},
      command_id=str(command_id or "") or None,
    )
    bound_plan = self._bind_plan_to_strategy_run(plan, run_id)
    bound_paused_plan = self._bind_plan_to_strategy_run(paused_plan, run_id)
    bind_kwargs: dict[str, Any] = {
      "plan": bound_plan if desired_enabled else bound_paused_plan,
      "enabled": desired_enabled,
    }
    if command_id:
      bind_kwargs["command_id"] = command_id
    await self._bind_strategy_run(record.plan_id, run_id, **bind_kwargs)
    record.strategy_run_id = run_id
    record.enabled = desired_enabled
    record.plan_state = (
      bound_plan if desired_enabled else bound_paused_plan
    ).to_dict()
    try:
      if desired_enabled:
        await self._set_managed_runtime_enabled(record, True)
      await self._finalize_managed_runtime_binding(
        record.plan_id,
        run_id,
        plan=bound_plan if desired_enabled else bound_paused_plan,
        enabled=desired_enabled,
        command_id=command_id,
      )
    except Exception as exc:
      await self._mark_managed_runtime_failed(
        record.plan_id,
        run_id,
        plan=bound_paused_plan,
        error=str(exc),
      )
      raise

  async def _bind_strategy_run(
    self,
    plan_id: str,
    run_id: str,
    *,
    plan: Optional[ExitPlan] = None,
    enabled: Optional[bool] = None,
    command_id: str = "",
  ) -> None:
    async with AsyncSessionLocal() as db:
      record = await AutoExitPlanRepository(db).find_by_id(plan_id, for_update=True)
      if record is None:
        raise ValueError("退出计划在 StrategyRun 创建后不存在")
      if plan is not None and int(record.config_version or 0) != int(
        plan.template.config_version or 0
      ):
        raise AutoExitPlanConcurrencyError(
          "退出计划配置已变化，拒绝绑定过期 StrategyRun"
        )
      metadata = dict(
        ExitPlan.from_dict(dict(record.plan_state or {})).template.metadata or {}
      )
      if command_id and str(metadata.get(MANAGED_RUNTIME_COMMAND_ID_KEY) or "") != (
        str(command_id)
      ):
        raise AutoExitPlanConcurrencyError(
          "退出计划命令已变化，拒绝绑定过期 StrategyRun"
        )
      duplicate_owner = await db.scalar(
        select(AutoExitPlanRecord.plan_id)
        .where(AutoExitPlanRecord.strategy_run_id == run_id)
        .where(AutoExitPlanRecord.plan_id != plan_id)
        .limit(1)
      )
      if duplicate_owner is not None:
        raise RuntimeError("独立卖出 StrategyRun 已绑定其他退出计划")
      record.strategy_run_id = run_id
      if plan is not None:
        bound_plan = self._bind_plan_to_strategy_run(plan, run_id)
        binding_pending = bool(command_id) or bool(
          metadata.get(MANAGED_RUNTIME_COMMAND_ID_KEY)
        )
        if binding_pending and bound_plan.status not in {
          ExitPlanStatus.COMPLETED,
          ExitPlanStatus.CANCELLED,
        }:
          # Publishing the owner and exposing an active plan are separate
          # durable steps.  Keep the Auto plan paused until the exact runtime
          # has started, so a crash after this commit remains discoverable and
          # can never leave ACTIVE/enabled with no consumer.
          bound_plan.status = ExitPlanStatus.PAUSED
        elif enabled is False and bound_plan.status not in {
          ExitPlanStatus.COMPLETED,
          ExitPlanStatus.CANCELLED,
        }:
          bound_plan.status = ExitPlanStatus.PAUSED
        elif enabled and bound_plan.status == ExitPlanStatus.PAUSED:
          bound_plan.status = ExitPlanStatus.ACTIVE
        self._sync_record(record, bound_plan)
      if command_id:
        record.last_error = (
          f"{MANAGED_RUNTIME_BINDING_PENDING}:{command_id}:"
          f"v{int(record.config_version or 0)}"
        )
      elif not metadata.get(MANAGED_RUNTIME_COMMAND_ID_KEY):
        record.last_error = None
      await self._append_event(
        db,
        business_key=f"plan-owner-bound:{plan_id}:{run_id}",
        plan_id=plan_id,
        event_type="PLAN_OWNER_BOUND",
        payload={"strategy_run_id": run_id, "enabled": bool(record.enabled)},
      )
      await db.commit()

  async def _finalize_managed_runtime_binding(
    self,
    plan_id: str,
    run_id: str,
    *,
    plan: ExitPlan,
    enabled: bool,
    command_id: str,
  ) -> None:
    """Expose the plan only after its exact owner reached the desired state."""

    runtime = self._runtime_manager.get_run(run_id) if self._runtime_manager else None
    if runtime is None:
      raise RuntimeError("卖出计划 StrategyRun 在绑定完成前丢失")
    runtime_status = str(
      getattr(
        getattr(runtime, "status", None),
        "value",
        getattr(runtime, "status", ""),
      )
      or ""
    ).upper()
    if enabled:
      if not managed_runtime_has_live_consumer(runtime):
        raise RuntimeError("卖出计划 StrategyRun 未确认活动消费任务")
    elif runtime_status not in {"PENDING", "PAUSED"}:
      raise RuntimeError("暂停卖出计划 StrategyRun 状态不一致")

    async with AsyncSessionLocal() as db:
      record = await AutoExitPlanRepository(db).find_by_id(plan_id, for_update=True)
      if record is None or str(record.strategy_run_id or "") != str(run_id):
        raise RuntimeError("卖出计划最终状态与 StrategyRun 所有权不一致")
      if int(record.config_version or 0) != int(plan.template.config_version or 0):
        raise AutoExitPlanConcurrencyError(
          "退出计划配置已变化，拒绝完成过期 StrategyRun 绑定"
        )
      persisted = ExitPlan.from_dict(dict(record.plan_state or {}))
      metadata = dict(persisted.template.metadata or {})
      if command_id and str(metadata.get(MANAGED_RUNTIME_COMMAND_ID_KEY) or "") != (
        str(command_id)
      ):
        raise AutoExitPlanConcurrencyError(
          "退出计划命令已变化，拒绝完成旧 StrategyRun 绑定"
        )
      # Starting the consumer and publishing ACTIVE cross an await boundary.
      # The consumer may already have persisted a fill, a new pending intent,
      # or a fail-closed ERROR.  Finalize from the row locked *now*, never from
      # the command-time snapshot passed by the caller.
      finalized_plan = persisted
      if enabled and _is_sticky_exit_plan_error(finalized_plan.error_message):
        raise ValueError(
          "EXIT_PLAN_RECONCILIATION_REQUIRED:退出计划必须先完成券商事实对账"
        )
      if finalized_plan.status == ExitPlanStatus.ERROR:
        raise RuntimeError(
          str(finalized_plan.error_message or "卖出计划启动期间进入错误状态")
        )
      if enabled and finalized_plan.status == ExitPlanStatus.PAUSED:
        finalized_plan.status = ExitPlanStatus.ACTIVE
      elif not enabled and finalized_plan.status not in {
        ExitPlanStatus.COMPLETED,
        ExitPlanStatus.CANCELLED,
        ExitPlanStatus.ERROR,
      }:
        finalized_plan.status = ExitPlanStatus.PAUSED
      self._sync_record(record, finalized_plan)
      binding_marker = (
        f"{MANAGED_RUNTIME_BINDING_PENDING}:{command_id}:"
        f"v{int(record.config_version or 0)}"
      )
      if record.last_error == binding_marker:
        record.last_error = None
      await self._append_event(
        db,
        business_key=(
          f"managed-runtime-binding-finalized:{plan_id}:{run_id}:"
          f"{int(record.config_version or 0)}:{command_id}:{int(bool(enabled))}"
        ),
        plan_id=plan_id,
        event_type="MANAGED_RUNTIME_BINDING_FINALIZED",
        payload={
          "command_id": command_id,
          "config_version": int(record.config_version or 0),
          "strategy_run_id": run_id,
          "enabled": bool(enabled),
        },
      )
      await db.commit()

  async def _finalize_managed_runtime_enabled(
    self,
    record: AutoExitPlanRecord,
    plan: ExitPlan,
    *,
    enabled: bool,
    command_id: str,
  ) -> AutoExitPlanRecord:
    run_id = str(record.strategy_run_id or "")
    runtime = self._runtime_manager.get_run(run_id) if self._runtime_manager else None
    if runtime is None:
      raise RuntimeError("卖出计划启停时 StrategyRun 丢失")
    runtime_status = str(
      getattr(
        getattr(runtime, "status", None),
        "value",
        getattr(runtime, "status", ""),
      )
      or ""
    ).upper()
    if enabled and not managed_runtime_has_live_consumer(runtime):
      raise RuntimeError("卖出计划启用时 StrategyRun 未确认活动消费任务")
    if not enabled and runtime_status not in {"PENDING", "PAUSED"}:
      raise RuntimeError("卖出计划暂停时 StrategyRun 状态不一致")
    async with AsyncSessionLocal() as db:
      current = await AutoExitPlanRepository(db).find_by_id(
        record.plan_id,
        for_update=True,
      )
      if current is None or str(current.strategy_run_id or "") != run_id:
        raise RuntimeError("卖出计划启停与 StrategyRun 所有权不一致")
      marker = self._parse_managed_enable_marker(current.last_error)
      if (
        marker is None
        or marker["command_id"] != command_id
        or marker["enabled"] is not bool(enabled)
        or int(marker["config_version"]) != int(current.config_version or 0)
      ):
        raise AutoExitPlanConcurrencyError("卖出计划启停命令已变化")
      finalized_plan = ExitPlan.from_dict(dict(current.plan_state or {}))
      if int(finalized_plan.template.config_version or 0) != int(
        current.config_version or 0
      ):
        raise AutoExitPlanConcurrencyError("卖出计划启停配置已变化")
      if enabled and _is_sticky_exit_plan_error(finalized_plan.error_message):
        raise ValueError(
          "EXIT_PLAN_RECONCILIATION_REQUIRED:退出计划必须先完成券商事实对账"
        )
      if finalized_plan.status == ExitPlanStatus.ERROR:
        raise RuntimeError(
          str(finalized_plan.error_message or "卖出计划启停期间进入错误状态")
        )
      if enabled and finalized_plan.status == ExitPlanStatus.PAUSED:
        finalized_plan.status = ExitPlanStatus.ACTIVE
      elif not enabled and finalized_plan.status not in {
        ExitPlanStatus.COMPLETED,
        ExitPlanStatus.CANCELLED,
        ExitPlanStatus.ERROR,
      }:
        finalized_plan.status = ExitPlanStatus.PAUSED
      self._sync_record(current, finalized_plan)
      if self._parse_managed_enable_marker(current.last_error) is not None:
        current.last_error = None
      await self._append_event(
        db,
        business_key=(
          f"managed-runtime-enable-finalized:{current.plan_id}:"
          f"{current.config_version}:{command_id}"
        ),
        plan_id=current.plan_id,
        event_type="MANAGED_RUNTIME_ENABLE_FINALIZED",
        payload={
          "command_id": command_id,
          "config_version": int(current.config_version or 0),
          "enabled": bool(enabled),
          "strategy_run_id": run_id,
        },
      )
      await db.commit()
      await db.refresh(current)
      return current

  async def _managed_enable_finalized_payload(
    self,
    *,
    plan_id: str,
    command_id: str,
  ) -> Optional[dict[str, Any]]:
    async with AsyncSessionLocal() as db:
      events = list(
        (
          await db.execute(
            select(AutoExitPlanEvent)
            .where(AutoExitPlanEvent.plan_id == plan_id)
            .where(
              AutoExitPlanEvent.event_type
              == "MANAGED_RUNTIME_ENABLE_FINALIZED"
            )
            .order_by(AutoExitPlanEvent.created_at.desc())
            .limit(100)
          )
        )
        .scalars()
        .all()
      )
    for event in events:
      payload = dict(event.payload or {})
      if str(payload.get("command_id") or "") == command_id:
        return payload
    return None

  async def _set_managed_exit_strategy_enabled(
    self,
    plan_id: str,
    enabled: bool,
    *,
    account_id: Optional[str],
    config_version: Optional[int],
    command_id: str,
  ) -> AutoExitPlanRecord:
    normalized_command_id = self._managed_command_id(command_id, required=True)
    finalized = await self._managed_enable_finalized_payload(
      plan_id=plan_id,
      command_id=normalized_command_id,
    )
    if finalized is not None:
      if bool(finalized.get("enabled")) is not bool(enabled):
        raise ValueError("EXIT_COMMAND_REPLAY_CONFLICT:启停命令载荷不一致")
      refreshed = await self._load_manual_plan_record(plan_id)
      if refreshed is None:
        raise RuntimeError("卖出计划启停重放时计划不存在")
      if account_id and str(refreshed.account_id or "") != str(account_id):
        raise ValueError("EXIT_COMMAND_REPLAY_CONFLICT:启停命令账户不一致")
      if config_version is not None and int(finalized.get("config_version") or 0) != (
        int(config_version)
      ):
        raise ValueError("EXIT_COMMAND_REPLAY_CONFLICT:启停命令版本不一致")
      return refreshed

    async with AsyncSessionLocal() as db:
      current = await AutoExitPlanRepository(db).find_by_id(plan_id, for_update=True)
      if current is None:
        raise ValueError("退出计划不存在")
      if account_id and current.account_id != account_id:
        raise ValueError("退出计划不属于当前账户")
      if config_version is not None and int(current.config_version) != int(
        config_version
      ):
        raise ValueError(f"CONFIG_VERSION_CONFLICT: current={current.config_version}")
      plan = ExitPlan.from_dict(dict(current.plan_state or {}))
      if enabled and _is_sticky_exit_plan_error(plan.error_message):
        raise ValueError(
          "EXIT_PLAN_RECONCILIATION_REQUIRED:退出计划必须先完成券商事实对账"
        )
      if plan.status in {ExitPlanStatus.COMPLETED, ExitPlanStatus.CANCELLED}:
        raise ValueError("终态退出计划不能启停")
      marker = self._parse_managed_enable_marker(current.last_error)
      if marker is not None:
        if (
          marker["command_id"] != normalized_command_id
          or marker["enabled"] is not bool(enabled)
          or int(marker["config_version"]) != int(current.config_version or 0)
        ):
          raise AutoExitPlanConcurrencyError("卖出计划存在其他待恢复启停命令")
      else:
        if current.last_error:
          raise RuntimeError("卖出计划处于错误状态，拒绝启停")
        if not enabled and (
          plan.status == ExitPlanStatus.EXIT_PENDING or plan.pending_order_id
        ):
          raise ValueError("已有卖出委托待成交，暂不能暂停")
        # The durable first phase is always paused/disabled.  Only a confirmed
        # runtime transition may expose ACTIVE in the final transaction.
        plan.status = ExitPlanStatus.PAUSED
        self._sync_record(current, plan)
        clear_exact_auto_exit_authorization(current, bump_state_version=False)
        current.last_error = self._managed_enable_marker(
          normalized_command_id,
          enabled=enabled,
          config_version=int(current.config_version or 0),
        )
        await self._append_event(
          db,
          business_key=(
            f"managed-runtime-enable-pending:{plan_id}:"
            f"{current.config_version}:{normalized_command_id}"
          ),
          plan_id=plan_id,
          event_type="MANAGED_RUNTIME_ENABLE_PENDING",
          payload={
            "command_id": normalized_command_id,
            "config_version": int(current.config_version or 0),
            "enabled": bool(enabled),
            "strategy_run_id": str(current.strategy_run_id or ""),
          },
        )
        await db.commit()
        await db.refresh(current)
      pending_record = current
      pending_plan = plan
    pending_record.enabled = bool(enabled)
    try:
      await self._set_managed_runtime_enabled(pending_record, enabled)
      return await self._finalize_managed_runtime_enabled(
        pending_record,
        pending_plan,
        enabled=enabled,
        command_id=normalized_command_id,
      )
    except Exception as exc:
      await self._mark_managed_runtime_failed(
        pending_record.plan_id,
        str(pending_record.strategy_run_id or ""),
        plan=pending_plan,
        error=str(exc),
      )
      raise

  async def _mark_managed_runtime_failed(
    self,
    plan_id: str,
    run_id: str,
    *,
    plan: ExitPlan,
    error: str,
  ) -> None:
    """Keep a failed dedicated owner explicit, paused, and monitor-ineligible."""

    message = str(error or "StrategyRun 启动失败")[:2000]
    async with AsyncSessionLocal() as db:
      record = await AutoExitPlanRepository(db).find_by_id(plan_id, for_update=True)
      if record is None or str(record.strategy_run_id or "") != str(run_id):
        raise RuntimeError("卖出计划失败状态与 StrategyRun 所有权不一致")
      # The failure handler is itself an asynchronous compensation path.  It
      # must preserve any fills/pending ownership/safety facts committed after
      # the initiating command snapshot.
      failed_plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      clear_exact_auto_exit_authorization(record, bump_state_version=False)
      if failed_plan.status not in {
        ExitPlanStatus.COMPLETED,
        ExitPlanStatus.CANCELLED,
      }:
        failed_plan.status = ExitPlanStatus.ERROR
        if not _is_sticky_exit_plan_error(failed_plan.error_message):
          failed_plan.error_message = message
      self._sync_record(record, failed_plan)
      record.enabled = False
      record.last_error = (
        str(failed_plan.error_message or "")
        if failed_plan.status == ExitPlanStatus.ERROR
        else message
      )
      await self._append_event(
        db,
        business_key=f"managed-runtime-failed:{plan_id}:{run_id}",
        plan_id=plan_id,
        event_type="MANAGED_RUNTIME_FAILED",
        payload={"strategy_run_id": run_id, "error": message},
      )
      await db.commit()
    if self._managed_runtime is not None:
      await self._managed_runtime.set_status(plan_id, "ERROR", error=message)
    runtime = self._runtime_manager.get_run(run_id) if self._runtime_manager else None
    if runtime is not None:
      try:
        await self._runtime_manager.pause_strategy(run_id)
      except Exception:
        # The durable disabled/ERROR state is the hard execution gate.  A
        # best-effort pause failure must not hide the original convergence
        # error or re-enable autonomous SELL persistence.
        pass

  async def sync_managed_runtime_state(
    self,
    *,
    strategy_run_id: str,
    plan_state: Mapping[str, Any],
    evaluated_at: Optional[datetime] = None,
  ) -> None:
    """Project strategy-owned exit algorithm state into the sell workspace."""

    if not strategy_run_id or not plan_state:
      return
    plan = ExitPlan.from_dict(dict(plan_state))
    async with AsyncSessionLocal() as db:
      record = await db.scalar(
        select(AutoExitPlanRecord)
        .where(AutoExitPlanRecord.strategy_run_id == strategy_run_id)
        .with_for_update()
      )
      if record is None or str(record.plan_id) != plan.plan_id:
        raise RuntimeError("独立卖出策略与退出计划所有权不一致")
      if durable_exit_plan_owner_kind(record) != MANAGED_EXIT_STRATEGY_OWNER:
        raise RuntimeError("独立卖出策略与退出计划持久化所有权不一致")
      persisted = ExitPlan.from_dict(dict(record.plan_state or {}))
      if (
        int(record.config_version or 0) != int(plan.template.config_version or 0)
        or persisted.template.to_dict() != plan.template.to_dict()
      ):
        raise AutoExitPlanConcurrencyError("退出计划配置已变化，拒绝旧运行时状态")
      if str(record.status or "").upper() in TERMINAL_PLAN_STATUSES:
        if dict(record.plan_state or {}) == plan.to_dict():
          return
        raise AutoExitPlanConcurrencyError("退出计划已终止，拒绝旧运行时状态")
      if not bool(record.enabled) and plan.status != ExitPlanStatus.PAUSED:
        raise AutoExitPlanConcurrencyError("退出计划已暂停，拒绝活动运行时状态")
      self._sync_record(record, plan, evaluated_at=evaluated_at)
      await self._sync_source_order(db, record, plan, checked_at=evaluated_at)
      await db.commit()

  async def load_managed_runtime_plan(
    self,
    *,
    strategy_run_id: str,
    expected_plan_id: str,
    expected_config_version: Optional[int] = None,
  ) -> tuple[dict[str, Any], int]:
    """Load the canonical plan aggregate for a dedicated managed-exit run."""

    async with AsyncSessionLocal() as db:
      record = await self._load_exact_managed_runtime_record(
        db,
        strategy_run_id=strategy_run_id,
        expected_plan_id=expected_plan_id,
        expected_config_version=expected_config_version,
      )
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      if (
        plan.plan_id != str(record.plan_id)
        or int(plan.template.config_version or 0)
        != int(record.config_version or 0)
      ):
        raise RuntimeError("独立卖出策略与退出计划持久化绑定不一致")
      return dict(record.plan_state or {}), max(1, int(record.state_version or 1))

  @staticmethod
  async def _load_exact_managed_runtime_record(
    db: Any,
    *,
    strategy_run_id: str,
    expected_plan_id: str,
    expected_config_version: Optional[int] = None,
    for_update: bool = False,
  ) -> AutoExitPlanRecord:
    """Resolve one plan/run binding and reject ambiguous durable ownership."""

    normalized_run_id = str(strategy_run_id or "").strip()
    normalized_plan_id = str(expected_plan_id or "").strip()
    if not normalized_run_id:
      raise ValueError("策略运行标识不能为空")
    if not normalized_plan_id:
      raise ValueError("预期退出计划标识不能为空")

    bindings_stmt = (
      select(AutoExitPlanRecord)
      .where(AutoExitPlanRecord.strategy_run_id == normalized_run_id)
      .order_by(AutoExitPlanRecord.plan_id)
      .limit(2)
    )
    if for_update:
      bindings_stmt = bindings_stmt.with_for_update()
    bindings = list((await db.execute(bindings_stmt)).scalars().all())
    if not bindings:
      raise RuntimeError("独立卖出策略缺少退出计划绑定")
    if len(bindings) != 1:
      raise RuntimeError("独立卖出 StrategyRun 重复绑定多个退出计划")
    record = bindings[0]
    if (
      str(record.plan_id or "") != normalized_plan_id
      or str(record.strategy_run_id or "") != normalized_run_id
    ):
      raise RuntimeError("独立卖出策略与预期退出计划绑定不一致")
    if str(record.source_type or "").strip().upper() != MANUAL_PLAN_SOURCE:
      raise RuntimeError("独立卖出策略只能恢复 MANUAL_POSITION 计划")
    if not has_managed_runtime_command_marker(record) or not (
      managed_runtime_command_id(record)
    ):
      raise RuntimeError("独立卖出策略缺少有效托管命令绑定")
    if durable_exit_plan_owner_kind(record) != MANAGED_EXIT_STRATEGY_OWNER:
      raise RuntimeError("独立卖出策略与退出计划持久化所有权不一致")
    persisted = ExitPlan.from_dict(dict(record.plan_state or {}))
    binding_pending = str(record.last_error or "").startswith(
      (MANAGED_RUNTIME_BINDING_PENDING, MANAGED_RUNTIME_ENABLE_PENDING)
    )
    if binding_pending and (
      bool(record.enabled)
      or str(record.status or "").upper() != ExitPlanStatus.PAUSED.value
      or persisted.status != ExitPlanStatus.PAUSED
    ):
      raise RuntimeError("待恢复独立卖出计划未保持暂停隔离")
    if expected_config_version is not None:
      try:
        normalized_config_version = int(expected_config_version)
      except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("预期退出计划配置版本无效") from exc
      if normalized_config_version <= 0:
        raise ValueError("预期退出计划配置版本无效")
      if int(record.config_version or 0) != normalized_config_version:
        raise AutoExitPlanConcurrencyError(
          "独立卖出策略与预期退出计划配置版本不一致"
        )
    return record

  async def persist_managed_runtime_transition(
    self,
    *,
    strategy_run_id: str,
    expected_plan_id: str,
    expected_config_version: Optional[int] = None,
    plan_state: Mapping[str, Any],
    expected_state_version: int,
    intent: Any = None,
    evaluated_at: Optional[datetime] = None,
    event_business_key: Optional[str] = None,
  ) -> tuple[dict[str, Any], int]:
    """Atomically persist a dedicated plan transition and its SELL intent."""

    plan = ExitPlan.from_dict(dict(plan_state or {}))
    async with AsyncSessionLocal() as db:
      record = await self._load_exact_managed_runtime_record(
        db,
        strategy_run_id=strategy_run_id,
        expected_plan_id=expected_plan_id,
        expected_config_version=expected_config_version,
        for_update=True,
      )
      normalized_run_id = str(record.strategy_run_id or "")
      if str(record.plan_id) != plan.plan_id:
        raise RuntimeError("独立卖出策略与退出计划所有权不一致")
      persisted = ExitPlan.from_dict(dict(record.plan_state or {}))
      if (
        int(record.config_version or 0) != int(plan.template.config_version or 0)
        or persisted.template.to_dict() != plan.template.to_dict()
      ):
        raise AutoExitPlanConcurrencyError("退出计划配置已变化，必须重新装载")
      if event_business_key and (
        await db.scalar(
          select(AutoExitPlanEvent.event_id)
          .where(
            AutoExitPlanEvent.plan_id == plan.plan_id,
            AutoExitPlanEvent.business_key == str(event_business_key),
          )
          .limit(1)
        )
        is not None
      ):
        # The plan mutation and marker crossed one commit boundary on the
        # prior attempt.  A runtime checkpoint crash may replay a strategy
        # callback that has already mutated its in-memory copy again; return
        # canonical state before checking its now-stale version or payload.
        return dict(record.plan_state or {}), max(
          1,
          int(record.state_version or 1),
        )
      if int(record.state_version or 0) != int(expected_state_version):
        raise AutoExitPlanConcurrencyError("退出计划状态版本已变化，必须重新装载")
      if str(record.status or "").upper() in TERMINAL_PLAN_STATUSES or (
        persisted.status in {ExitPlanStatus.COMPLETED, ExitPlanStatus.CANCELLED}
      ):
        if dict(record.plan_state or {}) == plan.to_dict():
          if event_business_key:
            await self._append_event(
              db,
              business_key=str(event_business_key),
              plan_id=plan.plan_id,
              event_type="MANAGED_EXIT_STATE_UPDATED",
              payload={
                "strategy_run_id": normalized_run_id,
                "state_version": max(1, int(record.state_version or 1)),
                "config_version": int(record.config_version or 0),
                "runtime_event_applied": True,
                "state_changed": False,
              },
            )
            await db.commit()
          return dict(record.plan_state or {}), max(
            1, int(record.state_version or 1)
          )
        raise AutoExitPlanConcurrencyError("退出计划已终止，拒绝旧运行时输出")
      persisted_error = str(persisted.error_message or "")
      incoming_error = str(plan.error_message or "")
      invalidated_intent_id = (
        persisted_error.removeprefix(ZERO_FILL_PROOF_INVALIDATED_PREFIX)
        if persisted_error.startswith(ZERO_FILL_PROOF_INVALIDATED_PREFIX)
        else ""
      )
      mismatched_intent_id = (
        persisted_error.removeprefix(EXIT_FILL_INTENT_MISMATCH_PREFIX).split(
          ":CURRENT_PENDING:", 1
        )[0]
        if persisted_error.startswith(EXIT_FILL_INTENT_MISMATCH_PREFIX)
        else ""
      )
      exact_safety_contradiction_replay = bool(
        (invalidated_intent_id or mismatched_intent_id)
        and plan.status == ExitPlanStatus.ERROR
        and persisted.status == ExitPlanStatus.ERROR
        and incoming_error == persisted_error
        and str(record.last_error or "") == persisted_error
        and (
          (
            invalidated_intent_id
            and invalidated_intent_id
            in set(persisted.reconciled_zero_fill_intent_ids)
            and invalidated_intent_id in set(plan.reconciled_zero_fill_intent_ids)
          )
          or (
            mismatched_intent_id
            and mismatched_intent_id
            != str(persisted.pending_intent_id or "")
            and mismatched_intent_id != str(plan.pending_intent_id or "")
          )
        )
        and plan.template.to_dict() == persisted.template.to_dict()
        and int(plan.exited_volume or 0) >= int(persisted.exited_volume or 0)
        and int(plan.exited_volume or 0) <= int(plan.entry_filled_volume or 0)
        and str(plan.pending_intent_id or "")
        == str(persisted.pending_intent_id or "")
        and str(plan.pending_rule_id or "") == str(persisted.pending_rule_id or "")
      )
      if (
        not bool(record.enabled)
        and plan.status != ExitPlanStatus.PAUSED
        and not exact_safety_contradiction_replay
      ):
        raise AutoExitPlanConcurrencyError("退出计划已暂停，拒绝活动运行时输出")
      previous_pending_intent_id = str(
        persisted.pending_intent_id or ""
      ).strip()
      next_pending_intent_id = str(plan.pending_intent_id or "").strip()
      if (
        next_pending_intent_id
        and next_pending_intent_id != previous_pending_intent_id
        and intent is None
      ):
        raise ValueError("退出计划保留新待提交意图时必须原子持久化同一 SELL 意图")
      if intent is not None:
        self._require_exact_strategy_exit_intent(
          record,
          intent,
          expected_intent_id=next_pending_intent_id,
        )
      stored = await AutoExitPlanRepository(db).compare_and_swap_state(
        plan_id=plan.plan_id,
        expected_state_version=int(expected_state_version),
        plan_state=plan.to_dict(),
        evaluated_at=evaluated_at,
        commit=False,
      )
      await db.refresh(stored)
      next_version = max(1, int(stored.state_version or 1))
      await self._append_event(
        db,
        business_key=(
          str(event_business_key)
          if event_business_key
          else (
            f"managed-runtime-state:{plan.plan_id}:{next_version}:"
            f"{str(plan.pending_intent_id or 'NO_INTENT')}"
          )
        ),
        plan_id=plan.plan_id,
        event_type=(
          "MANAGED_EXIT_INTENT_RESERVED"
          if intent is not None
          else "MANAGED_EXIT_STATE_UPDATED"
        ),
        payload={
          "strategy_run_id": normalized_run_id,
          "state_version": next_version,
          "config_version": int(stored.config_version or 0),
          "intent_id": str(plan.pending_intent_id or "") or None,
          "runtime_event_applied": bool(event_business_key),
        },
      )
      if intent is not None:
        await self._add_strategy_exit_intent(db, stored, intent)
      await self._sync_source_order(db, stored, plan, checked_at=evaluated_at)
      await db.commit()
      return dict(stored.plan_state or {}), next_version

  async def load_strategy_plan_book(
    self,
    *,
    strategy_run_id: str,
    terminal_history_limit: int = 200,
  ) -> tuple[dict[str, Any], dict[str, int]]:
    """Load the canonical PAPER/LIVE ExitPlanBook for one owning runtime."""

    normalized_run_id = str(strategy_run_id or "").strip()
    if not normalized_run_id:
      raise ValueError("策略运行标识不能为空")
    async with AsyncSessionLocal() as db:
      records = await AutoExitPlanRepository(db).find_for_strategy_run(
        normalized_run_id,
        terminal_history_limit=terminal_history_limit,
      )
    plans: dict[str, Any] = {}
    versions: dict[str, int] = {}
    for record in records:
      if durable_exit_plan_owner_kind(record) != RUNTIME_BOOK_OWNER:
        raise ValueError(
          f"strategy exit-plan durable owner mismatch: {record.plan_id}"
        )
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      if (
        plan.plan_id != str(record.plan_id)
        or str(record.strategy_run_id or "") != normalized_run_id
        or str(plan.template.run_id or "") != normalized_run_id
      ):
        raise ValueError(
          f"strategy exit-plan durable binding mismatch: {record.plan_id}"
        )
      plans[plan.plan_id] = plan.to_dict()
      versions[plan.plan_id] = max(1, int(record.state_version or 1))
    return {"version": ExitPlanBook.VERSION, "plans": plans}, versions

  @staticmethod
  async def _load_exact_pending_exit_order(
    db: Any,
    *,
    record: AutoExitPlanRecord,
    intent_id: str,
    strategy_run_id: str,
  ) -> Optional[PendingTradeOrder]:
    orders = list(
      (
        await db.execute(
          select(PendingTradeOrder)
          .where(PendingTradeOrder.intent_id == str(intent_id))
          .order_by(PendingTradeOrder.client_order_id)
          .limit(2)
        )
      )
      .scalars()
      .all()
    )
    if len(orders) > 1:
      raise RuntimeError("同一退出意图重复绑定多个待处理卖单")
    if not orders:
      return None
    order = orders[0]
    if (
      str(order.strategy_run_id or "") != str(strategy_run_id or "")
      or str(order.account_id or "") != str(record.account_id or "")
      or str(order.intent_id or "") != str(intent_id or "")
      or str(order.side or "").upper() != "SELL"
      or str(order.instrument_code or "").upper()
      != str(record.instrument_code or "").upper()
    ):
      raise RuntimeError("待处理卖单与退出计划、运行、账户或标的不一致")
    return order

  async def load_strategy_pending_exit_intents(
    self,
    *,
    strategy_run_id: str,
  ) -> list[dict[str, Any]]:
    """Return crash-gap exit intents that still belong to one runtime.

    An existing PendingTradeOrder means routing already crossed the durable
    command boundary and broker/runtime reports own convergence.  It is
    returned as WAIT_ORDER so recovery never mistakes a terminal/partial
    broker projection for permission to submit a second SELL.
    """

    normalized_run_id = str(strategy_run_id or "").strip()
    if not normalized_run_id:
      raise ValueError("策略运行标识不能为空")
    recoveries: list[dict[str, Any]] = []
    async with AsyncSessionLocal() as db:
      records = await AutoExitPlanRepository(db).find_for_strategy_run(
        normalized_run_id
      )
      for record in records:
        if durable_exit_plan_owner_kind(record) != RUNTIME_BOOK_OWNER:
          raise RuntimeError(
            f"退出计划持久化所有权不一致: {record.plan_id}"
          )
        plan = ExitPlan.from_dict(dict(record.plan_state or {}))
        intent_id = str(plan.pending_intent_id or "").strip()
        if not intent_id:
          continue
        intent = await db.get(TradeIntentRecord, intent_id)
        if intent is None:
          raise RuntimeError(
            f"退出计划存在无意图记录的待提交状态: {record.plan_id}"
          )
        metadata = dict(intent.intent_metadata or {})
        if not _is_exact_exit_plan_intent(
          record,
          intent,
          expected_intent_id=intent_id,
          expected_strategy_run_id=normalized_run_id,
        ):
          raise RuntimeError(
            f"退出计划待提交意图所有权不一致: {record.plan_id}"
          )
        pending = await self._load_exact_pending_exit_order(
          db,
          record=record,
          intent_id=intent_id,
          strategy_run_id=normalized_run_id,
        )
        status = str(intent.status or "").strip().upper()
        if (
          pending is not None
          and _terminal_pending_order_supports_zero_fill(pending, metadata)
          and _has_authoritative_zero_fill_proof(status, metadata)
        ):
          action = "RELEASE_ZERO"
        elif pending is not None:
          action = "WAIT_ORDER"
        elif status == "AWAITING_APPROVAL":
          action = "AWAIT_APPROVAL"
        elif _has_authoritative_zero_fill_proof(
          status,
          metadata,
        ) and not _requires_terminal_pending_order(metadata):
          action = "RELEASE_ZERO"
        elif status in {"PENDING", "APPROVED"}:
          action = "ROUTE"
        elif status in {
          "REJECTED",
          "CANCELLED",
          "EXPIRED",
          "DELAYED",
          "RECONCILED_ZERO_FILL",
        }:
          action = "BLOCK"
        else:
          action = "BLOCK"
        recoveries.append(
          {
            "action": action,
            "durable_status": status,
            "plan_id": str(record.plan_id),
            "intent_id": str(intent.id),
            "strategy_id": str(intent.strategy_id or ""),
            "strategy_run_id": normalized_run_id,
            "instrument_code": str(intent.instrument_code or ""),
            "bucket": str(intent.bucket or record.bucket),
            "reason": str(intent.reason or ""),
            "priority": str(intent.priority or "NORMAL"),
            "target_amount": intent.target_amount,
            "target_position_pct": intent.target_position_pct,
            "target_volume": intent.target_volume,
            "limit_price_hint": intent.limit_price_hint,
            "trace_id": intent.trace_id,
            "metadata": metadata,
            "approval_ttl_ms": metadata.get("approval_ttl_ms"),
            "max_price_deviation_bps": metadata.get(
              "max_price_deviation_bps"
            ),
            "expiry_policy": dict(metadata.get("expiry_policy") or {}),
            "created_at": intent.created_at,
          }
        )
    return recoveries

  async def load_managed_pending_exit_intent(
    self,
    *,
    strategy_run_id: str,
    expected_plan_id: str,
    expected_config_version: Optional[int] = None,
  ) -> Optional[dict[str, Any]]:
    """Restore one dedicated manual SELL across approval-to-route crashes."""

    async with AsyncSessionLocal() as db:
      record = await self._load_exact_managed_runtime_record(
        db,
        strategy_run_id=strategy_run_id,
        expected_plan_id=expected_plan_id,
        expected_config_version=expected_config_version,
        for_update=True,
      )
      normalized_run_id = str(record.strategy_run_id or "")
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      if (
        plan.plan_id != str(record.plan_id)
        or int(plan.template.config_version or 0)
        != int(record.config_version or 0)
      ):
        raise RuntimeError("独立卖出策略与退出计划持久化绑定不一致")
      intent_id = str(plan.pending_intent_id or "").strip()
      if not intent_id:
        return None
      intent = await db.get(TradeIntentRecord, intent_id)
      if intent is None:
        raise RuntimeError("独立卖出计划存在无意图记录的待提交状态")
      metadata = dict(intent.intent_metadata or {})
      if not _is_exact_exit_plan_intent(
        record,
        intent,
        expected_intent_id=intent_id,
        expected_strategy_run_id=normalized_run_id,
      ):
        raise RuntimeError("独立卖出计划待提交意图所有权不一致")
      pending = await self._load_exact_pending_exit_order(
        db,
        record=record,
        intent_id=intent_id,
        strategy_run_id=normalized_run_id,
      )
      status = str(intent.status or "").strip().upper()
      if (
        _has_authoritative_zero_fill_proof(status, metadata)
        and (
          (
            pending is None
            and not _requires_terminal_pending_order(metadata)
          )
          or (
            pending is not None
            and _terminal_pending_order_supports_zero_fill(pending, metadata)
          )
        )
      ):
        ExitPlanBook([plan]).apply_order_event(
          plan_id=plan.plan_id,
          intent_id=intent_id,
          status="RECONCILED_ZERO_FILL",
          order_id=(str(pending.client_order_id or "") if pending is not None else ""),
        )
        self._sync_record(record, plan)
        await self._append_event(
          db,
          business_key=f"managed-runtime-terminal-release:{record.plan_id}:{intent_id}",
          plan_id=str(record.plan_id),
          event_type="MANAGED_EXIT_TERMINAL_INTENT_RELEASED",
          payload={"intent_id": intent_id, "durable_status": status},
        )
        await db.commit()
        return None
      if pending is not None:
        action = "WAIT_ORDER"
      elif status in {"AWAITING_APPROVAL", "APPROVED"}:
        action = "RESTORE_APPROVAL"
      elif status == "PENDING":
        action = "ROUTE_AUTO"
      else:
        action = "RECONCILE_REQUIRED"
      return {
        "action": action,
        "durable_status": status,
        "plan_id": str(record.plan_id),
        "intent_id": str(intent.id),
        "strategy_id": str(intent.strategy_id or ""),
        "strategy_run_id": normalized_run_id,
        "instrument_code": str(intent.instrument_code or ""),
        "bucket": str(intent.bucket or record.bucket),
        "reason": str(intent.reason or ""),
        "priority": str(intent.priority or "NORMAL"),
        "target_amount": intent.target_amount,
        "target_position_pct": intent.target_position_pct,
        "target_volume": intent.target_volume,
        "limit_price_hint": intent.limit_price_hint,
        "trace_id": intent.trace_id,
        "metadata": metadata,
        "approval_ttl_ms": metadata.get("approval_ttl_ms"),
        "max_price_deviation_bps": metadata.get("max_price_deviation_bps"),
        "expiry_policy": dict(metadata.get("expiry_policy") or {}),
        "created_at": intent.created_at,
      }

  async def load_existing_managed_exit_order_projection(
    self,
    *,
    strategy_run_id: str,
    expected_plan_id: str,
    intent_id: str,
    account_id: str,
  ) -> Optional[dict[str, Any]]:
    return await self._load_existing_exit_order_projection(
      strategy_run_id=strategy_run_id,
      expected_plan_id=expected_plan_id,
      intent_id=intent_id,
      account_id=account_id,
      require_dedicated_owner=True,
    )

  async def load_existing_exit_order_projection(
    self,
    *,
    strategy_run_id: str,
    expected_plan_id: str,
    intent_id: str,
    account_id: str,
  ) -> Optional[dict[str, Any]]:
    """Return an already queued SELL for any exactly bound runtime plan."""

    return await self._load_existing_exit_order_projection(
      strategy_run_id=strategy_run_id,
      expected_plan_id=expected_plan_id,
      intent_id=intent_id,
      account_id=account_id,
      require_dedicated_owner=False,
    )

  async def _load_existing_exit_order_projection(
    self,
    *,
    strategy_run_id: str,
    expected_plan_id: str,
    intent_id: str,
    account_id: str,
    require_dedicated_owner: bool,
  ) -> Optional[dict[str, Any]]:
    """Return an exactly bound order for idempotent approval replay.

    Absence of both the durable intent/order boundary returns ``None``.  Any
    partial, duplicate, or cross-owner binding raises so a caller can never
    report replay success for another plan's broker command.
    """

    normalized_intent_id = str(intent_id or "").strip()
    normalized_account_id = str(account_id or "").strip()
    if not normalized_intent_id:
      raise ValueError("退出意图标识不能为空")
    if not normalized_account_id:
      raise ValueError("账户标识不能为空")
    async with AsyncSessionLocal() as db:
      if require_dedicated_owner:
        record = await self._load_exact_managed_runtime_record(
          db,
          strategy_run_id=strategy_run_id,
          expected_plan_id=expected_plan_id,
        )
      else:
        record = await db.get(AutoExitPlanRecord, str(expected_plan_id or "").strip())
        if record is None:
          raise RuntimeError("退出计划不存在")
        if str(record.strategy_run_id or "") != str(strategy_run_id or "").strip():
          raise RuntimeError("退出计划与预期 StrategyRun 绑定不一致")
        durable_owner = durable_exit_plan_owner_kind(record)
        if durable_owner == MANAGED_EXIT_STRATEGY_OWNER:
          # Dedicated manual plans must additionally prove that this run is not
          # ambiguously attached to another plan.
          record = await self._load_exact_managed_runtime_record(
            db,
            strategy_run_id=strategy_run_id,
            expected_plan_id=expected_plan_id,
          )
        elif durable_owner != RUNTIME_BOOK_OWNER:
          raise RuntimeError("退出计划持久化所有权不一致")
      if str(record.account_id or "") != normalized_account_id:
        raise RuntimeError("独立卖出策略与预期账户绑定不一致")

      intent = await db.get(TradeIntentRecord, normalized_intent_id)
      orders = list(
        (
          await db.execute(
            select(PendingTradeOrder)
            .where(PendingTradeOrder.intent_id == normalized_intent_id)
            .order_by(PendingTradeOrder.client_order_id)
            .limit(2)
          )
        )
        .scalars()
        .all()
      )
      if intent is None:
        if orders:
          raise RuntimeError("退出卖单存在但缺少对应的持久化 EXIT_PLAN 意图")
        return None

      metadata = dict(intent.intent_metadata or {})
      if (
        str(intent.id or "") != normalized_intent_id
        or str(intent.owner_type or "").upper() != "EXIT_PLAN"
        or str(intent.owner_id or "") != str(record.plan_id)
        or str(intent.strategy_run_id or "")
        != str(record.strategy_run_id or "")
        or str(intent.account_id or "") != normalized_account_id
        or str(intent.instrument_code or "").upper()
        != str(record.instrument_code or "").upper()
        or str(intent.direction or "").upper() != "SELL"
        or str(metadata.get("exit_plan_id") or "") != str(record.plan_id)
      ):
        raise RuntimeError("退出意图与计划、运行、账户或 SELL 所有权不一致")
      if not orders:
        return None
      if len(orders) != 1:
        raise RuntimeError("同一退出意图重复绑定多个待处理卖单")
      order = orders[0]
      if (
        str(order.strategy_run_id or "") != str(record.strategy_run_id or "")
        or str(order.account_id or "") != normalized_account_id
        or str(order.intent_id or "") != normalized_intent_id
        or str(order.side or "").upper() != "SELL"
        or str(order.instrument_code or "").upper()
        != str(record.instrument_code or "").upper()
      ):
        raise RuntimeError("待处理卖单与退出意图的运行、账户或标的不一致")
      return {
        "plan_id": str(record.plan_id),
        "strategy_run_id": str(order.strategy_run_id or ""),
        "intent_id": str(order.intent_id or ""),
        "account_id": str(order.account_id or ""),
        "instrument_code": str(order.instrument_code or ""),
        "side": str(order.side or "").upper(),
        "client_order_id": str(order.client_order_id or ""),
        "broker_order_id": str(order.broker_order_id or "") or None,
        "status": str(order.status or "").upper(),
        "order_type": str(order.order_type or ""),
        "limit_price": str(order.limit_price or ""),
        "volume": int(order.volume or 0),
        "execution_mode": str(order.execution_mode or "").lower(),
      }

  async def strategy_plan_event_applied(
    self,
    *,
    plan_id: str,
    business_key: str,
  ) -> bool:
    """Check the plan-side marker for one durable runtime fact."""

    if not plan_id or not business_key:
      return False
    async with AsyncSessionLocal() as db:
      return (
        await db.scalar(
          select(AutoExitPlanEvent.event_id)
          .where(
            AutoExitPlanEvent.plan_id == str(plan_id),
            AutoExitPlanEvent.business_key == str(business_key),
          )
          .limit(1)
        )
        is not None
      )

  async def persist_strategy_plan_state(
    self,
    *,
    strategy_run_id: str,
    plan_state: Mapping[str, Any],
    execution_mode: str,
    expected_state_version: Optional[int] = None,
    evaluated_at: Optional[datetime] = None,
    event_type: str = "STRATEGY_PLAN_STATE_UPDATED",
    event_business_key: Optional[str] = None,
    intent: Any = None,
    entry_authorization: Optional[Mapping[str, Any]] = None,
    revoke_authorization: bool = False,
  ) -> tuple[dict[str, Any], int]:
    """Persist one runtime-owned plan transition as the canonical aggregate.

    The plan table is authoritative for PAPER/LIVE.  A new entry fill creates
    the row; subsequent transitions use ``state_version`` CAS.  Configuration
    changes clear exact LIVE authorization before the new state is visible.
    """

    normalized_run_id = str(strategy_run_id or "").strip()
    if not normalized_run_id:
      raise ValueError("策略运行标识不能为空")
    plan = ExitPlan.from_dict(dict(plan_state or {}))
    template = plan.template
    if str(template.run_id or "") != normalized_run_id:
      raise ValueError("退出计划不属于当前策略运行")
    if str(template.source_type or "").upper() not in RUNTIME_EXIT_PLAN_SOURCE_TYPES:
      raise ValueError("Engine ExitPlanBook 不接受该退出计划来源")
    normalized_mode = self._execution_mode(execution_mode)
    async with AsyncSessionLocal() as db:
      repo = AutoExitPlanRepository(db)
      locked_scope = await lock_exit_plan_scope(
        db,
        account_id=str(template.account_id),
        instrument_code=str(template.instrument_code),
        target_plan_id=plan.plan_id,
        execution_mode=normalized_mode,
        strategy_run_id=normalized_run_id,
      )
      record = locked_scope.plan(plan.plan_id)
      if record is None:
        durable_plan = ExitPlan.from_dict(plan.to_dict())
        if normalized_mode == "live":
          durable_plan.template = ExitPlanTemplate.from_dict(
            {
              **durable_plan.template.to_dict(),
              "auto_exit_authorized": False,
            }
          )
        record = AutoExitPlanRecord(
          plan_id=durable_plan.plan_id,
          account_id=durable_plan.template.account_id,
          instrument_code=durable_plan.template.instrument_code,
          bucket=durable_plan.template.bucket,
          source_type=durable_plan.template.source_type,
          source_id=durable_plan.template.source_id or durable_plan.plan_id,
          strategy_run_id=normalized_run_id,
          enabled=durable_plan.status != ExitPlanStatus.PAUSED,
          status=durable_plan.status.value,
          execution_mode=normalized_mode,
          auto_exit_authorized=False,
          config_version=int(durable_plan.template.config_version),
          state_version=1,
          protected_volume=int(durable_plan.entry_filled_volume or 0),
          exited_volume=int(durable_plan.exited_volume or 0),
          remaining_volume=int(durable_plan.remaining_volume or 0),
          entry_avg_price=float(durable_plan.entry_avg_price or 0.0),
          plan_state=durable_plan.to_dict(),
        )
        self._sync_record(record, durable_plan, evaluated_at=evaluated_at)
        db.add(record)
        await self._append_event(
          db,
          business_key=(
            str(event_business_key)
            if event_business_key
            else f"strategy-plan-state-created:{plan.plan_id}:1"
          ),
          plan_id=plan.plan_id,
          event_type="STRATEGY_PLAN_STATE_CREATED",
          payload={
            "strategy_run_id": normalized_run_id,
            "state_version": 1,
            "config_version": int(record.config_version or 0),
          },
        )
        if intent is not None:
          await self._add_strategy_exit_intent(db, record, intent)
        await db.flush()
        locked_scope = LockedExitPlanScope(
          position=locked_scope.position,
          plans=[*locked_scope.plans, record],
          target_plan=record,
        )
        await self._derive_strategy_entry_authorization(
          db,
          record,
          entry_authorization=entry_authorization,
          state_version=1,
          locked_scope=locked_scope,
        )
        await db.flush()
        await db.commit()
        return (
          dict(record.plan_state or {}),
          max(1, int(record.state_version or 1)),
        )

      persisted_state = dict(record.plan_state or {})
      persisted_plan = ExitPlan.from_dict(persisted_state)
      self._require_strategy_entry_sync_binding(
        record=record,
        persistent_plan=persisted_plan,
        incoming_plan=plan,
        strategy_run_id=normalized_run_id,
      )
      current_config_version = int(record.config_version or 0)
      incoming_config_version = int(template.config_version or 0)
      if incoming_config_version < current_config_version:
        raise AutoExitPlanConcurrencyError(
          "退出计划配置版本落后，必须重新装载后重放行情事实"
        )
      if incoming_config_version > current_config_version:
        record.config_version = incoming_config_version
        clear_exact_auto_exit_authorization(record, bump_state_version=False)
        await db.flush()
      if revoke_authorization:
        clear_exact_auto_exit_authorization(record, bump_state_version=False)
      if bool(record.auto_exit_authorized) and (
        int(record.exited_volume or 0) != int(plan.exited_volume or 0)
        or int(record.remaining_volume or 0) != int(plan.remaining_volume or 0)
      ):
        clear_exact_auto_exit_authorization(record, bump_state_version=False)
        plan.template = ExitPlanTemplate.from_dict(
          {**plan.template.to_dict(), "auto_exit_authorized": False}
        )
      plan.template = ExitPlanTemplate.from_dict(
        {
          **plan.template.to_dict(),
          "auto_exit_authorized": bool(record.auto_exit_authorized),
        }
      )
      canonical_state = plan.to_dict()
      if persisted_state == canonical_state:
        current_version = max(1, int(record.state_version or 1))
        # A durable runtime fact may legitimately leave the aggregate state
        # unchanged (for example, a duplicate terminal report).  Its marker
        # and any atomically-created intent/authorization still have to cross
        # the same commit boundary, otherwise every restart replays the fact.
        if event_business_key:
          await self._append_event(
            db,
            business_key=str(event_business_key),
            plan_id=plan.plan_id,
            event_type=str(event_type or "STRATEGY_PLAN_STATE_UPDATED").upper(),
            payload={
              "strategy_run_id": normalized_run_id,
              "state_version": current_version,
              "config_version": int(record.config_version or 0),
              "state_changed": False,
            },
          )
        if intent is not None:
          await self._add_strategy_exit_intent(db, record, intent)
        await self._derive_strategy_entry_authorization(
          db,
          record,
          entry_authorization=entry_authorization,
          state_version=current_version,
          locked_scope=locked_scope,
        )
        await db.flush()
        await db.commit()
        return (
          dict(record.plan_state or {}),
          max(1, int(record.state_version or current_version)),
        )
      expected_version = (
        max(1, int(expected_state_version))
        if expected_state_version is not None
        else max(1, int(record.state_version or 1))
      )
      stored = await repo.compare_and_swap_state(
        plan_id=plan.plan_id,
        expected_state_version=expected_version,
        plan_state=canonical_state,
        evaluated_at=evaluated_at,
        commit=False,
      )
      next_version = int(stored.state_version or expected_version + 1)
      await self._append_event(
        db,
        business_key=(
          str(event_business_key)
          if event_business_key
          else (
            f"strategy-plan-state:{plan.plan_id}:{next_version}:"
            f"{str(event_type or 'STRATEGY_PLAN_STATE_UPDATED').upper()}"
          )
        ),
        plan_id=plan.plan_id,
        event_type=str(event_type or "STRATEGY_PLAN_STATE_UPDATED").upper(),
        payload={
          "strategy_run_id": normalized_run_id,
          "state_version": next_version,
          "config_version": int(stored.config_version or 0),
        },
      )
      if intent is not None:
        await self._add_strategy_exit_intent(db, stored, intent)
      await self._derive_strategy_entry_authorization(
        db,
        stored,
        entry_authorization=entry_authorization,
        state_version=next_version,
        locked_scope=locked_scope,
      )
      await db.flush()
      await db.commit()
      return (
        dict(stored.plan_state or {}),
        max(1, int(stored.state_version or next_version)),
      )

  async def rederive_t_trade_exit_authorizations_after_position_update(
    self,
    *,
    account_id: str,
    instrument_codes: Optional[Iterable[str]] = None,
  ) -> list[dict[str, Any]]:
    """Refresh exact LIVE T-exit grants after broker position convergence.

    A BUY execution and its position callback are independent Agent reports.
    The execution may therefore register a plan against the preceding position
    projection.  Re-derive every affected active T plan after the position
    write commits so the durable grant eventually covers the latest position
    fingerprint.  The derivation is idempotent and retains the expiry anchored
    to the original consumed entry challenge.

    This is eventual convergence over the current durable projection; it does
    not claim that a position callback carries a source watermark causally
    newer than the entry execution report.

    ``instrument_codes=None`` means an authoritative full-account snapshot and
    refreshes every eligible T plan for the account.  An explicit empty scope
    is a no-op.
    """

    normalized_account = str(account_id or "").strip()
    if not normalized_account:
      raise ValueError("账户标识不能为空")
    normalized_codes: Optional[tuple[str, ...]]
    if instrument_codes is None:
      normalized_codes = None
    else:
      raw_codes = (
        [instrument_codes]
        if isinstance(instrument_codes, str)
        else list(instrument_codes)
      )
      normalized_codes = tuple(
        sorted(
          {
            str(code or "").strip().upper()
            for code in raw_codes
            if str(code or "").strip()
          }
        )
      )
      if not normalized_codes:
        return []

    async with AsyncSessionLocal() as db:
      candidate_stmt = (
        select(AutoExitPlanRecord.plan_id)
        .where(
          AutoExitPlanRecord.account_id == normalized_account,
          AutoExitPlanRecord.source_type == T_TRADE_BATCH_SOURCE,
          AutoExitPlanRecord.execution_mode == "live",
          AutoExitPlanRecord.enabled == True,  # noqa: E712
          AutoExitPlanRecord.status.in_(("ACTIVE", "PARTIALLY_EXITED")),
          AutoExitPlanRecord.remaining_volume > 0,
          AutoExitPlanRecord.strategy_run_id.is_not(None),
        )
        .order_by(AutoExitPlanRecord.plan_id)
        .limit(_T_TRADE_AUTHORIZATION_REDERIVATION_LIMIT + 1)
      )
      if normalized_codes is not None:
        candidate_stmt = candidate_stmt.where(
          AutoExitPlanRecord.instrument_code.in_(normalized_codes)
        )
      candidate_ids = list((await db.execute(candidate_stmt)).scalars().all())
      if len(candidate_ids) > _T_TRADE_AUTHORIZATION_REDERIVATION_LIMIT:
        raise RuntimeError("活跃做 T 自动退出计划超过单次授权重算上限")

      outcomes: list[dict[str, Any]] = []
      for plan_id in candidate_ids:
        scope = await lock_exit_plan_scope_for_plan(db, str(plan_id))
        record = scope.plan(str(plan_id))
        if (
          record is None
          or str(record.account_id or "") != normalized_account
          or str(record.source_type or "").upper() != T_TRADE_BATCH_SOURCE
          or str(record.execution_mode or "").lower() != "live"
          or not bool(record.enabled)
          or str(record.status or "").upper()
          not in {"ACTIVE", "PARTIALLY_EXITED"}
          or int(record.remaining_volume or 0) <= 0
        ):
          continue

        batch = await db.get(TTradeBatch, str(record.source_id or ""))
        entry_intent_id = (
          str(batch.entry_intent_id or "").strip()
          if batch is not None
          and str(batch.account_id or "") == normalized_account
          and str(batch.instrument_code or "").upper()
          == str(record.instrument_code or "").upper()
          and str(batch.strategy_run_id or "")
          == str(record.strategy_run_id or "")
          else ""
        )
        intent = (
          await db.get(TradeIntentRecord, entry_intent_id)
          if entry_intent_id
          else None
        )
        intent_metadata = (
          dict(intent.intent_metadata or {}) if intent is not None else {}
        )
        context = {
          "entry_intent_id": entry_intent_id,
          "challenge_id": str(
            intent_metadata.get("t_trade_entry_approval_challenge_id") or ""
          ).strip(),
          "cumulative_filled_volume": int(
            getattr(intent, "executed_volume", 0) or 0
          ),
        }
        result = await self._derive_strategy_entry_authorization(
          db,
          record,
          entry_authorization=context,
          state_version=max(1, int(record.state_version or 1)),
          locked_scope=scope,
        )
        if result is not None:
          outcomes.append(
            {
              "plan_id": str(record.plan_id),
              "valid": bool(result.valid),
              "code": str(result.code),
              "outcome": (
                "DERIVED"
                if result.valid
                else "DEFERRED"
                if await self._t_trade_derivation_is_deferred(
                  db,
                  result,
                  entry_authorization=context,
                )
                else "REJECTED"
              ),
            }
          )
      await db.flush()
      await db.commit()
      return outcomes

  @staticmethod
  async def _t_trade_derivation_is_deferred(
    db: Any,
    result: TTradeExitAuthorizationDerivation,
    *,
    entry_authorization: Mapping[str, Any],
  ) -> bool:
    code = str(result.code or "").upper()
    if code not in _T_TRADE_AUTHORIZATION_DEFERRED_CODES:
      return False
    if code != "T_TRADE_ENTRY_FILL_SCOPE_MISMATCH":
      return True

    # This code also covers an actual fill above the user-confirmed ceiling.
    # That case is permanent; only an in-flight plan/intent projection mismatch
    # is eligible for a later position-driven retry.
    try:
      actual_volume = int(
        entry_authorization.get("cumulative_filled_volume") or 0
      )
    except (TypeError, ValueError, OverflowError):
      return False
    challenge_id = str(entry_authorization.get("challenge_id") or "").strip()
    challenge = (
      await db.get(TradeConfirmationChallenge, challenge_id)
      if challenge_id
      else None
    )
    payload = dict(challenge.payload or {}) if challenge is not None else {}
    raw_binding = payload.get(T_TRADE_EXIT_AUTHORIZATION_BINDING_KEY)
    binding = dict(raw_binding) if isinstance(raw_binding, Mapping) else {}
    raw_subject = binding.get("subject")
    subject = dict(raw_subject) if isinstance(raw_subject, Mapping) else {}
    try:
      confirmed_ceiling = int(subject.get("max_protected_volume") or 0)
    except (TypeError, ValueError, OverflowError):
      return False
    return actual_volume > 0 and confirmed_ceiling >= actual_volume

  async def _derive_strategy_entry_authorization(
    self,
    db: Any,
    record: AutoExitPlanRecord,
    *,
    entry_authorization: Optional[Mapping[str, Any]],
    state_version: int,
    locked_scope: LockedExitPlanScope,
  ) -> Optional[TTradeExitAuthorizationDerivation]:
    """Derive the LIVE exit grant in the same plan-state transaction."""

    context = dict(entry_authorization or {})
    if str(record.execution_mode or "").lower() != "live" or not context:
      return None
    result = await derive_exact_auto_exit_authorization_from_t_trade_entry(
      db,
      record,
      entry_intent_id=str(context.get("entry_intent_id") or ""),
      challenge_id=str(context.get("challenge_id") or ""),
      cumulative_filled_volume=int(
        context.get("cumulative_filled_volume") or 0
      ),
      locked_scope=locked_scope,
    )
    if result.valid:
      return result
    deferred = await self._t_trade_derivation_is_deferred(
      db,
      result,
      entry_authorization=context,
    )
    outcome = "DEFERRED" if deferred else "REJECTED"
    await self._append_event(
      db,
      business_key=(
        f"t-entry-exit-authorization-{outcome.lower()}:{record.plan_id}:"
        f"{int(state_version)}:{result.code}"
      ),
      plan_id=str(record.plan_id),
      event_type=f"AUTO_EXIT_AUTHORIZATION_DERIVATION_{outcome}",
      payload={
        "strategy_run_id": str(record.strategy_run_id or ""),
        "entry_intent_id": str(context.get("entry_intent_id") or ""),
        "challenge_id": str(context.get("challenge_id") or ""),
        "state_version": int(state_version),
        "config_version": int(record.config_version or 0),
        "reason_code": result.code,
        "message": result.message,
        "retryable": deferred,
      },
    )
    return result

  @staticmethod
  def _require_exact_strategy_exit_intent(
    record: AutoExitPlanRecord,
    intent: Any,
    *,
    expected_intent_id: str,
  ) -> str:
    """Validate the immutable plan/run/SELL identity before any state CAS."""

    intent_id = str(getattr(intent, "intent_id", "") or "").strip()
    metadata = dict(getattr(intent, "metadata", {}) or {})
    direction_value = getattr(getattr(intent, "direction", None), "value", None)
    direction = str(
      direction_value
      if direction_value is not None
      else getattr(intent, "direction", "") or ""
    ).upper()
    if (
      not intent_id
      or intent_id != str(expected_intent_id or "").strip()
      or direction != "SELL"
      or str(getattr(intent, "run_id", "") or "")
      != str(record.strategy_run_id or "")
      or str(getattr(intent, "instrument_code", "") or "").upper()
      != str(record.instrument_code or "").upper()
      or str(metadata.get("owner_type") or "").upper() != "EXIT_PLAN"
      or str(metadata.get("owner_id") or "") != str(record.plan_id)
      or str(metadata.get("exit_plan_id") or "") != str(record.plan_id)
    ):
      raise ValueError("退出意图必须与待提交状态绑定同一 SELL/EXIT_PLAN 所有权")
    return intent_id

  @staticmethod
  async def _add_strategy_exit_intent(
    db: Any,
    record: AutoExitPlanRecord,
    intent: Any,
  ) -> None:
    """Insert the EXIT_PLAN-owned intent in the plan transition transaction."""

    intent_id = AutoExitPlanService._require_exact_strategy_exit_intent(
      record,
      intent,
      expected_intent_id=str(getattr(intent, "intent_id", "") or ""),
    )
    metadata = dict(getattr(intent, "metadata", {}) or {})
    metadata.setdefault(
      "approval_ttl_ms",
      getattr(intent, "approval_ttl_ms", None),
    )
    metadata.setdefault(
      "max_price_deviation_bps",
      getattr(intent, "max_price_deviation_bps", None),
    )
    metadata.setdefault(
      "expiry_policy",
      dict(getattr(intent, "expiry_policy", {}) or {}),
    )
    existing = await db.get(TradeIntentRecord, intent_id)
    if existing is not None:
      existing_metadata = dict(existing.intent_metadata or {})
      if (
        str(existing.owner_type or "").upper() != "EXIT_PLAN"
        or str(existing.owner_id or "") != str(record.plan_id)
        or str(existing.strategy_run_id or "")
        != str(record.strategy_run_id or "")
        or str(existing.account_id or "") != str(record.account_id or "")
        or str(existing.instrument_code or "").upper()
        != str(record.instrument_code or "").upper()
        or str(existing.direction or "").upper() != "SELL"
        or str(existing_metadata.get("exit_plan_id") or "")
        != str(record.plan_id)
      ):
        raise ValueError("退出意图幂等键已绑定其他业务所有者")
      return
    direction = getattr(getattr(intent, "direction", None), "value", None)
    priority = getattr(getattr(intent, "priority", None), "value", None)
    intent_type = getattr(getattr(intent, "intent_type", None), "value", None)
    execution_mode = getattr(
      getattr(intent, "execution_mode", None), "value", None
    )
    metadata.setdefault("execution_mode", execution_mode)
    status = "AWAITING_APPROVAL" if execution_mode == "MANUAL_CONFIRM" else "PENDING"
    db.add(
      TradeIntentRecord(
        id=intent_id,
        strategy_run_id=str(record.strategy_run_id or "") or None,
        owner_type="EXIT_PLAN",
        owner_id=str(record.plan_id),
        account_id=str(record.account_id),
        strategy_id=str(getattr(intent, "strategy_id", "") or "") or None,
        instrument_code=str(getattr(intent, "instrument_code", "") or ""),
        direction=str(direction or ""),
        bucket=str(getattr(intent, "bucket", "") or record.bucket),
        reason=str(getattr(intent, "reason", "") or ""),
        priority=str(priority or "NORMAL"),
        intent_type=str(intent_type) if intent_type else None,
        confidence=float(getattr(intent, "confidence", 1.0) or 0.0),
        target_amount=getattr(intent, "target_amount", None),
        target_position_pct=getattr(intent, "target_position_pct", None),
        target_volume=getattr(intent, "target_volume", None),
        limit_price_hint=getattr(intent, "limit_price_hint", None),
        trace_id=getattr(intent, "trace_id", None),
        status=status,
        intent_metadata=metadata,
      )
    )

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

    manual_ownership = await self.migrate_manual_plans_to_monitor()

    condition_candidates: list[tuple[ConditionalLiquidationOrder, Position, int]] = []
    linked_conditions = 0
    async with AsyncSessionLocal() as db:
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
      "strategy_plans": 0,
      "conditional_plans": conditional_plans,
      "linked_conditions": linked_conditions,
      "manual_monitor_plans": manual_ownership["migrated"],
      "stopped_managed_runs": manual_ownership["stopped_runs"],
    }

  async def migrate_manual_plans_to_monitor(self) -> dict[str, int]:
    """Idempotently move every legacy manual plan to the global monitor."""

    migrated = 0
    stopped_runs = 0
    async with AsyncSessionLocal() as db:
      rows = list(
        (
          await db.execute(
            select(AutoExitPlanRecord)
            .where(
              AutoExitPlanRecord.source_type.in_(
                (MANUAL_PLAN_SOURCE, MANUAL_LIQUIDATION_SOURCE)
              )
            )
            .order_by(AutoExitPlanRecord.created_at, AutoExitPlanRecord.plan_id)
            .with_for_update()
          )
        )
        .scalars()
        .all()
      )
      for record in rows:
        plan = ExitPlan.from_dict(dict(record.plan_state or {}))
        metadata = dict(plan.template.metadata or {})
        legacy_marker = bool(
          has_managed_runtime_command_marker(record)
          or str(record.last_error or "").startswith(
            (MANAGED_RUNTIME_BINDING_PENDING, MANAGED_RUNTIME_ENABLE_PENDING)
          )
        )
        old_run_id = str(record.strategy_run_id or "").strip()
        if not old_run_id and not legacy_marker:
          continue

        for key in (
          MANAGED_RUNTIME_COMMAND_ID_KEY,
          MANAGED_RUNTIME_COMMAND_FINGERPRINT_KEY,
          MANAGED_RUNTIME_COMMAND_KIND_KEY,
          MANAGED_RUNTIME_PREVIOUS_CONFIG_VERSION_KEY,
          MANAGED_RUNTIME_DESIRED_ENABLED_KEY,
        ):
          metadata.pop(key, None)
        next_config_version = max(
          int(record.config_version or 0),
          int(plan.template.config_version or 0),
          1,
        ) + 1
        plan.template = ExitPlanTemplate.from_dict(
          {
            **plan.template.to_dict(),
            "run_id": "",
            "config_version": next_config_version,
            "metadata": {
              **metadata,
              "execution_owner_migrated_from": old_run_id or None,
              "execution_owner_migrated_at": time_utils.now().isoformat(),
            },
            "auto_exit_authorized": False,
          }
        )
        clear_exact_auto_exit_authorization(record, bump_state_version=False)
        record.strategy_run_id = None
        record.config_version = next_config_version
        record.state_version = max(1, int(record.state_version or 1)) + 1
        record.plan_state = plan.to_dict()
        record.last_error = None

        pending_intent_id = str(plan.pending_intent_id or "").strip()
        if pending_intent_id:
          intent = await db.scalar(
            select(TradeIntentRecord)
            .where(TradeIntentRecord.id == pending_intent_id)
            .with_for_update()
          )
          if intent is not None and str(intent.direction or "").upper() == "SELL":
            intent.owner_type = "EXIT_PLAN"
            intent.owner_id = str(record.plan_id)
            intent_metadata = dict(intent.intent_metadata or {})
            intent_metadata.update(
              {
                "owner_type": "EXIT_PLAN",
                "owner_id": str(record.plan_id),
                "exit_plan_id": str(record.plan_id),
              }
            )
            intent.intent_metadata = intent_metadata

        run_stopped = False
        if old_run_id:
          run = await db.scalar(
            select(StrategyRun)
            .where(StrategyRun.id == old_run_id)
            .with_for_update()
          )
          if run is not None and (
            str(run.plan_kind or "").upper() == "EXIT"
            and str(run.plan_id or "") == str(record.plan_id)
          ):
            run.status = StrategyRunStatus.STOPPED
            run.stop_time = time_utils.now()
            run.error_message = "已迁移至全局 ExitPlanMonitor"
            run_stopped = True
            stopped_runs += 1
        managed = await db.get(ManagedPlanRecord, str(record.plan_id))
        if managed is not None and str(managed.plan_kind or "").upper() == "EXIT":
          managed.status = "MIGRATED_TO_MONITOR"
          managed.current_run_id = None
          managed.last_error = None

        await self._append_event(
          db,
          business_key=f"manual-plan-monitor-migrated:{record.plan_id}",
          plan_id=str(record.plan_id),
          event_type="MANUAL_PLAN_MONITOR_MIGRATED",
          payload={
            "previous_strategy_run_id": old_run_id or None,
            "strategy_run_stopped": run_stopped,
            "config_version": next_config_version,
            "state_version": int(record.state_version or 1),
            "authorization_cleared": True,
          },
        )
        migrated += 1
      if migrated:
        await db.commit()
    return {"migrated": migrated, "stopped_runs": stopped_runs}

  async def sync_strategy_plan_book(
    self,
    *,
    strategy_run_id: str,
    book_state: Mapping[str, Any],
    execution_mode: str,
  ) -> int:
    """Idempotently persist plans owned by one runtime ExitPlanBook."""

    normalized_run_id = str(strategy_run_id or "").strip()
    if not normalized_run_id:
      raise ValueError("策略运行标识不能为空")
    book = ExitPlanBook.from_dict(book_state)
    synced = 0
    async with AsyncSessionLocal() as db:
      repo = AutoExitPlanRepository(db)
      for plan in sorted(book.plans.values(), key=lambda item: item.plan_id):
        if (
          str(plan.template.source_type or "").upper()
          not in RUNTIME_EXIT_PLAN_SOURCE_TYPES
        ):
          raise ValueError("Engine ExitPlanBook 不迁移该退出计划来源")
        if str(plan.template.run_id or "").strip() != normalized_run_id:
          raise ValueError("Engine ExitPlanBook 计划不属于当前 StrategyRun")
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
            strategy_run_id=normalized_run_id,
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
            clear_exact_auto_exit_authorization(record, bump_state_version=False)
            event_type = "STRATEGY_PLAN_ENTRY_EXPANDED"
            business_key = (
              f"strategy-plan-entry-expanded:{plan.plan_id}:"
              f"{record_version}:{expansion['entry_filled_volume']}"
            )
            event_payload = {
              "strategy_run_id": normalized_run_id,
              "source_type": persistent_plan.template.source_type,
              "config_version": record_version,
              "incoming_template_version": template_version,
              **expansion,
            }
          else:
            persistent_plan.apply_template(template)
            record.config_version = template_version
            clear_exact_auto_exit_authorization(record, bump_state_version=False)
            event_type = "STRATEGY_PLAN_POLICY_UPDATED"
            business_key = f"strategy-plan-sync:{plan.plan_id}:{template_version}"
            event_payload = {
              "strategy_run_id": normalized_run_id,
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
            strategy_run_id=normalized_run_id,
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
            "strategy_run_id": normalized_run_id,
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

  @staticmethod
  def _managed_command_id(value: Any, *, required: bool) -> str:
    command_id = str(value or "").strip()
    if required and not command_id:
      raise ValueError("卖出托管计划缺少 Engine command_id")
    if len(command_id) > 128:
      raise ValueError("卖出托管计划 command_id 不能超过 128 个字符")
    return command_id

  @staticmethod
  def _manual_plan_id_for_command(command_id: str) -> str:
    normalized = str(command_id or "").strip()
    if not normalized:
      return ""
    deterministic = uuid.uuid5(
      uuid.NAMESPACE_URL,
      f"quantx:manual-exit-plan:v1:{normalized}",
    )
    return f"manual-position:{deterministic}"

  @staticmethod
  def _managed_command_fingerprint(payload: Mapping[str, Any]) -> str:
    try:
      canonical = json.dumps(
        dict(payload or {}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
      )
    except (TypeError, ValueError) as exc:
      raise ValueError("卖出托管计划命令必须是有限 JSON") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

  @staticmethod
  def _managed_binding_marker(command_id: str, config_version: int) -> str:
    return (
      f"{MANAGED_RUNTIME_BINDING_PENDING}:"
      f"{str(command_id or '').strip()}:v{int(config_version)}"
    )[:2000]

  @staticmethod
  def _managed_enable_marker(
    command_id: str,
    *,
    enabled: bool,
    config_version: int,
  ) -> str:
    payload = json.dumps(
      {
        "command_id": str(command_id or ""),
        "enabled": bool(enabled),
        "config_version": int(config_version),
      },
      ensure_ascii=False,
      sort_keys=True,
      separators=(",", ":"),
    )
    return f"{MANAGED_RUNTIME_ENABLE_PENDING}:{payload}"[:2000]

  @staticmethod
  def _parse_managed_enable_marker(value: Any) -> Optional[dict[str, Any]]:
    marker = str(value or "")
    prefix = f"{MANAGED_RUNTIME_ENABLE_PENDING}:"
    if not marker.startswith(prefix):
      return None
    try:
      payload = json.loads(marker[len(prefix) :])
    except (TypeError, ValueError) as exc:
      raise RuntimeError("卖出计划启停恢复标记无效") from exc
    if not isinstance(payload, Mapping):
      raise RuntimeError("卖出计划启停恢复标记格式无效")
    command_id = str(payload.get("command_id") or "")
    config_version = int(payload.get("config_version") or 0)
    if not command_id or config_version <= 0 or not isinstance(
      payload.get("enabled"), bool
    ):
      raise RuntimeError("卖出计划启停恢复标记内容无效")
    return {
      "command_id": command_id,
      "enabled": bool(payload["enabled"]),
      "config_version": config_version,
    }

  @staticmethod
  def _managed_command_matches(
    metadata: Mapping[str, Any],
    *,
    command_id: str,
    command_fingerprint: str,
    command_kind: str,
    previous_config_version: int,
  ) -> bool:
    return bool(
      str(metadata.get(MANAGED_RUNTIME_COMMAND_ID_KEY) or "") == command_id
      and str(metadata.get(MANAGED_RUNTIME_COMMAND_FINGERPRINT_KEY) or "")
      == command_fingerprint
      and str(metadata.get(MANAGED_RUNTIME_COMMAND_KIND_KEY) or "").upper()
      == str(command_kind or "").upper()
      and int(metadata.get(MANAGED_RUNTIME_PREVIOUS_CONFIG_VERSION_KEY) or 0)
      == int(previous_config_version)
    )

  @staticmethod
  def _manual_command_id(value: Any) -> str:
    command_id = str(value or "").strip()
    if len(command_id) > 128:
      raise ValueError("人工计划 command_id 不能超过 128 个字符")
    return command_id

  @staticmethod
  def _manual_command_fingerprint(payload: Mapping[str, Any]) -> str:
    try:
      canonical = json.dumps(
        dict(payload or {}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
      )
    except (TypeError, ValueError) as exc:
      raise ValueError("人工计划命令必须是有限 JSON") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

  @staticmethod
  def _manual_command_matches(
    metadata: Mapping[str, Any],
    *,
    command_id: str,
    command_fingerprint: str,
    command_kind: str,
    previous_config_version: int,
  ) -> bool:
    return bool(
      command_id
      and str(metadata.get(MANUAL_COMMAND_ID_KEY) or "") == command_id
      and str(metadata.get(MANUAL_COMMAND_FINGERPRINT_KEY) or "")
      == command_fingerprint
      and str(metadata.get(MANUAL_COMMAND_KIND_KEY) or "").upper()
      == str(command_kind or "").upper()
      and int(metadata.get(MANUAL_COMMAND_PREVIOUS_CONFIG_VERSION_KEY) or 0)
      == int(previous_config_version)
    )

  async def _load_manual_plan_record(
    self,
    plan_id: str,
  ) -> Optional[AutoExitPlanRecord]:
    async with AsyncSessionLocal() as db:
      return await AutoExitPlanRepository(db).find_by_id(plan_id)

  async def _validate_finalized_managed_runtime_binding(
    self,
    record: AutoExitPlanRecord,
    plan: ExitPlan,
    *,
    command_id: str,
    command_kind: str,
  ) -> AutoExitPlanRecord:
    """Return a fresh canonical row without replaying a finalized binding."""

    if self._managed_runtime is None:
      return record
    if str(record.status or "").upper() in TERMINAL_PLAN_STATUSES:
      refreshed = await self._load_manual_plan_record(record.plan_id)
      if refreshed is None:
        raise RuntimeError("终态卖出计划重放校验失败")
      return refreshed
    if durable_exit_plan_owner_kind(record) != MANAGED_EXIT_STRATEGY_OWNER:
      raise RuntimeError("独立卖出计划持久化所有权不一致")
    run_id = str(record.strategy_run_id or "")
    binding_error = str(record.last_error or "")
    if not run_id or binding_error.startswith(
      MANAGED_RUNTIME_BINDING_PENDING
    ) or binding_error.startswith(MANAGED_RUNTIME_ENABLE_PENDING):
      raise RuntimeError("卖出计划 StrategyRun 绑定尚未完成")
    desired_enabled = bool(record.enabled)
    strategy_id = await self._strategy_template_id()
    validated_run_id = await self._managed_runtime.validate_current_binding(
      plan_id=str(record.plan_id),
      plan_kind="EXIT",
      account_id=str(record.account_id),
      instrument_code=str(record.instrument_code),
      config_version=int(record.config_version or 0),
      config_snapshot=self._managed_config_snapshot(plan),
      parameters=self._managed_parameters(record, plan),
      strategy_id=strategy_id,
      strategy_class=AshareManagedExitPlanStrategy,
      mode=(
        StrategyRunMode.LIVE
        if str(record.execution_mode or "").lower() == "live"
        else StrategyRunMode.PAPER
      ),
      name=(
        f"卖出托管-{record.instrument_code}"
        if str(command_kind or "").upper() == "CREATE"
        else f"卖出托管-{record.instrument_code}-v{record.config_version}"
      ),
      command_id=command_id,
      state_migration_policy=(
        "INITIAL_EXIT_PLAN_STATE"
        if str(command_kind or "").upper() == "CREATE"
        else "CARRY_EXIT_ALGORITHM_STATE"
      ),
      desired_enabled=desired_enabled,
    )
    if validated_run_id != run_id:
      raise RuntimeError("人工计划与托管计划当前 StrategyRun 不一致")
    async with AsyncSessionLocal() as db:
      duplicate_owner_count = int(
        await db.scalar(
          select(func.count())
          .select_from(AutoExitPlanRecord)
          .where(AutoExitPlanRecord.strategy_run_id == run_id)
        )
        or 0
      )
      if duplicate_owner_count != 1:
        raise RuntimeError("独立卖出 StrategyRun 没有唯一 AutoExitPlan 所有者")
      refreshed = await AutoExitPlanRepository(db).find_by_id(record.plan_id)
    if (
      refreshed is None
      or str(refreshed.strategy_run_id or "") != run_id
      or int(refreshed.config_version or 0) != int(record.config_version or 0)
      or str(refreshed.last_error or "").startswith(
        MANAGED_RUNTIME_BINDING_PENDING
      )
      or str(refreshed.last_error or "").startswith(
        MANAGED_RUNTIME_ENABLE_PENDING
      )
    ):
      raise AutoExitPlanConcurrencyError("退出计划在绑定校验期间发生变化")
    return refreshed

  async def _require_dedicated_manual_update_owner(
    self,
    record: AutoExitPlanRecord,
    *,
    expected_version: int,
    command_id: str,
    command_fingerprint: str,
  ) -> tuple[str, int]:
    """Positively prove this MANUAL_POSITION row is a dedicated managed plan."""

    if self._managed_runtime is None or not record.strategy_run_id:
      raise ValueError(
        "MANUAL_EXIT_PLAN_NOT_DEDICATED:人工计划没有独立 StrategyRun 所有者"
      )
    runtime = (
      self._runtime_manager.get_run(str(record.strategy_run_id))
      if self._runtime_manager is not None
      else None
    )
    if runtime is not None and (
      getattr(runtime, "strategy_class", None) is not AshareManagedExitPlanStrategy
    ):
      raise ValueError(
        "MANUAL_EXIT_PLAN_NOT_DEDICATED:人工计划不属于独立卖出策略"
      )
    plan = ExitPlan.from_dict(dict(record.plan_state or {}))
    metadata = dict(plan.template.metadata or {})
    existing_command_id = str(metadata.get(MANAGED_RUNTIME_COMMAND_ID_KEY) or "")
    existing_command_kind = str(
      metadata.get(MANAGED_RUNTIME_COMMAND_KIND_KEY) or ""
    ).upper()
    if not existing_command_id or existing_command_kind not in {"CREATE", "UPDATE"}:
      raise ValueError(
        "MANUAL_EXIT_PLAN_NOT_DEDICATED:人工计划缺少托管命令绑定"
      )
    current_version = int(record.config_version or 0)
    same_committed_update = current_version == int(expected_version) + 1 and (
      self._managed_command_matches(
        metadata,
        command_id=command_id,
        command_fingerprint=command_fingerprint,
        command_kind="UPDATE",
        previous_config_version=expected_version,
      )
    )
    if same_committed_update:
      managed = await self._managed_runtime.current_plan(record.plan_id)
      if (
        managed is None
        or str(managed.plan_kind or "").upper() != "EXIT"
        or str(managed.account_id or "") != str(record.account_id or "")
        or str(managed.instrument_code or "").upper()
        != str(record.instrument_code or "").upper()
        or int(managed.current_config_version or 0)
        not in {int(expected_version), current_version}
        or (
          int(managed.current_config_version or 0) == int(expected_version)
          and str(managed.current_run_id or "")
          != str(record.strategy_run_id or "")
        )
      ):
        raise ValueError(
          "MANUAL_EXIT_PLAN_NOT_DEDICATED:待恢复计划没有精确托管版本链"
        )
      return str(record.strategy_run_id), current_version
    validated = await self._validate_finalized_managed_runtime_binding(
      record,
      plan,
      command_id=existing_command_id,
      command_kind=existing_command_kind,
    )
    return str(validated.strategy_run_id or ""), int(
      validated.config_version or 0
    )

  async def _converge_replayed_manual_create(
    self,
    record: AutoExitPlanRecord,
    *,
    payload: Mapping[str, Any],
    command_id: str,
    command_fingerprint: str,
  ) -> AutoExitPlanRecord:
    deterministic_plan_id = self._manual_plan_id_for_command(command_id)
    plan = ExitPlan.from_dict(dict(record.plan_state or {}))
    metadata = dict(plan.template.metadata or {})
    if (
      str(record.plan_id or "") != deterministic_plan_id
      or str(record.source_type or "").upper() != MANUAL_PLAN_SOURCE
      or str(record.account_id or "") != str(payload.get("account_id") or "").strip()
      or str(record.instrument_code or "").upper()
      != str(payload.get("instrument_code") or "").strip().upper()
      or int(record.config_version or 0) != 1
      or int(plan.template.config_version or 0) != 1
      or not self._manual_command_matches(
        metadata,
        command_id=command_id,
        command_fingerprint=command_fingerprint,
        command_kind="CREATE",
        previous_config_version=0,
      )
    ):
      raise ValueError("EXIT_COMMAND_REPLAY_CONFLICT:创建命令或计划配置不一致")
    if self._managed_runtime is None:
      if not is_monitor_owned_exit_plan(record):
        raise ValueError("MANUAL_EXIT_PLAN_OWNER_MIGRATION_REQUIRED")
      return record
    if not self._managed_command_matches(
      metadata,
      command_id=command_id,
      command_fingerprint=command_fingerprint,
      command_kind="CREATE",
      previous_config_version=0,
    ):
      raise ValueError("EXIT_COMMAND_REPLAY_CONFLICT:托管创建命令不一致")
    pending = str(record.last_error or "").startswith(
      MANAGED_RUNTIME_BINDING_PENDING
    )
    if record.strategy_run_id and not pending:
      return await self._validate_finalized_managed_runtime_binding(
        record,
        plan,
        command_id=command_id,
        command_kind="CREATE",
      )
    desired_enabled = bool(
      metadata.get(MANAGED_RUNTIME_DESIRED_ENABLED_KEY, False)
    )
    runtime_plan = ExitPlan.from_dict(plan.to_dict())
    if desired_enabled and runtime_plan.status in {
      ExitPlanStatus.PAUSED,
      ExitPlanStatus.ERROR,
    }:
      runtime_plan.status = ExitPlanStatus.ACTIVE
    record.enabled = desired_enabled
    try:
      await self._create_managed_runtime(
        record,
        runtime_plan,
        command_id=command_id,
      )
    except Exception as exc:
      await self._fail_pending_managed_runtime_binding(
        record.plan_id,
        command_id=command_id,
        error=str(exc),
      )
      raise
    refreshed = await self._load_manual_plan_record(record.plan_id)
    if refreshed is None:
      raise RuntimeError("卖出托管创建完成后计划不存在")
    return refreshed

  async def _fail_pending_managed_runtime_binding(
    self,
    plan_id: str,
    *,
    command_id: str,
    error: str,
  ) -> None:
    """Fail closed after an ordinary cross-transaction convergence error."""

    message = str(error or "卖出计划 StrategyRun 绑定失败")[:2000]
    managed_run_id = ""
    if self._managed_runtime is not None:
      try:
        managed = await self._managed_runtime.current_plan(plan_id)
        if managed is not None:
          managed_run_id = str(managed.current_run_id or "")
      except Exception:
        managed_run_id = ""
    async with AsyncSessionLocal() as db:
      record = await AutoExitPlanRepository(db).find_by_id(plan_id, for_update=True)
      if record is None:
        return
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      metadata = dict(plan.template.metadata or {})
      if command_id and str(metadata.get(MANAGED_RUNTIME_COMMAND_ID_KEY) or "") != (
        command_id
      ):
        raise AutoExitPlanConcurrencyError(
          "退出计划命令已变化，拒绝旧命令覆盖失败状态"
        )
      if managed_run_id:
        record.strategy_run_id = managed_run_id
      if plan.status not in {ExitPlanStatus.COMPLETED, ExitPlanStatus.CANCELLED}:
        plan.status = ExitPlanStatus.ERROR
      self._sync_record(record, plan)
      record.enabled = False
      record.last_error = message
      await self._append_event(
        db,
        business_key=(
          f"managed-runtime-binding-failed:{plan_id}:"
          f"{int(record.config_version or 0)}:{command_id}"
        ),
        plan_id=plan_id,
        event_type="MANAGED_RUNTIME_BINDING_FAILED",
        payload={
          "command_id": command_id,
          "config_version": int(record.config_version or 0),
          "strategy_run_id": managed_run_id,
          "error": message,
        },
      )
      await db.commit()
    if self._managed_runtime is not None:
      await self._managed_runtime.set_status(plan_id, "ERROR", error=message)

  async def reconcile_pending_managed_runtime_bindings(
    self,
    *,
    limit: int = 200,
  ) -> dict[str, Any]:
    """Converge crash-window bindings before command consumption starts."""

    if self._managed_runtime is None:
      raise RuntimeError("卖出托管恢复只能由 QuantX Engine 执行")
    async with AsyncSessionLocal() as db:
      rows = list(
        (
          await db.execute(
            select(AutoExitPlanRecord)
            .where(AutoExitPlanRecord.source_type == MANUAL_PLAN_SOURCE)
            .where(
              or_(
                AutoExitPlanRecord.last_error.startswith(
                  MANAGED_RUNTIME_BINDING_PENDING
                ),
                AutoExitPlanRecord.last_error.startswith(
                  MANAGED_RUNTIME_ENABLE_PENDING
                ),
              )
            )
            .order_by(AutoExitPlanRecord.created_at, AutoExitPlanRecord.plan_id)
            .limit(max(1, min(int(limit or 200), 1000)))
          )
        )
        .scalars()
        .all()
      )
    recovered: list[str] = []
    failed: list[dict[str, str]] = []
    for row in rows:
      plan = ExitPlan.from_dict(dict(row.plan_state or {}))
      try:
        enable_marker = self._parse_managed_enable_marker(row.last_error)
      except Exception as exc:
        await self._mark_managed_runtime_failed(
          row.plan_id,
          str(row.strategy_run_id or ""),
          plan=plan,
          error=str(exc),
        )
        failed.append({"plan_id": str(row.plan_id), "error": str(exc)})
        continue
      if enable_marker is not None:
        command_id = str(enable_marker["command_id"])
        desired_enabled = bool(enable_marker["enabled"])
        row.enabled = desired_enabled
        try:
          await self._set_managed_runtime_enabled(row, desired_enabled)
          await self._finalize_managed_runtime_enabled(
            row,
            plan,
            enabled=desired_enabled,
            command_id=command_id,
          )
        except Exception as exc:
          await self._mark_managed_runtime_failed(
            row.plan_id,
            str(row.strategy_run_id or ""),
            plan=plan,
            error=str(exc),
          )
          failed.append({"plan_id": str(row.plan_id), "error": str(exc)})
        else:
          recovered.append(str(row.plan_id))
        continue
      metadata = dict(plan.template.metadata or {})
      command_id = str(metadata.get(MANAGED_RUNTIME_COMMAND_ID_KEY) or "")
      command_kind = str(
        metadata.get(MANAGED_RUNTIME_COMMAND_KIND_KEY) or ""
      ).upper()
      desired_enabled = bool(
        metadata.get(MANAGED_RUNTIME_DESIRED_ENABLED_KEY, False)
      )
      runtime_plan = ExitPlan.from_dict(plan.to_dict())
      if desired_enabled and runtime_plan.status in {
        ExitPlanStatus.PAUSED,
        ExitPlanStatus.ERROR,
      }:
        runtime_plan.status = ExitPlanStatus.ACTIVE
      row.enabled = desired_enabled
      try:
        if command_kind == "CREATE" and int(row.config_version or 0) == 1:
          await self._create_managed_runtime(
            row,
            runtime_plan,
            command_id=command_id,
          )
        elif command_kind == "UPDATE":
          expected_version = int(
            metadata.get(MANAGED_RUNTIME_PREVIOUS_CONFIG_VERSION_KEY) or 0
          )
          if expected_version <= 0 or int(row.config_version or 0) != (
            expected_version + 1
          ):
            raise RuntimeError("卖出计划待恢复更新版本不一致")
          await self._revise_managed_runtime(
            row,
            runtime_plan,
            expected_version=expected_version,
            command_id=command_id,
          )
        else:
          raise RuntimeError("卖出计划待恢复命令类型无效")
      except Exception as exc:
        await self._fail_pending_managed_runtime_binding(
          row.plan_id,
          command_id=command_id,
          error=str(exc),
        )
        failed.append({"plan_id": str(row.plan_id), "error": str(exc)})
      else:
        recovered.append(str(row.plan_id))
    return {
      "examined": len(rows),
      "recovered": recovered,
      "failed": failed,
    }

  async def audit_active_runtime_owned_plans(self) -> dict[str, Any]:
    """Fail if an enabled plan has no exact, live execution owner."""

    if self._runtime_manager is None:
      raise RuntimeError("退出计划运行所有权审计只能由 QuantX Engine 执行")
    async with AsyncSessionLocal() as db:
      rows = list(
        (
          await db.execute(
            select(AutoExitPlanRecord)
            .where(AutoExitPlanRecord.enabled.is_(True))
            .where(AutoExitPlanRecord.status.notin_(list(TERMINAL_PLAN_STATUSES)))
            .order_by(AutoExitPlanRecord.plan_id)
          )
        )
        .scalars()
        .all()
      )
    verified: list[str] = []
    failures: list[ActiveRuntimeExitPlanOwnerAuditFailure] = []
    for record in rows:
      run_id = str(record.strategy_run_id or "").strip()
      owner_kind = durable_exit_plan_owner_kind(record)
      try:
        if owner_kind == INVALID_OWNER:
          raise ValueError(
            "INVALID_DURABLE_OWNER:退出计划持久化身份不一致或没有合法执行所有者"
          )
        if owner_kind == MONITOR_OWNER:
          verified.append(str(record.plan_id))
          continue
        runtime = self._runtime_manager.get_run(run_id)
        if runtime is None:
          raise ValueError("RUNTIME_NOT_RESTORED:StrategyRun 未恢复到 Engine")
        resolved_owner_kind = self._strategy_owner_kind(record)
        runtime_status = str(
          getattr(
            getattr(runtime, "status", None),
            "value",
            getattr(runtime, "status", ""),
          )
          or ""
        ).upper()
        task = getattr(runtime, "task", None)
        if runtime_status not in {"RUNNING", "STARTING"}:
          raise ValueError(
            "RUNTIME_STATUS_INVALID:"
            f"StrategyRun 状态为 {runtime_status or 'UNKNOWN'}"
          )
        if task is None or getattr(task, "done", lambda: True)():
          raise ValueError("RUNTIME_CONSUMER_STOPPED:StrategyRun 消费任务未运行")
        plan = ExitPlan.from_dict(dict(record.plan_state or {}))
        if resolved_owner_kind == MANAGED_EXIT_STRATEGY_OWNER:
          metadata = dict(plan.template.metadata or {})
          command_id = str(metadata.get(MANAGED_RUNTIME_COMMAND_ID_KEY) or "")
          command_kind = str(
            metadata.get(MANAGED_RUNTIME_COMMAND_KIND_KEY) or ""
          ).upper()
          if not command_id or command_kind not in {"CREATE", "UPDATE"}:
            raise ValueError(
              "MANAGED_BINDING_INVALID:独立卖出计划缺少确定性配置命令"
            )
          await self._validate_finalized_managed_runtime_binding(
            record,
            plan,
            command_id=command_id,
            command_kind=command_kind,
          )
        elif resolved_owner_kind == RUNTIME_BOOK_OWNER:
          if (
            plan.plan_id != str(record.plan_id)
            or str(plan.template.run_id or "") != run_id
            or int(plan.template.config_version or 0)
            != int(record.config_version or 0)
          ):
            raise ValueError(
              "RUNTIME_BINDING_INVALID:运行内退出计划持久化绑定不一致"
            )
          runtime_plan = getattr(runtime, "exit_plan_book", None)
          runtime_plan = (
            runtime_plan.plans.get(record.plan_id)
            if runtime_plan is not None
            else None
          )
          if runtime_plan is None or (
            runtime_plan.template.to_dict() != plan.template.to_dict()
          ):
            raise ValueError(
              "RUNTIME_PLAN_NOT_LOADED:StrategyRun 未装载权威退出计划配置"
            )
        else:
          raise ValueError(
            "INVALID_RUNTIME_OWNER:退出计划没有唯一 Engine 所有者"
          )
      except Exception as exc:
        message = str(exc)
        reason_code, separator, detail = message.partition(":")
        reason_code = reason_code.strip()
        if (
          not separator
          or not reason_code
          or len(reason_code) > 64
          or any(
            character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
            for character in reason_code
          )
        ):
          reason_code = "RUNTIME_OWNER_INVALID"
          detail = message
        failures.append(
          ActiveRuntimeExitPlanOwnerAuditFailure(
            plan_id=str(record.plan_id),
            strategy_run_id=run_id,
            account_id=str(record.account_id or ""),
            owner_kind=owner_kind,
            reason_code=reason_code[:64],
            message=(detail or message)[:512],
            stage="runtime",
          )
        )
      else:
        verified.append(str(record.plan_id))
    if failures:
      raise ActiveRuntimeExitPlanOwnerAuditError(failures)
    return {"examined": len(rows), "verified": verified}

  async def preflight_active_runtime_owned_plans(self) -> dict[str, Any]:
    """Reject durable orphan plans before starting any StrategyRun consumer."""

    async with AsyncSessionLocal() as db:
      rows = list(
        (
          await db.execute(
            select(AutoExitPlanRecord)
            .where(AutoExitPlanRecord.enabled.is_(True))
            .where(AutoExitPlanRecord.status.notin_(list(TERMINAL_PLAN_STATUSES)))
            .order_by(AutoExitPlanRecord.plan_id)
          )
        )
        .scalars()
        .all()
      )
      run_ids = {
        str(record.strategy_run_id or "").strip()
        for record in rows
        if str(record.strategy_run_id or "").strip()
      }
      durable_runs = (
        {
          str(run.id): run
          for run in (
            (
              await db.execute(select(StrategyRun).where(StrategyRun.id.in_(run_ids)))
            )
            .scalars()
            .all()
          )
        }
        if run_ids
        else {}
      )

    verified: list[str] = []
    failures: list[ActiveRuntimeExitPlanOwnerAuditFailure] = []
    for record in rows:
      plan_id = str(record.plan_id or "")
      account_id = str(record.account_id or "")
      run_id = str(record.strategy_run_id or "").strip()
      owner_kind = durable_exit_plan_owner_kind(record)
      reason_code = ""
      message = ""
      if owner_kind == INVALID_OWNER:
        reason_code = "INVALID_DURABLE_OWNER"
        message = "退出计划持久化身份不一致或没有合法执行所有者"
      elif owner_kind == MONITOR_OWNER:
        verified.append(plan_id)
        continue
      else:
        durable_run = durable_runs.get(run_id)
        if durable_run is None:
          reason_code = "STRATEGY_RUN_MISSING"
          message = "退出计划绑定的 StrategyRun 持久化记录不存在"
        else:
          raw_status = getattr(durable_run, "status", "")
          run_status = str(getattr(raw_status, "value", raw_status) or "").upper()
          if run_status != StrategyRunStatus.RUNNING.value.upper():
            reason_code = "STRATEGY_RUN_NOT_RUNNING"
            message = f"退出计划绑定的 StrategyRun 状态为 {run_status or 'UNKNOWN'}"
      if reason_code:
        failures.append(
          ActiveRuntimeExitPlanOwnerAuditFailure(
            plan_id=plan_id,
            strategy_run_id=run_id,
            account_id=account_id,
            owner_kind=owner_kind,
            reason_code=reason_code,
            message=message,
            stage="preflight",
          )
        )
      else:
        verified.append(plan_id)
    if failures:
      raise ActiveRuntimeExitPlanOwnerAuditError(failures)
    return {"examined": len(rows), "verified": verified}

  async def create_manual_exit_plan(
    self,
    payload: Mapping[str, Any],
    *,
    command_id: str = "",
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
    normalized_command_id = self._manual_command_id(command_id)
    command_fingerprint = self._manual_command_fingerprint(payload)
    deterministic_plan_id = self._manual_plan_id_for_command(normalized_command_id)
    requested_plan_id = str(payload.get("plan_id") or "").strip()
    if deterministic_plan_id and requested_plan_id and (
      requested_plan_id != deterministic_plan_id
    ):
      raise ValueError("EXIT_COMMAND_REPLAY_CONFLICT:创建计划标识与命令不一致")
    plan_id = deterministic_plan_id or requested_plan_id or (
      f"manual-position:{uuid.uuid4()}"
    )
    if normalized_command_id:
      existing = await self._load_manual_plan_record(plan_id)
      if existing is not None:
        return await self._converge_replayed_manual_create(
          existing,
          payload=payload,
          command_id=normalized_command_id,
          command_fingerprint=command_fingerprint,
        )
    async with AsyncSessionLocal() as db:
      scope = await lock_exit_plan_scope(
        db,
        account_id=account_id,
        instrument_code=instrument_code,
        target_plan_id=plan_id,
        execution_mode=self._execution_mode(payload.get("execution_mode")),
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
      rules = self._rules_from_payload(plan_id, payload.get("rules"))
      desired_enabled = bool(payload.get("enabled", True))
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
          MANUAL_COMMAND_ID_KEY: normalized_command_id,
          MANUAL_COMMAND_FINGERPRINT_KEY: command_fingerprint,
          MANUAL_COMMAND_KIND_KEY: "CREATE",
          MANUAL_COMMAND_PREVIOUS_CONFIG_VERSION_KEY: 0,
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
        strategy_run_id=None,
        enabled=desired_enabled,
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
        last_error=None,
        plan_state={},
      )
      if not desired_enabled:
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
          "command_id": normalized_command_id,
          "command_fingerprint": command_fingerprint,
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
      created_record = record
    return created_record

  async def _update_monitor_owned_manual_exit_plan(
    self,
    payload: Mapping[str, Any],
    *,
    command_id: str = "",
  ) -> AutoExitPlanRecord:
    """Update a monitor-owned manual plan in one durable transaction."""

    if bool(payload.get("auto_exit_authorized", False)):
      raise ValueError(
        "AUTO_EXIT_AUTHORIZATION_REQUIRES_CHALLENGE: 布尔字段不能开启自动实盘退出"
      )
    plan_id = str(payload.get("plan_id") or "").strip()
    expected_version = int(payload.get("config_version") or 0)
    if not plan_id or expected_version <= 0:
      raise ValueError("CONFIG_VERSION_CONFLICT: current=0")
    normalized_command_id = self._manual_command_id(command_id)
    command_fingerprint = self._manual_command_fingerprint(payload)

    async with AsyncSessionLocal() as db:
      initial = await AutoExitPlanRepository(db).find_by_id(plan_id)
      if initial is None:
        raise ValueError("退出计划不存在")
      requested_account_id = str(payload.get("account_id") or "").strip()
      if requested_account_id and requested_account_id != initial.account_id:
        raise ValueError("退出计划不属于当前账户")
      if str(initial.source_type or "").upper() != MANUAL_PLAN_SOURCE:
        raise ValueError("业务来源计划请返回原业务页面修改规则")
      if str(initial.strategy_run_id or "").strip():
        raise ValueError("MANUAL_EXIT_PLAN_OWNER_MIGRATION_REQUIRED")

      scope = await lock_exit_plan_scope(
        db,
        account_id=str(initial.account_id),
        instrument_code=str(initial.instrument_code).upper(),
        target_plan_id=plan_id,
      )
      record = scope.plan(plan_id)
      if record is None:
        raise AutoExitPlanConcurrencyError("退出计划在更新前消失")
      if not is_monitor_owned_exit_plan(record):
        raise AutoExitPlanConcurrencyError("退出计划执行所有者在更新前发生变化")
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      metadata = dict(plan.template.metadata or {})
      current_version = int(record.config_version or 0)
      if current_version == expected_version + 1:
        if not self._manual_command_matches(
          metadata,
          command_id=normalized_command_id,
          command_fingerprint=command_fingerprint,
          command_kind="UPDATE",
          previous_config_version=expected_version,
        ):
          raise ValueError(f"CONFIG_VERSION_CONFLICT: current={current_version}")
        return record
      if current_version != expected_version:
        raise ValueError(f"CONFIG_VERSION_CONFLICT: current={current_version}")
      if plan.status == ExitPlanStatus.EXIT_PENDING or plan.pending_order_id:
        raise ValueError("已有卖出委托待成交，暂不能修改计划")
      if plan.status in {ExitPlanStatus.COMPLETED, ExitPlanStatus.CANCELLED}:
        raise ValueError("已完成或已取消的计划不能修改")

      protected_volume = int(
        payload.get("protected_volume", record.protected_volume)
        or record.protected_volume
        or 0
      )
      exited_volume = max(0, int(plan.exited_volume or 0))
      if protected_volume < exited_volume:
        raise ValueError(f"保护数量不能小于已卖数量 {exited_volume} 股")
      desired_remaining = protected_volume - exited_volume
      other_reserved = sum(
        max(0, int(item.remaining_volume or 0))
        for item in scope.plans
        if item.plan_id != plan_id
      )
      holding_volume = int(scope.position.volume or 0) if scope.position else 0
      available_to_plan = max(0, holding_volume - other_reserved)
      if desired_remaining <= 0 or desired_remaining > available_to_plan:
        raise ValueError(
          "可认领数量不足："
          f"当前计划最多可保护 {available_to_plan + exited_volume} 股，"
          f"申请 {protected_volume} 股"
        )
      if (
        plan.cost_basis.mode == ExitCostBasisMode.BROKER_BUY_ORDERS
        and protected_volume > plan.cost_basis.basis_volume
      ):
        raise ValueError(
          f"计划卖出数量不能超过已选成交委托数量 {plan.cost_basis.basis_volume} 股"
        )

      next_version = current_version + 1
      next_metadata = dict(metadata)
      for key in (
        MANAGED_RUNTIME_COMMAND_ID_KEY,
        MANAGED_RUNTIME_COMMAND_FINGERPRINT_KEY,
        MANAGED_RUNTIME_COMMAND_KIND_KEY,
        MANAGED_RUNTIME_PREVIOUS_CONFIG_VERSION_KEY,
        MANAGED_RUNTIME_DESIRED_ENABLED_KEY,
      ):
        next_metadata.pop(key, None)
      next_metadata.update(
        {
          "remark": str(payload.get("remark") or ""),
          MANUAL_COMMAND_ID_KEY: normalized_command_id,
          MANUAL_COMMAND_FINGERPRINT_KEY: command_fingerprint,
          MANUAL_COMMAND_KIND_KEY: "UPDATE",
          MANUAL_COMMAND_PREVIOUS_CONFIG_VERSION_KEY: expected_version,
        }
      )
      template = self._template(
        plan_id=plan_id,
        source_type=str(record.source_type),
        source_id=str(record.source_id),
        account_id=str(record.account_id),
        instrument_code=str(record.instrument_code),
        bucket=str(record.bucket),
        rules=self._rules_from_payload(plan_id, payload.get("rules")),
        config_version=next_version,
        metadata=next_metadata,
        auto_exit_authorized=False,
      )
      plan.apply_template(template)
      plan.entry_filled_volume = protected_volume
      if plan.status not in {ExitPlanStatus.COMPLETED, ExitPlanStatus.CANCELLED}:
        plan.status = (
          ExitPlanStatus.ACTIVE if record.enabled else ExitPlanStatus.PAUSED
        )
      record.config_version = next_version
      record.strategy_run_id = None
      record.protected_volume = protected_volume
      record.remaining_volume = desired_remaining
      if self._execution_mode(payload.get("execution_mode", record.execution_mode)) != record.execution_mode:
        raise ValueError("执行环境创建后不可切换，请为新环境创建独立计划")
      clear_exact_auto_exit_authorization(record, bump_state_version=False)
      self._sync_record(record, plan)
      record.last_error = None
      await self._append_event(
        db,
        business_key=f"plan-updated:{plan_id}:{next_version}",
        plan_id=plan_id,
        event_type="PLAN_UPDATED",
        payload={
          "config_version": next_version,
          "protected_volume": protected_volume,
          "command_id": normalized_command_id,
          "command_fingerprint": command_fingerprint,
          "execution_owner": "EXIT_PLAN_MONITOR",
        },
      )
      await db.commit()
      await db.refresh(record)
      return record

  async def update_manual_exit_plan(
    self,
    payload: Mapping[str, Any],
    *,
    command_id: str = "",
  ) -> AutoExitPlanRecord:
    return await self._update_monitor_owned_manual_exit_plan(
      payload,
      command_id=command_id,
    )

  async def _update_dedicated_managed_manual_exit_plan(
    self,
    payload: Mapping[str, Any],
    *,
    command_id: str = "",
  ) -> AutoExitPlanRecord:
    if bool(payload.get("auto_exit_authorized", False)):
      raise ValueError(
        "AUTO_EXIT_AUTHORIZATION_REQUIRES_CHALLENGE: 布尔字段不能开启自动实盘退出"
      )
    plan_id = str(payload.get("plan_id") or "")
    expected_version = int(payload.get("config_version") or 0)
    if expected_version <= 0:
      raise ValueError("CONFIG_VERSION_CONFLICT: current=0")
    normalized_command_id = self._managed_command_id(command_id, required=True)
    command_fingerprint = self._manual_command_fingerprint(payload)
    desired_enabled = False
    runtime_plan: Optional[ExitPlan] = None
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

      initial_account_id = str(record.account_id or "")
      initial_instrument_code = str(record.instrument_code or "").upper()
      initial_plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      initial_metadata = dict(initial_plan.template.metadata or {})
      owner_run_id, owner_version = (
        await self._require_dedicated_manual_update_owner(
          record,
          expected_version=expected_version,
          command_id=normalized_command_id,
          command_fingerprint=command_fingerprint,
        )
      )
      initial_replayed_commit = int(record.config_version or 0) == (
        expected_version + 1
      ) and self._managed_command_matches(
        initial_metadata,
        command_id=normalized_command_id,
        command_fingerprint=command_fingerprint,
        command_kind="UPDATE",
        previous_config_version=expected_version,
      )
      if (
        initial_replayed_commit
        and not str(record.last_error or "")
        and record.strategy_run_id
      ):
        # A completed outbox replay is read-only.  In particular, never feed
        # this command's earlier plan snapshot back through _bind_strategy_run:
        # ORDER/TRADE convergence may already have advanced dynamic state.
        return await self._validate_finalized_managed_runtime_binding(
          record,
          initial_plan,
          command_id=normalized_command_id,
          command_kind="UPDATE",
        )

      scope = await lock_exit_plan_scope(
        db,
        account_id=initial_account_id,
        instrument_code=initial_instrument_code,
        target_plan_id=plan_id,
      )
      position = scope.position
      reserving = scope.plans
      locked_record = scope.plan(plan_id)
      if locked_record is None:
        raise AutoExitPlanConcurrencyError("退出计划在更新前消失")
      record = locked_record
      await db.refresh(record)
      if (
        str(record.source_type or "").upper() != MANUAL_PLAN_SOURCE
        or str(record.account_id or "") != initial_account_id
        or str(record.instrument_code or "").upper() != initial_instrument_code
        or str(record.strategy_run_id or "") != owner_run_id
        or int(record.config_version or 0) != owner_version
      ):
        raise AutoExitPlanConcurrencyError(
          "退出计划执行所有者或配置在更新前发生变化"
        )
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      metadata = dict(plan.template.metadata or {})
      if (
        str(metadata.get(MANAGED_RUNTIME_COMMAND_ID_KEY) or "")
        != str(initial_metadata.get(MANAGED_RUNTIME_COMMAND_ID_KEY) or "")
        or str(metadata.get(MANAGED_RUNTIME_COMMAND_FINGERPRINT_KEY) or "")
        != str(
          initial_metadata.get(MANAGED_RUNTIME_COMMAND_FINGERPRINT_KEY) or ""
        )
        or str(metadata.get(MANAGED_RUNTIME_COMMAND_KIND_KEY) or "").upper()
        != str(initial_metadata.get(MANAGED_RUNTIME_COMMAND_KIND_KEY) or "").upper()
        or int(metadata.get(MANAGED_RUNTIME_PREVIOUS_CONFIG_VERSION_KEY) or 0)
        != int(
          initial_metadata.get(MANAGED_RUNTIME_PREVIOUS_CONFIG_VERSION_KEY) or 0
        )
      ):
        raise AutoExitPlanConcurrencyError(
          "退出计划托管命令在更新前发生变化"
        )
      replayed_commit = False
      if int(record.config_version or 0) == expected_version + 1:
        if not self._managed_command_matches(
          metadata,
          command_id=normalized_command_id,
          command_fingerprint=command_fingerprint,
          command_kind="UPDATE",
          previous_config_version=expected_version,
        ):
          raise ValueError(
            f"CONFIG_VERSION_CONFLICT: current={record.config_version}"
          )
        replayed_commit = True
      elif int(record.config_version or 0) != expected_version:
        raise ValueError(f"CONFIG_VERSION_CONFLICT: current={record.config_version}")
      if replayed_commit:
        if not isinstance(
          metadata.get(MANAGED_RUNTIME_DESIRED_ENABLED_KEY), bool
        ):
          raise ValueError(
            "MANUAL_EXIT_PLAN_NOT_DEDICATED:待恢复计划缺少目标启用状态"
          )
        desired_enabled = bool(metadata[MANAGED_RUNTIME_DESIRED_ENABLED_KEY])
        runtime_plan = ExitPlan.from_dict(plan.to_dict())
        updated_record = record
      else:
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
            f"当前计划最多可保护 "
            f"{available_to_plan + int(plan.exited_volume or 0)} 股，"
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
        desired_enabled = bool(record.enabled)
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
            MANUAL_COMMAND_ID_KEY: normalized_command_id,
            MANUAL_COMMAND_FINGERPRINT_KEY: command_fingerprint,
            MANUAL_COMMAND_KIND_KEY: "UPDATE",
            MANUAL_COMMAND_PREVIOUS_CONFIG_VERSION_KEY: expected_version,
            MANAGED_RUNTIME_DESIRED_ENABLED_KEY: desired_enabled,
            MANAGED_RUNTIME_COMMAND_ID_KEY: normalized_command_id,
            MANAGED_RUNTIME_COMMAND_FINGERPRINT_KEY: command_fingerprint,
            MANAGED_RUNTIME_COMMAND_KIND_KEY: "UPDATE",
            MANAGED_RUNTIME_PREVIOUS_CONFIG_VERSION_KEY: expected_version,
          },
          auto_exit_authorized=False,
        )
        plan.apply_template(template)
        plan.entry_filled_volume = protected_volume
        if plan.status not in {ExitPlanStatus.COMPLETED, ExitPlanStatus.CANCELLED}:
          plan.status = (
            ExitPlanStatus.ACTIVE if desired_enabled else ExitPlanStatus.PAUSED
          )
        record.config_version = next_version
        record.protected_volume = protected_volume
        record.remaining_volume = desired_remaining
        if self._execution_mode(payload.get("execution_mode", record.execution_mode)) != record.execution_mode:
          raise ValueError("执行环境创建后不可切换，请为新环境创建独立计划")
        clear_exact_auto_exit_authorization(record, bump_state_version=False)
        runtime_plan = ExitPlan.from_dict(plan.to_dict())
        pending_plan = ExitPlan.from_dict(plan.to_dict())
        if pending_plan.status not in {
          ExitPlanStatus.COMPLETED,
          ExitPlanStatus.CANCELLED,
        }:
          pending_plan.status = ExitPlanStatus.PAUSED
        self._sync_record(record, pending_plan)
        record.last_error = self._managed_binding_marker(
          normalized_command_id,
          next_version,
        )
        await self._append_event(
          db,
          business_key=f"plan-updated:{plan_id}:{next_version}",
          plan_id=plan_id,
          event_type="PLAN_UPDATED",
          payload={
            "config_version": next_version,
            "protected_volume": protected_volume,
            "command_id": normalized_command_id,
            "command_fingerprint": command_fingerprint,
          },
        )
        await db.commit()
        await db.refresh(record)
        updated_record = record
    if runtime_plan is None:
      raise RuntimeError("卖出托管更新缺少运行计划")
    if desired_enabled and runtime_plan.status in {
      ExitPlanStatus.PAUSED,
      ExitPlanStatus.ERROR,
    }:
      runtime_plan.status = ExitPlanStatus.ACTIVE
    elif not desired_enabled and runtime_plan.status not in {
      ExitPlanStatus.COMPLETED,
      ExitPlanStatus.CANCELLED,
    }:
      runtime_plan.status = ExitPlanStatus.PAUSED
    updated_record.enabled = desired_enabled
    try:
      await self._revise_managed_runtime(
        updated_record,
        runtime_plan,
        expected_version=expected_version,
        command_id=normalized_command_id,
      )
    except Exception as exc:
      await self._fail_pending_managed_runtime_binding(
        updated_record.plan_id,
        command_id=normalized_command_id,
        error=str(exc),
      )
      raise
    refreshed = await self._load_manual_plan_record(updated_record.plan_id)
    if refreshed is None:
      raise RuntimeError("卖出托管修订完成后计划不存在")
    return refreshed

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
      if execution_mode == "live":
        # Batch liquidation claims the same account capacity as every other
        # LIVE order/plan writer, before taking any instrument or plan lock.
        await db.get(AccountExecutionControl, account_id, with_for_update=True)
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
      )
      if execution_mode == "live":
        position_stmt = position_stmt.with_for_update()
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
      if native_confirmation and execution_mode == "live":
        pending_sell_stmt = (
          select(PendingTradeOrder)
          .where(PendingTradeOrder.account_id == account_id)
          .where(PendingTradeOrder.execution_mode == "live")
          .where(PendingTradeOrder.side == "SELL")
          .where(PendingTradeOrder.status.in_(ACTIVE_ORDER_STATUSES))
          .with_for_update()
        )
        if scope == "SELECTED":
          pending_sell_stmt = pending_sell_stmt.where(
            PendingTradeOrder.instrument_code.in_(selected)
          )
        pending_sell_rows = list(
          (await db.execute(pending_sell_stmt)).scalars().all()
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
          execution_mode=execution_mode,
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
            clear_exact_auto_exit_authorization(existing, bump_state_version=False)
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
        clear_exact_auto_exit_authorization(record, bump_state_version=False)
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
    command_id: str = "",
  ) -> Optional[AutoExitPlanRecord]:
    if not plan_id:
      return
    async with AsyncSessionLocal() as db:
      owner = await AutoExitPlanRepository(db).find_by_id(plan_id)
    if owner is not None and enabled:
      owner_plan = ExitPlan.from_dict(dict(owner.plan_state or {}))
      if _is_sticky_exit_plan_error(owner_plan.error_message):
        raise ValueError(
          "EXIT_PLAN_RECONCILIATION_REQUIRED:退出计划必须先完成券商事实对账"
        )
    owner_kind = self._strategy_owner_kind(owner) if owner is not None else "MONITOR"
    if owner is not None and owner_kind == "RUNTIME_BOOK":
      if account_id and owner.account_id != account_id:
        raise ValueError("退出计划不属于当前账户")
      if config_version is not None and int(owner.config_version) != int(
        config_version
      ):
        raise ValueError(f"CONFIG_VERSION_CONFLICT: current={owner.config_version}")
      if self._runtime_manager is None:
        raise RuntimeError("策略所属退出计划只能由 QuantX Engine 串行修改")
      await self._runtime_manager.executor.command_exit_plan(
        str(owner.strategy_run_id),
        ExitPlanCommand(
          command=(
            ExitPlanCommandType.RESUME if enabled else ExitPlanCommandType.PAUSE
          ),
          plan_id=str(owner.plan_id),
          reason="USER_RESUMED" if enabled else "USER_PAUSED",
        ),
        account_id=str(owner.account_id),
        config_version=int(owner.config_version),
      )
      async with AsyncSessionLocal() as db:
        return await AutoExitPlanRepository(db).find_by_id(plan_id)
    async with AsyncSessionLocal() as db:
      record = await AutoExitPlanRepository(db).find_by_id(plan_id, for_update=True)
      if record is None:
        return None
      if self._strategy_owner_kind(record) != owner_kind:
        raise RuntimeError("退出计划执行归属已变化，请重试")
      if account_id and record.account_id != account_id:
        raise ValueError("退出计划不属于当前账户")
      if config_version is not None and int(record.config_version) != int(
        config_version
      ):
        raise ValueError(f"CONFIG_VERSION_CONFLICT: current={record.config_version}")
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      if enabled and _is_sticky_exit_plan_error(plan.error_message):
        raise ValueError(
          "EXIT_PLAN_RECONCILIATION_REQUIRED:退出计划必须先完成券商事实对账"
        )
      if plan.status in {ExitPlanStatus.COMPLETED, ExitPlanStatus.CANCELLED}:
        raise ValueError("终态退出计划不能启停")
      if enabled and plan.remaining_volume > 0:
        plan.status = ExitPlanStatus.ACTIVE
      elif not enabled:
        if plan.status == ExitPlanStatus.EXIT_PENDING or plan.pending_order_id:
          raise ValueError("已有卖出委托待成交，暂不能暂停")
        plan.status = ExitPlanStatus.PAUSED
      record.enabled = bool(enabled)
      clear_exact_auto_exit_authorization(record, bump_state_version=False)
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
      updated_record = record
    return updated_record

  async def evaluate_now(
    self,
    plan_id: str,
    *,
    account_id: str,
  ) -> dict[str, Any]:
    if self._runtime_manager is None:
      raise RuntimeError("卖出计划即时检查只能由 QuantX Engine 执行")
    async with AsyncSessionLocal() as db:
      record = await AutoExitPlanRepository(db).find_by_id(plan_id)
    if record is None or (account_id and record.account_id != account_id):
      raise ValueError("退出计划不存在或不属于当前账户")
    if not record.strategy_run_id:
      raise ValueError("退出计划尚未绑定 StrategyRun")
    owner_kind = self._strategy_owner_kind(record)
    runtime = self._runtime_manager.get_run(str(record.strategy_run_id))
    if runtime is None or not bool(record.enabled):
      raise ValueError("退出计划未在监控，不能立即检查")
    instrument_code = str(record.instrument_code or "").upper()
    market_data = runtime.latest_market_data.get(instrument_code)
    if market_data is None:
      raise ValueError("最新权威行情不可用，不能立即检查")
    if owner_kind == "RUNTIME_BOOK":
      await self._runtime_manager.executor.evaluate_exit_plan_now(
        str(record.strategy_run_id),
        plan_id=str(record.plan_id),
        account_id=str(record.account_id),
        config_version=int(record.config_version),
        instrument_code=instrument_code,
        market_data=market_data,
      )
    else:
      raise RuntimeError("退出计划执行所有者不支持即时检查")
    return {
      "success": True,
      "code": "EXIT_PLAN_EVALUATION_QUEUED",
      "plan_id": plan_id,
      "run_id": str(record.strategy_run_id),
    }

  async def confirm_managed_intent(
    self,
    *,
    plan_id: str,
    intent_id: str,
    approval_audit: Optional[Mapping[str, Any]] = None,
  ) -> dict[str, Any]:
    if self._runtime_manager is None:
      raise RuntimeError("卖出意图确认只能由 QuantX Engine 执行")
    async with AsyncSessionLocal() as db:
      record = await AutoExitPlanRepository(db).find_by_id(plan_id)
    if record is None or not record.strategy_run_id:
      raise ValueError("退出计划或 StrategyRun 不存在")
    self._strategy_owner_kind(record)
    await self.validate_exit_plan_sell_approval(
      plan_id=str(record.plan_id),
      intent_id=intent_id,
      account_id=str(record.account_id),
      approval_audit=approval_audit,
    )
    result = await self._runtime_manager.executor.approve_trade_intent(
      str(record.strategy_run_id),
      intent_id,
      expected_exit_plan_id=str(record.plan_id),
      approval_audit=approval_audit,
    )
    if not result.get("success"):
      raise ValueError(str(result.get("message") or result.get("code") or "确认失败"))
    return dict(result)

  async def reject_managed_intent(
    self,
    *,
    plan_id: str,
    intent_id: str,
    reason: str,
  ) -> dict[str, Any]:
    if self._runtime_manager is None:
      raise RuntimeError("卖出意图忽略只能由 QuantX Engine 执行")
    async with AsyncSessionLocal() as db:
      record = await AutoExitPlanRepository(db).find_by_id(plan_id)
    if record is None or not record.strategy_run_id:
      raise ValueError("退出计划或 StrategyRun 不存在")
    self._strategy_owner_kind(record)
    result = await self._runtime_manager.executor.reject_trade_intent(
      str(record.strategy_run_id),
      intent_id,
      reason,
      expected_exit_plan_id=str(record.plan_id),
    )
    if not result.get("success"):
      raise ValueError(str(result.get("message") or result.get("code") or "忽略失败"))
    return dict(result)

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
      owner = await AutoExitPlanRepository(db).find_by_id(plan_id)
    owner_kind = self._strategy_owner_kind(owner) if owner is not None else "MONITOR"
    if owner is not None and owner_kind == "RUNTIME_BOOK":
      if account_id and owner.account_id != account_id:
        raise ValueError("退出计划不属于当前账户")
      if config_version is not None and int(owner.config_version) != int(
        config_version
      ):
        raise ValueError(f"CONFIG_VERSION_CONFLICT: current={owner.config_version}")
      if self._runtime_manager is None:
        raise RuntimeError("策略所属退出计划只能由 QuantX Engine 串行修改")
      await self._runtime_manager.executor.command_exit_plan(
        str(owner.strategy_run_id),
        ExitPlanCommand(
          command=ExitPlanCommandType.CANCEL,
          plan_id=str(owner.plan_id),
          reason=str(reason or "USER_CANCELLED"),
        ),
        account_id=str(owner.account_id),
        config_version=int(owner.config_version),
      )
      async with AsyncSessionLocal() as db:
        return await AutoExitPlanRepository(db).find_by_id(plan_id)
    async with AsyncSessionLocal() as db:
      record = await AutoExitPlanRepository(db).find_by_id(plan_id, for_update=True)
      if record is None:
        return None
      if self._strategy_owner_kind(record) != owner_kind:
        raise RuntimeError("退出计划执行归属已变化，请重试")
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
      clear_exact_auto_exit_authorization(record, bump_state_version=False)
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
      cancelled_record = record
    return cancelled_record

  async def evaluate_and_submit(
    self,
    *,
    plan_id: str,
    context: ExitEvaluationContext,
    position: Optional[Position],
    market_session_open: bool,
    market_ready: Optional[Callable[[], bool]] = None,
  ) -> Optional[dict[str, Any]]:
    reserved_submission = None
    async with AsyncSessionLocal() as db:
      scope = await lock_exit_plan_scope_for_plan(db, plan_id)
      record = scope.plan(plan_id)
      if (
        record is None
        or not record.enabled
        or str(record.strategy_run_id or "").strip()
      ):
        return None
      if not is_monitor_owned_exit_plan(record):
        raise RuntimeError("EXIT_PLAN_OWNER_INVALID:全局 Monitor 无权执行该退出计划")
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      if plan.pending_intent_id:
        reserved_intent = await db.get(TradeIntentRecord, plan.pending_intent_id)
        if reserved_intent is not None and str(
          reserved_intent.status or ""
        ).upper() == "RESERVED":
          pending_rule = next(
            (
              rule
              for rule in plan.template.rules
              if str(rule.rule_id) == str(plan.pending_rule_id)
            ),
            None,
          )
          if pending_rule is None or int(plan.pending_requested_volume or 0) <= 0:
            raise RuntimeError("退出计划保留意图缺少可恢复的规则和数量")
          recovered_decision = ExitDecision(
            plan_id=str(plan.plan_id),
            rule_id=str(pending_rule.rule_id),
            rule_type=str(pending_rule.strategy),
            reason=str(plan.last_exit_reason or "RECOVERED_EXIT_INTENT"),
            volume=int(plan.pending_requested_volume),
            priority=int(pending_rule.priority),
            metrics={"recovered_atomic_reservation": True},
          )
          reserved_submission = (
            record,
            recovered_decision,
            str(plan.pending_intent_id),
            self._protected_sell_price(context, record),
          )
          record.last_error = "exit_intent_reserved_for_recovery"
          recovered = True
        else:
          recovered = await self._recover_pending_submission(db, record, plan)
        self._sync_record(record, plan, evaluated_at=context.timestamp)
        await self._sync_source_order(
          db,
          record,
          plan,
          checked_at=context.timestamp,
        )
        await db.commit()
        if reserved_submission is not None:
          (
            reserved_record,
            recovered_decision,
            reserved_intent_id,
            reserved_price,
          ) = reserved_submission
          return await self._route_reserved_exit_intent(
            plan_id=plan_id,
            record=reserved_record,
            decision=recovered_decision,
            intent_id=reserved_intent_id,
            context=context,
            position=position,
            price=reserved_price,
            market_ready=market_ready,
          )
        elif recovered or plan.pending_intent_id:
          return None
      if plan.status not in {
        ExitPlanStatus.ACTIVE,
        ExitPlanStatus.PARTIALLY_EXITED,
      }:
        return None
      if not market_session_open:
        await self._persist_market_session_closed(
          db,
          record,
          plan,
          evaluated_at=context.timestamp,
        )
        return None
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
      self._clear_market_gate_error(record)
      decision = ExitPlanBook([plan]).evaluator.evaluate(plan, context)
      self._sync_record(record, plan, evaluated_at=context.timestamp)
      await self._sync_source_order(db, record, plan, checked_at=context.timestamp)
      await db.commit()
      expected_config_version = int(record.config_version or 0)
      expected_state_version = max(1, int(record.state_version or 1))
    if decision is None:
      return None
    return await self._submit_decision(
      plan_id=plan_id,
      decision=decision,
      context=context,
      position=position,
      market_ready=market_ready,
      expected_config_version=expected_config_version,
      expected_state_version=expected_state_version,
    )

  async def apply_order_event_for_report(
    self,
    *,
    client_order_id: str = "",
    broker_order_id: str = "",
    status: str,
    source_sequence: int = 0,
    cumulative_filled_volume: Optional[int] = None,
    cumulative_fill_state: str = "MISSING",
  ) -> None:
    cumulative_fill, fill_state = _normalize_cumulative_fill_report(
      cumulative_filled_volume,
      cumulative_fill_state,
    )
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
      if record is None or str(record.strategy_run_id or "").strip():
        # Runtime-owned plans consume their durable ORDER event on the owning
        # StrategyRun queue.  Updating them here as well would double-apply the
        # same broker fact and force the runtime into a permanent CAS conflict.
        return
      if not is_monitor_owned_exit_plan(record):
        raise RuntimeError("EXIT_PLAN_OWNER_INVALID:全局 Monitor 无权消费该委托回报")
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      ExitPlanBook([plan]).apply_order_event(
        plan_id=plan_id,
        intent_id=str((pending.request_metadata or {}).get("intent_id") or ""),
        status=status,
        order_id=broker_order_id or client_order_id,
        timestamp_ms=int(time_utils.now().timestamp() * 1000),
        cumulative_filled_volume=cumulative_fill,
      )
      self._sync_record(record, plan)
      fill_component = (
        str(int(cumulative_fill))
        if fill_state == "VALUE" and cumulative_fill is not None
        else fill_state
      )
      event_key = (
        f"order:{plan_id}:{client_order_id or broker_order_id}:"
        f"{source_sequence}:{str(status).upper()}:{fill_component}"
      )
      await self._append_event(
        db,
        business_key=event_key,
        plan_id=plan_id,
        event_type="ORDER_STATE",
        payload={
          "status": status,
          "broker_order_id": broker_order_id,
          "cumulative_filled_volume": cumulative_fill,
          "cumulative_fill_state": fill_state,
        },
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
      if record is None or str(record.strategy_run_id or "").strip():
        # See apply_order_event_for_report: one plan has exactly one report
        # consumer. Only positively classified manual plans remain owned by
        # ExitPlanMonitor.
        return
      if not is_monitor_owned_exit_plan(record):
        raise RuntimeError("EXIT_PLAN_OWNER_INVALID:全局 Monitor 无权消费该成交回报")
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      ExitPlanBook([plan]).apply_exit_fill(
        plan_id=plan_id,
        volume=volume,
        price=price,
        rule_id=str((pending.request_metadata or {}).get("exit_rule_id") or ""),
        intent_id=str((pending.request_metadata or {}).get("intent_id") or ""),
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
    market_session_open: bool,
    market_ready: Optional[Callable[[], bool]] = None,
    approval_audit: Optional[Mapping[str, Any]] = None,
  ) -> dict[str, Any]:
    async with AsyncSessionLocal() as db:
      scope = await lock_exit_plan_scope_for_plan(db, plan_id)
      record = scope.plan(plan_id)
      intent = await db.get(TradeIntentRecord, intent_id)
      if record is None or intent is None:
        raise ValueError("退出计划或卖出意图不存在")
      if not is_monitor_owned_exit_plan(record):
        raise ValueError("EXIT_PLAN_OWNER_CHANGED")
      await validate_consumed_exit_plan_sell_challenge(
        db,
        plan_id=plan_id,
        intent_id=intent_id,
        account_id=str(record.account_id),
        approval_audit=approval_audit,
      )
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      if plan.pending_intent_id != intent_id:
        raise ValueError("卖出意图已变化，请刷新后重试")
      if not market_session_open:
        await self._persist_market_session_closed(
          db,
          record,
          plan,
          evaluated_at=context.timestamp,
        )
        return {
          "success": False,
          "code": MARKET_SESSION_CLOSED,
          "error": MARKET_SESSION_CLOSED,
        }
      market_error = self._market_context_error(context, market_ready)
      if market_error:
        intent.status = "REJECTED"
        intent.notes = market_error
        intent.intent_metadata = local_pre_broker_zero_fill_metadata(
          {
            **dict(intent.intent_metadata or {}),
            "market_data_gate": market_error,
          },
          reason=market_error,
        )
        ExitPlanBook([plan]).apply_order_event(
          plan_id=plan_id,
          intent_id=intent_id,
          status="RECONCILED_ZERO_FILL",
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
      approval_audit=approval_audit,
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
      if not is_monitor_owned_exit_plan(stored):
        raise RuntimeError("EXIT_PLAN_OWNER_CHANGED_AFTER_SUBMISSION")
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

  async def _persist_market_session_closed(
    self,
    db,
    record: AutoExitPlanRecord,
    plan: ExitPlan,
    *,
    evaluated_at: datetime,
  ) -> None:
    self._sync_record(record, plan, evaluated_at=evaluated_at)
    record.data_quality = MARKET_SESSION_CLOSED
    self._clear_market_gate_error(record)
    await self._sync_source_order(
      db,
      record,
      plan,
      checked_at=evaluated_at,
    )
    await db.commit()

  @staticmethod
  def _clear_market_gate_error(record: AutoExitPlanRecord) -> None:
    if str(record.last_error or "").strip().upper() in MARKET_GATE_ERROR_CODES:
      record.last_error = None

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
      if not is_monitor_owned_exit_plan(record):
        raise ValueError("EXIT_PLAN_OWNER_CHANGED")
      metadata = dict(intent.intent_metadata or {})
      if str(metadata.get("exit_plan_id") or "") != plan_id:
        raise ValueError("卖出意图不属于该退出计划")
      if intent.status != "AWAITING_APPROVAL":
        raise ValueError("卖出意图已处理或不再等待确认")
      intent.status = "REJECTED"
      intent.notes = reason
      intent.intent_metadata = local_pre_broker_zero_fill_metadata(
        metadata,
        reason=reason,
      )
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      ExitPlanBook([plan]).apply_order_event(
        plan_id=plan_id,
        intent_id=intent_id,
        status="RECONCILED_ZERO_FILL",
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
    expected_config_version: int,
    expected_state_version: int,
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
      if (
        record is None
        or str(record.strategy_run_id or "").strip()
        or not record.enabled
        or int(record.config_version or 0) != int(expected_config_version)
        or max(1, int(record.state_version or 1)) != int(expected_state_version)
      ):
        return None
      if not is_monitor_owned_exit_plan(record):
        raise RuntimeError("EXIT_PLAN_OWNER_INVALID:全局 Monitor 无权提交该退出计划")
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
      if (
        plan.status
        not in {ExitPlanStatus.ACTIVE, ExitPlanStatus.PARTIALLY_EXITED}
        or plan.pending_intent_id
        or plan.pending_order_id
        or str(decision.plan_id or "") != str(record.plan_id)
      ):
        return None
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
      price = self._protected_sell_price(context, record)
      await TradeIntentProcessor.reserve_exit_intent(
        db,
        plan=record,
        decision=decision,
        intent_id=intent_id,
        limit_price=price,
      )
      await db.commit()

    return await self._route_reserved_exit_intent(
      plan_id=plan_id,
      record=record,
      decision=decision,
      intent_id=intent_id,
      context=context,
      position=position,
      price=price,
      market_ready=market_ready,
    )

  async def _route_reserved_exit_intent(
    self,
    *,
    plan_id: str,
    record: AutoExitPlanRecord,
    decision: ExitDecision,
    intent_id: str,
    context: ExitEvaluationContext,
    position: Optional[Position],
    price: float,
    market_ready: Optional[Callable[[], bool]],
  ) -> Optional[dict[str, Any]]:
    """Route an atomically reserved intent through the shared order pipeline."""

    requested = int(decision.volume)
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
      pending_status = str(pending.status or "").strip().upper()
      if pending_status in TERMINAL_PENDING_ORDER_LIFECYCLE_STATUSES:
        intent = await db.get(TradeIntentRecord, str(plan.pending_intent_id or ""))
        metadata = dict(intent.intent_metadata or {}) if intent is not None else {}
        exact_intent = bool(
          intent is not None
          and _is_exact_exit_plan_intent(
            record,
            intent,
            expected_intent_id=str(plan.pending_intent_id or ""),
            expected_strategy_run_id=str(record.strategy_run_id or ""),
          )
          and str((pending.request_metadata or {}).get("exit_plan_id") or "")
          == str(record.plan_id or "")
          and str(pending.instrument_code or "").upper()
          == str(record.instrument_code or "").upper()
          and str(pending.side or "").upper() == "SELL"
        )
        if (
          exact_intent
          and _terminal_pending_order_supports_zero_fill(pending, metadata)
          and _has_authoritative_zero_fill_proof(
            str(intent.status or ""),
            metadata,
          )
        ):
          ExitPlanBook([plan]).apply_order_event(
            plan_id=plan.plan_id,
            intent_id=plan.pending_intent_id,
            status="RECONCILED_ZERO_FILL",
            order_id=pending.client_order_id,
          )
          record.last_error = "outbox_expiry_reconciled_zero_fill"
          return True
      ExitPlanBook([plan]).apply_order_event(
        plan_id=plan.plan_id,
        intent_id=plan.pending_intent_id,
        status=pending_status,
        order_id=pending.client_order_id,
      )
      record.last_error = None
      return True

    if str(plan.pending_order_id or "").strip():
      record.last_error = "pending_exit_order_projection_missing"
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
      if intent is not None:
        intent.intent_metadata = local_pre_broker_zero_fill_metadata(
          dict(intent.intent_metadata or {}),
          reason="ORPHANED_BEFORE_DURABLE_BROKER_COMMAND",
        )
      ExitPlanBook([plan]).apply_order_event(
        plan_id=plan.plan_id,
        intent_id=plan.pending_intent_id,
        status="RECONCILED_ZERO_FILL",
      )
      record.last_error = "orphaned_exit_intent_reconciled_zero_fill"
    return False

  async def _release_failed_submission(
    self, plan_id: str, intent_id: str, error: str
  ) -> None:
    async with AsyncSessionLocal() as db:
      record = await AutoExitPlanRepository(db).find_by_id(plan_id, for_update=True)
      if record is None:
        return
      if not is_monitor_owned_exit_plan(record):
        raise RuntimeError("EXIT_PLAN_OWNER_CHANGED")
      plan = ExitPlan.from_dict(dict(record.plan_state or {}))
      pending = (
        await db.execute(
          select(PendingTradeOrder)
          .where(PendingTradeOrder.account_id == record.account_id)
          .where(PendingTradeOrder.intent_id == intent_id)
          .limit(1)
        )
      ).scalar_one_or_none()
      terminal_status = str(pending.status or "PENDING") if pending is not None else (
        "RECONCILED_ZERO_FILL"
      )
      if pending is None:
        intent = await db.get(TradeIntentRecord, intent_id)
        if intent is not None:
          intent.intent_metadata = local_pre_broker_zero_fill_metadata(
            dict(intent.intent_metadata or {}),
            reason=error,
          )
      ExitPlanBook([plan]).apply_order_event(
        plan_id=plan_id,
        intent_id=intent_id,
        status=terminal_status,
        order_id=(str(pending.client_order_id or "") if pending is not None else ""),
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
        if not is_monitor_owned_exit_plan(record):
          raise RuntimeError("EXIT_PLAN_OWNER_CHANGED")
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
          clear_exact_auto_exit_authorization(record, bump_state_version=False)
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
      clear_exact_auto_exit_authorization(record, bump_state_version=False)
    plan.template = ExitPlanTemplate.from_dict(
      {
        **plan.template.to_dict(),
        "auto_exit_authorized": bool(record.auto_exit_authorized),
      }
    )
    adaptive_state = next(
      (
        dict(value or {})
        for key, value in plan.rule_state.items()
        if str(key).endswith(ADAPTIVE_RULE_ID_SUFFIX)
      ),
      {},
    )
    next_state = plan.to_dict()
    current_state = dict(record.plan_state or {})
    if current_state != next_state:
      current_version = max(1, int(getattr(record, "state_version", 1) or 1))
      record.state_version = (
        current_version + 1 if current_state else current_version
      )
    record.plan_state = next_state
    cost_basis = plan.cost_basis
    record.cost_basis_mode = cost_basis.mode.value
    record.cost_basis_snapshot = cost_basis.to_dict()
    record.status = plan.status.value
    record.enabled = plan.status not in {
      ExitPlanStatus.PAUSED,
      ExitPlanStatus.CANCELLED,
      ExitPlanStatus.COMPLETED,
      ExitPlanStatus.ERROR,
    }
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
      record.last_evaluated_at = time_utils.to_shanghai(evaluated_at)

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


def _normalize_cumulative_fill_report(
  value: Any,
  state: str,
) -> tuple[Optional[int], str]:
  """Normalize one broker cumulative-fill fact without inventing zero.

  ``MISSING`` and ``INVALID`` remain distinct audit/idempotency states even
  though both deliberately feed ``None`` into the domain release barrier.
  """

  normalized_state = str(state or "MISSING").strip().upper()
  if normalized_state == "INVALID":
    return None, "INVALID"
  if value is None:
    return None, "INVALID" if normalized_state == "VALUE" else "MISSING"
  if isinstance(value, bool):
    return None, "INVALID"
  if isinstance(value, float) and (
    not isfinite(value) or not value.is_integer()
  ):
    return None, "INVALID"
  if isinstance(value, Decimal) and (
    not value.is_finite() or value != value.to_integral_value()
  ):
    return None, "INVALID"
  try:
    resolved = int(value)
  except (TypeError, ValueError, OverflowError):
    return None, "INVALID"
  if resolved < 0:
    return None, "INVALID"
  return resolved, "VALUE"


def _optional_datetime(value: Any) -> Optional[datetime]:
  if isinstance(value, datetime):
    return value.replace(tzinfo=None)
  if isinstance(value, str) and value:
    try:
      return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
      return None
  return None
