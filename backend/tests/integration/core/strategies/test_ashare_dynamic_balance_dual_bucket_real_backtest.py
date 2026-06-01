"""
Ashare dynamic balance dual bucket strategy real backtest integration test.

Using real historical data from InfluxDB, no mocks.
Execution path: StrategyManager -> StrategyExecutor -> HistoricalDataAdapter -> BacktestBroker

Data requirements:
- async test DB available
- historical bars for 1d and 1m in backtest window
- default code: 688213.SH
"""

import asyncio
import logging
from datetime import datetime, time, timedelta
from typing import Any, Dict, List

import pytest
from sqlalchemy import select

from core.strategies.ashare_dynamic_balance_dual_bucket import (
  AshareDynamicBalanceDualBucketStrategy,
)
from core.strategies.base import StrategyRunMode
from core.strategy_executor import ExecutionStatus
from core.strategy_manager import StrategyManager
from core.utils import time_utils
from database.connection import get_async_db
from models.strategy_run import StrategyRun
from repositories.strategy_repository import StrategyRepository
from services.historical_market_data_service import HistoricalMarketDataService


logging.basicConfig(
  level=logging.INFO,
  force=True,
  format="%(asctime)s | %(levelname)s | %(name)s - %(message)s",
)
logging.getLogger("StrategyExecutor").setLevel(logging.INFO)
logging.getLogger("StrategyManager").setLevel(logging.INFO)


@pytest.fixture
async def strategy_manager():
  """Create real StrategyManager instance (no mock)."""
  manager = StrategyManager()
  yield manager
  await manager.stop()


@pytest.fixture
def backtest_config() -> Dict[str, Any]:
  """Backtest configuration."""
  return {
    "run_name": "AshareDynamicBalanceDualBucket-RealBacktest",
    "stock_code": "688213.SH",
    "lookback_days": 30,
    "initial_capital": 1_000_000.0,
  }


async def _get_strategy_id_by_class(class_name: str) -> int:
  """Get strategy id from db by class name."""
  async for db in get_async_db():
    repo = StrategyRepository(db)
    strategy = await repo.find_by_class_name(class_name)
    if strategy:
      return strategy.id
  raise RuntimeError(f"Strategy not synced in DB: {class_name}")


async def _require_test_database() -> None:
  """Integration backtest requires external async database."""
  try:
    async for db in get_async_db():
      await db.execute(select(1))
      return
  except Exception as exc:
    pytest.skip(f"Need async test database: {exc}")


async def _require_real_backtest_data(
  strategy_manager: StrategyManager,
  instruments: List[str],
  start_time: datetime,
  end_time: datetime,
) -> None:
  """Integration backtest requires readable historical data."""
  try:
    service = HistoricalMarketDataService()
    missing = await strategy_manager._find_missing_backtest_data(
      service=service,
      instruments=instruments,
      start_time=start_time,
      end_time=end_time,
      required_kline_periods={"1m", "1d"},
      require_tick=True,
    )
  except Exception as exc:
    pytest.skip(f"Need readable historical data: {exc}")

  if missing:
    pytest.skip(
      "Real historical data incomplete: "
      f"{strategy_manager._format_missing_data(missing)}"
    )


async def _get_run_by_id(run_id: str) -> StrategyRun:
  """Get strategy run by id."""
  async for db in get_async_db():
    result = await db.execute(select(StrategyRun).filter(StrategyRun.id == run_id))
    run = result.scalar_one_or_none()
    if run:
      return run
  raise RuntimeError(f"Strategy run not found: {run_id}")


