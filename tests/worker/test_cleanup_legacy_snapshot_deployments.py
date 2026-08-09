from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_cleanup_module():
  script_path = (
    Path(__file__).parents[2]
    / "apps"
    / "worker"
    / "scripts"
    / "cleanup_legacy_snapshot_deployments.py"
  )
  spec = importlib.util.spec_from_file_location(
    "cleanup_legacy_snapshot_deployments",
    script_path,
  )
  assert spec and spec.loader
  module = importlib.util.module_from_spec(spec)
  spec.loader.exec_module(module)
  return module


def test_only_obsolete_snapshot_deployments_are_selected():
  module = _load_cleanup_module()
  deployments = [
    {
      "id": "old-market",
      "name": "daily-market-data-sync",
      "entrypoint": module.LEGACY_ENTRYPOINT,
    },
    {
      "id": "old-indicator",
      "name": "daily-indicator-snapshot",
      "entrypoint": module.LEGACY_ENTRYPOINT,
    },
    {
      "id": "new-market",
      "name": "daily-market-data-sync",
      "entrypoint": (
        "apps/worker/src/quantx_worker/prefector/flows/"
        "daily_market_data_sync_flow.py:daily_market_data_sync_flow"
      ),
    },
    {
      "id": "unrelated",
      "name": "financial-sync",
      "entrypoint": module.LEGACY_ENTRYPOINT,
    },
  ]

  assert module.legacy_deployment_ids(deployments) == [
    "old-market",
    "old-indicator",
  ]
