"""Exercise the actual journal/file/ack sequence without the Windows SDK."""

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from quantx_contracts.collection_permit import CollectionPermit, CollectionUnit
from quantx_qmt_agent.collection_execution import (
  CollectionExecution,
  CollectionFailed,
  CollectionOutcomeUnknown,
)
from quantx_qmt_agent.journal import LocalJournal
from quantx_qmt_agent.native_unit_artifact import NativeUnitArtifacts

NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)
PAYLOAD = {"operation": "bars", "stock_list": ["000001.SZ"], "periods": ["tick"]}


@pytest.fixture
def execution(tmp_path):
  journal = LocalJournal(tmp_path / "journal.sqlite")
  root = tmp_path / "units"
  root.mkdir()
  artifacts = NativeUnitArtifacts(
    root, max_bytes=10000, max_record_bytes=1000, max_records=10
  )
  permit = CollectionPermit(
    permit_id=uuid4(),
    device_id=uuid4(),
    owner_epoch=1,
    unit=CollectionUnit.from_payload(str(uuid4()), 0, PAYLOAD),
    issued_at=NOW,
    expires_at=NOW + timedelta(seconds=15),
  )
  runner = CollectionExecution(
    device_id=str(permit.device_id),
    journal=journal,
    artifacts=artifacts,
    native_lock=asyncio.Lock(),
    reserve=Mock(),
    release=Mock(),
    stop_native=Mock(),
    abort=AsyncMock(),
    clock=lambda: NOW,
  )
  yield runner, permit
  journal.connection.close()


async def test_native_entry_and_finish_follow_durable_ack_order(execution):
  runner, permit = execution
  order = []

  async def start(value):
    assert value == permit
    assert not runner.journal.collection_execution_started(permit)
    order.append("start")

  def collect():
    assert runner.journal.collection_execution_started(permit)
    order.append("native")
    yield {"value": 1}

  async def finish(value, artifact):
    assert value == permit
    assert (
      runner.journal.load_collection_artifact(
        device_id=runner.device_id, unit=permit.unit, artifacts=runner.artifacts
      )
      == artifact
    )
    order.append("finish")

  artifact = await runner.execute(
    permit,
    server_state="ISSUED",
    unit_payload=PAYLOAD,
    start=start,
    finish=finish,
    collect=collect,
  )
  assert order == ["start", "native", "finish"]
  assert list(runner.artifacts.replay(artifact)) == [{"value": 1}]


async def test_lost_finish_recovers_original_expired_permit_without_recollection(
  execution,
):
  runner, permit = execution
  collect = Mock(return_value=iter([{"value": 1}]))
  with pytest.raises(ConnectionError):
    await runner.execute(
      permit,
      server_state="ISSUED",
      unit_payload=PAYLOAD,
      start=AsyncMock(),
      finish=AsyncMock(side_effect=ConnectionError),
      collect=collect,
    )
  path = runner.journal.path
  runner.journal.connection.close()
  runner.journal = LocalJournal(path)
  runner.clock = lambda: NOW + timedelta(hours=1)
  start, finish = AsyncMock(), AsyncMock()
  try:
    result = await runner.execute(
      permit,
      server_state="STARTED",
      unit_payload=PAYLOAD,
      start=start,
      finish=finish,
      collect=collect,
    )
    collect.assert_called_once()
    start.assert_not_awaited()
    finish.assert_awaited_once_with(permit, result)
  finally:
    runner.journal.connection.close()


async def test_start_not_confirmed_cannot_enter_native(execution):
  runner, permit = execution
  collect, finish = Mock(), AsyncMock()
  with pytest.raises(ConnectionError):
    await runner.execute(
      permit,
      server_state="ISSUED",
      unit_payload=PAYLOAD,
      start=AsyncMock(side_effect=ConnectionError),
      finish=finish,
      collect=collect,
    )
  assert not runner.journal.collection_execution_started(permit)
  collect.assert_not_called()
  finish.assert_not_awaited()


