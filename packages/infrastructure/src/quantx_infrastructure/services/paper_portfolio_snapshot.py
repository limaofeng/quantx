"""One transaction cut of isolated PAPER facts for the public T allocator."""

from dataclasses import fields
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo

from quantx_application.t_trade_v3.daily_t_valuation import (
  TDailyFill,
  TOpeningPosition,
  TValuationMark,
  value_daily_t_positions,
)
from quantx_application.t_trade_v3.portfolio_reference import (
  TPortfolioReference,
  aware_time,
)
from quantx_application.t_trade_v3.portfolio_snapshot import (
  IndustryTExposure,
  PortfolioEvidenceCut,
  PortfolioTDecisionSnapshot,
  TEnvelopePosition,
  build_t_trading_envelope,
)
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_domain.trading.exit_plan import ExitPlanTemplate
from quantx_domain.trading.t_assistant_execution import (
  TAssistantConfigVersion,
  stable_manifest_hash,
)
from sqlalchemy import and_, or_, select

from quantx_infrastructure.models.agent_runtime import TTradeBatch
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.paper_execution import (
  PaperExecutionAccountRecord,
  PaperExecutionEventRecord,
  PaperExecutionFillRecord,
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
)
from quantx_infrastructure.models.t_trade_global_config import TTradeGlobalConfig
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.account_capacity_service import (
  AccountCapacityService,
  paper_pending_buy_cash,
)
from quantx_infrastructure.services.paper_execution_ledger import (
  _quote_event_clock,
  _stored_time,
)
from quantx_infrastructure.services.t_allocation_serialization import (
  allocation_evidence,
)

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_ZERO = Decimal(0)


def _money(value):
  if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
    raise ValueError("PAPER_PORTFOLIO_AMOUNT_INVALID")
  result = Decimal(str(value))
  if not result.is_finite() or result < 0:
    raise ValueError("PAPER_PORTFOLIO_AMOUNT_INVALID")
  return result


def _not_future(value, cut):
  result = _stored_time(value)
  if result > cut:
    raise ValueError("PAPER_PORTFOLIO_FUTURE_EVIDENCE")
  return result


def _head_material(row):
  return allocation_evidence(
    {
      attribute.key: getattr(row, attribute.key)
      for attribute in row.__mapper__.column_attrs
    }
  )


