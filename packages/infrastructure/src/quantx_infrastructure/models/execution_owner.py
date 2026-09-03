"""Persistence-side validation for the closed execution owner contract.

The wire ``execution_mode`` field is deliberately not used here.  Durable
facts carry a separate, canonical upper-case execution ``environment``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import event
from sqlalchemy import inspect as sa_inspect

EXECUTION_OWNER_TYPES = (
  "STRATEGY_RUN",
  "T_ASSISTANT_EXECUTION",
  "ENTRY_PLAN",
  "BOARD_ASSISTANT_EXECUTION",
  "EXIT_PLAN",
  "MANUAL_COMMAND",
)
EXECUTION_ENVIRONMENTS = ("PAPER", "LIVE", "BACKTEST")
OWNER_ENVIRONMENT_FIELDS = ("owner_type", "owner_id", "environment")
SOURCE_EXECUTION_FIELDS = (
  "source_execution_owner_type",
  "source_execution_owner_id",
  "source_execution_environment",
  "environment",
)


def validate_owner_environment(
  owner_type: Any,
  owner_id: Any,
  environment: Any,
  *,
  allow_none: bool = False,
) -> tuple[str | None, str | None, str | None]:
  """Validate and return canonical values for a durable execution fact.

  ``allow_none`` exists only for the generic confirmation-challenge table,
  where non-execution control challenges intentionally have no owner.  It is
  never used by the public intent/order/event facts.
  """

  if allow_none and owner_type is None and owner_id is None and environment is None:
    return None, None, None
  if owner_type is None or owner_id is None or environment is None:
    raise ValueError("OWNER_ENVIRONMENT_REQUIRED")
  normalized_type = getattr(owner_type, "value", owner_type)
  normalized_environment = getattr(environment, "value", environment)
  if (
    not isinstance(normalized_type, str)
    or normalized_type not in EXECUTION_OWNER_TYPES
  ):
    raise ValueError("OWNER_TYPE_INVALID")
  if not isinstance(owner_id, str) or not owner_id or owner_id != owner_id.strip():
    raise ValueError("OWNER_ID_INVALID")
  if len(owner_id) > 128:
    raise ValueError("OWNER_ID_INVALID")
  if any(ord(character) <= 31 or ord(character) == 127 for character in owner_id):
    raise ValueError("OWNER_ID_INVALID")
  if (
    not isinstance(normalized_environment, str)
    or normalized_environment not in EXECUTION_ENVIRONMENTS
  ):
    raise ValueError("EXECUTION_ENVIRONMENT_INVALID")
  return normalized_type, owner_id, normalized_environment


def register_identity_immutability(
  model: type,
  *,
  fields: tuple[str, ...] = OWNER_ENVIRONMENT_FIELDS,
) -> None:
  """Reject ORM mutations to a persisted execution identity.

  PostgreSQL enforces the same rule with the adoption migration's trigger.
  This mapper guard keeps SQLite/unit-test ORM paths fail-closed as well.
  """

  @event.listens_for(model, "before_update", propagate=False)
  def _reject_identity_update(mapper, connection, target) -> None:
    state = sa_inspect(target)
    if not state.persistent:
      return
    for field in fields:
      if state.attrs[field].history.has_changes():
        raise ValueError("OWNER_ENVIRONMENT_IMMUTABLE")


__all__ = [
  "EXECUTION_ENVIRONMENTS",
  "EXECUTION_OWNER_TYPES",
  "OWNER_ENVIRONMENT_FIELDS",
  "SOURCE_EXECUTION_FIELDS",
  "register_identity_immutability",
  "validate_owner_environment",
]
