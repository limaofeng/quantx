from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import quantx_research.next_day_selection_gpu as gpu
from quantx_domain.selection_factors import selection_feature_columns
from quantx_domain.stock_selection_training import (
  BackendResolutionError,
  resolve_backend,
  stable_json_sha256,
)
from quantx_infrastructure.training_host_guard import HostAdmissionDenied
from quantx_research.artifacts import file_sha256, fingerprint, write_json
from quantx_research.next_day_selection_training import _data_fingerprint


def _build_evidence() -> dict[str, object]:
  return {
    "schema_version": 1,
    "lightgbm_version": "4.7.0",
    "wheel": "lightgbm-4.7.0-cp312-cp312-win_amd64.whl",
    "wheel_sha256": "f" * 64,
    "use_gpu": True,
    "cmake_definitions": ["--config-settings=cmake.define.USE_GPU=ON"],
    "platform": "Windows",
    "cmake": "cmake version 3.30.0",
    "visual_studio_cl": "19.40",
    "boost_root_configured": False,
    "boost_librarydir_configured": False,
    "opencl_include_dir_configured": False,
    "opencl_library_configured": False,
    "source_revision": "deadbeef",
    "wheel_metadata_version": "4.7.0",
    "python": "Python 3.12.0",
    "python_abi": "cp312",
    "built_at_utc": "2026-09-02T00:00:00+00:00",
  }


def test_official_wheel_requires_matching_artifact_and_installed_binary(tmp_path, monkeypatch):
  import lightgbm.libpath

  wheel = tmp_path / "lightgbm.whl"
  binary = tmp_path / "lib_lightgbm.dll"
  binary.write_bytes(b"gpu-binary")
  with zipfile.ZipFile(wheel, "w") as archive:
    archive.writestr("lightgbm/bin/lib_lightgbm.dll", binary.read_bytes())
  monkeypatch.setattr(gpu, "_OFFICIAL_GPU_WHEEL_SHA256", file_sha256(wheel))
  monkeypatch.setattr(gpu, "lgb", SimpleNamespace(__version__="4.6.0"))
  monkeypatch.setattr(lightgbm.libpath, "_find_lib_path", lambda: [str(binary)])
  evidence_hash, public = gpu._load_build_evidence(wheel)
  assert evidence_hash == stable_json_sha256(public)
  assert public["binary_sha256"] == hashlib.sha256(b"gpu-binary").hexdigest()
  assert gpu._valid_public_build_evidence(public)
  binary.write_bytes(b"cpu-binary")
  with pytest.raises(ValueError, match="二进制"):
    gpu._load_build_evidence(wheel)
  wheel.write_bytes(b"untrusted-wheel")
  with pytest.raises(ValueError, match="SHA-256"):
    gpu._load_build_evidence(wheel)


def test_qualification_benchmark_excludes_future_outcomes(monkeypatch):
  panel = pd.DataFrame({column: np.arange(100) for column in selection_feature_columns()})
  panel["label"] = np.tile([0, 1], 50)
  panel["event_date"] = pd.date_range("2025-01-01", periods=100)
  panel["stock_code"] = "000001.SZ"
  panel["next_open_to_close_return"] = 999999.0
  panel["next_close"] = 999999.0

  class Model:
    def __init__(self, **kwargs):
      assert kwargs["n_jobs"] == 1

    def fit(self, x, y):
      assert x.shape[1] == len(selection_feature_columns())
      assert np.max(x) < 999999.0

    def predict_proba(self, x):
      return np.tile([0.5, 0.5], (len(x), 1))

  monkeypatch.setattr(gpu, "lgb", SimpleNamespace(LGBMClassifier=Model))
  result = gpu._default_backend_trial(panel, device_type="cpu")
  assert result["no_non_finite"] is True


