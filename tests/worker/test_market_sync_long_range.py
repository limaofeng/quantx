"""Long-range sync boundaries, missing source data and drain/cancel behavior."""

import asyncio
import pickle
from datetime import date
from unittest.mock import AsyncMock, Mock

import pytest
import quantx_worker.prefector.flows.daily_market_data_sync_flow as flow
from quantx_qmt_agent.broker import _validate_bars_request
from quantx_worker.prefector.flows.market_data_sync_partitions import (
  market_date_windows,
  validate_market_partition,
)

from tests.worker.market_sync_helpers import completed_transfer, trading_days


@pytest.fixture(autouse=True)
def isolated_calendar(monkeypatch):
  async def calendar(*, market, start_date, end_date):
    return trading_days(start_date.strftime("%Y%m%d"), end_date.strftime("%Y%m%d"))

  monkeypatch.setattr(
    flow, "TradingDateHelper", lambda: Mock(get_trading_calendar=calendar)
  )


@pytest.mark.parametrize("periods", [["1m"], ["1d", "1m"]])
async def test_two_months_reach_agent_as_disjoint_budgeted_windows(
  monkeypatch, periods
):
  calls = []

  async def request(payload, **kwargs):
    # Validate the actual Worker payload against the real Agent's limits.
    _validate_bars_request(payload)
    calls.append((payload, kwargs["idempotency_scope"]))
    return completed_transfer(payload, str(len(calls)))

  monkeypatch.setattr(flow, "_request_and_wait", request)
  codes = [f"{i:06d}.SZ" for i in range(301)]
  result = await flow._request_market_data_batches(
    codes=codes,
    periods=periods,
    start_time="20260801",
    end_time="20260930",
    agent_device_id="",
    idempotency_scope="two-months",
    logger=Mock(),
    lifetimes={},
  )
  assert len(result) == 12
  assert len({scope for _, scope in calls}) == 12
  for start, end in [("20260801", "20260831"), ("20260901", "20260930")]:
    window_codes = [
      code
      for payload, _ in calls
      if (payload["start_time"], payload["end_time"]) == (start, end)
      for code in payload["stock_list"]
    ]
    assert window_codes == codes


def test_window_limits_include_leap_day_and_daily_agent_boundary():
  assert list(market_date_windows("20240201", "20240304", ["1m"])) == [
    ("20240201", "20240302"),
    ("20240303", "20240304"),
  ]
  windows = list(market_date_windows("20100101", "20201231", ["1d"]))
  assert len(windows) == 2
  assert windows[0][0] == "20100101" and windows[-1][1] == "20201231"
  for start, end in windows:
    _validate_bars_request(
      {
        "stock_list": ["000001.SZ"],
        "periods": ["1d"],
        "start_time": start,
        "end_time": end,
      }
    )


@pytest.mark.parametrize("periods", [["1d", "1m"], ["tick", "1d"]])
def test_empty_period_is_not_hidden_by_nonempty_other_period(periods):
  payload = {
    "stock_list": ["000001.SZ"],
    "periods": periods,
    "start_time": "20260803",
    "end_time": "20260831",
  }
  transfer = completed_transfer(payload, "empty-period")
  transfer["code_summaries"][0]["row_count"] = 0
  with pytest.raises(RuntimeError, match="000001.SZ/" + periods[0]):
    validate_market_partition(
      transfer,
      payload["stock_list"],
      periods,
      payload["start_time"],
      payload["end_time"],
      trading_days=trading_days(payload["start_time"], payload["end_time"]),
      lifetimes={},
    )


@pytest.mark.parametrize(
  "mutation", ["empty", "missing", "duplicate", "missing_day", "absent_coverage"]
)
def test_partial_symbol_or_date_cannot_report_success(mutation):
  codes = ["000001.SZ", "600000.SH"]
  payload = {
    "stock_list": codes,
    "periods": ["1d"],
    "start_time": "20260803",
    "end_time": "20260804",
  }
  transfer = completed_transfer(payload, mutation)
  if mutation == "empty":
    transfer["code_summaries"][1]["row_count"] = 0
  elif mutation == "missing":
    transfer["code_summaries"].pop()
  elif mutation == "duplicate":
    transfer["code_summaries"][1] = transfer["code_summaries"][0]
  elif mutation == "missing_day":
    transfer["day_coverage"][0]["point_count"] = 0
  else:
    transfer.pop("day_coverage")
  with pytest.raises(RuntimeError):
    validate_market_partition(
      transfer,
      codes,
      ["1d"],
      "20260803",
      "20260804",
      trading_days=trading_days("20260803", "20260804"),
      lifetimes={},
    )


async def test_listing_boundaries_exclude_inactive_tick_days(monkeypatch):
  calls = []

  async def request(payload, **kwargs):
    calls.append(payload)
    return completed_transfer(payload, str(len(calls)))

  monkeypatch.setattr(flow, "_request_and_wait", request)
  await flow._request_market_data_batches(
    codes=["000001.SZ", "600000.SH"],
    periods=["tick"],
    start_time="20260803",
    end_time="20260805",
    agent_device_id="",
    idempotency_scope="listed",
    logger=Mock(),
    lifetimes={
      "000001.SZ": (date(2026, 8, 4), None),
      "600000.SH": (None, date(2026, 8, 4)),
    },
  )
  assert [(p["stock_list"][0], p["start_time"]) for p in calls] == [
    ("600000.SH", "20260803"),
    ("000001.SZ", "20260804"),
    ("600000.SH", "20260804"),
    ("000001.SZ", "20260805"),
  ]


