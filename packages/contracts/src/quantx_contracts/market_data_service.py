"""Contracts for the local historical data service."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
