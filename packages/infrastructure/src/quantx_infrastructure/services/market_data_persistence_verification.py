"""Bounded InfluxDB read-back verification for archived market data."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable, Sequence
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
  """Persisted keys do not match the Agent's authoritative summary."""


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
  start_ms: int,
  end_exclusive_ms: int,
  connection: _InfluxConnection | None = None,
  max_attempts: int = MARKET_DATA_READBACK_MAX_ATTEMPTS,
  retry_delays: Sequence[float] = MARKET_DATA_READBACK_RETRY_DELAYS_SECONDS,
  page_rows: int = MARKET_DATA_READBACK_PAGE_ROWS,
  sleep: Sleep = asyncio.sleep,
) -> dict[str, Any]:
  """Prove every requested ``(code, period)`` key set through uncached reads."""

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

  verified: list[dict[str, Any]] = []
  attempts_by_group: dict[str, int] = {}
  for expected in expected_summaries:
    last_error: MarketDataPersistenceVerificationError | None = None
    for attempt in range(1, max_attempts + 1):
      try:
        actual = await asyncio.to_thread(
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
        if actual == expected:
          verified.append(actual)
          attempts_by_group[f"{expected['code']}/{expected['period']}"] = attempt
          break
        last_error = MarketDataPersistenceMismatchError(
          "Influx read-back does not match Agent summary for "
          f"{expected['code']}/{expected['period']}: "
          f"expected={expected} actual={actual}"
        )
      if attempt < max_attempts:
        await sleep(float(retry_delays[attempt - 1]))
    else:
      assert last_error is not None
      raise last_error

  records_verified = sum(int(item["row_count"]) for item in verified)
  return {
    "status": "verified",
    "verification_method": "influxdb3_arrow_keyset_v1",
    "records_verified": records_verified,
    "groups_verified": len(verified),
    "attempts_by_group": attempts_by_group,
    "code_summaries": verified,
  }
