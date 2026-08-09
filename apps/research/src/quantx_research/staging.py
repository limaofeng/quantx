"""Bounded-memory staging pipeline for the formal volume-shock study."""

from __future__ import annotations

import gc
import hashlib
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from quantx_research.core import (
  StudyConfig,
  add_forward_outcomes,
  add_volume_features,
  analysis_bounds,
  apply_event_cooldown,
  event_record_columns,
  market_calendar,
  select_volume_analysis_sample,
)
from quantx_research.data import (
  DataQualityReport,
  DividendFactorCoverageError,
  DividendFactorCoverageReport,
  ResearchDataSource,
  apply_dividend_adjustment,
  build_dividend_factor_coverage_report,
  build_quality_report,
  combine_quality_reports,
  normalize_daily_bars,
  normalize_dividend_factors,
  normalize_instruments,
)
from quantx_research.data.dataset_builder import (
  _append_instrument_dates,
  _deduplicate,
  _filter_default_stock_universe,
  _unique_codes,
)
from quantx_research.runtime_memory import RuntimeMemoryMonitor

_PARQUET_ROW_GROUP_SIZE = 65_536
_BATCH_WORKING_BYTES_PER_ROW = 3_072
_PROJECTION_OVERHEAD_MULTIPLIER = 4.0
_ALT_COOLDOWNS = (5, 20)
_FINGERPRINT_DATE_COLUMNS = frozenset(
  {
    "event_date",
    "expire_date",
    "open_date",
    "time",
  }
)
_FINGERPRINT_STRING_COLUMNS = frozenset(
  {
    "instrument_type",
    "market",
    "name",
    "stock_code",
  }
)
_FINGERPRINT_BOOLEAN_COLUMNS = frozenset(
  {
    "adjustment_valid",
    "listing_valid",
    "suspend_flag",
  }
)
_FINGERPRINT_NUMERIC_COLUMNS = frozenset(
  {
    "amount",
    "close",
    "dr",
    "high",
    "low",
    "open",
    "volume",
  }
)


@dataclass(slots=True)
class StagedVolumeDataset:
  """Disk-backed eligible sample plus small in-memory audit metadata."""

  directory: Path
  partitions: tuple[Path, ...]
  quality: DataQualityReport
  factor_coverage: DividendFactorCoverageReport
  instruments: pd.DataFrame
  benchmark: pd.DataFrame
  analysis_start: pd.Timestamp
  analysis_end: pd.Timestamp
  analysis_dates: tuple[pd.Timestamp, ...]
  market_calendar: tuple[pd.Timestamp, ...]
  analysis_sample_count: int
  event_count: int
  benchmark_coverage_ratio: float
  data_fingerprint: str
  resource_estimate: dict[str, Any]
  source_provenance: dict[str, Any]

  @property
  def panel_row_count(self) -> int:
    return self.quality.row_count


