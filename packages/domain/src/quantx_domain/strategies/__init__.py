"""
策略模块

提供策略基类和内置策略实现。
"""

from .ashare_dynamic_balance_dual_bucket import AshareDynamicBalanceDualBucketStrategy
from .ashare_exit_plan_replay_harness import AshareExitPlanReplayHarnessStrategy
from .ashare_intraday_t_assistant import AshareIntradayTAssistantStrategy
from .ashare_limit_up_board import AshareLimitUpBoardStrategy
from .ashare_limit_up_board_assistant import AshareLimitUpBoardAssistantStrategy
from .ashare_managed_entry_plan import AshareManagedEntryPlanStrategy
from .ashare_supermarket import AshareSupermarketStrategy
from .base import (
  ManualApprovalRecoveryCandidate,
  MarketDataContext,
  MarketDataSession,
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
  TradeIntentExecutionMode,
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
  "StrategyInput",
  "StrategyOutput",
  "TradeIntent",
  "TradeIntentDirection",
  "TradeIntentExecutionMode",
  "TradeIntentPriority",
  "TradeIntentType",
  "RuntimeStatePatch",
  "OrderStateEvent",
  "TradeExecutionEvent",
  # 策略实现
  "AshareDynamicBalanceDualBucketStrategy",
  "AshareExitPlanReplayHarnessStrategy",
  "AshareIntradayTAssistantStrategy",
  "AshareLimitUpBoardStrategy",
  "AshareLimitUpBoardAssistantStrategy",
  "AshareManagedEntryPlanStrategy",
  "AshareSupermarketStrategy",
  "PullbackGridStrategy",
]
