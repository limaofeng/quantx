"""Translate API-owned runtime semantics without reproducing business logic."""

from __future__ import annotations

from typing import Any

import httpx

from ..models import MonitorStatus, ProbeResult, utc_now
from .base import failure_reason

COMPONENT_TARGETS = {
  "engine": "engine",
  "worker": "worker",
  "qmtAgent": "qmt-agent",
  "marketData": "market-data",
  "aiRuntime": "ai-runtime",
}

HEALTHY = frozenset({"ready", "alive", "healthy"})
DEGRADED = frozenset({"starting", "reconciling", "syncing", "stale"})
DISABLED = frozenset({"disabled", "unconfigured"})


def normalize_component_status(value: Any) -> MonitorStatus:
  normalized = str(value or "").strip().lower()
  if normalized in HEALTHY:
    return MonitorStatus.HEALTHY
  if normalized in DEGRADED:
    return MonitorStatus.DEGRADED
  if normalized in DISABLED:
    return MonitorStatus.DISABLED
  if not normalized:
    return MonitorStatus.UNKNOWN
  return MonitorStatus.UNAVAILABLE


class RuntimeSnapshotProbe:
  def __init__(self, url: str, timeout_seconds: float) -> None:
    self.url = url
    self.timeout_seconds = timeout_seconds

  async def run(self, client: httpx.AsyncClient) -> list[ProbeResult]:
    checked_at = utc_now()
    try:
      response = await client.get(self.url, timeout=self.timeout_seconds)
      if not response.is_success:
        raise RuntimeError("runtime snapshot returned an unsuccessful status")
      payload = response.json()
      components = payload.get("components") if isinstance(payload, dict) else None
      if not isinstance(components, dict):
        raise ValueError("runtime snapshot did not contain components")
    except Exception as exc:
      reason = failure_reason(exc)
      return [
        ProbeResult(
          target_id=target_id,
          checked_at=checked_at,
          observed_status=MonitorStatus.UNAVAILABLE,
          reason_code=reason,
        )
        for target_id in COMPONENT_TARGETS.values()
      ]

    results: list[ProbeResult] = []
    for component_name, target_id in COMPONENT_TARGETS.items():
      component = components.get(component_name)
      status = (
        normalize_component_status(component.get("status"))
        if isinstance(component, dict)
        else MonitorStatus.UNKNOWN
      )
      reason = None
      if status == MonitorStatus.UNAVAILABLE:
        reason = "DEPENDENCY_NOT_READY"
      elif status == MonitorStatus.UNKNOWN:
        reason = "SNAPSHOT_UNAVAILABLE"
      results.append(
        ProbeResult(
          target_id=target_id,
          checked_at=checked_at,
          observed_status=status,
          reason_code=reason,
        )
      )
    return results
