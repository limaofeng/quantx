"""Shared immutable candidate evidence projection for PAPER and BACKTEST."""

from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, Decimal

from quantx_application.t_trade_v3.portfolio_allocation import TAllocationCandidate
from quantx_domain.trading.exit_plan import estimate_buy_fee_cny
from quantx_domain.trading.market_rules import AShareMarketRules
from quantx_domain.trading.t_trade_opportunity_engine import DataHealth, OpportunityPath

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


def candidate_from_evaluation(intent, evidence, *, now: datetime, costs=None):
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
    str(estimate_buy_fee_cny(price=float(price), volume=lot, costs=costs))
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