def test_daily_listing_boundary_is_not_a_source_gap():
  payload = {
    "stock_list": ["000001.SZ"],
    "periods": ["1d"],
    "start_time": "20260803",
    "end_time": "20260804",
  }
  transfer = completed_transfer(payload, "new-listing")
  transfer["day_coverage"].pop(0)
  transfer["code_summaries"][0]["row_count"] = 1
  transfer["records_received"] = transfer["records_saved"] = 1
  validate_market_partition(
    transfer,
    payload["stock_list"],
    ["1d"],
    "20260803",
    "20260804",
    trading_days=trading_days("20260803", "20260804"),
    lifetimes={"000001.SZ": (date(2026, 8, 4), None)},
  )


async def test_tick_empty_and_failed_partitions_do_not_block_later_days(monkeypatch):
  calls = []
  saved = set()

  async def request(payload, **kwargs):
    key = (payload["stock_list"][0], payload["start_time"])
    calls.append(key)
    transfer = completed_transfer(payload, "/".join(key))
    if key == ("000001.SZ", "20260803"):
      transfer["code_summaries"][0]["row_count"] = 0
    elif key == ("600000.SH", "20260803"):
      return {
        "status": "failed",
        "request_id": "rejected",
        "reason": "source unavailable",
      }
    else:
      saved.add(key)
    return transfer

  monkeypatch.setattr(flow, "_request_and_wait", request)
  with pytest.raises(flow.MarketDataSyncIncomplete) as caught:
    await flow._request_market_data_batches(
      codes=["000001.SZ", "600000.SH"],
      periods=["tick"],
      start_time="20260803",
      end_time="20260805",
      agent_device_id="",
      idempotency_scope="continue",
      logger=Mock(),
      lifetimes={},
    )
  assert len(calls) == 6 and len(saved) == 4
  assert len(caught.value.failures) == 2
  assert caught.value.total_batches == 6
  assert {f["start_time"] for f in caught.value.failures} == {"20260803"}


async def test_user_cancellation_stops_inflight_tasks_and_dispatch(monkeypatch):
  running = []
  cancelled = []
  ready = asyncio.Event()

  async def request(payload, **kwargs):
    running.append(payload)
    if len(running) == 2:
      ready.set()
    try:
      await asyncio.Event().wait()
    finally:
      cancelled.append(payload)

  monkeypatch.setattr(flow, "_request_and_wait", request)
  task = asyncio.create_task(
    flow._request_market_data_batches(
      codes=["000001.SZ"],
      periods=["tick"],
      start_time="20260803",
      end_time="20260807",
      agent_device_id="",
      idempotency_scope="cancel",
      logger=Mock(),
      lifetimes={},
    )
  )
  await asyncio.wait_for(ready.wait(), timeout=2)
  task.cancel()
  with pytest.raises(asyncio.CancelledError):
    await task
  assert len(running) == len(cancelled) == 2


async def test_closed_market_range_is_skipped_without_requests(monkeypatch):
  monkeypatch.setattr(
    flow, "resolve_instruments", AsyncMock(return_value=[{"code": "000001.SZ"}])
  )
  monkeypatch.setattr(flow, "get_run_logger", Mock)
  request = AsyncMock()
  monkeypatch.setattr(flow, "_request_and_wait", request)
  result = await flow.daily_market_data_sync_flow.fn(
    start_time="20260801", end_time="20260802", periods=["1m"]
  )
  assert result["status"] == "skipped"
  request.assert_not_awaited()


def test_aggregate_failure_survives_prefect_exception_serialization():
  original = flow.MarketDataSyncIncomplete(
    [{"batch_index": 2, "reason": "request_id=failed"}], 3
  )
  restored = pickle.loads(pickle.dumps(original))
  assert restored.failures == original.failures
  assert restored.total_batches == 3
  assert str(restored) == str(original)


async def test_timeout_stops_dispatch_of_new_durable_requests(monkeypatch):
  calls = []
  cancelled = asyncio.Event()

  async def request(payload, **kwargs):
    calls.append(payload)
    if payload["start_time"] == "20260803":
      await asyncio.sleep(0)
      return {"status": "timeout", "request_id": "still-running"}
    try:
      await asyncio.Event().wait()
    finally:
      cancelled.set()

  monkeypatch.setattr(flow, "_request_and_wait", request)
  with pytest.raises(RuntimeError, match="still-running"):
    await flow._request_market_data_batches(
      codes=["000001.SZ"],
      periods=["tick"],
      start_time="20260803",
      end_time="20260807",
      agent_device_id="",
      idempotency_scope="outage",
      logger=Mock(),
      lifetimes={},
    )
  assert len(calls) == 2
  assert cancelled.is_set()


async def test_retry_reuses_successful_durable_requests(monkeypatch):
  durable_results = {}
  downloads = []
  request_calls = []

  async def request(payload, **kwargs):
    key = (kwargs["idempotency_scope"], payload["start_time"])
    request_calls.append(key)
    if key not in durable_results:
      downloads.append(key)
      result = completed_transfer(payload, payload["start_time"])
      if payload["start_time"] == "20260803":
        result["code_summaries"][0]["row_count"] = 0
      durable_results[key] = result
    return durable_results[key]

  monkeypatch.setattr(flow, "_request_and_wait", request)
  for _ in range(2):
    with pytest.raises(flow.MarketDataSyncIncomplete):
      await flow._request_market_data_batches(
        codes=["000001.SZ"],
        periods=["tick"],
        start_time="20260803",
        end_time="20260805",
        agent_device_id="",
        idempotency_scope="retry-stable",
        logger=Mock(),
        lifetimes={},
      )
  assert len(downloads) == 3
  assert request_calls[:3] == request_calls[3:]
