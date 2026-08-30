"""Shared application processor for plan- and strategy-owned trade intents."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Optional

from quantx_domain.brokers.base import (
  OrderRequest,
)
from quantx_domain.brokers.base import (
  OrderType as BrokerOrderType,
)
from quantx_domain.brokers.base import (
  PriceType as BrokerPriceType,
)
from quantx_domain.strategies.base import (
  ManualCommandIntentOrigin,
  StrategyRunIntentOrigin,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentPriority,
)
from quantx_domain.trading import (
  AShareMarketRules,
  MarketDataSnapshot,
  OrderSizer,
  RiskAction,
  TradingRiskChecker,
)
from quantx_domain.trading.exit_plan import ExitDecision, ExitEvaluationContext
from sqlalchemy import select

from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.agent_runtime import PendingTradeOrder
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.enums import AccountType, OrderType, PriceType
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.exit_plan_authorization_service import (
  AutoExitAuthorizationGuard,
)
from quantx_infrastructure.services.trading_service import TradingService

MARKET_DATA_STREAM_NOT_READY = "MARKET_DATA_STREAM_NOT_READY"
LOCAL_PRE_BROKER_ZERO_FILL_SOURCE = "LOCAL_PRE_BROKER_REJECTION"
LOCAL_OUTBOX_EXPIRED_ZERO_FILL_SOURCE = "LOCAL_OUTBOX_EXPIRED"
LOCAL_AGENT_PRE_EXECUTION_ZERO_FILL_SOURCE = "LOCAL_AGENT_PRE_EXECUTION_REJECTION"


def local_pre_broker_zero_fill_metadata(
  metadata: Optional[Mapping[str, Any]],
  *,
  reason: str,
) -> dict[str, Any]:
  """Mark a terminal intent that provably never crossed the broker boundary."""

  return {
    **dict(metadata or {}),
    "execution_terminal_source": LOCAL_PRE_BROKER_ZERO_FILL_SOURCE,
    "execution_terminal_reason": str(reason or "LOCAL_PRE_BROKER_REJECTION"),
  }


class TradeIntentProcessor:
  """Turn a SELL intent into a sized, risk-checked, durable broker command."""

  @staticmethod
  def _exit_intent_metadata(
    plan: AutoExitPlanRecord,
    decision: ExitDecision,
  ) -> dict[str, Any]:
    run_id = str(plan.strategy_run_id or "")
    manual_command_id = str(plan.group_id or plan.source_id or plan.plan_id)
    is_strategy_run = bool(run_id)
    return {
      "owner_type": "EXIT_PLAN",
      "owner_id": plan.plan_id,
      "intent_origin_type": (
        "STRATEGY_RUN" if is_strategy_run else "MANUAL_COMMAND"
      ),
      "manual_command_id": None if is_strategy_run else manual_command_id,
      "manual_action_type": (
        None
        if is_strategy_run
        else (
          "LIQUIDATE_POSITIONS"
          if str(plan.source_type or "") == "MANUAL_LIQUIDATION"
          else "MANUAL_EXIT_PLAN"
        )
      ),
      "account_id": plan.account_id,
      "strategy_run_id": run_id,
      "requested_volume": int(decision.volume),
      "exit_plan_id": plan.plan_id,
      "exit_rule_id": decision.rule_id,
      "exit_rule_type": decision.rule_type,
      "exit_reason": decision.reason,
      "exit_metrics": dict(decision.metrics or {}),
      "exit_policy_version": int(plan.config_version),
      "group_id": plan.group_id,
      "completion_strategy": plan.completion_strategy,
      "price_type": "LIMIT",
    }

  @staticmethod
  async def reserve_exit_intent(
    db: Any,
    *,
    plan: AutoExitPlanRecord,
    decision: ExitDecision,
    intent_id: str,
    limit_price: float,
  ) -> None:
    """Persist the business intent in the same transaction as plan pending state."""

    existing = await db.get(TradeIntentRecord, intent_id)
    if existing is not None:
      if (
        str(existing.owner_type or "").upper() != "EXIT_PLAN"
        or str(existing.owner_id or "") != str(plan.plan_id)
      ):
        raise ValueError("退出卖出意图标识已被其他业务对象占用")
      return
    db.add(
      TradeIntentRecord(
        id=intent_id,
        strategy_run_id=plan.strategy_run_id,
        owner_type="EXIT_PLAN",
        owner_id=str(plan.plan_id),
        account_id=plan.account_id,
        strategy_id=str(plan.strategy_run_id or "") or None,
        instrument_code=plan.instrument_code,
        direction="SELL",
        bucket=plan.bucket,
        reason=decision.reason,
        priority="HIGH",
        target_volume=int(decision.volume),
        limit_price_hint=limit_price,
        trace_id=intent_id,
        status="RESERVED",
        intent_metadata=TradeIntentProcessor._exit_intent_metadata(plan, decision),
        notes="EXIT_PLAN_ATOMIC_RESERVATION",
      )
    )

  async def process_exit_decision(
    self,
    *,
    plan: AutoExitPlanRecord,
    decision: ExitDecision,
    intent_id: str,
    context: ExitEvaluationContext,
    position: Optional[Position],
    limit_price: float,
    market_ready: Optional[Callable[[], bool]] = None,
  ) -> dict[str, Any]:
    authorization_code = "PAPER_NOT_REQUIRED"
    exact_auto_authorized = plan.execution_mode != "live"
    if plan.execution_mode == "live":
      if bool(plan.auto_exit_authorized):
        authorization = await AutoExitAuthorizationGuard.validate_or_invalidate(
          plan.plan_id
        )
        exact_auto_authorized = authorization.valid
        authorization_code = authorization.code
      else:
        authorization_code = "AUTO_EXIT_NOT_AUTHORIZED"
    run_id = str(plan.strategy_run_id or "")
    manual_command_id = str(plan.group_id or plan.source_id or plan.plan_id)
    is_strategy_run = bool(run_id)
    metadata = {
      **self._exit_intent_metadata(plan, decision),
      "auto_exit_authorization_code": authorization_code,
      "exact_auto_exit_authorized": bool(exact_auto_authorized),
      "auto_exit_authorization_user_id": (
        str(plan.auto_exit_authorization_user_id or "")
        if exact_auto_authorized and plan.execution_mode == "live"
        else ""
      ),
      "auto_exit_authorization_fingerprint": (
        str(plan.auto_exit_authorization_fingerprint or "")
        if exact_auto_authorized and plan.execution_mode == "live"
        else ""
      ),
      "auto_exit_authorization_challenge_id": (
        str(plan.auto_exit_authorization_challenge_id or "")
        if exact_auto_authorized and plan.execution_mode == "live"
        else ""
      ),
      "auto_exit_authorization_device_session_id": (
        str(plan.auto_exit_authorization_device_session_id or "")
        if exact_auto_authorized and plan.execution_mode == "live"
        else ""
      ),
      "auto_exit_authorized_at": (
        plan.auto_exit_authorized_at.isoformat()
        if exact_auto_authorized
        and plan.execution_mode == "live"
        and plan.auto_exit_authorized_at is not None
        else ""
      ),
      "auto_exit_authorization_expires_at": (
        plan.auto_exit_authorization_expires_at.isoformat()
        if exact_auto_authorized
        and plan.execution_mode == "live"
        and plan.auto_exit_authorization_expires_at is not None
        else ""
      ),
    }
    intent = TradeIntent(
      intent_id=intent_id,
      strategy_id=run_id,
      run_id=run_id,
      origin=(
        StrategyRunIntentOrigin(
          run_id=run_id,
          strategy_id=run_id,
          plan_id=plan.plan_id,
        )
        if is_strategy_run
        else ManualCommandIntentOrigin(
          command_id=manual_command_id,
          action_type=str(metadata["manual_action_type"]),
          liquidation_group_id=str(plan.group_id or "") or None,
        )
      ),
      instrument_code=plan.instrument_code,
      direction=TradeIntentDirection.SELL,
      bucket=plan.bucket,
      reason=decision.reason,
      priority=TradeIntentPriority.HIGH,
      target_volume=int(decision.volume),
      limit_price_hint=limit_price,
      metadata=metadata,
      trace_id=intent_id,
    )
    if not self._market_is_ready(market_ready):
      intent.metadata = local_pre_broker_zero_fill_metadata(
        intent.metadata,
        reason=MARKET_DATA_STREAM_NOT_READY,
      )
      await self._create_intent_record(
        plan,
        intent,
        status="REJECTED",
        notes=MARKET_DATA_STREAM_NOT_READY,
      )
      return self._market_not_ready_result(intent.intent_id)
    approval_required = (
      plan.execution_mode == "live" and not exact_auto_authorized
    )
    await self._create_intent_record(
      plan,
      intent,
      status="AWAITING_APPROVAL" if approval_required else "PENDING",
    )
    if approval_required:
      return {
        "success": True,
        "awaiting_approval": True,
        "intent_id": intent_id,
        "message": "卖出意图等待人工确认",
      }
    return await self._route(
      plan=plan,
      intent=intent,
      context=context,
      position=position,
      limit_price=limit_price,
      market_ready=market_ready,
    )

  async def process_approved_exit_intent(
    self,
    *,
    plan: AutoExitPlanRecord,
    record: TradeIntentRecord,
    context: ExitEvaluationContext,
    position: Optional[Position],
    limit_price: float,
    market_ready: Optional[Callable[[], bool]] = None,
    approval_audit: Optional[Mapping[str, Any]] = None,
  ) -> dict[str, Any]:
    if str(dict(record.intent_metadata or {}).get("exit_plan_id") or "") != plan.plan_id:
      raise ValueError("卖出意图不属于该退出计划")
    durable_status = str(record.status or "").upper()
    if durable_status not in {"AWAITING_APPROVAL", "APPROVED", "PENDING"} or (
      record.direction != "SELL"
    ):
      raise ValueError("卖出意图已处理或不再等待确认")
    if not self._market_is_ready(market_ready):
      await self._update_intent(
        record.id,
        status="REJECTED",
        notes=MARKET_DATA_STREAM_NOT_READY,
        metadata=local_pre_broker_zero_fill_metadata(
          {
            **dict(record.intent_metadata or {}),
            "market_data_gate": MARKET_DATA_STREAM_NOT_READY,
          },
          reason=MARKET_DATA_STREAM_NOT_READY,
        ),
      )
      return self._market_not_ready_result(record.id)
    record_metadata = dict(record.intent_metadata or {})
    audit = dict(approval_audit or {})
    expected_audit_metadata = {
      "exit_plan_approval_challenge_id": str(
        audit.get("challenge_id") or ""
      ).strip(),
      "exit_plan_approval_user_id": str(audit.get("actor_id") or "").strip(),
      "exit_plan_approval_device_session_id": str(
        audit.get("device_session_id") or ""
      ).strip(),
      "exit_plan_approval_channel": str(audit.get("channel") or "").strip(),
    }
    if not all(expected_audit_metadata.values()):
      raise ValueError("卖出意图缺少完整确认挑战审计")
    # Upstream has already validated this consumed challenge against the exact
    # plan/intent/account/device tuple.  If a previous APPROVED attempt failed
    # before creating PendingTradeOrder, a fresh challenge may safely replace
    # the prior audit and resume the same idempotent intent.
    record_metadata.update(
      expected_audit_metadata
    )
    if durable_status in {"APPROVED", "PENDING"}:
      async with AsyncSessionLocal() as db:
        pending = await db.scalar(
          select(PendingTradeOrder)
          .where(
            PendingTradeOrder.account_id == str(plan.account_id),
            PendingTradeOrder.intent_id == str(record.id),
          )
          .limit(1)
        )
      if pending is not None:
        return {
          "success": True,
          "code": "EXIT_INTENT_ALREADY_QUEUED",
          "intent_id": record.id,
          "client_order_id": str(pending.client_order_id),
          "order_id": str(pending.client_order_id),
          "status": str(pending.status or "PENDING"),
          "volume": int(pending.volume or 0),
        }
      if durable_status == "PENDING":
        raise ValueError("EXIT_INTENT_RECONCILIATION_REQUIRED")
    record_run_id = str(record.strategy_run_id or "")
    intent = TradeIntent(
      intent_id=record.id,
      strategy_id=str(record.strategy_id or "") if record_run_id else "",
      run_id=record_run_id,
      origin=(
        StrategyRunIntentOrigin(
          run_id=record_run_id,
          strategy_id=str(record.strategy_id or record_run_id),
          plan_id=plan.plan_id,
        )
        if record_run_id
        else ManualCommandIntentOrigin(
          command_id=str(record.owner_id),
          action_type=str(
            record_metadata.get("manual_action_type") or "LIQUIDATE_POSITIONS"
          ),
          liquidation_group_id=str(plan.group_id or "") or None,
        )
      ),
      instrument_code=record.instrument_code,
      direction=TradeIntentDirection.SELL,
      bucket=record.bucket,
      reason=record.reason,
      priority=TradeIntentPriority(record.priority),
      target_volume=record.target_volume,
      limit_price_hint=limit_price,
      metadata=record_metadata,
      trace_id=record.trace_id,
    )
    await self._update_intent(
      record.id,
      status="APPROVED",
      metadata=record_metadata,
    )
    return await self._route(
      plan=plan,
      intent=intent,
      context=context,
      position=position,
      limit_price=limit_price,
      market_ready=market_ready,
    )

  async def _route(
    self,
    *,
    plan: AutoExitPlanRecord,
    intent: TradeIntent,
    context: ExitEvaluationContext,
    position: Optional[Position],
    limit_price: float,
    market_ready: Optional[Callable[[], bool]] = None,
  ) -> dict[str, Any]:
    if not self._market_is_ready(market_ready):
      await self._reject_market_not_ready(intent)
      return self._market_not_ready_result(intent.intent_id)
    service = TradingService(
      account_id=plan.account_id,
      account_type=AccountType.STOCK,
      execution_mode=plan.execution_mode,
    )
    try:
      account_model = await service.get_account_info()
      account = {
        "available_cash": float(getattr(account_model, "cash", 0.0) or 0.0),
        "cash": float(getattr(account_model, "cash", 0.0) or 0.0),
        "frozen_cash": float(
          getattr(account_model, "frozen_cash", 0.0) or 0.0
        ),
        "total_asset": float(
          getattr(account_model, "total_asset", 0.0) or 0.0
        ),
      }
    except Exception:
      account = {}
    position_state = {
      "long_volume": int(getattr(position, "volume", 0) or 0),
      "available_volume": int(getattr(position, "can_use_volume", 0) or 0),
      "frozen_volume": int(getattr(position, "frozen_volume", 0) or 0),
      "today_buy_volume": max(
        0,
        int(getattr(position, "volume", 0) or 0)
        - int(getattr(position, "yesterday_volume", 0) or 0),
      ),
    }
    market = MarketDataSnapshot(
      instrument_code=plan.instrument_code,
      timestamp=context.timestamp,
      price=float(context.current_price or context.bid_price or 0.0),
      close=float(context.current_price or context.bid_price or 0.0),
      price_tick=float(context.price_tick or 0.01),
      limit_up=float(context.limit_up or 0.0) or None,
      limit_down=float(context.limit_down or 0.0) or None,
      bid_price=[float(context.bid_price or 0.0)],
      ask_price=[float(context.ask_price or 0.0)],
      is_trading=True,
      suspended=False,
      source=context.source,
    )
    rules = AShareMarketRules()
    normalized_price = rules.normalize_price(limit_price, market.price_tick)
    draft = OrderSizer(rules).draft_intent(
      intent,
      BrokerOrderType.SELL,
      normalized_price,
      account,
      position_state,
    )
    if draft.sized_volume <= 0:
      await self._update_intent(
        intent.intent_id,
        status="REJECTED",
        notes="ZERO_SIZED_VOLUME",
        metadata=local_pre_broker_zero_fill_metadata(
          {**intent.metadata, "size_reasons": draft.size_reason_codes},
          reason="ZERO_SIZED_VOLUME",
        ),
      )
      return {
        "success": False,
        "intent_id": intent.intent_id,
        "error": "ZERO_SIZED_VOLUME",
      }
    request = OrderRequest(
      instrument_code=plan.instrument_code,
      order_type=BrokerOrderType.SELL,
      price_type=BrokerPriceType.LIMIT,
      volume=draft.sized_volume,
      price=normalized_price,
      strategy_id=str(plan.strategy_run_id or "exit-plan"),
      metadata={
        **intent.metadata,
        "intent_id": intent.intent_id,
        "order_draft_id": draft.draft_id,
        "order_draft_size_reasons": draft.size_reason_codes,
      },
    )
    risk = await TradingRiskChecker(
      rules,
      strict_market_data=True,
      strict_limit_data=True,
      enforce_trading_hours=plan.execution_mode == "live",
    ).evaluate_order(
      request,
      account=account,
      position=position_state,
      market_data=market,
      current_time=context.timestamp,
      risk_caps={"allow_sell": True},
    )
    if not risk.allowed:
      status = "DELAYED" if risk.action == RiskAction.DELAY else "REJECTED"
      await self._update_intent(
        intent.intent_id,
        status=status,
        risk_decision_id=risk.risk_decision_id,
        notes=risk.reason_detail,
        metadata=local_pre_broker_zero_fill_metadata(
          {
            **intent.metadata,
            "risk_action": risk.action.value,
            "risk_reason_code": risk.reason_code,
            "risk_tags": risk.risk_tags,
          },
          reason=risk.reason_code,
        ),
      )
      return {
        "success": False,
        "intent_id": intent.intent_id,
        "error": risk.reason_code,
        "risk_action": risk.action.value,
      }
    if not self._market_is_ready(market_ready):
      await self._reject_market_not_ready(intent)
      return self._market_not_ready_result(intent.intent_id)
    final_volume = int(risk.final_volume or request.volume)
    result = await service.place_order(
      stock_code=plan.instrument_code,
      order_type=OrderType.SELL,
      order_volume=final_volume,
      price_type=PriceType.FIX_PRICE,
      price=normalized_price,
      strategy_name="卖出管理",
      order_remark=f"退出计划: {plan.instrument_code}",
      close_position=final_volume >= int(getattr(position, "volume", 0) or 0),
      idempotency_key=f"strategy-exit:{plan.plan_id}:{intent.intent_id}",
      execution_context={
        **intent.metadata,
        "trace_id": intent.intent_id,
        "intent_id": intent.intent_id,
        "risk_decision_id": risk.risk_decision_id,
        "risk_action": risk.action.value,
        "risk_reason_code": risk.reason_code,
      },
    )
    client_order_id = str(
      result.get("client_order_id") or result.get("order_id") or ""
    )
    await self._update_intent(
      intent.intent_id,
      status=str(result.get("status") or "PENDING").upper(),
      order_id=client_order_id or None,
      risk_decision_id=risk.risk_decision_id,
      metadata={
        **intent.metadata,
        "sized_volume": final_volume,
        "client_order_id": client_order_id,
        "risk_action": risk.action.value,
        "risk_reason_code": risk.reason_code,
      },
    )
    return {**result, "intent_id": intent.intent_id, "volume": final_volume}

  @staticmethod
  async def _create_intent_record(
    plan: AutoExitPlanRecord,
    intent: TradeIntent,
    *,
    status: str,
    notes: Optional[str] = None,
  ) -> None:
    async with AsyncSessionLocal() as db:
      record = await db.scalar(
        select(TradeIntentRecord)
        .where(TradeIntentRecord.id == intent.intent_id)
        .with_for_update()
      )
      if record is None:
        record = TradeIntentRecord(
          id=intent.intent_id,
          strategy_run_id=plan.strategy_run_id,
          owner_type=str(intent.metadata.get("owner_type") or "STRATEGY_RUN"),
          owner_id=str(intent.metadata.get("owner_id") or ""),
          account_id=plan.account_id,
          strategy_id=str(intent.strategy_id or "") or None,
          instrument_code=intent.instrument_code,
          direction=intent.direction.value,
          bucket=intent.bucket,
          reason=intent.reason,
          priority=intent.priority.value,
          intent_type=intent.intent_type.value if intent.intent_type else None,
          confidence=float(intent.confidence),
          target_volume=intent.target_volume,
          limit_price_hint=intent.limit_price_hint,
          trace_id=intent.trace_id,
          status=status,
          intent_metadata=dict(intent.metadata or {}),
          notes=notes,
        )
        db.add(record)
      else:
        if (
          str(record.owner_type or "").upper() != "EXIT_PLAN"
          or str(record.owner_id or "")
          != str(intent.metadata.get("owner_id") or "")
          or str(record.instrument_code or "").upper()
          != str(intent.instrument_code or "").upper()
          or str(record.direction or "").upper() != "SELL"
        ):
          raise ValueError("退出卖出意图持久化身份冲突")
        record.strategy_run_id = plan.strategy_run_id
        record.account_id = plan.account_id
        record.strategy_id = str(intent.strategy_id or "") or None
        record.bucket = intent.bucket
        record.reason = intent.reason
        record.priority = intent.priority.value
        record.intent_type = intent.intent_type.value if intent.intent_type else None
        record.confidence = float(intent.confidence)
        record.target_volume = intent.target_volume
        record.limit_price_hint = intent.limit_price_hint
        record.trace_id = intent.trace_id
        record.status = status
        record.intent_metadata = dict(intent.metadata or {})
        record.notes = notes
      await db.commit()

  @staticmethod
  def _market_is_ready(check: Optional[Callable[[], bool]]) -> bool:
    if check is None:
      return True
    try:
      return bool(check())
    except Exception:
      return False

  @staticmethod
  def _market_not_ready_result(intent_id: str) -> dict[str, Any]:
    return {
      "success": False,
      "intent_id": intent_id,
      "error": MARKET_DATA_STREAM_NOT_READY,
    }

  async def _reject_market_not_ready(self, intent: TradeIntent) -> None:
    await self._update_intent(
      intent.intent_id,
      status="REJECTED",
      notes=MARKET_DATA_STREAM_NOT_READY,
      metadata=local_pre_broker_zero_fill_metadata(
        {
          **dict(intent.metadata or {}),
          "market_data_gate": MARKET_DATA_STREAM_NOT_READY,
        },
        reason=MARKET_DATA_STREAM_NOT_READY,
      ),
    )

  @staticmethod
  async def _update_intent(intent_id: str, **updates: Any) -> None:
    async with AsyncSessionLocal() as db:
      record = await db.get(TradeIntentRecord, intent_id)
      if record is None:
        return
      for key, value in updates.items():
        if key == "metadata":
          record.intent_metadata = dict(value or {})
        else:
          setattr(record, key, value)
      await db.commit()
