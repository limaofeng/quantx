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

from .archive_sender import ArchiveSender


class EngineArchiveSession:
  def __init__(self, generation):
    self.generation = generation
    self.client = LocalMarketDataClient()
    self.scopes = {}
    self.durable = set()
    self.failures = Counter()
    self.queue = asyncio.Queue(maxsize=MAX_ARCHIVE_SCOPES_PER_GENERATION)
    self.sender = ArchiveSender(self.client, scope_is_durable=self._has_scope)
    self.sender.start()
    self.task = asyncio.create_task(self._register(), name="engine-archive-scopes")

  def _has_scope(self, request):
    scope = self.scopes.get(request.instrument)
    return bool(
      scope
      and request.instrument in self.durable
      and request.generation == scope.generation
      and request.minute >= scope.start_minute
    )

  def offer(self, kline, state):
    if self.task.done():
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
    if request.instrument not in self.scopes:
      if len(self.scopes) >= MAX_ARCHIVE_SCOPES_PER_GENERATION:
        self.failures["SCOPE_CAPACITY"] += 1
        return False
      scope = ArchiveRecoveryScope(
        generation=self.generation,
        instrument=request.instrument,
        start_minute=request.minute,
      )
      self.scopes[request.instrument] = scope
      self.queue.put_nowait(scope)
    return self.sender.offer(request)

  async def _register(self):
    while True:
      scope = await self.queue.get()
      try:
        for attempt in range(4):
          try:
            async with asyncio.timeout(3):
              await self.client.register_archive_scope(scope)
            self.durable.add(scope.instrument)
            break
          except asyncio.CancelledError:
            raise
          except Exception:
            if attempt == 3:
              self.failures["SCOPE_REGISTRATION_EXHAUSTED"] += 1
            else:
              await asyncio.sleep(2**attempt)
      finally:
        self.queue.task_done()

  async def stop(self):
    self.task.cancel()
    await asyncio.gather(self.task, return_exceptions=True)
    await self.sender.stop()
    await self.client.close()
