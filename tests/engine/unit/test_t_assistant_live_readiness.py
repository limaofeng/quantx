from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_contracts import PROTOCOL_VERSION
from quantx_domain.trading.t_assistant_execution import stable_manifest_hash
from quantx_domain.trading.t_assistant_market_state import (
  SymbolMarketCursor,
  TAssistantSymbolState,
  TMarketSourceIdentity,
)
from quantx_domain.trading.t_trade_opportunity_engine import OpportunityState
from quantx_engine import t_assistant_live_readiness as activation
from quantx_engine.t_trade_decision_snapshot import TMarketCapture
from quantx_infrastructure.models.agent_runtime import AccountExecutionControl
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantDecisionCycleRecord,
  TAssistantExecutionRecord,
)
from quantx_infrastructure.repositories.t_assistant_symbol_state_repository import (
  TAssistantSymbolStateRepository,
)

from tests.engine.unit.test_t_assistant_live_admission import (
  NOW,
  prepare,
  seed,
  sessions,
)

_FIXTURES = sessions
CODE = "600000.SH"
AT = NOW + timedelta(seconds=2)


@pytest.fixture
async def prepared(sessions, monkeypatch):
  source = await seed(sessions)
  async with sessions.kw["bind"].begin() as connection:
    await connection.run_sync(
      lambda sync: AccountExecutionControl.__table__.create(sync)
    )
  async with sessions() as db, db.begin():
    key = await prepare(db, source)
    row = await db.get(TAssistantExecutionRecord, key)
    db.add(
      AccountExecutionControl(
        account_id="account-1",
        authorization_state="ENABLED",
        reconcile_status="READY",
        state_version=1,
        last_snapshot_id="cut",
        last_snapshot_hash="a" * 64,
        last_snapshot_at=AT,
      )
    )
    state = TAssistantSymbolState(
      key,
      CODE,
      1,
      "ACTIVE",
      SymbolMarketCursor(
        "stream", "7", 1, 10, TMarketSourceIdentity("7", int(AT.timestamp() * 1000), 10)
      ),
      OpportunityState.initial(),
      row.policy_version,
      1,
      material_manifest_hash="b" * 64,
    )
    await TAssistantSymbolStateRepository(db).apply_material_states(
      (state,), expected_revisions={CODE: 0}
    )
    input_manifest = {
      "stream_id": "stream",
      "continuity_generation": "7",
      "fence_sequence": 10,
      "config_snapshot_hash": row.config_snapshot_hash,
    }
    output_manifest = {"symbol_state_manifest": {CODE: state.material_manifest_hash}}
    db.add(
      TAssistantDecisionCycleRecord(
        cycle_id="cycle",
        execution_id=key,
        cycle_sequence=1,
        decision_key="c" * 64,
        attempt=1,
        snapshot_hash="d" * 64,
        fence_from=0,
        fence_to=10,
        market_delta_manifest_hash="e" * 64,
        reducer_cursor_manifest_hash="e" * 64,
        status="PROPOSALS_COMMITTED",
        input_manifest_hash=stable_manifest_hash(input_manifest),
        input_manifest=input_manifest,
        output_manifest_hash=stable_manifest_hash(output_manifest),
        output_manifest=output_manifest,
        prepared_at=AT,
        committed_at=AT,
        created_at=AT,
      )
    )
  portfolio = SimpleNamespace(
    entry_blockers=("T_ACCOUNT_ENTRY_DISABLED",),
    cut=SimpleNamespace(account_snapshot_id="cut", account_snapshot_hash="a" * 64),
    portfolio_input_fingerprint="f" * 64,
  )
  reader = AsyncMock(return_value=portfolio)
  monkeypatch.setattr(activation.LivePortfolioSnapshotReader, "read", reader)
  health = dict(
    account_id="account-1",
    can_approve=True,
    rollout_enabled=True,
    stage="CANARY",
    protocol_version=PROTOCOL_VERSION,
    ready_live_agent_count=1,
    agent_mode="live",
    agent_device_id="device",
    snapshot_id="cut",
    snapshot_hash="a" * 64,
    account_safety={"state_version": 1},
  )
  return key, health, reader


