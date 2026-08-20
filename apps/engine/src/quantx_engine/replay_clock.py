"""Deterministic, runtime-local clock for historical execution replays."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class ReplayClock:
  """A monotonic clock advanced only by replay events.

  The clock is deliberately owned by one strategy runtime.  It must never
  monkeypatch the process-wide wall clock because paper/live runs can execute
  in the same Engine process.
  """

  _current: datetime

  @property
  def current(self) -> datetime:
    return self._current

  def now(self) -> datetime:
    return self._current

  def now_ms(self) -> int:
    return int(self._current.timestamp() * 1000)

  def advance_to(self, timestamp: datetime) -> datetime:
    if not isinstance(timestamp, datetime):
      raise TypeError("ReplayClock timestamp must be a datetime")
    try:
      moved_backwards = timestamp < self._current
    except TypeError as exc:
      raise ValueError(
        "ReplayClock cannot mix timezone-aware and timezone-naive timestamps"
      ) from exc
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
