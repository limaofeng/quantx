"""Fixed public and persisted monitor vocabulary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class MonitorStatus(StrEnum):
  HEALTHY = "healthy"
  DEGRADED = "degraded"
  UNAVAILABLE = "unavailable"
  UNKNOWN = "unknown"
  DISABLED = "disabled"


class TargetGroup(StrEnum):
  EXTERNAL = "external_dependency"
  RUNTIME = "quantx_runtime"


@dataclass(frozen=True)
class TargetDefinition:
  target_id: str
  name: str
  group: TargetGroup
  optional: bool = False
  derived: bool = False


@dataclass(frozen=True)
class ProbeResult:
  target_id: str
  checked_at: datetime
  observed_status: MonitorStatus
  latency_ms: float | None = None
  status_code: int | None = None
  reason_code: str | None = None

  @property
  def checked_at_epoch(self) -> float:
    value = self.checked_at
    if value.tzinfo is None:
      value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def utc_now() -> datetime:
  return datetime.now(timezone.utc)


def iso_timestamp(value: float | int | None) -> str | None:
  if value is None:
    return None
  return (
    datetime.fromtimestamp(float(value), timezone.utc)
    .isoformat()
    .replace("+00:00", "Z")
  )


def percentile(values: list[float], quantile: float) -> float | None:
  if not values:
    return None
  ordered = sorted(values)
  position = (len(ordered) - 1) * quantile
  lower = int(position)
  upper = min(lower + 1, len(ordered) - 1)
  fraction = position - lower
  return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def public_round(value: Any, digits: int = 3) -> Any:
  if isinstance(value, float):
    return round(value, digits)
  return value
