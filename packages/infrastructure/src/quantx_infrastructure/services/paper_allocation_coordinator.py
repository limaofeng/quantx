"""Project committed RULE_ONLY evidence into the one durable portfolio allocator.

The caller owns the transaction. No cash or inventory is reserved here; every
attempt, including recovery, is rebuilt from the current authoritative PAPER cut.
"""

from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal

from quantx_application.t_trade_v3.portfolio_allocation import TAllocationCandidate
from quantx_domain.clock import SHANGHAI
from quantx_domain.trading.exit_plan import estimate_buy_fee_cny
from quantx_domain.trading.market_rules import AShareMarketRules
from quantx_domain.trading.t_assistant_execution import stable_manifest_hash
from quantx_domain.trading.t_trade_opportunity_engine import DataHealth, OpportunityPath
from sqlalchemy import select

from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
  TTradeOpportunityEvaluation,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_allocation_repository import (
  TAllocationConflict,
  TAllocationRepository,
)
from quantx_infrastructure.repositories.t_assistant_decision_cycle_repository import (
  TAssistantDecisionCycleRepository,
)
from quantx_infrastructure.repositories.t_trade_opportunity_intelligence_repository import (
  _evaluation_fingerprint,
)
from quantx_infrastructure.services.paper_portfolio_snapshot import (
  PaperPortfolioSnapshotReader,
)
from quantx_infrastructure.services.t_allocation_serialization import allocation_time


def _number(value):
  if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
    raise ValueError("T_ALLOCATION_SOURCE_NUMBER_INVALID")
  result = Decimal(str(value))
  if not result.is_finite() or result < 0:
    raise ValueError("T_ALLOCATION_SOURCE_NUMBER_INVALID")
  return result


def _milliseconds(value):
  if type(value) is not int or value < 0:
    raise ValueError("T_ALLOCATION_SOURCE_TIME_INVALID")
  return datetime.fromtimestamp(value / 1000, tz=UTC)


def candidate_from_evaluation(intent, evidence, *, now: datetime):
  """Use the cycle's immutable evaluation, never a later mutable symbol score."""
  metadata = intent.intent_metadata
  raw = evidence.payload["candidate_evidence"]["evaluation"]
  observed = _milliseconds(metadata["source_time_ms"])
  created = datetime.fromisoformat(metadata["intent_created_at"])
  if created.tzinfo is None or created.utcoffset() is None:
    raise ValueError("T_ALLOCATION_SOURCE_TIME_INVALID")
  ttl = metadata["approval_ttl_ms"]
  if type(ttl) is not int or ttl <= 0:
    raise ValueError("T_ALLOCATION_SOURCE_TIME_INVALID")
  if (
    observed > created
    or created > now
    or raw["candidate_id"] != metadata["candidate_id"]
    or raw["candidate_fingerprint"] != metadata["candidate_fingerprint"]
    or raw["instrument_code"] != intent.instrument_code
    or raw["source_time_ms"] != metadata["source_time_ms"]
    or raw["tick_ordinal"] != metadata["tick_ordinal"]
    or raw["policy_version"] != metadata["policy_version"]
    or raw["feature_schema_version"] != metadata["feature_schema_version"]
    or _number(raw["opportunity_score"]) != _number(metadata["opportunity_score"])
    or _milliseconds(raw["evaluated_at_ms"]) > now
  ):
    raise ValueError("T_ALLOCATION_SOURCE_BINDING_CONFLICT")
  score = _number(raw["opportunity_score"])
  if score > 100:
    raise ValueError("T_ALLOCATION_SOURCE_SCORE_INVALID")
  path = {
    OpportunityPath.PULLBACK_REBOUND.value: "PULLBACK",
    OpportunityPath.MOMENTUM_ACCELERATION.value: "MOMENTUM",
  }.get(raw["selected_path"])
  if path is None:
    raise ValueError("T_ALLOCATION_SOURCE_PATH_INVALID")
  components = [
    item
    for item in raw[path.lower()]["components"]
    if item["name"] == f"{path}_LIQUIDITY"
  ]
  if len(components) != 1:
    raise ValueError("T_ALLOCATION_SOURCE_LIQUIDITY_REQUIRED")
  component = components[0]
  contribution, weight = (
    _number(component["contribution"]),
    _number(component["weight"]),
  )
  if contribution > weight:
    raise ValueError("T_ALLOCATION_SOURCE_LIQUIDITY_INVALID")
  quality = contribution / weight if weight else Decimal(0)
  features = raw["features"]
  price = max(_number(features["price"]), _number(features["ask_price"]))
  if intent.limit_price_hint is not None:
    price = max(price, _number(intent.limit_price_hint))
  tick = _number(features["price_tick"])
  deviation = _number(metadata["max_price_deviation_bps"])
  if min(price, tick) <= 0:
    raise ValueError("T_ALLOCATION_SOURCE_PRICE_INVALID")
  # Ceiling to a legal tick at the original execution deviation bound. This is
  # only a feasibility budget; the final Sizer owns legal quantity and price.
  price = (price * (1 + deviation / 10000) / tick).to_integral_value(
    rounding=ROUND_CEILING
  ) * tick
  lot = AShareMarketRules.lot_size
  cost = price * lot + Decimal(
    str(estimate_buy_fee_cny(price=float(price), volume=lot))
  )
  expires = min(
    created + timedelta(milliseconds=ttl),
    observed + timedelta(milliseconds=ttl),
    _milliseconds(raw["candidate_expires_at_ms"]),
  )
  health = DataHealth(raw["data_health"])
  return TAllocationCandidate(
    intent_id=intent.id,
    intent_version=intent.allocation_version,
    candidate_id=metadata["candidate_id"],
    candidate_fingerprint=metadata["candidate_fingerprint"],
    instrument_code=intent.instrument_code,
    rank_score=score / 100,
    rule_score=score,
    liquidity_quality=quality,
    observed_at=observed,
    expires_at=expires,
    requested_amount_ceiling=_number(intent.target_amount),
    conservative_lot_cost=cost,
    minimum_entry_volume=lot,
    data_healthy=health is DataHealth.READY,
    next_eligible_at=(
      allocation_time(intent.allocation_next_eligible_at)
      if intent.allocation_next_eligible_at
      else None
    ),
  )


