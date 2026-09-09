"""Current portfolio and market risk review for an existing manual T lifecycle."""

import math
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_domain.brokers.base import OrderRequest, OrderType, PriceType
from quantx_domain.clock import SHANGHAI
from quantx_domain.strategies.base import TAssistantExecutionIntentOrigin, TradeIntent
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_domain.trading.order_sizer import OrderSizer
from quantx_domain.trading.risk_checker import RiskAction, TradingRiskChecker
from sqlalchemy import select

from quantx_infrastructure.models.agent_runtime import TTradeBatch
from quantx_infrastructure.models.t_allocation import TAllocationDecisionRecord
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.live_entry_execution_review import (
  LiveEntryReviewResult,
)
from quantx_infrastructure.services.live_entry_replacement_review import (
  LiveEntryReplacementPreflight,
  review_live_entry_replacement,
)
from quantx_infrastructure.services.live_portfolio_snapshot import (
  LivePortfolioSnapshotReader,
)
from quantx_infrastructure.services.live_position_attribution import (
  LivePositionAttributionService,
)
from quantx_infrastructure.services.t_allocation_serialization import allocation_time
from quantx_infrastructure.services.t_live_entry_authorization import (
  _authorize_live_entry,
)


@dataclass(frozen=True)
class LiveEntryReplacementReviewResult(LiveEntryReviewResult):
  preflight: LiveEntryReplacementPreflight | None = None
  portfolio_input_fingerprint: str | None = None


def _valid_market(market, now):
  if (
    not isinstance(market, MarketDataSnapshot)
    or market.timestamp is None
    or market.timestamp.tzinfo is None
    or not 0 <= (now - market.timestamp).total_seconds() <= 2
    or not market.is_trading
    or market.suspended
  ):
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
    and all(
      v is not None and math.isfinite(v) and v > 0
      for v in (
        market.price,
        market.price_tick,
        market.limit_up,
        market.limit_down,
      )
    )
    and market.limit_down < market.limit_up
  )