def _certified_dataset(tmp_path: Path) -> Path:
  directory = tmp_path / "dataset"
  directory.mkdir()
  panel = pd.DataFrame(
    {
      "event_date": pd.date_range("2025-01-01", periods=30, freq="B"),
      "target_date": pd.date_range("2025-01-01", periods=30, freq="B") + pd.offsets.BDay(1),
      "stock_code": [f"{index:06d}.SZ" for index in range(30)],
      "label": [index % 2 for index in range(30)],
      "next_open_to_close_return": [0.01 if index % 2 else -0.01 for index in range(30)],
      "month": pd.period_range("2025-01", periods=30, freq="D").asfreq("M"),
      "open_date": pd.Timestamp("2023-01-01"),
      "valid_history": 500,
    }
  )
  for column in selection_feature_columns():
    panel[column] = np.linspace(0.0, 1.0, len(panel))
  panel_path = directory / "training-panel.parquet"
  panel.to_parquet(panel_path, index=False)
  panel_sha = file_sha256(panel_path)
  quality = {
    "sample_count": len(panel),
    "stock_count": len(panel),
    "trading_day_count": len(panel),
    "date_start": "2025-01-01",
    "date_end": "2025-02-11",
    "coverage": {},
    "leakage_checks": {"target_after_event": True},
  }
  quality_path = directory / "data-quality.json"
  write_json(quality_path, quality)
  manifest = {
    "schema_version": 2,
    "dataset_version": "gpu-test",
    "status": "CERTIFIED",
    "source_kind": "VERIFIED_PANEL",
    "source_reference": "gpu-test",
    "date_start": "2025-01-01",
    "date_end": "2025-02-11",
    "panel_path": "training-panel.parquet",
    "quality_path": "data-quality.json",
    "universe_spec": {"kind": "ORDINARY_A_SHARE", "index_code": None, "benchmark_code": "000300.SH", "stock_codes": None, "minimum_listing_days": 252},
    "indicator_version": "daily-indicator-v1",
    "factor_set_version": "selection-factor-v1",
    "factor_set_hash": "0" * 64,
    "label_version": "next-open-to-close-v1",
    "files": {
      "training-panel.parquet": {"sha256": panel_sha, "bytes": panel_path.stat().st_size},
      "data-quality.json": {"sha256": file_sha256(quality_path), "bytes": quality_path.stat().st_size},
    },
    "training_panel_sha256": panel_sha,
    "training_panel_bytes": panel_path.stat().st_size,
    "quality_sha256": file_sha256(quality_path),
    "quality_bytes": quality_path.stat().st_size,
    "data_fingerprint": _data_fingerprint(panel),
    "quality": quality,
    "immutable": True,
    "created_at": "2026-09-02T00:00:00+00:00",
  }
  manifest["manifest_sha256"] = fingerprint(manifest)
  write_json(directory / "manifest.json", manifest)
  return directory


def test_probe_classifies_build_and_runtime_failures(monkeypatch) -> None:
  monkeypatch.setattr(gpu, "lgb", None)
  assert gpu.probe_lightgbm_gpu()["status"] == "GPU_UNAVAILABLE_BUILD"

  monkeypatch.setattr(gpu, "lgb", object())
  monkeypatch.setattr(gpu, "_gpu_build_probe", lambda: (False, "OpenCL GPU 运行时探针失败"))
  assert gpu.probe_lightgbm_gpu()["status"] == "GPU_UNAVAILABLE_RUNTIME"


def test_gpu_probe_admission_precedes_model_initialization(monkeypatch):
  calls = []

  def blocked():
    calls.append("admission")
    raise HostAdmissionDenied("HOST_GPU_MEMORY_BUDGET")

  def model(**kwargs):
    pytest.fail("blocked probe must not initialize LightGBM")

  monkeypatch.setattr(gpu, "_monitor_host_gpu_memory", blocked)
  monkeypatch.setattr(gpu, "lgb", SimpleNamespace(LGBMClassifier=model))
  with pytest.raises(HostAdmissionDenied, match="HOST_GPU_MEMORY_BUDGET"):
    gpu._gpu_build_probe()
  assert calls == ["admission"]


