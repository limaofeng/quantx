from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from quantx_contracts.training_bundle import BundleFile, TrainingBundle
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import RuntimeComponentHeartbeat
from quantx_infrastructure.models.stock_selection import (
  StockSelectionDatasetVersion,
  StockSelectionTrainingRun,
  StockSelectionTrainingSpec,
)
from quantx_infrastructure.repositories.stock_selection_training_repository import (
  StockSelectionTrainingRepository,
  TrainingRepositoryError,
  TrainingStateConflict,
  _safe_error_message,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

DATASET = {
  "dataset_version": "dataset-v1",
  "status": "CERTIFIED",
  "source_kind": "CERTIFIED_PANEL",
  "source_reference": "datasets/v1",
  "date_start": date(2021, 1, 1),
  "date_end": date(2025, 12, 31),
  "universe_spec": {},
  "indicator_version": "indicator-v1",
  "factor_set_version": "factor-v1",
  "factor_set_hash": "a" * 64,
  "label_version": "label-v1",
  "manifest_sha256": "b" * 64,
  "sample_count": 1000,
  "stock_count": 20,
  "trading_day_count": 500,
  "quality_summary": {},
}


def spec_values(spec_id: str = "spec-1") -> dict:
  return {
    "spec_id": spec_id,
    "dataset_version": "dataset-v1",
    "universe_spec": {"kind": "ORDINARY_A_SHARE", "benchmark_code": "000300.SH"},
    "run_kind": "DEVELOPMENT",
    "split_spec": {},
    "model_spec": {},
    "evaluation_spec": {},
    "requested_backend": "CPU",
    "resolved_backend": "CPU",
    "random_seed": 1,
    "worker_batch_size": 10,
    "note": "test",
    "spec_hash": "c" * 64,
    "environment_requirement_hash": "d" * 64,
    "coordinate_hash": "e" * 64,
    "experiment_group_hash": "f" * 64,
    "frozen_test_access_count": 0,
    "created_by": "owner",
  }


def run_values(spec_id: str, run_id: str, key: str) -> dict:
  return {
    "run_id": run_id,
    "spec_id": spec_id,
    "run_kind": "DEVELOPMENT",
    "parent_run_id": None,
    "status": "QUEUED",
    "phase": "PREFLIGHT",
    "completed_units": 0,
    "total_units": 2,
    "idempotency_key": key,
    "state_version": 1,
  }


def test_error_message_redaction_removes_complete_absolute_paths() -> None:
  message = _safe_error_message(
    "failed F:\\Private Folder\\secret.parquet; "
    "\\\\server\\Private Folder\\x.parquet; /private folder/file.parquet"
  )
  assert message.count("[PATH]") == 1
  assert "Private Folder" not in message
  assert "secret.parquet" not in message


@pytest.fixture
async def session_factory():
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync: Base.metadata.create_all(
        sync,
        tables=[
          RuntimeComponentHeartbeat.__table__,
          StockSelectionDatasetVersion.__table__,
          StockSelectionTrainingSpec.__table__,
          StockSelectionTrainingRun.__table__,
        ],
      )
    )
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  try:
    yield sessions
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_capability_heartbeat_persists_naive_utc_and_returns_aware_utc() -> None:
  class Session:
    persisted_updated_at = None
    expunged = False

    async def get(self, _model, _key):
      return None

    def add(self, row):
      self.persisted_updated_at = row.updated_at

    async def commit(self):
      return None

    async def refresh(self, _row):
      return None

    def expunge(self, _row):
      self.expunged = True

  session = Session()
  now = datetime(2026, 9, 3, 2, 30, tzinfo=timezone.utc)

  heartbeat = await StockSelectionTrainingRepository(session).upsert_capability_heartbeat(
    status="GPU_UNAVAILABLE_BUILD",
    details={"gpu_status": "GPU_UNAVAILABLE_BUILD"},
    now=now,
  )

  assert session.persisted_updated_at == now.replace(tzinfo=None)
  assert session.expunged is True
  assert heartbeat.updated_at == now


