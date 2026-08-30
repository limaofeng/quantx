"""Sanitized, read-only public status API."""

from __future__ import annotations

from datetime import datetime
from time import time
from typing import Literal, Protocol

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse
from quantx_contracts import ACCOUNT_EXECUTION_SAFETY_CHECK_CODES

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
SAFETY_BUCKET_SECONDS: dict[str, int] = {
  "24h": 900,
  "7d": 3600,
  "30d": 14400,
  "90d": 43200,
  "1y": 172800,
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
    page: int = Query(default=1, ge=1, le=1000000),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
    as_of: datetime | None = Query(default=None, alias="asOf"),
    max_incident_id: int | None = Query(
      default=None, ge=0, le=9007199254740991, alias="maxIncidentId"
    ),
  ) -> dict[str, object]:
    if target_id is not None and target_id not in TARGET_BY_ID:
      raise HTTPException(status_code=404, detail="Unknown monitor target")
    if (as_of is None) != (max_incident_id is None):
      raise HTTPException(
        status_code=422, detail="asOf and maxIncidentId must be supplied together"
      )
    now = time()
    if as_of is not None:
      if as_of.tzinfo is None or as_of.timestamp() > now:
        raise HTTPException(
          status_code=422, detail="asOf must be a past timestamp with timezone"
        )
      now = as_of.timestamp()
    total, max_incident_id, rows = await runtime.storage.incidents(
      since=now - WINDOW_SECONDS[range],
      now=now,
      target_id=target_id,
      page=page,
      page_size=page_size,
      max_incident_id=max_incident_id,
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
    return {
      "range": range,
      "page": page,
      "pageSize": page_size,
      "total": total,
      "asOf": iso_timestamp(now),
      "maxIncidentId": max_incident_id,
      "incidents": public_rows,
    }

  @router.get("/monitor/internal/api/v1/account-safety/history")
  async def account_safety_history(
    range: HistoryRange = "30d",
  ) -> dict[str, object]:
    now = time()
    since = now - WINDOW_SECONDS[range]
    bucket_seconds = SAFETY_BUCKET_SECONDS[range]
    states = await runtime.storage.account_safety_states()
    (
      first_observed,
      last_observed,
    ) = await runtime.storage.account_safety_observation_bounds()
    incident_rows = await runtime.storage.account_safety_incidents(
      since=since,
      now=now,
      limit=201,
    )
    incidents_truncated = len(incident_rows) > 200
    incident_rows = incident_rows[:200]
    incident_counts: dict[str, int] = {}
    for row in incident_rows:
      code = str(row["check_code"])
      incident_counts[code] = incident_counts.get(code, 0) + 1

    checks: list[dict[str, object]] = []
    for code in ACCOUNT_EXECUTION_SAFETY_CHECK_CODES:
      points = await runtime.storage.account_safety_history(
        code,
        since=since,
        now=now,
        bucket_seconds=bucket_seconds,
        interval_seconds=runtime.settings.check_interval_seconds,
        use_rollups=range == "1y",
      )
      for point in points:
        point["start"] = iso_timestamp(point["start"])
        point["coveragePct"] = public_round(point["coveragePct"])
      state = states.get(code, {})
      coverage = (
        sum(float(point["coveragePct"]) for point in points) / len(points)
        if points
        else 0.0
      )
      checks.append(
        {
          "code": code,
          "currentStatus": str(state.get("status") or "unknown"),
          "checkedAt": iso_timestamp(state.get("checked_at")),
          "reasonCode": state.get("reason_code"),
          "publicMessage": str(state.get("public_message") or ""),
          "coveragePct": public_round(coverage),
          "incidentCount": incident_counts.get(code, 0),
          "points": points,
        }
      )

    freshness_seconds = max(
      90.0,
      float(runtime.settings.check_interval_seconds) * 3,
    )
    observer_fresh = bool(
      last_observed is not None and now - last_observed <= freshness_seconds
    )
    public_incidents: list[dict[str, object]] = []
    for row in incident_rows:
      resolved_at = row.get("resolved_at")
      code = str(row["check_code"])
      state = states.get(code, {})
      active = resolved_at is None
      observation_fresh = bool(
        active and observer_fresh and str(state.get("status") or "unknown") != "unknown"
      )
      public_incidents.append(
        {
          "id": int(row["id"]),
          "checkCode": code,
          "openedAt": iso_timestamp(row["opened_at"]),
          "resolvedAt": iso_timestamp(resolved_at),
          "lastConfirmedFailedAt": iso_timestamp(row.get("last_confirmed_failed_at")),
          "active": active,
          "observationFresh": observation_fresh,
          "openedReasonCode": row.get("opened_reason_code"),
          "lastReasonCode": row.get("last_reason_code"),
          "openedMessage": str(row.get("opened_message") or ""),
          "lastMessage": str(row.get("last_message") or ""),
        }
      )
    return {
      "available": True,
      "range": range,
      "generatedAt": iso_timestamp(now),
      "firstObservedAt": iso_timestamp(first_observed),
      "lastObservedAt": iso_timestamp(last_observed),
      "observerFresh": observer_fresh,
      "bucketSeconds": bucket_seconds,
      "checks": checks,
      "incidents": public_incidents,
      "incidentsTruncated": incidents_truncated,
    }

  return router
