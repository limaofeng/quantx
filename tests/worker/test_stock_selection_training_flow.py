from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest

flow_module = import_module("quantx_worker.prefector.flows.stock_selection_training_flow")
research_job = import_module("quantx_research.next_day_selection_job")


class TradingDates:
  async def is_trading_date(self, _market, target):
    return target.weekday() < 5


def _dataset(root: Path) -> tuple[dict, dict]:
  directory = root / "dataset-v1"
  directory.mkdir()
  panel = directory / "training-panel.parquet"
  panel.write_bytes(b"panel")
  panel_hash = flow_module._sha256_file(panel)
  quality = directory / "data-quality.json"
  quality_payload = {
    "sample_count": 0,
    "stock_count": 0,
    "trading_day_count": 0,
    "date_start": "2021-01-01",
    "date_end": "2025-12-31",
  }
  quality.write_text(json.dumps(quality_payload), encoding="utf-8")
  quality_hash = flow_module._sha256_file(quality)
  manifest = directory / "manifest.json"
  evidence = {
    "schema_version": 1,
    "dataset_version": "dataset-v1",
    "status": "CERTIFIED",
    "source_kind": "VERIFIED_PANEL",
    "source_reference": "dataset-v1",
    "panel_path": panel.name,
    "quality_path": quality.name,
    "date_start": "2021-01-01",
    "date_end": "2025-12-31",
    "universe_spec": {
      "kind": "ORDINARY_A_SHARE",
      "index_code": None,
      "benchmark_code": "000300.SH",
    },
    "indicator_version": "indicator-v1",
    "factor_set_version": "factor-v1",
    "factor_set_hash": "a" * 64,
    "label_version": "label-v1",
    "training_panel_sha256": panel_hash,
    "training_panel_bytes": panel.stat().st_size,
    "quality_sha256": quality_hash,
    "quality_bytes": quality.stat().st_size,
    "files": {
      panel.name: {"sha256": panel_hash, "bytes": panel.stat().st_size},
      quality.name: {"sha256": quality_hash, "bytes": quality.stat().st_size},
    },
    "quality": quality_payload,
  }
  evidence["manifest_sha256"] = flow_module._manifest_evidence_sha256(evidence)
  manifest.write_text(json.dumps(evidence), encoding="utf-8")
  dataset = {
    "dataset_version": "dataset-v1",
    "status": "CERTIFIED",
    "source_kind": "VERIFIED_PANEL",
    "source_reference": "dataset-v1",
    "date_start": "2021-01-01",
    "date_end": "2025-12-31",
    "universe_spec": evidence["universe_spec"],
    "indicator_version": "indicator-v1",
    "factor_set_version": "factor-v1",
    "factor_set_hash": "a" * 64,
    "label_version": "label-v1",
    "manifest_sha256": evidence["manifest_sha256"],
    "sample_count": 0,
    "stock_count": 0,
    "trading_day_count": 0,
    "quality_summary": quality_payload,
  }
  return dataset, {"directory": directory, "manifest_path": manifest, "panel_path": panel}


def _spec() -> dict:
  return {
    "spec_hash": "b" * 64,
    "coordinate_hash": "c" * 64,
    "environment_requirement_hash": "d" * 64,
    "dataset_version": "dataset-v1",
    "split_spec": {
      "date_start": "2021-01-01",
      "date_end": "2025-12-31",
      "minimum_training_months": 30,
      "calibration_months": 6,
      "validation_months": 1,
      "frozen_test_months": 12,
    },
    "model_spec": {
      "logistic": {"c_values": [0.1, 1.0, 10.0], "max_iter": 1000},
      "lightgbm": {
        "num_leaves": [15, 31],
        "reg_lambda": [1.0, 5.0],
        "learning_rate": 0.03,
        "n_estimators": 500,
        "min_child_samples": 100,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "max_bin": 63,
        "gpu_use_dp": False,
        "gpu_platform_id": None,
        "gpu_device_id": None,
      },
      "calibration": {
        "bins": 10,
        "isotonic_minimum_positives": 20_000,
        "isotonic_minimum_relative_brier_improvement": 0.01,
      },
      "candidate_gate": {
        "brier_skill_minimum": 0.0,
        "ece_maximum": 0.03,
        "minimum_probability": 0.6,
        "minimum_factor_completeness": 0.9,
        "minimum_valid_history": 252,
        "level_a_size": 20,
        "level_b_size": 30,
      },
    },
    "evaluation_spec": {"bootstrap_samples": 100},
    "requested_backend": "CPU",
    "resolved_backend": "CPU",
    "random_seed": 1,
    "worker_batch_size": 10,
    "frozen_test_access_count": 0,
    "universe_spec": {
      "kind": "ORDINARY_A_SHARE",
      "index_code": None,
      "benchmark_code": "000300.SH",
      "minimum_listing_days": 252,
      "stock_codes": None,
    },
  }