async def build_staged_volume_dataset(
  source: ResearchDataSource,
  config: StudyConfig,
  *,
  start: date | datetime,
  end: date | datetime,
  analysis_start: date | datetime,
  directory: str | Path,
  monitor: RuntimeMemoryMonitor,
) -> StagedVolumeDataset:
  """Build the eligible sample without ever materializing the full market panel."""
  root = Path(directory)
  features_dir = root / "features"
  outcomes_dir = root / "outcomes"
  final_dir = root / "sample"
  for path in (features_dir, outcomes_dir, final_dir):
    path.mkdir(parents=True, exist_ok=True)

  (
    requested_codes,
    instruments,
    benchmark_instrument,
    factor_coverage,
  ) = await _prepare_universe_and_factor_gate(
    source,
    config,
    start=start,
    end=end,
    analysis_start=analysis_start,
  )
  sorted_codes = tuple(sorted(requested_codes))
  batches = tuple(_batches(sorted_codes, config.runtime.batch_size))
  expected_sessions = max(
    1,
    math.ceil((pd.Timestamp(end) - pd.Timestamp(start)).days * 5 / 7) + 10,
  )
  estimated_batch_rows = min(config.runtime.batch_size, len(sorted_codes)) * (
    expected_sessions
  )
  estimated_batch_increment = estimated_batch_rows * _BATCH_WORKING_BYTES_PER_ROW
  monitor.guard(
    "load_sparse_factors",
    estimated_increment_bytes=min(estimated_batch_increment, 512 * 1024**2),
  )
  factors = normalize_dividend_factors(
    await source.load_dividend_factors(
      sorted_codes,
      start=start,
      end=end,
    )
  )

  monitor.guard(
    "load_benchmark",
    estimated_increment_bytes=128 * 1024**2,
  )
  benchmark = await _load_benchmark(
    source,
    config.universe.benchmark_code.strip().upper(),
    benchmark_instrument,
    start=start,
    end=end,
  )
  all_instruments = pd.concat(
    [instruments, benchmark_instrument],
    ignore_index=True,
    sort=False,
  ).drop_duplicates("stock_code", keep="last")

  feature_paths: list[Path] = []
  quality_reports: list[DataQualityReport] = []
  stock_dates: set[pd.Timestamp] = set()
  observed_batch_rows: list[int] = []
  hasher = _CanonicalDatasetHasher()

  for batch_index, batch_codes in enumerate(batches):
    monitor.guard(
      "stage1_load_features",
      estimated_increment_bytes=estimated_batch_increment,
    )
    bars = normalize_daily_bars(
      await source.load_daily_bars(
        batch_codes,
        start,
        end,
        batch_size=len(batch_codes),
      )
    )
    raw_panel = bars[bars["stock_code"].isin(batch_codes)].copy()
    batch_factors = factors[factors["stock_code"].isin(batch_codes)].copy()
    panel = apply_dividend_adjustment(
      raw_panel,
      batch_factors,
      mode="point_in_time",
      as_of=end,
    )
    batch_instruments = instruments[instruments["stock_code"].isin(batch_codes)].copy()
    panel = _append_instrument_dates(panel, batch_instruments)
    quality_reports.append(
      build_quality_report(
        panel,
        requested_codes=batch_codes,
        requested_start=start,
        requested_end=end,
        metadata_codes=batch_instruments["stock_code"].dropna().astype(str),
        minimum_observations=config.required_lookback,
      )
    )
    panel = _deduplicate(panel)
    hasher.update_panel(panel)
    observed_batch_rows.append(len(panel))
    featured = add_volume_features(panel, config)
    if featured.empty:
      del bars, raw_panel, batch_factors, panel, batch_instruments, featured
      gc.collect()
      monitor.sample()
      continue
    stock_dates.update(
      pd.Timestamp(value)
      for value in pd.to_datetime(featured["event_date"]).dropna().unique()
    )
    staged_features = featured.loc[
      :,
      [column for column in _feature_columns() if column in featured.columns],
    ]
    feature_path = features_dir / f"batch-{batch_index:05d}.parquet"
    _write_partition(feature_path, staged_features)
    feature_paths.append(feature_path)
    del (
      bars,
      raw_panel,
      batch_factors,
      panel,
      batch_instruments,
      featured,
      staged_features,
    )
    gc.collect()
    monitor.sample()

  stage1_feature_bytes = _paths_bytes(feature_paths)
  peak_staging_bytes = stage1_feature_bytes
  quality = combine_quality_reports(
    quality_reports,
    requested_codes=sorted_codes,
    requested_start=start,
    requested_end=end,
  )
  calendar = market_calendar(
    pd.DataFrame({"event_date": sorted(stock_dates)}),
    benchmark,
  )
  resolved_analysis_start, resolved_analysis_end = analysis_bounds(
    pd.Series(sorted(stock_dates), dtype="datetime64[ns]"),
    config,
  )

  market_parts: dict[str, list[pd.DataFrame]] = {
    column: [] for column in _absolute_return_columns(config)
  }
  outcome_paths: list[Path] = []
  for batch_index, feature_path in enumerate(feature_paths):
    monitor.guard(
      "stage2_forward_outcomes",
      estimated_increment_bytes=estimated_batch_increment,
    )
    featured = pd.read_parquet(feature_path)
    outcomes = add_forward_outcomes(
      featured,
      config.outcomes.horizons,
      benchmark=benchmark,
      calendar=calendar,
      include_close_response=config.outcomes.include_close_response,
      include_next_open_return=config.outcomes.include_next_open_return,
      include_benchmark_excess=config.outcomes.include_benchmark_excess,
      include_cross_section_excess=False,
    )
    if config.outcomes.include_cross_section_excess:
      for column in market_parts:
        if column not in outcomes:
          continue
        aggregate = outcomes.groupby("event_date", observed=True)[column].agg(
          ["sum", "count"]
        )
        market_parts[column].append(aggregate)
    sample = select_volume_analysis_sample(
      outcomes,
      config,
      start_date=resolved_analysis_start,
      end_date=resolved_analysis_end,
    )
    if sample.empty:
      feature_path.unlink(missing_ok=True)
      del featured, outcomes, sample
      gc.collect()
      monitor.sample()
      continue
    pre_market_columns = [
      column
      for column in (*event_record_columns(config), "rvol_bin", "adjustment_valid")
      if column in sample.columns and not column.startswith("market_excess_")
    ]
    outcome_path = outcomes_dir / f"batch-{batch_index:05d}.parquet"
    _write_partition(outcome_path, sample.loc[:, pre_market_columns])
    outcome_paths.append(outcome_path)
    peak_staging_bytes = max(
      peak_staging_bytes,
      _paths_bytes(feature_paths) + _paths_bytes(outcome_paths),
    )
    feature_path.unlink(missing_ok=True)
    del featured, outcomes, sample
    gc.collect()
    monitor.sample()

  stage2_outcome_bytes = _paths_bytes(outcome_paths)
  market_means = _combine_market_aggregates(market_parts)
  final_paths: list[Path] = []
  analysis_dates: set[pd.Timestamp] = set()
  analysis_sample_count = 0
  event_count = 0
  benchmark_counts = _empty_benchmark_counts(config)

  for batch_index, outcome_path in enumerate(outcome_paths):
    monitor.guard(
      "stage3_market_excess_and_cooldown",
      estimated_increment_bytes=max(
        256 * 1024**2,
        estimated_batch_increment // 2,
      ),
    )
    sample = pd.read_parquet(outcome_path)
    if config.outcomes.include_cross_section_excess:
      _add_market_excess(sample, market_means, config)
    _add_alternative_cooldown_identities(sample, config)
    if not sample.empty:
      analysis_dates.update(
        pd.Timestamp(value)
        for value in pd.to_datetime(sample["event_date"]).dropna().unique()
      )
    analysis_sample_count += len(sample)
    event_count += int(sample["is_primary_shock_event"].fillna(False).sum())
    _update_benchmark_counts(benchmark_counts, sample, config)
    final_path = final_dir / f"batch-{batch_index:05d}.parquet"
    internal_columns = [
      column
      for column in (
        *event_record_columns(config),
        "rvol_bin",
        "adjustment_valid",
        *_cooldown_identity_columns(),
      )
      if column in sample.columns
    ]
    _write_partition(final_path, sample.loc[:, internal_columns])
    final_paths.append(final_path)
    peak_staging_bytes = max(
      peak_staging_bytes,
      _paths_bytes(outcome_paths) + _paths_bytes(final_paths),
    )
    outcome_path.unlink(missing_ok=True)
    del sample
    gc.collect()
    monitor.sample()

  data_fingerprint = hasher.finish(
    benchmark=benchmark,
    instruments=all_instruments.reset_index(drop=True),
    factors=factors,
  )
  benchmark_coverage_ratio = _benchmark_coverage_ratio(benchmark_counts)
  maximum_observed_rows = max(observed_batch_rows, default=0)
  final_partition_bytes = _paths_bytes(final_paths)
  regression_dependent_count = len(config.outcomes.horizons) * (
    int(config.outcomes.include_close_response)
    + int(config.outcomes.include_next_open_return)
  )
  regression_stream_columns = 8
  resource_estimate = {
    "strategy": "three_pass_stock_batch_parquet",
    "stock_batches": len(batches),
    "configured_batch_size": config.runtime.batch_size,
    "maximum_observed_batch_rows": maximum_observed_rows,
    "estimated_batch_increment_bytes": (
      max(maximum_observed_rows, estimated_batch_rows) * _BATCH_WORKING_BYTES_PER_ROW
    ),
    "analysis_sample_rows": analysis_sample_count,
    "primary_event_rows": event_count,
    "staged_partition_count": len(final_paths),
    "stage1_feature_bytes": stage1_feature_bytes,
    "stage2_outcome_bytes": stage2_outcome_bytes,
    "final_sample_partition_bytes": final_partition_bytes,
    "peak_staging_bytes_observed": peak_staging_bytes,
    "maximum_projection_bytes_observed": 0,
    "estimated_peak_disk_bytes": max(
      peak_staging_bytes,
      final_partition_bytes * 2,
    ),
    "full_sample_output_columns": len(event_record_columns(config)),
    "regression_stream_batch_rows": _PARQUET_ROW_GROUP_SIZE,
    "regression_stream_columns_per_dependent": regression_stream_columns,
    "regression_scan_passes": 1 + 3 * regression_dependent_count,
    "estimated_regression_block_bytes": max(
      128 * 1024**2,
      estimate_projection_bytes(
        _PARQUET_ROW_GROUP_SIZE,
        regression_stream_columns,
      )
      * 6,
    ),
  }
  source_provenance = _source_provenance(source)
  return StagedVolumeDataset(
    directory=root,
    partitions=tuple(final_paths),
    quality=quality,
    factor_coverage=factor_coverage,
    instruments=all_instruments.reset_index(drop=True),
    benchmark=benchmark,
    analysis_start=resolved_analysis_start,
    analysis_end=resolved_analysis_end,
    analysis_dates=tuple(sorted(analysis_dates)),
    market_calendar=tuple(pd.Timestamp(value) for value in calendar),
    analysis_sample_count=analysis_sample_count,
    event_count=event_count,
    benchmark_coverage_ratio=benchmark_coverage_ratio,
    data_fingerprint=data_fingerprint,
    resource_estimate=resource_estimate,
    source_provenance=source_provenance,
  )