@pytest.mark.asyncio
async def test_certification_and_queue_insertion_are_idempotent(session_factory) -> None:
  async with session_factory() as db:
    repository = StockSelectionTrainingRepository(db)
    first = await repository.certify_dataset(DATASET)
    retry = await repository.certify_dataset(dict(DATASET))
    assert first.dataset_version == retry.dataset_version
    with pytest.raises(TrainingRepositoryError, match="immutable evidence"):
      changed = dict(DATASET)
      changed["manifest_sha256"] = "0" * 64
      await repository.certify_dataset(changed)

    spec = await repository.create_spec(spec_values())
    run = await repository.create_run(run_values(spec.spec_id, "run-1", "idem-1"))
    assert await repository.create_run(run_values(spec.spec_id, "other", "idem-1")) == run


@pytest.mark.asyncio
async def test_index_joins_specs_and_applies_lifecycle_filters(session_factory) -> None:
  async with session_factory() as db:
    repository = StockSelectionTrainingRepository(db)
    await repository.certify_dataset(DATASET)
    spec = await repository.create_spec(spec_values("index-spec"))
    run = await repository.create_run(run_values(spec.spec_id, "index-run", "index-idem"))
    run.status = "SUCCEEDED"
    run.run_key = "index-run-key"
    run.requested_at = datetime(2026, 2, 2, 23, 59, tzinfo=timezone.utc)
    run.started_at = datetime(2026, 2, 3, 0, 1, tzinfo=timezone.utc)
    run.completed_at = datetime(2026, 2, 3, 12, 0, tzinfo=timezone.utc)
    await db.commit()

    rows = await repository.list_runs_for_index(
      statuses=["SUCCEEDED"],
      run_kinds=["DEVELOPMENT"],
      date_from=date(2026, 2, 3),
      date_to=date(2026, 2, 3),
      search="DATASET-V1",
    )

    assert len(rows) == 1
    indexed_run, indexed_spec = rows[0]
    assert indexed_run.run_id == "index-run"
    assert indexed_spec.spec_id == "index-spec"
    assert indexed_spec.dataset_version == "dataset-v1"


@pytest.mark.asyncio
async def test_development_creation_is_atomic_and_idempotent(session_factory) -> None:
  async with session_factory() as db:
    repository = StockSelectionTrainingRepository(db)
    await repository.certify_dataset(DATASET)
    first_spec, first_run, idempotent = await repository.create_development(
      spec_values("development-spec-a"),
      run_values("development-spec-a", "development-run-a", "development-idem"),
    )
    assert idempotent is False

    retry_spec, retry_run, idempotent = await repository.create_development(
      spec_values("development-spec-b"),
      run_values("development-spec-b", "development-run-b", "development-idem"),
    )
    assert idempotent is True
    assert retry_spec.spec_id == first_spec.spec_id
    assert retry_run.run_id == first_run.run_id
    assert len(await repository.list_specs()) == 1
    assert await repository.count_runs(run_kind="DEVELOPMENT") == 1


@pytest.mark.asyncio
async def test_development_creation_concurrent_idempotency_has_no_orphan_spec(tmp_path) -> None:
  engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'training.db'}")
  tables = [
    RuntimeComponentHeartbeat.__table__,
    StockSelectionDatasetVersion.__table__,
    StockSelectionTrainingSpec.__table__,
    StockSelectionTrainingRun.__table__,
  ]
  async with engine.begin() as connection:
    await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
  sessions = async_sessionmaker(engine, expire_on_commit=False)
  try:
    async with sessions() as db:
      await StockSelectionTrainingRepository(db).certify_dataset(DATASET)

    async def create(suffix: str):
      async with sessions() as db:
        return await StockSelectionTrainingRepository(db).create_development(
          spec_values(f"development-spec-{suffix}"),
          run_values(
            f"development-spec-{suffix}",
            f"development-run-{suffix}",
            "development-concurrent-idem",
          ),
        )

    results = await asyncio.gather(create("a"), create("b"))
    assert sum(not result[2] for result in results) == 1
    assert sum(result[2] for result in results) == 1
    async with sessions() as db:
      repository = StockSelectionTrainingRepository(db)
      assert len(await repository.list_specs()) == 1
      assert await repository.count_runs(run_kind="DEVELOPMENT") == 1
  finally:
    await engine.dispose()


