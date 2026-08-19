"""Authoritative market-signal and exit policy for first-board trading.

The functions in this module are deliberately free of account, persistence,
engine, and broker dependencies.  Live/paper strategies and offline research
must call these functions instead of maintaining parallel rule sets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any, Mapping

from .exit_plan import (
  ExitExecutionPolicy,
  ExitPlanTemplate,
  ExitPriceReference,
  ExitRuleSpec,
  ExitRuleType,
  ExitSizingMode,
  ExitSizingPolicy,
  ExitT1Policy,
  TradingCostPolicy,
)
from .first_board_promotion import FIRST_BOARD_EXIT_POLICY_VERSION


@dataclass(frozen=True)
class FirstBoardMarketSnapshot:
  """Point-in-time market facts used by the first-board entry policy."""

  instrument_code: str
  timestamp: datetime
  price: float
  limit_up: float
  price_tick: float = 0.01
  open: float = 0.0
  high: float = 0.0
  low: float = 0.0
  amount: float = 0.0
  bid1_volume: int = 0
  suspended: bool = False
  is_st: bool = False
  delist_risk: bool = False
  data_quality: str = "OK"

  @property
  def distance_to_limit_ticks(self) -> float:
    if self.limit_up <= 0 or self.price <= 0 or self.price_tick <= 0:
      return 0.0
    return (self.limit_up - self.price) / self.price_tick

  @property
  def one_word_limit_up(self) -> bool:
    if self.limit_up <= 0 or self.price_tick <= 0:
      return False
    prices = (self.open, self.high, self.low)
    return all(
      value > 0 and abs(value - self.limit_up) <= self.price_tick / 2
      for value in prices
    )

  @classmethod
  def from_mapping(
    cls,
    value: Mapping[str, Any],
    *,
    instrument_code: str,
    timestamp: datetime,
  ) -> "FirstBoardMarketSnapshot":
    raw = dict(value or {})
    return cls(
      instrument_code=str(instrument_code or "").upper(),
      timestamp=timestamp,
      price=float(raw.get("price", 0.0) or 0.0),
      limit_up=float(raw.get("limit_up", 0.0) or 0.0),
      price_tick=max(float(raw.get("price_tick", 0.01) or 0.01), 1e-8),
      open=float(raw.get("open", 0.0) or 0.0),
      high=float(raw.get("high", 0.0) or 0.0),
      low=float(raw.get("low", 0.0) or 0.0),
      amount=float(raw.get("amount", 0.0) or 0.0),
      bid1_volume=int(raw.get("bid1_volume", 0) or 0),
      suspended=bool(raw.get("suspended", False)),
      is_st=bool(raw.get("is_st", False)),
      delist_risk=bool(raw.get("delist_risk", False)),
      data_quality=str(raw.get("data_quality", "OK") or "OK").upper(),
    )


@dataclass(frozen=True)
class FirstBoardEntryPolicy:
  entry_start_time: time = time(9, 30)
  entry_end_time: time = time(14, 50)
  entry_distance_ticks: int = 1
  min_bid1_volume: int = 0
  min_daily_amount: float = 0.0
  exclude_one_word_limit_up: bool = True
  require_data_quality_ok: bool = True

  @classmethod
  def from_parameters(cls, parameters: Mapping[str, Any]) -> "FirstBoardEntryPolicy":
    raw = dict(parameters or {})
    return cls(
      entry_start_time=_parse_time(raw.get("entry_start_time", "09:30")),
      entry_end_time=_parse_time(raw.get("entry_end_time", "14:50")),
      entry_distance_ticks=int(raw.get("entry_distance_ticks", 1) or 1),
      min_bid1_volume=int(raw.get("min_bid1_volume", 0) or 0),
      min_daily_amount=float(raw.get("min_daily_amount", 0.0) or 0.0),
      exclude_one_word_limit_up=bool(raw.get("exclude_one_word_limit_up", True)),
      require_data_quality_ok=bool(raw.get("require_data_quality_ok", True)),
    )


@dataclass(frozen=True)
class FirstBoardMarketSignalDecision:
  eligible: bool
  reason: str
  distance_to_limit_ticks: float
  signal_price: float
  limit_up_price: float
  metrics: Mapping[str, Any] = field(default_factory=dict)


def evaluate_first_board_market_signal(
  snapshot: FirstBoardMarketSnapshot,
  policy: FirstBoardEntryPolicy,
  *,
  promotion_eligible: bool = True,
  promotion_reason: str = "candidate_not_eligible",
) -> FirstBoardMarketSignalDecision:
  """Evaluate only market/candidate rules, never account execution gates."""

  reason = ""
  if not promotion_eligible:
    reason = str(promotion_reason or "candidate_not_eligible")
  elif snapshot.suspended:
    reason = "instrument_suspended"
  elif snapshot.is_st:
    reason = "st_stock_blocked"
  elif snapshot.delist_risk:
    reason = "delist_risk_blocked"
  elif snapshot.limit_up <= 0 or snapshot.price <= 0 or snapshot.price_tick <= 0:
    reason = "invalid_limit_quote"
  elif policy.require_data_quality_ok and snapshot.data_quality.upper() not in {
    "",
    "OK",
  }:
    reason = "data_quality_not_ok"
  elif snapshot.timestamp.time() < policy.entry_start_time:
    reason = "before_entry_window"
  elif snapshot.timestamp.time() > policy.entry_end_time:
    reason = "after_entry_window"
  elif policy.exclude_one_word_limit_up and snapshot.one_word_limit_up:
    reason = "one_word_limit_up_blocked"
  elif snapshot.distance_to_limit_ticks < -1e-6:
    reason = "price_above_limit_up"
  elif snapshot.distance_to_limit_ticks <= 1e-6:
    reason = "limit_up_already_sealed"
  elif snapshot.distance_to_limit_ticks > policy.entry_distance_ticks:
    reason = "not_in_entry_band"
  elif snapshot.bid1_volume < policy.min_bid1_volume:
    reason = "insufficient_bid1_volume"
  elif snapshot.amount < policy.min_daily_amount:
    reason = "insufficient_daily_amount"
  return FirstBoardMarketSignalDecision(
    eligible=not reason,
    reason=reason or "ELIGIBLE",
    distance_to_limit_ticks=round(snapshot.distance_to_limit_ticks, 6),
    signal_price=float(snapshot.price),
    limit_up_price=float(snapshot.limit_up),
    metrics={
      "bid1_volume": int(snapshot.bid1_volume),
      "amount": float(snapshot.amount),
      "one_word_limit_up": snapshot.one_word_limit_up,
      "data_quality": snapshot.data_quality,
    },
  )


@dataclass(frozen=True)
class FirstBoardExitPolicy:
  limit_break_ticks: int = 1
  min_seal_seconds: float = 3.0
  trailing_arm_profit_pct: float = 2.0
  trailing_drawdown_pct: float = 3.0
  max_holding_trading_days: int = 2
  max_holding_exit_time: str = "14:50"
  max_slippage_bps: float = 50.0
  costs: TradingCostPolicy = field(default_factory=TradingCostPolicy)

  @classmethod
  def from_parameters(cls, parameters: Mapping[str, Any]) -> "FirstBoardExitPolicy":
    raw = dict(parameters or {})
    return cls(
      limit_break_ticks=int(raw.get("exit_limit_break_ticks", 1) or 1),
      min_seal_seconds=float(raw.get("exit_min_seal_seconds", 3) or 0.0),
      trailing_arm_profit_pct=float(raw.get("exit_trailing_arm_profit_pct", 2) or 0.0),
      trailing_drawdown_pct=float(raw.get("exit_trailing_drawdown_pct", 3) or 3.0),
      max_holding_trading_days=int(raw.get("max_holding_trading_days", 2) or 2),
      max_holding_exit_time=str(raw.get("max_holding_exit_time", "14:50") or "14:50"),
      max_slippage_bps=float(raw.get("exit_max_slippage_bps", 50) or 0.0),
      costs=TradingCostPolicy(
        commission_rate=float(raw.get("commission_rate", 0.0003) or 0.0),
        minimum_commission=float(raw.get("minimum_commission", 5.0) or 0.0),
        stamp_tax_rate=float(raw.get("stamp_tax_rate", 0.0005) or 0.0),
        transfer_fee_rate=float(raw.get("transfer_fee_rate", 0.00001) or 0.0),
      ),
    )


def build_first_board_exit_plan(
  *,
  plan_id: str,
  account_id: str,
  instrument_code: str,
  strategy_id: str,
  run_id: str,
  entry_trade_date: str,
  signal_price: float,
  entry_limit_up: float,
  promotion_model_version: str = "",
  exit_policy_version: str = FIRST_BOARD_EXIT_POLICY_VERSION,
  cvar95_loss_pct: float = 0.0,
  policy: FirstBoardExitPolicy | None = None,
  auto_exit_authorized: bool = False,
) -> ExitPlanTemplate:
  """Build the exact exit template used by the account first-board assistant."""

  resolved = policy or FirstBoardExitPolicy()
  rules = [
    ExitRuleSpec(
      strategy=ExitRuleType.LIMIT_UP_TOUCH,
      priority=1100,
      sizing=ExitSizingPolicy(mode=ExitSizingMode.ALL_REMAINING),
      parameters={
        "min_holding_trading_days": 2,
        "reason": "SECOND_BOARD_LIMIT_TOUCH",
      },
    ),
  ]
  if cvar95_loss_pct > 0:
    rules.append(
      ExitRuleSpec(
        strategy=ExitRuleType.HARD_STOP,
        priority=1050,
        sizing=ExitSizingPolicy(mode=ExitSizingMode.ALL_REMAINING),
        parameters={
          "min_holding_trading_days": 2,
          "stop_loss_pct": -float(cvar95_loss_pct),
          "reason": "FIRST_BOARD_T1_TAIL_LOSS",
        },
      )
    )
  rules.extend(
    [
      ExitRuleSpec(
        strategy=ExitRuleType.LIMIT_UP_BREAK,
        priority=1000,
        sizing=ExitSizingPolicy(mode=ExitSizingMode.ALL_REMAINING),
        parameters={
          "break_ticks": resolved.limit_break_ticks,
          "min_seal_seconds": resolved.min_seal_seconds,
          "min_holding_trading_days": 2,
          "reason": "LIMIT_UP_BREAK",
        },
      ),
      ExitRuleSpec(
        strategy=ExitRuleType.TRAILING_PRICE_DRAWDOWN,
        priority=700,
        sizing=ExitSizingPolicy(mode=ExitSizingMode.ALL_REMAINING),
        parameters={
          "arm_profit_pct": resolved.trailing_arm_profit_pct,
          "drawdown_pct": resolved.trailing_drawdown_pct,
          "min_holding_trading_days": 2,
          "reason": "FIRST_BOARD_T1_WEAKNESS_EXIT",
        },
        once=True,
      ),
      ExitRuleSpec(
        strategy=ExitRuleType.MAX_HOLDING_DAYS,
        priority=600,
        sizing=ExitSizingPolicy(mode=ExitSizingMode.ALL_REMAINING),
        parameters={
          "max_holding_trading_days": resolved.max_holding_trading_days,
          "exit_time": resolved.max_holding_exit_time,
          "reason": "BOARD_MAX_HOLDING_DAYS",
        },
      ),
    ]
  )
  return ExitPlanTemplate(
    plan_id=plan_id,
    source_type="FIRST_BOARD_PROMOTION_V2",
    source_id=plan_id,
    account_id=account_id,
    instrument_code=instrument_code,
    bucket="swing",
    strategy_id=strategy_id,
    run_id=run_id,
    config_version=2,
    rules=rules,
    t1_policy=ExitT1Policy.WAIT_UNTIL_SELLABLE,
    execution=ExitExecutionPolicy(
      price_reference=ExitPriceReference.BID,
      price_type="LIMIT",
      protected_limit=True,
      max_slippage_bps=resolved.max_slippage_bps,
      urgency="LIMIT_UP_BOARD_EXIT",
      execution_mode="AUTO",
    ),
    costs=resolved.costs,
    metadata={
      "entry_style": "LIMIT_UP_BOARD",
      "entry_trade_date": entry_trade_date,
      "signal_price": float(signal_price),
      "entry_limit_up": float(entry_limit_up),
      "promotion_model_version": promotion_model_version,
      "exit_policy_version": exit_policy_version,
      "t_plus_one_locked": True,
    },
    auto_exit_authorized=auto_exit_authorized,
  )


def _parse_time(value: Any) -> time:
  if isinstance(value, time):
    return value
  try:
    hour, minute = str(value or "").split(":", 1)
    return time(int(hour), int(minute))
  except (TypeError, ValueError) as exc:
    raise ValueError(f"invalid first-board policy time: {value}") from exc


__all__ = [
  "FirstBoardEntryPolicy",
  "FirstBoardExitPolicy",
  "FirstBoardMarketSignalDecision",
  "FirstBoardMarketSnapshot",
  "build_first_board_exit_plan",
  "evaluate_first_board_market_signal",
]
