"""Read-only rehearsal for the protocol-1.2 maintenance window.

The rehearsal is a bounded preflight. It observes the existing P0 audit,
captures aggregate inbox facts, and walks a deterministic in-memory state
sequence. It never stops a component, creates a backup, deploys code, or
changes a database row.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from quantx_contracts.agent import PROTOCOL_VERSION as CONTRACT_PROTOCOL_VERSION
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from quantx_infrastructure.services import t_assistant_p0_audit as p0_audit
from quantx_infrastructure.services import t_assistant_p1_reconcile as p1_reconcile

P1_CUTOVER_REHEARSAL_SCHEMA_VERSION = 1
REHEARSAL_SCOPE = "P1_PROTOCOL_12_CUTOVER_REHEARSAL"
LEGACY_PROTOCOL_VERSION = "1.1"
RUNNING_PROTOCOL_VERSION = LEGACY_PROTOCOL_VERSION
CURRENT_PROTOCOL_VERSION = RUNNING_PROTOCOL_VERSION
TARGET_PROTOCOL_VERSION = CONTRACT_PROTOCOL_VERSION

REHEARSAL_STATE_SEQUENCE = (
  "BLOCK_NEW_COMMANDS",
  "DRAIN_INBOX",
  "AUDIT",
  "BACKUP",
  "ATOMIC_DEPLOY",
  "POST_DEPLOY_RECONCILE",
  "RESUME",
)

P0_REQUIRED_TABLES_MISSING = "P0_REQUIRED_TABLES_MISSING"
P0_READINESS_NOT_MET = "P0_READINESS_NOT_MET"
P0_READINESS_UNKNOWN = "P0_READINESS_UNKNOWN"
P0_AUDIT_UNAVAILABLE = "P0_AUDIT_UNAVAILABLE"
CONFIGURATION_MISSING = "CONFIGURATION_MISSING"
CONFIGURATION_STATUS_UNKNOWN = "CONFIGURATION_STATUS_UNKNOWN"
UNSETTLED_AGENT_INBOX = "UNSETTLED_AGENT_INBOX"
INBOX_STATUS_UNKNOWN = "INBOX_STATUS_UNKNOWN"
PROTOCOL_STATUS_UNKNOWN = "PROTOCOL_STATUS_UNKNOWN"
PROTOCOL_VERSION_UNSUPPORTED = "PROTOCOL_VERSION_UNSUPPORTED"
READ_ONLY_ROLLBACK_FAILED = "READ_ONLY_ROLLBACK_FAILED"

PROTOCOL_11_OUTBOX_NOT_DRAINED = "PROTOCOL_11_OUTBOX_NOT_DRAINED"
PROTOCOL_11_RESULT_UNKNOWN = "PROTOCOL_11_RESULT_UNKNOWN"
TERMINAL_EXIT_PLAN_UNROUTED_INTENT = (
  p1_reconcile.TERMINAL_EXIT_PLAN_UNROUTED_INTENT
)
TERMINAL_EXIT_INTENT_GATE_UNKNOWN = "TERMINAL_EXIT_INTENT_GATE_UNKNOWN"

_UNSET = object()
_TERMINAL_INBOX_STATUSES = ("PROCESSED", "SUPERSEDED")
LEGACY_REQUIRED_TABLES = tuple(
  "strategy_trade_intents" if table == "trade_intents"
  else "strategy_order_correlations" if table == "order_correlations"
  else table
  for table in p0_audit.REQUIRED_TABLES
)
_TARGET_VERSION_PATTERN = re.compile(
  r"(?:TARGET_PROTOCOL_VERSION|PROTOCOL_VERSION)\s*=\s*['\"]([^'\"]+)['\"]"
)

_PROTOCOL_CHECK_REASONS = {
  "P0_QUEUED_PROTOCOL_11_T_COMMAND": PROTOCOL_11_OUTBOX_NOT_DRAINED,
  "P0_UNKNOWN_RESULT_PROTOCOL_11_T_COMMAND": PROTOCOL_11_RESULT_UNKNOWN,
}
_SAFE_REASON_CODES = frozenset(
  {
    P0_REQUIRED_TABLES_MISSING,
    P0_READINESS_NOT_MET,
    P0_READINESS_UNKNOWN,
    P0_AUDIT_UNAVAILABLE,
    CONFIGURATION_MISSING,
    CONFIGURATION_STATUS_UNKNOWN,
    UNSETTLED_AGENT_INBOX,
    INBOX_STATUS_UNKNOWN,
    PROTOCOL_STATUS_UNKNOWN,
    PROTOCOL_VERSION_UNSUPPORTED,
    READ_ONLY_ROLLBACK_FAILED,
    PROTOCOL_11_OUTBOX_NOT_DRAINED,
    PROTOCOL_11_RESULT_UNKNOWN,
    TERMINAL_EXIT_PLAN_UNROUTED_INTENT,
    TERMINAL_EXIT_INTENT_GATE_UNKNOWN,
    *(
      spec.code
      for spec in p0_audit.AUDIT_CHECK_SPECS
      if spec.severity == "BLOCKER"
    ),
  }
)


def _result_scalar(result: Any) -> Any:
  """Read a scalar from SQLAlchemy or a deliberately small test double."""

  for method_name in ("scalar_one_or_none", "scalar", "scalar_one"):
    method = getattr(result, method_name, None)
    if callable(method):
      try:
        return method()
      except Exception:  # noqa: BLE001 - fail closed at the report boundary
        continue
  mappings = getattr(result, "mappings", None)
  if callable(mappings):
    mapped = mappings()
    first = getattr(mapped, "first", None)
    if callable(first):
      row = first()
      if isinstance(row, Mapping):
        return row.get("count_value", next(iter(row.values()), None))
  return None


def _strict_count(value: Any) -> int | None:
  """Normalize an aggregate count without treating malformed data as zero."""

  if isinstance(value, bool) or value is None:
    return None
  if isinstance(value, int):
    return value if value >= 0 else None
  if isinstance(value, str) and value.isdigit():
    return int(value)
  return None


def _check(report: Mapping[str, Any], code: str) -> Mapping[str, Any] | None:
  checks = report.get("checks")
  if not isinstance(checks, (list, tuple)):
    return None
  for item in checks:
    if isinstance(item, Mapping) and item.get("code") == code:
      return item
  return None


def _check_count(report: Mapping[str, Any], code: str) -> int | None:
  item = _check(report, code)
  return _strict_count(item.get("count")) if item is not None else None


def _safe_reason_code(value: Any) -> str:
  text_value = str(value or "")
  return (
    text_value if text_value in _SAFE_REASON_CODES else P0_READINESS_NOT_MET
  )


def _missing_table_count(report: Mapping[str, Any]) -> int | None:
  item = _check(report, P0_REQUIRED_TABLES_MISSING)
  if item is not None:
    return _strict_count(item.get("count"))
  missing = report.get("missingTables")
  if isinstance(missing, (list, tuple, set, frozenset)):
    return len(missing)
  if isinstance(report.get("requiredTables"), (list, tuple, set, frozenset)):
    return 0
  return None


def _status_for_count(value: int | None) -> str:
  if value is None:
    return "UNKNOWN"
  return "PASSED" if value == 0 else "BLOCKED"


def _status_for_presence(value: int | None) -> str:
  if value is None:
    return "UNKNOWN"
  return "BLOCKED" if value == 0 else "PASSED"


def _safe_protocol(
  value: Any,
  *,
  accepted_versions: set[str] | None = None,
) -> str | None:
  """Accept only an exact protocol string for a configuration fact."""

  accepted = accepted_versions or {
    CURRENT_PROTOCOL_VERSION,
    TARGET_PROTOCOL_VERSION,
  }
  return (
    value
    if isinstance(value, str)
    and value in accepted
    else None
  )


def _target_source_version(value: Any) -> tuple[str | None, bool]:
  """Extract one target version without exposing source paths or source text."""

  if value is _UNSET:
    return None, False
  if isinstance(value, Mapping):
    candidates = [
      value.get(key)
      for key in ("protocol_version", "protocolVersion", "target_protocol", "version")
      if key in value
    ]
    if not candidates or any(item != candidates[0] for item in candidates[1:]):
      return None, True
    value = candidates[0]
  elif isinstance(value, Path):
    try:
      source = value.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
      return None, True
    match = _TARGET_VERSION_PATTERN.search(source)
    value = match.group(1) if match else None
  return (value if isinstance(value, str) else None), True


async def _unsettled_inbox_count(connection: AsyncConnection) -> int | None:
  """Count inbox rows that are not in one of the two terminal states."""

  terminal_statuses = ", ".join(f"'{item}'" for item in _TERMINAL_INBOX_STATUSES)
  result = await connection.execute(
    text(
      """
      SELECT COUNT(*) AS count_value
      FROM agent_report_inbox
      WHERE processing_status IS NULL
         OR UPPER(CAST(processing_status AS TEXT)) NOT IN (__terminal_statuses__)
      """.replace("__terminal_statuses__", terminal_statuses)
    )
  )
  return _strict_count(_result_scalar(result))


async def _legacy_0045_audit_connection(
  connection: AsyncConnection,
) -> dict[str, Any]:
  """Audit only the explicitly supported pre-0046 schema shape.

  This is a cutover input, not a runtime compatibility path.  The target P0
  audit continues to query only trade_intents/order_correlations and protocol
  1.2; this function exists so the read-only rehearsal can drain a real 0045
  database before 0046 renames the tables.
  """

  table_names = ", ".join(f"'{name}'" for name in LEGACY_REQUIRED_TABLES)
  table_result = await connection.execute(
    text(
      """
      SELECT table_name
      FROM information_schema.tables
      WHERE table_schema = 'public'
        AND table_name IN (__legacy_table_names__)
      """.replace("__legacy_table_names__", table_names)
    )
  )
  existing_tables = {
    str(row.get("table_name"))
    for row in table_result.mappings().all()
    if row.get("table_name") is not None
  }
  missing_tables = [
    table for table in LEGACY_REQUIRED_TABLES if table not in existing_tables
  ]
  checks: list[dict[str, Any]] = []
  if missing_tables:
    checks.append(
      {
        "code": P0_REQUIRED_TABLES_MISSING,
        "severity": "BLOCKER",
        "count": len(missing_tables),
        "blocksP1": True,
        "missingTables": missing_tables,
      }
    )
    return {
      "schemaVersion": p0_audit.P0_SCHEMA_VERSION,
      "scope": p0_audit.AUDIT_SCOPE,
      "currentProtocol": LEGACY_PROTOCOL_VERSION,
      "targetProtocol": TARGET_PROTOCOL_VERSION,
      "readyForP1": False,
      "summary": {"blockerCount": 1, "warningCount": 0, "checkCount": 1},
      "checks": checks,
      "requiredTables": list(LEGACY_REQUIRED_TABLES),
      "schema": "LEGACY_0045",
    }

  async def count(query: str) -> int | None:
    try:
      return _strict_count(_result_scalar(await connection.execute(text(query))))
    except Exception:  # noqa: BLE001 - aggregate-only fail-closed result
      return None

  legacy_t_predicate = """
    (
      UPPER(COALESCE(command.payload ->> 't_trade_role', '')) IN ('ENTRY', 'EXIT')
      OR UPPER(COALESCE(command.payload ->> 'strategy_name', '')) =
        'ASHAREINTRADAYTASSISTANTSTRATEGY'
      OR EXISTS (
        SELECT 1
        FROM strategy_order_correlations AS correlation
        WHERE correlation.client_order_id = command.client_order_id
          AND UPPER(COALESCE(correlation.t_trade_role, '')) IN ('ENTRY', 'EXIT')
      )
    )
  """
  checks.extend(
    [
      {
        "code": "P0_ENABLED_LEGACY_CONFIG_COUNT",
        "severity": "FACT",
        "count": await count(
          "SELECT COUNT(*) AS count_value "
          "FROM t_trade_global_configs WHERE enabled IS TRUE"
        ),
      },
      {
        "code": "P0_QUEUED_PROTOCOL_11_T_COMMAND",
        "severity": "BLOCKER",
        "count": await count(
          "SELECT COUNT(*) AS count_value "
          "FROM trade_command_outbox AS command "
          f"WHERE {legacy_t_predicate} "
          "AND UPPER(command.delivery_status) = 'QUEUED'"
        ),
      },
      {
        "code": "P0_UNKNOWN_RESULT_PROTOCOL_11_T_COMMAND",
        "severity": "BLOCKER",
        "count": await count(
          "SELECT COUNT(*) AS count_value "
          "FROM trade_command_outbox AS command "
          f"WHERE {legacy_t_predicate} "
          "AND UPPER(command.delivery_status) IN "
          "('DELIVERED', 'ACKNOWLEDGED', 'RECONCILE_REQUIRED') "
          "AND NOT EXISTS ("
          "SELECT 1 FROM pending_trade_orders AS pending "
          "WHERE pending.client_order_id = command.client_order_id "
          "AND UPPER(pending.status) IN "
          "('FILLED', 'CANCELLED', 'CANCELED', 'REJECTED', 'EXPIRED')"
          ")"
        ),
      },
    ]
  )
  return {
    "schemaVersion": p0_audit.P0_SCHEMA_VERSION,
    "scope": p0_audit.AUDIT_SCOPE,
    "currentProtocol": LEGACY_PROTOCOL_VERSION,
    "targetProtocol": TARGET_PROTOCOL_VERSION,
    "readyForP1": all(
      item["count"] == 0
      or (item["code"] == "P0_ENABLED_LEGACY_CONFIG_COUNT" and item["count"] == 1)
      for item in checks
    ),
    "summary": {
      "blockerCount": sum(
        1
        for item in checks
        if item["severity"] == "BLOCKER" and item["count"] not in (None, 0)
      ),
      "warningCount": 0,
      "checkCount": len(checks),
    },
    "checks": checks,
    "requiredTables": list(LEGACY_REQUIRED_TABLES),
    "schema": "LEGACY_0045",
  }


def _reason_for_p0_check(code: str) -> str:
  return _PROTOCOL_CHECK_REASONS.get(code, code)


def _p0_reasons(report: Mapping[str, Any], reasons: set[str]) -> None:
  """Project only stable P0 blocker codes into the rehearsal result."""

  missing_count = _missing_table_count(report)
  if missing_count is None:
    reasons.add(P0_READINESS_UNKNOWN)
  elif missing_count:
    reasons.add(P0_REQUIRED_TABLES_MISSING)

  checks = report.get("checks")
  if not isinstance(checks, (list, tuple)):
    return
  for item in checks:
    if not isinstance(item, Mapping):
      continue
    if item.get("severity") != "BLOCKER":
      continue
    count = _strict_count(item.get("count"))
    if count is None:
      reasons.add(P0_READINESS_UNKNOWN)
    elif count:
      reasons.add(
        _safe_reason_code(
          _reason_for_p0_check(str(item.get("code") or P0_READINESS_NOT_MET))
        )
      )


def _rehearsal_projection() -> dict[str, Any]:
  """Return the fixed sequence with every step explicitly unperformed."""

  steps = [
    {
      "state": state,
      "source": "SIMULATION",
      "status": "SIMULATED",
      "simulated": True,
      "performed": False,
      "observed": False,
    }
    for state in REHEARSAL_STATE_SEQUENCE
  ]
  return {
    "mode": "REHEARSAL_ONLY",
    "simulated": True,
    "actualComponentStop": False,
    "actualBackup": False,
    "actualDeploy": False,
    "actualPostDeployReconcile": False,
    "stateSequence": list(REHEARSAL_STATE_SEQUENCE),
    "steps": steps,
  }


def _build_report(
  audit_report: Mapping[str, Any],
  *,
  unsettled_inbox_count: int | None,
  exit_intent_report: Mapping[str, Any] | None = None,
  configured_protocol: Any = _UNSET,
  target_contract_source: Any = _UNSET,
  extra_reasons: Iterable[str] = (),
  running_protocol: str = CURRENT_PROTOCOL_VERSION,
) -> dict[str, Any]:
  """Build an aggregate-only, deidentified rehearsal report."""

  reasons: set[str] = {
    _safe_reason_code(item) for item in extra_reasons if item
  }
  _p0_reasons(audit_report, reasons)

  missing_table_count = _missing_table_count(audit_report)
  p0_ready_value = audit_report.get("readyForP1", _UNSET)
  p0_ready = p0_ready_value if isinstance(p0_ready_value, bool) else None
  if p0_ready is None:
    reasons.add(P0_READINESS_UNKNOWN)
  elif not p0_ready and not reasons:
    reasons.add(P0_READINESS_NOT_MET)

  legacy_config_count = _check_count(
    audit_report,
    "P0_ENABLED_LEGACY_CONFIG_COUNT",
  )
  if legacy_config_count is None:
    reasons.add(CONFIGURATION_STATUS_UNKNOWN)
  elif legacy_config_count == 0:
    reasons.add(CONFIGURATION_MISSING)

  queued_count = _check_count(
    audit_report,
    "P0_QUEUED_PROTOCOL_11_T_COMMAND",
  )
  unknown_count = _check_count(
    audit_report,
    "P0_UNKNOWN_RESULT_PROTOCOL_11_T_COMMAND",
  )
  if queued_count is None:
    reasons.add(PROTOCOL_STATUS_UNKNOWN)
  elif queued_count:
    reasons.add(PROTOCOL_11_OUTBOX_NOT_DRAINED)
  if unknown_count is None:
    reasons.add(PROTOCOL_STATUS_UNKNOWN)
  elif unknown_count:
    reasons.add(PROTOCOL_11_RESULT_UNKNOWN)

  if unsettled_inbox_count is None:
    reasons.add(INBOX_STATUS_UNKNOWN)
  elif unsettled_inbox_count:
    reasons.add(UNSETTLED_AGENT_INBOX)

  exit_safe_count: int | None = None
  exit_blocked_count: int | None = None
  exit_safe_ids: list[str] = []
  exit_blocked_ids: list[str] = []
  if exit_intent_report is not None:
    exit_safe_count = _strict_count(exit_intent_report.get("safeExitIntentCount"))
    exit_blocked_count = _strict_count(
      exit_intent_report.get("blockedExitIntentCount")
    )
    raw_safe_ids = exit_intent_report.get("safeExitIntentIds")
    if isinstance(raw_safe_ids, (list, tuple, set, frozenset)):
      exit_safe_ids = sorted(
        {str(value) for value in raw_safe_ids if isinstance(value, str) and value}
      )
    raw_blocked_ids = exit_intent_report.get("blockedExitIntentIds")
    if isinstance(raw_blocked_ids, (list, tuple, set, frozenset)):
      exit_blocked_ids = sorted(
        {str(value) for value in raw_blocked_ids if isinstance(value, str) and value}
      )
    if exit_safe_count is None or exit_blocked_count is None:
      reasons.add(TERMINAL_EXIT_INTENT_GATE_UNKNOWN)
    elif exit_safe_count or exit_blocked_count:
      reasons.add(TERMINAL_EXIT_PLAN_UNROUTED_INTENT)
    if exit_intent_report.get("reasonCodes") and not (
      exit_safe_count or exit_blocked_count
    ):
      reasons.add(TERMINAL_EXIT_INTENT_GATE_UNKNOWN)

  raw_audit_current = audit_report.get("currentProtocol", _UNSET)
  audit_current = _safe_protocol(
    raw_audit_current,
    accepted_versions={running_protocol, TARGET_PROTOCOL_VERSION},
  )
  if audit_current != running_protocol:
    reasons.add(
      PROTOCOL_STATUS_UNKNOWN
      if audit_current is None
      else PROTOCOL_VERSION_UNSUPPORTED
    )

  raw_configured = (
    raw_audit_current
    if configured_protocol is _UNSET
    else configured_protocol
  )
  configured = _safe_protocol(
    raw_configured,
    accepted_versions={running_protocol, TARGET_PROTOCOL_VERSION},
  )
  if configured != running_protocol or raw_configured != running_protocol:
    reasons.add(
      PROTOCOL_STATUS_UNKNOWN
      if configured is None
      else PROTOCOL_VERSION_UNSUPPORTED
    )

  raw_audit_target = audit_report.get("targetProtocol", _UNSET)
  audit_target = _safe_protocol(raw_audit_target)
  if audit_target != TARGET_PROTOCOL_VERSION:
    reasons.add(
      PROTOCOL_STATUS_UNKNOWN
      if audit_target is None
      else PROTOCOL_VERSION_UNSUPPORTED
    )

  if target_contract_source is _UNSET:
    target_version, target_available = _target_source_version(_UNSET)
  else:
    target_version, target_available = _target_source_version(target_contract_source)
  if target_available and target_version != TARGET_PROTOCOL_VERSION:
    reasons.add(
      PROTOCOL_STATUS_UNKNOWN
      if target_version is None
      else PROTOCOL_VERSION_UNSUPPORTED
    )

  checks = [
    {
      "code": "P0_READINESS",
      "status": "UNKNOWN" if p0_ready is None else ("PASSED" if p0_ready else "BLOCKED"),
      "count": None if p0_ready is None else (0 if p0_ready else 1),
      "source": "OBSERVED_DB",
    },
    {
      "code": P0_REQUIRED_TABLES_MISSING,
      "status": _status_for_count(missing_table_count),
      "count": missing_table_count,
      "source": "OBSERVED_DB",
    },
    {
      "code": "P0_ENABLED_LEGACY_CONFIG_COUNT",
      "status": _status_for_presence(legacy_config_count),
      "count": legacy_config_count,
      "source": "OBSERVED_DB",
    },
    {
      "code": PROTOCOL_11_OUTBOX_NOT_DRAINED,
      "status": _status_for_count(queued_count),
      "count": queued_count,
      "source": "OBSERVED_DB",
    },
    {
      "code": PROTOCOL_11_RESULT_UNKNOWN,
      "status": _status_for_count(unknown_count),
      "count": unknown_count,
      "source": "OBSERVED_DB",
    },
    {
      "code": UNSETTLED_AGENT_INBOX,
      "status": _status_for_count(unsettled_inbox_count),
      "count": unsettled_inbox_count,
      "source": "OBSERVED_DB",
    },
    {
      "code": "CURRENT_PROTOCOL",
      "status": (
        "PASSED"
        if audit_current == running_protocol
        and configured == running_protocol
        else "UNKNOWN"
      ),
      "count": None,
      "source": "OBSERVED_CONFIGURATION",
    },
    {
      "code": "P0_AUDIT_TARGET_PROTOCOL",
      "status": (
        "PASSED"
        if audit_target == TARGET_PROTOCOL_VERSION
        else "UNKNOWN"
      ),
      "count": None,
      "source": "OBSERVED_P0_AUDIT",
    },
    {
      "code": "TARGET_CONTRACT_SOURCE",
      "status": (
        "NOT_AVAILABLE"
        if not target_available
        else "PASSED"
        if target_version == TARGET_PROTOCOL_VERSION
        else "UNKNOWN"
      ),
      "count": None,
      "source": "OBSERVED_SOURCE",
    },
    {
      "code": TERMINAL_EXIT_PLAN_UNROUTED_INTENT,
      "status": (
        "UNKNOWN"
        if exit_intent_report is not None
        and (exit_safe_count is None or exit_blocked_count is None)
        else "BLOCKED"
        if exit_intent_report is not None
        and (exit_safe_count or exit_blocked_count)
        else "PASSED"
        if exit_intent_report is not None
        else "NOT_CHECKED"
      ),
      "count": (
        None
        if exit_intent_report is None
        or exit_safe_count is None
        or exit_blocked_count is None
        else exit_safe_count + exit_blocked_count
      ),
      "safeCount": exit_safe_count,
      "blockedCount": exit_blocked_count,
      "safeIntentIds": exit_safe_ids,
      "blockedIntentIds": exit_blocked_ids,
      "source": "P1_RECONCILE_DETECTOR",
    },
  ]
  observed = {
    "p0Ready": p0_ready,
    "missingTableCount": missing_table_count,
    "legacyConfigCount": legacy_config_count,
    "queuedProtocol11OutboxCount": queued_count,
    "protocol11UnknownResultCount": unknown_count,
    "unsettledAgentInboxCount": unsettled_inbox_count,
    "p0CurrentProtocol": audit_current,
    "p0TargetProtocol": audit_target,
    "configuredProtocol": configured,
    "targetContractVersion": (
      target_version
      if target_available
      and target_version in {CURRENT_PROTOCOL_VERSION, TARGET_PROTOCOL_VERSION}
      else None
    ),
    "targetContractSourceAvailable": target_available,
    "terminalExitPlanUnroutedIntentCount": (
      None
      if exit_safe_count is None or exit_blocked_count is None
      else exit_safe_count + exit_blocked_count
    ),
    "terminalExitPlanUnroutedIntentSafeCount": exit_safe_count,
    "terminalExitPlanUnroutedIntentBlockedCount": exit_blocked_count,
    "safeExitIntentIds": exit_safe_ids,
    "blockedExitIntentIds": exit_blocked_ids,
  }
  return {
    "schemaVersion": P1_CUTOVER_REHEARSAL_SCHEMA_VERSION,
    "scope": REHEARSAL_SCOPE,
    "mode": "REHEARSAL_ONLY",
    "readyForCutover": not reasons,
    "actualCutover": False,
    "reasonCodes": sorted(reasons),
    "checks": checks,
    "observedDbFacts": observed,
    "observed": observed,
    "rehearsal": _rehearsal_projection(),
    "transaction": {
      "readOnly": True,
      "rolledBack": False,
    },
  }


async def inspect_connection(
  connection: AsyncConnection,
  *,
  configured_protocol: Any = _UNSET,
  target_contract_source: Any = _UNSET,
  legacy_schema: bool = False,
) -> dict[str, Any]:
  """Inspect one open connection without changing it."""

  try:
    audit_report = (
      await _legacy_0045_audit_connection(connection)
      if legacy_schema
      else await p0_audit.audit_connection(connection)
    )
  except Exception:  # noqa: BLE001 - no raw database errors cross this boundary
    return _build_report(
      {},
      unsettled_inbox_count=None,
      configured_protocol=configured_protocol,
      target_contract_source=target_contract_source,
      extra_reasons=(P0_AUDIT_UNAVAILABLE,),
      running_protocol=(
        LEGACY_PROTOCOL_VERSION if legacy_schema else CURRENT_PROTOCOL_VERSION
      ),
    )
  if not isinstance(audit_report, Mapping):
    return _build_report(
      {},
      unsettled_inbox_count=None,
      configured_protocol=configured_protocol,
      target_contract_source=target_contract_source,
      extra_reasons=(P0_AUDIT_UNAVAILABLE,),
      running_protocol=(
        LEGACY_PROTOCOL_VERSION if legacy_schema else CURRENT_PROTOCOL_VERSION
      ),
    )

  missing_count = _missing_table_count(audit_report)
  if missing_count:
    unsettled = None
  else:
    try:
      unsettled = await _unsettled_inbox_count(connection)
    except Exception:  # noqa: BLE001 - unknown inbox status is a blocker
      unsettled = None
  exit_intent_report: Mapping[str, Any] | None = None
  # Small test doubles do not expose a SQLAlchemy dialect.  Real connections
  # always do, and are the only connections on which the detector is needed.
  if getattr(connection, "dialect", None) is not None and not missing_count:
    try:
      exit_intent_report = (
        await p1_reconcile.inspect_terminal_exit_plan_unrouted_intents(
          connection,
          legacy_schema=legacy_schema,
        )
      )
    except Exception:  # noqa: BLE001 - readiness must fail closed
      exit_intent_report = {
        "safeExitIntentCount": None,
        "blockedExitIntentCount": None,
        "reasonCodes": [TERMINAL_EXIT_INTENT_GATE_UNKNOWN],
      }
  return _build_report(
    audit_report,
    unsettled_inbox_count=unsettled,
    exit_intent_report=exit_intent_report,
    configured_protocol=configured_protocol,
    target_contract_source=target_contract_source,
    running_protocol=(
      LEGACY_PROTOCOL_VERSION if legacy_schema else CURRENT_PROTOCOL_VERSION
    ),
  )


async def run_rehearsal(
  engine: AsyncEngine,
  *,
  configured_protocol: Any = _UNSET,
  target_contract_source: Any = _UNSET,
  legacy_schema: bool = False,
) -> dict[str, Any]:
  """Run a PostgreSQL read-only rehearsal and unconditionally roll it back."""

  report: dict[str, Any] | None = None
  read_only_applied = False
  rolled_back = False
  try:
    async with engine.connect() as connection:
      transaction = await connection.begin()
      try:
        await connection.execute(text("SET TRANSACTION READ ONLY"))
        read_only_applied = True
        report = await inspect_connection(
          connection,
          configured_protocol=configured_protocol,
          target_contract_source=target_contract_source,
          legacy_schema=legacy_schema,
        )
      except Exception:  # noqa: BLE001 - report only stable reason codes
        report = _build_report(
          {},
          unsettled_inbox_count=None,
          configured_protocol=configured_protocol,
          target_contract_source=target_contract_source,
          extra_reasons=(P0_AUDIT_UNAVAILABLE,),
          running_protocol=(
            LEGACY_PROTOCOL_VERSION if legacy_schema else CURRENT_PROTOCOL_VERSION
          ),
        )
      finally:
        try:
          await transaction.rollback()
          rolled_back = True
        except Exception:  # noqa: BLE001 - do not leak DB exception details
          rolled_back = False
  except Exception:  # noqa: BLE001 - connection failures are fail-closed
    report = _build_report(
      {},
      unsettled_inbox_count=None,
      configured_protocol=configured_protocol,
      target_contract_source=target_contract_source,
      extra_reasons=(P0_AUDIT_UNAVAILABLE,),
      running_protocol=(
        LEGACY_PROTOCOL_VERSION if legacy_schema else CURRENT_PROTOCOL_VERSION
      ),
    )

  if report is None:
    report = _build_report(
      {},
      unsettled_inbox_count=None,
      configured_protocol=configured_protocol,
      target_contract_source=target_contract_source,
      extra_reasons=(P0_AUDIT_UNAVAILABLE,),
      running_protocol=(
        LEGACY_PROTOCOL_VERSION if legacy_schema else CURRENT_PROTOCOL_VERSION
      ),
    )
  if not rolled_back:
    report["reasonCodes"] = sorted(
      {*report["reasonCodes"], READ_ONLY_ROLLBACK_FAILED}
    )
    report["readyForCutover"] = False
  report["transaction"] = {
    "readOnly": read_only_applied,
    "rolledBack": rolled_back,
  }
  return report


run_read_only_rehearsal = run_rehearsal
preflight = run_rehearsal


def exit_code_for_report(report: Mapping[str, Any], require_ready: bool) -> int:
  """Return the CLI status without inspecting process state or mutating DB."""

  if require_ready and report.get("readyForCutover") is not True:
    return 2
  return 0


def render_markdown(report: Mapping[str, Any]) -> str:
  """Render only aggregate facts and explicit simulation markers."""

  observed = report.get("observedDbFacts")
  observed = observed if isinstance(observed, Mapping) else {}
  reasons = report.get("reasonCodes")
  reasons_text = ", ".join(str(item) for item in reasons or ()) or "none"
  lines = [
    "# Protocol 1.2 cutover rehearsal",
    "",
    f"- Mode: {report.get('mode', 'REHEARSAL_ONLY')}",
    f"- Ready for cutover: {str(report.get('readyForCutover') is True).lower()}",
    f"- Actual cutover: {str(report.get('actualCutover') is True).lower()}",
    f"- Reason codes: {reasons_text}",
    "",
    "## Observed database facts",
    "",
    "| Fact | Value |",
    "| --- | ---: |",
    f"| Missing required tables | {observed.get('missingTableCount', 'unknown')} |",
    f"| Legacy config rows | {observed.get('legacyConfigCount', 'unknown')} |",
    f"| Queued protocol 1.1 outbox | {observed.get('queuedProtocol11OutboxCount', 'unknown')} |",
    f"| Protocol 1.1 unknown result | {observed.get('protocol11UnknownResultCount', 'unknown')} |",
    f"| Unsettled Agent inbox | {observed.get('unsettledAgentInboxCount', 'unknown')} |",
    f"| Terminal ExitPlan unrouted intents | {observed.get('terminalExitPlanUnroutedIntentCount', 'unknown')} |",
    f"| Configured protocol | {observed.get('configuredProtocol', 'unknown')} |",
    "",
    "## Simulated state sequence",
    "",
    "| State | Source | Performed |",
    "| --- | --- | :---: |",
  ]
  rehearsal = report.get("rehearsal")
  steps = rehearsal.get("steps") if isinstance(rehearsal, Mapping) else ()
  for step in steps or ():
    if not isinstance(step, Mapping):
      continue
    lines.append(
      f"| {step.get('state', '')} | {step.get('source', 'SIMULATION')} | "
      f"{'yes' if step.get('performed') else 'no'} |"
    )
  lines.extend(
    [
      "",
      "This report is a read-only rehearsal. No component stop, backup, deploy, "
      "or post-deploy reconciliation was performed.",
      "",
    ]
  )
  return "\n".join(lines)


async def _run_cli(args: argparse.Namespace) -> int:
  try:
    if args.database_url:
      database_url = args.database_url
    else:
      from quantx_infrastructure.runtime_store import resolve_database_url

      database_url = resolve_database_url()
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
      # The pre-cutover rehearsal is the default CLI operation: it is the
      # command operators run while the database is still at 0045.  The
      # target-schema P0 audit is an explicit post-migration verification so
      # this tool cannot accidentally inspect the wrong table shape.
      report = await run_rehearsal(
        engine,
        legacy_schema=not args.target_0046,
      )
    finally:
      await engine.dispose()
  except Exception:  # noqa: BLE001 - never print raw or secret-bearing errors
    print("P1 cutover rehearsal unavailable", file=sys.stderr)
    return 1

  if args.format == "markdown":
    print(render_markdown(report), end="")
  else:
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
  return exit_code_for_report(report, args.require_ready)


def build_argument_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
    description="Read-only protocol-1.2 cutover rehearsal"
  )
  parser.add_argument("--database-url", default=None)
  parser.add_argument("--format", choices=("json", "markdown"), default="json")
  parser.add_argument("--require-ready", action="store_true")
  schema_group = parser.add_mutually_exclusive_group()
  schema_group.add_argument(
    "--legacy-0045",
    action="store_true",
    help="explicitly select the pre-0046 schema (the default)",
  )
  schema_group.add_argument(
    "--target-0046",
    action="store_true",
    help="inspect the canonical 0046 schema for post-cutover verification",
  )
  return parser


def main(argv: Iterable[str] | None = None) -> int:
  return asyncio.run(_run_cli(build_argument_parser().parse_args(argv)))


if __name__ == "__main__":
  raise SystemExit(main())


__all__ = [
  "CONFIGURATION_MISSING",
  "CONFIGURATION_STATUS_UNKNOWN",
  "CURRENT_PROTOCOL_VERSION",
  "LEGACY_PROTOCOL_VERSION",
  "LEGACY_REQUIRED_TABLES",
  "INBOX_STATUS_UNKNOWN",
  "P0_AUDIT_UNAVAILABLE",
  "P0_READINESS_NOT_MET",
  "P0_READINESS_UNKNOWN",
  "P0_REQUIRED_TABLES_MISSING",
  "P1_CUTOVER_REHEARSAL_SCHEMA_VERSION",
  "PROTOCOL_11_OUTBOX_NOT_DRAINED",
  "PROTOCOL_11_RESULT_UNKNOWN",
  "PROTOCOL_STATUS_UNKNOWN",
  "PROTOCOL_VERSION_UNSUPPORTED",
  "READ_ONLY_ROLLBACK_FAILED",
  "REHEARSAL_SCOPE",
  "REHEARSAL_STATE_SEQUENCE",
  "TERMINAL_EXIT_INTENT_GATE_UNKNOWN",
  "TERMINAL_EXIT_PLAN_UNROUTED_INTENT",
  "TARGET_PROTOCOL_VERSION",
  "UNSETTLED_AGENT_INBOX",
  "build_argument_parser",
  "exit_code_for_report",
  "inspect_connection",
  "main",
  "preflight",
  "render_markdown",
  "run_read_only_rehearsal",
  "run_rehearsal",
]
