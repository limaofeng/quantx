# -*- coding: utf-8 -*-
"""
实时股票价格同步流程

用于盘中实时价格更新
"""

import datetime
from typing import Any, Dict, List, Optional

from prefect import flow, get_run_logger
from prefect.client.schemas.schedules import CronSchedule

from prefector.flow_hooks import STANDARD_FLOW_HOOKS
from core.utils import time_utils
from prefector.tasks import (
  fetch_stock_list,
  fetch_stock_prices,
  generate_task_report,
  update_price_cache,
)

# 调度配置 - 工作日每5分钟
REALTIME_SYNC_SCHEDULE = CronSchedule(cron="*/5 * * * 1-5")


@flow(
  name="实时股票价格同步",
  description="实时获取股票价格数据（用于盘中更新）",
  retries=2,
  **STANDARD_FLOW_HOOKS
)
async def realtime_price_sync_flow(
  stock_codes: Optional[List[str]] = None,
) -> Dict[str, Any]:
  """
  实时股票价格同步流程

  Args:
      stock_codes: 指定股票代码列表，为空则获取所有

  Returns:
      同步结果
  """
  logger = get_run_logger()
  start_time = time_utils.now()

  logger.info("=" * 50)
  logger.info("开始实时股票价格同步任务")
  logger.info("=" * 50)

  try:
    # 如果没有指定股票代码，获取全部
    if not stock_codes:
      logger.info("未指定股票代码，获取全部股票列表")
      stocks = await fetch_stock_list()
      stock_codes = [stock["code"] for stock in stocks]
    else:
      logger.info(f"指定股票代码: {stock_codes}")

    # 获取实时价格
    logger.info("获取实时价格数据")
    prices = await fetch_stock_prices(stock_codes)

    # 更新缓存
    logger.info("更新价格缓存")
    cache_updated = await update_price_cache(prices)

    # 生成报告
    report = await generate_task_report(
      task_name="实时股票价格同步",
      start_time=start_time,
      status="success",
      stock_codes=stock_codes,
      prices_count=len(prices),
      cache_updated=cache_updated,
    )

    logger.info(f"成功获取 {len(prices)} 只股票的实时价格")
    logger.info("实时股票价格同步任务完成")

    return report

  except Exception as e:
    logger.error(f"实时股票价格同步失败: {e}")
    raise
