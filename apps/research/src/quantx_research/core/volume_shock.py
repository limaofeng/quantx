"""No-lookahead feature and event construction for volume shocks."""

from __future__ import annotations

import numpy as np
import pandas as pd

from quantx_research.core.config import StudyConfig
from quantx_research.core.forward_returns import (
  add_forward_outcomes,
  required_outcome_columns,
)

_REQUIRED_COLUMNS = (
  "stock_code",
  "time",
  "open",
  "high",
  "low",
  "close",
  "volume",
)


def normalize_market_panel(panel: pd.DataFrame) -> pd.DataFrame:
  """Normalize adapter output into the study's stable tabular contract."""
  if not isinstance(panel, pd.DataFrame):
    raise TypeError("panel must be a pandas DataFrame")
  frame = panel.copy()
  aliases = {
    "code": "stock_code",
    "date": "time",
  }
  for source, target in aliases.items():
    if target not in frame and source in frame:
      frame = frame.rename(columns={source: target})

  missing = sorted(set(_REQUIRED_COLUMNS).difference(frame.columns))
  if missing:
    raise ValueError(f"market panel is missing columns: {', '.join(missing)}")

  frame["stock_code"] = frame["stock_code"].astype(str).str.strip().str.upper()
  frame["event_date"] = _normalize_dates(frame["time"])
  numeric_columns = [
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "suspend_flag",
  ]
  for column in numeric_columns:
    if column in frame:
      frame[column] = pd.to_numeric(frame[column], errors="coerce")
  if "amount" not in frame:
    frame["amount"] = np.nan
  if "suspend_flag" not in frame:
    frame["suspend_flag"] = 0

  for column in ("open_date", "expire_date"):
    if column in frame:
      frame[column] = pd.to_datetime(frame[column], errors="coerce").dt.normalize()
  if "adjustment_valid" in frame:
    frame["adjustment_valid"] = frame["adjustment_valid"].astype("boolean")

  frame = frame.dropna(subset=["stock_code", "event_date"])
  frame = frame.sort_values(["stock_code", "event_date"])
  frame = frame.drop_duplicates(["stock_code", "event_date"], keep="last")
  return frame.reset_index(drop=True)


def build_volume_shock_events(
  panel: pd.DataFrame,
  config: StudyConfig,
  *,
  benchmark: pd.DataFrame | None = None,
  cooldown_days: int | None = None,
) -> pd.DataFrame:
  """Construct eligible abnormal-volume events and their forward outcomes.

  All explanatory features are available at T close or earlier:

  * relative-volume baselines exclude T;
  * price position and controls stop at T-1;
  * the T return/direction uses only T close;
  * outcomes begin after T and are never used for feature construction.
  """
  analysis_sample = build_volume_analysis_sample(
    panel,
    config,
    benchmark=benchmark,
  )
  return volume_shock_events_from_sample(
    analysis_sample,
    config,
    cooldown_days=cooldown_days,
  )


def build_volume_analysis_sample(
  panel: pd.DataFrame,
  config: StudyConfig,
  *,
  benchmark: pd.DataFrame | None = None,
) -> pd.DataFrame:
  """Build every eligible stock-day before applying the shock threshold."""
  frame = add_volume_features(panel, config)
  if frame.empty:
    return _empty_event_frame(frame)
  resolved_horizons = config.outcomes.horizons
  frame = add_forward_outcomes(
    frame,
    resolved_horizons,
    benchmark=benchmark,
    include_close_response=config.outcomes.include_close_response,
    include_next_open_return=config.outcomes.include_next_open_return,
    include_benchmark_excess=config.outcomes.include_benchmark_excess,
    include_cross_section_excess=config.outcomes.include_cross_section_excess,
  )
  return select_volume_analysis_sample(frame, config)


def add_volume_features(
  panel: pd.DataFrame,
  config: StudyConfig,
) -> pd.DataFrame:
  """Normalize complete per-stock histories and add features known by T close."""
  frame = normalize_market_panel(panel)
  return _add_features(frame, config) if not frame.empty else frame


