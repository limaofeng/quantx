"""Read-only P0 audit for the legacy multi-instrument T assistant.

The P0 audit is deliberately a small operational probe rather than a repair
job.  It reports aggregate counts only, so it can be run against a development
database without copying account, run, order, or plan identifiers into an
artifact or a log stream.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

P0_SCHEMA_VERSION = 1
LEGACY_PROTOCOL_VERSION = "1.1"
CURRENT_PROTOCOL_VERSION = "1.2"
TARGET_PROTOCOL_VERSION = CURRENT_PROTOCOL_VERSION
TARGET_OWNER_TYPES = (
  "STRATEGY_RUN",
  "T_ASSISTANT_EXECUTION",
  "ENTRY_PLAN",
  "BOARD_ASSISTANT_EXECUTION",
  "EXIT_PLAN",
  "MANUAL_COMMAND",
)
AUDIT_SCOPE = "P0_LEGACY_T_ASSISTANT"

REQUIRED_TABLES = (
  "t_trade_global_configs",
  "strategy_runs",
  "strategies",
  "strategy_run_states",
  "trade_intents",
  "pending_trade_orders",
  "order_correlations",
  "trade_command_outbox",
  "strategy_runtime_events",
  "t_trade_batches",
  "auto_exit_plans",
  "agent_report_inbox",
  "orders",
  "trades",
)

_TERMINAL_INTENT_STATUSES = (
  "FILLED",
  "CANCELLED",
  "CANCELED",
  "REJECTED",
  "EXPIRED",
  "FAILED",
  "SUPPRESSED",
  "RECONCILED_ZERO_FILL",
)
_TERMINAL_PENDING_STATUSES = (
  "FILLED",
  "CANCELLED",
  "CANCELED",
  "REJECTED",
  "EXPIRED",
  "RECONCILED_ZERO_FILL",
)
_ACTIVE_RUN_STATUSES = ("PENDING", "RUNNING", "PAUSED")
_TERMINAL_BATCH_STATUSES = (
  "COMPLETED",
  "CANCELLED",
  "ENTRY_REJECTED",
  "ENTRY_EXPIRED",
)
_ACTIVE_EXIT_PLAN_STATUSES = (
  "PENDING_ENTRY",
  "ACTIVE",
  "EXIT_PENDING",
  "PARTIALLY_EXITED",
  "PAUSED",
  "ERROR",
)
_T_EXIT_PLAN_RUNTIME_SOURCES = (
  "T_TRADE_BATCH",
  "LIMIT_UP_BOARD",
  "FIRST_BOARD_PROMOTION_V2",
  "ENTRY_PLAN",
)
_T_EXIT_PLAN_MANUAL_SOURCES = ("MANUAL_POSITION", "MANUAL_LIQUIDATION")


@dataclass(frozen=True)
class AuditCheckSpec:
  """Immutable definition of one aggregate database check."""

  code: str
  category: str
  severity: str
  blocks_p1: bool
  description: str
  sql: str
  required_tables: tuple[str, ...] = REQUIRED_TABLES
  clear_disposition: str = "CLEAR"
  violation_disposition: str = "REVIEW_REQUIRED"


@dataclass(frozen=True)
class SourceScanPattern:
  """Immutable source identity pattern used by the P0 inventory."""

  name: str
  expression: str


_SOURCE_PATTERNS = (
  SourceScanPattern("strategy_run_id", r"\bstrategy_run_id\b"),
  SourceScanPattern("strategyRunId", r"\bstrategyRunId\b"),
  SourceScanPattern("run_id", r"(?<![A-Za-z0-9_])run_id(?![A-Za-z0-9_])"),
  SourceScanPattern("owner_type", r"\bowner_type\b"),
  SourceScanPattern("ownerType", r"\bownerType\b"),
  SourceScanPattern("STRATEGY_RUN", r"\bSTRATEGY_RUN\b"),
  SourceScanPattern("TradeCommandPayload", r"\bTradeCommandPayload\b"),
)
_SOURCE_PATTERN_REGEXES = tuple(
  (pattern.name, re.compile(pattern.expression)) for pattern in _SOURCE_PATTERNS
)
_SOURCE_EXTENSIONS = frozenset({".py", ".ts", ".tsx", ".graphql", ".sql"})
_EXCLUDED_PARTS = frozenset(
  {".git", ".venv", "node_modules", "generated", "__generated__", ".runtime"}
)
_GROUP_ORDER = (
  "DB",
  "contracts",
  "domain",
  "application",
  "infrastructure",
  "Engine",
  "API",
  "Worker",
  "QMT Agent",
  "GraphQL/Web",
)


def _sql_list(values: Sequence[str]) -> str:
  """Return a quoted list for fixed, source-controlled enum values."""

  return ", ".join(f"'{value}'" for value in values)


_T_RUN_CTE = f"""
  SELECT run.id
  FROM strategy_runs AS run
  JOIN strategies AS strategy ON strategy.id = run.strategy_id
  WHERE strategy.class_name = 'AshareIntradayTAssistantStrategy'
    AND UPPER(CAST(run.status AS TEXT)) IN ({_sql_list(_ACTIVE_RUN_STATUSES)})
"""
_T_RUN_ANY_CTE = """
  SELECT run.id
  FROM strategy_runs AS run
  JOIN strategies AS strategy ON strategy.id = run.strategy_id
  WHERE strategy.class_name = 'AshareIntradayTAssistantStrategy'
"""
_T_COMMAND_PREDICATE = f"""
  (
    command.owner_type = 'STRATEGY_RUN'
    AND command.owner_id IN ({_T_RUN_ANY_CTE})
    AND (
      EXISTS (
        SELECT 1
        FROM pending_trade_orders AS pending
        WHERE pending.client_order_id = command.client_order_id
          AND UPPER(COALESCE(pending.t_trade_role, '')) IN ('ENTRY', 'EXIT')
      )
      OR EXISTS (
        SELECT 1
        FROM order_correlations AS correlation
        WHERE correlation.client_order_id = command.client_order_id
          AND UPPER(COALESCE(correlation.t_trade_role, '')) IN ('ENTRY', 'EXIT')
      )
    )
  )
