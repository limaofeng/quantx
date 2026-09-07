from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerType
from quantx_domain.strategies.base import RuntimeStatePatch, SymbolRuntimeStatePatch
from quantx_domain.trading.t_assistant_execution import (
  TAssistantConfigVersion,
  TAssistantEntryAuthorization,
  TAssistantExecutionEvent,
  TAssistantRolloutStage,
)
from quantx_domain.trading.t_assistant_market_state import (
  AcceptedTMarketTick,
  SymbolDecisionSnapshot,
  SymbolMarketDeltaRing,
  SymbolMarketStateReducer,
  TAssistantSymbolState,
  TDecisionSnapshot,
)
from quantx_domain.trading.t_trade_opportunity_engine import (
  OpportunityGateContext,
  OpportunityPolicy,
  OpportunitySample,
)
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantConfigVersionRecord,
  TAssistantDecisionCycleRecord,
  TAssistantExecutionEventRecord,
  TAssistantExecutionRecord,
  TAssistantSymbolStateRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
  TTradeOpportunityEvaluation,
)
from quantx_infrastructure.repositories.t_assistant_config_repository import (
  TAssistantConfigConflict,
  TAssistantConfigRepository,
)
from quantx_infrastructure.repositories.t_assistant_decision_cycle_repository import (
  T_CYCLE_INPUT_STALE,
  T_CYCLE_LEASE_CONFLICT,
  TAssistantCycleConflict,
  TAssistantDecisionCycleRepository,
)
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionConflict,
  TAssistantExecutionRepository,
)
from quantx_infrastructure.repositories.t_assistant_symbol_state_repository import (
  TAssistantSymbolStateRepository,
)
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

NOW = datetime(2026, 9, 3, 9, 30, tzinfo=UTC)


@pytest.fixture
async def sessions():
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  tables = [
    TTradeGlobalConfig.__table__,
    TAssistantConfigVersionRecord.__table__,
    TAssistantExecutionRecord.__table__,
    TAssistantExecutionEventRecord.__table__,
    TAssistantSymbolStateRecord.__table__,
    TAssistantDecisionCycleRecord.__table__,
    TTradeOpportunityEvaluation.__table__,
  ]
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync: Base.metadata.create_all(sync, tables=tables)
    )
  yield async_sessionmaker(engine, expire_on_commit=False)
  await engine.dispose()


def _version(config_id: str) -> TAssistantConfigVersion:
  return TAssistantConfigVersion.create(
    config_version_id="config-version-1",
    config_id=config_id,
    version=1,
    config_schema_version="t_assistant_config_v1",
    canonical_payload={"symbol_rule_policy": OpportunityPolicy().to_dict()},
    entry_authorization=TAssistantEntryAuthorization.MANUAL_CONFIRM,
    rollout_stage=TAssistantRolloutStage.CANARY,
    policy_version=OpportunityPolicy().policy_version,
    feature_schema_version=OpportunityPolicy().feature_schema_version,
  )


async def _seed_execution(factory):
  async with factory() as db:
    async with db.begin():
      head = TTradeGlobalConfig(
        id="config-1",
        account_id="account-1",
        enabled=True,
        mode="paper",
      )
      db.add(head)
      version = _version(head.id)
      await TAssistantConfigRepository(db).append_version(version)
      execution = await TAssistantExecutionRepository(db).ensure_paper_shadow(
        account_id=head.account_id,
        version=version,
        now=NOW,
      )
    return version, execution.execution_id


async def test_config_version_and_paper_execution_are_idempotent(sessions):
  version, execution_id = await _seed_execution(sessions)
  async with sessions() as db:
    async with db.begin():
      same = await TAssistantExecutionRepository(db).ensure_paper_shadow(
        account_id="account-1",
        version=version,
        now=NOW,
      )
    assert same.execution_id == execution_id
    assert same.environment == ExecutionEnvironment.PAPER.value
    assert same.status == "WARMING"
    assert same.config_snapshot_hash == version.config_snapshot_hash
    assert (
      await db.scalar(select(func.count(TAssistantExecutionRecord.execution_id))) == 1
    )
    assert (
      await db.scalar(select(func.count(TAssistantExecutionEventRecord.event_id))) == 1
    )