def _source_provenance(source: ResearchDataSource) -> dict[str, Any]:
  value = getattr(source, "provenance", None)
  if callable(value):
    value = value()
  if value is None:
    return {}
  if not isinstance(value, Mapping):
    raise TypeError("research data source provenance 必须是 mapping")
  return dict(value)


def grouped_projection_columns(config: StudyConfig) -> tuple[str, ...]:
  columns = [
    "event_date",
    "rvol_bin",
    "price_position_bin",
    "event_direction",
    "is_volume_breakout",
    "is_high_position_stall",
    "relative_amount",
    "log_volume_zscore",
    "is_primary_shock_event",
    *_cooldown_identity_columns(),
  ]
  columns.extend(_outcome_statistic_columns(config))
  return tuple(dict.fromkeys(columns))


def comparison_projection_columns(
  config: StudyConfig,
  *,
  horizons: Sequence[int] | None = None,
) -> tuple[str, ...]:
  columns = [
    "event_date",
    "is_primary_shock_event",
    *_cooldown_identity_columns(),
    "price_position_bin",
    "relative_volume",
  ]
  columns.extend(_outcome_value_columns(config, horizons=horizons))
  return tuple(dict.fromkeys(columns))


def load_staged_projection(
  staged: StagedVolumeDataset,
  *,
  columns: Sequence[str],
  name: str,
  monitor: RuntimeMemoryMonitor,
  predicate: Callable[[pd.DataFrame], pd.Series] | None = None,
) -> pd.DataFrame:
  """Materialize one narrow projection without a list-of-DataFrames peak."""
  projection_dir = staged.directory / "projections"
  projection_dir.mkdir(parents=True, exist_ok=True)
  projection_path = projection_dir / f"{name}.parquet"
  writer: pq.ParquetWriter | None = None
  row_count = 0
  selected_columns = list(dict.fromkeys(columns))
  try:
    for partition in staged.partitions:
      available = set(pq.ParquetFile(partition).schema_arrow.names)
      missing = sorted(set(selected_columns).difference(available))
      if missing:
        raise ValueError(
          f"staging partition {partition.name} 缺少字段: {', '.join(missing)}"
        )
      for frame in _iter_partition_frames(
        partition,
        columns=selected_columns,
        monitor=monitor,
        stage=f"{name}_materialize",
      ):
        if predicate is not None and not frame.empty:
          mask = predicate(frame)
          frame = frame.loc[mask.fillna(False)]
        if frame.empty:
          continue
        writer = _append_frame(writer, projection_path, frame)
        row_count += len(frame)
        del frame
        monitor.checkpoint(f"{name}_materialize")
    if writer is not None:
      writer.close()
      writer = None
    if row_count == 0:
      return pd.DataFrame(columns=selected_columns)
    projection_bytes = projection_path.stat().st_size
    previous_projection_peak = int(
      staged.resource_estimate.get("maximum_projection_bytes_observed") or 0
    )
    staged.resource_estimate["maximum_projection_bytes_observed"] = max(
      previous_projection_peak,
      projection_bytes,
    )
    final_partition_bytes = int(
      staged.resource_estimate.get("final_sample_partition_bytes") or 0
    )
    staged.resource_estimate["estimated_peak_disk_bytes"] = max(
      int(staged.resource_estimate.get("peak_staging_bytes_observed") or 0),
      (
        final_partition_bytes * 2
        + staged.resource_estimate["maximum_projection_bytes_observed"]
      ),
    )
    estimated_bytes = estimate_projection_bytes(row_count, len(selected_columns))
    monitor.guard(name, estimated_increment_bytes=estimated_bytes)
    result = pd.read_parquet(projection_path)
    monitor.checkpoint(f"{name}_loaded")
    return result
  finally:
    try:
      if writer is not None:
        writer.close()
    finally:
      projection_path.unlink(missing_ok=True)


