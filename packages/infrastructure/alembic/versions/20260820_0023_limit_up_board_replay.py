"""Add point-in-time universes and account-level board replay jobs."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260820_0023"
down_revision = "20260818_0022"
branch_labels = None
depends_on = None

_TARGET_TABLES = frozenset(
  {
    "limit_up_board_universe_snapshots",
    "limit_up_board_replay_jobs",
    "limit_up_board_replay_scenarios",
  }
)
_EXPECTED_COLUMNS = {
  "limit_up_board_universe_snapshots": {
    "id": (sa.String, False),
    "snapshot_key": (sa.String, False),
    "trade_date": (sa.Date, False),
    "observed_at": (sa.DateTime, False),
    "source_max_at": (sa.DateTime, True),
    "schema_version": (sa.Integer, False),
    "snapshot_version": (sa.String, False),
    "score_version": (sa.String, False),
    "feature_version": (sa.String, False),
    "model_version": (sa.String, False),
    "exit_policy_version": (sa.String, False),
    "candidate_count": (sa.Integer, False),
    "eligible_count": (sa.Integer, False),
    "payload": (sa.JSON, False),
    "created_at": (sa.DateTime, False),
    "updated_at": (sa.DateTime, False),
  },
  "limit_up_board_replay_jobs": {
    "id": (sa.String, False),
    "account_id": (sa.String, False),
    "status": (sa.String, False),
    "progress_pct": (sa.Float, False),
    "processed_until": (sa.DateTime, True),
    "revision": (sa.BigInteger, False),
    "scenario_profile": (sa.String, False),
    "request": (sa.JSON, False),
    "dataset_fingerprint": (sa.String, False),
    "config_fingerprint": (sa.String, False),
    "input_manifest": (sa.JSON, False),
    "data_quality": (sa.JSON, False),
    "error_message": (sa.String, True),
    "started_at": (sa.DateTime, True),
    "completed_at": (sa.DateTime, True),
    "created_at": (sa.DateTime, False),
    "updated_at": (sa.DateTime, False),
  },
  "limit_up_board_replay_scenarios": {
    "id": (sa.String, False),
    "job_id": (sa.String, False),
    "scenario_id": (sa.String, False),
    "backtest_id": (sa.String, False),
    "status": (sa.String, False),
    "progress_pct": (sa.Float, False),
    "processed_until": (sa.DateTime, True),
    "revision": (sa.BigInteger, False),
    "error_message": (sa.String, True),
    "confirmation_delay_ms": (sa.Integer, False),
    "participation_cap_pct": (sa.Float, False),
    "book_depth_participation_pct": (sa.Float, False),
    "created_at": (sa.DateTime, False),
    "updated_at": (sa.DateTime, False),
  },
}
_EXPECTED_INDEXES = {
  "limit_up_board_universe_snapshots": {
    "ix_limit_up_board_universe_snapshots_trade_date": ("trade_date",),
    "ix_limit_up_board_universe_date_asof": ("trade_date", "observed_at"),
  },
  "limit_up_board_replay_jobs": {
    "ix_limit_up_board_replay_jobs_account_id": ("account_id",),
    "ix_limit_up_board_replay_account_status": ("account_id", "status"),
  },
  "limit_up_board_replay_scenarios": {
    "ix_limit_up_board_replay_scenarios_job_id": ("job_id",),
  },
}
_EXPECTED_UNIQUES = {
  "limit_up_board_universe_snapshots": {
    ("snapshot_key",),
  },
  "limit_up_board_replay_jobs": set(),
  "limit_up_board_replay_scenarios": {
    ("job_id", "scenario_id"),
    ("backtest_id",),
  },
}
_EXPECTED_CHECKS = {
  "limit_up_board_universe_snapshots": {
    "ck_board_universe_candidate_count",
    "ck_board_universe_eligible_count",
  },
  "limit_up_board_replay_jobs": {
    "ck_limit_up_board_replay_job_status",
    "ck_limit_up_board_replay_job_progress",
  },
  "limit_up_board_replay_scenarios": {
    "ck_limit_up_board_replay_delay",
    "ck_limit_up_board_replay_participation",
    "ck_limit_up_board_replay_depth_participation",
    "ck_limit_up_board_replay_scenario_status",
    "ck_limit_up_board_replay_scenario_progress",
  },
}
_EXPECTED_FOREIGN_KEYS = {
  "limit_up_board_universe_snapshots": set(),
  "limit_up_board_replay_jobs": set(),
  "limit_up_board_replay_scenarios": {
    (("job_id",), "limit_up_board_replay_jobs", ("id",), "CASCADE"),
    (("backtest_id",), "strategy_backtests", ("id",), "RESTRICT"),
  },
}


def _validate_or_reject_existing_schema(inspector: object) -> bool:
  """Return True only when all pre-existing replay tables exactly match."""

  existing = _TARGET_TABLES.intersection(inspector.get_table_names())
  if not existing:
    return False
  if existing != _TARGET_TABLES:
    missing = sorted(_TARGET_TABLES - existing)
    present = sorted(existing)
    raise RuntimeError(
      "partial limit-up board replay schema exists; "
      f"present={present} missing={missing}"
    )
  for table_name in sorted(_TARGET_TABLES):
    _validate_existing_table(inspector, table_name)
  return True


def _validate_existing_table(inspector: object, table_name: str) -> None:
  expected_columns = _EXPECTED_COLUMNS[table_name]
  actual_columns = {
    str(column["name"]): column for column in inspector.get_columns(table_name)
  }
  if set(actual_columns) != set(expected_columns):
    raise RuntimeError(
      f"invalid existing {table_name} columns; "
      f"expected={sorted(expected_columns)} actual={sorted(actual_columns)}"
    )
  for column_name, (expected_type, expected_nullable) in expected_columns.items():
    column = actual_columns[column_name]
    if not isinstance(column.get("type"), expected_type):
      raise RuntimeError(
        f"invalid existing {table_name}.{column_name} type: {column.get('type')}"
      )
    if bool(column.get("nullable")) != expected_nullable:
      raise RuntimeError(
        f"invalid existing {table_name}.{column_name} nullability"
      )

  primary_key = tuple(
    str(value) for value in (inspector.get_pk_constraint(table_name).get("constrained_columns") or [])
  )
  if primary_key != ("id",):
    raise RuntimeError(f"invalid existing {table_name} primary key: {primary_key}")

  indexes = {
    str(index.get("name")): tuple(str(value) for value in index.get("column_names") or [])
    for index in inspector.get_indexes(table_name)
  }
  for index_name, columns in _EXPECTED_INDEXES[table_name].items():
    if indexes.get(index_name) != columns:
      raise RuntimeError(
        f"invalid existing {table_name} index {index_name}: {indexes.get(index_name)}"
      )

  unique_columns = {
    tuple(str(value) for value in constraint.get("column_names") or [])
    for constraint in inspector.get_unique_constraints(table_name)
  }
  if not _EXPECTED_UNIQUES[table_name].issubset(unique_columns):
    raise RuntimeError(
      f"invalid existing {table_name} unique constraints: {sorted(unique_columns)}"
    )

  check_names = {
    str(constraint.get("name"))
    for constraint in inspector.get_check_constraints(table_name)
  }
  if not _EXPECTED_CHECKS[table_name].issubset(check_names):
    raise RuntimeError(
      f"invalid existing {table_name} check constraints: {sorted(check_names)}"
    )

  foreign_keys = set()
  for constraint in inspector.get_foreign_keys(table_name):
    options = dict(constraint.get("options") or {})
    foreign_keys.add(
      (
        tuple(str(value) for value in constraint.get("constrained_columns") or []),
        str(constraint.get("referred_table") or ""),
        tuple(str(value) for value in constraint.get("referred_columns") or []),
        str(options.get("ondelete") or "").upper(),
      )
    )
  if not _EXPECTED_FOREIGN_KEYS[table_name].issubset(foreign_keys):
    raise RuntimeError(
      f"invalid existing {table_name} foreign keys: {sorted(foreign_keys)}"
    )


def upgrade() -> None:
  bind = op.get_bind()
  schema_inspector = inspect(bind)
  if _validate_or_reject_existing_schema(schema_inspector):
    return
  tables = set(schema_inspector.get_table_names())
  if "limit_up_board_universe_snapshots" not in tables:
    op.create_table(
      "limit_up_board_universe_snapshots",
      sa.Column("id", sa.String(36), primary_key=True),
      sa.Column("snapshot_key", sa.String(96), nullable=False),
      sa.Column("trade_date", sa.Date(), nullable=False),
      sa.Column("observed_at", sa.DateTime(), nullable=False),
      sa.Column("source_max_at", sa.DateTime(), nullable=True),
      sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
      sa.Column("snapshot_version", sa.String(64), nullable=False),
      sa.Column("score_version", sa.String(64), nullable=False),
      sa.Column("feature_version", sa.String(64), nullable=False),
      sa.Column("model_version", sa.String(64), nullable=False),
      sa.Column("exit_policy_version", sa.String(64), nullable=False),
      sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
      sa.Column("eligible_count", sa.Integer(), nullable=False, server_default="0"),
      sa.Column("payload", sa.JSON(), nullable=False),
      sa.Column("created_at", sa.DateTime(), nullable=False),
      sa.Column("updated_at", sa.DateTime(), nullable=False),
      sa.UniqueConstraint(
        "snapshot_key",
        name="uq_limit_up_board_universe_snapshot_key",
      ),
      sa.CheckConstraint(
        "candidate_count >= 0",
        name="ck_board_universe_candidate_count",
      ),
      sa.CheckConstraint(
        "eligible_count >= 0",
        name="ck_board_universe_eligible_count",
      ),
      comment="账户级打板回放的不可变候选池快照",
    )
    op.create_index(
      "ix_limit_up_board_universe_snapshots_trade_date",
      "limit_up_board_universe_snapshots",
      ["trade_date"],
    )
    op.create_index(
      "ix_limit_up_board_universe_date_asof",
      "limit_up_board_universe_snapshots",
      ["trade_date", "observed_at"],
    )

  if "limit_up_board_replay_jobs" not in tables:
    op.create_table(
      "limit_up_board_replay_jobs",
      sa.Column("id", sa.String(36), primary_key=True),
      sa.Column("account_id", sa.String(50), nullable=False),
      sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
      sa.Column("progress_pct", sa.Float(), nullable=False, server_default="0"),
      sa.Column("processed_until", sa.DateTime(), nullable=True),
      sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
      sa.Column(
        "scenario_profile",
        sa.String(32),
        nullable=False,
        server_default="STANDARD_V1",
      ),
      sa.Column("request", sa.JSON(), nullable=False),
      sa.Column("dataset_fingerprint", sa.String(64), nullable=False),
      sa.Column("config_fingerprint", sa.String(64), nullable=False),
      sa.Column("input_manifest", sa.JSON(), nullable=False),
      sa.Column("data_quality", sa.JSON(), nullable=False),
      sa.Column("error_message", sa.String(512), nullable=True),
      sa.Column("started_at", sa.DateTime(), nullable=True),
      sa.Column("completed_at", sa.DateTime(), nullable=True),
      sa.Column("created_at", sa.DateTime(), nullable=False),
      sa.Column("updated_at", sa.DateTime(), nullable=False),
      sa.CheckConstraint(
        "status IN ('PENDING','STARTING','RUNNING','COMPLETED','CANCELLED','ERROR')",
        name="ck_limit_up_board_replay_job_status",
      ),
      sa.CheckConstraint(
        "progress_pct >= 0 AND progress_pct <= 100",
        name="ck_limit_up_board_replay_job_progress",
      ),
      comment="账户级打板助手历史回放任务",
    )
    op.create_index(
      "ix_limit_up_board_replay_jobs_account_id",
      "limit_up_board_replay_jobs",
      ["account_id"],
    )
    op.create_index(
      "ix_limit_up_board_replay_account_status",
      "limit_up_board_replay_jobs",
      ["account_id", "status"],
    )

  if "limit_up_board_replay_scenarios" not in tables:
    op.create_table(
      "limit_up_board_replay_scenarios",
      sa.Column("id", sa.String(36), primary_key=True),
      sa.Column(
        "job_id",
        sa.String(36),
        sa.ForeignKey("limit_up_board_replay_jobs.id", ondelete="CASCADE"),
        nullable=False,
      ),
      sa.Column("scenario_id", sa.String(32), nullable=False),
      sa.Column(
        "backtest_id",
        sa.String(36),
        sa.ForeignKey("strategy_backtests.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
      ),
      sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
      sa.Column("progress_pct", sa.Float(), nullable=False, server_default="0"),
      sa.Column("processed_until", sa.DateTime(), nullable=True),
      sa.Column("revision", sa.BigInteger(), nullable=False, server_default="1"),
      sa.Column("error_message", sa.String(512), nullable=True),
      sa.Column("confirmation_delay_ms", sa.Integer(), nullable=False),
      sa.Column("participation_cap_pct", sa.Float(), nullable=False),
      sa.Column("book_depth_participation_pct", sa.Float(), nullable=False),
      sa.Column("created_at", sa.DateTime(), nullable=False),
      sa.Column("updated_at", sa.DateTime(), nullable=False),
      sa.UniqueConstraint(
        "job_id",
        "scenario_id",
        name="uq_limit_up_board_replay_job_scenario",
      ),
      sa.CheckConstraint(
        "confirmation_delay_ms >= 0",
        name="ck_limit_up_board_replay_delay",
      ),
      sa.CheckConstraint(
        "participation_cap_pct > 0 AND participation_cap_pct <= 1",
        name="ck_limit_up_board_replay_participation",
      ),
      sa.CheckConstraint(
        "book_depth_participation_pct > 0 AND book_depth_participation_pct <= 1",
        name="ck_limit_up_board_replay_depth_participation",
      ),
      sa.CheckConstraint(
        "status IN ('PENDING','STARTING','RUNNING','COMPLETED','CANCELLED','ERROR')",
        name="ck_limit_up_board_replay_scenario_status",
      ),
      sa.CheckConstraint(
        "progress_pct >= 0 AND progress_pct <= 100",
        name="ck_limit_up_board_replay_scenario_progress",
      ),
      comment="打板助手回放固定成交情景",
    )
    op.create_index(
      "ix_limit_up_board_replay_scenarios_job_id",
      "limit_up_board_replay_scenarios",
      ["job_id"],
    )


def downgrade() -> None:
  op.drop_index(
    "ix_limit_up_board_replay_scenarios_job_id",
    table_name="limit_up_board_replay_scenarios",
  )
  op.drop_table("limit_up_board_replay_scenarios")
  op.drop_index(
    "ix_limit_up_board_replay_account_status",
    table_name="limit_up_board_replay_jobs",
  )
  op.drop_index(
    "ix_limit_up_board_replay_jobs_account_id",
    table_name="limit_up_board_replay_jobs",
  )
  op.drop_table("limit_up_board_replay_jobs")
  op.drop_index(
    "ix_limit_up_board_universe_date_asof",
    table_name="limit_up_board_universe_snapshots",
  )
  op.drop_index(
    "ix_limit_up_board_universe_snapshots_trade_date",
    table_name="limit_up_board_universe_snapshots",
  )
  op.drop_table("limit_up_board_universe_snapshots")
