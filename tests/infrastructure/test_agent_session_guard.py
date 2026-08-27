from datetime import datetime, timedelta
from types import SimpleNamespace

from quantx_infrastructure.services.agent_session_guard import (
  AGENT_SERVER_SESSION_PAYLOAD_KEY,
  REMOTE_AGENT_OFFLINE,
  REMOTE_AGENT_SESSION_STALE,
  evaluate_agent_session,
  report_belongs_to_current_session,
)


def _api(now: datetime, instance_id: str = "api-1") -> SimpleNamespace:
  return SimpleNamespace(
    instance_id=instance_id,
    status="READY",
    updated_at=now,
  )


def _agent(
  now: datetime,
  *,
  api_instance_id: str = "api-1",
  session_id: str = "session-1",
  active: bool = True,
  sent_at: datetime | None = None,
) -> SimpleNamespace:
  return SimpleNamespace(
    status="READY",
    updated_at=now,
    details={
      "apiInstanceId": api_instance_id,
      "agentSessionId": session_id,
      "serverReceivedAt": now.isoformat(),
      "agentSentAt": (sent_at or now).isoformat(),
      "sessionActive": active,
    },
  )


def test_current_remote_session_matches_api_generation() -> None:
  now = datetime(2026, 8, 27, 10, 0)

  result = evaluate_agent_session(
    _agent(now),
    _api(now),
    now=now,
    acceptable_statuses={"READY"},
  )

  assert result.current
  assert result.agent_session_id == "session-1"


def test_api_restart_invalidates_fresh_old_agent_heartbeat() -> None:
  now = datetime(2026, 8, 27, 10, 0)

  result = evaluate_agent_session(
    _agent(now, api_instance_id="api-old"),
    _api(now, "api-new"),
    now=now,
    acceptable_statuses={"READY"},
  )

  assert not result.current
  assert result.reason_code == REMOTE_AGENT_SESSION_STALE


def test_disconnect_and_clock_skew_fail_closed() -> None:
  now = datetime(2026, 8, 27, 10, 0)
  disconnected = evaluate_agent_session(
    _agent(now, active=False),
    _api(now),
    now=now,
  )
  skewed = evaluate_agent_session(
    _agent(now, sent_at=now + timedelta(seconds=6)),
    _api(now),
    now=now,
  )

  assert disconnected.reason_code == REMOTE_AGENT_OFFLINE
  assert skewed.reason_code == REMOTE_AGENT_SESSION_STALE


def test_missing_agent_timestamp_fails_closed() -> None:
  now = datetime(2026, 8, 27, 10, 0)
  heartbeat = _agent(now)
  heartbeat.details["agentSentAt"] = None

  result = evaluate_agent_session(heartbeat, _api(now), now=now)

  assert not result.current
  assert result.reason_code == REMOTE_AGENT_SESSION_STALE


def test_old_report_cannot_promote_replacement_session() -> None:
  now = datetime(2026, 8, 27, 10, 0)
  heartbeat = _agent(now, session_id="session-new")
  payload = {
    AGENT_SERVER_SESSION_PAYLOAD_KEY: {
      "apiInstanceId": "api-1",
      "agentSessionId": "session-old",
    }
  }

  assert not report_belongs_to_current_session(
    payload,
    heartbeat,
    _api(now),
    now=now,
  )


def test_old_api_report_cannot_promote_after_api_restart() -> None:
  now = datetime(2026, 8, 27, 10, 0)
  heartbeat = _agent(now, api_instance_id="api-old")
  payload = {
    AGENT_SERVER_SESSION_PAYLOAD_KEY: {
      "apiInstanceId": "api-old",
      "agentSessionId": "session-1",
    }
  }

  assert not report_belongs_to_current_session(
    payload,
    heartbeat,
    _api(now, "api-new"),
    now=now,
  )


def test_disconnected_session_report_cannot_promote() -> None:
  now = datetime(2026, 8, 27, 10, 0)
  payload = {
    AGENT_SERVER_SESSION_PAYLOAD_KEY: {
      "apiInstanceId": "api-1",
      "agentSessionId": "session-1",
    }
  }

  assert not report_belongs_to_current_session(
    payload,
    _agent(now, active=False),
    _api(now),
    now=now,
  )


def test_stale_session_report_cannot_promote() -> None:
  now = datetime(2026, 8, 27, 10, 0)
  payload = {
    AGENT_SERVER_SESSION_PAYLOAD_KEY: {
      "apiInstanceId": "api-1",
      "agentSessionId": "session-1",
    }
  }

  assert not report_belongs_to_current_session(
    payload,
    _agent(now - timedelta(seconds=91)),
    _api(now),
    now=now,
  )
