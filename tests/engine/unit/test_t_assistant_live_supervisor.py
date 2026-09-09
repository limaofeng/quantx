from datetime import timedelta

import pytest
from quantx_domain.trading.t_trade_opportunity_engine import OpportunityPolicy
from quantx_engine.instrument_universe_provider import InstrumentUniverseSnapshot
from quantx_engine.t_assistant_live_supervisor import TAssistantLiveSupervisor
from quantx_engine.t_assistant_paper_shadow_supervisor import (
  TAssistantPaperShadowSupervisor,
)
from quantx_infrastructure.models.agent_runtime import TradeCommandOutbox
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantDecisionCycleRecord,
  TAssistantExecutionRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.repositories.t_assistant_decision_cycle_repository import (
  TAssistantCycleConflict,
)
from sqlalchemy import func, select

from tests.engine.unit.test_t_assistant_paper_shadow_runtime import (
  NOW,
  FakeWholeQuoteHub,
  sessions,
)

_FIXTURES = sessions
UNIVERSE = InstrumentUniverseSnapshot.create(
  mode="ACCOUNT_HOLDINGS",
  instruments=("600000.SH",),
  metadata={"600000.SH": {"eligible": True}},
)


async def seed(sessions):
  config = TTradeGlobalConfig(
    id="live-config",
    account_id="live-account",
    enabled=True,
    mode="paper",
    settings={
      "signal_policy": OpportunityPolicy().to_dict(),
      "target_trade_amount": 1234,
    },
    ignored_stock_codes=[],
    config_version=1,
    desired_environment="PAPER",
    state_version=1,
    universe_revision=1,
  )
  async with sessions() as db, db.begin():
    db.add(config)
  paper = TAssistantPaperShadowSupervisor(
    quote_hub=FakeWholeQuoteHub(), session_factory=sessions, clock=lambda: NOW
  )
  key = await paper.reconcile(config=config, universe=UNIVERSE)
  async with sessions() as db, db.begin():
    head = await db.get(TTradeGlobalConfig, config.id)
    head.desired_environment = "LIVE"
    head.mode = "live"
    head.settings = {"target_trade_amount": 9999}
    row = await db.get(TAssistantExecutionRecord, key)
    row.environment = "LIVE"
    row.entry_readiness = "WARMING"
    row.entry_readiness_reasons = ["T_ASSISTANT_MARKET_WARMING"]
  return key


async def test_current_source_restores_and_reconciles_without_losing_hot_ring(sessions):
  key = await seed(sessions)
  hub = FakeWholeQuoteHub()
  supervisor = TAssistantLiveSupervisor(
    quote_hub=hub, session_factory=sessions, clock=lambda: NOW + timedelta(seconds=1)
  )
  await supervisor.start()
  try:
    await supervisor.reconcile(execution_id=key, universe=UNIVERSE, legacy_active=False)
    assert supervisor.runtime._parameters[key]["target_trade_amount"] == 1234
    assert supervisor.runtime._parameters[key]["mode"] == "live"
    await hub.emit(1)
    binding = supervisor._bindings[key]
    sequence = binding.sequences["600000.SH"]
    assert sequence == 1
    await supervisor.reconcile(execution_id=key, universe=UNIVERSE, legacy_active=False)
    assert supervisor._bindings[key].builder is binding.builder
    assert supervisor._bindings[key].sequences["600000.SH"] == sequence
    async with sessions() as db:
      assert (
        await db.scalar(select(func.count(TAssistantDecisionCycleRecord.cycle_id))) > 0
      )
      assert await db.scalar(select(func.count(TradeCommandOutbox.message_id))) == 0
  finally:
    await supervisor.stop()
  assert not supervisor._bindings and hub.unsubscribed


async def test_failed_refresh_revokes_previous_binding(sessions):
  key = await seed(sessions)
  supervisor = TAssistantLiveSupervisor(
    quote_hub=FakeWholeQuoteHub(), session_factory=sessions, clock=lambda: NOW
  )
  await supervisor.reconcile(execution_id=key, universe=UNIVERSE, legacy_active=False)
  async with sessions() as db, db.begin():
    head = await db.get(TTradeGlobalConfig, "live-config")
    head.enabled = False
  with pytest.raises(TAssistantCycleConflict, match="LIVE_SOURCE_CHANGED"):
    await supervisor.reconcile(execution_id=key, universe=UNIVERSE, legacy_active=False)
  assert key not in supervisor._bindings


async def test_legacy_producer_prevents_live_binding(sessions):
  key = await seed(sessions)
  supervisor = TAssistantLiveSupervisor(
    quote_hub=FakeWholeQuoteHub(), session_factory=sessions, clock=lambda: NOW
  )
  with pytest.raises(ValueError, match="LEGACY_PRODUCER_ACTIVE"):
    await supervisor.reconcile(execution_id=key, universe=UNIVERSE, legacy_active=True)
  assert not supervisor._bindings


async def test_restart_forces_rewarm_and_does_not_resume_old_ring(sessions):
  key = await seed(sessions)
  supervisor = TAssistantLiveSupervisor(
    quote_hub=FakeWholeQuoteHub(), session_factory=sessions, clock=lambda: NOW
  )
  await supervisor.reconcile(execution_id=key, universe=UNIVERSE, legacy_active=False)
  old = supervisor._bindings[key].builder
  await supervisor.stop()
  await supervisor.reconcile(execution_id=key, universe=UNIVERSE, legacy_active=False)
  assert supervisor._bindings[key].builder is not old
  assert supervisor._bindings[key].rewarm == {"600000.SH"}


async def test_disabled_owned_config_drains_without_releasing_lineage(sessions):
  key = await seed(sessions)
  supervisor = TAssistantLiveSupervisor(
    quote_hub=FakeWholeQuoteHub(), session_factory=sessions, clock=lambda: NOW
  )
  assert await supervisor.owns_config("live-config")
  await supervisor.reconcile(execution_id=key, universe=UNIVERSE, legacy_active=False)
  async with sessions() as db, db.begin():
    head = await db.get(TTradeGlobalConfig, "live-config")
    head.enabled = False
  assert (
    await supervisor.reconcile_config(
      config=head, universe=UNIVERSE, legacy_active=False
    )
    is None
  )
  assert not supervisor._bindings
  assert key not in supervisor.runtime._strategies
  async with sessions() as db:
    source = await db.get(TAssistantExecutionRecord, key)
    assert source.status == "DRAINING"
  assert await supervisor.owns_config("live-config")


async def test_stale_config_observation_cannot_drain_a_newer_head(sessions):
  key = await seed(sessions)
  supervisor = TAssistantLiveSupervisor(
    quote_hub=FakeWholeQuoteHub(), session_factory=sessions, clock=lambda: NOW
  )
  async with sessions() as db:
    old = await db.get(TTradeGlobalConfig, "live-config")
  async with sessions() as db, db.begin():
    head = await db.get(TTradeGlobalConfig, "live-config")
    head.state_version += 1
  with pytest.raises(ValueError, match="CONFIG_REFRESH_REQUIRED"):
    await supervisor.reconcile_config(config=old, universe=UNIVERSE, legacy_active=True)
  async with sessions() as db:
    source = await db.get(TAssistantExecutionRecord, key)
    assert source.status == "WARMING"
