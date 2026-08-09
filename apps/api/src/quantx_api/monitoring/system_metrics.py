"""
系统和应用指标收集器
"""

import logging
import time
from typing import Any, Dict

import psutil

from .metrics import (
  ACTIVE_CONNECTIONS,
  SUBSCRIPTION_COUNT,
  SYSTEM_CPU_USAGE,
  SYSTEM_DISK_USAGE,
  SYSTEM_MEMORY_USAGE,
)

logger = logging.getLogger(__name__)


class SystemMetrics:
  """系统指标收集器"""

  @staticmethod
  def update_system_metrics():
    """更新系统指标"""
    try:
      # CPU使用率
      cpu_percent = psutil.cpu_percent(interval=1)
      SYSTEM_CPU_USAGE.set(cpu_percent)

      # 内存使用率
      memory = psutil.virtual_memory()
      SYSTEM_MEMORY_USAGE.set(memory.percent)

      # 磁盘使用率 - 在Windows上使用C盘
      import os

      disk_path = "C:\\" if os.name == "nt" else "/"
      disk = psutil.disk_usage(disk_path)
      disk_percent = (disk.used / disk.total) * 100
      SYSTEM_DISK_USAGE.set(disk_percent)

    except Exception as e:
      logger.error(f"Failed to update system metrics: {e}")


class ApplicationMetrics:
  """应用指标管理器"""

  def __init__(self):
    self.active_subscriptions = {}

  def increment_subscription(self, subscription_type: str):
    """增加订阅计数"""
    if subscription_type not in self.active_subscriptions:
      self.active_subscriptions[subscription_type] = 0
    self.active_subscriptions[subscription_type] += 1
    SUBSCRIPTION_COUNT.labels(subscription_type=subscription_type).set(
      self.active_subscriptions[subscription_type]
    )

  def decrement_subscription(self, subscription_type: str):
    """减少订阅计数"""
    if subscription_type in self.active_subscriptions:
      self.active_subscriptions[subscription_type] = max(
        0, self.active_subscriptions[subscription_type] - 1
      )
      SUBSCRIPTION_COUNT.labels(subscription_type=subscription_type).set(
        self.active_subscriptions[subscription_type]
      )

  def set_active_connections(self, count: int):
    """设置活跃连接数"""
    ACTIVE_CONNECTIONS.set(count)

  def get_metrics_summary(self) -> Dict[str, Any]:
    """获取指标摘要"""
    try:
      memory = psutil.virtual_memory()
      cpu_percent = psutil.cpu_percent()

      return {
        "system": {
          "cpu_usage": cpu_percent,
          "memory_usage": memory.percent,
          "memory_total": memory.total,
          "memory_available": memory.available,
        },
        "application": {
          "active_subscriptions": self.active_subscriptions,
          "total_subscriptions": sum(self.active_subscriptions.values()),
        },
        "timestamp": time.time(),
      }
    except Exception as e:
      logger.error(f"Failed to get metrics summary: {e}")
      return {"error": str(e), "timestamp": time.time()}


# 全局指标实例
app_metrics = ApplicationMetrics()
