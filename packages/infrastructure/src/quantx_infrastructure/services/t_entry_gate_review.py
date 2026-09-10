"""Validate persisted candidate and frozen gate material for PAPER and LIVE."""

from dataclasses import dataclass, fields
from datetime import datetime

from quantx_application.t_trade_v3.entry_execution_gate import (
  EntryExecutionBinding,
  EntryExecutionGate,
  EntryExecutionGateInput,
  EntryExecutionGatePolicy,
  MarketDataCapabilityManifest,
)
from quantx_contracts import ExecutionEnvironment
from quantx_domain.clock import SHANGHAI
from quantx_domain.trading.t_assistant_execution import (
  TAssistantConfigVersion,
  TModelRuntimeBinding,
  stable_manifest_hash,
)
from quantx_domain.trading.t_assistant_market_state import (
  TAssistantSymbolState,
  candidate_evidence_key,
  decode_candidate_evidence,
)
from quantx_domain.trading.t_trade_opportunity_engine import OpportunityCandidate
from sqlalchemy import select

from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantConfigVersionRecord,
  TAssistantDecisionCycleRecord,
  TAssistantSymbolStateRecord,
)
from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
  TTradeOpportunityEvaluation,
)
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  _execution_from_record,
)
from quantx_infrastructure.repositories.t_trade_opportunity_intelligence_repository import (
  _evaluation_fingerprint,
)
from quantx_infrastructure.services.t_allocation_serialization import (
  allocation_time,
)
from quantx_infrastructure.services.trade_intent_intake import (
  trade_intent_initial_material,
)


@dataclass(frozen=True)
class TEntryGateReviewResult:
  outcome: str
  reason_codes: tuple[str, ...]
  candidate: OpportunityCandidate | None = None
  cycle: TAssistantDecisionCycleRecord | None = None


