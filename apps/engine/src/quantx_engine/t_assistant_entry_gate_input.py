"""Build entry Gate input from persisted candidate material and an accepted Tick."""

from datetime import datetime

from quantx_application.t_trade_v3.entry_execution_gate import (
  EntryExecutionBinding,
  EntryExecutionGateInput,
  EntryExecutionGatePolicy,
  MarketDataCapabilityManifest,
)
from quantx_contracts import ExecutionEnvironment
from quantx_domain.trading.t_assistant_execution import stable_manifest_hash
from quantx_domain.trading.t_assistant_market_state import (
  TAssistantSymbolState,
  decode_candidate_evidence,
)
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantConfigVersionRecord,
  TAssistantDecisionCycleRecord,
  TAssistantSymbolStateRecord,
)
from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
  TTradeOpportunityEvaluation,
)
from sqlalchemy import select


async def build_entry_gate(db, execution, intent, witness, now, *, environment):
  if (
    environment not in {ExecutionEnvironment.PAPER, ExecutionEnvironment.LIVE}
    or execution.environment != environment.value
  ):
    raise ValueError("T_ENTRY_GATE_ENVIRONMENT_CONFLICT")
  cycle = await db.get(
    TAssistantDecisionCycleRecord, intent.allocation_cycle_id, populate_existing=True
  )
  if (
    cycle is None
    or stable_manifest_hash(cycle.output_manifest) != cycle.output_manifest_hash
  ):
    raise ValueError(f"{environment.value}_ENTRY_CYCLE_EVIDENCE_INVALID")
  references = [
    item
    for item in cycle.output_manifest["accepted_intents"]
    if item["intent_id"] == intent.id
  ]
  if len(references) != 1:
    raise ValueError(f"{environment.value}_ENTRY_CANDIDATE_REFERENCE_REQUIRED")
  reference = references[0]
  event = await db.scalar(
    select(TTradeOpportunityEvaluation).where(
      TTradeOpportunityEvaluation.event_key == reference["candidate_evidence_key"]
    )
  )
  if event is None:
    raise ValueError(f"{environment.value}_ENTRY_CANDIDATE_EVIDENCE_REQUIRED")
  candidate, _, cursor = decode_candidate_evidence(event.payload["candidate_evidence"])
  version = await db.get(TAssistantConfigVersionRecord, execution.config_version_id)
  policy_fields = dict(version.canonical_payload["entry_execution_gate_policy"])
  capabilities = MarketDataCapabilityManifest(**policy_fields.pop("capabilities"))
  policy = EntryExecutionGatePolicy(**policy_fields)
  binding = EntryExecutionBinding(
    candidate.fingerprint,
    execution.config_version_id,
    execution.config_snapshot_hash,
    execution.policy_version,
    policy.version,
    execution.feature_schema_version,
    capabilities.version,
    execution.scorer_mode,
    None,
  )
  row = await db.scalar(
    select(TAssistantSymbolStateRecord)
    .where(
      TAssistantSymbolStateRecord.execution_id == execution.execution_id,
      TAssistantSymbolStateRecord.instrument_code == intent.instrument_code,
    )
    .execution_options(populate_existing=True)
  )
  if row is None:
    raise ValueError(f"{environment.value}_ENTRY_SYMBOL_STATE_REQUIRED")
  state = TAssistantSymbolState.from_dict(row.state_payload)
  valid = (
    state.opportunity_state.candidate == candidate
    and state.opportunity_state.candidate_status.value
    in {"LATCHED", "AWAITING_APPROVAL"}
  )
  metadata = intent.intent_metadata
  created = datetime.fromisoformat(metadata["intent_created_at"])
  created_ms = int(created.timestamp() * 1000)
  sample = witness.latest_tick.sample
  quality = frozenset(
    field
    for field in ("price", "bid_price", "ask_price", "bid_volume", "ask_volume")
    if getattr(sample, field) is not None
  )
  return EntryExecutionGateInput(
    candidate,
    intent.instrument_code,
    intent.id,
    True,
    valid,
    created_ms,
    min(created_ms, metadata["source_time_ms"]) + metadata["approval_ttl_ms"],
    int(now.timestamp() * 1000),
    environment,
    binding,
    binding,
    policy,
    capabilities,
    cursor.stream_id,
    cursor.continuity_generation,
    cursor.accepted_sequence,
    cursor.ring_generation,
    witness.ring_generation,
    witness.last_accepted_sequence,
    witness.latest_tick,
    quality,
  )
