"""Default Data API history selects only committed archive versions."""
# ruff: noqa: F811

import json
import re
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pyarrow as pa
import pytest
from quantx_contracts.market_data_service import HistoryRead
from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.services import local_history_reader, native_bar_ingestion
from quantx_infrastructure.services.local_market_data_client import (
  LocalMarketDataClient,
)
from quantx_market_data.api import create_app
from sqlalchemy import text

from tests.infrastructure.test_engine_archive_generation import archive_db  # noqa: F401
from tests.infrastructure.test_immutable_bar_storage import VersionStorage
from tests.infrastructure.test_market_data_transfer_ingestion import (
  _summary,
  _write_chunk,
)
from tests.infrastructure.test_realtime_archive_delivery import (
  archive_case,  # noqa: F401
  change,
  publish,
)


class ReadStorage(VersionStorage):
  def __init__(self):
    super().__init__()
    self.canonical, self.history_queries = [], []
    self.corruption = None

  def query(self, **kwargs):
    sql, params = kwargs["query"], kwargs["query_parameters"]
    if "storage_version=$storage_version" in sql:
      return super().query(**kwargs)
    self.history_queries.append(kwargs)
    table = re.search(r"FROM (\w+)", sql)[1]
    start = datetime.fromisoformat(re.search(r"time >= '([^']+)'", sql)[1])
    end = datetime.fromisoformat(re.search(r"time < '([^']+)'", sql)[1])
    cursor = re.search(r"time > '([^']+)'", sql)
    after = (
      datetime.fromisoformat(cursor[1]) if cursor else start - timedelta(seconds=1)
    )
    excluded = re.search(r"time NOT IN \(([^)]+)\)", sql)
    excluded = (
      {datetime.fromisoformat(value) for value in re.findall(r"'([^']+)'", excluded[1])}
      if excluded
      else set()
    )
    versions = {
      value
      for key, value in params.items()
      if key.startswith("archive_version_") or key == "storage_version"
    }
    rows = (
      self.canonical
      if table == "kline_1m"
      else [
        row
        for measurement, row in self.points.values()
        if measurement == table and row["storage_version"] in versions
      ]
    )
    rows = [
      dict(row)
      for row in rows
      if row["stock_code"] == params["stock_code"]
      and row["period"] == params["period"]
      and start <= row["time"] < end
      and row["time"] > after
      and row["time"] not in excluded
    ]
    rows = sorted(rows, key=lambda row: row["time"])[
      : int(re.search(r"LIMIT (\d+)", sql)[1])
    ]
    if table.endswith("_versions") and self.corruption and rows:
      if self.corruption == "missing":
        rows = []
      elif self.corruption == "version":
        rows[0]["storage_version"] = "f" * 64
      else:
        rows[0]["close"] += 0.01

    class Reader:
      def __iter__(self):
        if rows:
          yield pa.RecordBatch.from_pylist(rows)

      def close(self):
        pass

    return Reader()


@pytest.fixture
async def reader_case(archive_case, monkeypatch, tmp_path):
  case = archive_case
  storage = ReadStorage()
  case.native_dir, case.native_index = tmp_path, 0
  monkeypatch.setattr(
    native_bar_ingestion, "get_timeseries_connection", lambda: storage
  )
  from quantx_infrastructure.services import realtime_archive_worker

  monkeypatch.setattr(
    realtime_archive_worker, "get_timeseries_connection", lambda: storage
  )
  monkeypatch.setattr(
    local_history_reader, "get_timeseries_connection", lambda: storage
  )
  monkeypatch.setattr(settings, "environment", "production")
  async with case.engine.begin() as db:
    # Empty development publication directory. Its full publication transaction
    # is tested by the default delivery suite; here it must not route to raw data.
    await db.execute(
      text(
        "ALTER TABLE development_data_export ADD COLUMN state text, ADD COLUMN request json, ADD COLUMN manifest json"
      )
    )
    await db.execute(
      text("CREATE TABLE development_data_ingestion(delivery_id text,progress jsonb)")
    )
    await db.execute(
      text("""CREATE TABLE development_data_bar_version(
      delivery_id text,stock_code text,period text,trading_date date,source_version text,
      storage_version text,proof jsonb,verified_at timestamptz,records bigint,content_sha256 text)
    """)
    )
  result = SimpleNamespace(**{**vars(case), "storage": storage})
  yield result


