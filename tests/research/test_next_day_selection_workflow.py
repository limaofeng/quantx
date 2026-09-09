from __future__ import annotations

import asyncio
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import quantx_domain.stock_selection_training as domain_training
import quantx_research.next_day_selection_dataset as dataset_module
import quantx_research.next_day_selection_training as training
from quantx_domain.indicators import INDICATOR_VERSION
from quantx_domain.selection_factors import (
  FACTOR_SET_HASH,
  FACTOR_SET_VERSION,
  LABEL_VERSION,
  selection_feature_columns,
)
from quantx_research.artifacts import file_sha256, fingerprint, write_json
from quantx_research.next_day_selection_job import _cancel_reader, load_request
from quantx_research.next_day_selection_training import (
  FittedFamily,
  RunCancelled,
  _safe_error,
)


def _dataset(tmp_path: Path) -> Path:
  directory = tmp_path / "dataset"
  directory.mkdir()
  dates = pd.date_range("2018-01-01", periods=60, freq="MS")
  rows: list[dict[str, object]] = []
  for date_index, event_date in enumerate(dates):
    for stock_index, code in enumerate(("000001.SZ", "600000.SH")):
      label = float((date_index + stock_index) % 2)
      row: dict[str, object] = {
        "event_date": event_date,
        "open_date": event_date - pd.Timedelta(days=400),
        "valid_history": date_index + 400,
        "target_date": event_date + pd.offsets.MonthBegin(1),
        "stock_code": code,
        "label": label,
        "next_open_to_close_return": 0.01 if label else -0.01,
        "month": event_date.to_period("M"),
        "factor_completeness": 1.0,
      }
      row.update({column: float((date_index + stock_index) % 5) for column in selection_feature_columns()})
      rows.append(row)
  panel = pd.DataFrame(rows)
  panel_path = directory / "training-panel.parquet"
  panel.to_parquet(panel_path, index=False)
  panel_sha = file_sha256(panel_path)
  quality = {
    "sample_count": len(panel),
    "stock_count": 2,
    "trading_day_count": len(dates),
    "date_start": "2018-01-01",
    "date_end": "2022-12-01",
    "coverage": {"historical_universe": {"complete": True}},
    "leakage_checks": {
      "duplicate_samples": True,
      "target_after_event": True,
      "label_not_missing": True,
      "features_not_after_event": True,
      "split_overlap": True,
    },
  }
  quality_path = directory / "data-quality.json"
  write_json(quality_path, quality)
  manifest = {
    "schema_version": 2,
    "dataset_version": "workflow-test",
    "status": "CERTIFIED",
    "source_kind": "VERIFIED_PANEL",
    "source_reference": "workflow-test",
    "date_start": "2018-01-01",
    "date_end": "2022-12-01",
    "panel_path": "training-panel.parquet",
    "quality_path": "data-quality.json",
    "universe_spec": {"kind": "ORDINARY_A_SHARE", "index_code": None, "benchmark_code": "000300.SH", "stock_codes": None, "minimum_listing_days": 252},
    "indicator_version": INDICATOR_VERSION,
    "factor_set_version": FACTOR_SET_VERSION,
    "factor_set_hash": FACTOR_SET_HASH,
    "label_version": LABEL_VERSION,
    "files": {
      "training-panel.parquet": {"sha256": panel_sha, "bytes": panel_path.stat().st_size},
      "data-quality.json": {"sha256": file_sha256(quality_path), "bytes": quality_path.stat().st_size},
    },
    "training_panel_sha256": panel_sha,
    "training_panel_bytes": panel_path.stat().st_size,
    "quality_sha256": file_sha256(quality_path),
    "quality_bytes": quality_path.stat().st_size,
    "data_fingerprint": training._data_fingerprint(panel),
    "quality": quality,
    "immutable": True,
    "created_at": "2026-09-02T00:00:00+00:00",
  }
  manifest["manifest_sha256"] = fingerprint(manifest)
  write_json(directory / "manifest.json", manifest)
  return directory


