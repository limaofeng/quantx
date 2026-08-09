"""Consolidated, fingerprint-locked baseline for the pre-Alembic schema.

Existing databases are schema-doctored and stamped at this revision. Empty
databases execute this release baseline before subsequent revisions. The
metadata fingerprint makes accidental baseline drift fail closed: model changes
must be represented by a new Alembic revision.
"""

import hashlib
import json

import quantx_infrastructure.models  # noqa: F401
from alembic import op
from quantx_infrastructure.database.relational_base import Base
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql

revision = "20260729_0001"
down_revision = None
branch_labels = None
depends_on = None

EXPECTED_METADATA_SHA256 = (
  "ce1600fbb992cdd47fd945d7c8c2fe87a045c56c8223ede3e42c98deeaa339a5"
)


def _metadata_payload() -> list[dict[str, object]]:
  dialect = postgresql.dialect()
  payload: list[dict[str, object]] = []
  for table in sorted(Base.metadata.tables.values(), key=lambda value: value.key):
    constraints: list[dict[str, object]] = []
    for constraint in sorted(
      table.constraints,
      key=lambda value: (
        value.__class__.__name__,
        value.name or "",
        ",".join(column.name for column in value.columns),
      ),
    ):
      entry: dict[str, object] = {
        "kind": constraint.__class__.__name__,
        "name": constraint.name,
        "columns": [column.name for column in constraint.columns],
      }
      if isinstance(constraint, CheckConstraint):
        entry["sqltext"] = str(constraint.sqltext)
      if isinstance(constraint, ForeignKeyConstraint):
        entry["targets"] = [
          element.target_fullname for element in constraint.elements
        ]
        entry["ondelete"] = constraint.ondelete
        entry["onupdate"] = constraint.onupdate
      if isinstance(constraint, UniqueConstraint):
        entry["unique"] = True
      constraints.append(entry)
    payload.append(
      {
        "key": table.key,
        "schema": table.schema,
        "columns": [
          {
            "name": column.name,
            "type": column.type.compile(dialect=dialect),
            "nullable": column.nullable,
            "primary_key": column.primary_key,
            "unique": column.unique,
            "autoincrement": column.autoincrement,
            "server_default": (
              str(column.server_default.arg) if column.server_default else None
            ),
          }
          for column in table.columns
        ],
        "constraints": constraints,
        "indexes": [
          {
            "name": index.name,
            "unique": index.unique,
            "expressions": [str(expression) for expression in index.expressions],
          }
          for index in sorted(table.indexes, key=lambda value: value.name or "")
        ],
      }
    )
  return payload


def metadata_sha256() -> str:
  encoded = json.dumps(
    _metadata_payload(),
    ensure_ascii=True,
    separators=(",", ":"),
    sort_keys=True,
  ).encode("utf-8")
  return hashlib.sha256(encoded).hexdigest()


def upgrade() -> None:
  actual = metadata_sha256()
  if actual != EXPECTED_METADATA_SHA256:
    raise RuntimeError(
      "The immutable QuantX baseline metadata changed. "
      "Create a new Alembic revision instead of rewriting revision "
      f"{revision}; expected={EXPECTED_METADATA_SHA256} actual={actual}."
    )
  Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