def select_volume_analysis_sample(
  frame: pd.DataFrame,
  config: StudyConfig,
  *,
  start_date: pd.Timestamp | None = None,
  end_date: pd.Timestamp | None = None,
) -> pd.DataFrame:
  """Apply canonical eligibility and cooldown rules to an outcome panel."""
  if frame.empty:
    return _empty_event_frame(frame)
  if start_date is None or end_date is None:
    start_date, end_date = analysis_bounds(frame["event_date"], config)
  eligible = _eligible_mask(frame, config, start_date=start_date, end_date=end_date)

  maximum_horizon = max(config.outcomes.horizons)
  complete_columns = [
    column
    for column in required_outcome_columns(
      (maximum_horizon,),
      include_close_response=config.outcomes.include_close_response,
      include_next_open_return=config.outcomes.include_next_open_return,
    )
    if column in frame
  ]
  if complete_columns:
    eligible &= frame[complete_columns].notna().all(axis=1)

  sample = frame.loc[eligible].copy()
  sample["is_abnormal_volume"] = (
    sample["relative_volume"] >= config.event.relative_volume_threshold
  )
  sample["is_normal_volume"] = sample["relative_volume"].between(
    config.event.normal_relative_volume_min,
    config.event.normal_relative_volume_max,
    inclusive="left",
  )
  threshold_candidates = sample[sample["is_abnormal_volume"]]
  primary_events = _apply_cooldown(
    threshold_candidates.copy(),
    config.event.cooldown_days,
  )
  sample["is_primary_shock_event"] = sample.index.isin(primary_events.index)
  sample["quality_flags"] = pd.Series(
    [tuple() for _ in range(len(sample))],
    index=sample.index,
    dtype=object,
  )
  sample = sample.drop(columns=["_history_rows"], errors="ignore")
  return sample.reset_index(drop=True)


def volume_shock_events_from_sample(
  analysis_sample: pd.DataFrame,
  config: StudyConfig,
  *,
  cooldown_days: int | None = None,
) -> pd.DataFrame:
  """Select threshold events from an already constructed eligible sample."""
  if analysis_sample.empty:
    return analysis_sample.copy()
  events = analysis_sample[
    pd.to_numeric(analysis_sample["relative_volume"], errors="coerce")
    >= config.event.relative_volume_threshold
  ].copy()
  events = _apply_cooldown(
    events,
    config.event.cooldown_days if cooldown_days is None else cooldown_days,
  )
  events["is_abnormal_volume"] = True
  events["is_primary_shock_event"] = True
  return events.reset_index(drop=True)


def apply_event_cooldown(events: pd.DataFrame, cooldown_days: int) -> pd.DataFrame:
  """Apply the stock-local market-session cooldown to prebuilt candidates."""
  required = {"stock_code", "event_date", "_session_ordinal"}
  missing = sorted(required.difference(events.columns))
  if missing:
    raise ValueError(f"events are missing cooldown columns: {', '.join(missing)}")
  return _apply_cooldown(events.copy(), cooldown_days).reset_index(drop=True)


