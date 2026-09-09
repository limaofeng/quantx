"""Dataset acquisition and reference drift checks through local history HTTP."""

from datetime import date, datetime, time

import httpx
import pytest
from quantx_domain.clock import SHANGHAI
from quantx_engine.t_assistant_backtest_data import acquire_backtest_dataset
from quantx_infrastructure.core.data.tick_identity import tick_storage_time
from quantx_infrastructure.services import local_historical_tick_reader as module
from quantx_infrastructure.services.local_history_reader import LocalHistoryReader
from quantx_infrastructure.services.local_market_data_client import (
  LocalMarketDataClient,
)
from quantx_market_data.api import create_app

from tests.engine.unit.test_t_assistant_backtest_evaluation import Calendar, History
from tests.infrastructure.test_local_history_reader import Connection


@pytest.mark.parametrize("change", [None, "tick", "daily", "duplicate_daily"])
async def test_reference_dataset_uses_http_and_rejects_changed_content(
  tmp_path, monkeypatch, change
):
  day = date(2026, 9, 3)
  daily = {
    "stock_code": "600000.SH",
    "period": "1d",
    "time": datetime.combine(day, time.min, SHANGHAI),
    "up_stop_price": 110.0,
    "down_stop_price": 90.0,
  }
  records = []
  async for page in History().iter_tick_pages(stock_code="600000.SH"):
    records.extend(
      {
        **vars(value),
        "period": "tick",
        "time": tick_storage_time(value.source_time_ms, value.tick_ordinal).replace(
          tzinfo=SHANGHAI
        ),
      }
      for value in page
    )
  connection = Connection([[daily], records, []])
  app = create_app(
    store=object(), token="internal", reader=LocalHistoryReader(connection)
  )
  clients = []
  async with app.router.lifespan_context(app):

    def client_factory():
      client = LocalMarketDataClient(
        token="internal", transport=httpx.ASGITransport(app)
      )
      clients.append(client)
      return client

    monkeypatch.setattr(module, "LocalMarketDataClient", client_factory)
    dataset = await acquire_backtest_dataset(
      history=module.LocalHistoricalTickReader(),
      calendar=Calendar(),
      source_version="local-api-fixture",
      instruments=("600000.SH",),
      start=day,
      end=day,
      root=tmp_path,
      latency_ms=0,
    )
    assert dataset.manifest["material"]["status"] == "FROZEN", dataset.manifest[
      "material"
    ].get("failures")
    assert dataset.manifest["material"]["storage"] == "REFERENCE"
    assert all(client.client.is_closed for client in clients)
    assert connection.closed == 3

    if change == "tick":
      records = [{**value, "volume": value["volume"] + 1} for value in records]
    if change == "daily":
      daily = {**daily, "up_stop_price": 111.0}
    daily_rows = (
      [daily, {**daily, "time": daily["time"].replace(hour=1)}]
      if change == "duplicate_daily"
      else [daily]
    )
    connection.pages = iter([daily_rows, records, []])
    if change:
      with pytest.raises(
        ValueError,
        match="BACKTEST_DAILY_REFERENCE_INVALID"
        if change == "duplicate_daily"
        else "BACKTEST_SOURCE_CHANGED",
      ):
        await anext(dataset.events())
    else:
      events = [event async for event in dataset.events()]
      assert len(events) == 12
      assert events[0].market.limit_up == 110.0
    assert all(client.client.is_closed for client in clients)


async def test_bad_acquisition_closes_current_tick_stream_before_return(tmp_path):
  closed = []

  class BrokenHistory(History):
    async def iter_tick_pages(self, **kwargs):
      try:
        async for page in super().iter_tick_pages(**kwargs):
          del page[0].price_tick
          yield page
      finally:
        closed.append(kwargs["stock_code"])

  dataset = await acquire_backtest_dataset(
    history=BrokenHistory(),
    calendar=Calendar(),
    source_version="broken-fixture",
    instruments=("600000.SH",),
    start=date(2026, 9, 3),
    end=date(2026, 9, 3),
    root=tmp_path,
    latency_ms=0,
  )
  assert dataset.manifest["material"]["status"] != "FROZEN"
  assert closed == ["600000.SH"]
