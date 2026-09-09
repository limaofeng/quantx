"""Public schema keeps native maintenance permission and service contracts."""

from unittest.mock import AsyncMock

import pytest
import strawberry
from quantx_api.gqlapi.operation_policy import operation_policy
from quantx_api.gqlapi.schemas import t_assistant_legacy_schema as legacy

from tests.api.unit.gqlapi.test_trade_approval_challenge import _principal

SCHEMA = strawberry.Schema(
  query=legacy.TAssistantLegacyQuery, mutation=legacy.TAssistantLegacyMutation
)


@pytest.mark.asyncio
async def test_public_maintenance_contract(monkeypatch):
  principal = _principal(authorized_account_ids=("account-1",))

  class Session:
    def begin(self):
      return self

    async def __aenter__(self):
      return self

    async def __aexit__(self, *args):
      pass

  monkeypatch.setattr(legacy, "AsyncSessionLocal", Session)
  prepare = AsyncMock(return_value="command-1")
  monkeypatch.setattr(legacy, "enqueue_legacy_inventory", prepare)
  result = await SCHEMA.execute(
    'mutation {prepareTAssistantLegacyInventory(requestId:"request-1",request:{accountId:"account-1",configId:"head",runId:"run",expectedHeadVersion:1}){success engineCommandId}}',
    context_value={"principal": principal},
  )
  assert not result.errors
  assert result.data["prepareTAssistantLegacyInventory"] == {
    "success": True,
    "engineCommandId": "command-1",
  }
  assert prepare.call_args.kwargs["request"] == dict(
    account_id="account-1", config_id="head", run_id="run", expected_head_version=1
  )
  confirm = AsyncMock(return_value="command-2")
  monkeypatch.setattr(legacy, "consume_drain_confirmation", confirm)
  result = await SCHEMA.execute(
    'mutation {confirmTAssistantLegacyDrain(challengeId:"challenge",confirmationToken:"token"){success code engineCommandId}}',
    context_value={"principal": principal},
  )
  assert (
    not result.errors
    and result.data["confirmTAssistantLegacyDrain"]["code"] == "DRAIN_QUEUED"
  )
  confirm.side_effect = ValueError("private implementation detail")
  result = await SCHEMA.execute(
    'mutation {confirmTAssistantLegacyDrain(challengeId:"challenge",confirmationToken:"token"){success code message}}',
    context_value={"principal": principal},
  )
  assert (
    not result.errors and not result.data["confirmTAssistantLegacyDrain"]["success"]
  )
  assert "private" not in str(result.data)
  recover = AsyncMock(
    return_value={
      "challenge_id": "challenge",
      "status": "EXPIRED",
      "engine_command_id": None,
      "request": {"account_id": "account-1"},
    }
  )
  monkeypatch.setattr(legacy, "read_legacy_confirmation_status", recover)
  result = await SCHEMA.execute(
    'query {tAssistantLegacyConfirmationStatus(challengeId:"challenge"){challengeId status engineCommandId request}}',
    context_value={"principal": principal},
  )
  assert not result.errors
  assert result.data["tAssistantLegacyConfirmationStatus"]["status"] == "EXPIRED"
  assert recover.call_args.kwargs["principal"] == principal
  for name in (
    "prepareTAssistantLegacyInventory",
    "previewTAssistantLegacyDrain",
    "confirmTAssistantLegacyDrain",
    "tAssistantLegacyMaintenanceOperation",
    "tAssistantLegacyConfirmationStatus",
    "tAssistantLegacyMaintenanceSource",
  ):
    policy = operation_policy(
      "Query" if name.startswith("tAssistant") else "Mutation", name
    )
    assert policy.required_permissions == ("t-trade:control", "trade:approve")
    assert policy.audiences == ("native",)
