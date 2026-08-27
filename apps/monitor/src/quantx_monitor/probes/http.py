"""Bounded HTTP probes for known service endpoints."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

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
      return await timed_result(self.target_id, request, evaluate)
    finally:
      if temporary_client is not None:
        await temporary_client.aclose()
