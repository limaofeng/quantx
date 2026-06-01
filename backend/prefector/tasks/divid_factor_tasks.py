"""
除权因子同步相关任务
"""

import asyncio
import datetime
from typing import Any, Dict, List

import pandas as pd
from prefect import get_run_logger, task
from prefect.cache_policies import INPUTS
from xtquant import xtdata

from services.divid_factor_service import DividFactorService
from core.utils import time_utils

DEFAULT_RETRIES = 3
SAVE_RETRIES = 2
FETCH_BATCH_RETRIES = 2


@task(
  name="获取除权因子",
  description="获取指定股票的除权因子数据",
  retries=DEFAULT_RETRIES,
  retry_delay_seconds=60,
  cache_policy=INPUTS,
  cache_expiration=datetime.timedelta(days=30),
)
async def fetch_divid_factors(
  stock_code: str,
  start_time: str,
  end_time: str,
) -> pd.DataFrame:
  logger = get_run_logger()
  xtdata.enable_hello = False

  try:
    data = xtdata.get_divid_factors(stock_code, start_time, end_time)
    if data is None:
      return pd.DataFrame()
    if not isinstance(data, pd.DataFrame):
      data = pd.DataFrame(data)
    return data
  except Exception as e:
    logger.error(f"获取除权因子失败: {stock_code}, {e}")
    raise e


@task(
  name="批量获取除权因子",
  description="批量获取多个股票的除权因子数据",
  retries=FETCH_BATCH_RETRIES,
  retry_delay_seconds=60,
)
async def fetch_divid_factors_batch(
  stock_codes: List[str],
  start_time: str,
  end_time: str,
  max_concurrency: int = 10,
) -> Dict[str, Any]:
  """批量获取除权因子，返回包含 factors_map 和统计信息的结果"""
  logger = get_run_logger()
  logger.info(f"开始批量获取除权因子: {len(stock_codes)} 只股票")

  xtdata.enable_hello = False
  factors_map: Dict[str, pd.DataFrame] = {}
  success_count = 0
  failed_count = 0
  semaphore = asyncio.Semaphore(max_concurrency)

  async def fetch_one(code: str):
    nonlocal success_count, failed_count
    async with semaphore:
      try:
        data = await asyncio.to_thread(
          xtdata.get_divid_factors, code, start_time, end_time
        )
        if data is None:
          data = pd.DataFrame()
        elif not isinstance(data, pd.DataFrame):
          data = pd.DataFrame(data)

        if not data.empty:
          factors_map[code] = data

        # 无论是否有数据，都算成功
        success_count += 1
      except Exception as e:
        failed_count += 1
        logger.warning(f"获取除权因子失败: {code}, {e}")

  tasks = [asyncio.create_task(fetch_one(code)) for code in stock_codes]
  await asyncio.gather(*tasks, return_exceptions=True)

  logger.info(
    f"批量获取完成: 成功 {success_count}, 失败 {failed_count}, "
    f"有数据 {len(factors_map)} 只"
  )

  return {
    "factors_map": factors_map,
    "total_success": success_count,
    "total_failed": failed_count,
    "total_with_data": len(factors_map),
  }


@task(
  name="批量保存除权因子",
  description="批量保存多个股票的除权因子到数据库",
  retries=SAVE_RETRIES,
  retry_delay_seconds=30,
)
async def save_divid_factors_batch(
  fetch_result: Dict[str, Any],
) -> Dict[str, Any]:
  """批量保存除权因子，一次性写入数据库"""
  logger = get_run_logger()

  try:
    factors_map = fetch_result["factors_map"]
    if not factors_map:
      logger.info("没有除权因子数据需要保存")
      return {
        "total_stocks": 0,
        "total_records": 0,
        "status": "success",
        "save_time": time_utils.now().isoformat(),
      }

    service = DividFactorService()
    total_saved = await service.save_batch_divid_factors(factors_map)

    logger.info(f"批量保存除权因子完成: 共保存 {total_saved} 条记录")

    return {
      "total_stocks": len(factors_map),
      "total_records": total_saved,
      "status": "success",
      "save_time": time_utils.now().isoformat(),
    }
  except Exception as e:
    logger.error(f"批量保存除权因子失败: {e}")
    raise e


@task(
  name="保存除权因子",
  description="保存除权因子到数据库",
  retries=SAVE_RETRIES,
  retry_delay_seconds=30,
)
async def save_divid_factors(
  stock_code: str,
  factors_df: pd.DataFrame,
) -> Dict[str, Any]:
  logger = get_run_logger()

  try:
    service = DividFactorService()
    saved_count = await service.save_divid_factors(stock_code, factors_df)
    return {
      "stock_code": stock_code,
      "saved_count": saved_count,
      "status": "success",
      "save_time": time_utils.now().isoformat(),
    }
  except Exception as e:
    logger.error(f"保存除权因子失败: {stock_code}, {e}")
    raise e
