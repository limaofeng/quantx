"""Read-only factor-study runner, using the existing audited data sources."""

from __future__ import annotations

import hashlib
import html
import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml
from quantx_domain.factors import (
  FACTOR_VERSION,
  calculate_factor_frame,
  valid_factor_observations,
)

from quantx_research.artifacts import (
  artifact_index,
  create_run_directory,
  fingerprint,
  git_state,
  runtime_metadata,
  write_json,
  write_yaml,
)
from quantx_research.data import (
  DividendFactorCoverageError,
  ResearchDataSource,
  apply_dividend_adjustment,
  build_quality_report,
  combine_quality_reports,
  normalize_daily_bars,
  normalize_dividend_factors,
)
from quantx_research.data.dataset_builder import _append_instrument_dates, _deduplicate
from quantx_research.factor_config import FactorStudyConfig
from quantx_research.factor_study import (
  WARNINGS,
  add_factor_outcomes,
  analyze_factor_partitions,
)
from quantx_research.runtime_memory import (
  PhysicalMemoryGuardError,
  PhysicalMemoryMonitorError,
  RuntimeMemoryMonitor,
)
from quantx_research.staging import (
  _load_benchmark,
  _prepare_universe_and_factor_gate,
  _source_provenance,
)


def load_factor_config(path: str | Path) -> FactorStudyConfig:
  payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
  if not isinstance(payload, dict):
    raise ValueError("研究配置根节点必须是 YAML mapping")
  return FactorStudyConfig.model_validate(payload)


async def _freeze_window(
  config: FactorStudyConfig,
  *,
  source: ResearchDataSource | None,
  market_data_archive: str | Path | None,
) -> FactorStudyConfig:
  from quantx_research.runner import _research_source, _shift_years

  if config.date_range is not None:
    return config
  end = config.universe.end_date
  if end == "latest":
    async with _research_source(
      source, market_data_archive=market_data_archive
    ) as active:
      resolver = getattr(active, "latest_daily_date", None)
      if not callable(resolver):
        raise ValueError("数据源不能解析最新已持久化日线；请显式指定 date_range")
      end = await resolver(config.universe.benchmark_code)
  return config.model_copy(
    update={
      "date_range": (_shift_years(end, -config.universe.lookback_years), end),
    }
  )


async def validate_factor_study(
  config_path: str | Path,
  *,
  source: ResearchDataSource | None = None,
  market_data_archive: str | Path | None = None,
) -> dict[str, Any]:
  from quantx_research.runner import REPO_ROOT, _research_source

  config = await _freeze_window(
    load_factor_config(config_path),
    source=source,
    market_data_archive=market_data_archive,
  )
  root = REPO_ROOT / ".runtime" / "research-staging"
  root.mkdir(parents=True, exist_ok=True)
  monitor = RuntimeMemoryMonitor(
    reserve_gib=config.runtime.minimum_available_memory_gib,
    sample_interval_seconds=config.runtime.memory_sample_interval_seconds,
  )
  quality: dict[str, Any] = {}
  errors: list[str] = []
  try:
    with (
      monitor,
      tempfile.TemporaryDirectory(prefix="validate-factor-", dir=root) as temporary,
    ):
      async with _research_source(
        source, market_data_archive=market_data_archive
      ) as active:
        features = await stage_factor_features(active, config, Path(temporary), monitor)
        quality = dict(features.quality)
      _, quality = finish_factor_partitions(features, config, Path(temporary), monitor)
      errors = _preflight_errors(quality)
  except DividendFactorCoverageError as exc:
    quality["dividend_factor_coverage"] = exc.report.to_dict()
    errors = [str(exc)]
  except (PhysicalMemoryGuardError, PhysicalMemoryMonitorError) as exc:
    errors = [str(exc)]
    quality["resource_error"] = str(exc)
  quality["runtime_memory"] = monitor.to_dict()
  return {
    "valid": not errors,
    "study_id": config.study_id,
    "version": config.version,
    "event_count": 0,
    "analysis_sample_count": quality.get("analysis_sample_count", 0),
    "data_quality": quality,
    "errors": errors,
  }


