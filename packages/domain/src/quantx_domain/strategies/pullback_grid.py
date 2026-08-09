"""
Pullback Grid (回撤网格) 策略
结合趋势跟踪与网格交易，利用 Tick 级别回拉确认优化入场。
专为 A 股市场（T+1, 多头市场）优化。
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from quantx_domain.enums import StrategyCategory, StrategyInstrumentScope
from quantx_domain.grid_book import (
    GRID_BOOK_CUSTOM_STATE_KEY,
    GRID_BOOK_MODEL_VERSION,
    INVENTORY_MODEL,
    RELEASE_RULE,
    SELL_EMPTY_BEHAVIOR,
    build_initial_swing_lots,
    normalize_inventory_lot,
    normalize_release_event,
    normalize_status,
    now_iso,
)
from quantx_domain.indicators import ATR, EMA
from quantx_domain.market import KLine, Tick
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


@dataclass
class GridLevel:
    """网格层级状态"""
    level_index: int        # 层级索引 (0-based, 0离基准价最近)
    side: str               # BUY / SELL
    trigger_price: float    # 触发价格
    volume: int             # 计划成交数量
    grid_id: Optional[str] = None
    amount: float = 0.0
    pct_from_base: Optional[float] = None
    expected_profit: Optional[float] = None
    enabled: bool = True
    status: str = "PLANNED"
    role: str = "BUY_SLOT"
    cycle_count: int = 0
    available_inventory_shares: int = 0
    reserved_inventory_shares: int = 0
    waiting_reason: Optional[str] = None

    is_filled: bool = False  # 是否已成交
    entry_price: float = 0.0  # 实际成交价格
    entry_time: Optional[datetime] = None
    is_pending: bool = False
    order_id: Optional[str] = None

    # Tick 回拉确认状态
    is_monitoring: bool = False  # 是否正在监控回拉
    lowest_price_since_touch: float = float('inf')  # 触网后的最低价
    touch_time: Optional[datetime] = None

    # 成交状态（部分成交感知）
    filled_volume: int = 0
    pending_volume: int = 0
    last_intent_id: Optional[str] = None
    last_trace_id: Optional[str] = None
    last_intent_bar_key: Optional[str] = None
    last_intent_source: Optional[str] = None
    last_intent_side: Optional[str] = None
    last_rejected_side: Optional[str] = None
    last_rejected_date: Optional[str] = None
    last_rejected_reason: Optional[str] = None
    is_day_locked: bool = False
    sell_lock_reason: Optional[str] = None
    sell_last_filled_date: Optional[str] = None
    reason: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class GridInventoryLot:
    """Inventory lot contributed by a filled buy slot or initial swing bucket."""
    lot_id: str
    source_level_id: Optional[str]
    source_level_index: Optional[int]
    source: str
    bucket: str
    entry_price: float
    original_shares: int
    remaining_shares: int
    reserved_shares: int = 0
    reserved_for_level_id: Optional[str] = None
    reserved_order_id: Optional[str] = None
    target_sell_level_id: Optional[str] = None
    target_sell_level_index: Optional[int] = None
    entry_date: Optional[str] = None
    status: str = "OPEN"
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)


@dataclass
class GridReleaseEvent:
    """Audit record for inventory released by a sell waterline."""
    event_id: str
    sell_level_id: Optional[str]
    sell_level_index: Optional[int]
    released_level_id: Optional[str]
    released_level_index: Optional[int]
    lot_ids: List[str]
    order_id: Optional[str]
    intent_id: Optional[str]
    trade_id: Optional[str]
    price: float
    shares: int
    created_at: str = field(default_factory=now_iso)


class PullbackGridStrategy(StrategyBase):
    """
    Pullback Grid 回撤网格策略

    核心逻辑：
    1. 识别上升趋势 (Fast EMA > Slow EMA)
    2. 按 ATR 动态生成回撤买入网格，或兼容外部传入网格计划
    3. Tick 级回拉确认：买入触网后需反弹确认
    4. 卖出只由网格簿 SELL_WATERLINE 触发，买入格不做隐藏止盈止损
    """

    # 策略基本信息
    CATEGORY = StrategyCategory.TREND_FOLLOWING
    RISK_LEVEL = "medium"
    TAGS = ["趋势网格", "网格", "回撤买入", "A股"]
    INSTRUMENT_SCOPE = StrategyInstrumentScope.SINGLE

    @property
    def name(self) -> str:
        return "Pullback Grid 策略"

    @property
    def version(self) -> str:
        return "1.2.0"

    @property
    def description(self) -> str:
        return "在上升趋势回撤中按网格分批建仓，Tick 反弹确认买入，卖出由网格簿卖出水位线触发。"

    @classmethod
    def get_parameter_schema(cls) -> "ParameterSchema":
        return ParameterSchema(
            type="object",
            properties={
                # --- 趋势判断 ---
                "trend_ema_period": ParameterProperty(
                    type="integer", default=60, min=10, max=200,
                    title="趋势 EMA 周期", description="判断主趋势的均线周期 (如 60 日均线)", group="趋势判断"
                ),
                "fast_ema_period": ParameterProperty(
                    type="integer", default=20, min=5, max=100,
                    title="快速 EMA 周期", description="判断短期方向的均线周期", group="趋势判断"
                ),
                "atr_period": ParameterProperty(
                    type="integer", default=14, min=5, max=50,
                    title="ATR 周期", description="计算波动率的周期", group="网格设置"
                ),
                "grid_count": ParameterProperty(
                    type="integer", default=3, min=1, max=10,
                    title="网格层数", description="没有外部网格时向下铺设多少层买入网格", group="网格设置"
                ),
                "grid_atr_multiplier": ParameterProperty(
                    type="number", default=1.0, min=0.1, max=5.0, step=0.1,
                    title="网格间距 (ATR倍数)", description="每层动态网格之间的 ATR 距离", group="网格设置"
                ),

                # --- 网格计划 ---
                "base_price": ParameterProperty(
                    type="number", default=0,
                    title="网格基准价", description="前端生成网格时使用的基准价（可选）", group="网格计划"
                ),
                "grid_levels": ParameterProperty(
                    type="array",
                    title="网格明细",
                    description="前端生成的网格层级列表（BUY/SELL）",
                    group="网格计划",
                    items=ParameterProperty(
                        type="object",
                        properties={
                            "id": ParameterProperty(type="string", title="ID"),
                            "levelIndex": ParameterProperty(type="integer", title="层级索引"),
                            "side": ParameterProperty(
                                type="string", enum=["BUY", "SELL"], title="方向"
                            ),
                            "price": ParameterProperty(type="number", title="价格"),
                            "shares": ParameterProperty(type="integer", title="股数"),
                            "amount": ParameterProperty(type="number", title="金额"),
                            "pctFromBase": ParameterProperty(type="number", title="偏离基准"),
                            "expectedProfit": ParameterProperty(type="number", title="预期收益"),
                        },
                        required=["side", "price", "shares"],
                    ),
                ),

                # --- 入场确认 ---
                "pullback_confirm_pct": ParameterProperty(
                    type="number", default=0.002, min=0.000, max=0.02, step=0.001,
                    title="反弹确认幅度", description="触网后需反弹多少才买入 (例如 0.002 = 0.2%)", group="入场确认"
                ),

                # --- 资金管理 ---
                "position_per_grid": ParameterProperty(
                    type="integer", default=1000, min=100, step=100,
                    title="单格仓位", description="动态网格每层期望买入股数", group="资金管理"
                ),
                "max_total_position": ParameterProperty(
                    type="integer", default=5000, min=100,
                    title="最大总仓位", description="策略内网格期望持仓上限", group="资金管理"
                ),

                # --- 区间保护 ---
                "price_ceiling": ParameterProperty(
                    type="number", default=-1,
                    title="价格上限", description="高于此价格不新开仓 (-1 为不限)", group="区间控制"
                ),
                "price_floor": ParameterProperty(
                    type="number", default=-1,
                    title="价格下限", description="低于此价格停止生成买入网格 (-1 为不限)", group="区间控制"
                ),
            },
            required=["trend_ema_period"],
        )

    @classmethod
    def get_data_requirements(cls) -> Dict[str, Any]:
        return {"use_tick_data": True, "periods": ["1m", "1d"]}

    async def on_init(self) -> None:
        """策略初始化"""
        # 读取参数（兼容前端命名）
        self.trend_ema_period = self.get_parameter(
            "trend_ema_period", self.get_parameter("ma_period", 60)
        )
        self.fast_ema_period = self.get_parameter("fast_ema_period", 20)
        self.atr_period = int(self.get_parameter("atr_period", 14) or 14)
        self.grid_count = int(self.get_parameter("grid_count", 3) or 3)
        self.grid_atr_multiplier = float(
            self.get_parameter("grid_atr_multiplier", 1.0) or 1.0
        )
        self.pullback_confirm_pct = float(
            self.get_parameter("pullback_confirm_pct", 0.002) or 0.0
        )
        self.position_per_grid = int(self.get_parameter("position_per_grid", 1000) or 0)
        self.max_total_position = int(self.get_parameter("max_total_position", 5000) or 0)
        self.price_ceiling = float(self.get_parameter("price_ceiling", -1) or -1)
        self.price_floor = float(self.get_parameter("price_floor", -1) or -1)
        self.base_price = self.get_parameter(
            "base_price", self.get_parameter("basePrice", 0.0)
        )
        self._has_external_grid_plan = self._has_external_grids()

        # 初始化指标
        self.trend_ema = EMA(self.trend_ema_period)
        self.fast_ema = EMA(self.fast_ema_period)
        self.atr = ATR(self.atr_period)

        # 策略状态
        self.grids: List[GridLevel] = self._load_external_grids()
        self.inventory_lots: List[GridInventoryLot] = self._load_inventory_lots()
        self.release_events: List[GridReleaseEvent] = self._load_release_events()
        self._sync_grid_book_state("initialized")
        with self.state.silent(persist=True, notify=True, flush_on_exit=True):
            if self.state.get("last_trend_state") is None:
                self.state.last_trend_state = "undefined"  # "up", "down", "undefined"
            if self.state.get("warned_no_grids") is None:
                self.state.warned_no_grids = False
            if self.state.get("warned_invalid_tick") is None:
                self.state.warned_invalid_tick = False

        # 预热检查
        # 实际生产中可能需要在 initialize 时加载历史数据
        self.log_info(
            f"Pullback Grid 策略初始化完成. 趋势周期={self.trend_ema_period}, "
            f"网格数={len(self.grids)}"
        )

    @classmethod
    def get_state_schema(cls) -> StateSchema:
        return StateSchema(
            type="object",
            properties={
                "last_trend_state": StateProperty(
                    type="string",
                    default="undefined",
                    title="趋势状态",
                    description="上一次评估的趋势状态（up/down/undefined）",
                ),
                "warned_no_grids": StateProperty(
                    type="boolean",
                    default=False,
                    title="缺失网格告警",
                    description="是否已告知当前趋势下没有可执行网格",
                ),
                "warned_invalid_tick": StateProperty(
                    type="boolean",
                    default=False,
                    title="无效 Tick 告警",
                    description="是否已告知当前 Tick 价格异常",
                ),
                GRID_BOOK_CUSTOM_STATE_KEY: StateProperty(
                    type="object",
                    default={},
                    title="GridBook 快照",
                    description="Pullback Grid 网格簿运行状态",
                ),
            },
        )

    async def step(self, input: StrategyInput) -> StrategyOutput:
        if input.cadence == StrategyCadence.BAR:
            return self._handle_bar(input)
        if input.cadence == StrategyCadence.TICK:
            return self._handle_tick(
                input.event,
                position_profile=input.position_profile,
                execution_profile=input.execution_profile,
            )
        return StrategyOutput()

    async def warmup(self, input: StrategyInput) -> None:
        if input.cadence != StrategyCadence.BAR:
            return None
        bar = input.event
        if bar is None:
            return None
        self.trend_ema.update(bar)
        self.fast_ema.update(bar)
        self.atr.update(bar)
        return None

    def _handle_bar(self, input: StrategyInput) -> StrategyOutput:
        """K线周期逻辑：趋势判断、动态网格维护、BAR 级买入检查"""
        bar = input.event
        if bar is None:
            return StrategyOutput(decision_tags=["invalid_bar"])
        # 1. 更新指标
        self.trend_ema.update(bar)
        self.fast_ema.update(bar)
        self.atr.update(bar)

        if (
            not self.trend_ema.is_warmed_up
            or not self.fast_ema.is_warmed_up
            or not self.atr.is_warmed_up
        ):
            return StrategyOutput(
                decision_tags=["warming_up"],
                trace_payload={"reason": "indicator_warming_up"},
            )

        # 2. 获取当前指标值
        current_trend = self.trend_ema.get_current_value()
        current_fast = self.fast_ema.get_current_value()
        current_atr = float(self.atr.get_current_value() or 0.0)
        current_price = float(getattr(bar, "close", 0.0) or 0.0)
        instrument_code = input.instrument_code or self._get_bar_instrument_code(bar)
        if current_price <= 0 or current_atr <= 0:
            return StrategyOutput(
                decision_tags=["invalid_bar_price_or_atr"],
                trace_payload={
                    "reason": "invalid_bar_price_or_atr",
                    "close": current_price,
                    "atr": current_atr,
                },
            )

        # 3. 解析档位策略（优先 execution_profile，回退 position_profile）
        position_profile = input.position_profile or {}
        execution_profile = input.execution_profile or {}
        profile_snapshot = self._resolve_profile_constraints(
            execution_profile=execution_profile,
            position_profile=position_profile,
        )
        allow_swing_buy = profile_snapshot.get("allow_swing_buy", True)
        # 4. 趋势判断
        # 简单逻辑：快线 > 慢线 = 上升趋势
        trend_state = "up" if current_fast > current_trend else "down"

        # 5. 趋势切换处理
        grid_state_patch: Optional[RuntimeStatePatch] = None
        should_generate_dynamic_grid = False
        if trend_state != self.state.last_trend_state:
            if trend_state == "down":
                grid_state_patch = self._handle_downtrend_grid_cleanup()
                self.log_info(f"{bar.time} 趋势转折向下，暂停买入监控")
            else:
                should_generate_dynamic_grid = True
                self.log_info(
                    f"{bar.time} 趋势转折向上 (Fast={current_fast:.2f} > Slow={current_trend:.2f})，允许买入网格执行"
                )
            self.state.last_trend_state = trend_state

        if trend_state == "up" and not self.grids:
            if self._has_external_grid_plan and not self.state.warned_no_grids:
                self.log_warning("趋势向上但未提供外部网格，策略将不执行交易")
                self.state.warned_no_grids = True
            elif not self._has_external_grid_plan:
                should_generate_dynamic_grid = True

        if trend_state == "up" and should_generate_dynamic_grid and not self._has_external_grid_plan:
            if self._generate_dynamic_grids(current_price, current_atr):
                grid_state_patch = self._sync_grid_book_state("dynamic_grid_generated")

        bar_key = self._make_bar_key(getattr(bar, "time", None))
        buy_intents, buy_grid_changed, buy_block_events = self._collect_buy_intents(
            current_price=current_price,
            current_time=getattr(bar, "time", None),
            instrument_code=instrument_code,
            source="BAR",
            bar_key=bar_key,
            allow_swing_buy=allow_swing_buy,
            max_order_cash=profile_snapshot.get("max_order_cash"),
            max_order_qty=profile_snapshot.get("max_order_qty"),
            max_daily_spend_cash=profile_snapshot.get("max_daily_spend_cash"),
            daily_buy_used=profile_snapshot.get("daily_buy_used"),
        )

        if buy_grid_changed:
            grid_state_patch = self._sync_grid_book_state("bar_buy_intent")

        if trend_state == "up" and not self.grids and not self.state.warned_no_grids:
            self.log_warning("趋势向上但未提供外部网格，策略将不执行交易")
            self.state.warned_no_grids = True

        trade_intents = list(buy_intents)
        block_events = list(buy_block_events)

        patch = grid_state_patch
        if patch is None and self.state.last_trend_state == trend_state:
            patch = RuntimeStatePatch(set={"last_trend_state": trend_state})

        return StrategyOutput(
            trade_intents=trade_intents,
            runtime_state_patch=patch,
            decision_tags=["trend_updated", f"trend_{trend_state}"],
            trace_payload={
                "reason": "bar_update" if trade_intents or block_events else "bar_no_trade",
                "trend_state": trend_state,
                "fast_ema": current_fast,
                "trend_ema": current_trend,
                "atr": current_atr,
                "grid_count": len(self.grids),
                "bar_key": bar_key,
                "trade_intents": [intent.intent_id for intent in trade_intents],
                "block_events": block_events,
            },
        )

    def _handle_tick(
        self,
        tick: Tick,
        *,
        position_profile: Optional[Dict[str, Any]] = None,
        execution_profile: Optional[Dict[str, Any]] = None,
    ) -> StrategyOutput:
        """Tick处理：核心回撤确认逻辑 + 网格卖出执行"""
        if not self.grids or tick is None:
            return StrategyOutput()

        position_profile = position_profile or {}
        execution_profile = execution_profile or {}
        profile_snapshot = self._resolve_profile_constraints(
            execution_profile=execution_profile,
            position_profile=position_profile,
        )
        allow_swing_buy = profile_snapshot.get("allow_swing_buy", True)
        allow_swing_sell = profile_snapshot.get("allow_swing_sell", True)
        profile_trace = {
            "profile": position_profile.get("profile"),
            "allow_swing_buy": allow_swing_buy,
            "allow_swing_sell": allow_swing_sell,
            "allow_core_buy": profile_snapshot.get("allow_core_buy"),
            "allow_core_sell": profile_snapshot.get("allow_core_sell"),
            "max_daily_spend_cash": profile_snapshot.get("max_daily_spend_cash"),
            "daily_buy_used": profile_snapshot.get("daily_buy_used"),
            "reason_tags": list(position_profile.get("reason_tags") or []),
            "buy_disabled_reason": "position_profile_disallows_swing_buy"
            if not allow_swing_buy
            else None,
        }

        current_price = getattr(tick, "last_price", None)
        instrument_code = self._get_instrument_code(tick)
        if current_price is None or current_price <= 0:
            if not self.state.warned_invalid_tick:
                self.log_warning(
                    f"检测到无效 Tick 价格: {instrument_code} price={current_price}, time={getattr(tick, 'time', None)}"
                )
                self.state.warned_invalid_tick = True
            return StrategyOutput(decision_tags=["invalid_tick_price"])

        current_price = float(current_price)
        bar_key = self._make_bar_key(getattr(tick, "time", None))
        buy_intents, buy_grid_changed, buy_block_events = self._collect_buy_intents(
            current_price=current_price,
            current_time=getattr(tick, "time", None),
            instrument_code=instrument_code,
            source="TICK",
            bar_key=bar_key,
            allow_swing_buy=allow_swing_buy,
            max_order_cash=profile_snapshot.get("max_order_cash"),
            max_order_qty=profile_snapshot.get("max_order_qty"),
            max_daily_spend_cash=profile_snapshot.get("max_daily_spend_cash"),
            daily_buy_used=profile_snapshot.get("daily_buy_used"),
        )
        sell_intents, sell_grid_changed, sell_block_events = self._collect_sell_intents(
            current_price=current_price,
            current_time=getattr(tick, "time", None),
            instrument_code=instrument_code,
            source="TICK",
            bar_key=bar_key,
            allow_swing_sell=allow_swing_sell,
            max_daily_sell_qty=profile_snapshot.get("max_daily_sell_qty"),
            daily_sell_used=profile_snapshot.get("daily_sell_used"),
        )

        intents = [*buy_intents, *sell_intents]
        block_events = [*buy_block_events, *sell_block_events]
        grid_state_changed = buy_grid_changed or sell_grid_changed
        patch = self._sync_grid_book_state("tick") if grid_state_changed else None
        return StrategyOutput(
            trade_intents=intents,
            runtime_state_patch=patch,
            decision_tags=["tick_processed"] if intents else [],
            trace_payload={
                "reason": "tick_trade_intent" if intents else "tick_no_trade",
                "grid_count": len(self.grids),
                "position_profile": profile_trace,
                "bar_key": bar_key,
                "block_events": block_events,
            },
        )

    def _resolve_profile_constraints(
        self,
        *,
        execution_profile: Optional[Dict[str, Any]] = None,
        position_profile: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        execution_profile = execution_profile or {}
        position_profile = position_profile or {}
        allow_bucket_buy = position_profile.get("allow_bucket_buy") or {}
        allow_bucket_sell = position_profile.get("allow_bucket_sell") or {}
        return {
            "allow_swing_buy": bool(
                execution_profile.get(
                    "allow_swing_buy",
                    allow_bucket_buy.get("swing", position_profile.get("allow_swing_buy", True)),
                )
            ),
            "allow_swing_sell": bool(
                execution_profile.get(
                    "allow_swing_sell",
                    allow_bucket_sell.get("swing", position_profile.get("allow_swing_sell", True)),
                )
            ),
            "allow_core_buy": bool(position_profile.get("allow_core_buy", True)),
            "allow_core_sell": bool(position_profile.get("allow_core_sell", True)),
            "max_order_cash": execution_profile.get("max_order_cash"),
            "max_order_qty": execution_profile.get("max_order_qty"),
            "max_daily_spend_cash": execution_profile.get("max_daily_spend_cash"),
            "daily_buy_used": execution_profile.get("daily_buy_used"),
            "max_daily_sell_qty": execution_profile.get("max_daily_sell_qty"),
            "daily_sell_used": execution_profile.get("daily_sell_used"),
        }

    def _collect_buy_intents(
        self,
        *,
        current_price: float,
        current_time: Any,
        instrument_code: str,
        source: str,
        bar_key: str,
        allow_swing_buy: bool,
        max_order_cash: Optional[float] = None,
        max_order_qty: Optional[int] = None,
        max_daily_spend_cash: Optional[float] = None,
        daily_buy_used: Optional[float] = None,
    ) -> tuple[List[TradeIntent], bool, List[Dict[str, Any]]]:
        intents: List[TradeIntent] = []
        block_events: List[Dict[str, Any]] = []
        grid_state_changed = False

        daily_cash_remaining = None
        try:
            if max_daily_spend_cash is not None:
                daily_cash_remaining = max(
                    0.0, float(max_daily_spend_cash) - float(daily_buy_used or 0)
                )
        except (TypeError, ValueError):
            daily_cash_remaining = None

        for grid in self.grids:
            if grid.side != "BUY" or not grid.enabled:
                continue

            if grid.status == "WAIT_REARM":
                if current_price > grid.trigger_price:
                    grid.status = "PLANNED"
                    grid.reason = "price_recross_rearmed"
                    grid.waiting_reason = None
                    grid.is_monitoring = False
                    grid.lowest_price_since_touch = float("inf")
                    grid.touch_time = None
                    grid.updated_at = now_iso()
                    grid_state_changed = True
                else:
                    block_events.append(
                        self._build_block_event(
                            source=source,
                            bar_key=bar_key,
                            grid_id=grid.grid_id,
                            grid_level_index=grid.level_index,
                            reason="waiting_price_recross",
                            message="买入格等待价格重新站上触发价后再允许下一轮",
                            price=current_price,
                            event_time=current_time,
                        )
                    )
                    continue

            if grid.is_pending or grid.is_filled or grid.filled_volume > 0:
                continue

            if self.state.last_trend_state != "up":
                if current_price <= grid.trigger_price:
                    block_events.append(
                        self._build_block_event(
                            source=source,
                            bar_key=bar_key,
                            grid_id=grid.grid_id,
                            grid_level_index=grid.level_index,
                            reason="trend_not_up",
                            message="当前趋势不允许回撤买入",
                            price=current_price,
                            event_time=current_time,
                        )
                    )
                continue

            if not allow_swing_buy:
                if current_price <= grid.trigger_price:
                    block_events.append(
                        self._build_block_event(
                            source=source,
                            bar_key=bar_key,
                            grid_id=grid.grid_id,
                            grid_level_index=grid.level_index,
                            reason="disabled_by_profile",
                            message="策略仓位约束禁止买入",
                            price=current_price,
                            event_time=current_time,
                        )
                    )
                continue

            if self.price_ceiling > 0 and current_price > self.price_ceiling:
                continue
            if self.price_floor > 0 and current_price < self.price_floor:
                continue

            if not grid.is_monitoring:
                if current_price <= grid.trigger_price:
                    grid.is_monitoring = True
                    grid.lowest_price_since_touch = current_price
                    grid.touch_time = current_time
                    grid.status = "MONITORING"
                    grid.reason = "touch_buy_grid"
                    grid.updated_at = now_iso()
                    grid_state_changed = True
                continue

            if current_price < grid.lowest_price_since_touch:
                grid.lowest_price_since_touch = current_price
                grid.updated_at = now_iso()
                grid_state_changed = True

            if grid.lowest_price_since_touch <= 0:
                grid.lowest_price_since_touch = current_price
                continue

            rebound_pct = (
                current_price - grid.lowest_price_since_touch
            ) / grid.lowest_price_since_touch
            if rebound_pct < self.pullback_confirm_pct:
                continue

            requested_volume = max(0, int(grid.volume or 0))
            buy_volume = requested_volume
            if max_order_qty is not None:
                try:
                    buy_volume = min(buy_volume, max(0, int(max_order_qty)))
                except (TypeError, ValueError):
                    pass
            cash_limit = max_order_cash
            if daily_cash_remaining is not None:
                cash_limit = (
                    daily_cash_remaining
                    if cash_limit is None
                    else min(float(cash_limit), daily_cash_remaining)
                )
            if cash_limit is not None:
                try:
                    buy_volume = min(buy_volume, int(float(cash_limit) // current_price))
                except (TypeError, ValueError, ZeroDivisionError):
                    pass

            if buy_volume <= 0:
                block_events.append(
                    self._build_block_event(
                        source=source,
                        bar_key=bar_key,
                        grid_id=grid.grid_id,
                        grid_level_index=grid.level_index,
                        reason="max_budget_exhausted",
                        message="买入额度不足",
                        price=current_price,
                        event_time=current_time,
                    )
                )
                continue

            if self._would_exceed_strategy_position_cap(buy_volume):
                block_events.append(
                    self._build_block_event(
                        source=source,
                        bar_key=bar_key,
                        grid_id=grid.grid_id,
                        grid_level_index=grid.level_index,
                        reason="strategy_position_cap",
                        message="超过策略网格持仓上限",
                        price=current_price,
                        event_time=current_time,
                    )
                )
                continue

            if self._is_rejection_cooldown_active(grid, side="BUY", event_time=current_time):
                block_events.append(
                    self._build_block_event(
                        source=source,
                        bar_key=bar_key,
                        grid_id=grid.grid_id,
                        grid_level_index=grid.level_index,
                        reason="buy_rejected_today",
                        message="买入被交易域拒绝后，当日不重复发出同一网格买单",
                        price=current_price,
                        event_time=current_time,
                    )
                )
                continue

            intent_key = self._build_intent_key(
                source=source,
                bar_key=bar_key,
                side="BUY",
                grid=grid,
            )
            if self._is_duplicate_intent(grid, bar_key=bar_key, side="BUY"):
                block_events.append(
                    self._build_block_event(
                        source=source,
                        bar_key=bar_key,
                        grid_id=grid.grid_id,
                        grid_level_index=grid.level_index,
                        intent_key=intent_key,
                        reason="duplicate_intent_same_bar",
                        message="同bar同侧已触发过买入意图，拒绝重复发单",
                        price=current_price,
                        event_time=current_time,
                    )
                )
                continue

            intent = TradeIntent(
                strategy_id=self.name,
                run_id=self.context.run_id,
                instrument_code=instrument_code,
                direction=TradeIntentDirection.BUY,
                bucket="swing",
                reason="grid_pullback_buy",
                priority=TradeIntentPriority.NORMAL,
                confidence=0.9,
                target_volume=buy_volume,
                limit_price_hint=current_price,
                metadata={
                    "reason": "grid_pullback_buy",
                    "grid_level": grid.level_index,
                    "grid_id": grid.grid_id,
                    "bucket": "swing",
                    "trigger_price": grid.trigger_price,
                    "lowest_price": grid.lowest_price_since_touch,
                    "rebound_pct": rebound_pct,
                    "requested_volume": requested_volume,
                    "bar_key": bar_key,
                    "trigger_source": source,
                    "intent_key": intent_key,
                },
            )
            self._mark_intent_emitted(
                grid,
                intent,
                side="BUY",
                bar_key=bar_key,
                source=source,
            )
            grid.is_pending = True
            grid.pending_volume = buy_volume
            grid.status = "PENDING"
            grid.reason = "grid_pullback_buy"
            grid.waiting_reason = None
            grid.is_monitoring = False
            grid.updated_at = now_iso()
            intents.append(intent)
            grid_state_changed = True
            if daily_cash_remaining is not None:
                daily_cash_remaining = max(
                    0.0, daily_cash_remaining - current_price * buy_volume
                )

        return intents, grid_state_changed, block_events

    def _collect_sell_intents(
        self,
        *,
        current_price: float,
        current_time: Any,
        instrument_code: str,
        source: str,
        bar_key: str,
        allow_swing_sell: bool,
        max_daily_sell_qty: Optional[int] = None,
        daily_sell_used: Optional[int] = None,
    ) -> tuple[List[TradeIntent], bool, List[Dict[str, Any]]]:
        intents: List[TradeIntent] = []
        block_events: List[Dict[str, Any]] = []
        grid_state_changed = False

        max_daily_sell_remaining = None
        try:
            if max_daily_sell_qty is not None:
                max_daily_sell_remaining = max(
                    0, int(max_daily_sell_qty) - int(daily_sell_used or 0)
                )
        except (TypeError, ValueError):
            max_daily_sell_remaining = None

        for grid in self.grids:
            if grid.side != "SELL" or not grid.enabled:
                continue
            if current_price < grid.trigger_price:
                continue
            if grid.is_pending:
                continue
            requested_volume = max(0, int(grid.volume or 0) - int(grid.filled_volume or 0))
            if requested_volume <= 0:
                continue

            if not allow_swing_sell:
                block_events.append(
                    self._build_block_event(
                        source=source,
                        bar_key=bar_key,
                        grid_id=grid.grid_id,
                        grid_level_index=grid.level_index,
                        reason="disabled_by_profile",
                        message="策略仓位约束禁止卖出",
                        price=current_price,
                        event_time=current_time,
                    )
                )
                continue

            if max_daily_sell_remaining is not None:
                if max_daily_sell_remaining <= 0:
                    block_events.append(
                        self._build_block_event(
                            source=source,
                            bar_key=bar_key,
                            grid_id=grid.grid_id,
                            grid_level_index=grid.level_index,
                            reason="max_budget_exhausted",
                            message="日内可卖额度已用完，阻断本次卖出",
                            price=current_price,
                            event_time=current_time,
                        )
                    )
                    break
                requested_volume = min(requested_volume, max_daily_sell_remaining)

            if self._is_rejection_cooldown_active(grid, side="SELL", event_time=current_time):
                block_events.append(
                    self._build_block_event(
                        source=source,
                        bar_key=bar_key,
                        grid_id=grid.grid_id,
                        grid_level_index=grid.level_index,
                        reason="sell_rejected_today",
                        message="卖出被交易域拒绝后，当日不重复发出同一网格卖单",
                        price=current_price,
                        event_time=current_time,
                    )
                )
                continue

            selected_lots = self._select_inventory_lots_for_sell(
                grid,
                requested_volume,
                event_time=current_time,
            )
            selected_volume = sum(int(item["shares"] or 0) for item in selected_lots)
            if selected_volume < requested_volume:
                grid.available_inventory_shares = selected_volume
                grid.waiting_reason = "waiting_swing_inventory"
                grid.reason = "waiting_swing_inventory"
                grid.updated_at = now_iso()
                grid_state_changed = True
                block_events.append(
                    self._build_block_event(
                        source=source,
                        bar_key=bar_key,
                        grid_id=grid.grid_id,
                        grid_level_index=grid.level_index,
                        reason="insufficient_matching_lot",
                        message="目标卖出档可匹配活跃库存不足",
                        price=current_price,
                        event_time=current_time,
                        lot_id=selected_lots[0]["lot"].lot_id if selected_lots else None,
                    )
                )
                continue

            intent_key = self._build_intent_key(
                source=source,
                bar_key=bar_key,
                side="SELL",
                grid=grid,
            )
            if self._is_duplicate_intent(grid, bar_key=bar_key, side="SELL"):
                block_events.append(
                    self._build_block_event(
                        source=source,
                        bar_key=bar_key,
                        grid_id=grid.grid_id,
                        grid_level_index=grid.level_index,
                        intent_key=intent_key,
                        reason="duplicate_intent_same_bar",
                        message="同bar同侧已触发过卖出意图，拒绝重复发单",
                        price=current_price,
                        event_time=current_time,
                        lot_id=selected_lots[0]["lot"].lot_id if selected_lots else None,
                    )
                )
                continue

            intent = TradeIntent(
                strategy_id=self.name,
                run_id=self.context.run_id,
                instrument_code=instrument_code,
                direction=TradeIntentDirection.SELL,
                bucket="swing",
                reason="grid_sell",
                priority=TradeIntentPriority.NORMAL,
                confidence=0.8,
                target_volume=selected_volume,
                limit_price_hint=current_price,
                metadata={
                    "reason": "grid_sell",
                    "grid_level": grid.level_index,
                    "grid_id": grid.grid_id,
                    "bucket": "swing",
                    "trigger_price": grid.trigger_price,
                    "requested_volume": requested_volume,
                    "inventory_lot_ids": [item["lot"].lot_id for item in selected_lots],
                    "release_level_ids": [
                        item["lot"].source_level_id for item in selected_lots
                    ],
                    "bar_key": bar_key,
                    "trigger_source": source,
                    "intent_key": intent_key,
                },
            )
            self._reserve_inventory_lots(selected_lots, grid, intent.intent_id)
            self._mark_intent_emitted(
                grid,
                intent,
                side="SELL",
                bar_key=bar_key,
                source=source,
            )
            grid.is_pending = True
            grid.pending_volume = selected_volume
            grid.status = "PENDING"
            grid.reason = "grid_sell"
            grid.waiting_reason = None
            grid.updated_at = now_iso()
            intents.append(intent)
            grid_state_changed = True
            if max_daily_sell_remaining is not None:
                max_daily_sell_remaining = max(0, max_daily_sell_remaining - selected_volume)
                if max_daily_sell_remaining <= 0:
                    break

        return intents, grid_state_changed, block_events

    def _build_block_event(
        self,
        *,
        source: str,
        bar_key: str,
        grid_id: Optional[str],
        grid_level_index: Optional[int],
        reason: str,
        message: str,
        price: float,
        event_time: Any,
        intent_key: Optional[str] = None,
        lot_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        event = {
            "source": source,
            "bar_key": bar_key,
            "grid_id": grid_id,
            "grid_level_index": grid_level_index,
            "block_reason": reason,
            "message": message,
            "price": price,
            "event_time": event_time.isoformat() if hasattr(event_time, "isoformat") else event_time,
        }
        if intent_key:
            event["intent_key"] = intent_key
        if lot_id:
            event["lot_id"] = lot_id
        return event

    def _build_intent_key(
        self,
        *,
        source: str,
        bar_key: str,
        side: str,
        grid: GridLevel,
    ) -> str:
        return f"{source}:{bar_key}:{grid.grid_id}:{grid.side}:{grid.level_index}:{side}"

    def _is_duplicate_intent(self, grid: GridLevel, *, bar_key: str, side: str) -> bool:
        return (
            grid.last_intent_bar_key == bar_key
            and grid.last_intent_side == side
            and bool(grid.last_intent_id)
        )

    def _mark_intent_emitted(
        self,
        grid: GridLevel,
        intent: TradeIntent,
        *,
        side: str,
        bar_key: str,
        source: str,
    ) -> None:
        grid.last_intent_id = intent.intent_id
        grid.last_trace_id = intent.trace_id
        grid.last_intent_bar_key = bar_key
        grid.last_intent_source = source
        grid.last_intent_side = side

    def _is_rejection_cooldown_active(
        self, grid: GridLevel, *, side: str, event_time: Any
    ) -> bool:
        return (
            grid.last_rejected_side == side
            and grid.last_rejected_date
            and grid.last_rejected_date == self._to_date_key(event_time)
        )

    def _clear_rejection_cooldown(self, grid: GridLevel) -> None:
        grid.last_rejected_side = None
        grid.last_rejected_date = None
        grid.last_rejected_reason = None

    def _find_grid(
        self, grid_id: Optional[str], grid_level_index: Optional[int] = None
    ) -> Optional[GridLevel]:
        if grid_id:
            for grid in self.grids:
                if grid.grid_id == grid_id:
                    return grid
        if grid_level_index is not None:
            for grid in self.grids:
                if grid.level_index == grid_level_index:
                    return grid
        return None

    def _to_date_key(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "date"):
            return value.date().isoformat()
        text = str(value)
        return text[:10]

    def _optional_int(self, value: Any) -> Optional[int]:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _make_bar_key(self, value: Any) -> str:
        if hasattr(value, "strftime"):
            return value.strftime("%Y-%m-%d %H:%M")
        return str(value or "")

    def _get_bar_instrument_code(self, bar: KLine) -> str:
        return getattr(bar, "code", None) or getattr(bar, "stock_code", "")

    def _has_external_grids(self) -> bool:
        snapshot = self.state.get(GRID_BOOK_CUSTOM_STATE_KEY) or {}
        if isinstance(snapshot, dict) and snapshot.get("levels"):
            return True
        raw_levels = (
            self.get_parameter("grid_levels")
            or self.get_parameter("gridLevels")
            or self.get_parameter("levels")
        )
        return isinstance(raw_levels, list) and bool(raw_levels)

    def _handle_downtrend_grid_cleanup(self) -> RuntimeStatePatch:
        self._reset_buy_monitoring()
        return self._sync_grid_book_state("downtrend_cleanup")

    def _generate_dynamic_grids(self, current_price: float, current_atr: float) -> bool:
        if self.grids or current_price <= 0 or current_atr <= 0:
            return False
        count = max(0, int(self.grid_count or 0))
        volume = max(0, int(self.position_per_grid or 0))
        if count <= 0 or volume <= 0:
            return False
        step = current_atr * max(0.1, float(self.grid_atr_multiplier or 1.0))
        self.grids = [
            GridLevel(
                level_index=-(index + 1),
                side="BUY",
                trigger_price=round(current_price - step * (index + 1), 4),
                volume=volume,
                grid_id=f"buy-dyn-{index + 1}",
                amount=round((current_price - step * (index + 1)) * volume, 4),
                role="BUY_SLOT",
            )
            for index in range(count)
            if current_price - step * (index + 1) > 0
        ]
        self.grids.sort(key=lambda grid: grid.trigger_price)
        return bool(self.grids)

    def _would_exceed_strategy_position_cap(self, requested_volume: int) -> bool:
        max_total = int(self.max_total_position or 0)
        if max_total <= 0:
            return False
        current_grid_exposure = sum(
            max(0, int(grid.filled_volume or 0)) + max(0, int(grid.pending_volume or 0))
            for grid in self.grids
            if grid.side == "BUY"
        )
        return current_grid_exposure + max(0, int(requested_volume or 0)) > max_total

    def _resolve_target_sell_level_for_buy_grid(
        self, buy_grid: GridLevel
    ) -> Optional[GridLevel]:
        sell_grids = [
            grid
            for grid in self.grids
            if grid.side == "SELL" and grid.enabled and grid.trigger_price > buy_grid.trigger_price
        ]
        sell_grids.sort(key=lambda grid: grid.trigger_price)
        return sell_grids[0] if sell_grids else None

    def _unlock_sell_level_for_rebuild(self, grid_id: Optional[str]) -> None:
        grid = self._find_grid(grid_id)
        if not grid or grid.side != "SELL":
            return
        if grid.is_filled or grid.status == "FILLED":
            grid.is_filled = False
            grid.filled_volume = 0
            grid.status = "PLANNED"
            grid.reason = "rearmed_after_new_buy"
            grid.is_day_locked = False
            grid.sell_lock_reason = None
            grid.updated_at = now_iso()

    async def on_order(self, event: OrderStateEvent) -> Optional[RuntimeStatePatch]:
        metadata = event.metadata or {}
        grid = self._find_grid(
            metadata.get("grid_id"),
            self._optional_int(metadata.get("grid_level")),
        )
        if not grid and event.order_id:
            grid = next((item for item in self.grids if item.order_id == event.order_id), None)
        if not grid:
            return None

        status = str(event.status or "").split(".")[-1].upper()
        side = str(getattr(event.request, "order_type", "") or metadata.get("side") or grid.last_intent_side or grid.side).upper()
        if status in {"SUBMITTED", "ACCEPTED", "PARTIAL_FILLED"}:
            if event.order_id:
                grid.order_id = str(event.order_id)
                lot_ids = metadata.get("inventory_lot_ids") or [
                    lot.lot_id
                    for lot in getattr(self, "inventory_lots", [])
                    if lot.reserved_order_id == grid.last_intent_id
                ]
                self._attach_reserved_lots_to_order(lot_ids, event.order_id)
            grid.is_pending = True
            grid.status = "PENDING"
        elif status in {"REJECTED", "CANCELLED", "ERROR", "FAILED"}:
            self._release_inventory_reservations(
                metadata.get("inventory_lot_ids") or [],
                reason=status.lower(),
            )
            grid.is_pending = False
            grid.pending_volume = 0
            grid.order_id = None
            if grid.filled_volume > 0:
                grid.status = "PARTIAL_FILLED"
                grid.is_filled = grid.filled_volume >= grid.volume
            else:
                grid.status = "PLANNED" if grid.enabled else "DISABLED"
                grid.is_filled = False
            if status == "REJECTED" and (event.error_message or event.request is not None):
                grid.last_rejected_side = "SELL" if "SELL" in side else "BUY"
                grid.last_rejected_date = self._to_date_key(
                    event.timestamp
                    or grid.last_intent_bar_key
                    or self.context.current_time
                )
                grid.last_rejected_reason = event.error_message or status
            grid.last_intent_id = None
            grid.last_trace_id = None
            grid.last_intent_bar_key = None
            grid.last_intent_source = None
            grid.last_intent_side = None
        elif status == "FILLED":
            grid.is_pending = False
            grid.pending_volume = 0

        grid.updated_at = now_iso()
        return self._sync_grid_book_state(f"order_{status.lower()}")

    async def on_trade(self, event: TradeExecutionEvent) -> Optional[RuntimeStatePatch]:
        metadata = event.metadata or {}
        grid = self._find_grid(
            metadata.get("grid_id"),
            self._optional_int(metadata.get("grid_level")),
        )
        if not grid and event.order_id:
            grid = next((item for item in self.grids if item.order_id == event.order_id), None)
        if not grid:
            return None

        fill_volume = max(0, int(event.volume or 0))
        if fill_volume <= 0:
            return None

        trade_type = str(event.trade_type or "").split(".")[-1].upper()
        if trade_type == "BUY":
            previous_volume = max(0, int(grid.filled_volume or 0))
            total_volume = previous_volume + fill_volume
            if previous_volume > 0 and grid.entry_price > 0:
                grid.entry_price = (
                    grid.entry_price * previous_volume + float(event.price or 0) * fill_volume
                ) / total_volume
            else:
                grid.entry_price = float(event.price or grid.trigger_price)
            grid.entry_time = event.trade_time
            grid.filled_volume = total_volume
            grid.pending_volume = max(0, int(grid.pending_volume or 0) - fill_volume)
            grid.is_pending = grid.pending_volume > 0 and grid.filled_volume < grid.volume
            grid.is_filled = grid.filled_volume >= grid.volume
            grid.status = "FILLED" if grid.is_filled else "PARTIAL_FILLED"
            grid.reason = "buy_filled"
            grid.waiting_reason = "waiting_swing_inventory"
            self._create_inventory_lot_from_buy(grid, event, fill_volume)
        elif trade_type == "SELL":
            if grid.side == "SELL":
                sold_volume = self._apply_inventory_sell(grid, event, fill_volume)
                grid.filled_volume = max(0, int(grid.filled_volume or 0)) + sold_volume
                grid.pending_volume = max(0, int(grid.pending_volume or 0) - sold_volume)
                grid.is_pending = grid.pending_volume > 0 and grid.filled_volume < grid.volume
                grid.is_filled = grid.filled_volume >= grid.volume
                grid.status = "FILLED" if grid.is_filled else "PARTIAL_FILLED"
                if grid.is_filled:
                    grid.is_day_locked = True
                    grid.sell_last_filled_date = self._to_date_key(event.trade_time)
                grid.reason = "sell_filled"
            elif grid.side == "BUY":
                self._apply_buy_grid_exit(grid, event, fill_volume)

        grid.updated_at = now_iso()
        return self._sync_grid_book_state(f"trade_{trade_type.lower()}")

    def _load_external_grids(self) -> List[GridLevel]:
        """加载前端生成的网格"""
        snapshot = self.state.get(GRID_BOOK_CUSTOM_STATE_KEY) or {}
        raw_levels = snapshot.get("levels") if isinstance(snapshot, dict) else None
        if not raw_levels:
            raw_levels = (
                self.get_parameter("grid_levels")
                or self.get_parameter("gridLevels")
                or self.get_parameter("levels")
            )
        if not raw_levels or not isinstance(raw_levels, list):
            return []

        invalid_grid_rows = 0
        grids: List[GridLevel] = []
        for idx, raw in enumerate(raw_levels):
            if not isinstance(raw, dict):
                invalid_grid_rows += 1
                self.log_warning(
                    f"pullback_grid 外部网格配置无效，已跳过。 reason=not_dict, index={idx}, "
                    f"run_id={self.context.run_id}, raw={repr(raw)}"
                )
                continue

            side_raw = raw.get("side")
            if not side_raw:
                invalid_grid_rows += 1
                self.log_warning(
                    f"pullback_grid 外部网格配置无效，已跳过。 reason=missing_side, index={idx}, "
                    f"run_id={self.context.run_id}, raw={repr(raw)}"
                )
                continue
            side = str(side_raw).upper()
            if side not in ("BUY", "SELL"):
                invalid_grid_rows += 1
                self.log_warning(
                    f"pullback_grid 外部网格配置无效，已跳过。 reason=invalid_side, index={idx}, side={side}, "
                    f"run_id={self.context.run_id}, raw={repr(raw)}"
                )
                continue

            price = raw.get("price", raw.get("trigger_price", raw.get("triggerPrice")))
            shares = raw.get("shares", raw.get("volume", raw.get("qty")))
            try:
                price = float(price)
            except (TypeError, ValueError):
                invalid_grid_rows += 1
                self.log_warning(
                    f"pullback_grid 外部网格配置无效，已跳过。 reason=invalid_price, index={idx}, "
                    f"run_id={self.context.run_id}, raw={repr(raw)}"
                )
                continue
            try:
                shares = int(shares)
            except (TypeError, ValueError):
                shares = 0

            if price <= 0 or shares <= 0:
                invalid_grid_rows += 1
                self.log_warning(
                    f"pullback_grid 外部网格配置无效，已跳过。 reason=non_positive, index={idx}, "
                    f"price={price}, shares={shares}, run_id={self.context.run_id}"
                )
                continue

            level_index = raw.get("levelIndex", raw.get("level_index", idx))
            try:
                level_index = int(level_index)
            except (TypeError, ValueError):
                level_index = idx

            grid_id = raw.get("id", raw.get("grid_id"))
            if not grid_id:
                grid_id = raw.get("gridId")
            enabled = bool(raw.get("enabled", True))
            filled_volume = int(raw.get("filled_shares", raw.get("filledShares", raw.get("filled_volume", 0))) or 0)
            pending_volume = int(raw.get("pending_shares", raw.get("pendingShares", raw.get("pending_volume", 0))) or 0)
            status = normalize_status(raw.get("status"), enabled=enabled)

            grids.append(
                GridLevel(
                    level_index=level_index,
                    side=side,
                    trigger_price=price,
                    volume=shares,
                    grid_id=grid_id,
                    amount=float(raw.get("amount", price * shares) or 0),
                    pct_from_base=raw.get("pctFromBase", raw.get("pct_from_base")),
                    expected_profit=raw.get("expectedProfit", raw.get("expected_profit")),
                    enabled=enabled,
                    status=status,
                    role=raw.get(
                        "role",
                        "BUY_SLOT" if side == "BUY" else "SELL_WATERLINE",
                    ),
                    cycle_count=int(raw.get("cycle_count", raw.get("cycleCount", 0)) or 0),
                    available_inventory_shares=int(
                        raw.get(
                            "available_inventory_shares",
                            raw.get("availableInventoryShares", 0),
                        )
                        or 0
                    ),
                    reserved_inventory_shares=int(
                        raw.get(
                            "reserved_inventory_shares",
                            raw.get("reservedInventoryShares", 0),
                        )
                        or 0
                    ),
                    waiting_reason=raw.get("waiting_reason", raw.get("waitingReason")),
                    is_filled=status == "FILLED",
                    entry_price=float(raw.get("entry_price", raw.get("entryPrice", 0.0)) or 0.0),
                    entry_time=raw.get("entry_time", raw.get("entryTime")),
                    is_pending=status == "PENDING",
                    order_id=raw.get("order_id", raw.get("orderId")),
                    is_monitoring=bool(raw.get("monitoring", raw.get("is_monitoring", status == "MONITORING"))),
                    filled_volume=filled_volume,
                    pending_volume=pending_volume,
                    last_intent_id=raw.get("last_intent_id", raw.get("lastIntentId")),
                    last_trace_id=raw.get("last_trace_id", raw.get("lastTraceId")),
                    last_intent_bar_key=raw.get("last_intent_bar_key", raw.get("lastIntentBarKey")),
                    last_intent_source=raw.get("last_intent_source", raw.get("lastIntentSource")),
                    last_intent_side=raw.get("last_intent_side", raw.get("lastIntentSide")),
                    last_rejected_side=raw.get("last_rejected_side", raw.get("lastRejectedSide")),
                    last_rejected_date=raw.get("last_rejected_date", raw.get("lastRejectedDate")),
                    last_rejected_reason=raw.get("last_rejected_reason", raw.get("lastRejectedReason")),
                    is_day_locked=bool(raw.get("is_day_locked", raw.get("isDayLocked", False))),
                    sell_lock_reason=raw.get("sell_lock_reason", raw.get("sellLockReason")),
                    sell_last_filled_date=raw.get("sell_last_filled_date", raw.get("sellLastFilledDate")),
                    reason=raw.get("reason"),
                    updated_at=raw.get("updated_at", raw.get("updatedAt")),
                )
            )

        if invalid_grid_rows:
            self.log_warning(
                f"pullback_grid 外部网格总计失败: invalid_grid_row={invalid_grid_rows}, run_id={self.context.run_id}"
            )

        grids.sort(key=lambda g: g.trigger_price)
        return grids

    def _load_inventory_lots(self) -> List[GridInventoryLot]:
        snapshot = self.state.get(GRID_BOOK_CUSTOM_STATE_KEY) or {}
        raw_lots = []
        if isinstance(snapshot, dict):
            raw_lots = snapshot.get("inventory_lots") or snapshot.get("inventoryLots") or []

        invalid_lot_rows = 0
        lots = []
        for raw in raw_lots:
            if not isinstance(raw, dict):
                invalid_lot_rows += 1
                self.log_warning(
                    f"pullback_grid 持仓批次配置无效，已跳过。 reason=not_dict, run_id={self.context.run_id}, raw={repr(raw)}"
                )
                continue
            try:
                lot = self._inventory_lot_from_dict(raw)
            except Exception as exc:
                invalid_lot_rows += 1
                self.log_warning(
                    f"pullback_grid 持仓批次配置无效，已跳过。 reason=convert_failed, "
                    f"run_id={self.context.run_id}, raw={repr(raw)}, error={repr(exc)}"
                )
                continue
            if lot is None:
                invalid_lot_rows += 1
                self.log_warning(
                    f"pullback_grid 持仓批次配置无效，已跳过。 reason=empty_lot, run_id={self.context.run_id}, raw={repr(raw)}"
                )
                continue
            lots.append(lot)

        if invalid_lot_rows:
            self.log_warning(
                f"pullback_grid 持仓批次总计失败: invalid_lot_row={invalid_lot_rows}, run_id={self.context.run_id}"
            )
        if lots:
            return lots

        swing_shares = int(
            self.get_parameter(
                "swing_shares",
                self.get_parameter("initial_swing_shares", 0),
            )
            or 0
        )
        if swing_shares <= 0:
            return []
        entry_price = float(
            self.get_parameter("avg_cost", self.get_parameter("base_price", 0.0))
            or 0.0
        )
        instrument_code = self.get_parameter(
            "instrument_code",
            self.get_parameter("stockCodes", self.get_parameter("symbol", "")),
        )
        if isinstance(instrument_code, list):
            instrument_code = str(instrument_code[0] if instrument_code else "")
        else:
            instrument_code = str(instrument_code or "").split(",")[0].strip()
        lot_dicts = build_initial_swing_lots(
            swing_shares=swing_shares,
            entry_price=entry_price,
            owner=instrument_code or self.context.run_id,
            sell_levels=[self._grid_level_to_inventory_plan(grid) for grid in self.grids],
        )
        return [self._inventory_lot_from_dict(lot) for lot in lot_dicts]

    def _grid_level_to_inventory_plan(self, grid: GridLevel) -> Dict[str, Any]:
        return {
            "grid_id": grid.grid_id,
            "level_index": grid.level_index,
            "side": grid.side,
            "price": grid.trigger_price,
            "planned_shares": grid.volume,
            "enabled": grid.enabled,
        }

    def _load_release_events(self) -> List[GridReleaseEvent]:
        snapshot = self.state.get(GRID_BOOK_CUSTOM_STATE_KEY) or {}
        raw_events = []
        if isinstance(snapshot, dict):
            raw_events = snapshot.get("release_events") or snapshot.get("releaseEvents") or []
        return [
            self._release_event_from_dict(raw)
            for raw in raw_events
            if isinstance(raw, dict)
        ]

    def _inventory_lot_from_dict(self, raw: Dict[str, Any]) -> GridInventoryLot:
        data = normalize_inventory_lot(raw)
        source_level_index = data.get("source_level_index")
        if source_level_index is not None:
            try:
                source_level_index = int(source_level_index)
            except (TypeError, ValueError):
                source_level_index = None
        return GridInventoryLot(
            lot_id=data["lot_id"],
            source_level_id=data.get("source_level_id"),
            source_level_index=source_level_index,
            source=data["source"],
            bucket=data["bucket"],
            entry_price=data["entry_price"],
            original_shares=data["original_shares"],
            remaining_shares=data["remaining_shares"],
            reserved_shares=data["reserved_shares"],
            reserved_for_level_id=data.get("reserved_for_level_id"),
            reserved_order_id=data.get("reserved_order_id"),
            target_sell_level_id=data.get("target_sell_level_id"),
            target_sell_level_index=self._optional_int(
                data.get("target_sell_level_index")
            ),
            entry_date=raw.get("entry_date", raw.get("entryDate")),
            status=data["status"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )

    def _release_event_from_dict(self, raw: Dict[str, Any]) -> GridReleaseEvent:
        data = normalize_release_event(raw)
        return GridReleaseEvent(
            event_id=data["event_id"],
            sell_level_id=data.get("sell_level_id"),
            sell_level_index=data.get("sell_level_index"),
            released_level_id=data.get("released_level_id"),
            released_level_index=data.get("released_level_index"),
            lot_ids=list(data.get("lot_ids") or []),
            order_id=data.get("order_id"),
            intent_id=data.get("intent_id"),
            trade_id=data.get("trade_id"),
            price=data["price"],
            shares=data["shares"],
            created_at=data["created_at"],
        )

    def _inventory_lot_to_dict(self, lot: GridInventoryLot) -> Dict[str, Any]:
        return {
            "lot_id": lot.lot_id,
            "source_level_id": lot.source_level_id,
            "source_level_index": lot.source_level_index,
            "source": lot.source,
            "bucket": lot.bucket,
            "entry_price": lot.entry_price,
            "original_shares": lot.original_shares,
            "remaining_shares": lot.remaining_shares,
            "reserved_shares": lot.reserved_shares,
            "reserved_for_level_id": lot.reserved_for_level_id,
            "reserved_order_id": lot.reserved_order_id,
            "target_sell_level_id": lot.target_sell_level_id,
            "target_sell_level_index": lot.target_sell_level_index,
            "entry_date": lot.entry_date,
            "status": lot.status,
            "created_at": lot.created_at,
            "updated_at": lot.updated_at,
        }

    def _release_event_to_dict(self, event: GridReleaseEvent) -> Dict[str, Any]:
        return {
            "event_id": event.event_id,
            "sell_level_id": event.sell_level_id,
            "sell_level_index": event.sell_level_index,
            "released_level_id": event.released_level_id,
            "released_level_index": event.released_level_index,
            "lot_ids": list(event.lot_ids),
            "order_id": event.order_id,
            "intent_id": event.intent_id,
            "trade_id": event.trade_id,
            "price": event.price,
            "shares": event.shares,
            "created_at": event.created_at,
        }

    def _refresh_level_inventory_metrics(self) -> None:
        lots = getattr(self, "inventory_lots", [])
        for grid in self.grids:
            if grid.side == "SELL":
                available = 0
                reserved = 0
                for lot in lots:
                    if not self._lot_can_feed_sell(lot, grid):
                        continue
                    available += max(0, lot.remaining_shares - lot.reserved_shares)
                    if lot.reserved_for_level_id == grid.grid_id:
                        reserved += lot.reserved_shares
                grid.available_inventory_shares = available
                grid.reserved_inventory_shares = reserved
                if (
                    grid.enabled
                    and not grid.is_pending
                    and available < max(0, grid.volume - grid.filled_volume)
                ):
                    grid.waiting_reason = "waiting_swing_inventory"
                elif grid.waiting_reason == "waiting_swing_inventory":
                    grid.waiting_reason = None
            else:
                source_id = grid.grid_id
                grid.available_inventory_shares = sum(
                    max(0, lot.remaining_shares - lot.reserved_shares)
                    for lot in lots
                    if lot.source_level_id == source_id and lot.status in {"OPEN", "RESERVED"}
                )
                grid.reserved_inventory_shares = sum(
                    lot.reserved_shares
                    for lot in lots
                    if lot.source_level_id == source_id
                )

    def _lot_is_mature_for_sell(
        self,
        lot: GridInventoryLot,
        event_time: Any = None,
    ) -> bool:
        if not lot.entry_date or event_time is None:
            return True
        return str(lot.entry_date) < self._to_date_key(event_time)

    def _lot_can_feed_sell(
        self,
        lot: GridInventoryLot,
        grid: GridLevel,
        event_time: Any = None,
    ) -> bool:
        has_target = bool(lot.target_sell_level_id) or lot.target_sell_level_index is not None
        if has_target and lot.target_sell_level_id and lot.target_sell_level_id != grid.grid_id:
            return False
        if (
            has_target
            and lot.target_sell_level_index is not None
            and lot.target_sell_level_index != grid.level_index
        ):
            return False
        return (
            lot.bucket == "swing"
            and lot.status in {"OPEN", "RESERVED"}
            and lot.remaining_shares > 0
            and self._lot_is_mature_for_sell(lot, event_time)
            and lot.entry_price < grid.trigger_price
        )

    def _select_inventory_lots_for_sell(
        self,
        grid: GridLevel,
        target_shares: int,
        event_time: Any = None,
    ) -> List[Dict[str, Any]]:
        candidates = [
            lot
            for lot in getattr(self, "inventory_lots", [])
            if self._lot_can_feed_sell(lot, grid, event_time=event_time)
            and (
                not lot.reserved_for_level_id
                or lot.reserved_for_level_id == grid.grid_id
            )
            and lot.remaining_shares - lot.reserved_shares > 0
        ]
        candidates.sort(key=lambda lot: (lot.entry_price, lot.created_at), reverse=True)
        selected: List[Dict[str, Any]] = []
        remaining = max(0, int(target_shares or 0))
        for lot in candidates:
            if remaining <= 0:
                break
            available = max(0, lot.remaining_shares - lot.reserved_shares)
            take = min(remaining, available)
            if take <= 0:
                continue
            selected.append({"lot": lot, "shares": take})
            remaining -= take
        return selected

    def _reserve_inventory_lots(
        self, selected_lots: List[Dict[str, Any]], grid: GridLevel, intent_id: str
    ) -> None:
        for item in selected_lots:
            lot = item["lot"]
            shares = int(item["shares"] or 0)
            lot.reserved_shares = min(lot.remaining_shares, lot.reserved_shares + shares)
            lot.reserved_for_level_id = grid.grid_id
            lot.reserved_order_id = intent_id
            lot.status = "RESERVED"
            lot.updated_at = now_iso()
        grid.reserved_inventory_shares = sum(item["shares"] for item in selected_lots)

    def _attach_reserved_lots_to_order(self, lot_ids: List[str], order_id: Any) -> None:
        if not order_id:
            return
        lot_id_set = {str(lot_id) for lot_id in lot_ids or [] if lot_id}
        for lot in getattr(self, "inventory_lots", []):
            if lot.lot_id in lot_id_set:
                lot.reserved_order_id = str(order_id)
                lot.updated_at = now_iso()

    def _release_inventory_reservations(
        self, lot_ids: List[str], reason: str = ""
    ) -> None:
        lot_id_set = {str(lot_id) for lot_id in lot_ids or [] if lot_id}
        if not lot_id_set:
            return
        for lot in getattr(self, "inventory_lots", []):
            if lot.lot_id not in lot_id_set:
                continue
            if lot.reserved_shares <= 0:
                continue
            lot.reserved_shares = 0
            lot.reserved_for_level_id = None
            lot.reserved_order_id = None
            lot.status = "OPEN" if lot.remaining_shares > 0 else "CLOSED"
            lot.updated_at = now_iso()

    def _create_inventory_lot_from_buy(
        self, grid: GridLevel, event: TradeExecutionEvent, fill_volume: int
    ) -> None:
        trade_id = str(getattr(event, "trade_id", "") or "")
        lot_id = trade_id or f"buy-lot-{grid.grid_id}-{uuid.uuid4()}"
        if any(lot.lot_id == lot_id for lot in getattr(self, "inventory_lots", [])):
            return
        target_sell_grid = self._resolve_target_sell_level_for_buy_grid(grid)
        self.inventory_lots.append(
            GridInventoryLot(
                lot_id=lot_id,
                source_level_id=grid.grid_id,
                source_level_index=grid.level_index,
                source="BUY_FILL",
                bucket="swing",
                entry_price=float(event.price or grid.entry_price or grid.trigger_price),
                original_shares=fill_volume,
                remaining_shares=fill_volume,
                target_sell_level_id=(
                    target_sell_grid.grid_id if target_sell_grid else None
                ),
                target_sell_level_index=(
                    target_sell_grid.level_index if target_sell_grid else None
                ),
                entry_date=self._to_date_key(event.trade_time),
            )
        )
        if target_sell_grid:
            self._unlock_sell_level_for_rebuild(target_sell_grid.grid_id)

    def _apply_inventory_sell(
        self, grid: GridLevel, event: TradeExecutionEvent, fill_volume: int
    ) -> int:
        metadata = getattr(event, "metadata", {}) or {}
        lot_ids = [str(lot_id) for lot_id in metadata.get("inventory_lot_ids", []) or []]
        order_id = str(event.order_id or "")
        lots = [
            lot
            for lot in getattr(self, "inventory_lots", [])
            if self._lot_can_feed_sell(lot, grid)
            and ((lot_ids and lot.lot_id in lot_ids)
            or (order_id and lot.reserved_order_id == order_id))
        ]
        if not lots:
            self.log_warning(
                f"卖出成交匹配失败: 网格={grid.grid_id} 无法匹配到目标卖位lot "
                f"(order_id={order_id}, lot_ids={lot_ids})"
            )
            return 0
        lots.sort(key=lambda lot: (lot.entry_price, lot.created_at), reverse=True)
        remaining = fill_volume
        touched_lot_ids: List[str] = []
        released_level_id = None
        released_level_index = None
        for lot in lots:
            if remaining <= 0:
                break
            available = lot.reserved_shares if lot.reserved_shares > 0 else lot.remaining_shares
            take = min(remaining, available, lot.remaining_shares)
            if take <= 0:
                continue
            lot.remaining_shares = max(0, lot.remaining_shares - take)
            lot.reserved_shares = max(0, lot.reserved_shares - take)
            if lot.remaining_shares <= 0:
                lot.status = "CLOSED"
                lot.reserved_for_level_id = None
                lot.reserved_order_id = None
                released = self._release_source_buy_slot_if_ready(lot)
                if released:
                    released_level_id = released.grid_id
                    released_level_index = released.level_index
            else:
                lot.status = "RESERVED" if lot.reserved_shares > 0 else "OPEN"
            lot.updated_at = now_iso()
            touched_lot_ids.append(lot.lot_id)
            remaining -= take

        sold_volume = fill_volume - max(0, remaining)
        if touched_lot_ids:
            self.release_events.append(
                GridReleaseEvent(
                    event_id=f"release-{uuid.uuid4()}",
                    sell_level_id=grid.grid_id,
                    sell_level_index=grid.level_index,
                    released_level_id=released_level_id,
                    released_level_index=released_level_index,
                    lot_ids=touched_lot_ids,
                    order_id=order_id or None,
                    intent_id=grid.last_intent_id,
                    trade_id=str(getattr(event, "trade_id", "") or "") or None,
                    price=float(event.price or grid.trigger_price),
                    shares=sold_volume,
                )
            )
        return sold_volume

    def _apply_buy_grid_exit(
        self, grid: GridLevel, event: TradeExecutionEvent, fill_volume: int
    ) -> None:
        metadata = getattr(event, "metadata", {}) or {}
        lot_ids = [str(lot_id) for lot_id in metadata.get("inventory_lot_ids", []) or []]
        lots = [
            lot
            for lot in getattr(self, "inventory_lots", [])
            if (
                (lot_ids and lot.lot_id in lot_ids)
                or (not lot_ids and lot.source_level_id == grid.grid_id)
            )
            and lot.status in {"OPEN", "RESERVED"}
            and lot.remaining_shares > 0
        ]
        lots.sort(key=lambda lot: (lot.entry_price, lot.created_at), reverse=True)

        remaining = max(0, int(fill_volume or 0))
        touched_lot_ids: List[str] = []
        for lot in lots:
            if remaining <= 0:
                break
            take = min(remaining, lot.remaining_shares)
            if take <= 0:
                continue
            lot.remaining_shares = max(0, lot.remaining_shares - take)
            lot.reserved_shares = max(0, lot.reserved_shares - take)
            if lot.remaining_shares <= 0:
                lot.status = "CLOSED"
                lot.reserved_for_level_id = None
                lot.reserved_order_id = None
            else:
                lot.status = "RESERVED" if lot.reserved_shares > 0 else "OPEN"
            lot.updated_at = now_iso()
            touched_lot_ids.append(lot.lot_id)
            remaining -= take

        sold_volume = fill_volume - max(0, remaining)
        if sold_volume <= 0:
            sold_volume = fill_volume
        grid.filled_volume = max(0, int(grid.filled_volume or 0) - sold_volume)
        grid.pending_volume = max(0, int(grid.pending_volume or 0) - sold_volume)
        grid.is_pending = grid.pending_volume > 0
        grid.is_filled = grid.filled_volume >= grid.volume and grid.filled_volume > 0
        if grid.filled_volume <= 0:
            grid.cycle_count += 1
            self._mark_buy_grid_waiting_rearm(grid, "exit_completed_wait_rearm")
        else:
            grid.status = "PENDING" if grid.is_pending else "PARTIAL_FILLED"
            grid.reason = "exit_partial_filled"
        grid.updated_at = now_iso()

        if touched_lot_ids:
            self.release_events.append(
                GridReleaseEvent(
                    event_id=f"release-{uuid.uuid4()}",
                    sell_level_id=grid.grid_id,
                    sell_level_index=grid.level_index,
                    released_level_id=grid.grid_id if grid.filled_volume <= 0 else None,
                    released_level_index=grid.level_index if grid.filled_volume <= 0 else None,
                    lot_ids=touched_lot_ids,
                    order_id=str(event.order_id or "") or None,
                    intent_id=grid.last_intent_id,
                    trade_id=str(getattr(event, "trade_id", "") or "") or None,
                    price=float(event.price or 0.0),
                    shares=sold_volume,
                )
            )

    def _release_source_buy_slot_if_ready(
        self, lot: GridInventoryLot
    ) -> Optional[GridLevel]:
        if not lot.source_level_id:
            return None
        if any(
            other.source_level_id == lot.source_level_id
            and other.status in {"OPEN", "RESERVED"}
            and other.remaining_shares > 0
            for other in getattr(self, "inventory_lots", [])
        ):
            return None
        source_grid = self._find_grid(lot.source_level_id, lot.source_level_index)
        if not source_grid or source_grid.side != "BUY":
            return None
        source_grid.cycle_count += 1
        self._mark_buy_grid_waiting_rearm(source_grid, "released_by_sell_waterline")
        return source_grid

    def _mark_buy_grid_waiting_rearm(self, grid: GridLevel, reason: str) -> None:
        grid.is_filled = False
        grid.is_pending = False
        grid.is_monitoring = False
        grid.filled_volume = 0
        grid.pending_volume = 0
        grid.order_id = None
        grid.entry_price = 0.0
        grid.entry_time = None
        grid.status = "WAIT_REARM"
        grid.reason = reason
        grid.waiting_reason = "waiting_price_recross"
        grid.lowest_price_since_touch = float("inf")
        grid.touch_time = None
        self._clear_rejection_cooldown(grid)
        grid.updated_at = now_iso()

    def apply_grid_book_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """应用外部维护后的 GridBook 快照。"""
        with self.state.silent(persist=True, notify=True, flush_on_exit=True):
            self.state.grid_book_snapshot = dict(snapshot or {})
        self.grids = self._load_external_grids()
        self.inventory_lots = self._load_inventory_lots()
        self.release_events = self._load_release_events()

    def _sync_grid_book_state(self, reason: str) -> RuntimeStatePatch:
        """同步内存网格到策略状态，供 RuntimeStateManager 持久化。"""
        snapshot = self._grid_book_snapshot(reason)
        with self.state.silent(persist=True, notify=True, flush_on_exit=True):
            self.state.grid_book_snapshot = snapshot
        return RuntimeStatePatch(set={GRID_BOOK_CUSTOM_STATE_KEY: snapshot})

    def _grid_book_snapshot(self, reason: str) -> Dict[str, Any]:
        self._refresh_level_inventory_metrics()
        levels = []
        for grid in self.grids:
            status = grid.status
            if not grid.enabled:
                status = "DISABLED"
            elif grid.is_filled:
                status = "FILLED"
            elif grid.filled_volume > 0:
                status = "PARTIAL_FILLED"
            elif grid.is_pending:
                status = "PENDING"
            elif grid.is_monitoring:
                status = "MONITORING"
            levels.append({
                "grid_id": grid.grid_id or f"grid-{grid.level_index}-{grid.side}",
                "level_index": grid.level_index,
                "side": grid.side,
                "role": grid.role,
                "price": grid.trigger_price,
                "planned_shares": grid.volume,
                "amount": grid.amount or grid.trigger_price * grid.volume,
                "pct_from_base": grid.pct_from_base,
                "expected_profit": grid.expected_profit,
                "enabled": grid.enabled,
                "status": status,
                "monitoring": grid.is_monitoring,
                "pending_shares": grid.pending_volume,
                "filled_shares": grid.filled_volume,
                "available_inventory_shares": grid.available_inventory_shares,
                "reserved_inventory_shares": grid.reserved_inventory_shares,
                "cycle_count": grid.cycle_count,
                "waiting_reason": grid.waiting_reason,
                "order_id": grid.order_id,
                "entry_price": grid.entry_price or None,
                "entry_time": grid.entry_time.isoformat() if isinstance(grid.entry_time, datetime) else grid.entry_time,
                "last_intent_id": grid.last_intent_id,
                "last_trace_id": grid.last_trace_id,
                "last_intent_bar_key": grid.last_intent_bar_key,
                "last_intent_source": grid.last_intent_source,
                "last_intent_side": grid.last_intent_side,
                "last_rejected_side": grid.last_rejected_side,
                "last_rejected_date": grid.last_rejected_date,
                "last_rejected_reason": grid.last_rejected_reason,
                "is_day_locked": grid.is_day_locked,
                "sell_lock_reason": grid.sell_lock_reason,
                "sell_last_filled_date": grid.sell_last_filled_date,
                "reason": grid.reason or reason,
                "updated_at": grid.updated_at,
            })
        return {
            "run_id": self.context.run_id,
            "instrument_code": self.get_parameter("instrument_code", self.get_parameter("symbol", "")),
            "base_price": self.base_price,
            "parameter_version": str(self.get_parameter("_parameter_version", "")),
            "version": 1,
            "reason": reason,
            "model_version": GRID_BOOK_MODEL_VERSION,
            "inventory_model": INVENTORY_MODEL,
            "release_rule": RELEASE_RULE,
            "sell_empty_behavior": SELL_EMPTY_BEHAVIOR,
            "needs_backtest": False,
            "levels": levels,
            "inventory_lots": [self._inventory_lot_to_dict(lot) for lot in self.inventory_lots],
            "release_events": [
                self._release_event_to_dict(event) for event in self.release_events[-200:]
            ],
            "updated_at": now_iso(),
        }

    def _reset_buy_monitoring(self) -> None:
        """趋势转弱时重置回撤监控状态"""
        for grid in self.grids:
            if grid.side != "BUY" or grid.is_filled:
                continue
            grid.is_monitoring = False
            grid.lowest_price_since_touch = float("inf")
            grid.touch_time = None

    def _get_instrument_code(self, tick: Tick) -> str:
        return getattr(tick, "code", None) or getattr(tick, "stock_code", "")

    async def on_stop(self) -> None:
        """策略停止"""
        self.log_info("Pullback Grid 策略停止")
        # 这里可以选择是否清仓，通常网格策略停止时不自动清仓，留给用户处理

