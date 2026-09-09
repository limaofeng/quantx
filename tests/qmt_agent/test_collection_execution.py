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
    permit, unit_payload=PAYLOAD, start=start, finish=finish, collect=collect
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
      permit, unit_payload=PAYLOAD, start=start, finish=finish, collect=collect
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
      permit, unit_payload=PAYLOAD, start=start, finish=AsyncMock(), collect=collect
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
  with pytest.raises(RuntimeError, match="native failed"):
    await runner.execute(
      permit, unit_payload=PAYLOAD, start=start, finish=finish, collect=collect
    )
  with pytest.raises(CollectionOutcomeUnknown):
    await runner.execute(
      permit, unit_payload=PAYLOAD, start=start, finish=finish, collect=collect
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
      unit_payload=PAYLOAD,
      start=AsyncMock(),
      finish=AsyncMock(),
      collect=collect,
    )
  monkeypatch.setattr(runner.journal, "record_collection_artifact", original)
  finish = AsyncMock()
  await runner.execute(
    permit,
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
      permit, unit_payload=PAYLOAD, start=AsyncMock(), finish=finish, collect=collect
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
      permit, unit_payload=payload, start=start, finish=AsyncMock(), collect=collect
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
      permit, unit_payload=PAYLOAD, start=start, finish=AsyncMock(), collect=collect
    )
  collect.assert_not_called()
  assert not runner.journal.collection_execution_started(permit)


async def test_new_permit_cannot_replace_original_native_execution(execution):
  runner, permit = execution
  await runner.execute(
    permit,
    unit_payload=PAYLOAD,
    start=AsyncMock(),
    finish=AsyncMock(),
    collect=lambda: iter([]),
  )
  newer = permit.model_copy(update={"permit_id": uuid4(), "owner_epoch": 2})
  start, finish, collect = AsyncMock(), AsyncMock(), Mock()
  with pytest.raises(ValueError, match="another authorization"):
    await runner.execute(
      newer, unit_payload=PAYLOAD, start=start, finish=finish, collect=collect
    )
  start.assert_not_awaited()
  finish.assert_not_awaited()
  collect.assert_not_called()


async def test_deleted_completed_file_is_unknown_instead_of_recollected(execution):
  runner, permit = execution
  artifact = await runner.execute(
    permit,
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
      unit_payload=PAYLOAD,
      start=AsyncMock(),
      finish=AsyncMock(),
      collect=collect,
    )
  assert error.value.reason_code == "COLLECTION_NATIVE_OUTCOME_UNKNOWN"
  collect.assert_not_called()