async def _wait_run_terminal_status(
  run_id: str,
  *,
  timeout_seconds: float = 20.0,
) -> StrategyRun:
  """Wait until status in DB is terminal state."""
  deadline = asyncio.get_running_loop().time() + timeout_seconds
  last_run = None
  terminal_statuses = {"completed", "stopped", "error", "failed"}

  while asyncio.get_running_loop().time() < deadline:
    last_run = await _get_run_by_id(run_id)
    status_value = (
      last_run.status.value
      if hasattr(last_run.status, "value")
      else str(last_run.status)
    ).lower()
    if status_value in terminal_statuses:
      return last_run
    await asyncio.sleep(0.5)

  if last_run is not None:
    raise AssertionError(f"Wait terminal status timeout. Current: {last_run.status}")
  raise AssertionError(f"Wait terminal status timeout. Run not found: {run_id}")


def _build_backtest_window(lookback_days: int) -> tuple[datetime, datetime]:
  end_time = time_utils.now() - timedelta(days=1)
  start_time = end_time - timedelta(days=lookback_days)
  return start_time, end_time


@pytest.mark.integration
@pytest.mark.slow
class TestAshareDynamicBalanceDualBucketRealBacktest:
  """Ashare Dynamic Balance Dual Bucket real backtest test."""

  @pytest.mark.asyncio
  async def test_real_backtest_execute(
    self,
    strategy_manager: StrategyManager,
    backtest_config: Dict[str, Any],
  ):
    await _require_test_database()

    await strategy_manager._sync_strategies()
    strategy_id = await _get_strategy_id_by_class(
      "AshareDynamicBalanceDualBucketStrategy"
    )
    assert AshareDynamicBalanceDualBucketStrategy.get_data_requirements() == {
      "use_tick_data": True,
      "periods": ["1m", "1d"],
    }

    start_time, end_time = _build_backtest_window(backtest_config["lookback_days"])
    await _require_real_backtest_data(
      strategy_manager,
      [backtest_config["stock_code"]],
      datetime.combine(start_time.date(), time(0, 0)),
      datetime.combine(end_time.date(), time(23, 59, 59)),
    )

    run_id = await strategy_manager.run_strategy(
      strategy_id=strategy_id,
      strategy_class=AshareDynamicBalanceDualBucketStrategy,
      mode=StrategyRunMode.BACKTEST,
      instruments=[backtest_config["stock_code"]],
      name=backtest_config["run_name"],
      parameters={"initial_capital": backtest_config["initial_capital"]},
      backtest_start_time=start_time,
      backtest_end_time=end_time,
      auto_start=False,
    )
    assert run_id, "run_strategy failed"

    created_runtime = strategy_manager.get_run(run_id)
    assert created_runtime is not None, "Cannot get created runtime"
    assert created_runtime.context.mode == StrategyRunMode.BACKTEST, (
      "Mode should be BACKTEST"
    )
    assert created_runtime.context.backtest_id, "Backtest should create backtest_id"
    assert created_runtime.context.backtest_start_time == start_time
    assert created_runtime.context.backtest_end_time == end_time

    started = await strategy_manager.start_strategy(run_id)
    assert started, "Start strategy failed"

    runtime = strategy_manager.get_run(run_id)
    assert runtime is not None, "Runtime not found"
    assert runtime.task is not None, "Strategy task not created"
    await runtime.task

    runtime = strategy_manager.get_run(run_id)
    assert runtime is not None, "Runtime not found"
    assert runtime.error_message is None, f"Strategy failed: {runtime.error_message}"
    assert runtime.status == ExecutionStatus.COMPLETED, (
      f"Status should be COMPLETED, got {runtime.status}"
    )

    updated_run = await _wait_run_terminal_status(run_id)
    updated_status = (
      updated_run.status.value
      if hasattr(updated_run.status, "value")
      else str(updated_run.status)
    )
    assert updated_status == "completed", (
      f"DB status should be completed, got {updated_run.status}"
    )

    broker = runtime.broker
    assert broker is not None, "Broker not initialized"
    assert broker.__class__.__name__ == "BacktestBroker", "Should use backtest broker"

    metrics = broker.get_performance_metrics()
    assert metrics is not None, "Performance metrics should exist"
    assert len(getattr(broker, "equity_curve", [])) > 0, "Equity curve is empty"
