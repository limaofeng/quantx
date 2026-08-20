"""GraphQL types for account-level limit-up board historical replays."""

from dataclasses import field
from datetime import datetime
from enum import Enum
from typing import List, Optional

import strawberry


@strawberry.enum(description="打板助手历史回放成交情景档案")
class LimitUpBoardReplayScenarioProfile(Enum):
  STANDARD_V1 = "STANDARD_V1"


@strawberry.enum(description="打板助手历史回放更新类型")
class LimitUpBoardReplayUpdateKind(Enum):
  CREATED = "CREATED"
  STATUS_CHANGED = "STATUS_CHANGED"
  PROGRESS = "PROGRESS"
  RESULT_READY = "RESULT_READY"


@strawberry.input(description="启动账户级打板助手历史回放")
class LimitUpBoardReplayStartInput:
  account_id: str
  idempotency_key: str
  start_time: datetime
  end_time: datetime
  scenario_profile: LimitUpBoardReplayScenarioProfile = (
    LimitUpBoardReplayScenarioProfile.STANDARD_V1
  )
  initial_cash: Optional[float] = None
  initial_total_asset: Optional[float] = None


@strawberry.type(description="打板回放成交情景假设")
class LimitUpBoardReplayScenarioAssumption:
  scenario_id: str
  label: str
  confirmation_delay_ms: int
  participation_cap_pct: float
  book_depth_participation_pct: float
  theoretical_upper_bound: bool = False


@strawberry.type(description="打板回放请求快照")
class LimitUpBoardReplayRequest:
  start_time: datetime
  end_time: datetime
  scenario_profile: str
  initial_cash: Optional[float] = None
  initial_total_asset: Optional[float] = None


@strawberry.type(description="打板回放数据集请求区间")
class LimitUpBoardReplayRequestedRange:
  start_time: Optional[datetime] = None
  end_time: Optional[datetime] = None
  timezone: str = "Asia/Shanghai"


@strawberry.type(description="打板回放输入所绑定的规则版本")
class LimitUpBoardReplayVersions:
  score: List[str] = field(default_factory=list)
  feature: List[str] = field(default_factory=list)
  promotion_model: List[str] = field(default_factory=list)
  exit_policy: List[str] = field(default_factory=list)


@strawberry.type(description="打板回放候选快照与原始 Tick 覆盖率")
class LimitUpBoardReplayCoverage:
  frame_count: int = 0
  candidate_observations: int = 0
  promotion_eligible_observations: int = 0
  candidate_instrument_count: int = 0
  covered_trading_dates: List[str] = field(default_factory=list)
  expected_trading_dates: List[str] = field(default_factory=list)
  missing_trading_dates: List[str] = field(default_factory=list)
  first_observed_at: Optional[datetime] = None
  last_observed_at: Optional[datetime] = None
  max_frame_gap_seconds: float = 0.0
  frame_gaps_over_15_seconds: int = 0
  missing_continuous_sessions: List[str] = field(default_factory=list)
  session_boundary_gaps_over_15_seconds: int = 0
  scanner_stopped_frames: int = 0
  raw_tick_count: int = 0
  raw_tick_instrument_count: int = 0
  missing_tick_instruments: List[str] = field(default_factory=list)
  candidate_fresh_tick_coverage_pct: float = 0.0
  candidate_observations_without_fresh_tick: int = 0
  max_candidate_tick_age_seconds: float = 0.0


@strawberry.type(description="打板回放原始 Tick 必需字段质量")
class LimitUpBoardReplayTickFieldQuality:
  tick_count: int = 0
  invalid_identity_count: int = 0
  derived_source_time_count: int = 0
  missing_native_price_limits_count: int = 0
  missing_stock_status_count: int = 0
  missing_price_tick_count: int = 0
  missing_five_level_book_count: int = 0
  missing_price_fields_count: int = 0
  duplicate_identity_count: int = 0
  conflicting_identity_count: int = 0
  blockers: List[str] = field(default_factory=list)
  warnings: List[str] = field(default_factory=list)


