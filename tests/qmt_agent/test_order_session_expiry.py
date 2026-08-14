from __future__ import annotations

from datetime import datetime

import pytest
import quantx_qmt_agent.miniqmt.local_agent as local_agent_module
from quantx_qmt_agent import clock
from quantx_qmt_agent.miniqmt.local_agent import MiniQmtLocalAgent


class FakeTradingManager:
  is_connected = True

  def __init__(self, *, cancelable_order_ids: set[int] | None = None) -> None:
    self.cancelable_order_ids = cancelable_order_ids or set()
    self.orders = [
      {
        "account_id": "account-1",
        "order_id": 101,
        "stock_code": "600000.SH",
        "order_time": int(
          datetime(2026, 8, 13, 10, 55, tzinfo=clock.SHANGHAI_TZ).timestamp()
        ),
        "order_status": 50,
        "order_volume": 100,
        "traded_volume": 0,
      }
    ]

  def get_account_info(self):
    return {"account_id": "account-1", "cash": 1_000_000}

  def get_positions(self):
    return []

  def get_orders(self, cancelable_only: bool = False):
    if not cancelable_only:
      return self.orders
    return [
      item
      for item in self.orders
      if int(item["order_id"]) in self.cancelable_order_ids
    ]

  def get_trades(self):
    return []


def _snapshot_at(
  monkeypatch: pytest.MonkeyPatch,
  observed_at: datetime,
  *,
  cancelable_order_ids: set[int] | None = None,
):
  monkeypatch.setattr(local_agent_module.clock, "now_aware", lambda: observed_at)
  agent = MiniQmtLocalAgent(
    FakeTradingManager(cancelable_order_ids=cancelable_order_ids)
  )
  return agent.full_snapshot()["orders"][0]


def test_unfilled_day_order_is_expired_after_market_close(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  order = _snapshot_at(
    monkeypatch,
    datetime(2026, 8, 13, 15, 1, tzinfo=clock.SHANGHAI_TZ),
  )

  assert order["order_status"] == 50
  assert order["effective_order_status"] == "EXPIRED"
  assert order["session_expired"] is True
  assert order["can_cancel"] is False
  assert order["effective_status_reason"] == "MARKET_SESSION_CLOSED"
  assert order["order_session_date"] == "2026-08-13"


def test_unfilled_day_order_remains_active_before_market_close(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  order = _snapshot_at(
    monkeypatch,
    datetime(2026, 8, 13, 14, 59, tzinfo=clock.SHANGHAI_TZ),
  )

  assert order["effective_order_status"] == "SUBMITTED"
  assert order["session_expired"] is False
  assert order["effective_status_reason"] == ""


def test_stale_broker_cancelable_flag_cannot_keep_day_order_live_after_close(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  order = _snapshot_at(
    monkeypatch,
    datetime(2026, 8, 13, 15, 1, tzinfo=clock.SHANGHAI_TZ),
    cancelable_order_ids={101},
  )

  assert order["can_cancel"] is True
  assert order["effective_order_status"] == "EXPIRED"
  assert order["session_expired"] is True
  assert order["effective_status_reason"] == "MARKET_SESSION_CLOSED"


def test_prior_day_unfilled_order_is_expired_on_next_snapshot(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  order = _snapshot_at(
    monkeypatch,
    datetime(2026, 8, 14, 9, 0, tzinfo=clock.SHANGHAI_TZ),
  )

  assert order["effective_order_status"] == "EXPIRED"
  assert order["session_expired"] is True
