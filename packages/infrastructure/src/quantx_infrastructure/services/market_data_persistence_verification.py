"""Bounded InfluxDB read-back verification for merged historical data."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
import time
from collections.abc import AsyncIterable, Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from quantx_contracts import (
  HISTORICAL_TICK_ORDINAL_FIELD,
  HISTORICAL_TICK_ORDINALS_PER_MILLISECOND,
  HISTORICAL_TICK_SOURCE_TIME_FIELD,
  historical_bar_key,
)

from quantx_infrastructure.database.timeseries import get_timeseries_connection

from .market_data_timing import market_data_stage

MARKET_DATA_READBACK_PAGE_ROWS = 2000
MARKET_DATA_READBACK_GROUP_CODES = 20
MARKET_DATA_READBACK_GROUP_KEYS = 10_000
MARKET_DATA_READBACK_CONCURRENCY = 2
MARKET_DATA_READBACK_MAX_ATTEMPTS = 4
MARKET_DATA_READBACK_RETRY_DELAYS_SECONDS = (0.25, 0.75, 1.5)
MARKET_DATA_READBACK_MAX_SPLIT_NODES = 32
MARKET_DATA_READBACK_MAX_SPLIT_DEPTH = 8
MARKET_DATA_READBACK_TIMEOUT_SECONDS = 60.0

_MEASUREMENTS = {
  "tick": "ticks",
  "1m": "kline_1m",
  "1d": "kline_1d",
}
_UTC_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class MarketDataPersistenceVerificationError(RuntimeError):
  """Influx read-back could not yet prove the requested archive complete."""


class MarketDataPersistenceQueryError(MarketDataPersistenceVerificationError):
  """Influx read-back failed and must not be interpreted as an empty result."""


class MarketDataPersistenceMismatchError(MarketDataPersistenceVerificationError):
  """An uploaded key is missing or a persisted key is structurally invalid."""


class MarketDataPersistenceCapacityError(MarketDataPersistenceQueryError):
  """The query exceeds the storage scan limit and may be subdivided."""

  def __init__(self, diagnostic: dict[str, Any]):
    super().__init__("market-data read-back exceeded the storage scan limit")
    self.diagnostic = diagnostic


class MarketDataPersistenceBlockedError(MarketDataPersistenceQueryError):
  """Repeating the same read-back cannot make progress within its budget."""

  def __init__(self, reason_code: str, diagnostic: dict[str, Any] | None = None):
    super().__init__(reason_code)
    self.reason_code = reason_code
    self.diagnostic = diagnostic or {}


@dataclass
class ReadbackBudget:
  """One group's budget, shared by its pages, split children and retries."""

  deadline: float = field(
    default_factory=lambda: time.monotonic() + MARKET_DATA_READBACK_TIMEOUT_SECONDS
  )
  nodes: int = 0
  capacity_diagnostic: dict[str, Any] | None = None
  first_capacity_diagnostic: dict[str, Any] | None = None

  def remaining(self) -> float:
    remaining = self.deadline - time.monotonic()
    if remaining <= 0:
      self.block()
    return remaining

  def block(self) -> None:
    raise MarketDataPersistenceBlockedError(
      "DEPENDENCY_QUERY_CAPACITY_BLOCKED"
      if self.capacity_diagnostic is not None
      else "READBACK_BUDGET_EXHAUSTED",
      {**self.capacity_diagnostic, "first_failure": self.first_capacity_diagnostic}
      if self.capacity_diagnostic is not None
      else None,
    ) from None

  def enter(self, depth: int) -> None:
    self.remaining()
    if (
      depth > MARKET_DATA_READBACK_MAX_SPLIT_DEPTH
      or self.nodes >= MARKET_DATA_READBACK_MAX_SPLIT_NODES
    ):
      self.block()
    self.nodes += 1


def _query_failure(
  exc: Exception,
  sql: str,
  **context: Any,
) -> MarketDataPersistenceQueryError:
  # Inspect provider text locally; never propagate credentials or raw error chains.
  if re.search(
    r"\b(unauthenticated|unauthorized|forbidden|permission denied)\b", str(exc), re.I
  ):
    return MarketDataPersistenceBlockedError("DEPENDENCY_AUTH_BLOCKED")
  if re.search(
    r"scan\s+\d+\s+Parquet files.*exceeding.*file limit", str(exc), re.I | re.S
  ):
    return MarketDataPersistenceCapacityError(
      {
        **context,
        "query_sha256": hashlib.sha256(sql.encode()).hexdigest(),
        "error_code": "QUERY_SCAN_LIMIT",
      }
    )
  return MarketDataPersistenceQueryError("Influx read-back query failed")


class _InfluxClient(Protocol):
  def query(
    self,
    query: str,
    language: str = "sql",
    mode: str = "all",
    database: str | None = None,
    **kwargs: Any,
  ) -> Any: ...


class _InfluxClientContext(Protocol):
  def __enter__(self) -> _InfluxClient: ...

  def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None: ...


class _InfluxConnection(Protocol):
  def get_client(self, *, timeout: float | None = None) -> _InfluxClientContext: ...


class ReadbackProgress(Protocol):
  async def readback_result(self, digest: str) -> dict[str, int] | None: ...

  async def confirm_readback(self, digest: str, result: dict[str, int]) -> None: ...


