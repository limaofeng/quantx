"""Independent PAPER-only T-assistant decision runtime for P3 shadowing."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Callable, Mapping, Optional

from quantx_contracts import ExecutionEnvironment
from quantx_domain.enums import StrategyRunMode
from quantx_domain.strategies import AshareIntradayTAssistantStrategy
from quantx_domain.strategies.base import (
  MarketDataContext,
  MarketDataSession,
  StrategyCadence,
  StrategyContext,
  StrategyInput,
  StrategyOutput,
)
from quantx_domain.trading.t_assistant_execution import (
  TAssistantExecution,
  TAssistantExecutionEvent,
  stable_manifest_hash,
)
from quantx_domain.trading.t_assistant_market_state import (
  TAssistantSymbolState,
  TDecisionSnapshot,
  candidate_evidence_key,
  decode_candidate_evidence,
)
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.connection import AsyncSessionLocal
from quantx_infrastructure.repositories.t_assistant_decision_cycle_repository import (
  TAssistantCycleConflict,
  TAssistantDecisionCycleRepository,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class ShadowComparisonStatus(str, Enum):
  MATCH = "MATCH"
  DIFFERENT = "DIFFERENT"
  UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class TAssistantShadowComparison:
  instrument_code: str
  status: ShadowComparisonStatus
  difference_codes: tuple[str, ...]
  new_signature: Mapping[str, Any]
  legacy_signature: Optional[Mapping[str, Any]]

  def to_dict(self) -> dict[str, Any]:
    return {
      "instrument_code": self.instrument_code,
      "status": self.status.value,
      "difference_codes": list(self.difference_codes),
      "new_signature": dict(self.new_signature),
      "legacy_signature": (
        dict(self.legacy_signature) if self.legacy_signature is not None else None
      ),
    }


@dataclass(frozen=True)
class TAssistantShadowCycleResult:
  cycle_id: Optional[str]
  committed: bool
  output: StrategyOutput
  comparisons: tuple[TAssistantShadowComparison, ...]


class TAssistantPaperShadowRuntime:
  """Run StrategyBase.step(SNAPSHOT) without any execution-side write path.

  The class intentionally depends only on the P3 cycle repository.  It cannot
  create approvals, PendingTradeOrders, correlations, or TradeCommandOutbox
  rows.  Standard TradeIntent values are retained as isolated proposal JSON in
  the cycle output manifest for deterministic comparison and later P4 intake.
  """

  def __init__(
    self,
    *,
    session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal,
    processing_owner: str = "t-assistant-paper-shadow",
    clock: Callable[[], datetime] = time_utils.now_aware,
  ) -> None:
    self._session_factory = session_factory
    self._processing_owner = processing_owner
    self._clock = clock
    self._strategies: dict[str, AshareIntradayTAssistantStrategy] = {}
    self._parameters: dict[str, dict[str, Any]] = {}
    self._symbol_states: dict[str, dict[str, TAssistantSymbolState]] = {}

  def bind_execution(
    self,
    execution: TAssistantExecution,
    *,
    parameters: Mapping[str, Any],
    symbol_states: Optional[Mapping[str, TAssistantSymbolState]] = None,
  ) -> None:
    if execution.environment is not ExecutionEnvironment.PAPER:
      raise ValueError("P3 T-assistant shadow runtime accepts PAPER only")
    context = StrategyContext(
      mode=StrategyRunMode.PAPER,
      instruments=sorted((symbol_states or {}).keys()),
      parameters=dict(parameters),
      execution_ref=execution.execution_ref,
      environment=ExecutionEnvironment.PAPER,
    )
    strategy = AshareIntradayTAssistantStrategy(context)
    self._strategies[execution.execution_id] = strategy
    self._parameters[execution.execution_id] = dict(parameters)
    self._symbol_states[execution.execution_id] = dict(symbol_states or {})

  def symbol_states(self, execution_id: str) -> dict[str, TAssistantSymbolState]:
    return dict(self._symbol_states.get(execution_id, {}))

  async def run_cycle(
    self,
    *,
    execution: TAssistantExecution,
    snapshot: TDecisionSnapshot,
    legacy_results: Optional[Mapping[str, Mapping[str, Any]]] = None,
    _cycle_id: Optional[str] = None,
  ) -> TAssistantShadowCycleResult:
    if execution.environment is not ExecutionEnvironment.PAPER:
      raise ValueError("P3 T-assistant shadow cycle must stay in PAPER")
    if snapshot.execution_ref != execution.execution_ref:
      raise ValueError("T-assistant shadow snapshot owner mismatch")
    strategy = self._strategies.get(execution.execution_id)
    if strategy is None:
      self.bind_execution(execution, parameters={})
      strategy = self._strategies[execution.execution_id]

    cycle_id = _cycle_id or str(uuid.uuid4())
    market_data_context = MarketDataContext(
      source="WHOLE_QUOTE_SNAPSHOT",
      stream_id=snapshot.stream_id,
      continuity_generation=0,
      source_sequence=snapshot.fence_sequence,
      source_time_ms=int(snapshot.decision_time.timestamp() * 1000),
      tick_ordinal=snapshot.fence_sequence,
      received_at_ms=int(snapshot.capture_as_of.timestamp() * 1000),
      quote_stale=False,
      session=MarketDataSession.UNKNOWN,
      trade_date=snapshot.decision_time.date(),
    )
    output = await strategy.step(
      StrategyInput(
        strategy_id="ashare-intraday-t-assistant",
        timestamp=snapshot.decision_time,
        cadence=StrategyCadence.SNAPSHOT,
        instrument_code=None,
        input_id=cycle_id,
        market_data=snapshot,
        market_data_context=market_data_context,
        parameters=dict(self._parameters.get(execution.execution_id, {})),
        execution_ref=execution.execution_ref,
      )
    )
    comparisons = self._compare(
      output=output,
      legacy_results=legacy_results or {},
    )
    async with self._session_factory() as db:
      async with db.begin():
        repository = TAssistantDecisionCycleRepository(db)
        cycle = await repository.prepare_material_cycle(
          snapshot=snapshot,
          cycle_id=cycle_id,
          now=self._now(),
        )
      if cycle.status == "PROPOSALS_COMMITTED":
        self._apply_memory_state(execution.execution_id, output)
        return TAssistantShadowCycleResult(
          cycle_id=cycle.cycle_id,
          committed=True,
          output=output,
          comparisons=comparisons,
        )

      claim_conflict: Optional[TAssistantCycleConflict] = None
      committed_cycle = None
      async with db.begin():
        repository = TAssistantDecisionCycleRepository(db)
        try:
          claim = await repository.claim(
            cycle_id=cycle.cycle_id,
            processing_owner=self._processing_owner,
            expected_input_manifest_hash=cycle.input_manifest_hash,
            now=self._now(),
          )
          evidence = self._opportunity_evidence(
            execution=execution,
            cycle_id=cycle.cycle_id,
            output=output,
            comparisons=comparisons,
            evaluated_at=snapshot.decision_time,
          )
          events = self._comparison_events(
            execution=execution,
            cycle_id=cycle.cycle_id,
            comparisons=comparisons,
            occurred_at=snapshot.decision_time,
          )
          committed_cycle = await repository.commit_material_cycle(
            claim=claim,
            expected_input_manifest_hash=cycle.input_manifest_hash,
            symbol_patches=output.symbol_state_patches,
            opportunity_evidence=evidence,
            execution_events=events,
            trade_intents=output.trade_intents,
            now=self._now(),
          )
        except TAssistantCycleConflict as exc:
          # Catch inside the transaction so a fence-safe ABORTED_STALE fact is
          # committed instead of being rolled back with the raised conflict.
          claim_conflict = exc

      if claim_conflict is not None:
        raise claim_conflict
      if committed_cycle is None or committed_cycle.status != "PROPOSALS_COMMITTED":
        return TAssistantShadowCycleResult(
          cycle_id=cycle.cycle_id,
          committed=False,
          output=output,
          comparisons=comparisons,
        )

    self._apply_memory_state(execution.execution_id, output)
    return TAssistantShadowCycleResult(
      cycle_id=cycle.cycle_id,
      committed=True,
      output=output,
      comparisons=comparisons,
    )

  async def recover_cycle(
    self,
    *,
    execution: TAssistantExecution,
    snapshot: TDecisionSnapshot,
    cycle_id: str,
    legacy_results: Optional[Mapping[str, Mapping[str, Any]]] = None,
  ) -> TAssistantShadowCycleResult:
    """Resume only when the caller supplies the exact original snapshot."""

    async with self._session_factory() as db:
      async with db.begin():
        repository = TAssistantDecisionCycleRepository(db)
        cycle = await repository.get(cycle_id)
      if cycle is None:
        raise TAssistantCycleConflict("T_CYCLE_NOT_FOUND")
      if cycle.snapshot_hash != snapshot.snapshot_hash:
        conflict: Optional[TAssistantCycleConflict] = None
        async with db.begin():
          try:
            await repository.claim(
              cycle_id=cycle_id,
              processing_owner=self._processing_owner,
              expected_input_manifest_hash=stable_manifest_hash(
                snapshot.decision_payload()
              ),
              now=self._now(),
            )
          except TAssistantCycleConflict as exc:
            conflict = exc
        if conflict is not None:
          raise conflict
        raise TAssistantCycleConflict("T_CYCLE_INPUT_STALE")
    return await self.run_cycle(
      execution=execution,
      snapshot=snapshot,
      legacy_results=legacy_results,
      _cycle_id=cycle_id,
    )

  def _now(self) -> datetime:
    current = self._clock()
    if current.tzinfo is None:
      raise ValueError("T-assistant runtime clock must be timezone-aware")
    return current

  @staticmethod
  def _compare(
    *,
    output: StrategyOutput,
    legacy_results: Mapping[str, Mapping[str, Any]],
  ) -> tuple[TAssistantShadowComparison, ...]:
    new_results = dict(output.trace_payload.get("symbol_results") or {})
    comparisons: list[TAssistantShadowComparison] = []
    for instrument_code in sorted(new_results):
      new_signature = _comparison_signature(new_results[instrument_code])
      legacy = legacy_results.get(instrument_code)
      if legacy is None:
        comparisons.append(
          TAssistantShadowComparison(
            instrument_code=instrument_code,
            status=ShadowComparisonStatus.UNAVAILABLE,
            difference_codes=("LEGACY_CYCLE_EVIDENCE_UNAVAILABLE",),
            new_signature=new_signature,
            legacy_signature=None,
          )
        )
        continue
      unavailable_reason = str(legacy.get("_unavailable_reason") or "").strip()
      if unavailable_reason:
        comparisons.append(
          TAssistantShadowComparison(
            instrument_code=instrument_code,
            status=ShadowComparisonStatus.UNAVAILABLE,
            difference_codes=(unavailable_reason,),
            new_signature=new_signature,
            legacy_signature=None,
          )
        )
        continue
      new_identity = _source_fence_identity(new_results[instrument_code])
      legacy_identity = _source_fence_identity(legacy)
      if new_identity is None or legacy_identity is None:
        comparisons.append(
          TAssistantShadowComparison(
            instrument_code=instrument_code,
            status=ShadowComparisonStatus.UNAVAILABLE,
            difference_codes=("LEGACY_SOURCE_FENCE_UNAVAILABLE",),
            new_signature=new_signature,
            legacy_signature=_comparison_signature(legacy),
          )
        )
        continue
      if new_identity != legacy_identity:
        comparisons.append(
          TAssistantShadowComparison(
            instrument_code=instrument_code,
            status=ShadowComparisonStatus.UNAVAILABLE,
            difference_codes=("LEGACY_SOURCE_FENCE_MISMATCH",),
            new_signature=new_signature,
            legacy_signature=_comparison_signature(legacy),
          )
        )
        continue
      legacy_signature = _comparison_signature(legacy)
      differences = tuple(
        f"RULE_DIFFERENCE_{field.upper()}"
        for field in sorted(set(new_signature) | set(legacy_signature))
        if new_signature.get(field) != legacy_signature.get(field)
      )
      comparisons.append(
        TAssistantShadowComparison(
          instrument_code=instrument_code,
          status=(
            ShadowComparisonStatus.DIFFERENT
            if differences
            else ShadowComparisonStatus.MATCH
          ),
          difference_codes=differences,
          new_signature=new_signature,
          legacy_signature=legacy_signature,
        )
      )
    return tuple(comparisons)

  @staticmethod
  def _opportunity_evidence(
    *,
    execution: TAssistantExecution,
    cycle_id: str,
    output: StrategyOutput,
    comparisons: tuple[TAssistantShadowComparison, ...],
    evaluated_at: datetime,
  ) -> tuple[Mapping[str, Any], ...]:
    comparison_by_symbol = {
      item.instrument_code: item.to_dict() for item in comparisons
    }
    evidence: list[Mapping[str, Any]] = []
    for patch in output.symbol_state_patches:
      if not patch.material:
        continue
      evaluation = patch.patch.set.get("latest_evaluation")
      events = list(patch.patch.append_events)
      for material in events:
        witness = material.get("candidate_evidence")
        if witness is None:
          continue
        candidate, tick, _ = decode_candidate_evidence(witness)
        if tick.instrument_code != patch.instrument_code:
          raise ValueError("T_CANDIDATE_EVIDENCE_SYMBOL_CONFLICT")
        evidence.append(
          {
            "event_key": candidate_evidence_key(
              execution.execution_id, candidate.fingerprint
            ),
            "instrument_code": patch.instrument_code,
            "candidate_id": candidate.candidate_id,
            "event_type": "T_OPPORTUNITY_CANDIDATE_FROZEN",
            "evaluated_at": datetime.fromtimestamp(
              witness["evaluation"]["evaluated_at_ms"] / 1000, tz=UTC
            ),
            "payload": {
              "execution_ref": execution.execution_ref.to_dict(),
              "environment": "PAPER",
              "cycle_id": cycle_id,
              "paper_shadow_only": True,
              "candidate_evidence": witness,
            },
            "metrics": {"opportunity_score": candidate.score},
          }
        )
      events = [
        {key: value for key, value in item.items() if key != "candidate_evidence"}
        for item in events
      ]
      payload = {
        "execution_ref": execution.execution_ref.to_dict(),
        "environment": ExecutionEnvironment.PAPER.value,
        "cycle_id": cycle_id,
        "signal_snapshot": evaluation,
        "material_events": events,
        "shadow_comparison": comparison_by_symbol.get(patch.instrument_code),
        "paper_shadow_only": True,
      }
      event_hash = stable_manifest_hash(payload)
      evidence.append(
        {
          "event_key": (
            f"tta:{execution.execution_id}:{cycle_id}:"
            f"{patch.instrument_code}:{event_hash[:16]}"
          ),
          "instrument_code": patch.instrument_code,
          "candidate_id": (
            str(dict(evaluation or {}).get("candidate_id") or "") or None
          ),
          "event_type": (
            str(events[-1].get("event_type") or "SYMBOL_MARKET_STATE_MATERIAL")
            if events
            else "SYMBOL_MARKET_STATE_MATERIAL"
          ),
          "evaluated_at": evaluated_at,
          "payload": payload,
          "metrics": {
            "opportunity_score": dict(evaluation or {}).get("opportunity_score")
          },
        }
      )
    return tuple(evidence)

  @staticmethod
  def _comparison_events(
    *,
    execution: TAssistantExecution,
    cycle_id: str,
    comparisons: tuple[TAssistantShadowComparison, ...],
    occurred_at: datetime,
  ) -> tuple[TAssistantExecutionEvent, ...]:
    return tuple(
      TAssistantExecutionEvent(
        execution_id=execution.execution_id,
        event_key=(f"shadow-comparison:{cycle_id}:{comparison.instrument_code}"),
        event_type="PAPER_SHADOW_RULE_COMPARISON",
        occurred_at=occurred_at,
        payload={
          "cycle_id": cycle_id,
          **comparison.to_dict(),
        },
      )
      for comparison in comparisons
    )

  def _apply_memory_state(
    self,
    execution_id: str,
    output: StrategyOutput,
  ) -> None:
    states = self._symbol_states.setdefault(execution_id, {})
    for patch in output.symbol_state_patches:
      raw = patch.patch.set.get("symbol_state")
      if isinstance(raw, Mapping):
        states[patch.instrument_code] = TAssistantSymbolState.from_dict(raw)


def _comparison_signature(raw: Mapping[str, Any]) -> dict[str, Any]:
  return {
    "data_health": raw.get("data_health"),
    "pullback_phase": raw.get("pullback_phase")
    or dict(raw.get("pullback") or {}).get("phase"),
    "momentum_phase": raw.get("momentum_phase")
    or dict(raw.get("momentum") or {}).get("phase"),
    "selected_path": raw.get("selected_path"),
    "opportunity_score": raw.get("opportunity_score"),
    "candidate_status": raw.get("candidate_status"),
    "candidate_id": raw.get("candidate_id"),
    "candidate_fingerprint": raw.get("candidate_fingerprint"),
  }


def _source_fence_identity(
  raw: Mapping[str, Any],
) -> Optional[tuple[str, int, int, int]]:
  nested = raw.get("source_identity")
  source = nested if isinstance(nested, Mapping) else raw
  generation = str(source.get("continuity_generation") or "").strip()
  try:
    source_time_ms = int(source.get("source_time_ms") or 0)
    tick_ordinal = int(source.get("tick_ordinal") or 0)
    fence_sequence = int(raw.get("market_fence_sequence") or 0)
  except (TypeError, ValueError, OverflowError):
    return None
  if not generation or min(source_time_ms, tick_ordinal, fence_sequence) <= 0:
    return None
  return generation, source_time_ms, tick_ordinal, fence_sequence


__all__ = [
  "ShadowComparisonStatus",
  "TAssistantPaperShadowRuntime",
  "TAssistantShadowComparison",
  "TAssistantShadowCycleResult",
]
