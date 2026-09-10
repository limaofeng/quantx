"""Keep recovery intervals independently of the volatile Engine archive queue."""

import sqlalchemy as sa
from alembic import op

revision = "20260910_0087"
down_revision = "20260910_0086"
branch_labels = None
depends_on = None


def upgrade():
  if op.get_bind().scalar(
    sa.text("SELECT EXISTS(SELECT 1 FROM realtime_archive_revision)")
  ):
    raise RuntimeError(
      "archive scope migration requires explicit legacy evidence mapping"
    )
  op.create_table(
    "engine_archive_scope",
    sa.Column(
      "generation",
      sa.BigInteger,
      sa.ForeignKey("engine_archive_generation.generation"),
      primary_key=True,
    ),
    sa.Column("instrument", sa.String(20), primary_key=True),
    sa.Column("start_minute", sa.DateTime(timezone=True), nullable=False),
    sa.Column(
      "created_at",
      sa.DateTime(timezone=True),
      nullable=False,
      server_default=sa.func.now(),
    ),
    sa.CheckConstraint("EXTRACT(SECOND FROM start_minute)=0"),
  )
  op.create_foreign_key(
    "fk_archive_revision_recovery_scope",
    "realtime_archive_revision",
    "engine_archive_scope",
    ["generation", "instrument"],
    ["generation", "instrument"],
  )


def downgrade():
  if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM engine_archive_scope)")):
    raise RuntimeError("cannot remove realtime archive evidence")
  op.drop_constraint(
    "fk_archive_revision_recovery_scope",
    "realtime_archive_revision",
    type_="foreignkey",
  )
  op.drop_table("engine_archive_scope")
