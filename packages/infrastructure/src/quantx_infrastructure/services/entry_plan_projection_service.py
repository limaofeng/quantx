"""Read model for strategy-backed managed entry plans.

The product ``plan_id`` is the underlying ``StrategyRun.id``.  This service
only projects existing truth; it never advances a plan or treats an intent,
outbox acknowledgement, or broker order as a fill.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import PendingTradeOrder
from quantx_infrastructure.models.entry_plan_authorization import (
  EntryPlanAuthorizationEvent,
)
from quantx_infrastructure.models.instrument import Instrument
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.strategy import Strategy
from quantx_infrastructure.models.strategy_decision_trace_record import (
  StrategyDecisionTraceRecord,
)
from quantx_infrastructure.models.strategy_run import StrategyRun
from quantx_infrastructure.models.strategy_run_state import StrategyRunState
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord

MANAGED_ENTRY_STRATEGY_CLASS_NAME = "AshareManagedEntryPlanStrategy"
_ACTIVE_RUN_STATUSES = {"pending", "running", "paused"}
_WORKING_ORDER_STATUSES = {
  "QUEUED",
  "DELIVERED",
  "SUBMITTED",
  "ACCEPTED",
  "PENDING",
  "PARTIAL_FILLED",
  "PARTIALLY_FILLED",
  "RECONCILE_REQUIRED",
  "CANCEL_REQUESTED",
}


def _mapping(value: Any) -> dict[str, Any]:
  return dict(value) if isinstance(value, dict) else {}


def _enum_value(value: Any) -> str:
  return str(getattr(value, "value", value) or "")


def _iso(value: Any) -> Optional[str]:
  return value.isoformat() if isinstance(value, datetime) else None


def _number(value: Any, default: float = 0.0) -> float:
  try:
    result = float(value)
  except (TypeError, ValueError, OverflowError):
    return default
  return result if result == result else default


def _integer(value: Any, default: int = 0) -> int:
  try:
    return int(value)
  except (TypeError, ValueError, OverflowError):
    return default


class EntryPlanProjectionService:
  """Compose EntryPlan views from StrategyRun and durable execution facts."""

  async def list(
    self,
    account_id: str,
    *,
    instrument_code: str = "",
    statuses: Optional[Iterable[str]] = None,
  ) -> list[dict[str, Any]]:
    normalized_account = str(account_id or "").strip()
    if not normalized_account:
      raise ValueError("账户不能为空")
    normalized_code = str(instrument_code or "").strip().upper()
    wanted_statuses = {
      str(status or "").strip().upper() for status in statuses or [] if status
    }
    async with AsyncSessionLocal() as db:
      runs = list(
        (
          await db.execute(
            select(StrategyRun)
            .join(StrategyRun.strategy)
            .options(selectinload(StrategyRun.strategy))
            .where(Strategy.class_name == MANAGED_ENTRY_STRATEGY_CLASS_NAME)
            .order_by(StrategyRun.updated_at.desc(), StrategyRun.id.desc())
          )
        )
        .scalars()
        .all()
      )
      runs = [
        run
        for run in runs
        if str(_mapping(run.parameters).get("account_id") or "")
        == normalized_account
        and (
          not normalized_code
          or normalized_code
          in {str(code or "").upper() for code in list(run.instruments or [])}
        )
      ]
      views = [await self._project(db, run) for run in runs]
    if wanted_statuses:
      views = [view for view in views if view["phase"] in wanted_statuses]
    return views

  async def get(
    self,
    plan_id: str,
    *,
    account_id: str = "",
  ) -> Optional[dict[str, Any]]:
    normalized_plan_id = str(plan_id or "").strip()
    if not normalized_plan_id:
      return None
    async with AsyncSessionLocal() as db:
      run = (
        await db.execute(
          select(StrategyRun)
          .join(StrategyRun.strategy)
          .options(selectinload(StrategyRun.strategy))
          .where(
            StrategyRun.id == normalized_plan_id,
            Strategy.class_name == MANAGED_ENTRY_STRATEGY_CLASS_NAME,
          )
        )
      ).scalar_one_or_none()
      if run is None:
        return None
      bound_account = str(_mapping(run.parameters).get("account_id") or "")
      if account_id and bound_account != str(account_id):
        return None
      return await self._project(db, run)

  async def pending_intents(
    self,
    account_id: str,
    *,
    instrument_code: str = "",
  ) -> list[dict[str, Any]]:
    plans = await self.list(account_id, instrument_code=instrument_code)
    run_ids = [plan["plan_id"] for plan in plans]
    if not run_ids:
      return []
    async with AsyncSessionLocal() as db:
      rows = list(
        (
          await db.execute(
            select(TradeIntentRecord)
            .where(
              TradeIntentRecord.strategy_run_id.in_(run_ids),
              TradeIntentRecord.direction == "BUY",
              TradeIntentRecord.status == "AWAITING_APPROVAL",
            )
            .order_by(
              TradeIntentRecord.created_at.desc(), TradeIntentRecord.id.desc()
            )
          )
        )
        .scalars()
        .all()
      )
    return [self._intent_view(row) for row in rows]

  async def events(
    self,
    plan_id: str,
    *,
    account_id: str,
    limit: int = 100,
  ) -> list[dict[str, Any]]:
    plan = await self.get(plan_id, account_id=account_id)
    if plan is None:
      return []
    safe_limit = max(1, min(int(limit or 100), 300))
    async with AsyncSessionLocal() as db:
      intents = list(
        (
          await db.execute(
            select(TradeIntentRecord)
            .where(TradeIntentRecord.strategy_run_id == plan_id)
            .order_by(
              TradeIntentRecord.created_at.desc(), TradeIntentRecord.id.desc()
            )
            .limit(safe_limit)
          )
        )
        .scalars()
        .all()
      )
      traces = list(
        (
          await db.execute(
            select(StrategyDecisionTraceRecord)
            .where(StrategyDecisionTraceRecord.strategy_run_id == plan_id)
            .order_by(
              StrategyDecisionTraceRecord.decided_at.desc(),
              StrategyDecisionTraceRecord.id.desc(),
            )
            .limit(safe_limit)
          )
        )
        .scalars()
        .all()
      )
      authorization_events = list(
        (
          await db.execute(
            select(EntryPlanAuthorizationEvent)
            .where(EntryPlanAuthorizationEvent.plan_id == plan_id)
            .order_by(
              EntryPlanAuthorizationEvent.created_at.desc(),
              EntryPlanAuthorizationEvent.event_id.desc(),
            )
            .limit(safe_limit)
          )
        )
        .scalars()
        .all()
      )
    events: list[dict[str, Any]] = [
      {
        "event_id": f"plan-created:{plan_id}",
        "plan_id": plan_id,
        "event_type": "ENTRY_PLAN_CREATED",
        "occurred_at": plan["created_at"],
        "reason_code": "",
        "message": "买入托管计划已创建",
        "details": {},
      }
    ]
    for intent in intents:
      status = str(intent.status or "PENDING").upper()
      events.append(
        {
          "event_id": f"intent:{intent.id}:{status}",
          "plan_id": plan_id,
          "event_type": self._intent_event_type(status),
          "occurred_at": _iso(intent.updated_at or intent.created_at),
          "reason_code": str(intent.notes or intent.reason or ""),
          "message": self._intent_event_message(status),
          "details": {
            "intent_id": str(intent.id),
            "requested_amount_cny": _number(intent.target_amount),
            "requested_volume": _integer(intent.target_volume),
            "executed_volume": _integer(intent.executed_volume),
            "executed_price": _number(intent.executed_price),
          },
        }
      )
    for trace in traces:
      decision = _mapping(trace.decision_trace)
      output = _mapping(trace.output_summary)
      reason_code = str(
        decision.get("reason")
        or output.get("reason")
        or output.get("reason_code")
        or "ENTRY_PLAN_EVALUATED"
      )
      events.append(
        {
          "event_id": f"trace:{trace.id}",
          "plan_id": plan_id,
          "event_type": "ENTRY_PLAN_EVALUATED",
          "occurred_at": _iso(trace.decided_at),
          "reason_code": reason_code,
          "message": "系统已按最新行情和风控重新评估计划",
          "details": {
            "trace_id": str(trace.trace_id),
            "tags": list(output.get("decision_tags") or []),
          },
        }
      )
    for authorization_event in authorization_events:
      event_type = str(authorization_event.event_type or "")
      events.append(
        {
          "event_id": str(authorization_event.event_id),
          "plan_id": plan_id,
          "event_type": event_type,
          "occurred_at": _iso(authorization_event.created_at),
          "reason_code": str(authorization_event.reason_code or ""),
          "message": self._authorization_event_message(event_type),
          "details": {},
        }
      )
    events.sort(
      key=lambda item: (str(item.get("occurred_at") or ""), item["event_id"]),
      reverse=True,
    )
    return events[:safe_limit]

  @staticmethod
  def capabilities() -> dict[str, Any]:
    """Versioned server capability metadata consumed by the card editor."""

    return {
      "version": "managed-entry-v1",
      "target_modes": [
        {
          "value": "TARGET_POSITION_PCT",
          "label": "目标仓位比例",
          "description": "买到成交后的最终总仓位，只补当前正缺口。",
        },
        {
          "value": "INCREMENTAL_AMOUNT_CNY",
          "label": "计划新增金额",
          "description": "限定本计划累计最多新增投入的人民币金额。",
        },
        {
          "value": "ADDITIONAL_VOLUME",
          "label": "计划新增股数",
          "description": "限定本计划累计最多新增的股份数量。",
        },
      ],
      "rule_types": [
        {
          "rule_type": "TREND_PULLBACK_CONFIRMATION",
          "label": "趋势回撤建仓",
          "category": "TREND",
          "description": "先自动确认上涨趋势，再等待健康回撤和重新转强。",
          "suitable_for": "希望跟随趋势但避免瞬时追高",
          "warning": "趋势失效、数据陈旧或超过最高买价时不会买入。",
          "fields": [
            {
              "key": "fast_ema_period",
              "label": "快线周期",
              "type": "INTEGER",
              "unit": "交易日",
              "required": True,
              "min": 2,
              "max": 250,
              "step": 1,
              "default_value": 10,
              "help_text": "用于确认短期趋势，必须小于慢线周期。",
              "advanced": True,
            },
            {
              "key": "slow_ema_period",
              "label": "慢线周期",
              "type": "INTEGER",
              "unit": "交易日",
              "required": True,
              "min": 3,
              "max": 500,
              "step": 1,
              "default_value": 30,
              "help_text": "用于确认中期趋势和斜率。",
              "advanced": True,
            },
            {
              "key": "pullback_pct",
              "label": "健康回撤",
              "type": "NUMBER",
              "unit": "%",
              "required": True,
              "min": 0.1,
              "max": 30,
              "step": 0.1,
              "default_value": 2,
              "help_text": "上涨趋势中至少回撤到该幅度后才等待企稳。",
              "advanced": False,
            },
            {
              "key": "rebound_pct",
              "label": "重新转强",
              "type": "NUMBER",
              "unit": "%",
              "required": True,
              "min": 0.1,
              "max": 30,
              "step": 0.1,
              "default_value": 0.8,
              "help_text": "相对本轮回撤低点反弹到该幅度才触发。",
              "advanced": False,
            },
          ],
          "presets": [
            {
              "preset_id": "CONSERVATIVE",
              "label": "稳健",
              "summary": "确认更充分、批次更小、等待更久",
              "parameters": {
                "fast_ema_period": 20,
                "slow_ema_period": 60,
                "pullback_pct": 3.0,
                "rebound_pct": 1.0,
              },
            },
            {
              "preset_id": "BALANCED",
              "label": "均衡",
              "summary": "兼顾确认强度与入场效率",
              "parameters": {
                "fast_ema_period": 10,
                "slow_ema_period": 30,
                "pullback_pct": 2.0,
                "rebound_pct": 0.8,
              },
            },
            {
              "preset_id": "ACTIVE",
              "label": "积极",
              "summary": "更早响应，但仍受全部硬上限约束",
              "parameters": {
                "fast_ema_period": 5,
                "slow_ema_period": 20,
                "pullback_pct": 1.0,
                "rebound_pct": 0.3,
              },
            },
          ],
        },
        {
          "rule_type": "PRICE_LADDER",
          "label": "价格阶梯建仓",
          "category": "PRICE",
          "description": "价格到达预设档位时逐档处理，每次只处理一批。",
          "suitable_for": "已经明确可接受价格区间",
          "warning": "跳空跨档不会同时发出多张订单。",
          "fields": [
            {
              "key": "levels",
              "label": "价格档位",
              "type": "PRICE_LADDER",
              "unit": "元 / 人民币",
              "required": True,
              "min": None,
              "max": None,
              "step": 0.01,
              "default_value": [],
              "help_text": "每档填写触发价与本档预算；总预算不能超过计划上限。",
              "advanced": False,
            }
          ],
          "presets": [],
        },
        {
          "rule_type": "MANUAL_TRIGGER",
          "label": "人工触发",
          "category": "MANUAL",
          "description": "系统保存目标和风险边界，由你决定本批检查时点。",
          "suitable_for": "先验证资金和执行链路",
          "warning": "点击检查仍会重新经过实时行情和全部风控。",
          "fields": [],
          "presets": [],
        },
      ],
      "allowed_buckets": ["core", "swing"],
      "environments": ["PAPER", "LIVE"],
      "authorization_modes": ["MANUAL_CONFIRM", "AUTO"],
      "max_open_orders": 1,
    }

  async def automation_status(self, account_id: str) -> dict[str, Any]:
    """Read the persisted global auto-entry pause gate when available."""

    try:
      from quantx_infrastructure.services.entry_plan_authorization_service import (
        EntryPlanAuthorizationService,
      )
    except ImportError:
      return {
        "account_id": account_id,
        "paused": True,
        "reason": "ENTRY_AUTOMATION_GATE_UNAVAILABLE",
        "updated_at": None,
      }
    async with AsyncSessionLocal() as db:
      gate = await EntryPlanAuthorizationService(db).get_gate(account_id)
    return {
      "account_id": account_id,
      "paused": bool(getattr(gate, "paused", False)) if gate else False,
      "reason": str(getattr(gate, "reason", "") or "") if gate else "",
      "updated_at": _iso(getattr(gate, "changed_at", None)) if gate else None,
    }

  async def _project(self, db: Any, run: StrategyRun) -> dict[str, Any]:
    parameters = _mapping(run.parameters)
    config = _mapping(parameters.get("managed_entry_plan")) or parameters
    instrument_code = str(
      config.get("instrument_code")
      or (list(run.instruments or [""])[0] if run.instruments else "")
    ).upper()
    state_record = await db.scalar(
      select(StrategyRunState).where(StrategyRunState.run_id == run.id)
    )
    custom_state = _mapping(getattr(state_record, "custom_state", {}))
    state = _mapping(custom_state.get("managed_entry_plan")) or custom_state
    intents = list(
      (
        await db.execute(
          select(TradeIntentRecord)
          .where(
            TradeIntentRecord.strategy_run_id == run.id,
            TradeIntentRecord.direction == "BUY",
          )
          .order_by(TradeIntentRecord.created_at.desc())
        )
      )
      .scalars()
      .all()
    )
    working_orders = list(
      (
        await db.execute(
          select(PendingTradeOrder).where(
            PendingTradeOrder.strategy_run_id == run.id,
            PendingTradeOrder.side == "BUY",
            PendingTradeOrder.status.in_(tuple(_WORKING_ORDER_STATUSES)),
          )
        )
      )
      .scalars()
      .all()
    )
    account_id = str(parameters.get("account_id") or config.get("account_id") or "")
    position = await db.scalar(
      select(Position).where(
        Position.account_id == account_id,
        Position.stock_code == instrument_code,
      )
    )
    instrument = await db.get(Instrument, instrument_code)
    target_policy = _mapping(config.get("target_policy"))
    pacing = _mapping(config.get("pacing_policy"))
    execution = _mapping(config.get("execution_policy"))
    completion = _mapping(config.get("completion_policy"))
    trigger_rules = [
      self._rule_view(_mapping(rule))
      for rule in list(config.get("trigger_rules") or [])
    ]
    exit_protection = self._exit_protection_view(
      _mapping(config.get("exit_plan_template"))
    )
    baseline = _mapping(target_policy.get("baseline_snapshot"))
    filled_volume = sum(max(0, _integer(row.executed_volume)) for row in intents)
    filled_amount = sum(
      max(0, _integer(row.executed_volume)) * max(0.0, _number(row.executed_price))
      for row in intents
    )
    pending_amount = sum(
      max(0, _integer(row.volume)) * max(0.0, _number(row.limit_price))
      for row in working_orders
    )
    max_total_amount = max(0.0, _number(target_policy.get("max_total_amount_cny")))
    last_intent = intents[0] if intents else None
    entry_enabled = parameters.get("entry_plan_enabled") is True
    phase = self._phase(
      run,
      state,
      last_intent,
      bool(working_orders),
      entry_enabled=entry_enabled,
    )
    current_volume = max(0, _integer(getattr(position, "volume", 0)))
    baseline_volume = max(0, _integer(baseline.get("position_volume")))
    plan_kind = "BUILD" if baseline_volume == 0 else "ADD"
    return {
      "plan_id": str(run.id),
      "config_version": max(1, _integer(config.get("config_version"), 1)),
      "account_id": account_id,
      "instrument_code": instrument_code,
      "instrument_name": str(
        getattr(instrument, "name", None)
        or getattr(position, "instrument_name", None)
        or instrument_code
      ),
      "bucket": str(config.get("bucket") or "core"),
      "plan_kind": plan_kind,
      "phase": phase,
      "run_status": _enum_value(run.status).upper(),
      "environment": _enum_value(run.mode).upper(),
      "authorization_mode": str(
        execution.get("authorization_mode") or "MANUAL_CONFIRM"
      ).upper(),
      "authorization_state": await self._authorization_state(
        db, run, config, execution
      ),
      "target_mode": str(target_policy.get("mode") or "TARGET_POSITION_PCT"),
      "target_position_pct": _number(target_policy.get("target_position_pct")),
      "incremental_amount_cny": _number(
        target_policy.get("incremental_amount_cny")
      ),
      "additional_volume": _integer(target_policy.get("additional_volume")),
      "max_total_amount_cny": max_total_amount,
      "max_position_pct": _number(target_policy.get("max_position_pct")),
      "current_position_volume": current_volume,
      "baseline_position_volume": baseline_volume,
      "current_market_value_cny": _number(getattr(position, "market_value", 0.0)),
      "filled_volume": filled_volume,
      "filled_amount_cny": filled_amount,
      "remaining_amount_cny": max(
        0.0, max_total_amount - filled_amount - pending_amount
      ),
      "pending_reserved_amount_cny": pending_amount,
      "max_single_intent_amount_cny": _number(
        pacing.get("max_single_intent_amount_cny")
      ),
      "max_daily_filled_amount_cny": _number(
        pacing.get("max_daily_filled_amount_cny")
      ),
      "max_buy_price": _number(completion.get("max_buy_price")),
      "rule_types": [
        str(_mapping(rule).get("rule_type") or "")
        for rule in list(config.get("trigger_rules") or [])
        if _mapping(rule).get("enabled", True)
      ],
      "trigger_rules": trigger_rules,
      "pacing_policy": {
        "tranche_count": _integer(pacing.get("tranche_count"), 1),
        "max_single_intent_amount_cny": _number(
          pacing.get("max_single_intent_amount_cny")
        ),
        "max_daily_filled_amount_cny": _number(
          pacing.get("max_daily_filled_amount_cny")
        ),
        "max_orders_per_day": _integer(pacing.get("max_orders_per_day"), 1),
        "cash_buffer_pct": _number(pacing.get("cash_buffer_pct")),
        "min_interval_seconds": _integer(pacing.get("min_interval_seconds")),
        "cooldown_after_reject_seconds": _integer(
          pacing.get("cooldown_after_reject_seconds")
        ),
        "trend_adjustment_enabled": bool(
          pacing.get("trend_adjustment_enabled", True)
        ),
      },
      "execution_policy": {
        "price_reference": str(
          execution.get("price_reference") or "ASK1_PROTECTED_LIMIT"
        ),
        "max_slippage_bps": _number(execution.get("max_slippage_bps")),
        "max_price_deviation_bps": _number(
          execution.get("max_price_deviation_bps")
        ),
        "approval_ttl_ms": _integer(execution.get("approval_ttl_ms"), 60_000),
      },
      "completion_policy": {
        "expire_at_ms": completion.get("expire_at_ms"),
        "max_buy_price": _number(completion.get("max_buy_price")),
        "stop_when_target_reached": bool(
          completion.get("stop_when_target_reached", True)
        ),
        "stop_when_budget_exhausted": bool(
          completion.get("stop_when_budget_exhausted", True)
        ),
        "cancel_unsubmitted_on_expiry": bool(
          completion.get("cancel_unsubmitted_on_expiry", True)
        ),
      },
      "exit_protection": exit_protection,
      "entry_enabled": entry_enabled,
      "note": str(parameters.get("entry_plan_note") or ""),
      "last_reason_code": str(
        state.get("last_reason_code")
        or _mapping(state.get("last_decision")).get("reason")
        or getattr(last_intent, "notes", "")
        or ""
      ),
      "pending_intent_id": str(
        state.get("pending_intent_id")
        or (last_intent.id if last_intent and last_intent.status == "AWAITING_APPROVAL" else "")
        or ""
      ),
      "has_working_order": bool(working_orders),
      "next_eligible_at": state.get("retry_after_ms"),
      "expire_at": completion.get("expire_at_ms"),
      "has_exit_protection": bool(config.get("exit_plan_template")),
      "blocked_reasons": list(state.get("blocked_reasons") or []),
      "created_at": _iso(run.created_at),
      "updated_at": _iso(run.updated_at),
    }

  async def _authorization_state(
    self,
    db: Any,
    run: StrategyRun,
    config: dict[str, Any],
    execution: dict[str, Any],
  ) -> str:
    if _enum_value(run.mode).upper() != "LIVE":
      return "NOT_REQUIRED"
    if str(execution.get("authorization_mode") or "MANUAL_CONFIRM").upper() != "AUTO":
      return "MANUAL_CONFIRM"
    try:
      from quantx_infrastructure.core.utils import time_utils
      from quantx_infrastructure.repositories.entry_plan_authorization_repository import (
        EntryPlanAuthorizationRepository,
      )
      from quantx_infrastructure.services.entry_plan_authorization_service import (
        account_fingerprint,
        scope_from_managed_entry_config,
      )
    except ImportError:
      return "REQUIRED"
    repository = EntryPlanAuthorizationRepository(db)
    grant = await repository.find_current_for_plan(str(run.id))
    if grant is None:
      return "REQUIRED"
    if getattr(grant, "revoked_at", None) is not None:
      return "REVOKED"
    if getattr(grant, "invalidated_at", None) is not None:
      return "INVALID"
    if time_utils.to_shanghai(grant.expires_at) <= time_utils.now():
      return "EXPIRED"
    if int(getattr(grant, "config_version", 0) or 0) != int(
      config.get("config_version", 1) or 1
    ):
      return "STALE"
    expected = scope_from_managed_entry_config(plan_id=str(run.id), config=config)
    if (
      str(grant.plan_fingerprint) != expected.plan_fingerprint
      or str(grant.rule_fingerprint) != expected.rule_fingerprint
    ):
      return "STALE"
    account_id = str(_mapping(run.parameters).get("account_id") or "")
    gate = await repository.find_gate(account_fingerprint(account_id))
    if gate is not None and bool(gate.paused):
      return "PAUSED"
    return "AUTHORIZED"

  @staticmethod
  def _phase(
    run: StrategyRun,
    state: dict[str, Any],
    last_intent: Optional[TradeIntentRecord],
    has_working_order: bool,
    *,
    entry_enabled: bool,
  ) -> str:
    state_phase = str(state.get("phase") or state.get("status") or "").upper()
    if state_phase in {
      "COMPLETED",
      "EXPIRED",
      "CANCELLED",
      "ERROR",
      "RECONCILE_REQUIRED",
      "DRAINING",
    }:
      return state_phase
    if has_working_order:
      return "ENTRY_PENDING"
    if last_intent and str(last_intent.status or "").upper() == "AWAITING_APPROVAL":
      return "AWAITING_APPROVAL"
    if not entry_enabled:
      return "PAUSED"
    run_status = _enum_value(run.status).lower()
    if run_status == "paused" or run_status == "pending":
      return "PAUSED"
    if run_status in {"stopped", "completed"}:
      return state_phase or "COMPLETED"
    if run_status == "error":
      return "ERROR"
    return state_phase or "ARMED"

  @staticmethod
  def _rule_view(rule: dict[str, Any]) -> dict[str, Any]:
    parameters = _mapping(rule.get("parameters"))
    levels = [
      {
        "level_id": str(level.get("level_id") or ""),
        "trigger_price": _number(level.get("trigger_price")),
        "tranche_amount_cny": (
          _number(level.get("tranche_amount_cny"))
          if level.get("tranche_amount_cny") is not None
          else None
        ),
        "tranche_volume": (
          _integer(level.get("tranche_volume"))
          if level.get("tranche_volume") is not None
          else None
        ),
        "priority": _integer(level.get("priority")),
      }
      for level in (
        _mapping(item) for item in list(parameters.get("levels") or [])
      )
    ]
    return {
      "rule_id": str(rule.get("rule_id") or ""),
      "rule_type": str(rule.get("rule_type") or ""),
      "priority": _integer(rule.get("priority")),
      "enabled": bool(rule.get("enabled", True)),
      "once": bool(rule.get("once", False)),
      "preset_id": str(parameters.get("preset_id") or ""),
      "min_pullback_pct": (
        _number(parameters.get("pullback_pct"))
        if parameters.get("pullback_pct") is not None
        else None
      ),
      "max_pullback_pct": (
        _number(parameters.get("max_pullback_pct"))
        if parameters.get("max_pullback_pct") is not None
        else None
      ),
      "rebound_confirmation_pct": (
        _number(parameters.get("rebound_pct"))
        if parameters.get("rebound_pct") is not None
        else None
      ),
      "fast_ema_period": (
        _integer(parameters.get("fast_ema_period"))
        if parameters.get("fast_ema_period") is not None
        else None
      ),
      "slow_ema_period": (
        _integer(parameters.get("slow_ema_period"))
        if parameters.get("slow_ema_period") is not None
        else None
      ),
      "manual_trigger_sequence": (
        _integer(parameters.get("trigger_sequence"))
        if parameters.get("trigger_sequence") is not None
        else None
      ),
      "ladder_levels": levels,
    }

  @staticmethod
  def _exit_protection_view(template: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
      "enabled": bool(template),
      "stop_price": None,
      "gross_take_profit_pct": None,
      "trailing_arm_profit_pct": None,
      "trailing_drawdown_pct": None,
      "max_holding_days": None,
    }
    for raw_rule in list(template.get("rules") or []):
      rule = _mapping(raw_rule)
      strategy = str(rule.get("strategy") or "").upper()
      parameters = _mapping(rule.get("parameters"))
      if strategy == "STOP_PRICE":
        result["stop_price"] = _number(parameters.get("stop_price"))
      elif strategy == "GROSS_TAKE_PROFIT":
        result["gross_take_profit_pct"] = _number(
          parameters.get("target_profit_pct")
        )
      elif strategy == "TRAILING_NET_PROFIT":
        result["trailing_arm_profit_pct"] = _number(
          parameters.get("target_profit_pct")
        )
        result["trailing_drawdown_pct"] = _number(
          parameters.get("initial_gap_pct")
        )
      elif strategy == "MAX_HOLDING_DAYS":
        result["max_holding_days"] = _integer(
          parameters.get("max_holding_trading_days")
        )
    return result

  @staticmethod
  def _intent_view(row: TradeIntentRecord) -> dict[str, Any]:
    metadata = _mapping(row.intent_metadata)
    expires_at_ms = _integer(_mapping(metadata.get("expiry_policy")).get("expire_at_ms"))
    return {
      "intent_id": str(row.id),
      "plan_id": str(row.strategy_run_id or row.owner_id or ""),
      "instrument_code": str(row.instrument_code or ""),
      "bucket": str(row.bucket or "core"),
      "reason_code": str(row.reason or ""),
      "status": str(row.status or ""),
      "target_amount_cny": _number(row.target_amount),
      "target_volume": _integer(row.target_volume),
      "signal_price": _number(row.limit_price_hint),
      "current_price": 0.0,
      "price_deviation_bps": 0.0,
      "expires_at_ms": expires_at_ms,
      "risk_action": str(metadata.get("risk_action") or "PENDING_RECHECK"),
      "created_at": _iso(row.created_at),
    }

  @staticmethod
  def _intent_event_type(status: str) -> str:
    return {
      "AWAITING_APPROVAL": "ENTRY_INTENT_AWAITING_APPROVAL",
      "REJECTED": "ENTRY_INTENT_REJECTED",
      "DELAYED": "ENTRY_INTENT_DELAYED",
      "PARTIAL_FILLED": "ENTRY_ORDER_PARTIALLY_FILLED",
      "PARTIALLY_FILLED": "ENTRY_ORDER_PARTIALLY_FILLED",
      "FILLED": "ENTRY_ORDER_FILLED",
      "CANCELLED": "ENTRY_ORDER_CANCELED",
    }.get(status, "ENTRY_INTENT_CREATED")

  @staticmethod
  def _intent_event_message(status: str) -> str:
    return {
      "AWAITING_APPROVAL": "买入意图等待逐笔确认",
      "REJECTED": "买入意图已被拒绝",
      "DELAYED": "风控要求延后本次买入",
      "PARTIAL_FILLED": "买单已发生部分真实成交",
      "PARTIALLY_FILLED": "买单已发生部分真实成交",
      "FILLED": "买单真实成交已收敛",
      "CANCELLED": "买单已撤销并完成回报收敛",
    }.get(status, "系统产生了一条买入意图")

  @staticmethod
  def _authorization_event_message(event_type: str) -> str:
    return {
      "ENTRY_AUTO_AUTHORIZED": "设备绑定的自动买入授权已生效",
      "ENTRY_AUTO_AUTHORIZATION_INVALIDATED": "自动买入授权因风险边界变化而失效",
      "ENTRY_AUTO_AUTHORIZATION_REVOKED": "自动买入授权已撤销",
      "ENTRY_AUTHORIZATION_REAL_FILL_CONSUMED": "真实买入成交已消费授权额度",
      "ENTRY_AUTOMATION_PAUSED": "账户级自动买入安全门已暂停",
      "ENTRY_AUTOMATION_RESUMED": "账户级自动买入安全门已恢复",
    }.get(event_type, "自动买入授权状态已更新")


entry_plan_projection_service = EntryPlanProjectionService()
