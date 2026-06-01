from dataclasses import dataclass, field
from datetime import datetime
from typing import List

from database.timeseries_base import BaseModel


@dataclass
class MarketDepthLevel:
  """市场深度档位"""

  price: float
  """价格"""
  volume: int
  """数量"""


@dataclass
class MarketDepth(BaseModel):
  """市场深度数据领域模型"""

  stock_code: str
  """股票代码"""
  time: datetime
  """时间戳"""
  bid_levels: List[MarketDepthLevel] = field(default_factory=list)
  """买盘档位列表，按价格从高到低排序"""
  ask_levels: List[MarketDepthLevel] = field(default_factory=list)
  """卖盘档位列表，按价格从低到高排序"""

  def __init__(self, **kwargs):
    super().__init__()
    for key, value in kwargs.items():
      setattr(self, key, value)

  def get_measurement_name(self) -> str:
    return "market_depth"

  def get_timestamp_column(self) -> str:
    return "time"

  def get_tag_columns(self):
    return ["stock_code"]

  @property
  def best_bid(self) -> MarketDepthLevel:
    """最优买价"""
    return self.bid_levels[0] if self.bid_levels else MarketDepthLevel(0, 0)

  @property
  def best_ask(self) -> MarketDepthLevel:
    """最优卖价"""
    return self.ask_levels[0] if self.ask_levels else MarketDepthLevel(0, 0)

  @property
  def spread(self) -> float:
    """买卖价差"""
    if self.bid_levels and self.ask_levels:
      return self.best_ask.price - self.best_bid.price
    return 0.0
