"""日级技术指标快照服务。"""

import logging
from contextlib import aclosing
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd
from quantx_domain.indicators import INDICATOR_VERSION, calculate_indicator_frame

from quantx_infrastructure.database.relational_connection import get_async_db
from quantx_infrastructure.repositories.indicator_snapshot_repository import (
  IndicatorSnapshotRepository,
)
from quantx_infrastructure.repositories.kline_repository import KLineRepository
from quantx_infrastructure.services.snapshot_fencing import SnapshotFenceLost
from quantx_infrastructure.services.snapshot_inactive_evidence import (
  load_snapshot_inactive_empty_proofs,
)
from quantx_infrastructure.services.snapshot_price_history import (
  load_snapshot_price_history,
)
from quantx_infrastructure.services.trading_time_service import TradingDateHelper

logger = logging.getLogger(__name__)

_INFLUX_TIME_WINDOW_DAYS = 90


def _time_windows(
  start: datetime,
  end: datetime,
  *,
  days: int = _INFLUX_TIME_WINDOW_DAYS,
) -> Iterable[Tuple[datetime, datetime]]:
  """生成无重叠闭区间，规避 InfluxDB Core 单查询文件扫描上限。"""
  if days <= 0:
    raise ValueError("InfluxDB 查询时间窗口必须大于 0 天")
  if end < start:
    raise ValueError("K 线查询结束时间不能早于开始时间")

  cursor = start
  window = timedelta(days=days)
  while cursor <= end:
    window_end = min(cursor + window, end)
    yield cursor, window_end
    cursor = window_end + timedelta(microseconds=1)


def build_snapshot_record(
  code: str,
  instrument_type: str,
  name: str,
  snapshot_date: date,
  df,
  float_volume: Optional[float] = None,
  *,
  trading_dates=None,
) -> Optional[Dict[str, Any]]:
  """Build one versioned snapshot from validated point-in-time price history."""
  if df.empty:
    return None
  indicators = calculate_indicator_frame(df, trading_dates=trading_dates).iloc[-1]
  if pd.isna(indicators["current_price"]):
    return None
  current = df.iloc[-1]
  snap: Dict[str, Any] = {
    "code": code,
    "instrument_type": instrument_type,
    "name": name,
    "snapshot_date": snapshot_date,
    "calculation_version": INDICATOR_VERSION,
    "matched_signals": [],
  }
  valid_history = pd.Series(True, index=df.index)
  for column in ("open", "high", "low", "close"):
    values = pd.to_numeric(df.get(column), errors="coerce")
    valid_history &= values.notna() & values.gt(0)
  volume_history = pd.to_numeric(df.get("volume"), errors="coerce")
  valid_history &= volume_history.notna() & volume_history.gt(0)
  if "suspend_flag" in df:
    suspended = pd.to_numeric(df["suspend_flag"], errors="coerce").fillna(0)
    valid_history &= suspended.ne(1)
  snap["valid_history_count"] = int(valid_history.sum())
  for key, value in indicators.items():
    snap[key] = None if pd.isna(value) else float(value)
  for source, target in (
    ("open", "open_price"),
    ("high", "high_price"),
    ("low", "low_price_day"),
  ):
    value = current.get(f"raw_{source}", current.get(source))
    snap[target] = None if value is None or pd.isna(value) else float(value)
  for key in ("volume", "amount"):
    value = current.get(key)
    snap[key] = None if value is None or pd.isna(value) else float(value)
  for key in ("days_since_peak", "days_since_low", "consecutive_down_days"):
    if snap.get(key) is not None:
      snap[key] = int(snap[key])
  volume = snap.get("volume")
  snap["turnover_rate_pct"] = (
    round(volume * 100 / float_volume * 100, 8)
    if volume is not None and float_volume is not None and float_volume > 0
    else None
  )
  return snap


def _bar_is_inactive(bar: pd.Series) -> bool:
  volume = pd.to_numeric(bar.get("volume"), errors="coerce")
  suspended = pd.to_numeric(bar.get("suspend_flag", 0), errors="coerce")
  return (pd.notna(volume) and volume == 0) or (pd.notna(suspended) and suspended == 1)


