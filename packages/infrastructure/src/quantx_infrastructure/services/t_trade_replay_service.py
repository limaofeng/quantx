"""Application service for T-assistant historical replay runs."""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime
from math import isfinite
from typing import Any, Dict, List, Optional, Tuple

from quantx_domain.strategies.ashare_intraday_t_assistant import (
  AshareIntradayTAssistantStrategy,
)

from quantx_infrastructure.core.t_trade_replay_metrics import (
  build_t_trade_replay_metrics,
)
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.enums import StrategyRunMode
from quantx_infrastructure.repositories.backtest_repository import BacktestRepository
from quantx_infrastructure.repositories.daily_asset_snapshot_repository import (
  DailyAssetPositionSnapshotRepository,
  DailyAssetSnapshotRepository,
)
from quantx_infrastructure.repositories.instrument_repository import (
  InstrumentRepository,
)
from quantx_infrastructure.repositories.strategy_run_repository import (
  StrategyRunRepository,
)
from quantx_infrastructure.services.t_trade_replay_projection_service import (
  TERMINAL_REPLAY_STATUSES,
  TTradeReplayUpdateKind,
  t_trade_replay_projection_service,
)
from quantx_infrastructure.services.t_trade_service import (
  TTradeService,
)
from quantx_infrastructure.services.trading_time_service import TradingDateHelper

_CANCELLABLE_REPLAY_STATUSES = frozenset({"PENDING", "RUNNING", "PAUSED"})


