"""Account-level, radar-coordinated A-share limit-up board assistant."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from quantx_domain.enums import (
  StrategyInstrumentScope,
  StrategyInstrumentUniverseMode,
)
from quantx_domain.schemas import ParameterProperty, ParameterSchema
from quantx_domain.state_schema import StateProperty, StateSchema
from quantx_domain.strategies.ashare_limit_up_board import (
  SWING_BUCKET,
  AshareLimitUpBoardStrategy,
  _has_active_exit_plan,
  _has_open_buy,
  _parse_time,
  _position_volume,
)
from quantx_domain.strategies.base import (
  OrderStateEvent,
  RuntimeStatePatch,
  StrategyCadence,
  StrategyInput,
  StrategyOutput,
  TradeExecutionEvent,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentExecutionMode,
  TradeIntentPriority,
)
from quantx_domain.trading.exit_plan import ExitPlanTemplate


class AshareLimitUpBoardAssistantStrategy(AshareLimitUpBoardStrategy):
  """Run all account board candidates in one externally coordinated strategy."""

  TAGS = ["A股", "打板", "雷达", "人工确认", "自动退出", "T+1", "动态候选"]
  INSTRUMENT_SCOPE = StrategyInstrumentScope.MULTI
  INSTRUMENT_UNIVERSE_MODE = StrategyInstrumentUniverseMode.RADAR_CANDIDATES

  @property
  def name(self) -> str:
    return "A股账户级打板助手"

  @property
  def version(self) -> str:
    return "2.0.0"

  @property
  def description(self) -> str:
    return (
      "由全市场雷达协调候选，在一个账户级策略中生成限时人工确认买入意图，"
      "真实成交后交由统一退出计划自动托管。"
    )

  @classmethod
  def get_parameter_schema(cls) -> ParameterSchema:
    schema = super().get_parameter_schema()
    schema.additionalProperties = True
    schema.properties.update(
      {
        "account_id": ParameterProperty(
          type="string", default="", title="交易账户", group="binding"
        ),
        "max_single_position_pct": ParameterProperty(
          type="number",
          minimum=0.005,
          maximum=0.30,
          default=0.02,
          title="单标的资产上限",
          group="sizing",
          unit="ratio",
        ),
        "max_daily_exposure_pct": ParameterProperty(
          type="number",
          minimum=0.005,
          maximum=0.30,
          default=0.06,
          title="当日新增风险敞口上限",
          group="sizing",
          unit="ratio",
        ),
        "planned_tail_loss_pct": ParameterProperty(
          type="number",
          minimum=0.0001,
          maximum=0.02,
          default=0.0015,
          title="单笔计划尾损",
          group="sizing",
          unit="ratio",
        ),
        "liquidity_participation_pct": ParameterProperty(
          type="number",
          minimum=0.0001,
          maximum=0.05,
          default=0.005,
          title="成交额参与上限",
          group="sizing",
          unit="ratio",
        ),
        "max_open_positions": ParameterProperty(
          type="integer",
          minimum=1,
          maximum=10,
          default=2,
          title="最多同时持仓",
          group="sizing",
        ),
        "promotion_model_mode": ParameterProperty(
          type="string",
          default="SHADOW",
          title="发布阶段",
          group="binding",
        ),
        "execution_quote_max_age_seconds": ParameterProperty(
          type="number",
          minimum=0.25,
          maximum=15,
          default=3,
          title="执行行情最大年龄",
          group="execution",
          unit="seconds",
        ),
        "global_config_version": ParameterProperty(
          type="integer", minimum=0, default=0, group="binding"
        ),
      }
    )
    # Account assistants never auto-enter, including in live mode.
    schema.properties["entry_execution_mode"].default = "MANUAL_CONFIRM"
    schema.properties["target_position_pct"].default = 0.02
    return schema

  @classmethod
  def get_state_schema(cls) -> StateSchema:
    return StateSchema(
      type="object",
      properties={
        "instrument_states": StateProperty(type="object", default={}),
        "universe_revision": StateProperty(type="integer", default=0),
      },
    )

  def _instrument_states(self) -> Dict[str, Dict[str, Any]]:
    return {
      str(code): dict(value or {})
      for code, value in dict(self.state.get("instrument_states", {}) or {}).items()
    }

  @staticmethod
  def _empty_instrument_state() -> Dict[str, Any]:
    return {
      "trade_date": "",
      "confirmed_attempt_count": 0,
      "pending_entry_intent_id": "",
      "pending_entry_status": "",
      "inside_entry_band": False,
      "last_arm_version": 0,
      "last_signal_at_ms": 0,
      "last_signal_price": 0.0,
      "signal_sequence": 0,
      "last_entry_trade_date": "",
      "last_entry_price": 0.0,
      "last_entry_volume": 0,
      "entry_eligible": False,
      "eligibility_reason": "NOT_IN_RADAR_UNIVERSE",
      "candidate_source": "",
      "radar_score": 0.0,
      "radar_stage": "",
      "radar_updated_at": "",
      "radar_is_stale": False,
      "promotion_eligible": False,
      "promotion_score": 0.0,
      "promotion_snapshot_version": "",
      "promotion_model_version": "",
      "exit_policy_version": "",
      "board_segment": "",
      "cvar95_loss_pct": 0.0,
      "expected_net_return_pct": 0.0,
      "target_position_pct": 0.0,
      "liquidity_cap_amount": 0.0,
      "high_position_type": "",
      "draining": False,
    }

  def pending_manual_intent_ids(self) -> list[str]:
    result: list[str] = []
    for state in self._instrument_states().values():
      intent_id = str(state.get("pending_entry_intent_id", "") or "")
      status = str(state.get("pending_entry_status", "") or "").upper()
      if intent_id and status == "AWAITING_APPROVAL":
        result.append(intent_id)
    return result

  async def step(self, input: StrategyInput) -> StrategyOutput:
    if input.cadence == StrategyCadence.RECONCILE:
      return self._reconcile_universe(input)
    if input.cadence != StrategyCadence.TICK:
      return StrategyOutput()
    if not self.is_running:
      return self._no_trade("strategy_not_running")

    code = str(input.instrument_code or "").upper()
    states = self._instrument_states()
    if code not in states or code not in set(self.context.instruments or []):
      return self._no_trade("instrument_not_in_radar_universe")
    state = dict(states.get(code) or self._empty_instrument_state())
    if state.get("trade_date") != input.trade_date:
      state.update(
        {
          "trade_date": input.trade_date,
          "confirmed_attempt_count": 0,
          "inside_entry_band": False,
        }
      )

    snapshot = self._market_snapshot(input)
    in_band = self._is_entry_band(snapshot)
    if not in_band:
      state["inside_entry_band"] = False

    block_reason = self._assistant_entry_block_reason(input, snapshot, state)
    if block_reason:
      return self._state_output(code, states, state, block_reason, snapshot)
    if state.get("inside_entry_band"):
      return self._state_output(code, states, state, "entry_band_already_seen", snapshot)

    daily_cap = self._daily_exposure_cap(input)
    daily_used = self._daily_exposure_pct(states, input)
    target_position_pct = min(
      float(state.get("target_position_pct", 0.0) or 0.0),
      max(0.0, daily_cap - daily_used),
    )
    if target_position_pct <= 0:
      return self._state_output(
        code, states, state, "risk_budget_zero", snapshot
      )

    signal_sequence = int(state.get("signal_sequence", 0) or 0) + 1
    plan = self._build_exit_plan(
      input,
      snapshot,
      signal_sequence,
    )
    intent = TradeIntent(
      strategy_id=input.strategy_id,
      run_id=input.run_id,
      instrument_code=code,
      direction=TradeIntentDirection.BUY,
      bucket=SWING_BUCKET,
      reason="limit_up_board_assistant_entry",
      priority=TradeIntentPriority.HIGH,
      confidence=self._entry_confidence(snapshot),
      target_position_pct=target_position_pct,
      limit_price_hint=float(snapshot["limit_up"]),
      execution_mode=TradeIntentExecutionMode.MANUAL_CONFIRM,
      approval_ttl_ms=int(self.get_parameter("approval_ttl_ms", 15_000) or 15_000),
      max_price_deviation_bps=float(
        self.get_parameter("max_price_deviation_bps", 20) or 0.0
      ),
      expiry_policy={
        "type": "TTL_MS",
        "expire_at_ms": input.decision_time_ms
        + int(self.get_parameter("approval_ttl_ms", 15_000) or 15_000),
      },
      trace_id=input.trace_id,
      metadata={
        "entry_style": "LIMIT_UP_BOARD_ASSISTANT",
        "candidate_source": str(state.get("candidate_source") or "AUTO"),
        "radar_score": float(state.get("radar_score", 0.0) or 0.0),
        "radar_stage": str(state.get("radar_stage") or ""),
        "radar_updated_at": str(state.get("radar_updated_at") or ""),
        "signal_price": float(snapshot["price"]),
        "limit_up": float(snapshot["limit_up"]),
        "distance_to_limit_ticks": float(snapshot["distance_to_limit_ticks"]),
        "target_position_pct": target_position_pct,
        "daily_exposure_cap_pct": daily_cap,
        "daily_exposure_used_pct": daily_used,
        "liquidity_cap_amount": float(
          state.get("liquidity_cap_amount", 0.0) or 0.0
        ),
        "max_single_position_pct": float(
          self.get_parameter("max_single_position_pct", 0.02) or 0.02
        ),
        "planned_tail_loss_pct": float(
          self.get_parameter("planned_tail_loss_pct", 0.0015) or 0.0015
        ),
        "cvar95_loss_pct": float(state.get("cvar95_loss_pct", 0.0) or 0.0),
        "promotion_snapshot_version": str(
          state.get("promotion_snapshot_version") or ""
        ),
        "promotion_model_version": str(state.get("promotion_model_version") or ""),
        "exit_policy_version": str(state.get("exit_policy_version") or ""),
        "board_segment": str(state.get("board_segment") or ""),
        "high_position_type": str(state.get("high_position_type") or ""),
        "price_type": "LIMIT",
        "order_ttl_ms": int(
          self.get_parameter("entry_order_ttl_ms", 15_000) or 15_000
        ),
        "exit_plan_template": plan.to_dict(),
      },
    )
    state.update(
      {
        "pending_entry_intent_id": intent.intent_id,
        "pending_entry_status": "AWAITING_APPROVAL",
        "inside_entry_band": True,
        "last_signal_at_ms": input.decision_time_ms,
        "last_signal_price": float(snapshot["price"]),
        "signal_sequence": signal_sequence,
      }
    )
    states[code] = state
    patch = {"instrument_states": states}
    self.state.update(patch)
    return StrategyOutput(
      trade_intents=[intent],
      runtime_state_patch=RuntimeStatePatch(set=patch),
      decision_tags=["limit_up_board_assistant_entry", "entry_intent_created"],
      trace_payload={
        "reason": "limit_up_board_assistant_entry",
        "market": snapshot,
        "candidate_source": state.get("candidate_source"),
        "radar_score": state.get("radar_score"),
      },
    )

  async def on_order(self, event: OrderStateEvent) -> Optional[RuntimeStatePatch]:
    intent_id = str(event.metadata.get("intent_id", "") or "")
    code = str(event.metadata.get("instrument_code", "") or "").upper()
    states = self._instrument_states()
    candidates = [code] if code else list(states)
    for instrument_code in candidates:
      state = dict(states.get(instrument_code) or {})
      if str(state.get("pending_entry_intent_id", "") or "") != intent_id:
        continue
      status = str(event.status or "").upper()
      previous_status = str(state.get("pending_entry_status", "") or "").upper()
      state["pending_entry_status"] = status
      if status == "PENDING" and previous_status == "AWAITING_APPROVAL":
        state["confirmed_attempt_count"] = int(
          state.get("confirmed_attempt_count", 0) or 0
        ) + 1
      if status in {"REJECTED", "CANCELLED", "EXPIRED"}:
        state.update({"pending_entry_intent_id": "", "pending_entry_status": ""})
      states[instrument_code] = state
      patch = {"instrument_states": states}
      self.state.update(patch)
      return RuntimeStatePatch(set=patch)
    return None

  async def on_trade(self, event: TradeExecutionEvent) -> Optional[RuntimeStatePatch]:
    trade_type = str(event.trade_type or "").upper()
    if trade_type not in {"BUY", "SELL"} or event.volume <= 0:
      return None
    code = str(event.instrument_code or "").upper()
    states = self._instrument_states()
    if code not in states:
      return None
    state = dict(states[code])
    if trade_type == "SELL":
      remaining = max(
        0, int(state.get("last_entry_volume", 0) or 0) - int(event.volume)
      )
      state.update(
        {
          "last_entry_volume": remaining,
          "last_entry_price": (
            float(state.get("last_entry_price", 0.0) or 0.0)
            if remaining > 0
            else 0.0
          ),
          "draining": remaining > 0,
          "entry_eligible": False,
          "eligibility_reason": (
            "ACTIVE_EXIT_PLAN" if remaining > 0 else "POSITION_EXITED"
          ),
        }
      )
      states[code] = state
      patch = {"instrument_states": states}
      self.state.update(patch)
      return RuntimeStatePatch(set=patch)
    trade_date = (
      event.trade_time.date().isoformat()
      if event.trade_time is not None
      else str(state.get("trade_date", "") or "")
    )
    previous_volume = (
      int(state.get("last_entry_volume", 0) or 0)
      if str(state.get("last_entry_trade_date", "") or "") == trade_date
      else 0
    )
    previous_price = float(state.get("last_entry_price", 0.0) or 0.0)
    filled_volume = previous_volume + int(event.volume)
    average_price = (
      (previous_price * previous_volume + float(event.price) * int(event.volume))
      / filled_volume
      if filled_volume > 0
      else float(event.price)
    )
    state.update(
      {
        "pending_entry_intent_id": "",
        "pending_entry_status": "",
        "last_entry_trade_date": trade_date,
        "last_entry_price": average_price,
        "last_entry_volume": filled_volume,
        "draining": True,
        "entry_eligible": False,
        "eligibility_reason": "ACTIVE_EXIT_PLAN",
      }
    )
    states[code] = state
    patch = {"instrument_states": states}
    self.state.update(patch)
    return RuntimeStatePatch(set=patch)

  def validate_manual_approval(
    self, intent: TradeIntent, market_data: Any
  ) -> Optional[tuple[str, str]]:
    """Recheck the fast-changing board gate immediately before order sizing."""

    if intent.direction != TradeIntentDirection.BUY or market_data is None:
      return None
    code = str(intent.instrument_code or "").upper()
    state = self._instrument_states().get(code, {})
    if not state.get("promotion_eligible") or not state.get("entry_eligible"):
      return "PROMOTION_NO_LONGER_ELIGIBLE", "候选已失去首板晋级资格"
    if str(intent.metadata.get("promotion_model_version") or "") != str(
      state.get("promotion_model_version") or ""
    ):
      return "PROMOTION_MODEL_CHANGED", "晋级模型版本已经变化，请重新确认"
    price = float(
      getattr(market_data, "price", 0.0)
      or getattr(market_data, "last_price", 0.0)
      or 0.0
    )
    limit_up = float(
      getattr(market_data, "limit_up", 0.0)
      or getattr(market_data, "up_stop_price", 0.0)
      or intent.limit_price_hint
      or 0.0
    )
    price_tick = max(float(getattr(market_data, "price_tick", 0.01) or 0.01), 1e-8)
    if price <= 0 or limit_up <= 0:
      return "BOARD_QUOTE_INVALID", "最新行情缺少有效价格或涨停价"
    distance_ticks = (limit_up - price) / price_tick
    if distance_ticks <= 1e-6:
      return "BOARD_ALREADY_AT_LIMIT", "股票已触及或封住涨停，请等待新信号"
    if distance_ticks > int(self.get_parameter("entry_distance_ticks", 1) or 1):
      return "BOARD_LEFT_ENTRY_BAND", "股票已离开临板价位，请等待新信号"
    asks = list(getattr(market_data, "ask_price", []) or [])
    if asks and float(asks[0] or 0.0) <= 0:
      return "BOARD_ALREADY_SEALED", "卖一已消失，疑似封板，禁止追单"
    return None

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
      item = metadata.get(code, {})
      arm_version = int(item.get("arm_version", 0) or 0)
      if arm_version != int(state.get("last_arm_version", 0) or 0):
        state["inside_entry_band"] = False
      state.update(
        {
          "entry_eligible": bool(item.get("eligible", False)),
          "eligibility_reason": str(item.get("reason", "") or ""),
          "candidate_source": str(item.get("source", "") or ""),
          "radar_score": float(item.get("radar_score", 0.0) or 0.0),
          "radar_stage": str(item.get("radar_stage", "") or ""),
          "radar_updated_at": str(item.get("radar_updated_at", "") or ""),
          "radar_is_stale": bool(item.get("radar_is_stale", False)),
          "promotion_eligible": bool(item.get("promotion_eligible", False)),
          "promotion_score": float(item.get("promotion_score", 0.0) or 0.0),
          "promotion_snapshot_version": str(
            item.get("promotion_snapshot_version", "") or ""
          ),
          "promotion_model_version": str(
            item.get("promotion_model_version", "") or ""
          ),
          "exit_policy_version": str(item.get("exit_policy_version", "") or ""),
          "board_segment": str(item.get("board_segment", "") or ""),
          "cvar95_loss_pct": float(item.get("cvar95_loss_pct", 0.0) or 0.0),
          "expected_net_return_pct": float(
            item.get("expected_net_return_pct", 0.0) or 0.0
          ),
          "target_position_pct": float(
            item.get("target_position_pct", 0.0) or 0.0
          ),
          "liquidity_cap_amount": float(
            item.get("liquidity_cap_amount", 0.0) or 0.0
          ),
          "high_position_type": str(item.get("high_position_type", "") or ""),
          "draining": bool(item.get("draining", False)),
          "last_arm_version": arm_version,
        }
      )
      if state["draining"]:
        state["entry_eligible"] = False
      states[code] = state

    for code in list(states):
      if code in desired:
        continue
      state = dict(states[code])
      if state.get("pending_entry_intent_id") or state.get("last_entry_volume"):
        state.update(
          {
            "draining": True,
            "entry_eligible": False,
            "eligibility_reason": "REMOVED_WHILE_WORK_REMAINS",
          }
        )
        states[code] = state
      else:
        states.pop(code, None)

    patch = {
      "instrument_states": states,
      "universe_revision": int(self.state.get("universe_revision", 0) or 0) + 1,
    }
    self.state.update(patch)
    return StrategyOutput(
      runtime_state_patch=RuntimeStatePatch(set=patch),
      decision_tags=["radar_candidate_universe_reconciled"],
      trace_payload={
        "reason": "RADAR_CANDIDATES_UNIVERSE_RECONCILED",
        "added": list(event.get("added") or []),
        "removed": list(event.get("removed") or []),
        "instrument_count": len(desired),
      },
    )

  def _assistant_entry_block_reason(
    self,
    input: StrategyInput,
    snapshot: Dict[str, Any],
    state: Dict[str, Any],
  ) -> str:
    rollout = str(
      self.get_parameter("promotion_model_mode", "SHADOW") or "SHADOW"
    ).upper()
    runtime_mode = str(getattr(self.context.mode, "value", self.context.mode)).upper()
    if rollout not in {"PAPER", "LIVE"}:
      return "shadow_mode_hypothetical_only"
    if runtime_mode == "LIVE" and rollout != "LIVE":
      return "live_rollout_gate_not_passed"
    if state.get("draining") or not state.get("entry_eligible"):
      return str(state.get("eligibility_reason") or "candidate_not_eligible")
    if state.get("pending_entry_intent_id"):
      return "entry_pending"
    if int(state.get("confirmed_attempt_count", 0) or 0) >= int(
      self.get_parameter("max_entry_attempts_per_day", 1) or 1
    ):
      return "daily_confirmed_attempt_limit"
    if state.get("radar_is_stale") or self._radar_age_seconds(input, state) > 15:
      return "radar_market_data_stale"
    if self._market_age_seconds(input) > float(
      self.get_parameter("execution_quote_max_age_seconds", 3) or 3
    ):
      return "execution_market_data_stale"
    if _has_active_exit_plan(input.exit_plans, input.instrument_code):
      return "active_exit_plan"
    if _position_volume(input.portfolio_state, input.instrument_code) > 0:
      return "position_exists"
    instrument_states = self._instrument_states()
    open_slots = self._managed_open_position_count(instrument_states)
    open_slots += self._pending_entry_count(instrument_states, input.trade_date)
    if open_slots >= int(self.get_parameter("max_open_positions", 2) or 2):
      return "max_open_positions_reached"
    if _has_open_buy(input.open_orders, input.instrument_code):
      return "open_buy_order_exists"

    risk_caps = dict(input.risk_caps or {})
    if bool(risk_caps.get("kill_switch") or risk_caps.get("kill_switch_active")):
      return "risk_kill_switch"
    if bool(risk_caps.get("only_risk_reduction") or risk_caps.get("only_reduce_position")):
      return "risk_reduction_only"
    if risk_caps.get("allow_buy") is False:
      return "risk_disallow_buy"
    if self._daily_exposure_cap(input) <= 0:
      return "daily_exposure_budget_zero"
    if (
      risk_caps.get("allow_swing_buy") is False
      or risk_caps.get("allow_intraday_swing_buy") is False
    ):
      return "risk_disallow_swing_buy"

    position_profile = dict(input.position_profile or {})
    allow_bucket_buy = dict(position_profile.get("allow_bucket_buy") or {})
    if allow_bucket_buy.get(SWING_BUCKET) is False:
      return "profile_disallow_swing_buy"
    if position_profile.get("allow_swing_buy") is False:
      return "profile_disallow_swing_buy"
    if dict(input.execution_profile or {}).get("allow_swing_buy") is False:
      return "execution_disallow_swing_buy"

    if snapshot["suspended"]:
      return "instrument_suspended"
    if snapshot["is_st"]:
      return "st_stock_blocked"
    if snapshot["delist_risk"]:
      return "delist_risk_blocked"
    if snapshot["limit_up"] <= 0 or snapshot["price"] <= 0:
      return "invalid_limit_quote"
    if bool(self.get_parameter("require_data_quality_ok", True)) and str(
      snapshot["data_quality"]
    ).upper() not in {"", "OK"}:
      return "data_quality_not_ok"
    timestamp = input.timestamp.time()
    if timestamp < _parse_time(self.get_parameter("entry_start_time", "09:30")):
      return "before_entry_window"
    if timestamp > _parse_time(self.get_parameter("entry_end_time", "14:50")):
      return "after_entry_window"
    if bool(self.get_parameter("exclude_one_word_limit_up", True)) and snapshot[
      "one_word_limit_up"
    ]:
      return "one_word_limit_up_blocked"
    if not self._is_entry_band(snapshot):
      return "not_in_entry_band"
    if snapshot["bid1_volume"] < int(self.get_parameter("min_bid1_volume", 0) or 0):
      return "insufficient_bid1_volume"
    if snapshot["amount"] < float(self.get_parameter("min_daily_amount", 0) or 0.0):
      return "insufficient_daily_amount"
    return ""

  def _build_exit_plan(
    self, input: StrategyInput, snapshot: Dict[str, Any], attempt: int
  ) -> ExitPlanTemplate:
    template = super()._build_exit_plan(input, snapshot, attempt)
    payload = template.to_dict()
    payload["source_type"] = "FIRST_BOARD_PROMOTION_V2"
    payload["config_version"] = 2
    payload["metadata"].update(
      {
        "promotion_model_version": self._instrument_states()
        .get(str(input.instrument_code or "").upper(), {})
        .get("promotion_model_version", ""),
        "exit_policy_version": self._instrument_states()
        .get(str(input.instrument_code or "").upper(), {})
        .get("exit_policy_version", ""),
        "t_plus_one_locked": True,
      }
    )
    rules = list(payload["rules"])
    rules.insert(
      0,
      {
        "strategy": "LIMIT_UP_TOUCH",
        "priority": 1100,
        "sizing": {"mode": "ALL_REMAINING"},
        "parameters": {
          "min_holding_trading_days": 2,
          "reason": "SECOND_BOARD_LIMIT_TOUCH",
        },
      },
    )
    cvar95_loss_pct = float(
      self._instrument_states()
      .get(str(input.instrument_code or "").upper(), {})
      .get("cvar95_loss_pct", 0.0)
      or 0.0
    )
    if cvar95_loss_pct > 0:
      rules.insert(
        1,
        {
          "strategy": "HARD_STOP",
          "priority": 1050,
          "sizing": {"mode": "ALL_REMAINING"},
          "parameters": {
            "min_holding_trading_days": 2,
            "stop_loss_pct": -cvar95_loss_pct,
            "reason": "FIRST_BOARD_T1_TAIL_LOSS",
          },
        },
      )
    for rule in rules:
      if rule["strategy"] in {
        "LIMIT_UP_BREAK",
        "TRAILING_PRICE_DRAWDOWN",
        "MAX_HOLDING_DAYS",
      }:
        rule["sizing"] = {"mode": "ALL_REMAINING"}
      if rule["strategy"] == "TRAILING_PRICE_DRAWDOWN":
        rule["parameters"]["reason"] = "FIRST_BOARD_T1_WEAKNESS_EXIT"
        rule["parameters"]["min_holding_trading_days"] = 2
    payload["rules"] = rules
    return ExitPlanTemplate.from_dict(payload)

  @staticmethod
  def _managed_open_position_count(states: Dict[str, Dict[str, Any]]) -> int:
    return sum(
      1
      for state in states.values()
      if int(state.get("last_entry_volume", 0) or 0) > 0
    )

  @staticmethod
  def _pending_entry_count(
    states: Dict[str, Dict[str, Any]], trade_date: str
  ) -> int:
    return sum(
      1
      for state in states.values()
      if str(state.get("trade_date", "") or "") == trade_date
      and bool(state.get("pending_entry_intent_id"))
    )

  def _daily_exposure_cap(self, input: StrategyInput) -> float:
    caps = [float(self.get_parameter("max_daily_exposure_pct", 0.06) or 0.0)]
    risk_cap = dict(input.risk_caps or {}).get("max_new_buy_pct_today")
    if risk_cap is not None:
      try:
        caps.append(max(0.0, float(risk_cap)))
      except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(caps))

  @staticmethod
  def _daily_exposure_pct(
    states: Dict[str, Dict[str, Any]], input: StrategyInput
  ) -> float:
    account = dict((input.portfolio_state or {}).get("account") or {})
    total_asset = float(
      account.get("total_asset")
      or account.get("total_value")
      or account.get("cash_total")
      or 0.0
    )
    used = 0.0
    for state in states.values():
      target_pct = max(0.0, float(state.get("target_position_pct", 0.0) or 0.0))
      if (
        str(state.get("trade_date", "") or "") == input.trade_date
        and state.get("pending_entry_intent_id")
      ):
        used += target_pct
      if str(state.get("last_entry_trade_date", "") or "") != input.trade_date:
        continue
      price = float(state.get("last_entry_price", 0.0) or 0.0)
      volume = int(state.get("last_entry_volume", 0) or 0)
      used += (
        price * volume / total_asset
        if total_asset > 0 and price > 0 and volume > 0
        else target_pct
      )
    return max(0.0, used)

  def _is_entry_band(self, snapshot: Dict[str, Any]) -> bool:
    distance = float(snapshot.get("distance_to_limit_ticks", 0.0) or 0.0)
    return 1e-6 < distance <= int(self.get_parameter("entry_distance_ticks", 1) or 1)

  @staticmethod
  def _radar_age_seconds(input: StrategyInput, state: Dict[str, Any]) -> float:
    raw = str(state.get("radar_updated_at", "") or "")
    if not raw:
      return 0.0
    try:
      updated_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))
      decision_at = input.timestamp
      if updated_at.tzinfo is None and decision_at.tzinfo is not None:
        updated_at = updated_at.replace(tzinfo=decision_at.tzinfo)
      if updated_at.tzinfo is not None and decision_at.tzinfo is None:
        decision_at = decision_at.replace(tzinfo=updated_at.tzinfo)
      return max(0.0, (decision_at - updated_at).total_seconds())
    except (TypeError, ValueError):
      return 16.0

  @staticmethod
  def _market_age_seconds(input: StrategyInput) -> float:
    raw = getattr(input.market_data, "timestamp", None)
    if raw is None:
      return float("inf")
    if not isinstance(raw, datetime):
      try:
        raw = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
      except (TypeError, ValueError):
        return float("inf")
    decision_at = input.timestamp
    if raw.tzinfo is None and decision_at.tzinfo is not None:
      raw = raw.replace(tzinfo=decision_at.tzinfo)
    if raw.tzinfo is not None and decision_at.tzinfo is None:
      decision_at = decision_at.replace(tzinfo=raw.tzinfo)
    return max(0.0, (decision_at - raw).total_seconds())

  def _state_output(
    self,
    code: str,
    states: Dict[str, Dict[str, Any]],
    state: Dict[str, Any],
    reason: str,
    snapshot: Dict[str, Any],
  ) -> StrategyOutput:
    states[code] = state
    patch = {"instrument_states": states}
    self.state.update(patch)
    return StrategyOutput(
      runtime_state_patch=RuntimeStatePatch(set=patch),
      decision_tags=[reason, "no_trade"],
      trace_payload={"reason": reason, "market": snapshot},
    )
