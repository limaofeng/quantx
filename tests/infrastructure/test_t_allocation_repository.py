"""SQLite transactional behavior; PostgreSQL concurrency is validated separately."""

from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal as D

import pytest
from quantx_application.t_trade_v3.portfolio_allocation import TAllocationCandidate
from quantx_application.t_trade_v3.portfolio_snapshot import (
  IndustryTExposure,
  PortfolioEvidenceCut,
  PortfolioTDecisionSnapshot,
  TEnvelopePosition,
  TPortfolioPolicy,
  TTradingEnvelopePolicy,
  build_t_trading_envelope,
)
from quantx_contracts import ExecutionEnvironment
from quantx_domain.trading.t_assistant_execution import (
  TAssistantConfigVersion,
  TAssistantExecutionEvent,
)
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.t_allocation import (
  TAllocationBatchRecord,
  TAllocationDecisionRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_allocation_repository import (
  TAllocationConflict,
  TAllocationRepository,
)
from quantx_infrastructure.repositories.t_assistant_config_repository import (
  TAssistantConfigRepository,
)
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from tests.infrastructure.test_t_assistant_runtime_repository import (
  NOW,
  _version,
)
from tests.infrastructure.test_t_assistant_runtime_repository import (
  sessions as _base_sessions,
)
from tests.infrastructure.test_t_intent_atomic_intake import _intent, _prepare

base_sessions = _base_sessions


@pytest.fixture
async def sessions(base_sessions):
  async with base_sessions.kw["bind"].begin() as connection:
    await connection.run_sync(
      lambda sync: Base.metadata.create_all(
        sync,
        tables=[
          TAllocationBatchRecord.__table__,
          TAllocationDecisionRecord.__table__,
        ],
      )
    )
  return base_sessions


async def _seed(
  sessions,
  *,
  count=1,
  authorization="MANUAL_CONFIRM",
  source_age_seconds=0,
  enrich_intent=None,
):
  version_fields = asdict(_version("config-1"))
  version_fields.pop("config_snapshot_hash")
  version_fields["entry_authorization"] = authorization
  version = TAssistantConfigVersion.create(**version_fields)
  async with sessions() as db:
    async with db.begin():
      db.add(
        TTradeGlobalConfig(
          id="config-1", account_id="account-1", enabled=True, mode="paper"
        )
      )
      await TAssistantConfigRepository(db).append_version(version)
      owner = await TAssistantExecutionRepository(db).ensure_paper_shadow(
        account_id="account-1",
        version=version,
        now=NOW,
      )
      execution_id = owner.execution_id
      execution_repository = TAssistantExecutionRepository(db)
      warming = await execution_repository.get_domain(execution_id)
      await execution_repository.save_transition_with_event(
        warming.activate_ready(at=NOW),
        expected_state_version=warming.state_version,
        event=TAssistantExecutionEvent(
          execution_id, "fixture-ready", "EXECUTION_READY", NOW, {}
        ),
      )
  async with sessions() as db:
    async with db.begin():
      execution, repository, kwargs = await _prepare(db, execution_id)
      intents, evidence, candidates = [], [], []
      for index in range(count):
        intent = _intent(execution, intent_id=f"intent-{index}")
        identity, fingerprint = f"candidate-{index}", f"fingerprint-{index}"
        intent.origin = replace(
          intent.origin, candidate_id=identity, opportunity_id=identity
        )
        intent.approval_ttl_ms = 60_000
        intent.metadata.update(
          candidate_id=identity,
          candidate_fingerprint=fingerprint,
          source_time_ms=int(
            (NOW - timedelta(seconds=source_age_seconds)).timestamp() * 1000
          ),
          opportunity_score=90 - index,
        )
        if enrich_intent is not None:
          enrich_intent(intent)
        intents.append(intent)
        original = kwargs["opportunity_evidence"][0]
        evidence.append(
          {
            **original,
            "event_key": f"intake-evidence-{index}",
            "payload": {
              **original["payload"],
              "signal_snapshot": {
                "candidate_id": identity,
                "candidate_fingerprint": fingerprint,
              },
            },
          }
        )
        candidates.append(
          TAllocationCandidate(
            intent.intent_id,
            0,
            identity,
            fingerprint,
            intent.instrument_code,
            D(90 - index) / 100,
            D(90 - index),
            D(1),
            NOW - timedelta(seconds=source_age_seconds),
            NOW + timedelta(seconds=60 - source_age_seconds),
            D(1000),
            D(100),
            100,
            True,
          )
        )
      kwargs["opportunity_evidence"] = evidence
      await repository.commit_material_cycle(**kwargs, trade_intents=intents)
      version_id = version.config_version_id
  cut = PortfolioEvidenceCut(
    execution.execution_ref,
    ExecutionEnvironment.PAPER,
    NOW,
    "snapshot",
    "a" * 64,
    NOW,
    "watermark",
    NOW,
    True,
  )
  envelope = build_t_trading_envelope(
    cut=cut,
    config_version=version_id,
    policy=TTradingEnvelopePolicy("envelope-v1", 0, D(10000), 1000),
    position=TEnvelopePosition(
      "600000.SH", "bank", 0, 0, 1000, 1000, 0, D(0), D(0), False
    ),
  )
  snapshot = PortfolioTDecisionSnapshot(
    cut,
    "intake-cycle",
    version_id,
    execution.policy_version,
    "RULE_ONLY",
    TPortfolioPolicy("portfolio-v1", D(10000), D(1), D(0), D(10000), 3, D(1000)),
    (envelope,),
    (IndustryTExposure("bank", D(0), D(0)),),
    D(10000),
    D(10000),
    D(0),
    D(0),
    D(0),
    D(0),
    0,
    True,
    False,
    False,
  )
  return snapshot, tuple(candidates)


async def _prepared(repository, snapshot, candidates, *, now=NOW, ttl=10):
  return await repository.prepare(
    snapshot=snapshot,
    candidates=candidates,
    now=now,
    expires_at=now + timedelta(seconds=ttl),
  )


async def _claim(
  repository, batch, snapshot, candidates, *, now=NOW, owner="worker", seconds=5
):
  return await repository.claim(
    allocation_batch_id=batch.allocation_batch_id,
    processing_owner=owner,
    snapshot=snapshot,
    candidates=candidates,
    now=now,
    lease_seconds=seconds,
  )


@pytest.mark.parametrize(
  "authorization,status",
  [("MANUAL_CONFIRM", "AWAITING_APPROVAL"), ("AUTO", "EXECUTION_READY")],
)
async def test_prepare_reuse_and_complete_standard_intent_transition(
  sessions, authorization, status
):
  snapshot, candidates = await _seed(sessions, authorization=authorization)
  async with sessions() as db:
    async with db.begin():
      repository = TAllocationRepository(db)
      batch = await _prepared(repository, snapshot, candidates)
      assert await _prepared(repository, snapshot, candidates) is batch
      assert batch.allocation_attempt == 1
      assert (
        batch.intent_manifest[0]["candidate_fingerprint"]
        == candidates[0].candidate_fingerprint
      )
      assert batch.portfolio_snapshot["cut"]["account_snapshot_id"] == "snapshot"
      claim = await _claim(repository, batch, snapshot, candidates)
      await repository.commit(
        claim=claim, snapshot=snapshot, candidates=candidates, now=NOW
      )
  async with sessions() as db:
    row = await db.get(TradeIntentRecord, candidates[0].intent_id)
    assert (row.status, row.allocation_version) == (status, 1)
    assert (
      row.target_amount == 1000
    )  # allocation never sizes or mutates requested amount
    batch = await TAllocationRepository(db).get(batch.allocation_batch_id)
    decisions = await TAllocationRepository(db).list_decisions(
      batch.allocation_batch_id
    )
    assert batch.status == "COMMITTED" and batch.processing_owner is None
    assert len(decisions) == 1 and decisions[0].action == "ALLOW"
    assert row.allocation_decision_id == decisions[0].decision_id


async def test_lease_exclusivity_recovery_and_stale_fence(sessions):
  snapshot, candidates = await _seed(sessions)
  async with sessions() as db:
    async with db.begin():
      repository = TAllocationRepository(db)
      batch = await _prepared(repository, snapshot, candidates)
      first = await _claim(repository, batch, snapshot, candidates, seconds=1)
      with pytest.raises(TAllocationConflict, match="LEASE_CONFLICT"):
        await _claim(repository, batch, snapshot, candidates, owner="other")
      renewed = await repository.renew(
        claim=first, snapshot=snapshot, candidates=candidates, now=NOW, lease_seconds=2
      )
      assert renewed.processing_fence_token == first.processing_fence_token
  async with sessions() as db:
    async with db.begin():
      repository = TAllocationRepository(db)
      assert (
        len(
          await repository.list_recoverable(
            execution_id=snapshot.cut.execution_ref.owner_id
          )
        )
        == 1
      )
      recovered = await _claim(
        repository,
        batch,
        snapshot,
        candidates,
        now=NOW + timedelta(seconds=2),
        owner="restart",
      )
      assert recovered.processing_fence_token != first.processing_fence_token
      with pytest.raises(TAllocationConflict, match="LEASE_CONFLICT"):
        await repository.commit(
          claim=first,
          snapshot=snapshot,
          candidates=candidates,
          now=NOW + timedelta(seconds=2),
        )
      await repository.commit(
        claim=recovered,
        snapshot=snapshot,
        candidates=candidates,
        now=NOW + timedelta(seconds=2),
      )
      assert (
        await db.scalar(select(func.count(TAllocationBatchRecord.allocation_batch_id)))
        == 1
      )


async def test_expired_lease_cannot_renew_or_commit(sessions):
  snapshot, candidates = await _seed(sessions)
  async with sessions() as db:
    async with db.begin():
      repository = TAllocationRepository(db)
      batch = await _prepared(repository, snapshot, candidates)
      claim = await _claim(repository, batch, snapshot, candidates, seconds=1)
      with pytest.raises(TAllocationConflict, match="LEASE_EXPIRED"):
        await repository.renew(
          claim=claim,
          snapshot=snapshot,
          candidates=candidates,
          now=NOW + timedelta(seconds=1),
          lease_seconds=3,
        )
      with pytest.raises(TAllocationConflict, match="LEASE_EXPIRED"):
        await repository.commit(
          claim=claim,
          snapshot=snapshot,
          candidates=candidates,
          now=NOW + timedelta(seconds=1),
        )


async def test_changed_input_supersedes_then_new_attempt(sessions):
  snapshot, candidates = await _seed(sessions)
  changed = replace(snapshot, available_cash=D(500))
  async with sessions() as db:
    async with db.begin():
      repository = TAllocationRepository(db)
      first = await _prepared(repository, snapshot, candidates)
      claim = await _claim(repository, first, snapshot, candidates)
      second = await _prepared(repository, changed, candidates)
      assert first.status == "SUPERSEDED" and second.allocation_attempt == 2
      with pytest.raises(TAllocationConflict, match="LEASE_CONFLICT"):
        await repository.commit(
          claim=claim, snapshot=snapshot, candidates=candidates, now=NOW
        )
      second_claim = await _claim(repository, second, changed, candidates)
      await repository.commit(
        claim=second_claim, snapshot=changed, candidates=candidates, now=NOW
      )
      decision = (await repository.list_decisions(second.allocation_batch_id))[0]
      assert decision.action == "CAP" and decision.allocated_amount_cap == D(500)


@pytest.mark.parametrize("operation", ["claim", "commit", "expire"])
async def test_batch_ttl_terminalizes_without_intent_side_effects(sessions, operation):
  snapshot, candidates = await _seed(sessions)
  async with sessions() as db:
    async with db.begin():
      repository = TAllocationRepository(db)
      batch = await _prepared(repository, snapshot, candidates, ttl=1)
      if operation == "claim":
        assert (
          await _claim(
            repository, batch, snapshot, candidates, now=NOW + timedelta(seconds=1)
          )
          is None
        )
      elif operation == "commit":
        claim = await _claim(repository, batch, snapshot, candidates)
        await repository.commit(
          claim=claim,
          snapshot=snapshot,
          candidates=candidates,
          now=NOW + timedelta(seconds=1),
        )
      else:
        await repository.expire(
          allocation_batch_id=batch.allocation_batch_id, now=NOW + timedelta(seconds=1)
        )
      assert batch.status == "EXPIRED"
      assert (
        await db.get(TradeIntentRecord, candidates[0].intent_id)
      ).allocation_version == 0


async def test_restart_with_changed_snapshot_terminalizes_original_attempt(sessions):
  snapshot, candidates = await _seed(sessions)
  async with sessions() as db:
    async with db.begin():
      batch = await _prepared(TAllocationRepository(db), snapshot, candidates)
  async with sessions() as db:
    async with db.begin():
      repository = TAllocationRepository(db)
      assert (
        await _claim(
          repository, batch, replace(snapshot, available_cash=D(1)), candidates
        )
        is None
      )
      assert (await repository.get(batch.allocation_batch_id)).status == "SUPERSEDED"


async def test_delay_requires_new_eligible_attempt_and_version(sessions):
  snapshot, candidates = await _seed(sessions)
  unhealthy = (replace(candidates[0], data_healthy=False),)
  async with sessions() as db:
    async with db.begin():
      repository = TAllocationRepository(db)
      batch = await _prepared(repository, snapshot, unhealthy)
      claim = await _claim(repository, batch, snapshot, unhealthy)
      await repository.commit(
        claim=claim, snapshot=snapshot, candidates=unhealthy, now=NOW
      )
      row = await db.get(TradeIntentRecord, candidates[0].intent_id)
      assert row.status == "ALLOCATION_PENDING" and row.allocation_version == 1
      next_candidates = (
        replace(
          candidates[0], intent_version=1, next_eligible_at=NOW + timedelta(seconds=1)
        ),
      )
      with pytest.raises(TAllocationConflict, match="ELIGIBLE_SET"):
        await _prepared(repository, snapshot, next_candidates)
      assert await _prepared(repository, snapshot, ()) is None
      next_batch = await _prepared(
        repository, snapshot, next_candidates, now=NOW + timedelta(seconds=1)
      )
      assert next_batch.allocation_attempt == 2


async def test_all_eligible_intents_are_required_and_sorted_deterministically(sessions):
  snapshot, candidates = await _seed(sessions, count=2)
  async with sessions() as db:
    async with db.begin():
      repository = TAllocationRepository(db)
      with pytest.raises(TAllocationConflict, match="ELIGIBLE_SET"):
        await _prepared(repository, snapshot, candidates[:1])
      reversed_candidates = tuple(reversed(candidates))
      batch = await _prepared(repository, snapshot, reversed_candidates)
      assert await _prepared(repository, snapshot, candidates) is batch
      claim = await _claim(repository, batch, snapshot, candidates)
      await repository.commit(
        claim=claim, snapshot=snapshot, candidates=reversed_candidates, now=NOW
      )
      decisions = await repository.list_decisions(batch.allocation_batch_id)
      assert [decision.intent_id for decision in decisions] == [
        candidate.intent_id for candidate in candidates
      ]
      assert [decision.rank for decision in decisions] == [1, 2]
      assert [decision.action for decision in decisions] == ["ALLOW", "REJECT"]


async def test_late_flush_failure_rolls_back_whole_decision_batch(
  sessions, monkeypatch
):
  snapshot, candidates = await _seed(sessions, count=2)
  async with sessions() as db:
    async with db.begin():
      repository = TAllocationRepository(db)
      batch = await _prepared(repository, snapshot, candidates)
      claim = await _claim(repository, batch, snapshot, candidates)
    async with db.begin():
      original = db.flush

      async def fail_after_commit_flush(*args, **kwargs):
        final = batch.status == "COMMITTED"
        await original(*args, **kwargs)
        if final:
          raise RuntimeError("late whole batch failure")

      monkeypatch.setattr(db, "flush", fail_after_commit_flush)
      with pytest.raises(RuntimeError, match="whole batch"):
        await repository.commit(
          claim=claim, snapshot=snapshot, candidates=candidates, now=NOW
        )
  async with sessions() as db:
    assert (
      await db.scalar(select(func.count(TAllocationDecisionRecord.decision_id))) == 0
    )
    rows = list((await db.scalars(select(TradeIntentRecord))).all())
    assert all(
      row.status == "ALLOCATION_PENDING" and row.allocation_version == 0 for row in rows
    )
    assert (
      await TAllocationRepository(db).get(claim.allocation_batch_id)
    ).status == "PREPARED"


async def test_database_unique_prepared_attempt_guard(sessions):
  snapshot, candidates = await _seed(sessions)
  async with sessions() as db:
    async with db.begin():
      batch = await _prepared(TAllocationRepository(db), snapshot, candidates)
      fields = {
        column.name: getattr(batch, column.name) for column in batch.__table__.columns
      }
      fields.update(allocation_batch_id="duplicate", allocation_attempt=2)
      with pytest.raises(IntegrityError):
        async with db.begin_nested():
          db.add(TAllocationBatchRecord(**fields))
          await db.flush()
      assert (
        await db.scalar(select(func.count(TAllocationBatchRecord.allocation_batch_id)))
        == 1
      )


@pytest.mark.parametrize(
  "change",
  ["version", "fingerprint", "amount", "source", "expiry", "rule_score", "rank_score"],
)
async def test_candidate_binding_cannot_be_replaced(sessions, change):
  snapshot, candidates = await _seed(sessions)
  changes = {
    "version": {"intent_version": 1},
    "fingerprint": {"candidate_fingerprint": "changed"},
    "amount": {"requested_amount_ceiling": D(999)},
    "source": {"observed_at": NOW + timedelta(seconds=1)},
    "expiry": {"expires_at": NOW + timedelta(seconds=61)},
    "rule_score": {"rule_score": D(99)},
    "rank_score": {"rank_score": D(99)},
  }
  async with sessions() as db:
    async with db.begin():
      with pytest.raises(TAllocationConflict, match="CANDIDATE_BINDING"):
        await _prepared(
          TAllocationRepository(db),
          snapshot,
          (replace(candidates[0], **changes[change]),),
        )


async def test_changed_standard_intent_material_supersedes_without_decisions(sessions):
  snapshot, candidates = await _seed(sessions)
  async with sessions() as db:
    async with db.begin():
      repository = TAllocationRepository(db)
      batch = await _prepared(repository, snapshot, candidates)
      claim = await _claim(repository, batch, snapshot, candidates)
      row = await db.get(TradeIntentRecord, candidates[0].intent_id)
      row.target_amount = 999
      await db.flush()
      result = await repository.commit(
        claim=claim, snapshot=snapshot, candidates=candidates, now=NOW
      )
      assert result.status == "SUPERSEDED"
      assert row.allocation_version == 0
      assert (
        await db.scalar(select(func.count(TAllocationDecisionRecord.decision_id))) == 0
      )


async def test_cycle_intake_manifest_hash_must_match(sessions):
  snapshot, candidates = await _seed(sessions)
  async with sessions() as db:
    async with db.begin():
      from quantx_infrastructure.models.t_assistant_execution import (
        TAssistantDecisionCycleRecord,
      )

      cycle = await db.get(TAssistantDecisionCycleRecord, snapshot.cycle_id)
      cycle.output_manifest_hash = "0" * 64
      await db.flush()
      with pytest.raises(TAllocationConflict, match="MANIFEST_INVALID"):
        await _prepared(TAllocationRepository(db), snapshot, candidates)


async def test_intent_creation_does_not_extend_older_candidate_ttl(sessions):
  snapshot, candidates = await _seed(sessions, source_age_seconds=5)
  async with sessions() as db:
    async with db.begin():
      repository = TAllocationRepository(db)
      extended = replace(candidates[0], expires_at=NOW + timedelta(seconds=60))
      with pytest.raises(TAllocationConflict, match="CANDIDATE_BINDING"):
        await _prepared(repository, snapshot, (extended,))
      assert await _prepared(repository, snapshot, candidates) is not None


@pytest.mark.parametrize("status", ["DRAINING", "STOPPED", "RECONCILE_REQUIRED"])
async def test_revoked_execution_cannot_commit_old_ready_snapshot(sessions, status):
  snapshot, candidates = await _seed(sessions)
  async with sessions() as db:
    async with db.begin():
      repository = TAllocationRepository(db)
      batch = await _prepared(repository, snapshot, candidates)
      claim = await _claim(repository, batch, snapshot, candidates)
    async with db.begin():
      owners = TAssistantExecutionRepository(db)
      execution_id = snapshot.cut.execution_ref.owner_id
      execution = await owners.get_domain(execution_id)
      targets = ("DRAINING", "STOPPED") if status == "STOPPED" else (status,)
      for target in targets:
        revised = execution.transition(target, at=NOW, has_unsettled_buy_work=False)
        await owners.save_transition_with_event(
          revised,
          expected_state_version=execution.state_version,
          event=TAssistantExecutionEvent(
            execution_id, "fixture-" + target, "EXECUTION_" + target, NOW, {}
          ),
        )
        execution = revised
      result = await repository.commit(
        claim=claim, snapshot=snapshot, candidates=candidates, now=NOW
      )
      assert result.status == "SUPERSEDED"
      assert (
        await db.scalar(select(func.count(TAllocationDecisionRecord.decision_id))) == 0
      )
      assert (
        await db.get(TradeIntentRecord, candidates[0].intent_id)
      ).allocation_version == 0
      with pytest.raises(TAllocationConflict, match="NOT_ENTRY_READY"):
        await _prepared(repository, snapshot, candidates)


async def test_expiry_cleanup_still_works_after_execution_drain(sessions):
  snapshot, candidates = await _seed(sessions)
  async with sessions() as db:
    async with db.begin():
      repository = TAllocationRepository(db)
      batch = await _prepared(repository, snapshot, candidates, ttl=1)
      owners = TAssistantExecutionRepository(db)
      execution = await owners.get_domain(snapshot.cut.execution_ref.owner_id)
      await owners.save_transition_with_event(
        execution.transition("DRAINING", at=NOW, has_unsettled_buy_work=False),
        expected_state_version=execution.state_version,
        event=TAssistantExecutionEvent(
          execution.execution_id, "drain", "EXECUTION_DRAINING", NOW, {}
        ),
      )
      result = await repository.expire(
        allocation_batch_id=batch.allocation_batch_id, now=NOW + timedelta(seconds=1)
      )
      assert result.status == "EXPIRED"


@pytest.mark.parametrize("invalid", [True, float("nan"), float("inf"), 1.0])
async def test_lease_numeric_validation(sessions, invalid):
  snapshot, candidates = await _seed(sessions)
  async with sessions() as db:
    async with db.begin():
      repository = TAllocationRepository(db)
      batch = await _prepared(repository, snapshot, candidates)
      with pytest.raises(ValueError, match="INVALID_LEASE"):
        await _claim(repository, batch, snapshot, candidates, seconds=invalid)


async def test_naive_adapter_time_is_utc_and_nan_time_is_rejected(sessions):
  snapshot, candidates = await _seed(sessions)
  async with sessions() as db:
    async with db.begin():
      repository = TAllocationRepository(db)
      with pytest.raises(ValueError, match="DATETIME_REQUIRED"):
        await repository.prepare(
          snapshot=snapshot,
          candidates=candidates,
          now=float("nan"),
          expires_at=NOW + timedelta(seconds=10),
        )
      batch = await _prepared(
        repository, snapshot, candidates, now=NOW.replace(tzinfo=None)
      )
      assert batch.created_at == NOW