def _proof_key(value: Any) -> str:
  return hashlib.sha256(
    json.dumps(
      {"verification": "uploaded_key_subset_v2", "scope": value},
      sort_keys=True,
      separators=(",", ":"),
    ).encode()
  ).hexdigest()


Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class ExpectedBarKeyBatch:
  """One bounded, canonically ordered batch of uploaded source keys."""

  code: str
  period: str
  keys: tuple[tuple[int, int | None], ...]


def _utc_datetime_from_epoch_ms(value: int) -> datetime:
  return _UTC_EPOCH + timedelta(milliseconds=value)


def _utc_datetime(value: Any) -> datetime:
  if hasattr(value, "to_pydatetime"):
    value = value.to_pydatetime()
  if isinstance(value, str):
    try:
      value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
      raise MarketDataPersistenceQueryError(
        "Influx read-back returned an invalid time value"
      ) from exc
  if not isinstance(value, datetime):
    raise MarketDataPersistenceQueryError(
      "Influx read-back returned a non-datetime time value"
    )
  if value.tzinfo is None or value.utcoffset() is None:
    return value.replace(tzinfo=timezone.utc)
  return value.astimezone(timezone.utc)


def _epoch_millis(value: datetime) -> int:
  delta = value - _UTC_EPOCH
  return (delta.days * 86_400 + delta.seconds) * 1000 + delta.microseconds // 1000


def _sql_timestamp(value: datetime) -> str:
  return (
    value.astimezone(timezone.utc)
    .isoformat(timespec="microseconds")
    .replace("+00:00", "Z")
  )


def _integer(value: Any, *, field: str) -> int:
  if isinstance(value, bool) or not isinstance(value, int):
    raise MarketDataPersistenceQueryError(
      f"Influx read-back returned non-integer {field}"
    )
  return value


def _expected_summary(summary: dict[str, Any]) -> dict[str, Any]:
  code = summary.get("code")
  period = summary.get("period")
  row_count = summary.get("row_count")
  min_time = summary.get("min_time")
  max_time = summary.get("max_time")
  key_sha256 = summary.get("key_sha256")
  if not isinstance(code, str) or not code:
    raise ValueError("market-data persistence summary has no code")
  if period not in _MEASUREMENTS:
    raise ValueError(f"unsupported market-data persistence period: {period}")
  if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0:
    raise ValueError("market-data persistence summary has an invalid row_count")
  if row_count == 0:
    if min_time is not None or max_time is not None:
      raise ValueError("empty market-data persistence summary has time bounds")
  elif (
    isinstance(min_time, bool)
    or not isinstance(min_time, int)
    or isinstance(max_time, bool)
    or not isinstance(max_time, int)
  ):
    raise ValueError("non-empty market-data persistence summary has invalid bounds")
  if not isinstance(key_sha256, str) or len(key_sha256) != 64:
    raise ValueError("market-data persistence summary has an invalid key digest")
  return {
    "code": code,
    "period": period,
    "row_count": row_count,
    "min_time": min_time,
    "max_time": max_time,
    "key_sha256": key_sha256,
  }


def _query_page_sql(
  *,
  period: str,
  start: datetime,
  end: datetime,
  after: datetime | None,
  limit: int,
) -> str:
  measurement = _MEASUREMENTS[period]
  fields = (
    f"time, {HISTORICAL_TICK_SOURCE_TIME_FIELD}, {HISTORICAL_TICK_ORDINAL_FIELD}"
    if period == "tick"
    else "time"
  )
  after_clause = f"AND time > '{_sql_timestamp(after)}' " if after is not None else ""
  return (
    f"SELECT {fields} FROM {measurement} "
    "WHERE stock_code = $stock_code AND period = $period "
    f"AND time >= '{_sql_timestamp(start)}' "
    f"AND time < '{_sql_timestamp(end)}' "
    f"{after_clause}"
    "ORDER BY time ASC "
    f"LIMIT {limit}"
  )


def _storage_time_for_key(
  *,
  period: str,
  source_time_ms: int,
  tick_ordinal: int | None,
) -> datetime:
  storage_time = _utc_datetime_from_epoch_ms(source_time_ms)
  if period != "tick":
    if tick_ordinal is not None:
      raise ValueError("non-tick persistence key contains tick_ordinal")
    return storage_time
  if (
    isinstance(tick_ordinal, bool)
    or not isinstance(tick_ordinal, int)
    or not 0 <= tick_ordinal < HISTORICAL_TICK_ORDINALS_PER_MILLISECOND
  ):
    raise ValueError("tick persistence key has an invalid tick_ordinal")
  return storage_time + timedelta(microseconds=tick_ordinal)


