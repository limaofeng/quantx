"""
Ashare Supermarket 策略真实回测测试

使用 InfluxDB 中的真实历史数据进行回测，不使用 Mock。
测试路径: StrategyManager -> StrategyExecutor -> HistoricalDataAdapter -> BacktestBroker

数据要求:
- 可连接异步测试数据库
- InfluxDB 中需要有回测区间内的 1d 历史数据
- 默认标的: 688552.SH
"""

import asyncio
import logging
import os
from datetime import datetime, time, timedelta
from typing import Any, Dict, List

import pytest
from sqlalchemy import select

from core.strategies.ashare_supermarket import AshareSupermarketStrategy
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
  """创建策略管理器实例(真实实例,不 Mock)"""
  manager = StrategyManager()
  yield manager
  await manager.stop()


@pytest.fixture
def backtest_config() -> Dict[str, Any]:
  """回测配置参数"""
  stocks = [
    code.strip()
    for code in os.getenv(
      "ASHARE_SUPERMARKET_REAL_BACKTEST_STOCKS", "688552.SH"
    ).split(",")
    if code.strip()
  ]
  lookback_days = int(
    os.getenv("ASHARE_SUPERMARKET_REAL_BACKTEST_LOOKBACK_DAYS", "30")
  )
  return {
    "run_name": "AshareSupermarket-RealBacktest",
    "stock_codes": stocks,
    "lookback_days": lookback_days,
    "initial_capital": 1_000_000.0,
    "target_positions": 5,
    "min_position_pct": 0.02,
    "max_position_pct": 0.06,
    "buy_threshold_pct": 0.02,
    "box_window_daily": 10,
    "time_stop_bars_daily": 10,
  }


async def _get_strategy_id_by_class(class_name: str) -> int:
  """从数据库获取策略ID"""
  async for db in get_async_db():
    repo = StrategyRepository(db)
    strategy = await repo.find_by_class_name(class_name)
    if strategy:
      return strategy.id
  raise RuntimeError(f"策略未同步到数据库: {class_name}")


async def _require_test_database() -> None:
  """真实回测集成测试需要外部异步数据库；不可用时跳过。"""
  try:
    async for db in get_async_db():
      await db.execute(select(1))
      return
  except Exception as exc:
    pytest.skip(f"需要可连接的异步测试数据库: {exc}")


async def _require_real_backtest_data(
  strategy_manager: StrategyManager,
  instruments: List[str],
  start_time: datetime,
  end_time: datetime,
) -> None:
  """真实回测集成测试需要外部历史行情；不可用时跳过。"""
  try:
    service = HistoricalMarketDataService()
    missing = await strategy_manager._find_missing_backtest_data(
      service=service,
      instruments=instruments,
      start_time=start_time.replace(hour=9, minute=30, second=0, microsecond=0),
      end_time=end_time.replace(hour=15, minute=30, second=0, microsecond=0),
      required_kline_periods={"1d"},
      require_tick=False,
    )
  except Exception as exc:
    pytest.skip(f"需要可读取的真实历史行情数据: {exc}")

  if missing:
    pytest.skip(
      "真实历史行情数据不完整: "
      f"{strategy_manager._format_missing_data(missing)}"
    )


async def _get_run_by_id(run_id: str) -> StrategyRun:
  """获取指定策略运行实例"""
  async for db in get_async_db():
    result = await db.execute(select(StrategyRun).filter(StrategyRun.id == run_id))
    run = result.scalar_one_or_none()
    if run:
      return run
  raise RuntimeError(f"未找到策略运行实例: {run_id}")


async def _wait_run_terminal_status(
  run_id: str,
  *,
  timeout_seconds: float = 10.0,
) -> StrategyRun:
  """等待策略运行状态异步落库完成。"""
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
    await asyncio.sleep(0.2)

  if last_run is not None:
    raise AssertionError(f"等待策略运行终态超时，当前状态: {last_run.status}")
  raise AssertionError(f"等待策略运行终态超时，未找到运行实例: {run_id}")