def _add_features(frame: pd.DataFrame, config: StudyConfig) -> pd.DataFrame:
  pieces: list[pd.DataFrame] = []
  for _, stock_frame in frame.groupby("stock_code", sort=False, observed=True):
    group = stock_frame.copy()
    close = group["close"].where(group["close"] > 0)
    high = group["high"].where(group["high"] > 0)
    low = group["low"].where(group["low"] > 0)
    volume = group["volume"].where(group["volume"] > 0)
    amount = group["amount"].where(group["amount"] > 0)

    volume_window = config.event.relative_volume_window
    amount_window = config.event.relative_amount_window
    zscore_window = config.event.log_volume_zscore_window
    position_window = config.conditioning.price_position_window
    breakout_window = config.event.breakout_window

    prior_average_volume = (
      volume.shift(1).rolling(volume_window, min_periods=volume_window).mean()
    )
    prior_average_amount = (
      amount.shift(1).rolling(amount_window, min_periods=amount_window).mean()
    )
    log_volume = np.log(volume)
    prior_log_mean = (
      log_volume.shift(1).rolling(zscore_window, min_periods=zscore_window).mean()
    )
    prior_log_std = (
      log_volume.shift(1).rolling(zscore_window, min_periods=zscore_window).std(ddof=1)
    )

    prior_high = (
      high.shift(1).rolling(position_window, min_periods=position_window).max()
    )
    prior_low = low.shift(1).rolling(position_window, min_periods=position_window).min()
    prior_close = close.shift(1)
    price_range = (prior_high - prior_low).where(lambda values: values > 0)

    group["relative_volume"] = volume.div(prior_average_volume)
    group["relative_amount"] = amount.div(prior_average_amount)
    group["log_volume_zscore"] = log_volume.sub(prior_log_mean).div(
      prior_log_std.where(prior_log_std > 0)
    )
    group["price_position"] = (
      prior_close.sub(prior_low).div(price_range).clip(lower=0.0, upper=1.0)
    )
    group["event_return"] = close.div(prior_close).sub(1.0)
    group["momentum_20"] = close.shift(1).div(close.shift(21)).sub(1.0)
    daily_return = close.pct_change(fill_method=None)
    group["volatility_20"] = (
      daily_return.shift(1).rolling(20, min_periods=20).std(ddof=1)
    )
    group["average_amount_20"] = amount.shift(1).rolling(20, min_periods=20).mean()
    group["log_average_amount_20"] = np.log1p(group["average_amount_20"])

    previous_breakout_high = (
      high.shift(1).rolling(breakout_window, min_periods=breakout_window).max()
    )
    group["is_volume_breakout"] = (
      group["relative_volume"] >= config.event.relative_volume_threshold
    ) & (close > previous_breakout_high)
    threshold = config.event.flat_return_threshold_pct / 100.0
    group["event_direction"] = np.select(
      [group["event_return"] < -threshold, group["event_return"] > threshold],
      ["down", "up"],
      default="flat",
    )

    group["relative_volume_bin"] = _cut_relative_volume(
      group["relative_volume"], config.event.relative_volume_bins
    )
    group["price_position_bin"] = _cut_price_position(
      group["price_position"], config.conditioning.price_position_bins
    )
    high_position_start = config.conditioning.price_position_bins[-2]
    group["is_high_position_stall"] = (
      (group["relative_volume"] >= config.event.relative_volume_threshold)
      & (group["price_position"] >= high_position_start)
      & (group["event_return"].abs() <= threshold)
    )
    group["_history_rows"] = np.arange(len(group), dtype=np.int64)

    # Stable convenience aliases make the default v1 parquet self-describing.
    group["rvol20"] = group["relative_volume"]
    group["amount_ratio20"] = group["relative_amount"]
    group["log_volume_z60"] = group["log_volume_zscore"]
    group["price_position252"] = group["price_position"]
    group["rvol_bin"] = group["relative_volume_bin"]
    pieces.append(group)

  if not pieces:
    return frame
  return pd.concat(pieces, axis=0).sort_index()


def _eligible_mask(
  frame: pd.DataFrame,
  config: StudyConfig,
  *,
  start_date: pd.Timestamp,
  end_date: pd.Timestamp,
) -> pd.Series:
  mask = (
    frame["event_date"].between(start_date, end_date, inclusive="both")
    & (frame["_history_rows"] >= config.required_lookback)
    & (frame["_history_rows"] >= config.universe.minimum_listing_days)
    & frame[
      [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "relative_volume",
        "price_position",
        "event_return",
      ]
    ]
    .notna()
    .all(axis=1)
    & (frame[["open", "high", "low", "close", "volume"]] > 0).all(axis=1)
    & (frame["suspend_flag"].fillna(1) != 1)
  )
  prices = frame[["open", "high", "low", "close"]]
  mask &= frame["high"] >= prices[["open", "low", "close"]].max(axis=1)
  mask &= frame["low"] <= prices[["open", "high", "close"]].min(axis=1)

  if "open_date" in frame:
    mask &= frame["open_date"].isna() | (frame["event_date"] >= frame["open_date"])
  if "expire_date" in frame:
    mask &= frame["expire_date"].isna() | (frame["event_date"] <= frame["expire_date"])
  if (
    config.quality.exclude_corporate_action_windows_without_adjustment
    and "adjustment_valid" in frame
  ):
    mask &= frame["adjustment_valid"].fillna(False)
  return mask


def _empty_event_frame(frame: pd.DataFrame) -> pd.DataFrame:
  result = frame.copy()
  float_columns = (
    "relative_volume",
    "relative_amount",
    "log_volume_zscore",
    "price_position",
    "event_return",
    "momentum_20",
    "volatility_20",
    "average_amount_20",
    "log_average_amount_20",
    "rvol20",
    "amount_ratio20",
    "log_volume_z60",
    "price_position252",
  )
  for column in float_columns:
    result[column] = pd.Series(dtype=float)
  for column in (
    "event_direction",
    "relative_volume_bin",
    "price_position_bin",
    "rvol_bin",
  ):
    result[column] = pd.Series(dtype="string")
  for column in (
    "is_abnormal_volume",
    "is_primary_shock_event",
    "is_normal_volume",
    "is_volume_breakout",
    "is_high_position_stall",
  ):
    result[column] = pd.Series(dtype=bool)
  result["_session_ordinal"] = pd.Series(dtype="Int64")
  result["quality_flags"] = pd.Series(dtype=object)
  return result


