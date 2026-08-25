"""Durable control-plane command consumer owned by the Engine."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from quantx_domain.clock import utcnow
from quantx_domain.grid_book import GRID_BOOK_CUSTOM_STATE_KEY
from quantx_infrastructure.core.assistant_strategy_policy import (
  LIMIT_UP_BOARD_STRATEGY_CLASS_NAME,
)
from quantx_infrastructure.core.strategy_registry import strategy_registry
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import EngineCommandOutbox
from quantx_infrastructure.models.enums import StrategyRunMode
from quantx_infrastructure.repositories.strategy_repository import StrategyRepository
from quantx_infrastructure.repositories.strategy_run_repository import (
  StrategyRunRepository,
)
from quantx_infrastructure.services.auto_exit_plan_service import AutoExitPlanService
from quantx_infrastructure.services.entry_plan_service import EntryPlanService
from quantx_infrastructure.services.exit_plan_replay_service import (
  ExitPlanReplayService,
)
from quantx_infrastructure.services.t_trade_replay_service import TTradeReplayService
from quantx_infrastructure.services.t_trade_service import (
  TTradeApprovalExpectation,
  TTradeService,
)
from sqlalchemy import select, update

from quantx_engine.strategy_manager import strategy_manager
from quantx_engine.warm_cache import (
  intraday_warm_cache,
)

from .conditional_liquidation import conditional_liquidation_monitor
from .exit_plan_monitor import exit_plan_monitor
from .limit_up_board_replay_service import LimitUpBoardReplayService
from .limit_up_board_runtime import limit_up_board_assistant
from .t_trade_runtime import t_trade_global_monitor


def _json_value(value: Any) -> Any:
  if isinstance(value, Enum):
    return value.value
  if isinstance(value, (datetime, date)):
    return value.isoformat()
  if isinstance(value, dict):
    return {str(key): _json_value(item) for key, item in value.items()}
  if isinstance(value, (list, tuple, set)):
    return [_json_value(item) for item in value]
  if hasattr(value, "to_dict"):
    return _json_value(value.to_dict())
  return value


def _parse_datetime(value: Any) -> Optional[datetime]:
  if isinstance(value, datetime) or value is None:
    return value
  return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _mapping(value: Any) -> dict[str, Any]:
  if isinstance(value, dict):
    return dict(value)
  if isinstance(value, str):
    try:
      parsed = json.loads(value)
    except (TypeError, ValueError):
      return {}
    return dict(parsed) if isinstance(parsed, dict) else {}
  return {}


async def _strategy_create(payload: dict[str, Any]) -> dict[str, Any]:
  run_id = str(payload["run_id"])
  existing_runtime = strategy_manager.get_run(run_id)
  if existing_runtime is not None:
    return {"run_id": run_id}

  async with AsyncSessionLocal() as db:
    strategy = await StrategyRepository(db).find_by_id(int(payload["strategy_id"]))
    if strategy and strategy.class_name == LIMIT_UP_BOARD_STRATEGY_CLASS_NAME:
      mode = StrategyRunMode(str(payload["mode"]).lower())
      if mode != StrategyRunMode.BACKTEST:
        instruments = {
          str(code or "").strip().upper()
          for code in list(payload.get("instruments") or [])
          if str(code or "").strip()
        }
        parameters = _mapping(payload.get("parameters"))
        account_id = str(parameters.get("account_id") or "").strip()
        active_runs = await StrategyRunRepository(
          db
        ).find_active_runs_by_strategy_class(LIMIT_UP_BOARD_STRATEGY_CLASS_NAME)
        for active_run in active_runs:
          if str(active_run.id) == run_id:
            continue
          active_instruments = {
            str(code or "").strip().upper()
            for code in list(active_run.instruments or [])
            if str(code or "").strip()
          }
          if not instruments.intersection(active_instruments):
            continue
          active_parameters = _mapping(active_run.parameters)
          active_account_id = str(
            active_parameters.get("account_id") or ""
          ).strip()
          if account_id and active_account_id and account_id != active_account_id:
            continue
          raise ValueError(
            f"ACTIVE_LIMIT_UP_INSTANCE_EXISTS:{active_run.id}"
          )
  if strategy is None:
    raise ValueError(f"策略模板不存在: {payload['strategy_id']}")
  strategy_class = strategy_registry.get_strategy_class(
    strategy.class_name,
    strategy.file_path,
  )
  created_id = await strategy_manager.run_strategy(
    strategy_id=int(payload["strategy_id"]),
    strategy_class=strategy_class,
    mode=StrategyRunMode(str(payload["mode"]).lower()),
    instruments=list(payload.get("instruments") or []),
    parameters=dict(payload.get("parameters") or {}),
    name=payload.get("name"),
    backtest_start_time=_parse_datetime(payload.get("backtest_start_time")),
    backtest_end_time=_parse_datetime(payload.get("backtest_end_time")),
    auto_start=bool(payload.get("auto_start", True)),
    run_id=run_id,
    backtest_id=payload.get("backtest_id"),
  )
  return {"run_id": created_id, "backtest_id": payload.get("backtest_id")}


async def _delete_strategy(run_id: str) -> dict[str, Any]:
  if strategy_manager.get_run(run_id) is not None:
    await strategy_manager.executor.delete(run_id)
  async with AsyncSessionLocal() as db:
    deleted = await StrategyRunRepository(db).delete_run(run_id)
  return {"success": bool(deleted)}


async def _reload_strategy(payload: dict[str, Any]) -> dict[str, Any]:
  run_id = str(payload["run_id"])
  runtime = strategy_manager.get_run(run_id)
  if runtime is None:
    return {"success": True, "loaded": False}
  parameters = dict(payload.get("parameters") or {})
  runtime.context.parameters = parameters
  if runtime.strategy is not None:
    runtime.strategy.context.parameters = parameters
  snapshot = payload.get("grid_book")
  if snapshot is not None:
    if runtime.state_manager is not None:
      runtime.state_manager.set_custom(GRID_BOOK_CUSTOM_STATE_KEY, snapshot)
    if runtime.strategy is not None and hasattr(
      runtime.strategy,
      "apply_grid_book_snapshot",
    ):
      runtime.strategy.apply_grid_book_snapshot(snapshot)
  return {"success": True, "loaded": True}


async def _dispatch(
  command_type: str,
  payload: dict[str, Any],
  *,
  command_id: Optional[str] = None,
) -> dict[str, Any]:
  run_id = str(payload.get("run_id") or "")
  if command_type == "STRATEGY_CREATE":
    return await _strategy_create(payload)
  if command_type == "STRATEGY_START":
    return {"success": bool(await strategy_manager.start_strategy(run_id))}
  if command_type == "STRATEGY_STOP":
    return {"success": bool(await strategy_manager.stop_strategy(run_id))}
  if command_type == "STRATEGY_PAUSE":
    return {"success": bool(await strategy_manager.pause_strategy(run_id))}
  if command_type == "STRATEGY_RESUME":
    return {"success": bool(await strategy_manager.resume_strategy(run_id))}
  if command_type == "STRATEGY_DELETE":
    return await _delete_strategy(run_id)
  if command_type == "STRATEGY_RESTART":
    return {"success": bool(await strategy_manager.restart_strategy(run_id))}
  if command_type == "STRATEGY_RERUN_BACKTEST":
    backtest_id = await strategy_manager.rerun_backtest_version(
      run_id,
      backtest_start_time=_parse_datetime(payload.get("backtest_start_time")),
      backtest_end_time=_parse_datetime(payload.get("backtest_end_time")),
      backtest_id=payload.get("backtest_id"),
    )
    return {"backtest_id": backtest_id}
  if command_type == "STRATEGY_RELOAD":
    return await _reload_strategy(payload)
  if command_type == "STRATEGY_APPROVE_TRADE_INTENT":
    return _json_value(
      await strategy_manager.executor.approve_trade_intent(
        run_id,
        str(payload["intent_id"]),
        approval_audit=dict(payload.get("approval_audit") or {}),
      )
    )
  if command_type == "STRATEGY_REJECT_TRADE_INTENT":
    return _json_value(
      await strategy_manager.executor.reject_trade_intent(
        run_id,
        str(payload["intent_id"]),
        reason=str(payload.get("reason") or "USER_REJECTED"),
      )
    )
  if command_type.startswith("ENTRY_PLAN_") or command_type == (
    "ENTRY_AUTOMATION_SET_PAUSED"
  ):
    entry_plan_service = EntryPlanService(strategy_manager)
    if command_type == "ENTRY_PLAN_CREATE":
      return _json_value(
        await entry_plan_service.create(
          payload,
          command_id=str(command_id or ""),
        )
      )
    if command_type == "ENTRY_PLAN_UPDATE":
      return _json_value(
        await entry_plan_service.update(
          payload,
          command_id=str(command_id or ""),
        )
      )
    if command_type == "ENTRY_PLAN_SET_ENABLED":
      return _json_value(
        await entry_plan_service.set_enabled(
          str(payload["plan_id"]),
          bool(payload["enabled"]),
          account_id=str(payload["account_id"]),
          config_version=int(payload["config_version"]),
          actor_user_id=str(payload.get("actor_user_id") or ""),
        )
      )
    if command_type == "ENTRY_PLAN_CANCEL":
      return _json_value(
        await entry_plan_service.cancel(
          str(payload["plan_id"]),
          account_id=str(payload["account_id"]),
          config_version=int(payload["config_version"]),
          actor_user_id=str(payload.get("actor_user_id") or ""),
          cancel_working_order=bool(payload.get("cancel_working_order", False)),
        )
      )
    if command_type == "ENTRY_PLAN_EVALUATE_NOW":
      return _json_value(
        await entry_plan_service.evaluate_now(
          str(payload["plan_id"]),
          account_id=str(payload["account_id"]),
        )
      )
    if command_type == "ENTRY_PLAN_TRIGGER_MANUAL":
      return _json_value(
        await entry_plan_service.trigger_manual(
          str(payload["plan_id"]),
          str(payload["rule_id"]),
          account_id=str(payload["account_id"]),
        )
      )
    if command_type == "ENTRY_PLAN_PREVIEW_INTENT":
      return _json_value(
        await entry_plan_service.preview_intent(
          str(payload["plan_id"]),
          str(payload["intent_id"]),
          account_id=str(payload["account_id"]),
        )
      )
    if command_type == "ENTRY_AUTOMATION_SET_PAUSED":
      return _json_value(
        await entry_plan_service.set_automation_paused(
          account_id=str(payload["account_id"]),
          paused=bool(payload["paused"]),
          reason=str(payload.get("reason") or "USER_REQUESTED"),
          actor_user_id=str(payload.get("actor_user_id") or ""),
        )
      )
  if command_type == "LIQUIDATION_EVALUATE":
    return {
      "items": _json_value(
        await conditional_liquidation_monitor.evaluate_all_active_orders(
          account_id=payload.get("account_id"),
          stock_code=payload.get("stock_code"),
        )
      )
    }
  if command_type == "EXIT_PLAN_CREATE_MANUAL":
    record = await AutoExitPlanService().create_manual_exit_plan(payload)
    return {"plan_id": record.plan_id, "config_version": record.config_version}
  if command_type == "EXIT_PLAN_UPDATE_MANUAL":
    record = await AutoExitPlanService().update_manual_exit_plan(payload)
    return {"plan_id": record.plan_id, "config_version": record.config_version}
  if command_type == "EXIT_PLAN_RECONCILE_CAPACITY":
    return await AutoExitPlanService().reconcile_holding_capacity(
      account_id=str(payload["account_id"]),
      instrument_code=str(payload["instrument_code"]),
    )
  if command_type == "EXIT_PLAN_SET_ENABLED":
    record = await AutoExitPlanService().set_enabled(
      str(payload["plan_id"]),
      bool(payload["enabled"]),
      account_id=payload.get("account_id"),
      config_version=payload.get("config_version"),
    )
    return {
      "plan_id": record.plan_id if record else str(payload["plan_id"]),
      "config_version": record.config_version if record else None,
    }
  if command_type == "EXIT_PLAN_CANCEL":
    record = await AutoExitPlanService().cancel(
      str(payload["plan_id"]),
      str(payload.get("reason") or "USER_CANCELLED"),
      account_id=payload.get("account_id"),
      config_version=payload.get("config_version"),
    )
    return {
      "plan_id": record.plan_id if record else str(payload["plan_id"]),
      "config_version": record.config_version if record else None,
    }
  if command_type == "EXIT_PLAN_EVALUATE_NOW":
    return {
      "items": _json_value(
        await exit_plan_monitor.evaluate_all_active_plans(
          account_id=payload.get("account_id"),
          instrument_code=payload.get("instrument_code"),
          plan_id=payload.get("plan_id"),
        )
      )
    }
  if command_type == "EXIT_PLAN_LIQUIDATE_POSITIONS":
    return _json_value(
      await AutoExitPlanService().create_liquidation_group(payload)
    )
  if command_type == "EXIT_PLAN_CONFIRM_INTENT":
    return _json_value(
      await exit_plan_monitor.confirm_exit_intent(
        plan_id=str(payload["plan_id"]),
        intent_id=str(payload["intent_id"]),
      )
    )
  if command_type == "EXIT_PLAN_REJECT_INTENT":
    await AutoExitPlanService().reject_exit_intent(
      plan_id=str(payload["plan_id"]),
      intent_id=str(payload["intent_id"]),
      reason=str(payload.get("reason") or "USER_REJECTED"),
    )
    return {"success": True}
  if command_type == "WARM_CACHE_REFRESH_SOURCES":
    await intraday_warm_cache.refresh_source_symbols()
    return {"success": True}
  if command_type == "WARM_CACHE_STATUS":
    return {
      "items": _json_value(
        intraday_warm_cache.get_status(payload.get("symbols"))
      )
    }

  if command_type == "LIMIT_UP_BOARD_ASSISTANT_SAVE":
    return _json_value(
      await limit_up_board_assistant.save_config(payload["input"])
    )
  if command_type == "LIMIT_UP_BOARD_ASSISTANT_GET":
    return _json_value(
      await limit_up_board_assistant.get_monitor(payload["account_id"])
    )
  if command_type == "LIMIT_UP_BOARD_ASSISTANT_RECONCILE":
    return _json_value(
      await limit_up_board_assistant.reconcile_account(payload["account_id"])
    )
  if command_type == "LIMIT_UP_BOARD_CANDIDATE_ARM":
    return _json_value(await limit_up_board_assistant.arm_candidate(payload))
  if command_type == "LIMIT_UP_BOARD_CANDIDATE_DISARM":
    return _json_value(await limit_up_board_assistant.disarm_candidate(payload))
  if command_type == "FIRST_BOARD_CANDIDATE_PREFERENCE_SET":
    return _json_value(
      await limit_up_board_assistant.set_candidate_preference(payload)
    )

  board_replay_service = LimitUpBoardReplayService(strategy_manager)
  if command_type == "LIMIT_UP_BOARD_REPLAY_START":
    return _json_value(
      await board_replay_service.start(payload["input"], defer_start=True)
    )
  if command_type == "LIMIT_UP_BOARD_REPLAY_CANCEL":
    return _json_value(await board_replay_service.cancel(payload["job_id"]))

  t_trade_service = TTradeService(strategy_manager)
  if command_type == "T_TRADE_APPROVE_ENTRY":
    return _json_value(
      await t_trade_service.approve_entry(
        payload["run_id"],
        payload["intent_id"],
        approval_expectation=TTradeApprovalExpectation.from_payload(payload),
        approval_audit=dict(payload.get("approval_audit") or {}),
      )
    )
  if command_type == "T_TRADE_REJECT_ENTRY":
    return _json_value(
      await t_trade_service.reject_entry(payload["run_id"], payload["intent_id"])
    )
  if command_type == "T_TRADE_IMPORT_EXTERNAL_ENTRY":
    return _json_value(
      await t_trade_service.import_external_entry(
        payload["run_id"],
        payload["account_id"],
        payload["order_id"],
      )
    )
  if command_type == "T_TRADE_SYNC_SOURCE_ORDERS":
    return _json_value(await t_trade_service.sync_source_orders(payload["account_id"]))
  if command_type == "T_TRADE_STOP_SESSION":
    return _json_value(await t_trade_service.stop_session(payload["run_id"]))
  if command_type == "T_TRADE_GLOBAL_SAVE":
    return _json_value(await t_trade_global_monitor.save_config(payload["input"]))
  if command_type == "T_TRADE_SIGNAL_POLICY_PREVIEW":
    return _json_value(
      await t_trade_global_monitor.preview_signal_policy(payload["input"])
    )
  if command_type == "T_TRADE_GLOBAL_GET":
    return _json_value(
      await t_trade_global_monitor.get_monitor(payload["account_id"])
    )
  if command_type == "T_TRADE_GLOBAL_RECONCILE":
    return _json_value(
      await t_trade_global_monitor.reconcile_account(payload["account_id"])
    )

  replay_service = TTradeReplayService(strategy_manager)
  if command_type == "T_TRADE_REPLAY_START":
    start_kwargs: dict[str, Any] = {"defer_start": True}
    if command_id:
      start_kwargs["request_id"] = command_id
    return _json_value(await replay_service.start(payload["input"], **start_kwargs))
  if command_type == "T_TRADE_REPLAY_CANCEL":
    return _json_value(await replay_service.cancel(payload["run_id"]))
  exit_plan_replay_service = ExitPlanReplayService(strategy_manager)
  if command_type == "EXIT_PLAN_REPLAY_START":
    start_kwargs: dict[str, Any] = {"defer_start": True}
    if command_id:
      start_kwargs["request_id"] = command_id
    return _json_value(
      await exit_plan_replay_service.start(payload["input"], **start_kwargs)
    )
  if command_type == "EXIT_PLAN_REPLAY_CANCEL":
    return _json_value(await exit_plan_replay_service.cancel(payload["run_id"]))
  raise ValueError(f"不支持的 Engine command_type: {command_type}")


async def _recover_processing_commands() -> None:
  async with AsyncSessionLocal() as db:
    await db.execute(
      update(EngineCommandOutbox)
      .where(EngineCommandOutbox.processing_status == "PROCESSING")
      .values(
        processing_status="PENDING",
        processing_error="recovered after Engine restart",
        available_at=utcnow(),
      )
    )
    await db.commit()


async def _claim_next() -> Optional[tuple[str, str, dict[str, Any]]]:
  async with AsyncSessionLocal() as db:
    command = await db.scalar(
      select(EngineCommandOutbox)
      .where(
        EngineCommandOutbox.processing_status == "PENDING",
        EngineCommandOutbox.available_at <= utcnow(),
      )
      .order_by(EngineCommandOutbox.created_at)
      .with_for_update(skip_locked=True)
      .limit(1)
    )
    if command is None:
      return None
    command.processing_status = "PROCESSING"
    command.processing_attempts = int(command.processing_attempts or 0) + 1
    command.processing_error = None
    await db.commit()
    return command.message_id, command.command_type, dict(command.payload or {})


async def _complete(
  message_id: str,
  *,
  result: Optional[dict[str, Any]] = None,
  error: Optional[str] = None,
) -> None:
  async with AsyncSessionLocal() as db:
    command = await db.get(EngineCommandOutbox, message_id)
    if command is None:
      return
    command.processing_status = "FAILED" if error else "SUCCEEDED"
    command.result = _json_value(result or {}) if error is None else None
    command.processing_error = error
    command.processed_at = utcnow()
    await db.commit()


async def run_command_consumer(stopped: asyncio.Event) -> None:
  await _recover_processing_commands()
  while not stopped.is_set():
    claimed = await _claim_next()
    if claimed is None:
      try:
        await asyncio.wait_for(stopped.wait(), timeout=0.25)
      except asyncio.TimeoutError:
        pass
      continue
    message_id, command_type, payload = claimed
    try:
      result = await _dispatch(command_type, payload, command_id=message_id)
    except Exception as exc:
      await _complete(message_id, error=str(exc))
    else:
      await _complete(message_id, result=result)
