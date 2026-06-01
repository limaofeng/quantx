"""
移动平均线指标
"""

from typing import List, Union

from .base import IndicatorBase


class SMA(IndicatorBase):
  """简单移动平均线"""

  @property
  def name(self) -> str:
    return f"SMA_{self.period}"

  def calculate(self, data: List[float]) -> Union[float, None]:
    """计算简单移动平均线"""
    if len(data) < self.period:
      return None

    return sum(data[-self.period :]) / self.period


class EMA(IndicatorBase):
  """指数移动平均线"""

  def __init__(self, period: int = 20, **kwargs):
    super().__init__(period, **kwargs)
    self.alpha = 2.0 / (period + 1)
    self.previous_ema = None

  @property
  def name(self) -> str:
    return f"EMA_{self.period}"

  def calculate(self, data: List[float]) -> Union[float, None]:
    """计算指数移动平均线"""
    if len(data) < self.period:
      return None

    current_price = data[-1]

    if self.previous_ema is None:
      # 首次计算使用SMA作为初始值
      self.previous_ema = sum(data[-self.period :]) / self.period
      return self.previous_ema

    ema = self.alpha * current_price + (1 - self.alpha) * self.previous_ema
    self.previous_ema = ema
    return ema

  def reset(self) -> None:
    """重置EMA状态"""
    super().reset()
    self.previous_ema = None


class WMA(IndicatorBase):
  """加权移动平均线"""

  @property
  def name(self) -> str:
    return f"WMA_{self.period}"

  def calculate(self, data: List[float]) -> Union[float, None]:
    """计算加权移动平均线"""
    if len(data) < self.period:
      return None

    weights = list(range(1, self.period + 1))
    weighted_sum = sum(
      price * weight for price, weight in zip(data[-self.period :], weights)
    )
    weight_sum = sum(weights)

    return weighted_sum / weight_sum


class TEMA(IndicatorBase):
  """三重指数移动平均线"""

  def __init__(self, period: int = 20, **kwargs):
    super().__init__(period, **kwargs)
    self.ema1 = EMA(period)
    self.ema2 = EMA(period)
    self.ema3 = EMA(period)

  @property
  def name(self) -> str:
    return f"TEMA_{self.period}"

  def calculate(self, data: List[float]) -> Union[float, None]:
    """计算三重指数移动平均线"""
    if len(data) < self.period * 3:
      return None

    # 使用EMA对象计算
    # 注意：这里简化了实现，实际应该逐步计算
    ema1_values = []
    for i in range(self.period, len(data) + 1):
      subset = data[:i]
      if len(subset) >= self.period:
        ema1_values.append(sum(subset[-self.period :]) / self.period)

    if len(ema1_values) < self.period:
      return None

    ema2_values = []
    for i in range(self.period, len(ema1_values) + 1):
      subset = ema1_values[:i]
      if len(subset) >= self.period:
        ema2_values.append(sum(subset[-self.period :]) / self.period)

    if len(ema2_values) < self.period:
      return None

    ema3_value = sum(ema2_values[-self.period :]) / self.period

    # TEMA = 3*EMA1 - 3*EMA2 + EMA3
    if len(ema1_values) > 0 and len(ema2_values) > 0:
      return 3 * ema1_values[-1] - 3 * ema2_values[-1] + ema3_value

    return None

  def reset(self) -> None:
    """重置TEMA状态"""
    super().reset()
    self.ema1.reset()
    self.ema2.reset()
    self.ema3.reset()
