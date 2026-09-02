from __future__ import annotations

from datetime import date

import pytest
from quantx_application.stock_selection_training import (
  StockSelectionTrainingApplication,
  TrainingBackendUnavailable,
  TrainingPreviewMismatch,
)

DATASET = {
  "dataset_version": "dataset-v1",
  "status": "CERTIFIED",
  "source_kind": "CERTIFIED_PANEL",
  "source_reference": "dataset-v1",
  "date_start": date(2021, 1, 1),
  "date_end": date(2025, 12, 31),
  "universe_spec": {
    "kind": "ORDINARY_A_SHARE",
    "benchmark_code": "000300.SH",
  },
  "indicator_version": "daily-indicator-v1",
  "factor_set_version": "next-day-selection-factor-v1",
  "factor_set_hash": "a" * 64,
  "label_version": "next-open-to-close-up-v1",
  "manifest_sha256": "b" * 64,
  "sample_count": 250_000,
  "stock_count": 5_000,
  "trading_day_count": 1_200,
  "quality_summary": {
    "coverage": {"complete": True},
    "leakage_checks": {"target_after_event": True},
    "shadow_reasons": [],
  },
}


class Port:
  def __init__(self, *, capability: dict | None = None) -> None:
    self.capability = capability or {
      "status": "CPU_AVAILABLE",
      "gpu_status": "GPU_UNAVAILABLE_RUNTIME",
      "environment_requirement_hash": "c" * 64,
    }
    self.specs: dict[str, dict] = {}
    self.runs: dict[str, dict] = {}
    self.idempotency: dict[str, dict] = {}

  async def get_dataset(self, dataset_version: str):
    return DATASET if dataset_version == DATASET["dataset_version"] else None

  async def get_capability(self, **_kwargs):
    return self.capability

  async def create_spec(self, values):
    self.specs[values["spec_id"]] = dict(values)
    return self.specs[values["spec_id"]]

  async def create_run(self, values):
    self.runs[values["run_id"]] = dict(values)
    self.idempotency[values["idempotency_key"]] = self.runs[values["run_id"]]
    return self.runs[values["run_id"]]

  async def create_development(self, spec_values, run_values):
    existing = self.idempotency.get(run_values["idempotency_key"])
    if existing is not None:
      spec = self.specs[existing["spec_id"]]
      if (
        existing["run_kind"] != "DEVELOPMENT"
        or spec["spec_hash"] != spec_values["spec_hash"]
      ):
        raise ValueError("idempotency key is already bound to another payload")
      return spec, existing, True
    spec = dict(spec_values)
    run = dict(run_values)
    self.specs[spec["spec_id"]] = spec
    self.runs[run["run_id"]] = run
    self.idempotency[run["idempotency_key"]] = run
    return spec, run, False

  async def create_final_evaluation(self, spec_values, run_values):
    group = spec_values["experiment_group_hash"]
    count = sum(
      row["run_kind"] == "FINAL_EVALUATION"
      and row["experiment_group_hash"] == group
      for row in self.specs.values()
    )
    spec = dict(spec_values)
    spec["frozen_test_access_count"] = count + 1
    self.specs[spec["spec_id"]] = spec
    run = dict(run_values)
    self.runs[run["run_id"]] = run
    self.idempotency[run["idempotency_key"]] = run
    return spec, run, False

  async def get_run(self, run_id):
    return self.runs.get(run_id)

  async def get_spec(self, spec_id):
    return self.specs.get(spec_id)

  async def get_run_by_idempotency_key(self, key):
    return self.idempotency.get(key)

  async def count_final_evaluations(self, group_hash):
    return sum(
      row["run_kind"] == "FINAL_EVALUATION"
      and row["experiment_group_hash"] == group_hash
      for row in self.specs.values()
    )

  async def request_cancel(self, run_id, **_kwargs):
    self.runs[run_id]["status"] = "CANCELLED"
    return self.runs[run_id]

  async def comparison(self, run_ids):
    return {"comparable": True, "mismatch_fields": [], "runs": [self.runs[item] for item in run_ids]}


