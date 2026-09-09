"""Retirement preserves evidence until verified, aged and durably journaled."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from quantx_contracts.collection_permit import CollectionPermit
from quantx_contracts.history_upload import HistoryUploadChunk, HistoryUploadSnapshot
from quantx_qmt_agent.history_pipeline import HistoryPipeline

from tests.qmt_agent.test_history_assembly import assembly  # noqa: F401


async def prepare(components, verified_at):
  runtime, job, artifacts, _ = components
  runtime.configuration.api_url = "http://local.test"
  async with runtime._historical_worker_lock:
    spool = runtime._prepare_history_job_sync(job, artifacts)
  snapshot = HistoryUploadSnapshot(
    request_id=job.request.request_id,
    status="COMPLETED",
    verified_at=verified_at,
    total_chunks=len(spool.chunks),
    chunks=[
      HistoryUploadChunk(
        index=index,
        sha256=chunk.digest,
        record_count=chunk.record_count,
        byte_count=chunk.compressed_bytes,
      )
      for index, chunk in enumerate(spool.chunks)
    ],
  )
  pipeline = HistoryPipeline(runtime)
  pipeline._upload_snapshot = AsyncMock(return_value=snapshot)
  return runtime, job, pipeline


@pytest.mark.parametrize("age", [None, 1, 25])
async def test_only_old_verified_ingestion_retires_files(assembly, age):  # noqa: F811
  verified = (
    datetime.now(timezone.utc) - timedelta(hours=age) if age is not None else None
  )
  runtime, job, pipeline = await prepare(assembly, verified)
  result = await pipeline.recover_retained_uploads()
  retired = age == 25
  assert result[str(job.request.request_id)] == (
    "RETIRED" if retired else "UPLOAD_ACCEPTED"
  )
  assert job.directory.exists() is not retired
  assert (
    runtime.journal.history_upload_retired(
      runtime.configuration.device_id, str(job.request.request_id)
    )
    is retired
  )
  if retired:
    now = datetime.now(timezone.utc)
    grant = CollectionPermit(
      permit_id=uuid4(),
      device_id=runtime.configuration.device_id,
      owner_epoch=2,
      unit=job.units[0],
      issued_at=now,
      expires_at=now + timedelta(seconds=15),
    )
    with pytest.raises(ValueError, match="retired"):
      runtime.journal.accept_collection_permit(
        grant, device_id=runtime.configuration.device_id, unit=job.units[0], now=now
      )


async def test_partial_cleanup_resumes_from_journal_without_request_manifest(
  assembly, monkeypatch
):  # noqa: F811
  from quantx_qmt_agent import history_retirement as cleanup

  runtime, job, pipeline = await prepare(
    assembly, datetime.now(timezone.utc) - timedelta(hours=25)
  )
  original = cleanup.shutil.rmtree

  def interrupted(path):
    if path == job.directory:
      (path / "request.json").unlink()
      raise PermissionError("simulated interrupted cleanup")
    original(path)

  monkeypatch.setattr(cleanup.shutil, "rmtree", interrupted)
  assert (await pipeline.recover_retained_uploads())[
    str(job.request.request_id)
  ] == "HISTORY_RECOVERY_BLOCKED"
  assert runtime.journal.history_upload_retired(
    runtime.configuration.device_id, str(job.request.request_id)
  )
  monkeypatch.setattr(cleanup.shutil, "rmtree", original)
  assert (await pipeline.recover_retained_uploads())[
    str(job.request.request_id)
  ] == "RETIRED"
  pipeline._upload_snapshot.assert_awaited_once()
  assert not job.directory.exists()


async def test_cleanup_refuses_linked_content(assembly, tmp_path):  # noqa: F811
  runtime, job, pipeline = await prepare(
    assembly, datetime.now(timezone.utc) - timedelta(hours=25)
  )
  outside = tmp_path / "outside.txt"
  outside.write_text("preserve", encoding="utf-8")
  (job.artifacts_directory / "link").symlink_to(outside)
  assert (await pipeline.recover_retained_uploads())[
    str(job.request.request_id)
  ] == "HISTORY_RECOVERY_BLOCKED"
  assert outside.read_text() == "preserve"
  assert job.directory.exists()
