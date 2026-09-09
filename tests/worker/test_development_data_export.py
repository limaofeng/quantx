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


def test_digest_cannot_escape_export_directory():
  with pytest.raises(ValueError):
    content_path("../credentials")


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
