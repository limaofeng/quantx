"""Offline study orchestration and reproducible artifact generation."""

from __future__ import annotations

import hashlib
import json
import tempfile
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import numpy as np
import pandas as pd
import yaml

from quantx_research.artifacts import (
  artifact_index,
  create_run_directory,
  directory_fingerprint,
  file_sha256,
  fingerprint,
  git_state,
  runtime_metadata,
  write_json,
  write_yaml,
)
from quantx_research.core import StudyConfig, event_record_columns
from quantx_research.data import (
  DatasetBuilder,
  DividendFactorCoverageError,
  InfrastructureResearchDataSource,
  QmtDailyBarArchiveResearchDataSource,
  ResearchDataset,
  ResearchDataSource,
  describe_qmt_daily_bar_archive,
)
from quantx_research.reporting import render_report
from quantx_research.runtime_memory import (
  PhysicalMemoryGuardError,
  PhysicalMemoryMonitorError,
  RuntimeMemoryMonitor,
)
from quantx_research.staged_study import analyze_staged_volume_sample
from quantx_research.staging import (
  StagedVolumeDataset,
  _normalize_fingerprint_frame,
  build_staged_volume_dataset,
  write_analysis_sample_artifact,
  write_event_artifact,
)

REPO_ROOT = Path(__file__).resolve().parents[4]


class ResearchPreflightError(RuntimeError):
  """The requested study cannot produce defensible results from current data."""

  def __init__(self, message: str, *, run_dir: Path | None = None) -> None:
    super().__init__(message)
    self.run_dir = run_dir


class ResearchResourceError(RuntimeError):
  """The run stopped because its physical-resource safety boundary fired."""

  def __init__(self, message: str, *, run_dir: Path | None = None) -> None:
    super().__init__(message)
    self.run_dir = run_dir


def load_study_config(path: str | Path) -> StudyConfig:
  config_path = Path(path)
  payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
  if not isinstance(payload, dict):
    raise ValueError("研究配置根节点必须是 YAML mapping")
  return StudyConfig.model_validate(payload)


def resolve_source_window(config: StudyConfig) -> tuple[date, date]:
  """Resolve a conservative query window including all feature lookback rows."""
  analysis_start, requested_end = resolve_analysis_window(config)
  calendar_buffer = max(config.required_lookback * 2, 400)
  return analysis_start - timedelta(days=calendar_buffer), requested_end


def resolve_analysis_window(config: StudyConfig) -> tuple[date, date]:
  """Resolve the requested analysis interval without its feature warmup."""
  if config.date_range is not None:
    analysis_start, requested_end = config.date_range
  else:
    requested_end = (
      date.today() if config.universe.end_date == "latest" else config.universe.end_date
    )
    analysis_start = _shift_years(requested_end, -config.universe.lookback_years)
  return analysis_start, requested_end


async def validate_study(
  config_path: str | Path,
  *,
  source: ResearchDataSource | None = None,
  market_data_archive: str | Path | None = None,
) -> dict[str, Any]:
  config = load_study_config(config_path)
  staging_parent = REPO_ROOT / ".runtime" / "research-staging"
  staging_parent.mkdir(parents=True, exist_ok=True)
  monitor = RuntimeMemoryMonitor(
    reserve_gib=config.runtime.minimum_available_memory_gib,
    sample_interval_seconds=config.runtime.memory_sample_interval_seconds,
  )
  try:
    with (
      monitor,
      tempfile.TemporaryDirectory(
        prefix="validate-",
        dir=staging_parent,
      ) as staging_directory,
    ):
      staged = await _build_staged_dataset(
        config,
        source=source,
        market_data_archive=market_data_archive,
        staging_directory=staging_directory,
        monitor=monitor,
      )
      errors = _staged_preflight_errors(staged, config)
  except DividendFactorCoverageError as exc:
    return {
      "valid": False,
      "study_id": config.study_id,
      "version": config.version,
      "event_count": 0,
      "analysis_sample_count": 0,
      "data_quality": {
        "dividend_factor_coverage": exc.report.to_dict(),
      },
      "errors": [str(exc)],
    }
  except (PhysicalMemoryGuardError, PhysicalMemoryMonitorError) as exc:
    return {
      "valid": False,
      "study_id": config.study_id,
      "version": config.version,
      "event_count": 0,
      "analysis_sample_count": 0,
      "data_quality": {
        "runtime_memory": monitor.to_dict(),
        "resource_error": str(exc),
      },
      "failure_kind": "physical_memory_guard",
      "errors": [str(exc)],
    }
  quality = staged.quality.to_dict()
  quality["dividend_factor_coverage"] = staged.factor_coverage.to_dict()
  quality["resource_estimate"] = staged.resource_estimate
  quality["runtime_memory"] = monitor.to_dict()
  if staged.source_provenance:
    quality["source_provenance"] = staged.source_provenance
  return {
    "valid": not errors,
    "study_id": config.study_id,
    "version": config.version,
    "event_count": staged.event_count,
    "analysis_sample_count": staged.analysis_sample_count,
    "data_quality": quality,
    "errors": errors,
  }


