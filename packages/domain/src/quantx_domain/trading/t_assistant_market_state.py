"""Causal per-symbol delta buffering and reduction for the T assistant."""

from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

from quantx_contracts import ExecutionOwnerRef, ExecutionOwnerType

from .t_assistant_execution import (
  TAssistantEntryReadiness,
  TAssistantExecutionStatus,
  TAssistantSymbolLifecycle,
  canonical_json_payload,
  stable_manifest_hash,
)
from .t_trade_opportunity_engine import (
  CandidateControl,
  OpportunityCandidate,
  OpportunityEvaluation,
  OpportunityGateContext,
  OpportunityPolicy,
  OpportunityReferenceProfile,
  OpportunitySample,
  OpportunityState,
  reduce_opportunity,
  transition_candidate,
)

# P0 frozen v1 market/cycle thresholds.  They are named, exported policy
# constants rather than fallback defaults hidden in a scheduler.
T_ASSISTANT_MARKET_POLICY_VERSION = "t_assistant_market_v1"
T_ASSISTANT_DELTA_RING_CAPACITY = 4096
T_ASSISTANT_DELTA_RING_WINDOW_MS = 600_000
T_ASSISTANT_REDUCER_MAX_LAG_TICKS = 512
T_ASSISTANT_REDUCER_MAX_LAG_MS = 2_000
T_ASSISTANT_MARKET_CAPTURE_MAX_AGE_MS = 10_000
T_ASSISTANT_FUTURE_TIMESTAMP_SKEW_MS = 5_000
T_ASSISTANT_SYMBOL_QUOTE_MAX_AGE_MS = 3_000

T_MARKET_STREAM_NOT_READY = "T_MARKET_STREAM_NOT_READY"
T_MARKET_GENERATION_CHANGED = "T_MARKET_GENERATION_CHANGED"
T_MARKET_SEQUENCE_GAP = "T_MARKET_SEQUENCE_GAP"
T_MARKET_RING_OVERFLOW = "T_MARKET_RING_OVERFLOW"
T_REDUCER_LAG_EXCEEDED = "T_REDUCER_LAG_EXCEEDED"
T_QUOTE_STALE = "T_QUOTE_STALE"
T_SNAPSHOT_STALE = "T_SNAPSHOT_STALE"
T_REWARM_REQUIRED = "T_REWARM_REQUIRED"
T_MARKET_FUTURE_TIMESTAMP = "T_MARKET_FUTURE_TIMESTAMP"


class TickAcceptance(str, Enum):
  ACCEPTED = "ACCEPTED"
  DUPLICATE = "DUPLICATE"
  OUT_OF_ORDER = "OUT_OF_ORDER"
  FUTURE_TIMESTAMP = "FUTURE_TIMESTAMP"


class SymbolDeltaCoverage(str, Enum):
  READY = "READY"
  STREAM_NOT_READY = T_MARKET_STREAM_NOT_READY
  GENERATION_CHANGED = T_MARKET_GENERATION_CHANGED
  SEQUENCE_GAP = T_MARKET_SEQUENCE_GAP
  RING_OVERFLOW = T_MARKET_RING_OVERFLOW
  REDUCER_LAG_EXCEEDED = T_REDUCER_LAG_EXCEEDED

  @property
  def requires_rewarm(self) -> bool:
    return self is not self.READY


@dataclass(frozen=True, order=True)
class TMarketSourceIdentity:
  continuity_generation: str
  source_time_ms: int
  tick_ordinal: int

  def __post_init__(self) -> None:
    if not self.continuity_generation:
      raise ValueError("market source identity requires generation")
    if self.source_time_ms < 0 or self.tick_ordinal < 0:
      raise ValueError("market source identity values must be non-negative")


@dataclass(frozen=True)
class AcceptedTMarketTick:
  stream_id: str
  accepted_sequence: int
  received_at_ms: int
  sample: OpportunitySample
  discontinuity_reason: Optional[str] = None
  market_fence_sequence: Optional[int] = None

  def __post_init__(self) -> None:
    if not self.stream_id:
      raise ValueError("accepted T Tick requires stream_id")
    if self.accepted_sequence < 1 or self.received_at_ms < 0:
      raise ValueError("accepted T Tick sequence/time is invalid")
    if self.discontinuity_reason not in {
      None,
      T_MARKET_GENERATION_CHANGED,
      T_MARKET_SEQUENCE_GAP,
    }:
      raise ValueError("accepted T Tick discontinuity reason is invalid")
    fence = (
      self.accepted_sequence
      if self.market_fence_sequence is None
      else int(self.market_fence_sequence)
    )
    if fence < 1:
      raise ValueError("accepted T Tick market fence must be positive")
    object.__setattr__(self, "market_fence_sequence", fence)

  @property
  def instrument_code(self) -> str:
    return self.sample.instrument_code

  @property
  def source_identity(self) -> TMarketSourceIdentity:
    return TMarketSourceIdentity(
      self.sample.continuity_generation,
      self.sample.source_time_ms,
      self.sample.tick_ordinal,
    )

  def manifest_item(self) -> dict[str, Any]:
    return {
      "stream_id": self.stream_id,
      "accepted_sequence": self.accepted_sequence,
      "received_at_ms": self.received_at_ms,
      "discontinuity_reason": self.discontinuity_reason,
      "market_fence_sequence": self.market_fence_sequence,
      "sample": self.sample.to_dict(),
    }


