"""Add immutable T-trade opportunity evaluations and instrument profiles."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "20260823_0029"
down_revision = "20260822_0028"
branch_labels = None
depends_on = None


_TABLES = frozenset(
  {
    "t_trade_opportunity_evaluations",
    "t_trade_instrument_profiles",
  }
)


def _expected_columns(table_name: str) -> dict[str, tuple[sa.types.TypeEngine, bool]]:
  if table_name == "t_trade_opportunity_evaluations":
    return {
      "id": (sa.String(length=36), False),
      "event_key": (sa.String(length=160), False),
      "account_id": (sa.String(length=50), False),
      "strategy_run_id": (sa.String(length=36), False),
      "instrument_code": (sa.String(length=20), False),
      "candidate_id": (sa.String(length=128), True),
      "evaluated_at": (sa.DateTime(), False),
      "record_kind": (sa.String(length=24), False),
      "event_type": (sa.String(length=64), False),
      "window_started_at": (sa.DateTime(), True),
      "window_ended_at": (sa.DateTime(), True),
      "coalesced_count": (sa.Integer(), False),
      "policy_version": (sa.String(length=64), False),
      "schema_version": (sa.String(length=32), False),
      "content_fingerprint": (sa.String(length=64), False),
      "payload": (sa.JSON(), False),
      "metrics": (sa.JSON(), False),
      "created_at": (sa.DateTime(), False),
    }
  if table_name == "t_trade_instrument_profiles":
    return {
      "id": (sa.String(length=36), False),
      "instrument_code": (sa.String(length=20), False),
      "as_of": (sa.DateTime(), False),
      "profile": (sa.JSON(), False),
      "schema_version": (sa.String(length=32), False),
      "version": (sa.String(length=64), False),
      "fingerprint": (sa.String(length=64), False),
      "metrics": (sa.JSON(), False),
      "data_manifest": (sa.JSON(), False),
      "created_at": (sa.DateTime(), False),
    }
  raise RuntimeError(f"Unknown T-trade intelligence table: {table_name}")


_EXPECTED_UNIQUE_CONSTRAINTS: dict[str, dict[str, tuple[str, ...]]] = {
  "t_trade_opportunity_evaluations": {
    "uq_t_trade_opportunity_evaluation_event_key": ("event_key",),
  },
  "t_trade_instrument_profiles": {
    "uq_t_trade_instrument_profile_fingerprint": (
      "instrument_code",
      "fingerprint",
    ),
    "uq_t_trade_instrument_profile_coordinate": (
      "instrument_code",
      "as_of",
      "schema_version",
      "version",
    ),
  },
}


_EXPECTED_CHECK_CONSTRAINTS: dict[str, frozenset[str]] = {
  "t_trade_opportunity_evaluations": frozenset(
    {
      "ck_t_trade_evaluation_record_kind",
      "ck_t_trade_evaluation_coalesced_count",
      "ck_t_trade_evaluation_candidate_material",
      "ck_t_trade_evaluation_window_shape",
    }
  ),
  "t_trade_instrument_profiles": frozenset(),
}


_EXPECTED_CHECK_FRAGMENTS: dict[str, dict[str, tuple[str, ...]]] = {
  "t_trade_opportunity_evaluations": {
    "ck_t_trade_evaluation_record_kind": (
      "record_kind",
      "MATERIAL",
      "COALESCED_DIAGNOSTIC",
    ),
    "ck_t_trade_evaluation_coalesced_count": ("coalesced_count", ">= 1"),
    "ck_t_trade_evaluation_candidate_material": (
      "record_kind",
      "candidate_id",
      "NULL",
    ),
    "ck_t_trade_evaluation_window_shape": (
      "record_kind",
      "window_started_at",
      "window_ended_at",
      "evaluated_at",
      "COALESCED_DIAGNOSTIC",
    ),
  },
  "t_trade_instrument_profiles": {},
}


_EXPECTED_INDEXES: dict[str, dict[str, tuple[str, ...]]] = {
  "t_trade_opportunity_evaluations": {
    "ix_t_trade_evaluation_account_time": (
      "account_id",
      "evaluated_at",
      "id",
    ),
    "ix_t_trade_evaluation_account_instrument_time": (
      "account_id",
      "instrument_code",
      "evaluated_at",
      "id",
    ),
    "ix_t_trade_evaluation_run_time": (
      "strategy_run_id",
      "evaluated_at",
      "id",
    ),
    "ix_t_trade_evaluation_account_candidate_time": (
      "account_id",
      "candidate_id",
      "evaluated_at",
      "id",
    ),
  },
  "t_trade_instrument_profiles": {
    "ix_t_trade_instrument_profile_asof": (
      "instrument_code",
      "as_of",
      "id",
    ),
  },
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


def _fail(table_name: str, detail: str) -> None:
  raise RuntimeError(
    f"Partial or mismatched T-trade intelligence schema for {table_name}: {detail}"
  )


def _check_sql_contains(sqltext: object, fragments: tuple[str, ...]) -> bool:
  normalized = " ".join(str(sqltext or "").lower().split())
  return all(fragment.lower() in normalized for fragment in fragments)


def _validate_table(inspector, table_name: str) -> None:
  expected_columns = _expected_columns(table_name)
  actual_columns = {
    str(column["name"]): column for column in inspector.get_columns(table_name)
  }
  if set(actual_columns) != set(expected_columns):
    _fail(
      table_name,
      "columns expected="
      + ",".join(sorted(expected_columns))
      + " actual="
      + ",".join(sorted(actual_columns)),
    )
  for name, (expected_type, expected_nullable) in expected_columns.items():
    actual = actual_columns[name]
    if bool(actual.get("nullable", True)) != expected_nullable:
      _fail(table_name, f"column {name} nullable mismatch")
    if not _type_matches(actual.get("type"), expected_type):
      _fail(table_name, f"column {name} type mismatch")

  primary_key = inspector.get_pk_constraint(table_name)
  if tuple(primary_key.get("constrained_columns") or ()) != ("id",):
    _fail(table_name, "primary key mismatch")

  unique_constraints = {
    str(constraint.get("name")): tuple(constraint.get("column_names") or ())
    for constraint in inspector.get_unique_constraints(table_name)
    if constraint.get("name")
  }
  for name, columns in _EXPECTED_UNIQUE_CONSTRAINTS[table_name].items():
    if unique_constraints.get(name) != columns:
      _fail(table_name, f"unique constraint {name} mismatch")

  actual_checks = {
    str(constraint.get("name")): constraint.get("sqltext")
    for constraint in inspector.get_check_constraints(table_name)
    if constraint.get("name")
  }
  missing_checks = _EXPECTED_CHECK_CONSTRAINTS[table_name] - set(actual_checks)
  if missing_checks:
    _fail(table_name, "missing check constraints " + ",".join(sorted(missing_checks)))
  for name, fragments in _EXPECTED_CHECK_FRAGMENTS[table_name].items():
    if not _check_sql_contains(actual_checks.get(name), fragments):
      _fail(table_name, f"check constraint {name} expression mismatch")

  indexes = {
    str(index.get("name")): index
    for index in inspector.get_indexes(table_name)
    if index.get("name")
  }
  for name, columns in _EXPECTED_INDEXES[table_name].items():
    index = indexes.get(name)
    if (
      index is None
      or bool(index.get("unique"))
      or tuple(index.get("column_names") or ()) != columns
    ):
      _fail(table_name, f"index {name} mismatch")


def _create_evaluation_table() -> None:
  op.create_table(
    "t_trade_opportunity_evaluations",
    sa.Column("id", sa.String(length=36), nullable=False),
    sa.Column("event_key", sa.String(length=160), nullable=False),
    sa.Column("account_id", sa.String(length=50), nullable=False),
    sa.Column("strategy_run_id", sa.String(length=36), nullable=False),
    sa.Column("instrument_code", sa.String(length=20), nullable=False),
    sa.Column("candidate_id", sa.String(length=128), nullable=True),
    sa.Column("evaluated_at", sa.DateTime(), nullable=False),
    sa.Column("record_kind", sa.String(length=24), nullable=False),
    sa.Column("event_type", sa.String(length=64), nullable=False),
    sa.Column("window_started_at", sa.DateTime(), nullable=True),
    sa.Column("window_ended_at", sa.DateTime(), nullable=True),
    sa.Column(
      "coalesced_count",
      sa.Integer(),
      nullable=False,
      server_default="1",
    ),
    sa.Column("policy_version", sa.String(length=64), nullable=False),
    sa.Column("schema_version", sa.String(length=32), nullable=False),
    sa.Column("content_fingerprint", sa.String(length=64), nullable=False),
    sa.Column("payload", sa.JSON(), nullable=False),
    sa.Column("metrics", sa.JSON(), nullable=False),
    sa.Column(
      "created_at",
      sa.DateTime(),
      nullable=False,
      server_default=sa.func.now(),
    ),
    sa.CheckConstraint(
      "record_kind IN ('MATERIAL', 'COALESCED_DIAGNOSTIC')",
      name="ck_t_trade_evaluation_record_kind",
    ),
    sa.CheckConstraint(
      "coalesced_count >= 1",
      name="ck_t_trade_evaluation_coalesced_count",
    ),
    sa.CheckConstraint(
      "record_kind = 'MATERIAL' OR candidate_id IS NULL",
      name="ck_t_trade_evaluation_candidate_material",
    ),
    sa.CheckConstraint(
      "(record_kind = 'MATERIAL' "
      "AND coalesced_count = 1 "
      "AND window_started_at IS NULL "
      "AND window_ended_at IS NULL) "
      "OR (record_kind = 'COALESCED_DIAGNOSTIC' "
      "AND window_started_at IS NOT NULL "
      "AND window_ended_at IS NOT NULL "
      "AND window_started_at <= window_ended_at "
      "AND window_ended_at <= evaluated_at)",
      name="ck_t_trade_evaluation_window_shape",
    ),
    sa.PrimaryKeyConstraint("id"),
    sa.UniqueConstraint(
      "event_key",
      name="uq_t_trade_opportunity_evaluation_event_key",
    ),
    comment="做 T 机会评估不可变审计证据",
  )
  op.create_index(
    "ix_t_trade_evaluation_account_time",
    "t_trade_opportunity_evaluations",
    ["account_id", "evaluated_at", "id"],
  )
  op.create_index(
    "ix_t_trade_evaluation_account_instrument_time",
    "t_trade_opportunity_evaluations",
    ["account_id", "instrument_code", "evaluated_at", "id"],
  )
  op.create_index(
    "ix_t_trade_evaluation_run_time",
    "t_trade_opportunity_evaluations",
    ["strategy_run_id", "evaluated_at", "id"],
  )
  op.create_index(
    "ix_t_trade_evaluation_account_candidate_time",
    "t_trade_opportunity_evaluations",
    ["account_id", "candidate_id", "evaluated_at", "id"],
  )


def _create_profile_table() -> None:
  op.create_table(
    "t_trade_instrument_profiles",
    sa.Column("id", sa.String(length=36), nullable=False),
    sa.Column("instrument_code", sa.String(length=20), nullable=False),
    sa.Column("as_of", sa.DateTime(), nullable=False),
    sa.Column("profile", sa.JSON(), nullable=False),
    sa.Column("schema_version", sa.String(length=32), nullable=False),
    sa.Column("version", sa.String(length=64), nullable=False),
    sa.Column("fingerprint", sa.String(length=64), nullable=False),
    sa.Column("metrics", sa.JSON(), nullable=False),
    sa.Column("data_manifest", sa.JSON(), nullable=False),
    sa.Column(
      "created_at",
      sa.DateTime(),
      nullable=False,
      server_default=sa.func.now(),
    ),
    sa.PrimaryKeyConstraint("id"),
    sa.UniqueConstraint(
      "instrument_code",
      "fingerprint",
      name="uq_t_trade_instrument_profile_fingerprint",
    ),
    sa.UniqueConstraint(
      "instrument_code",
      "as_of",
      "schema_version",
      "version",
      name="uq_t_trade_instrument_profile_coordinate",
    ),
    comment="做 T 标的时点画像事实",
  )
  op.create_index(
    "ix_t_trade_instrument_profile_asof",
    "t_trade_instrument_profiles",
    ["instrument_code", "as_of", "id"],
  )


def upgrade() -> None:
  inspector = _get_inspector()
  existing_tables = set(inspector.get_table_names())
  existing_targets = _TABLES & existing_tables
  if existing_targets and existing_targets != _TABLES:
    missing = ", ".join(sorted(_TABLES - existing_targets))
    raise RuntimeError(
      "Partial T-trade intelligence schema detected; missing tables: " + missing
    )
  if existing_targets == _TABLES:
    for table_name in sorted(_TABLES):
      _validate_table(inspector, table_name)
    return

  _create_evaluation_table()
  _create_profile_table()


def downgrade() -> None:
  op.drop_index(
    "ix_t_trade_instrument_profile_asof",
    table_name="t_trade_instrument_profiles",
  )
  op.drop_table("t_trade_instrument_profiles")

  op.drop_index(
    "ix_t_trade_evaluation_account_candidate_time",
    table_name="t_trade_opportunity_evaluations",
  )
  op.drop_index(
    "ix_t_trade_evaluation_run_time",
    table_name="t_trade_opportunity_evaluations",
  )
  op.drop_index(
    "ix_t_trade_evaluation_account_instrument_time",
    table_name="t_trade_opportunity_evaluations",
  )
  op.drop_index(
    "ix_t_trade_evaluation_account_time",
    table_name="t_trade_opportunity_evaluations",
  )
  op.drop_table("t_trade_opportunity_evaluations")
