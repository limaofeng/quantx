"""
实时价格同步流程测试

测试 realtime_price_sync_flow 的实时价格更新功能
"""

import pytest
from unittest.mock import patch
from prefect.client.schemas.schedules import CronSchedule

# 导入被测试的 flows
from prefector.flows.realtime_price_flow import realtime_price_sync_flow, REALTIME_SYNC_SCHEDULE


@pytest.mark.integration
class TestRealtimePriceSyncFlow:
    """实时价格同步流程测试"""

    @pytest.mark.asyncio
    async def test_realtime_price_sync_flow_with_stock_codes(self):
        """测试实时价格同步流程（指定股票代码）"""
        stock_codes = ["000001", "600036"]

        with patch('prefector.flows.realtime_price_flow.fetch_stock_list') as mock_fetch_stocks, \
             patch('prefector.flows.realtime_price_flow.fetch_stock_prices') as mock_fetch_prices, \
             patch('prefector.flows.realtime_price_flow.update_price_cache') as mock_update_cache, \
             patch('prefector.flows.realtime_price_flow.generate_task_report') as mock_generate_report:

            mock_prices = {
                "000001": {"price": 12.50, "change": 0.15},
                "600036": {"price": 35.80, "change": -0.20}
            }

            # 设置异步 mock 返回值
            async def mock_fetch_prices_async(stock_codes):
                return mock_prices

            async def mock_update_cache_async():
                return True

            async def mock_generate_report_async(**kwargs):
                return {
                    "task_name": "实时股票价格同步",
                    "status": "success",
                    "updated_count": 2
                }

            mock_fetch_prices.side_effect = mock_fetch_prices_async
            mock_update_cache.side_effect = mock_update_cache_async
            mock_generate_report.side_effect = mock_generate_report_async

            # 执行流程
            result = await realtime_price_sync_flow(stock_codes)

            # 验证结果
            assert result["status"] == "success"
            assert result["updated_count"] == 2

            # 验证使用指定的股票代码
            mock_fetch_stocks.assert_not_called()
            mock_fetch_prices.assert_called_once_with(stock_codes)

    @pytest.mark.asyncio
    async def test_realtime_price_sync_flow_without_stock_codes(self):
        """测试实时价格同步流程（自动获取股票列表）"""
        with patch('prefector.flows.realtime_price_flow.fetch_stock_list') as mock_fetch_stocks, \
             patch('prefector.flows.realtime_price_flow.fetch_stock_prices') as mock_fetch_prices, \
             patch('prefector.flows.realtime_price_flow.update_price_cache') as mock_update_cache, \
             patch('prefector.flows.realtime_price_flow.generate_task_report') as mock_generate_report:

            mock_stocks = [
                {"code": "000001", "name": "平安银行"},
                {"code": "600036", "name": "招商银行"}
            ]
            mock_prices = {
                "000001": {"price": 12.50},
                "600036": {"price": 35.80}
            }

            # 设置异步 mock 返回值
            async def mock_fetch_stocks_async():
                return mock_stocks

            async def mock_fetch_prices_async(stock_codes):
                return mock_prices

            async def mock_update_cache_async():
                return True

            async def mock_generate_report_async(**kwargs):
                return {
                    "task_name": "实时股票价格同步",
                    "status": "success"
                }

            mock_fetch_stocks.side_effect = mock_fetch_stocks_async
            mock_fetch_prices.side_effect = mock_fetch_prices_async
            mock_update_cache.side_effect = mock_update_cache_async
            mock_generate_report.side_effect = mock_generate_report_async

            # 执行流程（不指定股票代码）
            result = await realtime_price_sync_flow()

            # 验证自动获取股票列表
            mock_fetch_stocks.assert_called_once()
            mock_fetch_prices.assert_called_once_with(["000001", "600036"])

    def test_realtime_sync_schedule_configuration(self):
        """测试实时同步调度配置"""
        assert isinstance(REALTIME_SYNC_SCHEDULE, CronSchedule)
        assert REALTIME_SYNC_SCHEDULE.cron == "*/5 * * * 1-5"  # 工作日每5分钟
