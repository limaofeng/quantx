"""Deterministic A-share market environment layer."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Dict, Optional

from .market_rules import MarketDataSnapshot


@dataclass
class MarketContextSnapshot:
  """Environment snapshot consumed by risk, sizing, and strategy layers."""

  instrument_code: str
  trade_date: Optional[str] = None
  timestamp: Optional[datetime] = None
  market_state: str = "NEUTRAL"
  sector_state: str = "NEUTRAL"
  concept_heat_state: str = "NEUTRAL"
  liquidity_state: str = "NORMAL"
  breadth_state: str = "NEUTRAL"
  volume_structure: str = "NORMAL"
  context_score: float = 0.0
  risk_tags: list[str] = field(default_factory=list)
  data_quality: str = "OK"
  previous_state: Optional[str] = None
  state_changed_reason: str = ""
  source_fingerprint: str = ""
  metrics: Dict[str, Any] = field(default_factory=dict)
  metadata: Dict[str, Any] = field(default_factory=dict)

  def to_dict(self) -> Dict[str, Any]:
    data = asdict(self)
    if self.timestamp is not None:
      data["timestamp"] = self.timestamp.isoformat()
    data["industry_state"] = self.sector_state
    data["risk_tags"] = sorted(set(self.risk_tags))
    return data


class EnvironmentLayer:
  """Build a reproducible single-instrument A-share market context."""

  DEFAULT_WEIGHTS = {
    "market": 0.30,
    "sector": 0.25,
    "concept": 0.05,
    "liquidity": 0.15,
    "breadth": 0.15,
    "volume": 0.10,
  }

  def build_snapshot(
    self,
    *,
    instrument_code: str,
    timestamp: Optional[datetime] = None,
    market_data: Optional[MarketDataSnapshot] = None,
    event: Optional[Any] = None,
    parameters: Optional[Dict[str, Any]] = None,
    previous_snapshot: Optional[Dict[str, Any]] = None,
  ) -> MarketContextSnapshot:
    params = dict(parameters or {})
    context = self._collect_context(
      parameters=params,
      event=event,
      market_data=market_data,
    )

    timestamp = timestamp or (market_data.timestamp if market_data else None)
    trade_date = _trade_date(context.get("trade_date"), timestamp)
    risk_tags: list[str] = list(context.get("risk_tags", []))
    quality_flags: list[str] = []

    require_market_index = _truthy(
      context.get(
        "require_market_index",
        params.get("require_market_index", params.get("environment_require_market_index")),
      )
    )
    if require_market_index and not self._has_market_index_data(context):
      quality_flags.append("INSUFFICIENT")
      risk_tags.append("missing_market_index")
    elif not self._has_market_index_data(context):
      quality_flags.append("DEGRADED")
      risk_tags.append("missing_market_index")

    if not self._has_breadth_data(context):
      quality_flags.append("DEGRADED")
      risk_tags.append("missing_breadth_data")

    if _truthy(context.get("require_volume")) and not self._has_volume_data(context):
      quality_flags.append("INSUFFICIENT")
      risk_tags.append("missing_volume_data")

    if market_data and (market_data.suspended or not market_data.is_trading):
      quality_flags.append("INSUFFICIENT")
      risk_tags.append("instrument_not_trading")
      context.setdefault("stock_state", "SUSPENDED")

    breadth_state = self._classify_breadth(context)
    market_state = self._classify_market(context, breadth_state)
    sector_state = self._classify_sector(context)
    concept_heat_state = self._classify_concept(context)
    liquidity_state = self._classify_liquidity(context)
    volume_structure = self._classify_volume_structure(context)

    previous_state = _first_text(
      context.get("previous_state"),
      (previous_snapshot or {}).get("market_state"),
    )
    state_changed_reason = ""
    allow_fast_recovery = _truthy(context.get("allow_intraday_environment_recovery"))
    if (
      previous_state
      and previous_state.upper() == "PANIC"
      and market_state == "RISK_ON"
      and not allow_fast_recovery
    ):
      market_state = "RISK_OFF"
      state_changed_reason = "panic_recovery_requires_confirmation"
      risk_tags.append("state_transition_guard")

    context_score = self._score_context(
      market_state=market_state,
      sector_state=sector_state,
      concept_heat_state=concept_heat_state,
      liquidity_state=liquidity_state,
      breadth_state=breadth_state,
      volume_structure=volume_structure,
      weights=dict(params.get("environment_weights") or {}),
    )
    risk_tags.extend(
      self._risk_tags(
        market_state=market_state,
        sector_state=sector_state,
        concept_heat_state=concept_heat_state,
        liquidity_state=liquidity_state,
        breadth_state=breadth_state,
        volume_structure=volume_structure,
      )
    )
    data_quality = self._resolve_data_quality(context, quality_flags)

    metrics = self._metrics(context)
    fingerprint = self._fingerprint(
      {
        "instrument_code": instrument_code,
        "trade_date": trade_date,
        "timestamp": timestamp.isoformat() if timestamp else None,
        "context": context,
        "states": {
          "market_state": market_state,
          "sector_state": sector_state,
          "concept_heat_state": concept_heat_state,
          "liquidity_state": liquidity_state,
          "breadth_state": breadth_state,
          "volume_structure": volume_structure,
          "data_quality": data_quality,
        },
      }
    )

    return MarketContextSnapshot(
      instrument_code=instrument_code,
      trade_date=trade_date,
      timestamp=timestamp,
      market_state=market_state,
      sector_state=sector_state,
      concept_heat_state=concept_heat_state,
      liquidity_state=liquidity_state,
      breadth_state=breadth_state,
      volume_structure=volume_structure,
      context_score=context_score,
      risk_tags=sorted(set(str(tag) for tag in risk_tags if tag)),
      data_quality=data_quality,
      previous_state=previous_state,
      state_changed_reason=state_changed_reason,
      source_fingerprint=fingerprint,
      metrics=metrics,
      metadata={
        "source": context.get("source"),
        "input_keys": sorted(context.keys()),
      },
    )

  def _collect_context(
    self,
    *,
    parameters: Dict[str, Any],
    event: Optional[Any],
    market_data: Optional[MarketDataSnapshot],
  ) -> Dict[str, Any]:
    context: Dict[str, Any] = {}
    context.update(dict(parameters.get("environment_context") or {}))
    context.update(dict(parameters.get("market_context") or {}))

    if market_data:
      context.setdefault("instrument_code", market_data.instrument_code)
      context.setdefault("price", market_data.price)
      context.setdefault("close", market_data.close if market_data.close else market_data.price)
      context.setdefault("volume", market_data.volume)
      context.setdefault("amount", market_data.amount)
      context.setdefault("is_trading", market_data.is_trading)
      context.setdefault("suspended", market_data.suspended)
      context.setdefault("source", market_data.source)
      context.setdefault("timestamp", market_data.timestamp)

    if event is not None:
      for key in (
        "market_state",
        "sector_state",
        "industry_state",
        "concept_heat_state",
        "liquidity_state",
        "breadth_state",
        "volume_structure",
        "context_score",
        "data_quality",
        "risk_tags",
        "market_return_1d",
        "market_return_5d",
        "market_return_20d",
        "market_amount",
        "market_amount_ma20",
        "market_amount_ratio",
        "market_price",
        "market_ema20",
        "market_ema60",
        "market_ema120",
        "sector_return_5d",
        "sector_return_20d",
        "sector_return_60d",
        "sector_amount_ratio",
        "sector_price",
        "sector_ema60",
        "sector_ema120",
        "concept_return_5d",
        "concept_amount_ratio",
        "concept_limit_up_count",
        "advancing_count",
        "declining_count",
        "limit_up_count",
        "limit_down_count",
        "volume_ratio",
        "amount_ratio",
        "turnover_rate",
        "price_return_1d",
        "price_position",
      ):
        value = getattr(event, key, None)
        if value is not None:
          context.setdefault(key, value)
    return context

  def _classify_market(self, context: Dict[str, Any], breadth_state: str) -> str:
    explicit = _state(context.get("market_state"))
    market_return_1d = _number(
      context.get("market_return_1d"),
      context.get("index_return_1d"),
    )
    market_return_5d = _number(context.get("market_return_5d"))
    amount_ratio = _number(
      context.get("market_amount_ratio"),
      _ratio(context.get("market_amount"), context.get("market_amount_ma20")),
    )
    price = _number(context.get("market_price"), context.get("index_close"))
    ema20 = _number(context.get("market_ema20"), context.get("index_ema20"))
    ema60 = _number(context.get("market_ema60"), context.get("index_ema60"))
    ema120 = _number(context.get("market_ema120"), context.get("index_ema120"))
    limit_down_count = _number(context.get("limit_down_count"))
    atr_rank = _number(context.get("atr_pct_rank"), context.get("market_atr_rank"))

    panic = (
      (market_return_1d is not None and market_return_1d <= -0.04)
      and (
        (amount_ratio is not None and amount_ratio >= 1.40)
        or breadth_state == "EXTREME_NEGATIVE"
        or (limit_down_count is not None and limit_down_count >= 80)
      )
    ) or (
      limit_down_count is not None
      and limit_down_count >= 150
    ) or (
      market_return_1d is not None
      and market_return_1d <= -0.025
      and atr_rank is not None
      and atr_rank >= 0.90
    )
    if panic:
      return "PANIC"
    if explicit:
      return explicit

    risk_off = (
      (price is not None and ema60 is not None and price < ema60)
      or (market_return_5d is not None and market_return_5d <= -0.05)
      or (
        market_return_1d is not None
        and market_return_1d <= -0.02
        and amount_ratio is not None
        and amount_ratio >= 1.20
      )
      or breadth_state in {"NEGATIVE", "EXTREME_NEGATIVE"}
    )
    if risk_off:
      return "RISK_OFF"

    risk_on = (
      price is not None
      and ema60 is not None
      and price >= ema60
      and (ema20 is None or ema20 >= ema60)
      and (ema120 is None or price >= ema120)
      and breadth_state in {"POSITIVE", "NEUTRAL"}
    )
    if risk_on:
      return "RISK_ON"
    return "NEUTRAL"

  def _classify_sector(self, context: Dict[str, Any]) -> str:
    explicit = _state(context.get("sector_state"), context.get("industry_state"))
    if explicit:
      return explicit

    sector_return_20d = _number(context.get("sector_return_20d"))
    market_return_20d = _number(context.get("market_return_20d"))
    relative = _number(context.get("sector_relative_strength"))
    if relative is None and sector_return_20d is not None and market_return_20d is not None:
      relative = sector_return_20d - market_return_20d

    price = _number(context.get("sector_price"))
    ema60 = _number(context.get("sector_ema60"))
    ema120 = _number(context.get("sector_ema120"))
    amount_ratio = _number(context.get("sector_amount_ratio"))

    if (
      price is not None
      and ema120 is not None
      and price < ema120
      and (
        relative is None
        or relative <= -0.05
        or (amount_ratio is not None and amount_ratio >= 1.30)
      )
    ):
      return "BROKEN"
    if (
      (relative is not None and relative <= -0.03)
      or (price is not None and ema60 is not None and price < ema60)
    ):
      return "WEAK"
    if (
      relative is not None
      and relative >= 0.03
      and (price is None or ema60 is None or price >= ema60)
    ):
      return "STRONG"
    return "NEUTRAL"

  def _classify_concept(self, context: Dict[str, Any]) -> str:
    explicit = _state(context.get("concept_heat_state"))
    if explicit:
      return explicit

    concept_return = _number(context.get("concept_return_5d"))
    amount_ratio = _number(context.get("concept_amount_ratio"))
    limit_up_count = _number(context.get("concept_limit_up_count"))
    price_position = _number(context.get("price_position"))
    stock_return = _number(context.get("stock_return_5d"), context.get("price_return_5d"))

    if (
      concept_return is not None
      and concept_return >= 0.12
      and (amount_ratio is None or amount_ratio >= 1.50)
      and (price_position is None or price_position >= 0.70)
      and (stock_return is None or stock_return < concept_return * 0.50)
    ):
      return "OVERHEATED"
    if (
      (concept_return is not None and concept_return >= 0.04)
      or (limit_up_count is not None and limit_up_count >= 3)
    ):
      return "HOT"
    if concept_return is not None and concept_return <= -0.05:
      return "COLD"
    return "NEUTRAL"

  def _classify_liquidity(self, context: Dict[str, Any]) -> str:
    explicit = _state(context.get("liquidity_state"))
    if explicit:
      return explicit

    amount_ratio = _number(
      context.get("amount_ratio"),
      context.get("stock_amount_ratio"),
      _ratio(context.get("amount"), context.get("amount_ma20")),
      _ratio(context.get("volume"), context.get("volume_ma20")),
    )
    turnover_rate = _number(context.get("turnover_rate"))
    min_amount = _number(context.get("min_liquidity_amount"))
    amount = _number(context.get("amount"))

    if amount is not None and min_amount is not None and amount < min_amount:
      return "DRY"
    if amount_ratio is not None:
      if amount_ratio <= 0.45:
        return "DRY"
      if amount_ratio <= 0.80:
        return "SHRINKING"
      if amount_ratio >= 1.20:
        return "EXPANDING"
    if turnover_rate is not None:
      if turnover_rate <= 0.003:
        return "DRY"
      if turnover_rate >= 0.03:
        return "EXPANDING"
    return "NORMAL"

  def _classify_breadth(self, context: Dict[str, Any]) -> str:
    explicit = _state(context.get("breadth_state"))
    if explicit:
      return explicit

    advancing = _number(context.get("advancing_count"), context.get("up_count"))
    declining = _number(context.get("declining_count"), context.get("down_count"))
    limit_up = _number(context.get("limit_up_count"))
    limit_down = _number(context.get("limit_down_count"))
    if advancing is None or declining is None:
      return "NEUTRAL"

    total = advancing + declining
    if total <= 0:
      return "NEUTRAL"
    decline_ratio = declining / total
    advance_ratio = advancing / total
    if decline_ratio >= 0.75 or (limit_down is not None and limit_down >= 80):
      return "EXTREME_NEGATIVE"
    if decline_ratio >= 0.58:
      return "NEGATIVE"
    if advance_ratio >= 0.56 and (limit_up is None or limit_down is None or limit_up >= limit_down):
      return "POSITIVE"
    return "NEUTRAL"

  def _classify_volume_structure(self, context: Dict[str, Any]) -> str:
    explicit = _state(context.get("volume_structure"))
    if explicit:
      return explicit

    volume_ratio = _number(
      context.get("volume_ratio"),
      context.get("amount_ratio"),
      context.get("stock_amount_ratio"),
      _ratio(context.get("volume"), context.get("volume_ma20")),
      _ratio(context.get("amount"), context.get("amount_ma20")),
    )
    price_return = _number(context.get("price_return_1d"), context.get("stock_return_1d"))
    price_position = _number(context.get("price_position"))
    support_break = _truthy(context.get("support_break"))
    long_upper_shadow = _truthy(context.get("long_upper_shadow"))

    if volume_ratio is not None and volume_ratio >= 1.30:
      if support_break or (price_return is not None and price_return <= -0.03):
        return "BREAKDOWN"
      if (
        long_upper_shadow
        or (price_position is not None and price_position >= 0.70 and (price_return or 0.0) <= 0.01)
      ):
        return "DISTRIBUTION"
      if (
        price_position is not None
        and price_position <= 0.35
        and (price_return is None or price_return >= 0.0)
      ):
        return "ACCUMULATION"
    return "NORMAL"

  def _score_context(
    self,
    *,
    market_state: str,
    sector_state: str,
    concept_heat_state: str,
    liquidity_state: str,
    breadth_state: str,
    volume_structure: str,
    weights: Dict[str, Any],
  ) -> float:
    merged_weights = dict(self.DEFAULT_WEIGHTS)
    for key, value in weights.items():
      parsed = _number(value)
      if parsed is not None:
        merged_weights[key] = parsed

    score = (
      merged_weights["market"] * _score("market", market_state)
      + merged_weights["sector"] * _score("sector", sector_state)
      + merged_weights["concept"] * _score("concept", concept_heat_state)
      + merged_weights["liquidity"] * _score("liquidity", liquidity_state)
      + merged_weights["breadth"] * _score("breadth", breadth_state)
      + merged_weights["volume"] * _score("volume", volume_structure)
    )
    return round(max(-1.0, min(score, 1.0)), 4)

  def _risk_tags(
    self,
    *,
    market_state: str,
    sector_state: str,
    concept_heat_state: str,
    liquidity_state: str,
    breadth_state: str,
    volume_structure: str,
  ) -> list[str]:
    tags: list[str] = []
    if market_state == "PANIC":
      tags.extend(["market_panic", "market_selloff"])
    elif market_state == "RISK_OFF":
      tags.append("market_risk_off")
    if sector_state == "BROKEN":
      tags.append("sector_breakdown")
    elif sector_state == "WEAK":
      tags.append("sector_underperforming")
    if concept_heat_state == "OVERHEATED":
      tags.extend(["concept_overheated", "theme_distribution_risk"])
    if liquidity_state == "DRY":
      tags.append("liquidity_dry")
    elif liquidity_state == "SHRINKING":
      tags.append("liquidity_shrinking")
    if breadth_state == "EXTREME_NEGATIVE":
      tags.append("breadth_extreme_negative")
    elif breadth_state == "NEGATIVE":
      tags.append("breadth_negative")
    if volume_structure == "DISTRIBUTION":
      tags.append("volume_distribution")
    elif volume_structure == "BREAKDOWN":
      tags.append("volume_breakdown")
    return tags

  def _resolve_data_quality(self, context: Dict[str, Any], flags: list[str]) -> str:
    explicit = _state(context.get("data_quality"))
    if explicit in {"MISSING", "STALE"}:
      return explicit
    if "INSUFFICIENT" in flags:
      return "INSUFFICIENT"
    if explicit == "INSUFFICIENT":
      return "INSUFFICIENT"
    if "DEGRADED" in flags:
      return "DEGRADED"
    return explicit or "OK"

  def _metrics(self, context: Dict[str, Any]) -> Dict[str, Any]:
    keys = (
      "market_return_1d",
      "market_return_5d",
      "market_return_20d",
      "market_amount_ratio",
      "sector_relative_strength",
      "sector_return_20d",
      "concept_return_5d",
      "amount_ratio",
      "volume_ratio",
      "price_return_1d",
      "price_position",
      "advancing_count",
      "declining_count",
      "limit_up_count",
      "limit_down_count",
    )
    return {key: context[key] for key in keys if key in context}

  def _has_market_index_data(self, context: Dict[str, Any]) -> bool:
    return any(
      context.get(key) is not None
      for key in (
        "market_state",
        "market_return_1d",
        "market_return_5d",
        "market_price",
        "index_close",
      )
    )

  def _has_breadth_data(self, context: Dict[str, Any]) -> bool:
    return any(
      context.get(key) is not None
      for key in (
        "breadth_state",
        "advancing_count",
        "declining_count",
        "limit_up_count",
        "limit_down_count",
      )
    )

  def _has_volume_data(self, context: Dict[str, Any]) -> bool:
    return any(
      context.get(key) is not None
      for key in (
        "volume",
        "amount",
        "volume_ratio",
        "amount_ratio",
        "stock_amount_ratio",
      )
    )

  def _fingerprint(self, payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
    return hashlib.sha1(encoded.encode("utf-8")).hexdigest()[:16]


def _score(group: str, value: str) -> float:
  tables = {
    "market": {
      "RISK_ON": 0.55,
      "NEUTRAL": 0.0,
      "RISK_OFF": -0.55,
      "PANIC": -0.90,
    },
    "sector": {
      "STRONG": 0.45,
      "NEUTRAL": 0.0,
      "WEAK": -0.35,
      "BROKEN": -0.75,
    },
    "concept": {
      "HOT": 0.15,
      "NEUTRAL": 0.0,
      "COLD": -0.10,
      "OVERHEATED": -0.25,
    },
    "liquidity": {
      "EXPANDING": 0.25,
      "NORMAL": 0.0,
      "SHRINKING": -0.25,
      "DRY": -0.65,
    },
    "breadth": {
      "POSITIVE": 0.25,
      "NEUTRAL": 0.0,
      "NEGATIVE": -0.35,
      "EXTREME_NEGATIVE": -0.75,
    },
    "volume": {
      "ACCUMULATION": 0.25,
      "NORMAL": 0.0,
      "DISTRIBUTION": -0.35,
      "BREAKDOWN": -0.75,
    },
  }
  return tables[group].get(value, 0.0)


def _state(*values: Any) -> str:
  for value in values:
    if value is None:
      continue
    text = str(value).strip().upper()
    if text:
      return text
  return ""


def _first_text(*values: Any) -> Optional[str]:
  for value in values:
    if value is None:
      continue
    text = str(value).strip()
    if text:
      return text
  return None


def _number(*values: Any) -> Optional[float]:
  for value in values:
    if value is None:
      continue
    try:
      return float(value)
    except (TypeError, ValueError):
      continue
  return None


def _ratio(value: Any, baseline: Any) -> Optional[float]:
  numerator = _number(value)
  denominator = _number(baseline)
  if numerator is None or denominator is None or denominator <= 0:
    return None
  return numerator / denominator


def _truthy(value: Any) -> bool:
  if isinstance(value, str):
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}
  return bool(value)


def _trade_date(value: Any, timestamp: Optional[datetime]) -> Optional[str]:
  if isinstance(value, date):
    return value.isoformat()
  if value is not None:
    return str(value)
  if timestamp is not None:
    return timestamp.date().isoformat()
  return None
