"""Explicit account bucket baseline plus durable, original-owner fill replay.

Seed approval is an internal control-plane operation, not a public command or an
implicit migration. The baseline is stored in the existing append-only execution
event log; account-wide lookup preserves it when the source execution is replaced.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from quantx_application.t_trade_v3.live_bucket_projection import (
  LiveAttributedFill,
  LiveBucketProjection,
  replay_live_bucket_projection,
)
from quantx_application.t_trade_v3.portfolio_reference import aware_time
from quantx_application.t_trade_v3.portfolio_snapshot import _hash
from quantx_contracts.order_lifecycle import (
  TERMINAL_ORDER_STATUSES,
  normalize_order_status,
)
from quantx_domain.trading.t_assistant_execution import TAssistantExecutionEvent
from sqlalchemy import select

from quantx_infrastructure.models.agent_runtime import (
  AccountExecutionControl,
  OrderCorrelation,
  PendingTradeOrder,
  StrategyRuntimeEvent,
)
from quantx_infrastructure.models.enums import OrderStatus
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantExecutionEventRecord,
  TAssistantExecutionRecord,
)
from quantx_infrastructure.models.trade import Trade
from quantx_infrastructure.repositories.t_assistant_execution_repository import (
  TAssistantExecutionRepository,
)
from quantx_infrastructure.services.account_capacity_service import (
  load_authoritative_account_snapshot,
)
from quantx_infrastructure.services.live_t_valuation import (
  broker_time,
  money,
  stored_time,
)

_KIND = "LIVE_ACCOUNT_BUCKET_SEEDED"


@dataclass(frozen=True)
class LivePositionAttributionEvidence:
  account_id: str
  snapshot_id: str
  snapshot_hash: str
  snapshot_as_of: datetime
  seed_event_key: str
  projection: LiveBucketProjection
  evidence_hash: str


def _trade_hash(trade):
  return _hash(
    {
      key: value
      for key, value in trade.to_dict().items()
      if key not in {"created_at", "updated_at"}
    }
  )


def _positions(payload, account_id):
  result = {}
  for position in payload["positions_by_account"][account_id]:
    code = position.get("stock_code")
    if not isinstance(code, str) or not code.strip() or code in result:
      raise ValueError("LIVE_BUCKET_SNAPSHOT_SYMBOL_INVALID")
    result[code] = {
      "volume": position.get("volume"),
      "can_use_volume": position.get("can_use_volume"),
    }
  return result


class LivePositionAttributionService:
  def __init__(self, db):
    self.db = db

  async def _cut(self, account_id, as_of, max_age_seconds):
    if (
      not self.db.in_transaction()
      or type(max_age_seconds) is not int
      or not 0 < max_age_seconds <= 90
    ):
      raise ValueError("LIVE_BUCKET_EXPLICIT_TRANSACTION_REQUIRED")
    as_of = aware_time(as_of).astimezone(UTC)
    control = await self.db.get(
      AccountExecutionControl, account_id, with_for_update=True, populate_existing=True
    )
    if control is None:
      raise ValueError("LIVE_BUCKET_ACCOUNT_REQUIRED")
    cut = stored_time(control.last_snapshot_at)
    if not 0 <= (as_of - cut).total_seconds() < max_age_seconds:
      raise ValueError("LIVE_BUCKET_SNAPSHOT_STALE_OR_FUTURE")
    payload = await load_authoritative_account_snapshot(self.db, control)
    if broker_time(payload.get("source_event_at")) != cut:
      raise ValueError("LIVE_BUCKET_SNAPSHOT_CLOCK_CONFLICT")
    return control, payload, cut

  async def approve_seed(
    self,
    *,
    execution_id,
    actor_id,
    instruments,
    expected_snapshot_id,
    expected_snapshot_hash,
    as_of,
    max_age_seconds,
    expected_seed_event_key=None,
  ):
    if not isinstance(actor_id, str) or not actor_id.strip():
      raise ValueError("LIVE_BUCKET_EXPLICIT_ACTOR_REQUIRED")
    if not self.db.in_transaction():
      raise ValueError("LIVE_BUCKET_EXPLICIT_TRANSACTION_REQUIRED")
    async with self.db.begin_nested():
      execution = await self.db.get(TAssistantExecutionRecord, execution_id)
      if execution is None or execution.environment != "LIVE":
        raise ValueError("LIVE_BUCKET_SOURCE_REQUIRED")
      control, payload, cut = await self._cut(
        execution.account_id, as_of, max_age_seconds
      )
      if (control.last_snapshot_id, control.last_snapshot_hash) != (
        expected_snapshot_id,
        expected_snapshot_hash,
      ):
        raise ValueError("LIVE_BUCKET_SNAPSHOT_CHANGED")
      existing = await self._seed(execution.account_id)
      # Reconciliation appends a successor baseline under exact predecessor CAS.
      if existing is not None:
        details = existing.payload
        if (
          details.get("snapshot_hash") == expected_snapshot_hash
          and details.get("instruments") == instruments
          and details.get("actor_id") == actor_id
        ):
          return existing.event_key
        if expected_seed_event_key != existing.event_key:
          raise ValueError("LIVE_BUCKET_SEED_PREDECESSOR_REQUIRED")
      elif expected_seed_event_key is not None:
        raise ValueError("LIVE_BUCKET_SEED_PREDECESSOR_CHANGED")
      active = await self.db.scalar(
        select(PendingTradeOrder.client_order_id)
        .where(
          PendingTradeOrder.account_id == execution.account_id,
          PendingTradeOrder.environment == "LIVE",
          PendingTradeOrder.status.notin_(TERMINAL_ORDER_STATUSES),
        )
        .limit(1)
      )
      if active:
        raise ValueError("LIVE_BUCKET_SEED_UNSETTLED_ORDER")
      for order in payload.get("orders", []):
        if order.get("account_id") != execution.account_id:
          continue
        status = order.get("order_status", order.get("status"))
        if type(status) is int:
          try:
            status = OrderStatus(status).name
          except ValueError:
            status = "UNKNOWN"
        if normalize_order_status(status) not in TERMINAL_ORDER_STATUSES:
          raise ValueError("LIVE_BUCKET_SEED_UNSETTLED_ORDER")
      projection = replay_live_bucket_projection(
        seed=instruments,
        seed_as_of=cut,
        as_of=cut,
        fills=(),
        broker_positions=_positions(payload, execution.account_id),
      )
      if any(
        values["available_volume"]
        != values["total_volume"] - values["today_buy_volume"]
        for buckets in projection.instruments.values()
        for values in buckets.values()
      ):
        raise ValueError("LIVE_BUCKET_SEED_FROZEN_POSITION")
      trades = list(
        (
          await self.db.scalars(
            select(Trade)
            .where(Trade.account_id == execution.account_id)
            .order_by(Trade.id)
          )
        ).all()
      )
      if any(
        broker_time(trade.time) > cut
        or stored_time(trade.created_at) > aware_time(as_of)
        for trade in trades
      ):
        raise ValueError("LIVE_BUCKET_SEED_UNCOVERED_FILL")
      details = dict(
        revision=existing.payload["revision"] + 1 if existing else 1,
        previous_seed_hash=existing.payload["seed_hash"] if existing else None,
        previous_seed_event_key=existing.event_key if existing else None,
        account_id=execution.account_id,
        snapshot_id=expected_snapshot_id,
        snapshot_hash=expected_snapshot_hash,
        snapshot_as_of=cut.isoformat(),
        actor_id=actor_id,
        instruments=instruments,
        baseline_trades={trade.id: _trade_hash(trade) for trade in trades},
      )
      details["seed_hash"] = _hash(details)
      key = "live-bucket-seed:" + details["seed_hash"]
      await TAssistantExecutionRepository(self.db).append_event(
        TAssistantExecutionEvent(execution_id, key, _KIND, aware_time(as_of), details)
      )
      return key

  async def _seed(self, account_id):
    records = list(
      (
        await self.db.scalars(
          select(TAssistantExecutionEventRecord)
          .join(
            TAssistantExecutionRecord,
            TAssistantExecutionRecord.execution_id
            == TAssistantExecutionEventRecord.execution_id,
          )
          .where(
            TAssistantExecutionRecord.account_id == account_id,
            TAssistantExecutionRecord.environment == "LIVE",
            TAssistantExecutionEventRecord.event_type == _KIND,
          )
        )
      ).all()
    )
    previous = None
    for revision, record in enumerate(
      sorted(records, key=lambda row: row.payload.get("revision", 0)), 1
    ):
      material = dict(record.payload)
      digest = material.pop("seed_hash", None)
      if (
        type(material.get("revision")) is not int
        or material["revision"] != revision
        or material.get("account_id") != account_id
        or digest != _hash(material)
        or record.event_key != "live-bucket-seed:" + str(digest)
        or material.get("previous_seed_event_key")
        != (previous.event_key if previous else None)
        or material.get("previous_seed_hash")
        != (previous.payload["seed_hash"] if previous else None)
        or (
          previous
          and stored_time(record.occurred_at) < stored_time(previous.occurred_at)
        )
      ):
        raise ValueError("LIVE_BUCKET_SEED_CHAIN_CONFLICT")
      previous = record
    return previous

  async def read(self, *, account_id, as_of, max_age_seconds):
    control, payload, cut = await self._cut(account_id, as_of, max_age_seconds)
    seed = await self._seed(account_id)
    if seed is None:
      raise ValueError("LIVE_BUCKET_APPROVED_SEED_REQUIRED")
    details = dict(seed.payload)
    seed_hash = details.pop("seed_hash", None)
    if (
      seed_hash != _hash(details)
      or details.get("account_id") != account_id
      or stored_time(seed.occurred_at) > aware_time(as_of)
    ):
      raise ValueError("LIVE_BUCKET_SEED_EVIDENCE_CONFLICT")
    seed_at = aware_time(details["snapshot_as_of"])
    trades = list(
      (
        await self.db.scalars(
          select(Trade).where(Trade.account_id == account_id).order_by(Trade.id)
        )
      ).all()
    )
    baseline = details["baseline_trades"]
    if not set(baseline).issubset({trade.id for trade in trades}):
      raise ValueError("LIVE_BUCKET_BASELINE_TRADE_MISSING")

    async def rows(statement):
      return list(
        (
          await self.db.scalars(
            statement.with_for_update().execution_options(populate_existing=True)
          )
        ).all()
      )

    broker_ids = {str(trade.order_id) for trade in trades if trade.id not in baseline}
    correlations = await rows(
      select(OrderCorrelation)
      .where(
        OrderCorrelation.account_id == account_id,
        OrderCorrelation.environment == "LIVE",
        OrderCorrelation.broker_order_id.in_(broker_ids),
      )
      .order_by(OrderCorrelation.id)
    )
    by_broker = {}
    for correlation in correlations:
      by_broker.setdefault(correlation.broker_order_id, []).append(correlation)
    clients = {row.client_order_id for row in correlations}
    pending_rows = await rows(
      select(PendingTradeOrder)
      .where(PendingTradeOrder.client_order_id.in_(clients))
      .order_by(PendingTradeOrder.client_order_id)
    )
    pending_map = {row.client_order_id: row for row in pending_rows}
    events = await rows(
      select(StrategyRuntimeEvent)
      .where(
        StrategyRuntimeEvent.client_order_id.in_(clients),
        StrategyRuntimeEvent.environment == "LIVE",
        StrategyRuntimeEvent.event_type == "TRADE",
      )
      .order_by(StrategyRuntimeEvent.event_id)
    )
    by_fill = {}
    for event in events:
      report = event.payload.get("report", {})
      identity = str(
        report.get("execution_id")
        or report.get("traded_id")
        or report.get("trade_id")
        or ""
      )
      by_fill.setdefault((event.client_order_id, identity), []).append(event)
    for row in [*correlations, *pending_rows]:
      if max(stored_time(row.created_at), stored_time(row.updated_at)) > aware_time(
        as_of
      ):
        raise ValueError("LIVE_BUCKET_FUTURE_LINEAGE")
    order_quantities = {}
    for trade in trades:
      order_quantities[str(trade.order_id)] = (
        order_quantities.get(str(trade.order_id), 0) + trade.volume
      )
    fills, lineage = [], []
    for trade in trades:
      if trade.id in baseline:
        if _trade_hash(trade) != baseline[trade.id]:
          raise ValueError("LIVE_BUCKET_BASELINE_TRADE_CHANGED")
        continue
      if (
        max(stored_time(trade.created_at), stored_time(trade.updated_at))
        > aware_time(as_of)
        or broker_time(trade.time) > cut
      ):
        raise ValueError("LIVE_BUCKET_SNAPSHOT_UNCOVERED_FILL")
      matched_correlations = by_broker.get(str(trade.order_id), [])
      if len(matched_correlations) != 1:
        raise ValueError("LIVE_BUCKET_FILL_ATTRIBUTION_REQUIRED")
      correlation = matched_correlations[0]
      pending = pending_map.get(correlation.client_order_id)
      matched = by_fill.get((correlation.client_order_id, trade.id), [])
      if len(matched) != 1 or pending is None:
        raise ValueError("LIVE_BUCKET_FILL_EVENT_REQUIRED")
      event = matched[0]
      if (
        event.application_status != "APPLIED"
        or event.applied_at is None
        or max(stored_time(event.created_at), stored_time(event.applied_at))
        > aware_time(as_of)
      ):
        raise ValueError("LIVE_BUCKET_FILL_NOT_CONVERGED")
      report, metadata = event.payload["report"], event.payload.get("metadata", {})
      owner = (correlation.owner_type, correlation.owner_id, "LIVE")
      if (
        owner != (pending.owner_type, pending.owner_id, pending.environment)
        or owner != (event.owner_type, event.owner_id, event.environment)
        or pending.account_id != account_id
        or trade.order_type != (23 if pending.side == "BUY" else 24)
        or order_quantities[str(trade.order_id)] > pending.volume
        or pending.broker_order_id != str(trade.order_id)
        or event.broker_order_id != str(trade.order_id)
        or pending.instrument_code != trade.stock_code
        or pending.bucket != correlation.bucket
        or metadata.get("bucket") != correlation.bucket
        or pending.substitution_plan != correlation.substitution_plan
        or metadata.get("substitution_plan") != correlation.substitution_plan
        or report.get("account_id") != account_id
        or str(report.get("order_id", report.get("broker_order_id")))
        != str(trade.order_id)
        or report.get("stock_code", report.get("instrument_code")) != trade.stock_code
        or report.get("traded_volume", report.get("volume")) != trade.volume
        or money(report.get("traded_price", report.get("price"))) != money(trade.price)
        or broker_time(report.get("traded_time", report.get("trade_time")))
        != broker_time(trade.time)
      ):
        raise ValueError("LIVE_BUCKET_FILL_LINEAGE_CONFLICT")
      fills.append(
        LiveAttributedFill(
          trade.id,
          pending.client_order_id,
          trade.stock_code,
          pending.side,
          trade.volume,
          money(trade.price),
          broker_time(trade.time),
          correlation.bucket,
          correlation.substitution_plan,
        )
      )
      lineage.append(dict(event_id=event.event_id, payload=event.payload, owner=owner))
    projection = replay_live_bucket_projection(
      seed=details["instruments"],
      seed_as_of=seed_at,
      as_of=cut,
      fills=tuple(fills),
      broker_positions=_positions(payload, account_id),
    )
    return LivePositionAttributionEvidence(
      account_id,
      control.last_snapshot_id,
      control.last_snapshot_hash,
      cut,
      seed.event_key,
      projection,
      _hash(
        dict(
          seed_hash=seed_hash,
          snapshot=control.last_snapshot_hash,
          projection=projection.source_hash,
          lineage=lineage,
        )
      ),
    )
