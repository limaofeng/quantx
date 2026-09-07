from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from quantx_application.t_trade_v3.execution_use_cases import (
  TAssistantExecutionLifecycle,
)
from quantx_domain.strategies.base import StrategyOutput
from quantx_domain.trading.t_assistant_execution import (
  TAssistantEntryAuthorization,
  TAssistantEntryReadiness,
  TAssistantEntryReadinessProjection,
  TAssistantExecution,
  TAssistantExecutionEvent,
  TAssistantExecutionStatus,
  TAssistantRolloutStage,
  TAssistantScorerMode,
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
)
from quantx_domain.trading.t_trade_opportunity_engine import (
  OPPORTUNITY_REFERENCE_PROFILE_SCHEMA_VERSION,
  OpportunityGateContext,
  OpportunityPolicy,
  OpportunityReferenceProfile,
  OpportunitySample,
  OpportunityState,
  reduce_opportunity,
)
from quantx_engine.instrument_universe_provider import InstrumentUniverseSnapshot
from quantx_engine.t_assistant_decision_runtime import (
  ShadowComparisonStatus,
  TAssistantPaperShadowRuntime,
)
from quantx_engine.t_assistant_paper_shadow_supervisor import (
  TAssistantPaperShadowSupervisor,
)
from quantx_engine.t_trade_decision_snapshot import (
  TDecisionSnapshotBuilder,
  TMarketCapture,
  TSymbolUniverseEntry,
)
from quantx_infrastructure.core.data.whole_quote_hub import (
  QuoteConsumerStatus,
  QuoteDeliveryMode,
)
from quantx_infrastructure.database.relational_base import Base
from quantx_infrastructure.models.agent_runtime import (
  OrderCorrelation,
  PendingTradeOrder,
  TradeCommandOutbox,
)
from quantx_infrastructure.models.auto_exit_plan import (
  AutoExitPlanEvent,
  AutoExitPlanRecord,
)
from quantx_infrastructure.models.entry_plan_authorization import (
  EntryAutomationGate,
  EntryPlanAuthorizationConsumption,
  EntryPlanAuthorizationEvent,
  EntryPlanAuthorizationGrant,
)
from quantx_infrastructure.models.order import Order
from quantx_infrastructure.models.risk_increase_admission import (
  AccountRiskIncreaseAdmissionBatch,
  AccountRiskIncreaseAdmissionItem,
)
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantConfigVersionRecord,
  TAssistantDecisionCycleRecord,
  TAssistantExecutionEventRecord,
  TAssistantExecutionRecord,
  TAssistantSymbolStateRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
  TTradeInstrumentProfile,
  TTradeOpportunityEvaluation,
)
from quantx_infrastructure.models.trade import Trade
from quantx_infrastructure.models.trade_confirmation_challenge import (
  TradeConfirmationChallenge,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_assistant_decision_cycle_repository import (
  TAssistantCycleConflict,
  TAssistantDecisionCycleRepository,
)
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

SHANGHAI = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 9, 3, 9, 30, 1, tzinfo=SHANGHAI)


class FakeWholeQuoteHub:
  def __init__(self) -> None:
    self.callback = None
    self.delivery = None
    self.handle = "critical-consumer-1"
    self.is_ready = True
    self.stream_id = "stream-1"
    self.generation = 7
    self.sequence = 0
    self.last_captured_at = NOW
    self.unsubscribed = []
    self.status = QuoteConsumerStatus.READY

  async def subscribe_batches(self, callback, *, delivery):
    self.callback = callback
    self.delivery = delivery
    return self.handle

  async def unsubscribe(self, handle):
    self.unsubscribed.append(handle)
    return True

  def consumer_status(self, handle):
    assert handle == self.handle
    return self.status

  async def emit(
    self,
    sequence: int,
    *,
    price: float = 10.0,
    source_time_ms: int | None = None,
    tick_ordinal: int | None = None,
  ) -> None:
    self.sequence = sequence
    self.last_captured_at = NOW
    assert self.callback is not None
    observed_source_time_ms = (
      int(NOW.timestamp() * 1000) + sequence
      if source_time_ms is None
      else source_time_ms
    )
    await self.callback(
      {
        "600000.SH": {
          "lastPrice": price,
          "time": observed_source_time_ms,
          "source_time_ms": observed_source_time_ms,
          "tick_ordinal": sequence if tick_ordinal is None else tick_ordinal,
          "continuity_generation": self.generation,
          "market_stream_id": self.stream_id,
          "market_stream_sequence": sequence,
          "market_stream_reset": False,
          "bidPrice": [price - 0.01],
          "askPrice": [price + 0.01],
          "bidVol": [1_000],
          "askVol": [1_000],
          "amount": sequence * 10_000,
          "volume": sequence * 1_000,
        }
      }
    )


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
    TTradeInstrumentProfile.__table__,
    TradeIntentRecord.__table__,
    PendingTradeOrder.__table__,
    TradeCommandOutbox.__table__,
    OrderCorrelation.__table__,
    Order.__table__,
    Trade.__table__,
    EntryPlanAuthorizationGrant.__table__,
    EntryPlanAuthorizationEvent.__table__,
    EntryPlanAuthorizationConsumption.__table__,
    EntryAutomationGate.__table__,
    TradeConfirmationChallenge.__table__,
    AccountRiskIncreaseAdmissionBatch.__table__,
    AccountRiskIncreaseAdmissionItem.__table__,
    AutoExitPlanRecord.__table__,
    AutoExitPlanEvent.__table__,
  ]
  async with engine.begin() as connection:
    await connection.run_sync(
      lambda sync: Base.metadata.create_all(sync, tables=tables)
    )
  factory = async_sessionmaker(engine, expire_on_commit=False)
  yield factory
  await engine.dispose()