async def run_study(
  config_path: str | Path,
  *,
  source: ResearchDataSource | None = None,
  market_data_archive: str | Path | None = None,
  output_root: str | Path | None = None,
  now: datetime | None = None,
) -> Path:
  if source is not None and market_data_archive is not None:
    raise ValueError("source 与 market_data_archive 不能同时指定")
  config = load_study_config(config_path)
  resolved_config = config.model_dump(mode="json")
  config_hash = fingerprint(resolved_config)
  root = _resolve_output_root(output_root or config.runtime.output_root)
  requested_data_source = (
    describe_qmt_daily_bar_archive(market_data_archive)
    if market_data_archive is not None
    else None
  )
  run_dir = create_run_directory(
    root,
    config.study_id,
    config.version,
    config_hash,
    now=now,
  )
  started_at = datetime.now(timezone.utc)
  base_manifest = {
    "run_id": run_dir.name,
    "study_id": config.study_id,
    "version": config.version,
    "status": "running",
    "started_at": started_at,
    "config_hash": config_hash,
    "code_fingerprint": directory_fingerprint(REPO_ROOT / "apps" / "research"),
    "lockfile_sha256": file_sha256(REPO_ROOT / "uv.lock"),
    "git": git_state(REPO_ROOT),
    "runtime": runtime_metadata(),
  }
  if requested_data_source is not None:
    base_manifest["requested_data_source"] = requested_data_source
  write_yaml(run_dir / "resolved-config.yaml", resolved_config)
  write_json(run_dir / "manifest.json", base_manifest)

  monitor = RuntimeMemoryMonitor(
    reserve_gib=config.runtime.minimum_available_memory_gib,
    sample_interval_seconds=config.runtime.memory_sample_interval_seconds,
  )
  quality: dict[str, Any] = {}
  staged: StagedVolumeDataset | None = None
  try:
    with (
      monitor,
      tempfile.TemporaryDirectory(
        prefix=".staging-",
        dir=run_dir,
      ) as staging_directory,
    ):
      staged = await _build_staged_dataset(
        config,
        source=source,
        market_data_archive=market_data_archive,
        staging_directory=staging_directory,
        monitor=monitor,
      )
      quality = staged.quality.to_dict()
      quality["dividend_factor_coverage"] = staged.factor_coverage.to_dict()
      quality["data_fingerprint"] = staged.data_fingerprint
      quality["resource_estimate"] = staged.resource_estimate
      quality["runtime_memory"] = monitor.to_dict()
      if staged.source_provenance:
        quality["source_provenance"] = staged.source_provenance
      write_json(run_dir / "data-quality.json", quality)

      errors = _staged_preflight_errors(staged, config)
      if errors:
        manifest = {
          **base_manifest,
          "status": "failed_preflight",
          "completed_at": datetime.now(timezone.utc),
          "event_count": staged.event_count,
          "analysis_sample_count": staged.analysis_sample_count,
          "data_fingerprint": quality["data_fingerprint"],
          "errors": errors,
          "resource_estimate": staged.resource_estimate,
          "runtime_memory": monitor.to_dict(),
        }
        if staged.source_provenance:
          manifest["source_provenance"] = staged.source_provenance
        write_json(run_dir / "manifest.json", manifest)
        raise ResearchPreflightError("; ".join(errors), run_dir=run_dir)

      events, result = analyze_staged_volume_sample(
        staged,
        config,
        monitor=monitor,
      )
      write_event_artifact(
        events,
        run_dir / "events.parquet",
        config,
        monitor=monitor,
      )
      written_sample_rows = write_analysis_sample_artifact(
        staged,
        run_dir / "analysis-sample.parquet",
        config,
        monitor=monitor,
      )
      if written_sample_rows != staged.analysis_sample_count:
        raise RuntimeError(
          "analysis-sample 流式写入行数不一致: "
          f"{written_sample_rows} != {staged.analysis_sample_count}"
        )
      metrics = result.model_dump(mode="json")
      quality["runtime_memory"] = monitor.to_dict()
      write_json(run_dir / "data-quality.json", quality)

    quality["runtime_memory"] = monitor.to_dict()
    write_json(run_dir / "data-quality.json", quality)
    write_json(run_dir / "metrics.json", metrics)
    _write_result_tables(run_dir, metrics)

    completed_at = datetime.now(timezone.utc)
    manifest = {
      **base_manifest,
      "status": "success",
      "completed_at": completed_at,
      "elapsed_seconds": (completed_at - started_at).total_seconds(),
      "event_count": len(events),
      "analysis_sample_count": staged.analysis_sample_count,
      "data_fingerprint": quality["data_fingerprint"],
      "resource_estimate": staged.resource_estimate,
      "runtime_memory": monitor.to_dict(),
    }
    if staged.source_provenance:
      manifest["source_provenance"] = staged.source_provenance
    render_report(
      run_dir,
      metrics,
      quality,
      manifest,
      resolved_config,
    )
    manifest["artifacts"] = artifact_index(run_dir)
    write_json(run_dir / "manifest.json", manifest)
    return run_dir
  except DividendFactorCoverageError as exc:
    quality = {
      "dividend_factor_coverage": exc.report.to_dict(),
    }
    write_json(run_dir / "data-quality.json", quality)
    completed_at = datetime.now(timezone.utc)
    message = str(exc)
    manifest = {
      **base_manifest,
      "status": "failed_preflight",
      "completed_at": completed_at,
      "elapsed_seconds": (completed_at - started_at).total_seconds(),
      "event_count": 0,
      "analysis_sample_count": 0,
      "errors": [message],
    }
    write_json(run_dir / "manifest.json", manifest)
    raise ResearchPreflightError(message, run_dir=run_dir) from exc
  except (PhysicalMemoryGuardError, PhysicalMemoryMonitorError) as exc:
    removed_partials = _remove_partial_output_artifacts(run_dir)
    runtime_memory = monitor.to_dict()
    quality = {
      **quality,
      "runtime_memory": runtime_memory,
      "resource_error": str(exc),
    }
    if staged is not None:
      quality.setdefault("dividend_factor_coverage", staged.factor_coverage.to_dict())
      quality.setdefault("data_fingerprint", staged.data_fingerprint)
      quality.setdefault("resource_estimate", staged.resource_estimate)
    write_json(run_dir / "data-quality.json", quality)
    completed_at = datetime.now(timezone.utc)
    event_count = staged.event_count if staged is not None else 0
    analysis_sample_count = staged.analysis_sample_count if staged is not None else 0
    manifest = {
      **base_manifest,
      "status": "failed_resource",
      "failure_kind": "physical_memory_guard",
      "completed_at": completed_at,
      "elapsed_seconds": (completed_at - started_at).total_seconds(),
      "event_count": event_count,
      "analysis_sample_count": analysis_sample_count,
      "errors": [str(exc)],
      "resource_stage": getattr(exc, "stage", None),
      "runtime_memory": runtime_memory,
      "partial_artifacts_removed": removed_partials,
      "artifacts": artifact_index(run_dir),
    }
    if staged is not None:
      manifest["data_fingerprint"] = staged.data_fingerprint
      manifest["resource_estimate"] = staged.resource_estimate
      if staged.source_provenance:
        manifest["source_provenance"] = staged.source_provenance
    write_json(run_dir / "manifest.json", manifest)
    raise ResearchResourceError(str(exc), run_dir=run_dir) from exc
  except ResearchPreflightError:
    raise
  except Exception as exc:
    if quality:
      quality["runtime_memory"] = monitor.to_dict()
      quality["runtime_error"] = f"{type(exc).__name__}: {exc}"
      write_json(run_dir / "data-quality.json", quality)
    failed_manifest = {
      **base_manifest,
      "status": "failed",
      "completed_at": datetime.now(timezone.utc),
      "error": f"{type(exc).__name__}: {exc}",
      "runtime_memory": monitor.to_dict(),
      "artifacts": artifact_index(run_dir),
    }
    write_json(run_dir / "manifest.json", failed_manifest)
    raise


