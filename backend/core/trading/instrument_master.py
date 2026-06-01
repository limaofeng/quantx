"""Instrument master aggregation snapshot for A-share trading checks."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional


@dataclass
class InstrumentMasterSnapshot:
  instrument_code: str
  trading_date: Optional[date] = None
  exchange: str = ""
  is_trading_day: bool = True
  suspended: bool = False
  is_st: bool = False
  delist_risk: bool = False
  limit_up: Optional[float] = None
  limit_down: Optional[float] = None
  min_buy_volume: int = 100
  min_sell_volume: int = 1
  max_buy_volume: Optional[int] = None
  max_sell_volume: Optional[int] = None
  industry: Optional[str] = None
  concepts: List[str] = field(default_factory=list)
  dividend_factor_fingerprint: Optional[str] = None
  data_quality: str = "OK"
  risk_tags: List[str] = field(default_factory=list)
  source_summary: Dict[str, Any] = field(default_factory=dict)

  def to_dict(self) -> Dict[str, Any]:
    return {
      "instrument_code": self.instrument_code,
      "trading_date": self.trading_date.isoformat() if self.trading_date else None,
      "exchange": self.exchange,
      "is_trading_day": self.is_trading_day,
      "suspended": self.suspended,
      "is_st": self.is_st,
      "delist_risk": self.delist_risk,
      "limit_up": self.limit_up,
      "limit_down": self.limit_down,
      "min_buy_volume": self.min_buy_volume,
      "min_sell_volume": self.min_sell_volume,
      "max_buy_volume": self.max_buy_volume,
      "max_sell_volume": self.max_sell_volume,
      "industry": self.industry,
      "concepts": list(self.concepts),
      "dividend_factor_fingerprint": self.dividend_factor_fingerprint,
      "data_quality": self.data_quality,
      "risk_tags": sorted(set(self.risk_tags)),
      "source_summary": dict(self.source_summary),
    }


class InstrumentMaster:
  """Build a conservative instrument master view from existing data sources."""

  def build_snapshot(
    self,
    *,
    instrument_code: str,
    trading_date: Optional[date] = None,
    instrument: Any = None,
    market_data: Any = None,
    calendar: Optional[Dict[str, Any]] = None,
    sector: Optional[Dict[str, Any]] = None,
    concepts: Optional[List[str]] = None,
    dividend_factors: Optional[List[Dict[str, Any]]] = None,
  ) -> InstrumentMasterSnapshot:
    code = str(instrument_code or _get(instrument, "instrument_code", "") or "")
    exchange = _exchange_from_code(code) or str(_get(instrument, "exchange", "") or "")
    suspended = bool(
      _get(market_data, "suspended", False)
      or int(_get(market_data, "suspend_flag", 0) or 0) == 1
      or int(_get(market_data, "stock_status", 0) or 0) == 1
      or _get(instrument, "suspended", False)
    )
    is_st = bool(_get(instrument, "is_st", False) or _get(market_data, "is_st", False))
    delist_risk = bool(
      _get(instrument, "delist_risk", False)
      or _get(instrument, "is_delisting", False)
      or _get(market_data, "delist_risk", False)
    )
    is_trading_day = bool((calendar or {}).get("is_trading_day", True))
    limit_up = _optional_float(_get(market_data, "limit_up", _get(instrument, "limit_up")))
    limit_down = _optional_float(
      _get(market_data, "limit_down", _get(instrument, "limit_down"))
    )

    risk_tags: List[str] = []
    if not is_trading_day:
      risk_tags.append("not_trading_day")
    if suspended:
      risk_tags.append("suspended")
    if is_st:
      risk_tags.append("st_stock")
    if delist_risk:
      risk_tags.append("delist_risk")
    if limit_up is None or limit_down is None:
      risk_tags.append("missing_limit_price")

    data_quality = "OK"
    if not code or not exchange:
      data_quality = "MISSING"
      risk_tags.append("missing_instrument_identity")
    elif limit_up is None or limit_down is None:
      data_quality = "INSUFFICIENT"

    return InstrumentMasterSnapshot(
      instrument_code=code,
      trading_date=trading_date,
      exchange=exchange,
      is_trading_day=is_trading_day,
      suspended=suspended,
      is_st=is_st,
      delist_risk=delist_risk,
      limit_up=limit_up,
      limit_down=limit_down,
      min_buy_volume=int(_get(instrument, "min_buy_volume", 100) or 100),
      min_sell_volume=int(_get(instrument, "min_sell_volume", 1) or 1),
      max_buy_volume=_optional_int(_get(instrument, "max_buy_volume")),
      max_sell_volume=_optional_int(_get(instrument, "max_sell_volume")),
      industry=(sector or {}).get("industry") or _get(instrument, "industry"),
      concepts=list(concepts or (sector or {}).get("concepts", []) or []),
      dividend_factor_fingerprint=_factor_fingerprint(dividend_factors or []),
      data_quality=data_quality,
      risk_tags=risk_tags,
      source_summary={
        "has_instrument": instrument is not None,
        "has_market_data": market_data is not None,
        "has_calendar": calendar is not None,
        "has_sector": sector is not None,
        "dividend_factor_count": len(dividend_factors or []),
      },
    )


def _get(source: Any, key: str, default: Any = None) -> Any:
  if source is None:
    return default
  if isinstance(source, dict):
    return source.get(key, default)
  return getattr(source, key, default)


def _exchange_from_code(code: str) -> str:
  text = str(code or "").upper()
  if text.endswith(".SH"):
    return "SH"
  if text.endswith(".SZ"):
    return "SZ"
  if text.startswith("6"):
    return "SH"
  if text.startswith(("0", "3")):
    return "SZ"
  return ""


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


def _factor_fingerprint(factors: List[Dict[str, Any]]) -> Optional[str]:
  if not factors:
    return None
  payload = "|".join(str(sorted(item.items())) for item in factors)
  return hashlib.sha1(payload.encode("utf-8")).hexdigest()
