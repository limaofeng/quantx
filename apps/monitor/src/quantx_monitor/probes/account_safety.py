"""Account-free safety snapshot probe for the local API process."""

from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any

import httpx
from pydantic import ValidationError
from quantx_contracts import (
  ACCOUNT_SAFETY_OBSERVATION_SCHEMA_VERSION,
  AccountSafetyObservationSnapshot,
)

from ..models import AccountSafetyProbeOutcome, MonitorStatus, ProbeResult, utc_now

ACCOUNT_SAFETY_CONNECT_ERROR = "ACCOUNT_SAFETY_CONNECT_ERROR"
ACCOUNT_SAFETY_TIMEOUT = "ACCOUNT_SAFETY_TIMEOUT"
ACCOUNT_SAFETY_HTTP_STATUS = "ACCOUNT_SAFETY_HTTP_STATUS"
ACCOUNT_SAFETY_PROTOCOL_ERROR = "ACCOUNT_SAFETY_PROTOCOL_ERROR"
ACCOUNT_SAFETY_SCHEMA_MISMATCH = "ACCOUNT_SAFETY_SCHEMA_MISMATCH"


class AccountSafetyProbe:
  def __init__(self, api_url: str, timeout_seconds: float) -> None:
    self.url = f"{api_url.rstrip('/')}/internal/monitor/account-safety"
    self.timeout_seconds = timeout_seconds

  async def run(self, client: httpx.AsyncClient) -> AccountSafetyProbeOutcome:
    checked_at = utc_now()
    started = perf_counter()
    try:
      response = await client.get(
        self.url,
        timeout=self.timeout_seconds,
        follow_redirects=False,
      )
    except (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException):
      return self._failure(checked_at, ACCOUNT_SAFETY_TIMEOUT)
    except (httpx.TransportError, ConnectionError, OSError):
      return self._failure(checked_at, ACCOUNT_SAFETY_CONNECT_ERROR)
    except Exception:
      return self._failure(checked_at, ACCOUNT_SAFETY_PROTOCOL_ERROR)

    if response.status_code != 200:
      return self._failure(
        checked_at,
        ACCOUNT_SAFETY_HTTP_STATUS,
        status_code=response.status_code,
      )
    try:
      payload: Any = response.json()
    except ValueError:
      return self._failure(
        checked_at,
        ACCOUNT_SAFETY_PROTOCOL_ERROR,
        status_code=response.status_code,
      )
    if not isinstance(payload, dict):
      return self._failure(
        checked_at,
        ACCOUNT_SAFETY_PROTOCOL_ERROR,
        status_code=response.status_code,
      )
    if payload.get("schema_version") != ACCOUNT_SAFETY_OBSERVATION_SCHEMA_VERSION:
      return self._failure(
        checked_at,
        ACCOUNT_SAFETY_SCHEMA_MISMATCH,
        status_code=response.status_code,
      )
    try:
      snapshot = AccountSafetyObservationSnapshot.model_validate(payload)
    except ValidationError:
      return self._failure(
        checked_at,
        ACCOUNT_SAFETY_PROTOCOL_ERROR,
        status_code=response.status_code,
      )
    latency_ms = (perf_counter() - started) * 1000
    return AccountSafetyProbeOutcome(
      source=ProbeResult(
        target_id="account-safety-observer",
        checked_at=checked_at,
        observed_status=(
          MonitorStatus.HEALTHY
          if snapshot.status == "ready"
          else MonitorStatus.DISABLED
        ),
        latency_ms=latency_ms,
        status_code=response.status_code,
        reason_code=(None if snapshot.status == "ready" else "ACCOUNT_SAFETY_DISABLED"),
      ),
      snapshot=snapshot,
    )

  @staticmethod
  def _failure(
    checked_at,
    reason_code: str,
    *,
    status_code: int | None = None,
  ) -> AccountSafetyProbeOutcome:
    return AccountSafetyProbeOutcome(
      source=ProbeResult(
        target_id="account-safety-observer",
        checked_at=checked_at,
        observed_status=MonitorStatus.UNAVAILABLE,
        latency_ms=None,
        status_code=status_code,
        reason_code=reason_code,
      ),
      snapshot=None,
    )
