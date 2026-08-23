"""Engine-owned account orchestration for the dynamic-holdings T strategy."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from quantx_application.t_trade_v3 import (
  SignalPolicyChangePlanner,
  SignalPolicyChangeRequest,
  SignalPolicyConfigSnapshot,
)
from quantx_domain.trading.t_trade_opportunity_engine import OpportunityPolicy
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.enums import StrategyRunMode
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.repositories.instrument_repository import (
  InstrumentRepository,
)
from quantx_infrastructure.repositories.t_trade_global_config_repository import (
  TTradeGlobalConfigRepository,
)
from quantx_infrastructure.services.position_service import PositionService
from quantx_infrastructure.services.t_trade_monitor_projection_service import (
  t_trade_monitor_projection_service,
)
from quantx_infrastructure.services.t_trade_operations_service import (
  TTradeOperationsService,
)
from quantx_infrastructure.services.t_trade_service import TTradeService

from .t_trade_coordination import t_trade_account_coordination_lock

logger = logging.getLogger(__name__)

GLOBAL_SETTING_DEFAULTS: Dict[str, Any] = {
  "target_trade_amount": 10_000.0,
  "max_trade_amount": 12_000.0,
  "max_concurrent_batches": 3,
  "max_total_t_exposure_pct": 0.1,
  "signal_policy": OpportunityPolicy().to_dict(),
  "max_price_deviation_pct": 0.3,
  "max_exit_slippage_bps": 30.0,
  "target_profit_pct": 2.0,
  "base_floor_pct": 0.5,
  "initial_gap_pct": 1.5,
  "trailing_gap_slope": 0.25,
  "max_gap_pct": 3.0,
  "high_profit_lock_enabled": True,
  "high_profit_arm_pct": 4.0,
  "high_profit_max_drawdown_pct": 1.2,
  "rapid_reversal_enabled": True,
  "rapid_reversal_window_seconds": 15,
  "rapid_reversal_drawdown_pct": 0.8,
  "rapid_reversal_confirm_ticks": 2,
  "limit_up_touch_exit_enabled": True,
  "limit_up_touch_tolerance_ticks": 0,
  "hard_stop_enabled": False,
  "hard_stop_pct": -0.8,
  "time_exit_mode": "UNLIMITED",
  "time_exit_time": "14:50",
  "max_holding_trading_days": 5,
  "cooldown_seconds": 300,
}

ACTIVE_T_STRATEGY_RUN_STATUSES = {"pending", "starting", "running", "paused"}
CONFIG_APPLY_PENDING_MARKER = "CONFIG_APPLY_PENDING"
CONFIG_APPLIED_CODE = "CONFIG_APPLIED"
CONFIG_APPLY_PENDING_CODE = "CONFIG_APPLY_PENDING"


class TTradeConfigVersionConflict(ValueError):
  """A monitor save used a stale optimistic-concurrency token."""

  def __init__(self, expected: int, actual: int) -> None:
    self.expected = expected
    self.actual = actual
    super().__init__(
      "CONFIG_VERSION_CONFLICT: "
      f"expected_config_version={expected}, actual_config_version={actual}"
    )


class TTradeGlobalMonitorService:
  """Keep one StrategyRun synchronized with an account holdings universe."""

  def __init__(
    self,
    runtime_manager: Any = None,
    interval_seconds: float = 10.0,
  ):
    self.interval_seconds = max(2.0, float(interval_seconds or 10.0))
    self.session_service = TTradeService(runtime_manager)
    self.position_service = PositionService()
    self._task: Optional[asyncio.Task] = None
    self._stopping = asyncio.Event()

  async def start(self) -> None:
    if self._task and not self._task.done():
      return
    self._stopping = asyncio.Event()
    self._task = asyncio.create_task(self._run(), name="TTradeGlobalMonitor")
    logger.info("动态持仓做 T 监控器已启动")

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
    logger.info("动态持仓做 T 监控器已停止")

  async def save_config(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    account_id = str(payload.get("account_id", "") or "").strip()
    if not account_id:
      raise ValueError("账户不能为空")
    if "expected_config_version" not in payload:
      raise ValueError("expected_config_version 不能为空")
    try:
      expected_config_version = int(payload["expected_config_version"])
    except (TypeError, ValueError, OverflowError) as exc:
      raise ValueError("expected_config_version 必须是非负整数") from exc
    if expected_config_version < 0:
      raise ValueError("expected_config_version 必须是非负整数")
    mode = self.session_service._parse_mode(payload.get("mode", "paper"))
    settings = self._settings_from_payload(payload)
    self.session_service._validate_parameters(settings, mode)
    enabled = bool(payload.get("enabled", False))
    acknowledged = bool(payload.get("auto_exit_acknowledged", False))
    if enabled and mode == StrategyRunMode.LIVE and not acknowledged:
      raise ValueError("启动全局实盘做 T 前必须确认自动卖出授权")

    # The same account lock is also acquired by V3 manual approval.  Keep it
    # across the optimistic database mutation and runtime reconcile so an old
    # candidate is linearized before the save or invalidated before approval.
    lock = t_trade_account_coordination_lock(account_id)
    async with lock:
      async for db in get_async_db():
        repo = TTradeGlobalConfigRepository(db)
        config = await repo.find_by_account_for_update(account_id)
        actual_config_version = int(config.config_version or 0) if config else 0
        if actual_config_version != expected_config_version:
          raise TTradeConfigVersionConflict(
            expected_config_version,
            actual_config_version,
          )
        if config is None:
          config = TTradeGlobalConfig(account_id=account_id)
        config.enabled = enabled
        config.mode = mode.value
        config.auto_exit_acknowledged = acknowledged
        config.ignored_stock_codes = self._normalize_ignored_codes(
          payload.get("ignored_stock_codes", [])
        )
        config.settings = settings
        config.config_version = actual_config_version + 1
        # Mark the new version as pending before committing it.  This closes
        # the crash window between the durable config write and runtime
        # rewarm: a restart/periodic reconcile must remain fail-closed until
        # it clears this marker after a successful apply.
        config.last_error = (
          f"{CONFIG_APPLY_PENDING_MARKER}: config_version={config.config_version}"
        )
        await repo.save(config)
        break
      # Invalidate the previous runtime's entry authorization immediately
      # after the version commit, before the full reconcile starts.
      await self._block_new_entries_if_needed(
        config,
        [CONFIG_APPLY_PENDING_MARKER],
      )
      committed_run_id = config.strategy_run_id
      try:
        monitor = await self._reconcile_account_locked(account_id)
        return self._with_apply_outcome(monitor)
      except Exception as exc:
        # The config row is already committed at this point.  Never turn an
        # unexpected post-commit reconcile failure into a validation failure:
        # retain the old run link, block entry authority, and persist an
        # actionable pending error for periodic/manual recovery.
        if committed_run_id and not config.strategy_run_id:
          config.strategy_run_id = committed_run_id
        errors = [
          f"{CONFIG_APPLY_PENDING_MARKER}: 配置应用失败: {exc}",
        ]
        await self._block_new_entries_if_needed(config, errors)
        try:
          await self._save_reconcile_config(config, errors)
        except Exception as persist_exc:
          logger.exception(
            "保存做 T 配置应用失败状态失败: account=%s error=%s",
            account_id,
            persist_exc,
          )
        try:
          monitor = dict(await self.get_monitor(account_id) or {})
        except Exception as monitor_exc:
          logger.exception(
            "读取做 T 配置应用失败监控失败: account=%s error=%s",
            account_id,
            monitor_exc,
          )
          monitor = {}
        monitor["account_id"] = account_id
        monitor["config_version"] = int(config.config_version or 0)
        monitor["strategy_run_id"] = config.strategy_run_id
        monitor["last_error"] = "; ".join(errors[:20])
        return self._with_apply_outcome(monitor)

  async def preview_signal_policy(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and normalize a policy without mutating runtime or persistence."""

    account_id = str(payload.get("account_id", "") or "").strip()
    if not account_id:
      raise ValueError("账户不能为空")
    if "expected_config_version" not in payload:
      raise ValueError("expected_config_version 不能为空")
    try:
      expected = int(payload["expected_config_version"])
    except (TypeError, ValueError, OverflowError) as exc:
      raise ValueError("expected_config_version 必须是非负整数") from exc
    if expected < 0:
      raise ValueError("expected_config_version 必须是非负整数")
    config = await self._load_config(account_id)
    actual = int(config.config_version or 0) if config else 0
    if expected != actual:
      raise TTradeConfigVersionConflict(expected, actual)

    current = self.session_service._normalize_signal_policy(
      (config.settings or {}).get("signal_policy") if config else None
    )
    requested_policy = payload.get("signal_policy")
    if requested_policy is None:
      requested_policy = OpportunityPolicy().to_dict()
    try:
      plan = SignalPolicyChangePlanner().plan(
        SignalPolicyChangeRequest(
          account_id=account_id,
          expected_config_version=expected,
          signal_policy=requested_policy,
        ),
        SignalPolicyConfigSnapshot(
          account_id=account_id,
          config_version=actual,
          signal_policy=current,
        ),
      )
    except (TypeError, ValueError, OverflowError) as exc:
      return {
        "errors": [str(exc)],
        "warnings": [],
        "normalized_policy": None,
        "changed_fields": [],
        "requires_rewarm": False,
        "config_version": actual,
      }
    if not plan.valid:
      return {
        "errors": list(plan.errors),
        "warnings": [],
        "normalized_policy": None,
        "changed_fields": [],
        "requires_rewarm": False,
        "config_version": actual,
      }
    return {
      "errors": list(plan.errors),
      "warnings": list(plan.warnings),
      "normalized_policy": dict(plan.normalized_policy or {}),
      "changed_fields": list(plan.changed_fields),
      "requires_rewarm": plan.requires_rewarm,
      "config_version": actual,
    }

  async def get_monitor(self, account_id: str) -> Dict[str, Any]:
    normalized = str(account_id or "").strip()
    if not normalized:
      raise ValueError("账户不能为空")
    config = await self._load_config(normalized)
    config_data = self._config_data(config, normalized)
    snapshot = await self.position_service.get_snapshot_status(normalized)
    config_data.update(self._snapshot_data(snapshot))
    try:
      positions = await self.position_service.get_positions(account_id=normalized)
    except Exception as exc:
      positions = []
      config_data["last_error"] = config_data.get("last_error") or str(exc)
      logger.warning("读取做 T 持仓快照失败: account=%s, error=%s", normalized, exc)
    sessions: List[Dict[str, Any]] = []
    if config and config.strategy_run_id:
      try:
        sessions = await self.session_service.get_run_sessions(config.strategy_run_id)
      except Exception as exc:
        config_data["last_error"] = config_data.get("last_error") or str(exc)

    position_by_code = {str(item.stock_code or "").upper(): item for item in positions}
    session_by_code = {item["stock_code"]: item for item in sessions}
    stock_codes = sorted(set(position_by_code) | set(session_by_code))
    codes_missing_names = [
      code
      for code in stock_codes
      if not self._has_instrument_name(
        code,
        getattr(position_by_code.get(code), "instrument_name", None),
        (session_by_code.get(code) or {}).get("instrument_name"),
      )
    ]
    instrument_names = await self._load_instrument_names(codes_missing_names)
    ignored = set(config_data["ignored_stock_codes"])
    holdings: List[Dict[str, Any]] = []
    eligible_count = 0
    ignored_count = 0
    draining_count = 0
    for code in stock_codes:
      position = position_by_code.get(code)
      session = session_by_code.get(code)
      volume = int(getattr(position, "volume", 0) or 0)
      available = int(getattr(position, "can_use_volume", 0) or 0)
      is_ignored = code in ignored
      is_eligible = (
        bool(config_data["enabled"])
        and not is_ignored
        and self._is_a_share_code(code)
        and volume > 0
        and available >= 100
      )
      if is_eligible:
        eligible_count += 1
      if is_ignored:
        ignored_count += 1
      status, reason = self._holding_status(
        config_data=config_data,
        stock_code=code,
        volume=volume,
        available=available,
        is_eligible=is_eligible,
        is_ignored=is_ignored,
        session=session,
      )
      if status == "DRAINING":
        draining_count += 1
      holdings.append(
        {
          "stock_code": code,
          "instrument_name": self._resolve_instrument_name(
            code,
            getattr(position, "instrument_name", None),
            instrument_names.get(code),
            (session or {}).get("instrument_name"),
          ),
          "volume": volume,
          "available_volume": available,
          "ignored": is_ignored,
          "eligible": is_eligible,
          "status": status,
          "reason": reason,
          "session": session,
        }
      )
    holdings.sort(
      key=lambda item: (
        0 if item["session"] and item["session"].get("pending_entry_intent_id") else 1,
        0
        if item["session"] and int(item["session"].get("active_volume", 0) or 0)
        else 1,
        item["stock_code"],
      )
    )
    monitor = {
      **config_data,
      "holdings": holdings,
      "sessions": sessions,
      "holding_count": len(position_by_code),
      "eligible_count": eligible_count,
      "ignored_count": ignored_count,
      "monitored_count": len(sessions),
      "pending_signal_count": sum(
        1 for item in sessions if item.get("pending_entry_intent_id")
      ),
      "active_batch_count": sum(
        1 for item in sessions if int(item.get("active_volume", 0) or 0) > 0
      ),
      "draining_count": draining_count,
    }
    try:
      readiness = await TTradeOperationsService().readiness(normalized)
      monitor.update(
        {
          "rollout_stage": readiness["stage"],
          "engine_status": readiness["engine_status"],
          "agent_status": readiness["agent_status"],
          "reconcile_status": readiness["reconcile_status"],
          "kill_switch": readiness["kill_switch"],
          "can_approve": readiness["can_approve"],
          "can_activate_live": readiness["can_activate_live"],
          "blocked_reasons": readiness["blocked_reasons"],
          "readiness": readiness,
        }
      )
    except Exception as exc:
      logger.warning("读取做 T 就绪投影失败: account=%s error=%s", normalized, exc)
      monitor.update(
        {
          "rollout_stage": "SHADOW",
          "engine_status": "OFFLINE",
          "agent_status": "OFFLINE",
          "reconcile_status": "UNKNOWN",
          "kill_switch": False,
          "can_approve": False,
          "can_activate_live": False,
          "blocked_reasons": [str(exc)],
        }
      )
    try:
      return await t_trade_monitor_projection_service.save(normalized, monitor)
    except Exception as exc:
      logger.warning(
        "保存做 T 监控投影失败，返回本次内存结果: account=%s error=%s",
        normalized,
        exc,
      )
      return monitor

  async def reconcile_account(self, account_id: str) -> Dict[str, Any]:
    normalized = str(account_id or "").strip()
    if not normalized:
      raise ValueError("账户不能为空")
    lock = t_trade_account_coordination_lock(normalized)
    async with lock:
      return await self._reconcile_account_locked(normalized)

  async def _reconcile_account_locked(self, account_id: str) -> Dict[str, Any]:
    config = await self._load_config(account_id)
    if config is None:
      return await self.get_monitor(account_id)
    errors: List[str] = []
    try:
      _, positions = await self.position_service.read_validated_snapshot_and_positions(
        account_id
      )
    except Exception as exc:
      error = f"持仓快照读取失败: {exc}"
      errors = [error]
      await self._block_new_entries_if_needed(config, errors)
      await self._record_reconcile_result(config.id, errors)
      return await self.get_monitor(account_id)

    sessions: List[Dict[str, Any]] = []
    coordination_blocked = False
    try:
      active_run_ids = await self.session_service.list_active_account_run_ids(
        account_id
      )
    except Exception as exc:
      active_run_ids = []
      coordination_blocked = True
      errors.append(f"做 T 活跃实例扫描失败: {exc}")

    sessions_by_run: Dict[str, List[Dict[str, Any]]] = {}
    session_read_unknown_run_ids = set()
    candidate_run_ids = list(active_run_ids)
    if config.strategy_run_id and config.strategy_run_id not in candidate_run_ids:
      candidate_run_ids.append(config.strategy_run_id)
    for run_id in candidate_run_ids:
      try:
        sessions_by_run[run_id] = await self.session_service.get_run_sessions(run_id)
      except Exception as exc:
        coordination_blocked = True
        session_read_unknown_run_ids.add(str(run_id))
        errors.append(f"做 T 实例 {run_id} 状态读取失败: {exc}")

    if config.strategy_run_id:
      configured_run_id = str(config.strategy_run_id)
      if configured_run_id in sessions_by_run:
        sessions = sessions_by_run[configured_run_id]
      else:
        # Keep the durable run pointer when its session read failed.  Clearing
        # it would allow adoption of another run and could leave the old
        # runtime emitting under an unapplied configuration.
        sessions = []
      if configured_run_id in sessions_by_run and not sessions:
        config.strategy_run_id = None

    if not config.strategy_run_id:
      adoptable = [run_id for run_id in active_run_ids if sessions_by_run.get(run_id)]
      if adoptable:
        config.strategy_run_id = adoptable[0]
        sessions = sessions_by_run[config.strategy_run_id]

    duplicate_run_ids = [
      run_id for run_id in active_run_ids if run_id != config.strategy_run_id
    ]
    for duplicate_run_id in duplicate_run_ids:
      if str(duplicate_run_id) in session_read_unknown_run_ids:
        # A duplicate whose sessions could not be read is not known to be
        # idle.  Stopping or adopting it would risk orphaning active exits or
        # orders, so keep coordination blocked and only attempt the
        # entry-authority invalidation, which preserves known exit state.
        coordination_blocked = True
        errors.append(
          f"重复做 T 实例 {duplicate_run_id} 状态未知，拒绝停止或接管；"
          "已停止其新入场协调"
        )
        try:
          await self.session_service.block_account_strategy_entries(
            duplicate_run_id,
            reason=CONFIG_APPLY_PENDING_MARKER,
          )
        except Exception as exc:
          errors.append(f"关闭未知重复实例新入场失败: {exc}")
        continue
      duplicate_sessions = sessions_by_run.get(duplicate_run_id, [])
      if self._sessions_have_open_work(duplicate_sessions):
        coordination_blocked = True
        errors.append(
          "检测到多个做 T 活跃实例，重复实例仍有持仓批次或待处理意图："
          f"{duplicate_run_id}；已停止新建和标的协调"
        )
        # An open-work duplicate must remain alive long enough to settle its
        # exits, pending intents, and reservations, but it must not retain the
        # authority to emit a new entry from a stale runtime snapshot.  Use the
        # public invalidation boundary on that exact run; never stop it here.
        try:
          await self.session_service.block_account_strategy_entries(
            duplicate_run_id,
            reason=CONFIG_APPLY_PENDING_MARKER,
          )
        except Exception as exc:
          errors.append(f"关闭重复实例 {duplicate_run_id} 新入场失败: {exc}")
        continue
      try:
        result = await self.session_service.stop_account_strategy(duplicate_run_id)
        if not bool(result.get("success")):
          raise RuntimeError(str(result.get("message") or "停止重复实例失败"))
      except Exception as exc:
        coordination_blocked = True
        errors.append(f"停止重复做 T 实例 {duplicate_run_id} 失败: {exc}")

    run_mode = str(sessions[0].get("mode", "") or "").lower() if sessions else ""
    mode_mismatch = bool(run_mode and run_mode != str(config.mode or "paper").lower())
    metadata, desired = self._build_universe(
      config,
      positions,
      sessions,
      force_draining=mode_mismatch,
    )
    if config.strategy_run_id:
      try:
        await self._reject_stale_pending(
          config.strategy_run_id,
          sessions,
          metadata,
          int(config.config_version or 0),
        )
      except Exception as exc:
        coordination_blocked = True
        errors.append(f"旧做 T 待确认意图失效失败: {exc}")

    active_count = sum(
      1 for item in sessions if int(item.get("active_volume", 0) or 0) > 0
    )
    run_status = (
      str(sessions[0].get("run_status", "") or "").lower() if sessions else ""
    )
    run_status_unavailable = bool(
      run_status and run_status not in ACTIVE_T_STRATEGY_RUN_STATUSES
    )
    unsafe_run_status = run_status_unavailable and active_count > 0
    coordination_blocked = coordination_blocked or unsafe_run_status
    if unsafe_run_status:
      errors.append(
        f"策略运行状态异常（{run_status}），仍有 {active_count} 个活动批次，"
        "已停止标的协调以保护交易状态"
      )

    should_restore_run = bool(
      config.strategy_run_id
      and run_status in {"paused", "pending"}
      and (
        (bool(config.enabled) and bool(desired) and not mode_mismatch)
        or active_count > 0
      )
    )
    if should_restore_run and not coordination_blocked:
      try:
        await self.session_service.ensure_account_strategy_running(
          config.strategy_run_id
        )
        run_status = "running"
      except Exception as exc:
        coordination_blocked = True
        errors.append(f"做 T 策略运行恢复失败: {exc}")

    should_stop = bool(
      config.strategy_run_id
      and active_count == 0
      and (
        not config.enabled
        or mode_mismatch
        or (config.enabled and not desired)
        or run_status_unavailable
      )
    )
    if should_stop:
      stopped_for_rebuild = mode_mismatch or run_status_unavailable
      try:
        result = await self.session_service.stop_account_strategy(
          config.strategy_run_id
        )
        if not bool(result.get("success")):
          raise RuntimeError(str(result.get("message") or "停止策略运行失败"))
        config.strategy_run_id = None
        sessions = []
        coordination_blocked = False
        if stopped_for_rebuild and config.enabled:
          mode_mismatch = False
          metadata, desired = self._build_universe(config, positions, [])
      except Exception as exc:
        coordination_blocked = True
        errors.append(f"旧策略停止失败: {exc}")

    if config.enabled and desired and not mode_mismatch and not coordination_blocked:
      payload = self._strategy_payload(config, account_id)
      configuration_changed = self._configuration_changed(config, sessions)
      try:
        if config.strategy_run_id:
          result = await self.session_service.update_account_strategy(
            config.strategy_run_id,
            payload,
            desired,
            metadata,
            configuration_changed=configuration_changed,
          )
          config.universe_revision = int(config.universe_revision or 0) + (
            1 if result.get("added") or result.get("removed") else 0
          )
        else:
          config.strategy_run_id = await self.session_service.start_account_strategy(
            payload,
            desired,
            metadata,
          )
          config.universe_revision = int(config.universe_revision or 0) + 1
      except Exception as exc:
        coordination_blocked = True
        errors.append(f"动态持仓策略协调失败: {exc}")
    elif config.strategy_run_id and active_count > 0 and not coordination_blocked:
      payload = self._strategy_payload(config, account_id)
      configuration_changed = self._configuration_changed(config, sessions)
      try:
        await self.session_service.update_account_strategy(
          config.strategy_run_id,
          payload,
          desired,
          metadata,
          configuration_changed=configuration_changed,
        )
      except Exception as exc:
        coordination_blocked = True
        errors.append(f"退出中标的协调失败: {exc}")

    await self._block_new_entries_if_needed(config, errors)
    try:
      await self._save_reconcile_config(config, errors)
    except Exception as exc:
      # Keep the pre-commit CONFIG_APPLY_PENDING marker durable when the
      # reconcile projection itself cannot be persisted.  The next periodic or
      # manual reconcile can retry and clear it after a successful apply.
      errors.append(f"保存做 T 对账状态失败: {exc}")
      await self._block_new_entries_if_needed(config, errors)
    return await self.get_monitor(account_id)

  @staticmethod
  def _configuration_changed(
    config: TTradeGlobalConfig,
    sessions: List[Dict[str, Any]],
  ) -> bool:
    """Return whether the persisted config is newer than the live run.

    A periodic universe reconciliation is also used to refresh position
    metadata.  It must not be treated as a policy/configuration change merely
    because the call repeats.  The run session projection carries the config
    version that was applied to that runtime.  Missing or malformed versions
    are deliberately fail-closed: the next reconciliation must re-apply the
    configuration and expire stale pending candidates.
    """

    raw_expected_version = getattr(config, "config_version", None)
    if (
      raw_expected_version is None
      or raw_expected_version == ""
      or isinstance(raw_expected_version, bool)
    ):
      return True
    try:
      if (
        isinstance(raw_expected_version, float)
        and not raw_expected_version.is_integer()
      ):
        return True
      expected_version = int(raw_expected_version)
    except (TypeError, ValueError, OverflowError):
      return True
    if not sessions:
      return True
    for session in sessions:
      session_version = TTradeGlobalMonitorService._session_config_version(session)
      if session_version is None:
        return True
      if session_version != expected_version:
        return True
    return False

  @staticmethod
  def _session_config_version(session: Dict[str, Any]) -> Optional[int]:
    raw_version = session.get("global_config_version")
    if raw_version is None or raw_version == "" or isinstance(raw_version, bool):
      return None
    try:
      if isinstance(raw_version, float) and not raw_version.is_integer():
        return None
      return int(raw_version)
    except (TypeError, ValueError, OverflowError):
      return None

  @staticmethod
  def _sessions_have_open_work(sessions: List[Dict[str, Any]]) -> bool:
    return any(
      int(item.get("active_volume", 0) or 0) > 0
      or bool(item.get("pending_entry_intent_id"))
      or bool(item.get("pending_exit_intent_id"))
      for item in sessions
    )

  def _build_universe(
    self,
    config: TTradeGlobalConfig,
    positions: List[Any],
    sessions: List[Dict[str, Any]],
    *,
    force_draining: bool = False,
  ) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    ignored = set(self._normalize_ignored_codes(config.ignored_stock_codes or []))
    position_by_code = {
      str(item.stock_code or "").upper(): item
      for item in positions
      if self._is_a_share_code(str(item.stock_code or "").upper())
      and int(item.volume or 0) > 0
    }
    session_by_code = {item["stock_code"]: item for item in sessions}
    desired = {
      code
      for code in position_by_code
      if bool(config.enabled) and code not in ignored and not force_draining
    }
    desired.update(
      code
      for code, item in session_by_code.items()
      if int(item.get("active_volume", 0) or 0) > 0
      or bool(item.get("pending_entry_intent_id"))
      or bool(item.get("pending_exit_intent_id"))
    )
    metadata: Dict[str, Dict[str, Any]] = {}
    for code in sorted(desired):
      position = position_by_code.get(code)
      volume = int(getattr(position, "volume", 0) or 0)
      available = int(getattr(position, "can_use_volume", 0) or 0)
      draining = (
        force_draining
        or not bool(config.enabled)
        or code in ignored
        or position is None
      )
      eligible = not draining and available >= 100
      reason = "ELIGIBLE"
      if draining:
        reason = "DRAINING_EXISTING_T_BATCH"
      elif available < 100:
        reason = "AVAILABLE_VOLUME_BELOW_100"
      metadata[code] = {
        "eligible": eligible,
        "reason": reason,
        "draining": draining,
        "instrument_name": str(getattr(position, "instrument_name", code) or code),
        "position_shares": volume,
        "position_available_shares": available,
        "position_frozen_shares": max(
          0, int(getattr(position, "frozen_volume", 0) or 0)
        ),
        "position_avg_price": float(
          getattr(position, "avg_price", 0.0)
          or getattr(position, "open_price", 0.0)
          or 0.0
        ),
        "position_market_value": float(getattr(position, "market_value", 0.0) or 0.0),
      }
    return metadata, sorted(desired)

  async def _reject_stale_pending(
    self,
    run_id: str,
    sessions: List[Dict[str, Any]],
    metadata: Dict[str, Dict[str, Any]],
    config_version: int,
  ) -> None:
    for session in sessions:
      intent_id = session.get("pending_entry_intent_id")
      if not intent_id:
        continue
      code = session["stock_code"]
      session_version = self._session_config_version(session)
      stale = session_version is None or session_version != config_version
      if stale or not bool(metadata.get(code, {}).get("eligible", False)):
        await self.session_service.reject_entry(
          run_id,
          str(intent_id),
          reason="GLOBAL_CONFIG_CHANGED" if stale else "HOLDING_NOT_ELIGIBLE",
        )

  async def _run(self) -> None:
    while not self._stopping.is_set():
      try:
        for config in await self._load_all_configs():
          try:
            await self.reconcile_account(config.account_id)
          except asyncio.CancelledError:
            raise
          except Exception as exc:
            logger.warning(
              "动态持仓做 T 协调失败: account=%s, error=%s",
              config.account_id,
              exc,
            )
            await self._record_reconcile_result(config.id, [str(exc)])
      except asyncio.CancelledError:
        raise
      except Exception as exc:
        logger.warning("动态持仓做 T 扫描失败: %s", exc)
      try:
        await asyncio.wait_for(self._stopping.wait(), timeout=self.interval_seconds)
      except asyncio.TimeoutError:
        continue

  async def _load_config(self, account_id: str) -> Optional[TTradeGlobalConfig]:
    async for db in get_async_db():
      return await TTradeGlobalConfigRepository(db).find_by_account(account_id)
    return None

  async def _load_instrument_names(self, stock_codes: List[str]) -> Dict[str, str]:
    """Load missing display names from the local instrument master in one query."""
    if not stock_codes:
      return {}
    try:
      async for db in get_async_db():
        instruments = await InstrumentRepository(db).find_by_ids(stock_codes)
        return {
          str(item.id or "").upper(): str(item.name or "").strip()
          for item in instruments
          if item.id and item.name
        }
    except Exception as exc:
      logger.warning("读取证券名称失败: codes=%s, error=%s", stock_codes, exc)
    return {}

  @staticmethod
  def _has_instrument_name(stock_code: str, *candidates: Any) -> bool:
    normalized_code = str(stock_code or "").strip().upper()
    code_without_exchange = normalized_code.split(".", 1)[0]
    for candidate in candidates:
      value = str(candidate or "").strip()
      if value and value.upper() not in {normalized_code, code_without_exchange}:
        return True
    return False

  @classmethod
  def _resolve_instrument_name(cls, stock_code: str, *candidates: Any) -> str:
    for candidate in candidates:
      if cls._has_instrument_name(stock_code, candidate):
        return str(candidate).strip()
    return stock_code

  async def _load_all_configs(self) -> List[TTradeGlobalConfig]:
    async for db in get_async_db():
      return await TTradeGlobalConfigRepository(db).find_all_configs()
    return []

  async def _block_new_entries_if_needed(
    self,
    config: TTradeGlobalConfig,
    errors: List[str],
  ) -> None:
    """Fail closed when runtime reconciliation is incomplete."""

    if not errors or not config.strategy_run_id:
      return
    try:
      await self.session_service.block_account_strategy_entries(
        config.strategy_run_id,
        reason=CONFIG_APPLY_PENDING_MARKER,
      )
    except Exception as exc:
      errors.append(f"关闭做 T 新入场失败: {exc}")

  async def _save_reconcile_config(
    self, config: TTradeGlobalConfig, errors: List[str]
  ) -> None:
    async for db in get_async_db():
      repo = TTradeGlobalConfigRepository(db)
      current = await repo.find_by_id(config.id)
      if current:
        current.strategy_run_id = config.strategy_run_id
        current.universe_revision = int(config.universe_revision or 0)
        current.last_reconciled_at = time_utils.now()
        current.last_error = "; ".join(errors[:20]) or None
        await repo.save(current)
      break

  @staticmethod
  def _with_apply_outcome(monitor: Dict[str, Any]) -> Dict[str, Any]:
    """Attach an explicit save/apply outcome without changing GraphQL shape."""

    result = dict(monitor or {})
    has_error = bool(str(result.get("last_error") or "").strip())
    result["apply_status"] = "PENDING" if has_error else "APPLIED"
    result["apply_code"] = (
      CONFIG_APPLY_PENDING_CODE if has_error else CONFIG_APPLIED_CODE
    )
    return result

  async def _record_reconcile_result(self, config_id: str, errors: List[str]) -> None:
    async for db in get_async_db():
      repo = TTradeGlobalConfigRepository(db)
      config = await repo.find_by_id(config_id)
      if config:
        config.last_reconciled_at = time_utils.now()
        config.last_error = "; ".join(errors[:20]) or None
        await repo.save(config)
      break

  def _strategy_payload(
    self, config: TTradeGlobalConfig, account_id: str
  ) -> Dict[str, Any]:
    return {
      **self._normalized_settings(config.settings or {}),
      "account_id": account_id,
      "mode": config.mode,
      "auto_exit_acknowledged": bool(config.auto_exit_acknowledged),
      "global_monitor_id": config.id,
      "global_config_version": int(config.config_version or 1),
    }

  def _config_data(
    self, config: Optional[TTradeGlobalConfig], account_id: str
  ) -> Dict[str, Any]:
    if config is None:
      return {
        "config_id": None,
        "strategy_run_id": None,
        "universe_revision": 0,
        "account_id": account_id,
        "enabled": False,
        "mode": "paper",
        "auto_exit_acknowledged": False,
        "ignored_stock_codes": [],
        "config_version": 0,
        **GLOBAL_SETTING_DEFAULTS,
        "last_reconciled_at": None,
        "last_error": None,
        "created_at": None,
        "updated_at": None,
      }
    return {
      "config_id": config.id,
      "strategy_run_id": config.strategy_run_id,
      "universe_revision": int(config.universe_revision or 0),
      "account_id": config.account_id,
      "enabled": bool(config.enabled),
      "mode": str(config.mode or "paper"),
      "auto_exit_acknowledged": bool(config.auto_exit_acknowledged),
      "ignored_stock_codes": self._normalize_ignored_codes(
        config.ignored_stock_codes or []
      ),
      "config_version": int(config.config_version or 1),
      **self._normalized_settings(config.settings or {}),
      "last_reconciled_at": config.last_reconciled_at,
      "last_error": config.last_error,
      "created_at": config.created_at,
      "updated_at": config.updated_at,
    }

  @staticmethod
  def _snapshot_data(snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not snapshot:
      return {
        "position_snapshot_source": None,
        "position_snapshot_sequence": "0",
        "position_snapshot_reported_at": None,
        "position_snapshot_received_at": None,
        "position_snapshot_complete": False,
        "position_snapshot_error": None,
      }
    return {
      "position_snapshot_source": snapshot.get("source"),
      "position_snapshot_sequence": str(snapshot.get("sequence", 0) or 0),
      "position_snapshot_reported_at": snapshot.get("reported_at"),
      "position_snapshot_received_at": snapshot.get("received_at"),
      "position_snapshot_complete": bool(snapshot.get("is_complete", False)),
      "position_snapshot_error": snapshot.get("last_error"),
    }

  def _settings_from_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = self._normalized_settings(payload)
    return {
      key: (
        dict(normalized[key])
        if key == "signal_policy"
        else normalized[key]
      )
      for key in GLOBAL_SETTING_DEFAULTS
    }

  def _normalized_settings(self, settings: Any) -> Dict[str, Any]:
    normalized = self.session_service._normalize_exit_settings(dict(settings or {}))
    return {
      **GLOBAL_SETTING_DEFAULTS,
      **{
        key: (
          self.session_service._normalize_signal_policy(
            normalized.get("signal_policy")
          )
          if key == "signal_policy"
          else normalized.get(key, default)
        )
        for key, default in GLOBAL_SETTING_DEFAULTS.items()
      },
    }

  def _holding_status(
    self,
    *,
    config_data: Dict[str, Any],
    stock_code: str,
    volume: int,
    available: int,
    is_eligible: bool,
    is_ignored: bool,
    session: Optional[Dict[str, Any]],
  ) -> Tuple[str, str]:
    active = int((session or {}).get("active_volume", 0) or 0)
    if (
      session
      and active > 0
      and (not config_data["enabled"] or is_ignored or not is_eligible)
    ):
      return "DRAINING", "已有 T 批次，仅保留自动退出监控"
    if is_ignored:
      return "IGNORED", "已加入忽略名单"
    if not self._is_a_share_code(stock_code):
      return "INELIGIBLE", "不是支持的 A 股代码"
    if volume <= 0:
      return "INELIGIBLE", "持仓已清空"
    if not config_data["enabled"]:
      return "STOPPED", "全局监控未启动"
    if not is_eligible:
      return (
        "INELIGIBLE",
        f"昨日可用库存 {max(0, available)} 股，不足一手（100 股）",
      )
    if session:
      return "MONITORED", "由账户级策略统一监听 Tick"
    return "PENDING_START", "等待动态标的池协调"

  def _normalize_ignored_codes(self, values: Any) -> List[str]:
    raw_values = (
      re.split(r"[\s,，;；]+", values)
      if isinstance(values, str)
      else list(values or [])
    )
    normalized: List[str] = []
    for raw in raw_values:
      code = self._normalize_stock_code(raw)
      if code and code not in normalized:
        normalized.append(code)
    return sorted(normalized)

  def _normalize_stock_code(self, value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
      return ""
    if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", text):
      return text
    if not re.fullmatch(r"\d{6}", text):
      raise ValueError(f"无效股票代码: {text}")
    if text.startswith(("6", "9")):
      return f"{text}.SH"
    if text.startswith(("0", "2", "3")):
      return f"{text}.SZ"
    if text.startswith(("4", "8")):
      return f"{text}.BJ"
    raise ValueError(f"无法识别股票市场: {text}")

  @staticmethod
  def _is_a_share_code(stock_code: str) -> bool:
    return bool(re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", stock_code or ""))
