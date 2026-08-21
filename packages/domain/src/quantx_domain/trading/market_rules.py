"""A-share market rules used before routing orders."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Optional, Sequence

from quantx_domain.brokers.base import OrderRequest, OrderType, PriceType

_MAIN_BOARD_ST_LIMIT_UNIFICATION_DATE = date(2026, 7, 6)
_CHINEXT_TWENTY_PCT_LIMIT_DATE = date(2020, 8, 24)
_BEIJING_STOCK_EXCHANGE_OPEN_DATE = date(2021, 11, 15)
_CONSERVATIVE_SPECIAL_TRADING_WINDOW = timedelta(days=30)


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
  def from_kline(
    cls,
    kline: Any,
    *,
    limit_rate: Optional[float] = None,
  ) -> "MarketDataSnapshot":
    suspend_flag = int(getattr(kline, "suspend_flag", 0) or 0)
    close = float(getattr(kline, "close", 0.0) or 0.0)
    price_tick = float(getattr(kline, "price_tick", 0.01) or 0.01)
    limit_up, limit_down, limits_derived = _resolve_limit_prices(
      limit_up=getattr(kline, "up_stop_price", getattr(kline, "limit_up", None)),
      limit_down=getattr(kline, "down_stop_price", getattr(kline, "limit_down", None)),
      previous_close=getattr(kline, "pre_close", None),
      price_tick=price_tick,
      limit_rate=limit_rate,
    )
    return cls(
      instrument_code=getattr(kline, "code", None) or getattr(kline, "stock_code", ""),
      timestamp=getattr(kline, "time", None),
      price=close,
      open=_optional_float(getattr(kline, "open", None)),
      high=_optional_float(getattr(kline, "high", None)),
      low=_optional_float(getattr(kline, "low", None)),
      close=close,
      volume=_optional_float(getattr(kline, "volume", None)),
      amount=_optional_float(getattr(kline, "amount", None)),
      price_tick=price_tick,
      limit_up=limit_up,
      limit_down=limit_down,
      is_trading=suspend_flag == 0,
      suspended=suspend_flag != 0,
      source="kline_derived_limits" if limits_derived else "kline",
    )

  @classmethod
  def from_tick(
    cls,
    tick: Any,
    *,
    limit_rate: Optional[float] = None,
  ) -> "MarketDataSnapshot":
    stock_status = getattr(tick, "stock_status", 0)
    suspended = _stock_status_indicates_suspension(stock_status)
    price = float(getattr(tick, "last_price", 0.0) or 0.0)
    price_tick = float(getattr(tick, "price_tick", 0.01) or 0.01)
    limit_up, limit_down, limits_derived = _resolve_limit_prices(
      limit_up=getattr(tick, "up_stop_price", getattr(tick, "limit_up", None)),
      limit_down=getattr(tick, "down_stop_price", getattr(tick, "limit_down", None)),
      previous_close=getattr(tick, "last_close", getattr(tick, "pre_close", None)),
      price_tick=price_tick,
      limit_rate=limit_rate,
    )
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
      price_tick=price_tick,
      limit_up=limit_up,
      limit_down=limit_down,
      is_trading=not suspended,
      suspended=suspended,
      bid_price=list(getattr(tick, "bid_price", []) or []),
      ask_price=list(getattr(tick, "ask_price", []) or []),
      bid_vol=list(getattr(tick, "bid_vol", []) or []),
      ask_vol=list(getattr(tick, "ask_vol", []) or []),
      source="tick_derived_limits" if limits_derived else "tick",
    )


def _optional_float(value: Any) -> Optional[float]:
  try:
    if value is None:
      return None
    return float(value)
  except (TypeError, ValueError):
    return None


def _resolve_limit_prices(
  *,
  limit_up: Any,
  limit_down: Any,
  previous_close: Any,
  price_tick: float,
  limit_rate: Optional[float],
) -> tuple[Optional[float], Optional[float], bool]:
  resolved_up = _optional_float(limit_up)
  resolved_down = _optional_float(limit_down)
  resolved_up = resolved_up if resolved_up is not None and resolved_up > 0 else None
  resolved_down = (
    resolved_down if resolved_down is not None and resolved_down > 0 else None
  )
  if resolved_up and resolved_down:
    return resolved_up, resolved_down, False

  close = _optional_float(previous_close)
  rate = _optional_float(limit_rate)
  if close is None or close <= 0 or rate is None or not 0 < rate < 1:
    return resolved_up, resolved_down, False

  tick = Decimal(str(max(float(price_tick or 0.01), 1e-8)))
  close_decimal = Decimal(str(close))
  rate_decimal = Decimal(str(rate))
  derived_up = float(
    (
      (close_decimal * (Decimal("1") + rate_decimal) / tick).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
      )
      * tick
    )
  )
  derived_down = float(
    (
      (close_decimal * (Decimal("1") - rate_decimal) / tick).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
      )
      * tick
    )
  )
  return resolved_up or derived_up, resolved_down or derived_down, True


def resolve_ashare_daily_limit_rate(
  instrument_code: str,
  trading_date: date | datetime,
  *,
  instrument_name: str = "",
  status_as_of_date: date | datetime | str | None = None,
  listing_date: date | datetime | str | None = None,
  expiry_date: date | datetime | str | None = None,
  is_st: Optional[bool] = None,
) -> Optional[float]:
  """Resolve a historical A-share price-limit rate without future prices.

  The raw daily upper/lower prices carried by a tick remain authoritative. This
  policy is only a backtest fallback for old tick archives that contain a
  previous close but no limit fields.  It deliberately requires lifecycle
  evidence: a code alone cannot distinguish IPO no-limit days, delisting
  transitions, or every historical risk-warning regime.

  ``listing_date`` and ``expiry_date`` are stable instrument-master facts.  A
  30-calendar-day guard is intentionally more conservative than the exchanges'
  first-five-trading-day exception and the usual delisting window.  Ambiguous
  cases return ``None`` so strict order risk continues to reject the order.
  A security name can establish historical ST status only when
  ``status_as_of_date`` is the event date; a current name never backfills the
  past.
  """

  trade_date = _as_date(trading_date)
  status_date = _as_date(status_as_of_date)
  listed_on = _as_date(listing_date)
  expires_on = _as_date(expiry_date)
  if trade_date is None or listed_on is None or expires_on is None:
    return None
  if trade_date < listed_on:
    return None
  if trade_date - listed_on < _CONSERVATIVE_SPECIAL_TRADING_WINDOW:
    return None
  if expires_on - trade_date < _CONSERVATIVE_SPECIAL_TRADING_WINDOW:
    return None

  name = "".join(str(instrument_name or "").upper().split())
  if name.startswith(("N", "C")) or "退" in name:
    return None

  code, exchange = _split_instrument_code(instrument_code)
  if not code or not exchange:
    return None

  if exchange == "BJ":
    if trade_date < _BEIJING_STOCK_EXCHANGE_OPEN_DATE:
      return None
    return 0.30

  if exchange == "SH" and code.startswith(("688", "689")):
    return 0.20

  if exchange == "SZ" and code.startswith("30"):
    if trade_date >= _CHINEXT_TWENTY_PCT_LIMIT_DATE:
      return 0.20
    resolved_st = _resolve_st_status(
      name,
      is_st,
      status_as_of_date=status_date,
      trading_date=trade_date,
    )
    if resolved_st is None:
      return None
    return 0.05 if resolved_st else 0.10

  is_main_board = (exchange == "SH" and code.startswith("60")) or (
    exchange == "SZ" and code.startswith("00")
  )
  if not is_main_board:
    return None
  if trade_date >= _MAIN_BOARD_ST_LIMIT_UNIFICATION_DATE:
    return 0.10

  resolved_st = _resolve_st_status(
    name,
    is_st,
    status_as_of_date=status_date,
    trading_date=trade_date,
  )
  if resolved_st is None:
    return None
  return 0.05 if resolved_st else 0.10


def _as_date(value: date | datetime | str | None) -> Optional[date]:
  if value is None:
    return None
  if isinstance(value, datetime):
    return value.date()
  if isinstance(value, date):
    return value
  try:
    return date.fromisoformat(str(value).strip()[:10])
  except (TypeError, ValueError):
    return None


def _split_instrument_code(instrument_code: str) -> tuple[str, str]:
  value = str(instrument_code or "").strip().upper()
  code, separator, exchange = value.partition(".")
  if not separator or not code.isdigit():
    return "", ""
  return code, exchange


def _resolve_st_status(
  name: str,
  is_st: Optional[bool],
  *,
  status_as_of_date: Optional[date],
  trading_date: date,
) -> Optional[bool]:
  if isinstance(is_st, bool):
    return is_st
  if not name or status_as_of_date != trading_date:
    return None
  return name.startswith(("*ST", "ST", "S*ST", "SST"))


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
        return OrderCheckResult.failed("odd_lot_sell", "不足100股的零股必须一次性卖出")

    if min_volume is not None and volume < int(min_volume):
      return OrderCheckResult.failed(
        "below_min_volume", f"订单数量低于最小申报量: {volume} < {min_volume}"
      )
    if max_volume is not None and volume > int(max_volume):
      return OrderCheckResult.failed(
        "above_max_volume", f"订单数量超过最大申报量: {volume} > {max_volume}"
      )
    return OrderCheckResult.passed()
