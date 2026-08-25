"""PostgreSQL async engine with one bounded pool per service process."""

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from quantx_infrastructure.config.settings import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DatabasePoolProfile:
  role: str
  pool_size: int
  max_overflow: int
  statement_timeout_ms: int | None = None

  @property
  def maximum_connections(self) -> int:
    return self.pool_size + self.max_overflow


_DATABASE_POOL_PROFILES = {
  "api": DatabasePoolProfile("api", pool_size=8, max_overflow=4, statement_timeout_ms=15_000),
  "market-gateway": DatabasePoolProfile(
    "market-gateway", pool_size=1, max_overflow=1, statement_timeout_ms=15_000
  ),
  "engine": DatabasePoolProfile("engine", pool_size=4, max_overflow=2),
  "worker": DatabasePoolProfile("worker", pool_size=4, max_overflow=2),
  "ai-runtime": DatabasePoolProfile("ai-runtime", pool_size=2, max_overflow=1),
  "tooling": DatabasePoolProfile("tooling", pool_size=2, max_overflow=1),
}


def database_pool_profile(
  role: str,
  *,
  pool_size: int | None = None,
  max_overflow: int | None = None,
) -> DatabasePoolProfile:
  normalized = str(role or "tooling").strip().lower()
  base = _DATABASE_POOL_PROFILES.get(normalized, _DATABASE_POOL_PROFILES["tooling"])
  return DatabasePoolProfile(
    role=base.role,
    pool_size=pool_size if pool_size is not None else base.pool_size,
    max_overflow=max_overflow if max_overflow is not None else base.max_overflow,
    statement_timeout_ms=base.statement_timeout_ms,
  )

# This service is PostgreSQL-only. Production validation also rejects SQLite
# before any process starts.
if "asyncpg" not in settings.database_url:
  raise ImportError("该配置仅支持 asyncpg 异步数据库连接")

pool_profile = database_pool_profile(
  settings.database_process_role,
  pool_size=settings.database_pool_size,
  max_overflow=settings.database_max_overflow,
)
server_settings = {
  "application_name": f"quantx-{pool_profile.role}",
  "idle_in_transaction_session_timeout": "30000",
  "lock_timeout": "3000",
}
if pool_profile.statement_timeout_ms is not None:
  server_settings["statement_timeout"] = str(pool_profile.statement_timeout_ms)

# Each operating-system process owns exactly one SQLAlchemy pool. Separate
# processes cannot safely share Python connections, while business modules in
# one process all reuse this engine/session factory.
engine = create_async_engine(
  settings.database_url,
  echo=settings.database_echo,
  pool_size=pool_profile.pool_size,
  max_overflow=pool_profile.max_overflow,
  pool_timeout=settings.database_pool_timeout_seconds,
  pool_recycle=settings.database_pool_recycle_seconds,
  pool_pre_ping=True,
  pool_use_lifo=True,
  connect_args={"server_settings": server_settings},
)
AsyncSessionLocal = async_sessionmaker(
  bind=engine, class_=AsyncSession, expire_on_commit=False
)

logger.info(
  "PostgreSQL pool configured: role=%s pool_size=%s max_overflow=%s maximum=%s timeout=%.1fs",
  pool_profile.role,
  pool_profile.pool_size,
  pool_profile.max_overflow,
  pool_profile.maximum_connections,
  settings.database_pool_timeout_seconds,
)


def database_pool_snapshot() -> dict[str, int | str]:
  """Return a low-cardinality, process-local pool snapshot for metrics."""
  pool = engine.sync_engine.pool
  return {
    "role": pool_profile.role,
    "size": int(pool.size()),
    "checked_in": int(pool.checkedin()),
    "checked_out": int(pool.checkedout()),
    "overflow": max(0, int(pool.overflow())),
    "maximum": pool_profile.maximum_connections,
  }


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
