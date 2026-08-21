from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from quantx_domain.strategies.base import StrategyContext
from quantx_engine.replay_clock import ReplayClock
from quantx_engine.strategy_executor import ExecutionStatus, StrategyExecutor
from quantx_infrastructure.models.enums import StrategyRunMode


def test_replay_clock_advances_monotonically() -> None:
  started_at = datetime(2024, 1, 2, 9, 30)
  clock = ReplayClock(started_at)

  assert clock.now() == started_at
  assert clock.advance_by(timedelta(milliseconds=500)) == datetime(
    2024, 1, 2, 9, 30, 0, 500_000
  )
  assert clock.advance_to(clock.now()) == clock.now()

  with pytest.raises(ValueError, match="cannot move backwards"):
    clock.advance_to(started_at)


def test_replay_clock_normalizes_influx_aware_event_to_exchange_local_time() -> None:
  clock = ReplayClock(datetime(2024, 1, 2, 9, 30))

  assert clock.advance_to(
    datetime(2024, 1, 2, 1, 31, tzinfo=timezone.utc)
  ) == datetime(2024, 1, 2, 9, 31)


def test_replay_clock_normalizes_aware_start_and_has_stable_epoch_ms() -> None:
  shanghai = ZoneInfo("Asia/Shanghai")
  started_at = datetime(2024, 1, 2, 9, 30, tzinfo=shanghai)

  clock = ReplayClock(started_at)

  assert clock.now() == datetime(2024, 1, 2, 9, 30)
  assert clock.now_ms() == int(started_at.timestamp() * 1000)


@pytest.mark.parametrize(
  ("mode", "parameters"),
  [
    (StrategyRunMode.BACKTEST, {}),
    (StrategyRunMode.PAPER, {"t_trade_replay": True}),
    (StrategyRunMode.LIVE, {"t_trade_replay": True}),
  ],
)
def test_replay_event_fail_fast_does_not_expand_to_other_runs(
  mode: StrategyRunMode,
  parameters: dict,
) -> None:
  runtime = SimpleNamespace(
    context=StrategyContext(
      run_id=f"non-strict-{mode.value}",
      mode=mode,
      instruments=["000001.SZ"],
      parameters=parameters,
    )
  )

  assert StrategyExecutor._requires_replay_event_integrity(runtime) is False


@pytest.mark.asyncio
async def test_t_trade_replay_clock_integrity_error_marks_runtime_error() -> None:
  executor = StrategyExecutor(max_workers=1)
  context = StrategyContext(
    run_id="t-replay-clock-error",
    mode=StrategyRunMode.BACKTEST,
    instruments=["000001.SZ"],
    parameters={"t_trade_replay": True},
    backtest_start_time=datetime(2024, 1, 2, 9, 30),
    backtest_end_time=datetime(2024, 1, 2, 15, 0),
  )
  runtime = executor.create(
    run_id=context.run_id,
    strategy_id=1,
    strategy_class=object,
    context=context,
  )
  runtime.status = ExecutionStatus.RUNNING
  runtime.log_manager = None
  runtime.market_data_manager = None
  runtime.strategy = SimpleNamespace(
    initialize=AsyncMock(),
    start=AsyncMock(),
    stop=AsyncMock(),
  )
  runtime.replay_clock = ReplayClock(datetime(2024, 1, 2, 10, 0))
  regressing_tick = SimpleNamespace(
    stock_code="000001.SZ",
    time=datetime(2024, 1, 2, 9, 59),
  )

  async def replay_one_invalid_event(_runtime) -> None:
    await executor._process_tick(runtime, regressing_tick)

  try:
    with patch.object(
      executor,
      "_run_backtest_loop",
      side_effect=replay_one_invalid_event,
    ):
      await executor._run_strategy_loop(runtime)

    assert runtime.status == ExecutionStatus.ERROR
    assert runtime.error_message is not None
    assert "cannot move backwards" in runtime.error_message
    assert runtime.metrics is not None
    assert runtime.metrics.error_count == 1
  finally:
    await executor.shutdown()
