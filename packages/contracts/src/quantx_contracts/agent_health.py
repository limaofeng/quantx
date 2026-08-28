"""Sanitized health contract shared by QMT Agent and Monitor."""

from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

QMT_AGENT_HEALTH_SCHEMA_VERSION = 1


class QmtAgentHealthStatus(StrEnum):
  READY = "ready"
  DEGRADED = "degraded"
  UNAVAILABLE = "unavailable"


class QmtAgentHealthReason(StrEnum):
  CONTROL_CONNECTION_OFFLINE = "CONTROL_CONNECTION_OFFLINE"
  TRADING_RECONCILING = "TRADING_RECONCILING"
  XTDATA_UNAVAILABLE = "XTDATA_UNAVAILABLE"
  XTTRADING_UNAVAILABLE = "XTTRADING_UNAVAILABLE"
  MARKET_STREAM_NOT_READY = "MARKET_STREAM_NOT_READY"


class QmtAgentMode(StrEnum):
  DATA_ONLY = "data-only"
  PAPER = "paper"
  LIVE = "live"


class QmtAgentControlConnectionStatus(StrEnum):
  CONNECTED = "connected"
  DISCONNECTED = "disconnected"


class QmtAgentReconciliationStatus(StrEnum):
  READY = "ready"
  RECONCILING = "reconciling"


class QmtAgentDependencyStatus(StrEnum):
  CONNECTED = "connected"
  DISCONNECTED = "disconnected"
  DISABLED = "disabled"


class QmtAgentMarketStreamStatus(StrEnum):
  READY = "ready"
  SYNCING = "syncing"
  STALE = "stale"
  OFFLINE = "offline"


class _QmtAgentHealthBase(BaseModel):
  model_config = ConfigDict(extra="forbid")

  schema_version: Literal[1] = QMT_AGENT_HEALTH_SCHEMA_VERSION
  observed_at: datetime

  @field_validator("observed_at")
  @classmethod
  def require_observed_timezone(cls, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
      raise ValueError("observed_at must be timezone-aware")
    return value


class QmtAgentLiveResponse(_QmtAgentHealthBase):
  """Minimal response proving that the Agent event loop can answer HTTP."""

  status: Literal["alive"] = "alive"
  component: Literal["qmt-agent"] = "qmt-agent"


class QmtAgentHealthSnapshot(_QmtAgentHealthBase):
  """Versioned, non-sensitive local readiness projection."""

  status: QmtAgentHealthStatus
  reason_code: QmtAgentHealthReason | None
  agent_version: str = Field(min_length=1, max_length=32)
  protocol_version: Literal["1.1"] = "1.1"
  mode: QmtAgentMode
  uptime_seconds: float = Field(ge=0)
  control_connection_status: QmtAgentControlConnectionStatus
  reconciliation_status: QmtAgentReconciliationStatus
  xtdata_status: QmtAgentDependencyStatus
  xttrading_status: QmtAgentDependencyStatus
  market_stream_status: QmtAgentMarketStreamStatus

  @field_validator("uptime_seconds")
  @classmethod
  def require_finite_uptime(cls, value: float) -> float:
    if not math.isfinite(value):
      raise ValueError("uptime_seconds must be finite")
    return value

  @model_validator(mode="after")
  def require_consistent_status(self) -> "QmtAgentHealthSnapshot":
    if self.mode is QmtAgentMode.LIVE:
      if self.xttrading_status is QmtAgentDependencyStatus.DISABLED:
        raise ValueError("live health snapshot cannot disable XTTrading")
    elif self.xttrading_status is not QmtAgentDependencyStatus.DISABLED:
      raise ValueError("non-live health snapshot must disable XTTrading")

    expected_status = QmtAgentHealthStatus.READY
    expected_reason = None
    if self.control_connection_status is QmtAgentControlConnectionStatus.DISCONNECTED:
      expected_status = QmtAgentHealthStatus.UNAVAILABLE
      expected_reason = QmtAgentHealthReason.CONTROL_CONNECTION_OFFLINE
    elif self.xtdata_status is not QmtAgentDependencyStatus.CONNECTED:
      expected_status = QmtAgentHealthStatus.UNAVAILABLE
      expected_reason = QmtAgentHealthReason.XTDATA_UNAVAILABLE
    elif (
      self.mode is QmtAgentMode.LIVE
      and self.xttrading_status is not QmtAgentDependencyStatus.CONNECTED
    ):
      expected_status = QmtAgentHealthStatus.UNAVAILABLE
      expected_reason = QmtAgentHealthReason.XTTRADING_UNAVAILABLE
    elif self.reconciliation_status is QmtAgentReconciliationStatus.RECONCILING:
      expected_status = QmtAgentHealthStatus.DEGRADED
      expected_reason = QmtAgentHealthReason.TRADING_RECONCILING
    elif self.market_stream_status is not QmtAgentMarketStreamStatus.READY:
      expected_status = QmtAgentHealthStatus.DEGRADED
      expected_reason = QmtAgentHealthReason.MARKET_STREAM_NOT_READY
    if self.status is not expected_status or self.reason_code is not expected_reason:
      raise ValueError("health status and reason do not match readiness fields")
    return self
