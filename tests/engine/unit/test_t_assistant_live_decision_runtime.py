"""LIVE proposals use actual strategy/cycle persistence without PAPER evidence."""

from dataclasses import replace

import pytest
from quantx_contracts import ExecutionEnvironment
from quantx_engine.t_assistant_decision_runtime import (
  TAssistantLiveDecisionRuntime,
  TAssistantPaperShadowRuntime,
)
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantDecisionCycleRecord,
  TAssistantExecutionEventRecord,
)
from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
  TTradeOpportunityEvaluation,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from sqlalchemy import select

from tests.engine.unit.test_t_assistant_paper_shadow_runtime import _execution
from tests.infrastructure.test_t_candidate_evidence import (
  allocation_sessions,
  base_sessions,
  frozen_config,
  ledger_sessions,
  seed_candidate_cycle,
  sessions,
)

_FIXTURES = allocation_sessions, base_sessions, frozen_config, ledger_sessions, sessions


def test_runtime_environment_is_not_selected_by_caller_payload():
  paper = _execution()
  live = replace(paper, environment=ExecutionEnvironment.LIVE)
  with pytest.raises(ValueError, match="LIVE only"):
    TAssistantLiveDecisionRuntime().bind_execution(paper, parameters={})
  with pytest.raises(ValueError, match="PAPER only"):
    TAssistantPaperShadowRuntime().bind_execution(live, parameters={})
  runtime = TAssistantLiveDecisionRuntime()
  runtime.bind_execution(live, parameters={})
  assert (
    runtime._strategies[live.execution_id].context.environment
    is ExecutionEnvironment.LIVE
  )


@pytest.mark.parametrize("deferred", [False, True])
async def test_real_live_candidates_and_recovered_deferred_state(
  sessions, frozen_config, deferred
):
  source = await seed_candidate_cycle(sessions, environment="LIVE", deferred=deferred)
  async with sessions() as db:
    intent = await db.get(TradeIntentRecord, source.intent_id)
    assert intent.environment == "LIVE"
    assert (
      intent.owner_type == "T_ASSISTANT_EXECUTION"
      and intent.owner_id == source.execution_id
    )
    assert intent.status == "ALLOCATION_PENDING"
    cycle = await db.get(TAssistantDecisionCycleRecord, source.cycle_id)
    assert cycle.status == "PROPOSALS_COMMITTED"
    evidence = list((await db.scalars(select(TTradeOpportunityEvaluation))).all())
    assert evidence
    assert all(
      row.payload["environment"] == "LIVE" and row.payload["paper_shadow_only"] is False
      for row in evidence
    )
    events = list((await db.scalars(select(TAssistantExecutionEventRecord))).all())
    assert not any(row.event_type == "PAPER_SHADOW_RULE_COMPARISON" for row in events)


@pytest.mark.parametrize("change", ["disabled", "paper", "legacy", "version"])
async def test_head_change_after_prepare_cannot_commit_live_intents(
  sessions, frozen_config, monkeypatch, change
):
  from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
  from quantx_infrastructure.repositories.t_assistant_decision_cycle_repository import (
    TAssistantCycleConflict,
  )

  original = TAssistantLiveDecisionRuntime._lock_live_source
  calls = 0

  async def switch(self, db, execution, **kwargs):
    nonlocal calls
    calls += 1
    if calls == 2:
      async with sessions() as writer, writer.begin():
        head = await writer.get(TTradeGlobalConfig, execution.config_id)
        if change == "disabled":
          head.enabled = False
        elif change == "paper":
          head.desired_environment = "PAPER"
        elif change == "legacy":
          head.strategy_run_id = "legacy-run"
        else:
          head.active_config_version_id = None
    return await original(self, db, execution, **kwargs)

  monkeypatch.setattr(TAssistantLiveDecisionRuntime, "_lock_live_source", switch)
  with pytest.raises(TAssistantCycleConflict, match="LIVE_SOURCE_CHANGED"):
    await seed_candidate_cycle(sessions, environment="LIVE")
  async with sessions() as db:
    assert not list((await db.scalars(select(TradeIntentRecord))).all())
    assert not list((await db.scalars(select(TTradeOpportunityEvaluation))).all())
    cycle = await db.scalar(select(TAssistantDecisionCycleRecord))
    assert cycle.status == "PREPARED" and not cycle.output_manifest


async def test_live_recovery_rejects_paper_before_reading_or_aborting_a_cycle():
  with pytest.raises(ValueError, match="LIVE only"):
    await TAssistantLiveDecisionRuntime().recover_cycle(
      execution=_execution(), snapshot=None, cycle_id="paper-cycle"
    )


async def test_canary_internal_snapshot_cannot_expand_the_frozen_universe(
  sessions, frozen_config, monkeypatch
):
  from types import SimpleNamespace

  original = TAssistantLiveDecisionRuntime._lock_live_source

  async def expand(self, db, execution, *, snapshot=None):
    if snapshot is not None:
      snapshot = SimpleNamespace(
        symbols=[*snapshot.symbols, SimpleNamespace(instrument_code="000001.SZ")]
      )
    return await original(self, db, execution, snapshot=snapshot)

  monkeypatch.setattr(TAssistantLiveDecisionRuntime, "_lock_live_source", expand)
  from quantx_infrastructure.repositories.t_assistant_decision_cycle_repository import (
    TAssistantCycleConflict,
  )

  with pytest.raises(TAssistantCycleConflict, match="CANARY_SCOPE_CONFLICT"):
    await seed_candidate_cycle(sessions, environment="LIVE")
  async with sessions() as db:
    assert not list((await db.scalars(select(TradeIntentRecord))).all())