def _remove_partial_output_artifacts(run_dir: Path) -> list[str]:
  removed: list[str] = []
  for artifact_name in ("events.parquet", "analysis-sample.parquet"):
    partial = run_dir / f".{artifact_name}.partial"
    if partial.exists():
      partial.unlink()
      removed.append(partial.name)
  return removed


def render_existing(run_dir: str | Path) -> Path:
  directory = Path(run_dir)
  metrics = _read_json(directory / "metrics.json")
  quality = _read_json(directory / "data-quality.json")
  manifest = _read_json(directory / "manifest.json")
  config_path = directory / "resolved-config.yaml"
  config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
  report = render_report(directory, metrics, quality, manifest, config)
  manifest["artifacts"] = artifact_index(directory)
  write_json(directory / "manifest.json", manifest)
  return report


async def _build_dataset(
  config: StudyConfig,
  *,
  source: ResearchDataSource | None,
  market_data_archive: str | Path | None = None,
) -> ResearchDataset:
  start, end = resolve_source_window(config)
  analysis_start, _ = resolve_analysis_window(config)
  async with _research_source(
    source,
    market_data_archive=market_data_archive,
  ) as active_source:
    return await DatasetBuilder(active_source).build(
      start=start,
      end=end,
      stock_codes=config.universe.stock_codes,
      benchmark_code=config.universe.benchmark_code,
      batch_size=config.runtime.batch_size,
      adjustment="point_in_time",
      minimum_observations=config.required_lookback,
      factor_start=start,
      universe_start=analysis_start,
      require_factor_coverage=True,
    )


