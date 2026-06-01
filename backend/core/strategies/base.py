"""
策略基础架构 - 策略抽象基类和生命周期管理
"""

import asyncio
import logging
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from models.enums import StrategyInstrumentScope, StrategyRunMode
from core.utils import time_utils

if TYPE_CHECKING:
  from models.parameter_schema import ParameterSchema
  from core.state_schema import StateSchema


class StrategyCadence(str, Enum):
  """策略 Step 输入节奏"""

  BAR = "BAR"
  TICK = "TICK"
  ORDER = "ORDER"
  TRADE = "TRADE"
  RECONCILE = "RECONCILE"


class TradeIntentDirection(str, Enum):
  """策略交易意图方向"""

  BUY = "BUY"
  SELL = "SELL"
  HOLD = "HOLD"


class TradeIntentPriority(str, Enum):
  """交易意图优先级"""

  LOW = "LOW"
  NORMAL = "NORMAL"
  HIGH = "HIGH"
  RISK_REDUCTION = "RISK_REDUCTION"
  URGENT = "URGENT"


class TradeIntentType(str, Enum):
  """交易意图尺寸类型"""

  TARGET_POSITION_PCT = "TARGET_POSITION_PCT"
  TARGET_AMOUNT = "TARGET_AMOUNT"
  TARGET_VOLUME = "TARGET_VOLUME"
  CANCEL_ORDER = "CANCEL_ORDER"


FORBIDDEN_RUNTIME_STATE_FIELDS = {
  "cash",
  "available_cash",
  "frozen_cash",
  "cash_total",
  "total_asset",
  "long_volume",
  "short_volume",
  "available_volume",
  "frozen_volume",
  "today_buy_volume",
}


@dataclass
class RuntimeStatePatch:
  """策略算法状态补丁，不允许携带真实账户状态。"""

  set: Dict[str, Any] = field(default_factory=dict)
  unset: List[str] = field(default_factory=list)
  append_events: List[Dict[str, Any]] = field(default_factory=list)

  def __post_init__(self) -> None:
    forbidden = FORBIDDEN_RUNTIME_STATE_FIELDS.intersection(self.set.keys())
    if forbidden:
      names = ", ".join(sorted(forbidden))
      raise ValueError(f"RuntimeStatePatch cannot mutate account fields: {names}")


@dataclass
class TradeIntent:
  """策略层唯一交易语义输出。"""

  strategy_id: str
  run_id: str
  instrument_code: str
  direction: TradeIntentDirection
  bucket: str
  reason: str
  priority: TradeIntentPriority = TradeIntentPriority.NORMAL
  intent_type: Optional[TradeIntentType] = None
  confidence: float = 1.0
  target_amount: Optional[float] = None
  target_position_pct: Optional[float] = None
  target_volume: Optional[int] = None
  limit_price_hint: Optional[float] = None
  expiry_policy: Dict[str, Any] = field(default_factory=dict)
  metadata: Dict[str, Any] = field(default_factory=dict)
  trace_id: Optional[str] = None
  intent_id: str = field(default_factory=lambda: str(uuid.uuid4()))
  created_at: datetime = field(default_factory=time_utils.now)

  def __post_init__(self) -> None:
    if isinstance(self.direction, str):
      self.direction = TradeIntentDirection(self.direction)
    if isinstance(self.priority, str):
      self.priority = TradeIntentPriority(self.priority)
    if isinstance(self.intent_type, str):
      self.intent_type = TradeIntentType(self.intent_type)
    if self.intent_type is None:
      self.intent_type = self._infer_intent_type()

    if self.direction in {TradeIntentDirection.BUY, TradeIntentDirection.SELL}:
      if not self.instrument_code:
        raise ValueError("TradeIntent requires instrument_code")
      if not self.bucket:
        raise ValueError("TradeIntent requires bucket for BUY/SELL")
      if not self.reason:
        raise ValueError("TradeIntent requires reason for BUY/SELL")
      requested_volume = self.target_volume or self.metadata.get(
        "requested_volume", self.metadata.get("volume")
      )
      if (
        self.target_amount is None
        and self.target_position_pct is None
        and requested_volume is None
        and not self.metadata.get("sell_all")
        and not self.metadata.get("close_position")
      ):
        raise ValueError(
          "TradeIntent requires target_amount, target_position_pct, requested_volume, or sell_all"
        )

  def _infer_intent_type(self) -> TradeIntentType:
    if self.target_position_pct is not None:
      return TradeIntentType.TARGET_POSITION_PCT
    if self.target_amount is not None:
      return TradeIntentType.TARGET_AMOUNT
    if self.target_volume is not None or self.metadata.get("requested_volume"):
      return TradeIntentType.TARGET_VOLUME
    return TradeIntentType.TARGET_AMOUNT


