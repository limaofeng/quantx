"""Fixed-instrument StrategyBase implementation for one managed A-share exit plan."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Dict, Mapping, Optional, Sequence

from quantx_contracts import ExecutionOwnerRef, ExecutionOwnerType

from quantx_domain.enums import StrategyInstrumentScope
from quantx_domain.schemas import ParameterProperty, ParameterSchema
from quantx_domain.state_schema import StateProperty, StateSchema
from quantx_domain.strategies.base import (
  BACKTEST_TICK_QUALITY_STRICT_DAILY_SESSION_COVERAGE,
  ExitPlanIntentOrigin,
  OrderStateEvent,
  RuntimeStatePatch,
  StrategyBase,
  StrategyCadence,
  StrategyInput,
  StrategyOutput,
  TradeExecutionEvent,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentExecutionMode,
  TradeIntentPriority,
)
from quantx_domain.trading.exit_plan import (
  ExitDecision,
  ExitEvaluationContext,
  ExitPlan,
  ExitPlanBook,
  ExitPlanStatus,
  ExitPlanTemplate,
  ExitPriceReference,
  ExitRuleType,
  ExitT1Policy,
)

MANAGED_EXIT_PLAN_KEY = "managed_exit_plan"
MANAGED_EXIT_RUNTIME_KEY = "managed_exit_plan_runtime"
EXIT_PLAN_ENABLED_KEY = "exit_plan_enabled"
STRATEGY_ID = "ashare_managed_exit_plan"


class AshareManagedExitPlanStrategy(StrategyBase):
  """Evaluate one durable exit plan through the shared strategy runtime."""

  INSTRUMENT_SCOPE = StrategyInstrumentScope.SINGLE

  def __init__(self, context):
    super().__init__(context)
    self._template: Optional[ExitPlanTemplate] = None

  @property
  def name(self) -> str:
    return "A股卖出托管计划"

  @property
  def version(self) -> str:
    return "1.0.0"

  @property
  def description(self) -> str:
    return "在固定标的与冻结计划版本内持续评估退出规则并提出 SELL 意图。"

  @classmethod
  def get_parameter_schema(cls) -> ParameterSchema:
    return ParameterSchema(
      type="object",
      properties={
        MANAGED_EXIT_PLAN_KEY: ParameterProperty(
          type="object",
          title="卖出托管计划",
          description="由卖出管理生成的不可变退出计划快照。",
        ),
        EXIT_PLAN_ENABLED_KEY: ParameterProperty(
          type="boolean",
          default=False,
          title="允许评估新的卖出触发",
        ),
        "account_id": ParameterProperty(type="string", title="交易账户"),
        "initial_protected_volume": ParameterProperty(
          type="integer", minimum=1, title="初始保护数量"
        ),
        "initial_entry_avg_price": ParameterProperty(
          type="number", minimum=0, title="冻结单位成本"
        ),
        "initial_entry_time": ParameterProperty(
          type="string", title="保护起始时间"
        ),
      },
      required=[
        MANAGED_EXIT_PLAN_KEY,
        "initial_protected_volume",
        "initial_entry_avg_price",
      ],
      additionalProperties=True,
    )

  @classmethod
  def get_state_schema(cls) -> StateSchema:
    return StateSchema(
      type="object",
      properties={
        MANAGED_EXIT_RUNTIME_KEY: StateProperty(
          type="object",
          default={},
          title="卖出计划算法状态",
          description="峰值、追踪锚点、规则状态和未决意图。",
        )
      },
    )

  @classmethod
  def get_data_requirements(cls) -> Dict[str, Any]:
    return {"use_tick_data": True, "periods": ["1m", "1d"]}

  @classmethod
  def get_backtest_data_requirements(
    cls,
    parameters: Mapping[str, Any],
  ) -> Dict[str, Any]:
    requirements = super().get_backtest_data_requirements(parameters)
    raw_template = _mapping(parameters.get(MANAGED_EXIT_PLAN_KEY))
    require_order_book_depth = False
    if raw_template:
      template = ExitPlanTemplate.from_dict(raw_template)
      require_order_book_depth = any(
        rule.enabled
        and rule.strategy == ExitRuleType.ADAPTIVE_VOLUME_PRICE_TRAILING.value
        for rule in template.rules
      )
    requirements.update(
      tick_quality_policy=BACKTEST_TICK_QUALITY_STRICT_DAILY_SESSION_COVERAGE,
      require_order_book_depth=require_order_book_depth,
    )
    return requirements

  async def on_init(self) -> None:
    self._template = ExitPlanTemplate.from_dict(
      _mapping(self.context.parameters.get(MANAGED_EXIT_PLAN_KEY))
    )
    if len(self.context.instruments) != 1:
      raise ValueError("managed exit plan requires exactly one instrument")
    if self.context.instruments[0] != self._template.instrument_code:
      raise ValueError("managed exit plan instrument does not match strategy run")
    if not _mapping(self.state.get(MANAGED_EXIT_RUNTIME_KEY)):
      plan = self._new_plan()
      self.state.set(
        MANAGED_EXIT_RUNTIME_KEY,
        plan.to_dict(),
        persist=False,
        notify=False,
      )

  async def on_stop(self) -> None:
    return None

  async def step(self, input: StrategyInput) -> StrategyOutput:
    template = self._require_template()
    if input.instrument_code != template.instrument_code:
      return StrategyOutput(decision_tags=["exit_instrument_mismatch"])
    if input.cadence not in {
      StrategyCadence.BAR,
      StrategyCadence.TICK,
      StrategyCadence.RECONCILE,
    }:
      return StrategyOutput()

    plan = self._plan_from_input(input)
    if self.context.parameters.get(EXIT_PLAN_ENABLED_KEY) is not True:
      if plan.status not in {ExitPlanStatus.COMPLETED, ExitPlanStatus.CANCELLED}:
        plan.status = ExitPlanStatus.PAUSED
      return self._state_only(plan, "EXIT_PLAN_PAUSED")
    if plan.status == ExitPlanStatus.PAUSED and plan.remaining_volume > 0:
      plan.status = ExitPlanStatus.ACTIVE

    if self.context.parameters.get("exit_plan_replay"):
      requires_depth = any(
        rule.enabled
        and rule.strategy == ExitRuleType.ADAPTIVE_VOLUME_PRICE_TRAILING.value
        for rule in plan.template.rules
      )
      market = input.market_data
      if requires_depth and not all(
        _has_complete_five_level_depth(_get(market, key, []))
        for key in ("bid_price", "ask_price", "bid_vol", "ask_vol")
      ):
        raise RuntimeError(
          "EXIT_PLAN_REPLAY_DEPTH_DATA_MISSING:"
          f"{input.instrument_code}:{input.timestamp.isoformat()}"
        )

    context = _evaluation_context(input)
    book = ExitPlanBook([plan])
    decisions = book.evaluate(input.instrument_code, context)
    if not decisions:
      return self._state_only(plan, "EXIT_RULES_NOT_TRIGGERED")

    decision = decisions[0]
    intent = self._build_intent(input, context, plan, decision)
    book.mark_intent(decision, intent.intent_id)
    snapshot = plan.to_dict()
    self.state.set(
      MANAGED_EXIT_RUNTIME_KEY,
      snapshot,
      persist=False,
      notify=False,
    )
    self.record_trade_intent(intent)
    return StrategyOutput(
      trade_intents=[intent],
      runtime_state_patch=RuntimeStatePatch(set={MANAGED_EXIT_RUNTIME_KEY: snapshot}),
      decision_tags=["managed_exit_intent", decision.rule_type.lower()],
      trace_payload={
        "plan_id": plan.plan_id,
        "config_version": plan.template.config_version,
        "reason": decision.reason,
        "rule_id": decision.rule_id,
        "rule_type": decision.rule_type,
        "requested_volume": decision.volume,
        "remaining_volume": plan.remaining_volume,
        "metrics": dict(decision.metrics or {}),
      },
    )

  async def on_order(self, event: OrderStateEvent) -> Optional[RuntimeStatePatch]:
    metadata = dict(event.metadata or {})
    plan = self._current_plan()
    owner = event.execution_ref
    if not (
      isinstance(owner, ExecutionOwnerRef)
      and owner.owner_type is ExecutionOwnerType.EXIT_PLAN
      and owner.owner_id == plan.plan_id
    ):
      return None
    intent_id = str(metadata.get("exit_intent_id") or plan.pending_intent_id or "")
    if not intent_id:
      return None
    status = str(event.status or "").upper()
    zero_fill_invalidation = metadata.get("zero_fill_proof_invalidation")
    if (
      isinstance(zero_fill_invalidation, Mapping)
      and zero_fill_invalidation.get("released") is True
      and str(zero_fill_invalidation.get("release_kind") or "")
      == "ZERO_FILL_PROOF"
      and intent_id not in plan.reconciled_zero_fill_intent_ids
    ):
      plan.reconciled_zero_fill_intent_ids.append(intent_id)
    local_zero_fill_proof = (
      str(metadata.get("execution_terminal_source") or "").upper()
      == "LOCAL_PRE_BROKER_REJECTION"
    )
    if local_zero_fill_proof and (
      status in {"REJECTED", "CANCELLED", "EXPIRED"}
      or (
        status == "PENDING"
        and str(metadata.get("risk_action") or "").upper() == "DELAY"
      )
    ):
      status = "RECONCILED_ZERO_FILL"
    ExitPlanBook([plan]).apply_order_event(
      plan_id=plan.plan_id,
      intent_id=intent_id,
      status=status,
      order_id=str(event.order_id or ""),
      risk_action=str(metadata.get("risk_action") or ""),
      timestamp_ms=int(
        (
          event.timestamp
          or self.context.current_time
          or datetime(1970, 1, 1)
        ).timestamp()
        * 1000
      ),
      cumulative_filled_volume=event.filled_volume,
    )
    return self._persist_plan(plan)

  async def on_trade(self, event: TradeExecutionEvent) -> Optional[RuntimeStatePatch]:
    metadata = dict(event.metadata or {})
    plan = self._current_plan()
    if (
      not isinstance(event.execution_ref, ExecutionOwnerRef)
      or event.execution_ref.owner_type is not ExecutionOwnerType.EXIT_PLAN
      or event.execution_ref.owner_id != plan.plan_id
      or event.instrument_code != plan.template.instrument_code
      or "SELL" not in str(event.trade_type or "").upper()
    ):
      return None
    zero_fill_invalidation = metadata.get("zero_fill_proof_invalidation")
    intent_id = str(
      metadata.get("intent_id") or metadata.get("exit_intent_id") or ""
    )
    if (
      isinstance(zero_fill_invalidation, Mapping)
      and zero_fill_invalidation.get("released") is True
      and str(zero_fill_invalidation.get("release_kind") or "")
      == "ZERO_FILL_PROOF"
      and intent_id
      and intent_id not in plan.reconciled_zero_fill_intent_ids
    ):
      plan.reconciled_zero_fill_intent_ids.append(intent_id)
    ExitPlanBook([plan]).apply_exit_fill(
      plan_id=plan.plan_id,
      volume=event.volume,
      price=event.price,
      rule_id=str(metadata.get("exit_rule_id") or ""),
      intent_id=intent_id,
    )
    patch = self._persist_plan(plan)
    patch.append_events.append(
      {
        "type": "EXIT_PLAN_TRADE_APPLIED",
        "plan_id": plan.plan_id,
        "volume": event.volume,
        "price": event.price,
        "remaining_volume": plan.remaining_volume,
      }
    )
    return patch

  def pending_manual_intent_ids(self) -> list[str]:
    plan = self._current_plan()
    return [plan.pending_intent_id] if plan.pending_intent_id else []

  def _build_intent(
    self,
    input: StrategyInput,
    context: ExitEvaluationContext,
    plan: ExitPlan,
    decision: ExitDecision,
  ) -> TradeIntent:
    execution = plan.template.execution
    if execution.price_reference == ExitPriceReference.BID:
      price_hint = context.bid_price or context.current_price
    elif execution.price_reference == ExitPriceReference.ASK:
      price_hint = context.ask_price or context.current_price
    else:
      price_hint = context.current_price
    requested_execution_mode = TradeIntentExecutionMode(
      str(execution.execution_mode or "AUTO").upper()
    )
    # Authorization is an Engine/infrastructure fact.  The pure strategy keeps
    # the configured execution policy; the Engine validates the exact durable
    # authorization envelope and downgrades LIVE AUTO to manual confirmation
    # before the intent is persisted or routed.
    execution_mode = requested_execution_mode
    urgent_types = {
      ExitRuleType.HARD_STOP.value,
      ExitRuleType.LIMIT_UP_TOUCH.value,
      ExitRuleType.LIMIT_UP_BREAK.value,
      ExitRuleType.RAPID_PROFIT_REVERSAL.value,
      ExitRuleType.STOP_PRICE.value,
    }
    intent_id = f"exit:{plan.plan_id}:{decision.rule_id}:{input.input_id}"
    return TradeIntent(
      strategy_id=input.strategy_id,
      run_id=input.run_id,
      execution_ref=ExecutionOwnerRef(
        ExecutionOwnerType.EXIT_PLAN,
        str(plan.plan_id),
      ),
      origin=ExitPlanIntentOrigin(
        plan_id=str(plan.plan_id),
        source_execution_ref=ExecutionOwnerRef.strategy_run(input.run_id),
      ),
      instrument_code=input.instrument_code,
      direction=TradeIntentDirection.SELL,
      bucket=plan.template.bucket,
      reason=f"AUTO_EXIT_{decision.reason}",
      priority=(
        TradeIntentPriority.URGENT
        if decision.rule_type in urgent_types
        else TradeIntentPriority.RISK_REDUCTION
      ),
      target_volume=int(decision.volume),
      limit_price_hint=price_hint,
      execution_mode=execution_mode,
      max_price_deviation_bps=execution.max_slippage_bps,
      metadata={
        **dict(plan.template.metadata or {}),
        "intent_origin_type": "EXIT_PLAN",
        "plan_id": plan.plan_id,
        "exit_intent_id": intent_id,
        "exit_rule_id": decision.rule_id,
        "exit_rule_type": decision.rule_type,
        "exit_reason": decision.reason,
        "exit_plan_source_type": plan.template.source_type,
        "exit_plan_source_id": plan.template.source_id,
        "exit_plan_config_version": plan.template.config_version,
        "price_type": execution.price_type,
        "price_reference": execution.price_reference.value,
        "protected_limit": execution.protected_limit,
        "max_exit_slippage_bps": execution.max_slippage_bps,
        "execution_urgency": execution.urgency,
        "t1_policy": plan.template.t1_policy.value,
        "allow_t1_substitution": (
          plan.template.t1_policy
          == ExitT1Policy.ALLOW_SAME_INSTRUMENT_SUBSTITUTION
        ),
        "t1_insufficient_action": (
          "REJECT"
          if plan.template.t1_policy == ExitT1Policy.REJECT_IF_UNSELLABLE
          else "DELAY"
        ),
        "exit_metrics": dict(decision.metrics or {}),
      },
      trace_id=input.trace_id,
      intent_id=intent_id,
      created_at=input.timestamp,
    )

  def _state_only(self, plan: ExitPlan, reason: str) -> StrategyOutput:
    snapshot = plan.to_dict()
    self.state.set(
      MANAGED_EXIT_RUNTIME_KEY,
      snapshot,
      persist=False,
      notify=False,
    )
    return StrategyOutput(
      runtime_state_patch=RuntimeStatePatch(set={MANAGED_EXIT_RUNTIME_KEY: snapshot}),
      decision_tags=[reason.lower()],
      trace_payload={
        "reason": reason,
        "plan_id": plan.plan_id,
        "remaining_volume": plan.remaining_volume,
      },
    )

  def _persist_plan(self, plan: ExitPlan) -> RuntimeStatePatch:
    snapshot = plan.to_dict()
    self.state.set(
      MANAGED_EXIT_RUNTIME_KEY,
      snapshot,
      persist=False,
      notify=False,
    )
    return RuntimeStatePatch(set={MANAGED_EXIT_RUNTIME_KEY: snapshot})

  def _plan_from_input(self, input: StrategyInput) -> ExitPlan:
    raw = _mapping(input.strategy_state).get(MANAGED_EXIT_RUNTIME_KEY)
    if raw is None:
      raw = self.state.get(MANAGED_EXIT_RUNTIME_KEY)
    if _mapping(raw):
      return ExitPlan.from_dict(_mapping(raw))
    return self._new_plan()

  def _current_plan(self) -> ExitPlan:
    raw = _mapping(self.state.get(MANAGED_EXIT_RUNTIME_KEY))
    return ExitPlan.from_dict(raw) if raw else self._new_plan()

  def _new_plan(self) -> ExitPlan:
    plan = ExitPlan(template=self._require_template())
    plan.register_entry_fill(
      volume=int(self.context.parameters.get("initial_protected_volume") or 0),
      price=float(self.context.parameters.get("initial_entry_avg_price") or 0.0),
      trade_time=_datetime(self.context.parameters.get("initial_entry_time")),
    )
    if self.context.parameters.get(EXIT_PLAN_ENABLED_KEY) is not True:
      plan.status = ExitPlanStatus.PAUSED
    return plan

  def _require_template(self) -> ExitPlanTemplate:
    if self._template is None:
      self._template = ExitPlanTemplate.from_dict(
        _mapping(self.context.parameters.get(MANAGED_EXIT_PLAN_KEY))
      )
    return self._template


def _evaluation_context(input: StrategyInput) -> ExitEvaluationContext:
  market = input.market_data
  bids = _sequence(_get(market, "bid_price", _get(market, "bid_prices", [])))
  asks = _sequence(_get(market, "ask_price", _get(market, "ask_prices", [])))
  bid_vol = _sequence(_get(market, "bid_vol", _get(market, "bid_volume", [])))
  ask_vol = _sequence(_get(market, "ask_vol", _get(market, "ask_volume", [])))
  current_price = _float(
    _get(market, "price", _get(market, "close", _get(input.event, "price", 0.0)))
  )
  bid_depth = sum(_float(item) for item in bid_vol[:5])
  ask_depth = sum(_float(item) for item in ask_vol[:5])
  return ExitEvaluationContext(
    timestamp=input.timestamp,
    current_price=current_price,
    bid_price=_float(bids[0]) if bids else 0.0,
    ask_price=_float(asks[0]) if asks else 0.0,
    limit_up=_float(_get(market, "limit_up", _get(market, "up_stop_price", 0.0))),
    limit_down=_float(
      _get(market, "limit_down", _get(market, "down_stop_price", 0.0))
    ),
    price_tick=_float(_get(market, "price_tick", 0.01)) or 0.01,
    cumulative_volume=_optional_float(_get(market, "volume")),
    cumulative_amount=_optional_float(_get(market, "amount")),
    depth_imbalance_5=(
      (bid_depth - ask_depth) / (bid_depth + ask_depth)
      if bid_depth + ask_depth > 0
      else None
    ),
    source=str(_get(market, "source", "") or ""),
  )


def _mapping(value: Any) -> Dict[str, Any]:
  return dict(value) if isinstance(value, Mapping) else {}


def _get(source: Any, key: str, default: Any = None) -> Any:
  if source is None:
    return default
  if isinstance(source, Mapping):
    return source.get(key, default)
  return getattr(source, key, default)


def _sequence(value: Any) -> list[Any]:
  if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
    return list(value)
  return [value] if value is not None else []


def _has_complete_five_level_depth(value: Any) -> bool:
  levels = _sequence(value)
  if len(levels) < 5:
    return False
  for item in levels[:5]:
    if isinstance(item, bool):
      return False
    try:
      number = float(item)
    except (TypeError, ValueError, OverflowError):
      return False
    if not math.isfinite(number) or number < 0.0:
      return False
  return True


def _float(value: Any) -> float:
  try:
    return float(value or 0.0)
  except (TypeError, ValueError, OverflowError):
    return 0.0


def _optional_float(value: Any) -> Optional[float]:
  return None if value is None else _float(value)


def _datetime(value: Any) -> Optional[datetime]:
  if isinstance(value, datetime):
    return value
  if isinstance(value, str) and value.strip():
    try:
      return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
      return None
  return None
