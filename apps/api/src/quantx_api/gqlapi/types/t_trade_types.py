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


@strawberry.enum(description="账户自动交易目标灰度阶段")
class TTradeRolloutTarget(Enum):
  CANARY = "CANARY"
  LIVE = "LIVE"


@strawberry.enum(description="原生端两阶段做 T 安全控制动作")
class TTradeControlAction(Enum):
  BEGIN_CONTROLLED_WINDOW = "BEGIN_CONTROLLED_WINDOW"
  ACTIVATE_CANARY = "ACTIVATE_CANARY"
  ACTIVATE_LIVE = "ACTIVATE_LIVE"
  KILL_SWITCH = "KILL_SWITCH"


@strawberry.enum(description="做 T V3 客户端遥测平台")
class TTradeClientTelemetryPlatform(Enum):
  WEB = "WEB"
  IOS = "IOS"


@strawberry.enum(description="做 T V3 客户端低基数遥测事件")
class TTradeClientTelemetryEvent(Enum):
  REFRESH_SUCCESS = "REFRESH_SUCCESS"
  REFRESH_FAILURE = "REFRESH_FAILURE"
  SUBSCRIPTION_RECONNECTED = "SUBSCRIPTION_RECONNECTED"


@strawberry.enum(description="做 T V3 客户端遥测固定界面")
class TTradeClientTelemetrySurface(Enum):
  T_TRADE_SIGNAL_V3 = "T_TRADE_SIGNAL_V3"


@strawberry.input(description="上报做 T V3 客户端低基数遥测")
class TTradeClientTelemetryInput:
  account_id: str
  platform: TTradeClientTelemetryPlatform
  event: TTradeClientTelemetryEvent
  surface: TTradeClientTelemetrySurface


@strawberry.type(description="做 T V3 客户端遥测接收结果")
class TTradeClientTelemetryResult:
  accepted: bool


@strawberry.enum(description="做 T 机会引擎数据健康")
class TTradeSignalDataHealth(Enum):
  WARMING = "WARMING"
  READY = "READY"
  DEGRADED = "DEGRADED"
  STALE = "STALE"
  CONTINUITY_LOST = "CONTINUITY_LOST"
  INSUFFICIENT = "INSUFFICIENT"


@strawberry.enum(description="做 T 回撤反弹分支阶段")
class TTradePullbackPhase(Enum):
  OBSERVING = "OBSERVING"
  PULLBACK_FORMING = "PULLBACK_FORMING"
  LOW_STABILIZING = "LOW_STABILIZING"
  REBOUND_CONFIRMING = "REBOUND_CONFIRMING"
  CANDIDATE_LATCHED = "CANDIDATE_LATCHED"
  SUPPRESSED = "SUPPRESSED"


@strawberry.enum(description="做 T 动量加速分支阶段")
class TTradeMomentumPhase(Enum):
  OBSERVING = "OBSERVING"
  BASELINING = "BASELINING"
  MOMENTUM_BUILDING = "MOMENTUM_BUILDING"
  ACCELERATING = "ACCELERATING"
  OVEREXTENDED = "OVEREXTENDED"
  CANDIDATE_LATCHED = "CANDIDATE_LATCHED"
  SUPPRESSED = "SUPPRESSED"


@strawberry.enum(description="做 T 信号路径")
class TTradeSignalPath(Enum):
  PULLBACK_REBOUND = "PULLBACK_REBOUND"
  MOMENTUM_ACCELERATION = "MOMENTUM_ACCELERATION"


@strawberry.enum(description="做 T 列表压缩展示的主导阶段")
class TTradeDominantPhase(Enum):
  NONE = "NONE"
  PULLBACK_OBSERVING = "PULLBACK_OBSERVING"
  PULLBACK_FORMING = "PULLBACK_FORMING"
  PULLBACK_LOW_STABILIZING = "PULLBACK_LOW_STABILIZING"
  PULLBACK_REBOUND_CONFIRMING = "PULLBACK_REBOUND_CONFIRMING"
  PULLBACK_CANDIDATE_LATCHED = "PULLBACK_CANDIDATE_LATCHED"
  PULLBACK_SUPPRESSED = "PULLBACK_SUPPRESSED"
  MOMENTUM_OBSERVING = "MOMENTUM_OBSERVING"
  MOMENTUM_BASELINING = "MOMENTUM_BASELINING"
  MOMENTUM_BUILDING = "MOMENTUM_BUILDING"
  MOMENTUM_ACCELERATING = "MOMENTUM_ACCELERATING"
  MOMENTUM_OVEREXTENDED = "MOMENTUM_OVEREXTENDED"
  MOMENTUM_CANDIDATE_LATCHED = "MOMENTUM_CANDIDATE_LATCHED"
  MOMENTUM_SUPPRESSED = "MOMENTUM_SUPPRESSED"


