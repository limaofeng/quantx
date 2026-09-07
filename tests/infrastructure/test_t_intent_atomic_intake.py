"""Standard intent intake shares the cycle transaction and preserves exact identity."""

from dataclasses import replace
from datetime import timedelta

import pytest
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef, ExecutionOwnerType
from quantx_domain.strategies.base import (
  RuntimeStatePatch,
  SymbolRuntimeStatePatch,
  TAssistantExecutionIntentOrigin,
  TradeIntent,
  TradeIntentDirection,
)
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantDecisionCycleRecord,
  TAssistantSymbolStateRecord,
)
from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
  TTradeOpportunityEvaluation,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_assistant_decision_cycle_repository import (
  TAssistantCycleConflict,
  TAssistantDecisionCycleRepository,
)
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from quantx_infrastructure.repositories.trade_intent_repository import (
  TradeIntentRepository,
)
from quantx_infrastructure.services.trade_intent_intake import (
  trade_intent_initial_material,
  trade_intent_material_from_payload,
  trade_intent_record_data,
)
from sqlalchemy import func, select

from tests.infrastructure.test_t_assistant_runtime_repository import (
  NOW,
  _seed_execution,
  _snapshot,
)
from tests.infrastructure.test_t_assistant_runtime_repository import (
  sessions as _sessions_fixture,
)

sessions = _sessions_fixture


async def test_candidate_source_retry_rejects_changed_witness(sessions):
  from copy import deepcopy

  _, scope = await _seed_execution(sessions)
  async with sessions() as db, db.begin():
    execution, repository, kwargs = await _prepare(db, scope)
    await repository.commit_material_cycle(
      **kwargs, trade_intents=(_intent(execution),)
    )
    changed = dict(kwargs)
    changed["opportunity_evidence"] = deepcopy(kwargs["opportunity_evidence"])
    changed["opportunity_evidence"][0]["payload"]["candidate_evidence"]["evaluation"][
      "features"
    ]["price"] = 1.0
    with pytest.raises(
      TAssistantCycleConflict, match="T_INTENT_CANDIDATE_EVIDENCE_CONFLICT"
    ):
      await repository.commit_material_cycle(
        **changed, trade_intents=(_intent(execution),)
      )


@pytest.mark.asyncio
async def test_initial_intake_material_survives_float_database_round_trip(sessions):
  _, execution_id = await _seed_execution(sessions)
  async with sessions() as db:
    async with db.begin():
      execution, repository, kwargs = await _prepare(db, execution_id)
      await repository.commit_material_cycle(
        **kwargs, trade_intents=(_intent(execution),)
      )
      record = await db.get(TradeIntentRecord, "intent-1")
      await db.refresh(record)
      expected = trade_intent_record_data(
        _intent(execution),
        status="ALLOCATION_PENDING",
        environment=ExecutionEnvironment.PAPER,
      )
      expected.update(
        account_id=execution.account_id,
        allocation_cycle_id="intake-cycle",
        allocation_version=0,
      )
      assert type(expected["target_amount"]) is float
      assert trade_intent_initial_material(
        record
      ) == trade_intent_material_from_payload(expected)


def _intent(execution, *, intent_id="intent-1", cycle_id="intake-cycle"):
  return TradeIntent(
    intent_id=intent_id,
    strategy_id="t-assistant",
    instrument_code="600000.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="T_ENTRY",
    target_amount=1000,
    approval_ttl_ms=60_000,
    created_at=NOW,
    execution_ref=execution.execution_ref,
    origin=TAssistantExecutionIntentOrigin(
      execution_id=execution.execution_id,
      producer_id="t-assistant",
      candidate_id="candidate",
      opportunity_id="candidate",
      cycle_id=cycle_id,
    ),
    metadata={
      "source_execution_ref": execution.execution_ref.to_dict(),
      "candidate_id": "candidate",
      "candidate_fingerprint": "fingerprint",
      "source_time_ms": int(NOW.timestamp() * 1000),
      "tick_ordinal": 1,
      "opportunity_score": 90.0,
      "policy_version": execution.policy_version,
      "feature_schema_version": execution.feature_schema_version,
      "t_trade_role": "entry",
    },
  )