async def run_factor_study(
  config_path: str | Path,
  *,
  source: ResearchDataSource | None = None,
  market_data_archive: str | Path | None = None,
  output_root: str | Path | None = None,
  now: datetime | None = None,
) -> Path:
  from quantx_research.runner import (
    REPO_ROOT,
    ResearchPreflightError,
    ResearchResourceError,
    _research_source,
    _resolve_output_root,
  )

  config = await _freeze_window(
    load_factor_config(config_path),
    source=source,
    market_data_archive=market_data_archive,
  )
  resolved = config.model_dump(mode="json")
  config_hash = fingerprint(resolved)
  root = _resolve_output_root(output_root or config.runtime.output_root)
  run_dir = create_run_directory(
    root, config.study_id, config.version, config_hash, now=now
  )
  started = datetime.now(timezone.utc)
  manifest: dict[str, Any] = {
    "run_id": run_dir.name,
    "study_id": config.study_id,
    "version": config.version,
    "status": "running",
    "started_at": started.isoformat(),
    "config_hash": config_hash,
    "factor_version": FACTOR_VERSION,
    "git": git_state(REPO_ROOT),
    "runtime": runtime_metadata(),
    "event_count": 0,
    "analysis_sample_count": 0,
    "errors": [],
  }
  write_yaml(run_dir / "resolved-config.yaml", resolved)
  write_json(run_dir / "manifest.json", manifest)
  quality: dict[str, Any] = {}
  monitor = RuntimeMemoryMonitor(
    reserve_gib=config.runtime.minimum_available_memory_gib,
    sample_interval_seconds=config.runtime.memory_sample_interval_seconds,
  )
  try:
    with (
      monitor,
      tempfile.TemporaryDirectory(prefix=".staging-", dir=run_dir) as temporary,
    ):
      staging = Path(temporary)
      async with _research_source(
        source, market_data_archive=market_data_archive
      ) as active:
        features = await stage_factor_features(active, config, staging, monitor)
        quality = dict(features.quality)
      partitions, quality = finish_factor_partitions(features, config, staging, monitor)
      errors = _preflight_errors(quality)
      if errors:
        raise ResearchPreflightError("; ".join(errors), run_dir=run_dir)
      _write_sample(partitions, run_dir / "analysis-sample.parquet", monitor)
      metrics = analyze_factor_partitions(
        partitions,
        config,
        staging_directory=staging,
        monitor=monitor,
        data_start=quality["data_start"],
        data_end=quality["data_end"],
        calendar=pd.DatetimeIndex(quality["analysis_trading_dates"]),
      )
      metrics["config_hash"] = config_hash
      metrics["data_fingerprint"] = quality["data_fingerprint"]
      metrics["warnings"] = list(
        dict.fromkeys(metrics["warnings"] + quality["warnings"])
      )
      for report in metrics["reports"]:
        report["warnings"] = list(
          dict.fromkeys(report["warnings"] + quality["warnings"])
        )
      # A threshold cohort isn't an execution event. Keep the generic run
      # event count zero instead of inventing a signal/event interpretation.
      write_json(run_dir / "metrics.json", metrics)
      if (run_dir / "metrics.json").stat().st_size > 64 * 1024**2:
        raise ResearchPreflightError(
          "结构化因子产物超过页面64MiB安全读取上限；请拆分研究配置",
          run_dir=run_dir,
        )
      table_dir = run_dir / "tables"
      table_dir.mkdir()
      for report in metrics["reports"]:
        pd.DataFrame(report["rows"]).to_csv(
          table_dir / f"{report['report_id']}.csv", index=False
        )
    manifest["status"] = "success"
  except DividendFactorCoverageError as exc:
    quality["dividend_factor_coverage"] = exc.report.to_dict()
    manifest.update(status="failed_preflight", errors=[str(exc)])
    raise ResearchPreflightError(str(exc), run_dir=run_dir) from exc
  except (PhysicalMemoryGuardError, PhysicalMemoryMonitorError) as exc:
    manifest.update(status="failed_resource", errors=[str(exc)])
    raise ResearchResourceError(str(exc), run_dir=run_dir) from exc
  except ResearchPreflightError as exc:
    manifest.update(status="failed_preflight", errors=[str(exc)])
    raise
  except Exception as exc:
    manifest.update(status="failed", errors=[f"{type(exc).__name__}: {exc}"])
    raise
  finally:
    finished = datetime.now(timezone.utc)
    quality["runtime_memory"] = monitor.to_dict()
    manifest.update(
      completed_at=finished.isoformat(),
      elapsed_seconds=(finished - started).total_seconds(),
      analysis_sample_count=quality.get("analysis_sample_count", 0),
      data_fingerprint=quality.get("data_fingerprint"),
    )
    write_json(run_dir / "data-quality.json", quality)
    if manifest["status"] == "success":
      render_factor_report(run_dir)
    manifest["artifacts"] = artifact_index(run_dir)
    write_json(run_dir / "manifest.json", manifest)
  return run_dir