@strawberry.type(description="打板回放 Tick 载入错误")
class LimitUpBoardReplayTickLoadError:
  instrument_code: str
  message: str


@strawberry.type(description="打板回放不可变输入文件摘要")
class LimitUpBoardReplayArtifact:
  content_sha256: str = ""
  row_count: int = 0
  format: str = ""
  compression: str = ""


@strawberry.type(description="打板回放不可变输入文件集合")
class LimitUpBoardReplayArtifacts:
  candidate_universe: LimitUpBoardReplayArtifact = field(
    default_factory=LimitUpBoardReplayArtifact
  )
  raw_ticks: LimitUpBoardReplayArtifact = field(
    default_factory=LimitUpBoardReplayArtifact
  )


@strawberry.type(description="打板回放数据质量")
class LimitUpBoardReplayDataQuality:
  status: str = "PENDING"
  executable: bool = False
  source: str = ""
  raw_tick_count: int = 0
  five_level_missing: int = 0
  native_limit_missing: int = 0
  fresh_coverage: float = 0.0
  coverage: LimitUpBoardReplayCoverage = field(
    default_factory=LimitUpBoardReplayCoverage
  )
  tick_field_quality: LimitUpBoardReplayTickFieldQuality = field(
    default_factory=LimitUpBoardReplayTickFieldQuality
  )
  tick_load_errors: List[LimitUpBoardReplayTickLoadError] = field(
    default_factory=list
  )
  future_data_violations: int = 0
  candidate_frame_count_mismatches: int = 0
  score_versions: List[str] = field(default_factory=list)
  feature_versions: List[str] = field(default_factory=list)
  model_versions: List[str] = field(default_factory=list)
  exit_policy_versions: List[str] = field(default_factory=list)
  blockers: List[str] = field(default_factory=list)
  warnings: List[str] = field(default_factory=list)


@strawberry.type(description="打板回放候选数据集清单")
class LimitUpBoardReplayInputManifest:
  schema_version: int = 0
  source: str = ""
  requested_range: LimitUpBoardReplayRequestedRange = field(
    default_factory=LimitUpBoardReplayRequestedRange
  )
  config_fingerprint: str = ""
  snapshot_refs_fingerprint: str = ""
  versions: LimitUpBoardReplayVersions = field(
    default_factory=LimitUpBoardReplayVersions
  )
  coverage: LimitUpBoardReplayCoverage = field(
    default_factory=LimitUpBoardReplayCoverage
  )
  artifacts: LimitUpBoardReplayArtifacts = field(
    default_factory=LimitUpBoardReplayArtifacts
  )
  data_quality: LimitUpBoardReplayDataQuality = field(
    default_factory=LimitUpBoardReplayDataQuality
  )
  dataset_fingerprint: str = ""
  manifest_sha256: str = ""


@strawberry.type(description="打板回放场景收益与尾部风险摘要")
class LimitUpBoardReplaySummary:
  initial_equity: float = 0.0
  final_equity: float = 0.0
  total_return_pct: float = 0.0
  max_drawdown_pct: float = 0.0
  cvar95_loss_pct: float = 0.0
  fees: float = 0.0
  fill_rate_pct: float = 0.0
  open_position_count: int = 0
  open_order_count: int = 0
  unsellable_position_count: int = 0


@strawberry.type(description="打板回放候选至退出漏斗")
class LimitUpBoardReplayFunnel:
  candidate_frames: int = 0
  candidate_observations: int = 0
  qualified_observations: int = 0
  entry_intents: int = 0
  approval_due: int = 0
  approval_rejected: int = 0
  orders: int = 0
  filled_orders: int = 0
  partial_orders: int = 0
  expired_orders: int = 0
  trades: int = 0
  completed_exits: int = 0


@strawberry.type(description="打板回放命名约束指标")
class LimitUpBoardReplayConstraintMetric:
  key: str
  value: float


@strawberry.type(description="打板回放拒绝原因计数")
class LimitUpBoardReplayRejectionReason:
  reason: str
  count: int


