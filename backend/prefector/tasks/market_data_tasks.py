"""
市场数据相关的原子任务

包含K线数据、tick数据的获取和保存任务
"""

import datetime
import time
from typing import Any, Dict, List

import pandas as pd
from prefect import get_run_logger, task
from prefect.cache_policies import INPUTS

from miniqmt import XTDataManagerRegistry
from services.historical_market_data_service import HistoricalMarketDataService
from core.utils import time_utils

# 缓存配置
CACHE_EXPIRATION = datetime.timedelta(minutes=30)
PRICE_CACHE_EXPIRATION = datetime.timedelta(minutes=1)

# 重试配置
DEFAULT_RETRIES = 3
SAVE_RETRIES = 2


@task(
  name="下载市场数据",
  description="下载指定股票的单日市场数据",
  retries=DEFAULT_RETRIES,
  retry_delay_seconds=60,
  cache_policy=INPUTS,
  cache_expiration=datetime.timedelta(days=30),
)
async def download_market_data(
  stock_list: List[str],
  period: str,
  date_time: str,
) -> Dict[str, Any]:
  logger = get_run_logger()
  logger.info(f"开始下载 {len(stock_list)} 只股票的 {period} 数据，日期: {date_time}")

  data_registry = XTDataManagerRegistry()
  data_manager = data_registry.get_manager()

  previous_progress = 0
  last_print_time = 0

  download_stock_list = []

  def on_progress(progress):
    nonlocal previous_progress, last_print_time
    try:
      finished = progress.get("finished", 0)
      total = progress.get("total", 1)
      stock_code = progress.get("message", "")

      if stock_code and stock_code not in download_stock_list:
        download_stock_list.append(stock_code)

      progress_pct = int((finished / total) * 100 if total > 0 else 0)

      current_time = time.time()
      should_print = False
      if current_time - last_print_time >= 1:
        should_print = True
        last_print_time = current_time
      elif finished == total:  # 完成时总是打印
        should_print = True

      if should_print and progress_pct > previous_progress:
        previous_progress = progress_pct
        logger.info(f"下载进度: {progress_pct}%, finished: {finished}, total: {total}")
        download_stock_list.clear()
    except Exception as e:
      logger.error(f"Progress callback error: {e}")

  try:
    data_manager.download_market_data(
      stock_list=stock_list,
      period=period,
      start_time=date_time,
      end_time=date_time,
      callback=on_progress,
    )
    if previous_progress != 100:
      raise Exception("下载未完成，进度未达100%")

    logger.info(
      f"批量下载市场数据完成，共 {len(stock_list)} 只股票的 {period} 数据，日期: {date_time}"
    )

  except Exception as e:
    logger.error(f"批量下载市场数据失败: {e}")
    raise e


@task(
  name="保存K线数据",
  description="将K线数据保存到数据库",
  retries=SAVE_RETRIES,
  retry_delay_seconds=30,
)
async def save_market_data(
  period: str, market_data: Dict[str, pd.DataFrame]
) -> Dict[str, Any]:
  """
  保存K线数据到数据库

  Args:
      market_data: K线数据字典

  Returns:
      保存结果
  """
  logger = get_run_logger()

  try:
    market_data_service = HistoricalMarketDataService()

    all_market_data = preprocess_market_data(period, market_data)

    if period == "tick":
      saved_count = market_data_service.bulk_save_ticks(all_market_data)
    else:
      saved_count = market_data_service.bulk_save_klines(period, all_market_data)
    logger.info(f"保存 {period} 数据完成，共保存 {saved_count} 条记录")

    return {
      "period": period,
      "saved_count": saved_count,
      "save_time": time_utils.now().isoformat(),
      "status": "success",
    }

  except Exception as e:
    logger.error(f"保存数据失败: {e}")
    raise e


def preprocess_market_data(
  period: str, market_data: Dict[str, pd.DataFrame]
) -> pd.DataFrame:
  logger = get_run_logger()
  # 高效合并：一次性 concat，避免循环拷贝
  dfs_with_code = [
    df.assign(stock_code=stock_code)  # 使用 assign 添加列，无需拷贝
    for stock_code, df in market_data.items()
  ]
  all_market_data = pd.concat(dfs_with_code, ignore_index=True)

  logger.info(f"所有股票数据合并后总记录数: {len(all_market_data)}")

  # 释放原始字典以节省内存
  del market_data, dfs_with_code

  logger.info(f"合并后数据预览: \n{all_market_data.head(3)}")

  logger.info("对数据进行清洗和格式化...")

  all_market_data["period"] = period
  all_market_data["time"] = pd.to_datetime(
    all_market_data["time"], unit="ms", utc=True
  ).dt.tz_convert("Asia/Shanghai")

  if period == "tick":
    pass
    all_market_data.rename(
      columns={
        "lastPrice": "last_price",
        "lastClose": "last_close",
        "settlementPrice": "settlement_price",
        "lastSettlementPrice": "last_settlement_price",
        "stockStatus": "stock_status",
        "openInt": "open_int",
        "transactionNum": "transaction_num",
        "askPrice": "ask_price",
        "bidPrice": "bid_price",
        "askVol": "ask_vol",
        "bidVol": "bid_vol",
      },
      inplace=True,
    )
    price_columns = [
      "last_price",
      "open",
      "high",
      "low",
      "last_close",
      "last_settlement_price",
    ]
    all_market_data[price_columns] = all_market_data[price_columns].round(3)
    all_market_data[["volume", "amount", "pvolume", "tickvol"]] = (
      all_market_data[["volume", "amount", "pvolume", "tickvol"]].astype(float).round(2)
    )
    all_market_data[["stock_status", "open_int", "transaction_num"]] = (
      all_market_data[["stock_status", "open_int", "transaction_num"]]
      .fillna(0)
      .astype(int)
    )
  else:
    all_market_data.rename(
      columns={
        "settelementPrice": "settelement_price",
        "openInterest": "open_interest",
        "preClose": "pre_close",
        "suspendFlag": "suspend_flag",
      },
      inplace=True,
    )
    price_columns = ["open", "high", "low", "close", "pre_close", "settelement_price"]
    all_market_data[price_columns] = all_market_data[price_columns].round(3)
    all_market_data[["volume", "amount"]] = (
      all_market_data[["volume", "amount"]].astype(float).round(2)
    )
    all_market_data[["open_interest", "suspend_flag"]] = (
      all_market_data[["open_interest", "suspend_flag"]].fillna(0).astype(int)
    )

  logger.info(f"清洗后数据预览: \n{all_market_data.head(3)}")

  return all_market_data
