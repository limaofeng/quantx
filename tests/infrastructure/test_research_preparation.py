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
    job = await repo.claim("flow-1")
    assert job.job_id == first.job_id
    assert await repo.claim("flow-2") is None
    await repo.progress(job.job_id, status="FAILED", error="test")
    await db.refresh(job)
    assert (await repo.retry(job.job_id)).status == "QUEUED"
  async with session() as db:
    repo = ResearchPreparationRepository(db)
    assert (await repo.config())["date_end"] == "2025-08-01"
    assert (await repo.claim("flow-3")).request == first.request
  await engine.dispose()
