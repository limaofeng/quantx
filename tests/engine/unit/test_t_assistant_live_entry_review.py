"""Real stored Gate construction and Engine freshness checks around a review."""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from quantx_application.t_trade_v3.entry_execution_gate import EntryExecutionGate
from quantx_contracts import ExecutionEnvironment
from quantx_engine import t_assistant_live_entry_review as adapter_module
from quantx_infrastructure.models.agent_runtime import PendingTradeOrder
from quantx_infrastructure.models.t_assistant_execution import TAssistantExecutionRecord
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_allocation_repository import (
  TAllocationRepository,
)
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  _execution_from_record,
)
from quantx_infrastructure.services.live_entry_execution_review import (
  LiveEntryReviewResult,
)

from tests.infrastructure import test_t_allocation_repository as allocation
from tests.infrastructure.test_paper_entry_execution_review import (
  allocation_sessions as _allocation_sessions,
)
from tests.infrastructure.test_paper_entry_execution_review import (
  base_sessions as _base_sessions,
)
from tests.infrastructure.test_paper_entry_execution_review import (
  enrich_intent,
  input_for,
)
from tests.infrastructure.test_paper_entry_execution_review import (
  frozen_config as _frozen_config,
)
from tests.infrastructure.test_paper_entry_execution_review import (
  ledger_sessions as _ledger_sessions,
)
from tests.infrastructure.test_paper_entry_execution_review import (
  review_evidence as _review_evidence,
)
from tests.infrastructure.test_paper_entry_execution_review import (
  sessions as _sessions,
)

allocation_sessions = _allocation_sessions
base_sessions = _base_sessions
frozen_config = _frozen_config
ledger_sessions = _ledger_sessions
sessions = _sessions
review_evidence = _review_evidence


@pytest.mark.parametrize("path", ["initial", "replacement"])
@pytest.mark.parametrize("fault", [None, "missing", "generation", "expired", "clock"])
async def test_engine_review_uses_live_gate_and_checks_witness_after_await(
  sessions, review_evidence, monkeypatch, fault, path
):
  at = allocation.NOW
  snapshot, candidates = await allocation._seed(
    sessions, environment=ExecutionEnvironment.LIVE, enrich_intent=enrich_intent
  )
  async with sessions() as db, db.begin():
    repo = TAllocationRepository(db)
    batch = await allocation._prepared(repo, snapshot, candidates, now=at)
    claim = await allocation._claim(repo, batch, snapshot, candidates, now=at)
    await repo.commit(claim=claim, snapshot=snapshot, candidates=candidates, now=at)
    intent = await db.get(TradeIntentRecord, "intent-0")
    intent.status = "EXECUTION_READY"
    source = await db.get(TAssistantExecutionRecord, intent.owner_id)
    gate, market = await input_for(db, intent.owner_id, intent.id)
    if path == "replacement":
      await db.run_sync(
        lambda session: PendingTradeOrder.__table__.create(
          session.connection(), checkfirst=True
        )
      )
      intent.intent_metadata = {
        **intent.intent_metadata,
        "risk_increase_order_request": {"t_order_parent_client_id": "parent"},
      }
      db.add(
        PendingTradeOrder(
          client_order_id="parent",
          user_id="fixture",
          account_id=intent.account_id,
          owner_type="T_ASSISTANT_EXECUTION",
          owner_id=intent.owner_id,
          environment="LIVE",
          instrument_code=intent.instrument_code,
          side="BUY",
          order_type="FIX_PRICE",
          volume=200,
          limit_price="9.9",
          status="CANCELLED",
          t_trade_role="ENTRY",
          intent_id=intent.id,
          t_order_original_created_at=market.timestamp - timedelta(seconds=31),
        )
      )
      await db.flush()
      monkeypatch.setattr(
        adapter_module,
        "build_entry_gate",
        AsyncMock(
          side_effect=AssertionError(
            "replacement must not create another candidate Gate"
          )
        ),
      )
    current = market.timestamp
    changed = False

    def validate():
      if changed:
        raise ValueError("TEST_MARKET_GENERATION_CHANGED")

    witness = adapter_module.LiveEntryMarketWitness(
      gate.latest_tick,
      gate.latest_ring_generation,
      gate.latest_accepted_sequence,
      market,
      validate,
    )
    provider = AsyncMock(return_value=None if fault == "missing" else witness)

    async def review(**kwargs):
      nonlocal current, changed
      # Actual Gate and domain execution binding, not a fabricated ALLOW input.
      if path == "initial":
        evaluated = EntryExecutionGate.evaluate_live(
          kwargs["gate_input"], execution=_execution_from_record(source)
        )
        assert evaluated.decision.value == "ALLOW", evaluated.reason_codes
      else:
        assert kwargs["client_order_id"] == "parent"
        assert kwargs["market_data"] == market
      if fault == "generation":
        changed = True
      elif fault == "expired":
        current += timedelta(seconds=4)
      return LiveEntryReviewResult("REVIEWED", ())

    reviewer = AsyncMock(side_effect=review)
    monkeypatch.setattr(
      adapter_module.LiveEntryExecutionReview
      if path == "initial"
      else adapter_module.LiveEntryReplacementExecutionReview,
      "review",
      reviewer,
    )
    adapter = adapter_module.LiveEntryReviewAdapter(
      db, witness_provider=provider, market_mark_reader=None, clock=lambda: current
    )
    if fault == "clock":
      current -= timedelta(seconds=1)
    call = adapter(
      execution_id=intent.owner_id, intent_id=intent.id, now=market.timestamp
    )
    if fault:
      with pytest.raises(
        ValueError, match="LIVE_ENTRY_|TEST_MARKET_GENERATION_CHANGED"
      ):
        await call
    else:
      assert (await call).outcome == "REVIEWED"
      reviewer.assert_awaited_once()
    provider.assert_awaited_once_with(intent.owner_id, intent.instrument_code)
    if fault in {"missing", "clock"}:
      reviewer.assert_not_awaited()


