from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from quantx_contracts.data_exchange import HistoryPartitionRequest
from quantx_infrastructure.services.data_exchange import content_path
from quantx_infrastructure.services.market_data_transfer_ingestion import (
  MarketDataValidationError,
  _iter_transfer_chunks,
  validate_bar_records_against_request,
)
from quantx_worker.prefector.flows.development_data_export_flow import (
  _has_positive_source_coverage,
  find_reusable_source_request,
  partition_records,
  publish,
)


def bar(code="600000.SH"):
  return {
    "code": code,
    "period": "1m",
    "time": int(
      datetime(2026, 9, 7, 9, 31, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000
    ),
    "open": 10.0,
    "high": 10.2,
    "low": 9.9,
    "close": 10.1,
    "preClose": 9.95,
    "volume": 1000.0,
    "amount": 10000.0,
    "suspendFlag": 0,
    "settlementPrice": 0.0,
    "openInterest": 0,
  }


def test_export_filters_scope_and_preserves_ingestion_contract(monkeypatch, tmp_path):
  monkeypatch.setenv("QUANTX_DATA_EXPORT_ROOT", str(tmp_path))
  request = HistoryPartitionRequest(
    instrument="600000.SH", period="1m", trading_date=date(2026, 9, 7)
  )
  records = partition_records([[bar(), bar("600001.SH")]], request)
  validate_bar_records_against_request(records, request.agent_payload())
  manifest = publish(records)
  files = [
    {**item, "storage_reference": str(content_path(item["checksum_sha256"]))}
    for item in manifest
  ]
  assert [
    record for chunk in _iter_transfer_chunks(files) for record in chunk
  ] == records
  assert publish(records) == manifest
  assert len(list(tmp_path.glob("*.json.gz"))) == 1


def test_corrupt_export_cannot_be_imported(monkeypatch, tmp_path):
  monkeypatch.setenv("QUANTX_DATA_EXPORT_ROOT", str(tmp_path))
  manifest = publish([bar()])
  item = manifest[0]
  path = content_path(item["checksum_sha256"])
  path.write_bytes(b"corrupt")
  with pytest.raises(MarketDataValidationError, match="checksum"):
    list(_iter_transfer_chunks([{**item, "storage_reference": str(path)}]))


def test_missing_rows_are_not_successful_empty_coverage():
  request = HistoryPartitionRequest(
    instrument="600000.SH", period="tick", trading_date=date(2026, 9, 7)
  )
  with pytest.raises(ValueError, match="COVERAGE_MISSING"):
    partition_records([[]], request)


async def test_reusable_source_requires_positive_target_day_coverage():
  request = HistoryPartitionRequest(
    instrument="000001.SZ", period="tick", trading_date=date(2026, 8, 3)
  )

  class Connection:
    def __init__(self):
      self.statement = ""
      self.parameters = {}

    async def scalar(self, statement, parameters):
      self.statement = str(statement)
      self.parameters = parameters
      return None

  connection = Connection()
  assert (
    await find_reusable_source_request(
      connection, request, request.agent_payload()
    )
    is None
  )

  sql = " ".join(connection.statement.split())
  assert "json_array_elements" in sql
  assert "day_coverage.value->>'instrument_code' = :instrument" in sql
  assert "REPLACE(day_coverage.value->>'trading_date', '-', '') = :day" in sql
  assert "day_coverage.value->>'point_count' ~ '^[1-9][0-9]*$'" in sql
  assert connection.parameters == {
    "codes": '["000001.SZ"]',
    "periods": '["tick"]',
    "day": "20260803",
    "instrument": "000001.SZ",
    "period": "tick",
  }


def test_linked_zero_coverage_source_is_not_reused_on_retry():
  request = HistoryPartitionRequest(
    instrument="000001.SZ", period="tick", trading_date=date(2026, 8, 3)
  )
  assert not _has_positive_source_coverage(
    {
      "ingestion_result": {
        "day_coverage": [
          {
            "instrument_code": "000001.SZ",
            "period": "tick",
            "trading_date": "2026-08-03",
            "point_count": 0,
          }
        ]
      }
    },
    request,
  )
  assert _has_positive_source_coverage(
    {
      "ingestion_result": {
        "day_coverage": [
          {
            "instrument_code": "000001.SZ",
            "period": "tick",
            "trading_date": "2026-08-03",
            "point_count": 1,
          }
        ]
      }
    },
    request,
  )


def test_digest_cannot_escape_export_directory():
  with pytest.raises(ValueError):
    content_path("../credentials")


@pytest.mark.parametrize("reason", [
  "SOURCE_COVERAGE_MISSING", "PERSISTED_COVERAGE_UNPROVEN",
  "PERSISTED_COVERAGE_CHANGED", "HISTORICAL_SOURCE_IDENTITY_MISSING",
])
def test_safe_export_error_preserves_known_codes(reason):
  from quantx_worker.prefector.flows.development_data_export_flow import (
    safe_export_error,
  )

  assert safe_export_error(ValueError(reason)) == reason
  assert safe_export_error(ValueError("private connection detail")) == "ValueError"


async def test_empty_replacement_does_not_create_another_request(monkeypatch):
  from contextlib import asynccontextmanager
  from types import SimpleNamespace
  from unittest.mock import AsyncMock

  from quantx_worker.prefector.flows import development_data_export_flow as flow

  request = HistoryPartitionRequest(
    instrument="000001.SZ", period="tick", trading_date=date(2026, 8, 3)
  )
  row = dict(id="export", request=request.model_dump(mode="json"),
             state="QUEUED", source_request_id="old")

  class Connection:
    async def scalar(self, *args):
      return True

    async def execute(self, statement, parameters=None):
      if parameters and "source" in parameters:
        row["source_request_id"] = parameters["source"]
      return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: [dict(row)]))

  @asynccontextmanager
  async def connect():
    yield Connection()

  async def source(identity):
    return dict(status="COMPLETED", development_only=identity != "old",
                ingestion_result={"day_coverage": []})

  store = SimpleNamespace(
    engine=SimpleNamespace(connect=connect, begin=connect),
    market_data_request=source,
    create_market_data_request=AsyncMock(return_value="replacement"),
    close=AsyncMock(),
  )
  monkeypatch.setenv("ENV", "production")
  monkeypatch.setattr(flow, "DurableRuntimeStore", lambda: store)
  monkeypatch.setattr(flow, "cleanup_expired", AsyncMock())
  monkeypatch.setattr(flow, "history_window_open", AsyncMock(return_value=True))
  monkeypatch.setattr(flow, "load_uploaded_request_manifest", AsyncMock(return_value=({}, {}, [])))
  failed = AsyncMock()
  monkeypatch.setattr(flow, "set_failed", failed)
  await flow.dispatch_once()
  row["state"] = "WAITING_SOURCE"
  await flow.dispatch_once()
  store.create_market_data_request.assert_awaited_once_with(
    request.agent_payload(), idempotency_scope="development-retry:old", development_only=True
  )
  failed.assert_awaited_once_with(store, "export", "SOURCE_COVERAGE_MISSING")


