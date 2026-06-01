"""
交易事件类型定义

定义交易系统中的各种事件类型及其数据结构。
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from models import Order


class TradingEventType(str, Enum):
  """交易事件类型枚举 (个人量化软件专用)"""

  # 订单事件 - 4种核心事件
  ORDER_CREATED = "ORDER_CREATED"  # 订单创建 (合并 CREATED + SUBMITTED)
  ORDER_FILLED = "ORDER_FILLED"  # 订单成交 (合并 PARTIALLY_FILLED + FILLED)
  ORDER_CANCELLED = "ORDER_CANCELLED"  # 订单撤销
  ORDER_REJECTED = "ORDER_REJECTED"  # 订单被拒绝


@dataclass
class OrderEvent:
  """订单事件 (个人量化软件专用)"""

  event_type: TradingEventType  # 事件类型
  order: Order  # 订单对象
  timestamp: datetime  # 事件时间戳
  changes: Optional[str] = None  # 变更描述 (可选)

  def to_dict(self) -> Dict[str, Any]:
    """转换为字典"""
    return {
      "event_type": self.event_type.value,
      "order": self.order.to_dict(),
      "timestamp": self.timestamp.isoformat(),
      "changes": self.changes,
    }


# 类型联合,用于类型提示
TradingEventUnion = OrderEvent
