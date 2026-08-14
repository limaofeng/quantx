"""Add session-bound APNs registrations and opaque notification outbox."""

from __future__ import annotations

from collections.abc import Iterable

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260815_0017"
down_revision = "20260815_0016"
branch_labels = None
depends_on = None

REGISTRATIONS = "ios_push_registrations"
PREFERENCES = "ios_push_category_preferences"
EVENTS = "ios_notification_events"
OUTBOX = "ios_notification_outbox"
TABLES = frozenset({REGISTRATIONS, PREFERENCES, EVENTS, OUTBOX})

_COLUMNS: dict[str, dict[str, bool]] = {
  REGISTRATIONS: {
    "id": False,
    "user_id": False,
    "device_session_id": False,
    "account_id": False,
    "device_install_id": False,
    "app_bundle_id": False,
    "app_version": False,
    "apns_environment": False,
    "token_ciphertext": True,
    "token_fingerprint": False,
    "registered_at": False,
    "last_seen_at": False,
    "invalidated_at": True,
    "created_at": False,
    "updated_at": False,
  },
  PREFERENCES: {
    "registration_id": False,
    "category": False,
    "enabled": False,
    "created_at": False,
    "updated_at": False,
  },
  EVENTS: {
    "id": False,
    "user_id": False,
    "device_session_id": False,
    "account_id": False,
    "category": False,
    "route_type": False,
    "occurred_at": False,
    "expires_at": False,
    "created_at": False,
    "updated_at": False,
  },
  OUTBOX: {
    "id": False,
    "event_id": False,
    "registration_id": False,
    "status": False,
    "attempt_count": False,
    "available_at": False,
    "sent_at": True,
    "apns_request_id": True,
    "last_error_code": True,
    "created_at": False,
    "updated_at": False,
  },
}
_PRIMARY_KEYS = {
  REGISTRATIONS: ("id",),
  PREFERENCES: ("registration_id", "category"),
  EVENTS: ("id",),
  OUTBOX: ("id",),
}
_STRING_LENGTHS: dict[str, dict[str, int]] = {
  REGISTRATIONS: {
    "id": 36,
    "user_id": 36,
    "device_session_id": 36,
    "account_id": 50,
    "device_install_id": 36,
    "app_bundle_id": 255,
    "app_version": 64,
    "apns_environment": 16,
    "token_fingerprint": 64,
  },
  PREFERENCES: {"registration_id": 36, "category": 32},
  EVENTS: {
    "id": 36,
    "user_id": 36,
    "device_session_id": 36,
    "account_id": 50,
    "category": 32,
    "route_type": 40,
  },
  OUTBOX: {
    "id": 36,
    "event_id": 36,
    "registration_id": 36,
    "status": 16,
    "apns_request_id": 36,
    "last_error_code": 64,
  },
}
_TEXT_COLUMNS = {REGISTRATIONS: {"token_ciphertext"}}
_BOOLEAN_COLUMNS = {PREFERENCES: {"enabled"}}
_INTEGER_COLUMNS = {OUTBOX: {"attempt_count"}}
_DATETIME_COLUMNS = {
  REGISTRATIONS: {
    "registered_at",
    "last_seen_at",
    "invalidated_at",
    "created_at",
    "updated_at",
  },
  PREFERENCES: {"created_at", "updated_at"},
  EVENTS: {"occurred_at", "expires_at", "created_at", "updated_at"},
  OUTBOX: {"available_at", "sent_at", "created_at", "updated_at"},
}
_CHECK_CONSTRAINTS: dict[str, dict[str, tuple[str, ...]]] = {
  REGISTRATIONS: {
    "ck_ios_push_registration_environment": (
      "apns_environment",
      "sandbox",
      "production",
    ),
    "ck_ios_push_registration_active_token": (
      "invalidated_at",
      "token_ciphertext",
    ),
  },
  PREFERENCES: {
    "ck_ios_push_preference_category": ("category", "connection_data")
  },
  EVENTS: {
    "ck_ios_notification_event_category": ("category", "connection_data"),
    "ck_ios_notification_event_route": ("route_type", "today.action"),
  },
  OUTBOX: {
    "ck_ios_notification_outbox_status": ("status", "discarded"),
    "ck_ios_notification_outbox_attempt_count": ("attempt_count", ">=0"),
  },
}
_UNIQUE_CONSTRAINTS = {
  REGISTRATIONS: {
    (
      "user_id",
      "app_bundle_id",
      "apns_environment",
      "device_install_id",
    )
  },
  PREFERENCES: set(),
  EVENTS: set(),
  OUTBOX: {("event_id", "registration_id")},
}
_INDEXES: dict[str, dict[str, tuple[tuple[str, ...], bool]]] = {
  REGISTRATIONS: {
    "uq_ios_push_registration_active_session": (
      ("device_session_id", "app_bundle_id", "apns_environment"),
      True,
    ),
    "ix_ios_push_registration_token_fingerprint": (
      ("token_fingerprint",),
      False,
    ),
    "ix_ios_push_registration_account_active": (
      ("account_id", "invalidated_at"),
      False,
    ),
  },
  PREFERENCES: {},
  EVENTS: {
    "ix_ios_notification_event_session_expiry": (
      ("device_session_id", "expires_at"),
      False,
    ),
    "ix_ios_notification_event_account_occurred": (
      ("account_id", "occurred_at"),
      False,
    ),
  },
  OUTBOX: {
    "ix_ios_notification_outbox_delivery": (
      ("status", "available_at"),
      False,
    )
  },
}
_FOREIGN_KEYS: dict[str, set[tuple[tuple[str, ...], str, tuple[str, ...], str]]] = {
  REGISTRATIONS: {
    (("user_id",), "auth_users", ("id",), "CASCADE"),
    (("device_session_id",), "auth_device_sessions", ("id",), "CASCADE"),
  },
  PREFERENCES: {
    (("registration_id",), REGISTRATIONS, ("id",), "CASCADE"),
  },
  EVENTS: {
    (("user_id",), "auth_users", ("id",), "CASCADE"),
    (("device_session_id",), "auth_device_sessions", ("id",), "CASCADE"),
  },
  OUTBOX: {
    (("event_id",), EVENTS, ("id",), "CASCADE"),
    (("registration_id",), REGISTRATIONS, ("id",), "CASCADE"),
  },
}


