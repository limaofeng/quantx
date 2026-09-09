"""Receipt-selected daily snapshots through real PG and the local HTTP API."""
# ruff: noqa: F811

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pyarrow as pa
import pytest
from quantx_contracts.daily_snapshot_read import DailySnapshotRead
from quantx_infrastructure.services.development_version_ingestion import (
  ingest_development_storage_version,
)
from quantx_infrastructure.services.local_daily_snapshot_reader import read_latest_daily
from quantx_infrastructure.services.local_history_reader import (
  HistoryReadInvalid,
  PublishedHistoryReader,
)
from quantx_market_data.api import create_app
from sqlalchemy import text

from tests.infrastructure.test_development_bar_publication import prepared  # noqa: F401
from tests.infrastructure.test_development_reference_transaction import (
  references,  # noqa: F401
)
from tests.infrastructure.test_immutable_bar_storage import VersionStorage
from tests.infrastructure.test_local_daily_snapshots import STAMP, request, row
from tests.infrastructure.test_local_history_reader import Connection
from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_worker_service import workers  # noqa: F401


@pytest.mark.parametrize("wrong", ["version", "code", "day", "missing"])
def test_snapshot_rejects_unselected_version_rows(wrong):
  record = {**row(), "storage_version": "a" * 64}
  selected = {
    (record["stock_code"], STAMP.astimezone(ZoneInfo("Asia/Shanghai")).date()): "a" * 64
  }
  if wrong == "version":
    record["storage_version"] = "b" * 64
  elif wrong == "code":
    record["stock_code"] = "600000.SH"
  elif wrong == "day":
    record["time"] -= timedelta(days=1)
  else:
    del record["storage_version"]
  connection = Connection([[record]])
  with pytest.raises(HistoryReadInvalid, match="unpublished version"):
    read_latest_daily(connection, request(), published_versions=selected)
  assert connection.closed == 1
  assert "FROM kline_1d_versions" in connection.calls[0]["query"]


def test_empty_publication_does_not_query_unversioned_storage():
  connection = Connection([])
  assert read_latest_daily(connection, request(), published_versions={}).records == []
  assert not connection.calls


class SnapshotStorage(VersionStorage):
  def query(self, **kwargs):
    sql, params = kwargs["query"], kwargs["query_parameters"]
    if "storage_version IN" not in sql:
      return super().query(**kwargs)
    self.queries.append(kwargs)
    assert "FROM kline_1d_versions" in sql
    codes = {value for name, value in params.items() if name.startswith("code_")}
    versions = {value for name, value in params.items() if name.startswith("version_")}
    start = datetime.fromisoformat(re.search(r"time >= '([^']+)'", sql)[1])
    end = datetime.fromisoformat(re.search(r"time <= '([^']+)'", sql)[1])
    columns = [
      name.strip() for name in sql.split("SELECT ")[1].split(" FROM ")[0].split(",")
    ]
    records = sorted(
      [
        {key: row[key] for key in columns}
        for table, row in self.points.values()
        if table == "kline_1d_versions"
        and row["stock_code"] in codes
        and row["storage_version"] in versions
        and start <= row["time"] <= end
      ],
      key=lambda row: (row["stock_code"], row["time"]),
    )

    class Reader:
      def __iter__(self):
        if records:
          yield pa.RecordBatch.from_pylist(records)

      def close(self):
        pass

    return Reader()


@pytest.mark.parametrize(
  "prepared", [{"period": "1d", "start_write": False}], indirect=True
)
async def test_api_uses_same_published_version_for_daily_and_history(prepared):
  case, connection = prepared, SnapshotStorage()
  stamp = datetime(2026, 9, 7, tzinfo=ZoneInfo("Asia/Shanghai"))
  query = DailySnapshotRead(
    instruments=["600000.SH"], start=stamp, end=stamp + timedelta(hours=23)
  )
  reader = PublishedHistoryReader(case.factory, connection)
  assert (await reader.read_latest_daily(query)).records == []
  receipt = await ingest_development_storage_version(
    case.request, case.manifest, case.progress, connection=connection
  )
  version = receipt["local_verification"]["immutable_storage"]["storage_version"]
  # An unselected row with a higher price must not become the latest snapshot.
  table, record = next(iter(connection.points.values()))
  connection.points[("unselected", "row")] = (
    table,
    {**record, "storage_version": "f" * 64, "close": 99.0},
  )
  app = create_app(store=object(), token="internal", reader=reader)
  async with app.router.lifespan_context(app):
    async with httpx.AsyncClient(
      transport=httpx.ASGITransport(app),
      base_url="http://local",
      headers={"Authorization": "Bearer internal"},
    ) as client:
      daily = await client.post(
        "/market-data/internal/v1/history/latest-daily",
        json=query.model_dump(mode="json"),
      )
      history = await client.get(
        "/market-data/internal/v1/history",
        params={
          "instrument": "600000.SH",
          "period": "1d",
          "trading_date": "2026-09-07",
        },
      )
  assert daily.status_code == history.status_code == 200
  assert daily.json()["records"][0]["close"] == 10.1
  assert "storage_version" not in daily.json()["records"][0]
  assert history.json()["records"][0]["storage_version"] == version
  assert connection.queries[-2]["query_parameters"]["version_0"] == version
  async with case.factory() as db:
    await db.execute(
      text(
        "UPDATE development_data_export SET state='WAITING_LOCAL_PROOF' WHERE id='delivery'"
      )
    )
    await db.commit()
  queries = len(connection.queries)
  assert (await reader.read_latest_daily(query)).records == []
  assert len(connection.queries) == queries


@pytest.mark.parametrize(
  "prepared", [{"period": "1d", "start_write": False}], indirect=True
)
async def test_catalog_lookup_and_history_share_one_capacity_slot(
  prepared, monkeypatch
):
  import asyncio

  from quantx_contracts.market_data_service import HistoryRead
  from quantx_infrastructure.services import development_bar_publication as catalog
  from quantx_infrastructure.services.local_history_reader import HistoryReadBusy

  case, connection = prepared, SnapshotStorage()
  reader = PublishedHistoryReader(case.factory, connection)
  entered = asyncio.Event()
  original = catalog.resolve_published_daily_versions

  async def pending(*args):
    entered.set()
    await asyncio.Event().wait()

  monkeypatch.setattr(catalog, "resolve_published_daily_versions", pending)
  task = asyncio.create_task(reader.read_latest_daily(request()))
  try:
    await asyncio.wait_for(entered.wait(), 2)
    with pytest.raises(HistoryReadBusy):
      await reader.read(
        HistoryRead(instrument="600000.SH", period="1d", trading_date="2026-09-07")
      )
    assert not connection.queries
  finally:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
      await task
  monkeypatch.setattr(catalog, "resolve_published_daily_versions", original)
  assert (await reader.read_latest_daily(request())).records == []
