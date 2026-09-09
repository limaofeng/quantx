"""Bounded comparison of every non-null field owned by the historical writer."""

import asyncio
import hashlib
import json
import math
import re
import time
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from numbers import Real

from quantx_infrastructure.database.timeseries import get_timeseries_connection

from .market_data_persistence_verification import (
  MarketDataPersistenceBlockedError,
  MarketDataPersistenceCapacityError,
  MarketDataPersistenceMismatchError,
  MarketDataPersistenceQueryError,
  MarketDataPersistenceVerificationError,
  _query_failure,
)

CONTENT_BATCH_ROWS = 1000
CONTENT_BATCH_BYTES = 1024 * 1024
CONTENT_PAGE_ROWS = 1000
CONTENT_PAGE_BYTES = 4 * 1024 * 1024
CONTENT_MAX_PAGES = 1024
CONTENT_MAX_SCANNED_ROWS = 1_000_000
CONTENT_MAX_BYTES = 512 * 1024 * 1024
CONTENT_MAX_SECONDS = 60


@dataclass
class ContentBudget:
  storage_version: str | None = None
  deadline: float = field(
    default_factory=lambda: time.monotonic() + CONTENT_MAX_SECONDS
  )
  pages: int = 0
  rows: int = 0
  bytes: int = 0
  splits: int = 0

  def remaining(self):
    seconds = self.deadline - time.monotonic()
    if seconds <= 0:
      raise MarketDataPersistenceBlockedError("CONTENT_READBACK_TIME_BUDGET_EXHAUSTED")
    return min(10, seconds)


def _stamp(value):
  if not isinstance(value, datetime) or getattr(value, "nanosecond", 0):
    raise MarketDataPersistenceMismatchError("content readback timestamp is invalid")
  return (
    value.replace(tzinfo=timezone.utc)
    if value.tzinfo is None
    else value.astimezone(timezone.utc)
  )


def _present(value):
  return value is not None and not (isinstance(value, Real) and math.isnan(value))


def _canonical(row):
  return json.dumps(
    row, sort_keys=True, separators=(",", ":"), allow_nan=False
  ).encode()


def _compare(expected, actual):
  left, right = {}, {}
  for name, value in expected.items():
    if not _present(value):
      continue  # Nulls are omitted by the Influx writer, not authoritative clears.
    observed = actual.get(name)
    if name == "time":
      value, observed = _stamp(value).isoformat(), _stamp(observed).isoformat()
    elif isinstance(value, Real):
      if (
        isinstance(observed, bool)
        or not isinstance(observed, Real)
        or not math.isfinite(value)
        or not math.isfinite(observed)
        or value != observed
      ):
        raise MarketDataPersistenceMismatchError(
          f"content readback field mismatch: {name}"
        )
      observed = float(observed) if isinstance(value, float) else int(observed)
    if value != observed:
      raise MarketDataPersistenceMismatchError(
        f"content readback field mismatch: {name}"
      )
    left[name], right[name] = value, observed
  return left, right


def _query_batches(connection, query, code, period, budget):
  try:
    with connection.get_client(timeout=budget.remaining()) as client:
      reader = client.query(
        query=query,
        language="sql",
        mode="reader",
        query_parameters={
          "stock_code": code,
          "period": period,
          **(
            {"storage_version": budget.storage_version}
            if budget.storage_version
            else {}
          ),
        },
        timeout=budget.remaining(),
      )
      if reader is None:
        raise MarketDataPersistenceQueryError("content query returned no reader")
      try:
        yield from reader
      finally:
        reader.close()
  except MarketDataPersistenceVerificationError:
    raise
  except Exception as exc:
    raise _query_failure(exc, query, code=code, period=period) from None


def _read_batch(connection, expected, budget, source_hash, persisted_hash, depth=0):
  try:
    return _read_batch_once(connection, expected, budget, source_hash, persisted_hash)
  except MarketDataPersistenceCapacityError as exc:
    if len(expected) <= 1 or depth >= 8 or budget.splits >= 32:
      raise MarketDataPersistenceBlockedError(
        "DEPENDENCY_QUERY_CAPACITY_BLOCKED",
        {**exc.diagnostic, "proof_kind": "content"},
      ) from None
    budget.splits += 1
    split = len(expected) // 2
    left = _read_batch(
      connection, expected.iloc[:split], budget, source_hash, persisted_hash, depth + 1
    )
    right = _read_batch(
      connection, expected.iloc[split:], budget, left[2], left[3], depth + 1
    )
    return left[0] + right[0], left[1] + right[1], right[2], right[3]


