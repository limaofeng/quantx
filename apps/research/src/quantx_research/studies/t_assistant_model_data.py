"""T-only observation labels and purged chronological development coordinates.

No data fetching, training, candidate selection or FINAL evaluation is performed.
All cost/horizon/coverage choices are explicit versioned inputs awaiting approval.
"""

from dataclasses import asdict, dataclass
from math import isfinite

from quantx_application.t_trade_v3.model_features import (
  TModelFeatureBar,
  market_session,
)
from quantx_domain.trading.t_assistant_execution import stable_manifest_hash


@dataclass(frozen=True)
class TLabelSpec:
  version: str
  horizon_ms: int
  max_gap_ms: int
  volume: int
  target_net_bps: float
  stop_net_bps: float
  slippage_bps: float
  commission_rate: float
  minimum_commission: float
  stamp_tax_rate: float
  transfer_fee_rate: float

  def __post_init__(self):
    if (
      not self.version
      or any(
        type(v) is not int or v <= 0
        for v in (self.horizon_ms, self.max_gap_ms, self.volume)
      )
      or self.volume % 100
      or self.max_gap_ms > self.horizon_ms
    ):
      raise ValueError("T_LABEL_SPEC_INVALID")
    values = (
      self.target_net_bps,
      self.stop_net_bps,
      self.slippage_bps,
      self.commission_rate,
      self.minimum_commission,
      self.stamp_tax_rate,
      self.transfer_fee_rate,
    )
    if any(type(v) not in (int, float) or not isfinite(v) or v < 0 for v in values):
      raise ValueError("T_LABEL_COST_INVALID")
    if (
      min(self.target_net_bps, self.stop_net_bps) <= 0
      or max(self.slippage_bps, self.stop_net_bps) >= 10_000
    ):
      raise ValueError("T_LABEL_BARRIER_INVALID")

  @property
  def fingerprint(self):
    return stable_manifest_hash(asdict(self))


@dataclass(frozen=True)
class TLabelTick:
  instrument_code: str
  source_ms: int
  ordinal: int
  generation: str
  bid: float
  ask: float
  executable_buy: bool
  healthy: bool


@dataclass(frozen=True)
class TObservationLabel:
  observation_id: str
  feature_bar_id: str
  instrument_code: str
  anchor_ms: int
  label_end_ms: int
  spec_hash: str
  label: str
  reason: str
  path_hash: str
  primary_horizon_ms: int


def label_observation(
  bar: TModelFeatureBar,
  ticks: tuple[TLabelTick, ...],
  spec: TLabelSpec,
  *,
  path_watermark_ms: int,
) -> TObservationLabel:
  anchor, deadline = bar.available_at_ms, bar.available_at_ms + spec.horizon_ms
  if bar.status != "COMPLETE" or anchor < bar.interval_end_ms:
    raise ValueError("T_LABEL_COMPLETE_ANCHOR_REQUIRED")
  if type(path_watermark_ms) is not int or path_watermark_ms < anchor:
    raise ValueError("T_LABEL_PATH_WATERMARK_INVALID")
  path = tuple(tick for tick in ticks if anchor < tick.source_ms <= deadline)
  path_hash = stable_manifest_hash(
    {"ticks": [asdict(t) for t in path], "watermark": path_watermark_ms}
  )
  observation_id = stable_manifest_hash(
    {"bar": bar.feature_bar_id, "spec": spec.fingerprint}
  )

  def result(label, reason, end=deadline):
    return TObservationLabel(
      observation_id,
      bar.feature_bar_id,
      bar.instrument_code,
      anchor,
      end,
      spec.fingerprint,
      label,
      reason,
      path_hash,
      spec.horizon_ms,
    )

  try:
    if (
      market_session(anchor) != bar.market_session
      or market_session(deadline) != bar.market_session
    ):
      return result("UNAVAILABLE", "SESSION_BOUNDARY")
  except ValueError:
    return result("UNAVAILABLE", "SESSION_BOUNDARY")
  previous_ms, previous_ordinal = anchor, -1
  entry_cost = None
  for tick in path:
    if (
      tick.instrument_code != bar.instrument_code
      or tick.generation != bar.continuity_generation
      or tick.healthy is not True
      or type(tick.executable_buy) is not bool
      or type(tick.source_ms) is not int
      or type(tick.ordinal) is not int
      or tick.ordinal < 0
      or (tick.source_ms, tick.ordinal) <= (previous_ms, previous_ordinal)
      or tick.source_ms > path_watermark_ms
      or tick.source_ms - previous_ms > spec.max_gap_ms
      or any(
        type(v) not in (int, float) or not isfinite(v) or v <= 0
        for v in (tick.bid, tick.ask)
      )
      or tick.ask < tick.bid
    ):
      return result("UNAVAILABLE", "PATH_UNRELIABLE")
    previous_ms, previous_ordinal = tick.source_ms, tick.ordinal
    if entry_cost is None:
      if not tick.executable_buy:
        continue
      gross = tick.ask * (1 + spec.slippage_bps / 10_000) * spec.volume
      entry_cost = (
        gross
        + max(spec.minimum_commission, gross * spec.commission_rate)
        + gross * spec.transfer_fee_rate
      )
      # An entry and an exit cannot both be invented from the same event.
      continue
    gross = tick.bid * (1 - spec.slippage_bps / 10_000) * spec.volume
    proceeds = (
      gross
      - max(spec.minimum_commission, gross * spec.commission_rate)
      - gross * (spec.stamp_tax_rate + spec.transfer_fee_rate)
    )
    net_bps = (proceeds / entry_cost - 1) * 10_000
    if net_bps >= spec.target_net_bps:
      return result("TARGET_FIRST", "NET_TARGET_TOUCHED", tick.source_ms)
    if net_bps <= -spec.stop_net_bps:
      return result("STOP_FIRST", "NET_STOP_TOUCHED", tick.source_ms)
  if entry_cost is None:
    return result("UNAVAILABLE", "NO_EXECUTABLE_ENTRY")
  if path_watermark_ms < deadline or deadline - previous_ms > spec.max_gap_ms:
    return result("UNAVAILABLE", "INCOMPLETE_HORIZON")
  return result("NO_TOUCH", "HORIZON_COMPLETE")


