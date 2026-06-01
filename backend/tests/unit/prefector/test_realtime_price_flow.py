"""
实时价格同步流程单元测试

使用 mock 对象测试实时价格同步流程的单个组件功能
"""

import pytest
from unittest.mock import patch

# 导入被测试的 flows
from prefector.flows.realtime_price_flow import realtime_price_sync_flow


@pytest.mark.unit
class TestRealtimePriceSyncFlow:
    """实时价格同步流程单元测试"""

    @pytest.mark.asyncio
    async def test_realtime_price_sync_flow_success(self):
        """测试实时价格同步流程成功执行"""
        with patch('prefector.flows.realtime_price_flow.fetch_stock_list') as mock_fetch_list, \
             patch('prefector.flows.realtime_price_flow.fetch_stock_prices') as mock_fetch_prices, \
             patch('prefector.flows.realtime_price_flow.save_stock_data') as mock_save_data, \
             patch('prefector.flows.realtime_price_flow.update_price_cache') as mock_update_cache, \
             patch('prefector.flows.realtime_price_flow.generate_sync_report') as mock_generate_report:

            # Mock 数据
            mock_stock_list = ["600000.SH", "000001.SZ"]
            mock_price_data = [
                {"stock_code": "600000.SH", "price": 10.50, "volume": 1000},
                {"stock_code": "000001.SZ", "price": 15.20, "volume": 2000}
            ]

            # 设置 mock 返回值
            mock_fetch_list.return_value = mock_stock_list
            mock_fetch_prices.return_value = mock_price_data
            mock_save_data.return_value = {"saved_count": 2}
            mock_update_cache.return_value = {"updated_count": 2}

            async def mock_generate_report_async(**kwargs):
                return {
                    "status": "success",
                    "total_stocks": 2,
                    "success_count": 2,
                    "records_saved": 2
                }

            mock_generate_report.side_effect = mock_generate_report_async

            # 执行测试
            result = await realtime_price_sync_flow()

            # 验证结果
            assert result["status"] == "success"
            assert result["total_stocks"] == 2
            assert result["success_count"] == 2

            # 验证调用
            mock_fetch_list.assert_called_once()
            mock_fetch_prices.assert_called_once_with(mock_stock_list)
            mock_save_data.assert_called_once()
            mock_update_cache.assert_called_once()

    @pytest.mark.asyncio
    async def test_realtime_price_sync_flow_fetch_error(self):
        """测试获取价格数据失败的处理"""
        with patch('prefector.flows.realtime_price_flow.fetch_stock_list') as mock_fetch_list, \
             patch('prefector.flows.realtime_price_flow.fetch_stock_prices') as mock_fetch_prices, \
             patch('prefector.flows.realtime_price_flow.generate_sync_report') as mock_generate_report:

            # 设置 mock
            mock_stock_list = ["600000.SH"]
            mock_fetch_list.return_value = mock_stock_list
            mock_fetch_prices.side_effect = Exception("网络连接失败")

            async def mock_generate_error_report(**kwargs):
                return {
                    "status": "failed",
                    "error": "网络连接失败",
                    "total_stocks": 1,
                    "success_count": 0
                }

            mock_generate_report.side_effect = mock_generate_error_report

            # 执行测试
            result = await realtime_price_sync_flow()

            # 验证错误处理
            assert result["status"] == "failed"
            assert "error" in result
            mock_fetch_prices.assert_called_once()
