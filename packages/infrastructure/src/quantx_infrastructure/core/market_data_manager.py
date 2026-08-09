"""
运行时市场数据管理器

职责：
- 管理每个策略运行实例的实时数据订阅队列
- 缓存最近 N 条 Tick/KLine 事件，便于新订阅者首帧补齐
- 按时间范围查询历史数据（走 HistoricalMarketDataService）
"""

import asyncio
from collections import deque
from datetime import datetime
from typing import Deque, Dict, List, Optional, Tuple

from quantx_infrastructure.services.historical_market_data_service import (
    HistoricalMarketDataService,
)


class MarketDataManager:
    """策略运行市场数据管理器"""

    def __init__(
        self,
        tick_cache_size: int = 2000,
        kline_cache_size: int = 2000,
    ) -> None:
        self.tick_cache_size = max(1, int(tick_cache_size))
        self.kline_cache_size = max(1, int(kline_cache_size))
        self._subscribers: Dict[str, List[Tuple[str, asyncio.Queue]]] = {}
        self._tick_cache: Dict[str, Deque] = {}
        self._kline_cache: Dict[str, Deque] = {}
        self._historical_service: Optional[HistoricalMarketDataService] = None

    def _get_historical_service(self) -> HistoricalMarketDataService:
        if self._historical_service is None:
            self._historical_service = HistoricalMarketDataService()
        return self._historical_service

    def subscribe(
        self,
        *,
        run_id: str,
        data_type: str = "all",
        maxsize: int = 1000,
        include_recent: bool = True,
    ) -> asyncio.Queue:
        """订阅实时数据"""
        queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers.setdefault(run_id, []).append((data_type, queue))

        if include_recent:
            if data_type in ("tick", "all"):
                for item in self._tick_cache.get(run_id, []):
                    try:
                        queue.put_nowait(("tick", item))
                    except asyncio.QueueFull:
                        break
            if data_type in ("kline", "all"):
                for item in self._kline_cache.get(run_id, []):
                    try:
                        queue.put_nowait(("kline", item))
                    except asyncio.QueueFull:
                        break

        return queue

    def unsubscribe(self, *, run_id: str, queue: asyncio.Queue) -> None:
        """取消订阅"""
        subscribers = self._subscribers.get(run_id, [])
        self._subscribers[run_id] = [
            (t, q) for t, q in subscribers if q is not queue
        ]

    def publish_tick(self, run_id: str, tick) -> None:
        """发布实时 Tick"""
        cache = self._tick_cache.setdefault(
            run_id, deque(maxlen=self.tick_cache_size)
        )
        cache.append(tick)

        for data_type, queue in self._subscribers.get(run_id, []):
            if data_type in ("tick", "all"):
                try:
                    queue.put_nowait(("tick", tick))
                except asyncio.QueueFull:
                    pass

    def publish_kline(self, run_id: str, kline) -> None:
        """发布实时 KLine"""
        cache = self._kline_cache.setdefault(
            run_id, deque(maxlen=self.kline_cache_size)
        )
        cache.append(kline)

        for data_type, queue in self._subscribers.get(run_id, []):
            if data_type in ("kline", "all"):
                try:
                    queue.put_nowait(("kline", kline))
                except asyncio.QueueFull:
                    pass

    def get_recent(
        self,
        *,
        run_id: str,
        data_type: str,
        limit: Optional[int] = None,
    ) -> List:
        """获取最近实时缓存"""
        cache = (
            self._tick_cache.get(run_id, [])
            if data_type == "tick"
            else self._kline_cache.get(run_id, [])
        )
        if limit is None:
            return list(cache)
        return list(cache)[-max(0, int(limit)):]

    async def get_history_ticks(
        self,
        *,
        stock_code: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: Optional[int] = None,
        order: str = "asc",
        dividend_type: str = "none",
    ):
        """查询历史 Tick（异步，支持复权）"""
        return await self._get_historical_service().get_tick_data(
            stock_code=stock_code,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            order=order,
            dividend_type=dividend_type,
        )

    async def get_history_klines(
        self,
        *,
        stock_code: str,
        period: str = "1m",
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: Optional[int] = None,
        order: str = "asc",
        dividend_type: str = "none",
    ):
        """查询历史 KLine（异步，支持复权）"""
        return await self._get_historical_service().get_kline_data(
            stock_code=stock_code,
            period=period,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            order=order,
            dividend_type=dividend_type,
        )