def _execution() -> TAssistantExecution:
  policy = OpportunityPolicy()
  return TAssistantExecution(
    execution_id="execution-1",
    config_id="config-1",
    config_version_id="config-version-1",
    frozen_config_version=1,
    config_snapshot_hash="a" * 64,
    account_id="account-1",
    environment="PAPER",
    entry_authorization=TAssistantEntryAuthorization.MANUAL_CONFIRM,
    rollout_stage=TAssistantRolloutStage.CANARY,
    status=TAssistantExecutionStatus.RUNNING,
    readiness=TAssistantEntryReadinessProjection(
      readiness=TAssistantEntryReadiness.READY,
      reasons=(),
      as_of=NOW,
    ),
    policy_version=policy.policy_version,
    feature_schema_version=policy.feature_schema_version,
    scorer_mode=TAssistantScorerMode.RULE_ONLY,
    started_at=NOW,
  )


def _accepted(sequence: int) -> AcceptedTMarketTick:
  observed = int(NOW.timestamp() * 1000) + sequence
  return AcceptedTMarketTick(
    stream_id="stream-1",
    accepted_sequence=sequence,
    received_at_ms=observed,
    sample=OpportunitySample(
      instrument_code="600000.SH",
      trade_date="2026-09-03",
      source_time_ms=observed,
      tick_ordinal=sequence,
      price=10.0 + sequence / 100,
      continuity_generation="7",
      received_at_ms=observed,
      bid_price=9.99,
      ask_price=10.01,
      bid_volume=1_000,
      ask_volume=1_000,
      cumulative_amount=sequence * 10_000,
      cumulative_volume=sequence * 1_000,
    ),
  )


def test_snapshot_builder_never_reads_past_capture_fence() -> None:
  execution = _execution()
  builder = TDecisionSnapshotBuilder()
  first = _accepted(1)
  second = _accepted(2)
  builder.accept_tick(first, capture_time_ms=first.received_at_ms)
  capture = TMarketCapture(
    stream_id="stream-1",
    continuity_generation="7",
    fence_sequence=1,
    captured_at=NOW,
    ready=True,
  )
  # Models the callback/build race: sequence 2 arrives after the header fence.
  builder.accept_tick(second, capture_time_ms=second.received_at_ms)
  state = TAssistantSymbolState.initial(
    execution_id=execution.execution_id,
    instrument_code="600000.SH",
    policy_version=execution.policy_version,
    feature_schema_version=execution.feature_schema_version,
    trade_date="2026-09-03",
  )

  snapshot = builder.build(
    execution=execution,
    capture=capture,
    symbol_states={"600000.SH": state},
    universe=(TSymbolUniverseEntry("600000.SH"),),
    decision_time=NOW,
    trade_date="2026-09-03",
    market_gate_context=OpportunityGateContext(
      continuous_session=True,
      session_code="CONTINUOUS_AM",
    ),
  )

  assert snapshot.fence_sequence == 1
  assert [tick.accepted_sequence for tick in snapshot.symbols[0].delta_slice.ticks] == [
    1
  ]
  assert snapshot.symbols[0].delta_slice.to_accepted_sequence_inclusive == 1


async def test_rejected_quote_does_not_advance_sequence_or_create_cycle(
  sessions,
) -> None:
  policy = OpportunityPolicy()
  config = TTradeGlobalConfig(
    id="config-rejected-tick",
    account_id="account-rejected-tick",
    enabled=True,
    mode="paper",
    ignored_stock_codes=[],
    settings={"signal_policy": policy.to_dict()},
    config_version=1,
    desired_environment="PAPER",
    state_version=1,
    universe_revision=1,
  )
  async with sessions() as db:
    db.add(config)
    await db.commit()
  hub = FakeWholeQuoteHub()
  supervisor = TAssistantPaperShadowSupervisor(
    quote_hub=hub,
    session_factory=sessions,
    clock=lambda: NOW,
  )
  await supervisor.start()
  execution_id = await supervisor.reconcile(
    config=config,
    universe=InstrumentUniverseSnapshot.create(
      mode="ACCOUNT_HOLDINGS",
      instruments=("600000.SH",),
      metadata={"600000.SH": {"eligible": True}},
    ),
  )
  await hub.emit(1)
  first_identity_ms = int(NOW.timestamp() * 1000) + 1
  async with sessions() as db:
    first_cycles = int(
      await db.scalar(select(func.count(TAssistantDecisionCycleRecord.cycle_id))) or 0
    )
    first_comparisons = int(
      await db.scalar(
        select(func.count(TAssistantExecutionEventRecord.event_id)).where(
          TAssistantExecutionEventRecord.event_type == "PAPER_SHADOW_RULE_COMPARISON"
        )
      )
      or 0
    )

  await hub.emit(
    2,
    source_time_ms=first_identity_ms,
    tick_ordinal=1,
  )
  await hub.emit(
    3,
    source_time_ms=first_identity_ms - 1,
    tick_ordinal=1,
  )
  await hub.emit(
    4,
    source_time_ms=int(NOW.timestamp() * 1000) + 6_000,
    tick_ordinal=4,
  )
  assert supervisor._bindings[execution_id].accepted_sequences["600000.SH"] == 1
  async with sessions() as db:
    assert (
      await db.scalar(select(func.count(TAssistantDecisionCycleRecord.cycle_id)))
      == first_cycles
    )
    assert (
      await db.scalar(
        select(func.count(TAssistantExecutionEventRecord.event_id)).where(
          TAssistantExecutionEventRecord.event_type == "PAPER_SHADOW_RULE_COMPARISON"
        )
      )
      == first_comparisons
    )

  await hub.emit(5)
  assert supervisor._bindings[execution_id].accepted_sequences["600000.SH"] == 2
  async with sessions() as db:
    assert (
      await db.scalar(select(func.count(TAssistantDecisionCycleRecord.cycle_id)))
      == first_cycles + 1
    )
  await supervisor.stop()


