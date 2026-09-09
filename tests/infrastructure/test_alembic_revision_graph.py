"""Load the real revision graph before any database deployment is attempted."""

import warnings
from pathlib import Path

from alembic.script import ScriptDirectory


def test_revision_graph_has_one_head_and_preserves_both_feature_chains():
  scripts = ScriptDirectory(
    str(Path(__file__).resolve().parents[2] / "packages/infrastructure/alembic")
  )
  # Duplicate IDs otherwise only warn and can silently hide a migration file.
  with warnings.catch_warnings():
    warnings.simplefilter("error", UserWarning)
    heads = scripts.get_heads()
    revisions = list(scripts.walk_revisions())
  assert len(heads) == 1
  ids = [revision.revision for revision in revisions]
  expected = [
    "20260909_0067",  # Native collection permits
    "20260909_0066",  # Offline demand catalog
    "20260909_0065",  # Historical worker lease
    "20260909_0064",  # Ingestion checkpoints
    "20260909_0063",  # History download policy
    "20260909_0062",  # T entry confirmation
    "20260909_0061",  # T LIVE allocation
    "20260909_0060",  # T order identity
    "20260908_0059",
  ]
  assert [identity for identity in ids if identity in expected] == expected
