"""
关系型数据库管理
"""

from .relational_base import (
  Base,
  BaseModel,
  BaseRepository,
  BulkSaveResult,
  TimestampMixin,
  WhereBuilder,
)
from .relational_connection import engine, get_async_db


async def create_tables():
  """创建表"""
  import models  # noqa: F401  # 确保所有模型注册到 Base.metadata

  async with engine.begin() as conn:
    await conn.run_sync(Base.metadata.create_all)


# 导出所有需要的组件
__all__ = [
  "Base",
  "BaseModel",
  "TimestampMixin",
  "BaseRepository",
  "BulkSaveResult",
  "WhereBuilder",
  "create_tables",
  "get_async_db",
  "engine",
]
