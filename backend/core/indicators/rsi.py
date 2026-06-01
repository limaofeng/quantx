"""
RSI指标 - 相对强弱指数
"""

from typing import List, Union

from .base import IndicatorBase


class RSI(IndicatorBase):
  """相对强弱指数指标"""

  def __init__(self, period: int = 14, **kwargs):
    super().__init__(period, **kwargs)

  @property
  def name(self) -> str:
    return f"RSI_{self.period}"

  def calculate(self, data: List[float]) -> Union[float, None]:
    """计算RSI"""
    if len(data) < self.period + 1:
      return None

    # 计算价格变化
    price_changes = []
    for i in range(1, len(data)):
      price_changes.append(data[i] - data[i - 1])

    if len(price_changes) < self.period:
      return None

    # 分离涨跌
    gains = []
    losses = []

    for change in price_changes[-self.period :]:
      if change > 0:
        gains.append(change)
        losses.append(0)
      elif change < 0:
        gains.append(0)
        losses.append(abs(change))
      else:
        gains.append(0)
        losses.append(0)

    # 计算平均涨跌幅
    avg_gain = sum(gains) / self.period
    avg_loss = sum(losses) / self.period

    # 无涨跌时 RSI 处于中性位置
    if avg_gain == 0 and avg_loss == 0:
      return 50.0

    # 避免除零
    if avg_loss == 0:
      return 100.0

    # 计算相对强度
    rs = avg_gain / avg_loss

    # 计算RSI
    rsi = 100 - (100 / (1 + rs))

    return rsi


class StochasticRSI(IndicatorBase):
  """随机RSI指标"""

  def __init__(self, period: int = 14, stoch_period: int = 14, **kwargs):
    super().__init__(period, **kwargs)
    self.stoch_period = stoch_period
    self.rsi_indicator = RSI(period)
    self.rsi_values = []

  @property
  def name(self) -> str:
    return f"StochRSI_{self.period}_{self.stoch_period}"

  def calculate(self, data: List[float]) -> Union[float, None]:
    """计算随机RSI"""
    if len(data) < self.period + 1:
      return None

    # 先计算RSI
    rsi_value = self.rsi_indicator.calculate(data)
    if rsi_value is None:
      return None

    self.rsi_values.append(rsi_value)

    # 保持RSI历史长度
    if len(self.rsi_values) > self.stoch_period * 2:
      self.rsi_values.pop(0)

    if len(self.rsi_values) < self.stoch_period:
      return None

    # 计算随机RSI
    recent_rsi = self.rsi_values[-self.stoch_period :]
    highest_rsi = max(recent_rsi)
    lowest_rsi = min(recent_rsi)

    if highest_rsi == lowest_rsi:
      return 50.0

    stoch_rsi = (rsi_value - lowest_rsi) / (highest_rsi - lowest_rsi) * 100

    return stoch_rsi

  def reset(self) -> None:
    """重置状态"""
    super().reset()
    self.rsi_indicator.reset()
    self.rsi_values.clear()
