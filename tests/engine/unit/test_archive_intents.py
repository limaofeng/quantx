import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_contracts.realtime_archive import ArchiveRecoveryScope
from quantx_engine import archive_intents, archive_session
from quantx_engine.archive_intents import ArchiveIntentJournal
from quantx_engine.realtime_manager import RealTimeDataManager


def test_original_scope_and_attempt_budget_survive_reopening(tmp_path, monkeypatch):
  clock = [1000]
  monkeypatch.setattr(archive_intents, "time", SimpleNamespace(time=lambda: clock[0]))
  path = tmp_path / "intents.sqlite"
  scope = ArchiveRecoveryScope(
    generation=1, instrument="600000.SH", start_minute="2026-09-10T09:30:00+08:00"
  )
  assert ArchiveIntentJournal(path).put(scope) == scope
  later = scope.model_copy(
    update={"start_minute": scope.start_minute.replace(minute=31)}
  )
  assert ArchiveIntentJournal(path).put(later) == scope
  for _ in range(4):
    journal = ArchiveIntentJournal(path)
    assert journal.reserve() == scope
    assert ArchiveIntentJournal(path).reserve() is None
    clock[0] += 30
  assert ArchiveIntentJournal(path).reserve() is None
  assert ArchiveIntentJournal(path).pending() == [scope]
  with sqlite3.connect(path) as db:
    assert db.execute("SELECT attempts,reason FROM scope_intent").fetchone() == (
      4,
      "REGISTRATION_UNCONFIRMED",
    )
  ArchiveIntentJournal(path).acknowledge(later)
  assert ArchiveIntentJournal(path).pending() == [scope]
  ArchiveIntentJournal(path).acknowledge(scope)
  assert ArchiveIntentJournal(path).pending() == []


def test_capacity_never_discards_previous_pending_scope(tmp_path, monkeypatch):
  monkeypatch.setattr(archive_intents, "MAX_PENDING_SCOPES", 1)
  journal = ArchiveIntentJournal(tmp_path / "intents.sqlite")
  scope = ArchiveRecoveryScope(
    generation=1, instrument="600000.SH", start_minute="2026-09-10T09:30:00+08:00"
  )
  journal.put(scope)
  with pytest.raises(RuntimeError, match="CAPACITY"):
    journal.put(scope.model_copy(update={"instrument": "000001.SZ"}))
  assert journal.pending() == [scope]


async def test_tick_subscription_starts_only_after_intent_is_reopenable(
  tmp_path, monkeypatch
):
  monkeypatch.setenv("QUANTX_RUNTIME_DIR", str(tmp_path))

  async def register(*args, **kwargs):
    await asyncio.Event().wait()

  client = SimpleNamespace(register_archive_scope=register, close=AsyncMock())
  monkeypatch.setattr(archive_session, "LocalMarketDataClient", lambda: client)
  subscribed = asyncio.Event()

  async def subscribe(**kwargs):
    assert ArchiveIntentJournal().pending()[0].instrument == kwargs["stock_code"]
    subscribed.set()
    return "handle"

  manager = RealTimeDataManager()
  manager.subscription_manager = SimpleNamespace(
    set_main_loop=lambda loop: None,
    subscribe=subscribe,
    unsubscribe=AsyncMock(),
    unsubscribe_all=AsyncMock(),
  )
  await manager.start(archive_generation=1)
  stream = manager.subscribe_tick("600000.SH")
  task = asyncio.create_task(anext(stream))
  try:
    await asyncio.wait_for(subscribed.wait(), 2)
    assert not manager.archive_session.durable
  finally:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await stream.aclose()
    await manager.stop()
  assert len(ArchiveIntentJournal().pending()) == 1


async def test_failed_intent_commit_cannot_start_source_subscription(
  tmp_path, monkeypatch
):
  monkeypatch.setenv("QUANTX_RUNTIME_DIR", str(tmp_path))
  client = SimpleNamespace(close=AsyncMock())
  monkeypatch.setattr(archive_session, "LocalMarketDataClient", lambda: client)
  manager = RealTimeDataManager()
  subscribe = AsyncMock()
  manager.subscription_manager = SimpleNamespace(
    set_main_loop=lambda loop: None, subscribe=subscribe, unsubscribe_all=AsyncMock()
  )
  await manager.start(archive_generation=1)

  def failed_commit(scope):
    raise OSError("injected full disk")

  monkeypatch.setattr(manager.archive_session.journal, "put", failed_commit)
  stream = manager.subscribe_tick("600000.SH")
  try:
    with pytest.raises(OSError, match="full disk"):
      await anext(stream)
    subscribe.assert_not_awaited()
    assert not manager.tick_subscribers
  finally:
    await stream.aclose()
    await manager.stop()
