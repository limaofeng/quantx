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
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from quantx_domain.strategies.base import (
  StrategyBase,
  StrategyContext,
  StrategyInput,
  StrategyOutput,
  StrategyRunMode,
)
from quantx_engine.strategy_executor import (
  ExecutionStatus,
  StrategyRuntime,
)
from quantx_engine.strategy_manager import StrategyManager
from quantx_infrastructure.core.runtime_state_manager import (
  RuntimeStateManager,
  RuntimeStateRestoreResult,
  RuntimeStateRestoreStatus,
)
from quantx_infrastructure.models.enums import StrategyRunStatus


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


def _t_trade_replay_runtime(instruments: list[str]) -> StrategyRuntime:
  metadata = {
    code: {"instrument_name": code, "position_shares": 100}
    for code in instruments
  }
  context = StrategyContext(
    run_id="replay-run",
    mode=StrategyRunMode.BACKTEST,
    instruments=list(instruments),
    parameters={
      "t_trade_replay": True,
      "initial_instrument_metadata": metadata,
      "replay_skipped_instruments": [],
    },
    backtest_start_time=datetime(2026, 8, 3, 9, 30),
    backtest_end_time=datetime(2026, 8, 4, 15, 0),
  )
  return StrategyRuntime(
    run_id=context.run_id,
    name="isolated replay",
    strategy_id=1,
    strategy_class=MockStrategy,
    context=context,
  )


async def keep_running_loop(runtime):
  """测试用：让执行循环保持运行，直到任务被取消。"""
  await asyncio.Event().wait()


async def restore_runtime_state_without_database(manager):
  """StrategyManager 单测不得读取共享的持久化运行状态。"""
  status = (
    RuntimeStateRestoreStatus.PERSISTENCE_DISABLED
    if not manager.persist_enabled
    else RuntimeStateRestoreStatus.NOT_FOUND
  )
  return RuntimeStateRestoreResult(status=status, state=manager._state)


async def checkpoint_runtime_state_without_database(manager):
  manager._dirty = False
  return True


async def save_runtime_state_without_database(manager):
  manager._dirty = False
  return True