def _read_expected_key_batch_once(
  *,
  batch: ExpectedBarKeyBatch,
  connection: _InfluxConnection | None,
  page_rows: int,
  cancelled: threading.Event | None = None,
  budget: ReadbackBudget | None = None,
) -> dict[str, int]:
  """Prove uploaded keys are present while tolerating pre-existing points."""

  if page_rows < 1 or page_rows > MARKET_DATA_READBACK_PAGE_ROWS:
    raise ValueError(
      f"page_rows must be between 1 and {MARKET_DATA_READBACK_PAGE_ROWS}"
    )
  if not batch.keys:
    raise ValueError("market-data persistence key batch must not be empty")
  if len(batch.keys) > MARKET_DATA_READBACK_PAGE_ROWS:
    raise ValueError(
      "market-data persistence key batch exceeds the bounded read-back limit"
    )
  if batch.period not in _MEASUREMENTS:
    raise ValueError(f"unsupported market-data persistence period: {batch.period}")

  expected: list[tuple[datetime, int, int | None]] = []
  previous_storage_time: datetime | None = None
  for source_time_ms, tick_ordinal in batch.keys:
    if isinstance(source_time_ms, bool) or not isinstance(source_time_ms, int):
      raise ValueError("market-data persistence key has an invalid time")
    storage_time = _storage_time_for_key(
      period=batch.period,
      source_time_ms=source_time_ms,
      tick_ordinal=tick_ordinal,
    )
    if previous_storage_time is not None and storage_time <= previous_storage_time:
      raise ValueError("market-data persistence key batch is unordered or duplicated")
    expected.append((storage_time, source_time_ms, tick_ordinal))
    previous_storage_time = storage_time

  resolved_connection = connection or get_timeseries_connection()
  if resolved_connection is None:
    raise MarketDataPersistenceQueryError(
      "InfluxDB is unavailable for market-data persistence verification"
    )

  code = str(batch.code)
  period = str(batch.period)
  required_columns = {
    "time",
    *(
      (HISTORICAL_TICK_SOURCE_TIME_FIELD, HISTORICAL_TICK_ORDINAL_FIELD)
      if period == "tick"
      else ()
    ),
  }
  start = expected[0][0]
  end = expected[-1][0] + timedelta(microseconds=1)
  after: datetime | None = None
  expected_index = 0
  existing_rows_observed = 0

  budget = budget or ReadbackBudget()
  sql = ""
  try:
    with resolved_connection.get_client(timeout=budget.remaining()) as client:
      while expected_index < len(expected):
        sql = _query_page_sql(
          period=period,
          start=start,
          end=end,
          after=after,
          limit=page_rows,
        )
        _check_readback_cancelled(cancelled)
        with market_data_stage("read_query_open"):
          reader = client.query(
            query=sql,
            language="sql",
            mode="reader",
            query_parameters={"stock_code": code, "period": period},
            timeout=budget.remaining(),
          )
        if reader is None:
          raise MarketDataPersistenceQueryError(
            f"Influx read-back returned no reader for {code}/{period}"
          )
        page_count = 0
        try:
          schema_names = set(reader.schema.names)
          missing_columns = required_columns - schema_names
          if missing_columns:
            raise MarketDataPersistenceQueryError(
              "Influx read-back is missing required columns for "
              f"{code}/{period}: {sorted(missing_columns)}"
            )
          for arrow_batch in reader:
            _check_readback_cancelled(cancelled)
            budget.remaining()
            if page_count + arrow_batch.num_rows > page_rows:
              raise MarketDataPersistenceQueryError(
                f"Influx read-back exceeded its {page_rows}-row page bound"
              )
            columns = {
              name: arrow_batch.column(arrow_batch.schema.get_field_index(name))
              for name in required_columns
            }
            for index in range(arrow_batch.num_rows):
              storage_time = _utc_datetime(columns["time"][index].as_py())
              if after is not None and storage_time <= after:
                raise MarketDataPersistenceQueryError(
                  f"Influx read-back did not advance for {code}/{period}"
                )
              if storage_time < start or storage_time >= end:
                raise MarketDataPersistenceQueryError(
                  f"Influx read-back escaped the key batch for {code}/{period}"
                )

              if period == "tick":
                actual_source_time_ms = _integer(
                  columns[HISTORICAL_TICK_SOURCE_TIME_FIELD][index].as_py(),
                  field=HISTORICAL_TICK_SOURCE_TIME_FIELD,
                )
                actual_ordinal = _integer(
                  columns[HISTORICAL_TICK_ORDINAL_FIELD][index].as_py(),
                  field=HISTORICAL_TICK_ORDINAL_FIELD,
                )
                if not 0 <= actual_ordinal < HISTORICAL_TICK_ORDINALS_PER_MILLISECOND:
                  raise MarketDataPersistenceMismatchError(
                    f"persisted tick ordinal is out of range for {code}"
                  )
                actual_storage_time = _storage_time_for_key(
                  period=period,
                  source_time_ms=actual_source_time_ms,
                  tick_ordinal=actual_ordinal,
                )
                if storage_time != actual_storage_time:
                  raise MarketDataPersistenceMismatchError(
                    f"persisted tick storage key is inconsistent for {code}"
                  )
              elif storage_time.microsecond % 1000:
                raise MarketDataPersistenceMismatchError(
                  f"persisted kline time is not millisecond aligned for {code}/{period}"
                )

              expected_storage_time, expected_source_time_ms, expected_ordinal = (
                expected[expected_index]
              )
              if storage_time < expected_storage_time:
                existing_rows_observed += 1
                after = storage_time
                page_count += 1
                continue
              if storage_time > expected_storage_time:
                raise MarketDataPersistenceMismatchError(
                  "Influx read-back is missing an uploaded key for "
                  f"{code}/{period}: time={expected_source_time_ms} "
                  f"tick_ordinal={expected_ordinal}"
                )
              if period == "tick" and (
                actual_source_time_ms != expected_source_time_ms
                or actual_ordinal != expected_ordinal
              ):
                raise MarketDataPersistenceMismatchError(
                  f"Influx read-back returned a conflicting tick key for {code}"
                )
              expected_index += 1
              after = storage_time
              page_count += 1
              if expected_index == len(expected):
                break
            if expected_index == len(expected):
              break
        finally:
          close = getattr(reader, "close", None)
          if callable(close):
            close()
        if expected_index == len(expected):
          break
        if page_count < page_rows:
          break
  except MarketDataPersistenceVerificationError:
    raise
  except Exception as exc:
    raise _query_failure(
      exc,
      sql,
      page_after=after.isoformat() if after else None,
      page_rows=page_rows,
    ) from None

  budget.remaining()
  if expected_index != len(expected):
    missing_storage_time, missing_source_time_ms, missing_ordinal = expected[
      expected_index
    ]
    del missing_storage_time
    raise MarketDataPersistenceMismatchError(
      "Influx read-back is missing an uploaded key for "
      f"{code}/{period}: time={missing_source_time_ms} "
      f"tick_ordinal={missing_ordinal}"
    )
  return {
    "records_verified": len(expected),
    "existing_rows_observed": existing_rows_observed,
  }


