"""Review LIVE T entries against fresh persisted capacity, without sending orders."""

import math
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal

from quantx_application.t_trade_v3.entry_execution_gate import EntryExecutionGateInput
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_domain.brokers.base import OrderRequest, OrderType, PriceType
from quantx_domain.clock import SHANGHAI
from quantx_domain.strategies.base import TAssistantExecutionIntentOrigin, TradeIntent
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_domain.trading.order_sizer import OrderDraft, OrderSizer
from quantx_domain.trading.risk_checker import (
  OrderRiskDecision,
  RiskAction,
  TradingRiskChecker,
)

from quantx_infrastructure.models.t_allocation import TAllocationDecisionRecord
from quantx_infrastructure.models.t_assistant_execution import TAssistantExecutionRecord
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.live_portfolio_snapshot import (
  LivePortfolioSnapshotReader,
)
from quantx_infrastructure.services.t_entry_gate_review import review_t_entry_gate
from quantx_infrastructure.services.t_live_entry_authorization import (
  authorize_live_entry,
)


@dataclass(frozen=True)
class LiveEntryReviewResult:
  outcome: str
  reason_codes: tuple[str, ...]
  user_id: str | None = None
  request: OrderRequest | None = None
  sizing: OrderDraft | None = None
  risk: OrderRiskDecision | None = None


def _same_market(gate, market):
  tick = gate.latest_tick.sample if gate.latest_tick else None
  if tick is None or market.timestamp.tzinfo is None:
    return False
  for prices, volumes in (
    (market.bid_price, market.bid_vol),
    (market.ask_price, market.ask_vol),
  ):
    if len(prices) != 5 or len(volumes) != 5:
      return False
    if any(not math.isfinite(v) or v <= 0 for v in prices):
      return False
    if any(not math.isfinite(v) or v < 0 for v in volumes):
      return False
  return (
    list(market.bid_price) == sorted(market.bid_price, reverse=True)
    and list(market.ask_price) == sorted(market.ask_price)
    and market.bid_price[0] <= market.ask_price[0]
    and market.instrument_code == gate.instrument_code
    and market.timestamp == datetime.fromtimestamp(tick.source_time_ms / 1000, UTC)
    and market.price == tick.price
    and market.bid_price[0] == tick.bid_price
    and market.ask_price[0] == tick.ask_price
    and market.bid_vol[0] == tick.bid_volume
    and market.ask_vol[0] == tick.ask_volume
  )


