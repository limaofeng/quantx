from unittest.mock import AsyncMock

import pytest
import quantx_api.gqlapi.resolvers.prefect as prefect_module
from quantx_api.gqlapi.resolvers.prefect import PrefectResolver


async def test_flow_logs_read_latest_page_so_progress_does_not_freeze(monkeypatch):
  request = AsyncMock(side_effect=[_flow_run("run"), [], [
    {"timestamp": "2026-09-08T09:30:00Z", "level":20, "message":"new"},
    {"timestamp": "2026-09-08T09:29:00Z", "level":20, "message":"old"},
  ]])
  monkeypatch.setattr(prefect_module, "_request", request)
  result = await PrefectResolver.get_flow_run("run")
  assert request.await_args_list[-1].kwargs["payload"]["sort"] == "TIMESTAMP_DESC"
  assert request.await_args_list[-1].kwargs["payload"]["limit"] == 500
  assert [log.message for log in result.detailed_logs] == ["old", "new"]


def _singleton_deployment() -> dict:
  return {
    "id": "deployment-1",
    "concurrency_limit": None,
    "global_concurrency_limit": {"limit": 1},
    "concurrency_options": {"collision_strategy": "CANCEL_NEW"},
  }


def _flow_run(run_id: str, state: str = "Running") -> dict:
  return {
    "id": run_id,
    "name": f"run-{run_id}",
    "state": {"type": state.upper(), "name": state},
    "expected_start_time": "2026-09-01T00:31:42+00:00",
    "start_time": "2026-09-01T00:31:48+00:00",
    "parameters": {"periods": ["1d"]},
  }


@pytest.mark.asyncio
async def test_run_deployment_reuses_active_cancel_new_singleton(monkeypatch):
  request = AsyncMock(side_effect=[_singleton_deployment(), [_flow_run("active-run")]])
  monkeypatch.setattr(prefect_module, "_request", request)

  result = await PrefectResolver.run_deployment(
    "deployment-1",
    {"periods": ["1d"]},
  )

  assert result.id == "active-run"
  assert result.state == "Running"
  assert request.await_count == 2
  assert request.await_args_list[0].args == (
    "GET",
    "deployments/deployment-1",
  )
  filter_call = request.await_args_list[1]
  assert filter_call.args == ("POST", "flow_runs/filter")
  payload = filter_call.kwargs["payload"]
  assert payload["flow_runs"]["deployment_id"] == {"any_": ["deployment-1"]}
  assert payload["flow_runs"]["state"]["type"]["any_"] == [
    "SCHEDULED",
    "PENDING",
    "RUNNING",
    "PAUSED",
    "CANCELLING",
  ]
  assert payload["flow_runs"]["expected_start_time"]["before_"].endswith("+00:00")


@pytest.mark.asyncio
async def test_run_deployment_creates_when_singleton_has_no_active_run(
  monkeypatch,
):
  created = _flow_run("new-run", "Scheduled")
  request = AsyncMock(side_effect=[_singleton_deployment(), [], created])
  monkeypatch.setattr(prefect_module, "_request", request)

  result = await PrefectResolver.run_deployment(
    "deployment-1",
    {"periods": ["1d"]},
  )

  assert result.id == "new-run"
  assert request.await_args_list[2].args == (
    "POST",
    "deployments/deployment-1/create_flow_run",
  )
  assert request.await_args_list[2].kwargs["payload"] == {
    "parameters": {"periods": ["1d"]}
  }


@pytest.mark.asyncio
async def test_run_deployment_keeps_parallel_deployment_semantics(monkeypatch):
  deployment = {
    "id": "deployment-2",
    "concurrency_limit": 2,
    "concurrency_options": {"collision_strategy": "ENQUEUE"},
  }
  request = AsyncMock(side_effect=[deployment, _flow_run("parallel-run")])
  monkeypatch.setattr(prefect_module, "_request", request)

  result = await PrefectResolver.run_deployment("deployment-2")

  assert result.id == "parallel-run"
  assert request.await_count == 2
  assert request.await_args_list[1].args == (
    "POST",
    "deployments/deployment-2/create_flow_run",
  )
