"""Application service for isolated sell-plan historical replays."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from math import isfinite
from typing import Any, Dict, List, Optional, Tuple

from quantx_domain.strategies.ashare_managed_exit_plan import (
  EXIT_PLAN_ENABLED_KEY,
  MANAGED_EXIT_PLAN_KEY,
  AshareManagedExitPlanStrategy,
)
from quantx_domain.trading.exit_plan import (
  ExitPlanTemplate,
  ExitRuleType,
  estimate_buy_fee_cny,
)
from sqlalchemy import select

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.enums import OrderType, StrategyRunMode
from quantx_infrastructure.models.order import Order
from quantx_infrastructure.repositories.auto_exit_plan_repository import (
  AutoExitPlanRepository,
)
from quantx_infrastructure.repositories.backtest_repository import BacktestRepository
from quantx_infrastructure.repositories.strategy_repository import StrategyRepository
from quantx_infrastructure.repositories.strategy_run_repository import (
  StrategyRunRepository,
)
from quantx_infrastructure.services.exit_plan_replay_projection_service import (
  TERMINAL_EXIT_PLAN_REPLAY_STATUSES,
  ExitPlanReplayUpdateKind,
  exit_plan_replay_projection_service,
)
from quantx_infrastructure.services.trading_time_service import TradingDateHelper

_CANCELLABLE_STATUSES = frozenset({"PENDING", "RUNNING", "PAUSED"})
_STRATEGY_CLASS_NAME = "AshareManagedExitPlanStrategy"


class ExitPlanReplayService:
  def __init__(self, runtime_manager: Any = None) -> None:
    self._runtime_manager = runtime_manager

  def _require_runtime_manager(self) -> Any:
    if self._runtime_manager is None:
      raise RuntimeError("该操作只能由 QuantX Engine 执行")
    return self._runtime_manager

  async def prepare(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    account_id = str(payload.get("account_id") or "").strip()
    if not account_id:
      raise ValueError("账户不能为空")
    template, saved = await self._resolve_template(payload, account_id=account_id)
    code = template.instrument_code.strip().upper()
    candidates = await self._load_buy_orders(
      account_id=account_id, instrument_code=code, order_ids=None, limit=100
    )
    cost_basis = dict(template.metadata.get("cost_basis") or {})
    selected_ids = {
      str(item.get("order_id") or "")
      for item in list(cost_basis.get("selected_orders") or [])
    }
    requires_depth = any(
      rule.enabled
      and rule.strategy == ExitRuleType.ADAPTIVE_VOLUME_PRICE_TRAILING.value
      for rule in template.rules
    )
    return {
      "account_id": account_id,
      "plan_id": saved.plan_id if saved is not None else None,
      "config_version": int(template.config_version),
      "instrument_code": code,
      "plan_source": "SAVED" if saved is not None else "DRAFT",
      "template": template.to_dict(),
      "requires_tick": True,
      "requires_depth": requires_depth,
      "default_window_trading_days": 20,
      "quick_windows": [5, 10, 20],
      "buy_fills": [
        {
          **item,
          "selected_by_plan": str(item["order_id"]) in selected_ids,
        }
        for item in candidates
      ],
      "message": (
        "已冻结已保存计划版本，可选择真实买入成交作为回放起点"
        if saved is not None
        else "将按当前未保存草稿生成一次性回放快照"
      ),
      "blocking_reasons": (
        [] if candidates else ["没有可用的真实历史买入成交，可改用手工历史快照"]
      ),
    }

  async def start(
    self,
    payload: Dict[str, Any],
    *,
    defer_start: bool = False,
    request_id: Optional[str] = None,
  ) -> Dict[str, Any]:
    account_id = str(payload.get("account_id") or "").strip()
    if not account_id:
      raise ValueError("账户不能为空")
    normalized_request_id = str(request_id or "").strip()
    if normalized_request_id:
      existing_run, existing_backtest = await self._load_run_and_backtest(
        normalized_request_id
      )
      if existing_run is not None:
        params = self._mapping(existing_run.parameters)
        if not params.get("exit_plan_replay"):
          raise ValueError("回放请求幂等键已被其他策略运行占用")
        projection = await exit_plan_replay_projection_service.get(
          normalized_request_id
        )
        if projection is None:
          raise ValueError("回放运行缺少生命周期投影")
        return self._project(existing_run, existing_backtest, projection)

    template, saved = await self._resolve_template(payload, account_id=account_id)
    expected_version = payload.get("expected_config_version")
    if saved is not None and (
      expected_version is None or int(expected_version) != int(saved.config_version)
    ):
      raise ValueError(
        f"CONFIG_VERSION_CONFLICT: current={int(saved.config_version or 0)}"
      )
    origin = await self._resolve_origin(payload, template, account_id=account_id)
    requested_start = self._naive(payload.get("start_time"))
    end_time = self._naive(payload.get("end_time"))
    start_time = max(requested_start, origin["activation_time"])
    if end_time <= start_time:
      raise ValueError("回放结束时间必须晚于计划激活时间")
    trading_dates = await TradingDateHelper().get_trading_calendar(
      market="SH", start_date=start_time.date(), end_date=end_time.date()
    )
    if not trading_dates:
      raise ValueError("回放区间内没有交易日")
    if len(trading_dates) > 20:
      raise ValueError("单次卖出计划回放最多支持 20 个交易日")
    if any(item >= time_utils.today() for item in trading_dates):
      raise ValueError("历史回放只能使用当前日期之前已完成的交易日")
    if await exit_plan_replay_projection_service.has_active(account_id):
      raise ValueError("该账户已有正在执行的卖出计划回放")

    code = template.instrument_code.strip().upper()
    volume = int(origin["volume"])
    entry_price = float(origin["unit_cost"])
    initial_total_asset = entry_price * volume
    if initial_total_asset <= 0 or not isfinite(initial_total_asset):
      raise ValueError("回放初始持仓价值无效")
    actual_sell_references = await self._load_actual_sell_references(
      account_id=account_id,
      instrument_code=code,
      start_time=origin["activation_time"],
      end_time=end_time,
    )
    template_payload = template.to_dict()
    template_payload.update(
      {
        "plan_id": f"replay-{normalized_request_id or uuid.uuid4()}",
        "account_id": account_id,
        "run_id": normalized_request_id,
        "source_type": "EXIT_PLAN_REPLAY",
        "source_id": saved.plan_id if saved is not None else "DRAFT",
        "auto_exit_authorized": False,
      }
    )
    replay_template = ExitPlanTemplate.from_dict(template_payload)
    cost_parameters = {
      "commission_rate": float(payload.get("commission_rate", 0.0003) or 0.0),
      "minimum_commission": float(payload.get("minimum_commission", 5.0) or 0.0),
      "stamp_tax_rate": float(payload.get("stamp_tax_rate", 0.0005) or 0.0),
      "transfer_fee_rate": float(payload.get("transfer_fee_rate", 0.00001) or 0.0),
      "slippage_rate": float(payload.get("slippage_rate", 0.0001) or 0.0),
    }
    if any(not isfinite(value) or value < 0 for value in cost_parameters.values()):
      raise ValueError("回放成本参数必须是有限的非负数")
    if cost_parameters["slippage_rate"] >= 1:
      raise ValueError("回放滑点率必须小于 1")
    initially_available = (
      volume if start_time.date() > origin["activation_time"].date() else 0
    )
    parameters = {
      "exit_plan_replay": True,
      "auto_approve_manual_intents": True,
      "runtime_state_checkpoint_policy": "DAY_BATCH",
      "account_id": account_id,
      "instrument_code": code,
      "position_shares": volume,
      "position_available_shares": initially_available,
      "core_shares": volume,
      "locked_core_shares": 0,
      "swing_shares": 0,
      "avg_cost": entry_price,
      "base_price": entry_price,
      "initial_capital": initial_total_asset,
      "initial_cash": 0.0,
      "initial_total_asset": initial_total_asset,
      "initial_portfolio_as_of": origin["activation_time"].isoformat(),
      "replay_start_time": start_time.isoformat(),
      "replay_end_time": end_time.isoformat(),
      "replay_entry_volume": volume,
      "exit_plan_replay_plan_id": saved.plan_id if saved is not None else None,
      "exit_plan_replay_config_version": int(template.config_version),
      "exit_plan_replay_template": replay_template.to_dict(),
      MANAGED_EXIT_PLAN_KEY: replay_template.to_dict(),
      EXIT_PLAN_ENABLED_KEY: True,
      "initial_protected_volume": volume,
      "initial_entry_avg_price": entry_price,
      "initial_entry_time": origin["activation_time"].isoformat(),
      "exit_plan_replay_origin": self._serialize_origin(origin),
      "actual_sell_references": actual_sell_references,
      **cost_parameters,
    }
    strategy_id = await self._get_strategy_template_id()
    manager = self._require_runtime_manager()
    run_id = await manager.run_strategy(
      strategy_id=strategy_id,
      strategy_class=AshareManagedExitPlanStrategy,
      mode=StrategyRunMode.BACKTEST,
      instruments=[code],
      parameters=parameters,
      name=f"卖出计划回放-{code}-{start_time:%Y%m%d}",
      backtest_start_time=start_time,
      backtest_end_time=end_time,
      auto_start=False,
      run_id=normalized_request_id or None,
      backtest_id=(
        self._request_backtest_id(normalized_request_id)
        if normalized_request_id
        else None
      ),
    )
    await exit_plan_replay_projection_service.create(
      run_id=run_id,
      account_id=account_id,
      plan_id=saved.plan_id if saved is not None else None,
      instrument_code=code,
    )
    if defer_start:
      if not await manager.defer_start_strategy(run_id):
        await manager.converge_deferred_start_error(
          run_id, "卖出计划历史回放后台启动调度失败"
        )
        raise ValueError("卖出计划历史回放后台启动调度失败")
      replay = await self.get(run_id)
      if replay is None:
        raise ValueError("卖出计划历史回放创建后无法读取")
      return replay
    if not await manager.start_strategy(run_id):
      replay = await self.get(run_id)
      raise ValueError(
        str((replay or {}).get("error_message") or "卖出计划历史回放启动失败")
      )
    replay = await self.get(run_id)
    if replay is None:
      raise ValueError("卖出计划历史回放启动后无法读取")
    return replay

  async def cancel(self, run_id: str) -> Dict[str, Any]:
    run, backtest = await self._load_run_and_backtest(run_id)
    if run is None or not self._mapping(run.parameters).get("exit_plan_replay"):
      raise ValueError("卖出计划历史回放不存在")
    projection = await exit_plan_replay_projection_service.get(run_id)
    status = str((projection or {}).get("status") or "").upper()
    if status not in _CANCELLABLE_STATUSES:
      if status in TERMINAL_EXIT_PLAN_REPLAY_STATUSES:
        raise ValueError(f"卖出计划历史回放已处于终态 {status}")
      raise ValueError("卖出计划历史回放缺少可取消状态")
    manager = self._require_runtime_manager()
    runtime = manager.get_run(run_id)
    await manager.cancel_deferred_start(run_id)
    if not await manager.stop_strategy(run_id, force=True):
      raise ValueError("取消卖出计划历史回放失败")
    if backtest is not None:
      async for db in get_async_db():
        await BacktestRepository(db).update_backtest_status(
          backtest_id=backtest.id,
          status="CANCELLED",
          metrics=runtime.get_metrics() if runtime else {},
          end_time=time_utils.now(),
        )
        break
    params = self._mapping(run.parameters)
    await exit_plan_replay_projection_service.update(
      run_id=run_id,
      account_id=str(params.get("account_id") or ""),
      status="CANCELLED",
      processed_until=(runtime.context.current_time if runtime else None),
      kind=ExitPlanReplayUpdateKind.RESULT_READY,
    )
    replay = await self.get(run_id)
    if replay is None:
      raise ValueError("取消后无法读取卖出计划回放")
    return replay

  async def get(self, run_id: str) -> Optional[Dict[str, Any]]:
    run, backtest = await self._load_run_and_backtest(run_id)
    if run is None or not self._mapping(run.parameters).get("exit_plan_replay"):
      return None
    projection = await exit_plan_replay_projection_service.get(run_id)
    return self._project(run, backtest, projection) if projection else None

  async def history(self, account_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    projections = await exit_plan_replay_projection_service.list_by_account(
      account_id, limit
    )
    run_ids = [str(item["run_id"]) for item in projections]
    if not run_ids:
      return []
    async for db in get_async_db():
      runs = await StrategyRunRepository(db).find_runs_by_ids(run_ids)
      runs_by_id = {row.id: row for row in runs}
      backtests = await BacktestRepository(db).get_latest_backtests_by_runs(run_ids)
      return [
        self._project(runs_by_id[run_id], backtests.get(run_id), projection)
        for run_id, projection in zip(run_ids, projections)
        if run_id in runs_by_id
      ]
    return []

  async def events(self, run_id: str, offset: int, limit: int) -> Dict[str, Any]:
    replay = await self.get(run_id)
    if replay is None:
      raise ValueError("卖出计划历史回放不存在")
    items = list(replay.get("events") or [])
    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or 50), 200))
    return {
      "run_id": run_id,
      "total": len(items),
      "offset": offset,
      "limit": limit,
      "has_more": offset + limit < len(items),
      "items": items[offset : offset + limit],
    }

  async def _resolve_template(
    self, payload: Dict[str, Any], *, account_id: str
  ) -> Tuple[ExitPlanTemplate, Optional[AutoExitPlanRecord]]:
    plan_id = str(payload.get("plan_id") or "").strip()
    draft = payload.get("draft_template")
    if bool(plan_id) == bool(draft):
      raise ValueError("必须且只能选择已保存计划或未保存草稿之一")
    if plan_id:
      async for db in get_async_db():
        record = await AutoExitPlanRepository(db).find_by_id(plan_id)
        if record is None or record.account_id != account_id:
          raise ValueError("退出计划不存在或不属于当前账户")
        state = self._mapping(record.plan_state)
        template = ExitPlanTemplate.from_dict(state.get("template") or {})
        return template, record
    raw = dict(draft or {})
    raw.setdefault("plan_id", f"draft-{uuid.uuid4()}")
    raw["account_id"] = account_id
    raw.setdefault("source_type", "MANUAL_EXIT_PLAN")
    raw.setdefault("source_id", "DRAFT")
    raw.setdefault("bucket", "core")
    return ExitPlanTemplate.from_dict(raw), None

  async def _resolve_origin(
    self,
    payload: Dict[str, Any],
    template: ExitPlanTemplate,
    *,
    account_id: str,
  ) -> Dict[str, Any]:
    raw = dict(payload.get("origin") or {})
    mode = str(raw.get("mode") or "").upper()
    if mode == "BUY_FILLS":
      order_ids = [str(item).strip() for item in list(raw.get("order_ids") or [])]
      if not order_ids:
        cost_basis = dict(template.metadata.get("cost_basis") or {})
        order_ids = [
          str(item.get("order_id") or "")
          for item in list(cost_basis.get("selected_orders") or [])
          if item.get("order_id")
        ]
      rows = await self._load_buy_orders(
        account_id=account_id,
        instrument_code=template.instrument_code,
        order_ids=order_ids,
        limit=max(1, len(order_ids)),
      )
      if len(rows) != len(set(order_ids)):
        raise ValueError("所选买入成交已变化或不属于当前账户与证券")
      basis_volume = sum(int(item["traded_volume"]) for item in rows)
      requested_volume = int(raw.get("volume") or basis_volume)
      if requested_volume <= 0 or requested_volume > basis_volume:
        raise ValueError("回放数量必须大于 0 且不能超过所选买入成交数量")
      total_cost = sum(
        float(item["traded_price"]) * int(item["traded_volume"])
        + float(item["estimated_buy_fee_cny"])
        for item in rows
      )
      return {
        "mode": mode,
        "volume": requested_volume,
        "unit_cost": total_cost / basis_volume,
        "activation_time": max(item["order_time"] for item in rows),
        "order_ids": order_ids,
        "fills": rows,
      }
    if mode == "MANUAL_SNAPSHOT":
      activation_time = self._naive(raw.get("activation_time"))
      volume = int(raw.get("volume") or 0)
      unit_cost = float(raw.get("unit_cost") or 0.0)
      if volume <= 0 or unit_cost <= 0 or not isfinite(unit_cost):
        raise ValueError("手工历史快照必须提供正数数量与每股全成本")
      return {
        "mode": mode,
        "volume": volume,
        "unit_cost": unit_cost,
        "activation_time": activation_time,
        "order_ids": [],
        "fills": [],
      }
    raise ValueError("回放起点必须选择真实买入成交或手工历史快照")

  async def _load_buy_orders(
    self,
    *,
    account_id: str,
    instrument_code: str,
    order_ids: Optional[List[str]],
    limit: int,
  ) -> List[Dict[str, Any]]:
    code = str(instrument_code or "").strip().upper()
    async for db in get_async_db():
      stmt = (
        select(Order)
        .where(Order.account_id == account_id)
        .where(Order.stock_code == code)
        .where(Order.type == OrderType.BUY)
        .where(Order.traded_volume > 0)
        .where(Order.traded_price > 0)
      )
      if order_ids is not None:
        parsed_ids = [int(item) for item in order_ids if str(item).isdigit()]
        if len(parsed_ids) != len(order_ids):
          raise ValueError("买入委托编号格式无效")
        stmt = stmt.where(Order.id.in_(parsed_ids))
      result = await db.execute(
        stmt.order_by(Order.time.desc(), Order.id.desc()).limit(
          max(1, min(int(limit or 100), 200))
        )
      )
      return [
        {
          "order_id": str(row.id),
          "traded_volume": int(row.traded_volume or 0),
          "traded_price": float(row.traded_price or 0.0),
          "estimated_buy_fee_cny": estimate_buy_fee_cny(
            price=float(row.traded_price or 0.0),
            volume=int(row.traded_volume or 0),
          ),
          "order_time": row.time,
          "strategy_name": row.strategy_name,
          "remark": row.remark,
        }
        for row in result.scalars().all()
      ]
    return []

  async def _load_actual_sell_references(
    self,
    *,
    account_id: str,
    instrument_code: str,
    start_time: datetime,
    end_time: datetime,
  ) -> List[Dict[str, Any]]:
    async for db in get_async_db():
      result = await db.execute(
        select(Order)
        .where(Order.account_id == account_id)
        .where(Order.stock_code == instrument_code)
        .where(Order.type == OrderType.SELL)
        .where(Order.traded_volume > 0)
        .where(Order.time >= start_time)
        .where(Order.time <= end_time)
        .order_by(Order.time.asc(), Order.id.asc())
        .limit(100)
      )
      return [
        {
          "order_id": str(row.id),
          "timestamp": row.time.isoformat(),
          "volume": int(row.traded_volume or 0),
          "price": float(row.traded_price or 0.0),
        }
        for row in result.scalars().all()
      ]
    return []

  async def _get_strategy_template_id(self) -> int:
    async for db in get_async_db():
      strategy = await StrategyRepository(db).find_by_class_name(
        _STRATEGY_CLASS_NAME
      )
      if strategy is None:
        raise ValueError("卖出托管策略尚未注册，请重启 Engine 后重试")
      return int(strategy.id)
    raise ValueError("无法读取卖出托管策略")

  async def _load_run_and_backtest(self, run_id: str) -> Tuple[Any, Any]:
    async for db in get_async_db():
      run = await StrategyRunRepository(db).find_run_by_id(run_id)
      rows = await BacktestRepository(db).get_backtests_by_run(run_id)
      return run, rows[0] if rows else None
    return None, None

  def _project(
    self, run: Any, backtest: Any, projection: Dict[str, Any]
  ) -> Dict[str, Any]:
    params = self._mapping(run.parameters)
    metrics = self._mapping(
      getattr(backtest, "metrics", None) or getattr(run, "metrics", None)
    )
    replay_metrics = self._mapping(metrics.get("exit_plan_replay"))
    status = str(projection.get("status") or "PENDING").upper()
    error_message = getattr(backtest, "error_message", None) or run.error_message
    return {
      "run_id": run.id,
      "backtest_id": backtest.id if backtest else None,
      "account_id": str(projection["account_id"]),
      "plan_id": projection.get("plan_id"),
      "config_version": int(params.get("exit_plan_replay_config_version") or 0),
      "instrument_code": str(projection["instrument_code"]),
      "status": status,
      "progress_pct": 100.0
      if status == "COMPLETED"
      else float(projection.get("progress_pct") or 0.0),
      "revision": str(projection.get("revision") or "0"),
      "processed_until": projection.get("processed_until"),
      "start_time": self._naive(params.get("replay_start_time")),
      "end_time": self._naive(params.get("replay_end_time")),
      "created_at": run.created_at,
      "updated_at": projection.get("updated_at"),
      "error_message": error_message,
      "data_quality": str(
        replay_metrics.get("data_quality") or ("ERROR" if error_message else "RUNNING")
      ),
      "data_quality_message": str(
        replay_metrics.get("data_quality_message") or error_message or "回放正在执行"
      ),
      "plan_snapshot": replay_metrics.get("plan_snapshot")
      or params.get("exit_plan_replay_template"),
      "origin": replay_metrics.get("origin") or params.get("exit_plan_replay_origin"),
      "summary": replay_metrics.get("summary"),
      "curve": list(replay_metrics.get("curve") or []),
      "events": list(replay_metrics.get("events") or []),
      "post_exit_horizons": list(replay_metrics.get("post_exit_horizons") or []),
      "actual_sell_references": list(
        replay_metrics.get("actual_sell_references")
        or params.get("actual_sell_references")
        or []
      ),
      "report": replay_metrics.get("report"),
    }

  @staticmethod
  def _serialize_origin(origin: Dict[str, Any]) -> Dict[str, Any]:
    return {
      **origin,
      "activation_time": origin["activation_time"].isoformat(),
      "fills": [
        {
          **item,
          "order_time": item["order_time"].isoformat(),
        }
        for item in list(origin.get("fills") or [])
      ],
    }

  @staticmethod
  def _naive(value: Any) -> datetime:
    if isinstance(value, datetime):
      return time_utils.to_shanghai(value) if value.tzinfo else value
    if isinstance(value, str) and value:
      parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
      return time_utils.to_shanghai(parsed) if parsed.tzinfo else parsed
    raise ValueError("回放时间格式无效")

  @staticmethod
  def _request_backtest_id(request_id: str) -> str:
    try:
      namespace = uuid.UUID(str(request_id))
    except ValueError:
      namespace = uuid.uuid5(uuid.NAMESPACE_URL, str(request_id))
    return str(uuid.uuid5(namespace, "exit-plan-replay-backtest-v1"))

  @staticmethod
  def _mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
      return dict(value)
    if isinstance(value, str) and value.strip():
      try:
        parsed = json.loads(value)
      except (TypeError, ValueError):
        return {}
      return dict(parsed) if isinstance(parsed, dict) else {}
    return {}
