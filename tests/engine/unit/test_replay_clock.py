from datetime import datetime, timedelta, timezone

import pytest
from quantx_engine.replay_clock import ReplayClock


def test_replay_clock_advances_monotonically() -> None:
  started_at = datetime(2024, 1, 2, 9, 30)
  clock = ReplayClock(started_at)

  assert clock.now() == started_at
  assert clock.advance_by(timedelta(milliseconds=500)) == datetime(
    2024, 1, 2, 9, 30, 0, 500_000
  )
  assert clock.advance_to(clock.now()) == clock.now()

  with pytest.raises(ValueError, match="cannot move backwards"):
    clock.advance_to(started_at)


def test_replay_clock_rejects_mixed_timezone_semantics() -> None:
  clock = ReplayClock(datetime(2024, 1, 2, 9, 30))

  with pytest.raises(ValueError, match="timezone-aware"):
    clock.advance_to(datetime(2024, 1, 2, 9, 31, tzinfo=timezone.utc))
