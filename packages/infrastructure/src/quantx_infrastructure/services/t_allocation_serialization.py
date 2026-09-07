"""Lossless JSON evidence for portfolio attempts, never another intent protocol."""

from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any


def allocation_evidence(value: Any) -> Any:
  if is_dataclass(value):
    return {
      field.name: allocation_evidence(getattr(value, field.name))
      for field in fields(value)
    }
  if isinstance(value, Decimal):
    if not value.is_finite():
      raise ValueError("T_ALLOCATION_INVALID_NUMBER")
    if value == 0:
      return "0"
    normalized = format(value, "f")
    return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized
  if isinstance(value, datetime):
    return allocation_time(value).isoformat(timespec="microseconds")
  if isinstance(value, Enum):
    return value.value
  if isinstance(value, (tuple, list)):
    return [allocation_evidence(item) for item in value]
  if isinstance(value, dict):
    return {key: allocation_evidence(item) for key, item in value.items()}
  return value


def allocation_time(value: datetime) -> datetime:
  """SQL timestamp adapter: naive persisted values mean UTC, never local time."""
  if not isinstance(value, datetime):
    raise ValueError("T_ALLOCATION_DATETIME_REQUIRED")
  return (
    value.replace(tzinfo=UTC)
    if value.tzinfo is None or value.utcoffset() is None
    else value.astimezone(UTC)
  )