async def _build_staged_dataset(
  config: StudyConfig,
  *,
  source: ResearchDataSource | None,
  market_data_archive: str | Path | None,
  staging_directory: str | Path,
  monitor: RuntimeMemoryMonitor,
) -> StagedVolumeDataset:
  start, end = resolve_source_window(config)
  requested_analysis_start, _ = resolve_analysis_window(config)
  async with _research_source(
    source,
    market_data_archive=market_data_archive,
  ) as active_source:
    return await build_staged_volume_dataset(
      active_source,
      config,
      start=start,
      end=end,
      analysis_start=requested_analysis_start,
      directory=staging_directory,
      monitor=monitor,
    )


@asynccontextmanager
async def _research_source(
  source: ResearchDataSource | None,
  *,
  market_data_archive: str | Path | None = None,
) -> AsyncIterator[ResearchDataSource]:
  if source is not None and market_data_archive is not None:
    raise ValueError("source 与 market_data_archive 不能同时指定")
  if source is not None:
    yield source
    return
  async with InfrastructureResearchDataSource() as active:
    if market_data_archive is None:
      yield active
      return
    yield QmtDailyBarArchiveResearchDataSource(
      market_data_archive,
      metadata_source=active,
    )


def _staged_preflight_errors(
  staged: StagedVolumeDataset,
  config: StudyConfig,
) -> list[str]:
  errors: list[str] = []
  quality = staged.quality
  if not quality.is_usable:
    errors.append("历史行情数据质量不足，无法构造研究面板")
  if not staged.factor_coverage.is_complete:
    errors.append("复权因子回填未覆盖全部研究标的和数据窗口")
  requested_symbols = len(quality.requested_codes)
  usable_symbols = sum(
    1
    for item in quality.coverage
    if item.valid_rows > 0
    and item.has_minimum_observations
    and item.has_instrument_metadata
    and item.adjustment_valid
  )
  if usable_symbols < config.quality.minimum_usable_symbols:
    errors.append(
      f"可用标的不足: {usable_symbols} < {config.quality.minimum_usable_symbols}"
    )
  history_coverage = usable_symbols / requested_symbols if requested_symbols else 0.0
  if history_coverage < config.quality.minimum_history_coverage_ratio:
    errors.append(
      "股票历史覆盖率不足: "
      f"{history_coverage:.1%} < "
      f"{config.quality.minimum_history_coverage_ratio:.1%} "
      f"({usable_symbols}/{requested_symbols})"
    )
  end_coverage_symbols = sum(1 for item in quality.coverage if item.has_end_coverage)
  end_coverage = end_coverage_symbols / requested_symbols if requested_symbols else 0.0
  if end_coverage < config.quality.minimum_end_coverage_ratio:
    errors.append(
      "研究截止日覆盖率不足: "
      f"{end_coverage:.1%} < {config.quality.minimum_end_coverage_ratio:.1%} "
      f"({end_coverage_symbols}/{requested_symbols})"
    )
  if staged.event_count < config.quality.minimum_total_events:
    errors.append(
      f"有效事件不足: {staged.event_count} < {config.quality.minimum_total_events}"
    )
  if (
    config.outcomes.include_benchmark_excess
    and staged.event_count > 0
    and staged.benchmark_coverage_ratio
    < config.quality.minimum_benchmark_coverage_ratio
  ):
    errors.append(
      "沪深300收益覆盖率不足: "
      f"{staged.benchmark_coverage_ratio:.1%} < "
      f"{config.quality.minimum_benchmark_coverage_ratio:.1%}"
    )
  if staged.panel_row_count == 0:
    errors.append("股票研究面板为空")
  return errors


