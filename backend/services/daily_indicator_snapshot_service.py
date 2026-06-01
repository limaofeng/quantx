"""日级技术指标快照服务。"""

import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from core.indicators.bollinger import BollingerBands
from core.indicators.ma import SMA
from core.indicators.rsi import RSI
from database.relational_connection import get_async_db
from miniqmt.manager_registry import XTDataManagerRegistry
from repositories.indicator_snapshot_repository import IndicatorSnapshotRepository

logger = logging.getLogger(__name__)


def compute_kdj_series(
  highs: List[float],
  lows: List[float],
  closes: List[float],
  period: int = 9,
) -> List[Tuple[float, float, float]]:
  """计算 KDJ (K, D, J) 完整序列。"""
  result: List[Tuple[float, float, float]] = []
  k_prev, d_prev = 50.0, 50.0

  for i in range(len(closes)):
    if i < period - 1:
      result.append((50.0, 50.0, 50.0))
      continue
    window_high = max(highs[i - period + 1 : i + 1])
    window_low = min(lows[i - period + 1 : i + 1])
    denom = window_high - window_low
    rsv = (closes[i] - window_low) / denom * 100 if denom > 0 else 50.0
    k = 2 / 3 * k_prev + 1 / 3 * rsv
    d = 2 / 3 * d_prev + 1 / 3 * k
    j = 3 * k - 2 * d
    result.append((k, d, j))
    k_prev, d_prev = k, d

  return result


def detect_daily_signals(snap: Dict[str, Any]) -> List[str]:
  """根据快照字段判断命中的量化信号。"""
  signals: List[str] = []

  def _f(key: str) -> Optional[float]:
    return snap.get(key)

  if (
    (_f("price_drop_pct") or 0) < -20
    and (_f("rsi12") or 100) < 40
    and (_f("boll_percent_b") or 1) < 0.25
  ):
    signals.append("超跌反弹")

  if (
    (_f("boll_percent_b") or 0) > 0.8
    and (_f("volume_ratio") or 0) > 1.2
    and (_f("price_drop_pct") or -100) > -10
  ):
    signals.append("强势股")

  k, d = _f("kdj_k"), _f("kdj_d")
  k_p, d_p = _f("kdj_k_prev"), _f("kdj_d_prev")
  if all(v is not None for v in (k, d, k_p, d_p)) and k > d and k_p <= d_p:
    signals.append("KDJ 金叉")

  if (_f("volume_ratio") or 0) > 1.5:
    signals.append("放量突破")

  ma5, ma10 = _f("ma5"), _f("ma10")
  ma5p, ma10p = _f("ma5_prev"), _f("ma10_prev")
  if all(v is not None for v in (ma5, ma10, ma5p, ma10p)) and ma5 > ma10 and ma5p <= ma10p:
    signals.append("均线金叉")

  lower = _f("boll_lower")
  price = _f("current_price")
  if lower is not None and price is not None and price <= lower * 1.02:
    signals.append("布林下轨反弹")

  upper = _f("boll_upper")
  if upper is not None and price is not None and price >= upper * 0.98:
    signals.append("布林上轨突破")

  if (_f("rsi12") or 100) <= 30:
    signals.append("RSI 超卖")

  if (_f("rsi12") or 0) >= 70:
    signals.append("RSI 强势")

  return signals


