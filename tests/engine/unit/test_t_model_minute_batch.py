"""Real multi-symbol feature closure, deterministic manifests and bounded rotation."""

from dataclasses import replace

import pytest
from quantx_application.t_trade_v3.model_minute_batch import (
  TModelMinuteBatchRuntime,
  TModelMinuteContext,
)

from tests.research.test_t_assistant_model_data import START, ticks

CODES = ("600000.SH", "000001.SZ", "000002.SZ")


def config(start=START):
  return dict(interval_start_ms=start, stream_id="stream-1", continuity_generation="generation-1",
    contexts={code: TModelMinuteContext("quotes-v1", start, start) for code in CODES},
    max_gap_ms=15000, max_ticks=10)


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
    new["contexts"][CODES[0]] = replace(new["contexts"][CODES[0]], market_context_as_of_ms=new["interval_start_ms"] + 1)
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


def test_contexts_are_per_symbol_frozen_and_recorded_even_without_ticks():
  settings = config()
  settings["contexts"][CODES[1]] = TModelMinuteContext("other-capability", START - 1000, START - 5000)
  runtime = TModelMinuteBatchRuntime(instrument_codes=CODES, **settings)
  original = dict(settings["contexts"])
  settings["contexts"][CODES[1]] = TModelMinuteContext("later", START, START)
  for code in CODES[:2]:
    feed(runtime, code)
  batch = close(runtime)
  assert dict(batch.contexts) == original
  bars = {bar.instrument_code: bar for bar in batch.complete_bars}
  assert bars[CODES[1]].sector_context_as_of_ms == START - 5000
  assert bars[CODES[1]].capability_manifest_version == "other-capability"
  assert bars[CODES[0]].sector_context_as_of_ms == START
  assert CODES[2] in dict(batch.contexts) and CODES[2] not in bars


@pytest.mark.parametrize("damage", ["missing", "extra", "future", "untyped"])
def test_bad_context_rotation_keeps_sealed_batch(damage):
  runtime = TModelMinuteBatchRuntime(instrument_codes=CODES, **config())
  prior = close(runtime)
  settings = config(START + 60000)
  contexts = settings["contexts"]
  if damage == "missing":
    contexts.pop(CODES[0])
  elif damage == "extra":
    contexts["999999.SH"] = contexts[CODES[0]]
  elif damage == "future":
    contexts[CODES[0]] = replace(contexts[CODES[0]], sector_context_as_of_ms=START + 60001)
  else:
    contexts[CODES[0]] = {"market_context_as_of_ms": START}
  with pytest.raises(ValueError, match="T_MODEL_MINUTE_"):
    runtime.advance(instrument_codes=CODES, **settings)
  assert runtime.latest is prior


def test_empty_batch_hash_binds_context_age():
  settings = config()
  first = close(TModelMinuteBatchRuntime(instrument_codes=CODES, **settings))
  settings["contexts"][CODES[0]] = replace(settings["contexts"][CODES[0]], sector_context_as_of_ms=START - 1)
  second = close(TModelMinuteBatchRuntime(instrument_codes=CODES, **settings))
  assert first.outcomes == second.outcomes and first.manifest_hash != second.manifest_hash



def boundaries():
  last = ticks()[-1]
  return tuple(replace(last, accepted_sequence=6, market_fence_sequence=6,
    received_at_ms=START + 60010 + i,
    sample=replace(last.sample, instrument_code=code, source_time_ms=START + 60000 + i))
    for i, code in enumerate(CODES))


def test_accepted_boundary_uses_slowest_source_and_latest_receipt():
  runtime = TModelMinuteBatchRuntime(instrument_codes=CODES, **config())
  for code in CODES[:2]:
    feed(runtime, code)
  boundary = boundaries()
  batch = runtime.seal_from_accepted_ticks(boundary)
  assert batch.watermark_ms == START + 60000
  assert batch.available_at_ms == START + 60012
  assert len(batch.complete_bars) == 2
  assert runtime.buffered_tick_count == 0
  assert runtime.seal_from_accepted_ticks(boundary) is batch
  assert all(bar.source_fence_range == (1, 5) for bar in batch.complete_bars)
  runtime.advance(instrument_codes=CODES, **config(START + 60000))
  for tick in boundary:
    runtime.accept_tick(tick)
  assert runtime.buffered_tick_count == len(CODES)


@pytest.mark.parametrize("damage", ["missing", "duplicate", "early", "stream", "generation", "sequence", "fence", "receipt"])
def test_bad_boundary_cannot_partially_seal_minute(damage):
  runtime = TModelMinuteBatchRuntime(instrument_codes=CODES, **config())
  for code in CODES:
    feed(runtime, code)
  boundary = list(boundaries())
  tick = boundary[-1]
  if damage == "missing":
    boundary.pop()
  elif damage == "duplicate":
    boundary[-1] = boundary[0]
  elif damage == "early":
    boundary[-1] = replace(tick, sample=replace(tick.sample, source_time_ms=START + 59999))
  elif damage == "stream":
    boundary[-1] = replace(tick, stream_id="other")
  elif damage == "generation":
    boundary[-1] = replace(tick, sample=replace(tick.sample, continuity_generation="other"))
  elif damage == "sequence":
    boundary[-1] = replace(tick, accepted_sequence=8)
  elif damage == "fence":
    boundary[-1] = replace(tick, market_fence_sequence=5)
  else:
    boundary[-1] = replace(tick, received_at_ms=START + 59000)
  with pytest.raises(ValueError, match="T_MODEL_MINUTE_BOUNDARY_"):
    runtime.seal_from_accepted_ticks(boundary)
  assert runtime.latest is None and runtime.buffered_tick_count == 15
  assert len(runtime.seal_from_accepted_ticks(boundaries()).complete_bars) == 3