async def test_supervisor_is_restart_idempotent_and_writes_no_order_chain(
  sessions,
) -> None:
  policy = OpportunityPolicy()
  config = TTradeGlobalConfig(
    id="config-1",
    account_id="account-1",
    enabled=True,
    mode="live",
    auto_exit_acknowledged=True,
    ignored_stock_codes=[],
    settings={"signal_policy": policy.to_dict()},
    config_version=1,
    desired_environment="LIVE",
    state_version=1,
    universe_revision=1,
  )
  async with sessions() as db:
    db.add(config)
    await db.commit()
  universe = InstrumentUniverseSnapshot.create(
    mode="ACCOUNT_HOLDINGS",
    instruments=("600000.SH",),
    metadata={"600000.SH": {"eligible": True, "reason": "ELIGIBLE"}},
  )
  hub = FakeWholeQuoteHub()
  supervisor = TAssistantPaperShadowSupervisor(
    quote_hub=hub,
    session_factory=sessions,
    clock=lambda: NOW,
  )

  await supervisor.start()
  assert supervisor.is_running is True
  hub.status = QuoteConsumerStatus.LAGGING
  assert supervisor.is_running is False
  hub.status = QuoteConsumerStatus.READY
  first_id = await supervisor.reconcile(config=config, universe=universe)
  second_id = await supervisor.reconcile(config=config, universe=universe)
  assert first_id == second_id
  assert hub.delivery is QuoteDeliveryMode.CRITICAL

  crash_builder = TDecisionSnapshotBuilder()
  crash_tick = _accepted(1)
  crash_builder.accept_tick(
    crash_tick,
    capture_time_ms=crash_tick.received_at_ms,
  )
  async with sessions() as db:
    async with db.begin():
      execution = await TAssistantExecutionRepository(db).get_domain(first_id)
      crash_snapshot = crash_builder.build(
        execution=execution,
        capture=TMarketCapture(
          stream_id="stream-1",
          continuity_generation="7",
          fence_sequence=1,
          captured_at=NOW,
          ready=True,
        ),
        symbol_states={},
        universe=(TSymbolUniverseEntry("600000.SH"),),
        decision_time=NOW,
        trade_date="2026-09-03",
        market_gate_context=OpportunityGateContext(
          continuous_session=True,
          session_code="CONTINUOUS_AM",
        ),
      )
      await TAssistantDecisionCycleRepository(db).prepare_material_cycle(
        snapshot=crash_snapshot,
        cycle_id="prepared-before-restart",
        now=NOW,
      )

  await supervisor.reconcile(config=config, universe=universe)
  async with sessions() as db:
    current = await db.get(TAssistantDecisionCycleRecord, "prepared-before-restart")
    assert current.status == "PREPARED"
  await supervisor.stop()
  await supervisor.start()
  await supervisor.reconcile(config=config, universe=universe)
  async with sessions() as db:
    recovered = await db.get(
      TAssistantDecisionCycleRecord,
      "prepared-before-restart",
    )
    assert recovered.status == "ABORTED_STALE"
    assert recovered.abort_reason == "T_CYCLE_RECOVERY_SNAPSHOT_UNAVAILABLE"

  await hub.emit(1)
  await supervisor.stop()

  restarted = TAssistantPaperShadowSupervisor(
    quote_hub=hub,
    session_factory=sessions,
    clock=lambda: NOW,
  )
  await restarted.start()
  restored_id = await restarted.reconcile(config=config, universe=universe)
  assert restored_id == first_id
  await hub.emit(2, price=10.01)

  async with sessions() as db:
    assert (
      await db.scalar(
        select(func.count(TAssistantConfigVersionRecord.config_version_id))
      )
      == 1
    )
    assert (
      await db.scalar(select(func.count(TAssistantExecutionRecord.execution_id))) == 1
    )
    assert (
      await db.scalar(select(func.count(TAssistantDecisionCycleRecord.cycle_id))) >= 2
    )
    assert (
      await db.scalar(
        select(func.count(TAssistantExecutionEventRecord.event_id)).where(
          TAssistantExecutionEventRecord.event_type == "PAPER_SHADOW_RULE_COMPARISON"
        )
      )
      >= 2
    )
    assert await db.scalar(select(func.count(TradeIntentRecord.id))) == 0
    assert await db.scalar(select(func.count(PendingTradeOrder.client_order_id))) == 0
    assert await db.scalar(select(func.count(TradeCommandOutbox.message_id))) == 0
    execution = await db.scalar(select(TAssistantExecutionRecord))
    assert execution.environment == "PAPER"
    assert execution.config_snapshot_hash != stable_manifest_hash(
      {"signal_policy": policy.to_dict()}
    )

  revoke_builder = TDecisionSnapshotBuilder()
  revoke_tick = replace(_accepted(1), market_fence_sequence=99)
  revoke_builder.accept_tick(
    revoke_tick,
    capture_time_ms=revoke_tick.received_at_ms,
  )
  async with sessions() as db:
    async with db.begin():
      predecessor = await TAssistantExecutionRepository(db).get_domain(first_id)
      revoke_snapshot = revoke_builder.build(
        execution=predecessor,
        capture=TMarketCapture(
          stream_id="stream-1",
          continuity_generation="7",
          fence_sequence=99,
          captured_at=NOW,
          ready=True,
        ),
        symbol_states={},
        universe=(TSymbolUniverseEntry("600000.SH"),),
        decision_time=NOW,
        trade_date="2026-09-03",
        market_gate_context=OpportunityGateContext(
          continuous_session=True,
          session_code="CONTINUOUS_AM",
        ),
      )
      revoke_cycle = await TAssistantDecisionCycleRepository(db).prepare_material_cycle(
        snapshot=revoke_snapshot,
        cycle_id="claimed-before-successor",
        now=NOW,
      )
    async with db.begin():
      await TAssistantDecisionCycleRepository(db).claim(
        cycle_id=revoke_cycle.cycle_id,
        processing_owner="old-worker",
        expected_input_manifest_hash=revoke_cycle.input_manifest_hash,
        now=NOW,
      )

  successor_settings = {
    "signal_policy": policy.to_dict(),
    "target_trade_amount": 20_000,
  }
  async with sessions() as db:
    async with db.begin():
      head = await db.get(TTradeGlobalConfig, config.id)
      head.settings = successor_settings
      head.config_version = 2
  config.settings = successor_settings
  config.config_version = 2
  successor_id = await restarted.reconcile(config=config, universe=universe)
  assert successor_id is not None
  assert successor_id != first_id
  assert set(restarted._bindings) == {successor_id}
  async with sessions() as db:
    executions = list(
      (
        await db.execute(
          select(TAssistantExecutionRecord).order_by(
            TAssistantExecutionRecord.created_at
          )
        )
      )
      .scalars()
      .all()
    )
    assert len(executions) == 2
    assert executions[0].status == "STOPPED"
    assert executions[1].status == "WARMING"
    assert executions[1].config_version_id != executions[0].config_version_id
    revoked = await db.get(
      TAssistantDecisionCycleRecord,
      "claimed-before-successor",
    )
    assert revoked.status == "ABORTED_STALE"
    assert revoked.abort_reason == "T_CYCLE_CONFIG_SUCCESSOR_REVOKED"
    assert revoked.processing_fence_token is None

  disable_builder = TDecisionSnapshotBuilder()
  disable_tick = replace(_accepted(1), market_fence_sequence=100)
  disable_builder.accept_tick(
    disable_tick,
    capture_time_ms=disable_tick.received_at_ms,
  )
  async with sessions() as db:
    async with db.begin():
      successor = await TAssistantExecutionRepository(db).get_domain(successor_id)
      disable_snapshot = disable_builder.build(
        execution=successor,
        capture=TMarketCapture(
          stream_id="stream-1",
          continuity_generation="7",
          fence_sequence=100,
          captured_at=NOW,
          ready=True,
        ),
        symbol_states={},
        universe=(TSymbolUniverseEntry("600000.SH"),),
        decision_time=NOW,
        trade_date="2026-09-03",
        market_gate_context=OpportunityGateContext(
          continuous_session=True,
          session_code="CONTINUOUS_AM",
        ),
      )
      await TAssistantDecisionCycleRepository(db).prepare_material_cycle(
        snapshot=disable_snapshot,
        cycle_id="prepared-before-disable",
        now=NOW,
      )

  config.enabled = False
  async with sessions() as db:
    async with db.begin():
      head = await db.get(TTradeGlobalConfig, config.id)
      head.enabled = False
  await restarted.reconcile(
    config=config,
    universe=InstrumentUniverseSnapshot.create(
      mode="ACCOUNT_HOLDINGS",
      instruments=(),
    ),
  )
  async with sessions() as db:
    executions = list(
      (await db.execute(select(TAssistantExecutionRecord))).scalars().all()
    )
    assert {execution.status for execution in executions} == {"STOPPED"}
    assert {execution.entry_readiness for execution in executions} == {"BLOCKED"}
    disabled_cycle = await db.get(
      TAssistantDecisionCycleRecord,
      "prepared-before-disable",
    )
    assert disabled_cycle.status == "ABORTED_STALE"
    assert disabled_cycle.abort_reason == "T_CYCLE_CONFIG_DISABLED"

  await restarted.stop()
  assert hub.unsubscribed == [hub.handle, hub.handle, hub.handle]


