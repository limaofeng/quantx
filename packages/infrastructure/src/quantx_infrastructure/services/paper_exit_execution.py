"""Route public T ExitPlan intents into the isolated, durable PAPER ledger."""

from dataclasses import fields
from datetime import datetime, timedelta
from decimal import Decimal

from quantx_application.t_trade_v3.portfolio_snapshot import TTradingEnvelopePolicy
from quantx_contracts import ExecutionEnvironment, ExecutionOwnerRef
from quantx_domain.brokers.base import OrderRequest, OrderType, PriceType
from quantx_domain.strategies.base import ExitPlanIntentOrigin, TradeIntent
from quantx_domain.trading import MarketDataSnapshot
from quantx_domain.trading.exit_plan import ExitPlan, ExitPlanBook
from quantx_domain.trading.market_rules import AShareMarketRules
from quantx_domain.trading.order_sizer import OrderSizer
from quantx_domain.trading.risk_checker import TradingRiskChecker
from quantx_domain.trading.t_assistant_execution import (
  TAssistantConfigVersion,
  stable_manifest_hash,
)
from quantx_domain.trading.t_order_policy import TExitOrderPolicy
from sqlalchemy import select

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.paper_execution import (
  PaperExecutionEventRecord,
  PaperExecutionOrderRecord,
)
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantConfigVersionRecord,
  TAssistantExecutionRecord,
)
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.account_capacity_service import (
  AccountCapacityService,
)
from quantx_infrastructure.services.exit_plan_execution_owner import (
  durable_exit_plan_source_binding,
)
from quantx_infrastructure.services.exit_plan_scope_lock import (
  lock_exit_plan_scope_for_plan,
)
from quantx_infrastructure.services.paper_broker_matching import PaperBrokerMatching
from quantx_infrastructure.services.paper_execution_ledger import (
  PaperExecutionLedger,
  _quote_event_clock,
  _stored_time,
)


def is_t_paper_exit(record):
  return (
    record.environment == "PAPER"
    and record.source_execution_owner_type == "T_ASSISTANT_EXECUTION"
  )


def _source(record):
  binding = durable_exit_plan_source_binding(record)
  if (
    not is_t_paper_exit(record)
    or binding is None
    or binding[1] is not ExecutionEnvironment.PAPER
    or binding[0].owner_type.value != "T_ASSISTANT_EXECUTION"
  ):
    raise ValueError("PAPER_EXIT_SOURCE_CONFLICT")
  return binding[0]


async def _intent_and_order(db, record, intent_id):
  source = _source(record)
  intent = await db.get(TradeIntentRecord, intent_id, populate_existing=True)
  if (
    intent is None
    or intent.owner_type != "EXIT_PLAN"
    or intent.owner_id != record.plan_id
    or intent.environment != "PAPER"
    or intent.account_id != record.account_id
    or intent.instrument_code != record.instrument_code
    or intent.direction != "SELL"
    or intent.bucket != record.bucket
    or intent.strategy_run_id is not None
    or intent.intent_metadata.get("t_batch_id") != record.source_id
  ):
    raise ValueError("PAPER_EXIT_INTENT_CONFLICT")
  orders = list(
    (
      await db.scalars(
        select(PaperExecutionOrderRecord)
        .where(
          PaperExecutionOrderRecord.intent_id == intent_id,
        )
        .execution_options(populate_existing=True)
      )
    ).all()
  )
  if len(orders) > 1:
    raise ValueError("PAPER_EXIT_ORDER_CONFLICT")
  order = orders[0] if orders else None
  if order is not None and (
    order.execution_id != source.owner_id
    or order.environment != "PAPER"
    or order.owner_type != "EXIT_PLAN"
    or order.owner_id != record.plan_id
    or order.instrument_code != record.instrument_code
    or order.side != "SELL"
    or intent.order_id != order.order_id
    or intent.executed_volume != order.filled_volume
    or intent.status
    != ("ROUTED" if order.status in {"PENDING", "SUBMITTED"} else order.status)
  ):
    raise ValueError("PAPER_EXIT_ORDER_CONFLICT")
  return intent, order