class TTradeReplayService:
  """Create, cancel, and project isolated BACKTEST runs for the T assistant."""

  def __init__(self, runtime_manager: Any = None) -> None:
    self._runtime_manager = runtime_manager
    self.t_trade_service = TTradeService(runtime_manager)

  def _require_runtime_manager(self) -> Any:
    if self._runtime_manager is None:
      raise RuntimeError("该操作只能由 QuantX Engine 执行")
    return self._runtime_manager

  async def prepare(self, account_id: str, start_time: datetime) -> Dict[str, Any]:
    account_id = str(account_id or "").strip()
    if not account_id:
      raise ValueError("账户不能为空")
    start_time = self._naive(start_time)
    snapshot, positions = await self._load_snapshot_portfolio(account_id, start_time)
    requires_manual = snapshot is None or not positions
    reconciliation = (
      self._build_initial_asset_reconciliation(
        cash=float(snapshot.cash_available_cny or 0.0),
        total_asset=float(snapshot.total_asset_cny or 0.0),
        positions=positions,
        snapshot=snapshot,
      )
      if snapshot is not None and positions
      else None
    )
    message = (
      "已采用回放开始日前最近一个账户日结快照"
      if not requires_manual
      else "开始日前没有完整账户快照，无法启动正式历史回放；请先导入开始日前的账户快照"
    )
    if reconciliation is not None:
      residual = float(reconciliation["non_trading_asset"])
      raw_residual = float(reconciliation["raw_residual"])
      if raw_residual < -0.01:
        message += "；快照分项超过总资产，回放将按已知分项计初始权益并标注数据质量"
      elif residual > 0.01:
        message += f"；其中 {residual:.2f} 元将作为恒定非交易资产计入权益"
    return {
      "account_id": account_id,
      "start_time": start_time,
      "snapshot_id": snapshot.id if snapshot else None,
      "snapshot_date": snapshot.trade_date.isoformat() if snapshot else None,
      "snapshot_source": snapshot.source if snapshot else None,
      "initial_cash": float(snapshot.cash_available_cny or 0.0) if snapshot else 0.0,
      "initial_total_asset": float(snapshot.total_asset_cny or 0.0)
      if snapshot
      else 0.0,
      "requires_manual_portfolio": requires_manual,
      "message": message,
      "positions": positions,
    }

  async def start(
    self,
    payload: Dict[str, Any],
    *,
    defer_start: bool = False,
    request_id: Optional[str] = None,
  ) -> Dict[str, Any]:
    account_id = str(payload.get("account_id", "") or "").strip()
    if not account_id:
      raise ValueError("账户不能为空")
    normalized_request_id = str(request_id or "").strip()
    if normalized_request_id:
      existing_run, existing_backtest = await self._load_run_and_backtest(
        normalized_request_id
      )
      if existing_run is not None:
        existing_parameters = self._mapping(existing_run.parameters)
        if not existing_parameters.get("t_trade_replay"):
          raise ValueError("回放请求幂等键已被其他策略运行占用")
        if str(existing_parameters.get("account_id") or "") != account_id:
          raise ValueError("回放请求幂等键不属于指定账户")
        if existing_backtest is None:
          async for db in get_async_db():
            existing_backtest = await BacktestRepository(db).create_backtest(
              backtest_id=self._request_backtest_id(normalized_request_id),
              strategy_run_id=normalized_request_id,
              parameters=existing_parameters,
              instruments=list(existing_run.instruments or []),
              backtest_start_time=self._naive(
                existing_parameters.get("replay_start_time")
              ),
              backtest_end_time=self._naive(
                existing_parameters.get("replay_end_time")
              ),
            )
            break
          strategy_manager = self._require_runtime_manager()
          runtime = strategy_manager.get_run(normalized_request_id)
          if runtime is not None and existing_backtest is not None:
            runtime.context.backtest_id = existing_backtest.id
            runtime.context.backtest_version = int(existing_backtest.version or 0) or None
        projection = await t_trade_replay_projection_service.get(
          normalized_request_id
        )
        if projection is None:
          projection = await t_trade_replay_projection_service.create(
            run_id=normalized_request_id,
            account_id=account_id,
          )
        replay = self._project(existing_run, existing_backtest, projection)
        if defer_start and replay["status"] in {"PENDING", "RUNNING", "PAUSED"}:
          strategy_manager = self._require_runtime_manager()
          if not await strategy_manager.defer_start_strategy(normalized_request_id):
            await strategy_manager.converge_deferred_start_error(
              normalized_request_id,
              "做 T 历史回放恢复后台启动失败",
            )
            raise ValueError("做 T 历史回放恢复后台启动失败")
        return replay
    start_time = self._naive(payload.get("start_time"))
    end_time = self._naive(payload.get("end_time"))
    if end_time <= start_time:
      raise ValueError("回放结束时间必须晚于开始时间")
    trading_dates = await TradingDateHelper().get_trading_calendar(
      market="SH",
      start_date=start_time.date(),
      end_date=end_time.date(),
    )
    if not trading_dates:
      raise ValueError("回放区间内没有交易日")
    if len(trading_dates) > 20:
      raise ValueError("单次回放最多支持 20 个交易日")
    if await self._has_active_replay(account_id):
      raise ValueError("该账户已有正在执行的做 T 回放，请等待完成或先取消")

    manual_positions = self._normalize_input_positions(
      payload.get("initial_positions") or []
    )
    manual_as_of = None
    if manual_positions:
      raw_manual_as_of = payload.get("initial_portfolio_as_of")
      if raw_manual_as_of is None:
        raise ValueError("手工历史组合必须提供可审计的组合时点")
      manual_as_of = self._naive(raw_manual_as_of)
      if manual_as_of >= start_time:
        raise ValueError("手工历史组合时点必须早于回放开始时间，禁止使用未来账户数据")
    snapshot = None
    positions = manual_positions
    if not positions:
      snapshot, positions = await self._load_snapshot_portfolio(account_id, start_time)
      if snapshot is None or not positions:
        raise ValueError("开始日前没有完整账户快照，无法启动正式历史回放")

    initial_cash = self._optional_number(payload.get("initial_cash"))
    initial_total_asset = self._optional_number(payload.get("initial_total_asset"))
    if snapshot is not None:
      initial_cash = float(snapshot.cash_available_cny or 0.0)
      initial_total_asset = float(snapshot.total_asset_cny or 0.0)
    if initial_cash is None or initial_total_asset is None:
      raise ValueError("手工初始组合必须同时提供可用资金与总资产")
    if not isfinite(initial_cash) or not isfinite(initial_total_asset):
      raise ValueError("初始资产必须是有限数字")
    if initial_cash < 0 or initial_total_asset <= 0:
      raise ValueError("初始可用资金不能为负且总资产必须大于 0")
    initial_asset_reconciliation = self._build_initial_asset_reconciliation(
      cash=initial_cash,
      total_asset=initial_total_asset,
      positions=positions,
      snapshot=snapshot,
      manual_as_of=manual_as_of,
    )

    settings = self.t_trade_service.build_parameters(payload)
    self.t_trade_service._validate_parameters(payload, StrategyRunMode.BACKTEST)
    instrument_references = await self._load_instrument_references(
      [str(position["stock_code"]) for position in positions]
    )
    metadata: Dict[str, Dict[str, Any]] = {}
    skipped: List[Dict[str, str]] = []
    instruments: List[str] = []
    for position in positions:
      code = position["stock_code"]
      reference = instrument_references.get(code)
      instrument_name = str(
        position.get("instrument_name")
        or getattr(reference, "name", "")
        or ""
      )
      volume = int(position["volume"])
      available = min(volume, int(position["available_volume"]))
      lifecycle_complete = bool(
        reference is not None
        and reference.open_date is not None
        and reference.expire_date is not None
        and instrument_name
      )
      eligible = volume >= 100 and available >= 100 and lifecycle_complete
      if volume < 100 or available < 100:
        reason = "昨日可用库存不足一手（100 股）"
      elif not lifecycle_complete:
        reason = "证券主数据不完整，无法确认历史涨跌停规则"
      else:
        reason = ""
      metadata[code] = {
        "instrument_name": instrument_name,
        "instrument_status_as_of": (
          snapshot.trade_date.isoformat() if snapshot is not None else None
        ),
        "listing_date": (
          reference.open_date.isoformat()
          if reference is not None and reference.open_date is not None
          else None
        ),
        "expiry_date": (
          reference.expire_date.isoformat()
          if reference is not None and reference.expire_date is not None
          else None
        ),
        "price_limit_reference_source": (
          "INSTRUMENT_MASTER" if reference is not None else "MISSING"
        ),
        "eligible": eligible,
        "reason": reason,
        "position_shares": volume,
        "position_available_shares": available,
        "position_frozen_shares": int(position.get("frozen_volume", 0) or 0),
        "position_avg_price": float(position["avg_price"]),
        "position_market_value": float(position["market_value"]),
      }
      if eligible:
        instruments.append(code)
      else:
        skipped.append(
          {
            "stock_code": code,
            "instrument_name": instrument_name,
            "reason": reason,
          }
        )
    if not instruments:
      raise ValueError("初始持仓中没有满足 100 股交易单位的可回放标的")

    parameters = {
      **settings,
      "t_trade_replay": True,
      "auto_approve_manual_intents": True,
      "account_id": account_id,
      "initial_capital": initial_asset_reconciliation["effective_initial_equity"],
      "initial_cash": initial_cash,
      "initial_total_asset": initial_total_asset,
      "initial_positions": positions,
      "initial_portfolio_as_of": manual_as_of.isoformat() if manual_as_of else None,
      "initial_asset_reconciliation": initial_asset_reconciliation,
      "initial_portfolio_metadata": metadata,
      "initial_instrument_metadata": {code: metadata[code] for code in instruments},
      "replay_skipped_instruments": skipped,
      "replay_price_limit_policy": {
        "schema_version": 1,
        "source_priority": [
          "HISTORICAL_TICK_NATIVE_LIMITS",
          "PREVIOUS_CLOSE_EXCHANGE_RULES",
        ],
        "instrument_reference": "INSTRUMENT_MASTER_AT_REPLAY_CREATION",
        "ambiguous_action": "STRICT_RISK_REJECT",
      },
      "replay_price_limit_source_counts": {},
      "replay_snapshot_id": snapshot.id if snapshot else None,
      "replay_snapshot_date": (snapshot.trade_date.isoformat() if snapshot else None),
      "replay_start_time": start_time.isoformat(),
      "replay_end_time": end_time.isoformat(),
      "commission_rate": float(payload.get("commission_rate", 0.0003) or 0.0),
      "minimum_commission": float(payload.get("minimum_commission", 5.0) or 0.0),
      "stamp_tax_rate": float(payload.get("stamp_tax_rate", 0.0005) or 0.0),
      "transfer_fee_rate": float(payload.get("transfer_fee_rate", 0.00001) or 0.0),
      "slippage_rate": float(payload.get("slippage_rate", 0.0001) or 0.0),
    }
    strategy_id = await self.t_trade_service._get_strategy_template_id()
    strategy_manager = self._require_runtime_manager()
    run_id = await strategy_manager.run_strategy(
      strategy_id=strategy_id,
      strategy_class=AshareIntradayTAssistantStrategy,
      mode=StrategyRunMode.BACKTEST,
      instruments=sorted(instruments),
      parameters=parameters,
      name=f"做T历史回放-{account_id}-{start_time:%Y%m%d}",
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
    await t_trade_replay_projection_service.create(
      run_id=run_id,
      account_id=account_id,
    )
    if defer_start:
      if not await strategy_manager.defer_start_strategy(run_id):
        await strategy_manager.converge_deferred_start_error(
          run_id,
          "做 T 历史回放后台启动调度失败",
        )
        raise ValueError("做 T 历史回放后台启动调度失败")
      replay = await self.get(run_id)
      if replay is None:
        raise ValueError("做 T 历史回放创建后无法读取")
      return replay

    return await self._start_prepared_replay(run_id)

  async def _start_prepared_replay(self, run_id: str) -> Dict[str, Any]:
    strategy_manager = self._require_runtime_manager()
    if not await strategy_manager.start_strategy(run_id):
      replay = await self.get(run_id)
      raise ValueError(
        replay.get("error_message") if replay else "做 T 历史回放启动失败"
      )
    replay = await self.get(run_id)
    if replay is None:
      raise ValueError("做 T 历史回放启动后无法读取")
    return replay

  async def cancel(self, run_id: str) -> Dict[str, Any]:
    run, backtest = await self._load_run_and_backtest(run_id)
    if run is None or not self._mapping(run.parameters).get("t_trade_replay"):
      raise ValueError("做 T 历史回放不存在")
    projection = await t_trade_replay_projection_service.get(run_id)
    replay_status = str((projection or {}).get("status") or "").upper()
    if replay_status not in _CANCELLABLE_REPLAY_STATUSES:
      if replay_status in TERMINAL_REPLAY_STATUSES:
        raise ValueError(f"做 T 历史回放已处于终态 {replay_status}，不能取消")
      if not replay_status:
        raise ValueError("做 T 历史回放缺少状态投影，不能安全取消")
      raise ValueError(f"做 T 历史回放当前状态 {replay_status} 不允许取消")
    strategy_manager = self._require_runtime_manager()
    runtime = strategy_manager.get_run(run_id)
    await strategy_manager.cancel_deferred_start(run_id)
    metrics = self._mapping(run.metrics)
    if runtime:
      metrics = runtime.get_metrics()
      metrics["t_trade_replay"] = build_t_trade_replay_metrics(runtime)
    # This is an isolated BACKTEST runtime. User cancellation must be able to
    # discard replay-only exit plans instead of leaving the simulation running.
    success = await strategy_manager.stop_strategy(run_id, force=True)
    if not success:
      raise ValueError("取消做 T 历史回放失败")
    if backtest:
      async for db in get_async_db():
        await BacktestRepository(db).update_backtest_status(
          backtest_id=backtest.id,
          status="CANCELLED",
          metrics=metrics,
          end_time=time_utils.now(),
        )
        break
    params = self._mapping(run.parameters)
    await t_trade_replay_projection_service.update(
      run_id=run_id,
      account_id=str(params.get("account_id") or ""),
      status="CANCELLED",
      progress_pct=(await t_trade_replay_projection_service.get(run_id) or {}).get(
        "progress_pct"
      ),
      processed_until=(
        self._naive(runtime.context.current_time)
        if runtime and runtime.context.current_time
        else None
      ),
      kind=TTradeReplayUpdateKind.RESULT_READY,
    )
    return await self.get(run_id)

  async def get(self, run_id: str) -> Optional[Dict[str, Any]]:
    run, backtest = await self._load_run_and_backtest(run_id)
    if run is None or not self._mapping(run.parameters).get("t_trade_replay"):
      return None
    projection = await t_trade_replay_projection_service.get(run_id)
    if projection is None:
      return None
    return self._project(run, backtest, projection)

  async def history(self, account_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit or 20), 100))
    projections = await t_trade_replay_projection_service.list_by_account(
      account_id,
      limit,
    )
    run_ids = [str(item["run_id"]) for item in projections]
    if not run_ids:
      return []
    async for db in get_async_db():
      runs = await StrategyRunRepository(db).find_runs_by_ids(run_ids)
      runs_by_id = {run.id: run for run in runs}
      backtests = await BacktestRepository(db).get_latest_backtests_by_runs(run_ids)
      return [
        self._project(runs_by_id[run_id], backtests.get(run_id), projection)
        for run_id, projection in zip(run_ids, projections)
        if run_id in runs_by_id
      ]
    return []

  async def cycles(
    self, run_id: str, offset: int = 0, limit: int = 50
  ) -> Dict[str, Any]:
    replay = await self.get(run_id)
    if replay is None:
      raise ValueError("做 T 历史回放不存在")
    run, backtest = await self._load_run_and_backtest(run_id)
    metrics = self._replay_metrics(run, backtest)
    items = list(metrics.get("cycles") or [])
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

  async def _has_active_replay(self, account_id: str) -> bool:
    return await t_trade_replay_projection_service.has_active(account_id)

  async def _load_snapshot_portfolio(
    self, account_id: str, start_time: datetime
  ) -> Tuple[Any, List[Dict[str, Any]]]:
    async for db in get_async_db():
      snapshot_repo = DailyAssetSnapshotRepository(db)
      position_repo = DailyAssetPositionSnapshotRepository(db)
      scope_key = snapshot_repo.scope_key("account", account_id)
      snapshot = await snapshot_repo.find_previous(scope_key, start_time.date())
      if snapshot is None:
        return None, []
      rows = await position_repo.find_by_snapshot(snapshot.id)
      return snapshot, self._aggregate_snapshot_positions(rows)
    return None, []

  async def _load_instrument_references(
    self,
    instrument_codes: List[str],
  ) -> Dict[str, Any]:
    codes = sorted({str(code or "").strip().upper() for code in instrument_codes})
    if not codes:
      return {}
    async for db in get_async_db():
      rows = await InstrumentRepository(db).find_by_ids(codes)
      return {str(row.id or "").upper(): row for row in rows}
    return {}

  @staticmethod
  def _aggregate_snapshot_positions(rows: List[Any]) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Any]] = defaultdict(list)
    for row in rows:
      if int(row.volume or 0) > 0:
        grouped[str(row.instrument_code or "").upper()].append(row)
    result = []
    for code, items in sorted(grouped.items()):
      volume = sum(int(item.volume or 0) for item in items)
      market_value = sum(float(item.market_value_cny or 0.0) for item in items)
      cost_value = sum(
        float(item.avg_price or 0.0) * int(item.volume or 0) for item in items
      )
      last_value = sum(
        float(item.last_price or 0.0) * int(item.volume or 0) for item in items
      )
      result.append(
        {
          "stock_code": code,
          "instrument_name": str(items[0].instrument_name or ""),
          "volume": volume,
          "available_volume": min(
            volume, sum(int(item.available_volume or 0) for item in items)
          ),
          "frozen_volume": sum(int(item.frozen_volume or 0) for item in items),
          "avg_price": cost_value / volume if volume else 0.0,
          "last_price": last_value / volume if volume else 0.0,
          "market_value": market_value,
        }
      )
    return result

  @staticmethod
  def _build_initial_asset_reconciliation(
    *,
    cash: float,
    total_asset: float,
    positions: List[Dict[str, Any]],
    snapshot: Any = None,
    manual_as_of: Optional[datetime] = None,
  ) -> Dict[str, Any]:
    available_cash = float(cash)
    reported_total_asset = float(total_asset)
    position_market_value = sum(
      max(0.0, float(position.get("market_value", 0.0) or 0.0))
      for position in positions
    )
    if not all(
      isfinite(value)
      for value in (available_cash, reported_total_asset, position_market_value)
    ):
      raise ValueError("初始资产与持仓市值必须是有限数字")

    component_total = available_cash + position_market_value
    raw_residual = reported_total_asset - component_total
    non_trading_asset = max(0.0, raw_residual)
    effective_initial_equity = component_total + non_trading_asset

    snapshot_metadata = dict(
      getattr(snapshot, "snapshot_metadata", {}) or {}
    )
    quality_flags = {
      str(flag)
      for flag in list(snapshot_metadata.get("quality_flags") or [])
      if str(flag)
    }
    snapshot_data_quality = str(
      getattr(snapshot, "data_quality", "") or ""
    ).upper()
    if snapshot_data_quality and snapshot_data_quality != "OK":
      quality_flags.add(snapshot_data_quality)
    if snapshot is None and manual_as_of is not None:
      quality_flags.add("MANUAL_HISTORICAL_PORTFOLIO")
    if raw_residual > 0.01:
      quality_flags.add("NON_TRADING_ASSET_RESIDUAL_PRESERVED")
    elif raw_residual < -0.01:
      quality_flags.add("INITIAL_COMPONENTS_EXCEED_REPORTED_TOTAL")

    snapshot_market_value = getattr(snapshot, "market_value_cny", None)
    return {
      "schema_version": 1,
      "reported_total_asset": reported_total_asset,
      "available_cash": available_cash,
      "position_market_value": position_market_value,
      "snapshot_market_value": (
        float(snapshot_market_value) if snapshot_market_value is not None else None
      ),
      "raw_residual": raw_residual,
      "non_trading_asset": non_trading_asset,
      "effective_initial_equity": effective_initial_equity,
      "negative_residual_clamped": raw_residual < 0.0,
      "policy": "PRESERVE_POSITIVE_RESIDUAL_CLAMP_NEGATIVE_TO_ZERO",
      "snapshot_data_quality": snapshot_data_quality or None,
      "portfolio_as_of": manual_as_of.isoformat() if manual_as_of else None,
      "quality_flags": sorted(quality_flags),
    }

  @staticmethod
  def _normalize_input_positions(items: List[Any]) -> List[Dict[str, Any]]:
    result = []
    for raw in items:
      data = vars(raw) if not isinstance(raw, dict) else dict(raw)
      code = str(data.get("stock_code", "") or "").strip().upper()
      volume = max(0, int(data.get("volume", 0) or 0))
      if not code or volume <= 0:
        continue
      last_price = max(0.0, float(data.get("last_price", 0.0) or 0.0))
      result.append(
        {
          "stock_code": code,
          "instrument_name": str(data.get("instrument_name", "") or ""),
          "volume": volume,
          "available_volume": min(
            volume, max(0, int(data.get("available_volume", 0) or 0))
          ),
          "frozen_volume": 0,
          "avg_price": max(0.0, float(data.get("avg_price", 0.0) or 0.0)),
          "last_price": last_price,
          "market_value": max(
            0.0,
            float(data.get("market_value", volume * last_price) or 0.0),
          ),
        }
      )
    return result

  async def _load_run_and_backtest(self, run_id: str) -> Tuple[Any, Any]:
    async for db in get_async_db():
      run = await StrategyRunRepository(db).find_run_by_id(run_id)
      backtests = await BacktestRepository(db).get_backtests_by_run(run_id)
      return run, backtests[0] if backtests else None
    return None, None

  def _replay_metrics(self, run: Any, backtest: Any) -> Dict[str, Any]:
    metrics = self._mapping(
      getattr(backtest, "metrics", None) or getattr(run, "metrics", None)
    )
    return self._mapping(metrics.get("t_trade_replay"))

  def _project(
    self,
    run: Any,
    backtest: Any,
    projection: Dict[str, Any],
  ) -> Dict[str, Any]:
    params = self._mapping(run.parameters)
    raw_status = str(projection["status"]).upper()
    start_time = self._naive(params.get("replay_start_time"))
    end_time = self._naive(params.get("replay_end_time"))
    current_time = projection.get("processed_until")
    progress = float(projection.get("progress_pct") or 0.0)
    if raw_status == "COMPLETED":
      progress = 100.0
    replay_metrics = self._replay_metrics(run, backtest)
    skipped = list(params.get("replay_skipped_instruments") or [])
    error_message = getattr(backtest, "error_message", None) or run.error_message
    return {
      "run_id": run.id,
      "backtest_id": backtest.id if backtest else None,
      "account_id": str(projection["account_id"]),
      "status": raw_status,
      "progress_pct": progress,
      "processed_until": current_time,
      "revision": str(projection["revision"]),
      "start_time": start_time,
      "end_time": end_time,
      "snapshot_id": params.get("replay_snapshot_id"),
      "snapshot_date": params.get("replay_snapshot_date"),
      "created_at": run.created_at,
      "updated_at": projection["updated_at"],
      "error_message": error_message,
      "data_quality": str(
        replay_metrics.get("data_quality") or ("ERROR" if error_message else "RUNNING")
      ),
      "data_quality_message": str(
        replay_metrics.get("data_quality_message") or error_message or "回放正在执行"
      ),
      "skipped_stock_codes": list(
        replay_metrics.get("skipped_stock_codes")
        or [str(item.get("stock_code", "") or "") for item in skipped]
      ),
      "summary": replay_metrics.get("summary"),
      "instruments": list(replay_metrics.get("instruments") or []),
      "curve": list(replay_metrics.get("curve") or []),
      "report": replay_metrics.get("report"),
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
    return str(uuid.uuid5(namespace, "t-trade-replay-backtest-v1"))

  @staticmethod
  def _optional_number(value: Any) -> Optional[float]:
    if value is None:
      return None
    try:
      return float(value)
    except (TypeError, ValueError) as exc:
      raise ValueError("初始资产必须是数字") from exc

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
