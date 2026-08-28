from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from quantx_infrastructure.services.agent_session_guard import (
  AGENT_SERVER_SESSION_PAYLOAD_KEY,
  QMT_AGENT_OFFLINE,
  QMT_AGENT_STALE,
  evaluate_agent_session,
  report_belongs_to_current_session,
)


@pytest.fixture(autouse=True)
def clear_qmt_launch_guard(monkeypatch: pytest.MonkeyPatch) -> None:
  for name in (
    "QMT_AGENT_LAUNCH_STATE",
    "QMT_AGENT_LAUNCH_REASON",
    "QMT_AGENT_LAUNCH_STARTED_AT",
  ):
    monkeypatch.delenv(name, raising=False)


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


def test_local_runtime_uses_server_heartbeat_not_api_generation() -> None:
  now = datetime(2026, 8, 27, 10, 0)

  result = evaluate_agent_session(
    _agent(now, api_instance_id="api-previous"),
    now=now,
    acceptable_statuses={"READY"},
  )

  assert result.current
  assert result.agent_session_id == "session-1"


def test_disconnect_and_stale_server_heartbeat_fail_closed() -> None:
  now = datetime(2026, 8, 27, 10, 0)
  disconnected = evaluate_agent_session(
    _agent(now, active=False),
    now=now,
  )
  stale = evaluate_agent_session(
    _agent(now - timedelta(seconds=91)),
    now=now,
  )

  assert disconnected.reason_code == QMT_AGENT_OFFLINE
  assert stale.reason_code == QMT_AGENT_STALE


def test_agent_timestamp_delay_is_diagnostic_only() -> None:
  now = datetime(2026, 8, 27, 10, 0)
  delayed = _agent(now, sent_at=now - timedelta(seconds=30))
  missing = _agent(now)
  missing.details["agentSentAt"] = None

  assert evaluate_agent_session(delayed, now=now).current
  assert evaluate_agent_session(missing, now=now).current


def test_current_windows_launch_rejects_prior_heartbeat(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  now = datetime(2026, 8, 27, 10, 0)
  monkeypatch.setenv("QMT_AGENT_LAUNCH_STATE", "LAUNCH_ALLOWED")
  monkeypatch.setenv(
    "QMT_AGENT_LAUNCH_STARTED_AT",
    (now - timedelta(seconds=10)).isoformat(),
  )

  result = evaluate_agent_session(
    _agent(now - timedelta(seconds=11)),
    now=now,
  )

  assert not result.current
  assert result.reason_code == QMT_AGENT_STALE


def test_blocked_windows_launch_overrides_persisted_heartbeat(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  now = datetime(2026, 8, 27, 10, 0)
  monkeypatch.setenv("QMT_AGENT_LAUNCH_STATE", "BLOCKED")
  monkeypatch.setenv("QMT_AGENT_LAUNCH_REASON", "QMT_ENROLLMENT_REQUIRED")

  result = evaluate_agent_session(_agent(now), now=now)

  assert not result.current
  assert result.reason_code == "QMT_ENROLLMENT_REQUIRED"


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
    now=now,
  )


def test_old_api_report_cannot_promote_new_api_session() -> None:
  now = datetime(2026, 8, 27, 10, 0)
  heartbeat = _agent(now, api_instance_id="api-new")
  payload = {
    AGENT_SERVER_SESSION_PAYLOAD_KEY: {
      "apiInstanceId": "api-old",
      "agentSessionId": "session-1",
    }
  }

  assert not report_belongs_to_current_session(
    payload,
    heartbeat,
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
    now=now,
  )
