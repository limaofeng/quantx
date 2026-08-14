from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).resolve().parents[2]
REVISION_PATH = (
  ROOT
  / "packages"
  / "infrastructure"
  / "alembic"
  / "versions"
  / "20260815_0018_exit_plan_authorization.py"
)


def _revision(module_name: str = "quantx_test_exit_plan_authorization_revision"):
  spec = importlib.util.spec_from_file_location(module_name, REVISION_PATH)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def _schema_inspector():
  engine = create_engine("sqlite:///:memory:")
  Base.metadata.create_all(engine, tables=[AutoExitPlanRecord.__table__])
  return engine, inspect(engine)


def test_exit_plan_authorization_revision_is_forward_only() -> None:
  revision = _revision()

  assert revision.down_revision == "20260815_0017"
  with pytest.raises(RuntimeError, match="downgrades"):
    revision.downgrade()


def test_exit_plan_authorization_model_schema_passes_validator() -> None:
  revision = _revision("quantx_test_exit_plan_authorization_schema")
  engine, inspector = _schema_inspector()
  try:
    revision._validate_existing_schema(inspector)
  finally:
    engine.dispose()


def test_exit_plan_authorization_validator_rejects_partial_envelope() -> None:
  revision = _revision("quantx_test_exit_plan_authorization_partial")
  engine, inspector = _schema_inspector()

  class MissingDeviceBindingInspector:
    def get_columns(self, table_name):
      return [
        column
        for column in inspector.get_columns(table_name)
        if column["name"] != "auto_exit_authorization_device_session_id"
      ]

    def get_check_constraints(self, table_name):
      return inspector.get_check_constraints(table_name)

  try:
    with pytest.raises(RuntimeError, match="device_session_id"):
      revision._validate_existing_schema(MissingDeviceBindingInspector())
  finally:
    engine.dispose()


class _FreshInspector:
  @staticmethod
  def get_table_names():
    return ["auto_exit_plans", "conditional_liquidation_orders"]

  @staticmethod
  def get_columns(table_name):
    assert table_name == "auto_exit_plans"
    return [{"name": "auto_exit_authorized", "type": sa.Boolean()}]


def test_exit_plan_authorization_upgrade_adds_envelope_and_invalidates_legacy(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  revision = _revision("quantx_test_exit_plan_authorization_fresh")
  added: list[tuple[str, sa.Column]] = []
  constraints: list[tuple[str, str, str]] = []
  statements: list[object] = []
  monkeypatch.setattr(revision.op, "get_bind", lambda: object())
  monkeypatch.setattr(revision, "inspect", lambda _bind: _FreshInspector())
  monkeypatch.setattr(
    revision.op,
    "add_column",
    lambda table_name, column: added.append((table_name, column)),
  )
  monkeypatch.setattr(
    revision.op,
    "create_check_constraint",
    lambda *args: constraints.append(args),
  )
  monkeypatch.setattr(revision.op, "execute", statements.append)

  revision.upgrade()

  assert [column.name for _, column in added] == list(
    revision.AUTHORIZATION_COLUMNS
  )
  assert constraints == [
    (
      revision.CONSTRAINT_NAME,
      revision.TABLE_NAME,
      revision.CONSTRAINT_SQL,
    )
  ]
  sql = "\n".join(str(statement) for statement in statements)
  assert "UPDATE auto_exit_plans SET auto_exit_authorized = false" in sql
  assert "auto_exit_authorization_device_session_id = NULL" in sql
  assert "UPDATE conditional_liquidation_orders" in sql
  assert "SET auto_exit_authorized = false" in sql


def test_model_rejects_legacy_boolean_without_exact_envelope() -> None:
  engine = create_engine("sqlite:///:memory:")
  Base.metadata.create_all(engine, tables=[AutoExitPlanRecord.__table__])
  try:
    with pytest.raises(IntegrityError):
      with engine.begin() as connection:
        connection.execute(
          AutoExitPlanRecord.__table__.insert().values(
            plan_id="legacy-bool-only",
            account_id="ACCOUNT-1",
            instrument_code="600000.SH",
            bucket="manual",
            source_type="MANUAL_POSITION",
            source_id="legacy-bool-only",
            enabled=True,
            status="ACTIVE",
            execution_mode="live",
            auto_exit_authorized=True,
            config_version=1,
            protected_volume=100,
            exited_volume=0,
            remaining_volume=100,
            entry_avg_price=10,
            plan_state={},
          )
        )
  finally:
    engine.dispose()
