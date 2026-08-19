import gzip
import hashlib
import json

import pytest
from quantx_contracts import (
  HISTORICAL_TICK_ORDINAL_FIELD,
  HISTORICAL_TICK_ORDINALS_PER_MILLISECOND,
  HISTORICAL_TICK_SOURCE_TIME_FIELD,
)
from quantx_worker.prefector.flows import durable_agent_flows


class FakeStore:
  def __init__(self, *, request, manifest):
    self.request = request
    self.manifest = manifest

  async def market_data_request(self, request_id):
    assert request_id == "request-1"
    return self.request

  async def market_data_transfers(self, request_id):
    assert request_id == "request-1"
    return self.manifest


def _transfer(tmp_path, records, *, index=0):
  compressed = gzip.compress(json.dumps(records).encode("utf-8"))
  path = tmp_path / f"{index:08d}.json.gz"
  path.write_bytes(compressed)
  return {
    "chunk_index": index,
    "checksum_sha256": hashlib.sha256(compressed).hexdigest(),
    "record_count": len(records),
    "compressed": True,
    "storage_reference": str(path),
  }


@pytest.mark.asyncio
async def test_uploaded_bars_are_validated_and_saved(tmp_path, monkeypatch):
  records = [
    {
      "code": "600000.SH",
      "period": "1d",
      "time": 1_700_000_000_000,
      "open": 10,
      "high": 11,
      "low": 9,
      "close": 10.5,
      "volume": 100,
      "amount": 1050,
    }
  ]
  store = FakeStore(
    request={
      "expected_chunks": 1,
      "request_payload": {"operation": "bars"},
    },
    manifest=[_transfer(tmp_path, records)],
  )
  calls = []

  async def fake_save_market_data(*, period, market_data):
    calls.append((period, market_data))
    return {
      "saved_count": len(market_data["600000.SH"]),
      "status": "success",
    }

  monkeypatch.setattr(
    durable_agent_flows,
    "save_market_data",
    fake_save_market_data,
  )

  result = await durable_agent_flows._ingest_uploaded_request(
    store,
    "request-1",
  )

  assert result == {
    "operation": "bars",
    "records_received": 1,
    "records_saved": 1,
  }
  assert calls[0][0] == "1d"
  assert calls[0][1]["600000.SH"].iloc[0]["close"] == 10.5


@pytest.mark.asyncio
async def test_same_millisecond_ticks_are_preserved_across_chunks(
  tmp_path,
  monkeypatch,
):
  source_time = 1_700_000_000_123
  chunks = [
    [
      {
        "code": "601318.SH",
        "period": "tick",
        "time": source_time,
        HISTORICAL_TICK_ORDINAL_FIELD: 0,
        "lastPrice": 50.1,
      }
    ],
    [
      {
        "code": "601318.SH",
        "period": "tick",
        "time": source_time,
        HISTORICAL_TICK_ORDINAL_FIELD: 1,
        "lastPrice": 50.2,
      }
    ],
  ]
  store = FakeStore(
    request={
      "expected_chunks": 2,
      "request_payload": {"operation": "bars"},
    },
    manifest=[
      _transfer(tmp_path, chunk, index=index) for index, chunk in enumerate(chunks)
    ],
  )
  captured = {}

  async def fake_save_market_data(*, period, market_data):
    captured["period"] = period
    captured["frame"] = market_data["601318.SH"]
    return {"saved_count": len(captured["frame"]), "status": "success"}

  monkeypatch.setattr(
    durable_agent_flows,
    "save_market_data",
    fake_save_market_data,
  )

  result = await durable_agent_flows._ingest_uploaded_request(store, "request-1")

  assert result["records_received"] == 2
  assert result["records_saved"] == 2
  assert captured["period"] == "tick"
  assert captured["frame"]["time"].tolist() == [source_time, source_time]
  assert captured["frame"][HISTORICAL_TICK_ORDINAL_FIELD].tolist() == [0, 1]


@pytest.mark.asyncio
async def test_duplicate_tick_composite_key_across_chunks_fails(tmp_path):
  source_time = 1_700_000_000_123
  duplicate = {
    "code": "601318.SH",
    "period": "tick",
    "time": source_time,
    HISTORICAL_TICK_ORDINAL_FIELD: 0,
  }
  store = FakeStore(
    request={
      "expected_chunks": 2,
      "request_payload": {"operation": "bars"},
    },
    manifest=[
      _transfer(tmp_path, [duplicate], index=0),
      _transfer(tmp_path, [duplicate], index=1),
    ],
  )

  with pytest.raises(RuntimeError, match="duplicate historical tick key"):
    await durable_agent_flows._ingest_uploaded_request(store, "request-1")


@pytest.mark.parametrize(
  ("ordinals", "match"),
  [
    pytest.param([None], "must be an integer", id="missing"),
    pytest.param([-1], "out of range", id="negative"),
    pytest.param([True], "must be an integer", id="boolean"),
    pytest.param([0.5], "must be an integer", id="fractional"),
    pytest.param(
      [HISTORICAL_TICK_ORDINALS_PER_MILLISECOND],
      "out of range",
      id="upper-bound",
    ),
    pytest.param([0, 2], "not contiguous", id="gap"),
  ],
)
@pytest.mark.asyncio
async def test_invalid_tick_ordinal_contract_fails(tmp_path, ordinals, match):
  records = []
  for ordinal in ordinals:
    record = {
      "code": "601318.SH",
      "period": "tick",
      "time": 1_700_000_000_123,
    }
    if ordinal is not None:
      record[HISTORICAL_TICK_ORDINAL_FIELD] = ordinal
    records.append(record)
  store = FakeStore(
    request={
      "expected_chunks": 1,
      "request_payload": {"operation": "bars"},
    },
    manifest=[_transfer(tmp_path, records)],
  )

  with pytest.raises(RuntimeError, match=match):
    await durable_agent_flows._ingest_uploaded_request(store, "request-1")


