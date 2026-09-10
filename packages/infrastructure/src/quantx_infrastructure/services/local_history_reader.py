"""Bounded local history queries with no collection or remote fallback."""

from __future__ import annotations

import asyncio
import math
import re
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from quantx_contracts.market_data_service import HistoryPage, HistoryRead

from quantx_infrastructure.database.timeseries import get_timeseries_connection
from quantx_infrastructure.services.historical_market_data_service import (
  _strict_source_identity,
  _validate_storage_time,
)


class HistoryReadBusy(RuntimeError):
  pass


class HistoryReadInvalid(RuntimeError):
  pass


class LocalHistoryReader:
  """One active local read per API process; cancellation joins its SDK thread."""

  def __init__(self, connection=None, *, session_factory=None):
    self.connection = connection
    self.session_factory = session_factory
    self._slot = asyncio.Lock()

  async def read(self, request: HistoryRead) -> HistoryPage:
    if request.period == "1m" and self.session_factory is not None:
      from .realtime_archive_reader import read_archive_history

      return await read_archive_history(
        self, request, session_factory=self.session_factory, development=False
      )
    if self.session_factory is not None:
      from .native_bar_publication import resolve_native_bar_version

      if self._slot.locked():
        raise HistoryReadBusy("local history query capacity exhausted")
      async with self._slot:
        async with asyncio.timeout(3), self.session_factory() as db:
          version = await resolve_native_bar_version(db, request)
        if version is None:
          raise HistoryReadInvalid("HISTORY_STORAGE_VERSION_UNAVAILABLE")
        return await self._read_thread(
          lambda value: self._read(value, storage_version=version.storage_version),
          request,
        )
    return await self._run_read(self._read, request)

  async def read_published(
    self, request: HistoryRead, *, session_factory
  ) -> HistoryPage:
    from quantx_contracts.data_exchange import HistoryPartitionRequest

    from .development_bar_publication import resolve_published_bar_version

    if self._slot.locked():
      raise HistoryReadBusy("local history query capacity exhausted")
    async with self._slot:
      async with asyncio.timeout(3), session_factory() as db:
        version = await resolve_published_bar_version(
          db,
          HistoryPartitionRequest(
            instrument=request.instrument,
            period=request.period,
            trading_date=request.trading_date,
          ),
        )
      if version is None:
        raise HistoryReadInvalid("HISTORY_STORAGE_VERSION_UNAVAILABLE")
      return await self._read_thread(
        lambda value: self._read(value, storage_version=version["storage_version"]),
        request,
      )

  async def read_latest_daily(self, request):
    from .local_daily_snapshot_reader import read_latest_daily

    return await self._run_read(
      lambda value: read_latest_daily(self.connection, value), request
    )

  async def read_latest_published_daily(self, request, *, session_factory):
    from .development_bar_publication import resolve_published_daily_versions
    from .local_daily_snapshot_reader import read_latest_daily

    if self._slot.locked():
      raise HistoryReadBusy("local history query capacity exhausted")
    async with self._slot:
      async with asyncio.timeout(3), session_factory() as db:
        versions = await resolve_published_daily_versions(db, request)
      return await self._read_thread(
        lambda value: read_latest_daily(
          self.connection, value, published_versions=versions
        ),
        request,
      )

  async def _run_read(self, read, request):
    if self._slot.locked():
      raise HistoryReadBusy("local history query capacity exhausted")
    async with self._slot:
      return await self._read_thread(read, request)

  async def _read_thread(self, read, request):
    task = asyncio.create_task(asyncio.to_thread(read, request))
    try:
      return await asyncio.shield(task)
    except asyncio.CancelledError:
      # Releasing the slot while Flight still runs would bypass the budget.
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

  def _read(
    self,
    request: HistoryRead,
    *,
    storage_version=None,
    archive_versions=None,
    excluded_minutes=None,
    budget=None,
  ) -> HistoryPage:
    if storage_version is not None and (
      not isinstance(storage_version, str)
      or re.fullmatch(r"[0-9a-f]{64}", storage_version) is None
    ):
      raise HistoryReadInvalid("invalid published storage version")
    budget = (
      budget if budget is not None else {"deadline": time.monotonic() + 10, "bytes": 0}
    )
    deadline = budget["deadline"]

    def remaining():
      seconds = deadline - time.monotonic()
      if seconds <= 0:
        raise TimeoutError("local history query deadline exceeded")
      return seconds

    start, end = request.bounds()
    if archive_versions is not None:
      if request.period != "1m" or storage_version is not None:
        raise HistoryReadInvalid("invalid realtime archive scope")
      if not archive_versions:
        return HistoryPage(records=[], next_after=None, exhausted=True)
      expected_minutes = sorted(
        minute
        for minute in archive_versions
        if request.after is None or minute > request.after
      )[: request.page_size]
      if not expected_minutes:
        return HistoryPage(records=[], next_after=None, exhausted=True)
    for minute in [*(archive_versions or {}), *(excluded_minutes or {})]:
      if (
        minute.tzinfo is None
        or not start <= minute < end
        or minute.second
        or minute.microsecond
      ):
        raise HistoryReadInvalid("invalid realtime archive minute")
    conditions = [
      "stock_code = $stock_code",
      "period = $period",
      f"time >= '{start.isoformat()}'",
      f"time < '{end.isoformat()}'",
    ]
    if request.after is not None:
      cursor = request.after.astimezone(timezone.utc).isoformat(timespec="microseconds")
      conditions.append(f"time > '{cursor}'")
    measurement = {"tick": "ticks", "1m": "kline_1m", "1d": "kline_1d"}[request.period]
    parameters = {"stock_code": request.instrument, "period": request.period}
    if storage_version is not None:
      measurement += "_versions"
      conditions.append("storage_version = $storage_version")
      parameters["storage_version"] = storage_version
    if archive_versions is not None:
      measurement += "_versions"
      placeholders = []
      for index, head in enumerate(archive_versions.values()):
        name = f"archive_version_{index}"
        parameters[name] = head.proof.storage_version
        placeholders.append("$" + name)
      conditions.append(f"storage_version IN ({','.join(placeholders)})")
    if excluded_minutes:
      values = ",".join(
        "'" + minute.astimezone(timezone.utc).isoformat() + "'"
        for minute in excluded_minutes
      )
      conditions.append(f"time NOT IN ({values})")
    sql = (
      f"SELECT * FROM {measurement} WHERE {' AND '.join(conditions)} "
      f"ORDER BY time ASC LIMIT {request.page_size}"
    )
    connection = self.connection or get_timeseries_connection()
    records = []
    previous = request.after
    with connection.get_client(timeout=remaining()) as client:
      reader = client.query(
        query=sql,
        language="sql",
        mode="reader",
        query_parameters=parameters,
        timeout=remaining(),
      )
      if reader is None:
        raise HistoryReadInvalid("history query returned no reader")
      try:
        for batch in reader:
          remaining()
          budget["bytes"] += batch.nbytes
          if (
            budget["bytes"] > 4 * 1024 * 1024
            or len(records) + batch.num_rows > request.page_size
          ):
            raise HistoryReadInvalid("history query exceeded its result budget")
          for row in batch.to_pylist():
            stamp = row.get("time")
            if not isinstance(stamp, datetime) or getattr(stamp, "nanosecond", 0):
              raise HistoryReadInvalid("history storage timestamp is not reversible")
            # Influx timestamps without a timezone are UTC, never local time.
            stamp = (
              stamp.replace(tzinfo=timezone.utc)
              if stamp.tzinfo is None
              else stamp.astimezone(timezone.utc)
            )
            if (
              not start <= stamp < end
              or (previous is not None and stamp <= previous)
              or row.get("stock_code") != request.instrument
              or row.get("period") != request.period
              or storage_version is not None
              and row.get("storage_version") != storage_version
            ):
              raise HistoryReadInvalid("history page is unordered or outside scope")
            row["time"] = stamp
            if excluded_minutes and stamp in excluded_minutes:
              raise HistoryReadInvalid("canonical query ignored archive exclusion")
            if archive_versions is not None:
              from .market_data_content_verification import _compare

              head = archive_versions.get(stamp)
              if (
                head is None or row.get("storage_version") != head.proof.storage_version
              ):
                raise HistoryReadInvalid("archive query returned an unselected version")
              _compare(
                {
                  **head.request.bar.model_dump(),
                  "time": stamp,
                  "stock_code": request.instrument,
                  "period": "1m",
                },
                row,
              )
            if request.period == "tick":
              tick = SimpleNamespace(**row)
              key = _strict_source_identity(tick)
              _validate_storage_time(tick, key)
              row["source_time_ms"], row["tick_ordinal"] = key
            # Nullable Influx numeric fields can be represented as NaN.
            row = {
              key: None
              if isinstance(value, float) and not math.isfinite(value)
              else value
              for key, value in row.items()
            }
            records.append(row)
            previous = stamp
      finally:
        reader.close()
    remaining()
    if (
      archive_versions is not None
      and [row["time"] for row in records] != expected_minutes
    ):
      raise HistoryReadInvalid("published archive rows are missing")
    return HistoryPage(
      records=records,
      next_after=previous if records else None,
      exhausted=not records,
    )


class PublishedHistoryReader(LocalHistoryReader):
  """The development API adapter reads only receipt-selected versions."""

  def __init__(self, session_factory, connection=None):
    super().__init__(connection, session_factory=session_factory)

  async def read(self, request):
    if request.period == "1m":
      from .realtime_archive_reader import read_archive_history

      return await read_archive_history(
        self, request, session_factory=self.session_factory, development=True
      )
    return await self.read_published(request, session_factory=self.session_factory)

  async def read_latest_daily(self, request):
    return await self.read_latest_published_daily(
      request, session_factory=self.session_factory
    )
