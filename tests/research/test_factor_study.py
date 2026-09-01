from __future__ import annotations

import copy
import json
import shutil
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import quantx_research.factor_runner as factor_runner_module
import quantx_research.factor_study as factor_study_module
import quantx_research.runner as runner_module
import yaml
from quantx_domain.factors import FACTOR_DEFINITIONS, calculate_factor_frame
from quantx_research.core.statistics import DateBlockBootstrap
from quantx_research.data import InfrastructureResearchDataSource
from quantx_research.factor_config import FactorStudyConfig
from quantx_research.factor_runner import (
  build_factor_partitions,
  factor_data_fingerprint,
)
from quantx_research.factor_study import (
  _Cell,
  _cell_result,
  add_factor_outcomes,
  analyze_factor_partitions,
  factor_groups,
  report_match_key,
)
from quantx_research.runner import render_existing, run_study, validate_study
from quantx_research.runtime_memory import RuntimeMemoryMonitor

from tests.research.test_runner import FakeResearchSource, _market_bars


def test_factor_config_normalizes_identity_and_rejects_unsupported_filters() -> None:
  conditions = [
    {"factor_id": "volume_ratio", "operator": "gte", "value": 0},
    {"factor_id": "rsi6", "operator": "between", "value": 0, "value_to": 40},
  ]
  first = FactorStudyConfig(conditions=conditions)
  second = FactorStudyConfig(conditions=conditions[::-1] + conditions)
  assert first.conditions == second.conditions
  assert first.outcomes.horizons == tuple(range(1, 21))
  assert first.statistics.run_regression is False
  assert report_match_key(
    "joint",
    list(first.required_factor_ids),
    list(first.conditions),
    first.universe.identity(),
  ) == report_match_key(
    "joint",
    list(second.required_factor_ids),
    list(second.conditions),
    second.universe.identity(),
  )
  first_universe = FactorStudyConfig(
    factor_ids=["change_pct"],
    date_range=("2025-01-01", "2025-06-30"),
    universe={
      "stock_codes": ["600000.SH", "000001.SZ"],
      "minimum_listing_days": 0,
    },
  )
  reordered = first_universe.model_copy(
    update={
      "universe": first_universe.universe.model_copy(
        update={"stock_codes": ("000001.SZ", "600000.SH")}
      )
    }
  )
  assert first_universe.sample_identity == reordered.sample_identity
  variants = [
    first_universe.model_copy(
      update={
        "universe": first_universe.universe.model_copy(
          update={"stock_codes": ("000001.SZ", "000002.SZ")}
        )
      }
    ),
    first_universe.model_copy(
      update={
        "universe": first_universe.universe.model_copy(
          update={"minimum_listing_days": 500}
        )
      }
    ),
    first_universe.model_copy(
      update={
        "universe": first_universe.universe.model_copy(
          update={"benchmark_code": "000905.SH"}
        )
      }
    ),
    first_universe.model_copy(
      update={"date_range": (date(2025, 1, 2), date(2025, 6, 30))}
    ),
  ]
  first_key = report_match_key(
    "joint", ["change_pct"], [], first_universe.sample_identity
  )
  assert all(
    report_match_key("joint", ["change_pct"], [], item.sample_identity) != first_key
    for item in variants
  )
  for payload in (
    {"factor_ids": ["roe_ttm"]},
    {"factor_ids": ["volume_ratio"], "outcomes": {"horizons": [61]}},
    {"factor_ids": ["volume_ratio"], "universe": {"exclude_st": True}},
    {"factor_ids": ["volume_ratio"], "universe": {"include_industries": ["银行"]}},
    {
      "conditions": [
        {"factor_id": "volume_ratio", "operator": "gte", "value": float("nan")}
      ]
    },
  ):
    with pytest.raises(ValueError):
      FactorStudyConfig.model_validate(payload)


def test_default_factor_configs_cover_every_researchable_definition() -> None:
  root = Path(__file__).resolve().parents[2]
  wanted = {factor.id for factor in FACTOR_DEFINITIONS if factor.research_supported}
  for filename in ("factor_study_v1.yaml", "factor_study_v1_20260729.yaml"):
    payload = yaml.safe_load(
      (root / "apps/research/configs" / filename).read_text(encoding="utf-8")
    )
    config = FactorStudyConfig.model_validate(payload)
    assert set(config.factor_ids) == wanted
    assert len(config.conditions) == 2