def test_shadow_comparison_joins_nested_legacy_phase_on_exact_source_fence() -> None:
  source_identity = {
    "continuity_generation": "7",
    "source_time_ms": 1_787_974_201_000,
    "tick_ordinal": 9,
  }
  new_result = {
    "data_health": "READY",
    "pullback_phase": "CONFIRMED",
    "momentum_phase": "FORMING",
    "selected_path": "PULLBACK",
    "opportunity_score": 88.0,
    "candidate_status": "LATCHED",
    "candidate_id": "candidate-1",
    "candidate_fingerprint": "f" * 64,
    "source_identity": source_identity,
    "market_fence_sequence": 42,
  }
  legacy_result = {
    "data_health": "READY",
    "pullback": {"phase": "CONFIRMED"},
    "momentum": {"phase": "FORMING"},
    "selected_path": "PULLBACK",
    "opportunity_score": 88.0,
    "candidate_status": "LATCHED",
    "candidate_id": "candidate-1",
    "candidate_fingerprint": "f" * 64,
    **source_identity,
    "market_fence_sequence": 42,
  }
  output = StrategyOutput(trace_payload={"symbol_results": {"600000.SH": new_result}})

  exact = TAssistantPaperShadowRuntime._compare(
    output=output,
    legacy_results={"600000.SH": legacy_result},
  )
  assert exact[0].status is ShadowComparisonStatus.MATCH
  assert exact[0].difference_codes == ()

  mismatched = TAssistantPaperShadowRuntime._compare(
    output=output,
    legacy_results={"600000.SH": {**legacy_result, "market_fence_sequence": 43}},
  )
  assert mismatched[0].status is ShadowComparisonStatus.UNAVAILABLE
  assert mismatched[0].difference_codes == ("LEGACY_SOURCE_FENCE_MISMATCH",)


