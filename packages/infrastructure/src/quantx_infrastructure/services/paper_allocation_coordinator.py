"""Project committed RULE_ONLY evidence into the one durable portfolio allocator.

The caller owns the transaction. No cash or inventory is reserved here; every
attempt, including recovery, is rebuilt from the current authoritative PAPER cut.
"""

from datetime import UTC, datetime, timedelta

from quantx_domain.clock import SHANGHAI
from quantx_domain.trading.t_assistant_execution import stable_manifest_hash
from sqlalchemy import select

from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
  TTradeOpportunityEvaluation,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_allocation_repository import (
  TAllocationConflict,
  TAllocationRepository,
)
from quantx_infrastructure.repositories.t_assistant_decision_cycle_repository import (
  TAssistantDecisionCycleRepository,
)
from quantx_infrastructure.repositories.t_trade_opportunity_intelligence_repository import (
  _evaluation_fingerprint,
)
from quantx_infrastructure.services.paper_portfolio_snapshot import (
  PaperPortfolioSnapshotReader,
)
from quantx_infrastructure.services.t_allocation_candidate_projection import (
  candidate_from_evaluation,
)
from quantx_infrastructure.services.t_allocation_serialization import allocation_time


class PaperAllocationCoordinator:
  def __init__(self, db):
    self.db = db

  async def allocate_cycle(
    self,
    *,
    execution_id: str,
    cycle_id: str,
    processing_owner: str,
    now: datetime,
    lease_seconds: int = 10,
  ):
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
      raise ValueError("T_ALLOCATION_AWARE_TIME_REQUIRED")
    TAllocationRepository._validate_lease(processing_owner, lease_seconds)
    now = now.astimezone(UTC)
    cycle = await TAssistantDecisionCycleRepository(self.db).get(
      cycle_id, for_update=True
    )
    if (
      cycle is None
      or cycle.execution_id != execution_id
      or cycle.status != "PROPOSALS_COMMITTED"
      or not isinstance(cycle.output_manifest, dict)
      or stable_manifest_hash(cycle.output_manifest) != cycle.output_manifest_hash
    ):
      raise TAllocationConflict("T_ALLOCATION_CYCLE_NOT_COMMITTED")
    rows = list(
      (
        await self.db.scalars(
          select(TradeIntentRecord)
          .where(
            TradeIntentRecord.allocation_cycle_id == cycle_id,
            TradeIntentRecord.status == "ALLOCATION_PENDING",
          )
          .order_by(TradeIntentRecord.id)
          .with_for_update()
          .execution_options(populate_existing=True)
        )
      ).all()
    )
    rows = [
      row
      for row in rows
      if row.allocation_next_eligible_at is None
      or allocation_time(row.allocation_next_eligible_at) <= now
    ]
    if not rows:
      return None
    accepted = {
      item["intent_id"]: item for item in cycle.output_manifest["accepted_intents"]
    }
    keys = [accepted[row.id]["candidate_evidence_key"] for row in rows]
    evidence = list(
      (
        await self.db.scalars(
          select(TTradeOpportunityEvaluation).where(
            TTradeOpportunityEvaluation.event_key.in_(keys)
          )
        )
      ).all()
    )
    if len(evidence) != len(keys) or len(set(keys)) != len(keys):
      raise TAllocationConflict("T_ALLOCATION_SOURCE_EVIDENCE_REQUIRED")
    for event in evidence:
      material = {
        column.key: getattr(event, column.key)
        for column in event.__mapper__.column_attrs
      }
      evaluated_at = event.evaluated_at
      if evaluated_at.tzinfo is None:
        evaluated_at = evaluated_at.replace(tzinfo=SHANGHAI)
      if (
        event.owner_type != "T_ASSISTANT_EXECUTION"
        or event.owner_id != execution_id
        or event.environment != "PAPER"
        or event.event_type != "T_OPPORTUNITY_CANDIDATE_FROZEN"
        or event.content_fingerprint != _evaluation_fingerprint(material)
        or evaluated_at > now
        or allocation_time(event.created_at) > now
      ):
        raise TAllocationConflict("T_ALLOCATION_SOURCE_EVIDENCE_CONFLICT")
    candidates = []
    for row in rows:
      matching = [
        event
        for event in evidence
        if event.candidate_id == row.intent_metadata["candidate_id"]
        and event.instrument_code == row.instrument_code
        and event.account_id == row.account_id
        and event.event_key == accepted[row.id]["candidate_evidence_key"]
        and stable_manifest_hash(event.payload["candidate_evidence"])
        == accepted[row.id]["candidate_evidence_hash"]
      ]
      if len(matching) != 1:
        raise TAllocationConflict("T_ALLOCATION_SOURCE_EVIDENCE_REQUIRED")
      candidates.append(candidate_from_evaluation(row, matching[0], now=now))
    candidates = tuple(candidates)
    snapshot = await PaperPortfolioSnapshotReader(self.db).read(
      execution_id=execution_id,
      cycle_id=cycle_id,
      instrument_codes=tuple(candidate.instrument_code for candidate in candidates),
      as_of=now,
    )
    repository = TAllocationRepository(self.db)
    # An expired candidate still needs a durable REJECT/EXPIRED decision. The
    # coordination lease is independent of the original candidate TTL.
    batch = await repository.prepare(
      snapshot=snapshot,
      candidates=candidates,
      now=now,
      expires_at=now + timedelta(seconds=lease_seconds),
    )
    claim = await repository.claim(
      allocation_batch_id=batch.allocation_batch_id,
      processing_owner=processing_owner,
      snapshot=snapshot,
      candidates=candidates,
      now=now,
      lease_seconds=lease_seconds,
    )
    if claim is None:
      return await repository.get(batch.allocation_batch_id)
    return await repository.commit(
      claim=claim, snapshot=snapshot, candidates=candidates, now=now
    )
