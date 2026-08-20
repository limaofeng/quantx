"""
时间序列数据库操作模块
提供高级数据操作接口
"""

import logging
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import pandas as pd

if TYPE_CHECKING:
  pass

from .timeseries_connection import (
  NonRetryableWriteError,
  QueryError,
  TimeSeriesConnection,
  WriteError,
  is_fatal_wal_error,
)

logger = logging.getLogger(__name__)


class TimeSeriesOperations:
  """时间序列数据库操作类"""

  def __init__(self, connection: TimeSeriesConnection):
    self.connection = connection

  def _execute_with_retry(self, operation, *args, **kwargs):
    """带重试的操作执行"""
    last_exception = None

    for attempt in range(self.connection.max_retries + 1):
      try:
        return operation(*args, **kwargs)
      except Exception as e:
        last_exception = e
        message = str(e).lower()
        if is_fatal_wal_error(e):
          self.connection._stats["errors"] += 1
          logger.error(
            "InfluxDB WAL 处于服务端致命状态，停止本次操作及内部重试: %s",
            e,
          )
          raise NonRetryableWriteError(
            f"InfluxDB WAL 处于不可重试状态: {e}"
          ) from e
        if (
          "query file limit exceeded" in message
          or "would scan" in message and "parquet files" in message
        ):
          self.connection._stats["errors"] += 1
          logger.error("时间序列查询范围无效，停止重试: %s", e)
          raise
        logger.warning(
          f"操作失败，尝试 {attempt + 1}/{self.connection.max_retries + 1}: {e}"
        )

        if attempt < self.connection.max_retries:
          time.sleep(self.connection.retry_delay * (2**attempt))
        else:
          self.connection._stats["errors"] += 1
          raise last_exception

  def _get_cache_key(self, operation: str, **kwargs) -> str:
    """生成缓存键"""
    key_parts = [operation]
    for k, v in sorted(kwargs.items()):
      key_parts.append(f"{k}={v}")
    return "|".join(key_parts)

  def _get_from_cache(self, cache_key: str) -> Optional[Any]:
    """从缓存获取数据"""
    if not self.connection.enable_cache:
      return None

    with self.connection._cache_lock:
      if cache_key not in self.connection._query_cache:
        self.connection._stats["cache_misses"] += 1
        return None

      timestamp = self.connection._cache_timestamps.get(cache_key, 0)
      if time.time() - timestamp > self.connection.cache_ttl:
        self.connection._query_cache.pop(cache_key, None)
        self.connection._cache_timestamps.pop(cache_key, None)
        self.connection._stats["cache_misses"] += 1
        return None

      self.connection._stats["cache_hits"] += 1
      return self.connection._query_cache[cache_key]

  def _set_cache(self, cache_key: str, data: Any):
    """设置缓存数据"""
    if not self.connection.enable_cache:
      return

    with self.connection._cache_lock:
      self.connection._query_cache[cache_key] = data
      self.connection._cache_timestamps[cache_key] = time.time()

  def build_point(
    self,
    measurement: str,
    tags: Dict[str, str],
    fields: Dict[str, Any],
    timestamp: Optional[datetime] = None,
  ) -> Any:
    """构建数据点"""
    try:
      from influxdb_client_3 import Point
    except Exception as exc:  # pragma: no cover
      raise WriteError(f"InfluxDB Point 构建依赖导入失败: {exc}")
    point = Point(measurement)

    for key, value in tags.items():
      point = point.tag(key, str(value))

    for key, value in fields.items():
      point = point.field(key, value)

    if timestamp:
      point = point.time(timestamp)

    return point

  def write_point(
    self,
    measurement: str,
    tags: Dict[str, str],
    fields: Dict[str, Any],
    timestamp: Optional[datetime] = None,
  ):
    """写入单个数据点"""
    try:
      with self.connection.get_client() as client:
        point = self.build_point(
          measurement=measurement,
          tags=tags,
          fields=fields,
          timestamp=timestamp,
        )

        client.write(record=point)
        self.connection._stats["writes"] += 1
        logger.debug(f"写入数据点成功: {measurement}")

    except NonRetryableWriteError:
      logger.exception("写入数据点失败：InfluxDB WAL 处于不可重试状态")
      raise
    except Exception as e:
      logger.error(f"写入数据点失败: {e}")
      self.connection._stats["errors"] += 1
      if is_fatal_wal_error(e):
        raise NonRetryableWriteError(
          f"InfluxDB WAL 处于不可重试状态: {e}"
        ) from e
      raise WriteError(f"写入数据点失败: {e}")

  def write_dataframe(
    self,
    dataframe: pd.DataFrame,
    measurement: str,
    tag_columns: List[str],
    timestamp_column: str = "time",
    batch_size: int = 1000,
  ):
    """批量写入DataFrame数据"""
    if dataframe.empty:
      return

    def _write_batch(batch_df: pd.DataFrame):
      with self.connection.get_client() as client:
        client.write(
          record=batch_df,
          data_frame_measurement_name=measurement,
          data_frame_tag_columns=tag_columns,
          data_frame_timestamp_column=timestamp_column,
        )

    try:
      for i in range(0, len(dataframe), batch_size):
        batch = dataframe.iloc[i : i + batch_size]
        self._execute_with_retry(_write_batch, batch)

      self.connection._stats["writes"] += len(dataframe)
      logger.debug(f"写入DataFrame数据成功: {len(dataframe)}条")

    except NonRetryableWriteError:
      logger.exception("写入DataFrame数据失败：InfluxDB WAL 处于不可重试状态")
      raise
    except Exception as e:
      logger.error(f"写入DataFrame数据失败: {e}")
      self.connection._stats["errors"] += 1
      if is_fatal_wal_error(e):
        raise NonRetryableWriteError(
          f"InfluxDB WAL 处于不可重试状态: {e}"
        ) from e
      raise WriteError(f"写入DataFrame数据失败: {e}")

  def write_records(
    self,
    records: pd.DataFrame,
    measurement: str,
    tag_columns: List[str],
    timestamp_column: str = "time",
    batch_size: int = 1000,
  ):
    """批量写入记录数据"""
    if records is None or records.empty:
      return

    self.write_dataframe(
      records, measurement, tag_columns, timestamp_column, batch_size
    )

  def query(
    self,
    sql: str,
    use_cache: bool = True,
    cache_key_suffix: str = "",
  ) -> List[Dict[str, Any]]:
    """执行SQL查询"""

    cache_key = self._get_cache_key("query", sql=sql, suffix=cache_key_suffix)

    if use_cache:
      cached_result = self._get_from_cache(cache_key)
      if cached_result is not None:
        return cached_result

    def _execute_query():
      with self.connection.get_client() as client:
        table = client.query(query=sql, language="sql")

        data = []
        if table is not None:
          df = table.to_pandas()
          if not df.empty:
            data = df.to_dict(orient="records")
        return data

    try:
      result = self._execute_with_retry(_execute_query)
      self.connection._stats["queries"] += 1

      if use_cache:
        self._set_cache(cache_key, result)

      return result

    except Exception as e:
      logger.error(f"查询失败: {e}")
      self.connection._stats["errors"] += 1
      raise QueryError(f"查询失败: {e}")

  def query_range(
    self,
    measurement: str,
    filters: Dict[str, Any] = None,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    fields: List[str] = None,
    limit: Optional[int] = None,
    offset: int = 0,
    order_by: str = "time ASC",
    use_cache: bool = True,
    use_chunking: bool = True,
    chunk_hours: Optional[int] = None,
  ) -> List[Dict[str, Any]]:
    """查询时间范围内的数据"""

    field_list = "*" if not fields else ", ".join(fields)

    cache_key_suffix = (
      f"{measurement}_{filters}_{start_time}_{end_time}_{fields}_{limit}_{offset}_{order_by}"
    )
    cache_key = self._get_cache_key("query_range", suffix=cache_key_suffix)

    if use_cache:
      cached_result = self._get_from_cache(cache_key)
      if cached_result is not None:
        return cached_result

    def _build_sql(
      range_start: Optional[datetime], range_end: Optional[datetime]
    ) -> str:
      sql = f"SELECT {field_list} FROM {measurement}"
      conditions = []

      if filters:
        for key, value in filters.items():
          if isinstance(value, str):
            conditions.append(f"{key} = '{value}'")
          else:
            conditions.append(f"{key} = {value}")

      if range_start:
        conditions.append(f"time >= '{range_start.isoformat()}'")
      if range_end:
        conditions.append(f"time <= '{range_end.isoformat()}'")

      if conditions:
        sql += " WHERE " + " AND ".join(conditions)

      if order_by:
        sql += f" ORDER BY {order_by}"

      return sql

    def _order_by_time_only(order_clause: Optional[str]) -> bool:
      if not order_clause:
        return True
      columns = []
      for part in order_clause.split(","):
        name = part.strip().split(" ")[0].strip()
        if name:
          columns.append(name.lower())
      return len(columns) > 0 and all(col == "time" for col in columns)

    def _should_chunk() -> bool:
      if not use_chunking:
        return False
      if start_time is None or end_time is None:
        return False
      if not _order_by_time_only(order_by):
        return False
      effective_chunk_hours = (
        chunk_hours
        if chunk_hours is not None
        else getattr(self.connection, "query_chunk_hours", 0)
      ) or 0
      if effective_chunk_hours <= 0:
        return False
      if end_time <= start_time:
        return False
      return (end_time - start_time) > timedelta(hours=effective_chunk_hours)

    def _query_chunked() -> List[Dict[str, Any]]:
      effective_chunk_hours = (
        chunk_hours
        if chunk_hours is not None
        else getattr(self.connection, "query_chunk_hours", 0)
      ) or 0
      chunk_size = timedelta(hours=effective_chunk_hours)
      results: List[Dict[str, Any]] = []
      remaining_offset = offset or 0
      remaining_limit = limit

      order_desc = (order_by or "").lower().find("desc") != -1
      if order_desc:
        range_end = end_time
        while range_end and range_end > start_time:
          range_start = max(start_time, range_end - chunk_size)
          sql = _build_sql(range_start, range_end)
          logger.debug(f"query_range chunk SQL: {sql}")
          chunk_rows = self.query(sql, use_cache=False)

          if remaining_offset:
            if remaining_offset >= len(chunk_rows):
              remaining_offset -= len(chunk_rows)
              chunk_rows = []
            else:
              chunk_rows = chunk_rows[remaining_offset:]
              remaining_offset = 0

          if remaining_limit is not None:
            if len(chunk_rows) > remaining_limit:
              chunk_rows = chunk_rows[:remaining_limit]
            remaining_limit -= len(chunk_rows)

          results.extend(chunk_rows)

          if remaining_limit is not None and remaining_limit <= 0:
            break

          range_end = range_start - timedelta(microseconds=1)
      else:
        range_start = start_time
        while range_start and range_start < end_time:
          range_end = min(end_time, range_start + chunk_size)
          sql = _build_sql(range_start, range_end)
          logger.debug(f"query_range chunk SQL: {sql}")
          chunk_rows = self.query(sql, use_cache=False)

          if remaining_offset:
            if remaining_offset >= len(chunk_rows):
              remaining_offset -= len(chunk_rows)
              chunk_rows = []
            else:
              chunk_rows = chunk_rows[remaining_offset:]
              remaining_offset = 0

          if remaining_limit is not None:
            if len(chunk_rows) > remaining_limit:
              chunk_rows = chunk_rows[:remaining_limit]
            remaining_limit -= len(chunk_rows)

          results.extend(chunk_rows)

          if remaining_limit is not None and remaining_limit <= 0:
            break

          range_start = range_end + timedelta(microseconds=1)

      return results

    if _should_chunk():
      result = _query_chunked()
    else:
      sql = _build_sql(start_time, end_time)
      if limit:
        sql += f" LIMIT {limit}"
      if offset:
        sql += f" OFFSET {offset}"
      logger.debug(f"query_range SQL: {sql}")
      result = self.query(sql, use_cache=False)

    if use_cache:
      self._set_cache(cache_key, result)

    return result


  def get_statistics(self) -> Dict[str, Any]:
    """获取统计信息"""
    with self.connection._cache_lock:
      stats = dict(self.connection._stats)
      cache_lookups = stats["cache_hits"] + stats["cache_misses"]
      return {
        **stats,
        "cache_hit_rate": (
          stats["cache_hits"] / cache_lookups if cache_lookups > 0 else 0
        ),
        "cache_size": (
          len(self.connection._query_cache)
          if self.connection.enable_cache
          else 0
        ),
      }

  def clear_cache(self):
    """清空缓存"""
    if self.connection.enable_cache:
      with self.connection._cache_lock:
        self.connection._query_cache.clear()
        self.connection._cache_timestamps.clear()