async def test_expiry_while_waiting_for_start_does_not_enter_native(execution):
  runner, permit = execution
  collect = Mock()

  async def start(_):
    runner.clock = lambda: permit.expires_at

  with pytest.raises(ValueError, match="expired"):
    await runner.execute(
      permit,
      server_state="ISSUED",
      unit_payload=PAYLOAD,
      start=start,
      finish=AsyncMock(),
      collect=collect,
    )
  collect.assert_not_called()
  assert not runner.journal.collection_execution_started(permit)


async def test_expiry_during_thread_scheduling_does_not_enter_native(
  execution, monkeypatch
):
  runner, permit = execution
  collect = Mock()

  async def delayed(function, *args):
    runner.clock = lambda: permit.expires_at
    return function(*args)

  monkeypatch.setattr(runner, "_join_native_thread", delayed)
  with pytest.raises(ValueError, match="expired"):
    await runner.execute(
      permit,
      server_state="ISSUED",
      unit_payload=PAYLOAD,
      start=AsyncMock(),
      finish=AsyncMock(),
      collect=collect,
    )
  collect.assert_not_called()
  assert not runner.journal.collection_execution_started(permit)


async def test_native_failure_is_not_automatically_repeated(execution):
  runner, permit = execution
  collect = Mock(side_effect=RuntimeError("native failed"))
  start, finish = AsyncMock(), AsyncMock()
  with pytest.raises(CollectionFailed):
    await runner.execute(
      permit,
      server_state="ISSUED",
      unit_payload=PAYLOAD,
      start=start,
      finish=finish,
      collect=collect,
    )
  with pytest.raises(CollectionFailed):
    await runner.execute(
      permit,
      server_state="ISSUED",
      unit_payload=PAYLOAD,
      start=start,
      finish=finish,
      collect=collect,
    )
  collect.assert_called_once()
  start.assert_awaited_once()
  finish.assert_not_awaited()


async def test_sealed_file_survives_failure_before_journal_binding(
  execution, monkeypatch
):
  runner, permit = execution
  collect = Mock(return_value=iter([{"value": 1}]))
  original = runner.journal.record_collection_artifact
  monkeypatch.setattr(
    runner.journal, "record_collection_artifact", Mock(side_effect=OSError)
  )
  with pytest.raises(OSError):
    await runner.execute(
      permit,
      server_state="ISSUED",
      unit_payload=PAYLOAD,
      start=AsyncMock(),
      finish=AsyncMock(),
      collect=collect,
    )
  monkeypatch.setattr(runner.journal, "record_collection_artifact", original)
  finish = AsyncMock()
  await runner.execute(
    permit,
    server_state="ISSUED",
    unit_payload=PAYLOAD,
    start=AsyncMock(side_effect=AssertionError),
    finish=finish,
    collect=collect,
  )
  collect.assert_called_once()
  finish.assert_awaited_once()


async def test_completed_but_corrupted_result_never_finishes_or_recollects(execution):
  runner, permit = execution
  result = await runner.execute(
    permit,
    server_state="ISSUED",
    unit_payload=PAYLOAD,
    start=AsyncMock(),
    finish=AsyncMock(),
    collect=lambda: iter([]),
  )
  result.path.write_bytes(b"corrupt")
  collect, finish = Mock(), AsyncMock()
  with pytest.raises(ValueError):
    await runner.execute(
      permit,
      server_state="ISSUED",
      unit_payload=PAYLOAD,
      start=AsyncMock(),
      finish=finish,
      collect=collect,
    )
  finish.assert_not_awaited()
  collect.assert_not_called()


