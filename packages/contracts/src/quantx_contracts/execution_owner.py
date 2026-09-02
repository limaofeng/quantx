"""Canonical execution-owner identity shared by every QuantX boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping


class ExecutionOwnerType(str, Enum):
  """The closed set of execution-owner kinds."""

  STRATEGY_RUN = "STRATEGY_RUN"
  T_ASSISTANT_EXECUTION = "T_ASSISTANT_EXECUTION"
  ENTRY_PLAN = "ENTRY_PLAN"
  BOARD_ASSISTANT_EXECUTION = "BOARD_ASSISTANT_EXECUTION"
  EXIT_PLAN = "EXIT_PLAN"
  MANUAL_COMMAND = "MANUAL_COMMAND"


class ExecutionEnvironment(str, Enum):
  """Execution namespace for a durable trading fact."""

  PAPER = "PAPER"
  LIVE = "LIVE"
  BACKTEST = "BACKTEST"


@dataclass(frozen=True, slots=True, init=False)
class ExecutionOwnerRef:
  """Validated owner identity for an intent and its durable descendants.

  The value object validates structure only.  The application layer must
  still prove that the referenced owner exists and belongs to the requested
  execution environment before routing a command.
  """

  owner_type: ExecutionOwnerType
  owner_id: str

  def __init__(
    self,
    owner_type: ExecutionOwnerType | str,
    owner_id: str,
  ) -> None:
    if not isinstance(owner_type, (ExecutionOwnerType, str)):
      raise ValueError("OWNER_TYPE_INVALID")
    try:
      normalized_owner_type = ExecutionOwnerType(owner_type)
    except (TypeError, ValueError) as exc:
      raise ValueError("OWNER_TYPE_INVALID") from exc

    if owner_id is None:
      raise ValueError("OWNER_ID_MISSING")
    if not isinstance(owner_id, str):
      raise ValueError("OWNER_ID_INVALID")
    if owner_id == "":
      raise ValueError("OWNER_ID_MISSING")
    if owner_id != owner_id.strip():
      raise ValueError("OWNER_ID_INVALID")
    if any(ord(character) <= 31 or ord(character) == 127 for character in owner_id):
      raise ValueError("OWNER_ID_INVALID")
    if len(owner_id) > 128:
      raise ValueError("OWNER_ID_INVALID")

    object.__setattr__(self, "owner_type", normalized_owner_type)
    object.__setattr__(self, "owner_id", owner_id)

  @classmethod
  def from_mapping(cls, value: Mapping[str, object]) -> "ExecutionOwnerRef":
    """Construct an owner from an untrusted mapping projection."""

    if not isinstance(value, Mapping):
      raise ValueError("OWNER_TYPE_INVALID")
    return cls(
      owner_type=value.get("owner_type"),
      owner_id=value.get("owner_id"),
    )

  @classmethod
  def strategy_run(cls, run_id: str) -> "ExecutionOwnerRef":
    """Build the owner reference for an ordinary strategy run."""

    return cls(ExecutionOwnerType.STRATEGY_RUN, run_id)

  @classmethod
  def manual_command(cls, command_id: str) -> "ExecutionOwnerRef":
    """Build the owner reference for a manually issued command."""

    return cls(ExecutionOwnerType.MANUAL_COMMAND, command_id)

  def require_matches(
    self,
    owner_type: ExecutionOwnerType | str,
    owner_id: str,
  ) -> "ExecutionOwnerRef":
    """Return this value only when a target identity is exactly equal."""

    try:
      expected = type(self)(owner_type, owner_id)
    except (TypeError, ValueError) as exc:
      raise ValueError("OWNER_CONFLICT") from exc
    if self != expected:
      raise ValueError("OWNER_CONFLICT")
    return self

  def to_dict(self) -> dict[str, str]:
    """Return the stable two-field wire/persistence projection."""

    return {
      "owner_type": self.owner_type.value,
      "owner_id": self.owner_id,
    }


__all__ = [
  "ExecutionEnvironment",
  "ExecutionOwnerRef",
  "ExecutionOwnerType",
]
