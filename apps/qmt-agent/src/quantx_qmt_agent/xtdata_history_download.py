"""Event-driven completion for the audited XTData native history interface.

The vendor Python wrapper sleeps for ten seconds on one native return branch.
Call that same native interface on the existing client; do not patch the SDK,
detach a download thread, or consider the submission return value completion.
The parent historical worker still enforces its hard 30-second unit deadline.
"""

import hashlib
import threading
import time
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from quantx_qmt_agent.history_timing import record_history_timing

AUDITED_XTDATA_SHA256 = (
  "a6e53353a33f0388a57a9f99c345db30021bcb5583b2340e3e6a3f1c89519bb7"
)
HISTORY_COMPLETION_TIMEOUT_SECONDS = 25.0


class HistoryDownloadError(RuntimeError):
  """A sanitized, terminal native history failure."""


@lru_cache(maxsize=1)
def _verify_sdk_file(path: str) -> None:
  if hashlib.sha256(Path(path).read_bytes()).hexdigest() != AUDITED_XTDATA_SHA256:
    raise HistoryDownloadError("UNSUPPORTED_XTDATA_HISTORY_SDK")


def download_history(
  sdk: Any,
  client: Any,
  stock_list: list[str],
  period: str,
  *,
  start_time: str = "",
  end_time: str = "",
  callback: Callable | None = None,
  incrementally: bool | None = True,
  timeout: float = HISTORY_COMPLETION_TIMEOUT_SECONDS,
) -> dict[str, Any]:
  _verify_sdk_file(sdk.__file__)
  if timeout <= 0:
    raise ValueError("history timeout must be positive")
  if not client.is_connected():
    raise HistoryDownloadError("XTDATA_HISTORY_DISCONNECTED")
  if incrementally is None:
    incrementally = not bool(start_time)
  if isinstance(start_time, datetime):
    start_time = start_time.strftime("%Y%m%d%H%M%S")
  if isinstance(end_time, datetime):
    end_time = end_time.strftime("%Y%m%d%H%M%S")
  spec_period, meta_id, period_num = sdk._validate_period(period)
  params = {"incrementally": incrementally}
  if meta_id > 0:
    params.update(metaid=meta_id, period=period_num)
  encoded = sdk._BSON_.BSON.encode(params)
  completed = threading.Event()
  lock = threading.Lock()
  result: dict[str, Any] = {}
  error = ""
  closed = False
  callback_at: float | None = None
  started = time.monotonic()
  deadline = started + timeout

  def on_progress(data: Any) -> bool:
    nonlocal error, callback_at
    received_at = time.monotonic()
    with lock:
      if closed or completed.is_set():
        return True
      try:
        finished, total = data["finished"], data["total"]
        if type(finished) is not int or type(total) is not int:
          raise HistoryDownloadError("XTDATA_HISTORY_INVALID_PROGRESS")
        if total < 0:
          raise HistoryDownloadError("XTDATA_HISTORY_DOWNLOAD_FAILED")
        if finished < 0 or finished > total:
          raise HistoryDownloadError("XTDATA_HISTORY_INVALID_PROGRESS")
        done = finished == total
        if done and "result" in data:
          decoded = sdk._BSON_.BSON.decode(data["result"])
          if not isinstance(decoded, dict) or set(decoded) - set(stock_list):
            raise HistoryDownloadError("XTDATA_HISTORY_INVALID_RESULT")
          for stock, info in decoded.items():
            result[stock] = {
              **info,
              "start_time": datetime.fromtimestamp(info["start_time"] / 1000),
              "end_time": datetime.fromtimestamp(info["end_time"] / 1000),
            }
        if callback:
          try:
            callback(data)
          except Exception:
            raise HistoryDownloadError("XTDATA_HISTORY_CALLBACK_FAILED") from None
        if done:
          callback_at = received_at
          completed.set()
        return done
      except Exception as exc:
        # Native callback machinery can swallow Python exceptions. Communicate
        # failure explicitly to the caller, without retaining vendor messages.
        error = (
          str(exc)
          if isinstance(exc, HistoryDownloadError)
          else "XTDATA_HISTORY_CALLBACK_FAILED"
        )
        completed.set()
        return True

  try:
    client.supply_history_data2(
      stock_list, spec_period, start_time, end_time, encoded, on_progress
    )
    record_history_timing("native_submit_return", started)
    while True:
      if not client.is_connected():
        raise HistoryDownloadError("XTDATA_HISTORY_DISCONNECTED")
      if time.monotonic() >= deadline:
        raise HistoryDownloadError("XTDATA_HISTORY_TIMEOUT")
      if completed.is_set():
        if error:
          raise HistoryDownloadError(error)
        break
      completed.wait(min(0.1, max(0, deadline - time.monotonic())))
    record_history_timing(
      "download_complete",
      started,
      callback_ms=(callback_at - started) * 1000,
      callback_to_return_ms=(time.monotonic() - callback_at) * 1000,
    )
    return result
  except BaseException:
    # Cancel on failure before returning control. A blocked/failed cancel is
    # handled by the existing parent supervisor, never by a second downloader.
    with lock:
      closed = True
    record_history_timing("download_failed", started)
    try:
      client.stop_supply_history_data2()
    except Exception:
      raise HistoryDownloadError("XTDATA_HISTORY_CANCEL_FAILED") from None
    raise
  finally:
    with lock:
      closed = True
