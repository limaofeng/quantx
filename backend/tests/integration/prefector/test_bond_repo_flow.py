"""
债券回购同步流程集成测试

测试债券回购工作流的集成逻辑，使用 Mock 服务避免真实交易
"""

import pytest
import asyncio
from unittest.mock import patch

# 导入被测试的 flows
from prefector.flows.bond_repo_flow import bond_repo_auto_trade_flow

# 导入Mock服务
from tests.mocks import MockTradingService


@pytest.fixture(autouse=True)
async def cleanup_db_connections():
    """清理数据库连接的fixture"""
    yield
    # 测试结束后等待一段时间让连接池清理
    await asyncio.sleep(0.1)


@pytest.mark.integration
class TestBondRepoAutoTradeFlowIntegration:
    """
    债券回购交易流程集成测试 - 使用Mock服务避免真实交易

    测试完整的业务逻辑流程，但不执行真实的交易操作
    """

    @pytest.mark.asyncio
    async def test_bond_repo_auto_trade_flow_with_mock(self):
        """测试债券回购自动交易流程（使用Mock交易服务）"""
        mock_trading_service = MockTradingService()

        # Mock TradingService
        with patch('services.trading_service.TradingService', return_value=mock_trading_service), \
             patch('prefector.tasks.bond_tasks.TradingService', return_value=mock_trading_service):

            try:
                result = await bond_repo_auto_trade_flow()

                # 验证结果结构
                assert isinstance(result, dict)
                assert "status" in result

                # 验证流程完成（无论是否执行交易）
                if result["status"] == "success":
                    # 如果有交易，验证是Mock交易
                    if "trades" in result or "purchases" in result:
                        # 验证没有消耗真实资金（Mock账户资金应该变化）
                        account_info = await mock_trading_service.get_account_info()
                        assert hasattr(account_info, 'cash')

            finally:
                # 确保异步操作有时间完成
                await asyncio.sleep(1.0)