def _preflight_errors(
  dataset: ResearchDataset,
  events: pd.DataFrame,
  config: StudyConfig,
  *,
  analysis_sample: pd.DataFrame | None = None,
) -> list[str]:
  errors: list[str] = []
  if not dataset.quality.is_usable:
    errors.append("历史行情数据质量不足，无法构造研究面板")
  if dataset.factor_coverage is None:
    errors.append("缺少复权因子回填覆盖证明")
  elif not dataset.factor_coverage.is_complete:
    errors.append("复权因子回填未覆盖全部研究标的和数据窗口")
  requested_symbols = len(dataset.quality.requested_codes)
  usable_symbols = sum(
    1
    for item in dataset.quality.coverage
    if item.valid_rows > 0
    and item.has_minimum_observations
    and item.has_instrument_metadata
    and item.adjustment_valid
  )
  if usable_symbols < config.quality.minimum_usable_symbols:
    errors.append(
      f"可用标的不足: {usable_symbols} < {config.quality.minimum_usable_symbols}"
    )
  history_coverage = usable_symbols / requested_symbols if requested_symbols else 0.0
  if history_coverage < config.quality.minimum_history_coverage_ratio:
    errors.append(
      "股票历史覆盖率不足: "
      f"{history_coverage:.1%} < "
      f"{config.quality.minimum_history_coverage_ratio:.1%} "
      f"({usable_symbols}/{requested_symbols})"
    )
  end_coverage_symbols = sum(
    1 for item in dataset.quality.coverage if item.has_end_coverage
  )
  end_coverage = end_coverage_symbols / requested_symbols if requested_symbols else 0.0
  if end_coverage < config.quality.minimum_end_coverage_ratio:
    errors.append(
      "研究截止日覆盖率不足: "
      f"{end_coverage:.1%} < {config.quality.minimum_end_coverage_ratio:.1%} "
      f"({end_coverage_symbols}/{requested_symbols})"
    )
  if len(events) < config.quality.minimum_total_events:
    errors.append(
      f"有效事件不足: {len(events)} < {config.quality.minimum_total_events}"
    )
  if config.outcomes.include_benchmark_excess and not events.empty:
    benchmark_coverage = _minimum_benchmark_coverage(
      analysis_sample if analysis_sample is not None else events,
      config,
    )
    if benchmark_coverage < config.quality.minimum_benchmark_coverage_ratio:
      errors.append(
        "沪深300收益覆盖率不足: "
        f"{benchmark_coverage:.1%} < "
        f"{config.quality.minimum_benchmark_coverage_ratio:.1%}"
      )
  if dataset.panel.empty:
    errors.append("股票研究面板为空")
  return errors


