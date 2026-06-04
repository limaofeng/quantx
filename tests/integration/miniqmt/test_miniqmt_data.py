"""
测试 miniqmt 数据获取功能
"""
import logging
import pytest
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
TEST_LOG_DIR = PROJECT_ROOT / ".quantx-dev" / "logs" / "tests" / "integration" / "miniqmt"
TEST_LOG_DIR.mkdir(parents=True, exist_ok=True)
TEST_LOG_FILE = TEST_LOG_DIR / "test_miniqmt_data.log"

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(TEST_LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


def test_get_current_tick():
    """测试获取当前的 tick 数据"""
    try:
        # 导入 xtquant 数据模块
        from xtquant import xtdata

        logger.info("=" * 60)
        logger.info("开始测试获取当前 tick 数据")
        logger.info(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # 获取股票代码列表（以一些常见股票为例）
        stock_codes = ['000001.SZ', '600000.SH', '000002.SZ']
        logger.info(f"测试股票代码: {stock_codes}")

        # 获取全市场 tick 数据
        logger.info("\n正在获取全市场 tick 数据...")
        tick_data = xtdata.get_full_tick(stock_codes)

        if tick_data:
            logger.info(f"成功获取 tick 数据，股票数量: {len(tick_data)}")
            logger.info("\n" + "=" * 60)
            logger.info("Tick 数据详情:")
            logger.info("=" * 60)

            # 打印每只股票的 tick 数据
            for stock_code in stock_codes:
                if stock_code in tick_data:
                    stock_tick = tick_data[stock_code]
                    logger.info(f"\n股票代码: {stock_code}")
                    logger.info("-" * 60)

                    # 打印所有字段
                    for field, value in stock_tick.items():
                        if field != 'stockCode':  # 跳过重复的 stockCode 字段
                            logger.info(f"  {field}: {value}")

                    # 打印关键字段
                    logger.info(f"\n  最新价: {stock_tick.get('lastPrice', 'N/A')}")
                    logger.info(f"  买一价: {stock_tick.get('bidPrice1', 'N/A')}")
                    logger.info(f"  卖一价: {stock_tick.get('askPrice1', 'N/A')}")
                    logger.info(f"  成交量: {stock_tick.get('volume', 'N/A')}")
                    logger.info(f"  成交额: {stock_tick.get('amount', 'N/A')}")
                    logger.info(f"  涨跌额: {stock_tick.get('diff', 'N/A')}")
                    logger.info(f"  涨跌幅: {stock_tick.get('diffPercent', 'N/A')}%")
                else:
                    logger.warning(f"未获取到股票 {stock_code} 的数据")

            logger.info("\n" + "=" * 60)
            logger.info("Tick 数据获取测试完成")
            logger.info("=" * 60)

            # 断言：验证获取到了数据
            assert tick_data is not None, "Tick 数据不应为空"
            assert len(tick_data) > 0, "应该至少获取到一只股票的数据"

        else:
            logger.error("未能获取到 tick 数据")
            pytest.fail("获取 tick 数据失败")

    except ImportError as e:
        logger.error(f"导入 xtquant 模块失败: {e}")
        logger.error("请确保已安装 xtquant 库并且在正确的环境中运行")
        pytest.skip("xtquant 模块未安装")

    except Exception as e:
        logger.error(f"测试过程中发生错误: {e}", exc_info=True)
        pytest.fail(f"测试失败: {e}")


def test_get_market_data():
    """测试获取市场行情数据"""
    try:
        from xtquant import xtdata

        logger.info("\n" + "=" * 60)
        logger.info("开始测试获取市场行情数据")
        logger.info("=" * 60)

        # 获取市场股票列表
        logger.info("\n正在获取深圳 A 股列表...")
        sz_stocks = xtdata.get_stock_list_in_sector('沪深A股')
        logger.info(f"沪深 A 股数量: {len(sz_stocks)}")

        # 获取前 10 只股票的行情
        if sz_stocks:
            test_stocks = sz_stocks[:10]
            logger.info(f"获取前 {len(test_stocks)} 只股票的行情...")

            quote_data = xtdata.get_full_tick(test_stocks)

            logger.info("\n市场行情摘要:")
            logger.info("-" * 60)
            for stock in test_stocks[:3]:  # 只打印前 3 只
                if stock in quote_data:
                    data = quote_data[stock]
                    logger.info(f"{stock}: 价格={data.get('lastPrice', 'N/A')}, "
                              f"涨跌={data.get('diffPercent', 'N/A')}%")

        assert sz_stocks is not None and len(sz_stocks) > 0, "应该获取到股票列表"

    except ImportError as e:
        logger.error(f"导入 xtquant 模块失败: {e}")
        pytest.skip("xtquant 模块未安装")

    except Exception as e:
        logger.error(f"获取市场数据时发生错误: {e}", exc_info=True)
        pytest.fail(f"测试失败: {e}")


if __name__ == "__main__":
    # 直接运行测试
    logger.info("开始执行 tick 数据测试...")
    test_get_current_tick()

    logger.info("\n开始执行市场数据测试...")
    test_get_market_data()

    logger.info("\n所有测试执行完成！")