async def test_config_version_id_rejects_safety_axis_drift(sessions):
  version, _ = await _seed_execution(sessions)
  drifted = TAssistantConfigVersion.create(
    config_version_id=version.config_version_id,
    config_id=version.config_id,
    version=version.version,
    config_schema_version=version.config_schema_version,
    canonical_payload=version.canonical_payload,
    entry_authorization=TAssistantEntryAuthorization.AUTO,
    rollout_stage=version.rollout_stage,
    policy_version=version.policy_version,
    feature_schema_version=version.feature_schema_version,
  )
  async with sessions() as db:
    async with db.begin():
      with pytest.raises(TAssistantConfigConflict):
        await TAssistantConfigRepository(db).append_version(drifted)


async def test_config_head_cannot_activate_a_stale_version(sessions):
  version, _ = await _seed_execution(sessions)
  async with sessions() as db:
    async with db.begin():
      head = await db.get(TTradeGlobalConfig, version.config_id)
      head.config_version = 2
    async with db.begin():
      with pytest.raises(
        TAssistantConfigConflict,
        match="T_ASSISTANT_CONFIG_HEAD_CONFLICT",
      ):
        await TAssistantConfigRepository(db).activate_version(
          config_id=version.config_id,
          config_version_id=version.config_version_id,
          desired_environment="PAPER",
          expected_state_version=int(head.state_version),
        )


async def test_execution_transition_and_event_share_strict_one_step_cas(sessions):
  _, execution_id = await _seed_execution(sessions)
  async with sessions() as db:
    async with db.begin():
      repository = TAssistantExecutionRepository(db)
      execution = await repository.get_domain(execution_id)
      revised = execution.with_universe_revision(1)
      await repository.save_transition_with_event(
        revised,
        expected_state_version=execution.state_version,
        event=TAssistantExecutionEvent(
          execution_id=execution_id,
          event_key="universe-revision:1",
          event_type="EXECUTION_UNIVERSE_REVISED",
          occurred_at=NOW,
          payload={"universe_revision": 1},
        ),
      )
      assert (await repository.get_domain(execution_id)).state_version == 2
    async with db.begin():
      with pytest.raises(
        TAssistantExecutionConflict,
        match="T_ASSISTANT_STATE_VERSION_INVALID",
      ):
        await repository.save_transition_with_event(
          replace(revised, state_version=4),
          expected_state_version=2,
          event=TAssistantExecutionEvent(
            execution_id=execution_id,
            event_key="invalid-jump",
            event_type="INVALID",
            occurred_at=NOW,
            payload={},
          ),
        )


async def test_partial_unique_allows_one_live_warming_or_running_producer(sessions):
  version, _ = await _seed_execution(sessions)
  async with sessions() as db:
    common = dict(
      config_id=version.config_id,
      config_version_id=version.config_version_id,
      frozen_config_version=version.version,
      config_snapshot_hash=version.config_snapshot_hash,
      account_id="account-1",
      environment="LIVE",
      entry_authorization="MANUAL_CONFIRM",
      rollout_stage="CANARY",
      status="WARMING",
      entry_readiness="WARMING",
      entry_readiness_reasons=["T_REWARM_REQUIRED"],
      entry_readiness_as_of=NOW,
      policy_version=version.policy_version,
      feature_schema_version=version.feature_schema_version,
      scorer_mode="RULE_ONLY",
      universe_revision=0,
      last_assigned_cycle_sequence=0,
      last_committed_cycle_sequence=0,
      checkpoint_revision=0,
      state_version=1,
    )
    db.add_all(
      [
        TAssistantExecutionRecord(execution_id="live-1", **common),
        TAssistantExecutionRecord(execution_id="live-2", **common),
      ]
    )
    with pytest.raises(IntegrityError):
      await db.flush()


