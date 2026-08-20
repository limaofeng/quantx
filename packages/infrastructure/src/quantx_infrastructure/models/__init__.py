"""
统一模型模块 - 导出所有数据库模型和枚举类型
"""

from quantx_infrastructure.database.relational_base import Base

# 枚举类型 (用于业务逻辑)
from .account import Account
from .agent_runtime import (
  AccountTradingRollout,
  AccountTradingRolloutEvent,
  AgentDevice,
  AgentEnrollmentCode,
  AgentReportInbox,
  EngineCommandOutbox,
  MarketDataRequest,
  MarketDataTransfer,
  OperationalAlert,
  PendingTradeOrder,
  RuntimeComponentHeartbeat,
  StrategyOrderCorrelation,
  StrategyRuntimeEvent,
  TradeCommandOutbox,
  TTradeBatch,
)
from .ai_assistant import (
  AiAssistantDeletionAudit,
  AiAssistantEvent,
  AiAssistantMessage,
  AiAssistantRun,
  AiAssistantSessionItem,
  AiAssistantThread,
  AiAssistantToolCall,
)
from .ai_runtime_settings import AiRuntimeSettingsAudit, AiRuntimeSettingsRecord
from .auth import (
  AuthAuditEvent,
  AuthConsumedRefreshToken,
  AuthDeviceSession,
  AuthUser,
  AuthUserAccountAccess,
)
from .auto_exit_plan import AutoExitPlanEvent, AutoExitPlanRecord
from .broker_position_snapshot import BrokerPositionSnapshot
from .closed_position_cycle import ClosedPositionCycle
from .daily_asset_snapshot import DailyAssetPositionSnapshot, DailyAssetSnapshot
from .daily_signal_definition import DailySignalDefinition
from .daily_signal_run import DailySignalRun
from .enums import (
  AccountType,
  InstrumentType,
  OrderPriceType,
  OrderStatus,
  OrderType,
  RiskLevel,
  StrategyCategory,
  StrategyStatus,
)
from .execution_metrics import ExecutionMetrics, ExecutionMetricsType
from .financial import (
  FinancialBalanceSheet,
  FinancialCapital,
  FinancialCashFlow,
  FinancialHolderNum,
  FinancialIncomeStatement,
  FinancialShareholder,
)
from .financial_metric_roe_quality import FinancialMetricRoeQuality
from .financial_metric_snapshot import FinancialMetricSnapshot
from .financial_sync_code_audit import FinancialSyncCodeAudit
from .financial_sync_run import FinancialSyncRun
from .first_board_promotion import (
  FirstBoardCandidatePreference,
  FirstBoardModelRelease,
  FirstBoardPromotionAssessmentRecord,
  LimitUpChainSnapshot,
  LimitUpLifecycleSnapshot,
  LimitUpResearchArtifact,
  LimitUpResearchJob,
)
from .holidays import Holiday
from .indicator_snapshot import IndicatorSnapshot

# 数据库模型 (统一的数据实体)
from .instrument import Instrument
from .ios_notifications import (
  IosBusinessNotificationReceipt,
  IosNotificationEvent,
  IosNotificationOutbox,
  IosPushCategoryPreference,
  IosPushRegistration,
)
from .kline import KLine
from .limit_up_board_assistant import (
  LimitUpBoardAssistantConfig,
  LimitUpBoardAssistantProjection,
  LimitUpBoardCandidateArm,
)
from .limit_up_board_replay import (
  LimitUpBoardReplayJob,
  LimitUpBoardReplayScenario,
  LimitUpBoardUniverseSnapshot,
)
from .limit_up_radar_event import LimitUpRadarEvent
from .liquidation import (
  ConditionalLiquidationOrder,
  ConditionalLiquidationSellMode,
  ConditionalLiquidationStatus,
  ConditionalLiquidationStrategy,
  LiquidationLog,
  LiquidationOrder,
  LiquidationStatus,
  LiquidationType,
  RedemptionRecord,
)
from .order import Order
from .position import Position
from .sector import Sector
from .sector_stock import SectorStock
from .stock_disclosure import (
  AnnouncementSyncRun,
  StockAnnouncement,
  StockRepurchaseEvent,
)
from .strategy import Strategy
from .strategy_backtest import StrategyBacktest
from .strategy_decision_trace_record import StrategyDecisionTraceRecord
from .strategy_grid_book_snapshot import StrategyGridBookSnapshot
from .strategy_performance_sample import StrategyPerformanceSample
from .strategy_run import StrategyRun
from .strategy_run_state import (
  StrategyRunPosition,
  StrategyRunState,
)
from .t_trade_global_config import TTradeGlobalConfig
from .t_trade_global_monitor_projection import TTradeGlobalMonitorProjection
from .t_trade_imported_entry import TTradeImportedEntry
from .t_trade_replay_projection import TTradeReplayProjection
from .table_comments import apply_table_comments
from .tick import Tick
from .trade import Trade
from .trade_confirmation_challenge import TradeConfirmationChallenge
from .trade_intent_record import TradeIntentRecord
from .watchlist_item import WatchlistItem

