"""Position adjustment layer for A-share single-instrument strategies."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class PositionProfileName(str, Enum):
  AGGRESSIVE_ACCUMULATION = "AGGRESSIVE_ACCUMULATION"
  NORMAL_BALANCE = "NORMAL_BALANCE"
  RANGE_TRADING = "RANGE_TRADING"
  CAUTIOUS = "CAUTIOUS"
  DISTRIBUTION = "DISTRIBUTION"
  DEFENSIVE = "DEFENSIVE"


@dataclass(frozen=True)
class PositionProfileTemplate:
  profile: PositionProfileName
  min_position_pct: float
  max_position_pct: float
  target_cash_buffer_pct: float
  core_share_min: float
  core_share_max: float
  swing_max_pct: float
  balance_beta_multiplier: float
  inventory_gamma_multiplier: float
  grid_step_multiplier: float
  allow_core_buy: bool
  allow_swing_buy: bool
  allow_core_sell: bool = True
  allow_swing_sell: bool = True


@dataclass
class PositionAdjustmentProfile:
  profile: str
  min_position_pct: float
  max_position_pct: float
  target_cash_buffer_pct: float
  core_share_min: float
  core_share_max: float
  swing_max_pct: float
  balance_beta_multiplier: float
  inventory_gamma_multiplier: float
  grid_step_multiplier: float
  allow_core_buy: bool
  allow_swing_buy: bool
  allow_core_sell: bool
  allow_swing_sell: bool
  reason_tags: list[str] = field(default_factory=list)
  bucket_caps: Dict[str, Dict[str, float]] = field(default_factory=dict)
  allow_bucket_buy: Dict[str, bool] = field(default_factory=dict)
  allow_bucket_sell: Dict[str, bool] = field(default_factory=dict)
  engine_multipliers: Dict[str, float] = field(default_factory=dict)
  current_position_pct: float = 0.0

  def to_dict(self) -> Dict[str, Any]:
    return asdict(self)


PROFILE_TEMPLATES: Dict[PositionProfileName, PositionProfileTemplate] = {
  PositionProfileName.AGGRESSIVE_ACCUMULATION: PositionProfileTemplate(
    profile=PositionProfileName.AGGRESSIVE_ACCUMULATION,
    min_position_pct=0.30,
    max_position_pct=0.80,
    target_cash_buffer_pct=0.20,
    core_share_min=0.80,
    core_share_max=0.95,
    swing_max_pct=0.10,
    balance_beta_multiplier=1.20,
    inventory_gamma_multiplier=0.85,
    grid_step_multiplier=1.00,
    allow_core_buy=True,
    allow_swing_buy=True,
  ),
  PositionProfileName.NORMAL_BALANCE: PositionProfileTemplate(
    profile=PositionProfileName.NORMAL_BALANCE,
    min_position_pct=0.20,
    max_position_pct=0.70,
    target_cash_buffer_pct=0.25,
    core_share_min=0.65,
    core_share_max=0.85,
    swing_max_pct=0.15,
    balance_beta_multiplier=1.00,
    inventory_gamma_multiplier=1.00,
    grid_step_multiplier=1.00,
    allow_core_buy=True,
    allow_swing_buy=True,
  ),
  PositionProfileName.RANGE_TRADING: PositionProfileTemplate(
    profile=PositionProfileName.RANGE_TRADING,
    min_position_pct=0.25,
    max_position_pct=0.65,
    target_cash_buffer_pct=0.25,
    core_share_min=0.50,
    core_share_max=0.75,
    swing_max_pct=0.25,
    balance_beta_multiplier=0.95,
    inventory_gamma_multiplier=1.00,
    grid_step_multiplier=0.90,
    allow_core_buy=True,
    allow_swing_buy=True,
  ),
  PositionProfileName.CAUTIOUS: PositionProfileTemplate(
    profile=PositionProfileName.CAUTIOUS,
    min_position_pct=0.10,
    max_position_pct=0.50,
    target_cash_buffer_pct=0.30,
    core_share_min=0.75,
    core_share_max=0.95,
    swing_max_pct=0.05,
    balance_beta_multiplier=0.75,
    inventory_gamma_multiplier=1.20,
    grid_step_multiplier=1.25,
    allow_core_buy=True,
    allow_swing_buy=False,
  ),
  PositionProfileName.DISTRIBUTION: PositionProfileTemplate(
    profile=PositionProfileName.DISTRIBUTION,
    min_position_pct=0.00,
    max_position_pct=0.40,
    target_cash_buffer_pct=0.35,
    core_share_min=0.80,
    core_share_max=1.00,
    swing_max_pct=0.00,
    balance_beta_multiplier=0.60,
    inventory_gamma_multiplier=1.30,
    grid_step_multiplier=1.50,
    allow_core_buy=False,
    allow_swing_buy=False,
  ),
  PositionProfileName.DEFENSIVE: PositionProfileTemplate(
    profile=PositionProfileName.DEFENSIVE,
    min_position_pct=0.00,
    max_position_pct=0.20,
    target_cash_buffer_pct=0.40,
    core_share_min=0.90,
    core_share_max=1.00,
    swing_max_pct=0.00,
    balance_beta_multiplier=0.45,
    inventory_gamma_multiplier=1.40,
    grid_step_multiplier=1.50,
    allow_core_buy=False,
    allow_swing_buy=False,
  ),
}


class PositionAdjustmentLayer:
  """Translate environment and pre-risk caps into strategy position parameters."""

  def build_profile(
    self,
    *,
    market_context: Optional[Dict[str, Any]] = None,
    risk_caps: Optional[Dict[str, Any]] = None,
    portfolio_state: Optional[Dict[str, Any]] = None,
    bucket_ledger: Optional[Dict[str, Any]] = None,
    runtime_state: Optional[Dict[str, Any]] = None,
    parameters: Optional[Dict[str, Any]] = None,
    instrument_code: Optional[str] = None,
  ) -> PositionAdjustmentProfile:
    market_context = dict(market_context or {})
    risk_caps = dict(risk_caps or {})
    portfolio_state = dict(portfolio_state or {})
    bucket_ledger = dict(bucket_ledger or {})
    runtime_state = dict(runtime_state or {})
    parameters = dict(parameters or {})

    profile_name, reason_tags = self._select_profile(
      market_context=market_context,
      risk_caps=risk_caps,
      runtime_state=runtime_state,
    )
    template = PROFILE_TEMPLATES[profile_name]
    profile = self._from_template(template, reason_tags)

    profile.current_position_pct = self._current_position_pct(
      portfolio_state, instrument_code
    )
    self._apply_parameter_overrides(profile, parameters)
    self._apply_risk_caps(profile, risk_caps)
    self._build_bucket_caps(profile)
    return profile

  def _select_profile(
    self,
    *,
    market_context: Dict[str, Any],
    risk_caps: Dict[str, Any],
    runtime_state: Dict[str, Any],
  ) -> tuple[PositionProfileName, list[str]]:
    tags: list[str] = []
    if risk_caps.get("kill_switch_active"):
      return PositionProfileName.DEFENSIVE, ["kill_switch"]

    data_quality = _upper(market_context.get("data_quality"))
    if data_quality in {"INSUFFICIENT", "MISSING"}:
      tags.append("data_quality_insufficient")

    market_state = _first_upper(
      market_context,
      "market_state",
      "market_regime",
      "market_risk_state",
      "environment_state",
    )
    industry_state = _first_upper(
      market_context, "industry_state", "sector_state", "sector_trend_state"
    )
    stock_state = _first_upper(
      market_context, "stock_state", "trend_state", "structure_state"
    )
    risk_mode = _upper(risk_caps.get("risk_mode") or market_context.get("risk_mode"))

    if market_state in {"PANIC", "CRASH", "BROKEN"}:
      return PositionProfileName.DEFENSIVE, tags + ["market_panic"]
    if industry_state in {"BROKEN", "BREAKDOWN"}:
      return PositionProfileName.DEFENSIVE, tags + ["sector_broken"]
    if _truthy(market_context.get("defensive")):
      return PositionProfileName.DEFENSIVE, tags + ["defensive_flag"]

    high_distribution_score = _optional_float(
      market_context.get("high_distribution_score")
      or runtime_state.get("high_distribution_score")
    )
    if (
      _truthy(market_context.get("high_distribution"))
      or _truthy(runtime_state.get("high_distribution"))
      or stock_state in {"HIGH_DISTRIBUTION", "DISTRIBUTION"}
      or (high_distribution_score is not None and high_distribution_score >= 75)
    ):
      return PositionProfileName.DISTRIBUTION, tags + ["distribution_risk"]

    if risk_mode in {"RISK_OFF", "RISK_REDUCED", "REDUCED"}:
      return PositionProfileName.CAUTIOUS, tags + ["risk_caps_reduced"]
    if market_state in {"RISK_OFF", "WEAK"} or data_quality in {"INSUFFICIENT", "MISSING"}:
      return PositionProfileName.CAUTIOUS, tags + ["market_weak_or_missing"]

    low_score = _optional_float(
      market_context.get("low_score") or runtime_state.get("low_score")
    )
    low_accumulation = (
      _truthy(market_context.get("low_accumulation"))
      or stock_state in {"LOW_ACCUMULATION", "LOW_SUPPORT"}
      or (low_score is not None and low_score >= 70)
    )
    market_stable = market_state in {"", "NORMAL", "STABLE", "RISK_ON", "BULL"}
    industry_strong = industry_state in {"STRONG", "UPTREND", "BULL"}
    if market_stable and industry_strong and low_accumulation:
      return PositionProfileName.AGGRESSIVE_ACCUMULATION, tags + ["low_accumulation"]

    range_bound = (
      _truthy(market_context.get("range_bound"))
      or stock_state in {"RANGE", "RANGE_TRADING", "BOX"}
    )
    industry_neutral = industry_state in {"", "NEUTRAL", "NORMAL", "STABLE"}
    if market_stable and industry_neutral and range_bound:
      return PositionProfileName.RANGE_TRADING, tags + ["range_trading"]

    return PositionProfileName.NORMAL_BALANCE, tags + ["default_normal"]

  def _from_template(
    self, template: PositionProfileTemplate, reason_tags: list[str]
  ) -> PositionAdjustmentProfile:
    return PositionAdjustmentProfile(
      profile=template.profile.value,
      min_position_pct=template.min_position_pct,
      max_position_pct=template.max_position_pct,
      target_cash_buffer_pct=template.target_cash_buffer_pct,
      core_share_min=template.core_share_min,
      core_share_max=template.core_share_max,
      swing_max_pct=template.swing_max_pct,
      balance_beta_multiplier=template.balance_beta_multiplier,
      inventory_gamma_multiplier=template.inventory_gamma_multiplier,
      grid_step_multiplier=template.grid_step_multiplier,
      allow_core_buy=template.allow_core_buy,
      allow_swing_buy=template.allow_swing_buy,
      allow_core_sell=template.allow_core_sell,
      allow_swing_sell=template.allow_swing_sell,
      reason_tags=list(reason_tags),
    )

  def _apply_parameter_overrides(
    self, profile: PositionAdjustmentProfile, parameters: Dict[str, Any]
  ) -> None:
    overrides = dict(parameters.get("position_profile_overrides") or {})
    for key in (
      "min_position_pct",
      "max_position_pct",
      "target_cash_buffer_pct",
      "core_share_min",
      "core_share_max",
      "swing_max_pct",
      "balance_beta_multiplier",
      "inventory_gamma_multiplier",
      "grid_step_multiplier",
    ):
      if key in overrides:
        value = _optional_float(overrides.get(key))
        if value is not None:
          setattr(profile, key, value)
    for key in (
      "allow_core_buy",
      "allow_swing_buy",
      "allow_core_sell",
      "allow_swing_sell",
    ):
      if key in overrides:
        value = _optional_bool(overrides.get(key))
        if value is not None:
          setattr(profile, key, value)

  def _apply_risk_caps(
    self, profile: PositionAdjustmentProfile, risk_caps: Dict[str, Any]
  ) -> None:
    risk_tags = list(risk_caps.get("risk_tags", []))
    reason_codes = list(risk_caps.get("reason_codes", []))
    profile.reason_tags = sorted(set(profile.reason_tags + risk_tags + reason_codes))

    cap_max_position = _optional_float(risk_caps.get("max_position_pct"))
    if cap_max_position is not None:
      profile.max_position_pct = min(profile.max_position_pct, cap_max_position)

    min_cash_buffer = _optional_float(risk_caps.get("min_cash_buffer_pct"))
    if min_cash_buffer is not None:
      profile.target_cash_buffer_pct = max(
        profile.target_cash_buffer_pct, min_cash_buffer
      )

    if risk_caps.get("allow_buy") is False:
      profile.allow_core_buy = False
      profile.allow_swing_buy = False
    if risk_caps.get("only_reduce_position"):
      profile.allow_core_buy = False
      profile.allow_swing_buy = False
      profile.max_position_pct = min(
        profile.max_position_pct, profile.current_position_pct
      )
      profile.min_position_pct = 0.0
    if risk_caps.get("allow_sell") is False:
      profile.allow_core_sell = False
      profile.allow_swing_sell = False
    if risk_caps.get("kill_switch_active"):
      defensive = PROFILE_TEMPLATES[PositionProfileName.DEFENSIVE]
      profile.profile = defensive.profile.value
      profile.min_position_pct = defensive.min_position_pct
      profile.max_position_pct = min(profile.max_position_pct, defensive.max_position_pct)
      profile.target_cash_buffer_pct = max(
        profile.target_cash_buffer_pct, defensive.target_cash_buffer_pct
      )
      profile.core_share_min = defensive.core_share_min
      profile.core_share_max = defensive.core_share_max
      profile.swing_max_pct = defensive.swing_max_pct
      profile.balance_beta_multiplier = defensive.balance_beta_multiplier
      profile.inventory_gamma_multiplier = defensive.inventory_gamma_multiplier
      profile.grid_step_multiplier = defensive.grid_step_multiplier
      profile.allow_core_buy = False
      profile.allow_swing_buy = False

    profile.min_position_pct = max(0.0, min(profile.min_position_pct, profile.max_position_pct))
    profile.max_position_pct = max(0.0, min(profile.max_position_pct, 1.0))
    profile.target_cash_buffer_pct = max(0.0, min(profile.target_cash_buffer_pct, 1.0))
    profile.core_share_min = max(0.0, min(profile.core_share_min, 1.0))
    profile.core_share_max = max(profile.core_share_min, min(profile.core_share_max, 1.0))
    profile.swing_max_pct = max(0.0, min(profile.swing_max_pct, profile.max_position_pct))

  def _build_bucket_caps(self, profile: PositionAdjustmentProfile) -> None:
    profile.bucket_caps = {
      "core": {
        "min_pct": profile.min_position_pct * profile.core_share_min,
        "max_pct": profile.max_position_pct * profile.core_share_max,
      },
      "swing": {
        "min_pct": 0.0,
        "max_pct": min(
          profile.swing_max_pct,
          profile.max_position_pct * max(0.0, 1.0 - profile.core_share_min),
        ),
      },
      "locked_core": {"min_pct": 0.0, "max_pct": 0.0},
    }
    profile.allow_bucket_buy = {
      "core": profile.allow_core_buy,
      "swing": profile.allow_swing_buy,
      "locked_core": False,
    }
    profile.allow_bucket_sell = {
      "core": profile.allow_core_sell,
      "swing": profile.allow_swing_sell,
      "locked_core": False,
    }
    profile.engine_multipliers = {
      "balance_beta": profile.balance_beta_multiplier,
      "inventory_gamma": profile.inventory_gamma_multiplier,
      "grid_step": profile.grid_step_multiplier,
    }

  def _current_position_pct(
    self, portfolio_state: Dict[str, Any], instrument_code: Optional[str]
  ) -> float:
    account = dict(portfolio_state.get("account") or {})
    total_asset = _optional_float(
      account.get("total_asset") or account.get("cash_total") or account.get("total_equity")
    )
    if not total_asset or total_asset <= 0:
      return 0.0
    positions = portfolio_state.get("positions") or {}
    position = positions.get(instrument_code, {}) if instrument_code else {}
    market_value = _optional_float(position.get("market_value"))
    if market_value is None:
      price = _optional_float(position.get("last_price") or position.get("price"))
      volume = _optional_float(
        position.get("long_volume") or position.get("total_volume") or 0
      )
      market_value = (price or 0.0) * (volume or 0.0)
    return max(0.0, min(float(market_value) / float(total_asset), 1.0))


def _upper(value: Any) -> str:
  return str(value or "").strip().upper()


def _first_upper(source: Dict[str, Any], *keys: str) -> str:
  for key in keys:
    value = source.get(key)
    if value not in (None, ""):
      return _upper(value)
  return ""


def _truthy(value: Any) -> bool:
  if isinstance(value, str):
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}
  return bool(value)


def _optional_bool(value: Any) -> Optional[bool]:
  if value is None:
    return None
  if isinstance(value, bool):
    return value
  if isinstance(value, str):
    text = value.strip().lower()
    if not text:
      return None
    if text in {"1", "true", "yes", "y", "on", "enabled", "allowed"}:
      return True
    if text in {"0", "false", "no", "n", "off", "disabled", "disallowed"}:
      return False
    return None
  if isinstance(value, (int, float)):
    return bool(value)
  return None


def _optional_float(value: Any) -> Optional[float]:
  if value is None:
    return None
  try:
    return float(value)
  except (TypeError, ValueError):
    return None
