"""A-share multi-instrument positive-T assistant.

The strategy owns signal and batch state only. The execution/orchestration layer
owns the account holdings universe, legal sizing, T+1 checks and broker truth.
"""

from __future__ import annotations

import uuid
from datetime import time
from typing import Any, Dict, List, Optional

from core.state_schema import StateProperty, StateSchema
from core.strategies.base import (
  OrderStateEvent,
  RuntimeStatePatch,
  StrategyBase,
  StrategyCadence,
  StrategyInput,
  StrategyOutput,
  TradeExecutionEvent,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentExecutionMode,
  TradeIntentPriority,
)
from core.trading.bucket_ledger import SWING_BUCKET
from core.trading.t_trade import (
  SignalPolicy,
  TickSample,
  TradingCostPolicy,
  TrailingProfitPolicy,
  calculate_target_trade_volume,
  calculate_trailing_floor_pct,
  estimate_net_profit_pct,
  evaluate_intraday_t_signal,
)
from models.enums import (
  StrategyCategory,
  StrategyInstrumentScope,
  StrategyInstrumentUniverseMode,
)
from models.parameter_schema import ParameterProperty, ParameterSchema


class TTradeStatus:
  OBSERVING = "OBSERVING"
  AWAITING_APPROVAL = "AWAITING_APPROVAL"
  ENTRY_SUBMITTED = "ENTRY_SUBMITTED"
  ENTRY_PARTIAL = "ENTRY_PARTIAL"
  MONITORING = "MONITORING"
  PROFIT_ARMED = "PROFIT_ARMED"
  EXIT_TRIGGERED = "EXIT_TRIGGERED"
  EXIT_SUBMITTED = "EXIT_SUBMITTED"
  EXIT_PARTIAL = "EXIT_PARTIAL"
  COOLDOWN = "COOLDOWN"
  DRAINING = "DRAINING"
  ERROR = "ERROR"


class TTradeTimeExitMode:
  UNLIMITED = "UNLIMITED"
  END_OF_DAY = "END_OF_DAY"
  MAX_HOLDING_DAYS = "MAX_HOLDING_DAYS"


