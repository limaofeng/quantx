"""Read standard intent outcomes as causal inputs to the sole symbol reducer."""

from datetime import UTC

from quantx_domain.trading.t_trade_opportunity_engine import CandidateControl
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.t_allocation_serialization import allocation_time
from sqlalchemy import select

_SUPPRESS = frozenset(
  {"ROUTED", "PARTIAL_FILLED", "FILLED", "REJECTED", "EXPIRED", "CANCELLED"}
)


async def read_candidate_controls(
  db, *, execution_id, account_id, symbol_states, as_of
):
  if as_of.tzinfo is None or as_of.utcoffset() is None:
    raise ValueError("T_CANDIDATE_CONTROL_AWARE_TIME_REQUIRED")
  as_of = as_of.astimezone(UTC)
  candidates = {
    code: state.opportunity_state.candidate
    for code, state in symbol_states.items()
    if state.opportunity_state.candidate is not None
  }
  if not candidates:
    return {}
  rows = list(
    (
      await db.scalars(
        select(TradeIntentRecord)
        .where(
          TradeIntentRecord.owner_type == "T_ASSISTANT_EXECUTION",
          TradeIntentRecord.owner_id == execution_id,
          TradeIntentRecord.account_id == account_id,
          TradeIntentRecord.environment == "PAPER",
          TradeIntentRecord.direction == "BUY",
          TradeIntentRecord.instrument_code.in_(candidates),
          TradeIntentRecord.intent_metadata["candidate_id"]
          .as_string()
          .in_([candidate.candidate_id for candidate in candidates.values()]),
        )
        .execution_options(populate_existing=True)
      )
    ).all()
  )
  result, seen = {}, set()
  for row in rows:
    candidate = candidates[row.instrument_code]
    metadata = row.intent_metadata
    if (
      metadata.get("candidate_id") != candidate.candidate_id
      or metadata.get("candidate_fingerprint") != candidate.fingerprint
      or metadata.get("source_time_ms") != candidate.source_time_ms
    ):
      raise ValueError("T_CANDIDATE_CONTROL_BINDING_CONFLICT")
    if row.instrument_code in seen:
      raise ValueError("T_CANDIDATE_CONTROL_INTENT_AMBIGUOUS")
    seen.add(row.instrument_code)
    if (
      allocation_time(row.created_at) > as_of or allocation_time(row.updated_at) > as_of
    ):
      raise ValueError("T_CANDIDATE_CONTROL_FUTURE_INTENT")
    if row.status in _SUPPRESS:
      result[row.instrument_code] = CandidateControl(
        suppress_candidate_id=candidate.candidate_id
      )
    elif row.status == "AWAITING_APPROVAL":
      result[row.instrument_code] = CandidateControl(
        awaiting_approval_candidate_id=candidate.candidate_id
      )
  return result