def _read_batch_once(connection, expected, budget, source_hash, persisted_hash):
  source_hash, persisted_hash = source_hash.copy(), persisted_hash.copy()
  rows = expected.to_dict("records")
  if not rows or len(rows) > CONTENT_BATCH_ROWS:
    raise ValueError("invalid content verification batch")
  code, period = rows[0]["stock_code"], rows[0]["period"]
  measurement = {"tick": "ticks", "1m": "kline_1m", "1d": "kline_1d"}[period]
  if budget.storage_version:
    measurement += "_versions"
  columns = [
    name for name in expected.columns if any(_present(row[name]) for row in rows)
  ]
  if len(columns) > 64 or any(
    re.fullmatch(r"[a-z_][a-z_0-9]*", name) is None for name in columns
  ):
    raise ValueError("invalid content verification columns")
  start, end = _stamp(rows[0]["time"]), _stamp(rows[-1]["time"])
  previous_expected = None
  for row in rows:
    stamp = _stamp(row["time"])
    if (
      row["stock_code"] != code
      or row["period"] != period
      or previous_expected is not None
      and stamp <= previous_expected
    ):
      raise ValueError("unordered content verification batch")
    previous_expected = stamp
  if (end - start).total_seconds() >= 86400:
    raise ValueError("content verification batch crosses its time budget")
  after, index, fields_verified = None, 0, 0
  while index < len(rows):
    budget.remaining()
    if budget.pages >= CONTENT_MAX_PAGES:
      raise MarketDataPersistenceBlockedError("CONTENT_READBACK_PAGE_BUDGET_EXHAUSTED")
    budget.pages += 1
    cursor = f" AND time > '{after.isoformat()}'" if after is not None else ""
    query = (
      f"SELECT {','.join(columns)}{',storage_version' if budget.storage_version else ''} FROM {measurement} "
      "WHERE stock_code=$stock_code AND period=$period "
      + ("AND storage_version=$storage_version " if budget.storage_version else "")
      + f"AND time >= '{start.isoformat()}' AND time <= '{end.isoformat()}'{cursor} "
      f"ORDER BY time ASC LIMIT {CONTENT_PAGE_ROWS}"
    )
    page_rows = page_bytes = 0
    with closing(_query_batches(connection, query, code, period, budget)) as reader:
      for batch in reader:
        budget.remaining()
        page_rows += batch.num_rows
        page_bytes += batch.nbytes
        budget.rows += batch.num_rows
        budget.bytes += batch.nbytes
        if (
          page_rows > CONTENT_PAGE_ROWS
          or page_bytes > CONTENT_PAGE_BYTES
          or budget.rows > CONTENT_MAX_SCANNED_ROWS
          or budget.bytes > CONTENT_MAX_BYTES
        ):
          raise MarketDataPersistenceBlockedError(
            "CONTENT_READBACK_RESULT_BUDGET_EXHAUSTED"
          )
        for row in batch.to_pylist():
          stamp = _stamp(row.get("time"))
          if (
            row.get("stock_code") != code
            or row.get("period") != period
            or budget.storage_version is not None
            and row.get("storage_version") != budget.storage_version
            or not start <= stamp <= end
            or after is not None
            and stamp <= after
          ):
            raise MarketDataPersistenceMismatchError(
              "content query is unordered or outside scope"
            )
          after = stamp
          if index == len(rows):
            raise MarketDataPersistenceMismatchError("duplicate final content key")
          wanted = _stamp(rows[index]["time"])
          if stamp < wanted:
            continue  # Other source requests may legitimately own extra keys.
          if stamp > wanted:
            raise MarketDataPersistenceMismatchError("content key missing")
          left, right = _compare(rows[index], row)
          source_hash.update(_canonical(left) + b"\n")
          persisted_hash.update(_canonical(right) + b"\n")
          fields_verified += len(left) - 3
          index += 1
    if page_rows == 0:
      raise MarketDataPersistenceMismatchError("content key missing")
  return len(rows), fields_verified, source_hash, persisted_hash


async def verify_persisted_bar_content(
  expected_batches, *, connection=None, storage_version=None
):
  if storage_version is not None and (
    not isinstance(storage_version, str)
    or re.fullmatch(r"[0-9a-f]{64}", storage_version) is None
  ):
    raise ValueError("invalid storage version")
  budget = ContentBudget(storage_version=storage_version)
  source_hash, persisted_hash = hashlib.sha256(), hashlib.sha256()
  count = fields_verified = 0
  async for expected in expected_batches:
    connection = connection or get_timeseries_connection()
    task = asyncio.create_task(
      asyncio.to_thread(
        _read_batch,
        connection,
        expected,
        budget,
        source_hash,
        persisted_hash,
      )
    )
    try:
      records, verified, source_hash, persisted_hash = await asyncio.shield(task)
    except asyncio.CancelledError:
      while not task.done():
        try:
          await asyncio.shield(task)
        except asyncio.CancelledError:
          continue
        except Exception:
          break
      if not task.cancelled():
        task.exception()
      raise
    except MarketDataPersistenceVerificationError:
      raise
    except Exception as exc:
      raise MarketDataPersistenceQueryError("content readback query failed") from exc
    count += records
    fields_verified += verified
  budget.remaining()
  return {
    "schema_version": 1,
    "records_verified": count,
    "fields_verified": fields_verified,
    "source_sha256": source_hash.hexdigest(),
    "persisted_sha256": persisted_hash.hexdigest(),
  }
