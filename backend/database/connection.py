"""
统一数据库连接接口
导出关系型数据库和时间序列数据库的主要连接组件
"""

# 关系型数据库连接组件
# Redis 缓存数据库组件
from .redis import RedisClient, redis_client
from .relational_base import (
  Base,
  BaseModel,
  BaseRepository,
  BulkSaveResult,
  TimestampMixin,
)
from .relational_connection import (
  AsyncSessionLocal,
  get_async_db,
)
from .relational_connection import (
  engine as relational_engine,
)

# 时间序列数据库连接组件
from .timeseries_connection import (
  ConnectionPool as TimeSeriesConnectionPool,
)
from .timeseries_connection import (
  TimeSeriesConnection,
)

# 时间序列数据库操作组件
from .timeseries_operations import TimeSeriesOperations

# 统一导出
__all__ = [
  # 关系型数据库
  "relational_engine",
  "AsyncSessionLocal",
  "get_async_db",
  "Base",
  "TimestampMixin",
  "BaseModel",
  "BaseRepository",
  "BulkSaveResult",
  # 时间序列数据库
  "TimeSeriesConnectionPool",
  "TimeSeriesConnection",
  "TimeSeriesOperations",
  # Redis 缓存数据库
  "redis_client",
  "RedisClient",
]
