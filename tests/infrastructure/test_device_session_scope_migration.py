from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ROOT / "packages" / "infrastructure" / "alembic" / "versions"


def _load_revision(filename: str, module_name: str) -> ModuleType:
  path = VERSIONS / filename
  spec = importlib.util.spec_from_file_location(module_name, path)
  assert spec is not None
  assert spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def test_device_session_scope_revision_follows_trade_confirmation() -> None:
  revision = _load_revision(
    "20260815_0016_device_session_scopes.py",
    "quantx_test_device_session_scope_revision",
  )

  assert revision.down_revision == "20260815_0015"
  with pytest.raises(RuntimeError, match="downgrades"):
    revision.downgrade()


def test_personal_session_scope_revision_merges_retired_head() -> None:
  legacy = _load_revision(
    "20260816_0015_legacy_graphql_write_permissions.py",
    "quantx_test_legacy_graphql_write_permissions_revision",
  )
  revision = _load_revision(
    "20260818_0022_personal_session_scope.py",
    "quantx_test_personal_session_scope_revision",
  )

  assert legacy.down_revision == "20260814_0014"
  assert revision.down_revision == ("20260816_0021", "20260816_0015")
  with pytest.raises(RuntimeError, match="downgrades"):
    revision.downgrade()


def test_personal_session_scope_drops_redundant_account_column(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  revision = _load_revision(
    "20260818_0022_personal_session_scope.py",
    "quantx_test_personal_session_scope_upgrade",
  )
  inspector = _DeviceSessionScopeInspector(
    columns=_valid_device_session_scope_columns(),
    constraints=[_valid_device_session_scope_constraint()],
  )
  statements = []
  dropped_constraints = []
  dropped_columns = []
  monkeypatch.setattr(revision.op, "get_bind", lambda: object())
  monkeypatch.setattr(revision, "inspect", lambda _bind: inspector)
  monkeypatch.setattr(revision.op, "execute", statements.append)
  monkeypatch.setattr(
    revision.op,
    "drop_constraint",
    lambda *args, **kwargs: dropped_constraints.append((args, kwargs)),
  )
  monkeypatch.setattr(
    revision.op,
    "drop_column",
    lambda *args: dropped_columns.append(args),
  )

  revision.upgrade()

  assert len(statements) == 1
  assert "revoked_at IS NULL" in str(statements[0])
  assert dropped_constraints == [
    (
      ("ck_auth_device_sessions_scope_pair", "auth_device_sessions"),
      {"type_": "check"},
    )
  ]
  assert dropped_columns == [("auth_device_sessions", "active_account_id")]


class _DeviceSessionScopeInspector:
  def __init__(self, *, columns, constraints=()):
    self.columns = columns
    self.constraints = constraints

  @staticmethod
  def get_table_names():
    return ["auth_device_sessions"]

  def get_columns(self, table_name):
    assert table_name == "auth_device_sessions"
    return self.columns

  def get_check_constraints(self, table_name):
    assert table_name == "auth_device_sessions"
    return self.constraints


def _valid_device_session_scope_columns():
  return [
    {
      "name": "active_account_id",
      "type": sa.String(length=50),
      "nullable": True,
    },
    {
      "name": "granted_permissions",
      "type": sa.JSON(),
      "nullable": True,
    },
  ]


def _valid_device_session_scope_constraint():
  return {
    "name": "ck_auth_device_sessions_scope_pair",
    "sqltext": "(active_account_id IS NULL) = (granted_permissions IS NULL)",
  }


def test_device_session_scope_revision_is_idempotent_for_valid_schema(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  revision = _load_revision(
    "20260815_0016_device_session_scopes.py",
    "quantx_test_device_session_scope_idempotent",
  )
  inspector = _DeviceSessionScopeInspector(
    columns=_valid_device_session_scope_columns(),
    constraints=[_valid_device_session_scope_constraint()],
  )
  calls = []
  monkeypatch.setattr(revision.op, "get_bind", lambda: object())
  monkeypatch.setattr(revision, "inspect", lambda _bind: inspector)
  monkeypatch.setattr(revision.op, "add_column", lambda *args: calls.append(args))
  monkeypatch.setattr(
    revision.op,
    "create_check_constraint",
    lambda *args: calls.append(args),
  )
  statements = []
  monkeypatch.setattr(revision.op, "execute", statements.append)

  revision.upgrade()

  assert calls == []
  assert len(statements) == 1
  sql = str(statements[0])
  assert "revoked_at IS NULL" in sql
  assert "active_account_id IS NULL" in sql
  assert "granted_permissions IS NULL" in sql


def test_device_session_scope_revision_adds_paired_columns_and_constraint(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  revision = _load_revision(
    "20260815_0016_device_session_scopes.py",
    "quantx_test_device_session_scope_fresh",
  )
  inspector = _DeviceSessionScopeInspector(columns=[])
  added_columns = []
  constraints = []
  statements = []
  monkeypatch.setattr(revision.op, "get_bind", lambda: object())
  monkeypatch.setattr(revision, "inspect", lambda _bind: inspector)
  monkeypatch.setattr(
    revision.op,
    "add_column",
    lambda table_name, column: added_columns.append((table_name, column)),
  )
  monkeypatch.setattr(
    revision.op,
    "create_check_constraint",
    lambda *args: constraints.append(args),
  )
  monkeypatch.setattr(revision.op, "execute", statements.append)

  revision.upgrade()

  assert [column.name for _, column in added_columns] == [
    "active_account_id",
    "granted_permissions",
  ]
  assert constraints == [
    (
      "ck_auth_device_sessions_scope_pair",
      "auth_device_sessions",
      "(active_account_id IS NULL) = (granted_permissions IS NULL)",
    )
  ]
  assert len(statements) == 1


def test_device_session_scope_rollout_revokes_only_active_legacy_sessions(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  revision = _load_revision(
    "20260815_0016_device_session_scopes.py",
    "quantx_test_device_session_scope_revocation",
  )
  statements = []
  monkeypatch.setattr(revision.op, "execute", statements.append)
  revision._revoke_unscoped_active_sessions()
  assert len(statements) == 1

  metadata = sa.MetaData()
  sessions = sa.Table(
    "auth_device_sessions",
    metadata,
    sa.Column("id", sa.String, primary_key=True),
    sa.Column("revoked_at", sa.DateTime, nullable=True),
    sa.Column("active_account_id", sa.String(50), nullable=True),
    sa.Column("granted_permissions", sa.JSON(none_as_null=True), nullable=True),
  )
  engine = sa.create_engine("sqlite://")
  prior_revocation = datetime(2026, 1, 2, 3, 4, 5)
  with engine.begin() as connection:
    metadata.create_all(connection)
    connection.execute(
      sessions.insert(),
      [
        {
          "id": "legacy-active",
          "revoked_at": None,
          "active_account_id": None,
          "granted_permissions": None,
        },
        {
          "id": "legacy-revoked",
          "revoked_at": prior_revocation,
          "active_account_id": None,
          "granted_permissions": None,
        },
        {
          "id": "native-scoped",
          "revoked_at": None,
          "active_account_id": "account-1",
          "granted_permissions": [],
        },
      ],
    )
    connection.execute(statements[0])
    rows = {
      row.id: row for row in connection.execute(sa.select(sessions)).mappings().all()
    }

  assert set(rows) == {"legacy-active", "legacy-revoked", "native-scoped"}
  assert rows["legacy-active"]["revoked_at"] is not None
  assert rows["legacy-revoked"]["revoked_at"] == prior_revocation
  assert rows["native-scoped"]["revoked_at"] is None


@pytest.mark.parametrize(
  ("columns", "constraints", "message"),
  [
    (
      _valid_device_session_scope_columns()[:1],
      [],
      "Partial auth_device_sessions scope schema",
    ),
    (
      [
        {
          "name": "active_account_id",
          "type": sa.String(length=36),
          "nullable": True,
        },
        _valid_device_session_scope_columns()[1],
      ],
      [_valid_device_session_scope_constraint()],
      r"active_account_id must be VARCHAR\(50\)",
    ),
    (
      _valid_device_session_scope_columns(),
      [],
      "scope-pair check constraint",
    ),
  ],
)
def test_device_session_scope_revision_rejects_malformed_existing_schema(
  monkeypatch: pytest.MonkeyPatch,
  columns,
  constraints,
  message,
) -> None:
  revision = _load_revision(
    "20260815_0016_device_session_scopes.py",
    "quantx_test_device_session_scope_malformed",
  )
  inspector = _DeviceSessionScopeInspector(
    columns=columns,
    constraints=constraints,
  )
  monkeypatch.setattr(revision.op, "get_bind", lambda: object())
  monkeypatch.setattr(revision, "inspect", lambda _bind: inspector)
  monkeypatch.setattr(
    revision.op,
    "execute",
    lambda _statement: pytest.fail("malformed schema must fail before revocation"),
  )

  with pytest.raises(RuntimeError, match=message):
    revision.upgrade()