def _snapshot(execution, *, now=NOW):
  policy = OpportunityPolicy()
  state = TAssistantSymbolState.initial(
    execution_id=execution.execution_id,
    instrument_code="600000.SH",
    policy_version=policy.policy_version,
    feature_schema_version=policy.feature_schema_version,
    trade_date="2026-09-03",
  )
  ring = SymbolMarketDeltaRing("600000.SH")
  tick = AcceptedTMarketTick(
    stream_id="stream-1",
    accepted_sequence=1,
    received_at_ms=int(now.timestamp() * 1000),
    sample=OpportunitySample(
      instrument_code="600000.SH",
      trade_date="2026-09-03",
      source_time_ms=int(now.timestamp() * 1000),
      tick_ordinal=1,
      price=10,
      continuity_generation="1",
      received_at_ms=int(now.timestamp() * 1000),
      bid_price=9.99,
      ask_price=10.01,
      bid_volume=1_000,
      ask_volume=1_000,
      cumulative_amount=10_000,
      cumulative_volume=1_000,
    ),
  )
  ring.accept(tick, capture_time_ms=tick.received_at_ms)
  item = SymbolDecisionSnapshot(
    instrument_code="600000.SH",
    state=state,
    delta_slice=ring.slice_after(None, decision_time_ms=tick.received_at_ms),
    gate_context=OpportunityGateContext(),
  )
  return TDecisionSnapshot(
    execution_ref=execution.execution_ref,
    decision_time=now,
    trade_date="2026-09-03",
    stream_id="stream-1",
    continuity_generation="1",
    fence_sequence=1,
    capture_as_of=now,
    universe_revision=0,
    config_version=execution.frozen_config_version,
    config_snapshot_hash=execution.config_snapshot_hash,
    policy_version=execution.policy_version,
    feature_schema_version=execution.feature_schema_version,
    execution_status=execution.status,
    entry_readiness=execution.readiness.readiness,
    entry_readiness_as_of=execution.readiness.as_of,
    symbols=(item,),
  )


async def test_cycle_commit_is_atomic_and_evidence_uses_execution_owner(sessions):
  _, execution_id = await _seed_execution(sessions)
  async with sessions() as db:
    async with db.begin():
      execution = await TAssistantExecutionRepository(db).get_domain(execution_id)
    snapshot = _snapshot(execution)
    reduction = SymbolMarketStateReducer().reduce(
      snapshot.symbols[0],
      policy=OpportunityPolicy(),
    )
    next_state = reduction.next_state
    if next_state.revision == 0:
      next_state = TAssistantSymbolState.from_dict(
        {**next_state.to_dict(), "revision": 1, "material_manifest_hash": "b" * 64}
      )
    patch = SymbolRuntimeStatePatch(
      instrument_code="600000.SH",
      expected_revision=0,
      material=True,
      patch=RuntimeStatePatch(set={"symbol_state": next_state.to_dict()}),
    )
    async with db.begin():
      repository = TAssistantDecisionCycleRepository(db)
      cycle = await repository.prepare_material_cycle(
        snapshot=snapshot,
        cycle_id="cycle-1",
        now=NOW,
      )
    async with db.begin():
      repository = TAssistantDecisionCycleRepository(db)
      claim = await repository.claim(
        cycle_id=cycle.cycle_id,
        processing_owner="worker-1",
        expected_input_manifest_hash=cycle.input_manifest_hash,
        now=NOW,
      )
      committed = await repository.commit_material_cycle(
        claim=claim,
        expected_input_manifest_hash=cycle.input_manifest_hash,
        symbol_patches=(patch,),
        opportunity_evidence=(
          {
            "event_key": "shadow-evaluation-1",
            "instrument_code": "600000.SH",
            "evaluated_at": NOW,
            "payload": {
              "execution_ref": execution.execution_ref.to_dict(),
              "environment": ExecutionEnvironment.PAPER.value,
              "signal_snapshot": None,
              "cycle_id": cycle.cycle_id,
              "paper_shadow_only": True,
            },
          },
        ),
        execution_events=(),
        proposed_intents=(
          {
            "intent_id": "shadow-intent-1",
            "execution_ref": execution.execution_ref.to_dict(),
            "environment": ExecutionEnvironment.PAPER.value,
            "instrument_code": "600000.SH",
          },
        ),
        now=NOW,
      )

    assert committed.status == "PROPOSALS_COMMITTED"
    evidence = await db.scalar(select(TTradeOpportunityEvaluation))
    assert evidence.owner_type == ExecutionOwnerType.T_ASSISTANT_EXECUTION.value
    assert evidence.owner_id == execution_id
    assert evidence.environment == ExecutionEnvironment.PAPER.value
    assert evidence.strategy_run_id is None
    assert (
      await db.scalar(select(func.count(TAssistantSymbolStateRecord.state_id))) == 1
    )
    assert committed.output_manifest["paper_shadow_intent_proposals"] == [
      {
        "intent_id": "shadow-intent-1",
        "execution_ref": execution.execution_ref.to_dict(),
        "environment": ExecutionEnvironment.PAPER.value,
        "instrument_code": "600000.SH",
      }
    ]