@dataclass
class _FactorFeatureStage:
  paths: list[Path]
  calendar: pd.DatetimeIndex
  quality: dict[str, Any]


def factor_data_fingerprint(
  panel_sha256: str,
  factor_calendar: pd.DatetimeIndex,
  outcome_calendar: pd.DatetimeIndex,
) -> str:
  """Both calendars are inputs, even when the stock bars themselves agree."""

  def days(values: pd.DatetimeIndex) -> list[str]:
    normalized = (
      pd.DatetimeIndex(pd.to_datetime(values, utc=True))
      .tz_convert("Asia/Shanghai")
      .normalize()
      .unique()
      .sort_values()
    )
    return normalized.strftime("%Y-%m-%d").tolist()

  return fingerprint(
    {
      "panel_sha256": panel_sha256,
      "factor_calendar": days(factor_calendar),
      "outcome_calendar": days(outcome_calendar),
    }
  )


async def build_factor_partitions(
  source: ResearchDataSource,
  config: FactorStudyConfig,
  directory: Path,
  monitor: RuntimeMemoryMonitor,
) -> tuple[dict[str, list[Path]], dict[str, Any]]:
  """Convenience for an externally owned source; production separates stages."""
  features = await stage_factor_features(source, config, directory, monitor)
  return finish_factor_partitions(features, config, directory, monitor)


