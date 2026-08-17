"""Binary wire contract for the dedicated whole-market quote stream."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

import orjson
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MARKET_STREAM_SUBPROTOCOL = "quantx.market.v1"
MARKET_STREAM_VERSION = 1
MARKET_STREAM_MARKETS = ("SH", "SZ")
MAX_MARKET_STREAM_FRAME_BYTES = 64 * 1024 * 1024


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
