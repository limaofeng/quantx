"""Small per-process login failure limiter keyed by a non-reversible fingerprint."""

import asyncio
import time
from collections import defaultdict, deque
from typing import DefaultDict, Deque


class LoginRateLimiter:
  def __init__(self):
    self._failures: DefaultDict[str, Deque[float]] = defaultdict(deque)
    self._lock = asyncio.Lock()

  async def is_limited(self, key: str, attempts: int, window_seconds: int) -> bool:
    async with self._lock:
      bucket = self._failures[key]
      self._prune(bucket, window_seconds)
      return len(bucket) >= max(1, attempts)

  async def record_failure(self, key: str, window_seconds: int) -> None:
    async with self._lock:
      bucket = self._failures[key]
      self._prune(bucket, window_seconds)
      bucket.append(time.monotonic())

  async def clear(self, key: str) -> None:
    async with self._lock:
      self._failures.pop(key, None)

  @staticmethod
  def _prune(bucket: Deque[float], window_seconds: int) -> None:
    cutoff = time.monotonic() - max(1, window_seconds)
    while bucket and bucket[0] <= cutoff:
      bucket.popleft()


login_rate_limiter = LoginRateLimiter()
