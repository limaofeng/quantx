#!/usr/bin/env python3
"""
快速性能基准测试
对比应用层复权 vs 数据库 JOIN
"""

import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# 添加项目路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import pandas as pd
from database.timeseries_connection import create_timeseries_connection, init_timeseries, shutdown_timeseries
from database.timeseries_operations import TimeSeriesOperations
from loguru import logger

def quick_benchmark():
    """快速基准测试"""
    logger.info("="*60)
    logger.info("🚀 复权计算性能快速基准测试")
    logger.info("="*60)

    # 测试参数
    stock_code = "000001.SZ"
    period = "1d"
    days = 30  # 测试30天数据
    table_name = f"kline_{period}"

    end_time = datetime.now()
    start_time = end_time - timedelta(days=days)

    logger.info(f"\n📊 测试配置:")
    logger.info(f"  股票: {stock_code}")
    logger.info(f"  周期: {period}")
    logger.info(f"  时间: {days}天 ({start_time.date()} ~ {end_time.date()})")

    try:
        # 初始化连接
        init_timeseries()
        connection = create_timeseries_connection()
        ts_ops = TimeSeriesOperations(connection)

        # ========== 方法1: 应用层复权 ==========
        logger.info(f"\n{'='*60}")
        logger.info("🔍 方法1: 应用层复权（当前实现）")
        logger.info(f"{'='*60}")

        start = time.time()

        # 1. 查询K线
        sql_klines = f"""
        SELECT * FROM {table_name}
        WHERE stock_code = '{stock_code}'
            AND time >= '{start_time.isoformat()}'
            AND time <= '{end_time.isoformat()}'
        ORDER BY time ASC
        """
        klines = ts_ops.query(sql_klines, use_cache=False)
        logger.info(f"  K线数据: {len(klines)} 条")

        # 2. 查询复权因子
        sql_factors = f"""
        SELECT * FROM divid_factors
        WHERE stock_code = '{stock_code}'
            AND time >= '{start_time.isoformat()}'
            AND time <= '{end_time.isoformat()}'
        ORDER BY time ASC
        """
        factors = ts_ops.query(sql_factors, use_cache=False)
        logger.info(f"  复权因子: {len(factors)} 条")

        # 3. 应用层合并
        if klines and factors:
            klines_df = pd.DataFrame(klines)
            factors_df = pd.DataFrame(factors)

            klines_df = klines_df.sort_values('time')
            factors_df = factors_df.sort_values('time')

            aligned = pd.merge_asof(
                klines_df[['time']],
                factors_df[['time', 'dr']],
                on='time',
                direction='backward'
            )

            klines_df['adj_close'] = klines_df['close'] / aligned['dr'].fillna(1.0)

        duration_app = time.time() - start

        data_size = (sys.getsizeof(klines) + sys.getsizeof(factors)) / (1024*1024)

        logger.info(f"  ⏱️  耗时: {duration_app:.4f} 秒")
        logger.info(f"  📊 数据量: {data_size:.2f} MB")
        logger.info(f"  ⚡ 吞吐量: {len(klines)/duration_app:.2f} 条/秒")

        # 等待一下
        time.sleep(1)

        # ========== 方法2: 数据库 JOIN ==========
        logger.info(f"\n{'='*60}")
        logger.info("🔍 方法2: 数据库 JOIN（优化方案）")
        logger.info(f"{'='*60}")

        start = time.time()

        sql_join = f"""
        WITH dividend_factors AS (
            SELECT time, stock_code, dr
            FROM divid_factors
            WHERE stock_code = '{stock_code}'
                AND time >= '{start_time.isoformat()}'
                AND time <= '{end_time.isoformat()}'
        )
        SELECT
            k.time,
            k.stock_code,
            k.close,
            cf.dr as adjust_factor
        FROM {table_name} k
        LEFT JOIN LATERAL (
            SELECT dr
            FROM dividend_factors cf
            WHERE cf.stock_code = k.stock_code
                AND cf.time <= k.time
            ORDER BY cf.time DESC
            LIMIT 1
        ) cf ON true
        WHERE k.stock_code = '{stock_code}'
            AND k.time >= '{start_time.isoformat()}'
            AND k.time <= '{end_time.isoformat()}'
        ORDER BY k.time ASC
        """

        result = ts_ops.query(sql_join, use_cache=False)

        duration_db = time.time() - start

        data_size_db = sys.getsizeof(result) / (1024*1024)

        logger.info(f"  查询结果: {len(result)} 条")
        logger.info(f"  ⏱️  耗时: {duration_db:.4f} 秒")
        logger.info(f"  📊 数据量: {data_size_db:.2f} MB")
        logger.info(f"  ⚡ 吞吐量: {len(result)/duration_db:.2f} 条/秒")

        # ========== 对比总结 ==========
        logger.info(f"\n{'='*60}")
        logger.info("📊 性能对比总结")
        logger.info(f"{'='*60}\n")

        logger.info(f"{'指标':<20} {'应用层':<15} {'数据库JOIN':<15} {'差异':<15}")
        logger.info(f"{'-'*60}")

        logger.info(f"{'耗时(秒)':<20} {duration_app:<15.4f} {duration_db:<15.4f} {(duration_app/duration_db):<15.2f}x")
        logger.info(f"{'数据量(MB)':<20} {data_size:<15.2f} {data_size_db:<15.2f} {(data_size/data_size_db):<15.2f}x")
        logger.info(f"{'吞吐量(条/秒)':<20} {len(klines)/duration_app:<15.2f} {len(result)/duration_db:<15.2f} {(duration_db/duration_app):<15.2f}x")

        # 性能建议
        logger.info(f"\n{'='*60}")
        logger.info("💡 性能建议")
        logger.info(f"{'='*60}\n")

        speedup = duration_app / duration_db

        if speedup > 1.5:
            logger.info(f"✅ 数据库JOIN 快 {speedup:.2f}倍")
            logger.info(f"   建议: 采用数据库JOIN方案")
        elif speedup < 0.8:
            logger.info(f"⚠️  应用层快 {1/speedup:.2f}倍")
            logger.info(f"   建议: 保持应用层实现")
        else:
            logger.info(f"ℹ️  性能接近（{speedup:.2f}x）")
            logger.info(f"   建议: 根据代码可维护性选择")

        logger.info(f"\n{'='*60}\n")

        return {
            'app_duration': duration_app,
            'db_duration': duration_db,
            'speedup': speedup,
            'klines_count': len(klines),
            'factors_count': len(factors)
        }

    except Exception as e:
        logger.error(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return None
    finally:
        try:
            shutdown_timeseries()
        except:
            pass

if __name__ == "__main__":
    result = quick_benchmark()
    if result:
        logger.info("✅ 测试完成！")
    else:
        logger.error("❌ 测试失败")
