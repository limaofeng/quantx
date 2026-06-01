"""Tests for the dynamic balance dual-bucket strategy."""

from datetime import datetime, timedelta

import pytest

from core.strategies.ashare_dynamic_balance_dual_bucket import (
  AshareDynamicBalanceDualBucketStrategy,
  BalancePositionPhase,
  BalanceTargets,
  BalanceTrendState,
)
from core.strategies.base import StrategyCadence, StrategyContext, StrategyInput
from core.strategies.base import TradeExecutionEvent
from models.enums import StrategyRunMode
from models.kline import KLine
from models.tick import Tick


pytestmark = pytest.mark.unit


def _context(parameters=None):
  return StrategyContext(
    run_id="run-dynamic",
    mode=StrategyRunMode.BACKTEST,
    instruments=["000001.SZ"],
    parameters=parameters or {},
    initial_capital=100_000,
  )


def _bar(day: int, close: float, volume: float = 100_000) -> KLine:
  return KLine(
    stock_code="000001.SZ",
    period="1d",
    time=datetime(2024, 1, 1) + timedelta(days=day),
    open=close,
    high=close * 1.02,
    low=close * 0.98,
    close=close,
    pre_close=close,
    volume=volume,
    amount=volume * close,
    settelement_price=0.0,
    open_interest=0,
    suspend_flag=0,
  )


def _input(strategy, cadence, event, **overrides):
  price = getattr(event, "close", getattr(event, "last_price", 10.0))
  base = {
    "run_id": strategy.context.run_id,
    "strategy_id": "dynamic",
    "timestamp": getattr(event, "time", datetime(2024, 1, 2, 10, 0)),
    "cadence": cadence,
    "instrument_code": "000001.SZ",
    "event": event,
    "portfolio_state": {
      "account": {"available_cash": 100_000, "total_asset": 100_000},
      "positions": {
        "000001.SZ": {
          "long_volume": 0,
          "available_volume": 0,
          "market_value": 0.0,
          "last_price": price,
        }
      },
    },
    "bucket_ledger": {"instruments": {"000001.SZ": {}}},
    "risk_caps": {"allow_buy": True, "allow_sell": True, "max_position_pct": 0.7},
    "position_profile": {
      "allow_bucket_buy": {"core": True, "swing": True},
      "allow_bucket_sell": {"core": True, "swing": True},
      "min_position_pct": 0.0,
      "max_position_pct": 0.7,
      "core_share_min": 0.6,
      "core_share_max": 0.95,
      "swing_max_pct": 0.15,
      "target_cash_buffer_pct": 0.25,
      "balance_beta_multiplier": 1.0,
      "inventory_gamma_multiplier": 1.0,
    },
  }
  base.update(overrides)
  return StrategyInput(**base)


@pytest.mark.asyncio
async def test_dynamic_strategy_builds_core_intent_after_daily_confirmation():
  strategy = AshareDynamicBalanceDualBucketStrategy(_context())
  await strategy.on_init()

  output = None
  for idx in range(35):
    close = 9.0 + idx * 0.04
    if idx == 34:
      close = 9.7
    output = await strategy.step(
      _input(strategy, StrategyCadence.BAR, _bar(idx, close, volume=130_000))
    )

  assert output is not None
  assert output.runtime_state_patch is not None
  assert output.runtime_state_patch.set["benchmark_price"] > 0
  assert output.runtime_state_patch.set["target_core_pct"] >= 0
  assert any(intent.bucket == "core" for intent in output.trade_intents)


@pytest.mark.asyncio
async def test_dynamic_warmup_primes_daily_window_before_backtest_start():
  strategy = AshareDynamicBalanceDualBucketStrategy(_context())
  await strategy.on_init()

  for idx in range(20):
    await strategy.warmup(
      _input(strategy, StrategyCadence.BAR, _bar(idx, 9.0 + idx * 0.02))
    )

  output = await strategy.step(
    _input(strategy, StrategyCadence.BAR, _bar(20, 9.5, volume=130_000))
  )

  assert len(strategy._bars) == 21
  assert "warming_up" not in output.decision_tags
  assert output.runtime_state_patch is not None
  assert output.runtime_state_patch.set["benchmark_price"] > 0


@pytest.mark.asyncio
async def test_dynamic_strategy_blocks_swing_buy_in_downtrend():
  strategy = AshareDynamicBalanceDualBucketStrategy(_context())
  await strategy.on_init()
  strategy._last_daily_confirm = {
    "benchmark_price": 10.0,
    "grid_step_pct": 0.01,
    "trend_state": BalanceTrendState.DOWNTREND,
    "position_phase": BalancePositionPhase.BALANCED_RUN,
    "targets": BalanceTargets(
      signal=0.5,
      target_total_pct=0.5,
      target_core_pct=0.35,
      target_swing_pct=0.15,
      locked_core_pct=0.0,
      core_share=0.7,
    ),
  }

  tick = Tick(
    stock_code="000001.SZ",
    period="tick",
    time=datetime(2024, 1, 2, 10, 0),
    last_price=9.6,
    open=10.0,
    high=10.0,
    low=9.6,
    last_close=10.0,
    amount=1_000_000,
    volume=100_000,
    pvolume=100_000,
    tickvol=100,
    stock_status=0,
    open_int=0,
    last_settlement_price=0.0,
    settlement_price=0.0,
    transaction_num=10,
  )
  output = await strategy.step(_input(strategy, StrategyCadence.TICK, tick))

  assert output.trade_intents == []
  assert BalanceTrendState.DOWNTREND in output.decision_tags