class DailyIndicatorSnapshotService:
  """读取日线、计算指标并写入日级快照。"""

  def __init__(
    self,
    kline_repo_factory=KLineRepository,
    db_factory=get_async_db,
    snapshot_repo_cls=IndicatorSnapshotRepository,
    logger_=None,
    price_history_loader=None,
    inactive_empty_proof_loader=None,
    trading_dates=None,
  ):
    self.kline_repo_factory = kline_repo_factory
    self.db_factory = db_factory
    self.snapshot_repo_cls = snapshot_repo_cls
    self.logger = logger_ or logger
    self.price_history_loader = price_history_loader
    self.inactive_empty_proof_loader = inactive_empty_proof_loader
    self.trading_dates = trading_dates or TradingDateHelper()

  def _load_daily_batch(
    self,
    *,
    codes: List[str],
    start: datetime,
    end: datetime,
  ) -> Dict[str, pd.DataFrame]:
    """按时间窗读取并合并一批日线，避免单查询扫描过多 Parquet 文件。"""
    repository = self.kline_repo_factory()
    parts: Dict[str, List[pd.DataFrame]] = {}
    for window_start, window_end in _time_windows(start, end):
      window_data = repository.find_daily_batch(
        stock_codes=codes,
        start=window_start,
        end=window_end,
        use_cache=False,
      )
      if not isinstance(window_data, dict):
        raise RuntimeError(
          f"批量读取 1d K 线返回格式异常: {type(window_data).__name__}"
        )
      for code, frame in window_data.items():
        if frame is None or getattr(frame, "empty", True):
          continue
        parts.setdefault(str(code).upper(), []).append(frame)

    return {
      code: pd.concat(frames, ignore_index=True, sort=False)
      for code, frames in parts.items()
    }

  async def compute_and_save_batch(
    self,
    codes: List[str],
    snapshot_date: date,
    instrument_type_map: Dict[str, str],
    name_map: Dict[str, str],
    float_volume_map: Optional[Dict[str, float]] = None,
    lookback_days: int = 540,
    snapshot_run_id: int = 0,
    lock_backend_pid: int = 0,
  ) -> Dict[str, Any]:
    """计算并保存一批标的的单日日级技术指标快照。"""
    if snapshot_run_id <= 0:
      raise ValueError("日级快照写入必须绑定有效运行代次")
    if lock_backend_pid <= 0:
      raise ValueError("日级快照写入必须绑定数据库锁会话")
    result = await self.compute_and_save_dates_batch(
      codes=codes,
      snapshot_dates=[snapshot_date],
      instrument_type_map=instrument_type_map,
      name_map=name_map,
      float_volume_map=float_volume_map,
      lookback_days=lookback_days,
      snapshot_run_ids={snapshot_date: snapshot_run_id},
      lock_backend_pid=lock_backend_pid,
    )
    day_result = result["dates"].get(snapshot_date.isoformat(), {})
    return {
      "total": day_result.get("total", len(codes)),
      "saved": day_result.get("saved", 0),
      "skipped": day_result.get("skipped", 0),
      "failed": day_result.get("failed", 0),
      "errors": result["errors"],
      "missing_target": day_result.get("missing_target", 0),
      "inactive_target": day_result.get("inactive_target", 0),
      "insufficient_history": day_result.get("insufficient_history", 0),
      "systemic_failure": result["systemic_failure"],
    }

  async def compute_and_save_dates_batch(
    self,
    codes: List[str],
    snapshot_dates: List[date],
    instrument_type_map: Dict[str, str],
    name_map: Dict[str, str],
    float_volume_map: Optional[Dict[str, float]] = None,
    lookback_days: int = 540,
    codes_by_snapshot_date: Optional[Dict[date, List[str]]] = None,
    snapshot_run_ids: Optional[Dict[date, int]] = None,
    lock_backend_pid: int = 0,
  ) -> Dict[str, Any]:
    """分段读取公共历史区间并生成一个代码批次的多个目标日快照。"""
    dates = sorted(set(snapshot_dates))
    code_set = set(codes)
    scope_by_date = {
      target: (
        set(codes_by_snapshot_date.get(target, []))
        if codes_by_snapshot_date is not None
        else set(codes)
      )
      for target in dates
    }
    unknown_codes = set().union(*scope_by_date.values()) - code_set if dates else set()
    if unknown_codes:
      raise ValueError("目标日期代码范围必须是当前批次代码的子集")
    if snapshot_run_ids is None or set(snapshot_run_ids) != set(dates):
      raise ValueError("日级快照写入代次必须完整覆盖目标日期")
    if any(int(run_id) <= 0 for run_id in snapshot_run_ids.values()):
      raise ValueError("日级快照写入代次必须为正整数")
    if lock_backend_pid <= 0:
      raise ValueError("日级快照写入必须绑定数据库锁会话")
    result: Dict[str, Any] = {
      "total": sum(len(scope_by_date[target]) for target in dates),
      "saved": 0,
      "skipped": 0,
      "failed": 0,
      "errors": [],
      "systemic_failure": False,
      "dates": {
        target.isoformat(): {
          "total": len(scope_by_date[target]),
          "saved": 0,
          "skipped": 0,
          "failed": 0,
          "missing_target": 0,
          "inactive_target": 0,
          "insufficient_history": 0,
          "errors": [],
        }
        for target in dates
      },
    }
    if not codes or not dates:
      return result

    # A rerun can turn a formerly valid bar into missing/inactive/bad data.
    # Remove indicator eligibility for the complete requested batch before any
    # fallible work.  This deliberately includes codes that are inactive on a
    # target date, because an older run may have written them before lifecycle
    # metadata was known or corrected.  Only freshly successful, active records
    # below regain the current version; historical values are retained for audit.
    try:
      async with aclosing(self.db_factory()) as sessions:
        db = await anext(sessions, None)
        if db is None:
          raise RuntimeError("日级指标重算未取得数据库连接")
        repo = self.snapshot_repo_cls(db)
        await repo.invalidate_indicator_scope(
          codes,
          dates,
          snapshot_run_ids=snapshot_run_ids,
        )
    except SnapshotFenceLost:
      raise
    except Exception as e:
      msg = f"失效旧日级指标资格失败: {e}"
      self.logger.exception(msg)
      result["failed"] = result["total"]
      result["systemic_failure"] = True
      result["errors"].append(msg)
      for target in dates:
        day_result = result["dates"][target.isoformat()]
        day_result["failed"] = len(scope_by_date[target])
        # Invalidation also owns inactive stale rows, so every target date is
        # affected even when its active count for this batch is zero.
        day_result["errors"].append(msg)
      return result

    active_codes = [
      code for code in codes if any(code in scope_by_date[target] for target in dates)
    ]
    if not active_codes:
      return result

    try:
      history_start = datetime.combine(
        dates[0] - timedelta(days=lookback_days),
        time.min,
      )
      history_end = datetime.combine(dates[-1], time.max)
      market_data = self._load_daily_batch(
        codes=active_codes,
        start=history_start,
        end=history_end,
      )
      if isinstance(market_data, dict):
        if self.price_history_loader is not None:
          market_data = await self.price_history_loader(market_data)
        else:
          market_data = await load_snapshot_price_history(market_data, self.db_factory)
      market_calendar = await self.trading_dates.get_trading_calendar(
        "SH",
        start_date=history_start.date(),
        end_date=history_end.date(),
      )
      if not market_calendar:
        raise ValueError("日级指标计算缺少交易日历")
    except Exception as e:
      msg = f"准备或读取日级指标历史数据失败: {e}"
      self.logger.exception(msg)
      result["failed"] = result["total"]
      result["systemic_failure"] = True
      result["errors"].append(msg)
      for target in dates:
        if not scope_by_date[target]:
          continue
        day_result = result["dates"][target.isoformat()]
        day_result["failed"] = len(scope_by_date[target])
        day_result["errors"].append(msg)
      return result

    if not isinstance(market_data, dict):
      msg = f"批量读取 1d K 线返回格式异常: {type(market_data).__name__}"
      self.logger.error(msg)
      result["failed"] = result["total"]
      result["systemic_failure"] = True
      result["errors"].append(msg)
      for target in dates:
        if not scope_by_date[target]:
          continue
        day_result = result["dates"][target.isoformat()]
        day_result["failed"] = len(scope_by_date[target])
        day_result["errors"].append(msg)
      return result

    prepared_frames: Dict[str, pd.DataFrame] = {}
    frame_errors: Dict[str, str] = {}
    for code in active_codes:
      df = market_data.get(code)
      if df is None or getattr(df, "empty", True):
        continue
      try:
        frame = df.copy()
        if "time" not in frame.columns:
          raise ValueError("K 线缺少 time 字段")
        frame["time"] = pd.to_datetime(frame["time"], errors="coerce", utc=True)
        frame = frame.dropna(subset=["time"]).sort_values("time")
        frame["_trade_date"] = frame["time"].dt.tz_convert("Asia/Shanghai").dt.date
      except Exception as e:
        frame_errors[code] = f"{code} K 线格式异常: {e}"
        continue
      prepared_frames[code] = frame

    inactive_proof_candidates: set[tuple[str, date]] = set()
    for code, frame in prepared_frames.items():
      for target in dates:
        if code not in scope_by_date[target]:
          continue
        has_target = bool((frame["_trade_date"] == target).any())
        if has_target:
          continue
        prior = frame.loc[frame["_trade_date"] <= target]
        if not prior.empty and _bar_is_inactive(prior.iloc[-1]):
          inactive_proof_candidates.add((code, target))

    inactive_empty_proofs: set[tuple[str, date]] = set()
    if inactive_proof_candidates:
      try:
        if self.inactive_empty_proof_loader is not None:
          inactive_empty_proofs = await self.inactive_empty_proof_loader(
            inactive_proof_candidates
          )
        else:
          inactive_empty_proofs = await load_snapshot_inactive_empty_proofs(
            inactive_proof_candidates,
            self.db_factory,
          )
        inactive_empty_proofs &= inactive_proof_candidates
      except Exception as e:
        # Missing proof must stay missing. A proof-store outage cannot promote
        # a historical inactive row into target-day inactivity.
        self.logger.warning("日级快照停牌双零行证据不可用，按缺失处理: %s", e)

    records: List[Dict[str, Any]] = []
    for code in active_codes:
      code_dates = [target for target in dates if code in scope_by_date[target]]
      if not code_dates:
        continue
      if code in frame_errors:
        msg = frame_errors[code]
        result["errors"].append(msg)
        for target in code_dates:
          day_result = result["dates"][target.isoformat()]
          day_result["failed"] += 1
          day_result["errors"].append(msg)
        continue
      frame = prepared_frames.get(code)
      if frame is None:
        for target in code_dates:
          day_result = result["dates"][target.isoformat()]
          day_result["skipped"] += 1
          day_result["missing_target"] += 1
        continue

      for target in code_dates:
        day_result = result["dates"][target.isoformat()]
        target_frame = frame.loc[frame["_trade_date"] <= target].drop(
          columns=["_trade_date"]
        )
        if target_frame.empty:
          day_result["skipped"] += 1
          day_result["missing_target"] += 1
          continue
        current = target_frame.iloc[-1]
        has_target = bool((frame["_trade_date"] == target).any())
        if _bar_is_inactive(current):
          day_result["skipped"] += 1
          if has_target or (code, target) in inactive_empty_proofs:
            day_result["inactive_target"] += 1
          else:
            day_result["missing_target"] += 1
          continue
        if not has_target:
          day_result["skipped"] += 1
          day_result["missing_target"] += 1
          continue
        snap = build_snapshot_record(
          code=code,
          instrument_type=instrument_type_map.get(code, "stock"),
          name=name_map.get(code, ""),
          snapshot_date=target,
          df=target_frame,
          float_volume=(float_volume_map or {}).get(code),
          trading_dates=market_calendar,
        )
        if snap is None:
          day_result["failed"] += 1
          msg = f"{code} {target.isoformat()} 指标计算失败"
          result["errors"].append(msg)
          day_result["errors"].append(msg)
        else:
          records.append(snap)
          day_result["saved"] += 1

    if records:
      try:
        async with aclosing(self.db_factory()) as sessions:
          db = await anext(sessions, None)
          if db is None:
            raise RuntimeError("日级指标写入未取得数据库连接")
          repo = self.snapshot_repo_cls(db)
          await repo.bulk_upsert(
            records,
            snapshot_run_ids=snapshot_run_ids,
            lock_backend_pid=lock_backend_pid,
          )
      except SnapshotFenceLost:
        raise
      except Exception as e:
        msg = f"批量写入快照失败: {e}"
        self.logger.exception(msg)
        result["systemic_failure"] = True
        result["errors"].append(msg)
        for day_result in result["dates"].values():
          if day_result["saved"]:
            day_result["errors"].append(msg)
          day_result["failed"] += day_result["saved"]
          day_result["saved"] = 0

    result["saved"] = sum(item["saved"] for item in result["dates"].values())
    result["skipped"] = sum(item["skipped"] for item in result["dates"].values())
    result["failed"] = sum(item["failed"] for item in result["dates"].values())

    self.logger.info(
      "指标批次完成: 目标 %s  保存 %s  跳过 %s  失败 %s",
      result["total"],
      result["saved"],
      result["skipped"],
      result["failed"],
    )
    return result

  async def cleanup_old_snapshots(self, retain_days: int = 30) -> int:
    """删除保留期之前的快照记录。"""
    cutoff = date.today() - timedelta(days=retain_days)
    try:
      async with aclosing(self.db_factory()) as sessions:
        db = await anext(sessions, None)
        if db is None:
          raise RuntimeError("清理日级指标快照未取得数据库连接")
        repo = self.snapshot_repo_cls(db)
        deleted = await repo.delete_older_than(cutoff)
        self.logger.info("已清理 %s 条 %s 之前的快照", deleted, cutoff)
        return deleted
    except Exception as e:
      self.logger.error("清理过期快照失败: %s", e)
      return 0