def write_analysis_sample_artifact(
  staged: StagedVolumeDataset,
  target: str | Path,
  config: StudyConfig,
  *,
  monitor: RuntimeMemoryMonitor,
) -> int:
  """Stream code-ordered partitions into the existing single-file contract."""
  output = Path(target)
  output.parent.mkdir(parents=True, exist_ok=True)
  partial = _partial_output_path(output)
  partial.unlink(missing_ok=True)
  columns = list(event_record_columns(config))
  writer: pq.ParquetWriter | None = None
  rows = 0
  try:
    for partition in staged.partitions:
      for frame in _iter_partition_frames(
        partition,
        columns=columns,
        monitor=monitor,
        stage="write_analysis_sample",
      ):
        if frame.empty:
          continue
        writer = _append_frame(writer, partial, frame)
        rows += len(frame)
        del frame
        monitor.checkpoint("write_analysis_sample")
    if writer is None:
      _write_partition(partial, pd.DataFrame(columns=columns))
    else:
      active_writer = writer
      writer = None
      active_writer.close()
    if rows != staged.analysis_sample_count:
      raise RuntimeError(
        f"analysis-sample 流式写入行数不一致: {rows} != {staged.analysis_sample_count}"
      )
    monitor.checkpoint("write_analysis_sample_complete")
    partial.replace(output)
    return rows
  except BaseException:
    try:
      if writer is not None:
        writer.close()
    finally:
      partial.unlink(missing_ok=True)
    raise


