from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List

from database.timeseries_base import BaseModel, ListAttributeConverter


@dataclass
class Tick(BaseModel):
  stock_code: str
  """代码"""
  period: str
  """周期"""
  time: datetime
  """时间"""
  last_price: float
  """最新价"""
  open: float
  """开盘价"""
  high: float
  """最高价"""
  low: float
  """最低价"""
  last_close: float
  """前收盘价"""
  amount: float
  """成交额"""
  volume: float
  """成交量"""
  pvolume: float
  """原始成交总量"""
  tickvol: float
  """现手 (即当前tick累计成交量与上条数据的差值)"""
  stock_status: int
  """股票状态 0-正常 1-停牌 -1-当日起复牌"""
  open_int: int
  """持仓量"""
  last_settlement_price: float
  """昨结算价(股票为0)"""
  settlement_price: float
  """今结算(股票为0)"""
  transaction_num: int
  """成交笔数"""
  ask_price: List[float] = field(
    default_factory=list,
    metadata={"converter": ListAttributeConverter(prefix="ask", max_levels=5)},
  )
  """委卖价"""
  bid_price: List[float] = field(
    default_factory=list,
    metadata={"converter": ListAttributeConverter(prefix="bid", max_levels=5)},
  )
  """委买价"""
  ask_vol: List[float] = field(
    default_factory=list,
    metadata={"converter": ListAttributeConverter(prefix="ask_vol", max_levels=5)},
  )
  """委卖量"""
  bid_vol: List[float] = field(
    default_factory=list,
    metadata={"converter": ListAttributeConverter(prefix="bid_vol", max_levels=5)},
  )
  """委买量"""

  def __init__(self, **kwargs):
    super().__init__()
    for key, value in kwargs.items():
      setattr(self, key, value)

  def get_measurement_name(self) -> str:
    return "ticks"

  def get_timestamp_column(self) -> str:
    return "time"

  def get_tag_columns(self):
    return [
      "stock_code",
      "period",
    ]

  @staticmethod
  def from_xtquant(stock_code: str, tick: Dict[str, Any]) -> "Tick":
    if "timetag" in tick:
      timetag = tick.get("timetag", "")
      # 处理带毫秒的格式: "20250930 15:30:12.1"
      if "." in timetag:
        time = datetime.strptime(timetag, "%Y%m%d %H:%M:%S.%f")
      else:
        time = datetime.strptime(timetag, "%Y%m%d %H:%M:%S")
    elif "time" in tick:
      time = datetime.fromtimestamp(tick.get("time", 0) / 1000)
    else:
      raise ValueError("Tick data must contain 'timetag' or 'time' field.")

    return Tick(
      stock_code=stock_code,
      period="tick",
      time=time,
      last_price=tick.get("lastPrice", 0.0),
      open=tick.get("open", 0.0),
      high=tick.get("high", 0.0),
      low=tick.get("low", 0.0),
      last_close=tick.get("lastClose", 0.0),
      amount=tick.get("amount", 0.0),
      volume=tick.get("volume", 0.0),
      pvolume=tick.get("pvolume", 0.0),
      tickvol=tick.get("tickvol", 0.0),
      stock_status=tick.get("stockStatus", 0),
      open_int=tick.get("openInt", 0),
      last_settlement_price=tick.get("lastSettlementPrice", 0.0),
      settlement_price=tick.get("settlementPrice", 0.0),
      transaction_num=tick.get("transactionNum", 0),
      ask_price=tick.get("askPrice", [0] * 5)[:5],
      bid_price=tick.get("bidPrice", [0] * 5)[:5],
      ask_vol=tick.get("askVol", [0] * 5)[:5],
      bid_vol=tick.get("bidVol", [0] * 5)[:5],
    )
