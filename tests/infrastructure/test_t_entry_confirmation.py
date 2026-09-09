"""Consumed device approval must enter a fresh, fenced allocation attempt."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from quantx_contracts import ExecutionEnvironment
from quantx_infrastructure.core.utils.time_utils import to_shanghai
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  OrderCorrelation,
  PendingTradeOrder,
)
from quantx_infrastructure.models.auth import AuthDeviceSession, AuthUser
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantExecutionEventRecord,
  TAssistantExecutionRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_allocation_repository import (
  TAllocationRepository,
)
from quantx_infrastructure.services import (
  exit_plan_authorization_service as authorization,
)
from quantx_infrastructure.services.t_entry_confirmation import confirm_live_entry
from quantx_infrastructure.services.trade_confirmation_material import (
  intent_fingerprint,
)
from sqlalchemy import select

from tests.infrastructure.test_t_allocation_repository import (
  NOW,
  _claim,
  _prepared,
  _seed,
)
from tests.infrastructure.test_t_allocation_repository import (
  base_sessions as _base_sessions,
)
from tests.infrastructure.test_t_allocation_repository import (
  sessions as _allocation_sessions,
)
from tests.infrastructure.test_t_trade_exit_authorization_derivation import (
  _exit_template,
)

allocation_sessions = _allocation_sessions
base_sessions = _base_sessions

AUDIT = {
  "challenge_id": "challenge-1",
  "actor_id": "user-1",
  "device_session_id": "session-1",
}
CONFIRMED = NOW + timedelta(seconds=1)


@pytest.fixture(autouse=True)
def signing_key(monkeypatch):
  monkeypatch.setattr(
    authorization,
    "settings",
    SimpleNamespace(
      secret_key="test-t-entry-confirmation-signing-key-at-least-32",
      algorithm="HS256",
    ),
  )


@pytest.fixture
async def sessions(allocation_sessions):
  async with allocation_sessions.kw["bind"].begin() as connection:
    await connection.run_sync(
      lambda sync: Base.metadata.create_all(
        sync,
        tables=[
          AuthUser.__table__,
          AuthDeviceSession.__table__,
          TradeConfirmationChallenge.__table__,
          PendingTradeOrder.__table__,
          OrderCorrelation.__table__,
        ],
      )
    )
  return allocation_sessions


def enrich(intent):
  template = _exit_template()
  template.update(account_id="account-1", run_id="")
  template["metadata"].pop("strategy_run_id")
  template["metadata"].update(account_id="account-1")
  for key in (
    "source_execution_ref",
    "candidate_id",
    "candidate_fingerprint",
    "policy_version",
    "feature_schema_version",
  ):
    template["metadata"][key] = intent.metadata[key]
  intent.limit_price_hint = 9.9
  intent.metadata.update(
    t_batch_id=template["source_id"],
    exit_plan_id=template["plan_id"],
    exit_plan_template=template,
    max_price_deviation_bps=30,
  )


async def seed_confirmable(sessions):
  snapshot, candidates = await _seed(
    sessions, environment=ExecutionEnvironment.LIVE, enrich_intent=enrich
  )
  async with sessions() as db, db.begin():
    repo = TAllocationRepository(db)
    batch = await _prepared(repo, snapshot, candidates)
    claim = await _claim(repo, batch, snapshot, candidates)
    await repo.commit(claim=claim, snapshot=snapshot, candidates=candidates, now=NOW)
    head = await db.get(TTradeGlobalConfig, "config-1")
    head.desired_environment = "LIVE"
    head.state_version += 1
    head.active_config_version_id = snapshot.config_version
    db.add(
      AuthUser(
        id="user-1", username="user", display_name="User", password_hash="fixture"
      )
    )
    await db.flush()
    db.add(
      AuthDeviceSession(
        id="session-1",
        user_id="user-1",
        refresh_token_hash="r" * 64,
        expires_at=to_shanghai(NOW + timedelta(hours=1)),
        last_used_at=to_shanghai(NOW),
      )
    )
    await db.flush()
    intent = await db.get(TradeIntentRecord, "intent-0")
    # Keep the durable arrival clock on the fixture's explicit historical clock.
    intent.created_at = NOW.replace(tzinfo=None)
    intent.updated_at = CONFIRMED.replace(tzinfo=None)
    payload = authorization.bind_t_trade_exit_authorization_to_challenge_payload(
      {
        "action": "T_TRADE_ENTRY_APPROVAL",
        "user_id": "user-1",
        "device_session_id": "session-1",
        "account_id": "account-1",
        "owner_type": intent.owner_type,
        "owner_id": intent.owner_id,
        "environment": "LIVE",
        "intent_id": intent.id,
        "intent_fingerprint": intent_fingerprint(intent),
      },
      intent,
    )
    db.add(
      TradeConfirmationChallenge(
        id="challenge-1",
        action=payload["action"],
        user_id="user-1",
        device_session_id="session-1",
        account_id="account-1",
        owner_type=intent.owner_type,
        owner_id=intent.owner_id,
        environment="LIVE",
        idempotency_key="confirm-1",
        payload=payload,
        payload_fingerprint=authorization.trade_confirmation_payload_fingerprint(
          payload
        ),
        token_digest="t" * 64,
        expires_at=to_shanghai(NOW + timedelta(seconds=60)),
        consumed_at=to_shanghai(CONFIRMED),
      )
    )
  return snapshot, candidates


async def confirm(db, **kwargs):
  return await confirm_live_entry(
    db,
    execution_id="live-fixture",
    intent_id="intent-0",
    account_id="account-1",
    approval_audit=AUDIT,
    now=CONFIRMED,
    **kwargs,
  )


def fresh(snapshot):
  cut = replace(
    snapshot.cut,
    as_of=CONFIRMED,
    account_snapshot_as_of=CONFIRMED,
    obligations_as_of=CONFIRMED,
    account_snapshot_id="snapshot-after-confirmation",
  )
  return replace(
    snapshot,
    cut=cut,
    envelopes=tuple(replace(e, cut=cut) for e in snapshot.envelopes),
    available_cash=Decimal(500),
  )


async def test_confirmation_reallocates_reduced_cash_and_replays(sessions):
  snapshot, candidates = await seed_confirmable(sessions)
  async with sessions() as db, db.begin():
    result = await confirm(db)
    assert result["outcome"] == "REALLOCATION_REQUESTED"
    assert (await db.get(TradeIntentRecord, "intent-0")).status == "ALLOCATION_PENDING"
    row = await db.get(TradeIntentRecord, "intent-0")
    await db.refresh(row)
    assert row.updated_at.replace(tzinfo=NOW.tzinfo) == CONFIRMED
  async with sessions() as db, db.begin():
    assert await confirm(db) == result
    repo = TAllocationRepository(db)
    newer = fresh(snapshot)
    candidates = tuple(replace(c, intent_version=1) for c in candidates)
    batch = await _prepared(repo, newer, candidates, now=CONFIRMED)
    claim = await _claim(repo, batch, newer, candidates, now=CONFIRMED)
    await repo.commit(claim=claim, snapshot=newer, candidates=candidates, now=CONFIRMED)
    decisions = await repo.list_decisions(batch.allocation_batch_id)
    assert decisions[0].allocated_amount_cap == 500
    assert batch.allocation_attempt == 2
    row = await db.get(TradeIntentRecord, "intent-0")
    assert row.status == "EXECUTION_READY" and row.allocation_version == 2
    await db.refresh(row)
    assert row.updated_at.replace(tzinfo=NOW.tzinfo) == CONFIRMED
    assert row.target_amount == 1000


@pytest.mark.parametrize(
  "kind", ["unconsumed", "wrong_actor", "tampered", "draining", "expired"]
)
async def test_invalid_confirmation_leaves_original_allocation(sessions, kind):
  await seed_confirmable(sessions)
  async with sessions() as db, db.begin():
    challenge = await db.get(TradeConfirmationChallenge, "challenge-1")
    if kind == "unconsumed":
      challenge.consumed_at = None
    if kind == "wrong_actor":
      challenge.user_id = "other-user"
    if kind == "tampered":
      challenge.payload_fingerprint = "0" * 64
    if kind == "expired":
      challenge.expires_at = to_shanghai(CONFIRMED)
    if kind == "draining":
      source = await db.get(TAssistantExecutionRecord, "live-fixture")
      source.status = "DRAINING"
      source.entry_readiness = "DRAINING"
      source.entry_readiness_reasons = ["DRAINING"]
    await db.flush()
    with pytest.raises(ValueError):
      await confirm(db)
    row = await db.get(TradeIntentRecord, "intent-0")
    assert row.status == "AWAITING_APPROVAL" and row.allocation_version == 1
    assert not await db.scalar(
      select(TAssistantExecutionEventRecord).where(
        TAssistantExecutionEventRecord.event_type == "LIVE_ENTRY_CONFIRMED"
      )
    )


@pytest.mark.parametrize(
  "stale_field", ["as_of", "account_snapshot_as_of", "obligations_as_of"]
)
async def test_confirmation_rejects_any_pre_confirmation_fact(sessions, stale_field):
  snapshot, candidates = await seed_confirmable(sessions)
  async with sessions() as db, db.begin():
    await confirm(db)
  newer = fresh(snapshot)
  cut = (
    replace(newer.cut, as_of=NOW, account_snapshot_as_of=NOW, obligations_as_of=NOW)
    if stale_field == "as_of"
    else replace(newer.cut, **{stale_field: NOW})
  )
  newer = replace(
    newer, cut=cut, envelopes=tuple(replace(e, cut=cut) for e in newer.envelopes)
  )
  async with sessions() as db, db.begin():
    with pytest.raises(ValueError, match="T_ENTRY_POST_CONFIRMATION_SNAPSHOT_REQUIRED"):
      await _prepared(
        TAllocationRepository(db),
        newer,
        tuple(replace(c, intent_version=1) for c in candidates),
        now=CONFIRMED,
      )


async def test_engine_command_uses_consumed_challenge_and_transaction(
  sessions, monkeypatch
):
  import quantx_engine.command_processor as processor

  await seed_confirmable(sessions)
  monkeypatch.setattr(processor, "AsyncSessionLocal", sessions)
  monkeypatch.setattr(processor, "utcnow", lambda: CONFIRMED.replace(tzinfo=None))
  result = await processor._dispatch(
    "T_ASSISTANT_APPROVE_ENTRY",
    {
      "execution_id": "live-fixture",
      "intent_id": "intent-0",
      "account_id": "account-1",
      "approval_audit": AUDIT,
    },
  )
  assert result["success"] and result["outcome"] == "REALLOCATION_REQUESTED"
  async with sessions() as db:
    row = await db.get(TradeIntentRecord, "intent-0")
    assert row.status == "ALLOCATION_PENDING" and row.allocation_version == 1


async def test_disabled_head_blocks_post_confirmation_allocation(sessions):
  snapshot, candidates = await seed_confirmable(sessions)
  async with sessions() as db, db.begin():
    await confirm(db)
    head = await db.get(TTradeGlobalConfig, "config-1")
    head.enabled = False
  async with sessions() as db, db.begin():
    with pytest.raises(RuntimeError, match="T_ALLOCATION_CONFIG_HEAD_INVALID"):
      await _prepared(
        TAllocationRepository(db),
        fresh(snapshot),
        tuple(replace(c, intent_version=1) for c in candidates),
        now=CONFIRMED,
      )


async def test_confirmed_operation_replays_after_execution_metadata_changes(sessions):
  await seed_confirmable(sessions)
  async with sessions() as db, db.begin():
    original = await confirm(db)
  async with sessions() as db, db.begin():
    row = await db.get(TradeIntentRecord, "intent-0")
    row.status = "CANCELLED"
    row.intent_metadata = {
      **row.intent_metadata,
      "execution_audit": {"result": "cancelled"},
    }
    source = await db.get(TAssistantExecutionRecord, "live-fixture")
    source.status = "DRAINING"
    source.entry_readiness = "DRAINING"
  async with sessions() as db, db.begin():
    assert await confirm(db) == original
    assert (await db.get(TradeIntentRecord, "intent-0")).status == "CANCELLED"


async def test_confirmation_audit_failure_rolls_back_even_when_caller_catches(
  sessions, monkeypatch
):
  from quantx_infrastructure.repositories.t_assistant_execution_repository import (
    TAssistantExecutionRepository,
  )

  await seed_confirmable(sessions)
  original = TAssistantExecutionRepository.append_event

  async def fail(self, event):
    await original(self, event)
    raise RuntimeError("INJECTED_AUDIT_FAILURE")

  monkeypatch.setattr(TAssistantExecutionRepository, "append_event", fail)
  async with sessions() as db, db.begin():
    with pytest.raises(RuntimeError, match="INJECTED_AUDIT_FAILURE"):
      await confirm(db)
  async with sessions() as db:
    assert (await db.get(TradeIntentRecord, "intent-0")).status == "AWAITING_APPROVAL"
    assert not await db.scalar(
      select(TAssistantExecutionEventRecord).where(
        TAssistantExecutionEventRecord.event_type == "LIVE_ENTRY_CONFIRMED"
      )
    )


async def test_api_preview_outbox_engine_and_reallocation(sessions, monkeypatch):
  import quantx_engine.command_processor as processor
  from quantx_api.gqlapi import trade_approval as api
  from quantx_contracts import ExecutionOwnerRef, ExecutionOwnerType
  from quantx_infrastructure.models.agent_runtime import EngineCommandOutbox

  from tests.api.unit.gqlapi.test_trade_approval_challenge import _principal

  snapshot, candidates = await seed_confirmable(sessions)
  async with sessions.kw["bind"].begin() as connection:
    await connection.run_sync(lambda sync: Base.metadata.create_all(
      sync, tables=[EngineCommandOutbox.__table__]))
  async with sessions() as db, db.begin():
    old = await db.get(TradeConfirmationChallenge, "challenge-1")
    await db.delete(old)

  async def database():
    async with sessions() as db:
      yield db

  monkeypatch.setattr(api, "get_async_db", database)
  monkeypatch.setattr(api, "settings", authorization.settings)
  monkeypatch.setattr(api.time_utils, "now", lambda: to_shanghai(CONFIRMED))
  monkeypatch.setattr(processor, "AsyncSessionLocal", sessions)
  monkeypatch.setattr(processor, "utcnow", lambda: CONFIRMED.replace(tzinfo=None))
  args = dict(principal=_principal(device_session_id="session-1", authorized_account_ids=("account-1",)),
    action=api.T_TRADE_ENTRY_APPROVAL, account_id="account-1", intent_id="intent-0",
    execution_ref=ExecutionOwnerRef(ExecutionOwnerType.T_ASSISTANT_EXECUTION, "live-fixture"),
    environment=ExecutionEnvironment.LIVE)
  preview = await api.TradeApprovalChallengeService.issue(**args)
  command = dict(command_type="T_ASSISTANT_APPROVE_ENTRY", command_aggregate_id="live-fixture",
    command_idempotency_key_factory=lambda challenge_id: f"t-confirm:{challenge_id}",
    command_payload={"execution_id": "live-fixture", "intent_id": "intent-0", "account_id": "account-1"})
  consumed = await api.TradeApprovalChallengeService.consume(**args,
    confirmation_token=preview.confirmation_token, **command)
  assert consumed == preview.challenge_id
  async with sessions() as db:
    commands = (await db.scalars(select(EngineCommandOutbox))).all()
    assert len(commands) == 1
    assert commands[0].payload["approval_audit"]["challenge_id"] == consumed
    result = await processor._dispatch(commands[0].command_type, commands[0].payload)
    assert result["outcome"] == "REALLOCATION_REQUESTED"
  async with sessions() as db, db.begin():
    newer = fresh(snapshot)
    candidates = tuple(replace(c, intent_version=1) for c in candidates)
    repo = TAllocationRepository(db)
    batch = await _prepared(repo, newer, candidates, now=CONFIRMED)
    claim = await _claim(repo, batch, newer, candidates, now=CONFIRMED)
    await repo.commit(claim=claim, snapshot=newer, candidates=candidates, now=CONFIRMED)
    assert (await db.get(TradeIntentRecord, "intent-0")).status == "EXECUTION_READY"
  replay = await api.TradeApprovalChallengeService.consume(**args,
    confirmation_token=preview.confirmation_token, **command)
  assert replay == consumed
