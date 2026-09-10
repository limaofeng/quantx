"""One bounded minute revision; acceptance is not persistence verification."""

import hashlib
import json
from datetime import timezone
from typing import Literal
from uuid import UUID

from pydantic import (
  AwareDatetime,
  BaseModel,
  ConfigDict,
  Field,
  FiniteFloat,
  model_validator,
)

MAX_ARCHIVE_REQUEST_BYTES = 4096
MAX_ARCHIVE_PENDING = 20_000
MAX_ARCHIVE_SCOPES_PER_GENERATION = 10_000


class ArchiveRecoveryScope(BaseModel):
  """Open instrument interval, persisted before any volatile archive work.

  It stays open across unsubscribe/re-subscribe for the entire Engine generation.
  Transport acceptance cannot close this interval or prove historical coverage.
  """

  model_config = ConfigDict(extra="forbid")
  generation: int = Field(strict=True, gt=0, le=2**63 - 1)
  instrument: str = Field(pattern=r"^[A-Z0-9]{1,16}\.(SH|SZ|BJ)$")
  start_minute: AwareDatetime

  @model_validator(mode="after")
  def minute_boundary(self):
    self.start_minute = self.start_minute.astimezone(timezone.utc)
    if (
      self.start_minute.second
      or self.start_minute.microsecond
      or not 1990 <= self.start_minute.year <= 2100
    ):
      raise ValueError("archive scope must start at an exact minute")
    return self


class ArchiveBar(BaseModel):
  model_config = ConfigDict(extra="forbid")
  open: FiniteFloat = Field(strict=True, gt=0)
  high: FiniteFloat = Field(strict=True, gt=0)
  low: FiniteFloat = Field(strict=True, gt=0)
  close: FiniteFloat = Field(strict=True, gt=0)
  pre_close: FiniteFloat = Field(strict=True, ge=0)
  volume: FiniteFloat = Field(strict=True, ge=0)
  amount: FiniteFloat = Field(strict=True, ge=0)
  settelement_price: FiniteFloat = Field(strict=True, ge=0)
  open_interest: int = Field(strict=True, ge=0)
  suspend_flag: int = Field(strict=True, ge=0)

  @model_validator(mode="after")
  def prices(self):
    if self.high < max(self.open, self.close, self.low) or self.low > min(
      self.open, self.close
    ):
      raise ValueError("archive OHLC range is inconsistent")
    return self


class ArchiveRevision(BaseModel):
  model_config = ConfigDict(extra="forbid")
  instrument: str = Field(pattern=r"^[A-Z0-9]{1,16}\.(SH|SZ|BJ)$")
  minute: AwareDatetime
  generation: int = Field(strict=True, gt=0, le=2**63 - 1)
  continuity_generation: int = Field(strict=True, gt=0, le=2**63 - 1)
  stream_id: UUID
  sequence: int = Field(strict=True, gt=0, le=2**63 - 1)
  sealed: bool = Field(strict=True)
  bar: ArchiveBar

  @model_validator(mode="after")
  def minute_boundary(self):
    self.minute = self.minute.astimezone(timezone.utc)
    if (
      self.minute.second
      or self.minute.microsecond
      or not 1990 <= self.minute.year <= 2100
    ):
      raise ValueError("archive timestamp must be an exact minute")
    return self

  def identity(self) -> str:
    return self._hash(
      "archive-revision-v1", self.model_dump(mode="json", exclude={"bar"})
    )

  def storage_version(self) -> str:
    return self._hash("archive-content-v1", self.model_dump(mode="json"))

  @staticmethod
  def _hash(namespace, value):
    return hashlib.sha256(
      (
        namespace + ":" + json.dumps(value, sort_keys=True, separators=(",", ":"))
      ).encode()
    ).hexdigest()


class ArchiveAccepted(BaseModel):
  model_config = ConfigDict(extra="forbid")
  request_id: str = Field(pattern=r"^[0-9a-f]{64}$")
  state: Literal["ACCEPTED"] = "ACCEPTED"


class ArchiveProof(BaseModel):
  model_config = ConfigDict(extra="forbid")
  storage_version: str = Field(pattern=r"^[0-9a-f]{64}$")
  schema_version: int = Field(strict=True, ge=1, le=1)
  records_verified: int = Field(strict=True, ge=1, le=1)
  fields_verified: int = Field(strict=True, gt=0)
  source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
  persisted_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

  @model_validator(mode="after")
  def same_content(self):
    if self.source_sha256 != self.persisted_sha256:
      raise ValueError("archive content proof differs")
    return self


class ArchiveStatus(BaseModel):
  model_config = ConfigDict(extra="forbid")
  request_id: str = Field(pattern=r"^[0-9a-f]{64}$")
  request: ArchiveRevision
  phase: Literal["WRITE", "READBACK", "VERIFIED", "BLOCKED"]
  write_attempts: int = Field(strict=True, ge=0, le=4)
  read_attempts: int = Field(strict=True, ge=0, le=4)
  reason: str | None
  next_retry_at: AwareDatetime
  proof: ArchiveProof | None

  @model_validator(mode="after")
  def identity_and_proof(self):
    if self.request_id != self.request.identity():
      raise ValueError("archive status identity mismatch")
    if (self.phase == "VERIFIED") != (self.proof is not None):
      raise ValueError("archive status proof mismatch")
    if self.proof and self.proof.storage_version != self.request.storage_version():
      raise ValueError("archive proof storage identity mismatch")
    return self