class PaperPortfolioSnapshotReader:
  def __init__(self, db):
    self.db = db

  async def read(
    self, *, execution_id: str, cycle_id: str, instrument_codes, as_of: datetime
  ) -> PortfolioTDecisionSnapshot:
    as_of = aware_time(as_of).astimezone(UTC)
    codes = tuple(sorted(set(instrument_codes)))
    if (
      not execution_id
      or not cycle_id
      or not codes
      or any(not isinstance(code, str) or not code.strip() for code in codes)
    ):
      raise ValueError("PAPER_PORTFOLIO_IDENTITY_REQUIRED")

    async def rows(statement, *, lock=True):
      if lock:
        statement = statement.with_for_update()
      return list(
        (
          await self.db.scalars(statement.execution_options(populate_existing=True))
        ).all()
      )

    executions = await rows(
      select(TAssistantExecutionRecord).where(
        TAssistantExecutionRecord.execution_id == execution_id
      )
    )
    if len(executions) != 1 or executions[0].environment != "PAPER":
      raise ValueError("PAPER_PORTFOLIO_EXECUTION_SCOPE_INVALID")
    execution = executions[0]
    accounts = await rows(
      select(PaperExecutionAccountRecord).where(
        PaperExecutionAccountRecord.execution_id == execution_id
      )
    )
    if (
      len(accounts) != 1
      or accounts[0].account_id != execution.account_id
      or accounts[0].environment != "PAPER"
    ):
      raise ValueError("PAPER_PORTFOLIO_ACCOUNT_SCOPE_INVALID")
    account = accounts[0]
    _not_future(account.snapshot_as_of, as_of)
    _not_future(account.seed_as_of, as_of)
    _not_future(execution.created_at, as_of)
    _not_future(execution.entry_readiness_as_of, as_of)
    for value in (
      execution.started_at,
      execution.drain_requested_at,
      execution.completed_at,
    ):
      if value is not None:
        _not_future(value, as_of)
    versions = await rows(
      select(TAssistantConfigVersionRecord).where(
        TAssistantConfigVersionRecord.config_version_id == execution.config_version_id
      ),
      lock=False,
    )
    if len(versions) != 1:
      raise ValueError("PAPER_PORTFOLIO_CONFIG_REQUIRED")
    version = versions[0]
    _not_future(version.created_at, as_of)
    # Domain constructor revalidates the one canonical immutable config hash.
    frozen = TAssistantConfigVersion(
      **{
        field.name: getattr(version, field.name)
        for field in fields(TAssistantConfigVersion)
      }
    )
    if (
      frozen.config_id != execution.config_id
      or frozen.config_snapshot_hash != execution.config_snapshot_hash
      or frozen.version != execution.frozen_config_version
      or frozen.policy_version != execution.policy_version
      or frozen.scorer_mode.value != execution.scorer_mode
      or execution.scorer_mode not in {"RULE_ONLY", "SHADOW"}
      or frozen.feature_schema_version != execution.feature_schema_version
      or frozen.entry_authorization.value != execution.entry_authorization
      or frozen.rollout_stage.value != execution.rollout_stage
      or execution.model_runtime_binding != frozen.model_runtime_binding
    ):
      raise ValueError("PAPER_PORTFOLIO_CONFIG_BINDING_CONFLICT")
    globals_ = await rows(
      select(TTradeGlobalConfig).where(TTradeGlobalConfig.id == execution.config_id),
      lock=False,
    )
    if len(globals_) != 1 or globals_[0].account_id != execution.account_id:
      raise ValueError("PAPER_PORTFOLIO_GLOBAL_CONTROL_REQUIRED")
    control = globals_[0]
    _not_future(control.created_at, as_of)
    _not_future(control.updated_at, as_of)
    if (
      control.active_config_version_id != execution.config_version_id
      or control.config_version != execution.frozen_config_version
    ):
      raise ValueError("PAPER_PORTFOLIO_ACTIVE_CONFIG_CONFLICT")
    # Supervisor publishes while holding head -> execution. Never acquire the
    # reverse lock here, including when a caller already owns execution. Read
    # committed control material twice and reject an observed publication race.
    head_material = _head_material(control)
    cycles = await rows(
      select(TAssistantDecisionCycleRecord).where(
        TAssistantDecisionCycleRecord.cycle_id == cycle_id
      )
    )
    if len(cycles) != 1 or cycles[0].execution_id != execution_id:
      raise ValueError("PAPER_PORTFOLIO_CYCLE_SCOPE_CONFLICT")

    orders = await rows(
      select(PaperExecutionOrderRecord)
      .where(PaperExecutionOrderRecord.execution_id == execution_id)
      .order_by(PaperExecutionOrderRecord.order_id)
    )
    fills = await rows(
      select(PaperExecutionFillRecord)
      .where(PaperExecutionFillRecord.execution_id == execution_id)
      .order_by(PaperExecutionFillRecord.fill_id)
    )
    batches = await rows(
      select(TTradeBatch)
      .where(
        TTradeBatch.source_execution_owner_id == execution_id,
        TTradeBatch.environment == "PAPER",
      )
      .order_by(TTradeBatch.batch_id)
    )
    plans = await rows(
      select(AutoExitPlanRecord)
      .where(
        AutoExitPlanRecord.source_execution_owner_id == execution_id,
        AutoExitPlanRecord.environment == "PAPER",
      )
      .order_by(AutoExitPlanRecord.plan_id)
    )
    intents = await rows(
      select(TradeIntentRecord)
      .where(
        TradeIntentRecord.id.in_([row.intent_id for row in orders])
        | (
          (TradeIntentRecord.owner_type == "T_ASSISTANT_EXECUTION")
          & (TradeIntentRecord.owner_id == execution_id)
          & (TradeIntentRecord.environment == "PAPER")
        )
        | (
          (TradeIntentRecord.owner_type == "EXIT_PLAN")
          & (TradeIntentRecord.owner_id.in_([row.plan_id for row in plans]))
          & (TradeIntentRecord.environment == "PAPER")
        )
      )
      .order_by(TradeIntentRecord.id)
    )
    accepted_intents = {order.intent_id for order in orders}
    referenced_decisions = {
      intent.allocation_decision_id
      for intent in intents
      if intent.allocation_decision_id
      and (
        intent.status in {"AWAITING_APPROVAL", "EXECUTION_READY"}
        or intent.id in accepted_intents
      )
    }
    decisions = await rows(
      select(TAllocationDecisionRecord)
      .where(TAllocationDecisionRecord.decision_id.in_(referenced_decisions))
      .order_by(TAllocationDecisionRecord.decision_id)
    )
    allocation_batches = await rows(
      select(TAllocationBatchRecord)
      .where(
        TAllocationBatchRecord.allocation_batch_id.in_(
          [item.allocation_batch_id for item in decisions]
        ),
        TAllocationBatchRecord.status == "COMMITTED",
      )
      .order_by(TAllocationBatchRecord.allocation_batch_id)
    )
    if {row.decision_id for row in decisions} != referenced_decisions or {
      row.allocation_batch_id for row in allocation_batches
    } != {row.allocation_batch_id for row in decisions}:
      raise ValueError("PAPER_PORTFOLIO_COMMITTED_ALLOCATION_REQUIRED")
    # Current mutable rows cannot release a claim at a cut before their version
    # became available. Database TimestampMixin values use UTC at this adapter.
    for row in [*intents, *batches, *plans]:
      _not_future(row.created_at, as_of)
      _not_future(row.updated_at, as_of)
    for plan in plans:
      if plan.last_evaluated_at is not None:
        evaluated = plan.last_evaluated_at
        if evaluated.tzinfo is None:
          evaluated = evaluated.replace(tzinfo=_SHANGHAI)
        _not_future(evaluated, as_of)
    positions = account.broker_checkpoint["material"]["positions"]
    all_codes = (
      set(codes)
      | set(positions)
      | {item.instrument_code for item in batches}
      | {item.instrument_code for item in intents}
    )
    today = as_of.astimezone(_SHANGHAI).date()
    order_map = {row.order_id: row for row in orders}
    # History stays in accounting/watermark. Current marks are needed only for
    # live economic quantities; closed intraday lots need industry attribution.
    current_mark_codes = (
      set(codes)
      | {code for code, position in positions.items() if position["long_volume"] > 0}
      | {
        batch.instrument_code
        for batch in batches
        if batch.entry_filled_volume > batch.exit_filled_volume
      }
      | {
        order.instrument_code
        for order in orders
        if order.status in {"PENDING", "SUBMITTED", "PARTIAL_FILLED"}
      }
      | {
        intent.instrument_code
        for intent in intents
        if intent.status in {"AWAITING_APPROVAL", "EXECUTION_READY"}
      }
    )
    economic_codes = current_mark_codes | {
      order_map[fill.order_id].instrument_code
      for fill in fills
      if fill.order_id in order_map
      and _stored_time(fill.occurred_at).astimezone(_SHANGHAI).date() == today
    }
    reference = TPortfolioReference.from_config(
      frozen.canonical_payload, as_of=as_of, required_codes=economic_codes
    )
    previous_day = reference.previous_trading_day(as_of)
    industries = dict(reference.industries)
    capacities = {}
    for code in sorted(economic_codes):
      capacities[code] = await AccountCapacityService(self.db).read(
        instrument_code=code,
        environment=ExecutionEnvironment.PAPER,
        paper_execution_id=execution_id,
        account_id=execution.account_id,
        expected_snapshot_id=f"paper:{execution_id}:{account.revision}",
        expected_snapshot_hash=account.snapshot_hash,
        protected_core_floor=reference.envelope_policy.protected_core_volume,
        allow_core_claim=True,
      )

    material = account.broker_checkpoint["material"]
    referenced_event_ids = {fill.event_id for fill in fills} | {
      witness["event_id"]
      for code, witness in material["latest_quote_events"].items()
      if code in current_mark_codes
    }
    close_start = datetime.combine(previous_day, time(15), _SHANGHAI).astimezone(UTC)
    close_end = close_start + timedelta(seconds=min(5, reference.mark_max_age_seconds))
    events = await rows(
      select(PaperExecutionEventRecord)
      .where(
        PaperExecutionEventRecord.execution_id == execution_id,
        or_(
          PaperExecutionEventRecord.event_id.in_(referenced_event_ids),
          PaperExecutionEventRecord.revision == account.revision,
          and_(
            PaperExecutionEventRecord.event_type == "QUOTE",
            PaperExecutionEventRecord.quote_source_at >= close_start,
            PaperExecutionEventRecord.quote_source_at <= close_end,
            PaperExecutionEventRecord.occurred_at <= as_of,
          ),
        ),
      )
      .order_by(PaperExecutionEventRecord.revision),
      lock=False,
    )
    # These are the immutable facts needed by this valuation, not a full ledger
    # history scan. Database ledger guards enforce the complete revision chain.
    event_map = {}
    for event in events:
      occurred = _not_future(event.occurred_at, as_of)
      if event.event_type == "QUOTE":
        _quote_event_clock(event)
      if (
        event.environment != "PAPER"
        or not 1 <= event.revision <= account.revision
        or occurred < _stored_time(account.seed_as_of)
        or stable_manifest_hash(
          {"event_type": event.event_type, "input": event.input_payload}
        )
        != event.input_hash
      ):
        raise ValueError("PAPER_PORTFOLIO_EVENT_EVIDENCE_CONFLICT")
      event_map[event.event_id] = event
    latest = next(
      (event for event in events if event.revision == account.revision), None
    )
    if account.revision > 0 and (
      latest is None
      or latest.resulting_snapshot_hash != account.snapshot_hash
      or _stored_time(latest.occurred_at) != _stored_time(account.snapshot_as_of)
    ):
      raise ValueError("PAPER_PORTFOLIO_CURRENT_EVENT_CONFLICT")
    if not referenced_event_ids.issubset(event_map):
      raise ValueError("PAPER_PORTFOLIO_REFERENCED_EVENT_REQUIRED")
    if account.revision == 0 and account.initial_snapshot_hash != account.snapshot_hash:
      raise ValueError("PAPER_PORTFOLIO_INITIAL_SNAPSHOT_CONFLICT")
    current_marks, opening_marks = {}, {}
    for code in current_mark_codes:
      quote = material["market_snapshots"].get(code)
      if quote is not None:
        source_id = material["latest_quote_events"][code]["event_id"]
        source_event = event_map.get(source_id)
        if (
          source_event is None
          or source_event.event_type != "QUOTE"
          or source_event.input_payload["quote"]["instrument_code"] != code
          or aware_time(source_event.input_payload["quote"]["timestamp"])
          != aware_time(quote["timestamp"])
          or _money(source_event.input_payload["quote"]["price"])
          != _money(quote["price"])
        ):
          raise ValueError("PAPER_PORTFOLIO_QUOTE_EVENT_CONFLICT")
        mark = TValuationMark(
          code, _money(quote["price"]), aware_time(quote["timestamp"]), source_id
        )
      elif code in positions:
        seed_position = account.seed_payload["positions"].get(code)
        if seed_position is None:
          raise ValueError("PAPER_PORTFOLIO_CURRENT_MARK_REQUIRED")
        mark = TValuationMark(
          code,
          _money(seed_position["last_price"]),
          _stored_time(account.seed_as_of),
          account.seed_snapshot_id,
        )
      else:
        raise ValueError("PAPER_PORTFOLIO_CURRENT_MARK_REQUIRED")
      mark.validate(as_of, max_age_seconds=reference.mark_max_age_seconds)
      current_marks[code] = mark
    for event in events:
      if event.event_type != "QUOTE":
        continue
      quote = event.input_payload["quote"]
      mark_at = aware_time(quote["timestamp"])
      local = mark_at.astimezone(_SHANGHAI)
      close = datetime.combine(previous_day, time(15), _SHANGHAI)
      closing_age = (local - close).total_seconds()
      if local.date() != previous_day or not 0 <= closing_age <= min(
        5, reference.mark_max_age_seconds
      ):
        continue
      code = quote["instrument_code"]
      opening_marks[code] = TValuationMark(
        code, _money(quote["price"]), mark_at, event.event_id
      )

    intent_map = {row.id: row for row in intents}
    plan_map = {row.plan_id: row for row in plans}
    batch_map = {row.batch_id: row for row in batches}
    pending = dict.fromkeys(all_codes, _ZERO)
    entry_ids = set()
    for intent in intents:
      if intent.environment != "PAPER" or intent.account_id != execution.account_id:
        raise ValueError("PAPER_PORTFOLIO_INTENT_SCOPE_CONFLICT")
      created = intent.intent_metadata.get("intent_created_at")
      if created is None:
        raise ValueError("PAPER_PORTFOLIO_INTENT_TIME_REQUIRED")
      _not_future(aware_time(created), as_of)
      source_ms = intent.intent_metadata.get("source_time_ms")
      if source_ms is not None:
        if isinstance(source_ms, bool) or not isinstance(source_ms, int):
          raise ValueError("PAPER_PORTFOLIO_INTENT_TIME_REQUIRED")
        _not_future(datetime.fromtimestamp(source_ms / 1000, UTC), as_of)
    for order in orders:
      _not_future(order.submitted_at, as_of)
      if order.side == "BUY":
        amount = paper_pending_buy_cash(order)
        pending[order.instrument_code] += amount
        if amount:
          entry_ids.add(intent_map[order.intent_id].intent_metadata["t_batch_id"])
    decision_map = {row.decision_id: row for row in decisions}
    allocation_map = {row.allocation_batch_id: row for row in allocation_batches}
    for decision in decisions:
      _not_future(decision.created_at, as_of)
    for allocation in allocation_batches:
      _not_future(allocation.created_at, as_of)
      if allocation.committed_at is not None:
        _not_future(allocation.committed_at, as_of)
    ordered_intents = {
      row.intent_id
      for row in orders
      if row.status in {"PENDING", "SUBMITTED", "PARTIAL_FILLED"}
    }
    for intent in intents:
      if (
        intent.direction != "BUY"
        or intent.status not in {"AWAITING_APPROVAL", "EXECUTION_READY"}
        or intent.id in ordered_intents
      ):
        continue
      decision = decision_map.get(intent.allocation_decision_id)
      allocation = (
        allocation_map.get(decision.allocation_batch_id)
        if decision is not None
        else None
      )
      if (
        decision is None
        or allocation is None
        or allocation.status != "COMMITTED"
        or allocation.execution_id != execution_id
        or allocation.environment != "PAPER"
        or decision.intent_id != intent.id
        or decision.intent_version + 1 != intent.allocation_version
        or decision.action not in {"ALLOW", "CAP"}
        or decision.instrument_code != intent.instrument_code
      ):
        raise ValueError("PAPER_PORTFOLIO_READY_ALLOCATION_CONFLICT")
      _not_future(decision.created_at, as_of)
      pending[intent.instrument_code] += _money(decision.allocated_amount_cap)
      metadata = intent.intent_metadata
      batch_id = metadata.get("t_batch_id")
      try:
        template = ExitPlanTemplate.from_dict(metadata["exit_plan_template"])
      except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("PAPER_PORTFOLIO_READY_SOURCE_REQUIRED") from exc
      if (
        not isinstance(batch_id, str)
        or not batch_id.strip()
        or batch_id != batch_id.strip()
        or template.source_type != "T_TRADE_BATCH"
        or template.source_id != batch_id
        or template.plan_id != metadata.get("exit_plan_id")
        or template.account_id != execution.account_id
        or template.instrument_code != intent.instrument_code
        or template.bucket != intent.bucket
        or template.run_id
        or template.metadata.get("source_execution_ref")
        != ExecutionOwnerRef("T_ASSISTANT_EXECUTION", execution_id).to_dict()
        or any(
          key in template.metadata and template.metadata[key] != value
          for key, value in {
            "source_execution_owner_type": "T_ASSISTANT_EXECUTION",
            "source_execution_owner_id": execution_id,
            "source_execution_environment": "PAPER",
          }.items()
        )
        or (batch_id in batch_map and batch_map[batch_id].entry_intent_id != intent.id)
        or (
          template.plan_id in plan_map
          and plan_map[template.plan_id].source_id != batch_id
        )
      ):
        raise ValueError("PAPER_PORTFOLIO_READY_SOURCE_CONFLICT")
      entry_ids.add(batch_id)

    daily, opening_qty, quantities, costs = [], {}, {}, {}
    fill_order = []
    for fill in fills:
      event = event_map.get(fill.event_id)
      order = order_map.get(fill.order_id)
      if (
        event is None
        or order is None
        or fill.fill_id not in event.result_payload["fill_ids"]
      ):
        raise ValueError("PAPER_PORTFOLIO_FILL_EVENT_CONFLICT")
      if _stored_time(fill.occurred_at) != _stored_time(event.occurred_at):
        raise ValueError("PAPER_PORTFOLIO_FILL_TIME_CONFLICT")
      fill_order.append(
        (
          event.revision,
          event.result_payload["fill_ids"].index(fill.fill_id),
          fill,
          order,
        )
      )
    with localcontext() as context:
      context.prec = 50
      for revision, index, fill, order in sorted(fill_order, key=lambda item: item[:2]):
        at = _not_future(fill.occurred_at, as_of)
        batch_id = (
          intent_map[order.intent_id].intent_metadata["t_batch_id"]
          if order.side == "BUY"
          else plan_map[order.owner_id].source_id
        )
        if batch_id not in batch_map:
          raise ValueError("PAPER_PORTFOLIO_FILL_BATCH_CONFLICT")
        quantity, cost = quantities.get(batch_id, 0), costs.get(batch_id, _ZERO)
        if order.side == "BUY":
          quantities[batch_id] = quantity + fill.volume
          costs[batch_id] = cost + fill.volume * _money(fill.price) + _money(fill.fee)
        else:
          if fill.volume > quantity:
            raise ValueError("PAPER_PORTFOLIO_EXIT_OVERFILL")
          quantities[batch_id] = quantity - fill.volume
          costs[batch_id] = cost - (
            cost if fill.volume == quantity else cost * fill.volume / quantity
          )
        if at.astimezone(_SHANGHAI).date() < today:
          opening_qty[batch_id] = quantities[batch_id]
        else:
          daily.append(
            TDailyFill(
              fill.fill_id,
              batch_id,
              order.instrument_code,
              order.side,
              fill.volume,
              _money(fill.price),
              _money(fill.fee),
              at,
              revision,
              index,
            )
          )
    exposure = dict.fromkeys(all_codes, _ZERO)
    for batch_id, quantity in quantities.items():
      batch = batch_map[batch_id]
      if quantity != batch.entry_filled_volume - batch.exit_filled_volume:
        raise ValueError("PAPER_PORTFOLIO_BATCH_QUANTITY_CONFLICT")
      exposure[batch.instrument_code] += costs[batch_id]
      if quantity:
        entry_ids.add(batch_id)
    valuation = value_daily_t_positions(
      as_of=as_of,
      previous_trading_day=previous_day,
      opening_positions=tuple(
        TOpeningPosition(batch_id, batch_map[batch_id].instrument_code, quantity)
        for batch_id, quantity in sorted(opening_qty.items())
        if quantity
      ),
      opening_marks=opening_marks,
      current_marks=current_marks,
      fills=tuple(daily),
      mark_max_age_seconds=reference.mark_max_age_seconds,
    )
    if {key: value for key, value in valuation.remaining_quantities if value} != {
      key: value for key, value in quantities.items() if value
    }:
      raise ValueError("PAPER_PORTFOLIO_DAILY_QUANTITY_CONFLICT")
    total_assets = _money(material["state"]["cash"]) + _money(
      material["state"]["non_trading_asset_value"]
    )
    for code, position in positions.items():
      if position["long_volume"]:
        if code not in current_marks:
          raise ValueError("PAPER_PORTFOLIO_CURRENT_MARK_REQUIRED")
        total_assets += position["long_volume"] * current_marks[code].price

    def evidence(row):
      # Admission assigns execution credentials, not new economic obligations.
      # Including these would invalidate the very snapshot that prepared them.
      credential_fields = (
        {
          "admission_batch_id",
          "admission_rank",
          "admission_policy_version",
          "admission_input_fingerprint",
        }
        if isinstance(row, TradeIntentRecord)
        else set()
      )
      return {
        attribute.key: getattr(row, attribute.key)
        for attribute in row.__mapper__.column_attrs
        if attribute.key not in credential_fields
      }

    refreshed_heads = await rows(
      select(TTradeGlobalConfig).where(TTradeGlobalConfig.id == execution.config_id),
      lock=False,
    )
    if refreshed_heads:
      _not_future(refreshed_heads[0].created_at, as_of)
      _not_future(refreshed_heads[0].updated_at, as_of)
    if len(refreshed_heads) != 1 or _head_material(refreshed_heads[0]) != head_material:
      raise ValueError("PAPER_PORTFOLIO_HEAD_CHANGED")

    watermark = stable_manifest_hash(
      allocation_evidence(
        {
          "version": "paper-portfolio-cut.v1",
          "capacity": {
            code: value.obligation_watermark for code, value in capacities.items()
          },
          "execution": evidence(execution),
          "config_hash": frozen.config_snapshot_hash,
          "config_created_at": _stored_time(version.created_at),
          "control": head_material,
          "batches": [evidence(row) for row in batches],
          "plans": [evidence(row) for row in plans],
          "orders": [evidence(row) for row in orders],
          "fills": [evidence(row) for row in fills],
          "events": [evidence(row) for row in events],
          "intents": [evidence(row) for row in intents],
          "decisions": [evidence(row) for row in decisions],
          "allocations": [evidence(row) for row in allocation_batches],
          "quotes": material["market_snapshots"],
        }
      )
    )
    # The evaluation deadline validates freshness/TTL but is not source evidence.
    # Reconstruct the same cut until an actual fact or its availability changes.
    availability = [
      account.snapshot_as_of,
      account.seed_as_of,
      version.created_at,
      control.created_at,
      control.updated_at,
      execution.created_at,
      execution.entry_readiness_as_of,
      reference.industry_as_of,
      reference.industry_effective_from,
      reference.calendar_as_of,
    ]
    availability.extend(
      value
      for value in (
        execution.started_at,
        execution.drain_requested_at,
        execution.completed_at,
      )
      if value is not None
    )
    availability.extend(
      value
      for row in [*intents, *batches, *plans]
      for value in (row.created_at, row.updated_at)
    )
    availability.extend(row.created_at for row in decisions)
    availability.extend(
      value
      for row in allocation_batches
      for value in (row.created_at, row.committed_at)
      if value is not None
    )
    availability.extend(mark.as_of for mark in current_marks.values())
    availability.extend(
      aware_time(intent.intent_metadata["intent_created_at"]) for intent in intents
    )
    availability.extend(
      datetime.fromtimestamp(intent.intent_metadata["source_time_ms"] / 1000, UTC)
      for intent in intents
      if intent.intent_metadata.get("source_time_ms") is not None
    )
    availability.extend(
      plan.last_evaluated_at.replace(tzinfo=_SHANGHAI)
      if plan.last_evaluated_at.tzinfo is None
      else plan.last_evaluated_at
      for plan in plans
      if plan.last_evaluated_at is not None
    )
    evidence_as_of = max(_not_future(value, as_of) for value in availability)
    cut = PortfolioEvidenceCut(
      ExecutionOwnerRef("T_ASSISTANT_EXECUTION", execution_id),
      ExecutionEnvironment.PAPER,
      evidence_as_of,
      f"paper:{execution_id}:{account.revision}",
      account.snapshot_hash,
      _stored_time(account.snapshot_as_of),
      watermark,
      evidence_as_of,
      True,
    )
    envelopes = []
    for code in codes:
      state = account.bucket_checkpoint["instruments"].get(code, {})
      cap = capacities[code]
      position = TEnvelopePosition(
        code,
        industries[code],
        *(
          state.get(name, {}).get("total_volume", 0)
          for name in ("locked_core", "core", "swing")
        ),
        cap.available_volume,
        sum(cap.old_inventory_claim_allocation.values()),
        exposure[code],
        pending[code],
        bool(pending[code] or exposure[code]),
      )
      envelopes.append(
        build_t_trading_envelope(
          cut=cut,
          config_version=frozen.config_version_id,
          policy=reference.envelope_policy,
          position=position,
        )
      )
    aggregates = tuple(
      IndustryTExposure(
        industry,
        sum(
          (exposure[code] for code in economic_codes if industries[code] == industry),
          _ZERO,
        ),
        sum(
          (pending[code] for code in economic_codes if industries[code] == industry),
          _ZERO,
        ),
      )
      for industry in sorted({industries[code] for code in economic_codes})
    )
    reconcile = execution.status in {"RECONCILE_REQUIRED", "FAILED"} or any(
      row.status in {"RECONCILE_REQUIRED", "ERROR"} for row in [*batches, *plans]
    )
    return PortfolioTDecisionSnapshot(
      cut,
      cycle_id,
      frozen.config_version_id,
      execution.policy_version,
      execution.scorer_mode,
      reference.portfolio_policy,
      tuple(envelopes),
      aggregates,
      _money(material["state"]["cash"]),
      total_assets,
      sum(pending.values(), _ZERO),
      sum(exposure.values(), _ZERO),
      valuation.realized,
      valuation.unrealized,
      len(entry_ids),
      execution.status == "RUNNING"
      and execution.entry_readiness == "READY"
      and control.enabled is True,
      control.enabled is not True,
      reconcile,
    )
