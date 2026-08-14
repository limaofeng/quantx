"""
历史数据适配器 - 从 InfluxDB 读取历史数据
"""

import asyncio
from datetime import datetime
from typing import Callable, Dict, List, Optional

from quantx_infrastructure.models.kline import KLine
from quantx_infrastructure.models.tick import Tick
from quantx_infrastructure.services.historical_market_data_service import HistoricalMarketDataService

from .adapter import DataAdapter, DataMode, DataSubscription


class HistoricalDataAdapter(DataAdapter):
  """历史数据适配器 - 用于回测"""

  def __init__(self):
    super().__init__(DataMode.HISTORICAL)
    self.market_data_service = HistoricalMarketDataService()
    self.current_time: Optional[datetime] = None
    self.replay_tasks = {}

  async def connect(self) -> bool:
    """连接数据源"""
    self.is_connected = True
    self.logger.info("历史数据适配器已连接")
    return True

  async def disconnect(self) -> None:
    """断开连接"""
    # 取消所有回放任务
    for task in self.replay_tasks.values():
      task.cancel()
    self.replay_tasks.clear()

    self.is_connected = False
    self.logger.info("历史数据适配器已断开")

  async def subscribe_kline(
    self,
    instrument_code: str,
    period: str = "1m",
    callback: Optional[Callable[[KLine], None]] = None,
  ) -> str:
    """订阅K线数据"""
    subscription_id = self.generate_subscription_id(instrument_code, "kline")

    subscription = DataSubscription(
      instrument_code=instrument_code,
      data_type="kline",
      period=period,
      callback=callback,
    )

    self.subscriptions[subscription_id] = subscription
    self.logger.info(f"订阅历史K线数据: {instrument_code} {period}")

    return subscription_id

  async def subscribe_tick(
    self, instrument_code: str, callback: Optional[Callable[[Tick], None]] = None
  ) -> str:
    """订阅Tick数据"""
    subscription_id = self.generate_subscription_id(instrument_code, "tick")

    subscription = DataSubscription(
      instrument_code=instrument_code,
      data_type="tick",
      callback=callback,
    )

    self.subscriptions[subscription_id] = subscription
    self.logger.info(f"订阅历史Tick数据: {instrument_code}")

    return subscription_id

  async def unsubscribe(self, subscription_id: str) -> bool:
    """取消订阅"""
    if subscription_id in self.subscriptions:
      # 停止回放任务
      if subscription_id in self.replay_tasks:
        self.replay_tasks[subscription_id].cancel()
        del self.replay_tasks[subscription_id]

      del self.subscriptions[subscription_id]
      self.logger.info(f"取消订阅: {subscription_id}")
      return True

    return False

  async def get_klines(
    self,
    instrument_code: str,
    period: str,
    start_time: datetime,
    end_time: Optional[datetime] = None,
    limit: Optional[int] = 1000,
    order: str = "asc",
    dividend_type: str = "none",
  ) -> List[KLine]:
    """获取历史K线数据"""
    try:
      self.logger.debug(
        f"get_klines: instrument={instrument_code}, period={period}, "
        f"start_time={start_time}, end_time={end_time}, limit={limit}, order={order}"
      )
      klines = await self.market_data_service.get_kline_data(
        stock_code=instrument_code,
        period=period,
        start_time=start_time,
        end_time=end_time,
        limit=limit,
        order=order,
        dividend_type=dividend_type,
      )

      self.logger.debug(f"get_klines: returned {len(klines)} records")
      return klines


    except Exception as e:
      self.logger.error(f"获取历史K线数据失败: {e}")
      return []

  async def get_ticks(
    self,
    instrument_code: str,
    start_time: datetime,
    end_time: Optional[datetime] = None,
    dividend_type: str = "none",
    limit: Optional[int] = 1000,
    order: str = "asc",
  ) -> List[Tick]:
    """获取历史Tick数据（dividend_type 仅为接口一致性）"""
    try:
      ticks = await self.market_data_service.get_tick_data(
        stock_code=instrument_code,
        start_time=start_time,
        end_time=end_time,
        dividend_type=dividend_type,
        limit=limit,
        order=order,
      )

      return ticks

    except Exception as e:
      self.logger.error(f"获取历史Tick数据失败: {e}")
      return []

  async def get_latest_price(self, instrument_code: str) -> Optional[float]:
    """获取最新价格（历史数据的当前时间价格）"""
    if not self.current_time:
      return None

    # 获取当前时间点的数据
    klines = await self.get_klines(
      instrument_code=instrument_code,
      period="1m",
      start_time=self.current_time,
      end_time=self.current_time,
      limit=1,
    )

    if klines:
      return klines[0].close

    return None

  async def get_latest_ticks(self, stock_codes: List[str]) -> Dict[str, Tick]:
    """获取股票列表最新tick数据（用于历史降级）"""
    try:
      return await self.market_data_service.get_latest_ticks(stock_codes)
    except Exception as e:
      self.logger.error(f"获取最新tick失败: {e}")
      return {}

  async def replay_historical_data(
    self,
    instrument_code: str,
    start_time: datetime,
    end_time: datetime,
    data_type: str = "kline",
    period: str = "1m",
    speed: float = 1.0,
  ) -> Optional[asyncio.Task]:
    """
    回放历史数据
    Args:
        instrument_code: 标的代码
        start_time: 开始时间
        end_time: 结束时间
        data_type: 数据类型 (kline/tick)
        period: K线周期
        speed: 回放速度倍数
    """
    if subscription:
      subscription.replay_speed = max(0.1, min(100.0, speed))
    self.current_time = start_time

    # 查找对应的订阅
    subscription = None
    subscription_id = None
    for sub_id, sub in self.subscriptions.items():
      if (
        sub.instrument_code == instrument_code
        and sub.data_type == data_type
        and (data_type == "tick" or sub.period == period)
      ):
        subscription = sub
        subscription_id = sub_id
        break

    if not subscription:
      self.logger.warning(f"未找到匹配的订阅: {instrument_code} {data_type}")
      return None

    # 创建回放任务
    if data_type == "kline":
      task = asyncio.create_task(
        self._replay_kline_data(
          subscription_id, instrument_code, period, start_time, end_time
        )
      )
    else:
      task = asyncio.create_task(
        self._replay_tick_data(subscription_id, instrument_code, start_time, end_time)
      )

    self.replay_tasks[subscription_id] = task
    return task

  async def _replay_kline_data(
    self,
    subscription_id: str,
    instrument_code: str,
    period: str,
    start_time: datetime,
    end_time: datetime,
  ) -> None:
    """回放K线数据"""
    # 获取历史数据
    klines = await self.get_klines(
      instrument_code, period, start_time, end_time
    )

    if not klines:
      self.logger.warning(f"没有历史K线数据: {instrument_code}")
      return

    speed = 1.0
    sub = self.subscriptions.get(subscription_id)
    if sub:
      speed = sub.replay_speed

    self.logger.info(
      f"开始回放K线数据: {instrument_code} {period}, 共 {len(klines)} 条, 速度 {speed}x"
    )

    # 计算K线间隔（秒）
    period_seconds = self._get_period_seconds(period)
    interval = period_seconds / speed

    # 逐条回放
    for i, kline in enumerate(klines):
      if subscription_id not in self.subscriptions:
        break

      # 更新当前时间
      self.current_time = kline.time

      # 发送数据
      await self.emit_kline_data(subscription_id, kline)

      # 等待下一条数据
      if i < len(klines) - 1:
        await asyncio.sleep(interval)

    self.logger.info(f"K线数据回放完成: {instrument_code}")

  async def _replay_tick_data(
    self,
    subscription_id: str,
    instrument_code: str,
    start_time: datetime,
    end_time: datetime,
  ) -> None:
    """回放Tick数据"""
    # 获取历史数据
    ticks = await self.get_ticks(instrument_code, start_time, end_time)

    if not ticks:
      self.logger.warning(f"没有历史Tick数据: {instrument_code}")
      return

    speed = 1.0
    sub = self.subscriptions.get(subscription_id)
    if sub:
      speed = sub.replay_speed

    self.logger.info(
      f"开始回放Tick数据: {instrument_code}, 共 {len(ticks)} 条, 速度 {speed}x"
    )

    # 逐条回放
    last_time = None
    for tick in ticks:
      if subscription_id not in self.subscriptions:
        break

      # 更新当前时间
      self.current_time = tick.time

      # 计算与上一条数据的时间间隔
      if last_time:
        time_diff = (tick.time - last_time).total_seconds()
        interval = time_diff / speed
        if interval > 0:
          await asyncio.sleep(interval)

      # 发送数据
      await self.emit_tick_data(subscription_id, tick)

      last_time = tick.time

    self.logger.info(f"Tick数据回放完成: {instrument_code}")

  def _get_period_seconds(self, period: str) -> int:
    """获取K线周期对应的秒数"""
    period_map = {
      "1m": 60,
      "5m": 300,
      "15m": 900,
      "30m": 1800,
      "60m": 3600,
      "1h": 3600,
      "1d": 86400,
      "1w": 604800,
    }
    return period_map.get(period, 60)