@pytest.mark.asyncio
async def test_trading_window_uses_trading_calendar_and_weekend_is_safe() -> None:
  assert await flow_module.is_critical_trading_window(
    datetime(2026, 9, 4, 1, 30), trading_dates=TradingDates()
  ) is True
  assert await flow_module.is_critical_trading_window(
    datetime(2026, 9, 5, 1, 30), trading_dates=TradingDates()
  ) is False
  assert await flow_module.is_critical_trading_window(
    datetime(2026, 9, 4, 8, 31), trading_dates=TradingDates()
  ) is False


def test_dataset_path_and_research_request_are_strict_and_path_safe(tmp_path, monkeypatch) -> None:
  dataset, files = _dataset(tmp_path)
  monkeypatch.setenv(flow_module.RESEARCH_RUNS_ENV, str(tmp_path / "runs"))
  resolved = flow_module.resolve_dataset_directory(dataset, root=tmp_path)
  assert resolved["panel_path"].name == "training-panel.parquet"
  request_path = tmp_path / "control" / "request.json"
  request_path.parent.mkdir()
  request = flow_module.build_training_request(
    SimpleNamespace(run_id="run-1", run_kind="DEVELOPMENT", spec_id="spec-1"),
    _spec(),
    dataset,
    {**files, "directory": resolved["directory"]},
    request_path.parent,
  )
  request_path.write_text(json.dumps(request), encoding="utf-8")
  loaded = json.loads(request_path.read_text(encoding="utf-8"))
  assert set(loaded) == set(research_job._REQUEST_KEYS)
  assert loaded["spec"]["requested_backend"] == "CPU"
  assert loaded["spec"]["spec_hash"] == "b" * 64
  assert loaded["spec"]["coordinate_hash"] == "c" * 64
  assert loaded["spec"]["environment_requirement_hash"] == "d" * 64
  with pytest.raises(ValueError, match="CERTIFIED"):
    flow_module.resolve_dataset_directory({"source_reference": "../escape"}, root=tmp_path)


def test_dataset_evidence_is_fail_closed_and_junctions_are_rejected(tmp_path, monkeypatch) -> None:
  dataset, _files = _dataset(tmp_path)
  incomplete = dict(dataset)
  incomplete.pop("factor_set_hash")
  with pytest.raises(ValueError, match="factor_set_hash evidence is missing"):
    flow_module.resolve_dataset_directory(incomplete, root=tmp_path)

  monkeypatch.setattr(
    Path,
    "is_junction",
    lambda path: path.name == "dataset-v1",
    raising=False,
  )
  with pytest.raises(ValueError, match="junction"):
    flow_module.resolve_dataset_directory(dataset, root=tmp_path)


def test_cancel_request_uses_research_json_contract(tmp_path) -> None:
  cancel_path = tmp_path / "cancel.request"
  flow_module._write_cancel_request(cancel_path)
  assert json.loads(cancel_path.read_text(encoding="utf-8")) == {"cancel": True}


