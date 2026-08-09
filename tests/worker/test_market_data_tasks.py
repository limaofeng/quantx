import pytest
from prefect.states import Failed
from quantx_infrastructure.database.timeseries_connection import WriteError
from quantx_worker.prefector.tasks.market_data_tasks import (
  _should_retry_market_data_save,
  save_market_data,
)


@pytest.mark.parametrize(
  "server_message",
  [
    "another process has written to the WAL ahead of this one",
    "wal is shutdown",
  ],
)
def test_save_task_does_not_retry_fatal_influx_wal_failure(server_message):
  state = Failed(data=WriteError(f"write failed: {server_message}"))

  assert not _should_retry_market_data_save(None, None, state)
  assert save_market_data.retry_condition_fn is _should_retry_market_data_save


def test_save_task_still_retries_transient_influx_failure():
  state = Failed(data=WriteError("write failed: connection timed out"))

  assert _should_retry_market_data_save(None, None, state)