class PaperAllocationCoordinator:
  def __init__(self, db):
    self.db = db

  async def allocate_cycle(
    self,
    *,
    execution_id: str,
    cycle_id: str,
    processing_owner: str,
    now: datetime,
    lease_seconds: int = 10,
  ):
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
      raise ValueError("T_ALLOCATION_AWARE_TIME_REQUIRED")
    TAllocationRepository._validate_lease(processing_owner, lease_seconds)
    now = now.astimezone(UTC)
    cycle = await TAssistantDecisionCycleRepository(self.db).get(
      cycle_id, for_update=True
    )
    if (
      cycle is None
      or cycle.execution_id != execution_id
      or cycle.status != "PROPOSALS_COMMITTED"
      or not isinstance(cycle.output_manifest, dict)
      or stable_manifest_hash(cycle.output_manifest) != cycle.output_manifest_hash
    ):
      raise TAllocationConflict("T_ALLOCATION_CYCLE_NOT_COMMITTED")
    rows = list(
      (
        await self.db.scalars(
          select(TradeIntentRecord)
          .where(
            TradeIntentRecord.allocation_cycle_id == cycle_id,
            TradeIntentRecord.status == "ALLOCATION_PENDING",
          )
          .order_by(TradeIntentRecord.id)
          .with_for_update()
          .execution_options(populate_existing=True)
        )
      ).all()
    )
    rows = [
      row
      for row in rows
      if row.allocation_next_eligible_at is None
      or allocation_time(row.allocation_next_eligible_at) <= now
    ]
    if not rows:
      return None
    accepted = {
      item["intent_id"]: item for item in cycle.output_manifest["accepted_intents"]
    }
    keys = [accepted[row.id]["candidate_evidence_key"] for row in rows]
    evidence = list(
      (
        await self.db.scalars(
          select(TTradeOpportunityEvaluation).where(
            TTradeOpportunityEvaluation.event_key.in_(keys)
          )
        )
      ).all()
    )
    if len(evidence) != len(keys) or len(set(keys)) != len(keys):
      raise TAllocationConflict("T_ALLOCATION_SOURCE_EVIDENCE_REQUIRED")
    for event in evidence:
      material = {
        column.key: getattr(event, column.key)
        for column in event.__mapper__.column_attrs
      }
      evaluated_at = event.evaluated_at
      if evaluated_at.tzinfo is None:
        evaluated_at = evaluated_at.replace(tzinfo=SHANGHAI)
      if (
        event.owner_type != "T_ASSISTANT_EXECUTION"
        or event.owner_id != execution_id
        or event.environment != "PAPER"
        or event.event_type != "T_OPPORTUNITY_CANDIDATE_FROZEN"
        or event.content_fingerprint != _evaluation_fingerprint(material)
        or evaluated_at > now
        or allocation_time(event.created_at) > now
      ):
        raise TAllocationConflict("T_ALLOCATION_SOURCE_EVIDENCE_CONFLICT")
    candidates = []
    for row in rows:
      matching = [
        event
        for event in evidence
        if event.candidate_id == row.intent_metadata["candidate_id"]
        and event.instrument_code == row.instrument_code
        and event.account_id == row.account_id
        and event.event_key == accepted[row.id]["candidate_evidence_key"]
        and stable_manifest_hash(event.payload["candidate_evidence"])
        == accepted[row.id]["candidate_evidence_hash"]
      ]
      if len(matching) != 1:
        raise TAllocationConflict("T_ALLOCATION_SOURCE_EVIDENCE_REQUIRED")
      candidates.append(candidate_from_evaluation(row, matching[0], now=now))
    candidates = tuple(candidates)
    snapshot = await PaperPortfolioSnapshotReader(self.db).read(
      execution_id=execution_id,
      cycle_id=cycle_id,
      instrument_codes=tuple(candidate.instrument_code for candidate in candidates),
      as_of=now,
    )
    repository = TAllocationRepository(self.db)
    # An expired candidate still needs a durable REJECT/EXPIRED decision. The
    # coordination lease is independent of the original candidate TTL.
    batch = await repository.prepare(
      snapshot=snapshot,
      candidates=candidates,
      now=now,
      expires_at=now + timedelta(seconds=lease_seconds),
    )
    claim = await repository.claim(
      allocation_batch_id=batch.allocation_batch_id,
      processing_owner=processing_owner,
      snapshot=snapshot,
      candidates=candidates,
      now=now,
      lease_seconds=lease_seconds,
    )
    if claim is None:
      return await repository.get(batch.allocation_batch_id)
    return await repository.commit(
      claim=claim, snapshot=snapshot, candidates=candidates, now=now
    )
