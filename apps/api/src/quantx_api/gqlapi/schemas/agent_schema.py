"""GraphQL view of the personal single-account QMT connection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import strawberry
from quantx_infrastructure.core.data.market_stream_transport import (
  market_stream_store,
)
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import (
  AgentDevice as AgentDeviceModel,
)
from quantx_infrastructure.models.agent_runtime import (
  AgentEnrollmentCode,
  RuntimeComponentHeartbeat,
)
from quantx_infrastructure.services.agent_session_guard import (
  API_HEARTBEAT_COMPONENT,
  REMOTE_AGENT_ACCOUNT_MISMATCH,
  evaluate_agent_session,
  parse_utc_timestamp,
)
from sqlalchemy import select

from quantx_api.agent_hub import agent_connection_hub
from quantx_api.auth.agent_service import AgentAuthService
from quantx_api.auth.tokens import utcnow

from ..security import principal_from_context
from ..types.agent_types import (
  AgentDeviceMutationResult,
  AgentEnrollment,
  AgentHandoverMutationResult,
  QmtAgentConnection,
  QmtAgentDiagnostics,
  QmtAgentHistoryEntry,
  QmtCurrentConnection,
  QmtMarketStreamMetrics,
)

AGENT_TTL = timedelta(seconds=90)


def _aware(value: datetime | None) -> datetime | None:
  if value is None or value.tzinfo is not None:
    return value
  return value.replace(tzinfo=timezone.utc)


def _age_seconds(value: datetime | None, now: datetime) -> float | None:
  normalized = _aware(value)
  reference = _aware(now)
  if normalized is None or reference is None:
    return None
  return round(max(0.0, (reference - normalized).total_seconds()), 3)


def _device_status(
  device: AgentDeviceModel,
  heartbeat: RuntimeComponentHeartbeat | None,
  api_heartbeat: RuntimeComponentHeartbeat | None,
  now: datetime,
  hub_connected: bool,
) -> str:
  if device.revoked_at is not None:
    return "REVOKED"
  heartbeat_status = str(heartbeat.status if heartbeat is not None else "").upper()
  if hub_connected and heartbeat_status == REMOTE_AGENT_ACCOUNT_MISMATCH:
    return REMOTE_AGENT_ACCOUNT_MISMATCH
  if (
    not hub_connected
    or not evaluate_agent_session(
      heartbeat,
      api_heartbeat,
      now=now,
      acceptable_statuses={
        "READY",
        "RECONCILING",
        "RECONCILE_REQUIRED",
        "TRADING_UNAVAILABLE",
        "XTDATA_UNAVAILABLE",
        "EMERGENCY_STOP",
      },
    ).current
  ):
    return "OFFLINE"
  return heartbeat_status or "OFFLINE"


def _mode(capabilities: list[str]) -> str:
  normalized = {str(value).lower() for value in capabilities}
  return next(
    (value for value in ("live", "paper", "data-only") if value in normalized),
    "unknown",
  )


def _single_account_id(account_ids: tuple[str, ...]) -> str:
  normalized = tuple(value.strip() for value in account_ids if value.strip())
  if len(normalized) != 1:
    raise ValueError("QMT 本机连接要求当前会话只授权一个账户")
  return normalized[0]


async def _heartbeat_map(
  db,
  devices: list[AgentDeviceModel],
) -> dict[str, RuntimeComponentHeartbeat]:
  if not devices:
    return {}
  components = [f"qmt-agent:{device.id}" for device in devices]
  rows = (
    await db.execute(
      select(RuntimeComponentHeartbeat).where(
        RuntimeComponentHeartbeat.component.in_(components)
      )
    )
  ).scalars()
  return {str(row.instance_id): row for row in rows}


def _select_current(
  devices: list[AgentDeviceModel],
  statuses: dict[str, str],
  now: datetime,
) -> AgentDeviceModel | None:
  active = [device for device in devices if device.revoked_at is None]
  if not active:
    return None
  replacement_target_ids = {
    str(device.replaces_device_id) for device in active if device.replaces_device_id
  }

  def timestamp(value: datetime | None) -> float:
    normalized = _aware(value)
    return normalized.timestamp() if normalized is not None else 0.0

  def score(device: AgentDeviceModel) -> tuple[int, int, int, float, float]:
    status = statuses.get(str(device.id), "OFFLINE")
    return (
      int(status == "READY"),
      int(status not in {"OFFLINE", "REVOKED"}),
      int(str(device.id) in replacement_target_ids),
      timestamp(device.last_seen_at),
      timestamp(device.created_at),
    )

  return max(active, key=score)


def _reconciliation_status(
  status: str,
  details: dict[str, Any],
  account_id: str,
) -> str:
  normalized = status.upper()
  if normalized in {"RECONCILING", "RECONCILE_REQUIRED"}:
    return normalized
  if account_id in {str(value) for value in details.get("blockedAccounts") or []}:
    return "RECONCILE_REQUIRED"
  if (
    account_id in {str(value) for value in details.get("readyAccounts") or []}
    or normalized == "READY"
  ):
    return "READY"
  return "UNKNOWN"


async def _current_connection(
  device: AgentDeviceModel,
  heartbeat: RuntimeComponentHeartbeat | None,
  *,
  account_id: str,
  now: datetime,
  status: str,
) -> QmtCurrentConnection:
  details = dict(heartbeat.details or {}) if heartbeat is not None else {}
  server_received_at = parse_utc_timestamp(details.get("serverReceivedAt"))
  try:
    stream_state = await market_stream_store.state()
  except Exception:
    stream_state = None
  captured_at = stream_state.captured_at if stream_state is not None else None
  stream_status = (
    str(stream_state.status)
    if stream_state is not None
    else str(details.get("marketStreamStatus") or "OFFLINE")
  )
  return QmtCurrentConnection(
    id=str(device.id),
    name=str(device.name),
    status=status,
    account_id=account_id,
    mode=_mode(list(device.capabilities or [])),
    websocket_status=(
      "CONNECTED" if status not in {"OFFLINE", "REVOKED"} else "OFFLINE"
    ),
    xtdata_status=str(details.get("xtdataStatus") or "UNKNOWN").upper(),
    xtdata_reason=str(details.get("xtdataReason") or ""),
    xttrading_status=str(details.get("xttradingStatus") or "UNKNOWN").upper(),
    xttrading_reason=str(details.get("xttradingReason") or ""),
    reconciliation_status=_reconciliation_status(status, details, account_id),
    last_seen_at=_aware(device.last_seen_at),
    heartbeat_age_seconds=_age_seconds(server_received_at, now),
    market_stream=QmtMarketStreamMetrics(
      status=stream_status.upper(),
      sequence=int(details.get("marketStreamSequence") or 0),
      queue_depth=int(details.get("marketStreamQueueDepth") or 0),
      resyncs=int(details.get("marketStreamResyncs") or 0),
      ack_latency_ms=round(
        float(details.get("marketStreamAckLatencyMs") or 0.0),
        3,
      ),
      instrument_count=(
        int(stream_state.instrument_count) if stream_state is not None else 0
      ),
      universe_count=(
        int(stream_state.universe_count) if stream_state is not None else 0
      ),
      snapshot_age_seconds=_age_seconds(captured_at, now),
      commit_phase=(
        str(stream_state.commit_phase) if stream_state is not None else "IDLE"
      ),
    ),
    diagnostics=QmtAgentDiagnostics(
      agent_version=str(details.get("agentVersion") or ""),
      protocol_version=str(details.get("protocolVersion") or ""),
      journal_integrity=str(details.get("journalIntegrity") or "unknown"),
      journal_size_bytes=int(details.get("journalSizeBytes") or 0),
      journal_pending_reports=int(details.get("journalPendingReports") or 0),
      journal_processing_commands=int(details.get("journalProcessingCommands") or 0),
    ),
  )


async def resolve_qmt_agent_connection(
  db,
  *,
  user_id: str,
  account_id: str,
) -> QmtAgentConnection:
  now = utcnow()
  devices = list(
    (
      await db.execute(
        select(AgentDeviceModel).where(AgentDeviceModel.user_id == user_id)
      )
    ).scalars()
  )
  heartbeats = await _heartbeat_map(db, devices)
  api_heartbeat = await db.get(RuntimeComponentHeartbeat, API_HEARTBEAT_COMPONENT)
  statuses: dict[str, str] = {}
  for device in devices:
    heartbeat = heartbeats.get(str(device.id))
    details = dict(heartbeat.details or {}) if heartbeat is not None else {}
    agent_session_id = str(details.get("agentSessionId") or "")
    hub_connected = bool(
      agent_session_id
      and await agent_connection_hub.is_connected(
        str(device.id),
        agent_session_id=agent_session_id,
      )
    )
    statuses[str(device.id)] = _device_status(
      device,
      heartbeat,
      api_heartbeat,
      now,
      hub_connected,
    )
  current_device = _select_current(devices, statuses, now)
  current = (
    await _current_connection(
      current_device,
      heartbeats.get(str(current_device.id)),
      account_id=account_id,
      now=now,
      status=statuses.get(str(current_device.id), "OFFLINE"),
    )
    if current_device is not None
    else None
  )
  candidates = [
    device
    for device in devices
    if current_device is not None
    and device.revoked_at is None
    and device.id != current_device.id
    and device.replaces_device_id == current_device.id
  ]
  candidate = max(
    candidates,
    key=lambda value: (
      _aware(value.created_at) or datetime.min.replace(tzinfo=timezone.utc)
    ),
    default=None,
  )
  pending = (
    await db.execute(
      select(AgentEnrollmentCode)
      .where(
        AgentEnrollmentCode.user_id == user_id,
        AgentEnrollmentCode.consumed_at.is_(None),
        AgentEnrollmentCode.expires_at > now,
      )
      .order_by(AgentEnrollmentCode.created_at.desc())
      .limit(1)
    )
  ).scalar_one_or_none()
  candidate_status = (
    statuses.get(str(candidate.id), "OFFLINE") if candidate is not None else None
  )
  if candidate is not None:
    handover_status = (
      "WAITING_FOR_CONNECTION"
      if candidate_status == "OFFLINE"
      else "RECONCILING"
      if candidate_status in {"RECONCILING", "RECONCILE_REQUIRED"}
      else "WAITING_FOR_READY"
    )
  elif pending is not None:
    handover_status = "WAITING_FOR_ENROLLMENT"
  else:
    handover_status = "IDLE"
  excluded = {
    str(device.id) for device in (current_device, candidate) if device is not None
  }
  history = [
    QmtAgentHistoryEntry(
      id=str(device.id),
      name=str(device.name),
      status=statuses.get(str(device.id), "OFFLINE"),
      last_seen_at=_aware(device.last_seen_at),
      revoked_at=_aware(device.revoked_at),
    )
    for device in sorted(
      (device for device in devices if str(device.id) not in excluded),
      key=lambda value: (
        _aware(value.created_at) or datetime.min.replace(tzinfo=timezone.utc)
      ),
      reverse=True,
    )
  ]
  return QmtAgentConnection(
    current=current,
    handover_status=handover_status,
    handover_device_status=candidate_status,
    pending_enrollment_expires_at=(_aware(pending.expires_at) if pending else None),
    history=history,
  )


@strawberry.type(description="QMT Agent 本机连接查询")
class AgentQuery:
  @strawberry.field(description="当前用户唯一的 QMT 本机连接")
  async def qmt_agent_connection(
    self,
    info: strawberry.types.Info,
  ) -> QmtAgentConnection:
    principal = principal_from_context(info.context)
    account_id = _single_account_id(principal.authorized_account_ids)
    async with AsyncSessionLocal() as db:
      return await resolve_qmt_agent_connection(
        db,
        user_id=principal.user_id,
        account_id=account_id,
      )


@strawberry.type(description="QMT Agent 本机连接管理")
class AgentMutation:
  @strawberry.mutation(description="创建十分钟内有效的安全交接登记码")
  async def create_agent_enrollment(
    self,
    info: strawberry.types.Info,
    name: str = "本机 QMT Agent",
  ) -> AgentEnrollment:
    principal = principal_from_context(info.context)
    account_id = _single_account_id(principal.authorized_account_ids)
    async with AsyncSessionLocal() as db:
      enrollment = await AgentAuthService(db).create_enrollment(
        user_id=principal.user_id,
        name=name,
        authorized_account_ids=[account_id],
      )
    return AgentEnrollment(
      enrollment_code=enrollment.code,
      expires_at=_aware(enrollment.expires_at),
    )

  @strawberry.mutation(description="取消尚未完成的 QMT Agent 安全交接")
  async def cancel_agent_handover(
    self,
    info: strawberry.types.Info,
  ) -> AgentHandoverMutationResult:
    principal = principal_from_context(info.context)
    async with AsyncSessionLocal() as db:
      cancelled = await AgentAuthService(db).cancel_handover(user_id=principal.user_id)
    for device_id in cancelled.revoked_device_ids:
      await agent_connection_hub.revoke(device_id)
    return AgentHandoverMutationResult(
      success=True,
      message="已取消安全交接",
    )

  @strawberry.mutation(description="撤销当前 QMT Agent")
  async def revoke_agent_device(
    self,
    info: strawberry.types.Info,
    device_id: str,
  ) -> AgentDeviceMutationResult:
    principal = principal_from_context(info.context)
    async with AsyncSessionLocal() as db:
      revoked = await AgentAuthService(db).revoke(
        device_id=device_id,
        user_id=principal.user_id,
      )
    if revoked:
      await agent_connection_hub.revoke(device_id)
    return AgentDeviceMutationResult(
      success=revoked,
      message="设备已撤销" if revoked else "设备不存在",
      device_id=device_id if revoked else None,
    )
