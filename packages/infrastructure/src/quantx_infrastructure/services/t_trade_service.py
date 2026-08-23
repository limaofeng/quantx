"""Application service for the account-level dynamic-holdings T strategy."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Dict, List, Mapping, Optional, Tuple

from quantx_application.t_trade_v3 import normalize_signal_policy
from quantx_domain.strategies.ashare_intraday_t_assistant import (
  AshareIntradayTAssistantStrategy,
)

from quantx_infrastructure.core.assistant_strategy_policy import (
  T_TRADE_STRATEGY_CLASS_NAME,
)
from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.enums import OrderStatus, OrderType, StrategyRunMode
from quantx_infrastructure.models.order import Order
from quantx_infrastructure.models.t_trade_imported_entry import TTradeImportedEntry
from quantx_infrastructure.repositories.strategy_repository import StrategyRepository
from quantx_infrastructure.repositories.strategy_run_repository import (
  StrategyRunRepository,
)
from quantx_infrastructure.repositories.strategy_run_state_repository import (
  StrategyRunStateRepository,
)
from quantx_infrastructure.repositories.t_trade_imported_entry_repository import (
  TTradeImportedEntryRepository,
)
from quantx_infrastructure.services.order_service import OrderService
from quantx_infrastructure.services.t_trade_signal_diagnostics_service import (
  TTradeSignalDiagnosticsService,
)

T_TRADE_CLASS_NAME = T_TRADE_STRATEGY_CLASS_NAME
ACTIVE_RUN_STATUSES = {"pending", "running", "paused"}


@dataclass(frozen=True)
class TTradeApprovalExpectation:
  """Client-observed V3 candidate identity used as an approval CAS token."""

  signal_version: int = 0
  candidate_id: str = ""
  candidate_fingerprint: str = ""
  candidate_state_version: int = 0
  config_version: Optional[int] = None
  policy_version: str = ""

  @classmethod
  def from_payload(cls, payload: Mapping[str, Any]) -> "TTradeApprovalExpectation":
    return cls(
      signal_version=cls._integer(payload.get("expected_signal_version")),
      candidate_id=str(payload.get("expected_candidate_id") or "").strip(),
      candidate_fingerprint=str(
        payload.get("expected_candidate_fingerprint") or ""
      ).strip(),
      candidate_state_version=cls._integer(
        payload.get("expected_candidate_state_version")
      ),
      config_version=(
        cls._integer(payload.get("expected_config_version"))
        if "expected_config_version" in payload
        and payload.get("expected_config_version") is not None
        else None
      ),
      policy_version=str(payload.get("expected_policy_version") or "").strip(),
    )

  @staticmethod
  def _integer(value: Any) -> int:
    try:
      return int(value or 0)
    except (TypeError, ValueError, OverflowError):
      return 0

  def to_dict(self) -> Dict[str, Any]:
    return {
      "signal_version": self.signal_version,
      "candidate_id": self.candidate_id,
      "candidate_fingerprint": self.candidate_fingerprint,
      "candidate_state_version": self.candidate_state_version,
      "config_version": self.config_version,
      "policy_version": self.policy_version,
    }


class TTradeService:
  """Create one T strategy run per account and project its child states."""

  def __init__(self, runtime_manager: Any = None) -> None:
    self._runtime_manager = runtime_manager

  def _require_runtime_manager(self) -> Any:
    if self._runtime_manager is None:
      raise RuntimeError("该操作只能由 QuantX Engine 执行")
    return self._runtime_manager

  async def signal_diagnostics(
    self,
    account_id: str,
    *,
    stock_code: Optional[str],
    start_time: datetime,
    end_time: datetime,
    merge_versions: bool = False,
  ) -> Dict[str, Any]:
    """Aggregate V3 evidence with READY instrument-time as its denominator."""

    async for db in get_async_db():
      return await TTradeSignalDiagnosticsService().signal_diagnostics(
        account_id,
        stock_code=stock_code,
        start_time=start_time,
        end_time=end_time,
        merge_versions=merge_versions,
        db=db,
      )
    raise RuntimeError("做 T 信号诊断数据库会话不可用")

  async def start_account_strategy(
    self,
    payload: Dict[str, Any],
    instruments: List[str],
    instrument_metadata: Dict[str, Dict[str, Any]],
  ) -> str:
    account_id = str(payload.get("account_id", "") or "").strip()
    if not account_id:
      raise ValueError("账户不能为空")
    mode = self._parse_mode(payload.get("mode", "paper"))
    self._validate_parameters(payload, mode)
    if mode == StrategyRunMode.LIVE and not bool(
      payload.get("auto_exit_acknowledged", False)
    ):
      raise ValueError("启动实盘做 T 前必须确认自动卖出授权")

    normalized = sorted({str(code or "").upper() for code in instruments if code})
    if not normalized:
      raise ValueError("当前没有需要监控或退出的持仓标的")
    strategy_id = await self._get_strategy_template_id()
    parameters = self.build_parameters(payload)
    strategy_manager = self._require_runtime_manager()
    run_id = await strategy_manager.run_strategy(
      strategy_id=strategy_id,
      strategy_class=AshareIntradayTAssistantStrategy,
      mode=mode,
      instruments=normalized,
      parameters=parameters,
      name=f"动态持仓做T-{account_id}",
      auto_start=False,
    )
    if not await strategy_manager.start_strategy(run_id):
      raise ValueError("全局做 T 策略启动失败")
    await strategy_manager.reconcile_run_instruments(
      run_id,
      normalized,
      instrument_metadata=instrument_metadata,
    )
    return run_id

  async def update_account_strategy(
    self,
    run_id: str,
    payload: Dict[str, Any],
    instruments: List[str],
    instrument_metadata: Dict[str, Dict[str, Any]],
    *,
    configuration_changed: bool,
  ) -> Dict[str, List[str]]:
    mode = self._parse_mode(payload.get("mode", "paper"))
    self._validate_parameters(payload, mode)
    strategy_manager = self._require_runtime_manager()
    return await strategy_manager.reconcile_run_instruments(
      run_id,
      instruments,
      instrument_metadata=instrument_metadata,
      parameters=self.build_parameters(payload),
      configuration_changed=bool(configuration_changed),
    )

  async def get_session(
    self,
    run_id: str,
    stock_code: Optional[str] = None,
    *,
    intent_id: Optional[str] = None,
  ) -> Dict[str, Any]:
    sessions = await self.get_run_sessions(run_id)
    if not sessions:
      raise ValueError("做 T 策略运行不存在")
    normalized_code = str(stock_code or "").upper()
    for session in sessions:
      if normalized_code and session["stock_code"] == normalized_code:
        return session
      if intent_id and intent_id in {
        session.get("pending_entry_intent_id"),
        session.get("pending_exit_intent_id"),
      }:
        return session
    if len(sessions) == 1 and not normalized_code and not intent_id:
      return sessions[0]
    if intent_id:
      raise ValueError("做 T 信号不属于该策略运行")
    raise ValueError("多标的做 T 策略必须指定股票代码")

  async def get_run_sessions(self, run_id: str) -> List[Dict[str, Any]]:
    run, persisted_state = await self._load_persisted_run(run_id)
    if run is None or not run.strategy or run.strategy.class_name != T_TRADE_CLASS_NAME:
      return []

    state = dict(persisted_state or {})
    run_status = run.status.value if run.status else "unknown"
    error_message = run.error_message

    params = self._mapping(run.parameters)
    child_states = {
      str(code or "").upper(): dict(value or {})
      for code, value in dict(state.get("instrument_states") or {}).items()
    }
    codes = sorted(set(run.instruments or []) | set(child_states))
    sessions = [
      self._project_session(
        run=run,
        run_status=run_status,
        error_message=error_message,
        params=params,
        stock_code=code,
        state=child_states.get(code, {}),
      )
      for code in codes
    ]
    return sessions

  async def list_sessions(
    self,
    *,
    account_id: Optional[str] = None,
    stock_code: Optional[str] = None,
    active_only: bool = False,
  ) -> List[Dict[str, Any]]:
    async for db in get_async_db():
      runs = await StrategyRunRepository(db).find_all_strategy_runs()
      run_ids = []
      for run in runs:
        if not run.strategy or run.strategy.class_name != T_TRADE_CLASS_NAME:
          continue
        params = self._mapping(run.parameters)
        if account_id and str(params.get("account_id", "")) != account_id:
          continue
        status = run.status.value if run.status else ""
        if active_only and status not in ACTIVE_RUN_STATUSES:
          continue
        run_ids.append(run.id)
      break

    sessions: List[Dict[str, Any]] = []
    normalized_code = str(stock_code or "").upper()
    for run_id in run_ids:
      rows = await self.get_run_sessions(run_id)
      sessions.extend(
        row
        for row in rows
        if not normalized_code or row["stock_code"] == normalized_code
      )
    sessions.sort(key=lambda item: item["stock_code"])
    return sessions

  async def list_active_account_run_ids(self, account_id: str) -> List[str]:
    """Return all active account-level T runs, newest first."""
    normalized_account_id = str(account_id or "").strip()
    if not normalized_account_id:
      raise ValueError("账户不能为空")
    async for db in get_async_db():
      runs = await StrategyRunRepository(db).find_active_runs_by_strategy_class(
        T_TRADE_CLASS_NAME
      )
      return [
        str(run.id)
        for run in runs
        if str(self._mapping(run.parameters).get("account_id", ""))
        == normalized_account_id
      ]
    return []

  async def block_account_strategy_entries(
    self, run_id: str, *, reason: str = "GLOBAL_CONFIG_APPLY_PENDING"
  ) -> None:
    strategy_manager = self._require_runtime_manager()
    executor = getattr(strategy_manager, "executor", None)
    invalidate = getattr(executor, "invalidate_t_trade_entry_authority", None)
    if not callable(invalidate):
      raise RuntimeError("Engine 缺少做 T 新入场 authority 失效接口")
    account_id = ""
    runtime = strategy_manager.get_run(run_id)
    if runtime is not None:
      context = getattr(runtime, "context", None)
      account_id = str(
        dict(getattr(context, "parameters", {}) or {}).get("account_id")
        or ""
      ).strip()
    if not await invalidate(run_id, account_id=account_id, reason=reason):
      raise ValueError(f"策略运行不存在或账户作用域不匹配: {run_id}")

  async def approve_entry(
    self,
    run_id: str,
    intent_id: str,
    *,
    approval_expectation: Optional[TTradeApprovalExpectation] = None,
    approval_audit: Optional[Mapping[str, Any]] = None,
  ) -> Dict[str, Any]:
    strategy_manager = self._require_runtime_manager()
    session_before_approval = await self.get_session(run_id, intent_id=intent_id)
    result = await strategy_manager.executor.approve_trade_intent(
      run_id,
      intent_id,
      approval_expectation=(
        approval_expectation.to_dict() if approval_expectation is not None else None
      ),
      approval_audit=approval_audit,
    )
    stock_code = str(session_before_approval.get("stock_code") or "")
    return {
      **result,
      "session": await self.get_session(run_id, stock_code=stock_code),
    }

  async def reject_entry(
    self, run_id: str, intent_id: str, reason: str = "USER_REJECTED"
  ) -> Dict[str, Any]:
    strategy_manager = self._require_runtime_manager()
    session_before_rejection = await self.get_session(run_id, intent_id=intent_id)
    result = await strategy_manager.executor.reject_trade_intent(
      run_id, intent_id, reason=reason
    )
    stock_code = str(session_before_rejection.get("stock_code") or "")
    return {
      **result,
      "session": await self.get_session(run_id, stock_code=stock_code),
    }

  async def import_external_entry(
    self, run_id: str, account_id: str, order_id: str
  ) -> Dict[str, Any]:
    strategy_manager = self._require_runtime_manager()
    runtime = strategy_manager.get_run(run_id)
    if (
      runtime is None
      or runtime.strategy is None
      or not isinstance(runtime.strategy, AshareIntradayTAssistantStrategy)
    ):
      raise ValueError("做 T 策略运行不存在或尚未启动")
    bound_account_id = str(runtime.context.parameters.get("account_id", "") or "")
    if not account_id or account_id != bound_account_id:
      raise ValueError("成交账户与做 T 监控账户不一致")
    normalized_order_id = str(order_id or "").strip()
    if not normalized_order_id:
      raise ValueError("委托编号不能为空")
    try:
      numeric_order_id = int(normalized_order_id)
    except ValueError as exc:
      raise ValueError("委托编号格式不正确") from exc
    async for db in get_async_db():
      order = await db.get(Order, numeric_order_id)
      break
    if order is None:
      raise ValueError("未找到该笔委托，请先同步委托记录")
    if str(getattr(order, "account_id", "") or "") != account_id:
      raise ValueError("委托账户与做 T 监控账户不一致")
    if int(getattr(order, "type", 0) or 0) != int(OrderType.BUY):
      raise ValueError("只能将买入委托加入做 T 助手")
    if int(getattr(order, "status", 0) or 0) != int(OrderStatus.SUCCEEDED):
      raise ValueError("只能将已成交委托加入做 T 助手")
    if int(getattr(order, "traded_volume", 0) or 0) <= 0:
      raise ValueError("已成交委托的成交数量必须大于 0")
    source_id = f"order:{normalized_order_id}"
    patch = runtime.strategy.import_external_entry(
      str(getattr(order, "stock_code", "") or ""),
      int(getattr(order, "traded_volume", 0) or 0),
      float(getattr(order, "traded_price", 0.0) or 0.0),
      source_id,
    )
    batch_id = str(
      patch.set["instrument_states"][order.stock_code].get("batch_id", "") or ""
    )
    async for db in get_async_db():
      repo = TTradeImportedEntryRepository(db)
      if await repo.find_source(account_id, source_id):
        raise ValueError("该笔委托已经加入做 T 助手")
      await repo.save(
        TTradeImportedEntry(
          account_id=account_id,
          source_trade_id=source_id,
          source_order_id=normalized_order_id,
          source_trade_time=getattr(order, "time", None),
          stock_code=order.stock_code,
          volume=int(order.traded_volume or 0),
          price=float(order.traded_price or 0.0),
          strategy_run_id=run_id,
          batch_id=batch_id,
          status="IMPORTED",
        )
      )
      break
    strategy_manager.executor.apply_external_state_patch(run_id, patch)
    imported_state = dict(patch.set["instrument_states"][order.stock_code] or {})
    strategy_manager.executor.register_external_exit_plan(
      run_id,
      runtime.strategy.build_exit_plan_template(
        instrument_code=order.stock_code,
        batch_id=batch_id,
        plan_id=str(imported_state.get("exit_plan_id", "") or ""),
        policy=dict(imported_state.get("exit_policy_snapshot") or {}),
      ),
      volume=int(order.traded_volume or 0),
      price=float(order.traded_price or 0.0),
      trade_time=getattr(order, "time", None),
    )
    return {
      "success": True,
      "code": "EXTERNAL_ENTRY_IMPORTED",
      "message": "已成交买入委托已加入做 T 自动退出监控",
      "session": await self.get_session(run_id, order.stock_code),
    }

  async def sync_source_orders(self, account_id: str) -> Dict[str, Any]:
    """Read today's Agent-reconciled orders from the durable projection."""
    normalized_account_id = str(account_id or "").strip()
    if not normalized_account_id:
      raise ValueError("账户不能为空")
    try:
      result = await OrderService(normalized_account_id).sync_today_orders(
        normalized_account_id
      )
    except ValueError:
      raise
    except Exception as exc:
      raise ValueError(f"同步当日委托失败：{exc}") from exc
    return {
      "success": True,
      "code": "SOURCE_ORDERS_SYNCED",
      "message": f"已同步 {result.saved_count} 笔当日委托",
    }

  async def list_imported_entries(self, account_id: str) -> List[Dict[str, Any]]:
    async for db in get_async_db():
      rows = await TTradeImportedEntryRepository(db).find_by_account(account_id)
      return [
        {
          "source_trade_id": row.source_trade_id,
          "source_order_id": row.source_order_id,
          "stock_code": row.stock_code,
          "volume": row.volume,
          "price": row.price,
          "status": row.status,
          "source_trade_time": row.source_trade_time,
          "strategy_run_id": row.strategy_run_id,
          "batch_id": row.batch_id,
        }
        for row in rows
      ]
    return []

  async def ensure_account_strategy_running(self, run_id: str) -> bool:
    """Start a restored idle runtime or resume an in-process paused runtime."""

    strategy_manager = self._require_runtime_manager()
    runtime = strategy_manager.get_run(run_id)
    if runtime is None:
      raise RuntimeError(f"做 T 策略运行未恢复到 Engine: {run_id}")

    runtime_status = str(
      getattr(getattr(runtime, "status", None), "value", getattr(runtime, "status", ""))
      or ""
    ).lower()
    task = getattr(runtime, "task", None)
    task_is_active = task is not None and not task.done()

    if task_is_active and runtime_status in {"running", "starting"}:
      return False
    if task_is_active and runtime_status == "paused":
      success = await strategy_manager.resume_strategy(run_id)
    else:
      # Restored PAUSED/PENDING runs are recreated with no task and a PENDING
      # in-memory status. They need the full start path to restore state,
      # connect the data adapter, and subscribe to realtime Tick data.
      success = await strategy_manager.start_strategy(run_id)

    if not success:
      raise RuntimeError(f"做 T 策略运行恢复失败: {run_id}")
    return True

  async def stop_account_strategy(self, run_id: str) -> Dict[str, Any]:
    strategy_manager = self._require_runtime_manager()
    sessions = await self.get_run_sessions(run_id)
    active = [item for item in sessions if int(item.get("active_volume", 0) or 0) > 0]
    if active:
      raise ValueError("仍有未完成的 T 批次，策略将保持退出监控")
    for session in sessions:
      pending = session.get("pending_entry_intent_id")
      if pending:
        await strategy_manager.executor.reject_trade_intent(
          run_id, str(pending), reason="GLOBAL_MONITOR_STOPPED"
        )
    success = await strategy_manager.stop_strategy(run_id)
    return {
      "success": success,
      "code": "STOPPED" if success else "STOP_FAILED",
      "message": "账户级做 T 策略已停止" if success else "停止做 T 策略失败",
    }

  async def stop_session(self, run_id: str) -> Dict[str, Any]:
    result = await self.stop_account_strategy(run_id)
    sessions = await self.get_run_sessions(run_id)
    return {**result, "session": sessions[0] if sessions else None}

  def build_parameters(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = self._normalize_exit_settings(payload)
    keys = {
      "account_id",
      "target_trade_amount",
      "max_trade_amount",
      "max_concurrent_batches",
      "max_total_t_exposure_pct",
      "signal_policy",
      "max_price_deviation_pct",
      "target_profit_pct",
      "base_floor_pct",
      "initial_gap_pct",
      "trailing_gap_slope",
      "max_gap_pct",
      "high_profit_lock_enabled",
      "high_profit_arm_pct",
      "high_profit_max_drawdown_pct",
      "rapid_reversal_enabled",
      "rapid_reversal_window_seconds",
      "rapid_reversal_drawdown_pct",
      "rapid_reversal_confirm_ticks",
      "limit_up_touch_exit_enabled",
      "limit_up_touch_tolerance_ticks",
      "hard_stop_enabled",
      "hard_stop_pct",
      "time_exit_mode",
      "time_exit_time",
      "max_holding_trading_days",
      "cooldown_seconds",
      "max_exit_slippage_bps",
      "commission_rate",
      "minimum_commission",
      "stamp_tax_rate",
      "transfer_fee_rate",
      "auto_exit_acknowledged",
      "global_monitor_id",
      "global_config_version",
    }
    parameters = {
      key: normalized[key] for key in keys if normalized.get(key) is not None
    }
    parameters.update({"enforce_trading_hours": True, "enable_reserve": True})
    return parameters

  async def _get_strategy_template_id(self) -> int:
    async for db in get_async_db():
      strategy = await StrategyRepository(db).find_by_class_name(T_TRADE_CLASS_NAME)
      if strategy is None:
        raise ValueError("做 T 策略模板尚未注册，请稍后重试")
      return int(strategy.id)
    raise ValueError("无法读取做 T 策略模板")

  async def _load_persisted_run(self, run_id: str) -> Tuple[Any, Dict[str, Any]]:
    async for db in get_async_db():
      run = await StrategyRunRepository(db).find_run_by_id(run_id)
      state_record = await StrategyRunStateRepository(db).get_state(run_id)
      return run, dict(state_record.custom_state or {}) if state_record else {}
    return None, {}

  def _project_session(
    self,
    *,
    run,
    run_status: str,
    error_message: Optional[str],
    params: Dict[str, Any],
    stock_code: str,
    state: Dict[str, Any],
  ) -> Dict[str, Any]:
    entry_volume = int(state.get("entry_filled_volume", 0) or 0)
    exit_volume = int(state.get("exit_filled_volume", 0) or 0)
    active_volume = max(0, entry_volume - exit_volume)
    pending_entry = str(state.get("pending_entry_intent_id", "") or "")
    pending_exit = str(state.get("pending_exit_intent_id", "") or "")
    opportunity = dict(state.get("opportunity") or {})
    raw_signal_snapshot = opportunity.get("latest_evaluation")
    signal_snapshot = (
      dict(raw_signal_snapshot) if isinstance(raw_signal_snapshot, dict) else None
    )
    return {
      "run_id": run.id,
      "account_id": str(params.get("account_id", "") or ""),
      "stock_code": stock_code,
      "mode": run.mode.value if run.mode else "",
      "run_status": run_status,
      "status": str(state.get("status", "STARTING") or "STARTING"),
      "target_trade_amount": float(
        params.get("target_trade_amount", 10_000.0) or 10_000.0
      ),
      "max_trade_amount": float(params.get("max_trade_amount", 12_000.0) or 12_000.0),
      "planned_entry_amount": float(state.get("requested_entry_amount", 0.0) or 0.0),
      "target_profit_pct": float(params.get("target_profit_pct", 2.0) or 2.0),
      "base_floor_pct": float(params.get("base_floor_pct", 0.5) or 0.5),
      "hard_stop_enabled": bool(
        params.get(
          "hard_stop_enabled",
          "hard_stop_pct" in params and "time_exit_mode" not in params,
        )
      ),
      "hard_stop_pct": float(params.get("hard_stop_pct", -0.8) or -0.8),
      "time_exit_mode": self._normalize_exit_settings(params)["time_exit_mode"],
      "time_exit_time": self._normalize_exit_settings(params)["time_exit_time"],
      "max_holding_trading_days": int(
        self._normalize_exit_settings(params)["max_holding_trading_days"]
      ),
      "pending_entry_intent_id": pending_entry or None,
      "pending_exit_intent_id": pending_exit or None,
      "entry_order_status": str(state.get("entry_order_status", "") or ""),
      "exit_order_status": str(state.get("exit_order_status", "") or ""),
      "entry_filled_volume": entry_volume,
      "entry_avg_price": float(state.get("entry_avg_price", 0.0) or 0.0),
      "exit_filled_volume": exit_volume,
      "exit_avg_price": float(state.get("exit_avg_price", 0.0) or 0.0),
      "active_volume": active_volume,
      "last_price": float(state.get("last_price", 0.0) or 0.0),
      "last_net_profit_pct": float(state.get("last_net_profit_pct", 0.0) or 0.0),
      "peak_net_profit_pct": float(state.get("peak_net_profit_pct", 0.0) or 0.0),
      "trailing_floor_pct": self._optional_floor(state.get("trailing_floor_pct")),
      "profit_armed": bool(state.get("profit_armed", False)),
      "last_exit_reason": str(state.get("last_exit_reason", "") or ""),
      "completed_cycles": int(state.get("completed_cycles", 0) or 0),
      "latest_intent": None,
      "can_cancel": active_volume == 0 and not pending_exit,
      "error_message": error_message,
      "created_at": run.created_at,
      "updated_at": run.updated_at,
      "global_monitor_id": str(params.get("global_monitor_id", "") or "") or None,
      "global_config_version": int(params.get("global_config_version", 0) or 0),
      "signal_snapshot": signal_snapshot,
    }

  @staticmethod
  def _parse_mode(value: Any) -> StrategyRunMode:
    try:
      mode = StrategyRunMode(str(value or "live").lower())
    except ValueError as exc:
      raise ValueError("做 T 策略仅支持 live 或 paper 模式") from exc
    if mode not in {StrategyRunMode.LIVE, StrategyRunMode.PAPER}:
      raise ValueError("做 T 策略仅支持 live 或 paper 模式")
    return mode

  @staticmethod
  def _mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
      return dict(value)
    if isinstance(value, str):
      try:
        decoded = json.loads(value)
      except json.JSONDecodeError:
        return {}
      return dict(decoded) if isinstance(decoded, dict) else {}
    return {}

  @staticmethod
  def _optional_floor(value: Any) -> Optional[float]:
    floor = float(value or -999.0)
    return None if floor <= -900 else floor

  @staticmethod
  def _optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
      return None
    return float(value)

  @classmethod
  def _validate_parameters(cls, payload: Dict[str, Any], mode: StrategyRunMode) -> None:
    payload = cls._normalize_exit_settings(payload)
    cls._normalize_signal_policy(payload.get("signal_policy"))
    numeric_ranges = {
      "target_trade_amount": (100.0, 1_000_000.0, 10_000.0),
      "max_trade_amount": (100.0, 1_000_000.0, 12_000.0),
      "max_concurrent_batches": (1.0, 20.0, 3.0),
      "max_total_t_exposure_pct": (0.01, 1.0, 0.1),
      "max_price_deviation_pct": (0.05, 2.0, 0.3),
      "target_profit_pct": (0.1, 20.0, 2.0),
      "base_floor_pct": (-2.0, 10.0, 0.5),
      "initial_gap_pct": (0.1, 10.0, 1.5),
      "trailing_gap_slope": (0.0, 2.0, 0.25),
      "max_gap_pct": (0.1, 15.0, 3.0),
      "high_profit_arm_pct": (0.5, 30.0, 4.0),
      "high_profit_max_drawdown_pct": (0.1, 10.0, 1.2),
      "rapid_reversal_window_seconds": (3.0, 120.0, 15.0),
      "rapid_reversal_drawdown_pct": (0.1, 5.0, 0.8),
      "rapid_reversal_confirm_ticks": (1.0, 10.0, 2.0),
      "limit_up_touch_tolerance_ticks": (0.0, 20.0, 0.0),
      "cooldown_seconds": (0.0, 3600.0, 300.0),
      "max_exit_slippage_bps": (0.0, 200.0, 30.0),
      "commission_rate": (0.0, 0.01, 0.0003),
      "minimum_commission": (0.0, 100.0, 5.0),
      "stamp_tax_rate": (0.0, 0.01, 0.0005),
      "transfer_fee_rate": (0.0, 0.01, 0.00001),
    }
    values: Dict[str, float] = {}
    for key, (minimum, maximum, default) in numeric_ranges.items():
      try:
        value = float(payload.get(key, default))
      except (TypeError, ValueError) as exc:
        raise ValueError(f"参数 {key} 必须是数字") from exc
      if not math.isfinite(value) or value < minimum or value > maximum:
        raise ValueError(f"参数 {key} 必须在 {minimum:g} 到 {maximum:g} 之间")
      values[key] = value
    if values["max_trade_amount"] < values["target_trade_amount"]:
      raise ValueError("单次金额硬上限不能低于目标单次金额")
    if values["base_floor_pct"] >= values["target_profit_pct"]:
      raise ValueError("初始保护线必须低于止盈武装线")
    if values["max_gap_pct"] < values["initial_gap_pct"]:
      raise ValueError("最大回撤间距不能小于初始回撤间距")
    if values["high_profit_arm_pct"] <= values["target_profit_pct"]:
      raise ValueError("高利润保护武装线必须高于基础止盈武装线")
    if values["high_profit_max_drawdown_pct"] >= values["high_profit_arm_pct"]:
      raise ValueError("高利润最大回吐必须低于高利润保护武装线")
    try:
      hard_stop_pct = float(payload.get("hard_stop_pct", -0.8))
    except (TypeError, ValueError) as exc:
      raise ValueError("参数 hard_stop_pct 必须是数字") from exc
    if bool(payload.get("hard_stop_enabled", False)) and (
      not math.isfinite(hard_stop_pct) or hard_stop_pct <= -10.0 or hard_stop_pct >= 0.0
    ):
      raise ValueError("启用硬止损时，止损比例必须大于 -10 且小于 0")

    time_exit_mode = str(payload.get("time_exit_mode", "UNLIMITED") or "")
    if time_exit_mode not in {"UNLIMITED", "END_OF_DAY", "MAX_HOLDING_DAYS"}:
      raise ValueError("时间退出模式必须是 UNLIMITED、END_OF_DAY 或 MAX_HOLDING_DAYS")
    try:
      max_holding_days = int(payload.get("max_holding_trading_days", 5))
    except (TypeError, ValueError) as exc:
      raise ValueError("最长持有交易日必须是整数") from exc
    if time_exit_mode == "MAX_HOLDING_DAYS" and not 1 <= max_holding_days <= 250:
      raise ValueError("最长持有交易日必须在 1 到 250 之间")
    if time_exit_mode == "UNLIMITED":
      return

    raw_exit_time = str(payload.get("time_exit_time", "14:50") or "")
    try:
      hour, minute = (int(part) for part in raw_exit_time.split(":", 1))
      parsed_exit_time = time(hour=hour, minute=minute)
    except (TypeError, ValueError) as exc:
      raise ValueError("时间退出时刻必须使用 HH:MM 格式") from exc
    if mode == StrategyRunMode.LIVE and not (
      time(hour=14, minute=30) <= parsed_exit_time <= time(hour=14, minute=57)
    ):
      raise ValueError("实盘时间退出时刻必须在 14:30 到 14:57 之间")

  @staticmethod
  def _normalize_exit_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(payload or {})
    raw_mode = normalized.get("time_exit_mode")
    if hasattr(raw_mode, "value"):
      raw_mode = raw_mode.value
    if raw_mode is None:
      raw_mode = (
        "END_OF_DAY"
        if bool(normalized.get("flatten_end_of_day", False))
        else "UNLIMITED"
      )
    normalized["time_exit_mode"] = str(raw_mode or "UNLIMITED").upper()
    normalized["time_exit_time"] = str(
      normalized.get(
        "time_exit_time",
        normalized.get("end_of_day_exit_time", "14:50"),
      )
      or "14:50"
    )
    normalized["max_holding_trading_days"] = normalized.get(
      "max_holding_trading_days", 5
    )
    if "hard_stop_enabled" not in normalized:
      normalized["hard_stop_enabled"] = (
        "hard_stop_pct" in normalized and "time_exit_mode" not in payload
      )
    normalized["hard_stop_pct"] = normalized.get("hard_stop_pct", -0.8)
    normalized["signal_policy"] = TTradeService._normalize_signal_policy(
      normalized.get("signal_policy")
    )
    return normalized

  @staticmethod
  def _normalize_signal_policy(value: Any) -> Dict[str, Any]:
    return normalize_signal_policy(value)
