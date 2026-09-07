"""Real StrategyBase.step candidate-time witnesses through committed P3 cycles."""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

import pytest
from quantx_application.t_trade_v3.execution_use_cases import (
  TAssistantExecutionLifecycle,
)
from quantx_domain.trading.t_assistant_execution import (
  TAssistantConfigVersion,
  stable_manifest_hash,
)
from quantx_domain.trading.t_assistant_market_state import (
  AcceptedTMarketTick,
  SymbolDecisionSnapshot,
  SymbolMarketCursor,
  SymbolMarketDeltaRing,
  TAssistantSymbolState,
  TDecisionSnapshot,
  TMarketSourceIdentity,
  decode_candidate_evidence,
)
from quantx_domain.trading.t_trade_opportunity_engine import (
  OpportunityGateContext,
  OpportunityPolicy,
  OpportunityReferenceProfile,
  OpportunitySample,
  OpportunityState,
  reduce_opportunity,
)
from quantx_engine.t_assistant_decision_runtime import TAssistantPaperShadowRuntime
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantDecisionCycleRecord,
  TAssistantSymbolStateRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
  TTradeOpportunityEvaluation,
)
from quantx_infrastructure.repositories.t_assistant_config_repository import (
  TAssistantConfigRepository,
)
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from sqlalchemy import event, select

from tests.infrastructure import test_t_allocation_repository as allocation_tests
from tests.infrastructure.test_paper_portfolio_snapshot import (
  allocation_sessions as _allocation_sessions,
)
from tests.infrastructure.test_paper_portfolio_snapshot import (
  base_sessions as _base_sessions,
)
from tests.infrastructure.test_paper_portfolio_snapshot import (
  frozen_config as _frozen_config,
)
from tests.infrastructure.test_paper_portfolio_snapshot import (
  ledger_sessions as _ledger_sessions,
)
from tests.infrastructure.test_paper_portfolio_snapshot import (
  sessions as _sessions,
)
from tests.infrastructure.test_paper_portfolio_snapshot import (
  storage_time,
)
from tests.infrastructure.test_t_assistant_runtime_repository import NOW

allocation_sessions = _allocation_sessions
base_sessions = _base_sessions
ledger_sessions = _ledger_sessions
sessions = _sessions
frozen_config = _frozen_config


@dataclass(frozen=True)
class CandidateCycleSeed:
  execution_id: str
  cycle_id: str
  detection_cycle_id: str
  intent_id: str
  now: datetime
  latest_tick: AcceptedTMarketTick


