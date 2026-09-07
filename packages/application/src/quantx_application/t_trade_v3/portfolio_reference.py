"""Explicit point-in-time policy and industry evidence for portfolio planning."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Mapping
from zoneinfo import ZoneInfo

from quantx_application.t_trade_v3.portfolio_snapshot import (
  TPortfolioPolicy,
  TTradingEnvelopePolicy,
)


def aware_time(value) -> datetime:
  try:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
  except ValueError as exc:
    raise ValueError("T_PORTFOLIO_EVIDENCE_TIME_INVALID") from exc
  if (
    not isinstance(parsed, datetime)
    or parsed.tzinfo is None
    or parsed.utcoffset() is None
  ):
    raise ValueError("T_PORTFOLIO_AWARE_TIME_REQUIRED")
  return parsed


def _date(value):
  if not isinstance(value, str):
    raise ValueError("T_PORTFOLIO_CALENDAR_DATE_INVALID")
  try:
    return date.fromisoformat(value)
  except ValueError as exc:
    raise ValueError("T_PORTFOLIO_CALENDAR_DATE_INVALID") from exc


def _integer(value):
  if type(value) is not int or value < 0:
    raise ValueError("T_PORTFOLIO_POLICY_INTEGER_REQUIRED")
  return value


def _decimal(value):
  if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
    raise ValueError("T_PORTFOLIO_POLICY_AMOUNT_REQUIRED")
  try:
    result = Decimal(str(value))
  except InvalidOperation as exc:
    raise ValueError("T_PORTFOLIO_POLICY_AMOUNT_REQUIRED") from exc
  if not result.is_finite() or result < 0:
    raise ValueError("T_PORTFOLIO_POLICY_AMOUNT_REQUIRED")
  return result


@dataclass(frozen=True)
class TPortfolioReference:
  portfolio_policy: TPortfolioPolicy
  envelope_policy: TTradingEnvelopePolicy
  industry_version: str
  industry_as_of: datetime
  industry_effective_from: datetime
  industries: tuple[tuple[str, str], ...]
  mark_max_age_seconds: int
  calendar_version: str
  calendar_as_of: datetime
  calendar_valid_from: date
  calendar_valid_through: date
  trading_dates: tuple[date, ...]

  def previous_trading_day(self, as_of: datetime) -> date:
    as_of = aware_time(as_of)
    day = as_of.astimezone(ZoneInfo("Asia/Shanghai")).date()
    if self.calendar_as_of > as_of:
      raise ValueError("T_PORTFOLIO_FUTURE_CALENDAR")
    if (
      not self.calendar_valid_from <= day <= self.calendar_valid_through
      or day not in self.trading_dates
    ):
      raise ValueError("T_PORTFOLIO_TRADING_DAY_UNAVAILABLE")
    previous = [value for value in self.trading_dates if value < day]
    if not previous:
      raise ValueError("T_PORTFOLIO_PREVIOUS_TRADING_DAY_UNAVAILABLE")
    return previous[-1]

  @classmethod
  def from_config(cls, payload: Mapping, *, as_of: datetime, required_codes):
    """No inferred industries or permissive policy defaults at this boundary."""
    as_of = aware_time(as_of)
    try:
      raw = payload["portfolio_policy"]
      envelope = payload["t_trading_envelope_policy"]
      classification = raw["industry_classification"]
      mappings = classification["mappings"]
      industry_as_of = aware_time(classification["as_of"])
      effective_from = aware_time(classification["effective_from"])
      version = classification["version"]
      calendar = raw["trading_calendar"]
      calendar_as_of = aware_time(calendar["as_of"])
      calendar_start = _date(calendar["valid_from"])
      calendar_end = _date(calendar["valid_through"])
      calendar_dates = tuple(
        _date(value) for value in calendar["trading_dates"]
      )
      calendar_version = calendar["version"]
      calendar_complete = calendar["complete"]
      age = _integer(raw["mark_max_age_seconds"])
      policy = TPortfolioPolicy(
        raw["version"],
        _decimal(raw["max_total_t_amount"]),
        _decimal(raw["max_total_asset_fraction"]),
        _decimal(raw["cash_buffer"]),
        _decimal(raw["max_industry_t_amount"]),
        _integer(raw["max_concurrent_batches"]),
        _decimal(raw["max_daily_loss"]),
      )
      envelope_policy = TTradingEnvelopePolicy(
        envelope["version"],
        _integer(envelope["protected_core_volume"]),
        _decimal(envelope["max_symbol_t_amount"]),
        _integer(envelope["max_entry_volume"]),
      )
    except (KeyError, TypeError) as exc:
      raise ValueError("T_PORTFOLIO_FROZEN_POLICY_INCOMPLETE") from exc
    if not isinstance(version, str) or not version.strip() or age <= 0:
      raise ValueError("T_PORTFOLIO_REFERENCE_IDENTITY_REQUIRED")
    if max(industry_as_of, effective_from) > as_of:
      raise ValueError("T_PORTFOLIO_FUTURE_INDUSTRY_EVIDENCE")
    if calendar_as_of > as_of:
      raise ValueError("T_PORTFOLIO_FUTURE_CALENDAR")
    if (
      calendar_complete is not True
      or not isinstance(calendar_version, str)
      or not calendar_version.strip()
      or calendar_end < calendar_start
      or not calendar_dates
      or len(set(calendar_dates)) != len(calendar_dates)
      or any(not calendar_start <= value <= calendar_end for value in calendar_dates)
    ):
      raise ValueError("T_PORTFOLIO_CALENDAR_INCOMPLETE")
    if not isinstance(mappings, Mapping):
      raise ValueError("T_PORTFOLIO_INDUSTRY_MAPPING_REQUIRED")
    normalized = {}
    for code, industry in mappings.items():
      if (
        not isinstance(code, str)
        or not isinstance(industry, str)
        or not industry.strip()
      ):
        raise ValueError("T_PORTFOLIO_INDUSTRY_MAPPING_INVALID")
      key = code.strip().upper()
      if not re.fullmatch(r"[0-9]{6}\.(SH|SZ|BJ)", key):
        raise ValueError("T_PORTFOLIO_INDUSTRY_SYMBOL_INVALID")
      if key in normalized:
        raise ValueError("T_PORTFOLIO_DUPLICATE_INDUSTRY_SYMBOL")
      normalized[key] = industry.strip()
    if any(code not in normalized for code in required_codes):
      raise ValueError("T_PORTFOLIO_INDUSTRY_MAPPING_INCOMPLETE")
    return cls(
      policy,
      envelope_policy,
      version,
      industry_as_of,
      effective_from,
      tuple(sorted(normalized.items())),
      age,
      calendar_version,
      calendar_as_of,
      calendar_start,
      calendar_end,
      tuple(sorted(calendar_dates)),
    )
