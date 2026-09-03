"""Authoritative durable execution-owner classification for exit plans."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef, ExecutionOwnerType

MANUAL_PLAN_SOURCE = "MANUAL_POSITION"
MANUAL_LIQUIDATION_SOURCE = "MANUAL_LIQUIDATION"
RUNTIME_EXIT_PLAN_SOURCE_TYPES = frozenset(
  {
    "T_TRADE_BATCH",
    "LIMIT_UP_BOARD",
    "FIRST_BOARD_PROMOTION_V2",
    "ENTRY_PLAN",
  }
)
MONITOR_EXIT_PLAN_SOURCE_TYPES = frozenset(
  {MANUAL_PLAN_SOURCE, MANUAL_LIQUIDATION_SOURCE}
)
MANAGED_RUNTIME_COMMAND_ID_KEY = "managed_runtime_command_id"
MANAGED_RUNTIME_BINDING_PENDING = "MANAGED_RUNTIME_BINDING_PENDING"
MANAGED_RUNTIME_ENABLE_PENDING = "MANAGED_RUNTIME_ENABLE_PENDING"

MONITOR_OWNER = "MONITOR"
RUNTIME_BOOK_OWNER = "RUNTIME_BOOK"
MANAGED_EXIT_STRATEGY_OWNER = "MANAGED_EXIT_STRATEGY"
INVALID_OWNER = "INVALID"


def _mapping(value: Any) -> Mapping[str, Any]:
  return value if isinstance(value, Mapping) else {}


def exit_plan_template(record: Any) -> Mapping[str, Any]:
  return _mapping(_mapping(getattr(record, "plan_state", None)).get("template"))


def exit_plan_template_metadata(record: Any) -> Mapping[str, Any]:
  return _mapping(exit_plan_template(record).get("metadata"))


def has_managed_runtime_command_marker(record: Any) -> bool:
  return MANAGED_RUNTIME_COMMAND_ID_KEY in exit_plan_template_metadata(record)


def managed_runtime_command_id(record: Any) -> str:
  return str(
    exit_plan_template_metadata(record).get(MANAGED_RUNTIME_COMMAND_ID_KEY) or ""
  ).strip()


def durable_exit_plan_source_binding(
  record: Any,
) -> Optional[tuple[ExecutionOwnerRef, ExecutionEnvironment]]:
  """Return the source owner/environment proved by an ExitPlan row.

  The source matrix is intentionally finite.  ``strategy_run_id`` is only a
  nullable witness: it can agree with a STRATEGY_RUN source, but it can never
  classify a row by itself.  Template fields are checked as forward evidence
  for the same matrix and never as an owner fallback.
  """

  plan_id = str(getattr(record, "plan_id", None) or "").strip()
  account_id = str(getattr(record, "account_id", None) or "").strip()
  instrument_code = str(
    getattr(record, "instrument_code", None) or ""
  ).strip().upper()
  source_type = str(getattr(record, "source_type", None) or "").strip().upper()
  source_id = str(getattr(record, "source_id", None) or "").strip()
  group_id = str(getattr(record, "group_id", None) or "").strip()
  template = exit_plan_template(record)
  template_plan_id = str(template.get("plan_id") or "").strip()
  template_account_id = str(template.get("account_id") or "").strip()
  template_instrument_code = str(
    template.get("instrument_code") or ""
  ).strip().upper()
  template_source_type = str(template.get("source_type") or "").strip().upper()
  template_source_id = str(template.get("source_id") or "").strip()
  template_run_id = str(template.get("run_id") or "").strip()
  if not (
    plan_id
    and account_id
    and instrument_code
    and source_type
    and source_id
    and template_plan_id == plan_id
    and template_account_id == account_id
    and template_instrument_code == instrument_code
    and template_source_type == source_type
    and template_source_id == source_id
  ):
    return None
  try:
    owner = ExecutionOwnerRef(
      ExecutionOwnerType(
        str(getattr(record, "source_execution_owner_type", "") or "").upper()
      ),
      str(getattr(record, "source_execution_owner_id", "") or ""),
    )
    source_environment = ExecutionEnvironment(
      str(getattr(record, "source_execution_environment", "") or "").upper()
    )
    environment = ExecutionEnvironment(
      str(getattr(record, "environment", "") or "").upper()
    )
  except (TypeError, ValueError):
    return None
  if source_environment is not environment:
    return None
  witness_run_id = str(getattr(record, "strategy_run_id", None) or "").strip()
  if witness_run_id and (
    owner.owner_type is not ExecutionOwnerType.STRATEGY_RUN
    or owner.owner_id != witness_run_id
  ):
    return None
  if source_type in RUNTIME_EXIT_PLAN_SOURCE_TYPES:
    if (
      owner.owner_type is not ExecutionOwnerType.STRATEGY_RUN
      or not template_run_id
      or owner.owner_id != template_run_id
      or (witness_run_id and witness_run_id != owner.owner_id)
    ):
      return None
  elif source_type == MANUAL_PLAN_SOURCE:
    if (
      owner.owner_type is not ExecutionOwnerType.MANUAL_COMMAND
      or not owner.owner_id
      or template_run_id
      or witness_run_id
    ):
      return None
  elif source_type == MANUAL_LIQUIDATION_SOURCE:
    if (
      owner.owner_type is not ExecutionOwnerType.MANUAL_COMMAND
      or not group_id
      or owner.owner_id != group_id
      or template_run_id
      or witness_run_id
    ):
      return None
  else:
    return None
  return owner, environment


def durable_exit_plan_owner_kind(record: Any) -> str:
  """Classify the only owner allowed by the canonical durable source matrix.

  The projection row, source execution triple, and embedded domain template
  must describe the same plan.  The source execution triple is the only owner
  authority; ``strategy_run_id`` is only a nullable consistency witness.
  Legacy managed-runtime markers are migration inputs and never repair an
  incomplete source binding.
  """

  source_type = str(getattr(record, "source_type", None) or "").strip().upper()
  binding = durable_exit_plan_source_binding(record)
  if binding is None:
    return INVALID_OWNER
  owner, _environment = binding
  if owner.owner_type is ExecutionOwnerType.MANUAL_COMMAND:
    return MONITOR_OWNER
  if source_type not in RUNTIME_EXIT_PLAN_SOURCE_TYPES:
    return INVALID_OWNER
  return (
    MANAGED_EXIT_STRATEGY_OWNER
    if has_managed_runtime_command_marker(record)
    else RUNTIME_BOOK_OWNER
  )