async def stage_factor_features(
  source: ResearchDataSource,
  config: FactorStudyConfig,
  directory: Path,
  monitor: RuntimeMemoryMonitor,
) -> _FactorFeatureStage:
  """Finish all source reads before releasing the read-only DB transaction."""
  from quantx_research.runner import resolve_analysis_window

  analysis_start, end = resolve_analysis_window(config)
  start = analysis_start - timedelta(days=max(400, config.required_lookback * 2))
  requested_source_start = start
  provenance = _source_provenance(source)
  archive_start = (provenance.get("campaign") or {}).get("start_date")
  if provenance.get("kind") == "qmt-daily-bar-archive" and archive_start:
    # A conservative calendar buffer may extend before an archive even when
    # enough observations exist. Preserve missing factor warmup as NaN; never
    # request non-existent evidence or pretend the clipped prefix was present.
    start = max(start, pd.Timestamp(archive_start).date())
  (
    codes,
    instruments,
    benchmark_instrument,
    coverage,
  ) = await _prepare_universe_and_factor_gate(
    source,
    config,
    start=start,
    end=end,
    analysis_start=analysis_start,
  )
  benchmark = await _load_benchmark(
    source,
    config.universe.benchmark_code,
    benchmark_instrument,
    start=start,
    end=end,
  )
  calendar_dates = set(pd.to_datetime(benchmark["time"]).dropna())
  factor_calendar = pd.DatetimeIndex(
    pd.to_datetime(benchmark["time"]).dropna()
  ).normalize()
  if factor_calendar.empty:
    raise ValueError("缺少基准交易日历，无法审计历史因子中的物理行情缺口")
  features_dir = directory / "features"
  months_dir = directory / "months"
  features_dir.mkdir()
  months_dir.mkdir()
  feature_paths: list[Path] = []
  quality_reports = []
  hasher = hashlib.sha256()
  sorted_codes = sorted(codes)
  # Full histories stay within one batch. Capping at 100 symbols bounds the
  # temporary feature/outcome expansion even if a user requests a larger batch.
  batch_size = min(config.runtime.batch_size, 100)
  counts = {factor_id: 0 for factor_id in config.required_factor_ids}
  for index in range(0, len(sorted_codes), batch_size):
    batch = sorted_codes[index : index + batch_size]
    estimate = len(batch) * max(1, (end - start).days) * (len(counts) + 12) * 32
    monitor.guard("factor_load_batch", estimated_increment_bytes=estimate)
    raw = normalize_daily_bars(
      await source.load_daily_bars(batch, start, end, batch_size=len(batch))
    )
    raw = raw[raw["stock_code"].isin(batch)].copy()
    raw["raw_close"] = raw["close"]
    factors = normalize_dividend_factors(
      await source.load_dividend_factors(batch, start=start, end=end)
    )
    panel = apply_dividend_adjustment(raw, factors, mode="point_in_time", as_of=end)
    metadata = instruments[instruments["stock_code"].isin(batch)]
    panel = _append_instrument_dates(panel, metadata)
    quality_reports.append(
      build_quality_report(
        panel,
        requested_codes=batch,
        requested_start=start,
        requested_end=end,
        metadata_codes=metadata["stock_code"].dropna().astype(str),
        minimum_observations=config.required_lookback,
      )
    )
    panel = _deduplicate(panel)
    hasher.update(pd.util.hash_pandas_object(panel, index=False).values.tobytes())
    if panel.empty:
      continue
    panel["event_date"] = pd.to_datetime(panel["time"]).dt.normalize()
    calendar_dates.update(panel["event_date"].dropna())
    valid = valid_factor_observations(panel)
    valid &= panel["stock_code"].isin(metadata["stock_code"])
    panel["outcome_valid"] = valid
    feature_frames = []
    for _, stock in panel.groupby("stock_code", sort=False):
      stock = stock.copy()
      for factor_id in counts:
        stock[factor_id] = np.nan
      calculation_input = stock.copy()
      calculation_input.loc[
        ~stock["outcome_valid"],
        [
          "open",
          "high",
          "low",
          "close",
          "raw_close",
          "volume",
          "amount",
        ],
      ] = np.nan
      computed = calculate_factor_frame(
        calculation_input, trading_dates=factor_calendar
      )
      for factor_id in counts:
        stock[factor_id] = pd.to_numeric(computed[factor_id], errors="coerce").astype(
          float
        )
      feature_frames.append(stock)
    featured = pd.concat(feature_frames, ignore_index=True)
    path = features_dir / f"batch-{index // batch_size:05d}.parquet"
    featured[
      [
        "stock_code",
        "event_date",
        "open",
        "close",
        "outcome_valid",
        "open_date",
        *counts,
      ]
    ].to_parquet(path, index=False)
    feature_paths.append(path)
  calendar = pd.DatetimeIndex(sorted(calendar_dates))
  quality = combine_quality_reports(
    quality_reports,
    requested_codes=sorted_codes,
    requested_start=start,
    requested_end=end,
  ).to_dict()
  quality.update(
    {
      "factor_version": FACTOR_VERSION,
      "dividend_factor_coverage": coverage.to_dict(),
      "data_fingerprint": factor_data_fingerprint(
        hasher.hexdigest(), factor_calendar, calendar
      ),
      "source_provenance": _source_provenance(source),
      "requested_analysis_start": analysis_start.isoformat(),
      "requested_analysis_end": end.isoformat(),
      "requested_source_start": requested_source_start.isoformat(),
      "actual_source_start": start.isoformat(),
      "calendar_sessions": len(calendar),
      "analysis_trading_dates": [
        day.date().isoformat()
        for day in calendar
        if pd.Timestamp(analysis_start) <= day <= pd.Timestamp(end)
      ],
      "warnings": list(dict.fromkeys([*quality["warnings"], *WARNINGS])),
      "universe": config.universe.model_dump(mode="json"),
    }
  )
  if start > requested_source_start:
    quality["warnings"].append(
      "复权和因子预热查询受 archive 起点限制；完整历史窗口不足的因子值保持不可用。"
    )
  return _FactorFeatureStage(feature_paths, calendar, quality)


