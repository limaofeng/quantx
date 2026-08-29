import asyncio
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import quantx_worker.prefector.flows.daily_indicator_snapshot_flow as indicator_flow
import quantx_worker.prefector.flows.daily_market_data_sync_flow as market_flow


class FakeLogger:
  def info(self, *args, **kwargs):
    return None


class FakeTradingTimeService:
  async def get_previous_trading_day(self, market, from_date):
    assert market == "SH"
    return date(2026, 7, 28)


class FakeTradingDates:
  def __init__(self):
    self.trading_time_service = FakeTradingTimeService()

  async def is_trading_date(self, market, check_date):
    return check_date == date(2026, 7, 29)

  async def get_trading_calendar(self, market, start_date, end_date):
    current = start_date
    result = []
    while current <= end_date:
      if current.weekday() < 5:
        result.append(current)
      current = date.fromordinal(current.toordinal() + 1)
    return result


def test_daily_market_sync_retries_durable_batches() -> None:
  assert market_flow.daily_market_data_sync_flow.retries == 2
  assert market_flow.daily_market_data_sync_flow.retry_delay_seconds == 60


def test_market_request_batch_size_respects_complete_record_budget() -> None:
  assert market_flow._market_data_request_batch_size(
    periods=["1d"],
    start_time="20260828",
    end_time="20260828",
  ) == 300
  assert market_flow._market_data_request_batch_size(
    periods=["tick"],
    start_time="20260828",
    end_time="20260828",
  ) == 24
  assert market_flow._market_data_request_batch_size(
    periods=["1m"],
    start_time="20260801",
    end_time="20260831",
  ) == 53


@pytest.mark.asyncio
async def test_expected_snapshot_date_changes_at_1535():
  helper = FakeTradingDates()

  before = await indicator_flow.expected_snapshot_date(
    datetime(2026, 7, 29, 15, 34),
    trading_dates=helper,
  )
  after = await indicator_flow.expected_snapshot_date(
    datetime(2026, 7, 29, 15, 35),
    trading_dates=helper,
  )

  assert before == date(2026, 7, 28)
  assert after == date(2026, 7, 29)


@pytest.mark.asyncio
async def test_explicit_snapshot_range_filters_weekend():
  dates = await indicator_flow.resolve_snapshot_dates(
    "20260723",
    "20260727",
    trading_dates=FakeTradingDates(),
  )

  assert dates == [
    date(2026, 7, 23),
    date(2026, 7, 24),
    date(2026, 7, 27),
  ]


@pytest.mark.asyncio
async def test_market_sync_resolves_sectors_and_uses_durable_transfer(
  monkeypatch,
):
  resolve = AsyncMock(
    return_value=[
      {
        "code": "600000.SH",
        "name": "浦发银行",
        "instrument_type": "stock",
        "float_volume": None,
      }
    ]
  )
  request = AsyncMock(
    return_value={
      "status": "completed",
      "request_id": "request-1",
      "records_received": 1,
      "records_saved": 1,
    }
  )
  monkeypatch.setattr(
    market_flow,
    "resolve_instruments",
    resolve,
  )
  monkeypatch.setattr(
    market_flow,
    "_request_and_wait",
    request,
  )

  monkeypatch.setattr(market_flow, "get_run_logger", FakeLogger)
  result = await market_flow.daily_market_data_sync_flow.fn(
    sectors=["沪深A股"],
    start_time="20260729",
    end_time="20260729",
    periods=["1d"],
  )

  assert result["status"] == "success"
  assert resolve.await_args.args[0] == ["沪深A股"]
  assert request.await_args.args[0]["stock_list"] == ["600000.SH"]
  assert request.await_args.args[0]["download"] is True
  assert result["transfer"]["batch_count"] == 1


