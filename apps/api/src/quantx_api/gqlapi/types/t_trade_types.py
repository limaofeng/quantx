"""GraphQL types for the existing-position intraday T assistant."""

from dataclasses import field
from datetime import datetime
from enum import Enum
from typing import List, Optional

import strawberry
from strawberry.scalars import JSON

from .common_types import PageInfo


@strawberry.enum(description="T 批次时间退出模式")
class TTradeTimeExitMode(Enum):
  UNLIMITED = "UNLIMITED"
  END_OF_DAY = "END_OF_DAY"
  MAX_HOLDING_DAYS = "MAX_HOLDING_DAYS"


@strawberry.input(description="启动持仓做 T 会话")
class TTradeStartInput:
  account_id: str
  stock_code: str
  mode: str = "live"
  auto_exit_acknowledged: bool = False
  target_trade_amount: float = 10_000.0
  max_trade_amount: float = 12_000.0
  max_concurrent_batches: int = 3
  max_total_t_exposure_pct: float = 0.1
  signal_lookback_seconds: int = 300
  stabilization_seconds: int = 15
  pullback_threshold_pct: float = 0.8
  rebound_threshold_pct: float = 0.2
  max_spread_ticks: int = 3
  approval_ttl_seconds: int = 30
  max_price_deviation_pct: float = 0.3
  target_profit_pct: float = 2.0
  base_floor_pct: float = 0.5
  initial_gap_pct: float = 1.5
  trailing_gap_slope: float = 0.25
  max_gap_pct: float = 3.0
  hard_stop_enabled: bool = False
  hard_stop_pct: float = -0.8
  time_exit_mode: TTradeTimeExitMode = TTradeTimeExitMode.UNLIMITED
  time_exit_time: str = "14:50"
  max_holding_trading_days: int = 5
  cooldown_seconds: int = 300


@strawberry.input(description="导入外部已成交的做 T 买入批次")
class TTradeExternalEntryInput:
  run_id: str
  account_id: str
  order_id: str


@strawberry.type(description="已纳入做 T 自动退出的来源成交")
class TTradeImportedEntry:
  source_trade_id: str
  source_order_id: Optional[str]
  stock_code: str
  volume: int
  price: float
  status: str
  source_trade_time: Optional[datetime]
  strategy_run_id: str
  batch_id: str


@strawberry.type(description="做 T 买入确认信号历史")
class TTradeSignalHistoryEntry:
  intent_id: str
  run_id: str
  stock_code: str
  status: str
  status_reason: str
  signal_price: float
  pullback_pct: float
  rebound_pct: float
  requested_volume: int
  created_at: Optional[datetime]
  expires_at: Optional[datetime]
  updated_at: Optional[datetime]


@strawberry.type(description="持仓做 T 会话")
class TTradeSession:
  run_id: str
  account_id: str
  stock_code: str
  mode: str
  run_status: str
  status: str
  position_shares: int
  position_available_shares: int
  target_trade_amount: float
  max_trade_amount: float
  planned_entry_volume: int
  target_profit_pct: float
  base_floor_pct: float
  hard_stop_enabled: bool
  hard_stop_pct: float
  time_exit_mode: TTradeTimeExitMode
  time_exit_time: str
  max_holding_trading_days: int
  current_signal: JSON
  pending_entry_intent_id: Optional[str]
  pending_exit_intent_id: Optional[str]
  entry_order_status: str
  exit_order_status: str
  entry_filled_volume: int
  entry_avg_price: float
  exit_filled_volume: int
  exit_avg_price: float
  active_volume: int
  last_price: float
  last_net_profit_pct: float
  peak_net_profit_pct: float
  trailing_floor_pct: Optional[float]
  profit_armed: bool
  last_exit_reason: str
  completed_cycles: int
  latest_intent: Optional[JSON]
  can_cancel: bool
  error_message: Optional[str]
  created_at: Optional[datetime]
  updated_at: Optional[datetime]
  global_monitor_id: Optional[str] = None
  global_config_version: int = 0


@strawberry.type(description="持仓做 T 操作结果")
class TTradeMutationResult:
  success: bool
  code: str
  message: str
  session: Optional[TTradeSession] = None


