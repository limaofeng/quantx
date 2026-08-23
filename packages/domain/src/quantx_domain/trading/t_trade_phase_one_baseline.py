"""Frozen phase-one AND-rule shadow baseline for the T-trade V3 engine.

The reducer is deliberately isolated from the production opportunity decision.
It consumes the same causal market facts, keeps its own bounded state, and emits
comparison evidence only.  No result from this module may authorize an order.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Mapping, Optional, Sequence

from .t_trade_opportunity_engine import OpportunityPath, OpportunitySample

PHASE_ONE_BASELINE_VERSION = "phase-one-and-v1"
_EPSILON = 1e-8


@dataclass(frozen=True)
class PhaseOneBaselinePolicy:
  """Immutable thresholds from ``持仓做T助手一期实现规格`` section 3."""

  version: str = PHASE_ONE_BASELINE_VERSION
  pullback_lookback_seconds: int = 300
  pullback_threshold_pct: float = 0.8
  rebound_threshold_pct: float = 0.2
  stabilization_seconds: int = 15
  pullback_max_spread_ticks: int = 3
  momentum_window_seconds: int = 60
  momentum_min_rise_pct: float = 0.8
  momentum_min_move_seconds: int = 15
  momentum_baseline_seconds: int = 300
  momentum_baseline_coverage_ratio: float = 0.8
  momentum_min_amount_velocity_ratio: float = 2.0
  momentum_min_vwap_premium_pct: float = 2.0
  momentum_max_vwap_premium_pct: float = 3.5
  momentum_high_tolerance_ticks: int = 1
  momentum_max_spread_ticks: int = 10
  momentum_max_spread_pct: float = 0.3
  max_samples: int = 8192

  def __post_init__(self) -> None:
    if self.version != PHASE_ONE_BASELINE_VERSION:
      raise ValueError("一期规则影子基线版本不可变更")
    integer_fields = (
      "pullback_lookback_seconds",
      "stabilization_seconds",
      "pullback_max_spread_ticks",
      "momentum_window_seconds",
      "momentum_min_move_seconds",
      "momentum_baseline_seconds",
      "momentum_high_tolerance_ticks",
      "momentum_max_spread_ticks",
      "max_samples",
    )
    if any(int(getattr(self, name)) <= 0 for name in integer_fields):
      raise ValueError("一期规则影子基线窗口、阈值和上界必须为正数")
    numeric_fields = (
      "pullback_threshold_pct",
      "rebound_threshold_pct",
      "momentum_min_rise_pct",
      "momentum_min_amount_velocity_ratio",
      "momentum_min_vwap_premium_pct",
      "momentum_max_vwap_premium_pct",
      "momentum_max_spread_pct",
    )
    if any(
      not math.isfinite(float(getattr(self, name))) or float(getattr(self, name)) < 0
      for name in numeric_fields
    ):
      raise ValueError("一期规则影子基线百分比阈值必须有限且非负")
    if not 0 < self.momentum_baseline_coverage_ratio <= 1:
      raise ValueError("一期规则影子基线覆盖率必须位于 (0, 1]")
    if self.momentum_window_seconds > self.momentum_baseline_seconds:
      raise ValueError("一期规则动量窗口不得长于成交额基线窗口")
    if self.momentum_min_move_seconds > self.momentum_window_seconds:
      raise ValueError("一期规则最短拉升时长不得长于动量窗口")
    if self.momentum_max_vwap_premium_pct <= self.momentum_min_vwap_premium_pct:
      raise ValueError("一期规则 VWAP 溢价上限必须大于下限")

  @property
  def state_window_seconds(self) -> int:
    return max(
      self.pullback_lookback_seconds,
      self.momentum_window_seconds + self.momentum_baseline_seconds,
    )


@dataclass(frozen=True)
class PhaseOneRuleFact:
  code: str
  passed: Optional[bool]
  actual: Optional[float] = None
  threshold: Optional[float] = None
  detail: str = ""

  def to_dict(self) -> dict[str, Any]:
    return asdict(self)


@dataclass(frozen=True)
class PhaseOneBaselineEvaluation:
  baseline_version: str
  instrument_code: str
  trade_date: str
  source_time_ms: int
  tick_ordinal: int
  continuity_generation: str
  sample_count: int
  coverage_seconds: Optional[float]
  selected_path: OpportunityPath
  raw_triggered: bool
  trigger_edge: bool
  duplicate: bool
  ignored_reason: Optional[str]
  top_blocker: Optional[str]
  pullback_checks: tuple[PhaseOneRuleFact, ...]
  momentum_checks: tuple[PhaseOneRuleFact, ...]
  metrics: Mapping[str, Optional[float]]

  def to_dict(self) -> dict[str, Any]:
    return {
      "baseline_version": self.baseline_version,
      "instrument_code": self.instrument_code,
      "trade_date": self.trade_date,
      "source_time_ms": self.source_time_ms,
      "tick_ordinal": self.tick_ordinal,
      "continuity_generation": self.continuity_generation,
      "sample_count": self.sample_count,
      "coverage_seconds": self.coverage_seconds,
      "selected_path": self.selected_path.value,
      "raw_triggered": self.raw_triggered,
      "trigger_edge": self.trigger_edge,
      "duplicate": self.duplicate,
      "ignored_reason": self.ignored_reason,
      "top_blocker": self.top_blocker,
      "pullback_checks": [item.to_dict() for item in self.pullback_checks],
      "momentum_checks": [item.to_dict() for item in self.momentum_checks],
      "metrics": dict(self.metrics),
    }


@dataclass(frozen=True)
class PhaseOneBaselineState:
  instrument_code: str = ""
  trade_date: str = ""
  continuity_generation: str = ""
  samples: tuple[OpportunitySample, ...] = field(default_factory=tuple)
  pullback_active: bool = False
  momentum_active: bool = False

  def to_dict(self) -> dict[str, Any]:
    return {
      "instrument_code": self.instrument_code,
      "trade_date": self.trade_date,
      "continuity_generation": self.continuity_generation,
      "samples": [item.to_dict() for item in self.samples],
      "pullback_active": self.pullback_active,
      "momentum_active": self.momentum_active,
    }

  @classmethod
  def from_dict(cls, raw: Optional[Mapping[str, Any]]) -> "PhaseOneBaselineState":
    values = dict(raw or {})
    return cls(
      instrument_code=str(values.get("instrument_code") or ""),
      trade_date=str(values.get("trade_date") or ""),
      continuity_generation=str(values.get("continuity_generation") or ""),
      samples=tuple(
        OpportunitySample.from_dict(item)
        for item in list(values.get("samples") or [])
        if isinstance(item, Mapping)
      ),
      pullback_active=bool(values.get("pullback_active", False)),
      momentum_active=bool(values.get("momentum_active", False)),
    )


@dataclass(frozen=True)
class PhaseOneBaselineReduction:
  state: PhaseOneBaselineState
  evaluation: PhaseOneBaselineEvaluation


def reduce_phase_one_baseline(
  previous: Optional[PhaseOneBaselineState],
  sample: OpportunitySample,
  *,
  continuous_session: bool,
  quote_stale: bool = False,
  policy: Optional[PhaseOneBaselinePolicy] = None,
) -> PhaseOneBaselineReduction:
  """Advance the frozen baseline without affecting the V3 decision state."""

  config = policy or PhaseOneBaselinePolicy()
  state = previous or PhaseOneBaselineState()
  reset_reason: Optional[str] = None
  if state.instrument_code and state.instrument_code != sample.instrument_code:
    raise ValueError("一期规则影子基线不可跨标的复用状态")
  if state.trade_date and state.trade_date != sample.trade_date:
    state = PhaseOneBaselineState()
    reset_reason = "TRADE_DATE_CHANGED"
  elif (
    state.continuity_generation
    and state.continuity_generation != sample.continuity_generation
  ):
    state = PhaseOneBaselineState()
    reset_reason = "CONTINUITY_CHANGED"

  if not _valid_sample(sample):
    evaluation = _evaluate(
      state.samples,
      sample,
      config,
      continuous_session=continuous_session,
      quote_stale=quote_stale,
      duplicate=False,
      ignored_reason="INVALID_SAMPLE",
      prior_pullback_active=state.pullback_active,
      prior_momentum_active=state.momentum_active,
    )
    return PhaseOneBaselineReduction(state=state, evaluation=evaluation)

  if state.samples:
    latest = state.samples[-1]
    identity = (sample.source_time_ms, sample.tick_ordinal)
    last_identity = (latest.source_time_ms, latest.tick_ordinal)
    if identity == last_identity:
      evaluation = _evaluate(
        state.samples,
        sample,
        config,
        continuous_session=continuous_session,
        quote_stale=quote_stale,
        duplicate=True,
        ignored_reason="DUPLICATE_SOURCE_IDENTITY",
        prior_pullback_active=state.pullback_active,
        prior_momentum_active=state.momentum_active,
      )
      return PhaseOneBaselineReduction(state=state, evaluation=evaluation)
    if identity < last_identity:
      evaluation = _evaluate(
        state.samples,
        sample,
        config,
        continuous_session=continuous_session,
        quote_stale=quote_stale,
        duplicate=False,
        ignored_reason="OUT_OF_ORDER_SOURCE_IDENTITY",
        prior_pullback_active=state.pullback_active,
        prior_momentum_active=state.momentum_active,
      )
      return PhaseOneBaselineReduction(state=state, evaluation=evaluation)
    if _counter_rolled_back(latest, sample):
      state = PhaseOneBaselineState()
      reset_reason = "CUMULATIVE_COUNTER_ROLLBACK"

  samples = _bounded_samples((*state.samples, sample), config)
  evaluation = _evaluate(
    samples,
    sample,
    config,
    continuous_session=continuous_session,
    quote_stale=quote_stale,
    duplicate=False,
    ignored_reason=reset_reason,
    prior_pullback_active=state.pullback_active,
    prior_momentum_active=state.momentum_active,
  )
  pullback_active = _checks_pass(evaluation.pullback_checks)
  momentum_active = _checks_pass(evaluation.momentum_checks)
  next_state = PhaseOneBaselineState(
    instrument_code=sample.instrument_code,
    trade_date=sample.trade_date,
    continuity_generation=sample.continuity_generation,
    samples=samples,
    pullback_active=pullback_active,
    momentum_active=momentum_active,
  )
  return PhaseOneBaselineReduction(state=next_state, evaluation=evaluation)


def _evaluate(
  samples: Sequence[OpportunitySample],
  source_sample: OpportunitySample,
  policy: PhaseOneBaselinePolicy,
  *,
  continuous_session: bool,
  quote_stale: bool,
  duplicate: bool,
  ignored_reason: Optional[str],
  prior_pullback_active: bool,
  prior_momentum_active: bool,
) -> PhaseOneBaselineEvaluation:
  latest = samples[-1] if samples else source_sample
  coverage_seconds = (
    (latest.source_time_ms - samples[0].source_time_ms) / 1000.0
    if len(samples) >= 2
    else None
  )
  common = (
    PhaseOneRuleFact("CONTINUOUS_SESSION", bool(continuous_session)),
    PhaseOneRuleFact("QUOTE_FRESH", not quote_stale),
    PhaseOneRuleFact(
      "SOURCE_IDENTITY_ACCEPTED",
      ignored_reason
      not in {
        "INVALID_SAMPLE",
        "OUT_OF_ORDER_SOURCE_IDENTITY",
        "DUPLICATE_SOURCE_IDENTITY",
        "CONTINUITY_CHANGED",
        "TRADE_DATE_CHANGED",
        "CUMULATIVE_COUNTER_ROLLBACK",
      },
    ),
  )
  pullback_checks, pullback_metrics = _pullback_checks(samples, latest, policy)
  momentum_checks, momentum_metrics = _momentum_checks(samples, latest, policy)
  pullback_checks = (*common, *pullback_checks)
  momentum_checks = (*common, *momentum_checks)
  pullback_triggered = _checks_pass(pullback_checks)
  momentum_triggered = _checks_pass(momentum_checks)
  selected_path = (
    OpportunityPath.PULLBACK_REBOUND
    if pullback_triggered
    else OpportunityPath.MOMENTUM_ACCELERATION
    if momentum_triggered
    else OpportunityPath.NONE
  )
  raw_triggered = selected_path is not OpportunityPath.NONE
  trigger_edge = bool(
    not duplicate
    and (
      (selected_path is OpportunityPath.PULLBACK_REBOUND and not prior_pullback_active)
      or (
        selected_path is OpportunityPath.MOMENTUM_ACCELERATION
        and not prior_momentum_active
      )
    )
  )
  selected_checks = (
    pullback_checks
    if selected_path is OpportunityPath.PULLBACK_REBOUND
    else momentum_checks
    if selected_path is OpportunityPath.MOMENTUM_ACCELERATION
    else _diagnostic_checks(latest, pullback_checks, momentum_checks)
  )
  return PhaseOneBaselineEvaluation(
    baseline_version=policy.version,
    instrument_code=source_sample.instrument_code,
    trade_date=source_sample.trade_date,
    source_time_ms=source_sample.source_time_ms,
    tick_ordinal=source_sample.tick_ordinal,
    continuity_generation=source_sample.continuity_generation,
    sample_count=len(samples),
    coverage_seconds=coverage_seconds,
    selected_path=selected_path,
    raw_triggered=raw_triggered,
    trigger_edge=trigger_edge,
    duplicate=duplicate,
    ignored_reason=ignored_reason,
    top_blocker=next(
      (item.code for item in selected_checks if item.passed is not True),
      None,
    ),
    pullback_checks=tuple(pullback_checks),
    momentum_checks=tuple(momentum_checks),
    metrics={**pullback_metrics, **momentum_metrics},
  )


def _pullback_checks(
  samples: Sequence[OpportunitySample],
  latest: OpportunitySample,
  policy: PhaseOneBaselinePolicy,
) -> tuple[tuple[PhaseOneRuleFact, ...], dict[str, Optional[float]]]:
  cutoff = latest.source_time_ms - policy.pullback_lookback_seconds * 1000
  window = tuple(item for item in samples if item.source_time_ms >= cutoff)
  if len(window) < 3:
    checks = (
      PhaseOneRuleFact("PULLBACK_MINIMUM_TICKS", False, float(len(window)), 3.0),
    )
    return checks, _empty_metrics("pullback")

  peak = window[0]
  high_anchor = window[0]
  low_anchor = window[0]
  best_pullback = 0.0
  for item in window[1:]:
    pullback = (peak.price - item.price) / peak.price * 100.0
    if pullback > best_pullback + _EPSILON:
      best_pullback = pullback
      high_anchor = peak
      low_anchor = item
    if item.price > peak.price:
      peak = item
  high = high_anchor.price
  low = low_anchor.price
  pullback_pct = (high - low) / high * 100.0 if high > 0 else None
  rebound_pct = (latest.price - low) / low * 100.0 if low > 0 else None
  stabilized_seconds = (latest.source_time_ms - low_anchor.source_time_ms) / 1000.0
  spread_ticks = _spread_tick_count(latest)
  vwap = _session_vwap(latest)
  vwap_condition = vwap is None or latest.price <= vwap + _EPSILON
  checks = (
    PhaseOneRuleFact("PULLBACK_MINIMUM_TICKS", True, float(len(window)), 3.0),
    PhaseOneRuleFact(
      "PULLBACK_DEPTH_AT_LEAST",
      _at_least(pullback_pct, policy.pullback_threshold_pct),
      pullback_pct,
      policy.pullback_threshold_pct,
    ),
    PhaseOneRuleFact(
      "PULLBACK_REBOUND_AT_LEAST",
      _at_least(rebound_pct, policy.rebound_threshold_pct),
      rebound_pct,
      policy.rebound_threshold_pct,
    ),
    PhaseOneRuleFact(
      "PULLBACK_LOW_STABILIZED",
      stabilized_seconds + _EPSILON >= policy.stabilization_seconds,
      stabilized_seconds,
      float(policy.stabilization_seconds),
    ),
    PhaseOneRuleFact("PULLBACK_BOOK_COMPLETE", spread_ticks is not None),
    PhaseOneRuleFact(
      "PULLBACK_SPREAD_AT_MOST_TICKS",
      _at_most(spread_ticks, float(policy.pullback_max_spread_ticks)),
      spread_ticks,
      float(policy.pullback_max_spread_ticks),
    ),
    PhaseOneRuleFact(
      "PULLBACK_NOT_ABOVE_VWAP_IF_AVAILABLE",
      vwap_condition,
      ((latest.price / vwap - 1.0) * 100.0 if vwap else None),
      0.0,
      "VWAP_UNAVAILABLE_NOT_APPLICABLE" if vwap is None else "",
    ),
  )
  return checks, {
    "pullback_pct": pullback_pct,
    "rebound_pct": rebound_pct,
    "seconds_since_low": stabilized_seconds,
    "pullback_spread_ticks": spread_ticks,
    "session_vwap": vwap,
  }


def _momentum_checks(
  samples: Sequence[OpportunitySample],
  latest: OpportunitySample,
  policy: PhaseOneBaselinePolicy,
) -> tuple[tuple[PhaseOneRuleFact, ...], dict[str, Optional[float]]]:
  cutoff = latest.source_time_ms - policy.momentum_window_seconds * 1000
  window = tuple(item for item in samples if item.source_time_ms >= cutoff)
  if len(window) < 2:
    checks = (
      PhaseOneRuleFact("MOMENTUM_MINIMUM_TICKS", False, float(len(window)), 2.0),
    )
    return checks, _empty_metrics("momentum")

  low = min(
    window,
    key=lambda item: (item.price, item.source_time_ms, item.tick_ordinal),
  )
  high = max(item.price for item in window)
  rise_pct = (latest.price / low.price - 1.0) * 100.0 if low.price > 0 else None
  move_seconds = (latest.source_time_ms - low.source_time_ms) / 1000.0
  price_tick = latest.price_tick if _positive(latest.price_tick) else None
  near_high = bool(
    price_tick is not None
    and latest.price
    >= high - policy.momentum_high_tolerance_ticks * price_tick - _EPSILON
  )
  baseline_cutoff = low.source_time_ms - policy.momentum_baseline_seconds * 1000
  baseline = tuple(
    item
    for item in samples
    if baseline_cutoff <= item.source_time_ms <= low.source_time_ms
  )
  baseline_start = baseline[0] if baseline else low
  baseline_seconds = (low.source_time_ms - baseline_start.source_time_ms) / 1000.0
  required_baseline = (
    policy.momentum_baseline_seconds * policy.momentum_baseline_coverage_ratio
  )
  move_amount = _counter_delta(latest.cumulative_amount, low.cumulative_amount)
  baseline_amount = _counter_delta(
    low.cumulative_amount,
    baseline_start.cumulative_amount,
  )
  velocity_ratio = (
    (move_amount / move_seconds) / (baseline_amount / baseline_seconds)
    if move_amount is not None
    and baseline_amount is not None
    and move_amount > 0
    and baseline_amount > 0
    and move_seconds > 0
    and baseline_seconds > 0
    else None
  )
  spread_ticks = _spread_tick_count(latest)
  spread_pct = (
    (float(latest.ask_price) - float(latest.bid_price)) / latest.price * 100.0
    if spread_ticks is not None and latest.price > 0
    else None
  )
  vwap = _session_vwap(latest)
  premium = (latest.price / vwap - 1.0) * 100.0 if vwap else None
  checks = (
    PhaseOneRuleFact("MOMENTUM_MINIMUM_TICKS", True, float(len(window)), 2.0),
    PhaseOneRuleFact(
      "MOMENTUM_RISE_AT_LEAST",
      _at_least(rise_pct, policy.momentum_min_rise_pct),
      rise_pct,
      policy.momentum_min_rise_pct,
    ),
    PhaseOneRuleFact(
      "MOMENTUM_MOVE_DURATION_AT_LEAST",
      move_seconds + _EPSILON >= policy.momentum_min_move_seconds,
      move_seconds,
      float(policy.momentum_min_move_seconds),
    ),
    PhaseOneRuleFact("MOMENTUM_NEAR_WINDOW_HIGH", near_high),
    PhaseOneRuleFact(
      "MOMENTUM_BASELINE_COVERAGE_AT_LEAST",
      baseline_seconds + _EPSILON >= required_baseline,
      baseline_seconds,
      required_baseline,
    ),
    PhaseOneRuleFact(
      "MOMENTUM_TURNOVER_FACTS_AVAILABLE",
      move_amount is not None
      and baseline_amount is not None
      and move_amount > 0
      and baseline_amount > 0,
    ),
    PhaseOneRuleFact(
      "MOMENTUM_AMOUNT_VELOCITY_AT_LEAST",
      _at_least(velocity_ratio, policy.momentum_min_amount_velocity_ratio),
      velocity_ratio,
      policy.momentum_min_amount_velocity_ratio,
    ),
    PhaseOneRuleFact("MOMENTUM_VWAP_AVAILABLE", vwap is not None),
    PhaseOneRuleFact(
      "MOMENTUM_VWAP_PREMIUM_AT_LEAST",
      _at_least(premium, policy.momentum_min_vwap_premium_pct),
      premium,
      policy.momentum_min_vwap_premium_pct,
    ),
    PhaseOneRuleFact(
      "MOMENTUM_VWAP_PREMIUM_AT_MOST",
      _at_most(premium, policy.momentum_max_vwap_premium_pct),
      premium,
      policy.momentum_max_vwap_premium_pct,
    ),
    PhaseOneRuleFact("MOMENTUM_BOOK_COMPLETE", spread_ticks is not None),
    PhaseOneRuleFact(
      "MOMENTUM_SPREAD_AT_MOST_TICKS",
      _at_most(spread_ticks, float(policy.momentum_max_spread_ticks)),
      spread_ticks,
      float(policy.momentum_max_spread_ticks),
    ),
    PhaseOneRuleFact(
      "MOMENTUM_SPREAD_AT_MOST_PCT",
      _at_most(spread_pct, policy.momentum_max_spread_pct),
      spread_pct,
      policy.momentum_max_spread_pct,
    ),
  )
  return checks, {
    "momentum_rise_pct": rise_pct,
    "momentum_move_seconds": move_seconds,
    "momentum_window_high": high,
    "momentum_baseline_coverage_seconds": baseline_seconds,
    "momentum_amount_velocity_ratio": velocity_ratio,
    "momentum_vwap_premium_pct": premium,
    "momentum_spread_ticks": spread_ticks,
    "momentum_spread_pct": spread_pct,
  }


def _diagnostic_checks(
  latest: OpportunitySample,
  pullback: Sequence[PhaseOneRuleFact],
  momentum: Sequence[PhaseOneRuleFact],
) -> Sequence[PhaseOneRuleFact]:
  vwap = _session_vwap(latest)
  if vwap is not None and latest.price > vwap:
    return momentum
  return pullback


def _checks_pass(checks: Sequence[PhaseOneRuleFact]) -> bool:
  return bool(checks) and all(item.passed is True for item in checks)


def _bounded_samples(
  samples: Sequence[OpportunitySample],
  policy: PhaseOneBaselinePolicy,
) -> tuple[OpportunitySample, ...]:
  latest = samples[-1]
  cutoff = latest.source_time_ms - policy.state_window_seconds * 1000
  bounded = tuple(item for item in samples if item.source_time_ms >= cutoff)
  return bounded[-policy.max_samples :]


def _valid_sample(sample: OpportunitySample) -> bool:
  return bool(
    sample.instrument_code
    and sample.trade_date
    and sample.continuity_generation
    and sample.source_time_ms > 0
    and sample.tick_ordinal >= 0
    and math.isfinite(sample.price)
    and sample.price > 0
  )


def _counter_rolled_back(
  previous: OpportunitySample,
  current: OpportunitySample,
) -> bool:
  for name in ("cumulative_amount", "cumulative_volume"):
    before = _non_negative(getattr(previous, name))
    after = _non_negative(getattr(current, name))
    if before is not None and after is not None and after + _EPSILON < before:
      return True
  return False


def _counter_delta(current: Any, previous: Any) -> Optional[float]:
  current_value = _non_negative(current)
  previous_value = _non_negative(previous)
  if current_value is None or previous_value is None:
    return None
  delta = current_value - previous_value
  return delta if delta >= -_EPSILON else None


def _session_vwap(sample: OpportunitySample) -> Optional[float]:
  amount = _positive(sample.cumulative_amount)
  pvolume = _positive(sample.cumulative_volume)
  return amount / pvolume if amount is not None and pvolume is not None else None


def _spread_tick_count(sample: OpportunitySample) -> Optional[float]:
  try:
    tick = Decimal(str(sample.price_tick))
    bid = Decimal(str(sample.bid_price))
    ask = Decimal(str(sample.ask_price))
    if (
      not tick.is_finite()
      or tick <= 0
      or not bid.is_finite()
      or not ask.is_finite()
      or bid <= 0
      or ask <= 0
      or ask < bid
    ):
      return None
    bid_tick = (bid / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    ask_tick = (ask / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
  except (InvalidOperation, TypeError, ValueError):
    return None
  return float(max(0, int(ask_tick - bid_tick)))


def _at_least(value: Optional[float], threshold: float) -> Optional[bool]:
  return None if value is None else value + _EPSILON >= threshold


def _at_most(value: Optional[float], threshold: float) -> Optional[bool]:
  return None if value is None else value <= threshold + _EPSILON


def _positive(value: Any) -> Optional[float]:
  try:
    normalized = float(value)
  except (TypeError, ValueError, OverflowError):
    return None
  return normalized if math.isfinite(normalized) and normalized > 0 else None


def _non_negative(value: Any) -> Optional[float]:
  try:
    normalized = float(value)
  except (TypeError, ValueError, OverflowError):
    return None
  return normalized if math.isfinite(normalized) and normalized >= 0 else None


def _empty_metrics(prefix: str) -> dict[str, Optional[float]]:
  if prefix == "pullback":
    return {
      "pullback_pct": None,
      "rebound_pct": None,
      "seconds_since_low": None,
      "pullback_spread_ticks": None,
      "session_vwap": None,
    }
  return {
    "momentum_rise_pct": None,
    "momentum_move_seconds": None,
    "momentum_window_high": None,
    "momentum_baseline_coverage_seconds": None,
    "momentum_amount_velocity_ratio": None,
    "momentum_vwap_premium_pct": None,
    "momentum_spread_ticks": None,
    "momentum_spread_pct": None,
  }


__all__ = [
  "PHASE_ONE_BASELINE_VERSION",
  "PhaseOneBaselineEvaluation",
  "PhaseOneBaselinePolicy",
  "PhaseOneBaselineReduction",
  "PhaseOneBaselineState",
  "PhaseOneRuleFact",
  "reduce_phase_one_baseline",
]