@dataclass(frozen=True)
class SymbolMarketCursor:
  stream_id: str
  continuity_generation: str
  ring_generation: int
  accepted_sequence: int
  source_identity: Optional[TMarketSourceIdentity] = None

  def __post_init__(self) -> None:
    if not self.stream_id or not self.continuity_generation:
      raise ValueError("symbol market cursor requires stream and generation")
    if self.ring_generation < 1 or self.accepted_sequence < 0:
      raise ValueError("symbol market cursor values are invalid")

  def to_dict(self) -> dict[str, Any]:
    identity = self.source_identity
    return {
      "stream_id": self.stream_id,
      "continuity_generation": self.continuity_generation,
      "ring_generation": self.ring_generation,
      "accepted_sequence": self.accepted_sequence,
      "source_identity": (
        {
          "continuity_generation": identity.continuity_generation,
          "source_time_ms": identity.source_time_ms,
          "tick_ordinal": identity.tick_ordinal,
        }
        if identity is not None
        else None
      ),
    }

  @classmethod
  def from_dict(cls, raw: Mapping[str, Any]) -> "SymbolMarketCursor":
    identity_raw = raw.get("source_identity")
    identity = None
    if isinstance(identity_raw, Mapping):
      identity = TMarketSourceIdentity(
        continuity_generation=str(identity_raw.get("continuity_generation") or ""),
        source_time_ms=int(identity_raw.get("source_time_ms") or 0),
        tick_ordinal=int(identity_raw.get("tick_ordinal") or 0),
      )
    return cls(
      stream_id=str(raw.get("stream_id") or ""),
      continuity_generation=str(raw.get("continuity_generation") or ""),
      ring_generation=int(raw.get("ring_generation") or 0),
      accepted_sequence=int(raw.get("accepted_sequence") or 0),
      source_identity=identity,
    )


@dataclass(frozen=True)
class TickAcceptanceResult:
  acceptance: TickAcceptance
  reason: Optional[str]
  ring_generation: int


@dataclass(frozen=True)
class SymbolMarketDeltaSlice:
  instrument_code: str
  coverage: SymbolDeltaCoverage
  reason_codes: tuple[str, ...]
  from_cursor: Optional[SymbolMarketCursor]
  from_accepted_sequence_exclusive: int
  to_accepted_sequence_inclusive: int
  ticks: tuple[AcceptedTMarketTick, ...]
  next_cursor: SymbolMarketCursor
  manifest_hash: str

  @property
  def computed_manifest_hash(self) -> str:
    return _delta_slice_manifest_hash(
      instrument_code=self.instrument_code,
      coverage=self.coverage,
      reason_codes=self.reason_codes,
      from_cursor=self.from_cursor,
      next_cursor=self.next_cursor,
      ticks=self.ticks,
    )

  def __post_init__(self) -> None:
    code = str(self.instrument_code or "").strip().upper()
    object.__setattr__(self, "instrument_code", code)
    object.__setattr__(self, "coverage", SymbolDeltaCoverage(self.coverage))
    if not code:
      raise ValueError("symbol delta slice requires instrument_code")
    expected_from = self.from_cursor.accepted_sequence if self.from_cursor else 0
    if self.from_accepted_sequence_exclusive != expected_from:
      raise ValueError("symbol delta slice from cursor mismatch")
    if self.next_cursor.accepted_sequence != self.to_accepted_sequence_inclusive:
      raise ValueError("symbol delta slice next cursor mismatch")
    expected_manifest = self.computed_manifest_hash
    if self.manifest_hash != expected_manifest:
      raise ValueError("symbol delta slice manifest mismatch")

  @property
  def accepted_tick_count(self) -> int:
    return len(self.ticks)


def _delta_slice_manifest_hash(
  *,
  instrument_code: str,
  coverage: SymbolDeltaCoverage,
  reason_codes: Sequence[str],
  from_cursor: Optional[SymbolMarketCursor],
  next_cursor: SymbolMarketCursor,
  ticks: Sequence[AcceptedTMarketTick],
) -> str:
  return stable_manifest_hash(
    {
      "instrument_code": instrument_code,
      "coverage": coverage.value,
      "reason_codes": list(reason_codes),
      "from_cursor": from_cursor.to_dict() if from_cursor is not None else None,
      "next_cursor": next_cursor.to_dict(),
      "ticks": [item.manifest_item() for item in ticks],
    }
  )


