"""Causal, stable historical Tick frames for the shared-account runner."""

from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import groupby
from math import isfinite

from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_domain.trading.t_assistant_market_state import AcceptedTMarketTick


@dataclass(frozen=True)
class BacktestTick:
  decision_time: datetime
  source_sequence: int
  source_identity: str
  tick: AcceptedTMarketTick
  market: MarketDataSnapshot

  def __post_init__(self):
    times = (self.decision_time, self.market.timestamp)
    if any(t.tzinfo is None or t.utcoffset() is None for t in times):
      raise ValueError("BACKTEST_AWARE_TIME_REQUIRED")
    sample = self.tick.sample
    now_ms = int(self.decision_time.timestamp() * 1000)
    if (
      max(sample.source_time_ms, self.tick.received_at_ms) > now_ms
      or self.market.timestamp > self.decision_time
      or int(self.market.timestamp.timestamp() * 1000) != sample.source_time_ms
      or self.market.instrument_code != sample.instrument_code
      or self.market.price != sample.price
    ):
      raise ValueError("BACKTEST_FUTURE_OR_MIXED_MARKET")
    if type(self.source_sequence) is not int or self.source_sequence < 1:
      raise ValueError("BACKTEST_SOURCE_SEQUENCE_REQUIRED")
    if not self.source_identity:
      raise ValueError("BACKTEST_SOURCE_IDENTITY_REQUIRED")
    books = (
      self.market.bid_price,
      self.market.ask_price,
      self.market.bid_vol,
      self.market.ask_vol,
    )
    if any(
      len(values) != 5 or any(not isfinite(v) or v < 0 for v in values)
      for values in books
    ):
      raise ValueError("BACKTEST_FULL_BOOK_REQUIRED")
    if (
      self.market.bid_price[0] != sample.bid_price
      or self.market.ask_price[0] != sample.ask_price
      or self.market.bid_vol[0] != sample.bid_volume
      or self.market.ask_vol[0] != sample.ask_volume
    ):
      raise ValueError("BACKTEST_MARKET_WITNESS_CONFLICT")

  @property
  def key(self):
    return (
      self.decision_time.astimezone(UTC),
      0,
      self.source_sequence,
      self.market.instrument_code,
      self.source_identity,
    )


def tick_frames(events):
  """Coalesce simultaneous quotes before allocation; never reorder duplicates."""
  ordered = sorted(events, key=lambda event: event.key)
  seen = set()
  sequences = {}
  for item in ordered:
    identity = (item.tick.stream_id, item.market.instrument_code, item.source_identity)
    if identity in seen:
      raise ValueError("BACKTEST_DUPLICATE_SOURCE")
    seen.add(identity)
    code = item.market.instrument_code
    previous = sequences.get(code, 0)
    if item.tick.accepted_sequence <= previous:
      raise ValueError("BACKTEST_NON_MONOTONIC_SOURCE")
    sequences[code] = item.tick.accepted_sequence
  for at, grouped in groupby(ordered, key=lambda event: event.decision_time):
    yield at, tuple(grouped)
