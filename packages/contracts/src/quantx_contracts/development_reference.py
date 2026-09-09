"""Bounded development calendar and factor snapshot requests."""

from datetime import date
from typing import Annotated, Literal

from pydantic import (
  AwareDatetime,
  BaseModel,
  ConfigDict,
  Field,
  TypeAdapter,
  model_validator,
)


class CalendarRequest(BaseModel):
  model_config = ConfigDict(extra="forbid")
  operation: Literal["calendar"] = "calendar"
  year: int = Field(ge=1990, le=2100)
  market: Literal["SH"] = "SH"


class FactorReferenceRequest(BaseModel):
  model_config = ConfigDict(extra="forbid")
  operation: Literal["divid_factors"] = "divid_factors"
  instrument: str = Field(pattern=r"^[A-Z0-9]{1,16}\.(SH|SZ|BJ)$")
  start_date: date
  end_date: date

  @model_validator(mode="after")
  def window(self):
    if not date(1990, 1, 1) <= self.start_date <= self.end_date <= date(2100, 12, 31):
      raise ValueError("invalid reference dates")
    return self


ReferenceRequest = Annotated[
  CalendarRequest | FactorReferenceRequest, Field(discriminator="operation")
]
REFERENCE_REQUEST = TypeAdapter(ReferenceRequest)


class CalendarDay(BaseModel):
  model_config = ConfigDict(extra="forbid")
  date: date
  description: str | None = Field(max_length=200)


class CalendarSnapshot(CalendarRequest):
  holidays: list[CalendarDay] = Field(min_length=1, max_length=366)

  @model_validator(mode="after")
  def scope(self):
    dates = [item.date for item in self.holidays]
    if len(dates) != len(set(dates)) or any(day.year != self.year for day in dates):
      raise ValueError("calendar snapshot scope mismatch")
    return self


class FactorReferenceResult(FactorReferenceRequest):
  records_verified: int = Field(strict=True, ge=0, le=10000)
  content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReferenceAccepted(BaseModel):
  model_config = ConfigDict(extra="forbid")
  request_id: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReferenceStatus(ReferenceAccepted):
  request: ReferenceRequest
  state: Literal["QUEUED", "WAITING", "VERIFIED", "BLOCKED"]
  attempts: int = Field(ge=0, le=4)
  reason: str | None
  next_probe_at: AwareDatetime
  result: CalendarSnapshot | FactorReferenceResult | None

  @model_validator(mode="after")
  def completion(self):
    if (self.state == "VERIFIED") != (self.result is not None):
      raise ValueError("reference result does not match state")
    if self.result is not None:
      if self.result.operation != self.request.operation:
        raise ValueError("reference result operation changed")
      request = {
        key: self.result.model_dump()[key] for key in type(self.request).model_fields
      }
      if request != self.request.model_dump():
        raise ValueError("reference result scope changed")
    return self