@strawberry.type(description="打板回放窗口末未平仓")
class LimitUpBoardReplayOpenPosition:
  instrument_code: str
  volume: int
  available_volume: int
  average_price: float
  last_price: float
  market_value: float
  status: str


@strawberry.type(description="打板回放成交明细")
class LimitUpBoardReplayTrade:
  trade_id: str
  order_id: str
  instrument_code: str
  side: str
  price: float
  volume: int
  amount: float
  fees: float
  trade_time: Optional[datetime]


@strawberry.type(description="打板回放权益曲线点")
class LimitUpBoardReplayCurvePoint:
  timestamp: datetime
  equity: float
  return_pct: float


@strawberry.type(description="打板回放成交分页")
class LimitUpBoardReplayTradePage:
  job_id: str
  scenario_id: str
  total: int
  offset: int
  limit: int
  has_more: bool
  items: List[LimitUpBoardReplayTrade] = field(default_factory=list)


@strawberry.type(description="打板回放权益曲线分页")
class LimitUpBoardReplayCurvePage:
  job_id: str
  scenario_id: str
  total: int
  offset: int
  limit: int
  has_more: bool
  items: List[LimitUpBoardReplayCurvePoint] = field(default_factory=list)


@strawberry.type(description="打板回放单一成交情景")
class LimitUpBoardReplayScenario:
  scenario_id: str
  label: str
  backtest_id: Optional[str]
  status: str
  progress_pct: float
  processed_until: Optional[datetime]
  revision: str
  error_message: Optional[str]
  confirmation_delay_ms: int
  participation_cap_pct: float
  book_depth_participation_pct: float
  theoretical_upper_bound: bool = False
  result_available: bool = False
  result_schema_version: int = 0
  no_queue_credit: bool = True
  summary: Optional[LimitUpBoardReplaySummary] = None
  funnel: Optional[LimitUpBoardReplayFunnel] = None
  constraint_statistics: List[LimitUpBoardReplayConstraintMetric] = field(
    default_factory=list
  )
  rejection_reasons: List[LimitUpBoardReplayRejectionReason] = field(
    default_factory=list
  )
  open_positions: List[LimitUpBoardReplayOpenPosition] = field(
    default_factory=list
  )


@strawberry.type(description="账户级打板助手历史回放任务")
class LimitUpBoardReplay:
  job_id: str
  account_id: str
  status: str
  progress_pct: float
  processed_until: Optional[datetime]
  revision: str
  scenario_profile: str
  request: LimitUpBoardReplayRequest
  dataset_fingerprint: str
  config_fingerprint: str
  input_manifest: LimitUpBoardReplayInputManifest
  data_quality: LimitUpBoardReplayDataQuality
  error_message: Optional[str]
  started_at: Optional[datetime]
  completed_at: Optional[datetime]
  created_at: Optional[datetime]
  updated_at: Optional[datetime]
  scenarios: List[LimitUpBoardReplayScenario] = field(default_factory=list)


@strawberry.type(description="打板助手历史回放启动准备信息")
class LimitUpBoardReplayPreparation:
  account_id: str
  start_time: datetime
  end_time: datetime
  scenario_profile: str
  ready: bool
  assistant_config_version: int
  assistant_projection_version: str
  has_active_job: bool
  active_job_id: Optional[str]
  message: str
  blockers: List[str] = field(default_factory=list)
  warnings: List[str] = field(default_factory=list)
  scenarios: List[LimitUpBoardReplayScenarioAssumption] = field(
    default_factory=list
  )


@strawberry.type(description="打板助手历史回放操作结果")
class LimitUpBoardReplayMutationResult:
  success: bool
  code: str
  message: str
  replay: Optional[LimitUpBoardReplay] = None


@strawberry.type(description="打板助手历史回放更新通知")
class LimitUpBoardReplayUpdateNotice:
  account_id: str
  job_id: str
  revision: str
  kind: LimitUpBoardReplayUpdateKind
  occurred_at: datetime
