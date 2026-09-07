"""Final review uses an actual gate, allocation, admission and PAPER receipts."""

from dataclasses import asdict, replace

import pytest
from quantx_application.t_trade_v3.entry_execution_gate import (
  EntryExecutionBinding,
  EntryExecutionGateInput,
  EntryExecutionGatePolicy,
  MarketDataCapabilityManifest,
)
from quantx_contracts import ExecutionEnvironment
from quantx_domain.strategies.base import RuntimeStatePatch, SymbolRuntimeStatePatch
from quantx_domain.trading.t_assistant_execution import (
  TAssistantConfigVersion,
  stable_manifest_hash,
)
from quantx_domain.trading.t_assistant_market_state import (
  AcceptedTMarketTick,
  SymbolMarketCursor,
  TAssistantSymbolState,
  TMarketSourceIdentity,
)
from quantx_domain.trading.t_trade_opportunity_engine import (
  OpportunityCandidate,
  OpportunityPath,
  OpportunitySample,
)
from quantx_infrastructure.models.paper_execution import PaperExecutionOrderRecord
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantExecutionRecord,
  TAssistantSymbolStateRecord,
)
from quantx_infrastructure.services.paper_entry_execution_review import (
  PaperEntryExecutionReview,
)
from quantx_infrastructure.services.paper_execution_ledger import PaperExecutionLedger
from quantx_infrastructure.services.paper_receipt_convergence import (
  PaperReceiptConvergence,
)
from sqlalchemy import select

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
from tests.infrastructure.test_paper_receipt_convergence import (
  enrich_intent,
  quote,
)
from tests.infrastructure.test_paper_receipt_convergence import (
  setup as _setup,
)
from tests.infrastructure.test_t_assistant_runtime_repository import NOW

allocation_sessions = _allocation_sessions
base_sessions = _base_sessions
ledger_sessions = _ledger_sessions
sessions = _sessions
frozen_config = _frozen_config


async def setup(*args, **kwargs):
  kwargs.setdefault("allocation_at", NOW)
  kwargs.setdefault("admission_at", NOW)
  return await _setup(*args, **kwargs)


@pytest.fixture
def review_evidence(monkeypatch, frozen_config, request):
  import sys

  from tests.infrastructure import test_paper_execution_ledger as ledger_tests
  from tests.infrastructure import test_t_assistant_runtime_repository as runtime_tests
  from tests.infrastructure import test_t_intent_atomic_intake as intake_tests

  session_time = getattr(request, "param", NOW.replace(hour=1))
  for module in (
    sys.modules[__name__],
    allocation_tests,
    ledger_tests,
    runtime_tests,
    intake_tests,
  ):
    monkeypatch.setattr(module, "NOW", session_time)
  original_version, original_prepare = (
    allocation_tests._version,
    allocation_tests._prepare,
  )

  def version(config_id):
    fields = asdict(original_version(config_id))
    fields.pop("config_snapshot_hash")
    fields["canonical_payload"]["entry_execution_gate_policy"] = {
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
    return TAssistantConfigVersion.create(**fields)

  async def prepare(db, scope):
    execution, repository, kwargs = await original_prepare(db, scope)
    original = TAssistantSymbolState.from_dict(
      kwargs["symbol_patches"][0].patch.set["symbol_state"]
    )
    ms = int(NOW.timestamp() * 1000)
    candidate = OpportunityCandidate(
      "candidate-0",
      "fingerprint-0",
      "episode",
      OpportunityPath.PULLBACK_REBOUND,
      ms,
      ms + 60000,
      ms,
      1,
      9.9,
      90.0,
      execution.policy_version,
      execution.feature_schema_version,
      "profile-v1",
      1,
    )
    opportunity = replace(
      original.opportunity_state, candidate=candidate, candidate_status="LATCHED"
    )
    cursor = SymbolMarketCursor(
      "stream-1", "1", 1, 1, TMarketSourceIdentity("1", ms, 1)
    )
    state = replace(original, opportunity_state=opportunity, cursor=cursor)
    state = replace(
      state,
      material_manifest_hash=stable_manifest_hash(
        {
          "opportunity_state": opportunity.to_dict(),
          "cursor": cursor.to_dict(),
          "lifecycle": state.lifecycle.value,
          "deferred_candidate": None,
          "deferred_candidate_fence_sequence": None,
        }
      ),
    )
    kwargs["symbol_patches"] = (
      SymbolRuntimeStatePatch(
        instrument_code="600000.SH",
        expected_revision=0,
        material=True,
        patch=RuntimeStatePatch(set={"symbol_state": state.to_dict()}),
      ),
    )
    return execution, repository, kwargs

  monkeypatch.setattr(allocation_tests, "_version", version)
  monkeypatch.setattr(allocation_tests, "_prepare", prepare)
  from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
    TTradeOpportunityEvaluation,
  )
  from sqlalchemy import event

  from tests.infrastructure.test_paper_portfolio_snapshot import storage_time

  def availability(_mapper, _connection, target):
    target.created_at = storage_time(type(target), "created_at", NOW)
    if isinstance(target, TAssistantSymbolStateRecord):
      target.updated_at = storage_time(type(target), "updated_at", NOW)

  for model in (TTradeOpportunityEvaluation, TAssistantSymbolStateRecord):
    event.listen(model, "before_insert", availability)
  yield
  for model in (TTradeOpportunityEvaluation, TAssistantSymbolStateRecord):
    event.remove(model, "before_insert", availability)


