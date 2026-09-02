from __future__ import annotations

import pytest
from quantx_domain.stock_selection_training import (
  BackendResolutionError,
  GateConclusion,
  RequestedBackend,
  ResolvedBackend,
  RunStatus,
  TrainingPhase,
  advance_phase,
  build_training_time_split,
  can_transition_status,
  gate_conclusion,
  resolve_backend,
  stable_json_sha256,
  transition_run_status,
)


def test_canonical_hash_and_fixed_chronological_split() -> None:
  assert stable_json_sha256({"b": 2, "a": 1}) == stable_json_sha256(
    {"a": 1, "b": 2}
  )
  months = [f"{2020 + index // 12:04d}-{index % 12 + 1:02d}" for index in range(60)]
  split = build_training_time_split(months)
  assert len(split.development_months) == 48
  assert len(split.frozen_test_months) == 12
  assert len(split.folds) == 12
  assert len(split.folds[0].train_months) == 30
  assert len(split.folds[0].calibration_months) == 6
  assert split.folds[0].validation_month == "2023-01"
  with pytest.raises(ValueError, match="不得重叠|重复"):
    build_training_time_split([*months, months[-1]])
  with pytest.raises(ValueError, match="连续自然月"):
    build_training_time_split([*months[:40], *months[41:]])
  with pytest.raises(ValueError, match="1..12"):
    build_training_time_split(["2020-00", *months[1:]])
  with pytest.raises(ValueError, match="1..12"):
    build_training_time_split([*months[:10], "2020-13", *months[11:]])


def test_status_and_phase_transitions_are_monotonic() -> None:
  assert can_transition_status(RunStatus.QUEUED, RunStatus.RUNNING)
  assert transition_run_status(RunStatus.RUNNING, RunStatus.SUCCEEDED) is RunStatus.SUCCEEDED
  assert not can_transition_status(RunStatus.SUCCEEDED, RunStatus.RUNNING)
  with pytest.raises(ValueError, match="非法运行状态"):
    transition_run_status(RunStatus.SUCCEEDED, RunStatus.RUNNING)
  assert advance_phase(TrainingPhase.PREFLIGHT, TrainingPhase.DATASET_BUILD) is TrainingPhase.DATASET_BUILD
  with pytest.raises(ValueError, match="不得回退"):
    advance_phase(TrainingPhase.WALK_FORWARD, TrainingPhase.PREFLIGHT)


def test_backend_resolution_has_no_gpu_fallback_for_required_backend() -> None:
  cpu = resolve_backend(RequestedBackend.CPU, None)
  assert cpu.resolved_backend is ResolvedBackend.CPU
  auto = resolve_backend(
    "AUTO",
    {"status": "GPU_UNQUALIFIED"},
    sample_count=500_000,
  )
  assert auto.resolved_backend is ResolvedBackend.CPU
  with pytest.raises(BackendResolutionError):
    resolve_backend(
      "GPU_REQUIRED",
      {
        "status": "GPU_AVAILABLE",
        "acceleration": 0.3,
        "minimum_sample_count": 100_000,
      },
      sample_count=500_000,
      estimated_memory_fraction=0.81,
    )
  gpu = resolve_backend(
    "AUTO",
    {
      "status": "GPU_AVAILABLE",
      "acceleration": 0.3,
      "minimum_sample_count": 100_000,
    },
    sample_count=500_000,
    estimated_memory_fraction=0.5,
  )
  assert gpu.resolved_backend is ResolvedBackend.LIGHTGBM_OPENCL_GPU
  legacy = resolve_backend(
    "AUTO",
    {
      "status": "GPU_AVAILABLE",
      "speedup_ratio": 0.3,
      "min_sample_count": 1,
    },
    sample_count=500_000,
    estimated_memory_fraction=0.5,
  )
  assert legacy.resolved_backend is ResolvedBackend.CPU
  with pytest.raises(BackendResolutionError):
    resolve_backend(
      "GPU_REQUIRED",
      {
        "status": "GPU_AVAILABLE",
        "speedup_ratio": 0.3,
        "min_sample_count": 1,
      },
      sample_count=500_000,
      estimated_memory_fraction=0.5,
    )


def test_gate_conclusion_blocks_bad_effect_and_repeats_are_shadow_only() -> None:
  good = {
    "brier_skill_positive": True,
    "ece_within_limit": True,
    "top20_lift_ci_lower_positive": True,
    "historical_universe_complete": True,
    "artifact_valid": True,
    "data_quality_valid": True,
    "unbiased_frozen_evidence": True,
  }
  assert gate_conclusion(good, frozen_test_access_count=1) is GateConclusion.ACTIVE_ELIGIBLE
  assert gate_conclusion(good, frozen_test_access_count=2) is GateConclusion.SHADOW_ELIGIBLE
  bad = dict(good, ece_within_limit=False)
  assert gate_conclusion(bad, frozen_test_access_count=1) is GateConclusion.BLOCKED