def _accepted(order, *, duplicate):
  return {
    "success": True,
    "intent_id": order.intent_id,
    "order_id": order.order_id,
    "client_order_id": order.order_id,
    "volume": order.volume,
    "status": order.status,
    "duplicate": duplicate,
  }


async def recover_paper_exit(db, record, plan):
  """Atomic ledger receipts already own plan projection; never infer a fill."""
  intent, order = await _intent_and_order(db, record, plan.pending_intent_id)
  if order is not None:
    if plan.pending_order_id != order.order_id:
      raise ValueError("PAPER_EXIT_RECEIPT_PROJECTION_CONFLICT")
    return True
  if plan.pending_order_id:
    raise ValueError("PAPER_EXIT_ORDER_REQUIRED")
  if intent.status == "RESERVED":
    return False
  raise ValueError("PAPER_EXIT_RECEIPT_REQUIRED")


async def release_paper_exit(db, record, plan, *, intent_id, reason):
  intent, order = await _intent_and_order(db, record, intent_id)
  if order is not None:
    # A successful ledger transaction includes its receipt. An uncertain caller
    # must not turn that durable acceptance into a fabricated zero-fill outcome.
    return False
  if plan.pending_intent_id != intent_id or intent.status != "RESERVED":
    raise ValueError("PAPER_EXIT_RESERVATION_CONFLICT")
  from quantx_infrastructure.services.trade_intent_processor import (
    local_pre_broker_zero_fill_metadata,
  )

  intent.status = "REJECTED"
  intent.notes = reason[:2000]
  intent.intent_metadata = local_pre_broker_zero_fill_metadata(
    intent.intent_metadata,
    reason=reason,
  )
  ExitPlanBook([plan]).apply_order_event(
    plan_id=record.plan_id,
    intent_id=intent_id,
    status="RECONCILED_ZERO_FILL",
  )
  return True


async def read_paper_exit_market(
  db, *, execution_id, instrument_code, now
) -> MarketDataSnapshot:
  """Read full quote evidence; caller acquires the execution/account scope."""
  from quantx_infrastructure.services.auto_exit_plan_service import (
    MARKET_DATA_CONTEXT_STALE_SECONDS,
  )
  from quantx_infrastructure.services.paper_receipt_convergence import (
    PaperReceiptConvergence,
  )

  now = time_utils.to_utc(now)
  snapshot = await PaperExecutionLedger(
    db, receipt_sink=PaperReceiptConvergence()
  ).get_snapshot(execution_id=execution_id)
  if snapshot["as_of"] > now:
    raise ValueError("PAPER_EXIT_MARKET_TIME_INVALID")
  matcher = PaperBrokerMatching.restore(
    scope_execution_id=execution_id, checkpoint=snapshot["broker_checkpoint"]
  )
  market = matcher._broker.market_snapshots.get(instrument_code)
  if market is None:
    raise ValueError("PAPER_EXIT_MARKET_REQUIRED")
  material = snapshot["broker_checkpoint"]["material"]
  event_id = material["latest_quote_events"][instrument_code]["event_id"]
  event = await db.get(PaperExecutionEventRecord, event_id, populate_existing=True)

  def quote_material(value):
    value = dict(value or {})
    value["timestamp"] = time_utils.to_utc(
      datetime.fromisoformat(value["timestamp"])
    ).isoformat()
    return value

  if (
    event is None
    or event.execution_id != execution_id
    or event.environment != "PAPER"
    or event.event_type != "QUOTE"
    or not 1 <= event.revision <= snapshot["revision"]
    or _quote_event_clock(event)[0] != market.timestamp
    or _stored_time(event.occurred_at) > now
    or quote_material(event.input_payload.get("quote"))
    != quote_material(material["market_snapshots"][instrument_code])
    or event.input_hash
    != stable_manifest_hash({"event_type": "QUOTE", "input": event.input_payload})
  ):
    raise ValueError("PAPER_EXIT_MARKET_EVIDENCE_CONFLICT")
  age = (now - market.timestamp).total_seconds()
  if not 0 <= age <= MARKET_DATA_CONTEXT_STALE_SECONDS:
    raise ValueError("PAPER_EXIT_MARKET_TIME_INVALID")
  return market