async def history(case, *, development=False, after=None, page_size=2):
  settings_value = settings.environment
  settings.environment = "development" if development else "production"
  try:
    app = create_app(store=case.first, token="internal")
    async with app.router.lifespan_context(app):
      client = LocalMarketDataClient(
        transport=httpx.ASGITransport(app), token="internal"
      )
      try:
        return await client.read_history(
          HistoryRead(
            instrument=case.request.instrument,
            period="1m",
            trading_date=case.request.minute.astimezone(
              ZoneInfo("Asia/Shanghai")
            ).date(),
            after=after,
            page_size=page_size,
          )
        )
      finally:
        await client.close()
  finally:
    settings.environment = settings_value


def original(case, *, offset=0, close=9.5):
  return {
    **case.request.bar.model_dump(),
    "stock_code": case.request.instrument,
    "period": "1m",
    "time": case.request.minute + timedelta(minutes=offset),
    "close": close,
    "low": min(case.request.bar.low, close),
    "high": max(case.request.bar.high, close),
  }


async def publish_native(case, rows, *, payload=None, created_at=None, corrupt=None):
  day = case.request.minute.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d")
  payload = payload or {
    "operation": "bars",
    "download": True,
    "stock_list": [case.request.instrument],
    "periods": ["1m"],
    "start_time": day,
    "end_time": day,
  }
  raw = []
  for row in rows:
    item = {
      **row,
      "code": row["stock_code"],
      "time": int(row["time"].timestamp() * 1000),
    }
    del item["stock_code"]
    for old, new in (
      ("pre_close", "preClose"),
      ("settelement_price", "settlementPrice"),
      ("open_interest", "openInterest"),
      ("suspend_flag", "suspendFlag"),
    ):
      item[new] = item.pop(old)
    raw.append(item)
  directory = case.native_dir / str(case.native_index)
  directory.mkdir()
  manifest = [_write_chunk(directory, [*raw, _summary(raw, period="1m")])]
  from quantx_infrastructure.services.market_data_transfer_ingestion import (
    _validate_bar_manifest,
  )

  audit = await native_bar_ingestion.ingest_native_bar_bundle(
    payload, manifest, _validate_bar_manifest(manifest, payload)
  )
  if corrupt:
    corrupt(audit)
  async with case.engine.begin() as db:
    await db.execute(
      text("""
      INSERT INTO market_data_request(request_id,status,request_payload,ingestion_result,created_at)
      VALUES (:id,'COMPLETED',CAST(:payload AS JSON),CAST(:audit AS JSON),:created_at)
    """),
      {
        "id": f"native-{case.native_index}",
        "payload": json.dumps(payload),
        "audit": json.dumps(audit),
        "created_at": created_at or case.request.minute.replace(tzinfo=None),
      },
    )
  case.native_index += 1
  return audit


async def test_default_reader_merges_selected_versions_before_cursor_limit(reader_case):
  case = reader_case
  case.storage.canonical = [original(case, offset=index) for index in range(4)]
  await publish_native(case, case.storage.canonical)
  for offset in (0, 2):
    request = change(
      case.request,
      minute=case.request.minute + timedelta(minutes=offset),
      sequence=10 + offset,
    )
    await case.client.submit_archive(request)
    await publish(case)
  first = await history(case)
  assert [(row["time"], row["close"]) for row in first.records] == [
    (case.request.minute, 10.1),
    (case.request.minute + timedelta(minutes=1), 9.5),
  ]
  second = await history(case, after=first.next_after)
  assert [row["close"] for row in second.records] == [10.1, 9.5]
  assert (await history(case, after=second.next_after)).exhausted
  native_queries = [
    q["query"]
    for q in case.storage.history_queries
    if "storage_version = $storage_version" in q["query"]
  ]
  assert all("time NOT IN" in query for query in native_queries)
  assert all(q["timeout"] <= 10 for q in case.storage.history_queries)


