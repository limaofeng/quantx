import threading
import time
from datetime import datetime
from types import SimpleNamespace

import pytest
from quantx_qmt_agent import xtdata_history_download as module
from quantx_qmt_agent.history_timing import history_unit


class NativeClient:
  def __init__(self, event=None, submission=True):
    self.event = event if event is not None else {"finished": 1, "total": 1}
    self.submission = submission
    self.connected = True
    self.stops = 0
    self.calls = []
    self.callback = None

  def is_connected(self):
    return self.connected

  def supply_history_data2(self, *args):
    self.calls.append(args[:-1])
    self.callback = args[-1]
    self.callback(self.event)
    return self.submission

  def stop_supply_history_data2(self):
    self.stops += 1


@pytest.fixture
def sdk(monkeypatch):
  monkeypatch.setattr(module, "_verify_sdk_file", lambda _path: None)
  return SimpleNamespace(
    __file__="audited-sdk.py",
    _validate_period=lambda p: (p, 3001, 60000),
    _BSON_=SimpleNamespace(
      BSON=SimpleNamespace(encode=lambda p: p, decode=lambda p: p)
    ),
  )


def download(sdk, client, **kwargs):
  return module.download_history(sdk, client, ["600000.SH"], "1m", **kwargs)


@pytest.mark.parametrize("submission", [False, True, 7, None])
def test_native_submission_value_does_not_change_completion(sdk, submission):
  client = NativeClient(
    {
      "finished": 1,
      "total": 1,
      "result": {
        "600000.SH": {"start_time": 1000, "end_time": 2000, "count": 241},
      },
    },
    submission,
  )
  result = download(
    sdk,
    client,
    start_time="20260907000000",
    end_time="20260907235959",
    incrementally=False,
  )
  assert result == {
    "600000.SH": {
      "start_time": datetime.fromtimestamp(1),
      "end_time": datetime.fromtimestamp(2),
      "count": 241,
    }
  }
  assert client.calls == [
    (
      ["600000.SH"],
      "1m",
      "20260907000000",
      "20260907235959",
      {"incrementally": False, "metaid": 3001, "period": 60000},
    )
  ]
  assert client.stops == 0


def test_async_completion_wakes_waiter_without_ten_second_poll(sdk):
  client = NativeClient({"finished": 0, "total": 1})

  def complete():
    while client.callback is None:
      time.sleep(0.001)
    time.sleep(0.02)
    client.callback({"finished": 1, "total": 1})

  worker = threading.Thread(target=complete)
  worker.start()
  started = time.monotonic()
  try:
    assert download(sdk, client, timeout=2) == {}
    assert time.monotonic() - started < 1
  finally:
    worker.join(timeout=2)


@pytest.mark.parametrize(
  "event,reason",
  [
    ({"finished": 0, "total": -1, "message": "secret"}, "DOWNLOAD_FAILED"),
    ({"finished": 2, "total": 1}, "INVALID_PROGRESS"),
    ({"finished": "1", "total": 1}, "INVALID_PROGRESS"),
    ({"finished": True, "total": 1}, "INVALID_PROGRESS"),
    ({"finished": -1, "total": 1}, "INVALID_PROGRESS"),
    ({}, "CALLBACK_FAILED"),
    ({"finished": 1, "total": 1, "result": []}, "INVALID_RESULT"),
    ({"finished": 1, "total": 1, "result": {"other": {}}}, "INVALID_RESULT"),
  ],
)
def test_invalid_or_failed_completion_cancels_instead_of_returning_empty(
  sdk, event, reason
):
  client = NativeClient(event)
  with pytest.raises(module.HistoryDownloadError, match=reason) as caught:
    download(sdk, client)
  assert "secret" not in str(caught.value)
  assert client.stops == 1
  assert client.callback({"finished": 1, "total": 1}) is True


def test_user_callback_failure_is_not_swallowed(sdk):
  def fail(_data):
    raise ValueError("private vendor details")

  client = NativeClient()
  with pytest.raises(module.HistoryDownloadError, match="CALLBACK_FAILED"):
    download(sdk, client, callback=fail)
  assert client.stops == 1


def test_partial_progress_times_out_and_cancels(sdk):
  client = NativeClient({"finished": 0, "total": 1})
  with pytest.raises(module.HistoryDownloadError, match="TIMEOUT"):
    download(sdk, client, timeout=0.01)
  assert client.stops == 1


def test_disconnect_after_submission_cancels(sdk):
  client = NativeClient()
  with pytest.raises(module.HistoryDownloadError, match="DISCONNECTED"):
    download(sdk, client, callback=lambda _: setattr(client, "connected", False))
  assert client.stops == 1


def test_cancel_failure_is_terminal(sdk):
  client = NativeClient({"finished": 0, "total": -1})

  def fail():
    raise RuntimeError("private vendor details")

  client.stop_supply_history_data2 = fail
  with pytest.raises(module.HistoryDownloadError, match="CANCEL_FAILED"):
    download(sdk, client)


def test_unknown_sdk_rejected_before_native_submission(tmp_path):
  path = tmp_path / "xtdata.py"
  path.write_text("unknown vendor version")
  client = NativeClient()
  with pytest.raises(module.HistoryDownloadError, match="UNSUPPORTED"):
    download(SimpleNamespace(__file__=str(path)), client)
  assert client.calls == []


def test_datetime_bounds_and_incremental_default(sdk):
  client = NativeClient()
  download(sdk, client, start_time=datetime(2026, 9, 7), incrementally=None)
  assert client.calls[0][2] == "20260907000000"
  assert client.calls[0][-1]["incrementally"] is False


def test_completion_without_result_preserves_empty_mapping(sdk):
  # The caller still reads and validates coverage; callback completion alone
  # does not prove any records exist (including total=0 responses).
  assert download(sdk, NativeClient({"finished": 0, "total": 0})) == {}


def test_timing_correlates_callback_and_return_with_unit(sdk, caplog):
  token = history_unit.set(("request-test", 3))
  try:
    with caplog.at_level("INFO", logger="quantx_qmt_agent.history_timing"):
      download(sdk, NativeClient())
  finally:
    history_unit.reset(token)
  assert (
    "request_id=request-test unit_index=3 stage=native_submit_return" in caplog.text
  )
  assert "stage=download_complete" in caplog.text
  assert "callback_to_return_ms" in caplog.text


def test_native_submission_exception_cancels(sdk):
  client = NativeClient()

  def fail(*_):
    raise RuntimeError("submission failed")

  client.supply_history_data2 = fail
  with pytest.raises(RuntimeError, match="submission failed"):
    download(sdk, client)
  assert client.stops == 1
