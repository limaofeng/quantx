"""
数据适配器基类 - 定义统一的数据接口
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from quantx_infrastructure.models.kline import KLine
from quantx_infrastructure.models.tick import Tick


class DataMode(Enum):
  """数据模式"""

  HISTORICAL = "historical"  # 历史数据
  REALTIME = "realtime"  # 实时数据
  SIMULATION = "simulation"  # 模拟数据


@dataclass
class DataSubscription:
  """数据订阅"""

  instrument_code: str
  data_type: str  # "kline" or "tick"
  period: Optional[str] = None  # K线周期
  callback: Optional[Callable] = None
  is_active: bool = True
  replay_speed: float = 1.0
  manager_handle: str = ""


class DataAdapter(ABC):
  """数据适配器抽象基类"""

  def __init__(self, mode: DataMode):
    self.mode = mode
    self.subscriptions: Dict[str, DataSubscription] = {}
    self.logger = logging.getLogger(f"DataAdapter-{mode.value}")
    self.is_connected = False

  @abstractmethod
  async def connect(self) -> bool:
    """连接数据源"""
    pass

  @abstractmethod
  async def disconnect(self) -> None:
    """断开连接"""
    pass

  @abstractmethod
  async def subscribe_kline(
    self,
    instrument_code: str,
    period: str = "1m",
    callback: Optional[Callable[[KLine], None]] = None,
  ) -> str:
    """
    订阅K线数据
    Args:
        instrument_code: 标的代码
        period: K线周期
        callback: 数据回调函数
    Returns:
        订阅ID
    """
    pass

  @abstractmethod
  async def subscribe_tick(
    self, instrument_code: str, callback: Optional[Callable[[Tick], None]] = None
  ) -> str:
    """
    订阅Tick数据
    Args:
        instrument_code: 标的代码
        callback: 数据回调函数
    Returns:
        订阅ID
    """
    pass

  @abstractmethod
  async def unsubscribe(self, subscription_id: str) -> bool:
    """
    取消订阅
    Args:
        subscription_id: 订阅ID
    Returns:
        是否成功
    """
    pass

  @abstractmethod
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
    """
    获取历史K线数据
    Args:
        instrument_code: 标的代码
        period: K线周期
        start_time: 开始时间
        end_time: 结束时间
        limit: 最大数据条数
        dividend_type: 分红复权类型
    Returns:
        K线数据列表
    """
    pass

  @abstractmethod
  async def get_ticks(
    self,
    instrument_code: str,
    start_time: datetime,
    end_time: Optional[datetime] = None,
    dividend_type: str = "none",
    limit: Optional[int] = 1000,
    order: str = "desc",
  ) -> List[Tick]:
    """
    获取历史Tick数据
    Args:
        instrument_code: 标的代码
        start_time: 开始时间
        end_time: 结束时间
        dividend_type: 分红复权类型（tick 通常不使用，保留参数一致性）
        limit: 最大数据条数
    Returns:
        Tick数据列表
    """
    pass

  @abstractmethod
  async def get_latest_price(self, instrument_code: str) -> Optional[float]:
    """
    获取最新价格
    Args:
        instrument_code: 标的代码
    Returns:
        最新价格
    """
    pass

  def generate_subscription_id(self, instrument_code: str, data_type: str) -> str:
    """生成订阅ID"""
    import uuid

    return f"{instrument_code}_{data_type}_{uuid.uuid4().hex[:8]}"

  async def emit_kline_data(self, subscription_id: str, kline: KLine) -> None:
    """发送K线数据"""
    if subscription_id in self.subscriptions:
      sub = self.subscriptions[subscription_id]
      if sub.callback and sub.is_active:
        try:
          if asyncio.iscoroutinefunction(sub.callback):
            await sub.callback(kline)
          else:
            sub.callback(kline)
        except Exception as e:
          self.logger.error(f"K线数据回调失败: {e}")

  async def emit_tick_data(self, subscription_id: str, tick: Tick) -> None:
    """发送Tick数据"""
    if subscription_id in self.subscriptions:
      sub = self.subscriptions[subscription_id]
      if sub.callback and sub.is_active:
        try:
          if asyncio.iscoroutinefunction(sub.callback):
            await sub.callback(tick)
          else:
            sub.callback(tick)
        except Exception as e:
          self.logger.error(f"Tick数据回调失败: {e}")

  def get_active_subscriptions(self) -> List[DataSubscription]:
    """获取活动订阅"""
    return [sub for sub in self.subscriptions.values() if sub.is_active]

  def pause_subscription(self, subscription_id: str) -> bool:
    """暂停订阅"""
    if subscription_id in self.subscriptions:
      self.subscriptions[subscription_id].is_active = False
      return True
    return False

  def resume_subscription(self, subscription_id: str) -> bool:
    """恢复订阅"""
    if subscription_id in self.subscriptions:
      self.subscriptions[subscription_id].is_active = True
      return True
    return False

  def get_statistics(self) -> Dict[str, Any]:
    """获取统计信息"""
    kline_subs = sum(1 for s in self.subscriptions.values() if s.data_type == "kline")
    tick_subs = sum(1 for s in self.subscriptions.values() if s.data_type == "tick")
    active_subs = sum(1 for s in self.subscriptions.values() if s.is_active)

    return {
      "mode": self.mode.value,
      "is_connected": self.is_connected,
      "total_subscriptions": len(self.subscriptions),
      "active_subscriptions": active_subs,
      "kline_subscriptions": kline_subs,
      "tick_subscriptions": tick_subs,
    }
