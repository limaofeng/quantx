"""Versioned, point-in-time daily indicators shared by screening and research.

No storage or data acquisition lives here. Callers supply one instrument's ordered
bars, on a continuous point-in-time adjusted price basis. ``raw_close`` optionally
restores price-denominated outputs to the price basis observable on each row.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

INDICATOR_VERSION = "daily-indicator-v1"
NUMERIC_OPERATORS = ("gte", "lte", "gt", "lt", "eq", "between")


@dataclass(frozen=True)
class IndicatorDefinition:
  id: str
  label: str
  category: str
  description: str
  unit: str
  lookback: int
  kind: str = "numeric"
  research_supported: bool = True
  unsupported_reason: str | None = None
  version: str = INDICATOR_VERSION

  @property
  def operators(self) -> tuple[str, ...]:
    return ("eq",) if self.kind == "binary" else NUMERIC_OPERATORS


def _indicator(
  id: str,
  label: str,
  category: str,
  formula: str,
  unit: str,
  lookback: int,
  **kwargs: object,
) -> IndicatorDefinition:
  return IndicatorDefinition(id, label, category, formula, unit, lookback, **kwargs)


INDICATOR_DEFINITIONS = (
  _indicator("current_price", "收盘价", "价格", "当日未复权收盘价", "元", 1),
  _indicator(
    "change_pct", "当日涨跌幅", "价格", "连续复权收盘价相对前一交易日变化 × 100", "%", 2
  ),
  _indicator(
    "volume_ratio", "20日量比", "量额", "当日成交量 / 前20日均量（不含当日）", "倍", 21
  ),
  _indicator(
    "volume_ratio_5", "5日量比", "量额", "当日成交量 / 前5日均量（不含当日）", "倍", 6
  ),
  _indicator(
    "avg_volume_5", "前5日均量", "量额", "前5日成交量均值（不含当日）", "手", 6
  ),
  _indicator(
    "avg_volume_20", "前20日均量", "量额", "前20日成交量均值（不含当日）", "手", 21
  ),
  _indicator(
    "avg_amount_20", "前20日均额", "量额", "前20日成交额均值（不含当日）", "元", 21
  ),
  _indicator(
    "amount_ratio_20",
    "20日额比",
    "量额",
    "当日成交额 / 前20日均额（不含当日）",
    "倍",
    21,
  ),
  _indicator(
    "volume_percentile_60",
    "60日成交量分位",
    "量额",
    "含当日60日内小于等于当日成交量的比例 × 100",
    "%",
    60,
  ),
  _indicator(
    "amount_percentile_60",
    "60日成交额分位",
    "量额",
    "含当日60日内小于等于当日成交额的比例 × 100",
    "%",
    60,
  ),
  _indicator(
    "price_drop_pct",
    "距252日高点回撤",
    "位置",
    "收盘价 / 含当日252日最高价 − 1，百分比",
    "%",
    252,
  ),
  _indicator(
    "price_rise_pct",
    "距252日低点涨幅",
    "位置",
    "收盘价 / 含当日252日最低价 − 1，百分比",
    "%",
    252,
  ),
  _indicator(
    "days_since_peak",
    "距252日高点天数",
    "位置",
    "含当日252日最高价首次出现距今的交易记录数",
    "日",
    252,
  ),
  _indicator(
    "days_since_low",
    "距252日低点天数",
    "位置",
    "含当日252日最低价首次出现距今的交易记录数",
    "日",
    252,
  ),
  _indicator(
    "consecutive_down_days",
    "连续下跌天数",
    "动量",
    "最近20个涨跌区间内连续收盘下跌数；平盘中断，最多20日",
    "日",
    21,
  ),
  _indicator(
    "consecutive_down_pct",
    "连续下跌幅度",
    "动量",
    "最近连续下跌区间的累计百分比，最多20日",
    "%",
    21,
  ),
  *(
    _indicator(
      f"rsi{n}",
      f"RSI({n})",
      "动量",
      f"最近{n}个收盘变化的简单平均涨跌幅 RSI；平盘为50",
      "",
      n + 1,
    )
    for n in (6, 12, 24)
  ),
  *(
    _indicator(
      f"ma{n}",
      f"MA({n})",
      "趋势",
      f"最近{n}日连续复权收盘均值，换算到当日价格基准",
      "元",
      n,
    )
    for n in (5, 10, 20)
  ),
  *(
    _indicator(
      f"kdj_{part}",
      f"KDJ {part.upper()}",
      "动量",
      "RSV(9)，K/D 各用63项2/3衰减及50初值残差；固定窗口保证可复现",
      "",
      133,
    )
    for part in ("k", "d", "j")
  ),
  _indicator(
    "kdj_cross_up", "KDJ金叉", "交叉", "当日K>D 且前日K≤D", "", 134, kind="binary"
  ),
  _indicator(
    "ma_cross_up",
    "MA5上穿MA10",
    "交叉",
    "当日MA5>MA10 且前日MA5≤MA10",
    "",
    11,
    kind="binary",
  ),
  _indicator(
    "boll_percent_b",
    "布林带位置",
    "位置",
    "(收盘−下轨)/(上轨−下轨)，20日总体标准差×2；带宽为零时0.5",
    "",
    20,
  ),
  _indicator(
    "boll_bandwidth", "布林带宽", "波动", "(上轨−下轨)/中轨，20日总体标准差×2", "", 20
  ),
  _indicator(
    "boll_near_lower",
    "触及布林下轨附近",
    "位置",
    "收盘价≤布林下轨×1.02；不代表已经反弹",
    "",
    20,
    kind="binary",
  ),
  _indicator(
    "boll_near_upper",
    "触及布林上轨附近",
    "位置",
    "收盘价≥布林上轨×0.98；不代表发生上穿",
    "",
    20,
    kind="binary",
  ),
  _indicator(
    "turnover_rate_pct",
    "换手率",
    "量额",
    "当日成交股数 / 当前流通股本 × 100",
    "%",
    1,
    research_supported=False,
    unsupported_reason="尚未核验历史流通股本的时点覆盖",
  ),
  _indicator(
    "roe_ttm",
    "ROE TTM",
    "财务",
    "筛选日可见且质量有效的ROE TTM",
    "%",
    0,
    research_supported=False,
    unsupported_reason="财务历史可见时间与质量覆盖尚未接入研究",
  ),
  _indicator(
    "net_profit_growth_pct",
    "单季净利润同比",
    "财务",
    "筛选日可见的单季净利润同比",
    "%",
    0,
    research_supported=False,
    unsupported_reason="财务历史可见时间与质量覆盖尚未接入研究",
  ),
  _indicator(
    "revenue_growth_pct",
    "单季营收同比",
    "财务",
    "筛选日可见的单季营业收入同比",
    "%",
    0,
    research_supported=False,
    unsupported_reason="财务历史可见时间与质量覆盖尚未接入研究",
  ),
)
INDICATOR_BY_ID = {indicator.id: indicator for indicator in INDICATOR_DEFINITIONS}


def normalize_conditions(conditions: Sequence[Mapping[str, object]]) -> list[dict]:
  """Validate one finite AND predicate; no unknown fields or silent fallbacks."""
  if len(conditions) > 64:
    raise ValueError("最多支持64个指标条件")
  normalized: dict[str, dict] = {}
  for condition in conditions:
    if set(condition) - {"indicator_id", "operator", "value", "value_to"}:
      raise ValueError("指标条件包含未知字段")
    indicator_id = str(condition.get("indicator_id", ""))
    indicator = INDICATOR_BY_ID.get(indicator_id)
    if indicator is None:
      raise ValueError(f"未知指标: {indicator_id}")
    operator = str(condition.get("operator", "")).lower()
    if operator not in indicator.operators:
      raise ValueError(f"{indicator_id} 不支持操作符 {operator}")
    value = _condition_number(condition.get("value"))
    item = {"indicator_id": indicator_id, "operator": operator, "value": value}
    if operator == "between":
      upper = _condition_number(condition.get("value_to"))
      if upper < value:
        raise ValueError("区间上限不能小于下限")
      item["value_to"] = upper
    elif condition.get("value_to") is not None:
      raise ValueError("只有between可设置区间上限")
    if indicator.kind == "binary" and value not in (0.0, 1.0):
      raise ValueError("二值指标仅接受0或1")
    normalized[json.dumps(item, sort_keys=True, separators=(",", ":"))] = item
  return [normalized[key] for key in sorted(normalized)]


def _condition_number(value: object) -> float:
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    raise ValueError("指标阈值必须是有限数值")
  result = float(value)
  if not math.isfinite(result):
    raise ValueError("指标阈值必须是有限数值")
  return 0.0 if result == 0 else result


def condition_mask(
  frame: pd.DataFrame, conditions: Sequence[Mapping[str, object]]
) -> pd.Series:
  mask = pd.Series(True, index=frame.index)
  for item in normalize_conditions(conditions):
    values = frame[item["indicator_id"]]
    value = item["value"]
    operator = item["operator"]
    selected = {
      "gte": lambda: values.ge(value),
      "lte": lambda: values.le(value),
      "gt": lambda: values.gt(value),
      "lt": lambda: values.lt(value),
      "eq": lambda: values.eq(value),
      "between": lambda: values.between(value, item["value_to"]),
    }[operator]()
    mask &= values.notna() & selected
  return mask


def valid_indicator_observations(bars: pd.DataFrame) -> pd.Series:
  """Shared OHLCV/amount and optional source-quality eligibility, not tradability."""
  prices = bars.reindex(columns=["open", "high", "low", "close"]).apply(
    pd.to_numeric, errors="coerce"
  )
  valid = np.isfinite(prices).all(axis=1) & prices.gt(0).all(axis=1)
  valid &= prices.high.ge(prices[["open", "close", "low"]].max(axis=1))
  valid &= prices.low.le(prices[["open", "close", "high"]].min(axis=1))
  volume = pd.to_numeric(
    bars.get("volume", pd.Series(np.nan, index=bars.index)), errors="coerce"
  )
  amount = pd.to_numeric(
    bars.get("amount", pd.Series(np.nan, index=bars.index)), errors="coerce"
  )
  valid &= np.isfinite(volume) & volume.gt(0) & np.isfinite(amount) & amount.ge(0)
  for name in ("adjustment_valid", "listing_valid"):
    if name in bars:
      valid &= bars[name].eq(True)
  if "suspend_flag" in bars:
    valid &= pd.to_numeric(bars["suspend_flag"], errors="coerce").eq(0).fillna(False)
  return valid.fillna(False)


def calculate_indicator_frame(
  bars: pd.DataFrame,
  *,
  trading_dates: Sequence | pd.DatetimeIndex | None = None,
) -> pd.DataFrame:
  """Calculate one ordered instrument without future reads or filled prices.

  Bad/suspended observations stay in place and invalidate the affected rolling
  window. KDJ uses a finite 63-term exponential convolution per smoothing stage,
  so additional earlier history cannot change an already warm indicator.
  Known missing market sessions invalidate rolling windows, rather than being
  compressed away. Return only the original rows/index. All outputs share
  eight-decimal precision.
  """
  frame = pd.DataFrame(index=bars.index)
  if bars.empty:
    return frame
  if trading_dates is not None:
    if len(trading_dates) == 0:
      raise ValueError("指标计算缺少交易日历")
    source_days = (
      pd.DatetimeIndex(pd.to_datetime(bars["time"], utc=True))
      .tz_convert("Asia/Shanghai")
      .normalize()
      .tz_localize(None)
    )
    if (
      source_days.hasnans
      or not source_days.is_unique
      or not source_days.is_monotonic_increasing
    ):
      raise ValueError("指标日线必须按唯一有效交易日升序排列")
    market_days = (
      pd.DatetimeIndex(pd.to_datetime(trading_dates, utc=True))
      .tz_convert("Asia/Shanghai")
      .normalize()
      .tz_localize(None)
    )
    market_days = market_days[
      (market_days >= source_days[0]) & (market_days <= source_days[-1])
    ]
    aligned_days = market_days.union(source_days).sort_values().unique()
    if len(aligned_days) != len(source_days):
      aligned = bars.copy()
      aligned.index = source_days
      calculated = calculate_indicator_frame(aligned.reindex(aligned_days))
      return calculated.reindex(source_days).set_axis(bars.index)
  valid = valid_indicator_observations(bars)

  def series(name: str, *, positive: bool = False) -> pd.Series:
    value = pd.to_numeric(
      bars.get(name, pd.Series(np.nan, index=bars.index)), errors="coerce"
    ).astype(float)
    return value.where(
      valid & np.isfinite(value) & (value.gt(0) if positive else value.ge(0))
    )

  close, high, low = (series(name, positive=True) for name in ("close", "high", "low"))
  volume, amount = series("volume"), series("amount")
  scale = (
    series("raw_close", positive=True) / close
    if "raw_close" in bars
    else pd.Series(1.0, index=bars.index)
  )
  frame["current_price"] = close * scale
  frame["change_pct"] = (close / close.shift(1) - 1) * 100
  for window in (5, 20):
    average = volume.shift(1).rolling(window, min_periods=window).mean()
    frame[f"avg_volume_{window}"] = average
    frame["volume_ratio" if window == 20 else "volume_ratio_5"] = (
      volume / average.where(average.gt(0))
    )
  frame["avg_amount_20"] = amount.shift(1).rolling(20, min_periods=20).mean()
  frame["amount_ratio_20"] = amount / frame["avg_amount_20"].where(
    frame["avg_amount_20"].gt(0)
  )
  for name, values in (("volume", volume), ("amount", amount)):
    frame[f"{name}_percentile_60"] = (
      values.rolling(60, min_periods=60).rank(method="max", pct=True) * 100
    )
  for window in (5, 10, 20):
    average = close.rolling(window, min_periods=window).mean()
    frame[f"ma{window}"] = average * scale
    if window in (5, 10):
      frame[f"ma{window}_prev"] = average.shift(1) * scale
  delta = close.diff()
  for window in (6, 12, 24):
    gain = delta.clip(lower=0).rolling(window, min_periods=window).mean()
    loss = -delta.clip(upper=0).rolling(window, min_periods=window).mean()
    rsi = 100 * gain / (gain + loss)
    frame[f"rsi{window}"] = rsi.mask(gain.eq(0) & loss.eq(0), 50.0)
  frame["rsi12_prev"] = frame["rsi12"].shift(1)
  highest, lowest = high.rolling(9).max(), low.rolling(9).min()
  rsv = (close - lowest) / (highest - lowest) * 100
  rsv = rsv.mask(highest.eq(lowest) & highest.notna(), 50.0)
  weights = (1 / 3) * (2 / 3) ** np.arange(63)

  def smooth(values: pd.Series) -> pd.Series:
    if len(values) < 63:
      return pd.Series(np.nan, index=values.index)
    convolution = np.convolve(values.to_numpy(), weights, mode="full")[: len(values)]
    result = pd.Series(convolution + 50 * (2 / 3) ** 63, index=values.index)
    return result.where(values.rolling(63).count().eq(63))

  k = smooth(rsv)
  d = smooth(k)
  frame["kdj_k"], frame["kdj_d"], frame["kdj_j"] = k.where(d.notna()), d, 3 * k - 2 * d
  frame["kdj_k_prev"], frame["kdj_d_prev"] = frame["kdj_k"].shift(1), d.shift(1)
  frame["kdj_cross_up"] = (
    (k.gt(d) & k.shift(1).le(d.shift(1)))
    .astype(float)
    .where(d.notna() & d.shift(1).notna())
  )
  frame["ma_cross_up"] = (
    (frame.ma5.gt(frame.ma10) & frame.ma5_prev.le(frame.ma10_prev))
    .astype(float)
    .where(frame.ma10_prev.notna() & frame.ma10.notna())
  )
  middle = close.rolling(20).mean()
  deviation = close.rolling(20).std(ddof=0)
  upper, lower = middle + 2 * deviation, middle - 2 * deviation
  frame["boll_mid"], frame["boll_upper"], frame["boll_lower"] = (
    middle * scale,
    upper * scale,
    lower * scale,
  )
  frame["boll_percent_b"] = ((close - lower) / (upper - lower)).mask(
    deviation.eq(0), 0.5
  )
  frame["boll_bandwidth"] = (upper - lower) / middle
  frame["boll_near_lower"] = (
    close.le(lower * 1.02).astype(float).where(lower.notna() & close.notna())
  )
  frame["boll_near_upper"] = (
    close.ge(upper * 0.98).astype(float).where(upper.notna() & close.notna())
  )
  peak, bottom = high.rolling(252).max(), low.rolling(252).min()
  frame["peak_price"], frame["low_price_252"] = peak * scale, bottom * scale
  frame["price_drop_pct"] = (close / peak - 1) * 100
  frame["price_rise_pct"] = (close / bottom - 1) * 100
  frame["days_since_peak"] = high.rolling(252).apply(
    lambda values: 251 - np.argmax(values), raw=True
  )
  frame["days_since_low"] = low.rolling(252).apply(
    lambda values: 251 - np.argmin(values), raw=True
  )
  down = delta.lt(0)
  streak = down.groupby((~down).cumsum()).cumsum().clip(upper=20).astype(int)
  start_index = np.arange(len(close)) - streak.to_numpy()
  frame["consecutive_down_days"] = streak.where(close.rolling(21).count().eq(21))
  frame["consecutive_down_pct"] = pd.Series(
    (close.to_numpy() / close.to_numpy()[start_index] - 1) * 100, index=close.index
  ).where(frame.consecutive_down_days.notna())
  return frame.replace([np.inf, -np.inf], np.nan).round(8)