@pytest.mark.parametrize("failure", ["event_owner", "proposal_owner", "late_write"])
async def test_late_material_failure_rolls_back_even_if_outer_caller_catches(
  sessions, monkeypatch, failure,
):
  _, execution_id = await _seed_execution(sessions)
  async with sessions() as db:
    async with db.begin():
      execution = await TAssistantExecutionRepository(db).get_domain(execution_id)
      snapshot = _snapshot(execution)
      cycle = await TAssistantDecisionCycleRepository(db).prepare_material_cycle(
        snapshot=snapshot, cycle_id="late-failure", now=NOW,
      )
    state = replace(
      snapshot.symbols[0].state, revision=1, material_manifest_hash="e" * 64,
    )
    patch = SymbolRuntimeStatePatch(
      instrument_code="600000.SH", expected_revision=0, material=True,
      patch=RuntimeStatePatch(set={"symbol_state": state.to_dict()}),
    )
    event = TAssistantExecutionEvent(
      execution_id="wrong" if failure == "event_owner" else execution_id,
      event_key="material-event", event_type="MATERIAL", occurred_at=NOW,
      payload={},
    )
    async with db.begin():
      repository = TAssistantDecisionCycleRepository(db)
      claim = await repository.claim(
        cycle_id=cycle.cycle_id, processing_owner="worker",
        expected_input_manifest_hash=cycle.input_manifest_hash, now=NOW,
      )
      if failure == "late_write":
        original_append = repository._executions.append_event

        async def fail_after_append(value):
          await original_append(value)
          if value.event_type == "DECISION_CYCLE_PROPOSALS_COMMITTED":
            await db.flush()
            raise RuntimeError("injected final event write failure")

        monkeypatch.setattr(repository._executions, "append_event", fail_after_append)
      expected_error = RuntimeError if failure == "late_write" else TAssistantCycleConflict
      with pytest.raises(expected_error):
        await repository.commit_material_cycle(
          claim=claim, expected_input_manifest_hash=cycle.input_manifest_hash,
          symbol_patches=(patch,),
          opportunity_evidence=({
            "event_key": "material-evidence", "instrument_code": "600000.SH",
            "evaluated_at": NOW, "payload": {
              "execution_ref": execution.execution_ref.to_dict(),
              "environment": "PAPER", "cycle_id": cycle.cycle_id,
            },
          },),
          execution_events=(event,),
          proposed_intents=({
            "execution_ref": {"owner_type": "T_ASSISTANT_EXECUTION", "owner_id": "wrong"},
            "environment": "PAPER", "instrument_code": "600000.SH",
          },) if failure == "proposal_owner" else (),
          now=NOW,
        )
      # Intentionally commit this outer transaction like the runtime's
      # domain-conflict catch: the material savepoint must already be clean.
  async with sessions() as db:
    assert await db.scalar(select(func.count(TAssistantSymbolStateRecord.state_id))) == 0
    assert await db.scalar(select(func.count(TTradeOpportunityEvaluation.id))) == 0
    assert await db.scalar(select(func.count(TAssistantExecutionEventRecord.event_id)).where(
      TAssistantExecutionEventRecord.event_key == "material-event"
    )) == 0
    persisted = await db.get(TAssistantDecisionCycleRecord, "late-failure")
    assert persisted.status == "PREPARED"
    assert persisted.output_manifest is None
    execution_record = await db.get(TAssistantExecutionRecord, execution_id)
    assert execution_record.last_committed_cycle_sequence == 0
    assert execution_record.checkpoint_revision == 0


