"""
时间序列数据库连接管理
参考 relational/connection.py 的设计模式
"""

import importlib
import logging
import threading
import time
import weakref
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any, Optional, Type

from config.settings import settings

logger = logging.getLogger(__name__)


class InfluxDBError(Exception):
  """InfluxDB相关错误基类"""

  pass


class ConnectionError(InfluxDBError):
  """连接错误"""

  pass


class QueryError(InfluxDBError):
  """查询错误"""

  pass


class WriteError(InfluxDBError):
  """写入错误"""

  pass


class ValidationError(InfluxDBError):
  """数据验证错误"""

  pass


_InfluxDBClient3: Optional[Type[Any]] = None
_INFLUXDB_IMPORT_ERROR = None
_INFLUXDB_IMPORT_ATTEMPTED = False


class ConnectionPool:
  """InfluxDB连接池"""

  def __init__(
    self,
    host: str,
    token: str,
    database: str,
    max_connections: int = 10,
    timeout: float = 30.0,
    pool_acquire_timeout: Optional[float] = None,
  ):
    self.host = host
    self.token = token
    self.database = database
    self.max_connections = max(1, int(max_connections or 1))
    self.timeout = timeout
    self.pool_acquire_timeout = (
      timeout if pool_acquire_timeout is None else pool_acquire_timeout
    )
    self._pool = []
    self._pool_lock = threading.Lock()
    self._pool_available = threading.Condition(self._pool_lock)
    self._in_use = weakref.WeakSet()

  def _create_client(self):
    """创建新的客户端连接"""
    if not _INFLUXDB_IMPORT_ATTEMPTED:
      self._load_influx_client()
    if _InfluxDBClient3 is None:
      raise ConnectionError(f"InfluxDB 客户端依赖导入失败: {_INFLUXDB_IMPORT_ERROR}")

    try:
      client = _InfluxDBClient3(
        host=self.host,
        token=self.token,
        database=self.database,
        timeout=self.timeout * 1000,
      )
      return client
    except Exception as e:
      raise ConnectionError(f"创建InfluxDB客户端失败: {e}")

  def _load_influx_client(self):
    """惰性导入 influxdb_client_3"""
    global _InfluxDBClient3, _INFLUXDB_IMPORT_ERROR, _INFLUXDB_IMPORT_ATTEMPTED

    if _INFLUXDB_IMPORT_ATTEMPTED:
      return

    _INFLUXDB_IMPORT_ATTEMPTED = True
    try:
      module = importlib.import_module("influxdb_client_3")
      _InfluxDBClient3 = getattr(module, "InfluxDBClient3", None)
      if _InfluxDBClient3 is None:
        raise ImportError("influxdb_client_3 未导出 InfluxDBClient3 类")
    except Exception as exc:  # pragma: no cover
      _INFLUXDB_IMPORT_ERROR = exc
      _InfluxDBClient3 = None

    if _INFLUXDB_IMPORT_ERROR is None and _InfluxDBClient3 is not None:
      _INFLUXDB_IMPORT_ERROR = None

  def get_client(self):
    """从连接池获取客户端"""
    deadline = None
    if self.pool_acquire_timeout is not None and self.pool_acquire_timeout >= 0:
      deadline = time.monotonic() + self.pool_acquire_timeout

    with self._pool_available:
      while True:
        if self._pool:
          client = self._pool.pop()
          self._in_use.add(client)
          return client

        if len(self._in_use) < self.max_connections:
          client = self._create_client()
          self._in_use.add(client)
          return client

        if deadline is None:
          self._pool_available.wait()
          continue

        remaining = deadline - time.monotonic()
        if remaining <= 0:
          raise ConnectionError("连接池已满，等待空闲连接超时")
        self._pool_available.wait(remaining)

  def return_client(self, client):
    """将客户端返回连接池"""
    with self._pool_available:
      if client in self._in_use:
        self._in_use.discard(client)
        if len(self._pool) < self.max_connections:
          self._pool.append(client)
        else:
          try:
            client.close()
          except Exception:
            pass
        self._pool_available.notify()

  def close_all(self):
    """关闭所有连接"""
    with self._pool_available:
      # 关闭池中的连接
      for client in self._pool:
        try:
          client.close()
        except Exception:
          pass
      self._pool.clear()

      # 关闭正在使用的连接
      for client in list(self._in_use):
        try:
          client.close()
        except Exception:
          pass
      self._in_use.clear()
      self._pool_available.notify_all()


