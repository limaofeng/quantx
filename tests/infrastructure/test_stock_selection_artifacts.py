from __future__ import annotations

import json
from pathlib import Path

import pytest
from quantx_domain.indicators import INDICATOR_VERSION
from quantx_domain.selection_factors import (
  FACTOR_SET_HASH,
  FACTOR_SET_VERSION,
  LABEL_VERSION,
  factor_schema_manifest,
  selection_feature_columns,
)
from quantx_domain.selection_model import CALIBRATOR_VERSION
from quantx_domain.stock_selection_training import build_training_time_split
from quantx_infrastructure.services.stock_selection_artifacts import (
  SelectionArtifactError,
  file_sha256,
  load_selection_artifact,
)


def _write_json(path: Path, value: object) -> None:
  path.write_text(
    json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8"
  )


def _valid_artifact(directory: Path) -> Path:
  directory.mkdir()
  split = build_training_time_split(
    [f"{year:04d}-{month:02d}" for year, month in ((2021 + index // 12, index % 12 + 1) for index in range(49))]
  )
  columns = list(selection_feature_columns())
  mappings = {
    "imputation_values": {column: 0.0 for column in columns},
    "means": {column: 0.0 for column in columns},
    "scales": {column: 1.0 for column in columns},
    "ood_lower": {column: -3.0 for column in columns},
    "ood_upper": {column: 3.0 for column in columns},
  }
  preprocessor = {
    "schema_version": 1,
    "feature_columns": columns,
    **mappings,
  }
  calibrator = {
    "version": CALIBRATOR_VERSION,
    "kind": "platt",
    "slope": 1.0,
    "intercept": 0.0,
    "calibration_sample_count": 1000,
    "calibration_positive_count": 500,
  }
  gates = {
    "brier_skill_positive": True,
    "ece_within_3pct": True,
    "top20_lift_ci_lower_positive": True,
    "historical_universe_complete": True,
    "effect_gate_passed": True,
    "unbiased_frozen_evidence": True,
    "active_eligible": True,
    "access_evidence_valid": True,
    "conclusion": "ACTIVE_ELIGIBLE",
    "registerable": True,
  }
  bins = [
    {
      "index": index,
      "lower": index / 10,
      "upper": (index + 1) / 10,
      "sample_count": 100,
      "mean_probability": (index + 0.5) / 10,
      "realized_rate": (index + 0.5) / 10,
    }
    for index in range(10)
  ]
  ranking_row = {
    "date_count": 240,
    "precision": 0.62,
    "mean_return": 0.01,
    "baseline_up_rate": 0.5,
    "up_rate_lift": 0.12,
    "up_rate_lift_ci_low": 0.05,
    "up_rate_lift_ci_high": 0.18,
    "mean_return_lift": 0.004,
  }
  model_version = "next-day-up-v1-0123456789abcdef"
  metrics = {
    "schema_version": 2,
    "parent_development": {"run_id": "development-run", "metrics_sha256": "1" * 64, "lock_sha256": "2" * 64},
    "model_version": model_version,
    "selected_family": "LOGISTIC",
    "spec_hash": "a" * 64,
    "config_hash": "c" * 64,
    "coordinate_hash": "b" * 64,
    "calibrator_version": CALIBRATOR_VERSION,
    "training_start": "2021-01-01",
    "training_end": "2023-07-31",
    "calibration_start": "2023-08-01",
    "calibration_end": "2024-01-31",
    "test_start": "2024-02-01",
    "test_end": "2025-01-31",
    "validation": {
      "fold_count": 12,
      "logistic_brier": 0.2,
      "lightgbm_brier": 0.21,
      "untrusted_extra": "must not escape",
    },
    "frozen_test": {
      "start": "2024-02-01",
      "end": "2025-01-31",
      "probability": {
        "sample_count": 1000,
        "positive_count": 500,
        "prevalence": 0.5,
        "brier": 0.2,
        "baseline_brier": 0.25,
        "brier_skill": 0.2,
        "log_loss": 0.6,
        "ece": 0.02,
        "roc_auc": 0.65,
        "pr_auc": 0.64,
        "calibration_bins": bins,
      },
      "ranking": {"top_20": ranking_row, "top_50": ranking_row},
      "annual_stability": [
        {
          "year": 2026,
          "sample_count": 1000,
          "brier": 0.2,
          "brier_skill": 0.2,
          "ece": 0.02,
          "top20_up_rate_lift": 0.12,
        }
      ],
      "access_count": 1,
    },
    "probability_disagreement": {
      "mean_absolute_difference": 0.01,
      "median_absolute_difference": 0.01,
      "max_absolute_difference": 0.05,
      "fraction_at_least_5pct": 0.01,
    },
    "gates": gates,
    "conclusion": "ACTIVE_ELIGIBLE",
    "registerable": True,
  }
  data_fingerprint = "d" * 64
  dataset_manifest_sha256 = "e" * 64
  training_panel_sha256 = "f" * 64
  environment_requirement_hash = "9" * 64
  environment = {
    "python": "3.13.9",
    "platform": "Windows-11",
    "pandas": "2.3.0",
    "numpy": "2.0.0",
    "dependencies": {"lightgbm": "4.7.0", "numpy": "2.0.0"},
    "environment_requirement_hash": environment_requirement_hash,
    "qualification_version": None,
    "requirement_hash": None,
    "evidence_sha256": None,
    "gpu": None,
    "opencl": None,
  }
  telemetry = {
    "wall_time_seconds": 1.0,
    "process_cpu_time_seconds": 0.5,
    "runtime_memory": {
      "physical_only": True,
      "reserve_gib": 1.0,
      "peak_process_rss_gib": 1.0,
      "sampling_error": None,
    },
    "gpu": {
      "sampling_available": False,
      "sample_count": 0,
      "peak_memory_fraction": None,
      "peak_used_memory_mib": None,
      "minimum_available_memory_mib": None,
    },
    "qualification": {
      "status": "CPU_AVAILABLE",
      "acceleration": None,
      "minimum_sample_count": None,
      "peak_memory_fraction": None,
      "gates_passed": True,
      "evidence_sha256": None,
    },
    "lightgbm": {
      "backend": "CPU",
      "device_type": "cpu",
      "max_bin": 63,
      "gpu_use_dp": False,
      "parameters": {
        "LOGISTIC": {"C": 1.0},
        "LIGHTGBM": {"num_leaves": 15, "reg_lambda": 1.0},
      },
    },
    "environment": environment,
  }
  files: dict[str, object] = {
    "metrics.json": metrics,
    "data-quality.json": {
      "schema_version": 2,
      "dataset_manifest_sha256": dataset_manifest_sha256,
      "source_training_panel_sha256": training_panel_sha256,
      "source": {"kind": "verified-panel", "path": "C:/secret/panel.parquet"},
      "historical_universe": {"complete": True, "reason": None, "coverage": {"complete": True, "ratio": 1.0}},
      "data_fingerprint": data_fingerprint,
      "sample_count": 1000,
      "stock_count": 100,
      "date_count": 240,
      "data_start": "2021-09-01",
      "data_end": "2026-08-31",
      "trading_day_count": 240,
      "date_start": "2021-01-01",
      "date_end": "2025-01-31",
      "training_start": "2021-01-01",
      "training_end": "2023-07-31",
      "calibration_start": "2023-08-01",
      "calibration_end": "2024-01-31",
      "test_start": "2024-02-01",
      "test_end": "2025-01-31",
      "coverage": {"complete": True},
      "leakage_checks": {"passed": True},
    },
    "factor-schema.json": factor_schema_manifest(),
    "model-runtime.json": {
      "schema_version": 2,
      "selected_family": "LOGISTIC",
      "backend": "CPU",
      "max_bin": 63,
      "gpu_use_dp": False,
      "families": {
        "LOGISTIC": {
          "family": "LOGISTIC",
          "model_path": "logistic.json",
          "preprocessing_path": "preprocessing.json",
          "calibrator_path": "calibrators.json",
        },
        "LIGHTGBM": {
          "family": "LIGHTGBM",
          "model_path": "lightgbm.txt",
          "preprocessing_path": "preprocessing.json",
          "calibrator_path": "calibrators.json",
        },
      },
    },
    "preprocessing.json": {
      "schema_version": 1,
      "families": {"LOGISTIC": preprocessor, "LIGHTGBM": preprocessor},
    },
    "calibrators.json": {
      "schema_version": 1,
      "version": CALIBRATOR_VERSION,
      "families": {"LOGISTIC": calibrator, "LIGHTGBM": calibrator},
    },
    "logistic.json": {
      "schema_version": 1,
      "family": "LOGISTIC",
      "coefficients": [0.0] * len(columns),
      "intercept": 0.0,
    },
  }
  for name, value in files.items():
    _write_json(directory / name, value)
  (directory / "lightgbm.txt").write_text("tree\n", encoding="utf-8")
  (directory / "resolved-config.yaml").write_text("requested_backend: CPU\nresolved_backend: CPU\n", encoding="utf-8")
  (directory / "test-predictions.parquet").write_bytes(b"PARQUET-EVIDENCE")
  artifacts = [
    {
      "path": path.name,
      "bytes": path.stat().st_size,
      "sha256": file_sha256(path),
    }
    for path in sorted(directory.iterdir())
  ]
  _write_json(
    directory / "manifest.json",
    {
      "schema_version": 2,
      "study_id": "next-day-selection",
      "version": "v1",
      "run_id": directory.name,
      "run_kind": "FINAL_EVALUATION",
      "status": "SUCCEEDED",
      "model_version": model_version,
      "selected_family": "LOGISTIC",
      "indicator_version": INDICATOR_VERSION,
      "factor_set_version": FACTOR_SET_VERSION,
      "factor_set_hash": FACTOR_SET_HASH,
      "label_version": LABEL_VERSION,
      "calibrator_version": CALIBRATOR_VERSION,
      "config_hash": "c" * 64,
      "spec_hash": "a" * 64,
      "coordinate_hash": "b" * 64,
      "dataset_manifest_sha256": dataset_manifest_sha256,
      "training_panel_sha256": training_panel_sha256,
      "data_fingerprint": data_fingerprint,
      "environment_requirement_hash": environment_requirement_hash,
      "requested_backend": "CPU",
      "resolved_backend": "CPU",
      "environment": environment,
      "telemetry": telemetry,
      "training_start": "2021-01-01",
      "training_end": "2023-07-31",
      "calibration_start": "2023-08-01",
      "calibration_end": "2024-01-31",
      "test_start": "2024-02-01",
      "test_end": "2025-01-31",
      "split": split.as_dict(),
      "gates": gates,
      "conclusion": "ACTIVE_ELIGIBLE",
      "registerable": True,
      "artifacts": artifacts,
    },
  )
  return directory


def _refresh_manifest_hash(directory: Path, relative: str) -> None:
  manifest_path = directory / "manifest.json"
  manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
  for item in manifest["artifacts"]:
    if item["path"] == relative:
      path = directory / relative
      item["bytes"] = path.stat().st_size
      item["sha256"] = file_sha256(path)
      break
  _write_json(manifest_path, manifest)


@pytest.mark.asyncio
async def test_validated_final_artifact_registration_requires_explicit_promotion(tmp_path, monkeypatch):
  import shutil
  from contextlib import contextmanager
  from datetime import datetime, timezone
  from types import SimpleNamespace
  from unittest.mock import AsyncMock

  from quantx_api.stock_selection_model_service import (
    StockSelectionModelService,
    _stable_run_key,
  )
  from quantx_infrastructure.database.relational_base import Base
  from quantx_infrastructure.models.stock_selection import StockSelectionModelVersion
  from quantx_infrastructure.repositories.stock_selection_repository import (
    StockSelectionRepository,
  )
  from sqlalchemy import func, select
  from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

  directory = _valid_artifact(tmp_path / "final-run")
  manifest_path = directory / "manifest.json"
  manifest = json.loads(manifest_path.read_text())
  manifest["parent_run_id"] = "development-run"
  _write_json(manifest_path, manifest)
  metrics = json.loads((directory / "metrics.json").read_text())
  metrics["parent_development"]["run_id"] = "development-run"
  _write_json(directory / "metrics.json", metrics)
  _refresh_manifest_hash(directory, "metrics.json")
  run_key = _stable_run_key(study_id="next-day-selection", version="v1", run_id=directory.name)
  row = SimpleNamespace(
    run_id=directory.name, run_key=run_key, run_kind="FINAL_EVALUATION",
    status="SUCCEEDED", spec_id="final-spec", parent_run_id="development-run",
    artifact_manifest_sha256=file_sha256(manifest_path),
  )
  from quantx_contracts.training_bundle import TrainingBundle
  from quantx_infrastructure.training_bundle_store import DirectoryBundleReader
  from quantx_worker.prefector.flows import research_result_import_flow as importer

  bundle = TrainingBundle(schema_version=1, kind="RESULT", source_id=row.run_id, files=tuple(
    {"path": path.name, "size": path.stat().st_size, "sha256": file_sha256(path)}
    for path in directory.iterdir()
  ))
  row.artifact_bundle = bundle.model_dump(mode="json")
  remote = tmp_path / "bundle-store" / bundle.bundle_id
  shutil.copytree(directory, remote)
  shutil.rmtree(directory)

  @contextmanager
  def store(*args, **kwargs):
    yield SimpleNamespace(artifacts=DirectoryBundleReader(remote.parent))

  monkeypatch.setattr(importer, "open_store", store)
  api_root = tmp_path / "api-runs"
  directory = await importer._import_one(row, object(), api_root, tmp_path / "import-cache", 0)
  final_spec = SimpleNamespace(run_kind="FINAL_EVALUATION", spec_hash="a" * 64,
                               coordinate_hash="b" * 64, requested_backend="CPU", resolved_backend="CPU")
  parent_spec = SimpleNamespace(run_kind="DEVELOPMENT", coordinate_hash="b" * 64)
  training = SimpleNamespace(
    get_run_by_run_key=AsyncMock(return_value=row),
    get_run=AsyncMock(return_value=SimpleNamespace(run_kind="DEVELOPMENT", status="SUCCEEDED", spec_id="parent-spec")),
    get_spec=AsyncMock(side_effect=lambda key: final_spec if key == "final-spec" else parent_spec),
  )
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  try:
    async with engine.begin() as connection:
      await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[StockSelectionModelVersion.__table__]))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
      repository = StockSelectionRepository(db)
      service = StockSelectionModelService(repository, training, runs_root=api_root)
      # Actual strict artifact loader and actual database writes; no model activation.
      model = await service.register(run_key)
      assert model.stage == "CANDIDATE"
      assert await repository.runtime_models() == []
      repeated = await service.register(run_key)
      assert repeated.model_version == model.model_version
      assert await db.scalar(select(func.count()).select_from(StockSelectionModelVersion)) == 1
      now = datetime(2026, 9, 10, tzinfo=timezone.utc)
      model = await repository.set_model_stage(model.model_version, "SHADOW", expected_version=model.state_version, approved_by="isolated-test", approved_at=now)
      model = await repository.set_model_stage(model.model_version, "ACTIVE", expected_version=model.state_version, approved_by="isolated-test", approved_at=now)
      assert [(value.model_version, value.stage) for value in await repository.runtime_models()] == [(model.model_version, "ACTIVE")]
      assert model.approved_by == "isolated-test"
      # A changed artifact cannot update the registry or undo the explicit approval.
      (directory / "logistic.json").write_text("{}")
      with pytest.raises(ValueError, match="安全校验"):
        await service.register(run_key)
      assert model.stage == "ACTIVE"
  finally:
    await engine.dispose()


@pytest.mark.parametrize("field,invalid", [("run_id", "../other"), ("metrics_sha256", None), ("lock_sha256", "invalid")])
def test_selection_artifact_rejects_invalid_parent_evidence(tmp_path, field, invalid):
  directory = _valid_artifact(tmp_path / "final")
  metrics = json.loads((directory / "metrics.json").read_text())
  metrics["parent_development"][field] = invalid
  _write_json(directory / "metrics.json", metrics)
  _refresh_manifest_hash(directory, "metrics.json")
  with pytest.raises(SelectionArtifactError):
    load_selection_artifact(directory)


def test_selection_artifact_loader_projects_only_safe_evidence(tmp_path: Path) -> None:
  directory = _valid_artifact(tmp_path / "run")

  bundle = load_selection_artifact(directory)

  assert bundle.metrics["validation"] == {
    "fold_count": 12,
    "logistic_brier": 0.2,
    "lightgbm_brier": 0.21,
  }
  assert bundle.data_quality["source_kind"] == "verified-panel"
  assert "path" not in bundle.data_quality


def test_selection_artifact_loader_rejects_non_finite_json_numbers(
  tmp_path: Path,
) -> None:
  directory = _valid_artifact(tmp_path / "run")
  logistic_path = directory / "logistic.json"
  logistic = json.loads(logistic_path.read_text(encoding="utf-8"))
  logistic["coefficients"][0] = float("nan")
  _write_json(logistic_path, logistic)
  _refresh_manifest_hash(directory, "logistic.json")

  with pytest.raises(SelectionArtifactError, match="JSON"):
    load_selection_artifact(directory)


def test_selection_artifact_loader_rejects_unbounded_calibration_values(
  tmp_path: Path,
) -> None:
  directory = _valid_artifact(tmp_path / "run")
  calibrator_path = directory / "calibrators.json"
  calibrators = json.loads(calibrator_path.read_text(encoding="utf-8"))
  calibrators["families"]["LOGISTIC"] = {
    "version": CALIBRATOR_VERSION,
    "kind": "isotonic",
    "x_thresholds": [0.0, 1.0],
    "y_thresholds": [0.0, 1.1],
    "calibration_sample_count": 1000,
    "calibration_positive_count": 500,
  }
  _write_json(calibrator_path, calibrators)
  _refresh_manifest_hash(directory, "calibrators.json")

  with pytest.raises(SelectionArtifactError, match="高于上界"):
    load_selection_artifact(directory)


def test_selection_artifact_loader_rejects_impossible_calibration_counts(
  tmp_path: Path,
) -> None:
  directory = _valid_artifact(tmp_path / "run")
  calibrator_path = directory / "calibrators.json"
  calibrators = json.loads(calibrator_path.read_text(encoding="utf-8"))
  calibrators["families"]["LOGISTIC"]["calibration_positive_count"] = 1001
  _write_json(calibrator_path, calibrators)
  _refresh_manifest_hash(directory, "calibrators.json")

  with pytest.raises(SelectionArtifactError, match="正样本数超过总样本数"):
    load_selection_artifact(directory)


def test_selection_artifact_loader_rejects_legacy_schema_v1(
  tmp_path: Path,
) -> None:
  directory = _valid_artifact(tmp_path / "run")
  manifest_path = directory / "manifest.json"
  manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
  manifest["schema_version"] = 1
  _write_json(manifest_path, manifest)

  with pytest.raises(SelectionArtifactError, match="身份或状态无效"):
    load_selection_artifact(directory)


def test_selection_artifact_loader_rejects_development_bundle(
  tmp_path: Path,
) -> None:
  directory = _valid_artifact(tmp_path / "run")
  manifest_path = directory / "manifest.json"
  manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
  manifest["run_kind"] = "DEVELOPMENT"
  _write_json(manifest_path, manifest)

  with pytest.raises(SelectionArtifactError, match="DEVELOPMENT"):
    load_selection_artifact(directory)


@pytest.mark.parametrize(
  ("mutation", "match"),
  [
    (lambda manifest: manifest.pop("telemetry"), "telemetry"),
    (
      lambda manifest: manifest["telemetry"]["gpu"].update(
        {"peak_used_memory_mib": 1.0}
      ),
      "伪造 GPU",
    ),
    (
      lambda manifest: manifest["telemetry"]["lightgbm"].update({"max_bin": 127}),
      "max_bin",
    ),
    (
      lambda manifest: manifest["telemetry"]["qualification"].update(
        {"gates_passed": False}
      ),
      "CPU 成功运行资格",
    ),
    (
      lambda manifest: manifest["telemetry"]["qualification"].update(
        {"status": "GPU_UNAVAILABLE_RUNTIME"}
      ),
      "CPU 成功运行资格",
    ),
  ],
)
def test_selection_artifact_loader_rejects_invalid_runtime_evidence(
  tmp_path: Path,
  mutation,
  match: str,
) -> None:
  directory = _valid_artifact(tmp_path / "run")
  manifest_path = directory / "manifest.json"
  manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
  mutation(manifest)
  _write_json(manifest_path, manifest)

  with pytest.raises(SelectionArtifactError, match=match):
    load_selection_artifact(directory)
