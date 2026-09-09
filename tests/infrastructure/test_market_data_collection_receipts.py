"""Receipt ingress and Worker transitions on local temporary PostgreSQL tables."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from quantx_contracts.collection_receipt import (
  CollectionAbort,
  CollectionCompletion,
  CollectionReceipt,
)
from quantx_infrastructure.services.market_data_collection_receipt_store import (
  CollectionReceiptConflict,
  CollectionReceiptStore,
)
from sqlalchemy import text

from tests.infrastructure.test_market_data_collection_permits import (  # noqa: F401
  durable_store,
  execute,
  permits,
  workers,
)
from tests.infrastructure.test_market_gateway_auth import gateway_auth  # noqa: F401


def abort_migration(sync):
  path = (
    Path(__file__).resolve().parents[2]
    / "packages/infrastructure/alembic/versions/20260910_0075_market_data_collection_abort.py"
  )
  spec = importlib.util.spec_from_file_location("abort_migration", path)
  migration = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(migration)
  migration.op = Operations(MigrationContext.configure(sync))
  return migration


@pytest.fixture
async def receipts(permits):  # noqa: F811
  first, second, grants, device, units = permits
  path = (
    Path(__file__).resolve().parents[2]
    / "packages/infrastructure/alembic/versions/20260910_0071_market_data_collection_receipt.py"
  )
  spec = importlib.util.spec_from_file_location("receipt_migration", path)
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
    abort_migration(sync).upgrade()

  async with first.engine.begin() as connection:
    await connection.run_sync(upgrade)
  grant = await grants.issue(device_id=device, unit=units[0])
  return first, second, CollectionReceiptStore(first.engine), grant


def receipt(grant, event):
  return CollectionReceipt(
    event=event,
    completion=(
      CollectionCompletion(
        unit=grant.unit, sha256="a" * 64, byte_count=100, record_count=0
      )
      if event == "FINISH"
      else None
    ),
    abort=CollectionAbort(
      unit=grant.unit,
      native_exit="CONFIRMED_STOPPED",
      reason_code="COLLECTION_NATIVE_FAILED",
    )
    if event == "ABORT"
    else None,
  )


async def accept(store, grant, event):
  return await store.accept(
    permit_id=str(grant.permit_id),
    device_id=str(grant.device_id),
    receipt=receipt(grant, event),
  )


async def state(worker, grant):
  return (
    await execute(
      worker,
      "SELECT state FROM market_data_collection_permit WHERE permit_id=:id",
      {"id": str(grant.permit_id)},
    )
  ).scalar_one()


async def test_received_start_is_pending_until_worker_commits(receipts):
  first, _, store, grant = receipts
  assert (await accept(store, grant, "START")).status == "PENDING"
  assert await state(first, grant) == "ISSUED"
  for _ in range(2):
    result = await store.status(
      permit_id=str(grant.permit_id), device_id=str(grant.device_id), event="START"
    )
    assert result.status == "PENDING"
  assert await first.consume_collection_receipts() == 1
  assert await state(first, grant) == "STARTED"
  assert (await accept(store, grant, "START")).status == "ACCEPTED"
  assert await first.consume_collection_receipts() == 0


@pytest.mark.parametrize("started", [False, True])
async def test_abort_releases_slot_without_counting_completion(receipts, started):
  import json

  from quantx_infrastructure.services.market_data_collection_permit_store import (
    CollectionPermitStore,
  )

  first, _, store, grant = receipts
  if started:
    await accept(store, grant, "START")
    await store.consume(first)
  assert (await accept(store, grant, "ABORT")).status == "PENDING"
  assert await state(first, grant) == ("STARTED" if started else "ISSUED")
  assert await store.consume(first) == 1
  assert await state(first, grant) == "ABORTED"
  row = (
    await execute(
      first,
      "SELECT status,processing_error FROM market_data_request WHERE request_id=:id",
      {"id": str(grant.unit.request_id)},
    )
  ).one()
  assert row.status == "FAILED"
  assert json.loads(row.processing_error) == {
    "reason_code": "COLLECTION_NATIVE_FAILED",
    "collection_permit_id": str(grant.permit_id),
  }
  assert (
    await execute(first, "SELECT next_unit_index FROM market_data_collection_plan")
  ).scalar_one() == 0
  assert (
    await execute(
      first, "SELECT production_streak FROM market_data_collection_schedule"
    )
  ).scalar_one() == 0
  assert (await accept(store, grant, "ABORT")).status == "ACCEPTED"
  assert await store.consume(first) == 0
  # An unrelated queued source can now use the sole native slot.
  new_id = str(uuid4())
  await execute(
    first,
    """INSERT INTO market_data_request(request_id,device_id,request_payload,status)
    SELECT :id,device_id,request_payload,'QUEUED' FROM market_data_request WHERE request_id=:old""",
    {"id": new_id, "old": str(grant.unit.request_id)},
  )
  permit_store = CollectionPermitStore(first)
  await permit_store.register(new_id)
  successor = await permit_store.issue_next(
    device_id=str(grant.device_id), collection_allowed=True, development_allowed=True
  )
  assert str(successor.unit.request_id) == new_id


async def test_conflicting_abort_is_rejected(receipts):
  _, _, store, grant = receipts
  await accept(store, grant, "ABORT")
  changed = receipt(grant, "ABORT").model_dump(mode="json")
  changed["abort"]["reason_code"] = "COLLECTION_RESULT_INVALID"
  with pytest.raises(CollectionReceiptConflict):
    await store.accept(
      permit_id=str(grant.permit_id),
      device_id=str(grant.device_id),
      receipt=CollectionReceipt.model_validate(changed),
    )


async def test_migration_preserves_existing_receipt_idempotency(receipts):
  first, _, store, grant = receipts
  await accept(store, grant, "START")
  async with first.engine.begin() as connection:
    await connection.run_sync(lambda sync: abort_migration(sync).downgrade())
  old = (
    await execute(first, "SELECT payload FROM market_data_collection_receipt")
  ).scalar_one()
  assert "abort" not in old
  async with first.engine.begin() as connection:
    await connection.run_sync(lambda sync: abort_migration(sync).upgrade())
  assert (await accept(store, grant, "START")).status == "PENDING"
  assert await store.consume(first) == 1
  assert await state(first, grant) == "STARTED"


async def test_migration_refuses_to_discard_abort_evidence(receipts):
  first, _, store, grant = receipts
  await accept(store, grant, "ABORT")
  await store.consume(first)
  with pytest.raises(RuntimeError, match="persisted collection abort"):
    async with first.engine.begin() as connection:
      await connection.run_sync(lambda sync: abort_migration(sync).downgrade())
  assert await state(first, grant) == "ABORTED"
  assert (await accept(store, grant, "ABORT")).status == "ACCEPTED"


@pytest.mark.parametrize("status", ["UPLOADED", "PROCESSING", "COMPLETED"])
async def test_abort_cannot_overwrite_uploaded_request(receipts, status):
  first, _, store, grant = receipts
  await accept(store, grant, "START")
  await store.consume(first)
  await accept(store, grant, "ABORT")
  await execute(
    first,
    "UPDATE market_data_request SET status=:status WHERE request_id=:id",
    {"status": status, "id": str(grant.unit.request_id)},
  )
  assert await store.consume(first) == 1
  assert (await accept(store, grant, "ABORT")).status == "REJECTED"
  assert await state(first, grant) == "STARTED"
  assert (
    await execute(
      first,
      "SELECT status FROM market_data_request WHERE request_id=:id",
      {"id": str(grant.unit.request_id)},
    )
  ).scalar_one() == status


async def test_finished_permit_rejects_abort(receipts):
  first, _, store, grant = receipts
  for event in ["START", "FINISH"]:
    await accept(store, grant, event)
    await store.consume(first)
  with pytest.raises(CollectionReceiptConflict):
    await accept(store, grant, "ABORT")
  assert await state(first, grant) == "FINISHED"


async def test_abort_fence_failure_rolls_back_request_and_permit(receipts, monkeypatch):
  first, _, store, grant = receipts
  await accept(store, grant, "ABORT")
  original = first._guard_ingestion_owner

  async def guard(connection):
    await original(connection)
    value = await connection.scalar(
      text("SELECT state FROM market_data_collection_permit")
    )
    if value == "ABORTED":
      raise RuntimeError("lost lease after abort")

  monkeypatch.setattr(first, "_guard_ingestion_owner", guard)
  with pytest.raises(RuntimeError, match="lost lease"):
    await store.consume(first)
  assert await state(first, grant) == "ISSUED"
  assert (
    await execute(
      first,
      "SELECT status FROM market_data_request WHERE request_id=:id",
      {"id": str(grant.unit.request_id)},
    )
  ).scalar_one() == "DELIVERED"
  assert (await accept(store, grant, "ABORT")).status == "PENDING"


async def test_finish_updates_cursor_counter_and_receipt_atomically(receipts):
  first, _, store, grant = receipts
  await accept(store, grant, "START")
  await store.consume(first)
  assert (await accept(store, grant, "FINISH")).status == "PENDING"
  assert await state(first, grant) == "STARTED"
  assert await store.consume(first) == 1
  assert await state(first, grant) == "FINISHED"
  assert (
    await execute(first, "SELECT next_unit_index FROM market_data_collection_plan")
  ).scalar_one() == 1
  assert (
    await execute(
      first, "SELECT production_streak FROM market_data_collection_schedule"
    )
  ).scalar_one() == 1
  assert (await accept(store, grant, "FINISH")).status == "ACCEPTED"
  assert await store.consume(first) == 0


async def test_finish_before_start_cannot_release_native_slot(receipts):
  first, _, store, grant = receipts
  with pytest.raises(CollectionReceiptConflict):
    await accept(store, grant, "FINISH")
  assert await state(first, grant) == "ISSUED"
  assert (
    await execute(first, "SELECT COUNT(*) FROM market_data_collection_receipt")
  ).scalar_one() == 0


async def test_conflicting_duplicate_evidence_is_rejected(receipts):
  first, _, store, grant = receipts
  await accept(store, grant, "START")
  await store.consume(first)
  await accept(store, grant, "FINISH")
  changed = receipt(grant, "FINISH").model_dump(mode="json")
  changed["completion"]["sha256"] = "b" * 64
  with pytest.raises(CollectionReceiptConflict):
    await store.accept(
      permit_id=str(grant.permit_id),
      device_id=str(grant.device_id),
      receipt=CollectionReceipt.model_validate(changed),
    )
  assert await state(first, grant) == "STARTED"


async def test_cross_device_receipts_and_status_are_unavailable(receipts):
  _, _, store, grant = receipts
  with pytest.raises(KeyError):
    await store.accept(
      permit_id=str(grant.permit_id),
      device_id=str(uuid4()),
      receipt=receipt(grant, "START"),
    )
  await accept(store, grant, "START")
  with pytest.raises(KeyError):
    await store.status(
      permit_id=str(grant.permit_id), device_id=str(uuid4()), event="START"
    )


async def test_late_start_is_durably_rejected_without_restarting_attempt(receipts):
  first, _, store, grant = receipts
  await accept(store, grant, "START")
  await execute(
    first,
    """
    UPDATE market_data_collection_permit SET expires_at=clock_timestamp()-INTERVAL '1 second',
      permit_payload=jsonb_set(jsonb_set(permit_payload,'{issued_at}',to_jsonb(clock_timestamp()-INTERVAL '20 seconds')),
        '{expires_at}',to_jsonb(clock_timestamp()-INTERVAL '1 second'))
  """,
  )
  assert await store.consume(first) == 1
  result = await accept(store, grant, "START")
  assert result.status == "REJECTED"
  assert result.reason_code == "COLLECTION_RECEIPT_REJECTED"
  assert await store.consume(first) == 0
  assert await state(first, grant) == "ISSUED"


async def test_old_worker_cannot_process_receipt_and_successor_can_finish(receipts):
  first, second, store, grant = receipts
  await accept(store, grant, "START")
  await store.consume(first)
  await accept(store, grant, "FINISH")
  await first.release()
  assert await second.acquire()
  with pytest.raises(RuntimeError):
    await store.consume(first)
  assert await state(second, grant) == "STARTED"
  assert await store.consume(second) == 1
  assert await state(second, grant) == "FINISHED"


async def test_fence_failure_rolls_back_receipt_and_native_transition(
  receipts, monkeypatch
):
  first, _, store, grant = receipts
  await accept(store, grant, "START")
  original = first._guard_ingestion_owner
  calls = 0

  async def guard(connection):
    nonlocal calls
    calls += 1
    await original(connection)
    if calls == 4:
      raise RuntimeError("lost lease before receipt commit")

  monkeypatch.setattr(first, "_guard_ingestion_owner", guard)
  with pytest.raises(RuntimeError, match="before receipt commit"):
    await store.consume(first)
  assert await state(first, grant) == "ISSUED"
  assert (await accept(store, grant, "START")).status == "PENDING"


async def test_receipt_consumer_runs_while_ingestion_waits(monkeypatch):
  import asyncio

  from quantx_market_data import worker

  entered, consumed, stop = asyncio.Event(), asyncio.Event(), asyncio.Event()

  async def sweep(_):
    entered.set()
    await stop.wait()

  async def consume():
    await entered.wait()
    consumed.set()

  store = SimpleNamespace(
    acquire=AsyncMock(return_value=True),
    release=AsyncMock(),
    consume_collection_receipts=consume,
    dispatch_history_collection=AsyncMock(return_value=None),
  )
  monkeypatch.setattr(worker, "sweep", sweep)
  task = asyncio.create_task(worker.run(store, stop))
  try:
    await asyncio.wait_for(consumed.wait(), 1)
  finally:
    stop.set()
    await asyncio.wait_for(task, 1)


@pytest.mark.parametrize("native_failed", [False, True])
async def test_agent_executor_through_authenticated_api_and_worker(
  receipts,
  gateway_auth,  # noqa: F811
  tmp_path,
  monkeypatch,
  native_failed,
):
  import asyncio

  from httpx import ASGITransport, AsyncClient
  from quantx_infrastructure.auth.tokens import issue_access_token
  from quantx_infrastructure.models.agent_runtime import AgentDevice
  from quantx_market_data import collection_receipts as api
  from quantx_market_data.api import create_app
  from quantx_qmt_agent.collection_execution import (
    CollectionExecution,
    CollectionFailed,
  )
  from quantx_qmt_agent.journal import LocalJournal
  from quantx_qmt_agent.native_unit_artifact import NativeUnitArtifacts

  first, _, store, grant = receipts
  configuration, sessions = gateway_auth
  async with sessions() as session:
    session.add(
      AgentDevice(
        id=str(grant.device_id), user_id="user", name="history", secret_hash="unused"
      )
    )
    await session.commit()
  monkeypatch.setattr(api, "settings", configuration)
  monkeypatch.setattr(api, "AsyncSessionLocal", sessions)
  token, _ = issue_access_token(
    "user", str(grant.device_id), configuration, scopes={"agent:history"}
  )
  trade_token, _ = issue_access_token("user", str(grant.device_id), configuration)
  path = f"/agent/market-data/collection/{grant.permit_id}/receipts"
  app = create_app(store=first, token="internal", reader=object())
  artifact_root = tmp_path / "units"
  artifact_root.mkdir()
  journal = LocalJournal(tmp_path / "native.sqlite")
  executor = CollectionExecution(
    device_id=str(grant.device_id),
    journal=journal,
    artifacts=NativeUnitArtifacts(
      artifact_root, max_bytes=10000, max_record_bytes=1000, max_records=100
    ),
    native_lock=asyncio.Lock(),
    reserve=lambda _: None,
    release=lambda _: None,
    stop_native=lambda _: None,
    abort=AsyncMock(side_effect=AssertionError("successful path cannot abort")),
  )
  payload = {
    "operation": "bars",
    "stock_list": ["000001.SZ"],
    "periods": ["1m"],
    "start_time": "20260901",
    "end_time": "20260901",
  }
  try:
    async with app.router.lifespan_context(app):
      async with AsyncClient(
        transport=ASGITransport(app), base_url="http://test"
      ) as client:
        assert (await client.post(path, json={"event": "START"})).status_code == 401
        client.headers["Authorization"] = "Bearer " + trade_token
        assert (await client.post(path, json={"event": "START"})).status_code in {
          401,
          403,
        }
        client.headers["Authorization"] = "Bearer " + token
        assert (await client.post(path, json={"event": "FINISH"})).status_code == 422
        assert (await client.post(path, content=b" " * 4097)).status_code == 413

        async def acknowledge(fact):
          response = await client.post(path, json=fact.model_dump(mode="json"))
          assert response.status_code == 202
          assert response.json()["status"] == "PENDING"
          # API persistence alone did not authorize this step. Worker approval
          # and the read-only status response are both required by the Agent.
          assert await state(first, grant) == (
            "ISSUED" if fact.event == "START" else "STARTED"
          )
          assert await store.consume(first) == 1
          response = await client.get(path + "/" + fact.event)
          assert response.status_code == 200
          assert response.json()["status"] == "ACCEPTED"

        async def start(value):
          assert value == grant
          await acknowledge(CollectionReceipt(event="START"))

        async def finish(value, artifact):
          assert value == grant
          await acknowledge(
            CollectionReceipt(
              event="FINISH",
              completion=CollectionCompletion(
                unit=artifact.unit,
                sha256=artifact.sha256,
                byte_count=artifact.byte_count,
                record_count=artifact.record_count,
              ),
            )
          )

        async def abort(value, failure):
          assert value == grant
          assert journal.load_collection_abort(grant) == failure
          await acknowledge(CollectionReceipt(event="ABORT", abort=failure))

        executor.abort = abort

        def collect():
          if native_failed:
            raise RuntimeError("native failure")
          yield {"value": 1}

        execution = executor.execute(
          grant,
          server_state="ISSUED",
          unit_payload=payload,
          start=start,
          finish=finish,
          collect=collect,
        )
        if native_failed:
          with pytest.raises(CollectionFailed):
            await execution
          assert await state(first, grant) == "ABORTED"
          assert (await client.get(path + "/ABORT")).json()["status"] == "ACCEPTED"
          return
        artifact = await execution
        assert await state(first, grant) == "FINISHED"
        assert list(executor.artifacts.replay(artifact)) == [{"value": 1}]
        assert (await client.get(path + "/FINISH")).json()["status"] == "ACCEPTED"
  finally:
    journal.connection.close()
