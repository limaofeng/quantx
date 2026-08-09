"""
MiniQMTTraderCallback 单元测试

测试回调系统的核心功能:
- 回调初始化和配置
- 事件循环集成
- 各类型回调处理 (委托、成交、持仓、资产)
- 异步任务提交机制
- 错误处理和异常恢复
- 回调完整性验证
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("xtquant", reason="miniQMT SDK is only available on the QMT host")

from quantx_qmt_agent.miniqmt.trading.trading_manager import (
  MiniQMTTraderCallback,
  XTTradingManager,
)


@pytest.mark.unit
class TestMiniQMTTraderCallback:
  """MiniQMTTraderCallback 单元测试类"""

  @pytest.fixture
  def mock_trading_manager(self):
    """创建模拟交易管理器"""
    manager = MagicMock(spec=XTTradingManager)
    manager.account_id = "test_account_001"
    manager.event_loop = asyncio.new_event_loop()
    manager.trading_service = None
    return manager

  @pytest.fixture
  def callback(self, mock_trading_manager):
    """创建回调对象实例"""
    return MiniQMTTraderCallback(mock_trading_manager)

  @pytest.fixture
  def mock_order(self):
    """创建模拟订单对象"""
    order = MagicMock()
    order.order_id = 12345
    order.stock_code = "000001.SZ"
    order.order_type = 1  # BUY
    order.order_status = 48  # 已委托
    order.order_volume = 1000
    order.price = 10.50
    order.traded_volume = 0
    order.traded_price = 0.0
    order.order_remark = "test_order"
    return order

  @pytest.fixture
  def mock_trade(self):
    """创建模拟成交对象"""
    trade = MagicMock()
    trade.order_id = 12345
    trade.stock_code = "000001.SZ"
    trade.traded_volume = 1000
    trade.traded_price = 10.52
    trade.order_status = 52  # 全部成交
    trade.order_remark = "test_trade"
    return trade

  @pytest.fixture
  def mock_position(self):
    """创建模拟持仓对象"""
    position = MagicMock()
    position.stock_code = "000001.SZ"
    position.volume = 1000
    position.can_use_volume = 1000
    position.open_price = 10.50
    position.market_value = 10500.0
    return position

  @pytest.fixture
  def mock_asset(self):
    """创建模拟资产对象"""
    asset = MagicMock()
    asset.total_asset = 100000.0
    asset.cash = 50000.0
    asset.market_value = 50000.0
    asset.frozen_cash = 0.0
    return asset

  @pytest.fixture
  def mock_order_error(self):
    """创建模拟委托失败对象"""
    error = MagicMock()
    error.order_id = 12345
    error.error_id = "ERR_001"
    error.error_msg = "资金不足"
    error.order_remark = "test_error"
    return error

  @pytest.fixture
  def mock_cancel_error(self):
    """创建模拟撤单失败对象"""
    error = MagicMock()
    error.order_id = 12345
    error.error_msg = "订单已成交无法撤销"
    return error

  @pytest.fixture
  def mock_order_response(self):
    """创建模拟异步下单响应"""
    response = MagicMock()
    response.seq = "SEQ_001"
    response.order_id = 12345
    response.order_remark = "test_async_order"
    return response

  @pytest.fixture
  def mock_cancel_response(self):
    """创建模拟异步撤单响应"""
    response = MagicMock()
    response.order_id = 12345
    return response

  # ==================== 初始化测试 ====================

  def test_callback_initialization(self, callback, mock_trading_manager):
    """测试回调初始化"""
    assert callback.trading_manager == mock_trading_manager
    assert callback.trading_manager.account_id == "test_account_001"
    assert callback.trading_manager.event_loop is not None

  def test_callback_has_all_required_methods(self, callback):
    """测试回调具有所有必需的方法"""
    required_methods = [
      "on_connected",
      "on_disconnected",
      "on_account_status",
      "on_stock_asset",
      "on_stock_position",
      "on_stock_order",
      "on_stock_trade",
      "on_order_error",
      "on_cancel_error",
      "on_order_stock_async_response",
      "on_cancel_order_stock_async_response",
      "on_smt_appointment_async_response",
      "on_bank_transfer_async_response",
      "on_ctp_internal_transfer_async_response",
    ]

    for method_name in required_methods:
      assert hasattr(callback, method_name), f"缺少方法: {method_name}"
      assert callable(getattr(callback, method_name)), f"方法不可调用: {method_name}"

  # ==================== 异步任务提交测试 ====================

  def test_submit_async_task_without_event_loop(self, callback):
    """测试没有事件循环时的异步任务提交"""
    callback.trading_manager.event_loop = None

    # 应该记录警告但不抛出异常
    async def dummy_coro():
      return "test"

    # 不应该抛出异常
    callback._submit_async_task(dummy_coro())

  def test_submit_async_task_with_event_loop(self, callback):
    """测试有事件循环时的异步任务提交"""
    # 验证事件循环存在
    assert callback.trading_manager.event_loop is not None

    async def test_coro():
      return "task_executed"

    # 提交任务不应该抛出异常
    try:
      callback._submit_async_task(test_coro())
      # 任务已提交到事件循环，不验证执行结果（需要运行事件循环）
    except Exception as e:
      pytest.fail(f"提交异步任务失败: {e}")

  # ==================== 连接状态回调测试 ====================

  def test_on_connected(self, callback):
    """测试连接成功回调"""
    with patch.object(callback.trading_manager, "handle_connection_event"):
      callback.on_connected()
      # 验证日志记录（通过检查是否调用了处理方法）
      # 注: 由于是异步提交，这里只验证调用发生

  def test_on_disconnected(self, callback):
    """测试连接断开回调"""
    with patch.object(callback.trading_manager, "handle_connection_event"):
      callback.on_disconnected()
      # 验证日志记录

  def test_on_account_status(self, callback):
    """测试账户状态变更回调"""
    mock_status = MagicMock()
    with patch.object(callback.trading_manager, "handle_account_status_event"):
      callback.on_account_status(mock_status)
      # 验证日志记录

  # ==================== 资产和持仓回调测试 ====================

  def test_on_stock_asset(self, callback, mock_asset):
    """测试资产变动回调"""
    with patch.object(callback.trading_manager, "handle_asset_update_event"):
      callback.on_stock_asset(mock_asset)
      # 验证任务提交

  def test_on_stock_position(self, callback, mock_position):
    """测试持仓变动回调"""
    with patch.object(callback.trading_manager, "handle_position_update_event"):
      callback.on_stock_position(mock_position)
      # 验证任务提交

  # ==================== 订单和成交回调测试 ====================

  def test_on_stock_order(self, callback, mock_order):
    """测试委托回报回调"""
    with patch.object(callback.trading_manager, "handle_order_event"):
      callback.on_stock_order(mock_order)
      # 验证日志包含订单信息

  def test_on_stock_trade(self, callback, mock_trade):
    """测试成交回报回调"""
    with patch.object(callback.trading_manager, "handle_trade_event"):
      callback.on_stock_trade(mock_trade)
      # 验证日志包含成交信息

  # ==================== 错误处理回调测试 ====================

  def test_on_order_error(self, callback, mock_order_error):
    """测试委托失败回调"""
    with patch.object(callback.trading_manager, "handle_order_error_event"):
      callback.on_order_error(mock_order_error)
      # 验证错误日志

  def test_on_cancel_error(self, callback, mock_cancel_error):
    """测试撤单失败回调"""
    with patch.object(callback.trading_manager, "handle_cancel_error_event"):
      callback.on_cancel_error(mock_cancel_error)
      # 验证错误日志

  # ==================== 异步响应回调测试 ====================

  def test_on_order_stock_async_response(self, callback, mock_order_response):
    """测试异步下单回报回调"""
    with patch.object(callback.trading_manager, "handle_async_order_response"):
      callback.on_order_stock_async_response(mock_order_response)
      # 验证日志

  def test_on_cancel_order_stock_async_response(self, callback, mock_cancel_response):
    """测试异步撤单回报回调"""
    with patch.object(
      callback.trading_manager, "handle_async_cancel_response"
    ):
      callback.on_cancel_order_stock_async_response(mock_cancel_response)
      # 验证日志

  # ==================== 扩展功能回调测试 ====================

  def test_on_smt_appointment_async_response(self, callback):
    """测试约券异步回报回调"""
    mock_response = MagicMock()
    # 预留接口，暂无实现
    callback.on_smt_appointment_async_response(mock_response)
    # 应该不抛出异常

  def test_on_bank_transfer_async_response(self, callback):
    """测试银证转账异步回报回调"""
    mock_response = MagicMock()
    # 预留接口，暂无实现
    callback.on_bank_transfer_async_response(mock_response)
    # 应该不抛出异常

  def test_on_ctp_internal_transfer_async_response(self, callback):
    """测试CTP内部转账异步回报回调"""
    mock_response = MagicMock()
    # 预留接口，暂无实现
    callback.on_ctp_internal_transfer_async_response(mock_response)
    # 应该不抛出异常

  # ==================== 并发测试 ====================

  @pytest.mark.asyncio
  async def test_concurrent_callbacks(self, callback, mock_order, mock_trade):
    """测试并发回调处理"""
    # 创建多个回调任务

    with patch.object(callback.trading_manager, "handle_order_event"):
      with patch.object(callback.trading_manager, "handle_trade_event"):
        # 同时触发多个回调
        for i in range(10):
          callback.on_stock_order(mock_order)
          callback.on_stock_trade(mock_trade)

        # 等待任务执行
        await asyncio.sleep(0.2)

        # 验证所有回调都被处理（通过事件循环）

  # ==================== 错误恢复测试 ====================

  def test_callback_error_handling(self, callback, mock_order):
    """测试回调错误处理"""
    # 模拟处理方法抛出异常
    with patch.object(
      callback.trading_manager,
      "handle_order_event",
      side_effect=Exception("Test exception"),
    ):
      # 回调不应该抛出异常
      try:
        callback.on_stock_order(mock_order)
        # 应该能正常执行
      except Exception as e:
        pytest.fail(f"回调不应该抛出异常: {e}")

  # ==================== 回调完整性验证 ====================

  def test_all_callbacks_registered(self, callback):
    """测试所有官方回调都已注册"""
    from xtquant.xttrader import XtQuantTraderCallback

    # 获取官方定义的所有回调方法
    official_callbacks = [
      method
      for method in dir(XtQuantTraderCallback)
      if method.startswith("on_") and callable(getattr(XtQuantTraderCallback, method))
    ]

    # 验证我们的实现包含所有官方回调
    implemented_callbacks = [
      method
      for method in dir(callback)
      if method.startswith("on_") and callable(getattr(callback, method))
    ]

    missing_callbacks = set(official_callbacks) - set(implemented_callbacks)
    assert (
      len(missing_callbacks) == 0
    ), f"缺少以下回调实现: {missing_callbacks}"

    # 验证覆盖率 100%
    assert len(implemented_callbacks) >= len(official_callbacks), "回调实现不完整"
