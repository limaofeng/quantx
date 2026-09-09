"""Persist completed-delivery proof attempts before any recovery IO."""

import sqlalchemy as sa
from alembic import op

revision = "20260910_0082"
down_revision = "20260910_0081"
branch_labels = None
depends_on = None


def upgrade():
  op.add_column(
    "development_data_download_budget",
    sa.Column("proof_attempts", sa.Integer(), nullable=False, server_default="0"),
  )
  op.create_check_constraint(
    "ck_development_proof_attempts",
    "development_data_download_budget",
    "proof_attempts BETWEEN 0 AND 4",
  )


def downgrade():
  if op.get_bind().scalar(
    sa.text(
      "SELECT EXISTS(SELECT 1 FROM development_data_download_budget WHERE proof_attempts > 0)"
    )
  ):
    raise RuntimeError("cannot remove persisted proof attempts")
  op.drop_constraint(
    "ck_development_proof_attempts", "development_data_download_budget", type_="check"
  )
  op.drop_column("development_data_download_budget", "proof_attempts")
