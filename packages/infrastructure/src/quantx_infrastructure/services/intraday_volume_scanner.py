"""Full-market intraday volume scanner for the screening page."""

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from threading import RLock
from typing import Any, Dict, Iterable, List, Optional

from quantx_infrastructure.core.data.whole_quote_hub import (
  QuoteDeliveryMode,
  WholeQuoteHub,
  whole_quote_hub,
)
from quantx_infrastructure.core.utils import time_utils

logger = logging.getLogger(__name__)

TRADING_MINUTES_PER_DAY = 240


@dataclass
class IntradayVolumeState:
  code: str
  current_price: float = 0.0
  pre_close: float = 0.0
  open_price: float = 0.0
  high_price: float = 0.0
  low_price: float = 0.0
  price_tick: float = 0.0
  up_stop_price: float = 0.0
  down_stop_price: float = 0.0
  stock_status: int = 0
  volume: float = 0.0
  amount: float = 0.0
  transaction_num: int = 0
  bid_price: List[float] = field(default_factory=list)
  ask_price: List[float] = field(default_factory=list)
  bid_vol: List[float] = field(default_factory=list)
  ask_vol: List[float] = field(default_factory=list)
  minute_volume: Dict[datetime, float] = field(default_factory=lambda: defaultdict(float))
  minute_close: Dict[datetime, float] = field(default_factory=dict)
  previous_volume: Optional[float] = None
  previous_amount: Optional[float] = None
  previous_transaction_num: Optional[int] = None
  transaction_delta: int = 0
  updated_at: Optional[datetime] = None


