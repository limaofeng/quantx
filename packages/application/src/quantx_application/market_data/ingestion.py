"""Deterministic ingestion transitions; storage and clocks are supplied by callers."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any

MAX_EXECUTIONS = 4
RETRY_WINDOW_SECONDS = 300
RETRY_DELAYS = (5, 30, 120)
PHASES = ("VALIDATE", "WRITE", "READBACK", "VERIFIED")


class IngestionEvidenceConflict(RuntimeError):
  """A frozen manifest or acknowledged block changed under the same identity."""


def transition(
  previous: dict[str, Any] | None,
  action: str,
  values: dict[str, Any],
  now: datetime,
) -> dict[str, Any]:
  state = (
    deepcopy(previous)
    if previous
    else {
      "version": 1,
      "phase": "VALIDATE",
      "attempt": 1,
      "executions": 0,
      "stage_started_at": now.isoformat(),
      "next_retry_at": None,
      "reason_code": None,
      "blocked": False,
      "manifest_hash": None,
      "checkpoints": {},
      "write_result": None,
      "failures": [],
      "last_progress_at": None,
    }
  )
  if state["version"] != 1:
    raise ValueError("unsupported ingestion progress version")
  if action == "begin":
    if state["next_retry_at"] and now < datetime.fromisoformat(state["next_retry_at"]):
      raise RuntimeError("ingestion retry is not due")
    if state["blocked"]:
      raise RuntimeError("ingestion requires explicit recovery")
    if _exhausted(state, now):
      state.update(
        blocked=True,
        reason_code=state["reason_code"] or "INGESTION_RETRY_BUDGET_EXHAUSTED",
        next_retry_at=None,
      )
    else:
      state["executions"] += 1
      state["next_retry_at"] = None
  elif action == "manifest":
    digest = values["sha256"]
    if state["manifest_hash"] not in (None, digest):
      raise IngestionEvidenceConflict("MANIFEST_CHANGED")
    if state["manifest_hash"] is None:
      state["manifest_hash"] = digest
      state["last_progress_at"] = now.isoformat()
  elif action == "advance":
    phase = values["phase"]
    if phase != state["phase"]:
      if PHASES.index(phase) != PHASES.index(state["phase"]) + 1:
        raise ValueError("invalid ingestion phase transition")
      if not state["manifest_hash"]:
        raise IngestionEvidenceConflict("MANIFEST_NOT_FROZEN")
      if phase == "READBACK":
        state["write_result"] = values["write_result"]
      state.update(
        phase=phase,
        executions=1,
        stage_started_at=now.isoformat(),
        next_retry_at=None,
        reason_code=None,
        last_progress_at=now.isoformat(),
      )
  elif action in {"check", "checkpoint"}:
    if state["phase"] != "WRITE" or not state["manifest_hash"]:
      raise ValueError("write checkpoint requires a frozen write phase")
    block = str(values["block"])
    checkpoint = state["checkpoints"].get(block)
    evidence = {"sha256": values["sha256"], "rows": values["rows"]}
    if checkpoint is not None and any(
      checkpoint[k] != value for k, value in evidence.items()
    ):
      raise IngestionEvidenceConflict("WRITE_BLOCK_CHANGED")
    if action == "checkpoint" and checkpoint is None:
      state["checkpoints"][block] = {**evidence, "attempt": state["attempt"]}
      state["last_progress_at"] = now.isoformat()
  elif action == "defer":
    state["reason_code"] = values["reason_code"]
    state["diagnostic"] = values.get("diagnostic", {})
    state["failures"].append(
      {
        "phase": state["phase"],
        "attempt": state["attempt"],
        "execution": state["executions"],
        "reason_code": state["reason_code"],
        "at": now.isoformat(),
      }
    )
    state["blocked"] = bool(values.get("blocked")) or _exhausted(state, now)
    state["next_retry_at"] = (
      None
      if state["blocked"]
      else (
        now + timedelta(seconds=RETRY_DELAYS[max(0, state["executions"] - 1)])
      ).isoformat()
    )
  elif action == "resume":
    if not values.get("reason"):
      raise ValueError("explicit recovery requires a reason")
    state["failures"].append(
      {
        "action": "resume",
        "reason": values["reason"],
        "attempt": state["attempt"],
        "at": now.isoformat(),
      }
    )
    state.update(
      attempt=state["attempt"] + 1,
      executions=0,
      blocked=False,
      reason_code=None,
      next_retry_at=None,
      stage_started_at=now.isoformat(),
    )
  else:
    raise ValueError("unsupported ingestion progress action")
  return state


def _exhausted(state: dict[str, Any], now: datetime) -> bool:
  return (
    state["executions"] >= MAX_EXECUTIONS
    or (now - datetime.fromisoformat(state["stage_started_at"])).total_seconds()
    >= RETRY_WINDOW_SECONDS
  )
