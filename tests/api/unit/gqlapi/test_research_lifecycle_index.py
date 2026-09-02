from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from quantx_api import research_run_index as index
from quantx_api.research_artifacts import ResearchRunRecord


def _artifact(
  *,
  run_id: str,
  study_id: str,
  key: str,
  completed_at: datetime | None,
  status: str = "success",
) -> ResearchRunRecord:
  return ResearchRunRecord(
    key=key,
    run_id=run_id,
    study_id=study_id,
    version="v1",
    status=status,
    started_at=completed_at,
    completed_at=completed_at,
    event_count=3,
    elapsed_seconds=1.5,
    config_hash="a" * 64,
    has_metrics=status == "success",
    run_directory=None,
  )


def _training(
  *,
  run_id: str,
  status: str,
  completed_at: datetime | None,
  run_kind: str = "DEVELOPMENT",
  run_key: str | None = "training-key",
  manifest_hash: str | None = "b" * 64,
):
  return (
    SimpleNamespace(
      run_id=run_id,
      run_kind=run_kind,
      status=status,
      phase="PREFLIGHT",
      completed_units=2,
      total_units=4,
      requested_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
      started_at=completed_at,
      completed_at=completed_at,
      cancel_requested_at=None,
      run_key=run_key,
      artifact_manifest_sha256=manifest_hash,
      gate_summary={"conclusion": "BLOCKED", "registerable": False},
    ),
    SimpleNamespace(
      dataset_version="dataset-v1",
      requested_backend="CPU",
      resolved_backend="CPU",
    ),
  )


class _ArtifactStore:
  def __init__(self, records):
    self.records = records

  def list_runs_for_index(self):
    return self.records


class _SessionContext:
  async def __aenter__(self):
    return object()

  async def __aexit__(self, *_args):
    return False


@pytest.mark.asyncio
async def test_merge_deduplicates_and_applies_global_sort_and_page(monkeypatch):
  duplicate = _artifact(
    run_id="same-run",
    study_id="next-day-selection",
    key="a" * 64,
    completed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
  )
  artifact = _artifact(
    run_id="research-run",
    study_id="volume-shock",
    key="c" * 64,
    completed_at=datetime(2026, 1, 4, tzinfo=timezone.utc),
  )
  training = _training(
    run_id="same-run",
    status="RUNNING",
    completed_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
  )

  class _Repository:
    def __init__(self, _db):
      pass

    async def list_runs_for_index(self, **_kwargs):
      return [training]

    async def list_run_ids_for_index(self):
      return [training[0].run_id]

  monkeypatch.setattr(index, "ResearchArtifactStore", lambda: _ArtifactStore([duplicate, artifact]))
  monkeypatch.setattr(index, "StockSelectionTrainingRepository", _Repository)
  monkeypatch.setattr(index, "AsyncSessionLocal", _SessionContext)

  records = await index.list_research_lifecycle_runs(
    index.validate_research_lifecycle_query()
  )

  assert [record.id for record in records] == [
    "artifact:" + "c" * 64,
    "training:same-run",
  ]
  assert index.paginate_research_lifecycle_runs(records, limit=1, offset=1) == (
    [records[1]],
    2,
  )
  assert records[1].training is not None
  assert records[1].training.can_start_final is False


@pytest.mark.asyncio
async def test_dedupe_uses_unfiltered_db_identity_for_research_stage(monkeypatch):
  duplicate = _artifact(
    run_id="same-run",
    study_id="next-day-selection",
    key="a" * 64,
    completed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
  )

  class _Repository:
    def __init__(self, _db):
      pass

    async def list_runs_for_index(self, **_kwargs):
      return []

    async def list_run_ids_for_index(self):
      return ["same-run"]

  monkeypatch.setattr(index, "ResearchArtifactStore", lambda: _ArtifactStore([duplicate]))
  monkeypatch.setattr(index, "StockSelectionTrainingRepository", _Repository)
  monkeypatch.setattr(index, "AsyncSessionLocal", _SessionContext)

  query = index.validate_research_lifecycle_query(stages=["RESEARCH"])
  records = await index.list_research_lifecycle_runs(query)

  assert records == []


