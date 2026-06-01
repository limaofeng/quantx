"""A-share market rules used before routing orders."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Optional, Sequence

from core.brokers.base import OrderRequest, OrderType, PriceType


@dataclass
class OrderCheckResult:
  ok: bool
  code: str = "ok"
  message: str = ""
  metadata: Dict[str, Any] = field(default_factory=dict)

  @classmethod
  def passed(cls) -> "OrderCheckResult":
    return cls(ok=True)

  @classmethod
  def failed(
    cls, code: str, message: str, metadata: Optional[Dict[str, Any]] = None
  ) -> "OrderCheckResult":
    return cls(ok=False, code=code, message=message, metadata=metadata or {})


@dataclass
class MarketDataSnapshot:
  instrument_code: str
  timestamp: Optional[datetime] = None
  price: float = 0.0
  open: Optional[float] = None
  high: Optional[float] = None
  low: Optional[float] = None
  close: Optional[float] = None
  volume: Optional[float] = None
  amount: Optional[float] = None
  price_tick: float = 0.01
  limit_up: Optional[float] = None
  limit_down: Optional[float] = None
  is_trading: bool = True
  suspended: bool = False
  bid_price: Sequence[float] = field(default_factory=list)
  ask_price: Sequence[float] = field(default_factory=list)
  bid_vol: Sequence[float] = field(default_factory=list)
  ask_vol: Sequence[float] = field(default_factory=list)
  source: str = ""

  @classmethod
  def from_kline(cls, kline: Any) -> "MarketDataSnapshot":
    suspend_flag = int(getattr(kline, "suspend_flag", 0) or 0)
    close = float(getattr(kline, "close", 0.0) or 0.0)
    return cls(
      instrument_code=getattr(kline, "code", None)
      or getattr(kline, "stock_code", ""),
      timestamp=getattr(kline, "time", None),
      price=close,
      open=_optional_float(getattr(kline, "open", None)),
      high=_optional_float(getattr(kline, "high", None)),
      low=_optional_float(getattr(kline, "low", None)),
      close=close,
      volume=_optional_float(getattr(kline, "volume", None)),
      amount=_optional_float(getattr(kline, "amount", None)),
      price_tick=float(getattr(kline, "price_tick", 0.01) or 0.01),
      limit_up=_optional_float(
        getattr(kline, "up_stop_price", getattr(kline, "limit_up", None))
      ),
      limit_down=_optional_float(
        getattr(kline, "down_stop_price", getattr(kline, "limit_down", None))
      ),
      is_trading=suspend_flag == 0,
      suspended=suspend_flag != 0,
      source="kline",
    )

  @classmethod
  def from_tick(cls, tick: Any) -> "MarketDataSnapshot":
    stock_status = getattr(tick, "stock_status", 0)
    suspended = _stock_status_indicates_suspension(stock_status)
    price = float(getattr(tick, "last_price", 0.0) or 0.0)
    return cls(
      instrument_code=getattr(tick, "code", None) or getattr(tick, "stock_code", ""),
      timestamp=getattr(tick, "time", None),
      price=price,
      open=_optional_float(getattr(tick, "open", None)),
      high=_optional_float(getattr(tick, "high", None)),
      low=_optional_float(getattr(tick, "low", None)),
      close=price,
      volume=_optional_float(getattr(tick, "volume", None)),
      amount=_optional_float(getattr(tick, "amount", None)),
      price_tick=float(getattr(tick, "price_tick", 0.01) or 0.01),
      limit_up=_optional_float(
        getattr(tick, "up_stop_price", getattr(tick, "limit_up", None))
      ),
      limit_down=_optional_float(
        getattr(tick, "down_stop_price", getattr(tick, "limit_down", None))
      ),
      is_trading=not suspended,
      suspended=suspended,
      bid_price=list(getattr(tick, "bid_price", []) or []),
      ask_price=list(getattr(tick, "ask_price", []) or []),
      bid_vol=list(getattr(tick, "bid_vol", []) or []),
      ask_vol=list(getattr(tick, "ask_vol", []) or []),
      source="tick",
    )


def _optional_float(value: Any) -> Optional[float]:
  try:
    if value is None:
      return None
    return float(value)
  except (TypeError, ValueError):
    return None


def _stock_status_indicates_suspension(value: Any) -> bool:
  if value is None:
    return False
  if isinstance(value, bool):
    return value

  text = str(value).strip().lower()
  if not text or text in {"0", "0.0", "-1", "-1.0"}:
    return False
  if any(token in text for token in ("suspend", "halt", "停牌", "暂停")):
    return True

  try:
    return int(float(text)) == 1
  except (TypeError, ValueError):
    return False


class AShareMarketRules:
  """Common rule checks for ordinary long-only A-share stock trading."""

  lot_size = 100
  default_price_tick = 0.01

  def normalize_price(self, price: float, price_tick: Optional[float] = None) -> float:
    tick = Decimal(str(price_tick or self.default_price_tick))
    if tick <= 0:
      tick = Decimal(str(self.default_price_tick))
    value = Decimal(str(price))
    ticks = (value / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return float((ticks * tick).quantize(tick))

  def is_price_tick_aligned(
    self, price: float, price_tick: Optional[float] = None
  ) -> bool:
    normalized = self.normalize_price(price, price_tick)
    tick = price_tick or self.default_price_tick
    return abs(normalized - float(price)) < max(1e-8, tick / 1000)

  def normalize_buy_volume(self, volume: int) -> int:
    volume = int(volume or 0)
    if volume <= 0:
      return 0
    return (volume // self.lot_size) * self.lot_size

  def normalize_sell_volume(self, requested_volume: int, available_volume: int) -> int:
    requested = int(requested_volume or 0)
    available = int(available_volume or 0)
    if requested <= 0 or available <= 0:
      return 0
    volume = min(requested, available)
    if volume == available:
      return volume
    if volume < self.lot_size:
      return 0
    return (volume // self.lot_size) * self.lot_size

  def estimate_commission(
    self, amount: float, rate: float = 0.0003, minimum: float = 5.0
  ) -> float:
    if amount <= 0:
      return 0.0
    return max(float(amount) * rate, minimum)

  def check_trading_status(
    self,
    market: Optional[MarketDataSnapshot],
    *,
    strict_market_data: bool = False,
  ) -> OrderCheckResult:
    if market is None:
      if strict_market_data:
        return OrderCheckResult.failed("missing_market_data", "缺少行情状态，拒绝下单")
      return OrderCheckResult.passed()
    if market.suspended or not market.is_trading:
      return OrderCheckResult.failed("suspended", "标的停牌或不可交易")
    return OrderCheckResult.passed()

  def check_price(
    self,
    request: OrderRequest,
    market: Optional[MarketDataSnapshot],
    *,
    strict_limit_data: bool = False,
  ) -> OrderCheckResult:
    if request.price_type == PriceType.MARKET:
      return OrderCheckResult.passed()
    if request.price <= 0:
      return OrderCheckResult.failed("invalid_price", "限价单价格必须大于0")

    tick = market.price_tick if market else self.default_price_tick
    if not self.is_price_tick_aligned(request.price, tick):
      return OrderCheckResult.failed(
        "price_tick",
        f"价格不符合最小变价单位: price={request.price}, tick={tick}",
      )

    if market is None:
      if strict_limit_data:
        return OrderCheckResult.failed("missing_limit_data", "缺少涨跌停价格")
      return OrderCheckResult.passed()

    if market.limit_up is None or market.limit_down is None:
      if strict_limit_data:
        return OrderCheckResult.failed("missing_limit_data", "缺少涨跌停价格")
      return OrderCheckResult.passed()

    if request.price > market.limit_up:
      return OrderCheckResult.failed(
        "above_limit_up",
        f"订单价格超过涨停价: {request.price} > {market.limit_up}",
      )
    if request.price < market.limit_down:
      return OrderCheckResult.failed(
        "below_limit_down",
        f"订单价格低于跌停价: {request.price} < {market.limit_down}",
      )
    return OrderCheckResult.passed()

  def check_limit_block(
    self, request: OrderRequest, market: Optional[MarketDataSnapshot]
  ) -> OrderCheckResult:
    if market is None or market.price <= 0:
      return OrderCheckResult.passed()
    if (
      request.order_type == OrderType.BUY
      and market.limit_up is not None
      and request.price >= market.limit_up
      and market.price >= market.limit_up
    ):
      return OrderCheckResult.failed("limit_up_blocked", "涨停封板默认不成交")
    if (
      request.order_type == OrderType.SELL
      and market.limit_down is not None
      and request.price <= market.limit_down
      and market.price <= market.limit_down
    ):
      return OrderCheckResult.failed("limit_down_blocked", "跌停封板默认不成交")
    return OrderCheckResult.passed()

  def check_volume(
    self,
    request: OrderRequest,
    *,
    available_volume: int = 0,
    min_volume: Optional[int] = None,
    max_volume: Optional[int] = None,
  ) -> OrderCheckResult:
    volume = int(request.volume or 0)
    if volume <= 0:
      return OrderCheckResult.failed("invalid_volume", "订单数量必须大于0")

    if request.order_type == OrderType.BUY:
      if volume % self.lot_size != 0:
        return OrderCheckResult.failed("buy_lot", "买入数量必须为100股整数倍")
    elif request.order_type == OrderType.SELL:
      available = int(available_volume or 0)
      if volume > available:
        return OrderCheckResult.failed(
          "insufficient_position", f"可用持仓不足: {available} < {volume}"
        )
      if volume != available and volume < self.lot_size:
        return OrderCheckResult.failed(
          "odd_lot_sell", "不足100股的零股必须一次性卖出"
        )

    if min_volume is not None and volume < int(min_volume):
      return OrderCheckResult.failed(
        "below_min_volume", f"订单数量低于最小申报量: {volume} < {min_volume}"
      )
    if max_volume is not None and volume > int(max_volume):
      return OrderCheckResult.failed(
        "above_max_volume", f"订单数量超过最大申报量: {volume} > {max_volume}"
      )
    return OrderCheckResult.passed()
