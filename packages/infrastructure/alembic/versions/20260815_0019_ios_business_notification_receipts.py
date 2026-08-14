"""Add global idempotency receipts for iOS business-event projection."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260815_0019"
down_revision = "20260815_0018"
branch_labels = None
depends_on = None

TABLE_NAME = "ios_business_notification_receipts"
ACCOUNT_INDEX = "ix_ios_business_notification_receipt_account_projected"
CATEGORY_CHECK = "ck_ios_business_notification_receipt_category"
COUNT_CHECK = "ck_ios_business_notification_receipt_queued_count"
SOURCE_UNIQUE = "uq_ios_business_notification_receipt_source"


def _validate_existing_schema(inspector) -> None:
  columns = {
    str(column.get("name") or ""): column
    for column in inspector.get_columns(TABLE_NAME)
  }
  expected_nullability = {
    "source_event_key_hash": False,
    "source_kind": False,
    "source_event_id": False,
    "account_id": False,
    "category": False,
    "occurred_at": False,
    "expires_at": False,
    "projected_at": False,
    "queued_event_count": False,
  }
  expected_strings = {
    "source_event_key_hash": 64,
    "source_kind": 48,
    "source_event_id": 128,
    "account_id": 50,
    "category": 32,
  }
  problems: list[str] = []
  missing = sorted(set(expected_nullability) - set(columns))
  if missing:
    problems.append("missing=" + ",".join(missing))
  wrong_nullability = sorted(
    name
    for name, nullable in expected_nullability.items()
    if name in columns and bool(columns[name].get("nullable", True)) != nullable
  )
  if wrong_nullability:
    problems.append("nullability=" + ",".join(wrong_nullability))
  for name, length in expected_strings.items():
    actual = columns.get(name, {}).get("type")
    if not isinstance(actual, sa.String) or getattr(actual, "length", None) != length:
      problems.append(f"type={name}")
  for name in ("occurred_at", "expires_at", "projected_at"):
    if not isinstance(columns.get(name, {}).get("type"), sa.DateTime):
      problems.append(f"type={name}")
  if not isinstance(columns.get("queued_event_count", {}).get("type"), sa.Integer):
    problems.append("type=queued_event_count")

  primary_key = tuple(
    str(value)
    for value in inspector.get_pk_constraint(TABLE_NAME).get(
      "constrained_columns", []
    )
  )
  if primary_key != ("source_event_key_hash",):
    problems.append("primary-key")

  unique_constraints = {
    tuple(str(value) for value in item.get("column_names", []))
    for item in inspector.get_unique_constraints(TABLE_NAME)
  }
  if ("source_kind", "source_event_id") not in unique_constraints:
    problems.append(f"unique={SOURCE_UNIQUE}")

  checks = {
    str(item.get("name") or ""): "".join(
      str(item.get("sqltext") or "").lower().split()
    )
    for item in inspector.get_check_constraints(TABLE_NAME)
  }
  if "connection_data" not in checks.get(CATEGORY_CHECK, ""):
    problems.append(f"check={CATEGORY_CHECK}")
  if "queued_event_count>=0" not in checks.get(COUNT_CHECK, ""):
    problems.append(f"check={COUNT_CHECK}")

  indexes = {
    str(item.get("name") or ""): item
    for item in inspector.get_indexes(TABLE_NAME)
  }
  account_index = indexes.get(ACCOUNT_INDEX)
  if account_index is None or tuple(
    str(value) for value in account_index.get("column_names", [])
  ) != ("account_id", "projected_at"):
    problems.append(f"index={ACCOUNT_INDEX}")

  if problems:
    raise RuntimeError(
      "Invalid existing iOS business notification receipt schema: "
      + "; ".join(problems)
    )


def upgrade() -> None:
  inspector = inspect(op.get_bind())
  if TABLE_NAME in set(inspector.get_table_names()):
    _validate_existing_schema(inspector)
    return

  op.create_table(
    TABLE_NAME,
    sa.Column("source_event_key_hash", sa.String(length=64), primary_key=True),
    sa.Column("source_kind", sa.String(length=48), nullable=False),
    sa.Column("source_event_id", sa.String(length=128), nullable=False),
    sa.Column("account_id", sa.String(length=50), nullable=False),
    sa.Column("category", sa.String(length=32), nullable=False),
    sa.Column("occurred_at", sa.DateTime(), nullable=False),
    sa.Column("expires_at", sa.DateTime(), nullable=False),
    sa.Column("projected_at", sa.DateTime(), nullable=False),
    sa.Column(
      "queued_event_count",
      sa.Integer(),
      nullable=False,
      server_default="0",
    ),
    sa.CheckConstraint(
      "category IN ('ACTION_REQUIRED', 'ORDER_UPDATE', 'RISK_SAFETY', "
      "'AUTOMATION_ERROR', 'CONNECTION_DATA')",
      name=CATEGORY_CHECK,
    ),
    sa.CheckConstraint(
      "queued_event_count >= 0",
      name=COUNT_CHECK,
    ),
    sa.UniqueConstraint(
      "source_kind",
      "source_event_id",
      name=SOURCE_UNIQUE,
    ),
    comment="iOS 业务通知全局幂等投影回执",
  )
  op.create_index(
    ACCOUNT_INDEX,
    TABLE_NAME,
    ["account_id", "projected_at"],
  )


def downgrade() -> None:
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
