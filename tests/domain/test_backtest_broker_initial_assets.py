from datetime import datetime

import pytest
from quantx_domain.brokers.backtest import BacktestBroker
from quantx_domain.brokers.base import Position
from quantx_domain.strategies.ashare_limit_up_board_assistant import (
  AshareLimitUpBoardAssistantStrategy,
)
from quantx_domain.strategies.base import StrategyContext, StrategyRunMode
from quantx_engine.strategy_executor import StrategyExecutor, StrategyRuntime


class _EmptyStateManager:
  @staticmethod
  def get_all_positions():
    return {}


def _position(*, market_value: float = 10_000.0) -> Position:
  return Position(
    instrument_code="000001.SZ",
    long_volume=1_000,
    available_volume=1_000,
    long_avg_price=10.0,
    last_price=10.0,
    market_value=market_value,
  )


@pytest.mark.asyncio
async def test_non_trading_asset_residual_is_constant_in_both_equity_curves() -> None:
  broker = BacktestBroker(initial_capital=100_000.0)
  broker.positions["000001.SZ"] = _position()

  broker.configure_initial_portfolio(
    cash=80_000.0,
    total_asset=100_000.0,
    positions=broker.positions,
  )

  assert broker.cash == 80_000.0
  assert broker.non_trading_asset_value == 10_000.0
  assert broker.initial_capital == 100_000.0
  assert broker.initial_asset_reconciliation["quality_flags"] == [
    "NON_TRADING_ASSET_RESIDUAL_PRESERVED"
  ]

  await broker.update_market_data(
    "000001.SZ",
    11.0,
    datetime(2024, 1, 2, 10, 0),
  )

  account = await broker.get_account()
  assert account.cash == 80_000.0
  assert account.market_value == 11_000.0
  assert account.total_asset == 101_000.0
  assert broker.replay_curve[-1]["equity"] == 101_000.0
  assert broker.replay_curve[-1]["passive_equity"] == 101_000.0


@pytest.mark.asyncio
async def test_negative_asset_residual_uses_known_components_without_fake_drawdown() -> (
  None
):
  broker = BacktestBroker(initial_capital=100_000.0)
  broker.positions["000001.SZ"] = _position()

  broker.configure_initial_portfolio(
    cash=95_000.0,
    total_asset=100_000.0,
    positions=broker.positions,
  )

  assert broker.non_trading_asset_value == 0.0
  assert broker.initial_capital == 105_000.0
  assert broker.initial_asset_reconciliation["raw_residual"] == -5_000.0
  assert broker.initial_asset_reconciliation["negative_residual_clamped"] is True
  assert broker.initial_asset_reconciliation["quality_flags"] == [
    "INITIAL_COMPONENTS_EXCEED_REPORTED_TOTAL"
  ]

  await broker.update_market_data(
    "000001.SZ",
    10.0,
    datetime(2024, 1, 2, 10, 0),
  )

  account = await broker.get_account()
  assert account.total_asset == 105_000.0
  assert broker.replay_curve[-1]["equity"] == 105_000.0
  assert broker.replay_curve[-1]["passive_equity"] == 105_000.0
  assert broker.max_drawdown == 0.0


def test_executor_reconciles_non_trading_asset_with_zero_initial_positions() -> None:
  context = StrategyContext(
    run_id="empty-board-replay-assets",
    mode=StrategyRunMode.BACKTEST,
    instruments=[],
    parameters={"initial_cash": 80_000.0, "initial_total_asset": 100_000.0},
  )
  runtime = StrategyRuntime(
    run_id=context.run_id,
    name="empty-board-replay-assets",
    strategy_id=1,
    strategy_class=AshareLimitUpBoardAssistantStrategy,
    context=context,
    broker=BacktestBroker(initial_capital=100_000.0),
    state_manager=_EmptyStateManager(),
  )

  StrategyExecutor()._seed_simulated_broker_positions(runtime)

  assert runtime.broker.cash == 80_000.0
  assert runtime.broker.non_trading_asset_value == 20_000.0
  assert runtime.broker.initial_capital == 100_000.0
