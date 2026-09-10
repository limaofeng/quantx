"""Default Engine lifecycle to actual Data API, durable scope and Worker proof."""
# ruff: noqa: F811

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from quantx_contracts.realtime_archive import ArchiveRecoveryScope
from quantx_engine import archive_session
from quantx_engine.archive_intents import ArchiveIntentJournal
from quantx_engine.realtime_manager import RealTimeDataManager
from quantx_infrastructure.services.engine_archive_generation import (
  register_engine_archive_generation,
)
from quantx_infrastructure.services.realtime_archive_worker import (
  advance_realtime_archive,
)
from sqlalchemy import text

from tests.engine.unit.test_intraday_klines import _tick
from tests.infrastructure.test_engine_archive_generation import archive_db  # noqa: F401
from tests.infrastructure.test_realtime_archive_delivery import (
  archive_case,  # noqa: F401
)


async def test_default_manager_archives_through_worker_without_direct_write(
  archive_case, monkeypatch, tmp_path
):
  case = archive_case
  monkeypatch.setenv("QUANTX_RUNTIME_DIR", str(tmp_path))
  async with case.engine.begin() as db:
    await db.execute(text("DELETE FROM engine_archive_scope"))
  monkeypatch.setattr(archive_session, "LocalMarketDataClient", lambda: case.client)
  manager = RealTimeDataManager()
  manager.subscription_manager = SimpleNamespace(
    set_main_loop=lambda loop: None, unsubscribe_all=AsyncMock()
  )
  await manager.start(archive_generation=case.request.generation)
  session = manager.archive_session
  try:
    await session.observe_scope(case.request.instrument, case.request.minute)

    async def registered():
      while case.request.instrument not in session.durable:
        await asyncio.sleep(0.01)

    await asyncio.wait_for(registered(), 2)
    for offset in (1, 20):
      tick = _tick(
        case.request.instrument,
        case.request.minute.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        + timedelta(seconds=offset),
        10,
        1000 + offset,
        10000 + offset * 10,
        5,
      )
      tick.continuity_generation = case.request.continuity_generation
      tick.market_stream_id = str(case.request.stream_id)
      tick.market_stream_sequence = offset
      await manager._handle_tick_generated_1m(case.request.instrument, tick)
    await asyncio.wait_for(session.sender.wait_idle(), 2)
    assert session.sender.accepted == 2
    for _ in range(4):
      assert await advance_realtime_archive(case.first)
    async with case.engine.connect() as db:
      saved = (
        (
          await db.execute(
            text(
              "SELECT phase,request,proof FROM realtime_archive_revision ORDER BY sequence DESC LIMIT 1"
            )
          )
        )
        .mappings()
        .one()
      )
      assert saved["phase"] == "VERIFIED" and saved["proof"]
      assert saved["request"]["sealed"] is False
      assert saved["request"]["sequence"] == 20
      assert await db.scalar(text("SELECT count(*) FROM engine_archive_scope")) == 1
    assert {table for table, row in case.storage.points.values()} == {
      "kline_1m_versions"
    }
  finally:
    await manager.stop()
  assert session.task.done() and session.sender._task.done()
  assert manager.archive_session is None


async def test_new_engine_recovers_old_unconfirmed_intent_without_write_permission(
  archive_case, monkeypatch, tmp_path
):
  case = archive_case
  monkeypatch.setenv("QUANTX_RUNTIME_DIR", str(tmp_path))
  monkeypatch.setattr(archive_session, "LocalMarketDataClient", lambda: case.client)
  async with case.engine.begin() as db:
    await db.execute(text("DELETE FROM engine_archive_scope"))
    created = await db.scalar(
      text("SELECT registered_at FROM engine_archive_generation WHERE generation=:g"),
      {"g": case.request.generation},
    )
  scope = ArchiveRecoveryScope(
    generation=case.request.generation,
    instrument=case.request.instrument,
    start_minute=created.replace(second=0, microsecond=0),
  )
  journal = ArchiveIntentJournal()
  import sqlite3

  import httpx

  with pytest.raises(httpx.HTTPStatusError) as active:
    await case.client.register_archive_scope(scope, recover=True)
  assert active.value.response.status_code == 409
  journal.put(scope)
  assert journal.reserve() == scope
  with sqlite3.connect(journal.path) as db:
    db.execute("UPDATE scope_intent SET next_retry=0")
  register = case.client.register_archive_scope

  async def resumed_register(value, *, recover=False):
    assert recover is True and value == scope
    with sqlite3.connect(journal.path) as db:
      assert db.execute("SELECT attempts FROM scope_intent").fetchone()[0] == 2
    return await register(value, recover=recover)

  monkeypatch.setattr(case.client, "register_archive_scope", resumed_register)
  # A new registration on the same lock holder invalidates the old generation.
  generation = await register_engine_archive_generation(case.source, str(uuid4()))
  session = archive_session.EngineArchiveSession(generation)
  await session.start()
  try:

    async def recovered():
      while await session._io(journal.pending):
        await asyncio.sleep(0.01)

    await asyncio.wait_for(recovered(), 3)
    assert not session.durable
    async with case.engine.connect() as db:
      saved = (
        (
          await db.execute(
            text("SELECT generation,start_minute,ended_at FROM engine_archive_scope")
          )
        )
        .mappings()
        .one()
      )
      assert saved["generation"] == scope.generation
      assert saved["start_minute"] == scope.start_minute and saved["ended_at"]
    with pytest.raises(httpx.HTTPStatusError) as failure:
      await case.client.submit_archive(
        case.request.model_copy(update={"minute": scope.start_minute})
      )
    assert failure.value.response.status_code == 409
  finally:
    await session.stop()


