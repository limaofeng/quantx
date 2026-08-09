"""
技术指标基类 - 定义指标的统一接口和计算模式
"""

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from datetime import datetime
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
        timestamp=bar.time, value=value, metadata={"bar": bar.to_dict()}
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