class SymbolMarketDeltaRing:
  """Bounded accepted-Tick ring for exactly one symbol.

  The ring does not invent a latest-only fallback.  A generation change,
  sequence gap, overwrite, or hard lag yields an explicit discontinuity slice;
  the reducer must clear its causal state and acknowledge the returned cursor.
  """

  def __init__(
    self,
    instrument_code: str,
    *,
    initial_ring_generation: int = 1,
  ) -> None:
    code = str(instrument_code or "").strip().upper()
    if not code:
      raise ValueError("symbol delta ring requires instrument_code")
    self.instrument_code = code
    self._ticks: deque[AcceptedTMarketTick] = deque()
    self._stream_id = ""
    self._continuity_generation = ""
    if int(initial_ring_generation) < 1:
      raise ValueError("symbol delta ring generation must be positive")
    self._ring_generation = int(initial_ring_generation)
    self._last_sequence = 0
    self._last_identity: Optional[TMarketSourceIdentity] = None
    self._discontinuity_reason: Optional[str] = None

  @property
  def last_sequence(self) -> int:
    return self._last_sequence

  @property
  def ring_generation(self) -> int:
    return self._ring_generation

  @property
  def latest_tick(self) -> Optional[AcceptedTMarketTick]:
    return self._ticks[-1] if self._ticks else None

  @property
  def last_source_time_ms(self) -> Optional[int]:
    return self._last_identity.source_time_ms if self._last_identity else None

  @property
  def stream_id(self) -> str:
    return self._stream_id

  @property
  def continuity_generation(self) -> str:
    return self._continuity_generation

  def align_capture_identity(
    self,
    *,
    stream_id: str,
    continuity_generation: str,
  ) -> None:
    """Bind an empty ring to a captured stream without inventing a Tick."""

    if self._ticks or self._last_identity is not None:
      return
    if not stream_id or not continuity_generation:
      raise ValueError("symbol ring capture identity is required")
    self._stream_id = str(stream_id)
    self._continuity_generation = str(continuity_generation)

  def accept(
    self,
    tick: AcceptedTMarketTick,
    *,
    capture_time_ms: int,
  ) -> TickAcceptanceResult:
    if tick.instrument_code != self.instrument_code:
      raise ValueError("accepted Tick does not belong to symbol ring")
    if (
      tick.sample.source_time_ms
      > capture_time_ms + T_ASSISTANT_FUTURE_TIMESTAMP_SKEW_MS
    ):
      return TickAcceptanceResult(
        TickAcceptance.FUTURE_TIMESTAMP,
        T_MARKET_FUTURE_TIMESTAMP,
        self._ring_generation,
      )

    generation = tick.sample.continuity_generation
    if tick.discontinuity_reason is not None:
      self._reset_for_discontinuity(tick.discontinuity_reason)
    elif self._stream_id and (
      tick.stream_id != self._stream_id or generation != self._continuity_generation
    ):
      self._reset_for_discontinuity(T_MARKET_GENERATION_CHANGED)
    self._stream_id = tick.stream_id
    self._continuity_generation = generation

    identity = tick.source_identity
    if self._last_identity is not None:
      if identity == self._last_identity:
        return TickAcceptanceResult(
          TickAcceptance.DUPLICATE,
          "DUPLICATE_SOURCE_IDENTITY",
          self._ring_generation,
        )
      if (
        identity < self._last_identity or tick.accepted_sequence <= self._last_sequence
      ):
        return TickAcceptanceResult(
          TickAcceptance.OUT_OF_ORDER,
          "OUT_OF_ORDER_SOURCE_IDENTITY",
          self._ring_generation,
        )

    self._ticks.append(tick)
    self._last_sequence = tick.accepted_sequence
    self._last_identity = identity
    self._prune(tick.sample.source_time_ms)
    return TickAcceptanceResult(
      TickAcceptance.ACCEPTED,
      self._discontinuity_reason,
      self._ring_generation,
    )

  def slice_after(
    self,
    cursor: Optional[SymbolMarketCursor],
    *,
    decision_time_ms: int,
    through_accepted_sequence: Optional[int] = None,
    rewarm_replay: bool = False,
  ) -> SymbolMarketDeltaSlice:
    if not self._stream_id or not self._continuity_generation:
      return self._discontinuity_slice(
        cursor,
        SymbolDeltaCoverage.STREAM_NOT_READY,
      )

    target_sequence = self._last_sequence
    eligible_ticks = tuple(
      tick
      for tick in self._ticks
      if through_accepted_sequence is None
      or int(tick.market_fence_sequence or 0) <= int(through_accepted_sequence)
    )
    if eligible_ticks:
      target_sequence = eligible_ticks[-1].accepted_sequence
    elif cursor is not None:
      target_sequence = cursor.accepted_sequence
    latest_identity = (
      eligible_ticks[-1].source_identity
      if eligible_ticks
      else cursor.source_identity
      if cursor is not None
      else None
    )
    next_cursor = SymbolMarketCursor(
      stream_id=self._stream_id,
      continuity_generation=self._continuity_generation,
      ring_generation=self._ring_generation,
      accepted_sequence=(
        eligible_ticks[-1].accepted_sequence
        if eligible_ticks
        else cursor.accepted_sequence
        if cursor is not None
        else 0
      ),
      source_identity=latest_identity,
    )
    if cursor is not None and (
      cursor.stream_id != self._stream_id
      or cursor.continuity_generation != self._continuity_generation
    ):
      return self._slice(
        cursor,
        self._rewarm_cursor(eligible_ticks, target_sequence),
        SymbolDeltaCoverage.GENERATION_CHANGED,
        (),
      )
    if cursor is not None and cursor.ring_generation != self._ring_generation:
      coverage = (
        SymbolDeltaCoverage.SEQUENCE_GAP
        if self._discontinuity_reason == T_MARKET_SEQUENCE_GAP
        else SymbolDeltaCoverage.GENERATION_CHANGED
      )
      return self._slice(
        cursor,
        self._rewarm_cursor(eligible_ticks, target_sequence),
        coverage,
        (),
      )

    cursor_sequence = cursor.accepted_sequence if cursor is not None else 0
    if eligible_ticks and cursor is not None:
      oldest_sequence = eligible_ticks[0].accepted_sequence
      if cursor_sequence < oldest_sequence - 1:
        return self._slice(
          cursor,
          self._rewarm_cursor(eligible_ticks, target_sequence),
          SymbolDeltaCoverage.RING_OVERFLOW,
          (),
        )

    pending = tuple(
      tick for tick in eligible_ticks if tick.accepted_sequence > cursor_sequence
    )
    if pending and not rewarm_replay:
      lag_ticks = len(pending)
      lag_ms = max(0, decision_time_ms - pending[0].received_at_ms)
      if (
        lag_ticks > T_ASSISTANT_REDUCER_MAX_LAG_TICKS
        or lag_ms > T_ASSISTANT_REDUCER_MAX_LAG_MS
      ):
        return self._slice(
          cursor,
          self._rewarm_cursor(eligible_ticks, target_sequence),
          SymbolDeltaCoverage.REDUCER_LAG_EXCEEDED,
          (),
        )
    return self._slice(
      cursor,
      next_cursor,
      SymbolDeltaCoverage.READY,
      pending,
    )

  def _rewarm_cursor(
    self,
    eligible_ticks: Sequence[AcceptedTMarketTick],
    target_sequence: int,
  ) -> SymbolMarketCursor:
    """Return a replay base without acknowledging any accepted Tick.

    A discontinuity cycle persists this cursor while clearing reducer state.
    The next cycle can then perform an explicit full-ring replay; advancing to
    the latest cursor here would silently discard the new generation.
    """

    return SymbolMarketCursor(
      stream_id=self._stream_id,
      continuity_generation=self._continuity_generation,
      ring_generation=self._ring_generation,
      accepted_sequence=(
        eligible_ticks[0].accepted_sequence - 1 if eligible_ticks else target_sequence
      ),
      source_identity=None,
    )

  def _reset_for_discontinuity(self, reason: str) -> None:
    self._ticks.clear()
    self._ring_generation += 1
    self._last_sequence = 0
    self._last_identity = None
    self._discontinuity_reason = reason

  def _prune(self, latest_source_time_ms: int) -> None:
    while len(self._ticks) > T_ASSISTANT_DELTA_RING_CAPACITY:
      self._ticks.popleft()
    minimum_source_time_ms = latest_source_time_ms - T_ASSISTANT_DELTA_RING_WINDOW_MS
    while self._ticks and self._ticks[0].sample.source_time_ms < minimum_source_time_ms:
      self._ticks.popleft()

  def _discontinuity_slice(
    self,
    cursor: Optional[SymbolMarketCursor],
    coverage: SymbolDeltaCoverage,
  ) -> SymbolMarketDeltaSlice:
    stream_id = self._stream_id or (cursor.stream_id if cursor else "unavailable")
    generation = self._continuity_generation or (
      cursor.continuity_generation if cursor else "unavailable"
    )
    next_cursor = SymbolMarketCursor(
      stream_id=stream_id,
      continuity_generation=generation,
      ring_generation=self._ring_generation,
      accepted_sequence=self._last_sequence,
      source_identity=self._last_identity,
    )
    return self._slice(cursor, next_cursor, coverage, ())

  def _slice(
    self,
    cursor: Optional[SymbolMarketCursor],
    next_cursor: SymbolMarketCursor,
    coverage: SymbolDeltaCoverage,
    ticks: Sequence[AcceptedTMarketTick],
  ) -> SymbolMarketDeltaSlice:
    reasons = ()
    if coverage is not SymbolDeltaCoverage.READY:
      reasons = (coverage.value, T_REWARM_REQUIRED)
    manifest_hash = _delta_slice_manifest_hash(
      instrument_code=self.instrument_code,
      coverage=coverage,
      reason_codes=reasons,
      from_cursor=cursor,
      next_cursor=next_cursor,
      ticks=ticks,
    )
    return SymbolMarketDeltaSlice(
      instrument_code=self.instrument_code,
      coverage=coverage,
      reason_codes=reasons,
      from_cursor=cursor,
      from_accepted_sequence_exclusive=cursor.accepted_sequence if cursor else 0,
      to_accepted_sequence_inclusive=next_cursor.accepted_sequence,
      ticks=tuple(ticks),
      next_cursor=next_cursor,
      manifest_hash=manifest_hash,
    )


