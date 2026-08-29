from __future__ import annotations

import asyncio
import threading

import pytest
from quantx_qmt_agent.runtime import (
  JOURNAL_PRIORITY_CANCEL,
  JOURNAL_PRIORITY_COMMAND,
  _JournalPriorityWorker,
)


@pytest.mark.asyncio
async def test_cancel_and_emergency_overtake_queued_normal_journal_work() -> None:
  worker = _JournalPriorityWorker()
  active_started = threading.Event()
  release_active = threading.Event()
  calls: list[str] = []

  def operation(name: str, *, block: bool = False) -> str:
    calls.append(name)
    if block:
      active_started.set()
      assert release_active.wait(timeout=2)
    return name

  active = asyncio.create_task(
    worker.execute(
      operation,
      "active-normal",
      priority=JOURNAL_PRIORITY_COMMAND,
      block=True,
    )
  )
  assert await asyncio.to_thread(active_started.wait, 1)
  normal_one = asyncio.create_task(
    worker.execute(
      operation,
      "normal-1",
      priority=JOURNAL_PRIORITY_COMMAND,
    )
  )
  normal_two = asyncio.create_task(
    worker.execute(
      operation,
      "normal-2",
      priority=JOURNAL_PRIORITY_COMMAND,
    )
  )
  cancel = asyncio.create_task(
    worker.execute(
      operation,
      "cancel",
      priority=JOURNAL_PRIORITY_CANCEL,
    )
  )
  emergency = asyncio.create_task(
    worker.execute(
      operation,
      "emergency",
      priority=JOURNAL_PRIORITY_CANCEL,
    )
  )

  release_active.set()
  assert await asyncio.gather(
    active,
    normal_one,
    normal_two,
    cancel,
    emergency,
  ) == ["active-normal", "normal-1", "normal-2", "cancel", "emergency"]
  assert calls == [
    "active-normal",
    "cancel",
    "emergency",
    "normal-1",
    "normal-2",
  ]
  worker.close()
