"""Authoritative durable execution-owner classification for exit plans."""

from __future__ import annotations

from typing import Any, Mapping

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


def durable_exit_plan_owner_kind(record: Any) -> str:
  """Classify the only owner allowed by the canonical durable source matrix.

  The projection row and embedded domain template must describe the same plan.
  PAPER/LIVE ownership is derived only from the durable ``strategy_run_id``
  and source matrix. Legacy managed-runtime markers are migration inputs;
  they never hide an unbound manual plan from the global monitor.
  """

  plan_id = str(getattr(record, "plan_id", None) or "").strip()
  account_id = str(getattr(record, "account_id", None) or "").strip()
  instrument_code = str(
    getattr(record, "instrument_code", None) or ""
  ).strip().upper()
  source_type = str(getattr(record, "source_type", None) or "").strip().upper()
  run_id = str(getattr(record, "strategy_run_id", None) or "").strip()
  template = exit_plan_template(record)
  if not (
    plan_id
    and account_id
    and instrument_code
    and source_type
    and str(template.get("plan_id") or "").strip() == plan_id
    and str(template.get("account_id") or "").strip() == account_id
    and str(template.get("instrument_code") or "").strip().upper()
    == instrument_code
    and str(template.get("source_type") or "").strip().upper() == source_type
    and str(template.get("run_id") or "").strip() == run_id
  ):
    return INVALID_OWNER

  if not run_id:
    return (
      MONITOR_OWNER
      if source_type in MONITOR_EXIT_PLAN_SOURCE_TYPES
      else INVALID_OWNER
    )
  if (
    source_type in RUNTIME_EXIT_PLAN_SOURCE_TYPES
    and not has_managed_runtime_command_marker(record)
  ):
    return RUNTIME_BOOK_OWNER
  return INVALID_OWNER
