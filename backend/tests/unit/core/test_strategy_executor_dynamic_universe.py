import pytest

from core.brokers.simulator import SimulatorBroker
from core.runtime_state_manager import RuntimeStateManager
from core.strategies.ashare_intraday_t_assistant import (
  AshareIntradayTAssistantStrategy,
)
from core.strategies.base import StrategyContext, StrategyRunMode
from core.strategy_executor import StrategyExecutor, StrategyRuntime


class FakeRealtimeAdapter:
  def __init__(self):
    self.subscribed = []
    self.unsubscribed = []

  async def subscribe_tick(self, instrument_code, callback):
    self.subscribed.append(instrument_code)
    return f"tick:{instrument_code}"

  async def unsubscribe(self, subscription_id):
    self.unsubscribed.append(subscription_id)


@pytest.mark.asyncio
async def test_reconcile_updates_tick_subscriptions_without_restarting_run():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-universe",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={"account_id": "account-1"},
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="dynamic-universe",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  await runtime.strategy.initialize()
  runtime.data_adapter = FakeRealtimeAdapter()

  first = await executor._apply_realtime_instrument_reconcile(
    runtime,
    ["600000.SH", "000001.SZ"],
    instrument_metadata={
      "600000.SH": {"eligible": True, "policy_volume": 100},
      "000001.SZ": {"eligible": True, "policy_volume": 200},
    },
  )
  second = await executor._apply_realtime_instrument_reconcile(
    runtime,
    ["000001.SZ"],
    instrument_metadata={
      "000001.SZ": {"eligible": True, "policy_volume": 200}
    },
  )

  assert first["added"] == ["000001.SZ"]
  assert set(runtime.data_adapter.subscribed) == {"600000.SH", "000001.SZ"}
  assert second["removed"] == ["600000.SH"]
  assert runtime.data_adapter.unsubscribed == ["tick:600000.SH"]
  assert runtime.context.instruments == ["000001.SZ"]
  assert set(runtime.realtime_subscription_ids) == {"000001.SZ"}


def test_dynamic_holding_snapshot_seeds_core_inventory_without_overwriting_active_batch():
  executor = StrategyExecutor()
  context = StrategyContext(
    run_id="run-inventory",
    mode=StrategyRunMode.PAPER,
    instruments=["600000.SH"],
    parameters={"account_id": "account-1"},
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="dynamic-inventory",
    strategy_id=1,
    strategy_class=AshareIntradayTAssistantStrategy,
    context=context,
  )
  runtime.strategy = AshareIntradayTAssistantStrategy(context)
  runtime.state_manager = RuntimeStateManager(
    run_id=context.run_id, persist_enabled=False, enable_reserve=True
  )
  runtime.broker = SimulatorBroker(account_id="paper-account")
  metadata = {
    "600000.SH": {
      "position_shares": 1000,
      "position_available_shares": 800,
      "position_frozen_shares": 100,
      "position_avg_price": 9.5,
      "position_market_value": 10_000.0,
    }
  }

  executor._sync_dynamic_holding_inventory(runtime, metadata)

  position = runtime.state_manager.get_position("600000.SH")
  ledger = runtime.state_manager.get_bucket_ledger_snapshot()
  assert position["long_volume"] == 1000
  assert position["available_volume"] == 800
  assert ledger["instruments"]["600000.SH"]["core"]["available_volume"] == 800
  assert ledger["instruments"]["600000.SH"]["swing"]["total_volume"] == 0
  assert runtime.broker.positions["600000.SH"].available_volume == 800

  runtime.strategy.state.update(
    {
      "instrument_states": {
        "600000.SH": {
          "entry_filled_volume": 100,
          "exit_filled_volume": 0,
        }
      }
    }
  )
  executor._sync_dynamic_holding_inventory(
    runtime,
    {
      "600000.SH": {
        **metadata["600000.SH"],
        "position_shares": 500,
        "position_available_shares": 500,
      }
    },
  )
  assert runtime.state_manager.get_position("600000.SH")["long_volume"] == 1000

  runtime.strategy.state["instrument_states"]["600000.SH"] = {
    "batch_id": "batch-awaiting-trade-detail",
    "entry_filled_volume": 0,
    "exit_filled_volume": 0,
  }
  executor._sync_dynamic_holding_inventory(
    runtime,
    {
      "600000.SH": {
        **metadata["600000.SH"],
        "position_shares": 600,
        "position_available_shares": 600,
      }
    },
  )
  assert runtime.state_manager.get_position("600000.SH")["long_volume"] == 1000