async def input_for(db, scope, intent_id, *, market=None):
  market = market or quote(1)
  execution = await db.get(TAssistantExecutionRecord, scope)
  row = await db.scalar(
    select(TAssistantSymbolStateRecord).where(
      TAssistantSymbolStateRecord.execution_id == scope
    )
  )
  state = TAssistantSymbolState.from_dict(row.state_payload)
  binding = EntryExecutionBinding(
    "fingerprint-0",
    execution.config_version_id,
    execution.config_snapshot_hash,
    execution.policy_version,
    "entry-gate-v1",
    execution.feature_schema_version,
    "book-v1",
  )
  ms = int(NOW.timestamp() * 1000)
  sample = OpportunitySample(
    instrument_code=market.instrument_code,
    trade_date=market.timestamp.date().isoformat(),
    source_time_ms=int(market.timestamp.timestamp() * 1000),
    tick_ordinal=1,
    price=market.price,
    continuity_generation="1",
    bid_price=market.bid_price[0],
    ask_price=market.ask_price[0],
    bid_volume=market.bid_vol[0],
    ask_volume=market.ask_vol[0],
  )
  capabilities = MarketDataCapabilityManifest(
    "book-v1",
    frozenset({"price", "bid_price", "ask_price", "bid_volume", "ask_volume"}),
  )
  gate = EntryExecutionGateInput(
    state.opportunity_state.candidate,
    market.instrument_code,
    intent_id,
    True,
    True,
    ms,
    ms + 60000,
    ms + 1000,
    ExecutionEnvironment.PAPER,
    binding,
    binding,
    EntryExecutionGatePolicy("entry-gate-v1", 3000, 100, 30),
    capabilities,
    "stream-1",
    "1",
    1,
    1,
    1,
    2,
    AcceptedTMarketTick("stream-1", 2, ms + 1000, sample),
    capabilities.required_fields,
  )
  return gate, market


async def test_real_gate_sizer_risk_and_public_receipt_path(sessions, review_evidence):
  sink = PaperReceiptConvergence()
  scope, args = await setup(sessions, sink, enrich_intent=enrich_intent)
  async with sessions() as db, db.begin():
    gate, market = await input_for(db, scope, args["intent_id"])
    review = PaperEntryExecutionReview(db)
    result = await review.review(
      execution_id=scope,
      intent_id=args["intent_id"],
      gate_input=gate,
      market_data=market,
      now=quote(1).timestamp,
    )
    assert result.outcome == "ACCEPTED", result.reason_codes
    order = await db.get(PaperExecutionOrderRecord, result.order_id)
    assert order.volume == 100 and order.filled_volume == 0
    assert order.sizing_evidence["metadata"]["allocated_amount_cap"] == "1000.00000000"
    repeated = await review.review(
      execution_id=scope,
      intent_id=args["intent_id"],
      gate_input=gate,
      market_data=market,
      now=quote(1).timestamp,
    )
    assert repeated.outcome == "DUPLICATE"
    await PaperExecutionLedger(db, receipt_sink=sink).process_quote(
      execution_id=scope,
      event_key="next-quote",
      quote=quote(2, depth=400),
      accepted_at=quote(2).timestamp,
    )
    assert (
      await db.get(PaperExecutionOrderRecord, result.order_id)
    ).filled_volume == 100


