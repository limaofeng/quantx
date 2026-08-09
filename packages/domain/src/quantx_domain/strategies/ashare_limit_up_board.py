"""A-share single-instrument limit-up board entry strategy."""

from __future__ import annotations

from datetime import time
from typing import Any, Dict, Iterable, Optional

from quantx_domain.enums import StrategyCategory, StrategyInstrumentScope
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
  TradeIntentExecutionMode,
  TradeIntentPriority,
)
from quantx_domain.trading.exit_plan import (
  ExitExecutionPolicy,
  ExitPlanTemplate,
  ExitPriceReference,
  ExitRuleSpec,
  ExitRuleType,
  ExitSizingMode,
  ExitSizingPolicy,
  ExitT1Policy,
)

SWING_BUCKET = "swing"
ENTRY_STYLE = "LIMIT_UP_BOARD"
TERMINAL_EXIT_PLAN_STATUSES = {"COMPLETED", "CANCELLED"}


class AshareLimitUpBoardStrategy(StrategyBase):
  """Create one audited board-entry intent and delegate exits to ExitPlanBook."""

  CATEGORY = StrategyCategory.TREND_FOLLOWING
  RISK_LEVEL = "high"
  TAGS = ["A股", "打板", "涨停", "超短线", "T+1", "单标的"]
  INSTRUMENT_SCOPE = StrategyInstrumentScope.SINGLE

  @property
  def name(self) -> str:
    return "A股单标的打板策略"

  @property
  def version(self) -> str:
    return "1.1.0"

  @property
  def description(self) -> str:
    return "临近涨停时生成受风控约束的单次买入意图，成交后由统一退出计划管理破板、回撤与持有期退出。"

  @classmethod
  def get_parameter_schema(cls) -> ParameterSchema:
    return ParameterSchema(
      type="object",
      properties={
        "target_position_pct": ParameterProperty(
          type="number",
          minimum=0.005,
          maximum=0.30,
          default=0.05,
          title="目标仓位",
          description="打板入场的目标总资产仓位，最终数量仍由 OrderSizer 和风控裁决。",
          group="entry",
          unit="ratio",
          step=0.005,
        ),
        "entry_distance_ticks": ParameterProperty(
          type="integer",
          minimum=1,
          maximum=10,
          default=1,
          title="触板距离",
          description="最新价距离涨停价不超过多少个最小价位时允许生成入场意图。",
          group="entry",
          unit="ticks",
        ),
        "entry_start_time": ParameterProperty(
          type="string",
          default="09:30",
          title="最早入场时间",
          group="entry",
        ),
        "entry_end_time": ParameterProperty(
          type="string",
          default="14:50",
          title="最晚入场时间",
          group="entry",
        ),
        "min_bid1_volume": ParameterProperty(
          type="integer",
          minimum=0,
          maximum=1000000000,
          default=0,
          title="最小买一量",
          description="设为 0 时不要求盘口买一量。",
          group="entry",
          unit="shares",
        ),
        "min_daily_amount": ParameterProperty(
          type="number",
          minimum=0,
          maximum=100000000000,
          default=0,
          title="最小当日成交额",
          description="设为 0 时不使用累计成交额过滤。",
          group="entry",
          unit="CNY",
        ),
        "min_context_score": ParameterProperty(
          type="number",
          minimum=-1,
          maximum=1,
          default=-1,
          title="最低环境评分",
          description="环境评分低于该值时禁止入场。",
          group="risk",
          step=0.05,
        ),
        "exclude_one_word_limit_up": ParameterProperty(
          type="boolean",
          default=True,
          title="排除一字板",
          group="risk",
        ),
        "require_data_quality_ok": ParameterProperty(
          type="boolean",
          default=True,
          title="要求数据完整",
          description="开启后，非 OK 数据质量只允许保守观望。",
          group="risk",
        ),
        "backtest_limit_rate": ParameterProperty(
          type="number",
          minimum=0,
          maximum=0.30,
          default=0,
          title="回测涨跌停比例",
          description="仅回测使用；旧行情缺涨跌停价时按前收盘价推导。0 表示禁止推导。",
          group="data",
          unit="ratio",
          step=0.01,
        ),
        "max_entry_attempts_per_day": ParameterProperty(
          type="integer",
          minimum=1,
          maximum=5,
          default=1,
          title="每日最多尝试次数",
          group="risk",
        ),
        "entry_execution_mode": ParameterProperty(
          type="string",
          enum=["AUTO", "MANUAL_CONFIRM"],
          default="MANUAL_CONFIRM",
          title="入场授权",
          description="默认需要人工确认；AUTO 仍必须通过统一风控和实盘开关。",
          group="execution",
        ),
        "approval_ttl_ms": ParameterProperty(
          type="integer",
          minimum=1000,
          maximum=300000,
          default=15000,
          title="人工确认有效期",
          group="execution",
          unit="ms",
        ),
        "entry_order_ttl_ms": ParameterProperty(
          type="integer",
          minimum=1000,
          maximum=300000,
          default=15000,
          title="入场委托有效期",
          description="确认后委托超过该时长仍未成交时请求撤单，防止追入已经失效的板。",
          group="execution",
          unit="ms",
        ),
        "max_price_deviation_bps": ParameterProperty(
          type="number",
          minimum=0,
          maximum=500,
          default=20,
          title="最大价格偏离",
          group="execution",
          unit="bps",
        ),
        "auto_exit_authorized": ParameterProperty(
          type="boolean",
          default=False,
          title="授权自动退出",
          description="实盘未显式授权时，退出意图自动降级为人工确认。",
          group="exit",
        ),
        "exit_limit_break_ticks": ParameterProperty(
          type="integer",
          minimum=1,
          maximum=20,
          default=1,
          title="破板确认价位",
          group="exit",
          unit="ticks",
        ),
        "exit_min_seal_seconds": ParameterProperty(
          type="number",
          minimum=0,
          maximum=600,
          default=3,
          title="封板确认时长",
          group="exit",
          unit="seconds",
        ),
        "exit_trailing_arm_profit_pct": ParameterProperty(
          type="number",
          minimum=0,
          maximum=30,
          default=2,
          title="回撤退出启动收益",
          group="exit",
          unit="percent",
          step=0.1,
        ),
        "exit_trailing_drawdown_pct": ParameterProperty(
          type="number",
          minimum=0.1,
          maximum=20,
          default=3,
          title="峰值回撤比例",
          group="exit",
          unit="percent",
          step=0.1,
        ),
        "exit_trailing_percent": ParameterProperty(
          type="number",
          minimum=1,
          maximum=100,
          default=50,
          title="回撤退出比例",
          group="exit",
          unit="percent",
        ),
        "max_holding_trading_days": ParameterProperty(
          type="integer",
          minimum=2,
          maximum=20,
          default=2,
          title="最长持有交易日",
          group="exit",
          unit="days",
        ),
        "max_holding_exit_time": ParameterProperty(
          type="string",
          default="14:50",
          title="持有期退出时点",
          group="exit",
        ),
        "exit_max_slippage_bps": ParameterProperty(
          type="number",
          minimum=0,
          maximum=1000,
          default=50,
          title="退出最大滑点",
          group="exit",
          unit="bps",
        ),
        "auto_approve_manual_intents": ParameterProperty(
          type="boolean",
          default=True,
          title="回测自动确认信号",
          description="只在回测模式生效，使人工确认信号进入同一订单与成交状态流；模拟盘和实盘不会自动确认。",
          group="backtest",
        ),
        "initial_capital": ParameterProperty(
          type="number",
          minimum=10000,
          maximum=1000000000,
          default=1000000,
          title="回测初始资金",
          group="backtest",
          unit="CNY",
        ),
        "commission_rate": ParameterProperty(
          type="number",
          minimum=0,
          maximum=0.01,
          default=0.0003,
          title="佣金比例",
          group="backtest",
          unit="ratio",
          step=0.00001,
        ),
        "minimum_commission": ParameterProperty(
          type="number",
          minimum=0,
          maximum=1000,
          default=5,
          title="最低佣金",
          group="backtest",
          unit="CNY",
        ),
        "stamp_tax_rate": ParameterProperty(
          type="number",
          minimum=0,
          maximum=0.01,
          default=0.0005,
          title="卖出印花税率",
          group="backtest",
          unit="ratio",
          step=0.00001,
        ),
        "transfer_fee_rate": ParameterProperty(
          type="number",
          minimum=0,
          maximum=0.01,
          default=0.00001,
          title="过户费率",
          group="backtest",
          unit="ratio",
          step=0.000001,
        ),
        "slippage_rate": ParameterProperty(
          type="number",
          minimum=0,
          maximum=0.05,
          default=0.0001,
          title="基础滑点比例",
          group="backtest",
          unit="ratio",
          step=0.00001,
        ),
        "participation_cap_pct": ParameterProperty(
          type="number",
          minimum=0.001,
          maximum=1,
          default=0.05,
          title="成交量参与率上限",
          description="缺少完整盘口深度时，单次模拟成交不超过当前行情成交量的该比例。",
          group="backtest",
          unit="ratio",
          step=0.005,
        ),
        "book_depth_participation_pct": ParameterProperty(
          type="number",
          minimum=0.001,
          maximum=1,
          default=0.25,
          title="盘口深度参与率",
          description="有盘口档位时，模拟成交最多占可执行盘口挂单量的该比例。",
          group="backtest",
          unit="ratio",
          step=0.05,
        ),
        "strict_market_data": ParameterProperty(
          type="boolean",
          default=True,
          title="严格行情完整性",
          group="backtest",
        ),
        "strict_limit_data": ParameterProperty(
          type="boolean",
          default=True,
          title="严格涨跌停数据",
          group="backtest",
        ),
      },
      additionalProperties=False,
    )

  @classmethod
  def get_state_schema(cls) -> StateSchema:
    return StateSchema(
      type="object",
      properties={
        "trade_date": StateProperty(type="string", default=""),
        "attempt_count": StateProperty(type="integer", default=0),
        "pending_entry_intent_id": StateProperty(type="string", default=""),
        "pending_entry_status": StateProperty(type="string", default=""),
        "last_signal_at_ms": StateProperty(type="integer", default=0),
        "last_signal_price": StateProperty(type="number", default=0.0),
        "last_entry_trade_date": StateProperty(type="string", default=""),
        "last_entry_price": StateProperty(type="number", default=0.0),
        "last_entry_volume": StateProperty(type="integer", default=0),
      },
    )

  @classmethod
  def get_data_requirements(cls) -> Dict[str, Any]:
    return {"use_tick_data": True, "periods": ["1d"]}

  async def on_init(self) -> None:
    self._validate_parameters()

  async def on_stop(self) -> None:
    return None

  def pending_manual_intent_ids(self) -> list[str]:
    intent_id = str(self.state.get("pending_entry_intent_id", "") or "")
    status = str(self.state.get("pending_entry_status", "") or "").upper()
    if intent_id and status == "AWAITING_APPROVAL":
      return [intent_id]
    return []

  async def step(self, input: StrategyInput) -> StrategyOutput:
    if input.cadence != StrategyCadence.TICK:
      return StrategyOutput()
    if not self.is_running:
      return self._no_trade("strategy_not_running")
    if not self._is_bound_instrument(input.instrument_code):
      return self._no_trade(
        "instrument_mismatch",
        {"bound_instruments": list(self.context.instruments or [])},
      )
    if not self._is_supported_instrument(input.instrument_code):
      return self._no_trade("unsupported_instrument")

    patch_values = self._roll_trade_date(input.trade_date)
    snapshot = self._market_snapshot(input)
    block_reason = self._entry_block_reason(input, snapshot)
    if block_reason:
      return self._no_trade(
        block_reason,
        snapshot,
        runtime_state_patch=self._patch(patch_values),
      )

    target_position_pct = self._target_position_pct(input)
    if target_position_pct <= 0:
      return self._no_trade(
        "position_cap_zero",
        snapshot,
        runtime_state_patch=self._patch(patch_values),
      )

    attempt = int(self.state.get("attempt_count", 0) or 0) + 1
    execution_mode = TradeIntentExecutionMode(
      str(self.get_parameter("entry_execution_mode", "MANUAL_CONFIRM")).upper()
    )
    plan = self._build_exit_plan(input, snapshot, attempt)
    intent = TradeIntent(
      strategy_id=input.strategy_id,
      run_id=input.run_id,
      instrument_code=input.instrument_code,
      direction=TradeIntentDirection.BUY,
      bucket=SWING_BUCKET,
      reason="limit_up_board_entry",
      priority=TradeIntentPriority.HIGH,
      confidence=self._entry_confidence(snapshot),
      target_position_pct=target_position_pct,
      limit_price_hint=float(snapshot["limit_up"]),
      execution_mode=execution_mode,
      approval_ttl_ms=int(self.get_parameter("approval_ttl_ms", 15000) or 15000),
      max_price_deviation_bps=float(
        self.get_parameter("max_price_deviation_bps", 20) or 0.0
      ),
      expiry_policy={
        "type": "TTL_MS",
        "expire_at_ms": input.decision_time_ms
        + int(self.get_parameter("approval_ttl_ms", 15000) or 15000),
      },
      trace_id=input.trace_id,
      metadata={
        "entry_style": ENTRY_STYLE,
        "signal_price": float(snapshot["price"]),
        "limit_up": float(snapshot["limit_up"]),
        "distance_to_limit_ticks": float(snapshot["distance_to_limit_ticks"]),
        "target_position_pct": target_position_pct,
        "attempt": attempt,
        "price_type": "LIMIT",
        "order_ttl_ms": int(
          self.get_parameter("entry_order_ttl_ms", 15000) or 15000
        ),
        "exit_plan_template": plan.to_dict(),
      },
    )

    patch_values.update(
      {
        "attempt_count": attempt,
        "pending_entry_intent_id": intent.intent_id,
        "pending_entry_status": (
          "AWAITING_APPROVAL"
          if execution_mode == TradeIntentExecutionMode.MANUAL_CONFIRM
          else "PENDING"
        ),
        "last_signal_at_ms": input.decision_time_ms,
        "last_signal_price": float(snapshot["price"]),
      }
    )
    self.state.update(patch_values)
    return StrategyOutput(
      trade_intents=[intent],
      runtime_state_patch=RuntimeStatePatch(set=patch_values),
      decision_tags=["limit_up_board_entry", "entry_intent_created"],
      trace_payload={
        "reason": "limit_up_board_entry",
        "market": snapshot,
        "target_position_pct": target_position_pct,
        "attempt": attempt,
        "exit_plan_id": plan.plan_id,
      },
    )

  async def warmup(self, input: StrategyInput) -> None:
    return None

  async def on_order(self, event: OrderStateEvent) -> Optional[RuntimeStatePatch]:
    pending_id = str(self.state.get("pending_entry_intent_id", "") or "")
    intent_id = str(event.metadata.get("intent_id", "") or "")
    if not pending_id or intent_id != pending_id:
      return None
    status = str(event.status or "").upper()
    updates: Dict[str, Any] = {"pending_entry_status": status}
    if status in {"REJECTED", "CANCELLED", "EXPIRED"}:
      updates.update(
        {
          "pending_entry_intent_id": "",
          "pending_entry_status": "",
        }
      )
    self.state.update(updates)
    return RuntimeStatePatch(set=updates)

  async def on_trade(self, event: TradeExecutionEvent) -> Optional[RuntimeStatePatch]:
    if str(event.trade_type or "").upper() != "BUY":
      return None
    if event.instrument_code not in set(self.context.instruments or []):
      return None
    if event.volume <= 0 or event.price <= 0:
      return None
    trade_date = (
      event.trade_time.date().isoformat()
      if event.trade_time is not None
      else str(self.state.get("trade_date", "") or "")
    )
    updates = {
      "pending_entry_intent_id": "",
      "pending_entry_status": "",
      "last_entry_trade_date": trade_date,
      "last_entry_price": float(event.price),
      "last_entry_volume": int(event.volume),
    }
    self.state.update(updates)
    return RuntimeStatePatch(set=updates)

  def _validate_parameters(self) -> None:
    self.validate_configuration(dict(self.context.parameters or {}))

  @classmethod
  def validate_configuration(cls, parameters: Dict[str, Any]) -> None:
    start = _parse_time(parameters.get("entry_start_time", "09:30"))
    end = _parse_time(parameters.get("entry_end_time", "14:50"))
    if start > end:
      raise ValueError("entry_start_time must not be later than entry_end_time")
    _parse_time(parameters.get("max_holding_exit_time", "14:50"))

  def _roll_trade_date(self, trade_date: str) -> Dict[str, Any]:
    current = str(self.state.get("trade_date", "") or "")
    if current == trade_date:
      return {}
    updates = {
      "trade_date": trade_date,
      "attempt_count": 0,
    }
    self.state.update(updates)
    return updates

  def _entry_block_reason(self, input: StrategyInput, snapshot: Dict[str, Any]) -> str:
    if self.state.get("pending_entry_intent_id"):
      return "entry_pending"
    if int(self.state.get("attempt_count", 0) or 0) >= int(
      self.get_parameter("max_entry_attempts_per_day", 1) or 1
    ):
      return "daily_attempt_limit"
    if _has_active_exit_plan(input.exit_plans, input.instrument_code):
      return "active_exit_plan"
    if _position_volume(input.portfolio_state, input.instrument_code) > 0:
      return "position_exists"
    if _has_open_buy(input.open_orders, input.instrument_code):
      return "open_buy_order_exists"

    risk_caps = dict(input.risk_caps or {})
    if bool(risk_caps.get("kill_switch") or risk_caps.get("kill_switch_active")):
      return "risk_kill_switch"
    if bool(
      risk_caps.get("only_risk_reduction") or risk_caps.get("only_reduce_position")
    ):
      return "risk_reduction_only"
    if risk_caps.get("allow_buy") is False:
      return "risk_disallow_buy"
    if (
      risk_caps.get("allow_swing_buy") is False
      or risk_caps.get("allow_intraday_swing_buy") is False
    ):
      return "risk_disallow_swing_buy"
    if _explicit_zero(
      risk_caps.get("max_buy_amount_cny")
      if risk_caps.get("max_buy_amount_cny") is not None
      else risk_caps.get("max_new_buy_amount_today")
    ):
      return "risk_buy_amount_zero"

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
    if snapshot["limit_up"] <= 0:
      return "missing_limit_up"
    if snapshot["price"] <= 0:
      return "invalid_price"
    if bool(self.get_parameter("require_data_quality_ok", True)) and str(
      snapshot["data_quality"]
    ).upper() not in {"", "OK"}:
      return "data_quality_not_ok"
    if snapshot["context_score"] < float(self.get_parameter("min_context_score", -1)):
      return "weak_market_context"

    timestamp = input.timestamp.time()
    if timestamp < _parse_time(self.get_parameter("entry_start_time", "09:30")):
      return "before_entry_window"
    if timestamp > _parse_time(self.get_parameter("entry_end_time", "14:50")):
      return "after_entry_window"
    if (
      bool(self.get_parameter("exclude_one_word_limit_up", True))
      and snapshot["one_word_limit_up"]
    ):
      return "one_word_limit_up_blocked"
    if snapshot["distance_to_limit_ticks"] < -1e-6:
      return "price_above_limit_up"
    if snapshot["distance_to_limit_ticks"] <= 1e-6:
      return "limit_up_already_sealed"
    if snapshot["distance_to_limit_ticks"] > int(
      self.get_parameter("entry_distance_ticks", 1) or 0
    ):
      return "not_near_limit_up"
    if snapshot["bid1_volume"] < int(self.get_parameter("min_bid1_volume", 0) or 0):
      return "insufficient_bid1_volume"
    if snapshot["amount"] < float(self.get_parameter("min_daily_amount", 0) or 0.0):
      return "insufficient_daily_amount"
    return ""

  def _market_snapshot(self, input: StrategyInput) -> Dict[str, Any]:
    market_data = input.market_data
    event = input.event
    market_context = dict(input.market_context or {})
    instrument = dict(market_context.get("instrument_master") or {})
    price = _float(
      _get(market_data, "price")
      or _get(event, "last_price")
      or _get(market_data, "close")
      or _get(event, "close")
    )
    limit_up = _float(
      _get(market_data, "limit_up")
      or market_context.get("limit_up")
      or instrument.get("limit_up")
      or _get(event, "up_stop_price")
      or _get(event, "limit_up")
    )
    price_tick = max(
      _float(
        _get(market_data, "price_tick")
        or instrument.get("price_tick")
        or _get(event, "price_tick")
        or 0.01
      ),
      1e-8,
    )
    open_price = _float(_get(market_data, "open") or _get(event, "open"))
    high = _float(_get(market_data, "high") or _get(event, "high"))
    low = _float(_get(market_data, "low") or _get(event, "low"))
    bid_volumes = list(
      _get(market_data, "bid_vol", None) or _get(event, "bid_vol", []) or []
    )
    amount = _float(_get(market_data, "amount") or _get(event, "amount"))
    one_word_limit_up = bool(
      limit_up > 0
      and open_price > 0
      and high > 0
      and low > 0
      and all(
        abs(value - limit_up) <= price_tick / 2 for value in (open_price, high, low)
      )
    )
    return {
      "price": price,
      "limit_up": limit_up,
      "price_tick": price_tick,
      "source": str(_get(market_data, "source", "") or ""),
      "distance_to_limit_ticks": (
        (limit_up - price) / price_tick if limit_up > 0 and price > 0 else 0.0
      ),
      "open": open_price,
      "high": high,
      "low": low,
      "amount": amount,
      "bid1_volume": int(bid_volumes[0] or 0) if bid_volumes else 0,
      "one_word_limit_up": one_word_limit_up,
      "suspended": bool(
        _get(market_data, "suspended", False)
        or instrument.get("suspended")
        or market_context.get("suspended")
      ),
      "is_st": bool(instrument.get("is_st") or market_context.get("is_st")),
      "delist_risk": bool(
        instrument.get("delist_risk") or market_context.get("delist_risk")
      ),
      "data_quality": str(
        market_context.get("data_quality") or instrument.get("data_quality") or "OK"
      ).upper(),
      "context_score": _float(market_context.get("context_score")),
    }

  def _target_position_pct(self, input: StrategyInput) -> float:
    candidates = [float(self.get_parameter("target_position_pct", 0.05) or 0.0)]
    risk_cap = _optional_positive_float(
      dict(input.risk_caps or {}).get(
        "max_position_pct_cap",
        dict(input.risk_caps or {}).get("max_position_pct"),
      )
    )
    if risk_cap is not None:
      candidates.append(risk_cap)
    profile = dict(input.position_profile or {})
    for value in (
      profile.get("max_position_pct"),
      profile.get("swing_max_pct"),
      dict(dict(profile.get("bucket_caps") or {}).get(SWING_BUCKET) or {}).get(
        "max_pct"
      ),
    ):
      cap = _optional_positive_float(value)
      if cap is not None:
        candidates.append(cap)
    return max(0.0, min(candidates))

  def _build_exit_plan(
    self, input: StrategyInput, snapshot: Dict[str, Any], attempt: int
  ) -> ExitPlanTemplate:
    plan_id = (
      f"limit-up-board:{input.run_id}:{input.instrument_code}:"
      f"{input.trade_date}:{attempt}"
    )
    return ExitPlanTemplate(
      plan_id=plan_id,
      source_type="LIMIT_UP_BOARD",
      source_id=plan_id,
      account_id=_account_id(input.portfolio_state),
      instrument_code=input.instrument_code,
      bucket=SWING_BUCKET,
      strategy_id=input.strategy_id,
      run_id=input.run_id,
      config_version=1,
      rules=[
        ExitRuleSpec(
          strategy=ExitRuleType.LIMIT_UP_BREAK,
          priority=1000,
          sizing=ExitSizingPolicy(mode=ExitSizingMode.ALL_REMAINING),
          parameters={
            "break_ticks": int(self.get_parameter("exit_limit_break_ticks", 1) or 1),
            "min_seal_seconds": float(
              self.get_parameter("exit_min_seal_seconds", 3) or 0.0
            ),
            "min_holding_trading_days": 2,
            "reason": "LIMIT_UP_BREAK",
          },
        ),
        ExitRuleSpec(
          strategy=ExitRuleType.TRAILING_PRICE_DRAWDOWN,
          priority=700,
          sizing=ExitSizingPolicy(
            mode=ExitSizingMode.PERCENT_REMAINING,
            value=float(self.get_parameter("exit_trailing_percent", 50) or 50.0),
          ),
          parameters={
            "arm_profit_pct": float(
              self.get_parameter("exit_trailing_arm_profit_pct", 2) or 0.0
            ),
            "drawdown_pct": float(
              self.get_parameter("exit_trailing_drawdown_pct", 3) or 3.0
            ),
            "reason": "BOARD_TRAILING_DRAWDOWN",
          },
          once=True,
        ),
        ExitRuleSpec(
          strategy=ExitRuleType.MAX_HOLDING_DAYS,
          priority=600,
          sizing=ExitSizingPolicy(mode=ExitSizingMode.ALL_REMAINING),
          parameters={
            "max_holding_trading_days": int(
              self.get_parameter("max_holding_trading_days", 2) or 2
            ),
            "exit_time": str(
              self.get_parameter("max_holding_exit_time", "14:50") or "14:50"
            ),
            "reason": "BOARD_MAX_HOLDING_DAYS",
          },
        ),
      ],
      t1_policy=ExitT1Policy.WAIT_UNTIL_SELLABLE,
      execution=ExitExecutionPolicy(
        price_reference=ExitPriceReference.BID,
        price_type="LIMIT",
        protected_limit=True,
        max_slippage_bps=float(self.get_parameter("exit_max_slippage_bps", 50) or 0.0),
        urgency="LIMIT_UP_BOARD_EXIT",
        execution_mode="AUTO",
      ),
      metadata={
        "entry_style": ENTRY_STYLE,
        "entry_trade_date": input.trade_date,
        "signal_price": float(snapshot["price"]),
        "entry_limit_up": float(snapshot["limit_up"]),
      },
      auto_exit_authorized=bool(self.get_parameter("auto_exit_authorized", False)),
    )

  @staticmethod
  def _entry_confidence(snapshot: Dict[str, Any]) -> float:
    distance = max(0.0, float(snapshot["distance_to_limit_ticks"]))
    distance_score = max(0.0, 1.0 - min(distance, 5.0) / 5.0)
    context_score = max(-1.0, min(1.0, float(snapshot["context_score"])))
    return round(
      max(0.0, min(1.0, 0.7 + 0.2 * distance_score + 0.1 * context_score)), 4
    )

  @staticmethod
  def _patch(values: Dict[str, Any]) -> Optional[RuntimeStatePatch]:
    return RuntimeStatePatch(set=values) if values else None

  @staticmethod
  def _no_trade(
    reason: str,
    metrics: Optional[Dict[str, Any]] = None,
    *,
    runtime_state_patch: Optional[RuntimeStatePatch] = None,
  ) -> StrategyOutput:
    return StrategyOutput(
      runtime_state_patch=runtime_state_patch,
      decision_tags=[reason, "no_trade"],
      trace_payload={"reason": reason, "market": dict(metrics or {})},
    )

  def _is_bound_instrument(self, instrument_code: str) -> bool:
    bound = list(self.context.instruments or [])
    return len(bound) == 1 and instrument_code == bound[0]

  @staticmethod
  def _is_supported_instrument(instrument_code: str) -> bool:
    code = str(instrument_code or "").upper()
    return code.endswith((".SH", ".SZ"))


