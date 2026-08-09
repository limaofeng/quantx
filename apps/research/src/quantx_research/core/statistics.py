"""Grouped event statistics, date-block bootstrap, and FDR correction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

from quantx_research.core.config import StudyConfig
from quantx_research.core.models import (
  ComparisonStatistic,
  EventCurvePoint,
  GroupStatistic,
)

_ReturnKind = Literal["close_response", "next_open"]
_Benchmark = Literal["absolute", "csi300", "market_equal_weight"]


@dataclass(frozen=True)
class OutcomeSpec:
  return_kind: _ReturnKind
  horizon: int
  benchmark: _Benchmark
  column: str


class DateBlockBootstrap:
  """Reusable circular moving-block bootstrap over ordered event dates."""

  def __init__(
    self,
    event_dates: pd.Series,
    *,
    samples: int,
    seed: int,
    confidence_level: float,
  ) -> None:
    dates = pd.DatetimeIndex(
      pd.to_datetime(event_dates).dropna().unique()
    ).sort_values()
    self.dates = dates
    self.samples = samples
    self.seed = seed
    self.confidence_level = confidence_level

  def infer(
    self,
    frame: pd.DataFrame,
    value_column: str,
    *,
    block_length: int = 1,
    minimum_dates: int = 30,
  ) -> tuple[float | None, float | None, float | None]:
    sample = frame[["event_date", value_column]].copy()
    sample["event_date"] = pd.to_datetime(sample["event_date"], errors="coerce")
    sample[value_column] = pd.to_numeric(sample[value_column], errors="coerce")
    sample = sample[sample["event_date"].notna() & np.isfinite(sample[value_column])]
    if len(sample) < 2 or block_length <= 0:
      return None, None, None

    grouped = sample.groupby("event_date", observed=True)[value_column].agg(
      ["sum", "count"]
    )
    if len(grouped) < minimum_dates:
      return None, None, None
    # Resample the complete ordered trading-date calendar rather than only
    # dates on which this cell happened to be observed. Missing cell dates
    # contribute zero observations, preserving temporal gaps and clustering.
    dates = self.dates.union(pd.DatetimeIndex(grouped.index)).sort_values()
    if len(dates) < block_length:
      return None, None, None
    sums = grouped["sum"].reindex(dates, fill_value=0.0).to_numpy(dtype=float)
    counts = grouped["count"].reindex(dates, fill_value=0).to_numpy(dtype=float)
    rng = np.random.default_rng(self.seed)
    date_count = len(dates)
    block_count = int(np.ceil(date_count / block_length))
    starts = rng.integers(0, date_count, size=(self.samples, block_count))
    offsets = np.arange(block_length, dtype=np.int64)
    indices = (starts[..., None] + offsets) % date_count
    indices = indices.reshape(self.samples, -1)[:, :date_count]
    bootstrap_sums = sums[indices].sum(axis=1)
    bootstrap_counts = counts[indices].sum(axis=1)
    valid = bootstrap_counts > 0
    means = bootstrap_sums[valid] / bootstrap_counts[valid]
    if not len(means):
      return None, None, None

    tail = (1.0 - self.confidence_level) / 2.0
    ci_low, ci_high = np.quantile(means, [tail, 1.0 - tail])
    observed_mean = float(sums.sum() / counts.sum())
    null_sums = sums - observed_mean * counts
    null_means = null_sums[indices].sum(axis=1)[valid] / bootstrap_counts[valid]
    exceedances = int(np.count_nonzero(np.abs(null_means) >= abs(observed_mean)))
    # Centering imposes the zero-mean null; add-one smoothing avoids a finite
    # Monte Carlo draw reporting the impossible value p=0.
    p_value = (exceedances + 1) / (len(null_means) + 1)
    return _finite(ci_low), _finite(ci_high), _finite(p_value)


def outcome_specs(events: pd.DataFrame, config: StudyConfig) -> tuple[OutcomeSpec, ...]:
  specs: list[OutcomeSpec] = []
  for horizon in config.outcomes.horizons:
    kinds: list[tuple[_ReturnKind, str]] = []
    if config.outcomes.include_close_response:
      kinds.append(("close_response", "close"))
    if config.outcomes.include_next_open_return:
      kinds.append(("next_open", "next_open"))
    for return_kind, prefix in kinds:
      candidates: list[tuple[_Benchmark, str]] = [
        ("absolute", f"{prefix}_return_h{horizon}")
      ]
      if config.outcomes.include_benchmark_excess:
        candidates.append(("csi300", f"csi300_excess_{prefix}_h{horizon}"))
      if config.outcomes.include_cross_section_excess:
        candidates.append(("market_equal_weight", f"market_excess_{prefix}_h{horizon}"))
      specs.extend(
        OutcomeSpec(return_kind, horizon, benchmark, column)
        for benchmark, column in candidates
        if column in events
      )
  return tuple(specs)


def calculate_comparison_statistics(
  analysis_sample: pd.DataFrame,
  config: StudyConfig,
  *,
  bootstrap: DateBlockBootstrap | None = None,
  apply_fdr: bool = True,
) -> list[ComparisonStatistic]:
  """Compare shock and preregistered normal-volume cohorts by date and position."""
  required = {
    "event_date",
    "is_primary_shock_event",
    "price_position_bin",
    "relative_volume",
  }
  missing = sorted(required.difference(analysis_sample.columns))
  if missing:
    raise ValueError(
      f"analysis sample is missing comparison columns: {', '.join(missing)}"
    )
  positions = _position_categories(analysis_sample["price_position_bin"])
  bootstrap = bootstrap or DateBlockBootstrap(
    analysis_sample["event_date"],
    samples=config.statistics.bootstrap_samples,
    seed=config.statistics.random_seed,
    confidence_level=config.statistics.confidence_level,
  )
  results: list[ComparisonStatistic] = []
  for spec in outcome_specs(analysis_sample, config):
    results.extend(
      _comparison_for_outcome(
        analysis_sample,
        spec,
        positions=positions,
        config=config,
        bootstrap=bootstrap,
      )
    )
  return (
    apply_comparison_fdr(results, alpha=config.statistics.fdr_alpha)
    if apply_fdr
    else results
  )


def _comparison_for_outcome(
  analysis_sample: pd.DataFrame,
  spec: OutcomeSpec,
  *,
  positions: tuple[str, ...],
  config: StudyConfig,
  bootstrap: DateBlockBootstrap,
) -> list[ComparisonStatistic]:
  frame = analysis_sample[
    [
      "event_date",
      "is_primary_shock_event",
      "price_position_bin",
      "relative_volume",
      spec.column,
    ]
  ].copy()
  frame["event_date"] = pd.to_datetime(frame["event_date"], errors="coerce")
  frame["price_position_bin"] = frame["price_position_bin"].astype("string")
  frame["relative_volume"] = pd.to_numeric(frame["relative_volume"], errors="coerce")
  frame["value"] = pd.to_numeric(frame[spec.column], errors="coerce")
  frame = frame[
    frame["event_date"].notna()
    & frame["price_position_bin"].notna()
    & np.isfinite(frame["relative_volume"])
    & np.isfinite(frame["value"])
  ].copy()
  shock = frame["is_primary_shock_event"].fillna(False).astype(bool)
  normal = frame["relative_volume"].between(
    config.event.normal_relative_volume_min,
    config.event.normal_relative_volume_max,
    inclusive="left",
  )
  frame["cohort"] = np.select(
    [shock, normal],
    ["shock", "normal"],
    default="other",
  )
  frame = frame[frame["cohort"] != "other"]

  results: list[ComparisonStatistic] = []
  daily_by_position: dict[str, pd.DataFrame] = {}
  for position in positions:
    positioned = frame[frame["price_position_bin"] == position]
    daily = (
      positioned.groupby(["event_date", "cohort"], observed=True)["value"]
      .mean()
      .unstack("cohort")
      .reindex(columns=["shock", "normal"])
      .dropna()
      .sort_index()
    )
    daily["spread"] = daily["shock"] - daily["normal"]
    daily_by_position[position] = daily
    results.append(
      _comparison_result(
        positioned,
        daily,
        spec,
        dimensions={
          "comparison": "shock_minus_normal",
          "price_position_bin": position,
        },
        config=config,
        bootstrap=bootstrap,
      )
    )

  if positions:
    low_position = positions[0]
    high_position = positions[-1]
    low = daily_by_position.get(low_position, pd.DataFrame())
    high = daily_by_position.get(high_position, pd.DataFrame())
    common_dates = low.index.intersection(high.index)
    interaction = pd.DataFrame(index=common_dates)
    if len(common_dates):
      interaction["shock"] = (
        high.loc[common_dates, "shock"] - low.loc[common_dates, "shock"]
      )
      interaction["normal"] = (
        high.loc[common_dates, "normal"] - low.loc[common_dates, "normal"]
      )
      interaction["spread"] = interaction["shock"] - interaction["normal"]
    positioned = frame[frame["price_position_bin"].isin([low_position, high_position])]
    results.append(
      _comparison_result(
        positioned,
        interaction,
        spec,
        dimensions={
          "comparison": "high_minus_low",
          "price_position_bin": f"{high_position}_minus_{low_position}",
        },
        config=config,
        bootstrap=bootstrap,
      )
    )
  return results


def _comparison_result(
  source: pd.DataFrame,
  daily: pd.DataFrame,
  spec: OutcomeSpec,
  *,
  dimensions: dict[str, str],
  config: StudyConfig,
  bootstrap: DateBlockBootstrap,
) -> ComparisonStatistic:
  paired_dates = pd.DatetimeIndex(daily.index)
  participating = source[source["event_date"].isin(paired_dates)]
  shock_count = int((participating["cohort"] == "shock").sum())
  normal_count = int((participating["cohort"] == "normal").sum())
  unique_dates = len(paired_dates)
  ci_low: float | None = None
  ci_high: float | None = None
  p_value: float | None = None
  minimum_dates = config.statistics.minimum_inference_dates
  if unique_dates >= minimum_dates and "spread" in daily:
    spread_frame = daily[["spread"]].rename_axis("event_date").reset_index()
    ci_low, ci_high, p_value = bootstrap.infer(
      spread_frame,
      "spread",
      block_length=config.statistics.block_length(spec.horizon),
      minimum_dates=minimum_dates,
    )
  return ComparisonStatistic(
    dimensions=dimensions,
    return_kind=spec.return_kind,
    horizon=spec.horizon,
    benchmark=spec.benchmark,
    shock_sample_size=shock_count,
    normal_sample_size=normal_count,
    unique_dates=unique_dates,
    shock_mean=_finite(daily["shock"].mean())
    if "shock" in daily and len(daily)
    else None,
    shock_median=_finite(daily["shock"].median())
    if "shock" in daily and len(daily)
    else None,
    normal_mean=_finite(daily["normal"].mean())
    if "normal" in daily and len(daily)
    else None,
    normal_median=_finite(daily["normal"].median())
    if "normal" in daily and len(daily)
    else None,
    spread_mean=_finite(daily["spread"].mean())
    if "spread" in daily and len(daily)
    else None,
    spread_median=_finite(daily["spread"].median())
    if "spread" in daily and len(daily)
    else None,
    ci_low=ci_low,
    ci_high=ci_high,
    p_value=p_value,
  )


def apply_comparison_fdr(
  statistics: list[ComparisonStatistic],
  *,
  alpha: float,
) -> list[ComparisonStatistic]:
  eligible = [
    (index, float(statistic.p_value))
    for index, statistic in enumerate(statistics)
    if statistic.p_value is not None
  ]
  if not eligible:
    return statistics
  eligible.sort(key=lambda item: item[1])
  count = len(eligible)
  adjusted = [1.0] * count
  running = 1.0
  for position in range(count - 1, -1, -1):
    running = min(running, eligible[position][1] * count / (position + 1))
    adjusted[position] = min(1.0, running)
  updates = {
    original_index: q_value for (original_index, _), q_value in zip(eligible, adjusted)
  }
  return [
    statistic.model_copy(
      update={
        "q_value": updates[index],
        "significant": updates[index] <= alpha,
      }
    )
    if index in updates
    else statistic
    for index, statistic in enumerate(statistics)
  ]


def _position_categories(values: pd.Series) -> tuple[str, ...]:
  if isinstance(values.dtype, pd.CategoricalDtype):
    return tuple(str(value) for value in values.cat.categories)
  observed = tuple(
    str(value) for value in values.dropna().astype("string").drop_duplicates().tolist()
  )
  preferred = tuple(value for value in ("low", "mid", "high") if value in observed)
  return preferred + tuple(value for value in observed if value not in preferred)


def calculate_grouped_statistics(
  events: pd.DataFrame,
  config: StudyConfig,
  *,
  dimensions: tuple[str, ...] = (
    "rvol_bin",
    "price_position_bin",
    "event_direction",
  ),
  bootstrap: DateBlockBootstrap | None = None,
) -> list[GroupStatistic]:
  """Calculate all configured outcomes for observed condition cells."""
  missing = sorted(set(dimensions).difference(events.columns))
  if missing:
    raise ValueError(f"events are missing grouping columns: {', '.join(missing)}")
  bootstrap = bootstrap or DateBlockBootstrap(
    events["event_date"],
    samples=config.statistics.bootstrap_samples,
    seed=config.statistics.random_seed,
    confidence_level=config.statistics.confidence_level,
  )
  specs = outcome_specs(events, config)
  results: list[GroupStatistic] = []

  grouped = events.groupby(
    list(dimensions),
    observed=True,
    dropna=False,
    sort=True,
  )
  for group_key, group in grouped:
    key_values = group_key if isinstance(group_key, tuple) else (group_key,)
    dimension_values = {
      name: _category(value) for name, value in zip(dimensions, key_values)
    }
    for spec in specs:
      results.append(
        _group_statistic(
          group,
          spec,
          dimensions=dimension_values,
          bootstrap=bootstrap,
          minimum_cell_samples=config.statistics.minimum_cell_samples,
          minimum_inference_dates=config.statistics.minimum_inference_dates,
          block_length=config.statistics.block_length(spec.horizon),
        )
      )

  results.extend(_special_event_statistics(events, config, specs, bootstrap))
  return apply_fdr(
    results,
    alpha=config.statistics.fdr_alpha,
    minimum_cell_samples=config.statistics.minimum_cell_samples,
  )


def calculate_event_curve(
  events: pd.DataFrame,
  config: StudyConfig,
  *,
  bootstrap: DateBlockBootstrap | None = None,
) -> list[EventCurvePoint]:
  bootstrap = bootstrap or DateBlockBootstrap(
    events["event_date"],
    samples=config.statistics.bootstrap_samples,
    seed=config.statistics.random_seed,
    confidence_level=config.statistics.confidence_level,
  )
  result: list[EventCurvePoint] = []
  for spec in outcome_specs(events, config):
    values = _finite_values(events[spec.column])
    valid_dates = _valid_outcome_dates(events, spec.column)
    ci_low, ci_high, _ = bootstrap.infer(
      events,
      spec.column,
      block_length=config.statistics.block_length(spec.horizon),
      minimum_dates=config.statistics.minimum_inference_dates,
    )
    result.append(
      EventCurvePoint(
        return_kind=spec.return_kind,
        horizon=spec.horizon,
        benchmark=spec.benchmark,
        sample_size=int(len(values)),
        unique_dates=valid_dates,
        mean=_finite(values.mean()) if len(values) else None,
        median=_finite(values.median()) if len(values) else None,
        positive_rate=_finite((values > 0).mean()) if len(values) else None,
        ci_low=ci_low,
        ci_high=ci_high,
      )
    )
  return result


def calculate_robustness_statistics(
  events: pd.DataFrame,
  config: StudyConfig,
  *,
  bootstrap: DateBlockBootstrap | None = None,
) -> dict[str, list[GroupStatistic]]:
  bootstrap = bootstrap or DateBlockBootstrap(
    events["event_date"],
    samples=config.statistics.bootstrap_samples,
    seed=config.statistics.random_seed,
    confidence_level=config.statistics.confidence_level,
  )
  result: dict[str, list[GroupStatistic]] = {}
  amount_events = events[
    pd.to_numeric(events.get("relative_amount"), errors="coerce")
    >= config.event.relative_volume_threshold
  ]
  if not amount_events.empty:
    result["relative_amount_shock"] = calculate_grouped_statistics(
      amount_events,
      config,
      dimensions=("price_position_bin", "event_direction"),
      bootstrap=bootstrap,
    )

  zscore_events = events[
    pd.to_numeric(events.get("log_volume_zscore"), errors="coerce")
    >= config.event.log_volume_zscore_threshold
  ]
  if not zscore_events.empty:
    result["log_volume_zscore"] = calculate_grouped_statistics(
      zscore_events,
      config,
      dimensions=("price_position_bin", "event_direction"),
      bootstrap=bootstrap,
    )
  return result


def apply_fdr(
  statistics: list[GroupStatistic],
  *,
  alpha: float,
  minimum_cell_samples: int,
) -> list[GroupStatistic]:
  """Apply Benjamini-Hochberg to eligible cells and return updated models."""
  eligible = [
    (index, statistic.p_value)
    for index, statistic in enumerate(statistics)
    if statistic.p_value is not None and statistic.sample_size >= minimum_cell_samples
  ]
  if not eligible:
    return statistics

  eligible.sort(key=lambda item: float(item[1]))
  count = len(eligible)
  q_values = [0.0] * count
  running = 1.0
  for position in range(count - 1, -1, -1):
    _, p_value = eligible[position]
    raw_q = float(p_value) * count / (position + 1)
    running = min(running, raw_q)
    q_values[position] = min(1.0, running)

  updates = {
    original_index: q_value for (original_index, _), q_value in zip(eligible, q_values)
  }
  return [
    statistic.model_copy(
      update={
        "q_value": updates[index],
        "significant": updates[index] <= alpha,
      }
    )
    if index in updates
    else statistic.model_copy(update={"q_value": None, "significant": None})
    for index, statistic in enumerate(statistics)
  ]


def _group_statistic(
  group: pd.DataFrame,
  spec: OutcomeSpec,
  *,
  dimensions: dict[str, str],
  bootstrap: DateBlockBootstrap,
  minimum_cell_samples: int,
  minimum_inference_dates: int,
  block_length: int,
) -> GroupStatistic:
  values = _finite_values(group[spec.column])
  unique_dates = _valid_outcome_dates(group, spec.column)
  ci_low, ci_high, p_value = bootstrap.infer(
    group,
    spec.column,
    block_length=max(spec.horizon, block_length),
    minimum_dates=minimum_inference_dates,
  )
  if len(values) < minimum_cell_samples or unique_dates < minimum_inference_dates:
    ci_low = None
    ci_high = None
    p_value = None

  excursion_prefix = "close" if spec.return_kind == "close_response" else "next_open"
  mae_column = f"mae_{excursion_prefix}_h{spec.horizon}"
  mfe_column = f"mfe_{excursion_prefix}_h{spec.horizon}"
  mae = (
    _finite_values(group[mae_column]) if mae_column in group else pd.Series(dtype=float)
  )
  mfe = (
    _finite_values(group[mfe_column]) if mfe_column in group else pd.Series(dtype=float)
  )
  quantiles = values.quantile([0.05, 0.25, 0.75, 0.95]) if len(values) else {}

  return GroupStatistic(
    dimensions=dimensions,
    return_kind=spec.return_kind,
    horizon=spec.horizon,
    benchmark=spec.benchmark,
    sample_size=int(len(values)),
    unique_dates=unique_dates,
    mean=_finite(values.mean()) if len(values) else None,
    median=_finite(values.median()) if len(values) else None,
    positive_rate=_finite((values > 0).mean()) if len(values) else None,
    p05=_finite(quantiles.get(0.05)) if len(values) else None,
    p25=_finite(quantiles.get(0.25)) if len(values) else None,
    p75=_finite(quantiles.get(0.75)) if len(values) else None,
    p95=_finite(quantiles.get(0.95)) if len(values) else None,
    mae_mean=_finite(mae.mean()) if len(mae) else None,
    mfe_mean=_finite(mfe.mean()) if len(mfe) else None,
    ci_low=ci_low,
    ci_high=ci_high,
    p_value=p_value,
  )


def _special_event_statistics(
  events: pd.DataFrame,
  config: StudyConfig,
  specs: tuple[OutcomeSpec, ...],
  bootstrap: DateBlockBootstrap,
) -> list[GroupStatistic]:
  result: list[GroupStatistic] = []
  for column, label in (
    ("is_volume_breakout", "volume_breakout"),
    ("is_high_position_stall", "high_position_stall"),
  ):
    if column not in events:
      continue
    subset = events[events[column].fillna(False).astype(bool)]
    if subset.empty:
      continue
    for spec in specs:
      result.append(
        _group_statistic(
          subset,
          spec,
          dimensions={"event_type": label},
          bootstrap=bootstrap,
          minimum_cell_samples=config.statistics.minimum_cell_samples,
          minimum_inference_dates=config.statistics.minimum_inference_dates,
          block_length=config.statistics.block_length(spec.horizon),
        )
      )
  return result


def _finite_values(values: pd.Series) -> pd.Series:
  numeric = pd.to_numeric(values, errors="coerce")
  return numeric[np.isfinite(numeric)]


def _valid_outcome_dates(frame: pd.DataFrame, value_column: str) -> int:
  dates = pd.to_datetime(frame["event_date"], errors="coerce")
  values = pd.to_numeric(frame[value_column], errors="coerce")
  return int(dates[np.isfinite(values) & dates.notna()].nunique())


def _finite(value: object) -> float | None:
  try:
    number = float(value)
  except (TypeError, ValueError):
    return None
  return number if np.isfinite(number) else None


def _category(value: object) -> str:
  if pd.isna(value):
    return "unknown"
  return str(value)
