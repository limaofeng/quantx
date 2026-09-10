"""Research batch reads via Data API, actual PG directory and native SDK storage."""

# ruff: noqa: F811
import httpx
import pytest
from quantx_infrastructure.services import local_history_reader
from quantx_infrastructure.services.local_market_data_client import (
  LocalMarketDataClient,
)
from quantx_market_data.api import create_app
from quantx_research.data.source import InfrastructureResearchDataSource

from tests.infrastructure.test_engine_archive_generation import archive_db  # noqa: F401
from tests.infrastructure.test_native_daily_snapshots import (  # noqa: F401
  daily_case,
  source,
)
from tests.infrastructure.test_realtime_archive_delivery import (
  archive_case,  # noqa: F401
)
from tests.infrastructure.test_realtime_archive_reader import reader_case  # noqa: F401


async def test_research_reads_all_fixed_partitions_and_resolves_persisted_latest(
  daily_case, monkeypatch
):
  case = daily_case
  await source(case, newer=True)
  await source(case)
  monkeypatch.setattr(
    local_history_reader, "get_timeseries_connection", lambda: case.storage
  )
  app = create_app(store=case.first, token="internal")
  async with app.router.lifespan_context(app):
    client = LocalMarketDataClient(token="internal", transport=httpx.ASGITransport(app))
    try:
      research = InfrastructureResearchDataSource(market_data_client=client)
      rows = await research.load_daily_bars(
        case.query.instruments, case.query.start, case.query.end
      )
      assert len(rows) == 4
      assert rows[rows.stock_code == "000001.SZ"].close.tolist() == [10, 11]
      assert rows[rows.stock_code == "600000.SH"].close.tolist() == [10, 10]
      assert rows.suspend_flag.tolist() == [0, 0, 0, 0]
      assert len(case.storage.queries) >= 1
      assert (await research.latest_daily_date("000001.SZ")).isoformat() == "2023-11-16"
      with pytest.raises(ValueError, match="已持久化"):
        await research.latest_daily_date("999999.SH")
    finally:
      await client.close()


async def test_corrupt_native_directory_never_returns_partial_batch(daily_case):
  case = daily_case
  await source(case)
  await source(case, newer=True, corrupt=True)
  app = create_app(store=case.first, token="internal")
  async with app.router.lifespan_context(app):
    client = LocalMarketDataClient(token="internal", transport=httpx.ASGITransport(app))
    try:
      research = InfrastructureResearchDataSource(market_data_client=client)
      before = len(case.storage.queries)
      with pytest.raises(httpx.HTTPStatusError) as error:
        await research.load_daily_bars(
          case.query.instruments, case.query.start, case.query.end
        )
      assert error.value.response.status_code == 503
      assert len(case.storage.queries) == before
    finally:
      await client.close()
