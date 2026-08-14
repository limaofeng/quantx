"""A 股单标的动态天平双仓策略（仅 long-only）。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from quantx_domain.enums import StrategyCategory, StrategyInstrumentScope
from quantx_domain.market import Tick
from quantx_domain.schemas import ParameterProperty, ParameterSchema
from quantx_domain.state_schema import StateProperty, StateSchema
from quantx_domain.strategies.base import (
  OrderStateEvent,
  RuntimeStatePatch,
  StrategyBase,
  StrategyCadence,
  StrategyInput,
  StrategyOutput,
  TradeExecutionEvent,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentPriority,
)
from quantx_domain.trading.bucket_ledger import (
  CORE_BUCKET,
  LOCKED_CORE_BUCKET,
  SWING_BUCKET,
)


class BalanceTrendState:
  LOW_ACCUMULATION = "LOW_ACCUMULATION"
  UPTREND = "UPTREND"
  NEUTRAL = "NEUTRAL"
  HIGH_DISTRIBUTION = "HIGH_DISTRIBUTION"
  DOWNTREND = "DOWNTREND"


class BalancePositionPhase:
  BUILDING_CORE = "BUILDING_CORE"
  BALANCED_RUN = "BALANCED_RUN"
  DISTRIBUTION = "DISTRIBUTION"
  DEFENSIVE = "DEFENSIVE"


@dataclass
class BalanceTargets:
  signal: float
  target_total_pct: float
  target_core_pct: float
  target_swing_pct: float
  locked_core_pct: float
  core_share: float


class AshareDynamicBalanceDualBucketStrategy(StrategyBase):
  """Single-instrument dynamic balance strategy with core/swing buckets."""

  CATEGORY = StrategyCategory.MEAN_REVERSION
  RISK_LEVEL = "medium"
  TAGS = ["A股", "动态天平", "双仓", "T+1", "单标的"]
  INSTRUMENT_SCOPE = StrategyInstrumentScope.SINGLE

  @property
  def name(self) -> str:
    return "A股动态天平双仓策略"

  @property
  def version(self) -> str:
    return "1.0.0"

  @property
  def description(self) -> str:
    return "面向单一 A 股标的的双仓动态仓位策略，基于核心仓与活跃仓进行分仓调节。"

  @classmethod
  def get_parameter_schema(cls) -> ParameterSchema:
    return ParameterSchema(
      type="object",
      properties={
        "max_position_pct": ParameterProperty(type="number", minimum=0.1, maximum=0.9, default=0.8, group="risk"),
        "neutral_position_pct": ParameterProperty(type="number", minimum=0.0, maximum=0.8, default=0.45, group="balance"),
        "min_position_pct": ParameterProperty(type="number", minimum=0.0, maximum=0.5, default=0.05, group="balance"),
        "cash_buffer_pct": ParameterProperty(
          type="number",
          minimum=0.0,
          maximum=0.8,
          default=0.20,
          title="现金缓冲比例",
          description="保留为现金、不参与目标仓位分配的资产比例。",
          group="risk",
          unit="比例",
          step=0.01,
          mobileEditable=True,
          mobileApplyImmediately=False,
          mobileRiskLevel="MEDIUM",
        ),
        "core_base_share": ParameterProperty(type="number", minimum=0.4, maximum=1.0, default=0.75, group="bucket"),
        "core_min_share": ParameterProperty(type="number", minimum=0.0, maximum=1.0, default=0.60, group="bucket"),
        "core_max_share": ParameterProperty(type="number", minimum=0.0, maximum=1.0, default=0.92, group="bucket"),
        "balance_beta": ParameterProperty(type="number", minimum=0.1, maximum=8.0, default=1.8, group="balance"),
        "inventory_gamma": ParameterProperty(type="number", minimum=0.0, maximum=3.0, default=0.8, group="balance"),
        "ema20_weight": ParameterProperty(type="number", minimum=0.0, maximum=1.0, default=0.50, group="benchmark"),
        "ema60_weight": ParameterProperty(type="number", minimum=0.0, maximum=1.0, default=0.30, group="benchmark"),
        "ema120_weight": ParameterProperty(type="number", minimum=0.0, maximum=1.0, default=0.20, group="benchmark"),
        "volume_poc_weight": ParameterProperty(type="number", minimum=0.0, maximum=0.6, default=0.25, group="benchmark"),
        "atr_period": ParameterProperty(type="integer", minimum=5, maximum=60, default=14, group="benchmark"),
        "grid_atr_multiplier": ParameterProperty(type="number", minimum=0.2, maximum=5.0, default=0.6, group="grid"),
        "min_grid_step_pct": ParameterProperty(type="number", minimum=0.002, maximum=0.05, default=0.008, group="grid"),
        "max_grid_step_pct": ParameterProperty(type="number", minimum=0.005, maximum=0.2, default=0.03, group="grid"),
        "rebalance_threshold_pct": ParameterProperty(
          type="number",
          minimum=0.002,
          maximum=0.10,
          default=0.02,
          title="再平衡触发偏差",
          description="目标仓位与当前仓位的偏差达到该比例后才产生调节意图。",
          group="balance",
          unit="比例",
          step=0.001,
          mobileEditable=True,
          mobileApplyImmediately=False,
          mobileRiskLevel="MEDIUM",
        ),
        "daily_core_add_limit_pct": ParameterProperty(type="number", minimum=0.0, maximum=0.20, default=0.05, group="risk"),
        "single_order_limit_pct": ParameterProperty(type="number", minimum=0.005, maximum=0.20, default=0.04, group="risk"),
        "min_order_amount": ParameterProperty(type="number", minimum=0.0, maximum=100000.0, default=3000.0, group="risk"),
        "min_expected_profit_bps": ParameterProperty(
          type="number",
          minimum=0.0,
          maximum=200.0,
          default=20.0,
          title="最低预期收益",
          description="低于该预期收益的调节意图不会继续执行。",
          group="risk",
          unit="基点",
          step=1.0,
          mobileEditable=True,
          mobileApplyImmediately=False,
          mobileRiskLevel="LOW",
        ),
        "high_distribution_volume_ratio": ParameterProperty(type="number", minimum=1.0, maximum=5.0, default=1.6, group="state"),
        "downtrend_grid_buy_block": ParameterProperty(
          type="boolean",
          default=True,
          title="下跌趋势阻止网格买入",
          description="开启后，在服务端识别为下跌趋势时不新增网格买入风险。",
          group="state",
          mobileEditable=True,
          mobileApplyImmediately=False,
          mobileRiskLevel="MEDIUM",
        ),
        "consecutive_down_days_limit": ParameterProperty(type="integer", minimum=1, maximum=10, default=3, group="state"),
        "high_reversal_reduce_threshold": ParameterProperty(type="number", minimum=0.0, maximum=1.0, default=0.65, group="state"),
      },
      required=["max_position_pct", "neutral_position_pct", "core_base_share"],
    )

  @classmethod
  def get_state_schema(cls) -> StateSchema:
    return StateSchema(
      type="object",
      properties={
        "benchmark_price": StateProperty(type="number", default=0.0),
        "grid_step_pct": StateProperty(type="number", default=0.01),
        "trend_state": StateProperty(type="string", default=BalanceTrendState.NEUTRAL),
        "position_phase": StateProperty(type="string", default=BalancePositionPhase.BALANCED_RUN),
        "target_total_pct": StateProperty(type="number", default=0.0),
        "target_core_pct": StateProperty(type="number", default=0.0),
        "target_swing_pct": StateProperty(type="number", default=0.0),
        "locked_core_pct": StateProperty(type="number", default=0.0),
        "low_score": StateProperty(type="number", default=0.0),
        "high_reversal_score": StateProperty(type="number", default=0.0),
        "consecutive_down_days": StateProperty(type="integer", default=0),
        "last_grid_index": StateProperty(type="integer", default=0),
        "last_filled_grid_index": StateProperty(type="integer", default=0),
        "pending_intents": StateProperty(type="object", default={}),
      },
    )

  @classmethod
  def get_data_requirements(cls) -> Dict[str, Any]:
    return {"use_tick_data": True, "periods": ["1m", "1d"]}

  async def on_init(self) -> None:
    self._bars: List[Dict[str, float]] = []
    self._last_daily_confirm: Dict[str, Any] = {}

  async def on_stop(self) -> None:
    return None

  async def step(self, input: StrategyInput) -> StrategyOutput:
    if not self._is_bound_instrument(input.instrument_code):
      return StrategyOutput(
        decision_tags=["instrument_mismatch", "no_trade"],
        trace_payload={
          "reason": "instrument_mismatch",
          "bound_instruments": list(self.context.instruments or []),
          "input_instrument": input.instrument_code,
        },
      )
    if not self._is_supported_instrument(input.instrument_code):
      return StrategyOutput(
        decision_tags=["unsupported_instrument", "no_trade"],
        trace_payload={"reason": "unsupported_instrument"},
      )

    if input.cadence == StrategyCadence.BAR:
      return self._handle_bar(input)
    if input.cadence == StrategyCadence.TICK:
      return self._handle_intraday(input)
    return StrategyOutput()

  async def warmup(self, input: StrategyInput) -> None:
    if input.cadence != StrategyCadence.BAR:
      return None
    if not self._is_bound_instrument(input.instrument_code):
      return None
    if not self._is_supported_instrument(input.instrument_code):
      return None

    row = self._bar_row(input.event, input.market_data)
    if not row:
      return None
    self._bars.append(row)
    max_len = max(160, int(self.get_parameter("atr_period", 14)) * 4)
    self._bars = self._bars[-max_len:]

    analysis = self._analyze_daily(row)
    if not analysis:
      return None
    targets = self._build_targets(input, analysis)
    phase = self._phase_from_state(analysis["trend_state"], targets)
    patch = self._state_patch(analysis, targets, phase)
    self.state.update(patch.set)
    self._last_daily_confirm = {
      **analysis,
      "targets": targets,
      "position_phase": phase,
    }
    return None

  async def on_order(self, event: OrderStateEvent) -> Optional[RuntimeStatePatch]:
    pending = dict(self.state.get("pending_intents", {}) or {})
    intent_id = str(event.metadata.get("intent_id", "") or "")
    if not intent_id:
      return None
    status = str(event.status or "").upper()
    if status in {"REJECTED", "CANCELLED", "EXPIRED", "FILLED"}:
      pending.pop(intent_id, None)
    else:
      pending[intent_id] = {
        "order_id": event.order_id,
        "status": status,
        "bucket": event.metadata.get("bucket"),
        "grid_index": event.metadata.get("grid_index"),
      }
    self.state.pending_intents = pending
    return RuntimeStatePatch(set={"pending_intents": pending})

  async def on_trade(self, event: TradeExecutionEvent) -> Optional[RuntimeStatePatch]:
    pending = dict(self.state.get("pending_intents", {}) or {})
    intent_id = str(event.metadata.get("intent_id", "") or "")
    if intent_id:
      pending.pop(intent_id, None)
    updates = {"pending_intents": pending}
    grid_index = event.metadata.get("grid_index")
    if grid_index is not None:
      try:
        updates["last_filled_grid_index"] = int(grid_index)
      except (TypeError, ValueError):
        pass
    self.state.update(updates)
    return RuntimeStatePatch(set=updates)

  def _handle_bar(self, input: StrategyInput) -> StrategyOutput:
    bar = input.event
    row = self._bar_row(bar, input.market_data)
    if not row:
      return StrategyOutput(decision_tags=["invalid_bar"])
    self._bars.append(row)
    max_len = max(160, int(self.get_parameter("atr_period", 14)) * 4)
    self._bars = self._bars[-max_len:]

    analysis = self._analyze_daily(row)
    if not analysis:
      return StrategyOutput(decision_tags=["warming_up"])

    targets = self._build_targets(input, analysis)
    phase = self._phase_from_state(analysis["trend_state"], targets)
    patch = self._state_patch(analysis, targets, phase)
    self.state.update(patch.set)

    intents = self._core_rebalance_intents(input, row["close"], targets, phase)
    self._last_daily_confirm = {
      **analysis,
      "targets": targets,
      "position_phase": phase,
    }
    return StrategyOutput(
      trade_intents=intents,
      runtime_state_patch=patch,
      decision_tags=[analysis["trend_state"], phase],
      trace_payload={
        "balance_signal": targets.signal,
        "benchmark_price": analysis["benchmark_price"],
        "grid_step_pct": analysis["grid_step_pct"],
        "low_score": analysis.get("low_score", 0.0),
        "high_reversal_score": analysis.get("high_reversal_score", 0.0),
        "target_total_pct": targets.target_total_pct,
        "target_core_pct": targets.target_core_pct,
        "target_swing_pct": targets.target_swing_pct,
        "reason": "daily_confirmation",
      },
    )

  def _handle_intraday(self, input: StrategyInput) -> StrategyOutput:
    tick = input.event
    price = self._tick_price(tick, input.market_data)
    if price <= 0:
      return StrategyOutput(decision_tags=["invalid_tick"])
    analysis = dict(self._last_daily_confirm or {})
    if not analysis:
      analysis = {
        "benchmark_price": float(self.state.get("benchmark_price", 0.0) or price),
        "grid_step_pct": float(self.state.get("grid_step_pct", 0.01) or 0.01),
        "trend_state": str(self.state.get("trend_state", BalanceTrendState.NEUTRAL)),
      }
      targets = BalanceTargets(
        signal=0.0,
        target_total_pct=float(self.state.get("target_total_pct", 0.0) or 0.0),
        target_core_pct=float(self.state.get("target_core_pct", 0.0) or 0.0),
        target_swing_pct=float(self.state.get("target_swing_pct", 0.0) or 0.0),
        locked_core_pct=float(self.state.get("locked_core_pct", 0.0) or 0.0),
        core_share=float(self.get_parameter("core_base_share", 0.75)),
      )
      phase = str(self.state.get("position_phase", BalancePositionPhase.BALANCED_RUN))
    else:
      targets = analysis["targets"]
      phase = str(analysis["position_phase"])

    intents = self._grid_intents(input, price, analysis, targets, phase)
    return StrategyOutput(
      trade_intents=intents,
      decision_tags=[analysis["trend_state"], phase],
      trace_payload={
        "benchmark_price": analysis["benchmark_price"],
        "grid_step_pct": analysis["grid_step_pct"],
      },
    )

  def _analyze_daily(self, row: Dict[str, float]) -> Optional[Dict[str, Any]]:
    closes = [bar["close"] for bar in self._bars]
    highs = [bar["high"] for bar in self._bars]
    lows = [bar["low"] for bar in self._bars]
    volumes = [bar["volume"] for bar in self._bars]
    if len(closes) < 20:
      return None

    ema20 = _ema(closes, 20)
    ema60 = _ema(closes, 60)
    ema120 = _ema(closes, 120)
    weights = self._normalized_weights()
    weighted_ema = ema20 * weights[0] + ema60 * weights[1] + ema120 * weights[2]
    poc = _volume_weighted_price(self._bars[-60:])
    support = min(lows[-20:])
    pressure = max(highs[-20:])
    poc_weight = _clamp(float(self.get_parameter("volume_poc_weight", 0.25)), 0.0, 0.6)
    benchmark = weighted_ema * (1.0 - poc_weight) + poc * poc_weight
    if row["close"] < benchmark:
      benchmark = benchmark * 0.90 + support * 0.10
    else:
      benchmark = benchmark * 0.90 + pressure * 0.10

    atr = _atr(highs, lows, closes, int(self.get_parameter("atr_period", 14)))
    raw_grid_step_pct = (
      (atr / benchmark) * float(self.get_parameter("grid_atr_multiplier", 0.6))
      if benchmark > 0
      else float(self.get_parameter("min_grid_step_pct", 0.008))
    )
    grid_step_pct = _clamp(
      raw_grid_step_pct,
      float(self.get_parameter("min_grid_step_pct", 0.008)),
      float(self.get_parameter("max_grid_step_pct", 0.03)),
    )
    volume_ratio = _volume_ratio(volumes)
    price_distance = (row["close"] - benchmark) / max(benchmark, 1e-8)
    consecutive_down_days = _consecutive_down_days(closes)
    low_score = self._low_accumulation_score(
      price_distance=price_distance,
      grid_step_pct=grid_step_pct,
      ema20=ema20,
      ema60=ema60,
      volume_ratio=volume_ratio,
    )
    high_reversal_score = self._high_reversal_score(
      price_distance=price_distance,
      grid_step_pct=grid_step_pct,
      ema20=ema20,
      ema60=ema60,
      ema120=ema120,
      volume_ratio=volume_ratio,
      consecutive_down_days=consecutive_down_days,
    )
    trend_state = self._trend_state(
      close=row["close"],
      benchmark=benchmark,
      ema20=ema20,
      ema60=ema60,
      ema120=ema120,
      grid_step_pct=grid_step_pct,
      volume_ratio=volume_ratio,
      price_distance=price_distance,
      low_score=low_score,
      high_reversal_score=high_reversal_score,
      consecutive_down_days=consecutive_down_days,
    )
    return {
      "benchmark_price": benchmark,
      "close": row["close"],
      "grid_step_pct": grid_step_pct,
      "atr": atr,
      "ema20": ema20,
      "ema60": ema60,
      "ema120": ema120,
      "poc": poc,
      "support": support,
      "pressure": pressure,
      "volume_ratio": volume_ratio,
      "price_distance": price_distance,
      "consecutive_down_days": consecutive_down_days,
      "low_score": low_score,
      "high_reversal_score": high_reversal_score,
      "trend_state": trend_state,
    }

  def _trend_state(
    self,
    *,
    close: float,
    benchmark: float,
    ema20: float,
    ema60: float,
    ema120: float,
    grid_step_pct: float,
    volume_ratio: float,
    price_distance: float,
    low_score: float,
    high_reversal_score: float,
    consecutive_down_days: int,
  ) -> str:
    down_limit = int(self.get_parameter("consecutive_down_days_limit", 3))
    if consecutive_down_days >= down_limit and close < ema20:
      return BalanceTrendState.DOWNTREND
    if ema20 < ema60 < ema120 and close < ema60:
      return BalanceTrendState.DOWNTREND
    if high_reversal_score >= float(self.get_parameter("high_reversal_reduce_threshold", 0.65)):
      return BalanceTrendState.HIGH_DISTRIBUTION
    if low_score >= 0.55:
      return BalanceTrendState.LOW_ACCUMULATION
    if ema20 > ema60 > ema120 and close >= benchmark:
      return BalanceTrendState.UPTREND
    return BalanceTrendState.NEUTRAL

  def _build_targets(self, input: StrategyInput, analysis: Dict[str, Any]) -> BalanceTargets:
    profile = dict(input.position_profile or {})
    risk_caps = dict(input.risk_caps or {})
    account = dict((input.portfolio_state or {}).get("account") or {})
    total_asset = _float(account.get("total_asset") or account.get("cash_total") or 0.0)
    current = self._current_bucket_pcts(
      input,
      price=float(analysis.get("close") or 0.0),
      total_asset=total_asset,
    ) if total_asset > 0 else {}

    neutral = float(self.get_parameter("neutral_position_pct", 0.35))
    min_pct = max(float(self.get_parameter("min_position_pct", 0.05)), _float(profile.get("min_position_pct")))
    max_pct = min(
      float(self.get_parameter("max_position_pct", 0.70)),
      _float(profile.get("max_position_pct"), 1.0),
    )
    risk_max = risk_caps.get("max_position_pct")
    if risk_max is not None:
      max_pct = min(max_pct, _float(risk_max, max_pct))
    cash_buffer = max(
      float(self.get_parameter("cash_buffer_pct", 0.25)),
      _float(profile.get("target_cash_buffer_pct")),
      _float(risk_caps.get("min_cash_buffer_pct")),
    )
    if total_asset > 0:
      max_pct = min(max_pct, max(0.0, 1.0 - cash_buffer))

    grid_step = max(float(analysis["grid_step_pct"]), 1e-6)
    distance_units = -float(analysis["price_distance"]) / grid_step
    beta = float(self.get_parameter("balance_beta", 1.8)) * _float(
      profile.get("balance_beta_multiplier"), 1.0
    )
    signal = 2.0 / (1.0 + math.exp(-beta * distance_units)) - 1.0
    trend_adjust = {
      BalanceTrendState.LOW_ACCUMULATION: 0.14,
      BalanceTrendState.UPTREND: 0.08,
      BalanceTrendState.NEUTRAL: 0.0,
      BalanceTrendState.HIGH_DISTRIBUTION: -0.18,
      BalanceTrendState.DOWNTREND: -0.28,
    }.get(analysis["trend_state"], 0.0)
    target_total = neutral + signal * 0.22 + trend_adjust
    if risk_caps.get("only_reduce_position"):
      target_total = min(target_total, _current_position_pct(input, input.instrument_code))
    target_total = _clamp(target_total, min_pct, max_pct)

    gamma = float(self.get_parameter("inventory_gamma", 0.8)) * _float(
      profile.get("inventory_gamma_multiplier"), 1.0
    )
    core_share = float(self.get_parameter("core_base_share", 0.75)) + gamma * max(0.0, signal) * 0.10
    core_share = _clamp(
      core_share,
      max(
        float(self.get_parameter("core_min_share", 0.60)),
        _float(profile.get("core_share_min"), 0.5),
      ),
      min(
        float(self.get_parameter("core_max_share", 0.92)),
        _float(profile.get("core_share_max"), 1.0),
      ),
    )
    swing_max = _float(profile.get("swing_max_pct"), 0.15)
    locked_core_pct = current.get("locked_core_pct", 0.0)
    active_target_total = max(0.0, target_total - locked_core_pct)
    target_core = active_target_total * core_share
    target_swing = min(max(0.0, active_target_total - target_core), swing_max)
    target_core = max(0.0, active_target_total - target_swing)
    return BalanceTargets(
      signal=signal,
      target_total_pct=target_total,
      target_core_pct=target_core,
      target_swing_pct=target_swing,
      locked_core_pct=locked_core_pct,
      core_share=core_share,
    )

  def _low_accumulation_score(
    self,
    *,
    price_distance: float,
    grid_step_pct: float,
    ema20: float,
    ema60: float,
    volume_ratio: float,
  ) -> float:
    distance_score = _clamp((-price_distance / max(grid_step_pct * 3.0, 1e-6)), 0.0, 1.0)
    trend_score = 1.0 if ema20 >= ema60 * 0.98 else 0.4 if ema20 >= ema60 * 0.94 else 0.0
    volume_score = _clamp((volume_ratio - 0.7) / 0.8, 0.0, 1.0)
    return _clamp(distance_score * 0.55 + trend_score * 0.25 + volume_score * 0.20, 0.0, 1.0)

  def _high_reversal_score(
    self,
    *,
    price_distance: float,
    grid_step_pct: float,
    ema20: float,
    ema60: float,
    ema120: float,
    volume_ratio: float,
    consecutive_down_days: int,
  ) -> float:
    distance_score = _clamp(price_distance / max(grid_step_pct * 3.0, 1e-6), 0.0, 1.0)
    volume_trigger = float(self.get_parameter("high_distribution_volume_ratio", 1.6))
    volume_score = _clamp((volume_ratio - 1.0) / max(volume_trigger - 1.0, 0.1), 0.0, 1.0)
    trend_rollover = 1.0 if ema20 <= ema60 else 0.5 if ema20 <= ema60 * 1.03 else 0.0
    long_trend_fatigue = 0.35 if ema60 <= ema120 and ema20 <= ema60 * 1.03 else 0.0
    down_score = _clamp(consecutive_down_days / max(1, int(self.get_parameter("consecutive_down_days_limit", 3))), 0.0, 1.0)
    return _clamp(
      distance_score * 0.35
      + volume_score * 0.25
      + trend_rollover * 0.25
      + long_trend_fatigue
      + down_score * 0.15,
      0.0,
      1.0,
    )

  def _phase_from_state(self, trend_state: str, targets: BalanceTargets) -> str:
    if trend_state == BalanceTrendState.DOWNTREND:
      return BalancePositionPhase.DEFENSIVE
    if trend_state == BalanceTrendState.HIGH_DISTRIBUTION:
      return BalancePositionPhase.DISTRIBUTION
    if trend_state == BalanceTrendState.LOW_ACCUMULATION and targets.core_share >= 0.75:
      return BalancePositionPhase.BUILDING_CORE
    return BalancePositionPhase.BALANCED_RUN

  def _core_rebalance_intents(
    self,
    input: StrategyInput,
    price: float,
    targets: BalanceTargets,
    phase: str,
  ) -> List[TradeIntent]:
    account, total_asset = _account(input)
    if total_asset <= 0 or price <= 0:
      return []
    profile = dict(input.position_profile or {})
    current = self._current_bucket_pcts(input, price, total_asset)
    threshold = float(self.get_parameter("rebalance_threshold_pct", 0.02))
    intents: List[TradeIntent] = []

    if phase in {BalancePositionPhase.DEFENSIVE, BalancePositionPhase.DISTRIBUTION}:
      excess = current["total_pct"] - targets.target_total_pct
      if excess > threshold and _allow_sell(profile, SWING_BUCKET):
        volume = _volume_for_amount(min(excess * total_asset, current["swing_amount"]), price)
        if volume > 0:
          intents.append(
            self._intent(
              input,
              direction=TradeIntentDirection.SELL,
              bucket=SWING_BUCKET,
              reason=f"{phase.lower()}_sell_swing",
              price=price,
              target_volume=volume,
              priority=TradeIntentPriority.RISK_REDUCTION,
              metadata={"target_total_pct": targets.target_total_pct, "phase": phase},
            )
          )
          return intents
      if excess > threshold and _allow_sell(profile, CORE_BUCKET):
        volume = _volume_for_amount(min(excess * total_asset, current["core_amount"]), price)
        if volume > 0:
          intents.append(
            self._intent(
              input,
              direction=TradeIntentDirection.SELL,
              bucket=CORE_BUCKET,
              reason=f"{phase.lower()}_reduce_core",
              price=price,
              target_volume=volume,
              priority=TradeIntentPriority.RISK_REDUCTION,
              metadata={"target_total_pct": targets.target_total_pct, "phase": phase},
            )
          )
      return intents

    core_gap = targets.target_core_pct - current["core_pct"]
    if core_gap > threshold and _allow_buy(profile, CORE_BUCKET):
      amount = min(
        core_gap * total_asset,
        float(self.get_parameter("daily_core_add_limit_pct", 0.05)) * total_asset,
        float(self.get_parameter("single_order_limit_pct", 0.04)) * total_asset,
        _float(account.get("available_cash"), total_asset),
      )
      if amount >= float(self.get_parameter("min_order_amount", 3000.0)):
        intents.append(
          self._intent(
            input,
            direction=TradeIntentDirection.BUY,
            bucket=CORE_BUCKET,
            reason="dynamic_balance_build_core",
            price=price,
            target_amount=amount,
            priority=TradeIntentPriority.NORMAL,
            metadata={
              "target_core_pct": targets.target_core_pct,
              "balance_signal": targets.signal,
              "phase": phase,
            },
          )
        )
    return intents

  def _grid_intents(
    self,
    input: StrategyInput,
    price: float,
    analysis: Dict[str, Any],
    targets: BalanceTargets,
    phase: str,
  ) -> List[TradeIntent]:
    if phase == BalancePositionPhase.DEFENSIVE:
      return []
    profile = dict(input.position_profile or {})
    trend_state = str(analysis["trend_state"])
    if (
      trend_state == BalanceTrendState.DOWNTREND
      and bool(self.get_parameter("downtrend_grid_buy_block", True))
    ):
      return []

    benchmark = float(analysis["benchmark_price"])
    grid_step_pct = max(float(analysis["grid_step_pct"]), 1e-6)
    expected_profit_bps = grid_step_pct * 10000.0
    if expected_profit_bps < float(self.get_parameter("min_expected_profit_bps", 20.0)):
      return []
    grid_index = math.floor((price - benchmark) / (benchmark * grid_step_pct))
    last_filled_grid_index = int(self.state.get("last_filled_grid_index", 0) or 0)
    self.state.last_grid_index = grid_index

    account, total_asset = _account(input)
    current = self._current_bucket_pcts(input, price, total_asset)
    if total_asset <= 0:
      return []

    pending = dict(self.state.get("pending_intents", {}) or {})
    if any(str(item.get("grid_index")) == str(grid_index) for item in pending.values()):
      return []

    if grid_index < min(last_filled_grid_index, 0) and _allow_buy(profile, SWING_BUCKET):
      swing_gap = targets.target_swing_pct - current["swing_pct"]
      amount = min(
        max(0.0, swing_gap * total_asset),
        float(self.get_parameter("single_order_limit_pct", 0.04)) * total_asset,
        _float(account.get("available_cash"), total_asset),
      )
      if amount >= float(self.get_parameter("min_order_amount", 3000.0)):
        return [
          self._intent(
            input,
            direction=TradeIntentDirection.BUY,
            bucket=SWING_BUCKET,
            reason="dynamic_balance_swing_grid_buy",
            price=price,
            target_amount=amount,
            priority=TradeIntentPriority.NORMAL,
            metadata={
              "grid_index": grid_index,
              "last_filled_grid_index": last_filled_grid_index,
              "benchmark_price": benchmark,
              "grid_step_pct": grid_step_pct,
              "expected_profit_bps": expected_profit_bps,
              "target_swing_pct": targets.target_swing_pct,
            },
          )
        ]

    if grid_index > max(last_filled_grid_index, 0) and _allow_sell(profile, SWING_BUCKET):
      sell_amount = min(
        current["swing_amount"],
        max(0.0, current["swing_pct"] - targets.target_swing_pct) * total_asset
        or current["swing_amount"] * 0.25,
      )
      volume = _volume_for_amount(sell_amount, price)
      if volume > 0:
        return [
          self._intent(
            input,
            direction=TradeIntentDirection.SELL,
            bucket=SWING_BUCKET,
            reason="dynamic_balance_swing_grid_sell",
            price=price,
            target_volume=volume,
            priority=TradeIntentPriority.HIGH,
            metadata={
              "grid_index": grid_index,
              "last_filled_grid_index": last_filled_grid_index,
              "benchmark_price": benchmark,
              "grid_step_pct": grid_step_pct,
              "expected_profit_bps": expected_profit_bps,
              "target_swing_pct": targets.target_swing_pct,
            },
          )
        ]
    return []

  def _intent(
    self,
    input: StrategyInput,
    *,
    direction: TradeIntentDirection,
    bucket: str,
    reason: str,
    price: float,
    priority: TradeIntentPriority,
    target_amount: Optional[float] = None,
    target_volume: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
  ) -> TradeIntent:
    return TradeIntent(
      strategy_id=input.strategy_id,
      run_id=input.run_id,
      instrument_code=input.instrument_code,
      direction=direction,
      bucket=bucket,
      reason=reason,
      priority=priority,
      target_amount=target_amount,
      target_volume=target_volume,
      limit_price_hint=price,
      trace_id=input.trace_id,
      metadata={
        **dict(metadata or {}),
        "strategy_name": self.name,
        "strategy_version": self.version,
      },
    )

  def _current_bucket_pcts(
    self, input: StrategyInput, price: float, total_asset: float
  ) -> Dict[str, float]:
    ledger = dict(input.bucket_ledger or {})
    instrument = dict((ledger.get("instruments") or {}).get(input.instrument_code, {}) or {})
    locked_core = dict(instrument.get(LOCKED_CORE_BUCKET, {}) or {})
    core = dict(instrument.get(CORE_BUCKET, {}) or {})
    swing = dict(instrument.get(SWING_BUCKET, {}) or {})
    position = dict((input.portfolio_state or {}).get("positions", {}).get(input.instrument_code, {}) or {})
    locked_core_volume = int(locked_core.get("total_volume", 0) or 0)
    core_volume = int(core.get("total_volume", 0) or 0)
    swing_volume = int(swing.get("total_volume", 0) or 0)
    if locked_core_volume + core_volume + swing_volume <= 0:
      core_volume = int(position.get("long_volume", 0) or 0)
    locked_core_amount = locked_core_volume * price
    core_amount = core_volume * price
    swing_amount = swing_volume * price
    total_amount = locked_core_amount + core_amount + swing_amount
    return {
      "locked_core_pct": locked_core_amount / total_asset if total_asset > 0 else 0.0,
      "core_pct": core_amount / total_asset if total_asset > 0 else 0.0,
      "swing_pct": swing_amount / total_asset if total_asset > 0 else 0.0,
      "total_pct": total_amount / total_asset if total_asset > 0 else 0.0,
      "locked_core_amount": locked_core_amount,
      "core_amount": core_amount,
      "swing_amount": swing_amount,
      "total_amount": total_amount,
    }

  def _state_patch(
    self, analysis: Dict[str, Any], targets: BalanceTargets, phase: str
  ) -> RuntimeStatePatch:
    return RuntimeStatePatch(
      set={
        "benchmark_price": analysis["benchmark_price"],
        "grid_step_pct": analysis["grid_step_pct"],
        "trend_state": analysis["trend_state"],
        "position_phase": phase,
        "target_total_pct": targets.target_total_pct,
        "target_core_pct": targets.target_core_pct,
        "target_swing_pct": targets.target_swing_pct,
        "locked_core_pct": targets.locked_core_pct,
        "low_score": analysis.get("low_score", 0.0),
        "high_reversal_score": analysis.get("high_reversal_score", 0.0),
        "consecutive_down_days": analysis.get("consecutive_down_days", 0),
      }
    )

  def _normalized_weights(self) -> Tuple[float, float, float]:
    weights = (
      float(self.get_parameter("ema20_weight", 0.50)),
      float(self.get_parameter("ema60_weight", 0.30)),
      float(self.get_parameter("ema120_weight", 0.20)),
    )
    total = sum(max(0.0, item) for item in weights) or 1.0
    return tuple(max(0.0, item) / total for item in weights)

  @staticmethod
  def _bar_row(bar: Any, market_data: Any) -> Optional[Dict[str, float]]:
    source = bar or market_data
    if source is None:
      return None
    close = _float(getattr(source, "close", getattr(source, "price", 0.0)))
    high = _float(getattr(source, "high", close), close)
    low = _float(getattr(source, "low", close), close)
    volume = _float(getattr(source, "volume", 0.0))
    if close <= 0:
      return None
    return {"close": close, "high": high, "low": low, "volume": volume}

  @staticmethod
  def _tick_price(tick: Optional[Tick], market_data: Any) -> float:
    if tick is not None:
      return _float(getattr(tick, "last_price", 0.0))
    return _float(getattr(market_data, "price", getattr(market_data, "close", 0.0)))

  @staticmethod
  def _is_supported_instrument(code: str) -> bool:
    code = str(code or "").upper()
    return code.endswith(".SH") or code.endswith(".SZ")

  def _is_bound_instrument(self, code: str) -> bool:
    instruments = [str(item or "").upper() for item in (self.context.instruments or []) if item]
    return len(instruments) == 1 and str(code or "").upper() == instruments[0]


def _ema(values: List[float], period: int) -> float:
  if not values:
    return 0.0
  period = max(1, int(period or 1))
  alpha = 2.0 / (period + 1.0)
  current = float(values[0])
  for value in values[1:]:
    current = alpha * float(value) + (1.0 - alpha) * current
  return current


def _atr(highs: List[float], lows: List[float], closes: List[float], period: int) -> float:
  if len(closes) < 2:
    return 0.0
  ranges: List[float] = []
  start = max(1, len(closes) - max(1, period))
  for idx in range(start, len(closes)):
    prev_close = closes[idx - 1]
    ranges.append(
      max(
        highs[idx] - lows[idx],
        abs(highs[idx] - prev_close),
        abs(lows[idx] - prev_close),
      )
    )
  return sum(ranges) / len(ranges) if ranges else 0.0


def _volume_weighted_price(rows: List[Dict[str, float]]) -> float:
  amount = sum(row["close"] * max(0.0, row.get("volume", 0.0)) for row in rows)
  volume = sum(max(0.0, row.get("volume", 0.0)) for row in rows)
  if volume <= 0 and rows:
    return sum(row["close"] for row in rows) / len(rows)
  return amount / volume if volume > 0 else 0.0


def _volume_ratio(volumes: List[float]) -> float:
  if len(volumes) < 6:
    return 1.0
  recent = max(0.0, volumes[-1])
  base = sum(max(0.0, value) for value in volumes[-21:-1]) / max(1, len(volumes[-21:-1]))
  return recent / base if base > 0 else 1.0


def _consecutive_down_days(closes: List[float]) -> int:
  if len(closes) < 2:
    return 0
  count = 0
  for idx in range(len(closes) - 1, 0, -1):
    if closes[idx] < closes[idx - 1]:
      count += 1
      continue
    break
  return count


def _account(input: StrategyInput) -> Tuple[Dict[str, Any], float]:
  account = dict((input.portfolio_state or {}).get("account") or {})
  total_asset = _float(account.get("total_asset") or account.get("cash_total") or account.get("available_cash") or 0.0)
  return account, total_asset


def _current_position_pct(input: StrategyInput, instrument_code: str) -> float:
  account, total_asset = _account(input)
  if total_asset <= 0:
    return 0.0
  position = dict((input.portfolio_state or {}).get("positions", {}).get(instrument_code, {}) or {})
  return _float(position.get("market_value")) / total_asset


def _allow_buy(profile: Dict[str, Any], bucket: str) -> bool:
  return bool(dict(profile.get("allow_bucket_buy", {}) or {}).get(bucket, profile.get(f"allow_{bucket}_buy", True)))


def _allow_sell(profile: Dict[str, Any], bucket: str) -> bool:
  return bool(dict(profile.get("allow_bucket_sell", {}) or {}).get(bucket, profile.get(f"allow_{bucket}_sell", True)))


def _volume_for_amount(amount: float, price: float) -> int:
  if amount <= 0 or price <= 0:
    return 0
  return max(0, int(amount // price))


def _float(value: Any, default: float = 0.0) -> float:
  try:
    return float(value)
  except (TypeError, ValueError):
    return default


def _clamp(value: float, lower: float, upper: float) -> float:
  if upper < lower:
    upper = lower
  return max(lower, min(upper, value))
