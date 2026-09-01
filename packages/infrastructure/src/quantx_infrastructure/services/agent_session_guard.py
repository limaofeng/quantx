"""Windows-local QMT runtime health and control-session isolation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Collection

from quantx_infrastructure.services.qmt_launch_guard import (
  qmt_agent_launch_block_reason,
  qmt_heartbeat_matches_current_launch,
)

API_HEARTBEAT_COMPONENT = "api"
AGENT_HEARTBEAT_PREFIX = "qmt-agent:"
AGENT_SERVER_SESSION_PAYLOAD_KEY = "_quantx_server_session"

QMT_AGENT_OFFLINE = "QMT_AGENT_OFFLINE"
QMT_AGENT_STALE = "QMT_AGENT_STALE"
QMT_AGENT_NOT_RECONCILED = "QMT_AGENT_NOT_RECONCILED"
QMT_ACCOUNT_MISMATCH = "QMT_ACCOUNT_MISMATCH"
QMT_DEVICE_REVOKED = "QMT_DEVICE_REVOKED"
QMT_CONTROL_SESSION_REPLACED = "QMT_CONTROL_SESSION_REPLACED"
QMT_API_RESTARTED = "QMT_API_RESTARTED"
QMT_CONTROL_TRANSPORT_LOST = "QMT_CONTROL_TRANSPORT_LOST"
QMT_CONTROL_DEPENDENCY_UNAVAILABLE = "QMT_CONTROL_DEPENDENCY_UNAVAILABLE"
XTDATA_UNAVAILABLE = "XTDATA_UNAVAILABLE"
XTTRADING_UNAVAILABLE = "XTTRADING_UNAVAILABLE"

DEFAULT_SESSION_TTL_SECONDS = 90.0
MAX_AGENT_CLOCK_SKEW_SECONDS = 5.0


@dataclass(frozen=True)
class AgentSessionEvaluation:
  current: bool
  reason_code: str
  api_instance_id: str = ""
  agent_session_id: str = ""
  server_received_at: datetime | None = None


def to_naive_utc(value: datetime | None) -> datetime | None:
  if value is None:
    return None
  if value.tzinfo is None:
    return value
  return value.astimezone(timezone.utc).replace(tzinfo=None)


def parse_utc_timestamp(value: Any) -> datetime | None:
  if isinstance(value, datetime):
    return to_naive_utc(value)
  text = str(value or "").strip()
  if not text:
    return None
  try:
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
  except ValueError:
    return None
  return to_naive_utc(parsed)


def utc_iso(value: datetime) -> str:
  normalized = to_naive_utc(value)
  if normalized is None:  # pragma: no cover - guarded by the type contract
    raise ValueError("timestamp is required")
  return normalized.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def api_instance_is_current(
  api_heartbeat: Any,
  *,
  now: datetime,
  ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS,
) -> bool:
  if api_heartbeat is None:
    return False
  updated_at = to_naive_utc(getattr(api_heartbeat, "updated_at", None))
  current = to_naive_utc(now)
  if updated_at is None or current is None:
    return False
  age = current - updated_at
  return bool(
    str(getattr(api_heartbeat, "status", "")).upper() == "READY"
    and str(getattr(api_heartbeat, "instance_id", "")).strip()
    and -timedelta(seconds=MAX_AGENT_CLOCK_SKEW_SECONDS)
    <= age
    <= timedelta(seconds=max(1.0, ttl_seconds))
  )


def evaluate_agent_session(
  agent_heartbeat: Any,
  *,
  now: datetime,
  acceptable_statuses: Collection[str] | None = None,
  ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS,
) -> AgentSessionEvaluation:
  """Evaluate the managed local Agent using server-owned timestamps.

  API and Agent generation IDs deliberately do not decide account readiness.
  They remain transport-only identities used when replacing a connection,
  delivering a command, leasing the market stream, or promoting a report.
  """

  launch_reason = qmt_agent_launch_block_reason()
  if launch_reason is not None:
    return AgentSessionEvaluation(False, launch_reason)

  if agent_heartbeat is None:
    return AgentSessionEvaluation(False, QMT_AGENT_OFFLINE)
  details = dict(getattr(agent_heartbeat, "details", None) or {})
  status = str(getattr(agent_heartbeat, "status", "")).upper()
  if (
    status == QMT_ACCOUNT_MISMATCH
    or str(details.get("reasonCode") or "").upper() == QMT_ACCOUNT_MISMATCH
  ):
    return AgentSessionEvaluation(False, QMT_ACCOUNT_MISMATCH)

  agent_api_instance_id = str(details.get("apiInstanceId") or "")
  agent_session_id = str(details.get("agentSessionId") or "")
  server_received_at = parse_utc_timestamp(details.get("serverReceivedAt"))
  current_time = to_naive_utc(now)
  heartbeat_updated_at = to_naive_utc(getattr(agent_heartbeat, "updated_at", None))
  if (
    current_time is None
    or not bool(details.get("sessionActive"))
    or not agent_session_id
    or heartbeat_updated_at is None
  ):
    reason = (
      QMT_AGENT_OFFLINE if not bool(details.get("sessionActive")) else QMT_AGENT_STALE
    )
    return AgentSessionEvaluation(
      False,
      reason,
      api_instance_id=agent_api_instance_id,
      agent_session_id=agent_session_id,
      server_received_at=server_received_at,
    )

  age = current_time - heartbeat_updated_at
  if age > timedelta(
    seconds=max(1.0, ttl_seconds)
  ) or not qmt_heartbeat_matches_current_launch(heartbeat_updated_at):
    return AgentSessionEvaluation(
      False,
      QMT_AGENT_STALE,
      api_instance_id=agent_api_instance_id,
      agent_session_id=agent_session_id,
      server_received_at=server_received_at,
    )

  if acceptable_statuses is not None:
    allowed = {str(value).upper() for value in acceptable_statuses}
    if status not in allowed:
      return AgentSessionEvaluation(
        False,
        QMT_AGENT_NOT_RECONCILED,
        api_instance_id=agent_api_instance_id,
        agent_session_id=agent_session_id,
        server_received_at=server_received_at,
      )
  return AgentSessionEvaluation(
    True,
    "",
    api_instance_id=agent_api_instance_id,
    agent_session_id=agent_session_id,
    server_received_at=server_received_at,
  )


def agent_unready_reason_code(agent_heartbeat: Any) -> str:
  """Return the stable capability reason for a current non-ready Agent."""

  if agent_heartbeat is None:
    return QMT_AGENT_OFFLINE
  status = str(getattr(agent_heartbeat, "status", "")).upper()
  if status == "READY":
    return ""
  if status == "XTDATA_UNAVAILABLE":
    return XTDATA_UNAVAILABLE
  if status == "TRADING_UNAVAILABLE":
    return XTTRADING_UNAVAILABLE
  if status == "EMERGENCY_STOP":
    return "EMERGENCY_STOP"
  return QMT_AGENT_NOT_RECONCILED


def report_belongs_to_current_session(
  payload: dict[str, Any],
  heartbeat: Any,
  *,
  now: datetime,
) -> bool:
  metadata = payload.get(AGENT_SERVER_SESSION_PAYLOAD_KEY)
  if not isinstance(metadata, dict) or heartbeat is None:
    return False
  details = dict(getattr(heartbeat, "details", None) or {})
  session = evaluate_agent_session(
    heartbeat,
    now=now,
  )
  return bool(
    session.current
    and metadata.get("apiInstanceId")
    and metadata.get("agentSessionId")
    and str(metadata.get("apiInstanceId")) == str(details.get("apiInstanceId") or "")
    and str(metadata.get("agentSessionId")) == str(details.get("agentSessionId") or "")
  )
