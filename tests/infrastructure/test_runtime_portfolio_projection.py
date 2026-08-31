from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_domain.brokers.base import AccountInfo
from quantx_domain.clock import utcnow
from quantx_domain.strategies.base import StrategyRunMode
from quantx_engine import strategy_executor as executor_module
from quantx_engine.strategy_executor import StrategyExecutor
from quantx_infrastructure.core.runtime_state_manager import RuntimeStateManager


def manager():
  result = RuntimeStateManager(run_id="run", persist_enabled=False, enable_reserve=True)
  result.update_account(cash=10000, total_asset=10000)
  return result


def test_old_broker_cash_refresh_cannot_erase_an_unobserved_reservation():
  state = manager()
  assert state.reserve_cash("first", 6000)
  state.update_account(cash=10000, frozen_cash=0, total_asset=10000)
  assert state.get_account()["cash"] == 4000
  assert not state.reserve_cash("second", 6000)
  assert state.release_cash("first")
  assert state.get_account()["cash"] == 10000


def test_observed_freeze_is_not_counted_twice_or_refunded_before_snapshot():
  state = manager()
  assert state.reserve_cash("first", 6000)
  state.update_account(
    cash=4000, frozen_cash=6000, total_asset=10000, covered_order_ids={"first"}
  )
  assert state.get_account()["cash"] == 4000
  assert state.get_account()["frozen_cash"] == 6000
  # Restart must retain the fact that the broker still reports this freeze.
  restored = RuntimeStateManager(
    run_id="run", persist_enabled=False, enable_reserve=True
  )
  restored._state = state._state.copy()
  restored._restore_reservation_state()
  assert restored.release_cash("first")
  assert restored.get_account()["cash"] == 4000
  restored.update_account(cash=10000, frozen_cash=0, total_asset=10000)
  assert restored.get_account()["cash"] == 10000


def test_complete_position_snapshot_removes_absent_positions_and_keeps_local_sell_hold():
  state = manager()
  state.update_position("600000.SH", long_volume=1000, available_volume=1000)
  state.update_position("600001.SH", long_volume=100, available_volume=100)
  assert state.reserve_position("sell", "600000.SH", 600)
  state.replace_broker_positions(
    {"600000.SH": {"long_volume": 1000, "available_volume": 1000}},
    covered_order_ids=set(),
  )
  assert state.get_position("600000.SH")["available_volume"] == 400
  assert state.get_position("600001.SH") is None


@pytest.mark.asyncio
async def test_serialized_refresh_waits_for_durable_fills_and_newer_snapshot(
  monkeypatch,
):
  state = manager()
  stamp = utcnow()
  account = AccountInfo(
    account_id="account",
    total_asset=8000,
    cash=8000,
    frozen_cash=0,
    market_value=0,
    total_pnl=0,
    daily_pnl=0,
    last_update_time=stamp,
  )
  runtime = SimpleNamespace(
    context=SimpleNamespace(mode=StrategyRunMode.LIVE),
    run_id="run",
    state_manager=state,
    broker=SimpleNamespace(
      get_portfolio_snapshot=AsyncMock(return_value=(account, set()))
    ),
    last_broker_report_at=stamp + timedelta(seconds=1),
  )
  db = SimpleNamespace(scalar=AsyncMock(return_value="unapplied-fill"))

  class Session:
    async def __aenter__(self):
      return db

    async def __aexit__(self, *_args):
      return False

  monkeypatch.setattr(executor_module, "AsyncSessionLocal", Session)
  executor = StrategyExecutor()
  await executor._refresh_live_account_snapshot(runtime)
  assert state.get_account()["cash"] == 10000
  db.scalar.return_value = None
  await executor._refresh_live_account_snapshot(runtime)
  assert state.get_account()["cash"] == 10000
  runtime.last_broker_report_at = stamp - timedelta(seconds=1)
  await executor._refresh_live_account_snapshot(runtime)
  assert state.get_account()["cash"] == 8000
