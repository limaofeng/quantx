"""Engine adapter for fresh LIVE review; latest-ring validation stays Engine-owned."""

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from quantx_application.t_trade_v3.entry_execution_gate import EntryExecutionGateInput
from quantx_application.t_trade_v3.portfolio_reference import aware_time
from quantx_contracts import ExecutionEnvironment
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_domain.trading.t_assistant_market_state import AcceptedTMarketTick
from quantx_infrastructure.models.t_assistant_execution import TAssistantExecutionRecord
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.live_entry_execution_review import (
  LiveEntryExecutionReview,
)

from quantx_engine.t_assistant_entry_gate_input import build_entry_gate


@dataclass(frozen=True)
class LiveEntryMarketWitness:
  latest_tick: AcceptedTMarketTick
  ring_generation: int
  last_accepted_sequence: int
  market_data: MarketDataSnapshot
  validate: Callable[[], None]


@dataclass(frozen=True)
class LiveEntryReviewInputs:
  gate_input: EntryExecutionGateInput
  market_data: MarketDataSnapshot
  now: datetime
  validate_market: Callable[[], None]


class LiveEntryReviewAdapter:
  def __init__(self, db, *, witness_provider, market_mark_reader, clock):
    self.db, self.witness_provider = db, witness_provider
    self.market_mark_reader, self.clock = market_mark_reader, clock

  async def prepare(self, *, execution_id, intent_id, now):
    if not self.db.in_transaction():
      raise ValueError("LIVE_ENTRY_REVIEW_TRANSACTION_REQUIRED")
    now = aware_time(now)
    source = await self.db.get(TAssistantExecutionRecord, execution_id)
    intent = await self.db.get(TradeIntentRecord, intent_id)
    if (
      source is None
      or intent is None
      or source.environment != "LIVE"
      or intent.environment != "LIVE"
      or intent.owner_type != "T_ASSISTANT_EXECUTION"
      or intent.owner_id != execution_id
      or intent.account_id != source.account_id
    ):
      raise ValueError("LIVE_ENTRY_REVIEW_SCOPE_INVALID")
    witness = await self.witness_provider(execution_id, intent.instrument_code)
    if not isinstance(witness, LiveEntryMarketWitness):
      raise ValueError("LIVE_ENTRY_LATEST_MARKET_REQUIRED")
    witness.validate()
    review_at = aware_time(self.clock())
    if review_at < now:
      raise ValueError("LIVE_ENTRY_REVIEW_CLOCK_REGRESSED")
    gate = await build_entry_gate(
      self.db, source, intent, witness, review_at, environment=ExecutionEnvironment.LIVE
    )

    def validate_market():
      # The provider must compare the captured ring/sequence and hub identity
      # synchronously, without waiting on a supervisor lock while DB locks are held.
      witness.validate()
      current = aware_time(self.clock())
      ms = int(current.timestamp() * 1000)
      tick = witness.latest_tick
      clocks = (tick.received_at_ms, tick.sample.source_time_ms)
      if (
        current < review_at
        or ms < max(clocks)
        or ms - min(clocks) > gate.policy.quote_max_age_ms
        or ms >= min(gate.intent_expires_at_ms, gate.candidate.expires_at_ms)
      ):
        raise ValueError("LIVE_ENTRY_LATEST_MARKET_EXPIRED")

    validate_market()
    return LiveEntryReviewInputs(gate, witness.market_data, review_at, validate_market)

  async def __call__(self, *, execution_id, intent_id, now):
    inputs = await self.prepare(execution_id=execution_id, intent_id=intent_id, now=now)
    result = await LiveEntryExecutionReview(self.db).review(
      execution_id=execution_id,
      intent_id=intent_id,
      gate_input=inputs.gate_input,
      market_data=inputs.market_data,
      market_mark_reader=self.market_mark_reader,
      now=inputs.now,
    )
    inputs.validate_market()
    return result
