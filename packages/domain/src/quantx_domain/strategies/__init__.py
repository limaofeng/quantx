"""
策略模块

提供策略基类和内置策略实现。
"""

from .ashare_dynamic_balance_dual_bucket import AshareDynamicBalanceDualBucketStrategy
from .ashare_intraday_t_assistant import AshareIntradayTAssistantStrategy
from .ashare_limit_up_board import AshareLimitUpBoardStrategy
from .ashare_limit_up_board_assistant import AshareLimitUpBoardAssistantStrategy
from .ashare_managed_entry_plan import AshareManagedEntryPlanStrategy
from .ashare_managed_exit_plan import AshareManagedExitPlanStrategy
from .ashare_supermarket import AshareSupermarketStrategy
from .base import (
  ManualApprovalRecoveryCandidate,
  ManualCommandIntentOrigin,
  MarketDataContext,
  MarketDataSession,
  OrderStateEvent,
  RuntimeStatePatch,
  StrategyBase,
  StrategyCadence,
  StrategyContext,
  StrategyInput,
  StrategyOutput,
  StrategyRunIntentOrigin,
  StrategyRunMode,
  TradeExecutionEvent,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentExecutionMode,
  TradeIntentOrigin,
  TradeIntentOriginType,
  TradeIntentPriority,
  TradeIntentType,
)
from .pullback_grid import PullbackGridStrategy

__all__ = [
  # 基础类
  "StrategyBase",
  "StrategyContext",
  "StrategyRunMode",
  "StrategyCadence",
  "MarketDataContext",
  "MarketDataSession",
  "ManualApprovalRecoveryCandidate",
  "ManualCommandIntentOrigin",
  "StrategyInput",
  "StrategyOutput",
  "StrategyRunIntentOrigin",
  "TradeIntent",
  "TradeIntentDirection",
  "TradeIntentExecutionMode",
  "TradeIntentOrigin",
  "TradeIntentOriginType",
  "TradeIntentPriority",
  "TradeIntentType",
  "RuntimeStatePatch",
  "OrderStateEvent",
  "TradeExecutionEvent",
  # 策略实现
  "AshareDynamicBalanceDualBucketStrategy",
  "AshareIntradayTAssistantStrategy",
  "AshareLimitUpBoardStrategy",
  "AshareLimitUpBoardAssistantStrategy",
  "AshareManagedEntryPlanStrategy",
  "AshareManagedExitPlanStrategy",
  "AshareSupermarketStrategy",
  "PullbackGridStrategy",
]
