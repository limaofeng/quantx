"""Stage timings without market payloads, endpoints, or device details."""

import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

logger = logging.getLogger(__name__)
market_data_request_id: ContextVar[str] = ContextVar(
  "market_data_request_id", default=""
)


@contextmanager
def market_data_stage(stage: str, **counts: int | str) -> Iterator[None]:
  started = time.monotonic()
  outcome = "ok"
  try:
    yield
  except BaseException as exc:
    outcome = type(exc).__name__
    raise
  finally:
    logger.info(
      "market_data_timing request_id=%s stage=%s elapsed_ms=%.3f outcome=%s counts=%s",
      market_data_request_id.get(),
      stage,
      (time.monotonic() - started) * 1000,
      outcome,
      counts,
    )
