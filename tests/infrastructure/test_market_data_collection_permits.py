"""Real migration and permit fences on local test PostgreSQL temporary tables."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from quantx_contracts.collection_permit import (
  CollectionUnit,
  plan_historical_work_units,
)
from quantx_infrastructure.services.market_data_collection_permit_store import (
  CollectionPermitStore,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.infrastructure.test_market_data_worker_service import (  # noqa: F401
  durable_store,
  workers,
)


@pytest.fixture
async def permits(workers, tmp_path, monkeypatch):  # noqa: F811
  (first, second), _ = workers
  monkeypatch.setattr(
    "quantx_infrastructure.services.market_data_collection_permit_store.market_data_staging_root",
    lambda: tmp_path,
  )
  request, device = str(uuid4()), str(uuid4())
  payload = {
    "operation": "bars",
    "stock_list": ["000001.SZ"],
    "periods": ["1m"],
    "start_time": "20260901",
    "end_time": "20260903",
  }
  async with first.engine.begin() as connection:
    path = (
      Path(__file__).resolve().parents[2]
      / "packages/infrastructure/alembic/versions/20260909_0067_market_data_collection_permit.py"
    )
    spec = importlib.util.spec_from_file_location("permit_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def upgrade(sync):
      operations = Operations(MigrationContext.configure(sync))
      migration.op = SimpleNamespace(
        create_table=lambda *args, **kwargs: operations.create_table(
          *args, prefixes=["TEMPORARY"], **kwargs
        ),
        create_index=operations.create_index,
      )
      migration.upgrade()

    await connection.run_sync(upgrade)
    plan_path = path.with_name("20260909_0068_market_data_collection_plan.py")
    spec = importlib.util.spec_from_file_location("plan_migration", plan_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    await connection.run_sync(upgrade)
    await connection.execute(
      text(
        "ALTER TABLE market_data_request ADD COLUMN development_only boolean NOT NULL DEFAULT false"
      )
    )
    await connection.execute(
      text("""
      INSERT INTO market_data_request(request_id,device_id,request_payload,status)
      VALUES (:request,:device,CAST(:payload AS json),'QUEUED')
    """),
      {"request": request, "device": device, "payload": json.dumps(payload)},
    )
  assert await first.acquire()
  units = [
    CollectionUnit.from_payload(request, i, unit)
    for i, unit in enumerate(plan_historical_work_units(payload))
  ]
  return first, second, CollectionPermitStore(first), device, units


async def execute(store, sql, params=None):
  async with store.engine.begin() as connection:
    return await connection.execute(text(sql), params or {})


async def ack(store, grant, event, device=None):
  return await store.acknowledge(
    permit_id=str(grant.permit_id),
    device_id=device or str(grant.device_id),
    event=event,
  )


async def test_order_idempotency_and_completed_unit_accounting(permits):
  first, _, store, device, units = permits
  assert await store.issue(device_id=device, unit=units[1]) is None
  grant = await store.issue(device_id=device, unit=units[0])
  assert grant == await store.issue(device_id=device, unit=units[0])
  lease = (
    await execute(first, "SELECT expires_at FROM market_data_worker_lease")
  ).scalar_one()
  assert grant.expires_at <= lease
  with pytest.raises(ValueError, match="has not started"):
    await ack(store, grant, "FINISH")
  assert await ack(store, grant, "START")
  assert not await ack(store, grant, "START")
  assert await store.issue(device_id=device, unit=units[0]) is None
  assert await store.issue(device_id=device, unit=units[1]) is None
  assert await ack(store, grant, "FINISH")
  assert not await ack(store, grant, "FINISH")
  assert (
    await execute(
      first, "SELECT production_streak FROM market_data_collection_schedule"
    )
  ).scalar_one() == 1
  assert await store.issue(device_id=device, unit=units[0]) is None
  assert await store.issue(device_id=device, unit=units[1]) is not None


async def test_expired_unstarted_grant_reissued_without_resetting_unit(permits):
  first, _, store, device, units = permits
  old = await store.issue(device_id=device, unit=units[0])
  # Keep the immutable grant and indexed expiry consistent, without sleeping.
  await execute(
    first,
    """
    UPDATE market_data_collection_permit SET
      expires_at=clock_timestamp()-INTERVAL '1 second',
      permit_payload=jsonb_set(jsonb_set(permit_payload,'{issued_at}',to_jsonb(clock_timestamp()-INTERVAL '20 seconds')),
        '{expires_at}',to_jsonb(clock_timestamp()-INTERVAL '1 second'))
  """,
  )
  with pytest.raises(ValueError, match="expired"):
    await ack(store, old, "START")
  new = await store.issue(device_id=device, unit=units[0])
  assert new.permit_id != old.permit_id and new.unit == old.unit
  assert (
    await execute(
      first, "SELECT production_streak FROM market_data_collection_schedule"
    )
  ).scalar_one() == 0


async def test_started_slot_survives_lease_loss_and_only_new_owner_can_finish(permits):
  first, second, store, device, units = permits
  grant = await store.issue(device_id=device, unit=units[0])
  await ack(store, grant, "START")
  await first.release()
  assert await second.acquire()
  successor = CollectionPermitStore(second)
  with pytest.raises(RuntimeError, match="lease was lost"):
    await ack(store, grant, "FINISH")
  with pytest.raises(RuntimeError, match="lease was lost"):
    await store.issue(device_id=device, unit=units[1])
  await execute(
    first,
    "UPDATE market_data_collection_permit SET expires_at=clock_timestamp()-INTERVAL '1 second'",
  )
  assert await successor.issue(device_id=device, unit=units[1]) is None
  assert await ack(successor, grant, "FINISH")
  new = await successor.issue(device_id=device, unit=units[1])
  assert new.owner_epoch == second.epoch


async def test_device_payload_and_source_status_are_bound(permits):
  first, _, store, device, units = permits
  with pytest.raises(ValueError, match="source identity"):
    await store.issue(device_id=str(uuid4()), unit=units[0])
  wrong = units[0].model_copy(update={"payload_sha256": "f" * 64})
  with pytest.raises(ValueError, match="original request"):
    await store.issue(device_id=device, unit=wrong)
  grant = await store.issue(device_id=device, unit=units[0])
  with pytest.raises(ValueError, match="device mismatch"):
    await ack(store, grant, "START", str(uuid4()))
  await execute(first, "UPDATE market_data_request SET status='UPLOADED'")
  with pytest.raises(ValueError, match="not eligible"):
    await store.issue(device_id=device, unit=units[0])


async def test_database_enforces_one_native_slot_and_completion_resets_dev_streak(
  permits,
):
  first, _, store, device, units = permits
  await execute(first, "UPDATE market_data_request SET development_only=true")
  grant = await store.issue(device_id=device, unit=units[0])
  await execute(first, "UPDATE market_data_collection_schedule SET production_streak=4")
  with pytest.raises(IntegrityError):
    await execute(
      first,
      """
      INSERT INTO market_data_collection_permit
        (permit_id,request_id,unit_id,unit_index,permit_payload,development_only,state,expires_at)
      SELECT :id,request_id,:unit,1,permit_payload,false,'ISSUED',expires_at
      FROM market_data_collection_permit
    """,
      {"id": str(uuid4()), "unit": units[1].unit_id},
    )
  await ack(store, grant, "START")
  await ack(store, grant, "FINISH")
  assert (
    await execute(
      first, "SELECT production_streak FROM market_data_collection_schedule"
    )
  ).scalar_one() == 0


async def test_takeover_cannot_start_previous_owner_grant(permits):
  first, second, store, device, units = permits
  grant = await store.issue(device_id=device, unit=units[0])
  await first.release()
  assert await second.acquire()
  with pytest.raises(ValueError, match="stale"):
    await ack(CollectionPermitStore(second), grant, "START")
  assert (
    await execute(first, "SELECT state FROM market_data_collection_permit")
  ).scalar_one() == "ISSUED"


async def test_changed_request_cannot_redefine_an_already_issued_unit(permits):
  first, _, store, device, units = permits
  await store.issue(device_id=device, unit=units[0])
  payload = {
    "operation": "bars",
    "stock_list": ["000002.SZ"],
    "periods": ["1m"],
    "start_time": "20260901",
    "end_time": "20260903",
  }
  await execute(
    first,
    "UPDATE market_data_request SET request_payload=CAST(:payload AS json)",
    {"payload": json.dumps(payload)},
  )
  new = CollectionUnit.from_payload(
    str(units[0].request_id), 0, plan_historical_work_units(payload)[0]
  )
  with pytest.raises(ValueError, match="changed after permission"):
    await store.issue(device_id=device, unit=new)


async def test_losing_owner_at_commit_rolls_back_completion_and_counter(
  permits, monkeypatch
):
  first, _, store, device, units = permits
  grant = await store.issue(device_id=device, unit=units[0])
  await ack(store, grant, "START")
  guard = first._guard_ingestion_owner
  calls = 0

  async def lose_on_final_check(connection, **kwargs):
    nonlocal calls
    calls += 1
    if calls == 3:
      raise RuntimeError("worker lease was lost")
    await guard(connection, **kwargs)

  monkeypatch.setattr(first, "_guard_ingestion_owner", lose_on_final_check)
  with pytest.raises(RuntimeError, match="lease was lost"):
    await ack(store, grant, "FINISH")
  assert (
    await execute(first, "SELECT state FROM market_data_collection_permit")
  ).scalar_one() == "STARTED"
  assert (
    await execute(
      first, "SELECT production_streak FROM market_data_collection_schedule"
    )
  ).scalar_one() == 0


async def test_plan_progress_rolls_back_with_native_completion_on_lease_loss(
  permits, monkeypatch
):
  first, _, store, device, units = permits
  grant = await store.issue(device_id=device, unit=units[0])
  await ack(store, grant, "START")
  guard = first._guard_ingestion_owner
  checks = 0

  async def lose_on_commit(connection, **kwargs):
    nonlocal checks
    checks += 1
    if checks == 3:
      raise RuntimeError("worker lease was lost")
    await guard(connection, **kwargs)

  monkeypatch.setattr(first, "_guard_ingestion_owner", lose_on_commit)
  with pytest.raises(RuntimeError, match="lease was lost"):
    await ack(store, grant, "FINISH")
  assert (
    await execute(first, "SELECT next_unit_index FROM market_data_collection_plan")
  ).scalar_one() == 0
  monkeypatch.setattr(first, "_guard_ingestion_owner", guard)
  assert await ack(store, grant, "FINISH")
  assert not await ack(store, grant, "FINISH")
  assert (
    await execute(first, "SELECT next_unit_index FROM market_data_collection_plan")
  ).scalar_one() == 1