def test_data_fingerprint_includes_canonical_factor_and_outcome_calendars() -> None:
  calendar = pd.bdate_range("2025-01-01", periods=5)
  original = factor_data_fingerprint("a" * 64, calendar, calendar)
  assert original == factor_data_fingerprint(
    "a" * 64, calendar[::-1].append(calendar), calendar
  )
  assert original != factor_data_fingerprint("a" * 64, calendar.delete(2), calendar)
  assert original != factor_data_fingerprint("a" * 64, calendar, calendar.delete(2))
  assert original != factor_data_fingerprint("b" * 64, calendar, calendar)


def test_quantiles_never_split_ties_and_constant_values_do_not_make_five_groups() -> (
  None
):
  groups = factor_groups(pd.Series([1, 1, 1, 2, 3, 4, 5]), binary=False)
  assert len(set(groups.iloc[:3])) == 1
  assert factor_groups(pd.Series([7] * 10), binary=False).tolist() == ["Q3"] * 10
  assert factor_groups(pd.Series([0, 1, np.nan]), binary=True).iloc[:2].tolist() == [
    "false",
    "true",
  ]


def test_outcomes_use_market_sessions_and_independent_horizon_completeness() -> None:
  dates = pd.bdate_range("2025-01-01", periods=5)
  frame = pd.DataFrame(
    {
      "stock_code": ["000001.SZ"] * 4,
      "event_date": dates[[0, 1, 3, 4]],
      "close": [10.0, 11.0, 12.0, 13.0],
      "open": [9.0, 10.5, 11.5, 12.5],
      "outcome_valid": [True] * 4,
    }
  )
  result = add_factor_outcomes(frame, dates, (1, 2, 4, 20))
  assert result.iloc[0].close_return_h1 == pytest.approx(0.1)
  assert result.iloc[0].next_open_return_h1 == pytest.approx(11 / 10.5 - 1)
  assert np.isnan(result.iloc[0].close_return_h2)
  assert result.iloc[0].close_return_h4 == pytest.approx(0.3)
  assert np.isnan(result.iloc[0].close_return_h20)
  assert np.isnan(result.iloc[1].next_open_return_h2)


def _config(path: Path, *, conditions: bool = True) -> Path:
  payload = {
    "study": "factor-study",
    "version": "v1",
    "date_range": ["2025-01-01", "2025-06-30"],
    "factor_ids": ["volume_ratio", "change_pct"],
    "conditions": [
      {"factor_id": "volume_ratio", "operator": "gte", "value": 1},
      {"factor_id": "change_pct", "operator": "gt", "value": 0},
    ]
    if conditions
    else [],
    "outcomes": {"horizons": [1, 3, 20]},
    "statistics": {"bootstrap_samples": 100, "minimum_cell_samples": 2},
    "runtime": {"batch_size": 2, "minimum_available_memory_gib": 1},
  }
  path.write_text(yaml.safe_dump(payload), encoding="utf-8")
  return path


@pytest.mark.asyncio
async def test_factor_runner_produces_reproducible_reports_and_keeps_short_outcomes(
  tmp_path: Path,
) -> None:
  config_path = _config(tmp_path / "factor.yaml")
  run_dir = await run_study(
    config_path,
    source=FakeResearchSource(_market_bars()),
    output_root=tmp_path / "runs",
  )
  manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
  metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
  assert manifest["status"] == "success"
  assert manifest["completed_at"]
  assert manifest["elapsed_seconds"] > 0
  assert manifest["statistics_input"]["schema_version"] == 2
  assert (
    manifest["statistics_input"]["statistics_engine"] == manifest["statistics_engine"]
  )
  assert "finished_at" not in manifest
  assert "duration_seconds" not in manifest
  assert len(metrics["reports"]) == 3
  assert metrics["factor_version"] == "daily-v1"
  sample = pd.read_parquet(run_dir / "analysis-sample.parquet")
  assert sample.close_return_h1.notna().sum() > sample.close_return_h20.notna().sum()
  single = next(
    item for item in metrics["reports"] if item["report_id"] == "single-volume_ratio"
  )
  assert single["definitions"][0]["id"] == "volume_ratio"
  assert single["coverage"]["valid_count"] == len(sample)
  joint = next(item for item in metrics["reports"] if item["kind"] == "joint")
  all_rows = [
    row
    for row in joint["rows"]
    if row["period"] == "all" and row["horizon"] == 1 and row["return_basis"] == "close"
  ]
  assert len({row["date_count"] for row in all_rows}) == 1
  assert any(row["q_value"] is not None for row in joint["rows"])
  assert metrics["inference_resolution"]["bootstrap_samples"] == 100
  assert len(list((run_dir / "statistics-checkpoints").glob("report-*.json"))) == 3
  before = (run_dir / "metrics.json").read_bytes()
  assert render_existing(run_dir).exists()
  assert (run_dir / "metrics.json").read_bytes() == before
  rendered_manifest = json.loads(
    (run_dir / "manifest.json").read_text(encoding="utf-8")
  )
  assert ".factor-study-run-lease.json" not in {
    item["path"] for item in rendered_manifest["artifacts"]
  }
  active_lease = factor_runner_module._acquire_factor_run_lease(
    run_dir,
    attempt_id="active-render-test",
    previous_status="success",
    previous_active_attempt=None,
  )
  try:
    with pytest.raises(ValueError, match="活动进程"):
      render_existing(run_dir)
  finally:
    active_lease.release()
  assert not list(run_dir.glob(".staging-*"))