class LiveEntryReplacementExecutionReview:
  def __init__(self, db):
    self.db = db

  async def review(self, *, client_order_id, market_data, market_mark_reader, now):
    """No staging or dispatch: the account coordinator must revalidate before send."""
    if not self.db.in_transaction() or now.tzinfo is None or now.utcoffset() is None:
      raise ValueError("LIVE_REVIEW_TRANSACTION_AND_AWARE_TIME_REQUIRED")
    market_data = deepcopy(market_data)
    if not _valid_market(market_data, now):
      return LiveEntryReplacementReviewResult(
        "REJECT", ("T_ORDER_MARKET_EVIDENCE_INVALID",)
      )
    proof = await review_live_entry_replacement(
      self.db,
      client_order_id=client_order_id,
      now=now,
      reference_price=Decimal(str(market_data.ask_price[0])),
      price_tick=Decimal(str(market_data.price_tick)),
      limit_up=Decimal(str(market_data.limit_up)),
      limit_down=Decimal(str(market_data.limit_down)),
    )
    intent = await self.db.get(TradeIntentRecord, proof.intent_id)
    if market_data.instrument_code != intent.instrument_code:
      return LiveEntryReplacementReviewResult("REJECT", ("LIVE_REVIEW_INTENT_SCOPE",))

    class BoundMarks:
      async def read(self, **kwargs):
        marks = await market_mark_reader.read(**kwargs)
        current = marks.current.get(market_data.instrument_code)
        if (
          current is None
          or current.as_of != market_data.timestamp
          or current.price != Decimal(str(market_data.price))
        ):
          raise ValueError("LIVE_REPLACEMENT_MARKET_CUT_CONFLICT")
        return marks

    portfolio = await LivePortfolioSnapshotReader(self.db).read(
      execution_id=proof.execution_id,
      cycle_id=intent.allocation_cycle_id,
      instrument_codes=(intent.instrument_code,),
      as_of=now,
      market_mark_reader=BoundMarks(),
      account_max_age_seconds=90,
    )
    envelope = portfolio.envelopes[0]
    position = envelope.observed_position_projection
    own_batch = await self.db.get(TTradeBatch, intent.intent_metadata["t_batch_id"])
    conflicting = await self.db.scalar(
      select(TTradeBatch.batch_id)
      .where(
        TTradeBatch.account_id == intent.account_id,
        TTradeBatch.environment == "LIVE",
        TTradeBatch.instrument_code == intent.instrument_code,
        TTradeBatch.batch_id != own_batch.batch_id,
        TTradeBatch.entry_filled_volume > TTradeBatch.exit_filled_volume,
      )
      .limit(1)
    )
    # Preserve all economic exposure, cash and old-inventory claims. Only this
    # batch's already open entry is exempt from the new-opportunity blocker.
    reasons = tuple(
      code for code in envelope.reason_codes if code != "T_SAME_SYMBOL_ENTRY_OBLIGATION"
    )
    if conflicting or position.uncovered_entry_amount > 0:
      reasons += ("T_SAME_SYMBOL_ENTRY_OBLIGATION",)
    if portfolio.entry_blockers or reasons:
      return LiveEntryReplacementReviewResult(
        "REJECT",
        portfolio.entry_blockers + reasons,
        preflight=proof,
        portfolio_input_fingerprint=portfolio.portfolio_input_fingerprint,
      )
    own_active = int(own_batch.entry_filled_volume > own_batch.exit_filled_volume)
    if (
      portfolio.active_batch_count - own_active
      >= portfolio.policy.max_concurrent_batches
    ):
      return LiveEntryReplacementReviewResult(
        "DELAY", ("T_MAX_CONCURRENT_BATCHES",), preflight=proof
      )
    decision = await self.db.get(
      TAllocationDecisionRecord, intent.allocation_decision_id
    )
    industry = next(
      v
      for v in portfolio.industry_exposures
      if v.primary_industry == position.primary_industry
    )
    amount_cap = max(
      Decimal(0),
      min(
        Decimal(decision.allocated_amount_cap)
        - proof.limit_price * proof.filled_volume,
        portfolio.planning_amount_cap,
        envelope.max_incremental_t_amount,
        portfolio.policy.max_industry_t_amount
        - industry.current_t_exposure
        - industry.uncovered_buy_amount,
      ),
    )
    proposal = TradeIntent(
      strategy_id=intent.strategy_id,
      instrument_code=intent.instrument_code,
      direction="BUY",
      bucket=intent.bucket,
      reason=intent.reason,
      target_volume=proof.remaining_volume,
      intent_id=intent.id,
      metadata=dict(intent.intent_metadata),
      created_at=datetime.fromisoformat(intent.intent_metadata["intent_created_at"]),
      execution_ref=ExecutionOwnerRef("T_ASSISTANT_EXECUTION", proof.execution_id),
      origin=TAssistantExecutionIntentOrigin(
        proof.execution_id,
        "live-entry-replacement-review",
        cycle_id=intent.allocation_cycle_id,
      ),
    )
    balances = dict(
      cash=float(
        max(Decimal(0), portfolio.available_cash - portfolio.uncovered_buy_amount)
      ),
      total_asset=float(portfolio.total_assets),
    )
    holding = dict(
      volume=position.locked_core + position.core + position.swing,
      available_volume=position.old_sellable_volume,
      t_trade_exit_capacity=min(
        envelope.planning_replaceable_old_volume_ceiling,
        envelope.planning_entry_volume_ceiling,
      ),
    )
    draft = OrderSizer().draft_intent(
      proposal,
      OrderType.BUY,
      float(proof.limit_price),
      balances,
      holding,
      allocated_amount_cap=amount_cap,
    )
    if not 0 < draft.sized_volume <= proof.remaining_volume:
      return LiveEntryReplacementReviewResult(
        "REJECT",
        tuple(draft.size_reason_codes) + ("LIVE_REVIEW_ZERO_SIZE",),
        preflight=proof,
      )
    attribution = await LivePositionAttributionService(self.db).read(
      account_id=intent.account_id,
      as_of=now,
      max_age_seconds=90,
    )
    request = OrderRequest(
      instrument_code=intent.instrument_code,
      order_type=OrderType.BUY,
      price_type=PriceType.LIMIT,
      volume=draft.sized_volume,
      price=float(proof.limit_price),
      execution_ref=proposal.execution_ref,
      environment=ExecutionEnvironment.LIVE,
      metadata=dict(
        bucket=intent.bucket,
        intent_id=intent.id,
        portfolio_input_fingerprint=portfolio.portfolio_input_fingerprint,
        t_order_parent_client_id=client_order_id,
        order_expire_at_ms=int(
          min(proof.expires_at, allocation_time(decision.expires_at)).timestamp() * 1000
        ),
        t_trading_envelope=dict(
          observed_position_projection=attribution.projection.instruments[
            intent.instrument_code
          ],
          protected_old_position_floor=envelope.protected_old_position_floor,
          allow_core_claim=True,
          attribution_evidence_hash=attribution.evidence_hash,
        ),
      ),
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
      return LiveEntryReplacementReviewResult(
        risk.action.value, (risk.reason_code,), preflight=proof
      )
    if not 0 < risk.final_volume <= draft.sized_volume:
      raise ValueError("T_ENTRY_REPLACEMENT_RISK_VOLUME_INVALID")
    request = replace(request, volume=risk.final_volume)
    actor = await _authorize_live_entry(
      self.db,
      intent=intent,
      account_id=intent.account_id,
      instrument_code=intent.instrument_code,
      volume=request.volume,
      limit_price=proof.limit_price,
      now=now,
      allowed_statuses={"EXECUTION_PENDING", "PARTIAL_FILLED"},
    )
    if actor != proof.user_id:
      raise ValueError("T_ENTRY_REPLACEMENT_ACTOR_CONFLICT")
    return LiveEntryReplacementReviewResult(
      "REVIEWED",
      tuple(draft.size_reason_codes),
      actor,
      request,
      draft,
      risk,
      proof,
      portfolio.portfolio_input_fingerprint,
    )
