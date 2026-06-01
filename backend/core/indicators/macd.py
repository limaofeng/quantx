"""
MACD指标 - 指数平滑移动平均线收敛发散指标
"""

from typing import Dict, List, Union

from .base import IndicatorBase
from .ma import EMA


class MACD(IndicatorBase):
  """MACD指标"""

  def __init__(
    self, fast_period: int = 12, slow_period: int = 26, signal_period: int = 9, **kwargs
  ):
    # 使用慢线周期作为主周期
    super().__init__(slow_period, **kwargs)
    self.fast_period = fast_period
    self.slow_period = slow_period
    self.signal_period = signal_period

    self.fast_ema = EMA(fast_period)
    self.slow_ema = EMA(slow_period)
    self.signal_ema = EMA(signal_period)
    self.macd_values = []

  @property
  def name(self) -> str:
    return f"MACD_{self.fast_period}_{self.slow_period}_{self.signal_period}"

  def calculate(self, data: List[float]) -> Union[Dict[str, float], None]:
    """计算MACD"""
    if len(data) < self.slow_period:
      return None

    # 计算快线和慢线EMA
    fast_ema = self.fast_ema.calculate(data)
    slow_ema = self.slow_ema.calculate(data)

    if fast_ema is None or slow_ema is None:
      return None

    # 计算MACD线 (DIF)
    macd_line = fast_ema - slow_ema
    self.macd_values.append(macd_line)

    # 保持MACD历史长度
    if len(self.macd_values) > self.signal_period * 2:
      self.macd_values.pop(0)

    # 计算信号线 (DEA)
    if len(self.macd_values) >= self.signal_period:
      signal_line = self.signal_ema.calculate(self.macd_values)
    else:
      signal_line = None

    # 计算柱状图 (MACD histogram)
    if signal_line is not None:
      histogram = macd_line - signal_line
    else:
      histogram = None

    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}

  def reset(self) -> None:
    """重置MACD状态"""
    super().reset()
    self.fast_ema.reset()
    self.slow_ema.reset()
    self.signal_ema.reset()
    self.macd_values.clear()