def _spec(tmp_path: Path) -> dict:
  return {
    "study": "next-day-selection",
    "version": "v1",
    "random_seed": 7,
    "data": {
      "date_range": ["2018-01-01", "2022-12-01"],
      "universe_kind": "ORDINARY_A_SHARE",
      "index_code": None,
      "market_data_archive": None,
      "verified_panel_path": None,
      "benchmark_code": "000300.SH",
      "stock_codes": None,
      "minimum_listing_days": 252,
      "historical_st_membership_path": None,
      "historical_industry_membership_path": None,
      "historical_delisting_status_path": None,
    },
    "walk_forward": {"frozen_test_months": 12, "minimum_training_months": 30, "calibration_months": 6, "validation_months": 1},
    "logistic": {"c_values": [0.1], "max_iter": 1000},
    "lightgbm": {"num_leaves": [15], "reg_lambda": [1.0], "learning_rate": 0.03, "n_estimators": 500, "min_child_samples": 100, "subsample": 0.8, "colsample_bytree": 0.8, "max_bin": 63, "gpu_use_dp": False, "gpu_platform_id": None, "gpu_device_id": None},
    "calibration": {"bins": 10, "isotonic_minimum_positives": 20000, "isotonic_minimum_relative_brier_improvement": 0.01},
    "evaluation": {"bootstrap_samples": 100},
    "candidate_gate": {"brier_skill_minimum": 0.0, "ece_maximum": 0.03, "minimum_probability": 0.6, "minimum_factor_completeness": 0.9, "minimum_valid_history": 252, "level_a_size": 20, "level_b_size": 30},
    "runtime": {"batch_size": 100, "minimum_available_memory_gib": 1, "memory_sample_interval_seconds": 0.25, "output_root": str(tmp_path / "runs")},
    "requested_backend": "CPU",
    "resolved_backend": "CPU",
    "spec_hash": "a" * 64,
    "coordinate_hash": "b" * 64,
    "environment_requirement_hash": "c" * 64,
  }


class _FakeModel:
  def __init__(self, family: str, columns: int) -> None:
    self.family = family
    self.coef_ = np.zeros((1, columns))
    self.intercept_ = np.zeros(1)
    self.booster_ = self

  def decision_function(self, matrix: np.ndarray) -> np.ndarray:
    return np.zeros(len(matrix))

  def predict_proba(self, matrix: np.ndarray) -> np.ndarray:
    return np.column_stack((np.full(len(matrix), 0.5), np.full(len(matrix), 0.5)))

  def save_model(self, path: str) -> None:
    Path(path).write_text("fake-lightgbm-text-model", encoding="utf-8")


