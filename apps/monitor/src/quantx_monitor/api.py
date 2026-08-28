"""Sanitized, read-only public status API."""

from __future__ import annotations

from time import time
from typing import Literal, Protocol

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from .config import MonitorSettings
from .models import MonitorStatus, iso_timestamp, public_round
from .scheduler import MonitorScheduler
from .storage import MonitorStorage
from .targets import TARGET_BY_ID, TARGETS

Window = Literal["24h", "7d", "30d"]
HistoryRange = Literal["24h", "7d", "30d", "90d", "1y"]

WINDOW_SECONDS: dict[str, int] = {
  "24h": 86400,
  "7d": 7 * 86400,
  "30d": 30 * 86400,
  "90d": 90 * 86400,
  "1y": 365 * 86400,
}
BUCKET_SECONDS: dict[str, int] = {
  "24h": 60,
  "7d": 300,
  "30d": 1800,
  "90d": 3600,
  "1y": 21600,
}


class RuntimeView(Protocol):
  settings: MonitorSettings
  storage: MonitorStorage
  scheduler: MonitorScheduler


def _status_rank(value: str) -> int:
  return {
    MonitorStatus.HEALTHY.value: 0,
    MonitorStatus.DISABLED.value: 0,
    MonitorStatus.DEGRADED.value: 1,
    MonitorStatus.UNKNOWN.value: 1,
    MonitorStatus.UNAVAILABLE.value: 2,
  }.get(value, 1)


def _aggregate_status(items: list[dict[str, object]]) -> str:
  required = [
    item
    for item in items
    if not bool(item["optional"])
    and str(item["status"]) != MonitorStatus.DISABLED.value
  ]
  if not required:
    return MonitorStatus.UNKNOWN.value
  return str(
    max(required, key=lambda item: _status_rank(str(item["status"])))["status"]
  )


def build_router(runtime: RuntimeView) -> APIRouter:
  router = APIRouter()

  @router.get("/monitor/health/live")
  async def health_live() -> dict[str, str]:
    return {"status": "alive", "component": "monitor"}

  @router.get("/monitor/health/ready")
  async def health_ready() -> JSONResponse:
    ready = (
      runtime.storage.is_open
      and runtime.scheduler.running
      and runtime.scheduler.last_cycle_at is not None
      and runtime.scheduler.last_persist_error is None
    )
    return JSONResponse(
      status_code=200 if ready else 503,
      content={
        "status": "ready" if ready else "not_ready",
        "component": "monitor",
      },
    )

  @router.get("/monitor/api/v1/summary")
  async def summary(window: Window = "24h") -> dict[str, object]:
    now = time()
    seconds = WINDOW_SECONDS[window]
    states = await runtime.storage.target_states()
    metrics = await runtime.storage.window_metrics(
      since=now - seconds,
      now=now,
      interval_seconds=runtime.settings.check_interval_seconds,
    )
    targets: list[dict[str, object]] = []
    for definition in TARGETS:
      state = states.get(definition.target_id, {})
      target_metrics = metrics.get(definition.target_id, {})
      targets.append(
        {
          "id": definition.target_id,
          "name": definition.name,
          "group": definition.group.value,
          "optional": definition.optional,
          "probeKind": definition.probe_kind.value,
          "status": str(state.get("effective_status") or MonitorStatus.UNKNOWN.value),
          "checkedAt": iso_timestamp(state.get("checked_at")),
          "lastSuccessAt": iso_timestamp(state.get("last_success_at")),
          "latencyMs": public_round(state.get("latency_ms")),
          "reasonCode": state.get("reason_code"),
          "availabilityPct": public_round(target_metrics.get("availabilityPct")),
          "healthyPct": public_round(target_metrics.get("healthyPct")),
          "coveragePct": public_round(target_metrics.get("coveragePct")),
          "latencyP50Ms": public_round(target_metrics.get("latencyP50Ms")),
          "latencyP95Ms": public_round(target_metrics.get("latencyP95Ms")),
          "sampleCount": int(target_metrics.get("sampleCount") or 0),
          "activeIncident": state.get("active_incident_id") is not None,
        }
      )
    groups = []
    for group, name in (
      ("external_dependency", "外部依赖"),
      ("quantx_runtime", "QuantX 运行组件"),
    ):
      members = [item for item in targets if item["group"] == group]
      groups.append(
        {
          "id": group,
          "name": name,
          "status": _aggregate_status(members),
          "targetIds": [item["id"] for item in members],
        }
      )
    overall_items = [item for item in targets if item["group"] == "external_dependency"]
    runtime_items = [item for item in targets if item["group"] == "quantx_runtime"]
    overall = _aggregate_status(
      [
        *overall_items,
        *[item for item in runtime_items if item["id"] in {"web-entry", "api-public"}],
      ]
    )
    return {
      "generatedAt": iso_timestamp(now),
      "lastCycleAt": iso_timestamp(runtime.scheduler.last_cycle_at),
      "window": window,
      "checkIntervalSeconds": runtime.settings.check_interval_seconds,
      "overallStatus": overall,
      "groups": groups,
      "targets": targets,
    }

  @router.get("/monitor/api/v1/targets/{target_id}/history")
  async def history(
    target_id: str,
    range: HistoryRange = "24h",
  ) -> dict[str, object]:
    if target_id not in TARGET_BY_ID:
      raise HTTPException(status_code=404, detail="Unknown monitor target")
    now = time()
    seconds = WINDOW_SECONDS[range]
    points = await runtime.storage.history(
      target_id,
      since=now - seconds,
      now=now,
      bucket_seconds=BUCKET_SECONDS[range],
      use_rollups=range == "1y",
    )
    for point in points:
      point["start"] = iso_timestamp(point["start"])
      for field in ("latencyMaxMs", "latencyP50Ms", "latencyP95Ms"):
        point[field] = public_round(point.get(field))
    return {
      "target": {
        "id": target_id,
        "name": TARGET_BY_ID[target_id].name,
      },
      "range": range,
      "bucketSeconds": BUCKET_SECONDS[range],
      "points": points[:2500],
    }

  @router.get("/monitor/api/v1/incidents")
  async def incidents(
    range: HistoryRange = "30d",
    target_id: str | None = Query(default=None, alias="targetId"),
    limit: int = Query(default=200, ge=1, le=200),
  ) -> dict[str, object]:
    if target_id is not None and target_id not in TARGET_BY_ID:
      raise HTTPException(status_code=404, detail="Unknown monitor target")
    now = time()
    rows = await runtime.storage.incidents(
      since=now - WINDOW_SECONDS[range],
      target_id=target_id,
      limit=limit,
    )
    public_rows = []
    for row in rows:
      definition = TARGET_BY_ID[str(row["target_id"])]
      resolved = row.get("resolved_at")
      public_rows.append(
        {
          "id": int(row["id"]),
          "targetId": definition.target_id,
          "targetName": definition.name,
          "openedAt": iso_timestamp(row["opened_at"]),
          "resolvedAt": iso_timestamp(resolved),
          "active": resolved is None,
          "reasonCode": row.get("last_reason_code") or row.get("opened_reason_code"),
        }
      )
    return {"range": range, "incidents": public_rows}

  return router
