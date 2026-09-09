"""Contracts for the local historical data service."""

from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class HistoryDemand(BaseModel):
  model_config = ConfigDict(extra="forbid")
  instrument: str = Field(pattern=r"^[A-Z0-9]{1,16}\.(SH|SZ|BJ)$")
  period: Literal["tick", "1m", "1d"]
  trading_date: date
  adjustment: Literal["none"] = "none"

  def agent_payload(self) -> dict:
    day = self.trading_date.strftime("%Y%m%d")
    return {
      "operation": "bars",
      "download": True,
      "stock_list": [self.instrument],
      "periods": [self.period],
      "start_time": day,
      "end_time": day,
    }


class ResumeHistory(BaseModel):
  model_config = ConfigDict(extra="forbid")
  reason: str = Field(min_length=1, max_length=256, pattern=r"\S")


class HistoryDemandAccepted(BaseModel):
  demand_id: str = Field(pattern=r"^[0-9a-f]{64}$")


class HistoryDemandStatus(HistoryDemandAccepted):
  partition: HistoryDemand
  source_kind: Literal["AGENT", "REMOTE"]
  state: Literal["WAITING_SOURCE", "LINKED"]
  source_request_id: str | None
  delivery_id: str | None
  source_status: str | None
  source_phase: str | None
  delivery_status: str | None
  reason_code: str | None
  next_probe_at: AwareDatetime | None
  created_at: AwareDatetime
  last_progress_at: AwareDatetime
  observed_at: AwareDatetime


class HistoryDemandResult(HistoryDemandAccepted):
  """Published local delivery proof; no source files or mutable progress fields."""

  model_config = ConfigDict(extra="forbid")
  partition: HistoryDemand
  delivery_id: str = Field(pattern=r"^[0-9a-f]{64}$")
  source_version: str = Field(pattern=r"^[0-9a-f]{64}$")
  storage_version: str = Field(pattern=r"^[0-9a-f]{64}$")
  content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
  records_verified: int = Field(strict=True, gt=0)
  verified_at: AwareDatetime


class HistoryRead(HistoryDemand):
  """One local partition, ascending storage-time keyset; no implicit backfill."""

  trading_date: date = Field(ge=date(1970, 1, 2), le=date(9999, 12, 30))
  page_size: int = Field(default=1000, ge=1, le=2000)
  after: AwareDatetime | None = None

  def bounds(self) -> tuple[datetime, datetime]:
    start = datetime.combine(self.trading_date, time(), ZoneInfo("Asia/Shanghai"))
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(
      timezone.utc
    )

  @model_validator(mode="after")
  def validate_cursor(self):
    start, end = self.bounds()
    if self.after is not None and not start <= self.after < end:
      raise ValueError("history cursor must belong to the requested trading date")
    return self


class HistoryPage(BaseModel):
  """Storage rows, including flattened Tick depth fields; no completeness proof.

  Every nonempty page supplies a cursor, even a short page. Only an empty
  probe ends pagination. This says nothing about upstream history coverage.
  """

  records: list[dict]
  next_after: AwareDatetime | None
  exhausted: bool
