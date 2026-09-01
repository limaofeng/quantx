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
    "active_eligible": True,
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
    "schema_version": 1,
    "model_version": model_version,
    "selected_family": "LOGISTIC",
    "validation": {
      "fold_count": 12,
      "logistic_brier": 0.2,
      "lightgbm_brier": 0.21,
      "untrusted_extra": "must not escape",
    },
    "frozen_test": {
      "start": "2025-09-01",
      "end": "2026-08-31",
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
    },
    "gates": gates,
  }
  data_fingerprint = "d" * 64
  files: dict[str, object] = {
    "metrics.json": metrics,
    "data-quality.json": {
      "source": {"kind": "verified-panel", "path": "C:/secret/panel.parquet"},
      "historical_universe": {"complete": True, "reason": None, "coverage": 1.0},
      "data_fingerprint": data_fingerprint,
      "sample_count": 1000,
      "stock_count": 100,
      "date_count": 240,
      "data_start": "2021-09-01",
      "data_end": "2026-08-31",
    },
    "factor-schema.json": factor_schema_manifest(),
    "model-runtime.json": {
      "selected_family": "LOGISTIC",
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
      "schema_version": 1,
      "study_id": "next-day-selection",
      "version": "v1",
      "status": "success",
      "model_version": model_version,
      "selected_family": "LOGISTIC",
      "indicator_version": INDICATOR_VERSION,
      "factor_set_version": FACTOR_SET_VERSION,
      "factor_set_hash": FACTOR_SET_HASH,
      "label_version": LABEL_VERSION,
      "calibrator_version": CALIBRATOR_VERSION,
      "config_hash": "c" * 64,
      "data_fingerprint": data_fingerprint,
      "training_start": "2021-09-01",
      "training_end": "2025-02-28",
      "calibration_start": "2025-03-01",
      "calibration_end": "2025-08-31",
      "test_start": "2025-09-01",
      "test_end": "2026-08-31",
      "gates": gates,
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