@strawberry.enum(description="做 T 候选生命周期")
class TTradeCandidateStatus(Enum):
  NONE = "NONE"
  LATCHED = "LATCHED"
  AWAITING_APPROVAL = "AWAITING_APPROVAL"
  SUPPRESSED = "SUPPRESSED"
  REARMING = "REARMING"


@strawberry.enum(description="做 T 信号评估持久化种类")
class TTradeSignalEvaluationKind(Enum):
  MATERIAL = "MATERIAL"
  COALESCED_DIAGNOSTIC = "COALESCED_DIAGNOSTIC"


@strawberry.input(description="V3 做 T 机会引擎规则参数")
class TTradeSignalPolicyInput:
  max_samples: int
  max_quote_age_ms: int
  pullback_min_samples: int
  pullback_min_coverage_seconds: int
  momentum_min_samples: int
  momentum_min_coverage_seconds: int
  sparse_degraded_gap_seconds: int
  pullback_required_fields: List[str]
  momentum_required_fields: List[str]
  allowed_session_codes: List[str]
  continuous_am_start_time: str
  continuous_am_end_time: str
  continuous_pm_start_time: str
  continuous_pm_end_time: str
  close_protection_seconds: int
  pullback_lookback_seconds: int
  pullback_stabilization_seconds: int
  pullback_threshold_pct: float
  pullback_formation_threshold_multiplier: float
  pullback_rebound_threshold_pct: float
  pullback_max_spread_ticks: int
  pullback_volume_short_window_seconds: int
  pullback_volume_baseline_window_seconds: int
  momentum_enabled: bool
  momentum_window_seconds: int
  momentum_min_rise_pct: float
  momentum_formation_threshold_multiplier: float
  momentum_min_move_seconds: int
  momentum_baseline_seconds: int
  momentum_baseline_coverage_ratio: float
  momentum_min_amount_velocity_ratio: float
  momentum_min_vwap_premium_pct: float
  momentum_max_vwap_premium_pct: float
  momentum_high_tolerance_ticks: int
  momentum_max_spread_ticks: int
  momentum_max_spread_pct: float
  profile_pullback_threshold_min_multiplier: float
  profile_pullback_threshold_max_multiplier: float
  profile_momentum_rise_min_multiplier: float
  profile_momentum_rise_max_multiplier: float
  profile_momentum_velocity_min_ratio: float
  profile_momentum_velocity_max_ratio: float
  pullback_depth_weight: float
  pullback_rebound_weight: float
  pullback_stabilization_weight: float
  pullback_turn_slope_weight: float
  pullback_vwap_weight: float
  pullback_liquidity_weight: float
  pullback_volume_weight: float
  momentum_rise_weight: float
  momentum_turnover_weight: float
  momentum_slope_weight: float
  momentum_persistence_weight: float
  momentum_vwap_weight: float
  momentum_liquidity_weight: float
  momentum_book_imbalance_weight: float
  pullback_depth_score_min_pct: float
  pullback_depth_score_target_multiplier: float
  pullback_rebound_score_min_pct: float
  pullback_rebound_score_max_pct: float
  pullback_stabilization_score_min_seconds: float
  pullback_stabilization_score_max_seconds: float
  pullback_turn_slope_score_min_pct_per_second: float
  pullback_turn_slope_score_max_pct_per_second: float
  pullback_vwap_full_score_max_premium_pct: float
  pullback_vwap_zero_score_premium_pct: float
  pullback_liquidity_full_score_spread_ticks: float
  pullback_liquidity_zero_score_spread_ticks: float
  pullback_volume_score_min_ratio: float
  pullback_volume_score_max_ratio: float
  momentum_rise_score_min_pct: float
  momentum_rise_score_target_multiplier: float
  momentum_turnover_score_min_ratio: float
  momentum_turnover_score_target_multiplier: float
  momentum_slope_score_min_pct_per_second: float
  momentum_slope_score_target_multiplier: float
  momentum_persistence_score_min_ratio: float
  momentum_persistence_score_max_ratio: float
  momentum_vwap_zero_score_min_premium_pct: float
  momentum_vwap_zero_score_max_premium_pct: float
  momentum_liquidity_full_score_spread_ticks: float
  momentum_liquidity_zero_score_spread_ticks: float
  momentum_book_imbalance_score_min_ratio: float
  momentum_book_imbalance_score_max_ratio: float
  pullback_data_quality_penalty_points: float
  pullback_chase_penalty_start_premium_pct: float
  pullback_chase_penalty_full_premium_pct: float
  pullback_chase_penalty_points: float
  momentum_data_quality_penalty_points: float
  momentum_overextension_penalty_start_premium_pct: float
  momentum_overextension_penalty_full_premium_pct: float
  momentum_overextension_penalty_points: float
  preview_score: float
  candidate_score: float
  revalidate_score: float
  rearm_score: float
  candidate_confirm_seconds: int
  candidate_confirm_ticks: int
  rearm_seconds: int
  candidate_ttl_seconds: int