def test_statistics_engine_identity_binds_source_and_runtime_versions() -> None:
  engine = factor_runner_module._factor_statistics_engine_identity()
  source_files = {item["module"]: item for item in engine["source_files"]}
  assert set(source_files) >= {
    "quantx_research.factor_runner",
    "quantx_research.factor_study",
    "quantx_research.factor_config",
    "quantx_research.core.statistics",
    "quantx_research.core.config",
    "quantx_domain.factors",
  }
  for source in source_files.values():
    assert source["sha256"] == factor_runner_module.file_sha256(
      runner_module.REPO_ROOT / source["path"]
    )
  assert engine["source_sha256"] == factor_runner_module.fingerprint(
    engine["source_files"]
  )
  assert set(engine["dependencies"]) == {"python", "numpy", "pandas", "pyarrow"}
  assert all(engine["dependencies"].values())
  payload = {key: value for key, value in engine.items() if key != "engine_sha256"}
  assert engine["engine_sha256"] == factor_runner_module.fingerprint(payload)


def _checkpoint_report(factor_id: str, p_value: float) -> dict[str, object]:
  return {
    "report_id": f"single-{factor_id}",
    "kind": "single",
    "factor_ids": [factor_id],
    "conditions": [],
    "match_key": factor_id,
    "coverage": {},
    "distribution": [],
    "rows": [
      {
        "period": "all",
        "p_value": p_value,
        "q_value": None,
        "mean_p_value": p_value,
        "mean_q_value": None,
      }
    ],
    "warnings": [],
  }


def test_factor_report_checkpoints_resume_before_one_global_fdr(
  tmp_path: Path, monkeypatch
) -> None:
  config = FactorStudyConfig(
    factor_ids=["change_pct", "volume_ratio"],
    date_range=("2025-01-01", "2025-06-30"),
    outcomes={"horizons": [1]},
    statistics={"bootstrap_samples": 100},
    runtime={"minimum_available_memory_gib": 1},
  )
  checkpoint_directory = tmp_path / "checkpoints"
  identity = {
    "config_hash": "a" * 64,
    "data_fingerprint": "b" * 64,
    "analysis_sample_sha256": "c" * 64,
    "statistics_engine": factor_runner_module._factor_statistics_engine_identity(),
  }
  calls: list[str] = []

  def interrupted(*args, factor_ids, **kwargs):
    calls.append(factor_ids[0])
    if len(calls) == 2:
      raise RuntimeError("interrupted after one completed report")
    return _checkpoint_report(factor_ids[0], 0.01)

  monkeypatch.setattr(factor_study_module, "_analyze_report", interrupted)
  with RuntimeMemoryMonitor(reserve_gib=1) as monitor:
    with pytest.raises(RuntimeError, match="interrupted"):
      analyze_factor_partitions(
        {},
        config,
        staging_directory=tmp_path,
        monitor=monitor,
        data_start="2025-01-01",
        data_end="2025-06-30",
        checkpoint_directory=checkpoint_directory,
        checkpoint_identity=identity,
      )
  assert (checkpoint_directory / "report-001.json").is_file()
  assert not (checkpoint_directory / "report-002.json").exists()
  assert not list(checkpoint_directory.glob("*.partial"))

  resumed_calls: list[str] = []

  def resumed(*args, factor_ids, **kwargs):
    resumed_calls.append(factor_ids[0])
    return _checkpoint_report(factor_ids[0], 0.02)

  progress: list[tuple[int, bool]] = []
  monkeypatch.setattr(factor_study_module, "_analyze_report", resumed)
  with RuntimeMemoryMonitor(reserve_gib=1) as monitor:
    metrics = analyze_factor_partitions(
      {},
      config,
      staging_directory=tmp_path,
      monitor=monitor,
      data_start="2025-01-01",
      data_end="2025-06-30",
      checkpoint_directory=checkpoint_directory,
      checkpoint_identity=identity,
      on_report_checkpoint=lambda completed, total, report_id, reused: progress.append(
        (completed, reused)
      ),
    )
  assert len(resumed_calls) == 1
  assert progress == [(1, True), (2, False)]
  assert [
    report["rows"][0]["q_value"] for report in metrics["reports"]
  ] == pytest.approx([0.02, 0.02])
  saved = json.loads(
    (checkpoint_directory / "report-001.json").read_text(encoding="utf-8")
  )
  assert saved["report"]["rows"][0]["q_value"] is None

  with RuntimeMemoryMonitor(reserve_gib=1) as monitor:
    with pytest.raises(ValueError, match="身份不匹配"):
      analyze_factor_partitions(
        {},
        config,
        staging_directory=tmp_path,
        monitor=monitor,
        data_start="2025-01-01",
        data_end="2025-06-30",
        checkpoint_directory=checkpoint_directory,
        checkpoint_identity={**identity, "analysis_sample_sha256": "d" * 64},
      )

  source_changed = copy.deepcopy(identity)
  source_changed["statistics_engine"]["source_files"][0]["sha256"] = "d" * 64
  source_changed["statistics_engine"]["source_sha256"] = (
    factor_runner_module.fingerprint(
      source_changed["statistics_engine"]["source_files"]
    )
  )
  dependency_changed = copy.deepcopy(identity)
  dependency_changed["statistics_engine"]["dependencies"]["numpy"] = "changed"
  for changed_identity in (source_changed, dependency_changed):
    engine = changed_identity["statistics_engine"]
    engine["engine_sha256"] = factor_runner_module.fingerprint(
      {key: value for key, value in engine.items() if key != "engine_sha256"}
    )
    with RuntimeMemoryMonitor(reserve_gib=1) as monitor:
      with pytest.raises(ValueError, match="身份不匹配"):
        analyze_factor_partitions(
          {},
          config,
          staging_directory=tmp_path,
          monitor=monitor,
          data_start="2025-01-01",
          data_end="2025-06-30",
          checkpoint_directory=checkpoint_directory,
          checkpoint_identity=changed_identity,
        )


