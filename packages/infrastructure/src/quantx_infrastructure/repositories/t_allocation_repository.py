"""Durable, fenced portfolio attempts over standard accepted TradeIntent rows.

All writes are flush-only. The caller must commit terminalized stale/expired
results as well as successful results; lease conflicts raise without mutation.
SQLite tests cover behavior; PostgreSQL row locks and unique indexes provide
cross-session arbitration in production.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from quantx_application.t_trade_v3.portfolio_allocation import (
  TAllocationAction,
  TAllocationCandidate,
  allocate_portfolio,
)
from quantx_application.t_trade_v3.portfolio_snapshot import PortfolioTDecisionSnapshot
from quantx_contracts import ExecutionEnvironment
from quantx_domain.trading.t_assistant_execution import stable_manifest_hash
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.t_allocation import (
  TAllocationBatchRecord,
  TAllocationDecisionRecord,
)
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantExecutionRecord,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_assistant_decision_cycle_repository import (
  TAssistantDecisionCycleRepository,
)
from quantx_infrastructure.services.t_allocation_serialization import (
  allocation_evidence,
  allocation_time,
)
from quantx_infrastructure.services.trade_intent_intake import (
  trade_intent_initial_material,
)


class TAllocationConflict(RuntimeError):
  pass


@dataclass(frozen=True)
class TAllocationClaim:
  allocation_batch_id: str
  processing_owner: str
  processing_fence_token: str
  processing_lease_until: datetime


def _candidate_manifest(candidates):
  return [
    allocation_evidence(item)
    for item in sorted(candidates, key=lambda item: item.intent_id)
  ]


def _manifest_hash(manifest):
  return stable_manifest_hash({"candidates": manifest})


class TAllocationRepository:
  def __init__(self, db: AsyncSession) -> None:
    self.db = db

  async def get(self, allocation_batch_id: str) -> TAllocationBatchRecord | None:
    return await self.db.scalar(
      select(TAllocationBatchRecord)
      .where(
        TAllocationBatchRecord.allocation_batch_id == allocation_batch_id,
      )
      .execution_options(populate_existing=True)
    )

  async def list_decisions(
    self, allocation_batch_id: str
  ) -> list[TAllocationDecisionRecord]:
    return list(
      (
        await self.db.scalars(
          select(TAllocationDecisionRecord)
          .where(
            TAllocationDecisionRecord.allocation_batch_id == allocation_batch_id,
          )
          .order_by(TAllocationDecisionRecord.rank)
        )
      ).all()
    )

  async def list_recoverable(
    self, *, execution_id: str
  ) -> list[TAllocationBatchRecord]:
    return list(
      (
        await self.db.scalars(
          select(TAllocationBatchRecord)
          .where(
            TAllocationBatchRecord.execution_id == execution_id,
            TAllocationBatchRecord.status == "PREPARED",
          )
          .order_by(
            TAllocationBatchRecord.created_at, TAllocationBatchRecord.allocation_attempt
          )
        )
      ).all()
    )

  async def prepare(
    self,
    *,
    snapshot: PortfolioTDecisionSnapshot,
    candidates: tuple[TAllocationCandidate, ...],
    now: datetime,
    expires_at: datetime,
  ) -> TAllocationBatchRecord | None:
    now, expires_at = allocation_time(now), allocation_time(expires_at)
    if expires_at <= now:
      raise ValueError("T_ALLOCATION_INVALID_TTL")
    candidates = tuple(candidates)
    cycle, execution, _ = await self._context(snapshot, candidates, now)
    previous = await self.db.scalar(
      select(TAllocationBatchRecord)
      .where(
        TAllocationBatchRecord.execution_id == execution.execution_id,
        TAllocationBatchRecord.cycle_id == cycle.cycle_id,
      )
      .order_by(TAllocationBatchRecord.allocation_attempt.desc())
      .limit(1)
      .with_for_update()
      .execution_options(populate_existing=True)
    )
    manifest = _candidate_manifest(candidates)
    if previous is not None and previous.status == "PREPARED":
      if allocation_time(previous.expires_at) <= now:
        await self._terminalize(previous, "EXPIRED", "T_ALLOCATION_TTL_EXPIRED", now)
      elif self._same_material(previous, snapshot, manifest):
        return previous
      else:
        await self._terminalize(
          previous, "SUPERSEDED", "T_ALLOCATION_INPUT_CHANGED", now
        )
    if not candidates:
      return None
    batch = TAllocationBatchRecord(
      allocation_batch_id=str(uuid.uuid4()),
      execution_id=execution.execution_id,
      cycle_id=cycle.cycle_id,
      environment="PAPER",
      allocation_attempt=previous.allocation_attempt + 1 if previous is not None else 1,
      portfolio_input_fingerprint=snapshot.portfolio_input_fingerprint,
      portfolio_snapshot=allocation_evidence(snapshot),
      intent_manifest_hash=_manifest_hash(manifest),
      intent_manifest=manifest,
      intent_count=len(candidates),
      status="PREPARED",
      created_at=now,
      expires_at=expires_at,
    )
    self.db.add(batch)
    await self.db.flush()
    return batch

  async def claim(
    self,
    *,
    allocation_batch_id: str,
    processing_owner: str,
    snapshot: PortfolioTDecisionSnapshot,
    candidates: tuple[TAllocationCandidate, ...],
    now: datetime,
    lease_seconds: int,
  ) -> TAllocationClaim | None:
    now = allocation_time(now)
    self._validate_lease(processing_owner, lease_seconds)
    batch = await self._locked_batch(allocation_batch_id)
    self._check_time(batch, now)
    if batch.status != "PREPARED":
      raise TAllocationConflict("T_ALLOCATION_NOT_PREPARED")
    if allocation_time(batch.expires_at) <= now:
      await self._terminalize(batch, "EXPIRED", "T_ALLOCATION_TTL_EXPIRED", now)
      return None
    if (
      batch.processing_lease_until is not None
      and allocation_time(batch.processing_lease_until) > now
    ):
      raise TAllocationConflict("T_ALLOCATION_LEASE_CONFLICT")
    if not await self._latest_matches(batch, snapshot, tuple(candidates), now):
      await self._terminalize(batch, "SUPERSEDED", "T_ALLOCATION_INPUT_CHANGED", now)
      return None
    batch.processing_owner = processing_owner
    batch.processing_fence_token = str(uuid.uuid4())
    batch.processing_lease_until = min(
      now
      + timedelta(
        seconds=min(
          lease_seconds, (allocation_time(batch.expires_at) - now).total_seconds()
        )
      ),
      allocation_time(batch.expires_at),
    )
    await self.db.flush()
    return self._claim_value(batch)

  async def renew(
    self,
    *,
    claim: TAllocationClaim,
    snapshot: PortfolioTDecisionSnapshot,
    candidates: tuple[TAllocationCandidate, ...],
    now: datetime,
    lease_seconds: int,
  ) -> TAllocationClaim | None:
    now = allocation_time(now)
    self._validate_lease(claim.processing_owner, lease_seconds)
    batch = await self._locked_batch(claim.allocation_batch_id)
    self._check_time(batch, now)
    self._check_claim(batch, claim, now)
    if not await self._latest_matches(batch, snapshot, tuple(candidates), now):
      await self._terminalize(batch, "SUPERSEDED", "T_ALLOCATION_INPUT_CHANGED", now)
      return None
    batch.processing_lease_until = min(
      now
      + timedelta(
        seconds=min(
          lease_seconds, (allocation_time(batch.expires_at) - now).total_seconds()
        )
      ),
      allocation_time(batch.expires_at),
    )
    await self.db.flush()
    return self._claim_value(batch)

  async def commit(
    self,
    *,
    claim: TAllocationClaim,
    snapshot: PortfolioTDecisionSnapshot,
    candidates: tuple[TAllocationCandidate, ...],
    now: datetime,
  ) -> TAllocationBatchRecord:
    now = allocation_time(now)
    candidates = tuple(candidates)
    batch = await self._locked_batch(claim.allocation_batch_id)
    self._check_time(batch, now)
    self._check_claim_identity(batch, claim)
    if allocation_time(batch.expires_at) <= now:
      await self._terminalize(batch, "EXPIRED", "T_ALLOCATION_TTL_EXPIRED", now)
      return batch
    self._check_claim(batch, claim, now)
    if not await self._latest_matches(batch, snapshot, candidates, now):
      await self._terminalize(batch, "SUPERSEDED", "T_ALLOCATION_INPUT_CHANGED", now)
      return batch
    # The allocator is rerun here; no external caller can submit its own decisions.
    async with self.db.begin_nested():
      _, execution, rows = await self._context(snapshot, candidates, now)
      decisions = allocate_portfolio(
        snapshot, candidates, allocation_attempt=batch.allocation_attempt, now=now
      )
      by_id = {candidate.intent_id: candidate for candidate in candidates}
      evidence = []
      for decision in decisions:
        candidate = by_id[decision.intent_id]
        detail = allocation_evidence(decision)
        evidence.append(detail)
        self.db.add(
          TAllocationDecisionRecord(
            decision_id=decision.decision_id,
            allocation_batch_id=batch.allocation_batch_id,
            intent_id=decision.intent_id,
            intent_version=decision.intent_version,
            candidate_id=decision.candidate_id,
            instrument_code=decision.instrument_code,
            rank=decision.rank,
            action=decision.action.value,
            requested_amount_ceiling=decision.requested_amount_ceiling,
            allocated_amount_cap=decision.allocated_amount_cap,
            evidence=detail,
            created_at=now,
            expires_at=candidate.expires_at,
            next_eligible_at=decision.next_eligible_at,
          )
        )
      # FK and deferred PostgreSQL guards require every decision before intent CAS.
      await self.db.flush()
      for decision in decisions:
        row = rows[decision.intent_id]
        if (
          row.allocation_version != decision.intent_version
          or row.status != "ALLOCATION_PENDING"
        ):
          raise TAllocationConflict("T_ALLOCATION_INTENT_VERSION_CONFLICT")
        if decision.action in {TAllocationAction.ALLOW, TAllocationAction.CAP}:
          row.status = (
            "AWAITING_APPROVAL"
            if execution.entry_authorization == "MANUAL_CONFIRM"
            else "EXECUTION_READY"
          )
        elif decision.action == TAllocationAction.DELAY:
          row.status = "ALLOCATION_PENDING"
        else:
          row.status = (
            "EXPIRED" if "T_INTENT_EXPIRED" in decision.blockers else "REJECTED"
          )
        row.allocation_version = decision.intent_version + 1
        row.allocation_decision_id = decision.decision_id
        row.allocation_next_eligible_at = decision.next_eligible_at
      batch.status = "COMMITTED"
      batch.committed_at = now
      batch.decision_manifest_hash = stable_manifest_hash({"decisions": evidence})
      self._clear_claim(batch)
      await self.db.flush()
    return batch

  async def expire(
    self, *, allocation_batch_id: str, now: datetime
  ) -> TAllocationBatchRecord:
    now = allocation_time(now)
    batch = await self._locked_batch(allocation_batch_id)
    self._check_time(batch, now)
    if batch.status == "PREPARED" and allocation_time(batch.expires_at) <= now:
      await self._terminalize(batch, "EXPIRED", "T_ALLOCATION_TTL_EXPIRED", now)
    return batch

  async def supersede(
    self, *, claim: TAllocationClaim, now: datetime
  ) -> TAllocationBatchRecord:
    now = allocation_time(now)
    batch = await self._locked_batch(claim.allocation_batch_id)
    self._check_time(batch, now)
    self._check_claim(batch, claim, now)
    await self._terminalize(batch, "SUPERSEDED", "T_ALLOCATION_INPUT_CHANGED", now)
    return batch

  async def _locked_batch(self, batch_id):
    probe = await self.get(batch_id)
    if probe is None:
      raise TAllocationConflict("T_ALLOCATION_NOT_FOUND")
    cycle = await TAssistantDecisionCycleRepository(self.db).get(
      probe.cycle_id,
      for_update=True,
    )
    if cycle is None:
      raise TAllocationConflict("T_ALLOCATION_CYCLE_NOT_COMMITTED")
    return await self.db.scalar(
      select(TAllocationBatchRecord)
      .where(
        TAllocationBatchRecord.allocation_batch_id == batch_id,
      )
      .with_for_update()
      .execution_options(populate_existing=True)
    )

  async def _context(self, snapshot, candidates, now):
    if not isinstance(snapshot, PortfolioTDecisionSnapshot) or now < snapshot.cut.as_of:
      raise TAllocationConflict("T_ALLOCATION_SNAPSHOT_INVALID")
    if (
      snapshot.environment != ExecutionEnvironment.PAPER
      or snapshot.scorer_binding != "RULE_ONLY"
    ):
      raise TAllocationConflict("T_ALLOCATION_SCOPE_INVALID")
    # Share P3's authoritative execution -> cycle order before batch/intent locks.
    cycle = await TAssistantDecisionCycleRepository(self.db).get(
      snapshot.cycle_id,
      for_update=True,
    )
    if cycle is None or cycle.status != "PROPOSALS_COMMITTED":
      raise TAllocationConflict("T_ALLOCATION_CYCLE_NOT_COMMITTED")
    execution = await self.db.scalar(
      select(TAssistantExecutionRecord)
      .where(
        TAssistantExecutionRecord.execution_id == cycle.execution_id,
      )
      .execution_options(populate_existing=True)
    )
    if (
      execution is None
      or cycle.execution_id != snapshot.cut.execution_ref.owner_id
      or execution.environment != "PAPER"
      or execution.scorer_mode != "RULE_ONLY"
      or snapshot.config_version != execution.config_version_id
      or snapshot.strategy_binding != execution.policy_version
    ):
      raise TAllocationConflict("T_ALLOCATION_EXECUTION_BINDING_CONFLICT")
    if execution.status != "RUNNING" or execution.entry_readiness != "READY":
      raise TAllocationConflict("T_ALLOCATION_EXECUTION_NOT_ENTRY_READY")
    if (
      not isinstance(cycle.output_manifest, dict)
      or stable_manifest_hash(cycle.output_manifest) != cycle.output_manifest_hash
    ):
      raise TAllocationConflict("T_ALLOCATION_CYCLE_MANIFEST_INVALID")
    items = cycle.output_manifest.get("accepted_intents")
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
      raise TAllocationConflict("T_ALLOCATION_CYCLE_MANIFEST_INVALID")
    accepted = {item.get("intent_id"): item.get("intake_hash") for item in items}
    if len(accepted) != len(items) or len(items) != cycle.proposed_intent_count:
      raise TAllocationConflict("T_ALLOCATION_CYCLE_MANIFEST_INVALID")
    rows = list(
      (
        await self.db.scalars(
          select(TradeIntentRecord)
          .where(
            TradeIntentRecord.allocation_cycle_id == cycle.cycle_id,
          )
          .order_by(TradeIntentRecord.id)
          .with_for_update()
          .execution_options(populate_existing=True)
        )
      ).all()
    )
    if set(accepted) != {row.id for row in rows}:
      raise TAllocationConflict("T_ALLOCATION_INTENT_SET_CONFLICT")
    for row in rows:
      if (
        row.owner_type != "T_ASSISTANT_EXECUTION"
        or row.owner_id != execution.execution_id
        or row.environment != "PAPER"
        or row.direction != "BUY"
        or row.account_id != execution.account_id
        or stable_manifest_hash(trade_intent_initial_material(row)) != accepted[row.id]
      ):
        raise TAllocationConflict("T_ALLOCATION_INTENT_MATERIAL_CONFLICT")
    eligible = {
      row.id: row
      for row in rows
      if row.status == "ALLOCATION_PENDING"
      and (
        row.allocation_next_eligible_at is None
        or allocation_time(row.allocation_next_eligible_at) <= now
      )
    }
    if (
      any(not isinstance(item, TAllocationCandidate) for item in candidates)
      or len({item.intent_id for item in candidates}) != len(candidates)
      or set(eligible) != {item.intent_id for item in candidates}
    ):
      raise TAllocationConflict("T_ALLOCATION_ELIGIBLE_SET_CONFLICT")
    for candidate in candidates:
      self._validate_candidate(candidate, eligible[candidate.intent_id])
    return cycle, execution, eligible

  @staticmethod
  def _validate_candidate(candidate, row):
    metadata = dict(row.intent_metadata or {})
    try:
      created = allocation_time(datetime.fromisoformat(metadata["intent_created_at"]))
      ttl = metadata["approval_ttl_ms"]
      if type(ttl) is not int or ttl <= 0:
        raise ValueError("invalid TTL")
      cutoff = created + timedelta(milliseconds=ttl)
      source = metadata["source_time_ms"]
      if type(source) is not int or source < 0:
        raise ValueError("invalid source clock")
      observed = datetime.fromtimestamp(source / 1000, tz=created.tzinfo)
      if observed > created:
        raise ValueError("candidate source must not follow intent creation")
      cutoff = min(cutoff, observed + timedelta(milliseconds=ttl))
      score_value = metadata["opportunity_score"]
      if type(score_value) not in (int, float):
        raise ValueError("invalid rule score")
      rule_score = Decimal(str(score_value))
      if not rule_score.is_finite() or not 0 <= rule_score <= 100:
        raise ValueError("invalid rule score")
      if (
        candidate.intent_version != row.allocation_version
        or candidate.candidate_id != metadata.get("candidate_id")
        or candidate.candidate_fingerprint != metadata.get("candidate_fingerprint")
        or candidate.instrument_code != row.instrument_code
        or candidate.requested_amount_ceiling != Decimal(str(row.target_amount))
        or candidate.rule_score != rule_score
        or candidate.rank_score != rule_score / 100
        or candidate.observed_at != observed
        or candidate.expires_at > cutoff
        or candidate.next_eligible_at
        != (
          allocation_time(row.allocation_next_eligible_at)
          if row.allocation_next_eligible_at
          else None
        )
      ):
        raise ValueError("candidate mismatch")
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
      raise TAllocationConflict("T_ALLOCATION_CANDIDATE_BINDING_CONFLICT") from exc

  async def _latest_matches(self, batch, snapshot, candidates, now):
    if (
      not isinstance(snapshot, PortfolioTDecisionSnapshot)
      or snapshot.cycle_id != batch.cycle_id
      or snapshot.cut.execution_ref.owner_id != batch.execution_id
    ):
      return False
    try:
      await self._context(snapshot, candidates, now)
    except TAllocationConflict:
      return False
    return self._same_material(batch, snapshot, _candidate_manifest(candidates))

  @staticmethod
  def _same_material(batch, snapshot, manifest):
    return (
      batch.portfolio_input_fingerprint == snapshot.portfolio_input_fingerprint
      and batch.portfolio_snapshot == allocation_evidence(snapshot)
      and batch.intent_manifest_hash == _manifest_hash(manifest)
      and batch.intent_manifest == manifest
    )

  @staticmethod
  def _validate_lease(owner, seconds):
    if (
      not isinstance(owner, str)
      or not owner.strip()
      or owner != owner.strip()
      or len(owner) > 128
      or type(seconds) is not int
      or seconds <= 0
    ):
      raise ValueError("T_ALLOCATION_INVALID_LEASE")

  @staticmethod
  def _check_time(batch, now):
    if now < allocation_time(batch.created_at):
      raise TAllocationConflict("T_ALLOCATION_TIME_PRECEDES_ATTEMPT")

  @staticmethod
  def _check_claim_identity(batch, claim):
    if (
      batch.status != "PREPARED"
      or batch.processing_owner != claim.processing_owner
      or batch.processing_fence_token != claim.processing_fence_token
      or batch.processing_lease_until is None
    ):
      raise TAllocationConflict("T_ALLOCATION_LEASE_CONFLICT")

  @classmethod
  def _check_claim(cls, batch, claim, now):
    cls._check_claim_identity(batch, claim)
    if (
      allocation_time(batch.processing_lease_until) <= now
      or allocation_time(batch.expires_at) <= now
    ):
      raise TAllocationConflict("T_ALLOCATION_LEASE_EXPIRED")

  @staticmethod
  def _claim_value(batch):
    return TAllocationClaim(
      batch.allocation_batch_id,
      batch.processing_owner,
      batch.processing_fence_token,
      allocation_time(batch.processing_lease_until),
    )

  @staticmethod
  def _clear_claim(batch):
    batch.processing_owner = batch.processing_fence_token = (
      batch.processing_lease_until
    ) = None

  async def _terminalize(self, batch, status, reason, now):
    batch.status, batch.terminal_reason, batch.committed_at = status, reason, now
    self._clear_claim(batch)
    await self.db.flush()
