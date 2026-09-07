from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_revision():
  path = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "infrastructure"
    / "alembic"
    / "versions"
    / "20260903_0048_p2_public_safety_groundwork.py"
  )
  spec = importlib.util.spec_from_file_location("quantx_p2_0048", path)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def test_p2_migration_preflights_before_schema_and_adds_strict_bindings(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  revision = _load_revision()
  calls: list[tuple[str, str]] = []

  monkeypatch.setattr(
    revision.op,
    "execute",
    lambda statement: calls.append(("execute", str(statement))),
  )
  for operation in (
    "add_column",
    "create_check_constraint",
    "create_index",
    "create_table",
    "create_foreign_key",
  ):
    monkeypatch.setattr(
      revision.op,
      operation,
      lambda *args, _operation=operation, **_kwargs: calls.append(
        (_operation, str(args[0]))
      ),
    )

  revision.upgrade()

  assert revision.revision == "20260903_0048"
  assert revision.down_revision == "20260903_0047"
  assert calls[0][0] == "execute"
  assert "P2_SHADOW_CONFLICT" in calls[0][1]
  assert "duplicate_outbox_client_order" in calls[0][1]
  assert "duplicate_owner_intent" in calls[0][1]
  assert "duplicate_exit_plan_source" in calls[0][1]
  batch_table_index = calls.index(
    ("create_table", "account_risk_increase_admission_batches")
  )
  intent_fk_index = calls.index(
    ("create_foreign_key", "fk_trade_intent_admission_batch")
  )
  assert batch_table_index < intent_fk_index
  function_call = next(
    call
    for call in calls
    if "quantx_enforce_risk_admission_item_binding" in call[1]
    and "CREATE OR REPLACE FUNCTION" in call[1]
  )
  drop_call = next(
    call
    for call in calls
    if "DROP TRIGGER IF EXISTS trg_risk_admission_item_binding" in call[1]
  )
  trigger_call = next(
    call
    for call in calls
    if "CREATE TRIGGER trg_risk_admission_item_binding" in call[1]
  )
  assert function_call[0] == drop_call[0] == trigger_call[0] == "execute"
  assert "RISK_ADMISSION_ITEM_INTENT_CONFLICT" in function_call[1]
  assert "CREATE TRIGGER" not in function_call[1]
  assert "DROP TRIGGER IF EXISTS trg_risk_admission_item_binding" in drop_call[1]
  assert "CREATE TRIGGER trg_risk_admission_item_binding" in trigger_call[1]
  assert "CREATE OR REPLACE FUNCTION" not in trigger_call[1]
  intent_function = next(
    call
    for call in calls
    if "quantx_enforce_risk_admission_intent_binding" in call[1]
    and "CREATE OR REPLACE FUNCTION" in call[1]
  )
  intent_trigger = next(
    call
    for call in calls
    if "CREATE CONSTRAINT TRIGGER trg_risk_admission_intent_binding" in call[1]
  )
  assert "RISK_ADMISSION_INTENT_ITEM_CONFLICT" in intent_function[1]
  assert "DEFERRABLE INITIALLY DEFERRED" in intent_trigger[1]
  assert "CREATE OR REPLACE FUNCTION" not in intent_trigger[1]


def test_p2_migration_refuses_unsafe_downgrade() -> None:
  with pytest.raises(RuntimeError, match="downgrades"):
    _load_revision().downgrade()
