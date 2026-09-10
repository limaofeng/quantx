"""Real multi-symbol feature closure, deterministic manifests and bounded rotation."""

from dataclasses import replace

import pytest
from quantx_application.t_trade_v3.model_minute_batch import TModelMinuteBatchRuntime

from tests.research.test_t_assistant_model_data import START, ticks

CODES = ("600000.SH", "000001.SZ", "000002.SZ")


def config(start=START):
  return dict(interval_start_ms=start, stream_id="stream-1", continuity_generation="generation-1",
    capability_manifest_version="quotes-v1", market_context_as_of_ms=start,
    sector_context_as_of_ms=start, max_gap_ms=15000, max_ticks=10)


def close(runtime, at=START):
  return runtime.seal(watermark_ms=at + 60000, available_at_ms=at + 60001,
    stream_id="stream-1", continuity_generation="generation-1")


def feed(runtime, code, offset=0):
  for tick in ticks():
    runtime.accept_tick(replace(tick, received_at_ms=tick.received_at_ms + offset,
      sample=replace(tick.sample, instrument_code=code, source_time_ms=tick.sample.source_time_ms + offset)))


def test_every_planned_symbol_has_outcome_and_manifest_is_order_independent():
  left = TModelMinuteBatchRuntime(instrument_codes=CODES, **config())
  right = TModelMinuteBatchRuntime(instrument_codes=tuple(reversed(CODES)), **config())
  for runtime in (left, right):
    feed(runtime, CODES[0])
    feed(runtime, CODES[1])
  first, second = close(left), close(right)
  assert first == second
  assert first.universe == tuple(sorted(CODES))
  assert len(first.outcomes) == 3 and len(first.complete_bars) == 2
  assert {item.instrument_code: item.status for item in first.outcomes} == {
    CODES[0]: "COMPLETE", CODES[1]: "COMPLETE", CODES[2]: "UNAVAILABLE",
  }
  assert left.buffered_tick_count == 0 and close(left) is first


def test_rotation_does_not_reuse_prior_complete_symbol_and_preserves_old_value():
  runtime = TModelMinuteBatchRuntime(instrument_codes=CODES, **config())
  for code in CODES:
    feed(runtime, code)
  first = close(runtime)
  assert len(first.complete_bars) == 3
  runtime.advance(instrument_codes=CODES, **config(START + 60000))
  assert runtime.latest is None and runtime.buffered_tick_count == 0
  feed(runtime, CODES[0], 60000)
  second = close(runtime, START + 60000)
  assert len(second.complete_bars) == 1 and len(second.outcomes) == 3
  assert second.manifest_hash != first.manifest_hash and len(first.complete_bars) == 3


@pytest.mark.parametrize("fault", ["early", "gap", "stream", "generation", "context"])
def test_rotation_rejects_invalid_continuity_without_replacing_state(fault):
  runtime = TModelMinuteBatchRuntime(instrument_codes=CODES, **config())
  feed(runtime, CODES[0])
  if fault != "early":
    close(runtime)
  before = runtime.latest
  new = config(START + 60000)
  if fault == "gap":
    new["interval_start_ms"] += 60000
  elif fault == "stream":
    new["stream_id"] = "other"
  elif fault == "generation":
    new["continuity_generation"] = "other"
  elif fault == "context":
    new["market_context_as_of_ms"] += 1
  with pytest.raises(ValueError):
    runtime.advance(instrument_codes=CODES, **new)
  assert runtime.latest is before


def test_early_seal_is_atomic_and_changed_watermark_invalidates_all_symbols():
  runtime = TModelMinuteBatchRuntime(instrument_codes=CODES, **config())
  for code in CODES:
    feed(runtime, code)
  with pytest.raises(ValueError, match="NOT_COMPLETE"):
    runtime.seal(watermark_ms=START + 59999, available_at_ms=START + 60001,
      stream_id="other", continuity_generation="other")
  assert runtime.buffered_tick_count == 15 and runtime.latest is None
  batch = runtime.seal(watermark_ms=START + 60000, available_at_ms=START + 60001,
    stream_id="other", continuity_generation="other")
  assert all(item.status == "UNAVAILABLE" for item in batch.outcomes)
  assert runtime.buffered_tick_count == 0
  assert batch.stream_id == "stream-1" and batch.watermark_stream_id == "other"
  with pytest.raises(ValueError, match="RESET_REQUIRED"):
    runtime.advance(instrument_codes=CODES, **config(START + 60000))


@pytest.mark.parametrize("codes", [(), ("600000.SH", "600000.SH"), ("bad ",), (None,)])
def test_invalid_universe(codes):
  with pytest.raises(ValueError, match="UNIVERSE_INVALID"):
    TModelMinuteBatchRuntime(instrument_codes=codes, **config())


def test_empty_batch_manifest_binds_first_publication_time():
  left = TModelMinuteBatchRuntime(instrument_codes=CODES, **config())
  right = TModelMinuteBatchRuntime(instrument_codes=CODES, **config())
  first = close(left)
  later = right.seal(watermark_ms=START + 60000, available_at_ms=START + 60002,
    stream_id="stream-1", continuity_generation="generation-1")
  assert first.outcomes == later.outcomes
  assert first.manifest_hash != later.manifest_hash
  assert first.available_at_ms == START + 60001
  assert close(left) is first