@pytest.mark.asyncio
async def test_market_sync_splits_universe_at_agent_request_limit(
  monkeypatch,
):
  instruments = [
    {
      "code": f"{index:06d}.SZ",
      "name": "",
      "instrument_type": "stock",
      "float_volume": None,
    }
    for index in range(301)
  ]
  request = AsyncMock(
    side_effect=[
      {
        "status": "completed",
        "request_id": "request-1",
        "records_received": 300,
        "records_saved": 300,
      },
      {
        "status": "completed",
        "request_id": "request-2",
        "records_received": 1,
        "records_saved": 1,
      },
    ]
  )
  monkeypatch.setattr(
    market_flow,
    "resolve_instruments",
    AsyncMock(return_value=instruments),
  )
  monkeypatch.setattr(market_flow, "_request_and_wait", request)
  monkeypatch.setattr(market_flow, "get_run_logger", FakeLogger)

  result = await market_flow.daily_market_data_sync_flow.fn(
    start_time="20260729",
    end_time="20260729",
    periods=["1d"],
  )

  assert request.await_count == 2
  assert [
    len(call.args[0]["stock_list"])
    for call in request.await_args_list
  ] == [300, 1]
  assert result["transfer"]["request_id"] is None
  assert result["transfer"]["request_ids"] == ["request-1", "request-2"]
  assert result["transfer"]["batch_count"] == 2
  assert result["transfer"]["records_received"] == 301
  assert result["transfer"]["records_saved"] == 301


@pytest.mark.asyncio
async def test_market_sync_keeps_7552_daily_symbols_at_26_durable_batches(
  monkeypatch,
) -> None:
  instruments = [
    {
      "code": f"{index:06d}.SZ",
      "name": "",
      "instrument_type": "stock",
      "float_volume": None,
    }
    for index in range(7552)
  ]
  active = 0
  peak = 0
  scopes_by_offset: dict[int, str] = {}

  async def request(payload, **kwargs):
    nonlocal active, peak
    offset = int(str(payload["stock_list"][0]).split(".")[0])
    scopes_by_offset[offset] = kwargs["idempotency_scope"]
    active += 1
    peak = max(peak, active)
    try:
      await asyncio.sleep(0.004 if offset == 0 else 0.001)
    finally:
      active -= 1
    return {
      "status": "completed",
      "request_id": f"request-{offset}",
      "records_received": len(payload["stock_list"]),
      "records_saved": len(payload["stock_list"]),
    }
  monkeypatch.setattr(
    market_flow,
    "resolve_instruments",
    AsyncMock(return_value=instruments),
  )
  monkeypatch.setattr(market_flow, "_request_and_wait", request)
  monkeypatch.setattr(market_flow, "get_run_logger", FakeLogger)

  result = await market_flow.daily_market_data_sync_flow.fn(
    start_time="20260828",
    end_time="20260828",
    periods=["1d"],
    idempotency_scope="repair-7552",
  )

  assert peak == 2
  assert sorted(scopes_by_offset) == [index * 300 for index in range(26)]
  assert [
    scopes_by_offset[index * 300] for index in range(26)
  ] == [
    f"repair-7552:batch:{index:04d}" for index in range(1, 27)
  ]
  assert result["transfer"]["batch_count"] == 26
  assert result["transfer"]["request_ids"] == [
    f"request-{index * 300}" for index in range(26)
  ]
  assert result["transfer"]["records_received"] == 7552


def test_market_sync_idempotency_scope_is_retry_stable_and_run_scoped(
  monkeypatch,
) -> None:
  monkeypatch.setattr(
    market_flow,
    "flow_run_runtime",
    SimpleNamespace(id="flow-run-1"),
  )

  first = market_flow._market_data_sync_idempotency_scope("")
  retry = market_flow._market_data_sync_idempotency_scope("")
  monkeypatch.setattr(
    market_flow,
    "flow_run_runtime",
    SimpleNamespace(id="flow-run-2"),
  )
  next_run = market_flow._market_data_sync_idempotency_scope("")

  assert first == retry == "daily-market-data-sync-v1:flow-run-1"
  assert next_run == "daily-market-data-sync-v1:flow-run-2"
  assert next_run != first
  assert market_flow._market_data_sync_idempotency_scope(
    " explicit-campaign "
  ) == "explicit-campaign"


