"""Default Engine lifecycle to actual Data API, durable scope and Worker proof."""
# ruff: noqa: F811

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from quantx_engine import archive_session
from quantx_engine.realtime_manager import RealTimeDataManager
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
  archive_case, monkeypatch
):
  case = archive_case
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
      await asyncio.wait_for(session.queue.join(), 2)
    await asyncio.wait_for(session.sender.wait_idle(), 2)
    assert session.sender.accepted == 1
    assert await advance_realtime_archive(case.first)
    assert await advance_realtime_archive(case.first)
    async with case.engine.connect() as db:
      saved = (
        (
          await db.execute(
            text("SELECT phase,request,proof FROM realtime_archive_revision")
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