async def test_stale_cycle_is_aborted_and_cannot_be_reused(sessions):
  _, execution_id = await _seed_execution(sessions)
  async with sessions() as db:
    async with db.begin():
      execution = await TAssistantExecutionRepository(db).get_domain(execution_id)
    snapshot = _snapshot(execution)
    async with db.begin():
      repository = TAssistantDecisionCycleRepository(db)
      cycle = await repository.prepare_material_cycle(
        snapshot=snapshot,
        cycle_id="cycle-stale",
        now=NOW,
      )
    async with db.begin():
      with pytest.raises(TAssistantCycleConflict, match=T_CYCLE_INPUT_STALE):
        await repository.claim(
          cycle_id=cycle.cycle_id,
          processing_owner="worker-1",
          expected_input_manifest_hash="0" * 64,
          now=NOW,
        )
    refreshed = await repository.get(cycle.cycle_id)
    assert refreshed.status == "ABORTED_STALE"
    assert refreshed.abort_reason == T_CYCLE_INPUT_STALE


async def test_cycle_ttl_uses_wall_clock_and_persists_fenced_abort(sessions):
  _, execution_id = await _seed_execution(sessions)
  async with sessions() as db:
    async with db.begin():
      execution = await TAssistantExecutionRepository(db).get_domain(execution_id)
      cycle = await TAssistantDecisionCycleRepository(db).prepare_material_cycle(
        snapshot=_snapshot(execution),
        cycle_id="cycle-expired",
        now=NOW,
      )
    async with db.begin():
      with pytest.raises(TAssistantCycleConflict, match=T_CYCLE_INPUT_STALE):
        await TAssistantDecisionCycleRepository(db).claim(
          cycle_id=cycle.cycle_id,
          processing_owner="worker-1",
          expected_input_manifest_hash=cycle.input_manifest_hash,
          now=NOW + timedelta(seconds=16),
        )
    refreshed = await TAssistantDecisionCycleRepository(db).get(cycle.cycle_id)
    assert refreshed.status == "ABORTED_STALE"
    assert refreshed.processing_fence_token is None


async def test_commit_and_renew_recheck_cycle_ttl_and_terminalize(sessions):
  _, execution_id = await _seed_execution(sessions)
  async with sessions() as db:
    async with db.begin():
      execution = await TAssistantExecutionRepository(db).get_domain(execution_id)
      commit_cycle = await TAssistantDecisionCycleRepository(db).prepare_material_cycle(
        snapshot=_snapshot(execution),
        cycle_id="cycle-commit-expired",
        now=NOW,
      )
    async with db.begin():
      repository = TAssistantDecisionCycleRepository(db)
      commit_claim = await repository.claim(
        cycle_id=commit_cycle.cycle_id,
        processing_owner="worker-1",
        expected_input_manifest_hash=commit_cycle.input_manifest_hash,
        now=NOW,
      )
      committed = await repository.commit_material_cycle(
        claim=commit_claim,
        expected_input_manifest_hash=commit_cycle.input_manifest_hash,
        symbol_patches=(),
        opportunity_evidence=(),
        execution_events=(),
        proposed_intents=(),
        now=NOW + timedelta(seconds=16),
      )
    assert committed.status == "ABORTED_STALE"
    assert committed.processing_fence_token is None

    later_snapshot = _snapshot(
      execution,
      now=NOW + timedelta(seconds=1),
    )
    async with db.begin():
      renew_cycle = await TAssistantDecisionCycleRepository(db).prepare_material_cycle(
        snapshot=later_snapshot,
        cycle_id="cycle-renew-expired",
        now=NOW,
      )
    async with db.begin():
      repository = TAssistantDecisionCycleRepository(db)
      renew_claim = await repository.claim(
        cycle_id=renew_cycle.cycle_id,
        processing_owner="worker-1",
        expected_input_manifest_hash=renew_cycle.input_manifest_hash,
        now=NOW,
      )
      renewed = await repository.renew(
        claim=renew_claim,
        now=NOW + timedelta(seconds=9),
      )
      assert renewed is not None
    async with db.begin():
      terminal = await TAssistantDecisionCycleRepository(db).renew(
        claim=renewed,
        now=NOW + timedelta(seconds=16),
      )
      assert terminal is None
    refreshed = await TAssistantDecisionCycleRepository(db).get(renew_cycle.cycle_id)
    assert refreshed.status == "ABORTED_STALE"
    assert refreshed.processing_fence_token is None


