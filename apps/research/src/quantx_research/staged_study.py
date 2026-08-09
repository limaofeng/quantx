"""Statistics over disk-backed volume-shock samples using narrow projections."""

from __future__ import annotations

import gc

import pandas as pd

from quantx_research.core import (
  DateBlockBootstrap,
  StudyConfig,
  StudyResult,
  apply_comparison_fdr,
  calculate_comparison_statistics,
  calculate_event_curve,
  calculate_grouped_statistics,
  calculate_robustness_statistics,
  event_record_columns,
)
from quantx_research.runtime_memory import RuntimeMemoryMonitor
from quantx_research.staged_regression import run_staged_panel_regressions
from quantx_research.staging import (
  StagedVolumeDataset,
  comparison_projection_columns,
  estimate_projection_bytes,
  grouped_projection_columns,
  load_staged_projection,
)
from quantx_research.studies.volume_shock import _study_warnings


def analyze_staged_volume_sample(
  staged: StagedVolumeDataset,
  config: StudyConfig,
  *,
  monitor: RuntimeMemoryMonitor,
) -> tuple[pd.DataFrame, StudyResult]:
  """Produce the legacy StudyResult without loading the 71-column full sample."""
  bootstrap = _bootstrap(staged, config)
  event_columns = tuple(
    dict.fromkeys(
      (
        *event_record_columns(config),
        "rvol_bin",
        "adjustment_valid",
        "_shock_cooldown_5d",
        "_shock_cooldown_20d",
      )
    )
  )
  event_pool = load_staged_projection(
    staged,
    columns=event_columns,
    name="load_event_pool",
    monitor=monitor,
    predicate=_retained_event_candidate_mask,
  )
  primary_mask = event_pool["is_primary_shock_event"].fillna(False).astype(bool)
  monitor.guard(
    "select_primary_events",
    estimated_increment_bytes=estimate_projection_bytes(
      int(primary_mask.sum()),
      len(event_pool.columns),
    ),
  )
  primary_events = (
    event_pool[primary_mask]
    .copy()
    .sort_values(["event_date", "stock_code"], kind="stable")
    .reset_index(drop=True)
  )
  del primary_mask
  warnings = _study_warnings(primary_events, config)
  grouped_statistics = (
    calculate_grouped_statistics(primary_events, config, bootstrap=bootstrap)
    if not primary_events.empty
    else []
  )
  event_curve = (
    calculate_event_curve(primary_events, config, bootstrap=bootstrap)
    if not primary_events.empty
    else []
  )
  cooldown_robustness: dict[str, list] = {}
  for cooldown in (5, 20):
    if cooldown == config.event.cooldown_days:
      continue
    identity = f"_shock_cooldown_{cooldown}d"
    sensitivity_events = event_pool[event_pool[identity].fillna(False).astype(bool)]
    cooldown_robustness[f"cooldown_{cooldown}d"] = (
      calculate_grouped_statistics(
        sensitivity_events,
        config,
        dimensions=("price_position_bin", "event_direction"),
        bootstrap=bootstrap,
      )
      if not sensitivity_events.empty
      else []
    )
  del event_pool
  gc.collect()
  monitor.sample()

  comparison: list = []
  sensitivity_keys = [
    f"cooldown_{cooldown}d"
    for cooldown in (5, 20)
    if cooldown != config.event.cooldown_days
  ]
  comparison_sensitivity: dict[str, list] = {key: [] for key in sensitivity_keys}
  # Normal-volume controls can cover most of the 8.5m-row panel. Load only one
  # horizon's outcomes at a time so this phase never materializes the original
  # wide comparison projection.
  for horizon in config.outcomes.horizons:
    comparison_frame = load_staged_projection(
      staged,
      columns=comparison_projection_columns(config, horizons=(horizon,)),
      name=f"load_comparison_projection_h{horizon}",
      monitor=monitor,
      predicate=lambda frame: _comparison_candidate_mask(frame, config),
    )
    if comparison_frame.empty:
      continue
    comparison.extend(
      calculate_comparison_statistics(
        comparison_frame,
        config,
        bootstrap=bootstrap,
        apply_fdr=False,
      )
    )
    primary_identity = comparison_frame["is_primary_shock_event"].copy()
    for key in sensitivity_keys:
      cooldown = int(key.removeprefix("cooldown_").removesuffix("d"))
      comparison_frame["is_primary_shock_event"] = comparison_frame[
        f"_shock_cooldown_{cooldown}d"
      ]
      comparison_sensitivity[key].extend(
        calculate_comparison_statistics(
          comparison_frame,
          config,
          bootstrap=bootstrap,
          apply_fdr=False,
        )
      )
    comparison_frame["is_primary_shock_event"] = primary_identity
    del primary_identity, comparison_frame
    gc.collect()
    monitor.checkpoint(f"comparison_h{horizon}_complete")
  comparison = apply_comparison_fdr(
    comparison,
    alpha=config.statistics.fdr_alpha,
  )
  comparison_sensitivity = {
    key: apply_comparison_fdr(
      statistics,
      alpha=config.statistics.fdr_alpha,
    )
    for key, statistics in comparison_sensitivity.items()
  }

  regressions = run_staged_panel_regressions(staged, config, monitor=monitor)
  gc.collect()
  monitor.sample()

  robustness_frame = load_staged_projection(
    staged,
    columns=grouped_projection_columns(config),
    name="load_robustness_projection",
    monitor=monitor,
    predicate=lambda frame: _robustness_candidate_mask(frame, config),
  )
  robustness = (
    calculate_robustness_statistics(
      robustness_frame,
      config,
      bootstrap=bootstrap,
    )
    if not robustness_frame.empty
    else {}
  )
  robustness.update(cooldown_robustness)
  del robustness_frame
  gc.collect()
  monitor.sample()

  return primary_events, StudyResult(
    study_id=config.study_id,
    version=config.version,
    event_count=len(primary_events),
    analysis_sample_count=staged.analysis_sample_count,
    grouped_statistics=grouped_statistics,
    event_curve=event_curve,
    comparison=comparison,
    comparison_sensitivity=comparison_sensitivity,
    regressions=regressions,
    robustness=robustness,
    warnings=warnings,
  )


