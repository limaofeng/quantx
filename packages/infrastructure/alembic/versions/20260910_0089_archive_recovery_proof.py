"""Persist original-demand archive recovery proofs and bounded probe scheduling."""

import sqlalchemy as sa
from alembic import op

revision = "20260910_0089"
down_revision = "20260910_0088"
branch_labels = None
depends_on = None


def _replace_checks(verified):
  # 0088 used unnamed constraints; discover their actual names in the active
  # schema instead of assuming PostgreSQL's generated suffixes.
  op.execute("""
    DO $$ DECLARE item record; BEGIN
      FOR item IN SELECT conname FROM pg_constraint
        WHERE conrelid='engine_archive_recovery'::regclass AND contype='c'
          AND pg_get_constraintdef(oid) LIKE '%state%'
      LOOP EXECUTE format('ALTER TABLE engine_archive_recovery DROP CONSTRAINT %I',item.conname);
      END LOOP;
    END $$;
  """)
  states = "'WAITING','NO_SESSION','VERIFIED'" if verified else "'WAITING','NO_SESSION'"
  op.execute(
    f"ALTER TABLE engine_archive_recovery ADD CONSTRAINT ck_archive_recovery_state CHECK(state IN ({states}))"
  )
  rule = "(state='WAITING' AND demand_id IS NOT NULL AND evidence IS NULL) OR (state='NO_SESSION' AND demand_id IS NULL AND evidence IS NOT NULL)"
  if verified:
    rule += " OR (state='VERIFIED' AND demand_id IS NOT NULL AND evidence IS NOT NULL AND verified_at IS NOT NULL)"
  op.execute(
    f"ALTER TABLE engine_archive_recovery ADD CONSTRAINT ck_archive_recovery_evidence CHECK({rule})"
  )


def upgrade():
  op.add_column(
    "engine_archive_recovery",
    sa.Column(
      "next_probe_at",
      sa.DateTime(timezone=True),
      nullable=False,
      server_default=sa.func.now(),
    ),
  )
  op.add_column(
    "engine_archive_recovery", sa.Column("verified_at", sa.DateTime(timezone=True))
  )
  op.add_column("engine_archive_recovery", sa.Column("reason", sa.String(64)))
  _replace_checks(True)
  op.create_index(
    "ix_archive_recovery_due",
    "engine_archive_recovery",
    ["next_probe_at"],
    postgresql_where=sa.text("state='WAITING'"),
  )


def downgrade():
  if op.get_bind().scalar(
    sa.text("SELECT EXISTS(SELECT 1 FROM engine_archive_recovery)")
  ):
    raise RuntimeError("cannot remove archive recovery proof or probe evidence")
  _replace_checks(False)
  op.drop_index("ix_archive_recovery_due", table_name="engine_archive_recovery")
  for column in ("reason", "verified_at", "next_probe_at"):
    op.drop_column("engine_archive_recovery", column)
