"""Local Caddy client; no database access, redirects or implicit task retries."""

import json
import os
from datetime import datetime

import httpx
from quantx_contracts.history_collection_api import (
  MAX_HISTORY_RESULT_BYTES,
  HistoryCollectionAccepted,
  HistoryCollectionResult,
  HistoryCollectionSubmission,
)
from quantx_contracts.market_data_service import HistoryPage, HistoryRead


class LocalMarketDataClient:
  def __init__(self, *, transport=None, token=None):
    credential = token or os.environ.get("QUANTX_MARKET_DATA_INTERNAL_TOKEN", "")
    if not credential:
      raise RuntimeError("local market data service token is required")
    self.client = httpx.AsyncClient(
      base_url="http://127.0.0.1:8080",
      headers={"Authorization": "Bearer " + credential},
      transport=transport,
      timeout=10,
      follow_redirects=False,
      trust_env=False,
    )

  async def _json(self, method, path, **kwargs):
    async with self.client.stream(method, path, **kwargs) as response:
      if response.status_code == 404:
        return None
      response.raise_for_status()
      content = bytearray()
      async for chunk in response.aiter_bytes():
        content.extend(chunk)
        if len(content) > MAX_HISTORY_RESULT_BYTES:
          raise ValueError("local history response exceeds byte limit")
    return json.loads(content)

  async def create_market_data_request(self, payload, **kwargs):
    request = HistoryCollectionSubmission(payload=payload, **kwargs)
    value = await self._json(
      "POST", "/market-data/internal/v1/requests", json=request.model_dump(mode="json")
    )
    return str(HistoryCollectionAccepted.model_validate(value).request_id)

  async def market_data_request(self, request_id):
    identity = str(HistoryCollectionAccepted(request_id=request_id).request_id)
    path = "/market-data/internal/v1/requests/" + identity
    value = await self._json("GET", path)
    if value is None:
      return None
    if (
      not isinstance(value, dict)
      or value.get("request_id") != identity
      or not isinstance(value.get("status"), str)
    ):
      raise ValueError("local history status identity mismatch")
    if value["status"] == "COMPLETED":
      result = HistoryCollectionResult.model_validate(
        await self._json("GET", path + "/result")
      )
      if str(result.request_id) != identity:
        raise ValueError("local history result identity mismatch")
      value["ingestion_result"] = result.result
    value["processing_error"] = value.get("reason_code")
    return value

  async def close(self):
    await self.client.aclose()

  async def read_history(self, request: HistoryRead) -> HistoryPage:
    value = await self._json(
      "GET",
      "/market-data/internal/v1/history",
      params=request.model_dump(mode="json", exclude_none=True),
    )
    page = HistoryPage.model_validate(value)
    if len(page.records) > request.page_size or page.exhausted != (not page.records):
      raise ValueError("local history page count mismatch")
    start, end = request.bounds()
    previous = request.after
    for row in page.records:
      stamp = datetime.fromisoformat(str(row.get("time")).replace("Z", "+00:00"))
      if (
        stamp.tzinfo is None
        or not start <= stamp < end
        or previous is not None
        and stamp <= previous
        or row.get("stock_code") != request.instrument
        or row.get("period") != request.period
      ):
        raise ValueError("local history page scope mismatch")
      previous = stamp
      row["time"] = stamp
    if page.next_after != (previous if page.records else None):
      raise ValueError("local history page cursor mismatch")
    return page
