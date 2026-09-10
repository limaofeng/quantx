"""Receiver migration preserves exhausted budgets and re-proves original local files."""

# ruff: noqa: F811
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from quantx_infrastructure.services import development_history_import as importer
from quantx_infrastructure.services.legacy_export_migration import (
  apply_export_migration,
  plan_export_migration,
)
from quantx_infrastructure.services.legacy_import_migration import (
  apply_import_migration,
  plan_import_migration,
  recover_import_migration,
)
from quantx_market_data import worker
from quantx_market_data.api import create_app
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.infrastructure.test_development_bar_publication import prepared  # noqa: F401
from tests.infrastructure.test_development_default_delivery import (
  delivery,  # noqa: F401
)
from tests.infrastructure.test_development_reference_transaction import (
  references,  # noqa: F401
)
from tests.infrastructure.test_legacy_delivery_proof import inputs
from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_worker_service import workers  # noqa: F401

pytestmark = pytest.mark.parametrize(
  "prepared", [{"period": "1d", "start_write": False}], indirect=True
)


async def seed(case):
  case.first.demand_source_kind = "REMOTE"
  receipt = await importer.import_partition(case.request, owner=case.first)
  legacy, _ = inputs(case)
  legacy["local_verification"] = receipt["local_verification"]
  path = (
    Path(__file__).resolve().parents[2]
    / "packages/infrastructure/alembic/versions/20260910_0090_legacy_delivery_receipt.py"
  )
  spec = importlib.util.spec_from_file_location("import_archive_migration", path)
  migration = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(migration)
  async with case.first.engine.begin() as db:

    def upgrade(connection):
      with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()

    await db.run_sync(upgrade)
    await db.execute(
      text("UPDATE development_data_export SET manifest=CAST(:legacy AS json)"),
      {"legacy": json.dumps(legacy)},
    )
    await db.execute(
      text("UPDATE development_data_bar_version SET source_version=:version"),
      {"version": legacy["data_version"]},
    )
    await db.execute(
      text(
        "UPDATE development_data_download_budget SET attempts=512,proof_attempts=4,reason_code='DELIVERY_DOWNLOAD_BUDGET_EXHAUSTED'"
      )
    )
    await db.execute(
      text(
        "UPDATE development_data_ingestion SET progress=jsonb_set(progress,'{executions}','4')"
      )
    )
  return {
    "id": case.identity,
    "state": "READY",
    "request": case.request.model_dump(mode="json"),
    "manifest": case.manifest,
  }


async def snapshot(case):
  async with case.first.engine.connect() as db:
    return {
      name: (
        await db.execute(
          text(
            f"SELECT to_jsonb(t) FROM {table} t WHERE "
            + ("id" if name == "export" else "delivery_id")
            + "=:id"
          ),
          {"id": case.identity},
        )
      ).scalar_one_or_none()
      for name, table in (
        ("export", "development_data_export"),
        ("ingestion", "development_data_ingestion"),
        ("budget", "development_data_download_budget"),
        ("version", "development_data_bar_version"),
      )
    }


async def migrate_producer(case):
  """Separate PG session/catalog: transfer the actual committed producer receipt."""
  legacy, source = inputs(case)
  legacy.pop("local_verification")
  engine = create_async_engine(case.first.engine.url, pool_size=1, max_overflow=0)
  try:
    async with engine.begin() as db:
      for ddl in (
        "CREATE TEMP TABLE market_data_worker_lease(expires_at timestamptz)",
        "CREATE TEMP TABLE market_data_request(request_id text,status text,created_at timestamptz,request_payload jsonb,ingestion_result jsonb)",
        "CREATE TEMP TABLE development_data_export(id text,request jsonb,state text,manifest jsonb,source_request_id text,legacy_manifest jsonb,error text,updated_at timestamptz)",
      ):
        await db.execute(text(ddl))
      await db.execute(
        text(
          "INSERT INTO market_data_request SELECT * FROM jsonb_populate_record(NULL::pg_temp.market_data_request,CAST(:row AS jsonb))"
        ),
        {"row": json.dumps(source, default=lambda value: value.isoformat())},
      )
      await db.execute(
        text(
          "INSERT INTO development_data_export(id,request,state,manifest,source_request_id) VALUES (:id,CAST(:request AS jsonb),'READY',CAST(:manifest AS jsonb),:source)"
        ),
        {
          "id": case.identity,
          "request": case.request.model_dump_json(),
          "manifest": json.dumps(legacy),
          "source": source["request_id"],
        },
      )
    plan = await plan_export_migration(engine, case.identity)
    assert (await apply_export_migration(engine, plan))["status"] == "migrated"
    async with engine.connect() as db:
      row = (
        await db.execute(text("SELECT to_jsonb(e) FROM development_data_export e"))
      ).scalar_one()
    assert row["legacy_manifest"]["previous_export"]["manifest"] == legacy
    assert row["source_request_id"] == source["request_id"]
    return {key: row[key] for key in ("id", "state", "request", "manifest")}
  finally:
    await engine.dispose()


