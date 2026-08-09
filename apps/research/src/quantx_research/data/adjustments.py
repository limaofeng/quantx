"""研究侧按 QMT ``dr`` 语义构造连续价格序列。"""

from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd

from .models import AdjustmentMode
from .normalization import as_datetime, normalize_dividend_factors

_PRICE_COLUMNS = ("open", "high", "low", "close")


def apply_dividend_adjustment(
  bars: pd.DataFrame,
  factors: pd.DataFrame,
  *,
  mode: AdjustmentMode = "point_in_time",
  as_of: date | datetime | None = None,
) -> pd.DataFrame:
  """应用只使用 ``as_of`` 当日及之前已生效因子的日线复权。

  QMT ``dr`` 是“除权前原始收盘 / 除权参考价”。``front`` 将事件日前
  历史价格换算到研究截止日价格基准；它是事后重述，历史日期会依赖截至
  ``as_of`` 已知、但在该历史日期之后发生的公司行为。``back`` 仅累计每根
  bar 当日及之前已经生效的因子。``point_in_time`` 等价于 ``back`` 并截断
  ``as_of`` 之后因子，适合禁止未来泄漏的历史信号与事件研究。

  空因子集表示请求窗口内没有公司行为，是合法且无需调整的结果。完整性由
  上游 QMT 回填状态账本保证，而不是用“必须存在一条因子”推断。
  """
  if mode not in {"none", "front", "back", "point_in_time"}:
    raise ValueError(f"不支持的复权模式: {mode}")

  adjusted = bars.copy()
  adjusted["adjustment_valid"] = mode == "none"
  if adjusted.empty or mode == "none":
    return adjusted
  bar_times = pd.to_datetime(adjusted["time"], errors="coerce")
  if bar_times.dt.tz is not None:
    bar_times = bar_times.dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
  adjusted["time"] = bar_times.astype("datetime64[ns]")
  for column in _PRICE_COLUMNS:
    adjusted[column] = pd.to_numeric(adjusted[column], errors="coerce").astype(float)

  normalized_factors = normalize_dividend_factors(factors)
  cutoff = pd.Timestamp(as_datetime(as_of)).normalize() if as_of else None
  effective_mode = "back" if mode == "point_in_time" else mode

  for code, row_index in adjusted.groupby("stock_code", sort=False).groups.items():
    code_factors = normalized_factors[
      normalized_factors["stock_code"] == str(code).upper()
    ].copy()
    if cutoff is not None:
      code_factors = code_factors[code_factors["time"] <= cutoff]

    invalid = code_factors["dr"].isna() | ~np.isfinite(code_factors["dr"])
    invalid |= code_factors["dr"] <= 0
    if invalid.any():
      continue
    adjusted.loc[row_index, "adjustment_valid"] = True
    if code_factors.empty:
      continue

    # 同一除权日若存在多条记录，其 dr 依次累乘。
    code_factors = code_factors.groupby("time", as_index=False, sort=True)["dr"].prod()
    code_factors["cum_factor"] = code_factors["dr"].cumprod()
    total_factor = float(code_factors["cum_factor"].iloc[-1])

    code_bars = adjusted.loc[row_index, ["time", *_PRICE_COLUMNS]].copy()
    code_bars["_source_index"] = code_bars.index
    code_bars = code_bars.sort_values("time", kind="stable")
    aligned = pd.merge_asof(
      code_bars,
      code_factors[["time", "cum_factor"]],
      on="time",
      direction="backward",
    )
    aligned["cum_factor"] = aligned["cum_factor"].fillna(1.0)
    if effective_mode == "front":
      multiplier = aligned["cum_factor"] / total_factor
    else:
      multiplier = aligned["cum_factor"]

    for column in _PRICE_COLUMNS:
      values = pd.to_numeric(aligned[column], errors="coerce") * multiplier
      adjusted.loc[aligned["_source_index"], column] = values.to_numpy()

  return adjusted
