"""
每日交易数据同步流程单元测试

使用 mock 对象测试每日交易数据同步流程的单个组件功能
"""

import asyncio
import importlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 导入被测试的 flows
backend_root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(backend_root))
loaded_prefector = sys.modules.get("prefector")
loaded_prefector_path = str(getattr(loaded_prefector, "__file__", ""))
if loaded_prefector and (
    "\\tests\\unit\\prefector\\" in loaded_prefector_path
    or "/tests/unit/prefector/" in loaded_prefector_path
):
    sys.modules.pop("prefector", None)
flow_module = importlib.import_module("prefector.flows.daily_trading_sync_flow")
daily_trading_sync_flow = flow_module.daily_trading_sync_flow


@pytest.fixture(autouse=True)
async def cleanup_db_connections():
    """清理数据库连接的fixture"""
    yield
    # 测试结束后等待一段时间让连接池清理
    await asyncio.sleep(0.1)


@pytest.mark.unit
class TestDailyTradingSyncFlow:
    """每日交易数据同步流程单元测试"""

    @pytest.mark.asyncio
    async def test_daily_trading_sync_flow_success(self):
        """测试每日交易数据同步流程成功执行"""
        # Mock数据
        mock_orders = [
            {"order_id": 1, "stock_code": "600000.SH", "side": "BUY", "quantity": 100}
        ]
        mock_trades = [
            {"trade_id": "T001", "stock_code": "600000.SH", "side": "BUY", "quantity": 100}
        ]
        mock_positions = [
            {"stock_code": "600000.SH", "volume": 100, "account_id": "300000013250"}
        ]

        with patch.object(flow_module, 'get_run_logger', return_value=MagicMock()), \
             patch.object(flow_module, 'check_trading_day', new_callable=AsyncMock) as mock_check_trading_day, \
             patch.object(flow_module, 'fetch_daily_orders', new_callable=AsyncMock) as mock_fetch_orders, \
             patch.object(flow_module, 'fetch_daily_trades', new_callable=AsyncMock) as mock_fetch_trades, \
             patch.object(flow_module, 'fetch_latest_positions', new_callable=AsyncMock) as mock_fetch_positions, \
             patch.object(flow_module, 'save_orders_data', new_callable=AsyncMock) as mock_save_orders, \
             patch.object(flow_module, 'save_trades_data', new_callable=AsyncMock) as mock_save_trades, \
             patch.object(flow_module, 'update_positions_data', new_callable=AsyncMock) as mock_update_positions, \
             patch.object(flow_module, 'create_daily_asset_snapshots', new_callable=AsyncMock) as mock_create_snapshots, \
             patch.object(flow_module, 'generate_trading_sync_report', new_callable=AsyncMock) as mock_generate_report:

            # 配置mock返回值
            mock_check_trading_day.return_value = True
            mock_fetch_orders.return_value = mock_orders
            mock_fetch_trades.return_value = mock_trades
            mock_fetch_positions.return_value = mock_positions
            mock_save_orders.return_value = {"saved_count": 1}
            mock_save_trades.return_value = {"saved_count": 1}
            mock_update_positions.return_value = {"updated_count": 1}
            mock_create_snapshots.return_value = {
                "account_snapshot_id": "snapshot-1",
                "strategy_snapshot_count": 0,
            }
            mock_generate_report.return_value = {"status": "success", "total_count": 3}

            # 执行测试
            result = await daily_trading_sync_flow.fn(account_id="300000013250")

            # 验证调用
            mock_fetch_orders.assert_awaited_once_with("300000013250")
            mock_fetch_trades.assert_awaited_once_with("300000013250")
            mock_fetch_positions.assert_awaited_once_with("300000013250")
            mock_create_snapshots.assert_awaited_once()
            snapshot_kwargs = mock_create_snapshots.call_args.kwargs
            assert snapshot_kwargs["account_id"] == "300000013250"
            assert snapshot_kwargs["positions_data"] == mock_positions
            assert isinstance(snapshot_kwargs["trade_date"], str)
            assert result is not None

    @pytest.mark.asyncio
    async def test_daily_trading_sync_flow_failure(self):
        """测试每日交易数据同步流程失败处理"""

        with patch.object(flow_module, 'get_run_logger', return_value=MagicMock()), \
             patch.object(flow_module, 'check_trading_day', new_callable=AsyncMock) as mock_check_trading_day, \
             patch.object(flow_module, 'fetch_daily_orders', new_callable=AsyncMock) as mock_fetch_orders, \
             patch.object(flow_module, 'generate_trading_sync_report', new_callable=AsyncMock) as mock_generate_report:

            # 设置为交易日
            mock_check_trading_day.return_value = True

            # 模拟获取数据失败
            mock_fetch_orders.side_effect = Exception("获取委托数据失败")
            mock_generate_report.return_value = {"status": "failed", "error": "获取委托数据失败"}

            # 执行测试
            result = await daily_trading_sync_flow.fn(account_id="300000013250")

            # 验证失败处理
            mock_generate_report.assert_awaited_once()
            assert result["status"] == "failed"

    @pytest.mark.asyncio
    async def test_daily_trading_sync_flow_non_trading_day(self):
        """测试非交易日的跳过逻辑"""

        with patch.object(flow_module, 'get_run_logger', return_value=MagicMock()), \
             patch.object(flow_module, 'check_trading_day', new_callable=AsyncMock) as mock_check_trading_day, \
             patch.object(flow_module, 'generate_trading_sync_report', new_callable=AsyncMock) as mock_report:

            # 设置为非交易日
            mock_check_trading_day.return_value = False
            mock_report.return_value = {
                "status": "skipped",
                "skip_reason": "非交易日",
                "orders_count": 0
            }

            # 执行测试
            result = await daily_trading_sync_flow.fn(account_id="300000013250")

            # 验证只调用了交易日检查
            mock_check_trading_day.assert_awaited_once()
            mock_report.assert_awaited_once()
            assert result["status"] == "skipped"