def _check_readback_cancelled(cancelled: threading.Event | None) -> None:
  if cancelled is not None and cancelled.is_set():
    raise MarketDataPersistenceQueryError("market-data read-back was cancelled")


async def _await_readback(function: Callable[..., Any], **kwargs: Any) -> Any:
  cancelled = threading.Event()
  task = asyncio.create_task(asyncio.to_thread(function, cancelled=cancelled, **kwargs))
  try:
    with market_data_stage("read_query_and_validate"):
      return await asyncio.shield(task)
  except asyncio.CancelledError:
    cancelled.set()
    try:
      await asyncio.shield(task)
    except Exception:
      pass
    raise


def _read_expected_key_group_once(
  *,
  batches: Sequence[ExpectedBarKeyBatch],
  connection: _InfluxConnection | None,
  page_rows: int,
  cancelled: threading.Event | None = None,
  budget: ReadbackBudget | None = None,
) -> dict[str, int]:
  if not 1 <= page_rows <= MARKET_DATA_READBACK_PAGE_ROWS:
    raise ValueError("invalid bounded read-back page size")
  if not 1 <= len(batches) <= MARKET_DATA_READBACK_GROUP_CODES:
    raise ValueError("invalid bounded read-back group size")
  if sum(len(batch.keys) for batch in batches) > MARKET_DATA_READBACK_GROUP_KEYS:
    raise ValueError("read-back group exceeds expected key limit")
  period = batches[0].period
  if period == "tick" or period not in _MEASUREMENTS:
    raise ValueError("grouped read-back requires a supported K-line period")
  expected: dict[str, list[datetime]] = {}
  bounds: dict[str, tuple[datetime, datetime]] = {}
  params: dict[str, str] = {"period": period}
  predicates = []
  for index, batch in enumerate(batches):
    code = str(batch.code)
    if batch.period != period or code in expected:
      raise ValueError("read-back group requires unique codes in one period")
    if not batch.keys or len(batch.keys) > MARKET_DATA_READBACK_PAGE_ROWS:
      raise ValueError("invalid expected key batch size")
    values = []
    for source_time_ms, ordinal in batch.keys:
      if isinstance(source_time_ms, bool) or not isinstance(source_time_ms, int):
        raise ValueError("invalid expected key time")
      value = _storage_time_for_key(
        period=period, source_time_ms=source_time_ms, tick_ordinal=ordinal
      )
      if values and value <= values[-1]:
        raise ValueError("expected keys are unordered or duplicated")
      values.append(value)
    expected[code] = values
    start, end = values[0], values[-1] + timedelta(microseconds=1)
    bounds[code] = (start, end)
    params[f"code_{index}"] = code
    predicates.append(
      f"(stock_code = $code_{index} AND time >= '{_sql_timestamp(start)}' AND time < '{_sql_timestamp(end)}')"
    )
  resolved = connection or get_timeseries_connection()
  if resolved is None:
    raise MarketDataPersistenceQueryError("InfluxDB is unavailable for read-back")
  indices = dict.fromkeys(expected, 0)
  after: tuple[str, datetime] | None = None
  extras = 0
  budget = budget or ReadbackBudget()
  sql = ""
  try:
    _check_readback_cancelled(cancelled)
    with resolved.get_client(timeout=budget.remaining()) as client:
      while any(indices[code] < len(keys) for code, keys in expected.items()):
        _check_readback_cancelled(cancelled)
        cursor = ""
        if after is not None:
          params["after_code"] = after[0]
          cursor = f"AND (stock_code > $after_code OR (stock_code = $after_code AND time > '{_sql_timestamp(after[1])}')) "
        sql = (
          f"SELECT stock_code,time FROM {_MEASUREMENTS[period]} WHERE period = $period "
          f"AND time >= '{_sql_timestamp(min(v[0] for v in bounds.values()))}' "
          f"AND time < '{_sql_timestamp(max(v[1] for v in bounds.values()))}' "
          f"AND ({' OR '.join(predicates)}) {cursor}ORDER BY stock_code ASC,time ASC LIMIT {page_rows}"
        )
        with market_data_stage("read_query_open"):
          reader = client.query(
            query=sql,
            language="sql",
            mode="reader",
            query_parameters=dict(params),
            timeout=budget.remaining(),
          )
        if reader is None:
          raise MarketDataPersistenceQueryError("grouped read-back returned no reader")
        count = 0
        try:
          if not {"stock_code", "time"}.issubset(reader.schema.names):
            raise MarketDataPersistenceQueryError(
              "grouped read-back is missing required columns"
            )
          for arrow_batch in reader:
            _check_readback_cancelled(cancelled)
            budget.remaining()
            if count + arrow_batch.num_rows > page_rows:
              raise MarketDataPersistenceQueryError(
                "grouped read-back exceeded page bound"
              )
            codes = arrow_batch.column(arrow_batch.schema.get_field_index("stock_code"))
            times = arrow_batch.column(arrow_batch.schema.get_field_index("time"))
            for i in range(arrow_batch.num_rows):
              code = codes[i].as_py()
              value = _utc_datetime(times[i].as_py())
              if code not in expected:
                raise MarketDataPersistenceQueryError(
                  "grouped read-back returned an unrequested code"
                )
              key = (code, value)
              if after is not None and key <= after:
                raise MarketDataPersistenceQueryError(
                  "grouped read-back did not advance"
                )
              if not bounds[code][0] <= value < bounds[code][1]:
                raise MarketDataPersistenceQueryError(
                  "grouped read-back escaped key bounds"
                )
              position = indices[code]
              if position < len(expected[code]):
                wanted = expected[code][position]
                if value > wanted:
                  raise MarketDataPersistenceMismatchError(
                    "grouped read-back is missing an uploaded key"
                  )
                if value == wanted:
                  indices[code] += 1
                else:
                  extras += 1
              else:
                extras += 1
              after = key
              count += 1
        finally:
          close = getattr(reader, "close", None)
          if callable(close):
            close()
        if count < page_rows:
          break
  except MarketDataPersistenceVerificationError:
    raise
  except Exception as exc:
    raise _query_failure(
      exc,
      sql,
      page_after=[after[0], after[1].isoformat()] if after else None,
      page_rows=page_rows,
    ) from None
  budget.remaining()
  if any(indices[code] != len(keys) for code, keys in expected.items()):
    raise MarketDataPersistenceMismatchError(
      "grouped read-back is missing an uploaded key"
    )
  return {"records_verified": sum(indices.values()), "existing_rows_observed": extras}


