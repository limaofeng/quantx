"""Decision continuity across JSON checkpoints and failed report rollback."""

import json
from copy import deepcopy
from datetime import datetime, timedelta

import pandas as pd
import pytest
from quantx_domain.indicators import ATR, EMA
from quantx_domain.market import KLine, Tick
from quantx_domain.strategies.ashare_dynamic_balance_dual_bucket import (
  AshareDynamicBalanceDualBucketStrategy,
)
from quantx_domain.strategies.ashare_supermarket import AshareSupermarketStrategy
from quantx_domain.strategies.base import (
  StrategyCadence,
  StrategyContext,
  StrategyInput,
  StrategyRunMode,
  TradeExecutionEvent,
)
from quantx_domain.strategies.pullback_grid import PullbackGridStrategy
from quantx_domain.trading.bar_timing import resolve_bar_timing
from quantx_engine.strategy_executor import StrategyExecutor, StrategyRuntime
from quantx_infrastructure.core.runtime_state_manager import RuntimeStateManager


def context(parameters=None, instruments=None):
  return StrategyContext(
    run_id="recovery-run",
    mode=StrategyRunMode.BACKTEST,
    instruments=instruments or ["000001.SZ"],
    parameters=parameters or {},
    initial_capital=100_000,
  )


def bar(day, close, code="000001.SZ"):
  return KLine(
    stock_code=code,
    period="1d",
    time=datetime(2024, 1, 1) + timedelta(days=day),
    open=close,
    high=close * 1.02,
    low=close * 0.98,
    close=close,
    volume=100_000,
    amount=100_000 * close,
  )


def tick(price, minute=0):
  return Tick(
    stock_code="000001.SZ", time=datetime(2024, 2, 10, 10, minute), last_price=price
  )


def strategy_input(strategy, event, **updates):
  is_bar = isinstance(event, KLine)
  values = dict(
    run_id=strategy.context.run_id,
    strategy_id="recovery",
    timestamp=resolve_bar_timing(event).available_at if is_bar else event.time,
    cadence=StrategyCadence.BAR if is_bar else StrategyCadence.TICK,
    instrument_code=event.stock_code,
    event=event,
    portfolio_state={
      "account": {"available_cash": 100_000, "total_asset": 100_000},
      "positions": {},
    },
    bucket_ledger={"instruments": {"000001.SZ": {}}},
    risk_caps={"allow_buy": True, "allow_sell": True, "max_position_pct": 0.7},
    position_profile={
      "allow_bucket_buy": {"core": True, "swing": True},
      "allow_bucket_sell": {"core": True, "swing": True},
      "min_position_pct": 0.0,
      "max_position_pct": 0.7,
      "core_share_min": 0.6,
      "core_share_max": 0.95,
      "swing_max_pct": 0.15,
      "target_cash_buffer_pct": 0.25,
    },
  )
  values.update(updates)
  return StrategyInput(**values)


def checkpoint(strategy):
  return json.loads(json.dumps(strategy.persistence_state_snapshot(), allow_nan=False))


async def restored_strategy(original):
  restored = type(original)(deepcopy(original.context))
  restored.apply_state_snapshot(checkpoint(original))
  await restored.start()
  return restored


def decision(output):
  # Intent identities are newly allocated; compare the executable decision.
  return (
    output.decision_tags,
    [
      (
        intent.instrument_code,
        intent.direction,
        intent.bucket,
        intent.reason,
        intent.target_volume,
        intent.target_position_pct,
        intent.limit_price_hint,
        intent.metadata.get("sell_all"),
        intent.metadata.get("lowest_price"),
        intent.metadata.get("grid_id"),
        intent.metadata.get("grid_index"),
      )
      for intent in output.trade_intents
    ],
  )


