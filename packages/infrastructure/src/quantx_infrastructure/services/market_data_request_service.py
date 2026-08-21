"""Durable market-data request gateway consumed by qmt-agent."""

from __future__ import annotations

import asyncio
from typing import Any

from quantx_infrastructure.runtime_store import DurableRuntimeStore
from quantx_infrastructure.services.market_data_transfer_ingestion import (
  claim_ingest_and_finish_market_data_request,
)

_MAX_FAILED_REQUEST_RETRY_HOPS = 32
_T_TRADE_REPLAY_SUPPLEMENT_SCOPE = "t-trade-replay-supplement-v1"


def _failed_request_retry_scope(request_id: str) -> str:
  """Derive one stable retry generation from the poisoned request it replaces."""

  return f"market-data-failed-retry:{request_id}"


async def recover_failed_market_data_request(
  store: DurableRuntimeStore,
  *,
  payload: dict[str, Any],
  request_id: str,
  reopen_attempted: set[str],
  retry_hops: int,
  device_id: str | None = None,
) -> tuple[str, int, bool] | None:
  """Reopen one failed transfer or derive one bounded retry generation.

  The returned boolean says whether a replacement request was selected.
  ``None`` means the retry chain reached its hard safety bound.
  """
  if request_id not in reopen_attempted:
    reopen_attempted.add(request_id)
    try:
      await store.reopen_failed_market_data_request(request_id)
    except RuntimeError:
      # Another consumer may have reopened or completed it after our read.
      # Re-read before deriving a replacement generation.
      current = await store.market_data_request(request_id)
      current_status = str((current or {}).get("status") or "MISSING").upper()
      if current is not None and current_status != "FAILED":
        return request_id, retry_hops, False
    else:
      return request_id, retry_hops, False

  if retry_hops >= _MAX_FAILED_REQUEST_RETRY_HOPS:
    return None
  create_kwargs: dict[str, Any] = {
    "idempotency_scope": _failed_request_retry_scope(request_id),
  }
  if device_id is not None:
    create_kwargs["device_id"] = device_id
  replacement_id = await store.create_market_data_request(
    payload,
    **create_kwargs,
  )
  return replacement_id, retry_hops + 1, True


def build_sync_lock_key(complete_key: str) -> str:
  return f"market-data-request-lock:{complete_key}"


async def request_market_data_sync(
  *,
  stock_list: list[str],
  start_time: str,
  end_time: str,
  periods: list[str],
  timeout_seconds: float = 600,
) -> dict[str, Any]:
  payload = {
    "operation": "bars",
    "download": True,
    "stock_list": stock_list,
    "start_time": start_time,
    "end_time": end_time,
    "periods": periods,
  }
  return await request_agent_market_data(
    payload=payload,
    timeout_seconds=timeout_seconds,
  )


async def queue_market_data_sync(
  *,
  stock_list: list[str],
  start_time: str,
  end_time: str,
  periods: list[str],
) -> dict[str, Any]:
  """Best-effort queueing for a future replay without waiting on the Agent."""

  payload = {
    "operation": "bars",
    "download": True,
    "stock_list": stock_list,
    "start_time": start_time,
    "end_time": end_time,
    "periods": periods,
  }
  return await queue_agent_market_data(
    payload=payload,
    # The payload already contains instrument/window/period. A fixed scope
    # deduplicates the same gap across separate replay runs instead of creating
    # one permanently queued request per user click.
    idempotency_scope=_T_TRADE_REPLAY_SUPPLEMENT_SCOPE,
  )


