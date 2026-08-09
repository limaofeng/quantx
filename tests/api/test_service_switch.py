#!/usr/bin/env python3
"""
复权服务切换测试脚本
"""

import asyncio
from datetime import datetime, timedelta

from loguru import logger
from quantx_infrastructure.services.service_config import (
  USE_ASYNC_HISTORICAL_SERVICE,
  USE_POSTGRESQL_DIVID_FACTOR,
  get_divid_factor_service,
  get_historical_market_data_service,
)


async def test_divid_factor_service():
  """测试复权因子服务"""

  logger.info("="*80)
  logger.info("🧪 测试复权因子服务")
  logger.info("="*80)

  # 获取服务实例
  divid_service = get_divid_factor_service()

  logger.info("\n📊 当前配置:")
  logger.info(f"  USE_POSTGRESQL_DIVID_FACTOR: {USE_POSTGRESQL_DIVID_FACTOR}")
  logger.info(f"  服务类型: {type(divid_service).__name__}")

  # 测试查询
  stock_code = "601985.SH"
  end_time = datetime.now()
  start_time = end_time - timedelta(days=30)

  logger.info("\n🔍 查询复权因子:")
  logger.info(f"  股票: {stock_code}")
  logger.info(f"  时间: {start_time.date()} ~ {end_time.date()}")

  try:
    if USE_POSTGRESQL_DIVID_FACTOR:
      # PostgreSQL 版本（异步）
      factors = await divid_service.get_divid_factors(
        stock_code=stock_code,
        start_time=start_time,
        end_time=end_time,
      )

      logger.info(f"\n✅ 查询成功: {len(factors)} 条")

      if factors:
        logger.info("\n前3条数据:")
        for i, factor in enumerate(factors[:3]):
          logger.info(f"  {i+1}. {factor.time} | dr={factor.dr} | ex_date={factor.ex_date}")

    else:
      # InfluxDB 版本（同步）
      factors = divid_service.get_divid_factors(
        stock_code=stock_code,
        start_time=start_time,
        end_time=end_time,
      )

      logger.info(f"\n✅ 查询成功: {len(factors)} 条")

  except Exception as e:
    logger.error(f"❌ 查询失败: {e}")
    import traceback
    traceback.print_exc()

  logger.info(f"\n{'='*80}\n")


async def test_historical_market_service():
  """测试历史市场数据服务"""

  logger.info("="*80)
  logger.info("🧪 测试历史市场数据服务")
  logger.info("="*80)

  # 获取服务实例
  market_service = get_historical_market_data_service()

  logger.info("\n📊 当前配置:")
  logger.info(f"  USE_ASYNC_HISTORICAL_SERVICE: {USE_ASYNC_HISTORICAL_SERVICE}")
  logger.info(f"  服务类型: {type(market_service).__name__}")

  # 测试查询复权K线
  stock_code = "601985.SH"
  period = "1d"
  end_time = datetime.now()
  start_time = end_time - timedelta(days=7)

  logger.info("\n🔍 查询复权K线:")
  logger.info(f"  股票: {stock_code}")
  logger.info(f"  周期: {period}")
  logger.info(f"  时间: {start_time.date()} ~ {end_time.date()}")

  try:
    if USE_ASYNC_HISTORICAL_SERVICE:
      # 异步版本
      klines = await market_service.get_adjusted_klines(
        stock_code=stock_code,
        period=period,
        start_time=start_time,
        end_time=end_time,
        dividend_type="front"
      )

      logger.info(f"\n✅ 查询成功: {len(klines)} 条")

      if klines:
        logger.info("\n前3条数据:")
        for i, kline in enumerate(klines[:3]):
          logger.info(f"  {i+1}. {kline.time} | close={kline.close:.2f}")

    else:
      logger.warning("\n⚠️  同步版本暂未实现测试")

  except Exception as e:
    logger.error(f"❌ 查询失败: {e}")
    import traceback
    traceback.print_exc()

  logger.info(f"\n{'='*80}\n")


async def main():
  """主测试函数"""

  logger.info("\n" + "="*80)
  logger.info("🚀 复权服务切换测试")
  logger.info("="*80)

  # 测试1: 复权因子服务
  await test_divid_factor_service()

  # 测试2: 历史市场数据服务
  await test_historical_market_service()

  # 总结
  logger.info("="*80)
  logger.info("📋 测试总结")
  logger.info("="*80)

  logger.info("\n✅ 配置状态:")
  logger.info(f"  复权因子服务: {'PostgreSQL (异步)' if USE_POSTGRESQL_DIVID_FACTOR else 'InfluxDB (同步)'}")
  logger.info(f"  历史数据服务: {'异步版本' if USE_ASYNC_HISTORICAL_SERVICE else '同步版本'}")

  logger.info("\n🎯 切换建议:")
  logger.info("  1. 如果测试通过，可以将 USE_POSTGRESQL_DIVID_FACTOR 设为 True")
  logger.info("  2. 同时将 USE_ASYNC_HISTORICAL_SERVICE 设为 True")
  logger.info("  3. 逐步迁移其他使用旧服务的代码")

  logger.info("\n📝 配置文件: services/service_config.py")
  logger.info(f"{'='*80}\n")


if __name__ == "__main__":
  asyncio.run(main())