async def test_repeated_cancellation_keeps_native_lock_until_thread_exits(execution):
  runner, permit = execution
  entered, release = threading.Event(), threading.Event()

  def collect():
    entered.set()
    if not release.wait(3):
      raise RuntimeError("test did not release native call")
    yield {"value": 1}

  finish = AsyncMock()
  task = asyncio.create_task(
    runner.execute(
      permit,
      server_state="ISSUED",
      unit_payload=PAYLOAD,
      start=AsyncMock(),
      finish=finish,
      collect=collect,
    )
  )
  try:
    assert await asyncio.to_thread(entered.wait, 2)
    for _ in range(2):
      task.cancel()
      await asyncio.sleep(0)
      assert runner.native_lock.locked()
      assert not task.done()
  finally:
    release.set()
    with pytest.raises(asyncio.CancelledError):
      await task
  assert not runner.native_lock.locked()
  finish.assert_not_awaited()
  # The completed background result remains recoverable after cancellation.
  await runner.execute(
    permit,
    server_state="ISSUED",
    unit_payload=PAYLOAD,
    start=AsyncMock(side_effect=AssertionError),
    finish=finish,
    collect=Mock(side_effect=AssertionError),
  )
  finish.assert_awaited_once()


@pytest.mark.parametrize("change", ["device", "payload"])
async def test_scope_mismatch_has_no_side_effect(execution, change):
  runner, permit = execution
  payload = PAYLOAD
  if change == "device":
    runner.device_id = str(uuid4())
  else:
    payload = {**PAYLOAD, "download": False}
  start, collect = AsyncMock(), Mock()
  with pytest.raises(ValueError, match="scope mismatch"):
    await runner.execute(
      permit,
      server_state="ISSUED",
      unit_payload=payload,
      start=start,
      finish=AsyncMock(),
      collect=collect,
    )
  start.assert_not_awaited()
  collect.assert_not_called()


def test_two_journal_connections_cannot_enter_same_unit_twice(execution):
  runner, permit = execution
  runner.journal.accept_collection_permit(
    permit, device_id=runner.device_id, unit=permit.unit, now=NOW
  )
  other = LocalJournal(runner.journal.path)
  try:
    runner.journal.begin_collection_execution(
      permit, device_id=runner.device_id, now=NOW
    )
    with pytest.raises(ValueError, match="already entered"):
      other.begin_collection_execution(permit, device_id=runner.device_id, now=NOW)
  finally:
    other.connection.close()


async def test_new_epoch_received_during_start_wait_fences_native_entry(execution):
  runner, permit = execution
  newer = permit.model_copy(update={"permit_id": uuid4(), "owner_epoch": 2})

  async def start(_):
    runner.journal.accept_collection_permit(
      newer, device_id=runner.device_id, unit=newer.unit, now=NOW
    )

  collect = Mock()
  with pytest.raises(ValueError, match="owner is stale"):
    await runner.execute(
      permit,
      server_state="ISSUED",
      unit_payload=PAYLOAD,
      start=start,
      finish=AsyncMock(),
      collect=collect,
    )
  collect.assert_not_called()
  assert not runner.journal.collection_execution_started(permit)


async def test_new_permit_cannot_replace_original_native_execution(execution):
  runner, permit = execution
  await runner.execute(
    permit,
    server_state="ISSUED",
    unit_payload=PAYLOAD,
    start=AsyncMock(),
    finish=AsyncMock(),
    collect=lambda: iter([]),
  )
  newer = permit.model_copy(update={"permit_id": uuid4(), "owner_epoch": 2})
  start, finish, collect = AsyncMock(), AsyncMock(), Mock()
  with pytest.raises(ValueError, match="another authorization"):
    await runner.execute(
      newer,
      server_state="ISSUED",
      unit_payload=PAYLOAD,
      start=start,
      finish=finish,
      collect=collect,
    )
  start.assert_not_awaited()
  finish.assert_not_awaited()
  collect.assert_not_called()


