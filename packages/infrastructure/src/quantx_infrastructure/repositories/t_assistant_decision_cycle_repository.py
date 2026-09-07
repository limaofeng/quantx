"""Durable decision-cycle fencing and atomic PAPER-shadow material commits."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Iterable, Mapping, Optional

from quantx_application.t_trade_v3.decision_cycle_use_cases import (
  T_ASSISTANT_CYCLE_LEASE_RENEW_SECONDS,
  T_ASSISTANT_CYCLE_LEASE_SECONDS,
  T_ASSISTANT_CYCLE_POLICY_VERSION,
  T_ASSISTANT_ENTRY_CYCLE_TTL_SECONDS,
  decision_key_for_snapshot,
)
from quantx_contracts import (
  ExecutionEnvironment,
  ExecutionOwnerRef,
  ExecutionOwnerType,
)
from quantx_domain.strategies.base import (
  SymbolRuntimeStatePatch,
  TAssistantExecutionIntentOrigin,
  TradeIntent,
  TradeIntentDirection,
)
from quantx_domain.trading.t_assistant_execution import (
  TAssistantEntryReadiness,
  TAssistantExecutionEvent,
  TAssistantExecutionStatus,
  TDecisionCycleStatus,
  stable_manifest_hash,
)
from quantx_domain.trading.t_assistant_market_state import (
  TAssistantSymbolState,
  TDecisionSnapshot,
  candidate_evidence_key,
  decode_candidate_evidence,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantDecisionCycleRecord,
  TAssistantExecutionRecord,
)
from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
  TTradeOpportunityEvaluation,
)
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from quantx_infrastructure.repositories.t_assistant_symbol_state_repository import (
  TAssistantSymbolStateConflict,
  TAssistantSymbolStateRepository,
)
from quantx_infrastructure.repositories.t_trade_opportunity_intelligence_repository import (
  TTradeOpportunityEvaluationRepository,
  _evaluation_fingerprint,
)
from quantx_infrastructure.repositories.trade_intent_repository import (
  TradeIntentRepository,
)
from quantx_infrastructure.services.trade_intent_intake import (
  trade_intent_material_from_payload,
  trade_intent_record_data,
)

T_CYCLE_LEASE_CONFLICT = "T_CYCLE_LEASE_CONFLICT"
T_CYCLE_INPUT_STALE = "T_CYCLE_INPUT_STALE"


class TAssistantCycleConflict(RuntimeError):
  pass


@dataclass(frozen=True)
class TAssistantCycleClaim:
  cycle_id: str
  processing_owner: str
  processing_fence_token: str
  processing_lease_until: datetime


def _aware(value: datetime) -> datetime:
  return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _same_instant(value: datetime, serialized: Any) -> bool:
  if not isinstance(serialized, str):
    return False
  try:
    expected = datetime.fromisoformat(serialized)
  except ValueError:
    return False
  if expected.tzinfo is None:
    return False
  return _aware(value) == expected


class TAssistantDecisionCycleRepository:
  def __init__(self, db: AsyncSession) -> None:
    self.db = db
    self._symbol_states = TAssistantSymbolStateRepository(db)
    self._executions = TAssistantExecutionRepository(db)

  async def get(
    self,
    cycle_id: str,
    *,
    for_update: bool = False,
  ) -> Optional[TAssistantDecisionCycleRecord]:
    statement = select(TAssistantDecisionCycleRecord).where(
      TAssistantDecisionCycleRecord.cycle_id == cycle_id
    )
    if for_update:
      # All cycle writers, including allocation recovery, use execution first.
      # The unlocked identity probe is safe because cycle ownership is immutable.
      execution_id = await self.db.scalar(
        select(TAssistantDecisionCycleRecord.execution_id).where(
          TAssistantDecisionCycleRecord.cycle_id == cycle_id
        )
      )
      if execution_id is None:
        return None
      await self.db.scalar(
        select(TAssistantExecutionRecord)
        .where(TAssistantExecutionRecord.execution_id == execution_id)
        .with_for_update()
        .execution_options(populate_existing=True)
      )
      statement = statement.with_for_update().execution_options(populate_existing=True)
    result = await self.db.execute(statement)
    return result.scalar_one_or_none()

  async def prepare_material_cycle(
    self,
    *,
    snapshot: TDecisionSnapshot,
    cycle_id: str,
    now: datetime,
  ) -> TAssistantDecisionCycleRecord:
    if now.tzinfo is None:
      raise ValueError("T-assistant cycle time must be timezone-aware")
    execution = await self.db.scalar(
      select(TAssistantExecutionRecord)
      .where(TAssistantExecutionRecord.execution_id == snapshot.execution_ref.owner_id)
      .with_for_update()
    )
    if execution is None:
      raise TAssistantCycleConflict("T_ASSISTANT_EXECUTION_NOT_FOUND")
    self._validate_execution_snapshot(execution, snapshot)
    decision_key = decision_key_for_snapshot(snapshot)
    existing = await self.db.scalar(
      select(TAssistantDecisionCycleRecord)
      .where(
        TAssistantDecisionCycleRecord.execution_id == execution.execution_id,
        TAssistantDecisionCycleRecord.decision_key == decision_key,
      )
      .order_by(TAssistantDecisionCycleRecord.attempt.desc())
      .limit(1)
      .with_for_update()
    )
    if existing is not None and existing.status in {
      TDecisionCycleStatus.PREPARED.value,
      TDecisionCycleStatus.PROPOSALS_COMMITTED.value,
    }:
      return existing

    attempt = int(existing.attempt) + 1 if existing is not None else 1
    sequence = int(execution.last_assigned_cycle_sequence) + 1
    manifest = snapshot.decision_payload()
    input_manifest_hash = stable_manifest_hash(manifest)
    record = TAssistantDecisionCycleRecord(
      cycle_id=cycle_id,
      execution_id=execution.execution_id,
      cycle_sequence=sequence,
      decision_key=decision_key,
      attempt=attempt,
      snapshot_hash=snapshot.snapshot_hash,
      fence_from=min(
        (
          item.delta_slice.from_accepted_sequence_exclusive for item in snapshot.symbols
        ),
        default=snapshot.fence_sequence,
      ),
      fence_to=snapshot.fence_sequence,
      market_delta_manifest_hash=snapshot.market_delta_manifest_hash,
      reducer_cursor_manifest_hash=snapshot.reducer_cursor_manifest_hash,
      evaluated_symbol_count=len(snapshot.symbols),
      material_symbol_count=0,
      proposed_intent_count=0,
      status=TDecisionCycleStatus.PREPARED.value,
      processing_owner=None,
      processing_fence_token=None,
      processing_lease_until=None,
      input_manifest_hash=input_manifest_hash,
      input_manifest=manifest,
      output_manifest_hash=None,
      output_manifest=None,
      prepared_at=now,
      committed_at=None,
      abort_reason=None,
      error_detail=None,
    )
    self.db.add(record)
    execution.last_assigned_cycle_sequence = sequence
    execution.state_version = int(execution.state_version) + 1
    await self._executions.append_event(
      TAssistantExecutionEvent(
        execution_id=execution.execution_id,
        event_key=f"cycle-prepared:{cycle_id}",
        event_type="DECISION_CYCLE_PREPARED",
        occurred_at=now,
        payload={
          "cycle_id": cycle_id,
          "cycle_sequence": sequence,
          "decision_key": decision_key,
          "attempt": attempt,
          "input_manifest_hash": input_manifest_hash,
        },
      )
    )
    await self.db.flush()
    return record

  async def claim(
    self,
    *,
    cycle_id: str,
    processing_owner: str,
    expected_input_manifest_hash: str,
    now: datetime,
  ) -> TAssistantCycleClaim:
    owner = str(processing_owner or "").strip()
    if not owner or now.tzinfo is None:
      raise ValueError("cycle claim requires owner and timezone-aware time")
    cycle = await self.get(cycle_id, for_update=True)
    if cycle is None:
      raise TAssistantCycleConflict("T_CYCLE_NOT_FOUND")
    if cycle.status != TDecisionCycleStatus.PREPARED.value:
      raise TAssistantCycleConflict("T_CYCLE_NOT_PREPARED")
    lease_until = (
      _aware(cycle.processing_lease_until)
      if cycle.processing_lease_until is not None
      else None
    )
    if lease_until is not None and lease_until > now:
      raise TAssistantCycleConflict(T_CYCLE_LEASE_CONFLICT)
    token = uuid.uuid4().hex
    cycle.processing_owner = owner
    cycle.processing_fence_token = token
    cycle.processing_lease_until = now + timedelta(
      seconds=T_ASSISTANT_CYCLE_LEASE_SECONDS
    )
    if cycle.input_manifest_hash != expected_input_manifest_hash:
      await self._abort_locked(cycle, now=now, reason=T_CYCLE_INPUT_STALE)
      raise TAssistantCycleConflict(T_CYCLE_INPUT_STALE)
    prepared_at = _aware(cycle.prepared_at)
    if (now - prepared_at).total_seconds() > T_ASSISTANT_ENTRY_CYCLE_TTL_SECONDS:
      await self._abort_locked(cycle, now=now, reason=T_CYCLE_INPUT_STALE)
      raise TAssistantCycleConflict(T_CYCLE_INPUT_STALE)
    await self.db.flush()
    return TAssistantCycleClaim(
      cycle_id=cycle_id,
      processing_owner=owner,
      processing_fence_token=token,
      processing_lease_until=cycle.processing_lease_until,
    )

  async def renew(
    self,
    *,
    claim: TAssistantCycleClaim,
    now: datetime,
  ) -> Optional[TAssistantCycleClaim]:
    if now.tzinfo is None:
      raise ValueError("cycle renew requires timezone-aware time")
    cycle = await self.get(claim.cycle_id, for_update=True)
    if (
      cycle is None
      or cycle.status != TDecisionCycleStatus.PREPARED.value
      or cycle.processing_owner != claim.processing_owner
      or cycle.processing_fence_token != claim.processing_fence_token
    ):
      raise TAssistantCycleConflict(T_CYCLE_LEASE_CONFLICT)
    if (
      _aware(cycle.processing_lease_until) <= now
      or (now - _aware(cycle.prepared_at)).total_seconds()
      > T_ASSISTANT_ENTRY_CYCLE_TTL_SECONDS
    ):
      await self._abort_locked(cycle, now=now, reason=T_CYCLE_INPUT_STALE)
      return None
    cycle.processing_lease_until = now + timedelta(
      seconds=T_ASSISTANT_CYCLE_LEASE_SECONDS
    )
    await self.db.flush()
    return TAssistantCycleClaim(
      cycle_id=claim.cycle_id,
      processing_owner=claim.processing_owner,
      processing_fence_token=claim.processing_fence_token,
      processing_lease_until=cycle.processing_lease_until,
    )

  async def commit_material_cycle(
    self,
    *,
    claim: TAssistantCycleClaim,
    expected_input_manifest_hash: str,
    symbol_patches: Iterable[SymbolRuntimeStatePatch],
    opportunity_evidence: Iterable[Mapping[str, Any]],
    execution_events: Iterable[TAssistantExecutionEvent],
    trade_intents: Iterable[TradeIntent],
    now: datetime,
  ) -> TAssistantDecisionCycleRecord:
    trade_intents = tuple(trade_intents)
    symbol_patches = tuple(symbol_patches)
    opportunity_evidence = tuple(opportunity_evidence)
    execution_events = tuple(execution_events)
    cycle = await self.get(claim.cycle_id, for_update=True)
    if cycle is None:
      raise TAssistantCycleConflict("T_CYCLE_NOT_FOUND")
    if cycle.status == TDecisionCycleStatus.PROPOSALS_COMMITTED.value:
      execution = await self.db.get(TAssistantExecutionRecord, cycle.execution_id)
      if execution is None or cycle.input_manifest_hash != expected_input_manifest_hash:
        raise TAssistantCycleConflict("T_CYCLE_IDEMPOTENCY_CONFLICT")
      payloads, references = await self._standard_intent_payloads(
        cycle=cycle,
        execution=execution,
        trade_intents=trade_intents,
        evidence_rows=opportunity_evidence,
        allowed_symbols=set(dict(cycle.input_manifest.get("symbols") or {})),
      )
      retry_states = [
        TAssistantSymbolState.from_dict(patch.patch.set["symbol_state"])
        for patch in symbol_patches
        if patch.material
      ]
      manifest = self._output_manifest(
        cycle,
        retry_states,
        opportunity_evidence,
        execution_events,
        payloads,
        references,
      )
      if stable_manifest_hash(manifest) != cycle.output_manifest_hash:
        raise TAssistantCycleConflict("T_CYCLE_IDEMPOTENCY_CONFLICT")
      return cycle
    if (
      cycle.status != TDecisionCycleStatus.PREPARED.value
      or cycle.processing_owner != claim.processing_owner
      or cycle.processing_fence_token != claim.processing_fence_token
      or cycle.input_manifest_hash != expected_input_manifest_hash
      or cycle.processing_lease_until is None
    ):
      raise TAssistantCycleConflict(T_CYCLE_LEASE_CONFLICT)
    if (
      _aware(cycle.processing_lease_until) <= now
      or (now - _aware(cycle.prepared_at)).total_seconds()
      > T_ASSISTANT_ENTRY_CYCLE_TTL_SECONDS
    ):
      await self._abort_locked(cycle, now=now, reason=T_CYCLE_INPUT_STALE)
      return cycle

    execution = await self.db.scalar(
      select(TAssistantExecutionRecord)
      .where(TAssistantExecutionRecord.execution_id == cycle.execution_id)
      .with_for_update()
    )
    if execution is None:
      raise TAssistantCycleConflict("T_ASSISTANT_EXECUTION_NOT_FOUND")
    if execution.environment != ExecutionEnvironment.PAPER.value:
      raise TAssistantCycleConflict("OWNER_ENVIRONMENT_MISMATCH")
    if not self._execution_matches_manifest(execution, cycle.input_manifest):
      await self._abort_locked(cycle, now=now, reason=T_CYCLE_INPUT_STALE)
      return cycle

    allowed_symbols = set(dict(cycle.input_manifest.get("symbols") or {}))
    material_patches = tuple(item for item in symbol_patches if item.material)
    states: list[TAssistantSymbolState] = []
    expected_revisions: dict[str, int] = {}
    for patch in material_patches:
      raw = patch.patch.set.get("symbol_state")
      if not isinstance(raw, Mapping):
        raise TAssistantCycleConflict("T_SYMBOL_STATE_PATCH_INVALID")
      state = TAssistantSymbolState.from_dict(raw)
      if (
        state.execution_id != cycle.execution_id
        or state.instrument_code not in allowed_symbols
      ):
        raise TAssistantCycleConflict("OWNER_CONFLICT")
      states.append(state)
      expected_revisions[state.instrument_code] = patch.expected_revision
    try:
      async with self.db.begin_nested():
        return await self._commit_material_locked(
          cycle=cycle,
          execution=execution,
          states=states,
          expected_revisions=expected_revisions,
          allowed_symbols=allowed_symbols,
          opportunity_evidence=opportunity_evidence,
          execution_events=execution_events,
          trade_intents=trade_intents,
          now=now,
        )
    except TAssistantSymbolStateConflict:
      await self._abort_locked(cycle, now=now, reason=T_CYCLE_INPUT_STALE)
      return cycle

  async def _commit_material_locked(
    self,
    *,
    cycle: TAssistantDecisionCycleRecord,
    execution: TAssistantExecutionRecord,
    states: list[TAssistantSymbolState],
    expected_revisions: dict[str, int],
    allowed_symbols: set[str],
    opportunity_evidence: Iterable[Mapping[str, Any]],
    execution_events: Iterable[TAssistantExecutionEvent],
    trade_intents: Iterable[TradeIntent],
    now: datetime,
  ) -> TAssistantDecisionCycleRecord:
    """Commit the entire material unit inside the caller's savepoint.

    The runtime catches domain conflicts to preserve explicitly terminalized
    stale cycles. Late validation or write errors must therefore undo symbol
    states, evidence, events and commit watermarks before that outer catch.
    """
    await self._symbol_states.apply_material_states(
      states,
      expected_revisions=expected_revisions,
    )
    evidence_rows = tuple(opportunity_evidence)
    for evidence in evidence_rows:
      await self._append_opportunity_evidence(
        cycle=cycle,
        execution=execution,
        evidence=evidence,
      )
    events = tuple(execution_events)
    for event in events:
      if event.execution_id != cycle.execution_id:
        raise TAssistantCycleConflict("OWNER_CONFLICT")
      event_symbol = str(event.payload.get("instrument_code") or "").upper()
      if event_symbol and event_symbol not in allowed_symbols:
        raise TAssistantCycleConflict("T_EXECUTION_EVENT_SYMBOL_CONFLICT")
      await self._executions.append_event(event)

    intent_payloads, references = await self._standard_intent_payloads(
      cycle=cycle,
      execution=execution,
      trade_intents=trade_intents,
      evidence_rows=evidence_rows,
      allowed_symbols=allowed_symbols,
    )
    await TradeIntentRepository(self.db).accept_intents_idempotent(intent_payloads)
    output_manifest = self._output_manifest(
      cycle,
      states,
      evidence_rows,
      events,
      intent_payloads,
      references,
    )
    output_hash = stable_manifest_hash(output_manifest)
    cycle.material_symbol_count = len(states)
    cycle.proposed_intent_count = len(intent_payloads)
    cycle.output_manifest = output_manifest
    cycle.output_manifest_hash = output_hash
    cycle.status = TDecisionCycleStatus.PROPOSALS_COMMITTED.value
    cycle.committed_at = now
    cycle.processing_owner = None
    cycle.processing_fence_token = None
    cycle.processing_lease_until = None
    execution.last_committed_cycle_sequence = max(
      int(execution.last_committed_cycle_sequence),
      int(cycle.cycle_sequence),
    )
    execution.checkpoint_revision = int(execution.checkpoint_revision) + 1
    execution.state_version = int(execution.state_version) + 1
    await self._executions.append_event(
      TAssistantExecutionEvent(
        execution_id=cycle.execution_id,
        event_key=f"cycle-committed:{cycle.cycle_id}:{output_hash}",
        event_type="DECISION_CYCLE_PROPOSALS_COMMITTED",
        occurred_at=now,
        payload={
          "cycle_id": cycle.cycle_id,
          "cycle_sequence": int(cycle.cycle_sequence),
          "output_manifest_hash": output_hash,
          "material_symbol_count": len(states),
          "proposed_intent_count": len(intent_payloads),
          "paper_shadow_only": True,
        },
      )
    )
    await self.db.flush()
    return cycle

  async def _standard_intent_payloads(
    self,
    *,
    cycle,
    execution,
    trade_intents,
    evidence_rows,
    allowed_symbols,
  ) -> tuple[list[dict[str, Any]], dict[str, dict[str, str]]]:
    """Validate producer scope before the sole standard intent intake."""
    payloads = []
    references = {}
    for intent in trade_intents:
      if not isinstance(intent, TradeIntent):
        raise TAssistantCycleConflict("T_INTENT_TYPE_INVALID")
      origin = intent.origin
      metadata = dict(intent.metadata)
      if execution.environment != ExecutionEnvironment.PAPER.value or any(
        metadata.get(key, "PAPER") != "PAPER"
        for key in ("environment", "execution_environment")
      ):
        raise TAssistantCycleConflict("OWNER_ENVIRONMENT_MISMATCH")
      if (
        intent.execution_ref
        != ExecutionOwnerRef(
          ExecutionOwnerType.T_ASSISTANT_EXECUTION,
          execution.execution_id,
        )
        or not isinstance(origin, TAssistantExecutionIntentOrigin)
        or origin.execution_ref != intent.execution_ref
        or origin.cycle_id != cycle.cycle_id
        or intent.run_id
        or intent.instrument_code not in allowed_symbols
        or metadata.get("source_execution_ref") != intent.execution_ref.to_dict()
        or metadata.get("account_id", execution.account_id) != execution.account_id
      ):
        raise TAssistantCycleConflict("T_INTENT_OWNER_CONFLICT")
      if (
        intent.direction is not TradeIntentDirection.BUY
        or metadata.get("t_trade_role") != "entry"
      ):
        raise TAssistantCycleConflict("T_INTENT_ENTRY_DIRECTION_INVALID")
      candidate_id = metadata.get("candidate_id")
      fingerprint = metadata.get("candidate_fingerprint")
      if (
        not candidate_id
        or not fingerprint
        or origin.candidate_id != candidate_id
        or origin.opportunity_id != candidate_id
        or metadata.get("policy_version") != execution.policy_version
        or metadata.get("feature_schema_version") != execution.feature_schema_version
      ):
        raise TAssistantCycleConflict("T_INTENT_CANDIDATE_CONFLICT")
      key = candidate_evidence_key(execution.execution_id, fingerprint)
      event = await TTradeOpportunityEvaluationRepository(self.db).get_by_event_key(key)
      if (
        event is None
        or event.event_type != "T_OPPORTUNITY_CANDIDATE_FROZEN"
        or event.owner_type != "T_ASSISTANT_EXECUTION"
        or event.owner_id != execution.execution_id
        or event.environment != "PAPER"
        or event.account_id != execution.account_id
        or event.instrument_code != intent.instrument_code
      ):
        raise TAssistantCycleConflict("T_INTENT_CANDIDATE_EVIDENCE_REQUIRED")
      witness = event.payload.get("candidate_evidence")
      supplied = [row for row in evidence_rows if row.get("event_key") == key]
      if any(
        stable_manifest_hash(row["payload"].get("candidate_evidence"))
        != stable_manifest_hash(witness)
        for row in supplied
      ):
        raise TAssistantCycleConflict("T_INTENT_CANDIDATE_EVIDENCE_CONFLICT")
      try:
        candidate, tick, cursor = decode_candidate_evidence(witness)
      except (TypeError, ValueError) as exc:
        raise TAssistantCycleConflict("T_INTENT_CANDIDATE_EVIDENCE_INVALID") from exc
      material = {
        column.key: getattr(event, column.key)
        for column in event.__mapper__.column_attrs
      }
      if (
        event.content_fingerprint != _evaluation_fingerprint(material)
        or candidate.candidate_id != candidate_id
        or candidate.fingerprint != fingerprint
        or candidate.policy_version != execution.policy_version
        or candidate.feature_schema_version != execution.feature_schema_version
        or metadata.get("source_time_ms") != candidate.source_time_ms
        or metadata.get("tick_ordinal") != candidate.tick_ordinal
        or metadata.get("opportunity_score") != candidate.score
        or tick.instrument_code != intent.instrument_code
        or cursor.stream_id != cycle.input_manifest["stream_id"]
        or cursor.continuity_generation != cycle.input_manifest["continuity_generation"]
      ):
        raise TAssistantCycleConflict("T_INTENT_CANDIDATE_CONFLICT")
      references[intent.intent_id] = {
        "candidate_evidence_key": key,
        "candidate_evidence_hash": stable_manifest_hash(witness),
      }
      payload = trade_intent_record_data(
        intent,
        status="ALLOCATION_PENDING",
        environment=ExecutionEnvironment.PAPER,
      )
      payload.update(
        account_id=execution.account_id,
        allocation_cycle_id=cycle.cycle_id,
        allocation_version=0,
      )
      payloads.append(payload)
    return payloads, references

  @staticmethod
  def _output_manifest(
    cycle, states, evidence_rows, events, intent_payloads, references
  ):
    return {
      "cycle_id": cycle.cycle_id,
      "symbol_state_manifest": {
        state.instrument_code: state.material_manifest_hash for state in states
      },
      "opportunity_event_keys": [
        str(item.get("event_key") or "") for item in evidence_rows
      ],
      "accepted_intents": [
        {
          "intent_id": payload["id"],
          "intake_hash": stable_manifest_hash(
            trade_intent_material_from_payload(payload)
          ),
          **references[payload["id"]],
        }
        for payload in intent_payloads
      ],
      "execution_event_keys": [event.event_key for event in events],
    }

  async def abort_stale(
    self,
    *,
    cycle_id: str,
    expected_fence_token: str,
    now: datetime,
    reason: str = T_CYCLE_INPUT_STALE,
  ) -> TAssistantDecisionCycleRecord:
    cycle = await self.get(cycle_id, for_update=True)
    if cycle is None:
      raise TAssistantCycleConflict("T_CYCLE_NOT_FOUND")
    if cycle.status != TDecisionCycleStatus.PREPARED.value:
      return cycle
    if not expected_fence_token:
      raise ValueError("abort stale requires a claim fence token")
    if cycle.processing_fence_token != expected_fence_token:
      raise TAssistantCycleConflict(T_CYCLE_LEASE_CONFLICT)
    await self._abort_locked(cycle, now=now, reason=reason)
    return cycle

  async def list_recoverable(
    self,
    *,
    execution_id: str,
  ) -> list[TAssistantDecisionCycleRecord]:
    result = await self.db.execute(
      select(TAssistantDecisionCycleRecord)
      .where(
        TAssistantDecisionCycleRecord.execution_id == execution_id,
        TAssistantDecisionCycleRecord.status == TDecisionCycleStatus.PREPARED.value,
      )
      .order_by(TAssistantDecisionCycleRecord.cycle_sequence.asc())
    )
    return list(result.scalars().all())

  async def terminalize_recoverable(
    self,
    *,
    cycle_id: str,
    now: datetime,
    reason: str,
  ) -> TAssistantDecisionCycleRecord:
    """Fence a PREPARED cycle after its execution authority is revoked."""

    cycle = await self.get(cycle_id, for_update=True)
    if cycle is None:
      raise TAssistantCycleConflict("T_CYCLE_NOT_FOUND")
    if cycle.status != TDecisionCycleStatus.PREPARED.value:
      return cycle
    await self._abort_locked(cycle, now=now, reason=reason)
    return cycle

  async def _abort_locked(
    self,
    cycle: TAssistantDecisionCycleRecord,
    *,
    now: datetime,
    reason: str,
  ) -> None:
    cycle.status = TDecisionCycleStatus.ABORTED_STALE.value
    cycle.abort_reason = reason
    cycle.committed_at = now
    cycle.processing_owner = None
    cycle.processing_fence_token = None
    cycle.processing_lease_until = None
    await self._executions.append_event(
      TAssistantExecutionEvent(
        execution_id=cycle.execution_id,
        event_key=f"cycle-aborted-stale:{cycle.cycle_id}:{reason}",
        event_type="DECISION_CYCLE_ABORTED_STALE",
        occurred_at=now,
        payload={"cycle_id": cycle.cycle_id, "reason": reason},
      )
    )
    await self.db.flush()

  @staticmethod
  def _validate_execution_snapshot(
    execution: TAssistantExecutionRecord,
    snapshot: TDecisionSnapshot,
  ) -> None:
    if (
      snapshot.execution_ref.owner_type is not ExecutionOwnerType.T_ASSISTANT_EXECUTION
    ):
      raise TAssistantCycleConflict("OWNER_TYPE_INVALID")
    if execution.environment != ExecutionEnvironment.PAPER.value:
      raise TAssistantCycleConflict("OWNER_ENVIRONMENT_MISMATCH")
    if execution.status not in {
      TAssistantExecutionStatus.WARMING.value,
      TAssistantExecutionStatus.RUNNING.value,
    }:
      raise TAssistantCycleConflict("T_ASSISTANT_EXECUTION_NOT_ENTRY_ACTIVE")
    if (
      int(execution.frozen_config_version) != snapshot.config_version
      or execution.config_snapshot_hash != snapshot.config_snapshot_hash
      or execution.policy_version != snapshot.policy_version
      or int(execution.feature_schema_version) != snapshot.feature_schema_version
      or int(execution.universe_revision) != snapshot.universe_revision
      or execution.status != snapshot.execution_status.value
      or execution.entry_readiness != snapshot.entry_readiness.value
      or _aware(execution.entry_readiness_as_of) != snapshot.entry_readiness_as_of
      or execution.scorer_mode != snapshot.scorer_mode
      or (
        stable_manifest_hash(dict(execution.model_runtime_binding))
        if execution.model_runtime_binding is not None
        else None
      )
      != snapshot.model_runtime_binding_hash
    ):
      raise TAssistantCycleConflict(T_CYCLE_INPUT_STALE)

  @staticmethod
  def _execution_matches_manifest(
    execution: TAssistantExecutionRecord,
    manifest: Mapping[str, Any],
  ) -> bool:
    execution_ref = manifest.get("execution_ref")
    expected_binding_hash = (
      stable_manifest_hash(dict(execution.model_runtime_binding))
      if execution.model_runtime_binding is not None
      else None
    )
    return bool(
      isinstance(execution_ref, Mapping)
      and execution_ref.get("owner_type")
      == ExecutionOwnerType.T_ASSISTANT_EXECUTION.value
      and execution_ref.get("owner_id") == execution.execution_id
      and execution.environment == ExecutionEnvironment.PAPER.value
      and execution.status
      in {
        TAssistantExecutionStatus.WARMING.value,
        TAssistantExecutionStatus.RUNNING.value,
      }
      and execution.entry_readiness
      in {
        TAssistantEntryReadiness.WARMING.value,
        TAssistantEntryReadiness.READY.value,
      }
      and execution.status == manifest.get("execution_status")
      and execution.entry_readiness == manifest.get("entry_readiness")
      and _same_instant(
        execution.entry_readiness_as_of,
        manifest.get("entry_readiness_as_of"),
      )
      and int(execution.universe_revision) == int(manifest.get("universe_revision", -1))
      and int(execution.frozen_config_version)
      == int(manifest.get("config_version", -1))
      and execution.config_snapshot_hash == manifest.get("config_snapshot_hash")
      and execution.policy_version == manifest.get("policy_version")
      and int(execution.feature_schema_version)
      == int(manifest.get("feature_schema_version", -1))
      and execution.scorer_mode == manifest.get("scorer_mode")
      and expected_binding_hash == manifest.get("model_runtime_binding_hash")
    )

  async def _append_opportunity_evidence(
    self,
    *,
    cycle: TAssistantDecisionCycleRecord,
    execution: TAssistantExecutionRecord,
    evidence: Mapping[str, Any],
  ) -> TTradeOpportunityEvaluation:
    event_key = str(evidence.get("event_key") or "").strip()
    instrument_code = str(evidence.get("instrument_code") or "").strip().upper()
    payload = evidence.get("payload")
    if not event_key or not instrument_code or not isinstance(payload, Mapping):
      raise TAssistantCycleConflict("T_OPPORTUNITY_EVIDENCE_INVALID")
    allowed_symbols = set(dict(cycle.input_manifest.get("symbols") or {}))
    payload_ref = payload.get("execution_ref")
    if (
      instrument_code not in allowed_symbols
      or not isinstance(payload_ref, Mapping)
      or payload_ref.get("owner_type") != ExecutionOwnerType.T_ASSISTANT_EXECUTION.value
      or payload_ref.get("owner_id") != execution.execution_id
      or payload.get("environment") != ExecutionEnvironment.PAPER.value
      or payload.get("cycle_id") != cycle.cycle_id
      or payload.get("paper_shadow_only") is not True
    ):
      raise TAssistantCycleConflict("T_OPPORTUNITY_EVIDENCE_OWNER_CONFLICT")
    evaluated_at = evidence.get("evaluated_at")
    if not isinstance(evaluated_at, datetime):
      raise TAssistantCycleConflict("T_OPPORTUNITY_EVIDENCE_INVALID")
    if evidence.get("event_type") == "T_OPPORTUNITY_CANDIDATE_FROZEN":
      try:
        candidate, tick, cursor = decode_candidate_evidence(
          payload["candidate_evidence"]
        )
        captured = datetime.fromisoformat(cycle.input_manifest["capture_as_of"])
      except (KeyError, TypeError, ValueError) as exc:
        raise TAssistantCycleConflict("T_CANDIDATE_EVIDENCE_INVALID") from exc
      if (
        event_key
        != candidate_evidence_key(execution.execution_id, candidate.fingerprint)
        or tick.instrument_code != instrument_code
        or cursor.stream_id != cycle.input_manifest["stream_id"]
        or cursor.continuity_generation != cycle.input_manifest["continuity_generation"]
        or tick.market_fence_sequence > cycle.fence_to
        or max(tick.received_at_ms, tick.sample.source_time_ms)
        > int(captured.timestamp() * 1000)
        or candidate.policy_version != execution.policy_version
        or candidate.feature_schema_version != execution.feature_schema_version
      ):
        raise TAssistantCycleConflict("T_CANDIDATE_EVIDENCE_BINDING_CONFLICT")
    return await TTradeOpportunityEvaluationRepository(self.db).append_material(
      event_key=event_key,
      account_id=execution.account_id,
      strategy_run_id=None,
      execution_ref=ExecutionOwnerRef(
        ExecutionOwnerType.T_ASSISTANT_EXECUTION,
        execution.execution_id,
      ),
      execution_environment=ExecutionEnvironment.PAPER,
      instrument_code=instrument_code,
      evaluated_at=evaluated_at,
      event_type=str(evidence.get("event_type") or "SYMBOL_MARKET_STATE_MATERIAL"),
      policy_version=execution.policy_version,
      schema_version=str(execution.feature_schema_version),
      payload=dict(payload),
      metrics=dict(evidence.get("metrics") or {}),
      commit=False,
    )


__all__ = [
  "TAssistantCycleClaim",
  "TAssistantCycleConflict",
  "TAssistantDecisionCycleRepository",
  "T_ASSISTANT_CYCLE_LEASE_RENEW_SECONDS",
  "T_ASSISTANT_CYCLE_LEASE_SECONDS",
  "T_ASSISTANT_CYCLE_POLICY_VERSION",
  "T_ASSISTANT_ENTRY_CYCLE_TTL_SECONDS",
  "T_CYCLE_INPUT_STALE",
  "T_CYCLE_LEASE_CONFLICT",
  "decision_key_for_snapshot",
]