def test_certified_development_final_workflow_and_cancel(monkeypatch, tmp_path: Path) -> None:
  allocate = training.tempfile.mkdtemp

  def scratch_directory(*args, **kwargs):
    directory = allocate(*args, **kwargs)
    if str(kwargs.get("prefix", "")).startswith("next-day-selection-"):
      (Path(directory) / "ephemeral.bin").write_bytes(b"temporary training input")
    return directory

  monkeypatch.setattr(training.tempfile, "mkdtemp", scratch_directory)
  dataset = _dataset(tmp_path)
  spec = _spec(tmp_path)
  monkeypatch.setattr(training, "_family_grid", lambda config, family: [{"C": 0.1}] if family == "LOGISTIC" else [{"num_leaves": 15, "reg_lambda": 1.0}])

  def fit_family(family, params, train, calibration, config, *, resolved_backend):
    return FittedFamily(family, _FakeModel(family, len(selection_feature_columns())), training._fit_preprocessor(train), {"version": "test", "kind": "platt", "slope": 1.0, "intercept": 0.0})

  def save_family(run_dir: Path, fitted: FittedFamily) -> dict[str, str]:
    name = "logistic.json" if fitted.family == "LOGISTIC" else "lightgbm.txt"
    if fitted.family == "LOGISTIC":
      write_json(run_dir / name, {"schema_version": 1, "family": "LOGISTIC", "coefficients": [0.0] * len(selection_feature_columns()), "intercept": 0.0})
    else:
      (run_dir / name).write_text("fake-lightgbm-text-model", encoding="utf-8")
    return {"family": fitted.family, "model_path": name}

  monkeypatch.setattr(training, "_fit_family", fit_family)
  monkeypatch.setattr(training, "_save_family", save_family)
  development = asyncio.run(training.execute_next_day_selection_run(run_kind="DEVELOPMENT", spec={**spec, "run_kind": "DEVELOPMENT"}, dataset_directory=dataset, output_root=tmp_path / "runs", run_id="development"))
  development_manifest = json.loads((development / "manifest.json").read_text(encoding="utf-8"))
  assert development_manifest["status"] == "SUCCEEDED"
  for entry in development_manifest["artifacts"]:
    artifact = development / entry["path"]
    assert artifact.is_file(), entry["path"]
    assert file_sha256(artifact) == entry["sha256"]
  assert not list((tmp_path / "runs").rglob("ephemeral.bin"))
  from quantx_trainer.publication import result_bundle

  assert result_bundle(development, run_id="development", run_kind="DEVELOPMENT").kind == "RESULT"
  assert development_manifest["run_kind"] == "DEVELOPMENT"
  assert development_manifest["spec_hash"] == "a" * 64
  assert development_manifest["coordinate_hash"] == "b" * 64
  assert development_manifest["environment_requirement_hash"] == "c" * 64
  assert "frozen_test" not in json.loads((development / "metrics.json").read_text(encoding="utf-8"))
  development_metrics = json.loads((development / "metrics.json").read_text(encoding="utf-8"))
  development_lock = json.loads((development / "development-lock.json").read_text(encoding="utf-8"))
  for payload in (development_metrics, development_lock):
    assert payload["spec_hash"] == "a" * 64
    assert payload["coordinate_hash"] == "b" * 64
    assert payload["environment_requirement_hash"] == "c" * 64
  assert (development / "development-lock.json").is_file()

  monkeypatch.setattr(training, "_load_parent_family", lambda parent_dir, family, runtime, preprocessing, calibrators: fit_family(family, {}, pd.DataFrame({"event_date": [pd.Timestamp("2020-01-01")], "label": [0]}), pd.DataFrame({"event_date": [pd.Timestamp("2020-01-01")], "label": [0]}), None, resolved_backend=None))
  final_spec = {**spec, "run_kind": "FINAL_EVALUATION", "parent_run_directory": str(development)}
  final = asyncio.run(training.execute_next_day_selection_run(run_kind="FINAL_EVALUATION", spec=final_spec, dataset_directory=dataset, output_root=tmp_path / "runs", run_id="final", parent_run_directory=development, frozen_test_access_count=1))
  final_metrics = json.loads((final / "metrics.json").read_text(encoding="utf-8"))
  assert result_bundle(final, run_id="final", run_kind="FINAL_EVALUATION").kind == "RESULT"
  assert "frozen_test" in final_metrics
  assert final_metrics["validation"] == json.loads(
    (development / "metrics.json").read_text(encoding="utf-8")
  )["validation"]
  assert final_metrics["parent_development"]["run_id"] == "development"
  final_manifest = json.loads((final / "manifest.json").read_text(encoding="utf-8"))
  assert final_manifest["spec_hash"] == development_manifest["spec_hash"]
  assert final_manifest["coordinate_hash"] == development_manifest["coordinate_hash"]
  assert final_manifest["environment_requirement_hash"] == development_manifest["environment_requirement_hash"]
  assert final_manifest["registerable"] is (
    final_manifest["conclusion"] != "BLOCKED"
    and final_manifest["gates"]["access_evidence_valid"]
  )

  with pytest.raises(RunCancelled):
    asyncio.run(training.execute_next_day_selection_run(run_kind="DEVELOPMENT", spec={**spec, "run_kind": "DEVELOPMENT"}, dataset_directory=dataset, output_root=tmp_path / "runs", run_id="cancelled", cancel_callback=lambda: True))
  cancelled = json.loads((tmp_path / "runs" / "cancelled" / "manifest.json").read_text(encoding="utf-8"))
  assert cancelled["status"] == "CANCELLED"
  assert cancelled["registerable"] is False


