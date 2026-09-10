import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from quantx_contracts.realtime_archive import ArchiveRevision
from quantx_engine import archive_sender
from quantx_engine.archive_sender import ArchiveSender


@pytest.fixture
def revision():
  return ArchiveRevision(
    instrument="600000.SH",
    minute="2026-09-10T09:31:00+08:00",
    generation=1,
    continuity_generation=1,
    stream_id=uuid4(),
    sequence=1,
    sealed=False,
    bar=dict(
      open=10.0,
      high=10.0,
      low=10.0,
      close=10.0,
      pre_close=9.0,
      volume=5.0,
      amount=50.0,
      settelement_price=0.0,
      open_interest=0,
      suspend_flag=0,
    ),
  )


@pytest.mark.asyncio
async def test_missing_durable_scope_refuses_before_transport(revision):
  client = SimpleNamespace(submit_archive=AsyncMock())
  sender = ArchiveSender(client, scope_is_durable=lambda _: False)
  sender.start()
  try:
    assert not sender.offer(revision)
    assert sender.failures == {"RECOVERY_SCOPE_MISSING": 1}
    assert sender.pending_items == sender.pending_bytes == 0
    client.submit_archive.assert_not_called()
  finally:
    await sender.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("capacity", ["items", "bytes"])
async def test_capacity_includes_inflight_and_stop_preserves_loss_count(
  revision, monkeypatch, capacity
):
  started = asyncio.Event()

  async def slow(_):
    started.set()
    await asyncio.Event().wait()

  if capacity == "items":
    monkeypatch.setattr(archive_sender, "MAX_ARCHIVE_QUEUE_ITEMS", 2)
  else:
    monkeypatch.setattr(
      archive_sender,
      "MAX_ARCHIVE_QUEUE_BYTES",
      2 * len(revision.model_dump_json().encode()),
    )
  sender = ArchiveSender(
    SimpleNamespace(submit_archive=slow), scope_is_durable=lambda _: True
  )
  sender.start()
  try:
    assert sender.offer(revision)
    await asyncio.wait_for(started.wait(), 1)
    assert sender.offer(revision)
    assert not sender.offer(revision)
    assert sender.pending_items == 2
    assert sender.failures == {"QUEUE_CAPACITY": 1}
  finally:
    await asyncio.wait_for(sender.stop(), 1)
  assert sender.failures["STOPPED_UNCONFIRMED"] == 2
  assert sender.pending_items == sender.pending_bytes == 0
  assert sender.accepted == 0
  assert not sender.offer(revision)
  with pytest.raises(RuntimeError, match="already started"):
    sender.start()


@pytest.mark.asyncio
async def test_queue_owns_content_and_never_reports_verified(revision):
  captured = []
  original = revision.model_copy(deep=True)

  async def accept(request):
    captured.append(request)
    return request.identity()

  sender = ArchiveSender(
    SimpleNamespace(submit_archive=accept), scope_is_durable=lambda _: True
  )
  sender.start()
  try:
    assert sender.offer(revision)
    revision.bar.close = 999
    revision.sequence = 99
    await asyncio.wait_for(sender.wait_idle(), 1)
    assert captured == [original]
    assert sender.accepted == 1
    assert sender.pending_items == sender.pending_bytes == 0
    assert not sender.failures
  finally:
    await sender.stop()


@pytest.mark.asyncio
async def test_offline_retries_are_bounded_and_do_not_kill_sender(revision):
  client = SimpleNamespace(
    submit_archive=AsyncMock(side_effect=httpx.ConnectError("offline")),
    archive_status=AsyncMock(side_effect=httpx.ConnectError("offline")),
  )
  sender = ArchiveSender(client, scope_is_durable=lambda _: True)
  sender.start()
  try:
    assert sender.offer(revision)
    await asyncio.wait_for(sender.wait_idle(), 9)
    assert client.submit_archive.await_count == 1
    assert client.archive_status.await_count == 3
    assert sender.failures == {"SEND_ATTEMPTS_EXHAUSTED": 1}
    assert sender.accepted == 0
    assert not sender._task.done()
  finally:
    await sender.stop()


@pytest.mark.asyncio
async def test_permanent_rejection_does_not_retry(revision):
  request = httpx.Request("POST", "http://local/archives")
  client = SimpleNamespace(
    submit_archive=AsyncMock(
      side_effect=httpx.HTTPStatusError(
        "conflict", request=request, response=httpx.Response(409, request=request)
      )
    )
  )
  sender = ArchiveSender(client, scope_is_durable=lambda _: True)
  sender.start()
  try:
    assert sender.offer(revision)
    await asyncio.wait_for(sender.wait_idle(), 1)
    client.submit_archive.assert_awaited_once()
    assert sender.failures == {"SEND_REJECTED": 1}
  finally:
    await sender.stop()


@pytest.mark.asyncio
async def test_http_timeout_releases_slot_for_next_revision(revision, monkeypatch):
  monkeypatch.setattr(archive_sender, "ARCHIVE_HTTP_TIMEOUT", 0.01)
  monkeypatch.setattr(archive_sender, "ARCHIVE_SEND_ATTEMPTS", 1)
  calls = []

  async def submit(request):
    calls.append(request.identity())
    if len(calls) == 1:
      await asyncio.Event().wait()
    return request.identity()

  sender = ArchiveSender(
    SimpleNamespace(submit_archive=submit), scope_is_durable=lambda _: True
  )
  sender.start()
  try:
    assert sender.offer(revision)
    assert sender.offer(revision.model_copy(update={"sequence": 2}))
    await asyncio.wait_for(sender.wait_idle(), 1)
    assert len(calls) == 2
    assert sender.accepted == 1
    assert sender.failures == {"SEND_ATTEMPTS_EXHAUSTED": 1}
    assert sender.pending_items == sender.pending_bytes == 0
  finally:
    await sender.stop()
