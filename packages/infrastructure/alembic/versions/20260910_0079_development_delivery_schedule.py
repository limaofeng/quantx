"""Retain remote submission acknowledgement and delivery probe deadlines."""

import sqlalchemy as sa
from alembic import op

revision = "20260910_0079"
down_revision = "20260910_0078"
branch_labels = None
depends_on = None


def upgrade():
  op.add_column(
    "development_data_download_budget",
    sa.Column(
      "remote_submitted",
      sa.Boolean(),
      nullable=False,
      server_default=sa.false(),
    ),
  )
  op.add_column(
    "development_data_download_budget",
    sa.Column(
      "next_probe_at",
      sa.DateTime(timezone=True),
      nullable=False,
      server_default=sa.func.now(),
    ),
  )
  op.add_column(
    "development_data_download_budget",
    sa.Column(
      "transient_failures",
      sa.Integer(),
      nullable=False,
      server_default="0",
    ),
  )
  op.add_column(
    "development_data_download_budget", sa.Column("wait_reason", sa.String(64))
  )
  op.create_check_constraint(
    "ck_development_delivery_failures",
    "development_data_download_budget",
    "transient_failures BETWEEN 0 AND 6",
  )
  op.create_index(
    "ix_development_delivery_probe",
    "development_data_download_budget",
    ["next_probe_at"],
  )


def downgrade():
  if op.get_bind().scalar(
    sa.text("""
    SELECT EXISTS(SELECT 1 FROM development_data_download_budget
      WHERE remote_submitted OR wait_reason IS NOT NULL OR transient_failures > 0)
  """)
  ):
    raise RuntimeError("cannot remove persisted delivery schedule")
  op.drop_index(
    "ix_development_delivery_probe", table_name="development_data_download_budget"
  )
  op.drop_constraint(
    "ck_development_delivery_failures",
    "development_data_download_budget",
    type_="check",
  )
  for name in (
    "wait_reason",
    "transient_failures",
    "next_probe_at",
    "remote_submitted",
  ):
    op.drop_column("development_data_download_budget", name)
