"""Single local subscription facade over WholeQuoteHub and QMT K-lines."""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Callable

from quantx_infrastructure.core.data.remote_market_data import (
  RemoteMarketDataRegistry,
)
from quantx_infrastructure.core.data.whole_quote_hub import (
  QuoteDeliveryMode,
  WholeQuoteHub,
  whole_quote_hub,
)

logger = logging.getLogger(__name__)


@dataclass
class _Handle:
  owner: str
  stock_code: str
  period: str
  callback: Callable
  hub_handle: str = ""


@dataclass
class _PeriodSubscription:
  stock_code: str
  period: str
  remote_subscription_id: int
  callbacks: dict[str, Callable] = field(default_factory=dict)


class UnifiedDataSubscriptionManager:
  """Deduplicate K-lines and filter the one process-wide tick stream locally."""

  _instance = None
  _lock = Lock()

  def __new__(cls):
    if cls._instance is None:
      with cls._lock:
        if cls._instance is None:
          cls._instance = super().__new__(cls)
    return cls._instance

  def __init__(self) -> None:
    if hasattr(self, "_initialized"):
      return
    self._initialized = True
    self._main_loop: asyncio.AbstractEventLoop | None = None
    self.data_manager_registry = RemoteMarketDataRegistry()
    self._data_manager = None
    self.hub: WholeQuoteHub = whole_quote_hub
    self._handles: dict[str, _Handle] = {}
    self._owner_handles: dict[str, set[str]] = {}
    self._period_subscriptions: dict[str, _PeriodSubscription] = {}

  @property
  def data_manager(self):
    if self._data_manager is None:
      self._data_manager = self.data_manager_registry.get_manager()
    return self._data_manager

  @property
  def latest_tick_data(self) -> dict[str, dict[str, Any]]:
    return self.hub.snapshot()

  def set_main_loop(
    self,
    loop: asyncio.AbstractEventLoop | None = None,
  ) -> bool:
    try:
      self._main_loop = loop or asyncio.get_running_loop()
    except RuntimeError:
      return False
    return True

  async def subscribe(
    self,
    stock_code: str,
    callback: Callable,
    subscriber_id: str,
    period: str = "tick",
    *,
    latest_only: bool = False,
  ) -> str:
    if callback is None:
      raise ValueError("callback cannot be None")
    handle = str(uuid.uuid4())
    record = _Handle(
      owner=str(subscriber_id),
      stock_code=str(stock_code),
      period=str(period),
      callback=callback,
    )
    if period == "tick":
      record.hub_handle = await self.hub.subscribe_tick(
        stock_code,
        callback,
        delivery=(
          QuoteDeliveryMode.LATEST_ONLY
          if latest_only
          else QuoteDeliveryMode.CRITICAL
        ),
      )
    else:
      await self._subscribe_period(handle, record)
    self._handles[handle] = record
    self._owner_handles.setdefault(record.owner, set()).add(handle)
    return handle

  async def _subscribe_period(self, handle: str, record: _Handle) -> None:
    key = f"{record.stock_code}:{record.period}"
    shared = self._period_subscriptions.get(key)
    if shared is None:

      async def dispatch(data: Any) -> None:
        current = self._period_subscriptions.get(key)
        if current is None:
          return
        for callback in tuple(current.callbacks.values()):
          try:
            result = callback(data)
            if inspect.isawaitable(result):
              await result
          except Exception:
            logger.exception("K-line callback failed: key=%s", key)

      remote_id = self.data_manager.subscribe_quote(
        record.stock_code,
        period=record.period,
        count=-1,
        callback=dispatch,
      )
      if remote_id is None:
        raise RuntimeError(
          f"remote K-line subscription failed: {record.stock_code} {record.period}"
        )
      shared = _PeriodSubscription(
        stock_code=record.stock_code,
        period=record.period,
        remote_subscription_id=int(remote_id),
      )
      self._period_subscriptions[key] = shared
    shared.callbacks[handle] = record.callback

  async def unsubscribe(self, handle: str) -> bool:
    handle = str(handle)
    record = self._handles.get(handle)
    if record is None:
      return True

    if record.period == "tick":
      if await self.hub.unsubscribe(record.hub_handle) is not True:
        return False
    else:
      key = f"{record.stock_code}:{record.period}"
      shared = self._period_subscriptions.get(key)
      if shared is not None and handle in shared.callbacks:
        if len(shared.callbacks) == 1:
          # The remote ownership stays discoverable until cancellation has
          # returned without error, so the same handle can retry safely.
          self.data_manager.unsubscribe_quote(shared.remote_subscription_id)
          self._period_subscriptions.pop(key, None)
        else:
          shared.callbacks.pop(handle, None)

    self._handles.pop(handle, None)
    owner_handles = self._owner_handles.get(record.owner)
    if owner_handles is not None:
      owner_handles.discard(handle)
      if not owner_handles:
        self._owner_handles.pop(record.owner, None)
    return True

  async def unsubscribe_all(self, subscriber_id: str) -> bool:
    handles = tuple(self._owner_handles.get(str(subscriber_id), ()))
    removed = True
    for handle in handles:
      removed = await self.unsubscribe(handle) and removed
    return removed

  def get_latest_tick(self, stock_code: str) -> dict[str, Any] | None:
    return self.hub.latest(stock_code)

  async def shutdown(self) -> None:
    for handle in tuple(self._handles):
      await self.unsubscribe(handle)
    if self._data_manager is not None:
      self._data_manager.close_connection()
      self._data_manager = None
    self._main_loop = None

  def get_subscription_stats(self) -> dict[str, Any]:
    tick_handles = [
      handle
      for handle, record in self._handles.items()
      if record.period == "tick"
    ]
    return {
      "whole_quote": self.hub.status_snapshot(),
      "total_tick_subscriptions": len(tick_handles),
      "total_period_subscriptions": len(self._period_subscriptions),
      "total_handles": len(self._handles),
      "owners": {
        owner: len(handles) for owner, handles in self._owner_handles.items()
      },
    }

  def is_subscribed(self, stock_code: str, period: str = "tick") -> bool:
    return any(
      record.stock_code == stock_code and record.period == period
      for record in self._handles.values()
    )


unified_subscription_manager = UnifiedDataSubscriptionManager()


__all__ = [
  "UnifiedDataSubscriptionManager",
  "unified_subscription_manager",
]
