# ruff: noqa: F811
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from quantx_engine import archive_session

from tests.engine.unit.test_archive_sender import revision  # noqa: F401


async def test_scope_wait_never_blocks_offer_and_stop_joins_transport(
  monkeypatch, revision
):  # noqa: F811
  entered = asyncio.Event()

  async def register(scope):
    entered.set()
    await asyncio.Event().wait()

  client = SimpleNamespace(register_archive_scope=register, close=AsyncMock())
  monkeypatch.setattr(archive_session, "LocalMarketDataClient", lambda: client)
  session = archive_session.EngineArchiveSession(revision.generation)
  bar = SimpleNamespace(
    stock_code=revision.instrument, time=revision.minute, **revision.bar.model_dump()
  )
  state = {
    "lineage": (revision.continuity_generation, str(revision.stream_id)),
    "sequence": revision.sequence,
  }
  try:
    assert not session.offer(bar, state)
    await asyncio.wait_for(entered.wait(), 1)
    for sequence in range(2, 50):
      assert not session.offer(bar, {**state, "sequence": sequence})
    assert len(session.scopes) == 1 and not session.durable
    assert session.sender.pending_items == 0
    assert session.sender.failures["RECOVERY_SCOPE_MISSING"] == 49
  finally:
    await session.stop()
  assert session.task.done() and session.sender._task.done()
  client.close.assert_awaited_once()


async def test_invalid_lineage_cannot_create_scope_or_send(monkeypatch, revision):  # noqa: F811
  client = SimpleNamespace(register_archive_scope=AsyncMock(), close=AsyncMock())
  monkeypatch.setattr(archive_session, "LocalMarketDataClient", lambda: client)
  session = archive_session.EngineArchiveSession(revision.generation)
  try:
    bar = SimpleNamespace(
      stock_code=revision.instrument, time=revision.minute, **revision.bar.model_dump()
    )
    assert not session.offer(bar, {"lineage": (0, ""), "sequence": 0})
    assert session.failures == {"ARCHIVE_SOURCE_INVALID": 1}
    assert not session.scopes
    client.register_archive_scope.assert_not_awaited()
  finally:
    await session.stop()
