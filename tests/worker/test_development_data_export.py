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
