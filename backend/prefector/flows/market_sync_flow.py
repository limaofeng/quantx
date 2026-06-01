# -*- coding: utf-8 -*-
"""
全市场基础数据同步流程

负责获取股票、ETF和指数的基础信息并入库
"""

import asyncio
import datetime
from typing import Any, Dict, List, Optional

from prefect import flow, get_run_logger
from prefect.client.schemas.schedules import CronSchedule

from prefector.flow_hooks import STANDARD_FLOW_HOOKS
from core.utils import time_utils
from database.connection import redis_client
from prefector.tasks import (
  fetch_divid_factors_batch,
  fetch_instrument_codes,
  generate_batch_sync_report,
  save_divid_factors_batch,
  send_sync_notification,
  sync_instruments_batch_task,
)
from services.instrument_service import InstrumentService

# 调度配置 - 每个交易日 08:00
MARKET_SYNC_SCHEDULE = CronSchedule(cron="0 8 * * 1-5")

# Redis 记录除权因子上次同步时间
DIVID_FACTOR_SYNC_REDIS_KEY = "divid_factor:last_sync_date"


@flow(
  name="全市场基础数据同步",
  description="获取全市场（股票/ETF/指数）代码，并发执行基础信息同步",
  retries=1,
  retry_delay_seconds=300,
  **STANDARD_FLOW_HOOKS
)
async def market_sync_flow(
  max_concurrency: int = 10, # 并发分片数
  skip_existing: bool = False,
  sectors: Optional[List[str]] = None,
  sync_divid_factors: bool = True,
  divid_factor_start_time: Optional[str] = None,
  divid_factor_end_time: Optional[str] = None,
  divid_factor_days_back: int = 3650,
) -> Dict[str, Any]:
  """
  全市场数据同步流程 (批量优化版)

  分步骤执行：
  1. 获取目标板块的标的代码列表
  2. (可选) 过滤已存在的标的
  3. 将剩余标的分片，并发调用 chunk_sync_flow 处理入库
  4. (可选) 批量同步除权因子
  """
  logger = get_run_logger()
  start_time = time_utils.now()

  logger.info("=" * 60)
  logger.info("开始全市场基础数据同步任务 (Batch Mode)")
  logger.info(f"配置: 并发数={max_concurrency}, 跳过已存在={skip_existing}")
  logger.info("=" * 60)

  try:
    # 1. 获取代码列表
    if not sectors:
        sectors = ["沪深A股", "沪深ETF", "沪深指数"]
        
    logger.info(f"同步板块范围: {sectors}")
    stock_codes = await fetch_instrument_codes(sectors=sectors)
    total_found = len(stock_codes)
    logger.info(f"共获取到 {total_found} 只标的")

    if total_found == 0:
      logger.warning("未获取到任何标的代码")
      return {
        "status": "skipped",
        "reason": "代码列表为空",
        "start_time": start_time,
        "end_time": time_utils.now(),
      }

    # 2. 过滤已存在的标的
    skipped_count = 0
    if skip_existing:
      logger.info("正在查询数据库以排除已存在的标的...")
      instrument_service = InstrumentService()
      # 获取所有已存在的标的
      existing_instruments = await instrument_service.find_all(limit=50000)
      existing_codes = {inst.code for inst in existing_instruments}
      
      original_count = len(stock_codes)
      stock_codes = [code for code in stock_codes if code not in existing_codes]
      skipped_count = original_count - len(stock_codes)
      logger.info(f"跳过 {skipped_count} 只已存在的标的，剩余 {len(stock_codes)} 只待同步")

    if not stock_codes:
        logger.info("没有新的标的需要同步")
        return {
            "status": "success",
            "message": "所有标的已处于最新状态",
            "total_found": total_found,
            "skipped_count": skipped_count
        }

    # 3. 分片并发处理
    CHUNK_SIZE = 500
    chunks = [stock_codes[i:i + CHUNK_SIZE] for i in range(0, len(stock_codes), CHUNK_SIZE)]
    logger.info(f"任务分片: {len(chunks)} 个批次，每批最大 {CHUNK_SIZE} 只")
    
    semaphore = asyncio.Semaphore(max_concurrency)

    async def process_chunk(chunk_codes):
      async with semaphore:
        # 调用 Task 处理分片
        return await sync_instruments_batch_task(chunk_codes)

    # 创建并等待所有并发任务
    tasks = [asyncio.create_task(process_chunk(chunk)) for chunk in chunks]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 4. 统计结果
    logger.info("正在统计同步结果...")
    
    success_count = 0
    failed_count = 0
    error_count = 0
    total_saved = 0
    failed_batches = []
    
    for i, res in enumerate(results):
        if isinstance(res, Exception):
            error_count += 1
            failed_batches.append(f"Batch {i} 异常: {str(res)}")
            continue
            
        if res.get("status") == "failed":
            error_count += 1
            failed_batches.append(f"Batch {i} 失败: {res.get('error')}")
            
        success_count += res.get("success", 0)
        failed_count += res.get("failed", 0)
        total_saved += res.get("saved_count", 0)

    # 5. 同步除权因子（可选）
    divid_factor_result = None
    divid_factor_params = {
      "enabled": sync_divid_factors,
      "start_time": divid_factor_start_time,
      "end_time": divid_factor_end_time,
      "days_back": divid_factor_days_back,
    }
    if sync_divid_factors:
      try:
        logger.info("开始同步除权因子...")
        divid_factor_result = {
          "status": "success",
          "success_count": 0,
          "failed_count": 0,
          "errors": [],
          "details": [],
        }

        end_time_str = divid_factor_end_time or time_utils.now().strftime("%Y%m%d")
        last_sync_time = redis_client.get(DIVID_FACTOR_SYNC_REDIS_KEY)
        if divid_factor_start_time:
          start_time_str = divid_factor_start_time
        else:
          if last_sync_time:
            start_time_str = str(last_sync_time)
          else:
            start_time_str = (
              time_utils.now() - datetime.timedelta(days=divid_factor_days_back)
            ).strftime("%Y%m%d")
        divid_factor_params.update(
          {
            "start_time": start_time_str,
            "end_time": end_time_str,
            "last_sync_time": last_sync_time,
            "redis_key": DIVID_FACTOR_SYNC_REDIS_KEY,
          }
        )

        logger.info(
          f"开始批量同步除权因子: stocks={len(stock_codes)}, start={start_time_str}, end={end_time_str}"
        )

        # 批量获取所有除权因子数据
        fetch_result = await fetch_divid_factors_batch(
          stock_codes=stock_codes,
          start_time=start_time_str,
          end_time=end_time_str,
          max_concurrency=10,
        )

        # 一次性批量保存到数据库
        save_result = await save_divid_factors_batch(fetch_result=fetch_result)

        # 使用 fetch_result 中的统计信息来正确计算结果
        divid_factor_result = {
          "status": "success",
          "success_count": fetch_result["total_success"],
          "failed_count": fetch_result["total_failed"],
          "with_data_count": fetch_result["total_with_data"],
          "errors": [],
          "details": [save_result],
          "total_records": save_result.get("total_records", 0),
        }

        logger.info(
          f"批量同步除权因子完成: 成功 {fetch_result['total_success']}, "
          f"失败 {fetch_result['total_failed']}, "
          f"有数据 {fetch_result['total_with_data']} 只, "
          f"保存 {save_result.get('total_records', 0)} 条记录"
        )

        if divid_factor_result["failed_count"] > 0:
          divid_factor_result["status"] = "partial_success"

        if divid_factor_result["status"] not in ["success", "skipped"]:
          error_count += 1
          failed_batches.append(
            f"DividFactor 同步异常: status={divid_factor_result['status']}"
          )
        else:
          # 仅在同步成功或部分成功时记录同步时间
          redis_client.set(DIVID_FACTOR_SYNC_REDIS_KEY, end_time_str)
      except Exception as e:
        error_count += 1
        msg = f"DividFactor 同步异常: {e}"
        failed_batches.append(msg)
        divid_factor_result = {"status": "failed", "error": str(e)}
        logger.error(msg)

    # 6. 生成并发送报告
    end_time = time_utils.now()
    total_elapsed = (end_time - start_time).total_seconds()
    
    # 计算统计指标
    success_rate = (success_count / total_found * 100) if total_found > 0 else 0
    avg_duration = (total_elapsed / success_count) if success_count > 0 else 0
    
    overall_status = "success" if failed_count == 0 and error_count == 0 else "partial"
    
    report = await generate_batch_sync_report(
      task_name="全市场基础数据同步",
      report_type="market_sync",
      start_time=start_time,
      end_time=end_time,
      total_elapsed_seconds=total_elapsed,
      total_stocks=total_found,
      success_count=success_count,
      failed_count=failed_count,
      skipped_count=skipped_count,
      error_count=error_count,
      success_rate=success_rate,
      avg_duration_per_stock=avg_duration,
      status=overall_status,
      total_records_saved=total_saved,
      error_stocks=failed_batches,
      max_concurrency=max_concurrency,
      divid_factor_sync=divid_factor_result,
      divid_factor_params=divid_factor_params,
    )
    
    if overall_status != "success":
      await send_sync_notification(
         notification_type="partial_failure",
         report=report
      )

    logger.info("=" * 60)
    logger.info(f"同步完成! 状态: {overall_status}")
    logger.info(f"成功: {success_count}, 失败: {failed_count}, 耗时: {total_elapsed:.1f}s")
    logger.info("=" * 60)

    return report

  except Exception as e:
    logger.error(f"Flow 执行过程中发生未捕获异常: {e}")
    import traceback
    logger.error(traceback.format_exc())
    raise


