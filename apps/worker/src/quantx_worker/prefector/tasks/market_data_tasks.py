"""Persist market-data batches uploaded by the QMT Agent."""

from typing import Any, Dict

import pandas as pd
from prefect import get_run_logger, task
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.timeseries_connection import is_fatal_wal_error
from quantx_infrastructure.services.historical_market_data_service import (
  HistoricalMarketDataService,
)

SAVE_RETRIES = 2


def _should_retry_market_data_save(_task: Any, _task_run: Any, state: Any) -> bool:
  """Keep transient retries, but never repeat a server-fatal Influx WAL write."""
  failure = state.result(raise_on_failure=False)
  return not is_fatal_wal_error(failure)


@task(
  name="保存K线数据",
  description="将K线数据保存到数据库",
  retries=SAVE_RETRIES,
  retry_delay_seconds=30,
  retry_condition_fn=_should_retry_market_data_save,
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
