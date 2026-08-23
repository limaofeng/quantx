"""Add restart-safe T-trade candidate outcome maturation records."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "20260823_0030"
down_revision = "20260823_0029"
branch_labels = None
depends_on = None


_CANDIDATE_TABLE = "t_trade_candidate_outcomes"
_ROLLOUT_EVENTS_TABLE = "account_trading_rollout_events"
_RUNTIME_EVENTS_TABLE = "strategy_runtime_events"
_RUNTIME_REPAIR_INDEX = "ix_strategy_runtime_event_run_created"


def _candidate_columns() -> dict[str, tuple[sa.types.TypeEngine, bool]]:
  return {
    "id": (sa.String(length=36), False),
    "account_id": (sa.String(length=50), False),
    "strategy_run_id": (sa.String(length=36), False),
    "instrument_code": (sa.String(length=20), False),
    "candidate_id": (sa.String(length=128), False),
    "candidate_fingerprint": (sa.String(length=64), False),
    "candidate_at": (sa.DateTime(), False),
    "source_time_ms": (sa.BigInteger(), False),
    "tick_ordinal": (sa.BigInteger(), False),
    "continuity_generation": (sa.String(length=64), False),
    "reference_price": (sa.Float(), False),
    "policy_version": (sa.String(length=64), False),
    "feature_schema_version": (sa.String(length=32), False),
    "profile_version": (sa.String(length=64), True),
    "profile_fingerprint": (sa.String(length=64), True),
    "outcome_schema_version": (sa.String(length=32), False),
    "status": (sa.String(length=24), False),
    "post_fill_status": (sa.String(length=24), False),
    "unavailable_reason": (sa.String(length=64), True),
    "state": (sa.JSON(), False),
    "content_fingerprint": (sa.String(length=64), False),
    "state_version": (sa.Integer(), False),
    "finalized_at": (sa.DateTime(), True),
    "created_at": (sa.DateTime(), False),
    "updated_at": (sa.DateTime(), False),
  }


_EXPECTED_UNIQUE = {
  "uq_t_trade_candidate_outcome_run_candidate": (
    "strategy_run_id",
    "candidate_id",
  ),
}

_EXPECTED_CHECKS = frozenset(
  {
    "ck_t_trade_candidate_outcome_status",
    "ck_t_trade_candidate_outcome_post_fill_status",
    "ck_t_trade_candidate_outcome_source_identity",
    "ck_t_trade_candidate_outcome_reference_price",
    "ck_t_trade_candidate_outcome_state_version",
    "ck_t_trade_candidate_outcome_terminal_shape",
  }
)

_EXPECTED_CHECK_FRAGMENTS = {
  "ck_t_trade_candidate_outcome_status": (
    "status",
    "OBSERVING",
    "MATURED",
    "UNAVAILABLE",
  ),
  "ck_t_trade_candidate_outcome_post_fill_status": (
    "post_fill_status",
    "WAITING_ENTRY",
    "OBSERVING",
    "MATURED",
    "UNAVAILABLE",
  ),
  "ck_t_trade_candidate_outcome_source_identity": (
    "source_time_ms",
    ">= 0",
    "tick_ordinal",
  ),
  "ck_t_trade_candidate_outcome_reference_price": (
    "reference_price",
    "> 0",
  ),
  "ck_t_trade_candidate_outcome_state_version": (
    "state_version",
    ">= 1",
  ),
  "ck_t_trade_candidate_outcome_terminal_shape": (
    "status",
    "finalized_at",
    "MATURED",
    "UNAVAILABLE",
  ),
}

_EXPECTED_INDEXES = {
  "ix_t_trade_candidate_outcome_run_status": (
    "strategy_run_id",
    "status",
    "instrument_code",
  ),
  "ix_t_trade_candidate_outcome_account_time": (
    "account_id",
    "candidate_at",
    "id",
  ),
  "ix_t_trade_candidate_outcome_run_post_fill_status": (
    "strategy_run_id",
    "post_fill_status",
    "instrument_code",
  ),
}


def _get_inspector():
  return sa_inspect(op.get_bind())


def _type_matches(actual, expected) -> bool:
  actual_affinity = getattr(actual, "_type_affinity", type(actual))
  expected_affinity = getattr(expected, "_type_affinity", type(expected))
  if actual_affinity != expected_affinity:
    return False
  for attribute in ("length", "precision", "scale"):
    expected_value = getattr(expected, attribute, None)
    if (
      expected_value is not None and getattr(actual, attribute, None) != expected_value
    ):
      return False
  return True


def _fail(detail: str) -> None:
  raise RuntimeError("Mismatched T-trade candidate schema: " + detail)


def _check_sql_contains(sqltext: object, fragments: tuple[str, ...]) -> bool:
  normalized = " ".join(str(sqltext or "").lower().split())
  return all(fragment.lower() in normalized for fragment in fragments)


def _validate_candidate_table(inspector) -> None:
  expected_columns = _candidate_columns()
  actual_columns = {
    str(column["name"]): column for column in inspector.get_columns(_CANDIDATE_TABLE)
  }
  if set(actual_columns) != set(expected_columns):
    _fail(
      "columns expected="
      + ",".join(sorted(expected_columns))
      + " actual="
      + ",".join(sorted(actual_columns))
    )
  for name, (expected_type, expected_nullable) in expected_columns.items():
    actual = actual_columns[name]
    if bool(actual.get("nullable", True)) != expected_nullable:
      _fail(f"column {name} nullable mismatch")
    if not _type_matches(actual.get("type"), expected_type):
      _fail(f"column {name} type mismatch")

  if tuple(
    inspector.get_pk_constraint(_CANDIDATE_TABLE).get("constrained_columns") or ()
  ) != ("id",):
    _fail("primary key mismatch")
  unique_constraints = {
    str(constraint.get("name")): tuple(constraint.get("column_names") or ())
    for constraint in inspector.get_unique_constraints(_CANDIDATE_TABLE)
    if constraint.get("name")
  }
  for name, columns in _EXPECTED_UNIQUE.items():
    if unique_constraints.get(name) != columns:
      _fail(f"unique constraint {name} mismatch")
  actual_checks = {
    str(constraint.get("name")): constraint.get("sqltext")
    for constraint in inspector.get_check_constraints(_CANDIDATE_TABLE)
    if constraint.get("name")
  }
  missing_checks = _EXPECTED_CHECKS - set(actual_checks)
  if missing_checks:
    _fail("missing check constraints " + ",".join(sorted(missing_checks)))
  for name, fragments in _EXPECTED_CHECK_FRAGMENTS.items():
    if not _check_sql_contains(actual_checks.get(name), fragments):
      _fail(f"check constraint {name} expression mismatch")
  indexes = {
    str(index.get("name")): index
    for index in inspector.get_indexes(_CANDIDATE_TABLE)
    if index.get("name")
  }
  for name, columns in _EXPECTED_INDEXES.items():
    index = indexes.get(name)
    if (
      index is None
      or bool(index.get("unique"))
      or tuple(index.get("column_names") or ()) != columns
    ):
      _fail(f"index {name} mismatch")


def _event_id_length(inspector) -> int:
  if _ROLLOUT_EVENTS_TABLE not in set(inspector.get_table_names()):
    _fail(f"missing table {_ROLLOUT_EVENTS_TABLE}")
  columns = {
    str(column["name"]): column
    for column in inspector.get_columns(_ROLLOUT_EVENTS_TABLE)
  }
  column = columns.get("event_id")
  if column is None or bool(column.get("nullable", True)):
    _fail("event_id column missing or nullable")
  actual_type = column.get("type")
  if getattr(actual_type, "_type_affinity", type(actual_type)) is not sa.String:
    _fail("event_id type is not VARCHAR")
  length = getattr(actual_type, "length", None)
  if length not in (36, 128):
    _fail(f"event_id length {length!r} is unsupported")
  return int(length)


def _validate_runtime_repair_index(inspector) -> bool:
  if _RUNTIME_EVENTS_TABLE not in set(inspector.get_table_names()):
    _fail(f"missing table {_RUNTIME_EVENTS_TABLE}")
  indexes = {
    str(index.get("name")): index
    for index in inspector.get_indexes(_RUNTIME_EVENTS_TABLE)
    if index.get("name")
  }
  index = indexes.get(_RUNTIME_REPAIR_INDEX)
  if index is None:
    return False
  expected = ("strategy_run_id", "created_at", "event_id")
  if bool(index.get("unique")) or tuple(index.get("column_names") or ()) != expected:
    _fail(f"index {_RUNTIME_REPAIR_INDEX} mismatch")
  return True


def _create_candidate_table() -> None:
  op.create_table(
    "t_trade_candidate_outcomes",
    sa.Column("id", sa.String(length=36), nullable=False),
    sa.Column("account_id", sa.String(length=50), nullable=False),
    sa.Column("strategy_run_id", sa.String(length=36), nullable=False),
    sa.Column("instrument_code", sa.String(length=20), nullable=False),
    sa.Column("candidate_id", sa.String(length=128), nullable=False),
    sa.Column("candidate_fingerprint", sa.String(length=64), nullable=False),
    sa.Column("candidate_at", sa.DateTime(), nullable=False),
    sa.Column("source_time_ms", sa.BigInteger(), nullable=False),
    sa.Column("tick_ordinal", sa.BigInteger(), nullable=False),
    sa.Column("continuity_generation", sa.String(length=64), nullable=False),
    sa.Column("reference_price", sa.Float(), nullable=False),
    sa.Column("policy_version", sa.String(length=64), nullable=False),
    sa.Column("feature_schema_version", sa.String(length=32), nullable=False),
    sa.Column("profile_version", sa.String(length=64), nullable=True),
    sa.Column("profile_fingerprint", sa.String(length=64), nullable=True),
    sa.Column("outcome_schema_version", sa.String(length=32), nullable=False),
    sa.Column("status", sa.String(length=24), nullable=False),
    sa.Column("post_fill_status", sa.String(length=24), nullable=False),
    sa.Column("unavailable_reason", sa.String(length=64), nullable=True),
    sa.Column("state", sa.JSON(), nullable=False),
    sa.Column("content_fingerprint", sa.String(length=64), nullable=False),
    sa.Column("state_version", sa.Integer(), nullable=False, server_default="1"),
    sa.Column("finalized_at", sa.DateTime(), nullable=True),
    sa.Column(
      "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
      "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
    ),
    sa.CheckConstraint(
      "status IN ('OBSERVING', 'MATURED', 'UNAVAILABLE')",
      name="ck_t_trade_candidate_outcome_status",
    ),
    sa.CheckConstraint(
      "post_fill_status IN ('WAITING_ENTRY', 'OBSERVING', 'MATURED', 'UNAVAILABLE')",
      name="ck_t_trade_candidate_outcome_post_fill_status",
    ),
    sa.CheckConstraint(
      "source_time_ms >= 0 AND tick_ordinal >= 0",
      name="ck_t_trade_candidate_outcome_source_identity",
    ),
    sa.CheckConstraint(
      "reference_price > 0",
      name="ck_t_trade_candidate_outcome_reference_price",
    ),
    sa.CheckConstraint(
      "state_version >= 1",
      name="ck_t_trade_candidate_outcome_state_version",
    ),
    sa.CheckConstraint(
      "(status = 'OBSERVING' AND finalized_at IS NULL) OR "
      "(status IN ('MATURED', 'UNAVAILABLE') AND finalized_at IS NOT NULL)",
      name="ck_t_trade_candidate_outcome_terminal_shape",
    ),
    sa.PrimaryKeyConstraint("id"),
    sa.UniqueConstraint(
      "strategy_run_id",
      "candidate_id",
      name="uq_t_trade_candidate_outcome_run_candidate",
    ),
    comment="做 T 候选因果结果成熟状态",
  )
  op.create_index(
    "ix_t_trade_candidate_outcome_run_status",
    "t_trade_candidate_outcomes",
    ["strategy_run_id", "status", "instrument_code"],
  )
  op.create_index(
    "ix_t_trade_candidate_outcome_account_time",
    "t_trade_candidate_outcomes",
    ["account_id", "candidate_at", "id"],
  )
  op.create_index(
    "ix_t_trade_candidate_outcome_run_post_fill_status",
    "t_trade_candidate_outcomes",
    ["strategy_run_id", "post_fill_status", "instrument_code"],
  )


def upgrade() -> None:
  # GraphQL operation ids are namespaced SHA-256 keys rather than UUIDs.
  # Widen the pre-existing audit column in the same V3 revision so the V3
  # chain remains independently applicable before any unrelated follow-up
  # revision is present.  A development ``create_all`` may already have
  # created the candidate table and the repair index, so validate those
  # objects before adopting them instead of issuing duplicate CREATE DDL.
  inspector = _get_inspector()
  event_id_length = _event_id_length(inspector)
  existing_tables = set(inspector.get_table_names())
  candidate_exists = _CANDIDATE_TABLE in existing_tables
  if candidate_exists:
    _validate_candidate_table(inspector)

  repair_index_exists = _validate_runtime_repair_index(inspector)

  if event_id_length == 36:
    op.alter_column(
      "account_trading_rollout_events",
      "event_id",
      existing_type=sa.String(length=36),
      type_=sa.String(length=128),
      existing_nullable=False,
    )
  if not candidate_exists:
    _create_candidate_table()
  if not repair_index_exists:
    op.create_index(
      _RUNTIME_REPAIR_INDEX,
      _RUNTIME_EVENTS_TABLE,
      ["strategy_run_id", "created_at", "event_id"],
    )


def downgrade() -> None:
  # Reverting the column width would truncate namespaced operation ids.
  # Production schema downgrades are intentionally disabled; use a forward
  # migration if a future schema transition is required.
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
