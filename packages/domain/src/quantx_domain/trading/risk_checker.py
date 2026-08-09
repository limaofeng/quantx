"""Run-level order risk checks for real A-share trading semantics."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from quantx_domain.brokers.base import OrderRequest, OrderType

from .market_rules import AShareMarketRules, MarketDataSnapshot, OrderCheckResult


class RiskAction(str, Enum):
  ALLOW = "ALLOW"
  CAP = "CAP"
  DELAY = "DELAY"
  REJECT = "REJECT"
  KILL_SWITCH = "KILL_SWITCH"


@dataclass
class RiskContextCaps:
  """Deterministic pre-strategy risk caps for StrategyInput."""

  risk_mode: str = "NORMAL"
  kill_switch_active: bool = False
  max_position_pct: Optional[float] = None
  max_new_buy_pct_today: Optional[float] = None
  max_new_buy_amount_today: Optional[float] = None
  min_cash_buffer_pct: Optional[float] = None
  allow_buy: bool = True
  allow_sell: bool = True
  allow_intraday_swing_buy: bool = True
  only_reduce_position: bool = False
  allow_locked_core_substitution: bool = False
  t1_insufficient_action: str = RiskAction.DELAY.value
  delay_core_buy_in_panic: bool = False
  max_single_order_amount: Optional[float] = None
  reason_codes: list[str] = field(default_factory=list)
  risk_tags: list[str] = field(default_factory=list)
  metadata: Dict[str, Any] = field(default_factory=dict)

  def to_dict(self) -> Dict[str, Any]:
    """Return a stable dict shape consumed by strategies and post-order risk."""

    return {
      "risk_mode": self.risk_mode,
      "kill_switch_active": self.kill_switch_active,
      "max_position_pct": self.max_position_pct,
      "max_new_buy_pct_today": self.max_new_buy_pct_today,
      "max_new_buy_amount_today": self.max_new_buy_amount_today,
      "min_cash_buffer_pct": self.min_cash_buffer_pct,
      "allow_buy": self.allow_buy,
      "allow_sell": self.allow_sell,
      "allow_intraday_swing_buy": self.allow_intraday_swing_buy,
      "only_reduce_position": self.only_reduce_position,
      "allow_locked_core_substitution": self.allow_locked_core_substitution,
      "t1_insufficient_action": self.t1_insufficient_action,
      "delay_core_buy_in_panic": self.delay_core_buy_in_panic,
      "max_single_order_amount": self.max_single_order_amount,
      "reason_codes": sorted(set(self.reason_codes)),
      "risk_tags": sorted(set(self.risk_tags)),
      "metadata": dict(self.metadata),
    }


class ContextRiskLayer:
  """Build pre-strategy risk caps from portfolio, environment, and run params."""

  def build_caps(
    self,
    *,
    portfolio_state: Optional[Dict[str, Any]] = None,
    market_context: Optional[Dict[str, Any]] = None,
    order_state: Optional[Dict[str, Any]] = None,
    broker_report: Optional[Dict[str, Any]] = None,
    runtime_state: Optional[Dict[str, Any]] = None,
    parameters: Optional[Dict[str, Any]] = None,
    instrument_code: Optional[str] = None,
  ) -> RiskContextCaps:
    params = dict(parameters or {})
    explicit_caps = dict(params.get("risk_caps") or {})
    risk_config = dict(params.get("risk_config") or params.get("risk") or {})
    context = dict(market_context or {})
    portfolio = dict(portfolio_state or {})
    account = dict(portfolio.get("account") or {})

    total_asset = _first_optional_float(
      account.get("total_asset"),
      account.get("cash_total"),
      account.get("available_cash"),
      account.get("cash"),
    )
    risk_mode = _normalize_state(
      explicit_caps.get("risk_mode"),
      params.get("risk_mode"),
      risk_config.get("risk_mode"),
      context.get("risk_mode"),
      "NORMAL",
    )

    caps = RiskContextCaps(
      risk_mode=risk_mode,
      kill_switch_active=bool(
        explicit_caps.get(
          "kill_switch_active",
          params.get("kill_switch_active", risk_config.get("kill_switch_active", False)),
        )
      ),
      max_position_pct=_first_optional_float(
        explicit_caps.get("max_position_pct"),
        params.get("max_position_pct"),
        risk_config.get("max_position_pct"),
      ),
      max_new_buy_pct_today=_first_optional_float(
        explicit_caps.get("max_new_buy_pct_today"),
        params.get("max_new_buy_pct_today"),
        risk_config.get("max_new_buy_pct_today"),
      ),
      max_new_buy_amount_today=_first_optional_float(
        explicit_caps.get("max_new_buy_amount_today"),
        params.get("max_new_buy_amount_today"),
        risk_config.get("max_new_buy_amount_today"),
      ),
      min_cash_buffer_pct=_first_optional_float(
        explicit_caps.get("min_cash_buffer_pct"),
        params.get("min_cash_buffer_pct"),
        risk_config.get("min_cash_buffer_pct"),
      ),
      allow_buy=bool(explicit_caps.get("allow_buy", params.get("allow_buy", True))),
      allow_sell=bool(explicit_caps.get("allow_sell", params.get("allow_sell", True))),
      allow_intraday_swing_buy=bool(
        explicit_caps.get(
          "allow_intraday_swing_buy",
          params.get("allow_intraday_swing_buy", True),
        )
      ),
      only_reduce_position=bool(
        explicit_caps.get(
          "only_reduce_position",
          params.get("only_reduce_position", False),
        )
      ),
      allow_locked_core_substitution=bool(
        explicit_caps.get(
          "allow_locked_core_substitution",
          params.get("allow_locked_core_substitution", False),
        )
      ),
      t1_insufficient_action=str(
        explicit_caps.get(
          "t1_insufficient_action",
          params.get("t1_insufficient_action", RiskAction.DELAY.value),
        )
      ).upper(),
      delay_core_buy_in_panic=bool(
        explicit_caps.get(
          "delay_core_buy_in_panic",
          params.get("delay_core_buy_in_panic", False),
        )
      ),
      max_single_order_amount=_first_optional_float(
        explicit_caps.get("max_single_order_amount"),
        params.get("max_single_order_amount"),
        risk_config.get("max_single_order_amount"),
      ),
      reason_codes=list(explicit_caps.get("reason_codes", [])),
      risk_tags=list(explicit_caps.get("risk_tags", [])),
      metadata=dict(explicit_caps.get("metadata", {})),
    )

    market_state = _normalize_state(
      context.get("market_state"),
      context.get("stock_state"),
      context.get("sector_state"),
      "NORMAL",
    )
    industry_state = _normalize_state(context.get("industry_state"), "NORMAL")
    liquidity_state = _normalize_state(context.get("liquidity_state"), "NORMAL")
    data_quality = _normalize_state(context.get("data_quality"), "OK")

    if market_state == "PANIC" or caps.risk_mode == "PANIC":
      caps.risk_mode = "PANIC"
      caps.allow_intraday_swing_buy = False
      caps.max_position_pct = _min_optional(caps.max_position_pct, 0.35)
      caps.max_new_buy_pct_today = _min_optional(caps.max_new_buy_pct_today, 0.01)
      caps.min_cash_buffer_pct = _max_optional(caps.min_cash_buffer_pct, 0.40)
      caps.reason_codes.append("RISK_CONTEXT_CAP")
      caps.risk_tags.append("market_panic")
    elif market_state == "RISK_OFF" or caps.risk_mode in {
      "RISK_OFF",
      "RISK_REDUCED",
      "REDUCED",
    }:
      caps.risk_mode = "RISK_REDUCED"
      caps.allow_intraday_swing_buy = False
      caps.max_position_pct = _min_optional(caps.max_position_pct, 0.50)
      caps.max_new_buy_pct_today = _min_optional(caps.max_new_buy_pct_today, 0.04)
      caps.min_cash_buffer_pct = _max_optional(caps.min_cash_buffer_pct, 0.30)
      caps.reason_codes.append("RISK_CONTEXT_CAP")
      caps.risk_tags.append("market_risk_off")

    if industry_state == "BROKEN":
      caps.max_position_pct = _min_optional(caps.max_position_pct, 0.40)
      caps.max_new_buy_pct_today = _min_optional(caps.max_new_buy_pct_today, 0.02)
      caps.reason_codes.append("RISK_CONTEXT_CAP")
      caps.risk_tags.append("industry_broken")

    if liquidity_state == "DRY":
      if total_asset is not None:
        caps.max_single_order_amount = _min_optional(
          caps.max_single_order_amount,
          total_asset * 0.02,
        )
      caps.reason_codes.append("LOW_LIQUIDITY")
      caps.risk_tags.append("liquidity_dry")

    if data_quality in {"INSUFFICIENT", "STALE", "MISSING"} and caps.risk_mode != "PANIC":
      caps.risk_mode = "RISK_REDUCED"
      caps.max_position_pct = _min_optional(caps.max_position_pct, 0.50)
      caps.min_cash_buffer_pct = _max_optional(caps.min_cash_buffer_pct, 0.30)
      caps.reason_codes.append("RISK_CONTEXT_CAP")
      caps.risk_tags.append("data_quality_guard")

    if caps.max_new_buy_amount_today is None and total_asset is not None:
      if caps.max_new_buy_pct_today is not None:
        caps.max_new_buy_amount_today = max(
          0.0, total_asset * caps.max_new_buy_pct_today
        )

    self._apply_kill_switch_guards(
      caps,
      context=context,
      order_state=dict(order_state or {}),
      broker_report=dict(broker_report or {}),
      runtime_state=dict(runtime_state or {}),
      risk_config=risk_config,
      params=params,
      instrument_code=instrument_code,
    )

    if caps.kill_switch_active:
      caps.allow_buy = False
      caps.only_reduce_position = True
      caps.reason_codes.append("KILL_SWITCH_TRIGGERED")
      caps.risk_tags.append("kill_switch")
    if caps.only_reduce_position:
      caps.reason_codes.append("ONLY_REDUCE_POSITION")
      caps.risk_tags.append("only_reduce_position")

    caps.reason_codes = sorted(set(code.upper() for code in caps.reason_codes))
    caps.risk_tags = sorted(set(str(tag) for tag in caps.risk_tags))
    caps.metadata.update(
      {
        "instrument_code": instrument_code,
        "market_state": market_state,
        "industry_state": industry_state,
        "liquidity_state": liquidity_state,
        "data_quality": data_quality,
        "order_state": _risk_metadata_order_state(dict(order_state or {})),
        "broker_report": _risk_metadata_broker_report(dict(broker_report or {})),
      }
    )
    return caps

  def _apply_kill_switch_guards(
    self,
    caps: RiskContextCaps,
    *,
    context: Dict[str, Any],
    order_state: Dict[str, Any],
    broker_report: Dict[str, Any],
    runtime_state: Dict[str, Any],
    risk_config: Dict[str, Any],
    params: Dict[str, Any],
    instrument_code: Optional[str],
  ) -> None:
    max_drawdown_pct = _first_optional_float(
      risk_config.get("max_drawdown_pct"),
      params.get("max_drawdown_pct"),
    )
    drawdown_pct = _first_optional_float(
      runtime_state.get("drawdown_pct"),
      runtime_state.get("max_drawdown"),
      context.get("drawdown_pct"),
    )
    if (
      max_drawdown_pct is not None
      and drawdown_pct is not None
      and drawdown_pct >= max_drawdown_pct
    ):
      caps.kill_switch_active = True
      caps.metadata["kill_switch_reason"] = "max_drawdown"
      caps.metadata["drawdown_pct"] = drawdown_pct

    if (
      context.get("suspended")
      or _normalize_state(context.get("stock_state"), "") in {"ST", "DELIST_RISK"}
      or context.get("delist_risk")
    ):
      caps.kill_switch_active = True
      caps.metadata["kill_switch_reason"] = "instrument_protection"
      caps.metadata["instrument_code"] = instrument_code

    if order_state.get("severe_mismatch") or broker_report.get("severe_mismatch"):
      caps.kill_switch_active = True
      caps.metadata["kill_switch_reason"] = "account_state_mismatch"

    max_report_lag_seconds = _first_optional_float(
      risk_config.get("max_broker_report_lag_seconds"),
      params.get("max_broker_report_lag_seconds"),
    )
    broker_lag_seconds = _first_optional_float(
      broker_report.get("report_lag_seconds"),
      broker_report.get("last_report_lag_seconds"),
    )
    if (
      max_report_lag_seconds is not None
      and broker_lag_seconds is not None
      and broker_lag_seconds >= max_report_lag_seconds
    ):
      caps.kill_switch_active = True
      caps.metadata["kill_switch_reason"] = "broker_report_stale"


@dataclass
class OrderRiskDecision:
  """Structured post-order risk decision."""

  action: RiskAction
  allowed: bool
  original_volume: int
  final_volume: int
  original_amount: float
  final_amount: float
  reason_code: str = "OK"
  reason_detail: str = ""
  risk_tags: list[str] = field(default_factory=list)
  risk_decision_id: str = field(default_factory=lambda: str(uuid.uuid4()))
  metadata: Dict[str, Any] = field(default_factory=dict)
  substitution_plan: Optional[Dict[str, Any]] = None

  @classmethod
  def allow(
    cls,
    request: OrderRequest,
    *,
    reason_code: str = "OK",
    reason_detail: str = "",
    risk_tags: Optional[list[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    substitution_plan: Optional[Dict[str, Any]] = None,
  ) -> "OrderRiskDecision":
    amount = float(request.price) * int(request.volume)
    return cls(
      action=RiskAction.ALLOW,
      allowed=True,
      original_volume=int(request.volume),
      final_volume=int(request.volume),
      original_amount=amount,
      final_amount=amount,
      reason_code=reason_code.upper(),
      reason_detail=reason_detail,
      risk_tags=risk_tags or [],
      metadata=metadata or {},
      substitution_plan=substitution_plan,
    )

  @classmethod
  def reject(
    cls,
    request: OrderRequest,
    reason_code: str,
    reason_detail: str,
    *,
    action: RiskAction = RiskAction.REJECT,
    risk_tags: Optional[list[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    substitution_plan: Optional[Dict[str, Any]] = None,
  ) -> "OrderRiskDecision":
    amount = float(request.price) * int(request.volume or 0)
    return cls(
      action=action,
      allowed=False,
      original_volume=int(request.volume or 0),
      final_volume=0,
      original_amount=amount,
      final_amount=0.0,
      reason_code=reason_code.upper(),
      reason_detail=reason_detail,
      risk_tags=risk_tags or [],
      metadata=metadata or {},
      substitution_plan=substitution_plan,
    )

  @classmethod
  def delay(
    cls,
    request: OrderRequest,
    reason_code: str,
    reason_detail: str,
    *,
    risk_tags: Optional[list[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    substitution_plan: Optional[Dict[str, Any]] = None,
  ) -> "OrderRiskDecision":
    return cls.reject(
      request,
      reason_code,
      reason_detail,
      action=RiskAction.DELAY,
      risk_tags=risk_tags,
      metadata=metadata,
      substitution_plan=substitution_plan,
    )

  @classmethod
  def cap(
    cls,
    request: OrderRequest,
    final_volume: int,
    reason_code: str,
    reason_detail: str,
    *,
    risk_tags: Optional[list[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    substitution_plan: Optional[Dict[str, Any]] = None,
  ) -> "OrderRiskDecision":
    original_amount = float(request.price) * int(request.volume or 0)
    final_amount = float(request.price) * int(final_volume or 0)
    return cls(
      action=RiskAction.CAP,
      allowed=True,
      original_volume=int(request.volume or 0),
      final_volume=int(final_volume or 0),
      original_amount=original_amount,
      final_amount=final_amount,
      reason_code=reason_code.upper(),
      reason_detail=reason_detail,
      risk_tags=risk_tags or [],
      metadata=metadata or {},
      substitution_plan=substitution_plan,
    )

  def to_check_result(self) -> OrderCheckResult:
    if self.allowed:
      return OrderCheckResult.passed()
    return OrderCheckResult.failed(
      self.reason_code.lower(),
      self.reason_detail,
      {
        "risk_action": self.action.value,
        "risk_decision_id": self.risk_decision_id,
        "substitution_plan": self.substitution_plan,
        **self.metadata,
      },
    )


class TradingRiskChecker:
  def __init__(
    self,
    rules: Optional[AShareMarketRules] = None,
    trading_time_service: Optional[Any] = None,
    *,
    commission_rate: float = 0.0003,
    min_commission: float = 5.0,
    strict_market_data: bool = False,
    strict_limit_data: bool = False,
    enforce_trading_hours: bool = False,
    market: str = "SH",
  ) -> None:
    self.rules = rules or AShareMarketRules()
    self.trading_time_service = trading_time_service
    self.commission_rate = commission_rate
    self.min_commission = min_commission
    self.strict_market_data = strict_market_data
    self.strict_limit_data = strict_limit_data
    self.enforce_trading_hours = enforce_trading_hours
    self.market = market

  async def validate_order(
    self,
    request: OrderRequest,
    *,
    account: Dict[str, Any],
    position: Optional[Dict[str, Any]],
    market_data: Optional[MarketDataSnapshot],
    current_time: Optional[datetime] = None,
    risk_caps: Optional[Dict[str, Any]] = None,
  ) -> OrderCheckResult:
    decision = await self.evaluate_order(
      request,
      account=account,
      position=position,
      market_data=market_data,
      current_time=current_time,
      risk_caps=risk_caps,
    )
    return decision.to_check_result()

  async def evaluate_order(
    self,
    request: OrderRequest,
    *,
    account: Dict[str, Any],
    position: Optional[Dict[str, Any]],
    market_data: Optional[MarketDataSnapshot],
    current_time: Optional[datetime] = None,
    risk_caps: Optional[Dict[str, Any]] = None,
  ) -> OrderRiskDecision:
    caps_decision = self._check_context_caps(request, account, position, market_data, risk_caps)
    if caps_decision is not None:
      return caps_decision

    available_volume = int((position or {}).get("available_volume", 0) or 0)
    if request.order_type == OrderType.SELL:
      available_volume = self._available_volume_for_order_check(
        request, position, risk_caps
      )

    checks = [
      self.rules.check_trading_status(
        market_data, strict_market_data=self.strict_market_data
      ),
      self.rules.check_price(
        request, market_data, strict_limit_data=self.strict_limit_data
      ),
      self.rules.check_limit_block(request, market_data),
      self.rules.check_volume(
        request,
        available_volume=available_volume,
        min_volume=self._min_volume(request, market_data),
        max_volume=self._max_volume(request, market_data),
      ),
    ]
    for check in checks:
      if not check.ok:
        return OrderRiskDecision.reject(
          request,
          check.code,
          check.message,
          metadata=check.metadata,
        )

    if self.enforce_trading_hours:
      timestamp = current_time or (market_data.timestamp if market_data else None)
      if timestamp and not await self._is_trading_hours(timestamp):
        return OrderRiskDecision.reject(
          request, "outside_trading_hours", "当前不在交易时间"
        )

    if request.order_type == OrderType.BUY:
      buy_decision = self._check_buying_power_decision(request, account)
      if not buy_decision.allowed:
        return buy_decision
      cap_decision = self._cap_buy_by_context(request, account, position, market_data, risk_caps)
      return cap_decision or buy_decision
    if request.order_type == OrderType.SELL:
      return self._check_sell_capacity_decision(request, position, risk_caps)
    return OrderRiskDecision.reject(
      request, "unsupported_order_type", "仅支持普通股票多头买卖"
    )

  def _check_buying_power(
    self, request: OrderRequest, account: Dict[str, Any]
  ) -> OrderCheckResult:
    return self._check_buying_power_decision(request, account).to_check_result()

  def _check_buying_power_decision(
    self, request: OrderRequest, account: Dict[str, Any]
  ) -> OrderRiskDecision:
    amount = request.price * request.volume
    commission = self.rules.estimate_commission(
      amount, rate=self.commission_rate, minimum=self.min_commission
    )
    required = amount + commission
    available_cash = float(account.get("available_cash", account.get("cash", 0.0)) or 0.0)
    if available_cash < required:
      return OrderRiskDecision.reject(
        request,
        "insufficient_cash",
        f"可用资金不足: 需要 {required:.2f}, 可用 {available_cash:.2f}",
        metadata={"required_cash": required, "available_cash": available_cash},
      )
    return OrderRiskDecision.allow(request)

  def _check_sell_capacity(
    self, request: OrderRequest, position: Optional[Dict[str, Any]]
  ) -> OrderCheckResult:
    return self._check_sell_capacity_decision(request, position).to_check_result()

  def _check_sell_capacity_decision(
    self,
    request: OrderRequest,
    position: Optional[Dict[str, Any]],
    risk_caps: Optional[Dict[str, Any]] = None,
  ) -> OrderRiskDecision:
    if self._uses_bucket_t1_model(request, position):
      substitution_decision = self._check_t1_substitution(
        request,
        position,
        risk_caps,
      )
      if substitution_decision is not None:
        return substitution_decision

    available = int((position or {}).get("available_volume", 0) or 0)
    if available < request.volume:
      return OrderRiskDecision.reject(
        request,
        "insufficient_position",
        f"可用持仓不足: {available} < {request.volume}",
      )
    return OrderRiskDecision.allow(request)

  def _check_context_caps(
    self,
    request: OrderRequest,
    account: Dict[str, Any],
    position: Optional[Dict[str, Any]],
    market_data: Optional[MarketDataSnapshot],
    risk_caps: Optional[Dict[str, Any]],
  ) -> Optional[OrderRiskDecision]:
    caps = risk_caps or {}
    if caps.get("kill_switch_active"):
      return OrderRiskDecision.reject(
        request,
        "KILL_SWITCH_TRIGGERED",
        "实例级熔断已开启，拒绝下单",
        action=RiskAction.KILL_SWITCH,
        risk_tags=list(caps.get("risk_tags", [])),
        metadata={"reason_codes": list(caps.get("reason_codes", []))},
      )
    max_single_order_amount = _optional_float(caps.get("max_single_order_amount"))
    if max_single_order_amount is not None:
      amount = float(request.price) * int(request.volume or 0)
      if amount > max_single_order_amount:
        return OrderRiskDecision.delay(
          request,
          "LOW_LIQUIDITY",
          "订单金额超过当前流动性风控上限，延迟执行",
          risk_tags=list(caps.get("risk_tags", [])),
          metadata={"max_single_order_amount": max_single_order_amount},
        )
    if request.order_type == OrderType.BUY:
      if caps.get("allow_buy") is False:
        return OrderRiskDecision.reject(
          request,
          "RISK_CONTEXT_CAP",
          "前置风控禁止新增买入",
          risk_tags=list(caps.get("risk_tags", [])),
        )
      if caps.get("only_reduce_position"):
        return OrderRiskDecision.reject(
          request,
          "only_reduce_position",
          "前置风控只允许降低仓位",
          risk_tags=list(caps.get("risk_tags", [])),
        )
      bucket = str((request.metadata or {}).get("bucket", "") or "").lower()
      if bucket == "swing" and caps.get("allow_intraday_swing_buy") is False:
        return OrderRiskDecision.delay(
          request,
          "RISK_CONTEXT_CAP",
          "当前风险环境禁止 swing 买入",
          risk_tags=list(caps.get("risk_tags", [])),
          metadata={"bucket": bucket, "risk_mode": caps.get("risk_mode")},
        )
      if (
        bucket == "core"
        and str(caps.get("risk_mode", "")).upper() == "PANIC"
        and caps.get("delay_core_buy_in_panic")
      ):
        return OrderRiskDecision.delay(
          request,
          "RISK_CONTEXT_CAP",
          "PANIC 环境延迟 core 买入",
          risk_tags=list(caps.get("risk_tags", [])),
          metadata={"bucket": bucket, "risk_mode": caps.get("risk_mode")},
        )
    if request.order_type == OrderType.SELL and caps.get("allow_sell") is False:
      return OrderRiskDecision.reject(
        request,
        "RISK_CONTEXT_CAP",
        "前置风控禁止卖出",
        risk_tags=list(caps.get("risk_tags", [])),
      )
    return None

  def _cap_buy_by_context(
    self,
    request: OrderRequest,
    account: Dict[str, Any],
    position: Optional[Dict[str, Any]],
    market_data: Optional[MarketDataSnapshot],
    risk_caps: Optional[Dict[str, Any]],
  ) -> Optional[OrderRiskDecision]:
    caps = risk_caps or {}
    if request.order_type != OrderType.BUY or request.price <= 0:
      return None

    cap_amounts: list[tuple[str, float]] = []
    available_cash = float(account.get("available_cash", account.get("cash", 0.0)) or 0.0)
    total_asset = float(
      account.get("total_asset")
      or account.get("cash_total")
      or available_cash
      or 0.0
    )

    max_new_buy_amount_today = _optional_float(caps.get("max_new_buy_amount_today"))
    if max_new_buy_amount_today is not None:
      cap_amounts.append(("MAX_NEW_BUY_AMOUNT_TODAY", max_new_buy_amount_today))

    min_cash_buffer_pct = _optional_float(caps.get("min_cash_buffer_pct"))
    if min_cash_buffer_pct is not None and total_asset > 0:
      cap_amounts.append(
        ("MIN_CASH_BUFFER", max(0.0, available_cash - total_asset * min_cash_buffer_pct))
      )

    max_position_pct = _optional_float(caps.get("max_position_pct"))
    if max_position_pct is not None and total_asset > 0:
      price = market_data.price if market_data and market_data.price > 0 else request.price
      current_volume = int(
        (position or {}).get(
          "long_volume",
          (position or {}).get("total_volume", 0),
        )
        or 0
      )
      market_value = _optional_float((position or {}).get("market_value"))
      if market_value is None:
        market_value = current_volume * float(price)
      max_position_amount = total_asset * max_position_pct
      cap_amounts.append(("MAX_POSITION_PCT", max(0.0, max_position_amount - market_value)))

    if not cap_amounts:
      return None

    reason_code, max_amount = min(cap_amounts, key=lambda item: item[1])
    original_amount = float(request.price) * int(request.volume or 0)
    if original_amount <= max_amount:
      return None

    capped_volume = self.rules.normalize_buy_volume(int(max_amount // float(request.price)))
    min_volume = self._min_volume(request, market_data)
    if min_volume is not None and capped_volume < int(min_volume):
      capped_volume = 0
    if capped_volume <= 0:
      return OrderRiskDecision.reject(
        request,
        _public_cap_reason_code(reason_code),
        "前置风控额度不足以形成合法买入订单",
        risk_tags=list(caps.get("risk_tags", [])),
        metadata={"cap_source": reason_code},
      )
    return OrderRiskDecision.cap(
      request,
      capped_volume,
      _public_cap_reason_code(reason_code),
      "订单数量被前置风控上限压低",
      risk_tags=list(caps.get("risk_tags", [])),
      metadata={"cap_source": reason_code},
    )

  def _available_volume_for_order_check(
    self,
    request: OrderRequest,
    position: Optional[Dict[str, Any]],
    risk_caps: Optional[Dict[str, Any]],
  ) -> int:
    available = int((position or {}).get("available_volume", 0) or 0)
    if request.order_type != OrderType.SELL:
      return available
    if not self._uses_bucket_t1_model(request, position):
      return available
    bucket_capacity = self._bucket_sell_capacity(position, risk_caps)
    if not bool((request.metadata or {}).get("allow_t1_substitution", True)):
      bucket_capacity["core_available"] = 0
      bucket_capacity["locked_core_available"] = 0
      bucket_capacity["total_substitutable"] = int(
        bucket_capacity.get("swing_available", 0) or 0
      )
    total_capacity = max(available, bucket_capacity.get("total_substitutable", 0))
    total_volume = int(
      (position or {}).get("long_volume", (position or {}).get("total_volume", 0)) or 0
    )
    if total_volume > 0:
      total_capacity = min(max(total_capacity, int(request.volume or 0)), total_volume)
    return max(available, total_capacity)

  def _check_t1_substitution(
    self,
    request: OrderRequest,
    position: Optional[Dict[str, Any]],
    risk_caps: Optional[Dict[str, Any]],
  ) -> Optional[OrderRiskDecision]:
    if request.order_type != OrderType.SELL:
      return None
    if not self._uses_bucket_t1_model(request, position):
      return None

    pos = position or {}
    caps = risk_caps or {}
    bucket = str((request.metadata or {}).get("bucket", "") or "").lower()
    total_volume = int(pos.get("long_volume", pos.get("total_volume", 0)) or 0)
    if total_volume and int(request.volume or 0) > total_volume:
      return OrderRiskDecision.reject(
        request,
        "INSUFFICIENT_POSITION",
        f"总持仓不足: {total_volume} < {request.volume}",
      )

    if bucket != "swing":
      return None

    requested = int(request.volume or 0)
    capacity = self._bucket_sell_capacity(pos, caps)
    allow_substitution = bool(
      (request.metadata or {}).get("allow_t1_substitution", True)
    )
    if not allow_substitution:
      capacity["core_available"] = 0
      capacity["locked_core_available"] = 0
      capacity["total_substitutable"] = int(
        capacity.get("swing_available", 0) or 0
      )
    remaining = requested
    legs: list[Dict[str, Any]] = []
    for source in ("swing", "core", "locked_core"):
      source_available = int(capacity.get(f"{source}_available", 0) or 0)
      if source_available <= 0 or remaining <= 0:
        continue
      volume = min(remaining, source_available)
      if volume <= 0:
        continue
      legs.append({"bucket": source, "volume": volume})
      remaining -= volume

    if remaining <= 0:
      uses_substitution = any(leg["bucket"] != "swing" for leg in legs)
      if not uses_substitution:
        return OrderRiskDecision.allow(request)
      plan = {
        "enabled": True,
        "requested_bucket": "swing",
        "sell_from_buckets": legs,
        "reattribute_buy_to_bucket": "core",
        "volume": requested,
        "reason": "swing_t0_sell_with_core_inventory",
      }
      return OrderRiskDecision.allow(
        request,
        reason_code="T1_SUBSTITUTION_APPLIED",
        reason_detail="使用同标的老仓执行 T+1 库存置换",
        risk_tags=list(caps.get("risk_tags", [])) + ["t1_substitution"],
        metadata={"substitution_plan": plan},
        substitution_plan=plan,
      )

    metadata = {
      "requested_volume": requested,
      "substitutable_volume": requested - remaining,
      "missing_volume": remaining,
      "allow_locked_core_substitution": bool(
        caps.get("allow_locked_core_substitution", False)
      ),
    }
    insufficient_action = str(
      (request.metadata or {}).get(
        "t1_insufficient_action",
        caps.get("t1_insufficient_action", RiskAction.DELAY.value),
      )
    ).upper()
    if insufficient_action == "REJECT":
      return OrderRiskDecision.reject(
        request,
        "T1_UNAVAILABLE",
        "T+1 可卖量不足，且实例配置为拒绝",
        risk_tags=list(caps.get("risk_tags", [])),
        metadata=metadata,
      )
    return OrderRiskDecision.delay(
      request,
      "T1_UNAVAILABLE",
      "T+1 可卖量不足，延迟等待可卖库存或撤单回报",
      risk_tags=list(caps.get("risk_tags", [])),
      metadata=metadata,
    )

  def _uses_bucket_t1_model(
    self,
    request: OrderRequest,
    position: Optional[Dict[str, Any]],
  ) -> bool:
    metadata = request.metadata or {}
    if metadata.get("bucket"):
      return True
    pos = position or {}
    for key in (
      "swing_available",
      "swing_available_volume",
      "core_available",
      "core_available_volume",
      "locked_core_available",
      "locked_core_available_volume",
    ):
      if key in pos:
        return True
    return False

  def _bucket_sell_capacity(
    self,
    position: Optional[Dict[str, Any]],
    risk_caps: Optional[Dict[str, Any]],
  ) -> Dict[str, int]:
    pos = position or {}
    caps = risk_caps or {}
    swing_available = _optional_int(
      pos.get("swing_available_volume", pos.get("swing_available"))
    )
    core_available = _optional_int(
      pos.get("core_available_volume", pos.get("core_available"))
    )
    locked_core_available = _optional_int(
      pos.get("locked_core_available_volume", pos.get("locked_core_available"))
    )
    available = int(pos.get("available_volume", 0) or 0)

    if swing_available is None:
      swing_available = min(
        available,
        int(pos.get("swing_volume", pos.get("swing_total_volume", 0)) or 0),
      )
    if core_available is None:
      core_available = int(pos.get("core_available_volume", 0) or 0)
    if locked_core_available is None:
      locked_core_available = int(pos.get("locked_core_available_volume", 0) or 0)

    if (
      core_available <= 0
      and swing_available <= 0
      and locked_core_available <= 0
      and available > 0
    ):
      core_available = available

    if not bool(caps.get("allow_locked_core_substitution", False)):
      locked_core_available = 0

    return {
      "swing_available": max(0, int(swing_available or 0)),
      "core_available": max(0, int(core_available or 0)),
      "locked_core_available": max(0, int(locked_core_available or 0)),
      "total_substitutable": max(0, int(swing_available or 0))
      + max(0, int(core_available or 0))
      + max(0, int(locked_core_available or 0)),
    }

  def _min_volume(
    self, request: OrderRequest, market_data: Optional[MarketDataSnapshot]
  ) -> Optional[int]:
    if not market_data:
      return None
    attr = (
      "min_limit_sell_order_volume"
      if request.order_type == OrderType.SELL
      else "min_limit_order_volume"
    )
    value = getattr(market_data, attr, None)
    return int(value) if value else None

  async def _is_trading_hours(self, timestamp: datetime) -> bool:
    if self.trading_time_service is not None:
      return await self.trading_time_service.is_trading_hours(self.market, timestamp)
    if timestamp.weekday() >= 5:
      return False
    current = timestamp.time()
    return (
      current >= datetime.strptime("09:30", "%H:%M").time()
      and current <= datetime.strptime("11:30", "%H:%M").time()
    ) or (
      current >= datetime.strptime("13:00", "%H:%M").time()
      and current <= datetime.strptime("15:00", "%H:%M").time()
    )

  def _max_volume(
    self, request: OrderRequest, market_data: Optional[MarketDataSnapshot]
  ) -> Optional[int]:
    if not market_data:
      return None
    attr = (
      "max_limit_sell_order_volume"
      if request.order_type == OrderType.SELL
      else "max_limit_order_volume"
    )
    value = getattr(market_data, attr, None)
    return int(value) if value else None

def _risk_metadata_order_state(order_state: Dict[str, Any]) -> Dict[str, Any]:
  return {
    "open_order_count": int(order_state.get("open_order_count", 0) or 0),
    "buy_open_order_count": int(order_state.get("buy_open_order_count", 0) or 0),
    "sell_open_order_count": int(order_state.get("sell_open_order_count", 0) or 0),
    "oldest_open_order_at": order_state.get("oldest_open_order_at"),
    "open_order_status_counts": dict(order_state.get("open_order_status_counts") or {}),
    "open_order_type_counts": dict(order_state.get("open_order_type_counts") or {}),
  }


def _risk_metadata_broker_report(broker_report: Dict[str, Any]) -> Dict[str, Any]:
  return {
    "last_order_report_at": broker_report.get("last_order_report_at"),
    "last_trade_report_at": broker_report.get("last_trade_report_at"),
    "last_report_at": broker_report.get("last_report_at"),
    "report_lag_seconds": broker_report.get("report_lag_seconds"),
  }


def _optional_float(value: Any) -> Optional[float]:
  if value is None:
    return None
  try:
    return float(value)
  except (TypeError, ValueError):
    return None


def _first_optional_float(*values: Any) -> Optional[float]:
  for value in values:
    parsed = _optional_float(value)
    if parsed is not None:
      return parsed
  return None


def _optional_int(value: Any) -> Optional[int]:
  if value is None:
    return None
  try:
    return int(value)
  except (TypeError, ValueError):
    return None


def _min_optional(current: Optional[float], limit: float) -> float:
  if current is None:
    return float(limit)
  return min(float(current), float(limit))


def _max_optional(current: Optional[float], limit: float) -> float:
  if current is None:
    return float(limit)
  return max(float(current), float(limit))


def _normalize_state(*values: Any) -> str:
  for value in values:
    if value is None:
      continue
    text = str(value).strip()
    if text:
      return text.upper()
  return ""


def _public_cap_reason_code(reason_code: str) -> str:
  if reason_code == "MAX_POSITION_PCT":
    return "POSITION_LIMIT_CAP"
  if reason_code in {"MAX_NEW_BUY_AMOUNT_TODAY", "MIN_CASH_BUFFER"}:
    return "RISK_CONTEXT_CAP"
  return reason_code