class TimeSeriesConnection:
  """时间序列数据库连接管理器"""

  def __init__(
    self,
    host: str,
    token: str,
    database: str,
    ssl_verify: bool = True,
    ssl_ca_cert: str = "",
    max_connections: int = 10,
    timeout: float = 30.0,
    pool_acquire_timeout: Optional[float] = None,
    max_retries: int = 3,
    retry_delay: float = 1.0,
    enable_cache: bool = True,
    cache_ttl: int = 300,
    query_chunk_hours: int = 0,
  ):
    self.host = host
    self.token = token
    self.database = database
    self.ssl_verify = ssl_verify
    self.ssl_ca_cert = ssl_ca_cert
    self.max_retries = max_retries
    self.retry_delay = retry_delay
    self.enable_cache = enable_cache
    self.cache_ttl = cache_ttl
    self.query_chunk_hours = query_chunk_hours

    # 连接池
    self._pool = ConnectionPool(
      host,
      token,
      database,
      max_connections,
      timeout,
      pool_acquire_timeout,
    )

    # 缓存
    if enable_cache:
      self._query_cache = {}
      self._cache_timestamps = {}

    # 线程池
    self._executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="influxdb")

    # 统计信息
    self._stats = {
      "queries": 0,
      "writes": 0,
      "errors": 0,
      "cache_hits": 0,
      "cache_misses": 0,
    }

    self._last_health_check = 0
    self._health_check_interval = 60

  def __enter__(self):
    """上下文管理器入口"""
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    """上下文管理器出口"""
    self.close()

  @contextmanager
  def get_client(self):
    """获取客户端的上下文管理器"""
    client = None
    try:
      client = self._pool.get_client()
      yield client
    finally:
      if client:
        self._pool.return_client(client)

  def is_connected(self) -> bool:
    """检查是否已连接"""
    return self.health_check()

  def health_check(self) -> bool:
    """健康检查"""
    current_time = time.time()

    if current_time - self._last_health_check < self._health_check_interval:
      return True

    try:
      with self.get_client() as client:
        client.query("SELECT 1 as health_check LIMIT 1", language="sql")
        self._last_health_check = current_time
        return True
    except Exception as e:
      logger.error(f"InfluxDB健康检查失败: {e}")
      return False

  def close(self):
    """关闭管理器"""
    try:
      self._pool.close_all()
      self._executor.shutdown(wait=False)
    except Exception as e:
      logger.error(f"关闭时间序列数据库管理器失败: {e}")


# 全局时间序列连接（类似 relational.py 的 engine）
timeseries_connection: Optional[TimeSeriesConnection] = None
_manager_lock = threading.Lock()


def create_timeseries_connection(
  host: str = None, token: str = None, database: str = None, **kwargs
) -> TimeSeriesConnection:
  """创建时间序列数据库连接管理器"""
  # 使用传入参数或从配置获取
  host = host or settings.influxdb_host
  token = token or settings.influxdb_token
  database = database or settings.influxdb_database

  if not all([host, token, database]):
    raise ValueError("时间序列数据库连接参数不完整")

  return TimeSeriesConnection(
    host=host,
    token=token,
    database=database,
    ssl_verify=getattr(settings, "influxdb_ssl_verify", True),
    ssl_ca_cert=getattr(settings, "influxdb_ssl_ca_cert", ""),
    max_connections=getattr(settings, "influxdb_max_connections", 10),
    timeout=getattr(settings, "influxdb_timeout", 30.0),
    pool_acquire_timeout=getattr(settings, "influxdb_pool_acquire_timeout", None),
    max_retries=getattr(settings, "influxdb_max_retries", 3),
    retry_delay=getattr(settings, "influxdb_retry_delay", 1.0),
    enable_cache=getattr(settings, "influxdb_enable_cache", True),
    cache_ttl=getattr(settings, "influxdb_cache_ttl", 300),
    query_chunk_hours=getattr(settings, "influxdb_query_chunk_hours", 0),
    **kwargs,
  )


def init_timeseries():
  """初始化时间序列数据库连接"""
  global timeseries_connection

  with _manager_lock:
    if timeseries_connection is not None:
      return timeseries_connection

    if hasattr(settings, "influxdb_host") and settings.influxdb_host:
      try:
        timeseries_connection = create_timeseries_connection()
        logger.info(f"时间序列数据库初始化成功: {timeseries_connection.host}")
      except Exception as e:
        logger.error(f"时间序列数据库初始化失败: {e}")
        timeseries_connection = None
    else:
      logger.warning("时间序列数据库配置未找到")
      timeseries_connection = None

    return timeseries_connection


def get_timeseries_connection() -> Optional[TimeSeriesConnection]:
  """获取时间序列数据库连接管理器"""
  global timeseries_connection

  if timeseries_connection is None:
    return init_timeseries()

  return timeseries_connection


def shutdown_timeseries():
  """关闭时间序列数据库连接"""
  global timeseries_connection

  with _manager_lock:
    if timeseries_connection:
      timeseries_connection.close()
      timeseries_connection = None