async def test_symbol_cas_conflict_terminalizes_cycle_without_partial_material(
  sessions,
):
  _, execution_id = await _seed_execution(sessions)
  async with sessions() as db:
    async with db.begin():
      execution = await TAssistantExecutionRepository(db).get_domain(execution_id)
      snapshot = _snapshot(execution)
      cycle = await TAssistantDecisionCycleRepository(db).prepare_material_cycle(
        snapshot=snapshot,
        cycle_id="cycle-symbol-conflict",
        now=NOW,
      )
    conflicting = replace(
      snapshot.symbols[0].state,
      revision=1,
      material_manifest_hash="c" * 64,
    )
    async with db.begin():
      await TAssistantSymbolStateRepository(db).apply_material_states(
        (conflicting,),
        expected_revisions={"600000.SH": 0},
      )
    proposed = replace(
      conflicting,
      revision=1,
      material_manifest_hash="d" * 64,
    )
    patch = SymbolRuntimeStatePatch(
      instrument_code="600000.SH",
      expected_revision=0,
      material=True,
      patch=RuntimeStatePatch(set={"symbol_state": proposed.to_dict()}),
    )
    async with db.begin():
      repository = TAssistantDecisionCycleRepository(db)
      claim = await repository.claim(
        cycle_id=cycle.cycle_id,
        processing_owner="worker-1",
        expected_input_manifest_hash=cycle.input_manifest_hash,
        now=NOW,
      )
      committed = await repository.commit_material_cycle(
        claim=claim,
        expected_input_manifest_hash=cycle.input_manifest_hash,
        symbol_patches=(patch,),
        opportunity_evidence=(),
        execution_events=(),
        proposed_intents=(),
        now=NOW,
      )
    assert committed.status == "ABORTED_STALE"
    persisted = await TAssistantSymbolStateRepository(db).get(
      execution_id=execution_id,
      instrument_code="600000.SH",
    )
    assert persisted.revision == 1
    assert persisted.material_manifest_hash == "c" * 64


async def test_cycle_database_shape_rejects_partial_claim_and_terminal(sessions):
  _, execution_id = await _seed_execution(sessions)
  cycle_id = "cycle-shape"
  async with sessions() as db:
    async with db.begin():
      execution = await TAssistantExecutionRepository(db).get_domain(execution_id)
      await TAssistantDecisionCycleRepository(db).prepare_material_cycle(
        snapshot=_snapshot(execution),
        cycle_id=cycle_id,
        now=NOW,
      )
    with pytest.raises(IntegrityError):
      async with db.begin():
        await db.execute(
          update(TAssistantDecisionCycleRecord)
          .where(TAssistantDecisionCycleRecord.cycle_id == cycle_id)
          .values(processing_owner="partial-owner")
        )
    with pytest.raises(IntegrityError):
      async with db.begin():
        await db.execute(
          update(TAssistantDecisionCycleRecord)
          .where(TAssistantDecisionCycleRecord.cycle_id == cycle_id)
          .values(status="PROPOSALS_COMMITTED", committed_at=NOW)
        )