@dataclass
class StrategyInput:
  """策略 Step 的唯一输入快照。"""

  run_id: str
  strategy_id: str
  timestamp: datetime
  cadence: StrategyCadence
  instrument_code: str
  input_id: str = field(default_factory=lambda: str(uuid.uuid4()))
  trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
  market_data: Any = None
  event: Any = None
  portfolio_state: Dict[str, Any] = field(default_factory=dict)
  bucket_ledger: Dict[str, Any] = field(default_factory=dict)
  market_context: Dict[str, Any] = field(default_factory=dict)
  risk_caps: Dict[str, Any] = field(default_factory=dict)
  position_profile: Dict[str, Any] = field(default_factory=dict)
  execution_profile: Dict[str, Any] = field(default_factory=dict)
  open_orders: List[Any] = field(default_factory=list)
  strategy_state: Dict[str, Any] = field(default_factory=dict)
  parameters: Dict[str, Any] = field(default_factory=dict)

  @property
  def decision_time_ms(self) -> int:
    return int(self.timestamp.timestamp() * 1000)

  @property
  def trade_date(self) -> str:
    return self.timestamp.date().isoformat()

  def __post_init__(self) -> None:
    if isinstance(self.cadence, str):
      self.cadence = StrategyCadence(self.cadence)


@dataclass
class StrategyOutput:
  """策略 Step 输出。"""

  trade_intents: List[TradeIntent] = field(default_factory=list)
  runtime_state_patch: Optional[RuntimeStatePatch] = None
  decision_tags: List[str] = field(default_factory=list)
  trace_payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderStateEvent:
  """结构化订单状态事件。"""

  order_id: Optional[str]
  status: str
  request: Any = None
  error_message: Optional[str] = None
  filled_volume: int = 0
  metadata: Dict[str, Any] = field(default_factory=dict)
  timestamp: Optional[datetime] = None

  @classmethod
  def from_raw(cls, source: Any) -> "OrderStateEvent":
    request = _extract(source, "request")
    metadata = _extract(request, "metadata", {}) or {}
    return cls(
      order_id=_extract(source, "order_id"),
      status=str(_extract(source, "status", "") or "").split(".")[-1].upper(),
      request=request,
      error_message=_extract(source, "error_message"),
      filled_volume=int(_extract(source, "filled_volume", 0) or 0),
      metadata=dict(metadata),
      timestamp=_extract(source, "last_update_time") or _extract(source, "submit_time"),
    )


@dataclass
class TradeExecutionEvent:
  """结构化成交事件。"""

  order_id: Optional[str]
  instrument_code: str
  trade_type: str
  price: float
  volume: int
  trade_time: Optional[datetime] = None
  metadata: Dict[str, Any] = field(default_factory=dict)

  @classmethod
  def from_raw(cls, source: Any) -> "TradeExecutionEvent":
    return cls(
      order_id=_extract(source, "order_id"),
      instrument_code=str(_extract(source, "instrument_code", "") or ""),
      trade_type=str(_extract(source, "trade_type", "") or "").split(".")[-1].upper(),
      price=float(_extract(source, "price", 0.0) or 0.0),
      volume=int(_extract(source, "volume", 0) or 0),
      trade_time=_extract(source, "trade_time"),
      metadata=dict(_extract(source, "metadata", {}) or {}),
    )


