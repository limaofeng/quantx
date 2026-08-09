"""Engine-side producer for API GraphQL subscriptions."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from enum import Enum
from typing import Any, AsyncIterator

from quantx_domain.clock import utcnow
from quantx_infrastructure.database.redis_pubsub import redis_pubsub
from quantx_infrastructure.services.runtime_market_query_bridge import (
  RUNTIME_MARKET_QUERY_CONTROL,
)
from quantx_infrastructure.services.runtime_subscription_bridge import (
  RUNTIME_SUBSCRIPTION_CONTROL,
)

from quantx_engine.realtime_manager import realtime_manager
from quantx_engine.strategy_manager import strategy_manager


def _json_value(value: Any) -> Any:
  if isinstance(value, Enum):
    return value.value
  if isinstance(value, (datetime, date)):
    return value.isoformat()
  if isinstance(value, dict):
    return {str(key): _json_value(item) for key, item in value.items()}
  if isinstance(value, (list, tuple, set)):
    return [_json_value(item) for item in value]
  if hasattr(value, "model_dump"):
    return _json_value(value.model_dump())
  if hasattr(value, "dict"):
    return _json_value(value.dict())
  if hasattr(value, "__dict__"):
    return _json_value(
      {
        key: item
        for key, item in vars(value).items()
        if not key.startswith("_")
      }
    )
  return value


async def _market_stream(control: dict[str, Any]) -> AsyncIterator[Any]:
  stream_type = str(control["stream_type"])
  symbol = str(control.get("symbol") or "")
  if stream_type == "market-price":
    async for item in realtime_manager.subscribe_price(symbol):
      yield item
    return
  if stream_type == "market-kline":
    async for item in realtime_manager.subscribe_kline(
      symbol,
      str(control.get("period") or "1m"),
    ):
      yield item
    return
  if stream_type == "market-depth":
    async for item in realtime_manager.subscribe_depth(symbol):
      yield item
    return
  if stream_type == "market-tick":
    async for item in realtime_manager.subscribe_tick(symbol):
      yield item
    return
  raise ValueError(f"未知行情订阅类型: {stream_type}")


async def _strategy_stream(control: dict[str, Any]) -> AsyncIterator[Any]:
  run_id = str(control.get("run_id") or "")
  runtime = strategy_manager.get_run(run_id)
  if runtime is None:
    raise ValueError(f"策略实例不存在或未加载: {run_id}")
  stream_type = str(control["stream_type"])

  if stream_type == "strategy-events":
    yield {
      "event_type": "SNAPSHOT",
      "timestamp": utcnow(),
      "payload": {
        "status": runtime.status.value,
        "mode": runtime.mode.value,
        "instruments": list(runtime.instruments or []),
      },
    }
    queue = runtime.subscribe_logs(include_history=False)
    try:
      while True:
        try:
          entry = await asyncio.wait_for(queue.get(), timeout=10.0)
          yield {
            "event_type": "LOG",
            "timestamp": entry.timestamp,
            "payload": {
              "level": getattr(entry.level, "value", entry.level),
              "message": entry.message,
              "source": entry.source,
            },
          }
        except asyncio.TimeoutError:
          yield {
            "event_type": "HEARTBEAT",
            "timestamp": utcnow(),
            "payload": {"status": runtime.status.value},
          }
    finally:
      runtime.unsubscribe_logs(queue)

  if stream_type == "strategy-logs":
    queue = runtime.subscribe_logs(
      include_history=bool(control.get("include_history", False))
    )
    try:
      while True:
        yield await queue.get()
    finally:
      runtime.unsubscribe_logs(queue)

  data_type = {
    "strategy-ticks": "tick",
    "strategy-klines": "kline",
  }.get(stream_type)
  if data_type is None:
    raise ValueError(f"未知策略订阅类型: {stream_type}")
  queue = runtime.subscribe_data(data_type, include_recent=False)
  try:
    while True:
      received_type, data = await queue.get()
      if received_type == data_type:
        yield data
  finally:
    runtime.unsubscribe_data(queue)


async def _publish_stream(control: dict[str, Any]) -> None:
  subscription_id = str(control["subscription_id"])
  channel = str(control["channel"])
  try:
    if str(control["stream_type"]).startswith("market-"):
      stream = _market_stream(control)
    else:
      stream = _strategy_stream(control)
    async for item in stream:
      await redis_pubsub.publish(
        channel,
        {
          "subscription_id": subscription_id,
          "event": "DATA",
          "payload": _json_value(item),
        },
      )
  except asyncio.CancelledError:
    raise
  except Exception as exc:
    await redis_pubsub.publish(
      channel,
      {
        "subscription_id": subscription_id,
        "event": "ERROR",
        "error": str(exc),
      },
    )


async def run_subscription_bridge(stopped: asyncio.Event) -> None:
  streams: dict[str, asyncio.Task] = {}
  while not stopped.is_set():
    subscription = None
    try:
      subscription = await redis_pubsub.open_subscription(
        RUNTIME_SUBSCRIPTION_CONTROL
      )
      async for control in subscription.messages():
        if stopped.is_set():
          break
        subscription_id = str(control.get("subscription_id") or "")
        if not subscription_id:
          continue
        existing = streams.pop(subscription_id, None)
        if existing is not None:
          existing.cancel()
        if control.get("action") == "SUBSCRIBE":
          task = asyncio.create_task(_publish_stream(control))
          streams[subscription_id] = task
          task.add_done_callback(
            lambda _task, key=subscription_id: streams.pop(key, None)
          )
    except asyncio.CancelledError:
      raise
    except Exception:
      try:
        await asyncio.wait_for(stopped.wait(), timeout=1.0)
      except asyncio.TimeoutError:
        pass
    finally:
      if subscription is not None:
        await subscription.close()
  for task in streams.values():
    task.cancel()
  if streams:
    await asyncio.gather(*streams.values(), return_exceptions=True)


def _parse_datetime(value: Any) -> datetime | None:
  if value is None or isinstance(value, datetime):
    return value
  if isinstance(value, str):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
  return None


async def _market_query_result(query: dict[str, Any]) -> dict[str, Any]:
  from quantx_engine.warm_cache import (
    intraday_warm_cache,
  )

  operation = str(query.get("operation") or "")
  payload = dict(query.get("payload") or {})
  if operation == "warm_klines":
    values = intraday_warm_cache.get_klines(
      str(payload.get("stock_code") or ""),
      start_time=_parse_datetime(payload.get("start_time")),
      end_time=_parse_datetime(payload.get("end_time")),
    )
    return {"items": _json_value(values)}
  if operation == "warm_ticks":
    values = intraday_warm_cache.get_ticks(
      str(payload.get("stock_code") or ""),
      start_time=_parse_datetime(payload.get("start_time")),
      end_time=_parse_datetime(payload.get("end_time")),
    )
    return {"items": _json_value(values)}
  if operation == "latest_ticks":
    items = {}
    for stock_code in payload.get("stock_codes") or []:
      values = intraday_warm_cache.get_ticks(str(stock_code))
      if values:
        items[str(stock_code)] = _json_value(values[-1])
    return {"items": items}
  raise ValueError(f"未知 Engine 行情查询: {operation}")


async def run_market_query_bridge(stopped: asyncio.Event) -> None:
  while not stopped.is_set():
    subscription = None
    try:
      subscription = await redis_pubsub.open_subscription(
        RUNTIME_MARKET_QUERY_CONTROL
      )
      async for query in subscription.messages():
        if stopped.is_set():
          break
        request_id = str(query.get("request_id") or "")
        response_channel = str(query.get("response_channel") or "")
        if not request_id or not response_channel:
          continue
        try:
          result = await _market_query_result(query)
          response = {
            "request_id": request_id,
            "ok": True,
            "result": result,
          }
        except Exception as exc:
          response = {
            "request_id": request_id,
            "ok": False,
            "error": str(exc),
          }
        await redis_pubsub.publish(response_channel, response)
    except asyncio.CancelledError:
      raise
    except Exception:
      try:
        await asyncio.wait_for(stopped.wait(), timeout=1.0)
      except asyncio.TimeoutError:
        pass
    finally:
      if subscription is not None:
        await subscription.close()