def test_gpu_probe_uses_admitted_thread_budget(monkeypatch):
  calls = []
  monkeypatch.setattr(gpu, "_monitor_host_gpu_memory", lambda: calls.append("admission"))
  monkeypatch.setattr(gpu, "training_cpu_threads", lambda: 2)

  class Model:
    def __init__(self, **kwargs):
      assert calls == ["admission"]
      assert kwargs["n_jobs"] == 2
      assert kwargs["device_type"] == "gpu"

    def fit(self, x, y):
      calls.append("fit")

  monkeypatch.setattr(gpu, "lgb", SimpleNamespace(LGBMClassifier=Model))
  assert gpu._gpu_build_probe() == (True, None)
  assert calls == ["admission", "fit"]


@pytest.mark.parametrize("blocked_backend", ["cpu", "gpu"])
def test_qualification_stops_immediately_on_admission_denial(tmp_path, blocked_backend):
  dataset = _certified_dataset(tmp_path)
  output = tmp_path / "qualification.json"
  calls = []

  def trial(panel, *, device_type, gpu_use_dp):
    calls.append(device_type)
    if device_type == blocked_backend:
      raise HostAdmissionDenied("HOST_GPU_MEMORY_STATE_UNKNOWN")
    return {}

  with pytest.raises(HostAdmissionDenied, match="HOST_GPU_MEMORY_STATE_UNKNOWN"):
    gpu.qualify_lightgbm_gpu(dataset, output, trial_runner=trial)
  assert calls == (["cpu"] if blocked_backend == "cpu" else ["cpu", "gpu"])
  assert not output.exists()


def test_requirement_hash_invalidates_binary_and_device_changes(monkeypatch, tmp_path):
  import lightgbm.libpath

  binary = tmp_path / "lib.dll"
  binary.write_bytes(b"first-build")
  monkeypatch.setattr(lightgbm.libpath, "_find_lib_path", lambda: [str(binary)])
  monkeypatch.setattr(
    gpu, "_run_nvidia_smi", lambda: {"model": "GPU-A", "driver_version": "1"}
  )
  monkeypatch.setattr(gpu, "_opencl_devices", lambda: {})
  first = gpu.gpu_requirement_hash()
  binary.write_bytes(b"second-build")
  second = gpu.gpu_requirement_hash()
  assert first != second
  monkeypatch.setattr(
    gpu, "_run_nvidia_smi", lambda: {"model": "GPU-B", "driver_version": "1"}
  )
  assert gpu.gpu_requirement_hash() != second


