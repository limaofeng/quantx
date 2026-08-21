"""Persist market-data batches uploaded by the QMT Agent."""

import asyncio
from typing import Any, Dict

import pandas as pd
from prefect import get_run_logger, task
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.timeseries_connection import is_fatal_wal_error
from quantx_infrastructure.services.historical_market_data_service import (
  HistoricalMarketDataService,
)
from quantx_infrastructure.services.market_data_transfer_ingestion import (
  preprocess_market_data,
)

SAVE_RETRIES = 2


def _save_market_data_sync(
  period: str,
  market_data: Dict[str, pd.DataFrame],
) -> int:
  market_data_service = HistoricalMarketDataService()
  normalized = preprocess_market_data(period, market_data)
  return (
    market_data_service.bulk_save_ticks(normalized)
    if period == "tick"
    else market_data_service.bulk_save_klines(period, normalized)
  )


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
    saved_count = await asyncio.to_thread(
      _save_market_data_sync,
      period,
      market_data,
    )
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
