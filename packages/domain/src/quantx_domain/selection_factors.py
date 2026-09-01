"""Point-in-time factors for the next-day probability research model.

Indicators are raw technical measurements for one instrument.  This module is the
separate factor layer: it derives scale-free inputs and performs one-date
cross-sectional transforms without storage, network access, or future reads.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .indicators import INDICATOR_VERSION

FACTOR_SET_VERSION = "next-day-selection-factor-v1"
LABEL_VERSION = "next-open-to-close-up-v1"
WINSOR_LOWER = 0.01
WINSOR_UPPER = 0.99


@dataclass(frozen=True)
class SelectionFactorDefinition:
  id: str
  source_indicators: tuple[str, ...]
  description: str
  transform: str


_DIRECT = (
  "change_pct",
  "volume_ratio",
  "volume_ratio_5",
  "amount_ratio_20",
  "volume_percentile_60",
  "amount_percentile_60",
  "price_drop_pct",
  "price_rise_pct",
  "days_since_peak",
  "days_since_low",
  "consecutive_down_days",
  "consecutive_down_pct",
  "rsi6",
  "rsi12",
  "rsi24",
  "kdj_k",
  "kdj_d",
  "kdj_j",
  "boll_percent_b",
  "boll_bandwidth",
)
_BINARY = (
  "kdj_cross_up",
  "ma_cross_up",
  "boll_near_lower",
  "boll_near_upper",
)


def _definition(
  factor_id: str,
  sources: tuple[str, ...],
  description: str,
  transform: str,
) -> SelectionFactorDefinition:
  return SelectionFactorDefinition(factor_id, sources, description, transform)


SELECTION_FACTOR_DEFINITIONS = (
  *(
    _definition(
      indicator_id,
      (indicator_id,),
      f"{indicator_id} 的当日横截面稳健标准化与百分位",
      "winsor_1_99_then_zscore_and_rank",
    )
    for indicator_id in _DIRECT
  ),
  *(
    _definition(
      indicator_id,
      (indicator_id,),
      f"{indicator_id} 二值状态",
      "binary_passthrough",
    )
    for indicator_id in _BINARY
  ),
  *(
    _definition(
      f"ma{window}_distance",
      (f"ma{window}", "current_price"),
      f"MA({window}) / 当日收盘价 - 1",
      "price_normalized_then_winsor_zscore_and_rank",
    )
    for window in (5, 10, 20)
  ),
  _definition(
    "avg_volume_5_log",
    ("avg_volume_5",),
    "log1p(前5日均量)",
    "log1p_then_winsor_zscore_and_rank",
  ),
  _definition(
    "avg_volume_20_log",
    ("avg_volume_20",),
    "log1p(前20日均量)",
    "log1p_then_winsor_zscore_and_rank",
  ),
  _definition(
    "avg_amount_20_log",
    ("avg_amount_20",),
    "log1p(前20日均额)",
    "log1p_then_winsor_zscore_and_rank",
  ),
)


def _schema_payload() -> dict[str, object]:
  return {
    "factor_set_version": FACTOR_SET_VERSION,
    "indicator_version": INDICATOR_VERSION,
    "winsor": [WINSOR_LOWER, WINSOR_UPPER],
    "definitions": [asdict(item) for item in SELECTION_FACTOR_DEFINITIONS],
  }


FACTOR_SET_HASH = hashlib.sha256(
  json.dumps(
    _schema_payload(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
  ).encode("utf-8")
).hexdigest()


def factor_schema_manifest() -> dict[str, object]:
  """Return the canonical, hash-verifiable factor schema."""

  return {**_schema_payload(), "factor_set_hash": FACTOR_SET_HASH}


def _numeric(frame: pd.DataFrame, name: str) -> pd.Series:
  if name not in frame:
    return pd.Series(np.nan, index=frame.index, dtype=float)
  return pd.to_numeric(frame[name], errors="coerce").replace([np.inf, -np.inf], np.nan)


def _base_series(
  frame: pd.DataFrame, definition: SelectionFactorDefinition
) -> pd.Series:
  factor_id = definition.id
  if factor_id.endswith("_distance"):
    ma = _numeric(frame, definition.source_indicators[0])
    price = _numeric(frame, "current_price")
    return ma.div(price.where(price.gt(0))).sub(1)
  if factor_id.endswith("_log"):
    values = _numeric(frame, definition.source_indicators[0])
    return np.log1p(values.where(values.ge(0)))
  return _numeric(frame, definition.source_indicators[0])


def _cross_sectional_transform(
  values: pd.Series,
  dates: pd.Series,
) -> tuple[pd.Series, pd.Series]:
  def transform(group: pd.Series) -> pd.DataFrame:
    finite = group.dropna()
    result = pd.DataFrame(index=group.index, columns=["z", "rank"], dtype=float)
    if finite.empty:
      return result
    lower, upper = finite.quantile([WINSOR_LOWER, WINSOR_UPPER])
    clipped = finite.clip(lower=float(lower), upper=float(upper))
    deviation = float(clipped.std(ddof=0))
    if deviation > 0:
      result.loc[clipped.index, "z"] = (clipped - float(clipped.mean())) / deviation
    else:
      result.loc[clipped.index, "z"] = 0.0
    result.loc[clipped.index, "rank"] = clipped.rank(method="average", pct=True)
    return result

  grouped = values.groupby(dates, sort=False, group_keys=False).apply(transform)
  if isinstance(grouped.index, pd.MultiIndex):
    grouped.index = grouped.index.get_level_values(-1)
  grouped = grouped.reindex(values.index)
  return grouped["z"], grouped["rank"]


def selection_factor_completeness(indicators: pd.DataFrame) -> pd.Series:
  """Measure observable factor inputs before cross-sectional transforms."""

  observed: dict[str, pd.Series] = {}
  for definition in SELECTION_FACTOR_DEFINITIONS:
    values = _base_series(indicators, definition)
    if definition.transform == "binary_passthrough":
      values = values.where(values.isin((0.0, 1.0)))
    observed[definition.id] = values.notna()
  if not observed:
    return pd.Series(0.0, index=indicators.index, dtype=float)
  return pd.DataFrame(observed, index=indicators.index).mean(axis=1).astype(float)


def build_selection_factor_frame(
  indicators: pd.DataFrame,
  *,
  date_column: str = "snapshot_date",
) -> pd.DataFrame:
  """Build deterministic model factors from a multi-instrument indicator panel.

  Every transform is contained within one ``date_column`` group.  Missing values
  remain missing in the value columns and receive an explicit marker; model-time
  imputation is defined by the training artifact, not hidden here.
  """

  if date_column not in indicators:
    raise ValueError(f"缺少横截面日期列: {date_column}")
  dates = pd.to_datetime(indicators[date_column], errors="coerce")
  if dates.isna().any():
    raise ValueError("横截面日期包含无效值")
  result = pd.DataFrame(index=indicators.index)
  for definition in SELECTION_FACTOR_DEFINITIONS:
    values = _base_series(indicators, definition)
    if definition.transform == "binary_passthrough":
      values = values.where(values.isin((0.0, 1.0)))
      result[f"{definition.id}__missing"] = values.isna().astype(float)
      result[definition.id] = values
      continue
    result[f"{definition.id}__missing"] = values.isna().astype(float)
    zscore, rank = _cross_sectional_transform(values, dates)
    result[f"{definition.id}__z"] = zscore
    result[f"{definition.id}__rank"] = rank
  return result.replace([np.inf, -np.inf], np.nan).round(10)


def selection_feature_columns() -> tuple[str, ...]:
  """Return ordered feature columns without needing a sample panel."""

  columns: list[str] = []
  for definition in SELECTION_FACTOR_DEFINITIONS:
    columns.append(f"{definition.id}__missing")
    if definition.transform == "binary_passthrough":
      columns.append(definition.id)
    else:
      columns.extend((f"{definition.id}__z", f"{definition.id}__rank"))
  return tuple(columns)
