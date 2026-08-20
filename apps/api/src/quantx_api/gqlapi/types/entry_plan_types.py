"""GraphQL contract for strategy-backed managed entry plans."""

from __future__ import annotations

from dataclasses import field
from typing import List, Optional

import strawberry
from strawberry.scalars import JSON


@strawberry.input(description="建仓/加仓目标与绝对风险上限")
class EntryPlanTargetInput:
  mode: str
  target_position_pct: Optional[float] = None
  incremental_amount_cny: Optional[float] = None
  additional_volume: Optional[int] = None
  max_total_amount_cny: float = 0.0
  max_position_pct: float = 0.0


@strawberry.input(description="价格阶梯中的一个一次性档位")
class EntryPriceLadderLevelInput:
  level_id: str
  trigger_price: float
  tranche_amount_cny: Optional[float] = None
  tranche_volume: Optional[int] = None
  priority: int = 0


@strawberry.input(description="买入触发规则；字段按 ruleType 受 capabilities 约束")
class EntryPlanRuleInput:
  rule_id: str
  rule_type: str
  priority: int = 0
  enabled: bool = True
  once: bool = False
  preset_id: Optional[str] = None
  min_pullback_pct: Optional[float] = None
  max_pullback_pct: Optional[float] = None
  rebound_confirmation_pct: Optional[float] = None
  fast_ema_period: Optional[int] = None
  slow_ema_period: Optional[int] = None
  manual_trigger_sequence: Optional[int] = None
  ladder_levels: List[EntryPriceLadderLevelInput] = field(default_factory=list)


@strawberry.input(description="分批节奏与计划级容量")
class EntryPlanPacingInput:
  tranche_count: int = 4
  max_single_intent_amount_cny: float = 0.0
  max_daily_filled_amount_cny: float = 0.0
  max_orders_per_day: int = 1
  cash_buffer_pct: float = 0.2
  min_interval_seconds: int = 300
  cooldown_after_reject_seconds: int = 60
  trend_adjustment_enabled: bool = True


@strawberry.input(description="执行环境、确认方式和保护限价参数")
class EntryPlanExecutionInput:
  environment: str = "PAPER"
  authorization_mode: str = "MANUAL_CONFIRM"
  price_reference: str = "ASK1_PROTECTED_LIMIT"
  max_slippage_bps: float = 20.0
  max_price_deviation_bps: float = 20.0
  approval_ttl_ms: int = 15_000


@strawberry.input(description="计划完成条件")
class EntryPlanCompletionInput:
  expire_at_ms: Optional[int] = None
  max_buy_price: float = 0.0
  stop_when_target_reached: bool = True
  stop_when_budget_exhausted: bool = True
  cancel_unsubmitted_on_expiry: bool = True


@strawberry.input(description="真实买入成交后创建的卖出保护模板")
class EntryExitProtectionInput:
  enabled: bool = False
  stop_price: Optional[float] = None
  gross_take_profit_pct: Optional[float] = None
  trailing_arm_profit_pct: Optional[float] = None
  trailing_drawdown_pct: Optional[float] = None
  max_holding_days: Optional[int] = None


@strawberry.input(description="创建建仓/加仓托管计划")
class CreateEntryPlanInput:
  instrument_code: str
  bucket: str
  target_policy: EntryPlanTargetInput
  trigger_rules: List[EntryPlanRuleInput]
  pacing_policy: EntryPlanPacingInput
  execution_policy: EntryPlanExecutionInput
  completion_policy: EntryPlanCompletionInput
  exit_protection: Optional[EntryExitProtectionInput] = None
  note: str = ""
  start_immediately: bool = False
  idempotency_key: str = ""


@strawberry.input(description="更新不存在待收敛订单的建仓/加仓计划")
class UpdateEntryPlanInput:
  plan_id: strawberry.ID
  config_version: int
  target_policy: EntryPlanTargetInput
  trigger_rules: List[EntryPlanRuleInput]
  pacing_policy: EntryPlanPacingInput
  execution_policy: EntryPlanExecutionInput
  completion_policy: EntryPlanCompletionInput
  exit_protection: Optional[EntryExitProtectionInput] = None
  note: str = ""
  idempotency_key: str = ""


@strawberry.input(description="自动建仓授权预览")
class EntryPlanAuthorizationPreviewInput:
  plan_id: strawberry.ID
  config_version: int
  idempotency_key: str


@strawberry.input(description="确认设备绑定的自动建仓授权挑战")
class EntryPlanAuthorizationConfirmationInput:
  plan_id: strawberry.ID
  config_version: int
  challenge_id: strawberry.ID
  confirmation_token: str


@strawberry.type(description="目标模式能力")
class EntryTargetModeCapability:
  value: str
  label: str
  description: str


@strawberry.type(description="规则预设")
class EntryRulePreset:
  preset_id: str
  label: str
  summary: str
  parameters: JSON = field(default_factory=dict)


@strawberry.type(description="买入规则的强类型前端字段定义")
class EntryRuleFieldCapability:
  key: str
  label: str
  type: str
  unit: str
  required: bool
  min: Optional[float]
  max: Optional[float]
  step: Optional[float]
  default_value: JSON
  help_text: str
  advanced: bool


@strawberry.type(description="买入规则卡能力")
class EntryRuleCapability:
  rule_type: str
  label: str
  category: str
  description: str
  suitable_for: str
  warning: str
  fields: List[EntryRuleFieldCapability] = field(default_factory=list)
  presets: List[EntryRulePreset] = field(default_factory=list)


@strawberry.type(description="建仓/加仓计划前端能力契约")
class EntryPlanCapabilities:
  version: str
  target_modes: List[EntryTargetModeCapability]
  rule_types: List[EntryRuleCapability]
  allowed_buckets: List[str]
  environments: List[str]
  authorization_modes: List[str]
  max_open_orders: int


