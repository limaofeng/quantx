"""Transport-neutral strategy runtime log records."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from quantx_domain.clock import now


class RuntimeLogLevel(Enum):
  DEBUG = "DEBUG"
  INFO = "INFO"
  SUCCESS = "SUCCESS"
  WARNING = "WARNING"
  ERROR = "ERROR"


@dataclass(frozen=True)
class RuntimeLogEntry:
  run_id: str
  timestamp: datetime
  level: RuntimeLogLevel
  message: str
  source: str

  @classmethod
  def create(
    cls,
    *,
    run_id: str,
    level: RuntimeLogLevel,
    message: str,
    source: str = "strategy",
  ) -> "RuntimeLogEntry":
    return cls(
      run_id=run_id,
      timestamp=now(),
      level=level,
      message=message,
      source=source,
    )