@pytest.mark.asyncio
async def test_final_creation_serializes_frozen_test_access_and_is_idempotent(session_factory) -> None:
  async with session_factory() as db:
    repository = StockSelectionTrainingRepository(db)
    await repository.certify_dataset(DATASET)
    development = await repository.create_spec(spec_values())
    await repository.create_run(run_values(development.spec_id, "development-run", "development-idem"))
    final_spec_values = spec_values("final-spec-1")
    final_spec_values.update({"run_kind": "FINAL_EVALUATION", "frozen_test_access_count": 0})
    final_run_values = run_values("final-spec-1", "final-run-1", "final-idem-1")
    final_run_values.update(
      {"run_kind": "FINAL_EVALUATION", "parent_run_id": "development-run"}
    )
    # The repository coordinates on the development coordinate row.  The
    # parent run itself is not needed for this persistence-level counter test.
    first_spec, first_run, idempotent = await repository.create_final_evaluation(
      final_spec_values, final_run_values
    )
    assert idempotent is False
    assert first_spec.frozen_test_access_count == 1
    assert first_spec.spec_hash == development.spec_hash
    assert first_spec.coordinate_hash == development.coordinate_hash
    retry_spec, retry_run, idempotent = await repository.create_final_evaluation(
      final_spec_values, final_run_values
    )
    assert idempotent is True
    assert retry_spec.spec_id == first_spec.spec_id
    assert retry_run.run_id == first_run.run_id

    second_spec_values = spec_values("final-spec-2")
    second_spec_values.update({"run_kind": "FINAL_EVALUATION", "frozen_test_access_count": 0})
    second_run_values = run_values("final-spec-2", "final-run-2", "final-idem-2")
    second_run_values.update(
      {"run_kind": "FINAL_EVALUATION", "parent_run_id": "development-run"}
    )
    second_spec, _second_run, idempotent = await repository.create_final_evaluation(
      second_spec_values, second_run_values
    )
    assert idempotent is False
    assert second_spec.frozen_test_access_count == 2
    assert development.experiment_group_hash == second_spec.experiment_group_hash


@pytest.mark.asyncio
async def test_claim_progress_terminal_and_optimistic_lock(session_factory) -> None:
  now = datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc)
  async with session_factory() as db:
    repository = StockSelectionTrainingRepository(db)
    await repository.certify_dataset(DATASET)
    spec = await repository.create_spec(spec_values())
    await repository.create_run(run_values(spec.spec_id, "run-1", "idem-1"))
    await repository.create_run(run_values(spec.spec_id, "run-2", "idem-2"))
    claimed = await repository.claim_next_queued("prefect-1", now)
    assert claimed is not None
    assert claimed.status == "RUNNING"
    assert await repository.claim_next_queued("prefect-2", now) is None
    with pytest.raises(TrainingStateConflict):
      await repository.update_progress(
        claimed.run_id,
        expected_flow_run_id="prefect-1",
        phase="DATASET_BUILD",
        completed_units=1,
        total_units=2,
        expected_state_version=1,
      )
    progressed = await repository.update_progress(
      claimed.run_id,
      expected_flow_run_id="prefect-1",
      phase="DATASET_BUILD",
      completed_units=1,
      total_units=2,
      expected_state_version=claimed.state_version,
    )
    with pytest.raises(TrainingRepositoryError, match="backwards"):
      await repository.update_progress(
        claimed.run_id,
        expected_flow_run_id="prefect-1",
        phase="PREFLIGHT",
        completed_units=0,
        total_units=2,
      )
    completed = await repository.complete_run(
      claimed.run_id,
      expected_flow_run_id="prefect-1",
      run_key="research-1",
      artifact_manifest_sha256="1" * 64,
      expected_state_version=progressed.state_version,
    )
    assert completed.status == "SUCCEEDED"
    assert await repository.complete_run(
      claimed.run_id,
      expected_flow_run_id="prefect-1",
      run_key="research-1",
      artifact_manifest_sha256="1" * 64,
    ) == completed


