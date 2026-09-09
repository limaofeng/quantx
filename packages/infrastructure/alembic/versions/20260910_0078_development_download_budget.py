"""Keep delivery download budgets across importer and Worker restarts."""

import sqlalchemy as sa
from alembic import op

revision = "20260910_0078"
down_revision = "20260910_0077"
branch_labels = None
depends_on = None


def upgrade():
  op.create_table(
    "development_data_download_budget",
    sa.Column(
      "delivery_id",
      sa.String(64),
      sa.ForeignKey("development_data_export.id"),
      primary_key=True,
    ),
    sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("reserved_bytes", sa.BigInteger(), nullable=False, server_default="0"),
    sa.Column("reserved_seconds", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("reason_code", sa.String(64)),
    sa.Column(
      "updated_at",
      sa.DateTime(timezone=True),
      nullable=False,
      server_default=sa.func.now(),
    ),
    sa.CheckConstraint(
      "attempts >= 0 AND reserved_bytes >= 0 AND reserved_seconds >= 0",
      name="ck_development_download_budget_nonnegative",
    ),
  )


def downgrade():
  if op.get_bind().scalar(
    sa.text("SELECT EXISTS(SELECT 1 FROM development_data_download_budget)")
  ):
    raise RuntimeError("cannot remove persisted download budgets")
  op.drop_table("development_data_download_budget")