async def test_default_fifo_subscription_seals_completed_observation_minute(
  archive_case, monkeypatch, tmp_path
):
  from quantx_infrastructure.core.data.unified_subscription_manager import (
    UnifiedDataSubscriptionManager,
  )
  from quantx_infrastructure.core.data.whole_quote_hub import (
    QuoteDeliveryMode,
    WholeQuoteHub,
    WholeQuoteStatus,
  )

  from tests.infrastructure.test_whole_quote_hub import AlwaysClosed, FakeStore

  case = archive_case
  monkeypatch.setenv("QUANTX_RUNTIME_DIR", str(tmp_path))
  monkeypatch.setattr(archive_session, "LocalMarketDataClient", lambda: case.client)
  hub = WholeQuoteHub(store=FakeStore(), trading_time_service=AlwaysClosed())
  hub.status = WholeQuoteStatus.READY
  subscriptions = object.__new__(UnifiedDataSubscriptionManager)
  subscriptions.hub, subscriptions._handles, subscriptions._owner_handles = hub, {}, {}
  manager = RealTimeDataManager()
  manager.subscription_manager = subscriptions
  monkeypatch.setattr(
    manager, "_normalize_tick_pre_close", AsyncMock(side_effect=lambda code, tick: tick)
  )
  await manager.start(archive_generation=case.request.generation)
  session = manager.archive_session
  await session.observe_scope(case.request.instrument, case.request.minute)
  stream = manager.subscribe_tick(case.request.instrument)
  first = asyncio.create_task(anext(stream))
  try:

    async def ready():
      while (
        case.request.instrument not in manager._tick_handles
        or case.request.instrument not in session.durable
      ):
        await asyncio.sleep(0.01)

    await asyncio.wait_for(ready(), 3)
    consumer = next(iter(hub._consumers.values()))
    assert consumer.delivery is QuoteDeliveryMode.ARCHIVE
    for sequence, offset in enumerate((10, 61, 100, 121), start=1):
      moment = case.request.minute + timedelta(seconds=offset)
      await hub._dispatch(
        {
          case.request.instrument: {
            "time": int(moment.timestamp() * 1000),
            "lastPrice": 10.0,
            "volume": 1000 + sequence * 5,
            "amount": 10000.0 + sequence * 50,
            "lastClose": 9.8,
            "tickVol": 5,
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "continuity_generation": case.request.continuity_generation,
            "market_stream_id": str(case.request.stream_id),
            "market_stream_sequence": sequence,
          }
        }
      )
      await asyncio.wait_for(consumer.queue.join(), 2)
    await asyncio.wait_for(first, 2)
    await asyncio.wait_for(session.sender.wait_idle(), 3)
    for _ in range(12):
      if not await advance_realtime_archive(case.first):
        break
    async with case.engine.connect() as db:
      sealed = (
        (
          await db.execute(
            text(
              "SELECT minute,phase,proof FROM realtime_archive_revision WHERE sealed"
            )
          )
        )
        .mappings()
        .all()
      )
      assert len(sealed) == 1
      assert sealed[0]["minute"] == case.request.minute + timedelta(minutes=1)
      assert sealed[0]["phase"] == "VERIFIED" and sealed[0]["proof"]
  finally:
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    await stream.aclose()
    await manager.stop()
