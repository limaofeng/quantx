"""Replace the broad GraphQL mutation permission with explicit write scopes."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260816_0020"
down_revision = "20260815_0019"
branch_labels = None
depends_on = None

REPLACEMENT_WRITE_PERMISSIONS = (
  "agent:manage",
  "limit-up:control",
  "liquidation:control",
  "market:write",
  "notification:manage",
  "operations:write",
  "orders:write",
  "portfolio:write",
  "strategy:control",
  "strategy:write",
  "t-trade:control",
  "watchlist:write",
)


def migrate_permissions(values: object) -> list[str]:
  if not isinstance(values, list):
    return []
  permissions = {str(value).strip() for value in values if str(value).strip()}
  if "mutation:write" in permissions:
    permissions.remove("mutation:write")
    permissions.update(REPLACEMENT_WRITE_PERMISSIONS)
  return sorted(permissions)


def upgrade() -> None:
  bind = op.get_bind()
  if "auth_users" not in set(inspect(bind).get_table_names()):
    return

  auth_users = sa.table(
    "auth_users",
    sa.column("id", sa.String(36)),
    sa.column("permissions", sa.JSON()),
  )
  rows = bind.execute(sa.select(auth_users.c.id, auth_users.c.permissions))
  for user_id, current_permissions in rows:
    next_permissions = migrate_permissions(current_permissions)
    if next_permissions != current_permissions:
      bind.execute(
        auth_users.update()
        .where(auth_users.c.id == user_id)
        .values(permissions=next_permissions)
      )


def downgrade() -> None:
  raise RuntimeError("GraphQL write-permission downgrades are intentionally refused")
