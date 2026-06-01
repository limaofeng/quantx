"""
A-share supermarket strategy with T+1 sell queue, box buying, and layered risk control.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import pandas as pd

from core.strategies.base import (
  OrderStateEvent,
  StrategyBase,
  StrategyCadence,
  StrategyInput,
  StrategyOutput,
  TradeExecutionEvent,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentPriority,
)
from core.strategies.universe import CandidatePool
from models.enums import (
  RiskControlLevel,
  SellReason,
  StrategyCategory,
  StrategyInstrumentScope,
)
from models.parameter_schema import ParameterProperty, ParameterSchema
from core.utils import time_utils


class StrategyState(str, Enum):
  INITIALIZED = "initialized"
  RUNNING = "running"
  PAUSED = "paused"
  STOPPED = "stopped"


@dataclass
class PositionState:
  """Strategy-local analytics state, not authoritative account position."""

  instrument_code: str
  volume: int
  entry_price: float
  entry_date: date
  holding_bars: int = 0
  highest_price: float = 0.0
  last_price: float = 0.0


@dataclass
class PeriodSettings:
  box_window: int
  time_stop_bars: int
  buy_threshold_pct: float
  structure_break_pct: float
  stop_loss_pct: float
  take_profit_pct: float


class AshareSupermarketStrategy(StrategyBase):
  """
  Diversified A-share strategy with T+1 constraint and layered sell priorities.
  """

  CATEGORY = StrategyCategory.MEAN_REVERSION
  RISK_LEVEL = "medium"
  TAGS = ["T+1", "box", "diversified", "risk-control", "A-share"]
  INSTRUMENT_SCOPE = StrategyInstrumentScope.MULTI

  SELL_PRIORITY = {
    SellReason.RISK_CONTROL: 0,
    SellReason.STOP_LOSS: 1,
    SellReason.STRUCTURE_BREAK: 2,
    SellReason.TIME_STOP: 3,
    SellReason.TAKE_PROFIT: 4,
    SellReason.REBALANCE: 5,
  }

  @property
  def name(self) -> str:
    return "A股超市策略"

  @property
  def version(self) -> str:
    return "1.0.0"

  @property
  def description(self) -> str:
    return "A 股多标的分散持仓策略，采用箱体支撑进场与分层风控实现动态仓位管理。"

  @classmethod
  def get_data_requirements(cls) -> Dict[str, Any]:
    return {"use_tick_data": False, "periods": ["1d"]}

  @classmethod
  def get_parameter_schema(cls) -> ParameterSchema:
    return ParameterSchema(
      type="object",
      properties={
        "target_positions": ParameterProperty(
          type="integer",
          minimum=5,
          maximum=50,
          default=20,
          title="Target holdings",
          description="Target number of concurrent positions.",
          group="position",
        ),
        "min_position_pct": ParameterProperty(
          type="number",
          minimum=0.01,
          maximum=0.1,
          default=0.02,
          title="Min position pct",
          description="Minimum allocation per position.",
          group="position",
          step=0.01,
        ),
        "max_position_pct": ParameterProperty(
          type="number",
          minimum=0.02,
          maximum=0.2,
          default=0.06,
          title="Max position pct",
          description="Maximum allocation per position.",
          group="position",
          step=0.01,
        ),
        "buy_threshold_pct": ParameterProperty(
          type="number",
          minimum=0.0,
          maximum=0.05,
          default=0.02,
          title="Box buy threshold",
          description="Buy when price is within this pct above support.",
          group="entry",
          step=0.005,
        ),
        "buy_threshold_pct_60m": ParameterProperty(
          type="number",
          minimum=0.0,
          maximum=0.05,
          default=0.02,
          title="60m buy threshold",
          description="Buy threshold for 60m bars.",
          group="entry",
          step=0.005,
        ),
        "stop_loss_pct": ParameterProperty(
          type="number",
          minimum=0.0,
          maximum=0.1,
          default=0.03,
          title="Stop loss pct",
          description="Loss threshold to trigger stop loss.",
          group="exit",
          step=0.005,
        ),
        "take_profit_pct": ParameterProperty(
          type="number",
          minimum=0.0,
          maximum=0.2,
          default=0.05,
          title="Take profit pct",
          description="Profit threshold to trigger take profit.",
          group="exit",
          step=0.005,
        ),
        "structure_break_pct": ParameterProperty(
          type="number",
          minimum=0.0,
          maximum=0.1,
          default=0.01,
          title="Structure break pct",
          description="Breakdown below support threshold.",
          group="exit",
          step=0.005,
        ),
        "time_stop_bars_daily": ParameterProperty(
          type="integer",
          minimum=5,
          maximum=60,
          default=20,
          title="Time stop bars (daily)",
          description="Max holding bars for daily period when no profit.",
          group="exit",
        ),
        "time_stop_bars_60m": ParameterProperty(
          type="integer",
          minimum=10,
          maximum=200,
          default=80,
          title="Time stop bars (60m)",
          description="Max holding bars for 60m period when no profit.",
          group="exit",
        ),
        "box_window_daily": ParameterProperty(
          type="integer",
          minimum=10,
          maximum=120,
          default=20,
          title="Box window (daily)",
          description="Lookback window for daily box detection.",
          group="entry",
        ),
        "box_window_60m": ParameterProperty(
          type="integer",
          minimum=20,
          maximum=300,
          default=80,
          title="Box window (60m)",
          description="Lookback window for 60m box detection.",
          group="entry",
        ),
        "max_daily_loss_pct": ParameterProperty(
          type="number",
          minimum=0.0,
          maximum=0.1,
          default=0.02,
          title="Daily loss limit",
          description="Stop opening new positions after this daily loss.",
          group="risk",
          step=0.005,
        ),
        "max_drawdown_pct": ParameterProperty(
          type="number",
          minimum=0.0,
          maximum=0.2,
          default=0.08,
          title="Max drawdown",
          description="Liquidate positions after this drawdown.",
          group="risk",
          step=0.005,
        ),
        "loss_streak_reduce": ParameterProperty(
          type="integer",
          minimum=1,
          maximum=10,
          default=3,
          title="Loss streak reduce",
          description="Loss streak to reduce position sizing.",
          group="risk",
        ),
        "loss_streak_stop": ParameterProperty(
          type="integer",
          minimum=2,
          maximum=10,
          default=5,
          title="Loss streak stop",
          description="Loss streak to stop opening trades.",
          group="risk",
        ),
        "max_turnover_per_day": ParameterProperty(
          type="integer",
          minimum=1,
          maximum=20,
          default=4,
          title="Max turnover per day",
          description="Max new positions per trading day.",
          group="position",
        ),
        "max_price_history": ParameterProperty(
          type="integer",
          minimum=20,
          maximum=500,
          default=200,
          title="Max price history",
          description="Max price history length per instrument.",
          group="system",
        ),
        "market": ParameterProperty(
          type="string",
          default="SH",
          title="Market",
          description="Trading calendar market.",
          group="system",
        ),
      },
      required=["target_positions"],
      additionalProperties=False,
    )

  async def on_init(self) -> None:
    self.target_positions = self.get_parameter("target_positions", 20)
    self.min_position_pct = self.get_parameter("min_position_pct", 0.02)
    self.max_position_pct = self.get_parameter("max_position_pct", 0.06)
    self.buy_threshold_pct = self.get_parameter("buy_threshold_pct", 0.02)
    self.buy_threshold_pct_60m = self.get_parameter(
      "buy_threshold_pct_60m", self.buy_threshold_pct
    )
    self.stop_loss_pct = self.get_parameter("stop_loss_pct", 0.03)
    self.take_profit_pct = self.get_parameter("take_profit_pct", 0.05)
    self.structure_break_pct = self.get_parameter("structure_break_pct", 0.01)
    self.time_stop_bars_daily = self.get_parameter("time_stop_bars_daily", 20)
    self.time_stop_bars_60m = self.get_parameter("time_stop_bars_60m", 80)
    self.box_window_daily = self.get_parameter("box_window_daily", 20)
    self.box_window_60m = self.get_parameter("box_window_60m", 80)
    self.max_daily_loss_pct = self.get_parameter("max_daily_loss_pct", 0.02)
    self.max_drawdown_pct = self.get_parameter("max_drawdown_pct", 0.08)
    self.loss_streak_reduce = self.get_parameter("loss_streak_reduce", 3)
    self.loss_streak_stop = self.get_parameter("loss_streak_stop", 5)
    self.max_turnover_per_day = self.get_parameter("max_turnover_per_day", 4)
    self.max_price_history = self.get_parameter("max_price_history", 200)
    self.market = self.get_parameter("market", "SH")

    if self.min_position_pct > self.max_position_pct:
      raise ValueError("min_position_pct must not exceed max_position_pct")

    self.strategy_state = StrategyState.INITIALIZED
    self.pending_exit_reasons: Dict[str, SellReason] = {}
    self.tracked_positions: Dict[str, PositionState] = {}
    self.pending_entry_codes: Set[str] = set()
    self.price_history: Dict[str, List[float]] = {}
    self.last_prices: Dict[str, float] = {}
    self.rebalance_out_codes: Set[str] = set()
    self.candidates: pd.DataFrame = pd.DataFrame()

    self.reference_equity = float(self.context.initial_capital)
    self.realized_pnl = 0.0
    self.current_date: Optional[date] = None
    self.daily_entry_signal_count = 0
    self.loss_streak = 0
    self.position_scale = 1.0
    self.risk_level = RiskControlLevel.NORMAL

    self.candidate_pool = CandidatePool()
    self.period_settings = {
      "1d": PeriodSettings(
        box_window=self.box_window_daily,
        time_stop_bars=self.time_stop_bars_daily,
        buy_threshold_pct=self.buy_threshold_pct,
        structure_break_pct=self.structure_break_pct,
        stop_loss_pct=self.stop_loss_pct,
        take_profit_pct=self.take_profit_pct,
      ),
      "60m": PeriodSettings(
        box_window=self.box_window_60m,
        time_stop_bars=self.time_stop_bars_60m,
        buy_threshold_pct=self.buy_threshold_pct_60m,
        structure_break_pct=self.structure_break_pct,
        stop_loss_pct=self.stop_loss_pct,
        take_profit_pct=self.take_profit_pct,
      ),
    }

  async def start(self) -> None:
    await super().start()
    self.strategy_state = StrategyState.RUNNING

  async def step(self, input: StrategyInput) -> StrategyOutput:
    if input.cadence != StrategyCadence.BAR:
      return StrategyOutput()
    bar = input.event
    if not self.is_running or self.strategy_state in (
      StrategyState.PAUSED,
      StrategyState.STOPPED,
    ):
      return StrategyOutput()

    code = self._resolve_code(bar)
    if not code:
      return StrategyOutput()

    self.context.current_time = getattr(bar, "time", None)
    bar_date = self._resolve_bar_date(bar)
    period_key = self._get_period_key(getattr(bar, "period", "1d"))
    settings = self._get_period_settings(period_key)

    self._update_price_history(code, bar.close)
    self._update_position_from_bar(code, bar.close)
    self._update_daily_state(bar_date, settings)
    self._update_risk_control()

    intents: List[TradeIntent] = []
    position_profile = input.position_profile or {}
    allow_bucket_buy = position_profile.get("allow_bucket_buy", {})
    allow_bucket_sell = position_profile.get("allow_bucket_sell", {})
    allow_swing_buy = bool(
      allow_bucket_buy.get("swing", position_profile.get("allow_swing_buy", True))
    )
    allow_swing_sell = bool(
      allow_bucket_sell.get("swing", position_profile.get("allow_swing_sell", True))
    )

    if self.risk_level == RiskControlLevel.LIQUIDATE:
      if allow_swing_sell:
        intents.extend(self._generate_liquidation_intents(bar_date))
      return StrategyOutput(trade_intents=intents)

    sell_intent = (
      self._maybe_generate_sell_intent(code, bar.close, bar_date, settings)
      if allow_swing_sell
      else None
    )
    if sell_intent:
      intents.append(sell_intent)

    if allow_swing_buy and self._can_open_new_position(code):
      buy_intent = await self._maybe_generate_buy_intent(
        code, bar.close, bar_date, settings
      )
      if buy_intent:
        buy_intent = self._apply_position_profile_to_buy_intent(
          buy_intent, position_profile
        )
      if buy_intent:
        intents.append(buy_intent)

    return StrategyOutput(trade_intents=intents)

  async def warmup(self, input: StrategyInput) -> None:
    if input.cadence != StrategyCadence.BAR:
      return None
    bar = input.event
    if not self.is_running or self.strategy_state in (
      StrategyState.PAUSED,
      StrategyState.STOPPED,
    ):
      return None

    code = self._resolve_code(bar)
    if not code:
      return None

    self.context.current_time = getattr(bar, "time", None)
    bar_date = self._resolve_bar_date(bar)
    period_key = self._get_period_key(getattr(bar, "period", "1d"))
    settings = self._get_period_settings(period_key)

    self._update_price_history(code, bar.close)
    self._update_position_from_bar(code, bar.close)
    self._update_daily_state(bar_date, settings)
    self._update_risk_control()
    return None

  async def on_stop(self) -> None:
    self.strategy_state = StrategyState.STOPPED

  async def pause(self) -> None:
    if self.strategy_state == StrategyState.RUNNING:
      self.strategy_state = StrategyState.PAUSED
      self.is_running = False

  async def resume(self) -> None:
    if self.strategy_state == StrategyState.PAUSED:
      self.strategy_state = StrategyState.RUNNING
      self.is_running = True

  def set_candidates(self, candidates: Optional[pd.DataFrame]) -> None:
    self.candidates = candidates.copy() if candidates is not None else pd.DataFrame()

  def update_candidates(
    self, universe: Sequence[Dict[str, Any]], price_map: Dict[str, Sequence[float]]
  ) -> pd.DataFrame:
    self.candidates = self.candidate_pool.build_candidates(universe, price_map)
    return self.candidates

  async def on_order(self, event: OrderStateEvent):
    status = event.status
    request = event.request
    code = self._extract(request, "instrument_code")
    order_type = str(self._extract(request, "order_type", "") or "").split(".")[-1].upper()
    if status in {"REJECTED", "CANCELLED", "EXPIRED"} and order_type == "BUY" and code:
      self.pending_entry_codes.discard(str(code))
    return None

  async def on_trade(self, event: TradeExecutionEvent):
    code = event.instrument_code
    if not code:
      return None
    code = str(code)
    price = event.price
    volume = event.volume
    if price <= 0 or volume <= 0:
      return None

    trade_type = event.trade_type
    trade_time = event.trade_time or time_utils.now()
    trade_date = trade_time.date() if isinstance(trade_time, datetime) else time_utils.today()

    if trade_type == "BUY":
      self.pending_entry_codes.discard(code)
      existing = self.tracked_positions.get(code)
      if existing:
        total_value = existing.entry_price * existing.volume + price * volume
        existing.volume += volume
        existing.entry_price = total_value / existing.volume
        existing.last_price = price
        existing.highest_price = max(existing.highest_price, price)
      else:
        self.tracked_positions[code] = PositionState(
          instrument_code=code,
          volume=volume,
          entry_price=price,
          entry_date=trade_date,
          highest_price=price,
          last_price=price,
        )
      return None

    if trade_type == "SELL":
      state = self.tracked_positions.get(code)
      if not state:
        return None
      pnl_pct = (price - state.entry_price) / state.entry_price if state.entry_price else 0.0
      self.record_trade_result(pnl_pct)
      self.realized_pnl += (price - state.entry_price) * min(volume, state.volume)
      state.volume -= volume
      if state.volume <= 0:
        self.tracked_positions.pop(code, None)
        self.pending_exit_reasons.pop(code, None)
      else:
        state.last_price = price
    return None

  def _extract(self, source: Any, key: str, default: Any = None) -> Any:
    if source is None:
      return default
    if isinstance(source, dict):
      return source.get(key, default)
    return getattr(source, key, default)

  def record_trade_result(self, pnl_pct: float) -> None:
    if pnl_pct < 0:
      self.loss_streak += 1
    else:
      self.loss_streak = 0
    self._update_risk_control()

  def _resolve_code(self, bar: Any) -> Optional[str]:
    return getattr(bar, "code", None) or getattr(bar, "stock_code", None)

  def _resolve_bar_date(self, bar: Any) -> date:
    bar_time = getattr(bar, "time", None)
    if isinstance(bar_time, datetime):
      return bar_time.date()
    return time_utils.today()

  def _get_period_key(self, period: str) -> str:
    period_value = (period or "").lower()
    if "60" in period_value or "1h" in period_value:
      return "60m"
    return "1d"

  def _get_period_settings(self, period_key: str) -> PeriodSettings:
    return self.period_settings.get(period_key, self.period_settings["1d"])

  def _update_price_history(self, code: str, close_price: float) -> None:
    history = self.price_history.setdefault(code, [])
    history.append(float(close_price))
    if len(history) > self.max_price_history:
      self.price_history[code] = history[-self.max_price_history :]
    self.last_prices[code] = float(close_price)

  def _update_position_from_bar(self, code: str, close_price: float) -> None:
    state = self.tracked_positions.get(code)
    if not state:
      return
    state.holding_bars += 1
    state.last_price = close_price
    state.highest_price = max(state.highest_price, close_price)

  def _update_daily_state(self, bar_date: date, settings: PeriodSettings) -> None:
    if self.current_date == bar_date:
      return
    self.current_date = bar_date
    self.daily_entry_signal_count = 0
    self._compute_rebalance_targets(settings)

  def _update_risk_control(self) -> RiskControlLevel:
    if self.loss_streak >= self.loss_streak_stop:
      self.risk_level = RiskControlLevel.STOP_ALL
    elif self.loss_streak >= self.loss_streak_reduce:
      self.risk_level = RiskControlLevel.REDUCE
    else:
      self.risk_level = RiskControlLevel.NORMAL

    self.position_scale = 0.5 if self.risk_level == RiskControlLevel.REDUCE else 1.0
    return self.risk_level

  def _compute_rebalance_targets(self, settings: PeriodSettings) -> None:
    ranked = self._rank_candidates(settings)
    current = set(self.tracked_positions.keys())
    if len(current) <= self.target_positions:
      self.rebalance_out_codes = set()
      return
    keep = set(ranked[: self.target_positions])
    self.rebalance_out_codes = current - keep

  def _rank_candidates(self, settings: PeriodSettings) -> List[str]:
    if self.candidates.empty:
      return []
    if "code" not in self.candidates.columns:
      return []
    ranked: List[Tuple[float, str]] = []
    for code in self.candidates["code"].astype(str).tolist():
      price = self.last_prices.get(code)
      if price is None:
        continue
      box_info = self._get_box_info(code, settings)
      if not box_info["structure_ok"] or not box_info["box_valid"]:
        continue
      support = box_info["support"]
      if not support:
        continue
      distance = (price - support) / support
      ranked.append((distance, code))
    ranked.sort(key=lambda item: item[0])
    return [code for _, code in ranked]

  def _get_box_info(self, code: str, settings: PeriodSettings) -> Dict[str, Any]:
    support = None
    resistance = None
    box_valid = False
    structure_ok = True
    if not self.candidates.empty and "code" in self.candidates.columns:
      row = self.candidates.loc[self.candidates["code"].astype(str) == code]
      if not row.empty:
        if "box_support" in row.columns:
          support = row["box_support"].iloc[0]
        if "box_resistance" in row.columns:
          resistance = row["box_resistance"].iloc[0]
        if "box_valid" in row.columns:
          box_valid = bool(row["box_valid"].iloc[0])
        if "structure_ok" in row.columns:
          structure_ok = bool(row["structure_ok"].iloc[0])

    if support is None:
      box = self.candidate_pool.detect_box(
        self.price_history.get(code), window=settings.box_window
      )
      support = box["support"]
      resistance = box["resistance"]
      box_valid = box["is_valid"]

    return {
      "support": support,
      "resistance": resistance,
      "box_valid": box_valid,
      "structure_ok": structure_ok,
    }

  def _calculate_allocation_pct(self, distance_pct: float, settings: PeriodSettings) -> float:
    threshold = max(settings.buy_threshold_pct, 1e-6)
    distance_clamped = max(0.0, min(distance_pct, threshold))
    slope = (self.max_position_pct - self.min_position_pct) / threshold
    allocation = self.max_position_pct - slope * distance_clamped
    return max(self.min_position_pct, min(allocation, self.max_position_pct))

  def _calculate_position_size(self, price: float, allocation_pct: float) -> int:
    if price <= 0 or self.reference_equity <= 0:
      return 0
    budget = self.reference_equity * allocation_pct * self.position_scale
    lots = int(budget // (price * 100))
    return max(lots * 100, 0)

  def _can_open_new_position(self, code: str) -> bool:
    if code in self.tracked_positions or code in self.pending_entry_codes:
      return False
    if self.risk_level in (
      RiskControlLevel.STOP_OPEN,
      RiskControlLevel.STOP_ALL,
      RiskControlLevel.LIQUIDATE,
    ):
      return False
    if len(self.tracked_positions) + len(self.pending_entry_codes) >= self.target_positions:
      return False
    if self.daily_entry_signal_count >= self.max_turnover_per_day:
      return False
    return True

  async def _maybe_generate_buy_intent(
    self, code: str, price: float, bar_date: date, settings: PeriodSettings
  ) -> Optional[TradeIntent]:
    ranked = self._rank_candidates(settings)
    if ranked and code not in ranked[: max(1, self.target_positions)]:
      return None

    box_info = self._get_box_info(code, settings)
    if not box_info["structure_ok"] or not box_info["box_valid"]:
      return None
    support = box_info["support"]
    if not support:
      return None

    distance_pct = (price - support) / support
    if distance_pct > settings.buy_threshold_pct:
      return None

    allocation_pct = self._calculate_allocation_pct(distance_pct, settings)
    target_position_pct = allocation_pct * self.position_scale
    if target_position_pct <= 0:
      return None

    self.pending_entry_codes.add(code)
    self.daily_entry_signal_count += 1
    return TradeIntent(
      strategy_id=self.name,
      run_id=self.context.run_id,
      instrument_code=code,
      direction=TradeIntentDirection.BUY,
      bucket="swing",
      reason="box_support_buy",
      priority=TradeIntentPriority.NORMAL,
      confidence=0.7,
      target_position_pct=target_position_pct,
      limit_price_hint=price,
      metadata={
        "reason": "box_support_buy",
        "support": support,
        "distance_pct": distance_pct,
        "allocation_pct": allocation_pct,
        "target_position_pct": target_position_pct,
      },
    )

  def _apply_position_profile_to_buy_intent(
    self, intent: TradeIntent, position_profile: Dict[str, Any]
  ) -> Optional[TradeIntent]:
    if intent.target_position_pct is None:
      return None
    bucket_caps = position_profile.get("bucket_caps", {})
    swing_cap = bucket_caps.get("swing", {}).get("max_pct")
    if swing_cap is None:
      swing_cap = position_profile.get("swing_max_pct")
    if swing_cap is None:
      swing_cap = position_profile.get("max_position_pct")
    try:
      cap_value = float(swing_cap)
    except (TypeError, ValueError) as exc:
      intent.metadata["position_profile_cap_invalid"] = str(exc)
      intent.metadata["position_profile_cap_raw"] = repr(swing_cap)
      self.log_warning(
        f"拒绝买入意图，仓位上限配置无效: instrument={intent.instrument_code}, "
        f"run_id={self.context.run_id}, position_profile_cap={repr(swing_cap)}"
      )
      return None
    capped_pct = min(float(intent.target_position_pct), cap_value)
    if capped_pct <= 0:
      return None
    if capped_pct < intent.target_position_pct:
      intent.target_position_pct = capped_pct
      intent.metadata["target_position_pct"] = capped_pct
      intent.metadata["position_profile_cap"] = position_profile.get("profile")
    return intent

  def _maybe_generate_sell_intent(
    self, code: str, price: float, bar_date: date, settings: PeriodSettings
  ) -> Optional[TradeIntent]:
    state = self.tracked_positions.get(code)
    if not state:
      return None
    reason = self._evaluate_sell_reason(code, price, settings)
    if not reason:
      return None
    return TradeIntent(
      strategy_id=self.name,
      run_id=self.context.run_id,
      instrument_code=code,
      direction=TradeIntentDirection.SELL,
      bucket="swing",
      reason=reason.value,
      priority=TradeIntentPriority.HIGH,
      confidence=0.8,
      limit_price_hint=price,
      metadata={
        "reason": reason.value,
        "entry_price": state.entry_price,
        "sell_all": True,
      },
    )

  def _evaluate_sell_reason(
    self, code: str, price: float, settings: PeriodSettings
  ) -> Optional[SellReason]:
    state = self.tracked_positions.get(code)
    if not state or state.entry_price <= 0:
      return None

    pnl_pct = (price - state.entry_price) / state.entry_price
    box_info = self._get_box_info(code, settings)
    support = box_info["support"]
    structure_break = False
    if support:
      structure_break = price < support * (1 - settings.structure_break_pct)

    if pnl_pct <= -settings.stop_loss_pct:
      return SellReason.STOP_LOSS
    if structure_break:
      return SellReason.STRUCTURE_BREAK
    if state.holding_bars >= settings.time_stop_bars and pnl_pct <= 0:
      return SellReason.TIME_STOP
    if pnl_pct >= settings.take_profit_pct:
      return SellReason.TAKE_PROFIT
    if code in self.rebalance_out_codes:
      return SellReason.REBALANCE
    return None

  def _generate_liquidation_intents(self, bar_date: date) -> List[TradeIntent]:
    intents: List[TradeIntent] = []
    for code, state in self.tracked_positions.items():
      price = self.last_prices.get(code, state.entry_price)
      intents.append(
        TradeIntent(
          strategy_id=self.name,
          run_id=self.context.run_id,
          instrument_code=code,
          direction=TradeIntentDirection.SELL,
          bucket="swing",
          reason=SellReason.RISK_CONTROL.value,
          priority=TradeIntentPriority.URGENT,
          confidence=0.95,
          limit_price_hint=price,
          metadata={"reason": SellReason.RISK_CONTROL.value, "sell_all": True},
        )
      )
    return intents

  def get_strategy_statistics(self) -> Dict[str, Any]:
    stats = self.get_statistics()
    stats.update(
      {
        "reference_equity": self.reference_equity,
        "realized_pnl": self.realized_pnl,
        "risk_level": self.risk_level.value,
        "loss_streak": self.loss_streak,
        "tracked_positions": len(self.tracked_positions),
        "pending_entries": len(self.pending_entry_codes),
        "pending_exit_reasons": len(self.pending_exit_reasons),
        "state": self.strategy_state.value,
      }
    )
    return stats