def test_config_hash_excludes_host_paths_and_runtime_output_root() -> None:
  first = {
    "data": {"verified_panel_path": "F:/one/panel.parquet", "date_range": ["2020-01-01", "2021-01-01"]},
    "runtime": {"output_root": "F:/one/runs", "batch_size": 100},
    "requested_backend": "CPU",
    "resolved_backend": "CPU",
  }
  second = {
    "data": {"verified_panel_path": "\\\\server\\share\\other panel.parquet", "date_range": ["2020-01-01", "2021-01-01"]},
    "runtime": {"output_root": "C:/other/runs", "batch_size": 100},
    "requested_backend": "CPU",
    "resolved_backend": "CPU",
  }
  assert training.stable_json_sha256(training._config_hash_payload(first)) == training.stable_json_sha256(training._config_hash_payload(second))


def test_execution_uses_immutable_locked_gpu_without_reparsing(monkeypatch, tmp_path: Path) -> None:
  dataset = _dataset(tmp_path)
  spec = {
    **_spec(tmp_path),
    "requested_backend": "AUTO",
    "resolved_backend": "LIGHTGBM_OPENCL_GPU",
  }
  seen: list[tuple[str, object]] = []
  monkeypatch.setattr(
    training,
    "_family_grid",
    lambda config, family: [{"C": 0.1}] if family == "LOGISTIC" else [{"num_leaves": 15, "reg_lambda": 1.0}],
  )

  def fit_family(family, params, train, calibration, config, *, resolved_backend):
    seen.append((family, resolved_backend))
    return FittedFamily(
      family,
      _FakeModel(family, len(selection_feature_columns())),
      training._fit_preprocessor(train),
      {"version": "test", "kind": "platt", "slope": 1.0, "intercept": 0.0},
    )

  monkeypatch.setattr(training, "_fit_family", fit_family)
  def save_family(run_dir: Path, fitted: FittedFamily) -> dict[str, str]:
    name = "logistic.json" if fitted.family == "LOGISTIC" else "lightgbm.txt"
    if fitted.family == "LOGISTIC":
      write_json(
        run_dir / name,
        {"family": "LOGISTIC", "coefficients": [0.0] * len(selection_feature_columns()), "intercept": 0.0},
      )
    else:
      (run_dir / name).write_text("fake", encoding="utf-8")
    return {"family": fitted.family, "model_path": name}
  monkeypatch.setattr(training, "_save_family", save_family)
  monkeypatch.setattr(domain_training, "resolve_backend", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("backend was reparsed")))

  result = asyncio.run(
    training.execute_next_day_selection_run(
      run_kind="DEVELOPMENT",
      spec={**spec, "run_kind": "DEVELOPMENT"},
      dataset_directory=dataset,
      output_root=tmp_path / "runs",
      run_id="locked-gpu",
    )
  )
  assert result.is_dir()
  assert ("LIGHTGBM", domain_training.ResolvedBackend.LIGHTGBM_OPENCL_GPU) in seen


def test_dataset_training_and_job_reject_junctions_and_malformed_cancel(
  monkeypatch, tmp_path: Path
) -> None:
  monkeypatch.setattr(Path, "is_junction", lambda self: self.name == "junction", raising=False)
  with pytest.raises(ValueError, match="联接点"):
    dataset_module.resolve_dataset_directory("v1", output_root=tmp_path / "junction")
  with pytest.raises(ValueError, match="联接点"):
    training._reject_links(tmp_path / "junction" / "run")

  cancel_file = tmp_path / "cancel.json"
  cancel_file.write_text("{}", encoding="utf-8")
  assert _cancel_reader(cancel_file)() is True
  cancel_file.write_text('{"cancel": false}', encoding="utf-8")
  assert _cancel_reader(cancel_file)() is True
  cancel_file.write_text('{"cancel": true}', encoding="utf-8")
  assert _cancel_reader(cancel_file)() is True


def test_execution_rejects_invalid_locked_backend_pair(tmp_path: Path) -> None:
  with pytest.raises(ValueError, match="组合非法"):
    asyncio.run(
      training.execute_next_day_selection_run(
        run_kind="DEVELOPMENT",
        spec={**_spec(tmp_path), "run_kind": "DEVELOPMENT", "resolved_backend": "LIGHTGBM_OPENCL_GPU"},
        dataset_directory=_dataset(tmp_path),
        output_root=tmp_path / "runs",
        run_id="invalid-backend",
      )
    )