def _tuples(values: Iterable[dict], key: str) -> set[tuple[str, ...]]:
  return {
    tuple(str(column) for column in list(value.get(key) or []))
    for value in values
  }


def _validate_existing_schema(inspector) -> None:
  problems: list[str] = []
  for table_name in sorted(TABLES):
    columns = {
      str(column.get("name") or ""): column
      for column in inspector.get_columns(table_name)
    }
    expected_columns = _COLUMNS[table_name]
    missing = sorted(set(expected_columns) - set(columns))
    wrong_nullability = sorted(
      name
      for name, nullable in expected_columns.items()
      if name in columns and bool(columns[name].get("nullable", True)) != nullable
    )
    if missing:
      problems.append(f"{table_name}:missing={','.join(missing)}")
    if wrong_nullability:
      problems.append(
        f"{table_name}:nullability={','.join(wrong_nullability)}"
      )

    wrong_types: list[str] = []
    for column_name, length in _STRING_LENGTHS.get(table_name, {}).items():
      column_type = columns.get(column_name, {}).get("type")
      if not isinstance(column_type, sa.String) or getattr(
        column_type, "length", None
      ) != length:
        wrong_types.append(column_name)
    for column_name in _TEXT_COLUMNS.get(table_name, set()):
      if not isinstance(columns.get(column_name, {}).get("type"), sa.Text):
        wrong_types.append(column_name)
    for column_name in _BOOLEAN_COLUMNS.get(table_name, set()):
      if not isinstance(columns.get(column_name, {}).get("type"), sa.Boolean):
        wrong_types.append(column_name)
    for column_name in _INTEGER_COLUMNS.get(table_name, set()):
      if not isinstance(columns.get(column_name, {}).get("type"), sa.Integer):
        wrong_types.append(column_name)
    for column_name in _DATETIME_COLUMNS.get(table_name, set()):
      if not isinstance(columns.get(column_name, {}).get("type"), sa.DateTime):
        wrong_types.append(column_name)
    if wrong_types:
      problems.append(f"{table_name}:types={','.join(sorted(set(wrong_types)))}")

    actual_pk = tuple(
      str(column)
      for column in list(
        inspector.get_pk_constraint(table_name).get("constrained_columns") or []
      )
    )
    if actual_pk != _PRIMARY_KEYS[table_name]:
      problems.append(f"{table_name}:primary-key")

    actual_checks = {
      str(constraint.get("name") or ""): "".join(
        str(constraint.get("sqltext") or "").lower().split()
      )
      for constraint in inspector.get_check_constraints(table_name)
    }
    for constraint_name, required_fragments in _CHECK_CONSTRAINTS[
      table_name
    ].items():
      sqltext = actual_checks.get(constraint_name)
      if sqltext is None or any(
        fragment not in sqltext for fragment in required_fragments
      ):
        problems.append(f"{table_name}:check={constraint_name}")

    actual_uniques = _tuples(
      inspector.get_unique_constraints(table_name), "column_names"
    )
    missing_uniques = sorted(_UNIQUE_CONSTRAINTS[table_name] - actual_uniques)
    if missing_uniques:
      problems.append(f"{table_name}:unique")

    actual_indexes = {
      str(index.get("name") or ""): index
      for index in inspector.get_indexes(table_name)
    }
    for index_name, (expected_index_columns, expected_unique) in _INDEXES[
      table_name
    ].items():
      index = actual_indexes.get(index_name)
      if index is None:
        problems.append(f"{table_name}:index={index_name}")
        continue
      index_columns = tuple(
        str(column) for column in list(index.get("column_names") or [])
      )
      if index_columns != expected_index_columns or bool(
        index.get("unique", False)
      ) != expected_unique:
        problems.append(f"{table_name}:invalid-index={index_name}")
      if index_name == "uq_ios_push_registration_active_session":
        dialect_options = dict(index.get("dialect_options") or {})
        predicate_value = dialect_options.get("postgresql_where")
        if predicate_value is None:
          predicate_value = dialect_options.get("sqlite_where")
        predicate = str(
          predicate_value if predicate_value is not None else ""
        ).lower()
        if "invalidated_at" not in predicate or "null" not in predicate:
          problems.append(f"{table_name}:invalid-index-predicate={index_name}")

    actual_foreign_keys = {
      (
        tuple(str(column) for column in list(foreign_key.get("constrained_columns") or [])),
        str(foreign_key.get("referred_table") or ""),
        tuple(str(column) for column in list(foreign_key.get("referred_columns") or [])),
        str(dict(foreign_key.get("options") or {}).get("ondelete") or "").upper(),
      )
      for foreign_key in inspector.get_foreign_keys(table_name)
    }
    missing_foreign_keys = _FOREIGN_KEYS[table_name] - actual_foreign_keys
    if missing_foreign_keys:
      problems.append(f"{table_name}:foreign-key")

  if problems:
    raise RuntimeError(
      "Invalid existing iOS notification schema detected: " + "; ".join(problems)
    )


