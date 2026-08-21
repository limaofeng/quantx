"""Persist market-data ingestion audits and compressed transfer sizes."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260821_0025"
down_revision = "20260820_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
  inspector = inspect(op.get_bind())
  request_columns = {
    column["name"] for column in inspector.get_columns("market_data_request")
  }
  transfer_columns = {
    column["name"] for column in inspector.get_columns("market_data_transfer")
  }
  if (
    {"ingestion_result", "processing_claim_token"} & request_columns
    or "compressed_bytes" in transfer_columns
  ):
    raise RuntimeError(
      "market-data ingestion audit schema already exists without this revision"
    )
  op.add_column(
    "market_data_request",
    sa.Column("ingestion_result", sa.JSON(), nullable=True),
  )
  op.add_column(
    "market_data_request",
    sa.Column("processing_claim_token", sa.String(length=36), nullable=True),
  )
  op.add_column(
    "market_data_transfer",
    sa.Column(
      "compressed_bytes",
      sa.BigInteger(),
      nullable=False,
      server_default="0",
    ),
  )
  op.alter_column(
    "market_data_transfer",
    "compressed_bytes",
    server_default=None,
  )


def downgrade() -> None:
  op.drop_column("market_data_transfer", "compressed_bytes")
  op.drop_column("market_data_request", "processing_claim_token")
  op.drop_column("market_data_request", "ingestion_result")