def test_execution_projects_requested_date_and_stock_scope(tmp_path: Path, monkeypatch) -> None:
  dataset = _dataset(tmp_path)
  spec = {
    **_spec(tmp_path),
    "data": {
      **_spec(tmp_path)["data"],
      "date_range": ["2018-01-01", "2022-06-30"],
      "stock_codes": ["000001.SZ"],
    },
  }
  monkeypatch.setattr(training, "_family_grid", lambda config, family: [{"C": 0.1}] if family == "LOGISTIC" else [{"num_leaves": 15, "reg_lambda": 1.0}])
  monkeypatch.setattr(training, "_fit_family", lambda family, params, train, calibration, config, *, resolved_backend: FittedFamily(family, _FakeModel(family, len(selection_feature_columns())), training._fit_preprocessor(train), {"kind": "platt", "slope": 1.0, "intercept": 0.0}))
  def save_family(run_dir: Path, fitted: FittedFamily):
    name = "logistic.json" if fitted.family == "LOGISTIC" else "lightgbm.txt"
    if fitted.family == "LOGISTIC":
      write_json(run_dir / name, {"family": "LOGISTIC", "coefficients": [0.0] * len(selection_feature_columns()), "intercept": 0.0})
    else:
      (run_dir / name).write_text("fake", encoding="utf-8")
    return {"family": fitted.family, "model_path": name}
  monkeypatch.setattr(training, "_save_family", save_family)
  result = asyncio.run(training.execute_next_day_selection_run(run_kind="DEVELOPMENT", spec={**spec, "run_kind": "DEVELOPMENT"}, dataset_directory=dataset, output_root=tmp_path / "runs", run_id="projected"))
  manifest = json.loads((result / "manifest.json").read_text(encoding="utf-8"))
  assert manifest["sample_count"] == 54
  assert manifest["stock_count"] == 1
  assert manifest["data_fingerprint"] != json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))["data_fingerprint"]


def test_dataset_certification_writes_immutable_manifest_and_exact_projection(
  monkeypatch, tmp_path: Path
) -> None:
  ready_root = tmp_path / "ready"
  ready_root.mkdir()
  ready = _dataset(ready_root)
  panel = pd.read_parquet(ready / "training-panel.parquet")
  captured: dict[str, object] = {}

  async def source_panel(config, staging):
    (staging / "features").mkdir()
    (staging / "features" / "part.parquet").write_bytes(b"temporary")
    return panel, pd.DatetimeIndex(panel["event_date"].unique()), {"kind": "test"}

  monkeypatch.setattr(training, "_source_panel", source_panel)
  monkeypatch.setattr(
    dataset_module,
    "prepare_training_panel",
    lambda raw, calendar, config: (panel, {"complete": True, "coverage": 1.0}),
  )
  config_path = tmp_path / "config.yaml"
  import yaml

  config_payload = _spec(tmp_path)
  config_payload.pop("resolved_backend")
  config_payload.pop("spec_hash")
  config_payload.pop("coordinate_hash")
  config_payload.pop("environment_requirement_hash")
  yaml.safe_dump(config_payload, config_path.open("w", encoding="utf-8"))
  output = asyncio.run(
    dataset_module.certify_next_day_selection_dataset(
      config_path,
      dataset_version="certified-v1",
      output_root=tmp_path / "datasets",
    )
  )
  manifest = dataset_module.load_certified_dataset_manifest(output)
  assert {path.name for path in output.iterdir()} == {
    "data-quality.json",
    "manifest.json",
    "training-panel.parquet",
  }
  assert manifest["status"] == "CERTIFIED"
  assert manifest["source_reference"] == "certified-v1"
  assert manifest["training_panel_sha256"] == file_sha256(
    output / "training-panel.parquet"
  )
  from quantx_infrastructure.training_dataset_store import certification_values

  captured = certification_values(dataset_version="certified-v1", manifest_sha256=manifest["manifest_sha256"], root=tmp_path / "datasets")
  assert captured["quality_summary"] == manifest["quality"]
  assert captured["manifest_sha256"] == manifest["manifest_sha256"]
  first_bytes = (output / "manifest.json").read_bytes()
  retried = asyncio.run(
    dataset_module.certify_next_day_selection_dataset(
      config_path,
      dataset_version="certified-v1",
      output_root=tmp_path / "datasets",
    )
  )
  assert retried == output
  assert (output / "manifest.json").read_bytes() == first_bytes
  panel.loc[0, "label"] = 1.0 - panel.loc[0, "label"]
  with pytest.raises(ValueError, match="不同证据"):
    asyncio.run(
      dataset_module.certify_next_day_selection_dataset(
        config_path,
        dataset_version="certified-v1",
        output_root=tmp_path / "datasets",
        )
    )
  assert (output / "manifest.json").read_bytes() == first_bytes


