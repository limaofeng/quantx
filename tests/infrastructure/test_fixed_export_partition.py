"""Fixed-source reconstruction rejects late versions and changed content."""
# ruff: noqa: F811

from datetime import date

import pytest
from quantx_contracts.data_exchange import HistoryPartitionRequest
from quantx_infrastructure.services.data_exchange_archive import persisted_partition
from quantx_infrastructure.services.market_data_transfer_ingestion import (
  ingest_uploaded_bar_request,
)

from tests.infrastructure.test_market_data_transfer_ingestion import (
  _kline_row,
  _payload,
  _summary,
  _tick_row,
)
from tests.infrastructure.test_native_bar_ingestion import Source, native  # noqa: F401


@pytest.mark.parametrize("period", ["tick", "1m", "1d"])
async def test_rebuild_reads_original_version_and_checks_partition_digest(
  native, period
):
  root, connection = native
  payload = _payload(periods=[period])
  row = _tick_row() if period == "tick" else _kline_row(period=period)
  field = "lastPrice" if period == "tick" else "close"
  original = Source(root, payload, [row, _summary([row], period=period)])
  audit = await ingest_uploaded_bar_request(original, original.identity)
  changed = {**row, field: row[field] + 0.1}
  newer = Source(root, payload, [changed, _summary([changed], period=period)])
  await ingest_uploaded_bar_request(newer, newer.identity)
  request = HistoryPartitionRequest(
    instrument=row["code"], period=period, trading_date=date(2023, 11, 15)
  )
  result = await persisted_partition(request, audit, source_payload=payload)
  assert result[0][field] == row[field]
  if period == "tick":
    assert result[0]["time"] == row["time"]
    assert result[0]["tick_ordinal"] == row["tick_ordinal"]
    assert result[0]["askPrice"] == row["askPrice"]
  for _, stored in connection.points.values():
    if stored["storage_version"] == audit["native_storage_version"]:
      stored["last_price" if period == "tick" else "close"] += 0.2
  with pytest.raises(ValueError, match="PERSISTED_COVERAGE_CHANGED"):
    await persisted_partition(request, audit, source_payload=payload)


async def test_legacy_receipt_requires_migration_before_any_query(native):
  _, connection = native
  request = HistoryPartitionRequest(
    instrument="000001.SZ", period="1m", trading_date=date(2023, 11, 15)
  )
  with pytest.raises(ValueError, match="MIGRATION_REQUIRED"):
    await persisted_partition(request, {}, source_payload=request.agent_payload())
  assert not connection.queries
