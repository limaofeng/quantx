"""Reference service commits must remain inside the worker's fenced transaction."""

# Imported pytest fixtures are intentionally referenced by test arguments.
# ruff: noqa: F811

import json

import pytest
from quantx_infrastructure.services import market_data_reference_ingestion as reference
from quantx_infrastructure.services.market_data_ingestion_progress import (
  IngestionProgress,
)
from quantx_infrastructure.services.market_data_transfer_ingestion import (
  ingest_uploaded_market_data_request,
)
from quantx_market_data.worker import sweep
from sqlalchemy import text

from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_transfer_ingestion import _write_chunk
from tests.infrastructure.test_market_data_worker_service import workers  # noqa: F401


@pytest.mark.parametrize("failure", [None, "audit", "lease"])
async def test_reference_data_and_checkpoint_commit_or_rollback_together(
  workers, failure
):
  (store, _), _ = workers
  assert await store.acquire()
  token = await store.claim_market_data_request("request-1")
  for action, values in [
    ("begin", {}),
    ("manifest", {"sha256": "a" * 64}),
    ("advance", {"phase": "WRITE"}),
  ]:
    await store.mutate_market_data_ingestion(
      "request-1", claim_token=token, action=action, values=values
    )
  async with store.engine.begin() as connection:
    await connection.execute(text("CREATE TEMP TABLE reference_probe (value integer)"))

  async def persist(db):
    # A FOR SHARE worker-lease query would take RowShareLock and prevent lease
    # row updates from another transaction during a long reference write.
    held = await db.scalar(
      text("""
      SELECT count(*) FROM pg_locks
      WHERE pid=pg_backend_pid() AND relation='market_data_worker_lease'::regclass
        AND mode='RowShareLock'
    """)
    )
    assert held == 0
    await db.execute(text("INSERT INTO reference_probe VALUES (1)"))
    await db.commit()  # existing repositories commit their session internally
    if failure == "audit":
      raise RuntimeError("reference audit rejected")
    if failure == "lease":
      await db.execute(
        text("""
        UPDATE market_data_worker_lease SET expires_at=clock_timestamp() - INTERVAL '1 second'
      """)
      )
      await db.commit()
    return {"operation": "financial_data", "records_saved": 1}

  if failure:
    with pytest.raises(RuntimeError):
      await store.persist_market_data_reference(
        "request-1", claim_token=token, persist=persist
      )
  else:
    result = await store.persist_market_data_reference(
      "request-1", claim_token=token, persist=persist
    )
    assert result["phase"] == "READBACK"
    assert result["write_result"]["records_saved"] == 1
  async with store.engine.connect() as connection:
    assert await connection.scalar(text("SELECT count(*) FROM reference_probe")) == (
      0 if failure else 1
    )
  row = await store.market_data_request("request-1")
  assert row["ingestion_progress"]["phase"] == ("WRITE" if failure else "READBACK")


async def test_worker_resumes_reference_checkpoint_without_repeating_persistence(
  workers, tmp_path, monkeypatch
):
  (store, _), _ = workers
  payload = {
    "operation": "financial_data",
    "record_format": "financial-row-v1",
    "stock_list": ["600000.SH"],
    "start_time": "20220101",
    "end_time": "20260909",
    "table_list": ["Balance", "Income", "CashFlow", "Capital"],
  }
  records = [
    {
      "record_type": "financial_summary",
      "schema_version": 1,
      "code": "600000.SH",
      "table_counts": {"Balance": 0, "Income": 0, "CashFlow": 0, "Capital": 0},
    }
  ]
  store.manifest = [_write_chunk(tmp_path, records)]
  async with store.engine.begin() as connection:
    await connection.execute(
      text("""
      UPDATE market_data_request SET request_payload=CAST(:payload AS JSON)
      WHERE request_id='request-1'
    """),
      {"payload": json.dumps(payload)},
    )
  writes = 0
  persist = reference._persist_reference_records

  async def counted(*args):
    nonlocal writes
    writes += 1
    return await persist(*args)

  monkeypatch.setattr(reference, "_persist_reference_records", counted)
  assert await store.acquire()
  token = await store.claim_market_data_request("request-1")
  progress = IngestionProgress(store, "request-1", token)
  await progress.apply("begin")
  result = await ingest_uploaded_market_data_request(
    store, "request-1", progress=progress
  )
  assert result["replacement_audit"]["empty_codes"] == ["600000.SH"]
  assert progress.state["phase"] == "READBACK" and writes == 1
  await store.release_market_data_request_claim(
    "request-1", claim_token=token, error="interrupted after commit"
  )
  assert await sweep(store) == 1
  final = await store.market_data_request("request-1")
  assert final["status"] == "COMPLETED"
  assert final["ingestion_progress"]["phase"] == "VERIFIED"
  assert final["ingestion_result"] == result and writes == 1
