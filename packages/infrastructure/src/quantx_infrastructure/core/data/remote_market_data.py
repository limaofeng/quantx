"""Redis subscription bridge used by server processes.

QMT connectivity belongs to ``apps/qmt-agent``. API and Engine consumers only
observe normalized events and fall back to PostgreSQL for durable snapshots.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import Any, Callable

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.redis_pubsub import redis_pubsub

logger = logging.getLogger(__name__)
MARKET_DATA_CONTROL_CHANNEL = "market-data:control"
MARKET_DATA_ACTIVE_SUBSCRIPTIONS = "market-data:active-subscriptions"


class RemoteMarketDataManager:
  def __init__(self) -> None:
    self._ids = itertools.count(1)
    self._tasks: dict[int, asyncio.Task] = {}
    self._controls: dict[int, dict[str, Any]] = {}
    self.is_connected = True

  def _init_connection(self) -> None:
    """Compatibility no-op: subscriptions open Redis connections lazily."""
    self.is_connected = True

  @staticmethod
  async def _publish_control(control: dict[str, Any]) -> None:
    subscription_id = str(control["subscription_id"])
    redis = await redis_pubsub.get_redis()
    if control["action"] == "SUBSCRIBE":
      await redis.hset(
        MARKET_DATA_ACTIVE_SUBSCRIPTIONS,
        subscription_id,
        json.dumps(control, ensure_ascii=False, default=str),
      )
    else:
      await redis.hdel(MARKET_DATA_ACTIVE_SUBSCRIPTIONS, subscription_id)
    await redis_pubsub.publish(MARKET_DATA_CONTROL_CHANNEL, control)

  @staticmethod
  def _schedule_control(control: dict[str, Any]) -> None:
    try:
      loop = asyncio.get_running_loop()
    except RuntimeError:
      logger.warning(
        "Market-data control skipped without a running event loop: action=%s",
        control.get("action"),
      )
      return
    task = loop.create_task(RemoteMarketDataManager._publish_control(control))

    def log_failure(done: asyncio.Task) -> None:
      if done.cancelled():
        return
      error = done.exception()
      if error is not None:
        logger.warning(
          "Market-data control publish failed: action=%s error=%s",
          control.get("action"),
          error.__class__.__name__,
        )

    task.add_done_callback(log_failure)

  def _subscribe(
    self,
    channel: str,
    callback: Callable,
    control: dict[str, Any],
  ) -> int:
    subscription_id = next(self._ids)
    control = {
      **control,
      "action": "SUBSCRIBE",
      "subscription_id": str(subscription_id),
    }

    async def consume() -> None:
      async for message in redis_pubsub.subscribe(channel):
        try:
          result = callback(message)
          if asyncio.iscoroutine(result):
            await result
        except Exception as exc:
          logger.warning(
            "Market-data subscriber failed: channel=%s error=%s",
            channel,
            exc.__class__.__name__,
          )

    self._tasks[subscription_id] = asyncio.create_task(consume())
    self._controls[subscription_id] = control
    self._schedule_control(control)
    return subscription_id

  def subscribe_quote(
    self,
    stock_code: str,
    *,
    period: str,
    count: int,
    callback: Callable,
  ) -> int:
    control = {
      "kind": "quote",
      "stock_code": stock_code,
      "period": period,
      "count": count,
    }
    if period == "1m" and count == -1:
      trading_date = time_utils.today().strftime("%Y%m%d")
      control.update(
        {
          "start_time": f"{trading_date}000000",
          "end_time": f"{trading_date}235959",
        }
      )
    return self._subscribe(
      f"market-data:{stock_code}:{period}",
      callback,
      control,
    )

  def unsubscribe_quote(self, subscription_id: int) -> None:
    self._cancel(subscription_id)

  def _cancel(self, subscription_id: int) -> None:
    control = self._controls.pop(subscription_id, None)
    if control is not None:
      self._schedule_control(
        {
          **control,
          "action": "UNSUBSCRIBE",
        }
      )
    task = self._tasks.pop(subscription_id, None)
    if task is not None:
      task.cancel()

  def close_connection(self) -> None:
    for subscription_id in list(self._tasks):
      self._cancel(subscription_id)
    self.is_connected = False

  def get_market_data(self, **_: Any) -> dict[str, Any]:
    """Fail closed for obsolete synchronous bulk reads.

    Bulk history is requested through the durable market-data message box and
    consumed by the Worker after an Agent upload converges.
    """
    return {}

  def get_instrument_detail(self, _: str) -> None:
    """Instrument metadata is resolved from PostgreSQL."""
    return None


class RemoteMarketDataRegistry:
  _manager = RemoteMarketDataManager()

  def get_manager(self) -> RemoteMarketDataManager:
    return self._manager

  def clear_all_managers(self) -> None:
    self._manager.close_connection()