@pytest.mark.asyncio
async def test_cancel_queued_and_running_runs_do_not_publish_artifacts(session_factory) -> None:
  async with session_factory() as db:
    repository = StockSelectionTrainingRepository(db)
    await repository.certify_dataset(DATASET)
    spec = await repository.create_spec(spec_values())
    queued = await repository.create_run(run_values(spec.spec_id, "run-queued", "idem-q"))
    cancelled = await repository.request_cancel(
      queued.run_id,
      expected_state_version=queued.state_version,
      idempotency_key="cancel-q",
    )
    assert cancelled.status == "CANCELLED"
    assert cancelled.artifact_manifest_sha256 is None
    running = await repository.create_run(run_values(spec.spec_id, "run-running", "idem-r"))
    running = await repository.claim_next_queued("prefect", datetime.now(timezone.utc))
    requested = await repository.request_cancel(
      running.run_id,
      expected_state_version=running.state_version,
      idempotency_key="cancel-r",
    )
    assert requested.status == "RUNNING"
    with pytest.raises(TrainingRepositoryError, match="different cancellation"):
      await repository.request_cancel(
        running.run_id,
        expected_state_version=requested.state_version,
        idempotency_key="cancel-r-different",
      )
    terminal = await repository.mark_cancelled(running.run_id, expected_flow_run_id="prefect")
    assert terminal.status == "CANCELLED"
    with pytest.raises(TrainingRepositoryError):
      await repository.complete_run(
        running.run_id,
        expected_flow_run_id="prefect",
        run_key="should-not-publish",
        artifact_manifest_sha256="2" * 64,
      )


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["update_progress", "complete_run", "fail_run", "mark_cancelled", "heartbeat_execution", "record_artifact_bundle"])
async def test_old_executor_cannot_write_after_persisted_ownership_changes(session_factory, operation):
  from sqlalchemy import update

  async with session_factory() as db:
    repository = StockSelectionTrainingRepository(db)
    await repository.certify_dataset(DATASET)
    spec = await repository.create_spec(spec_values())
    await repository.create_run(run_values(spec.spec_id, "owned-run", "owned-key"))
    cached = await repository.claim_next_queued("old-executor")
    assert cached.prefect_flow_run_id == "old-executor"
    async with session_factory() as second:
      await second.execute(update(StockSelectionTrainingRun).where(
        StockSelectionTrainingRun.run_id == "owned-run"
      ).values(prefect_flow_run_id="new-executor", state_version=cached.state_version + 1))
      await second.commit()
    payloads = {
      "update_progress": dict(phase="DATASET_BUILD", completed_units=1, total_units=2),
      "complete_run": dict(run_key="result", artifact_manifest_sha256="a" * 64),
      "fail_run": dict(error_code="STALE_FAILURE", error_message="old executor"),
      "mark_cancelled": {},
      "heartbeat_execution": {},
      "record_artifact_bundle": {"bundle": TrainingBundle(schema_version=1, kind="RESULT", source_id="owned-run", files=[BundleFile(path="manifest.json", size=10, sha256="a" * 64)])},
    }
    with pytest.raises(TrainingStateConflict, match="ownership lost"):
      await getattr(repository, operation)("owned-run", expected_flow_run_id="old-executor", **payloads[operation])
    current = await repository.get_run("owned-run")
    assert current.prefect_flow_run_id == "new-executor"
    assert current.status == "RUNNING"
    assert current.artifact_manifest_sha256 is None


