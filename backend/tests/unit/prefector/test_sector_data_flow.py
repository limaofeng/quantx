"""
行业数据同步流程单元测试

使用 mock 对象测试行业数据同步流程的单个组件功能
"""

import pytest
from unittest.mock import patch

# 导入被测试的 flows
from prefector.flows.sector_data_flow import sector_data_sync_flow


@pytest.mark.unit
class TestSectorDataSyncFlow:
    """行业数据同步流程单元测试"""

    @pytest.mark.asyncio
    async def test_sector_data_sync_flow_success(self):
        """测试行业数据同步流程成功执行"""
        with patch('prefector.flows.sector_data_flow.fetch_sector_list') as mock_fetch_sectors, \
             patch('prefector.flows.sector_data_flow.fetch_sector_stocks') as mock_fetch_stocks, \
             patch('prefector.flows.sector_data_flow.save_sector_data') as mock_save_data, \
             patch('prefector.flows.sector_data_flow.generate_sync_report') as mock_generate_report:

            # Mock 数据
            mock_sectors = [
                {"sector_code": "BK0001", "sector_name": "银行"},
                {"sector_code": "BK0002", "sector_name": "地产"}
            ]
            mock_sector_stocks = {
                "BK0001": ["600000.SH", "600036.SH"],
                "BK0002": ["000002.SZ", "600048.SH"]
            }

            # 设置 mock 返回值
            mock_fetch_sectors.return_value = mock_sectors
            mock_fetch_stocks.return_value = mock_sector_stocks
            mock_save_data.return_value = {"saved_sectors": 2, "saved_stocks": 4}

            async def mock_generate_report_async(**kwargs):
                return {
                    "status": "success",
                    "total_sectors": 2,
                    "success_count": 2,
                    "total_stocks": 4
                }

            mock_generate_report.side_effect = mock_generate_report_async

            # 执行测试
            result = await sector_data_sync_flow()

            # 验证结果
            assert result["status"] == "success"
            assert result["total_sectors"] == 2
            assert result["success_count"] == 2

            # 验证调用
            mock_fetch_sectors.assert_called_once()
            mock_fetch_stocks.assert_called_once()
            mock_save_data.assert_called_once()

    @pytest.mark.asyncio
    async def test_sector_data_sync_flow_empty_sectors(self):
        """测试没有行业数据的情况"""
        with patch('prefector.flows.sector_data_flow.fetch_sector_list') as mock_fetch_sectors, \
             patch('prefector.flows.sector_data_flow.generate_sync_report') as mock_generate_report:

            # Mock 空数据
            mock_fetch_sectors.return_value = []

            async def mock_generate_empty_report(**kwargs):
                return {
                    "status": "skipped",
                    "reason": "没有获取到行业数据",
                    "total_sectors": 0
                }

            mock_generate_report.side_effect = mock_generate_empty_report

            # 执行测试
            result = await sector_data_sync_flow()

            # 验证结果
            assert result["status"] == "skipped"
            assert result["total_sectors"] == 0
            mock_fetch_sectors.assert_called_once()
