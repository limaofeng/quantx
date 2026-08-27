"""Common probe helpers with bounded, non-sensitive failures."""

from __future__ import annotations

import asyncio
import ssl
from time import perf_counter
from typing import Awaitable, Callable, TypeVar

import httpx

from ..models import MonitorStatus, ProbeResult, utc_now

T = TypeVar("T")


def failure_reason(exc: BaseException) -> str:
  if isinstance(exc, (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException)):
    return "TIMEOUT"
  if isinstance(exc, ssl.SSLError):
    return "TLS_ERROR"
  if isinstance(exc, (ConnectionError, httpx.ConnectError, OSError)):
    return "CONNECT_ERROR"
  return "PROTOCOL_ERROR"


async def timed_result(
  target_id: str,
  action: Callable[[], Awaitable[T]],
  evaluate: Callable[[T], tuple[MonitorStatus, int | None, str | None]],
) -> ProbeResult:
  checked_at = utc_now()
  started = perf_counter()
  try:
    value = await action()
    status, status_code, reason_code = evaluate(value)
  except Exception as exc:
    status = MonitorStatus.UNAVAILABLE
    status_code = None
    reason_code = failure_reason(exc)
  latency_ms = (perf_counter() - started) * 1000
  return ProbeResult(
    target_id=target_id,
    checked_at=checked_at,
    observed_status=status,
    latency_ms=latency_ms,
    status_code=status_code,
    reason_code=reason_code,
  )
