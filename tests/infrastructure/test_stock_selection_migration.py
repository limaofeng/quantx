from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _revision():
  path = (
    Path(__file__).resolve().parents[2]
    / "packages/infrastructure/alembic/versions/20260901_0043_stock_selection_probability.py"
  )
  spec = importlib.util.spec_from_file_location("stock_selection_migration", path)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def test_stock_selection_migration_is_additive_and_seeds_one_active_rule(
  monkeypatch,
) -> None:
  revision = _revision()
  tables: list[str] = []
  inserted: list[dict] = []
  monkeypatch.setattr(
    revision.op,
    "create_table",
    lambda name, *_columns, **_kwargs: tables.append(name),
  )
  monkeypatch.setattr(revision.op, "create_index", lambda *_args, **_kwargs: None)
  monkeypatch.setattr(revision.op, "create_foreign_key", lambda *_args, **_kwargs: None)
  monkeypatch.setattr(
    revision.op,
    "bulk_insert",
    lambda _table, rows: inserted.extend(rows),
  )

  revision.upgrade()

  assert revision.down_revision == "20260901_0042"
  assert tables == [
    "stock_selection_model_versions",
    "stock_prediction_runs",
    "stock_predictions",
    "stock_candidate_rule_versions",
    "stock_candidates",
  ]
  assert inserted == [
    {
      "rule_version": "next-day-selection-candidate-v1",
      "status": "ACTIVE",
      "minimum_probability": 0.6,
      "minimum_confidence": 0.6,
      "minimum_ood_fit": 0.8,
      "minimum_factor_completeness": 0.9,
      "minimum_valid_history": 252,
      "level_a_size": 20,
      "level_b_size": 30,
      "rules": {
        "exclude_st": True,
        "exclude_suspended": True,
        "exclude_delisting_risk": True,
        "critical_ood_blocks": True,
        "confidence_formula": "geometric-v1",
        "calibration_support_full_samples": 1000,
        "sort": ["probability_desc", "code_asc"],
      },
      "state_version": 1,
    }
  ]
  with pytest.raises(RuntimeError, match="downgrades"):
    revision.downgrade()