@pytest.mark.parametrize(
  "fault", [None, "generation", "ring", "unbound", "missing_book"]
)
async def test_supervisor_captures_live_book_without_waiting_for_its_lock(fault):
  import asyncio
  from datetime import UTC, datetime
  from types import SimpleNamespace

  from quantx_engine.t_assistant_live_supervisor import TAssistantLiveSupervisor
  from quantx_engine.t_assistant_paper_shadow_supervisor import _accepted_tick

  from tests.engine.unit.test_paper_market_runtime import raw_quote

  raw = raw_quote(1)
  code = "600000.SH"
  now = datetime.fromtimestamp(raw["source_time_ms"] / 1000, UTC)
  tick = _accepted_tick(
    code,
    raw,
    capture_time_ms=raw["source_time_ms"],
    accepted_sequence=1,
    discontinuity_reason=None,
  )
  current = (tick, 1, 1)
  hub = SimpleNamespace(
    is_ready=True, stream_id="stream", generation=1, latest=lambda key: raw
  )
  supervisor = TAssistantLiveSupervisor(quote_hub=hub, clock=lambda: now)
  binding = SimpleNamespace(
    builder=SimpleNamespace(entry_market_witness=lambda key: current),
    ready_market_identity=("stream", "1"),
  )
  supervisor._bindings["execution"] = binding
  if fault == "missing_book":
    raw.pop("askVol")
  async with supervisor._lock:
    witness = await asyncio.wait_for(
      supervisor.entry_market_witness("execution", code), timeout=0.5
    )
  if fault == "missing_book":
    assert witness is None
    return
  assert witness.market_data.source == "LIVE_ACCEPTED_WHOLE_QUOTE"
  assert witness.market_data.ask_price == raw["askPrice"]
  assert witness.market_data.ask_price is not raw["askPrice"]
  witness.validate()
  if fault == "generation":
    hub.generation = 2
  elif fault == "ring":
    current = (tick, 2, 1)
  elif fault == "unbound":
    supervisor._bindings.clear()
  if fault:
    with pytest.raises(ValueError, match="LIVE_ENTRY_MARKET_WITNESS_CHANGED"):
      witness.validate()
