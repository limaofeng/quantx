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
FINANCIAL_BATCH_FLOW_TIMEOUT_GRACE_SECONDS = 30


@flow(
  name="全市场财务数据同步",
  description="从数据库获取股票信息，批量同步财务数据",
  retries=1,
  retry_delay_seconds=300,
  **STANDARD_FLOW_HOOKS
)
async def batch_financial_sync_flow(
  max_concurrency: int = 3,
  limit: Optional[int] = None,
  stock_codes: Optional[List[str]] = None,
  batch_size: int = 25,
  batch_timeout_seconds: int = 300,
  fail_on_batch_error: bool = True,
) -> Dict[str, Any]:
  """
  全市场财务数据同步流程
  
  Args:
      max_concurrency: 并发分片数
      limit: 限制处理的股票数量（用于测试）
      stock_codes: 指定股票代码列表；为空时同步全市场股票财务数据
      batch_size: 每个财务同步批次包含的股票数量
      batch_timeout_seconds: 单个批次允许的最大运行秒数
      fail_on_batch_error: 任一批次异常时是否让 Prefect FlowRun 失败
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
    max_concurrency = max(1, min(max_concurrency, 10))
    batch_size = max(1, min(batch_size, 200))
    batch_timeout_seconds = max(30, min(batch_timeout_seconds, 900))
    outer_batch_timeout_seconds = (
      batch_timeout_seconds + FINANCIAL_BATCH_FLOW_TIMEOUT_GRACE_SECONDS
    )

    chunks = [
      stock_codes[i:i + batch_size]
      for i in range(0, len(stock_codes), batch_size)
    ]
    batch_plans = [
      {
        "batch_index": index,
        "batch_total": len(chunks),
        "batch_label": f"{index}/{len(chunks)}",
        "stock_codes": chunk,
        "total": len(chunk),
        "stock_range_start": chunk[0],
        "stock_range_end": chunk[-1],
      }
      for index, chunk in enumerate(chunks, start=1)
    ]
    logger.info(
      f"将 {total_stocks} 只股票分为 {len(chunks)} 个批次，每批 {batch_size}，"
      f"并发数 {max_concurrency}，单批超时 {batch_timeout_seconds}s"
    )
    
    semaphore = asyncio.Semaphore(max_concurrency)

    async def process_chunk(plan):
      async with semaphore:
        chunk_codes = plan["stock_codes"]
        logger.info(
          f"开始财务同步批次 {plan['batch_label']}，"
          f"股票数 {len(chunk_codes)}，范围 {chunk_codes[0]} ~ {chunk_codes[-1]}"
        )
        task = asyncio.create_task(
          sync_financial_batch_task(
            chunk_codes,
            batch_index=plan["batch_index"],
            batch_total=plan["batch_total"],
            timeout_seconds=batch_timeout_seconds,
          )
        )
        done, pending = await asyncio.wait(
          {task},
          timeout=outer_batch_timeout_seconds,
        )
        if pending:
          task.cancel()
          raise TimeoutError(
            f"财务同步批次 {plan['batch_label']} 超过外层超时 "
            f"{outer_batch_timeout_seconds}s，范围 "
            f"{chunk_codes[0]}~{chunk_codes[-1]}"
          )
        return await done.pop()

    tasks = [
      asyncio.create_task(process_chunk(plan))
      for plan in batch_plans
    ]
    
    logger.info("任务创建完成，等待执行...")
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 步骤3: 统计结果
    logger.info("步骤3: 统计结果")
    
    success_count = 0
    failed_count = 0
    error_count = 0
    total_saved = 0
    
    failed_batches = []
    
    for plan, res in zip(batch_plans, results):
      batch_label = plan["batch_label"]
      planned_total = plan["total"]

      if isinstance(res, Exception):
        error_count += 1
        failed_count += planned_total
        failed_batches.append(f"Batch {batch_label}: {str(res)}")
        continue

      if not isinstance(res, dict):
        error_count += 1
        failed_count += planned_total
        failed_batches.append(f"Batch {batch_label}: 返回结果类型异常 {type(res).__name__}")
        continue

      validation_errors = _validate_financial_batch_result(plan, res)
      if validation_errors:
        error_count += 1
        failed_count += planned_total
        failed_batches.append(
          f"Batch {batch_label}: 返回结果不匹配: {'; '.join(validation_errors)}"
        )
        continue

      status = res.get("status") or (
        "success" if _to_int(res.get("failed", 0)) == 0 else "partial"
      )
      batch_success = _to_int(res.get("success", 0))
      batch_failed = _to_int(res.get("failed", 0))

      if status != "success":
        error_count += 1
        failed_batches.append(
          f"Batch {batch_label}: status={status}, success={batch_success}, "
          f"failed={batch_failed}, error={res.get('error')}"
        )

      success_count += batch_success
      failed_count += batch_failed
      total_saved += _to_int(res.get("saved_count", 0))

    # 计算整体状态
    if failed_count == 0 and error_count == 0:
      overall_status = "success"
    elif success_count == 0:
      overall_status = "failed"
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
      batch_size=batch_size,
      batch_timeout_seconds=batch_timeout_seconds,
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

    if fail_on_batch_error and error_count > 0:
      error_preview = "; ".join(failed_batches[:3])
      if len(failed_batches) > 3:
        error_preview = f"{error_preview}; ... 共 {len(failed_batches)} 个异常批次"
      raise RuntimeError(
        f"财务数据同步存在异常批次，status={overall_status}, "
        f"success={success_count}, failed={failed_count}, "
        f"errors={error_count}: {error_preview}"
      )

    return report

  except Exception as e:
    logger.error(f"财务数据同步流程失败: {e}")
    raise


def _validate_financial_batch_result(
  plan: Dict[str, Any],
  result: Dict[str, Any],
) -> List[str]:
  errors = []

  expected_index = plan["batch_index"]
  actual_index = result.get("batch_index")
  if actual_index is not None and actual_index != expected_index:
    errors.append(f"batch_index expected={expected_index}, actual={actual_index}")

  expected_total = plan["batch_total"]
  actual_total = result.get("batch_total")
  if actual_total is not None and actual_total != expected_total:
    errors.append(f"batch_total expected={expected_total}, actual={actual_total}")

  expected_count = plan["total"]
  actual_count = result.get("total")
  if actual_count is not None and _to_int(actual_count) != expected_count:
    errors.append(f"total expected={expected_count}, actual={actual_count}")

  expected_start = plan["stock_range_start"]
  actual_start = result.get("stock_range_start")
  if actual_start is not None and actual_start != expected_start:
    errors.append(f"range_start expected={expected_start}, actual={actual_start}")

  expected_end = plan["stock_range_end"]
  actual_end = result.get("stock_range_end")
  if actual_end is not None and actual_end != expected_end:
    errors.append(f"range_end expected={expected_end}, actual={actual_end}")

  actual_stock_codes = result.get("stock_codes")
  if actual_stock_codes is not None and actual_stock_codes != plan["stock_codes"]:
    errors.append("stock_codes 与计划批次不一致")

  batch_success = _to_int(result.get("success", 0))
  batch_failed = _to_int(result.get("failed", 0))
  if batch_success < 0 or batch_failed < 0:
    errors.append(
      f"success/failed 不能为负数: success={batch_success}, failed={batch_failed}"
    )
  elif batch_success + batch_failed != expected_count:
    errors.append(
      f"success+failed expected={expected_count}, "
      f"actual={batch_success + batch_failed}"
    )

  return errors


def _to_int(value: Any) -> int:
  try:
    return int(value)
  except (TypeError, ValueError):
    return 0
