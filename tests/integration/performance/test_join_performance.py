#!/usr/bin/env python3
"""
复权计算性能评估脚本
对比应用层复权 vs 数据库 JOIN 的性能差异

运行方式:
    conda activate quantx
    python tests/performance/test_join_performance.py
"""

import asyncio
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from loguru import logger

# 避免循环导入，直接导入需要的模块
from quantx_infrastructure.database.timeseries_connection import (
    create_timeseries_connection,
    init_timeseries,
    shutdown_timeseries,
)
from quantx_infrastructure.database.timeseries_operations import TimeSeriesOperations

project_root = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.dangerous


class PerformanceEvaluator:
    """性能评估器"""

    def __init__(self):
        self.ts_ops = None
        self.connection = None

    async def initialize(self):
        """初始化数据库连接"""
        try:
            init_timeseries()
            self.connection = create_timeseries_connection()
            if self.connection:
                self.ts_ops = TimeSeriesOperations(self.connection)
                logger.info("数据库连接初始化成功")
            else:
                logger.error("无法创建数据库连接")
                raise Exception("数据库连接失败")
        except Exception as e:
            logger.error(f"数据库初始化失败: {e}")
            raise

    async def cleanup(self):
        """清理资源"""
        try:
            shutdown_timeseries()
            logger.info("数据库连接已关闭")
        except Exception as e:
            logger.error(f"清理资源失败: {e}")

    def _format_result(self, name: str, duration: float, record_count: int,
                       data_size_mb: float, method: str) -> dict:
        """格式化测试结果"""
        return {
            "方法": method,
            "测试名称": name,
            "耗时(秒)": round(duration, 4),
            "记录数": record_count,
            "数据量(MB)": round(data_size_mb, 2),
            "每秒处理记录数": round(record_count / duration, 2) if duration > 0 else 0,
            "每条记录耗时(毫秒)": round(duration * 1000 / record_count, 4) if record_count > 0 else 0,
        }

    async def test_application_layer_adjustment(
        self, stock_code: str, period: str, days: int = 30
    ) -> dict:
        """测试应用层复权（简化模拟）"""
        test_name = f"应用层复权 - {stock_code} - {period} - {days}天"

        logger.info(f"🧪 开始测试: {test_name}")

        end_time = datetime.now()
        start_time = end_time - timedelta(days=days)

        # 确定表名
        table_name = f"kline_{period}" if period in ['1d', '1m'] else 'kline_1d'

        # 计时开始
        start_timer = time.time()

        try:
            # 1. 查询K线数据
            sql_klines = f"""
            SELECT *
            FROM {table_name}
            WHERE stock_code = '{stock_code}'
                AND time >= '{start_time.isoformat()}'
                AND time <= '{end_time.isoformat()}'
            ORDER BY time ASC
            """

            klines_result = self.ts_ops.query(sql_klines, use_cache=False)

            if not klines_result:
                logger.warning(f"没有找到K线数据: {stock_code}")
                return self._format_result(test_name, 0, 0, 0, "应用层")

            logger.info(f"  K线数据: {len(klines_result)} 条")

            # 2. 查询复权因子
            sql_factors = f"""
            SELECT *
            FROM divid_factors
            WHERE stock_code = '{stock_code}'
                AND time >= '{start_time.isoformat()}'
                AND time <= '{end_time.isoformat()}'
            ORDER BY time ASC
            """

            factors_result = self.ts_ops.query(sql_factors, use_cache=False)

            logger.info(f"  复权因子: {len(factors_result)} 条")

            # 3. 应用层合并（模拟）
            klines_df = pd.DataFrame(klines_result)
            factors_df = pd.DataFrame(factors_result)

            if not factors_df.empty:
                factors_df = factors_df.sort_values('time')
                klines_df = klines_df.sort_values('time')

                # 模拟 merge_asof 操作
                aligned = pd.merge_asof(
                    klines_df[['time']],
                    factors_df[['time', 'dr']],
                    on='time',
                    direction='backward'
                )

                # 计算复权后价格（简化）
                klines_df['adj_close'] = klines_df['close'] / aligned['dr'].fillna(1.0)

            # 计时结束
            duration = time.time() - start_timer

            # 计算数据大小
            data_size = sys.getsizeof(klines_result) + sys.getsizeof(factors_result)
            data_size_mb = data_size / (1024 * 1024)

            logger.info(f"  ✅ 复权完成: {len(klines_result)} 条数据")
            logger.info(f"  ⏱️  耗时: {duration:.4f} 秒")
            logger.info(f"  📊 数据量: {data_size_mb:.2f} MB")

            return self._format_result(
                test_name, duration, len(klines_result), data_size_mb, "应用层"
            )

        except Exception as e:
            logger.error(f"❌ 测试失败: {e}")
            import traceback
            traceback.print_exc()
            return self._format_result(test_name, 0, 0, 0, "应用层")

    async def test_database_join(
        self, stock_code: str, period: str, days: int = 30
    ) -> dict:
        """测试数据库 JOIN 复权"""
        test_name = f"数据库JOIN - {stock_code} - {period} - {days}天"

        logger.info(f"🧪 开始测试: {test_name}")

        end_time = datetime.now()
        start_time = end_time - timedelta(days=days)

        # 确定表名
        table_name = f"kline_{period}" if period in ['1d', '1m'] else 'kline_1d'

        # 计时开始
        start_timer = time.time()

        try:
            # 构建JOIN查询SQL
            sql = f"""
            WITH dividend_factors AS (
                SELECT
                    time,
                    stock_code,
                    dr
                FROM divid_factors
                WHERE stock_code = '{stock_code}'
                    AND time >= '{start_time.isoformat()}'
                    AND time <= '{end_time.isoformat()}'
            ),
            cumulative_factors AS (
                SELECT
                    time,
                    stock_code,
                    dr
                FROM dividend_factors
                WHERE dr > 0
            )
            SELECT
                k.time,
                k.stock_code,
                k.open,
                k.high,
                k.low,
                k.close,
                k.volume,
                k.amount,
                cf.dr as adjust_factor
            FROM {table_name} k
            LEFT JOIN LATERAL (
                SELECT dr
                FROM cumulative_factors cf
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

            # 执行查询
            result = self.ts_ops.query(sql, use_cache=False)

            # 计时结束
            duration = time.time() - start_timer

            if not result:
                logger.warning(f"查询结果为空: {stock_code}")
                return self._format_result(test_name, duration, 0, 0, "数据库JOIN")

            record_count = len(result)
            data_size = sys.getsizeof(result)
            data_size_mb = data_size / (1024 * 1024)

            logger.info(f"  ✅ 查询完成: {record_count} 条数据")
            logger.info(f"  ⏱️  耗时: {duration:.4f} 秒")
            logger.info(f"  📊 数据量: {data_size_mb:.2f} MB")

            return self._format_result(
                test_name, duration, record_count, data_size_mb, "数据库JOIN"
            )

        except Exception as e:
            logger.error(f"❌ 测试失败: {e}")
            import traceback
            traceback.print_exc()
            return self._format_result(test_name, 0, 0, 0, "数据库JOIN")

    async def run_comparison_tests(
        self, stock_code: str, period: str, test_cases: list[int]
    ) -> pd.DataFrame:
        """运行对比测试"""
        results = []

        for days in test_cases:
            logger.info(f"\n{'='*60}")
            logger.info(f"📊 测试股票: {stock_code} | 周期: {period} | 天数: {days}")
            logger.info(f"{'='*60}\n")

            # 测试应用层复权
            result_app = await self.test_application_layer_adjustment(
                stock_code, period, days
            )
            results.append(result_app)

            # 等待一下，避免连续查询
            await asyncio.sleep(1)

            # 测试数据库JOIN
            result_db = await self.test_database_join(stock_code, period, days)
            results.append(result_db)

            # 等待一下
            await asyncio.sleep(1)

        return pd.DataFrame(results)

    def print_summary(self, results_df: pd.DataFrame):
        """打印测试总结"""
        logger.info(f"\n{'='*80}")
        logger.info("📊 性能测试总结报告")
        logger.info(f"{'='*80}\n")

        # 按测试分组
        grouped = results_df.groupby("测试名称")

        for test_name, group in grouped:
            logger.info(f"\n🔍 {test_name}")
            logger.info(f"{'-'*60}")

            for _, row in group.iterrows():
                logger.info(f"\n  方法: {row['方法']}")
                logger.info(f"    - 耗时: {row['耗时(秒)']} 秒")
                logger.info(f"    - 记录数: {row['记录数']} 条")
                logger.info(f"    - 数据量: {row['数据量(MB)']} MB")
                logger.info(f"    - 吞吐量: {row['每秒处理记录数']} 条/秒")
                logger.info(f"    - 单条耗时: {row['每条记录耗时(毫秒)']} 毫秒/条")

            # 对比性能
            if len(group) == 2:
                app_row = group[group['方法'] == '应用层'].iloc[0]
                db_row = group[group['方法'] == '数据库JOIN'].iloc[0]

                if app_row['耗时(秒)'] > 0 and db_row['耗时(秒)'] > 0:
                    speedup = app_row['耗时(秒)'] / db_row['耗时(秒)']
                    logger.info(f"\n  ⚡ 性能提升: {speedup:.2f}x")

                    if speedup > 1:
                        logger.info(f"     ✅ 数据库JOIN比应用层快 {speedup:.2f} 倍")
                    else:
                        logger.info(f"     ⚠️  应用层比数据库JOIN快 {1/speedup:.2f} 倍")

        # 整体统计
        logger.info(f"\n{'='*80}")
        logger.info("📈 整体性能对比")
        logger.info(f"{'='*80}\n")

        app_avg = results_df[results_df['方法'] == '应用层']['耗时(秒)'].mean()
        db_avg = results_df[results_df['方法'] == '数据库JOIN']['耗时(秒)'].mean()

        logger.info(f"应用层平均耗时: {app_avg:.4f} 秒")
        logger.info(f"数据库JOIN平均耗时: {db_avg:.4f} 秒")

        if app_avg > 0 and db_avg > 0:
            overall_speedup = app_avg / db_avg
            logger.info(f"\n总体性能提升: {overall_speedup:.2f}x")

            if overall_speedup > 1.2:
                logger.info("✅ 建议: 数据库JOIN方案明显更快，建议采用")
            elif overall_speedup < 0.8:
                logger.info("⚠️  建议: 应用层方案更快，保持当前实现")
            else:
                logger.info("ℹ️  建议: 两种方案性能接近，可根据其他因素选择")

        logger.info(f"\n{'='*80}\n")


async def main():
    """主函数"""
    logger.info("🚀 复权计算性能评估测试")
    logger.info("="*80)

    evaluator = PerformanceEvaluator()

    try:
        # 初始化
        await evaluator.initialize()

        # 测试用例
        test_cases = {
            "短期": ("000001.SZ", "1d", [30, 60, 90]),      # 1-3个月
            "中期": ("000001.SZ", "1d", [180, 365, 730]),   # 半年、1年、2年
        }

        all_results = []

        for category, (stock_code, period, days_list) in test_cases.items():
            logger.info(f"\n{'#'*80}")
            logger.info(f"# {category}测试")
            logger.info(f"{'#'*80}")

            results = await evaluator.run_comparison_tests(
                stock_code=stock_code,
                period=period,
                test_cases=days_list
            )

            all_results.append(results)

        # 合并所有结果
        final_results = pd.concat(all_results, ignore_index=True)

        # 打印总结
        evaluator.print_summary(final_results)

        # 保存结果
        output_file = project_root / "logs" / f"join_performance_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        output_file.parent.mkdir(exist_ok=True)
        final_results.to_csv(output_file, index=False, encoding='utf-8-sig')
        logger.info(f"📄 详细结果已保存到: {output_file}")

        logger.info("\n✅ 所有测试完成！")

    except Exception as e:
        logger.error(f"❌ 测试过程中发生错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await evaluator.cleanup()


if __name__ == "__main__":
    # 运行测试
    asyncio.run(main())