"""


def _jsonb_instrument_states(alias: str = "state") -> str:
  return f"""
  CROSS JOIN LATERAL jsonb_each(
    COALESCE(({alias}.custom_state::jsonb) -> 'instrument_states', '{{}}'::jsonb)
  ) AS instrument_state(instrument_code, value)
  """


def _count_sql(body: str) -> str:
  return f"SELECT COUNT(*) AS count_value\nFROM ({body}) AS audit_rows"


_ACTIVE_CONFIG_ORPHAN_SQL = f"""
SELECT COUNT(*) AS count_value
FROM t_trade_global_configs AS config
LEFT JOIN strategy_runs AS run ON run.id = config.strategy_run_id
LEFT JOIN strategies AS strategy ON strategy.id = run.strategy_id
WHERE config.enabled IS TRUE
  AND (
    config.strategy_run_id IS NULL
    OR run.id IS NULL
    OR strategy.class_name <> 'AshareIntradayTAssistantStrategy'
    OR UPPER(CAST(run.status AS TEXT)) NOT IN ({_sql_list(_ACTIVE_RUN_STATUSES)})
  )
"""

_ENABLED_LEGACY_CONFIG_COUNT_SQL = """
SELECT COUNT(*) AS count_value
FROM t_trade_global_configs AS config
WHERE config.enabled IS TRUE
"""

_ACTIVE_T_RUN_UNBOUND_SQL = f"""
SELECT COUNT(*) AS count_value
FROM strategy_runs AS run
JOIN strategies AS strategy ON strategy.id = run.strategy_id
WHERE strategy.class_name = 'AshareIntradayTAssistantStrategy'
  AND UPPER(CAST(run.status AS TEXT)) IN ({_sql_list(_ACTIVE_RUN_STATUSES)})
  AND NOT EXISTS (
    SELECT 1
    FROM t_trade_global_configs AS config
    WHERE config.enabled IS TRUE
      AND config.strategy_run_id = run.id
  )
"""

_ACTIVE_T_RUN_COUNT_SQL = f"""
SELECT COUNT(*) AS count_value
FROM ({_T_RUN_CTE}) AS active_t_runs
"""

_ACTIVE_CANDIDATE_SQL = f"""
SELECT COUNT(*) AS count_value
FROM strategy_run_states AS state
JOIN ({_T_RUN_CTE}) AS t_run ON t_run.id = state.run_id
{_jsonb_instrument_states()}
WHERE UPPER(COALESCE(instrument_state.value -> 'opportunity' ->> 'candidate_status', ''))
  IN ('LATCHED', 'AWAITING_APPROVAL')
"""

_AWAITING_APPROVAL_SQL = """
SELECT COUNT(*) AS count_value
FROM trade_intents AS intent
JOIN strategy_runs AS run ON run.id = intent.strategy_run_id
JOIN strategies AS strategy ON strategy.id = run.strategy_id
WHERE strategy.class_name = 'AshareIntradayTAssistantStrategy'
  AND UPPER(intent.status) = 'AWAITING_APPROVAL'
"""

_CANDIDATE_APPROVAL_INCONSISTENCY_SQL = f"""
WITH instrument_states AS (
  SELECT
    state.run_id,
    UPPER(COALESCE(instrument_state.value -> 'opportunity' ->> 'candidate_status', ''))
      AS candidate_status,
    UPPER(COALESCE(instrument_state.value ->> 'entry_order_status', ''))
      AS order_status,
    NULLIF(TRIM(instrument_state.value ->> 'pending_entry_intent_id'), '')
      AS pending_intent_id
  FROM strategy_run_states AS state
  JOIN ({_T_RUN_CTE}) AS t_run ON t_run.id = state.run_id
  {_jsonb_instrument_states()}
)
SELECT COUNT(*) AS count_value
FROM instrument_states
WHERE (
    candidate_status = 'AWAITING_APPROVAL'
    AND (order_status <> 'AWAITING_APPROVAL' OR pending_intent_id IS NULL)
  )
  OR (
    order_status = 'AWAITING_APPROVAL'
    AND candidate_status <> 'AWAITING_APPROVAL'
  )
"""

_TERMINAL_RUN_AWAITING_APPROVAL_SQL = f"""
SELECT COUNT(*) AS count_value
FROM trade_intents AS intent
JOIN strategy_runs AS run ON run.id = intent.strategy_run_id
JOIN strategies AS strategy ON strategy.id = run.strategy_id
WHERE strategy.class_name = 'AshareIntradayTAssistantStrategy'
  AND UPPER(CAST(run.status AS TEXT)) NOT IN ({_sql_list(_ACTIVE_RUN_STATUSES)})
  AND UPPER(intent.status) = 'AWAITING_APPROVAL'
"""

_APPROVAL_IDENTITY_MISSING_SQL = """
SELECT COUNT(*) AS count_value
FROM trade_intents AS intent
JOIN strategy_runs AS run ON run.id = intent.strategy_run_id
JOIN strategies AS strategy ON strategy.id = run.strategy_id
WHERE strategy.class_name = 'AshareIntradayTAssistantStrategy'
  AND UPPER(intent.status) = 'AWAITING_APPROVAL'
  AND (
    NULLIF(TRIM(intent.metadata::jsonb ->> 'candidate_id'), '') IS NULL
    OR COALESCE(
      NULLIF(TRIM(intent.metadata::jsonb ->> 'candidate_fingerprint'), ''),
      NULLIF(TRIM(intent.metadata::jsonb ->> 'fingerprint'), '')
    ) IS NULL
  )
"""

_NONTERMINAL_INTENT_SQL = f"""
SELECT COUNT(*) AS count_value
FROM trade_intents AS intent
JOIN strategy_runs AS run ON run.id = intent.strategy_run_id
JOIN strategies AS strategy ON strategy.id = run.strategy_id
WHERE strategy.class_name = 'AshareIntradayTAssistantStrategy'
  AND UPPER(intent.status) NOT IN ({_sql_list(_TERMINAL_INTENT_STATUSES)})
