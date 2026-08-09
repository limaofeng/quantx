"""
Engine-owned intraday warm cache for actively watched symbols.

The service owns proactive tick/1m subscriptions for holdings, watchlist items,
and symbols opened by charts. Initial data is converged asynchronously through
durable Agent/Worker transfers; query paths never invoke a local trading terminal.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time
from threading import Lock
from typing import Dict, Iterable, List, Optional, Set

from quantx_infrastructure.core.data.unified_subscription_manager import (
  unified_subscription_manager,
)
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.kline import KLine
from quantx_infrastructure.models.tick import Tick
from quantx_infrastructure.services.watchlist_service import (
  DEFAULT_ACCOUNT_ID,
  normalize_stock_code,
)

logger = logging.getLogger(__name__)
INITIAL_DOWNLOAD_SOURCES = {"holding", "watchlist"}


@dataclass
class WarmSymbolState:
  stock_code: str
  sources: Set[str] = field(default_factory=set)
  first_added_at: datetime = field(default_factory=time_utils.now)
  last_tick_at: Optional[datetime] = None
  last_kline_at: Optional[datetime] = None
  initialized_date: Optional[date] = None
  initializing: bool = False
  initialization_error: Optional[str] = None
  tick_subscribed: bool = False
  kline_subscribed: bool = False


class IntradayWarmCacheService:
  """Maintain hot intraday data for holdings, watchlist, and open charts."""

  subscriber_id = "intraday_warm_cache"

  def __init__(self):
    self.subscription_manager = unified_subscription_manager
    self._lock = Lock()
    self._states: Dict[str, WarmSymbolState] = {}
    self._ticks: Dict[str, Dict[datetime, Tick]] = {}
    self._klines: Dict[str, Dict[datetime, KLine]] = {}
    self._source_symbols: Dict[str, Set[str]] = {}
    self._initial_downloads: Set[str] = set()
    self._current_date: Optional[date] = None
    self._started = False
    self._monitor_task: Optional[asyncio.Task] = None
    self._last_data_at: Optional[datetime] = None

  async def start(self) -> None:
    if self._started:
      return
    self._started = True
    self.subscription_manager.set_main_loop(asyncio.get_running_loop())
    self._roll_trading_date()
    self._monitor_task = asyncio.create_task(self._monitor_loop())
    logger.info("Intraday warm cache started; source preloading continues in background")

  async def shutdown(self) -> None:
    self._started = False
    if self._monitor_task is not None:
      self._monitor_task.cancel()
      try:
        await self._monitor_task
      except asyncio.CancelledError:
        pass
      self._monitor_task = None
    await self._unsubscribe_all()
    logger.info("Intraday warm cache stopped")

  async def _monitor_loop(self) -> None:
    while self._started:
      try:
        self._roll_trading_date()
        if self._is_warm_window(time_utils.now()):
          await self.refresh_source_symbols()
        elif self._should_stop_after_market():
          await self._unsubscribe_all()
        await asyncio.sleep(60)
      except asyncio.CancelledError:
        raise
      except Exception as exc:
        logger.warning("Intraday warm cache monitor failed: %s", exc)
        await asyncio.sleep(60)

  async def _unsubscribe_all(self) -> None:
    await self.subscription_manager.unsubscribe_all(self.subscriber_id)
    with self._lock:
      for state in self._states.values():
        state.tick_subscribed = False
        state.kline_subscribed = False

  async def _remove_symbol(self, stock_code: str) -> None:
    await self.subscription_manager.unsubscribe(
      self.subscriber_id, stock_code, period="tick"
    )
    await self.subscription_manager.unsubscribe(
      self.subscriber_id, stock_code, period="1m"
    )
    with self._lock:
      self._states.pop(stock_code, None)
      self._ticks.pop(stock_code, None)
      self._klines.pop(stock_code, None)
      self._initial_downloads = {
        key for key in self._initial_downloads if not key.startswith(f"{stock_code}:")
      }
      for source_symbols in self._source_symbols.values():
        source_symbols.discard(stock_code)

  def _roll_trading_date(self) -> None:
    today = time_utils.today()
    with self._lock:
      if self._current_date == today:
        return
      self._current_date = today
      self._ticks.clear()
      self._klines.clear()
      self._initial_downloads = {
        key for key in self._initial_downloads if key.endswith(today.isoformat())
      }
      for state in self._states.values():
        state.initialized_date = None
        state.initialization_error = None
        state.initializing = False

  def _is_warm_window(self, now: datetime) -> bool:
    local = time_utils.to_shanghai(now)
    current = local.time()
    return time(9, 0) <= current < time(16, 0)

  def _should_stop_after_market(self) -> bool:
    now = time_utils.to_shanghai(time_utils.now())
    if now.time() < time(15, 30):
      return False
    if now.time() >= time(16, 0):
      return True
    if self._last_data_at is None:
      return now.time() >= time(15, 40)
    return (now - time_utils.to_shanghai(self._last_data_at)).total_seconds() >= 600

  async def refresh_source_symbols(self) -> None:
    holding_symbols: Set[str] = set()
    watchlist_symbols: Set[str] = set()
    try:
      from quantx_infrastructure.core.data.market_data_service import (
        market_data_service,
      )

      positions = await market_data_service.get_positions(with_latest_price=False)
      holding_symbols.update(
        normalize_stock_code(getattr(position, "stock_code", ""))
        for position in positions
        if int(getattr(position, "volume", 0) or 0) > 0
      )
    except Exception as exc:
      logger.debug("Warm cache holdings source unavailable: %s", exc)

    try:
      from quantx_infrastructure.services.watchlist_service import WatchlistService

      items = await WatchlistService().get_watchlist(DEFAULT_ACCOUNT_ID)
      watchlist_symbols.update(normalize_stock_code(item.stock_code) for item in items)
    except Exception as exc:
      logger.debug("Warm cache watchlist source unavailable: %s", exc)

    await self.replace_source_symbols("holding", holding_symbols)
    await self.replace_source_symbols("watchlist", watchlist_symbols)

  async def replace_source_symbols(
    self, source: str, symbols: Iterable[str]
  ) -> None:
    normalized_symbols = {
      normalize_stock_code(symbol) for symbol in symbols if normalize_stock_code(symbol)
    }
    with self._lock:
      previous_symbols = self._source_symbols.get(source, set())
      removed_symbols = previous_symbols - normalized_symbols
      self._source_symbols[source] = set(normalized_symbols)
      orphaned_symbols = []
      for symbol in removed_symbols:
        state = self._states.get(symbol)
        if state is None:
          continue
        state.sources.discard(source)
        if not state.sources:
          orphaned_symbols.append(symbol)

    for symbol in normalized_symbols:
      await self.ensure_symbol(symbol, source=source)
    for symbol in orphaned_symbols:
      await self._remove_symbol(symbol)

  async def ensure_symbols(self, symbols: Iterable[str], source: str = "chart") -> None:
    for symbol in symbols:
      await self.ensure_symbol(symbol, source=source)

  async def ensure_symbol(self, stock_code: str, source: str = "chart") -> None:
    self._roll_trading_date()
    normalized_code = normalize_stock_code(stock_code)
    if not normalized_code:
      return

    with self._lock:
      state = self._states.get(normalized_code)
      if state is None:
        state = WarmSymbolState(stock_code=normalized_code)
        self._states[normalized_code] = state
      state.sources.add(source)
      if source in {"holding", "watchlist"}:
        self._source_symbols.setdefault(source, set()).add(normalized_code)

    await self._ensure_subscriptions(normalized_code)
    if source in INITIAL_DOWNLOAD_SOURCES:
      await self._ensure_initial_download(normalized_code)

  async def _ensure_subscriptions(self, stock_code: str) -> None:
    async def tick_callback(data):
      await self._handle_tick_data(stock_code, data)

    async def kline_callback(data):
      await self._handle_kline_data(stock_code, "1m", data)

    with self._lock:
      state = self._states[stock_code]
      needs_tick = not state.tick_subscribed
      needs_kline = not state.kline_subscribed

    if needs_tick:
      subscribed = await self.subscription_manager.subscribe(
        stock_code=stock_code,
        callback=tick_callback,
        subscriber_id=self.subscriber_id,
        period="tick",
      )
      with self._lock:
        self._states[stock_code].tick_subscribed = bool(subscribed)

    if needs_kline:
      subscribed = await self.subscription_manager.subscribe(
        stock_code=stock_code,
        callback=kline_callback,
        subscriber_id=self.subscriber_id,
        period="1m",
      )
      with self._lock:
        self._states[stock_code].kline_subscribed = bool(subscribed)

  async def _ensure_initial_download(self, stock_code: str) -> None:
    trading_date = self._current_date or time_utils.today()
    key = f"{stock_code}:{trading_date.isoformat()}"
    with self._lock:
      state = self._states[stock_code]
      if (
        key in self._initial_downloads
        or state.initializing
        or state.initialized_date == trading_date
      ):
        return
      self._initial_downloads.add(key)
      state.initializing = True

    asyncio.create_task(self._run_initial_download(stock_code, trading_date))

  async def _run_initial_download(self, stock_code: str, trading_date: date) -> None:
    """Initial data arrives asynchronously through Agent/Worker convergence."""
    with self._lock:
      state = self._states.get(stock_code)
      if state is not None:
        state.initialized_date = trading_date
        state.initialization_error = None
        state.initializing = False

  async def _handle_tick_data(self, stock_code: str, data: Dict) -> None:
    from quantx_engine.realtime_manager import realtime_manager

    await realtime_manager._handle_xt_tick_data(stock_code, data)

  async def _handle_kline_data(self, stock_code: str, period: str, data: Dict) -> None:
    from quantx_engine.realtime_manager import realtime_manager

    await realtime_manager._handle_xt_kline_data(stock_code, period, data)

  def store_tick(self, tick: Tick) -> None:
    tick_time = getattr(tick, "time", None)
    stock_code = normalize_stock_code(getattr(tick, "stock_code", ""))
    if not stock_code or tick_time is None:
      return
    tick_time = time_utils.to_shanghai(tick_time)
    with self._lock:
      self._ticks.setdefault(stock_code, {})[tick_time] = tick
      state = self._states.get(stock_code)
      if state is not None:
        state.last_tick_at = tick_time
      self._last_data_at = tick_time

  def store_kline(self, kline: KLine) -> None:
    kline_time = getattr(kline, "time", None)
    stock_code = normalize_stock_code(getattr(kline, "stock_code", ""))
    if not stock_code or kline_time is None:
      return
    kline_time = time_utils.to_shanghai(kline_time).replace(second=0, microsecond=0)
    with self._lock:
      self._klines.setdefault(stock_code, {})[kline_time] = kline
      state = self._states.get(stock_code)
      if state is not None:
        state.last_kline_at = kline_time
      self._last_data_at = kline_time

  def get_klines(
    self,
    stock_code: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
  ) -> List[KLine]:
    normalized_code = normalize_stock_code(stock_code)
    start = time_utils.to_shanghai(start_time) if start_time else None
    end = time_utils.to_shanghai(end_time) if end_time else None
    with self._lock:
      values = list(self._klines.get(normalized_code, {}).values())
    result = []
    for kline in values:
      kline_time = time_utils.to_shanghai(kline.time)
      if start is not None and kline_time < start:
        continue
      if end is not None and kline_time > end:
        continue
      result.append(kline)
    result.sort(key=lambda item: time_utils.to_shanghai(item.time))
    return result

  def get_ticks(
    self,
    stock_code: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
  ) -> List[Tick]:
    normalized_code = normalize_stock_code(stock_code)
    start = time_utils.to_shanghai(start_time) if start_time else None
    end = time_utils.to_shanghai(end_time) if end_time else None
    with self._lock:
      values = list(self._ticks.get(normalized_code, {}).values())
    result = []
    for tick in values:
      tick_time = time_utils.to_shanghai(tick.time)
      if start is not None and tick_time < start:
        continue
      if end is not None and tick_time > end:
        continue
      result.append(tick)
    result.sort(key=lambda item: time_utils.to_shanghai(item.time))
    return result

  def get_status(self, symbols: Optional[Iterable[str]] = None) -> List[dict]:
    with self._lock:
      if symbols is None:
        codes = sorted(self._states.keys())
      else:
        codes = sorted({normalize_stock_code(symbol) for symbol in symbols if symbol})
      rows = []
      for code in codes:
        state = self._states.get(code)
        rows.append(
          {
            "stock_code": code,
            "sources": sorted(state.sources) if state else [],
            "tick_subscribed": bool(state and state.tick_subscribed),
            "kline_subscribed": bool(state and state.kline_subscribed),
            "initialized_date": state.initialized_date if state else None,
            "initializing": bool(state and state.initializing),
            "initialization_error": state.initialization_error if state else None,
            "last_tick_at": state.last_tick_at if state else None,
            "last_kline_at": state.last_kline_at if state else None,
            "tick_count": len(self._ticks.get(code, {})),
            "kline_count": len(self._klines.get(code, {})),
          }
        )
      return rows


intraday_warm_cache = IntradayWarmCacheService()
