"""Real challenge/outbox persistence with synthetic authentication and P5 data."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_api.gqlapi import t_assistant_release_confirmation as api
from quantx_api.gqlapi import trade_approval
from quantx_engine import command_processor
from quantx_infrastructure.models.agent_runtime import EngineCommandOutbox
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from sqlalchemy import select

from tests.api.unit.gqlapi.test_trade_approval_challenge import _principal
from tests.engine.unit.test_t_assistant_release_approval import (
  NOW,
  release,
  review,
  sessions,
)

_FIXTURES = release, review, sessions


@pytest.fixture
async def context(sessions, review, monkeypatch):
  async with sessions.kw["bind"].begin() as connection:
    for table in (TradeConfirmationChallenge.__table__, EngineCommandOutbox.__table__):
      await connection.run_sync(lambda sync: table.create(sync))
  principal = replace(
    _principal(authorized_account_ids=("account-1",)),
    is_native_session=True,
    permissions=frozenset({"trade:approve", "t-trade:control"}),
  )
  lock = AsyncMock(return_value=principal)
  monkeypatch.setattr(
    api.TTradeControlChallengeService, "_lock_current_principal", lock
  )
  monkeypatch.setattr(
    trade_approval,
    "settings",
    SimpleNamespace(
      secret_key="release-test-secret-key-with-more-than-32-characters",
      algorithm="HS256",
    ),
  )
  request = {
    key: review[key]
    for key in (
      "account_id",
      "source_execution_id",
      "config_version_id",
      "expected_config_hash",
      "expected_head_version",
      "expected_report_hash",
      "expected_policy_hash",
      "window_start",
      "window_end",
    )
  }
  request["evaluation_id"] = review["evidence_directory"].name
  async with sessions() as db, db.begin():
    issued = await api.issue_release_confirmation(
      db, principal=principal, request=request, now=NOW
    )
  return principal, issued, lock


async def test_api_confirmation_to_engine_release_and_retry(
  sessions, review, context, monkeypatch
):
  principal, issued, lock = context
  async with sessions() as db, db.begin():
    identity = await api.consume_release_confirmation(
      db,
      principal=principal,
      challenge_id=issued["challenge_id"],
      confirmation_token=issued["confirmation_token"],
      now=NOW + timedelta(seconds=1),
    )
    row = await db.get(TradeConfirmationChallenge, issued["challenge_id"])
    assert issued["confirmation_token"] not in str(row.payload)
    assert row.token_digest != issued["confirmation_token"]
  monkeypatch.setattr(command_processor, "AsyncSessionLocal", sessions)
  monkeypatch.setattr(command_processor, "utcnow", lambda: NOW + timedelta(seconds=2))
  monkeypatch.setenv(
    "T_ASSISTANT_EVALUATION_ROOT", str(review["evidence_directory"].parent)
  )
  result = await command_processor._dispatch(
    api.COMMAND, {"challenge_id": issued["challenge_id"]}, command_id=identity
  )
  assert result["success"]
  async with sessions() as db, db.begin():
    assert (
      await api.consume_release_confirmation(
        db,
        principal=principal,
        challenge_id=issued["challenge_id"],
        confirmation_token=issued["confirmation_token"],
        now=NOW + timedelta(days=1),
      )
      == identity
    )
    assert len(list((await db.scalars(select(EngineCommandOutbox))).all())) == 1
  assert lock.await_count == 3


@pytest.mark.parametrize(
  "damage",
  ["device", "permission", "native", "token", "expired", "payload", "revoked", "head"],
)
async def test_invalid_confirmation_never_enqueues(sessions, context, damage):
  principal, issued, lock = context
  token, now = issued["confirmation_token"], NOW + timedelta(seconds=1)
  if damage == "device":
    principal = replace(principal, device_session_id="another")
    lock.return_value = principal
  elif damage == "permission":
    principal = replace(principal, permissions=frozenset())
  elif damage == "native":
    principal = replace(principal, is_native_session=False)
  elif damage == "token":
    token = "invalid"
  elif damage == "expired":
    now = NOW + timedelta(seconds=60)
  elif damage == "revoked":
    lock.side_effect = ValueError("SESSION_REVOKED")
  async with sessions() as db, db.begin():
    if damage == "head":
      from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig

      head = await db.get(TTradeGlobalConfig, "config-1")
      head.state_version += 1
      await db.flush()
    if damage == "payload":
      row = await db.get(TradeConfirmationChallenge, issued["challenge_id"])
      row.payload = {**row.payload, "expected_report_hash": "0" * 64}
      await db.flush()
    with pytest.raises(ValueError):
      await api.consume_release_confirmation(
        db,
        principal=principal,
        challenge_id=issued["challenge_id"],
        confirmation_token=token,
        now=now,
      )
    assert await db.scalar(select(EngineCommandOutbox)) is None
    assert (
      await db.get(TradeConfirmationChallenge, issued["challenge_id"])
    ).consumed_at is None


async def test_outbox_failure_rolls_back_challenge_consumption(
  sessions, context, monkeypatch
):
  principal, issued, _ = context
  with pytest.raises(RuntimeError, match="injected commit failure"):
    async with sessions() as db, db.begin():
      original = db.flush

      async def fail():
        await original()
        raise RuntimeError("injected commit failure")

      monkeypatch.setattr(db, "flush", fail)
      await api.consume_release_confirmation(
        db,
        principal=principal,
        challenge_id=issued["challenge_id"],
        confirmation_token=issued["confirmation_token"],
        now=NOW + timedelta(seconds=1),
      )
  async with sessions() as db:
    assert await db.scalar(select(EngineCommandOutbox)) is None
    assert (
      await db.get(TradeConfirmationChallenge, issued["challenge_id"])
    ).consumed_at is None
