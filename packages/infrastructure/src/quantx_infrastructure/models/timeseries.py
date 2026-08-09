"""
时间序列数据模型
包含K线数据和价格历史数据的模型定义
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from quantx_infrastructure.database.timeseries_base import BaseModel


class DataPeriod(str, Enum):
  """数据周期枚举"""

  MINUTE_1 = "1m"
  MINUTE_5 = "5m"
  MINUTE_15 = "15m"
  MINUTE_30 = "30m"
  HOUR_1 = "1h"
  DAY_1 = "1d"
  WEEK_1 = "1w"
  MONTH_1 = "1M"


@dataclass
class KLineData(BaseModel):
  """K线数据模型"""

  stock_code: str
  stock_name: str
  period: Union[str, DataPeriod]
  timestamp: datetime
  open_price: float
  high_price: float
  low_price: float
  close_price: float
  volume: int
  amount: float
  # 扩展字段
  change: Optional[float] = None  # 涨跌额
  change_pct: Optional[float] = None  # 涨跌幅
  turnover_rate: Optional[float] = None  # 换手率

  def __post_init__(self):
    """数据校验和处理"""
    # 确保时间戳包含时区信息
    if self.timestamp.tzinfo is None:
      self.timestamp = self.timestamp.replace(tzinfo=timezone.utc)

    # 数据有效性检查
    if self.high_price < self.low_price:
      raise ValueError(f"最高价({self.high_price})不能小于最低价({self.low_price})")

    if not (self.low_price <= self.open_price <= self.high_price):
      raise ValueError(f"开盘价({self.open_price})必须在最高价和最低价之间")

    if not (self.low_price <= self.close_price <= self.high_price):
      raise ValueError(f"收盘价({self.close_price})必须在最高价和最低价之间")

    if self.volume < 0:
      raise ValueError(f"成交量({self.volume})不能为负数")

    if self.amount < 0:
      raise ValueError(f"成交额({self.amount})不能为负数")

  def to_dict(self) -> Dict[str, Any]:
    """转换为字典格式，用于写入InfluxDB"""
    data = {
      "time": self.timestamp,
      "stock_code": self.stock_code,
      "stock_name": self.stock_name,
      "period": self.period.value
      if isinstance(self.period, DataPeriod)
      else self.period,
      "open": self.open_price,
      "high": self.high_price,
      "low": self.low_price,
      "close": self.close_price,
      "volume": self.volume,
      "amount": self.amount,
    }

    # 添加可选字段
    if self.change is not None:
      data["change"] = self.change
    if self.change_pct is not None:
      data["change_pct"] = self.change_pct
    if self.turnover_rate is not None:
      data["turnover_rate"] = self.turnover_rate

    return data

  def get_measurement_name(self) -> str:
    return "kline_data"

  def get_tag_columns(self) -> List[str]:
    return ["stock_code", "stock_name", "period"]

  def get_timestamp_column(self) -> str:
    return "time"

  @classmethod
  def from_dict(cls, data: Dict[str, Any]) -> "KLineData":
    """从字典创建K线数据对象"""
    return cls(
      stock_code=data["stock_code"],
      stock_name=data["stock_name"],
      period=data["period"],
      timestamp=data["timestamp"]
      if isinstance(data["timestamp"], datetime)
      else datetime.fromisoformat(str(data["timestamp"]).replace("Z", "+00:00")),
      open_price=data["open"],
      high_price=data["high"],
      low_price=data["low"],
      close_price=data["close"],
      volume=data["volume"],
      amount=data["amount"],
      change=data.get("change"),
      change_pct=data.get("change_pct"),
      turnover_rate=data.get("turnover_rate"),
    )