def write_event_artifact(
  events: pd.DataFrame,
  target: str | Path,
  config: StudyConfig,
  *,
  monitor: RuntimeMemoryMonitor,
) -> int:
  """Write globally ordered events through one bounded Arrow writer."""
  columns = list(event_record_columns(config))
  missing = sorted(set(columns).difference(events.columns))
  if missing:
    raise ValueError(f"events 缺少输出字段: {', '.join(missing)}")
  event_order = pd.MultiIndex.from_arrays(
    [
      pd.to_datetime(events["event_date"], errors="coerce"),
      events["stock_code"].astype(str),
    ]
  )
  ordered = (
    events
    if event_order.is_monotonic_increasing
    else events.sort_values(["event_date", "stock_code"], kind="stable")
  )
  output = Path(target)
  output.parent.mkdir(parents=True, exist_ok=True)
  partial = _partial_output_path(output)
  partial.unlink(missing_ok=True)
  writer: pq.ParquetWriter | None = None
  rows = 0
  try:
    for offset in range(0, len(ordered), _PARQUET_ROW_GROUP_SIZE):
      monitor.guard(
        "write_events",
        estimated_increment_bytes=256 * 1024**2,
      )
      chunk = ordered.iloc[offset : offset + _PARQUET_ROW_GROUP_SIZE][columns]
      writer = _append_frame(writer, partial, chunk)
      rows += len(chunk)
      monitor.checkpoint("write_events")
    if writer is None:
      _write_partition(partial, ordered.loc[:, columns])
    else:
      active_writer = writer
      writer = None
      active_writer.close()
    if rows != len(ordered):
      raise RuntimeError(f"events 流式写入行数不一致: {rows} != {len(ordered)}")
    monitor.checkpoint("write_events_complete")
    partial.replace(output)
    return rows
  except BaseException:
    try:
      if writer is not None:
        writer.close()
    finally:
      partial.unlink(missing_ok=True)
    raise


def estimate_projection_bytes(row_count: int, column_count: int) -> int:
  """Conservative in-RAM estimate for numeric-heavy pandas projections."""
  per_row = max(64, int(column_count) * 12)
  return int(row_count * per_row * _PROJECTION_OVERHEAD_MULTIPLIER)


def _iter_partition_frames(
  partition: Path,
  *,
  columns: Sequence[str],
  monitor: RuntimeMemoryMonitor,
  stage: str,
) -> Iterable[pd.DataFrame]:
  """Yield bounded pandas chunks from one Parquet partition."""
  parquet = pq.ParquetFile(partition)
  estimated_increment = estimate_projection_bytes(
    _PARQUET_ROW_GROUP_SIZE,
    len(columns),
  )
  for batch in parquet.iter_batches(
    batch_size=_PARQUET_ROW_GROUP_SIZE,
    columns=list(columns),
    use_threads=False,
  ):
    monitor.guard(stage, estimated_increment_bytes=estimated_increment)
    frame = batch.to_pandas()
    monitor.checkpoint(stage)
    yield frame