@pytest.mark.asyncio
async def test_dynamic_strategy_rejects_unbound_instrument():
  strategy = AshareDynamicBalanceDualBucketStrategy(_context())
  await strategy.on_init()

  output = await strategy.step(
    _input(
      strategy,
      StrategyCadence.BAR,
      _bar(1, 10.0),
      instrument_code="600000.SH",
    )
  )

  assert output.trade_intents == []
  assert "instrument_mismatch" in output.decision_tags


@pytest.mark.asyncio
async def test_dynamic_strategy_does_not_directionally_sell_locked_core():
  strategy = AshareDynamicBalanceDualBucketStrategy(_context())
  await strategy.on_init()
  event = _bar(40, 10.0)
  input_obj = _input(
    strategy,
    StrategyCadence.BAR,
    event,
    bucket_ledger={
      "instruments": {
        "000001.SZ": {
          "locked_core": {"total_volume": 5000, "available_volume": 5000},
          "core": {"total_volume": 0, "available_volume": 0},
          "swing": {"total_volume": 0, "available_volume": 0},
        }
      }
    },
    portfolio_state={
      "account": {"available_cash": 50_000, "total_asset": 100_000},
      "positions": {
        "000001.SZ": {
          "long_volume": 5000,
          "available_volume": 5000,
          "market_value": 50_000,
          "last_price": 10.0,
        }
      },
    },
  )
  targets = BalanceTargets(
    signal=-0.8,
    target_total_pct=0.1,
    target_core_pct=0.0,
    target_swing_pct=0.0,
    locked_core_pct=0.5,
    core_share=0.8,
  )

  intents = strategy._core_rebalance_intents(
    input_obj,
    price=10.0,
    targets=targets,
    phase=BalancePositionPhase.DEFENSIVE,
  )

  assert intents == []


@pytest.mark.asyncio
async def test_dynamic_strategy_updates_grid_state_only_after_trade_fill():
  strategy = AshareDynamicBalanceDualBucketStrategy(_context())
  await strategy.on_init()
  strategy._last_daily_confirm = {
    "benchmark_price": 10.0,
    "grid_step_pct": 0.01,
    "trend_state": BalanceTrendState.NEUTRAL,
    "position_phase": BalancePositionPhase.BALANCED_RUN,
    "targets": BalanceTargets(
      signal=0.5,
      target_total_pct=0.5,
      target_core_pct=0.35,
      target_swing_pct=0.15,
      locked_core_pct=0.0,
      core_share=0.7,
    ),
  }
  tick = Tick(
    stock_code="000001.SZ",
    period="tick",
    time=datetime(2024, 1, 2, 10, 0),
    last_price=9.8,
    open=10.0,
    high=10.0,
    low=9.8,
    last_close=10.0,
    amount=1_000_000,
    volume=100_000,
    pvolume=100_000,
    tickvol=100,
    stock_status=0,
    open_int=0,
    last_settlement_price=0.0,
    settlement_price=0.0,
    transaction_num=10,
  )

  output = await strategy.step(_input(strategy, StrategyCadence.TICK, tick))

  assert len(output.trade_intents) == 1
  grid_index = output.trade_intents[0].metadata["grid_index"]
  assert strategy.state.get("last_filled_grid_index") == 0

  await strategy.on_trade(
    TradeExecutionEvent(
      order_id="order-1",
      instrument_code="000001.SZ",
      trade_type="BUY",
      price=9.8,
      volume=300,
      metadata={"grid_index": grid_index},
    )
  )

  assert strategy.state.get("last_filled_grid_index") == grid_index


@pytest.mark.asyncio
async def test_dynamic_strategy_blocks_grid_when_expected_profit_too_low():
  strategy = AshareDynamicBalanceDualBucketStrategy(
    _context({"min_expected_profit_bps": 200})
  )
  await strategy.on_init()
  strategy._last_daily_confirm = {
    "benchmark_price": 10.0,
    "grid_step_pct": 0.01,
    "trend_state": BalanceTrendState.NEUTRAL,
    "position_phase": BalancePositionPhase.BALANCED_RUN,
    "targets": BalanceTargets(
      signal=0.5,
      target_total_pct=0.5,
      target_core_pct=0.35,
      target_swing_pct=0.15,
      locked_core_pct=0.0,
      core_share=0.7,
    ),
  }
  tick = Tick(
    stock_code="000001.SZ",
    period="tick",
    time=datetime(2024, 1, 2, 10, 0),
    last_price=9.8,
    open=10.0,
    high=10.0,
    low=9.8,
    last_close=10.0,
    amount=1_000_000,
    volume=100_000,
    pvolume=100_000,
    tickvol=100,
    stock_status=0,
    open_int=0,
    last_settlement_price=0.0,
    settlement_price=0.0,
    transaction_num=10,
  )

  output = await strategy.step(_input(strategy, StrategyCadence.TICK, tick))

  assert output.trade_intents == []
