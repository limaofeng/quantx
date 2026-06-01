# -*- coding: utf-8 -*-
"""
批量财务数据同步流程

独立的财务数据同步，从数据库获取股票信息后调用接口获取财务数据并保存
"""

import asyncio
import datetime
from typing import Any, Dict, List, Optional

from prefect import flow, get_run_logger
from prefect.client.schemas.schedules import CronSchedule

from prefector.flow_hooks import STANDARD_FLOW_HOOKS
from core.utils import time_utils
from prefector.tasks import (
  generate_batch_sync_report,
  send_sync_notification,
  sync_financial_batch_task,
)
from services.instrument_service import InstrumentService

# 调度配置 - 工作日早上9点（在市场数据同步后）
FINANCIAL_SYNC_SCHEDULE = CronSchedule(cron="0 9 * * 1-5")


@flow(
  name="全市场财务数据同步",
  description="从数据库获取股票信息，批量同步财务数据",
  retries=1,
  retry_delay_seconds=300,
  **STANDARD_FLOW_HOOKS
)
async def batch_financial_sync_flow(
  max_concurrency: int = 5,
  limit: Optional[int] = None,
  stock_codes: Optional[List[str]] = None,
) -> Dict[str, Any]:
  """
  全市场财务数据同步流程
  
  Args:
      max_concurrency: 并发分片数
      limit: 限制处理的股票数量（用于测试）
      stock_codes: 指定股票代码列表；为空时同步全市场股票财务数据
  """
  logger = get_run_logger()
  start_time = time_utils.now()

  logger.info("=" * 60)
  logger.info("开始全市场财务数据同步任务")
  logger.info("=" * 60)

  try:
    # 步骤1: 准备股票代码列表
    if stock_codes is not None:
      logger.info("步骤1: 使用传入的股票代码列表")
      stock_codes = list(dict.fromkeys(code for code in stock_codes if code))
      if limit:
        stock_codes = stock_codes[:limit]
      instruments_count = len(stock_codes)
    else:
      logger.info("步骤1: 从数据库查询股票信息")
      instrument_service = InstrumentService()
      instruments = await instrument_service.find_all(limit=limit if limit else 50000)
      instruments_count = len(instruments)

      # 过滤出股票代码（排除ETF、指数等，只保留 .SZ 和 .SH 结尾的股票）
      instrument_codes = [
        getattr(inst, "code", None) or getattr(inst, "id", None)
        for inst in instruments
      ]

      stock_codes = [
        code for code in instrument_codes
        if code and (code.endswith('.SZ') or code.endswith('.SH'))
        # 排除指数（通常以 000、399、8 开头的代码）
        and not code.startswith('000300')  # 沪深300
        and not code.startswith('399')      # 深证指数
        and not code.startswith('51')       # ETF
        and not code.startswith('56')       # ETF
        and not code.startswith('58')       # ETF
        and not code.startswith('159')      # 深市ETF
      ]

    total_stocks = len(stock_codes)
    logger.info(f"共获取到 {instruments_count} 只标的，过滤后 {total_stocks} 只股票需同步财务数据")

    if total_stocks == 0:
      logger.warning("未获取到任何股票代码")
      return {
        "status": "skipped",
        "reason": "未获取到股票代码",
        "start_time": start_time,
        "end_time": time_utils.now(),
      }

    # 步骤2: 分片并发执行
    CHUNK_SIZE = 50
    chunks = [stock_codes[i:i + CHUNK_SIZE] for i in range(0, len(stock_codes), CHUNK_SIZE)]
    logger.info(f"将 {total_stocks} 只股票分为 {len(chunks)} 个批次，每批 {CHUNK_SIZE}，并发数 {max_concurrency}")
    
    semaphore = asyncio.Semaphore(max_concurrency)

    async def process_chunk(chunk_codes):
      async with semaphore:
        return await sync_financial_batch_task(chunk_codes)

    tasks = [asyncio.create_task(process_chunk(chunk)) for chunk in chunks]
    
    logger.info("任务创建完成，等待执行...")
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 步骤3: 统计结果
    logger.info("步骤3: 统计结果")
    
    success_count = 0
    failed_count = 0
    error_count = 0
    total_saved = 0
    
    failed_batches = []
    
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            error_count += 1
            failed_batches.append(f"Batch {i}: {str(res)}")
            continue
            
        if res.get("status") == "failed":
            error_count += 1
            failed_batches.append(f"Batch {i}: {res.get('error')}")
            
        success_count += res.get("success", 0)
        failed_count += res.get("failed", 0)
        total_saved += res.get("saved_count", 0)

    # 计算整体状态
    if failed_count == 0 and error_count == 0:
      overall_status = "success"
    else:
      overall_status = "partial"

    success_rate = (success_count / total_stocks * 100) if total_stocks > 0 else 0
    
    end_time = time_utils.now()
    total_elapsed = (end_time - start_time).total_seconds()
    avg_duration = (total_elapsed / total_stocks) if total_stocks > 0 else 0

    # 步骤4: 生成报告
    report = await generate_batch_sync_report(
      task_name="全市场财务数据同步",
      report_type="batch_financial_sync",
      start_time=start_time,
      end_time=end_time,
      total_elapsed_seconds=total_elapsed,
      total_stocks=total_stocks,
      success_count=success_count,
      failed_count=failed_count,
      skipped_count=0,
      error_count=error_count,
      success_rate=success_rate,
      avg_duration_per_stock=avg_duration,
      total_records_saved=total_saved,
      status=overall_status,
      success_stocks=[],
      failed_stocks=[],
      skipped_stocks=[],
      error_stocks=failed_batches,
      max_concurrency=max_concurrency,
    )
    
    if failed_count > 0 or error_count > 0:
      await send_sync_notification(
         notification_type="partial_failure" if success_count > 0 else "complete_failure",
         report=report
      )

    logger.info("=" * 60)
    logger.info(f"财务数据同步完成: {overall_status}")
    logger.info(f"总数: {total_stocks}, 成功: {success_count} ({success_rate:.1f}%)")
    logger.info(f"保存记录数: {total_saved}")
    logger.info(f"总耗时: {total_elapsed:.1f}s, Batch Errors: {error_count}")
    logger.info("=" * 60)

    return report

  except Exception as e:
    logger.error(f"财务数据同步流程失败: {e}")
    raise