def candidate_evidence_row(execution, intent):
  """Complete typed witness for repository-boundary fixtures, not a strategy test."""
  from quantx_domain.trading.t_assistant_market_state import (
    AcceptedTMarketTick,
    SymbolMarketCursor,
    candidate_evidence_key,
  )
  from quantx_domain.trading.t_trade_opportunity_engine import (
    OpportunityCandidate,
    OpportunityPath,
    OpportunitySample,
  )

  metadata = intent.metadata
  source = metadata["source_time_ms"]
  price = float(intent.limit_price_hint or 9.9)
  candidate = OpportunityCandidate(
    metadata["candidate_id"],
    metadata["candidate_fingerprint"],
    "episode",
    OpportunityPath.PULLBACK_REBOUND,
    source,
    source + int(intent.approval_ttl_ms),
    source,
    metadata["tick_ordinal"],
    price,
    float(metadata["opportunity_score"]),
    execution.policy_version,
    execution.feature_schema_version,
    "profile-v1",
    1,
  )
  tick = AcceptedTMarketTick(
    "stream-1",
    1,
    source,
    OpportunitySample(
      intent.instrument_code,
      "2026-09-03",
      source,
      metadata["tick_ordinal"],
      price,
      continuity_generation="1",
      bid_price=price - 0.01,
      ask_price=price,
      bid_volume=1000,
      ask_volume=1000,
    ),
  )
  evaluation = {
    "instrument_code": intent.instrument_code,
    "candidate_id": candidate.candidate_id,
    "candidate_fingerprint": candidate.fingerprint,
    "candidate_expires_at_ms": candidate.expires_at_ms,
    "source_time_ms": source,
    "tick_ordinal": candidate.tick_ordinal,
    "evaluated_at_ms": source,
    "opportunity_score": candidate.score,
    "policy_version": candidate.policy_version,
    "feature_schema_version": candidate.feature_schema_version,
    "selected_path": candidate.path.value,
    "data_health": "READY",
    "features": {"price": price, "ask_price": price, "price_tick": 0.01},
    "pullback": {
      "components": [
        {"name": "PULLBACK_LIQUIDITY", "weight": 10.0, "contribution": 10.0}
      ]
    },
  }
  return {
    "event_key": candidate_evidence_key(execution.execution_id, candidate.fingerprint),
    "instrument_code": intent.instrument_code,
    "candidate_id": candidate.candidate_id,
    "event_type": "T_OPPORTUNITY_CANDIDATE_FROZEN",
    "evaluated_at": NOW,
    "payload": {
      "execution_ref": execution.execution_ref.to_dict(),
      "environment": "PAPER",
      "cycle_id": intent.origin.cycle_id,
      "paper_shadow_only": True,
      "candidate_evidence": {
        "candidate": candidate.to_dict(),
        "evaluation": evaluation,
        "tick": tick.manifest_item(),
        "cursor": SymbolMarketCursor(
          "stream-1", "1", 1, 1, tick.source_identity
        ).to_dict(),
      },
    },
    "metrics": {"opportunity_score": candidate.score},
  }


async def _prepare(db, execution_id):
  execution = await TAssistantExecutionRepository(db).get_domain(execution_id)
  snapshot = _snapshot(execution)
  repository = TAssistantDecisionCycleRepository(db)
  cycle = await repository.prepare_material_cycle(
    snapshot=snapshot,
    cycle_id="intake-cycle",
    now=NOW,
  )
  claim = await repository.claim(
    cycle_id=cycle.cycle_id,
    processing_owner="worker",
    expected_input_manifest_hash=cycle.input_manifest_hash,
    now=NOW,
  )
  state = replace(
    snapshot.symbols[0].state, revision=1, material_manifest_hash="d" * 64
  )
  kwargs = dict(
    claim=claim,
    expected_input_manifest_hash=cycle.input_manifest_hash,
    symbol_patches=(
      SymbolRuntimeStatePatch(
        instrument_code="600000.SH",
        expected_revision=0,
        material=True,
        patch=RuntimeStatePatch(set={"symbol_state": state.to_dict()}),
      ),
    ),
    opportunity_evidence=(candidate_evidence_row(execution, _intent(execution)),),
    execution_events=(),
    now=NOW,
  )
  return execution, repository, kwargs


