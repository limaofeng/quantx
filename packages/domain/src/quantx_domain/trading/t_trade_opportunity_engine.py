"""Causal, stateful opportunity engine for the A-share intraday T assistant.

This module is deliberately pure domain code.  It does not read a clock, an
account, a repository, or a market-data stream.  Callers adapt their immutable
point-in-time inputs into :class:`OpportunitySample` and
:class:`OpportunityGateContext`, then persist ``result.state.to_dict()`` inside
the strategy's ordinary ``RuntimeStatePatch``.

The reducer observes both pullback and momentum formations on every accepted
sample.  Opportunity scoring never overrides a failed hard gate, and a latched
candidate is stable for one causal episode until the pattern is explicitly
rearmed.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field, fields, replace
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

OPPORTUNITY_STATE_SCHEMA_VERSION = 3
OPPORTUNITY_FEATURE_SCHEMA_VERSION = 1
OPPORTUNITY_REFERENCE_PROFILE_SCHEMA_VERSION = 1
OPPORTUNITY_POLICY_VERSION = "t_trade_opportunity_v3.0.0"
_EPSILON = 1e-9

# Storage-safety limits, deliberately separate from trading defaults.  This
# state is checkpointed as JSON for every monitored holding, so an editable
# policy must not be able to create an unbounded runtime snapshot.
OPPORTUNITY_MAX_SAMPLES = 3_000
OPPORTUNITY_MAX_STATE_WINDOW_SECONDS = 14_400
OPPORTUNITY_MAX_QUOTE_AGE_MS = 30_000
OPPORTUNITY_MAX_CONFIRM_SECONDS = 60
OPPORTUNITY_MAX_CONFIRM_TICKS = 120
OPPORTUNITY_MAX_REARM_SECONDS = 14_400
OPPORTUNITY_MAX_CANDIDATE_TTL_SECONDS = 14_400

_SUPPORTED_REQUIRED_SAMPLE_FIELDS = (
  "bid_price",
  "ask_price",
  "bid_volume",
  "ask_volume",
  "cumulative_amount",
  "cumulative_volume",
)
_SUPPORTED_SESSION_CODES = ("CONTINUOUS_AM", "CONTINUOUS_PM")


class DataHealth(str, Enum):
  WARMING = "WARMING"
  READY = "READY"
  DEGRADED = "DEGRADED"
  STALE = "STALE"
  CONTINUITY_LOST = "CONTINUITY_LOST"
  INSUFFICIENT = "INSUFFICIENT"


class OpportunityPath(str, Enum):
  NONE = "NONE"
  PULLBACK_REBOUND = "PULLBACK_REBOUND"
  MOMENTUM_ACCELERATION = "MOMENTUM_ACCELERATION"


class PullbackPhase(str, Enum):
  OBSERVING = "OBSERVING"
  PULLBACK_FORMING = "PULLBACK_FORMING"
  LOW_STABILIZING = "LOW_STABILIZING"
  REBOUND_CONFIRMING = "REBOUND_CONFIRMING"
  CANDIDATE_LATCHED = "CANDIDATE_LATCHED"
  SUPPRESSED = "SUPPRESSED"


class MomentumPhase(str, Enum):
  OBSERVING = "OBSERVING"
  BASELINING = "BASELINING"
  MOMENTUM_BUILDING = "MOMENTUM_BUILDING"
  ACCELERATING = "ACCELERATING"
  OVEREXTENDED = "OVEREXTENDED"
  CANDIDATE_LATCHED = "CANDIDATE_LATCHED"
  SUPPRESSED = "SUPPRESSED"


class CandidateStatus(str, Enum):
  NONE = "NONE"
  LATCHED = "LATCHED"
  AWAITING_APPROVAL = "AWAITING_APPROVAL"
  SUPPRESSED = "SUPPRESSED"
  REARMING = "REARMING"


@dataclass(frozen=True)
class OpportunitySample:
  """One causally ordered market observation.

  ``continuity_generation`` is a scalar projection of the Engine-owned
  market-data context.  A quiet instrument may legitimately have a large time
  gap between samples; only a generation change invalidates the causal window.
  """

  instrument_code: str
  trade_date: str
  source_time_ms: int
  tick_ordinal: int
  price: float
  continuity_generation: str = "0"
  received_at_ms: Optional[int] = None
  bid_price: Optional[float] = None
  ask_price: Optional[float] = None
  bid_volume: Optional[float] = None
  ask_volume: Optional[float] = None
  cumulative_amount: Optional[float] = None
  cumulative_volume: Optional[float] = None
  price_tick: float = 0.01

  def __post_init__(self) -> None:
    if not self.instrument_code:
      raise ValueError("opportunity sample instrument_code is required")
    if not self.trade_date:
      raise ValueError("opportunity sample trade_date is required")
    if self.source_time_ms < 0:
      raise ValueError("source_time_ms must be non-negative")
    if self.tick_ordinal < 0:
      raise ValueError("tick_ordinal must be non-negative")
    generation = str(self.continuity_generation or "")
    if not generation:
      raise ValueError("continuity_generation is required")
    object.__setattr__(self, "continuity_generation", generation)

  @property
  def source_identity(self) -> tuple[str, int, int]:
    return (
      self.continuity_generation,
      self.source_time_ms,
      self.tick_ordinal,
    )

  def to_dict(self) -> dict[str, Any]:
    return _jsonable(asdict(self))

  @classmethod
  def from_dict(cls, raw: Mapping[str, Any]) -> "OpportunitySample":
    return cls(
      instrument_code=str(raw.get("instrument_code", "") or ""),
      trade_date=str(raw.get("trade_date", "") or ""),
      source_time_ms=int(raw.get("source_time_ms", 0) or 0),
      tick_ordinal=int(raw.get("tick_ordinal", 0) or 0),
      price=_float(raw.get("price")),
      continuity_generation=str(raw.get("continuity_generation", "") or ""),
      received_at_ms=_optional_int(raw.get("received_at_ms")),
      bid_price=_optional_float(raw.get("bid_price")),
      ask_price=_optional_float(raw.get("ask_price")),
      bid_volume=_optional_float(raw.get("bid_volume")),
      ask_volume=_optional_float(raw.get("ask_volume")),
      cumulative_amount=_optional_float(raw.get("cumulative_amount")),
      cumulative_volume=_optional_float(raw.get("cumulative_volume")),
      price_tick=_float(raw.get("price_tick"), default=0.01),
    )


@dataclass(frozen=True)
class OpportunityGateContext:
  """Market facts that may veto an opportunity candidate.

  Account, universe, batch, cooldown, and risk eligibility intentionally do
  not belong here.  Those facts decide whether a strategy may emit a
  ``TradeIntent``; they must not stop this market-opportunity reducer.
  """

  continuous_session: bool = True
  quote_stale: bool = False
  session_code: Optional[str] = None
  local_second_of_day: Optional[int] = None

  def __post_init__(self) -> None:
    if self.session_code is not None:
      normalized = str(self.session_code or "").strip().upper()
      if not normalized:
        raise ValueError("session_code must not be blank")
      object.__setattr__(self, "session_code", normalized)
    if self.local_second_of_day is not None and not (
      0 <= int(self.local_second_of_day) < 24 * 60 * 60
    ):
      raise ValueError("local_second_of_day must be within one day")


@dataclass(frozen=True)
class CandidateControl:
  """Optional causal feedback for a previously latched candidate."""

  awaiting_approval_candidate_id: Optional[str] = None
  suppress_candidate_id: Optional[str] = None

  def __post_init__(self) -> None:
    if self.awaiting_approval_candidate_id and self.suppress_candidate_id:
      raise ValueError("candidate control accepts only one transition")

  def to_dict(self) -> dict[str, Any]:
    return _jsonable(asdict(self))

  @classmethod
  def from_dict(cls, raw: Mapping[str, Any]) -> "CandidateControl":
    return cls(
      awaiting_approval_candidate_id=_optional_str(
        raw.get("awaiting_approval_candidate_id")
      ),
      suppress_candidate_id=_optional_str(raw.get("suppress_candidate_id")),
    )


@dataclass(frozen=True)
class OpportunityReferenceProfile:
  """Prior-only per-instrument thresholds supplied by the application layer."""

  profile_version: str
  profile_schema_version: int
  as_of_trade_date: str
  pullback_threshold_pct: float
  momentum_rise_threshold_pct: float
  momentum_amount_velocity_ratio: float
  pullback_max_spread_ticks: int
  momentum_max_spread_ticks: int

  def __post_init__(self) -> None:
    if not self.profile_version or not self.as_of_trade_date:
      raise ValueError("reference profile version and as_of_trade_date are required")
    if self.profile_schema_version <= 0:
      raise ValueError("reference profile schema version must be positive")
    for name in (
      "pullback_threshold_pct",
      "momentum_rise_threshold_pct",
      "momentum_amount_velocity_ratio",
    ):
      value = getattr(self, name)
      if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    for name in ("pullback_max_spread_ticks", "momentum_max_spread_ticks"):
      value = getattr(self, name)
      if value < 0:
        raise ValueError(f"{name} must be non-negative")

  def to_dict(self) -> dict[str, Any]:
    return _jsonable(asdict(self))

  @classmethod
  def from_dict(cls, raw: Mapping[str, Any]) -> "OpportunityReferenceProfile":
    return cls(
      profile_version=str(raw.get("profile_version", "") or ""),
      profile_schema_version=int(raw.get("profile_schema_version", 0) or 0),
      as_of_trade_date=str(raw.get("as_of_trade_date", "") or ""),
      pullback_threshold_pct=_float(raw.get("pullback_threshold_pct")),
      momentum_rise_threshold_pct=_float(raw.get("momentum_rise_threshold_pct")),
      momentum_amount_velocity_ratio=_float(raw.get("momentum_amount_velocity_ratio")),
      pullback_max_spread_ticks=int(raw.get("pullback_max_spread_ticks", -1)),
      momentum_max_spread_ticks=int(raw.get("momentum_max_spread_ticks", -1)),
    )


@dataclass(frozen=True)
class OpportunityPolicy:
  policy_version: str = OPPORTUNITY_POLICY_VERSION
  feature_schema_version: int = OPPORTUNITY_FEATURE_SCHEMA_VERSION

  # Bounded causal state and data-quality contract.
  max_samples: int = 3_000
  max_quote_age_ms: int = 3_000
  pullback_min_samples: int = 3
  pullback_min_coverage_seconds: int = 15
  momentum_min_samples: int = 3
  momentum_min_coverage_seconds: int = 240
  sparse_degraded_gap_seconds: int = 60
  pullback_required_fields: tuple[str, ...] = (
    "bid_price",
    "ask_price",
    "cumulative_amount",
    "cumulative_volume",
  )
  momentum_required_fields: tuple[str, ...] = (
    "bid_price",
    "ask_price",
    "cumulative_amount",
    "cumulative_volume",
  )

  # Engine-classified sessions plus policy-owned entry boundaries.
  allowed_session_codes: tuple[str, ...] = (
    "CONTINUOUS_AM",
    "CONTINUOUS_PM",
  )
  continuous_am_start_time: str = "09:30:00"
  continuous_am_end_time: str = "11:30:00"
  continuous_pm_start_time: str = "13:00:00"
  continuous_pm_end_time: str = "14:57:00"
  close_protection_seconds: int = 0

  # Pullback/rebound feature policy.
  pullback_lookback_seconds: int = 300
  pullback_stabilization_seconds: int = 15
  pullback_threshold_pct: float = 0.8
  pullback_formation_threshold_multiplier: float = 0.5
  pullback_rebound_threshold_pct: float = 0.2
  pullback_max_spread_ticks: int = 3
  pullback_volume_short_window_seconds: int = 15
  pullback_volume_baseline_window_seconds: int = 60

  # Momentum/acceleration feature policy.
  momentum_enabled: bool = True
  momentum_window_seconds: int = 60
  momentum_min_rise_pct: float = 0.8
  momentum_formation_threshold_multiplier: float = 0.5
  momentum_min_move_seconds: int = 15
  momentum_baseline_seconds: int = 300
  momentum_baseline_coverage_ratio: float = 0.8
  momentum_min_amount_velocity_ratio: float = 2.0
  momentum_min_vwap_premium_pct: float = 2.0
  momentum_max_vwap_premium_pct: float = 3.5
  momentum_high_tolerance_ticks: int = 1
  momentum_max_spread_ticks: int = 10
  momentum_max_spread_pct: float = 0.3

  # Prior-only profile safety clamps.
  profile_pullback_threshold_min_multiplier: float = 0.75
  profile_pullback_threshold_max_multiplier: float = 2.0
  profile_momentum_rise_min_multiplier: float = 0.75
  profile_momentum_rise_max_multiplier: float = 2.0
  profile_momentum_velocity_min_ratio: float = 1.25
  profile_momentum_velocity_max_ratio: float = 5.0

  # Positive contribution weights. Each path must sum to exactly 100.
  pullback_depth_weight: float = 25.0
  pullback_rebound_weight: float = 20.0
  pullback_stabilization_weight: float = 15.0
  pullback_turn_slope_weight: float = 10.0
  pullback_vwap_weight: float = 10.0
  pullback_liquidity_weight: float = 10.0
  pullback_volume_weight: float = 10.0
  momentum_rise_weight: float = 20.0
  momentum_turnover_weight: float = 20.0
  momentum_slope_weight: float = 15.0
  momentum_persistence_weight: float = 10.0
  momentum_vwap_weight: float = 15.0
  momentum_liquidity_weight: float = 10.0
  momentum_book_imbalance_weight: float = 10.0

  # Score normalization boundaries.
  pullback_depth_score_min_pct: float = 0.0
  pullback_depth_score_target_multiplier: float = 1.0
  pullback_rebound_score_min_pct: float = 0.0
  pullback_rebound_score_max_pct: float = 0.2
  pullback_stabilization_score_min_seconds: float = 0.0
  pullback_stabilization_score_max_seconds: float = 15.0
  pullback_turn_slope_score_min_pct_per_second: float = 0.0
  pullback_turn_slope_score_max_pct_per_second: float = 0.013333333333333334
  pullback_vwap_full_score_max_premium_pct: float = 0.0
  pullback_vwap_zero_score_premium_pct: float = 0.5
  pullback_liquidity_full_score_spread_ticks: float = 1.0
  pullback_liquidity_zero_score_spread_ticks: float = 4.0
  pullback_volume_score_min_ratio: float = 0.8
  pullback_volume_score_max_ratio: float = 1.5
  momentum_rise_score_min_pct: float = 0.0
  momentum_rise_score_target_multiplier: float = 1.0
  momentum_turnover_score_min_ratio: float = 1.0
  momentum_turnover_score_target_multiplier: float = 1.0
  momentum_slope_score_min_pct_per_second: float = 0.0
  momentum_slope_score_target_multiplier: float = 1.0
  momentum_persistence_score_min_ratio: float = 0.75
  momentum_persistence_score_max_ratio: float = 1.0
  momentum_vwap_zero_score_min_premium_pct: float = 0.0
  momentum_vwap_zero_score_max_premium_pct: float = 7.0
  momentum_liquidity_full_score_spread_ticks: float = 1.0
  momentum_liquidity_zero_score_spread_ticks: float = 11.0
  momentum_book_imbalance_score_min_ratio: float = -0.2
  momentum_book_imbalance_score_max_ratio: float = 0.4

  # Explicit penalties; they are not part of each path's positive weight sum.
  pullback_data_quality_penalty_points: float = 10.0
  pullback_chase_penalty_start_premium_pct: float = 0.0
  pullback_chase_penalty_full_premium_pct: float = 1.0
  pullback_chase_penalty_points: float = 20.0
  momentum_data_quality_penalty_points: float = 10.0
  momentum_overextension_penalty_start_premium_pct: float = 3.5
  momentum_overextension_penalty_full_premium_pct: float = 7.0
  momentum_overextension_penalty_points: float = 30.0

  # Candidate lifecycle thresholds.
  preview_score: float = 55.0
  candidate_score: float = 72.0
  revalidate_score: float = 60.0
  rearm_score: float = 45.0
  candidate_confirm_seconds: int = 2
  candidate_confirm_ticks: int = 2
  rearm_seconds: int = 15
  candidate_ttl_seconds: int = 30

  def __post_init__(self) -> None:
    if not self.policy_version:
      raise ValueError("policy_version is required")
    if self.feature_schema_version != OPPORTUNITY_FEATURE_SCHEMA_VERSION:
      raise ValueError(
        "feature_schema_version does not match the current opportunity feature schema"
      )
    for name in (
      "max_samples",
      "max_quote_age_ms",
      "pullback_min_samples",
      "pullback_min_coverage_seconds",
      "momentum_min_samples",
      "momentum_min_coverage_seconds",
      "pullback_lookback_seconds",
      "pullback_stabilization_seconds",
      "pullback_volume_short_window_seconds",
      "pullback_volume_baseline_window_seconds",
      "momentum_window_seconds",
      "momentum_min_move_seconds",
      "momentum_baseline_seconds",
      "candidate_confirm_ticks",
      "candidate_confirm_seconds",
      "rearm_seconds",
      "candidate_ttl_seconds",
    ):
      if int(getattr(self, name)) <= 0:
        raise ValueError(f"{name} must be positive")
    if self.close_protection_seconds < 0:
      raise ValueError("close_protection_seconds must be non-negative")
    if self.sparse_degraded_gap_seconds < 0:
      raise ValueError("sparse_degraded_gap_seconds must be non-negative")
    for name in (
      "pullback_max_spread_ticks",
      "momentum_high_tolerance_ticks",
      "momentum_max_spread_ticks",
    ):
      if int(getattr(self, name)) < 0:
        raise ValueError(f"{name} must be non-negative")
    for name in (
      "pullback_threshold_pct",
      "pullback_rebound_threshold_pct",
      "momentum_min_rise_pct",
      "momentum_min_amount_velocity_ratio",
      "momentum_max_vwap_premium_pct",
      "momentum_max_spread_pct",
    ):
      value = float(getattr(self, name))
      if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    if not 0 <= self.momentum_baseline_coverage_ratio <= 1:
      raise ValueError("momentum_baseline_coverage_ratio must be in [0, 1]")
    if (
      not math.isfinite(self.momentum_min_vwap_premium_pct)
      or self.momentum_min_vwap_premium_pct < 0
    ):
      raise ValueError("momentum_min_vwap_premium_pct must be finite and non-negative")
    if self.momentum_max_vwap_premium_pct <= self.momentum_min_vwap_premium_pct:
      raise ValueError("momentum VWAP premium band is invalid")
    if (
      self.pullback_volume_short_window_seconds
      > self.pullback_volume_baseline_window_seconds
    ):
      raise ValueError(
        "pullback_volume_short_window_seconds must not exceed "
        "pullback_volume_baseline_window_seconds"
      )
    if self.pullback_stabilization_seconds >= self.pullback_lookback_seconds:
      raise ValueError(
        "pullback_stabilization_seconds must be below pullback_lookback_seconds"
      )
    if self.momentum_min_move_seconds > self.momentum_window_seconds:
      raise ValueError(
        "momentum_min_move_seconds must not exceed momentum_window_seconds"
      )
    if self.momentum_window_seconds > self.momentum_baseline_seconds:
      raise ValueError(
        "momentum_window_seconds must not exceed momentum_baseline_seconds"
      )
    if self.max_samples < max(self.pullback_min_samples, self.momentum_min_samples):
      raise ValueError("max_samples must cover both path minimum sample counts")
    if self.max_samples > OPPORTUNITY_MAX_SAMPLES:
      raise ValueError(f"max_samples must not exceed {OPPORTUNITY_MAX_SAMPLES}")
    if self.max_quote_age_ms > OPPORTUNITY_MAX_QUOTE_AGE_MS:
      raise ValueError(
        f"max_quote_age_ms must not exceed {OPPORTUNITY_MAX_QUOTE_AGE_MS}"
      )
    if self.candidate_confirm_seconds > OPPORTUNITY_MAX_CONFIRM_SECONDS:
      raise ValueError(
        "candidate_confirm_seconds must not exceed "
        f"{OPPORTUNITY_MAX_CONFIRM_SECONDS}"
      )
    if self.candidate_confirm_ticks > OPPORTUNITY_MAX_CONFIRM_TICKS:
      raise ValueError(
        "candidate_confirm_ticks must not exceed "
        f"{OPPORTUNITY_MAX_CONFIRM_TICKS}"
      )
    if self.rearm_seconds > OPPORTUNITY_MAX_REARM_SECONDS:
      raise ValueError(
        f"rearm_seconds must not exceed {OPPORTUNITY_MAX_REARM_SECONDS}"
      )
    if self.candidate_ttl_seconds > OPPORTUNITY_MAX_CANDIDATE_TTL_SECONDS:
      raise ValueError(
        "candidate_ttl_seconds must not exceed "
        f"{OPPORTUNITY_MAX_CANDIDATE_TTL_SECONDS}"
      )
    if self.pullback_min_coverage_seconds < self.pullback_stabilization_seconds:
      raise ValueError(
        "pullback_min_coverage_seconds must cover pullback stabilization"
      )
    if self.pullback_min_coverage_seconds > self.pullback_lookback_seconds:
      raise ValueError(
        "pullback_min_coverage_seconds must not exceed pullback lookback"
      )
    required_momentum_coverage = (
      self.momentum_baseline_seconds * self.momentum_baseline_coverage_ratio
    )
    if self.momentum_min_coverage_seconds + _EPSILON < max(
      float(self.momentum_min_move_seconds),
      required_momentum_coverage,
    ):
      raise ValueError(
        "momentum_min_coverage_seconds must cover move and baseline requirements"
      )
    if self.momentum_min_coverage_seconds > (
      self.momentum_window_seconds + self.momentum_baseline_seconds
    ):
      raise ValueError(
        "momentum_min_coverage_seconds must fit the bounded momentum state window"
      )
    if self.state_window_seconds > OPPORTUNITY_MAX_STATE_WINDOW_SECONDS:
      raise ValueError(
        "opportunity state window must not exceed "
        f"{OPPORTUNITY_MAX_STATE_WINDOW_SECONDS} seconds"
      )
    if not (
      0
      <= self.rearm_score
      < self.preview_score
      < self.revalidate_score
      < self.candidate_score
      <= 100
    ):
      raise ValueError(
        "score thresholds must satisfy "
        "0 <= rearm < preview < revalidate < candidate <= 100"
      )
    self._normalize_and_validate_collections()
    self._validate_trading_windows()
    self._validate_scoring_contract()

  @property
  def state_window_seconds(self) -> int:
    return max(
      self.pullback_lookback_seconds,
      self.momentum_window_seconds + self.momentum_baseline_seconds,
      self.pullback_volume_short_window_seconds
      + self.pullback_volume_baseline_window_seconds,
    )

  def to_dict(self) -> dict[str, Any]:
    return _jsonable(asdict(self))

  @classmethod
  def from_dict(cls, raw: Mapping[str, Any]) -> "OpportunityPolicy":
    if not isinstance(raw, Mapping):
      raise TypeError("opportunity policy must be a mapping")
    allowed = {item.name for item in fields(cls)}
    supplied = set(raw)
    unknown = sorted(supplied - allowed)
    if unknown:
      raise ValueError(f"opportunity policy has unknown fields: {', '.join(unknown)}")
    missing = sorted(allowed - supplied)
    if missing:
      raise ValueError(f"opportunity policy missing fields: {', '.join(missing)}")
    return cls(**{name: raw[name] for name in allowed})

  @classmethod
  def configurable_field_names(cls) -> tuple[str, ...]:
    return tuple(
      item.name
      for item in fields(cls)
      if item.name not in {"policy_version", "feature_schema_version"}
    )

  def _normalize_and_validate_collections(self) -> None:
    for name in ("pullback_required_fields", "momentum_required_fields"):
      raw = getattr(self, name)
      if isinstance(raw, str) or not isinstance(raw, Sequence):
        raise ValueError(f"{name} must be a list of supported field names")
      normalized = tuple(str(item or "").strip() for item in raw)
      if not normalized or any(not item for item in normalized):
        raise ValueError(f"{name} must not be empty or contain blanks")
      if len(set(normalized)) != len(normalized):
        raise ValueError(f"{name} must not contain duplicates")
      unknown = sorted(set(normalized) - set(_SUPPORTED_REQUIRED_SAMPLE_FIELDS))
      if unknown:
        raise ValueError(f"{name} has unsupported fields: {', '.join(unknown)}")
      canonical = tuple(
        item for item in _SUPPORTED_REQUIRED_SAMPLE_FIELDS if item in normalized
      )
      object.__setattr__(self, name, canonical)

    raw_sessions = self.allowed_session_codes
    if isinstance(raw_sessions, str) or not isinstance(raw_sessions, Sequence):
      raise ValueError("allowed_session_codes must be a list")
    normalized_sessions = tuple(
      str(item or "").strip().upper() for item in raw_sessions
    )
    if not normalized_sessions or any(not item for item in normalized_sessions):
      raise ValueError("allowed_session_codes must not be empty or contain blanks")
    if len(set(normalized_sessions)) != len(normalized_sessions):
      raise ValueError("allowed_session_codes must not contain duplicates")
    unknown_sessions = sorted(set(normalized_sessions) - set(_SUPPORTED_SESSION_CODES))
    if unknown_sessions:
      raise ValueError(
        "allowed_session_codes has unsupported values: " + ", ".join(unknown_sessions)
      )
    canonical_sessions = tuple(
      item for item in _SUPPORTED_SESSION_CODES if item in normalized_sessions
    )
    object.__setattr__(self, "allowed_session_codes", canonical_sessions)

  def _validate_trading_windows(self) -> None:
    windows = {
      "CONTINUOUS_AM": (
        _parse_time_seconds(self.continuous_am_start_time, "continuous_am_start_time"),
        _parse_time_seconds(self.continuous_am_end_time, "continuous_am_end_time"),
      ),
      "CONTINUOUS_PM": (
        _parse_time_seconds(self.continuous_pm_start_time, "continuous_pm_start_time"),
        _parse_time_seconds(self.continuous_pm_end_time, "continuous_pm_end_time"),
      ),
    }
    for code, (start, end) in windows.items():
      if start >= end:
        raise ValueError(f"{code} trading window start must be before end")
      canonical_start = _format_time_seconds(start)
      canonical_end = _format_time_seconds(end)
      prefix = "continuous_am" if code == "CONTINUOUS_AM" else "continuous_pm"
      object.__setattr__(self, f"{prefix}_start_time", canonical_start)
      object.__setattr__(self, f"{prefix}_end_time", canonical_end)
    am_start, am_end = windows["CONTINUOUS_AM"]
    pm_start, pm_end = windows["CONTINUOUS_PM"]
    if am_start < 9 * 3600 + 30 * 60 or am_end > 11 * 3600 + 30 * 60:
      raise ValueError("CONTINUOUS_AM policy window must stay within 09:30-11:30")
    if pm_start < 13 * 3600 or pm_end > 14 * 3600 + 57 * 60:
      raise ValueError("CONTINUOUS_PM policy window must stay within 13:00-14:57")
    if am_end > pm_start:
      raise ValueError("continuous trading windows must not overlap")
    longest_lifecycle = max(
      self.candidate_ttl_seconds + self.candidate_confirm_seconds,
      self.rearm_seconds,
    )
    for code in self.allowed_session_codes:
      start, end = windows[code]
      usable = end - start - self.close_protection_seconds
      if usable <= 0 or longest_lifecycle > usable:
        raise ValueError(
          f"{code} window cannot contain TTL/confirmation/rearm before close protection"
        )

  def _validate_scoring_contract(self) -> None:
    pullback_weights = (
      self.pullback_depth_weight,
      self.pullback_rebound_weight,
      self.pullback_stabilization_weight,
      self.pullback_turn_slope_weight,
      self.pullback_vwap_weight,
      self.pullback_liquidity_weight,
      self.pullback_volume_weight,
    )
    momentum_weights = (
      self.momentum_rise_weight,
      self.momentum_turnover_weight,
      self.momentum_slope_weight,
      self.momentum_persistence_weight,
      self.momentum_vwap_weight,
      self.momentum_liquidity_weight,
      self.momentum_book_imbalance_weight,
    )
    for path, weights in (
      ("pullback", pullback_weights),
      ("momentum", momentum_weights),
    ):
      if any(not math.isfinite(value) or value < 0 for value in weights):
        raise ValueError(f"{path} weights must be finite and non-negative")
      if not math.isclose(sum(weights), 100.0, abs_tol=1e-8):
        raise ValueError(f"{path} weights must sum to 100")

    positive_names = (
      "pullback_depth_score_target_multiplier",
      "momentum_rise_score_target_multiplier",
      "momentum_turnover_score_target_multiplier",
      "momentum_slope_score_target_multiplier",
      "profile_pullback_threshold_min_multiplier",
      "profile_pullback_threshold_max_multiplier",
      "profile_momentum_rise_min_multiplier",
      "profile_momentum_rise_max_multiplier",
      "profile_momentum_velocity_min_ratio",
      "profile_momentum_velocity_max_ratio",
    )
    for name in positive_names:
      value = float(getattr(self, name))
      if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    for name in (
      "pullback_formation_threshold_multiplier",
      "momentum_formation_threshold_multiplier",
    ):
      value = float(getattr(self, name))
      if not math.isfinite(value) or not 0 < value <= 1:
        raise ValueError(f"{name} must be in (0, 1]")
    for lower_name, upper_name in (
      (
        "profile_pullback_threshold_min_multiplier",
        "profile_pullback_threshold_max_multiplier",
      ),
      (
        "profile_momentum_rise_min_multiplier",
        "profile_momentum_rise_max_multiplier",
      ),
      ("profile_momentum_velocity_min_ratio", "profile_momentum_velocity_max_ratio"),
      ("pullback_rebound_score_min_pct", "pullback_rebound_score_max_pct"),
      (
        "pullback_stabilization_score_min_seconds",
        "pullback_stabilization_score_max_seconds",
      ),
      (
        "pullback_turn_slope_score_min_pct_per_second",
        "pullback_turn_slope_score_max_pct_per_second",
      ),
      (
        "pullback_vwap_full_score_max_premium_pct",
        "pullback_vwap_zero_score_premium_pct",
      ),
      (
        "pullback_liquidity_full_score_spread_ticks",
        "pullback_liquidity_zero_score_spread_ticks",
      ),
      ("pullback_volume_score_min_ratio", "pullback_volume_score_max_ratio"),
      (
        "momentum_persistence_score_min_ratio",
        "momentum_persistence_score_max_ratio",
      ),
      (
        "momentum_vwap_zero_score_min_premium_pct",
        "momentum_min_vwap_premium_pct",
      ),
      (
        "momentum_max_vwap_premium_pct",
        "momentum_vwap_zero_score_max_premium_pct",
      ),
      (
        "momentum_liquidity_full_score_spread_ticks",
        "momentum_liquidity_zero_score_spread_ticks",
      ),
      (
        "momentum_book_imbalance_score_min_ratio",
        "momentum_book_imbalance_score_max_ratio",
      ),
      (
        "pullback_chase_penalty_start_premium_pct",
        "pullback_chase_penalty_full_premium_pct",
      ),
      (
        "momentum_overextension_penalty_start_premium_pct",
        "momentum_overextension_penalty_full_premium_pct",
      ),
    ):
      lower = float(getattr(self, lower_name))
      upper = float(getattr(self, upper_name))
      if not math.isfinite(lower) or not math.isfinite(upper) or lower >= upper:
        raise ValueError(f"{lower_name} must be below {upper_name}")
    derived_boundaries = (
      (
        "pullback_depth_score_min_pct",
        self.pullback_threshold_pct
        * self.profile_pullback_threshold_min_multiplier
        * self.pullback_depth_score_target_multiplier,
      ),
      (
        "momentum_rise_score_min_pct",
        self.momentum_min_rise_pct
        * self.profile_momentum_rise_min_multiplier
        * self.momentum_rise_score_target_multiplier,
      ),
      (
        "momentum_turnover_score_min_ratio",
        self.profile_momentum_velocity_min_ratio
        * self.momentum_turnover_score_target_multiplier,
      ),
      (
        "momentum_slope_score_min_pct_per_second",
        self.momentum_min_rise_pct
        * self.profile_momentum_rise_min_multiplier
        / self.momentum_window_seconds
        * self.momentum_slope_score_target_multiplier,
      ),
    )
    for lower_name, upper in derived_boundaries:
      lower = float(getattr(self, lower_name))
      if not math.isfinite(lower) or lower >= upper:
        raise ValueError(f"{lower_name} must be below its resolved score target")
    for name in (
      "pullback_data_quality_penalty_points",
      "pullback_chase_penalty_points",
      "momentum_data_quality_penalty_points",
      "momentum_overextension_penalty_points",
    ):
      value = float(getattr(self, name))
      if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    if not math.isclose(
      self.momentum_overextension_penalty_start_premium_pct,
      self.momentum_max_vwap_premium_pct,
      abs_tol=1e-8,
    ):
      raise ValueError(
        "momentum_overextension_penalty_start_premium_pct must equal "
        "momentum_max_vwap_premium_pct"
      )


@dataclass(frozen=True)
class ScoreComponent:
  name: str
  raw_value: Optional[float]
  contribution: float
  weight: float
  detail: str = ""

  def to_dict(self) -> dict[str, Any]:
    return _jsonable(asdict(self))


@dataclass(frozen=True)
class GateResult:
  code: str
  passed: bool
  detail: str = ""

  def to_dict(self) -> dict[str, Any]:
    return _jsonable(asdict(self))


@dataclass(frozen=True)
class OpportunityFeatures:
  sample_count: int = 0
  coverage_seconds: Optional[float] = None
  max_gap_seconds: Optional[float] = None
  price: Optional[float] = None
  price_tick: Optional[float] = None
  bid_price: Optional[float] = None
  ask_price: Optional[float] = None
  spread_ticks: Optional[float] = None
  spread_pct: Optional[float] = None
  book_imbalance: Optional[float] = None
  session_vwap: Optional[float] = None
  vwap_premium_pct: Optional[float] = None
  return_5s_pct: Optional[float] = None
  return_15s_pct: Optional[float] = None
  return_30s_pct: Optional[float] = None
  return_60s_pct: Optional[float] = None
  return_300s_pct: Optional[float] = None
  price_slope_60s_pct_per_second: Optional[float] = None
  price_acceleration_pct_per_second2: Optional[float] = None
  realized_volatility_60s_pct: Optional[float] = None
  realized_volatility_300s_pct: Optional[float] = None
  window_high: Optional[float] = None
  window_low: Optional[float] = None
  pullback_pct: Optional[float] = None
  rebound_pct: Optional[float] = None
  seconds_since_low: Optional[float] = None
  rebound_slope_pct_per_second: Optional[float] = None
  range_position: Optional[float] = None
  amount_velocity_ratio_15s_60s: Optional[float] = None
  momentum_rise_pct: Optional[float] = None
  momentum_move_seconds: Optional[float] = None
  momentum_window_high: Optional[float] = None
  momentum_range_position: Optional[float] = None
  momentum_baseline_coverage_seconds: Optional[float] = None
  momentum_amount_velocity_ratio: Optional[float] = None

  def to_dict(self) -> dict[str, Any]:
    return _jsonable(asdict(self))


@dataclass(frozen=True)
class PullbackBranchState:
  phase: PullbackPhase = PullbackPhase.OBSERVING
  episode_id: Optional[str] = None
  episode_started_source_time_ms: Optional[int] = None
  episode_started_tick_ordinal: Optional[int] = None
  confirmation_started_at_ms: Optional[int] = None
  confirmation_started_tick_ordinal: Optional[int] = None
  confirmation_ticks: int = 0
  last_score: Optional[float] = None

  def to_dict(self) -> dict[str, Any]:
    return _jsonable(asdict(self))

  @classmethod
  def from_dict(cls, raw: Optional[Mapping[str, Any]]) -> "PullbackBranchState":
    values = dict(raw or {})
    return cls(
      phase=PullbackPhase(values.get("phase", PullbackPhase.OBSERVING.value)),
      episode_id=_optional_str(values.get("episode_id")),
      episode_started_source_time_ms=_optional_int(
        values.get("episode_started_source_time_ms")
      ),
      episode_started_tick_ordinal=_optional_int(
        values.get("episode_started_tick_ordinal")
      ),
      confirmation_started_at_ms=_optional_int(
        values.get("confirmation_started_at_ms")
      ),
      confirmation_started_tick_ordinal=_optional_int(
        values.get("confirmation_started_tick_ordinal")
      ),
      confirmation_ticks=int(values.get("confirmation_ticks", 0) or 0),
      last_score=_optional_float(values.get("last_score")),
    )


@dataclass(frozen=True)
class MomentumBranchState:
  phase: MomentumPhase = MomentumPhase.OBSERVING
  episode_id: Optional[str] = None
  episode_started_source_time_ms: Optional[int] = None
  episode_started_tick_ordinal: Optional[int] = None
  confirmation_started_at_ms: Optional[int] = None
  confirmation_started_tick_ordinal: Optional[int] = None
  confirmation_ticks: int = 0
  last_score: Optional[float] = None

  def to_dict(self) -> dict[str, Any]:
    return _jsonable(asdict(self))

  @classmethod
  def from_dict(cls, raw: Optional[Mapping[str, Any]]) -> "MomentumBranchState":
    values = dict(raw or {})
    return cls(
      phase=MomentumPhase(values.get("phase", MomentumPhase.OBSERVING.value)),
      episode_id=_optional_str(values.get("episode_id")),
      episode_started_source_time_ms=_optional_int(
        values.get("episode_started_source_time_ms")
      ),
      episode_started_tick_ordinal=_optional_int(
        values.get("episode_started_tick_ordinal")
      ),
      confirmation_started_at_ms=_optional_int(
        values.get("confirmation_started_at_ms")
      ),
      confirmation_started_tick_ordinal=_optional_int(
        values.get("confirmation_started_tick_ordinal")
      ),
      confirmation_ticks=int(values.get("confirmation_ticks", 0) or 0),
      last_score=_optional_float(values.get("last_score")),
    )


@dataclass(frozen=True)
class OpportunityCandidate:
  candidate_id: str
  fingerprint: str
  episode_id: str
  path: OpportunityPath
  latched_at_ms: int
  expires_at_ms: int
  source_time_ms: int
  tick_ordinal: int
  price: float
  score: float
  policy_version: str
  feature_schema_version: int
  reference_profile_version: str
  reference_profile_schema_version: int

  def __post_init__(self) -> None:
    if not self.candidate_id or not self.fingerprint or not self.episode_id:
      raise ValueError("candidate identity fields are required")
    if OpportunityPath(self.path) == OpportunityPath.NONE:
      raise ValueError("candidate path cannot be NONE")
    object.__setattr__(self, "path", OpportunityPath(self.path))
    if self.latched_at_ms < 0 or self.source_time_ms < 0 or self.tick_ordinal < 0:
      raise ValueError("candidate source times and ordinal must be non-negative")
    if self.expires_at_ms <= self.source_time_ms:
      raise ValueError("candidate expiry must be after its source time")
    if not _valid_price(self.price):
      raise ValueError("candidate price must be finite and positive")
    if not math.isfinite(self.score) or not 0 <= self.score <= 100:
      raise ValueError("candidate score must be finite and in [0, 100]")
    if (
      not self.policy_version
      or self.feature_schema_version <= 0
      or not self.reference_profile_version
      or self.reference_profile_schema_version <= 0
    ):
      raise ValueError("candidate policy and feature versions are required")

  def to_dict(self) -> dict[str, Any]:
    return _jsonable(asdict(self))

  @classmethod
  def from_dict(cls, raw: Mapping[str, Any]) -> "OpportunityCandidate":
    return cls(
      candidate_id=str(raw.get("candidate_id", "") or ""),
      fingerprint=str(raw.get("fingerprint", "") or ""),
      episode_id=str(raw.get("episode_id", "") or ""),
      path=OpportunityPath(str(raw.get("path", OpportunityPath.NONE.value))),
      latched_at_ms=int(raw.get("latched_at_ms", 0) or 0),
      expires_at_ms=int(raw.get("expires_at_ms", 0) or 0),
      source_time_ms=int(raw.get("source_time_ms", 0) or 0),
      tick_ordinal=int(raw.get("tick_ordinal", 0) or 0),
      price=_float(raw.get("price")),
      score=_float(raw.get("score")),
      policy_version=str(raw.get("policy_version", "") or ""),
      feature_schema_version=int(raw.get("feature_schema_version", 0) or 0),
      reference_profile_version=str(raw.get("reference_profile_version", "") or ""),
      reference_profile_schema_version=int(
        raw.get("reference_profile_schema_version", 0) or 0
      ),
    )


@dataclass(frozen=True)
class OpportunityState:
  schema_version: int = OPPORTUNITY_STATE_SCHEMA_VERSION
  instrument_code: str = ""
  trade_date: str = ""
  continuity_generation: Optional[str] = None
  data_health: DataHealth = DataHealth.WARMING
  health_reasons: tuple[str, ...] = ()
  samples: tuple[OpportunitySample, ...] = ()
  pullback: PullbackBranchState = field(default_factory=PullbackBranchState)
  momentum: MomentumBranchState = field(default_factory=MomentumBranchState)
  candidate: Optional[OpportunityCandidate] = None
  candidate_status: CandidateStatus = CandidateStatus.NONE
  candidate_suppressed: bool = False
  candidate_awaiting_approval: bool = False
  rearm_started_at_ms: Optional[int] = None

  def __post_init__(self) -> None:
    if self.schema_version != OPPORTUNITY_STATE_SCHEMA_VERSION:
      raise ValueError(
        f"unsupported opportunity state schema_version={self.schema_version}"
      )
    object.__setattr__(self, "data_health", DataHealth(self.data_health))
    object.__setattr__(self, "candidate_status", CandidateStatus(self.candidate_status))
    object.__setattr__(self, "samples", tuple(self.samples))
    object.__setattr__(self, "health_reasons", tuple(self.health_reasons))
    if self.candidate is None and self.candidate_status != CandidateStatus.NONE:
      raise ValueError("candidate_status requires a candidate")
    if self.candidate is not None and self.candidate_status == CandidateStatus.NONE:
      raise ValueError("candidate requires a non-NONE candidate_status")
    if self.candidate is None and (
      self.candidate_suppressed or self.candidate_awaiting_approval
    ):
      raise ValueError("candidate lifecycle flags require a candidate")
    if self.candidate_suppressed and self.candidate_awaiting_approval:
      raise ValueError("candidate cannot be suppressed and awaiting approval")
    if (
      self.candidate_status == CandidateStatus.AWAITING_APPROVAL
      and not self.candidate_awaiting_approval
    ):
      raise ValueError("AWAITING_APPROVAL status requires its lifecycle flag")
    if (
      self.candidate_status == CandidateStatus.SUPPRESSED
      and not self.candidate_suppressed
    ):
      raise ValueError("SUPPRESSED status requires its lifecycle flag")
    if self.candidate_awaiting_approval and self.candidate_status not in {
      CandidateStatus.AWAITING_APPROVAL,
      CandidateStatus.REARMING,
    }:
      raise ValueError("awaiting approval flag conflicts with candidate status")
    if self.candidate_suppressed and self.candidate_status not in {
      CandidateStatus.SUPPRESSED,
      CandidateStatus.REARMING,
    }:
      raise ValueError("suppressed flag conflicts with candidate status")
    if self.samples:
      if (
        not self.instrument_code
        or not self.trade_date
        or self.continuity_generation is None
      ):
        raise ValueError("sampled opportunity state requires source identity metadata")
      for item in self.samples:
        if (
          item.instrument_code != self.instrument_code
          or item.trade_date != self.trade_date
        ):
          raise ValueError("opportunity state samples cross instrument or trade date")
        if item.continuity_generation != self.continuity_generation:
          raise ValueError("opportunity state samples cross continuity generations")
      if any(
        right.source_identity <= left.source_identity
        for left, right in zip(self.samples, self.samples[1:])
      ):
        raise ValueError("opportunity state samples must be strictly causal")

  @classmethod
  def initial(
    cls,
    *,
    instrument_code: str = "",
    trade_date: str = "",
  ) -> "OpportunityState":
    return cls(instrument_code=instrument_code, trade_date=trade_date)

  def to_dict(self) -> dict[str, Any]:
    return {
      "schema_version": self.schema_version,
      "instrument_code": self.instrument_code,
      "trade_date": self.trade_date,
      "continuity_generation": self.continuity_generation,
      "data_health": self.data_health.value,
      "health_reasons": list(self.health_reasons),
      "samples": [sample.to_dict() for sample in self.samples],
      "pullback": self.pullback.to_dict(),
      "momentum": self.momentum.to_dict(),
      "candidate": self.candidate.to_dict() if self.candidate is not None else None,
      "candidate_status": self.candidate_status.value,
      "candidate_suppressed": self.candidate_suppressed,
      "candidate_awaiting_approval": self.candidate_awaiting_approval,
      "rearm_started_at_ms": self.rearm_started_at_ms,
    }

  @classmethod
  def from_dict(cls, raw: Mapping[str, Any]) -> "OpportunityState":
    version = int(raw.get("schema_version", 0) or 0)
    if version != OPPORTUNITY_STATE_SCHEMA_VERSION:
      raise ValueError(f"unsupported opportunity state schema_version={version}")
    candidate_raw = raw.get("candidate")
    return cls(
      schema_version=version,
      instrument_code=str(raw.get("instrument_code", "") or ""),
      trade_date=str(raw.get("trade_date", "") or ""),
      continuity_generation=_optional_str(raw.get("continuity_generation")),
      data_health=DataHealth(str(raw.get("data_health", DataHealth.WARMING.value))),
      health_reasons=tuple(str(item) for item in raw.get("health_reasons", []) or []),
      samples=tuple(
        OpportunitySample.from_dict(_mapping(item))
        for item in raw.get("samples", []) or []
      ),
      pullback=PullbackBranchState.from_dict(_optional_mapping(raw.get("pullback"))),
      momentum=MomentumBranchState.from_dict(_optional_mapping(raw.get("momentum"))),
      candidate=(
        OpportunityCandidate.from_dict(_mapping(candidate_raw))
        if candidate_raw is not None
        else None
      ),
      candidate_status=CandidateStatus(
        str(raw.get("candidate_status", CandidateStatus.NONE.value))
      ),
      candidate_suppressed=bool(raw.get("candidate_suppressed", False)),
      candidate_awaiting_approval=bool(raw.get("candidate_awaiting_approval", False)),
      rearm_started_at_ms=_optional_int(raw.get("rearm_started_at_ms")),
    )


@dataclass(frozen=True)
class OpportunityPathEvaluation:
  path: OpportunityPath
  phase: str
  score: Optional[float]
  preview: bool
  candidate_ready: bool
  components: tuple[ScoreComponent, ...]
  hard_gates: tuple[GateResult, ...]
  blockers: tuple[str, ...]

  def to_dict(self) -> dict[str, Any]:
    return {
      "path": self.path.value,
      "phase": self.phase,
      "score": self.score,
      "preview": self.preview,
      "candidate_ready": self.candidate_ready,
      "components": [item.to_dict() for item in self.components],
      "hard_gates": [item.to_dict() for item in self.hard_gates],
      "blockers": list(self.blockers),
    }


@dataclass(frozen=True)
class OpportunityEvaluation:
  instrument_code: str
  trade_date: str
  evaluated_at_ms: int
  source_time_ms: int
  tick_ordinal: int
  continuity_generation: str
  data_health: DataHealth
  data_health_reasons: tuple[str, ...]
  features: OpportunityFeatures
  pullback: OpportunityPathEvaluation
  momentum: OpportunityPathEvaluation
  selected_path: OpportunityPath
  opportunity_score: Optional[float]
  hard_gates: tuple[GateResult, ...]
  blockers: tuple[str, ...]
  candidate_status: CandidateStatus
  candidate_id: Optional[str]
  candidate_fingerprint: Optional[str]
  candidate_created_at_ms: Optional[int]
  candidate_expires_at_ms: Optional[int]
  episode_id: Optional[str]
  policy_version: str
  feature_schema_version: int
  reference_profile_version: Optional[str]
  reference_profile_schema_version: Optional[int]

  def to_dict(self) -> dict[str, Any]:
    return {
      "instrument_code": self.instrument_code,
      "trade_date": self.trade_date,
      "evaluated_at_ms": self.evaluated_at_ms,
      "source_time_ms": self.source_time_ms,
      "tick_ordinal": self.tick_ordinal,
      "continuity_generation": self.continuity_generation,
      "data_health": self.data_health.value,
      "data_health_reasons": list(self.data_health_reasons),
      "features": self.features.to_dict(),
      "pullback": self.pullback.to_dict(),
      "momentum": self.momentum.to_dict(),
      "selected_path": self.selected_path.value,
      "opportunity_score": self.opportunity_score,
      "hard_gates": [item.to_dict() for item in self.hard_gates],
      "blockers": list(self.blockers),
      "candidate_status": self.candidate_status.value,
      "candidate_id": self.candidate_id,
      "candidate_fingerprint": self.candidate_fingerprint,
      "candidate_created_at_ms": self.candidate_created_at_ms,
      "candidate_expires_at_ms": self.candidate_expires_at_ms,
      "episode_id": self.episode_id,
      "policy_version": self.policy_version,
      "feature_schema_version": self.feature_schema_version,
      "reference_profile_version": self.reference_profile_version,
      "reference_profile_schema_version": self.reference_profile_schema_version,
    }


@dataclass(frozen=True)
class OpportunityReduction:
  state: OpportunityState
  evaluation: OpportunityEvaluation
  candidate_created: Optional[OpportunityCandidate] = None
  # ``accepted`` describes whether the source identity was eligible for the
  # normal causal reduction. ``ignored`` is deliberately separate: an
  # invalid quote remains an auditable observation, while a duplicate or
  # out-of-order source must not create any durable evaluation/patch.
  accepted: bool = True
  ignored: bool = False
  ignored_reason: Optional[str] = None

  def to_dict(self) -> dict[str, Any]:
    return {
      "state": self.state.to_dict(),
      "evaluation": self.evaluation.to_dict(),
      "candidate_created": (
        self.candidate_created.to_dict() if self.candidate_created is not None else None
      ),
      "accepted": self.accepted,
      "ignored": self.ignored,
      "ignored_reason": self.ignored_reason,
    }


@dataclass(frozen=True)
class _FeatureAnchors:
  pullback_high: Optional[OpportunitySample] = None
  pullback_low: Optional[OpportunitySample] = None
  momentum_low: Optional[OpportunitySample] = None


@dataclass(frozen=True)
class _ResolvedThresholds:
  pullback_pct: float
  momentum_rise_pct: float
  momentum_amount_velocity_ratio: float
  pullback_max_spread_ticks: int
  momentum_max_spread_ticks: int


def transition_candidate(
  state: OpportunityState,
  control: CandidateControl,
  *,
  source_time_ms: int,
) -> OpportunityState:
  """Apply a candidate lifecycle transition without consuming a market sample.

  The application layer calls this after durably creating an approval request
  or when suppressing a candidate.  TTL is evaluated only against the supplied
  causal source time; no wall clock is read here.
  """

  if source_time_ms < 0:
    raise ValueError("candidate transition source_time_ms must be non-negative")
  candidate = state.candidate
  if candidate is None:
    return state
  has_control = bool(
    control.awaiting_approval_candidate_id or control.suppress_candidate_id
  )
  if source_time_ms < candidate.source_time_ms:
    if has_control:
      raise ValueError("candidate transition cannot precede candidate source time")
    return state

  status = state.candidate_status
  suppressed = state.candidate_suppressed
  awaiting = state.candidate_awaiting_approval
  if source_time_ms >= candidate.expires_at_ms:
    status = CandidateStatus.SUPPRESSED
    suppressed = True
    awaiting = False
  elif control.awaiting_approval_candidate_id == candidate.candidate_id:
    status = CandidateStatus.AWAITING_APPROVAL
    suppressed = False
    awaiting = True
  elif control.suppress_candidate_id == candidate.candidate_id:
    status = CandidateStatus.SUPPRESSED
    suppressed = True
    awaiting = False
  else:
    return state

  pullback = state.pullback
  momentum = state.momentum
  if candidate.path == OpportunityPath.PULLBACK_REBOUND:
    pullback = replace(
      pullback,
      phase=(
        PullbackPhase.SUPPRESSED
        if status == CandidateStatus.SUPPRESSED
        else PullbackPhase.CANDIDATE_LATCHED
      ),
    )
  else:
    momentum = replace(
      momentum,
      phase=(
        MomentumPhase.SUPPRESSED
        if status == CandidateStatus.SUPPRESSED
        else MomentumPhase.CANDIDATE_LATCHED
      ),
    )
  return replace(
    state,
    pullback=pullback,
    momentum=momentum,
    candidate_status=status,
    candidate_suppressed=suppressed,
    candidate_awaiting_approval=awaiting,
  )


def reduce_opportunity(
  state: OpportunityState,
  sample: OpportunitySample,
  *,
  gate_context: Optional[OpportunityGateContext] = None,
  policy: Optional[OpportunityPolicy] = None,
  reference_profile: Optional[OpportunityReferenceProfile] = None,
  candidate_control: Optional[CandidateControl] = None,
) -> OpportunityReduction:
  """Reduce one sample into a new serializable state and evaluation.

  The function never mutates ``state``.  Duplicate or out-of-order source
  identities do not advance the state or candidate confirmation counters.
  """

  config = policy or OpportunityPolicy()
  gates = gate_context or OpportunityGateContext()
  control = candidate_control or CandidateControl()
  if state.schema_version != OPPORTUNITY_STATE_SCHEMA_VERSION:
    raise ValueError("opportunity state schema version mismatch")
  if state.instrument_code and state.instrument_code != sample.instrument_code:
    raise ValueError("opportunity state instrument does not match sample")

  working = state
  if working.trade_date and working.trade_date != sample.trade_date:
    working = OpportunityState.initial(
      instrument_code=sample.instrument_code,
      trade_date=sample.trade_date,
    )
  elif not working.instrument_code or not working.trade_date:
    working = replace(
      working,
      instrument_code=sample.instrument_code,
      trade_date=sample.trade_date,
    )

  generation_changed = (
    working.continuity_generation is not None
    and working.continuity_generation != sample.continuity_generation
  )
  # Source identity is checked before *any* candidate lifecycle transition.
  # This includes TTL and explicit approval/suppression controls: a duplicate
  # or an older source must be an ignored observation only.
  if working.samples and not generation_changed:
    last = working.samples[-1]
    if sample.source_identity == last.source_identity:
      return _nonadvancing_reduction(
        working,
        sample,
        config,
        gates,
        DataHealth.DEGRADED,
        ("DUPLICATE_SOURCE_IDENTITY",),
        reference_profile,
        accepted=False,
        ignored=True,
      )
    if sample.source_identity < last.source_identity:
      return _nonadvancing_reduction(
        working,
        sample,
        config,
        gates,
        DataHealth.DEGRADED,
        ("OUT_OF_ORDER_SOURCE_IDENTITY",),
        reference_profile,
        accepted=False,
        ignored=True,
      )

  # Quote validity is checked before candidate lifecycle transitions. An
  # invalid quote remains an explicit audit observation, but must not advance
  # TTL or approval/suppression state.
  if not _valid_price(sample.price):
    return _nonadvancing_reduction(
      working,
      sample,
      config,
      gates,
      DataHealth.INSUFFICIENT,
      ("INVALID_PRICE",),
      reference_profile,
      accepted=False,
      ignored=False,
    )

  if generation_changed:
    reset = OpportunityState(
      instrument_code=sample.instrument_code,
      trade_date=sample.trade_date,
      continuity_generation=sample.continuity_generation,
      data_health=DataHealth.CONTINUITY_LOST,
      health_reasons=("CONTINUITY_GENERATION_CHANGED",),
      samples=(sample,),
    )
    return _evaluate_state(
      reset,
      sample,
      config,
      gates,
      reference_profile,
      force_health=DataHealth.CONTINUITY_LOST,
      force_health_reasons=reset.health_reasons,
    )

  if working.samples:
    last = working.samples[-1]
    if _cumulative_counter_rolled_back(last, sample):
      reset = OpportunityState(
        instrument_code=sample.instrument_code,
        trade_date=sample.trade_date,
        continuity_generation=sample.continuity_generation,
        data_health=DataHealth.DEGRADED,
        health_reasons=("CUMULATIVE_COUNTER_ROLLBACK",),
        samples=(sample,),
      )
      return _evaluate_state(
        reset,
        sample,
        config,
        gates,
        reference_profile,
        force_health=DataHealth.DEGRADED,
        force_health_reasons=reset.health_reasons,
      )

  # Only a causally accepted source may advance candidate lifecycle state.
  working = transition_candidate(
    working,
    control,
    source_time_ms=sample.source_time_ms,
  )

  samples = _bounded_samples((*working.samples, sample), config)
  observed = replace(
    working,
    continuity_generation=sample.continuity_generation,
    samples=samples,
  )
  return _evaluate_state(
    observed,
    sample,
    config,
    gates,
    reference_profile,
  )


def _evaluate_state(
  state: OpportunityState,
  sample: OpportunitySample,
  policy: OpportunityPolicy,
  gate_context: OpportunityGateContext,
  reference_profile: Optional[OpportunityReferenceProfile],
  *,
  force_health: Optional[DataHealth] = None,
  force_health_reasons: Sequence[str] = (),
) -> OpportunityReduction:
  features, anchors = _extract_features(state.samples, policy)
  profile_issue = _reference_profile_issue(reference_profile, sample.trade_date)
  profile_ready = profile_issue is None
  thresholds = _resolve_thresholds(policy, reference_profile if profile_ready else None)
  health, health_reasons = _data_health(features, sample, policy)
  if force_health == DataHealth.CONTINUITY_LOST:
    health = force_health
    health_reasons = tuple(force_health_reasons)
  elif _quote_is_stale(sample, gate_context, policy):
    health = DataHealth.STALE
    health_reasons = (
      "QUOTE_STALE",
      *((profile_issue,) if profile_issue is not None else ()),
      *(tuple(force_health_reasons) if force_health is not None else health_reasons),
    )
  elif profile_issue is not None:
    health = DataHealth.INSUFFICIENT
    health_reasons = (
      profile_issue,
      *(tuple(force_health_reasons) if force_health is not None else health_reasons),
    )
  elif force_health is not None:
    health = force_health
    health_reasons = tuple(force_health_reasons)

  if profile_ready:
    pullback_phase, pullback_episode = _pullback_shape(
      features,
      anchors,
      state,
      sample,
      policy,
      thresholds,
    )
    momentum_phase, momentum_episode = _momentum_shape(
      features,
      anchors,
      state,
      sample,
      policy,
      thresholds,
    )
  else:
    pullback_phase, pullback_episode = PullbackPhase.OBSERVING, None
    momentum_phase, momentum_episode = MomentumPhase.BASELINING, None
  global_gates = _global_gates(
    sample,
    gate_context,
    health,
    reference_profile,
    policy,
  )
  pullback_calculable = profile_ready and _pullback_score_calculable(features, policy)
  momentum_calculable = profile_ready and _momentum_score_calculable(features, policy)
  pullback_components = (
    _score_pullback(features, health, policy, thresholds) if pullback_calculable else ()
  )
  momentum_components = (
    _score_momentum(features, health, policy, thresholds) if momentum_calculable else ()
  )
  pullback_score = _score_total(pullback_components) if pullback_calculable else None
  momentum_score = _score_total(momentum_components) if momentum_calculable else None
  pullback_gates = (
    *global_gates,
    *_pullback_gates(features, policy, thresholds),
  )
  momentum_gates = (
    *global_gates,
    *_momentum_gates(features, policy, thresholds),
  )
  pullback_blockers = _path_blockers(
    pullback_gates,
    phase_confirmed=pullback_phase == PullbackPhase.REBOUND_CONFIRMING,
    score=pullback_score,
    policy=policy,
    pattern_code="PULLBACK_PATTERN_NOT_CONFIRMED",
  )
  momentum_blockers = _path_blockers(
    momentum_gates,
    phase_confirmed=momentum_phase == MomentumPhase.ACCELERATING,
    score=momentum_score,
    policy=policy,
    pattern_code="MOMENTUM_PATTERN_NOT_CONFIRMED",
  )
  pullback_qualifies = not pullback_blockers
  momentum_qualifies = policy.momentum_enabled and not momentum_blockers

  previous_pullback = state.pullback
  if (
    previous_pullback.confirmation_started_at_ms is not None
    and not _lifecycle_started_in_current_session(
      previous_pullback.confirmation_started_at_ms,
      sample,
      gate_context,
      policy,
    )
  ):
    previous_pullback = replace(
      previous_pullback,
      confirmation_started_at_ms=None,
      confirmation_started_tick_ordinal=None,
      confirmation_ticks=0,
    )
  previous_momentum = state.momentum
  if (
    previous_momentum.confirmation_started_at_ms is not None
    and not _lifecycle_started_in_current_session(
      previous_momentum.confirmation_started_at_ms,
      sample,
      gate_context,
      policy,
    )
  ):
    previous_momentum = replace(
      previous_momentum,
      confirmation_started_at_ms=None,
      confirmation_started_tick_ordinal=None,
      confirmation_ticks=0,
    )

  pullback_state = _advance_pullback_branch(
    previous_pullback,
    pullback_phase,
    pullback_episode,
    anchors.pullback_high,
    sample,
    pullback_score,
    pullback_qualifies,
  )
  momentum_state = _advance_momentum_branch(
    previous_momentum,
    momentum_phase,
    momentum_episode,
    anchors.momentum_low,
    sample,
    momentum_score,
    momentum_qualifies,
  )

  candidate = state.candidate
  candidate_status = state.candidate_status
  candidate_suppressed = state.candidate_suppressed
  candidate_awaiting_approval = state.candidate_awaiting_approval
  rearm_started_at_ms = state.rearm_started_at_ms
  candidate_created: Optional[OpportunityCandidate] = None
  lifecycle_path = candidate.path if candidate is not None else OpportunityPath.NONE

  # Once a candidate exists, its branch identity is authoritative until the
  # candidate completes rearming. Rolling-window extrema may change the raw
  # shape anchor, but they must never silently replace the episode or clear the
  # candidate. A valid serialized state already carries the original anchor;
  # the candidate source identity is a deterministic fail-closed fallback for
  # a malformed branch snapshot.
  if candidate is not None and lifecycle_path == OpportunityPath.PULLBACK_REBOUND:
    prior = state.pullback
    same_episode = prior.episode_id == candidate.episode_id
    pullback_state = replace(
      pullback_state,
      episode_id=candidate.episode_id,
      episode_started_source_time_ms=(
        prior.episode_started_source_time_ms
        if same_episode and prior.episode_started_source_time_ms is not None
        else candidate.source_time_ms
      ),
      episode_started_tick_ordinal=(
        prior.episode_started_tick_ordinal
        if same_episode and prior.episode_started_tick_ordinal is not None
        else candidate.tick_ordinal
      ),
      confirmation_started_at_ms=(
        prior.confirmation_started_at_ms if same_episode else None
      ),
      confirmation_started_tick_ordinal=(
        prior.confirmation_started_tick_ordinal if same_episode else None
      ),
      confirmation_ticks=prior.confirmation_ticks if same_episode else 0,
    )
  elif candidate is not None:
    prior = state.momentum
    same_episode = prior.episode_id == candidate.episode_id
    momentum_state = replace(
      momentum_state,
      episode_id=candidate.episode_id,
      episode_started_source_time_ms=(
        prior.episode_started_source_time_ms
        if same_episode and prior.episode_started_source_time_ms is not None
        else candidate.source_time_ms
      ),
      episode_started_tick_ordinal=(
        prior.episode_started_tick_ordinal
        if same_episode and prior.episode_started_tick_ordinal is not None
        else candidate.tick_ordinal
      ),
      confirmation_started_at_ms=(
        prior.confirmation_started_at_ms if same_episode else None
      ),
      confirmation_started_tick_ordinal=(
        prior.confirmation_started_tick_ordinal if same_episode else None
      ),
      confirmation_ticks=prior.confirmation_ticks if same_episode else 0,
    )

  rearmed_now = False
  if candidate is not None:
    session_allowed, _ = _session_is_allowed(gate_context, policy)
    if rearm_started_at_ms is not None and not _lifecycle_started_in_current_session(
      rearm_started_at_ms,
      sample,
      gate_context,
      policy,
    ):
      rearm_started_at_ms = None
    current_score = (
      pullback_score
      if candidate.path == OpportunityPath.PULLBACK_REBOUND
      else momentum_score
    )
    lifecycle_ended = candidate_suppressed or candidate_status in {
      CandidateStatus.SUPPRESSED,
      CandidateStatus.REARMING,
    }
    resting_status = (
      CandidateStatus.SUPPRESSED
      if candidate_suppressed
      else CandidateStatus.AWAITING_APPROVAL
      if candidate_awaiting_approval
      else CandidateStatus.LATCHED
    )
    if lifecycle_ended:
      # Rearming advances only on actual READY evaluations with a calculable
      # score for the candidate's own path. Missing/degraded observations are
      # ignored (ordinary sparse ticks do not break the episode); a qualified
      # score at or above the threshold resets the dwell timer.
      if session_allowed and health == DataHealth.READY and current_score is not None:
        if current_score < policy.rearm_score - _EPSILON:
          if rearm_started_at_ms is None:
            rearm_started_at_ms = sample.source_time_ms
          candidate_status = CandidateStatus.REARMING
          if sample.source_time_ms - rearm_started_at_ms >= policy.rearm_seconds * 1000:
            candidate = None
            candidate_status = CandidateStatus.NONE
            candidate_suppressed = False
            candidate_awaiting_approval = False
            rearm_started_at_ms = None
            rearmed_now = True
        else:
          rearm_started_at_ms = None
          candidate_status = resting_status
      elif session_allowed and rearm_started_at_ms is not None:
        candidate_status = CandidateStatus.REARMING
      else:
        candidate_status = resting_status
    else:
      rearm_started_at_ms = None
      candidate_status = resting_status

  if rearmed_now:
    # Start the next opportunity epoch at the rearm-completing source. Keeping
    # older samples would allow their original anchor to hash back to the same
    # episode_id, violating the one-candidate-per-episode invariant. Reusing
    # the current sample is causal and forces the next episode to establish a
    # new post-rearm anchor after the bounded window has rewarmed.
    reset = OpportunityState(
      instrument_code=sample.instrument_code,
      trade_date=sample.trade_date,
      continuity_generation=sample.continuity_generation,
      data_health=DataHealth.WARMING,
      health_reasons=("REARM_COMPLETED_REWARMING",),
      samples=(sample,),
    )
    return _evaluate_state(
      reset,
      sample,
      policy,
      gate_context,
      reference_profile,
      force_health=DataHealth.WARMING,
      force_health_reasons=reset.health_reasons,
    )

  if candidate is None and not rearmed_now:
    candidate_path = _candidate_path(
      pullback_state,
      momentum_state,
      pullback_score,
      momentum_score,
      sample,
      policy,
    )
    if candidate_path != OpportunityPath.NONE:
      score = (
        pullback_score
        if candidate_path == OpportunityPath.PULLBACK_REBOUND
        else momentum_score
      )
      episode_id = _episode_for_path(
        candidate_path,
        pullback_state,
        momentum_state,
      )
      if episode_id is not None:
        if score is None:
          raise AssertionError("candidate-ready path must have a score")
        if reference_profile is None or not profile_ready:
          raise AssertionError("candidate-ready path must have a valid profile")
        candidate = _build_candidate(
          sample,
          candidate_path,
          episode_id,
          score,
          policy,
          reference_profile,
        )
        candidate_status = CandidateStatus.LATCHED
        candidate_suppressed = False
        candidate_awaiting_approval = False
        candidate_created = candidate

  if candidate is not None:
    if candidate.path == OpportunityPath.PULLBACK_REBOUND:
      pullback_state = replace(
        pullback_state,
        phase=(
          PullbackPhase.SUPPRESSED
          if candidate_status in {CandidateStatus.SUPPRESSED, CandidateStatus.REARMING}
          else PullbackPhase.CANDIDATE_LATCHED
        ),
      )
    else:
      momentum_state = replace(
        momentum_state,
        phase=(
          MomentumPhase.SUPPRESSED
          if candidate_status in {CandidateStatus.SUPPRESSED, CandidateStatus.REARMING}
          else MomentumPhase.CANDIDATE_LATCHED
        ),
      )

  # A live candidate always owns the top-level evaluation projection. The
  # other FSM remains available under its branch for diagnostics, but it can
  # neither replace the selected path nor revalidate the candidate.
  selected_path = (
    candidate.path
    if candidate is not None
    else _select_path(
      pullback_score,
      momentum_score,
      pullback_phase,
      momentum_phase,
      policy,
    )
  )

  next_state = replace(
    state,
    data_health=health,
    health_reasons=tuple(health_reasons),
    pullback=pullback_state,
    momentum=momentum_state,
    candidate=candidate,
    candidate_status=candidate_status,
    candidate_suppressed=candidate_suppressed,
    candidate_awaiting_approval=candidate_awaiting_approval,
    rearm_started_at_ms=rearm_started_at_ms,
  )
  pullback_evaluation = OpportunityPathEvaluation(
    path=OpportunityPath.PULLBACK_REBOUND,
    phase=pullback_state.phase.value,
    score=pullback_score,
    preview=(pullback_score is not None and pullback_score >= policy.preview_score),
    candidate_ready=pullback_qualifies,
    components=pullback_components,
    hard_gates=pullback_gates,
    blockers=pullback_blockers,
  )
  momentum_evaluation = OpportunityPathEvaluation(
    path=OpportunityPath.MOMENTUM_ACCELERATION,
    phase=momentum_state.phase.value,
    score=momentum_score,
    preview=(momentum_score is not None and momentum_score >= policy.preview_score),
    candidate_ready=momentum_qualifies,
    components=momentum_components,
    hard_gates=momentum_gates,
    blockers=momentum_blockers,
  )
  selected_evaluation = (
    pullback_evaluation
    if selected_path == OpportunityPath.PULLBACK_REBOUND
    else momentum_evaluation
    if selected_path == OpportunityPath.MOMENTUM_ACCELERATION
    else None
  )
  evaluation = OpportunityEvaluation(
    instrument_code=sample.instrument_code,
    trade_date=sample.trade_date,
    evaluated_at_ms=max(
      sample.source_time_ms,
      sample.received_at_ms
      if sample.received_at_ms is not None
      else sample.source_time_ms,
    ),
    source_time_ms=sample.source_time_ms,
    tick_ordinal=sample.tick_ordinal,
    continuity_generation=sample.continuity_generation,
    data_health=health,
    data_health_reasons=tuple(health_reasons),
    features=features,
    pullback=pullback_evaluation,
    momentum=momentum_evaluation,
    selected_path=selected_path,
    opportunity_score=(selected_evaluation.score if selected_evaluation else None),
    hard_gates=(
      selected_evaluation.hard_gates if selected_evaluation else global_gates
    ),
    blockers=(
      selected_evaluation.blockers
      if selected_evaluation
      else _failed_codes(global_gates)
    ),
    candidate_status=candidate_status,
    candidate_id=candidate.candidate_id if candidate is not None else None,
    candidate_fingerprint=candidate.fingerprint if candidate is not None else None,
    candidate_created_at_ms=(
      candidate.latched_at_ms if candidate is not None else None
    ),
    candidate_expires_at_ms=(
      candidate.expires_at_ms if candidate is not None else None
    ),
    episode_id=(
      candidate.episode_id
      if candidate is not None
      else _episode_for_path(selected_path, pullback_state, momentum_state)
    ),
    policy_version=policy.policy_version,
    feature_schema_version=policy.feature_schema_version,
    reference_profile_version=(
      reference_profile.profile_version
      if reference_profile is not None and profile_ready
      else None
    ),
    reference_profile_schema_version=(
      reference_profile.profile_schema_version
      if reference_profile is not None and profile_ready
      else None
    ),
  )
  return OpportunityReduction(
    state=next_state,
    evaluation=evaluation,
    candidate_created=candidate_created,
  )


def _nonadvancing_reduction(
  state: OpportunityState,
  sample: OpportunitySample,
  policy: OpportunityPolicy,
  gate_context: OpportunityGateContext,
  health: DataHealth,
  reasons: Sequence[str],
  reference_profile: Optional[OpportunityReferenceProfile],
  *,
  accepted: bool = False,
  ignored: bool = False,
) -> OpportunityReduction:
  evaluated = _evaluate_state(
    state,
    sample,
    policy,
    gate_context,
    reference_profile,
    force_health=health,
    force_health_reasons=reasons,
  )
  # Invalid, duplicate, and out-of-order inputs are audit observations only.
  # They must not alter durable state or advance a confirmation counter.
  return OpportunityReduction(
    state=state,
    evaluation=replace(
      evaluated.evaluation,
      candidate_status=state.candidate_status,
      candidate_id=state.candidate.candidate_id if state.candidate else None,
      candidate_fingerprint=state.candidate.fingerprint if state.candidate else None,
      episode_id=state.candidate.episode_id if state.candidate else None,
    ),
    candidate_created=None,
    accepted=accepted,
    ignored=ignored,
    ignored_reason=(str(reasons[0]) if reasons else None),
  )


def _extract_features(
  samples: Sequence[OpportunitySample],
  policy: OpportunityPolicy,
) -> tuple[OpportunityFeatures, _FeatureAnchors]:
  if not samples:
    return OpportunityFeatures(), _FeatureAnchors()
  ordered = tuple(samples)
  latest = ordered[-1]
  coverage = (
    (latest.source_time_ms - ordered[0].source_time_ms) / 1000.0
    if len(ordered) >= 2
    else None
  )
  max_gap = (
    max(
      (right.source_time_ms - left.source_time_ms) / 1000.0
      for left, right in zip(ordered, ordered[1:])
    )
    if len(ordered) >= 2
    else None
  )
  bid = _positive_or_none(latest.bid_price)
  ask = _positive_or_none(latest.ask_price)
  price_tick = _positive_or_none(latest.price_tick)
  spread_ticks = (
    float(_spread_tick_count(bid, ask, price_tick))
    if bid is not None and ask is not None and price_tick is not None
    else None
  )
  spread_pct = (
    max(0.0, ask - bid) / latest.price * 100.0
    if bid is not None and ask is not None and ask >= bid and latest.price > 0
    else None
  )
  bid_volume = _non_negative_or_none(latest.bid_volume)
  ask_volume = _non_negative_or_none(latest.ask_volume)
  book_imbalance = (
    (bid_volume - ask_volume) / (bid_volume + ask_volume)
    if bid_volume is not None and ask_volume is not None and bid_volume + ask_volume > 0
    else None
  )
  amount = _positive_or_none(latest.cumulative_amount)
  volume = _positive_or_none(latest.cumulative_volume)
  vwap = amount / volume if amount is not None and volume is not None else None
  vwap_premium = (
    (latest.price / vwap - 1.0) * 100.0 if vwap is not None and vwap > 0 else None
  )

  pullback_cutoff = latest.source_time_ms - policy.pullback_lookback_seconds * 1000
  pullback_window = tuple(
    item for item in ordered if item.source_time_ms >= pullback_cutoff
  )
  peak = pullback_window[0]
  high_anchor: Optional[OpportunitySample] = None
  low_anchor: Optional[OpportunitySample] = None
  best_pullback = 0.0
  for item in pullback_window[1:]:
    drawdown = (peak.price - item.price) / peak.price * 100.0
    if drawdown > best_pullback + _EPSILON:
      best_pullback = drawdown
      high_anchor = peak
      low_anchor = item
    if item.price > peak.price:
      peak = item
  window_high = max(item.price for item in pullback_window)
  window_low = min(item.price for item in pullback_window)
  rebound = (
    (latest.price / low_anchor.price - 1.0) * 100.0
    if low_anchor is not None and low_anchor.price > 0
    else None
  )
  seconds_since_low = (
    (latest.source_time_ms - low_anchor.source_time_ms) / 1000.0
    if low_anchor is not None
    else None
  )
  rebound_slope = (
    rebound / seconds_since_low
    if rebound is not None and seconds_since_low is not None and seconds_since_low > 0
    else None
  )
  range_position = (
    (latest.price - window_low) / (window_high - window_low)
    if window_high > window_low
    else None
  )

  momentum_cutoff = latest.source_time_ms - policy.momentum_window_seconds * 1000
  momentum_window = tuple(
    item for item in ordered if item.source_time_ms >= momentum_cutoff
  )
  momentum_low = (
    min(
      momentum_window,
      key=lambda item: (item.price, item.source_time_ms, item.tick_ordinal),
    )
    if len(momentum_window) >= 2
    else None
  )
  momentum_high = max((item.price for item in momentum_window), default=None)
  momentum_rise = (
    (latest.price / momentum_low.price - 1.0) * 100.0
    if momentum_low is not None and momentum_low.price > 0
    else None
  )
  momentum_move_seconds = (
    (latest.source_time_ms - momentum_low.source_time_ms) / 1000.0
    if momentum_low is not None
    else None
  )
  momentum_range_position = (
    (latest.price - momentum_low.price) / (momentum_high - momentum_low.price)
    if momentum_low is not None
    and momentum_high is not None
    and momentum_high > momentum_low.price
    else None
  )
  baseline_coverage: Optional[float] = None
  momentum_velocity_ratio: Optional[float] = None
  if momentum_low is not None:
    baseline_cutoff = (
      momentum_low.source_time_ms - policy.momentum_baseline_seconds * 1000
    )
    baseline = tuple(
      item
      for item in ordered
      if baseline_cutoff <= item.source_time_ms <= momentum_low.source_time_ms
    )
    if baseline:
      baseline_start = baseline[0]
      baseline_coverage = (
        momentum_low.source_time_ms - baseline_start.source_time_ms
      ) / 1000.0
      low_amount = _non_negative_or_none(momentum_low.cumulative_amount)
      start_amount = _non_negative_or_none(baseline_start.cumulative_amount)
      latest_amount = _non_negative_or_none(latest.cumulative_amount)
      if (
        low_amount is not None
        and start_amount is not None
        and latest_amount is not None
        and momentum_move_seconds is not None
        and momentum_move_seconds > 0
        and baseline_coverage > 0
      ):
        move_amount = latest_amount - low_amount
        baseline_amount = low_amount - start_amount
        if move_amount > 0 and baseline_amount > 0:
          momentum_velocity_ratio = (move_amount / momentum_move_seconds) / (
            baseline_amount / baseline_coverage
          )

  returns = {
    seconds: _causal_return(ordered, latest, seconds)
    for seconds in (5, 15, 30, 60, 300)
  }
  slope_60 = returns[60] / 60.0 if returns[60] is not None else None
  prior_15_return = _prior_interval_return(ordered, latest, 15, 30)
  acceleration = (
    (returns[15] / 15.0 - prior_15_return / 15.0) / 15.0
    if returns[15] is not None and prior_15_return is not None
    else None
  )
  amount_velocity_ratio = _short_amount_velocity_ratio(
    ordered,
    latest,
    short_window_seconds=policy.pullback_volume_short_window_seconds,
    baseline_window_seconds=policy.pullback_volume_baseline_window_seconds,
  )

  return (
    OpportunityFeatures(
      sample_count=len(ordered),
      coverage_seconds=coverage,
      max_gap_seconds=max_gap,
      price=latest.price,
      price_tick=price_tick,
      bid_price=bid,
      ask_price=ask,
      spread_ticks=spread_ticks,
      spread_pct=spread_pct,
      book_imbalance=book_imbalance,
      session_vwap=vwap,
      vwap_premium_pct=vwap_premium,
      return_5s_pct=returns[5],
      return_15s_pct=returns[15],
      return_30s_pct=returns[30],
      return_60s_pct=returns[60],
      return_300s_pct=returns[300],
      price_slope_60s_pct_per_second=slope_60,
      price_acceleration_pct_per_second2=acceleration,
      realized_volatility_60s_pct=_realized_volatility(ordered, latest, 60),
      realized_volatility_300s_pct=_realized_volatility(ordered, latest, 300),
      window_high=window_high,
      window_low=window_low,
      pullback_pct=(best_pullback if len(pullback_window) >= 2 else None),
      rebound_pct=rebound,
      seconds_since_low=seconds_since_low,
      rebound_slope_pct_per_second=rebound_slope,
      range_position=range_position,
      amount_velocity_ratio_15s_60s=amount_velocity_ratio,
      momentum_rise_pct=momentum_rise,
      momentum_move_seconds=momentum_move_seconds,
      momentum_window_high=momentum_high,
      momentum_range_position=momentum_range_position,
      momentum_baseline_coverage_seconds=baseline_coverage,
      momentum_amount_velocity_ratio=momentum_velocity_ratio,
    ),
    _FeatureAnchors(
      pullback_high=high_anchor,
      pullback_low=low_anchor,
      momentum_low=momentum_low,
    ),
  )


def _data_health(
  features: OpportunityFeatures,
  sample: OpportunitySample,
  policy: OpportunityPolicy,
) -> tuple[DataHealth, tuple[str, ...]]:
  minimum_samples = min(policy.pullback_min_samples, policy.momentum_min_samples)
  minimum_coverage = min(
    policy.pullback_min_coverage_seconds,
    policy.momentum_min_coverage_seconds,
  )
  if (
    features.sample_count < minimum_samples
    or features.coverage_seconds is None
    or features.coverage_seconds < minimum_coverage
  ):
    return DataHealth.WARMING, ("MINIMUM_COVERAGE_NOT_REACHED",)
  reasons: list[str] = []
  required_fields = tuple(
    dict.fromkeys((*policy.pullback_required_fields, *policy.momentum_required_fields))
  )
  for field_name in required_fields:
    if _required_feature_available(features, field_name):
      continue
    reasons.append(f"REQUIRED_FIELD_{field_name.upper()}_UNAVAILABLE")
  if (
    features.max_gap_seconds is not None
    and policy.sparse_degraded_gap_seconds > 0
    and features.max_gap_seconds > policy.sparse_degraded_gap_seconds
  ):
    reasons.append("SPARSE_SAMPLE_COVERAGE")
  if (
    sample.received_at_ms is not None and sample.received_at_ms < sample.source_time_ms
  ):
    reasons.append("INVALID_RECEIVE_TIME")
  return (DataHealth.DEGRADED, tuple(reasons)) if reasons else (DataHealth.READY, ())


def _pullback_shape(
  features: OpportunityFeatures,
  anchors: _FeatureAnchors,
  state: OpportunityState,
  sample: OpportunitySample,
  policy: OpportunityPolicy,
  thresholds: _ResolvedThresholds,
) -> tuple[PullbackPhase, Optional[str]]:
  pullback = features.pullback_pct
  formation_floor = (
    thresholds.pullback_pct * policy.pullback_formation_threshold_multiplier
  )
  if pullback is None or pullback < formation_floor:
    return PullbackPhase.OBSERVING, None
  episode = _episode_id(
    sample.instrument_code,
    sample.trade_date,
    OpportunityPath.PULLBACK_REBOUND,
    anchors.pullback_high,
    policy.policy_version,
  )
  if pullback < thresholds.pullback_pct:
    return PullbackPhase.PULLBACK_FORMING, episode
  if (
    features.seconds_since_low is None
    or features.seconds_since_low < policy.pullback_stabilization_seconds
    or features.rebound_pct is None
    or features.rebound_pct < policy.pullback_rebound_threshold_pct
  ):
    return PullbackPhase.LOW_STABILIZING, episode
  return PullbackPhase.REBOUND_CONFIRMING, episode


def _momentum_shape(
  features: OpportunityFeatures,
  anchors: _FeatureAnchors,
  state: OpportunityState,
  sample: OpportunitySample,
  policy: OpportunityPolicy,
  thresholds: _ResolvedThresholds,
) -> tuple[MomentumPhase, Optional[str]]:
  del state
  if not policy.momentum_enabled:
    return MomentumPhase.OBSERVING, None
  required_baseline = (
    policy.momentum_baseline_seconds * policy.momentum_baseline_coverage_ratio
  )
  if (
    features.momentum_baseline_coverage_seconds is None
    or features.momentum_baseline_coverage_seconds < required_baseline
  ):
    return MomentumPhase.BASELINING, None
  rise = features.momentum_rise_pct
  if rise is None or rise < (
    thresholds.momentum_rise_pct * policy.momentum_formation_threshold_multiplier
  ):
    return MomentumPhase.OBSERVING, None
  episode = _episode_id(
    sample.instrument_code,
    sample.trade_date,
    OpportunityPath.MOMENTUM_ACCELERATION,
    anchors.momentum_low,
    policy.policy_version,
  )
  if rise < thresholds.momentum_rise_pct:
    return MomentumPhase.MOMENTUM_BUILDING, episode
  if (
    features.vwap_premium_pct is not None
    and features.vwap_premium_pct > policy.momentum_max_vwap_premium_pct
  ):
    return MomentumPhase.OVEREXTENDED, episode
  return MomentumPhase.ACCELERATING, episode


def _global_gates(
  sample: OpportunitySample,
  context: OpportunityGateContext,
  health: DataHealth,
  reference_profile: Optional[OpportunityReferenceProfile],
  policy: OpportunityPolicy,
) -> tuple[GateResult, ...]:
  profile_available = reference_profile is not None
  profile_schema_compatible = (
    reference_profile is not None
    and reference_profile.profile_schema_version
    == OPPORTUNITY_REFERENCE_PROFILE_SCHEMA_VERSION
  )
  profile_causal = (
    profile_schema_compatible
    and reference_profile is not None
    and reference_profile.as_of_trade_date < sample.trade_date
  )
  session_allowed, session_detail = _session_is_allowed(context, policy)
  quote_fresh = not _quote_is_stale(sample, context, policy)
  results = [
    GateResult("CONTINUOUS_SESSION", context.continuous_session),
    GateResult("TRADING_SESSION", session_allowed, session_detail),
    GateResult("QUOTE_FRESH", quote_fresh),
    GateResult(
      "DATA_READY",
      health == DataHealth.READY,
      health.value,
    ),
    GateResult("REFERENCE_PROFILE_AVAILABLE", profile_available),
    GateResult("REFERENCE_PROFILE_SCHEMA_COMPATIBLE", profile_schema_compatible),
    GateResult("REFERENCE_PROFILE_CAUSAL", profile_causal),
  ]
  return tuple(results)


def _pullback_gates(
  features: OpportunityFeatures,
  policy: OpportunityPolicy,
  thresholds: _ResolvedThresholds,
) -> tuple[GateResult, ...]:
  return (
    GateResult(
      "PULLBACK_COVERAGE_READY",
      features.sample_count >= policy.pullback_min_samples
      and features.coverage_seconds is not None
      and features.coverage_seconds >= policy.pullback_min_coverage_seconds,
    ),
    *tuple(
      GateResult(
        f"PULLBACK_REQUIRED_{field_name.upper()}",
        _required_feature_available(features, field_name),
      )
      for field_name in policy.pullback_required_fields
    ),
    GateResult(
      "PULLBACK_SPREAD_ACCEPTABLE",
      features.spread_ticks is not None
      and features.spread_ticks <= thresholds.pullback_max_spread_ticks,
    ),
  )


def _momentum_gates(
  features: OpportunityFeatures,
  policy: OpportunityPolicy,
  thresholds: _ResolvedThresholds,
) -> tuple[GateResult, ...]:
  required_baseline = (
    policy.momentum_baseline_seconds * policy.momentum_baseline_coverage_ratio
  )
  tick_tolerance = policy.momentum_high_tolerance_ticks
  near_high = (
    features.price is not None
    and features.price_tick is not None
    and features.momentum_window_high is not None
    and features.price
    >= features.momentum_window_high - tick_tolerance * features.price_tick - _EPSILON
  )
  return (
    GateResult("MOMENTUM_ENABLED", policy.momentum_enabled),
    GateResult(
      "MOMENTUM_COVERAGE_READY",
      features.sample_count >= policy.momentum_min_samples
      and features.coverage_seconds is not None
      and features.coverage_seconds >= policy.momentum_min_coverage_seconds,
    ),
    GateResult(
      "MOMENTUM_BASELINE_READY",
      features.momentum_baseline_coverage_seconds is not None
      and features.momentum_baseline_coverage_seconds >= required_baseline,
    ),
    GateResult(
      "MOMENTUM_MOVE_DURATION_READY",
      features.momentum_move_seconds is not None
      and features.momentum_move_seconds >= policy.momentum_min_move_seconds,
    ),
    GateResult("MOMENTUM_NEAR_WINDOW_HIGH", near_high),
    GateResult(
      "MOMENTUM_TURNOVER_AVAILABLE",
      features.momentum_amount_velocity_ratio is not None,
    ),
    *tuple(
      GateResult(
        f"MOMENTUM_REQUIRED_{field_name.upper()}",
        _required_feature_available(features, field_name),
      )
      for field_name in policy.momentum_required_fields
    ),
    GateResult(
      "MOMENTUM_SPREAD_ACCEPTABLE",
      features.spread_ticks is not None
      and features.spread_ticks <= thresholds.momentum_max_spread_ticks
      and features.spread_pct is not None
      and features.spread_pct <= policy.momentum_max_spread_pct,
    ),
    GateResult(
      "MOMENTUM_NOT_OVEREXTENDED",
      features.vwap_premium_pct is not None
      and features.vwap_premium_pct <= policy.momentum_max_vwap_premium_pct,
    ),
  )


def _score_pullback(
  features: OpportunityFeatures,
  health: DataHealth,
  policy: OpportunityPolicy,
  thresholds: _ResolvedThresholds,
) -> tuple[ScoreComponent, ...]:
  return (
    _ramp_component(
      "PULLBACK_DEPTH",
      features.pullback_pct,
      policy.pullback_depth_score_min_pct,
      thresholds.pullback_pct * policy.pullback_depth_score_target_multiplier,
      policy.pullback_depth_weight,
    ),
    _ramp_component(
      "REBOUND_STRENGTH",
      features.rebound_pct,
      policy.pullback_rebound_score_min_pct,
      policy.pullback_rebound_score_max_pct,
      policy.pullback_rebound_weight,
    ),
    _ramp_component(
      "LOW_STABILIZATION",
      features.seconds_since_low,
      policy.pullback_stabilization_score_min_seconds,
      policy.pullback_stabilization_score_max_seconds,
      policy.pullback_stabilization_weight,
    ),
    _ramp_component(
      "TURN_SLOPE",
      features.rebound_slope_pct_per_second,
      policy.pullback_turn_slope_score_min_pct_per_second,
      policy.pullback_turn_slope_score_max_pct_per_second,
      policy.pullback_turn_slope_weight,
    ),
    _pullback_vwap_component(features.vwap_premium_pct, policy),
    _liquidity_component(
      "PULLBACK_LIQUIDITY",
      features.spread_ticks,
      policy.pullback_liquidity_full_score_spread_ticks,
      policy.pullback_liquidity_zero_score_spread_ticks,
      policy.pullback_liquidity_weight,
    ),
    _ramp_component(
      "PULLBACK_VOLUME_CONFIRMATION",
      features.amount_velocity_ratio_15s_60s,
      policy.pullback_volume_score_min_ratio,
      policy.pullback_volume_score_max_ratio,
      policy.pullback_volume_weight,
    ),
    _data_penalty(
      health,
      path=OpportunityPath.PULLBACK_REBOUND,
      points=policy.pullback_data_quality_penalty_points,
    ),
    _positive_premium_penalty(features.vwap_premium_pct, policy),
  )


def _pullback_score_calculable(
  features: OpportunityFeatures,
  policy: OpportunityPolicy,
) -> bool:
  return (
    features.price is not None
    and features.pullback_pct is not None
    and features.sample_count >= policy.pullback_min_samples
    and features.coverage_seconds is not None
    and features.coverage_seconds >= policy.pullback_min_coverage_seconds
  )


def _momentum_score_calculable(
  features: OpportunityFeatures,
  policy: OpportunityPolicy,
) -> bool:
  required_baseline = (
    policy.momentum_baseline_seconds * policy.momentum_baseline_coverage_ratio
  )
  return (
    policy.momentum_enabled
    and features.price is not None
    and features.momentum_rise_pct is not None
    and features.sample_count >= policy.momentum_min_samples
    and features.coverage_seconds is not None
    and features.coverage_seconds >= policy.momentum_min_coverage_seconds
    and features.momentum_baseline_coverage_seconds is not None
    and features.momentum_baseline_coverage_seconds >= required_baseline
  )


def _score_momentum(
  features: OpportunityFeatures,
  health: DataHealth,
  policy: OpportunityPolicy,
  thresholds: _ResolvedThresholds,
) -> tuple[ScoreComponent, ...]:
  slope_target = (
    thresholds.momentum_rise_pct
    / float(policy.momentum_window_seconds)
    * policy.momentum_slope_score_target_multiplier
  )
  return (
    _ramp_component(
      "MOMENTUM_RISE",
      features.momentum_rise_pct,
      policy.momentum_rise_score_min_pct,
      thresholds.momentum_rise_pct * policy.momentum_rise_score_target_multiplier,
      policy.momentum_rise_weight,
    ),
    _ramp_component(
      "MOMENTUM_TURNOVER",
      features.momentum_amount_velocity_ratio,
      policy.momentum_turnover_score_min_ratio,
      thresholds.momentum_amount_velocity_ratio
      * policy.momentum_turnover_score_target_multiplier,
      policy.momentum_turnover_weight,
    ),
    _ramp_component(
      "MOMENTUM_SLOPE",
      (
        features.momentum_rise_pct / features.momentum_move_seconds
        if features.momentum_rise_pct is not None
        and features.momentum_move_seconds is not None
        and features.momentum_move_seconds > 0
        else None
      ),
      policy.momentum_slope_score_min_pct_per_second,
      slope_target,
      policy.momentum_slope_weight,
    ),
    _ramp_component(
      "HIGH_PERSISTENCE",
      features.momentum_range_position,
      policy.momentum_persistence_score_min_ratio,
      policy.momentum_persistence_score_max_ratio,
      policy.momentum_persistence_weight,
    ),
    _momentum_vwap_component(
      features.vwap_premium_pct,
      policy.momentum_min_vwap_premium_pct,
      policy.momentum_max_vwap_premium_pct,
      policy.momentum_vwap_zero_score_min_premium_pct,
      policy.momentum_vwap_zero_score_max_premium_pct,
      policy.momentum_vwap_weight,
    ),
    _liquidity_component(
      "MOMENTUM_LIQUIDITY",
      features.spread_ticks,
      policy.momentum_liquidity_full_score_spread_ticks,
      policy.momentum_liquidity_zero_score_spread_ticks,
      policy.momentum_liquidity_weight,
    ),
    _ramp_component(
      "BOOK_IMBALANCE",
      features.book_imbalance,
      policy.momentum_book_imbalance_score_min_ratio,
      policy.momentum_book_imbalance_score_max_ratio,
      policy.momentum_book_imbalance_weight,
    ),
    _data_penalty(
      health,
      path=OpportunityPath.MOMENTUM_ACCELERATION,
      points=policy.momentum_data_quality_penalty_points,
    ),
    _overextension_penalty(
      features.vwap_premium_pct,
      policy,
    ),
  )


def _path_blockers(
  hard_gates: Sequence[GateResult],
  *,
  phase_confirmed: bool,
  score: Optional[float],
  policy: OpportunityPolicy,
  pattern_code: str,
) -> tuple[str, ...]:
  blockers = list(_failed_codes(hard_gates))
  if not phase_confirmed:
    blockers.append(pattern_code)
  if score is None:
    blockers.append("SCORE_UNAVAILABLE")
  elif score + _EPSILON < policy.candidate_score:
    blockers.append("SCORE_BELOW_CANDIDATE")
  return tuple(dict.fromkeys(blockers))


def _advance_pullback_branch(
  previous: PullbackBranchState,
  phase: PullbackPhase,
  episode_id: Optional[str],
  episode_anchor: Optional[OpportunitySample],
  sample: OpportunitySample,
  score: Optional[float],
  qualifies: bool,
) -> PullbackBranchState:
  if episode_id is None:
    return PullbackBranchState(phase=phase, last_score=score)
  # The first source identity that establishes the shape owns the episode.
  # Later rolling-window extrema can refine features but cannot rewrite that
  # causal anchor while the shape remains active.
  stable_episode_id = previous.episode_id or episode_id
  changed = previous.episode_id != stable_episode_id
  started = None if changed else previous.confirmation_started_at_ms
  started_ordinal = (
    None if changed else previous.confirmation_started_tick_ordinal
  )
  ticks = 0 if changed else previous.confirmation_ticks
  if qualifies:
    if started is None:
      started = sample.source_time_ms
      started_ordinal = sample.tick_ordinal
    ticks += 1
  else:
    started = None
    started_ordinal = None
    ticks = 0
  return PullbackBranchState(
    phase=phase,
    episode_id=stable_episode_id,
    episode_started_source_time_ms=(
      previous.episode_started_source_time_ms
      if not changed and previous.episode_started_source_time_ms is not None
      else episode_anchor.source_time_ms
      if episode_anchor is not None
      else None
    ),
    episode_started_tick_ordinal=(
      previous.episode_started_tick_ordinal
      if not changed and previous.episode_started_tick_ordinal is not None
      else episode_anchor.tick_ordinal
      if episode_anchor is not None
      else None
    ),
    confirmation_started_at_ms=started,
    confirmation_started_tick_ordinal=started_ordinal,
    confirmation_ticks=ticks,
    last_score=score,
  )


def _advance_momentum_branch(
  previous: MomentumBranchState,
  phase: MomentumPhase,
  episode_id: Optional[str],
  episode_anchor: Optional[OpportunitySample],
  sample: OpportunitySample,
  score: Optional[float],
  qualifies: bool,
) -> MomentumBranchState:
  if episode_id is None:
    return MomentumBranchState(phase=phase, last_score=score)
  stable_episode_id = previous.episode_id or episode_id
  changed = previous.episode_id != stable_episode_id
  started = None if changed else previous.confirmation_started_at_ms
  started_ordinal = (
    None if changed else previous.confirmation_started_tick_ordinal
  )
  ticks = 0 if changed else previous.confirmation_ticks
  if qualifies:
    if started is None:
      started = sample.source_time_ms
      started_ordinal = sample.tick_ordinal
    ticks += 1
  else:
    started = None
    started_ordinal = None
    ticks = 0
  return MomentumBranchState(
    phase=phase,
    episode_id=stable_episode_id,
    episode_started_source_time_ms=(
      previous.episode_started_source_time_ms
      if not changed and previous.episode_started_source_time_ms is not None
      else episode_anchor.source_time_ms
      if episode_anchor is not None
      else None
    ),
    episode_started_tick_ordinal=(
      previous.episode_started_tick_ordinal
      if not changed and previous.episode_started_tick_ordinal is not None
      else episode_anchor.tick_ordinal
      if episode_anchor is not None
      else None
    ),
    confirmation_started_at_ms=started,
    confirmation_started_tick_ordinal=started_ordinal,
    confirmation_ticks=ticks,
    last_score=score,
  )


def _candidate_path(
  pullback: PullbackBranchState,
  momentum: MomentumBranchState,
  pullback_score: Optional[float],
  momentum_score: Optional[float],
  sample: OpportunitySample,
  policy: OpportunityPolicy,
) -> OpportunityPath:
  pullback_ready = (
    pullback.confirmation_started_at_ms is not None
    and pullback.confirmation_ticks >= policy.candidate_confirm_ticks
    and sample.source_time_ms - pullback.confirmation_started_at_ms
    >= policy.candidate_confirm_seconds * 1000
  )
  momentum_ready = (
    momentum.confirmation_started_at_ms is not None
    and momentum.confirmation_ticks >= policy.candidate_confirm_ticks
    and sample.source_time_ms - momentum.confirmation_started_at_ms
    >= policy.candidate_confirm_seconds * 1000
  )
  if pullback_ready and momentum_ready:
    if pullback_score is None or momentum_score is None:
      raise AssertionError("confirmed branches must have scores")
    if momentum_score > pullback_score + _EPSILON:
      return OpportunityPath.MOMENTUM_ACCELERATION
    if pullback_score > momentum_score + _EPSILON:
      return OpportunityPath.PULLBACK_REBOUND

    # Equal scores are resolved by the first source identity that reached the
    # candidate threshold.  The continuity generation is already fixed by
    # the enclosing state/episode, so the bounded ordinal completes the
    # causal identity without consulting wall clock time.  A malformed or
    # incomplete persisted identity fails closed to the documented fixed
    # Pullback ordering.
    pullback_confirmation = _confirmation_source_identity(pullback)
    momentum_confirmation = _confirmation_source_identity(momentum)
    if (
      pullback_confirmation is not None
      and momentum_confirmation is not None
      and momentum_confirmation < pullback_confirmation
    ):
      return OpportunityPath.MOMENTUM_ACCELERATION
    return OpportunityPath.PULLBACK_REBOUND
  if pullback_ready:
    return OpportunityPath.PULLBACK_REBOUND
  if momentum_ready:
    return OpportunityPath.MOMENTUM_ACCELERATION
  return OpportunityPath.NONE


def _select_path(
  pullback_score: Optional[float],
  momentum_score: Optional[float],
  pullback_phase: PullbackPhase,
  momentum_phase: MomentumPhase,
  policy: OpportunityPolicy,
) -> OpportunityPath:
  del pullback_phase, momentum_phase, policy
  if pullback_score is None and momentum_score is None:
    return OpportunityPath.NONE
  if pullback_score is None:
    return OpportunityPath.MOMENTUM_ACCELERATION
  if momentum_score is None:
    return OpportunityPath.PULLBACK_REBOUND
  pullback_active = pullback_score >= 0
  momentum_active = momentum_score >= 0
  if not pullback_active and not momentum_active:
    return OpportunityPath.NONE
  if momentum_active and momentum_score > pullback_score + _EPSILON:
    return OpportunityPath.MOMENTUM_ACCELERATION
  return OpportunityPath.PULLBACK_REBOUND


def _build_candidate(
  sample: OpportunitySample,
  path: OpportunityPath,
  episode_id: str,
  score: float,
  policy: OpportunityPolicy,
  reference_profile: OpportunityReferenceProfile,
) -> OpportunityCandidate:
  payload = {
    "episode_id": episode_id,
    "feature_schema_version": policy.feature_schema_version,
    "path": path.value,
    "policy_version": policy.policy_version,
    "source_time_ms": sample.source_time_ms,
    "tick_ordinal": sample.tick_ordinal,
  }
  fingerprint = _stable_hash(payload)
  return OpportunityCandidate(
    candidate_id=f"toc_{fingerprint[:24]}",
    fingerprint=fingerprint,
    episode_id=episode_id,
    path=path,
    latched_at_ms=sample.source_time_ms,
    expires_at_ms=sample.source_time_ms + policy.candidate_ttl_seconds * 1000,
    source_time_ms=sample.source_time_ms,
    tick_ordinal=sample.tick_ordinal,
    price=sample.price,
    score=score,
    policy_version=policy.policy_version,
    feature_schema_version=policy.feature_schema_version,
    reference_profile_version=reference_profile.profile_version,
    reference_profile_schema_version=reference_profile.profile_schema_version,
  )


def _episode_id(
  instrument_code: str,
  trade_date: str,
  path: OpportunityPath,
  anchor: Optional[OpportunitySample],
  policy_version: str,
) -> Optional[str]:
  if anchor is None:
    return None
  digest = _stable_hash(
    {
      "anchor_source_time_ms": anchor.source_time_ms,
      "anchor_tick_ordinal": anchor.tick_ordinal,
      "continuity_generation": anchor.continuity_generation,
      "instrument_code": instrument_code,
      "path": path.value,
      "policy_version": policy_version,
      "trade_date": trade_date,
    }
  )
  return f"toe_{digest[:24]}"


def _episode_for_path(
  path: OpportunityPath,
  pullback: PullbackBranchState,
  momentum: MomentumBranchState,
) -> Optional[str]:
  if path == OpportunityPath.PULLBACK_REBOUND:
    return pullback.episode_id
  if path == OpportunityPath.MOMENTUM_ACCELERATION:
    return momentum.episode_id
  return None


def _confirmation_source_identity(
  branch: PullbackBranchState | MomentumBranchState,
) -> Optional[tuple[int, int]]:
  if (
    branch.confirmation_started_at_ms is None
    or branch.confirmation_started_tick_ordinal is None
  ):
    return None
  return (
    int(branch.confirmation_started_at_ms),
    int(branch.confirmation_started_tick_ordinal),
  )


def _resolve_thresholds(
  policy: OpportunityPolicy,
  profile: Optional[OpportunityReferenceProfile],
) -> _ResolvedThresholds:
  pullback = policy.pullback_threshold_pct
  momentum_rise = policy.momentum_min_rise_pct
  momentum_velocity = policy.momentum_min_amount_velocity_ratio
  pullback_spread = policy.pullback_max_spread_ticks
  momentum_spread = policy.momentum_max_spread_ticks
  if profile is not None:
    pullback = _clamp(
      profile.pullback_threshold_pct,
      policy.pullback_threshold_pct * policy.profile_pullback_threshold_min_multiplier,
      policy.pullback_threshold_pct * policy.profile_pullback_threshold_max_multiplier,
    )
    momentum_rise = _clamp(
      profile.momentum_rise_threshold_pct,
      policy.momentum_min_rise_pct * policy.profile_momentum_rise_min_multiplier,
      policy.momentum_min_rise_pct * policy.profile_momentum_rise_max_multiplier,
    )
    momentum_velocity = _clamp(
      profile.momentum_amount_velocity_ratio,
      policy.profile_momentum_velocity_min_ratio,
      policy.profile_momentum_velocity_max_ratio,
    )
    pullback_spread = min(
      policy.pullback_max_spread_ticks,
      profile.pullback_max_spread_ticks,
    )
    momentum_spread = min(
      policy.momentum_max_spread_ticks,
      profile.momentum_max_spread_ticks,
    )
  return _ResolvedThresholds(
    pullback_pct=pullback,
    momentum_rise_pct=momentum_rise,
    momentum_amount_velocity_ratio=momentum_velocity,
    pullback_max_spread_ticks=pullback_spread,
    momentum_max_spread_ticks=momentum_spread,
  )


def _reference_profile_issue(
  profile: Optional[OpportunityReferenceProfile],
  trade_date: str,
) -> Optional[str]:
  if profile is None:
    return "REFERENCE_PROFILE_MISSING"
  if profile.profile_schema_version != OPPORTUNITY_REFERENCE_PROFILE_SCHEMA_VERSION:
    return "REFERENCE_PROFILE_SCHEMA_INCOMPATIBLE"
  if profile.as_of_trade_date >= trade_date:
    return "REFERENCE_PROFILE_NOT_CAUSAL"
  return None


def _bounded_samples(
  samples: Sequence[OpportunitySample],
  policy: OpportunityPolicy,
) -> tuple[OpportunitySample, ...]:
  latest_ms = samples[-1].source_time_ms
  cutoff = latest_ms - policy.state_window_seconds * 1000
  bounded = tuple(item for item in samples if item.source_time_ms >= cutoff)
  if len(bounded) > policy.max_samples:
    bounded = bounded[-policy.max_samples :]
  return bounded


def _cumulative_counter_rolled_back(
  previous: OpportunitySample,
  current: OpportunitySample,
) -> bool:
  for name in ("cumulative_amount", "cumulative_volume"):
    before = _non_negative_or_none(getattr(previous, name))
    after = _non_negative_or_none(getattr(current, name))
    if before is not None and after is not None and after + _EPSILON < before:
      return True
  return False


def _causal_return(
  samples: Sequence[OpportunitySample],
  latest: OpportunitySample,
  seconds: int,
) -> Optional[float]:
  target = latest.source_time_ms - seconds * 1000
  baseline = _last_at_or_before(samples, target)
  if baseline is None or baseline.price <= 0:
    return None
  return (latest.price / baseline.price - 1.0) * 100.0


def _prior_interval_return(
  samples: Sequence[OpportunitySample],
  latest: OpportunitySample,
  recent_seconds: int,
  older_seconds: int,
) -> Optional[float]:
  recent = _last_at_or_before(samples, latest.source_time_ms - recent_seconds * 1000)
  older = _last_at_or_before(samples, latest.source_time_ms - older_seconds * 1000)
  if recent is None or older is None or older.price <= 0:
    return None
  return (recent.price / older.price - 1.0) * 100.0


def _last_at_or_before(
  samples: Sequence[OpportunitySample],
  target_ms: int,
) -> Optional[OpportunitySample]:
  result: Optional[OpportunitySample] = None
  for item in samples:
    if item.source_time_ms <= target_ms:
      result = item
    else:
      break
  return result


def _realized_volatility(
  samples: Sequence[OpportunitySample],
  latest: OpportunitySample,
  seconds: int,
) -> Optional[float]:
  cutoff = latest.source_time_ms - seconds * 1000
  window = [item for item in samples if item.source_time_ms >= cutoff]
  if len(window) < 3:
    return None
  returns = [
    math.log(right.price / left.price) * 100.0
    for left, right in zip(window, window[1:])
    if left.price > 0 and right.price > 0
  ]
  if len(returns) < 2:
    return None
  mean = sum(returns) / len(returns)
  variance = sum((item - mean) ** 2 for item in returns) / (len(returns) - 1)
  return math.sqrt(max(0.0, variance))


def _short_amount_velocity_ratio(
  samples: Sequence[OpportunitySample],
  latest: OpportunitySample,
  *,
  short_window_seconds: int,
  baseline_window_seconds: int,
) -> Optional[float]:
  short_start = _last_at_or_before(
    samples,
    latest.source_time_ms - short_window_seconds * 1000,
  )
  baseline_start = _last_at_or_before(
    samples,
    latest.source_time_ms - (short_window_seconds + baseline_window_seconds) * 1000,
  )
  if short_start is None or baseline_start is None:
    return None
  latest_amount = _non_negative_or_none(latest.cumulative_amount)
  short_amount = _non_negative_or_none(short_start.cumulative_amount)
  baseline_amount = _non_negative_or_none(baseline_start.cumulative_amount)
  if latest_amount is None or short_amount is None or baseline_amount is None:
    return None
  short_seconds = (latest.source_time_ms - short_start.source_time_ms) / 1000.0
  baseline_seconds = (
    short_start.source_time_ms - baseline_start.source_time_ms
  ) / 1000.0
  short_delta = latest_amount - short_amount
  baseline_delta = short_amount - baseline_amount
  if (
    short_seconds <= 0
    or baseline_seconds <= 0
    or short_delta <= 0
    or baseline_delta <= 0
  ):
    return None
  return (short_delta / short_seconds) / (baseline_delta / baseline_seconds)


def _ramp_component(
  name: str,
  value: Optional[float],
  low: float,
  high: float,
  weight: float,
) -> ScoreComponent:
  if value is None or not math.isfinite(value):
    return ScoreComponent(name, None, 0.0, weight, "UNAVAILABLE")
  if high <= low:
    normalized = 1.0 if value >= high else 0.0
  else:
    normalized = _clamp((value - low) / (high - low), 0.0, 1.0)
  return ScoreComponent(name, value, normalized * weight, weight)


def _pullback_vwap_component(
  value: Optional[float],
  policy: OpportunityPolicy,
) -> ScoreComponent:
  weight = policy.pullback_vwap_weight
  if value is None:
    return ScoreComponent("PULLBACK_VWAP_POSITION", None, 0.0, weight, "UNAVAILABLE")
  full = policy.pullback_vwap_full_score_max_premium_pct
  zero = policy.pullback_vwap_zero_score_premium_pct
  contribution = (
    weight
    if value <= full
    else weight * _clamp(1.0 - (value - full) / (zero - full), 0.0, 1.0)
  )
  return ScoreComponent("PULLBACK_VWAP_POSITION", value, contribution, weight)


def _momentum_vwap_component(
  value: Optional[float],
  minimum: float,
  maximum: float,
  lower_zero: float,
  upper_zero: float,
  weight: float,
) -> ScoreComponent:
  if value is None:
    return ScoreComponent("MOMENTUM_VWAP_REGIME", None, 0.0, weight, "UNAVAILABLE")
  if value < minimum:
    contribution = weight * _clamp(
      (value - lower_zero) / max(minimum - lower_zero, _EPSILON),
      0.0,
      1.0,
    )
  elif value <= maximum:
    contribution = weight
  else:
    contribution = weight * _clamp(
      1.0 - (value - maximum) / max(upper_zero - maximum, _EPSILON),
      0.0,
      1.0,
    )
  return ScoreComponent("MOMENTUM_VWAP_REGIME", value, contribution, weight)


def _liquidity_component(
  name: str,
  spread_ticks: Optional[float],
  full_score_ticks: float,
  zero_score_ticks: float,
  weight: float,
) -> ScoreComponent:
  if spread_ticks is None:
    return ScoreComponent(name, None, 0.0, weight, "UNAVAILABLE")
  contribution = weight * _clamp(
    (zero_score_ticks - spread_ticks) / (zero_score_ticks - full_score_ticks),
    0.0,
    1.0,
  )
  return ScoreComponent(name, spread_ticks, contribution, weight)


def _data_penalty(
  health: DataHealth,
  *,
  path: OpportunityPath,
  points: float,
) -> ScoreComponent:
  penalty = -points if health == DataHealth.DEGRADED else 0.0
  return ScoreComponent(
    "DATA_QUALITY_PENALTY",
    None,
    penalty,
    points,
    f"{path.value}:{health.value}",
  )


def _positive_premium_penalty(
  value: Optional[float],
  policy: OpportunityPolicy,
) -> ScoreComponent:
  start = policy.pullback_chase_penalty_start_premium_pct
  full = policy.pullback_chase_penalty_full_premium_pct
  points = policy.pullback_chase_penalty_points
  penalty = (
    -points * _clamp((value - start) / (full - start), 0.0, 1.0)
    if value is not None and value > start
    else 0.0
  )
  return ScoreComponent("PULLBACK_CHASE_PENALTY", value, penalty, points)


def _overextension_penalty(
  value: Optional[float],
  policy: OpportunityPolicy,
) -> ScoreComponent:
  start = policy.momentum_overextension_penalty_start_premium_pct
  full = policy.momentum_overextension_penalty_full_premium_pct
  points = policy.momentum_overextension_penalty_points
  penalty = (
    -points * _clamp((value - start) / (full - start), 0.0, 1.0)
    if value is not None and value > start
    else 0.0
  )
  return ScoreComponent("MOMENTUM_OVEREXTENSION_PENALTY", value, penalty, points)


def _score_total(components: Sequence[ScoreComponent]) -> float:
  value = sum(item.contribution for item in components)
  return round(_clamp(value, 0.0, 100.0), 8)


def _required_feature_available(
  features: OpportunityFeatures,
  field_name: str,
) -> bool:
  if field_name == "bid_price":
    return features.bid_price is not None
  if field_name == "ask_price":
    return features.ask_price is not None
  if field_name in {"bid_volume", "ask_volume"}:
    return features.book_imbalance is not None
  if field_name in {"cumulative_amount", "cumulative_volume"}:
    return features.session_vwap is not None
  raise AssertionError(f"unsupported required sample field: {field_name}")


def _quote_is_stale(
  sample: OpportunitySample,
  context: OpportunityGateContext,
  policy: OpportunityPolicy,
) -> bool:
  if context.quote_stale:
    return True
  if sample.received_at_ms is None or sample.received_at_ms < sample.source_time_ms:
    return False
  return sample.received_at_ms - sample.source_time_ms > policy.max_quote_age_ms


def _session_is_allowed(
  context: OpportunityGateContext,
  policy: OpportunityPolicy,
) -> tuple[bool, str]:
  if not context.continuous_session:
    return False, str(context.session_code or "NON_CONTINUOUS")
  if context.session_code is None or context.local_second_of_day is None:
    # Direct pure-domain callers may only know the already classified boolean.
    # Production StrategyInput always supplies the structured session identity.
    return True, "CLASSIFIED_CONTINUOUS"
  if context.session_code not in policy.allowed_session_codes:
    return False, context.session_code
  if context.session_code == "CONTINUOUS_AM":
    start = _parse_time_seconds(
      policy.continuous_am_start_time,
      "continuous_am_start_time",
    )
    end = _parse_time_seconds(policy.continuous_am_end_time, "continuous_am_end_time")
  elif context.session_code == "CONTINUOUS_PM":
    start = _parse_time_seconds(
      policy.continuous_pm_start_time,
      "continuous_pm_start_time",
    )
    end = _parse_time_seconds(policy.continuous_pm_end_time, "continuous_pm_end_time")
  else:
    return False, context.session_code
  current = int(context.local_second_of_day)
  latest_candidate_second = (
    end - policy.close_protection_seconds - policy.candidate_ttl_seconds
  )
  allowed = start <= current <= latest_candidate_second
  return allowed, (
    f"{context.session_code}:{_format_time_seconds(start)}-"
    f"{_format_time_seconds(latest_candidate_second)}"
  )


def _lifecycle_started_in_current_session(
  started_at_ms: int,
  sample: OpportunitySample,
  context: OpportunityGateContext,
  policy: OpportunityPolicy,
) -> bool:
  if context.session_code is None or context.local_second_of_day is None:
    return True
  if context.session_code == "CONTINUOUS_AM":
    session_start = _parse_time_seconds(
      policy.continuous_am_start_time,
      "continuous_am_start_time",
    )
  elif context.session_code == "CONTINUOUS_PM":
    session_start = _parse_time_seconds(
      policy.continuous_pm_start_time,
      "continuous_pm_start_time",
    )
  else:
    return False
  elapsed_in_session_ms = (int(context.local_second_of_day) - session_start) * 1000
  if elapsed_in_session_ms < 0:
    return False
  current_session_started_at_ms = sample.source_time_ms - elapsed_in_session_ms
  return int(started_at_ms) >= current_session_started_at_ms


def _parse_time_seconds(value: Any, field_name: str) -> int:
  if not isinstance(value, str):
    raise ValueError(f"{field_name} must use HH:MM or HH:MM:SS")
  parts = value.strip().split(":")
  if len(parts) not in {2, 3} or any(not part.isdecimal() for part in parts):
    raise ValueError(f"{field_name} must use HH:MM or HH:MM:SS")
  hour, minute = int(parts[0]), int(parts[1])
  second = int(parts[2]) if len(parts) == 3 else 0
  if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
    raise ValueError(f"{field_name} must be a valid wall-clock time")
  return hour * 3600 + minute * 60 + second


def _format_time_seconds(value: int) -> str:
  hour, remainder = divmod(int(value), 3600)
  minute, second = divmod(remainder, 60)
  return f"{hour:02d}:{minute:02d}:{second:02d}"


def _spread_tick_count(
  bid_price: float,
  ask_price: float,
  price_tick: float,
) -> Optional[int]:
  try:
    tick = Decimal(str(price_tick))
    bid = Decimal(str(bid_price))
    ask = Decimal(str(ask_price))
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
  return max(0, int(ask_tick - bid_tick))


def _failed_codes(gates: Sequence[GateResult]) -> tuple[str, ...]:
  return tuple(item.code for item in gates if not item.passed)


def _stable_hash(payload: Mapping[str, Any]) -> str:
  encoded = json.dumps(
    payload,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=True,
  ).encode("utf-8")
  return hashlib.sha256(encoded).hexdigest()


def _valid_price(value: Any) -> bool:
  return isinstance(value, (int, float)) and math.isfinite(float(value)) and value > 0


def _positive_or_none(value: Any) -> Optional[float]:
  number = _optional_float(value)
  return number if number is not None and number > 0 else None


def _non_negative_or_none(value: Any) -> Optional[float]:
  number = _optional_float(value)
  return number if number is not None and number >= 0 else None


def _float(value: Any, *, default: float = 0.0) -> float:
  try:
    number = float(value)
  except (TypeError, ValueError):
    return default
  return number


def _optional_float(value: Any) -> Optional[float]:
  if value is None or value == "":
    return None
  try:
    number = float(value)
  except (TypeError, ValueError):
    return None
  return number if math.isfinite(number) else None


def _optional_int(value: Any) -> Optional[int]:
  if value is None or value == "":
    return None
  return int(value)


def _optional_str(value: Any) -> Optional[str]:
  if value is None or value == "":
    return None
  return str(value)


def _mapping(value: Any) -> Mapping[str, Any]:
  return value if isinstance(value, Mapping) else {}


def _optional_mapping(value: Any) -> Optional[Mapping[str, Any]]:
  return value if isinstance(value, Mapping) else None


def _clamp(value: float, minimum: float, maximum: float) -> float:
  return min(maximum, max(minimum, value))


def _jsonable(value: Any) -> Any:
  if isinstance(value, Enum):
    return value.value
  if isinstance(value, dict):
    return {key: _jsonable(item) for key, item in value.items()}
  if isinstance(value, (list, tuple)):
    return [_jsonable(item) for item in value]
  return value


__all__ = [
  "OPPORTUNITY_FEATURE_SCHEMA_VERSION",
  "OPPORTUNITY_POLICY_VERSION",
  "OPPORTUNITY_REFERENCE_PROFILE_SCHEMA_VERSION",
  "OPPORTUNITY_STATE_SCHEMA_VERSION",
  "CandidateControl",
  "CandidateStatus",
  "DataHealth",
  "GateResult",
  "MomentumBranchState",
  "MomentumPhase",
  "OpportunityCandidate",
  "OpportunityEvaluation",
  "OpportunityFeatures",
  "OpportunityGateContext",
  "OpportunityPath",
  "OpportunityPathEvaluation",
  "OpportunityPolicy",
  "OpportunityReduction",
  "OpportunityReferenceProfile",
  "OpportunitySample",
  "OpportunityState",
  "PullbackBranchState",
  "PullbackPhase",
  "ScoreComponent",
  "reduce_opportunity",
  "transition_candidate",
]
