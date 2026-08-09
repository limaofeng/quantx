from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from quantx_infrastructure.database.timeseries_base import BaseModel


@dataclass
class RealTimePrice(BaseModel):
  """实时价格数据领域模型"""

  stock_code: str
  """股票代码"""
  time: datetime
  """时间戳"""
  current_price: float
  """当前价格"""
  change: Optional[float] = None
  """涨跌额"""
  change_percent: Optional[float] = None
  """涨跌幅（%）"""
  volume: float = 0.0
  """成交量"""
  amount: float = 0.0
  """成交额"""
  bid_price: float = 0.0
  """买一价"""
  ask_price: float = 0.0
  """卖一价"""
  bid_volume: int = 0
  """买一量"""
  ask_volume: int = 0
  """卖一量"""
  high: float = 0.0
  """最高价"""
  low: float = 0.0
  """最低价"""
  open: float = 0.0
  """开盘价"""
  pre_close: Optional[float] = None
  """前收盘价"""

  def __init__(self, **kwargs):
    super().__init__()
    for key, value in kwargs.items():
      setattr(self, key, value)

  def get_measurement_name(self) -> str:
    return "realtime_price"

  def get_timestamp_column(self) -> str:
    return "time"

  def get_tag_columns(self):
    return ["stock_code"]

  @classmethod
  def from_tick(cls, tick) -> "RealTimePrice":
    """从 Tick 数据转换为实时价格"""
    # 计算涨跌额和涨跌幅
    change = None
    change_percent = None

    if tick.last_close and tick.last_close > 0:
      change = tick.last_price - tick.last_close
      change_percent = (change / tick.last_close) * 100

    return cls(
      stock_code=tick.stock_code,
      time=tick.time,
      current_price=tick.last_price,
      change=change,
      change_percent=change_percent,
      volume=tick.volume,
      amount=tick.amount,
      bid_price=tick.bid_price[0]
      if tick.bid_price and len(tick.bid_price) > 0
      else 0.0,
      ask_price=tick.ask_price[0]
      if tick.ask_price and len(tick.ask_price) > 0
      else 0.0,
      bid_volume=int(tick.bid_vol[0]) if tick.bid_vol and len(tick.bid_vol) > 0 else 0,
      ask_volume=int(tick.ask_vol[0]) if tick.ask_vol and len(tick.ask_vol) > 0 else 0,
      high=tick.high,
      low=tick.low,
      open=tick.open,
      pre_close=tick.last_close,
    )

  @property
  def spread(self) -> float:
    """买卖价差"""
    if self.ask_price > 0 and self.bid_price > 0:
      return self.ask_price - self.bid_price
    return 0.0

  @property
  def is_rising(self) -> bool:
    """是否上涨"""
    return self.change is not None and self.change > 0

  @property
  def is_falling(self) -> bool:
    """是否下跌"""
    return self.change is not None and self.change < 0