async def test_deleted_completed_file_is_unknown_instead_of_recollected(execution):
  runner, permit = execution
  artifact = await runner.execute(
    permit,
    server_state="ISSUED",
    unit_payload=PAYLOAD,
    start=AsyncMock(),
    finish=AsyncMock(),
    collect=lambda: iter([]),
  )
  artifact.path.unlink()
  collect = Mock()
  with pytest.raises(CollectionOutcomeUnknown) as error:
    await runner.execute(
      permit,
      server_state="ISSUED",
      unit_payload=PAYLOAD,
      start=AsyncMock(),
      finish=AsyncMock(),
      collect=collect,
    )
  assert error.value.reason_code == "COLLECTION_NATIVE_OUTCOME_UNKNOWN"
  collect.assert_not_called()


async def test_server_started_without_local_receipt_never_recollects(execution):
  runner, permit = execution
  start, finish, collect = AsyncMock(), AsyncMock(), Mock()
  with pytest.raises(CollectionOutcomeUnknown, match="without local authorization"):
    await runner.execute(
      permit,
      server_state="STARTED",
      unit_payload=PAYLOAD,
      start=start,
      finish=finish,
      collect=collect,
    )
  start.assert_not_awaited()
  finish.assert_not_awaited()
  collect.assert_not_called()
  assert not runner.journal.collection_permit_received(permit)


async def test_artifact_failure_closes_native_iterator_under_lock(execution):
  runner, permit = execution
  closed = []

  def records():
    try:
      yield {"value": "x" * 2000}
    finally:
      assert runner.native_lock.locked()
      closed.append(True)

  with pytest.raises(CollectionFailed):
    await runner.execute(
      permit,
      server_state="ISSUED",
      unit_payload=PAYLOAD,
      start=AsyncMock(),
      finish=AsyncMock(),
      collect=records,
    )
  assert closed == [True]


async def test_failure_is_durable_before_abort_and_replayed_after_restart(execution):
  runner, permit = execution
  order = []

  def stop(error):
    assert runner.native_lock.locked()
    assert isinstance(error, RuntimeError)
    assert runner.journal.load_collection_abort(permit) is None
    order.append("stopped")

  async def abort(value, failure):
    assert value == permit
    assert runner.journal.load_collection_abort(permit) == failure
    order.append("abort")
    raise ConnectionError("confirmation lost")

  runner.stop_native, runner.abort = (
    Mock(side_effect=stop),
    AsyncMock(side_effect=abort),
  )
  collect = Mock(side_effect=RuntimeError("native failed"))
  with pytest.raises(ConnectionError):
    await runner.execute(
      permit,
      server_state="ISSUED",
      unit_payload=PAYLOAD,
      start=AsyncMock(),
      finish=AsyncMock(),
      collect=collect,
    )
  assert order == ["stopped", "abort"]
  runner.journal.connection.close()
  runner.journal = LocalJournal(runner.journal.path)
  runner.clock = lambda: NOW + timedelta(hours=1)
  runner.abort = AsyncMock()
  try:
    with pytest.raises(CollectionFailed):
      await runner.execute(
        permit,
        server_state="STARTED",
        unit_payload=PAYLOAD,
        start=AsyncMock(side_effect=AssertionError),
        finish=AsyncMock(side_effect=AssertionError),
        collect=collect,
      )
    collect.assert_called_once()
    runner.stop_native.assert_called_once()
    runner.abort.assert_awaited_once()
    facts = runner.journal.request_collection_aborts(
      runner.device_id, str(permit.unit.request_id)
    )
    assert len(facts) == 1 and facts[0][2] is True
  finally:
    runner.journal.connection.close()


async def test_unproven_stop_preserves_unknown_execution(execution):
  runner, permit = execution
  runner.stop_native = Mock(side_effect=RuntimeError("cannot prove exit"))
  collect = Mock(side_effect=ValueError("bad native output"))
  with pytest.raises(RuntimeError, match="cannot prove exit"):
    await runner.execute(
      permit,
      server_state="ISSUED",
      unit_payload=PAYLOAD,
      start=AsyncMock(),
      finish=AsyncMock(),
      collect=collect,
    )
  assert runner.journal.load_collection_abort(permit) is None
  with pytest.raises(CollectionOutcomeUnknown):
    await runner.execute(
      permit,
      server_state="STARTED",
      unit_payload=PAYLOAD,
      start=AsyncMock(),
      finish=AsyncMock(),
      collect=collect,
    )
  runner.stop_native.assert_called_once()
  runner.abort.assert_not_awaited()
  collect.assert_called_once()