async def test_migrated_receiver_recovers_without_network_or_budget_refund(delivery):
  case = delivery
  await seed(case)
  remote = await migrate_producer(case)
  before = await snapshot(case)
  calls = len(case.calls)
  writes = len(case.connection.lines)
  plan = await plan_import_migration(case.first.engine, case.identity, remote)
  assert await snapshot(case) == before
  await case.first.release()
  assert (await apply_import_migration(case.first.engine, plan, remote))[
    "status"
  ] == "migrated"
  migrated = await snapshot(case)
  assert migrated["export"]["state"] == "BLOCKED"
  assert migrated["export"]["legacy_manifest"]["previous_snapshot"] == before
  assert migrated["budget"] == before["budget"]
  for key in ("attempt", "executions", "stage_started_at", "failures"):
    assert (
      migrated["ingestion"]["progress"][key] == before["ingestion"]["progress"][key]
    )
  assert migrated["version"]["proof"] is None
  assert len(case.connection.lines) == writes and len(case.calls) == calls
  assert (await apply_import_migration(case.first.engine, plan, remote))[
    "status"
  ] == "already_migrated"
  await recover_import_migration(
    case.first.engine, case.identity, "operator confirmed original local files"
  )
  assert await case.first.acquire()
  assert await worker.advance_development_delivery(case.first)
  final = await snapshot(case)
  assert final["export"]["state"] == "LOCAL_VERIFIED"
  assert (
    final["export"]["manifest"]["local_verification"]["immutable_storage"][
      "records_verified"
    ]
    == 1
  )
  assert final["budget"] == before["budget"]
  assert final["export"]["legacy_manifest"]["previous_snapshot"] == before
  assert len(case.connection.lines) == writes + 1 and len(case.calls) == calls
  app = create_app(store=SimpleNamespace(engine=case.first.engine), token="internal")
  async with app.router.lifespan_context(app):
    async with httpx.AsyncClient(
      transport=httpx.ASGITransport(app),
      base_url="http://local",
      headers={"Authorization": "Bearer internal"},
    ) as client:
      history = await client.get(
        "/market-data/internal/v1/history",
        params={
          "instrument": case.request.instrument,
          "period": "1d",
          "trading_date": "2026-09-07",
        },
      )
      assert history.status_code == 200
      assert history.json()["records"][0]["storage_version"] == plan.storage_version
      assert history.json()["records"][0]["close"] == 10.1
  assert len(case.calls) == calls
  await case.first.release()
  assert (await apply_import_migration(case.first.engine, plan, remote))[
    "status"
  ] == "already_migrated"
  assert await snapshot(case) == final


