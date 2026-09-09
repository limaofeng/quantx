"""Keep ingestion checkpoints and retry budgets across process restarts."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260909_0064"
down_revision = "20260909_0063"
branch_labels = None
depends_on = None


def upgrade():
  op.add_column(
    "market_data_request",
    sa.Column(
      "ingestion_progress",
      postgresql.JSONB(),
      nullable=True,
      comment="Frozen manifest, phase checkpoints and persisted retry budget",
    ),
  )


def downgrade():
  op.drop_column("market_data_request", "ingestion_progress")
