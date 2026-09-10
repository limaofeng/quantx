"""Bounded accepted-Tick minute window shared by Engine and replay.

A wall clock cannot close a window: the caller must supply an accepted-stream
watermark. Invalid windows produce an explicit unavailable outcome and cannot
be repaired after sealing.
"""

from dataclasses import dataclass

from quantx_domain.trading.t_assistant_market_state import AcceptedTMarketTick

from .model_features import TModelFeatureBar, build_complete_feature_bar, market_session


@dataclass(frozen=True)
class TModelMinuteOutcome:
  instrument_code: str
  interval_start_ms: int
  interval_end_ms: int
  status: str
  reason: str
  feature_bar: TModelFeatureBar | None


class TModelMinuteWindow:
  def __init__(self, *, instrument_code, interval_start_ms, stream_id, continuity_generation,
    capability_manifest_version, market_context_as_of_ms, sector_context_as_of_ms,
    max_gap_ms, max_ticks):
    if (
      any(not isinstance(value, str) or not value for value in (
        instrument_code, stream_id, continuity_generation, capability_manifest_version,
      ))
      or type(interval_start_ms) is not int or interval_start_ms < 0 or interval_start_ms % 60000
      or type(max_ticks) is not int or max_ticks < 2
      or type(max_gap_ms) is not int or not 0 < max_gap_ms < 60000
      or any(type(value) is not int or not 0 <= value <= interval_start_ms
        for value in (market_context_as_of_ms, sector_context_as_of_ms))
    ):
      raise ValueError("T_MODEL_MINUTE_CONFIG_INVALID")
    market_session(interval_start_ms)
    self.instrument_code = instrument_code
    self.interval_start_ms = interval_start_ms
    self.interval_end_ms = interval_start_ms + 60000
    self._identity = (stream_id, continuity_generation)
    self._max_ticks = max_ticks
    self._feature_args = dict(interval_start_ms=interval_start_ms,
      capability_manifest_version=capability_manifest_version,
      market_context_as_of_ms=market_context_as_of_ms,
      sector_context_as_of_ms=sector_context_as_of_ms, max_gap_ms=max_gap_ms)
    self._ticks = []
    self._reason = None
    self._outcome = None

  @property
  def buffered_tick_count(self):
    return len(self._ticks)

  def accept_tick(self, tick: AcceptedTMarketTick):
    if self._outcome is not None:
      raise ValueError("T_MODEL_MINUTE_ALREADY_SEALED")
    if self._reason is not None:
      return
    reason = None
    if (
      tick.instrument_code != self.instrument_code
      or (tick.stream_id, tick.sample.continuity_generation) != self._identity
      or tick.discontinuity_reason is not None
      or not self.interval_start_ms <= tick.sample.source_time_ms < self.interval_end_ms
    ):
      reason = "T_MODEL_FEATURE_CONTINUITY_INVALID"
    elif len(self._ticks) >= self._max_ticks:
      reason = "T_MODEL_MINUTE_BUFFER_OVERFLOW"
    elif self._ticks:
      previous = self._ticks[-1]
      if (
        tick.accepted_sequence != previous.accepted_sequence + 1
        or tick.market_fence_sequence <= previous.market_fence_sequence
        or (tick.sample.source_time_ms, tick.sample.tick_ordinal)
        <= (previous.sample.source_time_ms, previous.sample.tick_ordinal)
        or tick.received_at_ms < previous.received_at_ms
      ):
        reason = "T_MODEL_FEATURE_CONTINUITY_INVALID"
    if reason:
      self._reason = reason
      self._ticks.clear()
    else:
      self._ticks.append(tick)

  def seal(self, *, watermark_ms, available_at_ms, stream_id, continuity_generation):
    if (stream_id, continuity_generation) != self._identity:
      if self._outcome is not None:
        raise ValueError("T_MODEL_MINUTE_WATERMARK_IDENTITY_INVALID")
      self._reason = "T_MODEL_MINUTE_WATERMARK_IDENTITY_INVALID"
      self._ticks.clear()
    if self._outcome is not None:
      return self._outcome
    if (
      type(watermark_ms) is not int or type(available_at_ms) is not int
      or watermark_ms < self.interval_end_ms or available_at_ms < watermark_ms
    ):
      raise ValueError("T_MODEL_MINUTE_NOT_COMPLETE")
    feature = None
    reason = self._reason
    if reason is None:
      try:
        feature = build_complete_feature_bar(tuple(self._ticks), watermark_ms=watermark_ms,
          available_at_ms=available_at_ms, **self._feature_args)
      except ValueError as exc:
        reason = str(exc)
    self._outcome = TModelMinuteOutcome(
      self.instrument_code, self.interval_start_ms, self.interval_end_ms,
      "COMPLETE" if feature is not None else "UNAVAILABLE", reason or "VALID", feature,
    )
    self._ticks.clear()
    return self._outcome