def analysis_bounds(
  event_dates: pd.Series, config: StudyConfig
) -> tuple[pd.Timestamp, pd.Timestamp]:
  if event_dates.empty:
    return pd.Timestamp.min.normalize(), pd.Timestamp.max.normalize()
  if config.date_range is not None:
    return pd.Timestamp(config.date_range[0]), pd.Timestamp(config.date_range[1])

  if config.universe.end_date == "latest":
    end_date = pd.Timestamp(event_dates.max())
  else:
    end_date = pd.Timestamp(config.universe.end_date)
  start_date = end_date - pd.DateOffset(years=config.universe.lookback_years)
  return start_date.normalize(), end_date.normalize()


def _apply_cooldown(events: pd.DataFrame, cooldown_days: int) -> pd.DataFrame:
  if cooldown_days <= 0 or events.empty:
    return events
  keep_indices: list[int] = []
  for _, group in events.sort_values(["stock_code", "_session_ordinal"]).groupby(
    "stock_code", sort=False, observed=True
  ):
    last_kept: int | None = None
    for index, ordinal in zip(group.index, group["_session_ordinal"]):
      current = int(ordinal)
      if last_kept is None or current - last_kept > cooldown_days:
        keep_indices.append(index)
        last_kept = current
  return events.loc[keep_indices].sort_values(["event_date", "stock_code"])


def _cut_relative_volume(
  values: pd.Series, configured_edges: tuple[float, ...]
) -> pd.Series:
  edges = list(configured_edges)
  if not np.isinf(edges[-1]):
    edges.append(np.inf)
  labels = [
    f"[{left:g},{right:g})" if np.isfinite(right) else f"[{left:g},+inf)"
    for left, right in zip(edges, edges[1:])
  ]
  return pd.cut(values, bins=edges, labels=labels, right=False, include_lowest=True)


def _cut_price_position(
  values: pd.Series, configured_edges: tuple[float, ...]
) -> pd.Series:
  edges = list(configured_edges)
  edges[-1] = np.inf
  if tuple(configured_edges) == (0.0, 0.3, 0.7, 1.0):
    labels = ["low", "mid", "high"]
  else:
    labels = [
      f"[{left:.2f},{right:.2f})" if np.isfinite(right) else f"[{left:.2f},1.00]"
      for left, right in zip(edges, edges[1:])
    ]
  return pd.cut(values, bins=edges, labels=labels, right=False, include_lowest=True)


def _normalize_dates(values: pd.Series) -> pd.Series:
  parsed = pd.to_datetime(values, errors="coerce", utc=True)
  return parsed.dt.tz_convert("Asia/Shanghai").dt.tz_localize(None).dt.normalize()


def event_record_columns(config: StudyConfig) -> tuple[str, ...]:
  """Return stable event-level columns, including configured outcomes."""
  base = (
    "stock_code",
    "event_date",
    "relative_volume",
    "relative_amount",
    "log_volume_zscore",
    "price_position",
    "event_return",
    "event_direction",
    "relative_volume_bin",
    "price_position_bin",
    "is_abnormal_volume",
    "is_primary_shock_event",
    "is_normal_volume",
    "is_volume_breakout",
    "is_high_position_stall",
    "momentum_20",
    "volatility_20",
    "average_amount_20",
    "log_average_amount_20",
    "_session_ordinal",
    "quality_flags",
  )
  outcome_columns: list[str] = []
  for horizon in config.outcomes.horizons:
    if config.outcomes.include_close_response:
      outcome_columns.extend(
        (
          f"close_return_h{horizon}",
          f"mfe_close_h{horizon}",
          f"mae_close_h{horizon}",
        )
      )
    if config.outcomes.include_next_open_return:
      outcome_columns.extend(
        (
          f"next_open_return_h{horizon}",
          f"mfe_next_open_h{horizon}",
          f"mae_next_open_h{horizon}",
        )
      )
    if config.outcomes.include_benchmark_excess:
      if config.outcomes.include_close_response:
        outcome_columns.append(f"csi300_excess_close_h{horizon}")
      if config.outcomes.include_next_open_return:
        outcome_columns.append(f"csi300_excess_next_open_h{horizon}")
    if config.outcomes.include_cross_section_excess:
      if config.outcomes.include_close_response:
        outcome_columns.append(f"market_excess_close_h{horizon}")
      if config.outcomes.include_next_open_return:
        outcome_columns.append(f"market_excess_next_open_h{horizon}")
  return base + tuple(outcome_columns)