async def test_supervisor_persists_exact_fence_legacy_comparison(sessions) -> None:
  policy = OpportunityPolicy()
  config = TTradeGlobalConfig(
    id="config-compare",
    account_id="account-compare",
    enabled=True,
    mode="paper",
    ignored_stock_codes=[],
    settings={"signal_policy": policy.to_dict()},
    config_version=1,
    desired_environment="PAPER",
    state_version=1,
    universe_revision=1,
  )
  expected_source_time_ms = int(NOW.timestamp() * 1000) + 2
  async with sessions() as db:
    db.add(config)
    await db.commit()

  async def persist_legacy_after_shadow_starts() -> None:
    await asyncio.sleep(0.01)
    async with sessions() as db:
      db.add(
        TTradeOpportunityEvaluation(
          id="legacy-evaluation-1",
          event_key="legacy-run-1:600000.SH:2",
          account_id="account-compare",
          owner_type="STRATEGY_RUN",
          owner_id="legacy-run-1",
          environment="PAPER",
          strategy_run_id="legacy-run-1",
          instrument_code="600000.SH",
          candidate_id=None,
          evaluated_at=NOW.replace(tzinfo=None),
          record_kind="MATERIAL",
          event_type="T_TRADE_OPPORTUNITY_EVALUATION",
          coalesced_count=1,
          policy_version=policy.policy_version,
          schema_version=str(policy.feature_schema_version),
          content_fingerprint="a" * 64,
          payload={
            "signal_snapshot": {
              "continuity_generation": "7",
              "source_time_ms": expected_source_time_ms,
              "tick_ordinal": 2,
              "market_fence_sequence": 2,
              "data_health": "LEGACY_VALUE",
              "pullback": {"phase": "LEGACY_PHASE"},
              "momentum": {"phase": "LEGACY_PHASE"},
            }
          },
          metrics={},
        )
      )
      await db.commit()

  hub = FakeWholeQuoteHub()
  supervisor = TAssistantPaperShadowSupervisor(
    quote_hub=hub,
    session_factory=sessions,
    clock=lambda: NOW,
  )
  await supervisor.start()
  execution_id = await supervisor.reconcile(
    config=config,
    universe=InstrumentUniverseSnapshot.create(
      mode="ACCOUNT_HOLDINGS",
      instruments=("600000.SH",),
      metadata={"600000.SH": {"eligible": True}},
    ),
    legacy_results={"600000.SH": {"strategy_run_id": "legacy-run-1"}},
  )
  await hub.emit(1)
  delayed_legacy = asyncio.create_task(persist_legacy_after_shadow_starts())
  await hub.emit(2)
  await delayed_legacy

  result = supervisor.last_result(execution_id)
  assert result is not None
  assert result.comparisons[0].status is ShadowComparisonStatus.DIFFERENT
  assert "LEGACY_SOURCE_FENCE_MISMATCH" not in (result.comparisons[0].difference_codes)
  async with sessions() as db:
    rows = list(
      (
        await db.execute(
          select(TAssistantExecutionEventRecord).where(
            TAssistantExecutionEventRecord.event_type == "PAPER_SHADOW_RULE_COMPARISON"
          )
        )
      )
      .scalars()
      .all()
    )
    assert len(rows) == 2
    assert rows[-1].payload["status"] == "DIFFERENT"
  await supervisor.stop()


async def test_supervisor_binds_only_prior_trade_date_reference_profile(
  sessions,
) -> None:
  policy = OpportunityPolicy()
  config = TTradeGlobalConfig(
    id="config-profile",
    account_id="account-profile",
    enabled=True,
    mode="paper",
    ignored_stock_codes=[],
    settings={"signal_policy": policy.to_dict()},
    config_version=1,
    desired_environment="PAPER",
    state_version=1,
    universe_revision=1,
  )
  profile_payload = {
    "pullback_threshold_pct": 0.8,
    "momentum_rise_threshold_pct": 0.8,
    "momentum_amount_velocity_ratio": 2.0,
    "pullback_max_spread_ticks": 3,
    "momentum_max_spread_ticks": 10,
  }
  async with sessions() as db:
    db.add(config)
    db.add_all(
      [
        TTradeInstrumentProfile(
          id="profile-prior",
          instrument_code="600000.SH",
          as_of=datetime(2026, 9, 2, 15),
          profile=profile_payload,
          schema_version="1",
          version="prior-v1",
          fingerprint="b" * 64,
          metrics={},
          data_manifest={},
        ),
        TTradeInstrumentProfile(
          id="profile-future",
          instrument_code="600000.SH",
          as_of=datetime(2026, 9, 3, 9),
          profile=profile_payload,
          schema_version="1",
          version="same-day-v2",
          fingerprint="c" * 64,
          metrics={},
          data_manifest={},
        ),
      ]
    )
    await db.commit()
  supervisor = TAssistantPaperShadowSupervisor(
    quote_hub=FakeWholeQuoteHub(),
    session_factory=sessions,
    clock=lambda: NOW,
  )
  execution_id = await supervisor.reconcile(
    config=config,
    universe=InstrumentUniverseSnapshot.create(
      mode="ACCOUNT_HOLDINGS",
      instruments=("600000.SH",),
      metadata={"600000.SH": {"eligible": True}},
    ),
  )

  profile = supervisor._bindings[execution_id].universe[0].reference_profile
  assert profile is not None
  assert profile.profile_version == "prior-v1"
  assert profile.as_of_trade_date == "2026-09-02"


