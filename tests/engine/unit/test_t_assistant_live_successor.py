"""Synthetic approval fixtures only; never authorize an actual AUTO release."""

from dataclasses import asdict
from datetime import timedelta

import pytest
from quantx_domain.trading.t_assistant_execution import (
  TAssistantConfigVersion,
  TAssistantExecutionEvent,
)
from quantx_engine.t_assistant_live_successor import prepare_live_auto_successor
from quantx_infrastructure.models.agent_runtime import (
  PendingTradeOrder,
  TradeCommandOutbox,
)
from quantx_infrastructure.models.t_assistant_execution import TAssistantExecutionRecord
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_assistant_config_repository import (
  TAssistantConfigRepository,
)
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from sqlalchemy import func, select

from tests.engine.unit.test_t_assistant_live_drain import (
  base_sessions as _base_sessions,
)
from tests.engine.unit.test_t_assistant_live_drain import sessions as _drain_sessions
from tests.infrastructure.test_t_assistant_runtime_repository import NOW, _version

base_sessions = _base_sessions
drain_sessions = _drain_sessions


@pytest.fixture
async def sessions(drain_sessions):
  async with drain_sessions() as db, db.begin():
    head = await db.get(TTradeGlobalConfig, "config-1")
    head.enabled = True
    head.desired_environment = "LIVE"
    head.active_config_version_id = "config-version-1"
    head.updated_at = NOW.replace(tzinfo=None)
    data = asdict(_version("config-1"))
    data.pop("config_snapshot_hash")
    data.update(
      config_version_id="config-version-2", version=2, entry_authorization="AUTO"
    )
    data["canonical_payload"].update(
      legacy_settings_snapshot={"entry_authorization": "AUTO"},
      universe_policy={"ignored_stock_codes": []},
    )
    version = TAssistantConfigVersion.create(**data)
    await TAssistantConfigRepository(db).append_version(version)
    await TAssistantExecutionRepository(db).append_event(
      TAssistantExecutionEvent(
        "live-1",
        "release-1",
        "AUTO_RELEASE_APPROVED",
        NOW,
        {
          "account_id": "account-1",
          "config_version_id": version.config_version_id,
          "config_snapshot_hash": version.config_snapshot_hash,
          "p6_outcome": "PASSED",
          "p6_evidence_hash": "a" * 64,
          "actor_id": "synthetic-operator",
          "auto_observation_policy_version": "fixture-only",
        },
      )
    )
  return drain_sessions


async def prepare(db, **overrides):
  args = dict(
    predecessor_id="live-1",
    config_version_id="config-version-2",
    approval_event_key="release-1",
    expected_head_version=1,
    now=NOW + timedelta(seconds=2),
  )
  args.update(overrides)
  return await prepare_live_auto_successor(db, **args)


async def test_successor_is_warming_and_keeps_original_order_ownership(sessions):
  async with sessions() as db, db.begin():
    identity = await prepare(db)
  async with sessions() as db, db.begin():
    assert await prepare(db) == identity
    head = await db.get(TTradeGlobalConfig, "config-1")
    assert (head.active_config_version_id, head.state_version) == (
      "config-version-2",
      2,
    )
    old = await db.get(TAssistantExecutionRecord, "live-1")
    new = await db.get(TAssistantExecutionRecord, identity)
    assert (old.status, old.entry_authorization) == ("DRAINING", "MANUAL_CONFIRM")
    assert (new.status, new.entry_readiness, new.entry_authorization) == (
      "WARMING",
      "WARMING",
      "AUTO",
    )
    assert "T_PREDECESSOR_RECONCILIATION_REQUIRED" in new.entry_readiness_reasons
    assert (await db.get(TradeIntentRecord, "unsubmitted")).status == "CANCELLED"
    assert (await db.get(PendingTradeOrder, "client-1")).owner_id == "live-1"
    assert (await db.get(TradeCommandOutbox, "message-1")).payload == {
      "immutable": "original"
    }
    assert (
      await db.scalar(select(func.count()).select_from(TAssistantExecutionRecord)) == 2
    )


@pytest.mark.parametrize(
  "overrides,reason",
  [
    ({"approval_event_key": "missing"}, "EXACT_RELEASE_APPROVAL"),
    ({"expected_head_version": 9}, "HEAD_CHANGED"),
    ({"expected_head_version": True}, "HEAD_VERSION_REQUIRED"),
    ({"config_version_id": "config-version-1"}, "CONFIG_SCOPE_INVALID"),
  ],
)
async def test_missing_approval_or_stale_configuration_preserves_source(
  sessions, overrides, reason
):
  async with sessions() as db, db.begin():
    with pytest.raises(ValueError, match=reason):
      await prepare(db, **overrides)
  async with sessions() as db:
    assert (await db.get(TAssistantExecutionRecord, "live-1")).status == "RUNNING"
    assert (
      await db.get(TTradeGlobalConfig, "config-1")
    ).active_config_version_id == "config-version-1"


async def test_late_failure_rolls_back_head_drain_and_successor_even_when_caught(
  sessions, monkeypatch
):
  original = TAssistantExecutionRepository.append_event

  async def fail(self, event):
    if event.event_type == "LIVE_AUTO_SUCCESSOR_PREPARED":
      raise RuntimeError("injected")
    return await original(self, event)

  monkeypatch.setattr(TAssistantExecutionRepository, "append_event", fail)
  async with sessions() as db, db.begin():
    with pytest.raises(RuntimeError, match="injected"):
      await prepare(db)
  async with sessions() as db:
    assert (await db.get(TAssistantExecutionRecord, "live-1")).status == "RUNNING"
    assert (
      await db.get(TradeIntentRecord, "unsubmitted")
    ).status == "AWAITING_APPROVAL"
    assert (
      await db.get(TTradeGlobalConfig, "config-1")
    ).active_config_version_id == "config-version-1"
    assert (
      await db.scalar(select(func.count()).select_from(TAssistantExecutionRecord)) == 1
    )
