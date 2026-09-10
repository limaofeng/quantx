"""Bounded, single-flight archive transport; durable scopes own gap recovery.

The caller must persist an open recovery scope before admitting its first revision
and retain that scope until historical reconciliation proves coverage. Acceptance
here never closes a scope. Thus memory loss, including a crash before offer(), is
repaired from the scope rather than from this volatile queue.
"""

import asyncio
from collections import Counter
from collections.abc import Callable

import httpx
from quantx_contracts.realtime_archive import (
  MAX_ARCHIVE_REQUEST_BYTES,
  ArchiveRevision,
)

MAX_ARCHIVE_QUEUE_ITEMS = 512
MAX_ARCHIVE_QUEUE_BYTES = 2 * 1024 * 1024
ARCHIVE_SEND_ATTEMPTS = 4
ARCHIVE_HTTP_TIMEOUT = 3


class ArchiveSender:
  def __init__(
    self,
    client,
    *,
    scope_is_durable: Callable[[ArchiveRevision], bool],
  ):
    self.client = client
    self.scope_is_durable = scope_is_durable
    self._queue = asyncio.Queue(maxsize=MAX_ARCHIVE_QUEUE_ITEMS)
    self._task = None
    self._stopping = False
    # Includes the in-flight item, not just Queue.qsize().
    self.pending_items = 0
    self.pending_bytes = 0
    self.accepted = 0
    self.failures = Counter()
    self._idle = asyncio.Event()
    self._idle.set()

  def start(self):
    if self._task is not None:
      raise RuntimeError("archive sender already started")
    if self._stopping:
      raise RuntimeError("stopped archive sender cannot restart")
    self._task = asyncio.create_task(self._run(), name="engine-archive-sender")

  def offer(self, request: ArchiveRevision) -> bool:
    """Bounded CPU/memory only; never await HTTP, disk or persistence here.

    The scope predicate must be a memory lookup of successfully persisted scopes.
    Missing scope is a refusal, never an implicit promise of crash recovery.
    """
    if self._stopping or self._task is None or self._task.done():
      self.failures["SENDER_STOPPED"] += 1
      return False
    if self.scope_is_durable(request) is not True:
      self.failures["RECOVERY_SCOPE_MISSING"] += 1
      return False
    payload = request.model_dump_json().encode()
    size = len(payload)
    if size > MAX_ARCHIVE_REQUEST_BYTES:
      self.failures["REVISION_TOO_LARGE"] += 1
      return False
    if (
      self.pending_items >= MAX_ARCHIVE_QUEUE_ITEMS
      or self.pending_bytes + size > MAX_ARCHIVE_QUEUE_BYTES
    ):
      self.failures["QUEUE_CAPACITY"] += 1
      return False
    # Own immutable bytes; later mutation of a caller's KLine/request cannot
    # change the identity or content of a retry after an ambiguous response.
    self._queue.put_nowait(payload)
    self.pending_items += 1
    self.pending_bytes += size
    self._idle.clear()
    return True

  async def wait_idle(self):
    await self._idle.wait()

  async def stop(self):
    self._stopping = True
    if self._task is not None:
      self._task.cancel()
      await asyncio.gather(self._task, return_exceptions=True)
    while not self._queue.empty():
      payload = self._queue.get_nowait()
      self.failures["STOPPED_UNCONFIRMED"] += 1
      self._release(payload)

  def _release(self, payload):
    self.pending_items -= 1
    self.pending_bytes -= len(payload)
    self._queue.task_done()
    if self.pending_items == 0:
      self._idle.set()

  async def _run(self):
    while True:
      payload = await self._queue.get()
      try:
        request = ArchiveRevision.model_validate_json(payload)
        if await self._send(request):
          self.accepted += 1
      except asyncio.CancelledError:
        self.failures["STOPPED_UNCONFIRMED"] += 1
        raise
      except Exception:
        # Do not log request contents or transport exception bodies. A bad item
        # leaves its durable recovery scope open and must not kill the sender.
        self.failures["SEND_INVALID"] += 1
      finally:
        self._release(payload)

  async def _send(self, request):
    for attempt in range(ARCHIVE_SEND_ATTEMPTS):
      try:
        if attempt:
          # The previous POST may have committed and lost only its response.
          # Read before retrying, including after the source generation exited.
          async with asyncio.timeout(ARCHIVE_HTTP_TIMEOUT):
            status = await self.client.archive_status(request)
          if status is not None:
            return True
        async with asyncio.timeout(ARCHIVE_HTTP_TIMEOUT):
          identity = await self.client.submit_archive(request)
        if identity != request.identity():
          raise ValueError("archive acceptance identity mismatch")
        return True
      except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code
        if 400 <= status_code < 500 and status_code not in (408, 429):
          self.failures["SEND_REJECTED"] += 1
          return False
      except (httpx.TransportError, TimeoutError, OSError):
        pass
      if attempt + 1 < ARCHIVE_SEND_ATTEMPTS:
        await asyncio.sleep(2**attempt)
    self.failures["SEND_ATTEMPTS_EXHAUSTED"] += 1
    return False
