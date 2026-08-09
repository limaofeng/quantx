"""Durable market-data request gateway consumed by qmt-agent."""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select

from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import AgentDevice, MarketDataRequest


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
    "stock_list": stock_list,
    "start_time": start_time,
    "end_time": end_time,
    "periods": periods,
  }
  return await request_agent_market_data(
    payload=payload,
    timeout_seconds=timeout_seconds,
  )


async def request_agent_market_data(
  *,
  payload: dict[str, Any],
  timeout_seconds: float = 600,
) -> dict[str, Any]:
  """Persist one idempotent XTData request and wait for all uploaded chunks."""
  encoded = json.dumps(
    payload,
    sort_keys=True,
    separators=(",", ":"),
    default=str,
  )
  idempotency_key = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
  async with AsyncSessionLocal() as db:
    existing = (
      await db.execute(
        select(MarketDataRequest).where(
          MarketDataRequest.idempotency_key == idempotency_key
        )
      )
    ).scalar_one_or_none()
    if existing is not None and existing.status == "COMPLETED":
      return {"status": "success", "request_id": existing.request_id}
    if existing is None:
      devices = (
        await db.execute(
          select(AgentDevice)
          .where(AgentDevice.revoked_at.is_(None))
          .order_by(AgentDevice.last_seen_at.desc().nullslast())
        )
      ).scalars()
      device = next(
        (
          item
          for item in devices
          if "market-data" in list(item.capabilities or [])
        ),
        None,
      )
      if device is None:
        raise RuntimeError("没有已登记且具备 market-data capability 的 QMT Agent")
      existing = MarketDataRequest(
        request_id=str(uuid.uuid4()),
        device_id=device.id,
        idempotency_key=idempotency_key,
        request_payload=payload,
        status="QUEUED",
        received_chunks=0,
      )
      db.add(existing)
      await db.commit()
    request_id = existing.request_id

  deadline = asyncio.get_running_loop().time() + timeout_seconds
  while asyncio.get_running_loop().time() < deadline:
    async with AsyncSessionLocal() as db:
      request = await db.get(MarketDataRequest, request_id)
      if request is None:
        raise RuntimeError("行情数据请求已不存在")
      if request.status == "COMPLETED":
        return {"status": "success", "request_id": request_id}
      if request.status == "FAILED":
        return {"status": "failed", "request_id": request_id}
    await asyncio.sleep(1)
  return {"status": "failed", "request_id": request_id, "reason": "timeout"}