@dataclass(frozen=True)
class TAssistantSymbolState:
  execution_id: str
  instrument_code: str
  revision: int
  lifecycle: TAssistantSymbolLifecycle
  cursor: Optional[SymbolMarketCursor]
  opportunity_state: OpportunityState
  policy_version: str
  feature_schema_version: int
  material_manifest_hash: str = ""
  rewarm_reason: Optional[str] = None
  deferred_candidate: Optional[OpportunityCandidate] = None
  deferred_candidate_fence_sequence: Optional[int] = None

  def __post_init__(self) -> None:
    code = str(self.instrument_code or "").strip().upper()
    if not self.execution_id or not code:
      raise ValueError("T-assistant symbol state requires execution and instrument")
    if self.revision < 0 or self.feature_schema_version < 1:
      raise ValueError("T-assistant symbol state revision/schema is invalid")
    if not self.policy_version:
      raise ValueError("T-assistant symbol state requires policy_version")
    object.__setattr__(self, "instrument_code", code)
    object.__setattr__(self, "lifecycle", TAssistantSymbolLifecycle(self.lifecycle))
    if (
      self.opportunity_state.instrument_code
      and self.opportunity_state.instrument_code != code
    ):
      raise ValueError("opportunity state crosses T-assistant symbol")
    if (self.deferred_candidate is None) != (
      self.deferred_candidate_fence_sequence is None
    ):
      raise ValueError("deferred candidate and fence must be paired")
    if self.deferred_candidate is not None:
      if int(self.deferred_candidate_fence_sequence or 0) < 1:
        raise ValueError("deferred candidate fence must be positive")
      if self.opportunity_state.candidate != self.deferred_candidate:
        raise ValueError("deferred candidate must match opportunity state")

  @classmethod
  def initial(
    cls,
    *,
    execution_id: str,
    instrument_code: str,
    policy_version: str,
    feature_schema_version: int,
    trade_date: str = "",
  ) -> "TAssistantSymbolState":
    code = str(instrument_code or "").strip().upper()
    return cls(
      execution_id=execution_id,
      instrument_code=code,
      revision=0,
      lifecycle=TAssistantSymbolLifecycle.WARMING,
      cursor=None,
      opportunity_state=OpportunityState.initial(
        instrument_code=code,
        trade_date=trade_date,
      ),
      policy_version=policy_version,
      feature_schema_version=feature_schema_version,
    )

  def to_dict(self) -> dict[str, Any]:
    return {
      "execution_id": self.execution_id,
      "instrument_code": self.instrument_code,
      "revision": self.revision,
      "lifecycle": self.lifecycle.value,
      "cursor": self.cursor.to_dict() if self.cursor is not None else None,
      "opportunity_state": self.opportunity_state.to_dict(),
      "policy_version": self.policy_version,
      "feature_schema_version": self.feature_schema_version,
      "material_manifest_hash": self.material_manifest_hash,
      "rewarm_reason": self.rewarm_reason,
      "deferred_candidate": (
        self.deferred_candidate.to_dict()
        if self.deferred_candidate is not None
        else None
      ),
      "deferred_candidate_fence_sequence": self.deferred_candidate_fence_sequence,
    }

  @classmethod
  def from_dict(cls, raw: Mapping[str, Any]) -> "TAssistantSymbolState":
    cursor_raw = raw.get("cursor")
    state_raw = raw.get("opportunity_state")
    if not isinstance(state_raw, Mapping):
      raise ValueError("T-assistant symbol opportunity_state is required")
    return cls(
      execution_id=str(raw.get("execution_id") or ""),
      instrument_code=str(raw.get("instrument_code") or ""),
      revision=int(raw.get("revision") or 0),
      lifecycle=TAssistantSymbolLifecycle(
        str(raw.get("lifecycle") or TAssistantSymbolLifecycle.WARMING.value)
      ),
      cursor=(
        SymbolMarketCursor.from_dict(cursor_raw)
        if isinstance(cursor_raw, Mapping)
        else None
      ),
      opportunity_state=OpportunityState.from_dict(state_raw),
      policy_version=str(raw.get("policy_version") or ""),
      feature_schema_version=int(raw.get("feature_schema_version") or 0),
      material_manifest_hash=str(raw.get("material_manifest_hash") or ""),
      rewarm_reason=(
        str(raw.get("rewarm_reason")) if raw.get("rewarm_reason") else None
      ),
      deferred_candidate=(
        OpportunityCandidate.from_dict(raw["deferred_candidate"])
        if isinstance(raw.get("deferred_candidate"), Mapping)
        else None
      ),
      deferred_candidate_fence_sequence=(
        int(raw["deferred_candidate_fence_sequence"])
        if raw.get("deferred_candidate_fence_sequence") is not None
        else None
      ),
    )


