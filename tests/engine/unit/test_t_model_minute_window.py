"""Accepted-stream minute closure with real shared feature calculations."""

from dataclasses import replace

import pytest
from quantx_application.t_trade_v3.model_minute_window import TModelMinuteWindow

from tests.research.test_t_assistant_model_data import START, bar, ticks


def window(**changes):
  return TModelMinuteWindow(**(dict(instrument_code="600000.SH", interval_start_ms=START,
    stream_id="stream-1", continuity_generation="generation-1",
    capability_manifest_version="quotes-v1", market_context_as_of_ms=START,
    sector_context_as_of_ms=START, max_gap_ms=15000, max_ticks=10) | changes))


def close(value):
  return value.seal(stream_id="stream-1", continuity_generation="generation-1", watermark_ms=START + 60000, available_at_ms=START + 60001)


def test_real_complete_minute_matches_research_and_seals_once():
  value = window()
  for tick in ticks():
    value.accept_tick(tick)
  assert value.buffered_tick_count == 5
  with pytest.raises(ValueError, match="NOT_COMPLETE"):
    value.seal(stream_id="stream-1", continuity_generation="generation-1", watermark_ms=START + 59999, available_at_ms=START + 60001)
  assert value.buffered_tick_count == 5
  outcome = close(value)
  assert outcome.status == "COMPLETE" and outcome.feature_bar == bar()
  assert value.buffered_tick_count == 0
  assert value.seal(stream_id="stream-1", continuity_generation="generation-1", watermark_ms=START + 120000, available_at_ms=START + 120001) is outcome
  with pytest.raises(ValueError, match="ALREADY_SEALED"):
    value.accept_tick(ticks()[0])


@pytest.mark.parametrize("fault", ["empty", "gap", "duplicate", "sequence", "generation", "stream", "symbol", "overflow", "receipt", "capability", "boundary"])
def test_invalid_minute_has_explicit_unavailable_outcome(fault):
  value = window(max_ticks=2 if fault == "overflow" else 10)
  source = list(ticks())
  if fault == "empty":
    source = []
  elif fault == "gap":
    source = [source[0], replace(source[-1], accepted_sequence=2)]
  elif fault == "duplicate":
    source.insert(1, source[0])
  elif fault == "sequence":
    source[1] = replace(source[1], accepted_sequence=99)
  elif fault == "generation":
    source[1] = replace(source[1], sample=replace(source[1].sample, continuity_generation="new-generation"))
  elif fault == "stream":
    source[1] = replace(source[1], stream_id="new-stream")
  elif fault == "symbol":
    source[1] = replace(source[1], sample=replace(source[1].sample, instrument_code="000001.SZ"))
  elif fault == "receipt":
    source[-1] = replace(source[-1], received_at_ms=START + 60002)
  elif fault == "capability":
    source[1] = replace(source[1], sample=replace(source[1].sample, bid_price=None))
  elif fault == "boundary":
    source[-1] = replace(source[-1], sample=replace(source[-1].sample, source_time_ms=START + 60000))
  for tick in source:
    value.accept_tick(tick)
    assert value.buffered_tick_count <= (2 if fault == "overflow" else 10)
  outcome = close(value)
  assert outcome.status == "UNAVAILABLE" and outcome.feature_bar is None
  assert outcome.reason.startswith("T_MODEL_") and value.buffered_tick_count == 0
  assert close(value) is outcome
  if fault == "overflow":
    assert outcome.reason == "T_MODEL_MINUTE_BUFFER_OVERFLOW"


@pytest.mark.parametrize("change", [{"max_ticks": True}, {"max_ticks": 1}, {"max_gap_ms": 60000},
  {"interval_start_ms": START + 1}, {"market_context_as_of_ms": START + 1}, {"stream_id": ""}])
def test_invalid_window_configuration_fails_before_acceptance(change):
  with pytest.raises(ValueError, match="CONFIG_INVALID"):
    window(**change)


@pytest.mark.parametrize("changed", [{"stream_id": "other"}, {"continuity_generation": "other"}])
def test_watermark_cannot_close_another_generation(changed):
  value = window()
  for tick in ticks():
    value.accept_tick(tick)
  outcome = value.seal(**(dict(stream_id="stream-1", continuity_generation="generation-1",
    watermark_ms=START + 60000, available_at_ms=START + 60001) | changed))
  assert outcome.status == "UNAVAILABLE" and outcome.reason == "T_MODEL_MINUTE_WATERMARK_IDENTITY_INVALID"
  assert close(value) is outcome
  assert value.buffered_tick_count == 0