@pytest.mark.parametrize("indicator_type", [EMA, ATR])
@pytest.mark.parametrize("warm_count", [3, 37, 180])
def test_recursive_indicator_checkpoint_matches_continuous_calculation(
  indicator_type, warm_count
):
  indicator = indicator_type(7)
  for day in range(warm_count):
    indicator.update(bar(day, 10 + day * 0.017 + (day % 5) * 0.08))
  saved = json.loads(json.dumps(indicator.snapshot_state(), allow_nan=False))
  restored = indicator_type(7)
  restored.restore_state(saved)
  for day in range(warm_count, warm_count + 20):
    event = bar(day, 12 + day * 0.019 - (day % 3) * 0.15)
    indicator.update(event)
    restored.update(event)
    assert indicator.get_current_value() == restored.get_current_value()
    assert indicator.snapshot_state() == restored.snapshot_state()


def test_indicator_restore_rejects_changed_period_and_corrupt_seed():
  indicator = EMA(3)
  for day in range(5):
    indicator.update(bar(day, 10 + day))
  saved = indicator.snapshot_state()
  with pytest.raises(ValueError, match="CONFIG_MISMATCH"):
    EMA(4).restore_state(saved)
  saved["previous_ema"] = float("nan")
  with pytest.raises(ValueError, match="ACCUMULATOR_INVALID"):
    EMA(3).restore_state(saved)


def candidates(strategy, codes):
  strategy.set_candidates(
    pd.DataFrame(
      [
        {
          "code": code,
          "box_support": 100.0,
          "box_resistance": 110.0,
          "box_valid": True,
          "structure_ok": True,
        }
        for code in codes
      ]
    )
  )


def fill(side="BUY", volume=100, price=100.0, *, metadata=None, at=None):
  return TradeExecutionEvent(
    order_id="reported-order",
    instrument_code="000001.SZ",
    trade_type=side,
    price=price,
    volume=volume,
    trade_time=at or datetime(2024, 2, 10, 10, 5),
    metadata=metadata or {},
  )


@pytest.mark.asyncio
async def test_supermarket_restores_holdings_risk_pending_entries_and_box_history():
  codes = ["000001.SZ", "000002.SZ"]
  strategy = AshareSupermarketStrategy(context({"target_positions": 2}, codes))
  await strategy.start()
  candidates(strategy, codes)
  for day in range(4):
    await strategy.warmup(strategy_input(strategy, bar(day, 100 + day)))
  await strategy.on_trade(fill(volume=200, at=datetime(2024, 1, 5, 10)))
  for _ in range(3):
    strategy.record_trade_result(-0.03)
  pending = await strategy.step(strategy_input(strategy, bar(5, 101, codes[1])))
  assert len(pending.trade_intents) == 1
  restored = await restored_strategy(strategy)
  assert (
    checkpoint(restored)["decision_memory"] == checkpoint(strategy)["decision_memory"]
  )
  assert restored.loss_streak == 3
  assert restored.tracked_positions[codes[0]].volume == 200
  assert restored.pending_entry_codes == {codes[1]}

  for event in [bar(6, 95), bar(6, 101, codes[1])]:
    original_output = await strategy.step(strategy_input(strategy, event))
    restored_output = await restored.step(strategy_input(restored, event))
    assert decision(original_output) == decision(restored_output)
    assert (
      checkpoint(restored)["decision_memory"] == checkpoint(strategy)["decision_memory"]
    )
    if event.stock_code == codes[0]:
      assert original_output.trade_intents[0].metadata["sell_all"] is True
  await strategy.on_trade(
    fill("SELL", volume=200, price=95, at=datetime(2024, 1, 8, 10))
  )
  patch = await restored.on_trade(
    fill("SELL", volume=200, price=95, at=datetime(2024, 1, 8, 10))
  )
  assert patch.set["decision_memory"] == checkpoint(strategy)["decision_memory"]


@pytest.mark.asyncio
async def test_supermarket_unknown_position_history_blocks_new_entries():
  strategy = AshareSupermarketStrategy(context())
  await strategy.start()
  candidates(strategy, strategy.context.instruments)
  output = await strategy.step(
    strategy_input(
      strategy,
      bar(5, 101),
      portfolio_state={
        "positions": {"000001.SZ": {"long_volume": 100, "long_avg_price": 100}}
      },
    )
  )
  assert output.trade_intents == []
  assert output.decision_tags == ["position_history_reconcile_required"]


