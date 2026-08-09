from datetime import date, datetime
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
async def test_market_sync_binds_explicit_data_only_agent(monkeypatch):
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
    agent_device_id="device-data-only",
  )

  assert request.await_args.kwargs == {
    "agent_device_id": "device-data-only"
  }


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
