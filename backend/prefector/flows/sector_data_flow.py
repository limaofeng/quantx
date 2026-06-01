"""
行业板块数据同步流程

同步行业板块涨跌幅数据
"""

import datetime
from typing import Any, Dict

from prefect import flow, get_run_logger

from prefector.tasks import fetch_sector_data, save_sector_data, generate_sync_report
from prefector.flow_hooks import STANDARD_FLOW_HOOKS
from core.utils import time_utils


@flow(
    name="行业板块数据同步", 
    description="同步行业板块数据及其层级结构", 
    retries=1,
    **STANDARD_FLOW_HOOKS
)
async def sector_data_sync_flow() -> Dict[str, Any]:
    """
    行业板块数据同步流程

    包含：板块数据获取 -> 建立层级关系 -> 数据入库 -> 报告生成

    Returns:
        同步结果报告
    """
    logger = get_run_logger()
    start_time = time_utils.now()

    logger.info("=" * 50)
    logger.info("开始行业板块数据同步流水线")
    logger.info("=" * 50)

    try:
        # 步骤1: 获取并解析板块数据（含层级推导）
        logger.info("步骤1: 获取行业板块列表及层级结构")
        sector_data_list = await fetch_sector_data()

        # 步骤2: 执行数据入库
        logger.info(f"步骤2: 保存 {len(sector_data_list)} 个板块到数据库")
        saved_count = await save_sector_data(sector_data_list)

        # 步骤3: 生成同步报告
        logger.info("步骤3: 生成同步任务总结报告")
        report = await generate_sync_report(
            task_name="行业板块数据层级同步",
            start_time=start_time,
            fetched_count=len(sector_data_list),
            saved_count=saved_count,
            status="success",
        )

        logger.info(f"行业板块同步完成：获取 {len(sector_data_list)} 个，成功入库 {saved_count} 个")
        return report

    except Exception as e:
        logger.error(f"行业板块数据同步在执行过程中失败: {str(e)}", exc_info=True)
        raise

if __name__ == "__main__":
    import asyncio
    asyncio.run(sector_data_sync_flow())
