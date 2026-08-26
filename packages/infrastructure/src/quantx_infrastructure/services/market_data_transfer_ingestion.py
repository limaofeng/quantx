"""Bounded, authoritative ingestion for durable QMT market-data transfers."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import io
import json
import logging
import math
import re
from collections.abc import AsyncIterable, Awaitable, Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import ValidationError
from quantx_contracts import (
  HISTORICAL_BAR_SUMMARY_RECORD_TYPE,
  HISTORICAL_KLINE_TRANSFER_REQUIRED_FIELDS,
  HISTORICAL_KLINE_TRANSFER_VARIANT_FIELDS,
  HISTORICAL_TICK_ORDINAL_FIELD,
  HISTORICAL_TICK_ORDINALS_PER_MILLISECOND,
  HISTORICAL_TICK_SOURCE_TIME_FIELD,
  HISTORICAL_TICK_TRANSFER_OPTIONAL_FIELDS,
  HISTORICAL_TICK_TRANSFER_REQUIRED_FIELDS,
  HistoricalBarSummary,
  historical_bar_key,
)

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.services.historical_market_data_service import (
  HistoricalMarketDataService,
)
from quantx_infrastructure.services.market_data_persistence_verification import (
  ExpectedBarKeyBatch,
  MarketDataPersistenceVerificationError,
  verify_persisted_bar_summaries,
)
from quantx_infrastructure.services.market_data_staging import (
  is_reparse_point,
  market_data_staging_root,
  safe_market_data_request_directory,
  safe_market_data_staging_file,
)

logger = logging.getLogger(__name__)

_MARKET_DATA_REQUEST_TIMEZONE = ZoneInfo("Asia/Shanghai")
MAX_TRANSFER_CHUNK_COMPRESSED_BYTES = 32 * 1024 * 1024
MAX_TRANSFER_CHUNK_UNCOMPRESSED_BYTES = 24 * 1024 * 1024
MAX_TRANSFER_RECORD_UNCOMPRESSED_BYTES = 1024 * 1024
MAX_TRANSFER_CHUNK_RECORDS = 5000
# Agent bounds allow at most 99 record-bound emissions, 22 byte-bound
# emissions, and one final chunk; round the proven 122 ceiling up slightly.
MAX_TRANSFER_REQUEST_CHUNKS = 128
MAX_TRANSFER_REQUEST_COMPRESSED_BYTES = 256 * 1024 * 1024
MAX_TRANSFER_REQUEST_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_TRANSFER_REQUEST_RECORDS = 500_000
MAX_TRANSFER_REQUEST_CODES = 300
MARKET_DATA_WRITE_BATCH_RECORDS = 2000
MARKET_DATA_WRITE_BATCH_BYTES = 8 * 1024 * 1024
MARKET_DATA_CLAIM_RENEW_SECONDS = 60.0

_INSTRUMENT_CODE_PATTERN = re.compile(r"^[A-Z0-9]{1,16}\.(?:SH|SZ|BJ)$")
_SIGNED_INT64_MIN = -(2**63)
_SIGNED_INT64_MAX = 2**63 - 1

_TICK_REQUIRED_FIELDS = frozenset(HISTORICAL_TICK_TRANSFER_REQUIRED_FIELDS)
_TICK_OPTIONAL_FIELDS = frozenset(HISTORICAL_TICK_TRANSFER_OPTIONAL_FIELDS)
_KLINE_REQUIRED_FIELDS = frozenset(HISTORICAL_KLINE_TRANSFER_REQUIRED_FIELDS)
_KLINE_VARIANT_FIELDS = frozenset(HISTORICAL_KLINE_TRANSFER_VARIANT_FIELDS)


class MarketDataValidationError(RuntimeError):
  """The immutable request or transfer cannot ever pass validation."""


class MarketDataTransferStore(Protocol):
  async def market_data_request(self, request_id: str) -> dict[str, Any] | None: ...

  async def market_data_transfers(
    self,
    request_id: str,
  ) -> list[dict[str, Any]]: ...

  async def claim_market_data_request(self, request_id: str) -> str | None: ...

  async def renew_market_data_request_claim(
    self,
    request_id: str,
    *,
    claim_token: str,
  ) -> bool: ...

  async def release_market_data_request_claim(
    self,
    request_id: str,
    *,
    claim_token: str,
    error: str,
  ) -> bool: ...

  async def finish_market_data_request(
    self,
    request_id: str,
    *,
    status: str,
    error: str = "",
    ingestion_result: dict[str, Any] | None = None,
    claim_token: str | None = None,
  ) -> None: ...


SaveMarketData = Callable[..., Awaitable[dict[str, Any]]]
VerifyPersistence = Callable[..., Awaitable[dict[str, Any]]]
IngestRequest = Callable[
  [MarketDataTransferStore, str],
  Awaitable[dict[str, Any]],
]


@dataclass(frozen=True)
class _BarsRequestScope:
  codes: tuple[str, ...]
  periods: tuple[str, ...]
  groups: tuple[tuple[str, str], ...]
  start_text: str
  end_text: str
  start_date: date
  end_date: date
  start_ms: int
  daily_end_ms: int
  end_exclusive_ms: int


@dataclass
class _TransferBudget:
  compressed_bytes: int = 0
  uncompressed_bytes: int = 0
  records: int = 0


def _validation_error(message: str) -> MarketDataValidationError:
  return MarketDataValidationError(message)


def _parse_bars_request(payload: dict[str, Any]) -> _BarsRequestScope:
  if str(payload.get("operation") or "bars") != "bars":
    raise _validation_error("market-data transfer is not a bars request")
  raw_codes = payload.get("stock_list")
  if not isinstance(raw_codes, list) or not raw_codes:
    raise _validation_error("bars request requires a non-empty stock_list")
  if any(not isinstance(code, str) or not code.strip() for code in raw_codes):
    raise _validation_error("bars request stock_list contains an invalid code")
  normalized_codes = tuple(code.strip().upper() for code in raw_codes)
  if any(raw != normalized for raw, normalized in zip(raw_codes, normalized_codes)):
    raise _validation_error("bars request stock_list must be canonical")
  if len(set(normalized_codes)) != len(normalized_codes):
    raise _validation_error("bars request stock_list contains duplicate codes")
  if len(normalized_codes) > MAX_TRANSFER_REQUEST_CODES:
    raise _validation_error(
      f"bars request accepts at most {MAX_TRANSFER_REQUEST_CODES} instruments"
    )
  invalid_codes = [
    code for code in normalized_codes if not _INSTRUMENT_CODE_PATTERN.fullmatch(code)
  ]
  if invalid_codes:
    raise _validation_error(
      f"bars request contains invalid instruments: {invalid_codes}"
    )

  raw_periods = payload.get("periods") or ["1d"]
  if not isinstance(raw_periods, list) or not raw_periods:
    raise _validation_error("bars request requires a non-empty periods list")
  if any(not isinstance(period, str) or not period.strip() for period in raw_periods):
    raise _validation_error("bars request periods contains an invalid value")
  normalized_periods = tuple(period.strip().lower() for period in raw_periods)
  if any(raw != normalized for raw, normalized in zip(raw_periods, normalized_periods)):
    raise _validation_error("bars request periods must be canonical")
  if len(set(normalized_periods)) != len(normalized_periods):
    raise _validation_error("bars request periods contains duplicate values")
  unsupported = set(normalized_periods) - {"tick", "1m", "1d"}
  if unsupported:
    raise _validation_error(
      f"bars request contains unsupported periods: {sorted(unsupported)}"
    )

  start_text = str(payload.get("start_time") or "").strip()
  end_text = str(payload.get("end_time") or "").strip()
  try:
    start_local = datetime.strptime(start_text, "%Y%m%d").replace(
      tzinfo=_MARKET_DATA_REQUEST_TIMEZONE
    )
    end_local = datetime.strptime(end_text, "%Y%m%d").replace(
      tzinfo=_MARKET_DATA_REQUEST_TIMEZONE
    )
  except ValueError as exc:
    raise _validation_error("bars request dates must be YYYYMMDD") from exc
  if end_local < start_local:
    raise _validation_error("bars request end_time precedes start_time")
  groups = tuple(
    (period, code)
    for period in normalized_periods
    for code in sorted(normalized_codes)
  )
  return _BarsRequestScope(
    codes=tuple(sorted(normalized_codes)),
    periods=normalized_periods,
    groups=groups,
    start_text=start_text,
    end_text=end_text,
    start_date=start_local.date(),
    end_date=end_local.date(),
    start_ms=int(start_local.timestamp() * 1000),
    daily_end_ms=int(end_local.timestamp() * 1000),
    end_exclusive_ms=int((end_local + timedelta(days=1)).timestamp() * 1000),
  )


def _finite_number(value: Any, *, field: str) -> None:
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    raise _validation_error(f"bar record {field} must be numeric")
  try:
    finite = math.isfinite(float(value))
  except (OverflowError, TypeError, ValueError) as exc:
    raise _validation_error(f"bar record {field} must be finite") from exc
  if not finite:
    raise _validation_error(f"bar record {field} must be finite")


def _signed_int64(value: Any, *, field: str) -> None:
  if isinstance(value, bool) or not isinstance(value, int):
    raise _validation_error(f"bar record {field} must be an integer")
  if not _SIGNED_INT64_MIN <= value <= _SIGNED_INT64_MAX:
    raise _validation_error(f"bar record {field} is outside signed int64 range")


def _validate_bar_schema(record: dict[str, Any], *, period: str) -> None:
  fields = frozenset(record)
  if period == "tick":
    missing = _TICK_REQUIRED_FIELDS - fields
    extra = fields - _TICK_REQUIRED_FIELDS - _TICK_OPTIONAL_FIELDS
    if missing:
      raise _validation_error(f"tick record is missing fields: {sorted(missing)}")
    if extra:
      raise _validation_error(f"tick record contains unsupported fields: {sorted(extra)}")
    for field in (
      "lastPrice",
      "open",
      "high",
      "low",
      "lastClose",
      "amount",
      "volume",
      "pvolume",
      "tickvol",
      "lastSettlementPrice",
      "settlementPrice",
      "priceTick",
      "upperLimit",
      "lowerLimit",
    ):
      if field in record:
        _finite_number(record[field], field=field)
    for field in ("stockStatus", "openInt", "transactionNum"):
      _signed_int64(record[field], field=field)
    for field in ("askPrice", "bidPrice", "askVol", "bidVol"):
      values = record[field]
      if not isinstance(values, list) or len(values) > 5:
        raise _validation_error(f"tick record {field} must be a list of at most 5")
      for value in values:
        _finite_number(value, field=field)
    return

  missing = _KLINE_REQUIRED_FIELDS - fields
  extra = fields - _KLINE_REQUIRED_FIELDS - _KLINE_VARIANT_FIELDS
  if missing:
    raise _validation_error(f"kline record is missing fields: {sorted(missing)}")
  if extra:
    raise _validation_error(f"kline record contains unsupported fields: {sorted(extra)}")
  settlement_fields = {"settelementPrice", "settlementPrice"} & fields
  if not settlement_fields:
    raise _validation_error("kline record is missing settlement price")
  if len(settlement_fields) > 1:
    raise _validation_error("kline record contains conflicting settlement price fields")
  open_interest_fields = {"openInterest", "openInt"} & fields
  if not open_interest_fields:
    raise _validation_error("kline record is missing open interest")
  if len(open_interest_fields) > 1:
    raise _validation_error("kline record contains conflicting open interest fields")
  for field in fields - {"code", "period", "time"}:
    _finite_number(record[field], field=field)


class _BarTransferValidator:
  def __init__(self, scope: _BarsRequestScope) -> None:
    self.scope = scope
    self.group_index = 0
    self.object_count = 0
    self.row_count = 0
    self.group_row_count = 0
    self.min_time: int | None = None
    self.max_time: int | None = None
    self.last_time: int | None = None
    self.last_ordinal: int | None = None
    self.key_digest = hashlib.sha256()
    self.summaries: list[dict[str, Any]] = []

  def _group(self) -> tuple[str, str]:
    if self.group_index >= len(self.scope.groups):
      raise _validation_error("bars transfer contains records after final summary")
    return self.scope.groups[self.group_index]

  def consume(self, record: dict[str, Any]) -> None:
    self.object_count += 1
    if self.object_count > MAX_TRANSFER_REQUEST_RECORDS:
      raise _validation_error("market-data request exceeds record count limit")
    if "record_type" in record:
      self._consume_summary(record)
    else:
      self._consume_row(record)

  def _consume_row(self, record: dict[str, Any]) -> None:
    expected_period, expected_code = self._group()
    raw_code = record.get("code")
    raw_period = record.get("period")
    if raw_code != expected_code or raw_period != expected_period:
      raise _validation_error(
        "bar record is outside canonical transfer order: "
        f"expected={expected_code}/{expected_period} "
        f"actual={raw_code}/{raw_period}"
      )
    source_time = record.get("time")
    if isinstance(source_time, bool) or not isinstance(source_time, int):
      raise _validation_error("bar record time must be integer milliseconds")
    upper_bound = (
      self.scope.daily_end_ms
      if expected_period == "1d"
      else self.scope.end_exclusive_ms - 1
    )
    if not self.scope.start_ms <= source_time <= upper_bound:
      raise _validation_error(
        "bar record time is outside request window: "
        f"{expected_code}/{expected_period}/{source_time}"
      )
    if HISTORICAL_TICK_SOURCE_TIME_FIELD in record:
      raise _validation_error(
        f"bar record contains storage-only field {HISTORICAL_TICK_SOURCE_TIME_FIELD}"
      )
    _validate_bar_schema(record, period=expected_period)

    ordinal: int | None = None
    if expected_period == "tick":
      ordinal = record.get(HISTORICAL_TICK_ORDINAL_FIELD)
      if isinstance(ordinal, bool) or not isinstance(ordinal, int):
        raise _validation_error("tick tick_ordinal must be an integer")
      if not 0 <= ordinal < HISTORICAL_TICK_ORDINALS_PER_MILLISECOND:
        raise _validation_error("tick tick_ordinal is out of range")
      if self.last_time is None or source_time > self.last_time:
        if ordinal != 0:
          raise _validation_error("historical tick ordinals are not contiguous")
      elif source_time == self.last_time:
        if ordinal != int(self.last_ordinal or 0) + 1:
          raise _validation_error("historical tick ordinals are not contiguous")
      else:
        raise _validation_error("historical tick keys are unordered or duplicated")
    else:
      if HISTORICAL_TICK_ORDINAL_FIELD in record:
        raise _validation_error("non-tick bar contains tick_ordinal")
      if self.last_time is not None and source_time <= self.last_time:
        raise _validation_error("historical bar keys are unordered or duplicated")

    key = historical_bar_key(
      code=expected_code,
      period=expected_period,
      time_ms=source_time,
      tick_ordinal=ordinal,
    )
    if self.group_row_count:
      self.key_digest.update(b"\n")
    self.key_digest.update(key.encode("utf-8"))
    self.group_row_count += 1
    self.row_count += 1
    self.min_time = source_time if self.min_time is None else self.min_time
    self.max_time = source_time
    self.last_time = source_time
    self.last_ordinal = ordinal

  def _consume_summary(self, record: dict[str, Any]) -> None:
    if record.get("record_type") != HISTORICAL_BAR_SUMMARY_RECORD_TYPE:
      raise _validation_error("bars transfer contains an unknown record_type")
    try:
      summary = HistoricalBarSummary.model_validate(record)
    except ValidationError as exc:
      raise _validation_error(f"invalid historical bar summary: {exc}") from exc
    expected_period, expected_code = self._group()
    if (summary.period, summary.code) != (expected_period, expected_code):
      raise _validation_error(
        "historical bar summary is outside canonical transfer order: "
        f"expected={expected_code}/{expected_period} "
        f"actual={summary.code}/{summary.period}"
      )
    actual = {
      "row_count": self.group_row_count,
      "min_time": self.min_time,
      "max_time": self.max_time,
      "key_sha256": self.key_digest.hexdigest(),
    }
    declared = {
      "row_count": summary.row_count,
      "min_time": summary.min_time,
      "max_time": summary.max_time,
      "key_sha256": summary.key_sha256,
    }
    if declared != actual:
      raise _validation_error(
        "historical bar summary does not match its rows: "
        f"{summary.code}/{summary.period}"
      )
    self.summaries.append(summary.model_dump(mode="json"))
    self.group_index += 1
    self.group_row_count = 0
    self.min_time = None
    self.max_time = None
    self.last_time = None
    self.last_ordinal = None
    self.key_digest = hashlib.sha256()

  def finish(self) -> dict[str, Any]:
    if self.group_index != len(self.scope.groups):
      missing = self.scope.groups[self.group_index :]
      raise _validation_error(
        f"bars transfer is missing required summaries: {list(missing)}"
      )
    empty_codes = sorted(
      {
        str(item["code"])
        for item in self.summaries
        if int(item["row_count"]) == 0
      }
    )
    return {
      "operation": "bars",
      "records_received": self.row_count,
      "code_summaries": self.summaries,
      "empty_codes": empty_codes,
      "requested_codes": list(self.scope.codes),
      "requested_periods": list(self.scope.periods),
      "start_time": self.scope.start_text,
      "end_time": self.scope.end_text,
    }


def validate_bar_record_keys(records: list[dict[str, Any]]) -> None:
  """Validate one non-empty authoritative record stream including summaries."""

  codes = sorted({str(record.get("code")) for record in records if record.get("code")})
  periods: list[str] = []
  for record in records:
    period = str(record.get("period") or "")
    if period and period not in periods:
      periods.append(period)
  times = [int(record["time"]) for record in records if "time" in record]
  if not codes or not periods or not times:
    raise _validation_error("standalone bar validation requires non-empty rows")
  start = datetime.fromtimestamp(
    min(times) / 1000,
    _MARKET_DATA_REQUEST_TIMEZONE,
  ).strftime("%Y%m%d")
  end = datetime.fromtimestamp(
    max(times) / 1000,
    _MARKET_DATA_REQUEST_TIMEZONE,
  ).strftime("%Y%m%d")
  validate_bar_records_against_request(
    records,
    {
      "operation": "bars",
      "stock_list": codes,
      "periods": periods,
      "start_time": start,
      "end_time": end,
    },
  )


def validate_bar_records_against_request(
  records: list[dict[str, Any]],
  payload: dict[str, Any],
) -> None:
  validator = _BarTransferValidator(_parse_bars_request(payload))
  for record in records:
    validator.consume(record)
  validator.finish()


def preprocess_market_data(
  period: str,
  market_data: dict[str, pd.DataFrame],
) -> pd.DataFrame:
  """Normalize one bounded Agent batch into the fixed Influx schema."""

  frames = [
    frame.assign(stock_code=stock_code) for stock_code, frame in market_data.items()
  ]
  if not frames:
    return pd.DataFrame()
  values = pd.concat(frames, ignore_index=True)
  values["period"] = period
  if period == "tick":
    source_time = values["time"]
    ordinal = values[HISTORICAL_TICK_ORDINAL_FIELD]
    if not pd.api.types.is_integer_dtype(source_time.dtype):
      raise MarketDataValidationError("tick time must contain integer milliseconds")
    if not pd.api.types.is_integer_dtype(ordinal.dtype):
      raise MarketDataValidationError("tick tick_ordinal must contain integers")
    source_time = source_time.astype("int64")
    ordinal = ordinal.astype("int64")
    values[HISTORICAL_TICK_SOURCE_TIME_FIELD] = source_time
    storage_time = pd.to_datetime(source_time, unit="ms", utc=True).astype(
      "datetime64[ns, UTC]"
    )
    values["time"] = (
      storage_time + pd.to_timedelta(ordinal, unit="us")
    ).dt.tz_convert("Asia/Shanghai")
    values.rename(
      columns={
        "lastPrice": "last_price",
        "lastClose": "last_close",
        "settlementPrice": "settlement_price",
        "lastSettlementPrice": "last_settlement_price",
        "stockStatus": "stock_status",
        "openInt": "open_int",
        "transactionNum": "transaction_num",
        "askPrice": "ask_price",
        "bidPrice": "bid_price",
        "askVol": "ask_vol",
        "bidVol": "bid_vol",
        "priceTick": "price_tick",
        "upperLimit": "up_stop_price",
        "lowerLimit": "down_stop_price",
      },
      inplace=True,
    )
    for column, default in {
      "price_tick": 0.01,
      "up_stop_price": 0.0,
      "down_stop_price": 0.0,
    }.items():
      if column not in values:
        values[column] = default
    price_columns = [
      "last_price",
      "open",
      "high",
      "low",
      "last_close",
      "settlement_price",
      "last_settlement_price",
      "price_tick",
      "up_stop_price",
      "down_stop_price",
    ]
    values[price_columns] = values[price_columns].astype(float).round(3)
    values[["volume", "amount", "pvolume", "tickvol"]] = (
      values[["volume", "amount", "pvolume", "tickvol"]].astype(float).round(2)
    )
    values[["stock_status", "open_int", "transaction_num"]] = (
      values[["stock_status", "open_int", "transaction_num"]]
      .fillna(0)
      .astype(int)
    )
    return values[
      [
        "stock_code",
        "period",
        "time",
        "last_price",
        "open",
        "high",
        "low",
        "last_close",
        "amount",
        "volume",
        "pvolume",
        "tickvol",
        "stock_status",
        "open_int",
        "last_settlement_price",
        "settlement_price",
        "transaction_num",
        "price_tick",
        "up_stop_price",
        "down_stop_price",
        "ask_price",
        "bid_price",
        "ask_vol",
        "bid_vol",
        HISTORICAL_TICK_SOURCE_TIME_FIELD,
        HISTORICAL_TICK_ORDINAL_FIELD,
      ]
    ]

  values["time"] = pd.to_datetime(values["time"], unit="ms", utc=True).dt.tz_convert(
    "Asia/Shanghai"
  )
  values.rename(
    columns={
      "settelementPrice": "settelement_price",
      "settlementPrice": "settelement_price",
      "openInterest": "open_interest",
      "openInt": "open_interest",
      "preClose": "pre_close",
      "suspendFlag": "suspend_flag",
    },
    inplace=True,
  )
  columns = [
    "stock_code",
    "period",
    "time",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "volume",
    "amount",
    "settelement_price",
    "open_interest",
    "suspend_flag",
  ]
  values[["open", "high", "low", "close", "pre_close", "settelement_price"]] = (
    values[["open", "high", "low", "close", "pre_close", "settelement_price"]]
    .astype(float)
    .round(3)
  )
  values[["volume", "amount"]] = values[["volume", "amount"]].astype(float).round(2)
  values[["open_interest", "suspend_flag"]] = (
    values[["open_interest", "suspend_flag"]].fillna(0).astype(int)
  )
  return values[columns]


def _reject_json_constant(value: str) -> None:
  raise _validation_error(f"market-data JSON contains non-finite value: {value}")


def _encoded_record_size(record: dict[str, Any]) -> int:
  try:
    return len(
      json.dumps(
        record,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
      ).encode("utf-8")
    )
  except (RecursionError, TypeError, ValueError) as exc:
    raise _validation_error("market-data record is not canonical JSON") from exc


def _validate_transfer_item(item: dict[str, Any]) -> tuple[Path, str, int, bool]:
  if not isinstance(item, dict):
    raise _validation_error("market-data manifest contains a non-object item")
  storage_reference = item.get("storage_reference")
  if not isinstance(storage_reference, str) or not storage_reference.strip():
    raise _validation_error("market-data manifest has an invalid storage reference")
  checksum = item.get("checksum_sha256")
  if (
    not isinstance(checksum, str)
    or len(checksum) != 64
    or any(character not in "0123456789abcdef" for character in checksum)
  ):
    raise _validation_error("market-data manifest has an invalid SHA256 checksum")
  record_count = item.get("record_count")
  if (
    isinstance(record_count, bool)
    or not isinstance(record_count, int)
    or not 0 <= record_count <= MAX_TRANSFER_CHUNK_RECORDS
  ):
    raise _validation_error("market-data manifest has an invalid record count")
  compressed = item.get("compressed")
  if not isinstance(compressed, bool):
    raise _validation_error("market-data manifest has an invalid compressed flag")
  return Path(storage_reference), checksum, record_count, compressed


def _read_chunk_bytes(path: Path) -> tuple[bytes, str]:
  if path.is_symlink() or not path.is_file():
    raise _validation_error(f"market-data chunk is not a regular file: {path.name}")
  body = bytearray()
  digest = hashlib.sha256()
  with path.open("rb") as source:
    while True:
      block = source.read(1024 * 1024)
      if not block:
        break
      body.extend(block)
      digest.update(block)
      if len(body) > MAX_TRANSFER_CHUNK_COMPRESSED_BYTES:
        raise _validation_error(f"market-data chunk exceeds compressed limit: {path.name}")
  return bytes(body), digest.hexdigest()


def _read_transfer_chunk(
  item: dict[str, Any],
  budget: _TransferBudget,
) -> list[dict[str, Any]]:
  path, expected_digest, expected_records, compressed_flag = _validate_transfer_item(
    item
  )
  compressed, digest = _read_chunk_bytes(path)
  if digest != expected_digest:
    raise _validation_error(f"market-data chunk checksum mismatch: {path.name}")
  budget.compressed_bytes += len(compressed)
  if budget.compressed_bytes > MAX_TRANSFER_REQUEST_COMPRESSED_BYTES:
    raise _validation_error("market-data request exceeds compressed byte limit")
  if compressed_flag:
    try:
      with gzip.GzipFile(fileobj=io.BytesIO(compressed), mode="rb") as source:
        raw = source.read(MAX_TRANSFER_CHUNK_UNCOMPRESSED_BYTES + 1)
    except (OSError, EOFError) as exc:
      raise _validation_error(f"invalid gzip market-data chunk: {path.name}") from exc
  else:
    raw = compressed
  if len(raw) > MAX_TRANSFER_CHUNK_UNCOMPRESSED_BYTES:
    raise _validation_error(f"market-data chunk exceeds uncompressed limit: {path.name}")
  budget.uncompressed_bytes += len(raw)
  if budget.uncompressed_bytes > MAX_TRANSFER_REQUEST_UNCOMPRESSED_BYTES:
    raise _validation_error("market-data request exceeds uncompressed byte limit")
  try:
    chunk = json.loads(
      raw.decode("utf-8"),
      parse_constant=_reject_json_constant,
    )
  except (RecursionError, UnicodeDecodeError, json.JSONDecodeError) as exc:
    raise _validation_error(f"invalid market-data JSON chunk: {path.name}") from exc
  if not isinstance(chunk, list):
    raise _validation_error(f"market-data chunk is not an array: {path.name}")
  if len(chunk) > MAX_TRANSFER_CHUNK_RECORDS:
    raise _validation_error(f"market-data chunk exceeds record limit: {path.name}")
  if len(chunk) != expected_records:
    raise _validation_error(f"market-data chunk record count mismatch: {path.name}")
  if any(not isinstance(record, dict) for record in chunk):
    raise _validation_error(f"market-data chunk contains a non-object: {path.name}")
  for record in chunk:
    encoded_size = _encoded_record_size(record)
    if encoded_size > MAX_TRANSFER_RECORD_UNCOMPRESSED_BYTES:
      raise _validation_error(f"market-data record exceeds byte limit: {path.name}")
  budget.records += len(chunk)
  if budget.records > MAX_TRANSFER_REQUEST_RECORDS:
    raise _validation_error("market-data request exceeds record count limit")
  return chunk


def _iter_transfer_chunks(
  manifest: list[dict[str, Any]],
) -> Iterator[list[dict[str, Any]]]:
  budget = _TransferBudget()
  for item in manifest:
    yield _read_transfer_chunk(item, budget)


def _read_transfer_records(
  manifest: list[dict[str, Any]],
) -> list[dict[str, Any]]:
  records: list[dict[str, Any]] = []
  for chunk in _iter_transfer_chunks(manifest):
    records.extend(chunk)
  return records


async def load_uploaded_request_manifest(
  store: MarketDataTransferStore,
  request_id: str,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
  request = await store.market_data_request(request_id)
  if request is None:
    raise _validation_error("market-data request disappeared before ingestion")
  manifest = await store.market_data_transfers(request_id)
  raw_expected = request.get("expected_chunks")
  raw_received = request.get("received_chunks")
  if (
    isinstance(raw_expected, bool)
    or not isinstance(raw_expected, int)
    or not 1 <= raw_expected <= MAX_TRANSFER_REQUEST_CHUNKS
  ):
    raise _validation_error("market-data request has an invalid expected chunk count")
  expected = raw_expected
  if raw_received is None:
    received = expected
  elif isinstance(raw_received, bool) or not isinstance(raw_received, int):
    raise _validation_error("market-data request has an invalid received chunk count")
  else:
    received = raw_received
  if expected <= 0 or received != expected or len(manifest) != expected:
    raise _validation_error(
      f"market-data transfer is incomplete: expected={expected} "
      f"received={received} manifest={len(manifest)}"
    )
  indices: list[int] = []
  for item in manifest:
    if not isinstance(item, dict):
      raise _validation_error("market-data manifest contains a non-object item")
    chunk_index = item.get("chunk_index")
    if isinstance(chunk_index, bool) or not isinstance(chunk_index, int):
      raise _validation_error("market-data manifest has an invalid chunk index")
    indices.append(chunk_index)
  if indices != list(range(expected)):
    raise _validation_error("market-data transfer has missing or unordered chunks")
  payload = request.get("request_payload") or {}
  if isinstance(payload, str):
    try:
      payload = json.loads(payload, parse_constant=_reject_json_constant)
    except (RecursionError, json.JSONDecodeError) as exc:
      raise _validation_error("market-data request payload is invalid JSON") from exc
  if not isinstance(payload, dict):
    raise _validation_error("market-data request payload is not an object")
  return request, payload, manifest


async def load_uploaded_request_records(
  store: MarketDataTransferStore,
  request_id: str,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
  request, payload, manifest = await load_uploaded_request_manifest(store, request_id)
  records = await asyncio.to_thread(_read_transfer_records, manifest)
  return request, payload, records


def _validate_bar_manifest(
  manifest: list[dict[str, Any]],
  payload: dict[str, Any],
) -> dict[str, Any]:
  validator = _BarTransferValidator(_parse_bars_request(payload))
  for chunk in _iter_transfer_chunks(manifest):
    for record in chunk:
      validator.consume(record)
  return validator.finish()


def _save_market_data_period_sync(
  *,
  period: str,
  market_data: dict[str, pd.DataFrame],
) -> dict[str, Any]:
  normalized = preprocess_market_data(period, market_data)
  service = HistoricalMarketDataService()
  accepted = (
    service.bulk_save_ticks(normalized)
    if period == "tick"
    else service.bulk_save_klines(period, normalized)
  )
  return {
    "period": period,
    "saved_count": accepted,
    "save_time": time_utils.now().isoformat(),
    "status": "success",
  }


async def save_market_data_period(
  *,
  period: str,
  market_data: dict[str, pd.DataFrame],
) -> dict[str, Any]:
  return await asyncio.to_thread(
    _save_market_data_period_sync,
    period=period,
    market_data=market_data,
  )


async def _iterate_record_chunks(
  record_chunks: Iterable[list[dict[str, Any]]]
  | AsyncIterable[list[dict[str, Any]]],
) -> AsyncIterable[list[dict[str, Any]]]:
  if isinstance(record_chunks, AsyncIterable):
    async for chunk in record_chunks:
      yield chunk
  else:
    for chunk in record_chunks:
      yield chunk


async def _uploaded_key_batches(
  manifest: list[dict[str, Any]],
) -> AsyncIterable[ExpectedBarKeyBatch]:
  """Re-read uploaded keys in bounded batches for merge-aware verification."""

  budget = _TransferBudget()
  current_group: tuple[str, str] | None = None
  keys: list[tuple[int, int | None]] = []
  for item in manifest:
    chunk = await asyncio.to_thread(_read_transfer_chunk, item, budget)
    for record in chunk:
      if "record_type" in record:
        if keys and current_group is not None:
          code, period = current_group
          yield ExpectedBarKeyBatch(
            code=code,
            period=period,
            keys=tuple(keys),
          )
        current_group = None
        keys = []
        continue
      group = (str(record["code"]), str(record["period"]))
      if current_group is not None and group != current_group:
        if keys:
          code, period = current_group
          yield ExpectedBarKeyBatch(
            code=code,
            period=period,
            keys=tuple(keys),
          )
        keys = []
      current_group = group
      keys.append(
        (
          int(record["time"]),
          (
            int(record[HISTORICAL_TICK_ORDINAL_FIELD])
            if group[1] == "tick"
            else None
          ),
        )
      )
      if len(keys) >= MARKET_DATA_WRITE_BATCH_RECORDS:
        code, period = current_group
        yield ExpectedBarKeyBatch(
          code=code,
          period=period,
          keys=tuple(keys),
        )
        keys = []
  if keys and current_group is not None:
    code, period = current_group
    yield ExpectedBarKeyBatch(code=code, period=period, keys=tuple(keys))


async def _persist_validated_records(
  record_chunks: Iterable[list[dict[str, Any]]]
  | AsyncIterable[list[dict[str, Any]]],
  *,
  payload: dict[str, Any],
  expected_audit: dict[str, Any],
  save_period: SaveMarketData,
) -> dict[str, Any]:
  scope = _parse_bars_request(payload)
  validator = _BarTransferValidator(scope)
  daily_counts: dict[tuple[str, str, date], int] = {}
  for period, code in scope.groups:
    current_day = scope.start_date
    while current_day <= scope.end_date:
      daily_counts[(code, period, current_day)] = 0
      current_day += timedelta(days=1)
  batch: list[dict[str, Any]] = []
  batch_bytes = 0
  batch_group: tuple[str, str] | None = None
  accepted = 0

  async def flush() -> None:
    nonlocal batch, batch_bytes, batch_group, accepted
    if not batch or batch_group is None:
      return
    period, code = batch_group
    frame = pd.DataFrame(
      [
        {key: value for key, value in record.items() if key not in {"code", "period"}}
        for record in batch
      ]
    )
    result = await save_period(period=period, market_data={code: frame})
    saved_count = int(result.get("saved_count", 0))
    if result.get("status") != "success" or saved_count != len(batch):
      raise RuntimeError(
        f"{code}/{period} market-data write was not fully accepted: "
        f"expected={len(batch)} accepted={saved_count}"
      )
    accepted += saved_count
    batch = []
    batch_bytes = 0
    batch_group = None

  async for chunk in _iterate_record_chunks(record_chunks):
    for record in chunk:
      validator.consume(record)
      if "record_type" in record:
        await flush()
        continue
      record_day = datetime.fromtimestamp(
        int(record["time"]) / 1000,
        _MARKET_DATA_REQUEST_TIMEZONE,
      ).date()
      coverage_key = (str(record["code"]), str(record["period"]), record_day)
      daily_counts[coverage_key] = daily_counts.get(coverage_key, 0) + 1
      group = (str(record["period"]), str(record["code"]))
      encoded_size = _encoded_record_size(record)
      if encoded_size > MARKET_DATA_WRITE_BATCH_BYTES:
        raise MarketDataValidationError(
          "market-data record exceeds persistence batch byte limit"
        )
      if batch and (
        batch_group != group
        or len(batch) >= MARKET_DATA_WRITE_BATCH_RECORDS
        or batch_bytes + encoded_size > MARKET_DATA_WRITE_BATCH_BYTES
      ):
        await flush()
      if batch_group is None:
        batch_group = group
      batch.append(record)
      batch_bytes += encoded_size
  await flush()
  actual_audit = validator.finish()
  if actual_audit != expected_audit:
    raise MarketDataValidationError("market-data manifest changed between validation passes")
  if accepted != int(expected_audit["records_received"]):
    raise RuntimeError(
      "market-data accepted row count mismatch: "
      f"expected={expected_audit['records_received']} accepted={accepted}"
    )
  return {
    **expected_audit,
    "records_saved": accepted,
    "rows_accepted": accepted,
    "day_coverage": [
      {
        "instrument_code": code,
        "period": period,
        "trading_date": trading_date.isoformat(),
        "point_count": point_count,
      }
      for (code, period, trading_date), point_count in sorted(daily_counts.items())
    ],
  }


async def persist_bar_records(
  records: list[dict[str, Any]],
  *,
  payload: dict[str, Any],
  save_period: SaveMarketData = save_market_data_period,
) -> dict[str, Any]:
  validator = _BarTransferValidator(_parse_bars_request(payload))
  for record in records:
    validator.consume(record)
  audit = validator.finish()
  return await _persist_validated_records(
    [records],
    payload=payload,
    expected_audit=audit,
    save_period=save_period,
  )


async def ingest_uploaded_bar_request(
  store: MarketDataTransferStore,
  request_id: str,
  *,
  save_period: SaveMarketData = save_market_data_period,
  verify_persistence: VerifyPersistence | None = None,
) -> dict[str, Any]:
  _, payload, manifest = await load_uploaded_request_manifest(store, request_id)
  audit = await asyncio.to_thread(_validate_bar_manifest, manifest, payload)

  async def read_chunks() -> AsyncIterable[list[dict[str, Any]]]:
    budget = _TransferBudget()
    for item in manifest:
      yield await asyncio.to_thread(_read_transfer_chunk, item, budget)

  persisted = await _persist_validated_records(
    read_chunks(),
    payload=payload,
    expected_audit=audit,
    save_period=save_period,
  )
  scope = _parse_bars_request(payload)
  verifier = verify_persistence or verify_persisted_bar_summaries
  verification = await verifier(
    code_summaries=audit["code_summaries"],
    expected_key_batches=_uploaded_key_batches(manifest),
    start_ms=scope.start_ms,
    end_exclusive_ms=scope.end_exclusive_ms,
  )
  records_verified = int(verification.get("records_verified", -1))
  summary_fields = (
    "code",
    "period",
    "row_count",
    "min_time",
    "max_time",
    "key_sha256",
  )
  expected_verified_summaries = [
    {field: summary.get(field) for field in summary_fields}
    for summary in audit["code_summaries"]
  ]
  if (
    verification.get("status") != "verified"
    or records_verified != int(audit["records_received"])
    or int(verification.get("groups_verified", -1))
    != len(expected_verified_summaries)
    or verification.get("code_summaries") != expected_verified_summaries
  ):
    raise MarketDataPersistenceVerificationError(
      "Influx persistence verification did not prove every accepted row"
    )
  return {
    **persisted,
    "records_verified": records_verified,
    "persistence_verification": verification,
  }


async def ingest_uploaded_market_data_request(
  store: MarketDataTransferStore,
  request_id: str,
) -> dict[str, Any]:
  """Route a validated upload to its single declared persistence destination."""

  request = await store.market_data_request(request_id)
  if request is None:
    raise _validation_error("market-data request disappeared before ingestion")
  payload = request.get("request_payload") or {}
  if isinstance(payload, str):
    try:
      payload = json.loads(payload, parse_constant=_reject_json_constant)
    except (RecursionError, json.JSONDecodeError) as exc:
      raise _validation_error("market-data request payload is invalid JSON") from exc
  if not isinstance(payload, dict):
    raise _validation_error("market-data request payload is not an object")
  destination = str(payload.get("destination") or "influxdb").strip().lower()
  if destination == "influxdb":
    return await ingest_uploaded_bar_request(store, request_id)
  raise _validation_error("market-data request destination is unsupported")


def _cleanup_completed_staging(
  request_id: str,
  manifest: list[dict[str, Any]],
) -> None:
  root = market_data_staging_root()
  parents: set[Path] = set()
  for item in manifest:
    storage_reference = (
      item.get("storage_reference") if isinstance(item, dict) else None
    )
    try:
      path = safe_market_data_staging_file(
        root=root,
        request_id=request_id,
        storage_reference=str(storage_reference or ""),
      )
    except FileNotFoundError:
      continue
    except (OSError, RuntimeError, TypeError, ValueError):
      logger.warning(
        "Skipped unsafe market-data staging path: %s",
        storage_reference,
      )
      continue
    path.unlink(missing_ok=True)
    parents.add(path.parent)
  for parent in parents:
    try:
      safe_parent = safe_market_data_request_directory(root, parent)
      if safe_parent.is_symlink() or is_reparse_point(safe_parent):
        logger.warning("Skipped unsafe market-data staging parent: %s", parent)
        continue
      safe_parent.rmdir()
    except (OSError, RuntimeError):
      pass


async def _renew_claim(
  store: MarketDataTransferStore,
  request_id: str,
  claim_token: str,
) -> None:
  while True:
    await asyncio.sleep(MARKET_DATA_CLAIM_RENEW_SECONDS)
    if not await store.renew_market_data_request_claim(
      request_id,
      claim_token=claim_token,
    ):
      raise RuntimeError("market-data ingestion claim was lost")


async def claim_ingest_and_finish_market_data_request(
  store: MarketDataTransferStore,
  request_id: str,
  *,
  ingest_request: IngestRequest = ingest_uploaded_market_data_request,
) -> dict[str, Any] | None:
  """Claim one immutable transfer and converge validation/persistence durably."""

  claim_token = await store.claim_market_data_request(request_id)
  if claim_token is None:
    return None
  lease_task = asyncio.create_task(
    _renew_claim(store, request_id, claim_token),
    name=f"market-data-ingestion-lease:{request_id}",
  )
  ingestion_task: asyncio.Task[dict[str, Any]] | None = None
  try:
    ingestion_task = asyncio.create_task(
      ingest_request(store, request_id),
      name=f"market-data-ingestion:{request_id}",
    )
    done, _ = await asyncio.wait(
      {ingestion_task, lease_task},
      return_when=asyncio.FIRST_COMPLETED,
    )
    if lease_task in done:
      lease_error = lease_task.exception()
      if lease_error is not None:
        ingestion_task.cancel()
        await asyncio.gather(ingestion_task, return_exceptions=True)
        raise lease_error
    ingestion = await ingestion_task
    await store.finish_market_data_request(
      request_id,
      status="COMPLETED",
      ingestion_result=ingestion,
      claim_token=claim_token,
    )
    try:
      manifest = await store.market_data_transfers(request_id)
      await asyncio.to_thread(_cleanup_completed_staging, request_id, manifest)
    except Exception:
      logger.exception(
        "Could not clean completed market-data staging request_id=%s",
        request_id,
      )
    return {"status": "completed", "request_id": request_id, **ingestion}
  except asyncio.CancelledError:
    # Cancellation is not evidence that the immutable transfer is invalid.
    try:
      await asyncio.shield(
        store.release_market_data_request_claim(
          request_id,
          claim_token=claim_token,
          error="CancelledError: market-data ingestion was cancelled",
        )
      )
    except Exception:
      logger.exception(
        "Could not release cancelled market-data claim request_id=%s",
        request_id,
      )
    raise
  except MarketDataValidationError as exc:
    reason = f"{exc.__class__.__name__}: {exc}"
    try:
      await store.finish_market_data_request(
        request_id,
        status="FAILED",
        error=reason,
        claim_token=claim_token,
      )
    except Exception as finish_exc:
      deferred_reason = f"{finish_exc.__class__.__name__}: {finish_exc}"
      logger.exception(
        "Could not terminally reject market-data transfer after losing its claim "
        "request_id=%s reason=%s",
        request_id,
        deferred_reason,
      )
      return {
        "status": "retryable",
        "request_id": request_id,
        "reason": deferred_reason,
      }
    logger.warning(
      "Rejected invalid market-data transfer request_id=%s reason=%s",
      request_id,
      reason,
    )
    return {"status": "failed", "request_id": request_id, "reason": reason}
  except Exception as exc:
    reason = f"{exc.__class__.__name__}: {exc}"
    await store.release_market_data_request_claim(
      request_id,
      claim_token=claim_token,
      error=reason,
    )
    logger.exception(
      "Deferred retryable market-data ingestion request_id=%s reason=%s",
      request_id,
      reason,
    )
    return {"status": "retryable", "request_id": request_id, "reason": reason}
  finally:
    if ingestion_task is not None and not ingestion_task.done():
      ingestion_task.cancel()
    lease_task.cancel()
    await asyncio.gather(
      *(task for task in (ingestion_task, lease_task) if task is not None),
      return_exceptions=True,
    )