async def seed_candidate_cycle(
  sessions, *, deferred=False, extra_tick=True, candidate_at=NOW
):
  """Requires frozen_config fixture; invokes the real runtime/strategy, no signal rewrite."""
  values = asdict(allocation_tests._version("config-1"))
  values.pop("config_snapshot_hash")
  values["entry_authorization"] = "AUTO"
  values["canonical_payload"]["entry_execution_gate_policy"] = {
    "version": "entry-gate-v1",
    "quote_max_age_ms": 3000,
    "max_price_deviation_bps": 100,
    "max_spread_bps": 30,
    "capabilities": {
      "version": "book-v1",
      "required_fields": [
        "price",
        "bid_price",
        "ask_price",
        "bid_volume",
        "ask_volume",
      ],
    },
  }
  version = TAssistantConfigVersion.create(**values)
  async with sessions() as db, db.begin():
    db.add(
      TTradeGlobalConfig(
        id="config-1", account_id="account-1", enabled=True, mode="paper"
      )
    )
    await TAssistantConfigRepository(db).append_version(version)
    repository = TAssistantExecutionRepository(db)
    row = await repository.ensure_paper_shadow(
      account_id="account-1", version=version, now=candidate_at
    )
    execution = await repository.get_domain(row.execution_id)
    if not deferred:
      execution = await TAssistantExecutionLifecycle(repository).activate_ready(
        execution, at=candidate_at, payload={"test": "candidate-evidence"}
      )
  profile = OpportunityReferenceProfile(
    profile_version="profile-v1",
    profile_schema_version=1,
    as_of_trade_date="2026-09-02",
    pullback_threshold_pct=0.8,
    momentum_rise_threshold_pct=0.8,
    momentum_amount_velocity_ratio=2.0,
    pullback_max_spread_ticks=3,
    momentum_max_spread_ticks=10,
  )
  policy = OpportunityPolicy()
  base_ms = int(candidate_at.timestamp() * 1000) - 24000
  shapes = [
    (0, 100.0, 1000000, 10000),
    (5, 99.0, 1050000, 10500),
    (20, 99.0, 1100000, 11000),
    (22, 99.30, 1120000, 11200),
    (24, 99.32, 1140000, 11400),
  ]
  if extra_tick:
    shapes.append((25, 99.32, 1150000, 11500))
  samples = [
    OpportunitySample(
      "600000.SH",
      "2026-09-03",
      base_ms + seconds * 1000,
      index + 1,
      price,
      continuity_generation="7",
      received_at_ms=base_ms + seconds * 1000,
      bid_price=price - (0.03 if index == 5 else 0.01),
      ask_price=price,
      bid_volume=1000,
      ask_volume=1000,
      cumulative_amount=amount,
      cumulative_volume=volume,
    )
    for index, (seconds, price, amount, volume) in enumerate(shapes)
  ]
  opportunity = OpportunityState.initial()
  for sample in samples[:4]:
    opportunity = reduce_opportunity(
      opportunity, sample, policy=policy, reference_profile=profile
    ).state
  cursor = SymbolMarketCursor(
    "stream-1", "7", 1, 4, TMarketSourceIdentity("7", samples[3].source_time_ms, 4)
  )
  state = TAssistantSymbolState(
    execution.execution_id,
    "600000.SH",
    0,
    "ACTIVE",
    cursor,
    opportunity,
    policy.policy_version,
    policy.feature_schema_version,
  )
  ring = SymbolMarketDeltaRing("600000.SH")
  for index, sample in enumerate(samples[4:], 5):
    ring.accept(
      AcceptedTMarketTick("stream-1", index, sample.received_at_ms, sample),
      capture_time_ms=sample.received_at_ms,
    )
  now = datetime.fromtimestamp(samples[-1].source_time_ms / 1000, UTC)
  clock = {"now": now}
  runtime = TAssistantPaperShadowRuntime(
    session_factory=sessions, clock=lambda: clock["now"]
  )

  def snapshot(execution, state):
    symbol = SymbolDecisionSnapshot(
      "600000.SH",
      state,
      ring.slice_after(
        state.cursor,
        decision_time_ms=int(clock["now"].timestamp() * 1000),
        through_accepted_sequence=len(samples),
      ),
      OpportunityGateContext(continuous_session=True, session_code="CONTINUOUS_AM"),
      profile,
    )
    return TDecisionSnapshot(
      execution.execution_ref,
      clock["now"],
      "2026-09-03",
      "stream-1",
      "7",
      len(samples),
      clock["now"],
      execution.universe_revision,
      execution.frozen_config_version,
      execution.config_snapshot_hash,
      execution.policy_version,
      execution.feature_schema_version,
      execution.status,
      execution.readiness.readiness,
      execution.readiness.as_of,
      (symbol,),
    )

  def bind(execution, state):
    runtime.bind_execution(
      execution,
      parameters={
        "account_id": "account-1",
        "signal_policy": policy.to_dict(),
        "target_trade_amount": 10000,
      },
      symbol_states={"600000.SH": state},
    )

  def availability(_mapper, _connection, record):
    record.created_at = storage_time(type(record), "created_at", clock["now"])
    if isinstance(record, TAssistantSymbolStateRecord):
      record.updated_at = storage_time(type(record), "updated_at", clock["now"])

  def update_availability(statement_context):
    if (
      statement_context.is_update
      and statement_context.statement.table.name
      == TAssistantSymbolStateRecord.__tablename__
    ):
      statement_context.statement = statement_context.statement.values(
        updated_at=storage_time(TAssistantSymbolStateRecord, "updated_at", clock["now"])
      )

  event.listen(TTradeOpportunityEvaluation, "before_insert", availability)
  event.listen(TAssistantSymbolStateRecord, "before_insert", availability)
  event.listen(
    sessions.class_.sync_session_class, "do_orm_execute", update_availability
  )
  try:
    bind(execution, state)
    detected = await runtime.run_cycle(
      execution=execution, snapshot=snapshot(execution, state)
    )
    assert detected.committed
    checkpoint = runtime.symbol_states(execution.execution_id)["600000.SH"].to_dict()
    assert checkpoint["material_manifest_hash"] == stable_manifest_hash(
      {
        key: checkpoint[key]
        for key in (
          "opportunity_state",
          "cursor",
          "lifecycle",
          "deferred_candidate",
          "deferred_candidate_fence_sequence",
        )
      }
    )
    final = detected
    if deferred:
      assert detected.output.trade_intents == []
      state = runtime.symbol_states(execution.execution_id)["600000.SH"]
      assert state.deferred_candidate is not None
      clock["now"] += timedelta(seconds=1)
      async with sessions() as db, db.begin():
        repository = TAssistantExecutionRepository(db)
        current = await repository.get_domain(execution.execution_id)
        execution = await TAssistantExecutionLifecycle(repository).activate_ready(
          current, at=clock["now"], payload={"test": "deferred"}
        )
      bind(execution, state)
      final = await runtime.run_cycle(
        execution=execution, snapshot=snapshot(execution, state)
      )
      assert final.committed
    assert len(final.output.trade_intents) == 1
    return CandidateCycleSeed(
      execution.execution_id,
      final.cycle_id,
      detected.cycle_id,
      final.output.trade_intents[0].intent_id,
      clock["now"],
      ring.latest_tick,
    )
  finally:
    event.remove(TTradeOpportunityEvaluation, "before_insert", availability)
    event.remove(TAssistantSymbolStateRecord, "before_insert", availability)
    event.remove(
      sessions.class_.sync_session_class, "do_orm_execute", update_availability
    )