"""

_NONTERMINAL_PENDING_SQL = f"""
SELECT COUNT(*) AS count_value
FROM pending_trade_orders AS pending
JOIN ({_T_RUN_ANY_CTE}) AS t_run ON t_run.id = pending.strategy_run_id
WHERE UPPER(pending.status) NOT IN ({_sql_list(_TERMINAL_PENDING_STATUSES)})
"""

_ACTIVE_OUTBOX_SQL = f"""
SELECT COUNT(*) AS count_value
FROM trade_command_outbox AS command
WHERE {_T_COMMAND_PREDICATE}
  AND UPPER(command.delivery_status) NOT IN (
    'COMPLETED', 'FAILED', 'EXPIRED', 'CANCELLED', 'CANCELLED_KILL'
  )
"""

_LINKED_ORDER_FILL_FACT_SQL = f"""
SELECT COUNT(DISTINCT trade.traded_id) AS count_value
FROM trades AS trade
JOIN orders AS broker_order ON broker_order.order_id = trade.order_id
WHERE UPPER(COALESCE(broker_order.order_remark, '')) LIKE 'QX:%'
  AND EXISTS (
    SELECT 1
    FROM trade_command_outbox AS command
    WHERE {_T_COMMAND_PREDICATE}
      AND UPPER(COALESCE(broker_order.order_remark, '')) LIKE
        'QX:' || LEFT(command.client_order_id, 20) || '%'
  )
"""

_LINKED_ORDER_FILL_GAP_SQL = f"""
SELECT COUNT(*) AS count_value
FROM pending_trade_orders AS pending
JOIN ({_T_RUN_ANY_CTE}) AS t_run ON t_run.id = pending.strategy_run_id
WHERE pending.broker_order_id IS NOT NULL
  AND NOT EXISTS (
    SELECT 1
    FROM orders AS broker_order
    WHERE CAST(broker_order.order_sysid AS TEXT) = pending.broker_order_id
       OR CAST(broker_order.order_id AS TEXT) = pending.broker_order_id
  )
"""

_PENDING_DURABLE_LINK_GAP_SQL = f"""
SELECT COUNT(*) AS count_value
FROM pending_trade_orders AS pending
JOIN ({_T_RUN_ANY_CTE}) AS t_run ON t_run.id = pending.strategy_run_id
LEFT JOIN trade_command_outbox AS command
  ON command.client_order_id = pending.client_order_id
LEFT JOIN order_correlations AS correlation
  ON correlation.client_order_id = pending.client_order_id
WHERE UPPER(pending.status) NOT IN ({_sql_list(_TERMINAL_PENDING_STATUSES)})
  AND (command.message_id IS NULL OR correlation.id IS NULL)
"""

_CORRELATION_PENDING_CONFLICT_SQL = f"""
SELECT COUNT(*) AS count_value
FROM order_correlations AS correlation
JOIN pending_trade_orders AS pending
  ON pending.client_order_id = correlation.client_order_id
JOIN ({_T_RUN_ANY_CTE}) AS t_run ON t_run.id = pending.strategy_run_id
WHERE (
    correlation.strategy_run_id IS DISTINCT FROM pending.strategy_run_id
    OR correlation.strategy_order_id IS DISTINCT FROM pending.strategy_order_id
    OR correlation.intent_id IS DISTINCT FROM pending.intent_id
    OR correlation.batch_id IS DISTINCT FROM pending.batch_id
    OR correlation.bucket IS DISTINCT FROM pending.bucket
    OR correlation.t_trade_role IS DISTINCT FROM pending.t_trade_role
    OR correlation.environment IS DISTINCT FROM pending.environment
    OR correlation.risk_decision_id IS DISTINCT FROM pending.risk_decision_id
    OR correlation.trace_id IS DISTINCT FROM pending.trace_id
  )
"""

_QUEUED_PROTOCOL_11_SQL = f"""
SELECT COUNT(*) AS count_value
FROM trade_command_outbox AS command
WHERE {_T_COMMAND_PREDICATE}
  AND UPPER(command.delivery_status) = 'QUEUED'
  AND command.payload ?| ARRAY[
    'protocol_version',
    'strategy_name',
    't_trade_role',
    'strategy_run_id',
    'strategy_order_id',
    'intent_id',
    'batch_id',
    'trace_id'
  ]
"""

_UNKNOWN_RESULT_PROTOCOL_11_SQL = f"""
SELECT COUNT(*) AS count_value
FROM trade_command_outbox AS command
WHERE {_T_COMMAND_PREDICATE}
  AND UPPER(command.delivery_status) IN (
    'DELIVERED', 'ACKNOWLEDGED', 'RECONCILE_REQUIRED'
  )
  AND command.payload ?| ARRAY[
    'protocol_version',
    'strategy_name',
    't_trade_role',
    'strategy_run_id',
    'strategy_order_id',
    'intent_id',
    'batch_id',
    'trace_id'
  ]
  AND NOT EXISTS (
    SELECT 1
    FROM pending_trade_orders AS pending
    WHERE pending.client_order_id = command.client_order_id
      AND UPPER(pending.status) IN ({_sql_list(_TERMINAL_PENDING_STATUSES)})
  )
  AND NOT EXISTS (
    SELECT 1
    FROM agent_report_inbox AS report
    WHERE report.client_order_id = command.client_order_id
      AND UPPER(report.processing_status) IN ('PROCESSED', 'APPLIED')
  )
"""

_UNAPPLIED_RUNTIME_EVENT_SQL = f"""
SELECT COUNT(*) AS count_value
FROM strategy_runtime_events AS event
JOIN ({_T_RUN_ANY_CTE}) AS t_run ON t_run.id = event.strategy_run_id
WHERE UPPER(event.application_status) <> 'APPLIED'
"""

_OPEN_UNBALANCED_BATCH_SQL = f"""
SELECT COUNT(*) AS count_value
FROM t_trade_batches AS batch
JOIN ({_T_RUN_ANY_CTE}) AS t_run ON t_run.id = batch.strategy_run_id
WHERE UPPER(batch.status) NOT IN ({_sql_list(_TERMINAL_BATCH_STATUSES)})
  AND COALESCE(batch.entry_filled_volume, 0) > COALESCE(batch.exit_filled_volume, 0)
