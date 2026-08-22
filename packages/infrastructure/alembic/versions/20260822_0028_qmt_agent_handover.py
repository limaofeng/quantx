"""Persist safe single-device QMT Agent handovers."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260822_0028"
down_revision = "20260821_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.add_column(
    "agent_devices",
    sa.Column("replaces_device_id", sa.String(length=36), nullable=True),
  )
  op.create_foreign_key(
    "fk_agent_devices_replaces_device",
    "agent_devices",
    "agent_devices",
    ["replaces_device_id"],
    ["id"],
    ondelete="RESTRICT",
  )
  op.create_index(
    "ix_agent_devices_replaces_device_id",
    "agent_devices",
    ["replaces_device_id"],
  )
  op.add_column(
    "agent_enrollment_codes",
    sa.Column("replaces_device_id", sa.String(length=36), nullable=True),
  )
  op.create_foreign_key(
    "fk_agent_enrollment_codes_replaces_device",
    "agent_enrollment_codes",
    "agent_devices",
    ["replaces_device_id"],
    ["id"],
    ondelete="RESTRICT",
  )


def downgrade() -> None:
  op.drop_constraint(
    "fk_agent_enrollment_codes_replaces_device",
    "agent_enrollment_codes",
    type_="foreignkey",
  )
  op.drop_column("agent_enrollment_codes", "replaces_device_id")
  op.drop_index(
    "ix_agent_devices_replaces_device_id",
    table_name="agent_devices",
  )
  op.drop_constraint(
    "fk_agent_devices_replaces_device",
    "agent_devices",
    type_="foreignkey",
  )
  op.drop_column("agent_devices", "replaces_device_id")