def _split_expected_keys(
  batches: Sequence[ExpectedBarKeyBatch],
) -> tuple[tuple[ExpectedBarKeyBatch, ...], tuple[ExpectedBarKeyBatch, ...]] | None:
  times = [
    _storage_time_for_key(period=b.period, source_time_ms=t, tick_ordinal=o)
    for b in batches
    for t, o in b.keys
  ]
  start, end = min(times), max(times)
  if start == end:
    if len(batches) == 1:
      return None
    middle = len(batches) // 2
    return tuple(batches[:middle]), tuple(batches[middle:])
  middle_time = start + (end - start) // 2
  children: list[list[ExpectedBarKeyBatch]] = [[], []]
  for batch in batches:
    keys: list[list[tuple[int, int | None]]] = [[], []]
    for key in batch.keys:
      value = _storage_time_for_key(
        period=batch.period, source_time_ms=key[0], tick_ordinal=key[1]
      )
      keys[int(value > middle_time)].append(key)
    for index, values in enumerate(keys):
      if values:
        children[index].append(
          ExpectedBarKeyBatch(batch.code, batch.period, tuple(values))
        )
  return tuple(children[0]), tuple(children[1])


def _read_expected_keys_bounded(
  *,
  batches: Sequence[ExpectedBarKeyBatch],
  connection: _InfluxConnection | None,
  page_rows: int,
  cancelled: threading.Event | None = None,
  budget: ReadbackBudget,
  depth: int = 0,
) -> dict[str, int]:
  _check_readback_cancelled(cancelled)
  budget.enter(depth)
  try:
    if len(batches) == 1:
      return _read_expected_key_batch_once(
        batch=batches[0],
        connection=connection,
        page_rows=page_rows,
        cancelled=cancelled,
        budget=budget,
      )
    return _read_expected_key_group_once(
      batches=batches,
      connection=connection,
      page_rows=page_rows,
      cancelled=cancelled,
      budget=budget,
    )
  except MarketDataPersistenceCapacityError as exc:
    budget.capacity_diagnostic = {
      **exc.diagnostic,
      "depth": depth,
      "nodes": budget.nodes,
      "ranges": [
        {
          "code": b.code,
          "period": b.period,
          "first_key": b.keys[0],
          "last_key": b.keys[-1],
          "keys": len(b.keys),
        }
        for b in batches
      ],
    }
    if budget.first_capacity_diagnostic is None:
      budget.first_capacity_diagnostic = budget.capacity_diagnostic
    children = _split_expected_keys(batches)
    if children is None:
      budget.block()
    total = {"records_verified": 0, "existing_rows_observed": 0}
    for child in children:
      result = _read_expected_keys_bounded(
        batches=child,
        connection=connection,
        page_rows=page_rows,
        cancelled=cancelled,
        budget=budget,
        depth=depth + 1,
      )
      for name in total:
        total[name] += result[name]
    return total


def _read_expected_key_batch_bounded(
  *,
  batch: ExpectedBarKeyBatch,
  **kwargs: Any,
) -> dict[str, int]:
  return _read_expected_keys_bounded(batches=(batch,), **kwargs)