@pytest.mark.parametrize(
  "fault", ["stale", "forged-candidate", "changed-binding", "missing-depth", "live"]
)
async def test_gate_and_binding_failures_never_place_orders(
  sessions, review_evidence, fault
):
  scope, args = await setup(
    sessions, PaperReceiptConvergence(), enrich_intent=enrich_intent
  )
  async with sessions() as db, db.begin():
    gate, market = await input_for(db, scope, args["intent_id"])
    now = quote(1).timestamp
    if fault == "stale":
      now = quote(5).timestamp
      gate = replace(gate, evaluated_at_ms=int(now.timestamp() * 1000))
    elif fault == "forged-candidate":
      gate = replace(gate, candidate=replace(gate.candidate, price=1.0))
    elif fault == "changed-binding":
      gate = replace(
        gate, frozen_binding=replace(gate.frozen_binding, config_snapshot_hash="fake")
      )
    elif fault == "live":
      gate = replace(gate, execution_environment=ExecutionEnvironment.LIVE)
    else:
      market = replace(market, bid_vol=[200])
    result = await PaperEntryExecutionReview(db).review(
      execution_id=scope,
      intent_id=args["intent_id"],
      gate_input=gate,
      market_data=market,
      now=now,
    )
    assert result.outcome in {"REJECT", "DELAY"}
    if fault == "stale":
      assert any("STALE" in code for code in result.reason_codes)
    assert list((await db.scalars(select(PaperExecutionOrderRecord))).all()) == []


@pytest.mark.parametrize("constraint", ["cash", "old-inventory", "admission"])
async def test_actual_capacity_and_standard_admission_prevent_submission(
  sessions, review_evidence, constraint
):
  from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord

  from tests.infrastructure.test_paper_execution_ledger import seed_values

  seed = seed_values()
  if constraint == "cash":
    seed["cash"] = 500.0
  elif constraint == "old-inventory":
    seed["positions"]["600000.SH"].long_volume = 50
    seed["positions"]["600000.SH"].available_volume = 50
    seed["positions"]["600000.SH"].market_value = 500.0
    seed["bucket_checkpoint"]["instruments"]["600000.SH"]["swing"].update(
      total_volume=50, available_volume=50, market_value=500.0
    )
  scope, args = await setup(
    sessions, PaperReceiptConvergence(), enrich_intent=enrich_intent, initial_seed=seed
  )
  async with sessions() as db, db.begin():
    if constraint == "admission":
      row = await db.get(TradeIntentRecord, args["intent_id"])
      for field in (
        "admission_batch_id",
        "admission_rank",
        "admission_policy_version",
        "admission_input_fingerprint",
      ):
        setattr(row, field, None)
      await db.flush()
    gate, market = await input_for(db, scope, args["intent_id"])
    result = await PaperEntryExecutionReview(db).review(
      execution_id=scope,
      intent_id=args["intent_id"],
      gate_input=gate,
      market_data=market,
      now=quote(1).timestamp,
    )
    assert result.outcome == "REJECT"
    assert list((await db.scalars(select(PaperExecutionOrderRecord))).all()) == []


