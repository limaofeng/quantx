"""Event-loop-owned, read-only QMT Agent health projection."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable

from quantx_contracts import (
  PROTOCOL_VERSION,
  QmtAgentControlConnectionStatus,
  QmtAgentDependencyStatus,
  QmtAgentHealthReason,
  QmtAgentHealthSnapshot,
  QmtAgentHealthStatus,
  QmtAgentLiveResponse,
  QmtAgentMarketStreamStatus,
  QmtAgentMode,
  QmtAgentReconciliationStatus,
)

AGENT_VERSION = "0.1.0"


@dataclass(frozen=True, slots=True)
class _AgentHealthProjection:
  control_connection_status: QmtAgentControlConnectionStatus
  reconciliation_status: QmtAgentReconciliationStatus
  xtdata_status: QmtAgentDependencyStatus
  xttrading_status: QmtAgentDependencyStatus
  market_stream_status: QmtAgentMarketStreamStatus


class AgentHealthState:
  """Own the immutable local health snapshot read by the ASGI handler.

  Runtime transitions and the HTTP handler share the Agent event loop. Each
  update replaces the frozen projection, so a request observes one coherent
  object without calling XTData, XTTrading, the broker, or the network.
  """

  def __init__(
    self,
    mode: str,
    *,
    monotonic_clock: Callable[[], float] = time.monotonic,
    utc_clock: Callable[[], datetime] | None = None,
  ) -> None:
    self.mode = QmtAgentMode(mode)
    self._monotonic_clock = monotonic_clock
    self._utc_clock = utc_clock or (lambda: datetime.now(timezone.utc))
    self._started_monotonic = monotonic_clock()
    self._projection = _AgentHealthProjection(
      control_connection_status=QmtAgentControlConnectionStatus.DISCONNECTED,
      reconciliation_status=(
        QmtAgentReconciliationStatus.RECONCILING
        if self.mode is QmtAgentMode.LIVE
        else QmtAgentReconciliationStatus.READY
      ),
      xtdata_status=QmtAgentDependencyStatus.DISCONNECTED,
      xttrading_status=(
        QmtAgentDependencyStatus.DISCONNECTED
        if self.mode is QmtAgentMode.LIVE
        else QmtAgentDependencyStatus.DISABLED
      ),
      market_stream_status=QmtAgentMarketStreamStatus.OFFLINE,
    )

  def set_control_connected(self, connected: bool) -> None:
    self._replace(
      control_connection_status=(
        QmtAgentControlConnectionStatus.CONNECTED
        if connected
        else QmtAgentControlConnectionStatus.DISCONNECTED
      )
    )

  def set_reconciliation_ready(self, ready: bool) -> None:
    self._replace(
      reconciliation_status=(
        QmtAgentReconciliationStatus.READY
        if ready
        else QmtAgentReconciliationStatus.RECONCILING
      )
    )

  def set_xtdata_connected(self, connected: bool) -> None:
    self._replace(
      xtdata_status=(
        QmtAgentDependencyStatus.CONNECTED
        if connected
        else QmtAgentDependencyStatus.DISCONNECTED
      )
    )

  def set_xttrading_connected(self, connected: bool) -> None:
    if self.mode is not QmtAgentMode.LIVE:
      self._replace(xttrading_status=QmtAgentDependencyStatus.DISABLED)
      return
    self._replace(
      xttrading_status=(
        QmtAgentDependencyStatus.CONNECTED
        if connected
        else QmtAgentDependencyStatus.DISCONNECTED
      )
    )

  def set_market_stream_status(self, status: str) -> None:
    self._replace(market_stream_status=QmtAgentMarketStreamStatus(status.lower()))

  def live_response(self) -> QmtAgentLiveResponse:
    return QmtAgentLiveResponse(observed_at=self._observed_at())

  def snapshot(self) -> QmtAgentHealthSnapshot:
    projection = self._projection
    status, reason = self._derive_status(projection)
    return QmtAgentHealthSnapshot(
      status=status,
      reason_code=reason,
      agent_version=AGENT_VERSION,
      protocol_version=PROTOCOL_VERSION,
      mode=self.mode,
      uptime_seconds=max(
        0.0,
        self._monotonic_clock() - self._started_monotonic,
      ),
      control_connection_status=projection.control_connection_status,
      reconciliation_status=projection.reconciliation_status,
      xtdata_status=projection.xtdata_status,
      xttrading_status=projection.xttrading_status,
      market_stream_status=projection.market_stream_status,
      observed_at=self._observed_at(),
    )

  def _replace(self, **changes: object) -> None:
    self._projection = replace(self._projection, **changes)

  def _observed_at(self) -> datetime:
    observed_at = self._utc_clock()
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
      raise RuntimeError("Agent health UTC clock returned a naive timestamp")
    return observed_at

  def _derive_status(
    self,
    projection: _AgentHealthProjection,
  ) -> tuple[QmtAgentHealthStatus, QmtAgentHealthReason | None]:
    if (
      projection.control_connection_status
      is QmtAgentControlConnectionStatus.DISCONNECTED
    ):
      return (
        QmtAgentHealthStatus.UNAVAILABLE,
        QmtAgentHealthReason.CONTROL_CONNECTION_OFFLINE,
      )
    if projection.xtdata_status is not QmtAgentDependencyStatus.CONNECTED:
      return (
        QmtAgentHealthStatus.UNAVAILABLE,
        QmtAgentHealthReason.XTDATA_UNAVAILABLE,
      )
    if (
      self.mode is QmtAgentMode.LIVE
      and projection.xttrading_status is not QmtAgentDependencyStatus.CONNECTED
    ):
      return (
        QmtAgentHealthStatus.UNAVAILABLE,
        QmtAgentHealthReason.XTTRADING_UNAVAILABLE,
      )
    if projection.reconciliation_status is QmtAgentReconciliationStatus.RECONCILING:
      return (
        QmtAgentHealthStatus.DEGRADED,
        QmtAgentHealthReason.TRADING_RECONCILING,
      )
    if projection.market_stream_status is not QmtAgentMarketStreamStatus.READY:
      return (
        QmtAgentHealthStatus.DEGRADED,
        QmtAgentHealthReason.MARKET_STREAM_NOT_READY,
      )
    return QmtAgentHealthStatus.READY, None