class AshareIntradayTAssistantStrategy(StrategyBase):
  """Monitor an account holdings universe in one strategy instance."""

  CATEGORY = StrategyCategory.MEAN_REVERSION
  RISK_LEVEL = "medium"
  TAGS = ["A股", "做T", "Tick", "人工确认", "动态止盈", "T+1", "动态持仓"]
  INSTRUMENT_SCOPE = StrategyInstrumentScope.MULTI
  INSTRUMENT_UNIVERSE_MODE = StrategyInstrumentUniverseMode.ACCOUNT_HOLDINGS

  @property
  def name(self) -> str:
    return "A股动态持仓做T策略"

  @property
  def version(self) -> str:
    return "2.1.0"

  @property
  def description(self) -> str:
    return "在一个账户级策略中动态监测全部持仓，人工确认买入，并按批次净收益自动退出。"

  @classmethod
  def get_parameter_schema(cls) -> ParameterSchema:
    return ParameterSchema(
      type="object",
      additionalProperties=True,
      properties={
        "account_id": ParameterProperty(type="string", default="", group="binding"),
        "target_trade_amount": ParameterProperty(
          type="number",
          minimum=100.0,
          maximum=1_000_000.0,
          default=10_000.0,
          group="sizing",
        ),
        "max_trade_amount": ParameterProperty(
          type="number",
          minimum=100.0,
          maximum=1_000_000.0,
          default=12_000.0,
          group="sizing",
        ),
        "max_concurrent_batches": ParameterProperty(type="integer", minimum=1, maximum=20, default=3, group="sizing"),
        "max_total_t_exposure_pct": ParameterProperty(type="number", minimum=0.01, maximum=1.0, default=0.1, group="sizing"),
        "signal_lookback_seconds": ParameterProperty(type="integer", minimum=60, maximum=900, default=300, group="signal"),
        "stabilization_seconds": ParameterProperty(type="integer", minimum=3, maximum=120, default=15, group="signal"),
        "pullback_threshold_pct": ParameterProperty(type="number", minimum=0.1, maximum=5.0, default=0.8, group="signal"),
        "rebound_threshold_pct": ParameterProperty(type="number", minimum=0.05, maximum=2.0, default=0.2, group="signal"),
        "max_spread_ticks": ParameterProperty(type="integer", minimum=1, maximum=10, default=3, group="signal"),
        "approval_ttl_seconds": ParameterProperty(type="integer", minimum=5, maximum=300, default=30, group="approval"),
        "max_price_deviation_pct": ParameterProperty(type="number", minimum=0.05, maximum=2.0, default=0.3, group="approval"),
        "target_profit_pct": ParameterProperty(type="number", minimum=0.1, maximum=20.0, default=2.0, group="exit"),
        "base_floor_pct": ParameterProperty(type="number", minimum=-2.0, maximum=10.0, default=0.5, group="exit"),
        "initial_gap_pct": ParameterProperty(type="number", minimum=0.1, maximum=10.0, default=1.5, group="exit"),
        "trailing_gap_slope": ParameterProperty(type="number", minimum=0.0, maximum=2.0, default=0.25, group="exit"),
        "max_gap_pct": ParameterProperty(type="number", minimum=0.1, maximum=15.0, default=3.0, group="exit"),
        "hard_stop_enabled": ParameterProperty(type="boolean", default=False, group="risk"),
        "hard_stop_pct": ParameterProperty(type="number", minimum=-10.0, maximum=0.0, default=-0.8, group="risk"),
        "time_exit_mode": ParameterProperty(type="string", default=TTradeTimeExitMode.UNLIMITED, group="risk"),
        "time_exit_time": ParameterProperty(type="string", default="14:50", group="risk"),
        "max_holding_trading_days": ParameterProperty(type="integer", minimum=1, maximum=250, default=5, group="risk"),
        "cooldown_seconds": ParameterProperty(type="integer", minimum=0, maximum=3600, default=300, group="risk"),
        "commission_rate": ParameterProperty(type="number", minimum=0.0, maximum=0.01, default=0.0003, group="cost"),
        "minimum_commission": ParameterProperty(type="number", minimum=0.0, maximum=100.0, default=5.0, group="cost"),
        "stamp_tax_rate": ParameterProperty(type="number", minimum=0.0, maximum=0.01, default=0.0005, group="cost"),
        "transfer_fee_rate": ParameterProperty(type="number", minimum=0.0, maximum=0.01, default=0.00001, group="cost"),
      },
      required=["account_id"],
    )

  @classmethod
  def get_state_schema(cls) -> StateSchema:
    return StateSchema(
      type="object",
      properties={
        "instrument_states": StateProperty(type="object", default={}),
        "universe_revision": StateProperty(type="integer", default=0),
      },
    )

  @classmethod
  def get_data_requirements(cls) -> Dict[str, Any]:
    return {"use_tick_data": True, "periods": []}

  def apply_state_snapshot(self, state: Optional[Dict[str, Any]]) -> None:
    """Restore v2 state and wrap a legacy single-instrument snapshot if needed."""

    snapshot = dict(state or {})
    instrument_states = dict(snapshot.get("instrument_states") or {})
    if not instrument_states and snapshot.get("status"):
      code = str((self.context.instruments or [""])[0] or "")
      if code:
        legacy_keys = set(self._empty_instrument_state())
        legacy = self._empty_instrument_state()
        legacy.update({key: snapshot[key] for key in legacy_keys if key in snapshot})
        instrument_states[code] = legacy
    super().apply_state_snapshot(
      {
        "instrument_states": instrument_states,
        "universe_revision": int(snapshot.get("universe_revision", 0) or 0),
      }
    )

  def pending_manual_intent_ids(self) -> List[str]:
    pending = []
    for state in self._instrument_states().values():
      intent_id = str(state.get("pending_entry_intent_id", "") or "")
      if intent_id and str(state.get("entry_order_status", "") or "").upper() == "AWAITING_APPROVAL":
        pending.append(intent_id)
    return pending

  async def on_init(self) -> None:
    self._samples_by_instrument: Dict[str, List[TickSample]] = {}

  async def on_stop(self) -> None:
    return None

  def import_external_entry(
    self,
    instrument_code: str,
    volume: int,
    price: float,
    source_trade_id: str,
  ) -> RuntimeStatePatch:
    """Register a user-declared external buy fill as an auditable T batch."""

    code = str(instrument_code or "").strip().upper()
    if not code or not self._is_bound_instrument(code):
      raise ValueError("该股票不在当前做 T 监控范围内")
    if volume <= 0 or volume % 100 != 0:
      raise ValueError("外部买入数量必须是大于 0 的 100 股整数倍")
    if price <= 0:
      raise ValueError("外部买入成交均价必须大于 0")
    trade_id = str(source_trade_id or "").strip()
    if not trade_id:
      raise ValueError("成交编号不能为空")
    imported_ids = {
      str(event.get("source_trade_id", "") or "")
      for event in list(self.state.get("runtime_events", []) or [])
      if isinstance(event, dict)
      and event.get("type") == "T_TRADE_EXTERNAL_ENTRY_IMPORTED"
    }
    if trade_id in imported_ids:
      raise ValueError("该笔成交已经加入做 T 助手")

    state = self._instrument_state(code)
    if self._active_volume(state) > 0:
      raise ValueError("该股票已有未完成的 T 批次")
    if self._has_pending_intent(state):
      raise ValueError("该股票仍有待处理委托，不能导入外部成交")
    position_shares = int(state.get("position_shares", 0) or 0)
    if position_shares <= 0 or volume > position_shares:
      raise ValueError("外部买入数量不能超过当前账户持仓")

    batch_id = str(uuid.uuid4())
    state.update(
      {
        "status": TTradeStatus.MONITORING,
        "pending_entry_intent_id": "",
        "pending_exit_intent_id": "",
        "entry_order_status": "EXTERNAL_FILLED",
        "exit_order_status": "",
        "entry_filled_volume": int(volume),
        "entry_avg_price": float(price),
        "exit_filled_volume": 0,
        "exit_avg_price": 0.0,
        "peak_net_profit_pct": 0.0,
        "trailing_floor_pct": -999.0,
        "profit_armed": False,
        "last_exit_reason": "",
        "batch_id": batch_id,
        "batch_started_trade_date": "",
        "last_holding_trade_date": "",
        "holding_trading_days": 0,
        "exit_policy_snapshot": self._exit_policy_snapshot(),
        "current_signal": {
          "source": "MANUAL_EXTERNAL_ENTRY",
          "source_trade_id": trade_id,
          "entry_price": float(price),
          "entry_volume": int(volume),
        },
      }
    )
    states = self._instrument_states()
    states[code] = dict(state)
    return RuntimeStatePatch(
      set={"instrument_states": states},
      append_events=[
        {
          "type": "T_TRADE_EXTERNAL_ENTRY_IMPORTED",
          "instrument_code": code,
          "batch_id": batch_id,
          "volume": int(volume),
          "price": float(price),
          "source_trade_id": trade_id,
        }
      ],
    )

  async def step(self, input: StrategyInput) -> StrategyOutput:
    if input.cadence == StrategyCadence.RECONCILE:
      return self._reconcile_universe(input)
    if input.cadence != StrategyCadence.TICK:
      return StrategyOutput()
    if not self._is_bound_instrument(input.instrument_code):
      return StrategyOutput(
        decision_tags=["instrument_mismatch", "no_trade"],
        trace_payload={"reason": "INSTRUMENT_NOT_IN_HOLDINGS_UNIVERSE"},
      )

    sample = self._tick_sample(input)
    if sample is None:
      return StrategyOutput(decision_tags=["invalid_tick", "no_trade"])
    self._append_sample(input.instrument_code, sample)

    state = self._instrument_state(input.instrument_code)
    active_volume = self._active_volume(state)
    if active_volume > 0:
      return self._monitor_open_lot(input, sample, state, active_volume)
    return self._observe_for_entry(input, sample, state)

  async def on_order(self, event: OrderStateEvent) -> Optional[RuntimeStatePatch]:
    role = str(event.metadata.get("t_trade_role", "") or "")
    intent_id = str(event.metadata.get("intent_id", "") or "")
    code = self._event_instrument_code(event, intent_id)
    status = str(event.status or "").upper()
    if role not in {"entry", "exit"} or not code:
      return None

    state = self._instrument_state(code)
    pending_key = f"pending_{role}_intent_id"
    if intent_id and intent_id != str(state.get(pending_key, "") or ""):
      return None

    state[f"{role}_order_status"] = status
    terminal = status in {"FILLED", "REJECTED", "CANCELLED", "EXPIRED"}
    if terminal:
      state[pending_key] = ""

    if role == "entry":
      if status == "PARTIAL_FILLED":
        state["status"] = TTradeStatus.ENTRY_PARTIAL
      elif status in {"PENDING", "SUBMITTED", "ACCEPTED"}:
        state["status"] = TTradeStatus.ENTRY_SUBMITTED
      elif status in {"REJECTED", "CANCELLED", "EXPIRED"}:
        if self._active_volume(state) > 0:
          state["status"] = TTradeStatus.MONITORING
        else:
          state.update(
            {
              "status": TTradeStatus.OBSERVING,
              "current_signal": {},
              "batch_id": "",
              "exit_policy_snapshot": {},
            }
          )
      elif status == "FILLED":
        state["status"] = TTradeStatus.MONITORING
    else:
      if status == "PARTIAL_FILLED":
        state["status"] = TTradeStatus.EXIT_PARTIAL
      elif status in {"PENDING", "SUBMITTED", "ACCEPTED"}:
        state["status"] = TTradeStatus.EXIT_SUBMITTED
      elif status in {"REJECTED", "CANCELLED", "EXPIRED"}:
        state["status"] = TTradeStatus.MONITORING
      elif status == "FILLED" and self._active_volume(state) <= 0:
        state["status"] = TTradeStatus.COOLDOWN

    return self._apply_callback_state(code, state)

  async def on_trade(self, event: TradeExecutionEvent) -> Optional[RuntimeStatePatch]:
    role = str(event.metadata.get("t_trade_role", "") or "")
    code = str(event.instrument_code or event.metadata.get("instrument_code", "") or "")
    if role not in {"entry", "exit"} or not code or event.volume <= 0 or event.price <= 0:
      return None

    state = self._instrument_state(code)
    volume_key = f"{role}_filled_volume"
    price_key = f"{role}_avg_price"
    previous_volume = int(state.get(volume_key, 0) or 0)
    previous_price = float(state.get(price_key, 0.0) or 0.0)
    total_volume = previous_volume + int(event.volume)
    average_price = (
      previous_price * previous_volume + float(event.price) * int(event.volume)
    ) / total_volume
    state[volume_key] = total_volume
    state[price_key] = average_price

    if role == "entry":
      if not state.get("exit_policy_snapshot"):
        state["exit_policy_snapshot"] = self._exit_policy_snapshot()
      if not state.get("batch_started_trade_date"):
        trade_time = event.trade_time or self.context.current_time
        if trade_time:
          trade_date = trade_time.date().isoformat()
          state["batch_started_trade_date"] = trade_date
          state["last_holding_trade_date"] = trade_date
          state["holding_trading_days"] = 1
      state["status"] = TTradeStatus.MONITORING
      state["current_signal"] = {
        **dict(state.get("current_signal") or {}),
        "entry_price": average_price,
        "entry_volume": total_volume,
      }
    else:
      if self._active_volume(state) <= 0:
        policy = dict(state.get("exit_policy_snapshot") or {})
        cooldown_ms = int(policy.get("cooldown_seconds", self.get_parameter("cooldown_seconds", 300))) * 1000
        trade_time = event.trade_time or self.context.current_time
        now_ms = int(trade_time.timestamp() * 1000) if trade_time else 0
        state.update(
          {
            "status": TTradeStatus.COOLDOWN,
            "pending_exit_intent_id": "",
            "cooldown_until_ms": now_ms + cooldown_ms,
            "completed_cycles": int(state.get("completed_cycles", 0) or 0) + 1,
            "batch_id": "",
            "batch_started_trade_date": "",
            "last_holding_trade_date": "",
            "holding_trading_days": 0,
            "exit_policy_snapshot": {},
            "current_signal": {},
          }
        )
      else:
        state["status"] = TTradeStatus.EXIT_PARTIAL

    return self._apply_callback_state(code, state)

  def _reconcile_universe(self, input: StrategyInput) -> StrategyOutput:
    event = dict(input.event or {})
    desired = [str(code or "").upper() for code in event.get("instruments", []) if code]
    metadata = {
      str(code or "").upper(): dict(value or {})
      for code, value in dict(event.get("instrument_metadata") or {}).items()
    }
    states = self._instrument_states()

    for code in desired:
      state = dict(states.get(code) or self._empty_instrument_state())
      item = metadata.get(code)
      if item is not None:
        state["entry_eligible"] = bool(item.get("eligible", False))
        state["eligibility_reason"] = str(item.get("reason", "") or "")
        state["instrument_name"] = str(item.get("instrument_name", code) or code)
        state["position_shares"] = max(0, int(item.get("position_shares", 0) or 0))
        state["position_available_shares"] = max(
          0, int(item.get("position_available_shares", 0) or 0)
        )
        state["draining"] = bool(item.get("draining", False))
        if state["draining"]:
          state["entry_eligible"] = False
      states[code] = state

    for code in list(states):
      if code in desired:
        continue
      state = dict(states[code])
      if self._active_volume(state) > 0 or self._has_pending_intent(state):
        state.update(
          {
            "draining": True,
            "entry_eligible": False,
            "eligibility_reason": "REMOVED_FROM_HOLDINGS_UNIVERSE",
            "status": TTradeStatus.DRAINING,
          }
        )
        states[code] = state
      else:
        states.pop(code, None)
        self._samples_by_instrument.pop(code, None)

    return StrategyOutput(
      runtime_state_patch=RuntimeStatePatch(
        set={
          "instrument_states": states,
          "universe_revision": int(self.state.get("universe_revision", 0) or 0) + 1,
        }
      ),
      decision_tags=["holdings_universe_reconciled"],
      trace_payload={
        "reason": "ACCOUNT_HOLDINGS_UNIVERSE_RECONCILED",
        "added": list(event.get("added") or []),
        "removed": list(event.get("removed") or []),
        "instrument_count": len(desired),
      },
    )

  def _observe_for_entry(
    self, input: StrategyInput, sample: TickSample, state: Dict[str, Any]
  ) -> StrategyOutput:
    code = input.instrument_code
    if state.get("draining") or not state.get("entry_eligible"):
      state.update({"last_price": sample.price, "current_signal": {}})
      return self._state_output(
        code,
        state,
        tags=["entry_ineligible", "no_trade"],
        reason=str(state.get("eligibility_reason") or "POSITION_NOT_ELIGIBLE"),
      )
    if self._has_pending_intent(state):
      return StrategyOutput(decision_tags=["intent_pending"])
    if sample.timestamp_ms < int(state.get("cooldown_until_ms", 0) or 0):
      return StrategyOutput(decision_tags=["cooldown"])
    if self._should_block_new_entry(input):
      state.update(
        {
          "status": TTradeStatus.OBSERVING,
          "last_price": sample.price,
          "current_signal": {},
        }
      )
      return self._state_output(
        code,
        state,
        tags=["end_of_day_entry_blocked", "no_trade"],
        reason="END_OF_DAY_ENTRY_BLOCKED",
      )

    signal = evaluate_intraday_t_signal(
      self._samples_by_instrument.get(code, []), policy=self._signal_policy()
    )
    signal_payload = {
      "triggered": signal.triggered,
      "reason": signal.reason,
      "signal_price": signal.signal_price,
      "window_high": signal.window_high,
      "window_low": signal.window_low,
      "pullback_pct": signal.pullback_pct,
      "rebound_pct": signal.rebound_pct,
      "vwap": signal.vwap,
      "spread_ticks": signal.spread_ticks,
      "detected_at_ms": sample.timestamp_ms,
    }
    state.update(
      {
        "status": TTradeStatus.OBSERVING,
        "last_price": sample.price,
        "current_signal": signal_payload if signal.triggered else {},
      }
    )
    if not signal.triggered:
      return self._state_output(
        code,
        state,
        tags=["observing", signal.reason.lower()],
        reason=signal.reason,
        trace={"signal": signal_payload},
      )

    sizing = calculate_target_trade_volume(
      entry_price=sample.ask_price or sample.price,
      available_volume=int(state.get("position_available_shares", 0) or 0),
      target_amount=float(self.get_parameter("target_trade_amount", 10_000.0)),
      max_amount=float(self.get_parameter("max_trade_amount", 12_000.0)),
    )
    desired_volume = sizing.volume
    signal_payload.update(
      {
        "requested_volume": desired_volume,
        "estimated_entry_amount": sizing.estimated_amount,
        "sizing_reason": sizing.reason,
      }
    )
    state["current_signal"] = signal_payload
    if desired_volume <= 0:
      return self._state_output(
        code,
        state,
        tags=[sizing.reason.lower(), "no_trade"],
        reason=sizing.reason,
        trace={"signal": signal_payload, "sizing": sizing.__dict__},
      )

    batch_id = str(uuid.uuid4())
    intent = TradeIntent(
      strategy_id=str(input.strategy_id),
      run_id=input.run_id,
      instrument_code=code,
      direction=TradeIntentDirection.BUY,
      bucket=SWING_BUCKET,
      reason="T_TRADE_PULLBACK_REBOUND_ENTRY",
      priority=TradeIntentPriority.NORMAL,
      target_volume=desired_volume,
      limit_price_hint=sample.ask_price or sample.price,
      execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
      approval_ttl_ms=int(self.get_parameter("approval_ttl_seconds", 30)) * 1000,
      max_price_deviation_bps=float(self.get_parameter("max_price_deviation_pct", 0.3)) * 100.0,
      metadata={
        "t_trade_role": "entry",
        "instrument_code": code,
        "t_batch_id": batch_id,
        "global_monitor_id": str(self.get_parameter("global_monitor_id", "") or ""),
        "config_version": int(self.get_parameter("global_config_version", 0) or 0),
        "signal": signal_payload,
        "requested_entry_volume": desired_volume,
        "target_trade_amount": float(
          self.get_parameter("target_trade_amount", 10_000.0)
        ),
        "max_trade_amount": float(
          self.get_parameter("max_trade_amount", 12_000.0)
        ),
      },
    )
    state.update(
      {
        "status": TTradeStatus.AWAITING_APPROVAL,
        "pending_entry_intent_id": intent.intent_id,
        "entry_order_status": "AWAITING_APPROVAL",
        "entry_filled_volume": 0,
        "entry_avg_price": 0.0,
        "exit_filled_volume": 0,
        "exit_avg_price": 0.0,
        "peak_net_profit_pct": 0.0,
        "trailing_floor_pct": -999.0,
        "profit_armed": False,
        "last_exit_reason": "",
        "batch_id": batch_id,
        "requested_entry_volume": desired_volume,
        "batch_started_trade_date": "",
        "last_holding_trade_date": "",
        "holding_trading_days": 0,
        "exit_policy_snapshot": {},
      }
    )
    return StrategyOutput(
      trade_intents=[intent],
      runtime_state_patch=self._patch_instrument_state(code, state),
      decision_tags=["t_trade_signal", "manual_confirmation_required"],
      trace_payload={"reason": signal.reason, "signal": signal_payload},
    )

  def _monitor_open_lot(
    self,
    input: StrategyInput,
    sample: TickSample,
    state: Dict[str, Any],
    active_volume: int,
  ) -> StrategyOutput:
    code = input.instrument_code
    policy, policy_event = self._refresh_exit_policy(input, state)
    self._update_holding_period(input, state)
    entry_price = float(state.get("entry_avg_price", 0.0) or 0.0)
    executable_price = sample.bid_price or sample.price
    net_profit_pct = estimate_net_profit_pct(
      entry_price=entry_price,
      exit_price=executable_price,
      volume=active_volume,
      costs=self._cost_policy(policy),
    )
    peak = max(float(state.get("peak_net_profit_pct", 0.0) or 0.0), net_profit_pct)
    previous_floor = float(state.get("trailing_floor_pct", -999.0))
    floor = calculate_trailing_floor_pct(
      peak_profit_pct=peak,
      previous_floor_pct=None if previous_floor <= -900 else previous_floor,
      policy=self._trailing_policy(policy),
    )
    armed = floor is not None
    state.update(
      {
        "last_price": sample.price,
        "last_net_profit_pct": net_profit_pct,
        "peak_net_profit_pct": peak,
        "trailing_floor_pct": floor if floor is not None else -999.0,
        "profit_armed": armed,
        "status": TTradeStatus.PROFIT_ARMED if armed else TTradeStatus.MONITORING,
      }
    )

    exit_reason = ""
    if not self._has_pending_intent(state):
      if bool(policy.get("hard_stop_enabled", False)) and net_profit_pct <= float(
        policy.get("hard_stop_pct", -0.8)
      ):
        exit_reason = "HARD_STOP"
      elif armed and floor is not None and net_profit_pct <= floor:
        exit_reason = "TRAILING_FLOOR_REACHED"
      else:
        exit_reason = self._time_exit_reason(input, state, policy)

    if not exit_reason:
      return self._state_output(
        code,
        state,
        tags=["profit_armed" if armed else "monitoring"],
        reason="MONITOR_T_LOT",
        trace={
          "net_profit_pct": net_profit_pct,
          "peak_net_profit_pct": peak,
          "trailing_floor_pct": floor,
          "time_exit_mode": policy.get("time_exit_mode"),
          "holding_trading_days": int(state.get("holding_trading_days", 0) or 0),
        },
        append_events=[policy_event] if policy_event else None,
      )

    intent = TradeIntent(
      strategy_id=str(input.strategy_id),
      run_id=input.run_id,
      instrument_code=code,
      direction=TradeIntentDirection.SELL,
      bucket=SWING_BUCKET,
      reason=f"T_TRADE_{exit_reason}",
      priority=TradeIntentPriority.RISK_REDUCTION,
      target_volume=active_volume,
      limit_price_hint=executable_price,
      execution_mode=TradeIntentExecutionMode.AUTO,
      metadata={
        "t_trade_role": "exit",
        "instrument_code": code,
        "t_batch_id": str(state.get("batch_id", "") or ""),
        "exit_policy_version": int(policy.get("config_version", 0) or 0),
        "price_type": "MARKET",
        "execution_urgency": "PROTECTIVE_EXIT",
        "exit_reason": exit_reason,
        "entry_price": entry_price,
        "net_profit_pct": net_profit_pct,
        "peak_net_profit_pct": peak,
        "trailing_floor_pct": floor,
      },
    )
    state.update(
      {
        "status": TTradeStatus.EXIT_TRIGGERED,
        "pending_exit_intent_id": intent.intent_id,
        "exit_order_status": "PENDING",
        "last_exit_reason": exit_reason,
      }
    )
    return StrategyOutput(
      trade_intents=[intent],
      runtime_state_patch=self._patch_instrument_state(
        code,
        state,
        append_events=[policy_event] if policy_event else None,
      ),
      decision_tags=["auto_exit", exit_reason.lower()],
      trace_payload={
        "reason": exit_reason,
        "net_profit_pct": net_profit_pct,
        "peak_net_profit_pct": peak,
        "trailing_floor_pct": floor,
        "time_exit_mode": policy.get("time_exit_mode"),
        "holding_trading_days": int(state.get("holding_trading_days", 0) or 0),
      },
    )

  @staticmethod
  def _empty_instrument_state() -> Dict[str, Any]:
    return {
      "status": TTradeStatus.OBSERVING,
      "entry_eligible": False,
      "eligibility_reason": "WAITING_FOR_HOLDINGS_SNAPSHOT",
      "requested_entry_volume": 0,
      "instrument_name": "",
      "position_shares": 0,
      "position_available_shares": 0,
      "draining": False,
      "current_signal": {},
      "pending_entry_intent_id": "",
      "pending_exit_intent_id": "",
      "entry_order_status": "",
      "exit_order_status": "",
      "entry_filled_volume": 0,
      "entry_avg_price": 0.0,
      "exit_filled_volume": 0,
      "exit_avg_price": 0.0,
      "last_price": 0.0,
      "last_net_profit_pct": 0.0,
      "peak_net_profit_pct": 0.0,
      "trailing_floor_pct": -999.0,
      "profit_armed": False,
      "last_exit_reason": "",
      "cooldown_until_ms": 0,
      "completed_cycles": 0,
      "batch_id": "",
      "batch_started_trade_date": "",
      "last_holding_trade_date": "",
      "holding_trading_days": 0,
      "exit_policy_snapshot": {},
    }

  def _instrument_states(self) -> Dict[str, Dict[str, Any]]:
    return {
      str(code): dict(value or {})
      for code, value in dict(self.state.get("instrument_states", {}) or {}).items()
    }

  def _instrument_state(self, code: str) -> Dict[str, Any]:
    state = self._empty_instrument_state()
    state.update(self._instrument_states().get(code, {}))
    return state

  def _patch_instrument_state(
    self,
    code: str,
    state: Dict[str, Any],
    *,
    append_events: Optional[List[Dict[str, Any]]] = None,
  ) -> RuntimeStatePatch:
    states = self._instrument_states()
    states[code] = dict(state)
    return RuntimeStatePatch(
      set={"instrument_states": states},
      append_events=list(append_events or []),
    )

  def _apply_callback_state(
    self, code: str, state: Dict[str, Any]
  ) -> RuntimeStatePatch:
    patch = self._patch_instrument_state(code, state)
    self.state.update(patch.set)
    return patch

  def _state_output(
    self,
    code: str,
    state: Dict[str, Any],
    *,
    tags: List[str],
    reason: str,
    trace: Optional[Dict[str, Any]] = None,
    append_events: Optional[List[Dict[str, Any]]] = None,
  ) -> StrategyOutput:
    return StrategyOutput(
      runtime_state_patch=self._patch_instrument_state(
        code,
        state,
        append_events=append_events,
      ),
      decision_tags=tags,
      trace_payload={"reason": reason, **dict(trace or {})},
    )

  @staticmethod
  def _active_volume(state: Dict[str, Any]) -> int:
    return max(
      0,
      int(state.get("entry_filled_volume", 0) or 0)
      - int(state.get("exit_filled_volume", 0) or 0),
    )

  @staticmethod
  def _has_pending_intent(state: Dict[str, Any]) -> bool:
    return bool(
      state.get("pending_entry_intent_id") or state.get("pending_exit_intent_id")
    )

  def _event_instrument_code(self, event: OrderStateEvent, intent_id: str) -> str:
    code = str(event.metadata.get("instrument_code", "") or "")
    if not code and event.request is not None:
      code = str(getattr(event.request, "instrument_code", "") or "")
    if code:
      return code
    if intent_id:
      for candidate, state in self._instrument_states().items():
        if intent_id in {
          str(state.get("pending_entry_intent_id", "") or ""),
          str(state.get("pending_exit_intent_id", "") or ""),
        }:
          return candidate
    return ""

  def _append_sample(self, code: str, sample: TickSample) -> None:
    lookback_ms = int(self.get_parameter("signal_lookback_seconds", 300)) * 1000
    cutoff = sample.timestamp_ms - lookback_ms
    samples = list(self._samples_by_instrument.get(code, []))
    samples.append(sample)
    self._samples_by_instrument[code] = [
      item for item in samples[-3000:] if item.timestamp_ms >= cutoff
    ]

  def _is_bound_instrument(self, code: str) -> bool:
    return bool(code) and code in set(self.context.instruments or [])

  @staticmethod
  def _tick_sample(input: StrategyInput) -> Optional[TickSample]:
    tick = input.event
    price = float(getattr(tick, "last_price", 0.0) or 0.0)
    if price <= 0:
      return None
    bids = list(getattr(tick, "bid_price", []) or [])
    asks = list(getattr(tick, "ask_price", []) or [])
    return TickSample(
      timestamp_ms=int(input.timestamp.timestamp() * 1000),
      price=price,
      bid_price=float(bids[0] if bids and bids[0] else 0.0),
      ask_price=float(asks[0] if asks and asks[0] else 0.0),
      cumulative_amount=float(getattr(tick, "amount", 0.0) or 0.0),
      cumulative_volume=float(getattr(tick, "volume", 0.0) or 0.0),
    )

  def _signal_policy(self) -> SignalPolicy:
    return SignalPolicy(
      lookback_seconds=int(self.get_parameter("signal_lookback_seconds", 300)),
      stabilization_seconds=int(self.get_parameter("stabilization_seconds", 15)),
      pullback_threshold_pct=float(self.get_parameter("pullback_threshold_pct", 0.8)),
      rebound_threshold_pct=float(self.get_parameter("rebound_threshold_pct", 0.2)),
      max_spread_ticks=int(self.get_parameter("max_spread_ticks", 3)),
    )

  def _exit_policy_snapshot(self) -> Dict[str, Any]:
    keys = [
      "target_profit_pct",
      "base_floor_pct",
      "initial_gap_pct",
      "trailing_gap_slope",
      "max_gap_pct",
      "hard_stop_enabled",
      "hard_stop_pct",
      "time_exit_mode",
      "time_exit_time",
      "max_holding_trading_days",
      "cooldown_seconds",
      "commission_rate",
      "minimum_commission",
      "stamp_tax_rate",
      "transfer_fee_rate",
    ]
    defaults = {
      "target_profit_pct": 2.0,
      "base_floor_pct": 0.5,
      "initial_gap_pct": 1.5,
      "trailing_gap_slope": 0.25,
      "max_gap_pct": 3.0,
      "hard_stop_enabled": False,
      "hard_stop_pct": -0.8,
      "time_exit_mode": TTradeTimeExitMode.UNLIMITED,
      "time_exit_time": "14:50",
      "max_holding_trading_days": 5,
      "cooldown_seconds": 300,
      "commission_rate": 0.0003,
      "minimum_commission": 5.0,
      "stamp_tax_rate": 0.0005,
      "transfer_fee_rate": 0.00001,
    }
    snapshot = {key: self.get_parameter(key, defaults[key]) for key in keys}
    if self.get_parameter("time_exit_mode", None) is None:
      snapshot["time_exit_mode"] = (
        TTradeTimeExitMode.END_OF_DAY
        if bool(self.get_parameter("flatten_end_of_day", False))
        else TTradeTimeExitMode.UNLIMITED
      )
      snapshot["time_exit_time"] = self.get_parameter(
        "end_of_day_exit_time", "14:50"
      )
    if self.get_parameter("hard_stop_enabled", None) is None:
      snapshot["hard_stop_enabled"] = self.get_parameter("hard_stop_pct", None) is not None
    snapshot["config_version"] = int(self.get_parameter("global_config_version", 0) or 0)
    return snapshot

  @staticmethod
  def _trailing_policy(policy: Dict[str, Any]) -> TrailingProfitPolicy:
    return TrailingProfitPolicy(
      target_profit_pct=float(policy.get("target_profit_pct", 2.0)),
      base_floor_pct=float(policy.get("base_floor_pct", 0.5)),
      initial_gap_pct=float(policy.get("initial_gap_pct", 1.5)),
      gap_slope=float(policy.get("trailing_gap_slope", 0.25)),
      max_gap_pct=float(policy.get("max_gap_pct", 3.0)),
    )

  @staticmethod
  def _cost_policy(policy: Dict[str, Any]) -> TradingCostPolicy:
    return TradingCostPolicy(
      commission_rate=float(policy.get("commission_rate", 0.0003)),
      minimum_commission=float(policy.get("minimum_commission", 5.0)),
      stamp_tax_rate=float(policy.get("stamp_tax_rate", 0.0005)),
      transfer_fee_rate=float(policy.get("transfer_fee_rate", 0.00001)),
    )

  def _is_time_exit(
    self, input: StrategyInput, policy: Optional[Dict[str, Any]] = None
  ) -> bool:
    raw = str(
      (policy or {}).get(
        "time_exit_time",
        self.get_parameter(
          "time_exit_time",
          self.get_parameter("end_of_day_exit_time", "14:50"),
        ),
      )
      or "14:50"
    )
    try:
      hour, minute = (int(part) for part in raw.split(":", 1))
      exit_time = time(hour=hour, minute=minute)
    except (TypeError, ValueError):
      exit_time = time(hour=14, minute=50)
    return input.timestamp.time() >= exit_time

  def _should_block_new_entry(self, input: StrategyInput) -> bool:
    policy = self._exit_policy_snapshot()
    mode = str(policy.get("time_exit_mode", TTradeTimeExitMode.UNLIMITED) or "")
    if mode == TTradeTimeExitMode.END_OF_DAY:
      return self._is_time_exit(input, policy)
    return (
      mode == TTradeTimeExitMode.MAX_HOLDING_DAYS
      and int(policy.get("max_holding_trading_days", 5) or 5) == 1
      and self._is_time_exit(input, policy)
    )

  def _time_exit_reason(
    self,
    input: StrategyInput,
    state: Dict[str, Any],
    policy: Dict[str, Any],
  ) -> str:
    mode = str(policy.get("time_exit_mode", TTradeTimeExitMode.UNLIMITED) or "")
    if mode == TTradeTimeExitMode.END_OF_DAY and self._is_time_exit(input, policy):
      return "END_OF_DAY_FLATTEN"
    if (
      mode == TTradeTimeExitMode.MAX_HOLDING_DAYS
      and int(state.get("holding_trading_days", 0) or 0)
      >= int(policy.get("max_holding_trading_days", 5) or 5)
      and self._is_time_exit(input, policy)
    ):
      return "MAX_HOLDING_DAYS_REACHED"
    return ""

  @staticmethod
  def _update_holding_period(input: StrategyInput, state: Dict[str, Any]) -> None:
    trade_date = input.trade_date
    last_date = str(state.get("last_holding_trade_date", "") or "")
    if not last_date:
      state["batch_started_trade_date"] = trade_date
      state["last_holding_trade_date"] = trade_date
      state["holding_trading_days"] = 1
    elif trade_date > last_date:
      state["last_holding_trade_date"] = trade_date
      state["holding_trading_days"] = int(
        state.get("holding_trading_days", 0) or 0
      ) + 1

  def _refresh_exit_policy(
    self, input: StrategyInput, state: Dict[str, Any]
  ) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    previous = self._normalize_exit_policy(
      dict(state.get("exit_policy_snapshot") or {})
    )
    current = self._exit_policy_snapshot()
    if not previous:
      state["exit_policy_snapshot"] = current
      return current, None
    previous_version = int(previous.get("config_version", 0) or 0)
    current_version = int(current.get("config_version", 0) or 0)
    if current_version == previous_version:
      return previous, None
    state["exit_policy_snapshot"] = current
    return current, {
      "type": "T_TRADE_EXIT_POLICY_UPDATED",
      "instrument_code": input.instrument_code,
      "batch_id": str(state.get("batch_id", "") or ""),
      "changed_at": input.timestamp.isoformat(),
      "previous_config_version": previous_version,
      "config_version": current_version,
      "previous_policy": previous,
      "policy": current,
      "previous_time_exit_mode": self._legacy_time_exit_mode(previous),
      "time_exit_mode": current.get("time_exit_mode"),
      "previous_hard_stop_enabled": self._legacy_hard_stop_enabled(previous),
      "hard_stop_enabled": bool(current.get("hard_stop_enabled", False)),
    }

  @classmethod
  def _normalize_exit_policy(cls, policy: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(policy)
    if "time_exit_mode" not in normalized:
      normalized["time_exit_mode"] = cls._legacy_time_exit_mode(normalized)
    if "time_exit_time" not in normalized:
      normalized["time_exit_time"] = str(
        normalized.get("end_of_day_exit_time", "14:50") or "14:50"
      )
    if "max_holding_trading_days" not in normalized:
      normalized["max_holding_trading_days"] = 5
    if "hard_stop_enabled" not in normalized:
      normalized["hard_stop_enabled"] = cls._legacy_hard_stop_enabled(normalized)
    return normalized

  @staticmethod
  def _legacy_time_exit_mode(policy: Dict[str, Any]) -> str:
    if policy.get("time_exit_mode"):
      return str(policy["time_exit_mode"])
    return (
      TTradeTimeExitMode.END_OF_DAY
      if bool(policy.get("flatten_end_of_day", False))
      else TTradeTimeExitMode.UNLIMITED
    )

  @staticmethod
  def _legacy_hard_stop_enabled(policy: Dict[str, Any]) -> bool:
    if "hard_stop_enabled" in policy:
      return bool(policy.get("hard_stop_enabled"))
    return "hard_stop_pct" in policy
