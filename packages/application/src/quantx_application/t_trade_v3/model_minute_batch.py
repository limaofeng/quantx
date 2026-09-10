"""Bounded Universe-wide minute assembly; no scoring, orders or wall-clock seal."""

from dataclasses import asdict, dataclass

from quantx_domain.trading.t_assistant_execution import stable_manifest_hash

from .model_minute_window import TModelMinuteOutcome, TModelMinuteWindow


@dataclass(frozen=True)
class TModelMinuteBatch:
  interval_start_ms: int
  interval_end_ms: int
  stream_id: str
  continuity_generation: str
  watermark_ms: int
  available_at_ms: int
  watermark_stream_id: str
  watermark_continuity_generation: str
  universe: tuple[str, ...]
  outcomes: tuple[TModelMinuteOutcome, ...]
  manifest_hash: str

  @property
  def complete_bars(self):
    return tuple(item.feature_bar for item in self.outcomes if item.feature_bar is not None)


class TModelMinuteBatchRuntime:
  def __init__(self, *, instrument_codes, **window_config):
    self._windows = self._create_windows(instrument_codes, window_config)
    self._config = dict(window_config)
    self.latest = None

  @staticmethod
  def _create_windows(instrument_codes, config):
    codes = tuple(instrument_codes)
    if (
      not codes or any(not isinstance(code, str) or not code.strip() or code != code.strip().upper() for code in codes)
      or len(set(codes)) != len(codes)
    ):
      raise ValueError("T_MODEL_MINUTE_UNIVERSE_INVALID")
    return {code: TModelMinuteWindow(instrument_code=code, **config) for code in sorted(codes)}

  @property
  def buffered_tick_count(self):
    return sum(window.buffered_tick_count for window in self._windows.values())

  def accept_tick(self, tick):
    window = self._windows.get(tick.instrument_code)
    if window is None:
      raise ValueError("T_MODEL_MINUTE_SYMBOL_NOT_PLANNED")
    window.accept_tick(tick)

  def seal(self, *, watermark_ms, available_at_ms, stream_id, continuity_generation):
    if self.latest is not None:
      if (stream_id, continuity_generation) != (self.latest.stream_id, self.latest.continuity_generation):
        raise ValueError("T_MODEL_MINUTE_WATERMARK_IDENTITY_INVALID")
      return self.latest
    # Check clocks before touching any symbol: early seals cannot partially close
    # a Universe, including when a malformed watermark also changes identity.
    end = self._config["interval_start_ms"] + 60000
    if (
      type(watermark_ms) is not int or type(available_at_ms) is not int
      or watermark_ms < end or available_at_ms < watermark_ms
    ):
      raise ValueError("T_MODEL_MINUTE_NOT_COMPLETE")
    outcomes = tuple(window.seal(watermark_ms=watermark_ms, available_at_ms=available_at_ms,
      stream_id=stream_id, continuity_generation=continuity_generation) for window in self._windows.values())
    identity = dict(interval_start_ms=self._config["interval_start_ms"], interval_end_ms=end,
      stream_id=self._config["stream_id"], continuity_generation=self._config["continuity_generation"],
      watermark_ms=watermark_ms, available_at_ms=available_at_ms,
      watermark_stream_id=stream_id, watermark_continuity_generation=continuity_generation,
      universe=tuple(self._windows), outcomes=outcomes)
    digest = stable_manifest_hash(identity | {"outcomes": [asdict(item) for item in outcomes]})
    self.latest = TModelMinuteBatch(**identity, manifest_hash=digest)
    return self.latest

  def advance(self, *, instrument_codes, **window_config):
    """Rotate only after a sealed contiguous minute. Gaps/reset need a new runtime.

    The caller supplies fresh PIT context and explicit Universe for the next
    minute. No future context, missing minutes or stream resets are synthesized.
    """
    if self.latest is None:
      raise ValueError("T_MODEL_MINUTE_ADVANCE_BEFORE_SEAL")
    if (
      (self.latest.watermark_stream_id, self.latest.watermark_continuity_generation)
      != (self.latest.stream_id, self.latest.continuity_generation)
      or window_config.get("interval_start_ms") != self.latest.interval_end_ms
      or window_config.get("stream_id") != self.latest.stream_id
      or window_config.get("continuity_generation") != self.latest.continuity_generation
    ):
      raise ValueError("T_MODEL_MINUTE_RESET_REQUIRED")
    windows = self._create_windows(instrument_codes, window_config)
    self._windows, self._config, self.latest = windows, dict(window_config), None
