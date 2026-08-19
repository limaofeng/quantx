import pandas as pd
import pytest
from prefect.states import Failed
from quantx_contracts import (
  HISTORICAL_TICK_ORDINAL_FIELD,
  HISTORICAL_TICK_SOURCE_TIME_FIELD,
)
from quantx_infrastructure.database.timeseries_connection import WriteError
from quantx_worker.prefector.tasks import market_data_tasks
from quantx_worker.prefector.tasks.market_data_tasks import (
  _should_retry_market_data_save,
  preprocess_market_data,
  save_market_data,
)


class _Logger:
  def info(self, *_args, **_kwargs):
    return None


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


def test_preprocess_tick_uses_reversible_microsecond_storage_time(monkeypatch):
  monkeypatch.setattr(market_data_tasks, "get_run_logger", _Logger)
  source_time = 1_700_000_000_123
  frame = pd.DataFrame(
    [
      {
        "time": source_time,
        HISTORICAL_TICK_ORDINAL_FIELD: 0,
        "lastPrice": 50.101,
        "open": 50.0,
        "high": 50.2,
        "low": 49.9,
        "lastClose": 49.8,
        "settlementPrice": 0,
        "lastSettlementPrice": 0,
        "volume": 100,
        "amount": 5010.1,
        "pvolume": 100,
        "tickvol": 5,
        "stockStatus": 0,
        "openInt": 0,
        "transactionNum": 10,
        "askPrice": [50.2],
        "bidPrice": [50.1],
        "askVol": [100],
        "bidVol": [200],
      },
      {
        "time": source_time,
        HISTORICAL_TICK_ORDINAL_FIELD: 1,
        "lastPrice": 50.102,
        "open": 50.0,
        "high": 50.2,
        "low": 49.9,
        "lastClose": 49.8,
        "settlementPrice": 0,
        "lastSettlementPrice": 0,
        "volume": 101,
        "amount": 5060.2,
        "pvolume": 101,
        "tickvol": 1,
        "stockStatus": 0,
        "openInt": 0,
        "transactionNum": 11,
        "askPrice": [50.2],
        "bidPrice": [50.1],
        "askVol": [100],
        "bidVol": [200],
      },
    ]
  )

  result = preprocess_market_data("tick", {"601318.SH": frame})

  assert result[HISTORICAL_TICK_SOURCE_TIME_FIELD].tolist() == [
    source_time,
    source_time,
  ]
  assert result[HISTORICAL_TICK_SOURCE_TIME_FIELD].dtype == "int64"
  assert result[HISTORICAL_TICK_ORDINAL_FIELD].tolist() == [0, 1]
  assert result[HISTORICAL_TICK_ORDINAL_FIELD].dtype == "int64"
  assert result["time"].iloc[1] - result["time"].iloc[0] == pd.Timedelta(microseconds=1)
  assert str(result["time"].dt.tz) == "Asia/Shanghai"
  reconstructed = result["time"].dt.tz_convert("UTC").astype("int64") // 1_000_000
  assert reconstructed.tolist() == [source_time, source_time]


def test_preprocess_non_tick_time_is_unchanged(monkeypatch):
  monkeypatch.setattr(market_data_tasks, "get_run_logger", _Logger)
  source_time = 1_700_000_000_123
  frame = pd.DataFrame(
    [
      {
        "time": source_time,
        "open": 10,
        "high": 11,
        "low": 9,
        "close": 10.5,
        "preClose": 9.8,
        "settelementPrice": 0,
        "volume": 100,
        "amount": 1050,
        "openInterest": 0,
        "suspendFlag": 0,
      }
    ]
  )

  result = preprocess_market_data("1d", {"600000.SH": frame})

  assert result["time"].iloc[0] == pd.Timestamp(
    source_time, unit="ms", tz="UTC"
  ).tz_convert("Asia/Shanghai")
  assert HISTORICAL_TICK_SOURCE_TIME_FIELD not in result
  assert HISTORICAL_TICK_ORDINAL_FIELD not in result
