"""Compatibility helpers for durable Agents SDK approval state."""

from __future__ import annotations

import json
from typing import Any

from agents import RunState


def serialize_run_state(state: Any) -> dict[str, Any]:
  value = state.to_json()
  if isinstance(value, str):
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
      raise TypeError("Agents SDK RunState JSON must be an object")
    return parsed
  if isinstance(value, dict):
    return value
  raise TypeError("Agents SDK RunState returned an unsupported JSON value")


async def deserialize_run_state(
  initial_agent: Any,
  payload: dict[str, Any],
  *,
  context_override: dict[str, Any],
) -> Any:
  """Restore the SDK state with the original top-level agent.

  RunState.from_json is asynchronous and resolves persisted tool identities from
  the supplied agent.  A small mapping context keeps approvals resumable without
  serializing database sessions, credentials, or other process-local objects.
  """

  try:
    return await RunState.from_json(
      initial_agent,
      payload,
      context_override=context_override,
      strict_context=True,
    )
  except (TypeError, ValueError) as exc:
    raise ValueError("AI_RUN_STATE_INCOMPATIBLE") from exc


def state_interruptions(state: Any) -> list[Any]:
  direct = getattr(state, "interruptions", None)
  if isinstance(direct, list):
    return direct
  getter = getattr(state, "get_interruptions", None)
  if callable(getter):
    value = getter()
    return list(value or [])
  return []


def interruption_details(interruption: Any) -> tuple[str, str, dict[str, Any]]:
  raw = getattr(interruption, "raw_item", None)
  tool_name = (
    getattr(interruption, "tool_name", None)
    or getattr(interruption, "name", None)
    or getattr(raw, "name", None)
    or getattr(raw, "tool_name", None)
    or ""
  )
  call_id = (
    getattr(interruption, "call_id", None)
    or getattr(raw, "call_id", None)
    or getattr(raw, "id", None)
    or ""
  )
  arguments = (
    getattr(interruption, "arguments", None) or getattr(raw, "arguments", None) or {}
  )
  if isinstance(arguments, str):
    try:
      arguments = json.loads(arguments)
    except json.JSONDecodeError:
      arguments = {}
  if not isinstance(arguments, dict):
    arguments = {}
  return str(tool_name), str(call_id), dict(arguments)
