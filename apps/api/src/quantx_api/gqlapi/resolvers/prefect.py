"""Prefect GraphQL facade implemented through the Prefect HTTP API."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from quantx_infrastructure.config.settings import settings

from ..types import (
  DeploymentFlowRun,
  FlowRun,
  LogLine,
  OperationResult,
  PaginatedFlowRuns,
  TaskRun,
)


def _api_url(path: str) -> str:
  return f"{settings.prefect_api_url.rstrip('/')}/{path.lstrip('/')}"


def _datetime(value: Any) -> Optional[datetime]:
  if isinstance(value, datetime):
    return value
  if isinstance(value, str) and value:
    try:
      return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
      return None
  return None


def _state_name(value: dict[str, Any]) -> str:
  state = value.get("state") or {}
  if isinstance(state, dict):
    return str(state.get("name") or state.get("type") or "Unknown")
  return str(state or "Unknown")


def _flow_run(value: dict[str, Any]) -> FlowRun:
  parameters = value.get("parameters")
  return FlowRun(
    id=str(value.get("id", "")),
    flow_name=str(value.get("name") or value.get("flow_name") or ""),
    state=_state_name(value),
    expected_start_time=_datetime(value.get("expected_start_time")),
    created=_datetime(value.get("created")),
    started_at=_datetime(value.get("start_time") or value.get("started_at")),
    finished_at=_datetime(value.get("end_time") or value.get("finished_at")),
    total_run_time=(
      float(value["total_run_time"])
      if value.get("total_run_time") is not None
      else None
    ),
    parameters=json.dumps(parameters, ensure_ascii=False) if parameters else None,
    logs=[],
    task_runs=[],
    detailed_logs=[],
  )


def _deployment(value: dict[str, Any]) -> DeploymentFlowRun:
  return DeploymentFlowRun(
    id=str(value.get("id", "")),
    name=str(value.get("name", "")),
    flow_name=str(value.get("flow_name", "")),
    description=value.get("description"),
    work_pool_name=value.get("work_pool_name"),
    work_queue_name=str(value.get("work_queue_name") or ""),
    is_schedule_active=not bool(value.get("paused", False)),
    next_run_time=_datetime(value.get("next_run_time")),
    last_run_time=_datetime(value.get("last_run_time")),
    status=value.get("status"),
    active_run_id=value.get("active_run_id"),
    active_run_status=value.get("active_run_status"),
    is_stale=bool(value.get("is_stale", False)),
    stale_reason=value.get("stale_reason"),
    latest_activity_time=_datetime(value.get("latest_activity_time")),
    created=_datetime(value.get("created")),
    updated=_datetime(value.get("updated")),
  )


async def _request(
  method: str,
  path: str,
  *,
  payload: Optional[dict[str, Any]] = None,
) -> Any:
  async with httpx.AsyncClient(timeout=10.0) as client:
    response = await client.request(method, _api_url(path), json=payload)
  response.raise_for_status()
  return response.json() if response.content else None


class PrefectResolver:
  @staticmethod
  async def run_deployment(
    id: str,
    parameters: Optional[Dict[str, Any]] = None,
  ) -> FlowRun:
    result = await _request(
      "POST",
      f"deployments/{id}/create_flow_run",
      payload={"parameters": parameters or {}},
    )
    return _flow_run(result)

  @staticmethod
  async def cancel_flow_run(run_id: str) -> OperationResult:
    await _request(
      "POST",
      f"flow_runs/{run_id}/set_state",
      payload={
        "state": {
          "type": "CANCELLED",
          "name": "Cancelled",
          "message": "Cancelled from QuantX API",
        },
        "force": True,
      },
    )
    return OperationResult(
      success=True,
      message=f"Flow run {run_id} cancellation requested",
      data=run_id,
    )

  @staticmethod
  async def retry_flow_run(run_id: str) -> FlowRun:
    await _request(
      "POST",
      f"flow_runs/{run_id}/set_state",
      payload={
        "state": {"type": "SCHEDULED", "name": "AwaitingRetry"},
        "force": True,
      },
    )
    result = await _request("GET", f"flow_runs/{run_id}")
    return _flow_run(result)

  @staticmethod
  async def set_deployment_schedule_active(
    id: str,
    active: bool,
  ) -> DeploymentFlowRun:
    await _request("PATCH", f"deployments/{id}", payload={"paused": not active})
    result = await _request("GET", f"deployments/{id}")
    return _deployment(result)

  @staticmethod
  async def get_flow_run(run_id: str) -> Optional[FlowRun]:
    try:
      result = await _request("GET", f"flow_runs/{run_id}")
    except httpx.HTTPStatusError as exc:
      if exc.response.status_code == 404:
        return None
      raise
    flow_run = _flow_run(result)
    task_values = await _request(
      "POST",
      "task_runs/filter",
      payload={"task_runs": {"flow_run_id": {"any_": [run_id]}}},
    )
    flow_run.task_runs = [
      TaskRun(
        id=str(item.get("id", "")),
        name=str(item.get("name", "")),
        state=_state_name(item),
        started_at=_datetime(item.get("start_time")),
        finished_at=_datetime(item.get("end_time")),
        total_run_time=item.get("total_run_time"),
        task_inputs=json.dumps(item.get("task_inputs"), ensure_ascii=False),
      )
      for item in task_values
    ]
    log_values = await _request(
      "POST",
      "logs/filter",
      payload={"logs": {"flow_run_id": {"any_": [run_id]}}},
    )
    flow_run.detailed_logs = [
      LogLine(
        time=_datetime(item.get("timestamp")) or datetime.min,
        level=int(item.get("level", 0)),
        message=str(item.get("message", "")),
      )
      for item in log_values
    ]
    return flow_run

  @staticmethod
  async def list_flow_runs(
    flow_name: Optional[str] = None,
    flow_id: Optional[str] = None,
    deployment_id: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
  ) -> PaginatedFlowRuns:
    filters: dict[str, Any] = {}
    if flow_name:
      filters["flows"] = {"name": {"any_": [flow_name]}}
    if flow_id:
      filters["flows"] = {"id": {"any_": [flow_id]}}
    if deployment_id:
      filters["flow_runs"] = {"deployment_id": {"any_": [deployment_id]}}
    body = {**filters, "limit": limit, "offset": offset, "sort": "START_TIME_DESC"}
    results = await _request("POST", "flow_runs/filter", payload=body)
    count_body = {key: value for key, value in filters.items()}
    total = await _request("POST", "flow_runs/count", payload=count_body)
    return PaginatedFlowRuns(
      total=int(total or 0),
      items=[_flow_run(item) for item in results],
    )

  @staticmethod
  async def list_deployments(
    limit: int = 20,
    offset: int = 0,
  ) -> List[DeploymentFlowRun]:
    results = await _request(
      "POST",
      "deployments/filter",
      payload={"limit": limit, "offset": offset, "sort": "UPDATED_DESC"},
    )
    return [_deployment(item) for item in results]

  @staticmethod
  async def get_deployment_by_name(name: str) -> Optional[DeploymentFlowRun]:
    results = await _request(
      "POST",
      "deployments/filter",
      payload={"deployments": {"name": {"any_": [name]}}, "limit": 1},
    )
    return _deployment(results[0]) if results else None

  @staticmethod
  async def get_deployment_by_id(id: str) -> Optional[DeploymentFlowRun]:
    try:
      result = await _request("GET", f"deployments/{id}")
    except httpx.HTTPStatusError as exc:
      if exc.response.status_code == 404:
        return None
      raise
    return _deployment(result)
