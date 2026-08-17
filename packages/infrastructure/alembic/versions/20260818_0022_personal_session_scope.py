"""Merge the retired revision and remove redundant session account state."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260818_0022"
down_revision = ("20260816_0021", "20260816_0015")
branch_labels = None
depends_on = None

_TABLE_NAME = "auth_device_sessions"
_ACCOUNT_COLUMN = "active_account_id"
_PERMISSIONS_COLUMN = "granted_permissions"
_SCOPE_PAIR_CONSTRAINT = "ck_auth_device_sessions_scope_pair"


def upgrade() -> None:
  bind = op.get_bind()
  inspector = inspect(bind)
  if _TABLE_NAME not in set(inspector.get_table_names()):
    raise RuntimeError("auth_device_sessions must exist before revision 0022")

  columns = {str(column["name"]) for column in inspector.get_columns(_TABLE_NAME)}
  if _PERMISSIONS_COLUMN not in columns:
    raise RuntimeError("auth_device_sessions.granted_permissions must exist")

  # The token/session authorization contract changes at this revision. Revoke
  # every existing session so no token minted with the retired account claim is
  # accepted under the new personal-account semantics.
  sessions = sa.table(
    _TABLE_NAME,
    sa.column("revoked_at", sa.DateTime()),
  )
  op.execute(
    sa.update(sessions)
    .where(sessions.c.revoked_at.is_(None))
    .values(revoked_at=sa.func.now())
  )

  if _ACCOUNT_COLUMN not in columns:
    return
  constraints = {
    str(constraint.get("name") or "")
    for constraint in inspector.get_check_constraints(_TABLE_NAME)
  }
  if _SCOPE_PAIR_CONSTRAINT in constraints:
    op.drop_constraint(
      _SCOPE_PAIR_CONSTRAINT,
      _TABLE_NAME,
      type_="check",
    )
  op.drop_column(_TABLE_NAME, _ACCOUNT_COLUMN)


def downgrade() -> None:
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
