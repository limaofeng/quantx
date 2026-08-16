"""Application service for T-assistant historical replay runs."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
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
from quantx_infrastructure.repositories.strategy_run_repository import (
  StrategyRunRepository,
)
from quantx_infrastructure.services.t_trade_service import (
  ACTIVE_RUN_STATUSES,
  TTradeService,
)
from quantx_infrastructure.services.trading_time_service import TradingDateHelper


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
      "message": (
        "已采用回放开始日前最近一个账户日结快照"
        if not requires_manual
        else "开始日前没有完整账户快照，请提供手工初始资产与持仓"
      ),
      "positions": positions,
    }

  async def start(self, payload: Dict[str, Any]) -> Dict[str, Any]:
    account_id = str(payload.get("account_id", "") or "").strip()
    if not account_id:
      raise ValueError("账户不能为空")
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
    snapshot = None
    positions = manual_positions
    if not positions:
      snapshot, positions = await self._load_snapshot_portfolio(account_id, start_time)
      if snapshot is None or not positions:
        raise ValueError("开始日前没有完整账户快照，请提供手工初始资产与持仓")

    initial_cash = self._optional_number(payload.get("initial_cash"))
    initial_total_asset = self._optional_number(payload.get("initial_total_asset"))
    if snapshot is not None:
      initial_cash = float(snapshot.cash_available_cny or 0.0)
      initial_total_asset = float(snapshot.total_asset_cny or 0.0)
    if initial_cash is None or initial_total_asset is None:
      raise ValueError("手工初始组合必须同时提供可用资金与总资产")
    if initial_cash < 0 or initial_total_asset <= 0:
      raise ValueError("初始可用资金不能为负且总资产必须大于 0")

    settings = self.t_trade_service.build_parameters(payload)
    self.t_trade_service._validate_parameters(payload, StrategyRunMode.BACKTEST)
    metadata: Dict[str, Dict[str, Any]] = {}
    skipped: List[Dict[str, str]] = []
    instruments: List[str] = []
    for position in positions:
      code = position["stock_code"]
      volume = int(position["volume"])
      available = min(volume, int(position["available_volume"]))
      eligible = volume >= 100 and available >= 100
      reason = "" if eligible else "昨日可用库存不足一手（100 股）"
      metadata[code] = {
        "instrument_name": position["instrument_name"],
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
            "instrument_name": position["instrument_name"],
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
      "initial_capital": initial_total_asset,
      "initial_cash": initial_cash,
      "initial_total_asset": initial_total_asset,
      "initial_positions": positions,
      "initial_portfolio_metadata": metadata,
      "initial_instrument_metadata": {code: metadata[code] for code in instruments},
      "replay_skipped_instruments": skipped,
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
    )
    if not await strategy_manager.start_strategy(run_id):
      replay = await self.get(run_id)
      raise ValueError(
        replay.get("error_message") if replay else "做 T 历史回放启动失败"
      )
    return await self.get(run_id)

  async def cancel(self, run_id: str) -> Dict[str, Any]:
    run, backtest = await self._load_run_and_backtest(run_id)
    if run is None or not self._mapping(run.parameters).get("t_trade_replay"):
      raise ValueError("做 T 历史回放不存在")
    strategy_manager = self._require_runtime_manager()
    runtime = strategy_manager.get_run(run_id)
    metrics = self._mapping(run.metrics)
    if runtime:
      metrics = runtime.get_metrics()
      metrics["t_trade_replay"] = build_t_trade_replay_metrics(runtime)
    success = await strategy_manager.stop_strategy(run_id)
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
    return await self.get(run_id)

  async def get(self, run_id: str) -> Optional[Dict[str, Any]]:
    run, backtest = await self._load_run_and_backtest(run_id)
    if run is None or not self._mapping(run.parameters).get("t_trade_replay"):
      return None
    return self._project(run, backtest)

  async def history(self, account_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    limit = max(1, min(int(limit or 20), 100))
    async for db in get_async_db():
      runs = await StrategyRunRepository(db).find_all_strategy_runs()
      backtest_repo = BacktestRepository(db)
      selected = [
        run
        for run in runs
        if self._mapping(run.parameters).get("t_trade_replay")
        and str(self._mapping(run.parameters).get("account_id", "")) == account_id
      ]
      selected.sort(key=lambda run: run.created_at or datetime.min, reverse=True)
      result = []
      for run in selected[:limit]:
        backtests = await backtest_repo.get_backtests_by_run(run.id)
        result.append(self._project(run, backtests[0] if backtests else None))
      return result
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
    async for db in get_async_db():
      runs = await StrategyRunRepository(db).find_all_strategy_runs()
      return any(
        self._mapping(run.parameters).get("t_trade_replay")
        and str(self._mapping(run.parameters).get("account_id", "")) == account_id
        and str(getattr(run.status, "value", run.status) or "") in ACTIVE_RUN_STATUSES
        for run in runs
      )
    return False

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

  def _project(self, run: Any, backtest: Any) -> Dict[str, Any]:
    params = self._mapping(run.parameters)
    runtime_status = str(getattr(run.status, "value", run.status) or "").upper()
    backtest_status = str(getattr(backtest, "status", "") or "").upper()
    raw_status = (
      backtest_status
      if backtest_status in {"CANCELLED", "COMPLETED", "ERROR"}
      and runtime_status in {"", "COMPLETED", "ERROR", "STOPPED"}
      else runtime_status
      or backtest_status
      or str(getattr(run.status, "value", run.status) or "PENDING").upper()
    )
    start_time = self._naive(params.get("replay_start_time"))
    end_time = self._naive(params.get("replay_end_time"))
    current_time = None
    if raw_status == "COMPLETED":
      progress = 100.0
    elif current_time and end_time > start_time:
      progress = max(
        0.0,
        min(
          99.9,
          (current_time - start_time).total_seconds()
          / (end_time - start_time).total_seconds()
          * 100.0,
        ),
      )
    else:
      progress = 0.0
    replay_metrics = self._replay_metrics(run, backtest)
    skipped = list(params.get("replay_skipped_instruments") or [])
    error_message = getattr(backtest, "error_message", None) or run.error_message
    return {
      "run_id": run.id,
      "backtest_id": backtest.id if backtest else None,
      "account_id": str(params.get("account_id", "") or ""),
      "status": raw_status,
      "progress_pct": progress,
      "start_time": start_time,
      "end_time": end_time,
      "snapshot_id": params.get("replay_snapshot_id"),
      "snapshot_date": params.get("replay_snapshot_date"),
      "created_at": run.created_at,
      "updated_at": run.updated_at,
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
