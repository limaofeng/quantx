"""Internal HTTP boundaries and stable identities; no implicit database fallback."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from quantx_infrastructure.services.local_market_data_client import (
  LocalMarketDataClient,
)
from quantx_market_data.api import create_app


@pytest.mark.parametrize("change", ["id", "partition", "count", "hash", "path"])
async def test_delivery_proof_client_rejects_wrong_identity_or_contract(change):
  from quantx_contracts.market_data_service import HistoryDemand

  demand = HistoryDemand(instrument="600000.SH", period="1d", trading_date="2026-09-07")
  body = {
    "demand_id": "a" * 64,
    "delivery_id": "b" * 64,
    "partition": demand.model_dump(mode="json"),
    "source_version": "c" * 64,
    "storage_version": "d" * 64,
    "content_sha256": "e" * 64,
    "records_verified": 1,
    "verified_at": "2026-09-07T12:00:00+00:00",
  }
  if change == "id":
    body["demand_id"] = "f" * 64
  elif change == "partition":
    body["partition"]["trading_date"] = "2026-09-08"
  elif change == "count":
    body["records_verified"] = True
  elif change == "hash":
    body["storage_version"] = "invalid"
  else:
    body["storage_reference"] = "/unexpected/local/path"
  client = LocalMarketDataClient(
    transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
    token="internal",
  )
  try:
    with pytest.raises(ValueError):
      await client.history_demand_result("a" * 64, expected_partition=demand)
  finally:
    await client.close()


@pytest.fixture
def store():
  identity = str(uuid4())
  return SimpleNamespace(
    identity=identity,
    create_market_data_request=AsyncMock(return_value=identity),
    market_data_request=AsyncMock(
      return_value={
        "status": "COMPLETED",
        "ingestion_progress": {"phase": "VERIFIED"},
        "ingestion_result": {"operation": "instrument_details", "records_verified": 1},
      }
    ),
  )


async def test_submission_and_verified_result_roundtrip(store):
  app = create_app(store=store, token="internal", reader=object())
  async with app.router.lifespan_context(app):
    client = LocalMarketDataClient(transport=httpx.ASGITransport(app), token="internal")
    try:
      payload = {"operation": "instrument_details", "stock_list": ["000001.SZ"]}
      identity = await client.create_market_data_request(
        payload, idempotency_scope="original-attempt"
      )
      assert identity == store.identity
      store.create_market_data_request.assert_awaited_once_with(
        payload,
        device_id=None,
        required_capabilities=[],
        idempotency_scope="original-attempt",
      )
      value = await client.market_data_request(identity)
      assert value["ingestion_result"]["records_verified"] == 1
      assert store.market_data_request.await_count == 2
    finally:
      await client.close()


@pytest.mark.parametrize(
  "state,phase,code",
  [
    ("PROCESSING", "READBACK", 409),
    ("COMPLETED", "READBACK", 409),
    ("COMPLETED", "VERIFIED", 200),
  ],
)
async def test_result_requires_final_verification_and_auth(store, state, phase, code):
  store.market_data_request.return_value["status"] = state
  store.market_data_request.return_value["ingestion_progress"]["phase"] = phase
  app = create_app(store=store, token="internal", reader=object())
  async with app.router.lifespan_context(app):
    async with httpx.AsyncClient(
      transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
      path = f"/market-data/internal/v1/requests/{store.identity}/result"
      assert (await client.get(path)).status_code == 401
      assert (
        await client.get(path, headers={"Authorization": "Bearer internal"})
      ).status_code == code


async def test_submission_rejects_account_fields_without_store_write(store):
  app = create_app(store=store, token="internal", reader=object())
  async with app.router.lifespan_context(app):
    async with httpx.AsyncClient(
      transport=httpx.ASGITransport(app),
      base_url="http://test",
      headers={"Authorization": "Bearer internal"},
    ) as client:
      response = await client.post(
        "/market-data/internal/v1/requests",
        json={"payload": {"operation": "bars", "account_id": "forbidden"}},
      )
      assert response.status_code == 422
  store.create_market_data_request.assert_not_awaited()


@pytest.mark.parametrize("kind", ["wrong-id", "oversize", "redirect"])
async def test_client_fails_closed_on_invalid_or_redirected_status(kind):
  calls = []

  def handler(request):
    calls.append(request)
    if kind == "wrong-id":
      return httpx.Response(200, json={"request_id": str(uuid4()), "status": "QUEUED"})
    if kind == "oversize":
      return httpx.Response(200, content=b"x" * (2 * 1024 * 1024 + 1))
    return httpx.Response(302, headers={"Location": "https://elsewhere.invalid"})

  client = LocalMarketDataClient(
    transport=httpx.MockTransport(handler), token="internal"
  )
  try:
    with pytest.raises((ValueError, httpx.HTTPStatusError)):
      await client.market_data_request(str(uuid4()))
    assert len(calls) == 1 and calls[0].url.host == "127.0.0.1"
  finally:
    await client.close()


async def test_flow_uses_local_api_without_database_fallback(store, monkeypatch):
  from quantx_worker.prefector.flows import durable_agent_flows as flows

  app = create_app(store=store, token="internal", reader=object())
  async with app.router.lifespan_context(app):
    client = LocalMarketDataClient(transport=httpx.ASGITransport(app), token="internal")
    monkeypatch.setattr(flows, "LocalMarketDataClient", lambda: client)

    def forbidden():
      pytest.fail(
        "history submission/status cannot access the business worker database"
      )

    monkeypatch.setattr(flows, "DurableRuntimeStore", forbidden)
    created = AsyncMock()
    result = await flows._request_and_wait(
      {"operation": "instrument_details", "stock_list": ["000001.SZ"]},
      idempotency_scope="same-demand",
      on_created=created,
    )
    assert result["status"] == "completed" and result["records_verified"] == 1
    assert result["request_id"] == store.identity
    created.assert_awaited_once_with(store.identity)
    assert (
      store.create_market_data_request.call_args.kwargs["idempotency_scope"]
      == "same-demand"
    )
    assert client.client.is_closed


async def test_result_endpoint_rejects_oversized_audit(store):
  store.market_data_request.return_value["ingestion_result"] = {
    "large": "x" * (2 * 1024 * 1024)
  }
  app = create_app(store=store, token="internal", reader=object())
  async with app.router.lifespan_context(app):
    async with httpx.AsyncClient(
      transport=httpx.ASGITransport(app),
      base_url="http://test",
      headers={"Authorization": "Bearer internal"},
    ) as client:
      response = await client.get(
        f"/market-data/internal/v1/requests/{store.identity}/result"
      )
      assert response.status_code == 503
      assert response.json()["detail"] == "HISTORY_RESULT_UNAVAILABLE"


@pytest.mark.parametrize("invalid", [None, "code", "day", "cursor"])
async def test_history_page_is_bound_to_partition_and_cursor(invalid):
  from datetime import date

  from quantx_contracts.market_data_service import HistoryRead

  stamp = "2026-06-02T16:00:00Z"
  row = {"stock_code": "000001.SZ", "period": "1d", "time": stamp, "close": 12.98}
  if invalid == "code":
    row["stock_code"] = "600000.SH"
  if invalid == "day":
    row["time"] = "2026-06-03T16:00:00Z"
  body = {
    "records": [row],
    "next_after": "2026-06-02T16:01:00Z" if invalid == "cursor" else row["time"],
    "exhausted": False,
  }

  def handler(request):
    assert request.url.params["trading_date"] == "2026-06-03"
    assert request.url.params["page_size"] == "2"
    return httpx.Response(200, json=body)

  client = LocalMarketDataClient(
    transport=httpx.MockTransport(handler), token="internal"
  )
  query = HistoryRead(
    instrument="000001.SZ", period="1d", trading_date=date(2026, 6, 3), page_size=2
  )
  try:
    if invalid:
      with pytest.raises(ValueError):
        await client.read_history(query)
    else:
      page = await client.read_history(query)
      assert page.records[0]["time"] == page.next_after
  finally:
    await client.close()


async def test_engine_previous_daily_read_uses_local_api(store, monkeypatch):
  from datetime import date, datetime, timezone

  from quantx_contracts.market_data_service import HistoryPage
  from quantx_engine import realtime_manager as module

  stamp = datetime(2026, 6, 2, 16, tzinfo=timezone.utc)
  reader = SimpleNamespace(
    read=AsyncMock(
      return_value=HistoryPage(
        records=[
          {"stock_code": "000001.SZ", "period": "1d", "time": stamp, "close": 12.98}
        ],
        next_after=stamp,
        exhausted=False,
      )
    )
  )
  app = create_app(store=store, token="internal", reader=reader)
  async with app.router.lifespan_context(app):
    client = LocalMarketDataClient(transport=httpx.ASGITransport(app), token="internal")
    monkeypatch.setattr(module, "LocalMarketDataClient", lambda: client)
    manager = module.RealTimeDataManager.__new__(module.RealTimeDataManager)
    values = await manager._read_previous_daily_klines("000001.SZ", date(2026, 6, 3))
    assert values[0].close == 12.98 and values[0].time == stamp
    assert client.client.is_closed
    query = reader.read.call_args.args[0]
    assert query.period == "1d" and query.trading_date == date(2026, 6, 3)