"""

_OUTSTANDING_T_EXIT_PLAN_FILTER = """
  UPPER(COALESCE(plan.source_type, '')) = 'T_TRADE_BATCH'
  AND COALESCE(UPPER(plan.status), '') NOT IN (
    'COMPLETED', 'CANCELLED'
  )
  AND COALESCE(plan.remaining_volume, 0) > 0
"""

_OUTSTANDING_T_EXIT_PLAN_OWNER_INVALID_SQL = f"""
SELECT COUNT(*) AS count_value
FROM auto_exit_plans AS plan
WHERE {_OUTSTANDING_T_EXIT_PLAN_FILTER}
  AND (
    NOT (
      COALESCE(NULLIF(TRIM(plan.plan_id), ''), '') <> ''
      AND COALESCE(NULLIF(TRIM(plan.account_id), ''), '') <> ''
      AND COALESCE(NULLIF(TRIM(plan.instrument_code), ''), '') <> ''
      AND COALESCE(NULLIF(TRIM(plan.source_type), ''), '') <> ''
      AND COALESCE(
        TRIM(plan.plan_state::jsonb -> 'template' ->> 'plan_id'), ''
      ) = COALESCE(TRIM(plan.plan_id), '')
      AND COALESCE(
        TRIM(plan.plan_state::jsonb -> 'template' ->> 'account_id'), ''
      ) = COALESCE(TRIM(plan.account_id), '')
      AND UPPER(COALESCE(
        TRIM(plan.plan_state::jsonb -> 'template' ->> 'instrument_code'), ''
      )) = UPPER(COALESCE(TRIM(plan.instrument_code), ''))
      AND UPPER(COALESCE(
        TRIM(plan.plan_state::jsonb -> 'template' ->> 'source_type'), ''
      )) = UPPER(COALESCE(TRIM(plan.source_type), ''))
      AND COALESCE(
        TRIM(plan.plan_state::jsonb -> 'template' ->> 'run_id'), ''
      ) = COALESCE(TRIM(plan.strategy_run_id), '')
    )
    OR (
      COALESCE(NULLIF(TRIM(plan.strategy_run_id), ''), '') = ''
      AND UPPER(COALESCE(TRIM(plan.source_type), '')) NOT IN (
        {_sql_list(_T_EXIT_PLAN_MANUAL_SOURCES)}
      )
    )
    OR (
      COALESCE(NULLIF(TRIM(plan.strategy_run_id), ''), '') <> ''
      AND (
        UPPER(COALESCE(TRIM(plan.source_type), '')) NOT IN (
          {_sql_list(_T_EXIT_PLAN_RUNTIME_SOURCES)}
        )
        OR COALESCE(
          (plan.plan_state::jsonb -> 'template' -> 'metadata')
            ? 'managed_runtime_command_id',
          FALSE
        )
      )
    )
    OR NOT EXISTS (
      SELECT 1
      FROM t_trade_batches AS batch
      JOIN ({_T_RUN_CTE}) AS t_run ON t_run.id = batch.strategy_run_id
      WHERE batch.batch_id = plan.source_id
        AND batch.strategy_run_id = plan.strategy_run_id
    )
  )
"""

_OUTSTANDING_T_EXIT_PLAN_COUNT_SQL = f"""
SELECT COUNT(*) AS count_value
FROM auto_exit_plans AS plan
WHERE {_OUTSTANDING_T_EXIT_PLAN_FILTER}
"""

_LEGACY_T_INTENT_OWNER_INVALID_SQL = f"""
SELECT COUNT(*) AS count_value
FROM trade_intents AS intent
JOIN strategy_runs AS run ON run.id = intent.strategy_run_id
JOIN strategies AS strategy ON strategy.id = run.strategy_id
WHERE strategy.class_name = 'AshareIntradayTAssistantStrategy'
  AND (
    UPPER(COALESCE(NULLIF(TRIM(intent.owner_type), ''), '')) NOT IN (
      {_sql_list(TARGET_OWNER_TYPES)}
    )
    OR NULLIF(TRIM(intent.owner_id), '') IS NULL
    OR (
      UPPER(COALESCE(NULLIF(TRIM(intent.owner_type), ''), '')) = 'STRATEGY_RUN'
      AND (
        intent.strategy_run_id IS NULL
        OR NULLIF(TRIM(intent.owner_id), '') IS DISTINCT FROM
          NULLIF(TRIM(intent.strategy_run_id), '')
      )
    )
  )