def test_worker_error_redaction_removes_complete_absolute_paths(tmp_path) -> None:
  control = tmp_path / "control"
  control.mkdir()
  (control / "stderr.log").write_text(
    "failed F:\\Private Folder\\secret.parquet and \\\\server\\Private Folder\\x.parquet\n"
    "also /private folder/file.parquet",
    encoding="utf-8",
  )
  text = flow_module._tail_logs(control)
  assert text.count("[PATH]") == 2
  assert "Private Folder" not in text
  assert "secret.parquet" not in text


def test_capability_probe_uses_the_installed_research_cli_protocol(monkeypatch) -> None:
  payload = {
    "status": "GPU_AVAILABLE",
    "qualification": {
      "status": "GPU_AVAILABLE",
      "acceleration": 0.3,
      "minimum_sample_count": 100,
      "peak_memory_fraction": 0.5,
      "gates_passed": True,
      "evidence_sha256": "a" * 64,
    },
    "qualification_version": "next-day-selection-gpu-v2",
    "requirement_hash": "b" * 64,
    "available_memory_mib": 4096.0,
  }
  calls = []

  def run(command, **kwargs):
    calls.append((command, kwargs))
    return SimpleNamespace(returncode=0, stdout=json.dumps(payload))

  monkeypatch.setattr(flow_module, "_research_cli_command", lambda: ["quantx-research"])
  monkeypatch.setattr(flow_module.subprocess, "run", run)

  assert flow_module._probe_capability() == payload
  assert calls[0][0] == ["quantx-research", "probe-lightgbm-gpu", "--json"]
  assert calls[0][1]["timeout"] == 10


def test_research_cli_uses_the_worker_interpreter_module_protocol(monkeypatch) -> None:
  monkeypatch.setattr(sys, "executable", "workspace-python")

  assert flow_module._research_cli_command() == [
    "workspace-python",
    "-m",
    "quantx_research.cli",
  ]


def test_spawn_uses_low_priority_and_exact_research_entrypoint(tmp_path, monkeypatch) -> None:
  calls = []

  class Process:
    def poll(self):
      return 0

  def popen(command, **kwargs):
    calls.append((command, kwargs))
    return Process()

  monkeypatch.setattr(flow_module.subprocess, "Popen", popen)
  monkeypatch.setattr(flow_module, "_research_cli_command", lambda: ["quantx-research"])
  monkeypatch.setattr(flow_module.os, "name", "nt", raising=False)
  control = tmp_path / "control"
  control.mkdir()
  process = flow_module._spawn_process(tmp_path / "request.json", control)
  assert isinstance(process, Process)
  assert calls[0][0][:2] == ["quantx-research", "run-next-day-selection-job"]
  assert calls[0][0][-2:] == ["--request-file", str(tmp_path / "request.json")]
  assert calls[0][1]["creationflags"] == (
    getattr(subprocess, "CREATE_NO_WINDOW", 0)
    | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
  )


def test_full_live_runtime_reads_authoritative_launcher_environment(monkeypatch) -> None:
  monkeypatch.delenv("ENV", raising=False)
  monkeypatch.setenv("RUNTIME_PROFILE", "full")
  monkeypatch.setenv("QMT_AGENT_MODE", "live")
  assert flow_module._full_live_runtime() is True
  monkeypatch.setenv("QMT_AGENT_MODE", "data-only")
  assert flow_module._full_live_runtime() is False


def test_parent_research_directory_is_bound_to_run_id_and_stable_key(tmp_path) -> None:
  run_directory = tmp_path / "run-1"
  run_directory.mkdir()
  manifest = {
    "run_id": "run-1",
    "run_kind": "DEVELOPMENT",
    "status": "SUCCEEDED",
    "study_id": "next-day-selection",
    "version": "v1",
  }
  (run_directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
  expected = flow_module._stable_research_run_key(manifest)
  assert flow_module.find_research_run_directory(
    parent_run_id="run-1", expected_run_key=expected, root=tmp_path
  ) == run_directory
  with pytest.raises(ValueError, match="identity"):
    flow_module.find_research_run_directory(
      parent_run_id="run-1", expected_run_key="0" * 64, root=tmp_path
    )
