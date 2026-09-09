from datetime import date, timedelta

import pytest
from quantx_contracts.research_preparation import ResearchPreparationConfig
from quantx_infrastructure.models.research_preparation import (
  ResearchPreparationJob,
  ResearchPreparationSettings,
)
from quantx_infrastructure.services.research_preparation import (
  ResearchPreparationRepository,
  download_preview,
  evidence_file,
  list_evidence_files,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

CONFIG = {
  "date_start": "2021-07-29",
  "date_end": "2025-07-29",
  "stock_codes": ["600000.SH"],
}


@pytest.mark.parametrize(
  "patch",
  [
    {"date_end": "2020-01-01"},
    {"stock_codes": ["600000.SH", "600000.SH"]},
    {"stock_codes": ["bad"]},
    {"st_file": "../history.csv"},
    {"st_file": "C:/secret.csv"},
    {"minimum_listing_days": 1},
  ],
)
def test_invalid_config_rejected(patch):
  with pytest.raises(ValueError):
    ResearchPreparationConfig.model_validate({**CONFIG, **patch})


def test_evidence_directory_rejects_escape_and_links(tmp_path, monkeypatch):
  monkeypatch.setenv("QUANTX_RESEARCH_EVIDENCE_ROOT", str(tmp_path))
  (tmp_path / "history.csv").write_text("event_date,stock_code,is_st\n")
  assert list_evidence_files() == ["history.csv"]
  assert evidence_file("history.csv").is_file()
  for value in ["../history.csv", "C:/history.csv", "missing.csv"]:
    with pytest.raises(ValueError):
      evidence_file(value)
  monkeypatch.setattr(
    type(tmp_path), "is_symlink", lambda self: self.name == "history.csv"
  )
  assert list_evidence_files() == []


@pytest.mark.asyncio
async def test_preview_extends_warmup_and_exact_next_session():
  class Calendar:
    async def get_next_trading_date(self, market, target):
      assert market == "SH"
      return target + timedelta(days=3)

  config = ResearchPreparationConfig.model_validate(CONFIG)
  result = await download_preview(config, Calendar())
  assert result["end"] == "2025-08-01"
  assert date.fromisoformat(result["start"]) <= config.date_start - timedelta(days=504)
  assert result["periods"] == ["1d"]


@pytest.mark.asyncio
async def test_config_and_jobs_are_durable_idempotent_and_retryable():
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(lambda c: ResearchPreparationSettings.__table__.create(c))
    await connection.run_sync(lambda c: ResearchPreparationJob.__table__.create(c))
  session = async_sessionmaker(engine, expire_on_commit=False)
  async with session() as db:
    repo = ResearchPreparationRepository(db)
    config = await repo.save(CONFIG)
    first = await repo.submit(kind="DOWNLOAD", config=config, request_key="a" * 32)
    duplicate = await repo.submit(kind="DOWNLOAD", config=config, request_key="b" * 32)
    assert first.job_id == duplicate.job_id
    await repo.save({**CONFIG, "date_end": "2025-08-01"})
    assert first.request["config"]["date_end"] == "2025-07-29"
    job = await repo.claim("flow-1", kinds=("DOWNLOAD",), executor="WORKER")
    assert job.job_id == first.job_id
    # Age alone must not permit a second execution while the original lives.
    job.updated_at -= timedelta(days=1)
    await db.commit()
    assert await repo.claim("flow-2", kinds=("DOWNLOAD",), executor="WORKER") is None
    with pytest.raises(ValueError, match="归属"):
      await repo.progress(job.job_id, expected_flow_run_id="stale-owner", status="FAILED")
    await db.refresh(job)
    await repo.progress(job.job_id, expected_flow_run_id="flow-1", status="FAILED", error="test")
    await db.refresh(job)
    assert (await repo.retry(job.job_id)).status == "QUEUED"
  async with session() as db:
    repo = ResearchPreparationRepository(db)
    assert (await repo.config())["date_end"] == "2025-08-01"
    claimed = await repo.claim("flow-3", kinds=("DOWNLOAD",), executor="WORKER")
    assert claimed.request == first.request
    with pytest.raises(ValueError, match="归属"):
      await repo.progress(claimed.job_id, expected_flow_run_id="flow-1", status="SUCCEEDED")
    await db.refresh(claimed)
    assert claimed.status == "RUNNING"
    await repo.progress(claimed.job_id, expected_flow_run_id="flow-3", status="SUCCEEDED")
    gpu = await repo.submit(kind="GPU", config=await repo.config(), request_key="gpu-request-key-1234", dataset_version="dataset-v1")
    assert await repo.claim("worker", kinds=("COVERAGE", "DOWNLOAD", "CERTIFY"), executor="WORKER") is None
    assigned = await repo.claim("trainer", kinds=("GPU",), executor="TRAINER")
    assert assigned.job_id == gpu.job_id
    assert assigned.flow_run_id == "trainer"
    assert [row.job_id for row in await repo.running_jobs(kinds=("GPU",))] == [gpu.job_id]
    assert await repo.running_jobs(kinds=("DOWNLOAD",)) == []
    gpu_id = assigned.job_id
    with pytest.raises(ValueError, match="归属"):
      await repo.requeue_trainer_admission(gpu_id, expected_flow_run_id="old-owner")
    await repo.requeue_trainer_admission(gpu_id, expected_flow_run_id="trainer")
    reassigned = await repo.claim("new-trainer", kinds=("GPU",), executor="TRAINER")
    assert reassigned.job_id == gpu_id
    assert reassigned.flow_run_id == "new-trainer"
    with pytest.raises(ValueError, match="归属"):
      await repo.requeue_trainer_admission(gpu_id, expected_flow_run_id="trainer")
    await repo.requeue_trainer_inputs(gpu_id, expected_flow_run_id="new-trainer")

    def unavailable(job_id, owner):
      raise OSError("input evidence unavailable")

    with pytest.raises(OSError):
      await repo.claim("input-owner", kinds=("GPU",), executor="TRAINER", prepare_execution=unavailable)
    assert await repo.running_jobs(kinds=("GPU",)) == []
    prepared = []
    next_run = await repo.claim("input-owner", kinds=("GPU",), executor="TRAINER", prepare_execution=lambda job_id, owner: prepared.append((job_id, owner)))
    assert next_run.job_id == gpu_id
    assert prepared == [(gpu_id, "input-owner")]
  await engine.dispose()


@pytest.mark.asyncio
async def test_certification_handoff_is_owner_fenced_immutable_and_executor_routed():
  from quantx_contracts.research_preparation import CertificationInputReference
  from quantx_contracts.training_bundle import BundleFile, TrainingBundle

  reference = CertificationInputReference(
    bundle=TrainingBundle(schema_version=1, kind="CERTIFICATION_INPUT", source_id="cert-v1", files=[
      BundleFile(path=name, size=1, sha256="a" * 64)
      for name in ["manifest.json", "config.json", "source/manifest.json"]
    ]), manifest_sha256="a" * 64,
  )
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as connection:
    await connection.run_sync(lambda c: ResearchPreparationJob.__table__.create(c))
  session = async_sessionmaker(engine, expire_on_commit=False)
  async with session() as db:
    repo = ResearchPreparationRepository(db)
    config = {**CONFIG, "st_file": "st.csv", "industry_file": "industry.csv", "delisting_file": "delisting.csv"}
    job = await repo.submit(kind="CERTIFY", config=config, request_key="c" * 32, dataset_version="cert-v1")
    job_id = job.job_id
    assert await repo.claim("trainer", kinds=("CERTIFY",), executor="TRAINER") is None
    job = await repo.claim("export-owner", kinds=("CERTIFY",), executor="WORKER")
    with pytest.raises(ValueError, match="归属"):
      await repo.requeue_worker_admission(job_id, expected_flow_run_id="other")
    await repo.requeue_worker_admission(job_id, expected_flow_run_id="export-owner")
    assert await repo.claim("trainer", kinds=("CERTIFY",), executor="TRAINER") is None
    await repo.claim("export-owner", kinds=("CERTIFY",), executor="WORKER")
    with pytest.raises(ValueError, match="归属"):
      await repo.handoff_certification(job_id, expected_flow_run_id="other", reference=reference)
    with pytest.raises(ValueError, match="归属"):
      await repo.requeue_trainer_inputs(job_id, expected_flow_run_id="export-owner")
    await repo.handoff_certification(job_id, expected_flow_run_id="export-owner", reference=reference)
    assert await repo.certification_handoff_status(job_id, expected_flow_run_id="export-owner") == "QUEUED"
    assert await repo.certification_handoff_status(job_id, expected_flow_run_id="other") is None
    # Commit acknowledgement loss: identical retry is harmless and does not
    # overwrite the subsequent Trainer owner.
    await repo.handoff_certification(job_id, expected_flow_run_id="export-owner", reference=reference.model_copy(update={"bundle": reference.bundle.model_copy(update={"files": tuple(reversed(reference.bundle.files))})}))
    assert await repo.claim("worker", kinds=("CERTIFY",), executor="WORKER") is None
    assigned = await repo.claim("trainer-owner", kinds=("CERTIFY",), executor="TRAINER")
    assert assigned.job_id == job_id
    with pytest.raises(ValueError, match="交接"):
      await repo.requeue_worker_admission(job_id, expected_flow_run_id="trainer-owner")
    await repo.handoff_certification(job_id, expected_flow_run_id="export-owner", reference=reference)
    await db.refresh(assigned)
    assert assigned.status == "RUNNING" and assigned.flow_run_id == "trainer-owner"
    with pytest.raises(ValueError, match="归属"):
      await repo.progress(job_id, expected_flow_run_id="export-owner", status="SUCCEEDED")
    with pytest.raises(ValueError, match="归属"):
      await repo.progress(job_id, expected_flow_run_id="trainer-owner", request={"dataset_version": "cert-v1"})
    with pytest.raises(ValueError, match="不可替换"):
      await repo.handoff_certification(job_id, expected_flow_run_id="trainer-owner", reference=reference)
    await repo.progress(job_id, expected_flow_run_id="trainer-owner", status="FAILED")
    await repo.retry(job_id)
    assert await repo.claim("worker", kinds=("CERTIFY",), executor="WORKER") is None
    retried = await repo.claim("trainer-retry", kinds=("CERTIFY",), executor="TRAINER")
    assert CertificationInputReference.model_validate(retried.request["certification_input"]).bundle.bundle_id == reference.bundle.bundle_id
    await repo.requeue_trainer_admission(job_id, expected_flow_run_id="trainer-retry")
    await repo.claim("trainer-final", kinds=("CERTIFY",), executor="TRAINER")
    await repo.requeue_trainer_inputs(job_id, expected_flow_run_id="trainer-final")
    assert await repo.claim("worker", kinds=("CERTIFY",), executor="WORKER") is None
  await engine.dispose()