@strawberry.type(description="V3 做 T 机会引擎规范化规则参数")
class TTradeSignalPolicy:
  policy_version: str
  feature_schema_version: str
  max_samples: int
  max_quote_age_ms: int
  pullback_min_samples: int
  pullback_min_coverage_seconds: int
  momentum_min_samples: int
  momentum_min_coverage_seconds: int
  sparse_degraded_gap_seconds: int
  pullback_required_fields: List[str]
  momentum_required_fields: List[str]
  allowed_session_codes: List[str]
  continuous_am_start_time: str
  continuous_am_end_time: str
  continuous_pm_start_time: str
  continuous_pm_end_time: str
  close_protection_seconds: int
  pullback_lookback_seconds: int
  pullback_stabilization_seconds: int
  pullback_threshold_pct: float
  pullback_formation_threshold_multiplier: float
  pullback_rebound_threshold_pct: float
  pullback_max_spread_ticks: int
  pullback_volume_short_window_seconds: int
  pullback_volume_baseline_window_seconds: int
  momentum_enabled: bool
  momentum_window_seconds: int
  momentum_min_rise_pct: float
  momentum_formation_threshold_multiplier: float
  momentum_min_move_seconds: int
  momentum_baseline_seconds: int
  momentum_baseline_coverage_ratio: float
  momentum_min_amount_velocity_ratio: float
  momentum_min_vwap_premium_pct: float
  momentum_max_vwap_premium_pct: float
  momentum_high_tolerance_ticks: int
  momentum_max_spread_ticks: int
  momentum_max_spread_pct: float
  profile_pullback_threshold_min_multiplier: float
  profile_pullback_threshold_max_multiplier: float
  profile_momentum_rise_min_multiplier: float
  profile_momentum_rise_max_multiplier: float
  profile_momentum_velocity_min_ratio: float
  profile_momentum_velocity_max_ratio: float
  pullback_depth_weight: float
  pullback_rebound_weight: float
  pullback_stabilization_weight: float
  pullback_turn_slope_weight: float
  pullback_vwap_weight: float
  pullback_liquidity_weight: float
  pullback_volume_weight: float
  momentum_rise_weight: float
  momentum_turnover_weight: float
  momentum_slope_weight: float
  momentum_persistence_weight: float
  momentum_vwap_weight: float
  momentum_liquidity_weight: float
  momentum_book_imbalance_weight: float
  pullback_depth_score_min_pct: float
  pullback_depth_score_target_multiplier: float
  pullback_rebound_score_min_pct: float
  pullback_rebound_score_max_pct: float
  pullback_stabilization_score_min_seconds: float
  pullback_stabilization_score_max_seconds: float
  pullback_turn_slope_score_min_pct_per_second: float
  pullback_turn_slope_score_max_pct_per_second: float
  pullback_vwap_full_score_max_premium_pct: float
  pullback_vwap_zero_score_premium_pct: float
  pullback_liquidity_full_score_spread_ticks: float
  pullback_liquidity_zero_score_spread_ticks: float
  pullback_volume_score_min_ratio: float
  pullback_volume_score_max_ratio: float
  momentum_rise_score_min_pct: float
  momentum_rise_score_target_multiplier: float
  momentum_turnover_score_min_ratio: float
  momentum_turnover_score_target_multiplier: float
  momentum_slope_score_min_pct_per_second: float
  momentum_slope_score_target_multiplier: float
  momentum_persistence_score_min_ratio: float
  momentum_persistence_score_max_ratio: float
  momentum_vwap_zero_score_min_premium_pct: float
  momentum_vwap_zero_score_max_premium_pct: float
  momentum_liquidity_full_score_spread_ticks: float
  momentum_liquidity_zero_score_spread_ticks: float
  momentum_book_imbalance_score_min_ratio: float
  momentum_book_imbalance_score_max_ratio: float
  pullback_data_quality_penalty_points: float
  pullback_chase_penalty_start_premium_pct: float
  pullback_chase_penalty_full_premium_pct: float
  pullback_chase_penalty_points: float
  momentum_data_quality_penalty_points: float
  momentum_overextension_penalty_start_premium_pct: float
  momentum_overextension_penalty_full_premium_pct: float
  momentum_overextension_penalty_points: float
  preview_score: float
  candidate_score: float
  revalidate_score: float
  rearm_score: float
  candidate_confirm_seconds: int
  candidate_confirm_ticks: int
  candidate_ttl_seconds: int
  rearm_seconds: int


