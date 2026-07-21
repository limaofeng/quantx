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


def _ensure_compat_columns(connection):
  """Apply the small additive migrations required by create-all deployments."""
  from sqlalchemy import inspect, text

  inspector = inspect(connection)
  if "t_trade_global_configs" not in inspector.get_table_names():
    return
  columns = {
    column["name"] for column in inspector.get_columns("t_trade_global_configs")
  }
  if "strategy_run_id" not in columns:
    connection.execute(
      text("ALTER TABLE t_trade_global_configs ADD COLUMN strategy_run_id VARCHAR(36)")
    )
  if "universe_revision" not in columns:
    connection.execute(
      text(
        "ALTER TABLE t_trade_global_configs "
        "ADD COLUMN universe_revision INTEGER NOT NULL DEFAULT 0"
      )
    )
  if "strategies" in inspector.get_table_names():
    strategy_columns = {
      column["name"] for column in inspector.get_columns("strategies")
    }
    if "instrument_universe_mode" not in strategy_columns:
      connection.execute(
        text(
          "ALTER TABLE strategies ADD COLUMN "
          "instrument_universe_mode VARCHAR(32) NOT NULL DEFAULT 'STATIC'"
        )
      )


async def create_tables():
  """创建表"""
  import models  # noqa: F401  # 确保所有模型注册到 Base.metadata

  async with engine.begin() as conn:
    await conn.run_sync(Base.metadata.create_all)
    await conn.run_sync(_ensure_compat_columns)


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
