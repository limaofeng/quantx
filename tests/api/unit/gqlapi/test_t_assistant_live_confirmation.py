"""GraphQL confirmation preserves typed owner, durable retry, and account scope."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import strawberry
from quantx_api.gqlapi import trade_approval as api
from quantx_api.gqlapi.schemas import t_assistant_live_schema as live
from quantx_api.gqlapi.security import required_permissions
from quantx_infrastructure.core.utils.time_utils import to_shanghai
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import EngineCommandOutbox
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.services import (
  exit_plan_authorization_service as authorization,
)
from sqlalchemy import select

from tests.api.unit.gqlapi.test_trade_approval_challenge import _principal
from tests.infrastructure.test_t_entry_confirmation import (
  CONFIRMED,
  allocation_sessions,
  base_sessions,
  seed_confirmable,
  sessions,
  signing_key,
)

_FIXTURES = allocation_sessions, base_sessions, sessions, signing_key
SCHEMA = strawberry.Schema(
  query=live.TAssistantLiveQuery, mutation=live.TAssistantLiveMutation
)
PERMISSIONS = frozenset(
  {"strategy:read", "t-trade:control", "liquidation:control", "trade:approve"}
)
QUEUE = '{tAssistantLiveApprovalQueue(accountId:"account-1"){executionId entries{intentId canPreview confirmationStatus}}}'
PREVIEW = 'mutation($account:String!){previewTAssistantLiveEntry(accountId:$account,executionId:"live-fixture",intentId:"intent-0"){success code preview{confirmationToken executionOwner{ownerType ownerId} environment}}}'
CONFIRM = 'mutation($token:String!){confirmTAssistantLiveEntry(accountId:"account-1",executionId:"live-fixture",intentId:"intent-0",confirmationToken:$token){success code challengeId}}'


def context(permissions=PERMISSIONS):
  return {
    "principal": replace(
      _principal(device_session_id="session-1", authorized_account_ids=("account-1",)),
      permissions=permissions,
      access_token_expires_at=datetime.now(UTC).replace(tzinfo=None)
      + timedelta(hours=1),
    )
  }


@pytest.mark.parametrize(
  "name", ["previewTAssistantLiveEntry", "confirmTAssistantLiveEntry"]
)
def test_permission_policy(name):
  assert set(required_permissions("Mutation", name)) == PERMISSIONS - {"strategy:read"}


async def test_real_challenge_outbox_and_queue(sessions, monkeypatch):
  await seed_confirmable(sessions)
  async with sessions.kw["bind"].begin() as connection:
    await connection.run_sync(
      lambda sync: Base.metadata.create_all(
        sync, tables=[EngineCommandOutbox.__table__]
      )
    )
  async with sessions() as db, db.begin():
    await db.delete(await db.get(TradeConfirmationChallenge, "challenge-1"))

  async def database():
    async with sessions() as db:
      yield db

  monkeypatch.setattr(api, "get_async_db", database)
  monkeypatch.setattr(api, "settings", authorization.settings)
  monkeypatch.setattr(api.time_utils, "now", lambda: to_shanghai(CONFIRMED))
  monkeypatch.setattr(live, "AsyncSessionLocal", sessions)

  class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
      return CONFIRMED.astimezone(tz or UTC)

  monkeypatch.setattr(live, "datetime", Clock)
  queue = await SCHEMA.execute(QUEUE, context_value=context())
  assert not queue.errors
  entries = queue.data["tAssistantLiveApprovalQueue"]["entries"]
  assert entries and entries[0]["canPreview"]
  assert entries[0]["confirmationStatus"] == "NONE"
  readonly = await SCHEMA.execute(
    QUEUE, context_value=context(frozenset({"strategy:read"}))
  )
  assert not readonly.errors
  assert not readonly.data["tAssistantLiveApprovalQueue"]["entries"][0]["canPreview"]
  for permissions, account in [
    (PERMISSIONS - {"liquidation:control"}, "account-1"),
    (PERMISSIONS, "foreign"),
  ]:
    denied = await SCHEMA.execute(
      PREVIEW, variable_values={"account": account}, context_value=context(permissions)
    )
    assert denied.errors
  preview = await SCHEMA.execute(
    PREVIEW, variable_values={"account": "account-1"}, context_value=context()
  )
  assert not preview.errors
  value = preview.data["previewTAssistantLiveEntry"]
  assert value["success"], value
  assert value["preview"]["executionOwner"] == {
    "ownerType": "T_ASSISTANT_EXECUTION",
    "ownerId": "live-fixture",
  }
  token = value["preview"]["confirmationToken"]
  first = await SCHEMA.execute(
    CONFIRM, variable_values={"token": token}, context_value=context()
  )
  replay = await SCHEMA.execute(
    CONFIRM, variable_values={"token": token}, context_value=context()
  )
  assert not first.errors and not replay.errors
  assert first.data == replay.data
  assert first.data["confirmTAssistantLiveEntry"]["success"]
  async with sessions() as db:
    commands = list((await db.scalars(select(EngineCommandOutbox))).all())
    assert len(commands) == 1
    assert commands[0].command_type == "T_ASSISTANT_APPROVE_ENTRY"
    assert commands[0].aggregate_id == "live-fixture"
  queue = await SCHEMA.execute(QUEUE, context_value=context())
  assert not queue.errors
  entry = queue.data["tAssistantLiveApprovalQueue"]["entries"][0]
  assert entry["confirmationStatus"] == "PENDING" and not entry["canPreview"]
  async with sessions() as db, db.begin():
    command = await db.get(EngineCommandOutbox, commands[0].message_id)
    command.processing_status = "FAILED"
  queue = await SCHEMA.execute(QUEUE, context_value=context())
  assert not queue.errors
  entry = queue.data["tAssistantLiveApprovalQueue"]["entries"][0]
  assert entry["confirmationStatus"] == "FAILED" and entry["canPreview"]


@pytest.mark.parametrize("failure", ["known", "unknown"])
async def test_confirmation_failure_preserves_outcome_semantics(monkeypatch, failure):
  async def consume(**kwargs):
    if failure == "known":
      raise api.TradeApprovalChallengeError("CHALLENGE_EXPIRED", "确认已过期")
    raise RuntimeError("internal database details")

  monkeypatch.setattr(api.TradeApprovalChallengeService, "consume", consume)
  result = await SCHEMA.execute(
    CONFIRM, variable_values={"token": "original"}, context_value=context()
  )
  assert not result.errors
  value = result.data["confirmTAssistantLiveEntry"]
  assert not value["success"]
  assert value["code"] == (
    "CHALLENGE_EXPIRED"
    if failure == "known"
    else "T_ASSISTANT_CONFIRMATION_OUTCOME_UNKNOWN"
  )
  assert "internal" not in str(value)


async def test_no_current_live_execution_returns_explicit_empty(sessions, monkeypatch):
  monkeypatch.setattr(live, "AsyncSessionLocal", sessions)
  result = await SCHEMA.execute(QUEUE, context_value=context())
  assert not result.errors
  assert result.data["tAssistantLiveApprovalQueue"] == {
    "executionId": None,
    "entries": [],
  }
