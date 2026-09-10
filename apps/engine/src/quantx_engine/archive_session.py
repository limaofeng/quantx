"""Engine-owned bounded scope admission and archive transport lifecycle."""

import asyncio
from collections import Counter
from zoneinfo import ZoneInfo

from quantx_contracts.realtime_archive import (
  MAX_ARCHIVE_SCOPES_PER_GENERATION,
  ArchiveRecoveryScope,
  ArchiveRevision,
)
from quantx_infrastructure.services.local_market_data_client import (
  LocalMarketDataClient,
)

from .archive_intents import ArchiveIntentJournal
from .archive_sender import ArchiveSender


class EngineArchiveSession:
  def __init__(self, generation):
    self.generation = generation
    self.client = LocalMarketDataClient()
    self.scopes = {}
    self.durable = set()
    self.failures = Counter()
    self.journal = ArchiveIntentJournal()
    self.wake = asyncio.Event()
    self.admission = asyncio.Lock()
    self.task = None
    self.sender = ArchiveSender(self.client, scope_is_durable=self._has_scope)

  async def _io(self, operation, *args):
    task = asyncio.create_task(asyncio.to_thread(operation, *args))
    try:
      return await asyncio.shield(task)
    except asyncio.CancelledError:
      while not task.done():
        try:
          await asyncio.shield(task)
        except asyncio.CancelledError:
          continue
      if not task.cancelled():
        task.exception()
      raise

  async def start(self):
    pending = await self._io(self.journal.pending)
    self.scopes = {
      scope.instrument: scope
      for scope in pending
      if scope.generation == self.generation
    }
    self.sender.start()
    self.task = asyncio.create_task(self._register(), name="engine-archive-scopes")

  async def observe_scope(self, instrument, minute):
    async with self.admission:
      if instrument in self.scopes:
        return
      if len(self.scopes) >= MAX_ARCHIVE_SCOPES_PER_GENERATION:
        raise RuntimeError("ARCHIVE_SCOPE_CAPACITY")
      scope = ArchiveRecoveryScope(
        generation=self.generation, instrument=instrument, start_minute=minute
      )
      scope = await self._io(self.journal.put, scope)
      self.scopes[instrument] = scope
      self.wake.set()

  def _has_scope(self, request):
    scope = self.scopes.get(request.instrument)
    return bool(
      scope
      and request.instrument in self.durable
      and request.generation == scope.generation
      and request.minute >= scope.start_minute
    )

  def offer(self, kline, state):
    if self.task is None or self.task.done():
      self.failures["SESSION_STOPPED"] += 1
      return False
    # Existing latest-only aggregation cannot prove a complete minute origin.
    # These revisions remain unsealed even when the next minute arrives.
    try:
      minute = kline.time
      if minute.tzinfo is None:
        minute = minute.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
      generation, stream_id = state["lineage"]
      request = ArchiveRevision(
        instrument=kline.stock_code,
        minute=minute,
        generation=self.generation,
        continuity_generation=generation,
        stream_id=stream_id,
        sequence=state["sequence"],
        sealed=False,
        bar={
          key: getattr(kline, key)
          for key in (
            "open",
            "high",
            "low",
            "close",
            "pre_close",
            "volume",
            "amount",
            "settelement_price",
            "open_interest",
            "suspend_flag",
          )
        },
      )
    except (ValueError, TypeError, KeyError):
      self.failures["ARCHIVE_SOURCE_INVALID"] += 1
      return False
    return self.sender.offer(request)

  async def _register(self):
    while True:
      self.wake.clear()
      scope = await self._io(self.journal.reserve)
      if scope is None:
        try:
          await asyncio.wait_for(self.wake.wait(), 1)
        except TimeoutError:
          pass
        continue
      try:
        async with asyncio.timeout(3):
          await self.client.register_archive_scope(
            scope, recover=scope.generation != self.generation
          )
        if scope.generation == self.generation:
          self.durable.add(scope.instrument)
        await self._io(self.journal.acknowledge, scope)
      except asyncio.CancelledError:
        raise
      except Exception:
        self.failures["SCOPE_REGISTRATION_UNCONFIRMED"] += 1

  async def stop(self):
    if self.task is not None:
      self.task.cancel()
      await asyncio.gather(self.task, return_exceptions=True)
    await self.sender.stop()
    await self.client.close()
