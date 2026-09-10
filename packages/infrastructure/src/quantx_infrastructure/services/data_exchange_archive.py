"""Rebuild an expired export from verified persisted market rows."""

import hashlib
import re
import time

from quantx_contracts.data_exchange import HistoryPartitionRequest
from quantx_contracts.market_data_service import HistoryRead

from .local_history_reader import LocalHistoryReader
from .market_data_content_verification import _canonical, _compare
from .market_data_staging_cleanup import _joined_thread
from .native_bar_publication import validate_native_bar_receipt

TICK_FIELDS = {
  "lastPrice": "last_price",
  "open": "open",
  "high": "high",
  "low": "low",
  "lastClose": "last_close",
  "amount": "amount",
  "volume": "volume",
  "pvolume": "pvolume",
  "tickvol": "tickvol",
  "stockStatus": "stock_status",
  "openInt": "open_int",
  "lastSettlementPrice": "last_settlement_price",
  "settlementPrice": "settlement_price",
  "transactionNum": "transaction_num",
  "askPrice": "ask_price",
  "bidPrice": "bid_price",
  "askVol": "ask_vol",
  "bidVol": "bid_vol",
  "priceTick": "price_tick",
  "tick_ordinal": "tick_ordinal",
}
KLINE_FIELDS = {
  "open": "open",
  "high": "high",
  "low": "low",
  "close": "close",
  "preClose": "pre_close",
  "volume": "volume",
  "amount": "amount",
  "settlementPrice": "settelement_price",
  "openInterest": "open_interest",
  "suspendFlag": "suspend_flag",
}
BOOK_FIELDS = {
  "askPrice": "ask",
  "bidPrice": "bid",
  "askVol": "ask_vol",
  "bidVol": "bid_vol",
}


async def persisted_partition(
  request: HistoryPartitionRequest, source_audit: dict, *, source_payload: dict
) -> list[dict]:
  version = validate_native_bar_receipt(source_payload, source_audit)
  day = request.trading_date.strftime("%Y%m%d")
  if (
    request.instrument not in source_payload.get("stock_list", [])
    or request.period not in source_payload.get("periods", [])
    or not source_payload["start_time"] <= day <= source_payload["end_time"]
  ):
    raise ValueError("PERSISTED_COVERAGE_UNPROVEN")
  coverage = [
    item
    for item in source_audit.get("day_coverage", [])
    if item["instrument_code"] == request.instrument
    and item["period"] == request.period
    and item["trading_date"] == request.trading_date.isoformat()
  ]
  if (
    len(coverage) != 1
    or type(coverage[0]["point_count"]) is not int
    or not 0 < coverage[0]["point_count"] <= 500000
  ):
    raise ValueError("PERSISTED_COVERAGE_UNPROVEN")
  content_hash = coverage[0].get("content_sha256")
  if (
    not isinstance(content_hash, str)
    or re.fullmatch(r"[0-9a-f]{64}", content_hash) is None
  ):
    raise ValueError("NATIVE_PARTITION_PROOF_MIGRATION_REQUIRED")
  return await _joined_thread(
    _read_partition, request, version, coverage[0]["point_count"], content_hash
  )


def _read_partition(request, version, expected, expected_hash):
  deadline = time.monotonic() + 60
  reader = LocalHistoryReader()
  query = HistoryRead(
    instrument=request.instrument,
    period=request.period,
    trading_date=request.trading_date,
    page_size=2000,
  )
  records, bytes_seen = [], 0
  digest = hashlib.sha256()
  fields = TICK_FIELDS if request.period == "tick" else KLINE_FIELDS
  if request.period == "1d":
    fields = {**fields, "upperLimit": "up_stop_price", "lowerLimit": "down_stop_price"}
  for _ in range(251):
    budget = {"deadline": deadline, "bytes": 0}
    page = reader._read(query, storage_version=version, budget=budget)
    bytes_seen += budget["bytes"]
    if bytes_seen > 512 * 1024 * 1024:
      raise ValueError("EXPORT_TRANSFER_BUDGET_EXCEEDED")
    for row in page.records:
      canonical, _ = _compare(
        {key: value for key, value in row.items() if key != "storage_version"}, row
      )
      digest.update(_canonical(canonical) + b"\n")
      stamp = (
        row["source_time_ms"]
        if request.period == "tick"
        else int(row["time"].timestamp() * 1000)
      )
      record = {"code": request.instrument, "period": request.period, "time": stamp}
      for wire, field in fields.items():
        value = row.get(field)
        if wire in {"upperLimit", "lowerLimit"} and (value is None or value <= 0):
          continue
        if wire in BOOK_FIELDS:
          value = [
            row[f"{BOOK_FIELDS[wire]}{level}"]
            for level in range(1, 6)
            if row.get(f"{BOOK_FIELDS[wire]}{level}") is not None
          ]
        record[wire] = value
      records.append(record)
      if len(records) > expected:
        raise ValueError("PERSISTED_COVERAGE_CHANGED")
    if page.exhausted:
      if len(records) != expected or digest.hexdigest() != expected_hash:
        raise ValueError("PERSISTED_COVERAGE_CHANGED")
      return records
    query = query.model_copy(update={"after": page.next_after})
  raise ValueError("EXPORT_RECORD_BUDGET_EXCEEDED")
