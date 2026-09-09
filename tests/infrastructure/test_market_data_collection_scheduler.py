"""Fairness uses completed native units, including one long production request."""

# ruff: noqa: F811 -- shared pytest fixtures are injected by parameter name

import json
from uuid import uuid4

import pytest
from quantx_infrastructure.services.market_data_collection_permit_store import (
  CollectionPermitStore,
)

from tests.infrastructure.test_market_data_collection_permits import (  # noqa: F401
  ack,
  durable_store,
  execute,
  permits,
  workers,
)


async def add_request(first, device, *, development, days):
  request = str(uuid4())
  payload = {
    "operation": "bars",
    "stock_list": ["000002.SZ"],
    "periods": ["1m"],
    "start_time": "20260901",
    "end_time": f"202609{days:02}",
  }
  await execute(
    first,
    """
    INSERT INTO market_data_request(request_id,device_id,status,request_payload,development_only,created_at)
    VALUES (:request,:device,'QUEUED',CAST(:payload AS json),:development,'2026-09-01')
  """,
    {
      "request": request,
      "device": device,
      "payload": json.dumps(payload),
      "development": development,
    },
  )
  return request


async def finish_next(store, device, *, development=True):
  grant = await store.issue_next(
    device_id=device, collection_allowed=True, development_allowed=development
  )
  assert grant is not None
  assert await ack(store, grant, "START")
  assert await ack(store, grant, "FINISH")
  return grant


async def test_long_request_yields_after_four_completed_units_across_takeover(permits):
  first, second, store, device, _ = permits
  production = await add_request(first, device, development=False, days=6)
  development = await add_request(first, device, development=True, days=2)
  await store.register(production)
  await store.register(development)
  granted = [await finish_next(store, device) for _ in range(2)]
  await first.release()
  assert await second.acquire()
  resumed = CollectionPermitStore(second)
  granted.extend([await finish_next(resumed, device) for _ in range(4)])
  assert [str(g.unit.request_id) for g in granted] == [production] * 4 + [
    development,
    production,
  ]
  assert [g.unit.unit_index for g in granted] == [0, 1, 2, 3, 0, 4]
  assert (
    await execute(
      second, "SELECT production_streak FROM market_data_collection_schedule"
    )
  ).scalar_one() == 1


async def test_window_closure_keeps_development_due_and_qos_stops_both(permits):
  first, _, store, device, _ = permits
  production = await add_request(first, device, development=False, days=6)
  development = await add_request(first, device, development=True, days=1)
  for request in (production, development):
    await store.register(request)
  for _ in range(4):
    await finish_next(store, device)
  assert (
    await store.issue_next(
      device_id=device, collection_allowed=False, development_allowed=True
    )
    is None
  )
  grant = await finish_next(store, device, development=False)
  assert str(grant.unit.request_id) == production and grant.unit.unit_index == 4
  assert (
    await execute(
      first, "SELECT production_streak FROM market_data_collection_schedule"
    )
  ).scalar_one() == 4
  assert str((await finish_next(store, device)).unit.request_id) == development


async def test_dev_does_not_wait_for_nonexistent_production_and_completed_plan_is_skipped(
  permits,
):
  first, _, store, device, _ = permits
  development = await add_request(first, device, development=True, days=1)
  await store.register(development)
  assert str((await finish_next(store, device)).unit.request_id) == development
  assert (
    await store.issue_next(
      device_id=device, collection_allowed=True, development_allowed=True
    )
    is None
  )
  production = await add_request(first, device, development=False, days=1)
  await store.register(production)
  assert str((await finish_next(store, device)).unit.request_id) == production
  # No upload/coverage completion is inferred from native completion.
  assert (
    await execute(
      first,
      "SELECT status FROM market_data_request WHERE request_id=:id",
      {"id": production},
    )
  ).scalar_one() == "DELIVERED"


@pytest.mark.parametrize(
  "reason",
  [
    "DEPENDENCY_QUERY_CAPACITY_BLOCKED",
    "DEPENDENCY_AUTH_BLOCKED",
    "DEPENDENCY_WRITE_CAPACITY_BLOCKED",
    "DEPENDENCY_READBACK_UNAVAILABLE",
    "DEPENDENCY_WRITE_UNAVAILABLE",
  ],
)
async def test_global_dependency_blocks_new_units_for_every_request(permits, reason):
  first, _, store, device, units = permits
  await store.register(str(units[0].request_id))
  await execute(
    first,
    """
    UPDATE market_data_request SET status='BLOCKED',ingestion_progress=CAST(:progress AS json)
    WHERE request_id='request-1'
  """,
    {"progress": json.dumps({"reason_code": reason})},
  )
  assert (
    await store.issue_next(
      device_id=device, collection_allowed=True, development_allowed=True
    )
    is None
  )
  assert (
    await execute(first, "SELECT count(*) FROM market_data_collection_permit")
  ).scalar_one() == 0