def upgrade() -> None:
  inspector = inspect(op.get_bind())
  existing_tables = set(inspector.get_table_names()) & TABLES
  if existing_tables:
    if existing_tables != TABLES:
      raise RuntimeError(
        "Partial iOS notification schema detected: present="
        + ",".join(sorted(existing_tables))
      )
    _validate_existing_schema(inspector)
    return

  op.create_table(
    REGISTRATIONS,
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column(
      "user_id",
      sa.String(length=36),
      sa.ForeignKey("auth_users.id", ondelete="CASCADE"),
      nullable=False,
    ),
    sa.Column(
      "device_session_id",
      sa.String(length=36),
      sa.ForeignKey("auth_device_sessions.id", ondelete="CASCADE"),
      nullable=False,
    ),
    sa.Column("account_id", sa.String(length=50), nullable=False),
    sa.Column("device_install_id", sa.String(length=36), nullable=False),
    sa.Column("app_bundle_id", sa.String(length=255), nullable=False),
    sa.Column("app_version", sa.String(length=64), nullable=False),
    sa.Column("apns_environment", sa.String(length=16), nullable=False),
    sa.Column("token_ciphertext", sa.Text(), nullable=True),
    sa.Column("token_fingerprint", sa.String(length=64), nullable=False),
    sa.Column("registered_at", sa.DateTime(), nullable=False),
    sa.Column("last_seen_at", sa.DateTime(), nullable=False),
    sa.Column("invalidated_at", sa.DateTime(), nullable=True),
    sa.Column(
      "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
      "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
    ),
    sa.CheckConstraint(
      "apns_environment IN ('SANDBOX', 'PRODUCTION')",
      name="ck_ios_push_registration_environment",
    ),
    sa.CheckConstraint(
      "invalidated_at IS NOT NULL OR token_ciphertext IS NOT NULL",
      name="ck_ios_push_registration_active_token",
    ),
    sa.UniqueConstraint(
      "user_id",
      "app_bundle_id",
      "apns_environment",
      "device_install_id",
      name="uq_ios_push_registration_install",
    ),
    comment="iOS 会话绑定的 APNs 设备注册",
  )
  op.create_index(
    "uq_ios_push_registration_active_session",
    REGISTRATIONS,
    ["device_session_id", "app_bundle_id", "apns_environment"],
    unique=True,
    postgresql_where=sa.text("invalidated_at IS NULL"),
  )
  op.create_index(
    "ix_ios_push_registration_token_fingerprint",
    REGISTRATIONS,
    ["token_fingerprint"],
  )
  op.create_index(
    "ix_ios_push_registration_account_active",
    REGISTRATIONS,
    ["account_id", "invalidated_at"],
  )

  op.create_table(
    PREFERENCES,
    sa.Column(
      "registration_id",
      sa.String(length=36),
      sa.ForeignKey(f"{REGISTRATIONS}.id", ondelete="CASCADE"),
      primary_key=True,
    ),
    sa.Column("category", sa.String(length=32), primary_key=True),
    sa.Column("enabled", sa.Boolean(), nullable=False),
    sa.Column(
      "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
      "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
    ),
    sa.CheckConstraint(
      "category IN ('ACTION_REQUIRED', 'ORDER_UPDATE', 'RISK_SAFETY', "
      "'AUTOMATION_ERROR', 'CONNECTION_DATA')",
      name="ck_ios_push_preference_category",
    ),
    comment="iOS 安装实例通知类别偏好",
  )

  op.create_table(
    EVENTS,
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column(
      "user_id",
      sa.String(length=36),
      sa.ForeignKey("auth_users.id", ondelete="CASCADE"),
      nullable=False,
    ),
    sa.Column(
      "device_session_id",
      sa.String(length=36),
      sa.ForeignKey("auth_device_sessions.id", ondelete="CASCADE"),
      nullable=False,
    ),
    sa.Column("account_id", sa.String(length=50), nullable=False),
    sa.Column("category", sa.String(length=32), nullable=False),
    sa.Column("route_type", sa.String(length=40), nullable=False),
    sa.Column("occurred_at", sa.DateTime(), nullable=False),
    sa.Column("expires_at", sa.DateTime(), nullable=False),
    sa.Column(
      "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
      "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
    ),
    sa.CheckConstraint(
      "category IN ('ACTION_REQUIRED', 'ORDER_UPDATE', 'RISK_SAFETY', "
      "'AUTOMATION_ERROR', 'CONNECTION_DATA')",
      name="ck_ios_notification_event_category",
    ),
    sa.CheckConstraint(
      "route_type IN ('today.action', 'trading.orders', 'trading.safety', "
      "'quant.workspace', 'system.status')",
      name="ck_ios_notification_event_route",
    ),
    comment="iOS 随机通知事件与解锁后路由元数据",
  )
  op.create_index(
    "ix_ios_notification_event_session_expiry",
    EVENTS,
    ["device_session_id", "expires_at"],
  )
  op.create_index(
    "ix_ios_notification_event_account_occurred",
    EVENTS,
    ["account_id", "occurred_at"],
  )

  op.create_table(
    OUTBOX,
    sa.Column("id", sa.String(length=36), primary_key=True),
    sa.Column(
      "event_id",
      sa.String(length=36),
      sa.ForeignKey(f"{EVENTS}.id", ondelete="CASCADE"),
      nullable=False,
    ),
    sa.Column(
      "registration_id",
      sa.String(length=36),
      sa.ForeignKey(f"{REGISTRATIONS}.id", ondelete="CASCADE"),
      nullable=False,
    ),
    sa.Column("status", sa.String(length=16), nullable=False),
    sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("available_at", sa.DateTime(), nullable=False),
    sa.Column("sent_at", sa.DateTime(), nullable=True),
    sa.Column("apns_request_id", sa.String(length=36), nullable=True),
    sa.Column("last_error_code", sa.String(length=64), nullable=True),
    sa.Column(
      "created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
    ),
    sa.Column(
      "updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()
    ),
    sa.CheckConstraint(
      "status IN ('PENDING', 'SENT', 'RETRY', 'FAILED', 'DISCARDED')",
      name="ck_ios_notification_outbox_status",
    ),
    sa.CheckConstraint(
      "attempt_count >= 0",
      name="ck_ios_notification_outbox_attempt_count",
    ),
    sa.UniqueConstraint(
      "event_id",
      "registration_id",
      name="uq_ios_notification_outbox_event_registration",
    ),
    comment="iOS 最小隐私推送持久化发件箱",
  )
  op.create_index(
    "ix_ios_notification_outbox_delivery",
    OUTBOX,
    ["status", "available_at"],
  )


def downgrade() -> None:
  raise RuntimeError("QuantX production schema downgrades are intentionally disabled")
