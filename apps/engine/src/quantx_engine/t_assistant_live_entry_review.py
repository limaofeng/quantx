"""Engine adapter for fresh LIVE review; latest-ring validation stays Engine-owned."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from quantx_application.t_trade_v3.entry_execution_gate import EntryExecutionGateInput
from quantx_application.t_trade_v3.portfolio_reference import aware_time
from quantx_contracts import ExecutionEnvironment
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_domain.trading.t_assistant_market_state import AcceptedTMarketTick
from quantx_domain.trading.t_order_policy import TEntryOrderPolicy
from quantx_infrastructure.models.agent_runtime import PendingTradeOrder
from quantx_infrastructure.models.t_allocation import TAllocationDecisionRecord
from quantx_infrastructure.models.t_assistant_execution import TAssistantExecutionRecord
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.live_entry_execution_review import (
  LiveEntryExecutionReview,
)
from quantx_infrastructure.services.live_entry_replacement_execution_review import (
  LiveEntryReplacementExecutionReview,
)
from quantx_infrastructure.services.t_allocation_serialization import allocation_time

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


@dataclass(frozen=True)
class LiveEntryReplacementReviewInputs:
  client_order_id: str
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

  async def prepare_replacement(self, *, execution_id, intent_id, client_order_id, now):
    if not self.db.in_transaction():
      raise ValueError("LIVE_ENTRY_REVIEW_TRANSACTION_REQUIRED")
    now = aware_time(now)
    source = await self.db.get(TAssistantExecutionRecord, execution_id)
    intent = await self.db.get(TradeIntentRecord, intent_id)
    parent = await self.db.get(PendingTradeOrder, client_order_id)
    if (
      source is None
      or intent is None
      or parent is None
      or source.environment != "LIVE"
      or intent.environment != "LIVE"
      or intent.owner_type != "T_ASSISTANT_EXECUTION"
      or intent.owner_id != execution_id
      or intent.account_id != source.account_id
      or parent.intent_id != intent_id
      or parent.owner_id != execution_id
      or parent.owner_type != "T_ASSISTANT_EXECUTION"
      or parent.environment != "LIVE"
      or parent.instrument_code != intent.instrument_code
      or parent.account_id != source.account_id
      or parent.side != "BUY"
      or parent.t_trade_role != "ENTRY"
      or parent.t_order_original_created_at is None
    ):
      raise ValueError("LIVE_ENTRY_REPLACEMENT_SCOPE_INVALID")
    allocation = await self.db.get(
      TAllocationDecisionRecord, intent.allocation_decision_id
    )
    if allocation is None:
      raise ValueError("LIVE_ENTRY_REPLACEMENT_ALLOCATION_REQUIRED")
    expiry = min(
      allocation_time(allocation.expires_at),
      allocation_time(parent.t_order_original_created_at)
      + timedelta(seconds=TEntryOrderPolicy().total_ttl_seconds),
    )
    witness = await self.witness_provider(execution_id, intent.instrument_code)
    if not isinstance(witness, LiveEntryMarketWitness):
      raise ValueError("LIVE_ENTRY_LATEST_MARKET_REQUIRED")
    witness.validate()
    review_at = aware_time(self.clock())
    if review_at < now:
      raise ValueError("LIVE_ENTRY_REVIEW_CLOCK_REGRESSED")

    def validate_market():
      witness.validate()
      current = aware_time(self.clock())
      clocks = (
        witness.latest_tick.received_at_ms,
        witness.latest_tick.sample.source_time_ms,
      )
      ms = int(current.timestamp() * 1000)
      if (
        current < review_at
        or ms < max(clocks)
        or ms - min(clocks) > 2000
        or current >= expiry
      ):
        raise ValueError("LIVE_ENTRY_LATEST_MARKET_EXPIRED")

    validate_market()
    return LiveEntryReplacementReviewInputs(
      client_order_id, deepcopy(witness.market_data), review_at, validate_market
    )

  async def __call__(self, *, execution_id, intent_id, now):
    if not self.db.in_transaction():
      raise ValueError("LIVE_ENTRY_REVIEW_TRANSACTION_REQUIRED")
    intent = await self.db.get(TradeIntentRecord, intent_id)
    staged = (
      (intent.intent_metadata or {}).get("risk_increase_order_request")
      if intent
      else None
    )
    parent = (
      staged.get("t_order_parent_client_id") if isinstance(staged, dict) else None
    )
    if parent:
      inputs = await self.prepare_replacement(
        execution_id=execution_id, intent_id=intent_id, client_order_id=parent, now=now
      )
      result = await LiveEntryReplacementExecutionReview(self.db).review(
        client_order_id=parent,
        market_data=inputs.market_data,
        market_mark_reader=self.market_mark_reader,
        now=inputs.now,
      )
      inputs.validate_market()
      return result

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