@strawberry.input(description="保存全局持仓做 T 监控设置")
class TTradeGlobalSettingsInput:
  account_id: str
  enabled: bool = False
  mode: str = "paper"
  auto_exit_acknowledged: bool = False
  ignored_stock_codes: List[str] = field(default_factory=list)
  target_trade_amount: float = 10_000.0
  max_trade_amount: float = 12_000.0
  max_concurrent_batches: int = 3
  max_total_t_exposure_pct: float = 0.1
  signal_lookback_seconds: int = 300
  stabilization_seconds: int = 15
  pullback_threshold_pct: float = 0.8
  rebound_threshold_pct: float = 0.2
  max_spread_ticks: int = 3
  approval_ttl_seconds: int = 30
  max_price_deviation_pct: float = 0.3
  target_profit_pct: float = 2.0
  base_floor_pct: float = 0.5
  initial_gap_pct: float = 1.5
  trailing_gap_slope: float = 0.25
  max_gap_pct: float = 3.0
  hard_stop_enabled: bool = False
  hard_stop_pct: float = -0.8
  time_exit_mode: TTradeTimeExitMode = TTradeTimeExitMode.UNLIMITED
  time_exit_time: str = "14:50"
  max_holding_trading_days: int = 5
  cooldown_seconds: int = 300


@strawberry.type(description="全局做 T 监控中的单只持仓")
class TTradeGlobalHolding:
  stock_code: str
  instrument_name: str
  volume: int
  available_volume: int
  ignored: bool
  eligible: bool
  status: str
  reason: str
  session: Optional[TTradeSession] = None


@strawberry.type(description="做 T 生产就绪检查项")
class TTradeReadinessCheck:
  code: str
  passed: bool
  message: str


@strawberry.type(description="做 T 生产就绪与灰度状态")
class TTradeLiveReadiness:
  account_id: str
  ready: bool
  stage: str
  engine_status: str
  agent_status: str
  agent_device_id: Optional[str]
  agent_mode: str
  protocol_version: str
  reconcile_status: str
  kill_switch: bool
  policy_version: int
  can_approve: bool
  can_activate_live: bool
  blocked_reasons: List[str]
  checks: List[TTradeReadinessCheck]
  snapshot_id: Optional[str]
  snapshot_hash: Optional[str]
  snapshot_at: Optional[datetime]
  reconciliation_age_seconds: Optional[float]
  queued_command_count: int
  queue_delay_seconds: float
  dead_letter_count: int
  unresolved_critical_alert_count: int
  journal_integrity: str
  journal_size_bytes: int
  journal_pending_reports: int
  last_backup_at: Optional[datetime]
  checked_at: datetime


@strawberry.type(description="可确认并闭环的持久化运行告警")
class OperationalAlert:
  id: str
  severity: str
  source: str
  code: str
  account_id: Optional[str]
  business_id: Optional[str]
  message: str
  details: JSON
  status: str
  occurrences: int
  first_seen_at: datetime
  last_seen_at: datetime
  acknowledged_by: Optional[str]
  acknowledged_at: Optional[datetime]
  resolved_by: Optional[str]
  resolved_at: Optional[datetime]
  resolution: Optional[str]


@strawberry.type(description="持久化做 T 批次")
class TTradeBatch:
  batch_id: str
  account_id: str
  stock_code: str
  strategy_run_id: str
  status: str
  entry_intent_id: Optional[str]
  exit_intent_id: Optional[str]
  entry_client_order_id: Optional[str]
  exit_client_order_id: Optional[str]
  entry_broker_order_id: Optional[str]
  exit_broker_order_id: Optional[str]
  target_volume: int
  entry_filled_volume: int
  entry_avg_price: float
  exit_filled_volume: int
  exit_avg_price: float
  active_volume: int
  last_price: float
  last_net_profit_pct: float
  peak_net_profit_pct: float
  trailing_floor_pct: Optional[float]
  exit_reason: Optional[str]
  exception_reason: Optional[str]
  policy_version: int
  version: int
  created_at: Optional[datetime]
  updated_at: Optional[datetime]


