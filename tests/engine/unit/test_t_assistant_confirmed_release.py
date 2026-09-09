"""Only a consumed device challenge may drive the release command."""

from datetime import timedelta

import pytest
from quantx_engine import command_processor
from quantx_infrastructure.models.agent_runtime import EngineCommandOutbox
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantExecutionEventRecord,
)
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from sqlalchemy import select

from tests.engine.unit.test_t_assistant_release_approval import (
  NOW,
  release,
  review,
  sessions,
)

_FIXTURES = review, release, sessions


@pytest.mark.parametrize(
  "damage",
  [
    None,
    "unconsumed",
    "command",
    "actor_payload",
    "expired",
    "directory",
    "future_window",
  ],
)
async def test_engine_requires_exact_durable_confirmation(
  sessions, review, monkeypatch, damage
):
  async with sessions.kw["bind"].begin() as connection:
    await connection.run_sync(
      lambda sync: TradeConfirmationChallenge.__table__.create(sync)
    )
    await connection.run_sync(lambda sync: EngineCommandOutbox.__table__.create(sync))
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
    )
  }
  request.update(
    evaluation_id=review["evidence_directory"].name,
    window_start=review["window_start"].isoformat(),
    window_end=review["window_end"].isoformat(),
  )
  if damage == "actor_payload":
    request["actor_id"] = "injected"
  if damage == "future_window":
    request["window_start"] = (NOW + timedelta(days=1)).isoformat()
    request["window_end"] = (NOW + timedelta(days=2)).isoformat()
  if damage == "directory":
    request["evaluation_id"] = "../outside"
  async with sessions() as db, db.begin():
    db.add(
      TradeConfirmationChallenge(
        id="challenge",
        action="T_ASSISTANT_LIVE_RELEASE",
        user_id="device-reviewer",
        device_session_id="device-session",
        account_id="account-1",
        idempotency_key="release",
        payload=request,
        payload_fingerprint="a" * 64,
        token_digest="b" * 64,
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=60),
        consumed_at=None
        if damage == "unconsumed"
        else NOW + timedelta(seconds=61 if damage == "expired" else 1),
        result_reference={
          "engine_command": {
            "message_id": "another" if damage == "command" else "command"
          }
        },
      )
    )
    db.add(
      EngineCommandOutbox(
        message_id="command",
        idempotency_key="release-command",
        command_type="T_ASSISTANT_CONFIRM_LIVE_RELEASE",
        aggregate_id=review["source_execution_id"],
        payload={"challenge_id": "challenge"},
        processing_status="PENDING",
        available_at=NOW,
      )
    )
  monkeypatch.setattr(command_processor, "AsyncSessionLocal", sessions)
  monkeypatch.setattr(command_processor, "utcnow", lambda: NOW + timedelta(seconds=2))
  monkeypatch.setenv(
    "T_ASSISTANT_EVALUATION_ROOT", str(review["evidence_directory"].parent)
  )

  async def dispatch():
    return await command_processor._dispatch(
      "T_ASSISTANT_CONFIRM_LIVE_RELEASE",
      {"challenge_id": "challenge"},
      command_id="command",
    )

  if damage:
    with pytest.raises(ValueError):
      await dispatch()
  else:
    result = await dispatch()
    assert await dispatch() == result
    assert result["success"]
  async with sessions() as db:
    approval = await db.scalar(
      select(TAssistantExecutionEventRecord).where(
        TAssistantExecutionEventRecord.event_key == "live-release:challenge"
      )
    )
    if damage:
      assert approval is None
    else:
      assert approval.payload["actor_id"] == "device-reviewer"
