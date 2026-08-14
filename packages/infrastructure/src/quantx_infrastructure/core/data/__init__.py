"""
数据适配器模块 - 统一历史和实时数据接口
"""

from .adapter import DataAdapter, DataMode
from .adapter_manager import AdapterManager, adapter_manager
from .historical import HistoricalDataAdapter
from .market_data_service import MarketDataService, market_data_service
from .realtime import RealtimeDataAdapter
from .unified_subscription_manager import (
  UnifiedDataSubscriptionManager,
  unified_subscription_manager,
)

__all__ = [
  "DataAdapter",
  "DataMode",
  "HistoricalDataAdapter",
  "RealtimeDataAdapter",
  "UnifiedDataSubscriptionManager",
  "unified_subscription_manager",
  "MarketDataService",
  "market_data_service",
  "AdapterManager",
  "adapter_manager",
]
