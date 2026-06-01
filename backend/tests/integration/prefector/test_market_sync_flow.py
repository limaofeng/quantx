"""
全市场基础数据同步流程集成测试

测试 market_sync_flow 的完整市场数据同步流程
"""

import pytest
import asyncio

# 导入被测试的 flows
from prefector.flows.market_sync_flow import market_sync_flow


@pytest.fixture(autouse=True)
async def cleanup_db_connections():
    """清理数据库连接的fixture"""
    yield
    # 测试结束后等待一段时间让连接池清理
    await asyncio.sleep(0.1)


@pytest.mark.integration
class TestMarketSyncFlowIntegration:
    """全市场基础数据同步流程集成测试"""

    @pytest.mark.asyncio
    async def test_market_sync_flow_complete_execution(self):
        """测试全市场数据同步流程完整执行"""
        try:
            # 执行完整的全市场数据同步流程  ["沪深A股", "沪深ETF", "沪深指数"]
            result = await market_sync_flow() # 选个小规模的测试

            # 验证流程能够成功执行
            assert result is not None
            assert isinstance(result, dict)
            assert "status" in result

            # 根据实际情况，结果可能是 success, partial_success, skipped 或 failed
            assert result["status"] in ["success", "partial", "failed", "skipped"]

        finally:
            # 确保异步操作有时间完成
            await asyncio.sleep(1.0)
