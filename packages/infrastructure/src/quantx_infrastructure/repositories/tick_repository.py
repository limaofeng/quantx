"""
K线数据仓储实现
"""

import logging
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd
from quantx_contracts import HISTORICAL_TICK_ORDINALS_PER_MILLISECOND

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.timeseries_base import BaseRepository
from quantx_infrastructure.models.tick import Tick

logger = logging.getLogger(__name__)


class TickRepository(BaseRepository[Tick]):
  """Tick数据仓储"""

  model_class = Tick
  measurement = "ticks"

  MAX_SOURCE_IDENTITY_PAGE_SIZE = 10_000

  def find_source_identity_page(
    self,
    *,
    stock_code: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    after: Optional[tuple[int, int]] = None,
    limit: int = MAX_SOURCE_IDENTITY_PAGE_SIZE,
  ) -> List[Tick]:
    """Read one bounded page with a reversible storage-time keyset cursor.

    Persisted Tick points encode ``(source_time_ms, tick_ordinal)`` as
    ``UTC epoch(source_time_ms) + ordinal microseconds`` in the primary
    timestamp.  Paging on that timestamp lets Influx use its primary time
    index while the service still validates the source identity on every row.
    Missing or duplicated identities are therefore returned by the same query
    and fail closed in the service; no COUNT preflight or OFFSET cursor is
    needed.
    """

    from quantx_infrastructure.core.data.tick_identity import (
      tick_query_end_time,
      tick_storage_time,
    )

    page_size = int(limit)
    if page_size <= 0 or page_size > self.MAX_SOURCE_IDENTITY_PAGE_SIZE:
      raise ValueError(
        "historical Tick page size must be between 1 and 10000"
      )
    if after is not None:
      if len(after) != 2:
        raise ValueError("historical Tick cursor must be (source_time_ms, ordinal)")
      raw_time_ms, raw_ordinal = after
      if isinstance(raw_time_ms, bool) or isinstance(raw_ordinal, bool):
        raise ValueError("historical Tick cursor values are out of range")
      after_time_ms, after_ordinal = (int(raw_time_ms), int(raw_ordinal))
      if (
        after_time_ms <= 0
        or after_ordinal < 0
        or after_ordinal >= HISTORICAL_TICK_ORDINALS_PER_MILLISECOND
        or raw_time_ms != after_time_ms
        or raw_ordinal != after_ordinal
      ):
        raise ValueError("historical Tick cursor values are out of range")
    else:
      after_time_ms = after_ordinal = None

    normalized_start = self._normalize_query_time(start_time)
    normalized_end = self._normalize_query_time(tick_query_end_time(end_time))
    code = str(stock_code or "").strip()
    if not code:
      raise ValueError("stock_code is required for historical Tick paging")
    escaped_code = code.replace("'", "''")

    base_conditions = [
      f"stock_code = '{escaped_code}'",
      "period = 'tick'",
    ]
    if normalized_start is not None:
      base_conditions.append(f"time >= '{normalized_start.isoformat()}'")
    if normalized_end is not None:
      base_conditions.append(f"time <= '{normalized_end.isoformat()}'")

    conditions = list(base_conditions)
    if after_time_ms is not None:
      try:
        cursor_storage_time = time_utils.to_utc(
          tick_storage_time(after_time_ms, after_ordinal)
        )
      except (OverflowError, ValueError) as exc:
        raise ValueError("historical Tick cursor storage time is invalid") from exc
      conditions.append(
        f"time > '{cursor_storage_time.isoformat(timespec='microseconds')}'"
      )

    sql = (
      f"SELECT * FROM {self.measurement} WHERE {' AND '.join(conditions)} "
      "ORDER BY time ASC "
      f"LIMIT {page_size}"
    )
    rows = self.operations.query(sql, use_cache=False)
    if not rows:
      return []

    records = self._process_query_results(pd.DataFrame(rows))
    return self._bulk_dict_to_entities(records)

  def get_full_tick(self, stock_codes: List[str]) -> Dict[str, Tick]:
    """
    获取完整的Tick数据 - 每只股票的最新记录


    """
    if not stock_codes:
      raise ValueError("未提供有效的股票代码")

    try:
      # 构建股票代码的IN条件
      codes_str = "', '".join(stock_codes)

      # 使用窗口函数查询每只股票的最新记录
      sql = f"""
        SELECT * FROM (
          SELECT *, ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY time DESC) as rn
          FROM {self.measurement}
          WHERE stock_code IN ('{codes_str}')
        ) WHERE rn = 1
      """

      print(sql)

      # 执行查询
      results = self.operations.query(sql, use_cache=True)

      if not results:
        logger.debug(f"未找到股票的Tick数据: {stock_codes}")
        return {}

      # 转换为DataFrame进行字段处理
      import pandas as pd

      df = pd.DataFrame(results)

      # 删除窗口函数添加的rn列
      if "rn" in df.columns:
        df = df.drop("rn", axis=1)

      # 使用基类的通用结果处理方法
      df = self._process_query_results(df)

      ticks = self._bulk_dict_to_entities(df)
      # 转换为Tick对象字典
      result_dict = {}
      for tick in ticks:
        result_dict[tick.stock_code] = tick

      logger.debug(f"成功获取{len(result_dict)}只股票的Tick数据")
      return result_dict

    except Exception as e:
      logger.error(f"获取Tick数据失败: {e}")
      return {}