@pytest.mark.asyncio
async def test_legacy_statistics_identity_upgrades_only_without_checkpoints(
  tmp_path: Path,
) -> None:
  config_path = _config(tmp_path / "legacy-factor.yaml")
  run_dir = await run_study(
    config_path,
    source=FakeResearchSource(_market_bars()),
    output_root=tmp_path / "runs",
  )
  manifest_path = run_dir / "manifest.json"
  manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
  manifest.update(status="failed", errors=["simulated legacy retry"])
  manifest["statistics_input"].pop("statistics_engine")
  manifest["statistics_input"]["schema_version"] = 1
  manifest.pop("statistics_engine")
  manifest_path.write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
  )

  with pytest.raises(ValueError, match="缺少统计引擎身份且已有"):
    await run_study(config_path, resume_run_dir=run_dir)

  shutil.rmtree(run_dir / "statistics-checkpoints")
  manifest["statistics_progress"] = {
    "completed_reports": 7,
    "total_reports": 32,
    "last_report_id": "stale-legacy-report",
    "last_report_reused": True,
  }
  legacy_statistics_input = manifest.pop("statistics_input")
  manifest_path.write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
  )
  with pytest.raises(ValueError, match="不能证明统计尚未开始"):
    await run_study(config_path, resume_run_dir=run_dir)

  manifest["statistics_input"] = legacy_statistics_input
  original_data_end = manifest["statistics_input"]["data_end"]
  manifest["statistics_input"]["data_end"] = "2025-01-02"
  manifest_path.write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
  )
  with pytest.raises(ValueError, match="旧 statistics_input 身份不一致"):
    await run_study(config_path, resume_run_dir=run_dir)

  manifest["statistics_input"]["data_end"] = original_data_end
  manifest_path.write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
  )
  resumed = await run_study(config_path, resume_run_dir=run_dir)
  assert resumed == run_dir
  upgraded = json.loads(manifest_path.read_text(encoding="utf-8"))
  assert upgraded["status"] == "success"
  assert upgraded["statistics_input"]["schema_version"] == 2
  assert (
    upgraded["statistics_input"]["statistics_engine"] == upgraded["statistics_engine"]
  )
  assert upgraded["statistics_progress"]["total_reports"] == 3
  assert upgraded["statistics_identity_upgrades"][-1]["reason"] == (
    "legacy_identity_without_statistics_checkpoints"
  )
  assert (
    upgraded["statistics_identity_upgrades"][-1]["previous_statistics_progress"][
      "completed_reports"
    ]
    == 7
  )
  quality = json.loads((run_dir / "data-quality.json").read_text(encoding="utf-8"))
  assert any("旧统计输入身份" in warning for warning in quality["warnings"])
  assert list((run_dir / "statistics-checkpoints").glob("report-*.json"))


