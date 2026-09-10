"""Bounded latest daily snapshots within an explicit local storage window."""

from datetime import date, timedelta
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


class DailyBar(DailySnapshot):
  suspend_flag: int | None = None


class DailyBarsResult(BaseModel):
  """Available fixed daily partitions; missing rows are not coverage evidence."""

  model_config = ConfigDict(extra="forbid")
  request: DailySnapshotRead
  records: list[DailyBar] = Field(max_length=32 * 62)

  @model_validator(mode="after")
  def exact_scope(self):
    from zoneinfo import ZoneInfo

    seen = set()
    previous = None
    for row in self.records:
      key = (row.stock_code, row.time)
      day = (row.stock_code, row.time.astimezone(ZoneInfo("Asia/Shanghai")).date())
      if (
        row.stock_code not in self.request.instruments
        or not self.request.start <= row.time <= self.request.end
        or day in seen
        or previous is not None
        and key <= previous
      ):
        raise ValueError("daily bars contain duplicate, unordered or out-of-scope rows")
      seen.add(day)
      previous = key
    return self


class LatestDailyDateRequest(BaseModel):
  model_config = ConfigDict(extra="forbid")
  instrument: InstrumentCode


class LatestDailyDateResult(BaseModel):
  model_config = ConfigDict(extra="forbid")
  request: LatestDailyDateRequest
  trading_date: date | None