async def test_failure_journal_error_never_sends_abort(execution, monkeypatch):
  runner, permit = execution
  monkeypatch.setattr(
    runner.journal, "record_collection_abort", Mock(side_effect=OSError("disk full"))
  )
  with pytest.raises(OSError, match="disk full"):
    await runner.execute(
      permit,
      server_state="ISSUED",
      unit_payload=PAYLOAD,
      start=AsyncMock(),
      finish=AsyncMock(),
      collect=Mock(side_effect=RuntimeError),
    )
  runner.stop_native.assert_called_once()
  runner.abort.assert_not_awaited()
  assert runner.journal.load_collection_abort(permit) is None


async def test_published_file_survives_post_publication_failure(execution, monkeypatch):
  runner, permit = execution
  original = runner.artifacts.seal

  def seal(*args, **kwargs):
    original(*args, **kwargs)
    raise OSError("directory fsync failed")

  monkeypatch.setattr(runner.artifacts, "seal", seal)
  finish = AsyncMock()
  with pytest.raises(OSError, match="directory fsync failed"):
    await runner.execute(
      permit,
      server_state="ISSUED",
      unit_payload=PAYLOAD,
      start=AsyncMock(),
      finish=finish,
      collect=lambda: iter([{"value": 1}]),
    )
  finish.assert_not_awaited()
  artifact = await runner.execute(
    permit,
    server_state="STARTED",
    unit_payload=PAYLOAD,
    start=AsyncMock(side_effect=AssertionError),
    finish=finish,
    collect=Mock(side_effect=AssertionError),
  )
  assert list(runner.artifacts.replay(artifact)) == [{"value": 1}]
  runner.abort.assert_not_awaited()
  finish.assert_awaited_once()
  assert runner.journal.load_collection_abort(permit) is None


async def test_abort_journal_rejects_conflicting_or_successful_evidence(execution):
  from quantx_contracts.collection_receipt import CollectionAbort

  runner, permit = execution
  failure = CollectionAbort(
    unit=permit.unit,
    native_exit="CONFIRMED_STOPPED",
    reason_code="COLLECTION_NATIVE_FAILED",
  )
  with pytest.raises(ValueError, match="original execution"):
    runner.journal.record_collection_abort(permit, failure)
  await runner.execute(
    permit,
    server_state="ISSUED",
    unit_payload=PAYLOAD,
    start=AsyncMock(),
    finish=AsyncMock(),
    collect=lambda: iter([]),
  )
  with pytest.raises(ValueError, match="completed artifact"):
    runner.journal.record_collection_abort(permit, failure)


async def test_cancel_during_stop_waits_for_durable_failure(execution):
  runner, permit = execution
  entered, released = threading.Event(), threading.Event()

  def stop(_):
    entered.set()
    if not released.wait(3):
      raise RuntimeError("test did not release stop")

  runner.stop_native = Mock(side_effect=stop)
  collect = Mock(side_effect=RuntimeError("failed"))
  task = asyncio.create_task(
    runner.execute(
      permit,
      server_state="ISSUED",
      unit_payload=PAYLOAD,
      start=AsyncMock(),
      finish=AsyncMock(),
      collect=collect,
    )
  )
  try:
    assert await asyncio.to_thread(entered.wait, 2)
    for _ in range(2):
      task.cancel()
      await asyncio.sleep(0)
      assert runner.native_lock.locked() and not task.done()
  finally:
    released.set()
    with pytest.raises(asyncio.CancelledError):
      await task
  assert runner.journal.load_collection_abort(permit) is not None
  runner.abort.assert_not_awaited()
  with pytest.raises(CollectionFailed):
    await runner.execute(
      permit,
      server_state="STARTED",
      unit_payload=PAYLOAD,
      start=AsyncMock(),
      finish=AsyncMock(),
      collect=collect,
    )
  collect.assert_called_once()
  runner.stop_native.assert_called_once()
  runner.abort.assert_awaited_once()


