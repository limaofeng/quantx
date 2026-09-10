"""Database APIs loaded only when explicitly requested.

Importing a model or repository must not initialize service database connections.
"""

from importlib import import_module

_EXPORTS = {
  'AsyncSessionLocal': ('connection', 'AsyncSessionLocal'),
  'TimeSeriesConnectionPool': ('connection', 'TimeSeriesConnectionPool'),
  'TimeSeriesOperations': ('connection', 'TimeSeriesOperations'),
  'relational_engine': ('connection', 'relational_engine'),
  'DatabaseManager': ('manager', 'DatabaseManager'),
  'db_manager': ('manager', 'db_manager'),
  'RedisClient': ('redis', 'RedisClient'),
  'redis_client': ('redis', 'redis_client'),
  'Base': ('relational_base', 'Base'),
  'BaseModel': ('relational_base', 'BaseModel'),
  'BaseRepository': ('relational_base', 'BaseRepository'),
  'BulkSaveResult': ('relational_base', 'BulkSaveResult'),
  'TimestampMixin': ('relational_base', 'TimestampMixin'),
  'WhereBuilder': ('relational_base', 'WhereBuilder'),
  'get_async_db': ('relational', 'get_async_db'),
  'create_relational_tables': ('relational', 'create_tables'),
  'TimeSeriesConnection': ('timeseries', 'TimeSeriesConnection'),
  'create_timeseries_connection': ('timeseries', 'create_timeseries_connection'),
  'get_timeseries_connection': ('timeseries', 'get_timeseries_connection'),
  'get_timeseries_operations': ('timeseries', 'get_timeseries_operations'),
  'init_timeseries': ('timeseries', 'init_timeseries'),
  'shutdown_timeseries': ('timeseries', 'shutdown_timeseries'),
  'Pageable': ('types', 'Pageable'),
  'Pagination': ('types', 'Pagination'),
  'Sort': ('types', 'Sort'),
  'SortDirection': ('types', 'SortDirection'),
  'SortOrder': ('types', 'SortOrder'),
  'T': ('types', 'T'),
}

__all__ = list(_EXPORTS)


def __getattr__(name):
  if name not in _EXPORTS:
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
  module, attribute = _EXPORTS[name]
  value = getattr(import_module(f".{module}", __name__), attribute)
  globals()[name] = value
  return value
