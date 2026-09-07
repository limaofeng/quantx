from copy import deepcopy
from datetime import UTC, datetime

import pytest
from quantx_application.t_trade_v3.portfolio_reference import TPortfolioReference

NOW = datetime(2026, 9, 7, 2, tzinfo=UTC)


def policy_payload():
  return {
    "portfolio_policy": {
      "version": "portfolio-v1",
      "max_total_t_amount": "20000",
      "max_total_asset_fraction": "0.2",
      "cash_buffer": "1000",
      "max_industry_t_amount": "15000",
      "max_concurrent_batches": 3,
      "max_daily_loss": "500",
      "mark_max_age_seconds": 5,
      "industry_classification": {
        "version": "industry-2026-09-01",
        "as_of": "2026-09-01T00:00:00+08:00",
        "effective_from": "2026-09-01T00:00:00+08:00",
        "mappings": {"600000.SH": "bank", "000001.SZ": "bank"},
      },
      "trading_calendar": {
        "version": "exchange-calendar-2026",
        "as_of": "2025-12-30T00:00:00+08:00",
        "valid_from": "2026-09-03",
        "valid_through": "2026-09-08",
        "trading_dates": ["2026-09-03", "2026-09-04", "2026-09-07", "2026-09-08"],
        "complete": True,
      },
    },
    "t_trading_envelope_policy": {
      "version": "envelope-v1",
      "protected_core_volume": 200,
      "max_symbol_t_amount": "10000",
      "max_entry_volume": 1000,
    },
  }


def test_explicit_calendar_crosses_weekend_and_frozen_industry_is_complete():
  payload = policy_payload()
  reference = TPortfolioReference.from_config(
    payload, as_of=NOW, required_codes=("600000.SH", "000001.SZ")
  )
  assert reference.previous_trading_day(NOW).isoformat() == "2026-09-04"
  assert reference.portfolio_policy.max_total_asset_fraction.as_tuple().exponent == -1
  payload["portfolio_policy"]["industry_classification"]["mappings"]["600000.SH"] = (
    "changed"
  )
  assert dict(reference.industries)["600000.SH"] == "bank"


@pytest.mark.parametrize(
  "target,reason",
  [
    ("industry", "FUTURE_INDUSTRY"),
    ("calendar", "FUTURE_CALENDAR"),
    ("missing", "MAPPING_INCOMPLETE"),
    ("duplicate", "DUPLICATE_INDUSTRY"),
    ("empty_policy", "FROZEN_POLICY_INCOMPLETE"),
    ("incomplete_calendar", "CALENDAR_INCOMPLETE"),
  ],
)
def test_missing_or_future_provenance_never_becomes_permissive(target, reason):
  payload = deepcopy(policy_payload())
  policy = payload["portfolio_policy"]
  if target == "industry":
    policy["industry_classification"]["as_of"] = "2026-09-08T00:00:00+08:00"
  elif target == "calendar":
    policy["trading_calendar"]["as_of"] = "2026-09-08T00:00:00+08:00"
  elif target == "missing":
    del policy["industry_classification"]["mappings"]["000001.SZ"]
  elif target == "duplicate":
    policy["industry_classification"]["mappings"]["600000.sh"] = "other"
  elif target == "empty_policy":
    payload["portfolio_policy"] = {}
  else:
    policy["trading_calendar"]["complete"] = False
  with pytest.raises(ValueError, match=reason):
    TPortfolioReference.from_config(
      payload, as_of=NOW, required_codes=("600000.SH", "000001.SZ")
    )
