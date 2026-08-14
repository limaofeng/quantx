from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.ios_notifications import (
  IosBusinessNotificationReceipt,
)
from sqlalchemy import create_engine, inspect

ROOT = Path(__file__).resolve().parents[2]
REVISION_PATH = (
  ROOT
  / "packages"
  / "infrastructure"
  / "alembic"
  / "versions"
  / "20260815_0019_ios_business_notification_receipts.py"
)


def _revision(module_name: str = "quantx_test_ios_business_notification_revision"):
  spec = importlib.util.spec_from_file_location(module_name, REVISION_PATH)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def _schema_inspector():
  engine = create_engine("sqlite:///:memory:")
  Base.metadata.create_all(
    engine,
    tables=[IosBusinessNotificationReceipt.__table__],
  )
  return engine, inspect(engine)


def test_ios_business_notification_revision_is_forward_only() -> None:
  revision = _revision()

  assert revision.down_revision == "20260815_0018"
  with pytest.raises(RuntimeError, match="downgrades"):
    revision.downgrade()


def test_ios_business_notification_model_passes_schema_validator() -> None:
  revision = _revision("quantx_test_ios_business_notification_schema")
  engine, inspector = _schema_inspector()
  try:
    revision._validate_existing_schema(inspector)
  finally:
    engine.dispose()


def test_ios_business_notification_validator_rejects_missing_hmac() -> None:
  revision = _revision("quantx_test_ios_business_notification_partial")
  engine, inspector = _schema_inspector()

  class MissingHmacInspector:
    def __getattr__(self, name):
      return getattr(inspector, name)

    def get_columns(self, table_name):
      return [
        column
        for column in inspector.get_columns(table_name)
        if column["name"] != "source_event_key_hash"
      ]

  try:
    with pytest.raises(RuntimeError, match="source_event_key_hash"):
      revision._validate_existing_schema(MissingHmacInspector())
  finally:
    engine.dispose()


def test_ios_business_notification_table_is_post_baseline_owned() -> None:
  baseline_path = REVISION_PATH.with_name("20260729_0001_production_baseline.py")
  spec = importlib.util.spec_from_file_location(
    "quantx_test_ios_business_notification_baseline",
    baseline_path,
  )
  assert spec is not None and spec.loader is not None
  baseline = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(baseline)

  assert (
    "ios_business_notification_receipts" in baseline.POST_BASELINE_TABLES
  )
