"""Complete LIVE portfolio planning cut over isolated, persisted account facts.

The market adapter supplies explicit current/prior-close marks; this reader binds
them into the evidence hash. It never connects to QMT or grants order permission.
"""

from dataclasses import fields
from datetime import UTC
from decimal import Decimal

from quantx_application.t_trade_v3.portfolio_reference import (
  TPortfolioReference,
  aware_time,
)
from quantx_application.t_trade_v3.portfolio_snapshot import (
  IndustryTExposure,
  PortfolioEvidenceCut,
  PortfolioTDecisionSnapshot,
  TEnvelopePosition,
  _hash,
  build_t_trading_envelope,
)
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_contracts.order_lifecycle import TERMINAL_ORDER_STATUSES
from quantx_domain.trading.t_assistant_execution import TAssistantConfigVersion
from sqlalchemy import select

from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  PendingTradeOrder,
  TTradeBatch,
)
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.t_allocation import (
  TAllocationBatchRecord,
  TAllocationDecisionRecord,
)
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantConfigVersionRecord,
  TAssistantDecisionCycleRecord,
  TAssistantExecutionRecord,
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade import Trade
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.account_capacity_service import (
  AccountCapacityService,
  buy_cash_required,
  load_authoritative_account_snapshot,
)
from quantx_infrastructure.services.live_position_attribution import (
  LivePositionAttributionService,
)
from quantx_infrastructure.services.live_t_valuation import (
  LiveTValuationReader,
  money,
  stored_time,
)

_ZERO = Decimal(0)


