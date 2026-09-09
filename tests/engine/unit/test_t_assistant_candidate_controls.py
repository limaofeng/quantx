"""Durable intent outcomes return through the real P3 snapshot/strategy reducer."""

from copy import deepcopy
from datetime import timedelta

import pytest
from quantx_contracts import ExecutionEnvironment
from quantx_domain.trading.t_assistant_market_state import (
  SymbolDecisionSnapshot,
  SymbolMarketDeltaRing,
  TDecisionSnapshot,
)
from quantx_domain.trading.t_trade_opportunity_engine import (
  CandidateControl,
  OpportunityGateContext,
  OpportunityPolicy,
)
from quantx_engine.t_assistant_candidate_controls import read_candidate_controls
from quantx_engine.t_assistant_decision_runtime import (
  TAssistantLiveDecisionRuntime,
  TAssistantPaperShadowRuntime,
)
from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
  TTradeOpportunityEvaluation,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from quantx_infrastructure.repositories.t_assistant_symbol_state_repository import (
  TAssistantSymbolStateRepository,
)
from sqlalchemy import func, select

from tests.infrastructure import test_t_candidate_evidence as candidate_tests
from tests.infrastructure.test_paper_portfolio_snapshot import storage_time

allocation_sessions = candidate_tests.allocation_sessions
base_sessions = candidate_tests.base_sessions
ledger_sessions = candidate_tests.ledger_sessions
sessions = candidate_tests.sessions
frozen_config = candidate_tests.frozen_config


async def source(
  sessions,
  status="ALLOCATION_PENDING",
  environment=ExecutionEnvironment.PAPER,
  *,
  candidate_at=None,
):
  seed = await candidate_tests.seed_candidate_cycle(
    sessions,
    extra_tick=True,
    environment=environment.value,
    **({"candidate_at": candidate_at} if candidate_at is not None else {}),
  )
  async with sessions() as db, db.begin():
    row = await db.get(TradeIntentRecord, seed.intent_id)
    row.status = status
    row.notes = (
      "PAPER_ENTRY_REBUILD_REQUIRED" if status == "CANCELLED" else "control fixture"
    )
    row.updated_at = storage_time(TradeIntentRecord, "updated_at", seed.now)
    states = await TAssistantSymbolStateRepository(db).load_domains(seed.execution_id)
    execution = await TAssistantExecutionRepository(db).get_domain(seed.execution_id)
  return seed, execution, states


async def controls(db, seed, states, **changes):
  return await read_candidate_controls(
    db,
    **{
      "execution_id": seed.execution_id,
      "account_id": "account-1",
      "environment": ExecutionEnvironment.PAPER,
      "symbol_states": states,
      "as_of": seed.now + timedelta(milliseconds=1),
      **changes,
    },
  )


@pytest.mark.parametrize(
  "status,kind",
  [
    ("CANCELLED", "suppress_candidate_id"),
    ("EXECUTION_PENDING", "suppress_candidate_id"),
    ("ROUTED", "suppress_candidate_id"),
    ("FILLED", "suppress_candidate_id"),
    ("AWAITING_APPROVAL", "awaiting_approval_candidate_id"),
    ("ALLOCATION_PENDING", None),
    ("EXECUTION_READY", None),
  ],
)
@pytest.mark.parametrize(
  "environment", [ExecutionEnvironment.PAPER, ExecutionEnvironment.LIVE]
)
async def test_standard_intent_lifecycle_projects_exact_candidate_control(
  sessions, frozen_config, status, kind, environment
):
  seed, _, states = await source(sessions, status, environment)
  async with sessions() as db:
    result = await controls(db, seed, states, environment=environment)
    if kind:
      assert (
        getattr(result["600000.SH"], kind)
        == states["600000.SH"].opportunity_state.candidate.candidate_id
      )
    else:
      assert result == {}
    assert (await db.get(TradeIntentRecord, seed.intent_id)).status == status


@pytest.mark.parametrize(
  "field,value",
  [
    ("owner_id", "another-execution"),
    ("account_id", "another-account"),
    ("environment", "LIVE"),
    ("owner_type", "MANUAL_COMMAND"),
    ("direction", "SELL"),
    ("instrument_code", "000001.SZ"),
  ],
)
async def test_other_standard_scope_never_controls_the_candidate(
  sessions, frozen_config, field, value
):
  seed, _, states = await source(sessions)
  async with sessions() as db, db.begin():
    row = await db.get(TradeIntentRecord, seed.intent_id)
    values = {
      column.key: deepcopy(getattr(row, column.key))
      for column in row.__mapper__.column_attrs
    }
    values.update(
      id="other-intent",
      idempotency_key="other-intent",
      status="CANCELLED",
      allocation_cycle_id=None,
      allocation_decision_id=None,
      allocation_next_eligible_at=None,
      allocation_version=0,
    )
    values[field] = value
    db.add(TradeIntentRecord(**values))
    await db.flush()
    assert await controls(db, seed, states) == {}


@pytest.mark.parametrize(
  "key,value,reason",
  [
    ("candidate_id", "other-candidate", None),
    ("candidate_fingerprint", "f" * 64, "BINDING_CONFLICT"),
    ("source_time_ms", 1, "BINDING_CONFLICT"),
  ],
)
async def test_current_candidate_id_fingerprint_and_source_are_bound(
  sessions, frozen_config, key, value, reason
):
  seed, _, states = await source(sessions, "CANCELLED")
  async with sessions() as db, db.begin():
    row = await db.get(TradeIntentRecord, seed.intent_id)
    # SQLite negative fixture: immutable producer material would also be guarded in PG.
    row.intent_metadata = {**row.intent_metadata, key: value}
    await db.flush()
    if reason:
      with pytest.raises(ValueError, match=reason):
        await controls(db, seed, states)
    else:
      assert await controls(db, seed, states) == {}


