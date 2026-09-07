"""Transactional PAPER facts around the checkpointable domain Broker adapter."""

from __future__ import annotations

import copy
import inspect
import math
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from quantx_contracts import ExecutionEnvironment
from quantx_domain.brokers.base import OrderRequest, OrderStatus, OrderType, Position
from quantx_domain.trading.bucket_ledger import KNOWN_BUCKETS, BucketLedger
from quantx_domain.trading.exit_plan import estimate_buy_fee_cny
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_domain.trading.order_sizer import OrderDraft
from quantx_domain.trading.risk_checker import OrderRiskDecision, RiskAction
from quantx_domain.trading.t_assistant_execution import stable_manifest_hash
from sqlalchemy import select

from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.paper_execution import (
  PaperExecutionAccountRecord,
  PaperExecutionEventRecord,
  PaperExecutionFillRecord,
  PaperExecutionOrderRecord,
)
from quantx_infrastructure.models.risk_increase_admission import (
  AccountRiskIncreaseAdmissionBatch,
  AccountRiskIncreaseAdmissionItem,
)
from quantx_infrastructure.models.t_allocation import (
  TAllocationBatchRecord,
  TAllocationDecisionRecord,
)
from quantx_infrastructure.models.t_assistant_execution import TAssistantExecutionRecord
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.paper_broker_matching import (
  PAPER_MATCHING_POLICY_VERSION,
  PaperBrokerMatching,
  _json,
  _time,
)

_TERMINAL = {
  OrderStatus.FILLED,
  OrderStatus.REJECTED,
  OrderStatus.CANCELLED,
  OrderStatus.EXPIRED,
}


def _utc(value):
  return _time(value).astimezone(UTC)


def _stored_time(value):
  return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _bucket_dump(ledger):
  values = ledger.to_dict()
  values.pop("run_id")
  values.pop("generated_at")
  return values


def _bucket_load(values):
  if not isinstance(values, dict):
    raise ValueError("PAPER_BUCKET_CHECKPOINT_REQUIRED")
  if values.get("run_id"):
    raise ValueError("PAPER_BUCKET_RUN_ID_FORBIDDEN")
  for states in values.get("instruments", {}).values():
    for name, state in states.items():
      if name not in KNOWN_BUCKETS:
        raise ValueError("PAPER_BUCKET_INVALID")
      for field in (
        "total_volume",
        "available_volume",
        "today_buy_volume",
        "frozen_volume",
      ):
        value = state.get(field, 0)
        if type(value) is not int or value < 0:
          raise ValueError("PAPER_BUCKET_VOLUME_INVALID")
      if sum(
        state.get(field, 0)
        for field in ("available_volume", "today_buy_volume", "frozen_volume")
      ) > state.get("total_volume", 0):
        raise ValueError("PAPER_BUCKET_VOLUME_INVALID")
  return BucketLedger.from_dict({**copy.deepcopy(values), "run_id": ""})


def _snapshot_hash(execution_id, revision, broker, buckets, as_of):
  return stable_manifest_hash(
    {
      "execution_id": execution_id,
      "revision": revision,
      "broker_checkpoint": broker,
      "bucket_checkpoint": buckets,
      "as_of": _utc(as_of).isoformat(),
    }
  )


@dataclass(frozen=True)
class PaperLedgerReceipt:
  event_id: str
  duplicate: bool
  result_payload: dict[str, Any]


