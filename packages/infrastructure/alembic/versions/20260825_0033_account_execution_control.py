"""Separate account execution authorization from T-assistant rollout policy."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260825_0033"
down_revision = "20260825_0032"
branch_labels = None
depends_on = None


_LEGACY_GENERIC_COLUMNS = (
  "kill_switch",
  "reconcile_status",
  "last_snapshot_id",
  "last_snapshot_hash",
  "last_snapshot_at",
  "last_backup_at",
  "controlled_window_active",
  "controlled_window_snapshot_id",
  "controlled_window_snapshot_hash",
  "controlled_window_started_at",
  "controlled_window_started_by_user_id",
  "controlled_window_external_order_ids",
  "controlled_window_external_trade_ids",
)


_ACCOUNT_CONTROL_COLUMN_SPECS = {
  "account_id": (sa.String, 50, False),
  "authorization_state": (sa.String, 24, False),
  "state_version": (sa.Integer, None, False),
  "reconcile_status": (sa.String, 32, False),
  "authorized_by_user_id": (sa.String, 36, True),
  "authorized_at": (sa.DateTime, None, True),
  "paused_reason": (sa.Text, None, True),
  "last_snapshot_id": (sa.String, 128, True),
  "last_snapshot_hash": (sa.String, 64, True),
  "last_snapshot_at": (sa.DateTime, None, True),
  "last_backup_at": (sa.DateTime, None, True),
  "controlled_window_active": (sa.Boolean, None, False),
  "controlled_window_snapshot_id": (sa.String, 128, True),
  "controlled_window_snapshot_hash": (sa.String, 64, True),
  "controlled_window_started_at": (sa.DateTime, None, True),
  "controlled_window_started_by_user_id": (sa.String, 36, True),
  "controlled_window_external_order_ids": (sa.JSON, None, False),
  "controlled_window_external_trade_ids": (sa.JSON, None, False),
  "created_at": (sa.DateTime, None, False),
  "updated_at": (sa.DateTime, None, False),
}

_ACCOUNT_EVENT_COLUMN_SPECS = {
  "event_id": (sa.String, 128, False),
  "account_id": (sa.String, 50, False),
  "event_type": (sa.String, 64, False),
  "actor_user_id": (sa.String, 36, True),
  "previous_state": (sa.String, 24, True),
  "next_state": (sa.String, 24, True),
  "snapshot_id": (sa.String, 128, True),
  "details": (sa.JSON, None, False),
  "created_at": (sa.DateTime, None, False),
}


def _validate_precreated_table(
  inspector: sa.Inspector,
  table_name: str,
  column_specs: dict[str, tuple[type[sa.types.TypeEngine], int | None, bool]],
  *,
  primary_key: tuple[str, ...],
  indexes: dict[str, tuple[str, ...]],
) -> None:
  columns = {column["name"]: column for column in inspector.get_columns(table_name)}
  if set(columns) != set(column_specs):
    raise RuntimeError(
      f"precreated {table_name} has incompatible columns: "
      f"expected={sorted(column_specs)}, actual={sorted(columns)}"
    )
  for column_name, (type_class, length, nullable) in column_specs.items():
    column = columns[column_name]
    column_type = column["type"]
    if not isinstance(column_type, type_class):
      raise RuntimeError(
        f"precreated {table_name}.{column_name} has incompatible type {column_type!s}"
      )
    if length is not None and getattr(column_type, "length", None) != length:
      raise RuntimeError(
        f"precreated {table_name}.{column_name} has incompatible length "
        f"{getattr(column_type, 'length', None)!r}"
      )
    if bool(column.get("nullable")) is not nullable:
      raise RuntimeError(
        f"precreated {table_name}.{column_name} has incompatible nullability"
      )
  actual_primary_key = tuple(
    inspector.get_pk_constraint(table_name).get("constrained_columns") or ()
  )
  if actual_primary_key != primary_key:
    raise RuntimeError(
      f"precreated {table_name} has incompatible primary key {actual_primary_key!r}"
    )
  actual_indexes = {
    str(index["name"]): tuple(index.get("column_names") or ())
    for index in inspector.get_indexes(table_name)
  }
  if actual_indexes != indexes:
    raise RuntimeError(
      f"precreated {table_name} has incompatible indexes: "
      f"expected={indexes!r}, actual={actual_indexes!r}"
    )


def _adopt_precreated_tables() -> bool:
  inspector = inspect(op.get_bind())
  expected_tables = {
    "account_execution_controls",
    "account_execution_control_events",
  }
  existing_tables = expected_tables & set(inspector.get_table_names())
  if not existing_tables:
    return False
  if existing_tables != expected_tables:
    raise RuntimeError(
      "account execution control schema is partially precreated: "
      f"{sorted(existing_tables)}"
    )
  _validate_precreated_table(
    inspector,
    "account_execution_controls",
    _ACCOUNT_CONTROL_COLUMN_SPECS,
    primary_key=("account_id",),
    indexes={},
  )
  _validate_precreated_table(
    inspector,
    "account_execution_control_events",
    _ACCOUNT_EVENT_COLUMN_SPECS,
    primary_key=("event_id",),
    indexes={
      "ix_account_execution_control_events_account_id": ("account_id",),
      "ix_account_execution_control_event_account_created": (
        "account_id",
        "created_at",
      ),
    },
  )
  return True


def _create_account_execution_control_tables() -> None:
  op.create_table(
    "account_execution_controls",
    sa.Column("account_id", sa.String(length=50), primary_key=True),
    sa.Column(
      "authorization_state",
      sa.String(length=24),
      nullable=False,
      server_default="DISABLED",
    ),
    sa.Column("state_version", sa.Integer(), nullable=False, server_default="1"),
    sa.Column(
      "reconcile_status",
      sa.String(length=32),
      nullable=False,
      server_default="UNKNOWN",
    ),
    sa.Column("authorized_by_user_id", sa.String(length=36), nullable=True),
    sa.Column("authorized_at", sa.DateTime(), nullable=True),
    sa.Column("paused_reason", sa.Text(), nullable=True),
    sa.Column("last_snapshot_id", sa.String(length=128), nullable=True),
    sa.Column("last_snapshot_hash", sa.String(length=64), nullable=True),
    sa.Column("last_snapshot_at", sa.DateTime(), nullable=True),
    sa.Column("last_backup_at", sa.DateTime(), nullable=True),
    sa.Column(
      "controlled_window_active",
      sa.Boolean(),
      nullable=False,
      server_default=sa.false(),
    ),
    sa.Column("controlled_window_snapshot_id", sa.String(length=128), nullable=True),
    sa.Column("controlled_window_snapshot_hash", sa.String(length=64), nullable=True),
    sa.Column("controlled_window_started_at", sa.DateTime(), nullable=True),
    sa.Column(
      "controlled_window_started_by_user_id",
      sa.String(length=36),
      nullable=True,
    ),
    sa.Column(
      "controlled_window_external_order_ids",
      sa.JSON(),
      nullable=False,
      server_default="[]",
    ),
    sa.Column(
      "controlled_window_external_trade_ids",
      sa.JSON(),
      nullable=False,
      server_default="[]",
    ),
    sa.Column(
      "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
      "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
    ),
    comment="账户级实盘执行授权、对账事实与紧急停止状态",
  )
  op.create_table(
    "account_execution_control_events",
    sa.Column("event_id", sa.String(length=128), primary_key=True),
    sa.Column("account_id", sa.String(length=50), nullable=False),
    sa.Column("event_type", sa.String(length=64), nullable=False),
    sa.Column("actor_user_id", sa.String(length=36), nullable=True),
    sa.Column("previous_state", sa.String(length=24), nullable=True),
    sa.Column("next_state", sa.String(length=24), nullable=True),
    sa.Column("snapshot_id", sa.String(length=128), nullable=True),
    sa.Column("details", sa.JSON(), nullable=False),
    sa.Column("created_at", sa.DateTime(), nullable=False),
    comment="账户级实盘执行授权审计事件",
  )
  op.create_index(
    "ix_account_execution_control_events_account_id",
    "account_execution_control_events",
    ["account_id"],
  )
  op.create_index(
    "ix_account_execution_control_event_account_created",
    "account_execution_control_events",
    ["account_id", "created_at"],
  )


def _normalize_precreated_table_defaults() -> None:
  for column_name, existing_type, server_default in (
    ("authorization_state", sa.String(length=24), sa.text("'DISABLED'")),
    ("state_version", sa.Integer(), sa.text("1")),
    ("reconcile_status", sa.String(length=32), sa.text("'UNKNOWN'")),
    ("controlled_window_active", sa.Boolean(), sa.false()),
    (
      "controlled_window_external_order_ids",
      sa.JSON(),
      sa.text("'[]'::json"),
    ),
    (
      "controlled_window_external_trade_ids",
      sa.JSON(),
      sa.text("'[]'::json"),
    ),
    ("created_at", sa.DateTime(), sa.func.now()),
    ("updated_at", sa.DateTime(), sa.func.now()),
  ):
    op.alter_column(
      "account_execution_controls",
      column_name,
      existing_type=existing_type,
      server_default=server_default,
    )
  op.create_table_comment(
    "account_execution_controls",
    "账户级实盘执行授权、对账事实与紧急停止状态",
  )
  op.create_table_comment(
    "account_execution_control_events",
    "账户级实盘执行授权审计事件",
  )


def upgrade() -> None:
  if _adopt_precreated_tables():
    _normalize_precreated_table_defaults()
  else:
    _create_account_execution_control_tables()

  # A precreated control row must not retain risk-increase authorization or a
  # controlled window merely because ORM create_all ran before this revision.
  op.execute(
    sa.text(
      """
      UPDATE account_execution_controls
      SET authorization_state = CASE
            WHEN authorization_state = 'KILLED' THEN 'KILLED'
            ELSE 'DISABLED'
          END,
          state_version = 1,
          authorized_by_user_id = NULL,
          authorized_at = NULL,
          paused_reason = CASE
            WHEN authorization_state = 'KILLED'
              THEN COALESCE(paused_reason, 'precreated kill switch')
            ELSE NULL
          END,
          controlled_window_active = FALSE,
          controlled_window_snapshot_id = NULL,
          controlled_window_snapshot_hash = NULL,
          controlled_window_started_at = NULL,
          controlled_window_started_by_user_id = NULL,
          controlled_window_external_order_ids = '[]'::json,
          controlled_window_external_trade_ids = '[]'::json
      """
    )
  )

  # Preserve broker facts and the old emergency state, but deliberately do
  # not carry forward risk-increase authorization or a controlled window.
  op.execute(
    sa.text(
      """
      INSERT INTO account_execution_controls (
        account_id, authorization_state, state_version, reconcile_status,
        authorized_by_user_id, authorized_at, paused_reason,
        last_snapshot_id, last_snapshot_hash, last_snapshot_at, last_backup_at,
        controlled_window_active, controlled_window_snapshot_id,
        controlled_window_snapshot_hash, controlled_window_started_at,
        controlled_window_started_by_user_id,
        controlled_window_external_order_ids,
        controlled_window_external_trade_ids, created_at, updated_at
      )
      SELECT
        account_id,
        CASE WHEN kill_switch THEN 'KILLED' ELSE 'DISABLED' END,
        1,
        reconcile_status,
        NULL,
        NULL,
        CASE WHEN kill_switch THEN COALESCE(paused_reason, 'legacy kill switch') ELSE NULL END,
        last_snapshot_id,
        last_snapshot_hash,
        last_snapshot_at,
        last_backup_at,
        FALSE,
        NULL,
        NULL,
        NULL,
        NULL,
        '[]'::json,
        '[]'::json,
        created_at,
        updated_at
      FROM account_trading_rollouts
      ON CONFLICT (account_id) DO UPDATE
      SET authorization_state = CASE
            WHEN account_execution_controls.authorization_state = 'KILLED'
              OR EXCLUDED.authorization_state = 'KILLED'
              THEN 'KILLED'
            ELSE 'DISABLED'
          END,
          state_version = 1,
          reconcile_status = CASE
            WHEN account_execution_controls.last_snapshot_at IS NULL
              OR (
                EXCLUDED.last_snapshot_at IS NOT NULL
                AND EXCLUDED.last_snapshot_at
                  >= account_execution_controls.last_snapshot_at
              )
              THEN EXCLUDED.reconcile_status
            ELSE account_execution_controls.reconcile_status
          END,
          authorized_by_user_id = NULL,
          authorized_at = NULL,
          paused_reason = CASE
            WHEN account_execution_controls.authorization_state = 'KILLED'
              OR EXCLUDED.authorization_state = 'KILLED'
              THEN COALESCE(
                account_execution_controls.paused_reason,
                EXCLUDED.paused_reason,
                'migrated kill switch'
              )
            ELSE NULL
          END,
          last_snapshot_id = CASE
            WHEN account_execution_controls.last_snapshot_at IS NULL
              OR (
                EXCLUDED.last_snapshot_at IS NOT NULL
                AND EXCLUDED.last_snapshot_at
                  >= account_execution_controls.last_snapshot_at
              )
              THEN EXCLUDED.last_snapshot_id
            ELSE account_execution_controls.last_snapshot_id
          END,
          last_snapshot_hash = CASE
            WHEN account_execution_controls.last_snapshot_at IS NULL
              OR (
                EXCLUDED.last_snapshot_at IS NOT NULL
                AND EXCLUDED.last_snapshot_at
                  >= account_execution_controls.last_snapshot_at
              )
              THEN EXCLUDED.last_snapshot_hash
            ELSE account_execution_controls.last_snapshot_hash
          END,
          last_snapshot_at = CASE
            WHEN account_execution_controls.last_snapshot_at IS NULL
              OR (
                EXCLUDED.last_snapshot_at IS NOT NULL
                AND EXCLUDED.last_snapshot_at
                  >= account_execution_controls.last_snapshot_at
              )
              THEN EXCLUDED.last_snapshot_at
            ELSE account_execution_controls.last_snapshot_at
          END,
          last_backup_at = CASE
            WHEN account_execution_controls.last_backup_at IS NULL
              OR (
                EXCLUDED.last_backup_at IS NOT NULL
                AND EXCLUDED.last_backup_at
                  >= account_execution_controls.last_backup_at
              )
              THEN EXCLUDED.last_backup_at
            ELSE account_execution_controls.last_backup_at
          END,
          controlled_window_active = FALSE,
          controlled_window_snapshot_id = NULL,
          controlled_window_snapshot_hash = NULL,
          controlled_window_started_at = NULL,
          controlled_window_started_by_user_id = NULL,
          controlled_window_external_order_ids = '[]'::json,
          controlled_window_external_trade_ids = '[]'::json,
          created_at = LEAST(
            account_execution_controls.created_at,
            EXCLUDED.created_at
          ),
          updated_at = GREATEST(
            account_execution_controls.updated_at,
            EXCLUDED.updated_at
          )
      """
    )
  )
  op.execute(
    sa.text(
      """
      INSERT INTO account_execution_control_events (
        event_id, account_id, event_type, actor_user_id, previous_state,
        next_state, snapshot_id, details, created_at
      )
      SELECT
        'migration:' || md5(account_id),
        account_id,
        'ACCOUNT_EXECUTION_CONTROL_MIGRATED',
        NULL,
        NULL,
        authorization_state,
        last_snapshot_id,
        json_build_object(
          'authorizationReset', authorization_state = 'DISABLED',
          'sourceTable', 'account_trading_rollouts'
        ),
        CURRENT_TIMESTAMP
      FROM account_execution_controls
      ON CONFLICT (event_id) DO NOTHING
      """
    )
  )
  op.execute(
    sa.text(
      """
      UPDATE account_trading_rollouts
      SET enabled = FALSE,
          stage = 'PAUSED',
          paused_reason = COALESCE(paused_reason, 'account kill migrated')
      WHERE kill_switch = TRUE
      """
    )
  )

  for column_name in _LEGACY_GENERIC_COLUMNS:
    op.drop_column("account_trading_rollouts", column_name)
  op.create_table_comment(
    "account_trading_rollouts",
    "持仓做 T 助手的灰度、额度与策略确认状态",
    existing_comment="证券账户实盘灰度与熔断状态",
  )

  # Operators who already hold live trade approval may independently
  # re-authorize the account after migration.
  op.execute(
    sa.text(
      """
      UPDATE auth_users
      SET permissions = (
        permissions::jsonb || '["account-execution:control"]'::jsonb
      )::json
      WHERE (permissions::jsonb ? 'trade:approve')
        AND NOT (permissions::jsonb ? 'account-execution:control')
      """
    )
  )
  op.execute(
    sa.text(
      """
      UPDATE auth_device_sessions
      SET granted_permissions = (
        granted_permissions::jsonb || '["account-execution:control"]'::jsonb
      )::json
      WHERE granted_permissions IS NOT NULL
        AND (granted_permissions::jsonb ? 'trade:approve')
        AND NOT (granted_permissions::jsonb ? 'account-execution:control')
      """
    )
  )


def downgrade() -> None:
  op.create_table_comment(
    "account_trading_rollouts",
    "证券账户实盘灰度与熔断状态",
    existing_comment="持仓做 T 助手的灰度、额度与策略确认状态",
  )
  for name, column_type in (
    ("kill_switch", sa.Boolean()),
    ("reconcile_status", sa.String(length=32)),
    ("last_snapshot_id", sa.String(length=128)),
    ("last_snapshot_hash", sa.String(length=64)),
    ("last_snapshot_at", sa.DateTime()),
    ("last_backup_at", sa.DateTime()),
    ("controlled_window_active", sa.Boolean()),
    ("controlled_window_snapshot_id", sa.String(length=128)),
    ("controlled_window_snapshot_hash", sa.String(length=64)),
    ("controlled_window_started_at", sa.DateTime()),
    ("controlled_window_started_by_user_id", sa.String(length=36)),
    ("controlled_window_external_order_ids", sa.JSON()),
    ("controlled_window_external_trade_ids", sa.JSON()),
  ):
    default = None
    if name in {"kill_switch", "controlled_window_active"}:
      default = sa.false()
    elif name == "reconcile_status":
      default = "UNKNOWN"
    elif name.endswith("_ids"):
      default = "[]"
    op.add_column(
      "account_trading_rollouts",
      sa.Column(name, column_type, nullable=default is None, server_default=default),
    )
  op.execute(
    sa.text(
      """
      UPDATE account_trading_rollouts AS rollout
      SET kill_switch = control.authorization_state = 'KILLED',
          reconcile_status = control.reconcile_status,
          last_snapshot_id = control.last_snapshot_id,
          last_snapshot_hash = control.last_snapshot_hash,
          last_snapshot_at = control.last_snapshot_at,
          last_backup_at = control.last_backup_at,
          controlled_window_active = control.controlled_window_active,
          controlled_window_snapshot_id = control.controlled_window_snapshot_id,
          controlled_window_snapshot_hash = control.controlled_window_snapshot_hash,
          controlled_window_started_at = control.controlled_window_started_at,
          controlled_window_started_by_user_id = control.controlled_window_started_by_user_id,
          controlled_window_external_order_ids = control.controlled_window_external_order_ids,
          controlled_window_external_trade_ids = control.controlled_window_external_trade_ids
      FROM account_execution_controls AS control
      WHERE rollout.account_id = control.account_id
      """
    )
  )
  op.drop_index(
    "ix_account_execution_control_event_account_created",
    table_name="account_execution_control_events",
  )
  op.drop_index(
    "ix_account_execution_control_events_account_id",
    table_name="account_execution_control_events",
  )
  op.drop_table("account_execution_control_events")
  op.drop_table("account_execution_controls")
