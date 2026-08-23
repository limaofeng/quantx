"""Replace the legacy watchlist group_name with account-scoped groups."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect as sa_inspect

revision = "20260823_0031"
down_revision = "20260823_0030"
branch_labels = None
depends_on = None


_GROUP_TABLE = "watchlist_groups"
_MEMBERSHIP_TABLE = "watchlist_group_memberships"
_ITEM_TABLE = "watchlist_items"
_TARGET_TABLES = frozenset({_GROUP_TABLE, _MEMBERSHIP_TABLE})


def _expected_columns(table_name: str) -> dict[str, tuple[sa.types.TypeEngine, bool]]:
  timestamp_columns = {
    "created_at": (sa.DateTime(), False),
    "updated_at": (sa.DateTime(), False),
  }
  if table_name == _GROUP_TABLE:
    return {
      "id": (sa.String(length=32), False),
      "account_id": (sa.String(length=50), False),
      "name": (sa.String(length=80), False),
      "display_order": (sa.Integer(), False),
      **timestamp_columns,
    }
  if table_name == _MEMBERSHIP_TABLE:
    return {
      "group_id": (sa.String(length=32), False),
      "watchlist_item_id": (sa.String(length=32), False),
      "display_order": (sa.Integer(), False),
      **timestamp_columns,
    }
  raise RuntimeError(f"Unknown watchlist migration table: {table_name}")


def _get_inspector():
  return sa_inspect(op.get_bind())


def _type_matches(actual, expected) -> bool:
  actual_affinity = getattr(actual, "_type_affinity", type(actual))
  expected_affinity = getattr(expected, "_type_affinity", type(expected))
  if actual_affinity != expected_affinity:
    return False
  for attribute in ("length", "precision", "scale"):
    expected_value = getattr(expected, attribute, None)
    if (
      expected_value is not None and getattr(actual, attribute, None) != expected_value
    ):
      return False
  return True


def _fail(detail: str) -> None:
  raise RuntimeError("Mismatched watchlist group schema: " + detail)


def _validate_columns(inspector, table_name: str) -> None:
  expected = _expected_columns(table_name)
  actual = {str(column["name"]): column for column in inspector.get_columns(table_name)}
  if set(actual) != set(expected):
    _fail(
      f"{table_name} columns expected={','.join(sorted(expected))} "
      f"actual={','.join(sorted(actual))}"
    )
  for name, (expected_type, expected_nullable) in expected.items():
    column = actual[name]
    if bool(column.get("nullable", True)) != expected_nullable:
      _fail(f"{table_name}.{name} nullable mismatch")
    if not _type_matches(column.get("type"), expected_type):
      _fail(f"{table_name}.{name} type mismatch")


def _validate_group_table(inspector) -> None:
  _validate_columns(inspector, _GROUP_TABLE)
  primary_key = inspector.get_pk_constraint(_GROUP_TABLE)
  if tuple(primary_key.get("constrained_columns") or ()) != ("id",):
    _fail("watchlist_groups primary key mismatch")
  indexes = {
    str(index.get("name")): index
    for index in inspector.get_indexes(_GROUP_TABLE)
    if index.get("name")
  }
  order_index = indexes.get("ix_watchlist_group_account_order")
  if (
    order_index is None
    or bool(order_index.get("unique"))
    or tuple(order_index.get("column_names") or ()) != ("account_id", "display_order")
  ):
    _fail("watchlist group account-order index mismatch")
  functional = indexes.get("uq_watchlist_group_account_name_ci")
  if functional is None or not bool(functional.get("unique")):
    _fail("watchlist group functional unique index is missing or non-unique")
  if tuple(functional.get("column_names") or ()) != ("account_id", None):
    _fail("watchlist group functional unique index columns mismatch")
  expressions = tuple(functional.get("expressions") or ())
  if (
    len(expressions) != 2
    or str(expressions[0]).strip().lower() != "account_id"
    or "lower" not in str(expressions[1]).lower()
    or "name" not in str(expressions[1]).lower()
  ):
    _fail("watchlist group functional unique index expression mismatch")


def _validate_membership_table(inspector) -> None:
  _validate_columns(inspector, _MEMBERSHIP_TABLE)
  primary_key = inspector.get_pk_constraint(_MEMBERSHIP_TABLE)
  if tuple(primary_key.get("constrained_columns") or ()) != (
    "group_id",
    "watchlist_item_id",
  ):
    _fail("watchlist group membership primary key mismatch")
  expected_foreign_keys = {
    ("group_id", _GROUP_TABLE, "id", "CASCADE"),
    ("watchlist_item_id", _ITEM_TABLE, "id", "CASCADE"),
  }
  actual_foreign_keys = {
    (
      tuple(foreign_key.get("constrained_columns") or ()),
      str(foreign_key.get("referred_table")),
      tuple(foreign_key.get("referred_columns") or ()),
      str((foreign_key.get("options") or {}).get("ondelete") or "").upper(),
    )
    for foreign_key in inspector.get_foreign_keys(_MEMBERSHIP_TABLE)
  }
  normalized_expected = {
    ((column,), table, (referred_column,), ondelete)
    for column, table, referred_column, ondelete in expected_foreign_keys
  }
  if not normalized_expected <= actual_foreign_keys:
    _fail("watchlist group membership foreign keys mismatch")
  indexes = {
    str(index.get("name")): index
    for index in inspector.get_indexes(_MEMBERSHIP_TABLE)
    if index.get("name")
  }
  expected_indexes = {
    "ix_watchlist_group_membership_group_order": ("group_id", "display_order"),
    "ix_watchlist_group_membership_item": ("watchlist_item_id",),
  }
  for name, columns in expected_indexes.items():
    index = indexes.get(name)
    if (
      index is None
      or bool(index.get("unique"))
      or tuple(index.get("column_names") or ()) != columns
    ):
      _fail(f"watchlist group membership index {name} mismatch")


def _legacy_group_name_present(inspector) -> bool:
  if _ITEM_TABLE not in set(inspector.get_table_names()):
    _fail(f"missing required table {_ITEM_TABLE}")
  columns = {
    str(column["name"]): column for column in inspector.get_columns(_ITEM_TABLE)
  }
  column = columns.get("group_name")
  if column is None:
    return False
  if bool(column.get("nullable", True)) is not True:
    _fail("watchlist_items.group_name must remain nullable during adoption")
  if not _type_matches(column.get("type"), sa.String(length=80)):
    _fail("watchlist_items.group_name type mismatch")
  return True


def _create_group_tables() -> None:
  op.create_table(
    "watchlist_groups",
    sa.Column("id", sa.String(length=32), nullable=False),
    sa.Column("account_id", sa.String(length=50), nullable=False),
    sa.Column("name", sa.String(length=80), nullable=False),
    sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
    sa.Column(
      "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
      "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
    ),
    sa.PrimaryKeyConstraint("id"),
    comment="账户自选分组",
  )
  op.create_index(
    "ix_watchlist_group_account_order",
    "watchlist_groups",
    ["account_id", "display_order"],
  )
  # PostgreSQL's lower() functional index gives the database the same
  # case-insensitive uniqueness guarantee as the service-level validation.
  op.execute(
    "CREATE UNIQUE INDEX uq_watchlist_group_account_name_ci "
    "ON watchlist_groups (account_id, lower(name))"
  )

  op.create_table(
    "watchlist_group_memberships",
    sa.Column("group_id", sa.String(length=32), nullable=False),
    sa.Column("watchlist_item_id", sa.String(length=32), nullable=False),
    sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
    sa.Column(
      "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
      "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
    ),
    sa.ForeignKeyConstraint(["group_id"], ["watchlist_groups.id"], ondelete="CASCADE"),
    sa.ForeignKeyConstraint(
      ["watchlist_item_id"], ["watchlist_items.id"], ondelete="CASCADE"
    ),
    sa.PrimaryKeyConstraint(
      "group_id", "watchlist_item_id", name="pk_watchlist_group_membership"
    ),
    comment="账户自选分组成员关系",
  )
  op.create_index(
    "ix_watchlist_group_membership_group_order",
    "watchlist_group_memberships",
    ["group_id", "display_order"],
  )
  op.create_index(
    "ix_watchlist_group_membership_item",
    "watchlist_group_memberships",
    ["watchlist_item_id"],
  )


def _backfill_legacy_groups() -> None:
  # Backfill each legacy non-empty group once per account.  The group ID is
  # deterministic for the migration so the membership backfill can join it
  # without relying on a database-specific UUID function.  ON CONFLICT keeps
  # a retry after an interrupted adoption idempotent; conflicting values still
  # fail through the functional unique index or foreign-key constraints.
  op.execute(
    """
    WITH grouped AS (
      SELECT
        account_id,
        lower(btrim(group_name)) AS normalized_name,
        min(btrim(group_name)) AS display_name,
        min(display_order) AS first_order
      FROM watchlist_items
      WHERE group_name IS NOT NULL AND btrim(group_name) <> ''
      GROUP BY account_id, lower(btrim(group_name))
    ), numbered AS (
      SELECT
        account_id,
        normalized_name,
        display_name,
        row_number() OVER (
          PARTITION BY account_id ORDER BY first_order, normalized_name
        ) AS display_order
      FROM grouped
    )
    INSERT INTO watchlist_groups
      (id, account_id, name, display_order)
    SELECT
      md5(account_id || ':' || normalized_name),
      account_id,
      display_name,
      display_order
    FROM numbered
    ON CONFLICT (id) DO NOTHING
    """
  )
  op.execute(
    """
    INSERT INTO watchlist_group_memberships
      (group_id, watchlist_item_id, display_order)
    SELECT
      md5(w.account_id || ':' || lower(btrim(w.group_name))),
      w.id,
      row_number() OVER (
        PARTITION BY w.account_id, lower(btrim(w.group_name))
        ORDER BY w.display_order, w.created_at, w.id
      )
    FROM watchlist_items AS w
    WHERE w.group_name IS NOT NULL AND btrim(w.group_name) <> ''
    ON CONFLICT (group_id, watchlist_item_id) DO NOTHING
    """
  )
  op.drop_column("watchlist_items", "group_name")


def upgrade() -> None:
  inspector = _get_inspector()
  existing_tables = set(inspector.get_table_names())
  if _ITEM_TABLE not in existing_tables:
    _fail(f"missing required table {_ITEM_TABLE}")
  legacy_group_name_present = _legacy_group_name_present(inspector)

  existing_targets = _TARGET_TABLES & existing_tables
  if existing_targets and existing_targets != _TARGET_TABLES:
    missing = ", ".join(sorted(_TARGET_TABLES - existing_targets))
    raise RuntimeError(
      "Partial watchlist group schema detected; missing tables: " + missing
    )
  if existing_targets == _TARGET_TABLES:
    _validate_group_table(inspector)
    _validate_membership_table(inspector)
  else:
    if not legacy_group_name_present:
      _fail("legacy watchlist_items.group_name is missing before group-table creation")
    _create_group_tables()

  if legacy_group_name_present:
    _backfill_legacy_groups()


def downgrade() -> None:
  # A downgrade necessarily projects a many-to-many grouping back to one
  # legacy name.  Keep the first ordered membership when rolling back.
  op.add_column(
    "watchlist_items", sa.Column("group_name", sa.String(length=80), nullable=True)
  )
  op.execute(
    """
    UPDATE watchlist_items AS w
    SET group_name = first_group.name
    FROM (
      SELECT DISTINCT ON (m.watchlist_item_id)
        m.watchlist_item_id,
        g.name
      FROM watchlist_group_memberships AS m
      JOIN watchlist_groups AS g ON g.id = m.group_id
      ORDER BY m.watchlist_item_id, m.display_order, m.created_at, m.group_id
    ) AS first_group
    WHERE first_group.watchlist_item_id = w.id
    """
  )
  op.drop_index(
    "ix_watchlist_group_membership_item",
    table_name="watchlist_group_memberships",
  )
  op.drop_index(
    "ix_watchlist_group_membership_group_order",
    table_name="watchlist_group_memberships",
  )
  op.drop_table("watchlist_group_memberships")
  op.execute("DROP INDEX uq_watchlist_group_account_name_ci")
  op.drop_index("ix_watchlist_group_account_order", table_name="watchlist_groups")
  op.drop_table("watchlist_groups")
