"""Engine-owned orchestration for account-level board historical replay."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from math import isfinite
from typing import Any, Optional

from quantx_domain.strategies import AshareLimitUpBoardAssistantStrategy
from quantx_domain.trading.limit_up_board_replay import (
  LIMIT_UP_BOARD_REPLAY_SCENARIO_PROFILE,
  get_limit_up_board_replay_scenarios,
)
from quantx_infrastructure.core.assistant_strategy_policy import (
  LIMIT_UP_BOARD_ASSISTANT_STRATEGY_CLASS_NAME,
)
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.enums import StrategyRunMode
from quantx_infrastructure.repositories.backtest_repository import BacktestRepository
from quantx_infrastructure.repositories.daily_asset_snapshot_repository import (
  DailyAssetSnapshotRepository,
)
from quantx_infrastructure.repositories.limit_up_board_assistant_repository import (
  LimitUpBoardAssistantConfigRepository,
)
from quantx_infrastructure.repositories.strategy_repository import StrategyRepository
from quantx_infrastructure.services.limit_up_board_replay_dataset import (
  LimitUpBoardReplayDatasetService,
)
from quantx_infrastructure.services.limit_up_board_replay_projection_service import (
  LimitUpBoardReplayUpdateKind,
  limit_up_board_replay_projection_service,
)
from quantx_infrastructure.services.trading_time_service import TradingDateHelper

from .limit_up_board_assistant import ASSISTANT_DEFAULTS

_CANCELLABLE_STATUSES = frozenset({"PENDING", "STARTING", "RUNNING"})
logger = logging.getLogger(__name__)


class LimitUpBoardReplayService:
  """Create and coordinate one isolated BACKTEST per standard scenario."""

  def __init__(self, runtime_manager: Any = None) -> None:
    self._runtime_manager = runtime_manager
    self._datasets = LimitUpBoardReplayDatasetService()

  def _require_runtime_manager(self) -> Any:
    if self._runtime_manager is None:
      raise RuntimeError("该操作只能由 QuantX Engine 执行")
    return self._runtime_manager

  async def prepare(
    self,
    account_id: str,
    start_time: datetime,
    end_time: datetime,
  ) -> dict[str, Any]:
    account_id = str(account_id or "").strip()
    if not account_id:
      raise ValueError("账户不能为空")
    start_time, end_time, trading_dates = await self._validate_window(
      start_time, end_time
    )
    config = await self._load_config(account_id)
    settings = {**ASSISTANT_DEFAULTS, **dict(getattr(config, "settings", {}) or {})}
    snapshot = await self._load_initial_snapshot(account_id, start_time)
    dataset = await self._datasets.prepare(
      start_time=start_time,
      end_time=end_time,
      settings=settings,
      expected_trading_dates=trading_dates,
    )
    return {
      "account_id": account_id,
      "start_time": start_time,
      "end_time": end_time,
      "trading_day_count": len(trading_dates),
      "scenario_profile": LIMIT_UP_BOARD_REPLAY_SCENARIO_PROFILE,
      "scenarios": [
        scenario.to_dict() for scenario in get_limit_up_board_replay_scenarios()
      ],
      "snapshot_id": getattr(snapshot, "id", None),
      "snapshot_date": getattr(snapshot, "trade_date", None),
      "initial_cash": float(getattr(snapshot, "cash_available_cny", 0.0) or 0.0),
      "initial_total_asset": float(
        getattr(snapshot, "total_asset_cny", 0.0) or 0.0
      ),
      "requires_manual_assets": snapshot is None,
      "dataset_fingerprint": dataset.dataset_fingerprint,
      "config_fingerprint": dataset.config_fingerprint,
      "input_manifest": dataset.input_manifest,
      "data_quality": dataset.data_quality,
    }

  async def start(
    self,
    payload: dict[str, Any],
    *,
    defer_start: bool = False,
    request_id: Optional[str] = None,
  ) -> dict[str, Any]:
    account_id = str(payload.get("account_id") or "").strip()
    if not account_id:
      raise ValueError("账户不能为空")
    # API derives the domain job id from account + client idempotency key.  The
    # outbox message id is transport metadata and must not become the durable
    # replay identity.
    job_id = str(
      payload.get("job_id")
      or payload.get("idempotency_key")
      or request_id
      or uuid.uuid4()
    )
    existing = await limit_up_board_replay_projection_service.get(job_id)
    if existing is not None:
      if str(existing.get("account_id") or "") != account_id:
        raise ValueError("回放幂等键不属于指定账户")
      return existing

    if await limit_up_board_replay_projection_service.has_active(account_id):
      raise ValueError("该账户已有正在执行的打板回放，请等待完成或先取消")
    start_time, end_time, trading_dates = await self._validate_window(
      payload.get("start_time"), payload.get("end_time")
    )
    profile = str(
      payload.get("scenario_profile") or LIMIT_UP_BOARD_REPLAY_SCENARIO_PROFILE
    ).upper()
    scenarios = get_limit_up_board_replay_scenarios(profile)
    config = await self._load_config(account_id)
    if config is None:
      raise ValueError("请先保存打板助手配置，再创建历史回放")
    settings = {**ASSISTANT_DEFAULTS, **dict(config.settings or {})}
    snapshot = await self._load_initial_snapshot(account_id, start_time)
    initial_cash, initial_total_asset = self._resolve_initial_assets(
      payload,
      snapshot,
    )
    replay_config = {
      "schema_version": 1,
      "account_id": account_id,
      "start_time": start_time.isoformat(),
      "end_time": end_time.isoformat(),
      "assistant_config_version": int(config.config_version or 0),
      "assistant_settings": settings,
      "strategy_contract": "AshareLimitUpBoardAssistantStrategy:2.0.0",
      "execution_model": "ENGINE_BACKTEST_STRICT_BOOK_V1",
      "queue_policy": "NO_QUEUE_CREDIT",
      "scenario_profile": profile,
      "scenarios": [scenario.to_dict() for scenario in scenarios],
      "initial_cash": initial_cash,
      "initial_total_asset": initial_total_asset,
      "initial_asset_source": {
        "snapshot_id": getattr(snapshot, "id", None),
        "snapshot_date": (
          getattr(snapshot, "trade_date", None).isoformat()
          if getattr(snapshot, "trade_date", None)
          else None
        ),
        "snapshot_source": getattr(snapshot, "source", None),
        "manual": snapshot is None,
      },
      "commission_rate": float(payload.get("commission_rate", 0.0003) or 0.0),
      "minimum_commission": float(payload.get("minimum_commission", 5.0) or 0.0),
      "stamp_tax_rate": float(payload.get("stamp_tax_rate", 0.0005) or 0.0),
      "transfer_fee_rate": float(
        payload.get("transfer_fee_rate", 0.00001) or 0.0
      ),
      "slippage_rate": float(payload.get("slippage_rate", 0.0001) or 0.0),
    }
    dataset = await self._datasets.prepare(
      start_time=start_time,
      end_time=end_time,
      settings={**settings, "_replay_contract": replay_config},
      expected_trading_dates=trading_dates,
    )
    blockers = list(dataset.data_quality.get("blockers") or [])
    if blockers:
      raise ValueError("历史回放数据质量不满足执行要求：" + "、".join(blockers))
    materialization = self._datasets.persist_artifact(dataset, job_id=job_id)
    manifest_path = materialization.manifest_path
    request = {
      "start_time": start_time.isoformat(),
      "end_time": end_time.isoformat(),
      "initial_cash": initial_cash,
      "initial_total_asset": initial_total_asset,
      "snapshot_id": getattr(snapshot, "id", None),
      "snapshot_date": (
        getattr(snapshot, "trade_date", None).isoformat()
        if getattr(snapshot, "trade_date", None)
        else None
      ),
    }
    await limit_up_board_replay_projection_service.create_job(
      job_id=job_id,
      account_id=account_id,
      scenario_profile=profile,
      request=request,
      dataset_fingerprint=materialization.dataset_fingerprint,
      config_fingerprint=materialization.config_fingerprint,
      input_manifest=materialization.input_manifest,
      data_quality=materialization.data_quality,
    )

    strategy_id = await self._strategy_template_id()
    manager = self._require_runtime_manager()
    created_runs: list[str] = []
    try:
      for scenario in scenarios:
        run_id = self._child_id(job_id, f"run:{scenario.scenario_id}")
        backtest_id = self._child_id(job_id, f"backtest:{scenario.scenario_id}")
        parameters = {
          **settings,
          "limit_up_board_replay": True,
          "limit_up_board_replay_job_id": job_id,
          "limit_up_board_replay_scenario_id": scenario.scenario_id,
          "limit_up_board_replay_scenario_profile": profile,
          "limit_up_board_replay_confirmation_delay_ms": (
            scenario.confirmation_delay_ms
          ),
          "replay_input_manifest_path": manifest_path,
          "limit_up_board_replay_dataset_fingerprint": (
            materialization.dataset_fingerprint
          ),
          "limit_up_board_replay_config_fingerprint": (
            materialization.config_fingerprint
          ),
          "limit_up_board_replay_input_manifest": materialization.input_manifest,
          "limit_up_board_replay_data_quality": materialization.data_quality,
          "account_id": account_id,
          "initial_capital": initial_total_asset,
          "initial_cash": initial_cash,
          "initial_total_asset": initial_total_asset,
          "initial_positions": [],
          "entry_execution_mode": "MANUAL_CONFIRM",
          "auto_approve_manual_intents": False,
          "auto_exit_authorized": True,
          "promotion_model_mode": "PAPER",
          "global_config_version": int(config.config_version or 0),
          "position_profile_overrides": {
            "max_position_pct": float(settings["max_single_position_pct"]),
            "swing_max_pct": float(settings["max_single_position_pct"]),
            "allow_swing_buy": True,
          },
          "risk_caps": {
            "max_position_pct": float(settings["max_single_position_pct"]),
            "max_new_buy_pct_today": float(settings["max_daily_exposure_pct"]),
            "max_open_positions": int(settings["max_open_positions"]),
          },
          "enable_reserve": True,
          "enforce_trading_hours": True,
          "require_book_depth": True,
          "no_queue_credit": True,
          "participation_cap_pct": scenario.participation_cap_pct,
          "book_depth_participation_pct": (
            scenario.book_depth_participation_pct
          ),
          "replay_start_time": start_time.isoformat(),
          "replay_end_time": end_time.isoformat(),
          "commission_rate": replay_config["commission_rate"],
          "minimum_commission": replay_config["minimum_commission"],
          "stamp_tax_rate": replay_config["stamp_tax_rate"],
          "transfer_fee_rate": replay_config["transfer_fee_rate"],
          "slippage_rate": replay_config["slippage_rate"],
        }
        created_id = await manager.run_strategy(
          strategy_id=strategy_id,
          strategy_class=AshareLimitUpBoardAssistantStrategy,
          mode=StrategyRunMode.BACKTEST,
          instruments=list(dataset.instruments),
          parameters=parameters,
          name=f"打板历史回放-{account_id}-{scenario.scenario_id}",
          backtest_start_time=start_time,
          backtest_end_time=end_time,
          auto_start=False,
          run_id=run_id,
          backtest_id=backtest_id,
        )
        created_runs.append(created_id)
        await limit_up_board_replay_projection_service.bind_scenario(
          job_id=job_id,
          scenario_id=scenario.scenario_id,
          backtest_id=backtest_id,
          confirmation_delay_ms=scenario.confirmation_delay_ms,
          participation_cap_pct=scenario.participation_cap_pct,
          book_depth_participation_pct=scenario.book_depth_participation_pct,
        )
      for run_id in created_runs:
        started = (
          await manager.defer_start_strategy(run_id)
          if defer_start
          else await manager.start_strategy(run_id)
        )
        if not started:
          raise ValueError(f"打板回放成交情景启动失败: {run_id}")
    except Exception as exc:
      for run_id in created_runs:
        try:
          await manager.cancel_deferred_start(run_id)
          await manager.stop_strategy(run_id)
        except Exception:
          pass
      for scenario in (await limit_up_board_replay_projection_service.get(job_id) or {}).get(
        "scenarios", []
      ):
        try:
          await limit_up_board_replay_projection_service.update_scenario(
            backtest_id=str(scenario["backtest_id"]),
            status="ERROR",
            error_message=str(exc),
            kind=LimitUpBoardReplayUpdateKind.RESULT_READY,
          )
        except Exception:
          pass
      try:
        await limit_up_board_replay_projection_service.update_job_error(
          job_id=job_id,
          error_message=str(exc),
        )
      except Exception:
        logger.exception("打板回放任务失败状态收敛异常: job_id=%s", job_id)
      raise
    result = await limit_up_board_replay_projection_service.get(job_id)
    if result is None:
      raise ValueError("打板回放创建后无法读取")
    return result

  async def cancel(self, job_id: str) -> dict[str, Any]:
    snapshot = await limit_up_board_replay_projection_service.get(job_id)
    if snapshot is None:
      raise ValueError("打板回放不存在")
    if str(snapshot.get("status") or "").upper() not in _CANCELLABLE_STATUSES:
      raise ValueError("当前打板回放状态不允许取消")
    manager = self._require_runtime_manager()
    for scenario in list(snapshot.get("scenarios") or []):
      status = str(scenario.get("status") or "").upper()
      if status not in _CANCELLABLE_STATUSES:
        continue
      backtest_id = str(scenario.get("backtest_id") or "")
      async for db in get_async_db():
        backtest = await BacktestRepository(db).get_backtest(backtest_id)
        break
      else:
        backtest = None
      if backtest is not None:
        run_id = str(backtest.strategy_run_id)
        await manager.cancel_deferred_start(run_id)
        await manager.stop_strategy(run_id)
        async for db in get_async_db():
          await BacktestRepository(db).update_backtest_status(
            backtest_id,
            "CANCELLED",
            end_time=time_utils.now(),
          )
          break
      await limit_up_board_replay_projection_service.update_scenario(
        backtest_id=backtest_id,
        status="CANCELLED",
        progress_pct=float(scenario.get("progress_pct") or 0.0),
        kind=LimitUpBoardReplayUpdateKind.RESULT_READY,
      )
    await limit_up_board_replay_projection_service.cancel_job(
      job_id=job_id,
      reason="USER_CANCELLED",
    )
    result = await limit_up_board_replay_projection_service.get(job_id)
    if result is None:
      raise ValueError("打板回放取消后无法读取")
    return result

  async def _validate_window(
    self,
    start_time: Any,
    end_time: Any,
  ) -> tuple[datetime, datetime, list[Any]]:
    start = self._naive(start_time)
    end = self._naive(end_time)
    if end <= start:
      raise ValueError("回放结束时间必须晚于开始时间")
    trading_dates = await TradingDateHelper().get_trading_calendar(
      market="SH",
      start_date=start.date(),
      end_date=end.date(),
    )
    if not trading_dates:
      raise ValueError("回放区间内没有交易日")
    if len(trading_dates) > 20:
      raise ValueError("单次打板回放最多支持 20 个交易日")
    return start, end, list(trading_dates)

  async def _load_config(self, account_id: str) -> Any:
    async for db in get_async_db():
      return await LimitUpBoardAssistantConfigRepository(db).find_by_account(
        account_id
      )
    return None

  async def _load_initial_snapshot(self, account_id: str, start_time: datetime) -> Any:
    async for db in get_async_db():
      repo = DailyAssetSnapshotRepository(db)
      return await repo.find_previous(repo.scope_key("account", account_id), start_time.date())
    return None

  @staticmethod
  def _resolve_initial_assets(payload: dict[str, Any], snapshot: Any) -> tuple[float, float]:
    if snapshot is not None:
      cash = float(snapshot.cash_available_cny or 0.0)
      total = float(snapshot.total_asset_cny or 0.0)
    else:
      cash = LimitUpBoardReplayService._optional_number(payload.get("initial_cash"))
      total = LimitUpBoardReplayService._optional_number(
        payload.get("initial_total_asset")
      )
      if cash is None or total is None:
        raise ValueError("开始日前没有账户日结快照，请提供初始资金与总资产")
    if not isfinite(cash) or not isfinite(total) or cash < 0 or total <= 0:
      raise ValueError("初始资金必须为有限非负数且总资产必须大于零")
    if cash > total + 0.01:
      raise ValueError("初始可用资金不能超过总资产")
    return cash, total

  async def _strategy_template_id(self) -> int:
    async for db in get_async_db():
      strategy = await StrategyRepository(db).find_by_class_name(
        LIMIT_UP_BOARD_ASSISTANT_STRATEGY_CLASS_NAME
      )
      if strategy is None:
        raise ValueError("账户级打板助手策略模板尚未注册")
      return int(strategy.id)
    raise ValueError("无法读取账户级打板助手策略模板")

  @staticmethod
  def _child_id(job_id: str, name: str) -> str:
    try:
      namespace = uuid.UUID(str(job_id))
    except ValueError:
      namespace = uuid.uuid5(uuid.NAMESPACE_URL, str(job_id))
    return str(uuid.uuid5(namespace, f"limit-up-board-replay-v1:{name}"))

  @staticmethod
  def _naive(value: Any) -> datetime:
    if isinstance(value, datetime):
      return time_utils.to_shanghai(value).replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str) and value:
      parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
      return time_utils.to_shanghai(parsed).replace(tzinfo=None) if parsed.tzinfo else parsed
    raise ValueError("回放时间格式无效")

  @staticmethod
  def _optional_number(value: Any) -> Optional[float]:
    if value is None:
      return None
    try:
      return float(value)
    except (TypeError, ValueError) as exc:
      raise ValueError("初始资金必须是数字") from exc

  @staticmethod
  def mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
      return dict(value)
    if isinstance(value, str) and value.strip():
      try:
        parsed = json.loads(value)
      except (TypeError, ValueError):
        return {}
      return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


__all__ = ["LimitUpBoardReplayService"]