"""

_TERMINAL_EXIT_PLAN_OWNER_INVALID_SQL = f"""
SELECT COUNT(*) AS count_value
FROM auto_exit_plans AS plan
WHERE UPPER(plan.status) IN ('COMPLETED', 'CANCELLED')
  AND UPPER(plan.source_type) IN (
    {_sql_list(_T_EXIT_PLAN_RUNTIME_SOURCES + _T_EXIT_PLAN_MANUAL_SOURCES)}
  )
  AND (
    NOT (
      COALESCE(NULLIF(TRIM(plan.plan_id), ''), '') <> ''
      AND COALESCE(NULLIF(TRIM(plan.account_id), ''), '') <> ''
      AND COALESCE(NULLIF(TRIM(plan.instrument_code), ''), '') <> ''
      AND COALESCE(NULLIF(TRIM(plan.source_type), ''), '') <> ''
      AND COALESCE(
        TRIM(plan.plan_state::jsonb -> 'template' ->> 'plan_id'), ''
      ) = COALESCE(TRIM(plan.plan_id), '')
      AND COALESCE(
        TRIM(plan.plan_state::jsonb -> 'template' ->> 'account_id'), ''
      ) = COALESCE(TRIM(plan.account_id), '')
      AND UPPER(COALESCE(
        TRIM(plan.plan_state::jsonb -> 'template' ->> 'instrument_code'), ''
      )) = UPPER(COALESCE(TRIM(plan.instrument_code), ''))
      AND UPPER(COALESCE(
        TRIM(plan.plan_state::jsonb -> 'template' ->> 'source_type'), ''
      )) = UPPER(COALESCE(TRIM(plan.source_type), ''))
      AND COALESCE(
        TRIM(plan.plan_state::jsonb -> 'template' ->> 'run_id'), ''
      ) = COALESCE(TRIM(plan.strategy_run_id), '')
    )
    OR (
      COALESCE(NULLIF(TRIM(plan.strategy_run_id), ''), '') = ''
      AND UPPER(COALESCE(TRIM(plan.source_type), '')) NOT IN (
        {_sql_list(_T_EXIT_PLAN_MANUAL_SOURCES)}
      )
    )
    OR (
      COALESCE(NULLIF(TRIM(plan.strategy_run_id), ''), '') <> ''
      AND (
        UPPER(COALESCE(TRIM(plan.source_type), '')) NOT IN (
          {_sql_list(_T_EXIT_PLAN_RUNTIME_SOURCES)}
        )
        OR COALESCE(
          (plan.plan_state::jsonb -> 'template' -> 'metadata')
            ? 'managed_runtime_command_id',
          FALSE
        )
      )
    )
    OR (
      UPPER(COALESCE(TRIM(plan.source_type), '')) IN (
        {_sql_list(_T_EXIT_PLAN_RUNTIME_SOURCES)}
      )
      AND NOT EXISTS (
        SELECT 1
        FROM strategy_runs AS run
        WHERE run.id = plan.strategy_run_id
      )
    )
  )
