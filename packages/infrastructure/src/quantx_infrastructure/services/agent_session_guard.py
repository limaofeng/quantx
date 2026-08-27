"""Fail-closed identity checks for remote QMT Agent control sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Collection

API_HEARTBEAT_COMPONENT = "api"
AGENT_HEARTBEAT_PREFIX = "qmt-agent:"
AGENT_SERVER_SESSION_PAYLOAD_KEY = "_quantx_server_session"

REMOTE_AGENT_OFFLINE = "REMOTE_AGENT_OFFLINE"
REMOTE_AGENT_SESSION_STALE = "REMOTE_AGENT_SESSION_STALE"
REMOTE_AGENT_NOT_RECONCILED = "REMOTE_AGENT_NOT_RECONCILED"
REMOTE_AGENT_ACCOUNT_MISMATCH = "REMOTE_AGENT_ACCOUNT_MISMATCH"

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
  api_heartbeat: Any,
  *,
  now: datetime,
  acceptable_statuses: Collection[str] | None = None,
  ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS,
) -> AgentSessionEvaluation:
  """Evaluate a session using server receive time and the current API generation."""

  if agent_heartbeat is None:
    return AgentSessionEvaluation(False, REMOTE_AGENT_OFFLINE)
  details = dict(getattr(agent_heartbeat, "details", None) or {})
  status = str(getattr(agent_heartbeat, "status", "")).upper()
  if (
    status == REMOTE_AGENT_ACCOUNT_MISMATCH
    or str(details.get("reasonCode") or "").upper() == REMOTE_AGENT_ACCOUNT_MISMATCH
  ):
    return AgentSessionEvaluation(False, REMOTE_AGENT_ACCOUNT_MISMATCH)

  api_instance_id = str(getattr(api_heartbeat, "instance_id", "") or "")
  agent_api_instance_id = str(details.get("apiInstanceId") or "")
  agent_session_id = str(details.get("agentSessionId") or "")
  server_received_at = parse_utc_timestamp(details.get("serverReceivedAt"))
  current_time = to_naive_utc(now)
  if (
    current_time is None
    or not api_instance_is_current(
      api_heartbeat,
      now=current_time,
      ttl_seconds=ttl_seconds,
    )
    or not bool(details.get("sessionActive"))
    or not agent_session_id
    or not api_instance_id
    or agent_api_instance_id != api_instance_id
    or server_received_at is None
  ):
    reason = (
      REMOTE_AGENT_OFFLINE
      if not bool(details.get("sessionActive"))
      else REMOTE_AGENT_SESSION_STALE
    )
    return AgentSessionEvaluation(
      False,
      reason,
      api_instance_id=agent_api_instance_id,
      agent_session_id=agent_session_id,
      server_received_at=server_received_at,
    )

  age = current_time - server_received_at
  sent_at = parse_utc_timestamp(details.get("agentSentAt"))
  if (
    age < -timedelta(seconds=MAX_AGENT_CLOCK_SKEW_SECONDS)
    or age > timedelta(seconds=max(1.0, ttl_seconds))
    or sent_at is None
    or abs((sent_at - server_received_at).total_seconds())
    > MAX_AGENT_CLOCK_SKEW_SECONDS
  ):
    return AgentSessionEvaluation(
      False,
      REMOTE_AGENT_SESSION_STALE,
      api_instance_id=agent_api_instance_id,
      agent_session_id=agent_session_id,
      server_received_at=server_received_at,
    )

  if acceptable_statuses is not None:
    allowed = {str(value).upper() for value in acceptable_statuses}
    if status not in allowed:
      return AgentSessionEvaluation(
        False,
        REMOTE_AGENT_NOT_RECONCILED,
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


def report_belongs_to_current_session(
  payload: dict[str, Any],
  heartbeat: Any,
  api_heartbeat: Any,
  *,
  now: datetime,
) -> bool:
  metadata = payload.get(AGENT_SERVER_SESSION_PAYLOAD_KEY)
  if not isinstance(metadata, dict) or heartbeat is None:
    return False
  details = dict(getattr(heartbeat, "details", None) or {})
  session = evaluate_agent_session(
    heartbeat,
    api_heartbeat,
    now=now,
  )
  return bool(
    session.current
    and metadata.get("apiInstanceId")
    and metadata.get("agentSessionId")
    and str(metadata.get("apiInstanceId")) == str(details.get("apiInstanceId") or "")
    and str(metadata.get("agentSessionId")) == str(details.get("agentSessionId") or "")
    and str(metadata.get("apiInstanceId"))
    == str(getattr(api_heartbeat, "instance_id", "") or "")
    and api_instance_is_current(api_heartbeat, now=now)
  )
