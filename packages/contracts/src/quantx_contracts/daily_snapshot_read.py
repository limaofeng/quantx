"""Bounded latest daily snapshots within an explicit local storage window."""

from datetime import timedelta
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

InstrumentCode = Annotated[str, Field(pattern=r"^[A-Z0-9]{1,16}\.(SH|SZ|BJ)$")]


class DailySnapshotRead(BaseModel):
  model_config = ConfigDict(extra="forbid")
  instruments: list[InstrumentCode] = Field(min_length=1, max_length=32)
  start: AwareDatetime
  end: AwareDatetime

  @model_validator(mode="after")
  def bounded_scope(self):
    if len(set(self.instruments)) != len(self.instruments):
      raise ValueError("daily snapshot instruments must be unique")
    if not timedelta(0) <= self.end - self.start <= timedelta(days=60):
      raise ValueError("daily snapshot window must not exceed 60 days")
    return self


class DailySnapshot(BaseModel):
  model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
  stock_code: InstrumentCode
  period: Literal["1d"]
  time: AwareDatetime
  open: float
  high: float
  low: float
  close: float
  pre_close: float
  volume: float
  amount: float


class DailySnapshotResult(BaseModel):
  """Missing instruments have no local row; this is not a coverage proof."""

  model_config = ConfigDict(extra="forbid")
  request: DailySnapshotRead
  records: list[DailySnapshot] = Field(max_length=32)

  @model_validator(mode="after")
  def exact_scope(self):
    seen = set()
    for row in self.records:
      if (
        row.stock_code not in self.request.instruments
        or row.stock_code in seen
        or not self.request.start <= row.time <= self.request.end
      ):
        raise ValueError("daily snapshots contain duplicate or out-of-scope rows")
      seen.add(row.stock_code)
    return self