class IntradayVolumeScanner:
  """Maintains latest QMT Agent whole-quote data and derives screening metrics."""

  def __init__(
    self,
    hub: WholeQuoteHub = whole_quote_hub,
  ):
    self.hub = hub
    self._handle = ""
    self._states: Dict[str, IntradayVolumeState] = {}
    self._lock = RLock()
    self._last_access_at: Optional[datetime] = None

  @property
  def is_running(self) -> bool:
    return bool(self._handle) and self.hub.is_ready

  async def start(self) -> bool:
    with self._lock:
      self._last_access_at = time_utils.now()
      if self._handle:
        return True

    try:
      handle = await self.hub.subscribe_batches(
        self._handle_whole_quote,
        delivery=QuoteDeliveryMode.CRITICAL,
      )
    except Exception as exc:
      logger.warning("启动全市场量能扫描失败: %s", exc)
      return False

    if not handle:
      logger.warning("启动全市场量能扫描失败: WholeQuoteHub 未返回 handle")
      return False

    with self._lock:
      self._handle = handle
      self._last_access_at = time_utils.now()

    logger.info("全市场量能扫描已接入 WholeQuoteHub: handle=%s", handle)
    return True

  def touch(self) -> None:
    with self._lock:
      self._last_access_at = time_utils.now()

  async def stop(self) -> None:
    with self._lock:
      handle = self._handle
      self._handle = ""
    if not handle:
      return
    try:
      await self.hub.unsubscribe(handle)
      logger.info("全市场量能扫描已停止: handle=%s", handle)
    except Exception as exc:
      logger.warning("停止全市场量能扫描失败: %s", exc)

  def _handle_whole_quote(self, data: Dict[str, Dict[str, Any]]) -> None:
    if not isinstance(data, dict):
      return
    for code, tick in data.items():
      if isinstance(tick, dict):
        self.update_tick(code, tick)

  def update_tick(self, code: str, tick: Dict[str, Any]) -> None:
    updated_at = self._parse_tick_time(tick)
    price = self._number(tick.get("lastPrice"), tick.get("last_price"))
    volume = max(0.0, self._number(tick.get("volume")))
    amount = max(0.0, self._number(tick.get("amount"), tick.get("turnover")))
    transaction_num = max(0, int(self._number(tick.get("transactionNum"))))

    with self._lock:
      state = self._states.get(code)
      if state is None:
        state = IntradayVolumeState(code=code)
        self._states[code] = state

      volume_delta = self._delta(
        current=volume,
        previous=state.previous_volume,
        fallback=self._number(tick.get("tickvol")),
      )
      state.current_price = price
      state.pre_close = self._number(
        tick.get("lastClose"),
        tick.get("preClose"),
        tick.get("pre_close"),
      )
      state.open_price = self._number(tick.get("open"), tick.get("open_price"))
      state.high_price = self._number(tick.get("high"), tick.get("high_price"))
      state.low_price = self._number(tick.get("low"), tick.get("low_price"))
      state.price_tick = max(
        0.0,
        self._number(
          tick.get("priceTick"),
          tick.get("PriceTick"),
          tick.get("price_tick"),
        ),
      )
      state.up_stop_price = self._number(
        tick.get("upperLimit"),
        tick.get("upStopPrice"),
        tick.get("UpStopPrice"),
        tick.get("up_stop_price"),
      )
      state.down_stop_price = self._number(
        tick.get("lowerLimit"),
        tick.get("downStopPrice"),
        tick.get("DownStopPrice"),
        tick.get("down_stop_price"),
      )
      state.stock_status = int(
        self._number(tick.get("stockStatus"), tick.get("stock_status"))
      )
      state.volume = volume
      state.amount = amount
      state.transaction_num = transaction_num
      state.bid_price = self._number_list(tick.get("bidPrice"), tick.get("bid_price"))
      state.ask_price = self._number_list(tick.get("askPrice"), tick.get("ask_price"))
      state.bid_vol = self._number_list(tick.get("bidVol"), tick.get("bid_vol"))
      state.ask_vol = self._number_list(tick.get("askVol"), tick.get("ask_vol"))
      state.updated_at = updated_at
      state.transaction_delta = (
        max(0, transaction_num - state.previous_transaction_num)
        if state.previous_transaction_num is not None
        else 0
      )
      state.previous_volume = volume
      state.previous_amount = amount
      state.previous_transaction_num = transaction_num
      if volume_delta > 0:
        minute = updated_at.replace(second=0, microsecond=0)
        state.minute_volume[minute] += volume_delta
      if price > 0:
        minute = updated_at.replace(second=0, microsecond=0)
        state.minute_close[minute] = price
      self._prune_minutes(state, updated_at)

  def screen(
    self,
    baselines: Iterable[Dict[str, Any]],
    *,
    min_volume_pace_ratio: Optional[float] = None,
    min_amount_pace_ratio: Optional[float] = None,
    min_last_5m_volume_ratio: Optional[float] = None,
    min_intraday_turnover_rate: Optional[float] = None,
    min_depth_imbalance_5: Optional[float] = None,
    stale_after_seconds: int = 10,
    limit: int = 200,
    offset: int = 0,
  ) -> Dict[str, Any]:
    self.touch()
    now = time_utils.now()
    items: List[Dict[str, Any]] = []
    with self._lock:
      states = dict(self._states)

    for baseline in baselines:
      state = states.get(str(baseline.get("code") or ""))
      if state is None or state.updated_at is None:
        continue
      item = self._build_item(
        baseline,
        state,
        now=now,
        stale_after_seconds=stale_after_seconds,
      )
      if not self._passes(
        item,
        min_volume_pace_ratio=min_volume_pace_ratio,
        min_amount_pace_ratio=min_amount_pace_ratio,
        min_last_5m_volume_ratio=min_last_5m_volume_ratio,
        min_intraday_turnover_rate=min_intraday_turnover_rate,
        min_depth_imbalance_5=min_depth_imbalance_5,
      ):
        continue
      items.append(item)

    items.sort(
      key=lambda item: (
        item["volume_pace_ratio"],
        item["amount_pace_ratio"],
        item["last_5m_volume_ratio"],
      ),
      reverse=True,
    )
    total = len(items)
    page = items[offset : offset + limit]
    latest_update = max((item["updated_at"] for item in page), default=None)
    return {
      "items": page,
      "total": total,
      "updated_at": latest_update,
      "is_scanner_running": self.is_running,
    }

  def _build_item(
    self,
    baseline: Dict[str, Any],
    state: IntradayVolumeState,
    *,
    now: datetime,
    stale_after_seconds: int,
  ) -> Dict[str, Any]:
    progress = self._trading_progress(state.updated_at or now)
    avg_volume_20 = self._number(baseline.get("avg_volume_20"))
    avg_amount_20 = self._number(baseline.get("avg_amount_20"))
    float_volume = self._number(baseline.get("float_volume"))
    volume_ratio = self._safe_ratio(state.volume, avg_volume_20)
    amount_ratio = self._safe_ratio(state.amount, avg_amount_20)
    volume_pace_ratio = self._safe_ratio(state.volume, avg_volume_20 * progress)
    amount_pace_ratio = self._safe_ratio(state.amount, avg_amount_20 * progress)
    last_5m_volume = self._last_minutes_volume(state, state.updated_at or now, 5)
    last_5m_expected = avg_volume_20 * 5 / TRADING_MINUTES_PER_DAY
    last_5m_volume_ratio = self._safe_ratio(last_5m_volume, last_5m_expected)
    intraday_turnover = (
      state.volume * 100 / float_volume * 100 if float_volume > 0 else None
    )
    depth_imbalance = self._depth_imbalance(state)
    avg_trade_amount_proxy = (
      state.amount / state.transaction_num if state.transaction_num > 0 else None
    )
    change_pct = (
      (state.current_price - state.pre_close) / state.pre_close * 100
      if state.pre_close > 0 and state.current_price > 0
      else 0.0
    )
    is_stale = (
      (now - (state.updated_at or now)).total_seconds()
      > max(1, stale_after_seconds)
    )
    signals = self._signals(
      volume_pace_ratio=volume_pace_ratio,
      amount_pace_ratio=amount_pace_ratio,
      last_5m_volume_ratio=last_5m_volume_ratio,
      intraday_turnover=intraday_turnover,
      depth_imbalance=depth_imbalance,
      transaction_delta=state.transaction_delta,
    )
    return {
      "code": state.code,
      "name": baseline.get("name") or state.code,
      "industry": baseline.get("industry"),
      "instrument_type": baseline.get("instrument_type") or "stock",
      "current_price": round(state.current_price, 4),
      "change_pct": round(change_pct, 4),
      "volume": round(state.volume, 2),
      "amount": round(state.amount, 2),
      "volume_ratio": round(volume_ratio, 4),
      "amount_ratio": round(amount_ratio, 4),
      "volume_pace_ratio": round(volume_pace_ratio, 4),
      "amount_pace_ratio": round(amount_pace_ratio, 4),
      "last_5m_volume_ratio": round(last_5m_volume_ratio, 4),
      "intraday_turnover_rate_pct": round(intraday_turnover, 4)
      if intraday_turnover is not None
      else None,
      "depth_imbalance_5": round(depth_imbalance, 4),
      "avg_trade_amount_proxy": round(avg_trade_amount_proxy, 2)
      if avg_trade_amount_proxy is not None
      else None,
      "matched_signals": signals,
      "updated_at": state.updated_at or now,
      "is_stale": is_stale,
    }

  def snapshot_states(self) -> Dict[str, IntradayVolumeState]:
    """Return a shallow snapshot for a single Engine-owned derived monitor."""
    self.touch()
    with self._lock:
      return dict(self._states)

  def _passes(self, item: Dict[str, Any], **thresholds) -> bool:
    metric_aliases = {
      "intraday_turnover_rate": "intraday_turnover_rate_pct",
    }
    for key, value in thresholds.items():
      if value is None:
        continue
      metric_key = key.removeprefix("min_")
      metric_key = metric_aliases.get(metric_key, metric_key)
      metric_value = item.get(metric_key)
      if metric_value is None or metric_value < value:
        return False
    return True

  def _signals(
    self,
    *,
    volume_pace_ratio: float,
    amount_pace_ratio: float,
    last_5m_volume_ratio: float,
    intraday_turnover: Optional[float],
    depth_imbalance: float,
    transaction_delta: int,
  ) -> List[str]:
    signals: List[str] = []
    if volume_pace_ratio >= 2.0:
      signals.append("盘中放量")
    if amount_pace_ratio >= 2.0:
      signals.append("成交额加速")
    if last_5m_volume_ratio >= 3.0:
      signals.append("近5分钟放量")
    if intraday_turnover is not None and intraday_turnover >= 3.0:
      signals.append("盘中高换手")
    if depth_imbalance >= 0.3:
      signals.append("买盘占优")
    if depth_imbalance <= -0.3:
      signals.append("卖盘占优")
    if transaction_delta > 0:
      signals.append("成交活跃")
    return signals

  def _depth_imbalance(self, state: IntradayVolumeState) -> float:
    bid = sum(state.bid_vol[:5])
    ask = sum(state.ask_vol[:5])
    total = bid + ask
    return (bid - ask) / total if total > 0 else 0.0

  def _last_minutes_volume(
    self,
    state: IntradayVolumeState,
    value: datetime,
    minutes: int,
  ) -> float:
    cutoff = value.replace(second=0, microsecond=0)
    result = 0.0
    for minute, volume in state.minute_volume.items():
      delta = (cutoff - minute).total_seconds()
      if 0 <= delta < minutes * 60:
        result += volume
    return result

  def _prune_minutes(self, state: IntradayVolumeState, value: datetime) -> None:
    cutoff = value.replace(second=0, microsecond=0)
    stale = [
      minute
      for minute in state.minute_volume
      if (cutoff - minute).total_seconds() > 30 * 60
    ]
    for minute in stale:
      state.minute_volume.pop(minute, None)
      state.minute_close.pop(minute, None)

  def _trading_progress(self, value: datetime) -> float:
    local = time_utils.to_shanghai(value)
    elapsed = self._elapsed_trading_minutes(local.time())
    return max(1, min(TRADING_MINUTES_PER_DAY, elapsed)) / TRADING_MINUTES_PER_DAY

  def _elapsed_trading_minutes(self, value: time) -> int:
    minutes = value.hour * 60 + value.minute
    morning_start = 9 * 60 + 30
    morning_end = 11 * 60 + 30
    afternoon_start = 13 * 60
    afternoon_end = 15 * 60
    if minutes < morning_start:
      return 0
    if minutes <= morning_end:
      return minutes - morning_start + 1
    if minutes < afternoon_start:
      return 120
    if minutes <= afternoon_end:
      return 120 + minutes - afternoon_start + 1
    return TRADING_MINUTES_PER_DAY

  def _parse_tick_time(self, tick: Dict[str, Any]) -> datetime:
    raw_time = tick.get("time")
    if raw_time:
      try:
        return time_utils.to_shanghai(
          datetime.fromtimestamp(float(raw_time) / 1000, timezone.utc)
        )
      except Exception:
        pass
    timetag = tick.get("timetag")
    if isinstance(timetag, str) and timetag:
      for fmt in ("%Y%m%d %H:%M:%S.%f", "%Y%m%d %H:%M:%S"):
        try:
          return time_utils.to_shanghai(datetime.strptime(timetag, fmt))
        except ValueError:
          continue
    return time_utils.now()

  def _number(self, *values: Any) -> float:
    for value in values:
      try:
        number = float(value)
      except (TypeError, ValueError):
        continue
      if number == number:
        return number
    return 0.0

  def _number_list(self, *values: Any) -> List[float]:
    for value in values:
      if isinstance(value, list):
        return [self._number(item) for item in value[:5]]
    return []

  def _safe_ratio(self, value: float, denominator: float) -> float:
    return value / denominator if denominator > 0 else 0.0

  def _delta(
    self,
    *,
    current: float,
    previous: Optional[float],
    fallback: float = 0.0,
  ) -> float:
    if previous is None:
      return max(0.0, fallback)
    return max(0.0, current - previous)


intraday_volume_scanner = IntradayVolumeScanner()
