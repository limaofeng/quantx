"""Engine-owned orchestration for the account-level limit-up board assistant."""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from quantx_domain.strategies import AshareLimitUpBoardAssistantStrategy
from quantx_domain.trading.first_board_promotion import FIRST_BOARD_MODEL_VERSION
from quantx_domain.trading.limit_up_board_universe import (
  liquidity_cap_amount,
  select_limit_up_board_universe,
  target_position_pct,
)
from quantx_infrastructure.core.assistant_strategy_policy import (
  LIMIT_UP_BOARD_ASSISTANT_STRATEGY_CLASS_NAME,
  LIMIT_UP_BOARD_STRATEGY_CLASS_NAME,
)
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.enums import StrategyRunMode
from quantx_infrastructure.models.limit_up_board_assistant import (
  LimitUpBoardAssistantConfig,
  LimitUpBoardCandidateArm,
)
from quantx_infrastructure.repositories.first_board_promotion_repository import (
  FirstBoardPromotionRepository,
)
from quantx_infrastructure.repositories.limit_up_board_assistant_repository import (
  LimitUpBoardAssistantConfigRepository,
  LimitUpBoardCandidateArmRepository,
)
from quantx_infrastructure.repositories.strategy_repository import StrategyRepository
from quantx_infrastructure.repositories.strategy_run_repository import (
  StrategyRunRepository,
)
from quantx_infrastructure.services.account_execution_safety_service import (
  AccountExecutionSafetyService,
)
from quantx_infrastructure.services.limit_up_board_assistant_projection_service import (
  limit_up_board_assistant_projection_service,
)
from quantx_infrastructure.services.limit_up_radar import limit_up_radar_store

logger = logging.getLogger(__name__)

ASSISTANT_DEFAULTS: Dict[str, Any] = {
  # Read-only compatibility fields for the V1 GraphQL projection.  V2 sizing
  # never consumes them.
  "target_entry_amount": 0.0,
  "auto_signal_min_score": 0.0,
  "max_single_position_pct": 0.02,
  "max_daily_exposure_pct": 0.06,
  "planned_tail_loss_pct": 0.0015,
  "liquidity_participation_pct": 0.005,
  "max_open_positions": 2,
  "max_ranked_candidates": 5,
  "entry_distance_ticks": 1,
  "entry_start_time": "09:30",
  "entry_end_time": "14:50",
  "approval_ttl_ms": 15_000,
  "entry_order_ttl_ms": 15_000,
  "max_price_deviation_bps": 20.0,
  "execution_quote_max_age_seconds": 3.0,
  "max_entry_attempts_per_day": 1,
  "exclude_one_word_limit_up": True,
  "require_data_quality_ok": True,
  "exit_limit_break_ticks": 1,
  "exit_min_seal_seconds": 3.0,
  "exit_trailing_arm_profit_pct": 2.0,
  "exit_trailing_drawdown_pct": 3.0,
  "exit_trailing_percent": 50.0,
  "max_holding_trading_days": 2,
  "max_holding_exit_time": "14:50",
  "exit_max_slippage_bps": 50.0,
  "promotion_model_mode": "SHADOW",
}


