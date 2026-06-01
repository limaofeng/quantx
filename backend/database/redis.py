"""
Redis 缓存数据库简单封装
避免每次使用时重复实例化
"""

import json
from typing import Any, Optional

import redis

from config.settings import settings


class RedisClient:
  """Redis 客户端简单封装"""

  _instance = None
  _client = None

  def __new__(cls):
    if cls._instance is None:
      cls._instance = super().__new__(cls)
    return cls._instance

  def __init__(self):
    if self._client is None:
      # 从配置获取 Redis 设置
      redis_config = {
        "host": getattr(settings, "redis_host", "localhost"),
        "port": getattr(settings, "redis_port", 6379),
        "db": getattr(settings, "redis_db", 0),
        "password": getattr(settings, "redis_password", ""),
        "max_connections": getattr(settings, "redis_max_connections", 10),
        "socket_timeout": getattr(settings, "redis_socket_timeout", None),
        "socket_connect_timeout": getattr(
          settings, "redis_socket_connect_timeout", None
        ),
      }
      self._client = redis.Redis(
        host=redis_config["host"],
        port=redis_config["port"],
        db=redis_config["db"],
        password=redis_config["password"] or None,  # 空字符串转换为 None
        decode_responses=True,
      )

  @property
  def client(self):
    """获取 Redis 客户端"""
    return self._client

  def set(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
    """设置键值对"""
    if isinstance(value, (dict, list)):
      value = json.dumps(value)
    return self._client.set(key, value, ex=ex)

  def get(self, key: str) -> Optional[Any]:
    """获取键值"""
    value = self._client.get(key)
    if value is None:
      return None
    try:
      return json.loads(value)
    except (json.JSONDecodeError, TypeError):
      return value

  def delete(self, *keys: str) -> int:
    """删除键"""
    return self._client.delete(*keys)

  def exists(self, *keys: str) -> int:
    """检查键是否存在"""
    return self._client.exists(*keys)

  def expire(self, key: str, time: int) -> bool:
    """设置键过期时间"""
    return self._client.expire(key, time)

  def ttl(self, key: str) -> int:
    """获取键剩余过期时间"""
    return self._client.ttl(key)


# 全局 Redis 实例
redis_client = RedisClient()


__all__ = ["redis_client", "RedisClient"]