def build_snapshot_record(
  code: str,
  instrument_type: str,
  name: str,
  snapshot_date: date,
  df,
) -> Optional[Dict[str, Any]]:
  """从单只标的日线 DataFrame 计算指标快照。"""
  try:
    closes = list(df["close"])
    highs = list(df["high"])
    lows = list(df["low"])
    opens = list(df["open"])
    volumes = list(df["volume"])
    amounts = list(df["amount"]) if "amount" in df.columns else [0.0] * len(closes)

    if len(closes) < 25:
      return None

    cur = closes[-1]
    prev_close = closes[-2]
    change_pct = (cur - prev_close) / prev_close * 100 if prev_close else 0.0

    vol_today = volumes[-1]
    recent_vols = volumes[-21:-1]
    avg_vol_20 = sum(recent_vols) / len(recent_vols) if recent_vols else vol_today
    volume_ratio = vol_today / avg_vol_20 if avg_vol_20 > 0 else 1.0

    ma5 = SMA(5).calculate(closes)
    ma10 = SMA(10).calculate(closes)
    ma20 = SMA(20).calculate(closes)
    ma5_prev = SMA(5).calculate(closes[:-1]) if len(closes) > 5 else None
    ma10_prev = SMA(10).calculate(closes[:-1]) if len(closes) > 10 else None

    rsi6 = RSI(6).calculate(closes)
    rsi12 = RSI(12).calculate(closes)
    rsi24 = RSI(24).calculate(closes)
    rsi12_prev = RSI(12).calculate(closes[:-1]) if len(closes) > 13 else None

    kdj_series = compute_kdj_series(highs, lows, closes, period=9)
    k_cur, d_cur, j_cur = kdj_series[-1]
    k_prev_val, d_prev_val, _ = (
      kdj_series[-2] if len(kdj_series) >= 2 else (50.0, 50.0, 50.0)
    )

    boll_result = BollingerBands(period=20, multiplier=2.0).calculate(closes)
    boll_upper = boll_result["upper"] if boll_result else None
    boll_mid = boll_result["middle"] if boll_result else None
    boll_lower = boll_result["lower"] if boll_result else None
    boll_percent_b = boll_result["percent_b"] if boll_result else None
    boll_bandwidth = boll_result["bandwidth"] if boll_result else None

    h252 = highs[-252:] if len(highs) >= 252 else highs
    l252 = lows[-252:] if len(lows) >= 252 else lows
    peak_price = max(h252)
    low_252 = min(l252)
    price_drop_pct = (cur - peak_price) / peak_price * 100 if peak_price else 0.0
    price_rise_pct = (cur - low_252) / low_252 * 100 if low_252 else 0.0
    peak_idx = max(range(len(h252)), key=lambda i: h252[i])
    low_idx = min(range(len(l252)), key=lambda i: l252[i])

    consecutive_down_days = 0
    for i in range(len(closes) - 2, max(len(closes) - 21, -1), -1):
      if closes[i] >= closes[i + 1]:
        break
      consecutive_down_days += 1

    consecutive_start = (
      closes[-(consecutive_down_days + 1)] if consecutive_down_days > 0 else cur
    )
    consecutive_down_pct = (
      (cur - consecutive_start) / consecutive_start * 100
      if consecutive_down_days > 0 and consecutive_start > 0
      else 0.0
    )

    snap: Dict[str, Any] = {
      "code": code,
      "snapshot_date": snapshot_date,
      "instrument_type": instrument_type,
      "name": name,
      "current_price": round(cur, 4),
      "open_price": round(opens[-1], 4),
      "high_price": round(highs[-1], 4),
      "low_price_day": round(lows[-1], 4),
      "change_pct": round(change_pct, 4),
      "volume": round(vol_today, 2),
      "amount": round(amounts[-1], 2),
      "volume_ratio": round(volume_ratio, 4),
      "avg_volume_20": round(avg_vol_20, 2),
      "ma5": round(ma5, 4) if ma5 is not None else None,
      "ma10": round(ma10, 4) if ma10 is not None else None,
      "ma20": round(ma20, 4) if ma20 is not None else None,
      "ma5_prev": round(ma5_prev, 4) if ma5_prev is not None else None,
      "ma10_prev": round(ma10_prev, 4) if ma10_prev is not None else None,
      "rsi6": round(rsi6, 4) if rsi6 is not None else None,
      "rsi12": round(rsi12, 4) if rsi12 is not None else None,
      "rsi24": round(rsi24, 4) if rsi24 is not None else None,
      "rsi12_prev": round(rsi12_prev, 4) if rsi12_prev is not None else None,
      "kdj_k": round(k_cur, 4),
      "kdj_d": round(d_cur, 4),
      "kdj_j": round(j_cur, 4),
      "kdj_k_prev": round(k_prev_val, 4),
      "kdj_d_prev": round(d_prev_val, 4),
      "boll_upper": round(boll_upper, 4) if boll_upper is not None else None,
      "boll_mid": round(boll_mid, 4) if boll_mid is not None else None,
      "boll_lower": round(boll_lower, 4) if boll_lower is not None else None,
      "boll_percent_b": round(boll_percent_b, 6) if boll_percent_b is not None else None,
      "boll_bandwidth": round(boll_bandwidth, 6) if boll_bandwidth is not None else None,
      "peak_price": round(peak_price, 4),
      "price_drop_pct": round(price_drop_pct, 4),
      "days_since_peak": int(len(h252) - 1 - peak_idx),
      "low_price_252": round(low_252, 4),
      "price_rise_pct": round(price_rise_pct, 4),
      "days_since_low": int(len(l252) - 1 - low_idx),
      "consecutive_down_days": consecutive_down_days,
      "consecutive_down_pct": round(consecutive_down_pct, 4),
    }
    snap["matched_signals"] = detect_daily_signals(snap)
    return snap
  except Exception:
    logger.exception("构建技术指标快照失败: %s", code)
    return None