@pytest.mark.asyncio
async def test_resume_rejects_changed_current_engine_with_or_without_checkpoints(
  tmp_path: Path, monkeypatch
) -> None:
  config_path = _config(tmp_path / "changed-engine-factor.yaml")
  run_dir = await run_study(
    config_path,
    source=FakeResearchSource(_market_bars()),
    output_root=tmp_path / "runs",
  )
  manifest_path = run_dir / "manifest.json"
  manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
  manifest.update(status="failed", errors=["simulated engine change"])
  manifest_path.write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
  )

  changed_engine = copy.deepcopy(manifest["statistics_engine"])
  changed_engine["dependencies"]["numpy"] = "changed"
  changed_engine["engine_sha256"] = factor_runner_module.fingerprint(
    {key: value for key, value in changed_engine.items() if key != "engine_sha256"}
  )
  monkeypatch.setattr(
    factor_runner_module,
    "_factor_statistics_engine_identity",
    lambda: copy.deepcopy(changed_engine),
  )

  with pytest.raises(ValueError, match="statistics_input 身份不一致"):
    await run_study(config_path, resume_run_dir=run_dir)

  shutil.rmtree(run_dir / "statistics-checkpoints")
  with pytest.raises(ValueError, match="statistics_input 身份不一致"):
    await run_study(config_path, resume_run_dir=run_dir)


