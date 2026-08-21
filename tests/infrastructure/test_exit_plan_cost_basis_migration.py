from __future__ import annotations

import importlib.util
from pathlib import Path

from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord

ROOT = Path(__file__).resolve().parents[2]
REVISION_PATH = (
  ROOT
  / "packages"
  / "infrastructure"
  / "alembic"
  / "versions"
  / "20260821_0026_exit_plan_cost_basis.py"
)


def _revision():
  spec = importlib.util.spec_from_file_location(
    "quantx_test_exit_plan_cost_basis_revision",
    REVISION_PATH,
  )
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def test_exit_plan_cost_basis_revision_extends_current_head() -> None:
  revision = _revision()

  assert revision.revision == "20260821_0026"
  assert revision.down_revision == "20260821_0025"


def test_exit_plan_cost_basis_columns_are_part_of_the_model() -> None:
  columns = AutoExitPlanRecord.__table__.columns

  assert columns.cost_basis_mode.default.arg == "POSITION_AVERAGE_SNAPSHOT"
  assert callable(columns.cost_basis_snapshot.default.arg)
  assert columns.capacity_status.default.arg == "READY"
  assert columns.capacity_error.nullable
