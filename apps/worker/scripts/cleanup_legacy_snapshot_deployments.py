"""Remove superseded snapshot deployments before deploying the current flows."""

from __future__ import annotations

import os
from typing import Any

import httpx

TARGET_DEPLOYMENT_NAMES = {
  "daily-market-data-sync",
  "daily-indicator-snapshot",
}
LEGACY_ENTRYPOINT = (
  "apps/worker/src/quantx_worker/prefector/flows/"
  "durable_agent_flows.py:daily_market_data_request_flow"
)


def legacy_deployment_ids(deployments: list[dict[str, Any]]) -> list[str]:
  """Return only the obsolete deployment ids, leaving history and new flows alone."""
  return [
    str(deployment["id"])
    for deployment in deployments
    if deployment.get("name") in TARGET_DEPLOYMENT_NAMES
    and deployment.get("entrypoint") == LEGACY_ENTRYPOINT
    and deployment.get("id")
  ]


def cleanup_legacy_deployments(api_url: str) -> list[str]:
  """Delete obsolete schedules so same-name deployment lookups stay unambiguous."""
  base_url = api_url.rstrip("/")
  with httpx.Client(base_url=base_url, timeout=30.0) as client:
    response = client.post("/deployments/filter", json={"limit": 200})
    response.raise_for_status()
    deployment_ids = legacy_deployment_ids(response.json())
    for deployment_id in deployment_ids:
      delete_response = client.delete(f"/deployments/{deployment_id}")
      delete_response.raise_for_status()
  return deployment_ids


def main() -> int:
  api_url = os.environ.get(
    "PREFECT_API_URL", "http://192.168.5.6:30420/api"
  )
  deleted_ids = cleanup_legacy_deployments(api_url)
  if deleted_ids:
    print(f"Removed {len(deleted_ids)} legacy snapshot deployment(s).")
  else:
    print("No legacy snapshot deployments found.")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