class LimitUpBoardAssistantService:
  def __init__(self, runtime_manager: Any, interval_seconds: float = 1.0):
    self.runtime_manager = runtime_manager
    self.interval_seconds = max(0.5, float(interval_seconds or 1.0))
    self._task: Optional[asyncio.Task] = None
    self._stopping = asyncio.Event()
    self._account_locks: Dict[str, asyncio.Lock] = {}
    self._last_metadata: Dict[str, Dict[str, Dict[str, Any]]] = {}

  async def start(self) -> None:
    if self._task and not self._task.done():
      return
    self._stopping = asyncio.Event()
    self._task = asyncio.create_task(self._run(), name="LimitUpBoardAssistant")
    logger.info("账户级打板助手协调器已启动")

  async def stop(self) -> None:
    self._stopping.set()
    if not self._task:
      return
    self._task.cancel()
    try:
      await self._task
    except asyncio.CancelledError:
      pass
    self._task = None
    logger.info("账户级打板助手协调器已停止")

  async def save_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    account_id = self._account_id(payload)
    mode = self._mode(payload.get("mode", "paper"))
    enabled = bool(payload.get("enabled", False))
    acknowledged = bool(payload.get("auto_exit_acknowledged", False))
    if enabled and mode == StrategyRunMode.LIVE and not acknowledged:
      raise ValueError("启动实盘打板助手前必须确认自动卖出授权")
    if enabled and mode == StrategyRunMode.LIVE:
      readiness = await AccountExecutionSafetyService().status(account_id)
      if not readiness.get("can_activate_automation", False):
        reasons = "；".join(readiness.get("blocked_reasons") or [])
        raise ValueError(reasons or "账户尚未通过实盘就绪检查")
    settings = {
      key: payload.get(key, default)
      for key, default in ASSISTANT_DEFAULTS.items()
    }
    self._validate_settings(settings)
    await self._validate_rollout_gate(settings)
    async for db in get_async_db():
      repo = LimitUpBoardAssistantConfigRepository(db)
      config = await repo.find_by_account(account_id)
      if config is None:
        config = LimitUpBoardAssistantConfig(account_id=account_id)
      config.enabled = enabled
      config.mode = mode.value
      config.auto_exit_acknowledged = acknowledged
      config.settings = settings
      config.config_version = int(config.config_version or 0) + 1
      config.last_error = None
      await repo.save(config)
      break
    return await self.reconcile_account(account_id)

  async def arm_candidate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    account_id = self._account_id(payload)
    code = self._instrument_code(payload.get("instrument_code"))
    radar_item = await self._radar_item(code)
    if radar_item is None:
      raise ValueError("候选已离开打板雷达，请刷新后重试")
    blocked = set(radar_item.get("blocked_reasons") or [])
    if bool(radar_item.get("is_stale")) or "STALE_MARKET_DATA" in blocked:
      raise ValueError("候选行情已过期，只能观察，不能加入布防")
    if blocked.intersection(
      {"ONE_WORD_LIMIT_UP", "LIMIT_UP_ALREADY_REACHED"}
    ):
      raise ValueError("候选已封板或属于一字板，不能加入布防")
    actor_id = str(payload.get("actor_id", "") or "")[:64]
    idempotency_key = str(payload.get("idempotency_key", "") or "")[:128]
    trade_date = time_utils.to_shanghai(time_utils.now()).date()
    async for db in get_async_db():
      repo = LimitUpBoardCandidateArmRepository(db)
      arm = await repo.find(account_id, trade_date, code)
      if arm is None:
        arm = LimitUpBoardCandidateArm(
          account_id=account_id,
          trade_date=trade_date,
          instrument_code=code,
        )
      if arm.armed and idempotency_key and arm.idempotency_key == idempotency_key:
        break
      arm.armed = True
      arm.source = "MANUAL"
      arm.actor_id = actor_id
      arm.idempotency_key = idempotency_key
      arm.arm_version = int(arm.arm_version or 0) + 1
      arm.disarmed_at = None
      await repo.save(arm)
      break
    return await self.reconcile_account(account_id)

  async def disarm_candidate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    account_id = self._account_id(payload)
    code = self._instrument_code(payload.get("instrument_code"))
    trade_date = time_utils.to_shanghai(time_utils.now()).date()
    async for db in get_async_db():
      repo = LimitUpBoardCandidateArmRepository(db)
      arm = await repo.find(account_id, trade_date, code)
      if arm is not None and arm.armed:
        arm.armed = False
        arm.actor_id = str(payload.get("actor_id", "") or "")[:64]
        arm.idempotency_key = str(payload.get("idempotency_key", "") or "")[:128]
        arm.arm_version = int(arm.arm_version or 0) + 1
        arm.disarmed_at = time_utils.now()
        await repo.save(arm)
      break
    return await self.reconcile_account(account_id)

  async def set_candidate_preference(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    account_id = self._account_id(payload)
    code = self._instrument_code(payload.get("instrument_code"))
    preference = str(payload.get("preference") or "PREFER").upper()
    if preference not in {"PREFER", "IGNORE"}:
      raise ValueError("候选偏好只能是 PREFER 或 IGNORE")
    trade_date = time_utils.to_shanghai(time_utils.now()).date()
    async for db in get_async_db():
      await FirstBoardPromotionRepository(db).upsert_preference(
        account_id=account_id,
        trade_date=trade_date,
        instrument_code=code,
        preference=preference,
        actor_id=str(payload.get("actor_id") or ""),
        idempotency_key=str(payload.get("idempotency_key") or ""),
      )
      break
    return await self.reconcile_account(account_id)

  async def get_monitor(self, account_id: str) -> Dict[str, Any]:
    normalized = self._account_id({"account_id": account_id})
    config = await self._load_config(normalized)
    data = self._config_data(config, normalized)
    trade_date = time_utils.to_shanghai(time_utils.now()).date()
    arms = await self._load_arms(normalized, trade_date)
    runtime = self._runtime(config.strategy_run_id if config else None)
    pending = list(runtime.pending_approvals.values()) if runtime else []
    active_plans = runtime.exit_plan_book.active_plans() if runtime else []
    run_status = str(getattr(getattr(runtime, "status", None), "value", "STOPPED"))
    if runtime is not None and not data["enabled"] and self._runtime_has_open_work(runtime):
      run_status = "DRAINING"
    data.update(
      {
        "armed_candidates": [
          {
            "instrument_code": arm.instrument_code,
            "source": arm.source,
            "arm_version": int(arm.arm_version or 0),
            "armed_at": arm.updated_at,
          }
          for arm in arms
        ],
        "manual_armed_count": len(arms),
        "pending_signal_count": len(pending),
        "active_exit_plan_count": len(active_plans),
        "monitored_count": len(runtime.context.instruments or []) if runtime else 0,
        "run_status": run_status,
      }
    )
    try:
      readiness = await AccountExecutionSafetyService().status(normalized)
      data.update(
        {
          "engine_status": readiness["engine_status"],
          "agent_status": readiness["agent_status"],
          "reconcile_status": readiness["reconcile_status"],
          "kill_switch": readiness["kill_switch"],
          "can_approve": readiness["can_increase_risk"],
          "can_activate_live": readiness["can_activate_automation"],
          "blocked_reasons": readiness["blocked_reasons"],
        }
      )
    except Exception as exc:
      data.update(
        {
          "engine_status": "OFFLINE",
          "agent_status": "OFFLINE",
          "reconcile_status": "UNKNOWN",
          "kill_switch": False,
          "can_approve": False,
          "can_activate_live": False,
          "blocked_reasons": [str(exc)],
        }
      )
    return await limit_up_board_assistant_projection_service.save(normalized, data)

  async def reconcile_account(self, account_id: str) -> Dict[str, Any]:
    normalized = self._account_id({"account_id": account_id})
    lock = self._account_locks.setdefault(normalized, asyncio.Lock())
    async with lock:
      await self._reconcile_locked(normalized)
    return await self.get_monitor(normalized)

  async def _reconcile_locked(self, account_id: str) -> None:
    config = await self._load_config(account_id)
    if config is None:
      return
    errors: List[str] = []
    runtime = self._runtime(config.strategy_run_id)
    if config.strategy_run_id and runtime is None:
      config.strategy_run_id = None

    if runtime is None:
      adopted = await self._adopt_existing_run(account_id)
      if adopted:
        config.strategy_run_id = adopted
        runtime = self._runtime(adopted)

    legacy_conflict = await self._drain_legacy_runs(account_id)
    if legacy_conflict:
      errors.append(legacy_conflict)

    radar = await limit_up_radar_store.read_radar() or {}
    arms = await self._load_arms(
      account_id,
      time_utils.to_shanghai(time_utils.now()).date(),
    )
    preferences = await self._load_preferences(
      account_id,
      time_utils.to_shanghai(time_utils.now()).date(),
    )
    metadata, desired = self._build_universe(
      config, radar, arms, runtime, preferences
    )

    if runtime and not config.enabled:
      for intent_id in list(runtime.pending_approvals):
        await self.runtime_manager.executor.reject_trade_intent(
          runtime.run_id,
          intent_id,
          reason="BOARD_ASSISTANT_DISABLED",
        )
      await self.runtime_manager.executor.cancel_open_buy_orders(
        runtime.run_id,
        reason="BOARD_ASSISTANT_DISABLED",
      )
      metadata, desired = self._build_universe(
        config, radar, [], runtime, preferences
      )

    if runtime and str(runtime.context.mode.value) != str(config.mode).lower():
      if self._runtime_has_open_work(runtime):
        errors.append("当前助手仍有待处理订单或退出计划，不能切换运行模式")
      else:
        await self.runtime_manager.stop_strategy(runtime.run_id)
        config.strategy_run_id = None
        runtime = None

    if not runtime and config.enabled:
      try:
        config.strategy_run_id = await self._start_runtime(config, account_id)
        runtime = self._runtime(config.strategy_run_id)
      except Exception as exc:
        errors.append(f"打板助手启动失败: {exc}")

    if runtime:
      if config.enabled or self._runtime_has_open_work(runtime):
        parameters = self._strategy_parameters(config, account_id)
        await self.runtime_manager.update_run_parameters(runtime.run_id, parameters)
        current = sorted(set(runtime.context.instruments or []))
        if current != desired or self._metadata_changed(runtime, metadata):
          result = await self.runtime_manager.reconcile_run_instruments(
            runtime.run_id,
            desired,
            instrument_metadata=metadata,
          )
          if result.get("added") or result.get("removed"):
            config.universe_revision = int(config.universe_revision or 0) + 1
          self._last_metadata[runtime.run_id] = metadata
      elif not config.enabled:
        await self.runtime_manager.stop_strategy(runtime.run_id)
        config.strategy_run_id = None

    config.last_reconciled_at = time_utils.now()
    config.last_error = "；".join(errors) if errors else None
    async for db in get_async_db():
      repo = LimitUpBoardAssistantConfigRepository(db)
      current = await repo.find_by_account(account_id)
      if current is not None:
        current.strategy_run_id = config.strategy_run_id
        current.universe_revision = config.universe_revision
        current.last_reconciled_at = config.last_reconciled_at
        current.last_error = config.last_error
        await repo.save(current)
      break

  def _build_universe(
    self,
    config: LimitUpBoardAssistantConfig,
    radar: Dict[str, Any],
    arms: List[LimitUpBoardCandidateArm],
    runtime: Any,
    preferences: Optional[Dict[str, Any]] = None,
  ) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    preferences = preferences or {}
    manual = {arm.instrument_code: arm for arm in arms if arm.armed}
    sticky = self._runtime_open_codes(runtime)
    selection = select_limit_up_board_universe(
      list(radar.get("items") or []),
      settings={**ASSISTANT_DEFAULTS, **dict(config.settings or {})},
      enabled=bool(config.enabled),
      preferences={
        str(code).upper(): str(getattr(value, "preference", "") or "")
        for code, value in preferences.items()
      },
      sticky_codes=sorted(sticky),
      force_preferred_codes=sorted(manual),
      arm_versions={
        code: int(arm.arm_version or 0) for code, arm in manual.items()
      },
    )
    return selection.metadata, list(selection.instruments)

  async def _start_runtime(
    self, config: LimitUpBoardAssistantConfig, account_id: str
  ) -> str:
    async for db in get_async_db():
      strategy = await StrategyRepository(db).find_by_class_name(
        LIMIT_UP_BOARD_ASSISTANT_STRATEGY_CLASS_NAME
      )
      if strategy is None:
        raise ValueError("账户级打板助手策略模板尚未注册")
      strategy_id = int(strategy.id)
      break
    else:
      raise ValueError("无法读取账户级打板助手策略模板")
    run_id = await self.runtime_manager.run_strategy(
      strategy_id=strategy_id,
      strategy_class=AshareLimitUpBoardAssistantStrategy,
      mode=self._mode(config.mode),
      instruments=[],
      parameters=self._strategy_parameters(config, account_id),
      name=f"账户级打板助手-{account_id}",
      auto_start=False,
    )
    if not await self.runtime_manager.start_strategy(run_id):
      raise ValueError("账户级打板助手启动失败")
    return run_id

  def _strategy_parameters(
    self, config: LimitUpBoardAssistantConfig, account_id: str
  ) -> Dict[str, Any]:
    settings = {**ASSISTANT_DEFAULTS, **dict(config.settings or {})}
    max_position = float(settings.get("max_single_position_pct", 0.05) or 0.05)
    settings.update(
      {
        "account_id": account_id,
        "entry_execution_mode": "MANUAL_CONFIRM",
        # An acknowledgement stored in strategy configuration is not an
        # exact, device-bound authorization for a LIVE exit plan.  PAPER does
        # not need the flag and LIVE authorization is granted after the plan
        # has been persisted and previewed.
        "auto_exit_authorized": False,
        "global_config_version": int(config.config_version or 0),
        "position_profile_overrides": {
          "max_position_pct": max_position,
          "swing_max_pct": max_position,
          "allow_swing_buy": True,
        },
        "risk_caps": {
          "max_position_pct": max_position,
          "max_new_buy_pct_today": float(
            settings.get("max_daily_exposure_pct", 0.06) or 0.06
          ),
          "max_open_positions": int(settings.get("max_open_positions", 2) or 2),
        },
        "enable_reserve": True,
        "enforce_trading_hours": True,
      }
    )
    return settings

  async def _adopt_existing_run(self, account_id: str) -> Optional[str]:
    async for db in get_async_db():
      runs = await StrategyRunRepository(db).find_active_runs_by_strategy_class(
        LIMIT_UP_BOARD_ASSISTANT_STRATEGY_CLASS_NAME
      )
      for run in runs:
        parameters = dict(run.parameters or {})
        if str(parameters.get("account_id") or "") == account_id:
          return str(run.id)
      break
    return None

  async def _drain_legacy_runs(self, account_id: str) -> str:
    async for db in get_async_db():
      runs = await StrategyRunRepository(db).find_active_runs_by_strategy_class(
        LIMIT_UP_BOARD_STRATEGY_CLASS_NAME
      )
      break
    else:
      runs = []
    blocked: List[str] = []
    for run in runs:
      parameters = dict(run.parameters or {})
      bound_account = str(parameters.get("account_id") or "")
      if bound_account and bound_account != account_id:
        continue
      runtime = self._runtime(str(run.id))
      if runtime is None:
        blocked.append(str(run.id))
        continue
      for intent_id in list(runtime.pending_approvals):
        await self.runtime_manager.executor.reject_trade_intent(
          runtime.run_id,
          intent_id,
          reason="FIRST_BOARD_V2_MIGRATION",
        )
      await self.runtime_manager.executor.cancel_open_buy_orders(
        runtime.run_id,
        reason="FIRST_BOARD_V2_MIGRATION",
      )
      if runtime.exit_plan_book.active_plans():
        blocked.append(str(run.id))
        continue
      await self.runtime_manager.stop_strategy(str(run.id))
    if blocked:
      return "旧打板实例已停止新买入，已有仓位继续排水：" + "、".join(blocked)
    return ""

  def _runtime(self, run_id: Optional[str]) -> Any:
    return self.runtime_manager.get_run(str(run_id)) if run_id else None

  @staticmethod
  def _runtime_has_open_work(runtime: Any) -> bool:
    return bool(
      runtime
      and (
        runtime.pending_approvals
        or runtime.exit_plan_book.active_plans()
        or LimitUpBoardAssistantService._open_broker_orders(runtime)
      )
    )

  @staticmethod
  def _runtime_open_codes(runtime: Any) -> set[str]:
    if runtime is None:
      return set()
    codes = {
      str(intent.instrument_code).upper()
      for intent in runtime.pending_approvals.values()
    }
    codes.update(
      str(plan.template.instrument_code).upper()
      for plan in runtime.exit_plan_book.active_plans()
    )
    for order in LimitUpBoardAssistantService._open_broker_orders(runtime):
      request = getattr(order, "request", None)
      code = str(
        getattr(request, "instrument_code", "")
        or getattr(order, "instrument_code", "")
        or ""
      ).upper()
      if code:
        codes.add(code)
    return codes

  @staticmethod
  def _open_broker_orders(runtime: Any) -> List[Any]:
    broker = getattr(runtime, "broker", None)
    orders = getattr(broker, "orders", {}) if broker else {}
    values = list(orders.values()) if isinstance(orders, dict) else []
    result: List[Any] = []
    for order in values:
      raw_status = getattr(order, "status", "")
      status = str(getattr(raw_status, "value", raw_status)).upper()
      if status in {"PENDING", "SUBMITTED", "ACCEPTED", "PARTIAL_FILLED"}:
        result.append(order)
    return result

  def _draining_universe(
    self, runtime: Any
  ) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    desired = sorted(self._runtime_open_codes(runtime))
    return (
      {
        code: {
          "eligible": False,
          "reason": "LEGACY_INSTANCE_DRAINING",
          "source": "DRAINING",
          "draining": True,
          "arm_version": 0,
          "radar_score": 0.0,
          "radar_stage": "",
          "radar_updated_at": "",
          "radar_is_stale": False,
        }
        for code in desired
      },
      desired,
    )

  def _metadata_changed(
    self, runtime: Any, metadata: Dict[str, Dict[str, Any]]
  ) -> bool:
    return self._last_metadata.get(runtime.run_id) != metadata

  async def _load_config(
    self, account_id: str
  ) -> Optional[LimitUpBoardAssistantConfig]:
    async for db in get_async_db():
      return await LimitUpBoardAssistantConfigRepository(db).find_by_account(account_id)
    return None

  async def _load_arms(
    self, account_id: str, trade_date: date
  ) -> List[LimitUpBoardCandidateArm]:
    async for db in get_async_db():
      return await LimitUpBoardCandidateArmRepository(db).list_armed(
        account_id, trade_date
      )
    return []

  async def _load_preferences(self, account_id: str, trade_date: date) -> Dict[str, Any]:
    async for db in get_async_db():
      return await FirstBoardPromotionRepository(db).list_preferences(
        account_id, trade_date
      )
    return {}

  @staticmethod
  def _target_position_pct(
    config: LimitUpBoardAssistantConfig, item: Dict[str, Any]
  ) -> float:
    return target_position_pct(
      {**ASSISTANT_DEFAULTS, **dict(config.settings or {})}, item
    )

  @staticmethod
  def _liquidity_cap_amount(
    config: LimitUpBoardAssistantConfig, item: Dict[str, Any]
  ) -> float:
    return liquidity_cap_amount(
      {**ASSISTANT_DEFAULTS, **dict(config.settings or {})}, item
    )

  async def _radar_item(self, code: str) -> Optional[Dict[str, Any]]:
    radar = await limit_up_radar_store.read_radar() or {}
    return next(
      (
        dict(item)
        for item in list(radar.get("items") or [])
        if str(item.get("code") or "").upper() == code
      ),
      None,
    )

  def _config_data(
    self, config: Optional[LimitUpBoardAssistantConfig], account_id: str
  ) -> Dict[str, Any]:
    if config is None:
      return {
        "config_id": None,
        "strategy_run_id": None,
        "account_id": account_id,
        "enabled": False,
        "mode": "paper",
        "auto_exit_acknowledged": False,
        "config_version": 0,
        "universe_revision": 0,
        **ASSISTANT_DEFAULTS,
        "last_reconciled_at": None,
        "last_error": None,
      }
    return {
      "config_id": config.id,
      "strategy_run_id": config.strategy_run_id,
      "account_id": config.account_id,
      "enabled": bool(config.enabled),
      "mode": config.mode,
      "auto_exit_acknowledged": bool(config.auto_exit_acknowledged),
      "config_version": int(config.config_version or 0),
      "universe_revision": int(config.universe_revision or 0),
      **{**ASSISTANT_DEFAULTS, **dict(config.settings or {})},
      "last_reconciled_at": config.last_reconciled_at,
      "last_error": config.last_error,
    }

  async def _run(self) -> None:
    while not self._stopping.is_set():
      try:
        async for db in get_async_db():
          configs = await LimitUpBoardAssistantConfigRepository(db).find_all_configs()
          break
        else:
          configs = []
        for config in configs:
          try:
            await self.reconcile_account(config.account_id)
          except asyncio.CancelledError:
            raise
          except Exception:
            logger.exception(
              "账户级打板助手协调失败: account=%s", config.account_id
            )
      except asyncio.CancelledError:
        raise
      except Exception:
        logger.exception("账户级打板助手周期扫描失败")
      try:
        await asyncio.wait_for(
          self._stopping.wait(), timeout=self.interval_seconds
        )
      except asyncio.TimeoutError:
        pass

  @staticmethod
  def _account_id(payload: Dict[str, Any]) -> str:
    account_id = str(payload.get("account_id", "") or "").strip()
    if not account_id:
      raise ValueError("账户不能为空")
    return account_id

  @staticmethod
  def _instrument_code(value: Any) -> str:
    code = str(value or "").strip().upper()
    if not code:
      raise ValueError("候选代码不能为空")
    return code

  @staticmethod
  def _mode(value: Any) -> StrategyRunMode:
    try:
      return StrategyRunMode(str(getattr(value, "value", value) or "paper").lower())
    except ValueError as exc:
      raise ValueError("运行模式只能是 paper 或 live") from exc

  @staticmethod
  def _validate_settings(settings: Dict[str, Any]) -> None:
    if not 0 < float(settings["max_single_position_pct"]) <= 0.30:
      raise ValueError("单标的资产上限必须在 0% 到 30% 之间")
    if not 0 < float(settings["max_daily_exposure_pct"]) <= 0.30:
      raise ValueError("当日新增风险敞口必须在 0% 到 30% 之间")
    if not 0 < float(settings["planned_tail_loss_pct"]) <= 0.02:
      raise ValueError("单笔计划尾损必须在 0% 到 2% 之间")
    if not 0 < float(settings["liquidity_participation_pct"]) <= 0.05:
      raise ValueError("流动性参与率必须在 0% 到 5% 之间")
    if not 1 <= int(settings["max_open_positions"]) <= 10:
      raise ValueError("同时持仓数必须在 1 到 10 之间")

  @staticmethod
  async def _validate_rollout_gate(settings: Dict[str, Any]) -> None:
    requested = str(settings.get("promotion_model_mode") or "SHADOW").upper()
    if requested == "SHADOW":
      return
    if requested not in {"PAPER", "LIVE"}:
      raise ValueError("首板模型发布阶段只能是 SHADOW、PAPER 或 LIVE")
    async for db in get_async_db():
      release = await FirstBoardPromotionRepository(db).get_model_release(
        FIRST_BOARD_MODEL_VERSION
      )
      break
    else:
      release = None
    if release is None:
      raise ValueError("首板模型尚无发布证据，只能运行影子阶段")
    paper_ready = bool(
      str(release.stage or "SHADOW").upper() in {"PAPER", "LIVE"}
      and int(release.sample_trading_days or 0) >= 20
      and int(release.main_board_eligible_samples or 0) >= 100
      and int(release.growth_board_eligible_samples or 0) >= 100
      and release.bootstrap_ci_lower_pct is not None
      and float(release.bootstrap_ci_lower_pct) > 0
      and bool(release.tail_loss_budget_passed)
      and bool(release.historical_rules_complete)
    )
    if not paper_ready:
      raise ValueError("影子样本、Bootstrap 置信区间或尾损门禁尚未通过")
    if requested == "LIVE" and not (
      str(release.stage or "").upper() == "LIVE"
      and bool(release.simulation_verified)
      and bool(release.live_reconciliation_verified)
    ):
      raise ValueError("模拟撮合、T+1 恢复或实盘对账门禁尚未通过")


__all__ = ["ASSISTANT_DEFAULTS", "LimitUpBoardAssistantService"]
