"""Persist configurable history download windows."""

import sqlalchemy as sa
from alembic import context, op

revision = "20260909_0063"
down_revision = "20260909_0062"
branch_labels = None
depends_on = None


def upgrade():
  # A prior development build created this table before its revision was ordered.
  # Adopt only the exact schema; never hide an incompatible existing object.
  if not context.is_offline_mode():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    name = "history_download_settings"
    if inspector.has_table(name):
      expected = {
        "id": sa.String(32),
        "version": sa.Integer(),
        "policy": sa.JSON(),
        "updated_by_user_id": sa.String(36),
        "created_at": sa.DateTime(),
        "updated_at": sa.DateTime(),
      }
      columns = inspector.get_columns(name)
      if (
        {column["name"] for column in columns} != set(expected)
        or any(
          column["nullable"]
          or column.get("default") is not None
          or str(column["type"].compile(dialect=bind.dialect))
          != str(expected[column["name"]].compile(dialect=bind.dialect))
          for column in columns
        )
        or inspector.get_pk_constraint(name)["constrained_columns"] != ["id"]
        or inspector.get_foreign_keys(name)
        or inspector.get_unique_constraints(name)
        or inspector.get_check_constraints(name)
        or inspector.get_indexes(name)
      ):
        raise RuntimeError("HISTORY_DOWNLOAD_SETTINGS_EXISTING_SCHEMA_CONFLICT")
      return
  op.create_table(
    "history_download_settings",
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("version", sa.Integer(), nullable=False),
    sa.Column("policy", sa.JSON(), nullable=False),
    sa.Column("updated_by_user_id", sa.String(36), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    sa.Column("updated_at", sa.DateTime(), nullable=False),
    comment="全局历史补采允许时段配置",
  )


def downgrade():
  op.drop_table("history_download_settings")
