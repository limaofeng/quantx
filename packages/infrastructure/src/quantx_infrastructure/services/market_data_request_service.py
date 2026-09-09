"""Durable market-data request gateway consumed by qmt-agent."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from typing import Any

from quantx_infrastructure.runtime_store import DurableRuntimeStore

# v2 separates replay supplements from completed v1 transfers created before
# the corrected QMT intraday download boundary was deployed.
_T_TRADE_REPLAY_SUPPLEMENT_SCOPE = "t-trade-replay-supplement-v2"


def build_sync_lock_key(complete_key: str) -> str:
  return f"market-data-request-lock:{complete_key}"


async def load_completed_empty_tick_days(
  *,
  instrument_code: str,
  trading_dates: list[date],
) -> set[date]:
  """Return only Tick days with dual strict-empty transfer proof.

  The store query accepts a date only if its Tick and ``1d`` requests are both
  completed, persistence-verified, exact-single-day ``XT_DATA_NO_ROWS``
  transfers, and no completed exact-day ``1d`` audit contradicts that result.
  This defensive parser intentionally accepts only canonical zero-count rows;
  malformed rows cannot turn a missing Tick day into a replay exception.
  """

  normalized_dates = sorted(set(trading_dates))
  if not normalized_dates:
    return set()
  store = DurableRuntimeStore()
  try:
    rows = await store.completed_tick_day_coverage(
      instrument_code=instrument_code,
      trading_dates=normalized_dates,
    )
  finally:
    await store.close()
  requested_dates = set(normalized_dates)
  empty_days: set[date] = set()
  for row in rows:
    raw_point_count = row.get("point_count")
    if isinstance(raw_point_count, bool):
      continue
    if isinstance(raw_point_count, int):
      point_count_is_zero = raw_point_count == 0
    elif isinstance(raw_point_count, str):
      point_count_is_zero = raw_point_count == "0"
    else:
      point_count_is_zero = False
    if not point_count_is_zero:
      continue
    raw_day = row.get("trading_date")
    if isinstance(raw_day, datetime):
      parsed_day = raw_day.date()
    elif isinstance(raw_day, date):
      parsed_day = raw_day
    elif isinstance(raw_day, str):
      try:
        parsed_day = date.fromisoformat(raw_day)
      except ValueError:
        continue
    else:
      continue
    if parsed_day in requested_dates:
      empty_days.add(parsed_day)
  return empty_days


async def request_market_data_sync(
  *,
  stock_list: list[str],
  start_time: str,
  end_time: str,
  periods: list[str],
  timeout_seconds: float = 600,
  idempotency_scope: str = _T_TRADE_REPLAY_SUPPLEMENT_SCOPE,
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
    idempotency_scope=idempotency_scope,
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
      if status == "BLOCKED":
        return {
          "status": "blocked",
          "request_id": request_id,
          "reason": request.get("processing_error"),
        }
      if status in {"FAILED", "CANCELLED"}:
        return {
          "status": status.lower(),
          "request_id": request_id,
          "reason": request.get("processing_error"),
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
  idempotency_scope: str = "",
) -> dict[str, Any]:
  """Request, ingest, and terminally converge one idempotent XTData transfer."""
  from quantx_infrastructure.config.settings import settings

  if settings.environment == "development":
    from quantx_infrastructure.services.development_history_import import (
      request_remote_history,
    )

    return await request_remote_history(payload, timeout_seconds=timeout_seconds)
  store = DurableRuntimeStore()
  try:
    create_kwargs: dict[str, Any] = {}
    if idempotency_scope:
      create_kwargs["idempotency_scope"] = idempotency_scope
    request_id = await store.create_market_data_request(payload, **create_kwargs)
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
      if status == "BLOCKED":
        return {
          "status": "blocked",
          "request_id": request_id,
          "reason": request.get("processing_error"),
        }
      if status in {"FAILED", "CANCELLED"}:
        return {
          "status": status.lower(),
          "request_id": request_id,
          "reason": request.get("processing_error"),
        }
      await asyncio.sleep(1)
    return {
      "status": "timeout",
      "request_id": request_id,
      "reason": "wait attempt expired; durable request remains open",
    }
  finally:
    await store.close()