@pytest.mark.parametrize("deferred", [False, True])
async def test_real_multi_tick_and_deferred_release_reference_original_candidate(
  sessions, frozen_config, deferred
):
  source = await seed_candidate_cycle(sessions, deferred=deferred)
  async with sessions() as db:
    cycle = await db.get(TAssistantDecisionCycleRecord, source.cycle_id)
    reference = cycle.output_manifest["accepted_intents"][0]
    evidence = await db.scalar(
      select(TTradeOpportunityEvaluation).where(
        TTradeOpportunityEvaluation.event_key == reference["candidate_evidence_key"]
      )
    )
    candidate, tick, cursor = decode_candidate_evidence(
      evidence.payload["candidate_evidence"]
    )
    assert tick.accepted_sequence == cursor.accepted_sequence == 5
    assert source.latest_tick.accepted_sequence == 6
    assert candidate.source_time_ms == int(NOW.timestamp() * 1000)
    assert evidence.payload["cycle_id"] == source.detection_cycle_id
    if deferred:
      assert source.cycle_id != source.detection_cycle_id
    summary = await db.scalar(
      select(TTradeOpportunityEvaluation).where(
        TTradeOpportunityEvaluation.event_key.in_(
          (
            await db.get(TAssistantDecisionCycleRecord, source.detection_cycle_id)
          ).output_manifest["opportunity_event_keys"]
        ),
        TTradeOpportunityEvaluation.event_type != "T_OPPORTUNITY_CANDIDATE_FROZEN",
      )
    )
    assert (
      summary.payload["signal_snapshot"]["source_time_ms"] > candidate.source_time_ms
    )
    assert summary.payload["signal_snapshot"]["opportunity_score"] != candidate.score