@strawberry.input(description="纯校验做 T 信号规则，不写入运行配置")
class TTradeSignalPolicyPreviewInput:
  account_id: str
  expected_config_version: int
  signal_policy: TTradeSignalPolicyInput


@strawberry.type(description="做 T 信号规则校验问题")
class TTradeSignalPolicyIssue:
  code: str
  message: str
  field: Optional[str] = None


@strawberry.type(description="做 T 信号规则纯校验结果")
class TTradeSignalPolicyPreviewResult:
  valid: bool
  config_version: int
  errors: List[TTradeSignalPolicyIssue]
  warnings: List[TTradeSignalPolicyIssue]
  normalized_policy: Optional[TTradeSignalPolicy]
  changed_fields: List[str]
  requires_rewarm: bool


@strawberry.input(description="确认 V3 做 T 候选时客户端观察到的 CAS 身份")
class TTradeCandidateApprovalExpectationInput:
  signal_version: int
  candidate_id: strawberry.ID
  candidate_fingerprint: str
  candidate_state_version: int
  config_version: int
  policy_version: str


@strawberry.input(description="生成原生端做 T 安全控制确认预览")
class TTradeControlPreviewInput:
  account_id: str
  action: TTradeControlAction
  policy_version: int
  idempotency_key: str
  snapshot_id: str = ""
  target_stage: Optional[TTradeRolloutTarget] = None
  reason: str = ""


@strawberry.input(description="消费原生端做 T 安全控制确认凭据")
class TTradeControlConfirmationInput:
  challenge_id: strawberry.ID
  confirmation_token: str


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


@strawberry.type(description="做 T 数据健康原因")
class TTradeSignalReason:
  code: str
  label: str
  detail: str


@strawberry.type(description="做 T 信号硬门禁")
class TTradeSignalGate:
  code: str
  label: str
  passed: bool
  observed_value: Optional[float]
  required_value: Optional[float]
  detail: str


@strawberry.type(description="做 T 机会分贡献")
class TTradeScoreContribution:
  code: str
  label: str
  points: float
  max_points: float
  observed_value: Optional[float]
  target_value: Optional[float]
  detail: str


@strawberry.type(description="做 T 信号阻断原因")
class TTradeSignalBlocker:
  code: str
  label: str
  detail: str


@strawberry.type(description="做 T 因果特征快照；不可计算值保持 null")
class TTradeSignalFeatures:
  sample_count: int
  coverage_seconds: Optional[float]
  max_gap_seconds: Optional[float]
  price: Optional[float]
  price_tick: Optional[float]
  bid_price: Optional[float]
  ask_price: Optional[float]
  spread_ticks: Optional[float]
  spread_pct: Optional[float]
  book_imbalance: Optional[float]
  session_vwap: Optional[float]
  vwap_premium_pct: Optional[float]
  return_5s_pct: Optional[float]
  return_15s_pct: Optional[float]
  return_30s_pct: Optional[float]
  return_60s_pct: Optional[float]
  return_300s_pct: Optional[float]
  price_slope_60s_pct_per_second: Optional[float]
  price_acceleration_pct_per_second2: Optional[float]
  realized_volatility_60s_pct: Optional[float]
  realized_volatility_300s_pct: Optional[float]
  window_high: Optional[float]
  window_low: Optional[float]
  pullback_pct: Optional[float]
  rebound_pct: Optional[float]
  seconds_since_low: Optional[float]
  rebound_slope_pct_per_second: Optional[float]
  range_position: Optional[float]
  amount_velocity_ratio_15s_60s: Optional[float]
  momentum_rise_pct: Optional[float]
  momentum_move_seconds: Optional[float]
  momentum_window_high: Optional[float]
  momentum_range_position: Optional[float]
  momentum_baseline_coverage_seconds: Optional[float]
  momentum_amount_velocity_ratio: Optional[float]


