from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import quantx_research.staged_study as staged_study_module
from quantx_research.core import StudyConfig, event_record_columns
from quantx_research.data import DatasetBuilder
from quantx_research.runner import _dataset_fingerprint
from quantx_research.runtime_memory import (
  MemorySnapshot,
  PhysicalMemoryGuardError,
  RuntimeMemoryMonitor,
)
from quantx_research.staged_study import analyze_staged_volume_sample
from quantx_research.staging import (
  _CanonicalDatasetHasher,
  _normalize_fingerprint_frame,
  build_staged_volume_dataset,
  write_analysis_sample_artifact,
  write_event_artifact,
)
from quantx_research.studies import VolumeShockStudy

_GIB = 1024**3


class BatchResearchSource:
  def __init__(self, bars: pd.DataFrame) -> None:
    self.bars = bars

  async def list_instruments(
    self,
    *,
    instrument_types=("stock",),
    codes=None,
  ) -> pd.DataFrame:
    is_index = "index" in instrument_types
    available = sorted(self.bars["stock_code"].unique())
    requested = set(codes or available)
    selected = [
      code
      for code in available
      if code in requested and (code == "000300.SH") == is_index
    ]
    return pd.DataFrame(
      {
        "stock_code": selected,
        "instrument_type": ["index" if is_index else "stock"] * len(selected),
        "name": selected,
        "market": [code[-2:] for code in selected],
        "open_date": [pd.Timestamp("2020-01-01")] * len(selected),
        "expire_date": [pd.NaT] * len(selected),
      }
    )

  async def load_daily_bars(
    self,
    stock_codes,
    start,
    end,
    *,
    batch_size=300,
  ) -> pd.DataFrame:
    del batch_size
    times = pd.to_datetime(self.bars["time"]).dt.tz_localize(None)
    return self.bars[
      self.bars["stock_code"].isin(stock_codes)
      & times.between(pd.Timestamp(start), pd.Timestamp(end))
    ].copy()

  async def load_dividend_factors(
    self,
    stock_codes,
    *,
    start=None,
    end=None,
  ) -> pd.DataFrame:
    del stock_codes, start, end
    return pd.DataFrame(columns=["stock_code", "time", "dr"])

  async def load_dividend_factor_coverage(
    self,
    stock_codes,
    *,
    start,
    end,
  ) -> pd.DataFrame:
    return pd.DataFrame(
      [
        {
          "request_id": "complete-factor-window",
          "source": "qmt-get-divid-factors-v1",
          "status": "COMPLETED",
          "start_date": pd.Timestamp(start).strftime("%Y%m%d"),
          "end_date": pd.Timestamp(end).strftime("%Y%m%d"),
          "stock_codes": list(stock_codes),
          "expected_chunks": 1,
          "received_chunks": 1,
          "completed_at": pd.Timestamp("2026-07-30"),
        }
      ]
    )


def _bars() -> pd.DataFrame:
  dates = pd.bdate_range("2024-01-01", periods=340)
  rows: list[dict[str, Any]] = []
  codes = ("000001.SZ", "000002.SZ", "600000.SH", "000300.SH")
  for code_index, code in enumerate(codes):
    phase = np.arange(len(dates), dtype=float)
    close_values = (
      10.0
      + code_index * 2.0
      + phase * (0.008 + code_index * 0.001)
      + np.sin(phase / (9.0 + code_index)) * 0.15
    )
    shock_days = {
      120 + code_index,
      124 + code_index,
      133 + code_index,
      200 + code_index,
      205 + code_index,
      230 + code_index,
      280 + code_index,
    }
    for ordinal, (trade_date, close) in enumerate(zip(dates, close_values)):
      volume = 1_000.0 + code_index * 80.0 + (ordinal % 7) * 5.0
      if code != "000300.SH" and ordinal in shock_days:
        volume *= 3.2
        close *= 1.0 + (code_index - 1) * 0.009
      rows.append(
        {
          "stock_code": code,
          "time": trade_date,
          "open": close * (0.997 + code_index * 0.0002),
          "high": close * 1.012,
          "low": close * 0.988,
          "close": close,
          "volume": volume,
          "amount": volume * close,
          "suspend_flag": 0,
        }
      )
  for ordinal, trade_date in enumerate(dates[-30:]):
    close = 6.0 + ordinal * 0.01
    rows.append(
      {
        "stock_code": "000000.SZ",
        "time": trade_date,
        "open": close * 0.998,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": 500.0,
        "amount": 500.0 * close,
        "suspend_flag": 0,
      }
    )
  return pd.DataFrame(rows)


