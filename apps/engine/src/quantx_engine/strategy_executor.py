"""
Engine 策略执行器 - 专注于策略运行的并发执行和资源管理

职责：
1. 管理策略运行实例的并发执行
2. 线程池/协程池资源管理
3. 实时状态监控和心跳管理
4. 异常处理和资源清理

不负责：
- 策略发现和协调（StrategyManager）
- API 层交互（StrategyManager）
- 持久化策略模板（StrategyManager）
"""

import asyncio
import inspect
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Type

from quantx_domain.brokers.backtest import BacktestBroker
from quantx_domain.brokers.base import BrokerBase, OrderRequest, OrderStatus, Position
from quantx_domain.brokers.base import OrderType as BrokerOrderType
from quantx_domain.brokers.simulator import SimulatorBroker
from quantx_domain.strategies.base import (
  OrderStateEvent,
  StrategyBase,
  StrategyCadence,
  StrategyContext,
  StrategyInput,
  StrategyOutput,
  StrategyRunMode,
  TradeExecutionEvent,
  TradeIntent,
  TradeIntentDirection,
  TradeIntentExecutionMode,
  TradeIntentPriority,
)
from quantx_domain.trading import (
  EXIT_PLAN_BOOK_STATE_KEY,
  AshareDataContextProvider,
  AShareMarketRules,
  ContextRiskLayer,
  DecisionTrace,
  ExitDecision,
  ExitEvaluationContext,
  ExitPlanBook,
  ExitPlanEvaluator,
  ExitPlanTemplate,
  ExitPriceReference,
  ExitRuleType,
  ExitStrategyRegistry,
  ExitT1Policy,
  MarketDataSnapshot,
  OrderRiskDecision,
  OrderSizer,
  PortfolioOrchestrationLayer,
  PositionAdjustmentLayer,
  RiskAction,
  TradingRiskChecker,
)
from quantx_domain.trading.decision_trace import (
  summarize_intent,
  summarize_strategy_input,
)
from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.core.brokers.live import LiveBroker
from quantx_infrastructure.core.data import (
  DataAdapter,
  HistoricalDataAdapter,
  adapter_manager,
)
from quantx_infrastructure.core.market_data_manager import MarketDataManager
from quantx_infrastructure.core.runtime_log_manager import RuntimeLogManager
from quantx_infrastructure.core.strategy_performance import (
  StrategyPerformanceRecorder,
  StrategyPerformanceService,
)
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.models import ExecutionMetrics, KLine
from quantx_infrastructure.services.auto_exit_plan_service import AutoExitPlanService
from quantx_infrastructure.services.trading_time_service import TradingDateHelper

if TYPE_CHECKING:
  from quantx_infrastructure.core.market_data_manager import MarketDataManager
  from quantx_infrastructure.core.runtime_log_manager import RuntimeLogManager
  from quantx_infrastructure.core.runtime_state_manager import RuntimeStateManager


class ExecutionStatus(Enum):
  """执行状态"""

  PENDING = "PENDING"
  STARTING = "STARTING"
  RUNNING = "RUNNING"
  STOPPING = "STOPPING"
  STOPPED = "STOPPED"
  COMPLETED = "COMPLETED"
  ERROR = "ERROR"
  PAUSED = "PAUSED"


@dataclass
class StrategyRuntime:
  """策略运行时对象"""

  #: 运行实例ID
  run_id: str
  #: 运行实例名称
  name: str
  #: 策略模板ID
  strategy_id: int
  #: 策略类
  strategy_class: Type[StrategyBase]
  #: 运行上下文（参数、模式、标的、时间范围）
  context: StrategyContext
  #: 策略实例
  strategy: Optional[StrategyBase] = None
  #: Broker 实例
  broker: Optional[BrokerBase] = None
  #: 数据适配器
  data_adapter: Optional[DataAdapter] = None
  #: 市场数据管理器（统一订阅与历史查询）
  market_data_manager: Optional["MarketDataManager"] = None
  performance_recorder: Optional[StrategyPerformanceRecorder] = None
  #: 当前执行状态
  status: ExecutionStatus = ExecutionStatus.PENDING
  #: 运行指标
  metrics: Optional[ExecutionMetrics] = None
  #: 错误信息
  error_message: Optional[str] = None
  #: 运行主任务
  task: Optional[asyncio.Task] = None
  #: 串行事件队列
  event_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
  #: 运行进程ID
  pid: int = field(default_factory=os.getpid)
  #: 运行主机名
  host: str = field(
    default_factory=lambda: (
      os.uname().nodename
      if os.name != "nt"
      else os.environ.get("COMPUTERNAME", "unknown")
    )
  )

  # === 订阅广播相关字段 ===
  #: 回测模式数据广播节流间隔（毫秒）
  broadcast_throttle_ms: int = 100
  #: 上次广播时间戳（用于节流）
  _last_broadcast_time: Optional[datetime] = field(default=None, repr=False)
  #: 状态管理器（用于持久化日志、持仓、订单等）
  state_manager: Optional["RuntimeStateManager"] = field(default=None, repr=False)
  #: 日志管理器（统一日志缓存与订阅）
  log_manager: Optional["RuntimeLogManager"] = field(default=None, repr=False)
  #: 最新行情快照（用于下单风控和回测撮合）
  latest_market_data: Dict[str, MarketDataSnapshot] = field(
    default_factory=dict, repr=False
  )
  #: 最近订单回报时间（用于 broker 健康状态）
  last_order_report_at: Optional[datetime] = field(default=None, repr=False)
  #: 最近成交回报时间（用于 broker 健康状态）
  last_trade_report_at: Optional[datetime] = field(default=None, repr=False)
  #: 最近任意 broker 回报时间（用于 broker 健康状态）
  last_broker_report_at: Optional[datetime] = field(default=None, repr=False)
  #: 等待人工确认的交易意图，仅在运行进程内保留完整对象
  pending_approvals: Dict[str, TradeIntent] = field(default_factory=dict, repr=False)
  #: 审批串行锁，避免重复点击导致重复下单
  approval_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
  #: 实时订阅按标的归组，支持运行中动态增删标的。
  realtime_subscription_ids: Dict[str, List[str]] = field(
    default_factory=dict, repr=False
  )
  #: 动态订阅变更锁，避免持仓同步与停机清理互相穿透。
  realtime_subscription_lock: asyncio.Lock = field(
    default_factory=asyncio.Lock, repr=False
  )
  #: 已确认但尚未完全由成交持仓接管的 T 入场金额预留。
  t_trade_entry_reservations: Dict[str, Dict[str, Any]] = field(
    default_factory=dict, repr=False
  )
  #: Engine-owned automatic exit plans, persisted outside strategy-owned state.
  exit_plan_book: ExitPlanBook = field(default_factory=ExitPlanBook, repr=False)

  @property
  def mode(self) -> StrategyRunMode:
    """获取运行模式"""
    return self.context.mode

  @property
  def instruments(self) -> List[str]:
    """便捷访问标的列表"""
    return self.context.instruments

  @property
  def parameters(self) -> Dict[str, Any]:
    """便捷访问策略参数"""
    return self.context.parameters

  @property
  def start_time(self) -> Optional[datetime]:
    """运行实例开始时间"""
    return self.metrics.start_time if self.metrics else None

  @property
  def stop_time(self) -> Optional[datetime]:
    """运行实例结束时间"""
    return self.metrics.end_time if self.metrics else None

  def get_metrics(self) -> Dict[str, Any]:
    """Return JSON-serializable runtime metrics for persistence."""
    if self.metrics is None:
      return {}

    self.metrics.end_time = self.metrics.end_time or time_utils.now()
    if self.broker and hasattr(self.broker, "get_performance_metrics"):
      perf_metrics = self.broker.get_performance_metrics()
      if isinstance(perf_metrics, dict):
        self.metrics.performance = perf_metrics
        self.metrics.max_drawdown = perf_metrics.get("max_drawdown", 0.0)
        self.metrics.max_drawdown_pct = perf_metrics.get("max_drawdown_pct", 0.0)
        self.metrics.win_rate = perf_metrics.get("win_rate", 0.0)
        self.metrics.win_rate_pct = perf_metrics.get("win_rate_pct", 0.0)
        self.metrics.sharpe_ratio = perf_metrics.get("sharpe_ratio", 0.0)
        self.metrics.total_return_pct = perf_metrics.get("total_return_pct", 0.0)
        self.metrics.total_pnl = (
          perf_metrics.get("final_equity", self.metrics.initial_capital)
          - self.metrics.initial_capital
        )
        self.metrics.current_capital = perf_metrics.get(
          "final_equity", self.metrics.initial_capital
        )
        self.metrics.trades_executed = perf_metrics.get("total_trades", 0)

    return self.metrics.model_dump(mode="json")

  def should_broadcast_data(self) -> bool:
    """判断是否应该广播数据（用于节流）"""
    # 实时模式不节流
    if self.context.mode != StrategyRunMode.BACKTEST:
      return True

    # 回测模式：检查节流间隔
    now = time_utils.now()
    if self._last_broadcast_time is None:
      return True

    elapsed_ms = (now - self._last_broadcast_time).total_seconds() * 1000
    return elapsed_ms >= self.broadcast_throttle_ms

  def subscribe_data(
    self, data_type: str = "all", *, include_recent: bool = True
  ) -> asyncio.Queue:
    """订阅市场数据，返回一个独立的队列

    Args:
        data_type: 订阅类型，"tick", "kline", 或 "all"
        include_recent: 是否推送最近缓存的数据

    Returns:
        订阅者专属队列
    """
    if not self.market_data_manager:
      return asyncio.Queue(maxsize=1000)
    return self.market_data_manager.subscribe(
      run_id=self.run_id,
      data_type=data_type,
      maxsize=1000,
      include_recent=include_recent,
    )

  def unsubscribe_data(self, queue: asyncio.Queue) -> None:
    """取消市场数据订阅"""
    if not self.market_data_manager:
      return
    self.market_data_manager.unsubscribe(run_id=self.run_id, queue=queue)

  def subscribe_logs(self, include_history: bool = True) -> asyncio.Queue:
    """订阅日志，返回一个独立的队列"""
    if not self.log_manager:
      return asyncio.Queue(maxsize=500)
    return self.log_manager.subscribe(
      run_id=self.run_id,
      maxsize=500,
      include_history=include_history,
    )

  def unsubscribe_logs(self, queue: asyncio.Queue) -> None:
    """取消日志订阅"""
    if not self.log_manager:
      return
    self.log_manager.unsubscribe(run_id=self.run_id, queue=queue)

  def broadcast_tick(self, tick) -> None:
    """广播 Tick 数据到所有订阅者"""
    if not self.should_broadcast_data():
      return

    self._last_broadcast_time = time_utils.now()
    # 广播到所有订阅了 tick 或 all 的订阅者
    if self.market_data_manager:
      self.market_data_manager.publish_tick(self.run_id, tick)

  def broadcast_kline(self, kline) -> None:
    """广播 K线 数据到所有订阅者"""
    if not self.should_broadcast_data():
      return

    self._last_broadcast_time = time_utils.now()
    # 广播到所有订阅了 kline 或 all 的订阅者
    if self.market_data_manager:
      self.market_data_manager.publish_kline(self.run_id, kline)

  def broadcast_log(self, level: str, message: str, source: str = "strategy") -> None:
    """广播日志到所有订阅者"""
    if not self.log_manager:
      return
    self.log_manager.append(
      run_id=self.run_id,
      level=level,
      message=message,
      source=source,
    )


@dataclass
class ExecutionContextSnapshot:
  """Executor-built domain context shared by strategy input and order routing."""

  account: Dict[str, Any]
  positions: Dict[str, Any]
  bucket_ledger: Dict[str, Any]
  portfolio_state: Dict[str, Any]
  open_orders: List[Dict[str, Any]]
  market_context: Dict[str, Any]
  risk_caps: Dict[str, Any]
  position_profile: Dict[str, Any]
  runtime_state: Dict[str, Any]
  parameters: Dict[str, Any]


