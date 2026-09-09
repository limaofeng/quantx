"""Actual synthetic evaluation -> persisted review -> WARMING preparation."""

from datetime import timedelta

import pytest
from quantx_domain.trading.t_assistant_execution import TAssistantConfigVersion
from quantx_engine.t_assistant_live_admission import dispatch_live_canary_preparation
from quantx_engine.t_assistant_release_approval import record_live_release_approval
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantExecutionEventRecord,
  TAssistantExecutionRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.repositories.t_assistant_config_repository import (
  TAssistantConfigRepository,
)
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionConflict,
  TAssistantExecutionRepository,
)
from sqlalchemy import select

from tests.engine.unit.test_t_assistant_release_evidence import release
from tests.infrastructure.test_t_assistant_runtime_repository import (
  NOW,
  _seed_execution,
  sessions,
)

_FIXTURES = (release, sessions)


@pytest.fixture
async def review(sessions, release):
  original, source = await _seed_execution(sessions)
  directory, report_hash, policy_hash, values = release
  target = TAssistantConfigVersion.create(**values)
  async with sessions() as db, db.begin():
    await TAssistantConfigRepository(db).append_version(target)
    head = await db.get(TTradeGlobalConfig, target.config_id)
    head.active_config_version_id = original.config_version_id
  return dict(
    account_id="account-1",
    actor_id="synthetic-reviewer",
    review_reference="unit-test-only",
    approval_event_key="review-1",
    source_execution_id=source,
    config_version_id=target.config_version_id,
    expected_config_hash=target.config_snapshot_hash,
    expected_head_version=1,
    evidence_directory=directory,
    expected_report_hash=report_hash,
    expected_policy_hash=policy_hash,
    window_start=NOW,
    window_end=NOW + timedelta(minutes=10),
    now=NOW,
  )


async def test_review_retries_then_prepares_exact_source(sessions, review):
  async with sessions() as db, db.begin():
    result = await record_live_release_approval(db, **review)
  async with sessions() as db, db.begin():
    assert await record_live_release_approval(db, **review) == result
    execution_id = await dispatch_live_canary_preparation(
      db,
      payload={
        **result,
        "account_id": review["account_id"],
        "source_execution_id": review["source_execution_id"],
        "config_version_id": review["config_version_id"],
        "expected_head_version": 1,
      },
      now=NOW + timedelta(seconds=1),
    )
  async with sessions() as db, db.begin():
    assert (await db.get(TAssistantExecutionRecord, execution_id)).status == "WARMING"
    # Exact review retry keeps its identity even after execution creation/window end.
    assert (
      await record_live_release_approval(
        db, **{**review, "now": NOW + timedelta(days=1)}
      )
      == result
    )
    with pytest.raises(TAssistantExecutionConflict, match="EVENT_CONFLICT"):
      await record_live_release_approval(
        db, **{**review, "actor_id": "another-reviewer"}
      )


@pytest.mark.parametrize("damage", ["account", "window", "config", "head", "audit"])
async def test_invalid_or_failed_review_leaves_no_approval(
  sessions, review, monkeypatch, damage
):
  args = dict(review)
  if damage == "account":
    args["account_id"] = "another"
  elif damage == "window":
    args["now"] = args["window_end"]
  elif damage == "config":
    args["expected_config_hash"] = "0" * 64
  elif damage == "head":
    args["expected_head_version"] = 2
  else:
    original = TAssistantExecutionRepository.append_event

    async def fail(self, event):
      await original(self, event)
      raise RuntimeError("audit failure")

    monkeypatch.setattr(TAssistantExecutionRepository, "append_event", fail)
  async with sessions() as db, db.begin():
    with pytest.raises((ValueError, RuntimeError)):
      await record_live_release_approval(db, **args)
    assert (
      await db.scalar(
        select(TAssistantExecutionEventRecord).where(
          TAssistantExecutionEventRecord.event_key == "review-1"
        )
      )
      is None
    )
    assert (await db.get(TTradeGlobalConfig, "config-1")).desired_environment == "PAPER"
