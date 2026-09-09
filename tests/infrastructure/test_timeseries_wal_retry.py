from types import SimpleNamespace

import pytest
from quantx_infrastructure.database import timeseries_operations
from quantx_infrastructure.database.timeseries_connection import (
  NonRetryableWriteError,
  is_fatal_wal_error,
)
from quantx_infrastructure.database.timeseries_operations import (
  TimeSeriesOperations,
  single_write_attempt,
)


def test_durable_writer_owns_retry_budget_without_changing_other_callers(monkeypatch):
  connection = SimpleNamespace(max_retries=3, retry_delay=1, _stats={"errors": 0})
  operations = TimeSeriesOperations(connection)
  calls, delays = [], []

  def fail():
    calls.append(1)
    raise ConnectionError("temporary failure")

  monkeypatch.setattr(timeseries_operations.time, "sleep", delays.append)
  with single_write_attempt(), pytest.raises(ConnectionError):
    operations._execute_with_retry(fail)
  assert len(calls) == 1 and delays == []
  with pytest.raises(ConnectionError):
    operations._execute_with_retry(fail)
  assert len(calls) == 5 and len(delays) == 3


@pytest.mark.parametrize(
  "server_message",
  [
    "another process has written to the WAL ahead of this one",
    "wal is shutdown",
  ],
)
def test_fatal_wal_error_stops_internal_retry_immediately(
  monkeypatch,
  server_message,
):
  connection = SimpleNamespace(
    max_retries=3,
    retry_delay=10,
    _stats={"errors": 0},
  )
  operations = TimeSeriesOperations(connection)
  attempts = 0

  def fail():
    nonlocal attempts
    attempts += 1
    raise RuntimeError(f"InfluxDB write rejected: {server_message}")

  def unexpected_sleep(_delay):
    pytest.fail("fatal WAL failures must not enter retry backoff")

  monkeypatch.setattr(timeseries_operations.time, "sleep", unexpected_sleep)

  with pytest.raises(NonRetryableWriteError, match="不可重试") as raised:
    operations._execute_with_retry(fail)

  assert attempts == 1
  assert connection._stats["errors"] == 1
  assert is_fatal_wal_error(raised.value)


def test_fatal_wal_detection_walks_wrapped_exception_chain():
  server_error = RuntimeError("WAL IS SHUTDOWN")
  try:
    raise server_error
  except RuntimeError as exc:
    wrapped = RuntimeError("generic client write failure")
    wrapped.__cause__ = exc

  assert is_fatal_wal_error(wrapped)
  assert is_fatal_wal_error(NonRetryableWriteError("sanitized fatal write"))
  assert not is_fatal_wal_error(RuntimeError("connection timed out"))


def test_transient_failure_keeps_existing_retry_policy(monkeypatch):
  connection = SimpleNamespace(
    max_retries=3,
    retry_delay=1,
    _stats={"errors": 0},
  )
  operations = TimeSeriesOperations(connection)
  attempts = 0
  delays = []

  def fail():
    nonlocal attempts
    attempts += 1
    raise RuntimeError("connection timed out")

  monkeypatch.setattr(timeseries_operations.time, "sleep", delays.append)

  with pytest.raises(RuntimeError, match="timed out"):
    operations._execute_with_retry(fail)

  assert attempts == 4
  assert delays == [1, 2, 4]
  assert connection._stats["errors"] == 1
