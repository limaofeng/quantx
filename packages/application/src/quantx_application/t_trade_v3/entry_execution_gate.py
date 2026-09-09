"""Deterministic pre-execution revalidation; no sizing or order construction.

The runtime supplies the latest accepted Tick and authoritative binding/sequence
witnesses. DELAY is a result for this evaluation only: a subsequent attempt must
rebuild candidate, scoring and allocation evidence through the complete path.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from quantx_contracts import ExecutionEnvironment
from quantx_domain.trading.t_assistant_execution import (
  TAssistantEntryAuthorization,
  TAssistantExecution,
  TAssistantScorerMode,
)
from quantx_domain.trading.t_assistant_market_state import AcceptedTMarketTick
from quantx_domain.trading.t_trade_opportunity_engine import OpportunityCandidate

_PRICE_FIELDS = frozenset({"price", "bid_price", "ask_price"})
_DEPTH_FIELDS = frozenset({"bid_volume", "ask_volume"})
_FIELDS = _PRICE_FIELDS | _DEPTH_FIELDS


class EntryExecutionDecision(StrEnum):
  ALLOW = "ALLOW"
  DELAY = "DELAY"
  REJECT = "REJECT"


@dataclass(frozen=True)
class MarketDataCapabilityManifest:
  """Frozen source-version capabilities, restricted to rules this gate supports."""

  version: str
  required_fields: frozenset[str]
  optional_fields: frozenset[str] = frozenset()

  def __post_init__(self) -> None:
    required = frozenset(self.required_fields)
    optional = frozenset(self.optional_fields)
    if not self.version or required & optional or (required | optional) - _FIELDS:
      raise ValueError("invalid market capability manifest")
    if not _PRICE_FIELDS <= required:
      raise ValueError("price and two-sided book are required gate capabilities")
    object.__setattr__(self, "required_fields", required)
    object.__setattr__(self, "optional_fields", optional)


@dataclass(frozen=True)
class EntryExecutionBinding:
  """Allocation's immutable binding, compared to current authoritative facts."""

  candidate_fingerprint: str
  config_version_id: str
  config_snapshot_hash: str
  policy_version: str
  gate_policy_version: str
  feature_schema_version: int
  capability_version: str
  scorer_mode: TAssistantScorerMode = TAssistantScorerMode.RULE_ONLY
  model_binding_hash: str | None = None

  def __post_init__(self) -> None:
    _require_int(self.feature_schema_version, "feature_schema_version", minimum=1)
    if (
      not all(
        (
          self.candidate_fingerprint,
          self.config_version_id,
          self.config_snapshot_hash,
          self.policy_version,
          self.gate_policy_version,
          self.capability_version,
        )
      )
      or self.feature_schema_version < 1
    ):
      raise ValueError("entry binding requires complete versioned identity")
    object.__setattr__(self, "scorer_mode", TAssistantScorerMode(self.scorer_mode))


@dataclass(frozen=True)
class EntryExecutionGatePolicy:
  """Explicit frozen limits; prices/depth constrain eligibility, never order size."""

  version: str
  quote_max_age_ms: int
  max_price_deviation_bps: float
  max_spread_bps: float
  minimum_book_depth: float | None = None
  minimum_book_imbalance: float | None = None

  def __post_init__(self) -> None:
    _require_int(self.quote_max_age_ms, "quote_max_age_ms", minimum=1)
    if not self.version or self.quote_max_age_ms <= 0:
      raise ValueError("gate policy requires version and positive quote age")
    for value in (self.max_price_deviation_bps, self.max_spread_bps):
      if not math.isfinite(value) or value < 0:
        raise ValueError("gate price limits must be finite and non-negative")
    if self.minimum_book_depth is not None and (
      not math.isfinite(self.minimum_book_depth) or self.minimum_book_depth < 0
    ):
      raise ValueError("minimum book depth must be finite and non-negative")
    if self.minimum_book_imbalance is not None and (
      not math.isfinite(self.minimum_book_imbalance)
      or not -1 <= self.minimum_book_imbalance <= 1
    ):
      raise ValueError("minimum book imbalance must be within [-1, 1]")


