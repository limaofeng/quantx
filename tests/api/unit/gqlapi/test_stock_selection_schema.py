from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from quantx_api.auth.principal import Principal
from quantx_api.gqlapi.operation_policy import operation_policy
from quantx_api.gqlapi.schema import schema
from quantx_api.gqlapi.schemas import stock_selection_schema
from quantx_api.gqlapi.schemas.stock_selection_schema import (
  StockSelectionQuery,
  _resolve_candidate_runs,
  _validate_candidate_input,
)
from quantx_api.gqlapi.schemas.stock_selection_schema import (
  _safe_error as _safe_graphql_error,
)
from quantx_api.gqlapi.types.stock_selection_types import (
  StockProbabilityCandidateInput,
)
from quantx_api.stock_selection_model_service import (
  StockSelectionModelService,
  _safe_error,
  _stable_run_key,
)


def _training_info() -> SimpleNamespace:
  return SimpleNamespace(
    context={
      "principal": Principal(
        user_id="user-1",
        username="operator",
        display_name="Operator",
        device_session_id="session-1",
        access_token_expires_at=(
          datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5)
        ),
        permissions=frozenset({"market:read"}),
        authorized_account_ids=(),
      )
    }
  )


class _SessionContext:
  async def __aenter__(self):
    return object()

  async def __aexit__(self, exc_type, exc, traceback):
    return False


def test_probability_and_indicator_contracts_are_atomic() -> None:
  graphql = schema.as_str()

  assert "stockProbabilityCandidates" in graphql
  assert "stockSelectionModels" in graphql
  assert "registerStockSelectionModel" in graphql
  assert "setStockSelectionModelStage" in graphql
  assert "stockIndicatorCatalog" in graphql
  assert "indicatorConditions" in graphql
  assert "stockIndicatorReportMatches" in graphql
  assert "candidateRuleVersion" in graphql
  assert "factorSnapshotSha256" in graphql
  assert "selectionMetrics" in graphql
  assert "stockFactorCatalog" not in graphql
  assert "factorConditions" not in graphql
  assert "stockFactorReportMatches" not in graphql


def test_training_workbench_contract_is_typed_and_web_only() -> None:
  graphql = schema.as_str()
  for field in (
    "stockSelectionTrainerStatus",
    "stockSelectionTrainingCapabilities",
    "stockSelectionDatasetVersions",
    "previewStockSelectionTraining",
    "stockSelectionTrainingRuns",
    "stockSelectionTrainingRun",
    "stockSelectionTrainingComparison",
    "startStockSelectionDevelopmentTraining",
    "startStockSelectionFinalEvaluation",
    "cancelStockSelectionTrainingRun",
  ):
    assert field in graphql
  assert "input StockSelectionTrainingInput" in graphql
  assert "sourceReference" not in graphql
  assert "artifactDirectory" not in graphql
  training_query_policy = operation_policy("Query", "stockSelectionTrainingRuns")
  assert training_query_policy.audiences == ("web",)
  assert training_query_policy.stability == "web-internal"
  training_mutation_policy = operation_policy(
    "Mutation", "startStockSelectionDevelopmentTraining"
  )
  assert training_mutation_policy.audiences == ("web",)
  assert training_mutation_policy.risk == "NON_TRADING_WRITE"
  assert operation_policy("Mutation", "registerStockSelectionModel").risk == "ADMIN"
  assert (
    operation_policy("Mutation", "deleteExitPlanHistory").risk == "NON_TRADING_WRITE"
  )


@pytest.mark.asyncio
async def test_trainer_query_projects_service_separately_from_execution_capability(monkeypatch):
  from quantx_contracts.trainer_status import TrainerRuntimeStatus

  class Repository:
    def __init__(self, db):
      pass

    async def read(self):
      return {**TrainerRuntimeStatus(service="ALIVE", admission="DRAINING",
              resource_reason="TRADING_OR_POST_CLOSE_CRITICAL_WINDOW").model_dump(),
              "fresh": True, "updated_at": datetime.now(timezone.utc)}

  monkeypatch.setattr(stock_selection_schema, "AsyncSessionLocal", _SessionContext)
  monkeypatch.setattr(stock_selection_schema, "TrainerStatusRepository", Repository)
  result = await StockSelectionQuery().stock_selection_trainer_status(_training_info())
  assert result.service == "ALIVE" and result.fresh is True
  assert result.admission == "DRAINING"
  assert result.training.state == "UNKNOWN" and result.training.reason is None
  assert operation_policy("Query", "stockSelectionTrainerStatus").audiences == ("web",)


