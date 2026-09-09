"""Actual completion consumer with SQLite; remote runtime effects are isolated."""

import asyncio
from datetime import UTC, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_engine import command_processor
from quantx_engine import t_assistant_legacy_completion as completion
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.agent_runtime import (
  EngineCommandOutbox,
  TTradeRolloutEvent,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.services.exit_plan_authorization_service import (
  trade_confirmation_payload_fingerprint,
)
from sqlalchemy.exc import DBAPIError

from tests.engine.unit.test_t_assistant_legacy_completion import seed_completion
from tests.infrastructure.test_t_entry_confirmation import signing_key as _signing_key

signing_key = _signing_key


async def seed_command(monkeypatch):
  engine, sessions, now = await seed_completion(monkeypatch)
  async with engine.begin() as connection:
    for table in (EngineCommandOutbox.__table__, TradeConfirmationChallenge.__table__):
      await connection.run_sync(lambda sync: table.create(sync))
  payload = {"drain_command_id": "original", "expected_head_version": 2}
  async with sessions() as db, db.begin():
    marker = await db.get(TTradeRolloutEvent, "legacy-t-drain:plan-1")
    at = marker.created_at.replace(tzinfo=UTC)
    request = {
      **marker.details["request"],
      "account_id": "account-1",
      "window_start": (at - timedelta(seconds=30)).isoformat(),
      "window_end": (at + timedelta(seconds=30)).isoformat(),
    }
    request["expected_inventory_hash"] = request.pop("inventory_hash")
    db.add(
      TradeConfirmationChallenge(
        id="original-challenge",
        action="T_ASSISTANT_LEGACY_DRAIN",
        account_id="account-1",
        user_id="user-1",
        device_session_id="session-1",
        idempotency_key="original-challenge",
        payload=request,
        payload_fingerprint=trade_confirmation_payload_fingerprint(request),
        token_digest="a" * 64,
        created_at=time_utils.to_shanghai(at - timedelta(seconds=10)),
        consumed_at=time_utils.to_shanghai(at),
        expires_at=time_utils.to_shanghai(at + timedelta(seconds=30)),
        result_reference={"engine_command": {"message_id": "original"}},
      )
    )
    db.add(
      EngineCommandOutbox(
        message_id="original",
        idempotency_key="original",
        command_type="T_ASSISTANT_CONFIRM_LEGACY_DRAIN",
        aggregate_id="plan-1",
        payload={"challenge_id": "original-challenge"},
        processing_status="SUCCEEDED",
        available_at=now.replace(tzinfo=None),
        processed_at=now.replace(tzinfo=None),
      )
    )
    db.add(
      EngineCommandOutbox(
        message_id="completion",
        idempotency_key="completion",
        command_type="T_ASSISTANT_COMPLETE_LEGACY_DRAIN",
        aggregate_id="plan-1",
        payload=payload,
        processing_status="PENDING",
        available_at=now.replace(tzinfo=None),
      )
    )
  return engine, sessions, now, payload


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["runtime", "serialization", "deadlock"])
async def test_consumer_retries_original_command_and_commits_once(monkeypatch, failure):
  engine, sessions, clock, _ = await seed_command(monkeypatch)
  stopped = asyncio.Event()
  monkeypatch.setattr(command_processor, "AsyncSessionLocal", sessions)
  monkeypatch.setattr(command_processor, "utcnow", lambda: clock.replace(tzinfo=None))
  retire = AsyncMock(
    side_effect=[False, True] if failure == "runtime" else None, return_value=True
  )
  monkeypatch.setattr(
    command_processor,
    "strategy_manager",
    SimpleNamespace(executor=SimpleNamespace(retire_completed_legacy_run=retire)),
  )
  actual_dispatch = completion.dispatch_legacy_completion
  attempts = 0

  async def dispatch(db, **kwargs):
    nonlocal attempts
    attempts += 1
    if failure != "runtime" and attempts == 1:
      (await db.get(TTradeGlobalConfig, "head")).last_error = "must roll back"
      await db.flush()
      error = RuntimeError("synthetic transaction conflict")
      error.sqlstate = "40001" if failure == "serialization" else "40P01"
      raise DBAPIError(None, None, error)
    return await actual_dispatch(db, **kwargs)

  monkeypatch.setattr(completion, "dispatch_legacy_completion", dispatch)
  reschedule = command_processor._reschedule_legacy_drain

  async def deferred(message_id, **kwargs):
    nonlocal clock
    await reschedule(message_id, **kwargs)
    async with sessions() as db:
      command = await db.get(EngineCommandOutbox, message_id)
      assert command.processing_status == "PENDING" and command.result is None
      assert command.processed_at is None
      assert (await db.get(TTradeGlobalConfig, "head")).last_error is None
    clock += timedelta(seconds=60)

  monkeypatch.setattr(command_processor, "_reschedule_legacy_drain", deferred)
  finish = command_processor._complete

  async def completed(message_id, **kwargs):
    await finish(message_id, **kwargs)
    stopped.set()

  monkeypatch.setattr(command_processor, "_complete", completed)
  try:
    await asyncio.wait_for(command_processor.run_command_consumer(stopped), timeout=5)
    async with sessions() as db:
      command = await db.get(EngineCommandOutbox, "completion")
      # Only the post-commit runtime retry can outlive snapshot freshness. For
      # transaction retries the first attempt never completed the source cut.
      assert command.processing_status == "SUCCEEDED"
      assert command.processing_attempts == 2
      assert (await db.get(TTradeGlobalConfig, "head")).state_version == 3
      assert await db.get(TTradeRolloutEvent, "legacy-t-completed:plan-1") is not None
    assert retire.await_count == (2 if failure == "runtime" else 1)
  finally:
    stopped.set()
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "damage", ["signature", "aggregate", "original_status", "missing_fence"]
)
async def test_completion_cannot_use_an_unconfirmed_or_wrong_source(
  monkeypatch, damage
):
  engine, sessions, now, payload = await seed_command(monkeypatch)
  monkeypatch.setattr(command_processor, "AsyncSessionLocal", sessions)
  monkeypatch.setattr(command_processor, "utcnow", lambda: now.replace(tzinfo=None))
  retire = AsyncMock(return_value=True)
  monkeypatch.setattr(
    command_processor,
    "strategy_manager",
    SimpleNamespace(executor=SimpleNamespace(retire_completed_legacy_run=retire)),
  )
  try:
    async with sessions() as db, db.begin():
      (await db.get(EngineCommandOutbox, "completion")).processing_status = "PROCESSING"
      if damage == "signature":
        (
          await db.get(TradeConfirmationChallenge, "original-challenge")
        ).payload_fingerprint = "bad"
      elif damage == "aggregate":
        (await db.get(EngineCommandOutbox, "completion")).aggregate_id = "other"
      elif damage == "original_status":
        (await db.get(EngineCommandOutbox, "original")).processing_status = "FAILED"
      else:
        await db.delete(await db.get(TTradeRolloutEvent, "legacy-t-drain:plan-1"))
    with pytest.raises(ValueError, match="LEGACY_T_"):
      await command_processor._dispatch(
        "T_ASSISTANT_COMPLETE_LEGACY_DRAIN", payload, command_id="completion"
      )
    retire.assert_not_awaited()
    async with sessions() as db:
      head = await db.get(TTradeGlobalConfig, "head")
      assert head.strategy_run_id == "plan-1" and head.state_version == 2
  finally:
    await engine.dispose()