async def test_frozen_evidence_rejects_non_integer_wire_shapes(sessions, frozen_config):
  from copy import deepcopy

  source = await seed_candidate_cycle(sessions)
  async with sessions() as db:
    cycle = await db.get(TAssistantDecisionCycleRecord, source.cycle_id)
    evidence = await db.scalar(
      select(TTradeOpportunityEvaluation).where(
        TTradeOpportunityEvaluation.event_key
        == cycle.output_manifest["accepted_intents"][0]["candidate_evidence_key"]
      )
    )
    original = evidence.payload["candidate_evidence"]
    paths = [
      ("candidate", field)
      for field in (
        "source_time_ms",
        "latched_at_ms",
        "expires_at_ms",
        "tick_ordinal",
        "feature_schema_version",
        "reference_profile_schema_version",
      )
    ]
    paths += [
      ("tick", field)
      for field in ("accepted_sequence", "received_at_ms", "market_fence_sequence")
    ]
    paths += [("cursor", field) for field in ("ring_generation", "accepted_sequence")]
    paths += [
      ("cursor", "source_identity", field)
      for field in ("source_time_ms", "tick_ordinal")
    ]
    for path in paths:
      for invalid in (True, "5", 5.7):
        malformed = deepcopy(original)
        node = malformed
        for key in path[:-1]:
          node = node[key]
        node[path[-1]] = invalid
        with pytest.raises(ValueError, match="T_CANDIDATE_EVIDENCE_INVALID"):
          decode_candidate_evidence(malformed)


