"""Recoverable public BUY ordering in an explicit LIVE or isolated PAPER scope.

The sequencer persists ordering evidence only. It neither reserves cash nor
routes orders; the final account transaction still owns sizing, risk, capacity,
pending/correlation/outbox creation, and commit.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from quantx_contracts import ExecutionEnvironment, ExecutionOwnerType
from quantx_domain.clock import to_naive_utc, utcnow
from quantx_domain.trading.risk_increase_admission import (
  RISK_INCREASE_ADMISSION_POLICY_VERSION,
  RiskIncreaseAdmissionCandidate,
  rank_risk_increase_candidates,
  risk_increase_input_fingerprint,
  risk_increase_intent_manifest_hash,
)
from sqlalchemy import select, text
from sqlalchemy.orm.attributes import flag_modified

from quantx_infrastructure.models.agent_runtime import AccountExecutionControl
from quantx_infrastructure.models.paper_execution import PaperExecutionAccountRecord
from quantx_infrastructure.models.risk_increase_admission import (
  AccountRiskIncreaseAdmissionBatch,
  AccountRiskIncreaseAdmissionItem,
)
from quantx_infrastructure.models.t_allocation import (
  TAllocationBatchRecord,
  TAllocationDecisionRecord,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.risk_increase_admission_repository import (
  RiskIncreaseAdmissionRepository,
)

ADMISSION_LEASE_SECONDS = 10
ADMISSION_RENEW_INTERVAL_SECONDS = 3
ADMISSION_TTL_SECONDS = 30
_READY_STATUSES = ("EXECUTION_READY",)


def _intent_material_fingerprint(
  intent: TradeIntentRecord,
  *,
  environment: ExecutionEnvironment,
  paper_execution_id: str | None,
  allocation_material: dict | None,
) -> str:
  payload = {
    "intent_id": str(intent.id),
    "environment": environment.value,
    "paper_execution_id": paper_execution_id,
    "owner_type": intent.owner_type,
    "owner_id": intent.owner_id,
    "allocation_material": allocation_material,
    "account_id": str(intent.account_id or ""),
    "instrument_code": str(intent.instrument_code or "").upper(),
    "direction": str(intent.direction or "").upper(),
    "bucket": str(intent.bucket or ""),
    "priority": str(intent.priority or ""),
    "target_amount": intent.target_amount,
    "target_position_pct": intent.target_position_pct,
    "target_volume": intent.target_volume,
    "limit_price_hint": intent.limit_price_hint,
    "risk_decision_id": str(intent.risk_decision_id or ""),
    "request": dict(intent.intent_metadata or {}).get("risk_increase_order_request"),
  }
  return hashlib.sha256(
    json.dumps(
      payload,
      sort_keys=True,
      separators=(",", ":"),
      default=str,
    ).encode()
  ).hexdigest()


@dataclass(frozen=True)
class AdmissionBatchClaim:
  admission_batch_id: str
  fence_token: str
  expires_at: datetime
  lease_until: datetime


class AccountRiskIncreaseAdmissionSequencer:
  """Durable scoped ordering before a BUY takes its final account lock."""

  def __init__(
    self,
    db: Any,
    *,
    environment=ExecutionEnvironment.LIVE,
    paper_execution_id: str | None = None,
  ) -> None:
    self.db = db
    self.repository = RiskIncreaseAdmissionRepository(
      db, environment=environment, paper_execution_id=paper_execution_id
    )
    self.environment = self.repository.environment
    self.paper_execution_id = self.repository.paper_execution_id

  async def _serialize_account(self, account_id: str) -> None:
    """Serialize batch construction without taking the execution-control row.

    The advisory transaction lock is deliberately admission-specific.  The
    execution-control ``FOR UPDATE`` remains the later, final authorization
    boundary after the complete READY set has a committed rank and fence.
    """

    bind = self.db.get_bind()
    if str(getattr(getattr(bind, "dialect", None), "name", "")) == "postgresql":
      await self.db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"),
        {
          "scope": f"quantx:risk-increase-admission:{self.environment.value}:{self.paper_execution_id or account_id}"
        },
      )

  async def _validate_account_snapshot(
    self, account_id, snapshot_id, snapshot_hash, now
  ):
    if self.environment is ExecutionEnvironment.PAPER:
      account = await self.db.scalar(
        select(PaperExecutionAccountRecord)
        .where(
          PaperExecutionAccountRecord.execution_id == self.paper_execution_id,
        )
        .execution_options(populate_existing=True)
      )
      if (
        account is None
        or account.account_id != account_id
        or account.environment != "PAPER"
      ):
        raise ValueError("RISK_ADMISSION_PAPER_ACCOUNT_SCOPE_CONFLICT")
      expected_id = f"paper:{account.execution_id}:{account.revision}"
      expected_hash = account.snapshot_hash
      if to_naive_utc(account.snapshot_as_of) > now:
        raise ValueError("RISK_ADMISSION_ACCOUNT_SNAPSHOT_FUTURE")
    else:
      control = await self.db.get(
        AccountExecutionControl, account_id, populate_existing=True
      )
      if control is None:
        raise ValueError("RISK_ADMISSION_ACCOUNT_CONTROL_MISSING")
      expected_id, expected_hash = control.last_snapshot_id, control.last_snapshot_hash
    if (
      str(expected_id or "") != str(snapshot_id or "")
      or str(expected_hash or "").lower() != str(snapshot_hash or "").lower()
    ):
      raise ValueError("RISK_ADMISSION_ACCOUNT_SNAPSHOT_CHANGED")

  async def _candidate(
    self, intent: TradeIntentRecord
  ) -> RiskIncreaseAdmissionCandidate:
    owner_type = ExecutionOwnerType(str(intent.owner_type or "").upper())
    if intent.environment != self.environment.value or (
      self.environment is ExecutionEnvironment.PAPER
      and (
        owner_type is not ExecutionOwnerType.T_ASSISTANT_EXECUTION
        or intent.owner_id != self.paper_execution_id
      )
    ):
      raise ValueError("RISK_ADMISSION_INTENT_SCOPE_CONFLICT")
    allocation_material, allocation_fields = None, {}
    if owner_type is ExecutionOwnerType.T_ASSISTANT_EXECUTION:
      decision = (
        await self.db.get(
          TAllocationDecisionRecord,
          intent.allocation_decision_id,
          populate_existing=True,
        )
        if intent.allocation_decision_id
        else None
      )
      allocation = (
        await self.db.get(
          TAllocationBatchRecord, decision.allocation_batch_id, populate_existing=True
        )
        if decision is not None
        else None
      )
      if (
        decision is None
        or allocation is None
        or allocation.status != "COMMITTED"
        or decision.action not in {"ALLOW", "CAP"}
        or allocation.environment != self.environment.value
        or allocation.execution_id != intent.owner_id
        or allocation.cycle_id != intent.allocation_cycle_id
        or decision.intent_id != intent.id
        or decision.instrument_code != intent.instrument_code
        or decision.intent_version + 1 != intent.allocation_version
        or decision.candidate_id
        != dict(intent.intent_metadata or {}).get("candidate_id")
        or type(decision.rank) is not int
        or decision.rank < 1
      ):
        raise ValueError("RISK_ADMISSION_T_ALLOCATION_REQUIRED")
      allocation_fields = dict(
        allocation_batch_id=allocation.allocation_batch_id,
        allocation_rank=decision.rank,
        allocation_created_at=to_naive_utc(allocation.created_at),
      )
      allocation_material = {
        "decision_id": decision.decision_id,
        "intent_version": decision.intent_version,
        "allocation_batch_id": allocation.allocation_batch_id,
        "rank": decision.rank,
        "allocated_amount_cap": str(decision.allocated_amount_cap),
        "portfolio_input_fingerprint": allocation.portfolio_input_fingerprint,
      }
    return RiskIncreaseAdmissionCandidate(
      intent_id=str(intent.id),
      owner_type=owner_type,
      owner_id=str(intent.owner_id),
      created_at=to_naive_utc(intent.created_at),
      material_fingerprint=_intent_material_fingerprint(
        intent,
        environment=self.environment,
        paper_execution_id=self.paper_execution_id,
        allocation_material=allocation_material,
      ),
      **allocation_fields,
    )

  async def prepare_batch(
    self,
    *,
    account_id: str,
    account_snapshot_id: str,
    account_snapshot_hash: str,
    obligation_watermark: str,
    intent_ids: Iterable[str] | None = None,
    policy_version: str = RISK_INCREASE_ADMISSION_POLICY_VERSION,
    now: datetime | None = None,
    commit: bool = True,
  ) -> AccountRiskIncreaseAdmissionBatch:
    current_time = to_naive_utc(now or utcnow())
    normalized_account = str(account_id or "").strip()
    if not normalized_account:
      raise ValueError("RISK_ADMISSION_ACCOUNT_REQUIRED")
    await self._serialize_account(normalized_account)
    await self._validate_account_snapshot(
      normalized_account, account_snapshot_id, account_snapshot_hash, current_time
    )

    ids = tuple(dict.fromkeys(str(value or "").strip() for value in intent_ids or ()))
    if any(not value for value in ids):
      raise ValueError("RISK_ADMISSION_INTENT_ID_REQUIRED")
    stmt = (
      select(TradeIntentRecord)
      .where(
        TradeIntentRecord.account_id == normalized_account,
        TradeIntentRecord.environment == self.environment.value,
        TradeIntentRecord.direction == "BUY",
        TradeIntentRecord.status.in_(_READY_STATUSES),
      )
      .order_by(
        TradeIntentRecord.created_at,
        TradeIntentRecord.owner_type,
        TradeIntentRecord.owner_id,
        TradeIntentRecord.id,
      )
      .with_for_update()
      .execution_options(populate_existing=True)
    )
    if self.environment is ExecutionEnvironment.PAPER:
      stmt = stmt.where(
        TradeIntentRecord.owner_type == ExecutionOwnerType.T_ASSISTANT_EXECUTION.value,
        TradeIntentRecord.owner_id == self.paper_execution_id,
      )
    intents = list((await self.db.scalars(stmt)).all())
    if ids and {str(item.id) for item in intents} != set(ids):
      raise ValueError("RISK_ADMISSION_READY_SET_CHANGED")
    if not intents:
      raise ValueError("RISK_ADMISSION_EMPTY_BATCH")
    candidates = tuple([await self._candidate(item) for item in intents])
    ranked = rank_risk_increase_candidates(candidates)
    manifest_hash = risk_increase_intent_manifest_hash(ranked)
    input_fingerprint = risk_increase_input_fingerprint(
      account_id=normalized_account,
      environment=self.environment,
      paper_execution_id=self.paper_execution_id,
      account_snapshot_id=account_snapshot_id,
      account_snapshot_hash=account_snapshot_hash,
      obligation_watermark=obligation_watermark,
      intent_manifest_hash=manifest_hash,
      policy_version=policy_version,
    )
    if any(item.admission_batch_id for item in intents):
      existing_batch_ids = {
        str(item.admission_batch_id) for item in intents if item.admission_batch_id
      }
      if len(existing_batch_ids) != 1:
        raise ValueError("RISK_ADMISSION_INTENT_ALREADY_ASSIGNED")
      existing = await self.repository.find_batch(
        next(iter(existing_batch_ids)),
        for_update=True,
      )
      if existing is None:
        raise ValueError("RISK_ADMISSION_DURABLE_BATCH_MISSING")
      existing_status = str(existing.status or "").upper()
      if (
        all(
          str(item.admission_batch_id or "") == str(existing.admission_batch_id)
          for item in intents
        )
        and existing_status == "PREPARED"
        and str(existing.input_fingerprint) == input_fingerprint
        and to_naive_utc(existing.expires_at) > current_time
      ):
        return existing
      if existing_status == "COMMITTED":
        raise ValueError("RISK_ADMISSION_BATCH_ALREADY_COMMITTED")
      if existing_status == "PREPARED":
        existing.status = (
          "EXPIRED"
          if to_naive_utc(existing.expires_at) <= current_time
          else "SUPERSEDED"
        )
        existing.terminal_reason = (
          "RISK_ADMISSION_TTL_EXPIRED"
          if existing.status == "EXPIRED"
          else "RISK_ADMISSION_INPUT_CHANGED"
        )
    existing = await self.repository.latest_for_input(
      account_id=normalized_account,
      environment=self.environment.value,
      input_fingerprint=input_fingerprint,
      for_update=True,
    )
    if existing is not None:
      existing_status = str(existing.status or "").upper()
      if (
        existing_status == "PREPARED"
        and to_naive_utc(existing.expires_at) > current_time
      ):
        return existing
      if existing_status == "COMMITTED":
        raise ValueError("RISK_ADMISSION_BATCH_ALREADY_COMMITTED")
    attempt = await self.repository.next_attempt(
      account_id=normalized_account,
      environment=self.environment.value,
    )
    batch_id = str(uuid.uuid4())
    batch = AccountRiskIncreaseAdmissionBatch(
      admission_batch_id=batch_id,
      account_id=normalized_account,
      environment=self.environment.value,
      paper_execution_id=self.paper_execution_id,
      attempt=attempt,
      policy_version=policy_version,
      account_snapshot_id=str(account_snapshot_id),
      account_snapshot_hash=str(account_snapshot_hash).lower(),
      obligation_watermark=str(obligation_watermark).lower(),
      input_fingerprint=input_fingerprint,
      intent_manifest_hash=manifest_hash,
      status="PREPARED",
      expires_at=current_time + timedelta(seconds=ADMISSION_TTL_SECONDS),
    )
    by_id = {str(item.id): item for item in intents}
    self.db.add(batch)
    admission_items = []
    for ranked_item in ranked:
      candidate = ranked_item.candidate
      intent = by_id[candidate.intent_id]
      intent.admission_batch_id = batch_id
      intent.admission_rank = ranked_item.admission_rank
      intent.admission_policy_version = policy_version
      intent.admission_input_fingerprint = input_fingerprint
      # Ranking credentials have their own batch timestamp; preserve the intent's
      # economic fact time instead of letting ORM onupdate invalidate this cut.
      flag_modified(intent, "updated_at")
      admission_items.append(
        AccountRiskIncreaseAdmissionItem(
          admission_item_id=str(uuid.uuid4()),
          admission_batch_id=batch_id,
          intent_id=candidate.intent_id,
          admission_rank=ranked_item.admission_rank,
          owner_type=candidate.owner_type.value,
          owner_id=candidate.owner_id,
          intent_created_at=candidate.created_at,
        )
      )
    # The immediate item guard reads persisted intent bindings, not ORM state.
    # Flush the new batch and the complete intent projection before inserting
    # immutable items; the deferred guard checks completeness at outer commit.
    await self.db.flush()
    self.db.add_all(admission_items)
    await self.db.flush()
    if commit:
      await self.db.commit()
      await self.db.refresh(batch)
    return batch

  async def claim_batch(
    self,
    *,
    admission_batch_id: str,
    processing_owner: str,
    now: datetime | None = None,
    commit: bool = True,
  ) -> AdmissionBatchClaim:
    current_time = to_naive_utc(now or utcnow())
    owner = str(processing_owner or "").strip()
    if not owner:
      raise ValueError("RISK_ADMISSION_PROCESSING_OWNER_REQUIRED")
    batch = await self.repository.find_batch(
      admission_batch_id,
      for_update=True,
    )
    if batch is None:
      raise ValueError("RISK_ADMISSION_BATCH_NOT_FOUND")
    await self._validate_account_snapshot(
      batch.account_id,
      batch.account_snapshot_id,
      batch.account_snapshot_hash,
      current_time,
    )
    if str(batch.status) != "PREPARED":
      raise ValueError("RISK_ADMISSION_BATCH_NOT_PREPARED")
    if to_naive_utc(batch.expires_at) <= current_time:
      batch.status = "EXPIRED"
      batch.terminal_reason = "RISK_ADMISSION_TTL_EXPIRED"
      if commit:
        await self.db.commit()
      raise ValueError("RISK_ADMISSION_TTL_EXPIRED")
    active_lease = (
      batch.processing_fence_token
      and batch.processing_lease_until is not None
      and to_naive_utc(batch.processing_lease_until) > current_time
    )
    if active_lease:
      raise ValueError("RISK_ADMISSION_LEASE_HELD")
    token = str(uuid.uuid4())
    lease_until = current_time + timedelta(seconds=ADMISSION_LEASE_SECONDS)
    batch.processing_owner = owner
    batch.processing_fence_token = token
    batch.processing_lease_until = lease_until
    await self.db.flush()
    if commit:
      await self.db.commit()
    return AdmissionBatchClaim(
      admission_batch_id=str(batch.admission_batch_id),
      fence_token=token,
      expires_at=to_naive_utc(batch.expires_at),
      lease_until=lease_until,
    )

  async def renew_claim(
    self,
    *,
    admission_batch_id: str,
    fence_token: str,
    now: datetime | None = None,
    commit: bool = True,
  ) -> datetime:
    current_time = to_naive_utc(now or utcnow())
    batch = await self._locked_claimed_batch(
      admission_batch_id=admission_batch_id,
      fence_token=fence_token,
      now=current_time,
    )
    lease_until = current_time + timedelta(seconds=ADMISSION_LEASE_SECONDS)
    batch.processing_lease_until = lease_until
    if commit:
      await self.db.commit()
    return lease_until

  async def commit_batch(
    self,
    *,
    admission_batch_id: str,
    fence_token: str,
    account_snapshot_id: str,
    account_snapshot_hash: str,
    obligation_watermark: str,
    now: datetime | None = None,
    commit: bool = True,
  ) -> AccountRiskIncreaseAdmissionBatch:
    current_time = to_naive_utc(now or utcnow())
    batch = await self._locked_claimed_batch(
      admission_batch_id=admission_batch_id,
      fence_token=fence_token,
      now=current_time,
    )
    if (
      str(batch.account_snapshot_id) != str(account_snapshot_id)
      or str(batch.account_snapshot_hash).lower() != str(account_snapshot_hash).lower()
      or str(batch.obligation_watermark).lower() != str(obligation_watermark).lower()
    ):
      batch.status = "SUPERSEDED"
      batch.terminal_reason = "RISK_ADMISSION_INPUT_CHANGED"
      if commit:
        await self.db.commit()
      raise ValueError("RISK_ADMISSION_INPUT_CHANGED")
    items = await self.repository.items(
      admission_batch_id,
      for_update=True,
    )
    intents = [
      await self.db.get(
        TradeIntentRecord,
        item.intent_id,
        with_for_update=True,
        populate_existing=True,
      )
      for item in items
    ]
    material_manifest_changed = True
    if all(intent is not None for intent in intents):
      try:
        current_ranked = rank_risk_increase_candidates(
          [await self._candidate(intent) for intent in intents if intent is not None]
        )
      except (TypeError, ValueError):
        current_ranked = ()
      material_manifest_changed = (
        risk_increase_intent_manifest_hash(current_ranked)
        != str(batch.intent_manifest_hash or "").lower()
      )
    if (
      any(
        intent is None
        or str(intent.status or "") not in _READY_STATUSES
        or str(intent.direction or "").upper() != "BUY"
        or str(intent.environment or "").upper() != self.environment.value
        or str(intent.account_id or "") != str(batch.account_id)
        or (
          self.environment is ExecutionEnvironment.PAPER
          and (
            intent.owner_type != ExecutionOwnerType.T_ASSISTANT_EXECUTION.value
            or intent.owner_id != self.paper_execution_id
          )
        )
        or str(intent.admission_batch_id or "") != admission_batch_id
        for intent in intents
      )
      or material_manifest_changed
    ):
      batch.status = "SUPERSEDED"
      batch.terminal_reason = "RISK_ADMISSION_INTENT_CHANGED"
      if commit:
        await self.db.commit()
      raise ValueError("RISK_ADMISSION_INTENT_CHANGED")
    batch.status = "COMMITTED"
    batch.committed_at = current_time
    batch.processing_lease_until = None
    batch.processing_owner = None
    if commit:
      await self.db.commit()
      await self.db.refresh(batch)
    return batch

  async def _locked_claimed_batch(
    self,
    *,
    admission_batch_id: str,
    fence_token: str,
    now: datetime,
  ) -> AccountRiskIncreaseAdmissionBatch:
    batch = await self.repository.find_batch(
      admission_batch_id,
      for_update=True,
    )
    if batch is None:
      raise ValueError("RISK_ADMISSION_BATCH_NOT_FOUND")
    await self._validate_account_snapshot(
      batch.account_id, batch.account_snapshot_id, batch.account_snapshot_hash, now
    )
    if str(batch.status or "") != "PREPARED":
      raise ValueError("RISK_ADMISSION_BATCH_NOT_PREPARED")
    if str(batch.processing_fence_token or "") != str(fence_token or ""):
      raise ValueError("RISK_ADMISSION_FENCE_CONFLICT")
    if (
      batch.processing_lease_until is None
      or to_naive_utc(batch.processing_lease_until) <= now
    ):
      raise ValueError("RISK_ADMISSION_LEASE_EXPIRED")
    if to_naive_utc(batch.expires_at) <= now:
      batch.status = "EXPIRED"
      batch.terminal_reason = "RISK_ADMISSION_TTL_EXPIRED"
      raise ValueError("RISK_ADMISSION_TTL_EXPIRED")
    return batch


__all__ = [
  "ADMISSION_LEASE_SECONDS",
  "ADMISSION_RENEW_INTERVAL_SECONDS",
  "ADMISSION_TTL_SECONDS",
  "AccountRiskIncreaseAdmissionSequencer",
  "AdmissionBatchClaim",
]
