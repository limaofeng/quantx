"""Shared policy for development-triggered historical downloads."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class HistoryDownloadMode(str, Enum):
  ALWAYS = "ALWAYS"
  CUSTOM = "CUSTOM"


class HistoryDownloadWindow(BaseModel):
  model_config = ConfigDict(extra="forbid")
  start: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
  end: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")

  @model_validator(mode="after")
  def distinct_bounds(self):
    if self.start == self.end:
      raise ValueError("时段起止时间不能相同；全天开放请选择全天允许")
    return self

  def contains(self, clock: str) -> bool:
    if self.start < self.end:
      return self.start <= clock < self.end
    return clock >= self.start or clock < self.end


class HistoryDownloadPolicy(BaseModel):
  model_config = ConfigDict(extra="forbid")
  mode: HistoryDownloadMode = HistoryDownloadMode.ALWAYS
  non_trading_days_allowed: bool = True
  windows: list[HistoryDownloadWindow] = Field(default_factory=list, max_length=12)

  @model_validator(mode="after")
  def valid_windows(self):
    if self.mode == HistoryDownloadMode.CUSTOM and not self.windows:
      raise ValueError("自定义模式至少需要一个允许时段")
    for index, window in enumerate(self.windows):
      for other in self.windows[:index]:
        if window.contains(other.start) or other.contains(window.start):
          raise ValueError("允许时段不能重叠")
    return self
