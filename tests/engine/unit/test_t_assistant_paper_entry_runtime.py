"""Real multi-symbol P3 -> allocation -> admission -> final review dispatch."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from quantx_domain.brokers.base import Position
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_engine.t_assistant_paper_entry_runtime import (
  PaperEntryMarketWitness,
  TAssistantPaperEntryRuntime,
)
from quantx_infrastructure.models.paper_execution import (
  PaperExecutionAccountRecord,
  PaperExecutionEventRecord,
  PaperExecutionOrderRecord,
)
from quantx_infrastructure.models.t_allocation import TAllocationDecisionRecord
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantExecutionEventRecord,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.paper_entry_execution_review import (
  PaperEntryReviewResult,
)
from quantx_infrastructure.services.paper_execution_ledger import PaperExecutionLedger
from quantx_infrastructure.services.paper_receipt_convergence import (
  PaperReceiptConvergence,
)
from sqlalchemy import event, select

from tests.infrastructure.test_paper_execution_ledger import seed_values
from tests.infrastructure.test_t_candidate_evidence import (
  allocation_sessions,
  base_sessions,
  frozen_config,
  ledger_sessions,
  seed_candidate_cycle,
  sessions,
)

_FIXTURES = (
  allocation_sessions,
  base_sessions,
  frozen_config,
  ledger_sessions,
  sessions,
)
CODES = ("600000.SH", "000001.SZ")
AT = datetime(2026, 9, 3, 1, 30, tzinfo=UTC)


async def seeded(sessions, *, initialize=True):
  source = await seed_candidate_cycle(sessions, candidate_at=AT, instrument_codes=CODES)
  witnesses = {}
  for code, tick in source.latest_ticks:
    sample = tick.sample
    market = MarketDataSnapshot(
      instrument_code=code,
      timestamp=datetime.fromtimestamp(sample.source_time_ms / 1000, UTC),
      price=sample.price,
      source="accepted-tick",
      limit_up=110.0,
      limit_down=90.0,
      bid_price=[sample.bid_price - index * 0.01 for index in range(5)],
      ask_price=[sample.ask_price + index * 0.01 for index in range(5)],
      bid_vol=[1000] * 5,
      ask_vol=[1000] * 5,
    )
    witnesses[code] = PaperEntryMarketWitness(tick, 1, tick.accepted_sequence, market)
  if initialize:
    async with sessions() as db, db.begin():
      ledger = PaperExecutionLedger(db, receipt_sink=PaperReceiptConvergence())
      seed = seed_values()
      seed["seed_as_of"] = AT
      seed["positions"][CODES[1]] = Position(
        CODES[1],
        long_volume=1000,
        available_volume=1000,
        long_avg_price=10.0,
        last_price=10.0,
        market_value=10000.0,
      )
      seed["bucket_checkpoint"]["instruments"][CODES[1]] = deepcopy(
        seed["bucket_checkpoint"]["instruments"][CODES[0]]
      )
      await ledger.initialize(
        execution_id=source.execution_id, account_id="account-1", **seed
      )
      for code, witness in witnesses.items():
        await ledger.process_quote(
          execution_id=source.execution_id,
          event_key=f"quote:{code}",
          quote=witness.market_data,
          accepted_at=source.now,
        )
  return source, witnesses


async def test_actual_two_symbol_ranked_dispatch_and_retry(sessions, frozen_config):
  source, witnesses = await seeded(sessions)
  statements = []

  def observe(_connection, _cursor, statement, _parameters, _context, _many):
    statements.append(statement.lower())

  engine = sessions.kw["bind"].sync_engine
  event.listen(engine, "before_cursor_execute", observe)
  seen = []

  async def provider(code):
    seen.append(code)
    return witnesses[code]

  runtime = TAssistantPaperEntryRuntime(
    session_factory=sessions, clock=lambda: source.now
  )
  try:
    result = await runtime.dispatch(
      execution_id=source.execution_id, market_witness_provider=provider
    )
  finally:
    event.remove(engine, "before_cursor_execute", observe)
  for forbidden in (
    "positions",
    "account_execution_controls",
    "pending_trade_orders",
    "agent_command_outbox",
  ):
    assert not any(
      f"from {forbidden}" in sql or f"join {forbidden}" in sql for sql in statements
    )
  assert len(result.order_ids) == 2, result
  assert all(review.outcome == "ACCEPTED" for _, review in result.reviews), result
  async with sessions() as db:
    decisions = list(
      (
        await db.scalars(
          select(TAllocationDecisionRecord).order_by(TAllocationDecisionRecord.rank)
        )
      ).all()
    )
    intents = list(
      (
        await db.scalars(
          select(TradeIntentRecord).order_by(TradeIntentRecord.admission_rank)
        )
      ).all()
    )
    assert [row.instrument_code for row in intents] == seen == list(CODES)
    assert [row.intent_id for row in decisions] == [row.id for row in intents]
    assert all(row.status == "ROUTED" for row in intents)
    events = list(
      (
        await db.scalars(
          select(TAssistantExecutionEventRecord).where(
            TAssistantExecutionEventRecord.event_type == "PAPER_ENTRY_REVIEWED"
          )
        )
      ).all()
    )
    assert len(events) == 2
  repeated = await runtime.dispatch(
    execution_id=source.execution_id, market_witness_provider=provider
  )
  assert repeated.status == "IDLE"
  async with sessions() as db:
    assert len(list((await db.scalars(select(PaperExecutionOrderRecord))).all())) == 2


async def test_missing_witness_revokes_grant_and_audits_rebuild(
  sessions, frozen_config
):
  source, _ = await seeded(sessions)

  async def provider(code):
    return None

  result = await TAssistantPaperEntryRuntime(
    session_factory=sessions, clock=lambda: source.now
  ).dispatch(execution_id=source.execution_id, market_witness_provider=provider)
  assert all(review.follow_up == "REBUILD_CANDIDATE" for _, review in result.reviews)
  async with sessions() as db:
    intents = list((await db.scalars(select(TradeIntentRecord))).all())
    assert all(row.status == "CANCELLED" for row in intents)
    events = list(
      (
        await db.scalars(
          select(TAssistantExecutionEventRecord).where(
            TAssistantExecutionEventRecord.event_type == "PAPER_ENTRY_REBUILD_REQUIRED"
          )
        )
      ).all()
    )
    assert {row.payload["candidate_id"] for row in events} == {
      row.intent_metadata["candidate_id"] for row in intents
    }
    assert not list((await db.scalars(select(PaperExecutionOrderRecord))).all())


async def test_late_sink_failure_rolls_back_whole_dispatch(
  sessions, frozen_config, monkeypatch
):
  source, witnesses = await seeded(sessions)
  original = PaperReceiptConvergence.__call__
  count = 0

  async def fail_second(self, *args, **kwargs):
    nonlocal count
    count += 1
    await original(self, *args, **kwargs)
    if count == 2:
      raise RuntimeError("receipt sink failed")

  monkeypatch.setattr(PaperReceiptConvergence, "__call__", fail_second)

  async def provider(code):
    return witnesses[code]

  async with sessions() as db:
    before = (await db.get(PaperExecutionAccountRecord, source.execution_id)).revision
  with pytest.raises(RuntimeError, match="receipt sink failed"):
    await TAssistantPaperEntryRuntime(
      session_factory=sessions, clock=lambda: source.now
    ).dispatch(execution_id=source.execution_id, market_witness_provider=provider)
  async with sessions() as db:
    assert (
      await db.get(PaperExecutionAccountRecord, source.execution_id)
    ).revision == before
    assert not list((await db.scalars(select(PaperExecutionOrderRecord))).all())
    assert not list((await db.scalars(select(TAllocationDecisionRecord))).all())
    assert all(
      row.status == "ALLOCATION_PENDING"
      for row in (await db.scalars(select(TradeIntentRecord))).all()
    )
    assert not list(
      (
        await db.scalars(
          select(PaperExecutionEventRecord).where(
            PaperExecutionEventRecord.event_type == "ORDER"
          )
        )
      ).all()
    )


async def test_missing_seed_is_explicit_and_never_requests_market(
  sessions, frozen_config
):
  source, _ = await seeded(sessions, initialize=False)

  async def provider(code):
    pytest.fail("must not fetch market before explicit PAPER seed")

  result = await TAssistantPaperEntryRuntime(
    session_factory=sessions, clock=lambda: source.now
  ).dispatch(execution_id=source.execution_id, market_witness_provider=provider)
  assert result.status == "SEED_REQUIRED"


async def test_second_ready_expires_at_its_own_review_clock(sessions, frozen_config):
  source, witnesses = await seeded(sessions)
  clock = {"now": source.now}
  seen = []

  async def provider(code):
    seen.append(code)
    if len(seen) == 2:
      clock["now"] += timedelta(minutes=5)
    return witnesses[code]

  result = await TAssistantPaperEntryRuntime(
    session_factory=sessions, clock=lambda: clock["now"]
  ).dispatch(execution_id=source.execution_id, market_witness_provider=provider)
  assert len(result.order_ids) == 1
  assert result.reviews[1][1].reason_codes == ("PAPER_INTENT_EXPIRED",)
  async with sessions() as db:
    expired = await db.get(TradeIntentRecord, result.reviews[1][0])
    assert expired.status == "EXPIRED"
    audit = await db.scalar(
      select(TAssistantExecutionEventRecord).where(
        TAssistantExecutionEventRecord.event_type == "PAPER_ENTRY_REVIEWED",
        TAssistantExecutionEventRecord.occurred_at == clock["now"],
      )
    )
    assert audit.payload["reason_codes"] == ["PAPER_INTENT_EXPIRED"]


@pytest.mark.parametrize("expire_before_restart", [False, True])
async def test_wait_predecessor_keeps_grant_and_audits(
  sessions, frozen_config, monkeypatch, expire_before_restart
):
  # Only the outcome seam is supplied here; actual ranked acceptance is covered
  # above. This isolates the dispatcher transition required by public review.
  from quantx_infrastructure.services.paper_entry_execution_review import (
    PaperEntryExecutionReview,
  )

  source, witnesses = await seeded(sessions)
  actual_review = PaperEntryExecutionReview.review

  async def waiting(*args, **kwargs):
    return PaperEntryReviewResult("DELAY", ("PAPER_ADMISSION_PREDECESSOR_PENDING",))

  monkeypatch.setattr(PaperEntryExecutionReview, "review", waiting)

  async def provider(code):
    return witnesses[code]

  result = await TAssistantPaperEntryRuntime(
    session_factory=sessions, clock=lambda: source.now
  ).dispatch(execution_id=source.execution_id, market_witness_provider=provider)
  assert all(review.follow_up == "WAIT_PREDECESSOR" for _, review in result.reviews)
  async with sessions() as db:
    assert all(
      row.status == "EXECUTION_READY"
      for row in (await db.scalars(select(TradeIntentRecord))).all()
    )
    assert not list((await db.scalars(select(PaperExecutionOrderRecord))).all())
    events = list(
      (
        await db.scalars(
          select(TAssistantExecutionEventRecord).where(
            TAssistantExecutionEventRecord.event_type == "PAPER_ENTRY_REVIEWED"
          )
        )
      ).all()
    )
    assert len(events) == 2
    assert all(row.payload["follow_up"] == "WAIT_PREDECESSOR" for row in events)
  monkeypatch.setattr(PaperEntryExecutionReview, "review", actual_review)
  if expire_before_restart:

    async def no_market(code):
      pytest.fail("expired READY must terminate before fresh-market requirement")

    resumed = await TAssistantPaperEntryRuntime(
      session_factory=sessions, clock=lambda: source.now + timedelta(minutes=5)
    ).dispatch(execution_id=source.execution_id, market_witness_provider=no_market)
    assert not resumed.order_ids
    assert len(resumed.reviews) == 2
    async with sessions() as db:
      assert all(
        row.status == "EXPIRED"
        for row in (await db.scalars(select(TradeIntentRecord))).all()
      )
    return
  resumed = await TAssistantPaperEntryRuntime(
    session_factory=sessions, clock=lambda: source.now
  ).dispatch(execution_id=source.execution_id, market_witness_provider=provider)
  assert len(resumed.order_ids) == 2, resumed


@pytest.mark.parametrize("claimed", [False, True])
async def test_real_prepared_allocation_restart_and_fenced_lease(
  sessions, frozen_config, claimed
):
  from quantx_infrastructure.models.t_assistant_execution import (
    TAssistantDecisionCycleRecord,
  )
  from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
    TTradeOpportunityEvaluation,
  )
  from quantx_infrastructure.repositories.t_allocation_repository import (
    TAllocationConflict,
    TAllocationRepository,
  )
  from quantx_infrastructure.services.paper_allocation_coordinator import (
    candidate_from_evaluation,
  )
  from quantx_infrastructure.services.paper_portfolio_snapshot import (
    PaperPortfolioSnapshotReader,
  )

  source, witnesses = await seeded(sessions)
  async with sessions() as db, db.begin():
    cycle = await db.get(TAssistantDecisionCycleRecord, source.cycle_id)
    candidates = []
    for reference in cycle.output_manifest["accepted_intents"]:
      intent = await db.get(TradeIntentRecord, reference["intent_id"])
      record = await db.scalar(
        select(TTradeOpportunityEvaluation).where(
          TTradeOpportunityEvaluation.event_key == reference["candidate_evidence_key"]
        )
      )
      candidates.append(candidate_from_evaluation(intent, record, now=source.now))
    snapshot = await PaperPortfolioSnapshotReader(db).read(
      execution_id=source.execution_id,
      cycle_id=source.cycle_id,
      instrument_codes=CODES,
      as_of=source.now,
    )
    repository = TAllocationRepository(db)
    batch = await repository.prepare(
      snapshot=snapshot,
      candidates=tuple(candidates),
      now=source.now,
      expires_at=source.now + timedelta(seconds=10),
    )
    batch_id = batch.allocation_batch_id
    if claimed:
      await repository.claim(
        allocation_batch_id=batch_id,
        snapshot=snapshot,
        candidates=tuple(candidates),
        processing_owner="crashed",
        now=source.now,
        lease_seconds=1,
      )

  async def provider(code):
    return witnesses[code]

  if claimed:
    with pytest.raises(TAllocationConflict, match="LEASE_CONFLICT"):
      await TAssistantPaperEntryRuntime(
        session_factory=sessions, clock=lambda: source.now + timedelta(milliseconds=1)
      ).dispatch(execution_id=source.execution_id, market_witness_provider=provider)
  later = source.now + timedelta(seconds=1.1 if claimed else 0.001)
  result = await TAssistantPaperEntryRuntime(
    session_factory=sessions, clock=lambda: later
  ).dispatch(execution_id=source.execution_id, market_witness_provider=provider)
  assert result.allocation_ids == (batch_id,)
  assert len(result.order_ids) == 2, result