@pytest.mark.parametrize("official_wheel", [False, True])
def test_probe_requires_complete_matching_qualification(monkeypatch, tmp_path: Path, official_wheel) -> None:
  build_evidence = _build_evidence()
  build_evidence_hash = stable_json_sha256(build_evidence)
  _, build_evidence_public = gpu._load_build_evidence(build_evidence) or (None, None)
  if official_wheel:
    build_evidence_public = {
      "schema_version": 2,
      "source": "pypi-official-wheel",
      "lightgbm_version": "4.6.0",
      "wheel_sha256": gpu._OFFICIAL_GPU_WHEEL_SHA256,
      "binary_sha256": "a" * 64,
      "platform": "Windows",
      "use_gpu": True,
    }
    build_evidence_hash = stable_json_sha256(build_evidence_public)
  evidence = {
    "golden_panel": "abc",
    "version": 1,
    "labels_count": 30,
    "build_evidence_sha256": build_evidence_hash,
  }
  qualification = {
    "schema_version": 1,
    "qualification_version": gpu.GPU_QUALIFICATION_VERSION,
    "status": "GPU_AVAILABLE",
    "requirement_hash": "req-1",
    "evidence": evidence,
    "evidence_sha256": stable_json_sha256(evidence),
    "brier_relative_difference": 0.001,
    "ece_absolute_difference": 0.001,
    "top20_overlap": 0.95,
    "speedup": 0.3,
    "minimum_sample_count": 30,
    "build_evidence_sha256": build_evidence_hash,
    "build_evidence": build_evidence_public,
    "peak_memory_fraction": 0.5,
    "model_cpu_loadable": True,
    "no_non_finite": True,
    "conclusion_not_flipped": True,
    "repeat_count": 3,
    "fp32_repeat_count": 3,
    "fp64_runs": 3,
    "fp64_brier_relative_difference": 0.001,
    "fp64_ece_absolute_difference": 0.001,
    "fp64_top20_overlap": 0.95,
    "memory_sampling_available": True,
    "cpu_reload_max_abs_difference": 0.0,
    "fp64_cpu_reload_max_abs_difference": 0.0,
    "repeat_consistent": True,
    "environment": {"build_evidence_sha256": build_evidence_hash},
  }
  qualification["integrity"] = {
    "algorithm": "sha256",
    "kind": "UNSIGNED_DIGEST",
    "signed": False,
    "digest": gpu._integrity_digest(qualification),
  }
  path = tmp_path / "qualification.json"
  path.write_text(json.dumps(qualification), encoding="utf-8")
  monkeypatch.setattr(gpu, "lgb", object())
  monkeypatch.setattr(gpu, "_gpu_build_probe", lambda: (True, None))
  monkeypatch.setattr(
    gpu,
    "_environment_evidence",
    lambda: {"gpu": {"memory_total_mib": 1000, "memory_free_mib": 900}},
  )
  result = gpu.probe_lightgbm_gpu(qualification_path=path, requirement_hash="req-1")
  assert result["status"] == "GPU_AVAILABLE"
  path.write_text(json.dumps({**qualification, "requirement_hash": "other"}), encoding="utf-8")
  assert gpu.probe_lightgbm_gpu(qualification_path=path, requirement_hash="req-1")["status"] == "GPU_UNQUALIFIED"


@pytest.mark.parametrize("holdout_only", [False, True])
def test_qualification_writes_evidence_and_applies_all_gates(tmp_path: Path, holdout_only) -> None:
  dataset = _certified_dataset(tmp_path)

  def trial_runner(panel: pd.DataFrame, *, device_type: str, gpu_use_dp: bool) -> dict:
    gpu_run = device_type == "gpu"
    if holdout_only:
      panel = panel.iloc[int(len(panel) * 0.8):]
    probability = np.linspace(0.1, 0.9, len(panel)).tolist()
    return {
      "probabilities": probability,
      "labels": panel["label"].tolist(),
      "brier": 0.2005 if gpu_run else 0.2,
      "ece": 0.101 if gpu_run else 0.1,
      "elapsed_seconds": 70.0 if gpu_run else 100.0,
      "top20": list(range(20)),
      "model_loadable_on_cpu": True,
      "cpu_reload_max_abs_difference": 0.0,
      "no_non_finite": True,
      "peak_memory_fraction": 0.5,
      "memory_sampling_available": True,
      "gate_conclusion": "SHADOW_ELIGIBLE",
    }

  output = tmp_path / "qualification.json"
  evidence = gpu.qualify_lightgbm_gpu(
    dataset,
    output,
    requirement_hash="req-1",
    trial_runner=trial_runner,
    build_evidence=_build_evidence(),
  )
  assert evidence["status"] == "GPU_AVAILABLE"
  assert evidence["speedup"] >= 0.2
  assert evidence["evidence_sha256"] == stable_json_sha256(evidence["evidence"])
  assert evidence["minimum_sample_count"] == 30
  assert gpu._qualification_is_complete(evidence, requirement_hash="req-1")
  assert evidence["build_evidence"]["lightgbm_version"] == "4.7.0"
  assert json.loads(output.read_text(encoding="utf-8"))["status"] == "GPU_AVAILABLE"