class DailyIndicatorSnapshotService:
  """读取日线、计算指标并写入日级快照。"""

  def __init__(
    self,
    data_registry_factory=XTDataManagerRegistry,
    db_factory=get_async_db,
    snapshot_repo_cls=IndicatorSnapshotRepository,
    logger_=None,
  ):
    self.data_registry_factory = data_registry_factory
    self.db_factory = db_factory
    self.snapshot_repo_cls = snapshot_repo_cls
    self.logger = logger_ or logger

  async def compute_and_save_batch(
    self,
    codes: List[str],
    snapshot_date: date,
    instrument_type_map: Dict[str, str],
    name_map: Dict[str, str],
    lookback_days: int = 310,
  ) -> Dict[str, Any]:
    """计算并保存一批标的的日级技术指标快照。"""
    start_str = (snapshot_date - timedelta(days=lookback_days)).strftime("%Y%m%d")
    end_str = snapshot_date.strftime("%Y%m%d")
    result: Dict[str, Any] = {
      "total": len(codes),
      "saved": 0,
      "skipped": 0,
      "failed": 0,
      "errors": [],
    }

    try:
      data_manager = self.data_registry_factory().get_manager()
      market_data = data_manager.get_market_data(
        stock_list=codes,
        period="1d",
        start_time=start_str,
        end_time=end_str,
        count=-1,
        dividend_type="none",
      )
    except Exception as e:
      msg = f"批量拉取 K 线失败: {e}"
      self.logger.error(msg)
      result["failed"] = len(codes)
      result["errors"].append(msg)
      return result

    if not hasattr(market_data, "get"):
      msg = f"批量拉取 K 线返回格式异常: {type(market_data).__name__}"
      self.logger.error(msg)
      result["failed"] = len(codes)
      result["errors"].append(msg)
      return result

    records: List[Dict[str, Any]] = []
    for code in codes:
      df = market_data.get(code)
      if df is None or getattr(df, "empty", True):
        result["skipped"] += 1
        continue

      snap = build_snapshot_record(
        code=code,
        instrument_type=instrument_type_map.get(code, "stock"),
        name=name_map.get(code, ""),
        snapshot_date=snapshot_date,
        df=df,
      )
      if snap is None:
        result["skipped"] += 1
      else:
        records.append(snap)

    if records:
      try:
        async for db in self.db_factory():
          repo = self.snapshot_repo_cls(db)
          await repo.bulk_upsert(records)
          result["saved"] = len(records)
          break
      except Exception as e:
        msg = f"批量写入快照失败: {e}"
        self.logger.error(msg)
        result["failed"] += len(records)
        result["saved"] = 0
        result["errors"].append(msg)

    self.logger.info(
      "批次完成: 共 %s  保存 %s  跳过 %s  失败 %s",
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
      async for db in self.db_factory():
        repo = self.snapshot_repo_cls(db)
        deleted = await repo.delete_older_than(cutoff)
        self.logger.info("已清理 %s 条 %s 之前的快照", deleted, cutoff)
        return deleted
    except Exception as e:
      self.logger.error("清理过期快照失败: %s", e)
      return 0