@strawberry.type(description="价格阶梯中的一个可编辑档位")
class EntryPriceLadderLevel:
  level_id: str
  trigger_price: float
  tranche_amount_cny: Optional[float]
  tranche_volume: Optional[int]
  priority: int


@strawberry.type(description="已保存的买入触发规则")
class EntryPlanRule:
  rule_id: str
  rule_type: str
  priority: int
  enabled: bool
  once: bool
  preset_id: str
  min_pullback_pct: Optional[float]
  max_pullback_pct: Optional[float]
  rebound_confirmation_pct: Optional[float]
  fast_ema_period: Optional[int]
  slow_ema_period: Optional[int]
  manual_trigger_sequence: Optional[int]
  ladder_levels: List[EntryPriceLadderLevel] = field(default_factory=list)


@strawberry.type(description="已保存的分批节奏与容量")
class EntryPlanPacing:
  tranche_count: int
  max_single_intent_amount_cny: float
  max_daily_filled_amount_cny: float
  max_orders_per_day: int
  cash_buffer_pct: float
  min_interval_seconds: int
  cooldown_after_reject_seconds: int
  trend_adjustment_enabled: bool


@strawberry.type(description="已保存的执行和价格保护策略")
class EntryPlanExecution:
  price_reference: str
  max_slippage_bps: float
  max_price_deviation_bps: float
  approval_ttl_ms: int


@strawberry.type(description="已保存的计划完成条件")
class EntryPlanCompletion:
  expire_at_ms: Optional[int]
  max_buy_price: float
  stop_when_target_reached: bool
  stop_when_budget_exhausted: bool
  cancel_unsubmitted_on_expiry: bool


@strawberry.type(description="真实成交后按批次创建的卖出保护设置")
class EntryExitProtection:
  enabled: bool
  stop_price: Optional[float]
  gross_take_profit_pct: Optional[float]
  trailing_arm_profit_pct: Optional[float]
  trailing_drawdown_pct: Optional[float]
  max_holding_days: Optional[int]


@strawberry.type(description="建仓/加仓托管计划投影")
class EntryPlan:
  plan_id: strawberry.ID
  config_version: int
  account_id: str
  instrument_code: str
  instrument_name: str
  bucket: str
  plan_kind: str
  phase: str
  run_status: str
  environment: str
  authorization_mode: str
  authorization_state: str
  target_mode: str
  target_position_pct: float
  incremental_amount_cny: float
  additional_volume: int
  max_total_amount_cny: float
  max_position_pct: float
  current_position_volume: int
  baseline_position_volume: int
  current_market_value_cny: float
  filled_volume: int
  filled_amount_cny: float
  remaining_amount_cny: float
  pending_reserved_amount_cny: float
  max_single_intent_amount_cny: float
  max_daily_filled_amount_cny: float
  max_buy_price: float
  rule_types: List[str]
  trigger_rules: List[EntryPlanRule]
  pacing_policy: EntryPlanPacing
  execution_policy: EntryPlanExecution
  completion_policy: EntryPlanCompletion
  exit_protection: EntryExitProtection
  entry_enabled: bool
  note: str
  last_reason_code: str
  pending_intent_id: str
  has_working_order: bool
  next_eligible_at: Optional[int]
  expire_at: Optional[int]
  has_exit_protection: bool
  blocked_reasons: List[str]
  created_at: Optional[str]
  updated_at: Optional[str]


@strawberry.type(description="等待逐笔确认的买入意图")
class EntryIntent:
  intent_id: strawberry.ID
  plan_id: strawberry.ID
  instrument_code: str
  bucket: str
  reason_code: str
  status: str
  target_amount_cny: float
  target_volume: int
  signal_price: float
  current_price: float
  price_deviation_bps: float
  expires_at_ms: int
  risk_action: str
  created_at: Optional[str]


@strawberry.type(description="逐笔确认前重新计算的最新买单预览")
class EntryIntentPreview:
  intent_id: strawberry.ID
  plan_id: strawberry.ID
  instrument_code: str
  valid: bool
  code: str
  message: str
  signal_price: float
  latest_price: float
  price_deviation_bps: float
  requested_amount_cny: float
  sized_volume: int
  final_volume: int
  risk_action: str
  expires_at_ms: int
  challenge_id: strawberry.ID
  confirmation_token: str
  challenge_expires_at: str
  warnings: List[str]


@strawberry.type(description="可读的建仓计划审计事件")
class EntryPlanEvent:
  event_id: strawberry.ID
  plan_id: strawberry.ID
  event_type: str
  occurred_at: Optional[str]
  reason_code: str
  message: str
  details: JSON


@strawberry.type(description="账户级自动买入安全门")
class EntryAutomationStatus:
  account_id: str
  paused: bool
  reason: str
  updated_at: Optional[str]


@strawberry.type(description="自动建仓精确授权预览")
class EntryPlanAuthorizationPreview:
  challenge_id: strawberry.ID
  confirmation_token: str
  authorization_fingerprint: str
  challenge_expires_at: str
  authorization_expires_at: str
  summary: str
  risk_envelope: JSON


@strawberry.type(description="建仓/加仓计划操作结果")
class EntryPlanMutationResult:
  success: bool
  code: str
  message: str
  plan: Optional[EntryPlan] = None


@strawberry.type(description="自动建仓授权操作结果")
class EntryPlanAuthorizationResult:
  success: bool
  code: str
  message: str
  authorization_state: str
  grant_id: Optional[str] = None
  expires_at: Optional[str] = None
  plan: Optional[EntryPlan] = None
