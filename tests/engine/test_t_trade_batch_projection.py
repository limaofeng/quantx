from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from quantx_engine.report_processor import _project_t_trade_event
from quantx_infrastructure.models.agent_runtime import TTradeBatch


def _batch() -> TTradeBatch:
  return TTradeBatch(
    batch_id="batch-1",
    account_id="account-1",
    instrument_code="600000.SH",
    strategy_run_id="run-1",
    target_volume=100,
    status="ENTRY_QUEUED",
  )


@pytest.mark.asyncio
async def test_trade_projection_freezes_first_entry_and_final_exit_times() -> None:
  batch = _batch()
  shanghai = ZoneInfo("Asia/Shanghai")
  first_entry = datetime(2026, 8, 29, 9, 35, tzinfo=shanghai)
  later_entry = datetime(2026, 8, 29, 9, 36, tzinfo=shanghai)
  earlier_exit = datetime(2026, 8, 29, 10, 30, tzinfo=shanghai)
  later_exit = datetime(2026, 8, 29, 10, 31, tzinfo=shanghai)

  await _project_t_trade_event(
    batch,
    event_type="TRADE",
    role="ENTRY",
    item={"traded_volume": 50, "traded_price": 10.2, "traded_time": later_entry},
  )
  await _project_t_trade_event(
    batch,
    event_type="TRADE",
    role="ENTRY",
    item={"traded_volume": 50, "traded_price": 10.0, "traded_time": first_entry},
  )
  await _project_t_trade_event(
    batch,
    event_type="TRADE",
    role="EXIT",
    item={"traded_volume": 50, "traded_price": 10.6, "traded_time": later_exit},
  )

  assert batch.entry_filled_at == datetime(2026, 8, 29, 1, 35)
  assert batch.closed_at is None
  assert batch.status == "EXIT_PARTIAL"

  await _project_t_trade_event(
    batch,
    event_type="TRADE",
    role="EXIT",
    item={"traded_volume": 50, "traded_price": 10.5, "traded_time": earlier_exit},
  )

  assert batch.entry_filled_at == datetime(2026, 8, 29, 1, 35)
  assert batch.last_exit_filled_at == datetime(2026, 8, 29, 2, 31)
  assert batch.closed_at == datetime(2026, 8, 29, 2, 31)
  assert batch.terminal_at == datetime(2026, 8, 29, 2, 31)
  assert batch.status == "CLOSED"


@pytest.mark.asyncio
async def test_zero_fill_entry_rejection_uses_terminal_order_time() -> None:
  batch = _batch()
  rejected_at = datetime(
    2026,
    8,
    29,
    9,
    40,
    tzinfo=ZoneInfo("Asia/Shanghai"),
  )

  await _project_t_trade_event(
    batch,
    event_type="ORDER",
    role="ENTRY",
    item={
      "status": "REJECTED",
      "order_time": rejected_at,
      "status_msg": "broker rejected",
    },
  )

  assert batch.status == "ENTRY_REJECTED"
  assert batch.entry_filled_at is None
  assert batch.closed_at is None
  assert batch.terminal_at == datetime(2026, 8, 29, 1, 40)


@pytest.mark.asyncio
async def test_missing_or_future_lifecycle_time_is_not_invented() -> None:
  batch = _batch()

  await _project_t_trade_event(
    batch,
    event_type="TRADE",
    role="ENTRY",
    item={"traded_volume": 100, "traded_price": 10.0},
  )
  assert batch.entry_filled_at is None

  rejected = _batch()
  await _project_t_trade_event(
    rejected,
    event_type="ORDER",
    role="ENTRY",
    item={
      "status": "REJECTED",
      "status_msg": "missing source time",
      "updated_at": "2026-08-29T01:40:00Z",
    },
  )
  assert rejected.closed_at is None
  assert rejected.terminal_at is None

  future = _batch()
  await _project_t_trade_event(
    future,
    event_type="TRADE",
    role="ENTRY",
    item={
      "traded_volume": 100,
      "traded_price": 10.0,
      "traded_time": "2999-01-01T00:00:00Z",
    },
  )
  assert future.entry_filled_at is None

  future_terminal = _batch()
  await _project_t_trade_event(
    future_terminal,
    event_type="ORDER",
    role="ENTRY",
    item={"status": "EXPIRED", "reported_at": "2999-01-01T00:00:00Z"},
  )
  assert future_terminal.closed_at is None
  assert future_terminal.terminal_at is None