def candidate_evidence_key(execution_id: str, fingerprint: str) -> str:
  if not execution_id or not fingerprint:
    raise ValueError("T_CANDIDATE_EVIDENCE_IDENTITY_REQUIRED")
  return f"tta:candidate:{execution_id}:{fingerprint}"


def decode_candidate_evidence(raw: Mapping[str, Any]):
  """Validate the single immutable candidate-time market/evaluation witness."""

  def integers(mapping, names, minimum=0):
    for name in names:
      value = mapping[name]
      if type(value) is not int or value < minimum:
        raise ValueError("T_CANDIDATE_EVIDENCE_INTEGER_REQUIRED")

  try:
    integers(
      raw["candidate"],
      ("source_time_ms", "latched_at_ms", "expires_at_ms", "tick_ordinal"),
    )
    integers(
      raw["candidate"],
      ("feature_schema_version", "reference_profile_schema_version"),
      1,
    )
    integers(raw["tick"], ("accepted_sequence", "market_fence_sequence"), 1)
    integers(raw["tick"], ("received_at_ms",))
    integers(raw["tick"]["sample"], ("source_time_ms", "tick_ordinal"))
    if raw["tick"]["sample"].get("received_at_ms") is not None:
      integers(raw["tick"]["sample"], ("received_at_ms",))
    integers(raw["cursor"], ("ring_generation", "accepted_sequence"), 1)
    integers(raw["cursor"]["source_identity"], ("source_time_ms", "tick_ordinal"))
    integers(
      raw["evaluation"],
      ("source_time_ms", "tick_ordinal", "evaluated_at_ms", "candidate_expires_at_ms"),
    )
    integers(raw["evaluation"], ("feature_schema_version",), 1)
    candidate = OpportunityCandidate.from_dict(raw["candidate"])
    tick_values = dict(raw["tick"])
    tick_values["sample"] = OpportunitySample.from_dict(tick_values["sample"])
    tick = AcceptedTMarketTick(**tick_values)
    cursor = SymbolMarketCursor.from_dict(raw["cursor"])
    evaluation = raw["evaluation"]
    if (
      cursor.stream_id != tick.stream_id
      or cursor.accepted_sequence != tick.accepted_sequence
      or cursor.source_identity != tick.source_identity
      or cursor.continuity_generation != tick.sample.continuity_generation
      or candidate.source_time_ms != tick.sample.source_time_ms
      or candidate.tick_ordinal != tick.sample.tick_ordinal
      or candidate.price != tick.sample.price
      or evaluation["instrument_code"] != tick.instrument_code
      or evaluation["candidate_id"] != candidate.candidate_id
      or evaluation["candidate_fingerprint"] != candidate.fingerprint
      or evaluation["candidate_expires_at_ms"] != candidate.expires_at_ms
      or evaluation["source_time_ms"] != candidate.source_time_ms
      or evaluation["tick_ordinal"] != candidate.tick_ordinal
      or evaluation["opportunity_score"] != candidate.score
      or evaluation["policy_version"] != candidate.policy_version
      or evaluation["feature_schema_version"] != candidate.feature_schema_version
      or evaluation["selected_path"] != candidate.path.value
    ):
      raise ValueError("T_CANDIDATE_EVIDENCE_BINDING_CONFLICT")
  except (KeyError, TypeError, ValueError) as exc:
    raise ValueError("T_CANDIDATE_EVIDENCE_INVALID") from exc
  return candidate, tick, cursor


@dataclass(frozen=True)
class SymbolDecisionSnapshot:
  instrument_code: str
  state: TAssistantSymbolState
  delta_slice: SymbolMarketDeltaSlice
  gate_context: OpportunityGateContext
  reference_profile: Optional[OpportunityReferenceProfile] = None
  eligible: bool = True
  draining: bool = False
  ignored: bool = False
  blockers: tuple[str, ...] = ()
  candidate_control: CandidateControl = field(default_factory=CandidateControl)

  def __post_init__(self) -> None:
    code = str(self.instrument_code or "").strip().upper()
    if not code:
      raise ValueError("symbol decision snapshot requires instrument_code")
    if self.state.instrument_code != code or self.delta_slice.instrument_code != code:
      raise ValueError("symbol decision snapshot identity mismatch")
    if not isinstance(self.candidate_control, CandidateControl):
      raise TypeError("symbol decision snapshot requires typed candidate control")
    object.__setattr__(self, "instrument_code", code)
    object.__setattr__(
      self,
      "blockers",
      tuple(dict.fromkeys(str(item) for item in self.blockers if str(item))),
    )