def _extract(source: Any, key: str, default: Any = None) -> Any:
  if source is None:
    return default
  if isinstance(source, dict):
    return source.get(key, default)
  return getattr(source, key, default)


@dataclass
class StrategyStateEvent:
  """策略状态变更事件"""

  run_id: str
  timestamp: datetime
  persist: bool = True
  key: Optional[str] = None
  value: Any = None
  changes: Optional[Dict[str, Any]] = None
  state: Dict[str, Any] = field(default_factory=dict)


class StrategyStateProxy:
  """策略状态代理，支持属性赋值触发事件"""

  def __init__(
    self,
    on_change: Callable[..., None],
    initial: Optional[Dict[str, Any]] = None,
    debounce_ms: int = 0,
  ) -> None:
    super().__setattr__("_data", dict(initial or {}))
    super().__setattr__("_on_change", on_change)
    super().__setattr__("_debounce_ms", max(0, int(debounce_ms)))
    super().__setattr__("_debounce_task", None)
    super().__setattr__("_pending_changes", {})
    super().__setattr__("_pending_persist", False)
    super().__setattr__("_pending_notify", False)
    super().__setattr__("_silent_depth", 0)
    super().__setattr__("_silent_changes", {})
    super().__setattr__("_silent_persist", True)
    super().__setattr__("_silent_notify", True)
    super().__setattr__("_silent_flush", False)

  def __getattr__(self, name: str) -> Any:
    if name.startswith("_"):
      return super().__getattribute__(name)
    return self._data.get(name)

  def __setattr__(self, name: str, value: Any) -> None:
    if name.startswith("_"):
      super().__setattr__(name, value)
      return
    self._data[name] = value
    self._emit_change(key=name, value=value, persist=True, notify=True)

  def __getitem__(self, key: str) -> Any:
    return self._data.get(key)

  def __setitem__(self, key: str, value: Any) -> None:
    self._data[key] = value
    self._emit_change(key=key, value=value, persist=True, notify=True)

  def get(self, key: str, default: Any = None) -> Any:
    return self._data.get(key, default)

  def set(self, key: str, value: Any, *, persist: bool = True, notify: bool = True) -> None:
    self._data[key] = value
    self._emit_change(key=key, value=value, persist=persist, notify=notify)

  def update(
    self,
    updates: Dict[str, Any],
    *,
    persist: bool = True,
    notify: bool = True,
  ) -> None:
    if not updates:
      return
    self._data.update(updates)
    self._emit_change(changes=dict(updates), persist=persist, notify=notify)

  def replace(self, state: Optional[Dict[str, Any]], *, notify: bool = False) -> None:
    if state is None:
      return
    self._data = dict(state)
    self._emit_change(changes=dict(self._data), persist=False, notify=notify, immediate=True)

  def to_dict(self) -> Dict[str, Any]:
    return dict(self._data)

  def set_debounce_ms(self, debounce_ms: int) -> None:
    """设置事件节流时间（毫秒），0 表示不节流"""
    self._debounce_ms = max(0, int(debounce_ms))

  def silent(
    self,
    *,
    persist: bool = True,
    notify: bool = True,
    flush_on_exit: bool = False,
  ):
    """静默模式：禁用事件触发，必要时退出时合并触发一次"""
    return _StateSilentContext(self, persist, notify, flush_on_exit)

  def _emit_change(
    self,
    *,
    key: Optional[str] = None,
    value: Any = None,
    changes: Optional[Dict[str, Any]] = None,
    persist: bool = True,
    notify: bool = True,
    immediate: bool = False,
  ) -> None:
    if not notify:
      return

    if changes is None and key is not None:
      changes = {key: value}
    if not changes:
      return

    if self._silent_depth > 0:
      self._silent_changes.update(changes)
      self._silent_persist = self._silent_persist or persist
      self._silent_notify = self._silent_notify or notify
      return

    if immediate or self._debounce_ms <= 0:
      if self._debounce_task and not self._debounce_task.done():
        self._debounce_task.cancel()
      self._flush_pending_changes(extra_changes=changes, persist=persist, notify=notify)
      return

    self._pending_changes.update(changes)
    self._pending_persist = self._pending_persist or persist
    self._pending_notify = self._pending_notify or notify

    if self._debounce_task and not self._debounce_task.done():
      return

    try:
      loop = asyncio.get_running_loop()
    except RuntimeError:
      self._flush_pending_changes()
      return

    self._debounce_task = loop.create_task(self._debounce_flush())

  async def _debounce_flush(self) -> None:
    try:
      await asyncio.sleep(self._debounce_ms / 1000)
      self._flush_pending_changes()
    except asyncio.CancelledError:
      return

  def _flush_pending_changes(
    self,
    *,
    extra_changes: Optional[Dict[str, Any]] = None,
    persist: Optional[bool] = None,
    notify: Optional[bool] = None,
  ) -> None:
    if extra_changes:
      self._pending_changes.update(extra_changes)
      if persist is not None:
        self._pending_persist = self._pending_persist or persist
      if notify is not None:
        self._pending_notify = self._pending_notify or notify

    if not self._pending_changes:
      return

    changes = dict(self._pending_changes)
    pending_persist = self._pending_persist or False
    pending_notify = self._pending_notify or False

    self._pending_changes.clear()
    self._pending_persist = False
    self._pending_notify = False

    self._on_change(changes=changes, persist=pending_persist, notify=pending_notify)

  def _enter_silent(
    self,
    *,
    persist: bool,
    notify: bool,
    flush_on_exit: bool,
  ) -> None:
    if self._silent_depth == 0:
      self._silent_changes = {}
      self._silent_persist = persist
      self._silent_notify = notify
      self._silent_flush = flush_on_exit
    else:
      self._silent_persist = self._silent_persist or persist
      self._silent_notify = self._silent_notify or notify
      self._silent_flush = self._silent_flush or flush_on_exit
    self._silent_depth += 1

  def _exit_silent(self) -> None:
    if self._silent_depth <= 0:
      return
    self._silent_depth -= 1
    if self._silent_depth != 0:
      return

    if self._silent_flush and self._silent_changes:
      self._emit_change(
        changes=dict(self._silent_changes),
        persist=self._silent_persist,
        notify=self._silent_notify,
        immediate=True,
      )

    self._silent_changes = {}
    self._silent_persist = True
    self._silent_notify = True
    self._silent_flush = False


