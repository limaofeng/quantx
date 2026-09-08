import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from quantx_domain.strategies.base import StrategyRunMode
from quantx_engine.strategy_executor import ExecutionStatus
from quantx_engine.strategy_manager import StrategyManager


def preparation_manager(error):
  runtime = SimpleNamespace(
    context=SimpleNamespace(mode=StrategyRunMode.BACKTEST, backtest_id="backtest"),
    status=ExecutionStatus.PENDING,
    error_message=None,
  )
  manager = StrategyManager.__new__(StrategyManager)
  manager.logger = logging.getLogger(__name__)
  manager.executor = SimpleNamespace(get=Mock(return_value=runtime), start=AsyncMock())
  manager._ensure_backtest_data_available = AsyncMock(side_effect=error)
  manager._finalize_t_trade_replay_initial_portfolio = AsyncMock()
  manager._set_t_trade_replay_phase = AsyncMock()
  manager._update_runtime_status = AsyncMock()
  manager._mark_backtest_error_safely = AsyncMock()
  return manager, runtime


@pytest.mark.parametrize(
  "error", [httpx.ConnectError("source unavailable"), ValueError("invalid evidence")]
)
async def test_preparation_failure_marks_both_records_failed(error):
  manager, runtime = preparation_manager(error)
  assert not await manager.start_strategy("run")
  assert runtime.status is ExecutionStatus.ERROR
  manager._update_runtime_status.assert_awaited_once_with("run", "ERROR", str(error))
  manager._mark_backtest_error_safely.assert_awaited_once_with("backtest", str(error))
  manager.executor.start.assert_not_awaited()


async def test_preparation_cancellation_propagates():
  manager, _ = preparation_manager(asyncio.CancelledError())
  with pytest.raises(asyncio.CancelledError):
    await manager.start_strategy("run")
  manager._mark_backtest_error_safely.assert_not_awaited()
  manager.executor.start.assert_not_awaited()
