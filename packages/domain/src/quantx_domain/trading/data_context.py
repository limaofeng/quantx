"""A-share data context assembly before strategy decisions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from .environment import EnvironmentLayer
from .instrument_master import InstrumentMaster
from .market_rules import MarketDataSnapshot


@dataclass
class AshareDataContext:
  instrument_code: str
  timestamp: Optional[datetime] = None
  market_context: Dict[str, Any] = field(default_factory=dict)
  instrument_master: Dict[str, Any] = field(default_factory=dict)
  data_quality: str = "OK"
  risk_tags: List[str] = field(default_factory=list)
  source_fingerprint: str = ""

  def to_dict(self) -> Dict[str, Any]:
    return {
      "instrument_code": self.instrument_code,
      "timestamp": self.timestamp.isoformat() if self.timestamp else None,
      "market_context": dict(self.market_context),
      "instrument_master": dict(self.instrument_master),
      "data_quality": self.data_quality,
      "risk_tags": sorted(set(self.risk_tags)),
      "source_fingerprint": self.source_fingerprint,
    }


class AshareDataContextProvider:
  """Build deterministic context from market data, instrument data, and parameters."""

  def __init__(
    self,
    *,
    environment_layer: Optional[EnvironmentLayer] = None,
    instrument_master: Optional[InstrumentMaster] = None,
  ) -> None:
    self.environment_layer = environment_layer or EnvironmentLayer()
    self.instrument_master = instrument_master or InstrumentMaster()

  def build_context(
    self,
    *,
    instrument_code: str,
    timestamp: Optional[datetime] = None,
    market_data: Optional[MarketDataSnapshot] = None,
    event: Optional[Any] = None,
    parameters: Optional[Dict[str, Any]] = None,
    previous_market_context: Optional[Dict[str, Any]] = None,
  ) -> AshareDataContext:
    params = dict(parameters or {})
    code = instrument_code or _get(market_data, "instrument_code", "") or _get(
      event, "stock_code", _get(event, "code", "")
    )
    ts = timestamp or _get(market_data, "timestamp") or _get(event, "timestamp")
    trading_date = _as_date(params.get("trading_date") or _get(event, "trade_date"), ts)

    instrument_snapshot = self.instrument_master.build_snapshot(
      instrument_code=str(code or ""),
      trading_date=trading_date,
      instrument=params.get("instrument") or params.get("instrument_master"),
      market_data=market_data or event,
      calendar=params.get("calendar"),
      sector=params.get("sector") or params.get("sector_context"),
      concepts=params.get("concepts"),
      dividend_factors=params.get("dividend_factors"),
    ).to_dict()

    enriched_parameters = dict(params)
    environment_context = dict(enriched_parameters.get("environment_context") or {})
    market_context = dict(enriched_parameters.get("market_context") or {})
    market_context.setdefault("limit_up", instrument_snapshot.get("limit_up"))
    market_context.setdefault("limit_down", instrument_snapshot.get("limit_down"))
    market_context.setdefault("suspended", instrument_snapshot.get("suspended"))
    market_context.setdefault("is_st", instrument_snapshot.get("is_st"))
    market_context.setdefault("delist_risk", instrument_snapshot.get("delist_risk"))
    market_context.setdefault("industry", instrument_snapshot.get("industry"))
    market_context.setdefault("concepts", instrument_snapshot.get("concepts"))
    enriched_parameters["environment_context"] = environment_context
    enriched_parameters["market_context"] = market_context

    env_snapshot = self.environment_layer.build_snapshot(
      instrument_code=str(code or ""),
      timestamp=ts,
      market_data=market_data,
      event=event,
      parameters=enriched_parameters,
      previous_snapshot=previous_market_context,
    ).to_dict()

    risk_tags = sorted(
      set(list(env_snapshot.get("risk_tags", []) or []) + list(instrument_snapshot.get("risk_tags", []) or []))
    )
    data_quality = _worst_quality(
      env_snapshot.get("data_quality", "OK"),
      instrument_snapshot.get("data_quality", "OK"),
    )
    env_snapshot["instrument_master"] = instrument_snapshot
    env_snapshot["risk_tags"] = risk_tags
    env_snapshot["data_quality"] = data_quality
    env_snapshot["source_fingerprint"] = _fingerprint(
      {
        "environment": env_snapshot.get("source_fingerprint"),
        "instrument": instrument_snapshot.get("source_summary"),
        "risk_tags": risk_tags,
        "data_quality": data_quality,
      }
    )

    return AshareDataContext(
      instrument_code=str(code or ""),
      timestamp=ts,
      market_context=env_snapshot,
      instrument_master=instrument_snapshot,
      data_quality=data_quality,
      risk_tags=risk_tags,
      source_fingerprint=env_snapshot["source_fingerprint"],
    )


def _get(source: Any, key: str, default: Any = None) -> Any:
  if source is None:
    return default
  if isinstance(source, dict):
    return source.get(key, default)
  return getattr(source, key, default)


def _as_date(value: Any, timestamp: Optional[datetime]) -> Optional[date]:
  if isinstance(value, date) and not isinstance(value, datetime):
    return value
  if isinstance(value, datetime):
    return value.date()
  if isinstance(value, str) and value:
    try:
      return date.fromisoformat(value[:10])
    except ValueError:
      return None
  if isinstance(timestamp, datetime):
    return timestamp.date()
  return None


def _worst_quality(*values: Any) -> str:
  order = {"OK": 0, "DEGRADED": 1, "INSUFFICIENT": 2, "MISSING": 3}
  worst = "OK"
  for value in values:
    text = str(value or "OK").upper()
    if order.get(text, 0) > order.get(worst, 0):
      worst = text
  return worst


def _fingerprint(payload: Dict[str, Any]) -> str:
  text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
  return hashlib.sha1(text.encode("utf-8")).hexdigest()
