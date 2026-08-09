from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict

from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.timeseries_base import BaseModel


@dataclass
class KLine(BaseModel):
  stock_code: str
  """代码"""
  period: str
  """周期"""
  time: datetime
  """时间"""
  open: float
  """开盘价"""
  high: float
  """最高价"""
  low: float
  """最低价"""
  close: float
  """收盘价"""
  pre_close: float
  """前收盘价"""
  volume: float
  """成交量"""
  amount: float
  """成交额"""
  settelement_price: float
  """今结算价(股票为0)"""
  open_interest: int
  """若是股票，则openInt含义为股票状态，非股票则是持仓量: 14:集合竞价, 15:连续竞价"""
  suspend_flag: int
  """#停牌标记 0 - 正常 1 - 停牌 -1 - 当日起复牌"""

  def __init__(self, **kwargs):
    super().__init__()
    for key, value in kwargs.items():
      setattr(self, key, value)

  def get_measurement_name(self) -> str:
    return f"kline_{self.period.lower()}"

  def get_timestamp_column(self) -> str:
    return "time"

  def get_tag_columns(self):
    return [
      "stock_code",
      "period",
    ]

  @staticmethod
  def from_xtquant(stock_code: str, period: str, data: Dict[str, Any]) -> "KLine":
    # 转换时间戳
    timestamp = (
      time_utils.to_shanghai(
        datetime.fromtimestamp(data.get("time", 0) / 1000, timezone.utc)
      )
      if data.get("time")
      else time_utils.now()
    )
    return KLine(
      stock_code=stock_code,
      period=period,
      time=timestamp,
      open=data.get("open", 0.0),
      high=data.get("high", 0.0),
      low=data.get("low", 0.0),
      close=data.get("close", 0.0),
      pre_close=data.get("preClose", 0.0),
      volume=data.get("volume", 0.0),
      amount=data.get("amount", 0.0),
      settelement_price=data.get("settlementPrice", 0.0),
      open_interest=data.get("openInt", 0),
      suspend_flag=data.get("suspendFlag", 0),
    )
