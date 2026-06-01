"""
交易事件系统

提供统一的事件发布订阅机制,用于订单、成交、持仓、账户变动的实时推送。
"""

from .trading_event_manager import TradingEventManager, trading_event_manager
from .types import (
  OrderEvent,
  TradingEventType,
)

__all__ = [
  "TradingEventManager",
  "trading_event_manager",
  "TradingEventType",
  "OrderEvent",
]
