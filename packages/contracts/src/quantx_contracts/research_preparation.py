"""Validated inputs shared by API, Worker and isolated Research jobs."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ResearchPreparationConfig(BaseModel):
  model_config = ConfigDict(extra="forbid", frozen=True)

  date_start: date
  date_end: date
  stock_codes: list[str] = Field(default_factory=list, max_length=10000)
  benchmark_code: str = Field(default="000300.SH", pattern=r"^\d{6}\.(SH|SZ)$")
  minimum_listing_days: int = Field(default=252, ge=252, le=10000)
  st_file: str | None = Field(
    default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.(csv|parquet)$"
  )
  industry_file: str | None = Field(
    default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.(csv|parquet)$"
  )
  delisting_file: str | None = Field(
    default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.(csv|parquet)$"
  )

  @model_validator(mode="after")
  def validate_range(self):
    import re

    if self.date_end < self.date_start or self.date_end > date.today():
      raise ValueError("日期范围无效或结束日期晚于今天")
    if len(set(self.stock_codes)) != len(self.stock_codes) or any(
      not re.fullmatch(r"(?:60\d{4}|68\d{4})\.SH|(?:00\d{4}|30\d{4})\.SZ", code)
      for code in self.stock_codes
    ):
      raise ValueError("股票代码必须为不重复的沪深普通 A 股代码")
    return self


PreparationKind = Literal["COVERAGE", "DOWNLOAD", "CERTIFY", "GPU"]
