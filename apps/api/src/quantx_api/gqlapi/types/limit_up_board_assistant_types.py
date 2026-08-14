"""GraphQL types for the account-level limit-up board assistant."""

from dataclasses import field
from datetime import datetime
from typing import List, Optional

import strawberry


@strawberry.input(description="账户级打板助手设置")
class LimitUpBoardAssistantSettingsInput:
  account_id: str
  enabled: bool = False
  mode: str = "paper"
  auto_exit_acknowledged: bool = False
  target_entry_amount: float = strawberry.field(
    default=0.0,
    deprecation_reason="V2 使用风险预算和 targetPositionPct，不再使用固定金额",
  )
  max_single_position_pct: float = 0.02
  auto_signal_min_score: float = strawberry.field(
    default=0.0,
    deprecation_reason="V2 使用冻结晋级模型，不开放普通评分阈值",
  )
  max_daily_exposure_pct: float = 0.06
  planned_tail_loss_pct: float = 0.0015
  max_open_positions: int = 2
  max_ranked_candidates: int = 5
  promotion_model_mode: str = "SHADOW"
  entry_distance_ticks: int = 1
  entry_start_time: str = "09:30"
  entry_end_time: str = "14:50"
  approval_ttl_ms: int = 15_000
  entry_order_ttl_ms: int = 15_000
  max_price_deviation_bps: float = 20.0
  execution_quote_max_age_seconds: float = 3.0
  max_entry_attempts_per_day: int = 1
  exit_limit_break_ticks: int = 1
  exit_min_seal_seconds: float = 3.0
  exit_trailing_arm_profit_pct: float = 2.0
  exit_trailing_drawdown_pct: float = 3.0
  exit_trailing_percent: float = 50.0
  max_holding_trading_days: int = 2
  max_holding_exit_time: str = "14:50"
  exit_max_slippage_bps: float = 50.0


@strawberry.input(description="打板候选布防操作")
class LimitUpBoardCandidateActionInput:
  account_id: str
  instrument_code: str
  idempotency_key: str = ""


@strawberry.input(description="首板候选账户偏好；偏好不能绕过硬否决")
class FirstBoardCandidatePreferenceInput:
  account_id: str
  instrument_code: str
  preference: str
  idempotency_key: str = ""


@strawberry.type(description="账户当日人工布防候选")
class LimitUpBoardArmedCandidate:
  instrument_code: str
  source: str
  arm_version: int
  armed_at: Optional[datetime] = None


@strawberry.type(description="账户级打板助手")
class LimitUpBoardAssistant:
  config_id: Optional[str]
  strategy_run_id: Optional[str]
  account_id: str
  enabled: bool
  mode: str
  auto_exit_acknowledged: bool
  config_version: int
  universe_revision: int
  target_entry_amount: float
  max_single_position_pct: float
  auto_signal_min_score: float
  max_daily_exposure_pct: float
  planned_tail_loss_pct: float
  max_open_positions: int
  max_ranked_candidates: int
  promotion_model_mode: str
  entry_distance_ticks: int
  entry_start_time: str
  entry_end_time: str
  approval_ttl_ms: int
  entry_order_ttl_ms: int
  max_price_deviation_bps: float
  execution_quote_max_age_seconds: float
  max_entry_attempts_per_day: int
  exit_limit_break_ticks: int
  exit_min_seal_seconds: float
  exit_trailing_arm_profit_pct: float
  exit_trailing_drawdown_pct: float
  exit_trailing_percent: float
  max_holding_trading_days: int
  max_holding_exit_time: str
  exit_max_slippage_bps: float
  armed_candidates: List[LimitUpBoardArmedCandidate] = field(default_factory=list)
  manual_armed_count: int = 0
  pending_signal_count: int = 0
  active_exit_plan_count: int = 0
  monitored_count: int = 0
  run_status: str = "STOPPED"
  engine_status: str = "OFFLINE"
  agent_status: str = "OFFLINE"
  reconcile_status: str = "UNKNOWN"
  kill_switch: bool = False
  can_approve: bool = False
  can_activate_live: bool = False
  blocked_reasons: List[str] = field(default_factory=list)
  last_reconciled_at: Optional[datetime] = None
  last_error: Optional[str] = None
  projection_version: str = "0"
  projection_generated_at: Optional[datetime] = None


@strawberry.type(description="账户级打板助手操作结果")
class LimitUpBoardAssistantMutationResult:
  success: bool
  code: str
  message: str
  assistant: Optional[LimitUpBoardAssistant] = None


@strawberry.type(description="账户级打板助手投影更新通知")
class LimitUpBoardAssistantUpdateNotice:
  account_id: str
  version: str
  occurred_at: datetime