def _read_group_once(
  *,
  expected: dict[str, Any],
  start_ms: int,
  end_exclusive_ms: int,
  connection: _InfluxConnection | None,
  page_rows: int,
  cancelled: threading.Event | None = None,
  budget: ReadbackBudget | None = None,
) -> dict[str, Any]:
  if page_rows < 1 or page_rows > MARKET_DATA_READBACK_PAGE_ROWS:
    raise ValueError(
      f"page_rows must be between 1 and {MARKET_DATA_READBACK_PAGE_ROWS}"
    )
  resolved_connection = connection or get_timeseries_connection()
  if resolved_connection is None:
    raise MarketDataPersistenceQueryError(
      "InfluxDB is unavailable for market-data persistence verification"
    )

  code = str(expected["code"])
  period = str(expected["period"])
  required_columns = {
    "time",
    *(
      (HISTORICAL_TICK_SOURCE_TIME_FIELD, HISTORICAL_TICK_ORDINAL_FIELD)
      if period == "tick"
      else ()
    ),
  }
  start = _utc_datetime_from_epoch_ms(start_ms)
  end = _utc_datetime_from_epoch_ms(end_exclusive_ms)
  maximum_rows = int(expected["row_count"]) + 1
  digest = hashlib.sha256()
  row_count = 0
  min_time: int | None = None
  max_time: int | None = None
  after: datetime | None = None

  budget = budget or ReadbackBudget()
  sql = ""
  try:
    with resolved_connection.get_client(timeout=budget.remaining()) as client:
      while row_count < maximum_rows:
        limit = min(page_rows, maximum_rows - row_count)
        sql = _query_page_sql(
          period=period,
          start=start,
          end=end,
          after=after,
          limit=limit,
        )
        _check_readback_cancelled(cancelled)
        with market_data_stage("read_query_open"):
          reader = client.query(
            query=sql,
            language="sql",
            mode="reader",
            query_parameters={"stock_code": code, "period": period},
            timeout=budget.remaining(),
          )
        if reader is None:
          raise MarketDataPersistenceQueryError(
            f"Influx read-back returned no reader for {code}/{period}"
          )
        page_count = 0
        try:
          schema_names = set(reader.schema.names)
          missing_columns = required_columns - schema_names
          if missing_columns:
            raise MarketDataPersistenceQueryError(
              "Influx read-back is missing required columns for "
              f"{code}/{period}: {sorted(missing_columns)}"
            )
          for batch in reader:
            _check_readback_cancelled(cancelled)
            budget.remaining()
            if page_count + batch.num_rows > limit:
              raise MarketDataPersistenceQueryError(
                f"Influx read-back exceeded its {limit}-row page bound"
              )
            columns = {
              name: batch.column(batch.schema.get_field_index(name))
              for name in required_columns
            }
            for index in range(batch.num_rows):
              storage_time = _utc_datetime(columns["time"][index].as_py())
              if after is not None and storage_time <= after:
                raise MarketDataPersistenceQueryError(
                  f"Influx read-back did not advance for {code}/{period}"
                )
              if storage_time < start or storage_time >= end:
                raise MarketDataPersistenceQueryError(
                  f"Influx read-back escaped the request window for {code}/{period}"
                )

              ordinal: int | None = None
              if period == "tick":
                source_time_ms = _integer(
                  columns[HISTORICAL_TICK_SOURCE_TIME_FIELD][index].as_py(),
                  field=HISTORICAL_TICK_SOURCE_TIME_FIELD,
                )
                ordinal = _integer(
                  columns[HISTORICAL_TICK_ORDINAL_FIELD][index].as_py(),
                  field=HISTORICAL_TICK_ORDINAL_FIELD,
                )
                if not 0 <= ordinal < HISTORICAL_TICK_ORDINALS_PER_MILLISECOND:
                  raise MarketDataPersistenceMismatchError(
                    f"persisted tick ordinal is out of range for {code}"
                  )
                expected_storage_time = _utc_datetime_from_epoch_ms(
                  source_time_ms
                ) + timedelta(microseconds=ordinal)
                if storage_time != expected_storage_time:
                  raise MarketDataPersistenceMismatchError(
                    f"persisted tick storage key is inconsistent for {code}"
                  )
                source_time = source_time_ms
              else:
                if storage_time.microsecond % 1000:
                  raise MarketDataPersistenceMismatchError(
                    f"persisted kline time is not millisecond aligned for {code}/{period}"
                  )
                source_time = _epoch_millis(storage_time)

              key = historical_bar_key(
                code=code,
                period=period,
                time_ms=source_time,
                tick_ordinal=ordinal,
              )
              if row_count:
                digest.update(b"\n")
              digest.update(key.encode("utf-8"))
              row_count += 1
              page_count += 1
              min_time = source_time if min_time is None else min_time
              max_time = source_time
              after = storage_time
        finally:
          close = getattr(reader, "close", None)
          if callable(close):
            close()
        if page_count < limit:
          break
  except MarketDataPersistenceVerificationError:
    raise
  except Exception as exc:
    raise _query_failure(
      exc,
      sql,
      page_after=after.isoformat() if after else None,
      page_rows=page_rows,
    ) from None

  budget.remaining()
  return {
    "code": code,
    "period": period,
    "row_count": row_count,
    "min_time": min_time,
    "max_time": max_time,
    "key_sha256": digest.hexdigest(),
  }


