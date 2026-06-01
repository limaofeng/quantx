"""
债券回购真实交易E2E测试

⚠️ 危险测试 - 会执行真实的交易操作！
仅在专门配置的测试环境中运行，需要：
1. 设置环境变量 ENABLE_REAL_TRADING=true
2. 确保测试账户有足够资金
3. 确认测试环境不是生产环境
"""

import os
import pytest
import asyncio

# 导入被测试的 flows
from prefector.flows.bond_repo_flow import bond_repo_auto_trade_flow


@pytest.fixture(autouse=True)
def check_environment():
    """检查是否允许真实交易测试"""
    if os.getenv("ENABLE_REAL_TRADING") != "true":
        pytest.skip("需要设置 ENABLE_REAL_TRADING=true 才能运行真实交易测试")

    if os.getenv("ENV") == "production":
        pytest.skip("禁止在生产环境运行真实交易测试")


@pytest.fixture(autouse=True)
async def cleanup_db_connections():
    """清理数据库连接的fixture"""
    yield
    # 测试结束后等待一段时间让连接池清理
    await asyncio.sleep(0.1)


@pytest.mark.e2e
@pytest.mark.dangerous
class TestBondRepoRealTrading:
    """
    债券回购真实交易E2E测试

    ⚠️ 警告：此测试会执行真实的交易操作
    - 会消耗真实资金
    - 会向交易系统下单
    - 仅在专门的测试环境中运行
    """

    @pytest.mark.asyncio
    async def test_bond_repo_auto_trade_flow_real_execution(self):
        """测试债券回购自动交易流程的真实执行

        ⚠️ 危险操作：会执行真实的交易下单
        """
        # 记录测试开始
        print("\n" + "="*60)
        print("⚠️  开始执行真实交易测试")
        print("此测试将消耗真实资金并向交易系统下单")
        print("="*60)

        try:
            result = await bond_repo_auto_trade_flow()

            # 验证结果结构
            assert isinstance(result, dict)
            assert "status" in result

            # 根据结果状态验证
            if result["status"] == "success":
                assert "trades" in result or "purchases" in result
                print(f"✅ 交易成功完成: {result}")
            elif result["status"] == "skipped":
                print(f"⏭️  交易跳过: {result.get('message', '未知原因')}")
            else:
                print(f"❌ 交易失败: {result}")

        except Exception as e:
            print(f"❌ 测试执行异常: {str(e)}")
            raise
        finally:
            # 确保异步操作有时间完成
            await asyncio.sleep(1.0)
            print("测试完成，请检查交易账户状态")
