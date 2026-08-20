"""Binary wire contract for the dedicated whole-market quote stream."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

import orjson
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MARKET_STREAM_SUBPROTOCOL = "quantx.market.v1"
MARKET_STREAM_VERSION = 1
MARKET_STREAM_MARKETS = ("SH", "SZ")
MAX_MARKET_STREAM_FRAME_BYTES = 64 * 1024 * 1024
MARKET_STREAM_MAX_CAPTURE_AGE_SECONDS = 10.0
MARKET_STREAM_MAX_FUTURE_SKEW_SECONDS = 5.0
_SHANGHAI_ZONE = ZoneInfo("Asia/Shanghai")


def _aware_utc(value: datetime | None, *, field: str) -> datetime:
  if not isinstance(value, datetime):
    raise ValueError(f"{field} must be a datetime")
  if value.tzinfo is None or value.utcoffset() is None:
    raise ValueError(f"{field} must be timezone-aware")
  return value.astimezone(timezone.utc)


def _parse_market_timetag(value: object) -> float | None:
  raw = str(value or "").strip()
  if not raw:
    return None
  formats = (
    "%Y%m%d %H:%M:%S.%f",
    "%Y%m%d %H:%M:%S",
    "%Y%m%dT%H:%M:%S.%f",
    "%Y%m%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y%m%d%H%M%S%f",
    "%Y%m%d%H%M%S",
  )
  for format_string in formats:
    try:
      parsed = datetime.strptime(raw, format_string)
      return parsed.replace(tzinfo=_SHANGHAI_ZONE).timestamp()
    except ValueError:
      continue
  return None


def market_tick_source_time(
  tick: dict[str, Any],
  *,
  reference_at: datetime | None = None,
  max_future_skew_seconds: float = MARKET_STREAM_MAX_FUTURE_SKEW_SECONDS,
) -> float:
  """Parse the broker timestamp without losing sub-second ordering.

  ``time`` accepts the XTData epoch-seconds or epoch-milliseconds forms.
  ``timetag`` is interpreted in Asia/Shanghai.  When a reference clock is
  supplied, implausibly future values are rejected before they can poison a
  per-instrument ordering watermark.
  """

  if not isinstance(tick, dict):
    raise ValueError("whole-market tick must be an object")
  source_time: float | None = None
  raw_time = tick.get("time")
  if not isinstance(raw_time, bool):
    try:
      numeric_time = float(raw_time)
    except (TypeError, ValueError):
      numeric_time = 0.0
    if math.isfinite(numeric_time) and numeric_time > 0:
      if numeric_time >= 100_000_000_000_000:
        raise ValueError(
          "whole-market tick time must use epoch seconds or milliseconds"
        )
      source_time = (
        numeric_time / 1000.0 if numeric_time > 10_000_000_000 else numeric_time
      )
  if source_time is None:
    source_time = _parse_market_timetag(tick.get("timetag"))
  if source_time is None:
    raise ValueError("whole-market tick has no valid source time")

  if reference_at is not None:
    if max_future_skew_seconds < 0:
      raise ValueError("max_future_skew_seconds must be non-negative")
    reference_timestamp = _aware_utc(
      reference_at,
      field="reference_at",
    ).timestamp()
    if source_time > reference_timestamp + max_future_skew_seconds:
      raise ValueError("whole-market tick source time is in the future")
  return source_time


def validate_market_stream_capture_time(
  captured_at: datetime | None,
  *,
  received_at: datetime,
  max_age_seconds: float | None = MARKET_STREAM_MAX_CAPTURE_AGE_SECONDS,
  max_future_skew_seconds: float = MARKET_STREAM_MAX_FUTURE_SKEW_SECONDS,
) -> float:
  """Return non-negative capture age or reject stale/future stream data."""

  if max_age_seconds is not None and max_age_seconds < 0:
    raise ValueError("max_age_seconds must be non-negative")
  if max_future_skew_seconds < 0:
    raise ValueError("max_future_skew_seconds must be non-negative")
  captured = _aware_utc(captured_at, field="captured_at")
  received = _aware_utc(received_at, field="received_at")
  signed_age = (received - captured).total_seconds()
  if signed_age < -max_future_skew_seconds:
    raise ValueError("market stream captured_at is in the future")
  if max_age_seconds is not None and signed_age > max_age_seconds:
    raise ValueError("market stream captured_at is stale")
  return max(0.0, signed_age)


def utcnow() -> datetime:
  return datetime.now(timezone.utc)


class MarketBatchKind(str, Enum):
  SNAPSHOT = "SNAPSHOT"
  DELTA = "DELTA"


class MarketControlType(str, Enum):
  START = "START"
  ACK = "ACK"
  RESYNC = "RESYNC"


class MarketStreamControl(BaseModel):
  """Small JSON control frame exchanged outside the binary data path."""

  model_config = ConfigDict(extra="forbid")

  version: int = MARKET_STREAM_VERSION
  type: MarketControlType
  stream_id: str = Field(min_length=1)
  sequence: int = Field(default=0, ge=0)
  markets: tuple[str, ...] = ()
  reason: str = ""

  @field_validator("version")
  @classmethod
  def require_version(cls, value: int) -> int:
    if value != MARKET_STREAM_VERSION:
      raise ValueError(f"unsupported market stream version: {value}")
    return value

  @field_validator("markets")
  @classmethod
  def require_supported_markets(cls, value: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(str(market).strip().upper() for market in value)
    if normalized and normalized != MARKET_STREAM_MARKETS:
      raise ValueError("market stream must cover exactly SH and SZ")
    return normalized


class MarketStreamBatch(BaseModel):
  """One ordered whole-market snapshot or delta batch."""

  model_config = ConfigDict(extra="forbid")

  version: int = MARKET_STREAM_VERSION
  stream_id: str = Field(min_length=1)
  sequence: int = Field(gt=0)
  kind: MarketBatchKind
  captured_at: datetime = Field(default_factory=utcnow)
  instrument_count: int = Field(ge=0)
  data: dict[str, dict[str, Any]]

  @field_validator("version")
  @classmethod
  def require_version(cls, value: int) -> int:
    if value != MARKET_STREAM_VERSION:
      raise ValueError(f"unsupported market stream version: {value}")
    return value

  @field_validator("captured_at")
  @classmethod
  def require_timezone(cls, value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
      raise ValueError("captured_at must be timezone-aware")
    return value

  @model_validator(mode="after")
  def require_matching_count(self):
    if self.instrument_count != len(self.data):
      raise ValueError("instrument_count does not match data")
    if self.kind is MarketBatchKind.SNAPSHOT and not self.data:
      raise ValueError("market stream SNAPSHOT cannot be empty")
    return self

  def to_bytes(self) -> bytes:
    # Avoid Pydantic recursively walking every tick before orjson walks it a
    # second time. The model is already validated and the explicit wire map
    # keeps serialization single-pass for full-market frames.
    payload = orjson.dumps(
      {
        "version": self.version,
        "stream_id": self.stream_id,
        "sequence": self.sequence,
        "kind": self.kind.value,
        "captured_at": self.captured_at,
        "instrument_count": self.instrument_count,
        "data": self.data,
      }
    )
    if len(payload) > MAX_MARKET_STREAM_FRAME_BYTES:
      raise ValueError("market stream frame exceeds 64 MiB")
    return payload

  @classmethod
  def from_bytes(cls, payload: bytes) -> "MarketStreamBatch":
    if not isinstance(payload, bytes):
      raise TypeError("market stream data frame must be bytes")
    if len(payload) > MAX_MARKET_STREAM_FRAME_BYTES:
      raise ValueError("market stream frame exceeds 64 MiB")
    return cls.model_validate(orjson.loads(payload))
