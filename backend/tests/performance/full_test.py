#!/usr/bin/env python3
"""
完整性能测试 - 使用有数据的股票
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

def run_full_test():
    """运行完整性能测试"""
    logger.info("="*80)
    logger.info("🚀 复权计算完整性能测试")
    logger.info("="*80)

    # 使用找到的有数据的股票
    stock_code = "601985.SH"
    period = "1d"
    table_name = f"kline_{period}"

    # 测试不同时间范围
    test_cases = [
        (7, "1周"),
        (14, "2周"),
        (30, "1个月"),
    ]

    results = []

    try:
        # 初始化连接
        init_timeseries()
        connection = create_timeseries_connection()
        ts_ops = TimeSeriesOperations(connection)

        for days, label in test_cases:
            logger.info(f"\n{'='*80}")
            logger.info(f"📊 测试: {stock_code} - {label} ({days}天)")
            logger.info(f"{'='*80}\n")

            end_time = datetime.now()
            start_time = end_time - timedelta(days=days)

            # ========== 方法1: 应用层复权 ==========
            logger.info("🔍 方法1: 应用层复权")
            logger.info("-" * 60)

            start_timer = time.time()

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
            if klines:
                klines_df = pd.DataFrame(klines)
                factors_df = pd.DataFrame(factors) if factors else pd.DataFrame()

                if not factors_df.empty:
                    factors_df = factors_df.sort_values('time')
                    klines_df = klines_df.sort_values('time')

                    aligned = pd.merge_asof(
                        klines_df[['time']],
                        factors_df[['time', 'dr']],
                        on='time',
                        direction='backward'
                    )

                    klines_df['adj_close'] = klines_df['close'] / aligned['dr'].fillna(1.0)

            duration_app = time.time() - start_timer
            data_size = (sys.getsizeof(klines) + sys.getsizeof(factors)) / (1024*1024)

            logger.info(f"  ⏱️  耗时: {duration_app:.4f} 秒")
            logger.info(f"  📊 数据量: {data_size:.4f} MB")
            logger.info(f"  ⚡ 吞吐量: {len(klines)/duration_app:.2f} 条/秒" if klines else "  ⚡ 吞吐量: N/A")

            results.append({
                '股票': stock_code,
                '时间范围': label,
                '天数': days,
                'K线数': len(klines),
                '因子数': len(factors),
                '应用层耗时': duration_app,
                '应用层数据量_MB': data_size,
                '数据库JOIN耗时': None,
                '性能提升': None
            })

            # 等待一下
            time.sleep(0.5)

        # ========== 打印总结 ==========
        logger.info(f"\n{'='*80}")
        logger.info("📊 性能测试总结")
        logger.info(f"{'='*80}\n")

        logger.info(f"{'股票':<15} {'时间范围':<10} {'K线数':<8} {'因子数':<8} {'耗时(秒)':<12} {'数据量(MB)':<15}")
        logger.info("-" * 80)

        for r in results:
            logger.info(f"{r['股票']:<15} {r['时间范围']:<10} {r['K线数']:<8} {r['因子数']:<8} {r['应用层耗时']:<12.4f} {r['应用层数据量_MB']:<15.4f}")

        logger.info(f"\n{'='*80}")
        logger.info("💡 结论")
        logger.info(f"{'='*80}\n")

        logger.info("✅ 应用层复权测试完成")
        logger.info("❌ 数据库JOIN方案不可行（InfluxDB 3.x不支持LATERAL JOIN）")
        logger.info("")
        logger.info("📊 性能分析:")

        if results:
            avg_duration = sum(r['应用层耗时'] for r in results) / len(results)
            avg_klines = sum(r['K线数'] for r in results) / len(results)

            logger.info(f"  - 平均耗时: {avg_duration:.4f} 秒")
            logger.info(f"  - 平均数据量: {avg_klines:.0f} 条")
            logger.info(f"  - 平均吞吐量: {avg_klines/avg_duration:.2f} 条/秒")

        logger.info("")
        logger.info("🎯 最终建议:")
        logger.info("  ✅ 保持当前应用层复权实现")
        logger.info("  💡 可添加 Redis 缓存优化")
        logger.info("  💡 可预计算常用复权数据")

        logger.info(f"\n{'='*80}\n")

        return results

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
    result = run_full_test()
    if result:
        logger.info("✅ 测试完成！")
    else:
        logger.error("❌ 测试失败")
