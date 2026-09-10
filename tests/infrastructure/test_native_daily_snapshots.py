"""Native multi-partition versions through the default latest-daily HTTP path."""
# ruff: noqa: F811

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from quantx_contracts.daily_snapshot_read import DailySnapshotRead
from quantx_infrastructure.services import (
  local_daily_snapshot_reader,
  native_bar_ingestion,
)
from quantx_infrastructure.services.market_data_transfer_ingestion import (
  ingest_uploaded_bar_request,
)
from quantx_market_data.api import create_app
from sqlalchemy import text

from tests.infrastructure.test_engine_archive_generation import archive_db  # noqa: F401
from tests.infrastructure.test_market_data_transfer_ingestion import (
  SHANGHAI_DAY_START_MS,
  _kline_row,
  _payload,
  _summary,
)
from tests.infrastructure.test_native_bar_ingestion import Source
from tests.infrastructure.test_published_daily_snapshots import SnapshotStorage
from tests.infrastructure.test_realtime_archive_delivery import (
  archive_case,  # noqa: F401
)
from tests.infrastructure.test_realtime_archive_reader import reader_case  # noqa: F401


@pytest.fixture
async def daily_case(reader_case, monkeypatch, tmp_path):
  case = reader_case
  storage = SnapshotStorage()
  monkeypatch.setenv("QUANTX_RUNTIME_DIR", str(tmp_path))
  monkeypatch.setattr(
    native_bar_ingestion, "get_timeseries_connection", lambda: storage
  )
  monkeypatch.setattr(
    local_daily_snapshot_reader, "get_timeseries_connection", lambda: storage
  )
  case.storage = storage
  case.root = tmp_path
  case.start = datetime.fromtimestamp(SHANGHAI_DAY_START_MS / 1000, timezone.utc)
  case.query = DailySnapshotRead(
    instruments=["000001.SZ", "600000.SH"],
    start=case.start,
    end=case.start + timedelta(days=1, hours=23),
  )
  return case


async def source(case, *, newer=False, corrupt=False, tied=False):
  codes = ["000001.SZ"] if newer else case.query.instruments
  payload = _payload(periods=["1d"], stock_list=codes)
  payload["end_time"] = "20231116"
  if newer:
    payload["start_time"] = "20231116"
  records = []
  for code in codes:
    rows = [
      _kline_row(code=code, period="1d", time=SHANGHAI_DAY_START_MS + day * 86400000)
      for day in ([1] if newer else [0, 1])
    ]
    for row in rows:
      row["open"] = row["high"] = row["low"] = row["close"] = 11.0 if newer else 10.0
    records.extend([*rows, _summary(rows, code=code, period="1d")])
  native = Source(case.root, payload, records)
  audit = await ingest_uploaded_bar_request(native, native.identity)
  if corrupt:
    audit["content_verification"]["persisted_sha256"] = "0" * 64
  async with case.engine.begin() as db:
    await db.execute(
      text("""INSERT INTO market_data_request(request_id,status,request_payload,ingestion_result,created_at)
      VALUES (:id,'COMPLETED',CAST(:payload AS JSON),CAST(:audit AS JSON),:created)"""),
      {
        "id": native.identity,
        "payload": json.dumps(payload),
        "audit": json.dumps(audit),
        "created": datetime(2023, 11, 17, 1 if newer and not tied else 0),
      },
    )
  return audit


async def snapshot(case):
  app = create_app(store=case.first, token="internal")
  async with app.router.lifespan_context(app):
    async with httpx.AsyncClient(
      transport=httpx.ASGITransport(app), base_url="http://local"
    ) as client:
      return await client.post(
        "/market-data/internal/v1/history/latest-daily",
        headers={"Authorization": "Bearer internal"},
        json=case.query.model_dump(mode="json"),
      )


async def test_default_snapshot_filters_old_bundle_per_day_before_limit(daily_case):
  case = daily_case
  new = await source(case, newer=True)
  old = await source(case)  # Older request completes and writes later.
  reply = await snapshot(case)
  assert reply.status_code == 200, reply.text
  assert [(row["stock_code"], row["close"]) for row in reply.json()["records"]] == [
    ("000001.SZ", 11.0),
    ("600000.SH", 10.0),
  ]
  query = case.storage.queries[-1]
  assert "FROM kline_1d_versions" in query["query"]
  assert "storage_version IN" not in query["query"]
  assert {
    value
    for key, value in query["query_parameters"].items()
    if key.startswith("version_")
  } == {new["native_storage_version"], old["native_storage_version"]}


@pytest.mark.parametrize("kind", ["corrupt", "tied"])
async def test_invalid_native_selection_never_falls_back_to_old_snapshot(
  daily_case, kind
):
  case = daily_case
  await source(case)
  await source(case, newer=True, corrupt=kind == "corrupt", tied=kind == "tied")
  before = len(case.storage.queries)
  assert (await snapshot(case)).status_code == 503
  assert len(case.storage.queries) == before


async def test_missing_native_publication_does_not_query_canonical_storage(daily_case):
  case = daily_case
  reply = await snapshot(case)
  assert reply.status_code == 200 and reply.json()["records"] == []
  assert not case.storage.queries
