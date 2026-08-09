#!/usr/bin/env python3
"""
检查 InfluxDB 中有哪些表
"""

import pytest
from loguru import logger
from quantx_infrastructure.database.timeseries_connection import (
    create_timeseries_connection,
    init_timeseries,
    shutdown_timeseries,
)
from quantx_infrastructure.database.timeseries_operations import TimeSeriesOperations

pytestmark = pytest.mark.dangerous


def check_tables():
    """检查数据库中的表"""
    logger.info("🔍 检查 InfluxDB 中的表...")

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

        # 查询所有表
        logger.info("📊 查询所有表...")
        sql = "SHOW TABLES"
        result = ts_ops.query(sql, use_cache=False)

        if result:
            logger.info(f"✅ 找到 {len(result)} 个表:")
            for row in result:
                logger.info(f"   - {row}")
        else:
            logger.warning("⚠️  没有找到任何表")

        logger.info("\n✅ 检查完成！")
        return True

    except Exception as e:
        logger.error(f"❌ 检查失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        try:
            shutdown_timeseries()
        except Exception:
            pass

if __name__ == "__main__":
    check_tables()