@dataclass(frozen=True)
class EntryExecutionGateInput:
  """Runtime witnesses for one evaluation.

  stream_id/continuity_generation/candidate_* are frozen at candidate allocation.
  latest_* and current_binding are freshly read authoritative projections. Ring
  generation detects an intervening gap even when the latest Tick is clean.
  quality_fields belongs to this exact Tick, never an older quote.
  """

  candidate: OpportunityCandidate
  instrument_code: str
  intent_id: str
  intent_active: bool
  candidate_valid: bool
  intent_created_at_ms: int
  intent_expires_at_ms: int
  evaluated_at_ms: int
  execution_environment: ExecutionEnvironment
  frozen_binding: EntryExecutionBinding
  current_binding: EntryExecutionBinding
  policy: EntryExecutionGatePolicy
  capabilities: MarketDataCapabilityManifest
  stream_id: str
  continuity_generation: str
  candidate_accepted_sequence: int
  candidate_ring_generation: int
  latest_ring_generation: int
  latest_accepted_sequence: int
  latest_tick: AcceptedTMarketTick | None
  quality_fields: frozenset[str]

  def __post_init__(self) -> None:
    for name in ("intent_created_at_ms", "intent_expires_at_ms", "evaluated_at_ms"):
      _require_int(getattr(self, name), name)
    for name in (
      "candidate_accepted_sequence",
      "candidate_ring_generation",
      "latest_ring_generation",
      "latest_accepted_sequence",
    ):
      _require_int(getattr(self, name), name, minimum=1)
    # Existing domain dataclasses are not strict integer parsers. Revalidate the
    # nested evidence here so NaN cannot bypass comparison-based TTL checks.
    for name in ("latched_at_ms", "expires_at_ms", "source_time_ms", "tick_ordinal"):
      _require_int(getattr(self.candidate, name), f"candidate.{name}")
    if self.latest_tick is not None:
      tick = self.latest_tick
      _require_int(tick.accepted_sequence, "tick.accepted_sequence", minimum=1)
      _require_int(tick.received_at_ms, "tick.received_at_ms")
      _require_int(tick.market_fence_sequence, "tick.market_fence_sequence", minimum=1)
      for name in ("source_time_ms", "tick_ordinal"):
        _require_int(getattr(tick.sample, name), f"tick.sample.{name}")
      if tick.sample.received_at_ms is not None:
        _require_int(tick.sample.received_at_ms, "tick.sample.received_at_ms")
    if not all(
      (self.instrument_code, self.intent_id, self.stream_id, self.continuity_generation)
    ):
      raise ValueError("entry gate requires intent, symbol and market identities")
    if not isinstance(self.intent_active, bool) or not isinstance(
      self.candidate_valid, bool
    ):
      raise ValueError("entry gate requires authoritative lifecycle witnesses")
    if (
      self.intent_created_at_ms < 0
      or self.evaluated_at_ms < 0
      or self.intent_expires_at_ms <= self.intent_created_at_ms
      or self.candidate_accepted_sequence < 1
      or self.candidate_ring_generation < 1
      or self.latest_ring_generation < 1
      or self.latest_accepted_sequence < 1
    ):
      raise ValueError("invalid entry gate time or sequence")
    object.__setattr__(
      self, "execution_environment", ExecutionEnvironment(self.execution_environment)
    )
    object.__setattr__(self, "quality_fields", frozenset(self.quality_fields))


@dataclass(frozen=True)
class EntryExecutionGateResult:
  decision: EntryExecutionDecision
  reason_codes: tuple[str, ...]
  candidate_id: str
  intent_id: str
  evaluated_at_ms: int
  expires_at_ms: int
  accepted_sequence: int | None