def test_failed_published_dataset_cleanup_accepts_matching_manifest_with_extras(
  tmp_path: Path,
) -> None:
  directory = tmp_path / "datasets" / "failed-v1"
  directory.mkdir(parents=True)
  manifest_sha256 = "a" * 64
  (directory / "manifest.json").write_text(
    json.dumps({"manifest_sha256": manifest_sha256}),
    encoding="utf-8",
  )
  (directory / "temporary-part.parquet").write_bytes(b"partial")

  dataset_module._remove_published_dataset_if_exact(directory, manifest_sha256)

  assert not directory.exists()


def test_dataset_file_certification_never_imports_database_registration(
  monkeypatch, tmp_path: Path
) -> None:
  ready_root = tmp_path / "ready"
  ready_root.mkdir()
  ready = _dataset(ready_root)
  panel = pd.read_parquet(ready / "training-panel.parquet")

  async def source_panel(config, staging):
    return panel, pd.DatetimeIndex(panel["event_date"].unique()), {"kind": "test"}

  import builtins

  original_import = builtins.__import__

  def without_registration(name, *args, **kwargs):
    if name in {"quantx_infrastructure.database.relational_connection", "quantx_infrastructure.repositories.stock_selection_training_repository"}:
      raise AssertionError("Research attempted database registration")
    return original_import(name, *args, **kwargs)

  monkeypatch.setattr(training, "_source_panel", source_panel)
  monkeypatch.setattr(
    dataset_module,
    "prepare_training_panel",
    lambda raw, calendar, config: (panel, {"complete": True, "coverage": 1.0}),
  )
  monkeypatch.setattr(builtins, "__import__", without_registration)
  import yaml

  config_path = tmp_path / "config.yaml"
  config_payload = _spec(tmp_path)
  config_payload.pop("resolved_backend")
  config_payload.pop("spec_hash")
  config_payload.pop("coordinate_hash")
  config_payload.pop("environment_requirement_hash")
  yaml.safe_dump(config_payload, config_path.open("w", encoding="utf-8"))
  output = asyncio.run(
    dataset_module.certify_next_day_selection_dataset(
      config_path,
      dataset_version="new-v1",
      output_root=tmp_path / "datasets",
    )
  )
  assert dataset_module.load_certified_dataset_manifest(output)["status"] == "CERTIFIED"


def test_isolated_request_file_rejects_non_finite_json(tmp_path: Path) -> None:
  request_path = tmp_path / "request.json"
  request = {
    "run_id": "run-1",
    "run_kind": "DEVELOPMENT",
    "spec": {"random_seed": 7},
    "dataset_directory": "dataset",
    "output_root": "runs",
    "parent_run_directory": None,
    "progress_file": "progress.json",
    "cancel_file": "cancel.json",
    "frozen_test_access_count": 0,
  }
  request_path.write_text(json.dumps(request), encoding="utf-8")
  assert load_request(request_path)["run_id"] == "run-1"
  request_path.write_text(
    json.dumps(request).replace('"random_seed": 7', '"random_seed": NaN'),
    encoding="utf-8",
  )
  with pytest.raises(ValueError, match="非法 JSON 常量"):
    load_request(request_path)


def test_job_error_sanitizer_redacts_windows_unc_and_unix_paths() -> None:
  message = _safe_error(
    ValueError(
      "failed F:\\Workspace\\research output\\run.json; "
      "\\\\server\\shared folder\\run.json; /var/lib/quantx/run.json"
    )
  )
  assert message == "failed <path>; <path>; <path>"
  assert "Workspace" not in message
  assert "server" not in message
  assert "/var/" not in message