async def execute_paper_exit(
  db, *, plan_id, intent_id, now: datetime, market_ready=None
):
  """Caller owns the transaction; scope, legal sizing, ledger and sink share it."""
  from quantx_infrastructure.services.paper_receipt_convergence import (
    PaperReceiptConvergence,
  )

  now = time_utils.to_utc(now)
  scope = await lock_exit_plan_scope_for_plan(db, plan_id)
  record = scope.plan(plan_id)
  if record is None:
    raise ValueError("PAPER_EXIT_PLAN_REQUIRED")
  source = _source(record)
  intent, existing = await _intent_and_order(db, record, intent_id)
  if existing is not None:
    return _accepted(existing, duplicate=True)
  plan = ExitPlan.from_dict(record.plan_state)
  created = datetime.fromisoformat(intent.intent_metadata["intent_created_at"])
  if created.tzinfo is None or time_utils.to_utc(created) > now:
    raise ValueError("PAPER_EXIT_INTENT_TIME_INVALID")
  if (
    not record.enabled
    or plan.pending_intent_id != intent_id
    or plan.pending_order_id
    or intent.status != "RESERVED"
    or plan.pending_requested_volume != intent.target_volume
    or intent.target_volume > plan.remaining_volume
  ):
    raise ValueError("PAPER_EXIT_RESERVATION_CONFLICT")
  if market_ready is not None and not market_ready():
    raise ValueError("MARKET_DATA_STREAM_NOT_READY")
  ledger = PaperExecutionLedger(db, receipt_sink=PaperReceiptConvergence())
  snapshot = await ledger.get_snapshot(execution_id=source.owner_id)
  market = await read_paper_exit_market(
    db, execution_id=source.owner_id, instrument_code=record.instrument_code, now=now
  )
  execution = await db.get(
    TAssistantExecutionRecord, source.owner_id, populate_existing=True
  )
  config = await db.get(
    TAssistantConfigVersionRecord, execution.config_version_id, populate_existing=True
  )
  if config is None or _stored_time(config.created_at) > now:
    raise ValueError("PAPER_EXIT_FROZEN_CONFIG_REQUIRED")
  frozen = TAssistantConfigVersion(
    **{
      field.name: getattr(config, field.name)
      for field in fields(TAssistantConfigVersion)
    }
  )
  if (
    frozen.config_snapshot_hash != execution.config_snapshot_hash
    or frozen.config_id != execution.config_id
    or frozen.version != execution.frozen_config_version
    or frozen.policy_version != execution.policy_version
  ):
    raise ValueError("PAPER_EXIT_FROZEN_CONFIG_CONFLICT")
  raw_policy = frozen.canonical_payload["t_trading_envelope_policy"]
  envelope_policy = TTradingEnvelopePolicy(
    raw_policy["version"],
    raw_policy["protected_core_volume"],
    Decimal(str(raw_policy["max_symbol_t_amount"])),
    raw_policy["max_entry_volume"],
  )
  capacity = await AccountCapacityService(db).read(
    instrument_code=record.instrument_code,
    environment=ExecutionEnvironment.PAPER,
    paper_execution_id=source.owner_id,
    account_id=record.account_id,
    expected_snapshot_id=snapshot["snapshot_id"],
    expected_snapshot_hash=snapshot["snapshot_hash"],
    own_plan_id=record.plan_id,
    own_batch_id=record.source_id,
    own_intent_id=intent_id,
    protected_core_floor=envelope_policy.protected_core_volume,
    allow_core_claim=True,
  )
  raw = snapshot["broker_checkpoint"]["material"]["positions"].get(
    record.instrument_code, {}
  )
  position = {
    **raw,
    "available_volume": raw.get("available_volume", 0),
    **{
      f"{bucket}_available_volume": volume
      for bucket, volume in capacity.unclaimed_by_bucket.items()
    },
  }
  account = {
    "cash": float(capacity.available_cash),
    "available_cash": float(capacity.available_cash),
  }
  policy = TExitOrderPolicy()
  price = float(
    policy.protected_limit_price(
      reference_price=market.bid_price[0] if market.bid_price else market.price,
      price_tick=market.price_tick,
      limit_down=market.limit_down,
    )
  )
  typed = TradeIntent(
    intent_id=intent_id,
    strategy_id="",
    run_id="",
    execution_ref=ExecutionOwnerRef("EXIT_PLAN", plan_id),
    origin=ExitPlanIntentOrigin(plan_id=plan_id, source_execution_ref=source),
    instrument_code=record.instrument_code,
    direction="SELL",
    bucket=record.bucket,
    reason=intent.reason,
    priority=intent.priority,
    created_at=created,
    trace_id=intent.trace_id,
    target_volume=intent.target_volume,
    limit_price_hint=price,
    metadata=dict(intent.intent_metadata),
  )
  rules = AShareMarketRules()
  draft = OrderSizer(rules).draft_intent(
    typed,
    OrderType.SELL,
    price,
    account,
    position,
    sell_volume_cap=capacity.unclaimed_by_bucket.get(record.bucket, 0),
  )
  if draft.sized_volume <= 0:
    raise ValueError("PAPER_EXIT_ZERO_SIZED_VOLUME")
  request = OrderRequest(
    instrument_code=record.instrument_code,
    order_type=OrderType.SELL,
    price_type=PriceType.LIMIT,
    volume=draft.sized_volume,
    price=price,
    execution_ref=typed.execution_ref,
    environment=ExecutionEnvironment.PAPER,
    metadata={
      **intent.intent_metadata,
      "intent_id": intent_id,
      "bucket": record.bucket,
      "order_expire_at_ms": int(
        (now + timedelta(seconds=policy.order_ttl_seconds)).timestamp() * 1000
      ),
      "capacity_obligation_watermark": capacity.obligation_watermark,
      "paper_exit_envelope_policy_version": envelope_policy.version,
      "protected_core_floor": envelope_policy.protected_core_volume,
    },
  )
  risk = await TradingRiskChecker(
    rules,
    strict_market_data=True,
    strict_limit_data=True,
    enforce_trading_hours=True,
  ).evaluate_order(
    request,
    account=account,
    position=position,
    market_data=market,
    current_time=time_utils.to_shanghai(now),
    risk_caps={"allow_sell": True},
  )
  if not risk.allowed:
    raise ValueError(f"PAPER_EXIT_RISK_{risk.reason_code}")
  request.volume = risk.final_volume
  intent.status = "EXECUTION_READY"
  await db.flush()
  receipt = await ledger.place_order(
    execution_id=source.owner_id,
    event_key=f"exit:{plan_id}:{intent_id}",
    order_id=f"paper-exit:{intent_id}",
    intent_id=intent_id,
    order_attempt=0,
    request=request,
    sizing_evidence=draft,
    risk_evidence=risk,
    expected_revision=snapshot["revision"],
    expected_snapshot_hash=snapshot["snapshot_hash"],
    now=now,
  )
  order = await db.get(
    PaperExecutionOrderRecord, f"paper-exit:{intent_id}", populate_existing=True
  )
  return _accepted(order, duplicate=receipt.duplicate)