async def test_shadow_runtime_late_owner_conflict_never_commits_partial_state(
  sessions, monkeypatch,
) -> None:
  config = TTradeGlobalConfig(
    id="late-config", account_id="late-account", enabled=True, mode="paper",
    settings={"signal_policy": OpportunityPolicy().to_dict()},
    ignored_stock_codes=[], config_version=1, desired_environment="PAPER",
    state_version=1, universe_revision=1,
  )
  async with sessions() as db:
    db.add(config)
    await db.commit()
  hub = FakeWholeQuoteHub()
  supervisor = TAssistantPaperShadowSupervisor(
    quote_hub=hub, session_factory=sessions, clock=lambda: NOW,
  )
  await supervisor.start()
  try:
    execution_id = await supervisor.reconcile(
      config=config,
      universe=InstrumentUniverseSnapshot.create(
        mode="ACCOUNT_HOLDINGS", instruments=("600000.SH",),
        metadata={"600000.SH": {"eligible": True}},
      ),
    )
    monkeypatch.setattr(
      supervisor._runtime, "_comparison_events",
      lambda **_kwargs: (TAssistantExecutionEvent(
        execution_id="wrong-owner", event_key="invalid-event", event_type="INVALID",
        occurred_at=NOW, payload={},
      ),),
    )
    with pytest.raises(TAssistantCycleConflict, match="OWNER_CONFLICT"):
      await hub.emit(1)
    async with sessions() as db:
      assert await db.scalar(select(func.count(TAssistantSymbolStateRecord.state_id))) == 0
      assert await db.scalar(select(func.count(TTradeOpportunityEvaluation.id))) == 0
      cycle = await db.scalar(select(TAssistantDecisionCycleRecord))
      assert cycle.status == "PREPARED"
      execution = await db.get(TAssistantExecutionRecord, execution_id)
      assert execution.last_committed_cycle_sequence == 0
    assert supervisor._runtime.symbol_states(execution_id) == {}
  finally:
    await supervisor.stop()


async def _reconcile_cursor_probe(sessions):
  config = TTradeGlobalConfig(
    id="cursor-config", account_id="cursor-account", enabled=True, mode="paper",
    settings={"signal_policy": OpportunityPolicy().to_dict()},
    ignored_stock_codes=[], config_version=1, desired_environment="PAPER",
    state_version=1, universe_revision=1,
  )
  async with sessions() as db:
    db.add(config)
    await db.commit()
  hub = FakeWholeQuoteHub()
  supervisor = TAssistantPaperShadowSupervisor(
    quote_hub=hub, session_factory=sessions, clock=lambda: NOW,
  )
  universe = InstrumentUniverseSnapshot.create(
    mode="ACCOUNT_HOLDINGS", instruments=("600000.SH",),
    metadata={"600000.SH": {"eligible": True}},
  )
  await supervisor.start()
  execution_id = await supervisor.reconcile(config=config, universe=universe)
  for sequence in range(1, 5):
    await hub.emit(sequence)
  return config, hub, supervisor, universe, execution_id


async def test_same_execution_reconcile_preserves_hot_cursor_with_live_ring(sessions):
  config, hub, supervisor, universe, execution_id = await _reconcile_cursor_probe(sessions)
  try:
    hot = supervisor._runtime.symbol_states(execution_id)["600000.SH"]
    async with sessions() as db:
      durable = await db.scalar(select(TAssistantSymbolStateRecord))
      assert durable.last_accepted_sequence < 4
    builder = supervisor._bindings[execution_id].builder
    await supervisor.reconcile(config=config, universe=universe)
    assert supervisor._bindings[execution_id].builder is builder
    assert supervisor._runtime.symbol_states(execution_id)["600000.SH"] == hot
    assert supervisor._bindings[execution_id].accepted_sequences["600000.SH"] == 4
    for sequence in range(5, 8):
      await hub.emit(sequence)
    assert supervisor._bindings[execution_id].accepted_sequences["600000.SH"] == 7
    assert supervisor._runtime.symbol_states(execution_id)["600000.SH"].cursor.accepted_sequence == 7
  finally:
    await supervisor.stop()


async def test_reconcile_waits_for_inflight_quote_cycle(sessions, monkeypatch):
  config, hub, supervisor, universe, _ = await _reconcile_cursor_probe(sessions)
  entered, release = asyncio.Event(), asyncio.Event()
  original = supervisor._runtime.run_cycle

  async def blocked(**kwargs):
    entered.set()
    await release.wait()
    return await original(**kwargs)

  monkeypatch.setattr(supervisor._runtime, "run_cycle", blocked)
  quote = asyncio.create_task(hub.emit(5))
  await asyncio.wait_for(entered.wait(), timeout=2)
  reconcile = asyncio.create_task(supervisor.reconcile(config=config, universe=universe))
  try:
    await asyncio.sleep(0)
    assert not reconcile.done()
    release.set()
    await quote
    await reconcile
  finally:
    release.set()
    await asyncio.gather(quote, reconcile, return_exceptions=True)
    await supervisor.stop()


@pytest.mark.parametrize("boundary", ["restart", "market_generation", "universe", "config"])
async def test_reconcile_boundaries_do_not_reuse_old_windows(sessions, boundary):
  config, hub, supervisor, universe, execution_id = await _reconcile_cursor_probe(sessions)
  try:
    old_builder = supervisor._bindings[execution_id].builder
    old_state = supervisor._runtime.symbol_states(execution_id)["600000.SH"]
    if boundary == "restart":
      await supervisor.stop()
      supervisor = TAssistantPaperShadowSupervisor(
        quote_hub=hub, session_factory=sessions, clock=lambda: NOW,
      )
      await supervisor.start()
    elif boundary == "market_generation":
      hub.generation = 8
    elif boundary == "universe":
      config.universe_revision = 2
      async with sessions() as db:
        head = await db.get(TTradeGlobalConfig, config.id)
        head.universe_revision = 2
        await db.commit()
    else:
      config.config_version = 2
      config.settings = {**config.settings, "target_trade_amount": 20_000}
      async with sessions() as db:
        head = await db.get(TTradeGlobalConfig, config.id)
        head.config_version = 2
        head.settings = config.settings
        await db.commit()
    current_id = await supervisor.reconcile(config=config, universe=universe)
    binding = supervisor._bindings[current_id]
    if boundary == "config":
      assert current_id != execution_id
      assert binding.builder is not old_builder
      assert supervisor._runtime.symbol_states(current_id) == {}
      assert binding.accepted_sequences["600000.SH"] == 0
    else:
      assert current_id == execution_id
      if boundary == "restart":
        assert binding.builder is not old_builder
        assert binding.accepted_sequences["600000.SH"] < 4
      await hub.emit(5)
      state = supervisor._runtime.symbol_states(current_id)["600000.SH"]
      assert state.rewarm_reason is not None
      assert state.deferred_candidate is None
      assert state.cursor.ring_generation > old_state.cursor.ring_generation
      if boundary == "market_generation":
        assert state.cursor.continuity_generation == "8"
        assert binding.accepted_sequences["600000.SH"] == 1
      await hub.emit(6)
      assert supervisor._runtime.symbol_states(current_id)["600000.SH"].rewarm_reason is None
  finally:
    await supervisor.stop()