@dataclass(frozen=True)
class TDecisionSnapshot:
  execution_ref: ExecutionOwnerRef
  decision_time: datetime
  trade_date: str
  stream_id: str
  continuity_generation: str
  fence_sequence: int
  capture_as_of: datetime
  universe_revision: int
  config_version: int
  config_snapshot_hash: str
  policy_version: str
  feature_schema_version: int
  execution_status: TAssistantExecutionStatus
  entry_readiness: TAssistantEntryReadiness
  entry_readiness_as_of: datetime
  symbols: tuple[SymbolDecisionSnapshot, ...]
  market_context: Mapping[str, Any] = field(default_factory=dict)
  scorer_mode: str = "RULE_ONLY"
  model_runtime_binding_hash: Optional[str] = None

  def __post_init__(self) -> None:
    if self.execution_ref.owner_type is not ExecutionOwnerType.T_ASSISTANT_EXECUTION:
      raise ValueError("T decision snapshot requires a T-assistant execution owner")
    if (
      self.decision_time.tzinfo is None
      or self.capture_as_of.tzinfo is None
      or self.entry_readiness_as_of.tzinfo is None
    ):
      raise ValueError("T decision snapshot times must be timezone-aware")
    object.__setattr__(
      self,
      "execution_status",
      TAssistantExecutionStatus(self.execution_status),
    )
    object.__setattr__(
      self,
      "entry_readiness",
      TAssistantEntryReadiness(self.entry_readiness),
    )
    if not self.trade_date or not self.stream_id or not self.continuity_generation:
      raise ValueError("T decision snapshot requires market identity")
    if self.fence_sequence < 0 or self.universe_revision < 0:
      raise ValueError("T decision snapshot revisions must be non-negative")
    if self.config_version < 1 or self.feature_schema_version < 1:
      raise ValueError("T decision snapshot versions must be positive")
    if not self.config_snapshot_hash or not self.policy_version:
      raise ValueError("T decision snapshot requires frozen config/policy")
    normalized = tuple(sorted(self.symbols, key=lambda item: item.instrument_code))
    if len({item.instrument_code for item in normalized}) != len(normalized):
      raise ValueError("T decision snapshot contains duplicate symbols")
    if any(
      item.state.execution_id != self.execution_ref.owner_id for item in normalized
    ):
      raise ValueError("T decision snapshot symbol state crosses execution")
    for item in normalized:
      delta = item.delta_slice
      if item.state.cursor != delta.from_cursor:
        raise ValueError("T decision snapshot state/delta cursor mismatch")
      if (
        delta.next_cursor.stream_id != self.stream_id
        or delta.next_cursor.continuity_generation != self.continuity_generation
      ):
        raise ValueError("T decision snapshot delta/header identity mismatch")
      if delta.to_accepted_sequence_inclusive > self.fence_sequence:
        raise ValueError("T decision snapshot delta crosses header fence")
      expected_manifest = delta.computed_manifest_hash
      if delta.manifest_hash != expected_manifest:
        raise ValueError("T decision snapshot delta manifest mismatch")
      sequences = [tick.accepted_sequence for tick in delta.ticks]
      if sequences:
        if sequences != list(
          range(
            delta.from_accepted_sequence_exclusive + 1,
            delta.to_accepted_sequence_inclusive + 1,
          )
        ):
          raise ValueError("T decision snapshot Tick sequence is not contiguous")
        if delta.next_cursor.source_identity != delta.ticks[-1].source_identity:
          raise ValueError("T decision snapshot Tick/cursor source identity mismatch")
        identities = [tick.source_identity for tick in delta.ticks]
        if any(
          current <= previous for previous, current in zip(identities, identities[1:])
        ):
          raise ValueError("T decision snapshot Tick source identity is not ordered")
        if (
          delta.from_cursor is not None
          and delta.from_cursor.source_identity is not None
          and identities[0] <= delta.from_cursor.source_identity
        ):
          raise ValueError("T decision snapshot Tick does not advance source identity")
      elif (
        delta.coverage is SymbolDeltaCoverage.READY
        and delta.from_accepted_sequence_exclusive
        != delta.to_accepted_sequence_inclusive
      ):
        raise ValueError("T decision snapshot empty READY delta advances cursor")
      if any(
        tick.stream_id != self.stream_id
        or tick.sample.continuity_generation != self.continuity_generation
        or int(tick.market_fence_sequence or 0) > self.fence_sequence
        or tick.accepted_sequence > delta.to_accepted_sequence_inclusive
        for tick in delta.ticks
      ):
        raise ValueError("T decision snapshot Tick crosses header fence")
    object.__setattr__(self, "symbols", normalized)
    canonical = canonical_json_payload(self.market_context)
    object.__setattr__(self, "market_context", json.loads(canonical))

  @property
  def market_delta_manifest_hash(self) -> str:
    return stable_manifest_hash(
      {
        "stream_id": self.stream_id,
        "continuity_generation": self.continuity_generation,
        "fence_sequence": self.fence_sequence,
        "symbols": {
          item.instrument_code: item.delta_slice.manifest_hash for item in self.symbols
        },
      }
    )

  @property
  def reducer_cursor_manifest_hash(self) -> str:
    return stable_manifest_hash(
      {
        item.instrument_code: (
          item.state.cursor.to_dict() if item.state.cursor is not None else None
        )
        for item in self.symbols
      }
    )

  @property
  def snapshot_hash(self) -> str:
    return stable_manifest_hash(self.decision_payload())

  def decision_payload(self) -> dict[str, Any]:
    return {
      "execution_ref": self.execution_ref.to_dict(),
      "decision_time": self.decision_time.isoformat(),
      "trade_date": self.trade_date,
      "stream_id": self.stream_id,
      "continuity_generation": self.continuity_generation,
      "fence_sequence": self.fence_sequence,
      "capture_as_of": self.capture_as_of.isoformat(),
      "universe_revision": self.universe_revision,
      "config_version": self.config_version,
      "config_snapshot_hash": self.config_snapshot_hash,
      "policy_version": self.policy_version,
      "feature_schema_version": self.feature_schema_version,
      "execution_status": self.execution_status.value,
      "entry_readiness": self.entry_readiness.value,
      "entry_readiness_as_of": self.entry_readiness_as_of.isoformat(),
      "scorer_mode": self.scorer_mode,
      "model_runtime_binding_hash": self.model_runtime_binding_hash,
      "market_delta_manifest_hash": self.market_delta_manifest_hash,
      "reducer_cursor_manifest_hash": self.reducer_cursor_manifest_hash,
      "market_context": dict(self.market_context),
      "symbols": {
        item.instrument_code: {
          "state_revision": item.state.revision,
          "state_material_manifest_hash": item.state.material_manifest_hash,
          "delta_manifest_hash": item.delta_slice.manifest_hash,
          "eligible": item.eligible,
          "draining": item.draining,
          "ignored": item.ignored,
          "blockers": list(item.blockers),
          "candidate_control": item.candidate_control.to_dict(),
        }
        for item in self.symbols
      },
    }


