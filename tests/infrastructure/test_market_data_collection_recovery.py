"""Explicit recovery keeps native evidence and waits for a new Worker permit."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from httpx import ASGITransport, AsyncClient
from quantx_contracts.collection_receipt import CollectionCompletion, CollectionReceipt
from quantx_infrastructure.services.market_data_collection_permit_store import (
  CollectionPermitStore,
)
from quantx_market_data.api import create_app

from tests.infrastructure.test_market_data_collection_receipts import (  # noqa: F401
  accept,
  durable_store,
  execute,
  permits,
  receipts,
  state,
  workers,
)


def migration(sync):
  path = (
    Path(__file__).resolve().parents[2]
    / "packages/infrastructure/alembic/versions/20260910_0076_market_data_collection_recovery.py"
  )
  spec = importlib.util.spec_from_file_location("collection_recovery_migration", path)
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  module.op = Operations(MigrationContext.configure(sync))
  return module


@pytest.fixture
async def recoverable(receipts):  # noqa: F811
  first, _, store, grant = receipts
  await execute(first, "CREATE TEMP TABLE market_data_transfer(request_id varchar(36))")
  return first, store, grant


async def failed(first, store, grant):
  await accept(store, grant, "START")
  await store.consume(first)
  await accept(store, grant, "ABORT")
  await store.consume(first)


async def test_explicit_recovery_preserves_request_and_audits_original_abort(
  recoverable,
):
  first, store, grant = recoverable
  await failed(first, store, grant)
  before = (
    await execute(
      first,
      "SELECT request_payload FROM market_data_request WHERE request_id=:id",
      {"id": str(grant.unit.request_id)},
    )
  ).scalar_one()
  assert (
    await CollectionPermitStore(first).issue_next(
      device_id=str(grant.device_id), collection_allowed=True, development_allowed=True
    )
    is None
  )
  app = create_app(store=first, token="internal", reader=object())
  async with (
    app.router.lifespan_context(app),
    AsyncClient(transport=ASGITransport(app), base_url="http://test") as client,
  ):
    path = f"/market-data/internal/v1/requests/{grant.unit.request_id}/resume"
    assert (
      await client.post(path, json={"reason": "repaired native source"})
    ).status_code == 401
    client.headers["Authorization"] = "Bearer internal"
    response = await client.post(path, json={"reason": "repaired native source"})
    assert response.json() == {
      "request_id": str(grant.unit.request_id),
      "status": "QUEUED",
      "attempt": 2,
    }
    assert (await client.post(path, json={"reason": "duplicate"})).status_code == 409
  row = (
    await execute(
      first, "SELECT state,resumed_at,resume_reason FROM market_data_collection_permit"
    )
  ).one()
  assert (
    row.state == "ABORTED"
    and row.resumed_at is not None
    and row.resume_reason == "repaired native source"
  )
  assert (await accept(store, grant, "ABORT")).status == "ACCEPTED"
  successor = await CollectionPermitStore(first).issue_next(
    device_id=str(grant.device_id), collection_allowed=True, development_allowed=True
  )
  assert successor.unit == grant.unit and successor.permit_id != grant.permit_id
  assert (
    await execute(
      first,
      "SELECT request_payload FROM market_data_request WHERE request_id=:id",
      {"id": str(grant.unit.request_id)},
    )
  ).scalar_one() == before
  assert (
    await execute(first, "SELECT next_unit_index FROM market_data_collection_plan")
  ).scalar_one() == 0
  assert (
    await execute(
      first, "SELECT production_streak FROM market_data_collection_schedule"
    )
  ).scalar_one() == 0


@pytest.mark.parametrize(
  "invalid",
  [
    "pending",
    "rejected",
    "unknown",
    "uploaded",
    "transfer",
    "wrong-identity",
    "missing-failure",
  ],
)
async def test_recovery_rejects_unconfirmed_or_conflicting_evidence(
  recoverable, invalid
):
  first, store, grant = recoverable
  await failed(first, store, grant)
  if invalid == "pending":
    await execute(
      first,
      "UPDATE market_data_collection_receipt SET processed_at=NULL WHERE event='ABORT'",
    )
  elif invalid == "rejected":
    await execute(
      first,
      "UPDATE market_data_collection_receipt SET reason_code='COLLECTION_RECEIPT_REJECTED' WHERE event='ABORT'",
    )
  elif invalid == "unknown":
    await execute(
      first, "UPDATE market_data_collection_permit SET state='STARTED',finished_at=NULL"
    )
  elif invalid == "uploaded":
    await execute(
      first,
      "UPDATE market_data_request SET received_chunks=1 WHERE request_id=:id",
      {"id": str(grant.unit.request_id)},
    )
  elif invalid == "transfer":
    await execute(
      first,
      "INSERT INTO market_data_transfer VALUES (:id)",
      {"id": str(grant.unit.request_id)},
    )
  elif invalid == "wrong-identity":
    await execute(
      first,
      "UPDATE market_data_collection_receipt SET payload=jsonb_set(payload,'{abort,reason_code}',to_jsonb('COLLECTION_RESULT_INVALID'::text)) WHERE event='ABORT'",
    )
  else:
    await execute(
      first,
      "UPDATE market_data_request SET processing_error='native failed' WHERE request_id=:id",
      {"id": str(grant.unit.request_id)},
    )
  with pytest.raises(RuntimeError):
    await first.resume_market_data_request(str(grant.unit.request_id), reason="repair")
  assert (
    await execute(
      first,
      "SELECT status FROM market_data_request WHERE request_id=:id",
      {"id": str(grant.unit.request_id)},
    )
  ).scalar_one() == "FAILED"
  assert (
    await execute(first, "SELECT resumed_at FROM market_data_collection_permit")
  ).scalar_one() is None


async def test_recovery_and_migration_keep_failure_audit(recoverable):
  first, store, grant = recoverable
  # No recovery evidence exists yet, so a round trip is safe.
  async with first.engine.begin() as connection:
    await connection.run_sync(lambda sync: migration(sync).downgrade())
    await connection.run_sync(lambda sync: migration(sync).upgrade())
  await failed(first, store, grant)
  await first.resume_market_data_request(str(grant.unit.request_id), reason="fixed")
  with pytest.raises(RuntimeError, match="persisted collection recovery"):
    async with first.engine.begin() as connection:
      await connection.run_sync(lambda sync: migration(sync).downgrade())
  assert (
    await execute(first, "SELECT resume_reason FROM market_data_collection_permit")
  ).scalar_one() == "fixed"


async def test_original_native_failure_then_explicit_recovery_finishes_new_permit(
  recoverable, tmp_path
):
  import asyncio

  from quantx_qmt_agent.collection_execution import (
    CollectionExecution,
    CollectionFailed,
  )
  from quantx_qmt_agent.journal import LocalJournal
  from quantx_qmt_agent.native_unit_artifact import NativeUnitArtifacts

  first, store, grant = recoverable
  journal = LocalJournal(tmp_path / "journal.sqlite")
  directory = tmp_path / "units"
  directory.mkdir()

  async def confirm(permit, fact):
    await store.accept(
      permit_id=str(permit.permit_id), device_id=str(permit.device_id), receipt=fact
    )
    await store.consume(first)
    assert (
      await store.status(
        permit_id=str(permit.permit_id),
        device_id=str(permit.device_id),
        event=fact.event,
      )
    ).status == "ACCEPTED"

  async def start(permit):
    await confirm(permit, CollectionReceipt(event="START"))

  async def abort(permit, failure):
    await confirm(permit, CollectionReceipt(event="ABORT", abort=failure))

  async def finish(permit, artifact):
    await confirm(
      permit,
      CollectionReceipt(
        event="FINISH",
        completion=CollectionCompletion(
          unit=permit.unit,
          sha256=artifact.sha256,
          byte_count=artifact.byte_count,
          record_count=artifact.record_count,
        ),
      ),
    )

  executor = CollectionExecution(
    device_id=str(grant.device_id),
    journal=journal,
    artifacts=NativeUnitArtifacts(
      directory, max_bytes=10000, max_record_bytes=1000, max_records=100
    ),
    native_lock=asyncio.Lock(),
    reserve=Mock(),
    release=Mock(),
    stop_native=Mock(),
    abort=abort,
  )
  payload = {
    "operation": "bars",
    "stock_list": ["000001.SZ"],
    "periods": ["1m"],
    "start_time": "20260901",
    "end_time": "20260901",
  }
  try:
    with pytest.raises(CollectionFailed):
      await executor.execute(
        grant,
        server_state="ISSUED",
        unit_payload=payload,
        start=start,
        finish=finish,
        collect=Mock(side_effect=RuntimeError),
      )
    assert await state(first, grant) == "ABORTED"
    await first.resume_market_data_request(
      str(grant.unit.request_id), reason="native repaired"
    )
    replacement = await CollectionPermitStore(first).issue_next(
      device_id=str(grant.device_id), collection_allowed=True, development_allowed=True
    )
    artifact = await executor.execute(
      replacement,
      server_state="ISSUED",
      unit_payload=payload,
      start=start,
      finish=finish,
      collect=lambda: iter([{"value": 1}]),
    )
    assert await state(first, replacement) == "FINISHED"
    assert list(executor.artifacts.replay(artifact)) == [{"value": 1}]
    assert journal.load_collection_abort(grant) is not None
    assert (
      await execute(first, "SELECT next_unit_index FROM market_data_collection_plan")
    ).scalar_one() == 1
    assert (
      await execute(
        first, "SELECT production_streak FROM market_data_collection_schedule"
      )
    ).scalar_one() == 1
  finally:
    journal.connection.close()


async def test_requeue_failure_rolls_back_recovery_audit(recoverable):
  from sqlalchemy.exc import DBAPIError

  first, store, grant = recoverable
  await failed(first, store, grant)
  await execute(
    first,
    """
    CREATE FUNCTION pg_temp.reject_collection_resume() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.status='QUEUED' THEN RAISE EXCEPTION 'injected requeue failure'; END IF;
      RETURN NEW;
    END $$
  """,
  )
  await execute(
    first,
    "CREATE TRIGGER reject_resume BEFORE UPDATE ON market_data_request FOR EACH ROW EXECUTE FUNCTION pg_temp.reject_collection_resume()",
  )
  with pytest.raises(DBAPIError, match="injected requeue failure"):
    await first.resume_market_data_request(str(grant.unit.request_id), reason="fixed")
  assert (
    await execute(first, "SELECT resumed_at FROM market_data_collection_permit")
  ).scalar_one() is None
  assert (
    await execute(
      first,
      "SELECT status FROM market_data_request WHERE request_id=:id",
      {"id": str(grant.unit.request_id)},
    )
  ).scalar_one() == "FAILED"


@pytest.mark.parametrize("reason", ["XTDATA_UNAVAILABLE", "COLLECTION_RESULT_INVALID"])
async def test_dependency_pause_survives_owner_change_and_requires_explicit_resume(
  recoverable, receipts, reason  # noqa: F811
):
  from uuid import uuid4

  from quantx_contracts.collection_permit import CollectionUnit
  from quantx_contracts.collection_receipt import CollectionAbort

  first, store, grant = recoverable
  second = receipts[1]
  await accept(store, grant, "START")
  await store.consume(first)
  await store.accept(
    permit_id=str(grant.permit_id),
    device_id=str(grant.device_id),
    receipt=CollectionReceipt(
      event="ABORT",
      abort=CollectionAbort(
        unit=grant.unit, native_exit="CONFIRMED_STOPPED", reason_code=reason
      ),
    ),
  )
  await store.consume(first)
  other = str(uuid4())
  app = create_app(store=first, token="internal", reader=object())
  async with (
    app.router.lifespan_context(app),
    AsyncClient(
      transport=ASGITransport(app),
      base_url="http://test",
      headers={"Authorization": "Bearer internal"},
    ) as client,
  ):
    response = await client.get(
      f"/market-data/internal/v1/requests/{grant.unit.request_id}"
    )
    assert response.status_code == 200
    assert response.json()["reason_code"] == reason
    assert "processing_error" not in response.json()
  await execute(
    first,
    """INSERT INTO market_data_request(request_id,device_id,request_payload,status)
    SELECT :id,device_id,request_payload,'QUEUED' FROM market_data_request WHERE request_id=:original""",
    {"id": other, "original": str(grant.unit.request_id)},
  )
  await first.release()
  assert await second.acquire()
  permit_store = CollectionPermitStore(second)
  await permit_store.register(other)
  unit = CollectionUnit.from_payload(
    other,
    0,
    {
      "operation": "bars",
      "stock_list": ["000001.SZ"],
      "periods": ["1m"],
      "start_time": "20260901",
      "end_time": "20260901",
    },
  )
  if reason == "XTDATA_UNAVAILABLE":
    assert await permit_store.issue(device_id=str(grant.device_id), unit=unit) is None
    assert (
      await permit_store.issue_next(
        device_id=str(grant.device_id),
        collection_allowed=True,
        development_allowed=True,
      )
      is None
    )
    assert (
      await execute(
        second,
        "SELECT status FROM market_data_request WHERE request_id=:id",
        {"id": other},
      )
    ).scalar_one() == "QUEUED"
    # Recovery submission is explicit and works even though the original owner has stopped.
    await first.resume_market_data_request(
      str(grant.unit.request_id), reason="XTData connection repaired"
    )
  successor = await permit_store.issue_next(
    device_id=str(grant.device_id), collection_allowed=True, development_allowed=True
  )
  assert successor is not None
  assert await state(second, grant) == "ABORTED"