async def test_default_reader_never_uses_unpublished_canonical_rows(reader_case):
  case = reader_case
  case.storage.canonical = [original(case)]
  with pytest.raises(httpx.HTTPStatusError) as error:
    await history(case)
  assert error.value.response.status_code == 503
  assert not case.storage.history_queries


@pytest.mark.parametrize("same_time", [True, False, "third_conflict"])
async def test_native_publication_uses_source_order_not_finish_order(
  reader_case, same_time
):
  case = reader_case
  created = (case.request.minute + timedelta(hours=6)).replace(tzinfo=None)
  await publish_native(
    case,
    [original(case, close=9.5)],
    created_at=created + (timedelta() if same_time else timedelta(minutes=1)),
  )
  await publish_native(case, [original(case, close=9.4)], created_at=created)
  if same_time == "third_conflict":
    await publish_native(case, [original(case, close=9.4)], created_at=created)
  if same_time:
    with pytest.raises(httpx.HTTPStatusError) as error:
      await history(case)
    assert error.value.response.status_code == 503
  else:
    assert (await history(case)).records[0]["close"] == 9.5


async def test_pending_newest_revision_hides_both_old_version_and_raw_minute(
  reader_case,
):
  case = reader_case
  case.storage.canonical = [original(case), original(case, offset=1)]
  await publish_native(case, case.storage.canonical)
  await case.client.submit_archive(case.request)
  await publish(case)
  await case.client.submit_archive(change(case.request, sequence=11))
  page = await history(case)
  assert len(page.records) == 1 and page.records[0][
    "time"
  ] == case.request.minute + timedelta(minutes=1)


@pytest.mark.parametrize("corruption", ["missing", "version", "content"])
async def test_inconsistent_published_storage_fails_instead_of_using_raw(
  reader_case, corruption
):
  case = reader_case
  case.storage.canonical = [original(case)]
  await case.client.submit_archive(case.request)
  await publish(case)
  case.storage.corruption = corruption
  with pytest.raises(httpx.HTTPStatusError) as error:
    await history(case)
  assert error.value.response.status_code == 503


async def test_development_without_history_delivery_reads_only_verified_archive(
  reader_case,
):
  case = reader_case
  case.storage.canonical = [original(case), original(case, offset=1)]
  await case.client.submit_archive(case.request)
  await publish(case)
  page = await history(case, development=True)
  assert len(page.records) == 1 and page.records[0]["close"] == 10.1
  assert all(
    "FROM kline_1m_versions" in query["query"] for query in case.storage.history_queries
  )
  assert (await history(case, development=True, after=page.next_after)).exhausted


@pytest.mark.parametrize(
  "source_kind",
  [
    "verified",
    "wrong_hash",
    "partial_window",
    "wrong_day",
    "intraday",
    "cached",
    "closing_minute",
    "at_boundary",
  ],
)
async def test_native_history_priority_requires_coverage_and_content_receipt(
  reader_case, source_kind
):
  case = reader_case
  verified = source_kind in {"verified", "at_boundary"}
  case.storage.canonical = [original(case)]
  await case.client.submit_archive(case.request)
  await publish(case)
  day = case.request.minute.date()
  payload = {
    "operation": "bars",
    "download": source_kind != "cached",
    "stock_list": [case.request.instrument],
    "periods": ["1m"],
    "start_time": day.strftime("%Y%m%d"),
    "end_time": day.strftime("%Y%m%d"),
  }
  elapsed = {
    "intraday": timedelta(),
    "closing_minute": timedelta(hours=5, minutes=29),
    "at_boundary": timedelta(hours=5, minutes=30),
  }.get(source_kind, timedelta(hours=6))

  def corrupt(audit):
    if source_kind == "wrong_hash":
      audit["content_verification"]["persisted_sha256"] = "b" * 64
    if source_kind == "wrong_day":
      audit["day_coverage"][0]["trading_date"] = str(day - timedelta(days=1))

  await publish_native(
    case,
    case.storage.canonical,
    payload=payload,
    created_at=(case.request.minute + elapsed).replace(tzinfo=None),
    corrupt=corrupt,
  )
  if source_kind == "partial_window":
    payload["end_time"] += "100000"
    async with case.engine.begin() as db:
      await db.execute(
        text(
          "UPDATE market_data_request SET request_payload=CAST(:payload AS JSON) WHERE request_id='native-0'"
        ),
        {"payload": json.dumps(payload)},
      )
  if source_kind == "wrong_hash":
    with pytest.raises(httpx.HTTPStatusError) as error:
      await history(case)
    assert error.value.response.status_code == 503
    return
  page = await history(case)
  assert page.records[0]["close"] == (9.5 if verified else 10.1)
  if verified:
    assert len(case.storage.history_queries) == 1
    assert (
      "storage_version = $storage_version" in case.storage.history_queries[0]["query"]
    )


