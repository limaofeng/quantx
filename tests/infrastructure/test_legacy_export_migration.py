"""Real PG producer adoption archives v1 evidence and keeps source work unchanged."""

# ruff: noqa: F811
import importlib.util
import json
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from quantx_infrastructure.services.legacy_export_migration import (
  apply_export_migration,
  plan_export_migration,
)
from sqlalchemy import text

from tests.infrastructure.test_development_bar_publication import prepared  # noqa: F401
from tests.infrastructure.test_development_reference_transaction import (
  references,  # noqa: F401
)
from tests.infrastructure.test_legacy_delivery_proof import inputs
from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_worker_service import workers  # noqa: F401


async def seed(case):
  legacy, source = inputs(case)
  legacy.pop("local_verification")
  path = (
    Path(__file__).resolve().parents[2]
    / "packages/infrastructure/alembic/versions/20260910_0090_legacy_delivery_receipt.py"
  )
  spec = importlib.util.spec_from_file_location("legacy_receipt_migration", path)
  migration = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(migration)
  async with case.first.engine.begin() as db:

    def upgrade(connection):
      with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()

    await db.run_sync(upgrade)
    await db.execute(
      text("ALTER TABLE development_data_export ADD COLUMN source_request_id text")
    )
    await db.execute(
      text(
        "UPDATE development_data_export SET state='READY',manifest=CAST(:manifest AS json),source_request_id=:source"
      ),
      {"manifest": json.dumps(legacy), "source": source["request_id"]},
    )
    await db.execute(
      text(
        "UPDATE market_data_request SET request_id=:id,status='COMPLETED',request_payload=CAST(:payload AS json),ingestion_result=CAST(:audit AS json),created_at=:created"
      ),
      {
        "id": source["request_id"],
        "payload": json.dumps(source["request_payload"]),
        "audit": json.dumps(source["ingestion_result"]),
        "created": source["created_at"].replace(tzinfo=None),
      },
    )
  return migration


async def snapshot(case):
  async with case.first.engine.connect() as db:
    return {
      table: (await db.execute(text(f"SELECT to_jsonb(t) FROM {table} t")))
      .scalars()
      .all()
      for table in (
        "development_data_export",
        "market_data_request",
        "development_data_ingestion",
        "development_data_download_budget",
      )
    }


@pytest.mark.parametrize(
  "prepared", [{"period": "1d", "start_write": False}], indirect=True
)
async def test_producer_upgrade_archives_receipt_and_downgrade_refuses_evidence_loss(
  prepared,
):
  case = prepared
  migration = await seed(case)
  before = await snapshot(case)
  plan = await plan_export_migration(case.first.engine, "delivery")
  assert await snapshot(case) == before
  await case.first.release()
  assert (await apply_export_migration(case.first.engine, plan))["status"] == "migrated"
  after = await snapshot(case)
  row = after["development_data_export"][0]
  assert (
    row["legacy_manifest"]["previous_export"] == before["development_data_export"][0]
  )
  assert row["manifest"] == case.manifest
  assert row["state"] == "READY"
  for table in before.keys() - {"development_data_export"}:
    assert after[table] == before[table]
  assert (await apply_export_migration(case.first.engine, plan))[
    "status"
  ] == "already_migrated"
  assert await snapshot(case) == after
  async with case.first.engine.begin() as db:

    def downgrade(connection):
      with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()

    with pytest.raises(RuntimeError, match="retained legacy"):
      await db.run_sync(downgrade)


@pytest.mark.parametrize(
  "prepared", [{"period": "1d", "start_write": False}], indirect=True
)
@pytest.mark.parametrize("change", ["worker", "export", "source", "plan", "terminal"])
async def test_producer_upgrade_rejects_conflict_without_mutation(prepared, change):
  case = prepared
  await seed(case)
  plan = await plan_export_migration(case.first.engine, "delivery")
  if change != "worker":
    await case.first.release()
  async with case.first.engine.begin() as db:
    if change == "export":
      await db.execute(text("UPDATE development_data_export SET error='changed'"))
    elif change == "source":
      await db.execute(
        text("UPDATE market_data_request SET updated_at=clock_timestamp()")
      )
    elif change == "terminal":
      await db.execute(text("UPDATE development_data_export SET state='INCOMPLETE'"))
  if change == "plan":
    plan.manifest["data_version"] = "f" * 64
  before = await snapshot(case)
  with pytest.raises(ValueError, match="LEGACY_EXPORT_"):
    await apply_export_migration(case.first.engine, plan)
  assert await snapshot(case) == before