@pytest.mark.parametrize("limits", [(11.0, 9.0), (None, None)])
async def test_persisted_daily_limits_round_trip(monkeypatch, tmp_path, limits):
  from types import SimpleNamespace

  import pandas as pd
  from quantx_infrastructure.services import data_exchange_archive as archive
  from quantx_infrastructure.services.market_data_transfer_ingestion import (
    preprocess_market_data,
  )

  request = HistoryPartitionRequest(
    instrument="600000.SH", period="1d", trading_date=date(2026, 9, 7)
  )
  stamp = datetime(2026, 9, 7, tzinfo=ZoneInfo("Asia/Shanghai"))
  stored = SimpleNamespace(
    time=stamp,
    open=10.0,
    high=10.2,
    low=9.9,
    close=10.1,
    pre_close=9.95,
    volume=1000.0,
    amount=10000.0,
    settelement_price=0.0,
    open_interest=0,
    suspend_flag=0,
    up_stop_price=limits[0],
    down_stop_price=limits[1],
  )

  class History:
    async def get_kline_data(self, **kwargs):
      return [stored]

  monkeypatch.setattr(archive, "HistoricalMarketDataService", History)
  proof = {
    "day_coverage": [
      {
        "instrument_code": request.instrument,
        "period": "1d",
        "trading_date": "2026-09-07",
        "point_count": 1,
      }
    ]
  }
  rows = await archive.persisted_partition(request, proof)
  records = partition_records([rows], request)
  validate_bar_records_against_request(records, request.agent_payload())
  frame = preprocess_market_data("1d", {request.instrument: pd.DataFrame(rows)})
  if limits[0] is None:
    assert "upperLimit" not in rows[0] and "up_stop_price" not in frame
  else:
    assert frame.iloc[0]["up_stop_price"] == limits[0]
    assert frame.iloc[0]["down_stop_price"] == limits[1]
