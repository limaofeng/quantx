"""Synthetic engineering fixtures only; no formal dataset/model evaluation."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from quantx_application.t_trade_v3.model_features import build_complete_feature_bar
from quantx_domain.trading.t_assistant_market_state import AcceptedTMarketTick
from quantx_domain.trading.t_trade_opportunity_engine import OpportunitySample
from quantx_research.studies.t_assistant_model_data import (
  TLabelSpec,
  TLabelTick,
  TObservationLabel,
  TWalkForwardWindow,
  label_observation,
  purged_walk_forward,
)

START = int(datetime(2026, 9, 3, 1, 30, tzinfo=UTC).timestamp() * 1000)


def ticks():
  return tuple(
    AcceptedTMarketTick(
      "stream-1",
      i + 1,
      START + offset + 1,
      OpportunitySample(
        "600000.SH",
        "2026-09-03",
        START + offset,
        i,
        10.0 + i / 100,
        "generation-1",
        bid_price=9.99 + i / 100,
        ask_price=10.01 + i / 100,
        bid_volume=100,
        ask_volume=200,
        cumulative_amount=1000 + i * 100,
      ),
    )
    for i, offset in enumerate((0, 15000, 30000, 45000, 59000))
  )


def bar(source=None, **kwargs):
  args = dict(
    interval_start_ms=START,
    watermark_ms=START + 60000,
    available_at_ms=START + 60001,
    capability_manifest_version="quotes-v1",
    market_context_as_of_ms=START,
    sector_context_as_of_ms=START,
    max_gap_ms=15000,
  )
  args.update(kwargs)
  return build_complete_feature_bar(ticks() if source is None else source, **args)


def spec():
  return TLabelSpec("fixture-only", 3000, 1000, 100, 20, 20, 0, 0, 0, 0, 0)


def path(prices):
  anchor = bar().available_at_ms
  return tuple(
    TLabelTick(
      "600000.SH", anchor + (i + 1) * 1000, i, "generation-1", bid, ask, True, True
    )
    for i, (bid, ask) in enumerate(prices)
  )


def test_complete_bar_replays_identically_and_has_no_identity_features():
  first = bar()
  assert first == bar()
  assert first.status == "COMPLETE" and first.feature_coverage == 1.0
  assert len(first.feature_values) == 6
  assert first.interval_end_ms < first.available_at_ms
  assert bar(available_at_ms=START + 60002).feature_bar_id != first.feature_bar_id


@pytest.mark.parametrize(
  "damage",
  [
    "forming",
    "future_context",
    "generation",
    "gap",
    "missing_depth",
    "late",
    "out_of_order",
  ],
)
def test_unqualified_minutes_cannot_form_observation_anchors(damage):
  source, kwargs = list(ticks()), {}
  if damage == "forming":
    kwargs["watermark_ms"] = START + 59999
  if damage == "future_context":
    kwargs["market_context_as_of_ms"] = START + 1
  if damage == "generation":
    source[2] = replace(
      source[2], sample=replace(source[2].sample, continuity_generation="new")
    )
  if damage == "gap":
    source.pop(2)
  if damage == "missing_depth":
    source[2] = replace(source[2], sample=replace(source[2].sample, bid_volume=None))
  if damage == "late":
    source[2] = replace(source[2], received_at_ms=START + 60002)
  if damage == "out_of_order":
    source.reverse()
  with pytest.raises(ValueError):
    bar(tuple(source), **kwargs)


@pytest.mark.parametrize(
  "prices,expected",
  [
    ([(10, 10), (10.1, 10.1), (9, 9)], "TARGET_FIRST"),
    ([(10, 10), (9.9, 9.9), (11, 11)], "STOP_FIRST"),
    ([(10, 10), (10, 10), (10, 10)], "NO_TOUCH"),
  ],
)
def test_ordered_executable_path_labels(prices, expected):
  feature = bar()
  result = label_observation(
    feature, path(prices), spec(), path_watermark_ms=feature.available_at_ms + 3000
  )
  assert result.label == expected
  assert result.anchor_ms == feature.available_at_ms


def test_costs_can_reverse_apparent_profitable_price_move():
  feature = bar()
  raw = path([(10, 10), (10.02, 10.02), (10.02, 10.02)])
  result = label_observation(
    feature,
    raw,
    replace(spec(), minimum_commission=5),
    path_watermark_ms=feature.available_at_ms + 3000,
  )
  assert result.label == "STOP_FIRST"


@pytest.mark.parametrize(
  "damage",
  ["missing", "generation", "no_entry", "unordered", "incomplete", "cross_session"],
)
def test_unreliable_or_non_executable_paths_are_unavailable(damage):
  feature, points = bar(), list(path([(10, 10), (10, 10), (10, 10)]))
  watermark = feature.available_at_ms + 3000
  if damage == "missing":
    points[1] = replace(points[1], healthy=False)
  if damage == "generation":
    points[1] = replace(points[1], generation="new")
  if damage == "no_entry":
    points = [replace(x, executable_buy=False) for x in points]
  if damage == "unordered":
    points.reverse()
  if damage == "incomplete":
    watermark -= 1
  if damage == "cross_session":
    feature = replace(feature, available_at_ms=START + 119 * 60000)
  result = label_observation(
    feature,
    tuple(points),
    spec(),
    path_watermark_ms=max(watermark, feature.available_at_ms),
  )
  assert result.label == "UNAVAILABLE"


def observations():
  return tuple(
    TObservationLabel(
      str(i),
      f"bar-{i}",
      "600000.SH",
      at,
      at + 10,
      "spec",
      "NO_TOUCH",
      "fixture",
      "path",
      10,
    )
    for i, at in enumerate((10, 50, 80, 90, 110, 130, 190, 210))
  )


def test_development_split_purges_overlap_and_never_uses_final():
  splits = purged_walk_forward(
    observations(),
    (TWalkForwardWindow(0, 100, 150),),
    embargo_ms=10,
    max_horizon_ms=10,
    final_start_ms=200,
  )
  assert splits[0].train_ids == ("0", "1")
  assert splits[0].validation_ids == ("4", "5")
  assert len(splits[0].coordinate_hash) == 64
  changed = tuple(
    replace(x, path_hash="different") if x.observation_id == "4" else x
    for x in observations()
  )
  assert (
    purged_walk_forward(
      changed,
      (TWalkForwardWindow(0, 100, 150),),
      embargo_ms=10,
      max_horizon_ms=10,
      final_start_ms=200,
    )[0].coordinate_hash
    != splits[0].coordinate_hash
  )


@pytest.mark.parametrize(
  "damage", ["embargo", "duplicate", "mixed_spec", "final_overlap"]
)
def test_invalid_training_coordinates_rejected(damage):
  data, embargo, end = observations(), 10, 150
  if damage == "embargo":
    embargo = 9
  if damage == "duplicate":
    data += (data[0],)
  if damage == "mixed_spec":
    data = (replace(data[0], spec_hash="other"), *data[1:])
  if damage == "final_overlap":
    end = 201
  with pytest.raises(ValueError):
    purged_walk_forward(
      data,
      (TWalkForwardWindow(0, 100, end),),
      embargo_ms=embargo,
      max_horizon_ms=10,
      final_start_ms=200,
    )


def test_early_touch_does_not_shorten_required_embargo():
  data = tuple(replace(x, primary_horizon_ms=100) for x in observations())
  with pytest.raises(ValueError, match="LABEL_INTERVAL"):
    purged_walk_forward(
      data,
      (TWalkForwardWindow(0, 100, 150),),
      embargo_ms=10,
      max_horizon_ms=10,
      final_start_ms=200,
    )
