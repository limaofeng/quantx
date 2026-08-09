from datetime import datetime
from typing import List, Optional

import strawberry


@strawberry.type(description="已登记的 QMT Agent 设备")
class AgentDevice:
  id: str
  name: str
  status: str
  authorized_account_ids: List[str]
  capabilities: List[str]
  last_seen_at: Optional[datetime]
  revoked_at: Optional[datetime]
  requires_reconciliation: bool


@strawberry.type(description="一次性 QMT Agent 登记码")
class AgentEnrollment:
  enrollment_code: str
  expires_at: datetime


@strawberry.type(description="QMT Agent 设备操作结果")
class AgentDeviceMutationResult:
  success: bool
  message: str
  device_id: Optional[str] = None
