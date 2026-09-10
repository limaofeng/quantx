"""Build immutable TDecisionSnapshots from accepted per-symbol Tick rings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional

from quantx_domain.trading.t_assistant_execution import (
  TAssistantExecution,
  TModelRuntimeBinding,
)
from quantx_domain.trading.t_assistant_market_state import (
  T_ASSISTANT_MARKET_CAPTURE_MAX_AGE_MS,
  T_ASSISTANT_SYMBOL_QUOTE_MAX_AGE_MS,
  T_MARKET_GENERATION_CHANGED,
  T_MARKET_STREAM_NOT_READY,
  T_QUOTE_STALE,
  T_SNAPSHOT_STALE,
  AcceptedTMarketTick,
  SymbolDecisionSnapshot,
  SymbolMarketDeltaRing,
  TAssistantSymbolState,
  TDecisionSnapshot,
  TickAcceptanceResult,
)
from quantx_domain.trading.t_model_score import TModelSnapshotView
from quantx_domain.trading.t_trade_opportunity_engine import (
  CandidateControl,
  OpportunityGateContext,
  OpportunityReferenceProfile,
)


class TDecisionSnapshotBuildError(RuntimeError):
  def __init__(self, reason_code: str) -> None:
    self.reason_code = reason_code
    super().__init__(reason_code)


@dataclass(frozen=True)
class TMarketCapture:
  stream_id: str
  continuity_generation: str
  fence_sequence: int
  captured_at: datetime
  ready: bool
  reason_codes: tuple[str, ...] = ()

  def __post_init__(self) -> None:
    if not self.stream_id or not self.continuity_generation:
      raise ValueError("T market capture requires stream identity")
    if self.fence_sequence < 0:
      raise ValueError("T market capture fence must be non-negative")
    if self.captured_at.tzinfo is None:
      raise ValueError("T market capture time must be timezone-aware")
    if self.ready and self.reason_codes:
      raise ValueError("ready T market capture cannot carry blockers")


@dataclass(frozen=True)
class TSymbolUniverseEntry:
  instrument_code: str
  eligible: bool = True
  draining: bool = False
  ignored: bool = False
  blockers: tuple[str, ...] = ()
  reference_profile: Optional[OpportunityReferenceProfile] = None


class TDecisionSnapshotBuilder:
  """Own per-symbol rings; never consume a UI latest-only projection."""

  def __init__(self) -> None:
    self._rings: dict[str, SymbolMarketDeltaRing] = {}

  def seed_restored_states(
    self,
    symbol_states: Mapping[str, TAssistantSymbolState],
  ) -> None:
    """Seed only local ring epochs; restored cursors remain durable truth."""

    for code, state in symbol_states.items():
      normalized = str(code or "").strip().upper()
      if normalized in self._rings:
        continue
      generation = state.cursor.ring_generation if state.cursor is not None else 1
      self._rings[normalized] = SymbolMarketDeltaRing(
        normalized,
        initial_ring_generation=generation,
      )

  def accept_tick(
    self,
    tick: AcceptedTMarketTick,
    *,
    capture_time_ms: int,
  ) -> TickAcceptanceResult:
    code = tick.instrument_code.strip().upper()
    ring = self._rings.setdefault(code, SymbolMarketDeltaRing(code))
    return ring.accept(tick, capture_time_ms=capture_time_ms)

  def entry_market_witness(
    self,
    instrument_code: str,
  ) -> tuple[AcceptedTMarketTick, int, int] | None:
    """Capture the latest accepted Tick and its current ring epoch/sequence."""
    ring = self._rings.get(instrument_code.strip().upper())
    if ring is None or ring.latest_tick is None:
      return None
    return ring.latest_tick, ring.ring_generation, ring.last_sequence

  def invalidate_symbols(
    self,
    instrument_codes: Iterable[str],
    *,
    symbol_states: Mapping[str, TAssistantSymbolState],
  ) -> None:
    """Discard affected windows without allowing old cursors to look current."""
    for code in instrument_codes:
      state = symbol_states.get(code)
      cursor_generation = state.cursor.ring_generation if state and state.cursor else 0
      previous = self._rings.get(code)
      self._rings[code] = SymbolMarketDeltaRing(
        code,
        initial_ring_generation=max(
          cursor_generation, previous.ring_generation if previous else 0,
        ) + 1,
      )

  def build(
    self,
    *,
    execution: TAssistantExecution,
    capture: TMarketCapture,
    symbol_states: Mapping[str, TAssistantSymbolState],
    universe: Iterable[TSymbolUniverseEntry],
    decision_time: datetime,
    trade_date: str,
    market_gate_context: OpportunityGateContext,
    market_context: Optional[Mapping[str, Any]] = None,
    candidate_controls: Optional[Mapping[str, CandidateControl]] = None,
    model_view: TModelSnapshotView | None = None,
  ) -> TDecisionSnapshot:
    if decision_time.tzinfo is None:
      raise ValueError("T decision time must be timezone-aware")
    if not capture.ready:
      raise TDecisionSnapshotBuildError(
        capture.reason_codes[0]
        if capture.reason_codes
        else T_MARKET_STREAM_NOT_READY
      )
    capture_age_ms = int((decision_time - capture.captured_at).total_seconds() * 1000)
    if capture_age_ms < 0:
      raise TDecisionSnapshotBuildError(T_MARKET_GENERATION_CHANGED)
    if capture_age_ms > T_ASSISTANT_MARKET_CAPTURE_MAX_AGE_MS:
      raise TDecisionSnapshotBuildError(T_SNAPSHOT_STALE)

    items: list[SymbolDecisionSnapshot] = []
    seen: set[str] = set()
    decision_time_ms = int(decision_time.timestamp() * 1000)
    for entry in sorted(universe, key=lambda item: item.instrument_code.upper()):
      code = str(entry.instrument_code or "").strip().upper()
      if not code or code in seen:
        raise ValueError("T universe contains an invalid or duplicate symbol")
      seen.add(code)
      state = symbol_states.get(code)
      if state is None:
        state = TAssistantSymbolState.initial(
          execution_id=execution.execution_id,
          instrument_code=code,
          policy_version=execution.policy_version,
          feature_schema_version=execution.feature_schema_version,
          trade_date=trade_date,
        )
      if state.execution_id != execution.execution_id:
        raise TDecisionSnapshotBuildError("OWNER_CONFLICT")
      ring = self._rings.setdefault(code, SymbolMarketDeltaRing(code))
      ring.align_capture_identity(
        stream_id=capture.stream_id,
        continuity_generation=capture.continuity_generation,
      )
      if ring.stream_id and (
        ring.stream_id != capture.stream_id
        or ring.continuity_generation != capture.continuity_generation
      ):
        raise TDecisionSnapshotBuildError(T_MARKET_GENERATION_CHANGED)
      delta_slice = ring.slice_after(
        state.cursor,
        decision_time_ms=decision_time_ms,
        through_accepted_sequence=capture.fence_sequence,
        rewarm_replay=state.rewarm_reason is not None,
      )
      blockers = list(entry.blockers)
      latest_identity = delta_slice.next_cursor.source_identity
      latest_source_time_ms = (
        latest_identity.source_time_ms if latest_identity is not None else None
      )
      quote_stale = (
        latest_source_time_ms is None
        or decision_time_ms - latest_source_time_ms
        > T_ASSISTANT_SYMBOL_QUOTE_MAX_AGE_MS
      )
      if quote_stale:
        blockers.append(T_QUOTE_STALE)
      items.append(
        SymbolDecisionSnapshot(
          instrument_code=code,
          state=state,
          delta_slice=delta_slice,
          gate_context=OpportunityGateContext(
            continuous_session=market_gate_context.continuous_session,
            quote_stale=quote_stale,
            session_code=market_gate_context.session_code,
            local_second_of_day=market_gate_context.local_second_of_day,
          ),
          reference_profile=entry.reference_profile,
          eligible=entry.eligible,
          draining=entry.draining,
          ignored=entry.ignored,
          blockers=tuple(dict.fromkeys(blockers)),
          candidate_control=(candidate_controls or {}).get(code, CandidateControl()),
        )
      )

    if execution.model_runtime_binding is not None and model_view is None:
      model_view = TModelSnapshotView(
        TModelRuntimeBinding.from_mapping(execution.model_runtime_binding),
        0, "UNAVAILABLE", "MODEL_NOT_READY", None, "", None, (),
        tuple((item.instrument_code, "MODEL_NOT_READY") for item in items),
      )
    return TDecisionSnapshot(
      execution_ref=execution.execution_ref,
      decision_time=decision_time,
      trade_date=trade_date,
      stream_id=capture.stream_id,
      continuity_generation=capture.continuity_generation,
      fence_sequence=capture.fence_sequence,
      capture_as_of=capture.captured_at,
      universe_revision=execution.universe_revision,
      config_version=execution.frozen_config_version,
      config_snapshot_hash=execution.config_snapshot_hash,
      policy_version=execution.policy_version,
      feature_schema_version=execution.feature_schema_version,
      execution_status=execution.status,
      entry_readiness=execution.readiness.readiness,
      entry_readiness_as_of=execution.readiness.as_of,
      symbols=tuple(items),
      market_context=dict(market_context or {}),
      scorer_mode=execution.scorer_mode.value,
      model_view=model_view,
      model_runtime_binding_hash=(
        TModelRuntimeBinding.from_mapping(execution.model_runtime_binding).binding_hash
        if execution.model_runtime_binding is not None
        else None
      ),
    )


__all__ = [
  "TDecisionSnapshotBuildError",
  "TDecisionSnapshotBuilder",
  "TMarketCapture",
  "TSymbolUniverseEntry",
]
