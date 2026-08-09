"""Helpers for the run-level portfolio state dictionary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, Optional


@dataclass
class PortfolioPosition:
  instrument_code: str
  long_volume: int = 0
  available_volume: int = 0
  frozen_volume: int = 0
  today_buy_volume: int = 0
  long_avg_price: float = 0.0
  last_price: float = 0.0
  market_value: float = 0.0
  pnl: float = 0.0
  last_settlement_date: Optional[str] = None

  def to_dict(self) -> Dict[str, Any]:
    return {
      "instrument_code": self.instrument_code,
      "long_volume": self.long_volume,
      "short_volume": 0,
      "available_volume": self.available_volume,
      "frozen_volume": self.frozen_volume,
      "today_buy_volume": self.today_buy_volume,
      "long_avg_price": self.long_avg_price,
      "short_avg_price": 0.0,
      "last_price": self.last_price,
      "market_value": self.market_value,
      "pnl": self.pnl,
      "last_settlement_date": self.last_settlement_date,
    }


def ensure_position_dict(
  instrument_code: str, position: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
  data = dict(position or {})
  long_volume = int(data.get("long_volume", data.get("volume", 0)) or 0)
  frozen_volume = int(data.get("frozen_volume", 0) or 0)
  available_volume = data.get("available_volume")
  if available_volume is None:
    available_volume = max(0, long_volume - frozen_volume)

  data.update(
    {
      "instrument_code": instrument_code,
      "long_volume": long_volume,
      "short_volume": int(data.get("short_volume", 0) or 0),
      "available_volume": max(0, int(available_volume or 0)),
      "frozen_volume": max(0, frozen_volume),
      "today_buy_volume": max(0, int(data.get("today_buy_volume", 0) or 0)),
      "long_avg_price": float(data.get("long_avg_price", 0.0) or 0.0),
      "short_avg_price": float(data.get("short_avg_price", 0.0) or 0.0),
      "last_price": float(data.get("last_price", 0.0) or 0.0),
      "market_value": float(data.get("market_value", 0.0) or 0.0),
      "pnl": float(data.get("pnl", 0.0) or 0.0),
    }
  )
  return data


def settle_position(position: Dict[str, Any], trading_date: date) -> Dict[str, Any]:
  data = ensure_position_dict(str(position.get("instrument_code", "")), position)
  date_key = trading_date.isoformat()
  if data.get("last_settlement_date") == date_key:
    return data

  long_volume = int(data.get("long_volume", 0) or 0)
  frozen_volume = int(data.get("frozen_volume", 0) or 0)
  data["available_volume"] = max(0, long_volume - frozen_volume)
  data["today_buy_volume"] = 0
  data["last_settlement_date"] = date_key
  return data
