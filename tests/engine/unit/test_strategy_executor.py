"""
StrategyExecutor 单元测试

测试策略执行器的核心功能:
- create: 创建策略运行实例
- start: 启动策略运行
- stop: 停止策略运行
- pause: 暂停策略运行
- resume: 恢复策略运行
- delete: 删除策略运行
- get: 获取策略运行
- get_all: 获取所有运行
- get_running: 获取运行中的策略
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from quantx_domain.brokers.base import (
  OrderRequest,
  OrderResponse,
  OrderStatus,
  OrderType,
  PriceType,
)
from quantx_domain.brokers.simulator import SimulatorBroker
from quantx_domain.strategies.base import (
  OrderStateEvent,
  RuntimeStatePatch,
  StrategyBase,
  StrategyCadence,
  StrategyContext,
  StrategyInput,
  StrategyOutput,
  TradeExecutionEvent,
  TradeIntent,
  TradeIntentDirection,
)
from quantx_domain.trading import MarketDataSnapshot
from quantx_engine.strategy_executor import (
  ExecutionStatus,
  StrategyExecutor,
  StrategyRuntime,
)
from quantx_infrastructure.models.enums import StrategyRunMode


class MockStrategy(StrategyBase):
  """测试用的模拟策略"""

  @property
  def name(self) -> str:
    return "MockExecutorStrategy"

  @property
  def description(self) -> str:
    return "用于测试执行器的模拟策略"

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

  async def on_start(self):
    self.started = True

  async def on_stop(self):
    self.stopped = True

  async def step(self, input: StrategyInput) -> StrategyOutput:
    return StrategyOutput()


class PatchCallbackStrategy(MockStrategy):
  async def on_order(self, event: OrderStateEvent) -> RuntimeStatePatch:
    return RuntimeStatePatch(set={"order_seen": str(event.status)})

  async def on_trade(self, event: TradeExecutionEvent) -> RuntimeStatePatch:
    return RuntimeStatePatch(set={"trade_seen": int(event.volume or 0)})


async def keep_running_loop(runtime):
  """测试用：让执行循环保持运行，直到任务被取消。"""
  await asyncio.Event().wait()


@pytest.mark.unit
class TestStrategyExecutor:
  """StrategyExecutor 单元测试类"""

  @pytest.fixture
  async def strategy_executor(self):
    """创建策略执行器实例"""
    executor = StrategyExecutor(max_workers=2)
    yield executor
    await executor.shutdown()

  @pytest.mark.asyncio
  async def test_create_run(self, strategy_executor: StrategyExecutor):
    """测试 create 创建策略运行"""
    # 创建上下文
    run_id = "test-run-001"
    context = StrategyContext(
      run_id=run_id,
      mode=StrategyRunMode.BACKTEST,
      instruments=["000001.SZ"],
      parameters={"period": 20, "threshold": 0.02},
      initial_capital=1000000.0,
    )

    # 创建策略运行（同步方法，不需要 await）
    runtime = strategy_executor.create(
      run_id=run_id,
      strategy_id=1,
      strategy_class=MockStrategy,
      context=context,
    )

    # 验证运行时对象已创建
    assert runtime is not None
    assert runtime.run_id == run_id
    assert runtime.strategy_id == 1
    assert runtime.strategy_class == MockStrategy
    assert runtime.context.mode == StrategyRunMode.BACKTEST
    assert runtime.context.instruments == ["000001.SZ"]
    assert runtime.context.parameters == {"period": 20, "threshold": 0.02}
    assert runtime.context.initial_capital == 1000000.0
    assert runtime.status == ExecutionStatus.PENDING
    assert runtime.metrics is not None
    assert runtime.metrics.initial_capital == 1000000.0
    assert runtime.metrics.current_capital == 1000000.0

  @pytest.mark.asyncio
  async def test_strategy_input_uses_environment_layer(self, strategy_executor):
    run_id = "test-run-environment-layer"
    context = StrategyContext(
      run_id=run_id,
      mode=StrategyRunMode.BACKTEST,
      instruments=["000001.SZ"],
      parameters={
        "environment_context": {
          "market_return_1d": -0.05,
          "market_amount_ratio": 1.60,
          "advancing_count": 300,
          "declining_count": 4400,
          "limit_down_count": 120,
        }
      },
      initial_capital=1000000.0,
    )
    runtime = strategy_executor.create(
      run_id=run_id,
      strategy_id=101,
      strategy_class=MockStrategy,
      context=context,
    )
    runtime.context.current_time = datetime(2024, 1, 2, 10, 0)

    input_snapshot = strategy_executor._build_strategy_input(
      runtime,
      cadence=StrategyCadence.BAR,
      instrument_code="000001.SZ",
      timestamp=datetime(2024, 1, 2, 10, 0),
      market_data=MarketDataSnapshot(
        instrument_code="000001.SZ",
        timestamp=datetime(2024, 1, 2, 10, 0),
        price=10.0,
        close=10.0,
        volume=100_000,
        amount=1_000_000,
      ),
    )

    assert input_snapshot.market_context["market_state"] == "PANIC"
    assert input_snapshot.market_context["breadth_state"] == "EXTREME_NEGATIVE"
    assert input_snapshot.risk_caps["risk_mode"] == "PANIC"
    assert input_snapshot.position_profile["profile"] == "DEFENSIVE"

  @pytest.mark.asyncio
  async def test_strategy_order_and_trade_patches_are_consumed(self, strategy_executor):
    run_id = "test-run-callback-patch"
    context = StrategyContext(
      run_id=run_id,
      mode=StrategyRunMode.BACKTEST,
      instruments=["000001.SZ"],
      parameters={},
    )
    runtime = strategy_executor.create(
      run_id=run_id,
      strategy_id=102,
      strategy_class=PatchCallbackStrategy,
      context=context,
    )
    runtime.strategy = PatchCallbackStrategy(context)

    await strategy_executor._notify_strategy_order(
      runtime,
      OrderStateEvent(order_id="order-1", status="SUBMITTED"),
    )
    await strategy_executor._notify_strategy_trade(
      runtime,
      TradeExecutionEvent(
        order_id="order-1",
        instrument_code="000001.SZ",
        trade_type="BUY",
        price=10.0,
        volume=300,
      ),
    )

    assert runtime.strategy.state.order_seen == "SUBMITTED"
    assert runtime.strategy.state.trade_seen == 300

  @pytest.mark.asyncio
  async def test_synthetic_reject_consumes_order_patch(self, strategy_executor):
    run_id = "test-run-synthetic-reject-patch"
    context = StrategyContext(
      run_id=run_id,
      mode=StrategyRunMode.BACKTEST,
      instruments=["000001.SZ"],
      parameters={},
    )
    runtime = strategy_executor.create(
      run_id=run_id,
      strategy_id=103,
      strategy_class=PatchCallbackStrategy,
      context=context,
    )
    runtime.strategy = PatchCallbackStrategy(context)
    runtime.context.current_time = datetime(2024, 1, 2, 10, 0)

    await strategy_executor._process_trade_intent(
      runtime,
      TradeIntent(
        strategy_id="103",
        run_id=run_id,
        instrument_code="000001.SZ",
        direction=TradeIntentDirection.BUY,
        bucket="swing",
        reason="below_lot_unit_test",
        target_volume=50,
        limit_price_hint=10.0,
      ),
    )

    assert runtime.strategy.state.order_seen == "REJECTED"

  @pytest.mark.asyncio
  async def test_non_ready_whole_quote_gate_rejects_paper_order(
    self,
    strategy_executor,
  ):
    context = StrategyContext(
      run_id="test-paper-market-gate",
      mode=StrategyRunMode.PAPER,
      instruments=["000001.SZ"],
      parameters={},
      current_time=datetime(2024, 1, 2, 10, 0),
    )
    runtime = strategy_executor.create(
      run_id=context.run_id,
      strategy_id=104,
      strategy_class=PatchCallbackStrategy,
      context=context,
    )
    runtime.strategy = PatchCallbackStrategy(context)
    runtime.data_adapter = SimpleNamespace(
      subscription_manager=SimpleNamespace(
        hub=SimpleNamespace(is_ready=False),
      )
    )
    runtime.broker = SimpleNamespace(place_order=AsyncMock())

    await strategy_executor._process_trade_intent(
      runtime,
      TradeIntent(
        strategy_id="104",
        run_id=context.run_id,
        instrument_code="000001.SZ",
        direction=TradeIntentDirection.BUY,
        bucket="swing",
        reason="market_gate_test",
        target_volume=100,
        limit_price_hint=10.0,
      ),
    )

    runtime.broker.place_order.assert_not_awaited()
    assert runtime.strategy.state.order_seen == "REJECTED"

  @pytest.mark.asyncio
  async def test_missing_whole_quote_gate_fails_closed_for_paper_order(
    self,
    strategy_executor,
  ):
    context = StrategyContext(
      run_id="test-paper-missing-market-gate",
      mode=StrategyRunMode.PAPER,
      instruments=["000001.SZ"],
      parameters={},
      current_time=datetime(2024, 1, 2, 10, 0),
    )
    runtime = strategy_executor.create(
      run_id=context.run_id,
      strategy_id=105,
      strategy_class=PatchCallbackStrategy,
      context=context,
    )
    runtime.strategy = PatchCallbackStrategy(context)
    runtime.data_adapter = SimpleNamespace()
    runtime.broker = SimpleNamespace(place_order=AsyncMock())

    await strategy_executor._process_trade_intent(
      runtime,
      TradeIntent(
        strategy_id="105",
        run_id=context.run_id,
        instrument_code="000001.SZ",
        direction=TradeIntentDirection.BUY,
        bucket="swing",
        reason="missing_market_gate_test",
        target_volume=100,
        limit_price_hint=10.0,
      ),
    )

    runtime.broker.place_order.assert_not_awaited()
    assert runtime.strategy.state.order_seen == "REJECTED"

  @pytest.mark.asyncio
  async def test_paper_order_ttl_requests_cancel_and_prevents_late_fill(
    self,
    strategy_executor,
  ):
    timestamp = datetime(2024, 1, 2, 10, 0)
    context = StrategyContext(
      run_id="test-paper-order-ttl",
      mode=StrategyRunMode.PAPER,
      instruments=["000001.SZ"],
      parameters={},
      current_time=timestamp,
    )
    runtime = strategy_executor.create(
      run_id=context.run_id,
      strategy_id=103,
      strategy_class=PatchCallbackStrategy,
      context=context,
    )
    broker = SimulatorBroker(delay_mean=0, delay_std=0)
    request = OrderRequest(
      instrument_code="000001.SZ",
      order_type=OrderType.BUY,
      price_type=PriceType.LIMIT,
      volume=100,
      price=10.0,
      metadata={
        "order_expire_at_ms": int(timestamp.timestamp() * 1000) - 1,
      },
    )
    order = OrderResponse(
      order_id="paper-order-1",
      request=request,
      status=OrderStatus.SUBMITTED,
      submit_time=timestamp,
    )
    broker.orders[order.order_id] = order
    broker.realtime_prices["000001.SZ"] = 10.0
    runtime.broker = broker

    await strategy_executor._cancel_expired_strategy_orders(runtime, timestamp)
    await broker._process_order_async(order)

    assert order.status == OrderStatus.CANCELLED
    assert request.metadata["expiry_cancel_requested"] is True
    assert broker.trades == []

  def test_strategy_input_includes_open_orders_and_broker_health(
    self,
    strategy_executor,
  ):
    run_id = "test-run-open-orders"
    context = StrategyContext(
      run_id=run_id,
      mode=StrategyRunMode.BACKTEST,
      instruments=["000001.SZ"],
      parameters={},
    )
    runtime = strategy_executor.create(
      run_id=run_id,
      strategy_id=104,
      strategy_class=MockStrategy,
      context=context,
    )
    runtime.context.current_time = datetime(2024, 1, 2, 10, 0)
    request = OrderRequest(
      instrument_code="000001.SZ",
      order_type=OrderType.BUY,
      price_type=PriceType.LIMIT,
      volume=500,
      price=10.0,
      metadata={"intent_id": "intent-1", "bucket": "swing"},
    )
    order = OrderResponse(
      order_id="order-1",
      request=request,
      status=OrderStatus.SUBMITTED,
      submit_time=datetime(2024, 1, 2, 9, 59),
      filled_volume=100,
    )
    runtime.broker = SimpleNamespace(orders={"order-1": order}, pending_orders=[])
    runtime.last_order_report_at = datetime(2024, 1, 2, 9, 59)
    runtime.last_broker_report_at = datetime(2024, 1, 2, 9, 59)

    input_snapshot = strategy_executor._build_strategy_input(
      runtime,
      cadence=StrategyCadence.BAR,
      instrument_code="000001.SZ",
      timestamp=datetime(2024, 1, 2, 10, 0),
      market_data=MarketDataSnapshot(
        instrument_code="000001.SZ",
        timestamp=datetime(2024, 1, 2, 10, 0),
        price=10.0,
        close=10.0,
        limit_up=11.0,
        limit_down=9.0,
      ),
    )

    assert input_snapshot.open_orders == [
      {
        "order_id": "order-1",
        "status": "SUBMITTED",
        "instrument_code": "000001.SZ",
        "order_type": "BUY",
        "price_type": "LIMIT",
        "price": 10.0,
        "volume": 500,
        "filled_volume": 100,
        "remaining_volume": 400,
        "submit_time": "2024-01-02T09:59:00",
        "last_update_time": None,
        "metadata": {"intent_id": "intent-1", "bucket": "swing"},
      }
    ]
    metadata = input_snapshot.risk_caps["metadata"]
    assert metadata["order_state"]["open_order_count"] == 1
    assert metadata["order_state"]["buy_open_order_count"] == 1
    assert metadata["broker_report"]["report_lag_seconds"] == 60.0

  def test_order_risk_strict_flags_default_by_mode(self, strategy_executor):
    backtest_runtime = strategy_executor.create(
      run_id="strict-backtest",
      strategy_id=105,
      strategy_class=MockStrategy,
      context=StrategyContext(
        run_id="strict-backtest",
        mode=StrategyRunMode.BACKTEST,
        instruments=[],
        parameters={},
      ),
    )
    paper_runtime = strategy_executor.create(
      run_id="strict-paper",
      strategy_id=106,
      strategy_class=MockStrategy,
      context=StrategyContext(
        run_id="strict-paper",
        mode=StrategyRunMode.PAPER,
        instruments=[],
        parameters={},
      ),
    )
    override_runtime = strategy_executor.create(
      run_id="strict-override",
      strategy_id=107,
      strategy_class=MockStrategy,
      context=StrategyContext(
        run_id="strict-override",
        mode=StrategyRunMode.BACKTEST,
        instruments=[],
        parameters={
          "strict_market_data": "false",
          "strict_limit_data": False,
        },
      ),
    )

    assert strategy_executor._order_risk_strict_flags(backtest_runtime) == (True, True)
    assert strategy_executor._order_risk_strict_flags(paper_runtime) == (True, False)
    assert strategy_executor._order_risk_strict_flags(override_runtime) == (
      False,
      False,
    )

  def test_backtest_continuous_session_filter_excludes_call_auction_ticks(
    self,
    strategy_executor,
  ):
    """回测 tick 回放应忽略早盘和尾盘集合竞价。"""
    from types import SimpleNamespace

    events = [
      SimpleNamespace(time=datetime(2026, 5, 14, 9, 15), label="open_call"),
      SimpleNamespace(time=datetime(2026, 5, 14, 9, 25), label="open_call_end"),
      SimpleNamespace(time=datetime(2026, 5, 14, 9, 30), label="morning_open"),
      SimpleNamespace(time=datetime(2026, 5, 14, 11, 30), label="morning_close"),
      SimpleNamespace(time=datetime(2026, 5, 14, 13, 0), label="afternoon_open"),
      SimpleNamespace(time=datetime(2026, 5, 14, 14, 56, 59), label="before_close_call"),
      SimpleNamespace(time=datetime(2026, 5, 14, 14, 57), label="close_call"),
      SimpleNamespace(time=datetime(2026, 5, 14, 15, 0), label="close_call_end"),
    ]

    filtered = strategy_executor._filter_backtest_continuous_session_events(events)

    assert [event.label for event in filtered] == [
      "morning_open",
      "morning_close",
      "afternoon_open",
      "before_close_call",
    ]

  def test_backtest_intraday_period_detection(self, strategy_executor):
    """只有日内周期才按集合竞价过滤，日线不参与过滤。"""
    assert strategy_executor._is_backtest_intraday_period("1m") is True
    assert strategy_executor._is_backtest_intraday_period("60m") is True
    assert strategy_executor._is_backtest_intraday_period("1h") is True
    assert strategy_executor._is_backtest_intraday_period("1d") is False

  @pytest.mark.asyncio
  async def test_create_multiple_runs(self, strategy_executor):
    """测试创建多个策略运行"""
    run_ids = []

    for i in range(3):
      run_id = f"test-run-{i:03d}"
      context = StrategyContext(
        run_id=run_id,
        mode=StrategyRunMode.BACKTEST,
        instruments=[f"00000{i}.SZ"],
        parameters={"index": i},
        initial_capital=1000000.0,
      )

      runtime = strategy_executor.create(
        run_id=run_id,
        strategy_id=i,
        strategy_class=MockStrategy,
        context=context,
      )
      run_ids.append(run_id)

      assert runtime.run_id == run_id
      assert runtime.strategy_id == i

    # 验证所有运行都已创建
    assert len(strategy_executor.runs) == 3
    assert all(rid in strategy_executor.runs for rid in run_ids)

  @pytest.mark.asyncio
  async def test_start_run(self, strategy_executor):
    """测试 start 启动策略运行"""
    # Mock 数据适配器
    with patch('quantx_engine.strategy_executor.adapter_manager.get_adapter_for_mode') as mock_get_adapter:
      mock_data_adapter = AsyncMock()
      mock_data_adapter.connect = AsyncMock()
      mock_data_adapter.subscribe_kline = AsyncMock(return_value="subscription-id")
      mock_get_adapter.return_value = mock_data_adapter

      # Mock BacktestBroker
      with patch('quantx_engine.strategy_executor.BacktestBroker') as mock_broker_class:
        mock_broker = AsyncMock()
        mock_broker.connect = AsyncMock()
        mock_broker_class.return_value = mock_broker

        # 创建策略运行
        run_id = "test-run-start"
        context = StrategyContext(
          run_id=run_id,
          mode=StrategyRunMode.BACKTEST,
          instruments=["000001.SZ"],
          parameters={},
          initial_capital=1000000.0,
        )

        runtime = strategy_executor.create(
          run_id=run_id,
          strategy_id=2,
          strategy_class=MockStrategy,
          context=context,
        )

        # 启动策略
        success = await strategy_executor.start(run_id)
        assert success is True

        # 验证状态
        assert runtime.status == ExecutionStatus.RUNNING
        assert runtime.strategy is not None
        assert runtime.broker is not None
        assert runtime.data_adapter is not None
        assert runtime.task is not None

        # 验证 mock 调用
        mock_get_adapter.assert_called_once_with(StrategyRunMode.BACKTEST)
        mock_broker.connect.assert_called_once()
        mock_data_adapter.connect.assert_called_once()

  @pytest.mark.asyncio
  async def test_start_nonexistent_run(self, strategy_executor):
    """测试启动不存在的策略运行"""
    success = await strategy_executor.start("nonexistent-run-id")
    assert success is False

  @pytest.mark.asyncio
  async def test_stop_run(self, strategy_executor):
    """测试 stop 停止策略运行"""
    # Mock 数据适配器
    with patch('quantx_engine.strategy_executor.adapter_manager.get_adapter_for_mode') as mock_get_adapter:
      mock_data_adapter = AsyncMock()
      mock_data_adapter.connect = AsyncMock()
      mock_get_adapter.return_value = mock_data_adapter

      # Mock release_adapter_for_mode
      with patch('quantx_engine.strategy_executor.adapter_manager.release_adapter_for_mode') as mock_release:
        # Mock BacktestBroker
        with patch('quantx_engine.strategy_executor.BacktestBroker') as mock_broker_class:
          mock_broker = AsyncMock()
          mock_broker.connect = AsyncMock()
          mock_broker.disconnect = AsyncMock()
          mock_broker.get_performance_metrics = MagicMock(return_value={
            "final_equity": 1050000.0,
            "max_drawdown": 0.02,
            "win_rate": 0.6,
            "sharpe_ratio": 1.5,
            "total_trades": 10,
          })
          mock_broker_class.return_value = mock_broker

          # 创建并启动策略
          run_id = "test-run-stop"
          context = StrategyContext(
            run_id=run_id,
            mode=StrategyRunMode.BACKTEST,
            instruments=["000001.SZ"],
            parameters={},
            initial_capital=1000000.0,
          )

          runtime = strategy_executor.create(
            run_id=run_id,
            strategy_id=3,
            strategy_class=MockStrategy,
            context=context,
          )

          await strategy_executor.start(run_id)
          await asyncio.sleep(0.1)  # 等待启动

          # 停止策略
          success = await strategy_executor.stop(run_id)
          assert success is True

          # 验证状态
          assert runtime.status == ExecutionStatus.STOPPED
          assert runtime.metrics.end_time is not None

          # 验证资源清理
          mock_broker.disconnect.assert_called_once()
          mock_release.assert_called_once_with("backtest")

  @pytest.mark.asyncio
  async def test_pause_and_resume_run(self, strategy_executor):
    """测试 pause 和 resume"""
    # Mock 数据适配器
    with patch('quantx_engine.strategy_executor.adapter_manager.get_adapter_for_mode') as mock_get_adapter:
      mock_data_adapter = AsyncMock()
      mock_data_adapter.connect = AsyncMock()
      mock_get_adapter.return_value = mock_data_adapter

      # Mock SimulatorBroker (PAPER 模式)
      with patch('quantx_engine.strategy_executor.SimulatorBroker') as mock_broker_class:
        mock_broker = AsyncMock()
        mock_broker.connect = AsyncMock()
        mock_broker_class.return_value = mock_broker

        # 创建并启动策略 (模拟盘模式)
        run_id = "test-run-pause-resume"
        context = StrategyContext(
          run_id=run_id,
          mode=StrategyRunMode.PAPER,
          instruments=["600519.SH"],
          parameters={},
          initial_capital=1000000.0,
        )

        runtime = strategy_executor.create(
          run_id=run_id,
          strategy_id=4,
          strategy_class=MockStrategy,
          context=context,
        )

        await strategy_executor.start(run_id)
        await asyncio.sleep(0.1)

        # 暂停策略
        success = await strategy_executor.pause(run_id)
        assert success is True
        assert runtime.status == ExecutionStatus.PAUSED

        # 恢复策略
        success = await strategy_executor.resume(run_id)
        assert success is True
        assert runtime.status == ExecutionStatus.RUNNING

  @pytest.mark.asyncio
  async def test_delete_run(self, strategy_executor):
    """测试 delete 删除策略运行"""
    # 创建策略运行
    run_id = "test-run-delete"
    context = StrategyContext(
      run_id=run_id,
      mode=StrategyRunMode.BACKTEST,
      instruments=["000001.SZ"],
      parameters={},
      initial_capital=1000000.0,
    )

    strategy_executor.create(
      run_id=run_id,
      strategy_id=5,
      strategy_class=MockStrategy,
      context=context,
    )

    assert run_id in strategy_executor.runs

    # 删除策略运行
    success = await strategy_executor.delete(run_id)
    assert success is True

    # 验证运行时对象已删除
    assert run_id not in strategy_executor.runs

  @pytest.mark.asyncio
  async def test_delete_nonexistent_run(self, strategy_executor):
    """测试删除不存在的策略运行"""
    success = await strategy_executor.delete("nonexistent-run-id")
    assert success is False

  @pytest.mark.asyncio
  async def test_get_run(self, strategy_executor):
    """测试 get 获取策略运行"""
    # 创建策略运行
    run_id = "test-run-get"
    context = StrategyContext(
      run_id=run_id,
      mode=StrategyRunMode.BACKTEST,
      instruments=["000001.SZ"],
      parameters={},
      initial_capital=1000000.0,
    )

    strategy_executor.create(
      run_id=run_id,
      strategy_id=6,
      strategy_class=MockStrategy,
      context=context,
    )

    # 获取策略运行
    runtime = strategy_executor.get(run_id)
    assert runtime is not None
    assert runtime.run_id == run_id

    # 获取不存在的策略运行
    nonexistent = strategy_executor.get("nonexistent-run-id")
    assert nonexistent is None

  @pytest.mark.asyncio
  async def test_get_all_runs(self, strategy_executor):
    """测试 get_all 获取所有运行"""
    # 创建多个策略运行
    for i in range(3):
      run_id = f"test-run-all-{i}"
      context = StrategyContext(
        run_id=run_id,
        mode=StrategyRunMode.BACKTEST,
        instruments=[f"00000{i}.SZ"],
        parameters={},
        initial_capital=1000000.0,
      )

      strategy_executor.create(
        run_id=run_id,
        strategy_id=i,
        strategy_class=MockStrategy,
        context=context,
      )

    # 获取所有运行
    all_runs = strategy_executor.get_all()
    assert len(all_runs) == 3
    assert all(isinstance(r, StrategyRuntime) for r in all_runs)

  @pytest.mark.asyncio
  async def test_get_running_runs(self, strategy_executor):
    """测试 get_running 获取运行中的策略"""
    # Mock 数据适配器
    with patch('quantx_engine.strategy_executor.adapter_manager.get_adapter_for_mode') as mock_get_adapter:
      mock_data_adapter = AsyncMock()
      mock_data_adapter.connect = AsyncMock()
      mock_get_adapter.return_value = mock_data_adapter

      # Mock BacktestBroker
      with patch('quantx_engine.strategy_executor.BacktestBroker') as mock_broker_class:
        mock_broker = AsyncMock()
        mock_broker.connect = AsyncMock()
        mock_broker_class.return_value = mock_broker

        # 创建多个运行，部分启动
        run_ids = []
        for i in range(3):
          run_id = f"test-run-running-{i}"
          context = StrategyContext(
            run_id=run_id,
            mode=StrategyRunMode.BACKTEST,
            instruments=[f"00000{i}.SZ"],
            parameters={},
            initial_capital=1000000.0,
          )

          strategy_executor.create(
            run_id=run_id,
            strategy_id=i,
            strategy_class=MockStrategy,
            context=context,
          )
          run_ids.append(run_id)

        # 只启动前两个
        with patch.object(strategy_executor, "_run_strategy_loop", side_effect=keep_running_loop):
          await strategy_executor.start(run_ids[0])
          await strategy_executor.start(run_ids[1])
          await asyncio.sleep(0.1)

          # 获取运行中的策略
          running_runs = strategy_executor.get_running()
          assert len(running_runs) == 2
          assert all(r.status == ExecutionStatus.RUNNING for r in running_runs)

  @pytest.mark.asyncio
  async def test_multiple_runs_concurrent(self, strategy_executor):
    """测试多个策略并发运行"""
    # Mock 数据适配器
    with patch('quantx_engine.strategy_executor.adapter_manager.get_adapter_for_mode') as mock_get_adapter:
      mock_data_adapter = AsyncMock()
      mock_data_adapter.connect = AsyncMock()
      mock_get_adapter.return_value = mock_data_adapter

      # Mock BacktestBroker
      with patch('quantx_engine.strategy_executor.BacktestBroker') as mock_broker_class:
        mock_broker = AsyncMock()
        mock_broker.connect = AsyncMock()
        mock_broker.disconnect = AsyncMock()
        # Mock get_performance_metrics 为同步方法
        mock_broker.get_performance_metrics = MagicMock(return_value={})
        mock_broker_class.return_value = mock_broker

        # Mock release_adapter_for_mode
        with patch('quantx_engine.strategy_executor.adapter_manager.release_adapter_for_mode'):
          # 创建多个策略运行
          run_ids = []
          for i in range(3):
            run_id = f"test-run-concurrent-{i}"
            context = StrategyContext(
              run_id=run_id,
              mode=StrategyRunMode.BACKTEST,
              instruments=[f"00000{i}.SZ"],
              parameters={"run_id": i},
              initial_capital=1000000.0,
            )

            strategy_executor.create(
              run_id=run_id,
              strategy_id=10 + i,
              strategy_class=MockStrategy,
              context=context,
            )
            run_ids.append(run_id)

          # 并发启动所有策略
          start_tasks = [
            strategy_executor.start(run_id)
            for run_id in run_ids
          ]
          results = await asyncio.gather(*start_tasks)

          # 验证所有策略都启动成功
          assert all(results) is True

          # 等待运行
          await asyncio.sleep(0.2)

          # 并发停止所有策略
          stop_tasks = [
            strategy_executor.stop(run_id)
            for run_id in run_ids
          ]
          results = await asyncio.gather(*stop_tasks)

          # 验证所有策略都停止成功
          assert all(results) is True

  @pytest.mark.asyncio
  async def test_metrics_update_on_stop(self, strategy_executor):
    """测试停止时更新指标"""
    # Mock 数据适配器
    with patch('quantx_engine.strategy_executor.adapter_manager.get_adapter_for_mode') as mock_get_adapter:
      mock_data_adapter = AsyncMock()
      mock_data_adapter.connect = AsyncMock()
      mock_get_adapter.return_value = mock_data_adapter

      # Mock release_adapter_for_mode
      with patch('quantx_engine.strategy_executor.adapter_manager.release_adapter_for_mode'):
        # Mock BacktestBroker with performance metrics
        with patch('quantx_engine.strategy_executor.BacktestBroker') as mock_broker_class:
          mock_broker = AsyncMock()
          mock_broker.connect = AsyncMock()
          mock_broker.disconnect = AsyncMock()
          mock_broker.get_performance_metrics = MagicMock(return_value={
            "final_equity": 1050000.0,
            "max_drawdown": 0.02,
            "win_rate": 0.6,
            "sharpe_ratio": 1.5,
            "total_trades": 10,
          })
          mock_broker_class.return_value = mock_broker

          # 创建并启动策略
          run_id = "test-run-metrics"
          context = StrategyContext(
            run_id=run_id,
            mode=StrategyRunMode.BACKTEST,
            instruments=["000001.SZ"],
            parameters={},
            initial_capital=1000000.0,
          )

          runtime = strategy_executor.create(
            run_id=run_id,
            strategy_id=7,
            strategy_class=MockStrategy,
            context=context,
          )

          await strategy_executor.start(run_id)
          await asyncio.sleep(0.1)

          # 停止策略
          await strategy_executor.stop(run_id)

          # 验证指标已更新
          assert runtime.metrics is not None
          assert runtime.metrics.end_time is not None
          assert runtime.metrics.total_pnl == 50000.0  # 1050000 - 1000000
          assert runtime.metrics.max_drawdown == 0.02
          assert runtime.metrics.win_rate == 0.6
          assert runtime.metrics.sharpe_ratio == 1.5
          assert runtime.metrics.trades_executed == 10
          assert runtime.metrics.current_capital == 1050000.0

  @pytest.mark.asyncio
  async def test_error_handling_on_start(self, strategy_executor):
    """测试启动时的错误处理"""
    # Mock 数据适配器抛出异常
    with patch('quantx_engine.strategy_executor.adapter_manager.get_adapter_for_mode') as mock_get_adapter:
      mock_get_adapter.side_effect = Exception("数据适配器错误")

      # 创建策略
      run_id = "test-run-error"
      context = StrategyContext(
        run_id=run_id,
        mode=StrategyRunMode.BACKTEST,
        instruments=["000001.SZ"],
        parameters={},
        initial_capital=1000000.0,
      )

      runtime = strategy_executor.create(
        run_id=run_id,
        strategy_id=8,
        strategy_class=MockStrategy,
        context=context,
      )

      # 启动应该失败
      success = await strategy_executor.start(run_id)
      assert success is False

      # 验证错误状态
      assert runtime.status == ExecutionStatus.ERROR
      assert runtime.error_message is not None
      assert "数据适配器错误" in runtime.error_message

  @pytest.mark.asyncio
  async def test_different_modes(self, strategy_executor):
    """测试不同运行模式"""
    modes = [
      (StrategyRunMode.BACKTEST, "BacktestBroker"),
      (StrategyRunMode.PAPER, "SimulatorBroker"),
      (StrategyRunMode.LIVE, "LiveBroker"),
    ]

    for mode, broker_class in modes:
      with patch('quantx_engine.strategy_executor.adapter_manager.get_adapter_for_mode') as mock_get_adapter:
        mock_data_adapter = AsyncMock()
        mock_data_adapter.connect = AsyncMock()
        mock_get_adapter.return_value = mock_data_adapter

        # Mock 对应的 broker
        broker_patch_path = f"quantx_engine.strategy_executor.{broker_class}"
        with patch(broker_patch_path) as mock_broker_class:
          mock_broker = AsyncMock()
          mock_broker.connect = AsyncMock()
          mock_broker_class.return_value = mock_broker

          # 创建策略
          run_id = f"test-run-{mode.value.lower()}"
          context = StrategyContext(
            run_id=run_id,
            mode=mode,
            instruments=["000001.SZ"],
            parameters={},
            initial_capital=1000000.0,
          )

          runtime = strategy_executor.create(
            run_id=run_id,
            strategy_id=100 + modes.index((mode, broker_class)),
            strategy_class=MockStrategy,
            context=context,
          )

          # 启动策略
          with patch.object(strategy_executor, "_run_strategy_loop", side_effect=keep_running_loop):
            await strategy_executor.start(run_id)
            await asyncio.sleep(0.1)

            # 验证运行时对象模式
            assert runtime.context.mode == mode
            assert runtime.status == ExecutionStatus.RUNNING

            # 验证正确的 broker 被创建
            mock_broker_class.assert_called_once()

            # 验证正确的适配器被获取
            mock_get_adapter.assert_called_with(mode)

  @pytest.mark.asyncio
  async def test_paper_setup_uses_simulator_broker_not_live(
    self,
    strategy_executor: StrategyExecutor,
  ):
    """PAPER 模式只应创建 SimulatorBroker，不触发实盘 Broker。"""
    context = StrategyContext(
      run_id="paper-run",
      mode=StrategyRunMode.PAPER,
      instruments=["688552.SH"],
      parameters={},
      initial_capital=250000.0,
    )
    runtime = StrategyRuntime(
      run_id="paper-run",
      name="Paper Run",
      strategy_id=1,
      strategy_class=MockStrategy,
      context=context,
    )

    with (
      patch("quantx_engine.strategy_executor.LiveBroker") as live_broker,
      patch("quantx_engine.strategy_executor.SimulatorBroker") as simulator_broker,
      patch("quantx_engine.strategy_executor.adapter_manager.get_adapter_for_mode") as get_adapter,
    ):
      broker = AsyncMock()
      broker.connect = AsyncMock(return_value=True)
      broker.subscribe_order_updates = MagicMock()
      broker.subscribe_trade_updates = MagicMock()
      simulator_broker.return_value = broker

      adapter = AsyncMock()
      adapter.connect = AsyncMock(return_value=True)
      get_adapter.return_value = adapter

      await strategy_executor._setup_broker_and_data(runtime)

    simulator_broker.assert_called_once_with(
      account_id="paper-run",
      initial_capital=250000.0,
    )
    live_broker.assert_not_called()

  @pytest.mark.asyncio
  async def test_paper_broker_seeds_initial_holdings(
    self,
    strategy_executor: StrategyExecutor,
  ):
    """模拟盘 broker 应从策略参数注入初始虚拟持仓。"""
    run_id = "paper-seed-run"
    context = StrategyContext(
      run_id=run_id,
      mode=StrategyRunMode.PAPER,
      instruments=["688552.SH"],
      parameters={
        "instrument_code": "688552.SH",
        "position_shares": 500,
        "locked_core_shares": 100,
        "core_shares": 200,
        "swing_shares": 200,
        "avg_cost": 40.0,
        "base_price": 42.0,
      },
      initial_capital=100000.0,
    )
    runtime = strategy_executor.create(
      run_id=run_id,
      strategy_id=2,
      strategy_class=MockStrategy,
      context=context,
    )

    with (
      patch("quantx_engine.strategy_executor.adapter_manager.get_adapter_for_mode") as get_adapter,
      patch.object(
        strategy_executor,
        "_run_strategy_loop",
        side_effect=keep_running_loop,
      ),
    ):
      adapter = AsyncMock()
      adapter.connect = AsyncMock(return_value=True)
      get_adapter.return_value = adapter

      success = await strategy_executor.start(run_id)

    assert success is True
    assert runtime.broker is not None
    position = runtime.broker.positions["688552.SH"]
    assert position.long_volume == 500
    assert position.available_volume == 500
    assert position.long_avg_price == 40.0
    assert position.last_price == 42.0
    account = await runtime.broker.get_account()
    assert account.cash == 100000.0
    assert account.total_asset == 121000.0

  @pytest.mark.asyncio
  async def test_realtime_loop_subscribes_context_instruments(
    self,
    strategy_executor: StrategyExecutor,
  ):
    """实时模式订阅应优先使用 context.instruments。"""
    context = StrategyContext(
      run_id="paper-context-instrument",
      mode=StrategyRunMode.PAPER,
      instruments=["688552.SH"],
      parameters={},
      initial_capital=100000.0,
    )
    adapter = AsyncMock()
    adapter.subscribe_kline = AsyncMock(return_value="sub-001")
    adapter.unsubscribe = AsyncMock(return_value=True)
    broker = AsyncMock()
    broker.get_position = AsyncMock(return_value={})
    broker.get_account = AsyncMock(
      return_value=SimpleNamespace(
        cash=100000.0,
        total_asset=100000.0,
        frozen_cash=0.0,
        market_value=0.0,
        total_pnl=0.0,
        daily_pnl=0.0,
      )
    )
    runtime = SimpleNamespace(
      run_id="paper-context-instrument",
      status=ExecutionStatus.RUNNING,
      context=context,
      data_adapter=adapter,
      broker=broker,
      event_queue=asyncio.Queue(),
      latest_market_data={},
      metrics=SimpleNamespace(last_heartbeat=None),
      realtime_subscription_ids={},
      realtime_subscription_lock=asyncio.Lock(),
      state_manager=None,
      strategy=None,
    )

    async def stop_after_heartbeat(_seconds):
      runtime.status = ExecutionStatus.STOPPED

    with patch("quantx_engine.strategy_executor.asyncio.sleep", side_effect=stop_after_heartbeat):
      await strategy_executor._run_realtime_loop(runtime)

    adapter.subscribe_kline.assert_awaited_once()
    assert adapter.subscribe_kline.await_args.kwargs["instrument_code"] == "688552.SH"
    adapter.unsubscribe.assert_awaited_once_with("sub-001")

  @pytest.mark.asyncio
  async def test_stop_all_runs(self, strategy_executor):
    """测试停止所有运行"""
    # Mock 数据适配器
    with patch('quantx_engine.strategy_executor.adapter_manager.get_adapter_for_mode') as mock_get_adapter:
      mock_data_adapter = AsyncMock()
      mock_data_adapter.connect = AsyncMock()
      mock_get_adapter.return_value = mock_data_adapter

      # Mock BacktestBroker
      with patch('quantx_engine.strategy_executor.BacktestBroker') as mock_broker_class:
        mock_broker = AsyncMock()
        mock_broker.connect = AsyncMock()
        mock_broker.disconnect = AsyncMock()
        # Mock get_performance_metrics 为同步方法
        mock_broker.get_performance_metrics = MagicMock(return_value={})
        mock_broker_class.return_value = mock_broker

        # Mock release_adapter_for_mode
        with patch('quantx_engine.strategy_executor.adapter_manager.release_adapter_for_mode'):
          # 创建并启动多个策略
          run_ids = []
          for i in range(3):
            run_id = f"test-run-stopall-{i}"
            context = StrategyContext(
              run_id=run_id,
              mode=StrategyRunMode.BACKTEST,
              instruments=[f"00000{i}.SZ"],
              parameters={},
              initial_capital=1000000.0,
            )

            strategy_executor.create(
              run_id=run_id,
              strategy_id=20 + i,
              strategy_class=MockStrategy,
              context=context,
            )
            run_ids.append(run_id)

          # 启动所有策略
          for run_id in run_ids:
            await strategy_executor.start(run_id)
          await asyncio.sleep(0.1)

          # 停止所有运行
          await strategy_executor.stop_all_runs()

          # 验证所有策略都已停止
          for run_id in run_ids:
            runtime = strategy_executor.get(run_id)
            assert runtime.status == ExecutionStatus.STOPPED

  @pytest.mark.asyncio
  async def test_get_statistics(self, strategy_executor):
    """测试获取执行器统计信息"""
    # 创建不同状态的运行
    run_id_1 = "test-run-stats-1"
    context_1 = StrategyContext(
      run_id=run_id_1,
      mode=StrategyRunMode.BACKTEST,
      instruments=["000001.SZ"],
      parameters={},
      initial_capital=1000000.0,
    )
    strategy_executor.create(
      run_id=run_id_1,
      strategy_id=30,
      strategy_class=MockStrategy,
      context=context_1,
    )

    run_id_2 = "test-run-stats-2"
    context_2 = StrategyContext(
      run_id=run_id_2,
      mode=StrategyRunMode.BACKTEST,
      instruments=["000002.SZ"],
      parameters={},
      initial_capital=1000000.0,
    )
    strategy_executor.create(
      run_id=run_id_2,
      strategy_id=31,
      strategy_class=MockStrategy,
      context=context_2,
    )

    # 获取统计信息
    stats = strategy_executor.get_statistics()
    assert stats["total_runs"] == 2
    assert stats["max_workers"] == 2
    assert "status_distribution" in stats
    assert stats["status_distribution"]["PENDING"] == 2
    assert stats["running_runs"] == 0
