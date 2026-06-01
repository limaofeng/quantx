"""
Pullback Grid 策略真实回测测试

使用 InfluxDB 中的真实历史数据进行回测，不使用 Mock。
测试路径: StrategyManager -> StrategyExecutor -> HistoricalDataAdapter -> BacktestBroker

数据要求:
- InfluxDB 中需要有近 30 天的 kline_1d 数据
- 默认标的: 002594.SZ (可按需替换)

说明:
- 走真实回测路径，回放为统一时间线处理
"""

import asyncio
import json
import logging
from datetime import timedelta
from typing import Any, Dict

import pytest
from sqlalchemy import select

from core.strategies.base import StrategyRunMode, StrategyContext
from core.strategies.pullback_grid import PullbackGridStrategy
from core.strategy_manager import StrategyManager
from core.strategy_executor import ExecutionStatus
from core.utils import time_utils
from repositories.strategy_repository import StrategyRepository
from database.connection import get_async_db
from models.strategy_run import StrategyRun


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
  return {
    "run_name": "PullbackGrid-RealBacktest",
    "stock_code": "688552.SH",
    "period": "1d",
    "lookback_days": 30,
    "initial_capital": 1_000_000.0,
    # Pullback Grid 参数(可按需调整)
    "trend_ema_period": 20,
    "fast_ema_period": 5,
    "pullback_confirm_pct": 0.02,
    # 前端生成的网格（示例，避免触发交易）
    "grid_levels": [
      {"id": "buy-1", "levelIndex": -1, "side": "BUY", "price": 40, "shares": 100},
      {"id": "sell-1", "levelIndex": 1, "side": "SELL", "price": 45, "shares": 100},
    ],
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


async def _get_run_by_name(name: str) -> StrategyRun:
  """获取指定名称的策略运行实例（优先最新一条）"""
  async for db in get_async_db():
    result = await db.execute(
      select(StrategyRun)
      .filter(StrategyRun.name == name)
      .order_by(StrategyRun.created_at.desc())
    )
    run = result.scalar_one_or_none()
    if run:
      return run
  raise RuntimeError(f"未找到策略运行实例: {name}")


def _ensure_params_dict(params) -> Dict[str, Any]:
  if isinstance(params, str):
    try:
      return json.loads(params)
    except json.JSONDecodeError:
      return {}
  return params or {}


def _load_runtime_from_db(
  strategy_manager: StrategyManager,
  run: StrategyRun,
  strategy_class: type,
  backtest_start_time,
  backtest_end_time,
  param_overrides: Dict[str, Any] | None = None,
) -> None:
  """将数据库中的运行实例加载进内存执行器"""
  params = _ensure_params_dict(run.parameters)
  if param_overrides:
    params.update(param_overrides)
  context = StrategyContext(
    run_id=run.id,
    mode=run.mode,
    instruments=run.instruments or [],
    parameters=params,
    initial_capital=run.initial_capital or 1000000.0,
    backtest_start_time=backtest_start_time,
    backtest_end_time=backtest_end_time,
  )

  strategy_manager.executor.create(
    run_id=run.id,
    name=run.name,
    strategy_id=run.strategy_id,
    strategy_class=strategy_class,
    context=context,
  )


@pytest.mark.integration
@pytest.mark.slow
class TestPullbackGridRealBacktest:
  """Pullback Grid 策略真实回测测试"""

  @pytest.mark.asyncio
  async def test_real_backtest_run(
    self,
    strategy_manager: StrategyManager,
    backtest_config: Dict[str, Any],
  ):
    await _require_test_database()

    # 0. 同步策略模板到数据库（跳过恢复运行实例）
    await strategy_manager._sync_strategies()
    strategy_id = await _get_strategy_id_by_class("PullbackGridStrategy")

    # 1. 创建回测运行（仅创建任务，不启动）
    end_time = time_utils.now() - timedelta(days=1)
    start_time = end_time - timedelta(days=backtest_config["lookback_days"])
    
    run_id = await strategy_manager.run_strategy(
      strategy_id=strategy_id,
      strategy_class=PullbackGridStrategy,
      mode=StrategyRunMode.BACKTEST,
      instruments=[backtest_config["stock_code"]],
      name=backtest_config["run_name"],
      parameters={
        "initial_capital": backtest_config["initial_capital"],
        "periods": [backtest_config["period"]],
        "trend_ema_period": backtest_config["trend_ema_period"],
        "fast_ema_period": backtest_config["fast_ema_period"],
        "pullback_confirm_pct": backtest_config["pullback_confirm_pct"],
        "grid_levels": backtest_config["grid_levels"],
      },
      backtest_start_time=start_time,
      backtest_end_time=end_time,
      auto_start=False,
    )

    assert run_id, "策略创建失败"

    # 2. 获取运行结果
    runtime = strategy_manager.get_run(run_id)
    assert runtime is not None, "无法获取策略运行时信息"

  @pytest.mark.asyncio
  async def test_real_backtest_execute(
    self,
    strategy_manager: StrategyManager,
    backtest_config: Dict[str, Any],
  ):
    await _require_test_database()

    # 0. 同步策略模板到数据库（跳过恢复运行实例）
    await strategy_manager._sync_strategies()
    strategy_id = await _get_strategy_id_by_class("PullbackGridStrategy")

    # 1. 设置回测时间范围（不在测试前检测数据可用性）
    end_time = time_utils.now() - timedelta(days=1)
    start_time = end_time - timedelta(days=backtest_config["lookback_days"])

    # 2. 使用已创建的待启动实例
    run = await _get_run_by_name(backtest_config["run_name"])
    _load_runtime_from_db(
      strategy_manager,
      run,
      PullbackGridStrategy,
      start_time,
      end_time,
      param_overrides={"periods": [backtest_config["period"]]},
    )

    started = await strategy_manager.start_strategy(run.id)
    assert started, "策略启动失败"

    # 3. 等待回测执行完成（回放耗时较久，测试不设置超时）
    runtime = strategy_manager.get_run(run.id)
    assert runtime is not None, "无法获取策略运行时信息"
    assert runtime.task is not None, "策略任务未创建"
    await runtime.task

    # 让事件队列处理完剩余数据
    await asyncio.sleep(0.2)

    # 5. 获取运行结果
    runtime = strategy_manager.get_run(run.id)
    assert runtime is not None, "无法获取策略运行时信息"
    assert runtime.error_message is None, f"策略运行出错: {runtime.error_message}"
    assert runtime.status == ExecutionStatus.COMPLETED, f"策略状态应为 COMPLETED, 实际为 {runtime.status}"

    # Verify DB status
    updated_run = await _get_run_by_name(backtest_config["run_name"])
    assert updated_run.status == "completed", f"数据库状态应为 completed, 实际为 {updated_run.status}"

    broker = runtime.broker
    assert broker is not None, "Broker 未初始化"

    metrics = broker.get_performance_metrics()
    assert metrics is not None, "应有回测绩效指标"

    # 6. 基础指标验证
    assert len(getattr(broker, "equity_curve", [])) > 0, "权益曲线为空"

    # 7. 清理
    await strategy_manager.stop_strategy(run.id)