async def test_cycle_intake_exact_retry_and_changed_retry(sessions):
  _, execution_id = await _seed_execution(sessions)
  async with sessions() as db:
    async with db.begin():
      execution, repository, kwargs = await _prepare(db, execution_id)
      intent = _intent(execution)
      cycle = await repository.commit_material_cycle(**kwargs, trade_intents=(intent,))
      assert (
        cycle.output_manifest["accepted_intents"][0]["intent_id"] == intent.intent_id
      )
      accepted = await db.get(TradeIntentRecord, intent.intent_id)
      assert accepted.status == "ALLOCATION_PENDING"
      assert accepted.allocation_cycle_id == cycle.cycle_id
      assert accepted.allocation_version == 0
      assert accepted.strategy_run_id is None
      assert accepted.account_id == execution.account_id
      assert (
        await repository.commit_material_cycle(**kwargs, trade_intents=(intent,))
        is cycle
      )
      with pytest.raises(TAssistantCycleConflict, match="IDEMPOTENCY_CONFLICT"):
        await repository.commit_material_cycle(
          **kwargs,
          trade_intents=(replace(intent, target_amount=2000),),
        )
    assert await db.scalar(select(func.count(TradeIntentRecord.id))) == 1


async def test_caller_outer_rollback_undoes_successful_standard_intake(sessions):
  _, execution_id = await _seed_execution(sessions)
  async with sessions() as db:
    with pytest.raises(RuntimeError, match="outer failure"):
      async with db.begin():
        execution, repository, kwargs = await _prepare(db, execution_id)
        await repository.commit_material_cycle(
          **kwargs, trade_intents=(_intent(execution),)
        )
        assert await db.scalar(select(func.count(TradeIntentRecord.id))) == 1
        raise RuntimeError("outer failure")
  async with sessions() as db:
    assert await db.scalar(select(func.count(TradeIntentRecord.id))) == 0
    assert (
      await db.scalar(select(func.count(TAssistantDecisionCycleRecord.cycle_id))) == 0
    )


async def test_final_cycle_failure_rolls_back_intents_and_all_material(
  sessions, monkeypatch
):
  _, execution_id = await _seed_execution(sessions)
  async with sessions() as db:
    async with db.begin():
      execution, repository, kwargs = await _prepare(db, execution_id)
    async with db.begin():
      original = repository._executions.append_event

      async def late_failure(event):
        await original(event)
        if event.event_type == "DECISION_CYCLE_PROPOSALS_COMMITTED":
          assert await db.scalar(select(func.count(TradeIntentRecord.id))) == 2
          raise RuntimeError("late failure after intake flush")

      monkeypatch.setattr(repository._executions, "append_event", late_failure)
      with pytest.raises(RuntimeError, match="after intake"):
        await repository.commit_material_cycle(
          **kwargs,
          trade_intents=(_intent(execution), _intent(execution, intent_id="intent-2")),
        )
  async with sessions() as db:
    for column in (
      TradeIntentRecord.id,
      TAssistantSymbolStateRecord.state_id,
      TTradeOpportunityEvaluation.id,
    ):
      assert await db.scalar(select(func.count(column))) == 0
    assert (
      await db.get(TAssistantDecisionCycleRecord, "intake-cycle")
    ).output_manifest is None