class _StateSilentContext:
  """策略状态静默上下文"""

  def __init__(
    self,
    state: StrategyStateProxy,
    persist: bool,
    notify: bool,
    flush_on_exit: bool,
  ) -> None:
    self._state = state
    self._persist = persist
    self._notify = notify
    self._flush_on_exit = flush_on_exit

  def __enter__(self) -> StrategyStateProxy:
    self._state._enter_silent(
      persist=self._persist,
      notify=self._notify,
      flush_on_exit=self._flush_on_exit,
    )
    return self._state

  def __exit__(self, exc_type, exc, tb) -> bool:
    self._state._exit_silent()
    return False


@dataclass
class StrategyContext:
  """策略运行上下文"""

  run_id: str
  mode: StrategyRunMode
  instruments: List[str]
  parameters: Dict[str, Any]
  initial_capital: float = 1000000.0
  backtest_start_time: Optional[datetime] = None  # 回测数据起始时间(仅回测模式)
  backtest_end_time: Optional[datetime] = None  # 回测数据结束时间(仅回测模式)
  current_time: Optional[datetime] = None
  backtest_id: Optional[str] = None  # 回测记录ID (StrategyBacktest.id，仅回测模式)
  backtest_version: Optional[int] = None  # 回测版本号，仅回测模式

  def __post_init__(self) -> None:
    if isinstance(self.mode, str):
      mode_value = str(self.mode).strip()
      try:
        self.mode = StrategyRunMode(mode_value)
      except ValueError:
        try:
          self.mode = StrategyRunMode[self.mode.upper()]
        except KeyError as exc:
          raise ValueError(f"Invalid strategy run mode: {mode_value}") from exc