def _partial_output_path(output: Path) -> Path:
  return output.with_name(f".{output.name}.partial")


async def _prepare_universe_and_factor_gate(
  source: ResearchDataSource,
  config: StudyConfig,
  *,
  start: date | datetime,
  end: date | datetime,
  analysis_start: date | datetime,
) -> tuple[
  list[str],
  pd.DataFrame,
  pd.DataFrame,
  DividendFactorCoverageReport,
]:
  if config.universe.stock_codes is None:
    instruments = normalize_instruments(
      await source.list_instruments(instrument_types=("stock",))
    )
    instruments = _filter_default_stock_universe(
      instruments,
      start=analysis_start,
      end=end,
    )
    requested_codes = _unique_codes(
      instruments["stock_code"].dropna().astype(str).tolist()
    )
  else:
    requested_codes = _unique_codes(config.universe.stock_codes)
    instruments = normalize_instruments(
      await source.list_instruments(
        instrument_types=("stock",),
        codes=requested_codes,
      )
    )
  if not requested_codes:
    raise ValueError("研究窗口内没有合法的沪深 A 股标的")
  benchmark_code = config.universe.benchmark_code.strip().upper()
  coverage_codes = _unique_codes([*requested_codes, benchmark_code])
  loader = getattr(source, "load_dividend_factor_coverage", None)
  evidence = (
    await loader(coverage_codes, start=start, end=end) if callable(loader) else None
  )
  factor_coverage = build_dividend_factor_coverage_report(
    evidence,
    requested_codes=coverage_codes,
    requested_start=start,
    requested_end=end,
  )
  if not factor_coverage.is_complete:
    raise DividendFactorCoverageError(factor_coverage)
  benchmark_instrument = normalize_instruments(
    await source.list_instruments(
      instrument_types=("index",),
      codes=[benchmark_code],
    )
  )
  return requested_codes, instruments, benchmark_instrument, factor_coverage


async def _load_benchmark(
  source: ResearchDataSource,
  benchmark_code: str,
  benchmark_instrument: pd.DataFrame,
  *,
  start: date | datetime,
  end: date | datetime,
) -> pd.DataFrame:
  bars = normalize_daily_bars(
    await source.load_daily_bars(
      [benchmark_code],
      start,
      end,
      batch_size=1,
    )
  )
  benchmark = bars[bars["stock_code"] == benchmark_code].copy()
  benchmark["adjustment_valid"] = True
  benchmark = _append_instrument_dates(benchmark, benchmark_instrument)
  return _deduplicate(benchmark)


def _feature_columns() -> tuple[str, ...]:
  return (
    "stock_code",
    "event_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "suspend_flag",
    "open_date",
    "expire_date",
    "adjustment_valid",
    "relative_volume",
    "relative_amount",
    "log_volume_zscore",
    "price_position",
    "event_return",
    "event_direction",
    "relative_volume_bin",
    "price_position_bin",
    "is_volume_breakout",
    "is_high_position_stall",
    "momentum_20",
    "volatility_20",
    "average_amount_20",
    "log_average_amount_20",
    "_history_rows",
    "rvol_bin",
  )


def _absolute_return_columns(config: StudyConfig) -> tuple[str, ...]:
  columns: list[str] = []
  for horizon in config.outcomes.horizons:
    if config.outcomes.include_close_response:
      columns.append(f"close_return_h{horizon}")
    if config.outcomes.include_next_open_return:
      columns.append(f"next_open_return_h{horizon}")
  return tuple(columns)


def _outcome_value_columns(
  config: StudyConfig,
  *,
  horizons: Sequence[int] | None = None,
) -> list[str]:
  columns: list[str] = []
  selected_horizons = config.outcomes.horizons if horizons is None else horizons
  for horizon in selected_horizons:
    for prefix, enabled in (
      ("close", config.outcomes.include_close_response),
      ("next_open", config.outcomes.include_next_open_return),
    ):
      if not enabled:
        continue
      columns.append(f"{prefix}_return_h{horizon}")
      if config.outcomes.include_benchmark_excess:
        columns.append(f"csi300_excess_{prefix}_h{horizon}")
      if config.outcomes.include_cross_section_excess:
        columns.append(f"market_excess_{prefix}_h{horizon}")
  return columns


