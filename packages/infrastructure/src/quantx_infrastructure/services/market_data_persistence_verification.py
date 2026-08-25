"""Bounded InfluxDB read-back verification for merged historical data."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterable, Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from quantx_contracts import (
  HISTORICAL_TICK_ORDINAL_FIELD,
  HISTORICAL_TICK_ORDINALS_PER_MILLISECOND,
  HISTORICAL_TICK_SOURCE_TIME_FIELD,
  historical_bar_key,
)

from quantx_infrastructure.database.timeseries import get_timeseries_connection

MARKET_DATA_READBACK_PAGE_ROWS = 2000
MARKET_DATA_READBACK_MAX_ATTEMPTS = 4
MARKET_DATA_READBACK_RETRY_DELAYS_SECONDS = (0.25, 0.75, 1.5)

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
  def get_client(self) -> _InfluxClientContext: ...


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
  return (
    (delta.days * 86_400 + delta.seconds) * 1000
    + delta.microseconds // 1000
  )


def _sql_timestamp(value: datetime) -> str:
  return value.astimezone(timezone.utc).isoformat(
    timespec="microseconds"
  ).replace("+00:00", "Z")


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
  after_clause = (
    f"AND time > '{_sql_timestamp(after)}' " if after is not None else ""
  )
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

  try:
    with resolved_connection.get_client() as client:
      while expected_index < len(expected):
        sql = _query_page_sql(
          period=period,
          start=start,
          end=end,
          after=after,
          limit=page_rows,
        )
        reader = client.query(
          query=sql,
          language="sql",
          mode="reader",
          query_parameters={"stock_code": code, "period": period},
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
    raise MarketDataPersistenceQueryError(
      f"Influx read-back query failed for {code}/{period}: {exc}"
    ) from exc

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


def _read_group_once(
  *,
  expected: dict[str, Any],
  start_ms: int,
  end_exclusive_ms: int,
  connection: _InfluxConnection | None,
  page_rows: int,
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

  try:
    with resolved_connection.get_client() as client:
      while row_count < maximum_rows:
        limit = min(page_rows, maximum_rows - row_count)
        sql = _query_page_sql(
          period=period,
          start=start,
          end=end,
          after=after,
          limit=limit,
        )
        reader = client.query(
          query=sql,
          language="sql",
          mode="reader",
          query_parameters={"stock_code": code, "period": period},
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
    raise MarketDataPersistenceQueryError(
      f"Influx read-back query failed for {code}/{period}: {exc}"
    ) from exc

  return {
    "code": code,
    "period": period,
    "row_count": row_count,
    "min_time": min_time,
    "max_time": max_time,
    "key_sha256": digest.hexdigest(),
  }


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
) -> dict[str, Any]:
  """Prove every uploaded key exists after merge through uncached reads.

  InfluxDB is shared by live persistence and post-close synchronization.  A
  request therefore owns the rows it uploads, not the entire requested time
  window.  Pre-existing points are accepted, while a missing uploaded key or
  a structurally invalid persisted key still fails closed.
  """

  if max_attempts < 1:
    raise ValueError("max_attempts must be positive")
  if len(retry_delays) != max_attempts - 1 or any(
    delay < 0 for delay in retry_delays
  ):
    raise ValueError("retry_delays must contain one non-negative delay per retry")
  expected_summaries = [_expected_summary(summary) for summary in code_summaries]
  pairs = [(item["code"], item["period"]) for item in expected_summaries]
  if len(set(pairs)) != len(pairs):
    raise ValueError("market-data persistence summaries contain duplicate groups")

  summary_by_pair = {
    (str(item["code"]), str(item["period"])): item
    for item in expected_summaries
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

  async for batch in expected_key_batches:
    if not isinstance(batch, ExpectedBarKeyBatch):
      raise ValueError("market-data persistence verifier received an invalid key batch")
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

    last_error: MarketDataPersistenceVerificationError | None = None
    for attempt in range(1, max_attempts + 1):
      try:
        result = await asyncio.to_thread(
          _read_expected_key_batch_once,
          batch=batch,
          connection=connection,
          page_rows=page_rows,
        )
      except MarketDataPersistenceVerificationError as exc:
        last_error = exc
      else:
        existing_rows_observed += int(result["existing_rows_observed"])
        group_name = f"{pair[0]}/{pair[1]}"
        attempts_by_group[group_name] = max(
          attempt,
          attempts_by_group.get(group_name, 0),
        )
        break
      if attempt < max_attempts:
        await sleep(float(retry_delays[attempt - 1]))
    else:
      assert last_error is not None
      raise last_error

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

    last_error = None
    for attempt in range(1, max_attempts + 1):
      try:
        existing = await asyncio.to_thread(
          _read_group_once,
          expected=expected,
          start_ms=start_ms,
          end_exclusive_ms=end_exclusive_ms,
          connection=connection,
          page_rows=page_rows,
        )
      except MarketDataPersistenceVerificationError as exc:
        last_error = exc
      else:
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
