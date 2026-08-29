"""Loopback client for sanitized account-safety observation history."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from quantx_infrastructure.config.settings import settings

HISTORY_RANGES = frozenset({"24h", "7d", "30d", "90d", "1y"})


def _unavailable(history_range: str) -> dict[str, Any]:
  return {
    "available": False,
    "range": history_range,
    "generatedAt": datetime.now(timezone.utc).isoformat(),
    "firstObservedAt": None,
    "lastObservedAt": None,
    "observerFresh": False,
    "bucketSeconds": 0,
    "checks": [],
    "incidents": [],
    "incidentsTruncated": False,
  }


async def fetch_account_safety_history(history_range: str) -> dict[str, Any]:
  """Return Monitor history without allowing it to affect trading decisions."""
  if history_range not in HISTORY_RANGES:
    raise ValueError(f"unsupported account safety history range: {history_range}")
  url = (
    f"{settings.monitor_internal_url.rstrip('/')}"
    "/monitor/internal/api/v1/account-safety/history"
  )
  try:
    async with httpx.AsyncClient(
      timeout=httpx.Timeout(3.0),
      follow_redirects=False,
      trust_env=False,
    ) as client:
      response = await client.get(url, params={"range": history_range})
    response.raise_for_status()
    payload = response.json()
  except (httpx.HTTPError, ValueError):
    return _unavailable(history_range)
  if not isinstance(payload, dict):
    return _unavailable(history_range)
  if not isinstance(payload.get("checks"), list) or not isinstance(
    payload.get("incidents"), list
  ):
    return _unavailable(history_range)
  if payload.get("range") != history_range:
    return _unavailable(history_range)
  return payload
