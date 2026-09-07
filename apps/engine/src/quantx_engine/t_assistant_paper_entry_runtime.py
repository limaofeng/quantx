"""Engine PAPER entry dispatch over the public allocation/admission/review path."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Awaitable, Callable

from quantx_application.t_trade_v3.entry_execution_gate import (
  EntryExecutionBinding,
  EntryExecutionGateInput,
  EntryExecutionGatePolicy,
  MarketDataCapabilityManifest,
)
from quantx_contracts import ExecutionEnvironment
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_domain.trading.t_assistant_execution import (
  TAssistantExecutionEvent,
  stable_manifest_hash,
)
from quantx_domain.trading.t_assistant_market_state import (
  AcceptedTMarketTick,
  TAssistantSymbolState,
  decode_candidate_evidence,
)
from quantx_infrastructure.models.paper_execution import (
  PaperExecutionAccountRecord,
  PaperExecutionOrderRecord,
)
from quantx_infrastructure.models.risk_increase_admission import (
  AccountRiskIncreaseAdmissionBatch,
)
from quantx_infrastructure.models.t_allocation import TAllocationDecisionRecord
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantConfigVersionRecord,
  TAssistantDecisionCycleRecord,
  TAssistantExecutionRecord,
  TAssistantSymbolStateRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
  TTradeOpportunityEvaluation,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.repositories.t_allocation_repository import (
  TAllocationRepository,
)
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from quantx_infrastructure.services.account_risk_increase_admission import (
  AccountRiskIncreaseAdmissionSequencer,
)
from quantx_infrastructure.services.paper_allocation_coordinator import (
  PaperAllocationCoordinator,
)
from quantx_infrastructure.services.paper_entry_execution_review import (
  PaperEntryExecutionReview,
  PaperEntryReviewResult,
)
from quantx_infrastructure.services.paper_portfolio_snapshot import (
  PaperPortfolioSnapshotReader,
)
from sqlalchemy import exists, select


@dataclass(frozen=True)
class PaperEntryMarketWitness:
  """One atomic, read-only capture from the accepted ring and original full Tick."""

  latest_tick: AcceptedTMarketTick
  ring_generation: int
  last_accepted_sequence: int
  market_data: MarketDataSnapshot

  def __post_init__(self):
    if (
      not isinstance(self.latest_tick, AcceptedTMarketTick)
      or not isinstance(self.market_data, MarketDataSnapshot)
      or type(self.ring_generation) is not int
      or self.ring_generation < 1
      or type(self.last_accepted_sequence) is not int
      or self.last_accepted_sequence < 1
      or self.latest_tick.accepted_sequence != self.last_accepted_sequence
      or self.latest_tick.instrument_code != self.market_data.instrument_code
    ):
      raise ValueError("PAPER_ENTRY_MARKET_WITNESS_INVALID")


@dataclass(frozen=True)
class PaperEntryDispatchResult:
  status: str
  reason_codes: tuple[str, ...] = ()
  allocation_ids: tuple[str, ...] = ()
  order_ids: tuple[str, ...] = ()
  reviews: tuple[tuple[str, PaperEntryReviewResult], ...] = ()


class TAssistantPaperEntryRuntime:
  """One dispatch transaction owns head -> execution -> PAPER account locks.

  Providers must only capture memory; waiting for another DB/ledger transaction
  from the provider would invert this lock order. Seed/matching are external.
  """

  def __init__(self, *, session_factory, clock: Callable[[], datetime]):
    self._sessions, self._clock = session_factory, clock

  def _now(self):
    value = self._clock()
    if (
      not isinstance(value, datetime)
      or value.tzinfo is None
      or value.utcoffset() is None
    ):
      raise ValueError("PAPER_ENTRY_AWARE_CLOCK_REQUIRED")
    return value.astimezone(UTC)

  async def dispatch(self, *, execution_id: str, market_witness_provider):
    # Original TTL is an independent durable fact. A later stale market or sink
    # failure must not revive it; the execution frame itself remains atomic.
    expired = await self._expire_due(execution_id)
    result = await self._dispatch_frame(
      execution_id=execution_id, market_witness_provider=market_witness_provider
    )
    return replace(
      result,
      reviews=tuple(expired) + result.reviews,
      status="PROCESSED" if expired and result.status == "IDLE" else result.status,
    )

  async def _expire_due(self, execution_id):
    async with self._sessions() as db, db.begin():
      probe = await db.get(TAssistantExecutionRecord, execution_id)
      if probe is None or probe.environment != "PAPER":
        return ()
      head = await db.get(
        TTradeGlobalConfig,
        probe.config_id,
        with_for_update=True,
        populate_existing=True,
      )
      execution = await db.get(
        TAssistantExecutionRecord,
        execution_id,
        with_for_update=True,
        populate_existing=True,
      )
      if head is None or head.account_id != execution.account_id:
        return ()
      now = self._now()
      expired_pending = await TAllocationRepository(db).expire_pending_intents(
        execution_id=execution_id, now=now
      )
      reviews = [
        (intent_id, PaperEntryReviewResult("REJECT", ("PAPER_INTENT_EXPIRED",)))
        for intent_id in expired_pending
      ]
      # Already allocated grants also expire before any fresh portfolio read.
      for intent in await self._ready(db, execution_id):
        expiry = await self._expiry(db, intent, now)
        if expiry:
          if (
            await db.scalar(
              select(PaperExecutionOrderRecord.order_id)
              .where(PaperExecutionOrderRecord.intent_id == intent.id)
              .limit(1)
            )
            is not None
          ):
            raise ValueError("PAPER_ENTRY_EXPIRY_ORDER_EXISTS")
          result = PaperEntryReviewResult("REJECT", (expiry,))
          await self._record_result(db, execution_id, intent, result, now)
          reviews.append((intent.id, result))
      return tuple(reviews)

  async def _dispatch_frame(
    self,
    *,
    execution_id: str,
    market_witness_provider: Callable[[str], Awaitable[PaperEntryMarketWitness | None]],
  ) -> PaperEntryDispatchResult:
    async with self._sessions() as db, db.begin():
      probe = await db.get(TAssistantExecutionRecord, execution_id)
      if probe is None or probe.environment != "PAPER":
        return PaperEntryDispatchResult("BLOCKED", ("PAPER_ENTRY_SCOPE_INVALID",))
      head = await db.get(
        TTradeGlobalConfig,
        probe.config_id,
        with_for_update=True,
        populate_existing=True,
      )
      if head is None:
        return PaperEntryDispatchResult("BLOCKED", ("PAPER_ENTRY_CONFIG_REQUIRED",))
      execution = await db.get(
        TAssistantExecutionRecord,
        execution_id,
        with_for_update=True,
        populate_existing=True,
      )
      account = await db.get(
        PaperExecutionAccountRecord,
        execution_id,
        with_for_update=True,
        populate_existing=True,
      )
      if account is not None and (
        account.environment != "PAPER" or account.account_id != execution.account_id
      ):
        return PaperEntryDispatchResult(
          "BLOCKED", ("PAPER_ENTRY_ACCOUNT_SCOPE_INVALID",)
        )
      reviews = []
      if account is None:
        return PaperEntryDispatchResult(
          "SEED_REQUIRED", ("PAPER_SEED_REQUIRED",), reviews=tuple(reviews)
        )
      cycles = list(
        (
          await db.scalars(
            select(TAssistantDecisionCycleRecord)
            .where(
              TAssistantDecisionCycleRecord.execution_id == execution_id,
              TAssistantDecisionCycleRecord.status == "PROPOSALS_COMMITTED",
              exists(
                select(TradeIntentRecord.id).where(
                  TradeIntentRecord.allocation_cycle_id
                  == TAssistantDecisionCycleRecord.cycle_id,
                  TradeIntentRecord.status == "ALLOCATION_PENDING",
                )
              ),
            )
            .order_by(TAssistantDecisionCycleRecord.cycle_sequence)
          )
        ).all()
      )
      allocations = []
      for cycle in cycles:
        batch = await PaperAllocationCoordinator(db).allocate_cycle(
          execution_id=execution_id,
          cycle_id=cycle.cycle_id,
          processing_owner=f"paper-entry:{execution_id}",
          now=self._now(),
        )
        if batch is not None:
          allocations.append(batch.allocation_batch_id)
      # Include the availability of our just-flushed allocation obligations in
      # the next evaluation deadline; retain the reader's stable evidence cut.
      now = self._now()
      orders = []
      ready = await self._ready(db, execution_id)
      for intent in ready:
        expiry = await self._expiry(db, intent, now)
        if expiry:
          result = PaperEntryReviewResult("REJECT", (expiry,))
          await self._record_result(db, execution_id, intent, result, now)
          reviews.append((intent.id, result))
      ready = [intent for intent in ready if intent.status == "EXECUTION_READY"]
      if not ready:
        return PaperEntryDispatchResult(
          "PROCESSED" if reviews else "IDLE",
          allocation_ids=tuple(allocations),
          reviews=tuple(reviews),
        )
      sequencer = AccountRiskIncreaseAdmissionSequencer(
        db, environment=ExecutionEnvironment.PAPER, paper_execution_id=execution_id
      )
      prepared = {}
      committed = {}
      unadmitted = []
      for intent in ready:
        existing = (
          await db.get(
            AccountRiskIncreaseAdmissionBatch,
            intent.admission_batch_id,
            populate_existing=True,
          )
          if intent.admission_batch_id
          else None
        )
        if intent.admission_batch_id and existing is None:
          raise ValueError("PAPER_ENTRY_ADMISSION_REQUIRED")
        if existing is not None:
          if (
            existing.environment != "PAPER"
            or existing.paper_execution_id != execution_id
            or existing.account_id != execution.account_id
          ):
            raise ValueError("PAPER_ENTRY_ADMISSION_SCOPE_INVALID")
          if existing.status == "COMMITTED":
            committed[existing.admission_batch_id] = existing
          elif (
            existing.status == "PREPARED"
            and (
              existing.expires_at.replace(tzinfo=UTC)
              if existing.expires_at.tzinfo is None
              else existing.expires_at
            )
            > now
          ):
            prepared[existing.admission_batch_id] = existing
          else:
            unadmitted.append(intent.id)
        else:
          unadmitted.append(intent.id)
      claims = {}
      for batch_id, batch in sorted(prepared.items(), key=lambda item: item[1].attempt):
        items = await sequencer.repository.items(batch_id, for_update=True)
        ready_ids = {row.id for row in ready if row.admission_batch_id == batch_id}
        if not items or {item.intent_id for item in items} != ready_ids:
          reason = "PAPER_ENTRY_ADMISSION_COMPLETE_BATCH_REQUIRED"
          await TAssistantExecutionRepository(db).append_event(
            TAssistantExecutionEvent(
              execution_id,
              f"paper-entry-incomplete-admission:{batch_id}",
              "PAPER_ENTRY_DISPATCH_BLOCKED",
              now,
              {"admission_batch_id": batch_id, "reason_codes": [reason]},
            )
          )
          return PaperEntryDispatchResult(
            "BLOCKED", (reason,), tuple(allocations), reviews=tuple(reviews)
          )
        # Claim the original grant before reading any fresh allocation evidence.
        # Active ownership must never be bypassed by preparing another attempt.
        claims[batch_id] = await sequencer.claim_batch(
          admission_batch_id=batch_id,
          processing_owner=f"paper-entry:{execution_id}",
          now=now,
          commit=False,
        )
      # This snapshot precedes our own admission bookkeeping. Account and all
      # scope writers are fenced by the locks above throughout prepare/commit.
      snapshot = await PaperPortfolioSnapshotReader(db).read(
        execution_id=execution_id,
        cycle_id=ready[0].allocation_cycle_id,
        instrument_codes=tuple(sorted({item.instrument_code for item in ready})),
        as_of=now,
      )
      sequencer = AccountRiskIncreaseAdmissionSequencer(
        db, environment=ExecutionEnvironment.PAPER, paper_execution_id=execution_id
      )
      material = dict(
        account_snapshot_id=snapshot.cut.account_snapshot_id,
        account_snapshot_hash=snapshot.cut.account_snapshot_hash,
        obligation_watermark=snapshot.cut.local_obligation_watermark,
        now=now,
        commit=False,
      )
      for batch_id, claim in claims.items():
        committed[batch_id] = await sequencer.commit_batch(
          admission_batch_id=batch_id, fence_token=claim.fence_token, **material
        )
      # A committed grant survives WAIT_PREDECESSOR/restart. The public
      # sequencer intentionally rejects preparing that same grant a second time.
      if unadmitted:
        admission = await sequencer.prepare_batch(
          account_id=execution.account_id, intent_ids=unadmitted, **material
        )
        if admission.status == "PREPARED":
          claim = await sequencer.claim_batch(
            admission_batch_id=admission.admission_batch_id,
            processing_owner=f"paper-entry:{execution_id}",
            now=now,
            commit=False,
          )
          admission = await sequencer.commit_batch(
            admission_batch_id=admission.admission_batch_id,
            fence_token=claim.fence_token,
            **material,
          )
        if admission.status != "COMMITTED":
          return PaperEntryDispatchResult(
            "BLOCKED",
            ("PAPER_ENTRY_ADMISSION_NOT_COMMITTED",),
            tuple(allocations),
            reviews=tuple(reviews),
          )
        committed[admission.admission_batch_id] = admission
      ranked = await self._ready(db, execution_id)
      if any(
        type(row.admission_rank) is not int or row.admission_batch_id not in committed
        for row in ranked
      ):
        raise ValueError("PAPER_ENTRY_ADMISSION_RANK_INVALID")
      ranked.sort(
        key=lambda row: (committed[row.admission_batch_id].attempt, row.admission_rank)
      )
      for intent in ranked:
        witness = await market_witness_provider(intent.instrument_code)
        reviewed_at = self._now()
        expiry = await self._expiry(db, intent, reviewed_at)
        if expiry:
          result = PaperEntryReviewResult("REJECT", (expiry,))
        elif witness is None:
          result = PaperEntryReviewResult(
            "DELAY", ("PAPER_ENTRY_MARKET_WITNESS_REQUIRED",)
          )
        elif not isinstance(witness, PaperEntryMarketWitness):
          result = PaperEntryReviewResult(
            "REJECT", ("PAPER_ENTRY_MARKET_WITNESS_INVALID",)
          )
        else:
          gate = await self._gate(db, execution, intent, witness, reviewed_at)
          result = await PaperEntryExecutionReview(db).review(
            execution_id=execution_id,
            intent_id=intent.id,
            gate_input=gate,
            market_data=witness.market_data,
            now=reviewed_at,
          )
        # No catches around the awaited real sink: late failures roll back the
        # allocation, admission, accepted orders and audits as one dispatch frame.
        await self._record_result(db, execution_id, intent, result, reviewed_at)
        reviews.append((intent.id, result))
        if result.order_id:
          orders.append(result.order_id)
      return PaperEntryDispatchResult(
        "PROCESSED",
        allocation_ids=tuple(allocations),
        order_ids=tuple(orders),
        reviews=tuple(reviews),
      )

  @staticmethod
  async def _expiry(db, intent, now):
    # Expiry is terminal even if the latest market is missing/stale. Never
    # prepare a fresh admission to revive an already expired execution grant.
    metadata = intent.intent_metadata
    try:
      created = datetime.fromisoformat(metadata["intent_created_at"])
      source_ms, ttl_ms = metadata["source_time_ms"], metadata["approval_ttl_ms"]
      if (
        created.tzinfo is None
        or type(source_ms) is not int
        or source_ms < 0
        or type(ttl_ms) is not int
        or ttl_ms <= 0
      ):
        raise ValueError("invalid clock")
      source = datetime.fromtimestamp(source_ms / 1000, UTC)
      deadline = min(created, source) + timedelta(milliseconds=ttl_ms)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
      raise ValueError("PAPER_INTENT_TTL_INVALID") from exc
    if now >= deadline:
      return "PAPER_INTENT_EXPIRED"
    for model, identity, reason in (
      (
        TAllocationDecisionRecord,
        intent.allocation_decision_id,
        "PAPER_ALLOCATION_EXPIRED",
      ),
      (
        AccountRiskIncreaseAdmissionBatch,
        intent.admission_batch_id,
        "PAPER_ADMISSION_EXPIRED",
      ),
    ):
      if identity:
        row = await db.get(model, identity, populate_existing=True)
        if row is None:
          raise ValueError("PAPER_ENTRY_GRANT_REQUIRED")
        if model is AccountRiskIncreaseAdmissionBatch and row.status != "COMMITTED":
          # PREPARED TTL is coordinator recovery state, not an execution grant.
          continue
        expiry = row.expires_at
        if expiry.tzinfo is None:
          expiry = expiry.replace(tzinfo=UTC)
        if now >= expiry:
          return reason
    return None

  @staticmethod
  async def _ready(db, execution_id):
    return list(
      (
        await db.scalars(
          select(TradeIntentRecord)
          .where(
            TradeIntentRecord.owner_type == "T_ASSISTANT_EXECUTION",
            TradeIntentRecord.owner_id == execution_id,
            TradeIntentRecord.environment == "PAPER",
            TradeIntentRecord.direction == "BUY",
            TradeIntentRecord.status == "EXECUTION_READY",
          )
          .order_by(TradeIntentRecord.id)
          .with_for_update()
          .execution_options(populate_existing=True)
        )
      ).all()
    )

  @staticmethod
  async def _gate(db, execution, intent, witness, now):
    cycle = await db.get(
      TAssistantDecisionCycleRecord, intent.allocation_cycle_id, populate_existing=True
    )
    if (
      cycle is None
      or stable_manifest_hash(cycle.output_manifest) != cycle.output_manifest_hash
    ):
      raise ValueError("PAPER_ENTRY_CYCLE_EVIDENCE_INVALID")
    references = [
      item
      for item in cycle.output_manifest["accepted_intents"]
      if item["intent_id"] == intent.id
    ]
    if len(references) != 1:
      raise ValueError("PAPER_ENTRY_CANDIDATE_REFERENCE_REQUIRED")
    reference = references[0]
    event = await db.scalar(
      select(TTradeOpportunityEvaluation).where(
        TTradeOpportunityEvaluation.event_key == reference["candidate_evidence_key"]
      )
    )
    if event is None:
      raise ValueError("PAPER_ENTRY_CANDIDATE_EVIDENCE_REQUIRED")
    candidate, _, cursor = decode_candidate_evidence(
      event.payload["candidate_evidence"]
    )
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
      raise ValueError("PAPER_ENTRY_SYMBOL_STATE_REQUIRED")
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
      ExecutionEnvironment.PAPER,
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

  @staticmethod
  async def _record_result(db, execution_id, intent, result, now):
    follow_up = result.follow_up
    if follow_up in {"REBUILD_CANDIDATE", "TERMINALIZE_INTENT"}:
      if intent.status != "EXECUTION_READY":
        raise ValueError("PAPER_ENTRY_REVIEW_STATE_CHANGED")
      intent.status = (
        "CANCELLED"
        if follow_up == "REBUILD_CANDIDATE"
        else "EXPIRED"
        if any("EXPIRED" in code for code in result.reason_codes)
        else "REJECTED"
      )
      intent.updated_at = now.replace(tzinfo=None)
      await db.flush()
    if result.outcome == "DUPLICATE":
      return
    payload = {
      "intent_id": intent.id,
      "candidate_id": intent.intent_metadata["candidate_id"],
      "candidate_fingerprint": intent.intent_metadata["candidate_fingerprint"],
      "instrument_code": intent.instrument_code,
      "outcome": result.outcome,
      "reason_codes": list(result.reason_codes),
      "follow_up": follow_up,
      "allocation_decision_id": intent.allocation_decision_id,
      "admission_batch_id": intent.admission_batch_id,
      "admission_rank": intent.admission_rank,
      "order_id": result.order_id,
      "paper_event_id": result.receipt.event_id if result.receipt else None,
    }
    event_type = (
      "PAPER_ENTRY_REBUILD_REQUIRED"
      if follow_up == "REBUILD_CANDIDATE"
      else "PAPER_ENTRY_REVIEWED"
    )
    digest = stable_manifest_hash(payload)[:16]
    await TAssistantExecutionRepository(db).append_event(
      TAssistantExecutionEvent(
        execution_id,
        f"paper-entry:{intent.id}:{now.isoformat()}:{digest}",
        event_type,
        now,
        payload,
      )
    )
