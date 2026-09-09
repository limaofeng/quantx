"""Local service boundary for bounded historical collection submissions/results."""

import json
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

MAX_HISTORY_CONTROL_BYTES = 512 * 1024
MAX_HISTORY_RESULT_BYTES = 2 * 1024 * 1024


class HistoryCollectionSubmission(BaseModel):
  model_config = ConfigDict(extra="forbid")
  payload: dict[str, JsonValue]
  device_id: UUID | None = None
  required_capabilities: list[
    Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")]
  ] = Field(default_factory=list, max_length=16)
  idempotency_scope: str = Field(default="", max_length=200)

  @model_validator(mode="after")
  def validate_payload(self):
    if self.payload.get("operation", "bars") not in {
      "bars",
      "financial_data",
      "divid_factors",
      "sector_instruments",
      "instrument_details",
    }:
      raise ValueError("unsupported historical operation")
    if set(self.payload) - {
      "operation",
      "stock_list",
      "periods",
      "start_time",
      "end_time",
      "download",
      "sectors",
      "destination",
      "as_of_date",
      "table_list",
      "record_format",
      "source",
    }:
      raise ValueError("unsupported historical payload fields")
    if (
      len(json.dumps(self.payload, allow_nan=False).encode())
      > MAX_HISTORY_CONTROL_BYTES
    ):
      raise ValueError("historical payload exceeds byte limit")
    return self


class HistoryCollectionAccepted(BaseModel):
  model_config = ConfigDict(extra="forbid")
  request_id: UUID


class HistoryCollectionResult(HistoryCollectionAccepted):
  result: dict[str, JsonValue]