class EntryExecutionGate:
  """One pure evaluation; callers must not treat ALLOW as trade authorization."""

  @staticmethod
  def evaluate(request: EntryExecutionGateInput) -> EntryExecutionGateResult:
    return EntryExecutionGate._evaluate(request, ExecutionEnvironment.PAPER)

  @staticmethod
  def evaluate_live(
    request: EntryExecutionGateInput, *, execution: TAssistantExecution
  ) -> EntryExecutionGateResult:
    """Explicit LIVE binding; ALLOW still requires confirmation, sizing and risk."""
    if (
      not isinstance(execution, TAssistantExecution)
      or execution.environment is not ExecutionEnvironment.LIVE
      or request.execution_environment is not ExecutionEnvironment.LIVE
      or not execution.can_produce_entry
      or execution.entry_authorization
      is not TAssistantEntryAuthorization.MANUAL_CONFIRM
      or execution.scorer_mode is not TAssistantScorerMode.RULE_ONLY
      or request.frozen_binding.config_version_id != execution.config_version_id
      or request.frozen_binding.config_snapshot_hash != execution.config_snapshot_hash
      or request.frozen_binding.policy_version != execution.policy_version
      or request.frozen_binding.feature_schema_version
      != execution.feature_schema_version
      or int(execution.readiness.as_of.timestamp() * 1000) > request.evaluated_at_ms
    ):
      raise ValueError("T_ENTRY_LIVE_EXECUTION_BINDING_INVALID")
    return EntryExecutionGate._evaluate(request, ExecutionEnvironment.LIVE)

  @staticmethod
  def evaluate_backtest(
    request: EntryExecutionGateInput, *, execution
  ) -> EntryExecutionGateResult:
    """Explicit frozen BACKTEST binding; the PAPER entry point stays PAPER-only."""
    if (
      execution.environment is not ExecutionEnvironment.BACKTEST
      or request.execution_environment is not ExecutionEnvironment.BACKTEST
      or request.frozen_binding.config_version_id != execution.config_version_id
      or request.frozen_binding.config_snapshot_hash != execution.config_snapshot_hash
      or request.frozen_binding.policy_version != execution.policy_version
      or request.frozen_binding.feature_schema_version
      != execution.feature_schema_version
      or execution.scorer_mode is not TAssistantScorerMode.RULE_ONLY
    ):
      raise ValueError("T_ENTRY_BACKTEST_EXECUTION_BINDING_INVALID")
    return EntryExecutionGate._evaluate(request, ExecutionEnvironment.BACKTEST)

  @staticmethod
  def _evaluate(
    request: EntryExecutionGateInput, environment: ExecutionEnvironment
  ) -> EntryExecutionGateResult:
    r = request
    candidate = r.candidate
    binding = r.frozen_binding
    tick = r.latest_tick
    rejects: list[str] = []
    delays: list[str] = []
    expiry = min(candidate.expires_at_ms, r.intent_expires_at_ms)
    if not r.intent_active:
      rejects.append("T_ENTRY_INTENT_INACTIVE")
    if not r.candidate_valid:
      rejects.append("T_ENTRY_CANDIDATE_INVALID")
    if r.candidate_ring_generation != r.latest_ring_generation:
      rejects.append("T_ENTRY_MARKET_DISCONTINUITY")
    if r.execution_environment is not environment:
      rejects.append("T_ENTRY_ENVIRONMENT_UNSUPPORTED")
    if (
      binding.scorer_mode is not TAssistantScorerMode.RULE_ONLY
      or r.current_binding.scorer_mode is not TAssistantScorerMode.RULE_ONLY
    ):
      rejects.append("T_ENTRY_SCORER_UNSUPPORTED")
    if (
      binding.model_binding_hash is not None
      or r.current_binding.model_binding_hash is not None
    ):
      rejects.append("T_ENTRY_MODEL_BINDING_INVALID")
    if r.evaluated_at_ms >= expiry:
      rejects.append("T_ENTRY_TTL_EXPIRED")
    if (
      max(candidate.source_time_ms, candidate.latched_at_ms, r.intent_created_at_ms)
      > r.evaluated_at_ms
    ):
      rejects.append("T_ENTRY_FUTURE_TIMESTAMP")
    if (
      binding.candidate_fingerprint != candidate.fingerprint
      or binding.candidate_fingerprint != r.current_binding.candidate_fingerprint
    ):
      rejects.append("T_ENTRY_FINGERPRINT_MISMATCH")
    if (
      binding.config_version_id != r.current_binding.config_version_id
      or binding.config_snapshot_hash != r.current_binding.config_snapshot_hash
    ):
      rejects.append("T_ENTRY_CONFIG_BINDING_MISMATCH")
    if not (
      binding.policy_version
      == r.current_binding.policy_version
      == candidate.policy_version
    ):
      rejects.append("T_ENTRY_POLICY_BINDING_MISMATCH")
    if not (
      binding.gate_policy_version
      == r.current_binding.gate_policy_version
      == r.policy.version
    ):
      rejects.append("T_ENTRY_GATE_POLICY_BINDING_MISMATCH")
    if not (
      binding.feature_schema_version
      == r.current_binding.feature_schema_version
      == candidate.feature_schema_version
    ):
      rejects.append("T_ENTRY_SCHEMA_BINDING_MISMATCH")
    if not (
      binding.capability_version
      == r.current_binding.capability_version
      == r.capabilities.version
    ):
      rejects.append("T_ENTRY_CAPABILITY_BINDING_MISMATCH")
    if tick is None:
      delays.append("T_ENTRY_QUOTE_MISSING")
    else:
      sample = tick.sample
      if tick.instrument_code != r.instrument_code:
        rejects.append("T_ENTRY_INSTRUMENT_MISMATCH")
      if (
        tick.stream_id != r.stream_id
        or sample.continuity_generation != r.continuity_generation
        or tick.discontinuity_reason is not None
      ):
        rejects.append("T_ENTRY_MARKET_DISCONTINUITY")
      if tick.accepted_sequence != r.latest_accepted_sequence:
        delays.append("T_ENTRY_QUOTE_NOT_LATEST")
      if tick.accepted_sequence < r.candidate_accepted_sequence or (
        sample.source_time_ms,
        sample.tick_ordinal,
      ) < (candidate.source_time_ms, candidate.tick_ordinal):
        rejects.append("T_ENTRY_QUOTE_PRECEDES_CANDIDATE")
      times = [sample.source_time_ms, tick.received_at_ms]
      if sample.received_at_ms is not None:
        times.append(sample.received_at_ms)
      if max(times) > r.evaluated_at_ms:
        rejects.append("T_ENTRY_FUTURE_TIMESTAMP")
      elif any(r.evaluated_at_ms - time > r.policy.quote_max_age_ms for time in times):
        delays.append("T_ENTRY_QUOTE_STALE")
      declared = r.capabilities.required_fields | r.capabilities.optional_fields
      usable = {
        name
        for name in declared & r.quality_fields
        if _usable(getattr(sample, name), positive=name in _PRICE_FIELDS)
      }
      missing = r.capabilities.required_fields - usable
      if missing:
        delays.append("T_ENTRY_REQUIRED_CAPABILITY_UNAVAILABLE")
        delays.extend(f"T_ENTRY_FIELD_UNAVAILABLE:{name}" for name in sorted(missing))
      if _PRICE_FIELDS <= usable:
        bid, ask = sample.bid_price, sample.ask_price
        assert bid is not None and ask is not None
        if bid > ask:
          delays.append("T_ENTRY_BOOK_INVALID")
        else:
          if (ask - bid) / ((ask + bid) / 2) * 10_000 > r.policy.max_spread_bps:
            delays.append("T_ENTRY_SPREAD_EXCEEDED")
          # Include the buy-side touch: a stable last trade must not hide a jump.
          if (
            max(abs(sample.price / candidate.price - 1), abs(ask / candidate.price - 1))
            * 10_000
            > r.policy.max_price_deviation_bps
          ):
            delays.append("T_ENTRY_PRICE_DEVIATION_EXCEEDED")
      if _DEPTH_FIELDS <= usable:
        bid_depth, ask_depth = sample.bid_volume, sample.ask_volume
        assert bid_depth is not None and ask_depth is not None
        if (
          r.policy.minimum_book_depth is not None
          and min(bid_depth, ask_depth) < r.policy.minimum_book_depth
        ):
          delays.append("T_ENTRY_BOOK_DEPTH_INSUFFICIENT")
        if r.policy.minimum_book_imbalance is not None:
          total = bid_depth + ask_depth
          if total <= 0:
            delays.append("T_ENTRY_BOOK_IMBALANCE_UNAVAILABLE")
          elif (bid_depth - ask_depth) / total < r.policy.minimum_book_imbalance:
            delays.append("T_ENTRY_BOOK_IMBALANCE_INSUFFICIENT")
    decision = (
      EntryExecutionDecision.REJECT
      if rejects
      else EntryExecutionDecision.DELAY
      if delays
      else EntryExecutionDecision.ALLOW
    )
    return EntryExecutionGateResult(
      decision=decision,
      reason_codes=tuple(dict.fromkeys(rejects + delays)) or ("T_ENTRY_ALLOWED",),
      candidate_id=candidate.candidate_id,
      intent_id=r.intent_id,
      evaluated_at_ms=r.evaluated_at_ms,
      expires_at_ms=expiry,
      accepted_sequence=tick.accepted_sequence if tick is not None else None,
    )


def _usable(value: float | None, *, positive: bool) -> bool:
  return (
    isinstance(value, (float, int))
    and not isinstance(value, bool)
    and math.isfinite(value)
    and (value > 0 if positive else value >= 0)
  )


def _require_int(value: object, name: str, *, minimum: int = 0) -> None:
  if type(value) is not int or value < minimum:
    raise ValueError(f"{name} must be an integer >= {minimum}")