@strawberry.type(description="做 T 批次委托与成交事件")
class TTradeBatchEvent:
  event_id: str
  batch_id: str
  event_type: str
  status: str
  client_order_id: str
  broker_order_id: Optional[str]
  payload: JSON
  created_at: datetime
  applied_at: Optional[datetime]
  error: Optional[str]


@strawberry.type(description="做 T 批次游标分页")
class TTradeBatchPage:
  items: List[TTradeBatch]
  page_info: PageInfo


@strawberry.type(description="做 T 委托与成交事件游标分页")
class TTradeBatchEventPage:
  items: List[TTradeBatchEvent]
  page_info: PageInfo


@strawberry.type(description="做 T 信号历史游标分页")
class TTradeSignalHistoryPage:
  items: List[TTradeSignalHistoryEntry]
  page_info: PageInfo


@strawberry.type(description="做 T 生产操作结果")
class TTradeOperationsMutationResult:
  success: bool
  code: str
  message: str
  readiness: Optional[TTradeLiveReadiness] = None


@strawberry.type(description="账户级全局持仓做 T 监控")
class TTradeGlobalMonitor:
  config_id: Optional[str]
  strategy_run_id: Optional[str]
  universe_revision: int
  account_id: str
  enabled: bool
  mode: str
  auto_exit_acknowledged: bool
  ignored_stock_codes: List[str]
  config_version: int
  target_trade_amount: float
  max_trade_amount: float
  max_concurrent_batches: int
  max_total_t_exposure_pct: float
  signal_lookback_seconds: int
  stabilization_seconds: int
  pullback_threshold_pct: float
  rebound_threshold_pct: float
  max_spread_ticks: int
  approval_ttl_seconds: int
  max_price_deviation_pct: float
  target_profit_pct: float
  base_floor_pct: float
  initial_gap_pct: float
  trailing_gap_slope: float
  max_gap_pct: float
  hard_stop_enabled: bool
  hard_stop_pct: float
  time_exit_mode: TTradeTimeExitMode
  time_exit_time: str
  max_holding_trading_days: int
  cooldown_seconds: int
  holding_count: int
  eligible_count: int
  ignored_count: int
  monitored_count: int
  pending_signal_count: int
  active_batch_count: int
  draining_count: int
  holdings: List[TTradeGlobalHolding]
  sessions: List[TTradeSession]
  position_snapshot_source: Optional[str]
  position_snapshot_sequence: str
  position_snapshot_reported_at: Optional[datetime]
  position_snapshot_received_at: Optional[datetime]
  position_snapshot_complete: bool
  position_snapshot_error: Optional[str]
  last_reconciled_at: Optional[datetime]
  last_error: Optional[str]
  created_at: Optional[datetime]
  updated_at: Optional[datetime]
  rollout_stage: str = "SHADOW"
  engine_status: str = "OFFLINE"
  agent_status: str = "OFFLINE"
  reconcile_status: str = "UNKNOWN"
  kill_switch: bool = False
  can_approve: bool = False
  can_activate_live: bool = False
  blocked_reasons: List[str] = field(default_factory=list)
  projection_version: str = "0"
  projection_generated_at: Optional[datetime] = None
  readiness: Optional[TTradeLiveReadiness] = None


@strawberry.type(description="做 T 监控读投影更新通知")
class TTradeUpdateNotice:
  account_id: str
  version: str
  occurred_at: datetime


@strawberry.type(description="全局做 T 监控操作结果")
class TTradeGlobalMutationResult:
  success: bool
  code: str
  message: str
  monitor: Optional[TTradeGlobalMonitor] = None


@strawberry.input(description="做 T 历史回放的手工初始持仓")
class TTradeReplayPositionInput:
  stock_code: str
  volume: int
  available_volume: int
  instrument_name: str = ""
  avg_price: float = 0.0
  last_price: float = 0.0
  market_value: float = 0.0


