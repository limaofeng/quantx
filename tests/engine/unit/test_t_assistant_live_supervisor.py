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
  source = await paper.reconcile(config=config, universe=UNIVERSE)
  from dataclasses import asdict

  from quantx_domain.trading.t_assistant_execution import (
    TAssistantConfigVersion,
    TAssistantExecutionEvent,
  )
  from quantx_engine.t_assistant_live_admission import prepare_live_canary_execution
  from quantx_infrastructure.repositories.t_assistant_config_repository import (
    TAssistantConfigRepository,
  )
  from quantx_infrastructure.repositories.t_assistant_execution_repository import (
    TAssistantExecutionRepository,
  )

  values = asdict(paper._config_version(config))
  values.pop("config_snapshot_hash")
  values.update(config_version_id="live-version", version=2)
  values["canonical_payload"]["universe_policy"]["allowed_stock_codes"] = ["600000.SH"]
  values["canonical_payload"]["legacy_settings_snapshot"]["entry_authorization"] = (
    "MANUAL_CONFIRM"
  )
  values["canonical_payload"]["portfolio_policy"]["max_total_t_amount"] = "5000"
  version = TAssistantConfigVersion.create(**values)
  async with sessions() as db, db.begin():
    head = await db.get(TTradeGlobalConfig, config.id)
    await TAssistantConfigRepository(db).append_version(version)
    await TAssistantExecutionRepository(db).append_event(
      TAssistantExecutionEvent(
        source,
        "approval",
        "LIVE_CANARY_RELEASE_APPROVED",
        NOW,
        dict(
          account_id=config.account_id,
          config_version_id=version.config_version_id,
          config_snapshot_hash=version.config_snapshot_hash,
          p5_outcome="PASSED",
          p5_evidence_hash="a" * 64,
          actor_id="synthetic-operator",
          allowed_stock_codes=["600000.SH"],
          max_total_t_amount="5000",
          window_start=NOW.isoformat(),
          window_end=(NOW + timedelta(minutes=1)).isoformat(),
        ),
      )
    )
    key = await prepare_live_canary_execution(
      db,
      source_execution_id=source,
      config_version_id=version.config_version_id,
      approval_event_key="approval",
      expected_head_version=head.state_version,
      now=NOW,
    )
    head.settings = {"target_trade_amount": 9999}
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


async def test_canary_binds_only_the_explicitly_approved_instruments(sessions):
  key = await seed(sessions)
  universe = InstrumentUniverseSnapshot.create(
    mode="ACCOUNT_HOLDINGS",
    instruments=["600000.SH", "000001.SZ"],
    metadata={"600000.SH": {"eligible": True}, "000001.SZ": {"eligible": True}},
  )
  supervisor = TAssistantLiveSupervisor(
    quote_hub=FakeWholeQuoteHub(), session_factory=sessions, clock=lambda: NOW
  )
  await supervisor.reconcile(execution_id=key, universe=universe, legacy_active=False)
  assert [entry.instrument_code for entry in supervisor._bindings[key].universe] == [
    "600000.SH"
  ]


@pytest.mark.parametrize("fail_profile", [False, True])
async def test_cold_running_binding_revokes_durable_ready(sessions, monkeypatch, fail_profile):
  key = await seed(sessions)
  async with sessions() as db, db.begin():
    row = await db.get(TAssistantExecutionRecord, key)
    row.status = "RUNNING"
    row.started_at = NOW
    row.entry_readiness = "READY"
    row.entry_readiness_reasons = []
  supervisor = TAssistantLiveSupervisor(
    quote_hub=FakeWholeQuoteHub(), session_factory=sessions, clock=lambda: NOW + timedelta(seconds=1)
  )
  if fail_profile:
    from unittest.mock import AsyncMock
    monkeypatch.setattr(
      "quantx_engine.t_assistant_live_supervisor.TTradeOpportunityRuntimeService.load_reference_profile",
      AsyncMock(side_effect=ValueError("PROFILE_UNAVAILABLE")),
    )
    with pytest.raises(ValueError, match="PROFILE_UNAVAILABLE"):
      await supervisor.reconcile(execution_id=key, universe=UNIVERSE, legacy_active=False)
    assert key not in supervisor._bindings
  else:
    await supervisor.reconcile(execution_id=key, universe=UNIVERSE, legacy_active=False)
  async with sessions() as db:
    row = await db.get(TAssistantExecutionRecord, key)
    assert row.status == "RUNNING"
    assert row.entry_readiness == "DEGRADED"
    assert row.entry_readiness_reasons == ["LIVE_READY_RECOVERY_REQUIRED"]
    assert await db.scalar(select(func.count(TradeCommandOutbox.message_id))) == 0