@dataclass(frozen=True)
class SymbolTReduction:
  instrument_code: str
  previous_revision: int
  next_state: TAssistantSymbolState
  evaluations: tuple[OpportunityEvaluation, ...]
  opportunities: tuple[OpportunityCandidate, ...]
  material_events: tuple[Mapping[str, Any], ...]
  material: bool


class SymbolMarketStateReducer:
  """Apply every accepted delta exactly once through the existing V3 reducer."""

  def reduce(
    self,
    snapshot: SymbolDecisionSnapshot,
    *,
    policy: OpportunityPolicy,
    allow_candidate_creation: bool = True,
    decision_time_ms: Optional[int] = None,
  ) -> SymbolTReduction:
    previous = snapshot.state
    delta_slice = snapshot.delta_slice
    if previous.policy_version != policy.policy_version:
      return self._rewarm(snapshot, "T_POLICY_VERSION_CHANGED")
    if previous.feature_schema_version != policy.feature_schema_version:
      return self._rewarm(snapshot, "T_FEATURE_SCHEMA_VERSION_CHANGED")
    if delta_slice.coverage.requires_rewarm:
      return self._rewarm(snapshot, delta_slice.coverage.value)

    state = previous.opportunity_state
    evaluations: list[OpportunityEvaluation] = []
    opportunities: list[OpportunityCandidate] = []
    material_events: list[Mapping[str, Any]] = []
    deferred_candidate = previous.deferred_candidate
    deferred_fence = previous.deferred_candidate_fence_sequence
    control = snapshot.candidate_control
    if control.awaiting_approval_candidate_id or control.suppress_candidate_id:
      if type(decision_time_ms) is not int or decision_time_ms < 0:
        raise ValueError("T_CANDIDATE_CONTROL_TIME_REQUIRED")
      controlled = transition_candidate(state, control, source_time_ms=decision_time_ms)
      if controlled != state:
        material_events.append({
          "event_type": "T_OPPORTUNITY_CANDIDATE_CONTROL_APPLIED",
          "instrument_code": snapshot.instrument_code,
          "candidate_id": state.candidate.candidate_id,
          "control": control.to_dict(),
          "source_time_ms": decision_time_ms,
        })
        state = controlled
      if (deferred_candidate is not None
          and control.suppress_candidate_id == deferred_candidate.candidate_id):
        deferred_candidate = None
        deferred_fence = None
    if allow_candidate_creation and deferred_candidate is not None:
      if (
        decision_time_ms is not None
        and decision_time_ms >= deferred_candidate.expires_at_ms
      ):
        state = transition_candidate(
          state,
          CandidateControl(),
          source_time_ms=decision_time_ms,
        )
        material_events.append(
          {
            "event_type": "T_OPPORTUNITY_DEFERRED_CANDIDATE_EXPIRED",
            "instrument_code": snapshot.instrument_code,
            "candidate_id": deferred_candidate.candidate_id,
            "market_fence_sequence": deferred_fence,
          }
        )
      else:
        opportunities.append(deferred_candidate)
        material_events.append(
          {
            "event_type": "T_OPPORTUNITY_DEFERRED_CANDIDATE_RELEASED",
            "instrument_code": snapshot.instrument_code,
            "candidate_id": deferred_candidate.candidate_id,
            "candidate_fingerprint": deferred_candidate.fingerprint,
            "market_fence_sequence": deferred_fence,
          }
        )
      deferred_candidate = None
      deferred_fence = None
    previous_signature = _material_signature(state)
    last_identity = previous.cursor.source_identity if previous.cursor else None
    for tick in delta_slice.ticks:
      identity = tick.source_identity
      if last_identity is not None and identity <= last_identity:
        # The ring normally filters this.  Retain a second, pure-domain guard
        # so restored cursors cannot cause a repeated FSM transition.
        continue
      reduction = reduce_opportunity(
        state,
        tick.sample,
        gate_context=snapshot.gate_context,
        policy=policy,
        reference_profile=snapshot.reference_profile,
        candidate_control=control,
        # Candidate detection is causal market-state work.  Entry readiness
        # controls release, not whether a transient qualifying Tick is kept.
        allow_candidate_creation=True,
      )
      if reduction.ignored:
        continue
      state = reduction.state
      evaluations.append(reduction.evaluation)
      if reduction.candidate_created is not None:
        if allow_candidate_creation:
          opportunities.append(reduction.candidate_created)
        else:
          deferred_candidate = reduction.candidate_created
          deferred_fence = int(tick.market_fence_sequence or 0)
          material_events.append(
            {
              "event_type": "T_OPPORTUNITY_CANDIDATE_DEFERRED",
              "instrument_code": snapshot.instrument_code,
              "candidate_id": reduction.candidate_created.candidate_id,
              "candidate_fingerprint": reduction.candidate_created.fingerprint,
              "market_fence_sequence": deferred_fence,
            }
          )
      next_signature = _material_signature(state)
      if (
        next_signature != previous_signature or reduction.candidate_created is not None
      ):
        event = {
          "event_type": "SYMBOL_MARKET_STATE_MATERIAL",
          "instrument_code": snapshot.instrument_code,
          "source_identity": {
            "continuity_generation": identity.continuity_generation,
            "source_time_ms": identity.source_time_ms,
            "tick_ordinal": identity.tick_ordinal,
          },
          "evaluation": reduction.evaluation.to_dict(),
        }
        if reduction.candidate_created is not None:
          event["candidate_evidence"] = {
            "candidate": reduction.candidate_created.to_dict(),
            "evaluation": reduction.evaluation.to_dict(),
            "tick": tick.manifest_item(),
            "cursor": SymbolMarketCursor(
              tick.stream_id,
              tick.sample.continuity_generation,
              delta_slice.next_cursor.ring_generation,
              tick.accepted_sequence,
              tick.source_identity,
            ).to_dict(),
          }
          decode_candidate_evidence(event["candidate_evidence"])
        material_events.append(event)
        previous_signature = next_signature
      last_identity = identity

    material = bool(material_events or opportunities)
    lifecycle = previous.lifecycle
    if snapshot.draining:
      lifecycle = TAssistantSymbolLifecycle.DRAINING
    elif snapshot.ignored:
      lifecycle = TAssistantSymbolLifecycle.RETIRED
    elif state.data_health.value == "READY":
      lifecycle = TAssistantSymbolLifecycle.ACTIVE
    else:
      lifecycle = TAssistantSymbolLifecycle.WARMING
    if lifecycle != previous.lifecycle:
      material = True
      material_events.append(
        {
          "event_type": "SYMBOL_LIFECYCLE_CHANGED",
          "instrument_code": snapshot.instrument_code,
          "from": previous.lifecycle.value,
          "to": lifecycle.value,
        }
      )

    # Persist and retain the same normalized DTO in memory: optional sample
    # numbers must not change their JSON representation after a DB round trip.
    state = OpportunityState.from_dict(state.to_dict())
    manifest = stable_manifest_hash(
      {
        "opportunity_state": state.to_dict(),
        "cursor": delta_slice.next_cursor.to_dict(),
        "lifecycle": lifecycle.value,
        "deferred_candidate": (
          deferred_candidate.to_dict() if deferred_candidate is not None else None
        ),
        "deferred_candidate_fence_sequence": deferred_fence,
      }
    )
    next_state = replace(
      previous,
      revision=previous.revision + (1 if material else 0),
      lifecycle=lifecycle,
      cursor=delta_slice.next_cursor,
      opportunity_state=state,
      material_manifest_hash=(
        manifest if material else previous.material_manifest_hash
      ),
      rewarm_reason=None,
      deferred_candidate=deferred_candidate,
      deferred_candidate_fence_sequence=deferred_fence,
    )
    return SymbolTReduction(
      instrument_code=snapshot.instrument_code,
      previous_revision=previous.revision,
      next_state=next_state,
      evaluations=tuple(evaluations),
      opportunities=tuple(opportunities),
      material_events=tuple(material_events),
      material=material,
    )

  def _rewarm(
    self,
    snapshot: SymbolDecisionSnapshot,
    reason: str,
  ) -> SymbolTReduction:
    previous = snapshot.state
    trade_date = previous.opportunity_state.trade_date
    next_state = replace(
      previous,
      revision=previous.revision + 1,
      lifecycle=TAssistantSymbolLifecycle.WARMING,
      cursor=snapshot.delta_slice.next_cursor,
      opportunity_state=OpportunityState.initial(
        instrument_code=previous.instrument_code,
        trade_date=trade_date,
      ),
      rewarm_reason=reason,
      deferred_candidate=None,
      deferred_candidate_fence_sequence=None,
      material_manifest_hash=stable_manifest_hash(
        {
          "instrument_code": previous.instrument_code,
          "cursor": snapshot.delta_slice.next_cursor.to_dict(),
          "reason": reason,
        }
      ),
    )
    event = {
      "event_type": "SYMBOL_REWARM_REQUIRED",
      "instrument_code": previous.instrument_code,
      "reason_codes": [reason, T_REWARM_REQUIRED],
    }
    return SymbolTReduction(
      instrument_code=previous.instrument_code,
      previous_revision=previous.revision,
      next_state=next_state,
      evaluations=(),
      opportunities=(),
      material_events=(event,),
      material=True,
    )