class LivePortfolioSnapshotReader:
  def __init__(self, db):
    self.db = db

  async def read(
    self,
    *,
    execution_id,
    cycle_id,
    instrument_codes,
    as_of,
    current_marks,
    opening_marks,
    account_max_age_seconds,
  ):
    as_of = aware_time(as_of).astimezone(UTC)
    codes = tuple(sorted(set(instrument_codes)))
    if (
      not self.db.in_transaction()
      or not codes
      or any(not isinstance(code, str) or not code for code in codes)
    ):
      raise ValueError("LIVE_PORTFOLIO_TRANSACTION_AND_SYMBOLS_REQUIRED")
    probe = await self.db.get(TAssistantExecutionRecord, execution_id)
    if probe is None:
      raise ValueError("LIVE_PORTFOLIO_EXECUTION_REQUIRED")
    head = await self.db.get(
      TTradeGlobalConfig, probe.config_id, with_for_update=True, populate_existing=True
    )
    execution = await self.db.get(
      TAssistantExecutionRecord,
      execution_id,
      with_for_update=True,
      populate_existing=True,
    )
    if (
      execution.environment != "LIVE"
      or head is None
      or head.account_id != execution.account_id
    ):
      raise ValueError("LIVE_PORTFOLIO_SCOPE_INVALID")
    record = await self.db.get(
      TAssistantConfigVersionRecord, execution.config_version_id
    )
    if record is None:
      raise ValueError("LIVE_PORTFOLIO_FROZEN_CONFIG_REQUIRED")
    config = TAssistantConfigVersion(
      **{
        field.name: getattr(record, field.name)
        for field in fields(TAssistantConfigVersion)
      }
    )
    if (
      config.config_snapshot_hash != execution.config_snapshot_hash
      or config.version != execution.frozen_config_version
      or config.config_id != head.id
      or config.policy_version != execution.policy_version
      or config.feature_schema_version != execution.feature_schema_version
      or config.scorer_mode.value != execution.scorer_mode
      or config.entry_authorization.value != execution.entry_authorization
      or config.rollout_stage.value != execution.rollout_stage
      or config.model_runtime_binding != execution.model_runtime_binding
      or execution.scorer_mode != "RULE_ONLY"
      or head.active_config_version_id != config.config_version_id
      or head.config_version != config.version
      or head.desired_environment != "LIVE"
    ):
      raise ValueError("LIVE_PORTFOLIO_CONFIG_BINDING_CONFLICT")
    for row in (head, execution, record):
      for name in ("created_at", "updated_at"):
        value = getattr(row, name, None)
        if value is not None and stored_time(value) > as_of:
          raise ValueError("LIVE_PORTFOLIO_FUTURE_EVIDENCE")
    cycle = await self.db.get(
      TAssistantDecisionCycleRecord,
      cycle_id,
      with_for_update=True,
      populate_existing=True,
    )
    if (
      cycle is None
      or cycle.execution_id != execution_id
      or cycle.status not in {"PREPARED", "PROPOSALS_COMMITTED"}
    ):
      raise ValueError("LIVE_PORTFOLIO_CYCLE_SCOPE_INVALID")
    clocks = [execution.entry_readiness_as_of, cycle.prepared_at, cycle.created_at]
    clocks.extend(
      value
      for value in (
        cycle.committed_at,
        execution.started_at,
        execution.drain_requested_at,
        execution.completed_at,
      )
      if value is not None
    )
    if any(stored_time(value) > as_of for value in clocks):
      raise ValueError("LIVE_PORTFOLIO_FUTURE_EVIDENCE")
    attribution = await LivePositionAttributionService(self.db).read(
      account_id=execution.account_id,
      as_of=as_of,
      max_age_seconds=account_max_age_seconds,
    )
    control = await self.db.get(AccountExecutionControl, execution.account_id)
    if any(
      stored_time(value) > as_of
      for value in (control.created_at, control.updated_at, control.authorized_at)
      if value is not None
    ):
      raise ValueError("LIVE_PORTFOLIO_FUTURE_CONTROL")
    payload = await load_authoritative_account_snapshot(self.db, control)
    account = next(
      row for row in payload["accounts"] if row["account_id"] == execution.account_id
    )

    async def rows(statement):
      return list(
        (
          await self.db.scalars(
            statement.with_for_update().execution_options(populate_existing=True)
          )
        ).all()
      )

    batches = await rows(
      select(TTradeBatch)
      .where(
        TTradeBatch.account_id == execution.account_id,
        TTradeBatch.environment == "LIVE",
      )
      .order_by(TTradeBatch.batch_id)
    )
    orders = await rows(
      select(PendingTradeOrder)
      .where(
        PendingTradeOrder.account_id == execution.account_id,
        PendingTradeOrder.environment == "LIVE",
        PendingTradeOrder.t_trade_role == "ENTRY",
      )
      .order_by(PendingTradeOrder.client_order_id)
    )
    intents = await rows(
      select(TradeIntentRecord)
      .where(
        TradeIntentRecord.account_id == execution.account_id,
        TradeIntentRecord.environment == "LIVE",
        TradeIntentRecord.direction == "BUY",
        TradeIntentRecord.status.in_(["AWAITING_APPROVAL", "EXECUTION_READY"]),
      )
      .order_by(TradeIntentRecord.id)
    )
    intents = [
      row
      for row in intents
      if row.owner_type == "T_ASSISTANT_EXECUTION"
      or row.intent_metadata.get("t_batch_id")
    ]
    plans = await rows(
      select(AutoExitPlanRecord)
      .where(
        AutoExitPlanRecord.account_id == execution.account_id,
        AutoExitPlanRecord.environment == "LIVE",
      )
      .order_by(AutoExitPlanRecord.plan_id)
    )
    for row in [*batches, *orders, *intents, *plans]:
      if max(stored_time(row.created_at), stored_time(row.updated_at)) > as_of:
        raise ValueError("LIVE_PORTFOLIO_FUTURE_EVIDENCE")
    economic_codes = (
      set(codes)
      | {
        row.instrument_code
        for row in batches
        if row.entry_filled_volume or row.exit_filled_volume
      }
      | {row.instrument_code for row in orders}
      | {row.instrument_code for row in intents}
    )
    reference = TPortfolioReference.from_config(
      config.canonical_payload, as_of=as_of, required_codes=economic_codes
    )
    for code in codes:
      mark = current_marks.get(code)
      if mark is None or mark.instrument_code != code:
        raise ValueError("LIVE_PORTFOLIO_CURRENT_MARK_REQUIRED")
      mark.validate(as_of, max_age_seconds=reference.mark_max_age_seconds)
    valuation = await LiveTValuationReader(self.db).read(
      account_id=execution.account_id,
      as_of=as_of,
      previous_trading_day=reference.previous_trading_day(as_of),
      opening_marks=opening_marks,
      current_marks=current_marks,
      mark_max_age_seconds=reference.mark_max_age_seconds,
    )
    exposure = dict(valuation.exposure_by_instrument)
    pending = dict.fromkeys(economic_codes, _ZERO)
    accepted_pending = _ZERO
    active_ids = set(valuation.open_batch_ids)
    accepted_intents = set()
    broker_ids = [
      int(row.broker_order_id)
      for row in orders
      if row.broker_order_id and row.broker_order_id.isdecimal()
    ]
    trades = await rows(
      select(Trade)
      .where(Trade.account_id == execution.account_id, Trade.order_id.in_(broker_ids))
      .order_by(Trade.id)
    )
    filled = {}
    for trade in trades:
      filled[str(trade.order_id)] = filled.get(str(trade.order_id), 0) + trade.volume
    observed_orders = {
      str(row.get("order_id") or row.get("broker_order_id") or "")
      for row in payload.get("orders", [])
      if row.get("account_id") == execution.account_id
    }
    for order in orders:
      quantity = max(0, order.volume - filled.get(order.broker_order_id, 0))
      if order.status in TERMINAL_ORDER_STATUSES - {"FILLED"} and (
        not order.broker_order_id or order.broker_order_id in observed_orders
      ):
        quantity = 0
      if quantity:
        amount = buy_cash_required(order.limit_price, quantity)
        pending[order.instrument_code] += amount
        accepted_pending += amount
        if not order.batch_id:
          raise ValueError("LIVE_PORTFOLIO_PENDING_BATCH_REQUIRED")
        active_ids.add(order.batch_id)
        accepted_intents.add(order.intent_id)
      elif filled.get(order.broker_order_id, 0):
        accepted_intents.add(order.intent_id)
    allocation_material = []
    for intent in intents:
      if intent.id in accepted_intents:
        continue
      decision = (
        await self.db.get(TAllocationDecisionRecord, intent.allocation_decision_id)
        if intent.allocation_decision_id
        else None
      )
      allocation = (
        await self.db.get(TAllocationBatchRecord, decision.allocation_batch_id)
        if decision
        else None
      )
      if (
        decision is None
        or allocation is None
        or allocation.status != "COMMITTED"
        or allocation.environment != "LIVE"
        or allocation.execution_id != intent.owner_id
        or decision.intent_id != intent.id
        or decision.intent_version + 1 != intent.allocation_version
        or decision.instrument_code != intent.instrument_code
        or decision.action not in {"ALLOW", "CAP"}
      ):
        raise ValueError("LIVE_PORTFOLIO_READY_ALLOCATION_REQUIRED")
      if (
        max(stored_time(decision.created_at), stored_time(allocation.committed_at))
        > as_of
      ):
        raise ValueError("LIVE_PORTFOLIO_FUTURE_EVIDENCE")
      batch_id = intent.intent_metadata.get("t_batch_id")
      if not isinstance(batch_id, str) or not batch_id:
        raise ValueError("LIVE_PORTFOLIO_READY_BATCH_REQUIRED")
      pending[intent.instrument_code] += money(decision.allocated_amount_cap)
      active_ids.add(batch_id)
      allocation_material.append(
        (
          decision.decision_id,
          decision.allocated_amount_cap,
          allocation.allocation_batch_id,
        )
      )
    capacities = {}
    for code in sorted(economic_codes):
      capacities[code] = await AccountCapacityService(self.db).read(
        control,
        instrument_code=code,
        expected_snapshot_id=attribution.snapshot_id,
        expected_snapshot_hash=attribution.snapshot_hash,
        bucket_inventory=attribution.projection.instruments.get(code, {}),
        protected_core_floor=reference.envelope_policy.protected_core_volume,
        allow_core_claim=True,
      )
    cash_values = {value.available_cash for value in capacities.values()}
    if len(cash_values) != 1:
      raise ValueError("LIVE_PORTFOLIO_MIXED_ACCOUNT_CUT")
    watermark = _hash(
      dict(
        attribution=attribution.evidence_hash,
        valuation=valuation.evidence_hash,
        capacities={code: cap.obligation_watermark for code, cap in capacities.items()},
        allocations=allocation_material,
        controls=dict(
          head_version=head.state_version,
          execution_version=execution.state_version,
          authorization=control.authorization_state,
          reconcile=control.reconcile_status,
          control_version=control.state_version,
        ),
      )
    )
    cut = PortfolioEvidenceCut(
      ExecutionOwnerRef("T_ASSISTANT_EXECUTION", execution_id),
      ExecutionEnvironment.LIVE,
      as_of,
      attribution.snapshot_id,
      attribution.snapshot_hash,
      attribution.snapshot_as_of,
      watermark,
      as_of,
      True,
    )
    industries = dict(reference.industries)
    envelopes = []
    for code in codes:
      state = attribution.projection.instruments.get(code, {})
      cap = capacities[code]
      position = TEnvelopePosition(
        code,
        industries[code],
        *(
          state.get(name, {}).get("total_volume", 0)
          for name in ("locked_core", "core", "swing")
        ),
        min(
          cap.available_volume,
          sum(values["available_volume"] for values in state.values()),
        ),
        sum(cap.old_inventory_claim_allocation.values()),
        exposure.get(code, _ZERO),
        pending[code],
        bool(exposure.get(code, _ZERO) or pending[code]),
      )
      envelopes.append(
        build_t_trading_envelope(
          cut=cut,
          config_version=config.config_version_id,
          policy=reference.envelope_policy,
          position=position,
        )
      )
    aggregates = tuple(
      IndustryTExposure(
        industry,
        sum(
          (
            exposure.get(code, _ZERO)
            for code in economic_codes
            if industries[code] == industry
          ),
          _ZERO,
        ),
        sum(
          (pending[code] for code in economic_codes if industries[code] == industry),
          _ZERO,
        ),
      )
      for industry in sorted({industries[code] for code in economic_codes})
    )
    return PortfolioTDecisionSnapshot(
      cut,
      cycle_id,
      config.config_version_id,
      execution.policy_version,
      execution.scorer_mode,
      reference.portfolio_policy,
      tuple(envelopes),
      aggregates,
      # Capacity already deducts accepted orders. Add back exactly their T
      # planning amount because the shared snapshot subtracts pending T again.
      next(iter(cash_values)) + accepted_pending,
      money(account.get("total_asset")),
      sum(pending.values(), _ZERO),
      sum(exposure.values(), _ZERO),
      valuation.valuation.realized,
      valuation.valuation.unrealized,
      len(active_ids),
      head.enabled is True
      and execution.status == "RUNNING"
      and execution.entry_readiness == "READY"
      and control.authorization_state == "ENABLED",
      head.enabled is not True or control.authorization_state != "ENABLED",
      control.reconcile_status != "READY"
      or execution.status in {"FAILED", "RECONCILE_REQUIRED"}
      or any(
        row.status in {"UNKNOWN", "RECONCILE_REQUIRED", "ERROR"}
        for row in [*orders, *batches, *plans]
      ),
    )
