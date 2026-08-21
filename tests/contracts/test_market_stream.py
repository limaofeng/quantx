from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from quantx_contracts import (
  MarketBatchKind,
  MarketStreamBatch,
  market_tick_source_time,
  validate_market_stream_capture_time,
)


def test_market_stream_batch_binary_round_trip() -> None:
  batch = MarketStreamBatch(
    stream_id="stream-1",
    sequence=1,
    kind=MarketBatchKind.SNAPSHOT,
    captured_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
    instrument_count=1,
    universe_codes=("600000.SH",),
    data={"600000.SH": {"lastPrice": 10.5, "time": 1_777_000_000_000}},
  )

  restored = MarketStreamBatch.from_bytes(batch.to_bytes())

  assert restored == batch


def test_market_stream_batch_rejects_count_mismatch_and_naive_time() -> None:
  with pytest.raises(ValidationError, match="instrument_count"):
    MarketStreamBatch(
      stream_id="stream-1",
      sequence=1,
      kind=MarketBatchKind.SNAPSHOT,
      captured_at=datetime.now(timezone.utc),
      instrument_count=2,
      universe_codes=("600000.SH",),
      data={"600000.SH": {}},
    )

  with pytest.raises(ValidationError, match="timezone-aware"):
    MarketStreamBatch(
      stream_id="stream-1",
      sequence=1,
      kind=MarketBatchKind.SNAPSHOT,
      captured_at=datetime(2026, 8, 18),
      instrument_count=0,
      data={},
    )


def test_market_stream_batch_requires_positive_sequence() -> None:
  with pytest.raises(ValidationError, match="greater than 0"):
    MarketStreamBatch(
      stream_id="stream-1",
      sequence=0,
      kind=MarketBatchKind.DELTA,
      captured_at=datetime.now(timezone.utc),
      instrument_count=0,
      data={},
    )


def test_market_stream_snapshot_cannot_be_empty() -> None:
  with pytest.raises(ValidationError, match="SNAPSHOT cannot be empty"):
    MarketStreamBatch(
      stream_id="stream-1",
      sequence=1,
      kind=MarketBatchKind.SNAPSHOT,
      captured_at=datetime.now(timezone.utc),
      instrument_count=0,
      data={},
    )


def test_market_stream_snapshot_can_materialize_part_of_universe() -> None:
  batch = MarketStreamBatch(
    stream_id="stream-1",
    sequence=1,
    kind=MarketBatchKind.SNAPSHOT,
    captured_at=datetime.now(timezone.utc),
    instrument_count=1,
    universe_codes=("000001.SH", "600000.SH"),
    data={"000001.SH": {"lastPrice": 3500.0}},
  )

  assert batch.universe_codes == ("000001.SH", "600000.SH")


def test_market_tick_source_time_preserves_timetag_subseconds() -> None:
  later = market_tick_source_time({"timetag": "20260819 09:30:00.900"})
  earlier = market_tick_source_time({"timetag": "20260819 09:30:00.100"})

  assert later - earlier == pytest.approx(0.8)


@pytest.mark.parametrize(
  "future_time",
  [
    1_800_000_006,
    1_800_000_006_000,
  ],
)
def test_market_tick_source_time_rejects_future_values(
  future_time: int,
) -> None:
  reference_at = datetime.fromtimestamp(1_800_000_000, timezone.utc)

  with pytest.raises(ValueError, match="in the future"):
    market_tick_source_time(
      {"time": future_time},
      reference_at=reference_at,
    )


def test_market_tick_source_time_rejects_microsecond_units() -> None:
  with pytest.raises(ValueError, match="seconds or milliseconds"):
    market_tick_source_time({"time": 1_800_000_000_000_000})


def test_market_stream_capture_time_rejects_stale_and_future_values() -> None:
  received_at = datetime(2026, 8, 19, 9, 30, tzinfo=timezone.utc)

  with pytest.raises(ValueError, match="stale"):
    validate_market_stream_capture_time(
      received_at - timedelta(seconds=11),
      received_at=received_at,
    )
  with pytest.raises(ValueError, match="future"):
    validate_market_stream_capture_time(
      received_at + timedelta(seconds=6),
      received_at=received_at,
    )
  assert validate_market_stream_capture_time(
    received_at - timedelta(days=1),
    received_at=received_at,
    max_age_seconds=None,
  ) == pytest.approx(24 * 60 * 60)
