"""Persist native session scope and revoke indistinguishable legacy sessions.

The revocation is a fail-closed rollout step: pre-0016 native and Web sessions
cannot be distinguished reliably. Rows and audit history remain intact; only a
previously NULL ``revoked_at`` is populated, so reruns preserve prior times.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260815_0016"
down_revision = "20260815_0015"
branch_labels = None
depends_on = None

_TABLE_NAME = "auth_device_sessions"
_ACCOUNT_COLUMN = "active_account_id"
_PERMISSIONS_COLUMN = "granted_permissions"
_SCOPE_PAIR_CONSTRAINT = "ck_auth_device_sessions_scope_pair"
_SCOPE_PAIR_SQL = "(active_account_id IS NULL) = (granted_permissions IS NULL)"


def _validate_existing_schema(inspector) -> None:
  columns = {
    str(column.get("name") or ""): column
    for column in inspector.get_columns(_TABLE_NAME)
  }
  scope_columns = {_ACCOUNT_COLUMN, _PERMISSIONS_COLUMN}
  present = scope_columns & set(columns)
  if present != scope_columns:
    raise RuntimeError(
      "Partial auth_device_sessions scope schema detected: present columns="
      + ",".join(sorted(present))
    )

  account_column = columns[_ACCOUNT_COLUMN]
  permissions_column = columns[_PERMISSIONS_COLUMN]
  account_type = account_column.get("type")
  permissions_type = permissions_column.get("type")
  invalid_details: list[str] = []
  if (
    not isinstance(account_type, sa.String)
    or getattr(account_type, "length", None) != 50
  ):
    invalid_details.append("active_account_id must be VARCHAR(50)")
  if not isinstance(permissions_type, sa.JSON):
    invalid_details.append("granted_permissions must be JSON")
  if not bool(account_column.get("nullable", False)):
    invalid_details.append("active_account_id must be nullable")
  if not bool(permissions_column.get("nullable", False)):
    invalid_details.append("granted_permissions must be nullable")

  constraints = {
    str(constraint.get("name") or ""): str(constraint.get("sqltext") or "")
    for constraint in inspector.get_check_constraints(_TABLE_NAME)
  }
  scope_constraint = "".join(
    character
    for character in constraints.get(_SCOPE_PAIR_CONSTRAINT, "").lower()
    if not character.isspace() and character not in "()"
  )
  expected_constraint = "active_account_idisnull=granted_permissionsisnull"
  if scope_constraint != expected_constraint:
    invalid_details.append("missing or invalid scope-pair check constraint")
  if invalid_details:
    raise RuntimeError(
      "Invalid auth_device_sessions scope schema detected: "
      + "; ".join(invalid_details)
    )


def _revoke_unscoped_active_sessions() -> None:
  sessions = sa.table(
    _TABLE_NAME,
    sa.column("revoked_at", sa.DateTime()),
    sa.column(_ACCOUNT_COLUMN, sa.String(length=50)),
    sa.column(_PERMISSIONS_COLUMN, sa.JSON()),
  )
  op.execute(
    sa.update(sessions)
    .where(
      sessions.c.revoked_at.is_(None),
      sessions.c.active_account_id.is_(None),
      sessions.c.granted_permissions.is_(None),
    )
    .values(revoked_at=sa.func.now())
  )


def upgrade() -> None:
  inspector = inspect(op.get_bind())
  if _TABLE_NAME not in set(inspector.get_table_names()):
    raise RuntimeError("auth_device_sessions must exist before revision 0016")

  columns = {column["name"] for column in inspector.get_columns(_TABLE_NAME)}
  existing_scope_columns = columns & {_ACCOUNT_COLUMN, _PERMISSIONS_COLUMN}
  if existing_scope_columns:
    _validate_existing_schema(inspector)
    _revoke_unscoped_active_sessions()
    return

  op.add_column(
    _TABLE_NAME,
    sa.Column(_ACCOUNT_COLUMN, sa.String(length=50), nullable=True),
  )
  op.add_column(
    _TABLE_NAME,
    sa.Column(
      _PERMISSIONS_COLUMN,
      sa.JSON(none_as_null=True),
      nullable=True,
    ),
  )
  op.create_check_constraint(
    _SCOPE_PAIR_CONSTRAINT,
    _TABLE_NAME,
    _SCOPE_PAIR_SQL,
  )
  _revoke_unscoped_active_sessions()


def downgrade() -> None:
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
