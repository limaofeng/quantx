"""Causal COMPLETE one-minute features shared by Research and Engine."""

from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from zoneinfo import ZoneInfo

from quantx_domain.trading.t_assistant_execution import stable_manifest_hash
from quantx_domain.trading.t_assistant_market_state import AcceptedTMarketTick

FEATURE_ORDER = (
  "return_1m",
  "range_1m",
  "relative_spread",
  "depth_imbalance",
  "turnover_growth",
  "tick_rate",
)
FEATURE_SCHEMA_VERSION = 1


def market_session(at_ms: int) -> str:
  at = datetime.fromtimestamp(at_ms / 1000, UTC).astimezone(ZoneInfo("Asia/Shanghai"))
  minute = at.hour * 60 + at.minute
  if 570 <= minute < 690:
    return f"{at.date()}:AM"
  if 780 <= minute < 900:
    return f"{at.date()}:PM"
  raise ValueError("T_MODEL_OUTSIDE_CONTINUOUS_SESSION")


@dataclass(frozen=True)
class TModelFeatureBar:
  feature_bar_id: str
  instrument_code: str
  interval_start_ms: int
  interval_end_ms: int
  available_at_ms: int
  market_session: str
  stream_id: str
  continuity_generation: str
  source_fence_range: tuple[int, int]
  feature_schema_version: int
  capability_manifest_version: str
  feature_values: tuple[float, ...]
  feature_coverage: float
  feature_vector_hash: str
  market_context_as_of_ms: int
  sector_context_as_of_ms: int
  status: str = "COMPLETE"


def build_complete_feature_bar(
  ticks: tuple[AcceptedTMarketTick, ...],
  *,
  interval_start_ms: int,
  watermark_ms: int,
  available_at_ms: int,
  capability_manifest_version: str,
  market_context_as_of_ms: int,
  sector_context_as_of_ms: int,
  max_gap_ms: int,
) -> TModelFeatureBar:
  """Close only a covered, continuous minute; late repairs cannot alter identity.

  The caller provides the accepted-stream watermark, not the wall clock. Source
  and receipt clocks are both retained in the identity. No candidate filtering
  enters this function: each eligible bar is an observation anchor.
  """
  clocks = (
    interval_start_ms,
    watermark_ms,
    available_at_ms,
    market_context_as_of_ms,
    sector_context_as_of_ms,
    max_gap_ms,
  )
  if (
    any(type(value) is not int or value < 0 for value in clocks)
    or not 0 < max_gap_ms < 60_000
  ):
    raise ValueError("T_MODEL_FEATURE_CLOCK_INVALID")
  end = interval_start_ms + 60_000
  if interval_start_ms % 60_000 or not capability_manifest_version:
    raise ValueError("T_MODEL_FEATURE_IDENTITY_INVALID")
  if watermark_ms < end or available_at_ms < watermark_ms:
    raise ValueError("T_MODEL_MINUTE_NOT_COMPLETE")
  if max(market_context_as_of_ms, sector_context_as_of_ms) > interval_start_ms:
    raise ValueError("T_MODEL_FUTURE_CONTEXT")
  session = market_session(interval_start_ms)
  if session != market_session(end - 1) or len(ticks) < 2:
    raise ValueError("T_MODEL_FEATURE_COVERAGE_INVALID")
  first = ticks[0]
  prices, spreads, imbalances, amounts, source_times = [], [], [], [], []
  previous = None
  for tick in ticks:
    sample = tick.sample
    source = sample.source_time_ms
    if (
      tick.instrument_code != first.instrument_code
      or tick.stream_id != first.stream_id
      or sample.continuity_generation != first.sample.continuity_generation
      or tick.discontinuity_reason is not None
      or not interval_start_ms <= source < end
      or not source <= tick.received_at_ms <= available_at_ms
      or (
        previous is not None
        and (
          tick.accepted_sequence != previous.accepted_sequence + 1
          or tick.market_fence_sequence <= previous.market_fence_sequence
          or (source, sample.tick_ordinal)
          <= (previous.sample.source_time_ms, previous.sample.tick_ordinal)
          or tick.received_at_ms < previous.received_at_ms
        )
      )
    ):
      raise ValueError("T_MODEL_FEATURE_CONTINUITY_INVALID")
    values = (
      sample.price,
      sample.bid_price,
      sample.ask_price,
      sample.bid_volume,
      sample.ask_volume,
      sample.cumulative_amount,
    )
    if any(
      type(value) not in (int, float) or not isfinite(value) or value < 0
      for value in values
    ):
      raise ValueError("T_MODEL_FEATURE_CAPABILITY_MISSING")
    price, bid, ask, bid_qty, ask_qty, amount = values
    if min(price, bid, ask, bid_qty + ask_qty) <= 0 or ask < bid:
      raise ValueError("T_MODEL_FEATURE_MARKET_INVALID")
    prices.append(price)
    spreads.append((ask - bid) / ((ask + bid) / 2))
    imbalances.append((bid_qty - ask_qty) / (bid_qty + ask_qty))
    amounts.append(amount)
    source_times.append(source)
    previous = tick
  gaps = [source_times[0] - interval_start_ms, end - source_times[-1]] + [
    right - left for left, right in zip(source_times, source_times[1:])
  ]
  if max(gaps) > max_gap_ms or any(b < a for a, b in zip(amounts, amounts[1:])):
    raise ValueError("T_MODEL_FEATURE_COVERAGE_INVALID")
  amount_delta = amounts[-1] - amounts[0]
  features = (
    prices[-1] / prices[0] - 1,
    (max(prices) - min(prices)) / prices[0],
    sum(spreads) / len(spreads),
    sum(imbalances) / len(imbalances),
    amount_delta / max(amounts[0], amount_delta, 1.0),
    len(ticks) / 60.0,
  )
  vector_hash = stable_manifest_hash(
    {
      "schema": FEATURE_SCHEMA_VERSION,
      "feature_order": FEATURE_ORDER,
      "values": features,
    }
  )
  identity = {
    "instrument_code": first.instrument_code,
    "start": interval_start_ms,
    "end": end,
    "available_at_ms": available_at_ms,
    "stream_id": first.stream_id,
    "generation": first.sample.continuity_generation,
    "capability": capability_manifest_version,
    "market_context": market_context_as_of_ms,
    "sector_context": sector_context_as_of_ms,
    "vector_hash": vector_hash,
    "source": [tick.manifest_item() for tick in ticks],
    "max_gap_ms": max_gap_ms,
  }
  return TModelFeatureBar(
    stable_manifest_hash(identity),
    first.instrument_code,
    interval_start_ms,
    end,
    available_at_ms,
    session,
    first.stream_id,
    first.sample.continuity_generation,
    (first.market_fence_sequence, ticks[-1].market_fence_sequence),
    FEATURE_SCHEMA_VERSION,
    capability_manifest_version,
    features,
    1.0,
    vector_hash,
    market_context_as_of_ms,
    sector_context_as_of_ms,
  )