async def no_unapplied_runtime_event(_manager):
  return None


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
      patch.object(
        RuntimeStateManager,
        "restore",
        restore_runtime_state_without_database,
      ),
      patch.object(
        RuntimeStateManager,
        "checkpoint_strategy_state_changes",
        checkpoint_runtime_state_without_database,
      ),
      patch.object(
        RuntimeStateManager,
        "save_snapshot",
        save_runtime_state_without_database,
      ),
      patch.object(
        RuntimeStateManager,
        "get_earliest_unapplied_runtime_event_key",
        no_unapplied_runtime_event,
      ),
      patch(
        "quantx_infrastructure.repositories.backtest_repository.BacktestRepository.create_backtest",
        new_callable=AsyncMock,
      ),
      patch(
        "quantx_infrastructure.repositories.backtest_repository.BacktestRepository.update_backtest_start",
        new_callable=AsyncMock,
      ),
      patch(
        "quantx_infrastructure.repositories.backtest_repository.BacktestRepository.update_backtest_status",
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
    with patch('quantx_engine.strategy_manager.get_async_db') as mock_db:
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
    with patch('quantx_engine.strategy_manager.get_async_db') as mock_db:
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
    with patch('quantx_engine.strategy_manager.get_async_db') as mock_db:
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
    with patch('quantx_engine.strategy_manager.get_async_db') as mock_db:
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
  async def test_stop_strategy_converges_persisted_run_missing_from_executor(
    self, strategy_manager
  ):
    """Engine 重启后内存运行态缺失时，停止动作仍应幂等收敛数据库状态。"""
    persisted_run = SimpleNamespace(status=StrategyRunStatus.RUNNING)
    repository = AsyncMock()
    repository.find_run_by_id.return_value = persisted_run

    with (
      patch("quantx_engine.strategy_manager.get_async_db") as mock_db,
      patch(
        "quantx_engine.strategy_manager.StrategyRunRepository",
        return_value=repository,
      ),
    ):
      mock_db.return_value.__aiter__.return_value = [AsyncMock()]

      success = await strategy_manager.stop_strategy("persisted-run")

    assert success is True
    repository.update_run.assert_awaited_once()
    run_id, update_data = repository.update_run.await_args.args
    assert run_id == "persisted-run"
    assert update_data["status"] == StrategyRunStatus.STOPPED
    assert update_data["stop_time"] is not None

  @pytest.mark.asyncio
  @pytest.mark.parametrize(
    "terminal_status",
    [
      StrategyRunStatus.STOPPED,
      StrategyRunStatus.COMPLETED,
      StrategyRunStatus.ERROR,
    ],
  )
  async def test_stop_strategy_is_idempotent_for_persisted_terminal_run(
    self, strategy_manager, terminal_status
  ):
    """终态运行不应被回退或重写，但停止请求应视为已完成。"""
    repository = AsyncMock()
    repository.find_run_by_id.return_value = SimpleNamespace(status=terminal_status)

    with (
      patch("quantx_engine.strategy_manager.get_async_db") as mock_db,
      patch(
        "quantx_engine.strategy_manager.StrategyRunRepository",
        return_value=repository,
      ),
    ):
      mock_db.return_value.__aiter__.return_value = [AsyncMock()]

      success = await strategy_manager.stop_strategy("terminal-run")

    assert success is True
    repository.update_run.assert_not_awaited()

  @pytest.mark.asyncio
  async def test_stop_strategy_returns_false_for_unknown_run(self, strategy_manager):
    """不存在于内存和数据库的运行不能被误报为停止成功。"""
    repository = AsyncMock()
    repository.find_run_by_id.return_value = None

    with (
      patch("quantx_engine.strategy_manager.get_async_db") as mock_db,
      patch(
        "quantx_engine.strategy_manager.StrategyRunRepository",
        return_value=repository,
      ),
    ):
      mock_db.return_value.__aiter__.return_value = [AsyncMock()]

      success = await strategy_manager.stop_strategy("unknown-run")

    assert success is False
    repository.update_run.assert_not_awaited()

  @pytest.mark.asyncio
  async def test_deferred_start_is_tracked_deduplicated_and_cancellable(
    self,
    strategy_manager: StrategyManager,
  ) -> None:
    run_id = "deferred-replay"
    strategy_manager.executor.create(
      run_id=run_id,
      strategy_id=1,
      strategy_class=MockStrategy,
      context=StrategyContext(
        run_id=run_id,
        mode=StrategyRunMode.BACKTEST,
        instruments=["600887.SH"],
        parameters={"t_trade_replay": True, "account_id": "account-1"},
        backtest_id="backtest-1",
      ),
    )
    entered = asyncio.Event()

    async def slow_start(_run_id: str) -> bool:
      entered.set()
      await asyncio.Event().wait()
      return True

    with patch.object(strategy_manager, "start_strategy", new=slow_start):
      assert await strategy_manager.defer_start_strategy(run_id) is True
      task = strategy_manager._deferred_start_tasks[run_id]
      await asyncio.wait_for(entered.wait(), timeout=1.0)
      assert await strategy_manager.defer_start_strategy(run_id) is True
      assert strategy_manager._deferred_start_tasks[run_id] is task

      assert await strategy_manager.cancel_deferred_start(run_id) is True

    assert task.cancelled()
    assert run_id not in strategy_manager._deferred_start_tasks

  @pytest.mark.asyncio
  async def test_deferred_start_exception_converges_all_durable_states(
    self,
    strategy_manager: StrategyManager,
  ) -> None:
    run_id = "failed-replay"
    strategy_manager.executor.create(
      run_id=run_id,
      strategy_id=1,
      strategy_class=MockStrategy,
      context=StrategyContext(
        run_id=run_id,
        mode=StrategyRunMode.BACKTEST,
        instruments=["600887.SH"],
        parameters={"t_trade_replay": True, "account_id": "account-1"},
        backtest_id="backtest-2",
      ),
    )

    async def failed_start(_run_id: str) -> bool:
      raise RuntimeError("tick sync failed")

    with (
      patch.object(strategy_manager, "start_strategy", new=failed_start),
      patch.object(
        strategy_manager,
        "_mark_backtest_error_safely",
        new_callable=AsyncMock,
      ) as mark_backtest_error,
      patch(
        "quantx_engine.strategy_manager.t_trade_replay_projection_service.update",
        new_callable=AsyncMock,
      ) as update_projection,
    ):
      assert await strategy_manager.defer_start_strategy(run_id) is True
      task = strategy_manager._deferred_start_tasks[run_id]
      await task

    strategy_manager._update_runtime_status.assert_awaited_once_with(
      run_id,
      "ERROR",
      "tick sync failed",
    )
    mark_backtest_error.assert_awaited_once_with(
      "backtest-2",
      "tick sync failed",
    )
    update_projection.assert_awaited_once()
    assert update_projection.await_args.kwargs["status"] == "ERROR"

  @pytest.mark.asyncio
  async def test_shutdown_cancels_preparation_without_terminalizing_replay(
    self,
    strategy_manager: StrategyManager,
  ) -> None:
    run_id = "shutdown-replay"
    strategy_manager.executor.create(
      run_id=run_id,
      strategy_id=1,
      strategy_class=MockStrategy,
      context=StrategyContext(
        run_id=run_id,
        mode=StrategyRunMode.BACKTEST,
        instruments=["600887.SH"],
        parameters={"t_trade_replay": True, "account_id": "account-1"},
        backtest_id="backtest-shutdown",
      ),
    )
    entered = asyncio.Event()

    async def slow_start(_run_id: str) -> bool:
      entered.set()
      await asyncio.Event().wait()
      return True

    with patch.object(strategy_manager, "start_strategy", new=slow_start):
      assert await strategy_manager.defer_start_strategy(run_id) is True
      task = strategy_manager._deferred_start_tasks[run_id]
      await asyncio.wait_for(entered.wait(), timeout=1.0)
      await strategy_manager._cancel_deferred_starts_for_shutdown()

    assert task.cancelled()
    assert strategy_manager._deferred_start_tasks == {}
    strategy_manager._update_runtime_status.assert_not_awaited()

  @pytest.mark.asyncio
  async def test_restore_pending_replay_after_shutdown_resumes_preparation(
    self,
  ) -> None:
    StrategyManager._instance = None
    manager = StrategyManager()
    run = SimpleNamespace(
      id="restored-replay",
      name="Restored replay",
      strategy_id=1,
      strategy=SimpleNamespace(class_name="MockStrategy", file_path=""),
      parameters={"t_trade_replay": True, "account_id": "account-1"},
      mode=StrategyRunMode.BACKTEST,
      status=StrategyRunStatus.PENDING,
      instruments=["600887.SH"],
      initial_capital=100_000.0,
      metrics=None,
    )
    backtest = SimpleNamespace(
      id="restored-backtest",
      status="PENDING",
      version=1,
      backtest_start_time=datetime(2026, 7, 23, 9, 30),
      backtest_end_time=datetime(2026, 8, 19, 15, 0),
    )
    run_repo = AsyncMock()
    run_repo.find_all_active_runs.return_value = [run]
    backtest_repo = AsyncMock()
    backtest_repo.get_backtests_by_run.return_value = [backtest]

    async def fake_get_async_db():
      yield AsyncMock()

    with (
      patch("quantx_engine.strategy_manager.get_async_db", fake_get_async_db),
      patch(
        "quantx_engine.strategy_manager.StrategyRunRepository",
        return_value=run_repo,
      ),
      patch(
        "quantx_infrastructure.repositories.backtest_repository.BacktestRepository",
        return_value=backtest_repo,
      ),
      patch(
        "quantx_engine.strategy_manager.strategy_registry.get_strategy_class",
        return_value=MockStrategy,
      ),
      patch(
        "quantx_engine.strategy_manager.t_trade_replay_projection_service.get",
        new_callable=AsyncMock,
        return_value={"status": "RUNNING"},
      ),
      patch.object(
        manager,
        "defer_start_strategy",
        new_callable=AsyncMock,
        return_value=True,
      ) as defer_start,
      patch.object(
        manager,
        "start_strategy",
        new_callable=AsyncMock,
      ) as start_strategy,
    ):
      await manager._restore_runs()

    defer_start.assert_awaited_once_with("restored-replay")
    start_strategy.assert_not_awaited()
    assert manager.get_run("restored-replay") is not None
    manager.executor.runs.clear()
    StrategyManager._instance = None

  @pytest.mark.asyncio
  async def test_pause_and_resume_strategy(self, strategy_manager):
    """测试 pause_strategy 和 resume_strategy"""
    with patch('quantx_engine.strategy_manager.get_async_db') as mock_db:
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
    with patch('quantx_engine.strategy_manager.get_async_db') as mock_db:
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
    with patch('quantx_engine.strategy_manager.get_async_db') as mock_db:
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
    with patch('quantx_engine.strategy_manager.get_async_db') as mock_db:
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
    with patch('quantx_engine.strategy_manager.get_async_db') as mock_db:
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
  async def test_clone_strategy_to_paper_uses_isolated_snapshot(
    self,
    strategy_manager: StrategyManager,
  ):
    """转模拟盘时应创建独立快照账户参数，且不自动启动。"""
    source_run = SimpleNamespace(
      id="source-run",
      strategy_id=11,
      strategy=SimpleNamespace(class_name="MockStrategy", file_path=""),
      parameters={
        "cash_total": "123456.78",
        "position_shares": 500,
        "avg_cost": 40.25,
      },
      instruments=["688552.SH"],
      initial_capital=None,
      name="Source Backtest",
    )

    async def fake_get_async_db():
      yield object()

    with (
      patch("quantx_engine.strategy_manager.get_async_db", fake_get_async_db),
      patch("quantx_engine.strategy_manager.StrategyRunRepository") as repo_class,
      patch(
        "quantx_engine.strategy_manager.strategy_registry.get_strategy_class",
        return_value=MockStrategy,
      ),
      patch.object(
        strategy_manager,
        "run_strategy",
        new_callable=AsyncMock,
        return_value="new-paper-run",
      ) as run_strategy,
    ):
      repo = repo_class.return_value
      repo.find_run_by_id = AsyncMock(return_value=source_run)

      new_run_id = await strategy_manager.clone_strategy(
        "source-run",
        StrategyRunMode.PAPER,
        parameter_overrides={
          "cash_total": 200000,
          "position_shares": 300,
        },
      )

    assert new_run_id == "new-paper-run"
    kwargs = run_strategy.await_args.kwargs
    assert kwargs["mode"] == StrategyRunMode.PAPER
    assert kwargs["instruments"] == ["688552.SH"]
    assert kwargs["auto_start"] is False
    assert kwargs["strategy_class"] is MockStrategy
    assert kwargs["parameters"]["initial_capital"] == 200000
    assert kwargs["parameters"]["cash_total"] == 200000
    assert kwargs["parameters"]["position_shares"] == 300
    paper_account = kwargs["parameters"]["_paper_account"]
    assert paper_account["model"] == "isolated_snapshot"
    assert paper_account["source_run_id"] == "source-run"
    assert paper_account["created_at"]

  @pytest.mark.asyncio
  async def test_run_strategy_live_mode(self, strategy_manager):
    """测试实盘模式的策略运行"""
    with patch('quantx_engine.strategy_manager.get_async_db') as mock_db:
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
      "quantx_engine.strategy_manager.redis_client.delete",
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
      "market-data-request-lock:daily_market_data_sync_complete:562500.SH:20260407-20260408:tick",
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

  @pytest.mark.asyncio
  async def test_sync_missing_tick_data_splits_requests_into_seven_day_windows(
    self,
    monkeypatch,
  ):
    """Tick 补齐请求必须满足 QMT Agent 的七个自然日跨度上限。"""
    StrategyManager._instance = None
    manager = StrategyManager()
    sync_request = AsyncMock(return_value={"status": "success"})
    monkeypatch.setattr(
      "quantx_engine.strategy_manager.request_market_data_sync",
      sync_request,
    )
    monkeypatch.setattr(manager, "_clear_market_data_sync_cache", lambda **_: None)
    trading_dates = [
      date(2026, 7, 20),
      date(2026, 7, 21),
      date(2026, 7, 22),
      date(2026, 7, 23),
      date(2026, 7, 24),
      date(2026, 7, 27),
      date(2026, 7, 28),
      date(2026, 7, 29),
      date(2026, 7, 30),
      date(2026, 7, 31),
      date(2026, 8, 3),
      date(2026, 8, 4),
      date(2026, 8, 5),
      date(2026, 8, 6),
      date(2026, 8, 7),
      date(2026, 8, 10),
      date(2026, 8, 11),
      date(2026, 8, 12),
      date(2026, 8, 13),
      date(2026, 8, 14),
    ]

    await manager._sync_missing_backtest_data(
      runtime=SimpleNamespace(
        run_id="replay-run",
        context=SimpleNamespace(backtest_id="backtest-split"),
      ),
      missing={
        "600887.SH": {
          "dates": set(trading_dates),
          "klines": set(),
          "tick": True,
        }
      },
      sync_periods={"tick"},
    )

    assert [call.kwargs for call in sync_request.await_args_list] == [
      {
        "stock_list": ["600887.SH"],
        "start_time": "20260720",
        "end_time": "20260724",
        "periods": ["tick"],
        "idempotency_scope": "backtest-data-supplement-v1:backtest-split",
      },
      {
        "stock_list": ["600887.SH"],
        "start_time": "20260727",
        "end_time": "20260731",
        "periods": ["tick"],
        "idempotency_scope": "backtest-data-supplement-v1:backtest-split",
      },
      {
        "stock_list": ["600887.SH"],
        "start_time": "20260803",
        "end_time": "20260807",
        "periods": ["tick"],
        "idempotency_scope": "backtest-data-supplement-v1:backtest-split",
      },
      {
        "stock_list": ["600887.SH"],
        "start_time": "20260810",
        "end_time": "20260814",
        "periods": ["tick"],
        "idempotency_scope": "backtest-data-supplement-v1:backtest-split",
      },
    ]
    StrategyManager._instance = None

  @pytest.mark.asyncio
  async def test_sync_missing_tick_data_requests_only_the_one_day_gap(
    self,
    monkeypatch,
  ):
    StrategyManager._instance = None
    manager = StrategyManager()
    sync_request = AsyncMock(return_value={"status": "success"})
    monkeypatch.setattr(
      "quantx_engine.strategy_manager.request_market_data_sync",
      sync_request,
    )
    monkeypatch.setattr(manager, "_clear_market_data_sync_cache", lambda **_: None)
    missing_day = date(2026, 8, 5)

    await manager._sync_missing_backtest_data(
      runtime=SimpleNamespace(
        run_id="replay-run",
        context=SimpleNamespace(backtest_id="backtest-1"),
      ),
      missing={
        "600887.SH": {
          "dates": {missing_day},
          "klines": set(),
          "tick": True,
        }
      },
      sync_periods={"tick"},
    )

    sync_request.assert_awaited_once_with(
      stock_list=["600887.SH"],
      start_time="20260805",
      end_time="20260805",
      periods=["tick"],
      idempotency_scope="backtest-data-supplement-v1:backtest-1",
    )
    StrategyManager._instance = None

  @pytest.mark.asyncio
  async def test_twenty_complete_tick_days_issue_zero_qmt_requests(
    self,
    monkeypatch,
  ):
    StrategyManager._instance = None
    manager = StrategyManager()
    trading_dates = []
    current = date(2026, 7, 20)
    while len(trading_dates) < 20:
      if current.weekday() < 5:
        trading_dates.append(current)
      current += timedelta(days=1)

    def complete_tick_times(trading_day):
      morning = [
        datetime.combine(trading_day, datetime.min.time()).replace(
          hour=9,
          minute=25,
        )
        + timedelta(minutes=index)
        for index in range(126)
      ]
      afternoon = [
        datetime.combine(trading_day, datetime.min.time()).replace(
          hour=13,
          minute=0,
        )
        + timedelta(minutes=index)
        for index in range(121)
      ]
      return morning + afternoon

    tick_repo = SimpleNamespace(
      find_all=lambda **kwargs: [
        SimpleNamespace(time=value)
        for value in complete_tick_times(kwargs["start_time"].date())
      ]
    )
    calendar = SimpleNamespace(
      get_trading_calendar=AsyncMock(return_value=trading_dates)
    )
    monkeypatch.setattr(
      "quantx_engine.strategy_manager.TradingDateHelper",
      lambda: calendar,
    )

    missing = await manager._find_missing_backtest_data(
      service=SimpleNamespace(tick_repo=tick_repo),
      instruments=["600887.SH"],
      start_time=datetime(2026, 7, 20, 9, 30),
      end_time=datetime(2026, 8, 14, 15, 0),
      required_kline_periods=set(),
      require_tick=True,
      strict_tick_quality=True,
    )
    sync_request = AsyncMock(return_value={"status": "success"})
    monkeypatch.setattr(
      "quantx_engine.strategy_manager.request_market_data_sync",
      sync_request,
    )

    await manager._sync_missing_backtest_data(
      runtime=SimpleNamespace(run_id="replay-run"),
      missing=missing,
      sync_periods={"tick"},
    )

    assert missing == {}
    sync_request.assert_not_awaited()
    StrategyManager._instance = None

  @pytest.mark.asyncio
  async def test_confirmed_empty_tick_day_requires_completed_coverage(
    self,
    monkeypatch,
  ):
    StrategyManager._instance = None
    manager = StrategyManager()
    trading_day = date(2026, 8, 3)
    calendar = SimpleNamespace(
      get_trading_calendar=AsyncMock(return_value=[trading_day])
    )
    confirmed_empty = AsyncMock(return_value={trading_day})
    monkeypatch.setattr(
      "quantx_engine.strategy_manager.TradingDateHelper",
      lambda: calendar,
    )
    monkeypatch.setattr(
      "quantx_engine.strategy_manager.load_completed_empty_tick_days",
      confirmed_empty,
    )

    missing = await manager._find_missing_backtest_data(
      service=SimpleNamespace(tick_repo=SimpleNamespace(find_all=lambda **_: [])),
      instruments=["600887.SH"],
      start_time=datetime(2026, 8, 3, 9, 30),
      end_time=datetime(2026, 8, 3, 15, 0),
      required_kline_periods=set(),
      require_tick=True,
      strict_tick_quality=True,
    )

    assert missing == {}
    confirmed_empty.assert_awaited_once_with(
      instrument_code="600887.SH",
      trading_dates=[trading_day],
    )
    StrategyManager._instance = None

  @pytest.mark.asyncio
  async def test_empty_tick_day_without_completed_coverage_remains_missing(
    self,
    monkeypatch,
  ):
    StrategyManager._instance = None
    manager = StrategyManager()
    trading_day = date(2026, 8, 3)
    calendar = SimpleNamespace(
      get_trading_calendar=AsyncMock(return_value=[trading_day])
    )
    monkeypatch.setattr(
      "quantx_engine.strategy_manager.TradingDateHelper",
      lambda: calendar,
    )
    monkeypatch.setattr(
      "quantx_engine.strategy_manager.load_completed_empty_tick_days",
      AsyncMock(return_value=set()),
    )

    missing = await manager._find_missing_backtest_data(
      service=SimpleNamespace(tick_repo=SimpleNamespace(find_all=lambda **_: [])),
      instruments=["600887.SH"],
      start_time=datetime(2026, 8, 3, 9, 30),
      end_time=datetime(2026, 8, 3, 15, 0),
      required_kline_periods=set(),
      require_tick=True,
      strict_tick_quality=True,
    )

    assert missing["600887.SH"]["dates"] == {trading_day}
    assert missing["600887.SH"]["quality_issues"][0]["classification"] == "MISSING"
    StrategyManager._instance = None

  @pytest.mark.asyncio
  async def test_t_trade_replay_partial_tick_day_is_not_treated_as_complete(
    self,
    monkeypatch,
  ):
    StrategyManager._instance = None
    manager = StrategyManager()
    trading_date = date(2026, 8, 3)
    partial_times = [
      datetime(2026, 8, 3, 10, 0) + timedelta(minutes=index)
      for index in range(10)
    ]
    tick_repo = SimpleNamespace(
      find_all=lambda **_kwargs: [
        SimpleNamespace(time=value) for value in partial_times
      ]
    )
    calendar = SimpleNamespace(
      get_trading_calendar=AsyncMock(return_value=[trading_date])
    )
    monkeypatch.setattr(
      "quantx_engine.strategy_manager.TradingDateHelper", lambda: calendar
    )

    missing = await manager._find_missing_backtest_data(
      service=SimpleNamespace(tick_repo=tick_repo),
      instruments=["600887.SH"],
      start_time=datetime(2026, 8, 3, 9, 30),
      end_time=datetime(2026, 8, 3, 15, 0),
      required_kline_periods=set(),
      require_tick=True,
      strict_tick_quality=True,
    )

    issue = missing["600887.SH"]["quality_issues"][0]
    assert issue["classification"] == "PARTIAL"
    assert issue["statistics"]["continuous_session_record_count"] == 10
    assert "TICK_COUNT_TOO_LOW" in issue["reason_codes"]
    assert "SESSION_OPEN_NOT_COVERED" in issue["reason_codes"]
    assert "SESSION_CLOSE_NOT_COVERED" in issue["reason_codes"]
    assert "TICK_COUNT_TOO_LOW" in manager._format_missing_data(missing)
    assert manager._contains_partial_market_data(missing) is True
    StrategyManager._instance = None

  @pytest.mark.asyncio
  async def test_t_trade_replay_complete_tick_day_passes_strict_quality_check(
    self,
    monkeypatch,
  ):
    StrategyManager._instance = None
    manager = StrategyManager()
    trading_date = date(2026, 8, 3)
    morning = [
      datetime(2026, 8, 3, 9, 30) + timedelta(minutes=index)
      for index in range(121)
    ]
    afternoon = [
      datetime(2026, 8, 3, 13, 0) + timedelta(minutes=index)
      for index in range(121)
    ]
    tick_repo = SimpleNamespace(
      find_all=lambda **_kwargs: [
        SimpleNamespace(time=value) for value in morning + afternoon
      ]
    )
    calendar = SimpleNamespace(
      get_trading_calendar=AsyncMock(return_value=[trading_date])
    )
    monkeypatch.setattr(
      "quantx_engine.strategy_manager.TradingDateHelper", lambda: calendar
    )

    missing = await manager._find_missing_backtest_data(
      service=SimpleNamespace(tick_repo=tick_repo),
      instruments=["600887.SH"],
      start_time=datetime(2026, 8, 3, 9, 30),
      end_time=datetime(2026, 8, 3, 15, 0),
      required_kline_periods=set(),
      require_tick=True,
      strict_tick_quality=True,
    )

    assert missing == {}
    StrategyManager._instance = None

  @pytest.mark.asyncio
  async def test_t_trade_replay_with_complete_local_data_never_calls_agent(
    self,
    monkeypatch,
  ):
    StrategyManager._instance = None
    manager = StrategyManager()
    runtime = _t_trade_replay_runtime(["600887.SH"])
    monkeypatch.setattr(
      manager,
      "_find_missing_backtest_data",
      AsyncMock(return_value={}),
    )
    queued = AsyncMock()
    waited = AsyncMock()
    monkeypatch.setattr("quantx_engine.strategy_manager.queue_market_data_sync", queued)
    monkeypatch.setattr("quantx_engine.strategy_manager.request_market_data_sync", waited)
    monkeypatch.setattr(
      "quantx_engine.strategy_manager.HistoricalMarketDataService",
      lambda: SimpleNamespace(),
    )

    await manager._ensure_backtest_data_available(runtime)

    queued.assert_not_awaited()
    waited.assert_not_awaited()
    assert runtime.context.instruments == ["600887.SH"]
    StrategyManager._instance = None

  @pytest.mark.asyncio
  async def test_t_trade_replay_syncs_missing_local_data_then_rechecks(
    self,
    monkeypatch,
  ):
    StrategyManager._instance = None
    manager = StrategyManager()
    runtime = _t_trade_replay_runtime(["600887.SH", "688552.SH"])
    missing = {
      "688552.SH": {
        "dates": {date(2026, 8, 3)},
        "klines": set(),
        "tick": True,
      }
    }
    monkeypatch.setattr(
      manager,
      "_find_missing_backtest_data",
      AsyncMock(side_effect=[missing, {}]),
    )
    sync = AsyncMock()
    queued = AsyncMock()
    monkeypatch.setattr(manager, "_sync_missing_backtest_data", sync)
    monkeypatch.setattr("quantx_engine.strategy_manager.queue_market_data_sync", queued)
    monkeypatch.setattr(
      "quantx_engine.strategy_manager.HistoricalMarketDataService",
      lambda: SimpleNamespace(),
    )
    repo = SimpleNamespace(update_run=AsyncMock())

    async def fake_get_async_db():
      yield object()

    monkeypatch.setattr("quantx_engine.strategy_manager.get_async_db", fake_get_async_db)
    monkeypatch.setattr(
      "quantx_engine.strategy_manager.StrategyRunRepository",
      lambda _db: repo,
    )

    await manager._ensure_backtest_data_available(runtime)

    sync.assert_awaited_once_with(
      runtime=runtime,
      missing=missing,
      sync_periods={"tick"},
    )
    queued.assert_not_awaited()
    find_missing = manager._find_missing_backtest_data
    assert find_missing.await_args_list[1].kwargs["strict_tick_quality"] is True
    assert runtime.context.instruments == ["600887.SH", "688552.SH"]
    preparation = runtime.context.parameters["replay_data_preparation"]
    assert preparation["schema_version"] == 3
    assert preparation["policy"] == "INFLUXDB_LOCAL_FIRST_AGENT_SYNC_BLOCKING"
    assert preparation["blocking"] is True
    assert preparation["required"] is True
    assert preparation["missing_before"] == ["688552.SH"]
    assert preparation["missing_after"] == []
    assert preparation["required_instruments"] == ["600887.SH", "688552.SH"]
    assert preparation["available_instruments"] == ["600887.SH", "688552.SH"]
    assert preparation["replay_start_allowed"] is True
    assert preparation["synchronization"] == {
      "mode": "BLOCKING_REQUIRED",
      "status": "COMPLETED",
      "requested_periods": ["tick"],
    }
    assert runtime.context.parameters["replay_skipped_instruments"] == []
    repo.update_run.assert_awaited_once()
    StrategyManager._instance = None

  @pytest.mark.asyncio
  async def test_t_trade_replay_incomplete_sync_fails_closed_without_pruning(
    self,
    monkeypatch,
  ):
    StrategyManager._instance = None
    manager = StrategyManager()
    runtime = _t_trade_replay_runtime(["688552.SH"])
    missing = {
      "688552.SH": {
        "dates": {date(2026, 8, 3)},
        "klines": set(),
        "tick": True,
        "quality_issues": [
          {
            "data_type": "tick",
            "date": "2026-08-03",
            "instrument_code": "688552.SH",
            "complete": False,
            "classification": "PARTIAL",
            "reason_codes": ["SESSION_CLOSE_NOT_COVERED"],
            "message": "收盘时段缺失",
            "statistics": {"continuous_session_record_count": 80},
          }
        ],
      }
    }
    monkeypatch.setattr(
      manager,
      "_find_missing_backtest_data",
      AsyncMock(side_effect=[missing, missing]),
    )
    sync = AsyncMock()
    queued = AsyncMock()
    monkeypatch.setattr(manager, "_sync_missing_backtest_data", sync)
    monkeypatch.setattr("quantx_engine.strategy_manager.queue_market_data_sync", queued)
    monkeypatch.setattr(
      "quantx_engine.strategy_manager.HistoricalMarketDataService",
      lambda: SimpleNamespace(),
    )
    repo = SimpleNamespace(update_run=AsyncMock())

    async def fake_get_async_db():
      yield object()

    monkeypatch.setattr("quantx_engine.strategy_manager.get_async_db", fake_get_async_db)
    monkeypatch.setattr(
      "quantx_engine.strategy_manager.StrategyRunRepository",
      lambda _db: repo,
    )

    with pytest.raises(RuntimeError, match="DATA_PARTIAL"):
      await manager._ensure_backtest_data_available(runtime)

    sync.assert_awaited_once_with(
      runtime=runtime,
      missing=missing,
      sync_periods={"tick"},
    )
    queued.assert_not_awaited()
    assert runtime.context.instruments == ["688552.SH"]
    assert runtime.context.parameters["replay_data_preparation"]["missing_after"] == [
      "688552.SH"
    ]
    preparation = runtime.context.parameters["replay_data_preparation"]
    assert preparation["schema_version"] == 3
    assert preparation["policy"] == "INFLUXDB_LOCAL_FIRST_AGENT_SYNC_BLOCKING"
    assert preparation["replay_start_allowed"] is False
    assert preparation["synchronization"]["mode"] == "BLOCKING_REQUIRED"
    assert preparation["synchronization"]["status"] == "COMPLETED"
    assert preparation["quality_policy"] == "STRICT_DAILY_SESSION_COVERAGE"
    assert preparation["quality_issues_after"]["688552.SH"][0][
      "reason_codes"
    ] == ["SESSION_CLOSE_NOT_COVERED"]
    assert runtime.context.parameters["replay_skipped_instruments"] == []
    repo.update_run.assert_awaited_once()
    StrategyManager._instance = None

  @pytest.mark.asyncio
  async def test_deferred_all_missing_replay_preserves_data_insufficient_error(
    self,
    monkeypatch,
  ):
    StrategyManager._instance = None
    manager = StrategyManager()
    runtime = _t_trade_replay_runtime(["688552.SH"])
    runtime.context.parameters["account_id"] = "account-1"
    runtime.context.backtest_id = "backtest-1"
    manager.executor.runs[runtime.run_id] = runtime
    missing = {
      "688552.SH": {
        "dates": {date(2026, 8, 3)},
        "klines": set(),
        "tick": True,
      }
    }
    monkeypatch.setattr(
      manager,
      "_find_missing_backtest_data",
      AsyncMock(side_effect=[missing, missing]),
    )
    sync = AsyncMock()
    monkeypatch.setattr(manager, "_sync_missing_backtest_data", sync)
    monkeypatch.setattr(
      "quantx_engine.strategy_manager.HistoricalMarketDataService",
      lambda: SimpleNamespace(),
    )
    repo = SimpleNamespace(update_run=AsyncMock())

    async def fake_get_async_db():
      yield object()

    monkeypatch.setattr("quantx_engine.strategy_manager.get_async_db", fake_get_async_db)
    monkeypatch.setattr(
      "quantx_engine.strategy_manager.StrategyRunRepository",
      lambda _db: repo,
    )
    update_status = AsyncMock()
    mark_backtest = AsyncMock()
    update_projection = AsyncMock()
    broker_start = AsyncMock()
    monkeypatch.setattr(manager, "_update_runtime_status", update_status)
    monkeypatch.setattr(manager, "_mark_backtest_error_safely", mark_backtest)
    monkeypatch.setattr(manager.executor, "start", broker_start)
    monkeypatch.setattr(
      "quantx_engine.strategy_manager.t_trade_replay_projection_service.update",
      update_projection,
    )

    assert await manager.defer_start_strategy(runtime.run_id) is True
    deferred = manager._deferred_start_tasks[runtime.run_id]
    await deferred
    await asyncio.sleep(0)

    assert runtime.status == ExecutionStatus.ERROR
    assert runtime.error_message.startswith("DATA_INSUFFICIENT:")
    assert runtime.context.instruments == ["688552.SH"]
    sync.assert_awaited_once_with(
      runtime=runtime,
      missing=missing,
      sync_periods={"tick"},
    )
    broker_start.assert_not_awaited()
    assert len(update_status.await_args_list) == 2
    assert all(
      call.args == (runtime.run_id, "ERROR", runtime.error_message)
      for call in update_status.await_args_list
    )
    assert len(mark_backtest.await_args_list) == 2
    assert all(
      call.args == ("backtest-1", runtime.error_message)
      for call in mark_backtest.await_args_list
    )
    assert update_projection.await_count == 5
    assert [
      call.kwargs["phase"]
      for call in update_projection.await_args_list[:-1]
    ] == ["CHECKING_DATA", "DOWNLOADING_DATA", "VERIFYING_DATA", "FAILED"]
    final_projection = update_projection.await_args_list[-1].kwargs
    assert final_projection["status"] == "ERROR"
    assert final_projection["kind"].value == "RESULT_READY"
    assert runtime.context.parameters["replay_data_preparation"]["missing_after"] == [
      "688552.SH"
    ]
    assert runtime.context.parameters["replay_skipped_instruments"] == []
    assert runtime.run_id not in manager._deferred_start_tasks
    StrategyManager._instance = None

  @pytest.mark.asyncio
  async def test_generic_backtest_keeps_blocking_sync_semantics(
    self,
    monkeypatch,
  ):
    StrategyManager._instance = None
    manager = StrategyManager()
    runtime = _t_trade_replay_runtime(["600887.SH"])
    runtime.context.parameters.pop("t_trade_replay")
    missing = {
      "600887.SH": {
        "dates": {date(2026, 8, 3)},
        "klines": set(),
        "tick": True,
      }
    }
    monkeypatch.setattr(
      manager,
      "_find_missing_backtest_data",
      AsyncMock(side_effect=[missing, {}]),
    )
    sync = AsyncMock()
    monkeypatch.setattr(manager, "_sync_missing_backtest_data", sync)
    queued = AsyncMock()
    monkeypatch.setattr("quantx_engine.strategy_manager.queue_market_data_sync", queued)
    monkeypatch.setattr(
      "quantx_engine.strategy_manager.HistoricalMarketDataService",
      lambda: SimpleNamespace(),
    )

    await manager._ensure_backtest_data_available(runtime)

    sync.assert_awaited_once_with(
      runtime=runtime,
      missing=missing,
      sync_periods={"tick"},
    )
    queued.assert_not_awaited()
    StrategyManager._instance = None

  @pytest.mark.asyncio
  async def test_failed_run_task_persists_runtime_error_message(self):
    StrategyManager._instance = None
    manager = StrategyManager()
    runtime = _t_trade_replay_runtime(["002594.SZ"])
    runtime.context.backtest_id = "backtest-clock-error"
    runtime.status = ExecutionStatus.ERROR
    runtime.error_message = "ReplayClock cannot move backwards"
    manager.executor.runs[runtime.run_id] = runtime
    update_status = AsyncMock()
    mark_backtest = AsyncMock()
    manager._update_runtime_status = update_status
    manager._mark_backtest_error_safely = mark_backtest

    task = asyncio.create_task(asyncio.sleep(0))
    await task
    await manager._on_run_task_done(runtime.run_id, task)

    mark_backtest.assert_awaited_once_with(
      "backtest-clock-error",
      runtime.error_message,
    )
    update_status.assert_awaited_once_with(
      runtime.run_id,
      "ERROR",
      runtime.error_message,
    )
    StrategyManager._instance = None
