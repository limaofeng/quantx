"""Process-wide, bounded whole-market capture state.

The XTData callback owns no network or serialization work.  It only advances a
local capture watermark, converges the latest state, and (while READY) appends
the original callback to a bounded ordered ingress.  A market WebSocket may be
discarded at any time without discarding the native subscription or the latest
state held here.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

SHANGHAI_ZONE = ZoneInfo("Asia/Shanghai")
MIN_CAPTURED_MARKET_EVENT_ESTIMATED_BYTES = 1024


class WholeMarketCaptureInvalidated(RuntimeError):
  """READY delivery lost continuity and requires a full resynchronization."""


class WholeMarketCaptureOverflow(WholeMarketCaptureInvalidated):
  """The READY stream cannot preserve every callback within its ingress cap."""


@dataclass(frozen=True, slots=True)
class CapturedMarketEvent:
  capture_sequence: int
  captured_at: datetime
  captured_monotonic: float
  data: dict[str, Any]
  estimated_bytes: int


@dataclass(frozen=True, slots=True)
class WholeMarketSnapshot:
  capture_watermark: int
  data: dict[str, Any]
  capture_sequences: dict[str, int]
  captured_monotonic: dict[str, float]


@dataclass(frozen=True, slots=True)
class _LatestTick:
  capture_sequence: int
  captured_at: datetime
  captured_monotonic: float
  value: Any


class WholeMarketCapture:
  """Thread-safe latest state plus an ordered READY-only callback ingress."""

  def __init__(
    self,
    *,
    max_ready_callbacks: int,
    max_ready_estimated_bytes: int,
    estimated_tick_bytes: int = 512,
  ) -> None:
    if max_ready_callbacks < 1:
      raise ValueError("max_ready_callbacks must be positive")
    if max_ready_estimated_bytes < 1:
      raise ValueError("max_ready_estimated_bytes must be positive")
    if estimated_tick_bytes < 1:
      raise ValueError("estimated_tick_bytes must be positive")
    self._max_ready_callbacks = max_ready_callbacks
    self._max_ready_estimated_bytes = max_ready_estimated_bytes
    self._estimated_tick_bytes = estimated_tick_bytes
    self._lock = threading.RLock()
    self._latest: dict[str, _LatestTick] = {}
    self._ready_events: deque[CapturedMarketEvent] = deque()
    self._ready_estimated_bytes = 0
    self._capture_sequence = 0
    self._ready = False
    self._invalidation_reason = ""
    self._invalidation_is_overflow = False
    self._loop: asyncio.AbstractEventLoop | None = None
    self._wake: asyncio.Event | None = None
    self._invalidated: asyncio.Event | None = None
    self._callback_count = 0
    self._sync_merged_callbacks = 0
    self._last_callback_monotonic = 0.0

  def _invalidation_exception_locked(
    self,
  ) -> WholeMarketCaptureInvalidated | None:
    if not self._invalidation_reason:
      return None
    exception_type = (
      WholeMarketCaptureOverflow
      if self._invalidation_is_overflow
      else WholeMarketCaptureInvalidated
    )
    return exception_type(self._invalidation_reason)

  def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
    with self._lock:
      self._loop = loop
      self._wake = asyncio.Event()
      self._invalidated = asyncio.Event()
      if self._latest or self._ready_events or self._invalidation_reason:
        self._wake.set()
      if self._invalidation_reason:
        self._invalidated.set()

  def unbind_loop(self) -> None:
    with self._lock:
      self._loop = None
      self._wake = None
      self._invalidated = None

  @staticmethod
  def _copy_tick_value(value: Any) -> Any:
    if isinstance(value, list):
      return list(value)
    if isinstance(value, tuple):
      return tuple(value)
    if isinstance(value, dict):
      return dict(value)
    if isinstance(value, (bytearray, memoryview)):
      return bytes(value)
    module_name = value.__class__.__module__
    copier = getattr(value, "copy", None)
    if module_name.startswith("numpy") and callable(copier):
      return copier()
    return value

  @classmethod
  def _normalize_data(cls, data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
      return {}
    normalized_data: dict[str, Any] = {}
    for code, value in data.items():
      normalized = str(code).strip().upper()
      if not normalized:
        continue
      if isinstance(value, dict):
        normalized_data[normalized] = {
          field: cls._copy_tick_value(field_value)
          for field, field_value in value.items()
        }
      else:
        normalized_data[normalized] = cls._copy_tick_value(value)
    return normalized_data

  def capture(self, data: Any) -> None:
    """Capture one XTData callback without serialization or network I/O."""
    normalized = self._normalize_data(data)
    if not normalized:
      return
    captured_at = datetime.now(timezone.utc)
    captured_monotonic = time.monotonic()
    estimated_bytes = max(
      MIN_CAPTURED_MARKET_EVENT_ESTIMATED_BYTES,
      len(normalized) * self._estimated_tick_bytes,
    )
    should_wake = False
    invalidated = False
    with self._lock:
      self._capture_sequence += 1
      capture_sequence = self._capture_sequence
      self._callback_count += 1
      self._last_callback_monotonic = captured_monotonic
      for code, tick in normalized.items():
        self._latest[code] = _LatestTick(
          capture_sequence=capture_sequence,
          captured_at=captured_at,
          captured_monotonic=captured_monotonic,
          value=tick,
        )
      if not self._ready:
        self._sync_merged_callbacks += 1
        should_wake = True
      elif (
        len(self._ready_events) >= self._max_ready_callbacks
        or self._ready_estimated_bytes + estimated_bytes
        > self._max_ready_estimated_bytes
      ):
        current_depth = len(self._ready_events)
        projected_estimated_bytes = (
          self._ready_estimated_bytes + estimated_bytes
        )
        self._ready = False
        self._ready_events.clear()
        self._ready_estimated_bytes = 0
        self._invalidation_reason = (
          "whole-market READY ingress overflow: "
          f"depth={current_depth} "
          f"projected_estimated_bytes={projected_estimated_bytes} "
          f"max_callbacks={self._max_ready_callbacks} "
          f"max_estimated_bytes={self._max_ready_estimated_bytes}"
        )
        self._invalidation_is_overflow = True
        should_wake = True
        invalidated = True
      else:
        self._ready_events.append(
          CapturedMarketEvent(
            capture_sequence=capture_sequence,
            captured_at=captured_at,
            captured_monotonic=captured_monotonic,
            data=normalized,
            estimated_bytes=estimated_bytes,
          )
        )
        self._ready_estimated_bytes += estimated_bytes
        should_wake = True
    if should_wake:
      self._notify()
    if invalidated:
      self._notify_invalidation()

  def _notify(self) -> None:
    with self._lock:
      loop = self._loop
      wake = self._wake
    if loop is None or wake is None or loop.is_closed():
      return
    try:
      loop.call_soon_threadsafe(wake.set)
    except RuntimeError:
      return

  def _notify_invalidation(self) -> None:
    with self._lock:
      loop = self._loop
      invalidated = self._invalidated
    if loop is None or invalidated is None or loop.is_closed():
      return
    try:
      loop.call_soon_threadsafe(invalidated.set)
    except RuntimeError:
      return

  def begin_syncing(self) -> None:
    """Drop obsolete READY ingress while retaining converged latest state."""
    with self._lock:
      self._ready = False
      self._ready_events.clear()
      self._ready_estimated_bytes = 0
      self._invalidation_reason = ""
      self._invalidation_is_overflow = False
      invalidated = self._invalidated
      if invalidated is not None:
        invalidated.clear()

  def force_resync(self, reason: str) -> None:
    """Invalidate READY delivery while retaining the converged latest state."""
    with self._lock:
      self._ready = False
      self._ready_events.clear()
      self._ready_estimated_bytes = 0
      self._invalidation_reason = str(
        reason or "whole-market capture invalidated"
      )
      self._invalidation_is_overflow = False
    self._notify()
    self._notify_invalidation()

  def reset_source(self, reason: str) -> None:
    """Discard state after confirmed loss of native source continuity."""
    with self._lock:
      self._ready = False
      self._latest.clear()
      self._ready_events.clear()
      self._ready_estimated_bytes = 0
      self._invalidation_reason = str(reason or "whole-market source reset")
      self._invalidation_is_overflow = False
    self._notify()
    self._notify_invalidation()

  def latest_snapshot(self, *, trading_date: date | None) -> WholeMarketSnapshot:
    """Copy a consistent latest-state view and its capture watermark."""
    with self._lock:
      watermark = self._capture_sequence
      if trading_date is None:
        values = {
          code: latest.value for code, latest in self._latest.items()
        }
        sequences = {
          code: latest.capture_sequence
          for code, latest in self._latest.items()
        }
        monotonic_values = {
          code: latest.captured_monotonic
          for code, latest in self._latest.items()
        }
      else:
        included = {
          code: latest
          for code, latest in self._latest.items()
          if latest.captured_at.astimezone(SHANGHAI_ZONE).date()
          == trading_date
        }
        values = {code: latest.value for code, latest in included.items()}
        sequences = {
          code: latest.capture_sequence for code, latest in included.items()
        }
        monotonic_values = {
          code: latest.captured_monotonic
          for code, latest in included.items()
        }
    return WholeMarketSnapshot(
      capture_watermark=watermark,
      data=values,
      capture_sequences=sequences,
      captured_monotonic=monotonic_values,
    )

  def _converged_event_locked(
    self,
    *,
    after_sequence: int,
    trading_date: date | None,
  ) -> CapturedMarketEvent:
    values: dict[str, Any] = {}
    captured_at: datetime | None = None
    captured_monotonic: float | None = None
    for code, latest in self._latest.items():
      if latest.capture_sequence <= after_sequence:
        continue
      if (
        trading_date is not None
        and latest.captured_at.astimezone(SHANGHAI_ZONE).date()
        != trading_date
      ):
        continue
      values[code] = latest.value
      if captured_at is None or latest.captured_at < captured_at:
        captured_at = latest.captured_at
      if (
        captured_monotonic is None
        or latest.captured_monotonic < captured_monotonic
      ):
        captured_monotonic = latest.captured_monotonic
    return CapturedMarketEvent(
      capture_sequence=self._capture_sequence,
      captured_at=captured_at or datetime.now(timezone.utc),
      captured_monotonic=captured_monotonic or time.monotonic(),
      data=values,
      estimated_bytes=max(
        MIN_CAPTURED_MARKET_EVENT_ESTIMATED_BYTES,
        len(values) * self._estimated_tick_bytes,
      ),
    )

  def converged_event(
    self,
    *,
    after_sequence: int,
    trading_date: date | None,
  ) -> CapturedMarketEvent:
    """Read a latest-per-instrument delta while remaining in SYNCING."""
    with self._lock:
      return self._converged_event_locked(
        after_sequence=after_sequence,
        trading_date=trading_date,
      )

  def activate_ready(
    self,
    *,
    after_sequence: int,
    trading_date: date | None,
  ) -> CapturedMarketEvent:
    """Atomically cut from state convergence to ordered callback capture."""
    with self._lock:
      invalidation = self._invalidation_exception_locked()
      if invalidation is not None:
        raise invalidation
      event = self._converged_event_locked(
        after_sequence=after_sequence,
        trading_date=trading_date,
      )
      self._ready_events.clear()
      self._ready_estimated_bytes = 0
      self._ready = True
      invalidated = self._invalidated
      if invalidated is not None:
        invalidated.clear()
      return event

  async def next_ready_event(self) -> CapturedMarketEvent:
    while True:
      with self._lock:
        invalidation = self._invalidation_exception_locked()
        if invalidation is not None:
          raise invalidation
        if self._ready_events:
          event = self._ready_events.popleft()
          self._ready_estimated_bytes = max(
            0,
            self._ready_estimated_bytes - event.estimated_bytes,
          )
          return event
        wake = self._wake
        if wake is None:
          raise RuntimeError("whole-market capture has no bound event loop")
        wake.clear()
      await wake.wait()

  async def wait_for_change(
    self,
    *,
    after_sequence: int,
    timeout: float,
  ) -> None:
    with self._lock:
      if self._capture_sequence > after_sequence:
        return
      wake = self._wake
      if wake is None:
        raise RuntimeError("whole-market capture has no bound event loop")
      wake.clear()
    await asyncio.wait_for(wake.wait(), timeout=timeout)

  async def wait_until_invalidated(self) -> None:
    with self._lock:
      invalidated = self._invalidated
      if invalidated is None:
        raise RuntimeError("whole-market capture has no bound event loop")
    await invalidated.wait()

  def raise_if_invalidated(self) -> None:
    """Raise the original continuity-loss reason without replacing it."""
    with self._lock:
      invalidation = self._invalidation_exception_locked()
    if invalidation is not None:
      raise invalidation

  @property
  def invalidation_reason(self) -> str:
    with self._lock:
      return self._invalidation_reason

  @property
  def queue_depth(self) -> int:
    with self._lock:
      return len(self._ready_events)

  @property
  def queue_estimated_bytes(self) -> int:
    with self._lock:
      return self._ready_estimated_bytes

  @property
  def capture_sequence(self) -> int:
    with self._lock:
      return self._capture_sequence

  def stats(self) -> dict[str, int | float | bool | str]:
    with self._lock:
      return {
        "ready": self._ready,
        "latest_instruments": len(self._latest),
        "queue_depth": len(self._ready_events),
        "queue_estimated_bytes": self._ready_estimated_bytes,
        "capture_sequence": self._capture_sequence,
        "callback_count": self._callback_count,
        "sync_merged_callbacks": self._sync_merged_callbacks,
        "last_callback_monotonic": self._last_callback_monotonic,
        "invalidation_reason": self._invalidation_reason,
        "overflow_reason": (
          self._invalidation_reason
          if self._invalidation_is_overflow
          else ""
        ),
      }