def _read_empty_group_bounded(
  *,
  expected: dict[str, Any],
  start_ms: int,
  end_exclusive_ms: int,
  connection: _InfluxConnection | None,
  page_rows: int,
  budget: ReadbackBudget,
  cancelled: threading.Event | None = None,
  depth: int = 0,
) -> dict[str, Any]:
  _check_readback_cancelled(cancelled)
  budget.enter(depth)
  try:
    return _read_group_once(
      expected=expected,
      start_ms=start_ms,
      end_exclusive_ms=end_exclusive_ms,
      connection=connection,
      page_rows=page_rows,
      budget=budget,
      cancelled=cancelled,
    )
  except MarketDataPersistenceCapacityError as exc:
    budget.capacity_diagnostic = {
      **exc.diagnostic,
      "code": expected["code"],
      "period": expected["period"],
      "start_ms": start_ms,
      "end_exclusive_ms": end_exclusive_ms,
      "depth": depth,
      "nodes": budget.nodes,
    }
    if budget.first_capacity_diagnostic is None:
      budget.first_capacity_diagnostic = budget.capacity_diagnostic
    if end_exclusive_ms - start_ms <= 1:
      budget.block()
    middle = (start_ms + end_exclusive_ms) // 2
    for start, end in ((start_ms, middle), (middle, end_exclusive_ms)):
      result = _read_empty_group_bounded(
        expected=expected,
        start_ms=start,
        end_exclusive_ms=end,
        connection=connection,
        page_rows=page_rows,
        budget=budget,
        cancelled=cancelled,
        depth=depth + 1,
      )
      # This path only samples pre-existing data for an empty source summary.
      # Finding a row ends the sample, exactly as the unsplit one-row query does.
      if result["row_count"]:
        return result
    return result