def _parse_time(value: Any) -> time:
  try:
    return time.fromisoformat(str(value or ""))
  except ValueError as exc:
    raise ValueError(f"invalid trading time: {value}") from exc


def _get(source: Any, key: str, default: Any = None) -> Any:
  if source is None:
    return default
  if isinstance(source, dict):
    return source.get(key, default)
  return getattr(source, key, default)


def _float(value: Any) -> float:
  try:
    return float(value or 0.0)
  except (TypeError, ValueError):
    return 0.0


def _optional_positive_float(value: Any) -> Optional[float]:
  if value is None:
    return None
  parsed = _float(value)
  return max(0.0, parsed)


def _explicit_zero(value: Any) -> bool:
  if value is None:
    return False
  return _float(value) <= 0


def _has_active_exit_plan(
  plans: Iterable[Dict[str, Any]], instrument_code: str
) -> bool:
  for plan in plans or []:
    raw = dict(plan or {})
    template = dict(raw.get("template") or {})
    if template.get("instrument_code") != instrument_code:
      continue
    if str(raw.get("status", "") or "").upper() in TERMINAL_EXIT_PLAN_STATUSES:
      continue
    if int(raw.get("remaining_volume", 0) or 0) > 0:
      return True
  return False


def _position_volume(portfolio_state: Dict[str, Any], instrument_code: str) -> int:
  state = dict(portfolio_state or {})
  positions = state.get("positions", state.get("position", {}))
  if isinstance(positions, dict):
    position = positions.get(instrument_code)
    if position is None and positions.get("instrument_code") == instrument_code:
      position = positions
    return int(
      _get(
        position,
        "total_volume",
        _get(position, "long_volume", _get(position, "volume", 0)),
      )
      or 0
    )
  if isinstance(positions, list):
    for position in positions:
      code = _get(position, "instrument_code", _get(position, "stock_code", ""))
      if code == instrument_code:
        return int(
          _get(
            position,
            "total_volume",
            _get(position, "long_volume", _get(position, "volume", 0)),
          )
          or 0
        )
  return 0


def _has_open_buy(open_orders: Iterable[Any], instrument_code: str) -> bool:
  for order in open_orders or []:
    code = _get(order, "instrument_code", _get(order, "stock_code", ""))
    direction = str(
      _get(order, "direction", _get(order, "side", _get(order, "order_type", ""))) or ""
    ).upper()
    status = str(_get(order, "status", "") or "").upper()
    if (
      code == instrument_code
      and "BUY" in direction
      and status not in {"REJECTED", "CANCELLED", "EXPIRED", "FILLED"}
    ):
      return True
  return False


def _account_id(portfolio_state: Dict[str, Any]) -> str:
  account = dict(dict(portfolio_state or {}).get("account") or {})
  return str(account.get("account_id") or account.get("id") or "")
