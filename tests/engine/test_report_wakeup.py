import asyncio
from typing import Optional

import pytest
from quantx_engine import report_processor


class FakeSubscription:
  def __init__(self, *, error: Optional[Exception] = None):
    self.error = error
    self.waited = False
    self.closed = False

  async def wait_for_message(self, *, timeout: float):
    self.waited = True
    assert timeout == 1.0
    if self.error is not None:
      raise self.error
    return {"message_id": "report-1"}

  async def close(self):
    self.closed = True


@pytest.mark.asyncio
async def test_report_consumer_uses_redis_only_as_a_wakeup() -> None:
  subscription = FakeSubscription()

  result = await report_processor._wait_for_work(
    asyncio.Event(),
    subscription,
  )

  assert result is subscription
  assert subscription.waited


@pytest.mark.asyncio
async def test_report_consumer_falls_back_to_database_polling() -> None:
  subscription = FakeSubscription(error=ConnectionError("offline"))

  result = await report_processor._wait_for_work(
    asyncio.Event(),
    subscription,
  )

  assert result is None
  assert subscription.closed