def _medium_bars(stock_count: int = 18) -> pd.DataFrame:
  dates = pd.bdate_range("2024-01-01", periods=340)
  rows: list[dict[str, Any]] = []
  codes = [f"{index + 1:06d}.SZ" for index in range(stock_count)]
  codes.append("000300.SH")
  for code_index, code in enumerate(codes):
    phase = np.arange(len(dates), dtype=float)
    close_values = (
      8.0
      + code_index * 0.3
      + phase * 0.006
      + np.sin(phase / (7.0 + code_index % 5)) * 0.08
    )
    for ordinal, (trade_date, close) in enumerate(zip(dates, close_values)):
      volume = 800.0 + code_index * 13.0 + (ordinal % 11) * 3.0
      if code != "000300.SH" and ordinal in {
        125 + code_index % 3,
        132 + code_index % 3,
        190 + code_index % 7,
        245 + code_index % 5,
        285 + code_index % 4,
      }:
        volume *= 2.8
      rows.append(
        {
          "stock_code": code,
          "time": trade_date,
          "open": close * 0.998,
          "high": close * 1.01,
          "low": close * 0.99,
          "close": close,
          "volume": volume,
          "amount": volume * close,
          "suspend_flag": 0,
        }
      )
  return pd.DataFrame(rows)


def _config() -> StudyConfig:
  return StudyConfig.model_validate(
    {
      "study": "volume-shock",
      "version": "v1",
      "date_range": ["2024-05-20", "2025-04-18"],
      "universe": {
        "benchmark_code": "000300.SH",
        "minimum_listing_days": 60,
      },
      "event": {
        "relative_volume_window": 20,
        "relative_amount_window": 20,
        "log_volume_zscore_window": 30,
        "relative_volume_threshold": 1.5,
        "normal_relative_volume_min": 0.8,
        "normal_relative_volume_max": 1.2,
        "cooldown_days": 10,
        "breakout_window": 20,
      },
      "conditioning": {"price_position_window": 60},
      "outcomes": {"horizons": [1, 3, 5]},
      "statistics": {
        "bootstrap_samples": 100,
        "minimum_cell_samples": 2,
        "minimum_inference_dates": 30,
        "random_seed": 42,
      },
      "quality": {
        "minimum_history_rows": 60,
        "minimum_total_events": 1,
        "minimum_usable_symbols": 1,
        "minimum_history_coverage_ratio": 0.5,
        "minimum_end_coverage_ratio": 0.5,
        "minimum_benchmark_coverage_ratio": 0.5,
      },
      "runtime": {
        "batch_size": 1,
        "output_root": ".runtime/test",
        "minimum_available_memory_gib": 1,
        "memory_sample_interval_seconds": 10,
      },
    }
  )


