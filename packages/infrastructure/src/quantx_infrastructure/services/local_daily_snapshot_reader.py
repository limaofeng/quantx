"""One bounded Flight query for a small universe of latest local daily bars."""

import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from quantx_contracts.daily_snapshot_read import DailySnapshot, DailySnapshotResult

from quantx_infrastructure.database.timeseries import get_timeseries_connection
from quantx_infrastructure.services.local_history_reader import HistoryReadInvalid


def read_latest_daily(connection, request, *, published_versions=None):
  deadline = time.monotonic() + 10

  def remaining():
    seconds = deadline - time.monotonic()
    if seconds <= 0:
      raise TimeoutError("daily snapshot deadline exceeded")
    return seconds

  parameters = {f"code_{i}": code for i, code in enumerate(request.instruments)}
  codes = ", ".join(f"${name}" for name in parameters)
  columns = ", ".join(DailySnapshot.model_fields)
  # A 60-day inclusive window intersects at most 62 Shanghai calendar dates
  # even across different endpoint offsets. An extra row detects overflow.
  row_limit = len(request.instruments) * 62
  measurement, version_filter = "kline_1d", ""
  if published_versions is not None:
    start_day = request.start.astimezone(ZoneInfo("Asia/Shanghai")).date()
    end_day = request.end.astimezone(ZoneInfo("Asia/Shanghai")).date()
    if len(published_versions) > row_limit or any(
      code not in request.instruments
      or not start_day <= day <= end_day
      or not isinstance(version, str)
      or re.fullmatch(r"[0-9a-f]{64}", version) is None
      for (code, day), version in published_versions.items()
    ):
      raise HistoryReadInvalid("invalid published daily version scope")
    if not published_versions:
      return DailySnapshotResult(request=request, records=[])
    measurement = "kline_1d_versions"
    columns += ", storage_version"
    versions = {
      f"version_{i}": version for i, version in enumerate(published_versions.values())
    }
    parameters.update(versions)
    version_filter = (
      f"AND storage_version IN ({', '.join('$' + name for name in versions)}) "
    )
  sql = (
    f"SELECT {columns} FROM {measurement} WHERE period = '1d' "
    f"AND stock_code IN ({codes}) "
    f"{version_filter}"
    f"AND time >= '{request.start.astimezone(timezone.utc).isoformat()}' "
    f"AND time <= '{request.end.astimezone(timezone.utc).isoformat()}' "
    f"ORDER BY stock_code ASC, time ASC LIMIT {row_limit + 1}"
  )
  connection = connection or get_timeseries_connection()
  seen = set()
  latest = {}
  previous = None
  rows_seen = bytes_seen = 0
  with connection.get_client(timeout=remaining()) as client:
    reader = client.query(
      query=sql,
      language="sql",
      mode="reader",
      query_parameters=parameters,
      timeout=remaining(),
    )
    if reader is None:
      raise HistoryReadInvalid("daily snapshot reader missing")
    try:
      for batch in reader:
        remaining()
        rows_seen += batch.num_rows
        bytes_seen += batch.nbytes
        if rows_seen > row_limit or bytes_seen > 2 * 1024 * 1024:
          raise HistoryReadInvalid("daily snapshot result budget exceeded")
        for raw in batch.to_pylist():
          stamp = raw.get("time")
          if not isinstance(stamp, datetime) or getattr(stamp, "nanosecond", 0):
            raise HistoryReadInvalid("daily snapshot timestamp is not reversible")
          raw["time"] = (
            stamp.replace(tzinfo=timezone.utc)
            if stamp.tzinfo is None
            else stamp.astimezone(timezone.utc)
          )
          if published_versions is not None:
            key = (
              raw.get("stock_code"),
              raw["time"].astimezone(ZoneInfo("Asia/Shanghai")).date(),
            )
            if (
              key not in published_versions
              or raw.pop("storage_version", None) != published_versions[key]
            ):
              raise HistoryReadInvalid("daily snapshot returned an unpublished version")
          row = DailySnapshot.model_validate(raw)
          key = (row.stock_code, row.time)
          day_key = (
            row.stock_code,
            row.time.astimezone(ZoneInfo("Asia/Shanghai")).date(),
          )
          if (
            row.stock_code not in request.instruments
            or not request.start <= row.time <= request.end
            or previous is not None
            and key <= previous
            or day_key in seen
          ):
            raise HistoryReadInvalid(
              "daily snapshot rows are duplicate or outside scope"
            )
          seen.add(day_key)
          previous = key
          latest[row.stock_code] = row
    finally:
      reader.close()
  remaining()
  return DailySnapshotResult(
    request=request,
    records=[latest[code] for code in request.instruments if code in latest],
  )
