"""Cleanup retains catalog evidence regardless of expiry, under the export lock."""
# ruff: noqa: F811

import json
import os
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from quantx_infrastructure.services import development_history_export as exporter
from sqlalchemy import text

from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_worker_service import workers  # noqa: F401


@pytest.fixture
async def cleanup_case(workers, tmp_path, monkeypatch):
  store = workers[0][0]
  monkeypatch.setenv("QUANTX_DATA_EXPORT_ROOT", str(tmp_path))
  async with store.engine.begin() as db:
    await db.execute(
      text("ALTER TABLE development_data_export ADD COLUMN manifest json")
    )
  return SimpleNamespace(engine=store.engine, root=tmp_path)


def file(case, digest, *, old=True):
  path = case.root / (digest + ".json.gz")
  path.write_bytes(b"original evidence")
  if old:
    stamp = time.time() - 8 * 86400
    os.utime(path, (stamp, stamp))
  return path


@asynccontextmanager
async def locked(case):
  async with case.engine.connect() as db:
    assert await db.scalar(text("SELECT pg_try_advisory_lock(817234591)"))
    try:
      yield db
    finally:
      await db.rollback()
      await db.execute(text("SELECT pg_advisory_unlock(817234591)"))
      await db.commit()


async def test_expired_waiting_blocked_and_published_manifests_keep_their_files(
  cleanup_case,
):
  case = cleanup_case
  retained = []
  async with case.engine.begin() as db:
    for index, state in enumerate(
      [
        "READY",
        "EXPIRED",
        "WAITING_LOCAL_PROOF",
        "WAITING_LOCAL_INGESTION",
        "LOCAL_VERIFIED",
        "BLOCKED",
        "INCOMPLETE",
      ]
    ):
      digest = str(index) * 64
      retained.append(file(case, digest))
      await db.execute(
        text("""
        INSERT INTO development_data_export(id,request,state,manifest,expires_at,updated_at)
        VALUES (:id,'{}',:state,CAST(:manifest AS JSON),
          CASE WHEN :expired THEN clock_timestamp()-INTERVAL '8 days' ELSE NULL END,clock_timestamp())
      """),
        {
          "id": str(index),
          "state": state,
          "expired": index < 2,
          "manifest": json.dumps({"chunks": [{"checksum_sha256": digest}]}),
        },
      )
    # A reference-only receipt has no chunk references and must not disable GC.
    await db.execute(
      text("""
      INSERT INTO development_data_export(id,request,state,manifest,updated_at)
      VALUES ('reference','{"operation":"reference"}','REFERENCE_VERIFIED','{"reference":{},"data_version":"version"}',clock_timestamp())
    """)
    )
  orphan = file(case, "a" * 64)
  recent = file(case, "b" * 64, old=False)
  unknown = file(case, "not-a-checksum")
  async with locked(case) as db:
    await exporter.cleanup_expired(db)
    assert (
      await db.scalar(text("SELECT state FROM development_data_export WHERE id='0'"))
      == "EXPIRED"
    )
  assert not orphan.exists()
  assert recent.exists() and unknown.exists()
  assert all(path.read_bytes() == b"original evidence" for path in retained)


async def test_cleanup_without_publication_lock_cannot_touch_files(cleanup_case):
  case = cleanup_case
  orphan = file(case, "a" * 64)
  async with case.engine.connect() as db:
    with pytest.raises(RuntimeError, match="live publication lock"):
      await exporter.cleanup_expired(db)
  assert orphan.exists()


async def test_retained_entries_do_not_starve_later_orphan_cleanup(
  cleanup_case, monkeypatch
):
  case = cleanup_case
  monkeypatch.setattr(exporter, "MAX_CLEANUP_DELETIONS", 1)
  recent = file(case, "a" * 64, old=False)
  orphans = [file(case, "b" * 64), file(case, "c" * 64)]
  async with locked(case) as db:
    await exporter.cleanup_expired(db)
    assert sum(path.exists() for path in orphans) == 1
    await exporter.cleanup_expired(db)
  assert recent.exists()
  assert not any(path.exists() for path in orphans)


@pytest.mark.parametrize("invalid", ["malformed", "empty", "checksum", "budget"])
async def test_unresolved_reference_set_never_authorizes_deletion(
  cleanup_case, monkeypatch, invalid
):
  case = cleanup_case
  manifest = {"chunks": [{"checksum_sha256": "b" * 64}, {"checksum_sha256": "c" * 64}]}
  if invalid == "malformed":
    manifest["chunks"] = "unreadable"
  elif invalid == "empty":
    manifest["chunks"] = []
  elif invalid == "checksum":
    manifest["chunks"][0]["checksum_sha256"] = "invalid"
  else:
    monkeypatch.setattr(exporter, "MAX_CLEANUP_REFERENCES", 1)
  async with case.engine.begin() as db:
    await db.execute(
      text(
        "INSERT INTO development_data_export(id,request,state,manifest,updated_at) VALUES ('evidence','{}','BLOCKED',CAST(:manifest AS JSON),clock_timestamp())"
      ),
      {"manifest": json.dumps(manifest)},
    )
  orphan = file(case, "a" * 64)
  async with locked(case) as db:
    with pytest.raises(RuntimeError, match="export cleanup"):
      await exporter.cleanup_expired(db)
  assert orphan.exists()