@strawberry.input(description="启动做 T 历史回放")
class TTradeReplayStartInput:
  account_id: str
  start_time: datetime
  end_time: datetime
  initial_cash: Optional[float] = None
  initial_total_asset: Optional[float] = None
  initial_positions: List[TTradeReplayPositionInput] = field(default_factory=list)
  target_trade_amount: float = 10_000.0
  max_trade_amount: float = 12_000.0
  max_concurrent_batches: int = 3
  max_total_t_exposure_pct: float = 0.1
  signal_lookback_seconds: int = 300
  stabilization_seconds: int = 15
  pullback_threshold_pct: float = 0.8
  rebound_threshold_pct: float = 0.2
  max_spread_ticks: int = 3
  approval_ttl_seconds: int = 30
  max_price_deviation_pct: float = 0.3
  target_profit_pct: float = 2.0
  base_floor_pct: float = 0.5
  initial_gap_pct: float = 1.5
  trailing_gap_slope: float = 0.25
  max_gap_pct: float = 3.0
  hard_stop_enabled: bool = False
  hard_stop_pct: float = -0.8
  time_exit_mode: TTradeTimeExitMode = TTradeTimeExitMode.UNLIMITED
  time_exit_time: str = "14:50"
  max_holding_trading_days: int = 5
  cooldown_seconds: int = 300
  commission_rate: float = 0.0003
  minimum_commission: float = 5.0
  stamp_tax_rate: float = 0.0005
  transfer_fee_rate: float = 0.00001
  slippage_rate: float = 0.0001


@strawberry.type(description="历史回放初始持仓")
class TTradeReplayPosition:
  stock_code: str
  instrument_name: str
  volume: int
  available_volume: int
  avg_price: float
  last_price: float
  market_value: float


@strawberry.type(description="做 T 历史回放准备信息")
class TTradeReplayPreparation:
  account_id: str
  start_time: datetime
  snapshot_id: Optional[str]
  snapshot_date: Optional[str]
  snapshot_source: Optional[str]
  initial_cash: float
  initial_total_asset: float
  requires_manual_portfolio: bool
  message: str
  positions: List[TTradeReplayPosition]


@strawberry.type(description="做 T 回放收益摘要")
class TTradeReplaySummary:
  initial_equity: float
  final_equity: float
  t_net_profit: float
  total_return_pct: float
  passive_final_equity: float
  passive_return_pct: float
  excess_return_pct: float
  max_drawdown_pct: float
  total_fees: float
  turnover: float
  completed_cycles: int
  open_cycles: int
  winning_cycles: int
  win_rate_pct: float


@strawberry.type(description="做 T 回放单标的结果")
class TTradeReplayInstrumentResult:
  stock_code: str
  instrument_name: str
  status: str
  reason: str
  t_net_profit: float
  total_fees: float
  completed_cycles: int
  open_cycles: int
  winning_cycles: int
  win_rate_pct: float


@strawberry.type(description="做 T 回放权益曲线点")
class TTradeReplayCurvePoint:
  timestamp: datetime
  equity: float
  passive_equity: float
  t_net_profit: float
  return_pct: float
  passive_return_pct: float
  excess_return_pct: float


@strawberry.type(description="做 T 回放批次")
class TTradeReplayCycle:
  batch_id: str
  stock_code: str
  status: str
  entry_time: Optional[datetime]
  exit_time: Optional[datetime]
  entry_volume: int
  exit_volume: int
  open_volume: int
  entry_avg_price: float
  exit_avg_price: float
  total_fees: float
  net_profit: float
  net_return_pct: float
  exit_reason: str


@strawberry.type(description="做 T 历史回放运行")
class TTradeReplay:
  run_id: str
  backtest_id: Optional[str]
  account_id: str
  status: str
  progress_pct: float
  start_time: datetime
  end_time: datetime
  snapshot_id: Optional[str]
  snapshot_date: Optional[str]
  created_at: Optional[datetime]
  updated_at: Optional[datetime]
  error_message: Optional[str]
  data_quality: str
  data_quality_message: str
  skipped_stock_codes: List[str]
  summary: Optional[TTradeReplaySummary]
  instruments: List[TTradeReplayInstrumentResult]
  curve: List[TTradeReplayCurvePoint]


@strawberry.type(description="做 T 历史回放批次分页")
class TTradeReplayCyclePage:
  run_id: str
  total: int
  offset: int
  limit: int
  has_more: bool
  items: List[TTradeReplayCycle]


@strawberry.type(description="做 T 历史回放操作结果")
class TTradeReplayMutationResult:
  success: bool
  code: str
  message: str
  replay: Optional[TTradeReplay] = None