def _outcome_statistic_columns(config: StudyConfig) -> list[str]:
  columns = _outcome_value_columns(config)
  for horizon in config.outcomes.horizons:
    if config.outcomes.include_close_response:
      columns.extend((f"mae_close_h{horizon}", f"mfe_close_h{horizon}"))
    if config.outcomes.include_next_open_return:
      columns.extend((f"mae_next_open_h{horizon}", f"mfe_next_open_h{horizon}"))
  return columns


def _combine_market_aggregates(
  parts: dict[str, list[pd.DataFrame]],
) -> dict[str, pd.Series]:
  means: dict[str, pd.Series] = {}
  for column, aggregates in parts.items():
    if not aggregates:
      means[column] = pd.Series(dtype=float)
      continue
    combined = pd.concat(aggregates, axis=0)
    totals = combined.groupby(level=0, sort=True)[["sum", "count"]].sum()
    means[column] = totals["sum"].div(totals["count"].where(totals["count"] > 0))
  return means


def _add_market_excess(
  sample: pd.DataFrame,
  market_means: dict[str, pd.Series],
  config: StudyConfig,
) -> None:
  for source in _absolute_return_columns(config):
    return_kind, _, horizon_text = source.partition("_return_h")
    mean = market_means.get(source, pd.Series(dtype=float))
    aligned = sample["event_date"].map(mean)
    sample[f"market_excess_{return_kind}_h{horizon_text}"] = pd.to_numeric(
      sample[source], errors="coerce"
    ) - pd.to_numeric(aligned, errors="coerce")


def _add_alternative_cooldown_identities(
  sample: pd.DataFrame,
  config: StudyConfig,
) -> None:
  candidates = sample[sample["is_abnormal_volume"].fillna(False)].copy()
  for cooldown, column in zip(_ALT_COOLDOWNS, _cooldown_identity_columns()):
    if cooldown == config.event.cooldown_days:
      sample[column] = sample["is_primary_shock_event"].astype(bool)
      continue
    events = apply_event_cooldown(candidates, cooldown)
    sample[column] = _event_membership(sample, events)


def _event_membership(
  sample: pd.DataFrame,
  events: pd.DataFrame,
) -> np.ndarray:
  if events.empty:
    return np.zeros(len(sample), dtype=bool)
  sample_keys = pd.MultiIndex.from_arrays(
    [
      sample["stock_code"].astype(str),
      pd.to_datetime(sample["event_date"], errors="coerce"),
    ]
  )
  event_keys = pd.MultiIndex.from_arrays(
    [
      events["stock_code"].astype(str),
      pd.to_datetime(events["event_date"], errors="coerce"),
    ]
  ).drop_duplicates()
  return sample_keys.isin(event_keys)


def _cooldown_identity_columns() -> tuple[str, ...]:
  return tuple(f"_shock_cooldown_{value}d" for value in _ALT_COOLDOWNS)


def _empty_benchmark_counts(
  config: StudyConfig,
) -> dict[str, list[int]]:
  counts: dict[str, list[int]] = {}
  if not config.outcomes.include_benchmark_excess:
    return counts
  for source in _absolute_return_columns(config):
    return_kind, _, horizon_text = source.partition("_return_h")
    counts[f"csi300_excess_{return_kind}_h{horizon_text}"] = [0, 0]
  return counts


def _update_benchmark_counts(
  counts: dict[str, list[int]],
  sample: pd.DataFrame,
  config: StudyConfig,
) -> None:
  if not counts:
    return
  for source in _absolute_return_columns(config):
    return_kind, _, horizon_text = source.partition("_return_h")
    benchmark = f"csi300_excess_{return_kind}_h{horizon_text}"
    absolute_values = pd.to_numeric(sample[source], errors="coerce").to_numpy(
      dtype=float
    )
    benchmark_values = pd.to_numeric(sample[benchmark], errors="coerce").to_numpy(
      dtype=float
    )
    absolute_valid = np.isfinite(absolute_values)
    counts[benchmark][1] += int(absolute_valid.sum())
    counts[benchmark][0] += int(
      np.count_nonzero(absolute_valid & np.isfinite(benchmark_values))
    )


def _benchmark_coverage_ratio(counts: dict[str, list[int]]) -> float:
  ratios = [
    numerator / denominator
    for numerator, denominator in counts.values()
    if denominator > 0
  ]
  return min(ratios, default=0.0)


def _write_partition(path: Path, frame: pd.DataFrame) -> None:
  path.parent.mkdir(parents=True, exist_ok=True)
  frame.to_parquet(
    path,
    index=False,
    compression="zstd",
    row_group_size=_PARQUET_ROW_GROUP_SIZE,
  )


