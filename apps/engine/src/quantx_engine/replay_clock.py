"""Deterministic, runtime-local clock for historical execution replays."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass
class ReplayClock:
  """A monotonic clock advanced only by replay events.

  The clock is deliberately owned by one strategy runtime.  It must never
  monkeypatch the process-wide wall clock because paper/live runs can execute
  in the same Engine process.
  """

  _current: datetime

  def __post_init__(self) -> None:
    self._current = self._normalize_timestamp(self._current)

  @staticmethod
  def _normalize_timestamp(timestamp: datetime) -> datetime:
    """Return the replay timeline's exchange-local naive representation.

    Persisted strategy windows use QuantX's naive Asia/Shanghai convention,
    while InfluxDB market events are returned as timezone-aware timestamps.
    Both describe the same exchange-local timeline and are normalized at this
    runtime-local boundary before monotonic comparisons.
    """

    if not isinstance(timestamp, datetime):
      raise TypeError("ReplayClock timestamp must be a datetime")
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
      return timestamp
    return timestamp.astimezone(_SHANGHAI).replace(tzinfo=None)

  @property
  def current(self) -> datetime:
    return self._current

  def now(self) -> datetime:
    return self._current

  def now_ms(self) -> int:
    return int(self._current.replace(tzinfo=_SHANGHAI).timestamp() * 1000)

  def advance_to(self, timestamp: datetime) -> datetime:
    timestamp = self._normalize_timestamp(timestamp)
    moved_backwards = timestamp < self._current
    if moved_backwards:
      raise ValueError(
        f"ReplayClock cannot move backwards: {timestamp!s} < {self._current!s}"
      )
    self._current = timestamp
    return self._current

  def advance_by(self, delta: timedelta) -> datetime:
    if delta.total_seconds() < 0:
      raise ValueError("ReplayClock delta must be non-negative")
    return self.advance_to(self._current + delta)
