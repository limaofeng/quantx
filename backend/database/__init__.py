"""
数据库管理模块
支持关系型数据库（PostgreSQL）和时间序列数据库（InfluxDB）
"""

# 数据库管理器
# 统一连接接口 - 提供更便捷的导入方式
from .connection import (
  AsyncSessionLocal,
  TimeSeriesConnectionPool,
  TimeSeriesOperations,
  relational_engine,
)
from .manager import DatabaseManager, db_manager

# Redis 缓存数据库组件
from .redis import RedisClient, redis_client

# 关系型数据库组件
from .relational import (
  Base,
  BaseModel,
  BaseRepository,
  BulkSaveResult,
  TimestampMixin,
  WhereBuilder,
  get_async_db,
)
from .relational import create_tables as create_relational_tables

# 时间序列数据库组件
from .timeseries import (
  TimeSeriesConnection,
  create_timeseries_connection,
  get_timeseries_connection,
  get_timeseries_operations,
  init_timeseries,
  shutdown_timeseries,
)

# 通用类型定义
from .types import Pageable, Pagination, Sort, SortDirection, SortOrder, T

__all__ = [
  # 数据库管理
  "DatabaseManager",
  "db_manager",
  # 关系型数据库
  "get_async_db",
  "create_relational_tables",
  "Base",
  "BaseModel",
  "TimestampMixin",
  "BaseRepository",
  "BulkSaveResult",
  "WhereBuilder",
  "relational_engine",
  "AsyncSessionLocal",
  # 时间序列数据库
  "TimeSeriesConnection",
  "create_timeseries_connection",
  "get_timeseries_connection",
  "get_timeseries_operations",
  "init_timeseries",
  "shutdown_timeseries",
  "TimeSeriesConnectionPool",
  "TimeSeriesOperations",
  # Redis 缓存数据库
  "redis_client",
  "RedisClient",
  # 通用类型定义
  "SortDirection",
  "SortOrder",
  "Sort",
  "Pageable",
  "Pagination",
  "T",
]
