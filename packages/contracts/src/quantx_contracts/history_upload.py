"""Historical upload receipts describe persisted manifests, not data verification."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HistoryUploadAcknowledgement(BaseModel):
  model_config = ConfigDict(extra="forbid", frozen=True)
  accepted: Literal[True]
  duplicate: bool = Field(strict=True)

  @model_validator(mode="before")
  @classmethod
  def explicit_acceptance(cls, value):
    if not isinstance(value, dict) or value.get("accepted") is not True:
      raise ValueError("history upload requires explicit acceptance")
    return value


class HistoryUploadChunk(BaseModel):
  model_config = ConfigDict(extra="forbid", frozen=True)
  index: int = Field(ge=0, lt=128, strict=True)
  sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
  record_count: int = Field(ge=0, le=5000, strict=True)
  byte_count: int = Field(gt=0, le=32 * 1024 * 1024, strict=True)


class HistoryUploadSnapshot(BaseModel):
  model_config = ConfigDict(extra="forbid", frozen=True)
  request_id: UUID
  status: Literal[
    "QUEUED",
    "DELIVERED",
    "RECEIVING",
    "UPLOADED",
    "PROCESSING",
    "BLOCKED",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "INCOMPLETE",
  ]
  total_chunks: int | None = Field(ge=1, le=128, strict=True)
  chunks: list[HistoryUploadChunk] = Field(max_length=128)

  @property
  def frozen(self) -> bool:
    return self.status in {"UPLOADED", "PROCESSING", "BLOCKED", "COMPLETED"}

  @model_validator(mode="after")
  def consistent_manifest(self):
    indices = [chunk.index for chunk in self.chunks]
    if indices != sorted(set(indices)):
      raise ValueError("history upload chunk indices are not unique and ordered")
    if self.total_chunks is not None and any(
      index >= self.total_chunks for index in indices
    ):
      raise ValueError("history upload chunk outside manifest")
    if self.frozen and (
      self.total_chunks is None or indices != list(range(self.total_chunks))
    ):
      raise ValueError("frozen history upload manifest is incomplete")
    return self
