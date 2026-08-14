"""Strategy-agnostic automatic exit plans and composable sell rules.

Entry strategies describe how a filled lot should be protected.  This module
owns neither account state nor broker execution; it only keeps the exit-plan
state machine and turns market observations into deterministic exit decisions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from typing import Any, Callable, Dict, Iterable, Mapping, Optional

EXIT_PLAN_BOOK_STATE_KEY = "auto_exit_plan_book"


class ExitPlanStatus(str, Enum):
  PENDING_ENTRY = "PENDING_ENTRY"
  ACTIVE = "ACTIVE"
  EXIT_PENDING = "EXIT_PENDING"
  PARTIALLY_EXITED = "PARTIALLY_EXITED"
  COMPLETED = "COMPLETED"
  PAUSED = "PAUSED"
  CANCELLED = "CANCELLED"
  ERROR = "ERROR"


class ExitRuleType(str, Enum):
  """Built-in sell strategies.

  More strategies can be added through :class:`ExitStrategyRegistry` without
  changing the plan state machine or broker execution path.
  """

  TARGET_PRICE = "TARGET_PRICE"
  STOP_PRICE = "STOP_PRICE"
  GROSS_TAKE_PROFIT = "GROSS_TAKE_PROFIT"
  NET_TAKE_PROFIT = "NET_TAKE_PROFIT"
  TRAILING_NET_PROFIT = "TRAILING_NET_PROFIT"
  ADAPTIVE_VOLUME_PRICE_TRAILING = "ADAPTIVE_VOLUME_PRICE_TRAILING"
  RAPID_PROFIT_REVERSAL = "RAPID_PROFIT_REVERSAL"
  TRAILING_PRICE_DRAWDOWN = "TRAILING_PRICE_DRAWDOWN"
  HARD_STOP = "HARD_STOP"
  TIME_OF_DAY = "TIME_OF_DAY"
  MAX_HOLDING_DAYS = "MAX_HOLDING_DAYS"
  LIMIT_UP_TOUCH = "LIMIT_UP_TOUCH"
  LIMIT_UP_BREAK = "LIMIT_UP_BREAK"
  MANUAL_TRIGGER = "MANUAL_TRIGGER"


class ExitSizingMode(str, Enum):
  ALL_REMAINING = "ALL_REMAINING"
  PERCENT_REMAINING = "PERCENT_REMAINING"
  FIXED_VOLUME = "FIXED_VOLUME"


class ExitT1Policy(str, Enum):
  """How a sell plan behaves when its own newly bought shares are not sellable."""

  WAIT_UNTIL_SELLABLE = "WAIT_UNTIL_SELLABLE"
  ALLOW_SAME_INSTRUMENT_SUBSTITUTION = "ALLOW_SAME_INSTRUMENT_SUBSTITUTION"
  REJECT_IF_UNSELLABLE = "REJECT_IF_UNSELLABLE"


class ExitPriceReference(str, Enum):
  BID = "BID"
  LAST = "LAST"
  ASK = "ASK"


class ExitPlanCommandType(str, Enum):
  UPSERT_POLICY = "UPSERT_POLICY"
  PAUSE = "PAUSE"
  RESUME = "RESUME"
  CANCEL = "CANCEL"


@dataclass(frozen=True)
class TradingCostPolicy:
  """Conservative A-share round-trip transaction cost policy."""

  commission_rate: float = 0.0003
  minimum_commission: float = 5.0
  stamp_tax_rate: float = 0.0005
  transfer_fee_rate: float = 0.00001

  def to_dict(self) -> Dict[str, float]:
    return {
      "commission_rate": float(self.commission_rate),
      "minimum_commission": float(self.minimum_commission),
      "stamp_tax_rate": float(self.stamp_tax_rate),
      "transfer_fee_rate": float(self.transfer_fee_rate),
    }

  @classmethod
  def from_dict(cls, value: Optional[Mapping[str, Any]]) -> "TradingCostPolicy":
    raw = dict(value or {})
    return cls(
      commission_rate=float(raw.get("commission_rate", 0.0003) or 0.0),
      minimum_commission=float(raw.get("minimum_commission", 5.0) or 0.0),
      stamp_tax_rate=float(raw.get("stamp_tax_rate", 0.0005) or 0.0),
      transfer_fee_rate=float(raw.get("transfer_fee_rate", 0.00001) or 0.0),
    )


@dataclass(frozen=True)
class TrailingProfitPolicy:
  target_profit_pct: float = 2.0
  base_floor_pct: float = 0.5
  initial_gap_pct: float = 1.5
  gap_slope: float = 0.25
  max_gap_pct: float = 3.0
  high_profit_arm_pct: Optional[float] = None
  high_profit_max_drawdown_pct: Optional[float] = None


def estimate_net_profit_pct(
  *,
  entry_price: float,
  exit_price: float,
  volume: int,
  costs: Optional[TradingCostPolicy] = None,
) -> float:
  """Estimate round-trip net return against the filled entry lot."""

  if entry_price <= 0 or exit_price <= 0 or volume <= 0:
    return 0.0
  policy = costs or TradingCostPolicy()
  buy_amount = entry_price * volume
  sell_amount = exit_price * volume
  buy_fee = max(policy.minimum_commission, buy_amount * policy.commission_rate)
  buy_fee += buy_amount * policy.transfer_fee_rate
  sell_fee = max(policy.minimum_commission, sell_amount * policy.commission_rate)
  sell_fee += sell_amount * (policy.stamp_tax_rate + policy.transfer_fee_rate)
  entry_cost = buy_amount + buy_fee
  if entry_cost <= 0:
    return 0.0
  return ((sell_amount - sell_fee) - entry_cost) / entry_cost * 100.0


def calculate_trailing_floor_pct(
  *,
  peak_profit_pct: float,
  previous_floor_pct: Optional[float] = None,
  policy: Optional[TrailingProfitPolicy] = None,
) -> Optional[float]:
  """Return a non-decreasing floor after the rule becomes armed."""

  config = policy or TrailingProfitPolicy()
  if peak_profit_pct < config.target_profit_pct:
    return previous_floor_pct
  gap = config.initial_gap_pct + config.gap_slope * (
    peak_profit_pct - config.target_profit_pct
  )
  gap = max(config.initial_gap_pct, min(config.max_gap_pct, gap))
  candidate = max(config.base_floor_pct, peak_profit_pct - gap)
  if (
    config.high_profit_arm_pct is not None
    and config.high_profit_max_drawdown_pct is not None
    and peak_profit_pct >= config.high_profit_arm_pct
  ):
    candidate = max(
      candidate,
      peak_profit_pct - config.high_profit_max_drawdown_pct,
    )
  if previous_floor_pct is None:
    return candidate
  return max(previous_floor_pct, candidate)


@dataclass(frozen=True)
class ExitSizingPolicy:
  mode: ExitSizingMode = ExitSizingMode.ALL_REMAINING
  value: Optional[float] = None
  lot_size: int = 100
  allow_odd_lot_full_exit: bool = True

  def __post_init__(self) -> None:
    object.__setattr__(self, "mode", ExitSizingMode(self.mode))
    if self.lot_size <= 0:
      raise ValueError("exit sizing lot_size must be positive")
    if self.mode == ExitSizingMode.PERCENT_REMAINING:
      if self.value is None or not 0 < float(self.value) <= 100:
        raise ValueError("percent exit sizing requires value in (0, 100]")
    if self.mode == ExitSizingMode.FIXED_VOLUME:
      if self.value is None or int(self.value) <= 0:
        raise ValueError("fixed exit sizing requires a positive volume")

  def calculate(self, remaining_volume: int) -> int:
    remaining = max(0, int(remaining_volume or 0))
    if remaining <= 0:
      return 0
    if self.mode == ExitSizingMode.ALL_REMAINING:
      requested = remaining
    elif self.mode == ExitSizingMode.PERCENT_REMAINING:
      requested = int(remaining * float(self.value or 0.0) / 100.0)
    else:
      requested = min(remaining, int(self.value or 0))
    requested = min(remaining, max(0, requested))
    if requested >= remaining and self.allow_odd_lot_full_exit:
      return remaining
    return (requested // self.lot_size) * self.lot_size

  def to_dict(self) -> Dict[str, Any]:
    return {
      "mode": self.mode.value,
      "value": self.value,
      "lot_size": int(self.lot_size),
      "allow_odd_lot_full_exit": bool(self.allow_odd_lot_full_exit),
    }

  @classmethod
  def from_dict(cls, value: Optional[Mapping[str, Any]]) -> "ExitSizingPolicy":
    raw = dict(value or {})
    return cls(
      mode=ExitSizingMode(raw.get("mode", ExitSizingMode.ALL_REMAINING.value)),
      value=raw.get("value"),
      lot_size=int(raw.get("lot_size", 100) or 100),
      allow_odd_lot_full_exit=bool(raw.get("allow_odd_lot_full_exit", True)),
    )


@dataclass(frozen=True)
class ExitRuleSpec:
  strategy: str
  parameters: Dict[str, Any] = field(default_factory=dict)
  priority: int = 500
  sizing: ExitSizingPolicy = field(default_factory=ExitSizingPolicy)
  enabled: bool = True
  once: bool = False
  rule_id: str = field(default_factory=lambda: str(uuid.uuid4()))

  def __post_init__(self) -> None:
    object.__setattr__(self, "strategy", _strategy_value(self.strategy))
    if isinstance(self.sizing, Mapping):
      object.__setattr__(self, "sizing", ExitSizingPolicy.from_dict(self.sizing))
    object.__setattr__(self, "parameters", dict(self.parameters or {}))

  def to_dict(self) -> Dict[str, Any]:
    return {
      "rule_id": self.rule_id,
      "strategy": self.strategy,
      "parameters": dict(self.parameters),
      "priority": int(self.priority),
      "sizing": self.sizing.to_dict(),
      "enabled": bool(self.enabled),
      "once": bool(self.once),
    }

  @classmethod
  def from_dict(cls, value: Mapping[str, Any]) -> "ExitRuleSpec":
    raw = dict(value or {})
    return cls(
      rule_id=str(raw.get("rule_id") or uuid.uuid4()),
      strategy=str(raw["strategy"]),
      parameters=dict(raw.get("parameters") or {}),
      priority=int(raw.get("priority", 500) or 500),
      sizing=ExitSizingPolicy.from_dict(raw.get("sizing")),
      enabled=bool(raw.get("enabled", True)),
      once=bool(raw.get("once", False)),
    )


@dataclass(frozen=True)
class ExitExecutionPolicy:
  price_reference: ExitPriceReference = ExitPriceReference.BID
  price_type: str = "LIMIT"
  protected_limit: bool = True
  max_slippage_bps: float = 30.0
  urgency: str = "PROTECTIVE_EXIT"
  execution_mode: str = "AUTO"

  def __post_init__(self) -> None:
    object.__setattr__(
      self, "price_reference", ExitPriceReference(self.price_reference)
    )

  def to_dict(self) -> Dict[str, Any]:
    return {
      "price_reference": self.price_reference.value,
      "price_type": str(self.price_type or "LIMIT").upper(),
      "protected_limit": bool(self.protected_limit),
      "max_slippage_bps": float(self.max_slippage_bps),
      "urgency": self.urgency,
      "execution_mode": str(self.execution_mode or "AUTO").upper(),
    }

  @classmethod
  def from_dict(cls, value: Optional[Mapping[str, Any]]) -> "ExitExecutionPolicy":
    raw = dict(value or {})
    return cls(
      price_reference=ExitPriceReference(
        raw.get("price_reference", ExitPriceReference.BID.value)
      ),
      price_type=str(raw.get("price_type", "LIMIT") or "LIMIT").upper(),
      protected_limit=bool(raw.get("protected_limit", True)),
      max_slippage_bps=float(raw.get("max_slippage_bps", 30.0) or 0.0),
      urgency=str(raw.get("urgency", "PROTECTIVE_EXIT") or ""),
      execution_mode=str(raw.get("execution_mode", "AUTO") or "AUTO").upper(),
    )


@dataclass(frozen=True)
class ExitPlanTemplate:
  plan_id: str
  source_type: str
  source_id: str
  account_id: str
  instrument_code: str
  bucket: str
  rules: list[ExitRuleSpec]
  strategy_id: str = ""
  run_id: str = ""
  config_version: int = 1
  costs: TradingCostPolicy = field(default_factory=TradingCostPolicy)
  t1_policy: ExitT1Policy = ExitT1Policy.WAIT_UNTIL_SELLABLE
  execution: ExitExecutionPolicy = field(default_factory=ExitExecutionPolicy)
  metadata: Dict[str, Any] = field(default_factory=dict)
  auto_exit_authorized: bool = False

  def __post_init__(self) -> None:
    if not self.plan_id or not self.instrument_code or not self.bucket:
      raise ValueError("exit plan template requires plan_id, instrument and bucket")
    object.__setattr__(
      self,
      "rules",
      [
        item if isinstance(item, ExitRuleSpec) else ExitRuleSpec.from_dict(item)
        for item in list(self.rules or [])
      ],
    )
    if not self.rules:
      raise ValueError("exit plan template requires at least one sell rule")
    if isinstance(self.costs, Mapping):
      object.__setattr__(self, "costs", TradingCostPolicy.from_dict(self.costs))
    if isinstance(self.execution, Mapping):
      object.__setattr__(
        self, "execution", ExitExecutionPolicy.from_dict(self.execution)
      )
    object.__setattr__(self, "t1_policy", ExitT1Policy(self.t1_policy))
    object.__setattr__(self, "metadata", dict(self.metadata or {}))

  def to_dict(self) -> Dict[str, Any]:
    return {
      "plan_id": self.plan_id,
      "source_type": self.source_type,
      "source_id": self.source_id,
      "account_id": self.account_id,
      "instrument_code": self.instrument_code,
      "bucket": self.bucket,
      "rules": [rule.to_dict() for rule in self.rules],
      "strategy_id": self.strategy_id,
      "run_id": self.run_id,
      "config_version": int(self.config_version),
      "costs": self.costs.to_dict(),
      "t1_policy": self.t1_policy.value,
      "execution": self.execution.to_dict(),
      "metadata": dict(self.metadata),
      "auto_exit_authorized": bool(self.auto_exit_authorized),
    }

  @classmethod
  def from_dict(cls, value: Mapping[str, Any]) -> "ExitPlanTemplate":
    raw = dict(value or {})
    return cls(
      plan_id=str(raw["plan_id"]),
      source_type=str(raw.get("source_type", "STRATEGY_ENTRY") or ""),
      source_id=str(raw.get("source_id", "") or ""),
      account_id=str(raw.get("account_id", "") or ""),
      instrument_code=str(raw["instrument_code"]),
      bucket=str(raw["bucket"]),
      rules=[ExitRuleSpec.from_dict(item) for item in list(raw.get("rules") or [])],
      strategy_id=str(raw.get("strategy_id", "") or ""),
      run_id=str(raw.get("run_id", "") or ""),
      config_version=int(raw.get("config_version", 1) or 1),
      costs=TradingCostPolicy.from_dict(raw.get("costs")),
      t1_policy=ExitT1Policy(
        raw.get("t1_policy", ExitT1Policy.WAIT_UNTIL_SELLABLE.value)
      ),
      execution=ExitExecutionPolicy.from_dict(raw.get("execution")),
      metadata=dict(raw.get("metadata") or {}),
      auto_exit_authorized=bool(raw.get("auto_exit_authorized", False)),
    )


@dataclass
class ExitPlan:
  template: ExitPlanTemplate
  status: ExitPlanStatus = ExitPlanStatus.PENDING_ENTRY
  entry_filled_volume: int = 0
  entry_avg_price: float = 0.0
  exited_volume: int = 0
  exit_avg_price: float = 0.0
  peak_price: float = 0.0
  last_price: float = 0.0
  last_net_profit_pct: float = 0.0
  peak_net_profit_pct: float = 0.0
  trailing_floor_pct: Optional[float] = None
  entry_trade_date: str = ""
  last_holding_trade_date: str = ""
  holding_trading_days: int = 0
  pending_intent_id: str = ""
  pending_order_id: str = ""
  pending_rule_id: str = ""
  pending_requested_volume: int = 0
  pending_filled_volume: int = 0
  pending_order_terminal: bool = False
  rule_state: Dict[str, Dict[str, Any]] = field(default_factory=dict)
  rule_target_volumes: Dict[str, int] = field(default_factory=dict)
  rule_filled_volumes: Dict[str, int] = field(default_factory=dict)
  completed_rule_ids: list[str] = field(default_factory=list)
  last_exit_reason: str = ""
  last_evaluated_at: str = ""
  retry_after_ms: int = 0
  error_message: str = ""

  def __post_init__(self) -> None:
    if isinstance(self.template, Mapping):
      self.template = ExitPlanTemplate.from_dict(self.template)
    self.status = ExitPlanStatus(self.status)

  @property
  def plan_id(self) -> str:
    return self.template.plan_id

  @property
  def remaining_volume(self) -> int:
    return max(0, int(self.entry_filled_volume) - int(self.exited_volume))

  def register_entry_fill(
    self,
    *,
    volume: int,
    price: float,
    trade_time: Optional[datetime] = None,
  ) -> None:
    fill_volume = max(0, int(volume or 0))
    if fill_volume <= 0 or price <= 0:
      return
    previous = int(self.entry_filled_volume or 0)
    total = previous + fill_volume
    self.entry_avg_price = (
      self.entry_avg_price * previous + float(price) * fill_volume
    ) / total
    self.entry_filled_volume = total
    self.status = ExitPlanStatus.ACTIVE
    if trade_time:
      trade_date = trade_time.date().isoformat()
      if not self.entry_trade_date:
        self.entry_trade_date = trade_date
        self.last_holding_trade_date = trade_date
        self.holding_trading_days = 1

  def apply_template(self, template: ExitPlanTemplate) -> None:
    if template.plan_id != self.plan_id:
      raise ValueError("cannot replace an exit plan with a different plan_id")
    self.template = template

  def projection(self) -> Dict[str, Any]:
    return {
      **self.to_dict(),
      "remaining_volume": self.remaining_volume,
      "profit_armed": self.trailing_floor_pct is not None,
    }

  def to_dict(self) -> Dict[str, Any]:
    return {
      "template": self.template.to_dict(),
      "status": self.status.value,
      "entry_filled_volume": int(self.entry_filled_volume),
      "entry_avg_price": float(self.entry_avg_price),
      "exited_volume": int(self.exited_volume),
      "exit_avg_price": float(self.exit_avg_price),
      "peak_price": float(self.peak_price),
      "last_price": float(self.last_price),
      "last_net_profit_pct": float(self.last_net_profit_pct),
      "peak_net_profit_pct": float(self.peak_net_profit_pct),
      "trailing_floor_pct": self.trailing_floor_pct,
      "entry_trade_date": self.entry_trade_date,
      "last_holding_trade_date": self.last_holding_trade_date,
      "holding_trading_days": int(self.holding_trading_days),
      "pending_intent_id": self.pending_intent_id,
      "pending_order_id": self.pending_order_id,
      "pending_rule_id": self.pending_rule_id,
      "pending_requested_volume": int(self.pending_requested_volume),
      "pending_filled_volume": int(self.pending_filled_volume),
      "pending_order_terminal": bool(self.pending_order_terminal),
      "rule_state": {
        str(key): dict(value or {}) for key, value in self.rule_state.items()
      },
      "rule_target_volumes": {
        str(key): int(value) for key, value in self.rule_target_volumes.items()
      },
      "rule_filled_volumes": {
        str(key): int(value) for key, value in self.rule_filled_volumes.items()
      },
      "completed_rule_ids": list(self.completed_rule_ids),
      "last_exit_reason": self.last_exit_reason,
      "last_evaluated_at": self.last_evaluated_at,
      "retry_after_ms": int(self.retry_after_ms),
      "error_message": self.error_message,
    }

  @classmethod
  def from_dict(cls, value: Mapping[str, Any]) -> "ExitPlan":
    raw = dict(value or {})
    return cls(
      template=ExitPlanTemplate.from_dict(raw["template"]),
      status=ExitPlanStatus(raw.get("status", ExitPlanStatus.PENDING_ENTRY.value)),
      entry_filled_volume=int(raw.get("entry_filled_volume", 0) or 0),
      entry_avg_price=float(raw.get("entry_avg_price", 0.0) or 0.0),
      exited_volume=int(raw.get("exited_volume", 0) or 0),
      exit_avg_price=float(raw.get("exit_avg_price", 0.0) or 0.0),
      peak_price=float(raw.get("peak_price", 0.0) or 0.0),
      last_price=float(raw.get("last_price", 0.0) or 0.0),
      last_net_profit_pct=float(raw.get("last_net_profit_pct", 0.0) or 0.0),
      peak_net_profit_pct=float(raw.get("peak_net_profit_pct", 0.0) or 0.0),
      trailing_floor_pct=_optional_float(raw.get("trailing_floor_pct")),
      entry_trade_date=str(raw.get("entry_trade_date", "") or ""),
      last_holding_trade_date=str(raw.get("last_holding_trade_date", "") or ""),
      holding_trading_days=int(raw.get("holding_trading_days", 0) or 0),
      pending_intent_id=str(raw.get("pending_intent_id", "") or ""),
      pending_order_id=str(raw.get("pending_order_id", "") or ""),
      pending_rule_id=str(raw.get("pending_rule_id", "") or ""),
      pending_requested_volume=int(raw.get("pending_requested_volume", 0) or 0),
      pending_filled_volume=int(raw.get("pending_filled_volume", 0) or 0),
      pending_order_terminal=bool(raw.get("pending_order_terminal", False)),
      rule_state={
        str(key): dict(value or {})
        for key, value in dict(raw.get("rule_state") or {}).items()
      },
      rule_target_volumes={
        str(key): int(value or 0)
        for key, value in dict(raw.get("rule_target_volumes") or {}).items()
      },
      rule_filled_volumes={
        str(key): int(value or 0)
        for key, value in dict(raw.get("rule_filled_volumes") or {}).items()
      },
      completed_rule_ids=[
        str(item) for item in list(raw.get("completed_rule_ids") or [])
      ],
      last_exit_reason=str(raw.get("last_exit_reason", "") or ""),
      last_evaluated_at=str(raw.get("last_evaluated_at", "") or ""),
      retry_after_ms=int(raw.get("retry_after_ms", 0) or 0),
      error_message=str(raw.get("error_message", "") or ""),
    )


@dataclass(frozen=True)
class ExitEvaluationContext:
  timestamp: datetime
  current_price: float
  bid_price: float = 0.0
  ask_price: float = 0.0
  limit_up: float = 0.0
  limit_down: float = 0.0
  price_tick: float = 0.01
  cumulative_volume: Optional[float] = None
  cumulative_amount: Optional[float] = None
  depth_imbalance_5: Optional[float] = None
  market_data_age_seconds: float = 0.0
  volume_data_age_seconds: float = 0.0
  source: str = ""

  @property
  def trade_date(self) -> str:
    return self.timestamp.date().isoformat()

  @property
  def timestamp_ms(self) -> int:
    return int(self.timestamp.timestamp() * 1000)


@dataclass(frozen=True)
class ExitRuleMatch:
  triggered: bool
  reason: str = ""
  metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExitDecision:
  plan_id: str
  rule_id: str
  rule_type: str
  reason: str
  volume: int
  priority: int
  metrics: Dict[str, Any] = field(default_factory=dict)


ExitRuleEvaluator = Callable[
  [ExitRuleSpec, ExitPlan, ExitEvaluationContext], ExitRuleMatch
]


class ExitStrategyRegistry:
  """Registry of sell-rule evaluators used by all entry features."""

  def __init__(self) -> None:
    self._evaluators: Dict[str, ExitRuleEvaluator] = {}

  def register(
    self, strategy: str | ExitRuleType, evaluator: ExitRuleEvaluator
  ) -> None:
    self._evaluators[_strategy_value(strategy)] = evaluator

  def evaluate(
    self,
    rule: ExitRuleSpec,
    plan: ExitPlan,
    context: ExitEvaluationContext,
  ) -> ExitRuleMatch:
    evaluator = self._evaluators.get(rule.strategy)
    if evaluator is None:
      raise ValueError(f"exit strategy is not registered: {rule.strategy}")
    return evaluator(rule, plan, context)

  @classmethod
  def builtins(cls) -> "ExitStrategyRegistry":
    registry = cls()
    registry.register(ExitRuleType.TARGET_PRICE, _target_price)
    registry.register(ExitRuleType.STOP_PRICE, _stop_price)
    registry.register(ExitRuleType.GROSS_TAKE_PROFIT, _gross_take_profit)
    registry.register(ExitRuleType.NET_TAKE_PROFIT, _net_take_profit)
    registry.register(ExitRuleType.TRAILING_NET_PROFIT, _trailing_net_profit)
    registry.register(
      ExitRuleType.ADAPTIVE_VOLUME_PRICE_TRAILING,
      _adaptive_volume_price_trailing,
    )
    registry.register(ExitRuleType.RAPID_PROFIT_REVERSAL, _rapid_profit_reversal)
    registry.register(ExitRuleType.TRAILING_PRICE_DRAWDOWN, _trailing_price_drawdown)
    registry.register(ExitRuleType.HARD_STOP, _hard_stop)
    registry.register(ExitRuleType.TIME_OF_DAY, _time_of_day)
    registry.register(ExitRuleType.MAX_HOLDING_DAYS, _max_holding_days)
    registry.register(ExitRuleType.LIMIT_UP_TOUCH, _limit_up_touch)
    registry.register(ExitRuleType.LIMIT_UP_BREAK, _limit_up_break)
    registry.register(ExitRuleType.MANUAL_TRIGGER, _manual_trigger)
    return registry


class ExitPlanEvaluator:
  def __init__(self, registry: Optional[ExitStrategyRegistry] = None) -> None:
    self.registry = registry or ExitStrategyRegistry.builtins()

  def evaluate(
    self, plan: ExitPlan, context: ExitEvaluationContext
  ) -> Optional[ExitDecision]:
    if plan.status not in {
      ExitPlanStatus.ACTIVE,
      ExitPlanStatus.PARTIALLY_EXITED,
    }:
      return None
    if plan.pending_intent_id or plan.remaining_volume <= 0:
      return None
    has_manual_trigger = any(
      rule.enabled and rule.strategy == ExitRuleType.MANUAL_TRIGGER.value
      for rule in plan.template.rules
    )
    if context.current_price <= 0 and context.bid_price <= 0 and not has_manual_trigger:
      return None
    if context.timestamp_ms < int(plan.retry_after_ms or 0):
      return None

    self._observe(plan, context)
    matches: list[tuple[ExitRuleSpec, ExitRuleMatch]] = []
    completed = set(plan.completed_rule_ids)
    for rule in plan.template.rules:
      if not rule.enabled or (rule.once and rule.rule_id in completed):
        continue
      result = self.registry.evaluate(rule, plan, context)
      if result.triggered:
        matches.append((rule, result))
    if not matches:
      return None
    rule, match = max(matches, key=lambda item: (item[0].priority, item[0].rule_id))
    volume = rule.sizing.calculate(plan.remaining_volume)
    if rule.once and rule.rule_id in plan.rule_target_volumes:
      volume = min(
        plan.remaining_volume,
        max(
          0,
          int(plan.rule_target_volumes.get(rule.rule_id, 0) or 0)
          - int(plan.rule_filled_volumes.get(rule.rule_id, 0) or 0),
        ),
      )
    if volume <= 0:
      return None
    return ExitDecision(
      plan_id=plan.plan_id,
      rule_id=rule.rule_id,
      rule_type=rule.strategy,
      reason=match.reason or rule.strategy,
      volume=volume,
      priority=int(rule.priority),
      metrics={
        "current_price": context.current_price,
        "profit_reference": plan.template.execution.price_reference.value,
        "profit_reference_price": plan.last_price,
        "last_net_profit_pct": plan.last_net_profit_pct,
        "peak_net_profit_pct": plan.peak_net_profit_pct,
        "trailing_floor_pct": plan.trailing_floor_pct,
        "holding_trading_days": plan.holding_trading_days,
        **dict(match.metrics or {}),
      },
    )

  @staticmethod
  def _observe(plan: ExitPlan, context: ExitEvaluationContext) -> None:
    reference = plan.template.execution.price_reference
    if reference == ExitPriceReference.BID:
      reference_price = float(context.bid_price or context.current_price or 0.0)
    elif reference == ExitPriceReference.ASK:
      reference_price = float(context.ask_price or context.current_price or 0.0)
    else:
      reference_price = float(context.current_price or 0.0)
    if reference_price <= 0:
      return
    plan.last_price = reference_price
    plan.peak_price = max(float(plan.peak_price or 0.0), reference_price)
    plan.last_evaluated_at = context.timestamp.isoformat()
    if not plan.entry_trade_date:
      plan.entry_trade_date = context.trade_date
      plan.last_holding_trade_date = context.trade_date
      plan.holding_trading_days = 1
    elif context.trade_date > plan.last_holding_trade_date:
      plan.last_holding_trade_date = context.trade_date
      plan.holding_trading_days = int(plan.holding_trading_days or 0) + 1
    plan.last_net_profit_pct = estimate_net_profit_pct(
      entry_price=plan.entry_avg_price,
      exit_price=reference_price,
      volume=plan.remaining_volume,
      costs=plan.template.costs,
    )
    plan.peak_net_profit_pct = max(
      float(plan.peak_net_profit_pct or 0.0),
      plan.last_net_profit_pct,
    )


@dataclass(frozen=True)
class ExitPlanCommand:
  command: ExitPlanCommandType
  plan_id: str
  template: Optional[ExitPlanTemplate] = None
  reason: str = ""

  def __post_init__(self) -> None:
    object.__setattr__(self, "command", ExitPlanCommandType(self.command))
    if isinstance(self.template, Mapping):
      object.__setattr__(self, "template", ExitPlanTemplate.from_dict(self.template))


class ExitPlanBook:
  """Serializable lifecycle owner for all plans hosted by one strategy run."""

  VERSION = 1

  def __init__(
    self,
    plans: Optional[Iterable[ExitPlan]] = None,
    *,
    evaluator: Optional[ExitPlanEvaluator] = None,
  ) -> None:
    self.plans = {plan.plan_id: plan for plan in list(plans or [])}
    self.evaluator = evaluator or ExitPlanEvaluator()

  def register_entry_fill(
    self,
    template: ExitPlanTemplate | Mapping[str, Any],
    *,
    volume: int,
    price: float,
    trade_time: Optional[datetime] = None,
  ) -> ExitPlan:
    resolved = (
      template
      if isinstance(template, ExitPlanTemplate)
      else ExitPlanTemplate.from_dict(template)
    )
    plan = self.plans.get(resolved.plan_id)
    if plan is None:
      plan = ExitPlan(template=resolved)
      self.plans[resolved.plan_id] = plan
    elif resolved.config_version >= plan.template.config_version:
      plan.apply_template(resolved)
    plan.register_entry_fill(volume=volume, price=price, trade_time=trade_time)
    return plan

  def apply_command(self, command: ExitPlanCommand) -> Optional[ExitPlan]:
    plan = self.plans.get(command.plan_id)
    if command.command == ExitPlanCommandType.UPSERT_POLICY:
      if plan is None or command.template is None:
        return plan
      if command.template.config_version >= plan.template.config_version:
        plan.apply_template(command.template)
      return plan
    if plan is None:
      return None
    if command.command == ExitPlanCommandType.PAUSE:
      plan.status = ExitPlanStatus.PAUSED
    elif command.command == ExitPlanCommandType.RESUME:
      if plan.remaining_volume > 0:
        plan.status = ExitPlanStatus.ACTIVE
    elif command.command == ExitPlanCommandType.CANCEL:
      plan.status = ExitPlanStatus.CANCELLED
      plan.error_message = command.reason
    return plan

  def evaluate(
    self,
    instrument_code: str,
    context: ExitEvaluationContext,
  ) -> list[ExitDecision]:
    decisions = []
    for plan in self.plans.values():
      if plan.template.instrument_code != instrument_code:
        continue
      decision = self.evaluator.evaluate(plan, context)
      if decision:
        decisions.append(decision)
    return sorted(decisions, key=lambda item: item.priority, reverse=True)

  def mark_intent(self, decision: ExitDecision, intent_id: str) -> Optional[ExitPlan]:
    plan = self.plans.get(decision.plan_id)
    if plan is None:
      return None
    plan.pending_intent_id = intent_id
    plan.pending_rule_id = decision.rule_id
    plan.pending_requested_volume = int(decision.volume)
    plan.pending_filled_volume = 0
    plan.pending_order_terminal = False
    rule = self._rule(plan, decision.rule_id)
    if rule and rule.once and rule.rule_id not in plan.rule_target_volumes:
      plan.rule_target_volumes[rule.rule_id] = int(decision.volume)
      plan.rule_filled_volumes.setdefault(rule.rule_id, 0)
    plan.last_exit_reason = decision.reason
    plan.status = ExitPlanStatus.EXIT_PENDING
    return plan

  def apply_order_event(
    self,
    *,
    plan_id: str,
    intent_id: str,
    status: str,
    order_id: str = "",
    risk_action: str = "",
    timestamp_ms: int = 0,
  ) -> Optional[ExitPlan]:
    plan = self.plans.get(plan_id)
    if (
      plan is None or not plan.pending_intent_id or intent_id != plan.pending_intent_id
    ):
      return plan
    normalized = str(status or "").upper()
    if order_id:
      plan.pending_order_id = order_id
    if normalized in {"PENDING", "SUBMITTED", "ACCEPTED", "PARTIAL_FILLED"}:
      if str(risk_action or "").upper() == "DELAY" and not order_id:
        self._release_pending(plan)
        plan.retry_after_ms = max(
          int(plan.retry_after_ms or 0), int(timestamp_ms or 0) + 1000
        )
        plan.status = ExitPlanStatus.ACTIVE
      else:
        plan.status = ExitPlanStatus.EXIT_PENDING
      return plan
    if normalized in {"REJECTED", "CANCELLED", "EXPIRED"}:
      self._release_pending(plan)
      plan.status = (
        ExitPlanStatus.PARTIALLY_EXITED
        if plan.exited_volume > 0
        else ExitPlanStatus.ACTIVE
      )
      return plan
    if normalized == "FILLED":
      plan.pending_order_terminal = True
      if plan.pending_filled_volume >= plan.pending_requested_volume:
        self._finalize_pending(plan)
    return plan

  def apply_exit_fill(
    self,
    *,
    plan_id: str,
    volume: int,
    price: float,
    rule_id: str = "",
  ) -> Optional[ExitPlan]:
    plan = self.plans.get(plan_id)
    fill_volume = max(0, int(volume or 0))
    if plan is None or fill_volume <= 0 or price <= 0:
      return plan
    had_pending = bool(plan.pending_intent_id)
    previous = int(plan.exited_volume or 0)
    applied = min(fill_volume, plan.remaining_volume)
    total = previous + applied
    if applied <= 0:
      return plan
    plan.exit_avg_price = (
      plan.exit_avg_price * previous + float(price) * applied
    ) / total
    plan.exited_volume = total
    if had_pending:
      plan.pending_filled_volume += applied
    resolved_rule_id = str(rule_id or plan.pending_rule_id or "")
    if resolved_rule_id:
      plan.rule_filled_volumes[resolved_rule_id] = (
        int(plan.rule_filled_volumes.get(resolved_rule_id, 0) or 0) + applied
      )
    if plan.remaining_volume <= 0 or (
      had_pending
      and plan.pending_order_terminal
      and plan.pending_filled_volume >= plan.pending_requested_volume
    ):
      self._finalize_pending(plan)
    elif not had_pending:
      self._complete_once_rule(plan, resolved_rule_id)
      plan.status = ExitPlanStatus.PARTIALLY_EXITED
    else:
      plan.status = ExitPlanStatus.EXIT_PENDING
    return plan

  def projections(self, instrument_code: Optional[str] = None) -> list[Dict[str, Any]]:
    return [
      plan.projection()
      for plan in self.plans.values()
      if not instrument_code or plan.template.instrument_code == instrument_code
    ]

  def active_plans(self) -> list[ExitPlan]:
    return [
      plan
      for plan in self.plans.values()
      if plan.status
      not in {
        ExitPlanStatus.COMPLETED,
        ExitPlanStatus.CANCELLED,
      }
      and plan.remaining_volume > 0
    ]

  def prune_terminal(self, max_terminal: int = 200) -> list[str]:
    """Bound persisted history while never removing a protected live position."""

    limit = max(0, int(max_terminal or 0))
    terminal_ids = [
      plan_id
      for plan_id, plan in self.plans.items()
      if plan.status
      in {
        ExitPlanStatus.COMPLETED,
        ExitPlanStatus.CANCELLED,
      }
    ]
    removed = terminal_ids[: max(0, len(terminal_ids) - limit)]
    for plan_id in removed:
      self.plans.pop(plan_id, None)
    return removed

  def to_dict(self) -> Dict[str, Any]:
    return {
      "version": self.VERSION,
      "plans": {plan_id: plan.to_dict() for plan_id, plan in self.plans.items()},
    }

  @classmethod
  def from_dict(
    cls,
    value: Optional[Mapping[str, Any]],
    *,
    evaluator: Optional[ExitPlanEvaluator] = None,
  ) -> "ExitPlanBook":
    raw = dict(value or {})
    plans = []
    for item in dict(raw.get("plans") or {}).values():
      try:
        plans.append(ExitPlan.from_dict(item))
      except (KeyError, TypeError, ValueError):
        continue
    return cls(plans, evaluator=evaluator)

  @staticmethod
  def _release_pending(plan: ExitPlan) -> None:
    plan.pending_intent_id = ""
    plan.pending_order_id = ""
    plan.pending_rule_id = ""
    plan.pending_requested_volume = 0
    plan.pending_filled_volume = 0
    plan.pending_order_terminal = False

  def _finalize_pending(self, plan: ExitPlan) -> None:
    if plan.pending_rule_id:
      self._complete_once_rule(plan, plan.pending_rule_id)
    self._release_pending(plan)
    plan.status = (
      ExitPlanStatus.COMPLETED
      if plan.remaining_volume <= 0
      else ExitPlanStatus.PARTIALLY_EXITED
    )

  @classmethod
  def _complete_once_rule(cls, plan: ExitPlan, rule_id: str) -> None:
    if not rule_id:
      return
    rule = cls._rule(plan, rule_id)
    target = int(plan.rule_target_volumes.get(rule_id, 0) or 0)
    filled = int(plan.rule_filled_volumes.get(rule_id, 0) or 0)
    if (
      rule
      and rule.once
      and filled >= target > 0
      and rule.rule_id not in plan.completed_rule_ids
    ):
      plan.completed_rule_ids.append(rule.rule_id)

  @staticmethod
  def _rule(plan: ExitPlan, rule_id: str) -> Optional[ExitRuleSpec]:
    return next(
      (rule for rule in plan.template.rules if rule.rule_id == rule_id),
      None,
    )


def _threshold(rule: ExitRuleSpec, key: str, default: float = 0.0) -> float:
  return float(rule.parameters.get(key, default) or 0.0)


def _reason(rule: ExitRuleSpec, default: str) -> str:
  return str(rule.parameters.get("reason", default) or default)


def _target_price(
  rule: ExitRuleSpec, plan: ExitPlan, context: ExitEvaluationContext
) -> ExitRuleMatch:
  target = _threshold(rule, "target_price")
  return ExitRuleMatch(
    context.current_price >= target > 0,
    _reason(rule, "TARGET_PRICE_REACHED"),
    {"target_price": target},
  )


def _stop_price(
  rule: ExitRuleSpec, plan: ExitPlan, context: ExitEvaluationContext
) -> ExitRuleMatch:
  stop = _threshold(rule, "stop_price")
  return ExitRuleMatch(
    stop > 0 and context.current_price <= stop,
    _reason(rule, "STOP_PRICE_REACHED"),
    {"stop_price": stop},
  )


def _gross_take_profit(
  rule: ExitRuleSpec, plan: ExitPlan, context: ExitEvaluationContext
) -> ExitRuleMatch:
  target = _threshold(rule, "target_profit_pct")
  gross = (
    (context.current_price / plan.entry_avg_price - 1.0) * 100.0
    if plan.entry_avg_price > 0
    else 0.0
  )
  return ExitRuleMatch(
    gross >= target,
    _reason(rule, "GROSS_TAKE_PROFIT_REACHED"),
    {"gross_profit_pct": gross, "target_profit_pct": target},
  )


def _net_take_profit(
  rule: ExitRuleSpec, plan: ExitPlan, context: ExitEvaluationContext
) -> ExitRuleMatch:
  target = _threshold(rule, "target_profit_pct")
  return ExitRuleMatch(
    plan.last_net_profit_pct >= target,
    _reason(rule, "NET_TAKE_PROFIT_REACHED"),
    {"target_profit_pct": target},
  )


def _trailing_net_profit(
  rule: ExitRuleSpec, plan: ExitPlan, context: ExitEvaluationContext
) -> ExitRuleMatch:
  high_profit_lock_enabled = bool(
    rule.parameters.get("high_profit_lock_enabled", False)
  )
  policy = TrailingProfitPolicy(
    target_profit_pct=_threshold(rule, "target_profit_pct", 2.0),
    base_floor_pct=_threshold(rule, "base_floor_pct", 0.5),
    initial_gap_pct=_threshold(rule, "initial_gap_pct", 1.5),
    gap_slope=_threshold(rule, "gap_slope", 0.25),
    max_gap_pct=_threshold(rule, "max_gap_pct", 3.0),
    high_profit_arm_pct=(
      _threshold(rule, "high_profit_arm_pct", 4.0)
      if high_profit_lock_enabled
      else None
    ),
    high_profit_max_drawdown_pct=(
      _threshold(rule, "high_profit_max_drawdown_pct", 1.2)
      if high_profit_lock_enabled
      else None
    ),
  )
  state = plan.rule_state.setdefault(rule.rule_id, {})
  floor = calculate_trailing_floor_pct(
    peak_profit_pct=plan.peak_net_profit_pct,
    previous_floor_pct=_optional_float(state.get("trailing_floor_pct")),
    policy=policy,
  )
  state["trailing_floor_pct"] = floor
  observed_floors = [
    candidate
    for candidate in (
      _optional_float(item.get("trailing_floor_pct"))
      for item in plan.rule_state.values()
    )
    if candidate is not None
  ]
  plan.trailing_floor_pct = max(observed_floors) if observed_floors else None
  triggered = floor is not None and plan.last_net_profit_pct <= floor
  return ExitRuleMatch(
    triggered,
    _reason(rule, "TRAILING_FLOOR_REACHED"),
    {
      "target_profit_pct": policy.target_profit_pct,
      "trailing_floor_pct": floor,
      "high_profit_lock_enabled": high_profit_lock_enabled,
      "high_profit_arm_pct": policy.high_profit_arm_pct,
      "high_profit_max_drawdown_pct": policy.high_profit_max_drawdown_pct,
    },
  )


def _adaptive_volume_price_trailing(
  rule: ExitRuleSpec, plan: ExitPlan, context: ExitEvaluationContext
) -> ExitRuleMatch:
  """Follow strength after arming and exit a fixed protected lot on weakness.

  The rule consumes only causal observations.  Its compact rolling window is
  persisted in ``ExitPlan.rule_state`` so live recovery and tick backtests use
  the same state machine.
  """

  state = plan.rule_state.setdefault(rule.rule_id, {})
  executable_price = float(context.bid_price or context.current_price or 0.0)
  timestamp_ms = context.timestamp_ms
  trade_date = context.trade_date
  if state.get("sample_trade_date") != trade_date:
    state["samples"] = []
    state["sample_trade_date"] = trade_date
    state["consecutive_weak"] = 0

  samples = [
    dict(item)
    for item in list(state.get("samples") or [])
    if isinstance(item, Mapping)
  ]
  last_sample = samples[-1] if samples else None
  is_new_observation = not last_sample or int(last_sample.get("timestamp_ms", 0)) < (
    timestamp_ms
  )
  volume = _optional_float(context.cumulative_volume)
  if (
    is_new_observation
    and last_sample
    and volume is not None
    and _optional_float(last_sample.get("volume")) is not None
    and volume < float(last_sample["volume"])
  ):
    # A cumulative counter reset must not be interpreted as negative flow.
    samples = []
    last_sample = None
  if is_new_observation:
    samples.append(
      {
        "timestamp_ms": timestamp_ms,
        "price": executable_price,
        "volume": volume,
      }
    )
    cutoff_ms = timestamp_ms - 420_000
    samples = [
      item for item in samples if int(item.get("timestamp_ms", 0)) >= cutoff_ms
    ]
    state["samples"] = samples
    state["last_observation_timestamp_ms"] = timestamp_ms

  previous_peak = float(state.get("observed_peak_price", 0.0) or 0.0)
  if executable_price > previous_peak + 1e-9:
    state["observed_peak_price"] = executable_price
    state["peak_timestamp_ms"] = timestamp_ms
    previous_peak = executable_price
  observed_peak = max(previous_peak, executable_price)
  peak_timestamp_ms = int(state.get("peak_timestamp_ms", timestamp_ms) or timestamp_ms)
  peak_age_seconds = max(0.0, (timestamp_ms - peak_timestamp_ms) / 1000.0)
  peak_drawdown_pct = (
    (observed_peak - executable_price) / observed_peak * 100.0
    if observed_peak > 0 and executable_price > 0
    else 0.0
  )
  return_15s_pct = _window_return_pct(samples, timestamp_ms, 15)
  return_60s_pct = _window_return_pct(samples, timestamp_ms, 60)
  volume_velocity = _volume_velocity(samples, timestamp_ms)

  target_profit_pct = _optional_float(
    rule.parameters.get("arm_target_profit_pct")
  )
  target_price = _optional_float(rule.parameters.get("arm_target_price"))
  if target_profit_pct is None and target_price is None:
    target_profit_pct = 2.0
  gross_profit_pct = (
    (executable_price / plan.entry_avg_price - 1.0) * 100.0
    if executable_price > 0 and plan.entry_avg_price > 0
    else 0.0
  )
  armed = bool(state.get("armed", False))
  if not armed:
    armed = bool(
      (target_profit_pct is not None and gross_profit_pct >= target_profit_pct)
      or (target_price is not None and executable_price >= target_price > 0)
    )
    if armed:
      state["armed"] = True
      state["armed_at_ms"] = timestamp_ms
      state["armed_price"] = executable_price

  market_stale = float(context.market_data_age_seconds or 0.0) > 5.0
  volume_stale = float(context.volume_data_age_seconds or 0.0) > 5.0
  price_available = executable_price > 0 and not market_stale
  volume_available = volume is not None and not volume_stale
  data_quality = (
    "PRICE_UNAVAILABLE"
    if not price_available
    else "FULL"
    if volume_available and volume_velocity is not None
    else "PRICE_ONLY_DEGRADED"
  )
  state["data_quality"] = data_quality
  state["peak_drawdown_pct"] = peak_drawdown_pct
  state["return_15s_pct"] = return_15s_pct
  state["return_60s_pct"] = return_60s_pct
  state["volume_velocity"] = volume_velocity
  state["peak_age_seconds"] = peak_age_seconds

  if not armed:
    state["phase"] = "WAITING_ARM"
    state["last_decision"] = "WAITING_ARM"
    state["weak_score"] = 0
    return ExitRuleMatch(
      False,
      "ADAPTIVE_TRAILING_WAITING_ARM",
      _adaptive_metrics(state, gross_profit_pct),
    )
  if not price_available:
    state["phase"] = "PAUSED_STALE_PRICE"
    state["last_decision"] = "PAUSE"
    state["weak_score"] = 0
    return ExitRuleMatch(
      False,
      "ADAPTIVE_TRAILING_PRICE_UNAVAILABLE",
      _adaptive_metrics(state, gross_profit_pct),
    )

  trailing_policy = TrailingProfitPolicy(
    target_profit_pct=float(target_profit_pct or 0.0),
    base_floor_pct=_threshold(rule, "base_floor_pct", 0.5),
    initial_gap_pct=_threshold(rule, "initial_gap_pct", 1.5),
    gap_slope=_threshold(rule, "gap_slope", 0.25),
    max_gap_pct=_threshold(rule, "max_gap_pct", 3.0),
  )
  trailing_floor = calculate_trailing_floor_pct(
    peak_profit_pct=plan.peak_net_profit_pct,
    previous_floor_pct=_optional_float(state.get("trailing_floor_pct")),
    policy=trailing_policy,
  )
  state["trailing_floor_pct"] = trailing_floor
  observed_floors = [
    candidate
    for candidate in (
      _optional_float(item.get("trailing_floor_pct"))
      for item in plan.rule_state.values()
    )
    if candidate is not None
  ]
  plan.trailing_floor_pct = max(observed_floors) if observed_floors else None

  weak_score = 0
  if peak_drawdown_pct >= _threshold(rule, "weak_drawdown_pct", 0.6):
    weak_score += 2
  if return_15s_pct is not None and return_15s_pct <= -_threshold(
    rule, "weak_return_15s_pct", 0.25
  ):
    weak_score += 1
  if (
    data_quality == "FULL"
    and volume_velocity is not None
    and volume_velocity >= _threshold(rule, "stagnation_volume_velocity", 1.5)
    and return_60s_pct is not None
    and return_60s_pct <= _threshold(rule, "stagnation_return_60s_pct", 0.1)
  ):
    weak_score += 1
  depth_imbalance = _optional_float(context.depth_imbalance_5)
  state["depth_imbalance_5"] = depth_imbalance
  if (
    data_quality == "FULL"
    and depth_imbalance is not None
    and depth_imbalance <= _threshold(rule, "weak_depth_imbalance", -0.2)
  ):
    weak_score += 1
  if peak_age_seconds <= _threshold(rule, "new_high_bonus_seconds", 10.0):
    weak_score -= 1
  if (
    data_quality == "FULL"
    and return_15s_pct is not None
    and return_15s_pct >= _threshold(rule, "strong_return_15s_pct", 0.25)
    and volume_velocity is not None
    and volume_velocity >= _threshold(rule, "strong_volume_velocity", 1.2)
  ):
    weak_score -= 1
  state["weak_score"] = weak_score

  floor_breached = bool(
    trailing_floor is not None and plan.last_net_profit_pct <= trailing_floor
  )
  rapid_price_reversal = bool(
    return_15s_pct is not None
    and return_15s_pct <= -_threshold(rule, "immediate_return_15s_pct", 0.8)
    and volume_velocity is not None
    and volume_velocity >= _threshold(rule, "immediate_volume_velocity", 2.0)
  )
  immediate = bool(
    peak_drawdown_pct >= _threshold(rule, "immediate_drawdown_pct", 1.2)
    or rapid_price_reversal
    or floor_breached
  )
  if immediate:
    state["phase"] = "TRIGGERED"
    state["last_decision"] = "IMMEDIATE_EXIT"
    state["consecutive_weak"] = 0
    reason = (
      "ADAPTIVE_TRAILING_FLOOR_BREACHED"
      if floor_breached
      else "ADAPTIVE_TRAILING_IMMEDIATE_REVERSAL"
    )
    return ExitRuleMatch(
      True,
      _reason(rule, reason),
      _adaptive_metrics(state, gross_profit_pct),
    )

  confirm_score = int(rule.parameters.get("confirm_score", 3) or 3)
  confirm_observations = max(
    1, int(rule.parameters.get("confirm_observations", 2) or 2)
  )
  consecutive = int(state.get("consecutive_weak", 0) or 0)
  if is_new_observation:
    consecutive = consecutive + 1 if weak_score >= confirm_score else 0
    state["consecutive_weak"] = consecutive
  if consecutive >= confirm_observations:
    state["phase"] = "TRIGGERED"
    state["last_decision"] = "CONFIRMED_EXIT"
    return ExitRuleMatch(
      True,
      _reason(rule, "ADAPTIVE_TRAILING_WEAKNESS_CONFIRMED"),
      _adaptive_metrics(state, gross_profit_pct),
    )

  state["phase"] = (
    "FOLLOWING" if data_quality == "FULL" else "PRICE_ONLY_DEGRADED"
  )
  state["last_decision"] = "FOLLOW"
  return ExitRuleMatch(
    False,
    "ADAPTIVE_TRAILING_FOLLOW",
    _adaptive_metrics(state, gross_profit_pct),
  )


def _adaptive_metrics(state: Mapping[str, Any], gross_profit_pct: float) -> Dict[str, Any]:
  return {
    "phase": str(state.get("phase", "WAITING_ARM") or "WAITING_ARM"),
    "data_quality": str(state.get("data_quality", "PRICE_UNAVAILABLE") or ""),
    "last_decision": str(state.get("last_decision", "") or ""),
    "gross_profit_pct": float(gross_profit_pct),
    "peak_drawdown_pct": float(state.get("peak_drawdown_pct", 0.0) or 0.0),
    "return_15s_pct": _optional_float(state.get("return_15s_pct")),
    "return_60s_pct": _optional_float(state.get("return_60s_pct")),
    "volume_velocity": _optional_float(state.get("volume_velocity")),
    "depth_imbalance_5": _optional_float(state.get("depth_imbalance_5")),
    "weak_score": int(state.get("weak_score", 0) or 0),
    "consecutive_weak": int(state.get("consecutive_weak", 0) or 0),
    "trailing_floor_pct": _optional_float(state.get("trailing_floor_pct")),
  }


def _sample_at_or_before(
  samples: Iterable[Mapping[str, Any]], target_ms: int
) -> Optional[Mapping[str, Any]]:
  selected: Optional[Mapping[str, Any]] = None
  for sample in samples:
    if int(sample.get("timestamp_ms", 0) or 0) <= target_ms:
      selected = sample
    else:
      break
  return selected


def _window_return_pct(
  samples: list[Mapping[str, Any]], timestamp_ms: int, seconds: int
) -> Optional[float]:
  if not samples:
    return None
  target_ms = timestamp_ms - max(1, seconds) * 1000
  baseline = _sample_at_or_before(samples, target_ms)
  latest_price = _optional_float(samples[-1].get("price"))
  baseline_price = _optional_float(baseline.get("price")) if baseline else None
  if latest_price is None or baseline_price is None or baseline_price <= 0:
    return None
  coverage_ms = timestamp_ms - int(baseline.get("timestamp_ms", 0) or 0)
  if coverage_ms < seconds * 800:
    return None
  return (latest_price / baseline_price - 1.0) * 100.0


def _volume_velocity(
  samples: list[Mapping[str, Any]], timestamp_ms: int
) -> Optional[float]:
  if not samples:
    return None
  current = samples[-1]
  recent_start = _sample_at_or_before(samples, timestamp_ms - 60_000)
  baseline_start = _sample_at_or_before(samples, timestamp_ms - 360_000)
  if recent_start is None or baseline_start is None:
    return None
  current_volume = _optional_float(current.get("volume"))
  recent_volume = _optional_float(recent_start.get("volume"))
  baseline_volume = _optional_float(baseline_start.get("volume"))
  if current_volume is None or recent_volume is None or baseline_volume is None:
    return None
  baseline_seconds = (
    int(recent_start.get("timestamp_ms", 0) or 0)
    - int(baseline_start.get("timestamp_ms", 0) or 0)
  ) / 1000.0
  if baseline_seconds < 240.0:
    return None
  recent_delta = max(0.0, current_volume - recent_volume)
  baseline_delta = max(0.0, recent_volume - baseline_volume)
  baseline_per_60 = baseline_delta / baseline_seconds * 60.0
  if baseline_per_60 <= 0:
    return None
  return recent_delta / baseline_per_60


def _rapid_profit_reversal(
  rule: ExitRuleSpec, plan: ExitPlan, context: ExitEvaluationContext
) -> ExitRuleMatch:
  """Confirm a sharp executable-profit collapse shortly after a new peak."""

  arm_profit_pct = _threshold(rule, "arm_profit_pct", 4.0)
  window_seconds = max(1, int(rule.parameters.get("window_seconds", 15) or 15))
  drawdown_pct = _threshold(rule, "drawdown_pct", 0.8)
  confirm_ticks = max(1, int(rule.parameters.get("confirm_ticks", 2) or 2))
  state = plan.rule_state.setdefault(rule.rule_id, {})
  observed_peak = float(state.get("observed_peak_net_profit_pct", -1e9) or -1e9)
  if plan.peak_net_profit_pct > observed_peak + 1e-9:
    state["observed_peak_net_profit_pct"] = plan.peak_net_profit_pct
    state["peak_timestamp_ms"] = context.timestamp_ms
    state["consecutive_matches"] = 0

  peak_timestamp_ms = int(state.get("peak_timestamp_ms", 0) or 0)
  peak_age_ms = max(0, context.timestamp_ms - peak_timestamp_ms)
  profit_drawdown_pct = max(
    0.0,
    plan.peak_net_profit_pct - plan.last_net_profit_pct,
  )
  qualified = bool(
    context.bid_price > 0
    and plan.peak_net_profit_pct >= arm_profit_pct
    and peak_timestamp_ms > 0
    and peak_age_ms <= window_seconds * 1000
    and profit_drawdown_pct >= drawdown_pct
  )
  consecutive_matches = (
    int(state.get("consecutive_matches", 0) or 0) + 1 if qualified else 0
  )
  state["consecutive_matches"] = consecutive_matches
  triggered = qualified and consecutive_matches >= confirm_ticks
  return ExitRuleMatch(
    triggered,
    _reason(rule, "RAPID_PROFIT_REVERSAL"),
    {
      "arm_profit_pct": arm_profit_pct,
      "window_seconds": window_seconds,
      "drawdown_pct": drawdown_pct,
      "confirm_ticks": confirm_ticks,
      "consecutive_matches": consecutive_matches,
      "peak_age_ms": peak_age_ms,
      "profit_drawdown_pct": profit_drawdown_pct,
      "executable_bid": context.bid_price,
    },
  )


def _trailing_price_drawdown(
  rule: ExitRuleSpec, plan: ExitPlan, context: ExitEvaluationContext
) -> ExitRuleMatch:
  min_holding_days = max(
    1, int(rule.parameters.get("min_holding_trading_days", 1) or 1)
  )
  arm_profit = _threshold(rule, "arm_profit_pct", 0.0)
  drawdown = _threshold(rule, "drawdown_pct", 3.0)
  gross = (
    (context.current_price / plan.entry_avg_price - 1.0) * 100.0
    if plan.entry_avg_price > 0
    else 0.0
  )
  peak_drawdown = (
    (plan.peak_price - context.current_price) / plan.peak_price * 100.0
    if plan.peak_price > 0
    else 0.0
  )
  peak_gross = (
    (plan.peak_price / plan.entry_avg_price - 1.0) * 100.0
    if plan.entry_avg_price > 0
    else 0.0
  )
  return ExitRuleMatch(
    plan.holding_trading_days >= min_holding_days
    and peak_gross >= arm_profit
    and peak_drawdown >= drawdown,
    _reason(rule, "TRAILING_PRICE_DRAWDOWN_REACHED"),
    {
      "gross_profit_pct": gross,
      "peak_gross_profit_pct": peak_gross,
      "arm_profit_pct": arm_profit,
      "peak_drawdown_pct": peak_drawdown,
      "drawdown_pct": drawdown,
      "min_holding_trading_days": min_holding_days,
    },
  )


def _hard_stop(
  rule: ExitRuleSpec, plan: ExitPlan, context: ExitEvaluationContext
) -> ExitRuleMatch:
  stop = _threshold(rule, "stop_loss_pct", -0.8)
  min_holding_days = max(
    1, int(rule.parameters.get("min_holding_trading_days", 1) or 1)
  )
  return ExitRuleMatch(
    plan.holding_trading_days >= min_holding_days
    and plan.last_net_profit_pct <= stop,
    _reason(rule, "HARD_STOP"),
    {
      "stop_loss_pct": stop,
      "min_holding_trading_days": min_holding_days,
    },
  )


def _parse_time(value: Any, default: time = time(14, 50)) -> time:
  try:
    return time.fromisoformat(str(value or ""))
  except ValueError:
    return default


def _time_of_day(
  rule: ExitRuleSpec, plan: ExitPlan, context: ExitEvaluationContext
) -> ExitRuleMatch:
  exit_time = _parse_time(rule.parameters.get("exit_time", "14:50"))
  return ExitRuleMatch(
    context.timestamp.time() >= exit_time,
    _reason(rule, "TIME_OF_DAY_REACHED"),
    {"exit_time": exit_time.isoformat(timespec="minutes")},
  )


def _max_holding_days(
  rule: ExitRuleSpec, plan: ExitPlan, context: ExitEvaluationContext
) -> ExitRuleMatch:
  days = max(1, int(rule.parameters.get("max_holding_trading_days", 1) or 1))
  exit_time = _parse_time(rule.parameters.get("exit_time", "14:50"))
  return ExitRuleMatch(
    plan.holding_trading_days >= days and context.timestamp.time() >= exit_time,
    _reason(rule, "MAX_HOLDING_DAYS_REACHED"),
    {
      "max_holding_trading_days": days,
      "exit_time": exit_time.isoformat(timespec="minutes"),
    },
  )


def _limit_up_touch(
  rule: ExitRuleSpec, plan: ExitPlan, context: ExitEvaluationContext
) -> ExitRuleMatch:
  """Exit when the best executable bid reaches the configured upper limit."""

  limit_up = float(context.limit_up or 0.0)
  price_tick = max(float(context.price_tick or 0.01), 1e-8)
  tolerance_ticks = max(0, int(rule.parameters.get("tolerance_ticks", 0) or 0))
  min_holding_days = max(
    1, int(rule.parameters.get("min_holding_trading_days", 1) or 1)
  )
  trigger_price = limit_up - tolerance_ticks * price_tick
  executable_bid = float(context.bid_price or 0.0)
  triggered = bool(
    limit_up > 0
    and plan.holding_trading_days >= min_holding_days
    and executable_bid > 0
    and executable_bid >= trigger_price - 1e-8
  )
  return ExitRuleMatch(
    triggered,
    _reason(rule, "LIMIT_UP_TOUCH"),
    {
      "limit_up": limit_up,
      "price_tick": price_tick,
      "tolerance_ticks": tolerance_ticks,
      "trigger_price": trigger_price,
      "executable_bid": executable_bid,
      "min_holding_trading_days": min_holding_days,
    },
  )


def _limit_up_break(
  rule: ExitRuleSpec, plan: ExitPlan, context: ExitEvaluationContext
) -> ExitRuleMatch:
  """Trigger after a confirmed limit-up seal opens on the same trade date."""

  state = plan.rule_state.setdefault(rule.rule_id, {})
  trade_date = context.trade_date
  if state.get("trade_date") != trade_date:
    state.clear()
    state.update(
      {
        "trade_date": trade_date,
        "touch_started_at_ms": 0,
        "armed": False,
      }
    )

  limit_up = float(context.limit_up or 0.0)
  price_tick = max(float(context.price_tick or 0.01), 1e-8)
  tolerance_ticks = max(0, int(rule.parameters.get("seal_tolerance_ticks", 0) or 0))
  break_ticks = max(1, int(rule.parameters.get("break_ticks", 1) or 1))
  min_seal_seconds = max(
    0.0, float(rule.parameters.get("min_seal_seconds", 0.0) or 0.0)
  )
  min_holding_days = max(
    1, int(rule.parameters.get("min_holding_trading_days", 1) or 1)
  )

  seal_floor = limit_up - tolerance_ticks * price_tick
  at_limit = limit_up > 0 and context.current_price >= seal_floor - 1e-8
  if at_limit:
    touch_started_at_ms = int(state.get("touch_started_at_ms", 0) or 0)
    if touch_started_at_ms <= 0:
      touch_started_at_ms = context.timestamp_ms
      state["touch_started_at_ms"] = touch_started_at_ms
    if context.timestamp_ms - touch_started_at_ms >= int(min_seal_seconds * 1000):
      state["armed"] = True
  elif not state.get("armed"):
    state["touch_started_at_ms"] = 0

  break_price = limit_up - break_ticks * price_tick
  triggered = bool(
    limit_up > 0
    and plan.holding_trading_days >= min_holding_days
    and state.get("armed")
    and context.current_price <= break_price + 1e-8
  )
  return ExitRuleMatch(
    triggered,
    _reason(rule, "LIMIT_UP_BREAK"),
    {
      "limit_up": limit_up,
      "price_tick": price_tick,
      "seal_floor": seal_floor,
      "break_price": break_price,
      "seal_armed": bool(state.get("armed")),
      "min_seal_seconds": min_seal_seconds,
      "min_holding_trading_days": min_holding_days,
    },
  )


def _manual_trigger(
  rule: ExitRuleSpec, plan: ExitPlan, context: ExitEvaluationContext
) -> ExitRuleMatch:
  """Trigger an operator-confirmed exit without inventing a market condition."""

  del plan, context
  return ExitRuleMatch(
    True,
    _reason(rule, "manual_liquidation_confirmed"),
    {"trigger": ExitRuleType.MANUAL_TRIGGER.value},
  )


def _optional_float(value: Any) -> Optional[float]:
  try:
    return None if value is None else float(value)
  except (TypeError, ValueError):
    return None


def _strategy_value(value: Any) -> str:
  return str(getattr(value, "value", value) or "").upper()