@strawberry.type(description="做 T 回撤反弹分支的服务端评估")
class TTradePullbackSignalBranch:
  phase: TTradePullbackPhase
  score: Optional[float]
  preview: bool
  candidate_ready: bool
  hard_gates: List[TTradeSignalGate]
  score_contributions: List[TTradeScoreContribution]
  blockers: List[TTradeSignalBlocker]


@strawberry.type(description="做 T 动量加速分支的服务端评估")
class TTradeMomentumSignalBranch:
  phase: TTradeMomentumPhase
  score: Optional[float]
  preview: bool
  candidate_ready: bool
  hard_gates: List[TTradeSignalGate]
  score_contributions: List[TTradeScoreContribution]
  blockers: List[TTradeSignalBlocker]


@strawberry.type(description="做 T 单标的权威 V3 信号快照")
class TTradeSignalSnapshot:
  instrument_code: str
  trade_date: str
  evaluated_at: datetime
  source_at: datetime
  source_time_ms: str
  tick_ordinal: str
  continuity_generation: str
  data_age_ms: Optional[int]
  window_coverage_seconds: Optional[int]
  sample_count: int
  data_health: TTradeSignalDataHealth
  data_health_reasons: List[TTradeSignalReason]
  pullback_phase: TTradePullbackPhase
  momentum_phase: TTradeMomentumPhase
  dominant_phase: TTradeDominantPhase
  selected_path: Optional[TTradeSignalPath]
  pullback_score: Optional[float]
  momentum_score: Optional[float]
  opportunity_score: Optional[float]
  preview_threshold: float
  candidate_threshold: float
  revalidate_threshold: float
  rearm_threshold: float
  features: TTradeSignalFeatures
  pullback: TTradePullbackSignalBranch
  momentum: TTradeMomentumSignalBranch
  hard_gates: List[TTradeSignalGate]
  score_contributions: List[TTradeScoreContribution]
  top_blockers: List[TTradeSignalBlocker]
  episode_id: Optional[strawberry.ID]
  candidate_id: Optional[strawberry.ID]
  candidate_fingerprint: Optional[str]
  candidate_status: TTradeCandidateStatus
  candidate_created_at: Optional[datetime]
  candidate_expires_at: Optional[datetime]
  pending_entry_intent_id: Optional[strawberry.ID]
  signal_version: int
  candidate_state_version: int
  state_schema_version: str
  feature_schema_version: str
  policy_version: str
  config_version: int
  profile_version: Optional[str]
  profile_fingerprint: Optional[str]


@strawberry.type(description="持仓做 T 会话")
class TTradeSession:
  run_id: str
  account_id: str
  stock_code: str
  mode: str
  run_status: str
  status: str
  target_trade_amount: float
  max_trade_amount: float
  planned_entry_amount: float
  target_profit_pct: float
  base_floor_pct: float
  hard_stop_enabled: bool
  hard_stop_pct: float
  time_exit_mode: TTradeTimeExitMode
  time_exit_time: str
  max_holding_trading_days: int
  signal_snapshot: Optional[TTradeSignalSnapshot]
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
  expected_config_version: int
  signal_policy: TTradeSignalPolicyInput
  enabled: bool = False
  mode: str = "paper"
  auto_exit_acknowledged: bool = False
  ignored_stock_codes: List[str] = field(default_factory=list)
  target_trade_amount: float = 10_000.0
  max_trade_amount: float = 12_000.0
  max_concurrent_batches: int = 3
  max_total_t_exposure_pct: float = 0.1
  max_price_deviation_pct: float = 0.3
  target_profit_pct: float = 2.0
  base_floor_pct: float = 0.5
  initial_gap_pct: float = 1.5
  trailing_gap_slope: float = 0.25
  max_gap_pct: float = 3.0
  high_profit_lock_enabled: bool = True
  high_profit_arm_pct: float = 4.0
  high_profit_max_drawdown_pct: float = 1.2
  rapid_reversal_enabled: bool = True
  rapid_reversal_window_seconds: int = 15
  rapid_reversal_drawdown_pct: float = 0.8
  rapid_reversal_confirm_ticks: int = 2
  limit_up_touch_exit_enabled: bool = True
  limit_up_touch_tolerance_ticks: int = 0
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
  scope: str


