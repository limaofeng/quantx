"""GraphQL types for account-level execution safety."""

from dataclasses import field
from datetime import datetime
from typing import List, Optional

import strawberry


@strawberry.type(description="账户执行安全检查项")
class AccountExecutionSafetyCheck:
  code: str
  passed: bool
  message: str
  scope: str


@strawberry.type(description="账户级实盘执行能力，不包含具体助手的灰度策略")
class AccountExecutionSafety:
  account_id: str
  health_status: str
  execution_mode: str
  can_increase_risk: bool
  can_reduce_risk: bool
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
