"""GraphQL types for account-level execution safety."""

from dataclasses import field
from datetime import datetime
from enum import Enum
from typing import List, Optional

import strawberry


@strawberry.enum(description="账户事实链路健康状态；不包含查询或检查过程")
class AccountExecutionHealthStatus(Enum):
  HEALTHY = "HEALTHY"
  BLOCKED = "BLOCKED"
  KILLED = "KILLED"


@strawberry.enum(description="账户执行安全检查状态")
class AccountExecutionSafetyCheckStatus(Enum):
  PASSED = "PASSED"
  STANDBY = "STANDBY"
  FAILED = "FAILED"


@strawberry.enum(description="账户准入观测历史范围")
class AccountSafetyHistoryRange(Enum):
  HOURS_24 = "24h"
  DAYS_7 = "7d"
  DAYS_30 = "30d"
  DAYS_90 = "90d"
  YEAR_1 = "1y"


@strawberry.enum(description="Monitor 见证的账户准入检查状态")
class AccountSafetyHistoryStatus(Enum):
  PASSED = "PASSED"
  STANDBY = "STANDBY"
  FAILED = "FAILED"
  UNKNOWN = "UNKNOWN"


@strawberry.type(description="账户准入检查历史时间桶")
class AccountSafetyHistoryPoint:
  start: datetime
  status: AccountSafetyHistoryStatus
  coverage_pct: float
  sample_count: int
  passed_count: int
  standby_count: int
  failed_count: int
  unknown_count: int


@strawberry.type(description="单项账户准入检查的历史摘要")
class AccountSafetyCheckHistory:
  code: str
  current_status: AccountSafetyHistoryStatus
  checked_at: Optional[datetime]
  reason_code: Optional[str]
  public_message: Optional[str]
  coverage_pct: float
  incident_count: int
  points: List[AccountSafetyHistoryPoint] = field(default_factory=list)


@strawberry.type(description="账户准入检查异常事件")
class AccountSafetyIncident:
  id: strawberry.ID
  check_code: str
  opened_at: datetime
  resolved_at: Optional[datetime]
  last_confirmed_failed_at: datetime
  active: bool
  observation_fresh: bool
  opened_reason_code: str
  last_reason_code: str
  opened_message: str
  last_message: str


@strawberry.type(description="由独立 Monitor 见证的账户准入历史")
class AccountSafetyHistory:
  available: bool
  range: AccountSafetyHistoryRange
  generated_at: datetime
  first_observed_at: Optional[datetime]
  last_observed_at: Optional[datetime]
  observer_fresh: bool
  bucket_seconds: int
  checks: List[AccountSafetyCheckHistory] = field(default_factory=list)
  incidents: List[AccountSafetyIncident] = field(default_factory=list)
  incidents_truncated: bool = False


@strawberry.type(description="账户执行安全检查项")
class AccountExecutionSafetyCheck:
  code: str
  status: AccountExecutionSafetyCheckStatus
  message: str
  scope: str


@strawberry.type(description="需要显式修复的账户隔离委托")
class QuarantinedOrder:
  client_order_id: str
  plan_id: str
  intent_id: str
  quarantine_reason: str
  broker_order_id: str
  repairable: bool
  blocked_reason: str
  quarantined_at: datetime
  source_sequence: int


@strawberry.type(description="账户级实盘执行能力，不包含具体助手的灰度策略")
class AccountExecutionSafety:
  account_id: str
  authorization_state: str
  state_version: int
  health_status: AccountExecutionHealthStatus
  execution_mode: str
  can_increase_risk: bool
  can_reduce_risk: bool
  can_activate_automation: bool
  summary: str
  blocked_reasons: List[str] = field(default_factory=list)
  checks: List[AccountExecutionSafetyCheck] = field(default_factory=list)
  quarantined_orders: List[QuarantinedOrder] = field(default_factory=list)
  engine_status: str = "OFFLINE"
  agent_status: str = "OFFLINE"
  agent_mode: str = "offline"
  protocol_version: str = ""
  reconcile_status: str = "UNKNOWN"
  kill_switch: bool = False
  execution_window_active: bool = False
  snapshot_id: Optional[str] = None
  snapshot_hash: Optional[str] = None
  snapshot_at: Optional[datetime] = None
  reconciliation_age_seconds: Optional[float] = None
  queued_command_count: int = 0
  queue_delay_seconds: float = 0
  dead_letter_count: int = 0
  unresolved_critical_alert_count: int = 0
  external_order_count: int = 0
  external_trade_count: int = 0
  new_external_order_count: int = 0
  new_external_trade_count: int = 0
  working_external_order_count: int = 0
  last_backup_at: Optional[datetime] = None
  checked_at: Optional[datetime] = None


@strawberry.enum(description="账户级两阶段执行控制动作")
class AccountExecutionControlAction(Enum):
  BEGIN_CONTROLLED_WINDOW = "BEGIN_CONTROLLED_WINDOW"
  ENABLE_RISK_INCREASE = "ENABLE_RISK_INCREASE"
  PAUSE_RISK_INCREASE = "PAUSE_RISK_INCREASE"
  KILL_SWITCH = "KILL_SWITCH"
  CLEAR_KILL_SWITCH = "CLEAR_KILL_SWITCH"
  REPAIR_QUARANTINED_ORDER = "REPAIR_QUARANTINED_ORDER"


@strawberry.input(description="预览账户级执行控制")
class AccountExecutionControlPreviewInput:
  account_id: str
  action: AccountExecutionControlAction
  state_version: int
  idempotency_key: str
  snapshot_id: str = ""
  reason: str = ""
  client_order_id: str = ""
  quarantine_reason: str = ""


@strawberry.type(description="账户级执行控制预览")
class AccountExecutionControlPreview:
  challenge_id: strawberry.ID
  confirmation_token: Optional[str]
  token_issued: bool
  account_id: str
  action: AccountExecutionControlAction
  state_version: int
  snapshot_id: str
  reason: str
  client_order_id: str
  quarantine_reason: str
  challenge_expires_at: datetime
  challenge_status: str
  operation_status: str
  safety: AccountExecutionSafety


@strawberry.type(description="账户级执行控制预览结果")
class AccountExecutionControlPreviewResult:
  success: bool
  code: str
  message: str
  preview: Optional[AccountExecutionControlPreview] = None


@strawberry.input(description="确认账户级执行控制")
class AccountExecutionControlConfirmationInput:
  challenge_id: strawberry.ID
  confirmation_token: str


@strawberry.type(description="账户级执行控制确认结果")
class AccountExecutionControlConfirmationResult:
  success: bool
  code: str
  message: str
  challenge_id: Optional[strawberry.ID] = None
  action: Optional[AccountExecutionControlAction] = None
  operation_status: str = "NOT_CONSUMED"
  safety: Optional[AccountExecutionSafety] = None
  event_id: Optional[strawberry.ID] = None
  client_order_id: Optional[str] = None
  plan_id: Optional[str] = None
  intent_id: Optional[str] = None
  snapshot_id: Optional[str] = None
  broker_terminal_status: Optional[str] = None
  cumulative_filled_volume: Optional[int] = None