async def test_recorded_failure_is_immutable_and_excludes_success(execution):
  runner, permit = execution
  with pytest.raises(CollectionFailed):
    await runner.execute(
      permit,
      server_state="ISSUED",
      unit_payload=PAYLOAD,
      start=AsyncMock(),
      finish=AsyncMock(),
      collect=Mock(side_effect=RuntimeError),
    )
  failure = runner.journal.load_collection_abort(permit)
  with pytest.raises(ValueError, match="conflicts with recorded exit"):
    runner.journal.record_collection_abort(
      permit, failure.model_copy(update={"reason_code": "COLLECTION_RESULT_INVALID"})
    )
  with pytest.raises(ValueError, match="original authorization"):
    runner.journal.load_collection_abort(permit.model_copy(update={"owner_epoch": 2}))
  artifact = runner.artifacts.seal(permit.unit, [], reserve=Mock(), release=Mock())
  with pytest.raises(ValueError, match="recorded failure"):
    runner.journal.record_collection_artifact(
      permit_id=str(permit.permit_id), artifacts=runner.artifacts, artifact=artifact
    )
  assert runner.journal.load_collection_abort(permit) == failure


async def test_new_permits_retry_confirmed_failures_without_erasing_attempts(execution):
  runner, original = execution
  permits = [original] + [
    original.model_copy(update={"permit_id": uuid4()}) for _ in range(2)
  ]
  for permit in permits[:2]:
    with pytest.raises(CollectionFailed):
      await runner.execute(
        permit,
        server_state="ISSUED",
        unit_payload=PAYLOAD,
        start=AsyncMock(),
        finish=AsyncMock(),
        collect=Mock(side_effect=RuntimeError),
      )
  finish = AsyncMock()
  artifact = await runner.execute(
    permits[2],
    server_state="ISSUED",
    unit_payload=PAYLOAD,
    start=AsyncMock(),
    finish=finish,
    collect=lambda: iter([{"value": 7}]),
  )
  assert list(runner.artifacts.replay(artifact)) == [{"value": 7}]
  finish.assert_awaited_once()
  rows = runner.journal.connection.execute(
    "SELECT permit_id,attempt_index FROM history_collection_executions ORDER BY attempt_index"
  ).fetchall()
  assert [(row["permit_id"], row["attempt_index"]) for row in rows] == [
    (str(p.permit_id), i) for i, p in enumerate(permits)
  ]
  assert all(runner.journal.load_collection_abort(p) is not None for p in permits[:2])
  assert (
    runner.journal.request_collection_aborts(
      runner.device_id, str(original.unit.request_id)
    )
    == []
  )
  assert runner.journal.collection_execution_started(permits[2])


async def test_unconfirmed_abort_does_not_allow_replacement(execution):
  runner, permit = execution
  runner.abort = AsyncMock(side_effect=ConnectionError)
  with pytest.raises(ConnectionError):
    await runner.execute(
      permit,
      server_state="ISSUED",
      unit_payload=PAYLOAD,
      start=AsyncMock(),
      finish=AsyncMock(),
      collect=Mock(side_effect=RuntimeError),
    )
  next_permit = permit.model_copy(update={"permit_id": uuid4()})
  collect, start = Mock(), AsyncMock()
  with pytest.raises(ValueError, match="another authorization"):
    await runner.execute(
      next_permit,
      server_state="ISSUED",
      unit_payload=PAYLOAD,
      start=start,
      finish=AsyncMock(),
      collect=collect,
    )
  start.assert_not_awaited()
  collect.assert_not_called()
  assert (
    runner.journal.connection.execute(
      "SELECT COUNT(*) FROM history_collection_executions"
    ).fetchone()[0]
    == 1
  )