def finish_factor_partitions(
  features: _FactorFeatureStage,
  config: FactorStudyConfig,
  directory: Path,
  monitor: RuntimeMemoryMonitor,
) -> tuple[dict[str, list[Path]], dict[str, Any]]:
  """CPU/disk-only phase: no database connection is retained by the runner."""
  from quantx_research.runner import resolve_analysis_window

  analysis_start, end = resolve_analysis_window(config)
  months_dir = directory / "months"
  counts = {factor_id: 0 for factor_id in config.required_factor_ids}
  actual_dates: set[pd.Timestamp] = set()
  partitions: dict[str, list[Path]] = {}
  sample_count = 0
  for index, path in enumerate(features.paths):
    monitor.guard(
      "factor_outcomes_batch",
      estimated_increment_bytes=pq.read_metadata(path).num_rows
      * (len(counts) + len(config.outcomes.horizons) * 2 + 8)
      * 32,
    )
    panel = pd.read_parquet(path)
    panel = add_factor_outcomes(panel, features.calendar, config.outcomes.horizons)
    panel = panel[
      panel["event_date"].between(pd.Timestamp(analysis_start), pd.Timestamp(end))
      & panel["outcome_valid"]
    ].copy()
    if config.universe.minimum_listing_days:
      panel = panel[
        (panel["event_date"] - panel["open_date"]).dt.days.ge(
          config.universe.minimum_listing_days
        )
      ]
    panel = panel.drop(columns=["open", "close", "outcome_valid", "open_date"])
    sample_count += len(panel)
    actual_dates.update(panel["event_date"].dropna().unique())
    for factor_id in counts:
      counts[factor_id] += int(np.isfinite(panel[factor_id]).sum())
    for month, monthly in panel.groupby(panel["event_date"].dt.strftime("%Y-%m")):
      output = months_dir / f"{month}-{index:05d}.parquet"
      monthly.to_parquet(output, index=False, row_group_size=65_536)
      partitions.setdefault(str(month), []).append(output)
    monitor.checkpoint("factor_outcomes_written")
  quality = dict(features.quality)
  quality.update(
    {
      "analysis_sample_count": sample_count,
      "factor_valid_counts": counts,
      "data_start": pd.Timestamp(min(actual_dates)).date().isoformat()
      if actual_dates
      else None,
      "data_end": pd.Timestamp(max(actual_dates)).date().isoformat()
      if actual_dates
      else None,
    }
  )
  return partitions, quality


def _preflight_errors(quality: dict[str, Any]) -> list[str]:
  errors = []
  if not quality.get("analysis_sample_count"):
    errors.append("研究区间没有有效行情样本")
  if not any(quality.get("factor_valid_counts", {}).values()):
    errors.append("所选因子均缺少足够的历史窗口")
  return errors


def _write_sample(
  partitions: dict[str, list[Path]], target: Path, monitor: RuntimeMemoryMonitor
) -> None:
  temporary = target.with_name(f".{target.name}.partial")
  writer = None
  try:
    for month in sorted(partitions):
      for path in partitions[month]:
        for batch in pq.ParquetFile(path).iter_batches(batch_size=65_536):
          monitor.guard(
            "factor_sample_artifact", estimated_increment_bytes=batch.nbytes * 3
          )
          table = pa.Table.from_batches([batch])
          if writer is None:
            writer = pq.ParquetWriter(temporary, table.schema)
          writer.write_table(table)
    if writer is not None:
      writer.close()
      writer = None
      temporary.replace(target)
  finally:
    if writer is not None:
      writer.close()
    temporary.unlink(missing_ok=True)


def render_factor_report(run_dir: str | Path) -> Path:
  directory = Path(run_dir)
  metrics = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))

  def escape(value: object) -> str:
    return html.escape(str(value), quote=True)

  sections = [
    "<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>QuantX 因子研究</title>",
    "<style>body{font:14px system-ui;background:#050b16;color:#d9e5f4;margin:24px}table{border-collapse:collapse;width:100%}td,th{padding:6px;border-bottom:1px solid #29374b;text-align:right}th:first-child,td:first-child{text-align:left}section{margin:28px 0}small{color:#9cabbd}</style>",
    "<h1>QuantX 因子历史关联研究</h1>",
    f"<p>{escape(metrics['data_start'])} — {escape(metrics['data_end'])} · 定义 {escape(metrics['factor_version'])}</p>",
    "<p>主口径 C(T+h)/C(T)-1；辅助口径 C(T+h)/O(T+1)-1。单位：交易日；所有比例和收益为小数。</p>",
    "<ul>"
    + "".join(f"<li>{escape(warning)}</li>" for warning in metrics["warnings"])
    + "</ul>",
  ]
  for report in metrics["reports"]:
    sections.append(
      f"<section id='{escape(report['report_id'])}'><h2>{escape(report['report_id'])}</h2>"
    )
    sections.append(
      "<pre>"
      + escape(
        json.dumps(
          {
            key: report[key]
            for key in ("definitions", "conditions", "coverage", "distribution")
          },
          ensure_ascii=False,
          indent=2,
        )
      )
      + "</pre>"
    )
    sections.append(
      pd.DataFrame(report["rows"]).to_html(
        index=False, escape=True, na_rep="—", float_format=lambda value: f"{value:.6f}"
      )
    )
    sections.append("</section>")
  sections.append("</html>")
  output = directory / "report.html"
  output.write_text("\n".join(sections), encoding="utf-8")
  return output
