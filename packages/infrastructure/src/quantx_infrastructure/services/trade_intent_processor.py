"""Shared application processor for plan- and strategy-owned trade intents."""

from __future__ import annotations

from typing import Any, Callable, Optional

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

from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.models.auto_exit_plan import AutoExitPlanRecord
from quantx_infrastructure.models.enums import AccountType, OrderType, PriceType
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.trade_intent_record import TradeIntentRecord
from quantx_infrastructure.services.exit_plan_authorization_service import (
  AutoExitAuthorizationGuard,
)
from quantx_infrastructure.services.trading_service import TradingService

MARKET_DATA_STREAM_NOT_READY = "MARKET_DATA_STREAM_NOT_READY"


class TradeIntentProcessor:
  """Turn a SELL intent into a sized, risk-checked, durable broker command."""

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
    metadata = {
      "owner_type": "EXIT_PLAN",
      "owner_id": plan.plan_id,
      "account_id": plan.account_id,
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
    }
    intent = TradeIntent(
      intent_id=intent_id,
      strategy_id=str(plan.strategy_run_id or "exit-plan"),
      run_id=str(plan.strategy_run_id or ""),
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
  ) -> dict[str, Any]:
    if record.owner_type != "EXIT_PLAN" or record.owner_id != plan.plan_id:
      raise ValueError("卖出意图不属于该退出计划")
    if record.status != "AWAITING_APPROVAL" or record.direction != "SELL":
      raise ValueError("卖出意图已处理或不再等待确认")
    if not self._market_is_ready(market_ready):
      await self._update_intent(
        record.id,
        status="REJECTED",
        notes=MARKET_DATA_STREAM_NOT_READY,
        metadata={
          **dict(record.intent_metadata or {}),
          "market_data_gate": MARKET_DATA_STREAM_NOT_READY,
        },
      )
      return self._market_not_ready_result(record.id)
    intent = TradeIntent(
      intent_id=record.id,
      strategy_id=str(record.strategy_id or "exit-plan"),
      run_id=str(record.strategy_run_id or ""),
      instrument_code=record.instrument_code,
      direction=TradeIntentDirection.SELL,
      bucket=record.bucket,
      reason=record.reason,
      priority=TradeIntentPriority(record.priority),
      target_volume=record.target_volume,
      limit_price_hint=limit_price,
      metadata=dict(record.intent_metadata or {}),
      trace_id=record.trace_id,
    )
    await self._update_intent(record.id, status="APPROVED")
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
        metadata={**intent.metadata, "size_reasons": draft.size_reason_codes},
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
        metadata={
          **intent.metadata,
          "risk_action": risk.action.value,
          "risk_reason_code": risk.reason_code,
          "risk_tags": risk.risk_tags,
        },
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
      idempotency_key=f"exit-plan:{plan.plan_id}:{intent.intent_id}",
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
      db.add(
        TradeIntentRecord(
          id=intent.intent_id,
          strategy_run_id=plan.strategy_run_id,
          owner_type="EXIT_PLAN",
          owner_id=plan.plan_id,
          account_id=plan.account_id,
          strategy_id=str(plan.strategy_run_id or "exit-plan"),
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
      )
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
      metadata={
        **dict(intent.metadata or {}),
        "market_data_gate": MARKET_DATA_STREAM_NOT_READY,
      },
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
