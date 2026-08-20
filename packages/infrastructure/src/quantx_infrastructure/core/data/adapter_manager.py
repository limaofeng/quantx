"""
数据适配器管理器
负责管理全局的数据适配器实例，避免重复创建
"""

import asyncio
import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, Optional

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models.enums import StrategyRunMode
from quantx_infrastructure.services.trading_time_service import TradingTimeService

from .adapter import DataAdapter
from .historical import HistoricalDataAdapter
from .realtime import RealtimeDataAdapter


class AdapterManager:
  """数据适配器管理器（单例模式 + 工厂模式）"""

  _instance: Optional["AdapterManager"] = None
  _realtime_adapter: Optional[RealtimeDataAdapter] = None
  _historical_adapter: Optional[HistoricalDataAdapter] = None

  def __new__(cls):
    if cls._instance is None:
      cls._instance = super().__new__(cls)
      cls._instance.logger = logging.getLogger(__name__)
      # 初始化引用计数器
      cls._instance._ref_counts = defaultdict(int)
      cls._instance._lifecycle_locks = defaultdict(asyncio.Lock)
      cls._instance.trading_time_service = TradingTimeService()
    return cls._instance

  @property
  def realtime_adapter(self) -> RealtimeDataAdapter:
    """获取实时数据适配器实例"""
    if self._realtime_adapter is None:
      self._realtime_adapter = RealtimeDataAdapter()
      self.logger.info("创建实时数据适配器实例")
    return self._realtime_adapter

  @property
  def historical_adapter(self) -> HistoricalDataAdapter:
    """获取历史数据适配器实例"""
    if self._historical_adapter is None:
      self._historical_adapter = HistoricalDataAdapter()
      self.logger.info("创建历史数据适配器实例")
    return self._historical_adapter

  async def initialize_all(self) -> bool:
    """初始化所有适配器"""
    try:
      # 初始化实时适配器
      realtime_success = await self.realtime_adapter.connect()

      # 初始化历史适配器
      historical_success = await self.historical_adapter.connect()

      self.logger.info(
        f"适配器初始化完成 - 实时: {realtime_success}, 历史: {historical_success}"
      )
      return realtime_success or historical_success  # 至少一个成功即可

    except Exception as e:
      self.logger.error(f"适配器初始化失败: {e}")
      return False

  async def get_adapter_for_time_range(
    self,
    start_time: Optional[datetime],
    end_time: Optional[datetime],
  ) -> DataAdapter:
    """根据查询时间范围选择数据适配器"""
    if end_time is None:
      end_time = time_utils.now()

    if end_time is None:
      return self.historical_adapter

    end_local = time_utils.to_shanghai(end_time)
    check_date = end_local.date()

    if not await self.trading_time_service.is_trading_day("SH", check_date):
      return self.historical_adapter

    trading_hours = self.trading_time_service.get_trading_hours("SH")
    if any(start <= end_local.time() <= end for start, end in trading_hours):
      return self.realtime_adapter

    return self.historical_adapter

  async def shutdown_all(self) -> None:
    """关闭所有适配器"""
    try:
      if self._realtime_adapter:
        await self._realtime_adapter.disconnect()

      if self._historical_adapter:
        await self._historical_adapter.disconnect()

      self.logger.info("所有适配器已关闭")

    except Exception as e:
      self.logger.error(f"关闭适配器失败: {e}")

  async def get_adapter_for_mode(self, mode: StrategyRunMode) -> DataAdapter:
    """
    根据策略模式获取适配器实例（工厂方法）

    Args:
        mode: 策略运行模式枚举

    Returns:
        对应的数据适配器实例
    """
    if mode == StrategyRunMode.BACKTEST:
      adapter = self.historical_adapter
      adapter_type = "historical"
    elif mode in (StrategyRunMode.PAPER, StrategyRunMode.LIVE):
      adapter = self.realtime_adapter
      adapter_type = "realtime"
    else:
      raise ValueError(f"不支持的策略模式: {mode}")

    async with self._lifecycle_locks[adapter_type]:
      # 增加引用计数。与最后一个 owner 的 disconnect 共用同一把锁，
      # 防止失败释放与新 acquire 交错后误断新 owner 的连接。
      self._ref_counts[adapter_type] += 1
      self.logger.info(
        f"获取 {adapter_type} 适配器，当前引用计数: {self._ref_counts[adapter_type]}"
      )

    return adapter

  async def ensure_adapter_connected_for_mode(
    self,
    mode: StrategyRunMode,
    adapter: DataAdapter,
  ) -> bool:
    """Serialize connection of the shared adapter selected for ``mode``."""

    if mode == StrategyRunMode.BACKTEST:
      adapter_type = "historical"
    elif mode in (StrategyRunMode.PAPER, StrategyRunMode.LIVE):
      adapter_type = "realtime"
    else:
      raise ValueError(f"不支持的策略模式: {mode}")

    async with self._lifecycle_locks[adapter_type]:
      if getattr(adapter, "is_connected", False) is True:
        return True
      return bool(await adapter.connect())

  async def release_adapter_for_mode(self, mode: str) -> None:
    """
    释放指定模式的适配器引用

    Args:
        mode: 策略模式 ('backtest', 'paper', 'live')
    """
    if mode == "backtest":
      adapter_type = "historical"
      adapter = self._historical_adapter
    elif mode in ["paper", "live"]:
      adapter_type = "realtime"
      adapter = self._realtime_adapter
    else:
      self.logger.warning(f"无法释放未知模式的适配器: {mode}")
      return

    async with self._lifecycle_locks[adapter_type]:
      refs = self._ref_counts[adapter_type]
      if refs <= 0:
        self.logger.warning(f"{adapter_type} 适配器没有可释放的引用")
        return
      if refs > 1:
        self._ref_counts[adapter_type] = refs - 1
        self.logger.info(
          f"释放 {adapter_type} 适配器，当前引用计数: {refs - 1}"
        )
        return

      # The final reference remains owned until disconnect succeeds. If it
      # fails, retrying this same release is safe; a new acquire cannot cross
      # this await because it uses the same lifecycle lock.
      if adapter:
        await adapter.disconnect()
        if bool(getattr(adapter, "is_connected", False)):
          raise RuntimeError(
            f"{adapter_type} 适配器断开后仍报告 connected"
          )
      self._ref_counts[adapter_type] = 0
      self.logger.info(f"{adapter_type} 适配器无引用，已断开连接")

  def get_adapter_stats(self) -> Dict[str, Any]:
    """获取适配器统计信息"""
    return {
      "realtime_refs": self._ref_counts["realtime"],
      "historical_refs": self._ref_counts["historical"],
      "realtime_connected": self._realtime_adapter.is_connected
      if self._realtime_adapter
      else False,
      "historical_connected": self._historical_adapter.is_connected
      if self._historical_adapter
      else False,
    }

  def reset(self) -> None:
    """重置管理器（主要用于测试）"""
    self._realtime_adapter = None
    self._historical_adapter = None
    self._ref_counts.clear()
    self._lifecycle_locks.clear()
    self.logger.info("适配器管理器已重置")


# 全局适配器管理器实例
adapter_manager = AdapterManager()