async def activate(db, prepared, **changes):
  key, health, _ = prepared
  args = dict(
    execution_id=key,
    cycle_id="cycle",
    instrument_codes=[CODE],
    market_capture=TMarketCapture("stream", "7", 10, AT, True, ()),
    market_mark_reader=object(),
    health=health,
    health_observed_at=AT,
    now=AT,
  )
  args.update(changes)
  return await activation.activate_live_canary_ready(db, **args)


async def test_actual_admission_and_warm_cycle_activate_under_same_account_cut(
  sessions, prepared
):
  async with sessions() as db, db.begin():
    execution = await activate(db, prepared)
    assert (
      execution.status.value == "RUNNING"
      and execution.readiness.readiness.value == "READY"
    )
    assert execution.started_at == AT
  prepared[2].assert_awaited_once()
  assert prepared[2].await_args.kwargs["account_max_age_seconds"] == 90


@pytest.mark.parametrize(
  "damage",
  [
    "protocol",
    "two_agents",
    "disabled",
    "snapshot",
    "control",
    "generation",
    "scope",
    "old_health",
  ],
)
async def test_missing_or_changed_evidence_keeps_source_warming(
  sessions, prepared, damage
):
  key, health, _ = prepared
  changes = {}
  if damage == "protocol":
    health["protocol_version"] = "obsolete"
  elif damage == "two_agents":
    health["ready_live_agent_count"] = 2
  elif damage == "disabled":
    health["can_approve"] = False
  elif damage == "snapshot":
    health["snapshot_hash"] = "changed"
  elif damage == "control":
    health["account_safety"] = {"state_version": 2}
  elif damage == "generation":
    changes["market_capture"] = TMarketCapture("stream", "8", 10, AT, True, ())
  elif damage == "scope":
    changes["instrument_codes"] = ["000001.SZ"]
  else:
    changes["health_observed_at"] = AT - timedelta(seconds=90)
  async with sessions() as db, db.begin():
    with pytest.raises(activation.LiveReadinessBlocked):
      await activate(db, prepared, **changes)
    assert (await db.get(TAssistantExecutionRecord, key)).status == "WARMING"


async def test_failed_audit_rolls_back_activation(sessions, prepared, monkeypatch):
  repository = activation.TAssistantExecutionRepository
  original = repository.append_event

  async def fail_ready(self, event):
    if event.event_type == "EXECUTION_ENTRY_READY":
      raise RuntimeError("audit unavailable")
    return await original(self, event)

  monkeypatch.setattr(repository, "append_event", fail_ready)
  async with sessions() as db, db.begin():
    with pytest.raises(RuntimeError, match="audit unavailable"):
      await activate(db, prepared)
    row = await db.get(TAssistantExecutionRecord, prepared[0])
    assert row.status == "WARMING"
    assert row.started_at is None


@pytest.mark.parametrize("damage", ["manifest", "loss", "expired", "current_cut"])
async def test_durable_input_failure_blocks_activation(sessions, prepared, damage):
  async with sessions() as db, db.begin():
    changes = {}
    if damage == "manifest":
      cycle = await db.get(TAssistantDecisionCycleRecord, "cycle")
      cycle.output_manifest = {"symbol_state_manifest": {CODE: "changed"}}
    elif damage == "loss":
      prepared[2].return_value.entry_blockers = ("T_DAILY_LOSS_LIMIT",)
    elif damage == "expired":
      changes["now"] = AT + timedelta(days=1)
      changes["health_observed_at"] = changes["now"]
    else:
      control = await db.get(AccountExecutionControl, "account-1")
      control.last_snapshot_id = "new-cut"
    await db.flush()
    with pytest.raises(activation.LiveReadinessBlocked):
      await activate(db, prepared, **changes)
    assert (await db.get(TAssistantExecutionRecord, prepared[0])).status == "WARMING"


