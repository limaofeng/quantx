from dataclasses import asdict
from datetime import timedelta

import pytest
from quantx_domain.trading.t_assistant_execution import (
  TAssistantConfigVersion,
  TAssistantExecutionEvent,
)
from quantx_engine.t_assistant_live_admission import prepare_live_canary_execution
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantExecutionEventRecord,
  TAssistantExecutionRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.repositories.t_assistant_config_repository import (
  TAssistantConfigRepository,
)
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from sqlalchemy import select

from tests.infrastructure.test_t_assistant_runtime_repository import (
  NOW,
  _seed_execution,
  sessions,
)

_FIXTURES = sessions


async def seed(sessions, **approval_changes):
  old, source = await _seed_execution(sessions)
  values = asdict(old)
  values.pop("config_snapshot_hash")
  values.update(config_version_id="config-2", version=2)
  values["canonical_payload"].update(
    legacy_settings_snapshot={"entry_authorization": "MANUAL_CONFIRM"},
    universe_policy={"allowed_stock_codes": ["600000.SH"], "ignored_stock_codes": []},
    portfolio_policy={"max_total_t_amount": "1000"},
  )
  version = TAssistantConfigVersion.create(**values)
  evidence = dict(
    account_id="account-1",
    config_version_id=version.config_version_id,
    config_snapshot_hash=version.config_snapshot_hash,
    p5_outcome="PASSED",
    p5_evidence_hash="a" * 64,
    actor_id="synthetic-operator",
    allowed_stock_codes=["600000.SH"],
    max_total_t_amount="1000",
    window_start=NOW.isoformat(),
    window_end=(NOW + timedelta(minutes=10)).isoformat(),
  )
  evidence.update(approval_changes)
  async with sessions() as db, db.begin():
    head = await db.get(TTradeGlobalConfig, "config-1")
    head.active_config_version_id = old.config_version_id
    await TAssistantConfigRepository(db).append_version(version)
    await TAssistantExecutionRepository(db).append_event(
      TAssistantExecutionEvent(
        source, "approve-1", "LIVE_CANARY_RELEASE_APPROVED", NOW, evidence
      )
    )
  return source


async def prepare(db, source, **changes):
  args = dict(
    source_execution_id=source,
    config_version_id="config-2",
    approval_event_key="approve-1",
    expected_head_version=1,
    now=NOW + timedelta(seconds=1),
  )
  args.update(changes)
  return await prepare_live_canary_execution(db, **args)


async def test_preparation_is_atomic_warming_and_exactly_idempotent(sessions):
  source = await seed(sessions)
  async with sessions() as db, db.begin():
    identity = await prepare(db, source)
  async with sessions() as db, db.begin():
    replay = await prepare(db, source, now=NOW + timedelta(hours=1))
    assert replay == identity
    head = await db.get(TTradeGlobalConfig, "config-1")
    assert head.desired_environment == "LIVE" and head.strategy_run_id is None
    row = await db.get(TAssistantExecutionRecord, identity)
    assert row.environment == "LIVE" and row.status == "WARMING"
    assert (
      row.entry_readiness == "WARMING" and row.entry_authorization == "MANUAL_CONFIRM"
    )
    assert (await db.get(TAssistantExecutionRecord, source)).environment == "PAPER"
    events = list(
      (
        await db.scalars(
          select(TAssistantExecutionEventRecord).where(
            TAssistantExecutionEventRecord.execution_id == identity
          )
        )
      ).all()
    )
    assert len(events) == 1 and events[0].payload["allowed_stock_codes"] == [
      "600000.SH"
    ]


@pytest.mark.parametrize(
  "change",
  [
    {"p5_outcome": "UNCONFIRMED"},
    {"p5_evidence_hash": "bad"},
    {"actor_id": ""},
    {"config_snapshot_hash": "b" * 64},
    {"allowed_stock_codes": ["000001.SZ"]},
    {"max_total_t_amount": "500"},
    {"window_end": NOW.isoformat()},
  ],
)
async def test_inexact_approval_does_not_change_head_or_create_live_source(
  sessions, change
):
  source = await seed(sessions, **change)
  async with sessions() as db, db.begin():
    with pytest.raises(ValueError):
      await prepare(db, source)
    head = await db.get(TTradeGlobalConfig, "config-1")
    assert (
      head.desired_environment == "PAPER"
      and head.active_config_version_id == "config-version-1"
    )
    assert not list(
      (
        await db.scalars(
          select(TAssistantExecutionRecord).where(
            TAssistantExecutionRecord.environment == "LIVE"
          )
        )
      ).all()
    )


