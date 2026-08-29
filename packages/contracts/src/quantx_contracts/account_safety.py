"""Sanitized account-safety observation contract shared with Monitor."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ACCOUNT_SAFETY_OBSERVATION_SCHEMA_VERSION = 1
ACCOUNT_EXECUTION_SAFETY_CHECK_CODES: tuple[str, ...] = (
  "SERVER_REAL_TRADING_ENABLED",
  "ACCOUNT_ALLOWLISTED",
  "ENGINE_READY",
  "LIVE_AGENT_READY",
  "AGENT_MODE_LIVE",
  "MARKET_STREAM_READY",
  "PROTOCOL_1_1",
  "EXECUTION_CONTROL_CONFIGURED",
  "SNAPSHOT_RECONCILED",
  "SNAPSHOT_FRESH",
  "SNAPSHOT_ACTIVITY_CLASSIFIED",
  "RECENT_BACKUP",
  "NO_CRITICAL_ALERTS",
  "NO_DEAD_LETTERS",
  "CONTROLLED_WINDOW_ACTIVE",
  "NO_EXTERNAL_BROKER_ACTIVITY",
  "KILL_SWITCH_CLEAR",
  "ACCOUNT_RISK_INCREASE_AUTHORIZED",
)
ACCOUNT_EXECUTION_SAFETY_CHECK_CODE_SET = frozenset(
  ACCOUNT_EXECUTION_SAFETY_CHECK_CODES
)


class AccountSafetyCheckStatus(StrEnum):
  PASSED = "PASSED"
  STANDBY = "STANDBY"
  FAILED = "FAILED"


class AccountSafetyCheckObservation(BaseModel):
  model_config = ConfigDict(extra="forbid")

  code: str = Field(min_length=1, max_length=64)
  status: AccountSafetyCheckStatus
  scope: Literal["OBSERVATION", "INCREASE_RISK"]
  reason_code: str | None = Field(default=None, min_length=1, max_length=64)
  public_message: str = Field(default="", max_length=500)

  @field_validator("code")
  @classmethod
  def require_known_code(cls, value: str) -> str:
    if value not in ACCOUNT_EXECUTION_SAFETY_CHECK_CODE_SET:
      raise ValueError("unknown account-safety check code")
    return value

  @model_validator(mode="after")
  def require_reason_for_non_passed(self) -> "AccountSafetyCheckObservation":
    if self.status is AccountSafetyCheckStatus.PASSED:
      if self.reason_code is not None or self.public_message:
        raise ValueError("passed account-safety checks must not expose a reason")
    elif self.reason_code is None or not self.public_message:
      raise ValueError("non-passed account-safety checks require a public reason")
    return self


class AccountSafetyObservationSnapshot(BaseModel):
  """Account-free readiness projection safe for the local Monitor process."""

  model_config = ConfigDict(extra="forbid")

  schema_version: Literal[1] = ACCOUNT_SAFETY_OBSERVATION_SCHEMA_VERSION
  status: Literal["ready", "disabled"]
  observed_at: datetime
  checks: list[AccountSafetyCheckObservation] = Field(max_length=18)

  @field_validator("observed_at")
  @classmethod
  def require_observed_timezone(cls, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
      raise ValueError("observed_at must be timezone-aware")
    return value

  @model_validator(mode="after")
  def require_complete_snapshot(self) -> "AccountSafetyObservationSnapshot":
    codes = tuple(item.code for item in self.checks)
    if self.status == "disabled":
      if codes:
        raise ValueError("disabled account-safety snapshots must not include checks")
    elif codes != ACCOUNT_EXECUTION_SAFETY_CHECK_CODES:
      raise ValueError("ready account-safety snapshots must contain every check in order")
    return self
