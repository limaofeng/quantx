"""
技术指标基类 - 定义指标的统一接口和计算模式
"""

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Any, Dict, List, Optional, Union

from quantx_domain.market import KLine


@dataclass
class IndicatorValue:
  """指标值"""

  timestamp: datetime
  value: Union[float, Dict[str, float]]
  metadata: Dict[str, Any] = None

  def __post_init__(self):
    if self.metadata is None:
      self.metadata = {}


class IndicatorBase(ABC):
  """技术指标基类"""

  def __init__(self, period: int = 20, **kwargs):
    self.period = period
    self.params = kwargs
    self.data_window = deque(maxlen=max(period * 2, 100))
    self.values: List[IndicatorValue] = []
    self.is_warmed_up = False

  @property
  @abstractmethod
  def name(self) -> str:
    """指标名称"""
    pass

  @abstractmethod
  def calculate(self, data: List[float]) -> Union[float, Dict[str, float], None]:
    """
    计算指标值
    Args:
        data: 价格数据列表
    Returns:
        指标值，可能是单个值或多个值的字典
    """
    pass

  def update(self, bar: KLine) -> Optional[IndicatorValue]:
    """
    更新指标数据
    Args:
        bar: K线数据
    Returns:
        新的指标值，如果数据不足则返回None
    """
    self.data_window.append(bar.close)

    if len(self.data_window) < self.period:
      return None

    if not self.is_warmed_up and len(self.data_window) >= self.period:
      self.is_warmed_up = True

    value = self.calculate(list(self.data_window))
    if value is not None:
      indicator_value = IndicatorValue(
        timestamp=bar.time,
        value=value,
        metadata={
          "bar": {
            "stock_code": bar.stock_code,
            "period": bar.period,
            "time": bar.time,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
          }
        },
      )
      self.values.append(indicator_value)
      return indicator_value

    return None

  def get_current_value(self) -> Optional[Union[float, Dict[str, float]]]:
    """获取当前指标值"""
    if self.values:
      return self.values[-1].value
    return None

  def get_values(self, count: int = None) -> List[IndicatorValue]:
    """
    获取历史指标值
    Args:
        count: 获取的数量，None表示全部
    Returns:
        指标值列表
    """
    if count is None:
      return self.values
    return self.values[-count:]

  def reset(self) -> None:
    """重置指标状态"""
    self.data_window.clear()
    self.values.clear()
    self.is_warmed_up = False

  def _window_snapshot(self) -> Dict[str, Any]:
    """Bounded calculation state; chart history is not part of recovery."""
    latest = self.values[-1] if self.values else None
    return {
      "indicator": self.__class__.__name__,
      "period": self.period,
      "data_window": list(self.data_window),
      "is_warmed_up": self.is_warmed_up,
      "last_value": {
        "timestamp": latest.timestamp.isoformat(),
        "value": latest.value,
      }
      if latest
      else None,
    }

  def _restore_window(self, snapshot: Dict[str, Any]) -> None:
    if (
      snapshot["indicator"] != self.__class__.__name__
      or snapshot["period"] != self.period
    ):
      raise ValueError("INDICATOR_STATE_CONFIG_MISMATCH")
    window = [float(value) for value in snapshot["data_window"]]
    if len(window) > self.data_window.maxlen or not all(
      isfinite(value) for value in window
    ):
      raise ValueError("INDICATOR_STATE_WINDOW_INVALID")
    warmed = snapshot["is_warmed_up"]
    latest = snapshot["last_value"]
    if (
      type(warmed) is not bool
      or warmed != (len(window) >= self.period)
      or warmed != (latest is not None)
    ):
      raise ValueError("INDICATOR_STATE_WARMUP_INVALID")
    values = []
    if latest is not None:
      value = float(latest["value"])
      if not isfinite(value):
        raise ValueError("INDICATOR_STATE_VALUE_INVALID")
      values.append(IndicatorValue(datetime.fromisoformat(latest["timestamp"]), value))
    self.data_window.clear()
    self.data_window.extend(window)
    self.is_warmed_up = warmed
    self.values = values