@pytest.mark.asyncio
async def test_market_sync_cancels_inflight_batch_after_first_failure(
  monkeypatch,
) -> None:
  instruments = [
    {
      "code": f"{index:06d}.SZ",
      "name": "",
      "instrument_type": "stock",
      "float_volume": None,
    }
    for index in range(601)
  ]
  started: list[int] = []
  cancelled = asyncio.Event()
  never_finish = asyncio.Event()

  async def request(payload, **kwargs):
    del kwargs
    offset = int(str(payload["stock_list"][0]).split(".")[0])
    started.append(offset)
    if offset == 300:
      await asyncio.sleep(0)
      return {
        "status": "failed",
        "request_id": "request-failed",
        "reason": "injected",
      }
    try:
      await never_finish.wait()
    except asyncio.CancelledError:
      cancelled.set()
      raise
    raise AssertionError("unreachable")

  monkeypatch.setattr(
    market_flow,
    "resolve_instruments",
    AsyncMock(return_value=instruments),
  )
  monkeypatch.setattr(market_flow, "_request_and_wait", request)
  monkeypatch.setattr(market_flow, "get_run_logger", FakeLogger)

  with pytest.raises(RuntimeError, match="request-failed"):
    await market_flow.daily_market_data_sync_flow.fn(
      start_time="20260828",
      end_time="20260828",
      periods=["1d"],
      idempotency_scope="failure-campaign",
    )

  assert started == [0, 300]
  assert cancelled.is_set()


@pytest.mark.asyncio
async def test_market_sync_propagates_agent_timeout(monkeypatch):
  monkeypatch.setattr(
    market_flow,
    "resolve_instruments",
    AsyncMock(
      return_value=[
        {
          "code": "600000.SH",
          "name": "",
          "instrument_type": "stock",
          "float_volume": None,
        }
      ]
    ),
  )
  monkeypatch.setattr(
    market_flow,
    "_request_and_wait",
    AsyncMock(
      return_value={
        "status": "timeout",
        "request_id": "request-timeout",
      }
    ),
  )
  monkeypatch.setattr(market_flow, "get_run_logger", FakeLogger)

  with pytest.raises(RuntimeError, match="request-timeout"):
    await market_flow.daily_market_data_sync_flow.fn(
      start_time="20260729",
      end_time="20260729",
      periods=["1d"],
    )


@pytest.mark.asyncio
async def test_market_sync_binds_explicit_live_agent(monkeypatch):
  request = AsyncMock(
    return_value={
      "status": "completed",
      "request_id": "request-bound",
      "records_received": 1,
      "records_saved": 1,
    }
  )
  monkeypatch.setattr(
    market_flow,
    "resolve_instruments",
    AsyncMock(
      return_value=[
        {
          "code": "600000.SH",
          "name": "",
          "instrument_type": "stock",
          "float_volume": None,
        }
      ]
    ),
  )
  monkeypatch.setattr(market_flow, "_request_and_wait", request)
  monkeypatch.setattr(market_flow, "get_run_logger", FakeLogger)

  await market_flow.daily_market_data_sync_flow.fn(
    stock_list=["600000.SH"],
    start_time="20260729",
    end_time="20260729",
    periods=["1d"],
    agent_device_id="device-live",
  )

  assert request.await_args.kwargs["agent_device_id"] == "device-live"
  assert request.await_args.kwargs["idempotency_scope"].endswith(
    ":batch:0001"
  )


@pytest.mark.asyncio
async def test_skip_download_only_runs_snapshot_flow(monkeypatch):
  request = AsyncMock()
  indicator = AsyncMock(
    return_value={
      "status": "success",
      "dates": [
        {"snapshot_date": "2026-07-29", "status": "success"}
      ],
    }
  )
  monkeypatch.setattr(
    market_flow,
    "resolve_instruments",
    AsyncMock(
      return_value=[
        {
          "code": "600000.SH",
          "name": "",
          "instrument_type": "stock",
          "float_volume": None,
        }
      ]
    ),
  )
  monkeypatch.setattr(
    market_flow,
    "_request_and_wait",
    request,
  )
  monkeypatch.setattr(
    market_flow,
    "daily_indicator_snapshot_flow",
    indicator,
  )

  monkeypatch.setattr(market_flow, "get_run_logger", FakeLogger)
  result = await market_flow.daily_market_data_sync_flow.fn(
    sectors=["沪深A股", "沪深ETF"],
    start_time="20260729",
    end_time="20260729",
    periods=["1d"],
    skip_download=True,
    compute_daily_signals=True,
  )

  assert result["status"] == "success"
  request.assert_not_awaited()
  indicator.assert_awaited_once()