@pytest.mark.asyncio
@pytest.mark.parametrize("warm_count", [7, 35])
async def test_dynamic_balance_restores_partial_and_confirmed_daily_memory(warm_count):
  strategy = AshareDynamicBalanceDualBucketStrategy(context())
  await strategy.start()
  for day in range(warm_count):
    await strategy.warmup(strategy_input(strategy, bar(day, 9 + day * 0.04)))
  restored = await restored_strategy(strategy)
  assert checkpoint(strategy) == checkpoint(restored)
  saw_intent = False
  for day in range(warm_count, warm_count + 22):
    event = bar(day, 9.7 if day % 3 else 9.4)
    left = await strategy.step(strategy_input(strategy, event))
    right = await restored.step(strategy_input(restored, event))
    assert decision(left) == decision(right)
    assert checkpoint(strategy) == checkpoint(restored)
    saw_intent = saw_intent or bool(left.trade_intents)
  assert saw_intent


@pytest.mark.asyncio
async def test_dynamic_balance_cannot_trade_from_display_summary_without_daily_memory():
  strategy = AshareDynamicBalanceDualBucketStrategy(context())
  strategy.apply_state_snapshot({"benchmark_price": 10.0, "target_swing_pct": 0.15})
  await strategy.start()
  output = await strategy.step(strategy_input(strategy, tick(9.0)))
  assert output.trade_intents == []
  assert output.trace_payload["reason"] == "daily_confirmation_required"


def grid_strategy():
  return PullbackGridStrategy(
    context(
      {
        "trend_ema_period": 3,
        "fast_ema_period": 2,
        "atr_period": 2,
        "pullback_confirm_pct": 0.01,
        "instrument_code": "000001.SZ",
        "initial_swing_shares": 1000,
        "avg_cost": 10.0,
        "grid_levels": [
          {"id": "buy-1", "levelIndex": 1, "side": "BUY", "price": 10.0, "shares": 100}
        ],
      }
    )
  )


async def warm_grid(strategy):
  await strategy.start()
  for day in range(4):
    await strategy.warmup(strategy_input(strategy, bar(day, 9 + day)))
  await strategy.step(strategy_input(strategy, bar(4, 13)))


@pytest.mark.asyncio
async def test_grid_restores_ema_atr_and_touch_low_before_rebound_decision():
  strategy = grid_strategy()
  await warm_grid(strategy)
  await strategy.step(strategy_input(strategy, tick(10.0)))
  await strategy.step(strategy_input(strategy, tick(9.8, 1)))
  restored = await restored_strategy(strategy)
  assert restored.grids[0].volume == 100
  assert restored.grids[0].lowest_price_since_touch == 9.8
  assert restored.grids[0].touch_time == strategy.grids[0].touch_time
  assert (
    checkpoint(strategy)["decision_memory"] == checkpoint(restored)["decision_memory"]
  )
  left = await strategy.step(strategy_input(strategy, tick(9.95, 2)))
  right = await restored.step(strategy_input(restored, tick(9.95, 2)))
  assert len(left.trade_intents) == 1
  assert decision(left) == decision(right)


