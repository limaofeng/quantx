from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_revision():
  path = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "infrastructure"
    / "alembic"
    / "versions"
    / "20260825_0035_t_trade_replay_preparation.py"
  )
  spec = importlib.util.spec_from_file_location("t_trade_replay_0035", path)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def test_replay_preparation_migration_adds_only_missing_projection_columns(
  monkeypatch,
) -> None:
  revision = _load_revision()

  class Inspector:
    @staticmethod
    def get_table_names():
      return ["t_trade_replay_projections"]

    @staticmethod
    def get_columns(_table_name):
      return [{"name": "run_id"}, {"name": "phase"}]

  added = []
  monkeypatch.setattr(revision.op, "get_bind", lambda: object())
  monkeypatch.setattr(revision, "inspect", lambda _bind: Inspector())
  monkeypatch.setattr(
    revision.op,
    "add_column",
    lambda table_name, column: added.append((table_name, column.name)),
  )

  revision.upgrade()

  assert revision.down_revision == "20260825_0034"
  assert added == [
    ("t_trade_replay_projections", "phase_progress_pct"),
    ("t_trade_replay_projections", "phase_message"),
    ("t_trade_replay_projections", "data_preparation"),
  ]
