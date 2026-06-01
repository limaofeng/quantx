"""
全市场基础数据同步流程单元测试

使用 mock 对象测试 market_sync_flow 的单个组件功能
"""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime

# 导入被测试的 flows
from prefector.flows.market_sync_flow import market_sync_flow


@pytest.mark.unit
class TestMarketSyncFlow:
    """全市场基础数据同步流程单元测试"""

    @pytest.mark.asyncio
    async def test_market_sync_flow_success(self):
        """测试全市场数据同步流程成功执行"""
        with patch('prefector.flows.market_sync_flow.fetch_instrument_codes') as mock_fetch_codes, \
             patch('prefector.flows.market_sync_flow.sync_instruments_batch_task') as mock_sync_task, \
             patch('prefector.flows.market_sync_flow.generate_batch_sync_report') as mock_generate_report, \
             patch('prefector.flows.market_sync_flow.InstrumentService') as mock_service_cls:

            # 1. 设置 Mock 返回值
            mock_fetch_codes.return_value = ["000001.SZ", "600000.SH"]
            
            # 模拟同步任务返回值
            async def mock_sync_task_side_effect(codes):
                return {
                    "status": "success",
                    "success": len(codes),
                    "failed": 0,
                    "saved_count": len(codes)
                }
            mock_sync_task.side_effect = mock_sync_task_side_effect

            # 模拟报告生成
            async def mock_generate_report_side_effect(**kwargs):
                return {
                    "status": "success",
                    "total_found": 2,
                    "success_count": 2,
                    "failed_count": 0,
                    "error_count": 0
                }
            mock_generate_report.side_effect = mock_generate_report_side_effect

            # 2. 执行流程
            result = await market_sync_flow(max_concurrency=2)

            # 3. 验证结果
            assert result["status"] == "success"
            assert result["success_count"] == 2
            
            # 验证调用
            mock_fetch_codes.assert_called_once()
            assert mock_sync_task.call_count >= 1
            mock_generate_report.assert_called_once()

    @pytest.mark.asyncio
    async def test_market_sync_flow_empty_codes(self):
        """测试无标的代码时的情况"""
        with patch('prefector.flows.market_sync_flow.fetch_instrument_codes') as mock_fetch_codes:
            mock_fetch_codes.return_value = []
            
            result = await market_sync_flow()
            
            assert result["status"] == "skipped"
            assert "代码列表为空" in result["reason"]

    @pytest.mark.asyncio
    async def test_market_sync_flow_partial_failure(self):
        """测试部分分片同步失败的情况"""
        with patch('prefector.flows.market_sync_flow.fetch_instrument_codes') as mock_fetch_codes, \
             patch('prefector.flows.market_sync_flow.sync_instruments_batch_task') as mock_sync_task, \
             patch('prefector.flows.market_sync_flow.generate_batch_sync_report') as mock_generate_report, \
             patch('prefector.flows.market_sync_flow.send_sync_notification') as mock_notify:

            mock_fetch_codes.return_value = ["000001.SZ", "600000.SH"]
            
            # 一个成功，一个失败
            mock_sync_task.side_effect = [
                {"status": "success", "success": 1, "failed": 0, "saved_count": 1},
                {"status": "failed", "error": "Mock Error"}
            ]

            async def mock_generate_report_side_effect(**kwargs):
                return {"status": "partial", "success_count": 1, "failed_count": 0, "error_count": 1}
            mock_generate_report.side_effect = mock_generate_report_side_effect

            # 执行流程
            result = await market_sync_flow(max_concurrency=1) # 强制按顺序执行以便 verify call_count

            assert result["status"] == "partial"
            mock_notify.assert_called_once()
