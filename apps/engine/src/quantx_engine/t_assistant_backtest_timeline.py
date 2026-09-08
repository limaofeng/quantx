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


class _SequenceValidator:
  def __init__(self, presorted):
    self.presorted = presorted
    self.seen = set()
    self.sequences = {}
    self.sources = {}
    self.last_key = None

  def accept(self, item):
    if self.last_key is not None and item.key < self.last_key:
      raise ValueError("BACKTEST_NON_MONOTONIC_TIMELINE")
    self.last_key = item.key
    identity = (item.tick.stream_id, item.market.instrument_code, item.source_identity)
    code = item.market.instrument_code
    source_key = (item.tick.sample.source_time_ms, item.tick.sample.tick_ordinal)
    if (not self.presorted and identity in self.seen) or self.sources.get(
      code
    ) == source_key:
      raise ValueError("BACKTEST_DUPLICATE_SOURCE")
    if not self.presorted:
      self.seen.add(identity)
    if item.tick.accepted_sequence <= self.sequences.get(
      code, 0
    ) or source_key < self.sources.get(code, (-1, -1)):
      raise ValueError("BACKTEST_NON_MONOTONIC_SOURCE")
    self.sources[code] = source_key
    self.sequences[code] = item.tick.accepted_sequence
    return item


def tick_frames(events, *, presorted=False):
  """Coalesce simultaneous quotes before allocation; never reorder duplicates."""
  ordered = events if presorted else sorted(events, key=lambda event: event.key)
  validator = _SequenceValidator(presorted)
  for at, grouped in groupby(
    map(validator.accept, ordered), key=lambda event: event.decision_time
  ):
    yield at, tuple(grouped)


async def iterate_events(events):
  if hasattr(events, "__aiter__"):
    async for event in events:
      yield event
  else:
    for event in events:
      yield event


async def async_tick_frames(events, *, presorted=False):
  if not hasattr(events, "__aiter__"):
    for frame in tick_frames(events, presorted=presorted):
      yield frame
    return
  if not presorted:
    raise ValueError("BACKTEST_ASYNC_ORDER_REQUIRED")
  validator = _SequenceValidator(True)
  frame, at = [], None
  async for event in events:
    validator.accept(event)
    if frame and event.decision_time != at:
      yield at, tuple(frame)
      frame = []
    at = event.decision_time
    frame.append(event)
  if frame:
    yield at, tuple(frame)
