"""
策略模块

提供策略基类和内置策略实现。
"""

from .base import (
  OrderStateEvent,
  RuntimeStatePatch,
  StrategyBase,
  StrategyCadence,
  StrategyContext,
  StrategyInput,
  StrategyOutput,
  StrategyRunMode,
  TradeExecutionEvent,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentPriority,
  TradeIntentType,
)
from .ashare_dynamic_balance_dual_bucket import AshareDynamicBalanceDualBucketStrategy
from .ashare_supermarket import AshareSupermarketStrategy
from .pullback_grid import PullbackGridStrategy

__all__ = [
  # 基础类
  "StrategyBase",
  "StrategyContext",
  "StrategyRunMode",
  "StrategyCadence",
  "StrategyInput",
  "StrategyOutput",
  "TradeIntent",
  "TradeIntentDirection",
  "TradeIntentPriority",
  "TradeIntentType",
  "RuntimeStatePatch",
  "OrderStateEvent",
  "TradeExecutionEvent",
  # 策略实现
  "AshareDynamicBalanceDualBucketStrategy",
  "AshareSupermarketStrategy",
  "PullbackGridStrategy",
]
