"""Observe an existing ingestion request without acquiring or changing its work."""

import asyncio

from quantx_infrastructure.runtime_store import DurableRuntimeStore


async def wait_for_market_data_ingestion(
  request_id: str, *, timeout_seconds=1800
) -> dict:
  identity = str(request_id or "").strip()
  if not identity:
    raise ValueError("market-data request_id is required")
  store = DurableRuntimeStore()
  try:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while True:
      request = await store.market_data_request(identity)
      if request is None:
        raise RuntimeError("market-data request disappeared while waiting")
      status = str(request.get("status") or "").upper()
      if status == "COMPLETED":
        audit = request.get("ingestion_result")
        if not isinstance(audit, dict):
          raise RuntimeError("COMPLETED market-data request has no ingestion audit")
        return {**audit, "status": "completed", "request_id": identity}
      if status in {"FAILED", "CANCELLED", "BLOCKED"}:
        reason = (request.get("ingestion_progress") or {}).get("reason_code") or status
        raise RuntimeError(f"market-data ingestion did not complete: {reason}")
      remaining = deadline - asyncio.get_running_loop().time()
      if remaining <= 0:
        raise TimeoutError(
          f"market-data wait expired; request remains open: {identity}"
        )
      await asyncio.sleep(min(1, remaining))
  finally:
    await store.close()
