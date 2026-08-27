from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import quantx_research.runner as runner_module
import yaml
from quantx_research.runner import (
  ResearchPreflightError,
  ResearchResourceError,
  render_existing,
  run_study,
  validate_study,
)
from quantx_research.runtime_memory import MemorySnapshot, PhysicalMemoryGuardError

_GIB = 1024**3


class FakeResearchSource:
  def __init__(self, bars: pd.DataFrame) -> None:
    self.bars = bars
    self.factor_coverage_complete = True
    self.daily_bar_calls = 0

  @property
  def provenance(self) -> dict[str, str]:
    return {
      "kind": "test-fixture",
      "snapshot_sha256": "a" * 64,
    }

  async def list_instruments(
    self,
    *,
    instrument_types=("stock",),
    codes=None,
  ) -> pd.DataFrame:
    all_codes = sorted(self.bars["stock_code"].unique())
    requested = set(codes or all_codes)
    is_index = "index" in instrument_types
    selected = [
      code
      for code in all_codes
      if code in requested and (code == "000300.SH") == is_index
    ]
    return pd.DataFrame(
      {
        "stock_code": selected,
        "instrument_type": ["index" if is_index else "stock"] * len(selected),
        "name": selected,
        "market": ["SH"] * len(selected),
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
    self.daily_bar_calls += 1
    start_at = pd.Timestamp(start)
    end_at = pd.Timestamp(end)
    times = pd.to_datetime(self.bars["time"]).dt.tz_localize(None)
    return self.bars[
      self.bars["stock_code"].isin(stock_codes) & times.between(start_at, end_at)
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
    covered = (
      list(stock_codes) if self.factor_coverage_complete else list(stock_codes)[:-1]
    )
    return pd.DataFrame(
      [
        {
          "request_id": "factor-request",
          "source": "qmt-get-divid-factors-v1",
          "status": "COMPLETED",
          "start_date": pd.Timestamp(start).strftime("%Y%m%d"),
          "end_date": pd.Timestamp(end).strftime("%Y%m%d"),
          "stock_codes": covered,
          "expected_chunks": 1,
          "received_chunks": 1,
          "completed_at": pd.Timestamp("2025-07-01"),
        }
      ]
    )


def _market_bars(*, with_shocks: bool = True) -> pd.DataFrame:
  dates = pd.bdate_range("2024-01-01", periods=390)
  rows = []
  for code_index, code in enumerate(
    ["000001.SZ", "000002.SZ", "600000.SH", "000300.SH"]
  ):
    trend = 10.0 + code_index + np.arange(len(dates)) * 0.01
    for index, (trade_date, close) in enumerate(zip(dates, trend)):
      volume = 1000.0 + code_index * 50
      if (
        with_shocks and code != "000300.SH" and index in {270, 285, 300, 315, 330, 345}
      ):
        volume *= 3.0
        close *= 1.0 + (code_index - 1) * 0.012
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


def _write_config(path: Path, output_root: Path, *, minimum_events: int) -> Path:
  payload = {
    "study": "volume-shock",
    "version": "v1",
    "date_range": ["2024-12-01", "2025-06-30"],
    "universe": {
      "instrument_type": "stock",
      "lookback_years": 5,
      "end_date": "latest",
      "benchmark_code": "000300.SH",
      "minimum_listing_days": 120,
    },
    "event": {
      "relative_volume_window": 20,
      "relative_amount_window": 20,
      "log_volume_zscore_window": 60,
      "relative_volume_threshold": 1.5,
      "normal_relative_volume_min": 0.8,
      "normal_relative_volume_max": 1.2,
      "relative_volume_bins": [0, 1, 1.5, 2, 3],
      "cooldown_days": 10,
      "flat_return_threshold_pct": 1,
      "breakout_window": 20,
    },
    "conditioning": {
      "price_position_window": 252,
      "price_position_bins": [0, 0.3, 0.7, 1],
    },
    "outcomes": {
      "horizons": [1, 3, 5, 10, 20],
      "include_close_response": True,
      "include_next_open_return": True,
      "include_benchmark_excess": True,
      "include_cross_section_excess": True,
    },
    "statistics": {
      "bootstrap_method": "moving_block",
      "bootstrap_samples": 100,
      "moving_block_length": "horizon",
      "random_seed": 42,
      "confidence_level": 0.95,
      "minimum_cell_samples": 2,
      "fdr_alpha": 0.05,
      "run_regression": True,
    },
    "quality": {
      "minimum_history_rows": 252,
      "minimum_total_events": minimum_events,
      "exclude_corporate_action_windows_without_adjustment": True,
    },
    "runtime": {
      "batch_size": 300,
      "minimum_available_memory_gib": 1,
      "output_root": str(output_root),
    },
  }
  path.write_text(
    yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
    encoding="utf-8",
  )
  return path


@pytest.mark.asyncio
async def test_runner_generates_reproducible_report_bundle(tmp_path: Path) -> None:
  config = _write_config(tmp_path / "study.yaml", tmp_path / "runs", minimum_events=3)
  source = FakeResearchSource(_market_bars())

  validation = await validate_study(config, source=source)
  assert validation["valid"] is True
  assert validation["event_count"] >= 3
  assert validation["analysis_sample_count"] > validation["event_count"]
  assert validation["data_quality"]["source_provenance"]["kind"] == (
    "test-fixture"
  )

  run_dir = await run_study(
    config,
    source=source,
    now=datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc),
  )

  expected = {
    "manifest.json",
    "resolved-config.yaml",
    "data-quality.json",
    "events.parquet",
    "analysis-sample.parquet",
    "metrics.json",
    "report.html",
    "tables/grouped-statistics.csv",
    "tables/event-curve.csv",
    "tables/comparison.csv",
    "tables/comparison-cooldown_5d.csv",
    "tables/comparison-cooldown_20d.csv",
    "tables/regressions.csv",
    "figures/event_curve.svg",
    "figures/interaction_heatmap.svg",
    "figures/regression_coefficients.svg",
  }
  actual = {
    path.relative_to(run_dir).as_posix()
    for path in run_dir.rglob("*")
    if path.is_file()
  }
  assert expected <= actual

  manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
  metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
  quality = json.loads((run_dir / "data-quality.json").read_text(encoding="utf-8"))
  assert manifest["status"] == "success"
  assert manifest["event_count"] == metrics["event_count"]
  assert manifest["analysis_sample_count"] == metrics["analysis_sample_count"]
  events = pd.read_parquet(run_dir / "events.parquet")
  analysis_sample = pd.read_parquet(run_dir / "analysis-sample.parquet")
  assert len(analysis_sample) == metrics["analysis_sample_count"]
  assert len(analysis_sample) > len(events)
  assert events["is_abnormal_volume"].all()
  assert events["is_primary_shock_event"].all()
  assert int(analysis_sample["is_primary_shock_event"].sum()) == len(events)
  assert analysis_sample["is_normal_volume"].any()
  assert {"cooldown_5d", "cooldown_20d"} == set(metrics["comparison_sensitivity"])
  assert len(manifest["data_fingerprint"]) == 64
  assert quality["dividend_factor_coverage"]["is_complete"] is True
  assert quality["resource_estimate"]["strategy"] == ("three_pass_stock_batch_parquet")
  assert quality["resource_estimate"]["peak_staging_bytes_observed"] > 0
  assert quality["runtime_memory"]["physical_only"] is True
  assert manifest["runtime_memory"]["peak_process_rss_bytes"] > 0
  assert manifest["source_provenance"]["snapshot_sha256"] == "a" * 64
  assert quality["source_provenance"] == manifest["source_provenance"]
  assert "<html" in (run_dir / "report.html").read_text(encoding="utf-8").lower()

  report = render_existing(run_dir)
  assert report == run_dir / "report.html"
  first_render = report.read_bytes()
  render_existing(run_dir)
  assert report.read_bytes() == first_render


@pytest.mark.asyncio
async def test_validation_rejects_severely_incomplete_stock_universe(
  tmp_path: Path,
) -> None:
  config = _write_config(
    tmp_path / "study.yaml",
    tmp_path / "runs",
    minimum_events=3,
  )
  bars = _market_bars()
  short_codes = {"000002.SZ", "600000.SH"}
  shortened = bars[
    ~bars["stock_code"].isin(short_codes)
    | (bars.groupby("stock_code").cumcount() >= 310)
  ].copy()

  result = await validate_study(config, source=FakeResearchSource(shortened))

  assert result["valid"] is False
  assert any("股票历史覆盖率不足" in error for error in result["errors"])


@pytest.mark.asyncio
async def test_validation_rejects_incomplete_benchmark_coverage(
  tmp_path: Path,
) -> None:
  config = _write_config(
    tmp_path / "study.yaml",
    tmp_path / "runs",
    minimum_events=3,
  )
  bars = _market_bars()
  benchmark_mask = bars["stock_code"] == "000300.SH"
  shortened = bars[
    ~benchmark_mask | (bars.groupby("stock_code").cumcount() >= 310)
  ].copy()

  result = await validate_study(config, source=FakeResearchSource(shortened))

  assert result["valid"] is False
  assert any("沪深300收益覆盖率不足" in error for error in result["errors"])


@pytest.mark.asyncio
async def test_preflight_failure_is_explicit_and_keeps_diagnostics(
  tmp_path: Path,
) -> None:
  config = _write_config(
    tmp_path / "study.yaml",
    tmp_path / "runs",
    minimum_events=1,
  )
  source = FakeResearchSource(_market_bars(with_shocks=False))

  with pytest.raises(ResearchPreflightError) as exc_info:
    await run_study(config, source=source)

  run_dir = exc_info.value.run_dir
  assert run_dir is not None
  manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
  assert manifest["status"] == "failed_preflight"
  assert manifest["errors"]
  assert (run_dir / "data-quality.json").exists()
  assert not (run_dir / "report.html").exists()


@pytest.mark.asyncio
async def test_factor_coverage_failure_stops_before_daily_scan(
  tmp_path: Path,
) -> None:
  config = _write_config(
    tmp_path / "study.yaml",
    tmp_path / "runs",
    minimum_events=1,
  )
  source = FakeResearchSource(_market_bars())
  source.factor_coverage_complete = False

  validation = await validate_study(config, source=source)

  assert validation["valid"] is False
  assert any("复权因子回填覆盖不足" in item for item in validation["errors"])
  assert source.daily_bar_calls == 0

  with pytest.raises(ResearchPreflightError) as exc_info:
    await run_study(config, source=source)

  assert source.daily_bar_calls == 0
  run_dir = exc_info.value.run_dir
  assert run_dir is not None
  manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
  quality = json.loads((run_dir / "data-quality.json").read_text(encoding="utf-8"))
  assert manifest["status"] == "failed_preflight"
  assert quality["dividend_factor_coverage"]["is_complete"] is False


@pytest.mark.asyncio
async def test_physical_memory_guard_is_an_explicit_resource_failure(
  tmp_path: Path,
) -> None:
  config = _write_config(
    tmp_path / "study.yaml",
    tmp_path / "runs",
    minimum_events=1,
  )
  payload = yaml.safe_load(config.read_text(encoding="utf-8"))
  payload["runtime"]["minimum_available_memory_gib"] = 1024
  config.write_text(
    yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
    encoding="utf-8",
  )
  source = FakeResearchSource(_market_bars())

  with pytest.raises(ResearchResourceError, match="物理内存保护触发") as exc_info:
    await run_study(config, source=source)

  assert source.daily_bar_calls == 0
  run_dir = exc_info.value.run_dir
  assert run_dir is not None
  manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
  quality = json.loads((run_dir / "data-quality.json").read_text(encoding="utf-8"))
  assert manifest["status"] == "failed_resource"
  assert manifest["failure_kind"] == "physical_memory_guard"
  assert manifest["event_count"] == 0
  assert manifest["analysis_sample_count"] == 0
  assert manifest["partial_artifacts_removed"] == []
  assert "artifacts" in manifest
  assert "pagefile" in quality["resource_error"]
  assert quality["runtime_memory"]["physical_only"] is True


@pytest.mark.asyncio
async def test_late_resource_failure_preserves_staged_diagnostics(
  tmp_path: Path,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  config = _write_config(
    tmp_path / "study.yaml",
    tmp_path / "runs",
    minimum_events=1,
  )
  source = FakeResearchSource(_market_bars())

  def fail_event_output(*args: object, **kwargs: object) -> int:
    del args, kwargs
    raise PhysicalMemoryGuardError(
      stage="write_events",
      snapshot=MemorySnapshot(
        total_physical_bytes=32 * _GIB,
        available_physical_bytes=512 * 1024**2,
        process_rss_bytes=4 * _GIB,
      ),
      reserve_bytes=_GIB,
      estimated_increment_bytes=256 * 1024**2,
    )

  monkeypatch.setattr(runner_module, "write_event_artifact", fail_event_output)

  with pytest.raises(ResearchResourceError) as exc_info:
    await run_study(config, source=source)

  run_dir = exc_info.value.run_dir
  assert run_dir is not None
  manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
  quality = json.loads((run_dir / "data-quality.json").read_text(encoding="utf-8"))
  assert manifest["status"] == "failed_resource"
  assert manifest["resource_stage"] == "write_events"
  assert manifest["event_count"] > 0
  assert manifest["analysis_sample_count"] > 0
  assert manifest["data_fingerprint"] == quality["data_fingerprint"]
  assert manifest["resource_estimate"] == quality["resource_estimate"]
  assert quality["dividend_factor_coverage"]["is_complete"] is True
