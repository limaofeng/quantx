"""Public completion producer, actual Engine dispatch and durable status evidence."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_api.gqlapi import t_assistant_legacy_drain_confirmation as api
from quantx_api.gqlapi import trade_approval
from quantx_engine import command_processor
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.agent_runtime import (
  EngineCommandOutbox,
  TTradeRolloutEvent,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.services import exit_plan_authorization_service
from sqlalchemy import func, select

from tests.api.unit.gqlapi.test_trade_approval_challenge import _principal
from tests.engine.unit.test_t_assistant_legacy_completion_command import seed_command
from tests.infrastructure.test_t_entry_confirmation import signing_key as _signing_key

signing_key = _signing_key


async def setup(monkeypatch):
  monkeypatch.setattr(trade_approval, "settings", exit_plan_authorization_service.settings)
  engine, sessions, now, _ = await seed_command(monkeypatch)
  async with sessions() as db, db.begin():
    await db.delete(await db.get(EngineCommandOutbox, "completion"))
  principal = replace(
    _principal(authorized_account_ids=("account-1",), device_session_id="session-1"),
    is_native_session=True,
    permissions=frozenset({"trade:approve", "t-trade:control"}),
    access_token_expires_at=time_utils.to_shanghai(now + timedelta(minutes=5)),
  )
  monkeypatch.setattr(
    api.TTradeControlChallengeService,
    "_lock_current_principal",
    AsyncMock(return_value=principal),
  )
  monkeypatch.setattr(command_processor, "AsyncSessionLocal", sessions)
  monkeypatch.setattr(command_processor, "utcnow", lambda: now.replace(tzinfo=None))
  monkeypatch.setattr(
    command_processor,
    "strategy_manager",
    SimpleNamespace(
      executor=SimpleNamespace(
        retire_completed_legacy_run=AsyncMock(return_value=True)
      ),
    ),
  )
  return engine, sessions, now, principal


@pytest.mark.asyncio
async def test_enqueue_dispatch_status_and_exact_replay(monkeypatch):
  engine, sessions, now, principal = await setup(monkeypatch)
  kwargs = dict(
    principal=principal,
    account_id="account-1",
    drain_command_id="original",
    expected_head_version=2,
    now=now,
  )
  try:
    async with sessions() as db, db.begin():
      identity = await api.enqueue_legacy_completion(db, **kwargs)
      assert len(identity) == 36  # PostgreSQL message_id is varchar(36).
      assert await api.enqueue_legacy_completion(db, **kwargs) == identity
      status = await api.read_legacy_maintenance_operation(
        db, principal=principal, account_id="account-1", command_id=identity
      )
      assert status == dict(command_id=identity, status="PENDING", evidence=None)
    claimed = await command_processor._claim_next()
    assert claimed[0] == identity
    result = await command_processor._dispatch(
      claimed[1], claimed[2], command_id=identity
    )
    # A committed source cut is not successful completion until cleanup and
    # terminal command persistence have both succeeded.
    async with sessions() as db, db.begin():
      status = await api.read_legacy_maintenance_operation(
        db, principal=principal, account_id="account-1", command_id=identity
      )
      assert status["status"] == "PROCESSING" and status["evidence"] is None
    await command_processor._complete(identity, result=result)
    async with sessions() as db, db.begin():
      assert await api.enqueue_legacy_completion(db, **kwargs) == identity
      assert (await db.get(TTradeGlobalConfig, "head")).strategy_run_id is None
      status = await api.read_legacy_maintenance_operation(
        db, principal=principal, account_id="account-1", command_id=identity
      )
      assert status["status"] == "SUCCEEDED"
      assert status["evidence"]["evidence_hash"] == result["evidence_hash"]
      with pytest.raises(ValueError, match="REQUEST_CONFLICT"):
        await api.enqueue_legacy_completion(
          db, **{**kwargs, "expected_head_version": 3}
        )
      assert await db.scalar(select(func.count()).select_from(EngineCommandOutbox)) == 2
    async with sessions() as db, db.begin():
      event = await db.get(TTradeRolloutEvent, "legacy-t-completed:plan-1")
      event.details = {**event.details, "evidence_hash": "bad"}
    async with sessions() as db, db.begin():
      with pytest.raises(ValueError, match="EVIDENCE_CONFLICT"):
        await api.read_legacy_maintenance_operation(
          db, principal=principal, account_id="account-1", command_id=identity
        )
  finally:
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "damage", ["device", "signature", "actor", "status", "fence", "head", "recursive"]
)
async def test_completion_producer_rejects_wrong_source_without_enqueue(
  monkeypatch, damage
):
  engine, sessions, now, principal = await setup(monkeypatch)
  try:
    async with sessions() as db, db.begin():
      challenge = await db.get(TradeConfirmationChallenge, "original-challenge")
      if damage == "device":
        challenge.device_session_id = "other"
      elif damage == "signature":
        challenge.payload_fingerprint = "bad"
      elif damage == "actor":
        challenge.user_id = "other"
      elif damage == "status":
        (await db.get(EngineCommandOutbox, "original")).processing_status = "PENDING"
      elif damage == "recursive":
        (
          await db.get(EngineCommandOutbox, "original")
        ).command_type = api.COMPLETION_COMMAND
      elif damage == "fence":
        await db.delete(await db.get(TTradeRolloutEvent, "legacy-t-drain:plan-1"))
      else:
        (await db.get(TTradeGlobalConfig, "head")).state_version += 1
    async with sessions() as db, db.begin():
      with pytest.raises(ValueError, match="LEGACY_T_"):
        await api.enqueue_legacy_completion(
          db,
          principal=principal,
          account_id="account-1",
          drain_command_id="original",
          expected_head_version=2,
          now=now,
        )
      assert await db.scalar(select(func.count()).select_from(EngineCommandOutbox)) == 1
  finally:
    await engine.dispose()