async def review_t_entry_gate(db, *, execution, intent, gate, now: datetime):
  """Caller locks head/execution; this function locks cycle and symbol evidence."""
  if not db.in_transaction():
    raise ValueError("T_ENTRY_REVIEW_TRANSACTION_REQUIRED")
  if not isinstance(gate, EntryExecutionGateInput):
    raise TypeError("T_ENTRY_REVIEW_TYPED_GATE_REQUIRED")
  environment = ExecutionEnvironment(execution.environment)
  execution_id, intent_id = execution.execution_id, intent.id
  if environment not in {ExecutionEnvironment.PAPER, ExecutionEnvironment.LIVE}:
    raise ValueError("T_ENTRY_REVIEW_ENVIRONMENT_INVALID")
  if now.tzinfo is None or now.utcoffset() is None:
    raise ValueError("T_ENTRY_REVIEW_AWARE_TIME_REQUIRED")

  def deny(reason, outcome="REJECT"):
    return TEntryGateReviewResult(outcome, (reason,))

  async def get(model, identity, *, lock=False):
    return await db.get(model, identity, with_for_update=lock, populate_existing=True)

  if (
    intent.owner_type != "T_ASSISTANT_EXECUTION"
    or intent.owner_id != execution_id
    or intent.account_id != execution.account_id
    or intent.environment != environment.value
    or intent.direction != "BUY"
    or intent.status != "EXECUTION_READY"
  ):
    return deny(f"{environment.value}_REVIEW_INTENT_SCOPE")
  version = await get(TAssistantConfigVersionRecord, execution.config_version_id)
  if version is None:
    return deny(f"{environment.value}_REVIEW_CONFIG_REQUIRED")
  TAssistantConfigVersion(
    **{
      field.name: getattr(version, field.name)
      for field in fields(TAssistantConfigVersion)
    }
  )
  try:
    gate_config = dict(version.canonical_payload["entry_execution_gate_policy"])
    capabilities = MarketDataCapabilityManifest(**gate_config.pop("capabilities"))
    policy = EntryExecutionGatePolicy(**gate_config)
  except (KeyError, TypeError, ValueError):
    return deny(f"{environment.value}_REVIEW_FROZEN_GATE_POLICY_REQUIRED")
  cycle = await get(
    TAssistantDecisionCycleRecord, intent.allocation_cycle_id, lock=True
  )
  symbol = await db.scalar(
    select(TAssistantSymbolStateRecord)
    .where(
      TAssistantSymbolStateRecord.execution_id == execution_id,
      TAssistantSymbolStateRecord.instrument_code == intent.instrument_code,
    )
    .with_for_update()
    .execution_options(populate_existing=True)
  )
  if (
    cycle is None
    or symbol is None
    or cycle.execution_id != execution_id
    or cycle.status != "PROPOSALS_COMMITTED"
    or stable_manifest_hash(cycle.output_manifest) != cycle.output_manifest_hash
    or stable_manifest_hash(cycle.input_manifest) != cycle.input_manifest_hash
  ):
    return deny(f"{environment.value}_REVIEW_CANDIDATE_EVIDENCE_REQUIRED")
  if (
    allocation_time(symbol.updated_at) > now or allocation_time(symbol.created_at) > now
  ):
    return deny(f"{environment.value}_REVIEW_FUTURE_SYMBOL_STATE")
  state = TAssistantSymbolState.from_dict(symbol.state_payload)
  metadata = intent.intent_metadata
  references = [
    item
    for item in cycle.output_manifest["accepted_intents"]
    if item["intent_id"] == intent_id
  ]
  if len(references) != 1:
    return deny(f"{environment.value}_REVIEW_CANDIDATE_EVIDENCE_REQUIRED")
  reference = references[0]
  source = await db.scalar(
    select(TTradeOpportunityEvaluation).where(
      TTradeOpportunityEvaluation.event_key == reference["candidate_evidence_key"]
    )
  )
  if (
    source is None
    or source.event_type != "T_OPPORTUNITY_CANDIDATE_FROZEN"
    or source.owner_type != "T_ASSISTANT_EXECUTION"
    or source.owner_id != execution_id
    or source.environment != environment.value
    or source.instrument_code != intent.instrument_code
    or source.account_id != intent.account_id
    or source.content_fingerprint
    != _evaluation_fingerprint(
      {
        column.key: getattr(source, column.key)
        for column in source.__mapper__.column_attrs
      }
    )
  ):
    return deny(f"{environment.value}_REVIEW_CANDIDATE_EVIDENCE_REQUIRED")
  witness = source.payload["candidate_evidence"]
  candidate, original_tick, cursor = decode_candidate_evidence(witness)
  evaluated_at = source.evaluated_at
  if evaluated_at.tzinfo is None:
    evaluated_at = evaluated_at.replace(tzinfo=SHANGHAI)
  if (
    allocation_time(source.created_at) > now
    or evaluated_at > now
    or max(original_tick.received_at_ms, witness["evaluation"]["evaluated_at_ms"])
    > int(now.timestamp() * 1000)
  ):
    return deny(f"{environment.value}_REVIEW_FUTURE_CANDIDATE_EVIDENCE")
  if stable_manifest_hash(witness) != reference["candidate_evidence_hash"] or reference[
    "candidate_evidence_key"
  ] != candidate_evidence_key(execution_id, candidate.fingerprint):
    return deny(f"{environment.value}_REVIEW_CANDIDATE_BINDING_CONFLICT")
  if state.opportunity_state.candidate != candidate:
    return deny(f"{environment.value}_REVIEW_CANDIDATE_EVIDENCE_ADVANCED", "DELAY")
  material_hash = stable_manifest_hash(
    {
      key: symbol.state_payload[key]
      for key in (
        "opportunity_state",
        "cursor",
        "lifecycle",
        "deferred_candidate",
        "deferred_candidate_fence_sequence",
      )
    }
  )
  if (
    candidate is None
    or cursor is None
    or state.material_manifest_hash != symbol.material_manifest_hash
    or material_hash != symbol.material_manifest_hash
    or state.execution_id != execution_id
    or state.instrument_code != intent.instrument_code
    or gate.candidate != candidate
    or candidate.candidate_id != metadata.get("candidate_id")
    or candidate.fingerprint != metadata.get("candidate_fingerprint")
    or not any(
      item["intent_id"] == intent_id
      and item["intake_hash"]
      == stable_manifest_hash(trade_intent_initial_material(intent))
      for item in cycle.output_manifest["accepted_intents"]
    )
  ):
    return deny(f"{environment.value}_REVIEW_CANDIDATE_BINDING_CONFLICT")
  expected = EntryExecutionBinding(
    candidate.fingerprint,
    execution.config_version_id,
    execution.config_snapshot_hash,
    execution.policy_version,
    policy.version,
    execution.feature_schema_version,
    capabilities.version,
    execution.scorer_mode,
    (TModelRuntimeBinding.from_mapping(execution.model_runtime_binding).binding_hash
      if execution.model_runtime_binding is not None else None),
  )
  created = datetime.fromisoformat(metadata["intent_created_at"])
  deadline = (
    min(int(created.timestamp() * 1000), metadata["source_time_ms"])
    + metadata["approval_ttl_ms"]
  )
  if (
    gate.intent_id != intent_id
    or gate.instrument_code != intent.instrument_code
    or not gate.intent_active
    or gate.execution_environment is not environment
    or gate.frozen_binding != expected
    or gate.current_binding != expected
    or gate.policy != policy
    or gate.capabilities != capabilities
    or gate.intent_created_at_ms != int(created.timestamp() * 1000)
    or gate.intent_expires_at_ms != deadline
    or gate.evaluated_at_ms != int(now.timestamp() * 1000)
    or gate.stream_id != cursor.stream_id
    or gate.continuity_generation != cursor.continuity_generation
    or gate.candidate_accepted_sequence != cursor.accepted_sequence
    or gate.candidate_ring_generation != cursor.ring_generation
    or gate.latest_ring_generation != symbol.ring_generation
    or gate.candidate_valid
    != (
      state.opportunity_state.candidate_status.value in {"LATCHED", "AWAITING_APPROVAL"}
    )
  ):
    return deny(f"{environment.value}_REVIEW_GATE_BINDING_CONFLICT")
  if environment is ExecutionEnvironment.LIVE:
    result = EntryExecutionGate.evaluate_live(
      gate, execution=_execution_from_record(execution)
    )
  else:
    result = EntryExecutionGate.evaluate(gate)
  return TEntryGateReviewResult(
    result.decision.value, result.reason_codes, candidate, cycle
  )
