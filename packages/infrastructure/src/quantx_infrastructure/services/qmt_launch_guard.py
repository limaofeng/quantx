"""Process-local guard against heartbeats from a previous QMT launch."""

from __future__ import annotations

import os
from datetime import datetime, timezone

QMT_LAUNCH_BLOCK_REASONS = frozenset(
  {"QMT_ENROLLMENT_REQUIRED", "QMT_RUNTIME_UNAVAILABLE"}
)


def _naive_utc(value: datetime) -> datetime:
  if value.tzinfo is None:
    return value
  return value.astimezone(timezone.utc).replace(tzinfo=None)


def qmt_agent_launch_state() -> str:
  return os.environ.get("QMT_AGENT_LAUNCH_STATE", "").strip().upper()


def qmt_agent_launch_block_reason() -> str | None:
  """Return a stable public reason when this process skipped QMT launch."""

  if qmt_agent_launch_state() != "BLOCKED":
    return None
  reason = os.environ.get("QMT_AGENT_LAUNCH_REASON", "").strip().upper()
  if reason in QMT_LAUNCH_BLOCK_REASONS:
    return reason
  return "QMT_LAUNCH_BLOCKED"


def qmt_agent_launch_started_at() -> datetime | None:
  """Parse the current managed runtime's QMT launch boundary as naive UTC."""

  raw = os.environ.get("QMT_AGENT_LAUNCH_STARTED_AT", "").strip()
  if not raw:
    return None
  try:
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
  except (TypeError, ValueError):
    return None
  return _naive_utc(parsed)


def qmt_heartbeat_matches_current_launch(updated_at: datetime | None) -> bool:
  """Return whether a heartbeat may represent this managed QMT launch.

  Runtimes that do not publish launch metadata keep the established heartbeat
  behavior. Explicit managed states fail closed: BLOCKED/NOT_REQUESTED never
  consume QMT heartbeats, while LAUNCH_ALLOWED requires a valid launch boundary
  and a heartbeat written at or after it.
  """

  state = qmt_agent_launch_state()
  if state in {"BLOCKED", "NOT_REQUESTED"}:
    return False
  if state != "LAUNCH_ALLOWED":
    return True
  launch_started_at = qmt_agent_launch_started_at()
  if launch_started_at is None or updated_at is None:
    return False
  try:
    return _naive_utc(updated_at) >= launch_started_at
  except (AttributeError, TypeError, ValueError):
    return False