async def verify_persisted_bar_summaries(
  *,
  code_summaries: Sequence[dict[str, Any]],
  expected_key_batches: AsyncIterable[ExpectedBarKeyBatch],
  start_ms: int,
  end_exclusive_ms: int,
  connection: _InfluxConnection | None = None,
  max_attempts: int = MARKET_DATA_READBACK_MAX_ATTEMPTS,
  retry_delays: Sequence[float] = MARKET_DATA_READBACK_RETRY_DELAYS_SECONDS,
  page_rows: int = MARKET_DATA_READBACK_PAGE_ROWS,
  sleep: Sleep = asyncio.sleep,
  progress: ReadbackProgress | None = None,
  concurrency: int = MARKET_DATA_READBACK_CONCURRENCY,
) -> dict[str, Any]:
  """Prove every uploaded key exists after merge through uncached reads.

  InfluxDB is shared by live persistence and post-close synchronization.  A
  request therefore owns the rows it uploads, not the entire requested time
  window.  Pre-existing points are accepted, while a missing uploaded key or
  a structurally invalid persisted key still fails closed.
  """

  if max_attempts < 1:
    raise ValueError("max_attempts must be positive")
  if (
    isinstance(concurrency, bool)
    or not isinstance(concurrency, int)
    or not 1 <= concurrency <= MARKET_DATA_READBACK_CONCURRENCY
  ):
    raise ValueError("read-back concurrency is outside the service budget")
  if len(retry_delays) != max_attempts - 1 or any(delay < 0 for delay in retry_delays):
    raise ValueError("retry_delays must contain one non-negative delay per retry")
  expected_summaries = [_expected_summary(summary) for summary in code_summaries]
  pairs = [(item["code"], item["period"]) for item in expected_summaries]
  if len(set(pairs)) != len(pairs):
    raise ValueError("market-data persistence summaries contain duplicate groups")

  summary_by_pair = {
    (str(item["code"]), str(item["period"])): item for item in expected_summaries
  }
  pair_order = {pair: index for index, pair in enumerate(pairs)}
  observed: dict[tuple[str, str], dict[str, Any]] = {
    pair: {
      "row_count": 0,
      "min_time": None,
      "max_time": None,
      "key_digest": hashlib.sha256(),
      "last_storage_time": None,
    }
    for pair in pairs
  }
  attempts_by_group: dict[str, int] = {}
  existing_rows_observed = 0
  last_pair_index = -1

  pending: list[ExpectedBarKeyBatch] = []

  async def verify_group(group: tuple[ExpectedBarKeyBatch, ...]) -> None:
    nonlocal existing_rows_observed
    if not group:
      return
    digest = _proof_key([(item.code, item.period, item.keys) for item in group])
    previous = await progress.readback_result(digest) if progress is not None else None
    if previous is not None:
      if previous["records_verified"] != sum(len(item.keys) for item in group):
        raise MarketDataPersistenceMismatchError(
          "read-back checkpoint key count mismatch"
        )
      existing_rows_observed += previous["existing_rows_observed"]
      for item in group:
        attempts_by_group[f"{item.code}/{item.period}"] = 0
      return
    budget = ReadbackBudget()
    for attempt in range(1, max_attempts + 1):
      try:
        if len(group) == 1:
          result = await _await_readback(
            _read_expected_key_batch_bounded,
            batch=group[0],
            connection=connection,
            page_rows=page_rows,
            budget=budget,
          )
        else:
          result = await _await_readback(
            _read_expected_keys_bounded,
            batches=tuple(group),
            connection=connection,
            page_rows=page_rows,
            budget=budget,
          )
      except MarketDataPersistenceBlockedError:
        raise
      except MarketDataPersistenceVerificationError:
        if attempt == max_attempts:
          raise
        await sleep(float(retry_delays[attempt - 1]))
      else:
        if progress is not None:
          await progress.confirm_readback(digest, result)
        existing_rows_observed += int(result["existing_rows_observed"])
        for item in group:
          group_name = f"{item.code}/{item.period}"
          attempts_by_group[group_name] = max(
            attempts_by_group.get(group_name, 0), attempt
          )
        return

  active: set[asyncio.Task[None]] = set()

  async def drain_one() -> None:
    if not active:
      return
    done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
    for task in done:
      # Keep failed tasks owned until finally retrieves and joins every worker.
      task.result()
      active.remove(task)

  async def flush_pending() -> None:
    if not pending:
      return
    group = tuple(pending)
    pending.clear()
    if group[0].period == "tick":
      # Tick ordinal verification keeps its existing serial execution.
      while active:
        await drain_one()
      await verify_group(group)
      return
    while len(active) >= concurrency:
      await drain_one()
    active.add(asyncio.create_task(verify_group(group)))

  try:
    async for batch in expected_key_batches:
      if not isinstance(batch, ExpectedBarKeyBatch):
        raise ValueError(
          "market-data persistence verifier received an invalid key batch"
        )
      pair = (str(batch.code), str(batch.period))
      expected_summary = summary_by_pair.get(pair)
      if expected_summary is None:
        raise ValueError(
          f"market-data persistence key batch has no summary: {pair[0]}/{pair[1]}"
        )
      current_pair_index = pair_order[pair]
      if current_pair_index < last_pair_index:
        raise ValueError("market-data persistence key batches are out of group order")
      last_pair_index = current_pair_index
      state = observed[pair]
      for source_time_ms, tick_ordinal in batch.keys:
        storage_time = _storage_time_for_key(
          period=pair[1],
          source_time_ms=source_time_ms,
          tick_ordinal=tick_ordinal,
        )
        if not start_ms <= source_time_ms < end_exclusive_ms:
          raise ValueError("market-data persistence key is outside the request window")
        previous_storage_time = state["last_storage_time"]
        if previous_storage_time is not None and storage_time <= previous_storage_time:
          raise ValueError("market-data persistence keys are unordered or duplicated")
        key = historical_bar_key(
          code=pair[0],
          period=pair[1],
          time_ms=source_time_ms,
          tick_ordinal=tick_ordinal,
        )
        if state["row_count"]:
          state["key_digest"].update(b"\n")
        state["key_digest"].update(key.encode("utf-8"))
        state["row_count"] += 1
        state["min_time"] = (
          source_time_ms if state["min_time"] is None else state["min_time"]
        )
        state["max_time"] = source_time_ms
        state["last_storage_time"] = storage_time

      if pending and (
        batch.period == "tick"
        or pending[0].period != batch.period
        or any(item.code == batch.code for item in pending)
        or len(pending) >= MARKET_DATA_READBACK_GROUP_CODES
        or sum(len(item.keys) for item in pending) + len(batch.keys)
        > MARKET_DATA_READBACK_GROUP_KEYS
      ):
        await flush_pending()
      pending.append(batch)
      if batch.period == "tick":
        await flush_pending()
    await flush_pending()
    while active:
      await drain_one()
  finally:
    # An invalid source, exhausted retry or caller cancellation must not leave
    # readers running after the request releases its ingestion claim.
    for task in active:
      if not task.done():
        task.cancel()
    if active:
      await asyncio.shield(asyncio.gather(*active, return_exceptions=True))

  for expected in expected_summaries:
    pair = (str(expected["code"]), str(expected["period"]))
    state = observed[pair]
    actual_source_summary = {
      "code": pair[0],
      "period": pair[1],
      "row_count": int(state["row_count"]),
      "min_time": state["min_time"],
      "max_time": state["max_time"],
      "key_sha256": state["key_digest"].hexdigest(),
    }
    if actual_source_summary != expected:
      raise MarketDataPersistenceMismatchError(
        "persistence key stream does not match Agent summary for "
        f"{pair[0]}/{pair[1]}: expected={expected} "
        f"actual={actual_source_summary}"
      )
    if int(expected["row_count"]) != 0:
      continue

    digest = _proof_key({"empty": expected, "start": start_ms, "end": end_exclusive_ms})
    previous = await progress.readback_result(digest) if progress is not None else None
    if previous is not None:
      existing_rows_observed += previous["existing_rows_observed"]
      attempts_by_group[f"{pair[0]}/{pair[1]}"] = 0
      continue
    last_error = None
    budget = ReadbackBudget()
    for attempt in range(1, max_attempts + 1):
      try:
        existing = await _await_readback(
          _read_empty_group_bounded,
          expected=expected,
          start_ms=start_ms,
          end_exclusive_ms=end_exclusive_ms,
          connection=connection,
          page_rows=page_rows,
          budget=budget,
        )
      except MarketDataPersistenceBlockedError:
        raise
      except MarketDataPersistenceVerificationError as exc:
        last_error = exc
      else:
        if progress is not None:
          await progress.confirm_readback(
            digest,
            {
              "records_verified": 0,
              "existing_rows_observed": int(existing["row_count"]),
            },
          )
        existing_rows_observed += int(existing["row_count"])
        attempts_by_group[f"{pair[0]}/{pair[1]}"] = attempt
        break
      if attempt < max_attempts:
        await sleep(float(retry_delays[attempt - 1]))
    else:
      assert last_error is not None
      raise last_error

  records_verified = sum(int(item["row_count"]) for item in expected_summaries)
  return {
    "status": "verified",
    "verification_method": "influxdb3_arrow_uploaded_key_subset_v2",
    "records_verified": records_verified,
    "groups_verified": len(expected_summaries),
    "attempts_by_group": attempts_by_group,
    "existing_rows_observed": existing_rows_observed,
    "code_summaries": expected_summaries,
  }