@pytest.mark.asyncio
async def test_failed_factor_run_resumes_only_from_bound_frozen_checkpoints(
  tmp_path: Path, monkeypatch
) -> None:
  config_path = _config(tmp_path / "resume-factor.yaml")
  run_dir = await run_study(
    config_path,
    source=FakeResearchSource(_market_bars()),
    output_root=tmp_path / "runs",
  )
  manifest_path = run_dir / "manifest.json"
  manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
  manifest.update(status="failed_resource", errors=["simulated finalization failure"])
  manifest_path.write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
  )
  (run_dir / "metrics.json").unlink()
  (run_dir / "report.html").unlink()
  shutil.rmtree(run_dir / "tables")

  repartition_sample = factor_runner_module._partition_existing_factor_sample

  def must_not_repartition(*args, **kwargs):
    raise AssertionError("all report checkpoints should avoid sample repartitioning")

  monkeypatch.setattr(
    factor_runner_module, "_partition_existing_factor_sample", must_not_repartition
  )
  resumed = await run_study(config_path, resume_run_dir=run_dir)
  assert resumed == run_dir
  completed = json.loads(manifest_path.read_text(encoding="utf-8"))
  assert completed["status"] == "success"
  assert completed["resume_attempts"][-1]["previous_status"] == "failed_resource"
  assert completed["resume_attempts"][-1]["previous_errors"] == [
    "simulated finalization failure"
  ]
  assert completed["resume_attempts"][-1]["previous_runtime_memory"]["reserve_gib"] == 1
  assert completed["resume_attempts"][-1]["status"] == "success"
  assert completed["statistics_progress"] == {
    "completed_reports": 3,
    "last_report_id": next(
      report["report_id"]
      for report in json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))[
        "reports"
      ]
      if report["kind"] == "joint"
    ),
    "last_report_reused": True,
    "total_reports": 3,
  }
  assert (run_dir / "report.html").is_file()

  # A partial checkpoint family takes the other supported path: rebuild only
  # transient month files from the frozen Parquet and calculate the missing
  # report.  Market data and factor/outcome construction stay out of recovery.
  missing_checkpoint = run_dir / "statistics-checkpoints" / "report-002.json"
  missing_checkpoint.unlink()
  completed.update(status="failed_resource", errors=["simulated partial family"])
  manifest_path.write_text(
    json.dumps(completed, ensure_ascii=False, indent=2), encoding="utf-8"
  )
  analyzed: list[str] = []
  analyze_report = factor_study_module._analyze_report

  def record_analysis(*args, factor_ids, **kwargs):
    analyzed.append(factor_ids[0])
    return analyze_report(*args, factor_ids=factor_ids, **kwargs)

  monkeypatch.setattr(
    factor_runner_module, "_partition_existing_factor_sample", repartition_sample
  )
  monkeypatch.setattr(factor_study_module, "_analyze_report", record_analysis)
  await run_study(config_path, resume_run_dir=run_dir)
  assert analyzed == ["volume_ratio"]
  assert missing_checkpoint.is_file()

  latest = json.loads(manifest_path.read_text(encoding="utf-8"))
  quality_path = run_dir / "data-quality.json"
  original_quality = quality_path.read_bytes()
  original_data_end = json.loads(original_quality)["data_end"]
  changed_data_end = original_data_end[:-1] + (
    "8" if original_data_end[-1] != "8" else "7"
  )
  tampered_quality = original_quality.replace(
    f'"data_end": "{original_data_end}"'.encode(),
    f'"data_end": "{changed_data_end}"'.encode(),
  )
  assert tampered_quality != original_quality
  quality_path.write_bytes(tampered_quality)
  latest.update(status="failed_resource", errors=["simulated artifact tamper"])
  manifest_path.write_text(
    json.dumps(latest, ensure_ascii=False, indent=2), encoding="utf-8"
  )
  with pytest.raises(ValueError, match="artifact SHA256"):
    await run_study(config_path, resume_run_dir=run_dir)
  quality_path.write_bytes(original_quality)

  # A hard-killed process leaves both status=running and its unlocked lease.
  # The stale PID/create-time evidence and an obtainable OS lock allow the
  # next attempt to take over the same run.
  stale_attempt = factor_runner_module._current_attempt_identity(
    run_dir, "stale-attempt"
  )
  stale_attempt["pid"] = 2_147_483_647
  stale_attempt["process_create_time"] = 1.0
  latest.update(
    status="running",
    errors=[],
    active_attempt=stale_attempt,
  )
  manifest_path.write_text(
    json.dumps(latest, ensure_ascii=False, indent=2), encoding="utf-8"
  )
  lease_path = run_dir / ".factor-study-run-lease.json"
  lease_path.write_text(json.dumps(stale_attempt, ensure_ascii=False), encoding="utf-8")
  await run_study(config_path, resume_run_dir=run_dir)
  orphan_recovered = json.loads(manifest_path.read_text(encoding="utf-8"))
  assert orphan_recovered["status"] == "success"
  assert orphan_recovered["resume_attempts"][-1]["previous_status"] == "running"
  assert lease_path.is_file()

  # The OS lock rejects a concurrent takeover even in the narrow interval
  # before the active owner replaces the manifest's stale attempt marker.
  active_lease = factor_runner_module._acquire_factor_run_lease(
    run_dir,
    attempt_id="active-attempt",
    previous_status="failed",
    previous_active_attempt=None,
  )
  orphan_recovered.update(status="running", active_attempt=stale_attempt)
  manifest_path.write_text(
    json.dumps(orphan_recovered, ensure_ascii=False, indent=2), encoding="utf-8"
  )
  try:
    with pytest.raises(ValueError, match="活动进程"):
      await run_study(config_path, resume_run_dir=run_dir)
  finally:
    active_lease.release()

  live_marker = factor_runner_module._current_attempt_identity(run_dir, "live-marker")
  orphan_recovered.update(status="running", active_attempt=live_marker)
  manifest_path.write_text(
    json.dumps(orphan_recovered, ensure_ascii=False, indent=2), encoding="utf-8"
  )
  with pytest.raises(ValueError, match="活动进程"):
    await run_study(config_path, resume_run_dir=run_dir)

  changed_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
  changed_config["date_range"][0] = "2025-01-02"
  changed_path = tmp_path / "changed-factor.yaml"
  changed_path.write_text(yaml.safe_dump(changed_config), encoding="utf-8")
  orphan_recovered.update(status="failed_resource", active_attempt=None)
  manifest_path.write_text(
    json.dumps(orphan_recovered, ensure_ascii=False, indent=2), encoding="utf-8"
  )
  with pytest.raises(ValueError, match="恢复配置"):
    await run_study(changed_path, resume_run_dir=run_dir)


@pytest.mark.asyncio
async def test_keyboard_interrupt_marks_factor_resume_failed_and_releases_lease(
  tmp_path: Path, monkeypatch
) -> None:
  config_path = _config(tmp_path / "interrupted-factor.yaml")
  run_dir = await run_study(
    config_path,
    source=FakeResearchSource(_market_bars()),
    output_root=tmp_path / "runs",
  )
  manifest_path = run_dir / "manifest.json"
  manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
  manifest.update(status="failed_resource", errors=["simulated retry"])
  manifest_path.write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
  )

  def interrupt(*args, **kwargs):
    raise KeyboardInterrupt("operator stopped recovery")

  monkeypatch.setattr(factor_runner_module, "analyze_factor_partitions", interrupt)
  with pytest.raises(KeyboardInterrupt, match="operator stopped"):
    await run_study(config_path, resume_run_dir=run_dir)

  interrupted = json.loads(manifest_path.read_text(encoding="utf-8"))
  assert interrupted["status"] == "failed"
  assert interrupted["resume_attempts"][-1]["status"] == "failed"
  assert "KeyboardInterrupt" in interrupted["errors"][0]
  probe = factor_runner_module._acquire_factor_run_lease(
    run_dir,
    attempt_id="post-interrupt-probe",
    previous_status="failed",
    previous_active_attempt=None,
  )
  probe.release()


