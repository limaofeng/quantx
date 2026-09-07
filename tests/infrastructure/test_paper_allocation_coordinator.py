"""Authoritative candidate projection and durable allocation, without a broker."""

import os
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from quantx_infrastructure.models.t_allocation import TAllocationBatchRecord
from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
  TTradeOpportunityEvaluation,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_allocation_repository import (
  TAllocationConflict,
  TAllocationRepository,
)
from quantx_infrastructure.services.paper_allocation_coordinator import (
  PaperAllocationCoordinator,
  candidate_from_evaluation,
)
from quantx_infrastructure.services.paper_execution_ledger import PaperExecutionLedger
from quantx_infrastructure.services.paper_receipt_convergence import (
  PaperReceiptConvergence,
)
from sqlalchemy import event, func, select

from tests.infrastructure import test_paper_portfolio_snapshot as portfolio_tests
from tests.infrastructure import test_t_allocation_repository as allocation_tests
from tests.infrastructure.test_paper_execution_ledger import seed_values
from tests.infrastructure.test_paper_portfolio_snapshot import (
  enrich_intent,
  storage_time,
)
from tests.infrastructure.test_t_assistant_runtime_repository import NOW

allocation_sessions = portfolio_tests.allocation_sessions
base_sessions = portfolio_tests.base_sessions
ledger_sessions = portfolio_tests.ledger_sessions
sessions = portfolio_tests.sessions
frozen_config = portfolio_tests.frozen_config


def signal(intent):
  metadata = intent.metadata if hasattr(intent, "metadata") else intent.intent_metadata
  return {
    "candidate_id": metadata["candidate_id"],
    "candidate_fingerprint": metadata["candidate_fingerprint"],
    "instrument_code": intent.instrument_code,
    "source_time_ms": metadata["source_time_ms"],
    "evaluated_at_ms": int(NOW.timestamp() * 1000),
    "tick_ordinal": metadata["tick_ordinal"],
    "policy_version": metadata["policy_version"],
    "feature_schema_version": metadata["feature_schema_version"],
    "opportunity_score": metadata["opportunity_score"],
    "candidate_expires_at_ms": int((NOW + timedelta(seconds=60)).timestamp() * 1000),
    "data_health": "READY",
    "selected_path": "PULLBACK_REBOUND",
    "pullback": {
      "components": [{"name": "PULLBACK_LIQUIDITY", "contribution": 8, "weight": 10}]
    },
    "features": {"price": 9.8, "ask_price": 9.8, "price_tick": 0.01},
  }


def projection_inputs():
  row = SimpleNamespace(
    id="intent",
    allocation_version=2,
    allocation_next_eligible_at=None,
    instrument_code="600000.SH",
    target_amount=2000,
    limit_price_hint=9.8,
    intent_metadata={
      "candidate_id": "candidate",
      "candidate_fingerprint": "fingerprint",
      "intent_created_at": NOW.isoformat(),
      "source_time_ms": int(NOW.timestamp() * 1000),
      "approval_ttl_ms": 30_000,
      "max_price_deviation_bps": 20,
      "tick_ordinal": 1,
      "policy_version": "v1",
      "feature_schema_version": 1,
      "opportunity_score": 90,
    },
  )
  return row, SimpleNamespace(
    payload={"candidate_evidence": {"evaluation": signal(row)}}
  )


def test_projection_uses_original_ttl_rule_rank_and_fee_inclusive_deviation_ceiling():
  row, evidence = projection_inputs()
  value = candidate_from_evaluation(row, evidence, now=NOW)
  assert value.rank_score == Decimal("0.9") and value.liquidity_quality == Decimal(
    "0.8"
  )
  assert value.expires_at == NOW + timedelta(seconds=30)
  assert value.conservative_lot_cost == Decimal("987.00982")
  assert value.minimum_entry_volume == 100 and value.intent_version == 2


@pytest.mark.parametrize(
  "field,value",
  [
    ("opportunity_score", 91),
    ("candidate_fingerprint", "wrong"),
    ("instrument_code", "000001.SZ"),
    ("tick_ordinal", 2),
    ("evaluated_at_ms", int(NOW.timestamp() * 1000) + 1),
  ],
)
def test_projection_refuses_changed_or_future_evidence(field, value):
  row, evidence = projection_inputs()
  evidence.payload["candidate_evidence"]["evaluation"][field] = value
  with pytest.raises(ValueError, match="BINDING_CONFLICT"):
    candidate_from_evaluation(row, evidence, now=NOW)


def test_degraded_evidence_is_not_healthy_and_expiry_is_not_extended_on_retry():
  row, evidence = projection_inputs()
  evidence.payload["candidate_evidence"]["evaluation"]["data_health"] = "DEGRADED"
  first = candidate_from_evaluation(row, evidence, now=NOW)
  later = candidate_from_evaluation(row, evidence, now=NOW + timedelta(seconds=40))
  assert not first.data_healthy and later == first


@pytest.fixture
def committed_evaluation():
  def availability(_mapper, _connection, target):
    target.created_at = storage_time(type(target), "created_at", NOW)

  event.listen(TTradeOpportunityEvaluation, "before_insert", availability)
  yield
  event.remove(TTradeOpportunityEvaluation, "before_insert", availability)


