from datetime import datetime
from typing import List, Optional

import strawberry


@strawberry.type(description="QMT Agent 行情流运行指标")
class QmtMarketStreamMetrics:
  status: str
  sequence: int
  queue_depth: int
  resyncs: int
  ack_latency_ms: float
  instrument_count: int
  universe_count: int
  snapshot_age_seconds: Optional[float]
  commit_phase: str


@strawberry.type(description="QMT Agent 本地可靠性诊断")
class QmtAgentDiagnostics:
  agent_version: str
  protocol_version: str
  journal_integrity: str
  journal_size_bytes: int
  journal_pending_reports: int
  journal_processing_commands: int


@strawberry.type(description="当前唯一 QMT 本机连接")
class QmtCurrentConnection:
  id: str
  name: str
  status: str
  account_id: str
  mode: str
  websocket_status: str
  xtdata_status: str
  xtdata_reason: str
  xttrading_status: str
  xttrading_reason: str
  reconciliation_status: str
  last_seen_at: Optional[datetime]
  heartbeat_age_seconds: Optional[float]
  market_stream: QmtMarketStreamMetrics
  diagnostics: QmtAgentDiagnostics


@strawberry.type(description="历史 QMT Agent 登记")
class QmtAgentHistoryEntry:
  id: str
  name: str
  status: str
  last_seen_at: Optional[datetime]
  revoked_at: Optional[datetime]


@strawberry.type(description="个人单账户 QMT 本机连接视图")
class QmtAgentConnection:
  current: Optional[QmtCurrentConnection]
  handover_status: str
  handover_device_status: Optional[str]
  pending_enrollment_expires_at: Optional[datetime]
  history: List[QmtAgentHistoryEntry]


@strawberry.type(description="一次性 QMT Agent 登记码")
class AgentEnrollment:
  enrollment_code: str
  expires_at: datetime


@strawberry.type(description="QMT Agent 安全交接取消结果")
class AgentHandoverMutationResult:
  success: bool
  message: str


@strawberry.type(description="QMT Agent 设备操作结果")
class AgentDeviceMutationResult:
  success: bool
  message: str
  device_id: Optional[str] = None