def _bootstrap(
  staged: StagedVolumeDataset,
  config: StudyConfig,
) -> DateBlockBootstrap:
  return DateBlockBootstrap(
    pd.Series(staged.analysis_dates, dtype="datetime64[ns]"),
    samples=config.statistics.bootstrap_samples,
    seed=config.statistics.random_seed,
    confidence_level=config.statistics.confidence_level,
  )


def _comparison_candidate_mask(
  frame: pd.DataFrame,
  config: StudyConfig,
) -> pd.Series:
  relative_volume = pd.to_numeric(frame["relative_volume"], errors="coerce")
  normal = relative_volume.between(
    config.event.normal_relative_volume_min,
    config.event.normal_relative_volume_max,
    inclusive="left",
  )
  identities = frame["is_primary_shock_event"].fillna(False).astype(bool)
  for cooldown in (5, 20):
    identities |= frame[f"_shock_cooldown_{cooldown}d"].fillna(False).astype(bool)
  return normal | identities


def _retained_event_candidate_mask(frame: pd.DataFrame) -> pd.Series:
  """Keep only events retained by the main or preregistered cooldowns."""
  retained = frame["is_primary_shock_event"].fillna(False).astype(bool)
  for cooldown in (5, 20):
    retained |= frame[f"_shock_cooldown_{cooldown}d"].fillna(False).astype(bool)
  return retained


def _robustness_candidate_mask(
  frame: pd.DataFrame,
  config: StudyConfig,
) -> pd.Series:
  amount = pd.to_numeric(frame["relative_amount"], errors="coerce")
  zscore = pd.to_numeric(frame["log_volume_zscore"], errors="coerce")
  return (amount >= config.event.relative_volume_threshold) | (
    zscore >= config.event.log_volume_zscore_threshold
  )
