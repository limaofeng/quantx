"""
时间序列数据库管理
参考 relational.py 的设计模式
"""

from .timeseries_connection import (
  ConnectionError,
  ConnectionPool,
  InfluxDBError,
  QueryError,
  TimeSeriesConnection,
  WriteError,
  create_timeseries_connection,
  get_timeseries_connection,
  init_timeseries,
  shutdown_timeseries,
)
from .timeseries_operations import TimeSeriesOperations


def get_timeseries_operations() -> TimeSeriesOperations:
  """获取时间序列数据库操作实例"""
  connection = get_timeseries_connection()
  if connection is None:
    raise ConnectionError("时间序列数据库未初始化")
  return TimeSeriesOperations(connection)


# 导出所有需要的组件
__all__ = [
  # 核心类
  "TimeSeriesConnection",
  "ConnectionPool",
  "TimeSeriesOperations",
  # 工厂函数
  "create_timeseries_connection",
  "init_timeseries",
  "get_timeseries_connection",
  "get_timeseries_operations",
  "shutdown_timeseries",
  # 异常类
  "InfluxDBError",
  "ConnectionError",
  "QueryError",
  "WriteError",
]