@pytest.mark.parametrize(
  "change",
  [
    "worker",
    "snapshot",
    "remote",
    "plan",
    "budget_missing",
    "download_budget_missing",
    "checkpoint",
  ],
)
async def test_receiver_rejects_changes_without_partial_adoption(delivery, change):
  case = delivery
  remote = await seed(case)
  plan = await plan_import_migration(case.first.engine, case.identity, remote)
  if change != "worker":
    await case.first.release()
  if change == "snapshot":
    async with case.first.engine.begin() as db:
      await db.execute(
        text("UPDATE development_data_download_budget SET attempts=attempts+1")
      )
  elif change == "remote":
    remote = copy.deepcopy(remote)
    remote["id"] = "replacement"
  elif change == "plan":
    plan.progress_manifest_sha256 = "f" * 64
  elif change == "budget_missing":
    async with case.first.engine.begin() as db:
      await db.execute(text("DELETE FROM development_data_ingestion"))
    with pytest.raises(ValueError, match="BUDGET_EVIDENCE_REQUIRED"):
      await plan_import_migration(case.first.engine, case.identity, remote)
  elif change == "download_budget_missing":
    async with case.first.engine.begin() as db:
      await db.execute(text("DELETE FROM development_data_download_budget"))
    with pytest.raises(ValueError, match="BUDGET_EVIDENCE_REQUIRED"):
      await plan_import_migration(case.first.engine, case.identity, remote)
  elif change == "checkpoint":
    async with case.first.engine.begin() as db:
      await db.execute(
        text(
          "UPDATE development_data_ingestion SET progress=jsonb_set(progress,'{manifest_hash}',CAST(:digest AS jsonb))"
        ),
        {"digest": json.dumps("f" * 64)},
      )
    with pytest.raises(ValueError, match="CHECKPOINT_CONFLICT"):
      await plan_import_migration(case.first.engine, case.identity, remote)
  before = await snapshot(case)
  with pytest.raises(ValueError, match="LEGACY_IMPORT_"):
    await apply_import_migration(case.first.engine, plan, remote)
  assert await snapshot(case) == before


async def test_receiver_rejects_inflight_partition_even_without_worker_lease(delivery):
  case = delivery
  remote = await seed(case)
  plan = await plan_import_migration(case.first.engine, case.identity, remote)
  await case.first.release()
  engine = create_async_engine(case.first.engine.url)
  key = int.from_bytes(
    hashlib.sha256(case.request.model_dump_json().encode()).digest()[:8],
    "big",
    signed=True,
  )
  try:
    async with engine.begin() as db:
      await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
      with pytest.raises(ValueError, match="PARTITION_ACTIVE"):
        await apply_import_migration(case.first.engine, plan, remote)
  finally:
    await engine.dispose()


async def test_catalog_failure_rolls_back_progress_and_version_changes(delivery):
  from sqlalchemy import event

  case = delivery
  remote = await seed(case)
  plan = await plan_import_migration(case.first.engine, case.identity, remote)
  await case.first.release()
  before = await snapshot(case)
  changed = []

  def fail_catalog(_connection, _cursor, statement, _parameters, _context, _many):
    if statement.startswith("UPDATE development_data_ingestion"):
      changed.append("progress")
    if statement.startswith("UPDATE development_data_export SET manifest="):
      raise RuntimeError("injected catalog failure")

  event.listen(case.first.engine.sync_engine, "before_cursor_execute", fail_catalog)
  try:
    with pytest.raises(RuntimeError, match="injected catalog failure"):
      await apply_import_migration(case.first.engine, plan, remote)
  finally:
    event.remove(case.first.engine.sync_engine, "before_cursor_execute", fail_catalog)
  assert changed == ["progress"]
  assert await snapshot(case) == before


async def test_receiver_refuses_partition_owned_by_another_delivery(delivery):
  case = delivery
  remote = await seed(case)
  plan = await plan_import_migration(case.first.engine, case.identity, remote)
  await case.first.release()
  async with case.first.engine.begin() as db:
    await db.execute(
      text(
        "INSERT INTO development_data_export(id,request,state,updated_at) SELECT :other,request,'QUEUED',clock_timestamp() FROM development_data_export WHERE id=:id"
      ),
      {"other": "f" * 64, "id": case.identity},
    )
    await db.execute(
      text(
        "UPDATE development_data_bar_version SET delivery_id=:other WHERE delivery_id=:id"
      ),
      {"other": "f" * 64, "id": case.identity},
    )
  before = await snapshot(case)
  with pytest.raises(ValueError, match="STORAGE_BINDING_CONFLICT"):
    await apply_import_migration(case.first.engine, plan, remote)
  assert await snapshot(case) == before
