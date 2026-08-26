import gzip
import hashlib
import json
from unittest.mock import AsyncMock

import pytest
from quantx_contracts import (
  HISTORICAL_BAR_NO_DATA_REASON,
  HISTORICAL_BAR_SUMMARY_RECORD_TYPE,
  HISTORICAL_BAR_TRANSFER_SCHEMA_VERSION,
  HISTORICAL_TICK_ORDINAL_FIELD,
  HISTORICAL_TICK_ORDINALS_PER_MILLISECOND,
  HISTORICAL_TICK_SOURCE_TIME_FIELD,
  historical_bar_key,
)
from quantx_infrastructure.services import market_data_transfer_ingestion as ingestion
from quantx_worker.prefector.flows import durable_agent_flows


@pytest.fixture(autouse=True)
def _stub_persistence_readback(monkeypatch):
  async def verify(
    *,
    code_summaries,
    expected_key_batches,
    start_ms,
    end_exclusive_ms,
  ):
    assert start_ms < end_exclusive_ms
    batches = [batch async for batch in expected_key_batches]
    assert sum(len(batch.keys) for batch in batches) == sum(
      int(summary["row_count"]) for summary in code_summaries
    )
    actual = [
      {
        key: summary[key]
        for key in (
          "code",
          "period",
          "row_count",
          "min_time",
          "max_time",
          "key_sha256",
        )
      }
      for summary in code_summaries
    ]
    return {
      "status": "verified",
      "records_verified": sum(item["row_count"] for item in actual),
      "groups_verified": len(actual),
      "code_summaries": actual,
    }

  monkeypatch.setattr(ingestion, "verify_persisted_bar_summaries", verify)


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


def _bars_request(*, stock_list, periods, expected_chunks=1):
  return {
    "expected_chunks": expected_chunks,
    "request_payload": {
      "operation": "bars",
      "stock_list": stock_list,
      "periods": periods,
      "start_time": "20231115",
      "end_time": "20231115",
    },
  }


def _kline_record(
  *,
  code="600000.SH",
  period="1d",
  time=1_699_977_600_000,
  close=10.5,
):
  return {
    "code": code,
    "period": period,
    "time": time,
    "open": 10.0,
    "high": 11.0,
    "low": 9.0,
    "close": close,
    "preClose": 9.8,
    "volume": 100.0,
    "amount": 1050.0,
    "settelementPrice": 0.0,
    "openInterest": 0,
    "suspendFlag": 0,
  }


def _tick_record(*, time, ordinal=0, code="601318.SH", last_price=50.1):
  return {
    "code": code,
    "period": "tick",
    "time": time,
    HISTORICAL_TICK_ORDINAL_FIELD: ordinal,
    "lastPrice": last_price,
    "open": 49.8,
    "high": 50.2,
    "low": 49.7,
    "lastClose": 49.5,
    "amount": 1000.0,
    "volume": 100.0,
    "pvolume": 100.0,
    "tickvol": 1.0,
    "stockStatus": 0,
    "openInt": 0,
    "lastSettlementPrice": 0.0,
    "settlementPrice": 0.0,
    "transactionNum": 10,
    "askPrice": [50.2, 0, 0, 0, 0],
    "bidPrice": [50.0, 0, 0, 0, 0],
    "askVol": [100, 0, 0, 0, 0],
    "bidVol": [90, 0, 0, 0, 0],
  }


def _bar_summary(*, code, period, rows):
  keys = [
    historical_bar_key(
      code=code,
      period=period,
      time_ms=int(row["time"]),
      tick_ordinal=(
        int(row[HISTORICAL_TICK_ORDINAL_FIELD]) if period == "tick" else None
      ),
    )
    for row in rows
  ]
  return {
    "record_type": HISTORICAL_BAR_SUMMARY_RECORD_TYPE,
    "schema_version": HISTORICAL_BAR_TRANSFER_SCHEMA_VERSION,
    "code": code,
    "period": period,
    "row_count": len(rows),
    "min_time": int(rows[0]["time"]) if rows else None,
    "max_time": int(rows[-1]["time"]) if rows else None,
    "key_sha256": hashlib.sha256("\n".join(keys).encode()).hexdigest(),
    "no_data_reason": None if rows else HISTORICAL_BAR_NO_DATA_REASON,
  }