async def queue_agent_market_data(
  *,
  payload: dict[str, Any],
  idempotency_scope: str,
) -> dict[str, Any]:
  """Queue only when a fresh market-data Agent is connected.

  This path intentionally does not wait for download/upload/ingestion.  It is
  used by isolated historical replays whose current result must be based only
  on the data already persisted in InfluxDB.  A queued transfer can improve a
  later replay, while an offline Agent leaves no permanently pending request.
  """

  store = DurableRuntimeStore()
  try:
    device_id = await store.available_market_data_device()
    if not device_id:
      return {
        "status": "skipped",
        "reason": "market_data_agent_unavailable",
      }
    request_id = await store.create_market_data_request(
      payload,
      device_id=device_id,
      idempotency_scope=idempotency_scope,
    )
    reopen_attempted: set[str] = set()
    retry_hops = 0
    newly_queued_retry_ids: set[str] = set()
    while True:
      request = await store.market_data_request(request_id)
      status = str((request or {}).get("status") or "MISSING").upper()
      if status == "COMPLETED":
        ingestion_result = (request or {}).get("ingestion_result")
        if not isinstance(ingestion_result, dict):
          raise RuntimeError(
            "COMPLETED market-data request is missing its ingestion audit"
          )
        return {
          "status": "success",
          "request_id": request_id,
          "device_id": device_id,
          **ingestion_result,
        }
      if status == "FAILED":
        if request_id in newly_queued_retry_ids:
          return {
            "status": "failed",
            "request_id": request_id,
            "device_id": device_id,
            "reason": (request or {}).get("processing_error"),
          }
        recovery = await recover_failed_market_data_request(
          store,
          payload=payload,
          request_id=request_id,
          reopen_attempted=reopen_attempted,
          retry_hops=retry_hops,
          device_id=device_id,
        )
        if recovery is None:
          return {
            "status": "failed",
            "request_id": request_id,
            "device_id": device_id,
            "reason": "market-data failed-request retry chain exceeded safe limit",
          }
        request_id, retry_hops, replacement_created = recovery
        if replacement_created:
          replacement = await store.market_data_request(request_id)
          if replacement is None:
            raise RuntimeError("行情数据重试请求已不存在")
          if str(replacement.get("status") or "MISSING").upper() != "FAILED":
            newly_queued_retry_ids.add(request_id)
        continue
      if status in {"UPLOADED", "PROCESSING"}:
        ingestion = await claim_ingest_and_finish_market_data_request(
          store,
          request_id,
        )
        if ingestion is None:
          return {
            "status": "queued",
            "request_id": request_id,
            "device_id": device_id,
          }
        if ingestion.get("status") == "completed":
          return {
            **ingestion,
            "status": "success",
            "device_id": device_id,
          }
        if ingestion.get("status") == "retryable":
          return {
            **ingestion,
            "status": "queued",
            "device_id": device_id,
          }
        # A reopened complete transfer can still fail validation or storage.
        # Its terminal FAILED state is eligible for a fresh retry generation.
        if request_id in reopen_attempted:
          continue
        return {
          **ingestion,
          "status": "failed",
          "device_id": device_id,
        }
      return {
        "status": "queued",
        "request_id": request_id,
        "device_id": device_id,
      }
  finally:
    await store.close()


async def request_agent_market_data(
  *,
  payload: dict[str, Any],
  timeout_seconds: float = 600,
) -> dict[str, Any]:
  """Request, ingest, and terminally converge one idempotent XTData transfer."""
  store = DurableRuntimeStore()
  try:
    request_id = await store.create_market_data_request(payload)
    reopen_attempted: set[str] = set()
    retry_hops = 0
    newly_queued_retry_ids: set[str] = set()
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
      request = await store.market_data_request(request_id)
      if request is None:
        raise RuntimeError("行情数据请求已不存在")
      status = str(request.get("status") or "MISSING").upper()
      if status == "COMPLETED":
        ingestion_result = request.get("ingestion_result")
        if not isinstance(ingestion_result, dict):
          raise RuntimeError(
            "COMPLETED market-data request is missing its ingestion audit"
          )
        return {
          "status": "success",
          "request_id": request_id,
          **ingestion_result,
        }
      if status == "FAILED":
        # A request created during this invocation already received one fresh
        # Agent attempt.  Return its concrete failure instead of spinning and
        # producing an unbounded retry chain in one caller deadline.
        if request_id in newly_queued_retry_ids:
          return {
            "status": "failed",
            "request_id": request_id,
            "reason": request.get("processing_error"),
          }

        recovery = await recover_failed_market_data_request(
          store,
          payload=payload,
          request_id=request_id,
          reopen_attempted=reopen_attempted,
          retry_hops=retry_hops,
        )
        if recovery is None:
          return {
            "status": "failed",
            "request_id": request_id,
            "reason": "market-data failed-request retry chain exceeded safe limit",
          }
        request_id, retry_hops, replacement_created = recovery
        if replacement_created:
          replacement = await store.market_data_request(request_id)
          if replacement is None:
            raise RuntimeError("行情数据重试请求已不存在")
          if str(replacement.get("status") or "MISSING").upper() != "FAILED":
            newly_queued_retry_ids.add(request_id)
        continue
      if status in {"UPLOADED", "PROCESSING"}:
        ingestion = await claim_ingest_and_finish_market_data_request(
          store,
          request_id,
        )
        if ingestion is not None:
          if ingestion["status"] == "completed":
            return {
              **ingestion,
              "status": "success",
            }
          if ingestion["status"] == "retryable":
            await asyncio.sleep(1)
            continue
          # A structurally complete transfer that was reopened can still fail
          # checksum/decoding/persistence validation.  Let the next loop create
          # a new deterministic Agent retry generation instead of reusing the
          # same poisoned transfer forever.
          if request_id in reopen_attempted:
            continue
          return ingestion
      await asyncio.sleep(1)
    return {
      "status": "timeout",
      "request_id": request_id,
      "reason": "wait attempt expired; durable request remains open",
    }
  finally:
    await store.close()
