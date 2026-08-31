"""Keep resource cleanup owned until it finishes, even during repeated cancellation."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any


async def finish_cleanup(cleanup: Coroutine[Any, Any, None]) -> None:
  task = asyncio.create_task(cleanup)
  cancelled: asyncio.CancelledError | None = None
  try:
    while not task.done():
      try:
        await asyncio.shield(task)
      except asyncio.CancelledError as exc:
        if task.cancelled():
          task.result()
        cancelled = exc
    task.result()
  finally:
    # Shield alone leaves cleanup running in the background. Keep joining the
    # same task, then propagate cancellation rather than reporting success.
    if cancelled is not None:
      raise cancelled
