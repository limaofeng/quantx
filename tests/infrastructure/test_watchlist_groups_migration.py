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
    / "20260823_0031_watchlist_groups.py"
  )
  spec = importlib.util.spec_from_file_location("watchlist_groups_revision", path)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module, path.read_text(encoding="utf-8")


def test_watchlist_groups_revision_follows_real_head_and_backfills_legacy_groups():
  revision, source = _load_revision()

  assert revision.revision == "20260823_0031"
  assert revision.down_revision == "20260823_0030"
  assert '"watchlist_groups"' in source
  assert '"watchlist_group_memberships"' in source
  assert "lower(btrim(group_name))" in source
  assert "md5(account_id || ':' || normalized_name)" in source
  assert 'op.drop_column("watchlist_items", "group_name")' in source
