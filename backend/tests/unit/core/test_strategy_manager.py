"""
StrategyManager 单元测试

测试策略管理器的核心功能:
- run_strategy: 创建并运行策略
- start_strategy: 启动策略
- stop_strategy: 停止策略
- pause_strategy: 暂停策略
- resume_strategy: 恢复策略
"""

import asyncio
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.strategy_manager import StrategyManager
from core.strategy_executor import ExecutionStatus, StrategyRuntime
from core.strategies.base import StrategyBase, StrategyContext, StrategyInput, StrategyOutput, StrategyRunMode


class MockStrategy(StrategyBase):
  """测试用的模拟策略"""

  @property
  def name(self) -> str:
    return "MockStrategy"

  @property
  def description(self) -> str:
    return "用于测试的模拟策略"

  @property
  def version(self) -> str:
    return "1.0.0"

  @classmethod
  def get_parameter_schema(cls) -> dict:
    return {
      "type": "object",
      "properties": {
        "period": {"type": "integer", "default": 20},
        "threshold": {"type": "number", "default": 0.02},
      },
      "required": []
    }

  async def on_init(self):
    self.initialized = True

  async def step(self, input: StrategyInput) -> StrategyOutput:
    return StrategyOutput()

  async def on_stop(self):
    self.stopped = True


async def keep_running_loop(runtime):
  """测试用：让执行循环保持运行，直到任务被取消。"""
  await asyncio.Event().wait()