class StrategyExecutor:
  """
  策略执行器 - 专注于策略运行的并发执行和资源管理

  职责：
  - 创建和管理策略运行实例
  - 并发执行控制（线程池）
  - 资源分配和回收（Broker、DataAdapter）
  - 实时状态监控和心跳
  - 异常处理和恢复

  特点：
  - 可创建多个 Executor 实例
  - 支持资源隔离
  - 不负责持久化（由调用方处理）
  """

  def __init__(
    self,
    max_workers: int = 10,
    *,
    exit_strategy_registry: Optional[ExitStrategyRegistry] = None,
  ):
    """
    初始化策略执行器

    Args:
        max_workers: 最大并发执行数量
    """
    self.max_workers = max_workers
    self.runs: Dict[str, StrategyRuntime] = {}
    self.thread_pool = ThreadPoolExecutor(max_workers=max_workers)
    self.logger = logging.getLogger("StrategyExecutor")
    self._shutdown_event = asyncio.Event()
    self.log_manager = RuntimeLogManager()
    self.market_data_manager = MarketDataManager()
    self.exit_strategy_registry = (
      exit_strategy_registry or ExitStrategyRegistry.builtins()
    )

  def register_exit_strategy(self, strategy: str, evaluator: Any) -> None:
    """Register a sell trigger once for new and restored runtime plans."""

    self.exit_strategy_registry.register(strategy, evaluator)

  def _runtime_log(
    self,
    runtime: StrategyRuntime,
    level: str,
    message: str,
    source: str = "executor",
  ) -> None:
    """Write a run-scoped execution log and mirror it to the executor logger."""
    normalized_level = str(level or "INFO").upper()
    logger_message = f"[{runtime.run_id}] {message}"
    if normalized_level == "ERROR":
      self.logger.error(logger_message)
    elif normalized_level == "WARNING":
      self.logger.warning(logger_message)
    elif normalized_level == "DEBUG":
      self.logger.debug(logger_message)
    else:
      self.logger.info(logger_message)

    try:
      runtime.broadcast_log(normalized_level, message, source=source)
    except Exception as exc:
      self.logger.debug("写入运行执行日志失败: %s", exc)

  def create(
    self,
    run_id: str,
    name: Optional[str] = None,
    strategy_id: Optional[int] = None,
    strategy_class: Optional[Type[StrategyBase]] = None,
    context: Optional[StrategyContext] = None,
  ) -> StrategyRuntime:
    """
    创建策略运行实例（纯内存操作）

    Args:
        run_id: 运行实例ID（由调用方生成）
        strategy_id: 策略模板ID
        strategy_class: 策略类
        context: 策略上下文

    Returns:
        StrategyRuntime: 运行时对象

    Note:
        - 不负责参数验证（由 StrategyManager 完成）
        - 不负责持久化（由 StrategyManager 完成）
        - 仅创建运行时对象并加入管理
    """
    if strategy_id is None or strategy_class is None or context is None:
      raise TypeError("strategy_id, strategy_class and context are required")

    runtime_name = name or f"Strategy-{strategy_id}"

    # 创建策略运行时对象
    strategy_runtime = StrategyRuntime(
      run_id=run_id,
      name=runtime_name,
      strategy_id=strategy_id,
      strategy_class=strategy_class,
      context=context,
      metrics=ExecutionMetrics(
        start_time=time_utils.now(),
        last_heartbeat=time_utils.now(),
        initial_capital=context.initial_capital,
        current_capital=context.initial_capital,
      ),
      log_manager=self.log_manager,
      market_data_manager=self.market_data_manager,
    )
    strategy_runtime.exit_plan_book = ExitPlanBook(
      evaluator=ExitPlanEvaluator(self.exit_strategy_registry)
    )

    self.runs[run_id] = strategy_runtime

    self.logger.info(f"创建策略运行时: {run_id}")
    return strategy_runtime

  async def start(self, run_id: str) -> bool:
    """
    启动策略运行

    Args:
        run_id: 运行实例ID

    Returns:
        bool: 是否启动成功

    Note:
        状态变更由调用方（StrategyManager）负责持久化
    """
    if run_id not in self.runs:
      self.logger.error(f"策略运行不存在: {run_id}")
      return False

    runtime = self.runs[run_id]

    if runtime.status in [ExecutionStatus.RUNNING, ExecutionStatus.STARTING]:
      self.logger.warning(f"策略运行已在运行: {run_id}")
      return True

    try:
      # 更新状态
      runtime.status = ExecutionStatus.STARTING

      # 创建策略对象
      runtime.strategy = runtime.strategy_class(runtime.context)

      # 初始化状态管理器（回测模式不持久化，仅维护策略额度与状态）
      from quantx_infrastructure.core.runtime_state_manager import RuntimeStateManager

      enable_reserve = bool(runtime.context.parameters.get("enable_reserve", True))
      runtime.state_manager = RuntimeStateManager(
        run_id=run_id,
        persist_enabled=runtime.context.mode != StrategyRunMode.BACKTEST,
        log_dir=os.path.join("logs", "strategy", runtime.context.mode.value),
        enable_reserve=enable_reserve,
      )

      if runtime.context.mode == StrategyRunMode.BACKTEST:
        # 回测模式：配置为文件存储
        if runtime.context.backtest_id:
          runtime.state_manager.set_backtest_mode(
            runtime.context.backtest_id,
            backtest_version=runtime.context.backtest_version,
          )

      # 附加日志广播 Handler，并把每个运行实例绑定到独立日志文件。
      if runtime.log_manager:
        runtime.log_manager.configure_file(
          run_id=runtime.run_id,
          file_path=(
            runtime.state_manager.get_log_file_path() if runtime.state_manager else None
          ),
        )
        runtime.log_manager.attach_handler(
          run_id=runtime.run_id,
          logger=runtime.strategy.logger,
          source=getattr(runtime.strategy, "name", "strategy"),
        )
        self._runtime_log(
          runtime,
          "INFO",
          (
            f"策略运行启动准备: mode={runtime.context.mode.value}, "
            f"backtest_id={runtime.context.backtest_id or '-'}, "
            f"backtest_version={runtime.context.backtest_version or '-'}"
          ),
        )

      await runtime.state_manager.start()

      # 恢复之前的状态（如果有）
      restored_state = await runtime.state_manager.restore()
      runtime.exit_plan_book = ExitPlanBook.from_dict(
        (restored_state.get("custom") or {}).get(EXIT_PLAN_BOOK_STATE_KEY)
        if restored_state
        else None,
        evaluator=ExitPlanEvaluator(self.exit_strategy_registry),
      )
      if runtime.strategy and hasattr(runtime.strategy, "apply_state_snapshot"):
        strategy_snapshot = dict((restored_state or {}).get("custom") or {})
        strategy_snapshot.pop(EXIT_PLAN_BOOK_STATE_KEY, None)
        runtime.strategy.apply_state_snapshot(strategy_snapshot)
      await self._restore_pending_manual_approvals(runtime)
      self._restore_t_trade_entry_reservations(runtime)
      if restored_state.get("positions"):
        self.logger.info(f"恢复持仓: {len(restored_state['positions'])} 个")
      if restored_state.get("active_orders"):
        self.logger.info(f"恢复活动订单: {len(restored_state['active_orders'])} 个")

      # 初始化策略额度（新运行实例）
      if runtime.state_manager:
        account = runtime.state_manager.get_account()
        positions = runtime.state_manager.get_all_positions()
        if (
          account.get("cash", 0.0) <= 0
          and account.get("frozen_cash", 0.0) <= 0
          and account.get("total_asset", 0.0) <= 0
          and not positions
        ):
          runtime.state_manager.update_account(
            cash=runtime.context.initial_capital,
            frozen_cash=0.0,
            total_asset=runtime.context.initial_capital,
          )
        if not positions:
          initial_metadata = dict(
            runtime.context.parameters.get("initial_portfolio_metadata")
            or runtime.context.parameters.get("initial_instrument_metadata")
            or {}
          )
          if initial_metadata:
            self._sync_dynamic_holding_inventory(runtime, initial_metadata)
          else:
            self._seed_bucket_ledger_from_parameters(runtime)

      # 根据模式创建 Broker 和 DataAdapter
      await self._setup_broker_and_data(runtime)
      self._seed_simulated_broker_positions(runtime)
      runtime.performance_recorder = StrategyPerformanceRecorder(
        run_id=run_id,
        mode=runtime.context.mode,
        backtest_id=runtime.context.backtest_id,
        initial_capital=runtime.context.initial_capital,
      )

      # 启动策略执行任务
      if runtime.state_manager and runtime.strategy:
        await runtime.state_manager.start_state_sync(runtime.strategy)
      runtime.task = asyncio.create_task(self._run_strategy_loop(runtime))

      # 启动事件处理循环
      asyncio.create_task(self._process_event_queue(runtime))

      # 更新状态
      runtime.status = ExecutionStatus.RUNNING

      self._runtime_log(runtime, "SUCCESS", f"策略运行启动成功: {run_id}")
      return True

    except Exception as e:
      runtime.status = ExecutionStatus.ERROR
      runtime.error_message = str(e)
      self._runtime_log(runtime, "ERROR", f"启动策略运行失败: {run_id}, 错误: {e}")
      return False

  def _seed_bucket_ledger_from_parameters(self, runtime) -> None:
    """Initialize core/swing bucket attribution from strategy parameters."""
    if not runtime or not runtime.state_manager:
      return

    params = dict(getattr(runtime.context, "parameters", {}) or {})
    total_shares = int(params.get("position_shares", 0) or 0)
    available_shares = max(
      0,
      min(
        total_shares,
        int(params.get("position_available_shares", total_shares) or 0),
      ),
    )
    locked_core_shares = max(0, int(params.get("locked_core_shares", 0) or 0))
    swing_shares = max(0, int(params.get("swing_shares", 0) or 0))
    raw_core_shares = params.get("core_shares")
    core_shares = (
      max(0, int(raw_core_shares or 0))
      if raw_core_shares is not None
      else max(0, total_shares - locked_core_shares - swing_shares)
    )
    attributed_total = locked_core_shares + core_shares + swing_shares
    if attributed_total <= 0:
      return

    instrument_code = str(params.get("instrument_code", "") or "")
    if not instrument_code:
      stock_codes = params.get("stockCodes", params.get("stock_codes", ""))
      if isinstance(stock_codes, list):
        instrument_code = str(stock_codes[0] if stock_codes else "")
      else:
        instrument_code = str(stock_codes or "").split(",")[0].strip()
    if not instrument_code:
      return

    avg_price = float(params.get("avg_cost", params.get("base_price", 0.0)) or 0.0)
    last_price = float(params.get("base_price", avg_price) or avg_price)
    position_payload = {
      "long_volume": attributed_total,
      "available_volume": min(available_shares, attributed_total),
      "frozen_volume": 0,
      "today_buy_volume": 0,
      "long_avg_price": avg_price,
      "avg_price": avg_price,
      "last_price": last_price,
      "market_value": attributed_total * (last_price or avg_price),
    }
    runtime.state_manager.update_position(instrument_code, **position_payload)

    remaining_available = min(available_shares, attributed_total)

    def bucket_payload(volume):
      nonlocal remaining_available
      volume = max(0, int(volume or 0))
      bucket_available = min(volume, remaining_available)
      remaining_available -= bucket_available
      return {
        "total_volume": volume,
        "available_volume": bucket_available,
        "frozen_volume": 0,
        "today_buy_volume": max(0, volume - bucket_available),
        "avg_price": avg_price,
        "last_price": last_price,
        "market_value": volume * (last_price or avg_price),
      }

    runtime.state_manager.seed_bucket_positions(
      instrument_code,
      {
        "locked_core": bucket_payload(locked_core_shares),
        "core": bucket_payload(core_shares),
        "swing": bucket_payload(swing_shares),
      },
    )

  def _seed_simulated_broker_positions(self, runtime) -> None:
    """Seed backtest/paper brokers with configured initial holdings."""
    if (
      not runtime
      or runtime.context.mode not in {StrategyRunMode.BACKTEST, StrategyRunMode.PAPER}
      or not runtime.broker
      or not hasattr(runtime.broker, "positions")
      or not runtime.state_manager
    ):
      return

    positions = runtime.state_manager.get_all_positions()
    if not positions:
      return

    seeded = 0
    for instrument_code, pos in positions.items():
      if not instrument_code:
        continue
      long_volume = int(pos.get("long_volume", pos.get("available_volume", 0)) or 0)
      available_volume = int(pos.get("available_volume", long_volume) or 0)
      if long_volume <= 0 and available_volume <= 0:
        continue
      last_price = float(
        pos.get(
          "last_price",
          pos.get("avg_price", pos.get("long_avg_price", 0.0)),
        )
        or 0.0
      )
      avg_price = float(
        pos.get("long_avg_price", pos.get("avg_price", last_price)) or 0.0
      )
      runtime.broker.positions[instrument_code] = Position(
        instrument_code=instrument_code,
        long_volume=long_volume,
        available_volume=min(available_volume, long_volume),
        frozen_volume=int(pos.get("frozen_volume", 0) or 0),
        today_buy_volume=int(pos.get("today_buy_volume", 0) or 0),
        long_avg_price=avg_price,
        market_value=float(pos.get("market_value", long_volume * last_price) or 0.0),
        pnl=float(pos.get("pnl", 0.0) or 0.0),
        last_price=last_price,
      )
      seeded += 1

    if seeded:
      self.logger.info(
        f"{runtime.context.mode.value} Broker 初始持仓已注入: {seeded} 个标的"
      )
    if isinstance(runtime.broker, BacktestBroker):
      params = dict(runtime.context.parameters or {})
      runtime.broker.configure_initial_portfolio(
        cash=float(params.get("initial_cash", runtime.context.initial_capital) or 0.0),
        total_asset=float(
          params.get("initial_total_asset", runtime.context.initial_capital) or 0.0
        ),
        positions=dict(runtime.broker.positions or {}),
      )

  def _sync_dynamic_holding_inventory(
    self,
    runtime: StrategyRuntime,
    instrument_metadata: Optional[Dict[str, Dict[str, Any]]],
  ) -> None:
    """Seed idle dynamic-holdings symbols as core inventory for T+1 substitution."""

    if not runtime.state_manager or not instrument_metadata:
      return
    instrument_states = (
      dict(runtime.strategy.state.get("instrument_states", {}) or {})
      if runtime.strategy
      else {}
    )
    for raw_code, raw_metadata in instrument_metadata.items():
      code = str(raw_code or "").strip().upper()
      metadata = dict(raw_metadata or {})
      if not code or "position_shares" not in metadata:
        continue
      state = dict(instrument_states.get(code, {}) or {})
      active_volume = max(
        0,
        int(state.get("entry_filled_volume", 0) or 0)
        - int(state.get("exit_filled_volume", 0) or 0),
      )
      if (
        active_volume > 0
        or state.get("batch_id")
        or state.get("pending_entry_intent_id")
        or state.get("pending_exit_intent_id")
      ):
        continue

      total_volume = max(0, int(metadata.get("position_shares", 0) or 0))
      available_volume = min(
        total_volume,
        max(0, int(metadata.get("position_available_shares", 0) or 0)),
      )
      frozen_volume = min(
        max(0, total_volume - available_volume),
        max(0, int(metadata.get("position_frozen_shares", 0) or 0)),
      )
      today_buy_volume = max(0, total_volume - available_volume - frozen_volume)
      avg_price = max(0.0, float(metadata.get("position_avg_price", 0.0) or 0.0))
      market_value = max(0.0, float(metadata.get("position_market_value", 0.0) or 0.0))
      last_price = (
        market_value / total_volume
        if total_volume > 0 and market_value > 0
        else avg_price
      )
      position_payload = {
        "long_volume": total_volume,
        "available_volume": available_volume,
        "frozen_volume": frozen_volume,
        "today_buy_volume": today_buy_volume,
        "long_avg_price": avg_price,
        "last_price": last_price,
        "market_value": market_value or total_volume * last_price,
      }
      runtime.state_manager.update_position(code, **position_payload)
      runtime.state_manager.seed_bucket_positions(
        code,
        {
          "locked_core": {},
          "core": {
            "total_volume": total_volume,
            "available_volume": available_volume,
            "frozen_volume": frozen_volume,
            "today_buy_volume": today_buy_volume,
            "avg_price": avg_price,
            "last_price": last_price,
            "market_value": position_payload["market_value"],
          },
          "swing": {},
        },
      )
      if (
        runtime.context.mode == StrategyRunMode.PAPER
        and runtime.broker
        and hasattr(runtime.broker, "positions")
      ):
        if total_volume <= 0:
          runtime.broker.positions.pop(code, None)
        else:
          runtime.broker.positions[code] = Position(
            instrument_code=code,
            long_volume=total_volume,
            available_volume=available_volume,
            frozen_volume=frozen_volume,
            today_buy_volume=today_buy_volume,
            long_avg_price=avg_price,
            market_value=position_payload["market_value"],
            pnl=0.0,
            last_price=last_price,
          )

  def _seed_backtest_broker_positions(self, runtime) -> None:
    """Backward-compatible wrapper for tests and older callers."""
    self._seed_simulated_broker_positions(runtime)

  async def stop(self, run_id: str, *, force: bool = False) -> bool:
    """
    停止策略运行并清理资源

    Args:
        run_id: 运行实例ID

    Returns:
        bool: 是否停止成功

    Note:
        - 负责资源清理（Broker、DataAdapter、Task）
        - 收集最终指标
        - 指标持久化由调用方负责
    """
    if run_id not in self.runs:
      return False

    runtime = self.runs[run_id]

    if runtime.status in [ExecutionStatus.STOPPED, ExecutionStatus.STOPPING]:
      return True
    active_exit_plans = runtime.exit_plan_book.active_plans()
    if active_exit_plans and not force:
      self._runtime_log(
        runtime,
        "WARNING",
        "仍有自动退出计划保护未退出仓位，运行保持监控；请先进入 DRAINING",
      )
      return False

    try:
      runtime.status = ExecutionStatus.STOPPING

      # 停止策略
      if runtime.strategy:
        # 移除日志广播 Handler
        if runtime.log_manager:
          runtime.log_manager.detach_handler(
            run_id=runtime.run_id,
            logger=runtime.strategy.logger,
          )
        await runtime.strategy.stop()

      # 停止策略状态同步
      if runtime.state_manager:
        await runtime.state_manager.stop_state_sync(runtime.strategy)

      # 断开 Broker 和释放 DataAdapter 引用
      if runtime.broker:
        await runtime.broker.disconnect()
      if runtime.data_adapter:
        # 释放适配器引用而不是直接断开
        adapter_manager.release_adapter_for_mode(runtime.context.mode.value.lower())

      # 取消任务
      if runtime.task and not runtime.task.done():
        runtime.task.cancel()
        try:
          await asyncio.wait_for(runtime.task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
          self.logger.warning(f"策略任务 {run_id} 停止超时,强制跳过")

      # 更新指标
      if runtime.metrics and runtime.broker:
        runtime.metrics.end_time = time_utils.now()
        # 从 broker 获取性能指标
        if hasattr(runtime.broker, "get_performance_metrics"):
          perf_metrics = runtime.broker.get_performance_metrics()
          if inspect.isawaitable(perf_metrics):
            perf_metrics = await perf_metrics
          if not isinstance(perf_metrics, dict):
            perf_metrics = {}
          runtime.metrics.max_drawdown = perf_metrics.get("max_drawdown", 0.0)
          runtime.metrics.win_rate = perf_metrics.get("win_rate", 0.0)
          runtime.metrics.sharpe_ratio = perf_metrics.get("sharpe_ratio", 0.0)
          runtime.metrics.total_pnl = (
            perf_metrics.get("final_equity", runtime.metrics.initial_capital)
            - runtime.metrics.initial_capital
          )
          runtime.metrics.current_capital = perf_metrics.get(
            "final_equity", runtime.metrics.initial_capital
          )
          runtime.metrics.trades_executed = perf_metrics.get("total_trades", 0)

      # 停止状态管理器
      if runtime.performance_recorder:
        await runtime.performance_recorder.flush()
      if runtime.state_manager:
        await runtime.state_manager.stop()

      runtime.status = ExecutionStatus.STOPPED

      self.logger.info(f"策略运行停止成功: {run_id}")
      return True

    except Exception as e:
      runtime.status = ExecutionStatus.ERROR
      runtime.error_message = str(e)
      self.logger.error(f"停止策略运行失败: {run_id}, 错误: {e}")
      return False

  async def pause(self, run_id: str) -> bool:
    """
    暂停策略运行

    Args:
        run_id: 运行实例ID

    Returns:
        bool: 是否暂停成功
    """
    if run_id not in self.runs:
      return False

    runtime = self.runs[run_id]

    if runtime.status != ExecutionStatus.RUNNING:
      return False
    if runtime.exit_plan_book.active_plans():
      self._runtime_log(
        runtime,
        "WARNING",
        "仍有自动退出计划保护未退出仓位，不能暂停行情监控",
      )
      return False

    runtime.status = ExecutionStatus.PAUSED
    self.logger.info(f"策略运行已暂停: {run_id}")
    return True

  async def resume(self, run_id: str) -> bool:
    """
    恢复策略运行

    Args:
        run_id: 运行实例ID

    Returns:
        bool: 是否恢复成功
    """
    if run_id not in self.runs:
      return False

    runtime = self.runs[run_id]

    if runtime.status != ExecutionStatus.PAUSED:
      return False

    runtime.status = ExecutionStatus.RUNNING
    self.logger.info(f"策略运行已恢复: {run_id}")
    return True

  async def delete(self, run_id: str) -> bool:
    """删除策略运行"""
    if run_id not in self.runs:
      return False

    # 先停止实例
    if not await self.stop(run_id):
      return False

    # 从内存中删除
    del self.runs[run_id]

    self.logger.info(f"策略运行已删除: {run_id}")
    return True

  def get(self, run_id: str) -> Optional[StrategyRuntime]:
    """获取策略运行"""
    return self.runs.get(run_id)

  def get_all(self) -> List[StrategyRuntime]:
    """获取所有策略运行"""
    return list(self.runs.values())

  async def reconcile_instruments(
    self,
    run_id: str,
    instruments: List[str],
    *,
    instrument_metadata: Optional[Dict[str, Dict[str, Any]]] = None,
  ) -> Dict[str, List[str]]:
    """串行调整运行中策略的标的池和实时行情订阅。"""

    runtime = self.runs.get(run_id)
    if runtime is None:
      raise ValueError(f"策略运行不存在: {run_id}")
    if runtime.context.mode == StrategyRunMode.BACKTEST:
      raise ValueError("回测运行不支持动态修改标的池")

    normalized = []
    for raw in instruments or []:
      code = str(raw or "").strip().upper()
      if code and code not in normalized:
        normalized.append(code)

    if runtime.status in {ExecutionStatus.RUNNING, ExecutionStatus.PAUSED}:
      future = asyncio.get_running_loop().create_future()
      await runtime.event_queue.put(
        (
          "universe",
          {
            "instruments": normalized,
            "instrument_metadata": dict(instrument_metadata or {}),
            "future": future,
          },
        )
      )
      return await future

    current = list(runtime.context.instruments or [])
    runtime.context.instruments = normalized
    return {
      "added": [code for code in normalized if code not in current],
      "removed": [code for code in current if code not in normalized],
      "instruments": normalized,
    }

  def get_running(self) -> List[StrategyRuntime]:
    """获取运行中的策略运行"""
    return [
      runtime
      for runtime in self.runs.values()
      if runtime.status == ExecutionStatus.RUNNING
    ]

  async def stop_all_runs(self, timeout: float = 10.0) -> None:
    """停止所有策略运行

    Args:
        timeout: 总超时时间(秒),默认10秒
    """
    tasks = []
    for run_id in list(self.runs.keys()):
      tasks.append(self.stop(run_id, force=True))

    if tasks:
      try:
        await asyncio.wait_for(
          asyncio.gather(*tasks, return_exceptions=True), timeout=timeout
        )
      except asyncio.TimeoutError:
        self.logger.warning(f"停止所有策略超时({timeout}秒),部分策略可能未完全停止")

  async def shutdown(self) -> None:
    """关闭执行器"""
    self._shutdown_event.set()
    await self.stop_all_runs()
    self.thread_pool.shutdown(wait=False)
    self.logger.info("策略执行器已关闭")

  async def _setup_broker_and_data(self, runtime: StrategyRuntime) -> None:
    """设置 Broker 和 DataAdapter"""
    mode = runtime.context.mode

    # 创建 Broker
    if mode == StrategyRunMode.BACKTEST:
      runtime.broker = BacktestBroker(
        account_id=runtime.run_id,
        initial_capital=runtime.context.initial_capital,
        commission_rate=float(
          runtime.context.parameters.get("commission_rate", 0.0003) or 0.0
        ),
        min_commission=float(
          runtime.context.parameters.get("minimum_commission", 5.0) or 0.0
        ),
        stamp_tax_rate=float(
          runtime.context.parameters.get("stamp_tax_rate", 0.0005) or 0.0
        ),
        transfer_fee_rate=float(
          runtime.context.parameters.get("transfer_fee_rate", 0.00001) or 0.0
        ),
        slippage_rate=float(
          runtime.context.parameters.get("slippage_rate", 0.0001) or 0.0
        ),
        participation_cap_pct=float(
          runtime.context.parameters.get("participation_cap_pct", 0.05) or 0.05
        ),
        book_depth_participation_pct=float(
          runtime.context.parameters.get(
            "book_depth_participation_pct",
            0.25,
          )
          or 0.25
        ),
      )
    elif mode == StrategyRunMode.PAPER:
      runtime.broker = SimulatorBroker(
        account_id=runtime.run_id,
        initial_capital=runtime.context.initial_capital,
      )
    else:  # LIVE
      runtime.broker = LiveBroker(
        account_id=str(runtime.context.parameters.get("account_id") or runtime.run_id),
        initial_capital=runtime.context.initial_capital,
      )

    # 使用 AdapterManager 获取数据适配器
    runtime.data_adapter = adapter_manager.get_adapter_for_mode(mode)

    # 连接 Broker 和 DataAdapter（适配器可能已连接，会自动处理）
    await runtime.broker.connect()
    await runtime.data_adapter.connect()

    # 订阅订单和成交回调
    order_subscription = runtime.broker.subscribe_order_updates(
      lambda order: runtime.event_queue.put_nowait(("order", order))
    )
    if inspect.isawaitable(order_subscription):
      await order_subscription
    trade_subscription = runtime.broker.subscribe_trade_updates(
      lambda trade: runtime.event_queue.put_nowait(("trade", trade))
    )
    if inspect.isawaitable(trade_subscription):
      await trade_subscription

    self.logger.info(f"Broker 和 DataAdapter 已设置: {mode.value}")

  async def _initialize_backtest_dynamic_universe(
    self, runtime: StrategyRuntime
  ) -> None:
    """Apply the account-holdings universe snapshot before historical replay."""

    if runtime.context.mode != StrategyRunMode.BACKTEST or not runtime.strategy:
      return
    metadata = dict(runtime.context.parameters.get("initial_instrument_metadata") or {})
    if not metadata:
      return
    desired = list(runtime.context.instruments or [])
    state = runtime.strategy.state.to_dict()
    account = runtime.state_manager.get_account_quota() if runtime.state_manager else {}
    positions = (
      runtime.state_manager.get_all_positions() if runtime.state_manager else {}
    )
    reconcile_input = StrategyInput(
      run_id=runtime.run_id,
      strategy_id=str(runtime.strategy_id),
      timestamp=runtime.context.backtest_start_time or time_utils.now(),
      cadence=StrategyCadence.RECONCILE,
      instrument_code="",
      event={
        "added": desired,
        "removed": [],
        "instruments": desired,
        "instrument_metadata": metadata,
      },
      portfolio_state={"account": account, "positions": positions},
      strategy_state=state,
      parameters=dict(runtime.context.parameters or {}),
    )
    output = await runtime.strategy.step(reconcile_input)
    await self._process_strategy_output(runtime, output, reconcile_input)

  async def _run_strategy_loop(self, runtime: StrategyRuntime) -> None:
    """策略运行循环"""
    strategy = runtime.strategy

    try:
      # 初始化策略
      await strategy.initialize()
      await strategy.start()
      await self._initialize_backtest_dynamic_universe(runtime)
      self._runtime_log(runtime, "INFO", "策略初始化完成，进入执行循环")

      # 根据模式运行不同的逻辑（回测模式需要回放数据）
      if runtime.context.mode == StrategyRunMode.BACKTEST:
        self._runtime_log(runtime, "INFO", "回测执行开始")
        await self._run_backtest_loop(runtime)
        await self._finalize_t_trade_replay(runtime)
      else:
        # 实时模式下，_run_realtime_loop 主要负责状态更新和心跳
        # 事件处理在 _process_event_queue 中进行
        await self._run_realtime_loop(runtime)

      # 如果正常结束且未被停止，标记为完成
      if runtime.status == ExecutionStatus.RUNNING:
        runtime.status = ExecutionStatus.COMPLETED
        self._runtime_log(runtime, "SUCCESS", f"策略运行完成: {runtime.run_id}")

        # 回测模式：写入结果文件并更新数据库记录
        if runtime.context.mode == StrategyRunMode.BACKTEST and runtime.state_manager:
          self._runtime_log(runtime, "INFO", "回测结果文件写入开始")
          if runtime.log_manager:
            await runtime.log_manager.flush(runtime.run_id)
          final_grid_book_snapshot = (
            runtime.state_manager.get_latest_backtest_grid_book_snapshot()
          )
          grid_book_snapshot_count = (
            runtime.state_manager.get_backtest_grid_book_snapshot_count()
          )
          grid_book_observed_count = (
            runtime.state_manager.get_backtest_grid_book_observed_count()
          )
          result_path = await runtime.state_manager.finalize_backtest()
          self._runtime_log(runtime, "SUCCESS", f"回测结果文件写入完成: {result_path}")

          # 更新 StrategyBacktest 记录
          if runtime.context.backtest_id:
            from quantx_infrastructure.database.connection import get_async_db
            from quantx_infrastructure.models.enums import StrategyRunStatus
            from quantx_infrastructure.repositories.backtest_repository import (
              BacktestRepository,
            )
            from quantx_infrastructure.repositories.strategy_grid_book_snapshot_repository import (
              StrategyGridBookSnapshotRepository,
            )
            from quantx_infrastructure.repositories.strategy_run_repository import (
              StrategyRunRepository,
            )

            metrics = runtime.get_metrics()
            if runtime.context.parameters.get("t_trade_replay"):
              from quantx_infrastructure.core.t_trade_replay_metrics import (
                build_t_trade_replay_metrics,
              )
              from quantx_infrastructure.core.t_trade_replay_report import (
                write_t_trade_replay_report,
              )

              replay_metrics = build_t_trade_replay_metrics(runtime)
              try:
                replay_metrics["report"] = write_t_trade_replay_report(
                  result_path,
                  replay_metrics,
                  run_id=runtime.run_id,
                  backtest_id=runtime.context.backtest_id,
                  start_time=runtime.context.backtest_start_time,
                  end_time=runtime.context.backtest_end_time,
                )
              except Exception:
                self.logger.exception("做 T 回放报告生成失败")
                replay_metrics["report"] = {
                  "status": "FAILED",
                  "schema_version": 1,
                  "generated_at": None,
                  "conclusion_code": "REPORT_GENERATION_FAILED",
                  "conclusion": "回放已完成，但报告文件生成失败，请检查 Engine 日志。",
                  "html_artifact": "",
                  "json_artifact": "",
                }
              metrics["t_trade_replay"] = replay_metrics
            if runtime.performance_recorder:
              try:
                await runtime.performance_recorder.flush()
                (
                  performance_path,
                  performance_view,
                ) = await StrategyPerformanceService.finalize_backtest_snapshot(
                  run_id=runtime.run_id,
                  backtest_id=runtime.context.backtest_id,
                  mode=runtime.context.mode,
                  metrics=metrics,
                )
                metrics["performance_snapshot_path"] = performance_path
                metrics["performance_summary"] = performance_view.get("summary")
              except Exception as exc:
                self.logger.error(f"回测绩效快照生成失败: {exc}")

            async for db in get_async_db():
              backtest_repo = BacktestRepository(db)
              run_repo = StrategyRunRepository(db)
              backtest = await backtest_repo.update_backtest_status(
                backtest_id=runtime.context.backtest_id,
                status="COMPLETED",
                metrics=metrics,
                end_time=time_utils.now(),
              )
              await run_repo.update_run(
                runtime.run_id,
                {
                  "status": StrategyRunStatus.COMPLETED,
                  "metrics": metrics,
                  "error_message": None,
                  "stop_time": time_utils.now(),
                },
              )
              if backtest and final_grid_book_snapshot:
                snapshot_repo = StrategyGridBookSnapshotRepository(db)
                await snapshot_repo.upsert_backtest_final(
                  strategy_run_id=runtime.run_id,
                  backtest_id=runtime.context.backtest_id,
                  backtest_version=int(getattr(backtest, "version", 0) or 0),
                  snapshot=final_grid_book_snapshot,
                  source_path=result_path,
                  snapshot_count=grid_book_snapshot_count,
                  observed_count=grid_book_observed_count,
                )
              break
            self._runtime_log(
              runtime,
              "SUCCESS",
              f"回测记录已更新: {runtime.context.backtest_id}",
            )

    except Exception as e:
      runtime.status = ExecutionStatus.ERROR
      runtime.error_message = str(e)
      self._runtime_log(
        runtime,
        "ERROR",
        f"策略运行循环异常: {runtime.run_id}, 错误: {e}",
      )
    finally:
      if runtime.performance_recorder:
        try:
          await runtime.performance_recorder.flush()
        except Exception as e:
          self.logger.error(f"绩效采样刷新失败: {e}")
      if strategy:
        try:
          await strategy.stop()
        except Exception as e:
          self._runtime_log(runtime, "ERROR", f"策略停止异常: {e}")
      if runtime.log_manager:
        await runtime.log_manager.flush(runtime.run_id)

  async def _run_backtest_loop(self, runtime: StrategyRuntime) -> None:
    """运行回测循环 - 支持tick和K线双数据流"""
    data_adapter = runtime.data_adapter

    # 使用运行时上下文的回测时间范围（来自 StrategyManager.run_strategy）
    end_time = runtime.context.backtest_end_time or time_utils.now()
    start_time = runtime.context.backtest_start_time or (end_time - timedelta(days=30))

    # 读取策略声明的数据需求
    requirements = runtime.strategy_class.get_data_requirements()
    use_tick_data = bool(requirements.get("use_tick_data", False))
    periods = [
      p.lower()
      for p in (requirements.get("periods") or [])
      if p and p.lower() != "tick"
    ]
    self._runtime_log(
      runtime,
      "INFO",
      (
        f"回测数据回放准备: {start_time} -> {end_time}, "
        f"use_tick_data={use_tick_data}, periods={periods or ['tick']}, "
        f"instruments={runtime.context.instruments}"
      ),
    )

    if (
      isinstance(data_adapter, HistoricalDataAdapter)
      and len(runtime.context.instruments) > 1
    ):
      await self._run_backtest_multi_instrument_timeline(
        runtime,
        list(runtime.context.instruments),
        periods,
        start_time,
        end_time,
        use_tick_data=use_tick_data,
      )
      return

    for instrument_code in runtime.context.instruments:
      self._runtime_log(runtime, "INFO", f"回测标的开始回放: {instrument_code}")
      if isinstance(data_adapter, HistoricalDataAdapter):
        if use_tick_data:
          await self._run_backtest_timeline_with_ticks(
            runtime, instrument_code, periods, start_time, end_time
          )
        else:
          await self._run_backtest_timeline_with_klines(
            runtime, instrument_code, periods, start_time, end_time
          )
        self._runtime_log(runtime, "SUCCESS", f"回测标的回放完成: {instrument_code}")
        continue

      if use_tick_data:
        # 双数据流模式：订阅tick和K线
        await data_adapter.subscribe_tick(
          instrument_code,
          lambda tick: runtime.event_queue.put_nowait(("tick", tick)),
        )

        for period in periods:
          await data_adapter.subscribe_kline(
            instrument_code,
            period,
            lambda kline: runtime.event_queue.put_nowait(("kline", kline)),
          )
      else:
        # 仅K线模式 - 支持多周期
        for period in periods:
          await data_adapter.subscribe_kline(
            instrument_code,
            period,
            lambda kline: runtime.event_queue.put_nowait(("kline", kline)),
          )

  async def _wait_for_backtest_reports(
    self,
    runtime: StrategyRuntime,
    *,
    timeout_seconds: float = 30.0,
  ) -> None:
    """Wait until simulated broker reports have reached runtime state."""

    try:
      await asyncio.wait_for(
        runtime.event_queue.join(),
        timeout=max(1.0, float(timeout_seconds)),
      )
    except asyncio.TimeoutError as exc:
      raise RuntimeError("回测 Broker 回报未在结束前完成收敛") from exc

  async def _cancel_backtest_pending_orders(self, runtime: StrategyRuntime) -> None:
    broker = runtime.broker
    if not isinstance(broker, BacktestBroker):
      return
    active_statuses = {
      OrderStatus.PENDING,
      OrderStatus.SUBMITTED,
      OrderStatus.PARTIAL_FILLED,
    }
    for order in list((broker.orders or {}).values()):
      if getattr(order, "status", None) not in active_statuses:
        continue
      await broker.cancel_order(str(order.order_id))

  def _build_backtest_end_exit_intent(
    self,
    runtime: StrategyRuntime,
    plan: Any,
    market_data: MarketDataSnapshot,
  ) -> tuple[ExitDecision, TradeIntent]:
    bids = list(getattr(market_data, "bid_price", []) or [])
    current_price = float(
      getattr(market_data, "price", 0.0) or getattr(market_data, "close", 0.0) or 0.0
    )
    price_hint = float(bids[0] if bids and bids[0] else current_price)
    batch_id = str(plan.template.metadata.get("t_batch_id", "") or "")
    rule_id = f"backtest-end-force-close:{plan.plan_id}"
    decision = ExitDecision(
      plan_id=plan.plan_id,
      rule_id=rule_id,
      rule_type="BACKTEST_END_FORCE_CLOSE",
      reason="BACKTEST_END_FORCE_CLOSE",
      volume=int(plan.remaining_volume),
      priority=10_000,
      metrics={
        "backtest_end": True,
        "current_price": current_price,
        "remaining_volume": int(plan.remaining_volume),
      },
    )
    intent = TradeIntent(
      strategy_id=plan.template.strategy_id or str(runtime.strategy_id),
      run_id=plan.template.run_id or runtime.run_id,
      instrument_code=plan.template.instrument_code,
      direction=TradeIntentDirection.SELL,
      bucket=plan.template.bucket,
      reason="BACKTEST_END_FORCE_CLOSE",
      priority=TradeIntentPriority.URGENT,
      target_volume=int(plan.remaining_volume),
      limit_price_hint=price_hint,
      execution_mode=TradeIntentExecutionMode.AUTO,
      max_price_deviation_bps=plan.template.execution.max_slippage_bps,
      metadata={
        **dict(plan.template.metadata or {}),
        "t_batch_id": batch_id,
        "exit_plan_id": plan.plan_id,
        "exit_rule_id": rule_id,
        "exit_rule_type": "BACKTEST_END_FORCE_CLOSE",
        "exit_reason": "BACKTEST_END_FORCE_CLOSE",
        "exit_plan_source_type": plan.template.source_type,
        "exit_plan_source_id": plan.template.source_id,
        "exit_plan_config_version": plan.template.config_version,
        "price_type": "MARKET",
        "price_reference": ExitPriceReference.BID.value,
        "protected_limit": False,
        "max_exit_slippage_bps": plan.template.execution.max_slippage_bps,
        "execution_urgency": "URGENT",
        "t1_policy": plan.template.t1_policy.value,
        "allow_t1_substitution": True,
        "t1_insufficient_action": "REJECT",
        "backtest_forced_close": True,
        "exit_metrics": dict(decision.metrics),
      },
    )
    return decision, intent

  async def _finalize_t_trade_replay(self, runtime: StrategyRuntime) -> None:
    """Close replay-created T batches on the final tradable quote."""

    if (
      runtime.context.mode != StrategyRunMode.BACKTEST
      or not runtime.context.parameters.get("t_trade_replay")
      or not isinstance(runtime.broker, BacktestBroker)
    ):
      return

    await self._wait_for_backtest_reports(runtime)
    await self._cancel_backtest_pending_orders(runtime)
    await self._wait_for_backtest_reports(runtime)

    attempts: List[Dict[str, Any]] = []
    plans = sorted(
      list(runtime.exit_plan_book.plans.values()),
      key=lambda item: (item.template.instrument_code, item.plan_id),
    )
    for plan in plans:
      if plan.remaining_volume <= 0:
        continue
      attempt = {
        "plan_id": plan.plan_id,
        "batch_id": str(plan.template.metadata.get("t_batch_id", "") or ""),
        "stock_code": plan.template.instrument_code,
        "requested_volume": int(plan.remaining_volume),
        "status": "PENDING",
        "remaining_volume": int(plan.remaining_volume),
      }
      attempts.append(attempt)
      market_data = runtime.latest_market_data.get(plan.template.instrument_code)
      if market_data is None:
        attempt.update(status="FAILED_NO_FINAL_QUOTE")
        continue
      if plan.pending_intent_id:
        attempt.update(status="FAILED_PENDING_EXIT_NOT_RELEASED")
        continue

      decision, intent = self._build_backtest_end_exit_intent(
        runtime,
        plan,
        market_data,
      )
      runtime.exit_plan_book.mark_intent(decision, intent.intent_id)
      self._persist_exit_plan_book(runtime)
      await self._process_strategy_output(
        runtime,
        StrategyOutput(
          trade_intents=[intent],
          decision_tags=["backtest_end_force_close"],
          trace_payload={
            "exit_plan_id": plan.plan_id,
            "t_batch_id": attempt["batch_id"],
            "requested_volume": attempt["requested_volume"],
          },
        ),
      )
      await self._wait_for_backtest_reports(runtime)
      attempt["remaining_volume"] = int(plan.remaining_volume)
      attempt["status"] = (
        "FILLED" if plan.remaining_volume <= 0 else "FAILED_NOT_FULLY_LIQUIDATED"
      )

    await runtime.broker.refresh_performance_snapshot()
    liquidation = {
      "attempted_cycles": len(attempts),
      "closed_cycles": sum(item["status"] == "FILLED" for item in attempts),
      "failed_cycles": sum(item["status"] != "FILLED" for item in attempts),
      "attempts": attempts,
    }
    runtime.context.parameters["replay_forced_liquidation"] = liquidation
    level = "SUCCESS" if liquidation["failed_cycles"] == 0 else "WARNING"
    self._runtime_log(
      runtime,
      level,
      "做 T 回放期末清算完成: "
      f"attempted={liquidation['attempted_cycles']}, "
      f"closed={liquidation['closed_cycles']}, "
      f"failed={liquidation['failed_cycles']}",
    )

  async def _run_backtest_multi_instrument_timeline(
    self,
    runtime: StrategyRuntime,
    instrument_codes: List[str],
    periods: List[str],
    start_time: datetime,
    end_time: datetime,
    *,
    use_tick_data: bool,
  ) -> None:
    """Replay all instruments on one chronological event timeline."""
    data_adapter = runtime.data_adapter
    if not isinstance(data_adapter, HistoricalDataAdapter):
      return
    for code in instrument_codes:
      await self._run_backtest_warmup_klines(runtime, code, periods, start_time)

    trading_dates = await TradingDateHelper().get_trading_calendar(
      market="SH",
      start_date=start_time.date(),
      end_date=end_time.date(),
    )
    if not trading_dates:
      self._runtime_log(runtime, "WARNING", "多标的回测区间无交易日")
      return

    window_hours = self._get_backtest_window_hours()
    last_tick_time: Dict[str, Optional[datetime]] = {
      code: None for code in instrument_codes
    }
    last_kline_time: Dict[tuple[str, str], Optional[datetime]] = {
      (code, period): None for code in instrument_codes for period in periods
    }
    totals = {"tick": 0, "kline": 0}
    alignment = str(
      runtime.context.parameters.get("kline_time_alignment", "end") or "end"
    ).lower()
    for trading_date in trading_dates:
      if runtime.status != ExecutionStatus.RUNNING:
        break
      day_start = max(start_time, datetime.combine(trading_date, time(9, 30)))
      day_end = min(end_time, datetime.combine(trading_date, time(15, 30)))
      if day_end < day_start:
        continue
      for window_start, window_end in self._iter_backtest_windows(
        day_start, day_end, window_hours
      ):
        events: List[tuple[datetime, int, str, str, Any]] = []
        for code in instrument_codes:
          if use_tick_data:
            ticks = await data_adapter.get_ticks(
              instrument_code=code,
              start_time=window_start,
              end_time=window_end,
              dividend_type="front",
              limit=6000,
            )
            previous_tick = last_tick_time.get(code)
            filtered_ticks = [
              tick
              for tick in (ticks or [])
              if tick is not None
              and tick.time is not None
              and (previous_tick is None or tick.time > previous_tick)
            ]
            for tick in self._filter_backtest_continuous_session_events(filtered_ticks):
              events.append((tick.time, 0, code, "tick", tick))
          for period in periods:
            klines = await data_adapter.get_klines(
              instrument_code=code,
              period=period,
              start_time=window_start,
              end_time=window_end,
            )
            previous_kline = last_kline_time.get((code, period))
            filtered_klines = [
              kline
              for kline in (klines or [])
              if kline is not None
              and kline.time is not None
              and (previous_kline is None or kline.time > previous_kline)
            ]
            if self._is_backtest_intraday_period(period):
              filtered_klines = self._filter_backtest_continuous_session_events(
                filtered_klines
              )
            for kline in filtered_klines:
              event_time = self._get_kline_end_time(kline, period, alignment=alignment)
              events.append((event_time, 1, code, period, kline))

        events.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
        for _, event_type, code, period, event in events:
          if runtime.status != ExecutionStatus.RUNNING:
            break
          if event_type == 0:
            await self._process_tick(runtime, event)
            last_tick_time[code] = event.time
            totals["tick"] += 1
          else:
            await self._process_kline(runtime, event)
            last_kline_time[(code, period)] = event.time
            totals["kline"] += 1
        self._runtime_log(
          runtime,
          "INFO",
          f"多标的回测窗口完成: {window_start} -> {window_end}, events={len(events)}",
        )
    self._runtime_log(
      runtime,
      "SUCCESS",
      f"多标的全局时间线回测完成: instruments={len(instrument_codes)}, "
      f"tick={totals['tick']}, kline={totals['kline']}",
    )

  def _get_backtest_window_hours(self) -> int:
    """获取回测回放窗口大小（小时）"""
    try:
      window_hours = int(getattr(settings, "backtest_replay_window_hours", 12))
    except Exception:
      window_hours = 12
    return max(1, window_hours)

  def _iter_backtest_windows(
    self, start_time: datetime, end_time: datetime, window_hours: int
  ):
    """生成回测时间窗口"""
    if end_time < start_time:
      return
    window_hours = max(1, int(window_hours))
    window_delta = timedelta(hours=window_hours)
    current = start_time
    while current <= end_time:
      window_end = min(end_time, current + window_delta)
      yield current, window_end
      current = window_end + timedelta(microseconds=1)

  def _get_backtest_warmup_bars(self, runtime: StrategyRuntime, period: str) -> int:
    """Return how many bars to preload before the formal backtest window."""
    params = dict(runtime.context.parameters or {})
    period_key = (period or "").lower().replace(" ", "")
    specific_keys = [
      f"backtest_warmup_bars_{period_key}",
      f"warmup_bars_{period_key}",
    ]
    for key in specific_keys + ["backtest_warmup_bars", "warmup_bars"]:
      if params.get(key) is not None:
        try:
          return max(0, min(2000, int(params.get(key) or 0)))
        except (TypeError, ValueError):
          continue

    candidates = [20]
    if period_key == "1d":
      for key in ("box_window_daily", "box_window", "atr_period"):
        if params.get(key) is not None:
          try:
            candidates.append(int(params.get(key) or 0))
          except (TypeError, ValueError):
            pass
    elif "60" in period_key or "1h" in period_key:
      for key in ("box_window_60m", "box_window"):
        if params.get(key) is not None:
          try:
            candidates.append(int(params.get(key) or 0))
          except (TypeError, ValueError):
            pass

    return max(0, min(2000, max(candidates)))

  def _get_backtest_warmup_start_time(
    self, start_time: datetime, period: str, warmup_bars: int
  ) -> datetime:
    period_key = (period or "").lower().replace(" ", "")
    if period_key == "1d":
      return start_time - timedelta(days=max(30, warmup_bars * 3))
    if period_key in {"1w", "week", "1week"}:
      return start_time - timedelta(days=max(70, warmup_bars * 10))
    if "60" in period_key or "1h" in period_key:
      return start_time - timedelta(days=max(10, warmup_bars // 4 + 5))
    if period_key.endswith("m"):
      try:
        minutes = max(1, int(period_key[:-1]))
      except (TypeError, ValueError):
        minutes = 1
      return start_time - timedelta(minutes=warmup_bars * minutes * 3)
    return start_time - timedelta(days=max(30, warmup_bars))

  def _is_backtest_intraday_period(self, period: str) -> bool:
    period_key = (period or "").lower().replace(" ", "")
    return period_key.endswith("m") or period_key.endswith("h")

  def _is_ashare_continuous_trading_time(self, timestamp: datetime) -> bool:
    """Return True only for A-share continuous auction sessions."""
    if not timestamp:
      return False
    local_time = (
      time_utils.to_shanghai(timestamp).time() if timestamp.tzinfo else timestamp.time()
    )
    return time(9, 30) <= local_time <= time(11, 30) or time(
      13, 0
    ) <= local_time < time(14, 57)

  def _filter_backtest_continuous_session_events(self, events: List[Any]) -> List[Any]:
    """Drop call-auction events from backtest replay."""
    return [
      event
      for event in (events or [])
      if getattr(event, "time", None)
      and self._is_ashare_continuous_trading_time(event.time)
    ]

  async def _run_backtest_warmup_klines(
    self,
    runtime: StrategyRuntime,
    instrument_code: str,
    periods: List[str],
    start_time: datetime,
  ) -> None:
    data_adapter = runtime.data_adapter
    strategy = runtime.strategy
    if not isinstance(data_adapter, HistoricalDataAdapter) or not strategy:
      return

    warmup_events: List[KLine] = []
    warmup_end = start_time - timedelta(microseconds=1)
    dividend_type = str(
      (runtime.context.parameters or {}).get("dividend_type", "none") or "none"
    )
    for period in periods:
      warmup_bars = self._get_backtest_warmup_bars(runtime, period)
      if warmup_bars <= 0:
        continue
      warmup_start = self._get_backtest_warmup_start_time(
        start_time, period, warmup_bars
      )
      klines = await data_adapter.get_klines(
        instrument_code=instrument_code,
        period=period,
        start_time=warmup_start,
        end_time=warmup_end,
        limit=warmup_bars,
        order="desc",
        dividend_type=dividend_type,
      )
      if self._is_backtest_intraday_period(period):
        klines = self._filter_backtest_continuous_session_events(klines)
      warmup_events.extend(
        k for k in (klines or []) if k is not None and k.time is not None
      )

    if not warmup_events:
      return

    warmup_events.sort(key=lambda kline: kline.time)
    for kline in warmup_events:
      if runtime.status != ExecutionStatus.RUNNING:
        break
      await self._process_warmup_kline(runtime, kline)

    self._runtime_log(
      runtime,
      "INFO",
      f"回测预热完成: {instrument_code}, start={start_time}, bars={len(warmup_events)}",
    )

  async def _process_warmup_kline(self, runtime: StrategyRuntime, kline: KLine) -> None:
    strategy = runtime.strategy
    if not strategy:
      return

    runtime.context.current_time = kline.time
    if isinstance(runtime.data_adapter, HistoricalDataAdapter):
      runtime.data_adapter.current_time = kline.time
    market_snapshot = MarketDataSnapshot.from_kline(
      kline,
      limit_rate=self._backtest_limit_rate(runtime),
    )
    runtime.latest_market_data[kline.stock_code] = market_snapshot
    strategy_input = self._build_strategy_input(
      runtime,
      cadence=StrategyCadence.BAR,
      instrument_code=kline.stock_code,
      timestamp=kline.time,
      market_data=market_snapshot,
      event=kline,
    )
    output = await strategy.warmup(strategy_input)
    if output and getattr(output, "runtime_state_patch", None):
      self._apply_runtime_state_patch(runtime, output.runtime_state_patch)

  def _persist_exit_plan_book(self, runtime: StrategyRuntime) -> None:
    if runtime.state_manager:
      runtime.state_manager.set_custom(
        EXIT_PLAN_BOOK_STATE_KEY,
        runtime.exit_plan_book.to_dict(),
      )

  def register_external_exit_plan(
    self,
    run_id: str,
    template: ExitPlanTemplate | Dict[str, Any],
    *,
    volume: int,
    price: float,
    trade_time: Optional[datetime] = None,
  ) -> Dict[str, Any]:
    """Register an audited fill that did not originate from this executor."""

    runtime = self.runs.get(run_id)
    if runtime is None:
      raise ValueError("策略运行不存在或尚未启动")
    plan = runtime.exit_plan_book.register_entry_fill(
      template,
      volume=volume,
      price=price,
      trade_time=trade_time,
    )
    runtime.exit_plan_book.prune_terminal(
      int(runtime.context.parameters.get("exit_plan_history_limit", 200) or 200)
    )
    self._persist_exit_plan_book(runtime)
    return plan.projection()

  async def _process_auto_exit_plans(
    self,
    runtime: StrategyRuntime,
    *,
    instrument_code: str,
    timestamp: datetime,
    market_data: MarketDataSnapshot,
  ) -> None:
    """Evaluate Engine-owned exit plans and route resulting SELL intents."""

    if not any(
      plan.template.instrument_code == instrument_code and plan.remaining_volume > 0
      for plan in runtime.exit_plan_book.plans.values()
    ):
      return
    from quantx_engine.exit_plan_monitor import exit_plan_monitor

    if (
      runtime.context.mode != StrategyRunMode.BACKTEST and exit_plan_monitor.is_running
    ):
      await AutoExitPlanService().sync_strategy_plan_book(
        strategy_run_id=runtime.run_id,
        book_state=runtime.exit_plan_book.to_dict(),
        execution_mode=runtime.context.mode.value,
      )
      return
    bids = list(getattr(market_data, "bid_price", []) or [])
    asks = list(getattr(market_data, "ask_price", []) or [])
    current_price = float(
      getattr(market_data, "price", 0.0) or getattr(market_data, "close", 0.0) or 0.0
    )
    configured_instrument = (
      runtime.context.parameters.get("instrument")
      or runtime.context.parameters.get("instrument_master")
      or {}
    )
    if isinstance(configured_instrument, dict):
      configured_limit_up = configured_instrument.get(
        "limit_up"
      ) or configured_instrument.get("up_stop_price")
      configured_limit_down = configured_instrument.get(
        "limit_down"
      ) or configured_instrument.get("down_stop_price")
      configured_price_tick = configured_instrument.get("price_tick")
    else:
      configured_limit_up = getattr(configured_instrument, "limit_up", None) or getattr(
        configured_instrument, "up_stop_price", None
      )
      configured_limit_down = getattr(
        configured_instrument, "limit_down", None
      ) or getattr(configured_instrument, "down_stop_price", None)
      configured_price_tick = getattr(configured_instrument, "price_tick", None)
    bid_depth = sum(list(getattr(market_data, "bid_vol", []) or [])[:5])
    ask_depth = sum(list(getattr(market_data, "ask_vol", []) or [])[:5])
    context = ExitEvaluationContext(
      timestamp=timestamp,
      current_price=current_price,
      bid_price=float(bids[0] if bids and bids[0] else 0.0),
      ask_price=float(asks[0] if asks and asks[0] else 0.0),
      limit_up=float(
        getattr(market_data, "limit_up", 0.0) or configured_limit_up or 0.0
      ),
      limit_down=float(
        getattr(market_data, "limit_down", 0.0) or configured_limit_down or 0.0
      ),
      price_tick=float(
        getattr(market_data, "price_tick", 0.0) or configured_price_tick or 0.01
      ),
      cumulative_volume=getattr(market_data, "volume", None),
      cumulative_amount=getattr(market_data, "amount", None),
      depth_imbalance_5=(
        (bid_depth - ask_depth) / (bid_depth + ask_depth)
        if bid_depth + ask_depth > 0
        else None
      ),
      source=str(getattr(market_data, "source", "") or ""),
    )
    decisions = runtime.exit_plan_book.evaluate(instrument_code, context)
    if not decisions:
      self._persist_exit_plan_book(runtime)
      return

    intents: List[TradeIntent] = []
    for decision in decisions:
      plan = runtime.exit_plan_book.plans.get(decision.plan_id)
      if plan is None:
        continue
      execution = plan.template.execution
      if execution.price_reference == ExitPriceReference.BID:
        price_hint = context.bid_price or context.current_price
      elif execution.price_reference == ExitPriceReference.ASK:
        price_hint = context.ask_price or context.current_price
      else:
        price_hint = context.current_price
      execution_mode = TradeIntentExecutionMode(
        str(execution.execution_mode or "AUTO").upper()
      )
      if runtime.context.mode == StrategyRunMode.LIVE:
        # LIVE automation is granted only to the persisted plan version by
        # ExitPlanAuthorizationChallengeService.  This in-memory fallback has
        # no durable challenge envelope to validate, so it must fail closed
        # even when an old strategy template contains the legacy boolean.
        execution_mode = TradeIntentExecutionMode.MANUAL_CONFIRM
      priority = (
        TradeIntentPriority.URGENT
        if decision.rule_type
        in {
          ExitRuleType.HARD_STOP.value,
          ExitRuleType.LIMIT_UP_TOUCH.value,
          ExitRuleType.LIMIT_UP_BREAK.value,
          ExitRuleType.RAPID_PROFIT_REVERSAL.value,
          ExitRuleType.STOP_PRICE.value,
        }
        else TradeIntentPriority.RISK_REDUCTION
      )
      intent = TradeIntent(
        strategy_id=plan.template.strategy_id or str(runtime.strategy_id),
        run_id=plan.template.run_id or runtime.run_id,
        instrument_code=plan.template.instrument_code,
        direction=TradeIntentDirection.SELL,
        bucket=plan.template.bucket,
        reason=f"AUTO_EXIT_{decision.reason}",
        priority=priority,
        target_volume=decision.volume,
        limit_price_hint=price_hint,
        execution_mode=execution_mode,
        max_price_deviation_bps=execution.max_slippage_bps,
        metadata={
          **dict(plan.template.metadata or {}),
          "exit_plan_id": plan.plan_id,
          "exit_rule_id": decision.rule_id,
          "exit_rule_type": decision.rule_type,
          "exit_reason": decision.reason,
          "exit_plan_source_type": plan.template.source_type,
          "exit_plan_source_id": plan.template.source_id,
          "exit_plan_config_version": plan.template.config_version,
          "price_type": execution.price_type,
          "price_reference": execution.price_reference.value,
          "protected_limit": execution.protected_limit,
          "max_exit_slippage_bps": execution.max_slippage_bps,
          "execution_urgency": execution.urgency,
          "t1_policy": plan.template.t1_policy.value,
          "allow_t1_substitution": (
            plan.template.t1_policy == ExitT1Policy.ALLOW_SAME_INSTRUMENT_SUBSTITUTION
          ),
          "t1_insufficient_action": (
            "REJECT"
            if plan.template.t1_policy == ExitT1Policy.REJECT_IF_UNSELLABLE
            else "DELAY"
          ),
          "exit_metrics": dict(decision.metrics or {}),
        },
      )
      runtime.exit_plan_book.mark_intent(decision, intent.intent_id)
      intents.append(intent)

    self._persist_exit_plan_book(runtime)
    if intents:
      await self._process_strategy_output(
        runtime,
        StrategyOutput(
          trade_intents=intents,
          decision_tags=["auto_exit_plan_triggered"],
          trace_payload={
            "exit_plan_ids": [
              str(intent.metadata.get("exit_plan_id") or "") for intent in intents
            ]
          },
        ),
      )

  def _apply_exit_plan_order_event(
    self, runtime: StrategyRuntime, event: OrderStateEvent
  ) -> None:
    metadata = dict(event.metadata or {})
    plan_id = str(metadata.get("exit_plan_id", "") or "")
    if not plan_id:
      return
    event_time = event.timestamp or runtime.context.current_time or time_utils.now()
    runtime.exit_plan_book.apply_order_event(
      plan_id=plan_id,
      intent_id=str(metadata.get("intent_id", "") or ""),
      status=event.status,
      order_id=str(event.order_id or ""),
      risk_action=str(metadata.get("risk_action", "") or ""),
      timestamp_ms=int(event_time.timestamp() * 1000),
    )
    self._persist_exit_plan_book(runtime)

  def _apply_exit_plan_trade_event(
    self, runtime: StrategyRuntime, event: TradeExecutionEvent
  ) -> None:
    metadata = dict(event.metadata or {})
    trade_type = str(event.trade_type or "").upper()
    changed = False
    if trade_type == "BUY" and metadata.get("exit_plan_template"):
      runtime.exit_plan_book.register_entry_fill(
        metadata["exit_plan_template"],
        volume=event.volume,
        price=event.price,
        trade_time=event.trade_time or runtime.context.current_time,
      )
      changed = True
    elif trade_type == "SELL" and metadata.get("exit_plan_id"):
      runtime.exit_plan_book.apply_exit_fill(
        plan_id=str(metadata["exit_plan_id"]),
        volume=event.volume,
        price=event.price,
        rule_id=str(metadata.get("exit_rule_id", "") or ""),
      )
      changed = True
    if changed:
      runtime.exit_plan_book.prune_terminal(
        int(runtime.context.parameters.get("exit_plan_history_limit", 200) or 200)
      )
      self._persist_exit_plan_book(runtime)

  async def _run_backtest_timeline_with_ticks(
    self,
    runtime: StrategyRuntime,
    instrument_code: str,
    periods: List[str],
    start_time: datetime,
    end_time: datetime,
  ) -> None:
    """统一时间线回放：tick 驱动 + 触发多周期K线"""
    data_adapter = runtime.data_adapter
    if not isinstance(data_adapter, HistoricalDataAdapter):
      return

    total_ticks = 0
    total_klines_by_period = {period: 0 for period in periods}

    await self._run_backtest_warmup_klines(
      runtime, instrument_code, periods, start_time
    )

    market = "SH"  # 沪深市场的交易时间是一致的，所以使用 SH 就可以了

    trading_helper = TradingDateHelper()
    trading_dates = await trading_helper.get_trading_calendar(
      market=market,
      start_date=start_time.date(),
      end_date=end_time.date(),
    )
    if not trading_dates:
      self._runtime_log(
        runtime,
        "WARNING",
        f"回测区间无交易日: {instrument_code}, {start_time.date()} -> {end_time.date()}",
      )
      return

    window_hours = self._get_backtest_window_hours()
    last_tick_time: Optional[datetime] = None
    last_kline_time: Dict[str, Optional[datetime]] = {
      period: None for period in periods
    }

    session_start = time(9, 30)
    session_end = time(15, 30)

    for trading_date in trading_dates:
      if runtime.status != ExecutionStatus.RUNNING:
        break

      day_start = datetime.combine(trading_date, session_start)
      day_end = datetime.combine(trading_date, session_end)
      day_window_start = max(start_time, day_start)
      day_window_end = min(end_time, day_end)

      if day_window_end < day_window_start:
        continue

      for window_start, window_end in self._iter_backtest_windows(
        day_window_start, day_window_end, window_hours
      ):
        if runtime.status != ExecutionStatus.RUNNING:
          break

        self._runtime_log(
          runtime,
          "INFO",
          f"回测窗口开始: {instrument_code}, {window_start} -> {window_end}",
        )
        self.logger.info(
          "回测tick查询窗口: %s, local=%s~%s, utc=%s~%s",
          instrument_code,
          window_start,
          window_end,
          time_utils.to_utc(window_start),
          time_utils.to_utc(window_end),
        )

        ticks = await data_adapter.get_ticks(
          instrument_code=instrument_code,
          start_time=window_start,
          end_time=window_end,
          dividend_type="front",
          limit=6000,
        )
        if ticks:
          ticks = [
            t
            for t in ticks
            if t is not None
            and t.time is not None
            and (last_tick_time is None or t.time > last_tick_time)
          ]
          ticks = self._filter_backtest_continuous_session_events(ticks)
        else:
          ticks = []

        self._runtime_log(
          runtime,
          "INFO",
          f"回测tick查询结果: {instrument_code}, {window_start.date()}, ticks={len(ticks)}",
        )

        all_klines: Dict[str, List[KLine]] = {}
        for period in periods:
          period_lower = period.lower()
          is_intraday = period_lower.endswith("m") or period_lower.endswith("h")
          if is_intraday:
            kline_start = window_start
            kline_end = window_end
          else:
            kline_start = datetime.combine(trading_date, time(0, 0))
            kline_end = datetime.combine(trading_date, time(23, 59, 59))

          klines = await data_adapter.get_klines(
            instrument_code=instrument_code,
            period=period,
            start_time=kline_start,
            end_time=kline_end,
          )
          if klines:
            last_time = last_kline_time.get(period)
            if last_time is not None:
              klines = [
                k
                for k in klines
                if k is not None and k.time is not None and k.time > last_time
              ]
            else:
              klines = [k for k in klines if k is not None and k.time is not None]
            if self._is_backtest_intraday_period(period):
              klines = self._filter_backtest_continuous_session_events(klines)
          else:
            klines = []

          all_klines[period] = klines

        if not ticks and all(not v for v in all_klines.values()):
          self._runtime_log(
            runtime, "INFO", f"回测窗口无数据: {instrument_code}, {window_start.date()}"
          )
          continue

        tick_idx = 0
        kline_indices = {period: 0 for period in periods}
        kline_end_times: Dict[str, datetime] = {}

        kline_time_alignment = (
          runtime.context.parameters.get("kline_time_alignment", "end") or "end"
        ).lower()

        for period, klines in all_klines.items():
          if klines:
            kline_end_times[period] = self._get_kline_end_time(
              klines[0], period, alignment=kline_time_alignment
            )

        def has_more_data() -> bool:
          return tick_idx < len(ticks) or any(
            kline_indices[p] < len(all_klines[p]) for p in periods if p in all_klines
          )

        while has_more_data():
          if runtime.status != ExecutionStatus.RUNNING:
            break

          if tick_idx < len(ticks):
            tick = ticks[tick_idx]
            await self._process_tick(runtime, tick)
            if tick.time and (last_tick_time is None or tick.time > last_tick_time):
              last_tick_time = tick.time

            # tick 驱动 K 线触发（可能跨越多根K线）
            for period in periods:
              klines = all_klines.get(period, [])
              kline_idx = kline_indices[period]
              while (
                kline_idx < len(klines)
                and period in kline_end_times
                and tick.time >= kline_end_times[period]
              ):
                kline = klines[kline_idx]
                await self._process_kline(runtime, kline)
                if kline.time and (
                  last_kline_time.get(period) is None
                  or kline.time > last_kline_time[period]
                ):
                  last_kline_time[period] = kline.time
                kline_idx += 1
                if kline_idx < len(klines):
                  kline_end_times[period] = self._get_kline_end_time(
                    klines[kline_idx], period, alignment=kline_time_alignment
                  )

              kline_indices[period] = kline_idx

            tick_idx += 1
            continue

          # 无 tick 时，按时间顺序处理剩余K线
          next_period = None
          next_time = None
          for period in periods:
            kline_idx = kline_indices[period]
            klines = all_klines.get(period, [])
            if kline_idx < len(klines):
              kline_time = klines[kline_idx].time
              if next_time is None or kline_time < next_time:
                next_time = kline_time
                next_period = period

          if not next_period:
            break

          kline = all_klines[next_period][kline_indices[next_period]]
          await self._process_kline(runtime, kline)
          if kline.time and (
            last_kline_time.get(next_period) is None
            or kline.time > last_kline_time[next_period]
          ):
            last_kline_time[next_period] = kline.time
          kline_indices[next_period] += 1
          if kline_indices[next_period] < len(all_klines[next_period]):
            kline_end_times[next_period] = self._get_kline_end_time(
              all_klines[next_period][kline_indices[next_period]],
              next_period,
              alignment=kline_time_alignment,
            )

        total_ticks += tick_idx
        for period in periods:
          total_klines_by_period[period] += kline_indices.get(period, 0)

        if periods:
          per_period_summary = ", ".join(
            f"{period}:{kline_indices.get(period, 0)}" for period in periods
          )
        else:
          per_period_summary = "none"

        self._runtime_log(
          runtime,
          "INFO",
          f"回测窗口完成: {instrument_code}, {window_start} -> {window_end}, "
          f"tick={tick_idx}, kline={per_period_summary}",
        )

    total_klines = sum(total_klines_by_period.values())
    self._runtime_log(
      runtime,
      "SUCCESS",
      f"统一时间线回测完成: {instrument_code}, "
      f"处理了 {total_ticks} 个tick和 {total_klines} 根K线",
    )

  async def _run_backtest_timeline_with_klines(
    self,
    runtime: StrategyRuntime,
    instrument_code: str,
    periods: List[str],
    start_time: datetime,
    end_time: datetime,
  ) -> None:
    """统一时间线回放：多周期K线按时间顺序回放"""
    data_adapter = runtime.data_adapter
    if not isinstance(data_adapter, HistoricalDataAdapter):
      return

    market = runtime.context.parameters.get("market")
    if not market:
      market = "SZ" if instrument_code.endswith(".SZ") else "SH"

    trading_helper = TradingDateHelper()
    trading_dates = await trading_helper.get_trading_calendar(
      market=market,
      start_date=start_time.date(),
      end_date=end_time.date(),
    )
    if not trading_dates:
      self._runtime_log(
        runtime,
        "WARNING",
        f"回测区间无交易日: {instrument_code}, {start_time.date()} -> {end_time.date()}",
      )
      return

    total_klines_by_period = {period: 0 for period in periods}
    window_hours = self._get_backtest_window_hours()
    last_kline_time: Dict[str, Optional[datetime]] = {
      period: None for period in periods
    }

    await self._run_backtest_warmup_klines(
      runtime, instrument_code, periods, start_time
    )

    for trading_date in trading_dates:
      if runtime.status != ExecutionStatus.RUNNING:
        break

      day_window_start = max(start_time, datetime.combine(trading_date, time(0, 0)))
      day_window_end = min(end_time, datetime.combine(trading_date, time(23, 59, 59)))

      if day_window_end < day_window_start:
        continue

      for window_start, window_end in self._iter_backtest_windows(
        day_window_start, day_window_end, window_hours
      ):
        if runtime.status != ExecutionStatus.RUNNING:
          break

        all_klines: Dict[str, List[KLine]] = {}
        for period in periods:
          klines = await data_adapter.get_klines(
            instrument_code=instrument_code,
            period=period,
            start_time=window_start,
            end_time=window_end,
          )
          if klines:
            last_time = last_kline_time.get(period)
            if last_time is not None:
              klines = [
                k
                for k in klines
                if k is not None and k.time is not None and k.time > last_time
              ]
            else:
              klines = [k for k in klines if k is not None and k.time is not None]
            if self._is_backtest_intraday_period(period):
              klines = self._filter_backtest_continuous_session_events(klines)
          else:
            klines = []
          all_klines[period] = klines

        if all(not v for v in all_klines.values()):
          self._runtime_log(
            runtime,
            "INFO",
            f"回测窗口无数据: {instrument_code}, {window_start} -> {window_end}",
          )
          continue

        kline_indices = {period: 0 for period in periods}

        def has_more_klines() -> bool:
          return any(
            kline_indices[p] < len(all_klines[p]) for p in periods if p in all_klines
          )

        while has_more_klines():
          if runtime.status != ExecutionStatus.RUNNING:
            break

          next_period = None
          next_time = None
          for period in periods:
            kline_idx = kline_indices[period]
            klines = all_klines.get(period, [])
            if kline_idx < len(klines):
              kline_time = klines[kline_idx].time
              if next_time is None or kline_time < next_time:
                next_time = kline_time
                next_period = period

          if not next_period:
            break

          kline = all_klines[next_period][kline_indices[next_period]]
          await self._process_kline(runtime, kline)
          if kline.time and (
            last_kline_time.get(next_period) is None
            or kline.time > last_kline_time[next_period]
          ):
            last_kline_time[next_period] = kline.time
          kline_indices[next_period] += 1

        for period in periods:
          total_klines_by_period[period] += kline_indices.get(period, 0)

        if periods:
          per_period_summary = ", ".join(
            f"{period}:{kline_indices.get(period, 0)}" for period in periods
          )
        else:
          per_period_summary = "none"

        self._runtime_log(
          runtime,
          "INFO",
          f"回测窗口完成: {instrument_code}, {window_start} -> {window_end}, "
          f"kline={per_period_summary}",
        )

    total_klines = sum(total_klines_by_period.values())
    self._runtime_log(
      runtime,
      "SUCCESS",
      f"统一时间线回测完成: {instrument_code}, 处理了 {total_klines} 根K线",
    )

  def _get_kline_end_time(
    self,
    kline: KLine,
    period: str,
    alignment: str = "end",
  ) -> datetime:
    """获取K线结束时间

    alignment:
      - "end": kline.time 表示该K线结束时间（更常见）
      - "start": kline.time 表示该K线开始时间
    """
    period_map = {
      "1m": timedelta(minutes=1),
      "5m": timedelta(minutes=5),
      "15m": timedelta(minutes=15),
      "30m": timedelta(minutes=30),
      "60m": timedelta(hours=1),
      "1h": timedelta(hours=1),
      "1d": timedelta(days=1),
      "1w": timedelta(days=7),
    }
    alignment = (alignment or "end").lower()
    if alignment == "start":
      return kline.time + period_map.get(period, timedelta(minutes=1))
    return kline.time

  async def _notify_strategy_order(
    self,
    runtime: StrategyRuntime,
    event: OrderStateEvent,
  ) -> None:
    """Notify strategy about an order event and consume any returned state patch."""
    if not runtime.strategy:
      return
    try:
      self._apply_exit_plan_order_event(runtime, event)
      self._update_t_trade_entry_reservation(runtime, event)
      result = runtime.strategy.on_order(event)
      patch = await result if inspect.isawaitable(result) else result
      if patch:
        self._apply_runtime_state_patch(runtime, patch)
      self._refresh_t_trade_entry_reservation(runtime, event)
    except Exception as exc:
      if runtime.metrics:
        runtime.metrics.error_count += 1
      self._runtime_log(runtime, "ERROR", f"策略订单回调失败: {exc}")

  async def _notify_strategy_trade(
    self,
    runtime: StrategyRuntime,
    event: TradeExecutionEvent,
  ) -> None:
    """Notify strategy about a trade event and consume any returned state patch."""
    if not runtime.strategy:
      return
    try:
      self._apply_exit_plan_trade_event(runtime, event)
      result = runtime.strategy.on_trade(event)
      patch = await result if inspect.isawaitable(result) else result
      if patch:
        self._apply_runtime_state_patch(runtime, patch)
      self._refresh_t_trade_entry_reservation(runtime, event)
    except Exception as exc:
      if runtime.metrics:
        runtime.metrics.error_count += 1
      self._runtime_log(runtime, "ERROR", f"策略成交回调失败: {exc}")

  def _update_broker_report_health(
    self,
    runtime: StrategyRuntime,
    report_type: str,
    report: Any,
  ) -> None:
    reported_at = self._extract_report_time(report) or runtime.context.current_time
    reported_at = reported_at or time_utils.now()
    if report_type == "order":
      runtime.last_order_report_at = reported_at
    elif report_type == "trade":
      runtime.last_trade_report_at = reported_at
    runtime.last_broker_report_at = reported_at

  def _extract_report_time(self, report: Any) -> Optional[datetime]:
    for key in ("last_update_time", "trade_time", "submit_time", "timestamp"):
      value = self._get_value(report, key)
      if isinstance(value, datetime):
        return value
    return None

  def _build_open_order_snapshots(
    self,
    runtime: StrategyRuntime,
  ) -> List[Dict[str, Any]]:
    broker = runtime.broker
    if not broker:
      return []

    orders_by_id: Dict[str, Any] = {}
    raw_orders = getattr(broker, "orders", None)
    if isinstance(raw_orders, dict):
      for order_id, order in raw_orders.items():
        orders_by_id[str(order_id)] = order

    pending_orders = getattr(broker, "pending_orders", None)
    if isinstance(pending_orders, list):
      for order in pending_orders:
        order_id = str(self._get_value(order, "order_id", "") or "")
        if order_id:
          orders_by_id[order_id] = order

    snapshots: List[Dict[str, Any]] = []
    for order in orders_by_id.values():
      status = str(self._enum_value(self._get_value(order, "status", "")) or "").upper()
      if status not in {"PENDING", "SUBMITTED", "PARTIAL_FILLED"}:
        continue
      snapshots.append(self._summarize_open_order(order))

    return sorted(
      snapshots,
      key=lambda item: (
        str(item.get("submit_time") or ""),
        str(item.get("order_id") or ""),
      ),
    )

  def _summarize_open_order(self, order: Any) -> Dict[str, Any]:
    request = self._get_value(order, "request", {}) or {}
    volume = int(self._get_value(request, "volume", 0) or 0)
    filled_volume = int(self._get_value(order, "filled_volume", 0) or 0)
    return {
      "order_id": str(self._get_value(order, "order_id", "") or ""),
      "status": self._enum_value(self._get_value(order, "status", "")),
      "instrument_code": str(self._get_value(request, "instrument_code", "") or ""),
      "order_type": self._enum_value(self._get_value(request, "order_type", "")),
      "price_type": self._enum_value(self._get_value(request, "price_type", "")),
      "price": float(self._get_value(request, "price", 0.0) or 0.0),
      "volume": volume,
      "filled_volume": filled_volume,
      "remaining_volume": max(0, volume - filled_volume),
      "submit_time": self._serialize_datetime(self._get_value(order, "submit_time")),
      "last_update_time": self._serialize_datetime(
        self._get_value(order, "last_update_time")
      ),
      "metadata": dict(self._get_value(request, "metadata", {}) or {}),
    }

  def _build_order_state(self, open_orders: List[Dict[str, Any]]) -> Dict[str, Any]:
    status_counts: Dict[str, int] = {}
    order_type_counts: Dict[str, int] = {}
    buy_count = 0
    sell_count = 0
    oldest_open_order_at: Optional[str] = None
    for order in open_orders:
      status = str(order.get("status") or "").upper()
      order_type = str(order.get("order_type") or "").upper()
      status_counts[status] = status_counts.get(status, 0) + 1
      order_type_counts[order_type] = order_type_counts.get(order_type, 0) + 1
      if order_type in {"BUY", "BUY_TO_COVER"}:
        buy_count += 1
      elif order_type in {"SELL", "SELL_SHORT"}:
        sell_count += 1
      submit_time = order.get("submit_time")
      if submit_time and (
        oldest_open_order_at is None or str(submit_time) < oldest_open_order_at
      ):
        oldest_open_order_at = str(submit_time)
    return {
      "open_order_count": len(open_orders),
      "buy_open_order_count": buy_count,
      "sell_open_order_count": sell_count,
      "open_order_status_counts": status_counts,
      "open_order_type_counts": order_type_counts,
      "oldest_open_order_at": oldest_open_order_at,
    }

  def _build_broker_report(self, runtime: StrategyRuntime) -> Dict[str, Any]:
    last_report_at = runtime.last_broker_report_at
    if not last_report_at:
      return {}
    reference_time = runtime.context.current_time or time_utils.now()
    report_lag_seconds = max(
      0.0,
      (reference_time - last_report_at).total_seconds(),
    )
    return {
      "last_order_report_at": self._serialize_datetime(runtime.last_order_report_at),
      "last_trade_report_at": self._serialize_datetime(runtime.last_trade_report_at),
      "last_report_at": self._serialize_datetime(last_report_at),
      "report_lag_seconds": report_lag_seconds,
    }

  def _order_risk_strict_flags(self, runtime: StrategyRuntime) -> tuple[bool, bool]:
    params = dict(runtime.context.parameters or {})
    strict_market_default = runtime.context.mode in {
      StrategyRunMode.LIVE,
      StrategyRunMode.BACKTEST,
      StrategyRunMode.PAPER,
    }
    strict_limit_default = runtime.context.mode in {
      StrategyRunMode.LIVE,
      StrategyRunMode.BACKTEST,
    }
    return (
      self._bool_parameter(params, "strict_market_data", strict_market_default),
      self._bool_parameter(params, "strict_limit_data", strict_limit_default),
    )

  @staticmethod
  def _backtest_limit_rate(runtime: StrategyRuntime) -> Optional[float]:
    """Return an explicit backtest-only limit rate; never derive live limits."""

    if runtime.context.mode != StrategyRunMode.BACKTEST:
      return None
    try:
      rate = float(runtime.context.parameters.get("backtest_limit_rate", 0) or 0)
    except (TypeError, ValueError):
      return None
    return rate if 0 < rate < 1 else None

  def _bool_parameter(
    self,
    params: Dict[str, Any],
    key: str,
    default: bool,
  ) -> bool:
    value = params.get(key)
    if value is None:
      return default
    if isinstance(value, bool):
      return value
    if isinstance(value, str):
      text = value.strip().lower()
      if text in {"1", "true", "yes", "y", "on"}:
        return True
      if text in {"0", "false", "no", "n", "off"}:
        return False
    return bool(value)

  def _serialize_datetime(self, value: Any) -> Optional[str]:
    if value is None:
      return None
    if isinstance(value, datetime):
      return value.isoformat()
    return str(value)

  def _enum_value(self, value: Any) -> Any:
    return getattr(value, "value", value)

  def _get_value(self, source: Any, key: str, default: Any = None) -> Any:
    if source is None:
      return default
    if isinstance(source, dict):
      return source.get(key, default)
    return getattr(source, key, default)

  async def _process_event_queue(self, runtime: StrategyRuntime) -> None:
    """串行处理事件队列"""
    while (
      runtime.status in [ExecutionStatus.RUNNING, ExecutionStatus.PAUSED]
      and not self._shutdown_event.is_set()
    ):
      try:
        completion = None
        # 获取下一个事件
        try:
          event_type, data = await asyncio.wait_for(
            runtime.event_queue.get(), timeout=1.0
          )
        except asyncio.TimeoutError:
          continue

        if event_type in {"durable_order", "durable_trade"}:
          data, completion = data
          event_type = event_type.removeprefix("durable_")

        if runtime.status == ExecutionStatus.PAUSED and event_type not in [
          "order",
          "trade",
          "universe",
        ]:
          runtime.event_queue.task_done()
          continue

        # 根据事件类型分发
        if event_type == "kline":
          await self._process_kline(runtime, data)
        elif event_type == "tick":
          await self._process_tick(runtime, data)
        elif event_type == "order":
          self._update_broker_report_health(runtime, "order", data)
          if runtime.state_manager and hasattr(data, "status"):
            status = data.status
            request = getattr(data, "request", None)
            metadata = dict(getattr(request, "metadata", {}) or {})
            await runtime.state_manager.update_trade_intent_status(
              metadata.get("intent_id"),
              getattr(status, "value", str(status)),
              order_id=getattr(data, "order_id", None),
              risk_decision_id=metadata.get("risk_decision_id"),
            )
            if status in [
              OrderStatus.CANCELLED,
              OrderStatus.REJECTED,
              OrderStatus.EXPIRED,
            ]:
              runtime.state_manager.release_order_resources(data.order_id)
              if runtime.metrics:
                if status == OrderStatus.CANCELLED:
                  runtime.metrics.cancelled_orders += 1
                else:
                  runtime.metrics.rejected_orders += 1

          await self._notify_strategy_order(runtime, OrderStateEvent.from_raw(data))
          if runtime.performance_recorder:
            await runtime.performance_recorder.record(runtime, "order", data)

        elif event_type == "trade":
          self._update_broker_report_health(runtime, "trade", data)
          # 持久化成交记录（不再支持，如有独立成交表可在此处保存）
          # 但交易信号是独立表，如果这里能关联到信号，可以更新信号状态

          if runtime.state_manager:
            runtime.state_manager.apply_trade(data)
            metadata = dict(getattr(data, "metadata", {}) or {})
            trade_status = "FILLED"
            try:
              order = await runtime.broker.get_order(getattr(data, "order_id", ""))
              if order and order.status == OrderStatus.PARTIAL_FILLED:
                trade_status = "PARTIAL_FILLED"
            except Exception:
              trade_status = "FILLED"
            await runtime.state_manager.update_trade_intent_status(
              metadata.get("intent_id"),
              trade_status,
              order_id=getattr(data, "order_id", None),
              executed_price=float(getattr(data, "price", 0.0) or 0.0),
              executed_volume=int(getattr(data, "volume", 0) or 0),
              executed_time=getattr(data, "trade_time", None),
              accumulate_executed_volume=True,
            )

          await self._notify_strategy_trade(runtime, TradeExecutionEvent.from_raw(data))
          if runtime.performance_recorder:
            await runtime.performance_recorder.record(runtime, "trade", data)

        elif event_type == "universe":
          future = data.get("future")
          try:
            result = await self._apply_realtime_instrument_reconcile(
              runtime,
              list(data.get("instruments") or []),
              instrument_metadata=dict(data.get("instrument_metadata") or {}),
            )
            if future and not future.done():
              future.set_result(result)
          except Exception as exc:
            if future and not future.done():
              future.set_exception(exc)
            raise

        if completion is not None and not completion.done():
          completion.set_result(True)
        runtime.event_queue.task_done()

      except Exception as e:
        if completion is not None and not completion.done():
          completion.set_exception(e)
        self.logger.error(f"处理事件失败: {e}")
        if runtime.metrics:
          runtime.metrics.error_count += 1

  async def apply_durable_order_report(
    self,
    run_id: str,
    order: Any,
  ) -> None:
    """Apply a persisted order report on the runtime's serial event queue."""
    runtime = self.runs.get(run_id)
    if runtime is None:
      raise RuntimeError(f"策略运行尚未恢复: {run_id}")
    future = asyncio.get_running_loop().create_future()
    await runtime.event_queue.put(("durable_order", (order, future)))
    await future

  async def apply_durable_trade_report(
    self,
    run_id: str,
    trade: Any,
  ) -> None:
    """Apply a persisted execution report on the runtime's serial event queue."""
    runtime = self.runs.get(run_id)
    if runtime is None:
      raise RuntimeError(f"策略运行尚未恢复: {run_id}")
    future = asyncio.get_running_loop().create_future()
    await runtime.event_queue.put(("durable_trade", (trade, future)))
    await future

  async def _process_tick(self, runtime: StrategyRuntime, tick) -> None:
    """处理Tick数据"""
    strategy = runtime.strategy
    broker = runtime.broker
    metrics = runtime.metrics

    try:
      if tick.stock_code not in set(runtime.context.instruments or []):
        self.logger.debug("忽略已移出标的池的迟到 Tick: %s", tick.stock_code)
        return
      # 更新策略上下文时间
      runtime.context.current_time = tick.time
      if isinstance(runtime.data_adapter, HistoricalDataAdapter):
        runtime.data_adapter.current_time = tick.time
      market_snapshot = MarketDataSnapshot.from_tick(
        tick,
        limit_rate=self._backtest_limit_rate(runtime),
      )
      runtime.latest_market_data[tick.stock_code] = market_snapshot
      if runtime.state_manager:
        runtime.state_manager.settle_trading_day(tick.time.date())
      await self._expire_pending_approvals(runtime)
      await self._cancel_expired_strategy_orders(runtime, tick.time)

      # 更新回测 Broker 的市场数据
      if isinstance(broker, BacktestBroker):
        await broker.update_market_data(
          tick.stock_code,
          tick.last_price,
          tick.time,
          market_data=market_snapshot,
        )

      # 广播 Tick 数据到订阅者
      runtime.broadcast_tick(tick)

      await self._process_auto_exit_plans(
        runtime,
        instrument_code=tick.stock_code,
        timestamp=tick.time,
        market_data=market_snapshot,
      )

      strategy_input = self._build_strategy_input(
        runtime,
        cadence=StrategyCadence.TICK,
        instrument_code=tick.stock_code,
        timestamp=tick.time,
        market_data=market_snapshot,
        event=tick,
      )
      output = await strategy.step(strategy_input)
      await self._process_strategy_output(runtime, output, strategy_input)
      if runtime.performance_recorder:
        await runtime.performance_recorder.record(runtime, "tick", tick)

    except Exception as e:
      metrics.error_count += 1
      self.logger.error(f"处理Tick数据失败: {e}")

  async def _process_kline(self, runtime: StrategyRuntime, kline: KLine) -> None:
    """处理K线数据"""
    strategy = runtime.strategy
    broker = runtime.broker
    metrics = runtime.metrics

    try:
      if kline.stock_code not in set(runtime.context.instruments or []):
        self.logger.debug("忽略已移出标的池的迟到 K 线: %s", kline.stock_code)
        return
      # 更新策略上下文时间
      runtime.context.current_time = kline.time
      if isinstance(runtime.data_adapter, HistoricalDataAdapter):
        runtime.data_adapter.current_time = kline.time
      market_snapshot = MarketDataSnapshot.from_kline(
        kline,
        limit_rate=self._backtest_limit_rate(runtime),
      )
      runtime.latest_market_data[kline.stock_code] = market_snapshot
      if runtime.state_manager:
        runtime.state_manager.settle_trading_day(kline.time.date())
      await self._expire_pending_approvals(runtime)
      await self._cancel_expired_strategy_orders(runtime, kline.time)

      # 更新回测 Broker 的市场数据
      if isinstance(broker, BacktestBroker):
        await broker.update_market_data(
          kline.stock_code,
          kline.close,
          kline.time,
          market_data=market_snapshot,
        )

      # 广播 K线 数据到订阅者
      runtime.broadcast_kline(kline)

      await self._process_auto_exit_plans(
        runtime,
        instrument_code=kline.stock_code,
        timestamp=kline.time,
        market_data=market_snapshot,
      )

      strategy_input = self._build_strategy_input(
        runtime,
        cadence=StrategyCadence.BAR,
        instrument_code=kline.stock_code,
        timestamp=kline.time,
        market_data=market_snapshot,
        event=kline,
      )
      output = await strategy.step(strategy_input)
      await self._process_strategy_output(runtime, output, strategy_input)
      if runtime.performance_recorder:
        await runtime.performance_recorder.record(runtime, "bar", kline)

    except Exception as e:
      metrics.error_count += 1
      self.logger.error(f"处理K线数据失败: {e}")

  def _build_execution_context_snapshot(
    self,
    runtime: StrategyRuntime,
    *,
    instrument_code: str,
    market_data: Optional[MarketDataSnapshot] = None,
    event: Any = None,
    account: Optional[Dict[str, Any]] = None,
    positions: Optional[Dict[str, Any]] = None,
    bucket_ledger: Optional[Dict[str, Any]] = None,
  ) -> ExecutionContextSnapshot:
    if account is None or positions is None or bucket_ledger is None:
      state_account: Dict[str, Any] = {}
      state_positions: Dict[str, Any] = {}
      state_bucket_ledger: Dict[str, Any] = {}
      if runtime.state_manager:
        state_account = runtime.state_manager.get_account_quota()
        state_positions = runtime.state_manager.get_all_positions()
        state_bucket_ledger = runtime.state_manager.get_bucket_ledger_snapshot()
      account = state_account if account is None else account
      positions = state_positions if positions is None else positions
      bucket_ledger = state_bucket_ledger if bucket_ledger is None else bucket_ledger

    account = dict(account or {})
    positions = dict(positions or {})
    bucket_ledger = dict(bucket_ledger or {})
    runtime_state = runtime.strategy.state.to_dict() if runtime.strategy else {}
    parameters = dict(runtime.context.parameters or {})
    open_orders = self._build_open_order_snapshots(runtime)
    order_state = self._build_order_state(open_orders)
    broker_report = self._build_broker_report(runtime)
    market_context = self._build_market_context(runtime, market_data, event)
    risk_caps = self._build_risk_caps(
      runtime,
      account,
      positions,
      instrument_code,
      market_context=market_context,
      order_state=order_state,
      broker_report=broker_report,
      runtime_state=runtime_state,
      parameters=parameters,
    )
    portfolio_state = {"account": account, "positions": positions}
    position_profile = self._build_position_profile(
      runtime,
      portfolio_state=portfolio_state,
      market_context=market_context,
      risk_caps=risk_caps,
      bucket_ledger=bucket_ledger,
      instrument_code=instrument_code,
      runtime_state=runtime_state,
      parameters=parameters,
    )
    return ExecutionContextSnapshot(
      account=account,
      positions=positions,
      bucket_ledger=bucket_ledger,
      portfolio_state=portfolio_state,
      open_orders=open_orders,
      market_context=market_context,
      risk_caps=risk_caps,
      position_profile=position_profile,
      runtime_state=runtime_state,
      parameters=parameters,
    )

  def _build_strategy_input(
    self,
    runtime: StrategyRuntime,
    *,
    cadence: StrategyCadence,
    instrument_code: str,
    timestamp: datetime,
    market_data: Optional[MarketDataSnapshot] = None,
    event: Any = None,
  ) -> StrategyInput:
    snapshot = self._build_execution_context_snapshot(
      runtime,
      instrument_code=instrument_code,
      market_data=market_data,
      event=event,
    )

    return StrategyInput(
      run_id=runtime.run_id,
      strategy_id=str(runtime.strategy_id),
      timestamp=timestamp,
      cadence=cadence,
      instrument_code=instrument_code,
      market_data=market_data,
      event=event,
      portfolio_state=snapshot.portfolio_state,
      bucket_ledger=snapshot.bucket_ledger,
      market_context=snapshot.market_context,
      risk_caps=snapshot.risk_caps,
      position_profile=snapshot.position_profile,
      execution_profile=self._build_execution_profile(
        runtime=runtime,
        account=snapshot.account,
        positions=snapshot.positions,
        risk_caps=snapshot.risk_caps,
        position_profile=snapshot.position_profile,
        market_context=snapshot.market_context,
        runtime_state=snapshot.runtime_state,
        parameters=snapshot.parameters,
        instrument_code=instrument_code,
      ),
      exit_plans=runtime.exit_plan_book.projections(instrument_code),
      open_orders=snapshot.open_orders,
      strategy_state=snapshot.runtime_state,
      parameters=snapshot.parameters,
    )

  def _build_execution_profile(
    self,
    runtime: StrategyRuntime,
    *,
    account: Dict[str, Any],
    positions: Dict[str, Any],
    risk_caps: Dict[str, Any],
    position_profile: Dict[str, Any],
    market_context: Dict[str, Any],
    runtime_state: Dict[str, Any],
    parameters: Dict[str, Any],
    instrument_code: str,
  ) -> Dict[str, Any]:
    """Build strategy-facing execution profile for orchestration layer."""
    profile = PortfolioOrchestrationLayer().build_profile(
      market_context=market_context or {},
      risk_caps=risk_caps or {},
      position_profile=position_profile or {},
      portfolio_state={"account": account, "positions": positions},
      runtime_state=runtime_state or {},
      parameters=parameters or {},
      instrument_code=instrument_code,
    )
    return profile.to_dict()

  def _build_market_context(
    self,
    runtime: StrategyRuntime,
    market_data: Optional[MarketDataSnapshot],
    event: Any,
  ) -> Dict[str, Any]:
    params = dict(runtime.context.parameters or {})
    params.setdefault(
      "require_market_index",
      runtime.context.mode == StrategyRunMode.LIVE,
    )
    previous_market_context = params.get("previous_market_context")
    if previous_market_context is None and runtime.strategy:
      try:
        previous_market_context = runtime.strategy.state.get("last_market_context")
      except Exception:
        previous_market_context = None
    data_context = AshareDataContextProvider().build_context(
      instrument_code=(
        market_data.instrument_code
        if market_data and market_data.instrument_code
        else (getattr(event, "stock_code", None) or getattr(event, "code", None) or "")
      ),
      timestamp=(
        market_data.timestamp
        if market_data and market_data.timestamp
        else runtime.context.current_time
      ),
      market_data=market_data,
      event=event,
      parameters=params,
      previous_market_context=previous_market_context,
    )
    return data_context.market_context

  def _build_risk_caps(
    self,
    runtime: StrategyRuntime,
    account: Dict[str, Any],
    positions: Dict[str, Any],
    instrument_code: str,
    market_context: Optional[Dict[str, Any]] = None,
    order_state: Optional[Dict[str, Any]] = None,
    broker_report: Optional[Dict[str, Any]] = None,
    runtime_state: Optional[Dict[str, Any]] = None,
    parameters: Optional[Dict[str, Any]] = None,
  ) -> Dict[str, Any]:
    """Build deterministic pre-risk caps from run parameters and portfolio snapshot."""
    params = dict(
      parameters if parameters is not None else runtime.context.parameters or {}
    )
    state = dict(
      runtime_state
      if runtime_state is not None
      else (runtime.strategy.state.to_dict() if runtime.strategy else {})
    )
    return (
      ContextRiskLayer()
      .build_caps(
        portfolio_state={"account": account, "positions": positions},
        market_context=market_context or {},
        order_state=order_state or {},
        broker_report=broker_report or {},
        runtime_state=state,
        parameters=params,
        instrument_code=instrument_code,
      )
      .to_dict()
    )

  def _build_position_profile(
    self,
    runtime: StrategyRuntime,
    *,
    portfolio_state: Dict[str, Any],
    market_context: Dict[str, Any],
    risk_caps: Dict[str, Any],
    bucket_ledger: Dict[str, Any],
    instrument_code: str,
    runtime_state: Optional[Dict[str, Any]] = None,
    parameters: Optional[Dict[str, Any]] = None,
  ) -> Dict[str, Any]:
    params = dict(
      parameters if parameters is not None else runtime.context.parameters or {}
    )
    if params.get("position_profile") and not params.get("position_profile_overrides"):
      params["position_profile_overrides"] = dict(params.get("position_profile") or {})
    state = dict(
      runtime_state
      if runtime_state is not None
      else (runtime.strategy.state.to_dict() if runtime.strategy else {})
    )
    base_profile = (
      PositionAdjustmentLayer()
      .build_profile(
        market_context=market_context,
        risk_caps=risk_caps,
        portfolio_state=portfolio_state,
        bucket_ledger=bucket_ledger,
        runtime_state=state,
        parameters=params,
        instrument_code=instrument_code,
      )
      .to_dict()
    )
    base_profile.setdefault("instrument_code", instrument_code)
    return base_profile

  async def _process_strategy_output(
    self,
    runtime: StrategyRuntime,
    output: Optional[StrategyOutput],
    input_snapshot: Optional[StrategyInput] = None,
  ) -> None:
    if not output:
      return
    if output.runtime_state_patch:
      self._apply_runtime_state_patch(runtime, output.runtime_state_patch)
    if output.exit_plan_commands:
      for command in output.exit_plan_commands:
        runtime.exit_plan_book.apply_command(command)
      self._persist_exit_plan_book(runtime)
    intents = output.trade_intents or []
    if runtime.metrics:
      runtime.metrics.trade_intents_generated += len(intents)
    if runtime.state_manager:
      for intent in intents:
        status = (
          "AWAITING_APPROVAL"
          if intent.execution_mode == TradeIntentExecutionMode.MANUAL_CONFIRM
          else "PENDING"
        )
        await runtime.state_manager.record_trade_intent(intent, status=status)
    if runtime.strategy:
      for intent in intents:
        runtime.strategy.record_trade_intent(intent)
    self._record_strategy_output_trace(runtime, output, input_snapshot)
    for intent in intents:
      if intent.execution_mode == TradeIntentExecutionMode.MANUAL_CONFIRM:
        runtime.pending_approvals[intent.intent_id] = intent
        if (
          runtime.context.mode == StrategyRunMode.BACKTEST
          and runtime.context.parameters.get("auto_approve_manual_intents")
        ):
          result = await self.approve_trade_intent(runtime.run_id, intent.intent_id)
          self._runtime_log(
            runtime,
            "INFO" if result.get("success") else "WARNING",
            f"回放测试自动确认交易信号: intent_id={intent.intent_id}, "
            f"result={result.get('code')}",
          )
          continue
        self._runtime_log(
          runtime,
          "INFO",
          f"交易信号等待人工确认: {intent.instrument_code} {intent.direction.value} "
          f"intent_id={intent.intent_id}",
        )
        continue
      await self._process_trade_intent(runtime, intent)

  async def approve_trade_intent(self, run_id: str, intent_id: str) -> Dict[str, Any]:
    """Approve one manual-confirm intent after rechecking TTL and price drift."""

    runtime = self.runs.get(run_id)
    if runtime is None:
      return {"success": False, "code": "RUN_NOT_FOUND", "message": "策略运行不存在"}
    async with runtime.approval_lock:
      intent = runtime.pending_approvals.get(intent_id)
      if intent is None:
        return {
          "success": False,
          "code": "INTENT_NOT_AWAITING_APPROVAL",
          "message": "信号不存在、已处理或已过期",
        }

      failure = self._approval_failure(runtime, intent)
      if failure is not None:
        await self._reject_pending_approval(
          runtime,
          intent,
          status="EXPIRED",
          reason=failure[0],
          message=failure[1],
        )
        return {"success": False, "code": failure[0], "message": failure[1]}

      portfolio_failure = self._t_trade_portfolio_approval_failure(runtime, intent)
      if portfolio_failure is not None:
        return {
          "success": False,
          "code": portfolio_failure[0],
          "message": portfolio_failure[1],
        }

      self._reserve_t_trade_entry_exposure(runtime, intent)

      runtime.pending_approvals.pop(intent_id, None)
      if runtime.state_manager:
        await runtime.state_manager.update_trade_intent_status(
          intent_id,
          "APPROVED",
          notes="MANUAL_APPROVAL_ACCEPTED",
        )
      await self._notify_strategy_order(
        runtime,
        OrderStateEvent(
          order_id=None,
          status=OrderStatus.PENDING.value,
          metadata={
            **dict(intent.metadata or {}),
            "intent_id": intent.intent_id,
            "instrument_code": intent.instrument_code,
          },
        ),
      )
      await self._process_trade_intent(runtime, intent)
      return {
        "success": True,
        "code": "APPROVED",
        "message": "信号已确认并进入下单风控",
      }

  async def _restore_pending_manual_approvals(self, runtime: StrategyRuntime) -> None:
    """Restore only strategy-declared manual intents, preserving TTL semantics."""
    if not runtime.strategy or not runtime.state_manager:
      return
    for intent_id in runtime.strategy.pending_manual_intent_ids():
      intent = await runtime.state_manager.restore_manual_trade_intent(intent_id)
      if intent is None:
        continue
      failure = self._approval_failure(runtime, intent)
      if failure and failure[0] == "APPROVAL_TTL_EXPIRED":
        await self._reject_pending_approval(
          runtime,
          intent,
          status="EXPIRED",
          reason=failure[0],
          message=failure[1],
        )
        continue
      runtime.pending_approvals[intent.intent_id] = intent
      self._runtime_log(
        runtime,
        "INFO",
        f"已恢复待人工确认交易信号: intent_id={intent.intent_id}",
      )

  def _restore_t_trade_entry_reservations(self, runtime: StrategyRuntime) -> None:
    """Rebuild approved-but-unfinished T entry exposure after a restart."""
    if not runtime.strategy:
      return
    states = dict(runtime.strategy.state.get("instrument_states", {}) or {})
    for code, raw_state in states.items():
      state = dict(raw_state or {})
      status = str(state.get("entry_order_status", "") or "").upper()
      intent_id = str(state.get("pending_entry_intent_id", "") or "")
      if not intent_id or status not in {
        "PENDING",
        "SUBMITTED",
        "ACCEPTED",
        "PARTIAL_FILLED",
      }:
        continue
      requested = int(state.get("requested_entry_volume", 0) or 0)
      filled = int(state.get("entry_filled_volume", 0) or 0)
      remaining = max(0, requested - filled)
      if remaining <= 0:
        continue
      signal = dict(state.get("current_signal", {}) or {})
      price = float(
        signal.get("signal_price", 0.0)
        or state.get("last_price", 0.0)
        or state.get("entry_avg_price", 0.0)
        or 0.0
      )
      runtime.t_trade_entry_reservations[intent_id] = {
        "instrument_code": str(code),
        "batch_id": state.get("batch_id"),
        "requested_volume": requested,
        "volume": remaining,
        "price": price,
        "amount": remaining * price,
      }

  async def reject_trade_intent(
    self, run_id: str, intent_id: str, reason: str = "USER_REJECTED"
  ) -> Dict[str, Any]:
    """Reject one manual-confirm intent without creating any broker order."""

    runtime = self.runs.get(run_id)
    if runtime is None:
      return {"success": False, "code": "RUN_NOT_FOUND", "message": "策略运行不存在"}
    async with runtime.approval_lock:
      intent = runtime.pending_approvals.get(intent_id)
      if intent is None:
        return {
          "success": False,
          "code": "INTENT_NOT_AWAITING_APPROVAL",
          "message": "信号不存在、已处理或已过期",
        }
      await self._reject_pending_approval(
        runtime,
        intent,
        status="REJECTED",
        reason=reason,
        message="用户已忽略本次交易信号",
      )
      return {"success": True, "code": "REJECTED", "message": "信号已忽略"}

  async def cancel_open_buy_orders(self, run_id: str, reason: str) -> int:
    """Cancel this runtime's unfinished buy orders while preserving sell exits."""

    runtime = self.runs.get(run_id)
    broker = runtime.broker if runtime else None
    orders = getattr(broker, "orders", {}) if broker else {}
    if runtime is None or not isinstance(orders, dict):
      return 0
    cancelled_count = 0
    async with runtime.approval_lock:
      for order_id, order in list(orders.items()):
        raw_status = getattr(order, "status", "")
        status = str(getattr(raw_status, "value", raw_status)).upper()
        request = getattr(order, "request", None)
        raw_type = getattr(request, "order_type", "")
        order_type = str(getattr(raw_type, "value", raw_type)).upper()
        if status not in {
          "PENDING",
          "SUBMITTED",
          "ACCEPTED",
          "PARTIAL_FILLED",
        } or order_type not in {"BUY", "BUY_TO_COVER"}:
          continue
        if not await broker.cancel_order(str(order_id)):
          continue
        if runtime.state_manager:
          runtime.state_manager.release_order_resources(str(order_id))
          intent_id = str(
            dict(getattr(request, "metadata", {}) or {}).get("intent_id") or ""
          )
          if intent_id:
            await runtime.state_manager.update_trade_intent_status(
              intent_id,
              "CANCELLED",
              order_id=str(order_id),
              notes=reason,
            )
        await self._notify_strategy_order(
          runtime,
          OrderStateEvent(
            order_id=str(order_id),
            status=OrderStatus.CANCELLED.value,
            request=request,
            metadata=dict(getattr(request, "metadata", {}) or {}),
          ),
        )
        cancelled_count += 1
    return cancelled_count

  def _approval_failure(
    self, runtime: StrategyRuntime, intent: TradeIntent
  ) -> Optional[tuple[str, str]]:
    ttl_ms = int(intent.approval_ttl_ms or 0)
    if ttl_ms > 0:
      elapsed_ms = (time_utils.now() - intent.created_at).total_seconds() * 1000
      if elapsed_ms > ttl_ms:
        return "APPROVAL_TTL_EXPIRED", "信号已超过确认有效期，请等待新信号"

    market_data = runtime.latest_market_data.get(intent.instrument_code)
    quote_max_age = float(
      dict(runtime.context.parameters or {}).get(
        "execution_quote_max_age_seconds",
        0.0,
      )
      or 0.0
    )
    if intent.direction == TradeIntentDirection.BUY and quote_max_age > 0:
      if market_data is None:
        return "APPROVAL_QUOTE_MISSING", "确认时缺少最新执行行情，请等待新信号"
      quote_at = getattr(market_data, "timestamp", None)
      if not isinstance(quote_at, datetime):
        try:
          quote_at = datetime.fromisoformat(str(quote_at).replace("Z", "+00:00"))
        except (TypeError, ValueError):
          return "APPROVAL_QUOTE_STALE", "确认时执行行情时间无效，请等待新信号"
      approval_at = time_utils.now()
      if quote_at.tzinfo is None and approval_at.tzinfo is not None:
        quote_at = quote_at.replace(tzinfo=approval_at.tzinfo)
      if quote_at.tzinfo is not None and approval_at.tzinfo is None:
        approval_at = approval_at.replace(tzinfo=quote_at.tzinfo)
      quote_age = max(0.0, (approval_at - quote_at).total_seconds())
      if quote_age > quote_max_age:
        return "APPROVAL_QUOTE_STALE", "确认时执行行情已超过有效期，请等待新信号"
    reference_price = float(intent.limit_price_hint or 0.0)
    current_price = float(getattr(market_data, "price", 0.0) or 0.0)
    if intent.direction == TradeIntentDirection.BUY and market_data:
      asks = list(getattr(market_data, "ask_price", []) or [])
      current_price = float(asks[0] if asks and asks[0] else current_price)
    if intent.direction == TradeIntentDirection.SELL and market_data:
      bids = list(getattr(market_data, "bid_price", []) or [])
      current_price = float(bids[0] if bids and bids[0] else current_price)
    max_deviation_bps = float(intent.max_price_deviation_bps or 0.0)
    if reference_price > 0 and current_price > 0 and max_deviation_bps > 0:
      deviation_bps = abs(current_price - reference_price) / reference_price * 10000
      if deviation_bps > max_deviation_bps:
        return "PRICE_DEVIATION_EXCEEDED", "价格已偏离信号价，请等待新信号"
    if runtime.strategy is not None:
      strategy_failure = runtime.strategy.validate_manual_approval(
        intent,
        market_data,
      )
      if strategy_failure is not None:
        return strategy_failure
    return None

  def _t_trade_portfolio_approval_failure(
    self, runtime: StrategyRuntime, intent: TradeIntent
  ) -> Optional[tuple[str, str]]:
    metadata = dict(intent.metadata or {})
    if (
      intent.direction != TradeIntentDirection.BUY
      or metadata.get("t_trade_role") != "entry"
    ):
      return None

    params = dict(runtime.context.parameters or {})
    max_batches = max(1, int(params.get("max_concurrent_batches", 3) or 3))
    max_exposure_pct = float(params.get("max_total_t_exposure_pct", 0.1) or 0.1)
    states = {}
    if runtime.strategy:
      states = dict(runtime.strategy.state.get("instrument_states", {}) or {})

    active_batch_keys: set[str] = set()
    active_exposure = 0.0
    for instrument_code, state in states.items():
      item = dict(state or {})
      active_volume = max(
        0,
        int(item.get("entry_filled_volume", 0) or 0)
        - int(item.get("exit_filled_volume", 0) or 0),
      )
      if active_volume > 0:
        batch_key = str(
          item.get("batch_id") or item.get("instrument_code") or instrument_code
        )
        active_batch_keys.add(batch_key)
        active_exposure += active_volume * float(
          item.get("entry_avg_price", 0.0) or 0.0
        )
      elif item.get("batch_id") and str(
        item.get("entry_order_status", "") or ""
      ).upper() in {
        "PENDING",
        "SUBMITTED",
        "ACCEPTED",
        "PARTIAL_FILLED",
        "FILLED",
      }:
        active_batch_keys.add(str(item["batch_id"]))

    reserved_batch_keys = {
      str(item.get("batch_id") or item.get("instrument_code") or intent_id)
      for intent_id, item in runtime.t_trade_entry_reservations.items()
    }
    if len(active_batch_keys | reserved_batch_keys) >= max_batches:
      return (
        "T_TRADE_CONCURRENT_BATCH_LIMIT",
        f"账户级做 T 批次已达到上限（{max_batches} 个），信号仍保留至过期",
      )

    market_data = runtime.latest_market_data.get(intent.instrument_code)
    asks = list(getattr(market_data, "ask_price", []) or []) if market_data else []
    current_price = float(
      (asks[0] if asks and asks[0] else 0.0)
      or getattr(market_data, "price", 0.0)
      or intent.limit_price_hint
      or 0.0
    )
    requested_volume = int(intent.target_volume or 0)
    requested_amount = current_price * requested_volume
    max_trade_amount = float(params.get("max_trade_amount", 12_000.0) or 12_000.0)
    account = runtime.state_manager.get_account_quota() if runtime.state_manager else {}
    total_asset = float(
      account.get("total_asset", account.get("total_value", 0.0)) or 0.0
    )
    if total_asset <= 0 or current_price <= 0 or requested_volume <= 0:
      return (
        "T_TRADE_PORTFOLIO_SNAPSHOT_STALE",
        "账户资产或最新可执行价格不可用，暂不允许确认新批次",
      )
    if requested_amount > max_trade_amount + 1e-6:
      return (
        "T_TRADE_SINGLE_AMOUNT_LIMIT",
        f"按最新卖一价计算将超过单次金额硬上限 ¥{max_trade_amount:,.2f}",
      )

    reserved_exposure = sum(
      float(item.get("amount", 0.0) or 0.0)
      for item in runtime.t_trade_entry_reservations.values()
    )
    if (
      active_exposure + reserved_exposure + requested_amount
      > total_asset * max_exposure_pct
    ):
      return (
        "T_TRADE_TOTAL_EXPOSURE_LIMIT",
        f"确认后将超过账户总资产 {max_exposure_pct * 100:g}% 的做 T 敞口上限",
      )
    return None

  def _reserve_t_trade_entry_exposure(
    self, runtime: StrategyRuntime, intent: TradeIntent
  ) -> None:
    metadata = dict(intent.metadata or {})
    if (
      intent.direction != TradeIntentDirection.BUY
      or metadata.get("t_trade_role") != "entry"
    ):
      return
    market_data = runtime.latest_market_data.get(intent.instrument_code)
    asks = list(getattr(market_data, "ask_price", []) or []) if market_data else []
    price = float(
      (asks[0] if asks and asks[0] else 0.0)
      or getattr(market_data, "price", 0.0)
      or intent.limit_price_hint
      or 0.0
    )
    runtime.t_trade_entry_reservations[intent.intent_id] = {
      "instrument_code": intent.instrument_code,
      "batch_id": metadata.get("t_batch_id"),
      "requested_volume": int(intent.target_volume or 0),
      "volume": int(intent.target_volume or 0),
      "price": price,
      "amount": price * int(intent.target_volume or 0),
    }

  def _update_t_trade_entry_reservation(
    self, runtime: StrategyRuntime, order: Any
  ) -> None:
    request = self._get_value(order, "request")
    metadata = dict(self._get_value(order, "metadata", {}) or {})
    if not metadata:
      metadata = dict(self._get_value(request, "metadata", {}) or {})
    if metadata.get("t_trade_role") != "entry":
      return
    intent_id = str(metadata.get("intent_id", "") or "")
    if not intent_id or intent_id not in runtime.t_trade_entry_reservations:
      return
    raw_status = self._get_value(order, "status", "")
    status = str(getattr(raw_status, "value", raw_status)).upper()
    if status not in {"FILLED", "REJECTED", "CANCELLED", "EXPIRED"}:
      return
    reservation = runtime.t_trade_entry_reservations[intent_id]
    requested_volume = int(
      reservation.get("requested_volume", 0)
      or self._get_value(request, "volume", 0)
      or 0
    )
    filled_volume = int(self._get_value(order, "filled_volume", 0) or 0)
    terminal_volume = requested_volume if status == "FILLED" else filled_volume
    if terminal_volume <= 0:
      runtime.t_trade_entry_reservations.pop(intent_id, None)
      return
    reservation["terminal_status"] = status
    reservation["terminal_filled_volume"] = terminal_volume

  def _refresh_t_trade_entry_reservation(
    self, runtime: StrategyRuntime, report: Any
  ) -> None:
    """Keep exposure reserved until trade details are reflected in strategy state."""

    request = self._get_value(report, "request")
    metadata = dict(self._get_value(report, "metadata", {}) or {})
    if not metadata:
      metadata = dict(self._get_value(request, "metadata", {}) or {})
    if metadata.get("t_trade_role") != "entry":
      return
    intent_id = str(metadata.get("intent_id", "") or "")
    reservation = runtime.t_trade_entry_reservations.get(intent_id)
    if reservation is None:
      batch_id = str(metadata.get("t_batch_id", "") or "")
      instrument_code = str(
        self._get_value(report, "instrument_code", "")
        or metadata.get("instrument_code", "")
        or self._get_value(request, "instrument_code", "")
        or ""
      )
      for candidate_id, candidate in runtime.t_trade_entry_reservations.items():
        if batch_id and str(candidate.get("batch_id", "") or "") == batch_id:
          intent_id, reservation = candidate_id, candidate
          break
        candidate_code = str(candidate.get("instrument_code", "") or "")
        if instrument_code and candidate_code == instrument_code:
          intent_id, reservation = candidate_id, candidate
          break
    if reservation is None or not runtime.strategy:
      return

    code = str(reservation.get("instrument_code", "") or "")
    state = dict(
      dict(runtime.strategy.state.get("instrument_states", {}) or {}).get(code, {})
      or {}
    )
    reflected_volume = max(0, int(state.get("entry_filled_volume", 0) or 0))
    requested_volume = max(
      0,
      int(reservation.get("requested_volume", 0) or reservation.get("volume", 0) or 0),
    )
    terminal_status = str(reservation.get("terminal_status", "") or "")
    terminal_volume = max(
      0,
      int(reservation.get("terminal_filled_volume", 0) or 0),
    )
    if terminal_status and reflected_volume >= terminal_volume:
      runtime.t_trade_entry_reservations.pop(intent_id, None)
      return
    remaining = max(
      0,
      (terminal_volume if terminal_status else requested_volume) - reflected_volume,
    )
    reservation["volume"] = remaining
    reservation["amount"] = remaining * float(reservation.get("price", 0.0) or 0.0)

  async def _expire_pending_approvals(self, runtime: StrategyRuntime) -> None:
    for intent in list(runtime.pending_approvals.values()):
      failure = self._approval_failure(runtime, intent)
      if failure and failure[0] == "APPROVAL_TTL_EXPIRED":
        await self._reject_pending_approval(
          runtime,
          intent,
          status="EXPIRED",
          reason=failure[0],
          message=failure[1],
        )

  async def _cancel_expired_strategy_orders(
    self,
    runtime: StrategyRuntime,
    _timestamp: datetime,
  ) -> None:
    """Request cancellation for live/paper orders whose strategy TTL elapsed.

    BacktestBroker applies the same rule inside its deterministic market update
    so the expiry event is ordered before any fill on the next quote.
    """

    broker = runtime.broker
    if not broker or runtime.context.mode == StrategyRunMode.BACKTEST:
      return
    now_ms = int(time_utils.now().timestamp() * 1000)
    for order_id, order in list(getattr(broker, "orders", {}).items()):
      raw_status = getattr(order, "status", "")
      status = str(getattr(raw_status, "value", raw_status)).upper()
      if status not in {
        "PENDING",
        "SUBMITTED",
        "ACCEPTED",
        "PARTIAL_FILLED",
      }:
        continue
      request = getattr(order, "request", None)
      metadata = dict(getattr(request, "metadata", {}) or {})
      try:
        expire_at_ms = int(metadata.get("order_expire_at_ms", 0) or 0)
      except (TypeError, ValueError):
        expire_at_ms = 0
      if (
        expire_at_ms <= 0
        or now_ms < expire_at_ms
        or metadata.get("expiry_cancel_requested")
      ):
        continue
      cancelled = await broker.cancel_order(order_id)
      if not cancelled:
        continue
      getattr(request, "metadata", {})["expiry_cancel_requested"] = True
      self._runtime_log(
        runtime,
        "INFO",
        f"策略委托已超过有效期，已请求撤单: order_id={order_id}",
      )

  async def _reject_pending_approval(
    self,
    runtime: StrategyRuntime,
    intent: TradeIntent,
    *,
    status: str,
    reason: str,
    message: str,
  ) -> None:
    runtime.pending_approvals.pop(intent.intent_id, None)
    if runtime.state_manager:
      await runtime.state_manager.update_trade_intent_status(
        intent.intent_id,
        status,
        notes=reason,
      )
    await self._notify_strategy_order(
      runtime,
      OrderStateEvent(
        order_id=None,
        status=status,
        error_message=message,
        metadata={
          **dict(intent.metadata or {}),
          "intent_id": intent.intent_id,
          "approval_reason": reason,
        },
      ),
    )

  def _record_strategy_output_trace(
    self,
    runtime: StrategyRuntime,
    output: StrategyOutput,
    input_snapshot: Optional[StrategyInput],
  ) -> None:
    if not runtime.state_manager or input_snapshot is None:
      return
    patch = output.runtime_state_patch
    state_patch = {}
    if patch:
      state_patch = {
        "set": dict(getattr(patch, "set", {}) or {}),
        "unset": list(getattr(patch, "unset", []) or []),
        "append_events": list(getattr(patch, "append_events", []) or []),
      }
    intents = [summarize_intent(intent) for intent in output.trade_intents or []]
    output_summary = {
      "trade_intent_count": len(intents),
      "decision_tags": list(output.decision_tags or []),
      "trace_payload": dict(output.trace_payload or {}),
    }
    trace = DecisionTrace.from_decision(
      run_id=runtime.run_id,
      strategy_id=str(runtime.strategy_id),
      instrument_code=input_snapshot.instrument_code,
      input_summary=summarize_strategy_input(input_snapshot),
      environment=dict(input_snapshot.market_context or {}),
      risk_caps=dict(input_snapshot.risk_caps or {}),
      position_profile=dict(input_snapshot.position_profile or {}),
      execution_profile=dict(input_snapshot.execution_profile or {}),
      output_summary=output_summary,
      state_patch=state_patch,
      trade_intents=intents,
      trace_id=input_snapshot.trace_id,
      tags=["strategy_output", *list(output.decision_tags or [])],
      reason=(
        str((output.trace_payload or {}).get("reason") or "")
        or ("NO_TRADE_INTENT" if not intents else "TRADE_INTENT_GENERATED")
      ),
    )
    runtime.state_manager.record_decision_trace(trace)

  def _apply_runtime_state_patch(self, runtime: StrategyRuntime, patch) -> None:
    if not runtime.strategy or not patch:
      return
    updates = dict(getattr(patch, "set", {}) or {})
    unset = list(getattr(patch, "unset", []) or [])
    events = list(getattr(patch, "append_events", []) or [])
    if updates:
      runtime.strategy.state.update(updates)
    if unset:
      state = runtime.strategy.state.to_dict()
      for key in unset:
        state.pop(key, None)
      runtime.strategy.state.replace(state, notify=True)
    if events:
      existing = list(runtime.strategy.state.get("runtime_events", []) or [])
      existing.extend(events)
      runtime.strategy.state.runtime_events = existing[-200:]

  def apply_external_state_patch(self, run_id: str, patch) -> None:
    """Apply a state patch produced by an explicit, audited external action."""
    runtime = self.runs.get(run_id)
    if runtime is None or runtime.strategy is None:
      raise ValueError("策略运行不存在或尚未启动")
    self._apply_runtime_state_patch(runtime, patch)

  async def _process_trade_intent(
    self, runtime: StrategyRuntime, intent: TradeIntent
  ) -> None:
    """处理策略交易意图"""
    broker = runtime.broker
    metrics = runtime.metrics

    try:
      from quantx_domain.brokers.base import PriceType

      if intent.direction == TradeIntentDirection.BUY:
        order_type = BrokerOrderType.BUY
      elif intent.direction == TradeIntentDirection.SELL:
        order_type = BrokerOrderType.SELL
      else:
        return

      market_data = runtime.latest_market_data.get(intent.instrument_code)
      strict_market_data, strict_limit_data = self._order_risk_strict_flags(runtime)
      if market_data is None and not strict_market_data:
        market_data = MarketDataSnapshot(
          instrument_code=intent.instrument_code,
          timestamp=runtime.context.current_time,
          price=float(intent.limit_price_hint or 0.0),
          close=float(intent.limit_price_hint or 0.0),
          source="intent",
        )

      rules = AShareMarketRules()
      price_source = intent.limit_price_hint or (
        market_data.price if market_data else 0.0
      )
      price_tick = market_data.price_tick if market_data else None
      price = rules.normalize_price(price_source, price_tick)

      account = {}
      position = {}
      if runtime.state_manager:
        account = runtime.state_manager.get_account_quota()
        position = runtime.state_manager.get_position(intent.instrument_code) or {}
      elif broker:
        account_info = await broker.get_account()
        account = {
          "available_cash": account_info.cash,
          "frozen_cash": account_info.frozen_cash,
          "cash_total": account_info.cash + account_info.frozen_cash,
          "total_asset": account_info.total_asset,
        }
        positions = await broker.get_position(intent.instrument_code)
        broker_position = positions.get(intent.instrument_code)
        if broker_position:
          position = {
            "long_volume": broker_position.long_volume,
            "available_volume": broker_position.available_volume
            or broker_position.long_volume,
          }

      context_snapshot = self._build_execution_context_snapshot(
        runtime,
        instrument_code=intent.instrument_code,
        market_data=market_data,
        account=account,
        positions={intent.instrument_code: position},
      )
      sizer = OrderSizer(rules)
      draft = sizer.draft_intent(intent, order_type, price, account, position)
      if draft.sized_volume <= 0:
        size_reasons = list(getattr(draft, "size_reason_codes", []) or [])
        rejection_reason = (
          "MIN_LOT_EXCEEDS_RISK_BUDGET"
          if "MIN_LOT_EXCEEDS_RISK_BUDGET" in size_reasons
          else "ZERO_SIZED_VOLUME"
        )
        if runtime.state_manager:
          await runtime.state_manager.update_trade_intent_status(
            intent.intent_id,
            "REJECTED",
            metadata={
              **dict(intent.metadata or {}),
              "order_draft_id": getattr(draft, "draft_id", None),
              "order_draft_size_reasons": size_reasons,
              "sized_volume": getattr(draft, "sized_volume", None),
            },
            notes=rejection_reason,
          )
        self._record_decision_trace(
          runtime,
          intent=intent,
          market_context=context_snapshot.market_context,
          risk_caps=context_snapshot.risk_caps,
          position_profile=context_snapshot.position_profile,
          order_draft=draft,
          tags=[rejection_reason.lower()],
          reason=rejection_reason,
        )
        await self._notify_strategy_order(
          runtime,
          OrderStateEvent(
            order_id=None,
            status=OrderStatus.REJECTED.value,
            request={
              "instrument_code": intent.instrument_code,
              "order_type": order_type,
              "metadata": {
                **(intent.metadata or {}),
                "intent_id": intent.intent_id,
                "order_draft": draft.__dict__,
              },
            },
            error_message="交易意图无法转换为合法订单数量",
            metadata={
              **(intent.metadata or {}),
              "intent_id": intent.intent_id,
              "order_draft": draft.__dict__,
            },
          ),
        )
        self.logger.warning(
          f"交易意图无法转换为合法订单数量: {intent.instrument_code} {order_type.value}"
        )
        return

      try:
        order_ttl_ms = max(
          0,
          int((intent.metadata or {}).get("order_ttl_ms", 0) or 0),
        )
      except (TypeError, ValueError):
        order_ttl_ms = 0
      order_created_at = (
        runtime.context.current_time
        if runtime.context.mode == StrategyRunMode.BACKTEST
        else time_utils.now()
      ) or time_utils.now()
      order_expire_at_ms = (
        int(order_created_at.timestamp() * 1000) + order_ttl_ms
        if order_ttl_ms > 0
        else 0
      )
      request = OrderRequest(
        instrument_code=intent.instrument_code,
        order_type=order_type,
        price_type=(
          PriceType.MARKET
          if str((intent.metadata or {}).get("price_type", "LIMIT")).upper() == "MARKET"
          else PriceType.LIMIT
        ),
        volume=draft.sized_volume,
        price=price,
        strategy_id=str(runtime.strategy_id),
        metadata={
          **(intent.metadata or {}),
          "strategy_run_id": runtime.run_id,
          "strategy_order_id": "",
          "execution_mode": runtime.context.mode.value,
          "intent_id": intent.intent_id,
          "order_draft_id": draft.draft_id,
          "order_draft_size_reasons": draft.size_reason_codes,
          "bucket": intent.bucket,
          "reason": intent.reason,
          "priority": intent.priority.value,
          "expiry_policy": dict(intent.expiry_policy or {}),
          "approval_ttl_ms": intent.approval_ttl_ms,
          "order_ttl_ms": order_ttl_ms,
          "order_expire_at_ms": order_expire_at_ms,
        },
      )

      checker = TradingRiskChecker(
        rules,
        commission_rate=getattr(broker, "commission_rate", 0.0003),
        min_commission=getattr(broker, "min_commission", 5.0),
        strict_market_data=strict_market_data,
        strict_limit_data=strict_limit_data,
        enforce_trading_hours=bool(
          runtime.context.parameters.get(
            "enforce_trading_hours",
            runtime.context.mode == StrategyRunMode.LIVE,
          )
        ),
        market=runtime.context.parameters.get("market", "SH"),
      )
      decision: OrderRiskDecision = await checker.evaluate_order(
        request,
        account=account,
        position=position,
        market_data=market_data,
        current_time=runtime.context.current_time,
        risk_caps=context_snapshot.risk_caps,
      )
      request.metadata.update(
        {
          "risk_decision_id": decision.risk_decision_id,
          "risk_action": decision.action.value,
          "risk_reason_code": decision.reason_code,
          "risk_tags": decision.risk_tags,
          "substitution_plan": decision.substitution_plan,
        }
      )
      if not decision.allowed:
        if runtime.state_manager:
          await runtime.state_manager.update_trade_intent_status(
            intent.intent_id,
            "DELAYED" if decision.action == RiskAction.DELAY else "REJECTED",
            risk_decision_id=decision.risk_decision_id,
            metadata={
              **dict(intent.metadata or {}),
              "order_draft_id": draft.draft_id,
              "order_draft_size_reasons": draft.size_reason_codes,
              "sized_volume": draft.sized_volume,
              "risk_reason_code": decision.reason_code,
              "risk_action": decision.action.value,
              "risk_tags": decision.risk_tags,
            },
            notes=decision.reason_detail,
          )
        self._record_decision_trace(
          runtime,
          intent=intent,
          market_context=context_snapshot.market_context,
          risk_caps=context_snapshot.risk_caps,
          position_profile=context_snapshot.position_profile,
          order_draft=draft,
          order_request=request,
          risk_decision=decision,
          tags=["risk_blocked", decision.action.value],
          reason=decision.reason_code,
        )
        status = (
          OrderStatus.PENDING.value
          if decision.action == RiskAction.DELAY
          else OrderStatus.REJECTED.value
        )
        await self._notify_strategy_order(
          runtime,
          OrderStateEvent(
            order_id=None,
            status=status,
            request=request,
            error_message=decision.reason_detail,
            metadata=request.metadata,
          ),
        )
        self.logger.warning(
          f"下单前校验失败: {intent.instrument_code} {order_type.value} "
          f"{request.volume}股, 原因: {decision.reason_code} {decision.reason_detail}"
        )
        return
      if decision.final_volume != request.volume:
        request.volume = decision.final_volume

      reservation_key = intent.intent_id
      reserved = await self._reserve_order_resources(runtime, reservation_key, request)
      if not reserved:
        if runtime.state_manager:
          await runtime.state_manager.update_trade_intent_status(
            intent.intent_id,
            "REJECTED",
            risk_decision_id=decision.risk_decision_id,
            metadata={
              **dict(intent.metadata or {}),
              **dict(request.metadata or {}),
              "sized_volume": request.volume,
            },
            notes="RESERVE_FAILED",
          )
        self._record_decision_trace(
          runtime,
          intent=intent,
          market_context=context_snapshot.market_context,
          risk_caps=context_snapshot.risk_caps,
          position_profile=context_snapshot.position_profile,
          order_draft=draft,
          order_request=request,
          risk_decision=decision,
          tags=["reserve_failed"],
          reason="RESERVE_FAILED",
        )
        await self._notify_strategy_order(
          runtime,
          OrderStateEvent(
            order_id=None,
            status=OrderStatus.REJECTED.value,
            request=request,
            error_message="订单资源冻结失败",
            metadata=request.metadata,
          ),
        )
        self.logger.warning(
          f"订单资源冻结失败: {intent.instrument_code} {order_type.value} "
          f"{request.volume}股"
        )
        return

      # 下单
      try:
        order = await broker.place_order(request)
      except Exception:
        if runtime.state_manager:
          runtime.state_manager.release_order_resources(reservation_key)
        raise

      if runtime.state_manager:
        runtime.state_manager.transfer_reservation(reservation_key, order.order_id)
        await runtime.state_manager.update_trade_intent_status(
          intent.intent_id,
          order.status.value,
          order_id=order.order_id,
          risk_decision_id=decision.risk_decision_id,
          metadata={
            **dict(intent.metadata or {}),
            **dict(request.metadata or {}),
            "sized_volume": request.volume,
            "broker_status": order.status.value,
          },
        )
        if order.status in [
          OrderStatus.REJECTED,
          OrderStatus.CANCELLED,
          OrderStatus.EXPIRED,
        ]:
          runtime.state_manager.release_order_resources(order.order_id)

      self._record_decision_trace(
        runtime,
        intent=intent,
        market_context=context_snapshot.market_context,
        risk_caps=context_snapshot.risk_caps,
        position_profile=context_snapshot.position_profile,
        order_draft=draft,
        order_request=request,
        risk_decision=decision,
        broker_report={
          "order_id": order.order_id,
          "status": order.status.value,
          "filled_volume": order.filled_volume,
          "error_message": order.error_message,
        },
        tags=["broker_report", order.status.value],
        reason=decision.reason_code,
      )

      if order.status == OrderStatus.REJECTED:
        if runtime.metrics:
          runtime.metrics.rejected_orders += 1
        await self._notify_strategy_order(runtime, OrderStateEvent.from_raw(order))
        self._runtime_log(
          runtime,
          "WARNING",
          f"Broker拒单: {intent.instrument_code} {order_type.value}, "
          f"原因: {order.error_message}",
        )
        return

      metrics.orders_placed += 1

      self._runtime_log(
        runtime,
        "INFO",
        f"下单: {intent.instrument_code} {order_type.value} "
        f"{request.volume}股 @ {request.price:.2f}",
      )

    except Exception as e:
      runtime.t_trade_entry_reservations.pop(intent.intent_id, None)
      if metrics:
        metrics.error_count += 1
      await self._notify_strategy_order(
        runtime,
        OrderStateEvent(
          order_id=None,
          status=OrderStatus.REJECTED.value,
          request={
            "instrument_code": intent.instrument_code,
            "metadata": {
              **dict(intent.metadata or {}),
              "intent_id": intent.intent_id,
            },
          },
          error_message=str(e),
          metadata={
            **dict(intent.metadata or {}),
            "intent_id": intent.intent_id,
          },
        ),
      )
      self._runtime_log(runtime, "ERROR", f"处理交易意图失败: {e}")

  async def _reserve_order_resources(
    self,
    runtime: StrategyRuntime,
    reservation_key: str,
    request: OrderRequest,
  ) -> bool:
    if not runtime.state_manager or not runtime.state_manager.enable_reserve:
      if runtime.state_manager and hasattr(
        runtime.state_manager, "reserve_bucket_order"
      ):
        return runtime.state_manager.reserve_bucket_order(reservation_key, request)
      return True

    if request.order_type in [BrokerOrderType.BUY, BrokerOrderType.BUY_TO_COVER]:
      est_cost = self._estimate_order_cost(runtime, request)
      cash_reserved = bool(
        est_cost and runtime.state_manager.reserve_cash(reservation_key, est_cost)
      )
      if not cash_reserved:
        return False
      if hasattr(runtime.state_manager, "reserve_bucket_order"):
        if not runtime.state_manager.reserve_bucket_order(reservation_key, request):
          runtime.state_manager.release_order_resources(reservation_key)
          return False
      return True
    if request.order_type == BrokerOrderType.SELL:
      uses_substitution = bool((request.metadata or {}).get("substitution_plan"))
      if not uses_substitution:
        if not runtime.state_manager.reserve_position(
          reservation_key, request.instrument_code, request.volume
        ):
          return False
      if hasattr(runtime.state_manager, "reserve_bucket_order"):
        if not runtime.state_manager.reserve_bucket_order(reservation_key, request):
          runtime.state_manager.release_order_resources(reservation_key)
          return False
      return True
    return False

  def _record_decision_trace(
    self,
    runtime: StrategyRuntime,
    *,
    intent: TradeIntent,
    market_context: Dict[str, Any],
    risk_caps: Dict[str, Any],
    position_profile: Dict[str, Any],
    order_draft: Any = None,
    order_request: Optional[OrderRequest] = None,
    risk_decision: Optional[OrderRiskDecision] = None,
    broker_report: Optional[Dict[str, Any]] = None,
    tags: Optional[List[str]] = None,
    reason: str = "",
  ) -> None:
    if not runtime.state_manager:
      return
    draft_summary = {}
    if order_draft is not None:
      draft_summary = {
        "draft_id": getattr(order_draft, "draft_id", None),
        "intent_id": getattr(order_draft, "intent_id", None),
        "side": getattr(
          getattr(order_draft, "side", None),
          "value",
          getattr(order_draft, "side", None),
        ),
        "instrument_code": getattr(order_draft, "instrument_code", None),
        "bucket": getattr(order_draft, "bucket", None),
        "limit_price": getattr(order_draft, "limit_price", None),
        "raw_target_amount": getattr(order_draft, "raw_target_amount", None),
        "raw_target_volume": getattr(order_draft, "raw_target_volume", None),
        "sized_amount": getattr(order_draft, "sized_amount", None),
        "sized_volume": getattr(order_draft, "sized_volume", None),
        "size_reason_codes": list(getattr(order_draft, "size_reason_codes", []) or []),
        "metadata": dict(getattr(order_draft, "metadata", {}) or {}),
      }
    request_summary = {}
    if order_request is not None:
      request_summary = {
        "instrument_code": order_request.instrument_code,
        "order_type": order_request.order_type.value,
        "price_type": order_request.price_type.value,
        "volume": order_request.volume,
        "price": order_request.price,
        "metadata": dict(order_request.metadata or {}),
      }
    decision_summary = {}
    if risk_decision is not None:
      decision_summary = {
        "risk_decision_id": risk_decision.risk_decision_id,
        "action": risk_decision.action.value,
        "allowed": risk_decision.allowed,
        "original_volume": risk_decision.original_volume,
        "final_volume": risk_decision.final_volume,
        "reason_code": risk_decision.reason_code,
        "reason_detail": risk_decision.reason_detail,
        "risk_tags": list(risk_decision.risk_tags or []),
        "metadata": dict(risk_decision.metadata or {}),
        "substitution_plan": risk_decision.substitution_plan,
      }
    trace = DecisionTrace.from_decision(
      run_id=runtime.run_id,
      strategy_id=str(runtime.strategy_id),
      instrument_code=intent.instrument_code,
      environment=market_context,
      risk_caps=risk_caps,
      position_profile=position_profile,
      trade_intents=[summarize_intent(intent)],
      order_draft=draft_summary,
      order_request=request_summary,
      risk_decision=decision_summary,
      broker_report=broker_report or {},
      trace_id=intent.trace_id,
      tags=tags,
      reason=reason,
    )
    runtime.state_manager.record_decision_trace(trace)

  def _estimate_order_price(
    self, runtime: StrategyRuntime, request: OrderRequest
  ) -> Optional[float]:
    if request.price and request.price > 0:
      return request.price

    broker = runtime.broker
    if broker and hasattr(broker, "current_prices"):
      price = broker.current_prices.get(request.instrument_code)
      if price:
        return float(price)

    return None

  def _estimate_order_cost(
    self, runtime: StrategyRuntime, request: OrderRequest
  ) -> Optional[float]:
    price = self._estimate_order_price(runtime, request)
    if price is None or price <= 0:
      return None

    amount = price * request.volume
    commission = 0.0
    if runtime.broker:
      commission = runtime.broker.calculate_commission(
        amount,
        rate=getattr(runtime.broker, "commission_rate", 0.0003),
      )
    return amount + commission

  def _resolve_realtime_instruments(self, runtime: StrategyRuntime) -> List[str]:
    """Resolve realtime subscriptions from context first, then legacy parameters."""
    instruments = [
      str(item or "").strip()
      for item in list(getattr(runtime.context, "instruments", []) or [])
      if str(item or "").strip()
    ]
    if instruments:
      return instruments

    params = dict(getattr(runtime.context, "parameters", {}) or {})
    raw = (
      params.get("instruments")
      or params.get("stockCodes")
      or params.get("stock_codes")
      or params.get("instrument_code")
      or params.get("instrumentCode")
    )
    if isinstance(raw, list):
      candidates = raw
    else:
      candidates = str(raw or "").split(",")
    return [str(item or "").strip() for item in candidates if str(item or "").strip()]

  def _realtime_data_requirements(
    self, runtime: StrategyRuntime
  ) -> tuple[bool, List[str]]:
    strategy_class = getattr(runtime, "strategy_class", None)
    requirements = (
      strategy_class.get_data_requirements()
      if strategy_class and hasattr(strategy_class, "get_data_requirements")
      else {
        "use_tick_data": False,
        "periods": [runtime.context.parameters.get("period", "1m")],
      }
    )
    use_tick_data = bool(requirements.get("use_tick_data", False))
    periods = [
      str(period).lower()
      for period in list(requirements.get("periods") or [])
      if period and str(period).lower() != "tick"
    ]
    if not use_tick_data and not periods:
      periods = [str(runtime.context.parameters.get("period", "1m"))]
    return use_tick_data, periods

  async def _subscribe_realtime_instrument(
    self, runtime: StrategyRuntime, instrument: str
  ) -> List[str]:
    data_adapter = runtime.data_adapter
    if data_adapter is None:
      return []
    use_tick_data, periods = self._realtime_data_requirements(runtime)
    subscription_ids: List[str] = []
    try:
      if use_tick_data:
        subscription_ids.append(
          await data_adapter.subscribe_tick(
            instrument_code=instrument,
            callback=lambda tick: runtime.event_queue.put_nowait(("tick", tick)),
          )
        )
      for period in periods:
        subscription_ids.append(
          await data_adapter.subscribe_kline(
            instrument_code=instrument,
            period=period,
            callback=lambda kline: runtime.event_queue.put_nowait(("kline", kline)),
          )
        )
    except Exception:
      for subscription_id in subscription_ids:
        await data_adapter.unsubscribe(subscription_id)
      raise
    self.logger.info(
      "订阅实时数据: %s, tick=%s, periods=%s",
      instrument,
      use_tick_data,
      periods,
    )
    return subscription_ids

  async def _apply_realtime_instrument_reconcile(
    self,
    runtime: StrategyRuntime,
    instruments: List[str],
    *,
    instrument_metadata: Optional[Dict[str, Dict[str, Any]]] = None,
  ) -> Dict[str, List[str]]:
    """在运行事件队列内安全调整标的池和订阅。"""

    desired = []
    for raw in instruments or []:
      code = str(raw or "").strip().upper()
      if code and code not in desired:
        desired.append(code)
    previous = list(runtime.context.instruments or [])
    membership_added = [code for code in desired if code not in previous]
    membership_removed = [code for code in previous if code not in desired]

    async with runtime.realtime_subscription_lock:
      subscribed = set(runtime.realtime_subscription_ids)
      to_subscribe = [code for code in desired if code not in subscribed]
      to_unsubscribe = [code for code in subscribed if code not in desired]
      created: List[str] = []
      runtime.context.instruments = desired
      try:
        for code in to_subscribe:
          runtime.realtime_subscription_ids[
            code
          ] = await self._subscribe_realtime_instrument(runtime, code)
          created.append(code)
      except Exception:
        runtime.context.instruments = previous
        for code in created:
          for subscription_id in runtime.realtime_subscription_ids.pop(code, []):
            await runtime.data_adapter.unsubscribe(subscription_id)
        raise

      for code in to_unsubscribe:
        for subscription_id in runtime.realtime_subscription_ids.pop(code, []):
          await runtime.data_adapter.unsubscribe(subscription_id)
        runtime.latest_market_data.pop(code, None)
        self.logger.info("取消已移出标的池的实时订阅: %s", code)

    if runtime.strategy:
      self._sync_dynamic_holding_inventory(runtime, instrument_metadata)
      state = runtime.strategy.state.to_dict()
      account = (
        runtime.state_manager.get_account_quota() if runtime.state_manager else {}
      )
      positions = (
        runtime.state_manager.get_all_positions() if runtime.state_manager else {}
      )
      reconcile_input = StrategyInput(
        run_id=runtime.run_id,
        strategy_id=str(runtime.strategy_id),
        timestamp=runtime.context.current_time or time_utils.now(),
        cadence=StrategyCadence.RECONCILE,
        instrument_code="",
        event={
          "added": membership_added,
          "removed": membership_removed,
          "instruments": desired,
          "instrument_metadata": dict(instrument_metadata or {}),
        },
        portfolio_state={"account": account, "positions": positions},
        strategy_state=state,
        parameters=dict(runtime.context.parameters or {}),
      )
      output = await runtime.strategy.step(reconcile_input)
      await self._process_strategy_output(runtime, output, reconcile_input)

    return {
      "added": membership_added,
      "removed": membership_removed,
      "instruments": desired,
    }

  async def _clear_realtime_subscriptions(self, runtime: StrategyRuntime) -> None:
    data_adapter = runtime.data_adapter
    if data_adapter is None:
      return
    async with runtime.realtime_subscription_lock:
      subscription_groups = list(runtime.realtime_subscription_ids.values())
      runtime.realtime_subscription_ids.clear()
      for subscription_ids in subscription_groups:
        for subscription_id in subscription_ids:
          await data_adapter.unsubscribe(subscription_id)

  async def _run_realtime_loop(self, runtime: StrategyRuntime) -> None:
    """运行实时交易循环"""
    metrics = runtime.metrics
    broker = runtime.broker

    instruments = self._resolve_realtime_instruments(runtime)
    await self._apply_realtime_instrument_reconcile(runtime, instruments)

    # 运行直到停止
    while (
      runtime.status in [ExecutionStatus.RUNNING, ExecutionStatus.PAUSED]
      and not self._shutdown_event.is_set()
    ):
      if runtime.status == ExecutionStatus.PAUSED:
        await asyncio.sleep(1)
        continue

      # 更新心跳
      metrics.last_heartbeat = time_utils.now()
      # 每10次心跳同步一次指标到数据库
      heartbeat_count = getattr(self, f"heartbeat_count_{runtime.run_id}", 0)
      heartbeat_count += 1
      setattr(self, f"heartbeat_count_{runtime.run_id}", heartbeat_count)

      # 检查持仓
      positions_result = await broker.get_position()
      positions = positions_result if isinstance(positions_result, dict) else {}
      runtime.context.positions = positions

      # 检查账户
      account = await broker.get_account()
      runtime.context.account_info = {
        "cash": account.cash,
        "total_value": account.total_asset,
        "buying_power": account.cash,
        "frozen_cash": account.frozen_cash,
        "market_value": account.market_value,
        "total_pnl": account.total_pnl,
        "daily_pnl": account.daily_pnl,
      }
      state_manager = getattr(runtime, "state_manager", None)
      if state_manager:
        state_manager.update_account(
          cash=account.cash,
          frozen_cash=account.frozen_cash,
          total_asset=account.total_asset,
        )
        for instrument_code, position in positions.items():
          state_manager.update_position(
            instrument_code,
            long_volume=position.long_volume,
            available_volume=position.available_volume,
            frozen_volume=position.frozen_volume,
            today_buy_volume=position.today_buy_volume,
            long_avg_price=position.long_avg_price,
            last_price=position.last_price,
            market_value=position.market_value,
            pnl=position.pnl,
          )

      await asyncio.sleep(1)

    await self._clear_realtime_subscriptions(runtime)

  def get_statistics(self) -> Dict[str, Any]:
    """获取执行器统计信息"""
    status_counts = {}
    for status in ExecutionStatus:
      status_counts[status.value] = sum(
        1 for runtime in self.runs.values() if runtime.status == status
      )

    return {
      "total_runs": len(self.runs),
      "max_workers": self.max_workers,
      "status_distribution": status_counts,
      "running_runs": len(self.get_running()),
    }