async def test_real_candidate_proposal_stays_inside_shadow_cycle(sessions) -> None:
  policy = OpportunityPolicy()
  config = TTradeGlobalConfig(
    id="config-happy",
    account_id="account-happy",
    enabled=True,
    mode="paper",
    ignored_stock_codes=[],
    settings={"signal_policy": policy.to_dict(), "target_trade_amount": 10_000},
    config_version=1,
    desired_environment="PAPER",
    state_version=1,
    universe_revision=1,
  )
  hub = FakeWholeQuoteHub()
  supervisor = TAssistantPaperShadowSupervisor(
    quote_hub=hub,
    session_factory=sessions,
    clock=lambda: NOW,
  )
  async with sessions() as db:
    db.add(config)
    await db.commit()
  execution_id = await supervisor.reconcile(
    config=config,
    universe=InstrumentUniverseSnapshot.create(
      mode="ACCOUNT_HOLDINGS",
      instruments=("600000.SH",),
      metadata={"600000.SH": {"eligible": True}},
    ),
  )
  async with sessions() as db:
    async with db.begin():
      repository = TAssistantExecutionRepository(db)
      execution = await repository.get_domain(execution_id)
      assert execution is not None

  profile = OpportunityReferenceProfile(
    profile_version="profile-v1",
    profile_schema_version=OPPORTUNITY_REFERENCE_PROFILE_SCHEMA_VERSION,
    as_of_trade_date="2026-09-02",
    pullback_threshold_pct=0.8,
    momentum_rise_threshold_pct=0.8,
    momentum_amount_velocity_ratio=2.0,
    pullback_max_spread_ticks=3,
    momentum_max_spread_ticks=10,
  )
  base_ms = int(NOW.timestamp() * 1000) - 24_000
  shapes = (
    (0, 100.0, 1_000_000, 10_000),
    (5, 99.0, 1_050_000, 10_500),
    (20, 99.0, 1_100_000, 11_000),
    (22, 99.30, 1_120_000, 11_200),
    (24, 99.32, 1_140_000, 11_400),
  )
  samples = [
    OpportunitySample(
      instrument_code="600000.SH",
      trade_date="2026-09-03",
      source_time_ms=base_ms + seconds * 1_000,
      tick_ordinal=index + 1,
      price=price,
      continuity_generation="7",
      received_at_ms=base_ms + seconds * 1_000,
      bid_price=price - 0.01,
      ask_price=price,
      bid_volume=1_000,
      ask_volume=1_000,
      cumulative_amount=amount,
      cumulative_volume=volume,
    )
    for index, (seconds, price, amount, volume) in enumerate(shapes)
  ]
  opportunity = OpportunityState.initial()
  for sample in samples[:4]:
    opportunity = reduce_opportunity(
      opportunity,
      sample,
      policy=policy,
      reference_profile=profile,
    ).state
  cursor = SymbolMarketCursor(
    stream_id="stream-1",
    continuity_generation="7",
    ring_generation=1,
    accepted_sequence=4,
    source_identity=TMarketSourceIdentity("7", samples[3].source_time_ms, 4),
  )
  state = TAssistantSymbolState(
    execution_id=execution_id,
    instrument_code="600000.SH",
    revision=0,
    lifecycle="ACTIVE",
    cursor=cursor,
    opportunity_state=opportunity,
    policy_version=policy.policy_version,
    feature_schema_version=policy.feature_schema_version,
  )
  ring = SymbolMarketDeltaRing("600000.SH")
  last = AcceptedTMarketTick(
    stream_id="stream-1",
    accepted_sequence=5,
    market_fence_sequence=5,
    received_at_ms=samples[4].received_at_ms,
    sample=samples[4],
  )
  ring.accept(last, capture_time_ms=int(NOW.timestamp() * 1000))
  symbol = SymbolDecisionSnapshot(
    instrument_code="600000.SH",
    state=state,
    delta_slice=ring.slice_after(
      cursor,
      decision_time_ms=int(NOW.timestamp() * 1000),
      through_accepted_sequence=5,
    ),
    gate_context=OpportunityGateContext(
      continuous_session=True,
      session_code="CONTINUOUS_AM",
    ),
    reference_profile=profile,
  )
  snapshot = TDecisionSnapshot(
    execution_ref=execution.execution_ref,
    decision_time=NOW,
    trade_date="2026-09-03",
    stream_id="stream-1",
    continuity_generation="7",
    fence_sequence=5,
    capture_as_of=NOW,
    universe_revision=execution.universe_revision,
    config_version=execution.frozen_config_version,
    config_snapshot_hash=execution.config_snapshot_hash,
    policy_version=execution.policy_version,
    feature_schema_version=execution.feature_schema_version,
    execution_status=execution.status,
    entry_readiness=execution.readiness.readiness,
    entry_readiness_as_of=execution.readiness.as_of,
    symbols=(symbol,),
  )
  runtime = TAssistantPaperShadowRuntime(
    session_factory=sessions,
    clock=lambda: NOW,
  )
  runtime.bind_execution(
    execution,
    parameters={
      "account_id": "account-happy",
      "signal_policy": policy.to_dict(),
      "target_trade_amount": 10_000,
    },
    symbol_states={"600000.SH": state},
  )
  async with sessions() as db:
    record = await db.get(TAssistantExecutionRecord, execution_id)
    assert record is not None
    assert int(record.frozen_config_version) == snapshot.config_version
    assert record.config_snapshot_hash == snapshot.config_snapshot_hash
    assert record.policy_version == snapshot.policy_version
    assert int(record.feature_schema_version) == snapshot.feature_schema_version
    assert int(record.universe_revision) == snapshot.universe_revision
    assert record.status == snapshot.execution_status.value
    assert record.entry_readiness == snapshot.entry_readiness.value
    assert record.scorer_mode == snapshot.scorer_mode
  warming_result = await runtime.run_cycle(
    execution=execution,
    snapshot=snapshot,
  )
  assert warming_result.committed is True
  assert warming_result.output.trade_intents == []
  deferred_state = runtime.symbol_states(execution_id)["600000.SH"]
  assert deferred_state.deferred_candidate is not None
  assert deferred_state.deferred_candidate_fence_sequence == 5

  async with sessions() as db:
    async with db.begin():
      repository = TAssistantExecutionRepository(db)
      current = await repository.get_domain(execution_id)
      assert current is not None
      execution = await TAssistantExecutionLifecycle(repository).activate_ready(
        current,
        at=NOW,
        payload={"paper_shadow_only": True, "test": "deferred-candidate"},
      )
  replay_symbol = SymbolDecisionSnapshot(
    instrument_code="600000.SH",
    state=deferred_state,
    delta_slice=ring.slice_after(
      deferred_state.cursor,
      decision_time_ms=int(NOW.timestamp() * 1000),
      through_accepted_sequence=5,
    ),
    gate_context=OpportunityGateContext(
      continuous_session=True,
      session_code="CONTINUOUS_AM",
    ),
    reference_profile=profile,
  )
  replay_snapshot = TDecisionSnapshot(
    execution_ref=execution.execution_ref,
    decision_time=NOW,
    trade_date="2026-09-03",
    stream_id="stream-1",
    continuity_generation="7",
    fence_sequence=5,
    capture_as_of=NOW,
    universe_revision=execution.universe_revision,
    config_version=execution.frozen_config_version,
    config_snapshot_hash=execution.config_snapshot_hash,
    policy_version=execution.policy_version,
    feature_schema_version=execution.feature_schema_version,
    execution_status=execution.status,
    entry_readiness=execution.readiness.readiness,
    entry_readiness_as_of=execution.readiness.as_of,
    symbols=(replay_symbol,),
  )
  runtime.bind_execution(
    execution,
    parameters={
      "account_id": "account-happy",
      "signal_policy": policy.to_dict(),
      "target_trade_amount": 10_000,
    },
    symbol_states={"600000.SH": deferred_state},
  )
  blocked_replay = replace(
    replay_snapshot,
    symbols=(replace(replay_symbol, blockers=("T_QUOTE_STALE",)),),
  )
  blocked_result = await runtime.run_cycle(
    execution=execution,
    snapshot=blocked_replay,
  )
  assert blocked_result.output.trade_intents == []
  assert runtime.symbol_states(execution_id)["600000.SH"].deferred_candidate is not None
  result = await runtime.run_cycle(
    execution=execution,
    snapshot=replay_snapshot,
  )

  assert result.committed is True
  assert len(result.output.trade_intents) == 1
  assert runtime.symbol_states(execution_id)["600000.SH"].deferred_candidate is None
  async with sessions() as db:
    cycle = await db.get(TAssistantDecisionCycleRecord, result.cycle_id)
    assert len(cycle.output_manifest["paper_shadow_intent_proposals"]) == 1
    evidence = list(
      (
        await db.execute(
          select(TTradeOpportunityEvaluation).where(
            TTradeOpportunityEvaluation.owner_type
            == "T_ASSISTANT_EXECUTION",
            TTradeOpportunityEvaluation.owner_id == execution_id,
          )
        )
      )
      .scalars()
      .all()
    )
    assert any(
      dict(item.payload.get("signal_snapshot") or {}).get("deferred_release")
      is True
      and item.candidate_id == deferred_state.deferred_candidate.candidate_id
      for item in evidence
    )
    for model, column in (
      (TradeIntentRecord, TradeIntentRecord.id),
      (PendingTradeOrder, PendingTradeOrder.client_order_id),
      (TradeCommandOutbox, TradeCommandOutbox.message_id),
      (OrderCorrelation, OrderCorrelation.id),
      (Order, Order.id),
      (Trade, Trade.id),
      (EntryPlanAuthorizationGrant, EntryPlanAuthorizationGrant.grant_id),
      (EntryPlanAuthorizationEvent, EntryPlanAuthorizationEvent.event_id),
      (
        EntryPlanAuthorizationConsumption,
        EntryPlanAuthorizationConsumption.consumption_id,
      ),
      (EntryAutomationGate, EntryAutomationGate.account_fingerprint),
      (TradeConfirmationChallenge, TradeConfirmationChallenge.id),
      (
        AccountRiskIncreaseAdmissionBatch,
        AccountRiskIncreaseAdmissionBatch.admission_batch_id,
      ),
      (
        AccountRiskIncreaseAdmissionItem,
        AccountRiskIncreaseAdmissionItem.admission_item_id,
      ),
      (AutoExitPlanRecord, AutoExitPlanRecord.plan_id),
      (AutoExitPlanEvent, AutoExitPlanEvent.event_id),
    ):
      del model
      assert await db.scalar(select(func.count(column))) == 0
