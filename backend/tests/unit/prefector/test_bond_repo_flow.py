"""
债券回购流程单元测试

使用 mock 对象测试债券回购流程的单个组件功能
"""

import pytest
import asyncio
from unittest.mock import patch

# 导入被测试的 flows
from prefector.flows.bond_repo_flow import bond_repo_auto_trade_flow


@pytest.fixture(autouse=True)
async def cleanup_db_connections():
    """清理数据库连接的fixture"""
    yield
    # 测试结束后等待一段时间让连接池清理
    await asyncio.sleep(0.1)


@pytest.mark.unit
class TestBondRepoAutoTradeFlow:
    """债券回购自动交易流程单元测试"""

    @pytest.mark.asyncio
    async def test_bond_repo_auto_trade_flow_success(self):
        """测试债券回购自动交易流程成功执行"""
        with patch('prefector.flows.bond_repo_flow.fetch_bond_repo_rates') as mock_fetch_bond, \
             patch('prefector.flows.bond_repo_flow.analyze_bond_repo_opportunities') as mock_analyze, \
             patch('prefector.flows.bond_repo_flow.generate_trade_report') as mock_generate_report:

            mock_data = [{"code": "GC001", "rate": 2.35}]

            # 设置异步 mock 返回值
            async def mock_fetch_bond_async():
                return mock_data

            async def mock_analyze_async(data):
                return {"opportunities": []}

            async def mock_generate_report_async(**kwargs):
                return {"status": "success"}

            mock_fetch_bond.side_effect = mock_fetch_bond_async
            mock_analyze.side_effect = mock_analyze_async
            mock_generate_report.side_effect = mock_generate_report_async

            result = await bond_repo_auto_trade_flow()

            assert result["status"] == "success"
            mock_fetch_bond.assert_called_once()
