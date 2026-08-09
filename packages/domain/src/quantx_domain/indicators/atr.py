"""Average True Range (ATR) indicator."""

from typing import Dict, List, Optional, Union

from quantx_domain.indicators.base import IndicatorBase, IndicatorValue
from quantx_domain.market import KLine


class ATR(IndicatorBase):
  """Streaming simple-moving-average ATR implementation."""

  def __init__(self, period: int = 14):
    super().__init__(period=period)
    self.previous_close: Optional[float] = None
    self.tr_history: List[float] = []

  @property
  def name(self) -> str:
    return f"ATR_{self.period}"

  def calculate(self, data: List[float]) -> Union[float, Dict[str, float], None]:
    """Calculate ATR from a true-range sequence."""
    if len(data) < self.period:
      return None
    return sum(data[-self.period :]) / self.period

  def update(self, bar: KLine) -> Optional[IndicatorValue]:
    """Consume a bar and return an ATR value after the warm-up window."""
    if self.previous_close is None:
      true_range = bar.high - bar.low
    else:
      true_range = max(
        bar.high - bar.low,
        abs(bar.high - self.previous_close),
        abs(bar.low - self.previous_close),
      )
    self.previous_close = bar.close
    self.tr_history.append(true_range)
    self.data_window.append(bar.close)

    atr_value = self.calculate(self.tr_history)
    if atr_value is None:
      return None

    self.is_warmed_up = True
    value = IndicatorValue(
      timestamp=bar.time,
      value=atr_value,
      metadata={"tr": true_range},
    )
    self.values.append(value)
    return value

  def calculate_tr(self, high: float, low: float, prev_close: float) -> float:
    """Calculate one bar's true range."""
    return max(high - low, abs(high - prev_close), abs(low - prev_close))

  def reset(self) -> None:
    super().reset()
    self.previous_close = None
    self.tr_history.clear()