@pytest.mark.parametrize("confirmed", [False, True])
async def test_original_journal_migration_preserves_execution_and_exit(
  execution, confirmed
):
  runner, permit = execution
  if not confirmed:
    runner.abort = AsyncMock(side_effect=ConnectionError)
  with pytest.raises(CollectionFailed if confirmed else ConnectionError):
    await runner.execute(
      permit,
      server_state="ISSUED",
      unit_payload=PAYLOAD,
      start=AsyncMock(),
      finish=AsyncMock(),
      collect=Mock(side_effect=RuntimeError),
    )
  before = dict(
    runner.journal.connection.execute(
      "SELECT * FROM history_collection_executions"
    ).fetchone()
  )
  failure_before = dict(
    runner.journal.connection.execute(
      "SELECT * FROM history_collection_aborts"
    ).fetchone()
  )
  # Recreate precisely the previous committed table constraints in this temp journal.
  runner.journal.connection.executescript("""
    ALTER TABLE history_collection_executions RENAME TO executions_new;
    CREATE TABLE history_collection_executions(unit_id TEXT PRIMARY KEY,permit_id TEXT NOT NULL UNIQUE,started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    INSERT INTO history_collection_executions SELECT unit_id,permit_id,started_at FROM executions_new;
    DROP TABLE executions_new;
    ALTER TABLE history_collection_aborts RENAME TO aborts_new;
    CREATE TABLE history_collection_aborts(permit_id TEXT PRIMARY KEY,unit_id TEXT NOT NULL UNIQUE,permit_json TEXT NOT NULL,failure_json TEXT NOT NULL,accepted_at TEXT,recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
    INSERT INTO history_collection_aborts SELECT * FROM aborts_new;
    DROP TABLE aborts_new;
  """)
  runner.journal.connection.close()
  runner.journal = LocalJournal(runner.journal.path)
  try:
    assert (
      dict(
        runner.journal.connection.execute(
          "SELECT * FROM history_collection_executions"
        ).fetchone()
      )
      == before
    )
    assert (
      dict(
        runner.journal.connection.execute(
          "SELECT * FROM history_collection_aborts"
        ).fetchone()
      )
      == failure_before
    )
    replacement = permit.model_copy(update={"permit_id": uuid4()})
    if confirmed:
      assert runner.journal.collection_execution_started(replacement) is False
    else:
      with pytest.raises(ValueError, match="another authorization"):
        runner.journal.collection_execution_started(replacement)
    # Reopening the migrated journal is idempotent and retains the original fact.
    other = LocalJournal(runner.journal.path)
    try:
      assert other.load_collection_abort(
        permit
      ) == runner.journal.load_collection_abort(permit)
    finally:
      other.connection.close()
  finally:
    runner.journal.connection.close()


@pytest.mark.parametrize("confirmed", [False, True])
async def test_retirement_preserves_pending_failure_and_compacts_confirmed_attempts(
  execution, confirmed
):
  from quantx_contracts.history_upload import HistoryUploadChunk, HistoryUploadSnapshot

  runner, permit = execution
  if not confirmed:
    runner.abort = AsyncMock(side_effect=ConnectionError)
  with pytest.raises(CollectionFailed if confirmed else ConnectionError):
    await runner.execute(
      permit,
      server_state="ISSUED",
      unit_payload=PAYLOAD,
      start=AsyncMock(),
      finish=AsyncMock(),
      collect=Mock(side_effect=RuntimeError),
    )
  if confirmed:
    replacement = permit.model_copy(update={"permit_id": uuid4()})
    await runner.execute(
      replacement,
      server_state="ISSUED",
      unit_payload=PAYLOAD,
      start=AsyncMock(),
      finish=AsyncMock(),
      collect=lambda: iter([]),
    )
  snapshot = HistoryUploadSnapshot(
    request_id=permit.unit.request_id,
    status="COMPLETED",
    verified_at=NOW - timedelta(hours=25),
    total_chunks=1,
    chunks=[
      HistoryUploadChunk(index=0, sha256="a" * 64, record_count=0, byte_count=100)
    ],
  )

  def retire():
    runner.journal.retire_history_upload(
      device_id=runner.device_id, request_sha256="b" * 64, snapshot=snapshot, now=NOW
    )

  if confirmed:
    retire()
    for table in [
      "history_collection_aborts",
      "history_collection_executions",
      "history_collection_receipts",
    ]:
      assert (
        runner.journal.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        == 0
      )
    assert runner.journal.history_upload_retired(
      runner.device_id, str(permit.unit.request_id)
    )
  else:
    with pytest.raises(ValueError, match="unconfirmed"):
      retire()
    assert runner.journal.load_collection_abort(permit) is not None
    assert not runner.journal.history_upload_retired(
      runner.device_id, str(permit.unit.request_id)
    )