@pytest.mark.asyncio
async def test_graphql_connection_projects_type_specific_summary(monkeypatch, authorized_graphql_context):
  artifact = _artifact(
    run_id="research-run",
    study_id="volume-shock",
    key="c" * 64,
    completed_at=datetime(2026, 1, 4, tzinfo=timezone.utc),
  )
  training = _training(
    run_id="training-run",
    status="SUCCEEDED",
    completed_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
  )

  class _Repository:
    def __init__(self, _db):
      pass

    async def list_runs_for_index(self, **_kwargs):
      return [training]

    async def list_run_ids_for_index(self):
      return [training[0].run_id]

  monkeypatch.setattr(index, "ResearchArtifactStore", lambda: _ArtifactStore([artifact]))
  monkeypatch.setattr(index, "StockSelectionTrainingRepository", _Repository)
  monkeypatch.setattr(index, "AsyncSessionLocal", _SessionContext)

  from quantx_api.gqlapi.schema import schema

  result = await schema.execute(
    """
    {
      researchLifecycleRuns {
        total
        items {
          id runId studyId stage status target updatedAt
          artifact { key version eventCount hasMetrics }
          training {
            runKey datasetVersion requestedBackend resolvedBackend phase
            completedUnits totalUnits canStartFinal registerable
          }
        }
      }
    }
    """,
    context_value=authorized_graphql_context,
  )

  assert result.errors is None
  page = result.data["researchLifecycleRuns"]
  assert page["total"] == 2
  assert page["items"][0]["target"] == "RESEARCH_EVIDENCE"
  assert page["items"][0]["artifact"]["key"] == "c" * 64
  assert page["items"][0]["training"] is None
  assert page["items"][1]["target"] == "TRAINING_RUN"
  assert page["items"][1]["artifact"] is None
  assert page["items"][1]["training"]["requestedBackend"] == "CPU"
  assert page["items"][1]["training"]["canStartFinal"] is True


@pytest.mark.asyncio
async def test_filters_search_and_utc_date_are_applied_to_both_sources(monkeypatch):
  artifact = _artifact(
    run_id="artifact-run",
    study_id="volume-shock",
    key="d" * 64,
    completed_at=datetime(2026, 1, 2, 1, tzinfo=timezone.utc),
  )
  training = _training(
    run_id="training-run",
    status="SUCCEEDED",
    completed_at=datetime(2026, 1, 1, 23, tzinfo=timezone.utc),
  )

  class _Repository:
    def __init__(self, _db):
      pass

    async def list_runs_for_index(self, **_kwargs):
      return [training]

    async def list_run_ids_for_index(self):
      return [training[0].run_id]

  monkeypatch.setattr(index, "ResearchArtifactStore", lambda: _ArtifactStore([artifact]))
  monkeypatch.setattr(index, "StockSelectionTrainingRepository", _Repository)
  monkeypatch.setattr(index, "AsyncSessionLocal", _SessionContext)

  query = index.validate_research_lifecycle_query(
    statuses=["SUCCEEDED"],
    date_from=datetime(2026, 1, 1, tzinfo=timezone.utc).date(),
    date_to=datetime(2026, 1, 1, tzinfo=timezone.utc).date(),
    search=" TRAINING-RUN ",
  )
  records = await index.list_research_lifecycle_runs(query)

  assert [record.id for record in records] == ["training:training-run"]


def test_validation_bounds_and_training_gate_projection():
  with pytest.raises(ValueError, match="dateFrom"):
    index.validate_research_lifecycle_query(
      date_from=datetime(2026, 1, 2).date(),
      date_to=datetime(2026, 1, 1).date(),
    )
  with pytest.raises(ValueError, match="search"):
    index.validate_research_lifecycle_query(search="x" * 129)
  with pytest.raises(ValueError, match="studyId"):
    index.validate_research_lifecycle_query(study_id="../private")

  run, spec = _training(
    run_id="successful-development",
    status="SUCCEEDED",
    completed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
  )
  record = index._training_record(run, spec)
  assert record.training is not None
  assert record.training.can_start_final is True
  assert record.training.registerable is False
