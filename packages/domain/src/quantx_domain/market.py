"""Transport-agnostic market snapshots consumed by strategies."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class KLine:
  stock_code: str = ""
  period: str = ""
  time: Optional[datetime] = None
  open: float = 0.0
  high: float = 0.0
  low: float = 0.0
  close: float = 0.0
  pre_close: float = 0.0
  volume: float = 0.0
  amount: float = 0.0
  settlement_price: float = 0.0
  open_interest: int = 0
  suspend_flag: int = 0


@dataclass
class Tick:
  stock_code: str = ""
  period: str = "tick"
  time: Optional[datetime] = None
  last_price: float = 0.0
  open: float = 0.0
  high: float = 0.0
  low: float = 0.0
  last_close: float = 0.0
  amount: float = 0.0
  volume: float = 0.0
  pvolume: float = 0.0
  tickvol: float = 0.0
  stock_status: int = 0
  open_int: int = 0
  last_settlement_price: float = 0.0
  settlement_price: float = 0.0
  transaction_num: int = 0
  price_tick: float = 0.01
  up_stop_price: float = 0.0
  down_stop_price: float = 0.0
  ask_price: List[float] = field(default_factory=list)
  bid_price: List[float] = field(default_factory=list)
  ask_vol: List[float] = field(default_factory=list)
  bid_vol: List[float] = field(default_factory=list)
