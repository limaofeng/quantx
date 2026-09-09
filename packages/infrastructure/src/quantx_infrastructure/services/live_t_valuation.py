"""Account-wide T valuation from durable broker fills and their original owners.

This reader never samples a broker or changes a batch. Costs are explicitly
RULE_ESTIMATE from each batch's frozen policy, charged per broker order (including
replacement orders), not once per batch or repeatedly per partial fill.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, localcontext
from zoneinfo import ZoneInfo

from quantx_application.t_trade_v3.daily_t_valuation import (
  TDailyFill,
  TDailyValuation,
  TOpeningPosition,
  TValuationMark,
  value_daily_t_positions,
)
from quantx_application.t_trade_v3.portfolio_reference import aware_time
from quantx_application.t_trade_v3.portfolio_snapshot import _hash
from sqlalchemy import select

from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  OrderCorrelation,
  PendingTradeOrder,
  StrategyRuntimeEvent,
  TTradeBatch,
)
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.trade import Trade

_EXCHANGE = ZoneInfo("Asia/Shanghai")
_ZERO = Decimal(0)


def stored_time(value):
  if not isinstance(value, datetime):
    raise ValueError("LIVE_T_VALUATION_TIME_REQUIRED")
  return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def money(value):
  if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
    raise ValueError("LIVE_T_VALUATION_AMOUNT_INVALID")
  try:
    result = Decimal(str(value))
  except InvalidOperation as exc:
    raise ValueError("LIVE_T_VALUATION_AMOUNT_INVALID") from exc
  if not result.is_finite() or result < 0:
    raise ValueError("LIVE_T_VALUATION_AMOUNT_INVALID")
  return result


def broker_time(value):
  if type(value) in (int, float):
    numeric = money(value)
    return datetime.fromtimestamp(
      float(numeric / 1000 if numeric > 10_000_000_000 else numeric), UTC
    )
  if isinstance(value, str):
    try:
      value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
      raise ValueError("LIVE_T_VALUATION_BROKER_TIME_INVALID") from exc
  if not isinstance(value, datetime):
    raise ValueError("LIVE_T_VALUATION_BROKER_TIME_INVALID")
  # Native QMT wall-clock strings have exchange-local meaning.
  return (
    value.replace(tzinfo=_EXCHANGE) if value.tzinfo is None else value
  ).astimezone(UTC)


@dataclass(frozen=True)
class LiveTValuationEvidence:
  account_id: str
  as_of: datetime
  valuation: TDailyValuation
  exposure_by_instrument: tuple[tuple[str, Decimal], ...]
  open_batch_ids: tuple[str, ...]
  evidence_hash: str
  cost_basis: str = "RULE_ESTIMATE"


class LiveTValuationReader:
  def __init__(self, db):
    self.db = db

  async def read(
    self,
    *,
    account_id: str,
    as_of: datetime,
    previous_trading_day,
    opening_marks: dict[str, TValuationMark],
    current_marks: dict[str, TValuationMark],
    mark_max_age_seconds: int,
  ) -> LiveTValuationEvidence:
    as_of = aware_time(as_of).astimezone(UTC)
    if not self.db.in_transaction() or not account_id:
      raise ValueError("LIVE_T_VALUATION_TRANSACTION_REQUIRED")
    control = await self.db.get(
      AccountExecutionControl, account_id, with_for_update=True, populate_existing=True
    )
    if control is None:
      raise ValueError("LIVE_T_VALUATION_ACCOUNT_REQUIRED")

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
      .where(TTradeBatch.account_id == account_id, TTradeBatch.environment == "LIVE")
      .order_by(TTradeBatch.batch_id)
    )
    batch_map = {batch.batch_id: batch for batch in batches}
    pending = await rows(
      select(PendingTradeOrder)
      .where(
        PendingTradeOrder.account_id == account_id,
        PendingTradeOrder.batch_id.in_(batch_map),
        PendingTradeOrder.environment == "LIVE",
      )
      .order_by(PendingTradeOrder.client_order_id)
    )
    pending_map = {row.client_order_id: row for row in pending}
    correlations = await rows(
      select(OrderCorrelation)
      .where(
        OrderCorrelation.account_id == account_id,
        OrderCorrelation.batch_id.in_(batch_map),
        OrderCorrelation.environment == "LIVE",
      )
      .order_by(OrderCorrelation.id)
    )
    correlation_map = {}
    for row in correlations:
      if row.client_order_id in correlation_map:
        raise ValueError("LIVE_T_VALUATION_DUPLICATE_CORRELATION")
      correlation_map[row.client_order_id] = row
    plans = await rows(
      select(AutoExitPlanRecord)
      .where(
        AutoExitPlanRecord.account_id == account_id,
        AutoExitPlanRecord.source_id.in_(batch_map),
        AutoExitPlanRecord.environment == "LIVE",
      )
      .order_by(AutoExitPlanRecord.plan_id)
    )
    plan_map = {row.plan_id: row for row in plans}
    events = await rows(
      select(StrategyRuntimeEvent)
      .where(
        StrategyRuntimeEvent.client_order_id.in_(pending_map),
        StrategyRuntimeEvent.environment == "LIVE",
        StrategyRuntimeEvent.event_type == "TRADE",
      )
      .order_by(StrategyRuntimeEvent.event_id)
    )
    pending_broker_ids = [
      str(row.broker_order_id) for row in pending if row.broker_order_id
    ]
    if len(pending_broker_ids) != len(set(pending_broker_ids)):
      raise ValueError("LIVE_T_VALUATION_DUPLICATE_BROKER_ORDER")
    broker_ids = set(pending_broker_ids)
    for batch in batches:
      broker_ids.update(
        str(value)
        for value in (batch.entry_broker_order_id, batch.exit_broker_order_id)
        if value
      )
    if any(not identity.isdecimal() for identity in broker_ids):
      raise ValueError("LIVE_T_VALUATION_BROKER_ID_INVALID")
    trades = await rows(
      select(Trade)
      .where(
        Trade.account_id == account_id,
        Trade.order_id.in_([int(value) for value in broker_ids]),
      )
      .order_by(Trade.id)
    )
    trade_map = {row.id: row for row in trades}
    for row in [*batches, *pending, *correlations, *plans, *trades]:
      if max(stored_time(row.created_at), stored_time(row.updated_at)) > as_of:
        raise ValueError("LIVE_T_VALUATION_FUTURE_EVIDENCE")
    for batch in batches:
      if (
        batch.source_execution_environment != "LIVE"
        or batch.source_execution_owner_type
        not in {"STRATEGY_RUN", "T_ASSISTANT_EXECUTION", "MANUAL_COMMAND"}
        or not batch.source_execution_owner_id
      ):
        raise ValueError("LIVE_T_VALUATION_BATCH_SOURCE_INVALID")

    parsed, seen = [], set()
    clock_sides, order_quantities = {}, {}
    for event in events:
      order = pending_map[event.client_order_id]
      correlation = correlation_map.get(event.client_order_id)
      batch = batch_map[order.batch_id]
      report, metadata = (
        event.payload.get("report", {}),
        event.payload.get("metadata", {}),
      )
      fill_id = str(
        report.get("execution_id")
        or report.get("traded_id")
        or report.get("trade_id")
        or ""
      )
      trade = trade_map.get(fill_id)
      if event.application_status != "APPLIED" or event.applied_at is None:
        raise ValueError("LIVE_T_VALUATION_UNAPPLIED_FILL")
      if max(stored_time(event.created_at), stored_time(event.applied_at)) > as_of:
        raise ValueError("LIVE_T_VALUATION_FUTURE_EVIDENCE")
      owner = (order.owner_type, order.owner_id, order.environment)
      if (
        correlation is None
        or owner != (event.owner_type, event.owner_id, event.environment)
        or owner
        != (correlation.owner_type, correlation.owner_id, correlation.environment)
        or correlation.batch_id != order.batch_id
        or correlation.broker_order_id != order.broker_order_id
        or event.broker_order_id != order.broker_order_id
        or correlation.t_trade_role != order.t_trade_role
        or metadata.get("t_batch_id") != order.batch_id
        or str(metadata.get("t_trade_role", "")).upper() != order.t_trade_role
        or order.instrument_code != batch.instrument_code
      ):
        raise ValueError("LIVE_T_VALUATION_OWNER_CONFLICT")
      if order.t_trade_role == "ENTRY":
        if order.side != "BUY" or owner != (
          batch.source_execution_owner_type,
          batch.source_execution_owner_id,
          "LIVE",
        ):
          raise ValueError("LIVE_T_VALUATION_ENTRY_OWNER_CONFLICT")
      elif order.t_trade_role == "EXIT":
        plan = plan_map.get(order.owner_id)
        if (
          order.side != "SELL"
          or plan is None
          or order.owner_type != "EXIT_PLAN"
          or (
            plan.source_type != "T_TRADE_BATCH"
            or plan.source_id != batch.batch_id
            or plan.instrument_code != batch.instrument_code
            or (
              plan.source_execution_owner_type,
              plan.source_execution_owner_id,
              plan.source_execution_environment,
            )
            != (
              batch.source_execution_owner_type,
              batch.source_execution_owner_id,
              "LIVE",
            )
          )
        ):
          raise ValueError("LIVE_T_VALUATION_EXIT_OWNER_CONFLICT")
      else:
        raise ValueError("LIVE_T_VALUATION_ROLE_REQUIRED")
      occurred = broker_time(report.get("traded_time", report.get("trade_time")))
      volume = report.get("traded_volume", report.get("volume"))
      price = money(report.get("traded_price", report.get("price")))
      if (
        trade is None
        or not fill_id
        or fill_id in seen
        or type(volume) is not int
        or volume <= 0
        or price <= 0
        or occurred > as_of
        or stored_time(event.created_at) < occurred
        or str(trade.order_id) != str(order.broker_order_id)
        or str(report.get("order_id", report.get("broker_order_id")))
        != str(order.broker_order_id)
        or report.get("account_id") != account_id
        or trade.stock_code != order.instrument_code
        or report.get("stock_code", report.get("instrument_code"))
        != order.instrument_code
        or trade.order_type != (23 if order.side == "BUY" else 24)
        or trade.volume != volume
        or money(trade.price) != price
        or money(trade.amount) != price * volume
      ):
        raise ValueError("LIVE_T_VALUATION_FILL_CONFLICT")
      # The operational Trade table stores native broker wall-clock time.
      if broker_time(trade.time) != occurred:
        raise ValueError("LIVE_T_VALUATION_FILL_CLOCK_CONFLICT")
      clock_key = (batch.batch_id, occurred)
      if clock_key in clock_sides and clock_sides[clock_key] != order.side:
        raise ValueError("LIVE_T_VALUATION_AMBIGUOUS_FILL_ORDER")
      clock_sides[clock_key] = order.side
      order_quantities[order.client_order_id] = (
        order_quantities.get(order.client_order_id, 0) + volume
      )
      if order_quantities[order.client_order_id] > order.volume:
        raise ValueError("LIVE_T_VALUATION_ORDER_OVERFILL")
      seen.add(fill_id)
      parsed.append((occurred, event.event_id, order, batch, fill_id, volume, price))
    if seen != set(trade_map):
      raise ValueError("LIVE_T_VALUATION_FILL_LINEAGE_INCOMPLETE")
    quantities, costs, opening, exposure, totals, order_amounts, order_fees = (
      {},
      {},
      {},
      {},
      {},
      {},
      {},
    )
    daily = []
    today = as_of.astimezone(_EXCHANGE).date()
    with localcontext() as context:
      context.prec = 50
      for revision, (at, _, order, batch, fill_id, volume, price) in enumerate(
        sorted(parsed, key=lambda item: item[:2]), 1
      ):
        key, order_key = batch.batch_id, order.client_order_id
        amount = price * volume
        cumulative = order_amounts.get(order_key, _ZERO) + amount
        commission, minimum, stamp, transfer = (
          money(getattr(batch, name))
          for name in (
            "commission_rate",
            "minimum_commission",
            "stamp_tax_rate",
            "transfer_fee_rate",
          )
        )
        if type(batch.policy_version) is not int or batch.policy_version < 1:
          raise ValueError("LIVE_T_VALUATION_FROZEN_COST_REQUIRED")
        total_fee = max(minimum, cumulative * commission) + cumulative * (
          transfer + (stamp if order.side == "SELL" else _ZERO)
        )
        fee = total_fee - order_fees.get(order_key, _ZERO)
        order_amounts[order_key], order_fees[order_key] = cumulative, total_fee
        qty, cost = quantities.get(key, 0), costs.get(key, _ZERO)
        if order.side == "BUY":
          quantities[key], costs[key] = qty + volume, cost + amount + fee
        else:
          if volume > qty:
            raise ValueError("LIVE_T_VALUATION_EXIT_OVERFILL")
          quantities[key], costs[key] = (
            qty - volume,
            cost - (cost if volume == qty else cost * volume / qty),
          )
        totals[(key, order.side)] = totals.get((key, order.side), 0) + volume
        if at.astimezone(_EXCHANGE).date() < today:
          opening[key] = quantities[key]
        else:
          daily.append(
            TDailyFill(
              fill_id,
              key,
              order.instrument_code,
              order.side,
              volume,
              price,
              fee,
              at,
              revision,
              0,
            )
          )
      for batch in batches:
        if (
          totals.get((batch.batch_id, "BUY"), 0) != batch.entry_filled_volume
          or totals.get((batch.batch_id, "SELL"), 0) != batch.exit_filled_volume
        ):
          raise ValueError("LIVE_T_VALUATION_BATCH_QUANTITY_CONFLICT")
        exposure[batch.instrument_code] = exposure.get(
          batch.instrument_code, _ZERO
        ) + costs.get(batch.batch_id, _ZERO)
      for key, quantity in opening.items():
        if not quantity:
          continue
        mark = opening_marks.get(batch_map[key].instrument_code)
        if mark is not None:
          local = aware_time(mark.as_of).astimezone(_EXCHANGE)
          seconds = (
            local - local.replace(hour=15, minute=0, second=0, microsecond=0)
          ).total_seconds()
          if not 0 <= seconds <= min(5, mark_max_age_seconds):
            raise ValueError("LIVE_T_VALUATION_PRIOR_CLOSE_WINDOW_REQUIRED")
      valuation = value_daily_t_positions(
        as_of=as_of,
        previous_trading_day=previous_trading_day,
        opening_positions=tuple(
          TOpeningPosition(key, batch_map[key].instrument_code, qty)
          for key, qty in sorted(opening.items())
          if qty
        ),
        opening_marks=opening_marks,
        current_marks=current_marks,
        fills=tuple(daily),
        mark_max_age_seconds=mark_max_age_seconds,
      )
    if {key: qty for key, qty in valuation.remaining_quantities if qty} != {
      key: qty for key, qty in quantities.items() if qty
    }:
      raise ValueError("LIVE_T_VALUATION_QUANTITY_CONFLICT")
    evidence = {
      "account": account_id,
      "as_of": as_of,
      "cost_basis": "RULE_ESTIMATE",
      "rows": [
        {column.key: getattr(row, column.key) for column in row.__mapper__.column_attrs}
        for row in [*batches, *pending, *correlations, *plans, *trades, *events]
      ],
      "opening_marks": opening_marks,
      "current_marks": current_marks,
      "previous_trading_day": previous_trading_day.isoformat(),
      "mark_max_age_seconds": mark_max_age_seconds,
    }
    return LiveTValuationEvidence(
      account_id,
      as_of,
      valuation,
      tuple(sorted(exposure.items())),
      tuple(sorted(key for key, qty in quantities.items() if qty)),
      _hash(evidence),
    )