@pytest.mark.parametrize("reason", ["XTDATA_UNAVAILABLE", "COLLECTION_RESULT_INVALID", "DATA_UNAVAILABLE"])
async def test_native_failure_classification_survives_journal_and_abort(
  execution, reason
):
  from quantx_qmt_agent.native_unit_ipc import NativeUnitFailure

  runner, permit = execution
  with pytest.raises(CollectionFailed) as caught:
    await runner.execute(
      permit,
      server_state="ISSUED",
      unit_payload=PAYLOAD,
      start=AsyncMock(),
      finish=AsyncMock(),
      collect=Mock(side_effect=NativeUnitFailure(reason)),
    )
  assert caught.value.reason_code == reason
  failure = runner.journal.load_collection_abort(permit)
  assert failure.reason_code == reason
  runner.stop_native.assert_called_once()
  runner.abort.assert_awaited_once_with(permit, failure)


async def test_unit_splitting_cannot_reset_original_request_record_budget(execution):
  runner, first = execution
  collect = Mock(return_value=iter([{"value": i} for i in range(7)]))
  await runner.execute(
    first,
    server_state="ISSUED",
    unit_payload=PAYLOAD,
    start=AsyncMock(),
    finish=AsyncMock(),
    collect=collect,
  )
  second = first.model_copy(
    update={
      "permit_id": uuid4(),
      "unit": CollectionUnit.from_payload(str(first.unit.request_id), 1, PAYLOAD),
    }
  )
  observed, closed = [], []

  def oversized():
    try:
      for i in range(100):
        observed.append(i)
        yield {"value": i}
    finally:
      assert runner.native_lock.locked()
      closed.append(True)

  finish = AsyncMock()
  with pytest.raises(CollectionFailed) as caught:
    await runner.execute(
      second,
      server_state="ISSUED",
      unit_payload=PAYLOAD,
      start=AsyncMock(),
      finish=finish,
      collect=oversized,
    )
  assert caught.value.reason_code == "COLLECTION_RESULT_INVALID"
  assert len(observed) == 4 and closed == [True]
  finish.assert_not_awaited()
  assert (
    runner.journal.collection_request_record_count(
      runner.device_id, str(first.unit.request_id)
    )
    == 7
  )
  # An explicit new authorization after the accepted failure retains the same
  # remaining allowance; it does not erase the seven original durable records.
  retry = second.model_copy(update={"permit_id": uuid4()})
  await runner.execute(
    retry,
    server_state="ISSUED",
    unit_payload=PAYLOAD,
    start=AsyncMock(),
    finish=finish,
    collect=lambda: iter([{"value": i} for i in range(3)]),
  )
  assert (
    runner.journal.collection_request_record_count(
      runner.device_id, str(first.unit.request_id)
    )
    == 10
  )
  assert (
    runner.journal.collection_request_record_count(runner.device_id, str(uuid4())) == 0
  )
  assert (
    runner.journal.collection_request_record_count(
      str(uuid4()), str(first.unit.request_id)
    )
    == 0
  )