@pytest.mark.asyncio
async def test_shared_factor_values_and_quality_gate(tmp_path: Path) -> None:
  source = FakeResearchSource(_market_bars())
  config = FactorStudyConfig(
    factor_ids=["volume_ratio", "change_pct"],
    date_range=("2025-01-01", "2025-06-30"),
    runtime={"minimum_available_memory_gib": 1},
  )
  with RuntimeMemoryMonitor(reserve_gib=1) as monitor:
    partitions, quality = await build_factor_partitions(
      source, config, tmp_path, monitor
    )
  sample = pd.concat(
    [pd.read_parquet(path) for paths in partitions.values() for path in paths]
  )
  raw = source.bars[source.bars.stock_code == "000001.SZ"].sort_values("time")
  expected = calculate_factor_frame(raw).set_index(raw.time)
  actual = sample[sample.stock_code == "000001.SZ"].set_index("event_date")
  np.testing.assert_allclose(
    actual.volume_ratio, expected.loc[actual.index, "volume_ratio"]
  )
  assert quality["dividend_factor_coverage"]["is_complete"]
  source.factor_coverage_complete = False
  validated = await validate_study(_config(tmp_path / "invalid.yaml"), source=source)
  assert not validated["valid"]
  assert validated["data_quality"]["dividend_factor_coverage"]["is_complete"] is False


@pytest.mark.asyncio
async def test_bad_bar_invalidates_factor_window_instead_of_bridging_gap(
  tmp_path: Path,
) -> None:
  bars = _market_bars()
  bad = bars.stock_code.eq("000001.SZ") & bars.time.eq(pd.Timestamp("2025-01-02"))
  bars.loc[bad, "suspend_flag"] = 1
  config = FactorStudyConfig(
    factor_ids=["ma20"],
    date_range=("2025-01-01", "2025-01-31"),
    runtime={"minimum_available_memory_gib": 1},
  )
  with RuntimeMemoryMonitor(reserve_gib=1) as monitor:
    partitions, _ = await build_factor_partitions(
      FakeResearchSource(bars), config, tmp_path, monitor
    )
  sample = pd.concat(
    [pd.read_parquet(path) for paths in partitions.values() for path in paths]
  )
  stock = sample[sample.stock_code.eq("000001.SZ")].set_index("event_date")
  assert pd.Timestamp("2025-01-02") not in stock.index
  assert pd.isna(stock.loc["2025-01-03", "ma20"])
  assert pd.notna(stock.loc["2025-01-31", "ma20"])


@pytest.mark.asyncio
async def test_physically_missing_bar_has_same_factor_results_as_explicit_invalid_bar(
  tmp_path: Path,
) -> None:
  bars = _market_bars()
  missing = bars.stock_code.eq("000001.SZ") & bars.time.eq(pd.Timestamp("2025-01-02"))
  absent = bars.loc[~missing].copy()
  invalid = bars.copy()
  invalid.loc[missing, "suspend_flag"] = 1
  config = FactorStudyConfig(
    factor_ids=["ma5", "rsi6", "volume_ratio"],
    date_range=("2025-01-01", "2025-01-31"),
    universe={"stock_codes": ["000001.SZ"]},
    runtime={"minimum_available_memory_gib": 1},
  )
  results = []
  for name, source_bars in (("absent", absent), ("invalid", invalid)):
    directory = tmp_path / name
    directory.mkdir()
    with RuntimeMemoryMonitor(reserve_gib=1) as monitor:
      partitions, quality = await build_factor_partitions(
        FakeResearchSource(source_bars), config, directory, monitor
      )
    results.append(
      pd.concat(
        [pd.read_parquet(path) for paths in partitions.values() for path in paths]
      ).reset_index(drop=True)
    )
    assert "2025-01-02" in quality["analysis_trading_dates"]
  pd.testing.assert_frame_equal(results[0], results[1])
  assert pd.isna(results[0].set_index("event_date").loc["2025-01-03", "ma5"])


@pytest.mark.asyncio
async def test_runner_releases_source_before_cpu_outcomes(
  tmp_path: Path, monkeypatch
) -> None:
  context_exited = False
  source = FakeResearchSource(_market_bars())

  @asynccontextmanager
  async def scope(*args, **kwargs):
    nonlocal context_exited
    yield source
    context_exited = True

  complete = factor_runner_module.finish_factor_partitions

  def checked_complete(*args, **kwargs):
    assert context_exited, "DB source scope must end before long CPU-only work"
    return complete(*args, **kwargs)

  monkeypatch.setattr(runner_module, "_research_source", scope)
  monkeypatch.setattr(
    factor_runner_module, "finish_factor_partitions", checked_complete
  )
  await run_study(
    _config(tmp_path / "scope.yaml", conditions=False),
    source=source,
    output_root=tmp_path / "runs",
  )