@pytest.mark.asyncio
async def test_execution_heartbeat_is_persisted_without_changing_cancel_version(session_factory):
  now = datetime(2026, 9, 9, 1, 0, tzinfo=timezone.utc)
  async with session_factory() as db:
    repository = StockSelectionTrainingRepository(db)
    await repository.certify_dataset(DATASET)
    spec = await repository.create_spec(spec_values())
    await repository.create_run(run_values(spec.spec_id, "heartbeat-run", "heartbeat-key"))
    row = await repository.claim_next_queued("executor", now)
    version = row.state_version
    assert row.execution_heartbeat_at == now
    await repository.heartbeat_execution(
      row.run_id, expected_flow_run_id="executor", now=now + timedelta(seconds=10)
    )
    # A wall-clock adjustment cannot move persisted liveness backwards.
    await repository.heartbeat_execution(
      row.run_id, expected_flow_run_id="executor", now=now - timedelta(seconds=10)
    )
    async with session_factory() as observer:
      stored = await StockSelectionTrainingRepository(observer).get_run(row.run_id)
      assert stored.execution_heartbeat_at == now + timedelta(seconds=10)
      assert stored.state_version == version
    cancelled = await repository.request_cancel(
      row.run_id, expected_state_version=version, idempotency_key="cancel-heartbeat"
    )
    assert cancelled.cancel_requested_at is not None
    await repository.mark_cancelled(row.run_id, expected_flow_run_id="executor")
    with pytest.raises(TrainingStateConflict, match="no longer running"):
      await repository.heartbeat_execution(row.run_id, expected_flow_run_id="executor")


@pytest.mark.asyncio
async def test_published_bundle_is_immutable_and_fences_success_manifest(session_factory):
  async with session_factory() as db:
    repository = StockSelectionTrainingRepository(db)
    await repository.certify_dataset(DATASET)
    spec = await repository.create_spec(spec_values())
    await repository.create_run(run_values(spec.spec_id, "published-run", "published-key"))
    row = await repository.claim_next_queued("executor")
    bundle = TrainingBundle(schema_version=1, kind="RESULT", source_id=row.run_id, files=[BundleFile(path="manifest.json", size=10, sha256="a" * 64)])
    with pytest.raises(TrainingStateConflict):
      await repository.record_artifact_bundle(row.run_id, expected_flow_run_id="stale", bundle=bundle)
    await repository.record_artifact_bundle(row.run_id, expected_flow_run_id="executor", bundle=bundle)
    changed = bundle.model_copy(update={"files": (BundleFile(path="manifest.json", size=10, sha256="b" * 64),)})
    with pytest.raises(TrainingStateConflict, match="inventory conflicts"):
      await repository.record_artifact_bundle(row.run_id, expected_flow_run_id="executor", bundle=changed)
    with pytest.raises(TrainingStateConflict, match="manifest differs"):
      await repository.complete_run(row.run_id, expected_flow_run_id="executor", run_key="result", artifact_manifest_sha256="b" * 64)
    await repository.complete_run(row.run_id, expected_flow_run_id="executor", run_key="result", artifact_manifest_sha256="a" * 64)
    await repository.record_artifact_bundle(row.run_id, expected_flow_run_id="executor", bundle=bundle)
    assert row.status == "SUCCEEDED"
    assert TrainingBundle.model_validate(row.artifact_bundle).bundle_id == bundle.bundle_id


@pytest.mark.asyncio
async def test_poll_refreshes_external_cancellation_instead_of_session_cache(session_factory):
  async with session_factory() as db:
    repository = StockSelectionTrainingRepository(db)
    await repository.certify_dataset(DATASET)
    spec = await repository.create_spec(spec_values())
    await repository.create_run(run_values(spec.spec_id, "cancel-observed", "cancel-key"))
    cached = await repository.claim_next_queued("executor")
    assert cached.cancel_requested_at is None
    async with session_factory() as second:
      await StockSelectionTrainingRepository(second).request_cancel(
        "cancel-observed", idempotency_key="cancel", expected_state_version=cached.state_version
      )
    assert (await repository.get_run("cancel-observed")).cancel_requested_at is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["", "   ", "x" * 129])
async def test_claim_rejects_missing_or_truncated_execution_identity(session_factory, identity):
  async with session_factory() as db:
    with pytest.raises(TrainingRepositoryError, match="execution identity"):
      await StockSelectionTrainingRepository(db).claim_next_queued(identity)
