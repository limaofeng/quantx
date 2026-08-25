"""Fixed-instrument StrategyBase adapter for managed A-share entry plans."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from quantx_domain.enums import StrategyInstrumentScope
from quantx_domain.schemas import ParameterProperty, ParameterSchema
from quantx_domain.state_schema import StateProperty, StateSchema
from quantx_domain.strategies.base import (
  OrderStateEvent,
  RuntimeStatePatch,
  StrategyBase,
  StrategyCadence,
  StrategyInput,
  StrategyOutput,
  StrategyRunIntentOrigin,
  TradeExecutionEvent,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentExecutionMode,
  TradeIntentPriority,
  TradeIntentType,
)
from quantx_domain.trading.entry_plan import (
  CausalPriceObservation,
  EntryAuthorizationMode,
  EntryEvaluationContext,
  EntryPlanStatus,
  ManagedEntryPlanConfig,
  ManagedEntryPlanEvaluator,
  ManagedEntryPlanState,
  PendingBuyExposure,
)

MANAGED_ENTRY_STATE_KEY = "managed_entry_plan"
ENTRY_PLAN_ENABLED_KEY = "entry_plan_enabled"
STRATEGY_ID = "ashare_managed_entry_plan"
TERMINAL_ORDER_STATUSES = {
  "FILLED",
  "REJECTED",
  "BROKER_REJECTED",
  "CANCELLED",
  "CANCELED",
  "EXPIRED",
  "PARTIALLY_CANCELED",
  "RECONCILED_ZERO_FILL",
}


class AshareManagedEntryPlanStrategy(StrategyBase):
  """Thin adapter from StrategyInput to the pure managed-entry evaluator."""

  INSTRUMENT_SCOPE = StrategyInstrumentScope.SINGLE

  def __init__(self, context):
    super().__init__(context)
    self._config: Optional[ManagedEntryPlanConfig] = None
    self._evaluator = ManagedEntryPlanEvaluator()

  @property
  def name(self) -> str:
    return "A股建仓/加仓托管计划"

  @property
  def version(self) -> str:
    return "1.0.0"

  @property
  def description(self) -> str:
    return "在固定标的、目标暴露和硬风险边界内按规则提出分批买入意图。"

  @classmethod
  def get_parameter_schema(cls) -> ParameterSchema:
    return ParameterSchema(
      type="object",
      properties={
        MANAGED_ENTRY_STATE_KEY: ParameterProperty(
          type="object",
          title="建仓/加仓托管计划",
          description="由买入管理产品页生成的强类型计划配置。",
        ),
        ENTRY_PLAN_ENABLED_KEY: ParameterProperty(
          type="boolean",
          default=False,
          title="允许评估新的买入触发",
          description="暂停时仍继续收敛既有委托、真实成交与卖出保护。",
        ),
        "account_id": ParameterProperty(
          type="string",
          title="交易账户",
          description="计划绑定的个人交易账户。",
        ),
        "entry_plan_actor_user_id": ParameterProperty(
          type="string",
          title="操作主体",
          description="创建或最近修改计划的用户标识。",
        ),
        "entry_plan_note": ParameterProperty(
          type="string",
          title="计划备注",
          description="由买入管理产品页保存的计划备注。",
        ),
        "initial_capital": ParameterProperty(
          type="number",
          minimum=0,
          title="基准总资产",
          description="创建计划时权威账户快照中的总资产。",
        ),
        "enable_reserve": ParameterProperty(
          type="boolean",
          title="启用资金预留",
          description="要求执行链路为工作中买单预留资金。",
        ),
        "enforce_trading_hours": ParameterProperty(
          type="boolean",
          title="校验交易时段",
          description="要求执行链路按 A 股交易时段失败关闭。",
        ),
        "entry_plan_last_command_id": ParameterProperty(
          type="string",
          title="最近命令标识",
          description="用于收敛计划管理命令重放的幂等标识。",
        ),
      },
      required=[MANAGED_ENTRY_STATE_KEY],
      additionalProperties=False,
    )

  @classmethod
  def get_state_schema(cls) -> StateSchema:
    return StateSchema(
      type="object",
      properties={
        MANAGED_ENTRY_STATE_KEY: StateProperty(
          type="object",
          default=ManagedEntryPlanState().to_dict(),
          title="托管买入算法状态",
          description="可恢复规则阶段、pending 屏障和因果观察状态。",
        )
      },
    )

  @classmethod
  def get_data_requirements(cls) -> Dict[str, Any]:
    return {"use_tick_data": True, "periods": ["1m", "1d"]}

  async def on_init(self) -> None:
    raw = _mapping(self.context.parameters.get(MANAGED_ENTRY_STATE_KEY))
    self._config = ManagedEntryPlanConfig.from_dict(raw)
    if len(self.context.instruments) != 1:
      raise ValueError("managed entry plan requires exactly one instrument")
    if self.context.instruments[0] != self._config.instrument_code:
      raise ValueError("managed entry plan instrument does not match strategy run")

  async def on_stop(self) -> None:
    return None

  async def warmup(self, input: StrategyInput) -> None:
    # Historical windows are provided in StrategyInput. Warmup must not keep a
    # second hidden market-data history outside RuntimeStatePatch.
    del input
    return None

  async def step(self, input: StrategyInput) -> StrategyOutput:
    config = self._require_config()
    plan_id = self._plan_id()
    if input.instrument_code != config.instrument_code:
      return StrategyOutput(
        decision_tags=["entry_instrument_mismatch"],
        trace_payload={"reason": "ENTRY_INSTRUMENT_MISMATCH"},
      )
    if input.cadence not in {
      StrategyCadence.BAR,
      StrategyCadence.TICK,
      StrategyCadence.RECONCILE,
    }:
      return StrategyOutput()

    # The runtime itself must keep running while the entry side is paused: an
    # already-routed BUY can still receive an authoritative late fill and its
    # per-slice ExitPlan must keep consuming market data.  This durable product
    # flag gates only *new* entry evaluations.
    if self.context.parameters.get(ENTRY_PLAN_ENABLED_KEY) is not True:
      state = _state_from_input(input, self.state.to_dict())
      return StrategyOutput(
        decision_tags=["entry_plan_paused"],
        trace_payload={
          "reason": "ENTRY_PLAN_PAUSED",
          "plan_id": plan_id,
          "phase": state.phase.value,
        },
      )

    state = _state_from_input(input, self.state.to_dict())
    evaluation_context = _build_evaluation_context(
      input,
      config,
      state,
      plan_id=plan_id,
    )
    result = self._evaluator.evaluate(config, state, evaluation_context)
    patch = RuntimeStatePatch(set={MANAGED_ENTRY_STATE_KEY: result.state.to_dict()})
    self.state.set(
      MANAGED_ENTRY_STATE_KEY,
      result.state.to_dict(),
      persist=False,
      notify=False,
    )
    if result.decision is None:
      return StrategyOutput(
        runtime_state_patch=patch,
        decision_tags=[result.reason.lower()],
        trace_payload={
          "reason": result.reason,
          "plan_id": evaluation_context.plan_id,
          "phase": result.state.phase.value,
          "gap": _gap_trace(result.gap),
        },
      )

    decision = result.decision
    execution_mode = (
      TradeIntentExecutionMode.MANUAL_CONFIRM
      if config.execution_policy.authorization_mode
      == EntryAuthorizationMode.MANUAL_CONFIRM
      else TradeIntentExecutionMode.AUTO
    )
    intent_type = (
      TradeIntentType.TARGET_VOLUME
      if decision.target_volume is not None
      else TradeIntentType.TARGET_AMOUNT
    )
    intent = TradeIntent(
      strategy_id=input.strategy_id,
      run_id=input.run_id,
      origin=StrategyRunIntentOrigin(
        run_id=input.run_id,
        strategy_id=input.strategy_id,
        plan_id=plan_id,
      ),
      instrument_code=input.instrument_code,
      direction=TradeIntentDirection.BUY,
      bucket=config.bucket,
      reason=decision.reason,
      priority=TradeIntentPriority.NORMAL,
      intent_type=intent_type,
      target_amount=decision.target_amount_cny,
      target_volume=decision.target_volume,
      limit_price_hint=evaluation_context.executable_price,
      execution_mode=execution_mode,
      approval_ttl_ms=config.execution_policy.approval_ttl_ms,
      max_price_deviation_bps=config.execution_policy.max_price_deviation_bps,
      expiry_policy={
        "type": "EXPLICIT",
        "expire_at_ms": config.completion_policy.expire_at_ms,
      },
      metadata={
        "owner_type": "STRATEGY_RUN",
        "owner_id": input.run_id,
        "entry_plan_id": plan_id,
        "entry_config_version": config.config_version,
        "entry_rule_id": decision.rule_id,
        "entry_rule_type": decision.rule_type,
        "entry_stage_id": decision.stage_id,
        "entry_business_key": decision.business_key,
        "protected_limit_price": evaluation_context.executable_price,
        "max_buy_price": config.completion_policy.max_buy_price,
        "entry_cash_buffer_pct": config.pacing_policy.cash_buffer_pct,
        "exit_plan_template": _exit_plan_template_for_stage(
          config.exit_plan_template,
          plan_id=plan_id,
          run_id=input.run_id,
          stage_id=decision.stage_id,
        ),
      },
      trace_id=input.trace_id,
      intent_id=decision.intent_id,
      created_at=input.timestamp,
    )
    self.record_trade_intent(intent)
    return StrategyOutput(
      trade_intents=[intent],
      runtime_state_patch=patch,
      decision_tags=["managed_entry_intent", decision.rule_type.lower()],
      trace_payload={
        "reason": decision.reason,
        "plan_id": plan_id,
        "rule_id": decision.rule_id,
        "stage_id": decision.stage_id,
        "intent_id": decision.intent_id,
        "target_amount_cny": decision.target_amount_cny,
        "target_volume": decision.target_volume,
        "metrics": dict(decision.metrics),
      },
    )

  def pending_manual_intent_ids(self) -> list[str]:
    state = ManagedEntryPlanState.from_dict(
      _mapping(self.state.get(MANAGED_ENTRY_STATE_KEY))
    )
    if state.phase == EntryPlanStatus.AWAITING_APPROVAL and state.pending_intent_id:
      return [state.pending_intent_id]
    return []

  def validate_manual_approval(
    self, intent: TradeIntent, market_data: Any
  ) -> Optional[tuple[str, str]]:
    config = self._require_config()
    if intent.direction != TradeIntentDirection.BUY:
      return ("ENTRY_APPROVAL_DIRECTION_INVALID", "托管买入只允许确认 BUY 意图")
    price = _executable_price(market_data, None)
    if price <= 0:
      return ("ENTRY_APPROVAL_PRICE_MISSING", "缺少可执行买价")
    if price > config.completion_policy.max_buy_price:
      return ("ENTRY_MAX_BUY_PRICE_EXCEEDED", "当前价格超过计划最高可买价")
    return None

  async def on_order(self, event: OrderStateEvent) -> Optional[RuntimeStatePatch]:
    metadata = dict(event.metadata or {})
    if str(metadata.get("entry_plan_id", "")) != self._plan_id():
      return None
    status = str(event.status or "").split(".")[-1].upper()
    if status not in TERMINAL_ORDER_STATUSES:
      return None
    state = ManagedEntryPlanState.from_dict(
      _mapping(self.state.get(MANAGED_ENTRY_STATE_KEY))
    )
    timestamp = event.timestamp or self.context.current_time or datetime.min
    state.apply_order_terminal(
      status=status,
      timestamp_ms=int(timestamp.timestamp() * 1000),
      cooldown_after_reject_seconds=(
        self._require_config().pacing_policy.cooldown_after_reject_seconds
      ),
      expected_filled_volume=_terminal_expected_filled_volume(event, status),
      target_reached=state.cumulative_target_reached(self._require_config()),
    )
    snapshot = state.to_dict()
    self.state.set(MANAGED_ENTRY_STATE_KEY, snapshot, persist=False, notify=False)
    return RuntimeStatePatch(set={MANAGED_ENTRY_STATE_KEY: snapshot})

  async def on_trade(self, event: TradeExecutionEvent) -> Optional[RuntimeStatePatch]:
    metadata = dict(event.metadata or {})
    if str(metadata.get("entry_plan_id", "")) != self._plan_id():
      return None
    if event.instrument_code != self._require_config().instrument_code:
      return None
    if "BUY" not in str(event.trade_type or "").upper():
      return None
    state = ManagedEntryPlanState.from_dict(
      _mapping(self.state.get(MANAGED_ENTRY_STATE_KEY))
    )
    config = self._require_config()
    trade_time = event.trade_time or self.context.current_time or datetime.min
    trade_key = str(
      metadata.get("trade_id")
      or metadata.get("report_id")
      or (f"{event.order_id}:{trade_time.isoformat()}:{event.price:.8f}:{event.volume}")
    )
    changed = state.apply_trade_fill(
      trade_key=trade_key,
      volume=event.volume,
      price=event.price,
      trade_date=trade_time.date().isoformat(),
      timestamp_ms=int(trade_time.timestamp() * 1000),
      rule_id=str(metadata.get("entry_rule_id") or state.pending_rule_id),
      target_reached=_target_reached_after_fill(
        state,
        config,
        volume=event.volume,
        price=event.price,
      ),
    )
    if not changed:
      return None
    snapshot = state.to_dict()
    self.state.set(MANAGED_ENTRY_STATE_KEY, snapshot, persist=False, notify=False)
    return RuntimeStatePatch(
      set={MANAGED_ENTRY_STATE_KEY: snapshot},
      append_events=[
        {
          "type": "ENTRY_PLAN_TRADE_APPLIED",
          "trade_key": trade_key,
          "volume": event.volume,
          "price": event.price,
          "stage_id": metadata.get("entry_stage_id"),
        }
      ],
    )

  def _require_config(self) -> ManagedEntryPlanConfig:
    if self._config is None:
      raw = _mapping(self.context.parameters.get(MANAGED_ENTRY_STATE_KEY))
      self._config = ManagedEntryPlanConfig.from_dict(raw)
    return self._config

  def _plan_id(self) -> str:
    binding = _mapping(self.context.parameters.get("_managed_plan_binding"))
    return str(binding.get("plan_id") or self.context.run_id)


def _target_reached_after_fill(
  state: ManagedEntryPlanState,
  config: ManagedEntryPlanConfig,
  *,
  volume: int,
  price: float,
) -> bool:
  projected = ManagedEntryPlanState.from_dict(state.to_dict())
  projected.filled_volume += max(0, int(volume or 0))
  projected.filled_amount_cny += max(0, int(volume or 0)) * max(0.0, float(price or 0))
  return projected.cumulative_target_reached(config)


def _terminal_expected_filled_volume(
  event: OrderStateEvent,
  status: str,
) -> Optional[int]:
  candidates: list[int] = []
  if event.filled_volume is not None and int(event.filled_volume) > 0:
    candidates.append(int(event.filled_volume))
  metadata = dict(event.metadata or {})
  for key in (
    "terminal_filled_volume",
    "traded_volume",
    "filled_volume",
    "executed_volume",
  ):
    if metadata.get(key) is not None and int(metadata[key] or 0) > 0:
      candidates.append(int(metadata[key]))
  if candidates:
    return max(candidates)
  if status == "FILLED":
    requested_volume = int(_get(event.request, "volume", 0) or 0)
    if requested_volume > 0:
      return requested_volume
  if status in {"REJECTED", "BROKER_REJECTED"}:
    return 0
  return None


def _state_from_input(
  input: StrategyInput, fallback: Mapping[str, Any]
) -> ManagedEntryPlanState:
  raw_state = _mapping(input.strategy_state)
  selected = raw_state.get(MANAGED_ENTRY_STATE_KEY)
  if selected is None:
    selected = _mapping(fallback).get(MANAGED_ENTRY_STATE_KEY)
  return ManagedEntryPlanState.from_dict(_mapping(selected))


def _exit_plan_template_for_stage(
  template: Optional[Mapping[str, Any]],
  *,
  plan_id: str,
  run_id: Optional[str] = None,
  stage_id: str,
) -> Optional[Dict[str, Any]]:
  """Bind one independent exit plan to one entry slice.

  All partial fills belonging to the same intent keep the same ``stage_id``
  and therefore converge into one protection plan.  A later tranche receives
  a different plan id, so its cost/peak/trailing state cannot rewrite an
  earlier tranche's protection.
  """

  if not template:
    return None
  result = dict(template)
  result.update(
    {
      "plan_id": f"entry:{plan_id}:slice:{stage_id}",
      "source_type": "ENTRY_PLAN",
      "source_id": stage_id,
      "run_id": str(run_id or plan_id),
    }
  )
  metadata = dict(_mapping(result.get("metadata")))
  metadata.update({"entry_plan_id": plan_id, "entry_stage_id": stage_id})
  result["metadata"] = metadata
  return result


def _build_evaluation_context(
  input: StrategyInput,
  config: ManagedEntryPlanConfig,
  state: ManagedEntryPlanState,
  *,
  plan_id: Optional[str] = None,
) -> EntryEvaluationContext:
  portfolio = _mapping(input.portfolio_state)
  account = _mapping(portfolio.get("account"))
  total_equity = _first_float(
    account,
    "total_equity_cny",
    "total_asset_cny",
    "total_asset",
    "cash_total",
  )
  position = _position(portfolio, input.instrument_code)
  volume = int(
    _get(
      position,
      "total_volume",
      _get(position, "long_volume", _get(position, "volume", 0)),
    )
    or 0
  )
  price = _executable_price(input.market_data, input.event)
  market_value = _first_float(
    position,
    "market_value_cny",
    "market_value",
    default=volume * price,
  )
  risk = _mapping(input.risk_caps)
  profile = _mapping(input.position_profile)
  allow_bucket = _mapping(profile.get("allow_bucket_buy"))
  facts = _mapping(_mapping(input.market_context).get("managed_entry_plan_facts"))
  state_filled_amount_cny = max(
    _float(state.filled_amount_cny),
    sum(max(0.0, _float(value)) for value in state.rule_filled_amounts_cny.values()),
  )
  state_filled_volume = max(
    int(state.filled_volume or 0),
    sum(max(0, int(value or 0)) for value in state.rule_filled_volumes.values()),
  )
  daily = _observations(
    _get(input.market_data, "daily_observations")
    or _get(input.market_data, "daily_bars")
    or _mapping(input.market_context).get("daily_observations")
    or []
  )
  intraday = _observations(
    _get(input.market_data, "intraday_observations")
    or _mapping(input.market_context).get("intraday_observations")
    or []
  )
  if input.cadence in {StrategyCadence.TICK, StrategyCadence.BAR} and price > 0:
    if not intraday or intraday[-1].timestamp_ms < input.decision_time_ms:
      intraday = (*intraday, CausalPriceObservation(input.decision_time_ms, price))
  manual_rule_id = _manual_rule_id(input.event)
  return EntryEvaluationContext(
    plan_id=str(plan_id or input.run_id),
    decision_time_ms=input.decision_time_ms,
    trade_date=input.trade_date,
    instrument_code=input.instrument_code,
    executable_price=price,
    total_equity_cny=total_equity,
    current_position_volume=max(0, volume),
    current_market_value_cny=max(0.0, market_value),
    pending_buys=tuple(_pending_buys(input.open_orders, input.instrument_code)),
    plan_filled_amount_cny=max(
      state_filled_amount_cny,
      _float(facts.get("filled_amount_cny")),
    ),
    plan_filled_volume=max(
      state_filled_volume,
      int(facts.get("filled_volume", 0) or 0),
    ),
    daily_filled_amount_cny=max(
      _float(facts.get("daily_filled_amount_cny")),
      _float(state.daily_filled_amounts_cny.get(input.trade_date, 0.0)),
    ),
    daily_order_count=max(
      int(facts.get("daily_order_count", 0) or 0),
      int(state.daily_order_counts.get(input.trade_date, 0) or 0),
    ),
    risk_max_buy_amount_cny=_optional_float(
      risk.get("max_buy_amount_cny", risk.get("max_new_buy_amount_today"))
    ),
    liquidity_cap_cny=_optional_float(
      _mapping(input.execution_profile).get("liquidity_cap_cny")
    ),
    data_quality=str(
      _mapping(input.market_context).get("data_quality")
      or _get(input.market_data, "data_quality", "INSUFFICIENT")
      or "INSUFFICIENT"
    ).upper(),
    allow_buy=risk.get("allow_buy") is not False,
    allow_bucket_buy=(
      allow_bucket.get(config.bucket, profile.get(f"allow_{config.bucket}_buy", True))
      is not False
    ),
    only_risk_reduction=bool(
      risk.get("only_risk_reduction") or risk.get("only_reduce_position")
    ),
    kill_switch=bool(risk.get("kill_switch") or risk.get("kill_switch_active")),
    conflicting_sell=_has_conflicting_sell(input.open_orders, input.instrument_code),
    market_ready=bool(
      _mapping(input.market_context).get("market_ready", True)
      and not _mapping(input.market_context).get("reconcile_required", False)
    ),
    manual_trigger_rule_id=manual_rule_id,
    daily_observations=tuple(daily),
    intraday_observations=tuple(intraday),
  )


def _pending_buys(
  orders: Iterable[Any], instrument_code: str
) -> Iterable[PendingBuyExposure]:
  for order in orders or []:
    code = str(_get(order, "instrument_code", _get(order, "stock_code", "")) or "")
    direction = str(
      _get(order, "direction", _get(order, "side", _get(order, "order_type", ""))) or ""
    ).upper()
    status = str(_get(order, "status", "") or "").split(".")[-1].upper()
    if code != instrument_code or "BUY" not in direction:
      continue
    if status in TERMINAL_ORDER_STATUSES:
      continue
    requested = int(
      _get(
        order, "requested_volume", _get(order, "volume", _get(order, "order_volume", 0))
      )
      or 0
    )
    filled = int(_get(order, "filled_volume", _get(order, "traded_volume", 0)) or 0)
    price = _first_float(
      order,
      "protected_limit_price",
      "limit_price",
      "price",
      "order_price",
    )
    remaining = int(_get(order, "remaining_volume", max(0, requested - filled)) or 0)
    if remaining > 0 and price > 0:
      yield PendingBuyExposure(
        remaining_volume=remaining,
        protected_limit_price=price,
        owner_plan_id=str(_get(order, "owner_id", "") or "") or None,
      )


def _has_conflicting_sell(orders: Iterable[Any], instrument_code: str) -> bool:
  for order in orders or []:
    code = str(_get(order, "instrument_code", _get(order, "stock_code", "")) or "")
    direction = str(
      _get(order, "direction", _get(order, "side", _get(order, "order_type", ""))) or ""
    ).upper()
    status = str(_get(order, "status", "") or "").split(".")[-1].upper()
    if (
      code == instrument_code
      and "SELL" in direction
      and status not in TERMINAL_ORDER_STATUSES
    ):
      return True
  return False


def _position(portfolio: Mapping[str, Any], instrument_code: str) -> Any:
  positions = portfolio.get("positions", portfolio.get("position", {}))
  if isinstance(positions, Mapping):
    selected = positions.get(instrument_code)
    if selected is not None:
      return selected
    if positions.get("instrument_code") == instrument_code:
      return positions
  if isinstance(positions, Sequence) and not isinstance(positions, (str, bytes)):
    for item in positions:
      if (
        str(_get(item, "instrument_code", _get(item, "stock_code", "")))
        == instrument_code
      ):
        return item
  return {}


def _observations(raw: Iterable[Any]) -> tuple[CausalPriceObservation, ...]:
  result: list[CausalPriceObservation] = []
  for item in raw or []:
    timestamp_ms = _timestamp_ms(
      _get(item, "timestamp_ms", _get(item, "time", _get(item, "timestamp")))
    )
    price = _float(
      _get(item, "price", _get(item, "close", _get(item, "last_price", 0.0)))
    )
    if timestamp_ms is None:
      # Missing time cannot prove causality, so keep an invalid future marker.
      timestamp_ms = 2**63 - 1
    result.append(
      CausalPriceObservation(
        timestamp_ms=timestamp_ms,
        price=price,
        volume=_optional_float(_get(item, "volume", _get(item, "cumulative_volume"))),
      )
    )
  return tuple(result)


def _manual_rule_id(event: Any) -> Optional[str]:
  event_type = str(
    _get(event, "command", _get(event, "type", _get(event, "event_type", ""))) or ""
  ).upper()
  if event_type not in {"MANUAL_ENTRY_TRIGGER", "ENTRY_PLAN_MANUAL_TRIGGER"}:
    return None
  return str(_get(event, "rule_id", "") or "") or None


def _executable_price(market_data: Any, event: Any) -> float:
  ask_prices = _get(market_data, "ask_prices", _get(market_data, "ask_price", []))
  if isinstance(ask_prices, Sequence) and not isinstance(ask_prices, (str, bytes)):
    ask1 = _float(ask_prices[0]) if ask_prices else 0.0
  else:
    ask1 = _float(ask_prices)
  return next(
    (
      value
      for value in (
        ask1,
        _float(_get(market_data, "ask1")),
        _float(_get(event, "ask1")),
        _float(_get(market_data, "price")),
        _float(_get(event, "last_price")),
        _float(_get(market_data, "close")),
        _float(_get(event, "close")),
      )
      if value > 0
    ),
    0.0,
  )


def _gap_trace(gap: Any) -> Optional[Dict[str, Any]]:
  if gap is None:
    return None
  return {
    "remaining_amount_cny": gap.remaining_amount_cny,
    "remaining_volume": gap.remaining_volume,
    "pending_amount_cny": gap.pending_amount_cny,
    "pending_volume": gap.pending_volume,
    "position_cap_remaining_cny": gap.position_cap_remaining_cny,
    "plan_budget_remaining_cny": gap.plan_budget_remaining_cny,
  }


def _mapping(value: Any) -> Mapping[str, Any]:
  return value if isinstance(value, Mapping) else {}


def _get(source: Any, key: str, default: Any = None) -> Any:
  if source is None:
    return default
  if isinstance(source, Mapping):
    return source.get(key, default)
  return getattr(source, key, default)


def _float(value: Any, default: float = 0.0) -> float:
  try:
    return float(value)
  except (TypeError, ValueError):
    return default


def _optional_float(value: Any) -> Optional[float]:
  if value is None:
    return None
  try:
    return float(value)
  except (TypeError, ValueError):
    return None


def _first_float(source: Any, *keys: str, default: float = 0.0) -> float:
  for key in keys:
    value = _get(source, key)
    if value is not None:
      return _float(value, default)
  return default


def _timestamp_ms(value: Any) -> Optional[int]:
  if isinstance(value, datetime):
    return int(value.timestamp() * 1000)
  if isinstance(value, (int, float)):
    number = int(value)
    return number * 1000 if number < 10_000_000_000 else number
  if isinstance(value, str) and value:
    try:
      return int(datetime.fromisoformat(value).timestamp() * 1000)
    except ValueError:
      return None
  return None