async def test_caller_rollback_and_real_sink_failure_have_no_order_facts(
  sessions, review_evidence, monkeypatch
):
  from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord

  scope, args = await setup(
    sessions, PaperReceiptConvergence(), enrich_intent=enrich_intent
  )
  async with sessions() as db:
    with pytest.raises(RuntimeError, match="outer rollback"):
      async with db.begin():
        gate, market = await input_for(db, scope, args["intent_id"])
        assert (
          await PaperEntryExecutionReview(db).review(
            execution_id=scope,
            intent_id=args["intent_id"],
            gate_input=gate,
            market_data=market,
            now=quote(1).timestamp,
          )
        ).outcome == "ACCEPTED"
        raise RuntimeError("outer rollback")
  async with sessions() as db, db.begin():
    assert list((await db.scalars(select(PaperExecutionOrderRecord))).all()) == []
    assert (
      await db.get(TradeIntentRecord, args["intent_id"])
    ).status == "EXECUTION_READY"

    async def failing_sink(self, db, execution_id, result):
      raise RuntimeError("receipt failure")

    monkeypatch.setattr(PaperReceiptConvergence, "__call__", failing_sink)
    gate, market = await input_for(db, scope, args["intent_id"])
    with pytest.raises(RuntimeError, match="receipt failure"):
      await PaperEntryExecutionReview(db).review(
        execution_id=scope,
        intent_id=args["intent_id"],
        gate_input=gate,
        market_data=market,
        now=quote(1).timestamp,
      )
    assert list((await db.scalars(select(PaperExecutionOrderRecord))).all()) == []


async def test_history_recovers_after_stop_expiry_and_new_tick_without_new_events(
  sessions, review_evidence
):
  from quantx_domain.trading.t_assistant_execution import TAssistantExecutionEvent
  from quantx_infrastructure.models.paper_execution import PaperExecutionEventRecord
  from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
  from quantx_infrastructure.repositories.t_assistant_execution_repository import (
    TAssistantExecutionRepository,
  )
  from sqlalchemy import func

  sink = PaperReceiptConvergence()
  scope, args = await setup(sessions, sink, enrich_intent=enrich_intent)
  async with sessions() as db, db.begin():
    gate, market = await input_for(db, scope, args["intent_id"])
    original = await PaperEntryExecutionReview(db).review(
      execution_id=scope,
      intent_id=args["intent_id"],
      gate_input=gate,
      market_data=market,
      now=quote(1).timestamp,
    )
    await PaperExecutionLedger(db, receipt_sink=sink).process_quote(
      execution_id=scope,
      event_key="fill",
      quote=quote(2, depth=400),
      accepted_at=quote(2).timestamp,
    )
    repository = TAssistantExecutionRepository(db)
    current = await repository.get_domain(scope)
    for status in ("DRAINING", "STOPPED"):
      next_state = current.transition(
        status, at=quote(2).timestamp, has_unsettled_buy_work=False
      )
      await repository.save_transition_with_event(
        next_state,
        expected_state_version=current.state_version,
        event=TAssistantExecutionEvent(scope, status, status, quote(2).timestamp, {}),
      )
      current = next_state
    head = await db.get(TTradeGlobalConfig, "config-1")
    head.enabled = False
    head.state_version += 1
  async with sessions() as db, db.begin():
    gate, market = await input_for(db, scope, args["intent_id"], market=quote(120))
    later_ms = int(market.timestamp.timestamp() * 1000)
    gate = replace(
      gate,
      evaluated_at_ms=later_ms,
      latest_tick=replace(gate.latest_tick, received_at_ms=later_ms),
    )
    before = await db.scalar(
      select(func.count()).select_from(PaperExecutionEventRecord)
    )
    restored = await PaperEntryExecutionReview(db).review(
      execution_id=scope,
      intent_id=args["intent_id"],
      gate_input=gate,
      market_data=market,
      now=market.timestamp,
    )
    assert restored.outcome == "DUPLICATE" and restored.order_id == original.order_id
    assert (
      restored.receipt.event_id == original.receipt.event_id
      and restored.receipt.duplicate
    )
    assert restored.follow_up == "NONE"
    for kwargs in (
      {"execution_id": "other-scope"},
      {"order_attempt": 1},
      {"gate_input": replace(gate, intent_id="another-intent")},
    ):
      result = await PaperEntryExecutionReview(db).review(
        **{
          "execution_id": scope,
          "intent_id": args["intent_id"],
          "gate_input": gate,
          "market_data": market,
          "now": market.timestamp,
          **kwargs,
        }
      )
      assert result.outcome == "REJECT"
    assert (
      await db.scalar(select(func.count()).select_from(PaperExecutionEventRecord))
      == before
    )


