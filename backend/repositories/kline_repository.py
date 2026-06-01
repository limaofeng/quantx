"""
K线数据仓储实现
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from database.timeseries_base import BaseRepository
from models.kline import KLine
from core.utils import time_utils

logger = logging.getLogger(__name__)


class KLineRepository(BaseRepository[KLine]):
  """K线数据仓储"""

  model_class = KLine

  def find_by_stock_code_and_period(
    self,
    stock_code: str,
    period: str,
    start: datetime,
    end: datetime,
    limit: Optional[int] = None,
    offset: int = 0,
    use_cache: bool = True,
  ) -> List[KLine]:
    """根据股票代码、周期和时间范围查询K线数据"""
    if not self.influx_manager or not self.influx_manager.is_connected():
      logger.warning("InfluxDB未连接，返回空K线数据")
      return []

    sql = f"""
        SELECT *
        FROM {self.measurement_name}
        WHERE stock_code = '{stock_code}'
        AND period = '{period}'
        AND time >= '{start.isoformat()}'
        AND time <= '{end.isoformat()}'
        ORDER BY time ASC
        """

    if limit:
      sql += f" LIMIT {limit}"
    if offset:
      sql += f" OFFSET {offset}"

    return self._execute_query(sql, use_cache)

  def find_by_period_and_time_range(
    self,
    period: str,
    start: datetime,
    end: datetime,
    stock_codes: Optional[List[str]] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    use_cache: bool = True,
  ) -> List[KLine]:
    """根据周期和时间范围查询K线数据"""
    if not self.influx_manager or not self.influx_manager.is_connected():
      logger.warning("InfluxDB未连接，返回空K线数据")
      return []

    sql = f"""
        SELECT *
        FROM {self.measurement_name}
        WHERE period = '{period}'
        AND time >= '{start.isoformat()}'
        AND time <= '{end.isoformat()}'
        """

    if stock_codes:
      codes_str = "', '".join(stock_codes)
      sql += f" AND stock_code IN ('{codes_str}')"

    sql += " ORDER BY stock_code, time ASC"

    if limit:
      sql += f" LIMIT {limit}"
    if offset:
      sql += f" OFFSET {offset}"

    return self._execute_query(sql, use_cache)

  def find_latest_by_stock_code_and_period(
    self, stock_code: str, period: str, limit: int = 1
  ) -> List[KLine]:
    """查询指定股票和周期的最新K线数据"""
    if not self.influx_manager or not self.influx_manager.is_connected():
      logger.warning("InfluxDB未连接，返回空K线数据")
      return []

    sql = f"""
        SELECT *
        FROM {self.measurement_name}
        WHERE stock_code = '{stock_code}'
        AND period = '{period}'
        ORDER BY time DESC
        LIMIT {limit}
        """

    return self._execute_query(sql, use_cache=False)

  def get_kline_summary(
    self, stock_code: str, period: str, days: int = 30
  ) -> Dict[str, Any]:
    """获取K线数据摘要统计"""
    end_time = time_utils.now()
    start_time = end_time - timedelta(days=days)

    kline_data = self.find_by_stock_code_and_period(
      stock_code, period, start_time, end_time
    )

    if not kline_data:
      return {}

    prices = [item.close_price for item in kline_data]
    volumes = [item.volume for item in kline_data]

    return {
      "count": len(kline_data),
      "period": period,
      "start_time": start_time,
      "end_time": end_time,
      "price_summary": {
        "max": max(prices),
        "min": min(prices),
        "avg": sum(prices) / len(prices),
        "first": prices[0] if prices else 0,
        "last": prices[-1] if prices else 0,
      },
      "volume_summary": {
        "max": max(volumes),
        "min": min(volumes),
        "avg": sum(volumes) / len(volumes),
        "total": sum(volumes),
      },
    }

  def delete_by_stock_code_and_period(
    self,
    stock_code: str,
    period: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
  ) -> bool:
    """删除指定股票和周期的K线数据"""
    if not self.influx_manager or not self.influx_manager.is_connected():
      logger.warning("InfluxDB未连接，无法删除数据")
      return False

    try:
      sql = f"""
            DELETE FROM {self.measurement_name}
            WHERE stock_code = '{stock_code}'
            AND period = '{period}'
            """

      if start:
        sql += f" AND time >= '{start.isoformat()}'"
      if end:
        sql += f" AND time <= '{end.isoformat()}'"

      with self.influx_manager.get_client() as client:
        client.query(query=sql, language="sql")

      logger.debug(f"删除K线数据成功: {stock_code} {period}")
      return True

    except Exception as e:
      logger.error(f"删除K线数据失败: {e}")
      return False