@pytest.mark.parametrize("deferred", [False, True])
async def test_actual_candidate_evidence_reaches_allocation_and_final_gate(
  sessions, frozen_config, deferred
):
  from quantx_application.t_trade_v3.entry_execution_gate import (
    EntryExecutionBinding,
    EntryExecutionGateInput,
    EntryExecutionGatePolicy,
    MarketDataCapabilityManifest,
  )
  from quantx_contracts import ExecutionEnvironment
  from quantx_domain.trading.market_rules import MarketDataSnapshot
  from quantx_infrastructure.models.paper_execution import PaperExecutionOrderRecord
  from quantx_infrastructure.models.t_assistant_execution import (
    TAssistantExecutionRecord,
  )
  from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
  from quantx_infrastructure.services.account_risk_increase_admission import (
    AccountRiskIncreaseAdmissionSequencer,
  )
  from quantx_infrastructure.services.paper_allocation_coordinator import (
    PaperAllocationCoordinator,
  )
  from quantx_infrastructure.services.paper_entry_execution_review import (
    PaperEntryExecutionReview,
  )
  from quantx_infrastructure.services.paper_execution_ledger import PaperExecutionLedger
  from quantx_infrastructure.services.paper_portfolio_snapshot import (
    PaperPortfolioSnapshotReader,
  )
  from quantx_infrastructure.services.paper_receipt_convergence import (
    PaperReceiptConvergence,
  )

  from tests.infrastructure.test_paper_execution_ledger import seed_values

  candidate_at = NOW.replace(hour=1)
  source = await seed_candidate_cycle(
    sessions, deferred=deferred, candidate_at=candidate_at
  )
  latest = source.latest_tick.sample
  market = MarketDataSnapshot(
    instrument_code="600000.SH",
    timestamp=datetime.fromtimestamp(latest.source_time_ms / 1000, UTC),
    price=latest.price,
    source="accepted-tick",
    limit_up=110.0,
    limit_down=90.0,
    bid_price=[latest.bid_price - index * 0.01 for index in range(5)],
    ask_price=[latest.ask_price + index * 0.01 for index in range(5)],
    bid_vol=[1000] * 5,
    ask_vol=[1000] * 5,
  )
  async with sessions() as db, db.begin():
    ledger = PaperExecutionLedger(db, receipt_sink=PaperReceiptConvergence())
    seed = seed_values()
    seed["seed_as_of"] = candidate_at
    await ledger.initialize(
      execution_id=source.execution_id, account_id="account-1", **seed
    )
    await ledger.process_quote(
      execution_id=source.execution_id, event_key="accepted-market", quote=market
    )
    allocated = await PaperAllocationCoordinator(db).allocate_cycle(
      execution_id=source.execution_id,
      cycle_id=source.cycle_id,
      processing_owner="allocator",
      now=source.now,
    )
    assert allocated.status == "COMMITTED"
  async with sessions() as db, db.begin():
    snapshot = await PaperPortfolioSnapshotReader(db).read(
      execution_id=source.execution_id,
      cycle_id=source.cycle_id,
      instrument_codes=("600000.SH",),
      as_of=source.now,
    )
    sequencer = AccountRiskIncreaseAdmissionSequencer(
      db, environment=ExecutionEnvironment.PAPER, paper_execution_id=source.execution_id
    )
    kwargs = dict(
      account_snapshot_id=snapshot.cut.account_snapshot_id,
      account_snapshot_hash=snapshot.cut.account_snapshot_hash,
      obligation_watermark=snapshot.cut.local_obligation_watermark,
      now=source.now,
      commit=False,
    )
    admission = await sequencer.prepare_batch(account_id="account-1", **kwargs)
    claim = await sequencer.claim_batch(
      admission_batch_id=admission.admission_batch_id,
      processing_owner="admission",
      now=source.now,
      commit=False,
    )
    await sequencer.commit_batch(
      admission_batch_id=admission.admission_batch_id,
      fence_token=claim.fence_token,
      **kwargs,
    )
  async with sessions() as db, db.begin():
    execution = await db.get(TAssistantExecutionRecord, source.execution_id)
    intent = await db.get(TradeIntentRecord, source.intent_id)
    cycle = await db.get(TAssistantDecisionCycleRecord, source.cycle_id)
    ref = cycle.output_manifest["accepted_intents"][0]
    record = await db.scalar(
      select(TTradeOpportunityEvaluation).where(
        TTradeOpportunityEvaluation.event_key == ref["candidate_evidence_key"]
      )
    )
    candidate, _, cursor = decode_candidate_evidence(
      record.payload["candidate_evidence"]
    )
    binding = EntryExecutionBinding(
      candidate.fingerprint,
      execution.config_version_id,
      execution.config_snapshot_hash,
      execution.policy_version,
      "entry-gate-v1",
      execution.feature_schema_version,
      "book-v1",
    )
    capabilities = MarketDataCapabilityManifest(
      "book-v1",
      frozenset({"price", "bid_price", "ask_price", "bid_volume", "ask_volume"}),
    )
    created_ms = int(
      datetime.fromisoformat(intent.intent_metadata["intent_created_at"]).timestamp()
      * 1000
    )
    gate = EntryExecutionGateInput(
      candidate,
      "600000.SH",
      source.intent_id,
      True,
      True,
      created_ms,
      candidate.expires_at_ms,
      int(source.now.timestamp() * 1000),
      ExecutionEnvironment.PAPER,
      binding,
      binding,
      EntryExecutionGatePolicy("entry-gate-v1", 3000, 100, 30),
      capabilities,
      cursor.stream_id,
      cursor.continuity_generation,
      cursor.accepted_sequence,
      cursor.ring_generation,
      1,
      source.latest_tick.accepted_sequence,
      source.latest_tick,
      capabilities.required_fields,
    )
    result = await PaperEntryExecutionReview(db).review(
      execution_id=source.execution_id,
      intent_id=source.intent_id,
      gate_input=gate,
      market_data=market,
      now=source.now,
    )
    assert result.outcome == "ACCEPTED", result.reason_codes
    order = await db.get(PaperExecutionOrderRecord, result.order_id)
    assert (
      order.request_payload["metadata"]["entry_gate_witness"][
        "candidate_accepted_sequence"
      ]
      == 5
    )
    assert (
      order.request_payload["metadata"]["entry_gate_witness"][
        "latest_accepted_sequence"
      ]
      == 6
    )