async def test_late_audit_failure_rolls_back_even_if_caller_catches(
  sessions, monkeypatch
):
  source = await seed(sessions)

  async def fail(*args, **kwargs):
    raise RuntimeError("audit unavailable")

  monkeypatch.setattr(TAssistantExecutionRepository, "append_event", fail)
  async with sessions() as db, db.begin():
    with pytest.raises(RuntimeError, match="audit unavailable"):
      await prepare(db, source)
    assert (await db.get(TTradeGlobalConfig, "config-1")).desired_environment == "PAPER"
    assert not list(
      (
        await db.scalars(
          select(TAssistantExecutionRecord).where(
            TAssistantExecutionRecord.environment == "LIVE"
          )
        )
      ).all()
    )


@pytest.mark.parametrize("damage", ["legacy", "version", "expired"])
async def test_current_head_and_maintenance_window_are_required(sessions, damage):
  source = await seed(sessions)
  async with sessions() as db, db.begin():
    head = await db.get(TTradeGlobalConfig, "config-1")
    if damage == "legacy":
      head.strategy_run_id = "legacy"
    elif damage == "version":
      head.state_version += 1
    await db.flush()
    with pytest.raises(ValueError):
      await prepare(
        db,
        source,
        now=NOW + timedelta(hours=1)
        if damage == "expired"
        else NOW + timedelta(seconds=1),
      )


async def test_retry_does_not_accept_changed_source_authorization(sessions):
  source = await seed(sessions)
  async with sessions() as db, db.begin():
    identity = await prepare(db, source)
  async with sessions() as db, db.begin():
    row = await db.get(TAssistantExecutionRecord, identity)
    row.entry_authorization = "AUTO"
  async with sessions() as db, db.begin():
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
      await prepare(db, source)


@pytest.mark.parametrize("damage", [None, "account", "approval", "injected", "version"])
async def test_engine_preparation_command_binds_existing_approval(
  sessions, monkeypatch, damage
):
  import quantx_engine.command_processor as processor
  from quantx_domain.trading.t_assistant_execution import stable_manifest_hash

  source = await seed(sessions)
  async with sessions() as db:
    approval = await db.scalar(
      select(TAssistantExecutionEventRecord).where(
        TAssistantExecutionEventRecord.execution_id == source,
        TAssistantExecutionEventRecord.event_key == "approve-1",
      )
    )
    approval_hash = stable_manifest_hash(approval.payload)
  payload = dict(
    account_id="account-1",
    source_execution_id=source,
    config_version_id="config-2",
    approval_event_key="approve-1",
    approval_hash=approval_hash,
    expected_head_version=1,
  )
  if damage == "account":
    payload["account_id"] = "another-account"
  elif damage == "approval":
    payload["approval_hash"] = "0" * 64
  elif damage == "injected":
    payload["p5_outcome"] = "PASSED"
  elif damage == "version":
    payload["expected_head_version"] = True
  monkeypatch.setattr(processor, "AsyncSessionLocal", sessions)
  monkeypatch.setattr(processor, "utcnow", lambda: NOW + timedelta(seconds=1))

  async def dispatch():
    return await processor._dispatch(
      "T_ASSISTANT_PREPARE_LIVE_CANARY", payload, command_id="release-1"
    )

  if damage:
    with pytest.raises(ValueError):
      await dispatch()
    async with sessions() as db:
      assert (
        await db.get(TTradeGlobalConfig, "config-1")
      ).desired_environment == "PAPER"
      assert (
        await db.scalar(
          select(TAssistantExecutionRecord.execution_id).where(
            TAssistantExecutionRecord.environment == "LIVE"
          )
        )
        is None
      )
  else:
    result = await dispatch()
    assert await dispatch() == result
    async with sessions() as db:
      row = await db.get(TAssistantExecutionRecord, result["execution_id"])
      assert result["success"] and row.status == "WARMING"