@strawberry.type(description="做 T 生产就绪与灰度状态")
class TTradeLiveReadiness:
  account_id: str
  ready: bool
  status: str
  preparation_ready: bool
  automation_ready: bool
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
  preparation_blocked_reasons: List[str]
  checks: List[TTradeReadinessCheck]
  snapshot_id: Optional[str]
  snapshot_hash: Optional[str]
  snapshot_at: Optional[datetime]
  reconciliation_age_seconds: Optional[float]
  queued_command_count: int
  queue_delay_seconds: float
  dead_letter_count: int
  unresolved_critical_alert_count: int
  manual_coexistence: bool
  external_order_count: int
  external_trade_count: int
  controlled_window_active: bool
  controlled_window_snapshot_id: Optional[str]
  controlled_window_started_at: Optional[datetime]
  new_external_order_count: int
  new_external_trade_count: int
  working_external_order_count: int
  journal_integrity: str
  journal_size_bytes: int
  journal_pending_reports: int
  last_backup_at: Optional[datetime]
  checked_at: datetime


@strawberry.type(description="原生端两阶段做 T 安全控制预览")
class TTradeControlPreview:
  challenge_id: strawberry.ID
  confirmation_token: Optional[str]
  token_issued: bool
  account_id: str
  action: TTradeControlAction
  policy_version: int
  snapshot_id: str
  target_stage: Optional[TTradeRolloutTarget]
  reason: str
  current_stage: str
  readiness_status: str
  readiness_fingerprint: str
  challenge_expires_at: datetime
  challenge_status: str
  operation_status: str
  checks: List[TTradeReadinessCheck]
  warnings: List[str]


@strawberry.type(description="原生端做 T 安全控制预览结果")
class TTradeControlPreviewResult:
  success: bool
  code: str
  message: str
  preview: Optional[TTradeControlPreview] = None


@strawberry.type(description="原生端做 T 安全控制确认结果")
class TTradeControlConfirmationResult:
  success: bool
  code: str
  message: str
  challenge_id: Optional[strawberry.ID] = None
  account_id: Optional[str] = None
  action: Optional[TTradeControlAction] = None
  challenge_consumed: bool = False
  operation_status: str = "NOT_CONSUMED"
  readiness: Optional[TTradeLiveReadiness] = None


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


@strawberry.type(description="持久化做 T 信号评估证据")
class TTradeSignalEvaluation:
  id: strawberry.ID
  account_id: str
  run_id: strawberry.ID
  stock_code: str
  event_kind: TTradeSignalEvaluationKind
  event_type: str
  evaluated_at: datetime
  window_started_at: Optional[datetime]
  window_ended_at: Optional[datetime]
  coalesced_count: int
  policy_version: str
  schema_version: str
  content_fingerprint: str
  signal_snapshot: Optional[TTradeSignalSnapshot]


@strawberry.type(description="做 T 信号评估稳定游标分页")
class TTradeSignalEvaluationPage:
  items: List[TTradeSignalEvaluation]
  page_info: PageInfo


@strawberry.enum(description="做 T 候选全链路完整性")
class TTradeCandidateTraceIntegrityStatus(Enum):
  COMPLETE = "COMPLETE"
  IN_PROGRESS = "IN_PROGRESS"
  BROKEN = "BROKEN"


@strawberry.type(description="做 T 候选的因果行情源身份")
class TTradeCandidateTraceSourceIdentity:
  source_time_ms: Optional[str]
  tick_ordinal: Optional[str]
  continuity_generation: Optional[str]
  trade_date: Optional[str]
  candidate_fingerprint: Optional[str]
  policy_version: Optional[str]
  feature_schema_version: Optional[str]
  profile_version: Optional[str]


@strawberry.type(description="做 T 候选追踪中缺失或尚未发生的链路节点")
class TTradeCandidateTraceMissingReason:
  code: str
  stage: str
  expected: bool
  detail: str


