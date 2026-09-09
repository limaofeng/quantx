"""One bounded Flight query for a small universe of latest local daily bars."""

import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from quantx_contracts.daily_snapshot_read import DailySnapshot, DailySnapshotResult

from quantx_infrastructure.database.timeseries import get_timeseries_connection
from quantx_infrastructure.services.local_history_reader import HistoryReadInvalid


def read_latest_daily(connection, request):
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
  sql = (
    f"SELECT {columns} FROM kline_1d WHERE period = '1d' "
    f"AND stock_code IN ({codes}) "
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
