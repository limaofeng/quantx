"""Final PAPER BUY review using the public gate, sizing, risk and receipt path."""

import uuid
from copy import deepcopy
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime
from decimal import Decimal

from quantx_application.t_trade_v3.entry_execution_gate import (
  EntryExecutionBinding,
  EntryExecutionDecision,
  EntryExecutionGate,
  EntryExecutionGateInput,
  EntryExecutionGatePolicy,
  MarketDataCapabilityManifest,
)
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_domain.brokers.base import OrderRequest, OrderType, PriceType
from quantx_domain.clock import SHANGHAI
from quantx_domain.strategies.base import TAssistantExecutionIntentOrigin, TradeIntent
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_domain.trading.order_sizer import OrderSizer
from quantx_domain.trading.risk_checker import RiskAction, TradingRiskChecker
from quantx_domain.trading.t_assistant_execution import (
  TAssistantConfigVersion,
  stable_manifest_hash,
)
from quantx_domain.trading.t_assistant_market_state import (
  TAssistantSymbolState,
  candidate_evidence_key,
  decode_candidate_evidence,
)
from sqlalchemy import select

from quantx_infrastructure.models.paper_execution import (
  PaperExecutionEventRecord,
  PaperExecutionOrderRecord,
)
from quantx_infrastructure.models.t_allocation import (
  TAllocationBatchRecord,
  TAllocationDecisionRecord,
)
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
from quantx_infrastructure.repositories.t_trade_opportunity_intelligence_repository import (
  _evaluation_fingerprint,
)
from quantx_infrastructure.services.account_capacity_service import (
  AccountCapacityService,
)
from quantx_infrastructure.services.paper_broker_matching import (
  PaperBrokerMatching,
  _json,
)
from quantx_infrastructure.services.paper_execution_ledger import (
  PaperExecutionLedger,
  PaperLedgerReceipt,
  _stored_time,
)
from quantx_infrastructure.services.paper_portfolio_snapshot import (
  PaperPortfolioSnapshotReader,
)
from quantx_infrastructure.services.paper_receipt_convergence import (
  PaperReceiptConvergence,
)
from quantx_infrastructure.services.t_allocation_serialization import (
  allocation_evidence,
)
from quantx_infrastructure.services.trade_intent_intake import (
  trade_intent_initial_material,
)


@dataclass(frozen=True)
class PaperEntryReviewResult:
  outcome: str
  reason_codes: tuple[str, ...]
  order_id: str | None = None
  receipt: PaperLedgerReceipt | None = None

  @property
  def follow_up(self) -> str:
    """Required dispatcher transition, persisted with its execution audit event."""
    if self.outcome in {"ACCEPTED", "DUPLICATE"}:
      return "NONE"
    if "PAPER_ADMISSION_PREDECESSOR_PENDING" in self.reason_codes:
      return "WAIT_PREDECESSOR"
    if self.outcome == "DELAY":
      return "REBUILD_CANDIDATE"
    return "TERMINALIZE_INTENT"


