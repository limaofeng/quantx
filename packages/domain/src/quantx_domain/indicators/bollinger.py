"""
布林带指标
"""

import math
from typing import Dict, List, Union

from .base import IndicatorBase
from .ma import SMA


class BollingerBands(IndicatorBase):
  """布林带指标"""

  def __init__(self, period: int = 20, multiplier: float = 2.0, **kwargs):
    super().__init__(period, **kwargs)
    self.multiplier = multiplier
    self.sma = SMA(period)

  @property
  def name(self) -> str:
    return f"BOLL_{self.period}_{self.multiplier}"

  def calculate(self, data: List[float]) -> Union[Dict[str, float], None]:
    """计算布林带"""
    if len(data) < self.period:
      return None

    # 计算中轨（移动平均线）
    middle_band = self.sma.calculate(data)
    if middle_band is None:
      return None

    # 计算标准差
    recent_data = data[-self.period :]
    variance = sum((x - middle_band) ** 2 for x in recent_data) / self.period
    std_dev = math.sqrt(variance)

    # 计算上轨和下轨
    upper_band = middle_band + (self.multiplier * std_dev)
    lower_band = middle_band - (self.multiplier * std_dev)

    # 计算当前价格相对位置（%B）
    current_price = data[-1]
    if upper_band != lower_band:
      percent_b = (current_price - lower_band) / (upper_band - lower_band)
    else:
      percent_b = 0.5

    # 计算带宽
    if middle_band != 0:
      bandwidth = (upper_band - lower_band) / middle_band
    else:
      bandwidth = 0

    return {
      "upper": upper_band,
      "middle": middle_band,
      "lower": lower_band,
      "percent_b": percent_b,
      "bandwidth": bandwidth,
    }

  def reset(self) -> None:
    """重置状态"""
    super().reset()
    self.sma.reset()
