"""Repeated cancellation must not detach a sent synchronous database write."""

import asyncio
import threading

import pytest
from quantx_infrastructure.services import market_data_transfer_ingestion as ingestion

from tests.infrastructure.test_market_data_transfer_ingestion import AtomicRequestStore


def install_writer(monkeypatch, *, fail=False):
  loop = asyncio.get_running_loop()
  entered = asyncio.Event()
  release = threading.Event()
  finished = threading.Event()

  def write(**kwargs):
    loop.call_soon_threadsafe(entered.set)
    try:
      if not release.wait(5):
        raise RuntimeError("test did not release bounded writer")
      if fail:
        raise RuntimeError("write failed after cancellation")
      return {"status": "success", "saved_count": 1}
    finally:
      finished.set()

  monkeypatch.setattr(ingestion, "_save_market_data_period_sync", write)
  return entered, release, finished


async def cancel_repeatedly(task):
  for _ in range(3):
    task.cancel()
    # Allow both the outer task and its shielded child to handle cancellation.
    for _ in range(3):
      await asyncio.sleep(0)
    assert not task.done()


@pytest.mark.parametrize("fail", [False, True])
async def test_write_join_survives_repeated_cancellation(monkeypatch, fail):
  entered, release, finished = install_writer(monkeypatch, fail=fail)
  task = asyncio.create_task(
    ingestion.save_market_data_period(period="1m", market_data={})
  )
  try:
    await asyncio.wait_for(entered.wait(), 2)
    await cancel_repeatedly(task)
    assert not finished.is_set()
  finally:
    release.set()
    with pytest.raises(asyncio.CancelledError):
      await asyncio.wait_for(task, 2)
  assert finished.is_set()


async def test_claim_release_waits_for_writer_and_its_own_database_operation(
  monkeypatch,
):
  entered, release, finished = install_writer(monkeypatch)
  release_entered, release_allowed = asyncio.Event(), asyncio.Event()

  class Store(AtomicRequestStore):
    async def release_market_data_request_claim(self, *args, **kwargs):
      assert finished.is_set()
      release_entered.set()
      await release_allowed.wait()
      return await super().release_market_data_request_claim(*args, **kwargs)

  store = Store()

  async def ingest(_store, _request_id, *, progress=None):
    return await ingestion.save_market_data_period(period="1m", market_data={})

  task = asyncio.create_task(
    ingestion.claim_ingest_and_finish_market_data_request(
      store,
      "request-1",
      ingest_request=ingest,
    )
  )
  try:
    await asyncio.wait_for(entered.wait(), 2)
    await cancel_repeatedly(task)
    assert store.release_count == 0
    assert store.status == "PROCESSING"
    release.set()
    await asyncio.wait_for(release_entered.wait(), 2)
    await cancel_repeatedly(task)
    assert store.release_count == 0
  finally:
    release.set()
    release_allowed.set()
    with pytest.raises(asyncio.CancelledError):
      await asyncio.wait_for(task, 2)
  assert store.release_count == 1
  assert store.status == "UPLOADED"


async def test_cancellation_during_normal_cleanup_waits_and_remains_cancellation(
  monkeypatch,
):
  started, cleaning, release, finished = (asyncio.Event() for _ in range(4))

  async def renew(*args):
    started.set()
    try:
      await asyncio.Event().wait()
    finally:
      cleaning.set()
      await release.wait()
      finished.set()

  async def ingest(*args, **kwargs):
    await started.wait()
    return {"records_saved": 1}

  monkeypatch.setattr(ingestion, "_renew_claim", renew)
  store = AtomicRequestStore()
  task = asyncio.create_task(
    ingestion.claim_ingest_and_finish_market_data_request(
      store,
      "request-1",
      ingest_request=ingest,
    )
  )
  try:
    await asyncio.wait_for(cleaning.wait(), 2)
    await cancel_repeatedly(task)
    assert not finished.is_set()
  finally:
    release.set()
    with pytest.raises(asyncio.CancelledError):
      await asyncio.wait_for(task, 2)
  assert finished.is_set()