class PaperEntryExecutionReview:
  """Caller commits. Enter before taking execution/account locks.

  The supervisor must provide the newest accepted Tick plus the original full
  book from that same Tick. This adapter cannot discover a newer in-memory Tick.
  The dispatcher must persist every denial and its reason codes in the standard
  intent/execution event transaction. REBUILD_CANDIDATE revokes the old grant and
  rearms candidate -> scoring -> allocation; only WAIT_PREDECESSOR retains it.
  """

  def __init__(self, db):
    self.db = db

  async def review(
    self,
    *,
    execution_id: str,
    intent_id: str,
    gate_input: EntryExecutionGateInput,
    market_data: MarketDataSnapshot,
    now: datetime,
    order_attempt: int = 0,
  ) -> PaperEntryReviewResult:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
      raise ValueError("PAPER_REVIEW_AWARE_TIME_REQUIRED")
    if not isinstance(gate_input, EntryExecutionGateInput) or not isinstance(
      market_data, MarketDataSnapshot
    ):
      raise TypeError("PAPER_REVIEW_TYPED_MARKET_REQUIRED")
    if type(order_attempt) is not int or order_attempt < 0:
      raise ValueError("PAPER_REVIEW_ATTEMPT_INVALID")
    now = now.astimezone(UTC)
    # Domain/business denials are explicit outcomes; exceptions from persistence
    # and the awaited real sink propagate and roll back the entire savepoint.
    async with self.db.begin_nested():
      return await self._review(
        execution_id, intent_id, gate_input, market_data, now, order_attempt
      )

  async def _review(self, execution_id, intent_id, gate, market, now, attempt):
    def deny(reason, outcome="REJECT"):
      return PaperEntryReviewResult(outcome, (reason,))

    async def get(model, identity, *, lock=False):
      return await self.db.get(
        model, identity, with_for_update=lock, populate_existing=True
      )

    # Publication owns head -> execution; a final authorization uses that order.
    probe = await get(TAssistantExecutionRecord, execution_id)
    if probe is None or probe.environment != "PAPER":
      return deny("PAPER_REVIEW_EXECUTION_SCOPE")
    intent = await get(TradeIntentRecord, intent_id)
    if (
      intent is None
      or intent.environment != "PAPER"
      or intent.owner_type != "T_ASSISTANT_EXECUTION"
      or intent.owner_id != execution_id
      or intent.account_id != probe.account_id
      or intent.direction != "BUY"
      or gate.intent_id != intent_id
      or gate.instrument_code != intent.instrument_code
      or gate.execution_environment is not ExecutionEnvironment.PAPER
      or market.instrument_code != intent.instrument_code
    ):
      return deny("PAPER_REVIEW_INTENT_SCOPE")
    intake_hash = stable_manifest_hash(trade_intent_initial_material(intent))
    order_id = "pe:" + str(
      uuid.uuid5(
        uuid.NAMESPACE_URL, f"quantx:paper-entry:{execution_id}:{intent_id}:{attempt}"
      )
    )
    event_key = f"entry-review:{intent_id}:{attempt}"

    async def recover():
      existing = await get(PaperExecutionOrderRecord, order_id)
      if existing is None:
        return None
      # A committed receipt is history, not a new authorization. New Tick/time,
      # expiry and a stopped source cannot revoke or duplicate that acceptance.
      event = await self.db.scalar(
        select(PaperExecutionEventRecord).where(
          PaperExecutionEventRecord.execution_id == execution_id,
          PaperExecutionEventRecord.event_key == event_key,
        )
      )
      if (
        existing.execution_id != execution_id
        or existing.environment != "PAPER"
        or existing.intent_id != intent_id
        or existing.order_attempt != attempt
        or existing.owner_type != intent.owner_type
        or existing.owner_id != intent.owner_id
        or existing.instrument_code != intent.instrument_code
        or existing.side != "BUY"
        or existing.request_payload["metadata"].get("entry_intake_hash") != intake_hash
        or event is None
        or event.environment != "PAPER"
        or event.event_type != "ORDER"
        or event.input_payload.get("order_id") != order_id
        or event.input_payload.get("intent_id") != intent_id
        or event.input_payload.get("order_attempt") != attempt
        or event.input_payload.get("request") != existing.request_payload
        or order_id not in event.result_payload["order_ids"]
        or stable_manifest_hash({"event_type": "ORDER", "input": event.input_payload})
        != event.input_hash
      ):
        return deny("PAPER_REVIEW_HISTORY_BINDING_CONFLICT")
      return PaperEntryReviewResult(
        "DUPLICATE",
        ("PAPER_REVIEW_ALREADY_ACCEPTED",),
        order_id,
        PaperLedgerReceipt(event.event_id, True, deepcopy(event.result_payload)),
      )

    historical = await recover()
    if historical is not None:
      return historical
    head = await get(TTradeGlobalConfig, probe.config_id, lock=True)
    execution = await get(TAssistantExecutionRecord, execution_id, lock=True)
    historical = await recover()
    if historical is not None:
      return historical
    if (
      head is None
      or head.account_id != execution.account_id
      or not head.enabled
      or head.active_config_version_id != execution.config_version_id
      or head.config_version != execution.frozen_config_version
      or execution.status != "RUNNING"
      or execution.entry_readiness != "READY"
    ):
      return deny("PAPER_REVIEW_ENTRY_DISABLED")
    ledger = PaperExecutionLedger(self.db, receipt_sink=PaperReceiptConvergence())
    account = await ledger._account(execution_id, lock=True)
    intent = await get(TradeIntentRecord, intent_id, lock=True)
    if (
      intent is None
      or intent.environment != "PAPER"
      or intent.owner_type != "T_ASSISTANT_EXECUTION"
      or intent.owner_id != execution_id
      or intent.account_id != execution.account_id
      or intent.direction != "BUY"
    ):
      return deny("PAPER_REVIEW_INTENT_SCOPE")
    gate_witness = allocation_evidence(gate)
    gate_witness["quality_fields"] = sorted(gate.quality_fields)
    gate_witness["capabilities"]["required_fields"] = sorted(
      gate.capabilities.required_fields
    )
    gate_witness["capabilities"]["optional_fields"] = sorted(
      gate.capabilities.optional_fields
    )
    review_hash = stable_manifest_hash(
      allocation_evidence(
        {"gate": gate_witness, "market": _json(market), "now": now, "attempt": attempt}
      )
    )
    if intent.status != "EXECUTION_READY":
      return deny("PAPER_REVIEW_INTENT_NOT_READY")
    version = await get(TAssistantConfigVersionRecord, execution.config_version_id)
    if version is None:
      return deny("PAPER_REVIEW_CONFIG_REQUIRED")
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
      return deny("PAPER_REVIEW_FROZEN_GATE_POLICY_REQUIRED")
    cycle = await get(
      TAssistantDecisionCycleRecord, intent.allocation_cycle_id, lock=True
    )
    symbol = await self.db.scalar(
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
      return deny("PAPER_REVIEW_CANDIDATE_EVIDENCE_REQUIRED")
    if _stored_time(symbol.updated_at) > now or _stored_time(symbol.created_at) > now:
      return deny("PAPER_REVIEW_FUTURE_SYMBOL_STATE")
    state = TAssistantSymbolState.from_dict(symbol.state_payload)
    metadata = intent.intent_metadata
    references = [
      item
      for item in cycle.output_manifest["accepted_intents"]
      if item["intent_id"] == intent_id
    ]
    if len(references) != 1:
      return deny("PAPER_REVIEW_CANDIDATE_EVIDENCE_REQUIRED")
    reference = references[0]
    source = await self.db.scalar(
      select(TTradeOpportunityEvaluation).where(
        TTradeOpportunityEvaluation.event_key == reference["candidate_evidence_key"]
      )
    )
    if (
      source is None
      or source.event_type != "T_OPPORTUNITY_CANDIDATE_FROZEN"
      or source.owner_type != "T_ASSISTANT_EXECUTION"
      or source.owner_id != execution_id
      or source.environment != "PAPER"
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
      return deny("PAPER_REVIEW_CANDIDATE_EVIDENCE_REQUIRED")
    witness = source.payload["candidate_evidence"]
    candidate, original_tick, cursor = decode_candidate_evidence(witness)
    evaluated_at = source.evaluated_at
    if evaluated_at.tzinfo is None:
      evaluated_at = evaluated_at.replace(tzinfo=SHANGHAI)
    if (
      _stored_time(source.created_at) > now
      or evaluated_at > now
      or max(original_tick.received_at_ms, witness["evaluation"]["evaluated_at_ms"])
      > int(now.timestamp() * 1000)
    ):
      return deny("PAPER_REVIEW_FUTURE_CANDIDATE_EVIDENCE")
    if stable_manifest_hash(witness) != reference[
      "candidate_evidence_hash"
    ] or reference["candidate_evidence_key"] != candidate_evidence_key(
      execution_id, candidate.fingerprint
    ):
      return deny("PAPER_REVIEW_CANDIDATE_BINDING_CONFLICT")
    if state.opportunity_state.candidate != candidate:
      return deny("PAPER_REVIEW_CANDIDATE_EVIDENCE_ADVANCED", "DELAY")
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
      return deny("PAPER_REVIEW_CANDIDATE_BINDING_CONFLICT")
    expected = EntryExecutionBinding(
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
    created = datetime.fromisoformat(metadata["intent_created_at"])
    deadline = (
      min(int(created.timestamp() * 1000), metadata["source_time_ms"])
      + metadata["approval_ttl_ms"]
    )
    if (
      gate.intent_id != intent_id
      or gate.instrument_code != intent.instrument_code
      or not gate.intent_active
      or gate.execution_environment is not ExecutionEnvironment.PAPER
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
        state.opportunity_state.candidate_status.value
        in {"LATCHED", "AWAITING_APPROVAL"}
      )
    ):
      return deny("PAPER_REVIEW_GATE_BINDING_CONFLICT")
    result = EntryExecutionGate.evaluate(gate)
    if result.decision is not EntryExecutionDecision.ALLOW:
      return PaperEntryReviewResult(result.decision.value, result.reason_codes)
    tick = gate.latest_tick.sample
    try:
      PaperBrokerMatching._validate_quote(market)
    except (TypeError, ValueError):
      return deny("PAPER_REVIEW_FULL_BOOK_REQUIRED")
    if (
      market.instrument_code != intent.instrument_code
      or market.timestamp != datetime.fromtimestamp(tick.source_time_ms / 1000, UTC)
      or market.price != tick.price
      or market.bid_price[0] != tick.bid_price
      or market.ask_price[0] != tick.ask_price
      or market.bid_vol[0] != tick.bid_volume
      or market.ask_vol[0] != tick.ask_volume
      or market.limit_up is None
      or market.limit_down is None
    ):
      return deny("PAPER_REVIEW_MARKET_WITNESS_CONFLICT")
    decision = await get(TAllocationDecisionRecord, intent.allocation_decision_id)
    allocation = (
      await get(TAllocationBatchRecord, decision.allocation_batch_id)
      if decision
      else None
    )
    if (
      decision is None
      or allocation is None
      or allocation.status != "COMMITTED"
      or decision.intent_id != intent_id
      or decision.candidate_id != candidate.candidate_id
      or allocation.execution_id != execution_id
      or allocation.environment != "PAPER"
    ):
      return deny("PAPER_REVIEW_ALLOCATION_REQUIRED")
    portfolio = await PaperPortfolioSnapshotReader(self.db).read(
      execution_id=execution_id,
      cycle_id=cycle.cycle_id,
      instrument_codes=(intent.instrument_code,),
      as_of=now,
    )
    if portfolio.entry_blockers:
      return PaperEntryReviewResult("REJECT", portfolio.entry_blockers)
    capacity = await AccountCapacityService(self.db).read(
      environment=ExecutionEnvironment.PAPER,
      paper_execution_id=execution_id,
      account_id=execution.account_id,
      instrument_code=intent.instrument_code,
      expected_snapshot_hash=account.snapshot_hash,
      protected_core_floor=portfolio.envelopes[0].policy.protected_core_volume,
      allow_core_claim=True,
    )
    domain_intent = TradeIntent(
      strategy_id=intent.strategy_id,
      instrument_code=intent.instrument_code,
      direction="BUY",
      bucket=intent.bucket,
      reason=intent.reason,
      target_amount=intent.target_amount,
      target_volume=intent.target_volume,
      target_position_pct=intent.target_position_pct,
      intent_id=intent.id,
      metadata=dict(metadata),
      created_at=_stored_time(created),
      execution_ref=ExecutionOwnerRef("T_ASSISTANT_EXECUTION", execution_id),
      origin=TAssistantExecutionIntentOrigin(
        execution_id,
        "paper-entry-review",
        candidate_id=candidate.candidate_id,
        opportunity_id=candidate.candidate_id,
        cycle_id=cycle.cycle_id,
      ),
    )
    price = float(
      intent.limit_price_hint
      if intent.limit_price_hint is not None
      else candidate.price
    )
    balances = {
      "cash": float(capacity.available_cash),
      "total_asset": float(portfolio.total_assets),
    }
    holding = {
      **account.broker_checkpoint["material"]["positions"].get(
        intent.instrument_code, {}
      ),
      "available_volume": capacity.available_volume,
      "t_trade_exit_capacity": capacity.unclaimed_volume,
    }
    draft = OrderSizer().draft_intent(
      domain_intent,
      OrderType.BUY,
      price,
      balances,
      holding,
      allocated_amount_cap=Decimal(decision.allocated_amount_cap),
    )
    if draft.sized_volume <= 0:
      return PaperEntryReviewResult(
        "REJECT", tuple(draft.size_reason_codes) + ("PAPER_REVIEW_ZERO_SIZE",)
      )
    request = OrderRequest(
      instrument_code=intent.instrument_code,
      order_type=OrderType.BUY,
      price_type=PriceType.LIMIT,
      volume=draft.sized_volume,
      price=price,
      execution_ref=domain_intent.execution_ref,
      environment=ExecutionEnvironment.PAPER,
      metadata={
        "bucket": intent.bucket,
        "intent_id": intent_id,
        "entry_review_hash": review_hash,
        "entry_intake_hash": intake_hash,
        "entry_gate_witness": gate_witness,
        "entry_market_witness": _json(market),
        "order_expire_at_ms": min(candidate.expires_at_ms, deadline),
      },
    )
    risk = await TradingRiskChecker(
      strict_market_data=True, strict_limit_data=True, enforce_trading_hours=True
    ).evaluate_order(
      request,
      account=balances,
      position=holding,
      market_data=market,
      current_time=now.astimezone(SHANGHAI),
    )
    if not risk.allowed or risk.action not in {RiskAction.ALLOW, RiskAction.CAP}:
      return PaperEntryReviewResult(risk.action.value, (risk.reason_code,))
    request = replace(request, volume=risk.final_volume)
    try:
      await ledger._authorize_order(account, intent_id, request, draft, now)
    except ValueError as exc:
      return deny(
        str(exc),
        "DELAY" if str(exc) == "PAPER_ADMISSION_PREDECESSOR_PENDING" else "REJECT",
      )
    # Ledger rechecks the committed allocation/admission, original TTL and rank
    # under these same locks, and awaits the real shared receipt convergence.
    receipt = await ledger.place_order(
      execution_id=execution_id,
      intent_id=intent_id,
      event_key=event_key,
      order_id=order_id,
      order_attempt=attempt,
      request=request,
      sizing_evidence=draft,
      risk_evidence=risk,
      expected_revision=account.revision,
      expected_snapshot_hash=account.snapshot_hash,
      now=now,
    )
    return PaperEntryReviewResult(
      "ACCEPTED",
      tuple(result.reason_codes) + tuple(draft.size_reason_codes),
      order_id,
      receipt,
    )