@pytest.mark.parametrize(
  "invalid", ["owner", "environment", "sell", "candidate", "symbol", "origin_cycle"]
)
async def test_cycle_rejects_invalid_standard_intent_scope(sessions, invalid):
  _, execution_id = await _seed_execution(sessions)
  async with sessions() as db:
    async with db.begin():
      execution, repository, kwargs = await _prepare(db, execution_id)
      intent = _intent(execution)
      if invalid == "owner":
        intent.execution_ref = ExecutionOwnerRef(
          ExecutionOwnerType.T_ASSISTANT_EXECUTION, "other"
        )
      elif invalid == "environment":
        intent.metadata["environment"] = "LIVE"
      elif invalid == "sell":
        intent.direction = TradeIntentDirection.SELL
      elif invalid == "candidate":
        intent.metadata["candidate_fingerprint"] = "other"
      elif invalid == "symbol":
        intent.instrument_code = "000001.SZ"
      else:
        intent.origin = replace(intent.origin, cycle_id="other")
      with pytest.raises(TAssistantCycleConflict):
        await repository.commit_material_cycle(**kwargs, trade_intents=(intent,))
    assert await db.scalar(select(func.count(TradeIntentRecord.id))) == 0
    assert (
      await db.scalar(select(func.count(TAssistantSymbolStateRecord.state_id))) == 0
    )


async def test_session_batch_retries_conflicts_and_preserves_lifecycle(sessions):
  _, execution_id = await _seed_execution(sessions)
  async with sessions() as db:
    async with db.begin():
      execution, _, _ = await _prepare(db, execution_id)
      payload = trade_intent_record_data(
        _intent(execution),
        status="ALLOCATION_PENDING",
        environment=ExecutionEnvironment.PAPER,
      )
      payload.update(
        account_id=execution.account_id,
        allocation_cycle_id="intake-cycle",
        allocation_version=0,
      )
      repository = TradeIntentRepository(db)
      first = await repository.accept_intents_idempotent([payload])
      assert await repository.accept_intents_idempotent([payload]) == first
      other = {**payload, "id": "intent-2", "idempotency_key": "intent-2"}
      with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        await repository.accept_intents_idempotent(
          [other, {**payload, "target_amount": 2}]
        )
      assert await repository.find_by_id("intent-2") is None
      first[0].status = "REJECTED"
      await db.flush()
      with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        await repository.accept_intents_idempotent([payload])
      assert first[0].status == "REJECTED"


async def test_late_batch_constraint_failure_leaves_no_partial_records(sessions):
  _, execution_id = await _seed_execution(sessions)
  async with sessions() as db:
    async with db.begin():
      execution, _, _ = await _prepare(db, execution_id)
      payload = trade_intent_record_data(
        _intent(execution),
        status="ALLOCATION_PENDING",
        environment=ExecutionEnvironment.PAPER,
      )
      payload.update(
        account_id=execution.account_id,
        allocation_cycle_id="intake-cycle",
        allocation_version=0,
      )
      # Distinct primary keys collide on the authoritative owner idempotency key.
      from sqlalchemy.exc import IntegrityError

      with pytest.raises(IntegrityError):
        await TradeIntentRepository(db).accept_intents_idempotent(
          [payload, {**payload, "id": "intent-2"}]
        )
      assert await db.scalar(select(func.count(TradeIntentRecord.id))) == 0


def test_shared_serializer_preserves_identity_time_and_execution_controls():
  from types import SimpleNamespace

  execution = SimpleNamespace(
    execution_id="execution",
    execution_ref=ExecutionOwnerRef(
      ExecutionOwnerType.T_ASSISTANT_EXECUTION, "execution"
    ),
    policy_version="policy",
    feature_schema_version=1,
  )
  intent = _intent(execution)
  intent.approval_ttl_ms = 1000
  intent.max_price_deviation_bps = 20
  intent.metadata.update(owner_id="spoof", environment="LIVE")
  payload = trade_intent_record_data(
    intent, status="ALLOCATION_PENDING", environment=ExecutionEnvironment.PAPER
  )
  assert payload["owner_id"] == "execution"
  assert payload["environment"] == "PAPER"
  assert "owner_id" not in payload["metadata"]
  assert "environment" not in payload["metadata"]
  assert payload["metadata"]["approval_ttl_ms"] == 1000
  assert payload["metadata"]["max_price_deviation_bps"] == 20
  assert payload["metadata"]["intent_created_at"] == NOW.isoformat()
  later = trade_intent_record_data(
    replace(intent, created_at=NOW + timedelta(seconds=1)),
    status="ALLOCATION_PENDING",
    environment=ExecutionEnvironment.PAPER,
  )
  assert (
    later["metadata"]["intent_created_at"] != payload["metadata"]["intent_created_at"]
  )
