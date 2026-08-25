from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_domain.strategies.base import StrategyContext
from quantx_domain.trading import MarketDataSnapshot
from quantx_engine.strategy_executor import StrategyExecutor
from quantx_infrastructure.models.enums import StrategyRunMode


def _runtime(parameters):
  return SimpleNamespace(
    context=StrategyContext(
      run_id="exit-replay-1",
      mode=StrategyRunMode.BACKTEST,
      instruments=["000001.SZ"],
      parameters=parameters,
    )
  )


def test_exit_plan_replay_fails_fast_on_market_event_errors() -> None:
  assert StrategyExecutor._requires_replay_event_integrity(
    _runtime({"exit_plan_replay": True})
  ) is True


@pytest.mark.asyncio
async def test_exit_plan_replay_does_not_use_t_trade_end_force_close(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  executor = StrategyExecutor()
  wait_for_reports = AsyncMock()
  monkeypatch.setattr(executor, "_wait_for_backtest_reports", wait_for_reports)
  runtime = _runtime({"exit_plan_replay": True})
  runtime.broker = None

  await executor._finalize_t_trade_replay(runtime)

  wait_for_reports.assert_not_awaited()


@pytest.mark.asyncio
async def test_adaptive_exit_replay_rejects_missing_depth() -> None:
  executor = StrategyExecutor()
  rule = SimpleNamespace(
    enabled=True,
    strategy="ADAPTIVE_VOLUME_PRICE_TRAILING",
  )
  plan = SimpleNamespace(
    remaining_volume=100,
    template=SimpleNamespace(
      instrument_code="000001.SZ",
      rules=[rule],
    ),
  )
  runtime = _runtime({"exit_plan_replay": True})
  runtime.exit_plan_book = SimpleNamespace(
    active_plans=lambda: [plan],
    plans={"plan-1": plan},
  )
  market = MarketDataSnapshot(
    instrument_code="000001.SZ",
    timestamp=datetime(2026, 8, 20, 10, 0),
    price=10.0,
    bid_price=[],
    ask_price=[],
    bid_vol=[],
    ask_vol=[],
    source="tick",
  )

  with pytest.raises(RuntimeError, match="EXIT_PLAN_REPLAY_DEPTH_DATA_MISSING"):
    await executor._process_auto_exit_plans(
      runtime,
      instrument_code="000001.SZ",
      timestamp=market.timestamp,
      market_data=market,
    )

