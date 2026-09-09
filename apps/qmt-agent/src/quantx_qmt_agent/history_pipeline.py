"""Compose retained requests, acknowledged native calls and canonical uploads.

The runtime owns the session lifetime and invokes this handler serially. Every
blocking disk/native operation is joined before cancelling its owning handler.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
from quantx_contracts.history_session import (
  HistoryGrant,
  HistoryRequest,
  HistoryRequestRemoved,
)
from quantx_contracts.history_upload import (
  HistoryUploadAcknowledgement,
  HistoryUploadChunk,
  HistoryUploadSnapshot,
)

from .collection_execution import (
  CollectionExecution,
  CollectionFailed,
  join_history_thread,
)
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
    self._recovery_cursor = None
    self.receipts = HistoryReceiptClient(
      runtime._market_data_upload_client(),
      api_url=runtime.configuration.api_url,
      history_token=runtime._history_access_token,
    )

  def _budget(self):
    return _HistoricalDiskBudget(
      max_bytes=self.runtime._available_history_spool_bytes()
    )

  def _stop_failed_native(self, error):
    from .runtime import _FatalMarketDataPreparationError

    if isinstance(error, _FatalMarketDataPreparationError):
      # A stuck IPC helper is also uncertainty, even if its child was killed.
      raise error
    self.runtime._shutdown_historical_worker_sync(graceful=False)

  def _retain(self, message):
    if self.runtime.journal.history_upload_retired(
      self.runtime.configuration.device_id, str(message.request_id)
    ):
      raise ValueError("history request has been retired")
    budget = self._budget()
    return self.jobs.retain(message, reserve=budget.reserve, release=budget.release)

  def _record_acceptance(self, job, snapshot):
    budget = self._budget()
    self.jobs.record_upload_acceptance(
      job, snapshot, reserve=budget.reserve, release=budget.release
    )

  async def recover_retained_uploads(self):
    """Reconcile at most two retained jobs, including those absent from WS delivery."""
    identifiers = await join_history_thread(self.jobs.request_ids)
    eligible = [value for value in identifiers if value not in self.active]
    if self._recovery_cursor is not None:
      eligible = [value for value in eligible if value > self._recovery_cursor] + [
        value for value in eligible if value <= self._recovery_cursor
      ]
    results = {}
    for request_id in eligible[:2]:
      self._recovery_cursor = request_id
      try:
        async with self.runtime._historical_worker_lock:
          if request_id in self.active:
            continue
          if str(request_id) in getattr(
            self.runtime, "_market_upload_tasks", {}
          ) or str(request_id) in getattr(self.runtime, "_market_upload_cache", {}):
            continue
          if await join_history_thread(
            self.runtime.journal.history_upload_retired,
            self.runtime.configuration.device_id,
            str(request_id),
          ):
            await join_history_thread(
              self.runtime._remove_retired_history_files, request_id
            )
            results[str(request_id)] = "RETIRED"
            continue
          job = await join_history_thread(self.jobs.load, request_id)
          failures = await join_history_thread(
            self.runtime.journal.request_collection_aborts,
            self.runtime.configuration.device_id,
            str(request_id),
          )
          accepted = await join_history_thread(self.jobs.upload_acceptance, job)
        if failures:
          pending = [
            (permit, failure)
            for permit, failure, confirmed in failures
            if not confirmed
          ]
          for permit, failure in pending[:2]:
            await self.receipts.abort(permit, failure)
            await join_history_thread(
              self.runtime.journal.confirm_collection_abort, permit
            )
          results[str(request_id)] = (
            "COLLECTION_FAILED" if len(pending) <= 2 else "ABORT_PENDING"
          )
          continue
        snapshot = await self._upload_snapshot(str(request_id))
        if not snapshot.frozen:
          results[str(request_id)] = "WAITING_HISTORY_SESSION"
          continue
        async with self.runtime._historical_worker_lock:
          if request_id in self.active:
            continue
          if accepted is None:
            prepared = await join_history_thread(
              self.runtime._read_retained_history_upload, job
            )
            self._matching_chunks(snapshot, prepared)
            await join_history_thread(self._record_acceptance, job, snapshot)
          elif (
            accepted.chunks != snapshot.chunks
            or accepted.total_chunks != snapshot.total_chunks
          ):
            raise ValueError("history accepted manifest changed before retirement")
          if snapshot.verified_at is not None and datetime.now(
            timezone.utc
          ) - snapshot.verified_at >= timedelta(hours=24):
            await join_history_thread(
              self.runtime._retire_history_job_sync, job, snapshot
            )
            results[str(request_id)] = "RETIRED"
            continue
        results[str(request_id)] = "UPLOAD_ACCEPTED"
      except httpx.HTTPStatusError as exc:
        if exc.response.status_code not in {404, 409}:
          raise
        results[str(request_id)] = "HISTORY_RECOVERY_BLOCKED"
      except (ValueError, KeyError, TypeError, OSError, RuntimeError):
        # One missing/corrupt original job does not prevent another from being
        # reconciled. Retain all bytes; these reasons never authorize collection.
        results[str(request_id)] = "HISTORY_RECOVERY_BLOCKED"
    return results

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

  async def _upload_snapshot(self, request_id):
    runtime = self.runtime
    async with asyncio.timeout(10):
      token = await runtime._history_access_token()
      async with runtime._market_data_upload_client().stream(
        "GET",
        f"{runtime.configuration.api_url}/agent/market-data/{request_id}/upload",
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
      ) as response:
        response.raise_for_status()
        if response.status_code != 200:
          raise ValueError("unexpected history upload snapshot response")
        raw = bytearray()
        async for block in response.aiter_bytes():
          if len(raw) + len(block) > 64 * 1024:
            raise ValueError("history upload snapshot exceeds limit")
          raw.extend(block)
    snapshot = HistoryUploadSnapshot.model_validate_json(raw)
    if str(snapshot.request_id) != request_id:
      raise ValueError("history upload snapshot request mismatch")
    if snapshot.status in {"FAILED", "CANCELLED", "INCOMPLETE"}:
      raise ValueError("history upload request is terminal without acceptance")
    return snapshot

  @staticmethod
  def _matching_chunks(snapshot, prepared):
    if snapshot.total_chunks is not None and snapshot.total_chunks != len(
      prepared.chunks
    ):
      raise ValueError("history upload manifest count mismatch")
    matched = set()
    for actual in snapshot.chunks:
      if actual.index >= len(prepared.chunks):
        raise ValueError("history upload manifest has unexpected chunk")
      chunk = prepared.chunks[actual.index]
      expected = HistoryUploadChunk(
        index=actual.index,
        sha256=chunk.digest,
        record_count=chunk.record_count,
        byte_count=chunk.compressed_bytes,
      )
      if actual != expected:
        raise ValueError("history upload manifest differs from local bytes")
      matched.add(actual.index)
    return matched

  @staticmethod
  def _require_upload_ack(response):
    if response.status_code != 202 or len(response.content) > 4096:
      raise ValueError("unexpected history upload acknowledgement")
    HistoryUploadAcknowledgement.model_validate_json(response.content)

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
      snapshot = await self._upload_snapshot(request_id)
      matched = self._matching_chunks(snapshot, prepared)
      if snapshot.frozen:
        async with runtime._historical_worker_lock:
          await join_history_thread(self._record_acceptance, job, snapshot)
        return
      for index, chunk in enumerate(prepared.chunks):
        if index in matched:
          continue
        response = await runtime._upload_provisional_market_data_chunk(
          client, request_id, index, chunk
        )
        self._require_upload_ack(response)
      response = await runtime._finalize_market_data_upload(
        request_id, len(prepared.chunks), client=client
      )
      self._require_upload_ack(response)
      snapshot = await self._upload_snapshot(request_id)
      self._matching_chunks(snapshot, prepared)
      if not snapshot.frozen:
        raise ValueError("history upload has not frozen its manifest")
      async with runtime._historical_worker_lock:
        await join_history_thread(self._record_acceptance, job, snapshot)
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
      stop_native=self._stop_failed_native,
      abort=self.receipts.abort,
    )
    try:
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
    except CollectionFailed:
      self.active.pop(message.permit.unit.request_id, None)