def _minimum_benchmark_coverage(
  events: pd.DataFrame,
  config: StudyConfig,
) -> float:
  ratios: list[float] = []
  for horizon in config.outcomes.horizons:
    prefixes = []
    if config.outcomes.include_close_response:
      prefixes.append("close")
    if config.outcomes.include_next_open_return:
      prefixes.append("next_open")
    for prefix in prefixes:
      absolute_column = f"{prefix}_return_h{horizon}"
      benchmark_column = f"csi300_excess_{prefix}_h{horizon}"
      if absolute_column not in events:
        continue
      absolute = pd.to_numeric(events[absolute_column], errors="coerce")
      absolute_valid = pd.Series(
        np.isfinite(absolute.to_numpy(dtype=float)),
        index=events.index,
      )
      denominator = int(absolute_valid.sum())
      if denominator == 0:
        continue
      if benchmark_column not in events:
        ratios.append(0.0)
        continue
      benchmark = pd.to_numeric(events[benchmark_column], errors="coerce")
      benchmark_valid = pd.Series(
        np.isfinite(benchmark.to_numpy(dtype=float)),
        index=events.index,
      )
      ratios.append(float((absolute_valid & benchmark_valid).sum() / denominator))
  return min(ratios, default=0.0)


def _event_output_frame(
  events: pd.DataFrame,
  config: StudyConfig,
) -> pd.DataFrame:
  columns = [
    column for column in event_record_columns(config) if column in events.columns
  ]
  return events.loc[:, columns].copy()


def _dataset_fingerprint(dataset: ResearchDataset) -> str:
  digest = hashlib.sha256()
  for name, frame in (
    ("panel", dataset.panel),
    ("benchmark", dataset.benchmark),
    ("instruments", dataset.instruments),
    ("factors", dataset.factors),
  ):
    digest.update(name.encode("utf-8"))
    if frame.empty:
      digest.update(b"<empty>")
      continue
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
    digest.update(hashed.to_numpy().tobytes())
  return digest.hexdigest()


def _write_result_tables(run_dir: Path, metrics: dict[str, Any]) -> None:
  tables_dir = run_dir / "tables"
  tables_dir.mkdir(parents=True, exist_ok=True)
  _rows_to_csv(
    tables_dir / "grouped-statistics.csv",
    _flatten_grouped(metrics.get("grouped_statistics") or []),
  )
  _rows_to_csv(
    tables_dir / "event-curve.csv",
    metrics.get("event_curve") or [],
  )
  _rows_to_csv(
    tables_dir / "comparison.csv",
    _flatten_grouped(metrics.get("comparison") or []),
  )
  for name, rows in sorted((metrics.get("comparison_sensitivity") or {}).items()):
    safe_name = "".join(
      char if char.isalnum() or char in "-_" else "-" for char in name
    )
    _rows_to_csv(
      tables_dir / f"comparison-{safe_name}.csv",
      _flatten_grouped(rows),
    )
  _rows_to_csv(
    tables_dir / "regressions.csv",
    _flatten_regressions(metrics.get("regressions") or []),
  )
  for name, rows in sorted((metrics.get("robustness") or {}).items()):
    safe_name = "".join(
      char if char.isalnum() or char in "-_" else "-" for char in name
    )
    _rows_to_csv(
      tables_dir / f"robustness-{safe_name}.csv",
      _flatten_grouped(rows),
    )


def _rows_to_csv(path: Path, rows: list[dict[str, Any]]) -> None:
  frame = pd.DataFrame(rows)
  frame.to_csv(path, index=False, encoding="utf-8-sig")


def _flatten_grouped(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
  flattened: list[dict[str, Any]] = []
  for row in rows:
    values = dict(row)
    dimensions = values.pop("dimensions", {}) or {}
    flattened.append({**dimensions, **values})
  return flattened


def _flatten_regressions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
  flattened: list[dict[str, Any]] = []
  for row in rows:
    base = {
      key: value
      for key, value in row.items()
      if key not in {"coefficients", "warnings"}
    }
    for coefficient in row.get("coefficients") or []:
      flattened.append(
        {
          **base,
          **coefficient,
          "warnings": " | ".join(row.get("warnings") or []),
        }
      )
  return flattened


def _resolve_output_root(value: str | Path) -> Path:
  path = Path(value)
  return path if path.is_absolute() else REPO_ROOT / path


def _shift_years(value: date, years: int) -> date:
  try:
    return value.replace(year=value.year + years)
  except ValueError:
    return value.replace(month=2, day=28, year=value.year + years)


def _read_json(path: Path) -> dict[str, Any]:
  value = json.loads(path.read_text(encoding="utf-8"))
  if not isinstance(value, dict):
    raise ValueError(f"{path} 根节点必须为 JSON object")
  return value