def _paths_bytes(paths: Iterable[Path]) -> int:
  return sum(path.stat().st_size for path in paths if path.is_file())


def _append_frame(
  writer: pq.ParquetWriter | None,
  path: Path,
  frame: pd.DataFrame,
) -> pq.ParquetWriter:
  table = pa.Table.from_pandas(frame, preserve_index=False)
  if writer is None:
    writer = pq.ParquetWriter(
      path,
      table.schema,
      compression="zstd",
    )
  elif not table.schema.equals(writer.schema, check_metadata=False):
    table = table.cast(writer.schema.remove_metadata()).replace_schema_metadata(
      writer.schema.metadata
    )
  elif table.schema.metadata != writer.schema.metadata:
    table = table.replace_schema_metadata(writer.schema.metadata)
  writer.write_table(table, row_group_size=_PARQUET_ROW_GROUP_SIZE)
  return writer


def _batches(
  values: Sequence[str],
  size: int,
) -> Iterable[list[str]]:
  for offset in range(0, len(values), size):
    yield list(values[offset : offset + size])


class _CanonicalDatasetHasher:
  """Streaming equivalent of the legacy canonical DataFrame fingerprint."""

  def __init__(self) -> None:
    self._digest = hashlib.sha256()
    self._digest.update(b"panel")
    self._panel_rows = 0

  def update_panel(self, frame: pd.DataFrame) -> None:
    if frame.empty:
      return
    self._panel_rows += len(frame)
    self._update_rows(frame)

  def finish(
    self,
    *,
    benchmark: pd.DataFrame,
    instruments: pd.DataFrame,
    factors: pd.DataFrame,
  ) -> str:
    if self._panel_rows == 0:
      self._digest.update(b"<empty>")
    for name, frame in (
      ("benchmark", benchmark),
      ("instruments", instruments),
      ("factors", factors),
    ):
      self._digest.update(name.encode("utf-8"))
      if frame.empty:
        self._digest.update(b"<empty>")
      else:
        self._update_rows(frame)
    return self._digest.hexdigest()

  def _update_rows(self, frame: pd.DataFrame) -> None:
    normalized = _normalize_fingerprint_frame(frame)
    sort_columns = [
      column
      for column in ("stock_code", "time", "event_date")
      if column in normalized.columns
    ]
    if sort_columns:
      normalized = normalized.sort_values(sort_columns, kind="stable")
    hashed = pd.util.hash_pandas_object(
      normalized.reset_index(drop=True),
      index=False,
      categorize=True,
    )
    self._digest.update(hashed.to_numpy().tobytes())


def _normalize_fingerprint_frame(frame: pd.DataFrame) -> pd.DataFrame:
  """Normalize value semantics before hashing independently loaded batches."""
  normalized = frame.reindex(sorted(frame.columns), axis=1).copy()
  for column in normalized.columns:
    values = normalized[column]
    if column in _FINGERPRINT_DATE_COLUMNS or pd.api.types.is_datetime64_any_dtype(
      values.dtype
    ):
      normalized[column] = _normalize_fingerprint_dates(values)
    elif column in _FINGERPRINT_BOOLEAN_COLUMNS or pd.api.types.is_bool_dtype(
      values.dtype
    ):
      normalized[column] = values.astype("boolean")
    elif column in _FINGERPRINT_STRING_COLUMNS:
      normalized[column] = values.astype("string")
    elif column in _FINGERPRINT_NUMERIC_COLUMNS or pd.api.types.is_numeric_dtype(
      values.dtype
    ):
      normalized[column] = _normalize_fingerprint_numbers(values)
    elif pd.api.types.is_timedelta64_dtype(values.dtype):
      normalized[column] = pd.to_timedelta(values, errors="coerce").astype(
        "timedelta64[ns]"
      )
    else:
      normalized[column] = values.astype("string")
  return normalized


def _normalize_fingerprint_dates(values: pd.Series) -> pd.Series:
  parsed = pd.to_datetime(values, errors="coerce", utc=True)
  return parsed.dt.tz_localize(None).astype("datetime64[ns]")


def _normalize_fingerprint_numbers(values: pd.Series) -> pd.Series:
  normalized = pd.to_numeric(values, errors="coerce").to_numpy(
    dtype="float64",
    na_value=np.nan,
    copy=True,
  )
  normalized[np.isnan(normalized)] = np.nan
  normalized[normalized == 0.0] = 0.0
  return pd.Series(normalized, index=values.index, dtype="float64")
