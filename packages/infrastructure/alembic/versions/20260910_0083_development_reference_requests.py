"""Durable bounded requests for local reference bootstrap and import."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260910_0083"
down_revision = "20260910_0082"
branch_labels = None
depends_on = None


def upgrade():
  op.create_table(
    "development_reference_request",
    sa.Column("request_id", sa.String(64), primary_key=True),
    sa.Column("request", JSONB, nullable=False),
    sa.Column("state", sa.String(16), nullable=False, server_default="QUEUED"),
    sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("source", JSONB),
    sa.Column("result", JSONB),
    sa.Column("reason", sa.String(128)),
    sa.Column(
      "next_probe_at",
      sa.DateTime(timezone=True),
      nullable=False,
      server_default=sa.func.now(),
    ),
    sa.Column(
      "updated_at",
      sa.DateTime(timezone=True),
      nullable=False,
      server_default=sa.func.now(),
    ),
    sa.CheckConstraint("attempts BETWEEN 0 AND 4"),
    sa.CheckConstraint("state IN ('QUEUED','WAITING','VERIFIED','BLOCKED')"),
    sa.CheckConstraint("(state='VERIFIED') = (result IS NOT NULL)"),
  )
  op.create_index(
    "ix_development_reference_due",
    "development_reference_request",
    ["next_probe_at", "updated_at", "request_id"],
    postgresql_where=sa.text("state IN ('QUEUED','WAITING')"),
  )


def downgrade():
  if op.get_bind().scalar(
    sa.text("SELECT EXISTS(SELECT 1 FROM development_reference_request)")
  ):
    raise RuntimeError("cannot remove persisted reference requests")
  op.drop_table("development_reference_request")
