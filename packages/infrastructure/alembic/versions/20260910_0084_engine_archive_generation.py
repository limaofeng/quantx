"""Persistent monotonic generations bound to the Engine singleton session."""

import sqlalchemy as sa
from alembic import op

revision = "20260910_0084"
down_revision = "20260910_0083"
branch_labels = None
depends_on = None


def upgrade():
  op.create_table(
    "engine_archive_generation",
    sa.Column("generation", sa.BigInteger, primary_key=True, autoincrement=True),
    sa.Column("registration_id", sa.String(36), nullable=False, unique=True),
    sa.Column("backend_pid", sa.Integer, nullable=False),
    sa.Column("backend_start", sa.DateTime(timezone=True), nullable=False),
    sa.Column(
      "registered_at",
      sa.DateTime(timezone=True),
      nullable=False,
      server_default=sa.func.now(),
    ),
    sa.CheckConstraint("generation > 0"),
  )


def downgrade():
  if op.get_bind().scalar(
    sa.text("SELECT EXISTS(SELECT 1 FROM engine_archive_generation)")
  ):
    raise RuntimeError("cannot remove persisted Engine archive generations")
  op.drop_table("engine_archive_generation")