class LiveEntryExecutionReview:
  def __init__(self, db):
    self.db = db

  async def review(
    self, *, execution_id, intent_id, gate_input, market_data, market_mark_reader, now
  ):
    """Caller commits and persists denials. A reviewed request is not an order."""
    if not self.db.in_transaction() or now.tzinfo is None or now.utcoffset() is None:
      raise ValueError("LIVE_REVIEW_TRANSACTION_AND_AWARE_TIME_REQUIRED")
    if not isinstance(gate_input, EntryExecutionGateInput) or not isinstance(
      market_data, MarketDataSnapshot
    ):
      raise TypeError("LIVE_REVIEW_TYPED_MARKET_REQUIRED")
    intent = await self.db.get(TradeIntentRecord, intent_id)
    if (
      intent is None or intent.owner_id != execution_id or intent.environment != "LIVE"
    ):
      return LiveEntryReviewResult("REJECT", ("LIVE_REVIEW_INTENT_SCOPE",))
    portfolio = await LivePortfolioSnapshotReader(self.db).read(
      execution_id=execution_id,
      cycle_id=intent.allocation_cycle_id,
      instrument_codes=(intent.instrument_code,),
      as_of=now,
      market_mark_reader=market_mark_reader,
      account_max_age_seconds=90,
      review_intent_id=intent_id,
    )
    execution = await self.db.get(TAssistantExecutionRecord, execution_id)
    gate = await review_t_entry_gate(
      self.db, execution=execution, intent=intent, gate=gate_input, now=now
    )
    if gate.outcome != "ALLOW":
      return LiveEntryReviewResult(gate.outcome, gate.reason_codes)
    if not _same_market(gate_input, market_data):
      return LiveEntryReviewResult("REJECT", ("LIVE_REVIEW_MARKET_WITNESS_CONFLICT",))
    envelope = portfolio.envelopes[0]
    if portfolio.entry_blockers or not envelope.positive_t_eligible:
      return LiveEntryReviewResult(
        "REJECT", portfolio.entry_blockers + envelope.reason_codes
      )
    decision = await self.db.get(
      TAllocationDecisionRecord, intent.allocation_decision_id
    )
    if portfolio.active_batch_count >= portfolio.policy.max_concurrent_batches:
      return LiveEntryReviewResult("DELAY", ("T_MAX_CONCURRENT_BATCHES",))
    industry = next(
      v
      for v in portfolio.industry_exposures
      if v.primary_industry == envelope.observed_position_projection.primary_industry
    )
    industry_remaining = max(
      Decimal(0),
      portfolio.policy.max_industry_t_amount
      - industry.current_t_exposure
      - industry.uncovered_buy_amount,
    )
    amount_cap = min(
      industry_remaining,
      Decimal(decision.allocated_amount_cap),
      portfolio.planning_amount_cap,
      envelope.max_incremental_t_amount,
    )
    domain_intent = TradeIntent(
      strategy_id=intent.strategy_id,
      instrument_code=intent.instrument_code,
      direction="BUY",
      bucket=intent.bucket,
      reason=intent.reason,
      target_amount=intent.target_amount,
      target_volume=intent.target_volume,
      target_position_pct=intent.target_position_pct,
      intent_id=intent.id,
      metadata=dict(intent.intent_metadata),
      created_at=datetime.fromisoformat(intent.intent_metadata["intent_created_at"]),
      execution_ref=ExecutionOwnerRef("T_ASSISTANT_EXECUTION", execution_id),
      origin=TAssistantExecutionIntentOrigin(
        execution_id,
        "live-entry-review",
        candidate_id=gate.candidate.candidate_id,
        opportunity_id=gate.candidate.candidate_id,
        cycle_id=intent.allocation_cycle_id,
      ),
    )
    price = float(
      intent.limit_price_hint
      if intent.limit_price_hint is not None
      else gate.candidate.price
    )
    position = envelope.observed_position_projection
    balances = {
      "cash": float(
        max(Decimal(0), portfolio.available_cash - portfolio.uncovered_buy_amount)
      ),
      "total_asset": float(portfolio.total_assets),
    }
    holding = {
      "volume": position.locked_core + position.core + position.swing,
      "available_volume": position.old_sellable_volume,
      "t_trade_exit_capacity": min(
        envelope.planning_replaceable_old_volume_ceiling,
        envelope.planning_entry_volume_ceiling,
      ),
    }
    draft = OrderSizer().draft_intent(
      domain_intent,
      OrderType.BUY,
      price,
      balances,
      holding,
      allocated_amount_cap=amount_cap,
    )
    if draft.sized_volume <= 0:
      return LiveEntryReviewResult(
        "REJECT", tuple(draft.size_reason_codes) + ("LIVE_REVIEW_ZERO_SIZE",)
      )
    from quantx_infrastructure.services.live_position_attribution import (
      LivePositionAttributionService,
    )

    attribution = await LivePositionAttributionService(self.db).read(
      account_id=intent.account_id,
      as_of=now,
      max_age_seconds=90,
    )
    bucket_inventory = attribution.projection.instruments[intent.instrument_code]
    request = OrderRequest(
      instrument_code=intent.instrument_code,
      order_type=OrderType.BUY,
      price_type=PriceType.LIMIT,
      volume=draft.sized_volume,
      price=price,
      execution_ref=domain_intent.execution_ref,
      environment=ExecutionEnvironment.LIVE,
      metadata={
        "t_trading_envelope": {
          "observed_position_projection": bucket_inventory,
          "protected_old_position_floor": envelope.protected_old_position_floor,
          "allow_core_claim": True,
          "attribution_evidence_hash": attribution.evidence_hash,
        },
        "bucket": intent.bucket,
        "intent_id": intent.id,
        "portfolio_input_fingerprint": portfolio.portfolio_input_fingerprint,
        "order_expire_at_ms": min(
          gate.candidate.expires_at_ms, gate_input.intent_expires_at_ms
        ),
      },
    )
    risk = await TradingRiskChecker(
      strict_market_data=True, strict_limit_data=True, enforce_trading_hours=True
    ).evaluate_order(
      request,
      account=balances,
      position=holding,
      market_data=market_data,
      current_time=now.astimezone(SHANGHAI),
    )
    if not risk.allowed or risk.action not in {RiskAction.ALLOW, RiskAction.CAP}:
      return LiveEntryReviewResult(risk.action.value, (risk.reason_code,))
    request = replace(request, volume=risk.final_volume)
    user_id = await authorize_live_entry(
      self.db,
      intent=intent,
      account_id=intent.account_id,
      instrument_code=intent.instrument_code,
      volume=request.volume,
      limit_price=Decimal(str(price)),
      now=now,
    )
    return LiveEntryReviewResult(
      "REVIEWED",
      tuple(gate.reason_codes) + tuple(draft.size_reason_codes),
      user_id,
      request,
      draft,
      risk,
    )