@pytest.mark.asyncio
async def test_non_tick_duplicate_time_fails(tmp_path):
  record = {
    "code": "600000.SH",
    "period": "1m",
    "time": 1_700_000_000_000,
  }
  store = FakeStore(
    request={
      "expected_chunks": 1,
      "request_payload": {"operation": "bars"},
    },
    manifest=[_transfer(tmp_path, [record, record])],
  )

  with pytest.raises(RuntimeError, match="duplicate historical bar key"):
    await durable_agent_flows._ingest_uploaded_request(store, "request-1")


@pytest.mark.parametrize(
  "reserved_field",
  [HISTORICAL_TICK_ORDINAL_FIELD, HISTORICAL_TICK_SOURCE_TIME_FIELD],
)
@pytest.mark.asyncio
async def test_non_tick_internal_tick_field_fails(tmp_path, reserved_field):
  record = {
    "code": "600000.SH",
    "period": "1d",
    "time": 1_700_000_000_000,
    reserved_field: 0,
  }
  store = FakeStore(
    request={
      "expected_chunks": 1,
      "request_payload": {"operation": "bars"},
    },
    manifest=[_transfer(tmp_path, [record])],
  )

  with pytest.raises(RuntimeError, match="tick_ordinal|storage-only field"):
    await durable_agent_flows._ingest_uploaded_request(store, "request-1")


@pytest.mark.asyncio
async def test_uploaded_chunk_checksum_mismatch_fails(tmp_path):
  transfer = _transfer(tmp_path, [])
  transfer["checksum_sha256"] = "0" * 64
  store = FakeStore(
    request={
      "expected_chunks": 1,
      "request_payload": {"operation": "bars"},
    },
    manifest=[transfer],
  )

  with pytest.raises(RuntimeError, match="checksum mismatch"):
    await durable_agent_flows._ingest_uploaded_request(store, "request-1")


@pytest.mark.asyncio
async def test_uploaded_financial_rows_are_validated_saved_and_rebuilt(
  tmp_path,
  monkeypatch,
):
  records = [
    {
      "record_type": "financial_row",
      "schema_version": 1,
      "code": "688552.SH",
      "table": "Income",
      "row": {
        "m_timetag": "20260331",
        "m_anntime": "20260422",
        "revenue": 100,
      },
    },
    {
      "record_type": "financial_summary",
      "schema_version": 1,
      "code": "688552.SH",
      "table_counts": {
        "Balance": 0,
        "Income": 1,
        "CashFlow": 0,
        "Capital": 0,
      },
    },
  ]
  store = FakeStore(
    request={
      "expected_chunks": 1,
      "request_payload": {
        "operation": "financial_data",
        "record_format": "financial-row-v1",
        "stock_list": ["688552.SH"],
        "table_list": ["Balance", "Income", "CashFlow", "Capital"],
        "start_time": "20220101",
        "end_time": "20260810",
      },
    },
    manifest=[_transfer(tmp_path, records)],
  )
  captured = {}
  parse_report_date = durable_agent_flows.FinancialService._parse_report_date

  class FakeFinancialService:
    _parse_report_date = staticmethod(parse_report_date)

    async def save_batch_financial_data_with_audit(self, frames):
      captured.update(frames)
      return {
        "rows_received": 1,
        "rows_upserted": 1,
        "rows_rejected": 0,
        "metric_codes_rebuilt": 1,
        "metric_rows_rebuilt": 6,
      }

  monkeypatch.setattr(
    durable_agent_flows,
    "FinancialService",
    FakeFinancialService,
  )

  result = await durable_agent_flows._ingest_uploaded_request(store, "request-1")

  assert captured["688552.SH"]["Income"].iloc[0]["m_timetag"] == "20260331"
  assert result["records_saved"] == 1
  assert result["replacement_audit"]["synced_codes"] == 1
  assert result["replacement_audit"]["metric_rows_rebuilt"] == 6


@pytest.mark.asyncio
async def test_uploaded_financial_rows_require_per_code_summary(tmp_path):
  records = [
    {
      "record_type": "financial_row",
      "schema_version": 1,
      "code": "688552.SH",
      "table": "Income",
      "row": {"m_timetag": "20260331", "m_anntime": "20260422"},
    }
  ]
  store = FakeStore(
    request={
      "expected_chunks": 1,
      "request_payload": {
        "operation": "financial_data",
        "stock_list": ["688552.SH"],
        "table_list": ["Balance", "Income", "CashFlow", "Capital"],
        "start_time": "20220101",
        "end_time": "20260810",
      },
    },
    manifest=[_transfer(tmp_path, records)],
  )

  with pytest.raises(RuntimeError, match="summaries missing"):
    await durable_agent_flows._ingest_uploaded_request(store, "request-1")