apply_table_comments(Base.metadata)

# 导出所有模型
__all__ = [
  # 枚举类型
  "OrderType",
  "OrderStatus",
  "AccountType",
  "OrderPriceType",
  "StrategyStatus",
  "StrategyCategory",
  "RiskLevel",
  "InstrumentType",
  # 数据库模型
  "Instrument",
  "Position",
  "BrokerPositionSnapshot",
  "ClosedPositionCycle",
  "Order",
  "Trade",
  "Strategy",
  "StrategyRun",
  "StrategyDecisionTraceRecord",
  "TradeIntentRecord",
  "TTradeGlobalConfig",
  "TTradeGlobalMonitorProjection",
  "TTradeImportedEntry",
  "TTradeReplayProjection",
  "WatchlistItem",
  "Account",
  "AutoExitPlanRecord",
  "AutoExitPlanEvent",
  "AuthUser",
  "AuthUserAccountAccess",
  "AuthDeviceSession",
  "AuthConsumedRefreshToken",
  "AuthAuditEvent",
  "IosPushRegistration",
  "IosPushCategoryPreference",
  "IosBusinessNotificationReceipt",
  "IosNotificationEvent",
  "IosNotificationOutbox",
  "TradeConfirmationChallenge",
  "AiAssistantDeletionAudit",
  "AiAssistantEvent",
  "AiAssistantMessage",
  "AiAssistantRun",
  "AiAssistantSessionItem",
  "AiAssistantThread",
  "AiAssistantToolCall",
  "AiRuntimeSettingsAudit",
  "AiRuntimeSettingsRecord",
  "AgentDevice",
  "AccountTradingRollout",
  "AccountTradingRolloutEvent",
  "AgentEnrollmentCode",
  "AgentReportInbox",
  "EngineCommandOutbox",
  "MarketDataRequest",
  "MarketDataTransfer",
  "OperationalAlert",
  "PendingTradeOrder",
  "RuntimeComponentHeartbeat",
  "StrategyOrderCorrelation",
  "StrategyRuntimeEvent",
  "TTradeBatch",
  "TradeCommandOutbox",
  "Holiday",
  "Sector",
  "SectorStock",
  "LiquidationOrder",
  "LiquidationLog",
  "RedemptionRecord",
  "LiquidationStatus",
  "LiquidationType",
  "ConditionalLiquidationOrder",
  "ConditionalLiquidationStatus",
  "ConditionalLiquidationSellMode",
  "ConditionalLiquidationStrategy",
  # 财务数据模型
  "FinancialBalanceSheet",
  "FinancialIncomeStatement",
  "FinancialCashFlow",
  "FinancialCapital",
  "FinancialHolderNum",
  "FinancialShareholder",
  "FinancialMetricSnapshot",
  "FinancialMetricRoeQuality",
  "FinancialSyncCodeAudit",
  "FinancialSyncRun",
  "FirstBoardCandidatePreference",
  "FirstBoardModelRelease",
  "FirstBoardPromotionAssessmentRecord",
  "LimitUpChainSnapshot",
  "LimitUpLifecycleSnapshot",
  "LimitUpResearchArtifact",
  "LimitUpResearchJob",
  "DailySignalDefinition",
  "DailySignalRun",
  "DailyAssetSnapshot",
  "DailyAssetPositionSnapshot",
  "IndicatorSnapshot",
  # 执行指标
  "ExecutionMetrics",
  "ExecutionMetricsType",
  # 市场数据模型
  "KLine",
  "Tick",
  "LimitUpRadarEvent",
  "LimitUpBoardAssistantConfig",
  "LimitUpBoardAssistantProjection",
  "LimitUpBoardCandidateArm",
  # 策略运行时状态
  "StrategyRunPosition",
  "LimitUpBoardReplayJob",
  "LimitUpBoardReplayScenario",
  "LimitUpBoardUniverseSnapshot",
  "StrategyRunState",
  # 回测历史
  "StrategyBacktest",
  "StrategyGridBookSnapshot",
  "StrategyPerformanceSample",
  "StockAnnouncement",
  "StockRepurchaseEvent",
  "AnnouncementSyncRun",
]