@pytest.mark.asyncio
async def test_uploaded_bars_are_validated_and_saved(tmp_path, monkeypatch):
  rows = [_kline_record()]
  records = [
    *rows,
    _bar_summary(code="600000.SH", period="1d", rows=rows),
  ]
  store = FakeStore(
    request=_bars_request(stock_list=["600000.SH"], periods=["1d"]),
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

  assert result["operation"] == "bars"
  assert result["records_received"] == 1
  assert result["records_saved"] == 1
  assert result["records_verified"] == 1
  assert result["persistence_verification"]["status"] == "verified"
  assert result["requested_codes"] == ["600000.SH"]
  assert result["requested_periods"] == ["1d"]
  assert result["code_summaries"][0]["row_count"] == 1
  assert calls[0][0] == "1d"
  assert calls[0][1]["600000.SH"].iloc[0]["close"] == 10.5


@pytest.mark.asyncio
async def test_missing_requested_code_summary_is_rejected_before_any_write(
  tmp_path,
  monkeypatch,
):
  rows = [_kline_record()]
  records = [
    *rows,
    _bar_summary(code="600000.SH", period="1d", rows=rows),
  ]
  store = FakeStore(
    request=_bars_request(
      stock_list=["600000.SH", "601318.SH"],
      periods=["1d"],
    ),
    manifest=[_transfer(tmp_path, records)],
  )
  save = AsyncMock()
  monkeypatch.setattr(durable_agent_flows, "save_market_data", save)

  with pytest.raises(RuntimeError, match="missing required summaries"):
    await durable_agent_flows._ingest_uploaded_request(store, "request-1")

  save.assert_not_awaited()


@pytest.mark.asyncio
async def test_explicit_empty_series_is_completed_and_auditable(
  tmp_path,
  monkeypatch,
):
  records = [_bar_summary(code="600000.SH", period="tick", rows=[])]
  store = FakeStore(
    request=_bars_request(stock_list=["600000.SH"], periods=["tick"]),
    manifest=[_transfer(tmp_path, records)],
  )
  save = AsyncMock()
  monkeypatch.setattr(durable_agent_flows, "save_market_data", save)

  result = await durable_agent_flows._ingest_uploaded_request(store, "request-1")

  assert result["records_received"] == 0
  assert result["records_saved"] == 0
  assert result["records_verified"] == 0
  assert result["empty_codes"] == ["600000.SH"]
  assert result["code_summaries"][0]["no_data_reason"] == (
    HISTORICAL_BAR_NO_DATA_REASON
  )
  save.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_millisecond_ticks_are_preserved_across_chunks(
  tmp_path,
  monkeypatch,
):
  source_time = 1_700_000_000_123
  rows = [
    _tick_record(time=source_time, ordinal=0, last_price=50.1),
    _tick_record(time=source_time, ordinal=1, last_price=50.2),
  ]
  chunks = [
    [rows[0]],
    [rows[1], _bar_summary(code="601318.SH", period="tick", rows=rows)],
  ]
  store = FakeStore(
    request=_bars_request(
      stock_list=["601318.SH"],
      periods=["tick"],
      expected_chunks=2,
    ),
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
  assert result["records_verified"] == 2
  assert captured["period"] == "tick"
  assert captured["frame"]["time"].tolist() == [source_time, source_time]
  assert captured["frame"][HISTORICAL_TICK_ORDINAL_FIELD].tolist() == [0, 1]


@pytest.mark.asyncio
async def test_duplicate_tick_composite_key_across_chunks_fails(tmp_path):
  source_time = 1_700_000_000_123
  duplicate = {
    **_tick_record(time=source_time, ordinal=0),
  }
  store = FakeStore(
    request=_bars_request(
      stock_list=["601318.SH"],
      periods=["tick"],
      expected_chunks=2,
    ),
    manifest=[
      _transfer(tmp_path, [duplicate], index=0),
      _transfer(tmp_path, [duplicate], index=1),
    ],
  )

  with pytest.raises(RuntimeError, match="unordered or duplicated|not contiguous"):
    await durable_agent_flows._ingest_uploaded_request(store, "request-1")


@pytest.mark.parametrize(
  ("ordinals", "match"),
  [
    pytest.param([None], "missing fields.*tick_ordinal", id="missing"),
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
    record = _tick_record(time=1_700_000_000_123)
    if ordinal is not None:
      record[HISTORICAL_TICK_ORDINAL_FIELD] = ordinal
    else:
      record.pop(HISTORICAL_TICK_ORDINAL_FIELD)
    records.append(record)
  store = FakeStore(
    request=_bars_request(stock_list=["601318.SH"], periods=["tick"]),
    manifest=[_transfer(tmp_path, records)],
  )

  with pytest.raises(RuntimeError, match=match):
    await durable_agent_flows._ingest_uploaded_request(store, "request-1")


@pytest.mark.asyncio
async def test_non_tick_duplicate_time_fails(tmp_path):
  record = _kline_record(period="1m", time=1_700_000_000_000)
  store = FakeStore(
    request=_bars_request(stock_list=["600000.SH"], periods=["1m"]),
    manifest=[_transfer(tmp_path, [record, record])],
  )

  with pytest.raises(RuntimeError, match="unordered or duplicated"):
    await durable_agent_flows._ingest_uploaded_request(store, "request-1")


@pytest.mark.parametrize(
  "reserved_field",
  [HISTORICAL_TICK_ORDINAL_FIELD, HISTORICAL_TICK_SOURCE_TIME_FIELD],
)
@pytest.mark.asyncio
async def test_non_tick_internal_tick_field_fails(tmp_path, reserved_field):
  record = {
    **_kline_record(),
    reserved_field: 0,
  }
  store = FakeStore(
    request=_bars_request(stock_list=["600000.SH"], periods=["1d"]),
    manifest=[_transfer(tmp_path, [record])],
  )

  with pytest.raises(RuntimeError, match="tick_ordinal|storage-only field"):
    await durable_agent_flows._ingest_uploaded_request(store, "request-1")


@pytest.mark.asyncio
async def test_uploaded_chunk_checksum_mismatch_fails(tmp_path):
  transfer = _transfer(tmp_path, [])
  transfer["checksum_sha256"] = "0" * 64
  store = FakeStore(
    request=_bars_request(stock_list=["600000.SH"], periods=["1d"]),
    manifest=[transfer],
  )

  with pytest.raises(RuntimeError, match="checksum mismatch"):
    await durable_agent_flows._ingest_uploaded_request(store, "request-1")


@pytest.mark.parametrize(
  ("record", "match"),
  [
    pytest.param(
      {"code": "000001.SZ", "period": "1d", "time": 1_700_000_000_000},
      "outside canonical transfer order",
      id="wrong-code",
    ),
    pytest.param(
      {"code": "600000.SH", "period": "1m", "time": 1_700_000_000_000},
      "outside canonical transfer order",
      id="wrong-period",
    ),
    pytest.param(
      {"code": "600000.SH", "period": "1d", "time": 1_699_000_000_000},
      "outside request window",
      id="out-of-window",
    ),
  ],
)
@pytest.mark.asyncio
async def test_uploaded_bars_outside_request_scope_are_not_saved(
  tmp_path,
  monkeypatch,
  record,
  match,
):
  store = FakeStore(
    request=_bars_request(stock_list=["600000.SH"], periods=["1d"]),
    manifest=[_transfer(tmp_path, [record])],
  )
  save = AsyncMock()
  monkeypatch.setattr(durable_agent_flows, "save_market_data", save)

  with pytest.raises(RuntimeError, match=match):
    await durable_agent_flows._ingest_uploaded_request(store, "request-1")

  save.assert_not_awaited()


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