@strawberry.type(description="做 T 候选关联的持久化实体标识")
class TTradeCandidateTraceLinks:
  evaluation_ids: List[str]
  intent_ids: List[str]
  client_order_ids: List[str]
  correlation_ids: List[str]
  broker_order_ids: List[str]
  order_ids: List[str]
  trade_ids: List[str]
  batch_ids: List[str]
  exit_plan_ids: List[str]
  exit_plan_event_ids: List[str]


@strawberry.type(description="做 T 候选从评估到退出计划的单个持久化事实")
class TTradeCandidateTraceEvent:
  stage: str
  event_type: str
  entity_id: str
  occurred_at: datetime
  status: Optional[str]
  related_ids: JSON
  details: JSON


@strawberry.type(description="账户内单个做 T 候选的端到端持久化事实时间线")
class TTradeCandidateTrace:
  account_id: str
  candidate_id: str
  strategy_run_id: str
  instrument_code: str
  source_evaluation_id: str
  source_identity: TTradeCandidateTraceSourceIdentity
  integrity_status: TTradeCandidateTraceIntegrityStatus
  missing_reasons: List[TTradeCandidateTraceMissingReason]
  links: TTradeCandidateTraceLinks
  events: List[TTradeCandidateTraceEvent]


@strawberry.type(description="做 T 信号诊断统计口径")
class TTradeSignalDiagnosticDenominator:
  code: str
  label: str
  ready_instrument_seconds: float


@strawberry.type(description="做 T 信号诊断漏斗阶段")
class TTradeSignalFunnelStage:
  code: str
  label: str
  unit_code: str
  denominator_code: Optional[str]
  count: int
  conversion_rate: Optional[float]


@strawberry.type(description="做 T 信号诊断 blocker 排名")
class TTradeSignalBlockerAggregate:
  blocker: TTradeSignalBlocker
  count: int
  rate: Optional[float]
  denominator_code: str
  denominator_value: float


@strawberry.type(description="做 T 信号分数分布区间")
class TTradeSignalScoreBucket:
  policy_version: str
  feature_schema_version: str
  profile_version: Optional[str]
  path: Optional[TTradeSignalPath]
  lower_bound: float
  upper_bound: float
  count: int


@strawberry.type(description="做 T 双 FSM 停留时间")
class TTradeSignalFsmDwell:
  branch: str
  phase: str
  duration_seconds: float
  transition_count: int


@strawberry.type(description="做 T 双 FSM from-to 转移边")
class TTradeSignalFsmTransition:
  branch: str
  from_phase: str
  to_phase: str
  count: int


@strawberry.type(description="做 T 候选生命周期结果")
class TTradeCandidateOutcomeAggregate:
  code: str
  label: str
  count: int


@strawberry.type(description="做 T 信号诊断版本分组")
class TTradeSignalVersionGroup:
  policy_version: str
  feature_schema_version: str
  profile_version: Optional[str]
  count: int


@strawberry.type(description="做 T 固定窗口费用后收益聚合")
class TTradeFixedWindowReturnAggregate:
  window_seconds: int
  sample_count: int
  average_net_return_pct: Optional[float]


@strawberry.type(description="做 T 成交后费用化机会表现；数据不足时明确不可用")
class TTradePostCandidatePerformance:
  available: bool
  reason_code: Optional[str]
  reason: Optional[str]
  sample_count: int
  net_mfe_pct: Optional[float]
  net_mae_pct: Optional[float]
  fixed_window_returns: List[TTradeFixedWindowReturnAggregate]
  required_data_codes: List[str]


@strawberry.type(description="按 policy/feature/profile 唯一版本坐标隔离的诊断分区")
class TTradeSignalDiagnosticPartition:
  policy_version: str
  feature_schema_version: str
  profile_version: Optional[str]
  denominator: TTradeSignalDiagnosticDenominator
  funnel: List[TTradeSignalFunnelStage]
  blockers: List[TTradeSignalBlockerAggregate]
  score_distribution: List[TTradeSignalScoreBucket]
  fsm_dwell: List[TTradeSignalFsmDwell]
  fsm_transitions: List[TTradeSignalFsmTransition]
  candidate_outcomes: List[TTradeCandidateOutcomeAggregate]
  post_candidate_performance: TTradePostCandidatePerformance