@dataclass(frozen=True)
class TWalkForwardWindow:
  train_start_ms: int
  validation_start_ms: int
  validation_end_ms: int


@dataclass(frozen=True)
class TWalkForwardSplit:
  train_ids: tuple[str, ...]
  validation_ids: tuple[str, ...]
  coordinate_hash: str


def purged_walk_forward(
  labels: tuple[TObservationLabel, ...],
  windows: tuple[TWalkForwardWindow, ...],
  *,
  embargo_ms: int,
  max_horizon_ms: int,
  final_start_ms: int,
):
  if (
    type(embargo_ms) is not int
    or type(max_horizon_ms) is not int
    or max_horizon_ms <= 0
    or embargo_ms < max_horizon_ms
    or not windows
  ):
    raise ValueError("T_MODEL_EMBARGO_INVALID")
  if (
    len({x.observation_id for x in labels}) != len(labels)
    or len({x.spec_hash for x in labels}) != 1
  ):
    raise ValueError("T_MODEL_OBSERVATION_IDENTITY_INVALID")
  for item in labels:
    if (
      type(item.primary_horizon_ms) is not int
      or not 0 < item.primary_horizon_ms <= max_horizon_ms
      or item.label_end_ms < item.anchor_ms
      or item.label_end_ms > item.anchor_ms + item.primary_horizon_ms
    ):
      raise ValueError("T_MODEL_LABEL_INTERVAL_INVALID")
  ordered = sorted(labels, key=lambda x: (x.anchor_ms, x.observation_id))
  splits = []
  previous_end = -1
  for window in windows:
    if (
      not 0
      <= window.train_start_ms
      < window.validation_start_ms
      < window.validation_end_ms
      <= final_start_ms
      or window.validation_start_ms < previous_end
    ):
      raise ValueError("T_MODEL_WALK_FORWARD_WINDOW_INVALID")
    eligible = [
      x for x in ordered if x.label in {"TARGET_FIRST", "STOP_FIRST", "NO_TOUCH"}
    ]
    train = tuple(
      x.observation_id
      for x in eligible
      if window.train_start_ms <= x.anchor_ms < window.validation_start_ms
      and x.label_end_ms < window.validation_start_ms - embargo_ms
    )
    validation = tuple(
      x.observation_id
      for x in eligible
      if window.validation_start_ms <= x.anchor_ms < window.validation_end_ms
      and x.label_end_ms < min(window.validation_end_ms, final_start_ms)
    )
    if not train or not validation:
      raise ValueError("T_MODEL_EMPTY_PURGED_FOLD")
    selected_ids = set(train + validation)
    coordinate = {
      "window": asdict(window),
      "train_ids": train,
      "validation_ids": validation,
      "embargo_ms": embargo_ms,
      "max_horizon_ms": max_horizon_ms,
      "final_start_ms": final_start_ms,
      "observations": [asdict(x) for x in ordered if x.observation_id in selected_ids],
    }
    splits.append(
      TWalkForwardSplit(train, validation, stable_manifest_hash(coordinate))
    )
    previous_end = window.validation_end_ms
  return tuple(splits)
