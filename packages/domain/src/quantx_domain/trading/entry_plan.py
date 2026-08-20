"""Pure domain model for managed A-share entry plans.

The module deliberately has no repository, broker, clock, or network dependency.
Callers must provide an immutable, point-in-time :class:`EntryEvaluationContext`.
The evaluator only proposes a positive incremental BUY; legal lot sizing and the
latest account checks remain execution-layer responsibilities.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Mapping, Optional, Sequence

EPSILON = 1e-8
ENTRY_INTENT_NAMESPACE = uuid.UUID("f76c35bf-bfde-48d2-a7d6-768c17e59d76")
TERMINAL_ENTRY_PHASES = {"COMPLETED", "EXPIRED", "CANCELLED", "ERROR"}


class EntryTargetMode(str, Enum):
  TARGET_POSITION_PCT = "TARGET_POSITION_PCT"
  INCREMENTAL_AMOUNT_CNY = "INCREMENTAL_AMOUNT_CNY"
  ADDITIONAL_VOLUME = "ADDITIONAL_VOLUME"


class EntryPlanStatus(str, Enum):
  ARMED = "ARMED"
  ACCUMULATING = "ACCUMULATING"
  AWAITING_APPROVAL = "AWAITING_APPROVAL"
  ENTRY_PENDING = "ENTRY_PENDING"
  PAUSED = "PAUSED"
  DRAINING = "DRAINING"
  COMPLETED = "COMPLETED"
  EXPIRED = "EXPIRED"
  CANCELLED = "CANCELLED"
  ERROR = "ERROR"


class EntryEnvironment(str, Enum):
  PAPER = "PAPER"
  LIVE = "LIVE"


class EntryAuthorizationMode(str, Enum):
  MANUAL_CONFIRM = "MANUAL_CONFIRM"
  AUTO = "AUTO"


class EntryRuleType:
  """Built-in public rule names; the registry remains open to extensions."""

  TREND_PULLBACK_CONFIRMATION = "TREND_PULLBACK_CONFIRMATION"
  PRICE_LADDER = "PRICE_LADDER"
  MANUAL_TRIGGER = "MANUAL_TRIGGER"


@dataclass(frozen=True)
class EntryBaselineSnapshot:
  position_volume: int
  market_value_cny: float
  total_asset_cny: float
  reference_price: float
  account_snapshot_version: str

  def __post_init__(self) -> None:
    if self.position_volume < 0:
      raise ValueError("baseline position_volume must be non-negative")
    _require_non_negative("baseline market_value_cny", self.market_value_cny)
    _require_positive("baseline total_asset_cny", self.total_asset_cny)
    _require_positive("baseline reference_price", self.reference_price)
    if not self.account_snapshot_version:
      raise ValueError("baseline account_snapshot_version is required")

  @classmethod
  def from_dict(cls, raw: Mapping[str, Any]) -> "EntryBaselineSnapshot":
    return cls(
      position_volume=int(raw.get("position_volume", 0) or 0),
      market_value_cny=_float(raw.get("market_value_cny")),
      total_asset_cny=_float(raw.get("total_asset_cny")),
      reference_price=_float(raw.get("reference_price")),
      account_snapshot_version=str(raw.get("account_snapshot_version", "") or ""),
    )


@dataclass(frozen=True)
class EntryTargetPolicy:
  mode: EntryTargetMode
  max_total_amount_cny: float
  max_position_pct: float
  baseline_snapshot: EntryBaselineSnapshot
  target_position_pct: Optional[float] = None
  incremental_amount_cny: Optional[float] = None
  additional_volume: Optional[int] = None

  def __post_init__(self) -> None:
    object.__setattr__(self, "mode", EntryTargetMode(self.mode))
    active = {
      EntryTargetMode.TARGET_POSITION_PCT: self.target_position_pct,
      EntryTargetMode.INCREMENTAL_AMOUNT_CNY: self.incremental_amount_cny,
      EntryTargetMode.ADDITIONAL_VOLUME: self.additional_volume,
    }
    if sum(value is not None for value in active.values()) != 1:
      raise ValueError("exactly one entry target field must be set")
    if active[self.mode] is None:
      raise ValueError(f"target field does not match mode {self.mode.value}")
    _require_positive("max_total_amount_cny", self.max_total_amount_cny)
    if not 0 < self.max_position_pct <= 1:
      raise ValueError("max_position_pct must be in (0, 1]")
    if self.target_position_pct is not None:
      if not 0 < self.target_position_pct <= self.max_position_pct:
        raise ValueError("target_position_pct must be in (0, max_position_pct]")
    if self.incremental_amount_cny is not None:
      _require_positive("incremental_amount_cny", self.incremental_amount_cny)
      if self.incremental_amount_cny > self.max_total_amount_cny + EPSILON:
        raise ValueError("incremental target exceeds max_total_amount_cny")
    if self.additional_volume is not None and self.additional_volume <= 0:
      raise ValueError("additional_volume must be positive")

  @classmethod
  def from_dict(cls, raw: Mapping[str, Any]) -> "EntryTargetPolicy":
    return cls(
      mode=EntryTargetMode(str(raw.get("mode", ""))),
      target_position_pct=_optional_float(raw.get("target_position_pct")),
      incremental_amount_cny=_optional_float(raw.get("incremental_amount_cny")),
      additional_volume=_optional_int(raw.get("additional_volume")),
      max_total_amount_cny=_float(raw.get("max_total_amount_cny")),
      max_position_pct=_float(raw.get("max_position_pct")),
      baseline_snapshot=EntryBaselineSnapshot.from_dict(
        _mapping(raw.get("baseline_snapshot"))
      ),
    )


@dataclass(frozen=True)
class EntryRuleSpec:
  rule_id: str
  rule_type: str
  priority: int = 0
  parameters: Mapping[str, Any] = field(default_factory=dict)
  once: bool = False
  enabled: bool = True

  def __post_init__(self) -> None:
    if not self.rule_id:
      raise ValueError("entry rule_id is required")
    if not self.rule_type:
      raise ValueError("entry rule_type is required")
    object.__setattr__(self, "rule_type", str(self.rule_type).upper())
    object.__setattr__(self, "parameters", dict(self.parameters or {}))

  @classmethod
  def from_dict(cls, raw: Mapping[str, Any]) -> "EntryRuleSpec":
    return cls(
      rule_id=str(raw.get("rule_id", "") or ""),
      rule_type=str(raw.get("rule_type", raw.get("strategy", "")) or ""),
      priority=int(raw.get("priority", 0) or 0),
      parameters=_mapping(raw.get("parameters")),
      once=bool(raw.get("once", False)),
      enabled=bool(raw.get("enabled", True)),
    )


@dataclass(frozen=True)
class EntryPacingPolicy:
  tranche_count: int
  max_single_intent_amount_cny: float
  max_daily_filled_amount_cny: float
  max_orders_per_day: int
  cash_buffer_pct: float = 0.0
  min_interval_seconds: int = 0
  max_open_orders: int = 1
  cooldown_after_reject_seconds: int = 0
  trend_adjustment_enabled: bool = True

  def __post_init__(self) -> None:
    if self.tranche_count <= 0:
      raise ValueError("tranche_count must be positive")
    _require_positive("max_single_intent_amount_cny", self.max_single_intent_amount_cny)
    _require_positive("max_daily_filled_amount_cny", self.max_daily_filled_amount_cny)
    if self.max_orders_per_day <= 0:
      raise ValueError("max_orders_per_day must be positive")
    if self.max_open_orders != 1:
      raise ValueError("managed entry plans require max_open_orders=1")
    if not math.isfinite(self.cash_buffer_pct) or not 0 <= self.cash_buffer_pct < 1:
      raise ValueError("cash_buffer_pct must be in [0, 1)")
    if self.min_interval_seconds < 0 or self.cooldown_after_reject_seconds < 0:
      raise ValueError("entry pacing intervals must be non-negative")

  @classmethod
  def from_dict(cls, raw: Mapping[str, Any]) -> "EntryPacingPolicy":
    return cls(
      tranche_count=int(raw.get("tranche_count", 1) or 1),
      max_single_intent_amount_cny=_float(raw.get("max_single_intent_amount_cny")),
      max_daily_filled_amount_cny=_float(raw.get("max_daily_filled_amount_cny")),
      max_orders_per_day=int(raw.get("max_orders_per_day", 1) or 1),
      cash_buffer_pct=_float(raw.get("cash_buffer_pct")),
      min_interval_seconds=int(raw.get("min_interval_seconds", 0) or 0),
      max_open_orders=int(raw.get("max_open_orders", 1) or 1),
      cooldown_after_reject_seconds=int(
        raw.get("cooldown_after_reject_seconds", 0) or 0
      ),
      trend_adjustment_enabled=bool(raw.get("trend_adjustment_enabled", True)),
    )


@dataclass(frozen=True)
class EntryExecutionPolicy:
  environment: EntryEnvironment
  authorization_mode: EntryAuthorizationMode
  price_reference: str = "ASK1_PROTECTED_LIMIT"
  max_slippage_bps: float = 0.0
  max_price_deviation_bps: float = 0.0
  approval_ttl_ms: int = 60_000

  def __post_init__(self) -> None:
    object.__setattr__(self, "environment", EntryEnvironment(self.environment))
    object.__setattr__(
      self, "authorization_mode", EntryAuthorizationMode(self.authorization_mode)
    )
    if self.price_reference not in {
      "ASK1_PROTECTED_LIMIT",
      "LATEST_PROTECTED_LIMIT",
    }:
      raise ValueError("managed entry plans require a protected limit reference")
    if self.max_slippage_bps < 0 or self.max_price_deviation_bps < 0:
      raise ValueError("price protection bps must be non-negative")
    if self.approval_ttl_ms <= 0:
      raise ValueError("approval_ttl_ms must be positive")

  @classmethod
  def from_dict(cls, raw: Mapping[str, Any]) -> "EntryExecutionPolicy":
    return cls(
      environment=EntryEnvironment(str(raw.get("environment", "PAPER"))),
      authorization_mode=EntryAuthorizationMode(
        str(raw.get("authorization_mode", "MANUAL_CONFIRM"))
      ),
      price_reference=str(
        raw.get("price_reference", "ASK1_PROTECTED_LIMIT") or "ASK1_PROTECTED_LIMIT"
      ),
      max_slippage_bps=_float(raw.get("max_slippage_bps")),
      max_price_deviation_bps=_float(raw.get("max_price_deviation_bps")),
      approval_ttl_ms=int(raw.get("approval_ttl_ms", 60_000) or 60_000),
    )


@dataclass(frozen=True)
class EntryCompletionPolicy:
  max_buy_price: float
  expire_at_ms: Optional[int] = None
  stop_when_target_reached: bool = True
  stop_when_budget_exhausted: bool = True
  cancel_unsubmitted_on_expiry: bool = True

  def __post_init__(self) -> None:
    _require_positive("max_buy_price", self.max_buy_price)
    if self.expire_at_ms is not None and self.expire_at_ms <= 0:
      raise ValueError("expire_at_ms must be positive")

  @classmethod
  def from_dict(cls, raw: Mapping[str, Any]) -> "EntryCompletionPolicy":
    return cls(
      max_buy_price=_float(raw.get("max_buy_price")),
      expire_at_ms=_optional_int(raw.get("expire_at_ms")),
      stop_when_target_reached=bool(raw.get("stop_when_target_reached", True)),
      stop_when_budget_exhausted=bool(raw.get("stop_when_budget_exhausted", True)),
      cancel_unsubmitted_on_expiry=bool(raw.get("cancel_unsubmitted_on_expiry", True)),
    )


@dataclass(frozen=True)
class ManagedEntryPlanConfig:
  template_version: int
  instrument_code: str
  bucket: str
  target_policy: EntryTargetPolicy
  trigger_rules: tuple[EntryRuleSpec, ...]
  pacing_policy: EntryPacingPolicy
  execution_policy: EntryExecutionPolicy
  completion_policy: EntryCompletionPolicy
  config_version: int = 1
  exit_plan_template: Optional[Mapping[str, Any]] = None

  def __post_init__(self) -> None:
    if self.template_version <= 0 or self.config_version <= 0:
      raise ValueError("entry plan versions must be positive")
    if not self.instrument_code:
      raise ValueError("entry plan instrument_code is required")
    if self.bucket not in {"core", "swing"}:
      raise ValueError("entry plan bucket must be core or swing")
    if not self.trigger_rules:
      raise ValueError("entry plan requires at least one trigger rule")
    if not any(rule.enabled for rule in self.trigger_rules):
      raise ValueError("entry plan requires at least one enabled trigger rule")
    ids = [rule.rule_id for rule in self.trigger_rules]
    if len(ids) != len(set(ids)):
      raise ValueError("entry rule_id values must be unique")
    if (
      self.bucket == "swing"
      and self.execution_policy.environment == EntryEnvironment.LIVE
      and self.execution_policy.authorization_mode == EntryAuthorizationMode.AUTO
      and not self.exit_plan_template
    ):
      raise ValueError("LIVE AUTO swing entry requires exit_plan_template")

  @classmethod
  def from_dict(cls, raw: Mapping[str, Any]) -> "ManagedEntryPlanConfig":
    return cls(
      template_version=int(raw.get("template_version", 1) or 1),
      config_version=int(raw.get("config_version", 1) or 1),
      instrument_code=str(raw.get("instrument_code", "") or ""),
      bucket=str(raw.get("bucket", "") or "").lower(),
      target_policy=EntryTargetPolicy.from_dict(_mapping(raw.get("target_policy"))),
      trigger_rules=tuple(
        EntryRuleSpec.from_dict(_mapping(item))
        for item in list(raw.get("trigger_rules") or [])
      ),
      pacing_policy=EntryPacingPolicy.from_dict(_mapping(raw.get("pacing_policy"))),
      execution_policy=EntryExecutionPolicy.from_dict(
        _mapping(raw.get("execution_policy"))
      ),
      completion_policy=EntryCompletionPolicy.from_dict(
        _mapping(raw.get("completion_policy"))
      ),
      exit_plan_template=(
        dict(_mapping(raw.get("exit_plan_template")))
        if raw.get("exit_plan_template") is not None
        else None
      ),
    )

  def to_dict(self) -> Dict[str, Any]:
    return _enum_values(asdict(self))


@dataclass(frozen=True)
class PendingBuyExposure:
  remaining_volume: int
  protected_limit_price: float
  owner_plan_id: Optional[str] = None

  @property
  def amount_cny(self) -> float:
    return max(0, self.remaining_volume) * max(0.0, self.protected_limit_price)


@dataclass(frozen=True)
class CausalPriceObservation:
  timestamp_ms: int
  price: float
  volume: Optional[float] = None


@dataclass(frozen=True)
class EntryEvaluationContext:
  plan_id: str
  decision_time_ms: int
  trade_date: str
  instrument_code: str
  executable_price: float
  total_equity_cny: float
  current_position_volume: int
  current_market_value_cny: float
  pending_buys: tuple[PendingBuyExposure, ...] = ()
  plan_filled_amount_cny: float = 0.0
  plan_filled_volume: int = 0
  daily_filled_amount_cny: float = 0.0
  daily_order_count: int = 0
  risk_max_buy_amount_cny: Optional[float] = None
  liquidity_cap_cny: Optional[float] = None
  data_quality: str = "OK"
  allow_buy: bool = True
  allow_bucket_buy: bool = True
  only_risk_reduction: bool = False
  kill_switch: bool = False
  conflicting_sell: bool = False
  market_ready: bool = True
  manual_trigger_rule_id: Optional[str] = None
  daily_observations: tuple[CausalPriceObservation, ...] = ()
  intraday_observations: tuple[CausalPriceObservation, ...] = ()

  @property
  def pending_buy_amount_cny(self) -> float:
    return sum(item.amount_cny for item in self.pending_buys)

  @property
  def pending_buy_volume(self) -> int:
    return sum(max(0, item.remaining_volume) for item in self.pending_buys)


@dataclass
class ManagedEntryPlanState:
  phase: EntryPlanStatus = EntryPlanStatus.ARMED
  terminal_requested: Optional[EntryPlanStatus] = None
  terminal_request_reason: str = ""
  filled_volume: int = 0
  filled_amount_cny: float = 0.0
  pending_intent_id: str = ""
  pending_stage_id: str = ""
  pending_rule_id: str = ""
  pending_rule_type: str = ""
  pending_activation_id: str = ""
  pending_requested_volume: int = 0
  pending_requested_amount_cny: float = 0.0
  pending_filled_volume: int = 0
  pending_filled_amount_cny: float = 0.0
  reserved_amount_cny: float = 0.0
  order_terminal_seen: bool = False
  terminal_expected_filled_volume: Optional[int] = None
  trade_reconciled: bool = False
  rule_state: Dict[str, Dict[str, Any]] = field(default_factory=dict)
  rule_activation_counts: Dict[str, int] = field(default_factory=dict)
  completed_rule_ids: set[str] = field(default_factory=set)
  completed_activation_ids: set[str] = field(default_factory=set)
  rule_filled_volumes: Dict[str, int] = field(default_factory=dict)
  rule_filled_amounts_cny: Dict[str, float] = field(default_factory=dict)
  daily_order_counts: Dict[str, int] = field(default_factory=dict)
  daily_filled_amounts_cny: Dict[str, float] = field(default_factory=dict)
  last_fill_at_ms: Optional[int] = None
  last_intent_at_ms: Optional[int] = None
  retry_after_ms: Optional[int] = None
  last_decision: Dict[str, Any] = field(default_factory=dict)
  data_quality: str = "OK"
  processed_trade_keys: list[str] = field(default_factory=list)

  def __post_init__(self) -> None:
    self.phase = EntryPlanStatus(self.phase)
    if self.terminal_requested in {None, ""}:
      self.terminal_requested = None
    else:
      requested = EntryPlanStatus(self.terminal_requested)
      if requested not in {EntryPlanStatus.CANCELLED, EntryPlanStatus.EXPIRED}:
        raise ValueError("terminal_requested must be CANCELLED or EXPIRED")
      self.terminal_requested = requested
    if self.terminal_expected_filled_volume is not None:
      self.terminal_expected_filled_volume = int(
        self.terminal_expected_filled_volume
      )
      if self.terminal_expected_filled_volume < 0:
        raise ValueError("terminal_expected_filled_volume must be non-negative")
    self.completed_rule_ids = set(self.completed_rule_ids or set())
    self.completed_activation_ids = set(self.completed_activation_ids or set())

  @classmethod
  def from_dict(cls, raw: Optional[Mapping[str, Any]]) -> "ManagedEntryPlanState":
    values = dict(raw or {})
    allowed = cls.__dataclass_fields__.keys()
    return cls(**{key: values[key] for key in allowed if key in values})

  def to_dict(self) -> Dict[str, Any]:
    raw = asdict(self)
    raw["phase"] = self.phase.value
    raw["terminal_requested"] = (
      self.terminal_requested.value if self.terminal_requested is not None else None
    )
    raw["completed_rule_ids"] = sorted(self.completed_rule_ids)
    raw["completed_activation_ids"] = sorted(self.completed_activation_ids)
    return raw

  @property
  def has_pending(self) -> bool:
    return bool(self.pending_intent_id or self.pending_stage_id)

  def request_terminal(
    self,
    status: EntryPlanStatus | str,
    *,
    reason: str = "",
    pending_work: bool = False,
  ) -> None:
    """Persist a terminal intent while authoritative order facts drain."""

    requested = EntryPlanStatus(status)
    if requested not in {EntryPlanStatus.CANCELLED, EntryPlanStatus.EXPIRED}:
      raise ValueError("terminal request must be CANCELLED or EXPIRED")
    if self.phase == EntryPlanStatus.COMPLETED:
      return
    if self.terminal_requested is None:
      self.terminal_requested = requested
      self.terminal_request_reason = str(reason or "")
    elif self.terminal_requested != requested:
      # The first persisted terminal request is the causal product decision;
      # a later expiry/cancel race must not rewrite its meaning.
      return
    elif not self.terminal_request_reason and reason:
      self.terminal_request_reason = str(reason)
    self.phase = (
      EntryPlanStatus.DRAINING
      if self.has_pending or pending_work
      else self.terminal_requested
    )

  def cumulative_target_reached(self, config: ManagedEntryPlanConfig) -> bool:
    """Return only completion facts derivable from durable plan fills."""

    if self.phase == EntryPlanStatus.COMPLETED:
      return True
    policy = config.target_policy
    if (
      config.completion_policy.stop_when_budget_exhausted
      and self.filled_amount_cny + EPSILON >= policy.max_total_amount_cny
    ):
      return True
    if not config.completion_policy.stop_when_target_reached:
      return False
    if policy.mode == EntryTargetMode.INCREMENTAL_AMOUNT_CNY:
      return self.filled_amount_cny + EPSILON >= float(
        policy.incremental_amount_cny or 0.0
      )
    if policy.mode == EntryTargetMode.ADDITIONAL_VOLUME:
      return self.filled_volume >= int(policy.additional_volume or 0)
    # TARGET_POSITION_PCT requires the latest authoritative account/position
    # snapshot.  A trade callback cannot safely infer it from cumulative fills.
    return False

  def clear_pending(self) -> None:
    self.pending_intent_id = ""
    self.pending_stage_id = ""
    self.pending_rule_id = ""
    self.pending_rule_type = ""
    self.pending_activation_id = ""
    self.pending_requested_volume = 0
    self.pending_requested_amount_cny = 0.0
    self.pending_filled_volume = 0
    self.pending_filled_amount_cny = 0.0
    self.reserved_amount_cny = 0.0
    self.order_terminal_seen = False
    self.terminal_expected_filled_volume = None
    self.trade_reconciled = True

  def _terminal_fill_barrier_satisfied(self) -> bool:
    return bool(
      self.order_terminal_seen
      and self.terminal_expected_filled_volume is not None
      and self.pending_filled_volume >= self.terminal_expected_filled_volume
    )

  def _settle_pending(self, *, target_reached: bool) -> None:
    self.clear_pending()
    if target_reached:
      self.phase = EntryPlanStatus.COMPLETED
    elif self.terminal_requested is not None:
      self.phase = self.terminal_requested
    elif self.phase.value not in TERMINAL_ENTRY_PHASES:
      self.phase = (
        EntryPlanStatus.ACCUMULATING
        if self.last_fill_at_ms is not None
        else EntryPlanStatus.ARMED
      )

  def apply_trade_fill(
    self,
    *,
    trade_key: str,
    volume: int,
    price: float,
    trade_date: str,
    timestamp_ms: int,
    rule_id: Optional[str] = None,
    target_reached: bool = False,
  ) -> bool:
    """Apply an authoritative BUY trade once; intent/order acks must not call this."""

    if not trade_key or trade_key in self.processed_trade_keys:
      return False
    if volume <= 0 or price <= 0:
      return False
    self.processed_trade_keys = [*self.processed_trade_keys[-255:], trade_key]
    amount = volume * price
    self.filled_volume += volume
    self.filled_amount_cny += amount
    self.pending_filled_volume += volume
    self.pending_filled_amount_cny += amount
    selected_rule = str(rule_id or self.pending_rule_id or "")
    pending_rule_id = self.pending_rule_id
    pending_rule_type = self.pending_rule_type
    pending_activation_id = self.pending_activation_id
    if pending_activation_id:
      self.completed_activation_ids.add(pending_activation_id)
    if pending_activation_id and pending_activation_id == pending_rule_id:
      self.completed_rule_ids.add(pending_rule_id)
    if selected_rule:
      self.rule_filled_volumes[selected_rule] = (
        int(self.rule_filled_volumes.get(selected_rule, 0) or 0) + volume
      )
      self.rule_filled_amounts_cny[selected_rule] = (
        float(self.rule_filled_amounts_cny.get(selected_rule, 0.0) or 0.0) + amount
      )
    self.daily_filled_amounts_cny[trade_date] = (
      float(self.daily_filled_amounts_cny.get(trade_date, 0.0) or 0.0) + amount
    )
    self.last_fill_at_ms = timestamp_ms
    if pending_rule_type == EntryRuleType.TREND_PULLBACK_CONFIRMATION:
      self.rule_state.setdefault(pending_rule_id, {}).update(
        {
          "phase": "WAITING_PULLBACK",
          "observed_peak_price": price,
          "pullback_low_price": price,
          "confirmations": 0,
        }
      )
    if self._terminal_fill_barrier_satisfied() or (
      self.terminal_requested is not None and not self.has_pending
    ):
      self._settle_pending(target_reached=target_reached)
    elif self.terminal_requested is not None:
      self.phase = EntryPlanStatus.DRAINING
    elif target_reached and not self.has_pending:
      self.phase = EntryPlanStatus.COMPLETED
    else:
      self.phase = EntryPlanStatus.ACCUMULATING
    return True

  def apply_order_terminal(
    self,
    *,
    status: str,
    timestamp_ms: int,
    cooldown_after_reject_seconds: int,
    expected_filled_volume: Optional[int] = None,
    target_reached: bool = False,
  ) -> None:
    terminal = str(status or "").split(".")[-1].upper()
    if terminal not in {
      "FILLED",
      "REJECTED",
      "BROKER_REJECTED",
      "CANCELLED",
      "CANCELED",
      "EXPIRED",
      "PARTIALLY_CANCELED",
      "RECONCILED_ZERO_FILL",
    }:
      return
    self.order_terminal_seen = True
    if terminal == "RECONCILED_ZERO_FILL":
      self.terminal_expected_filled_volume = 0
      if self.pending_filled_volume <= 0:
        self.retry_after_ms = timestamp_ms + cooldown_after_reject_seconds * 1000
      self._settle_pending(target_reached=target_reached)
      return

    if expected_filled_volume is not None and int(expected_filled_volume) > 0:
      expected = int(expected_filled_volume)
      self.terminal_expected_filled_volume = max(
        int(self.terminal_expected_filled_volume or 0),
        expected,
      )
    elif terminal == "FILLED" and self.pending_requested_volume > 0:
      # FILLED means the submitted order quantity was fully executed even
      # when an early order-state projection still carries traded_volume=0.
      # The strategy adapter prefers the concrete OrderRequest volume; this
      # durable intent quantity is the fail-closed fallback for replayed or
      # synthetic events that no longer retain that request object.
      self.terminal_expected_filled_volume = max(
        int(self.terminal_expected_filled_volume or 0),
        self.pending_requested_volume,
      )
    elif terminal in {"REJECTED", "BROKER_REJECTED"}:
      # These statuses are also used before an order reaches the broker; no
      # execution report can follow such a locally rejected intent.
      self.terminal_expected_filled_volume = 0

    # A zero cumulative fill on CANCELLED/EXPIRED/PARTIALLY_CANCELED is not a
    # reconciliation barrier. Broker order-state reports can lead their
    # execution reports, so only RECONCILED_ZERO_FILL may prove that no late
    # execution exists. Keep the pending intent fail-closed meanwhile.

    if not self._terminal_fill_barrier_satisfied():
      # A terminal order report can lead its execution reports. Keep the
      # durable pending barrier until every execution announced by that
      # terminal report has arrived, or zero-fill reconciliation is explicit.
      return
    if self.pending_filled_volume <= 0 and terminal != "FILLED":
      self.retry_after_ms = timestamp_ms + cooldown_after_reject_seconds * 1000
    self._settle_pending(target_reached=target_reached)


@dataclass(frozen=True)
class EntryGapResult:
  remaining_amount_cny: float
  remaining_volume: Optional[int]
  target_reached: bool
  pending_amount_cny: float
  pending_volume: int
  position_cap_remaining_cny: float
  plan_budget_remaining_cny: float


class EntryGapCalculator:
  """Convert all product targets to one positive incremental exposure gap."""

  @staticmethod
  def calculate(
    policy: EntryTargetPolicy, context: EntryEvaluationContext
  ) -> EntryGapResult:
    price = context.executable_price
    if price <= 0 or context.total_equity_cny <= 0:
      return EntryGapResult(0.0, None, False, 0.0, 0, 0.0, 0.0)
    pending_amount = context.pending_buy_amount_cny
    pending_volume = context.pending_buy_volume
    current_value = max(
      0.0,
      context.current_market_value_cny
      if context.current_market_value_cny > 0
      else context.current_position_volume * price,
    )
    budget_remaining = max(
      0.0,
      policy.max_total_amount_cny - context.plan_filled_amount_cny - pending_amount,
    )
    position_cap_remaining = max(
      0.0,
      context.total_equity_cny * policy.max_position_pct
      - current_value
      - pending_amount,
    )

    if policy.mode == EntryTargetMode.TARGET_POSITION_PCT:
      target_value = context.total_equity_cny * float(policy.target_position_pct or 0)
      actual_gap = max(0.0, target_value - current_value)
      raw_gap = max(0.0, target_value - current_value - pending_amount)
      amount = min(raw_gap, budget_remaining, position_cap_remaining)
      return EntryGapResult(
        amount,
        None,
        actual_gap <= EPSILON,
        pending_amount,
        pending_volume,
        position_cap_remaining,
        budget_remaining,
      )

    if policy.mode == EntryTargetMode.INCREMENTAL_AMOUNT_CNY:
      actual_gap = max(
        0.0,
        float(policy.incremental_amount_cny or 0.0) - context.plan_filled_amount_cny,
      )
      raw_gap = max(
        0.0,
        actual_gap - pending_amount,
      )
      amount = min(raw_gap, budget_remaining, position_cap_remaining)
      return EntryGapResult(
        amount,
        None,
        actual_gap <= EPSILON,
        pending_amount,
        pending_volume,
        position_cap_remaining,
        budget_remaining,
      )

    actual_volume_gap = max(
      0, int(policy.additional_volume or 0) - context.plan_filled_volume
    )
    raw_volume = max(0, actual_volume_gap - pending_volume)
    amount_cap = min(budget_remaining, position_cap_remaining)
    capped_volume = min(raw_volume, max(0, math.floor(amount_cap / price)))
    return EntryGapResult(
      capped_volume * price,
      capped_volume,
      actual_volume_gap <= 0,
      pending_amount,
      pending_volume,
      position_cap_remaining,
      budget_remaining,
    )


@dataclass(frozen=True)
class EntryRuleMatch:
  matched: bool
  reason: str
  suggested_amount_cny: Optional[float] = None
  suggested_volume: Optional[int] = None
  metrics: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EntryDecision:
  plan_id: str
  config_version: int
  rule_id: str
  rule_type: str
  stage_id: str
  intent_id: str
  business_key: str
  target_amount_cny: Optional[float]
  target_volume: Optional[int]
  reason: str
  metrics: Mapping[str, Any]


@dataclass(frozen=True)
class EntryEvaluationResult:
  state: ManagedEntryPlanState
  decision: Optional[EntryDecision]
  reason: str
  gap: Optional[EntryGapResult] = None


EntryRuleEvaluator = Callable[
  [EntryRuleSpec, ManagedEntryPlanState, EntryEvaluationContext], EntryRuleMatch
]


class ManagedEntryRuleRegistry:
  def __init__(self) -> None:
    self._evaluators: Dict[str, EntryRuleEvaluator] = {}

  @classmethod
  def builtins(cls) -> "ManagedEntryRuleRegistry":
    registry = cls()
    registry.register(
      EntryRuleType.TREND_PULLBACK_CONFIRMATION, _trend_pullback_confirmation
    )
    registry.register(EntryRuleType.PRICE_LADDER, _price_ladder)
    registry.register(EntryRuleType.MANUAL_TRIGGER, _manual_trigger)
    return registry

  def register(self, rule_type: str, evaluator: EntryRuleEvaluator) -> None:
    key = str(rule_type or "").upper()
    if not key:
      raise ValueError("entry rule type is required")
    self._evaluators[key] = evaluator

  def evaluate(
    self,
    rule: EntryRuleSpec,
    state: ManagedEntryPlanState,
    context: EntryEvaluationContext,
  ) -> EntryRuleMatch:
    evaluator = self._evaluators.get(rule.rule_type)
    if evaluator is None:
      return EntryRuleMatch(False, "ENTRY_RULE_NOT_REGISTERED")
    return evaluator(rule, state, context)


class ManagedEntryPlanEvaluator:
  def __init__(self, registry: Optional[ManagedEntryRuleRegistry] = None) -> None:
    self.registry = registry or ManagedEntryRuleRegistry.builtins()

  def evaluate(
    self,
    config: ManagedEntryPlanConfig,
    state: ManagedEntryPlanState,
    context: EntryEvaluationContext,
  ) -> EntryEvaluationResult:
    state.data_quality = str(context.data_quality or "INSUFFICIENT").upper()
    block = self._block_reason(config, state, context)
    if block:
      state.last_decision = {
        "decision_time_ms": context.decision_time_ms,
        "reason": block,
      }
      return EntryEvaluationResult(state, None, block)

    gap = EntryGapCalculator.calculate(config.target_policy, context)
    if gap.target_reached and config.completion_policy.stop_when_target_reached:
      state.phase = EntryPlanStatus.COMPLETED
      return self._no_decision(state, context, "ENTRY_TARGET_REACHED", gap)
    if (
      config.target_policy.max_total_amount_cny - context.plan_filled_amount_cny
      <= EPSILON
      and config.completion_policy.stop_when_budget_exhausted
    ):
      state.phase = EntryPlanStatus.COMPLETED
      return self._no_decision(state, context, "ENTRY_BUDGET_EXHAUSTED", gap)
    if gap.remaining_amount_cny <= EPSILON:
      return self._no_decision(state, context, "ENTRY_CAPACITY_ZERO", gap)

    matches: list[tuple[EntryRuleSpec, EntryRuleMatch]] = []
    for rule in config.trigger_rules:
      if not rule.enabled or rule.rule_id in state.completed_rule_ids:
        continue
      match = self.registry.evaluate(rule, state, context)
      if match.matched:
        matches.append((rule, match))
    if not matches:
      return self._no_decision(state, context, "ENTRY_RULES_NOT_MATCHED", gap)

    rule, match = max(matches, key=lambda item: item[0].priority)
    amount_cap = self._amount_cap(config, context, gap)
    if match.suggested_amount_cny is not None:
      amount_cap = min(amount_cap, max(0.0, match.suggested_amount_cny))
    target_volume: Optional[int] = None
    target_amount: Optional[float] = None
    if config.target_policy.mode == EntryTargetMode.ADDITIONAL_VOLUME:
      requested_volume = min(
        int(gap.remaining_volume or 0),
        int(match.suggested_volume or gap.remaining_volume or 0),
      )
      target_volume = min(
        requested_volume,
        max(0, math.floor(amount_cap / context.executable_price)),
      )
      if target_volume <= 0:
        return self._no_decision(state, context, "ENTRY_CAPACITY_ZERO", gap)
      reserved_amount = target_volume * context.executable_price
    else:
      target_amount = amount_cap
      if target_amount <= EPSILON:
        return self._no_decision(state, context, "ENTRY_CAPACITY_ZERO", gap)
      reserved_amount = target_amount

    activation = int(state.rule_activation_counts.get(rule.rule_id, 0) or 0) + 1
    business_key = (
      f"{context.plan_id}:{config.config_version}:{rule.rule_id}:{activation}"
    )
    intent_id = str(uuid.uuid5(ENTRY_INTENT_NAMESPACE, business_key))
    stage_id = (
      f"entry-stage:{uuid.uuid5(ENTRY_INTENT_NAMESPACE, business_key + ':stage')}"
    )
    state.rule_activation_counts[rule.rule_id] = activation
    state.daily_order_counts[context.trade_date] = (
      int(state.daily_order_counts.get(context.trade_date, 0) or 0) + 1
    )
    state.pending_intent_id = intent_id
    state.pending_stage_id = stage_id
    state.pending_rule_id = rule.rule_id
    state.pending_rule_type = rule.rule_type
    state.pending_activation_id = str(
      match.metrics.get("activation_id")
      or (rule.rule_id if rule.once else f"{rule.rule_id}:{activation}")
    )
    state.pending_requested_volume = int(target_volume or 0)
    state.pending_requested_amount_cny = float(target_amount or reserved_amount)
    state.pending_filled_volume = 0
    state.pending_filled_amount_cny = 0.0
    state.reserved_amount_cny = reserved_amount
    state.order_terminal_seen = False
    state.terminal_expected_filled_volume = None
    state.trade_reconciled = False
    state.last_intent_at_ms = context.decision_time_ms
    state.phase = (
      EntryPlanStatus.AWAITING_APPROVAL
      if config.execution_policy.authorization_mode
      == EntryAuthorizationMode.MANUAL_CONFIRM
      else EntryPlanStatus.ENTRY_PENDING
    )
    metrics = {
      **dict(match.metrics),
      "remaining_gap_cny": gap.remaining_amount_cny,
      "position_cap_remaining_cny": gap.position_cap_remaining_cny,
      "plan_budget_remaining_cny": gap.plan_budget_remaining_cny,
      "activation": activation,
    }
    decision = EntryDecision(
      plan_id=context.plan_id,
      config_version=config.config_version,
      rule_id=rule.rule_id,
      rule_type=rule.rule_type,
      stage_id=stage_id,
      intent_id=intent_id,
      business_key=business_key,
      target_amount_cny=target_amount,
      target_volume=target_volume,
      reason=match.reason,
      metrics=metrics,
    )
    state.last_decision = {
      "decision_time_ms": context.decision_time_ms,
      "reason": match.reason,
      "rule_id": rule.rule_id,
      "intent_id": intent_id,
    }
    return EntryEvaluationResult(state, decision, match.reason, gap)

  @staticmethod
  def _block_reason(
    config: ManagedEntryPlanConfig,
    state: ManagedEntryPlanState,
    context: EntryEvaluationContext,
  ) -> str:
    if context.instrument_code != config.instrument_code:
      return "ENTRY_INSTRUMENT_MISMATCH"
    if state.phase.value in TERMINAL_ENTRY_PHASES:
      return f"ENTRY_PLAN_{state.phase.value}"
    if state.terminal_requested is not None:
      return f"ENTRY_PLAN_{state.terminal_requested.value}"
    if state.phase in {EntryPlanStatus.PAUSED, EntryPlanStatus.DRAINING}:
      return f"ENTRY_PLAN_{state.phase.value}"
    if state.has_pending:
      return "ENTRY_PENDING_EXISTS"
    if context.kill_switch:
      return "ENTRY_KILL_SWITCHED"
    if context.only_risk_reduction:
      return "ENTRY_RISK_REDUCTION_ONLY"
    if not context.allow_buy or not context.allow_bucket_buy:
      return "ENTRY_BUY_NOT_ALLOWED"
    if context.conflicting_sell:
      return "ENTRY_CONFLICTING_SELL"
    if not context.market_ready:
      return "ENTRY_MARKET_NOT_READY"
    if str(context.data_quality or "").upper() != "OK":
      return "ENTRY_DATA_INSUFFICIENT"
    if context.executable_price <= 0 or context.total_equity_cny <= 0:
      return "ENTRY_SNAPSHOT_INCOMPLETE"
    if context.executable_price > config.completion_policy.max_buy_price + EPSILON:
      return "ENTRY_MAX_BUY_PRICE_EXCEEDED"
    if (
      config.completion_policy.expire_at_ms is not None
      and context.decision_time_ms >= config.completion_policy.expire_at_ms
    ):
      state.request_terminal(
        EntryPlanStatus.EXPIRED,
        reason="ENTRY_PLAN_EXPIRED",
      )
      return "ENTRY_PLAN_EXPIRED"
    if (
      state.retry_after_ms is not None
      and context.decision_time_ms < state.retry_after_ms
    ):
      return "ENTRY_REJECT_COOLDOWN"
    last = state.last_intent_at_ms
    if last is not None and context.decision_time_ms < (
      last + config.pacing_policy.min_interval_seconds * 1000
    ):
      return "ENTRY_MIN_INTERVAL"
    orders = max(
      context.daily_order_count,
      int(state.daily_order_counts.get(context.trade_date, 0) or 0),
    )
    if orders >= config.pacing_policy.max_orders_per_day:
      return "ENTRY_DAILY_ORDER_LIMIT"
    if not _causal(context.daily_observations, context.decision_time_ms):
      return "ENTRY_FUTURE_DATA_REJECTED"
    if not _causal(context.intraday_observations, context.decision_time_ms):
      return "ENTRY_FUTURE_DATA_REJECTED"
    return ""

  @staticmethod
  def _amount_cap(
    config: ManagedEntryPlanConfig,
    context: EntryEvaluationContext,
    gap: EntryGapResult,
  ) -> float:
    daily_used = max(0.0, context.daily_filled_amount_cny)
    candidates = [
      gap.remaining_amount_cny,
      config.pacing_policy.max_single_intent_amount_cny,
      max(0.0, config.pacing_policy.max_daily_filled_amount_cny - daily_used),
    ]
    if context.risk_max_buy_amount_cny is not None:
      candidates.append(max(0.0, context.risk_max_buy_amount_cny))
    if context.liquidity_cap_cny is not None:
      candidates.append(max(0.0, context.liquidity_cap_cny))
    return max(0.0, min(candidates))

  @staticmethod
  def _no_decision(
    state: ManagedEntryPlanState,
    context: EntryEvaluationContext,
    reason: str,
    gap: Optional[EntryGapResult],
  ) -> EntryEvaluationResult:
    state.last_decision = {
      "decision_time_ms": context.decision_time_ms,
      "reason": reason,
    }
    return EntryEvaluationResult(state, None, reason, gap)


def _price_ladder(
  rule: EntryRuleSpec,
  state: ManagedEntryPlanState,
  context: EntryEvaluationContext,
) -> EntryRuleMatch:
  levels = [
    dict(item)
    for item in list(rule.parameters.get("levels") or [])
    if isinstance(item, Mapping)
  ]
  eligible: list[Mapping[str, Any]] = []
  for level in levels:
    level_id = str(level.get("level_id", "") or "")
    activation_id = f"{rule.rule_id}:{level_id}"
    if not level_id or activation_id in state.completed_activation_ids:
      continue
    trigger_price = _float(level.get("trigger_price"))
    if trigger_price > 0 and context.executable_price <= trigger_price + EPSILON:
      eligible.append(level)
  if not eligible:
    return EntryRuleMatch(False, "ENTRY_PRICE_LADDER_WAITING")
  # One tick may activate only one level. Higher priority wins, then the closest
  # trigger price, producing deterministic behavior even when price gaps down.
  level = max(
    eligible,
    key=lambda item: (
      int(item.get("priority", rule.priority) or 0),
      _float(item.get("trigger_price")),
      str(item.get("level_id", "")),
    ),
  )
  amount = _optional_float(level.get("tranche_amount_cny", level.get("tranche_value")))
  volume = _optional_int(level.get("tranche_volume"))
  state.rule_state.setdefault(rule.rule_id, {})["matched_level_id"] = str(
    level.get("level_id")
  )
  return EntryRuleMatch(
    True,
    "ENTRY_PRICE_LADDER_REACHED",
    suggested_amount_cny=amount,
    suggested_volume=volume,
    metrics={
      "level_id": str(level.get("level_id")),
      "activation_id": f"{rule.rule_id}:{level.get('level_id')}",
      "trigger_price": _float(level.get("trigger_price")),
      "executable_price": context.executable_price,
    },
  )


def _manual_trigger(
  rule: EntryRuleSpec,
  state: ManagedEntryPlanState,
  context: EntryEvaluationContext,
) -> EntryRuleMatch:
  del state
  matched = context.manual_trigger_rule_id == rule.rule_id
  return EntryRuleMatch(
    matched,
    "ENTRY_MANUAL_TRIGGER_CONFIRMED" if matched else "ENTRY_MANUAL_TRIGGER_WAITING",
    suggested_amount_cny=_optional_float(rule.parameters.get("tranche_amount_cny")),
    suggested_volume=_optional_int(rule.parameters.get("tranche_volume")),
  )


def _trend_pullback_confirmation(
  rule: EntryRuleSpec,
  state: ManagedEntryPlanState,
  context: EntryEvaluationContext,
) -> EntryRuleMatch:
  observations = context.daily_observations
  fast_period = max(2, int(rule.parameters.get("fast_ema_period", 5) or 5))
  slow_period = max(
    fast_period + 1, int(rule.parameters.get("slow_ema_period", 20) or 20)
  )
  if len(observations) < slow_period:
    return EntryRuleMatch(False, "ENTRY_TREND_WARMING_UP")
  closes = [item.price for item in observations]
  fast = _ema(closes, fast_period)
  slow = _ema(closes, slow_period)
  previous_slow = _ema(closes[:-1], slow_period) if len(closes) > slow_period else slow
  slope_pct = (slow / previous_slow - 1.0) * 100.0 if previous_slow > 0 else 0.0
  min_slope_pct = _float(rule.parameters.get("min_slow_ema_slope_pct"), 0.0)
  trend_ok = fast > slow and closes[-1] >= slow and slope_pct >= min_slope_pct
  rule_state = state.rule_state.setdefault(rule.rule_id, {})
  if not trend_ok:
    rule_state.update(
      {
        "phase": "WAITING_TREND",
        "trend_fast_ema": fast,
        "trend_slow_ema": slow,
        "slow_ema_slope_pct": slope_pct,
      }
    )
    return EntryRuleMatch(False, "ENTRY_TREND_NOT_CONFIRMED", metrics=rule_state)

  timestamp_ms = context.decision_time_ms
  last_observation_ms = int(rule_state.get("last_observation_ms", 0) or 0)
  is_new_observation = timestamp_ms > last_observation_ms
  current = context.executable_price
  peak = max(_float(rule_state.get("observed_peak_price")), current)
  phase = str(rule_state.get("phase", "WAITING_PULLBACK") or "WAITING_PULLBACK")
  if phase == "WAITING_TREND":
    phase = "WAITING_PULLBACK"
  low = _float(rule_state.get("pullback_low_price"), current)
  pullback_pct = (peak - current) / peak * 100.0 if peak > 0 else 0.0
  required_pullback = max(0.0, _float(rule.parameters.get("pullback_pct"), 1.0))
  required_rebound = max(0.0, _float(rule.parameters.get("rebound_pct"), 0.3))
  confirmations_required = max(
    1, int(rule.parameters.get("confirm_observations", 1) or 1)
  )

  if phase == "WAITING_PULLBACK" and pullback_pct >= required_pullback:
    phase = "WAITING_REBOUND"
    low = current
    rule_state["confirmations"] = 0
  elif phase == "WAITING_PULLBACK":
    peak = max(peak, current)
  if phase == "WAITING_REBOUND":
    low = min(low, current)
  rebound_pct = (current / low - 1.0) * 100.0 if low > 0 else 0.0
  confirmations = int(rule_state.get("confirmations", 0) or 0)
  if phase == "WAITING_REBOUND" and is_new_observation:
    confirmations = confirmations + 1 if rebound_pct >= required_rebound else 0

  rule_state.update(
    {
      "phase": phase,
      "last_observation_ms": timestamp_ms,
      "observed_peak_price": peak,
      "pullback_low_price": low,
      "pullback_pct": pullback_pct,
      "rebound_pct": rebound_pct,
      "confirmations": confirmations,
      "trend_fast_ema": fast,
      "trend_slow_ema": slow,
      "slow_ema_slope_pct": slope_pct,
    }
  )
  matched = phase == "WAITING_REBOUND" and confirmations >= confirmations_required
  if matched:
    rule_state["phase"] = "CONFIRMED"
  multiplier = 1.0
  if bool(rule.parameters.get("trend_adjustment_enabled", True)):
    strong_spread_pct = (fast / slow - 1.0) * 100.0 if slow > 0 else 0.0
    if strong_spread_pct >= _float(rule.parameters.get("strong_trend_spread_pct"), 1.0):
      multiplier = min(
        1.0, _float(rule.parameters.get("strong_tranche_multiplier"), 1.0)
      )
    else:
      multiplier = min(
        1.0,
        max(_float(rule.parameters.get("weak_tranche_multiplier"), 1.0), 0.0),
      )
  base_amount = _optional_float(rule.parameters.get("tranche_amount_cny"))
  return EntryRuleMatch(
    matched,
    "ENTRY_TREND_PULLBACK_CONFIRMED" if matched else "ENTRY_TREND_PULLBACK_WAITING",
    suggested_amount_cny=(base_amount * multiplier if base_amount else None),
    suggested_volume=_optional_int(rule.parameters.get("tranche_volume")),
    metrics=dict(rule_state),
  )


def _causal(
  observations: Sequence[CausalPriceObservation], decision_time_ms: int
) -> bool:
  previous = -1
  for item in observations:
    if item.timestamp_ms > decision_time_ms or item.timestamp_ms < previous:
      return False
    if item.price <= 0:
      return False
    previous = item.timestamp_ms
  return True


def _ema(values: Sequence[float], period: int) -> float:
  if not values:
    return 0.0
  alpha = 2.0 / (period + 1.0)
  result = float(values[0])
  for value in values[1:]:
    result = alpha * float(value) + (1.0 - alpha) * result
  return result


def _mapping(value: Any) -> Mapping[str, Any]:
  return value if isinstance(value, Mapping) else {}


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


def _optional_int(value: Any) -> Optional[int]:
  if value is None:
    return None
  try:
    return int(value)
  except (TypeError, ValueError):
    return None


def _require_positive(name: str, value: float) -> None:
  if not math.isfinite(value) or value <= 0:
    raise ValueError(f"{name} must be a finite positive value")


def _require_non_negative(name: str, value: float) -> None:
  if not math.isfinite(value) or value < 0:
    raise ValueError(f"{name} must be a finite non-negative value")


def _enum_values(value: Any) -> Any:
  if isinstance(value, Enum):
    return value.value
  if isinstance(value, Mapping):
    return {key: _enum_values(item) for key, item in value.items()}
  if isinstance(value, (list, tuple, set)):
    return [_enum_values(item) for item in value]
  return value
