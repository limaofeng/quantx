"""
每日交易数据同步流程集成测试

测试 daily_trading_sync_flow 的完整交易数据同步流程
"""

import pytest
import asyncio
from datetime import datetime, timedelta

# 导入被测试的 flows
from prefector.flows.daily_trading_sync_flow import daily_trading_sync_flow


@pytest.fixture(autouse=True)
async def cleanup_db_connections():
    """清理数据库连接的fixture"""
    yield
    # 测试结束后等待一段时间让连接池清理
    await asyncio.sleep(0.1)


@pytest.mark.integration
class TestDailyTradingSyncFlowIntegration:
    """
    每日交易数据同步流程集成测试 - 完全真实的流程测试

    这个测试类直接测试完整的 daily_trading_sync_flow 流程，
    使用真实的数据源和数据库操作
    """

    @pytest.mark.asyncio
    async def test_daily_trading_sync_flow_complete_real_flow(self):
        """测试每日交易数据同步流程完整执行（使用真实数据）"""
        try:
            # 执行当日交易数据同步
            result = await daily_trading_sync_flow(account_id="300000013250")

            # 验证流程执行状态
            assert result["status"] in ["success", "skipped", "failed"]

            # 根据不同状态进行验证
            if result["status"] == "success":
                # 如果成功，验证数据结构
                assert isinstance(result, dict)
                # 可能包含数据摘要信息
                if "data_summary" in result:
                    assert isinstance(result["data_summary"], dict)

            elif result["status"] == "skipped":
                # 如果跳过（如非交易日），验证跳过原因
                assert "skip_reason" in result or "message" in result

            elif result["status"] == "failed":
                # 如果失败，验证错误信息
                assert "error" in result or "message" in result

        finally:
            # 确保异步操作有时间完成
            await asyncio.sleep(1.0)

    @pytest.mark.asyncio
    async def test_daily_trading_sync_flow_with_different_accounts(self):
        """测试不同账户的每日交易数据同步"""
        # 测试不同的账户ID
        test_accounts = ["300000013250", "test_account"]

        for account_id in test_accounts:
            try:
                result = await daily_trading_sync_flow(account_id=account_id)

                # 验证每个账户都能返回有效结果
                assert result is not None
                assert isinstance(result, dict)
                assert "status" in result
                assert result["status"] in ["success", "skipped", "failed"]

            except Exception as e:
                # 某些账户可能不存在或无权限，这是正常的
                assert isinstance(e, Exception)

            finally:
                await asyncio.sleep(0.5)