@pytest.mark.unit
class TestStrategyManager:
  """StrategyManager 单元测试类"""

  @pytest.fixture
  async def strategy_manager(self):
    """创建策略管理器实例"""
    StrategyManager._instance = None
    manager = StrategyManager()
    with (
      patch.object(manager, "_sync_strategies", new_callable=AsyncMock),
      patch.object(manager, "_restore_runs", new_callable=AsyncMock),
      patch.object(manager, "_save_runtime_to_db", new_callable=AsyncMock),
      patch.object(manager, "_update_runtime_status", new_callable=AsyncMock),
      patch.object(manager, "_update_runtime_metrics", new_callable=AsyncMock),
      patch.object(manager, "_ensure_backtest_data_available", new_callable=AsyncMock),
      patch.object(manager.executor, "_setup_broker_and_data", new_callable=AsyncMock),
      patch.object(manager.executor, "_run_strategy_loop", side_effect=keep_running_loop),
      patch(
        "repositories.backtest_repository.BacktestRepository.create_backtest",
        new_callable=AsyncMock,
      ),
    ):
      await manager.start()
      yield manager
      await manager.stop()
    StrategyManager._instance = None

  @pytest.mark.asyncio
  async def test_run_strategy_with_auto_start(self, strategy_manager: StrategyManager):
    """测试 run_strategy 自动启动模式"""
    # Mock 数据库操作
    with patch('core.strategy_manager.get_async_db') as mock_db:
      mock_session = AsyncMock()
      mock_db.return_value.__aiter__.return_value = [mock_session]

      # 创建并自动启动策略
      run_id = await strategy_manager.run_strategy(
        strategy_id=1,
        strategy_class=MockStrategy,
        mode=StrategyRunMode.BACKTEST,
        instruments=["000001.SZ"],
        parameters={"period": 20, "threshold": 0.02},
        backtest_start_time=datetime.now() - timedelta(days=1),
        backtest_end_time=datetime.now(),
        auto_start=True,
      )

      # 验证运行实例已创建
      assert run_id is not None
      assert len(run_id) == 36  # UUID 格式

      # 验证可以获取运行实例
      runtime = strategy_manager.get_run(run_id)
      assert runtime is not None
      assert runtime.strategy_id == 1
      assert runtime.mode == StrategyRunMode.BACKTEST
      assert runtime.instruments == ["000001.SZ"]
      assert runtime.parameters == {"period": 20, "threshold": 0.02}

  @pytest.mark.asyncio
  async def test_run_strategy_without_auto_start(self, strategy_manager: StrategyManager):
    """测试 run_strategy 不自动启动模式"""
    with patch('core.strategy_manager.get_async_db') as mock_db:
      mock_session = AsyncMock()
      mock_db.return_value.__aiter__.return_value = [mock_session]

      # 创建策略但不自动启动
      run_id = await strategy_manager.run_strategy(
        strategy_id=2,
        strategy_class=MockStrategy,
        mode=StrategyRunMode.PAPER,
        instruments=["600519.SH"],
        parameters={"period": 30},
        auto_start=False,
      )

      # 验证运行实例已创建
      runtime = strategy_manager.get_run(run_id)
      assert runtime is not None
      assert runtime.status == ExecutionStatus.PENDING

  @pytest.mark.asyncio
  async def test_start_strategy(self, strategy_manager):
    """测试 start_strategy 启动策略"""
    with patch('core.strategy_manager.get_async_db') as mock_db:
      mock_session = AsyncMock()
      mock_db.return_value.__aiter__.return_value = [mock_session]

      # 先创建策略(不自动启动)
      run_id = await strategy_manager.run_strategy(
        strategy_id=4,
        strategy_class=MockStrategy,
        mode=StrategyRunMode.BACKTEST,
        instruments=["000001.SZ"],
        parameters={},
        backtest_start_time=datetime.now() - timedelta(days=1),
        backtest_end_time=datetime.now(),
        auto_start=False,
      )

      # 启动策略
      await strategy_manager.start_strategy(run_id)

      # 等待启动完成
      await asyncio.sleep(0.1)

      # 验证状态
      runtime = strategy_manager.get_run(run_id)
      assert runtime.status in [ExecutionStatus.RUNNING, ExecutionStatus.PENDING]

  @pytest.mark.asyncio
  async def test_stop_strategy(self, strategy_manager):
    """测试 stop_strategy 停止策略"""
    with patch('core.strategy_manager.get_async_db') as mock_db:
      mock_session = AsyncMock()
      mock_db.return_value.__aiter__.return_value = [mock_session]

      # 创建并启动策略
      run_id = await strategy_manager.run_strategy(
        strategy_id=5,
        strategy_class=MockStrategy,
        mode=StrategyRunMode.BACKTEST,
        instruments=["000001.SZ"],
        parameters={},
        backtest_start_time=datetime.now() - timedelta(days=1),
        backtest_end_time=datetime.now(),
        auto_start=False,
      )

      # 启动策略
      await strategy_manager.start_strategy(run_id)
      await asyncio.sleep(0.1)

      # 停止策略
      await strategy_manager.stop_strategy(run_id)
      await asyncio.sleep(0.1)

      # 验证状态
      runtime = strategy_manager.get_run(run_id)
      assert runtime.status in [ExecutionStatus.STOPPED, ExecutionStatus.ERROR]

  @pytest.mark.asyncio
  async def test_pause_and_resume_strategy(self, strategy_manager):
    """测试 pause_strategy 和 resume_strategy"""
    with patch('core.strategy_manager.get_async_db') as mock_db:
      mock_session = AsyncMock()
      mock_db.return_value.__aiter__.return_value = [mock_session]

      # 创建并启动策略
      run_id = await strategy_manager.run_strategy(
        strategy_id=6,
        strategy_class=MockStrategy,
        mode=StrategyRunMode.PAPER,
        instruments=["600519.SH"],
        parameters={},
        auto_start=False,
      )

      await strategy_manager.start_strategy(run_id)
      await asyncio.sleep(0.1)

      # 暂停策略
      await strategy_manager.pause_strategy(run_id)
      await asyncio.sleep(0.1)

      runtime = strategy_manager.get_run(run_id)
      assert runtime.status == ExecutionStatus.PAUSED

      # 恢复策略
      await strategy_manager.resume_strategy(run_id)
      await asyncio.sleep(0.1)

      runtime = strategy_manager.get_run(run_id)
      assert runtime.status == ExecutionStatus.RUNNING

  @pytest.mark.asyncio
  async def test_get_all_runs(self, strategy_manager):
    """测试 get_all_runs 获取所有运行"""
    with patch('core.strategy_manager.get_async_db') as mock_db:
      mock_session = AsyncMock()
      mock_db.return_value.__aiter__.return_value = [mock_session]

      # 创建多个策略运行
      run_id_1 = await strategy_manager.run_strategy(
        strategy_id=7,
        strategy_class=MockStrategy,
        mode=StrategyRunMode.BACKTEST,
        instruments=["000001.SZ"],
        parameters={},
        auto_start=False,
      )

      run_id_2 = await strategy_manager.run_strategy(
        strategy_id=8,
        strategy_class=MockStrategy,
        mode=StrategyRunMode.PAPER,
        instruments=["600519.SH"],
        parameters={},
        auto_start=False,
      )

      # 获取所有运行
      all_runs = strategy_manager.get_all_runs()
      assert len(all_runs) >= 2
      assert run_id_1 in [r.run_id for r in all_runs]
      assert run_id_2 in [r.run_id for r in all_runs]

  @pytest.mark.asyncio
  async def test_get_runs_by_status(self, strategy_manager):
    """测试 get_runs_by_status 按状态获取运行"""
    with patch('core.strategy_manager.get_async_db') as mock_db:
      mock_session = AsyncMock()
      mock_db.return_value.__aiter__.return_value = [mock_session]

      # 创建 PENDING 状态的策略
      run_id = await strategy_manager.run_strategy(
        strategy_id=9,
        strategy_class=MockStrategy,
        mode=StrategyRunMode.BACKTEST,
        instruments=["000001.SZ"],
        parameters={},
        auto_start=False,
      )

      # 获取 PENDING 状态的运行
      pending_runs = strategy_manager.get_runs_by_status(ExecutionStatus.PENDING)
      assert len(pending_runs) >= 1
      assert run_id in [r.run_id for r in pending_runs]

  @pytest.mark.asyncio
  async def test_run_strategy_backtest_mode(self, strategy_manager):
    """测试回测模式的策略运行"""
    with patch('core.strategy_manager.get_async_db') as mock_db:
      mock_session = AsyncMock()
      mock_db.return_value.__aiter__.return_value = [mock_session]

      # 回测模式需要指定时间范围
      run_id = await strategy_manager.run_strategy(
        strategy_id=10,
        strategy_class=MockStrategy,
        mode=StrategyRunMode.BACKTEST,
        instruments=["000001.SZ", "600519.SH"],
        parameters={"period": 20},
        backtest_start_time=datetime.now() - timedelta(days=30),
        backtest_end_time=datetime.now(),
        auto_start=False,
      )

      runtime = strategy_manager.get_run(run_id)
      assert runtime.mode == StrategyRunMode.BACKTEST
      assert runtime.context.backtest_start_time is not None
      assert runtime.context.backtest_end_time is not None

  @pytest.mark.asyncio
  async def test_run_strategy_paper_mode(self, strategy_manager):
    """测试模拟盘模式的策略运行"""
    with patch('core.strategy_manager.get_async_db') as mock_db:
      mock_session = AsyncMock()
      mock_db.return_value.__aiter__.return_value = [mock_session]

      # 模拟盘模式
      run_id = await strategy_manager.run_strategy(
        strategy_id=11,
        strategy_class=MockStrategy,
        mode=StrategyRunMode.PAPER,
        instruments=["000001.SZ"],
        parameters={},
        auto_start=False,
      )

      runtime = strategy_manager.get_run(run_id)
      assert runtime.mode == StrategyRunMode.PAPER

  @pytest.mark.asyncio
  async def test_run_strategy_live_mode(self, strategy_manager):
    """测试实盘模式的策略运行"""
    with patch('core.strategy_manager.get_async_db') as mock_db:
      mock_session = AsyncMock()
      mock_db.return_value.__aiter__.return_value = [mock_session]

      # 实盘模式
      run_id = await strategy_manager.run_strategy(
        strategy_id=12,
        strategy_class=MockStrategy,
        mode=StrategyRunMode.LIVE,
        instruments=["600519.SH"],
        parameters={},
        auto_start=False,
      )

      runtime = strategy_manager.get_run(run_id)
      assert runtime.mode == StrategyRunMode.LIVE

  def test_clear_market_data_sync_cache_for_missing_backtest_window(
    self,
    monkeypatch,
  ):
    """回测补齐前应清理缺失日期与整体窗口的同步完成缓存。"""
    StrategyManager._instance = None
    manager = StrategyManager()
    deleted_keys = []

    def fake_delete(key):
      deleted_keys.append(key)
      return 1

    monkeypatch.setattr(
      "core.strategy_manager.redis_client.delete",
      fake_delete,
    )

    manager._clear_market_data_sync_cache(
      instrument="562500.SH",
      dates=[date(2026, 4, 7), date(2026, 4, 8)],
      periods={"tick"},
      start_day="20260407",
      end_day="20260408",
    )

    assert deleted_keys == [
      "daily_market_data_stock:562500.SH:20260407:tick",
      "daily_market_data_stock:562500.SH:20260408:tick",
      "daily_market_data_sync_complete:562500.SH:20260407-20260408:tick",
    ]
    StrategyManager._instance = None

  def test_sync_periods_for_missing_backtest_info(self):
    """按缺失明细只同步 daily-market-data-sync 支持的周期。"""
    StrategyManager._instance = None
    manager = StrategyManager()

    periods = manager._sync_periods_for_missing_info(
      {
        "klines": {"1m", "1d", "5m"},
        "tick": True,
      },
      {"tick", "1m", "1d"},
    )

    assert periods == {"tick", "1m", "1d"}
    StrategyManager._instance = None