@strawberry.type(description="做 T 信号诊断聚合；不可用时明确 fail-closed")
class TTradeSignalDiagnostics:
  available: bool
  reason_code: Optional[str]
  reason: Optional[str]
  account_id: str
  stock_code: Optional[str]
  start_time: datetime
  end_time: datetime
  merged_versions: bool
  warnings: List[str]
  partitions: List[TTradeSignalDiagnosticPartition]
  version_groups: List[TTradeSignalVersionGroup]


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
  signal_policy: TTradeSignalPolicy
  max_price_deviation_pct: float
  max_exit_slippage_bps: float
  target_profit_pct: float
  base_floor_pct: float
  initial_gap_pct: float
  trailing_gap_slope: float
  max_gap_pct: float
  high_profit_lock_enabled: bool
  high_profit_arm_pct: float
  high_profit_max_drawdown_pct: float
  rapid_reversal_enabled: bool
  rapid_reversal_window_seconds: int
  rapid_reversal_drawdown_pct: float
  rapid_reversal_confirm_ticks: int
  limit_up_touch_exit_enabled: bool
  limit_up_touch_tolerance_ticks: int
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


@strawberry.enum(description="做 T 历史回放更新类型")
class TTradeReplayUpdateKind(Enum):
  CREATED = "CREATED"
  STATUS_CHANGED = "STATUS_CHANGED"
  PROGRESS = "PROGRESS"
  RESULT_READY = "RESULT_READY"


@strawberry.type(description="做 T 历史回放更新通知")
class TTradeReplayUpdateNotice:
  account_id: str
  run_id: str
  revision: str
  kind: TTradeReplayUpdateKind
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
  idempotency_key: str
  start_time: datetime
  end_time: datetime
  signal_policy: TTradeSignalPolicyInput
  initial_portfolio_as_of: Optional[datetime] = None
  initial_cash: Optional[float] = None
  initial_total_asset: Optional[float] = None
  initial_positions: List[TTradeReplayPositionInput] = field(default_factory=list)
  target_trade_amount: float = 10_000.0
  max_trade_amount: float = 12_000.0
  max_concurrent_batches: int = 3
  max_total_t_exposure_pct: float = 0.1
  max_price_deviation_pct: float = 0.3
  target_profit_pct: float = 2.0
  base_floor_pct: float = 0.5
  initial_gap_pct: float = 1.5
  trailing_gap_slope: float = 0.25
  max_gap_pct: float = 3.0
  high_profit_lock_enabled: bool = True
  high_profit_arm_pct: float = 4.0
  high_profit_max_drawdown_pct: float = 1.2
  rapid_reversal_enabled: bool = True
  rapid_reversal_window_seconds: int = 15
  rapid_reversal_drawdown_pct: float = 0.8
  rapid_reversal_confirm_ticks: int = 2
  limit_up_touch_exit_enabled: bool = True
  limit_up_touch_tolerance_ticks: int = 0
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
  natural_exit_cycles: int = 0
  forced_exit_cycles: int = 0
  liquidation_failed_cycles: int = 0
  capital_capacity: float = 0.0
  average_occupied_capital: float = 0.0
  peak_occupied_capital: float = 0.0
  capital_occupancy_pct: float = 0.0
  capital_availability_pct: float = 0.0
  capital_turnover_times: float = 0.0
  capital_turnover_per_trading_day: float = 0.0
  capital_utilization_pct: float = 0.0
  average_holding_hours: float = 0.0
  max_holding_hours: float = 0.0
  capital_profit_per_occupied_day_pct: float = 0.0


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
  forced_exit_cycles: int = 0
  capital_utilization_pct: float = 0.0
  average_holding_hours: float = 0.0


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
  liquidation_status: str = ""
  forced_exit: bool = False
  entry_capital: float = 0.0
  holding_hours: float = 0.0
  capital_utilization_pct: float = 0.0


@strawberry.type(description="做 T 回放报告产物与样本内结论")
class TTradeReplayReport:
  status: str
  schema_version: int
  generated_at: Optional[datetime]
  conclusion_code: str
  conclusion: str
  html_artifact: str
  json_artifact: str


@strawberry.type(description="做 T 历史回放运行")
class TTradeReplay:
  run_id: str
  backtest_id: Optional[str]
  account_id: str
  status: str
  progress_pct: float
  revision: str
  processed_until: Optional[datetime]
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
  report: Optional[TTradeReplayReport] = None


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