@pytest.mark.parametrize("health_fails", [False, True])
async def test_supervisor_activates_or_persists_blocker(
  sessions, prepared, health_fails
):
  from quantx_engine.t_assistant_live_supervisor import TAssistantLiveSupervisor
  from quantx_engine.t_trade_decision_snapshot import TSymbolUniverseEntry

  key, health, _ = prepared
  provider = AsyncMock(return_value=health)
  if health_fails:
    provider.side_effect = RuntimeError("private backend detail")
  supervisor = TAssistantLiveSupervisor(
    quote_hub=SimpleNamespace(is_ready=True, stream_id="stream", generation=7),
    session_factory=sessions,
    clock=lambda: AT,
    readiness_provider=provider,
  )
  async with sessions() as db:
    execution = await activation.TAssistantExecutionRepository(db).get_domain(key)
    states = await TAssistantSymbolStateRepository(db).load_domains(key)
  binding = SimpleNamespace(
    execution=execution,
    universe=(TSymbolUniverseEntry(CODE),),
    readiness_checked_at=None,
  )
  capture = TMarketCapture("stream", "7", 10, AT, True, ())
  # Cold memory must not query health or activate from a previous warm database row.
  await supervisor._try_activate(binding, "cycle", capture)
  provider.assert_not_awaited()
  supervisor.runtime.bind_execution(execution, parameters={}, symbol_states=states)
  await supervisor._try_activate(binding, "cycle", capture)
  await supervisor._try_activate(binding, "cycle", capture)
  provider.assert_awaited_once()
  async with sessions() as db:
    current = await activation.TAssistantExecutionRepository(db).get_domain(key)
    assert current == binding.execution
    if health_fails:
      assert current.status.value == "WARMING"
      assert current.readiness.reasons == ("LIVE_READY_HEALTH_UNAVAILABLE",)
    else:
      assert current.status.value == "RUNNING"


async def test_supervisor_rolls_back_when_market_changes_during_valuation(
  sessions, prepared
):
  from quantx_engine.t_assistant_live_supervisor import TAssistantLiveSupervisor
  from quantx_engine.t_trade_decision_snapshot import TSymbolUniverseEntry

  key, health, reader = prepared
  hub = SimpleNamespace(is_ready=True, stream_id="stream", generation=7)

  async def changed_market(**kwargs):
    hub.generation = 8
    return reader.return_value

  reader.side_effect = changed_market
  supervisor = TAssistantLiveSupervisor(
    quote_hub=hub,
    session_factory=sessions,
    clock=lambda: AT,
    readiness_provider=AsyncMock(return_value=health),
  )
  async with sessions() as db:
    execution = await activation.TAssistantExecutionRepository(db).get_domain(key)
    states = await TAssistantSymbolStateRepository(db).load_domains(key)
  supervisor.runtime.bind_execution(execution, parameters={}, symbol_states=states)
  binding = SimpleNamespace(
    execution=execution,
    universe=(TSymbolUniverseEntry(CODE),),
    readiness_checked_at=None,
  )
  await supervisor._try_activate(
    binding, "cycle", TMarketCapture("stream", "7", 10, AT, True, ())
  )
  async with sessions() as db:
    current = await activation.TAssistantExecutionRepository(db).get_domain(key)
    assert current.status.value == "WARMING"
    assert current.started_at is None
    assert current.readiness.reasons == ("LIVE_READY_MARKET_CHANGED",)


async def test_running_recovery_rechecks_health_after_original_window(sessions, prepared):
  from quantx_domain.trading.t_assistant_execution import (
    TAssistantEntryReadinessProjection,
  )

  async with sessions() as db, db.begin():
    original = await activate(db, prepared)
    degraded = original.with_readiness(TAssistantEntryReadinessProjection(
      "DEGRADED", ("LIVE_READY_RECOVERY_REQUIRED",), AT
    ))
    # Simulate the durable restart gate without changing execution identity/start time.
    row = await db.get(TAssistantExecutionRecord, original.execution_id)
    row.entry_readiness = "DEGRADED"
    row.entry_readiness_reasons = ["LIVE_READY_RECOVERY_REQUIRED"]
    row.state_version = degraded.state_version
  later = AT + timedelta(minutes=2)
  async with sessions() as db, db.begin():
    cycle = await db.get(TAssistantDecisionCycleRecord, "cycle")
    cycle.committed_at = later
  changes = dict(now=later, health_observed_at=later,
    market_capture=TMarketCapture("stream", "7", 10, later, True, ()))
  async with sessions() as db, db.begin():
    with pytest.raises(activation.LiveReadinessBlocked, match="ACCOUNT_OR_AGENT"):
      await activate(db, prepared, **changes, health={**prepared[1], "agent_mode": "paper"})
  async with sessions() as db, db.begin():
    restored = await activate(db, prepared, **changes)
    assert restored.execution_id == original.execution_id
    assert restored.started_at == original.started_at
    assert restored.readiness.readiness.value == "READY"
