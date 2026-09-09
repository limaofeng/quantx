"""Engine → local HTTP → bounded real PostgreSQL factor reads."""

import asyncio
from datetime import date
from decimal import Decimal

import httpx
import pytest
from quantx_contracts.divid_factor_read import DividFactorRead
from quantx_infrastructure.models.divid_factor import DividFactorTable
from quantx_infrastructure.services.local_market_data_client import (
  LocalMarketDataClient,
)
from quantx_market_data.api import create_app
from sqlalchemy import MetaData, text
from sqlalchemy.schema import CreateTable

from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)


@pytest.fixture
async def factor_api(durable_store):  # noqa: F811
  store, _ = durable_store
  table = DividFactorTable.__table__.to_metadata(MetaData())
  table._prefixes = ["TEMPORARY"]
  async with store.engine.begin() as connection:
    await connection.execute(CreateTable(table))
    await connection.execute(
      text("""
      INSERT INTO divid_factors (stock_code,time,ex_date,interest,stock_bonus,stock_gift,allot_num,allot_price,gugai,dr)
      VALUES ('000001.SZ','2026-06-05','20260605',0.5,0,0,0,0,0,1.047894),
             ('000001.SZ','2026-05-05','20260505',0,0,0,0,0,0,2),
             ('600000.SH','2026-06-05','20260605',0,0,0,0,0,0,3)
    """)
    )
  app = create_app(store=store, token="internal", reader=object())
  async with app.router.lifespan_context(app):
    yield store, app


def query():
  return DividFactorRead(
    instrument="000001.SZ", start_date=date(2026, 6, 5), end_date=date(2026, 6, 5)
  )


async def test_engine_front_adjust_reads_only_requested_window(factor_api, monkeypatch):
  from quantx_engine import realtime_manager as module

  _, app = factor_api
  client = LocalMarketDataClient(transport=httpx.ASGITransport(app), token="internal")
  monkeypatch.setattr(module, "LocalMarketDataClient", lambda: client)
  manager = module.RealTimeDataManager.__new__(module.RealTimeDataManager)
  value = await manager._front_adjust_previous_daily_close(
    "000001.SZ",
    12.69,
    date(2026, 6, 4),
    date(2026, 6, 5),
  )
  assert value == pytest.approx(12.69 / 1.047894)
  assert client.client.is_closed


async def test_read_preserves_precision_and_timezone(factor_api):
  _, app = factor_api
  client = LocalMarketDataClient(transport=httpx.ASGITransport(app), token="internal")
  try:
    result = await client.read_divid_factors(query())
    assert len(result.records) == 1
    row = result.records[0]
    assert row.dr == Decimal("1.047894") and row.interest == Decimal("0.5000")
    assert row.time.isoformat() == "2026-06-05T00:00:00+08:00"
  finally:
    await client.close()


@pytest.mark.parametrize(
  "change", ["duplicate", "wrong_date", "null", "zero_dr", "overflow"]
)
async def test_inconsistent_or_excess_storage_rows_fail_closed(factor_api, change):
  store, app = factor_api
  changes = {
    "duplicate": "INSERT INTO divid_factors (stock_code,time,ex_date,dr) VALUES ('000001.SZ','2026-06-05','20260605',1)",
    "wrong_date": "UPDATE divid_factors SET time='2026-06-06' WHERE stock_code='000001.SZ'",
    "null": "UPDATE divid_factors SET interest=NULL WHERE stock_code='000001.SZ'",
    "zero_dr": "UPDATE divid_factors SET dr=0 WHERE stock_code='000001.SZ'",
    "overflow": "INSERT INTO divid_factors (stock_code,time,ex_date,interest,stock_bonus,stock_gift,allot_num,allot_price,gugai,dr) SELECT '000001.SZ','2026-06-05','20260605',0,0,0,0,0,0,1 FROM generate_series(1,513)",
  }
  async with store.engine.begin() as connection:
    await connection.execute(text(changes[change]))
  client = LocalMarketDataClient(transport=httpx.ASGITransport(app), token="internal")
  try:
    with pytest.raises(httpx.HTTPStatusError) as error:
      await client.read_divid_factors(query())
    assert error.value.response.status_code == 503
  finally:
    await client.close()


async def test_auth_capacity_and_invalid_window(factor_api):
  _, app = factor_api
  async with httpx.AsyncClient(
    transport=httpx.ASGITransport(app), base_url="http://test"
  ) as client:
    path = "/market-data/internal/v1/reference/divid-factors"
    params = query().model_dump(mode="json")
    assert (await client.get(path, params=params)).status_code == 401
    client.headers["Authorization"] = "Bearer internal"
    async with app.state.factor_reader._slot:
      assert (await client.get(path, params=params)).status_code == 429
    for dates in [("2025-01-01", "2026-06-05"), ("2026-06-06", "2026-06-05")]:
      assert (
        await client.get(
          path, params={**params, "start_date": dates[0], "end_date": dates[1]}
        )
      ).status_code == 422
    assert (await client.get(path, params=params)).status_code == 200


async def test_client_rejects_wrong_echoed_window():
  wrong = {**query().model_dump(mode="json"), "instrument": "600000.SH"}
  client = LocalMarketDataClient(
    token="internal",
    transport=httpx.MockTransport(
      lambda request: httpx.Response(200, json={"request": wrong, "records": []})
    ),
  )
  try:
    with pytest.raises(ValueError, match="scope mismatch"):
      await client.read_divid_factors(query())
  finally:
    await client.close()


async def test_empty_local_window_returns_no_coverage_claim(factor_api):
  _, app = factor_api
  client = LocalMarketDataClient(transport=httpx.ASGITransport(app), token="internal")
  try:
    request = DividFactorRead(
      instrument="000002.SZ", start_date=date(2026, 6, 5), end_date=date(2026, 6, 5)
    )
    result = await client.read_divid_factors(request)
    assert result.records == [] and result.request == request
  finally:
    await client.close()


async def test_cancelled_pool_wait_releases_query_capacity(factor_api):
  store, app = factor_api
  reader = app.state.factor_reader
  # Fixture has one connection. Hold it to exercise actual pool cancellation.
  async with store.engine.connect():
    task = asyncio.create_task(reader.read(query()))
    await asyncio.sleep(0)
    assert reader._slot.locked()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
      await task
    assert not reader._slot.locked()
  assert len((await reader.read(query())).records) == 1


async def test_engine_api_failure_closes_client_and_preserves_existing_price(
  monkeypatch,
):
  from quantx_engine import realtime_manager as module

  def unavailable(request):
    raise httpx.ConnectError("local service offline", request=request)

  client = LocalMarketDataClient(
    token="internal", transport=httpx.MockTransport(unavailable)
  )
  monkeypatch.setattr(module, "LocalMarketDataClient", lambda: client)
  manager = module.RealTimeDataManager.__new__(module.RealTimeDataManager)
  assert (
    await manager._front_adjust_previous_daily_close(
      "000001.SZ",
      12.69,
      date(2026, 6, 4),
      date(2026, 6, 5),
    )
    == 12.69
  )
  assert client.client.is_closed