def _material_signature(state: OpportunityState) -> str:
  candidate = state.candidate
  return hashlib.sha256(
    canonical_json_payload(
      {
        "data_health": state.data_health.value,
        "health_reasons": list(state.health_reasons),
        "pullback_phase": state.pullback.phase.value,
        "momentum_phase": state.momentum.phase.value,
        "candidate_status": state.candidate_status.value,
        "candidate_id": candidate.candidate_id if candidate else None,
        "candidate_fingerprint": candidate.fingerprint if candidate else None,
      }
    ).encode("utf-8")
  ).hexdigest()


__all__ = [
  "candidate_evidence_key",
  "decode_candidate_evidence",
  "AcceptedTMarketTick",
  "SymbolDecisionSnapshot",
  "SymbolDeltaCoverage",
  "SymbolMarketCursor",
  "SymbolMarketDeltaRing",
  "SymbolMarketDeltaSlice",
  "SymbolMarketStateReducer",
  "SymbolTReduction",
  "TAssistantSymbolState",
  "TDecisionSnapshot",
  "TMarketSourceIdentity",
  "TickAcceptance",
  "TickAcceptanceResult",
  "T_ASSISTANT_DELTA_RING_CAPACITY",
  "T_ASSISTANT_DELTA_RING_WINDOW_MS",
  "T_ASSISTANT_FUTURE_TIMESTAMP_SKEW_MS",
  "T_ASSISTANT_MARKET_CAPTURE_MAX_AGE_MS",
  "T_ASSISTANT_MARKET_POLICY_VERSION",
  "T_ASSISTANT_REDUCER_MAX_LAG_MS",
  "T_ASSISTANT_REDUCER_MAX_LAG_TICKS",
  "T_ASSISTANT_SYMBOL_QUOTE_MAX_AGE_MS",
  "T_MARKET_GENERATION_CHANGED",
  "T_MARKET_RING_OVERFLOW",
  "T_MARKET_SEQUENCE_GAP",
  "T_MARKET_STREAM_NOT_READY",
  "T_QUOTE_STALE",
  "T_REDUCER_LAG_EXCEEDED",
  "T_REWARM_REQUIRED",
  "T_SNAPSHOT_STALE",
]
