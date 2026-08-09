#!/usr/bin/env python3
"""
快速数据库连接测试
"""

import sys
from datetime import datetime, timedelta

import pytest
from loguru import logger
from quantx_infrastructure.database.timeseries_connection import (
    create_timeseries_connection,
    init_timeseries,
    shutdown_timeseries,
)
from quantx_infrastructure.database.timeseries_operations import TimeSeriesOperations

pytestmark = pytest.mark.dangerous


def test_database_connection():
    """测试数据库连接"""
    logger.info("🔍 测试 InfluxDB 连接...")

    try:
        # 初始化连接
        init_timeseries()
        connection = create_timeseries_connection()

        if not connection:
            logger.error("❌ 无法创建数据库连接")
            return False

        logger.info("✅ 数据库连接成功")

        # 创建操作对象
        ts_ops = TimeSeriesOperations(connection)

        # 测试查询：检查 K线表
        logger.info("📊 检查 klines 表...")
        sql_klines = "SELECT COUNT(*) as count FROM klines LIMIT 1"
        result = ts_ops.query(sql_klines, use_cache=False)

        if result:
            logger.info("✅ klines 表存在，记录数示例查询成功")
        else:
            logger.warning("⚠️  klines 表查询返回空结果")

        # 测试查询：检查复权因子表
        logger.info("📊 检查 divid_factors 表...")
        sql_factors = "SELECT COUNT(*) as count FROM divid_factors LIMIT 1"
        result = ts_ops.query(sql_factors, use_cache=False)

        if result:
            logger.info("✅ divid_factors 表存在，记录数示例查询成功")
        else:
            logger.warning("⚠️  divid_factors 表查询返回空结果")

        # 测试实际数据查询
        logger.info("🔍 查询 000001.SZ 的最近数据...")

        end_time = datetime.now()
        start_time = end_time - timedelta(days=7)

        sql_test = f"""
        SELECT *
        FROM klines
        WHERE stock_code = '000001.SZ'
            AND period = '1d'
            AND time >= '{start_time.isoformat()}'
            AND time <= '{end_time.isoformat()}'
        ORDER BY time DESC
        LIMIT 5
        """

        result = ts_ops.query(sql_test, use_cache=False)

        if result:
            logger.info(f"✅ 查询到 {len(result)} 条 K线数据")
            for row in result[:2]:  # 显示前2条
                logger.info(f"   - {row.get('time')}: close={row.get('close')}")
        else:
            logger.warning("⚠️  没有查询到 000001.SZ 的数据")

        logger.info("\n✅ 数据库连接测试完成！")
        return True

    except Exception as e:
        logger.error(f"❌ 数据库测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        try:
            shutdown_timeseries()
        except Exception:
            pass

if __name__ == "__main__":
    success = test_database_connection()
    sys.exit(0 if success else 1)
