from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord

ROOT = Path(__file__).resolve().parents[2]
REVISION_PATH = (
  ROOT
  / "packages"
  / "infrastructure"
  / "alembic"
  / "versions"
  / "20260829_0038_exit_plan_state_version.py"
)


def _load_revision():
  spec = importlib.util.spec_from_file_location(
    "quantx_test_exit_plan_state_version_revision", REVISION_PATH
  )
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def test_revision_adds_non_nullable_state_version_with_backfill_default(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  revision = _load_revision()
  added: list[tuple[str, object]] = []
  altered: list[tuple[str, str, dict]] = []
  checks: list[tuple[str, str, str]] = []
  monkeypatch.setattr(
    revision.op,
    "add_column",
    lambda table_name, column: added.append((table_name, column)),
  )
  monkeypatch.setattr(
    revision.op,
    "alter_column",
    lambda table_name, column_name, **kwargs: altered.append(
      (table_name, column_name, kwargs)
    ),
  )
  monkeypatch.setattr(
    revision.op,
    "create_check_constraint",
    lambda name, table_name, condition: checks.append(
      (name, table_name, condition)
    ),
  )

  revision.upgrade()

  assert revision.revision == "20260829_0038"
  assert revision.down_revision == "20260829_0037"
  assert len(added) == 1
  table_name, column = added[0]
  assert table_name == "auto_exit_plans"
  assert column.name == "state_version"
  assert column.nullable is False
  assert str(column.server_default.arg) == "1"
  assert len(altered) == 1
  assert altered[0][0:2] == ("auto_exit_plans", "state_version")
  assert altered[0][2]["nullable"] is False
  assert altered[0][2]["server_default"] is None
  assert isinstance(altered[0][2]["existing_type"], revision.sa.Integer)
  assert checks == [
    (
      "ck_auto_exit_plan_state_version",
      "auto_exit_plans",
      "state_version >= 1",
    )
  ]
  assert AutoExitPlanRecord.__table__.c.state_version.nullable is False
  assert AutoExitPlanRecord.__table__.c.state_version.default.arg == 1
  assert "ck_auto_exit_plan_state_version" in {
    constraint.name for constraint in AutoExitPlanRecord.__table__.constraints
  }
  with pytest.raises(RuntimeError, match="downgrades"):
    revision.downgrade()
