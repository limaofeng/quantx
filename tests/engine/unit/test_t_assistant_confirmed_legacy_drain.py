from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_engine import command_processor
from quantx_engine.t_assistant_confirmed_legacy_drain import ACTION, COMMAND
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.agent_runtime import EngineCommandOutbox
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.services.exit_plan_authorization_service import (
  trade_confirmation_payload_fingerprint,
)

from tests.engine.unit.test_t_assistant_legacy_drain import seed_legacy_drain
from tests.infrastructure.test_t_entry_confirmation import signing_key as _signing_key

signing_key = _signing_key


async def seed_confirmed_legacy_drain(monkeypatch, damage=None):
  engine, sessions, now, digest = await seed_legacy_drain(monkeypatch)
  async with engine.begin() as connection:
    await connection.run_sync(lambda sync: EngineCommandOutbox.__table__.create(sync))
    await connection.run_sync(
      lambda sync: TradeConfirmationChallenge.__table__.create(sync)
    )
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
  async with sessions() as db, db.begin():
    db.add(
      TradeConfirmationChallenge(
        id="challenge",
        action=ACTION,
        account_id="account-1",
        user_id="user-1",
        device_session_id="session",
        idempotency_key="challenge",
        payload=request,
        token_digest="a" * 64,
        payload_fingerprint="bad"
        if damage == "tampered"
        else trade_confirmation_payload_fingerprint(request),
        created_at=time_utils.to_shanghai(now - timedelta(seconds=10)),
        consumed_at=None if damage == "unconsumed" else time_utils.to_shanghai(now),
        expires_at=time_utils.to_shanghai(now + timedelta(seconds=30)),
        result_reference={
          "engine_command": {
            "message_id": "other" if damage == "command" else "command"
          }
        },
      )
    )
    db.add(
      EngineCommandOutbox(
        message_id="command",
        command_type=COMMAND,
        aggregate_id="plan-1",
        idempotency_key="command",
        payload={"challenge_id": "challenge"},
        available_at=now.replace(tzinfo=None),
        processing_status="PENDING",
      )
    )
  return engine, sessions, now


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "damage", [None, "unconsumed", "tampered", "command", "window", "runtime"]
)
async def test_confirmation_command_drains_and_recovers_after_commit(
  monkeypatch, damage
):
  engine, sessions, now = await seed_confirmed_legacy_drain(monkeypatch, damage)
  clock = now + timedelta(minutes=2) if damage == "window" else now
  monkeypatch.setattr(command_processor, "AsyncSessionLocal", sessions)
  monkeypatch.setattr(command_processor, "utcnow", lambda: clock.replace(tzinfo=None))
  invalidate = AsyncMock(return_value=True)
  if damage == "runtime":
    invalidate.side_effect = [RuntimeError("synthetic runtime fault"), True]
  monkeypatch.setattr(
    command_processor,
    "strategy_manager",
    SimpleNamespace(
      get_run=lambda _run: object(),
      executor=SimpleNamespace(invalidate_t_trade_entry_authority=invalidate),
    ),
  )

  async def dispatch():
    return await command_processor._dispatch(
      COMMAND, {"challenge_id": "challenge"}, command_id="command"
    )

  try:
    claimed = await command_processor._claim_next()
    assert claimed == ("command", COMMAND, {"challenge_id": "challenge"})
    if damage not in {None, "runtime"}:
      with pytest.raises(ValueError, match="LEGACY_T_DRAIN_"):
        await dispatch()
      invalidate.assert_not_awaited()
      async with sessions() as db:
        assert (await db.get(TTradeGlobalConfig, "head")).state_version == 1
    else:
      if damage == "runtime":
        with pytest.raises(command_processor.LegacyDrainRuntimeInvalidationPending):
          await dispatch()
      else:
        result = await dispatch()
        assert result["cancelled_intent_ids"] == ["unsubmitted"]
      # The original operation was committed; recovery is allowed after its
      # maintenance window, without reapplying cancellation or advancing head.
      clock = now + timedelta(minutes=2)
      result = await dispatch()
      await command_processor._complete("command", result=result)
      async with sessions() as db:
        head = await db.get(TTradeGlobalConfig, "head")
        assert head.state_version == 2 and head.strategy_run_id == "plan-1"
        command = await db.get(EngineCommandOutbox, "command")
        assert command.processing_status == "SUCCEEDED" and command.result == result
      assert await command_processor._claim_next() is None
      assert invalidate.await_count == 2
  finally:
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["recover", "invalid", "restart"])
async def test_real_consumer_retries_only_committed_runtime_convergence(
  monkeypatch, mode
):
  import asyncio

  from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

  engine, sessions, now = await seed_confirmed_legacy_drain(
    monkeypatch, "tampered" if mode == "invalid" else None
  )
  stopped = asyncio.Event()
  clock = now
  monkeypatch.setattr(command_processor, "AsyncSessionLocal", sessions)
  monkeypatch.setattr(command_processor, "utcnow", lambda: clock.replace(tzinfo=None))
  invalidate = AsyncMock(
    side_effect=[RuntimeError("synthetic runtime fault"), False, True]
    if mode == "recover"
    else [False, True]
  )
  monkeypatch.setattr(
    command_processor,
    "strategy_manager",
    SimpleNamespace(
      get_run=lambda _: object(),
      executor=SimpleNamespace(invalidate_t_trade_entry_authority=invalidate),
    ),
  )
  original_complete = command_processor._complete
  original_reschedule = command_processor._reschedule_legacy_drain
  delays = []

  async def complete(*args, **kwargs):
    await original_complete(*args, **kwargs)
    stopped.set()

  async def reschedule(identity):
    nonlocal clock
    if mode == "restart":
      stopped.set()
      raise SQLAlchemyTimeoutError("synthetic pool exhaustion")
    await original_reschedule(identity)
    async with sessions() as db:
      row = await db.get(EngineCommandOutbox, identity)
      assert (
        row.processing_status == "PENDING"
        and row.processed_at is None
        and row.result is None
      )
      assert row.processing_error == "LEGACY_T_DRAIN_RUNTIME_INVALIDATION_PENDING"
      delays.append((row.available_at - clock.replace(tzinfo=None)).total_seconds())
      assert (await db.get(TTradeGlobalConfig, "head")).state_version == 2
    assert await command_processor._claim_next() is None
    clock += timedelta(minutes=2)

  monkeypatch.setattr(command_processor, "_complete", complete)
  monkeypatch.setattr(command_processor, "_reschedule_legacy_drain", reschedule)
  try:
    await asyncio.wait_for(command_processor.run_command_consumer(stopped), timeout=5)
    if mode == "restart":
      async with sessions() as db:
        assert (
          await db.get(EngineCommandOutbox, "command")
        ).processing_status == "PROCESSING"
        assert (await db.get(TTradeGlobalConfig, "head")).state_version == 2
      # New consumer performs its real startup recovery; the original command
      # and signed confirmation are reused after the maintenance window closed.
      clock += timedelta(minutes=2)
      stopped = asyncio.Event()
      monkeypatch.setattr(
        command_processor, "_reschedule_legacy_drain", original_reschedule
      )
      await asyncio.wait_for(command_processor.run_command_consumer(stopped), timeout=5)
    async with sessions() as db:
      row = await db.get(EngineCommandOutbox, "command")
      head = await db.get(TTradeGlobalConfig, "head")
      assert head.strategy_run_id == "plan-1"
      if mode == "invalid":
        assert row.processing_status == "FAILED" and row.processing_attempts == 1
        assert head.state_version == 1
        invalidate.assert_not_awaited()
      else:
        assert row.processing_status == "SUCCEEDED"
        assert row.processing_attempts == (3 if mode == "recover" else 2)
        assert head.state_version == 2
        assert row.result["cancelled_intent_ids"] == ["unsubmitted"]
    assert delays == ([1, 2] if mode == "recover" else [])
  finally:
    stopped.set()
    await engine.dispose()