@pytest.mark.asyncio
async def test_grid_restores_partial_fill_pending_volume_and_does_not_reseed_empty_book():
  strategy = grid_strategy()
  await warm_grid(strategy)
  await strategy.step(strategy_input(strategy, tick(10.0)))
  await strategy.step(strategy_input(strategy, tick(10.2, 1)))
  await strategy.on_trade(
    fill(volume=40, price=10.2, metadata={"grid_id": "buy-1", "trade_id": "fill-1"})
  )
  restored = await restored_strategy(strategy)
  assert restored.grids[0].is_pending is True
  assert restored.grids[0].pending_volume == 60
  assert restored.grids[0].filled_volume == 40
  for instance in (strategy, restored):
    await instance.on_trade(
      fill(volume=60, price=10.1, metadata={"grid_id": "buy-1", "trade_id": "fill-2"})
    )
  assert restored.grids[0].entry_price == strategy.grids[0].entry_price
  assert restored.grids[0].is_filled is True
  assert sum(lot.remaining_shares for lot in restored.inventory_lots) == sum(
    lot.remaining_shares for lot in strategy.inventory_lots
  )

  strategy.grids = []
  strategy.inventory_lots = []
  strategy._sync_grid_book_state("empty_after_execution")
  emptied = await restored_strategy(strategy)
  assert emptied.grids == []
  assert emptied.inventory_lots == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "factory", [lambda: AshareSupermarketStrategy(context()), grid_strategy]
)
async def test_durable_report_rollback_restores_private_caches_as_well_as_state(
  factory,
):
  strategy = factory()
  await strategy.start()
  expected = checkpoint(strategy)
  executor = StrategyExecutor()
  runtime = StrategyRuntime(
    run_id=strategy.context.run_id,
    name="rollback",
    strategy_id=1,
    strategy_class=type(strategy),
    context=strategy.context,
    strategy=strategy,
    state_manager=RuntimeStateManager(strategy.context.run_id, persist_enabled=False),
  )
  before = executor._capture_durable_runtime_state(runtime)
  try:
    await strategy.on_trade(
      fill(price=10, metadata={"grid_id": "buy-1", "trade_id": "failed-fill"})
    )
    assert checkpoint(strategy) != expected
    executor._restore_durable_runtime_state(runtime, before)
    assert checkpoint(strategy) == expected
    if isinstance(strategy, AshareSupermarketStrategy):
      assert strategy.tracked_positions == {}
    else:
      assert strategy.grids[0].filled_volume == 0
      assert sum(lot.remaining_shares for lot in strategy.inventory_lots) == 1000
  finally:
    executor.thread_pool.shutdown(wait=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "factory",
  [
    lambda: AshareSupermarketStrategy(context()),
    lambda: AshareDynamicBalanceDualBucketStrategy(context()),
    grid_strategy,
  ],
)
async def test_restored_daily_warmup_does_not_count_existing_bars_again(factory):
  strategy = factory()
  await strategy.start()
  for day in range(4):
    await strategy.warmup(strategy_input(strategy, bar(day, 10 + day)))
  restored = await restored_strategy(strategy)
  before = checkpoint(restored)["decision_memory"]
  for day in range(4):
    await restored.warmup(strategy_input(restored, bar(day, 100 + day)))
  assert checkpoint(restored)["decision_memory"] == before


@pytest.mark.asyncio
async def test_grid_restore_rejects_incomplete_active_touch_state():
  strategy = grid_strategy()
  await warm_grid(strategy)
  await strategy.step(strategy_input(strategy, tick(9.8)))
  saved = checkpoint(strategy)
  del saved["grid_book_snapshot"]["levels"][0]["lowest_price_since_touch"]
  restored = grid_strategy()
  restored.apply_state_snapshot(saved)
  with pytest.raises(ValueError, match="GRID_MONITORING_STATE_INCOMPLETE"):
    await restored.start()


@pytest.mark.asyncio
async def test_grid_missing_inventory_snapshot_cannot_fall_back_to_initial_holdings():
  strategy = grid_strategy()
  await strategy.start()
  saved = checkpoint(strategy)
  del saved["grid_book_snapshot"]["inventory_lots"]
  restored = grid_strategy()
  restored.apply_state_snapshot(saved)
  with pytest.raises(KeyError, match="inventory_lots"):
    await restored.start()


@pytest.mark.asyncio
async def test_restored_dynamic_grids_keep_their_generation_policy():
  strategy = PullbackGridStrategy(
    context(
      {
        "trend_ema_period": 3,
        "fast_ema_period": 2,
        "atr_period": 2,
        "grid_count": 3,
        "position_per_grid": 100,
      }
    )
  )
  await warm_grid(strategy)
  assert strategy.grids and strategy._has_external_grid_plan is False
  restored = await restored_strategy(strategy)
  assert restored._has_external_grid_plan is False
  for instance in (strategy, restored):
    instance.grids = []
    instance._sync_grid_book_state("cleared")
    await instance.step(strategy_input(instance, bar(5, 14)))
  assert len(strategy.grids) == len(restored.grids) == 3
  assert [grid.trigger_price for grid in strategy.grids] == [
    grid.trigger_price for grid in restored.grids
  ]
