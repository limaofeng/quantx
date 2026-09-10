"""Bounded realtime revision admission and Worker-owned publication receipts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260910_0085"
down_revision = "20260910_0084"
branch_labels = None
depends_on = None


def upgrade():
  op.create_table(
    "realtime_archive_stream",
    sa.Column(
      "generation",
      sa.BigInteger,
      sa.ForeignKey("engine_archive_generation.generation"),
      primary_key=True,
    ),
    sa.Column("continuity_generation", sa.BigInteger, primary_key=True),
    sa.Column("stream_id", sa.String(36), nullable=False),
    sa.CheckConstraint("continuity_generation > 0"),
  )
  op.create_table(
    "realtime_archive_revision",
    sa.Column("request_id", sa.String(64), primary_key=True),
    sa.Column("request", JSONB, nullable=False),
    sa.Column("instrument", sa.String(20), nullable=False),
    sa.Column("minute", sa.DateTime(timezone=True), nullable=False),
    sa.Column("generation", sa.BigInteger, nullable=False),
    sa.Column("continuity_generation", sa.BigInteger, nullable=False),
    sa.Column("sequence", sa.BigInteger, nullable=False),
    sa.Column("sealed", sa.Boolean, nullable=False),
    sa.Column("phase", sa.String(16), nullable=False, server_default="WRITE"),
    sa.Column("write_attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("read_attempts", sa.Integer, nullable=False, server_default="0"),
    sa.Column("reason", sa.String(128)),
    sa.Column("proof", JSONB),
    sa.Column(
      "next_retry_at",
      sa.DateTime(timezone=True),
      nullable=False,
      server_default=sa.func.now(),
    ),
    sa.Column(
      "created_at",
      sa.DateTime(timezone=True),
      nullable=False,
      server_default=sa.func.now(),
    ),
    sa.ForeignKeyConstraint(
      ["generation", "continuity_generation"],
      [
        "realtime_archive_stream.generation",
        "realtime_archive_stream.continuity_generation",
      ],
    ),
    sa.CheckConstraint("phase IN ('WRITE','READBACK','VERIFIED','BLOCKED')"),
    sa.CheckConstraint(
      "write_attempts BETWEEN 0 AND 4 AND read_attempts BETWEEN 0 AND 4"
    ),
    sa.CheckConstraint("(phase='VERIFIED') = (proof IS NOT NULL)"),
    sa.CheckConstraint("sequence > 0 AND octet_length(request::text) <= 4096"),
  )
  op.create_index(
    "ix_realtime_archive_due",
    "realtime_archive_revision",
    ["next_retry_at", "created_at", "request_id"],
    postgresql_where=sa.text("phase IN ('WRITE','READBACK')"),
  )
  op.create_index(
    "ix_realtime_archive_minute",
    "realtime_archive_revision",
    [
      "instrument",
      "minute",
      "generation",
      "continuity_generation",
      "sequence",
      "sealed",
    ],
  )


def downgrade():
  if op.get_bind().scalar(
    sa.text("SELECT EXISTS(SELECT 1 FROM realtime_archive_stream)")
  ):
    raise RuntimeError("cannot remove realtime archive evidence")
  op.drop_table("realtime_archive_revision")
  op.drop_table("realtime_archive_stream")
