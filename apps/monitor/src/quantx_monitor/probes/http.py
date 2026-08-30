"""Bounded HTTP probes for known service endpoints."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

import httpx
from pydantic import ValidationError
from quantx_contracts.market_health import MarketGatewayHealth

from ..models import MonitorStatus, ProbeResult, utc_now
from .base import timed_result

PayloadEvaluator = Callable[
  [httpx.Response, dict[str, Any] | None],
  tuple[MonitorStatus, str | None],
]


def success_response(
  response: httpx.Response,
  _: dict[str, Any] | None,
) -> tuple[MonitorStatus, str | None]:
  if response.is_success:
    return MonitorStatus.HEALTHY, None
  return MonitorStatus.UNAVAILABLE, "HTTP_STATUS"


def json_status(expected: str) -> PayloadEvaluator:
  def evaluate(
    response: httpx.Response,
    payload: dict[str, Any] | None,
  ) -> tuple[MonitorStatus, str | None]:
    if not response.is_success:
      return MonitorStatus.UNAVAILABLE, "HTTP_STATUS"
    if payload is None:
      return MonitorStatus.UNAVAILABLE, "PROTOCOL_ERROR"
    if str(payload.get("status") or "").lower() == expected.lower():
      return MonitorStatus.HEALTHY, None
    return MonitorStatus.UNAVAILABLE, "DEPENDENCY_NOT_READY"

  return evaluate


def market_gateway_status(
  response: httpx.Response,
  payload: dict[str, Any] | None,
) -> tuple[MonitorStatus, str | None]:
  """A real HTTP response contributes RTT, including unavailable supply (503)."""
  try:
    health = MarketGatewayHealth.model_validate(payload)
    if response.status_code != (200 if health.status == "ready" else 503):
      raise ValueError("HTTP status and market supply health disagree")
  except (ValidationError, ValueError):
    return MonitorStatus.UNAVAILABLE, "PROTOCOL_ERROR"
  return (
    MonitorStatus.HEALTHY if health.status == "ready" else MonitorStatus.UNAVAILABLE,
    health.reason_code.value if health.reason_code is not None else None,
  )


class HttpProbe:
  def __init__(
    self,
    target_id: str,
    url: str,
    *,
    timeout_seconds: float,
    evaluator: PayloadEvaluator = success_response,
    verify: bool = True,
    headers: dict[str, str] | None = None,
    enabled: bool = True,
  ) -> None:
    self.target_id = target_id
    self.url = url
    self.timeout_seconds = timeout_seconds
    self.evaluator = evaluator
    self.verify = verify
    self.headers = dict(headers or {})
    self.enabled = enabled

  async def run(self, client: httpx.AsyncClient) -> ProbeResult:
    if not self.enabled:
      return ProbeResult(
        target_id=self.target_id,
        checked_at=utc_now(),
        observed_status=MonitorStatus.DISABLED,
      )

    temporary_client: httpx.AsyncClient | None = None
    request_client = client
    if not self.verify:
      temporary_client = httpx.AsyncClient(verify=False, trust_env=False)
      request_client = temporary_client

    async def request() -> httpx.Response:
      return await request_client.get(
        self.url,
        timeout=self.timeout_seconds,
        headers=self.headers,
        follow_redirects=True,
      )

    def evaluate(
      response: httpx.Response,
    ) -> tuple[MonitorStatus, int | None, str | None]:
      payload: dict[str, Any] | None = None
      content_type = response.headers.get("content-type", "").lower()
      if "json" in content_type:
        try:
          decoded = response.json()
          if isinstance(decoded, dict):
            payload = decoded
        except ValueError:
          payload = None
      status, reason = self.evaluator(response, payload)
      return status, response.status_code, reason

    try:
      result = await timed_result(self.target_id, request, evaluate)
      # A timeout/connection failure has no HTTP RTT; do not chart its deadline.
      return (
        result if result.status_code is not None else replace(result, latency_ms=None)
      )
    finally:
      if temporary_client is not None:
        await temporary_client.aclose()