@pytest.mark.asyncio
async def test_staged_batches_match_legacy_statistics_and_market_excess(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  bars = _bars()
  config = _config()
  source = BatchResearchSource(bars)
  start = date(2024, 1, 1)
  end = date(2025, 4, 18)
  legacy_dataset = await DatasetBuilder(source).build(
    start=start,
    end=end,
    benchmark_code="000300.SH",
    batch_size=300,
    adjustment="point_in_time",
    minimum_observations=config.required_lookback,
    factor_start=start,
    universe_start=config.date_range[0],
    require_factor_coverage=True,
  )
  legacy_sample, legacy_events, legacy_result = (
    VolumeShockStudy().run_with_analysis_sample(
      legacy_dataset.panel,
      config,
      benchmark=legacy_dataset.benchmark,
    )
  )

  monitor = RuntimeMemoryMonitor(
    reserve_gib=1,
    sample_interval_seconds=10,
  )
  original_projection_loader = staged_study_module.load_staged_projection

  def guarded_projection_loader(*args: Any, **kwargs: Any) -> pd.DataFrame:
    assert kwargs.get("name") != "load_regression_projection"
    return original_projection_loader(*args, **kwargs)

  monkeypatch.setattr(
    staged_study_module,
    "load_staged_projection",
    guarded_projection_loader,
  )
  with monitor:
    staged = await build_staged_volume_dataset(
      source,
      config,
      start=start,
      end=end,
      analysis_start=config.date_range[0],
      directory=tmp_path / "staging",
      monitor=monitor,
    )
    staged_events, staged_result = analyze_staged_volume_sample(
      staged,
      config,
      monitor=monitor,
    )
    analysis_artifact = tmp_path / "analysis-sample.parquet"
    event_artifact = tmp_path / "events.parquet"
    assert write_analysis_sample_artifact(
      staged,
      analysis_artifact,
      config,
      monitor=monitor,
    ) == len(legacy_sample)
    assert write_event_artifact(
      staged_events,
      event_artifact,
      config,
      monitor=monitor,
    ) == len(legacy_events)
    interrupted_artifact = tmp_path / "interrupted-analysis-sample.parquet"
    original_guard = monitor.guard
    write_guard_calls = 0

    def fail_before_second_output_chunk(
      stage: str,
      *,
      estimated_increment_bytes: int = 0,
    ) -> MemorySnapshot:
      nonlocal write_guard_calls
      if stage == "write_analysis_sample":
        write_guard_calls += 1
        if write_guard_calls == 2:
          raise PhysicalMemoryGuardError(
            stage=stage,
            snapshot=MemorySnapshot(
              total_physical_bytes=16 * _GIB,
              available_physical_bytes=monitor.reserve_bytes - 1,
              process_rss_bytes=2 * _GIB,
            ),
            reserve_bytes=monitor.reserve_bytes,
            estimated_increment_bytes=estimated_increment_bytes,
          )
      return original_guard(
        stage,
        estimated_increment_bytes=estimated_increment_bytes,
      )

    monkeypatch.setattr(monitor, "guard", fail_before_second_output_chunk)
    with pytest.raises(PhysicalMemoryGuardError):
      write_analysis_sample_artifact(
        staged,
        interrupted_artifact,
        config,
        monitor=monitor,
      )
    assert not interrupted_artifact.exists()
    assert not (
      interrupted_artifact.parent / f".{interrupted_artifact.name}.partial"
    ).exists()

  columns = [
    column for column in event_record_columns(config) if column != "quality_flags"
  ]
  staged_sample = pd.concat(
    [pd.read_parquet(path, columns=columns) for path in staged.partitions],
    ignore_index=True,
  )
  expected_sample = legacy_sample.loc[:, columns].reset_index(drop=True)
  pd.testing.assert_frame_equal(
    staged_sample,
    expected_sample,
    check_dtype=False,
    check_categorical=False,
    rtol=1e-12,
    atol=1e-14,
  )
  expected_events = (
    legacy_events.loc[:, columns]
    .sort_values(["event_date", "stock_code"], kind="stable")
    .reset_index(drop=True)
  )
  pd.testing.assert_frame_equal(
    staged_events.loc[:, columns].reset_index(drop=True),
    expected_events,
    check_dtype=False,
    check_categorical=False,
    rtol=1e-12,
    atol=1e-14,
  )
  assert _rounded(staged_result.model_dump()) == _rounded(legacy_result.model_dump())
  _assert_regressions_close(legacy_result.regressions, staged_result.regressions)
  assert staged.data_fingerprint == _dataset_fingerprint(legacy_dataset)
  integer_panel = legacy_dataset.panel.copy()
  float_panel = legacy_dataset.panel.copy()
  integer_panel["suspend_flag"] = integer_panel["suspend_flag"].astype("Int64")
  float_panel["suspend_flag"] = float_panel["suspend_flag"].astype("float64")
  assert _dataset_fingerprint(
    replace(legacy_dataset, panel=integer_panel)
  ) == _dataset_fingerprint(replace(legacy_dataset, panel=float_panel))
  assert staged.quality.to_dict() == legacy_dataset.quality.to_dict()
  assert len(staged.partitions) == 3
  assert monitor.to_dict()["peak_process_rss_bytes"] > 0
  assert list(pd.read_parquet(analysis_artifact).columns) == list(
    event_record_columns(config)
  )
  assert list(pd.read_parquet(event_artifact).columns) == list(
    event_record_columns(config)
  )


@pytest.mark.asyncio
async def test_medium_staged_sample_matches_legacy_across_many_batches(
  tmp_path: Path,
) -> None:
  bars = _medium_bars()
  config = _config().model_copy(
    update={
      "runtime": _config().runtime.model_copy(update={"batch_size": 4}),
    }
  )
  source = BatchResearchSource(bars)
  start = date(2024, 1, 1)
  end = date(2025, 4, 18)
  legacy_dataset = await DatasetBuilder(source).build(
    start=start,
    end=end,
    benchmark_code="000300.SH",
    batch_size=300,
    adjustment="point_in_time",
    minimum_observations=config.required_lookback,
    factor_start=start,
    universe_start=config.date_range[0],
    require_factor_coverage=True,
  )
  legacy_sample = VolumeShockStudy().build_analysis_sample(
    legacy_dataset.panel,
    config,
    benchmark=legacy_dataset.benchmark,
  )
  monitor = RuntimeMemoryMonitor(reserve_gib=1, sample_interval_seconds=10)
  with monitor:
    staged = await build_staged_volume_dataset(
      source,
      config,
      start=start,
      end=end,
      analysis_start=config.date_range[0],
      directory=tmp_path / "medium-staging",
      monitor=monitor,
    )
  comparison_columns = [
    "stock_code",
    "event_date",
    "is_primary_shock_event",
    *[
      column
      for column in event_record_columns(config)
      if column.startswith("market_excess_")
    ],
  ]
  staged_sample = pd.concat(
    [pd.read_parquet(path, columns=comparison_columns) for path in staged.partitions],
    ignore_index=True,
  )
  pd.testing.assert_frame_equal(
    staged_sample,
    legacy_sample.loc[:, comparison_columns].reset_index(drop=True),
    check_dtype=False,
    rtol=1e-12,
    atol=1e-14,
  )
  assert staged.data_fingerprint == _dataset_fingerprint(legacy_dataset)
  assert staged.analysis_sample_count == len(legacy_sample)
  assert len(staged.partitions) == 5


def test_physical_memory_guard_fails_before_consuming_reserve() -> None:
  snapshot = MemorySnapshot(
    total_physical_bytes=16 * _GIB,
    available_physical_bytes=3 * _GIB,
    process_rss_bytes=2 * _GIB,
  )
  monitor = RuntimeMemoryMonitor(
    reserve_gib=2,
    snapshot_provider=lambda: snapshot,
  )

  with pytest.raises(PhysicalMemoryGuardError, match="不会使用 pagefile"):
    monitor.guard(
      "regression_projection",
      estimated_increment_bytes=2 * _GIB,
    )

  report = monitor.to_dict()
  assert report["physical_only"] is True
  assert report["minimum_available_physical_gib"] == 3


def test_physical_memory_reserve_breach_is_latched_after_recovery() -> None:
  snapshots = iter(
    (
      MemorySnapshot(
        total_physical_bytes=16 * _GIB,
        available_physical_bytes=1 * _GIB,
        process_rss_bytes=4 * _GIB,
      ),
      MemorySnapshot(
        total_physical_bytes=16 * _GIB,
        available_physical_bytes=10 * _GIB,
        process_rss_bytes=2 * _GIB,
      ),
    )
  )
  monitor = RuntimeMemoryMonitor(
    reserve_gib=2,
    snapshot_provider=lambda: next(snapshots),
  )

  with pytest.raises(PhysicalMemoryGuardError):
    monitor.checkpoint("bounded_chunk")
  with pytest.raises(PhysicalMemoryGuardError):
    monitor.guard("next_chunk")

  report = monitor.to_dict()
  assert report["reserve_breached"] is True
  assert report["reserve_breach_stage"] == "bounded_chunk"
  assert report["reserve_breach_available_physical_bytes"] == _GIB


def test_canonical_fingerprint_is_stable_across_mixed_numeric_batch_schemas() -> None:
  integer_batch = pd.DataFrame(
    {
      "stock_code": pd.Series(["000001.SZ"], dtype=object),
      "time": pd.to_datetime(["2024-01-02"]),
      "open": pd.Series([10], dtype="int64"),
      "high": pd.Series([11], dtype="int64"),
      "low": pd.Series([9], dtype="int64"),
      "close": pd.Series([10], dtype="int64"),
      "volume": pd.Series([1_000], dtype="int64"),
      "amount": pd.Series([10_000], dtype="int64"),
      "suspend_flag": pd.Series([0], dtype="Int64"),
      "adjustment_valid": pd.Series([True], dtype=bool),
      "listing_valid": pd.Series([True], dtype=bool),
      "open_date": pd.to_datetime(["2020-01-01"]),
      "expire_date": pd.to_datetime([None]),
    }
  )
  float_batch = pd.DataFrame(
    {
      "stock_code": pd.Series(["000002.SZ"], dtype="string"),
      "time": pd.to_datetime(["2024-01-02"]),
      "open": pd.Series([10.0], dtype="float64"),
      "high": pd.Series([11.0], dtype="float64"),
      "low": pd.Series([9.0], dtype="float64"),
      "close": pd.Series([10.0], dtype="float64"),
      "volume": pd.Series([1_000.0], dtype="float64"),
      "amount": pd.Series([10_000.0], dtype="float64"),
      "suspend_flag": pd.Series([0.0], dtype="float64"),
      "adjustment_valid": pd.Series([True], dtype="boolean"),
      "listing_valid": pd.Series([True], dtype="boolean"),
      "open_date": pd.to_datetime(["2020-01-01"]),
      "expire_date": pd.to_datetime([None]),
    }
  )
  nullable_integer_batch = integer_batch.copy()
  nullable_integer_batch["stock_code"] = pd.Series(["000003.SZ"], dtype="string")
  for column in ("open", "high", "low", "close", "volume", "amount"):
    nullable_integer_batch[column] = nullable_integer_batch[column].astype("Int64")

  source_batches = [integer_batch, float_batch, nullable_integer_batch]
  batch_size_one = _fingerprint_for_panel_batches(source_batches)
  batch_size_two = _fingerprint_for_panel_batches(
    [
      pd.concat(source_batches[:2], ignore_index=True),
      source_batches[2],
    ]
  )
  canonical_full = _fingerprint_for_panel_batches(
    [pd.concat(source_batches, ignore_index=True)]
  )

  assert batch_size_one == batch_size_two == canonical_full
  normalized = _normalize_fingerprint_frame(
    pd.concat(source_batches, ignore_index=True)
  )
  assert all(
    normalized[column].dtype == np.dtype("float64")
    for column in ("open", "high", "low", "close", "volume", "amount")
  )
  assert normalized["time"].dtype == np.dtype("datetime64[ns]")
  assert normalized["stock_code"].dtype == pd.StringDtype()
  assert normalized["suspend_flag"].dtype == pd.BooleanDtype()
  assert normalized["adjustment_valid"].dtype == pd.BooleanDtype()


def _fingerprint_for_panel_batches(batches: list[pd.DataFrame]) -> str:
  hasher = _CanonicalDatasetHasher()
  for batch in batches:
    hasher.update_panel(batch)
  empty = pd.DataFrame()
  return hasher.finish(
    benchmark=empty,
    instruments=empty,
    factors=empty,
  )


def _assert_regressions_close(legacy: list[Any], staged: list[Any]) -> None:
  assert len(staged) == len(legacy)
  for expected, actual in zip(legacy, staged):
    assert actual.return_kind == expected.return_kind
    assert actual.horizon == expected.horizon
    assert actual.dependent_variable == expected.dependent_variable
    assert actual.nobs == expected.nobs
    assert actual.r_squared == pytest.approx(
      expected.r_squared,
      rel=1e-12,
      abs=1e-14,
    )
    assert actual.warnings == expected.warnings
    assert len(actual.coefficients) == len(expected.coefficients)
    for expected_coefficient, actual_coefficient in zip(
      expected.coefficients,
      actual.coefficients,
    ):
      assert actual_coefficient.term == expected_coefficient.term
      for field in (
        "estimate",
        "std_error",
        "t_stat",
        "p_value",
        "ci_low",
        "ci_high",
        "q_value",
      ):
        expected_value = getattr(expected_coefficient, field)
        actual_value = getattr(actual_coefficient, field)
        if expected_value is None:
          assert actual_value is None
        else:
          assert actual_value == pytest.approx(
            expected_value,
            rel=1e-11,
            abs=1e-13,
          )
      assert actual_coefficient.significant == expected_coefficient.significant


def _rounded(value: Any) -> Any:
  if isinstance(value, float):
    return round(value, 11)
  if isinstance(value, dict):
    return {key: _rounded(item) for key, item in value.items()}
  if isinstance(value, list):
    return [_rounded(item) for item in value]
  return value
