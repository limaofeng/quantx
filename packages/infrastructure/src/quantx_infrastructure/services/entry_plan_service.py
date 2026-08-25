"""Engine-owned product service for managed A-share entry plans.

The service deliberately creates a real fixed-instrument ``StrategyRun`` and
only controls its lifecycle/configuration.  Market evaluation, sizing, risk
checks and broker routing remain owned by ``StrategyBase`` and
``StrategyExecutor``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Optional

from quantx_domain.brokers.base import OrderRequest, OrderType, PriceType
from quantx_domain.strategies.ashare_managed_entry_plan import (
  ENTRY_PLAN_ENABLED_KEY,
  MANAGED_ENTRY_STATE_KEY,
  AshareManagedEntryPlanStrategy,
)
from quantx_domain.strategies.base import RuntimeStatePatch
from quantx_domain.trading import AShareMarketRules, OrderSizer, TradingRiskChecker
from quantx_domain.trading.entry_plan import (
  EntryAuthorizationMode,
  EntryEnvironment,
  EntryPlanStatus,
  ManagedEntryPlanConfig,
  ManagedEntryPlanState,
)
from quantx_domain.trading.exit_plan import (
  ExitExecutionPolicy,
  ExitPlanTemplate,
  ExitRuleSpec,
  ExitRuleType,
)
from sqlalchemy import select

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.account import Account
from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  PendingTradeOrder,
  StrategyOrderCorrelation,
  StrategyRuntimeEvent,
  TradeCommandOutbox,
)
from quantx_infrastructure.models.broker_position_snapshot import (
  BrokerPositionSnapshot,
)
from quantx_infrastructure.models.enums import (
  AccountType,
  StrategyRunMode,
  StrategyRunStatus,
)
from quantx_infrastructure.models.instrument import Instrument
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.strategy_repository import StrategyRepository
from quantx_infrastructure.repositories.strategy_run_repository import (
  StrategyRunRepository,
)
from quantx_infrastructure.repositories.strategy_run_state_repository import (
  StrategyRunStateRepository,
)
from quantx_infrastructure.services.account_snapshot_contract import (
  ACCOUNT_SNAPSHOT_MAX_AGE,
)
from quantx_infrastructure.services.entry_plan_authorization_service import (
  EntryPlanAuthorizationScope,
  EntryPlanAuthorizationService,
  scope_from_managed_entry_config,
)

MANAGED_ENTRY_STRATEGY_CLASS_NAME = "AshareManagedEntryPlanStrategy"
ENTRY_PLAN_LAST_COMMAND_ID_KEY = "entry_plan_last_command_id"

_ACTIVE_INTENT_STATUSES = {
  "AWAITING_APPROVAL",
  "PENDING",
  "APPROVED",
  "QUEUED",
  "ROUTED",
  "DELIVERED",
  "SUBMITTED",
  "ACCEPTED",
  "PARTIAL_FILLED",
  "PARTIALLY_FILLED",
  "RECONCILE_REQUIRED",
  "CANCEL_REQUESTED",
}
_WORKING_ORDER_STATUSES = {
  "QUEUED",
  "PENDING",
  "DELIVERED",
  "SUBMITTED",
  "ACCEPTED",
  "PARTIAL_FILLED",
  "PARTIALLY_FILLED",
  "RECONCILE_REQUIRED",
  "CANCEL_REQUESTED",
}
_MAX_LIVE_ROLLOUT_SNAPSHOT_AGE = timedelta(seconds=90)
_MAX_INSTRUMENT_SNAPSHOT_AGE = timedelta(days=7)
# Engine owns one command consumer and one StrategyManager lease.  A process
# wide lock closes the check/create and config-version races between commands.
_ENTRY_PLAN_COMMAND_LOCK = asyncio.Lock()


@dataclass(frozen=True)
class EntryPlanFacts:
  filled_volume: int = 0
  filled_amount_cny: float = 0.0
  pending_intent_id: str = ""
  active_intent_id: str = ""
  reconciled_zero_intent_id: str = ""
  has_working_order: bool = False

  @property
  def has_pending(self) -> bool:
    return bool(
      self.pending_intent_id or self.active_intent_id or self.has_working_order
    )


@dataclass(frozen=True)
class _LoadedPlan:
  run: Any
  parameters: dict[str, Any]
  config: ManagedEntryPlanConfig


class EntryPlanService:
  """Coordinate EntryPlan commands without becoming a second strategy engine."""

  def __init__(
    self,
    runtime_manager: Any,
    *,
    session_factory: Callable[[], Any] = AsyncSessionLocal,
    authorization_service_factory: Callable[[Any], Any] = EntryPlanAuthorizationService,
  ) -> None:
    if runtime_manager is None:
      raise RuntimeError("EntryPlan 操作只能由 QuantX Engine 执行")
    self._runtime_manager = runtime_manager
    self._session_factory = session_factory
    self._authorization_service_factory = authorization_service_factory

  async def create(
    self,
    payload: Mapping[str, Any],
    *,
    command_id: str = "",
  ) -> dict[str, Any]:
    account_id = self._required_text(payload.get("account_id"), "账户不能为空")
    actor_user_id = self._required_text(
      payload.get("actor_user_id"), "操作主体不能为空"
    )
    raw_input = self._mapping(payload.get("input"))
    normalized_command_id = str(command_id or "").strip()
    plan_id = normalized_command_id or str(uuid.uuid4())
    instrument_code = self._required_text(
      raw_input.get("instrument_code"), "股票代码不能为空"
    ).upper()
    environment = self._entry_environment(raw_input)
    if normalized_command_id:
      existing = await self._load_owned_plan_if_exists(plan_id, account_id)
      if existing is not None:
        return await self._converge_replayed_create(
          existing,
          raw_input=raw_input,
          actor_user_id=actor_user_id,
          command_id=normalized_command_id,
        )
    baseline = await self._authoritative_baseline(
      account_id,
      instrument_code,
      environment,
    )
    config_input = self._with_authoritative_baseline(raw_input, baseline)
    config = self._build_config(
      config_input,
      plan_id=plan_id,
      account_id=account_id,
      config_version=1,
    )
    mode = self._run_mode(config)
    parameters = self._build_parameters(
      account_id=account_id,
      actor_user_id=actor_user_id,
      note=str(raw_input.get("note") or ""),
      config=config,
    )
    start_immediately = bool(raw_input.get("start_immediately", False))
    authorization_required = self._requires_live_auto_authorization(config)
    # A runtime may be partially started even when its start call ultimately
    # fails.  Keep the durable strategy gate closed until activation has
    # completely succeeded; enabling is always the final write.
    parameters[ENTRY_PLAN_ENABLED_KEY] = False
    if normalized_command_id:
      parameters[ENTRY_PLAN_LAST_COMMAND_ID_KEY] = normalized_command_id

    async with _ENTRY_PLAN_COMMAND_LOCK:
      if normalized_command_id:
        existing = await self._load_owned_plan_if_exists(plan_id, account_id)
        if existing is not None:
          return await self._converge_replayed_create(
            existing,
            raw_input=raw_input,
            actor_user_id=actor_user_id,
            command_id=normalized_command_id,
          )
      await self._ensure_no_active_overlap(account_id, config)
      strategy_id = await self._strategy_template_id()
      created_id = await self._runtime_manager.run_strategy(
        strategy_id=strategy_id,
        strategy_class=AshareManagedEntryPlanStrategy,
        mode=mode,
        instruments=[config.instrument_code],
        parameters=parameters,
        name=f"买入托管-{config.instrument_code}",
        auto_start=False,
        run_id=plan_id,
      )
      if str(created_id) != plan_id:
        raise RuntimeError("EntryPlan 与 StrategyRun 标识不一致")

      started = False
      if start_immediately and not authorization_required:
        try:
          parameters = await self._activate_entry_plan(plan_id, parameters)
        except Exception:
          await self._persist_run_status(plan_id, StrategyRunStatus.PAUSED)
          raise
        started = True
      else:
        await self._persist_run_status(plan_id, StrategyRunStatus.PAUSED)

    return {
      "plan_id": plan_id,
      "run_id": plan_id,
      "config_version": config.config_version,
      "started": started,
      "authorization_required": authorization_required,
    }

  async def update(
    self,
    payload: Mapping[str, Any],
    *,
    command_id: str = "",
  ) -> dict[str, Any]:
    account_id = self._required_text(payload.get("account_id"), "账户不能为空")
    actor_user_id = self._required_text(
      payload.get("actor_user_id"), "操作主体不能为空"
    )
    raw_input = self._mapping(payload.get("input"))
    plan_id = self._required_text(raw_input.get("plan_id"), "计划不能为空")
    expected_version = self._positive_int(
      raw_input.get("config_version"), "配置版本无效"
    )
    normalized_command_id = str(command_id or "").strip()

    async with _ENTRY_PLAN_COMMAND_LOCK:
      loaded = await self._load_owned_plan(plan_id, account_id)
      await self._require_plan_not_terminal(plan_id)
      if (
        normalized_command_id
        and str(loaded.parameters.get(ENTRY_PLAN_LAST_COMMAND_ID_KEY) or "")
        == normalized_command_id
      ):
        if loaded.config.config_version != expected_version + 1:
          raise ValueError("ENTRY_COMMAND_REPLAY_CONFLICT:更新命令落点版本不一致")
        return self._idempotent_update_result(loaded)
      self._require_version(loaded.config, expected_version)
      facts = await self._facts(plan_id)
      if facts.has_pending:
        raise ValueError("计划存在待确认意图或待收敛买单，当前不能修改配置")
      config_input = {
        **raw_input,
        "instrument_code": loaded.config.instrument_code,
        "bucket": loaded.config.bucket,
      }
      baseline = await self._authoritative_baseline(
        account_id,
        loaded.config.instrument_code,
        self._entry_environment(config_input),
      )
      config_input = self._with_authoritative_baseline(config_input, baseline)
      updated = self._build_config(
        config_input,
        plan_id=plan_id,
        account_id=account_id,
        config_version=expected_version + 1,
      )
      self._validate_target_not_below_fills(updated, facts)
      await self._ensure_no_active_overlap(
        account_id,
        updated,
        exclude_plan_id=plan_id,
      )
      if (
        updated.execution_policy.environment
        != loaded.config.execution_policy.environment
      ):
        raise ValueError("执行环境创建后不可切换，请为新环境创建独立计划")

      runtime_was_running = self._runtime_is_running(plan_id)
      plan_was_enabled = bool(
        loaded.parameters.get(ENTRY_PLAN_ENABLED_KEY, runtime_was_running)
      )
      if (
        runtime_was_running
        and plan_was_enabled
        and not await self._pause_runtime_for_update(plan_id)
      ):
        raise ValueError("计划正在产生或收敛交易事实，请暂停后再修改")
      if runtime_was_running and plan_was_enabled:
        facts = await self._facts(plan_id)
        if facts.has_pending:
          raise ValueError("暂停过程中出现待收敛买单，配置未修改")

      # Revoke before widening/changing the plan.  A later persistence failure
      # is deliberately fail-closed and merely requires a fresh authorization.
      await self._revoke_authorization(
        plan_id,
        actor_user_id=actor_user_id,
        reason="CONFIG_UPDATED",
      )
      should_reenable = bool(
        plan_was_enabled and not self._requires_live_auto_authorization(updated)
      )
      parameters = {
        **loaded.parameters,
        "entry_plan_note": str(raw_input.get("note") or "")[:500],
        # Persist the new config under a closed gate.  If the runtime was
        # previously armed, it is reopened only after resume succeeds.
        ENTRY_PLAN_ENABLED_KEY: False,
        MANAGED_ENTRY_STATE_KEY: updated.to_dict(),
      }
      if normalized_command_id:
        parameters[ENTRY_PLAN_LAST_COMMAND_ID_KEY] = normalized_command_id
      await self._require_plan_not_terminal(plan_id)
      await self._runtime_manager.update_run_parameters(plan_id, parameters)
      self._install_runtime_config(plan_id, updated)

      runtime_resumed = False
      if runtime_was_running:
        if should_reenable:
          parameters = await self._activate_entry_plan(plan_id, parameters)
          runtime_resumed = True
        else:
          await self._require_plan_not_terminal(plan_id)
          runtime_resumed = await self._start_or_resume(plan_id)
          if not runtime_resumed:
            raise RuntimeError("计划配置已保存，但恢复监控失败")
      enabled_after_update = bool(
        runtime_resumed and parameters[ENTRY_PLAN_ENABLED_KEY]
      )

    return {
      "plan_id": plan_id,
      "run_id": plan_id,
      "config_version": updated.config_version,
      "started": enabled_after_update and runtime_resumed,
      "authorization_required": self._requires_live_auto_authorization(updated),
    }

  async def set_enabled(
    self,
    plan_id: str,
    enabled: bool,
    *,
    account_id: str,
    config_version: int,
    actor_user_id: str,
  ) -> dict[str, Any]:
    normalized_plan_id = self._required_text(plan_id, "计划不能为空")
    normalized_account_id = self._required_text(account_id, "账户不能为空")
    expected_version = self._positive_int(config_version, "配置版本无效")

    async with _ENTRY_PLAN_COMMAND_LOCK:
      loaded = await self._load_owned_plan(normalized_plan_id, normalized_account_id)
      self._require_version(loaded.config, expected_version)
      if enabled:
        await self._require_plan_not_terminal(normalized_plan_id)
        if self._requires_live_auto_authorization(loaded.config):
          await self._require_live_auto_authorization(
            normalized_plan_id,
            normalized_account_id,
            loaded.config,
          )
        await self._activate_entry_plan(
          normalized_plan_id,
          loaded.parameters,
        )
        success = True
        code = "ENTRY_PLAN_ARMED"
      else:
        await self._set_entry_enabled(loaded, False)
        facts = await self._facts(normalized_plan_id)
        reconciled_zero_intent_id = ""
        if facts.pending_intent_id:
          rejection = await self._runtime_manager.executor.reject_trade_intent(
            normalized_plan_id,
            facts.pending_intent_id,
            reason="ENTRY_PLAN_PAUSED",
          )
          if (
            not bool(dict(rejection or {}).get("success"))
            and self._runtime_manager.get_run(normalized_plan_id) is None
          ):
            reconciled_zero_intent_id = await self._terminalize_offline_awaiting_intent(
              normalized_plan_id,
              facts.pending_intent_id,
              account_id=normalized_account_id,
              instrument_code=loaded.config.instrument_code,
              reason="ENTRY_PLAN_PAUSED",
            )
        await self._set_phase(
          normalized_plan_id,
          EntryPlanStatus.PAUSED,
          reason="USER_PAUSED",
          reconciled_zero_intent_id=reconciled_zero_intent_id,
        )
        success = True
        code = "ENTRY_PLAN_PAUSED"

    return {
      "success": success,
      "code": code,
      "plan_id": normalized_plan_id,
      "config_version": loaded.config.config_version,
      "actor_user_id": str(actor_user_id or ""),
    }

  async def cancel(
    self,
    plan_id: str,
    *,
    account_id: str,
    config_version: int,
    actor_user_id: str,
    cancel_working_order: bool = False,
  ) -> dict[str, Any]:
    normalized_plan_id = self._required_text(plan_id, "计划不能为空")
    normalized_account_id = self._required_text(account_id, "账户不能为空")
    expected_version = self._positive_int(config_version, "配置版本无效")

    async with _ENTRY_PLAN_COMMAND_LOCK:
      loaded = await self._load_owned_plan(normalized_plan_id, normalized_account_id)
      self._require_version(loaded.config, expected_version)
      await self._revoke_authorization(
        normalized_plan_id,
        actor_user_id=actor_user_id,
        reason="PLAN_CANCELLED",
      )
      await self._set_entry_enabled(loaded, False)
      facts = await self._facts(normalized_plan_id)
      reconciled_zero_intent_id = ""
      if facts.pending_intent_id:
        rejection = await self._runtime_manager.executor.reject_trade_intent(
          normalized_plan_id,
          facts.pending_intent_id,
          reason="ENTRY_PLAN_CANCELLED",
        )
        if (
          not bool(dict(rejection or {}).get("success"))
          and self._runtime_manager.get_run(normalized_plan_id) is None
        ):
          reconciled_zero_intent_id = await self._terminalize_offline_awaiting_intent(
            normalized_plan_id,
            facts.pending_intent_id,
            account_id=normalized_account_id,
            instrument_code=loaded.config.instrument_code,
            reason="ENTRY_PLAN_CANCELLED",
          )
      facts = await self._facts(normalized_plan_id)
      pending_work = bool(facts.active_intent_id or facts.has_working_order)
      terminal_phase = await self._request_terminal(
        normalized_plan_id,
        EntryPlanStatus.CANCELLED,
        reason="USER_CANCELLED",
        pending_work=pending_work,
        reconciled_zero_intent_id=(
          reconciled_zero_intent_id or facts.reconciled_zero_intent_id
        ),
      )
      draining = terminal_phase == EntryPlanStatus.DRAINING
      cancel_count = 0
      if draining and cancel_working_order:
        cancel_count = await self._runtime_manager.executor.cancel_open_buy_orders(
          normalized_plan_id,
          "ENTRY_PLAN_CANCELLED",
        )
        facts = await self._facts(normalized_plan_id)
        if (
          not facts.active_intent_id
          and not facts.has_working_order
          and facts.reconciled_zero_intent_id
        ):
          terminal_phase = await self._request_terminal(
            normalized_plan_id,
            EntryPlanStatus.CANCELLED,
            reason="USER_CANCELLED",
            pending_work=False,
            reconciled_zero_intent_id=facts.reconciled_zero_intent_id,
          )
          draining = terminal_phase == EntryPlanStatus.DRAINING
      if not draining:
        # ExitPlanBook may still protect filled lots.  The generic stop guard
        # correctly keeps such a runtime alive while the entry phase stays
        # CANCELLED and cannot emit another BUY.
        await self._runtime_manager.stop_strategy(normalized_plan_id)

    return {
      "success": True,
      "code": "ENTRY_PLAN_DRAINING" if draining else "ENTRY_PLAN_CANCELLED",
      "plan_id": normalized_plan_id,
      "config_version": loaded.config.config_version,
      "cancel_requested_count": cancel_count,
    }

  async def evaluate_now(
    self,
    plan_id: str,
    *,
    account_id: str,
  ) -> dict[str, Any]:
    normalized_plan_id = self._required_text(plan_id, "计划不能为空")
    await self._load_owned_plan(normalized_plan_id, account_id)
    runtime = self._runtime_manager.get_run(normalized_plan_id)
    if runtime is None or not self._runtime_is_running(normalized_plan_id):
      raise ValueError("计划未在监控，不能立即检查")
    if getattr(runtime, "durable_event_barrier_key", None):
      raise ValueError("持久化成交回报尚未收敛，暂不执行新的计划检查")
    instrument_code = str((runtime.context.instruments or [""])[0] or "")
    market_data = runtime.latest_market_data.get(instrument_code)
    if market_data is None:
      raise ValueError("最新权威行情不可用，不能立即检查")
    # Evaluate the latest normalized MarketDataSnapshot on the executor's
    # serial event loop.  It is intentionally not queued as a raw ``tick``:
    # ``_process_tick`` consumes the transport Tick shape, while this snapshot
    # must enter through a RECONCILE StrategyInput.
    await runtime.event_queue.put(
      (
        "entry_plan_evaluate",
        {
          "type": "ENTRY_PLAN_EVALUATE_NOW",
          "instrument_code": instrument_code,
          "market_data": market_data,
        },
      )
    )
    return {
      "success": True,
      "code": "ENTRY_PLAN_EVALUATION_QUEUED",
      "plan_id": normalized_plan_id,
    }

  async def trigger_manual(
    self,
    plan_id: str,
    rule_id: str,
    *,
    account_id: str,
  ) -> dict[str, Any]:
    """Queue one explicit manual-rule activation on the runtime serial loop."""

    normalized_plan_id = self._required_text(plan_id, "计划不能为空")
    normalized_rule_id = self._required_text(rule_id, "人工触发规则不能为空")
    loaded = await self._load_owned_plan(normalized_plan_id, account_id)
    rule = next(
      (
        item
        for item in loaded.config.trigger_rules
        if item.rule_id == normalized_rule_id
      ),
      None,
    )
    if rule is None or rule.rule_type != "MANUAL_TRIGGER":
      raise ValueError("指定规则不是当前计划的人工触发规则")
    if not rule.enabled:
      raise ValueError("指定人工触发规则当前未启用")
    if loaded.parameters.get(ENTRY_PLAN_ENABLED_KEY) is not True:
      raise ValueError("计划已暂停，不能人工触发买入")

    runtime = self._runtime_manager.get_run(normalized_plan_id)
    if runtime is None or not self._runtime_is_running(normalized_plan_id):
      raise ValueError("计划未在监控，不能人工触发")
    if getattr(runtime, "durable_event_barrier_key", None):
      raise ValueError("持久化成交回报尚未收敛，暂不接受人工触发")
    instrument_code = loaded.config.instrument_code
    market_data = runtime.latest_market_data.get(instrument_code)
    if market_data is None:
      raise ValueError("最新权威行情不可用，不能人工触发")

    await runtime.event_queue.put(
      (
        "entry_plan_evaluate",
        {
          "type": "ENTRY_PLAN_MANUAL_TRIGGER",
          "rule_id": normalized_rule_id,
          "instrument_code": instrument_code,
          "market_data": market_data,
        },
      )
    )
    return {
      "success": True,
      "code": "ENTRY_PLAN_MANUAL_TRIGGER_QUEUED",
      "plan_id": normalized_plan_id,
      "rule_id": normalized_rule_id,
    }

  async def preview_intent(
    self,
    plan_id: str,
    intent_id: str,
    *,
    account_id: str,
  ) -> dict[str, Any]:
    normalized_plan_id = self._required_text(plan_id, "计划不能为空")
    normalized_intent_id = self._required_text(intent_id, "买入意图不能为空")
    await self._load_owned_plan(normalized_plan_id, account_id)
    runtime = self._runtime_manager.get_run(normalized_plan_id)
    if runtime is None:
      raise ValueError("计划尚未恢复到 Engine")
    intent = runtime.pending_approvals.get(normalized_intent_id)
    if intent is None:
      raise ValueError("买入意图不存在、已处理或已过期")

    market_data = runtime.latest_market_data.get(intent.instrument_code)
    signal_price = float(intent.limit_price_hint or 0.0)
    asks = list(getattr(market_data, "ask_price", []) or []) if market_data else []
    latest_price = float(
      (asks[0] if asks and asks[0] else 0.0)
      or getattr(market_data, "price", 0.0)
      or 0.0
    )
    deviation_bps = (
      abs(latest_price - signal_price) / signal_price * 10_000
      if latest_price > 0 and signal_price > 0
      else 0.0
    )
    failure = self._runtime_manager.executor._approval_failure(runtime, intent)
    if failure is not None:
      return self._intent_preview(
        intent,
        valid=False,
        code=failure[0],
        message=failure[1],
        signal_price=signal_price,
        latest_price=latest_price,
        deviation_bps=deviation_bps,
      )

    rules = AShareMarketRules()
    price_tick = getattr(market_data, "price_tick", None) if market_data else None
    order_price = rules.normalize_price(signal_price or latest_price, price_tick)
    account = runtime.state_manager.get_account_quota() if runtime.state_manager else {}
    position = (
      runtime.state_manager.get_position(intent.instrument_code) or {}
      if runtime.state_manager
      else {}
    )
    draft = OrderSizer(rules).draft_intent(
      intent,
      OrderType.BUY,
      order_price,
      account,
      position,
    )
    if draft.sized_volume <= 0:
      code = (
        "MIN_LOT_EXCEEDS_RISK_BUDGET"
        if "MIN_LOT_EXCEEDS_RISK_BUDGET" in draft.size_reason_codes
        else "ZERO_SIZED_VOLUME"
      )
      return self._intent_preview(
        intent,
        valid=False,
        code=code,
        message="买入目标无法转换为合法整手数量",
        signal_price=signal_price,
        latest_price=latest_price,
        deviation_bps=deviation_bps,
        sized_volume=draft.sized_volume,
      )

    context_snapshot = self._runtime_manager.executor._build_execution_context_snapshot(
      runtime,
      instrument_code=intent.instrument_code,
      market_data=market_data,
      account=account,
      positions={intent.instrument_code: position},
    )
    strict_market_data, strict_limit_data = (
      self._runtime_manager.executor._order_risk_strict_flags(runtime)
    )
    request = OrderRequest(
      instrument_code=intent.instrument_code,
      order_type=OrderType.BUY,
      price_type=PriceType.LIMIT,
      volume=draft.sized_volume,
      price=order_price,
      strategy_id=str(runtime.strategy_id),
      metadata={
        **dict(intent.metadata or {}),
        "intent_id": intent.intent_id,
        "bucket": intent.bucket,
      },
    )
    broker = runtime.broker
    decision = await TradingRiskChecker(
      rules,
      commission_rate=getattr(broker, "commission_rate", 0.0003),
      min_commission=getattr(broker, "min_commission", 5.0),
      strict_market_data=strict_market_data,
      strict_limit_data=strict_limit_data,
      enforce_trading_hours=bool(
        runtime.context.parameters.get(
          "enforce_trading_hours",
          runtime.context.mode == StrategyRunMode.LIVE,
        )
      ),
      market=runtime.context.parameters.get("market", "SH"),
    ).evaluate_order(
      request,
      account=account,
      position=position,
      market_data=market_data,
      current_time=runtime.context.current_time,
      risk_caps=context_snapshot.risk_caps,
    )
    return self._intent_preview(
      intent,
      valid=bool(decision.allowed and decision.final_volume > 0),
      code=str(decision.reason_code or "ALLOWED"),
      message=str(decision.reason_detail or "最新行情和风控预览通过"),
      signal_price=signal_price,
      latest_price=latest_price,
      deviation_bps=deviation_bps,
      sized_volume=draft.sized_volume,
      final_volume=int(decision.final_volume or 0),
      risk_action=decision.action.value,
    )

  async def set_automation_paused(
    self,
    *,
    account_id: str,
    paused: bool,
    reason: str,
    actor_user_id: str,
  ) -> dict[str, Any]:
    normalized_account_id = self._required_text(account_id, "账户不能为空")
    normalized_actor = self._required_text(actor_user_id, "操作主体不能为空")
    async with self._session_factory() as db:
      state = await self._authorization_service_factory(db).set_paused(
        account_id=normalized_account_id,
        paused=bool(paused),
        reason=str(reason or "USER_REQUESTED")[:160],
        actor_user_id=normalized_actor,
      )
    return {
      "account_id": normalized_account_id,
      "paused": bool(state.paused),
      "reason": str(state.reason or ""),
      "actor_user_id": str(state.actor_user_id or ""),
      "updated_at": state.changed_at,
    }

  @staticmethod
  def authorization_scope(
    plan_id: str,
    config: ManagedEntryPlanConfig | Mapping[str, Any],
  ) -> EntryPlanAuthorizationScope:
    """Build the one canonical scope used by preview, start and order gates."""
    return scope_from_managed_entry_config(
      plan_id=str(plan_id),
      config=config,
    )

  async def _authoritative_baseline(
    self,
    account_id: str,
    instrument_code: str,
    environment: EntryEnvironment,
  ) -> dict[str, Any]:
    """Rebuild target baseline from server-owned account facts.

    Client baseline fields are presentation hints only.  They can never set
    total assets, an existing position, the reference price or the snapshot
    identity used by a risk-increasing authorization.
    """

    normalized_account_id = self._required_text(account_id, "账户不能为空")
    normalized_code = self._required_text(instrument_code, "股票代码不能为空").upper()
    now = time_utils.now()
    async with self._session_factory() as db:
      account = (
        await db.execute(
          select(Account).where(
            Account.account_id == normalized_account_id,
            Account.account_type == AccountType.STOCK,
          )
        )
      ).scalar_one_or_none()
      position = (
        await db.execute(
          select(Position).where(
            Position.account_id == normalized_account_id,
            Position.stock_code == normalized_code,
          )
        )
      ).scalar_one_or_none()
      position_snapshot = await db.get(
        BrokerPositionSnapshot,
        normalized_account_id,
      )
      instrument = await db.get(Instrument, normalized_code)
      rollout = (
        await db.get(AccountExecutionControl, normalized_account_id)
        if environment == EntryEnvironment.LIVE
        else None
      )

    if account is None:
      raise ValueError("ENTRY_ACCOUNT_SNAPSHOT_UNAVAILABLE:交易账户快照不存在")
    self._require_fresh_timestamp(
      getattr(account, "updated_at", None),
      now=now,
      max_age=ACCOUNT_SNAPSHOT_MAX_AGE,
      code="ENTRY_ACCOUNT_SNAPSHOT_STALE",
      message="交易账户快照已过期",
    )
    total_asset = self._finite_positive(getattr(account, "total_asset", None))
    if total_asset is None:
      raise ValueError("ENTRY_ACCOUNT_SNAPSHOT_UNAVAILABLE:账户总资产不可用")

    if position_snapshot is None:
      raise ValueError("ENTRY_POSITION_SNAPSHOT_UNAVAILABLE:完整持仓快照不存在")
    if (
      not bool(position_snapshot.is_complete)
      or int(position_snapshot.sequence or 0) <= 0
      or bool(str(position_snapshot.last_error or "").strip())
    ):
      raise ValueError("ENTRY_POSITION_SNAPSHOT_INCOMPLETE:完整持仓快照不可用")
    self._require_fresh_timestamp(
      position_snapshot.reported_at,
      now=now,
      max_age=ACCOUNT_SNAPSHOT_MAX_AGE,
      code="ENTRY_POSITION_SNAPSHOT_STALE",
      message="券商持仓报告时间已过期",
    )
    self._require_fresh_timestamp(
      position_snapshot.received_at,
      now=now,
      max_age=ACCOUNT_SNAPSHOT_MAX_AGE,
      code="ENTRY_POSITION_SNAPSHOT_STALE",
      message="券商持仓接收时间已过期",
    )

    if instrument is None:
      raise ValueError("ENTRY_SECURITY_STATUS_UNAVAILABLE:证券主数据不存在")
    self._require_fresh_timestamp(
      getattr(instrument, "updated_at", None),
      now=now,
      max_age=_MAX_INSTRUMENT_SNAPSHOT_AGE,
      code="ENTRY_SECURITY_STATUS_STALE",
      message="证券主数据已过期",
    )

    position_volume = 0
    market_value = 0.0
    position_updated_at = None
    price_candidates: list[Any] = []
    if position is not None:
      self._require_fresh_timestamp(
        getattr(position, "updated_at", None),
        now=now,
        max_age=ACCOUNT_SNAPSHOT_MAX_AGE,
        code="ENTRY_POSITION_SNAPSHOT_STALE",
        message="持仓快照已过期",
      )
      position_volume = max(0, int(getattr(position, "volume", 0) or 0))
      market_value = max(0.0, float(getattr(position, "market_value", 0.0) or 0.0))
      position_updated_at = getattr(position, "updated_at", None)
      price_candidates.extend(
        [
          getattr(position, "last_price", None),
          (market_value / position_volume if position_volume > 0 else None),
        ]
      )
    price_candidates.extend(
      [
        getattr(instrument, "pre_close", None),
        getattr(instrument, "settlement_price", None),
      ]
    )
    reference_price = next(
      (
        value
        for candidate in price_candidates
        if (value := self._finite_positive(candidate)) is not None
      ),
      None,
    )
    if reference_price is None:
      raise ValueError("ENTRY_PRICE_SNAPSHOT_UNAVAILABLE:证券参考价格不可用")

    binding: dict[str, Any] = {
      "account_id": normalized_account_id,
      "instrument_code": normalized_code,
      "environment": environment.value,
      "account_updated_at": self._timestamp_token(account.updated_at),
      "position_updated_at": self._timestamp_token(position_updated_at),
      "position_snapshot_sequence": int(position_snapshot.sequence),
      "position_snapshot_source": str(position_snapshot.source or ""),
      "position_snapshot_reported_at": self._timestamp_token(
        position_snapshot.reported_at
      ),
      "position_snapshot_received_at": self._timestamp_token(
        position_snapshot.received_at
      ),
      "position_snapshot_count": int(position_snapshot.position_count or 0),
      "instrument_updated_at": self._timestamp_token(instrument.updated_at),
      "total_asset_cny": total_asset,
      "position_volume": position_volume,
      "market_value_cny": market_value,
      "reference_price": reference_price,
    }
    if environment == EntryEnvironment.LIVE:
      if rollout is None:
        raise ValueError("ENTRY_LIVE_SNAPSHOT_UNAVAILABLE:实盘对账快照不存在")
      if str(rollout.reconcile_status or "").upper() != "READY":
        raise ValueError("ENTRY_RECONCILE_REQUIRED:实盘账户尚未完成权威对账")
      snapshot_id = str(rollout.last_snapshot_id or "").strip()
      snapshot_hash = str(rollout.last_snapshot_hash or "").strip()
      snapshot_at = getattr(rollout, "last_snapshot_at", None)
      if not snapshot_id or len(snapshot_hash) != 64 or not snapshot_at:
        raise ValueError("ENTRY_LIVE_SNAPSHOT_INCOMPLETE:实盘对账快照不完整")
      self._require_fresh_timestamp(
        snapshot_at,
        now=now,
        max_age=_MAX_LIVE_ROLLOUT_SNAPSHOT_AGE,
        code="ENTRY_LIVE_SNAPSHOT_STALE",
        message="实盘对账快照已过期",
      )
      if self._timestamp_token(snapshot_at) != self._timestamp_token(
        position_snapshot.reported_at
      ):
        raise ValueError(
          "ENTRY_LIVE_SNAPSHOT_MISMATCH:实盘对账与完整持仓快照不属于同一报告"
        )
      binding.update(
        {
          "rollout_snapshot_id": snapshot_id,
          "rollout_snapshot_hash": snapshot_hash,
          "rollout_snapshot_at": self._timestamp_token(snapshot_at),
        }
      )

    encoded = json.dumps(
      binding,
      ensure_ascii=True,
      separators=(",", ":"),
      sort_keys=True,
      default=str,
    ).encode("utf-8")
    snapshot_version = hashlib.sha256(encoded).hexdigest()
    return {
      "position_volume": position_volume,
      "market_value_cny": market_value,
      "total_asset_cny": total_asset,
      "reference_price": reference_price,
      "account_snapshot_version": snapshot_version,
    }

  @staticmethod
  def _with_authoritative_baseline(
    raw_input: Mapping[str, Any],
    baseline: Mapping[str, Any],
  ) -> dict[str, Any]:
    normalized = dict(raw_input or {})
    normalized["target_policy"] = {
      **EntryPlanService._mapping(normalized.get("target_policy")),
      "baseline_snapshot": dict(baseline),
    }
    return normalized

  async def _strategy_template_id(self) -> int:
    async with self._session_factory() as db:
      strategy = await StrategyRepository(db).find_by_class_name(
        MANAGED_ENTRY_STRATEGY_CLASS_NAME
      )
      if strategy is None:
        raise ValueError("买入托管策略模板尚未注册，请稍后重试")
      return int(strategy.id)

  async def _ensure_no_active_overlap(
    self,
    account_id: str,
    config: ManagedEntryPlanConfig,
    *,
    exclude_plan_id: str = "",
  ) -> None:
    async with self._session_factory() as db:
      runs = await StrategyRunRepository(db).find_active_runs_by_strategy_class(
        MANAGED_ENTRY_STRATEGY_CLASS_NAME
      )
      state_repository = StrategyRunStateRepository(db)
      for run in runs:
        if str(run.id) == str(exclude_plan_id or ""):
          continue
        parameters = self._mapping(run.parameters)
        other_account_id = str(parameters.get("account_id") or "")
        if other_account_id != account_id:
          continue
        persisted_state = await state_repository.get_state(str(run.id))
        if persisted_state is not None:
          try:
            state = ManagedEntryPlanState.from_dict(
              self._mapping(
                self._mapping(persisted_state.custom_state).get(MANAGED_ENTRY_STATE_KEY)
              )
            )
          except (TypeError, ValueError):
            state = None
          if state is not None and state.phase in {
            EntryPlanStatus.CANCELLED,
            EntryPlanStatus.EXPIRED,
            EntryPlanStatus.COMPLETED,
          }:
            # The runtime may remain alive solely to protect filled slices via
            # ExitPlanBook; a terminal entry phase no longer competes to BUY.
            continue
        try:
          other = ManagedEntryPlanConfig.from_dict(
            self._mapping(parameters.get(MANAGED_ENTRY_STATE_KEY))
          )
        except (TypeError, ValueError):
          # An unparseable active run is an unsafe overlap, not permission to
          # create another strategy over the same unknown exposure.
          instruments = [
            str(item or "").upper() for item in list(run.instruments or [])
          ]
          if config.instrument_code in instruments:
            raise ValueError(f"ACTIVE_ENTRY_PLAN_EXISTS:{run.id}")
          continue
        if other.instrument_code == config.instrument_code:
          raise ValueError(f"ACTIVE_ENTRY_PLAN_EXISTS:{run.id}")

  async def _load_owned_plan_if_exists(
    self,
    plan_id: str,
    account_id: str,
  ) -> Optional[_LoadedPlan]:
    async with self._session_factory() as db:
      run = await StrategyRunRepository(db).find_run_by_id(plan_id)
    if run is None:
      return None
    return self._owned_plan_from_run(run, account_id)

  async def _load_owned_plan(self, plan_id: str, account_id: str) -> _LoadedPlan:
    async with self._session_factory() as db:
      run = await StrategyRunRepository(db).find_run_by_id(plan_id)
    if run is None:
      raise ValueError("建仓/加仓计划不存在")
    return self._owned_plan_from_run(run, account_id)

  def _owned_plan_from_run(self, run: Any, account_id: str) -> _LoadedPlan:
    if (
      getattr(run, "strategy", None) is None
      or str(run.strategy.class_name) != MANAGED_ENTRY_STRATEGY_CLASS_NAME
    ):
      raise ValueError("目标 StrategyRun 不是建仓/加仓托管计划")
    parameters = self._mapping(run.parameters)
    if str(parameters.get("account_id") or "") != str(account_id or ""):
      raise ValueError("建仓/加仓计划不属于当前账户")
    try:
      config = ManagedEntryPlanConfig.from_dict(
        self._mapping(parameters.get(MANAGED_ENTRY_STATE_KEY))
      )
    except (TypeError, ValueError) as exc:
      raise ValueError("建仓/加仓计划配置已损坏") from exc
    run_mode = self._status_value(getattr(run, "mode", ""))
    if run_mode and run_mode != config.execution_policy.environment.value:
      raise ValueError("建仓计划执行环境与 StrategyRun 模式不一致")
    return _LoadedPlan(run=run, parameters=parameters, config=config)

  async def _converge_replayed_create(
    self,
    loaded: _LoadedPlan,
    *,
    raw_input: Mapping[str, Any],
    actor_user_id: str,
    command_id: str,
  ) -> dict[str, Any]:
    if str(loaded.parameters.get(ENTRY_PLAN_LAST_COMMAND_ID_KEY) or "") != command_id:
      raise ValueError("ENTRY_COMMAND_REPLAY_CONFLICT:计划标识已被其他命令占用")
    saved_baseline = loaded.config.to_dict()["target_policy"]["baseline_snapshot"]
    requested = self._build_config(
      self._with_authoritative_baseline(raw_input, saved_baseline),
      plan_id=str(loaded.run.id),
      account_id=str(loaded.parameters.get("account_id") or ""),
      config_version=loaded.config.config_version,
    )
    if requested != loaded.config:
      raise ValueError("ENTRY_COMMAND_REPLAY_CONFLICT:创建命令配置不一致")
    if str(loaded.parameters.get("entry_plan_actor_user_id") or "") != actor_user_id:
      raise ValueError("ENTRY_COMMAND_REPLAY_CONFLICT:创建命令操作主体不一致")
    if (
      str(loaded.parameters.get("entry_plan_note") or "")
      != str(raw_input.get("note") or "")[:500]
    ):
      raise ValueError("ENTRY_COMMAND_REPLAY_CONFLICT:创建命令备注不一致")

    plan_id = str(loaded.run.id)
    authorization_required = self._requires_live_auto_authorization(loaded.config)
    start_requested = bool(raw_input.get("start_immediately", False))
    started = False
    if start_requested and not authorization_required:
      try:
        await self._activate_entry_plan(plan_id, loaded.parameters)
      except Exception:
        await self._persist_run_status(plan_id, StrategyRunStatus.PAUSED)
        raise
      started = True
    else:
      await self._set_entry_enabled(loaded, False)
      await self._set_phase(
        plan_id,
        EntryPlanStatus.PAUSED,
        reason="CREATE_REPLAY_PAUSED",
      )
      await self._persist_run_status(plan_id, StrategyRunStatus.PAUSED)

    return {
      "plan_id": plan_id,
      "run_id": plan_id,
      "config_version": loaded.config.config_version,
      "started": started,
      "authorization_required": authorization_required,
    }

  def _idempotent_update_result(self, loaded: _LoadedPlan) -> dict[str, Any]:
    plan_id = str(loaded.run.id)
    self._install_runtime_config(plan_id, loaded.config)
    return {
      "plan_id": plan_id,
      "run_id": plan_id,
      "config_version": loaded.config.config_version,
      "started": bool(
        loaded.parameters.get(ENTRY_PLAN_ENABLED_KEY) is True
        and self._runtime_is_running(plan_id)
      ),
      "authorization_required": self._requires_live_auto_authorization(loaded.config),
    }

  async def _terminalize_offline_awaiting_intent(
    self,
    plan_id: str,
    intent_id: str,
    *,
    account_id: str,
    instrument_code: str,
    reason: str,
  ) -> str:
    """Close a no-runtime approval only after proving no order side effect exists."""

    if self._runtime_manager.get_run(plan_id) is not None:
      return ""
    async with self._session_factory() as db:
      intent = await db.get(
        TradeIntentRecord,
        intent_id,
        with_for_update=True,
      )
      if intent is None:
        return ""
      metadata = dict(intent.intent_metadata or {})
      if (
        str(intent.strategy_run_id or "") != plan_id
        or (intent.account_id and str(intent.account_id) != account_id)
        or str(intent.instrument_code or "").upper() != instrument_code.upper()
        or str(intent.direction or "").upper() != "BUY"
        or str(intent.status or "").upper() != "AWAITING_APPROVAL"
        or str(metadata.get("entry_plan_id") or "") != plan_id
        or str(metadata.get("execution_mode") or "").upper() != "MANUAL_CONFIRM"
      ):
        return ""

      try:
        executed_volume = int(intent.executed_volume or 0)
        executed_price = float(intent.executed_price or 0.0)
      except (TypeError, ValueError, OverflowError):
        return ""
      if (
        executed_volume != 0
        or not math.isfinite(executed_price)
        or executed_price > 0
        or intent.executed_time is not None
        or str(intent.order_id or "").strip()
      ):
        return ""

      pending_order = await db.scalar(
        select(PendingTradeOrder.client_order_id)
        .where(
          PendingTradeOrder.strategy_run_id == plan_id,
          PendingTradeOrder.intent_id == intent_id,
        )
        .with_for_update()
        .limit(1)
      )
      correlation = await db.scalar(
        select(StrategyOrderCorrelation.id)
        .where(
          StrategyOrderCorrelation.strategy_run_id == plan_id,
          StrategyOrderCorrelation.intent_id == intent_id,
        )
        .with_for_update()
        .limit(1)
      )
      outbox = await db.scalar(
        select(TradeCommandOutbox.message_id)
        .where(
          TradeCommandOutbox.account_id == account_id,
          TradeCommandOutbox.payload["strategy_run_id"].as_string() == plan_id,
          TradeCommandOutbox.payload["intent_id"].as_string() == intent_id,
        )
        .with_for_update()
        .limit(1)
      )
      runtime_event = await db.scalar(
        select(StrategyRuntimeEvent.event_id)
        .where(
          StrategyRuntimeEvent.strategy_run_id == plan_id,
          StrategyRuntimeEvent.payload["metadata"]["intent_id"].as_string()
          == intent_id,
        )
        .with_for_update()
        .limit(1)
      )
      if any((pending_order, correlation, outbox, runtime_event)):
        return ""

      reason_code = f"{reason}_BEFORE_ORDER_RECONCILED_ZERO_FILL"
      intent.status = "RECONCILED_ZERO_FILL"
      intent.notes = reason_code
      intent.intent_metadata = {
        **metadata,
        "execution_terminal_reason": reason_code,
        "execution_terminal_source": "ENTRY_PLAN_SERVICE_OFFLINE",
        "execution_terminal_at": time_utils.now().isoformat(),
      }
      await db.commit()
      return intent_id

  async def _facts(self, plan_id: str) -> EntryPlanFacts:
    async with self._session_factory() as db:
      intents = list(
        (
          await db.execute(
            select(TradeIntentRecord)
            .where(
              TradeIntentRecord.strategy_run_id == plan_id,
              TradeIntentRecord.direction == "BUY",
            )
            .order_by(TradeIntentRecord.created_at.desc(), TradeIntentRecord.id.desc())
          )
        )
        .scalars()
        .all()
      )
      working_order = await db.scalar(
        select(PendingTradeOrder.client_order_id)
        .where(
          PendingTradeOrder.strategy_run_id == plan_id,
          PendingTradeOrder.side == "BUY",
          PendingTradeOrder.status.in_(tuple(_WORKING_ORDER_STATUSES)),
        )
        .limit(1)
      )
    active = next(
      (
        str(intent.id)
        for intent in intents
        if str(intent.status or "").upper() in _ACTIVE_INTENT_STATUSES
      ),
      "",
    )
    awaiting_approval = next(
      (
        str(intent.id)
        for intent in intents
        if str(intent.status or "").upper() == "AWAITING_APPROVAL"
      ),
      "",
    )
    reconciled_zero = next(
      (
        str(intent.id)
        for intent in intents
        if str(intent.status or "").upper() == "RECONCILED_ZERO_FILL"
      ),
      "",
    )
    return EntryPlanFacts(
      filled_volume=sum(max(0, int(intent.executed_volume or 0)) for intent in intents),
      filled_amount_cny=sum(
        max(0, int(intent.executed_volume or 0))
        * max(0.0, float(intent.executed_price or 0.0))
        for intent in intents
      ),
      pending_intent_id=awaiting_approval,
      active_intent_id=active,
      reconciled_zero_intent_id=reconciled_zero,
      has_working_order=working_order is not None,
    )

  async def _require_live_auto_authorization(
    self,
    plan_id: str,
    account_id: str,
    config: ManagedEntryPlanConfig,
  ) -> None:
    # Refresh the server-owned facts to fail closed on stale account,
    # position, instrument or rollout data.  Do not compare the composite
    # baseline hash here: fills legitimately consumed by this same grant alter
    # account/position facts between tranches.  The final quantitative order
    # gates distinguish those fills from unexplained external exposure using
    # the grant's monotonic consumption ledger.
    await self._authoritative_baseline(
      account_id,
      config.instrument_code,
      EntryEnvironment.LIVE,
    )
    scope = self.authorization_scope(plan_id, config)
    async with self._session_factory() as db:
      validation = await self._authorization_service_factory(db).validate_or_invalidate(
        plan_id=plan_id,
        current_scope=scope,
        account_id=account_id,
      )
    if not validation.valid:
      raise ValueError(f"{validation.code}:{validation.message}")

  async def _revoke_authorization(
    self,
    plan_id: str,
    *,
    actor_user_id: str,
    reason: str,
  ) -> None:
    async with self._session_factory() as db:
      await self._authorization_service_factory(db).revoke(
        plan_id=plan_id,
        reason=reason,
        actor_user_id=str(actor_user_id or "system"),
      )

  async def _set_entry_enabled(
    self,
    loaded: _LoadedPlan,
    enabled: bool,
  ) -> dict[str, Any]:
    return await self._persist_entry_enabled(
      str(loaded.run.id),
      loaded.parameters,
      enabled,
    )

  async def _persist_entry_enabled(
    self,
    plan_id: str,
    parameters: Mapping[str, Any],
    enabled: bool,
  ) -> dict[str, Any]:
    parameters = {
      **dict(parameters),
      ENTRY_PLAN_ENABLED_KEY: bool(enabled),
    }
    await self._runtime_manager.update_run_parameters(
      plan_id,
      parameters,
    )
    return parameters

  async def _activate_entry_plan(
    self,
    plan_id: str,
    parameters: Mapping[str, Any],
  ) -> dict[str, Any]:
    """Start/resume monitoring while the risk-increasing gate stays closed."""

    await self._require_plan_not_terminal(plan_id)
    safe_parameters = await self._persist_entry_enabled(
      plan_id,
      parameters,
      False,
    )
    try:
      await self._set_phase(plan_id, await self._active_phase(plan_id))
      await self._require_plan_not_terminal(plan_id)
      started = await self._start_or_resume(plan_id)
    except Exception:
      await self._activation_failed(plan_id, safe_parameters)
      raise
    if not started:
      await self._activation_failed(plan_id, safe_parameters)
      raise RuntimeError("买入托管计划启动失败")

    try:
      await self._require_plan_not_terminal(plan_id)
      return await self._persist_entry_enabled(
        plan_id,
        safe_parameters,
        True,
      )
    except Exception as exc:
      # StrategyManager rolls its in-memory parameters back when persistence
      # fails.  The explicit close also protects alternate injected managers;
      # monitoring remains alive so late broker facts and ExitPlans converge.
      try:
        await self._persist_entry_enabled(plan_id, safe_parameters, False)
      except Exception:
        pass
      raise RuntimeError("计划监控已启动，但买入门禁开启失败") from exc

  async def _activation_failed(
    self,
    plan_id: str,
    safe_parameters: Mapping[str, Any],
  ) -> None:
    try:
      await self._persist_entry_enabled(plan_id, safe_parameters, False)
    finally:
      await self._require_plan_not_terminal(plan_id)
      await self._set_phase(
        plan_id,
        EntryPlanStatus.PAUSED,
        reason="ACTIVATION_FAILED",
      )

  async def _persist_run_status(
    self,
    plan_id: str,
    status: StrategyRunStatus,
  ) -> None:
    runtime = self._runtime_manager.get_run(plan_id)
    if runtime is not None:
      enum_type = type(runtime.status)
      candidate = getattr(enum_type, status.name, None)
      if candidate is not None:
        runtime.status = candidate
    async with self._session_factory() as db:
      updated = await StrategyRunRepository(db).update_run(plan_id, {"status": status})
    if updated is None:
      raise ValueError("建仓/加仓计划不存在")

  async def _pause_runtime_for_update(self, plan_id: str) -> bool:
    runtime = self._runtime_manager.get_run(plan_id)
    if runtime is None:
      return False
    return bool(await self._runtime_manager.pause_strategy(plan_id))

  async def _start_or_resume(self, plan_id: str) -> bool:
    runtime = self._runtime_manager.get_run(plan_id)
    if runtime is None:
      return False
    task = getattr(runtime, "task", None)
    task_active = task is not None and not task.done()
    status = self._status_value(runtime.status)
    if task_active and status in {"RUNNING", "STARTING"}:
      return True
    if task_active and status == "PAUSED":
      return bool(await self._runtime_manager.resume_strategy(plan_id))
    return bool(await self._runtime_manager.start_strategy(plan_id))

  async def _set_phase(
    self,
    plan_id: str,
    phase: EntryPlanStatus,
    *,
    reason: str = "",
    reconciled_zero_intent_id: str = "",
  ) -> None:
    runtime = self._runtime_manager.get_run(plan_id)
    if runtime is not None and runtime.strategy is not None:
      raw_state = runtime.strategy.state.get(MANAGED_ENTRY_STATE_KEY, {})
      state = ManagedEntryPlanState.from_dict(self._mapping(raw_state))
      if (
        reconciled_zero_intent_id
        and state.pending_intent_id == reconciled_zero_intent_id
      ):
        state.apply_order_terminal(
          status="RECONCILED_ZERO_FILL",
          timestamp_ms=int(time_utils.now().timestamp() * 1000),
          cooldown_after_reject_seconds=0,
        )
      state.phase = phase
      state.last_decision = {
        **dict(state.last_decision or {}),
        "reason": reason or f"ENTRY_PLAN_{phase.value}",
      }
      value = state.to_dict()
      self._runtime_manager.executor.apply_external_state_patch(
        plan_id,
        RuntimeStatePatch(
          set={MANAGED_ENTRY_STATE_KEY: value},
          append_events=[
            {
              "type": f"ENTRY_PLAN_{phase.value}",
              "reason": reason or f"ENTRY_PLAN_{phase.value}",
            }
          ],
        ),
      )
      if runtime.state_manager is not None:
        runtime.state_manager.set_custom(MANAGED_ENTRY_STATE_KEY, value)
        if not await runtime.state_manager.save_snapshot():
          raise RuntimeError("计划阶段快照持久化失败")
      return

    async with self._session_factory() as db:
      repo = StrategyRunStateRepository(db)
      record = await repo.get_state(plan_id)
      custom = self._mapping(getattr(record, "custom_state", {}))
      state = ManagedEntryPlanState.from_dict(
        self._mapping(custom.get(MANAGED_ENTRY_STATE_KEY))
      )
      if (
        reconciled_zero_intent_id
        and state.pending_intent_id == reconciled_zero_intent_id
      ):
        state.apply_order_terminal(
          status="RECONCILED_ZERO_FILL",
          timestamp_ms=int(time_utils.now().timestamp() * 1000),
          cooldown_after_reject_seconds=0,
        )
      state.phase = phase
      state.last_decision = {
        **dict(state.last_decision or {}),
        "reason": reason or f"ENTRY_PLAN_{phase.value}",
      }
      custom[MANAGED_ENTRY_STATE_KEY] = state.to_dict()
      saved = await repo.upsert_state(
        plan_id,
        cash=float(getattr(record, "cash", 0.0) or 0.0),
        frozen_cash=float(getattr(record, "frozen_cash", 0.0) or 0.0),
        total_asset=float(getattr(record, "total_asset", 0.0) or 0.0),
        custom_state=custom,
        expected_version=(int(record.version) if record is not None else 0),
      )
      if not saved:
        raise RuntimeError("计划阶段版本冲突，请刷新后重试")

  async def _request_terminal(
    self,
    plan_id: str,
    status: EntryPlanStatus,
    *,
    reason: str,
    pending_work: bool = False,
    reconciled_zero_intent_id: str = "",
  ) -> EntryPlanStatus:
    if status not in {EntryPlanStatus.CANCELLED, EntryPlanStatus.EXPIRED}:
      raise ValueError("EntryPlan 终态请求只能是 CANCELLED 或 EXPIRED")

    runtime = self._runtime_manager.get_run(plan_id)
    if runtime is not None and runtime.strategy is not None:
      raw_state = runtime.strategy.state.get(MANAGED_ENTRY_STATE_KEY, {})
      state = ManagedEntryPlanState.from_dict(self._mapping(raw_state))
      if (
        reconciled_zero_intent_id
        and state.pending_intent_id == reconciled_zero_intent_id
      ):
        state.apply_order_terminal(
          status="RECONCILED_ZERO_FILL",
          timestamp_ms=int(time_utils.now().timestamp() * 1000),
          cooldown_after_reject_seconds=0,
        )
      state.request_terminal(
        status,
        reason=reason,
        pending_work=pending_work,
      )
      state.last_decision = {
        **dict(state.last_decision or {}),
        "reason": reason or f"ENTRY_PLAN_{status.value}",
      }
      value = state.to_dict()
      self._runtime_manager.executor.apply_external_state_patch(
        plan_id,
        RuntimeStatePatch(
          set={MANAGED_ENTRY_STATE_KEY: value},
          append_events=[
            {
              "type": f"ENTRY_PLAN_{status.value}_REQUESTED",
              "phase": state.phase.value,
              "reason": reason or f"ENTRY_PLAN_{status.value}",
            }
          ],
        ),
      )
      if runtime.state_manager is not None:
        runtime.state_manager.set_custom(MANAGED_ENTRY_STATE_KEY, value)
        if not await runtime.state_manager.save_snapshot():
          raise RuntimeError("计划终态请求快照持久化失败")
      return state.phase

    async with self._session_factory() as db:
      repo = StrategyRunStateRepository(db)
      record = await repo.get_state(plan_id)
      custom = self._mapping(getattr(record, "custom_state", {}))
      state = ManagedEntryPlanState.from_dict(
        self._mapping(custom.get(MANAGED_ENTRY_STATE_KEY))
      )
      if (
        reconciled_zero_intent_id
        and state.pending_intent_id == reconciled_zero_intent_id
      ):
        state.apply_order_terminal(
          status="RECONCILED_ZERO_FILL",
          timestamp_ms=int(time_utils.now().timestamp() * 1000),
          cooldown_after_reject_seconds=0,
        )
      state.request_terminal(
        status,
        reason=reason,
        pending_work=pending_work,
      )
      state.last_decision = {
        **dict(state.last_decision or {}),
        "reason": reason or f"ENTRY_PLAN_{status.value}",
      }
      custom[MANAGED_ENTRY_STATE_KEY] = state.to_dict()
      saved = await repo.upsert_state(
        plan_id,
        cash=float(getattr(record, "cash", 0.0) or 0.0),
        frozen_cash=float(getattr(record, "frozen_cash", 0.0) or 0.0),
        total_asset=float(getattr(record, "total_asset", 0.0) or 0.0),
        custom_state=custom,
        expected_version=(int(record.version) if record is not None else 0),
      )
      if not saved:
        raise RuntimeError("计划终态请求版本冲突，请刷新后重试")
      return state.phase

  async def _require_plan_not_terminal(self, plan_id: str) -> None:
    runtime = self._runtime_manager.get_run(plan_id)
    state: ManagedEntryPlanState
    if runtime is not None and runtime.strategy is not None:
      state = ManagedEntryPlanState.from_dict(
        self._mapping(runtime.strategy.state.get(MANAGED_ENTRY_STATE_KEY, {}))
      )
    elif runtime is not None and getattr(runtime, "state_manager", None) is not None:
      state = ManagedEntryPlanState.from_dict(
        self._mapping(runtime.state_manager.get_custom(MANAGED_ENTRY_STATE_KEY, {}))
      )
    else:
      async with self._session_factory() as db:
        record = await StrategyRunStateRepository(db).get_state(plan_id)
      custom = self._mapping(getattr(record, "custom_state", {}))
      state = ManagedEntryPlanState.from_dict(
        self._mapping(custom.get(MANAGED_ENTRY_STATE_KEY))
      )

    if state.terminal_requested is not None or state.phase in {
      EntryPlanStatus.CANCELLED,
      EntryPlanStatus.EXPIRED,
      EntryPlanStatus.COMPLETED,
    }:
      raise ValueError("ENTRY_PLAN_TERMINAL:计划已终止，继续操作请新建计划")

  async def _active_phase(self, plan_id: str) -> EntryPlanStatus:
    facts = await self._facts(plan_id)
    return (
      EntryPlanStatus.ACCUMULATING
      if facts.filled_volume > 0 or facts.filled_amount_cny > 0
      else EntryPlanStatus.ARMED
    )

  def _install_runtime_config(
    self,
    plan_id: str,
    config: ManagedEntryPlanConfig,
  ) -> None:
    runtime = self._runtime_manager.get_run(plan_id)
    if runtime is None or runtime.strategy is None:
      return
    runtime.strategy.context.parameters = runtime.context.parameters
    if isinstance(runtime.strategy, AshareManagedEntryPlanStrategy):
      runtime.strategy._config = config

  @classmethod
  def _build_config(
    cls,
    raw_input: Mapping[str, Any],
    *,
    plan_id: str,
    account_id: str,
    config_version: int,
  ) -> ManagedEntryPlanConfig:
    instrument_code = cls._required_text(
      raw_input.get("instrument_code"), "股票代码不能为空"
    ).upper()
    bucket = str(raw_input.get("bucket") or "").strip().lower()
    trigger_rules = [
      cls._normalize_rule(item) for item in list(raw_input.get("trigger_rules") or [])
    ]
    raw_exit = cls._mapping(
      raw_input.get("exit_protection", raw_input.get("exit_plan_template"))
    )
    exit_template = cls._build_exit_template(
      raw_exit,
      plan_id=plan_id,
      account_id=account_id,
      instrument_code=instrument_code,
      bucket=bucket,
      config_version=config_version,
    )
    raw = {
      "template_version": 1,
      "config_version": config_version,
      "instrument_code": instrument_code,
      "bucket": bucket,
      "target_policy": cls._mapping(raw_input.get("target_policy")),
      "trigger_rules": trigger_rules,
      "pacing_policy": {
        **cls._mapping(raw_input.get("pacing_policy")),
        "max_open_orders": 1,
      },
      "execution_policy": cls._mapping(raw_input.get("execution_policy")),
      "completion_policy": cls._mapping(raw_input.get("completion_policy")),
      "exit_plan_template": exit_template,
    }
    try:
      return ManagedEntryPlanConfig.from_dict(raw)
    except (TypeError, ValueError) as exc:
      raise ValueError(f"建仓/加仓计划配置无效：{exc}") from exc

  @staticmethod
  def _normalize_rule(value: Any) -> dict[str, Any]:
    raw = EntryPlanService._mapping(value)
    rule_type = str(raw.get("rule_type") or "").upper()
    parameters: dict[str, Any] = {}
    if raw.get("preset_id") is not None:
      parameters["preset_id"] = str(raw.get("preset_id") or "")
    if rule_type == "PRICE_LADDER":
      parameters["levels"] = [
        {
          key: item[key]
          for key in (
            "level_id",
            "trigger_price",
            "tranche_amount_cny",
            "tranche_volume",
            "priority",
          )
          if key in item and item[key] is not None
        }
        for item in (
          EntryPlanService._mapping(level)
          for level in list(raw.get("ladder_levels") or [])
        )
      ]
    elif rule_type == "TREND_PULLBACK_CONFIRMATION":
      field_map = {
        "min_pullback_pct": "pullback_pct",
        "max_pullback_pct": "max_pullback_pct",
        "rebound_confirmation_pct": "rebound_pct",
        "fast_ema_period": "fast_ema_period",
        "slow_ema_period": "slow_ema_period",
      }
      parameters.update(
        {
          target: raw[source]
          for source, target in field_map.items()
          if raw.get(source) is not None
        }
      )
    elif (
      rule_type == "MANUAL_TRIGGER" and raw.get("manual_trigger_sequence") is not None
    ):
      parameters["trigger_sequence"] = raw["manual_trigger_sequence"]
    parameters.update(EntryPlanService._mapping(raw.get("parameters")))
    return {
      "rule_id": str(raw.get("rule_id") or ""),
      "rule_type": rule_type,
      "priority": int(raw.get("priority", 0) or 0),
      "enabled": bool(raw.get("enabled", True)),
      "once": bool(raw.get("once", False)),
      "parameters": parameters,
    }

  @staticmethod
  def _build_exit_template(
    raw: Mapping[str, Any],
    *,
    plan_id: str,
    account_id: str,
    instrument_code: str,
    bucket: str,
    config_version: int,
  ) -> Optional[dict[str, Any]]:
    if not raw or not bool(raw.get("enabled", True)):
      return None
    rules: list[ExitRuleSpec] = []
    if raw.get("stop_price") is not None:
      rules.append(
        ExitRuleSpec(
          rule_id=f"{plan_id}:entry-stop",
          strategy=ExitRuleType.STOP_PRICE,
          priority=1000,
          parameters={"stop_price": float(raw["stop_price"])},
        )
      )
    if raw.get("gross_take_profit_pct") is not None:
      rules.append(
        ExitRuleSpec(
          rule_id=f"{plan_id}:entry-take-profit",
          strategy=ExitRuleType.GROSS_TAKE_PROFIT,
          priority=700,
          parameters={"target_profit_pct": float(raw["gross_take_profit_pct"])},
        )
      )
    if raw.get("trailing_arm_profit_pct") is not None:
      arm = float(raw["trailing_arm_profit_pct"])
      drawdown = float(raw.get("trailing_drawdown_pct", 1.0) or 1.0)
      rules.append(
        ExitRuleSpec(
          rule_id=f"{plan_id}:entry-trailing-profit",
          strategy=ExitRuleType.TRAILING_NET_PROFIT,
          priority=800,
          parameters={
            "target_profit_pct": arm,
            "base_floor_pct": max(0.0, arm - drawdown),
            "initial_gap_pct": drawdown,
            "max_gap_pct": drawdown,
          },
        )
      )
    if raw.get("max_holding_days") is not None:
      rules.append(
        ExitRuleSpec(
          rule_id=f"{plan_id}:entry-max-holding-days",
          strategy=ExitRuleType.MAX_HOLDING_DAYS,
          priority=600,
          parameters={
            "max_holding_trading_days": int(raw["max_holding_days"]),
            "exit_time": "14:50",
          },
        )
      )
    if not rules:
      raise ValueError("已启用卖出保护，但没有配置任何保护规则")
    return ExitPlanTemplate(
      plan_id=f"{plan_id}:entry-protection",
      source_type="ENTRY_PLAN",
      source_id=plan_id,
      account_id=account_id,
      instrument_code=instrument_code,
      bucket=bucket,
      run_id=plan_id,
      config_version=config_version,
      rules=rules,
      execution=ExitExecutionPolicy(execution_mode="MANUAL_CONFIRM"),
      metadata={"entry_plan_id": plan_id},
      auto_exit_authorized=False,
    ).to_dict()

  @staticmethod
  def _build_parameters(
    *,
    account_id: str,
    actor_user_id: str,
    note: str,
    config: ManagedEntryPlanConfig,
  ) -> dict[str, Any]:
    return {
      "account_id": account_id,
      "entry_plan_actor_user_id": actor_user_id,
      "entry_plan_note": str(note or "")[:500],
      "initial_capital": config.target_policy.baseline_snapshot.total_asset_cny,
      "enable_reserve": True,
      "enforce_trading_hours": True,
      MANAGED_ENTRY_STATE_KEY: config.to_dict(),
    }

  @staticmethod
  def _validate_target_not_below_fills(
    config: ManagedEntryPlanConfig,
    facts: EntryPlanFacts,
  ) -> None:
    if config.target_policy.max_total_amount_cny + 1e-8 < facts.filled_amount_cny:
      raise ValueError("计划总预算不能低于已经真实成交的金额")
    additional_volume = config.target_policy.additional_volume
    if additional_volume is not None and int(additional_volume) < facts.filled_volume:
      raise ValueError("新增股数目标不能低于已经真实成交的股数")

  @staticmethod
  def _run_mode(config: ManagedEntryPlanConfig) -> StrategyRunMode:
    if config.execution_policy.environment == EntryEnvironment.PAPER:
      return StrategyRunMode.PAPER
    if config.execution_policy.environment == EntryEnvironment.LIVE:
      return StrategyRunMode.LIVE
    raise ValueError("买入托管计划仅支持模拟或实盘环境")

  @staticmethod
  def _entry_environment(raw_input: Mapping[str, Any]) -> EntryEnvironment:
    raw_execution = EntryPlanService._mapping(raw_input.get("execution_policy"))
    try:
      return EntryEnvironment(str(raw_execution.get("environment") or "PAPER"))
    except ValueError as exc:
      raise ValueError("买入托管计划仅支持模拟或实盘环境") from exc

  @staticmethod
  def _requires_live_auto_authorization(config: ManagedEntryPlanConfig) -> bool:
    return (
      config.execution_policy.environment == EntryEnvironment.LIVE
      and config.execution_policy.authorization_mode == EntryAuthorizationMode.AUTO
    )

  @staticmethod
  def _require_version(config: ManagedEntryPlanConfig, expected: int) -> None:
    if int(config.config_version) != int(expected):
      raise ValueError(
        f"计划版本冲突：当前为 {config.config_version}，请求为 {expected}"
      )

  def _runtime_is_running(self, plan_id: str) -> bool:
    runtime = self._runtime_manager.get_run(plan_id)
    return runtime is not None and self._status_value(runtime.status) in {
      "RUNNING",
      "STARTING",
    }

  @staticmethod
  def _intent_preview(
    intent: Any,
    *,
    valid: bool,
    code: str,
    message: str,
    signal_price: float,
    latest_price: float,
    deviation_bps: float,
    sized_volume: int = 0,
    final_volume: int = 0,
    risk_action: str = "REJECT",
  ) -> dict[str, Any]:
    expiry = dict(intent.expiry_policy or {})
    return {
      "intent_id": intent.intent_id,
      "plan_id": intent.run_id,
      "instrument_code": intent.instrument_code,
      "valid": bool(valid),
      "code": str(code or ""),
      "message": str(message or ""),
      "signal_price": signal_price,
      "latest_price": latest_price,
      "price_deviation_bps": deviation_bps,
      "requested_amount_cny": float(intent.target_amount or 0.0),
      "sized_volume": int(sized_volume or 0),
      "final_volume": int(final_volume or 0),
      "risk_action": risk_action,
      "expires_at_ms": int(expiry.get("expire_at_ms", 0) or 0),
    }

  @staticmethod
  def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
      return dict(value)
    if isinstance(value, str):
      try:
        parsed = json.loads(value)
      except (TypeError, ValueError):
        return {}
      return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}

  @staticmethod
  def _required_text(value: Any, message: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
      raise ValueError(message)
    return normalized

  @staticmethod
  def _positive_int(value: Any, message: str) -> int:
    try:
      normalized = int(value)
    except (TypeError, ValueError) as exc:
      raise ValueError(message) from exc
    if normalized <= 0:
      raise ValueError(message)
    return normalized

  @staticmethod
  def _finite_positive(value: Any) -> Optional[float]:
    try:
      normalized = float(value)
    except (TypeError, ValueError):
      return None
    return normalized if math.isfinite(normalized) and normalized > 0 else None

  @staticmethod
  def _timestamp_token(value: Any) -> Optional[str]:
    return (
      value.isoformat(timespec="microseconds") if isinstance(value, datetime) else None
    )

  @staticmethod
  def _require_fresh_timestamp(
    value: Any,
    *,
    now: datetime,
    max_age: timedelta,
    code: str,
    message: str,
  ) -> None:
    if not isinstance(value, datetime):
      raise ValueError(f"{code}:{message}")
    snapshot_at = time_utils.to_shanghai(value)
    checked_at = time_utils.to_shanghai(now)
    age = checked_at - snapshot_at
    if age < timedelta(0) or age > max_age:
      raise ValueError(f"{code}:{message}")

  @staticmethod
  def _status_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").upper()


__all__ = [
  "EntryPlanFacts",
  "EntryPlanService",
  "MANAGED_ENTRY_STRATEGY_CLASS_NAME",
]