class PaperExecutionLedger:
  """Every operation is a savepoint; the caller owns the outer commit.

  receipt_sink must be awaited in this same transaction and implement the
  existing public receipt convergence. This class never substitutes a noop.
  """

  def __init__(self, db, *, receipt_sink):
    if not (
      inspect.iscoroutinefunction(receipt_sink)
      or inspect.iscoroutinefunction(getattr(receipt_sink, "__call__", None))
    ):
      raise TypeError("PAPER_ASYNC_RECEIPT_SINK_REQUIRED")
    self.db, self.receipt_sink = db, receipt_sink

  async def _execution(self, execution_id, *, lock=False):
    query = select(TAssistantExecutionRecord).where(
      TAssistantExecutionRecord.execution_id == execution_id
    )
    if lock:
      query = query.with_for_update()
    execution = await self.db.scalar(query.execution_options(populate_existing=True))
    if execution is None or execution.environment != "PAPER":
      raise ValueError("PAPER_EXECUTION_SCOPE_INVALID")
    return execution

  async def _account(self, execution_id, *, lock=False):
    execution = await self._execution(execution_id, lock=lock)
    query = select(PaperExecutionAccountRecord).where(
      PaperExecutionAccountRecord.execution_id == execution_id
    )
    if lock:
      query = query.with_for_update()
    account = await self.db.scalar(query.execution_options(populate_existing=True))
    if (
      account is None
      or account.environment != "PAPER"
      or account.account_id != execution.account_id
    ):
      raise ValueError("PAPER_ACCOUNT_SCOPE_INVALID")
    if (
      account.matching_policy_version != PAPER_MATCHING_POLICY_VERSION
      or _snapshot_hash(
        execution_id,
        account.revision,
        account.broker_checkpoint,
        account.bucket_checkpoint,
        _stored_time(account.snapshot_as_of),
      )
      != account.snapshot_hash
    ):
      raise ValueError("PAPER_ACCOUNT_SNAPSHOT_CORRUPT")
    return account

  async def initialize(
    self,
    *,
    execution_id: str,
    account_id: str,
    cash: float,
    non_trading_asset_value: float,
    positions: dict[str, Position],
    bucket_checkpoint: dict,
    seed_as_of: datetime,
    seed_snapshot_id: str,
    seed_snapshot_hash: str,
  ):
    now = _utc(seed_as_of)
    if not seed_snapshot_id or len(seed_snapshot_hash) != 64:
      raise ValueError("PAPER_SEED_IDENTITY_REQUIRED")
    matcher = PaperBrokerMatching(
      scope_execution_id=execution_id,
      cash=cash,
      non_trading_asset_value=non_trading_asset_value,
      positions=positions,
      now=now,
    )
    buckets = _bucket_load(bucket_checkpoint)
    bucket_values = _bucket_dump(buckets)
    if bucket_values["pending_orders"] or bucket_values["pending_substitutions"]:
      raise ValueError("PAPER_SEED_PENDING_ORDERS_FORBIDDEN")
    bucket_values["last_settlement_date"] = _time(now).date().isoformat()
    buckets = _bucket_load(bucket_values)
    self._conserve(buckets, positions)
    broker_values = matcher.export_checkpoint()
    seed = {
      "cash": cash,
      "non_trading_asset_value": non_trading_asset_value,
      "positions": _json(positions),
      "bucket_checkpoint": bucket_values,
      "as_of": now.isoformat(),
      "source_id": seed_snapshot_id,
      "source_hash": seed_snapshot_hash,
    }
    async with self.db.begin_nested():
      execution = await self._execution(execution_id, lock=True)
      if execution.account_id != account_id:
        raise ValueError("PAPER_ACCOUNT_SCOPE_INVALID")
      existing = await self.db.get(
        PaperExecutionAccountRecord, execution_id, populate_existing=True
      )
      if existing is not None:
        if existing.account_id != account_id or existing.seed_payload != seed:
          raise ValueError("PAPER_SEED_IDEMPOTENCY_CONFLICT")
        return existing
      fingerprint = _snapshot_hash(execution_id, 0, broker_values, bucket_values, now)
      row = PaperExecutionAccountRecord(
        execution_id=execution_id,
        account_id=account_id,
        environment="PAPER",
        seed_snapshot_id=seed_snapshot_id,
        seed_snapshot_hash=seed_snapshot_hash,
        seed_as_of=now,
        seed_payload=seed,
        matching_policy_version=PAPER_MATCHING_POLICY_VERSION,
        broker_checkpoint=broker_values,
        bucket_checkpoint=bucket_values,
        revision=0,
        snapshot_hash=fingerprint,
        initial_snapshot_hash=fingerprint,
        snapshot_as_of=now,
      )
      self.db.add(row)
      await self.db.flush()
      return row

  async def get_snapshot(self, *, execution_id: str):
    account = await self._account(execution_id)
    return {
      "execution_id": execution_id,
      "account_id": account.account_id,
      "environment": "PAPER",
      "snapshot_id": f"paper:{execution_id}:{account.revision}",
      "snapshot_hash": account.snapshot_hash,
      "revision": account.revision,
      "as_of": _stored_time(account.snapshot_as_of),
      "broker_checkpoint": copy.deepcopy(account.broker_checkpoint),
      "bucket_checkpoint": copy.deepcopy(account.bucket_checkpoint),
    }

  async def place_order(
    self,
    *,
    execution_id: str,
    event_key: str,
    order_id: str,
    intent_id: str,
    order_attempt: int,
    request: OrderRequest,
    sizing_evidence: OrderDraft,
    risk_evidence: OrderRiskDecision,
    expected_revision: int,
    expected_snapshot_hash: str,
    now: datetime,
  ):
    self._review(request, intent_id, sizing_evidence, risk_evidence)
    if (
      type(order_attempt) is not int
      or order_attempt < 0
      or type(expected_revision) is not int
      or expected_revision < 0
    ):
      raise ValueError("PAPER_ORDER_REVISION_INVALID")
    now = _utc(now)
    expires = request.metadata.get("order_expire_at_ms")
    if type(expires) is not int or expires <= int(now.timestamp() * 1000):
      raise ValueError("PAPER_ORDER_TTL_INVALID")
    payload = {
      "order_id": order_id,
      "intent_id": intent_id,
      "order_attempt": order_attempt,
      "request": _json(request),
      "sizing_evidence": _json(sizing_evidence),
      "risk_evidence": _json(risk_evidence),
      "expected_revision": expected_revision,
      "expected_snapshot_hash": expected_snapshot_hash,
      "now": now.isoformat(),
    }
    return await self._operate(
      execution_id,
      event_key,
      "ORDER",
      payload,
      now,
      request=request,
      draft=sizing_evidence,
      risk=risk_evidence,
    )

  async def process_quote(
    self, *, execution_id: str, event_key: str, quote: MarketDataSnapshot
  ):
    now = _utc(quote.timestamp)
    quote = copy.deepcopy(quote)
    quote.timestamp = now
    return await self._operate(
      execution_id, event_key, "QUOTE", {"quote": _json(quote)}, now, quote=quote
    )

  async def cancel(
    self, *, execution_id: str, event_key: str, order_id: str, now: datetime
  ):
    now = _utc(now)
    return await self._operate(
      execution_id,
      event_key,
      "CANCEL",
      {"order_id": order_id, "now": now.isoformat()},
      now,
    )

  async def _operate(
    self,
    execution_id,
    event_key,
    kind,
    payload,
    now,
    *,
    request=None,
    draft=None,
    risk=None,
    quote=None,
  ):
    if not isinstance(event_key, str) or not event_key.strip() or len(event_key) > 256:
      raise ValueError("PAPER_EVENT_KEY_REQUIRED")
    input_hash = stable_manifest_hash({"event_type": kind, "input": payload})
    async with self.db.begin_nested():
      account = await self._account(execution_id, lock=True)
      prior = await self.db.scalar(
        select(PaperExecutionEventRecord).where(
          PaperExecutionEventRecord.execution_id == execution_id,
          PaperExecutionEventRecord.event_key == event_key,
        )
      )
      if prior is not None:
        return self._duplicate(prior, kind, input_hash)
      if kind == "ORDER":
        existing = await self.db.get(
          PaperExecutionOrderRecord, payload["order_id"], populate_existing=True
        )
        if existing is not None:
          if existing.execution_id != execution_id:
            raise ValueError("PAPER_ORDER_SCOPE_CONFLICT")
          original = await self.db.scalar(
            select(PaperExecutionEventRecord).where(
              PaperExecutionEventRecord.execution_id == execution_id,
              PaperExecutionEventRecord.event_type == "ORDER",
              PaperExecutionEventRecord.input_payload["order_id"].as_string()
              == existing.order_id,
            )
          )
          if original is None:
            raise ValueError("PAPER_ORDER_ORIGINAL_EVENT_MISSING")
          return self._duplicate(original, kind, input_hash)
        if (
          account.revision != payload["expected_revision"]
          or account.snapshot_hash != payload["expected_snapshot_hash"]
        ):
          raise ValueError("PAPER_ACCOUNT_REVISION_CONFLICT")
        intent = await self._authorize_order(
          account, payload["intent_id"], request, draft, now
        )
      elif kind == "CANCEL":
        existing = await self.db.get(
          PaperExecutionOrderRecord, payload["order_id"], populate_existing=True
        )
        if existing is None or existing.execution_id != execution_id:
          raise ValueError("PAPER_ORDER_SCOPE_CONFLICT")
        if OrderStatus(existing.status) in _TERMINAL:
          raise ValueError("PAPER_ORDER_TERMINAL")
      if now < _stored_time(account.snapshot_as_of):
        raise ValueError("PAPER_EVENT_NOT_CAUSAL")
      matcher = PaperBrokerMatching.restore(
        scope_execution_id=execution_id, checkpoint=account.broker_checkpoint
      )
      buckets = _bucket_load(account.bucket_checkpoint)
      buckets.settle_trading_day(_time(now).date())
      event_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"quantx:paper-event:{execution_id}:{event_key}")
      )
      if kind == "ORDER":
        plan = copy.deepcopy(risk.substitution_plan)
        if plan is not None:
          plan["plan_id"] = str(
            uuid.uuid5(
              uuid.NAMESPACE_URL,
              f"paper-substitution:{execution_id}:{payload['order_id']}",
            )
          )
        if not buckets.reserve_order(
          payload["order_id"],
          instrument_code=request.instrument_code,
          order_type=request.order_type,
          bucket=intent.bucket,
          volume=request.volume,
          price=request.price,
          metadata={**request.metadata, "bucket": intent.bucket},
          substitution_plan=plan,
        ):
          raise ValueError("PAPER_BUCKET_RESERVATION_REJECTED")
        values = _bucket_dump(buckets)
        values["pending_orders"][payload["order_id"]]["created_at"] = now.isoformat()
        buckets = _bucket_load(values)
        result = await matcher.place(
          order_id=payload["order_id"], request=request, now=now
        )
      elif kind == "QUOTE":
        result = await matcher.process_quote(event_id=event_id, quote=quote)
      else:
        result = await matcher.cancel(order_id=payload["order_id"], now=now)
      for trade in result.trades:
        buckets.apply_trade(trade)
      for order in result.orders:
        if order.status in _TERMINAL:
          buckets.rollback_order(order.order_id, reason=order.status.value)
      self._conserve(buckets, result.account.positions)
      new_broker, new_buckets = matcher.export_checkpoint(), _bucket_dump(buckets)
      revision = account.revision + 1
      fingerprint = _snapshot_hash(execution_id, revision, new_broker, new_buckets, now)
      result_payload = {
        "order_ids": [order.order_id for order in result.orders],
        "fill_ids": [trade.trade_id for trade in result.trades],
        "revision": revision,
        "snapshot_hash": fingerprint,
        "orders": _json(result.orders),
        "trades": _json(result.trades),
      }
      event = PaperExecutionEventRecord(
        event_id=event_id,
        execution_id=execution_id,
        environment="PAPER",
        event_key=event_key,
        event_type=kind,
        revision=revision,
        input_hash=input_hash,
        input_payload=payload,
        result_payload=result_payload,
        previous_snapshot_hash=account.snapshot_hash,
        resulting_snapshot_hash=fingerprint,
        occurred_at=now,
      )
      self.db.add(event)
      await self.db.flush()
      for order in result.orders:
        row = await self.db.get(
          PaperExecutionOrderRecord, order.order_id, populate_existing=True
        )
        if row is None:
          if kind != "ORDER" or order.order_id != payload["order_id"]:
            raise ValueError("PAPER_ORDER_HISTORY_MISSING")
          row = PaperExecutionOrderRecord(
            order_id=order.order_id,
            execution_id=execution_id,
            environment="PAPER",
            owner_type=request.execution_ref.owner_type.value,
            owner_id=request.execution_ref.owner_id,
            intent_id=intent.id,
            allocation_decision_id=intent.allocation_decision_id,
            admission_batch_id=intent.admission_batch_id,
            instrument_code=request.instrument_code,
            order_attempt=payload["order_attempt"],
            side=request.order_type.value,
            volume=request.volume,
            limit_price=Decimal(str(request.price)),
            request_payload=_json(request),
            sizing_evidence=_json(draft),
            risk_evidence=_json(risk),
            submitted_at=_utc(order.submit_time),
            expires_at=datetime.fromtimestamp(
              request.metadata["order_expire_at_ms"] / 1000, UTC
            ),
          )
          self.db.add(row)
        elif row.execution_id != execution_id:
          raise ValueError("PAPER_ORDER_SCOPE_CONFLICT")
        row.status, row.filled_volume, row.response_payload, row.last_event_id = (
          order.status.value,
          order.filled_volume,
          _json(order),
          event_id,
        )
      await self.db.flush()
      for trade in result.trades:
        self.db.add(
          PaperExecutionFillRecord(
            fill_id=trade.trade_id,
            execution_id=execution_id,
            environment="PAPER",
            order_id=trade.order_id,
            event_id=event_id,
            volume=trade.volume,
            price=Decimal(str(trade.price)),
            fee=Decimal(str(trade.commission)),
            occurred_at=_utc(trade.trade_time),
            trade_payload=_json(trade),
          )
        )
      account.broker_checkpoint, account.bucket_checkpoint = new_broker, new_buckets
      account.revision, account.snapshot_hash, account.snapshot_as_of = (
        revision,
        fingerprint,
        now,
      )
      await self.db.flush()
      awaited = self.receipt_sink(self.db, execution_id, result)
      if not inspect.isawaitable(awaited):
        raise TypeError("PAPER_ASYNC_RECEIPT_SINK_REQUIRED")
      await awaited
      await self.db.flush()
      return PaperLedgerReceipt(event_id, False, copy.deepcopy(result_payload))

  @staticmethod
  def _duplicate(event, kind, input_hash):
    if event.event_type != kind or event.input_hash != input_hash:
      raise ValueError("PAPER_EVENT_IDEMPOTENCY_CONFLICT")
    return PaperLedgerReceipt(event.event_id, True, copy.deepcopy(event.result_payload))

  @staticmethod
  def _review(request, intent_id, draft, risk):
    if (
      not isinstance(request, OrderRequest)
      or request.environment is not ExecutionEnvironment.PAPER
    ):
      raise ValueError("PAPER_REQUEST_REQUIRED")
    if not isinstance(draft, OrderDraft) or not isinstance(risk, OrderRiskDecision):
      raise TypeError("PAPER_TYPED_SIZING_AND_RISK_REQUIRED")
    if (
      draft.intent_id != intent_id
      or draft.instrument_code != request.instrument_code
      or draft.side is not request.order_type
      or draft.limit_price != request.price
      or request.metadata.get("intent_id") != intent_id
      or request.order_type not in {OrderType.BUY, OrderType.SELL}
      or type(draft.sized_volume) is not int
      or type(request.volume) is not int
      or request.volume <= 0
      or risk.allowed is not True
      or not isinstance(risk.action, RiskAction)
      or risk.action not in {RiskAction.ALLOW, RiskAction.CAP}
      or risk.original_volume != draft.sized_volume
      or type(risk.original_volume) is not int
      or type(risk.final_volume) is not int
      or risk.final_volume != request.volume
      or request.volume > draft.sized_volume
      or not math.isfinite(risk.final_amount)
      or not math.isfinite(risk.original_amount)
      or not math.isclose(risk.original_amount, request.price * draft.sized_volume)
      or not math.isclose(draft.sized_amount, request.price * draft.sized_volume)
      or not math.isclose(risk.final_amount, request.price * request.volume)
    ):
      raise ValueError("PAPER_SIZING_RISK_BINDING_CONFLICT")
    if request.metadata.get("substitution_plan") not in (None, risk.substitution_plan):
      raise ValueError("PAPER_SUBSTITUTION_RISK_CONFLICT")

  async def _authorize_order(self, account, intent_id, request, draft, now):
    intent = await self.db.get(
      TradeIntentRecord, intent_id, with_for_update=True, populate_existing=True
    )
    if (
      intent is None
      or intent.environment != "PAPER"
      or intent.account_id != account.account_id
      or intent.owner_type != request.execution_ref.owner_type.value
      or intent.owner_id != request.execution_ref.owner_id
      or intent.instrument_code != request.instrument_code
      or intent.direction != request.order_type.value
      or intent.bucket != draft.bucket
      or request.metadata.get("bucket") != intent.bucket
      or intent.status != "EXECUTION_READY"
    ):
      raise ValueError("PAPER_INTENT_BINDING_CONFLICT")
    if request.order_type is OrderType.BUY:
      metadata = dict(intent.intent_metadata or {})
      try:
        created = _utc(datetime.fromisoformat(metadata["intent_created_at"]))
        source_ms, ttl_ms = metadata["source_time_ms"], metadata["approval_ttl_ms"]
        if (
          type(source_ms) is not int
          or source_ms < 0
          or type(ttl_ms) is not int
          or ttl_ms <= 0
        ):
          raise ValueError("invalid intent clock")
        source = datetime.fromtimestamp(source_ms / 1000, UTC)
        deadline = min(created, source) + timedelta(milliseconds=ttl_ms)
      except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("PAPER_INTENT_TTL_INVALID") from exc
      if source > created or now < created:
        raise ValueError("PAPER_INTENT_NOT_CAUSAL")
      if now >= deadline:
        raise ValueError("PAPER_INTENT_EXPIRED")
      execution = await self._execution(account.execution_id)
      if (
        execution.status != "RUNNING"
        or execution.entry_readiness != "READY"
        or intent.owner_type != "T_ASSISTANT_EXECUTION"
        or intent.owner_id != account.execution_id
      ):
        raise ValueError("PAPER_ENTRY_NOT_READY")
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
      admission = (
        await self.db.get(
          AccountRiskIncreaseAdmissionBatch,
          intent.admission_batch_id,
          populate_existing=True,
        )
        if intent.admission_batch_id
        else None
      )
      item = await self.db.scalar(
        select(AccountRiskIncreaseAdmissionItem).where(
          AccountRiskIncreaseAdmissionItem.admission_batch_id
          == intent.admission_batch_id,
          AccountRiskIncreaseAdmissionItem.intent_id == intent.id,
        )
      )
      if (
        decision is None
        or allocation is None
        or allocation.status != "COMMITTED"
        or allocation.environment != "PAPER"
        or allocation.execution_id != account.execution_id
        or decision.intent_id != intent.id
        or decision.action not in {"ALLOW", "CAP"}
        or decision.intent_version + 1 != intent.allocation_version
        or allocation.cycle_id != intent.allocation_cycle_id
        or decision.instrument_code != request.instrument_code
        or admission is None
        or admission.status != "COMMITTED"
        or admission.environment != "PAPER"
        or admission.paper_execution_id != account.execution_id
        or admission.account_id != account.account_id
        or item is None
        or item.admission_rank != intent.admission_rank
      ):
        raise ValueError("PAPER_BUY_ADMISSION_CONFLICT")
      # These authorize creation only. Once submitted within the window, the
      # order's own execution TTL governs subsequent matching/cancellation.
      if now < _stored_time(decision.created_at):
        raise ValueError("PAPER_ALLOCATION_NOT_CAUSAL")
      if now >= _stored_time(decision.expires_at):
        raise ValueError("PAPER_ALLOCATION_EXPIRED")
      if admission.committed_at is None or now < _stored_time(admission.committed_at):
        raise ValueError("PAPER_ADMISSION_NOT_CAUSAL")
      if now >= _stored_time(admission.expires_at):
        raise ValueError("PAPER_ADMISSION_EXPIRED")
      try:
        cap = Decimal(str(draft.metadata.get("allocated_amount_cap")))
      except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("PAPER_ALLOCATION_CAP_CONFLICT") from exc
      gross = Decimal(str(request.price)) * request.volume + Decimal(
        str(estimate_buy_fee_cny(price=request.price, volume=request.volume))
      )
      if not cap.is_finite() or cap != decision.allocated_amount_cap or gross > cap:
        raise ValueError("PAPER_ALLOCATION_CAP_CONFLICT")
    else:
      if draft.metadata.get("allocated_amount_cap") is not None:
        raise ValueError("PAPER_EXIT_ALLOCATION_CAP_FORBIDDEN")
      plan = await self.db.get(
        AutoExitPlanRecord, request.execution_ref.owner_id, populate_existing=True
      )
      if (
        intent.owner_type != "EXIT_PLAN"
        or plan is None
        or plan.environment != "PAPER"
        or plan.account_id != account.account_id
        or plan.instrument_code != request.instrument_code
        or plan.source_execution_owner_type != "T_ASSISTANT_EXECUTION"
        or plan.source_execution_owner_id != account.execution_id
      ):
        raise ValueError("PAPER_EXIT_OWNER_CONFLICT")
    return intent

  @staticmethod
  def _conserve(ledger, positions):
    instruments = _bucket_dump(ledger)["instruments"]
    for code in set(instruments) | set(positions):
      buckets = instruments.get(code, {})
      if set(buckets) - set(KNOWN_BUCKETS):
        raise ValueError("PAPER_BUCKET_INVALID")
      position = positions.get(code, Position(code))
      if (
        sum(value["total_volume"] for value in buckets.values()) != position.long_volume
        or sum(value["today_buy_volume"] for value in buckets.values())
        != position.today_buy_volume
        or sum(
          value["available_volume"] + value["frozen_volume"]
          for value in buckets.values()
        )
        != position.available_volume + position.frozen_volume
      ):
        raise ValueError("PAPER_BUCKET_BROKER_CONSERVATION_FAILED")
