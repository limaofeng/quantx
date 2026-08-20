from datetime import datetime, timedelta, timezone

from quantx_api.gqlapi.resolvers.t_trade import TTradeResolver


def test_operational_readiness_naive_datetimes_are_explicit_utc():
  payload = TTradeResolver._with_utc_datetimes(
    {"last_backup_at": datetime(2026, 8, 17, 18, 54, 5)},
    "last_backup_at",
  )

  assert payload["last_backup_at"].tzinfo is timezone.utc
  assert payload["last_backup_at"].utcoffset() == timedelta(0)


def test_operational_readiness_preserves_aware_datetime_offsets():
  shanghai = timezone(timedelta(hours=8))
  payload = TTradeResolver._with_utc_datetimes(
    {"checked_at": "2026-08-20T09:30:00+08:00"},
    "checked_at",
  )

  assert payload["checked_at"].utcoffset() == shanghai.utcoffset(None)