def test_qualification_missing_peak_memory_or_conclusion_is_unqualified(tmp_path: Path) -> None:
  dataset = _certified_dataset(tmp_path)

  def incomplete_trial(panel: pd.DataFrame, *, device_type: str, gpu_use_dp: bool) -> dict:
    probabilities = np.linspace(0.1, 0.9, len(panel)).tolist()
    return {
      "probabilities": probabilities,
      "labels": panel["label"].tolist(),
      "brier": 0.2,
      "ece": 0.1,
      "elapsed_seconds": 1.0,
      "model_loadable_on_cpu": True,
      "no_non_finite": True,
    }

  output = tmp_path / "incomplete-qualification.json"
  evidence = gpu.qualify_lightgbm_gpu(
    dataset,
    output,
    requirement_hash="req-1",
    trial_runner=incomplete_trial,
    build_evidence=_build_evidence(),
  )
  assert evidence["status"] == "GPU_UNQUALIFIED"
  assert output.is_file()


def test_valid_gpu_certificate_projects_to_domain_backend_and_sample_gate(
  monkeypatch, tmp_path: Path
) -> None:
  dataset = _certified_dataset(tmp_path)

  def trial_runner(panel: pd.DataFrame, *, device_type: str, gpu_use_dp: bool) -> dict:
    gpu_run = device_type == "gpu"
    probabilities = np.linspace(0.1, 0.9, len(panel)).tolist()
    return {
      "probabilities": probabilities,
      "labels": panel["label"].tolist(),
      "brier": 0.2005 if gpu_run else 0.2,
      "ece": 0.101 if gpu_run else 0.1,
      "elapsed_seconds": 70.0 if gpu_run else 100.0,
      "model_loadable_on_cpu": True,
      "cpu_reload_max_abs_difference": 0.0,
      "no_non_finite": True,
      "peak_memory_fraction": 0.5,
      "memory_sampling_available": True,
      "gate_conclusion": "SHADOW_ELIGIBLE",
    }

  output = tmp_path / "qualification-e2e.json"
  gpu.qualify_lightgbm_gpu(
    dataset,
    output,
    requirement_hash="req-1",
    trial_runner=trial_runner,
    build_evidence=_build_evidence(),
  )
  monkeypatch.setattr(gpu, "lgb", object())
  monkeypatch.setattr(gpu, "_gpu_build_probe", lambda: (True, None))
  monkeypatch.setattr(
    gpu,
    "_environment_evidence",
    lambda: {"gpu": {"memory_total_mib": 1000, "memory_free_mib": 900}},
  )
  capability = gpu.probe_lightgbm_gpu(
    qualification_path=output,
    requirement_hash="req-1",
    sample_count=30,
    estimated_memory_fraction=0.5,
  )
  assert capability["status"] == "GPU_AVAILABLE"
  assert capability["available_memory_mib"] == 900.0
  qualification = capability["qualification"]
  assert qualification["status"] == "GPU_AVAILABLE"
  assert qualification["acceleration"] == pytest.approx(0.3)
  assert qualification["minimum_sample_count"] == 30
  assert qualification["peak_memory_fraction"] == 0.5
  assert qualification["gates_passed"] is True
  assert isinstance(qualification["evidence_sha256"], str)
  assert resolve_backend("AUTO", qualification, sample_count=30).resolved_backend.value == "LIGHTGBM_OPENCL_GPU"
  assert resolve_backend("GPU_REQUIRED", qualification, sample_count=30).resolved_backend.value == "LIGHTGBM_OPENCL_GPU"
  assert resolve_backend("AUTO", qualification, sample_count=29).resolved_backend.value == "CPU"
  with pytest.raises(BackendResolutionError):
    resolve_backend("GPU_REQUIRED", qualification, sample_count=29)


def test_gpu_certificate_paths_reject_mocked_junction(monkeypatch, tmp_path: Path) -> None:
  monkeypatch.setattr(Path, "is_junction", lambda self: self.name == "junction", raising=False)
  with pytest.raises(ValueError, match="联接点"):
    gpu._safe_output(tmp_path / "junction" / "qualification.json")
  with pytest.raises(ValueError, match="联接点"):
    gpu._load_qualification(tmp_path / "junction" / "qualification.json")
