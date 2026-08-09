"""
Candidate pool construction utilities for A-share strategies.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence, Tuple, Union

import pandas as pd

UniverseInput = Union[pd.DataFrame, Sequence[Mapping[str, Any]]]
PriceSeriesInput = Optional[Sequence[float]]


class CandidatePool:
  """Build and filter a candidate pool based on liquidity and structure rules."""

  DEFAULT_MA_PERIODS: Tuple[int, ...] = (5, 10, 20)

  def __init__(
    self,
    min_turnover: float = 50_000_000,
    volatility_threshold: float = 0.03,
    ma_deviation_threshold: float = 0.05,
    ma_periods: Optional[Sequence[int]] = None,
    box_window: int = 20,
  ) -> None:
    self.min_turnover = min_turnover
    self.volatility_threshold = volatility_threshold
    self.ma_deviation_threshold = ma_deviation_threshold
    self.ma_periods = tuple(ma_periods) if ma_periods else self.DEFAULT_MA_PERIODS
    self.box_window = box_window

  def apply_hard_filters(self, universe: UniverseInput) -> pd.DataFrame:
    """Filter out ST, suspended, and low-liquidity stocks."""
    df = self._normalize_universe(universe)
    if df.empty:
      return df

    is_st = self._resolve_is_st(df)
    is_suspended = self._resolve_is_suspended(df)
    turnover = self._resolve_turnover(df)
    mask = (~is_st) & (~is_suspended) & (turnover >= self.min_turnover)
    return df.loc[mask].reset_index(drop=True)

  def is_sideways(self, prices: PriceSeriesInput, window: Optional[int] = None) -> bool:
    """Identify sideways movement using return volatility."""
    series = self._to_series(prices)
    if series is None or len(series) < 3:
      return False
    if window:
      series = series.tail(window)
    returns = series.pct_change().dropna()
    if returns.empty:
      return False
    volatility = returns.std(ddof=0)
    if pd.isna(volatility):
      return False
    return float(volatility) <= self.volatility_threshold

  def is_ma_converging(
    self, prices: PriceSeriesInput, periods: Optional[Sequence[int]] = None
  ) -> bool:
    """Check whether moving averages are converging."""
    series = self._to_series(prices)
    if series is None:
      return False
    ma_periods = tuple(periods) if periods else self.ma_periods
    if not ma_periods or len(series) < max(ma_periods):
      return False

    ma_values = []
    for period in ma_periods:
      value = series.rolling(period).mean().iloc[-1]
      if pd.isna(value):
        return False
      ma_values.append(float(value))

    max_ma = max(ma_values)
    min_ma = min(ma_values)
    avg_ma = sum(ma_values) / len(ma_values)
    if avg_ma == 0:
      return False

    deviation = (max_ma - min_ma) / abs(avg_ma)
    return deviation <= self.ma_deviation_threshold

  def detect_box(
    self, prices: PriceSeriesInput, window: Optional[int] = None
  ) -> Dict[str, Optional[float]]:
    """Detect support/resistance and range width for a price window."""
    series = self._to_series(prices)
    if series is None or len(series) < 2:
      return {"support": None, "resistance": None, "width": None, "is_valid": False}

    if window:
      series = series.tail(window)
    support = float(series.min())
    resistance = float(series.max())

    if support <= 0:
      return {
        "support": support,
        "resistance": resistance,
        "width": None,
        "is_valid": False,
      }

    width = (resistance - support) / support
    return {
      "support": support,
      "resistance": resistance,
      "width": float(width),
      "is_valid": True,
    }

  def build_candidates(
    self, universe: UniverseInput, price_map: Mapping[str, Sequence[float]]
  ) -> pd.DataFrame:
    """Apply hard filters and compute structure + box metrics."""
    filtered = self.apply_hard_filters(universe)
    if filtered.empty:
      return filtered

    codes = self._resolve_codes(filtered)
    filtered = filtered.copy()
    filtered["code"] = codes

    sideways_flags = []
    ma_flags = []
    supports = []
    resistances = []
    widths = []
    box_valid = []

    for code in codes:
      prices = price_map.get(code)
      sideways = self.is_sideways(prices, window=self.box_window)
      ma_converging = self.is_ma_converging(prices)
      box_info = self.detect_box(prices, window=self.box_window)

      sideways_flags.append(sideways)
      ma_flags.append(ma_converging)
      supports.append(box_info["support"])
      resistances.append(box_info["resistance"])
      widths.append(box_info["width"])
      box_valid.append(box_info["is_valid"])

    filtered["is_sideways"] = sideways_flags
    filtered["is_ma_converging"] = ma_flags
    filtered["structure_ok"] = filtered["is_sideways"] & filtered["is_ma_converging"]
    filtered["box_support"] = supports
    filtered["box_resistance"] = resistances
    filtered["box_width"] = widths
    filtered["box_valid"] = box_valid

    return filtered

  def _normalize_universe(self, universe: UniverseInput) -> pd.DataFrame:
    if isinstance(universe, pd.DataFrame):
      return universe.copy()
    return pd.DataFrame(list(universe))

  def _to_series(self, prices: PriceSeriesInput) -> Optional[pd.Series]:
    if prices is None:
      return None
    series = pd.Series(list(prices)).dropna()
    if series.empty:
      return None
    return series.astype(float)

  def _resolve_is_st(self, df: pd.DataFrame) -> pd.Series:
    if "is_st" in df.columns:
      return df["is_st"].fillna(False).astype(bool)
    if "name" in df.columns:
      return (
        df["name"]
        .fillna("")
        .astype(str)
        .str.contains("ST", case=False, regex=False)
      )
    return pd.Series(False, index=df.index)

  def _resolve_is_suspended(self, df: pd.DataFrame) -> pd.Series:
    for column in ("is_suspended", "suspended"):
      if column in df.columns:
        return df[column].fillna(False).astype(bool)

    if "suspend_flag" in df.columns:
      return pd.to_numeric(df["suspend_flag"], errors="coerce").fillna(0).gt(0)

    if "instrument_status" in df.columns:
      return pd.to_numeric(df["instrument_status"], errors="coerce").fillna(0).gt(0)

    if "trading_status" in df.columns:
      status = df["trading_status"].fillna("").astype(str).str.lower()
      return status.str.contains("suspend|halt|stopped", regex=True)

    return pd.Series(False, index=df.index)

  def _resolve_turnover(self, df: pd.DataFrame) -> pd.Series:
    for column in (
      "turnover",
      "amount",
      "avg_turnover",
      "avg_amount",
      "daily_turnover",
      "daily_amount",
    ):
      if column in df.columns:
        return pd.to_numeric(df[column], errors="coerce").fillna(0.0)
    return pd.Series(0.0, index=df.index)

  def _resolve_codes(self, df: pd.DataFrame) -> pd.Series:
    for column in ("code", "stock_code", "symbol", "instrument_id", "id"):
      if column in df.columns:
        return df[column].astype(str)
    raise ValueError("Universe data missing instrument code column")