async def test_combined_storage_reads_share_the_byte_budget(reader_case):
  case = reader_case
  await case.client.submit_archive(case.request)
  await publish(case)
  await publish_native(case, [original(case, offset=1)])
  for _, value in case.storage.points.values():
    value["unexpected_blob"] = "x" * (2200 * 1024)
  with pytest.raises(httpx.HTTPStatusError) as error:
    await history(case)
  assert error.value.response.status_code == 503
  assert len(case.storage.history_queries) == 2


async def test_directory_limit_rejects_before_influx(reader_case, monkeypatch):
  from quantx_infrastructure.services import realtime_archive_reader

  case = reader_case
  await case.client.submit_archive(case.request)
  await case.client.submit_archive(
    change(case.request, minute=case.request.minute + timedelta(minutes=1), sequence=11)
  )
  monkeypatch.setattr(realtime_archive_reader, "MAX_ARCHIVE_MINUTES", 1)
  with pytest.raises(httpx.HTTPStatusError) as error:
    await history(case)
  assert error.value.response.status_code == 503
  assert not case.storage.history_queries


async def test_development_published_history_version_precedes_realtime_revision(
  reader_case,
):
  from quantx_contracts.data_exchange import HistoryPartitionRequest
  from quantx_infrastructure.services import realtime_archive_worker

  case = reader_case
  await case.client.submit_archive(case.request)
  await publish(case)
  historical = change(
    case.request, sequence=12, bar={**case.request.bar.model_dump(), "close": 9.9}
  )
  realtime_archive_worker.write(historical, case.storage)

  async def expected():
    yield realtime_archive_worker.frame(historical)

  proof = await realtime_archive_worker.verify_persisted_bar_content(
    expected(), connection=case.storage, storage_version=historical.storage_version()
  )
  proof["storage_version"] = historical.storage_version()
  partition = HistoryPartitionRequest(
    instrument=case.request.instrument,
    period="1m",
    trading_date=case.request.minute.date(),
  )
  manifest = {
    "data_version": "a" * 64,
    "local_verification": {"immutable_storage": proof},
  }
  async with case.engine.begin() as db:
    await db.execute(
      text(
        "INSERT INTO development_data_export VALUES ('historical','LOCAL_VERIFIED',CAST(:request AS JSON),CAST(:manifest AS JSON))"
      ),
      {"request": partition.model_dump_json(), "manifest": json.dumps(manifest)},
    )
    await db.execute(
      text(
        "INSERT INTO development_data_ingestion VALUES ('historical','{\"phase\":\"VERIFIED\"}')"
      )
    )
    await db.execute(
      text("""
      INSERT INTO development_data_bar_version(delivery_id,stock_code,period,trading_date,source_version,storage_version,proof,verified_at,records,content_sha256)
      VALUES ('historical',:code,'1m',:day,:source,:version,CAST(:proof AS JSONB),clock_timestamp(),1,:digest)
    """),
      {
        "code": partition.instrument,
        "day": partition.trading_date,
        "source": "a" * 64,
        "version": historical.storage_version(),
        "proof": json.dumps(proof),
        "digest": proof["source_sha256"],
      },
    )
  page = await history(case, development=True)
  assert len(page.records) == 1 and page.records[0]["close"] == 9.9
  assert len(case.storage.history_queries) == 1
  assert (
    case.storage.history_queries[0]["query_parameters"]["storage_version"]
    == historical.storage_version()
  )
