from datetime import datetime, timedelta, timezone

from quantx_domain.clock import to_naive_utc


def test_to_naive_utc_preserves_naive_utc_value() -> None:
  value = datetime(2026, 7, 28, 10, 30)

  assert to_naive_utc(value) == value
  assert to_naive_utc(value).tzinfo is None


def test_to_naive_utc_converts_aware_value_to_utc() -> None:
  value = datetime(
    2026,
    7,
    28,
    18,
    30,
    tzinfo=timezone(timedelta(hours=8)),
  )

  assert to_naive_utc(value) == datetime(2026, 7, 28, 10, 30)
  assert to_naive_utc(value).tzinfo is None
