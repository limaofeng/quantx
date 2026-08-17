from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from quantx_contracts import MarketBatchKind, MarketStreamBatch


def test_market_stream_batch_binary_round_trip() -> None:
  batch = MarketStreamBatch(
    stream_id="stream-1",
    sequence=1,
    kind=MarketBatchKind.SNAPSHOT,
    captured_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
    instrument_count=1,
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
