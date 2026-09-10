"""Forward checkpoint migration retains budgets and requires explicit recovery."""

# ruff: noqa: F811
import json
from datetime import datetime
from pathlib import Path

import pytest
from quantx_application.market_data.ingestion import transition
from quantx_infrastructure.services.market_data_transfer_ingestion import (
  MarketDataValidationError,
)
from quantx_infrastructure.services.native_checkpoint_migration import (
  apply_native_migration,
  manifest_hashes,
  plan_native_migration,
)
from quantx_market_data import worker
from sqlalchemy import text

from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_transfer_ingestion import (
  _payload,
  _summary,
  _tick_row,
)
from tests.infrastructure.test_market_data_worker_service import workers  # noqa: F401
from tests.infrastructure.test_native_bar_ingestion import Source, native  # noqa: F401


async def seed(store, root):
  row = _tick_row()
  source = Source(root, _payload(), [row, _summary([row])])
  source.manifest[0]["compressed_bytes"] = (
    Path(source.manifest[0]["storage_reference"]).stat().st_size
  )
  old, _ = manifest_hashes(source.payload, source.manifest, "a" * 64)
  state = transition(None, "manifest", {"sha256": old}, datetime(2026, 9, 9, 12))
  state.update(
    phase="READBACK",
    attempt=3,
    executions=4,
    blocked=True,
    reason_code="DEPENDENCY_QUERY_CAPACITY_BLOCKED",
    checkpoints={"0": {"sha256": "f" * 64, "rows": 1, "attempt": 3}},
    write_result={"records_saved": 1},
    failures=[{"reason_code": "OLD_FAILURE"}],
  )
  async with store.engine.begin() as db:
    await db.execute(
      text(
        "CREATE TEMP TABLE market_data_transfer (request_id text,chunk_index integer,checksum_sha256 text,record_count integer,compressed boolean,compressed_bytes integer,storage_reference text)"
      )
    )
    item = source.manifest[0]
    await db.execute(
      text(
        "INSERT INTO market_data_transfer VALUES (:id,:chunk_index,:checksum_sha256,:record_count,:compressed,:compressed_bytes,:storage_reference)"
      ),
      {"id": source.identity, **item},
    )
    await db.execute(
      text(
        "UPDATE market_data_request SET request_id=:id,ingestion_progress=CAST(:progress AS jsonb),status='BLOCKED',ingestion_result=json_build_object('legacy',true)"
      ),
      {"id": source.identity, "progress": json.dumps(state)},
    )
  store.manifest = source.manifest
  return source, state


async def test_migration_preserves_budget_then_explicit_recovery_writes_new_version(
  workers, native
):
  (store, _), _ = workers
  root, storage = native
  source, old = await seed(store, root)
  original = Path(source.manifest[0]["storage_reference"]).read_bytes()
  plan = await plan_native_migration(store.engine, source.identity)
  assert not storage.lines
  assert (await apply_native_migration(store.engine, plan))["status"] == "migrated"
  migrated = await store.market_data_request(source.identity)
  state = migrated["ingestion_progress"]
  assert migrated["status"] == "BLOCKED"
  assert state["legacy_storage_migration"]["previous_progress"] == old
  for key in ("executions", "attempt", "stage_started_at", "failures"):
    assert state[key] == old[key]
  assert state["checkpoints"] == {} and state["phase"] == "WRITE"
  assert state["legacy_storage_migration"]["previous_result"] == {"legacy": True}
  assert not storage.lines
  assert (await apply_native_migration(store.engine, plan))[
    "status"
  ] == "already_migrated"
  assert await store.acquire()
  assert await worker.sweep(store) == 0
  await store.resume_market_data_request(
    source.identity, reason="verified explicit migration recovery"
  )
  assert await worker.sweep(store) == 1
  completed = await store.market_data_request(source.identity)
  assert completed["status"] == "COMPLETED"
  assert (
    completed["ingestion_result"]["native_storage_version"]
    == plan.native_storage_version
  )
  assert (
    completed["ingestion_progress"]["legacy_storage_migration"]["previous_progress"]
    == old
  )
  assert len(storage.lines) == 1
  assert Path(source.manifest[0]["storage_reference"]).read_bytes() == original
  await store.release()
  assert (await apply_native_migration(store.engine, plan))[
    "status"
  ] == "already_migrated"
  assert await store.market_data_request(source.identity) == completed


@pytest.mark.parametrize(
  "change", ["row", "file", "worker", "budget_missing", "bad_version"]
)
async def test_migration_rejects_changes_without_mutation(workers, native, change):
  (store, _), _ = workers
  root, storage = native
  source, _ = await seed(store, root)
  plan = await plan_native_migration(store.engine, source.identity)
  if change == "row":
    async with store.engine.begin() as db:
      await db.execute(
        text("UPDATE market_data_request SET processing_error='changed'")
      )
  elif change == "file":
    Path(source.manifest[0]["storage_reference"]).write_bytes(b"corrupt")
  elif change == "worker":
    assert await store.acquire()
  else:
    async with store.engine.begin() as db:
      await db.execute(
        text(
          "UPDATE market_data_request SET ingestion_progress=NULL"
          if change == "budget_missing"
          else "UPDATE market_data_request SET ingestion_progress=jsonb_set(ingestion_progress,'{version}','true')"
        )
      )
    with pytest.raises(ValueError, match="BUDGET_EVIDENCE_REQUIRED"):
      await plan_native_migration(store.engine, source.identity)
  before = await store.market_data_request(source.identity)
  with pytest.raises(MarketDataValidationError if change == "file" else ValueError):
    await apply_native_migration(store.engine, plan)
  assert await store.market_data_request(source.identity) == before
  assert not storage.lines
