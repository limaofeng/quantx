"""统一编排层：把风险上下限与仓位方向规则统一为策略执行画像。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional


def _to_float(value: Any) -> Optional[float]:
  try:
    if value is None:
      return None
    if isinstance(value, (int, float)):
      return float(value)
    text = str(value).strip()
    if not text:
      return None
    return float(text)
  except (TypeError, ValueError):
    return None


def _to_int(value: Any) -> Optional[int]:
  try:
    if value is None:
      return None
    if isinstance(value, bool):
      return int(value)
    if isinstance(value, (int, float)):
      return int(value)
    text = str(value).strip()
    if not text:
      return None
    return int(float(text))
  except (TypeError, ValueError):
    return None


def _to_bool(value: Any, default: bool = False) -> bool:
  if isinstance(value, bool):
    return value
  if isinstance(value, str):
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled", "allowed"}:
      return True
    if normalized in {"0", "false", "no", "off", "disabled", "disallowed"}:
      return False
  if isinstance(value, (int, float)):
    return bool(value)
  return default


@dataclass(frozen=True)
class PortfolioExecutionProfile:
  """Strategy-friendly execution capability summary."""

  allow_core_buy: bool = True
  allow_core_sell: bool = True
  allow_swing_buy: bool = True
  allow_swing_sell: bool = True
  allow_locked_core_sell: bool = False
  max_order_cash: Optional[float] = None
  max_order_qty: Optional[int] = None
  max_daily_spend_cash: Optional[float] = None
  max_daily_sell_qty: Optional[int] = None
  daily_buy_used: float = 0.0
  daily_sell_used: int = 0
  cooldown_tokens: List[str] = field(default_factory=list)
  day_state: Dict[str, Any] = field(default_factory=dict)
  constraints_version: str = "v1.0"
  rejected_reasons: List[str] = field(default_factory=list)
  source_layer: str = "portfolio_orchestration"

  def to_dict(self) -> Dict[str, Any]:
    return asdict(self)


class PortfolioOrchestrationLayer:
  """Compose risk caps and position profile into one execution picture for strategies."""

  def build_profile(
    self,
    *,
    market_context: Optional[Dict[str, Any]] = None,
    risk_caps: Optional[Dict[str, Any]] = None,
    position_profile: Optional[Dict[str, Any]] = None,
    portfolio_state: Optional[Dict[str, Any]] = None,
    runtime_state: Optional[Dict[str, Any]] = None,
    parameters: Optional[Dict[str, Any]] = None,
    instrument_code: Optional[str] = None,
  ) -> PortfolioExecutionProfile:
    del instrument_code
    market_context = dict(market_context or {})
    risk_caps = dict(risk_caps or {})
    position_profile = dict(position_profile or {})
    portfolio_state = dict(portfolio_state or {})
    runtime_state = dict(runtime_state or {})
    parameters = dict(parameters or {})

    allow_core_buy = _to_bool(position_profile.get("allow_core_buy"), True)
    allow_core_sell = _to_bool(position_profile.get("allow_core_sell"), True)
    allow_swing_buy = _to_bool(position_profile.get("allow_swing_buy"), True)
    allow_swing_sell = _to_bool(position_profile.get("allow_swing_sell"), True)
    allow_locked_core_sell = _to_bool(position_profile.get("allow_locked_core_sell"), False)

    max_order_cash = _to_float(
      risk_caps.get("max_single_order_amount")
      or risk_caps.get("max_order_amount")
      or parameters.get("max_single_order_amount")
      or parameters.get("max_order_amount")
      or risk_caps.get("max_order_cash")
    )
    max_order_qty = _to_int(
      risk_caps.get("max_single_order_qty")
      or risk_caps.get("max_volume")
      or risk_caps.get("max_order_qty")
      or risk_caps.get("max_limit_order_volume")
      or parameters.get("max_single_order_qty")
    )
    max_daily_spend_cash = _to_float(
      risk_caps.get("max_new_buy_amount_today")
      or risk_caps.get("max_daily_buy_amount")
      or risk_caps.get("daily_buy_amount_cap")
    )
    max_daily_sell_qty = _to_int(
      risk_caps.get("max_daily_sell_volume")
      or risk_caps.get("max_sell_volume_today")
      or risk_caps.get("daily_sell_volume_cap")
    )
    daily_buy_used = _to_float(
      runtime_state.get("daily_buy_used")
      or runtime_state.get("used_daily_buy")
      or runtime_state.get("today_buy_amount")
      or portfolio_state.get("daily_buy_used")
    ) or 0.0
    daily_sell_used = _to_int(
      runtime_state.get("daily_sell_used")
      or runtime_state.get("used_daily_sell")
      or runtime_state.get("today_sell_volume")
      or portfolio_state.get("daily_sell_used")
    ) or 0

    day_state = {
      "trade_date": market_context.get("trade_date")
      or str(date.today()),
      "risk_mode": risk_caps.get("risk_mode"),
      "data_quality": market_context.get("data_quality"),
    }

    rejected_reasons: List[str] = []
    if _to_bool(risk_caps.get("kill_switch_active"), False):
      rejected_reasons.append("risk_kill_switch")
    if not _to_bool(position_profile.get("allow_swing_buy"), True) and not _to_bool(
      position_profile.get("allow_core_buy"), True
    ):
      rejected_reasons.append("position_profile_disallow_buy")
    if _to_float(max_order_cash) == 0.0:
      rejected_reasons.append("zero_max_order_cash")

    return PortfolioExecutionProfile(
      allow_core_buy=allow_core_buy,
      allow_core_sell=allow_core_sell,
      allow_swing_buy=allow_swing_buy,
      allow_swing_sell=allow_swing_sell,
      allow_locked_core_sell=allow_locked_core_sell,
      max_order_cash=max_order_cash,
      max_order_qty=max_order_qty,
      max_daily_spend_cash=max_daily_spend_cash,
      max_daily_sell_qty=max_daily_sell_qty,
      daily_buy_used=daily_buy_used,
      daily_sell_used=daily_sell_used,
      cooldown_tokens=list(position_profile.get("cooldown_tokens", []) or []),
      day_state=day_state,
      constraints_version="v1.0",
      rejected_reasons=rejected_reasons,
      source_layer="portfolio_orchestration",
    )
