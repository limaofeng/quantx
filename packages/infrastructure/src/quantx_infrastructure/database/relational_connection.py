"""
数据库连接配置
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from quantx_infrastructure.config.settings import settings

logger = logging.getLogger(__name__)

# This service is PostgreSQL-only. Production validation also rejects SQLite
# before any process starts.
if "asyncpg" not in settings.database_url:
  raise ImportError("该配置仅支持 asyncpg 异步数据库连接")

# 异步引擎和会话
engine = create_async_engine(settings.database_url, echo=settings.database_echo)
AsyncSessionLocal = async_sessionmaker(
  bind=engine, class_=AsyncSession, expire_on_commit=False
)


async def get_async_db():
  """获取异步数据库会话"""
  if AsyncSessionLocal is None:
    raise RuntimeError("数据库会话未正确配置")
  async with AsyncSessionLocal() as db:
    yield db


async def close_database():
  """关闭当前数据库连接池，保留可重启的 Engine 与会话工厂。"""
  if engine is not None:
    await engine.dispose()
    logger.info("关系型数据库连接已关闭")