@pytest.mark.asyncio
async def test_training_capabilities_query_uses_canonical_repository_method(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  calls: list[str] = []

  class CanonicalTrainingRepository:
    def __init__(self, _db) -> None:
      pass

    async def get_capability(self):
      calls.append("get_capability")
      return {
        "status": "GPU_AVAILABLE",
        "gpu_status": "GPU_AVAILABLE",
        "fresh": True,
        "cpu_available": True,
        "updated_at": "2026-09-02T06:00:00+00:00",
        "available_memory_mib": 4096,
        "environment_requirement_hash": "a" * 64,
        "qualification": {"status": "GPU_AVAILABLE"},
        "environment_summary": {"platform": "Windows"},
      }

  monkeypatch.setattr(stock_selection_schema, "AsyncSessionLocal", _SessionContext)
  monkeypatch.setattr(
    stock_selection_schema,
    "StockSelectionTrainingRepository",
    CanonicalTrainingRepository,
  )

  result = await StockSelectionQuery().stock_selection_training_capabilities(
    _training_info()
  )

  assert calls == ["get_capability"]
  assert result.cpu_available is True
  assert result.gpu_status.value == "GPU_AVAILABLE"
  assert result.fresh is True
  assert result.available_memory_mib == 4096
  assert result.environment_requirement_hash == "a" * 64
  assert result.qualification == {"status": "GPU_AVAILABLE"}


@pytest.mark.asyncio
async def test_training_dataset_versions_query_uses_canonical_repository_method(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  calls: list[tuple[str, int, int]] = []

  class CanonicalTrainingRepository:
    def __init__(self, _db) -> None:
      pass

    async def list_datasets(self, *, limit: int, offset: int):
      calls.append(("list_datasets", limit, offset))
      return [
        {
          "dataset_version": "certified-v1",
          "status": "CERTIFIED",
          "source_kind": "verified-panel",
          "date_start": "2022-01-01",
          "date_end": "2025-12-31",
          "universe_spec": {"kind": "CERTIFIED_INDEX", "index_code": "000300.SH"},
          "indicator_version": "indicator-v1",
          "factor_set_version": "factor-v1",
          "factor_set_hash": "b" * 64,
          "label_version": "label-v1",
          "manifest_sha256": "c" * 64,
          "sample_count": 1000,
          "stock_count": 100,
          "trading_day_count": 240,
          "quality_summary": {"coverage": {"complete": True}},
          "created_at": "2026-09-02T06:00:00+00:00",
        }
      ]

  monkeypatch.setattr(stock_selection_schema, "AsyncSessionLocal", _SessionContext)
  monkeypatch.setattr(
    stock_selection_schema,
    "StockSelectionTrainingRepository",
    CanonicalTrainingRepository,
  )

  result = await StockSelectionQuery().stock_selection_dataset_versions(
    _training_info(), limit=7, offset=3
  )

  assert calls == [("list_datasets", 7, 3)]
  assert len(result) == 1
  assert result[0].dataset_version == "certified-v1"
  assert result[0].manifest_sha256 == "c" * 64
  assert result[0].universe_spec == {
    "kind": "CERTIFIED_INDEX",
    "index_code": "000300.SH",
  }
  assert result[0].sample_count == 1000


@pytest.mark.parametrize(
  "message",
  (
    r"读取失败: C:\Users\limao\private source\manifest.json",
    r"读取失败: \\server\share\private source\manifest.json",
    "读取失败: /srv/private source/manifest.json",
  ),
)
def test_registration_errors_redact_complete_absolute_paths(message: str) -> None:
  safe = _safe_error(ValueError(message))

  assert safe == "读取失败: <path>"
  assert "private" not in safe
  assert "manifest" not in safe
  assert _safe_graphql_error(ValueError(message)) == "读取失败: <path>"


def test_probability_candidate_input_is_bounded() -> None:
  _validate_candidate_input(
    StockProbabilityCandidateInput(
      as_of=date(2026, 9, 1), minimum_probability=0.6, limit=50, offset=0
    )
  )

  with pytest.raises(ValueError, match="minimumProbability"):
    _validate_candidate_input(StockProbabilityCandidateInput(minimum_probability=1.1))
  with pytest.raises(ValueError, match="limit"):
    _validate_candidate_input(StockProbabilityCandidateInput(limit=201))


def test_latest_failed_attempt_never_falls_back_to_old_candidates() -> None:
  runs = [
    SimpleNamespace(
      model_version="model-a",
      model_stage="ACTIVE",
      as_of_date=date(2026, 9, 1),
      started_at=datetime(2026, 9, 1, 16, tzinfo=timezone.utc),
      status="FAILED",
    ),
    SimpleNamespace(
      model_version="model-a",
      model_stage="ACTIVE",
      as_of_date=date(2026, 9, 1),
      started_at=datetime(2026, 9, 1, 15, tzinfo=timezone.utc),
      status="SUCCESS",
    ),
    SimpleNamespace(
      model_version="model-a",
      model_stage="ACTIVE",
      as_of_date=date(2026, 8, 31),
      started_at=datetime(2026, 8, 31, 15, tzinfo=timezone.utc),
      status="SUCCESS",
    ),
  ]

  resolved, selected, warnings = _resolve_candidate_runs(
    runs,
    current_stages={"model-a": "ACTIVE"},
    requested_date=None,
  )

  assert resolved == date(2026, 9, 1)
  assert selected == []
  assert warnings == ["模型 model-a 最新运行状态为 FAILED，未复用旧候选"]


def test_old_shadow_run_is_not_reused_after_model_activation() -> None:
  runs = [
    SimpleNamespace(
      model_version="model-a",
      model_stage="SHADOW",
      as_of_date=date(2026, 9, 1),
      started_at=datetime(2026, 9, 1, 15, tzinfo=timezone.utc),
      status="SUCCESS",
    )
  ]

  resolved, selected, warnings = _resolve_candidate_runs(
    runs,
    current_stages={"model-a": "ACTIVE"},
    requested_date=None,
  )

  assert resolved is None
  assert selected == []
  assert warnings == ["尚无当前模型阶段的概率推理运行"]


@pytest.mark.asyncio
async def test_registration_uses_final_db_run_key_and_sanitizes_projection(
  tmp_path, monkeypatch
) -> None:
  run_id = "final-run-20260902"
  run_key = _stable_run_key(study_id="next-day-selection", version="v1", run_id=run_id)
  (tmp_path / run_id).mkdir()
  row = SimpleNamespace(
    run_id=run_id,
    run_key=run_key,
    spec_id="final-spec",
    parent_run_id="development-run",
    run_kind="FINAL_EVALUATION",
    status="SUCCEEDED",
    artifact_manifest_sha256="c" * 64,
  )
  parent = SimpleNamespace(run_kind="DEVELOPMENT", spec_id="development-spec", status="SUCCEEDED")

  class TrainingRepository:
    async def get_run_by_run_key(self, value):
      return row if value == run_key else None

    async def get_spec(self, value):
      if value == "final-spec":
        return SimpleNamespace(
          run_kind="FINAL_EVALUATION",
          spec_hash="a" * 64,
          coordinate_hash="b" * 64,
          requested_backend="CPU",
          resolved_backend="CPU",
        )
      if value == "development-spec":
        return SimpleNamespace(run_kind="DEVELOPMENT", coordinate_hash="b" * 64)
      return None

    async def get_run(self, value):
      return (
        parent
        if value == "development-run"
        else None
      )

  class ModelRepository:
    payload = None

    async def register_model(self, values):
      self.payload = values
      return values

  gates = {
    "historical_universe_complete": True,
    "effect_gate_passed": True,
  }
  manifest = {
    "study_id": "next-day-selection",
    "version": "v1",
    "run_id": run_id,
    "run_kind": "FINAL_EVALUATION",
    "status": "SUCCEEDED",
    "registerable": True,
    "model_version": "next-day-selection-v1-20260902",
    "selected_family": "LOGISTIC",
    "indicator_version": "daily-indicator-v1",
    "factor_set_version": "next-day-factor-v1",
    "factor_set_hash": "d" * 64,
    "label_version": "next-day-positive-v1",
    "calibrator_version": "platt-v1",
    "training_start": "2022-01-01",
    "training_end": "2025-12-31",
    "calibration_start": "2025-01-01",
    "calibration_end": "2025-06-30",
    "test_start": "2025-07-01",
    "test_end": "2025-12-31",
    "config_hash": "e" * 64,
    "data_fingerprint": "f" * 64,
    "spec_hash": "a" * 64,
    "coordinate_hash": "b" * 64,
    "requested_backend": "CPU",
    "resolved_backend": "CPU",
    "gates": gates,
    "source_reference": "C:/private/source.csv",
    "parent_run_id": "development-run",
  }
  bundle = SimpleNamespace(
    directory=tmp_path / run_id,
    manifest_sha256="c" * 64,
    manifest=manifest,
    metrics={
      "gates": gates,
      "conclusion": "ACTIVE_ELIGIBLE",
      "registerable": True,
      "source_reference": "C:/private/metrics.json",
      "parent_development": {"run_id": "development-run"},
    },
    data_quality={"source_reference": "C:/private/data.csv", "coverage": 1},
  )
  monkeypatch.setattr(
    "quantx_api.stock_selection_model_service.load_selection_artifact",
    lambda *_args, **_kwargs: bundle,
  )
  model_repository = ModelRepository()

  result = await StockSelectionModelService(
    model_repository, TrainingRepository(), runs_root=tmp_path
  ).register(run_key)

  assert result is model_repository.payload
  assert model_repository.payload["run_key"] == run_key
  assert model_repository.payload["evidence"]["conclusion"] == "ACTIVE_ELIGIBLE"
  assert "source_reference" not in model_repository.payload["metrics"]
  assert "source_reference" not in model_repository.payload["evidence"]["data_quality"]

  for parent_status in ("RUNNING", "FAILED", "CANCELLED"):
    parent.status = parent_status
    with pytest.raises(ValueError, match="父运行无效"):
      await StockSelectionModelService(model_repository, TrainingRepository(), runs_root=tmp_path).register(run_key)
  parent.status = "SUCCEEDED"
  for container, field in ((manifest, "parent_run_id"), (bundle.metrics["parent_development"], "run_id")):
    original = container[field]
    for invalid in (None, "another-development-run"):
      container[field] = invalid
      with pytest.raises(ValueError, match="父运行不一致"):
        await StockSelectionModelService(model_repository, TrainingRepository(), runs_root=tmp_path).register(run_key)
    container[field] = original

  for field, invalid_value in (
    ("spec_hash", "f" * 64),
    ("coordinate_hash", "f" * 64),
    ("requested_backend", "GPU_REQUIRED"),
    ("resolved_backend", "LIGHTGBM_OPENCL_GPU"),
  ):
    original_value = manifest[field]
    manifest[field] = invalid_value
    with pytest.raises(ValueError, match=f"{field}"):
      await StockSelectionModelService(
        model_repository, TrainingRepository(), runs_root=tmp_path
      ).register(run_key)
    manifest[field] = original_value


@pytest.mark.asyncio
async def test_registration_rejects_development_row_before_loading_artifacts(
  tmp_path, monkeypatch
) -> None:
  run_id = "development-run-20260902"
  run_key = _stable_run_key(study_id="next-day-selection", version="v1", run_id=run_id)
  row = SimpleNamespace(
    run_id=run_id,
    run_key=run_key,
    run_kind="DEVELOPMENT",
    status="SUCCEEDED",
    artifact_manifest_sha256="c" * 64,
  )

  class TrainingRepository:
    async def get_run_by_run_key(self, value):
      return row if value == run_key else None

  called = False

  def load_bundle(*_args, **_kwargs):
    nonlocal called
    called = True
    return None

  monkeypatch.setattr(
    "quantx_api.stock_selection_model_service.load_selection_artifact", load_bundle
  )
  with pytest.raises(ValueError, match="FINAL_EVALUATION"):
    await StockSelectionModelService(
      SimpleNamespace(), TrainingRepository(), runs_root=tmp_path
    ).register(run_key)
  assert called is False
