"""
持仓同步流程集成测试

测试 position_sync_flow 的完整持仓数据同步流程
"""

import pytest
import asyncio

# 导入被测试的 flows
from prefector.flows.position_sync_flow import position_sync_flow


@pytest.fixture(autouse=True)
async def cleanup_db_connections():
    """清理数据库连接的fixture"""
    yield
    # 测试结束后等待一段时间让连接池清理
    await asyncio.sleep(0.1)


@pytest.mark.integration
class TestPositionSyncFlowIntegration:
    """持仓同步流程集成测试"""

    @pytest.mark.asyncio
    async def test_position_sync_flow_complete_execution(self):
        """测试持仓同步流程完整执行（包含行情同步）"""
        try:
            # 执行完整的持仓同步流程，使用默认账户
            result = await position_sync_flow(
                sync_market_data=True,
                days_back=180,
                periods=["tick", "1m", "1d"],
            )

            # 验证流程能够成功执行
            assert result is not None
            assert isinstance(result, dict)
            assert "status" in result

            # 状态应该是 success（无论是否有持仓）
            assert result["status"] == "success"
            
            # 验证返回结构
            if "message" in result and result["message"] == "No positions found":
                # 无持仓的情况
                assert result.get("saved_result") is None
            else:
                # 有持仓的情况
                assert "account_id" in result
                assert "position_report" in result

        finally:
            # 确保异步操作有时间完成
            await asyncio.sleep(1.0)