def test_dispatcher_followup_distinguishes_wait_from_rebuild():
  from quantx_infrastructure.services.paper_entry_execution_review import (
    PaperEntryReviewResult,
  )

  assert (
    PaperEntryReviewResult("DELAY", ("T_ENTRY_QUOTE_STALE",)).follow_up
    == "REBUILD_CANDIDATE"
  )
  assert (
    PaperEntryReviewResult(
      "DELAY", ("PAPER_REVIEW_CANDIDATE_EVIDENCE_ADVANCED",)
    ).follow_up
    == "REBUILD_CANDIDATE"
  )
  assert (
    PaperEntryReviewResult("DELAY", ("PAPER_ADMISSION_PREDECESSOR_PENDING",)).follow_up
    == "WAIT_PREDECESSOR"
  )
  assert (
    PaperEntryReviewResult("REJECT", ("PAPER_INTENT_EXPIRED",)).follow_up
    == "TERMINALIZE_INTENT"
  )


@pytest.mark.parametrize(
  "review_evidence",
  [
    NOW.replace(hour=3, minute=29, second=59),
    NOW.replace(hour=6, minute=59, second=59),
  ],
  indirect=True,
)
async def test_final_review_rejects_lunch_and_close_after_valid_candidate(
  sessions, review_evidence
):
  scope, args = await setup(
    sessions, PaperReceiptConvergence(), enrich_intent=enrich_intent
  )
  async with sessions() as db, db.begin():
    gate, market = await input_for(db, scope, args["intent_id"], market=quote(2))
    evaluated_ms = int(market.timestamp.timestamp() * 1000)
    gate = replace(
      gate,
      evaluated_at_ms=evaluated_ms,
      latest_tick=replace(gate.latest_tick, received_at_ms=evaluated_ms),
    )
    result = await PaperEntryExecutionReview(db).review(
      execution_id=scope,
      intent_id=args["intent_id"],
      gate_input=gate,
      market_data=market,
      now=market.timestamp,
    )
    assert result.outcome == "REJECT" and result.reason_codes == (
      "OUTSIDE_TRADING_HOURS",
    )
    assert list((await db.scalars(select(PaperExecutionOrderRecord))).all()) == []


@pytest.mark.parametrize("future_source", ["symbol", "candidate"])
async def test_final_review_refuses_future_persisted_source_availability(
  sessions, review_evidence, future_source
):
  from datetime import timedelta

  from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
    TTradeOpportunityEvaluation,
  )
  from sqlalchemy import event

  from tests.infrastructure.test_paper_portfolio_snapshot import storage_time

  model = (
    TAssistantSymbolStateRecord
    if future_source == "symbol"
    else TTradeOpportunityEvaluation
  )

  def future_availability(_mapper, _connection, target):
    field = "updated_at" if future_source == "symbol" else "created_at"
    setattr(
      target, field, storage_time(type(target), field, NOW + timedelta(seconds=2))
    )

  event.listen(model, "before_insert", future_availability)
  try:
    scope, args = await setup(
      sessions, PaperReceiptConvergence(), enrich_intent=enrich_intent
    )
  finally:
    event.remove(model, "before_insert", future_availability)
  async with sessions() as db, db.begin():
    gate, market = await input_for(db, scope, args["intent_id"])
    result = await PaperEntryExecutionReview(db).review(
      execution_id=scope,
      intent_id=args["intent_id"],
      gate_input=gate,
      market_data=market,
      now=market.timestamp,
    )
    assert result.outcome == "REJECT" and any(
      "FUTURE" in code for code in result.reason_codes
    )
    assert list((await db.scalars(select(PaperExecutionOrderRecord))).all()) == []
