from __future__ import annotations

import importlib.util
from pathlib import Path

from quantx_infrastructure.models.order import Order

ROOT = Path(__file__).resolve().parents[2]
REVISION_PATH = (
  ROOT
  / "packages"
  / "infrastructure"
  / "alembic"
  / "versions"
  / "20260821_0027_exit_plan_order_lookup.py"
)


def _revision():
  spec = importlib.util.spec_from_file_location(
    "quantx_test_exit_plan_order_lookup_revision",
    REVISION_PATH,
  )
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def test_exit_plan_order_lookup_revision_extends_current_head() -> None:
  revision = _revision()

  assert revision.revision == "20260821_0027"
  assert revision.down_revision == "20260821_0026"


def test_exit_plan_order_lookup_index_is_part_of_the_model() -> None:
  index = next(
    value
    for value in Order.__table__.indexes
    if value.name == "ix_orders_exit_plan_cost_basis"
  )

  assert index.dialect_options["postgresql"]["where"] is not None
  assert [expression.name for expression in list(index.expressions)[:3]] == [
    "account_id",
    "stock_code",
    "order_type",
  ]
