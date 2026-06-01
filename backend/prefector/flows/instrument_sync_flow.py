"""
单只标的数据同步流程

通用流程，支持股票、ETF、指数等。
"""

from datetime import datetime
from typing import Any, Dict

from prefect import flow, get_run_logger

from prefector.flow_hooks import STANDARD_FLOW_HOOKS
from core.utils import time_utils
from prefector.tasks import (
    fetch_stock_info,
    fetch_stock_financial_data,
    save_single_stock_data,
)


@flow(
  name="单只标的数据同步",
  description="单只标的（股票/ETF/指数）的完整数据同步流程",
  retries=2,
  retry_delay_seconds=60,
  **STANDARD_FLOW_HOOKS
)
async def instrument_sync_flow(stock_code: str) -> Dict[str, Any]:
  """
  单只标的数据同步子流程

  Args:
      stock_code: 标的代码，如 "000001.SZ", "159915.SZ", "000001.SH"

  Returns:
      同步结果
  """
  logger = get_run_logger()
  start_time = time_utils.now()

  logger.info(f"▶ 开始同步标的: {stock_code}")

  try:
    financial_records_saved = 0

    # 步骤1: 获取基础信息
    logger.debug(f"步骤1: 获取 {stock_code} 基础信息")
    stock_info = await fetch_stock_info(stock_code)

    if not stock_info:
      logger.warning(f"⚠️ 未找到标的信息: {stock_code}，跳过")
      return {
        "stock_code": stock_code,
        "status": "skipped",
        "reason": "标的信息不存在",
        "start_time": start_time,
        "end_time": time_utils.now(),
      }

    # 步骤2: 保存基础信息 (Instrument表)
    logger.debug(f"步骤2: 保存 {stock_code} 基础信息")
    await save_single_stock_data(stock_code=stock_code, stock_info=stock_info)

    # 步骤3: 获取财务数据 (仅限股票)
    instrument_type = stock_info.get("InstrumentType", -1)
    
    # 简单的类型判断逻辑: 0通常是股票
    # 只有股票才尝试获取财务数据
    if instrument_type == 0:
        logger.debug(f"步骤3: 获取 {stock_code} 财务数据")
        financial_data = await fetch_stock_financial_data(stock_code)
        if financial_data:
          from services.financial_service import FinancialService

          service = FinancialService()
          financial_records_saved = await service.save_batch_financial_data(
            {stock_code: financial_data}
          )
          logger.debug(f"步骤3: 保存 {stock_code} 财务数据 {financial_records_saved} 条")
        else:
          logger.debug(f"步骤3: {stock_code} 无可保存财务数据")
    else:
        logger.debug(f"步骤3: 跳过非股票标的财务数据同步 (Type: {instrument_type})")

    end_time = time_utils.now()
    duration = (end_time - start_time).total_seconds()
    
    logger.info(f"✓ {stock_code} 同步成功 (耗时: {duration:.2f}s)")

    return {
      "stock_code": stock_code,
      "status": "success",
      "start_time": start_time,
      "end_time": end_time,
      "duration_seconds": duration,
      "type": instrument_type,
      "financial_records_saved": financial_records_saved,
    }

  except Exception as e:
    end_time = time_utils.now()
    duration = (end_time - start_time).total_seconds()
    logger.error(f"✗ {stock_code} 同步失败: {e}")
    raise
