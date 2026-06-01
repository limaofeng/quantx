"""
部署状态事件广播器

使用 asyncio.Queue 实现内存级别的事件广播，支持多订阅者
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Set
from weakref import WeakSet

logger = logging.getLogger(__name__)


@dataclass
class DeploymentStatusEvent:
    """部署状态变更事件"""
    deployment_name: str
    status: str  # Running, Completed, Failed, Cancelled, Crashed
    flow_run_id: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    message: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


class DeploymentEventBroadcaster:
    """
    部署事件广播器
    
    支持多个订阅者同时监听同一个部署的状态变化
    """
    
    _instance: Optional['DeploymentEventBroadcaster'] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        
        # 每个 deployment_name 对应一组订阅者队列
        self._subscribers: Dict[str, Set[asyncio.Queue]] = {}
        self._lock = asyncio.Lock()
        logger.info("DeploymentEventBroadcaster 初始化完成")
    
    async def subscribe(self, deployment_name: str) -> asyncio.Queue:
        """
        订阅指定部署的状态变更
        
        Returns:
            asyncio.Queue: 用于接收事件的队列
        """
        queue: asyncio.Queue = asyncio.Queue()
        
        async with self._lock:
            if deployment_name not in self._subscribers:
                self._subscribers[deployment_name] = set()
            self._subscribers[deployment_name].add(queue)
            logger.debug(f"新订阅者加入: {deployment_name}, 当前订阅者数: {len(self._subscribers[deployment_name])}")
        
        return queue
    
    async def unsubscribe(self, deployment_name: str, queue: asyncio.Queue):
        """取消订阅"""
        async with self._lock:
            if deployment_name in self._subscribers:
                self._subscribers[deployment_name].discard(queue)
                if not self._subscribers[deployment_name]:
                    del self._subscribers[deployment_name]
                logger.debug(f"订阅者退出: {deployment_name}")
    
    async def broadcast(self, event: DeploymentStatusEvent):
        """
        广播事件给所有订阅该部署的订阅者
        """
        deployment_name = event.deployment_name
        
        async with self._lock:
            subscribers = self._subscribers.get(deployment_name, set()).copy()
        
        if not subscribers:
            logger.debug(f"没有订阅者监听 {deployment_name}")
            return
        
        logger.info(f"广播事件: {deployment_name} -> {event.status}, 订阅者数: {len(subscribers)}")
        
        for queue in subscribers:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(f"队列已满，丢弃事件: {deployment_name}")


# 全局单例
deployment_event_broadcaster = DeploymentEventBroadcaster()
