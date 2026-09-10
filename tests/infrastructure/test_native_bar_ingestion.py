"""Default native ingest, real compressed manifests and SDK/Arrow version IO."""
# ruff: noqa: F811

from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from quantx_application.market_data.ingestion import IngestionEvidenceConflict
from quantx_contracts.market_data_service import HistoryRead
from quantx_infrastructure.services import local_history_reader, native_bar_ingestion
from quantx_infrastructure.services import market_data_transfer_ingestion as ingestion
from quantx_infrastructure.services.local_market_data_client import (
  LocalMarketDataClient,
)
from quantx_infrastructure.services.market_data_ingestion_progress import evidence_hash
from quantx_market_data import worker
from quantx_market_data.api import create_app

from tests.infrastructure.test_immutable_bar_storage import VersionStorage
from tests.infrastructure.test_market_data_durable_progress import (
  durable_store,  # noqa: F401
)
from tests.infrastructure.test_market_data_transfer_ingestion import (
  SHANGHAI_DAY_START_MS,
  _kline_row,
  _payload,
  _summary,
  _tick_row,
  _write_chunk,
)
from tests.infrastructure.test_market_data_worker_service import workers  # noqa: F401


class Source:
  def __init__(self, root, payload, records):
    self.identity = str(uuid4())
    path = root / "market-data" / self.identity
    path.mkdir(parents=True)
    self.manifest = [_write_chunk(path, records)]
    self.payload = payload

  async def market_data_request(self, request_id):
    assert request_id == self.identity
    return {"request_payload": self.payload, "expected_chunks": 1, "received_chunks": 1}

  async def market_data_transfers(self, request_id):
    assert request_id == self.identity
    return self.manifest


@pytest.fixture
def native(tmp_path, monkeypatch):
  connection = VersionStorage()
  monkeypatch.setenv("QUANTX_RUNTIME_DIR", str(tmp_path))
  monkeypatch.setattr(
    native_bar_ingestion, "get_timeseries_connection", lambda: connection
  )
  monkeypatch.setattr(
    local_history_reader, "get_timeseries_connection", lambda: connection
  )

  def forbidden():
    raise AssertionError("default native ingestion must not write canonical tables")

  monkeypatch.setattr(ingestion, "HistoricalMarketDataService", forbidden)
  return tmp_path, connection


async def test_default_native_bundle_preserves_multiple_codes_periods_and_days(native):
  root, connection = native
  codes, periods = ["000001.SZ", "600000.SH"], ["tick", "1m", "1d"]
  payload = _payload(stock_list=codes, periods=periods)
  payload["end_time"] = "20231116"
  records = []
  for period in periods:
    for code in codes:
      rows = [
        (
          _tick_row(code=code, time=value)
          if period == "tick"
          else _kline_row(code=code, period=period, time=value)
        )
        for value in (SHANGHAI_DAY_START_MS, SHANGHAI_DAY_START_MS + 86400000)
      ]
      records.extend([*rows, _summary(rows, code=code, period=period)])
  source = Source(root, payload, records)
  result = await ingestion.ingest_uploaded_bar_request(source, source.identity)
  assert result["records_saved"] == result["records_verified"] == 12
  assert len(result["day_coverage"]) == 12
  assert all(item["point_count"] == 1 for item in result["day_coverage"])
  assert {table for table, _ in connection.points.values()} == {
    "ticks_versions",
    "kline_1m_versions",
    "kline_1d_versions",
  }
  assert (
    result["content_verification"]["storage_version"]
    == result["native_storage_version"]
  )
  writes = len(connection.lines)
  rechecked = await ingestion.verify_uploaded_bar_request(source, source.identity)
  assert rechecked == result
  assert len(connection.lines) == writes


async def test_legacy_checkpoint_cannot_be_relabelled_as_immutable(native):
  root, connection = native
  row = _tick_row()
  source = Source(root, _payload(), [row, _summary([row])])
  state = {
    "manifest_hash": evidence_hash(
      {
        "payload": source.payload,
        "chunks": [
          {k: v for k, v in item.items() if k != "storage_reference"}
          for item in source.manifest
        ],
      }
    ),
    "phase": "READBACK",
    "write_attempts": 3,
  }
  before = dict(state)
  with pytest.raises(IngestionEvidenceConflict, match="MIGRATION_REQUIRED"):
    await ingestion.ingest_uploaded_bar_request(
      source, source.identity, progress=SimpleNamespace(state=state)
    )
  assert state == before
  assert not connection.lines


@pytest.mark.parametrize("period", ["tick", "1m", "1d"])
async def test_default_native_late_old_write_does_not_change_new_proof(native, period):
  root, connection = native
  old = _tick_row() if period == "tick" else _kline_row(period=period)
  field = "lastPrice" if period == "tick" else "close"
  new = {**old, field: old[field] + 0.1}
  payload = _payload(periods=[period])
  older = Source(root, payload, [old, _summary([old], period=period)])
  newer = Source(root, payload, [new, _summary([new], period=period)])
  first = await ingestion.ingest_uploaded_bar_request(newer, newer.identity)
  late = await ingestion.ingest_uploaded_bar_request(older, older.identity)
  assert first["native_storage_version"] != late["native_storage_version"]
  assert await ingestion.verify_uploaded_bar_request(newer, newer.identity) == first
  assert len(connection.points) == 2


async def test_default_worker_restarts_at_readback_then_default_api_reads_version(
  workers, native, monkeypatch
):
  (store, _), clock = workers
  root, connection = native
  assert await store.acquire()
  row = _tick_row()
  store.manifest = [_write_chunk(root, [row, _summary([row])])]
  query = connection.query
  fail = True

  def transient(**kwargs):
    if fail:
      raise TimeoutError("injected readback failure")
    return query(**kwargs)

  monkeypatch.setattr(connection, "query", transient)
  await worker.sweep(store)
  pending = await store.market_data_request("request-1")
  assert pending["ingestion_progress"]["phase"] == "READBACK"
  assert pending["status"] != "COMPLETED"
  writes = len(connection.lines)
  assert writes == 1
  fail = False
  clock[0] += timedelta(minutes=1)
  assert await worker.sweep(store) == 1
  completed = await store.market_data_request("request-1")
  assert completed["status"] == "COMPLETED"
  assert completed["ingestion_result"]["native_storage_version"]
  assert len(connection.lines) == writes
  app = create_app(store=store, token="native-test")
  async with app.router.lifespan_context(app):
    client = LocalMarketDataClient(
      transport=httpx.ASGITransport(app), token="native-test"
    )
    try:
      page = await client.read_history(
        HistoryRead(
          instrument="600000.SH",
          period="tick",
          trading_date=date(2023, 11, 15),
        )
      )
      assert len(page.records) == 1
      assert page.records[0]["stock_code"] == "600000.SH"
      assert page.records[0]["last_price"] == row["lastPrice"]
    finally:
      await client.close()