async def test_plan_freezes_full_request_before_any_grant_and_cancel_is_excluded(
  permits,
):
  first, _, store, device, units = permits
  request = str(units[0].request_id)
  await store.register(request)
  await execute(
    first,
    "UPDATE market_data_request SET request_payload=jsonb_set(request_payload::jsonb,'{end_time}','\"20260902\"') WHERE request_id=:id",
    {"id": request},
  )
  with pytest.raises(ValueError, match="changed after plan registration"):
    await store.issue_next(
      device_id=device, collection_allowed=True, development_allowed=True
    )
  await execute(
    first,
    "UPDATE market_data_request SET status='CANCELLED' WHERE request_id=:id",
    {"id": request},
  )
  assert (
    await store.issue_next(
      device_id=device, collection_allowed=True, development_allowed=True
    )
    is None
  )


async def test_registered_plan_version_cannot_change_without_reconciliation(permits):
  first, _, store, device, units = permits
  await store.register(str(units[0].request_id))
  await execute(
    first, "UPDATE market_data_collection_plan SET plan_version='unknown-version'"
  )
  with pytest.raises(ValueError, match="changed after plan registration"):
    await store.issue_next(
      device_id=device, collection_allowed=True, development_allowed=True
    )
  assert (
    await execute(first, "SELECT count(*) FROM market_data_collection_permit")
  ).scalar_one() == 0


async def test_full_request_slots_allow_continuation_but_not_a_third_request(permits):
  first, _, store, device, units = permits
  original = str(units[0].request_id)
  await store.register(original)
  grant = await finish_next(store, device)
  assert str(grant.unit.request_id) == original
  other = await add_request(first, device, development=False, days=1)
  await execute(
    first,
    "UPDATE market_data_request SET status='UPLOADED' WHERE request_id=:id",
    {"id": other},
  )
  development = await add_request(first, device, development=True, days=1)
  await store.register(development)
  await execute(first, "UPDATE market_data_collection_schedule SET production_streak=4")
  # Development is due but cannot create a third retained request. The admitted
  # production request must be able to finish and release capacity.
  grant = await finish_next(store, device)
  assert str(grant.unit.request_id) == original and grant.unit.unit_index == 1
  await execute(
    first,
    "UPDATE market_data_request SET status='COMPLETED' WHERE request_id=:id",
    {"id": other},
  )
  assert str((await finish_next(store, device)).unit.request_id) == development


async def test_requeued_request_keeps_its_admission_and_low_disk_issues_nothing(
  permits, monkeypatch
):
  first, _, store, device, units = permits
  await store.register(str(units[0].request_id))
  await finish_next(store, device)
  await execute(
    first,
    "UPDATE market_data_request SET status='QUEUED' WHERE request_id=:id",
    {"id": str(units[0].request_id)},
  )
  other = await add_request(first, device, development=False, days=1)
  await execute(
    first,
    "UPDATE market_data_request SET status='UPLOADED' WHERE request_id=:id",
    {"id": other},
  )
  third = await add_request(first, device, development=False, days=1)
  await store.register(third)
  # Low-level issuance also enforces the same slot bound.
  from quantx_contracts.collection_permit import (
    CollectionUnit,
    plan_historical_work_units,
  )

  payload = {
    "operation": "bars",
    "stock_list": ["000002.SZ"],
    "periods": ["1m"],
    "start_time": "20260901",
    "end_time": "20260901",
  }
  assert (
    await store.issue(
      device_id=device,
      unit=CollectionUnit.from_payload(
        third, 0, plan_historical_work_units(payload)[0]
      ),
    )
    is None
  )
  monkeypatch.setattr(
    "quantx_infrastructure.services.market_data_capacity.staging_free_bytes",
    lambda root: 0,
  )
  assert (
    await store.issue_next(
      device_id=device, collection_allowed=True, development_allowed=True
    )
    is None
  )
  assert (
    await execute(
      first, "SELECT count(*) FROM market_data_collection_permit WHERE state='ISSUED'"
    )
  ).scalar_one() == 0


async def test_failed_requests_release_pipeline_slots_but_files_remain_budgeted(
  permits,
  monkeypatch,
):
  first, _, store, device, units = permits
  for _ in range(2):
    failed = await add_request(first, device, development=False, days=1)
    await execute(
      first,
      "UPDATE market_data_request SET status='FAILED',received_chunks=1 WHERE request_id=:id",
      {"id": failed},
    )
    directory = store.staging_root / failed
    directory.mkdir()
    (directory / "chunk").write_bytes(b"x")
  await store.register(str(units[0].request_id))
  from quantx_infrastructure.services import market_data_capacity as capacity

  monkeypatch.setattr(
    capacity,
    "MAX_MARKET_DATA_STAGING_BYTES",
    capacity.MAX_MARKET_DATA_REQUEST_COMPRESSED_BYTES,
  )
  assert (
    await store.issue_next(
      device_id=device, collection_allowed=True, development_allowed=True
    )
    is None
  )
  monkeypatch.setattr(
    capacity,
    "MAX_MARKET_DATA_STAGING_BYTES",
    capacity.MAX_MARKET_DATA_REQUEST_COMPRESSED_BYTES + 2,
  )
  assert (
    await store.issue_next(
      device_id=device, collection_allowed=True, development_allowed=True
    )
    is not None
  )