@pytest.mark.parametrize("change", ["generation", "stream", "not_ready", "reconcile"])
async def test_running_market_identity_change_revokes_ready_without_symbol_tick(sessions, change):
  from quantx_infrastructure.repositories.t_assistant_execution_repository import (
    TAssistantExecutionRepository,
  )

  key = await seed(sessions)
  hub = FakeWholeQuoteHub()
  supervisor = TAssistantLiveSupervisor(quote_hub=hub, session_factory=sessions, clock=lambda: NOW)
  await supervisor.reconcile(execution_id=key, universe=UNIVERSE, legacy_active=False)
  async with sessions() as db, db.begin():
    row = await db.get(TAssistantExecutionRecord, key)
    row.status = "RUNNING"
    row.started_at = NOW
    row.entry_readiness = "READY"
    row.entry_readiness_reasons = []
    row.state_version += 1
    await db.flush()
    supervisor._bindings[key].execution = await TAssistantExecutionRepository(db).get_domain(key)
  supervisor._bindings[key].ready_market_identity = (hub.stream_id, str(hub.generation))
  if change == "not_ready":
    hub.is_ready = False
  elif change == "stream":
    hub.stream_id = "new-stream"
  else:
    hub.generation += 1
  if change == "reconcile":
    await supervisor.reconcile(execution_id=key, universe=UNIVERSE, legacy_active=False)
  else:
    await supervisor._on_quotes({})
  async with sessions() as db:
    row = await db.get(TAssistantExecutionRecord, key)
    assert row.status == "RUNNING" and row.entry_readiness == "DEGRADED"
    assert row.entry_readiness_reasons
    assert await db.scalar(select(func.count(TradeCommandOutbox.message_id))) == 0


async def test_committed_ready_cycle_dispatches_allocation_and_market_failure_revokes(sessions):
  from types import SimpleNamespace
  from unittest.mock import AsyncMock

  from quantx_infrastructure.repositories.t_assistant_execution_repository import (
    TAssistantExecutionRepository,
  )

  key = await seed(sessions)
  hub = FakeWholeQuoteHub()
  supervisor = TAssistantLiveSupervisor(quote_hub=hub, session_factory=sessions, clock=lambda: NOW)
  await supervisor.start()
  await supervisor.reconcile(execution_id=key, universe=UNIVERSE, legacy_active=False)
  async with sessions() as db, db.begin():
    row = await db.get(TAssistantExecutionRecord, key)
    row.status, row.started_at = "RUNNING", NOW
    row.entry_readiness, row.entry_readiness_reasons = "READY", []
    row.state_version += 1
    await db.flush()
    supervisor._bindings[key].execution = await TAssistantExecutionRepository(db).get_domain(key)
  supervisor._bindings[key].ready_market_identity = (hub.stream_id, str(hub.generation))
  supervisor._bindings[key].rewarm.clear()
  supervisor.runtime.run_cycle = AsyncMock(return_value=SimpleNamespace(committed=True, cycle_id="cycle"))
  supervisor._try_activate = AsyncMock()
  dispatch = AsyncMock(side_effect=ValueError("LIVE_ALLOCATION_MARKET_CHANGED"))
  supervisor.allocation_runtime.dispatch = dispatch
  with pytest.raises(ValueError, match="MARKET_CHANGED"):
    await hub.emit(1)
  dispatch.assert_awaited_once()
  assert dispatch.await_args.kwargs["execution_id"] == key
  assert key not in supervisor._bindings
  async with sessions() as db:
    row = await db.get(TAssistantExecutionRecord, key)
    assert row.entry_readiness == "DEGRADED"
  await supervisor.stop()
