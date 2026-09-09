"""Bounded local factor snapshots; returned rows do not certify source coverage."""

from datetime import date
from decimal import Decimal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

MAX_FACTOR_WINDOW_ROWS = 512


class DividFactorRead(BaseModel):
  model_config = ConfigDict(extra="forbid")
  instrument: str = Field(pattern=r"^[A-Z0-9]{1,16}\.(SH|SZ|BJ)$")
  start_date: date = Field(ge=date(1970, 1, 1))
  end_date: date = Field(ge=date(1970, 1, 1))

  @model_validator(mode="after")
  def bounded_window(self):
    if not 0 <= (self.end_date - self.start_date).days < 366:
      raise ValueError("factor window must contain between 1 and 366 calendar days")
    return self


class DividFactorRow(BaseModel):
  model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
  stock_code: str = Field(pattern=r"^[A-Z0-9]{1,16}\.(SH|SZ|BJ)$")
  time: AwareDatetime
  ex_date: date
  interest: Decimal
  stock_bonus: Decimal
  stock_gift: Decimal
  allot_num: Decimal
  allot_price: Decimal
  gugai: Decimal
  dr: Decimal = Field(gt=0)


class DividFactorWindow(BaseModel):
  model_config = ConfigDict(extra="forbid")
  request: DividFactorRead
  records: list[DividFactorRow] = Field(max_length=MAX_FACTOR_WINDOW_ROWS)

  @model_validator(mode="after")
  def exact_scope(self):
    previous = None
    for row in self.records:
      if (
        row.stock_code != self.request.instrument
        or not self.request.start_date <= row.ex_date <= self.request.end_date
        or row.time.astimezone(ZoneInfo("Asia/Shanghai")).date() != row.ex_date
        or (previous is not None and row.ex_date <= previous)
      ):
        raise ValueError(
          "factor window contains duplicate, unordered or out-of-scope rows"
        )
      previous = row.ex_date
    return self
