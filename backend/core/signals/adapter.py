"""Intent adapter helpers for data and indicator callbacks."""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

from core.indicators import IndicatorBase
from core.strategies.base import TradeIntent
from models.kline import KLine
from models.tick import Tick


class DataMode(ABC):
  @abstractmethod
  async def start(self) -> None:
    pass

  @abstractmethod
  async def stop(self) -> None:
    pass


class PullMode(DataMode):
  def __init__(self, data_source: Callable, interval_seconds: int = 1):
    self.data_source = data_source
    self.interval_seconds = interval_seconds
    self.is_running = False
    self.task: Optional[asyncio.Task] = None
    self.logger = logging.getLogger("PullMode")

  async def start(self) -> None:
    if self.is_running:
      return
    self.is_running = True
    self.task = asyncio.create_task(self._pull_loop())

  async def stop(self) -> None:
    self.is_running = False
    if self.task:
      self.task.cancel()
      try:
        await self.task
      except asyncio.CancelledError:
        pass

  async def _pull_loop(self) -> None:
    while self.is_running:
      try:
        await self.data_source()
        await asyncio.sleep(self.interval_seconds)
      except Exception as exc:
        self.logger.error(f"拉取数据失败: {exc}")
        await asyncio.sleep(self.interval_seconds)


class PushMode(DataMode):
  def __init__(self, subscription_manager: Any):
    self.subscription_manager = subscription_manager
    self.subscribers: Dict[str, List[Callable]] = {}
    self.is_running = False
    self.logger = logging.getLogger("PushMode")

  async def start(self) -> None:
    self.is_running = True

  async def stop(self) -> None:
    self.is_running = False
    self.subscribers.clear()

  def subscribe(self, instrument_code: str, callback: Callable) -> None:
    self.subscribers.setdefault(instrument_code, []).append(callback)

  def unsubscribe(self, instrument_code: str, callback: Callable) -> None:
    callbacks = self.subscribers.get(instrument_code)
    if not callbacks:
      return
    try:
      callbacks.remove(callback)
    except ValueError:
      return
    if not callbacks:
      self.subscribers.pop(instrument_code, None)

  async def push_data(self, instrument_code: str, data: Any) -> None:
    for callback in self.subscribers.get(instrument_code, []):
      if asyncio.iscoroutinefunction(callback):
        await callback(data)
      else:
        callback(data)


class IntentAdapter:
  """Connect data/indicator callbacks with TradeIntent consumers."""

  def __init__(self, mode: DataMode):
    self.mode = mode
    self.indicators: Dict[str, IndicatorBase] = {}
    self.intent_callbacks: List[Callable[[TradeIntent], None]] = []
    self.data_callbacks: List[Callable[[KLine], None]] = []
    self.logger = logging.getLogger("IntentAdapter")

  def register_indicator(self, name: str, indicator: IndicatorBase) -> None:
    self.indicators[name] = indicator

  def subscribe_intents(self, callback: Callable[[TradeIntent], None]) -> None:
    self.intent_callbacks.append(callback)

  def subscribe_data(self, callback: Callable[[KLine], None]) -> None:
    self.data_callbacks.append(callback)

  async def process_bar_data(self, bar: KLine) -> Dict[str, Any]:
    results = {}
    for name, indicator in self.indicators.items():
      indicator_value = indicator.update(bar)
      if indicator_value:
        results[name] = indicator_value.value

    for callback in self.data_callbacks:
      if asyncio.iscoroutinefunction(callback):
        await callback(bar)
      else:
        callback(bar)
    return results

  async def process_tick_data(self, tick: Tick) -> None:
    return None

  async def emit_intent(self, intent: TradeIntent) -> None:
    for callback in self.intent_callbacks:
      if asyncio.iscoroutinefunction(callback):
        await callback(intent)
      else:
        callback(intent)

  async def start(self) -> None:
    await self.mode.start()

  async def stop(self) -> None:
    await self.mode.stop()

  def get_indicator_values(self, name: str, count: int = 1) -> List[Any]:
    indicator = self.indicators.get(name)
    if not indicator:
      return []
    return [value.value for value in indicator.get_values(count)]

  def get_current_indicator_value(self, name: str) -> Any:
    indicator = self.indicators.get(name)
    return indicator.get_current_value() if indicator else None

  def reset_indicators(self) -> None:
    for indicator in self.indicators.values():
      indicator.reset()

  def get_statistics(self) -> Dict[str, Any]:
    return {
      "mode": type(self.mode).__name__,
      "indicators_count": len(self.indicators),
      "intent_callbacks": len(self.intent_callbacks),
      "data_callbacks": len(self.data_callbacks),
    }