async def seed(sessions):
  def enrich(intent):
    enrich_intent(intent)
    intent.metadata["tick_ordinal"] = 1
    intent.max_price_deviation_bps = 20

  source, _ = await allocation_tests._seed(
    sessions, authorization="AUTO", enrich_intent=enrich
  )
  scope = source.cut.execution_ref.owner_id
  async with sessions() as db, db.begin():
    await PaperExecutionLedger(db, receipt_sink=PaperReceiptConvergence()).initialize(
      execution_id=scope,
      account_id="account-1",
      **seed_values(),
    )
  return scope


async def test_actual_reader_allocation_commit_and_restart_is_idempotent(
  sessions,
  frozen_config,
  committed_evaluation,
):
  scope = await seed(sessions)
  async with sessions() as db, db.begin():
    batch = await PaperAllocationCoordinator(db).allocate_cycle(
      execution_id=scope,
      cycle_id="intake-cycle",
      processing_owner="first",
      now=NOW,
    )
    assert batch.status == "COMMITTED"
    assert batch.intent_manifest[0]["liquidity_quality"] == "1"
    batch_id = batch.allocation_batch_id
  async with sessions() as db, db.begin():
    assert (
      await PaperAllocationCoordinator(db).allocate_cycle(
        execution_id=scope,
        cycle_id="intake-cycle",
        processing_owner="restart",
        now=NOW + timedelta(milliseconds=1),
      )
      is None
    )
    assert (
      await db.scalar(select(func.count()).select_from(TAllocationBatchRecord)) == 1
    )
    assert (await db.get(TAllocationBatchRecord, batch_id)).status == "COMMITTED"


@pytest.mark.parametrize("recover_at", [0.001, 11])
async def test_recovers_prepared_attempt_and_respects_active_lease(
  sessions,
  frozen_config,
  committed_evaluation,
  recover_at,
):
  scope = await seed(sessions)
  async with sessions() as db, db.begin():
    intent = await db.get(TradeIntentRecord, "intent-0")
    evidence = await db.scalar(select(TTradeOpportunityEvaluation))
    candidate = candidate_from_evaluation(intent, evidence, now=NOW)
    snapshot = await portfolio_tests.read(db, scope)
    repository = TAllocationRepository(db)
    batch = await repository.prepare(
      snapshot=snapshot,
      candidates=(candidate,),
      now=NOW,
      expires_at=NOW + timedelta(seconds=30),
    )
    batch_id = batch.allocation_batch_id
    if recover_at == 11:
      await repository.claim(
        allocation_batch_id=batch_id,
        snapshot=snapshot,
        candidates=(candidate,),
        processing_owner="crashed",
        now=NOW,
        lease_seconds=10,
      )
  if recover_at == 11:
    async with sessions() as db, db.begin():
      with pytest.raises(TAllocationConflict, match="LEASE_CONFLICT"):
        await PaperAllocationCoordinator(db).allocate_cycle(
          execution_id=scope,
          cycle_id="intake-cycle",
          processing_owner="too-early",
          now=NOW + timedelta(seconds=1),
        )
  async with sessions() as db, db.begin():
    recovered = await PaperAllocationCoordinator(db).allocate_cycle(
      execution_id=scope,
      cycle_id="intake-cycle",
      processing_owner="restarted",
      now=NOW + timedelta(seconds=recover_at),
    )
    assert recovered.allocation_batch_id == batch_id and recovered.status == "COMMITTED"


async def test_original_expiry_is_committed_without_order_or_refreshed_ttl(
  sessions,
  frozen_config,
  committed_evaluation,
):
  scope = await seed(sessions)
  async with sessions() as db, db.begin():
    # The seed mark is exactly 60 seconds old, within the frozen mark age bound.
    batch = await PaperAllocationCoordinator(db).allocate_cycle(
      execution_id=scope,
      cycle_id="intake-cycle",
      processing_owner="expired",
      now=NOW + timedelta(seconds=60),
    )
    assert batch.status == "COMMITTED"
    assert (await db.get(TradeIntentRecord, "intent-0")).status == "EXPIRED"


@pytest.mark.parametrize("deferred", [False, True])
async def test_real_strategy_candidate_uses_original_liquidity_on_allocation(
  sessions,
  frozen_config,
  deferred,
):
  from tests.infrastructure.test_t_candidate_evidence import seed_candidate_cycle

  source = await seed_candidate_cycle(sessions, deferred=deferred, extra_tick=True)
  async with sessions() as db, db.begin():
    await PaperExecutionLedger(db, receipt_sink=PaperReceiptConvergence()).initialize(
      execution_id=source.execution_id,
      account_id="account-1",
      **seed_values(),
    )
    batch = await PaperAllocationCoordinator(db).allocate_cycle(
      execution_id=source.execution_id,
      cycle_id=source.cycle_id,
      processing_owner="actual-strategy",
      now=source.now,
    )
    assert batch.status == "COMMITTED"
    assert len(batch.intent_manifest) == 1
    original = batch.intent_manifest[0]
    assert original["liquidity_quality"] == "1"
    assert original["observed_at"].startswith(NOW.isoformat().split("+")[0])
    assert (
      await db.get(TradeIntentRecord, source.intent_id)
    ).status == "EXECUTION_READY"


@pytest.mark.skipif(
  os.environ.get("QUANTX_RUN_MIGRATION_GATE") != "true",
  reason="isolated PostgreSQL migration gate",
)
async def test_postgresql_coordinator_uses_actual_evidence_reader_and_constraints(
  frozen_config,
):
  from tests.infrastructure.test_p4_allocation_postgresql import _sessions

  async with _sessions(head="20260907_0054") as isolated:
    await test_real_strategy_candidate_uses_original_liquidity_on_allocation(
      isolated,
      frozen_config,
      True,
    )
