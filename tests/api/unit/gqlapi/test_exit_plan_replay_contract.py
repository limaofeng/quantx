from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_api.gqlapi.operation_policy import operation_policy
from quantx_api.gqlapi.resolvers.liquidation import LiquidationResolver
from quantx_api.gqlapi.schema import schema
from quantx_api.gqlapi.types.liquidation_types import (
  ExitPlanReplayOriginInput,
  ExitPlanReplayStartInput,
)
from quantx_infrastructure.services.engine_command_service import (
  engine_command_service,
)


def test_exit_plan_replay_graphql_contract_is_read_only_and_non_trading() -> None:
  for field_name in (
    "exitPlanReplayPreparation",
    "exitPlanReplay",
    "exitPlanReplayHistory",
    "exitPlanReplayEvents",
  ):
    policy = operation_policy("Query", field_name)
    assert policy.required_permissions == ("strategy:read",)
    assert policy.risk == "READ"

  for field_name in ("startExitPlanReplay", "cancelExitPlanReplay"):
    policy = operation_policy("Mutation", field_name)
    assert policy.required_permissions == ("strategy:write",)
    assert policy.risk == "NON_TRADING_WRITE"

  assert "exitPlanReplay" in schema.as_str()
  assert "exitPlanReplayUpdates" in schema.as_str()
  mutation_result = schema.as_str().split(
    "type ExitPlanReplayMutationResult", maxsplit=1
  )[1].split("}", maxsplit=1)[0]
  assert "runId: String" in mutation_result


@pytest.mark.asyncio
async def test_queued_replay_exposes_its_deterministic_run_id(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  request = AsyncMock(
    return_value=SimpleNamespace(
      status="PENDING",
      message_id="12345678-1234-1234-1234-123456789abc",
      result=None,
      error=None,
    )
  )
  monkeypatch.setattr(engine_command_service, "request", request)

  result = await LiquidationResolver.start_exit_plan_replay(
    ExitPlanReplayStartInput(
      account_id="300000000001",
      idempotency_key="start-1",
      start_time=datetime(2026, 8, 3, 9, 30),
      end_time=datetime(2026, 8, 7, 15, 0),
      origin=ExitPlanReplayOriginInput(
        mode="MANUAL_SNAPSHOT",
        activation_time=datetime(2026, 8, 1, 15, 0),
        volume=100,
        unit_cost=10.0,
      ),
      draft_template={"instrument_code": "000001.SZ", "rules": []},
    ),
    "300000000001",
  )

  assert result.success is True
  assert result.code == "QUEUED"
  assert result.run_id == "12345678-1234-1234-1234-123456789abc"
