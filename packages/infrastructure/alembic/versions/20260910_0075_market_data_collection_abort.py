"""Converge confirmed native failures without counting them as completed units."""

import sqlalchemy as sa
from alembic import op

revision = "20260910_0075"
down_revision = "20260910_0074"
branch_labels = None
depends_on = None


def _constraints(abort):
  inspector = sa.inspect(op.get_bind())
  for table, field in [
    ("market_data_collection_permit", "state"),
    ("market_data_collection_receipt", "event"),
  ]:
    for constraint in inspector.get_check_constraints(table):
      if field in constraint["sqltext"]:
        op.drop_constraint(constraint["name"], table, type_="check")
  states = "'ISSUED','STARTED','FINISHED','EXPIRED'" + (",'ABORTED'" if abort else "")
  op.create_check_constraint(
    "ck_collection_permit_state",
    "market_data_collection_permit",
    f"state IN ({states})",
  )
  timing = "(state IN ('ISSUED','EXPIRED') AND started_at IS NULL AND finished_at IS NULL) OR (state='STARTED' AND started_at IS NOT NULL AND finished_at IS NULL) OR (state='FINISHED' AND started_at IS NOT NULL AND finished_at IS NOT NULL)"
  if abort:
    timing += " OR (state='ABORTED' AND finished_at IS NOT NULL)"
  op.create_check_constraint(
    "ck_collection_permit_times", "market_data_collection_permit", timing
  )
  events = "'START','FINISH'" + (",'ABORT'" if abort else "")
  op.create_check_constraint(
    "ck_collection_receipt_event",
    "market_data_collection_receipt",
    f"event IN ({events})",
  )
  op.drop_index(
    "uq_market_data_collection_unit", table_name="market_data_collection_permit"
  )
  op.create_index(
    "uq_market_data_collection_unit",
    "market_data_collection_permit",
    ["request_id", "unit_index"],
    unique=True,
    postgresql_where=sa.text(
      "state NOT IN ('EXPIRED','ABORTED')" if abort else "state <> 'EXPIRED'"
    ),
  )


def upgrade():
  _constraints(True)
  op.execute(
    "UPDATE market_data_collection_receipt SET payload=payload || jsonb_build_object('abort',NULL)"
  )


def downgrade():
  if (
    op.get_bind()
    .execute(
      sa.text(
        "SELECT EXISTS(SELECT 1 FROM market_data_collection_permit WHERE state='ABORTED') OR EXISTS(SELECT 1 FROM market_data_collection_receipt WHERE event='ABORT')"
      )
    )
    .scalar_one()
  ):
    raise RuntimeError("cannot remove persisted collection abort evidence")
  _constraints(False)
  op.execute("UPDATE market_data_collection_receipt SET payload=payload - 'abort'")
