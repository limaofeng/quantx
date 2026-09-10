"""Local Caddy client; no database access, redirects or implicit task retries."""

import json
import os
from datetime import datetime

import httpx
from quantx_contracts.daily_snapshot_read import (
  DailyBarsResult,
  DailySnapshotRead,
  DailySnapshotResult,
  LatestDailyDateRequest,
  LatestDailyDateResult,
)
from quantx_contracts.development_reference import (
  REFERENCE_REQUEST,
  CalendarRequest,
  CalendarSnapshot,
  ReferenceAccepted,
  ReferenceStatus,
)
from quantx_contracts.divid_factor_read import DividFactorRead, DividFactorWindow
from quantx_contracts.history_collection_api import (
  MAX_HISTORY_RESULT_BYTES,
  HistoryCollectionAccepted,
  HistoryCollectionResult,
  HistoryCollectionSubmission,
)
from quantx_contracts.market_data_service import (
  HistoryDemand,
  HistoryDemandAccepted,
  HistoryDemandResult,
  HistoryDemandStatus,
  HistoryPage,
  HistoryRead,
)


class LocalMarketDataClient:
  async def register_archive_scope(self, scope, *, recover=False):
    from quantx_contracts.realtime_archive import ArchiveRecoveryScope

    result = ArchiveRecoveryScope.model_validate(
      await self._json(
        "POST",
        "/market-data/internal/v1/archives/scopes" + ("/recover" if recover else ""),
        json=scope.model_dump(mode="json"),
      )
    )
    if result != scope:
      raise ValueError("archive scope identity mismatch")
    return result

  async def submit_archive(self, request):
    from quantx_contracts.realtime_archive import ArchiveAccepted

    result = ArchiveAccepted.model_validate(
      await self._json(
        "POST",
        "/market-data/internal/v1/archives",
        json=request.model_dump(mode="json"),
      )
    )
    if result.request_id != request.identity():
      raise ValueError("archive acceptance identity mismatch")
    return result.request_id

  async def archive_status(self, request):
    from quantx_contracts.realtime_archive import ArchiveStatus

    value = await self._json(
      "GET", "/market-data/internal/v1/archives/" + request.identity()
    )
    if value is None:
      return None
    result = ArchiveStatus.model_validate(value)
    if result.request != request:
      raise ValueError("archive status request mismatch")
    return result

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

  async def submit_reference_request(self, request):
    request = REFERENCE_REQUEST.validate_python(request)
    value = await self._json(
      "POST",
      "/market-data/internal/v1/reference-requests",
      json=request.model_dump(mode="json"),
    )
    return ReferenceAccepted.model_validate(value).request_id

  async def reference_status(self, identity, *, expected_request):
    identity = HistoryDemandAccepted(demand_id=identity).demand_id
    value = await self._json(
      "GET", "/market-data/internal/v1/reference-requests/" + identity
    )
    if value is None:
      return None
    result = ReferenceStatus.model_validate(value)
    if result.request_id != identity or result.request != expected_request:
      raise ValueError("reference request identity mismatch")
    return result

  async def submit_history_demand(self, demand: HistoryDemand) -> str:
    value = await self._json(
      "POST",
      "/market-data/internal/v1/demands",
      json=demand.model_dump(mode="json"),
    )
    return HistoryDemandAccepted.model_validate(value).demand_id

  async def history_demand(self, demand_id: str, *, expected_partition: HistoryDemand):
    identity = HistoryDemandAccepted(demand_id=demand_id).demand_id
    value = await self._json("GET", "/market-data/internal/v1/demands/" + identity)
    if value is None:
      return None
    result = HistoryDemandStatus.model_validate(value)
    if result.demand_id != identity or result.partition != expected_partition:
      raise ValueError("local history demand identity mismatch")
    return result

  async def history_demand_result(
    self, demand_id: str, *, expected_partition: HistoryDemand
  ):
    identity = HistoryDemandAccepted(demand_id=demand_id).demand_id
    value = await self._json(
      "GET", "/market-data/internal/v1/demands/" + identity + "/result"
    )
    if value is None:
      return None
    result = HistoryDemandResult.model_validate(value)
    if result.demand_id != identity or result.partition != expected_partition:
      raise ValueError("local history result identity mismatch")
    return result

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

  async def read_calendar(self, request: CalendarRequest) -> CalendarSnapshot:
    value = await self._json(
      "GET",
      "/market-data/internal/v1/reference/calendar",
      params=request.model_dump(mode="json"),
    )
    snapshot = CalendarSnapshot.model_validate(value)
    if snapshot.year != request.year or snapshot.market != request.market:
      raise ValueError("local calendar response scope mismatch")
    return snapshot

  async def read_divid_factors(self, request: DividFactorRead) -> DividFactorWindow:
    value = await self._json(
      "GET",
      "/market-data/internal/v1/reference/divid-factors",
      params=request.model_dump(mode="json"),
    )
    window = DividFactorWindow.model_validate(value)
    if window.request != request:
      raise ValueError("local factor response scope mismatch")
    return window

  async def read_daily_bars(self, request: DailySnapshotRead) -> DailyBarsResult:
    result = DailyBarsResult.model_validate(
      await self._json(
        "POST",
        "/market-data/internal/v1/history/daily-bars",
        json=request.model_dump(mode="json"),
      )
    )
    if result.request != request:
      raise ValueError("local daily bars response scope mismatch")
    return result

  async def latest_daily_date(self, request: LatestDailyDateRequest):
    result = LatestDailyDateResult.model_validate(
      await self._json(
        "GET",
        "/market-data/internal/v1/history/latest-daily-date",
        params=request.model_dump(mode="json"),
      )
    )
    if result.request != request:
      raise ValueError("local latest daily date scope mismatch")
    return result.trading_date

  async def read_latest_daily(self, request: DailySnapshotRead) -> DailySnapshotResult:
    value = await self._json(
      "POST",
      "/market-data/internal/v1/history/latest-daily",
      json=request.model_dump(mode="json"),
    )
    result = DailySnapshotResult.model_validate(value)
    if result.request != request:
      raise ValueError("local daily snapshot response scope mismatch")
    return result
