"""Compose retained requests, acknowledged native calls and canonical uploads.

The runtime owns the session lifetime and invokes this handler serially. Every
blocking disk/native operation is joined before cancelling its owning handler.
"""

from __future__ import annotations

from quantx_contracts.history_session import (
  HistoryGrant,
  HistoryRequest,
  HistoryRequestRemoved,
)

from .collection_execution import CollectionExecution, join_history_thread
from .collection_receipts import HistoryReceiptClient
from .historical_worker import _HistoricalDiskBudget
from .history_jobs import HistoryJobs
from .native_unit_artifact import NativeUnitArtifacts


class HistoryPipeline:
  def __init__(self, runtime):
    self.runtime = runtime
    self.jobs = HistoryJobs(
      runtime._market_spool_root, device_id=runtime.configuration.device_id
    )
    self.active = {}
    self.receipts = HistoryReceiptClient(
      runtime._market_data_upload_client(),
      api_url=runtime.configuration.api_url,
      history_token=runtime._history_access_token,
    )

  def _budget(self):
    return _HistoricalDiskBudget(
      max_bytes=self.runtime._available_history_spool_bytes()
    )

  def _retain(self, message):
    budget = self._budget()
    return self.jobs.retain(message, reserve=budget.reserve, release=budget.release)

  def _artifacts(self, job):
    from .runtime import (
      MAX_MARKET_DATA_RECORD_UNCOMPRESSED_BYTES,
      MAX_MARKET_DATA_REQUEST_RECORDS,
      MAX_MARKET_DATA_REQUEST_UNCOMPRESSED_BYTES,
    )

    return NativeUnitArtifacts(
      job.artifacts_directory,
      max_bytes=MAX_MARKET_DATA_REQUEST_UNCOMPRESSED_BYTES,
      max_record_bytes=MAX_MARKET_DATA_RECORD_UNCOMPRESSED_BYTES,
      max_records=MAX_MARKET_DATA_REQUEST_RECORDS,
    )

  async def handle(self, message):
    runtime = self.runtime
    if isinstance(message, HistoryRequestRemoved):
      self.active.pop(message.request_id, None)
      return  # Removing delivery eligibility never removes durable results.
    if isinstance(message, HistoryRequest):
      if message.request_id not in self.active and len(self.active) >= 2:
        raise ValueError("history pipeline admission exceeded")
      async with runtime._historical_worker_lock:
        job = await join_history_thread(self._retain, message)
        self.active[message.request_id] = job
        if message.completed_units != message.unit_count:
          return
        artifacts = self._artifacts(job)
        prepared = await join_history_thread(
          runtime._prepare_history_job_sync, job, artifacts
        )
      request_id = str(message.request_id)
      client = runtime._market_data_upload_client()
      for index, chunk in enumerate(prepared.chunks):
        await runtime._upload_provisional_market_data_chunk(
          client, request_id, index, chunk
        )
      await runtime._finalize_market_data_upload(
        request_id, len(prepared.chunks), client=client
      )
      return
    if not isinstance(message, HistoryGrant):
      raise ValueError("unsupported history pipeline message")
    job = self.active.get(message.permit.unit.request_id)
    if job is None:
      raise ValueError("history grant lacks retained request")
    index = message.permit.unit.unit_index
    if (
      index != job.request.completed_units
      or index >= len(job.units)
      or job.units[index] != message.permit.unit
    ):
      raise ValueError("history grant differs from retained plan")
    # The first reserve runs inside the native lock, after START confirmation.
    # Recompute actual retained bytes then; no stale pre-lock capacity snapshot.
    budget = None

    def reserve(size):
      nonlocal budget
      if budget is None:
        budget = self._budget()
      budget.reserve(size)

    def release(size):
      if budget is not None:
        budget.release(size)

    async def start(permit):
      nonlocal budget
      budget = await join_history_thread(self._budget)
      if budget.max_bytes <= 0:
        raise ValueError("history spool capacity unavailable before START")
      await self.receipts.start(permit)

    execution = CollectionExecution(
      device_id=runtime.configuration.device_id,
      journal=runtime.journal,
      artifacts=self._artifacts(job),
      native_lock=runtime._historical_worker_lock,
      reserve=reserve,
      release=release,
    )
    await execution.execute(
      message.permit,
      server_state=message.state,
      unit_payload=message.unit_payload,
      start=start,
      finish=self.receipts.finish,
      collect=lambda: runtime._collect_history_unit_sync(
        message.permit, job.request.payload
      ),
    )
