"""Add exact, expiring authorization envelopes to live exit plans."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260815_0018"
down_revision = "20260815_0017"
branch_labels = None
depends_on = None

TABLE_NAME = "auto_exit_plans"
CONSTRAINT_NAME = "ck_auto_exit_plan_exact_authorization"
AUTHORIZATION_COLUMNS: dict[str, sa.types.TypeEngine] = {
  "auto_exit_authorization_fingerprint": sa.String(length=64),
  "auto_exit_authorization_config_version": sa.Integer(),
  "auto_exit_authorized_at": sa.DateTime(),
  "auto_exit_authorization_expires_at": sa.DateTime(),
  "auto_exit_authorization_challenge_id": sa.String(length=36),
  "auto_exit_authorization_user_id": sa.String(length=36),
  "auto_exit_authorization_device_session_id": sa.String(length=36),
}
CONSTRAINT_SQL = (
  "(auto_exit_authorized = false AND "
  "auto_exit_authorization_fingerprint IS NULL AND "
  "auto_exit_authorization_config_version IS NULL AND "
  "auto_exit_authorized_at IS NULL AND "
  "auto_exit_authorization_expires_at IS NULL AND "
  "auto_exit_authorization_challenge_id IS NULL AND "
  "auto_exit_authorization_user_id IS NULL AND "
  "auto_exit_authorization_device_session_id IS NULL) OR "
  "(auto_exit_authorized = true AND "
  "auto_exit_authorization_fingerprint IS NOT NULL AND "
  "auto_exit_authorization_config_version IS NOT NULL AND "
  "auto_exit_authorized_at IS NOT NULL AND "
  "auto_exit_authorization_expires_at IS NOT NULL AND "
  "auto_exit_authorization_challenge_id IS NOT NULL AND "
  "auto_exit_authorization_user_id IS NOT NULL AND "
  "auto_exit_authorization_device_session_id IS NOT NULL)"
)


def _validate_existing_schema(inspector) -> None:
  columns = {
    str(column.get("name") or ""): column
    for column in inspector.get_columns(TABLE_NAME)
  }
  problems: list[str] = []
  for name, expected_type in AUTHORIZATION_COLUMNS.items():
    column = columns.get(name)
    if column is None:
      problems.append(f"missing={name}")
      continue
    actual_type = column.get("type")
    if isinstance(expected_type, sa.String):
      if not isinstance(actual_type, sa.String):
        problems.append(f"type={name}")
      elif getattr(actual_type, "length", None) != getattr(
        expected_type, "length", None
      ):
        problems.append(f"length={name}")
    elif isinstance(expected_type, sa.Integer):
      if not isinstance(actual_type, sa.Integer):
        problems.append(f"type={name}")
    elif isinstance(expected_type, sa.DateTime) and not isinstance(
      actual_type, sa.DateTime
    ):
      problems.append(f"type={name}")
    if not bool(column.get("nullable", True)):
      problems.append(f"nullable={name}")
  checks = {
    str(item.get("name") or ""): "".join(
      str(item.get("sqltext") or "").lower().split()
    )
    for item in inspector.get_check_constraints(TABLE_NAME)
  }
  check_sql = checks.get(CONSTRAINT_NAME, "")
  if not check_sql or not all(
    value in check_sql
    for value in (
      "auto_exit_authorized=false",
      "auto_exit_authorization_fingerprintisnotnull",
      "auto_exit_authorization_device_session_idisnotnull",
    )
  ):
    problems.append("exact-authorization-check")
  if problems:
    raise RuntimeError(
      "Partial auto_exit_plans authorization schema detected: "
      + "; ".join(problems)
    )


def _invalidate_legacy_boolean_authorizations() -> None:
  assignments = ["auto_exit_authorized = false"]
  assignments.extend(f"{name} = NULL" for name in AUTHORIZATION_COLUMNS)
  op.execute(sa.text(f"UPDATE {TABLE_NAME} SET {', '.join(assignments)}"))
  op.execute(
    sa.text(
      "UPDATE conditional_liquidation_orders "
      "SET auto_exit_authorized = false "
      "WHERE auto_exit_authorized = true"
    )
  )


def upgrade() -> None:
  inspector = inspect(op.get_bind())
  if TABLE_NAME not in set(inspector.get_table_names()):
    raise RuntimeError("auto_exit_plans must exist before exact authorization")
  existing = {
    str(column.get("name") or "")
    for column in inspector.get_columns(TABLE_NAME)
  }
  present = set(AUTHORIZATION_COLUMNS) & existing
  if present and present != set(AUTHORIZATION_COLUMNS):
    raise RuntimeError(
      "Partial auto_exit_plans authorization schema detected: columns="
      + ",".join(sorted(present))
    )
  if not present:
    for name, column_type in AUTHORIZATION_COLUMNS.items():
      op.add_column(
        TABLE_NAME,
        sa.Column(name, column_type, nullable=True),
      )
    _invalidate_legacy_boolean_authorizations()
    op.create_check_constraint(CONSTRAINT_NAME, TABLE_NAME, CONSTRAINT_SQL)
    return
  _validate_existing_schema(inspector)
  _invalidate_legacy_boolean_authorizations()


def downgrade() -> None:
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