async def test_active_lease_rejects_reclaim_even_from_same_owner(sessions):
  _, execution_id = await _seed_execution(sessions)
  async with sessions() as db:
    async with db.begin():
      execution = await TAssistantExecutionRepository(db).get_domain(execution_id)
      cycle = await TAssistantDecisionCycleRepository(db).prepare_material_cycle(
        snapshot=_snapshot(execution),
        cycle_id="cycle-leased",
        now=NOW,
      )
    async with db.begin():
      repository = TAssistantDecisionCycleRepository(db)
      await repository.claim(
        cycle_id=cycle.cycle_id,
        processing_owner="worker-1",
        expected_input_manifest_hash=cycle.input_manifest_hash,
        now=NOW,
      )
    async with db.begin():
      with pytest.raises(TAssistantCycleConflict, match=T_CYCLE_LEASE_CONFLICT):
        await TAssistantDecisionCycleRepository(db).claim(
          cycle_id=cycle.cycle_id,
          processing_owner="worker-1",
          expected_input_manifest_hash=cycle.input_manifest_hash,
          now=NOW + timedelta(seconds=1),
        )


async def test_commit_aborts_when_locked_execution_readiness_drifted(sessions):
  _, execution_id = await _seed_execution(sessions)
  async with sessions() as db:
    async with db.begin():
      execution = await TAssistantExecutionRepository(db).get_domain(execution_id)
      cycle = await TAssistantDecisionCycleRepository(db).prepare_material_cycle(
        snapshot=_snapshot(execution),
        cycle_id="cycle-readiness-drift",
        now=NOW,
      )
    async with db.begin():
      repository = TAssistantDecisionCycleRepository(db)
      claim = await repository.claim(
        cycle_id=cycle.cycle_id,
        processing_owner="worker-1",
        expected_input_manifest_hash=cycle.input_manifest_hash,
        now=NOW,
      )
      record = await db.get(TAssistantExecutionRecord, execution_id)
      record.entry_readiness = "DEGRADED"
      record.entry_readiness_reasons = ["TEST_DRIFT"]
      record.entry_readiness_as_of = NOW + timedelta(seconds=1)
      record.state_version += 1
      committed = await repository.commit_material_cycle(
        claim=claim,
        expected_input_manifest_hash=cycle.input_manifest_hash,
        symbol_patches=(),
        opportunity_evidence=(),
        execution_events=(),
        proposed_intents=(),
        now=NOW + timedelta(seconds=1),
      )
    assert committed.status == "ABORTED_STALE"
    assert committed.abort_reason == T_CYCLE_INPUT_STALE


async def test_abort_stale_requires_exact_claim_fence(sessions):
  _, execution_id = await _seed_execution(sessions)
  async with sessions() as db:
    async with db.begin():
      execution = await TAssistantExecutionRepository(db).get_domain(execution_id)
      cycle = await TAssistantDecisionCycleRepository(db).prepare_material_cycle(
        snapshot=_snapshot(execution),
        cycle_id="cycle-abort-fence",
        now=NOW,
      )
    async with db.begin():
      claim = await TAssistantDecisionCycleRepository(db).claim(
        cycle_id=cycle.cycle_id,
        processing_owner="worker-1",
        expected_input_manifest_hash=cycle.input_manifest_hash,
        now=NOW,
      )
    async with db.begin():
      with pytest.raises(TAssistantCycleConflict, match=T_CYCLE_LEASE_CONFLICT):
        await TAssistantDecisionCycleRepository(db).abort_stale(
          cycle_id=cycle.cycle_id,
          expected_fence_token="other-fence",
          now=NOW,
        )
    async with db.begin():
      aborted = await TAssistantDecisionCycleRepository(db).abort_stale(
        cycle_id=cycle.cycle_id,
        expected_fence_token=claim.processing_fence_token,
        now=NOW,
      )
    assert aborted.status == "ABORTED_STALE"
