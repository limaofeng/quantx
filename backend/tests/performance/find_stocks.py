#!/usr/bin/env python3
"""
查找有数据的股票
"""

import sys
from pathlib import Path

# 添加项目路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from database.timeseries_connection import create_timeseries_connection, init_timeseries, shutdown_timeseries
from database.timeseries_operations import TimeSeriesOperations
from loguru import logger
from collections import defaultdict

def find_stocks_with_data():
    """查找有数据的股票"""
    logger.info("🔍 查找有数据的股票...")

    try:
        # 初始化连接
        init_timeseries()
        connection = create_timeseries_connection()
        ts_ops = TimeSeriesOperations(connection)

        # 查询 kline_1d 表中的股票列表（限制时间范围）
        logger.info("📊 查询 kline_1d 表（最近7天）...")
        sql = """
        SELECT stock_code, COUNT(*) as count
        FROM kline_1d
        WHERE time >= now() - INTERVAL '7 days'
        GROUP BY stock_code
        ORDER BY count DESC
        LIMIT 20
        """
        
        result = ts_ops.query(sql, use_cache=False)

        if result:
            logger.info(f"✅ 找到 {len(result)} 个股票:\n")
            
            print(f"\n{'股票代码':<15} {'数据条数':<10}")
            print("-" * 30)
            
            for row in result:
                stock_code = row.get('stock_code', 'N/A')
                count = row.get('count', 0)
                print(f"{stock_code:<15} {count:<10}")
                
            # 返回前5个股票
            top_stocks = [row.get('stock_code') for row in result[:5]]
            return top_stocks
        else:
            logger.warning("⚠️  没有找到任何股票数据")
            return []

    except Exception as e:
        logger.error(f"❌ 查询失败: {e}")
        import traceback
        traceback.print_exc()
        return []
    finally:
        try:
            shutdown_timeseries()
        except:
            pass

if __name__ == "__main__":
    stocks = find_stocks_with_data()
    if stocks:
        logger.info(f"\n✅ 推荐测试股票: {', '.join(stocks)}")