"""


AUDIT_CHECK_SPECS = (
  AuditCheckSpec(
    code="P0_ENABLED_CONFIG_ORPHAN_RUN",
    category="configuration",
    severity="BLOCKER",
    blocks_p1=True,
    description="enabled global T configuration is missing or bound to a non-active T run",
    sql=_ACTIVE_CONFIG_ORPHAN_SQL,
    violation_disposition="BLOCK_P1_ORPHAN_ENABLED_CONFIG",
  ),
  AuditCheckSpec(
    code="P0_ENABLED_LEGACY_CONFIG_COUNT",
    category="configuration",
    severity="FACT",
    blocks_p1=False,
    description="enabled legacy T global configuration row count",
    sql=_ENABLED_LEGACY_CONFIG_COUNT_SQL,
  ),
  AuditCheckSpec(
    code="P0_ACTIVE_T_RUN_COUNT",
    category="runtime",
    severity="FACT",
    blocks_p1=False,
    description="active AshareIntradayTAssistantStrategy StrategyRun count",
    sql=_ACTIVE_T_RUN_COUNT_SQL,
  ),
  AuditCheckSpec(
    code="P0_ACTIVE_T_RUN_UNBOUND",
    category="runtime",
    severity="BLOCKER",
    blocks_p1=True,
    description="active T StrategyRun is not bound by an enabled global configuration",
    sql=_ACTIVE_T_RUN_UNBOUND_SQL,
    violation_disposition="BLOCK_P1_UNBOUND_ACTIVE_T_RUN",
  ),
  AuditCheckSpec(
    code="P0_ACTIVE_CANDIDATE_COUNT",
    category="candidate",
    severity="FACT",
    blocks_p1=False,
    description="durable active candidate count from instrument opportunity state",
    sql=_ACTIVE_CANDIDATE_SQL,
  ),
  AuditCheckSpec(
    code="P0_AWAITING_APPROVAL_COUNT",
    category="approval",
    severity="FACT",
    blocks_p1=False,
    description="T trade intents awaiting manual approval",
    sql=_AWAITING_APPROVAL_SQL,
  ),
  AuditCheckSpec(
    code="P0_CANDIDATE_APPROVAL_STATUS_INCONSISTENT",
    category="approval",
    severity="BLOCKER",
    blocks_p1=True,
    description="candidate status and durable approval state disagree",
    sql=_CANDIDATE_APPROVAL_INCONSISTENCY_SQL,
    violation_disposition="BLOCK_P1_CANDIDATE_APPROVAL_MISMATCH",
  ),
  AuditCheckSpec(
    code="P0_TERMINAL_RUN_AWAITING_APPROVAL",
    category="approval",
    severity="BLOCKER",
    blocks_p1=True,
    description="a terminal T StrategyRun still owns an awaiting-approval intent",
    sql=_TERMINAL_RUN_AWAITING_APPROVAL_SQL,
    violation_disposition="BLOCK_P1_TERMINAL_RUN_APPROVAL",
  ),
  AuditCheckSpec(
    code="P0_APPROVAL_IDENTITY_MISSING",
    category="approval",
    severity="BLOCKER",
    blocks_p1=True,
    description="an awaiting-approval intent lacks candidate identity or fingerprint",
    sql=_APPROVAL_IDENTITY_MISSING_SQL,
    violation_disposition="BLOCK_P1_APPROVAL_IDENTITY",
  ),
  AuditCheckSpec(
    code="P0_NONTERMINAL_T_INTENT",
    category="obligation",
    severity="BLOCKER",
    blocks_p1=True,
    description="a T intent remains non-terminal and must be drained or reconciled",
    sql=_NONTERMINAL_INTENT_SQL,
    violation_disposition="BLOCK_P1_NONTERMINAL_INTENT",
  ),
  AuditCheckSpec(
    code="P0_NONTERMINAL_T_PENDING_ORDER",
    category="obligation",
    severity="BLOCKER",
    blocks_p1=True,
    description="a T pending order remains non-terminal and must be reconciled",
    sql=_NONTERMINAL_PENDING_SQL,
    violation_disposition="BLOCK_P1_NONTERMINAL_PENDING_ORDER",
  ),
  AuditCheckSpec(
    code="P0_ACTIVE_T_OUTBOX",
    category="obligation",
    severity="FACT",
    blocks_p1=False,
    description="active T command outbox row count",
    sql=_ACTIVE_OUTBOX_SQL,
  ),
  AuditCheckSpec(
    code="P0_LINKED_ORDER_FILL_FACT",
    category="broker_facts",
    severity="FACT",
    blocks_p1=False,
    description="broker order and fill facts linked to a T command by the durable remark",
    sql=_LINKED_ORDER_FILL_FACT_SQL,
  ),
  AuditCheckSpec(
    code="P0_LINKED_ORDER_FILL_GAP",
    category="broker_facts",
    severity="WARNING",
    blocks_p1=False,
    description="T pending order has a broker id without a durable order row",
    sql=_LINKED_ORDER_FILL_GAP_SQL,
    violation_disposition="REVIEW_T_ORDER_FILL_LINK",
  ),
  AuditCheckSpec(
    code="P0_PENDING_DURABLE_LINK_GAP",
    category="obligation",
    severity="BLOCKER",
    blocks_p1=True,
    description="non-terminal T pending order lacks outbox or correlation linkage",
    sql=_PENDING_DURABLE_LINK_GAP_SQL,
    violation_disposition="BLOCK_P1_PENDING_DURABLE_LINK",
  ),
  AuditCheckSpec(
    code="P0_CORRELATION_PENDING_CONFLICT",
    category="obligation",
    severity="BLOCKER",
    blocks_p1=True,
    description="strategy-order correlation fields conflict with its pending order",
    sql=_CORRELATION_PENDING_CONFLICT_SQL,
    violation_disposition="BLOCK_P1_CORRELATION_PENDING_CONFLICT",
  ),
  AuditCheckSpec(
    code="P0_QUEUED_PROTOCOL_11_T_COMMAND",
    category="protocol",
    severity="BLOCKER",
    blocks_p1=True,
    description="queued T command still carries the legacy protocol 1.1 payload",
    sql=_QUEUED_PROTOCOL_11_SQL,
    violation_disposition="BLOCK_P1_DRAIN_PROTOCOL_11_QUEUE",
  ),
  AuditCheckSpec(
    code="P0_UNKNOWN_RESULT_PROTOCOL_11_T_COMMAND",
    category="protocol",
    severity="BLOCKER",
    blocks_p1=True,
    description="delivered or acknowledged legacy T command has no durable result",
    sql=_UNKNOWN_RESULT_PROTOCOL_11_SQL,
    violation_disposition="BLOCK_P1_RECONCILE_UNKNOWN_RESULT",
  ),
  AuditCheckSpec(
    code="P0_UNAPPLIED_RUNTIME_EVENT",
    category="obligation",
    severity="BLOCKER",
    blocks_p1=True,
    description="T runtime event is not yet applied by the Engine",
    sql=_UNAPPLIED_RUNTIME_EVENT_SQL,
    violation_disposition="BLOCK_P1_UNAPPLIED_RUNTIME_EVENT",
  ),
  AuditCheckSpec(
    code="P0_OPEN_UNBALANCED_BATCH",
    category="obligation",
    severity="BLOCKER",
    blocks_p1=True,
    description="T batch has more entry fills than exit fills and remains open",
    sql=_OPEN_UNBALANCED_BATCH_SQL,
    violation_disposition="BLOCK_P1_UNBALANCED_T_BATCH",
  ),
  AuditCheckSpec(
    code="P0_OUTSTANDING_T_EXIT_PLAN_OWNER_INVALID",
    category="exit_plan",
    severity="BLOCKER",
    blocks_p1=True,
    description="outstanding T_TRADE_BATCH ExitPlan violates the current owner/source matrix",
    sql=_OUTSTANDING_T_EXIT_PLAN_OWNER_INVALID_SQL,
    violation_disposition="BLOCK_P1_INVALID_OUTSTANDING_T_EXIT_PLAN_OWNER",
  ),
  AuditCheckSpec(
    code="P0_OUTSTANDING_T_EXIT_PLAN_COUNT",
    category="exit_plan",
    severity="FACT",
    blocks_p1=False,
    description="outstanding T_TRADE_BATCH ExitPlan count, including ERROR obligations",
    sql=_OUTSTANDING_T_EXIT_PLAN_COUNT_SQL,
  ),
  AuditCheckSpec(
    code="P0_LEGACY_T_INTENT_OWNER_INVALID_COUNT",
    category="history",
    severity="WARNING",
    blocks_p1=False,
    description="legacy T intent violates the current owner reference rules",
    sql=_LEGACY_T_INTENT_OWNER_INVALID_SQL,
    violation_disposition="REVIEW_LEGACY_T_INTENT_OWNER_INVALID",
  ),
  AuditCheckSpec(
    code="P0_TERMINAL_T_EXIT_PLAN_OWNER_INVALID",
    category="history",
    severity="WARNING",
    blocks_p1=False,
    description="terminal historical T ExitPlan violates its source/owner matrix",
    sql=_TERMINAL_EXIT_PLAN_OWNER_INVALID_SQL,
    violation_disposition="REVIEW_TERMINAL_T_EXIT_PLAN_HISTORY",
  ),
)


def _normalise_count(value: Any) -> int:
  try:
    return max(0, int(value or 0))
  except (TypeError, ValueError, OverflowError):
    return 0


def _result_scalar(result: Any) -> Any:
  """Read a SQLAlchemy result while keeping test doubles intentionally small."""

  scalar_one_or_none = getattr(result, "scalar_one_or_none", None)
  if callable(scalar_one_or_none):
    return scalar_one_or_none()
  scalar = getattr(result, "scalar", None)
  if callable(scalar):
    return scalar()
  scalar_one = getattr(result, "scalar_one", None)
  if callable(scalar_one):
    return scalar_one()
  mappings = getattr(result, "mappings", None)
  if callable(mappings):
    mapped = mappings()
    first = getattr(mapped, "first", None)
    if callable(first):
      row = first()
      if row is None:
        return None
      if isinstance(row, Mapping):
        return row.get("count_value", next(iter(row.values()), None))
  return None


def _result_rows(result: Any) -> list[Any]:
  mappings = getattr(result, "mappings", None)
  if callable(mappings):
    mapped = mappings()
    all_rows = getattr(mapped, "all", None)
    if callable(all_rows):
      return list(all_rows())
  scalars = getattr(result, "scalars", None)
  if callable(scalars):
    values = scalars()
    all_values = getattr(values, "all", None)
    if callable(all_values):
      return list(all_values())
  all_rows = getattr(result, "all", None)
  if callable(all_rows):
    return list(all_rows())
  return []


async def _query_count(connection: AsyncConnection, sql: str) -> int:
  result = await connection.execute(text(sql))
  return _normalise_count(_result_scalar(result))


async def _existing_tables(connection: AsyncConnection) -> set[str]:
  table_names = ", ".join(f"'{name}'" for name in REQUIRED_TABLES)
  result = await connection.execute(
    text(
      """
      SELECT table_name
      FROM information_schema.tables
      WHERE table_schema = 'public'
        AND table_name IN (__required_table_names__)
      ORDER BY table_name
      """.replace("__required_table_names__", table_names)
    )
  )
  values: set[str] = set()
  for row in _result_rows(result):
    if isinstance(row, Mapping):
      value = row.get("table_name")
    elif isinstance(row, (tuple, list)):
      value = row[0] if row else None
    else:
      value = row
    if value is not None:
      values.add(str(value))
  return values


def _check_payload(
  spec: AuditCheckSpec,
  count: int | None,
  *,
  skipped: bool = False,
) -> dict[str, Any]:
  if skipped:
    disposition = "SKIPPED_MISSING_REQUIRED_TABLE"
  elif count:
    disposition = "OBSERVED" if spec.severity == "FACT" else spec.violation_disposition
  else:
    disposition = spec.clear_disposition
  return {
    "code": spec.code,
    "category": spec.category,
    "severity": spec.severity,
    "count": count,
    "blocksP1": spec.blocks_p1,
    "description": spec.description,
    "disposition": disposition,
  }


async def audit_connection(connection: AsyncConnection) -> dict[str, Any]:
  """Audit one already-open connection without mutating it.

  The first statement is always the ``information_schema`` table check.  A
  missing table produces a fail-closed blocker and all dependent queries are
  skipped; in particular, the function never treats an unqueryable table as an
  empty table.
  """

  existing_tables = await _existing_tables(connection)
  missing_tables = tuple(
    table_name for table_name in REQUIRED_TABLES if table_name not in existing_tables
  )
  checks: list[dict[str, Any]] = []
  if missing_tables:
    checks.append(
      {
        "code": "P0_REQUIRED_TABLES_MISSING",
        "category": "schema",
        "severity": "BLOCKER",
        "count": len(missing_tables),
        "blocksP1": True,
        "description": "required P0 audit table is missing; dependent checks were not run",
        "disposition": "BLOCK_P1_MISSING_REQUIRED_TABLE",
        "missingTables": list(missing_tables),
      }
    )
  for spec in AUDIT_CHECK_SPECS:
    if missing_tables and any(table not in existing_tables for table in spec.required_tables):
      checks.append(_check_payload(spec, None, skipped=True))
      continue
    checks.append(_check_payload(spec, await _query_count(connection, spec.sql)))

  blocker_count = sum(
    1
    for check in checks
    if check["severity"] == "BLOCKER" and check["count"] not in (None, 0)
  )
  warning_count = sum(
    1
    for check in checks
    if check["severity"] == "WARNING" and check["count"] not in (None, 0)
  )
  return {
    "schemaVersion": P0_SCHEMA_VERSION,
    "scope": AUDIT_SCOPE,
    "currentProtocol": CURRENT_PROTOCOL_VERSION,
    "targetProtocol": TARGET_PROTOCOL_VERSION,
    "readyForP1": blocker_count == 0 and not missing_tables,
    "summary": {
      "blockerCount": blocker_count,
      "warningCount": warning_count,
      "checkCount": len(checks),
    },
    "checks": checks,
    "requiredTables": list(REQUIRED_TABLES),
  }


def _path_parts(relative_path: Path) -> tuple[str, ...]:
  return tuple(part.lower() for part in relative_path.parts)


def _source_group(relative_path: Path) -> str | None:
  parts = _path_parts(relative_path)
  path_text = "/".join(parts)
  if (
    path_text.startswith("packages/infrastructure/src/quantx_infrastructure/models/")
    or path_text.startswith("packages/infrastructure/alembic/")
    or path_text.startswith("apps/api/migrations/")
  ):
    return "DB"
  if path_text.startswith("packages/contracts/"):
    return "contracts"
  if path_text.startswith("packages/domain/"):
    return "domain"
  if path_text.startswith("packages/application/"):
    return "application"
  if path_text.startswith("packages/infrastructure/"):
    return "infrastructure"
  if path_text.startswith("apps/engine/"):
    return "Engine"
  if path_text.startswith("apps/api/src/quantx_api/gqlapi/") or path_text.startswith(
    "apps/web/"
  ):
    return "GraphQL/Web"
  if path_text.startswith("apps/api/"):
    return "API"
  if path_text.startswith("apps/worker/"):
    return "Worker"
  if path_text.startswith("apps/qmt-agent/"):
    return "QMT Agent"
  return None


def _is_excluded(relative_path: Path) -> bool:
  return any(part.lower() in _EXCLUDED_PARTS for part in relative_path.parts)


def scan_run_identity_assumptions(repository_root: str | Path) -> dict[str, Any]:
  """Scan source identity assumptions into deterministic, aggregate groups."""

  root = Path(repository_root).resolve()
  grouped: dict[str, list[dict[str, Any]]] = {group: [] for group in _GROUP_ORDER}
  for directory, dirnames, filenames in os.walk(root):
    dirnames[:] = sorted(
      directory_name
      for directory_name in dirnames
      if directory_name.lower() not in _EXCLUDED_PARTS
    )
    for filename in sorted(filenames, key=str.lower):
      path = Path(directory) / filename
      if path.suffix.lower() not in _SOURCE_EXTENSIONS:
        continue
      try:
        relative_path = path.relative_to(root)
      except ValueError:
        continue
      if _is_excluded(relative_path):
        continue
      group = _source_group(relative_path)
      if group is None:
        continue
      try:
        contents = path.read_text(encoding="utf-8")
      except (OSError, UnicodeError):
        continue
      match_count = sum(
        len(regex.findall(contents)) for _, regex in _SOURCE_PATTERN_REGEXES
      )
      if not match_count:
        continue
      grouped[group].append(
        {
          "path": relative_path.as_posix(),
          "matchCount": match_count,
        }
      )

  groups: dict[str, dict[str, Any]] = {}
  for group in _GROUP_ORDER:
    files = sorted(grouped[group], key=lambda item: str(item["path"]))
    groups[group] = {
      "fileCount": len(files),
      "matchCount": sum(int(item["matchCount"]) for item in files),
      "files": files,
    }
  return {"groups": groups}


async def run_read_only_audit(
  engine: AsyncEngine,
  repository_root: str | Path,
) -> dict[str, Any]:
  """Run a fresh, explicit read-only transaction and append source inventory."""

  async with engine.connect() as connection:
    transaction = await connection.begin()
    try:
      await connection.execute(text("SET TRANSACTION READ ONLY"))
      report = await audit_connection(connection)
    finally:
      await transaction.rollback()
  report["sourceInventory"] = scan_run_identity_assumptions(repository_root)
  return report


def exit_code_for_report(report: Mapping[str, Any], require_ready: bool) -> int:
  """Return the CLI status without consulting a database or process state."""

  return 2 if require_ready and not report.get("readyForP1", False) else 0


def render_markdown(report: Mapping[str, Any]) -> str:
  """Render the aggregate report without exposing database identifiers."""

  summary = dict(report.get("summary") or {})
  lines = [
    "# Multi-instrument T assistant P0 audit",
    "",
    f"- Scope: `{report.get('scope', AUDIT_SCOPE)}`",
    f"- Protocol: `{report.get('currentProtocol', CURRENT_PROTOCOL_VERSION)}` -> "
    f"`{report.get('targetProtocol', TARGET_PROTOCOL_VERSION)}`",
    f"- Ready for P1: `{str(bool(report.get('readyForP1'))).lower()}`",
    f"- Blockers: `{summary.get('blockerCount', 0)}`; "
    f"warnings: `{summary.get('warningCount', 0)}`; "
    f"checks: `{summary.get('checkCount', 0)}`",
    "",
    "## Checks",
    "",
    "| Code | Category | Severity | Count | Blocks P1 | Disposition |",
    "| --- | --- | --- | ---: | :---: | --- |",
  ]
  for check in report.get("checks", []):
    lines.append(
      "| {code} | {category} | {severity} | {count} | {blocks} | {disposition} |".format(
        code=check.get("code", ""),
        category=check.get("category", ""),
        severity=check.get("severity", ""),
        count="unknown" if check.get("count") is None else check.get("count", 0),
        blocks="yes" if check.get("blocksP1") else "no",
        disposition=check.get("disposition", ""),
      )
    )
  inventory = dict(report.get("sourceInventory") or {})
  groups = dict(inventory.get("groups") or {})
  lines.extend(
    [
      "",
      "## Source identity inventory",
      "",
      "| Group | Files | Matches |",
      "| --- | ---: | ---: |",
    ]
  )
  for group in _GROUP_ORDER:
    item = dict(groups.get(group) or {})
    lines.append(
      f"| {group} | {item.get('fileCount', 0)} | {item.get('matchCount', 0)} |"
    )
  return "\n".join(lines) + "\n"


def _resolve_repository_root(value: str | None) -> Path:
  if value:
    return Path(value).resolve()
  return Path(__file__).resolve().parents[5]


async def _run_cli(args: argparse.Namespace) -> int:
  repository_root = _resolve_repository_root(args.repository_root)
  try:
    if args.database_url:
      database_url = args.database_url
    else:
      from quantx_infrastructure.runtime_store import resolve_database_url

      database_url = resolve_database_url()
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
      report = await run_read_only_audit(engine, repository_root)
    finally:
      await engine.dispose()
  except Exception as exc:  # noqa: BLE001 - CLI must not print secret-bearing errors
    print(f"P0 audit failed: {type(exc).__name__}", file=sys.stderr)
    return 1

  if args.format == "markdown":
    print(render_markdown(report), end="")
  else:
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
  return exit_code_for_report(report, args.require_ready)


def build_argument_parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(description="Read-only P0 T assistant audit")
  parser.add_argument(
    "--repository-root",
    default=None,
    help="repository root (defaults to the QuantX repository root)",
  )
  parser.add_argument(
    "--database-url",
    default=None,
    help="optional async SQLAlchemy database URL; default loads development config",
  )
  parser.add_argument(
    "--format",
    choices=("json", "markdown"),
    default="json",
    help="output format",
  )
  parser.add_argument(
    "--require-ready",
    action="store_true",
    help="exit 2 when the report is not ready for P1",
  )
  return parser


def main(argv: Iterable[str] | None = None) -> int:
  return asyncio.run(_run_cli(build_argument_parser().parse_args(argv)))


if __name__ == "__main__":
  raise SystemExit(main())


__all__ = [
  "AUDIT_CHECK_SPECS",
  "AUDIT_SCOPE",
  "CURRENT_PROTOCOL_VERSION",
  "P0_SCHEMA_VERSION",
  "REQUIRED_TABLES",
  "TARGET_OWNER_TYPES",
  "TARGET_PROTOCOL_VERSION",
  "audit_connection",
  "build_argument_parser",
  "exit_code_for_report",
  "main",
  "render_markdown",
  "run_read_only_audit",
  "scan_run_identity_assumptions",
]
