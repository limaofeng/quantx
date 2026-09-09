"""One authorized native call with durable recovery and confirmed START/FINISH.

The history transport supplies authenticated acknowledgement callbacks. This
executor does not issue permits, create requests, or interpret upload completion.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from quantx_contracts.collection_permit import CollectionPermit, CollectionUnit

from .journal import LocalJournal
from .native_unit_artifact import NativeUnitArtifact, NativeUnitArtifacts


class CollectionOutcomeUnknown(RuntimeError):
  """Native entry was recorded but no complete result can be proved."""

  reason_code = "COLLECTION_NATIVE_OUTCOME_UNKNOWN"


async def join_history_thread(function, *args):
  """Cancellation never releases ownership before its disk/native call exits."""
  task = asyncio.create_task(asyncio.to_thread(function, *args))
  try:
    return await asyncio.shield(task)
  except asyncio.CancelledError:
    while not task.done():
      try:
        await asyncio.shield(task)
      except asyncio.CancelledError:
        continue
      except Exception:
        break
    if not task.cancelled():
      task.exception()
    raise


class CollectionExecution:
  def __init__(
    self,
    *,
    device_id: str,
    journal: LocalJournal,
    artifacts: NativeUnitArtifacts,
    native_lock: asyncio.Lock,
    reserve: Callable[[int], None],
    release: Callable[[int], None],
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
  ):
    self.device_id = str(UUID(device_id))
    self.journal = journal
    self.artifacts = artifacts
    self.native_lock = native_lock
    self.reserve = reserve
    self.release = release
    self.clock = clock

  async def execute(
    self,
    permit: CollectionPermit,
    *,
    unit_payload: dict[str, Any],
    server_state: Literal["ISSUED", "STARTED"],
    start: Callable[[CollectionPermit], Awaitable[None]],
    finish: Callable[[CollectionPermit, NativeUnitArtifact], Awaitable[None]],
    collect: Callable[[], Iterable[dict[str, Any]]],
  ) -> NativeUnitArtifact:
    unit = CollectionUnit.from_payload(
      str(permit.unit.request_id), permit.unit.unit_index, unit_payload
    )
    if str(permit.device_id) != self.device_id or unit != permit.unit:
      raise ValueError("collection execution scope mismatch")
    if server_state not in {"ISSUED", "STARTED"}:
      raise ValueError("unexpected collection permit state")
    # The same process-wide native lock must be shared by every history caller.
    async with self.native_lock:
      if self.journal.history_upload_retired(
        self.device_id, str(permit.unit.request_id)
      ):
        raise ValueError("history request has been retired")
      if server_state == "STARTED" and not self.journal.collection_permit_received(
        permit
      ):
        raise CollectionOutcomeUnknown(
          "server reports native entry without local authorization evidence"
        )
      if self.journal.collection_execution_started(permit):
        artifact = await self._join_native_thread(self._recover, permit)
      else:
        self.journal.accept_collection_permit(
          permit, device_id=self.device_id, unit=unit, now=self.clock()
        )
        await start(permit)
        artifact = await self._join_native_thread(self._collect, permit, collect)
      await finish(permit, artifact)
      return artifact

  async def _join_native_thread(self, function, *args):
    return await join_history_thread(function, *args)

  def _collect(self, permit, collect):
    # Recheck after both the acknowledgement and thread-pool wait. Commit the
    # uncertainty marker before any broker call; cancellation never erases it.
    self.journal.begin_collection_execution(
      permit, device_id=self.device_id, now=self.clock()
    )
    records = iter(collect())
    try:
      artifact = self.artifacts.seal(
        permit.unit, records, reserve=self.reserve, release=self.release
      )
    finally:
      close = getattr(records, "close", None)
      if callable(close):
        close()
    self.journal.record_collection_artifact(
      permit_id=str(permit.permit_id), artifacts=self.artifacts, artifact=artifact
    )
    return artifact

  def _recover(self, permit):
    try:
      artifact = self.journal.load_collection_artifact(
        device_id=self.device_id, unit=permit.unit, artifacts=self.artifacts
      )
      if artifact is None:
        # Publication may have completed before its SQLite binding committed.
        # The original execution marker and full sealed-file validation allow
        # completing that local binding without executing any native call.
        artifact = self.artifacts.inspect(permit.unit)
        self.journal.record_collection_artifact(
          permit_id=str(permit.permit_id), artifacts=self.artifacts, artifact=artifact
        )
      return artifact
    except FileNotFoundError as exc:
      raise CollectionOutcomeUnknown(
        "collection native outcome is unknown; retain original execution evidence"
      ) from exc
