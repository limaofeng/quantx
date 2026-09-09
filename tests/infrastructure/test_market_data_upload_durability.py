"""Data API must persist file bytes before acknowledging the database receipt."""

import asyncio
import os
import stat
import threading
from datetime import datetime, timezone

import pytest
from quantx_infrastructure.models.agent_runtime import (
  MarketDataRequest,
  MarketDataTransfer,
)
from quantx_market_data import agent_upload as api
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.api.integration.test_agent_market_upload_conflict import (
  REQUEST_ID,
  _configure_api,
  _market_data_database,
  _seed_dispatch_request,
  _upload,
)


async def test_fsync_and_rename_precede_database_commit(tmp_path, monkeypatch):
  events = []
  fsync, replace = os.fsync, os.replace

  def sync(descriptor):
    events.append("file" if stat.S_ISREG(os.fstat(descriptor).st_mode) else "directory")
    return fsync(descriptor)

  def rename(*args):
    events.append("rename")
    return replace(*args)

  def commit(_):
    events.append("commit")

  async with _market_data_database() as (_, sessions):
    _configure_api(monkeypatch, sessions, tmp_path)
    await _seed_dispatch_request(
      sessions,
      request_id=REQUEST_ID,
      status="DELIVERED",
      now=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    monkeypatch.setattr(api.os, "fsync", sync)
    monkeypatch.setattr(api.os, "replace", rename)
    event.listen(AsyncSession.sync_session_class, "before_commit", commit)
    try:
      result = await _upload(b"compressed-test-bytes", total_chunks=1)
    finally:
      event.remove(AsyncSession.sync_session_class, "before_commit", commit)
    assert result["accepted"]
    assert events[:2] == ["file", "rename"]
    assert events[-1] == "commit"
    if os.name != "nt":
      assert events[2:-1] == ["directory", "directory"]


async def test_fsync_failure_never_records_transfer(tmp_path, monkeypatch):
  async with _market_data_database() as (_, sessions):
    _configure_api(monkeypatch, sessions, tmp_path)
    await _seed_dispatch_request(
      sessions,
      request_id=REQUEST_ID,
      status="DELIVERED",
      now=datetime.now(timezone.utc).replace(tzinfo=None),
    )

    def fail(_):
      raise OSError("simulated fsync failure")

    monkeypatch.setattr(api.os, "fsync", fail)
    with pytest.raises(OSError, match="fsync failure"):
      await _upload(b"compressed-test-bytes", total_chunks=1)
    async with sessions() as db:
      assert await db.scalar(select(func.count()).select_from(MarketDataTransfer)) == 0
      assert (await db.get(MarketDataRequest, REQUEST_ID)).status == "DELIVERED"
    assert not list(tmp_path.rglob("*.tmp"))


async def test_cancel_waits_for_publisher_before_cleanup(tmp_path, monkeypatch):
  entered, release = threading.Event(), threading.Event()
  original = api._publish_upload_file

  def publish(*args):
    entered.set()
    release.wait(3)
    original(*args)

  monkeypatch.setattr(api, "_publish_upload_file", publish)
  temporary, destination = tmp_path / "chunk.tmp", tmp_path / "chunk.gz"
  task = asyncio.create_task(
    api._persist_upload_file(temporary, destination, b"retained")
  )
  try:
    assert await asyncio.to_thread(entered.wait, 1)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
  finally:
    release.set()
    with pytest.raises(asyncio.CancelledError):
      await task
  assert destination.read_bytes() == b"retained"
  assert not temporary.exists()


async def test_upload_snapshot_is_scoped_read_only_and_excludes_storage_paths(
  tmp_path, monkeypatch
):
  from types import SimpleNamespace
  from uuid import UUID

  import httpx
  from fastapi import FastAPI, HTTPException
  from quantx_contracts.history_upload import HistoryUploadSnapshot

  async with _market_data_database() as (_, sessions):
    _configure_api(monkeypatch, sessions, tmp_path)
    await _seed_dispatch_request(
      sessions,
      request_id=REQUEST_ID,
      status="DELIVERED",
      now=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    request = SimpleNamespace(headers={"authorization": "Bearer agent-token"})
    app = FastAPI()
    app.include_router(api.agent_router)
    async with httpx.AsyncClient(
      transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
      response = await client.get(
        f"/agent/market-data/{REQUEST_ID}/upload", headers=request.headers
      )
      assert response.status_code == 200
      before = HistoryUploadSnapshot.model_validate_json(response.content)
    assert not before.frozen and before.chunks == []
    await _upload(b"compressed-test-bytes", total_chunks=1)
    snapshot = await api.get_market_data_upload(UUID(REQUEST_ID), request)
    assert snapshot.frozen and snapshot.total_chunks == 1
    assert snapshot.chunks[0].byte_count == len(b"compressed-test-bytes")
    assert "storage_reference" not in snapshot.model_dump_json()
    assert await api.get_market_data_upload(UUID(REQUEST_ID), request) == snapshot
    async with sessions() as db:
      assert (await db.get(MarketDataRequest, REQUEST_ID)).status == "UPLOADED"

    async def wrong_device(*args, **kwargs):
      return SimpleNamespace(device=SimpleNamespace(id="other-device"))

    monkeypatch.setattr(api, "authenticate_agent_session", wrong_device)
    with pytest.raises(HTTPException) as rejected:
      await api.get_market_data_upload(UUID(REQUEST_ID), request)
    assert rejected.value.status_code == 404


async def test_verification_requires_durable_phase_and_no_native_permit(
  tmp_path, monkeypatch
):
  from datetime import timedelta
  from types import SimpleNamespace
  from uuid import UUID

  from sqlalchemy import text

  async with _market_data_database() as (engine, sessions):
    async with engine.begin() as connection:
      await connection.execute(
        text("CREATE TABLE market_data_collection_permit (request_id TEXT, state TEXT)")
      )
    _configure_api(monkeypatch, sessions, tmp_path)
    await _seed_dispatch_request(
      sessions,
      request_id=REQUEST_ID,
      status="DELIVERED",
      now=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    await _upload(b"compressed-test-bytes", total_chunks=1)
    request = SimpleNamespace(headers={"authorization": "Bearer agent-token"})
    async with sessions() as db:
      row = await db.get(MarketDataRequest, REQUEST_ID)
      row.status = "COMPLETED"
      row.completed_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        hours=25
      )
      row.ingestion_result = {}
      row.ingestion_progress = {"phase": "WRITE"}
      await db.commit()
    assert (
      await api.get_market_data_upload(UUID(REQUEST_ID), request)
    ).verified_at is None
    async with sessions() as db:
      row = await db.get(MarketDataRequest, REQUEST_ID)
      row.ingestion_progress = {"phase": "VERIFIED"}
      await db.execute(
        text("INSERT INTO market_data_collection_permit VALUES (:request, 'STARTED')"),
        {"request": REQUEST_ID},
      )
      await db.commit()
    assert (
      await api.get_market_data_upload(UUID(REQUEST_ID), request)
    ).verified_at is None
    async with sessions() as db:
      await db.execute(
        text("UPDATE market_data_collection_permit SET state='FINISHED'")
      )
      await db.commit()
    snapshot = await api.get_market_data_upload(UUID(REQUEST_ID), request)
    assert snapshot.verified_at is not None
    assert snapshot.verified_at.tzinfo is not None
