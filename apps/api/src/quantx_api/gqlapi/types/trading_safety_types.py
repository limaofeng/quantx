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


@strawberry.type(description="账户执行安全检查项")
class AccountExecutionSafetyCheck:
  code: str
  passed: bool
  message: str
  scope: str


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


@strawberry.input(description="预览账户级执行控制")
class AccountExecutionControlPreviewInput:
  account_id: str
  action: AccountExecutionControlAction
  state_version: int
  idempotency_key: str
  snapshot_id: str = ""
  reason: str = ""


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
