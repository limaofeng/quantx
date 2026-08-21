from datetime import date

import pytest
from quantx_domain.trading.market_rules import resolve_ashare_daily_limit_rate

pytestmark = pytest.mark.unit


MATURE_LIFECYCLE = {
  "listing_date": date(2010, 1, 1),
  "expiry_date": date(2038, 1, 1),
}


@pytest.mark.parametrize(
  ("instrument_code", "expected_rate"),
  [
    ("600000.SH", 0.10),
    ("605499.SH", 0.10),
    ("000001.SZ", 0.10),
    ("002594.SZ", 0.10),
    ("300917.SZ", 0.20),
    ("301001.SZ", 0.20),
    ("302132.SZ", 0.20),
    ("688552.SH", 0.20),
    ("689009.SH", 0.20),
    ("920001.BJ", 0.30),
  ],
)
def test_resolves_mature_stock_limit_rate_by_exchange_board(
  instrument_code: str,
  expected_rate: float,
) -> None:
  rate = resolve_ashare_daily_limit_rate(
    instrument_code,
    date(2026, 8, 19),
    **MATURE_LIFECYCLE,
  )

  assert rate == pytest.approx(expected_rate)


def test_main_board_st_cutover_is_date_aware() -> None:
  before = resolve_ashare_daily_limit_rate(
    "600000.SH",
    date(2026, 7, 5),
    instrument_name="*ST测试",
    status_as_of_date=date(2026, 7, 5),
    **MATURE_LIFECYCLE,
  )
  after = resolve_ashare_daily_limit_rate(
    "600000.SH",
    date(2026, 7, 6),
    instrument_name="*ST测试",
    **MATURE_LIFECYCLE,
  )

  assert before == pytest.approx(0.05)
  assert after == pytest.approx(0.10)


def test_chinext_twenty_percent_cutover_is_date_aware() -> None:
  before = resolve_ashare_daily_limit_rate(
    "300001.SZ",
    date(2020, 8, 23),
    instrument_name="特锐德",
    status_as_of_date=date(2020, 8, 23),
    **MATURE_LIFECYCLE,
  )
  after = resolve_ashare_daily_limit_rate(
    "300001.SZ",
    date(2020, 8, 24),
    instrument_name="特锐德",
    **MATURE_LIFECYCLE,
  )

  assert before == pytest.approx(0.10)
  assert after == pytest.approx(0.20)


def test_beijing_exchange_opening_date_is_enforced() -> None:
  before = resolve_ashare_daily_limit_rate(
    "920001.BJ",
    date(2021, 11, 14),
    **MATURE_LIFECYCLE,
  )
  after = resolve_ashare_daily_limit_rate(
    "920001.BJ",
    date(2021, 11, 15),
    **MATURE_LIFECYCLE,
  )

  assert before is None
  assert after == pytest.approx(0.30)


def test_historical_main_board_unknown_st_status_fails_closed() -> None:
  assert (
    resolve_ashare_daily_limit_rate(
      "000001.SZ",
      date(2026, 7, 5),
      instrument_name="",
      **MATURE_LIFECYCLE,
    )
    is None
  )


def test_current_instrument_name_cannot_backfill_historical_st_status() -> None:
  assert (
    resolve_ashare_daily_limit_rate(
      "600000.SH",
      date(2026, 7, 5),
      instrument_name="*ST测试",
      status_as_of_date=date(2026, 8, 19),
      **MATURE_LIFECYCLE,
    )
    is None
  )


@pytest.mark.parametrize(
  "kwargs",
  [
    {"listing_date": None, "expiry_date": date(2038, 1, 1)},
    {"listing_date": date(2026, 8, 1), "expiry_date": date(2038, 1, 1)},
    {"listing_date": date(2010, 1, 1), "expiry_date": None},
    {"listing_date": date(2010, 1, 1), "expiry_date": date(2026, 8, 25)},
  ],
)
def test_missing_or_special_lifecycle_evidence_fails_closed(kwargs) -> None:
  assert (
    resolve_ashare_daily_limit_rate(
      "600000.SH",
      date(2026, 8, 19),
      instrument_name="浦发银行",
      **kwargs,
    )
    is None
  )


@pytest.mark.parametrize("instrument_name", ["N测试", "C测试", "测试退"])
def test_no_limit_or_delisting_name_marker_fails_closed(
  instrument_name: str,
) -> None:
  assert (
    resolve_ashare_daily_limit_rate(
      "600000.SH",
      date(2026, 8, 19),
      instrument_name=instrument_name,
      **MATURE_LIFECYCLE,
    )
    is None
  )


def test_non_stock_code_is_not_assigned_a_stock_limit_rate() -> None:
  assert (
    resolve_ashare_daily_limit_rate(
      "787825.SH",
      date(2026, 8, 19),
      **MATURE_LIFECYCLE,
    )
    is None
  )
