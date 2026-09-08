"""Local history stage measurements, without payloads or device details."""

import logging
import time
from contextvars import ContextVar

logger = logging.getLogger(__name__)
history_unit: ContextVar[tuple[str, int]] = ContextVar("history_unit", default=("", 0))


def record_history_timing(
  stage: str,
  started: float,
  **measurements: float | str | None,
) -> None:
  request_id, unit_index = history_unit.get()
  logger.info(
    "history_timing request_id=%s unit_index=%s stage=%s elapsed_ms=%.3f metrics=%s",
    request_id,
    unit_index,
    stage,
    (time.monotonic() - started) * 1000,
    measurements,
  )
