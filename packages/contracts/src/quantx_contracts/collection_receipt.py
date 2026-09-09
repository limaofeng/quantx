"""Durable native START/FINISH facts; receipt acceptance is not execution approval."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .collection_permit import CollectionUnit


class CollectionCompletion(BaseModel):
  model_config = ConfigDict(extra="forbid", frozen=True)
  unit: CollectionUnit
  sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
  byte_count: int = Field(gt=0, strict=True)
  record_count: int = Field(ge=0, strict=True)


class CollectionReceipt(BaseModel):
  model_config = ConfigDict(extra="forbid", frozen=True)
  event: Literal["START", "FINISH"]
  completion: CollectionCompletion | None = None

  @model_validator(mode="after")
  def requires_completion(self):
    if (self.event == "FINISH") != (self.completion is not None):
      raise ValueError("only FINISH requires native completion evidence")
    return self


class CollectionReceiptStatus(BaseModel):
  model_config = ConfigDict(extra="forbid", frozen=True)
  permit_id: UUID
  event: Literal["START", "FINISH"]
  status: Literal["PENDING", "ACCEPTED", "REJECTED"]
  reason_code: Literal["COLLECTION_RECEIPT_REJECTED"] | None = None

  @model_validator(mode="after")
  def reason_matches_status(self):
    if (self.status == "REJECTED") != (self.reason_code is not None):
      raise ValueError("only rejected collection receipts require a reason")
    return self