def _request(**overrides):
  value = {
    "dataset_version": "dataset-v1",
    "date_start": "2021-01-01",
    "date_end": "2025-12-31",
    "requested_backend": "CPU",
    "stock_codes": ["600000.SH", "000001.SZ"],
    "benchmark_code": "000300.SH",
    "created_by": "owner",
  }
  value.update(overrides)
  return value


@pytest.mark.asyncio
async def test_preview_has_fixed_non_overlapping_split_and_stable_fingerprint() -> None:
  port = Port()
  app = StockSelectionTrainingApplication(port)

  first = await app.preview(_request(created_by="owner-a"))
  second = await app.preview(_request(created_by="owner-b"))

  assert first["preview_fingerprint"] == second["preview_fingerprint"]
  split = first["spec_payload"]["split_spec"]
  assert (split["minimum_training_months"], split["calibration_months"]) == (30, 6)
  assert (split["validation_months"], split["frozen_test_months"]) == (1, 12)
  assert split["development_end"] < split["frozen_test_start"]
  assert first["coverage"]["strict_non_overlap"] is True
  assert first["resource_estimate"]["sample_count"] == DATASET["sample_count"]


@pytest.mark.asyncio
async def test_development_is_idempotent_and_final_locks_parent_coordinates() -> None:
  port = Port()
  app = StockSelectionTrainingApplication(port)
  preview = await app.preview(_request())
  request = _request(
    preview_fingerprint=preview["preview_fingerprint"],
    idempotency_key="development-1",
  )

  first = await app.start_development(request)
  retry = await app.start_development(request)
  assert first["run"]["run_id"] == retry["run"]["run_id"]
  assert retry["idempotent"] is True

  parent = port.runs[first["run"]["run_id"]]
  parent.update(
    {
      "status": "SUCCEEDED",
      "run_key": "research-parent",
      "artifact_manifest_sha256": "d" * 64,
    }
  )
  final = await app.start_final(
    {
      "parent_run_id": parent["run_id"],
      "idempotency_key": "final-1",
      "created_by": "owner",
    }
  )
  final_spec = final["spec"]
  assert final_spec["run_kind"] == "FINAL_EVALUATION"
  assert final_spec["spec_hash"] == first["spec"]["spec_hash"]
  assert final_spec["coordinate_hash"] == first["spec"]["coordinate_hash"]
  assert final_spec["frozen_test_access_count"] == 1
  assert final["run"]["parent_run_id"] == parent["run_id"]


@pytest.mark.asyncio
async def test_preview_fingerprint_and_gpu_required_fail_closed() -> None:
  port = Port()
  app = StockSelectionTrainingApplication(port)
  with pytest.raises(TrainingPreviewMismatch):
    await app.start_development(
      _request(
        preview_fingerprint="0" * 64,
        idempotency_key="development-bad-preview",
      )
    )

  with pytest.raises(TrainingBackendUnavailable):
    await StockSelectionTrainingApplication(
      Port(capability={"status": "GPU_UNAVAILABLE_BUILD"})
    ).preview(_request(requested_backend="GPU_REQUIRED"))


@pytest.mark.asyncio
async def test_incomplete_historical_universe_is_shadow_reason_not_submission_blocker() -> None:
  quality = dict(DATASET["quality_summary"])
  quality["coverage"] = {"historical_universe": {"complete": False}}
  dataset = {**DATASET, "quality_summary": quality}
  port = Port()
  port.dataset = dataset

  async def get_dataset(dataset_version: str):
    return port.dataset if dataset_version == dataset["dataset_version"] else None

  port.get_dataset = get_dataset
  preview = await StockSelectionTrainingApplication(port).preview(_request())
  assert "HISTORICAL_UNIVERSE_INCOMPLETE" in preview["shadow_reasons"]
  assert "DATA_COVERAGE_INCOMPLETE" not in preview["blockers"]
  assert preview["blockers"] == []
