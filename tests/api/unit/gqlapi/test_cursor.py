from datetime import date, datetime, timezone

from quantx_api.gqlapi.utils.cursor import (
  decode_date_cursor,
  decode_datetime_cursor,
  encode_cursor,
)


def test_date_cursor_round_trip_keeps_stable_tie_breaker():
  cursor = encode_cursor(date(2026, 7, 28), "snapshot-42")

  cursor_value, row_id = decode_date_cursor(cursor)

  assert cursor_value == date(2026, 7, 28)
  assert row_id == "snapshot-42"


def test_datetime_cursor_round_trip_keeps_timezone_and_tie_breaker():
  value = datetime(2026, 7, 28, 4, 32, tzinfo=timezone.utc)
  cursor = encode_cursor(value, "event-42")

  cursor_value, row_id = decode_datetime_cursor(cursor)

  assert cursor_value == value
  assert row_id == "event-42"
