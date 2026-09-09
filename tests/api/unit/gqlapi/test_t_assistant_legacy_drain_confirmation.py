"""Persisted API-to-Engine drain chain; authentication refresh is synthetic."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from quantx_api.gqlapi import t_assistant_legacy_drain_confirmation as api
from quantx_api.gqlapi import trade_approval
from quantx_engine import command_processor
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.agent_runtime import EngineCommandOutbox
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.services import exit_plan_authorization_service
from sqlalchemy import func, select

from tests.api.unit.gqlapi.test_trade_approval_challenge import _principal
from tests.engine.unit.test_t_assistant_legacy_drain import seed_legacy_drain


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", [None, "token", "head", "device"])
async def test_api_confirmation_dispatch_and_replay(monkeypatch, damage):
  engine, sessions, now, digest = await seed_legacy_drain(monkeypatch)
  try:
    async with engine.begin() as connection:
      for table in (
        EngineCommandOutbox.__table__,
        TradeConfirmationChallenge.__table__,
      ):
        await connection.run_sync(lambda sync: table.create(sync))
    principal = replace(
      _principal(authorized_account_ids=("account-1",)),
      is_native_session=True,
      permissions=frozenset({"trade:approve", "t-trade:control"}),
      access_token_expires_at=time_utils.to_shanghai(now + timedelta(minutes=5)),
    )
    lock = AsyncMock(return_value=principal)
    monkeypatch.setattr(
      api.TTradeControlChallengeService, "_lock_current_principal", lock
    )
    settings = SimpleNamespace(
      secret_key="legacy-drain-test-secret-with-more-than-32-characters",
      algorithm="HS256",
    )
    monkeypatch.setattr(trade_approval, "settings", settings)
    monkeypatch.setattr(exit_plan_authorization_service, "settings", settings)
    request = dict(
      account_id="account-1",
      config_id="head",
      run_id="plan-1",
      expected_head_version=1,
      inventory_operation_id="inventory",
      expected_inventory_hash=digest,
      window_start=(now - timedelta(minutes=1)).isoformat(),
      window_end=(now + timedelta(minutes=1)).isoformat(),
    )
    monkeypatch.setattr(command_processor, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(command_processor, "utcnow", lambda: now.replace(tzinfo=None))
    preparation_id = str(uuid4())
    preparation_request = {
      key: request[key]
      for key in ("account_id", "config_id", "run_id", "expected_head_version")
    }
    async with sessions() as db, db.begin():
      for _ in range(2):
        assert (
          await api.enqueue_legacy_inventory(
            db,
            principal=principal,
            request_id=preparation_id,
            request=preparation_request,
            now=now,
          )
          == preparation_id
        )
    claimed = await command_processor._claim_next()
    assert claimed[0] == preparation_id
    inventory = await command_processor._dispatch(
      claimed[1], claimed[2], command_id=claimed[0]
    )
    assert inventory == await command_processor._dispatch(
      claimed[1], claimed[2], command_id=claimed[0]
    )
    # Command success alone cannot prove a reviewed inventory exists.
    async with sessions() as db, db.begin():
      command = await db.get(EngineCommandOutbox, preparation_id)
      command.processing_status = "SUCCEEDED"
      from quantx_infrastructure.models.agent_runtime import TTradeRolloutEvent

      event = await db.get(TTradeRolloutEvent, inventory["inventory_operation_id"])
      saved = event.details
      event.details = {**saved, "manifest_hash": "0" * 64}
    async with sessions() as db, db.begin():
      with pytest.raises(ValueError, match="EVIDENCE_CONFLICT"):
        await api.read_legacy_maintenance_operation(
          db,
          principal=principal,
          account_id="account-1",
          command_id=preparation_id,
        )
    async with sessions() as db, db.begin():
      (
        await db.get(TTradeRolloutEvent, inventory["inventory_operation_id"])
      ).details = saved
    await command_processor._complete(preparation_id, result=inventory)
    async with sessions() as db, db.begin():
      status = await api.read_legacy_maintenance_operation(
        db,
        principal=principal,
        account_id="account-1",
        command_id=preparation_id,
      )
      assert status["status"] == "SUCCEEDED"
      assert status["evidence"]["manifest_hash"] == inventory["manifest_hash"]
    request.update(
      inventory_operation_id=inventory["inventory_operation_id"],
      expected_inventory_hash=inventory["manifest_hash"],
    )
    async with sessions() as db, db.begin():
      issued = await api.issue_drain_confirmation(
        db, principal=principal, request=request, now=now
      )
      row = await db.get(TradeConfirmationChallenge, issued["challenge_id"])
      assert issued["confirmation_token"] not in str(row.payload)
      assert row.token_digest != issued["confirmation_token"]
    if damage == "head":
      async with sessions() as db, db.begin():
        (await db.get(TTradeGlobalConfig, "head")).state_version += 1
    if damage == "device":
      principal = replace(principal, device_session_id="other-device")
      lock.return_value = principal

    async def consume():
      async with sessions() as db, db.begin():
        return await api.consume_drain_confirmation(
          db,
          principal=principal,
          challenge_id=issued["challenge_id"],
          confirmation_token="bad"
          if damage == "token"
          else issued["confirmation_token"],
          now=now + timedelta(seconds=1),
        )

    if damage:
      with pytest.raises(ValueError):
        await consume()
      async with sessions() as db:
        assert (
          await db.get(TradeConfirmationChallenge, issued["challenge_id"])
        ).consumed_at is None
        assert (
          await db.scalar(select(func.count()).select_from(EngineCommandOutbox)) == 1
        )
      return
    identity = await consume()
    assert await consume() == identity
    monkeypatch.setattr(command_processor, "AsyncSessionLocal", sessions)
    monkeypatch.setattr(
      command_processor,
      "utcnow",
      lambda: (now + timedelta(seconds=2)).replace(tzinfo=None),
    )
    monkeypatch.setattr(
      command_processor, "strategy_manager", SimpleNamespace(get_run=lambda _: None)
    )
    result = await command_processor._dispatch(
      api.COMMAND, {"challenge_id": issued["challenge_id"]}, command_id=identity
    )
    assert result["success"] and result["cancelled_intent_ids"] == ["unsubmitted"]
    async with sessions() as db, db.begin():
      status = await api.read_legacy_maintenance_operation(
        db,
        principal=principal,
        account_id="account-1",
        command_id=identity,
      )
      assert status["status"] == "PENDING" and status["evidence"] is None
    await command_processor._complete(identity, result=result)
    async with sessions() as db, db.begin():
      status = await api.read_legacy_maintenance_operation(
        db,
        principal=principal,
        account_id="account-1",
        command_id=identity,
      )
      assert status["status"] == "SUCCEEDED"
      assert status["evidence"]["cancelled_intent_ids"] == ["unsubmitted"]
    assert await consume() == identity
    async with sessions() as db:
      head = await db.get(TTradeGlobalConfig, "head")
      assert head.state_version == 2 and head.strategy_run_id == "plan-1"
      assert await db.scalar(select(func.count()).select_from(EngineCommandOutbox)) == 2
    # A committed inventory retry preserves its review cut even after drain changed facts.
    assert inventory == await command_processor._dispatch(
      claimed[1], claimed[2], command_id=claimed[0]
    )
  finally:
    await engine.dispose()
