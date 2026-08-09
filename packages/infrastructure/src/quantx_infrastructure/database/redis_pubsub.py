"""
Redis Pub/Sub 异步客户端

用于跨进程事件广播和可丢失的消费者唤醒；持久化状态仍以数据库为准。
"""

import json
import logging
from typing import Any, AsyncIterator, Dict, Optional

import redis.asyncio as aioredis

from quantx_infrastructure.config.settings import settings

logger = logging.getLogger(__name__)


class RedisChannelSubscription:
    """An explicitly opened subscription so publishers cannot race setup."""

    def __init__(self, redis, pubsub, channel: str):
        self.redis = redis
        self.pubsub = pubsub
        self.channel = channel

    async def messages(self) -> AsyncIterator[Dict[str, Any]]:
        async for message in self.pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                yield json.loads(message["data"])
            except json.JSONDecodeError as exc:
                logger.warning("Failed to decode message on %s: %s", self.channel, exc)

    async def wait_for_message(
        self,
        *,
        timeout: float = 1.0,
    ) -> Optional[Dict[str, Any]]:
        """Wait briefly for one notification without treating Redis as a queue."""
        message = await self.pubsub.get_message(
            ignore_subscribe_messages=True,
            timeout=timeout,
        )
        if message is None:
            return None
        try:
            return json.loads(message["data"])
        except json.JSONDecodeError as exc:
            logger.warning("Failed to decode message on %s: %s", self.channel, exc)
            return None

    async def close(self) -> None:
        await self.pubsub.unsubscribe(self.channel)
        await self.pubsub.close()
        await self.redis.close()


class RedisPubSub:
    """Redis Pub/Sub 异步客户端"""

    _instance = None
    _redis: Optional[aioredis.Redis] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def get_redis(self) -> aioredis.Redis:
        """获取异步 Redis 连接"""
        if self._redis is None:
            self._redis = aioredis.Redis(
                host=getattr(settings, "redis_host", "localhost"),
                port=getattr(settings, "redis_port", 6379),
                db=getattr(settings, "redis_db", 0),
                password=getattr(settings, "redis_password", "") or None,
                decode_responses=True,
            )
        return self._redis

    async def publish(self, channel: str, message: Dict[str, Any]) -> int:
        """
        发布消息到频道

        Args:
            channel: 频道名称
            message: 消息内容（会被 JSON 序列化）

        Returns:
            收到消息的订阅者数量
        """
        redis = await self.get_redis()
        payload = json.dumps(message, ensure_ascii=False, default=str)
        count = await redis.publish(channel, payload)
        logger.debug(f"Published to {channel}: {payload[:100]}... (receivers: {count})")
        return count

    async def subscribe(self, channel: str) -> AsyncIterator[Dict[str, Any]]:
        """
        订阅频道并异步迭代消息
        
        注意：每个订阅者需要独立的 Redis 连接，
        因为订阅模式下连接只能接收消息，不能执行其他命令。

        Args:
            channel: 频道名称

        Yields:
            解析后的消息字典
        """
        # 为订阅者创建独立的 Redis 连接
        subscriber_redis = aioredis.Redis(
            host=getattr(settings, "redis_host", "localhost"),
            port=getattr(settings, "redis_port", 6379),
            db=getattr(settings, "redis_db", 0),
            password=getattr(settings, "redis_password", "") or None,
            decode_responses=True,
        )
        pubsub = subscriber_redis.pubsub()
        await pubsub.subscribe(channel)
        logger.info(f"Subscribed to channel: {channel}")

        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        logger.info(f"Received message on {channel}: {data.get('status', 'unknown')}")
                        yield data
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to decode message: {e}")
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
            await subscriber_redis.close()
            logger.info(f"Unsubscribed from channel: {channel}")

    async def open_subscription(self, channel: str) -> RedisChannelSubscription:
        subscriber_redis = aioredis.Redis(
            host=getattr(settings, "redis_host", "localhost"),
            port=getattr(settings, "redis_port", 6379),
            db=getattr(settings, "redis_db", 0),
            password=getattr(settings, "redis_password", "") or None,
            decode_responses=True,
        )
        pubsub = subscriber_redis.pubsub()
        await pubsub.subscribe(channel)
        return RedisChannelSubscription(subscriber_redis, pubsub, channel)

    async def close(self):
        """关闭 Redis 连接"""
        if self._redis:
            await self._redis.close()
            self._redis = None


# 全局实例
redis_pubsub = RedisPubSub()


# 频道名称常量
CHANNEL_DEPLOYMENT_STATUS = "deployment:status"
AGENT_REPORT_WAKE_CHANNEL = "agent-report:wakeup"


def get_deployment_channel(deployment_name: str) -> str:
    """获取特定部署的频道名称"""
    return f"deployment:{deployment_name}"


__all__ = [
    "redis_pubsub",
    "RedisPubSub",
    "RedisChannelSubscription",
    "CHANNEL_DEPLOYMENT_STATUS",
    "AGENT_REPORT_WAKE_CHANNEL",
    "get_deployment_channel",
]
