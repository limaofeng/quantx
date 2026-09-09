"""UI's existing market sync flow uses bounded, audited Tick partitions."""

from datetime import date
from unittest.mock import AsyncMock, Mock

import pytest
import quantx_worker.prefector.flows.daily_market_data_sync_flow as flow
from quantx_worker.prefector.flows.market_data_sync_partitions import (
  validate_market_partition,
)

from tests.worker.market_sync_helpers import completed_transfer


async def test_month_range_is_split_through_public_request_gateway(monkeypatch):
  days = [date(2026, 8, 3), date(2026, 8, 31)]
  monkeypatch.setattr(
    flow,
    "TradingDateHelper",
    lambda: Mock(get_trading_calendar=AsyncMock(return_value=days)),
  )
  calls = []

  async def request(payload, **kwargs):
    calls.append((payload, kwargs))
    return completed_transfer(payload, f"request-{len(calls)}")

  monkeypatch.setattr(flow, "_request_and_wait", request)
  result = await flow._request_market_data_batches(
    codes=["600036.SH", "000001.SZ"],
    lifetimes={},
    periods=["tick", "1d"],
    start_time="20260801",
    end_time="20260831",
    agent_device_id="",
    idempotency_scope="fixture",
    logger=Mock(),
  )
  assert result["batch_count"] == 3
  tick_calls = [p for p, _ in calls if p["periods"] == ["tick"]]
  assert [(p["stock_list"], p["start_time"], p["end_time"]) for p in tick_calls] == [
    (["600036.SH", "000001.SZ"], day, day) for day in ("20260803", "20260831")
  ]
  assert [p["periods"] for p, _ in calls] == [["tick"], ["1d"], ["tick"]]
  assert all("retry_failed_requests" not in k for p, k in calls)
  assert len({k["idempotency_scope"] for _, k in calls}) == 3


def test_one_empty_period_cannot_be_hidden_by_nonempty_daily_bars():
  transfer = {
    "request_id": "gap",
    "code_summaries": [
      {"code": "000001.SZ", "period": "tick", "row_count": 0},
      {"code": "000001.SZ", "period": "1d", "row_count": 1},
    ],
  }
  with pytest.raises(RuntimeError, match="000001.SZ/tick"):
    validate_market_partition(
      transfer,
      ["000001.SZ"],
      ["tick", "1d"],
      "20260803",
      "20260803",
      trading_days=[date(2026, 8, 3)],
      lifetimes={},
    )


async def test_calendar_must_not_silently_change_scope(monkeypatch):
  monkeypatch.setattr(flow, "TradingDateHelper", lambda: Mock(get_trading_calendar=AsyncMock(return_value=[date(2026,9,1)])))
  with pytest.raises(ValueError, match="交易日历"):
    await flow._request_market_data_batches(
      codes=["000001.SZ"], periods=["tick"], start_time="20260801", end_time="20260831",
      agent_device_id="", idempotency_scope="calendar", logger=Mock(), lifetimes={},
    )


async def test_failed_tick_request_is_not_reopened(monkeypatch):
  from quantx_worker.prefector.flows import durable_agent_flows as durable

  store = Mock(
    create_market_data_request=AsyncMock(return_value="same-request"),
    market_data_request=AsyncMock(
      return_value={"status": "FAILED", "processing_error": "unavailable"}
    ),
    close=AsyncMock(),
  )
  monkeypatch.setattr(durable, "DurableRuntimeStore", lambda: store)
  result = await durable._request_and_wait(
    {"operation": "bars"}
  )
  assert result["status"] == "failed"
  assert result["request_id"] == "same-request"
  store.create_market_data_request.assert_awaited_once()


async def test_empty_tick_gateway_failure_identifies_day_and_symbol(monkeypatch):
  monkeypatch.setattr(
    flow,
    "_request_and_wait",
    AsyncMock(
      return_value={
        "status": "completed",
        "request_id": "empty-request",
        "records_received": 0,
        "records_saved": 0,
        "code_summaries": [{"code": "000001.SZ", "period": "tick", "row_count": 0}],
      }
    ),
  )
  with pytest.raises(RuntimeError, match="20260803.*000001.SZ/tick.*empty-request"):
    await flow._request_market_data_batch(
      code_batch=["000001.SZ"],
      batch_index=1,
      total_batches=1,
      periods=["tick"],
      start_time="20260803",
      end_time="20260803",
      agent_device_id="",
      idempotency_scope="fixture",
      trading_days=[date(2026, 8, 3)],
      lifetimes={},
    )