class StrategyBase(ABC):
  """策略抽象基类 - 定义策略的统一接口和生命周期"""

  # 策略标的范围（用于展示与校验）
  INSTRUMENT_SCOPE = StrategyInstrumentScope.MULTI

  def __init__(self, context: StrategyContext):
    self.context = context
    self.is_initialized = False
    self.is_running = False
    self.logger = logging.getLogger(f"Strategy-{context.run_id}")
    self.trade_intents: List[TradeIntent] = []
    self.positions: Dict[str, float] = {}
    self.orders: List[Dict[str, Any]] = []
    self.state = StrategyStateProxy(
      on_change=self._on_state_change,
      initial=self._build_state_defaults(),
    )
    self._state_subscribers: List[asyncio.Queue] = []

  @property
  @abstractmethod
  def name(self) -> str:
    """策略名称"""
    pass

  @property
  @abstractmethod
  def version(self) -> str:
    """策略版本"""
    pass

  @property
  @abstractmethod
  def description(self) -> str:
    """策略描述"""
    pass

  @classmethod
  @abstractmethod
  def get_parameter_schema(cls) -> "ParameterSchema":
    """获取策略参数 Schema 定义（返回 Pydantic 对象）"""
    pass

  @classmethod
  def get_state_schema(cls) -> "StateSchema":
    """获取策略状态 Schema 定义（独立结构）"""
    from core.state_schema import StateSchema

    return StateSchema(type="object", properties={})

  @classmethod
  def get_data_requirements(cls) -> Dict[str, Any]:
    """获取策略的数据订阅需求（固定声明，运行层据此订阅数据）"""
    return {"use_tick_data": True, "periods": ["1m", "1d"]}

  @abstractmethod
  async def on_init(self) -> None:
    """
    策略初始化回调
    在策略开始运行前调用，用于初始化状态、订阅数据等
    """
    pass

  @abstractmethod
  async def step(self, input: StrategyInput) -> StrategyOutput:
    """策略唯一决策入口。"""
    pass

  async def warmup(self, input: StrategyInput) -> None:
    """回测预热入口，只允许更新指标窗口和算法内部状态，不输出交易意图。"""
    return None

  async def on_order(self, event: OrderStateEvent) -> Optional[RuntimeStatePatch]:
    """订单状态更新回调；返回的算法状态补丁会由执行器消费。

    如果策略已直接修改 self.state，返回的补丁必须保持幂等。
    """
    return None

  async def on_trade(self, event: TradeExecutionEvent) -> Optional[RuntimeStatePatch]:
    """成交回调；返回的算法状态补丁会由执行器消费。

    如果策略已直接修改 self.state，返回的补丁必须保持幂等。
    """
    return None

  @abstractmethod
  async def on_stop(self) -> None:
    """
    策略停止回调
    在策略停止时调用，用于清理资源、保存状态等
    """
    pass

  async def initialize(self) -> None:
    """初始化策略"""
    if self.is_initialized:
      return

    try:
      await self.on_init()
      self.is_initialized = True
      self.logger.info(f"策略 {self.name} v{self.version} 初始化成功")
    except Exception as e:
      self.logger.error(f"策略初始化失败: {e}")
      raise

  async def start(self) -> None:
    """启动策略"""
    if not self.is_initialized:
      await self.initialize()

    self.is_running = True
    self.logger.info(f"策略 {self.name} 启动成功")

  async def stop(self) -> None:
    """停止策略"""
    if not self.is_running:
      return

    try:
      await self.on_stop()
      self.is_running = False
      self.logger.info(f"策略 {self.name} 停止成功")
    except Exception as e:
      self.logger.error(f"策略停止失败: {e}")
      raise

  def get_parameter(self, key: str, default: Any = None) -> Any:
    """获取策略参数"""
    return self.context.parameters.get(key, default)

  # ======== 策略状态管理 ========

  def _build_state_defaults(self) -> Dict[str, Any]:
    """根据 state_schema 构建默认 state"""
    schema = None
    try:
      schema = self.get_state_schema()
    except Exception as exc:
      self.logger.error(
        "Failed to build strategy state defaults from schema",
        extra={
          "event": "state_defaults_error",
          "strategy_name": getattr(self, "name", ""),
          "run_id": self.context.run_id,
          "error": repr(exc),
        },
      )
      return {}

    if not schema:
      return {}

    try:
      return schema.build_defaults()
    except Exception as exc:
      self.logger.error(
        "Failed to build strategy state defaults from schema defaults",
        extra={
          "event": "state_defaults_build_error",
          "strategy_name": getattr(self, "name", ""),
          "run_id": self.context.run_id,
          "error": repr(exc),
          "schema_type": type(schema).__name__,
        },
      )
      return {}

  def apply_state_snapshot(self, state: Optional[Dict[str, Any]]) -> None:
    """应用持久化状态快照（覆盖默认值）"""
    defaults = self._build_state_defaults()
    if state:
      defaults.update(state)
    self.state.replace(defaults, notify=False)

  def subscribe_state(self, maxsize: int = 200) -> asyncio.Queue:
    """订阅策略状态事件"""
    queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
    self._state_subscribers.append(queue)
    return queue

  def unsubscribe_state(self, queue: asyncio.Queue) -> None:
    """取消订阅策略状态事件"""
    self._state_subscribers = [q for q in self._state_subscribers if q is not queue]

  def _broadcast_state_change(
    self,
    *,
    key: Optional[str] = None,
    value: Any = None,
    changes: Optional[Dict[str, Any]] = None,
    persist: bool = True,
  ) -> None:
    """广播策略状态变更事件"""
    if not self._state_subscribers:
      return

    event = StrategyStateEvent(
      run_id=self.context.run_id,
      timestamp=time_utils.now(),
      persist=persist,
      key=key,
      value=value,
      changes=changes,
      state=self.state.to_dict(),
    )

    for queue in self._state_subscribers:
      try:
        queue.put_nowait(event)
      except asyncio.QueueFull:
        try:
          queue.get_nowait()
          queue.put_nowait(event)
        except (asyncio.QueueEmpty, asyncio.QueueFull):
          pass

  def _on_state_change(
    self,
    *,
    key: Optional[str] = None,
    value: Any = None,
    changes: Optional[Dict[str, Any]] = None,
    persist: bool = True,
    notify: bool = True,
  ) -> None:
    """处理状态变更（StrategyStateProxy 回调）"""
    if not notify:
      return
    self._broadcast_state_change(
      key=key,
      value=value,
      changes=changes,
      persist=persist,
    )

  def record_trade_intent(self, intent: TradeIntent) -> TradeIntent:
    """记录策略输出的交易意图。"""
    self.trade_intents.append(intent)
    self.logger.info(
      f"生成交易意图: {intent.direction.value} {intent.instrument_code} "
      f"bucket:{intent.bucket} reason:{intent.reason} confidence:{intent.confidence}"
    )
    return intent

  def log_info(self, message: str) -> None:
    """记录信息日志"""
    self.logger.info(message)

  def log_warning(self, message: str) -> None:
    """记录警告日志"""
    self.logger.warning(message)

  def log_error(self, message: str) -> None:
    """记录错误日志"""
    self.logger.error(message)

  def get_position(self, instrument_code: str) -> float:
    """获取持仓"""
    return self.positions.get(instrument_code, 0.0)

  def set_position(self, instrument_code: str, position: float) -> None:
    """设置持仓"""
    self.positions[instrument_code] = position

  def get_statistics(self) -> Dict[str, Any]:
    """获取策略运行统计信息"""
    return {
      "trade_intents_count": len(self.trade_intents),
      "orders_count": len(self.orders),
      "positions": dict(self.positions),
      "is_running": self.is_running,
      "is_initialized": self.is_initialized,
    }
