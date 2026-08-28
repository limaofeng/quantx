"""Direct QMT Agent health probe and pure semantic-status composition."""

from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any

import httpx
from pydantic import ValidationError
from quantx_contracts import QmtAgentHealthSnapshot, QmtAgentHealthStatus

from ..models import MonitorStatus, ProbeResult, utc_now

QMT_HEALTH_CONNECT_ERROR = "QMT_HEALTH_CONNECT_ERROR"
QMT_HEALTH_TIMEOUT = "QMT_HEALTH_TIMEOUT"
QMT_HEALTH_HTTP_STATUS = "QMT_HEALTH_HTTP_STATUS"
QMT_HEALTH_PROTOCOL_ERROR = "QMT_HEALTH_PROTOCOL_ERROR"
QMT_HEALTH_SCHEMA_MISMATCH = "QMT_HEALTH_SCHEMA_MISMATCH"

QMT_HEALTH_PROBE_ERRORS = frozenset(
  {
    QMT_HEALTH_CONNECT_ERROR,
    QMT_HEALTH_TIMEOUT,
    QMT_HEALTH_HTTP_STATUS,
    QMT_HEALTH_PROTOCOL_ERROR,
    QMT_HEALTH_SCHEMA_MISMATCH,
  }
)


class QmtAgentHealthProbe:
  def __init__(self, root_url: str, timeout_seconds: float) -> None:
    self._ready_url = f"{root_url.rstrip('/')}/health/ready"
    self.timeout_seconds = timeout_seconds

  async def run(self, client: httpx.AsyncClient) -> ProbeResult:
    checked_at = utc_now()
    started = perf_counter()
    try:
      response = await client.get(
        self._ready_url,
        timeout=self.timeout_seconds,
        follow_redirects=False,
      )
    except (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException):
      return self._failure(checked_at, QMT_HEALTH_TIMEOUT)
    except (httpx.TransportError, ConnectionError, OSError):
      return self._failure(checked_at, QMT_HEALTH_CONNECT_ERROR)
    except Exception:
      return self._failure(checked_at, QMT_HEALTH_PROTOCOL_ERROR)

    latency_ms = (perf_counter() - started) * 1000
    if response.status_code not in {200, 503}:
      return self._failure(
        checked_at,
        QMT_HEALTH_HTTP_STATUS,
        status_code=response.status_code,
      )
    try:
      payload: Any = response.json()
    except ValueError:
      return self._failure(
        checked_at,
        QMT_HEALTH_PROTOCOL_ERROR,
        status_code=response.status_code,
      )
    if not isinstance(payload, dict):
      return self._failure(
        checked_at,
        QMT_HEALTH_PROTOCOL_ERROR,
        status_code=response.status_code,
      )
    if payload.get("schema_version") != 1:
      return self._failure(
        checked_at,
        QMT_HEALTH_SCHEMA_MISMATCH,
        status_code=response.status_code,
      )
    try:
      snapshot = QmtAgentHealthSnapshot.model_validate(payload)
    except ValidationError:
      return self._failure(
        checked_at,
        QMT_HEALTH_PROTOCOL_ERROR,
        status_code=response.status_code,
      )
    if (
      response.status_code == 200 and snapshot.status is not QmtAgentHealthStatus.READY
    ) or (
      response.status_code == 503 and snapshot.status is QmtAgentHealthStatus.READY
    ):
      return self._failure(
        checked_at,
        QMT_HEALTH_PROTOCOL_ERROR,
        status_code=response.status_code,
      )
    status = {
      QmtAgentHealthStatus.READY: MonitorStatus.HEALTHY,
      QmtAgentHealthStatus.DEGRADED: MonitorStatus.DEGRADED,
      QmtAgentHealthStatus.UNAVAILABLE: MonitorStatus.UNAVAILABLE,
    }[snapshot.status]
    return ProbeResult(
      target_id="qmt-agent",
      checked_at=checked_at,
      observed_status=status,
      latency_ms=latency_ms,
      status_code=response.status_code,
      reason_code=(snapshot.reason_code.value if snapshot.reason_code else None),
    )

  @staticmethod
  def _failure(
    checked_at,
    reason_code: str,
    *,
    status_code: int | None = None,
  ) -> ProbeResult:
    return ProbeResult(
      target_id="qmt-agent",
      checked_at=checked_at,
      observed_status=MonitorStatus.UNAVAILABLE,
      latency_ms=None,
      status_code=status_code,
      reason_code=reason_code,
    )


def combine_qmt_agent_probe(
  direct: ProbeResult,
  semantic: ProbeResult,
) -> ProbeResult:
  """Return the single QMT sample from local transport and API semantics."""

  if direct.target_id != "qmt-agent" or semantic.target_id != "qmt-agent":
    raise ValueError("QMT Agent combination requires qmt-agent results")

  direct_transport_failed = direct.reason_code in QMT_HEALTH_PROBE_ERRORS
  if direct_transport_failed:
    final_status = MonitorStatus.UNAVAILABLE
    reason_code = direct.reason_code
  else:
    semantic_status = semantic.observed_status
    if direct.observed_status is MonitorStatus.UNAVAILABLE:
      final_status = MonitorStatus.UNAVAILABLE
    elif semantic_status in {
      MonitorStatus.UNAVAILABLE,
      MonitorStatus.UNKNOWN,
      MonitorStatus.DISABLED,
    }:
      final_status = MonitorStatus.UNAVAILABLE
    elif (
      direct.observed_status is MonitorStatus.DEGRADED
      or semantic_status is MonitorStatus.DEGRADED
    ):
      final_status = MonitorStatus.DEGRADED
    elif (
      direct.observed_status is MonitorStatus.HEALTHY
      and semantic_status is MonitorStatus.HEALTHY
    ):
      final_status = MonitorStatus.HEALTHY
    else:
      final_status = MonitorStatus.UNAVAILABLE

    reason_code = (
      semantic.reason_code
      if semantic_status is not MonitorStatus.HEALTHY and semantic.reason_code
      else direct.reason_code
    )
    if final_status is MonitorStatus.HEALTHY:
      reason_code = None
    elif reason_code is None:
      reason_code = "SNAPSHOT_UNAVAILABLE"

  return ProbeResult(
    target_id="qmt-agent",
    checked_at=max(direct.checked_at, semantic.checked_at),
    observed_status=final_status,
    latency_ms=direct.latency_ms,
    status_code=direct.status_code,
    reason_code=reason_code,
  )
