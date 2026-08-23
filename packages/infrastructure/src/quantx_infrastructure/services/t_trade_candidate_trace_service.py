"""Read-only, account-scoped lineage for one stateful T-trade candidate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.models.agent_runtime import (
  PendingTradeOrder,
  StrategyOrderCorrelation,
  TTradeBatch,
)
from quantx_infrastructure.models.auto_exit_plan import (
  AutoExitPlanEvent,
  AutoExitPlanRecord,
)
from quantx_infrastructure.models.order import Order
from quantx_infrastructure.models.t_trade_opportunity_intelligence import (
  T_TRADE_EVALUATION_KIND_MATERIAL,
  TTradeOpportunityEvaluation,
)
from quantx_infrastructure.models.trade import Trade
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord

_STAGE_ORDER = {
  "EVALUATION": 0,
  "TRADE_INTENT": 10,
  "T_TRADE_BATCH": 20,
  "PENDING_ORDER": 30,
  "ORDER_CORRELATION": 40,
  "BROKER_ORDER": 50,
  "BROKER_TRADE": 60,
  "AUTO_EXIT_PLAN": 70,
  "AUTO_EXIT_PLAN_EVENT": 80,
}
_INTENT_TERMINAL_WITHOUT_EXECUTION = {
  "CANCELLED",
  "EXPIRED",
  "REJECTED",
  "SUPPRESSED",
}
_EXECUTION_CLAIM_STATUSES = {
  "ACCEPTED",
  "FILLED",
  "PARTIAL_FILLED",
  "ROUTED",
  "SUBMITTED",
}
_PENDING_BROKER_CLAIM_STATUSES = {
  "ACCEPTED",
  "CANCELLED",
  "FILLED",
  "PARTIAL_FILLED",
  "REJECTED",
  "SUBMITTED",
}
_FILLED_ORDER_STATUSES = {"PART_SUCC", "SUCCEEDED"}
_DEFAULT_STAGE_ROW_LIMIT = 256
_DEFAULT_TOTAL_EVENT_LIMIT = 1_024


@dataclass(frozen=True, slots=True)
class CandidateTraceMissingReason:
  code: str
  stage: str
  expected: bool
  detail: str

  def to_dict(self) -> dict[str, Any]:
    return {
      "code": self.code,
      "stage": self.stage,
      "expected": self.expected,
      "detail": self.detail,
    }


@dataclass(frozen=True, slots=True)
class CandidateTraceSourceIdentity:
  source_time_ms: Optional[int]
  tick_ordinal: Optional[int]
  continuity_generation: Optional[str]
  trade_date: Optional[str]
  candidate_fingerprint: Optional[str]
  policy_version: Optional[str]
  feature_schema_version: Optional[str]
  profile_version: Optional[str]

  def to_dict(self) -> dict[str, Any]:
    return {
      "source_time_ms": self.source_time_ms,
      "tick_ordinal": self.tick_ordinal,
      "continuity_generation": self.continuity_generation,
      "trade_date": self.trade_date,
      "candidate_fingerprint": self.candidate_fingerprint,
      "policy_version": self.policy_version,
      "feature_schema_version": self.feature_schema_version,
      "profile_version": self.profile_version,
    }


@dataclass(frozen=True, slots=True)
class CandidateTraceEvent:
  stage: str
  event_type: str
  entity_id: str
  occurred_at: datetime
  status: Optional[str]
  related_ids: Mapping[str, tuple[str, ...]]
  details: Mapping[str, Any]

  def to_dict(self) -> dict[str, Any]:
    return {
      "stage": self.stage,
      "event_type": self.event_type,
      "entity_id": self.entity_id,
      "occurred_at": self.occurred_at.isoformat(),
      "status": self.status,
      "related_ids": {
        key: list(values) for key, values in sorted(self.related_ids.items())
      },
      "details": dict(self.details),
    }


@dataclass(frozen=True, slots=True)
class CandidateTraceLinks:
  evaluation_ids: tuple[str, ...]
  intent_ids: tuple[str, ...]
  client_order_ids: tuple[str, ...]
  correlation_ids: tuple[str, ...]
  broker_order_ids: tuple[str, ...]
  order_ids: tuple[str, ...]
  trade_ids: tuple[str, ...]
  batch_ids: tuple[str, ...]
  exit_plan_ids: tuple[str, ...]
  exit_plan_event_ids: tuple[str, ...]

  def to_dict(self) -> dict[str, list[str]]:
    return {
      "evaluation_ids": list(self.evaluation_ids),
      "intent_ids": list(self.intent_ids),
      "client_order_ids": list(self.client_order_ids),
      "correlation_ids": list(self.correlation_ids),
      "broker_order_ids": list(self.broker_order_ids),
      "order_ids": list(self.order_ids),
      "trade_ids": list(self.trade_ids),
      "batch_ids": list(self.batch_ids),
      "exit_plan_ids": list(self.exit_plan_ids),
      "exit_plan_event_ids": list(self.exit_plan_event_ids),
    }


@dataclass(frozen=True, slots=True)
class CandidateTrace:
  account_id: str
  candidate_id: str
  strategy_run_id: str
  instrument_code: str
  source_evaluation_id: str
  source_identity: CandidateTraceSourceIdentity
  integrity_status: str
  missing_reasons: tuple[CandidateTraceMissingReason, ...]
  links: CandidateTraceLinks
  events: tuple[CandidateTraceEvent, ...]

  def to_dict(self) -> dict[str, Any]:
    return {
      "account_id": self.account_id,
      "candidate_id": self.candidate_id,
      "strategy_run_id": self.strategy_run_id,
      "instrument_code": self.instrument_code,
      "source_evaluation_id": self.source_evaluation_id,
      "source_identity": self.source_identity.to_dict(),
      "integrity_status": self.integrity_status,
      "missing_reasons": [item.to_dict() for item in self.missing_reasons],
      "links": self.links.to_dict(),
      "events": [item.to_dict() for item in self.events],
    }


class TTradeCandidateTraceService:
  """Build a trace from durable facts without consulting monitor projections."""

  def __init__(
    self,
    db: AsyncSession,
    *,
    stage_row_limit: int = _DEFAULT_STAGE_ROW_LIMIT,
    total_event_limit: int = _DEFAULT_TOTAL_EVENT_LIMIT,
  ) -> None:
    if stage_row_limit < 1 or total_event_limit < 1:
      raise ValueError("候选追踪查询上限必须为正整数")
    self.db = db
    self._stage_row_limit = int(stage_row_limit)
    self._total_event_limit = int(total_event_limit)

  async def get_trace(
    self,
    *,
    account_id: str,
    strategy_run_id: str,
    candidate_id: str,
  ) -> Optional[CandidateTrace]:
    account = _required_text(account_id, "证券账户", 50)
    strategy_run_id = _required_text(strategy_run_id, "策略运行标识", 128)
    candidate = _required_text(candidate_id, "候选标识", 128)
    evaluations = await self._candidate_evaluations(
      account,
      strategy_run_id,
      candidate,
    )
    if not evaluations:
      return None

    instruments = {str(row.instrument_code).upper() for row in evaluations}
    if len(instruments) != 1:
      raise ValueError("同一策略运行内候选标识对应多个证券标的")
    instrument_code = next(iter(instruments))

    candidate_intents = await self._candidate_intents(
      account_id=account,
      strategy_run_id=strategy_run_id,
      instrument_code=instrument_code,
      candidate_id=candidate,
    )
    candidate_intent_ids = {str(row.id) for row in candidate_intents}
    seed_batch_ids = {
      value
      for row in candidate_intents
      for value in (
        _metadata_text(row.intent_metadata, "t_batch_id"),
        _metadata_text(row.intent_metadata, "batch_id"),
      )
      if value
    }
    batches = await self._batches(
      account_id=account,
      strategy_run_id=strategy_run_id,
      instrument_code=instrument_code,
      intent_ids=candidate_intent_ids,
      batch_ids=seed_batch_ids,
    )
    batch_ids = seed_batch_ids | {str(row.batch_id) for row in batches}
    batch_intent_ids = {
      value
      for row in batches
      for value in (str(row.entry_intent_id or ""), str(row.exit_intent_id or ""))
      if value
    }
    relevant_intent_ids = candidate_intent_ids | batch_intent_ids
    relevant_intents = await self._linked_intents(
      account_id=account,
      strategy_run_id=strategy_run_id,
      instrument_code=instrument_code,
      intent_ids=relevant_intent_ids,
      batch_ids=batch_ids,
    )
    relevant_intent_ids |= {str(row.id) for row in relevant_intents}

    pending_orders = await self._pending_orders(
      account_id=account,
      strategy_run_id=strategy_run_id,
      instrument_code=instrument_code,
      intent_ids=relevant_intent_ids,
      batch_ids=batch_ids,
    )
    client_order_ids = {str(row.client_order_id) for row in pending_orders}
    correlations = await self._correlations(
      account_id=account,
      strategy_run_id=strategy_run_id,
      intent_ids=relevant_intent_ids,
      batch_ids=batch_ids,
      client_order_ids=client_order_ids,
    )
    client_order_ids |= {str(row.client_order_id) for row in correlations}

    raw_broker_order_ids = {
      value
      for value in (
        *[str(row.broker_order_id or "") for row in pending_orders],
        *[str(row.broker_order_id or "") for row in correlations],
        *[str(row.entry_broker_order_id or "") for row in batches],
        *[str(row.exit_broker_order_id or "") for row in batches],
      )
      if value.strip()
    }
    broker_order_numbers = {
      number
      for value in raw_broker_order_ids
      if (number := _broker_order_number(value)) is not None
    }
    orders = await self._orders(
      account_id=account,
      instrument_code=instrument_code,
      broker_order_numbers=broker_order_numbers,
    )
    order_numbers = {int(row.id) for row in orders}
    trades = await self._trades(
      account_id=account,
      instrument_code=instrument_code,
      order_numbers=order_numbers,
    )

    exit_plan_ids = {
      value
      for row in relevant_intents
      if (value := _metadata_text(row.intent_metadata, "exit_plan_id"))
    }
    plans = await self._exit_plans(
      account_id=account,
      strategy_run_id=strategy_run_id,
      instrument_code=instrument_code,
      batch_ids=batch_ids,
      plan_ids=exit_plan_ids,
    )
    # Metadata is only a discovery hint.  Exit-plan events have no account/run
    # columns of their own, so query them exclusively through plan IDs that
    # survived the scoped AutoExitPlanRecord lookup above.
    validated_exit_plan_ids = {str(row.plan_id) for row in plans}
    plan_events = await self._exit_plan_events(validated_exit_plan_ids)

    events = _events(
      candidate_id=candidate,
      evaluations=evaluations,
      intents=relevant_intents,
      batches=batches,
      pending_orders=pending_orders,
      correlations=correlations,
      orders=orders,
      trades=trades,
      plans=plans,
      plan_events=plan_events,
    )
    if len(events) > self._total_event_limit:
      raise ValueError(
        "候选追踪事件总数超过有界上限: "
        f"candidate_id={candidate}, limit={self._total_event_limit}"
      )
    missing_reasons = _missing_reasons(
      evaluations=evaluations,
      candidate_intents=candidate_intents,
      relevant_intents=relevant_intents,
      batches=batches,
      pending_orders=pending_orders,
      correlations=correlations,
      orders=orders,
      trades=trades,
      plans=plans,
      plan_events=plan_events,
      raw_broker_order_ids=raw_broker_order_ids,
    )
    integrity_status = (
      "BROKEN"
      if any(not item.expected for item in missing_reasons)
      else "IN_PROGRESS"
      if missing_reasons
      else "COMPLETE"
    )
    first_evaluation = evaluations[0]
    source_snapshot = _snapshot(first_evaluation)
    links = CandidateTraceLinks(
      evaluation_ids=_ids(row.id for row in evaluations),
      intent_ids=_ids(row.id for row in relevant_intents),
      client_order_ids=_ids(client_order_ids),
      correlation_ids=_ids(row.id for row in correlations),
      broker_order_ids=_ids(
        _canonical_broker_order_id(value) for value in raw_broker_order_ids
      ),
      order_ids=_ids(str(row.id) for row in orders),
      trade_ids=_ids(row.id for row in trades),
      batch_ids=_ids(row.batch_id for row in batches),
      exit_plan_ids=_ids(row.plan_id for row in plans),
      exit_plan_event_ids=_ids(row.event_id for row in plan_events),
    )
    return CandidateTrace(
      account_id=account,
      candidate_id=candidate,
      strategy_run_id=strategy_run_id,
      instrument_code=instrument_code,
      source_evaluation_id=str(first_evaluation.id),
      source_identity=_source_identity(source_snapshot),
      integrity_status=integrity_status,
      missing_reasons=missing_reasons,
      links=links,
      events=events,
    )

  async def _candidate_evaluations(
    self,
    account_id: str,
    strategy_run_id: str,
    candidate_id: str,
  ) -> list[TTradeOpportunityEvaluation]:
    rows = list(
      (
        await self.db.execute(
          select(TTradeOpportunityEvaluation)
          .where(
            TTradeOpportunityEvaluation.account_id == account_id,
            TTradeOpportunityEvaluation.strategy_run_id == strategy_run_id,
            TTradeOpportunityEvaluation.record_kind == T_TRADE_EVALUATION_KIND_MATERIAL,
            TTradeOpportunityEvaluation.candidate_id == candidate_id,
          )
          .order_by(
            TTradeOpportunityEvaluation.evaluated_at,
            TTradeOpportunityEvaluation.id,
          )
          .limit(self._stage_row_limit + 1)
        )
      )
      .scalars()
      .all()
    )
    rows = self._bounded_rows(rows, "MATERIAL 评估")
    return [
      row
      for row in rows
      if str(_snapshot(row).get("candidate_id") or "") == candidate_id
    ]

  async def _candidate_intents(
    self,
    *,
    account_id: str,
    strategy_run_id: str,
    instrument_code: str,
    candidate_id: str,
  ) -> list[TradeIntentRecord]:
    rows = list(
      (
        await self.db.execute(
          select(TradeIntentRecord)
          .where(
            TradeIntentRecord.account_id == account_id,
            TradeIntentRecord.strategy_run_id == strategy_run_id,
            TradeIntentRecord.instrument_code == instrument_code,
            TradeIntentRecord.intent_metadata["candidate_id"].as_string()
            == candidate_id,
          )
          .order_by(TradeIntentRecord.created_at, TradeIntentRecord.id)
          .limit(self._stage_row_limit + 1)
        )
      )
      .scalars()
      .all()
    )
    return self._bounded_rows(rows, "候选交易意图")

  async def _linked_intents(
    self,
    *,
    account_id: str,
    strategy_run_id: str,
    instrument_code: str,
    intent_ids: set[str],
    batch_ids: set[str],
  ) -> list[TradeIntentRecord]:
    links = []
    if intent_ids:
      links.append(TradeIntentRecord.id.in_(tuple(intent_ids)))
    if batch_ids:
      normalized_batch_ids = tuple(batch_ids)
      links.extend(
        (
          TradeIntentRecord.intent_metadata["t_batch_id"]
          .as_string()
          .in_(normalized_batch_ids),
          TradeIntentRecord.intent_metadata["batch_id"]
          .as_string()
          .in_(normalized_batch_ids),
        )
      )
    if not links:
      return []
    rows = list(
      (
        await self.db.execute(
          select(TradeIntentRecord)
          .where(
            TradeIntentRecord.account_id == account_id,
            TradeIntentRecord.strategy_run_id == strategy_run_id,
            TradeIntentRecord.instrument_code == instrument_code,
            or_(*links),
          )
          .order_by(TradeIntentRecord.created_at, TradeIntentRecord.id)
          .limit(self._stage_row_limit + 1)
        )
      )
      .scalars()
      .all()
    )
    return self._bounded_rows(rows, "关联交易意图")

  async def _batches(
    self,
    *,
    account_id: str,
    strategy_run_id: str,
    instrument_code: str,
    intent_ids: set[str],
    batch_ids: set[str],
  ) -> list[TTradeBatch]:
    links = []
    if intent_ids:
      links.extend(
        (
          TTradeBatch.entry_intent_id.in_(tuple(intent_ids)),
          TTradeBatch.exit_intent_id.in_(tuple(intent_ids)),
        )
      )
    if batch_ids:
      links.append(TTradeBatch.batch_id.in_(tuple(batch_ids)))
    if not links:
      return []
    rows = list(
      (
        await self.db.execute(
          select(TTradeBatch)
          .where(
            TTradeBatch.account_id == account_id,
            TTradeBatch.strategy_run_id == strategy_run_id,
            TTradeBatch.instrument_code == instrument_code,
            or_(*links),
          )
          .order_by(TTradeBatch.created_at, TTradeBatch.batch_id)
          .limit(self._stage_row_limit + 1)
        )
      )
      .scalars()
      .all()
    )
    return self._bounded_rows(rows, "做 T 批次")

  async def _pending_orders(
    self,
    *,
    account_id: str,
    strategy_run_id: str,
    instrument_code: str,
    intent_ids: set[str],
    batch_ids: set[str],
  ) -> list[PendingTradeOrder]:
    links = []
    if intent_ids:
      links.append(PendingTradeOrder.intent_id.in_(tuple(intent_ids)))
    if batch_ids:
      links.append(PendingTradeOrder.batch_id.in_(tuple(batch_ids)))
    if not links:
      return []
    rows = list(
      (
        await self.db.execute(
          select(PendingTradeOrder)
          .where(
            PendingTradeOrder.account_id == account_id,
            PendingTradeOrder.strategy_run_id == strategy_run_id,
            PendingTradeOrder.instrument_code == instrument_code,
            or_(*links),
          )
          .order_by(PendingTradeOrder.created_at, PendingTradeOrder.client_order_id)
          .limit(self._stage_row_limit + 1)
        )
      )
      .scalars()
      .all()
    )
    return self._bounded_rows(rows, "待处理委托")

  async def _correlations(
    self,
    *,
    account_id: str,
    strategy_run_id: str,
    intent_ids: set[str],
    batch_ids: set[str],
    client_order_ids: set[str],
  ) -> list[StrategyOrderCorrelation]:
    links = []
    if intent_ids:
      links.append(StrategyOrderCorrelation.intent_id.in_(tuple(intent_ids)))
    if batch_ids:
      links.append(StrategyOrderCorrelation.batch_id.in_(tuple(batch_ids)))
    if client_order_ids:
      links.append(
        StrategyOrderCorrelation.client_order_id.in_(tuple(client_order_ids))
      )
    if not links:
      return []
    rows = list(
      (
        await self.db.execute(
          select(StrategyOrderCorrelation)
          .where(
            StrategyOrderCorrelation.account_id == account_id,
            StrategyOrderCorrelation.strategy_run_id == strategy_run_id,
            or_(*links),
          )
          .order_by(
            StrategyOrderCorrelation.created_at,
            StrategyOrderCorrelation.id,
          )
          .limit(self._stage_row_limit + 1)
        )
      )
      .scalars()
      .all()
    )
    return self._bounded_rows(rows, "委托关联")

  async def _orders(
    self,
    *,
    account_id: str,
    instrument_code: str,
    broker_order_numbers: set[int],
  ) -> list[Order]:
    if not broker_order_numbers:
      return []
    rows = list(
      (
        await self.db.execute(
          select(Order)
          .where(
            Order.account_id == account_id,
            Order.stock_code == instrument_code,
            Order.id.in_(tuple(broker_order_numbers)),
          )
          .order_by(Order.time, Order.id)
          .limit(self._stage_row_limit + 1)
        )
      )
      .scalars()
      .all()
    )
    return self._bounded_rows(rows, "Broker 委托")

  async def _trades(
    self,
    *,
    account_id: str,
    instrument_code: str,
    order_numbers: set[int],
  ) -> list[Trade]:
    if not order_numbers:
      return []
    rows = list(
      (
        await self.db.execute(
          select(Trade)
          .where(
            Trade.account_id == account_id,
            Trade.stock_code == instrument_code,
            Trade.order_id.in_(tuple(order_numbers)),
          )
          .order_by(Trade.time, Trade.id)
          .limit(self._stage_row_limit + 1)
        )
      )
      .scalars()
      .all()
    )
    return self._bounded_rows(rows, "Broker 成交")

  async def _exit_plans(
    self,
    *,
    account_id: str,
    strategy_run_id: str,
    instrument_code: str,
    batch_ids: set[str],
    plan_ids: set[str],
  ) -> list[AutoExitPlanRecord]:
    links = []
    if batch_ids:
      links.append(
        (AutoExitPlanRecord.source_type == "T_TRADE_BATCH")
        & AutoExitPlanRecord.source_id.in_(tuple(batch_ids))
      )
    if plan_ids:
      links.append(AutoExitPlanRecord.plan_id.in_(tuple(plan_ids)))
    if not links:
      return []
    rows = list(
      (
        await self.db.execute(
          select(AutoExitPlanRecord)
          .where(
            AutoExitPlanRecord.account_id == account_id,
            AutoExitPlanRecord.strategy_run_id == strategy_run_id,
            AutoExitPlanRecord.instrument_code == instrument_code,
            or_(*links),
          )
          .order_by(AutoExitPlanRecord.created_at, AutoExitPlanRecord.plan_id)
          .limit(self._stage_row_limit + 1)
        )
      )
      .scalars()
      .all()
    )
    return self._bounded_rows(rows, "自动退出计划")

  async def _exit_plan_events(
    self,
    plan_ids: set[str],
  ) -> list[AutoExitPlanEvent]:
    if not plan_ids:
      return []
    rows = list(
      (
        await self.db.execute(
          select(AutoExitPlanEvent)
          .where(AutoExitPlanEvent.plan_id.in_(tuple(plan_ids)))
          .order_by(AutoExitPlanEvent.created_at, AutoExitPlanEvent.event_id)
          .limit(self._stage_row_limit + 1)
        )
      )
      .scalars()
      .all()
    )
    return self._bounded_rows(rows, "自动退出计划事件")

  def _bounded_rows(self, rows: list[Any], stage: str) -> list[Any]:
    if len(rows) > self._stage_row_limit:
      raise ValueError(
        "候选追踪阶段记录数超过有界上限: "
        f"stage={stage}, limit={self._stage_row_limit}"
      )
    return rows


def _events(
  *,
  candidate_id: str,
  evaluations: Sequence[TTradeOpportunityEvaluation],
  intents: Sequence[TradeIntentRecord],
  batches: Sequence[TTradeBatch],
  pending_orders: Sequence[PendingTradeOrder],
  correlations: Sequence[StrategyOrderCorrelation],
  orders: Sequence[Order],
  trades: Sequence[Trade],
  plans: Sequence[AutoExitPlanRecord],
  plan_events: Sequence[AutoExitPlanEvent],
) -> tuple[CandidateTraceEvent, ...]:
  result: list[CandidateTraceEvent] = []
  clients_by_broker: dict[str, set[str]] = {}
  for row in (*pending_orders, *correlations):
    broker_id = _canonical_broker_order_id(row.broker_order_id)
    if broker_id:
      clients_by_broker.setdefault(broker_id, set()).add(str(row.client_order_id))

  for row in evaluations:
    snapshot = _snapshot(row)
    result.append(
      CandidateTraceEvent(
        stage="EVALUATION",
        event_type=str(row.event_type),
        entity_id=str(row.id),
        occurred_at=_required_datetime(row.evaluated_at),
        status=_optional_text(snapshot.get("candidate_status")),
        related_ids=_related_ids(
          candidate_id=candidate_id,
          evaluation_id=row.id,
          episode_id=snapshot.get("episode_id"),
          intent_id=snapshot.get("pending_entry_intent_id"),
        ),
        details=_compact(
          {
            "record_kind": row.record_kind,
            "selected_path": snapshot.get("selected_path"),
            "dominant_phase": snapshot.get("dominant_phase"),
            "opportunity_score": snapshot.get("opportunity_score"),
            "data_health": snapshot.get("data_health"),
            "source_time_ms": snapshot.get("source_time_ms"),
            "tick_ordinal": snapshot.get("tick_ordinal"),
            "continuity_generation": snapshot.get("continuity_generation"),
            "policy_version": snapshot.get("policy_version") or row.policy_version,
            "feature_schema_version": snapshot.get("feature_schema_version"),
            "profile_version": snapshot.get("profile_version"),
          }
        ),
      )
    )
  for row in intents:
    metadata = _metadata(row.intent_metadata)
    result.append(
      CandidateTraceEvent(
        stage="TRADE_INTENT",
        event_type="TRADE_INTENT",
        entity_id=str(row.id),
        occurred_at=_required_datetime(row.created_at),
        status=_optional_text(row.status),
        related_ids=_related_ids(
          candidate_id=metadata.get("candidate_id"),
          intent_id=row.id,
          batch_id=metadata.get("t_batch_id") or metadata.get("batch_id"),
          exit_plan_id=metadata.get("exit_plan_id"),
          strategy_order_id=row.order_id,
          trace_id=row.trace_id,
        ),
        details=_compact(
          {
            "direction": row.direction,
            "bucket": row.bucket,
            "reason": row.reason,
            "intent_type": row.intent_type,
            "target_amount": row.target_amount,
            "target_volume": row.target_volume,
            "limit_price_hint": row.limit_price_hint,
            "executed_price": row.executed_price,
            "executed_volume": row.executed_volume,
            "executed_time": _iso(row.executed_time),
            "t_trade_role": metadata.get("t_trade_role"),
          }
        ),
      )
    )
  for row in batches:
    result.append(
      CandidateTraceEvent(
        stage="T_TRADE_BATCH",
        event_type="T_TRADE_BATCH",
        entity_id=str(row.batch_id),
        occurred_at=_required_datetime(row.created_at),
        status=_optional_text(row.status),
        related_ids=_related_ids(
          batch_id=row.batch_id,
          intent_id=(row.entry_intent_id, row.exit_intent_id),
          client_order_id=(row.entry_client_order_id, row.exit_client_order_id),
          broker_order_id=(
            _canonical_broker_order_id(row.entry_broker_order_id),
            _canonical_broker_order_id(row.exit_broker_order_id),
          ),
        ),
        details=_compact(
          {
            "target_volume": row.target_volume,
            "entry_filled_volume": row.entry_filled_volume,
            "entry_avg_price": row.entry_avg_price,
            "exit_filled_volume": row.exit_filled_volume,
            "exit_avg_price": row.exit_avg_price,
            "last_net_profit_pct": row.last_net_profit_pct,
            "peak_net_profit_pct": row.peak_net_profit_pct,
            "exit_reason": row.exit_reason,
            "policy_version": row.policy_version,
            "version": row.version,
            "updated_at": _iso(row.updated_at),
          }
        ),
      )
    )
  for row in pending_orders:
    result.append(
      CandidateTraceEvent(
        stage="PENDING_ORDER",
        event_type="ORDER_COMMAND",
        entity_id=str(row.client_order_id),
        occurred_at=_required_datetime(row.created_at),
        status=_optional_text(row.status),
        related_ids=_related_ids(
          intent_id=row.intent_id,
          batch_id=row.batch_id,
          client_order_id=row.client_order_id,
          broker_order_id=_canonical_broker_order_id(row.broker_order_id),
          strategy_order_id=row.strategy_order_id,
          trace_id=row.trace_id,
        ),
        details=_compact(
          {
            "side": row.side,
            "order_type": row.order_type,
            "limit_price": row.limit_price,
            "volume": row.volume,
            "execution_mode": row.execution_mode,
            "bucket": row.bucket,
            "t_trade_role": row.t_trade_role,
            "status_reason": row.status_reason,
            "last_source_event_at": _iso(row.last_source_event_at),
            "updated_at": _iso(row.updated_at),
          }
        ),
      )
    )
  for row in correlations:
    result.append(
      CandidateTraceEvent(
        stage="ORDER_CORRELATION",
        event_type="ORDER_CORRELATION",
        entity_id=str(row.id),
        occurred_at=_required_datetime(row.created_at),
        status=None,
        related_ids=_related_ids(
          intent_id=row.intent_id,
          batch_id=row.batch_id,
          client_order_id=row.client_order_id,
          broker_order_id=_canonical_broker_order_id(row.broker_order_id),
          strategy_order_id=row.strategy_order_id,
          trace_id=row.trace_id,
        ),
        details=_compact(
          {
            "bucket": row.bucket,
            "t_trade_role": row.t_trade_role,
            "execution_mode": row.execution_mode,
          }
        ),
      )
    )
  for row in orders:
    broker_id = _canonical_broker_order_id(row.id)
    result.append(
      CandidateTraceEvent(
        stage="BROKER_ORDER",
        event_type="BROKER_ORDER",
        entity_id=broker_id,
        occurred_at=_required_datetime(row.time),
        status=_enum_text(row.status),
        related_ids=_related_ids(
          broker_order_id=broker_id,
          client_order_id=clients_by_broker.get(broker_id, set()),
        ),
        details=_compact(
          {
            "order_type": _enum_text(row.type),
            "price_type": _enum_text(row.price_type),
            "volume": row.volume,
            "price": row.price,
            "traded_volume": row.traded_volume,
            "traded_price": row.traded_price,
          }
        ),
      )
    )
  for row in trades:
    broker_id = _canonical_broker_order_id(row.order_id)
    result.append(
      CandidateTraceEvent(
        stage="BROKER_TRADE",
        event_type="BROKER_TRADE",
        entity_id=str(row.id),
        occurred_at=_required_datetime(row.time),
        status="FILLED",
        related_ids=_related_ids(
          trade_id=row.id,
          broker_order_id=broker_id,
          client_order_id=clients_by_broker.get(broker_id, set()),
        ),
        details={
          "price": row.price,
          "volume": row.volume,
          "amount": row.amount,
          "order_type": str(row.order_type),
        },
      )
    )
  for row in plans:
    result.append(
      CandidateTraceEvent(
        stage="AUTO_EXIT_PLAN",
        event_type="AUTO_EXIT_PLAN",
        entity_id=str(row.plan_id),
        occurred_at=_required_datetime(row.created_at),
        status=_optional_text(row.status),
        related_ids=_related_ids(
          exit_plan_id=row.plan_id,
          batch_id=row.source_id if row.source_type == "T_TRADE_BATCH" else None,
          client_order_id=row.pending_client_order_id,
        ),
        details=_compact(
          {
            "source_type": row.source_type,
            "phase": row.phase,
            "data_quality": row.data_quality,
            "enabled": row.enabled,
            "execution_mode": row.execution_mode,
            "protected_volume": row.protected_volume,
            "exited_volume": row.exited_volume,
            "remaining_volume": row.remaining_volume,
            "entry_avg_price": row.entry_avg_price,
            "last_decision": row.last_decision,
            "config_version": row.config_version,
            "updated_at": _iso(row.updated_at),
          }
        ),
      )
    )
  for row in plan_events:
    payload = row.payload if isinstance(row.payload, Mapping) else {}
    result.append(
      CandidateTraceEvent(
        stage="AUTO_EXIT_PLAN_EVENT",
        event_type=str(row.event_type),
        entity_id=str(row.event_id),
        occurred_at=_required_datetime(row.created_at),
        status=_optional_text(payload.get("status")),
        related_ids=_related_ids(
          exit_plan_id=row.plan_id,
          exit_plan_event_id=row.event_id,
          client_order_id=payload.get("client_order_id"),
          intent_id=payload.get("intent_id"),
        ),
        details=_safe_plan_event_details(payload),
      )
    )
  return tuple(sorted(result, key=_event_sort_key))


def _missing_reasons(
  *,
  evaluations: Sequence[TTradeOpportunityEvaluation],
  candidate_intents: Sequence[TradeIntentRecord],
  relevant_intents: Sequence[TradeIntentRecord],
  batches: Sequence[TTradeBatch],
  pending_orders: Sequence[PendingTradeOrder],
  correlations: Sequence[StrategyOrderCorrelation],
  orders: Sequence[Order],
  trades: Sequence[Trade],
  plans: Sequence[AutoExitPlanRecord],
  plan_events: Sequence[AutoExitPlanEvent],
  raw_broker_order_ids: set[str],
) -> tuple[CandidateTraceMissingReason, ...]:
  reasons: list[CandidateTraceMissingReason] = []
  source_snapshot = _snapshot(evaluations[0])
  latest_snapshot = _snapshot(evaluations[-1])
  fingerprints = {
    value
    for row in evaluations
    if (value := _optional_text(_snapshot(row).get("candidate_fingerprint")))
  }
  if not fingerprints:
    reasons.append(
      CandidateTraceMissingReason(
        code="CANDIDATE_FINGERPRINT_NOT_FOUND",
        stage="EVALUATION",
        expected=False,
        detail="MATERIAL 评估缺少候选指纹",
      )
    )
  elif len(fingerprints) > 1:
    reasons.append(
      CandidateTraceMissingReason(
        code="CANDIDATE_FINGERPRINT_CONFLICT",
        stage="EVALUATION",
        expected=False,
        detail="同一候选标识对应多个候选指纹",
      )
    )
  missing_source_fields = [
    key
    for key in ("source_time_ms", "tick_ordinal", "continuity_generation")
    if source_snapshot.get(key) is None or str(source_snapshot.get(key)) == ""
  ]
  if missing_source_fields:
    reasons.append(
      CandidateTraceMissingReason(
        code="SOURCE_IDENTITY_INCOMPLETE",
        stage="EVALUATION",
        expected=False,
        detail="MATERIAL 评估缺少源身份字段：" + ",".join(missing_source_fields),
      )
    )
  expected_intent_id = _optional_text(latest_snapshot.get("pending_entry_intent_id"))
  intent_statuses = {
    str(row.status or "").upper() for row in candidate_intents if row.status
  }
  terminal_without_execution = bool(intent_statuses) and intent_statuses.issubset(
    _INTENT_TERMINAL_WITHOUT_EXECUTION
  )
  if not candidate_intents:
    reasons.append(
      CandidateTraceMissingReason(
        code="TRADE_INTENT_NOT_FOUND",
        stage="TRADE_INTENT",
        expected=expected_intent_id is None,
        detail=(
          "候选尚未生成交易意图"
          if expected_intent_id is None
          else f"评估已声明意图 {expected_intent_id}，但权威意图记录不存在"
        ),
      )
    )
  elif expected_intent_id and expected_intent_id not in {
    str(row.id) for row in candidate_intents
  }:
    reasons.append(
      CandidateTraceMissingReason(
        code="DECLARED_TRADE_INTENT_NOT_FOUND",
        stage="TRADE_INTENT",
        expected=False,
        detail=f"评估声明的交易意图 {expected_intent_id} 与候选意图记录不一致",
      )
    )
  intent_fingerprints = {
    value
    for row in candidate_intents
    if (
      value := _optional_text(
        _metadata(row.intent_metadata).get("candidate_fingerprint")
      )
    )
  }
  if intent_fingerprints and fingerprints and intent_fingerprints != fingerprints:
    reasons.append(
      CandidateTraceMissingReason(
        code="TRADE_INTENT_CANDIDATE_FINGERPRINT_MISMATCH",
        stage="TRADE_INTENT",
        expected=False,
        detail="交易意图候选指纹与 MATERIAL 评估不一致",
      )
    )
  if terminal_without_execution:
    return _deduplicate_reasons(reasons)

  execution_claimed = any(
    str(row.status or "").upper() in _EXECUTION_CLAIM_STATUSES
    or row.order_id
    or int(row.executed_volume or 0) > 0
    for row in relevant_intents
  )
  if relevant_intents and not pending_orders:
    reasons.append(
      CandidateTraceMissingReason(
        code="ORDER_COMMAND_NOT_FOUND",
        stage="PENDING_ORDER",
        expected=not execution_claimed,
        detail="尚未创建订单命令"
        if not execution_claimed
        else "意图已进入执行状态但订单命令缺失",
      )
    )

  pending_by_client = {str(row.client_order_id): row for row in pending_orders}
  correlation_by_client = {str(row.client_order_id): row for row in correlations}
  for client_id in sorted(pending_by_client.keys() - correlation_by_client.keys()):
    reasons.append(
      CandidateTraceMissingReason(
        code="ORDER_CORRELATION_NOT_FOUND",
        stage="ORDER_CORRELATION",
        expected=False,
        detail=f"订单命令 {client_id} 缺少策略订单关联",
      )
    )
  for client_id in sorted(correlation_by_client.keys() - pending_by_client.keys()):
    reasons.append(
      CandidateTraceMissingReason(
        code="PENDING_ORDER_NOT_FOUND",
        stage="PENDING_ORDER",
        expected=False,
        detail=f"策略订单关联 {client_id} 缺少订单命令",
      )
    )

  normalized_broker_ids = {
    _canonical_broker_order_id(value) for value in raw_broker_order_ids
  } - {""}
  persisted_order_ids = {str(row.id) for row in orders}
  pending_status_by_broker = {
    _canonical_broker_order_id(row.broker_order_id): str(row.status or "").upper()
    for row in pending_orders
    if row.broker_order_id
  }
  for raw_id in sorted(raw_broker_order_ids):
    if _broker_order_number(raw_id) is None:
      status = pending_status_by_broker.get(raw_id, "")
      reasons.append(
        CandidateTraceMissingReason(
          code="BROKER_ORDER_ID_NOT_NUMERIC",
          stage="BROKER_ORDER",
          expected=status not in _PENDING_BROKER_CLAIM_STATUSES,
          detail=f"Broker 委托标识 {raw_id} 无法关联整数型权威委托表",
        )
      )
  for broker_id in sorted(normalized_broker_ids - persisted_order_ids):
    status = pending_status_by_broker.get(broker_id, "")
    reasons.append(
      CandidateTraceMissingReason(
        code="BROKER_ORDER_FACT_NOT_FOUND",
        stage="BROKER_ORDER",
        expected=status not in _PENDING_BROKER_CLAIM_STATUSES,
        detail=f"Broker 委托 {broker_id} 尚无权威委托事实",
      )
    )

  trades_by_order = {str(row.order_id) for row in trades}
  for order in orders:
    broker_id = str(order.id)
    if broker_id in trades_by_order:
      continue
    filled_claimed = int(order.traded_volume or 0) > 0 or (
      _enum_text(order.status) in _FILLED_ORDER_STATUSES
    )
    reasons.append(
      CandidateTraceMissingReason(
        code="TRADE_FACT_NOT_FOUND",
        stage="BROKER_TRADE",
        expected=not filled_claimed,
        detail=(
          f"Broker 委托 {broker_id} 尚未成交"
          if not filled_claimed
          else f"Broker 委托 {broker_id} 已声明成交但成交事实缺失"
        ),
      )
    )

  batch_expected = bool(pending_orders or execution_claimed)
  if candidate_intents and not batches:
    reasons.append(
      CandidateTraceMissingReason(
        code="T_TRADE_BATCH_NOT_FOUND",
        stage="T_TRADE_BATCH",
        expected=not batch_expected,
        detail="做 T 批次尚未创建"
        if not batch_expected
        else "候选已声明批次但批次记录缺失",
      )
    )
  intent_ids = {str(row.id) for row in relevant_intents}
  client_ids = {str(row.client_order_id) for row in pending_orders}
  for batch in batches:
    for intent_id in (batch.entry_intent_id, batch.exit_intent_id):
      if intent_id and str(intent_id) not in intent_ids:
        reasons.append(
          CandidateTraceMissingReason(
            code="BATCH_INTENT_NOT_FOUND",
            stage="TRADE_INTENT",
            expected=False,
            detail=f"批次 {batch.batch_id} 关联意图 {intent_id} 不存在",
          )
        )
    for client_id in (batch.entry_client_order_id, batch.exit_client_order_id):
      if client_id and str(client_id) not in client_ids:
        reasons.append(
          CandidateTraceMissingReason(
            code="BATCH_ORDER_COMMAND_NOT_FOUND",
            stage="PENDING_ORDER",
            expected=False,
            detail=f"批次 {batch.batch_id} 关联订单命令 {client_id} 不存在",
          )
        )

  entry_broker_ids = {
    _canonical_broker_order_id(row.broker_order_id)
    for row in (*pending_orders, *correlations)
    if str(row.t_trade_role or "").upper() == "ENTRY" and row.broker_order_id
  }
  entry_filled = any(str(row.order_id) in entry_broker_ids for row in trades) or any(
    int(row.entry_filled_volume or 0) > 0 for row in batches
  )
  if (candidate_intents or batches) and not plans:
    reasons.append(
      CandidateTraceMissingReason(
        code="AUTO_EXIT_PLAN_NOT_FOUND",
        stage="AUTO_EXIT_PLAN",
        expected=not entry_filled,
        detail=(
          "入场尚未成交，退出计划尚未建立"
          if not entry_filled
          else "入场已成交但自动退出计划缺失"
        ),
      )
    )
  plan_event_plan_ids = {str(row.plan_id) for row in plan_events}
  for plan in plans:
    if str(plan.plan_id) not in plan_event_plan_ids:
      reasons.append(
        CandidateTraceMissingReason(
          code="AUTO_EXIT_PLAN_EVENT_NOT_FOUND",
          stage="AUTO_EXIT_PLAN_EVENT",
          expected=False,
          detail=f"自动退出计划 {plan.plan_id} 缺少持久化事件",
        )
      )
    if (
      plan.pending_client_order_id
      and str(plan.pending_client_order_id) not in client_ids
    ):
      reasons.append(
        CandidateTraceMissingReason(
          code="EXIT_PLAN_ORDER_COMMAND_NOT_FOUND",
          stage="PENDING_ORDER",
          expected=False,
          detail=f"自动退出计划 {plan.plan_id} 关联订单命令缺失",
        )
      )
  return _deduplicate_reasons(reasons)


def _source_identity(snapshot: Mapping[str, Any]) -> CandidateTraceSourceIdentity:
  return CandidateTraceSourceIdentity(
    source_time_ms=_optional_int(snapshot.get("source_time_ms")),
    tick_ordinal=_optional_int(snapshot.get("tick_ordinal")),
    continuity_generation=_optional_text(snapshot.get("continuity_generation")),
    trade_date=_optional_text(snapshot.get("trade_date")),
    candidate_fingerprint=_optional_text(snapshot.get("candidate_fingerprint")),
    policy_version=_optional_text(snapshot.get("policy_version")),
    feature_schema_version=_optional_text(snapshot.get("feature_schema_version")),
    profile_version=_optional_text(
      snapshot.get("profile_version") or snapshot.get("reference_profile_version")
    ),
  )


def _snapshot(row: TTradeOpportunityEvaluation) -> dict[str, Any]:
  payload = row.payload if isinstance(row.payload, Mapping) else {}
  snapshot = payload.get("signal_snapshot")
  return dict(snapshot) if isinstance(snapshot, Mapping) else {}


def _metadata(value: Any) -> dict[str, Any]:
  return dict(value) if isinstance(value, Mapping) else {}


def _metadata_candidate_id(value: Any) -> str:
  return str(_metadata(value).get("candidate_id") or "")


def _metadata_text(value: Any, key: str) -> str:
  return str(_metadata(value).get(key) or "").strip()


def _safe_plan_event_details(payload: Mapping[str, Any]) -> dict[str, Any]:
  allowed = (
    "status",
    "phase",
    "source_type",
    "config_version",
    "incoming_template_version",
    "entry_filled_volume",
    "exit_filled_volume",
    "protected_volume",
    "remaining_volume",
    "monitor_enabled",
    "reason",
  )
  return _compact({key: payload.get(key) for key in allowed})


def _related_ids(**values: Any) -> Mapping[str, tuple[str, ...]]:
  related: dict[str, tuple[str, ...]] = {}
  for key, value in values.items():
    if isinstance(value, (list, tuple, set, frozenset)):
      normalized = _ids(value)
    else:
      normalized = _ids((value,))
    if normalized:
      related[key] = normalized
  return related


def _ids(values: Iterable[Any]) -> tuple[str, ...]:
  normalized = {str(value).strip() for value in values if str(value or "").strip()}
  return tuple(sorted(normalized, key=_identifier_sort_key))


def _identifier_sort_key(value: str) -> tuple[int, Any, str]:
  try:
    return 0, int(value), value
  except (TypeError, ValueError, OverflowError):
    return 1, value, value


def _broker_order_number(value: Any) -> Optional[int]:
  normalized = str(value or "").strip()
  if not normalized or not normalized.isdecimal():
    return None
  number = int(normalized)
  return number if number >= 0 else None


def _canonical_broker_order_id(value: Any) -> str:
  number = _broker_order_number(value)
  return str(number) if number is not None else str(value or "").strip()


def _event_sort_key(event: CandidateTraceEvent) -> tuple[float, int, str, str]:
  occurred_at = event.occurred_at
  if occurred_at.tzinfo is None:
    occurred_at = occurred_at.replace(tzinfo=timezone.utc)
  return (
    occurred_at.timestamp(),
    _STAGE_ORDER.get(event.stage, 999),
    event.entity_id,
    event.event_type,
  )


def _deduplicate_reasons(
  reasons: Sequence[CandidateTraceMissingReason],
) -> tuple[CandidateTraceMissingReason, ...]:
  unique = {
    (item.code, item.stage, item.expected, item.detail): item for item in reasons
  }
  return tuple(
    sorted(
      unique.values(),
      key=lambda item: (
        _STAGE_ORDER.get(item.stage, 999),
        item.code,
        item.detail,
      ),
    )
  )


def _enum_text(value: Any) -> Optional[str]:
  if value is None:
    return None
  if isinstance(value, Enum):
    return str(value.name)
  return str(value)


def _compact(values: Mapping[str, Any]) -> dict[str, Any]:
  return {key: value for key, value in values.items() if value is not None}


def _iso(value: Any) -> Optional[str]:
  return value.isoformat() if isinstance(value, datetime) else None


def _required_datetime(value: Any) -> datetime:
  if not isinstance(value, datetime):
    raise ValueError("候选追溯事实缺少权威时间")
  return value


def _required_text(value: Any, label: str, maximum: int) -> str:
  normalized = str(value or "").strip()
  if not normalized:
    raise ValueError(f"{label}不能为空")
  if len(normalized) > maximum:
    raise ValueError(f"{label}长度不能超过 {maximum}")
  return normalized


def _optional_text(value: Any) -> Optional[str]:
  normalized = str(value or "").strip()
  return normalized or None


def _optional_int(value: Any) -> Optional[int]:
  try:
    return int(value) if value is not None else None
  except (TypeError, ValueError, OverflowError):
    return None


__all__ = [
  "CandidateTrace",
  "CandidateTraceEvent",
  "CandidateTraceLinks",
  "CandidateTraceMissingReason",
  "CandidateTraceSourceIdentity",
  "TTradeCandidateTraceService",
]
