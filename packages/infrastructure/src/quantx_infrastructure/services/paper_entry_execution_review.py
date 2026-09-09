"""Final PAPER BUY review using the public gate, sizing, risk and receipt path."""

import uuid
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal

from quantx_application.t_trade_v3.entry_execution_gate import (
  EntryExecutionGateInput,
)
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_domain.brokers.base import OrderRequest, OrderType, PriceType
from quantx_domain.clock import SHANGHAI
from quantx_domain.strategies.base import TAssistantExecutionIntentOrigin, TradeIntent
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_domain.trading.order_sizer import OrderSizer
from quantx_domain.trading.risk_checker import RiskAction, TradingRiskChecker
from quantx_domain.trading.t_assistant_execution import (
  stable_manifest_hash,
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
  TAssistantExecutionRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
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
from quantx_infrastructure.services.t_entry_gate_review import review_t_entry_gate
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
    result = await review_t_entry_gate(
      self.db,
      execution=execution,
      intent=intent,
      gate=gate,
      now=now,
    )
    if result.outcome != "ALLOW":
      return PaperEntryReviewResult(result.outcome, result.reason_codes)
    candidate, cycle = result.candidate, result.cycle
    metadata = intent.intent_metadata
    created = datetime.fromisoformat(metadata["intent_created_at"])
    deadline = gate.intent_expires_at_ms
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