def _build_backtest_window(lookback_days: int) -> tuple[datetime, datetime]:
  end_time = time_utils.now() - timedelta(days=1)
  start_time = end_time - timedelta(days=lookback_days)
  return start_time, end_time


@pytest.mark.integration
@pytest.mark.slow
class TestAshareSupermarketRealBacktest:
  """Ashare Supermarket 策略真实回测测试"""

  @pytest.mark.asyncio
  async def test_real_backtest_execute(
    self,
    strategy_manager: StrategyManager,
    backtest_config: Dict[str, Any],
  ):
    await _require_test_database()

    await strategy_manager._sync_strategies()
    strategy_id = await _get_strategy_id_by_class("AshareSupermarketStrategy")
    data_requirements = AshareSupermarketStrategy.get_data_requirements()
    assert data_requirements == {"use_tick_data": False, "periods": ["1d"]}

    start_time, end_time = _build_backtest_window(backtest_config["lookback_days"])
    await _require_real_backtest_data(
      strategy_manager,
      backtest_config["stock_codes"],
      datetime.combine(start_time.date(), time(0, 0)),
      datetime.combine(end_time.date(), time(23, 59, 59)),
    )

    run_id = await strategy_manager.run_strategy(
      strategy_id=strategy_id,
      strategy_class=AshareSupermarketStrategy,
      mode=StrategyRunMode.BACKTEST,
      instruments=backtest_config["stock_codes"],
      name=backtest_config["run_name"],
      parameters={
        "initial_capital": backtest_config["initial_capital"],
        "target_positions": backtest_config["target_positions"],
        "min_position_pct": backtest_config["min_position_pct"],
        "max_position_pct": backtest_config["max_position_pct"],
        "buy_threshold_pct": backtest_config["buy_threshold_pct"],
        "box_window_daily": backtest_config["box_window_daily"],
        "time_stop_bars_daily": backtest_config["time_stop_bars_daily"],
      },
      backtest_start_time=start_time,
      backtest_end_time=end_time,
      auto_start=False,
    )
    assert run_id, "策略创建失败"

    created_runtime = strategy_manager.get_run(run_id)
    assert created_runtime is not None, "无法获取已创建的策略运行时信息"
    assert created_runtime.context.mode == StrategyRunMode.BACKTEST, "应采用回测模式"
    assert created_runtime.context.backtest_id, "回测模式应创建 backtest_id"
    assert created_runtime.context.backtest_start_time == start_time
    assert created_runtime.context.backtest_end_time == end_time

    started = await strategy_manager.start_strategy(run_id)
    assert started, "策略启动失败"

    runtime = strategy_manager.get_run(run_id)
    assert runtime is not None, "无法获取策略运行时信息"
    assert runtime.task is not None, "策略任务未创建"
    await runtime.task

    runtime = strategy_manager.get_run(run_id)
    assert runtime is not None, "无法获取策略运行时信息"
    assert runtime.error_message is None, f"策略运行出错: {runtime.error_message}"
    assert runtime.status == ExecutionStatus.COMPLETED, (
      f"策略状态应为 COMPLETED, 实际为 {runtime.status}"
    )

    updated_run = await _wait_run_terminal_status(run_id)
    updated_status = (
      updated_run.status.value
      if hasattr(updated_run.status, "value")
      else str(updated_run.status)
    )
    assert updated_status == "completed", (
      f"数据库状态应为 completed, 实际为 {updated_run.status}"
    )

    broker = runtime.broker
    assert broker is not None, "Broker 未初始化"
    assert broker.__class__.__name__ == "BacktestBroker", "应使用回测 Broker"

    metrics = broker.get_performance_metrics()
    assert metrics is not None, "应有回测绩效指标"
    assert len(getattr(broker, "equity_curve", [])) > 0, "权益曲线为空"
