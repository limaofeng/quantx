from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.auth import AuthDeviceSession, AuthUser
from quantx_infrastructure.models.ios_notifications import (
  IosNotificationEvent,
  IosNotificationOutbox,
  IosPushCategoryPreference,
  IosPushRegistration,
)
from sqlalchemy import create_engine, inspect

ROOT = Path(__file__).resolve().parents[2]
REVISION_PATH = (
  ROOT
  / "packages"
  / "infrastructure"
  / "alembic"
  / "versions"
  / "20260815_0017_ios_notifications.py"
)


def _revision():
  spec = importlib.util.spec_from_file_location(
    "quantx_test_ios_notification_revision",
    REVISION_PATH,
  )
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def _schema_inspector():
  engine = create_engine("sqlite:///:memory:")
  Base.metadata.create_all(
    engine,
    tables=[
      AuthUser.__table__,
      AuthDeviceSession.__table__,
      IosPushRegistration.__table__,
      IosPushCategoryPreference.__table__,
      IosNotificationEvent.__table__,
      IosNotificationOutbox.__table__,
    ],
  )
  return engine, inspect(engine)


def test_ios_notification_revision_follows_scoped_sessions_and_is_forward_only():
  revision = _revision()

  assert revision.down_revision == "20260815_0016"
  with pytest.raises(RuntimeError, match="downgrades"):
    revision.downgrade()


def test_ios_notification_model_schema_passes_fail_closed_validator():
  revision = _revision()
  engine, inspector = _schema_inspector()
  try:
    revision._validate_existing_schema(inspector)
  finally:
    engine.dispose()


def test_ios_notification_validator_rejects_missing_column():
  revision = _revision()
  engine, inspector = _schema_inspector()

  class MissingCiphertextInspector:
    def __getattr__(self, name):
      return getattr(inspector, name)

    def get_columns(self, table_name):
      columns = inspector.get_columns(table_name)
      if table_name == revision.REGISTRATIONS:
        return [
          column for column in columns if column["name"] != "token_ciphertext"
        ]
      return columns

  try:
    with pytest.raises(RuntimeError, match="token_ciphertext"):
      revision._validate_existing_schema(MissingCiphertextInspector())
  finally:
    engine.dispose()


def test_ios_notification_upgrade_rejects_partial_preexisting_schema(monkeypatch):
  revision = _revision()

  class PartialInspector:
    @staticmethod
    def get_table_names():
      return [revision.REGISTRATIONS]

  monkeypatch.setattr(revision, "inspect", lambda _bind: PartialInspector())
  monkeypatch.setattr(revision.op, "get_bind", lambda: object())
  with pytest.raises(RuntimeError, match="Partial iOS notification schema"):
    revision.upgrade()


def test_ios_notification_tables_are_post_baseline_owned():
  baseline_path = REVISION_PATH.with_name("20260729_0001_production_baseline.py")
  spec = importlib.util.spec_from_file_location(
    "quantx_test_ios_notification_baseline",
    baseline_path,
  )
  assert spec is not None and spec.loader is not None
  baseline = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(baseline)
  revision = _revision()

  assert revision.TABLES <= baseline.POST_BASELINE_TABLES