def test_latest_year_is_trailing_twelve_months_with_exact_median_and_stock_count(
  tmp_path: Path,
) -> None:
  dates = pd.to_datetime(["2024-01-01", "2024-07-01", "2024-12-31", "2025-06-30"])
  cell = _Cell(tmp_path / "values.bin")
  for day, value, code in zip(dates, [0.9, 0.1, 0.2, -0.3], ["old", "a", "b", "c"]):
    cell.append(day, np.array([value]), np.array([code]))
  cell.flush()
  config = FactorStudyConfig(factor_ids=["volume_ratio"])
  row = _cell_result(
    cell,
    cell,
    np.fromfile(cell.path, dtype="float64"),
    group="baseline",
    horizon=1,
    basis="close",
    period="latest_year",
    latest_date=dates[-1],
    config=config,
    bootstrap=DateBlockBootstrap(
      pd.Series(dates), samples=100, seed=42, confidence_level=0.95
    ),
  )
  assert row["sample_count"] == 3
  assert row["stock_count"] == 3
  assert row["median_return"] == pytest.approx(0.1)
  assert row["up_rate"] == pytest.approx(2 / 3)
  assert row["inference_status"] == "descriptive_only"


def test_daily_lift_is_not_pooled_stock_day_difference(tmp_path: Path) -> None:
  dates = pd.to_datetime(["2025-01-02", "2025-01-03"])
  cell, baseline = _Cell(tmp_path / "cohort.bin"), _Cell(tmp_path / "baseline.bin")
  cell.append(dates[0], np.array([0.1]), np.array(["a"]))
  cell.append(dates[1], np.array([-0.1]), np.array(["a"]))
  baseline.append(dates[0], np.array([0.1] * 9 + [-0.1]), np.arange(10).astype(str))
  baseline.append(dates[1], np.array([-0.1, -0.1]), np.array(["a", "b"]))
  cell.flush()
  row = _cell_result(
    cell,
    baseline,
    np.fromfile(cell.path, dtype="float64"),
    group="joint",
    horizon=1,
    basis="close",
    period="all",
    latest_date=dates[-1],
    config=FactorStudyConfig(factor_ids=["volume_ratio"]),
    bootstrap=DateBlockBootstrap(
      pd.Series(dates), samples=100, seed=42, confidence_level=0.95
    ),
  )
  assert row["up_rate"] == 0.5
  assert row["baseline_up_rate"] == 0.45
  assert row["up_rate_lift"] == pytest.approx(0.05)
  assert row["ci_low"] is None
  assert row["p_value"] is None
  assert row["inference_status"] == "insufficient_sample"


@pytest.mark.asyncio
async def test_persisted_latest_date_uses_shanghai_calendar_and_rejects_no_data() -> (
  None
):
  repository = SimpleNamespace(
    find_latest_by_stock_code_and_period=lambda *args: [
      SimpleNamespace(time=pd.Timestamp("2025-06-29T16:00:00Z")),
    ]
  )
  source = InfrastructureResearchDataSource(kline_repository=repository)
  assert (
    await source.latest_daily_date("000300.SH") == pd.Timestamp("2025-06-30").date()
  )
  repository.find_latest_by_stock_code_and_period = lambda *args: []
  with pytest.raises(ValueError, match="已持久化"):
    await source.latest_daily_date("000300.SH")


@pytest.mark.asyncio
async def test_latest_window_is_frozen_from_persisted_source_before_config_hash(
  tmp_path: Path,
) -> None:
  source = FakeResearchSource(_market_bars())

  async def latest_daily_date(code: str):
    assert code == "000300.SH"
    return pd.Timestamp("2025-06-27").date()

  source.latest_daily_date = latest_daily_date
  path = tmp_path / "latest.yaml"
  path.write_text(
    yaml.safe_dump(
      {
        "study": "factor-study",
        "factor_ids": ["change_pct"],
        "outcomes": {"horizons": [1]},
        "statistics": {"bootstrap_samples": 100},
        "runtime": {"minimum_available_memory_gib": 1},
      }
    ),
    encoding="utf-8",
  )
  run_dir = await run_study(path, source=source, output_root=tmp_path / "runs")
  resolved = yaml.safe_load(
    (run_dir / "resolved-config.yaml").read_text(encoding="utf-8")
  )
  assert resolved["date_range"] == ["2020-06-27", "2025-06-27"]