async def test_future_updated_intent_cannot_control_an_older_cut(
  sessions, frozen_config
):
  seed, _, states = await source(sessions, "CANCELLED")
  async with sessions() as db, db.begin():
    row = await db.get(TradeIntentRecord, seed.intent_id)
    row.updated_at = storage_time(
      TradeIntentRecord, "updated_at", seed.now + timedelta(seconds=1)
    )
    await db.flush()
    with pytest.raises(ValueError, match="FUTURE_INTENT"):
      await controls(db, seed, states)


async def test_control_read_refreshes_cached_intent_availability(
  sessions, frozen_config
):
  seed, _, states = await source(sessions)
  async with sessions() as reader:
    cached = await reader.get(TradeIntentRecord, seed.intent_id)
    assert cached.status == "ALLOCATION_PENDING"
    await reader.commit()  # Release SQLite connection, retain the identity-map object.
    async with sessions() as writer, writer.begin():
      latest = await writer.get(TradeIntentRecord, seed.intent_id)
      latest.status = "CANCELLED"
      latest.updated_at = storage_time(
        TradeIntentRecord, "updated_at", seed.now + timedelta(seconds=1)
      )
    with pytest.raises(ValueError, match="FUTURE_INTENT"):
      await controls(reader, seed, states)


def snapshot(seed, execution, states, current_controls, now):
  symbols = []
  for code, state in states.items():
    ring = SymbolMarketDeltaRing(code)
    tick = dict(seed.latest_ticks)[code]
    ring.accept(tick, capture_time_ms=int(now.timestamp() * 1000))
    delta = ring.slice_after(state.cursor, decision_time_ms=int(now.timestamp() * 1000))
    assert delta.ticks == ()  # Control delivery must not manufacture another Tick.
    symbols.append(
      SymbolDecisionSnapshot(
        code,
        state,
        delta,
        OpportunityGateContext(continuous_session=True, session_code="CONTINUOUS_AM"),
        candidate_control=current_controls.get(code, CandidateControl()),
      )
    )
  return TDecisionSnapshot(
    execution.execution_ref,
    now,
    "2026-09-03",
    "stream-1",
    "7",
    seed.latest_tick.market_fence_sequence,
    now,
    execution.universe_revision,
    execution.frozen_config_version,
    execution.config_snapshot_hash,
    execution.policy_version,
    execution.feature_schema_version,
    execution.status,
    execution.readiness.readiness,
    execution.readiness.as_of,
    tuple(symbols),
  )


@pytest.mark.parametrize(
  "status,expected",
  [
    ("CANCELLED", "SUPPRESSED"),
    ("EXECUTION_PENDING", "SUPPRESSED"),
    ("ROUTED", "SUPPRESSED"),
    ("FILLED", "SUPPRESSED"),
    ("AWAITING_APPROVAL", "AWAITING_APPROVAL"),
  ],
)
@pytest.mark.parametrize(
  "environment", [ExecutionEnvironment.PAPER, ExecutionEnvironment.LIVE]
)
async def test_control_enters_snapshot_hash_and_real_strategy_step_once_without_tick(
  sessions, frozen_config, status, expected, environment
):
  seed, execution, states = await source(sessions, status, environment)
  now = seed.now + timedelta(milliseconds=1)
  async with sessions() as db:
    current = await controls(db, seed, states, environment=environment)
  baseline = snapshot(seed, execution, states, {}, now)
  controlled = snapshot(seed, execution, states, current, now)
  assert controlled.snapshot_hash != baseline.snapshot_hash
  runtime_class = (
    TAssistantLiveDecisionRuntime
    if environment is ExecutionEnvironment.LIVE
    else TAssistantPaperShadowRuntime
  )
  runtime = runtime_class(session_factory=sessions, clock=lambda: now)
  runtime.bind_execution(
    execution,
    parameters={
      "account_id": "account-1",
      "signal_policy": OpportunityPolicy().to_dict(),
      "target_trade_amount": 10000,
    },
    symbol_states=states,
  )
  first = await runtime.run_cycle(execution=execution, snapshot=controlled)
  assert first.committed and first.output.trade_intents == []
  next_states = runtime.symbol_states(seed.execution_id)
  assert next_states["600000.SH"].opportunity_state.candidate_status.value == expected
  assert next_states["600000.SH"].cursor == states["600000.SH"].cursor
  async with sessions() as db:
    persisted = await TAssistantSymbolStateRepository(db).load_domains(
      seed.execution_id
    )
    assert persisted["600000.SH"].opportunity_state.candidate_status.value == expected
    count = await db.scalar(
      select(func.count())
      .select_from(TTradeOpportunityEvaluation)
      .where(
        TTradeOpportunityEvaluation.event_type
        == "T_OPPORTUNITY_CANDIDATE_CONTROL_APPLIED"
      )
    )
    assert count == 1
  now += timedelta(milliseconds=1)
  repeated = await runtime.run_cycle(
    execution=execution, snapshot=snapshot(seed, execution, next_states, current, now)
  )
  assert repeated.output.trade_intents == []
  assert all(
    not patch.material and not patch.patch.append_events
    for patch in repeated.output.symbol_state_patches
  )
  async with sessions() as db:
    assert (
      await db.scalar(
        select(func.count())
        .select_from(TTradeOpportunityEvaluation)
        .where(
          TTradeOpportunityEvaluation.event_type
          == "T_OPPORTUNITY_CANDIDATE_CONTROL_APPLIED"
        )
      )
      == count
    )
    assert await db.scalar(select(func.count()).select_from(TradeIntentRecord)) == 1
    assert (await db.get(TradeIntentRecord, seed.intent_id)).status == status
