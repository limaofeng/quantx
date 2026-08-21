"""
策略管理器服务 - 统一的策略生命周期管理入口

职责：
1. 策略发现和协调（通过 StrategyRegistry & Reconciler）
2. 服务生命周期管理（start/stop）
3. API 层统一接口
4. 持久化和恢复功能
5. 委托执行逻辑给 StrategyExecutor

不负责：
- 具体的策略执行逻辑（StrategyExecutor）
- 并发控制（StrategyExecutor）
- 资源分配（StrategyExecutor）
"""

import asyncio
import json
import logging
import select
import uuid
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Set, Type

from quantx_domain.strategies.base import (
  StrategyBase,
  StrategyContext,
  StrategyRunMode,
)
from quantx_infrastructure.core.config import COMMON_PARAMETER_SCHEMAS, ParameterManager
from quantx_infrastructure.core.strategy_reconciler import StrategyReconciler
from quantx_infrastructure.core.strategy_registry import strategy_registry
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.database.connection import get_async_db, redis_client
from quantx_infrastructure.models import ExecutionMetrics
from quantx_infrastructure.models.enums import StrategyRunStatus
from quantx_infrastructure.models.parameter_schema import (
  validate_strategy_configuration,
)
from quantx_infrastructure.repositories import StrategyRunRepository
from quantx_infrastructure.services.historical_market_data_service import (
  HistoricalMarketDataService,
)
from quantx_infrastructure.services.limit_up_board_replay_projection_service import (
  limit_up_board_replay_projection_service,
)
from quantx_infrastructure.services.market_data_request_service import (
  build_sync_lock_key,
  queue_market_data_sync,
  request_market_data_sync,
)
from quantx_infrastructure.services.t_trade_replay_projection_service import (
  TTradeReplayUpdateKind,
  t_trade_replay_projection_service,
)
from quantx_infrastructure.services.trading_time_service import TradingDateHelper

from .strategy_executor import ExecutionStatus, StrategyExecutor, StrategyRuntime

_MARKET_DATA_SYNC_MAX_DATE_SPAN_DAYS = {
  "tick": 7,
  "1m": 31,
  "1d": 3_700,
}

_T_TRADE_REPLAY_MIN_CONTINUOUS_TICKS_PER_DAY = 120
_T_TRADE_REPLAY_SESSION_EDGE_TOLERANCE = timedelta(minutes=5)
_T_TRADE_REPLAY_MAX_CONTINUOUS_GAP = timedelta(minutes=15)
_T_TRADE_REPLAY_CONTINUOUS_SESSIONS = (
  (time(9, 30), time(11, 30)),
  (time(13, 0), time(15, 0)),
)


class StrategyManager:
  """
  策略管理器 - 统一的策略生命周期管理入口（单例）

  职责：
  - 服务启动和协调
  - API 层统一接口
  - 持久化管理
  - 委托执行给 StrategyExecutor

  架构：
  - 使用 StrategyExecutor 处理并发执行
  - 使用 StrategyRegistry 进行策略发现
  - 使用 StrategyReconciler 进行策略协调
  """

  _instance = None

  def __new__(cls):
    if cls._instance is None:
      cls._instance = super().__new__(cls)
    return cls._instance

  def __init__(self):
    if not hasattr(self, "_initialized"):
      self._initialized = True
      self.running = False
      self.logger = logging.getLogger("StrategyManager")
      self.parameter_manager = ParameterManager()

      # 核心组件：策略执行器
      self.executor = StrategyExecutor(max_workers=10)
      self._executor_retired = False
      self._shutdown_in_progress = False
      self._executor_shutdown_task: Optional[asyncio.Task[None]] = None
      self._deferred_start_tasks: Dict[str, asyncio.Task] = {}

      # 策略注册和协调组件
      self._registry = strategy_registry
      self._reconciler = StrategyReconciler()

      # 加载通用参数schema
      for schema_name, schema in COMMON_PARAMETER_SCHEMAS.items():
        self.parameter_manager.register_schema(schema_name, schema)

  @staticmethod
  def _coerce_positive_float(value: Any) -> Optional[float]:
    """Return a positive float or None when the value is absent/invalid."""
    if value is None:
      return None
    try:
      parsed = float(value)
    except (TypeError, ValueError):
      return None
    return parsed if parsed > 0 else None

  def _resolve_initial_capital(
    self,
    parameters: Dict[str, Any],
    fallback: Optional[float] = None,
  ) -> float:
    """Resolve starting cash from canonical and legacy parameter keys."""
    params = dict(parameters or {})
    for key in ("initial_capital", "initialCapital", "cash_total", "cashTotal"):
      parsed = self._coerce_positive_float(params.get(key))
      if parsed is not None:
        return parsed
    parsed_fallback = self._coerce_positive_float(fallback)
    return parsed_fallback if parsed_fallback is not None else 1000000.0

  async def _mark_backtest_started_safely(self, backtest_id: str) -> None:
    """Best-effort persistence for backtest lifecycle state."""
    try:
      from quantx_infrastructure.repositories.backtest_repository import (
        BacktestRepository,
      )

      async for db in get_async_db():
        backtest_repo = BacktestRepository(db)
        await backtest_repo.update_backtest_start(backtest_id, time_utils.now())
        break
    except Exception as exc:
      self.logger.warning(f"更新回测开始状态失败: backtest_id={backtest_id}, error={exc}")

  async def _mark_backtest_error_safely(
    self,
    backtest_id: str,
    error_message: str,
  ) -> None:
    """Best-effort persistence for a failed backtest version."""
    try:
      from quantx_infrastructure.repositories.backtest_repository import (
        BacktestRepository,
      )

      async for db in get_async_db():
        backtest_repo = BacktestRepository(db)
        await backtest_repo.update_backtest_status(
          backtest_id=backtest_id,
          status="ERROR",
          error_message=error_message,
          end_time=time_utils.now(),
        )
        break
    except Exception as exc:
      self.logger.warning(f"更新回测失败状态失败: backtest_id={backtest_id}, error={exc}")

  async def start(self):
    """启动策略管理器服务"""
    if self.running:
      return

    if self._executor_retired:
      # StrategyExecutor.shutdown() is terminal: it sets a permanent shutdown
      # event and closes its ThreadPoolExecutor.  The Engine supervisor restarts
      # run_engine() inside the same Python process, so reusing that executor
      # would make every restored realtime loop exit immediately.
      await self._await_retired_executor_shutdown()
      previous_executor = self.executor
      self.executor = StrategyExecutor(
        max_workers=previous_executor.max_workers,
        exit_strategy_registry=previous_executor.exit_strategy_registry,
      )
      self._executor_retired = False
      self._executor_shutdown_task = None
      self.logger.info("监督重启已创建新的策略执行器")

    self._shutdown_in_progress = False
    self.running = True
    self.logger.info("策略管理器服务启动中...")
    # 1. 自动发现并同步策略
    await self._sync_strategies()

    # 2. 恢复未完成的策略运行
    await self._restore_runs()

    self.logger.info("策略管理器服务已启动")

  async def stop(self):
    """停止策略管理器服务"""
    self.running = False
    if not self._executor_retired:
      # Retire the generation and create its shutdown task before the first
      # await. main._stop_component uses wait_for(), whose timeout cancels this
      # stop coroutine; shield below keeps the actual teardown alive.
      self._shutdown_in_progress = True
      self._executor_retired = True
      retiring_executor = self.executor
      self._executor_shutdown_task = asyncio.create_task(
        self._shutdown_executor_generation(retiring_executor),
        name=f"strategy-executor-shutdown:{id(retiring_executor)}",
      )

    await self._await_retired_executor_shutdown()

  async def _shutdown_executor_generation(
    self,
    executor: StrategyExecutor,
  ) -> None:
    """Finish one executor teardown even if the outer stop waiter is cancelled."""

    await self._cancel_deferred_starts_for_shutdown()
    await executor.shutdown()
    self._verify_executor_shutdown(executor)
    self.logger.info("策略管理器服务已停止")

  async def _await_retired_executor_shutdown(self) -> None:
    """Join the single shutdown task and fail closed when teardown is unsafe."""

    task = self._executor_shutdown_task
    if task is None:
      raise RuntimeError("策略执行器已退役但缺少停机任务，拒绝启动")
    try:
      await asyncio.shield(task)
    except asyncio.CancelledError as exc:
      if task.cancelled():
        raise RuntimeError("策略执行器停机任务被取消，拒绝启动") from exc
      raise
    except Exception as exc:
      raise RuntimeError("旧策略执行器未安全关闭，拒绝启动") from exc

  @staticmethod
  def _verify_executor_shutdown(executor: StrategyExecutor) -> None:
    """Prove a retired generation cannot keep strategy work alive."""

    if not executor._shutdown_event.is_set():
      raise RuntimeError("旧策略执行器未进入停机状态")
    if not bool(getattr(executor.thread_pool, "_shutdown", False)):
      raise RuntimeError("旧策略执行器线程池未关闭")

    active_tasks: List[str] = []
    for runtime in executor.get_all():
      for task_name in ("task", "event_task"):
        runtime_task = getattr(runtime, task_name, None)
        if runtime_task is not None and not runtime_task.done():
          active_tasks.append(f"{runtime.run_id}:{task_name}")
    if active_tasks:
      raise RuntimeError(
        "旧策略执行器仍有未结束任务: " + ", ".join(active_tasks)
      )

  async def _sync_strategies(self):
    """协调策略到数据库"""
    try:
      # 发现所有策略
      discovered_strategies = self._registry.discover_strategies()

      # 执行协调
      result = await self._reconciler.reconcile(discovered_strategies)

      self.logger.info(
        f"策略协调完成: 新增={result.new}, "
        f"更新={result.updated}, 删除={result.deleted}, "
        f"未变更={result.unchanged}"
      )
      if result.paused_runs > 0 or result.stopped_runs > 0:
        self.logger.warning(
          f"受影响的运行实例: 暂停={result.paused_runs}, 停止={result.stopped_runs}"
        )

    except Exception as e:
      self.logger.error(f"策略协调失败: {e}")
      # 协调失败不阻止服务启动
      self.logger.warning("策略协调失败,将使用数据库中的现有策略")

  async def _restore_runs(self):
    """从数据库恢复活跃的策略运行实例
    
    恢复以下状态的运行实例到 executor：
    - RUNNING: 恢复后自动启动
    - PAUSED: 恢复到 executor 但不启动，等待用户 resume
    - PENDING: 恢复到 executor 但不启动，等待用户 start
    """
    try:
      async for db in get_async_db():
        repo = StrategyRunRepository(db)
        # 查询所有活跃的策略运行（RUNNING、PAUSED、PENDING）
        active_runs = await repo.find_all_active_runs()

        for run in active_runs:
          try:
            status_name = run.status.value if hasattr(run.status, 'value') else run.status
            self.logger.info(f"恢复策略运行: {run.id} ({run.name}), 状态: {status_name}")

            # 获取关联的策略信息
            if not run.strategy:
              # 如果没有预加载，手动查询
              from quantx_infrastructure.models.strategy import Strategy
              stmt = select(Strategy).filter(Strategy.id == run.strategy_id)
              res = await db.execute(stmt)
              run.strategy = res.scalar_one_or_none()

            if not run.strategy:
              self.logger.error(f"恢复运行 {run.id} 失败: 找不到策略模板 {run.strategy_id}")
              continue

            # 载入运行参数
            parameters = run.parameters
            if isinstance(parameters, str):
              parameters = json.loads(parameters)

            # 获取策略类
            try:
              strategy_class = strategy_registry.get_strategy_class(
                run.strategy.class_name, run.strategy.file_path
              )
            except Exception as e:
              self.logger.error(f"恢复运行 {run.id} 失败: 无法加载策略类 {run.strategy.class_name}: {e}")
              await repo.update_strategy_run_status(run.id, "ERROR", f"策略代码加载失败: {e}")
              continue

            mode_value = str(getattr(run.mode, "value", run.mode)).lower()
            status_value = str(getattr(run.status, "value", run.status)).lower()
            backtest = None
            backtest_start_time = None
            backtest_end_time = None
            backtest_version = None
            if mode_value == StrategyRunMode.BACKTEST.value:
              from quantx_infrastructure.repositories.backtest_repository import (
                BacktestRepository,
              )

              backtest_repo = BacktestRepository(db)
              history = await backtest_repo.get_backtests_by_run(run.id)
              backtest = history[0] if history else None
              if backtest:
                backtest_start_time = (
                  self._read_backtest_time_from_parameters(
                    parameters,
                    ["backtestStartTime", "backtest_start_time", "startTime", "start_time"],
                  )
                  or backtest.backtest_start_time
                )
                backtest_end_time = (
                  self._read_backtest_time_from_parameters(
                    parameters,
                    ["backtestEndTime", "backtest_end_time", "endTime", "end_time"],
                  )
                  or backtest.backtest_end_time
                )
                backtest_version = int(backtest.version or 0) or None

            is_t_trade_replay = bool(
              mode_value == StrategyRunMode.BACKTEST.value
              and parameters.get("t_trade_replay")
            )
            is_limit_up_board_replay = bool(
              mode_value == StrategyRunMode.BACKTEST.value
              and parameters.get("limit_up_board_replay")
            )
            is_deferred_replay = is_t_trade_replay or is_limit_up_board_replay
            should_start = status_value == StrategyRunStatus.RUNNING.value
            if (
              mode_value == StrategyRunMode.BACKTEST.value
              and status_value == StrategyRunStatus.PENDING.value
              and backtest
              and str(backtest.status or "").upper() == "RUNNING"
            ):
              self.logger.warning(
                f"检测到中断的回测版本，恢复启动: "
                f"run_id={run.id}, backtest_id={backtest.id}, v{backtest_version}"
              )
              should_start = True
            if (
              is_t_trade_replay
              and status_value == StrategyRunStatus.PENDING.value
            ):
              projection = await t_trade_replay_projection_service.get(run.id)
              projection_status = str(
                (projection or {}).get("status") or ""
              ).upper()
              if projection_status in {"PENDING", "RUNNING", "PAUSED"}:
                self.logger.warning(
                  "检测到中断的做 T 回放启动，恢复后台准备: run_id=%s",
                  run.id,
                )
                should_start = True
            if (
              is_limit_up_board_replay
              and status_value == StrategyRunStatus.PENDING.value
            ):
              replay_job_id = str(
                parameters.get("limit_up_board_replay_job_id") or ""
              ).strip()
              projection = (
                await limit_up_board_replay_projection_service.get(replay_job_id)
                if replay_job_id
                else None
              )
              projection_status = str(
                (projection or {}).get("status") or ""
              ).upper()
              if projection_status in {"PENDING", "RUNNING", "PAUSED"}:
                self.logger.warning(
                  "检测到中断的打板回放启动，恢复后台准备: run_id=%s",
                  run.id,
                )
                should_start = True

            # 创建策略上下文
            context = StrategyContext(
              run_id=run.id,
              mode=run.mode,
              instruments=run.instruments or [],
              parameters=parameters or {},
              initial_capital=run.initial_capital or 1000000.0,
              backtest_start_time=backtest_start_time,
              backtest_end_time=backtest_end_time,
              backtest_id=backtest.id if backtest else None,
              backtest_version=backtest_version,
            )

            # 委托给 Executor 重建运行时并加入管理
            runtime = self.executor.create(
              run_id=run.id,
              name=run.name,
              strategy_id=run.strategy_id,
              strategy_class=strategy_class,
              context=context,
            )

            # 恢复指标数据
            if run.metrics:
              runtime.metrics = run.metrics

            # 根据状态决定是否自动启动
            if should_start:
              if is_deferred_replay:
                await self.defer_start_strategy(run.id)
                self.logger.info(f"历史回放 {run.id} 已恢复后台启动")
              else:
                # 普通运行维持原有同步恢复语义。
                started = await self.start_strategy(run.id)
                if started:
                  self.logger.info(f"策略运行 {run.id} 恢复并启动成功")
                else:
                  runtime = self.executor.get(run.id)
                  error_message = (
                    str(runtime.error_message or "")
                    if runtime is not None
                    else ""
                  )
                  self.logger.error(
                    "策略运行 %s 恢复启动失败%s",
                    run.id,
                    f": {error_message}" if error_message else "",
                  )
            else:
              # PAUSED 和 PENDING 状态只加载到 executor，等待用户操作
              self.logger.info(f"策略运行 {run.id} 已加载到 executor (状态: {status_name})")

          except Exception as e:
            self.logger.error(f"恢复策略运行实例 {run.id} 失败: {e}")
            # 这里不抛出异常，继续恢复下一个
    except Exception as e:
      self.logger.error(f"恢复策略运行整体流程失败: {e}")

  async def run_strategy(
    self,
    strategy_id: int,
    strategy_class: Type[StrategyBase],
    mode: StrategyRunMode,
    instruments: List[str],
    parameters: Dict[str, Any],
    name: Optional[str] = None,
    backtest_start_time: Optional[datetime] = None,
    backtest_end_time: Optional[datetime] = None,
    auto_start: bool = True,
    run_id: Optional[str] = None,
    backtest_id: Optional[str] = None,
  ) -> str:
    """
    运行策略（统一入口）

    创建策略运行实例并启动（默认）
    或仅创建实例等待手动启动（auto_start=False）

    Args:
        strategy_id: 策略模板ID
        strategy_class: 策略类
        mode: 运行模式 (StrategyRunMode枚举)
        instruments: 交易标的列表
        parameters: 策略参数
        backtest_start_time: 回测数据起始时间(仅回测模式)
        backtest_end_time: 回测数据结束时间(仅回测模式)
        auto_start: 是否自动启动（默认 True）

    Returns:
        run_id: 运行实例ID
    """
    run_id = run_id or str(uuid.uuid4())

    # API is not the only caller of StrategyManager. Validate again at the
    # Engine boundary so restored jobs and internal services cannot bypass the
    # strategy schema or cross-field checks.
    parameters = validate_strategy_configuration(strategy_class, parameters)

    if mode == StrategyRunMode.BACKTEST:
      if (backtest_start_time is None) != (backtest_end_time is None):
        raise ValueError("回测开始和结束时间必须同时提供")
      if (
        backtest_start_time is not None
        and backtest_end_time is not None
        and backtest_end_time < backtest_start_time
      ):
        raise ValueError("回测结束时间不能早于开始时间")
      backtest_id = backtest_id or str(uuid.uuid4())
    else:
      backtest_id = None

    initial_capital = self._resolve_initial_capital(parameters)

    # 创建策略上下文
    context = StrategyContext(
      run_id=run_id,
      mode=mode,
      instruments=instruments,
      parameters=parameters,
      initial_capital=initial_capital,
      backtest_start_time=backtest_start_time,
      backtest_end_time=backtest_end_time,
      backtest_id=backtest_id,
    )

    # 委托给 Executor 创建运行时
    runtime = self.executor.create(
      run_id=run_id,
      name=name or f"Strategy-{strategy_id}",
      strategy_id=strategy_id,
      strategy_class=strategy_class,
      context=context,
    )

    # 先持久化 strategy_run 到数据库（strategy_backtests 有外键依赖）
    await self._save_runtime_to_db(runtime, strategy_id, name)

    # 再创建回测记录（依赖 strategy_runs 外键）
    if mode == StrategyRunMode.BACKTEST:
      from quantx_infrastructure.repositories.backtest_repository import (
        BacktestRepository,
      )
      async for db in get_async_db():
        backtest_repo = BacktestRepository(db)
        backtest = await backtest_repo.create_backtest(
          backtest_id=backtest_id,
          strategy_run_id=run_id,
          parameters=parameters,
          instruments=instruments,
          backtest_start_time=backtest_start_time,
          backtest_end_time=backtest_end_time,
        )
        context.backtest_version = int(backtest.version or 0) or None
        break
      self.logger.info(f"创建回测记录: backtest_id={backtest_id}")

    self.logger.info(f"创建策略运行: {run_id}, 模式: {mode}")

    # 自动启动（后台异步，不阻塞 API 响应）
    if auto_start:
      async def _safe_start():
        try:
          await self.start_strategy(run_id)
        except Exception as e:
          self.logger.error(f"后台启动策略失败: {run_id}, 错误: {e}")
          try:
            await self._update_runtime_status(run_id, "ERROR", str(e))
          except Exception:
            pass
      asyncio.create_task(_safe_start())

    return run_id

  def _parse_backtest_datetime(self, value: Any) -> Optional[datetime]:
    """Parse a datetime-like value from persisted backtest metadata."""
    if isinstance(value, datetime):
      return value
    if not value:
      return None
    if isinstance(value, str):
      try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
      except ValueError:
        return None
    return None

  def _read_backtest_time_from_parameters(
    self, parameters: Dict[str, Any], keys: List[str]
  ) -> Optional[datetime]:
    for key in keys:
      parsed = self._parse_backtest_datetime(parameters.get(key))
      if parsed:
        return parsed
    return None

  def _backtest_result_path_candidates(self, raw_path: str) -> List[str]:
    import os

    if not raw_path:
      return []
    return [
      raw_path,
      os.path.join("data", raw_path),
      os.path.join("data", "backtests", os.path.basename(raw_path)),
    ]

  async def rerun_backtest_version(
    self,
    run_id: str,
    backtest_start_time: Optional[datetime] = None,
    backtest_end_time: Optional[datetime] = None,
    backtest_id: Optional[str] = None,
  ) -> str:
    """Create a new StrategyBacktest version for an existing backtest run.

    重跑语义：
    - 重用网格计划参数（网格等级、基准价、模板版本）；
    - 不重用历史网格库存（inventory_lots），即默认从零建仓与重建初始仓位。
    """
    from quantx_domain.grid_book import (
      grid_book_levels_to_parameters,
      grid_book_to_template_snapshot,
      normalize_grid_book,
    )
    from quantx_infrastructure.core.backtest_result_storage import BacktestResultStorage
    from quantx_infrastructure.repositories.backtest_repository import (
      BacktestRepository,
    )
    from quantx_infrastructure.repositories.strategy_grid_book_snapshot_repository import (
      StrategyGridBookSnapshotRepository,
    )

    run_info = None
    backtest_id = backtest_id or str(uuid.uuid4())

    async for db in get_async_db():
      run_repo = StrategyRunRepository(db)
      backtest_repo = BacktestRepository(db)
      run = await run_repo.find_run_by_id(run_id)
      if not run:
        raise ValueError(f"未找到策略运行实例: {run_id}")

      mode_value = str(getattr(run.mode, "value", run.mode)).lower()
      if mode_value != StrategyRunMode.BACKTEST.value:
        raise ValueError("只有回测实例可以创建新的回测版本")

      status_value = str(getattr(run.status, "value", run.status)).lower()
      if status_value in {
        StrategyRunStatus.RUNNING.value,
        StrategyRunStatus.PENDING.value,
      }:
        raise ValueError("当前实例仍在运行或等待中，不能创建新的回测版本")

      parameters = run.parameters or {}
      if isinstance(parameters, str):
        parameters = json.loads(parameters)
      parameters = dict(parameters or {})
      instruments = list(run.instruments or [])

      history = await backtest_repo.get_backtests_by_run(run_id)
      latest = history[0] if history else None
      snapshot_repo = StrategyGridBookSnapshotRepository(db)
      template_record = await snapshot_repo.get_template(run_id)
      if not template_record and latest:
        for path in self._backtest_result_path_candidates(latest.result_path):
          latest_snapshot = await BacktestResultStorage.load_latest_grid_book_snapshot(path)
          if not latest_snapshot:
            continue
          template_snapshot = grid_book_to_template_snapshot(
            latest_snapshot,
            run_id=run_id,
            instrument_code=(
              instruments[0] if instruments else parameters.get("instrument_code", "")
            ),
            parameters=parameters,
            needs_backtest=False,
          )
          template_record = await snapshot_repo.upsert_template(
            strategy_run_id=run_id,
            snapshot=template_snapshot,
            mode="BACKTEST",
            note="template_bootstrap_from_latest_backtest",
          )
          break
      if template_record:
        template_snapshot = normalize_grid_book(
          dict(template_record.snapshot or {}),
          run_id=run_id,
          instrument_code=(instruments[0] if instruments else parameters.get("instrument_code", "")),
          parameters=parameters,
          editable=True,
          needs_backtest=True,
        )
        parameters["grid_levels"] = grid_book_levels_to_parameters(
          template_snapshot.get("levels") or []
        )
        parameters["base_price"] = template_snapshot.get("base_price")
        parameters["_grid_book_template_version"] = str(
          template_snapshot.get("version", 1) or 1
        )

      persisted_start_time = (
        self._read_backtest_time_from_parameters(
          parameters,
          ["backtestStartTime", "backtest_start_time", "startTime", "start_time"],
        )
        or (latest.backtest_start_time if latest else None)
      )
      persisted_end_time = (
        self._read_backtest_time_from_parameters(
          parameters,
          ["backtestEndTime", "backtest_end_time", "endTime", "end_time"],
        )
        or (latest.backtest_end_time if latest else None)
      )
      start_time = backtest_start_time or persisted_start_time
      end_time = backtest_end_time or persisted_end_time
      if not start_time or not end_time:
        raise ValueError("无法确定回测时间范围，请先配置 backtestStartTime/backtestEndTime")
      if end_time < start_time:
        raise ValueError("回测结束时间不能早于开始时间")

      if instruments:
        parameters.setdefault("instrument_code", instruments[0])
        parameters.setdefault("stockCodes", instruments)
      parameters["backtestStartTime"] = start_time.isoformat()
      parameters["backtestEndTime"] = end_time.isoformat()

      backtest = await backtest_repo.create_backtest(
        backtest_id=backtest_id,
        strategy_run_id=run_id,
        parameters=parameters,
        instruments=instruments,
        backtest_start_time=start_time,
        backtest_end_time=end_time,
      )

      run.parameters = parameters
      run.instruments = instruments
      run.status = StrategyRunStatus.PENDING
      run.metrics = None
      run.error_message = None
      run.start_time = None
      run.stop_time = None
      await db.commit()

      run_info = {
        "id": run.id,
        "name": run.name,
        "strategy_id": run.strategy_id,
        "class_name": run.strategy.class_name,
        "file_path": run.strategy.file_path,
        "parameters": parameters,
        "instruments": instruments,
        "initial_capital": run.initial_capital or 1000000.0,
        "mode": StrategyRunMode.BACKTEST,
        "backtest_start_time": start_time,
        "backtest_end_time": end_time,
        "backtest_id": backtest.id,
        "backtest_version": int(backtest.version or 0) or None,
      }
      break

    if not run_info:
      raise ValueError(f"无法创建新的回测版本: {run_id}")

    if self.executor.get(run_id):
      await self.executor.delete(run_id)

    strategy_class = strategy_registry.get_strategy_class(
      run_info["class_name"], run_info["file_path"]
    )
    context = StrategyContext(
      run_id=run_info["id"],
      mode=run_info["mode"],
      instruments=run_info["instruments"],
      parameters=run_info["parameters"],
      initial_capital=run_info["initial_capital"],
      backtest_start_time=run_info["backtest_start_time"],
      backtest_end_time=run_info["backtest_end_time"],
      backtest_id=run_info["backtest_id"],
      backtest_version=run_info["backtest_version"],
    )
    self.executor.create(
      run_id=run_info["id"],
      name=run_info["name"],
      strategy_id=run_info["strategy_id"],
      strategy_class=strategy_class,
      context=context,
    )

    async def _safe_start_rerun() -> None:
      try:
        success = await self.start_strategy(run_id)
        if success:
          return
        await self._mark_backtest_error_safely(
          run_info["backtest_id"],
          "启动新回测版本失败",
        )
      except Exception as e:
        self.logger.error(f"后台启动新回测版本失败: {run_id}, 错误: {e}")
        await self._mark_backtest_error_safely(run_info["backtest_id"], str(e))

    asyncio.create_task(_safe_start_rerun())

    return run_info["backtest_id"]

  async def start_strategy(self, run_id: str) -> bool:
    """
    启动策略运行（委托给 Executor）

    Args:
        run_id: 运行实例ID

    Returns:
        bool: 是否启动成功
    """
    # 委托给 Executor 启动
    runtime = self.executor.get(run_id)
    if not runtime:
      self.logger.error(f"未找到策略运行实例: {run_id}")
      return False

    if runtime.context.mode == StrategyRunMode.BACKTEST:
      try:
        await self._ensure_backtest_data_available(runtime)
      except RuntimeError as e:
        runtime.status = ExecutionStatus.ERROR
        runtime.error_message = str(e)
        self.logger.error(f"回测数据准备失败: {e}")
        await self._update_runtime_status(run_id, "ERROR", str(e))
        if runtime.context.backtest_id:
          await self._mark_backtest_error_safely(runtime.context.backtest_id, str(e))
        return False

    success = await self.executor.start(run_id)

    if success:
      if runtime.context.mode == StrategyRunMode.BACKTEST and runtime.context.backtest_id:
        await self._mark_backtest_started_safely(runtime.context.backtest_id)

      # 注册任务完成回调
      runtime = self.executor.get(run_id)
      if runtime and runtime.task:
        # 注意: add_done_callback 是同步调用的，且不能直接 await
        # 我们在这里调度一个异步任务来处理 DB 更新
        callback_executor = self.executor
        runtime.task.add_done_callback(
          lambda t, executor=callback_executor: asyncio.create_task(
            self._on_run_task_done(run_id, t, executor=executor)
          )
        )

      # 更新数据库状态
      await self._update_runtime_status(run_id, "RUNNING")
      self.logger.info(f"启动策略运行: {run_id}")
    else:
      if runtime and runtime.error_message:
        await self._update_runtime_status(run_id, "ERROR", runtime.error_message)

    return success

  async def defer_start_strategy(self, run_id: str) -> bool:
    """Track slow backtest preparation without blocking the command consumer."""

    runtime = self.executor.get(run_id)
    if runtime is None:
      self.logger.error("无法后台启动不存在的策略运行: %s", run_id)
      return False
    if runtime.task is not None and not runtime.task.done():
      return True
    existing = self._deferred_start_tasks.get(run_id)
    if existing is not None and not existing.done():
      return True

    task = asyncio.create_task(
      self._run_deferred_start(run_id),
      name=f"strategy-deferred-start:{run_id}",
    )
    self._deferred_start_tasks[run_id] = task

    def _discard(completed: asyncio.Task) -> None:
      if self._deferred_start_tasks.get(run_id) is completed:
        self._deferred_start_tasks.pop(run_id, None)

    task.add_done_callback(_discard)
    return True

  async def cancel_deferred_start(self, run_id: str) -> bool:
    """Cancel and join one tracked preparation task before stopping its runtime."""

    task = self._deferred_start_tasks.get(run_id)
    if task is None:
      return False
    if not task.done():
      task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    if self._deferred_start_tasks.get(run_id) is task:
      self._deferred_start_tasks.pop(run_id, None)
    return True

  async def _run_deferred_start(self, run_id: str) -> None:
    try:
      success = await self.start_strategy(run_id)
    except asyncio.CancelledError:
      raise
    except Exception as exc:
      self.logger.exception("后台启动策略失败: %s", run_id)
      await self.converge_deferred_start_error(run_id, str(exc))
      return
    if success:
      return
    runtime = self.executor.get(run_id)
    message = (
      str(runtime.error_message or "") if runtime is not None else ""
    ) or "后台启动策略失败"
    await self.converge_deferred_start_error(run_id, message)

  async def converge_deferred_start_error(
    self,
    run_id: str,
    error_message: str,
  ) -> None:
    """Best-effort convergence of run, backtest, and replay projection to ERROR."""

    runtime = self.executor.get(run_id)
    backtest_id = runtime.context.backtest_id if runtime is not None else None
    parameters = dict(runtime.context.parameters or {}) if runtime is not None else {}
    account_id = str(parameters.get("account_id") or "").strip()
    is_t_trade_replay = bool(parameters.get("t_trade_replay"))
    board_replay_job_id = str(
      parameters.get("limit_up_board_replay_job_id") or ""
    ).strip()
    if runtime is None or not backtest_id or not account_id:
      try:
        from quantx_infrastructure.repositories.backtest_repository import (
          BacktestRepository,
        )

        async for db in get_async_db():
          run = await StrategyRunRepository(db).find_run_by_id(run_id)
          if run is not None:
            persisted_parameters = run.parameters or {}
            if isinstance(persisted_parameters, str):
              persisted_parameters = json.loads(persisted_parameters)
            account_id = account_id or str(
              dict(persisted_parameters or {}).get("account_id") or ""
            ).strip()
          if not backtest_id:
            history = await BacktestRepository(db).get_backtests_by_run(run_id)
            backtest_id = history[0].id if history else None
          break
      except Exception:
        self.logger.exception("读取后台启动持久化上下文失败: %s", run_id)
    try:
      await self._update_runtime_status(run_id, "ERROR", error_message)
    except Exception:
      self.logger.exception("收敛后台启动运行状态失败: %s", run_id)
    if backtest_id:
      await self._mark_backtest_error_safely(backtest_id, error_message)
    if board_replay_job_id:
      try:
        await limit_up_board_replay_projection_service.update_job_error(
          job_id=board_replay_job_id,
          error_message=error_message,
        )
      except Exception:
        self.logger.exception(
          "收敛打板回放后台启动失败状态失败: %s",
          board_replay_job_id,
        )
    if account_id and is_t_trade_replay:
      try:
        await t_trade_replay_projection_service.update(
          run_id=run_id,
          account_id=account_id,
          status="ERROR",
          processed_until=(
            runtime.context.current_time if runtime is not None else None
          ),
          kind=TTradeReplayUpdateKind.RESULT_READY,
        )
      except Exception:
        self.logger.exception("收敛后台启动回放投影失败: %s", run_id)

  async def _cancel_deferred_starts_for_shutdown(self) -> None:
    tasks = list(self._deferred_start_tasks.values())
    for task in tasks:
      if not task.done():
        task.cancel()
    if tasks:
      await asyncio.gather(*tasks, return_exceptions=True)
    self._deferred_start_tasks.clear()

  async def _ensure_backtest_data_available(self, runtime: StrategyRuntime) -> None:
    """确保回测模式所需的历史数据已经准备就绪"""
    start_time = runtime.context.backtest_start_time
    if not start_time:
      self.logger.warning("回测模式未提供 backtest_start_time，跳过历史数据校验")
      return

    end_time = runtime.context.backtest_end_time or start_time
    if end_time < start_time:
      end_time = start_time

    service = HistoricalMarketDataService()

    # 处理开始与结束时间，将其扩展到包含整个交易时段
    start_time = start_time.replace(hour=9, minute=30, second=0, microsecond=0)
    end_time = end_time.replace(hour=15, minute=30, second=0, microsecond=0)
    requirements = runtime.strategy_class.get_data_requirements()
    require_tick = bool(requirements.get("use_tick_data", False))
    required_kline_periods = {
      str(period).lower()
      for period in (requirements.get("periods") or [])
      if period and str(period).lower() != "tick"
    }
    if any(
      str(period).lower() == "tick"
      for period in requirements.get("periods") or []
    ):
      require_tick = True

    self.logger.info(
      f"检查回测历史数据: {runtime.run_id}, "
      f"{start_time.date()} ~ {end_time.date()}, "
      f"标的: {runtime.instruments}, "
      f"tick={require_tick}, periods={sorted(required_kline_periods)}"
    )

    is_t_trade_replay = bool(runtime.context.parameters.get("t_trade_replay"))
    missing_before = await self._find_missing_backtest_data(
      service=service,
      instruments=runtime.instruments,
      start_time=start_time,
      end_time=end_time,
      required_kline_periods=required_kline_periods,
      require_tick=require_tick,
      strict_tick_quality=is_t_trade_replay,
    )

    self.logger.info(
      f"回测历史数据检查完成: {runtime.run_id}, "
      f"缺失标的数量: {len(missing_before)}"
    )

    if not missing_before:
      return

    missing_desc = self._format_missing_data(missing_before)
    sync_periods = self._extract_supported_sync_periods(missing_before)
    unsupported_periods = self._collect_unsupported_periods(missing_before)
    if is_t_trade_replay:
      self.logger.warning(
        "做 T 回放本地历史数据不完整，将按 InfluxDB 已落库数据执行隔离降级: %s",
        missing_desc,
      )
      supplement = {
        "mode": "ASYNC_OPTIONAL",
        "status": "UNSUPPORTED_PERIODS" if not sync_periods else "NOT_REQUESTED",
        "queued_request_ids": [],
        "completed_request_ids": [],
        "failed_requests": [],
      }
      if sync_periods and runtime.context.parameters.get(
        "replay_queue_missing_data_supplement", True
      ):
        try:
          supplement = await self._queue_missing_backtest_data_supplement(
            runtime=runtime,
            missing=missing_before,
            sync_periods=sync_periods,
          )
        except Exception as exc:
          supplement = {
            **supplement,
            "status": "QUEUE_FAILED",
            "reason": str(exc),
          }
          self.logger.warning(
            "做 T 回放可选历史数据补数排队失败；本次仍按本地数据降级: %s",
            exc,
            exc_info=True,
          )
      elif sync_periods:
        supplement["status"] = "DISABLED"

      # The current run never waits for the optional Agent transfer. Re-read
      # once to pick up data that may have completed concurrently, then make a
      # deterministic decision from persisted InfluxDB only.
      missing_after = await self._find_missing_backtest_data(
        service=service,
        instruments=runtime.instruments,
        start_time=start_time,
        end_time=end_time,
        required_kline_periods=required_kline_periods,
        require_tick=require_tick,
        strict_tick_quality=True,
      )
      await self._apply_t_trade_replay_data_availability(
        runtime=runtime,
        missing=missing_after,
        missing_before=missing_before,
        supplement=supplement,
        unsupported_periods=unsupported_periods,
      )
      if missing_after and not runtime.context.instruments:
        error_code = (
          "DATA_PARTIAL"
          if self._contains_partial_market_data(missing_after)
          else "DATA_INSUFFICIENT"
        )
        raise RuntimeError(
          f"{error_code}: 回放区间内全部可回放持仓标的均缺少可证明完整的已落库历史数据"
        )
      return

    self.logger.info(
      f"回测前历史数据缺失: {missing_desc}，即将调用 daily-market-data-sync 补全数据"
    )

    if not sync_periods:
      raise RuntimeError(
        "存在回测所需但 daily-market-data-sync 不支持的周期: "
        + ", ".join(sorted(unsupported_periods))
      )

    await self._sync_missing_backtest_data(
      runtime=runtime,
      missing=missing_before,
      sync_periods=sync_periods,
    )

    missing_after = await self._find_missing_backtest_data(
      service=service,
      instruments=runtime.instruments,
      start_time=start_time,
      end_time=end_time,
      required_kline_periods=required_kline_periods,
      require_tick=require_tick,
    )

    if missing_after:
      remaining_desc = self._format_missing_data(missing_after)
      raise RuntimeError(f"历史数据同步后仍缺失: {remaining_desc}")

  async def _queue_missing_backtest_data_supplement(
    self,
    *,
    runtime: StrategyRuntime,
    missing: Dict[str, Dict[str, Any]],
    sync_periods: Set[str],
  ) -> Dict[str, Any]:
    """Queue optional Agent downloads without making this replay wait for them."""

    summary: Dict[str, Any] = {
      "mode": "ASYNC_OPTIONAL",
      "status": "NOT_NEEDED",
      "queued_request_ids": [],
      "completed_request_ids": [],
      "failed_requests": [],
    }
    for instrument, info in missing.items():
      periods = self._sync_periods_for_missing_info(info, sync_periods)
      dates = sorted(info.get("dates") or [])
      if not periods or not dates:
        continue
      for chunk_start, chunk_end in self._market_data_sync_date_windows(
        dates,
        periods,
      ):
        chunk_start_day = chunk_start.strftime("%Y%m%d")
        chunk_end_day = chunk_end.strftime("%Y%m%d")
        result = await queue_market_data_sync(
          stock_list=[instrument],
          start_time=chunk_start_day,
          end_time=chunk_end_day,
          periods=sorted(periods),
        )
        status = str(result.get("status") or "failed").lower()
        if status == "skipped":
          summary["status"] = "AGENT_UNAVAILABLE"
          summary["reason"] = str(result.get("reason") or "")
          self.logger.info(
            "做 T 回放未排队可选补数：当前没有在线 market-data Agent"
          )
          return summary
        request_id = str(result.get("request_id") or "")
        if status == "success":
          if request_id:
            summary["completed_request_ids"].append(request_id)
          continue
        if status == "queued":
          if request_id:
            summary["queued_request_ids"].append(request_id)
          continue
        summary["failed_requests"].append(
          {
            "request_id": request_id,
            "instrument": instrument,
            "reason": str(result.get("reason") or "unknown"),
          }
        )

    if summary["failed_requests"]:
      summary["status"] = "PARTIAL_QUEUE_FAILURE"
    elif summary["queued_request_ids"]:
      summary["status"] = "QUEUED_FOR_FUTURE_REPLAY"
    elif summary["completed_request_ids"]:
      summary["status"] = "ALREADY_COMPLETED"
    return summary

  async def _apply_t_trade_replay_data_availability(
    self,
    *,
    runtime: StrategyRuntime,
    missing: Dict[str, Dict[str, Any]],
    missing_before: Dict[str, Dict[str, Any]],
    supplement: Dict[str, Any],
    unsupported_periods: Set[str],
  ) -> None:
    """Persist the local-data decision and remove only unavailable symbols."""

    missing_codes = set(missing)
    available = [
      code for code in runtime.context.instruments if code not in missing_codes
    ]
    metadata = dict(
      runtime.context.parameters.get("initial_instrument_metadata") or {}
    )
    skipped_by_code = {
      str(item.get("stock_code") or ""): dict(item)
      for item in list(
        runtime.context.parameters.get("replay_skipped_instruments") or []
      )
      if str(item.get("stock_code") or "")
    }
    supplement_status = str(supplement.get("status") or "NOT_REQUESTED")
    for code in sorted(missing_codes):
      item = dict(metadata.get(code) or {})
      local_detail = self._format_missing_data({code: missing[code]})
      if supplement_status == "QUEUED_FOR_FUTURE_REPLAY":
        suffix = "；在线 Agent 补数已异步排队，仅供后续回放使用"
      elif supplement_status == "AGENT_UNAVAILABLE":
        suffix = "；当前无在线行情 Agent，本次不等待外部链路"
      elif supplement_status == "DISABLED":
        suffix = "；可选 Agent 补数已禁用"
      elif supplement_status == "UNSUPPORTED_PERIODS":
        suffix = "；所需周期不支持 Agent 补数"
      elif supplement_status in {"QUEUE_FAILED", "PARTIAL_QUEUE_FAILURE"}:
        suffix = "；可选 Agent 补数排队失败，本次不等待外部链路"
      else:
        suffix = "；本次仅采用已落库 InfluxDB 数据"
      skipped_by_code[code] = {
        "stock_code": code,
        "instrument_name": str(item.get("instrument_name", "") or ""),
        "data_status": (
          "DATA_PARTIAL"
          if self._contains_partial_market_data({code: missing[code]})
          else "DATA_INSUFFICIENT"
        ),
        "reason": f"本地历史数据不完整（{local_detail}）{suffix}",
      }

    runtime.context.instruments = available
    runtime.context.parameters["initial_instrument_metadata"] = {
      code: value for code, value in metadata.items() if code in available
    }
    runtime.context.parameters["replay_skipped_instruments"] = [
      skipped_by_code[code] for code in sorted(skipped_by_code)
    ]
    replay_data_preparation = {
      "schema_version": 1,
      "policy": "INFLUXDB_LOCAL_FIRST_AGENT_SUPPLEMENT_NON_BLOCKING",
      "local_authority": "INFLUXDB",
      "missing_before": sorted(missing_before),
      "missing_after": sorted(missing),
      "available_instruments": list(available),
      "unsupported_periods": sorted(unsupported_periods),
      "supplement": supplement,
    }
    quality_issues_before = {
      code: list(info.get("quality_issues") or [])
      for code, info in missing_before.items()
      if info.get("quality_issues")
    }
    quality_issues_after = {
      code: list(info.get("quality_issues") or [])
      for code, info in missing.items()
      if info.get("quality_issues")
    }
    if quality_issues_before or quality_issues_after:
      replay_data_preparation.update(
        {
          "schema_version": 2,
          "quality_policy": "STRICT_DAILY_SESSION_COVERAGE",
          "quality_issues_before": quality_issues_before,
          "quality_issues_after": quality_issues_after,
        }
      )
    runtime.context.parameters["replay_data_preparation"] = replay_data_preparation
    async for db in get_async_db():
      await StrategyRunRepository(db).update_run(
        runtime.run_id,
        {
          "instruments": available,
          "parameters": runtime.context.parameters,
        },
      )
      break
    if missing_codes:
      self.logger.warning(
        "做 T 回放已按本地数据跳过历史数据不足标的: %s (supplement=%s)",
        ", ".join(sorted(missing_codes)),
        supplement_status,
      )

  async def _sync_missing_backtest_data(
    self,
    *,
    runtime: StrategyRuntime,
    missing: Dict[str, Dict[str, Any]],
    sync_periods: Set[str],
  ) -> None:
    """按本地缺口逐标的补齐回测历史数据，并清理可能过期的同步缓存。"""
    for instrument, info in missing.items():
      periods = self._sync_periods_for_missing_info(info, sync_periods)
      dates = sorted(info.get("dates") or [])
      if not periods or not dates:
        continue

      start_day = dates[0].strftime("%Y%m%d")
      end_day = dates[-1].strftime("%Y%m%d")
      self._clear_market_data_sync_cache(
        instrument=instrument,
        dates=dates,
        periods=periods,
        start_day=start_day,
        end_day=end_day,
      )

      for chunk_start, chunk_end in self._market_data_sync_date_windows(
        dates,
        periods,
      ):
        chunk_start_day = chunk_start.strftime("%Y%m%d")
        chunk_end_day = chunk_end.strftime("%Y%m%d")
        self.logger.info(
          f"补齐回测历史数据: {runtime.run_id}, 标的={instrument}, "
          f"日期={chunk_start_day}~{chunk_end_day}, periods={sorted(periods)}"
        )
        result = await request_market_data_sync(
          stock_list=[instrument],
          start_time=chunk_start_day,
          end_time=chunk_end_day,
          periods=sorted(periods),
        )

        status = result.get("status")
        if status == "skipped":
          self.logger.info(
            f"daily-market-data-sync 已跳过: {runtime.run_id}, "
            f"instrument={instrument}, reason={result.get('reason')}"
          )
          status = "success"
        if status not in {"success", "partial_success"}:
          raise RuntimeError(
            f"daily-market-data-sync 执行失败: instrument={instrument}, "
            f"status={status}, reason={result.get('reason')}"
          )

  @staticmethod
  def _market_data_sync_date_windows(
    dates: List[date],
    periods: Set[str],
  ) -> List[tuple[date, date]]:
    """Split requests to stay within the QMT Agent's per-period date limits."""
    if not dates:
      return []
    max_span_days = min(
      _MARKET_DATA_SYNC_MAX_DATE_SPAN_DAYS[period] for period in periods
    )
    windows: List[tuple[date, date]] = []
    window_start = dates[0]
    window_end = dates[0]
    for current_date in dates[1:]:
      if (current_date - window_start).days + 1 > max_span_days:
        windows.append((window_start, window_end))
        window_start = current_date
      window_end = current_date
    windows.append((window_start, window_end))
    return windows

  def _sync_periods_for_missing_info(
    self,
    info: Dict[str, Any],
    supported_periods: Set[str],
  ) -> Set[str]:
    periods = set(info.get("klines") or set())
    if info.get("tick"):
      periods.add("tick")
    return {period for period in periods if period in supported_periods}

  def _clear_market_data_sync_cache(
    self,
    *,
    instrument: str,
    dates: List[date],
    periods: Set[str],
    start_day: str,
    end_day: str,
  ) -> None:
    """清理缺失窗口的同步完成缓存，避免空数据被历史缓存误判为已补齐。"""
    keys: List[str] = []
    for trading_date in dates:
      day = trading_date.strftime("%Y%m%d")
      for period in periods:
        keys.append(f"daily_market_data_stock:{instrument}:{day}:{period}")

    period_key = "".join(sorted(periods))
    complete_key = (
      f"daily_market_data_sync_complete:{instrument}:{start_day}-{end_day}:{period_key}"
    )
    keys.append(complete_key)
    keys.append(build_sync_lock_key(complete_key))

    deleted = 0
    for key in keys:
      try:
        deleted += int(redis_client.delete(key) or 0)
      except Exception as exc:
        self.logger.warning(f"清理历史数据同步缓存失败: key={key}, error={exc}")
    if deleted:
      self.logger.info(
        f"已清理历史数据同步缓存: instrument={instrument}, deleted={deleted}"
      )

  async def _find_missing_backtest_data(
    self,
    service: HistoricalMarketDataService,
    instruments: List[str],
    start_time: datetime,
    end_time: datetime,
    required_kline_periods: Optional[Set[str]] = None,
    require_tick: bool = True,
    strict_tick_quality: bool = False,
  ) -> Dict[str, Dict[str, Any]]:
    """逐日统计每个标的缺失的历史数据（按策略数据需求检查）。

    普通回测继续使用既有存在性语义。做 T 历史回放显式启用
    ``strict_tick_quality``，只有交易时段覆盖、记录数和连续性均达到最小严格
    口径时才允许生成绩效。
    """
    missing: Dict[str, Dict[str, Any]] = {}
    if not instruments:
      return missing
    if required_kline_periods is None:
      required_kline_periods = {"1m", "1d"}
    required_kline_periods = {
      str(period).lower()
      for period in required_kline_periods
      if period and str(period).lower() != "tick"
    }

    start_date = start_time.date()
    end_date = end_time.date()
    trading_helper = TradingDateHelper()

    try:
      trading_dates = await trading_helper.get_trading_calendar(
        market="SH",
        start_date=start_date,
        end_date=end_date,
      )
    except Exception as exc:
      self.logger.warning(f"获取交易日历失败: SH, 错误: {exc}")
      trading_dates = []

    if not trading_dates:
      total_days = (end_date - start_date).days + 1
      trading_dates = [
        start_date + timedelta(days=i) for i in range(max(0, total_days))
      ]

    for instrument in instruments:
      if not trading_dates:
        continue

      missing_dates: Set[date] = set()
      missing_periods: Set[str] = set()
      tick_missing_any = False
      quality_issues: List[Dict[str, Any]] = []

      for trading_date in trading_dates:
        day_missing = False
        day_start = datetime.combine(trading_date, time(00, 00, 00))
        day_end = datetime.combine(trading_date, time(23, 59, 59))

        if require_tick:
          if strict_tick_quality:
            inspection = await asyncio.to_thread(
              self._inspect_t_trade_replay_tick_day,
              service,
              instrument,
              trading_date,
            )
            if not inspection["complete"]:
              tick_missing_any = True
              day_missing = True
              quality_issues.append(inspection)
          elif not await asyncio.to_thread(
            self._has_tick_data, service, instrument, day_start, day_end
          ):
            tick_missing_any = True
            day_missing = True

        for period in sorted(required_kline_periods):
          period_start = day_start
          period_end = day_end
          if period in {"1d", "1w", "1mon", "1q", "1hy", "1y"}:
            period_start = datetime.combine(trading_date, time(0, 0))
            period_end = datetime.combine(trading_date, time(23, 59, 59))
          if not await asyncio.to_thread(
            self._has_kline_data,
            service,
            instrument,
            period,
            period_start,
            period_end,
          ):
            missing_periods.add(period)
            day_missing = True

        if day_missing:
          missing_dates.add(trading_date)

      if missing_dates:
        missing[instrument] = {
          "klines": missing_periods,
          "tick": tick_missing_any,
          "dates": missing_dates,
        }
        if quality_issues:
          missing[instrument]["quality_issues"] = quality_issues

    return missing

  def _inspect_t_trade_replay_tick_day(
    self,
    service: HistoricalMarketDataService,
    instrument: str,
    trading_date: date,
  ) -> Dict[str, Any]:
    """Return an auditable, fail-closed quality decision for one Tick day."""

    query_start = datetime.combine(trading_date, time(9, 25))
    query_end = datetime.combine(trading_date, time(15, 5))
    base = {
      "data_type": "tick",
      "date": trading_date.isoformat(),
      "instrument_code": instrument,
    }
    try:
      records = service.tick_repo.find_all(
        filters={"stock_code": instrument},
        start_time=query_start,
        end_time=query_end,
        fields=["time"],
        limit=None,
        order_by="time ASC",
      )
    except Exception as exc:
      self.logger.warning(
        "做 T 回放逐日 Tick 质量查询失败: instrument=%s, date=%s, error=%s",
        instrument,
        trading_date,
        exc,
      )
      return {
        **base,
        "complete": False,
        "classification": "UNAVAILABLE",
        "reason_codes": ["TICK_QUERY_FAILED"],
        "message": "逐日 Tick 质量查询失败，无法证明数据完整",
        "query_error_type": type(exc).__name__,
        "statistics": {
          "record_count": 0,
          "continuous_session_record_count": 0,
        },
      }

    raw_times: List[Any]
    if hasattr(records, "empty"):
      if records.empty or "time" not in records.columns:
        raw_times = []
      else:
        raw_times = list(records["time"])
    else:
      raw_times = []
      for record in records or []:
        if isinstance(record, dict):
          raw_times.append(record.get("time"))
        else:
          raw_times.append(getattr(record, "time", None))

    timestamps: List[datetime] = []
    invalid_timestamp_count = 0
    for value in raw_times:
      if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
      if not isinstance(value, datetime):
        invalid_timestamp_count += 1
        continue
      timestamps.append(time_utils.to_shanghai(value))
    timestamps.sort()

    session_times: List[List[datetime]] = []
    for session_start, session_end in _T_TRADE_REPLAY_CONTINUOUS_SESSIONS:
      lower = datetime.combine(trading_date, session_start)
      upper = datetime.combine(trading_date, session_end)
      session_times.append([value for value in timestamps if lower <= value <= upper])

    continuous_times = [value for values in session_times for value in values]
    morning_times, afternoon_times = session_times
    max_gap_seconds = 0.0
    max_gap_start: Optional[datetime] = None
    max_gap_end: Optional[datetime] = None
    for values in session_times:
      for previous, current in zip(values, values[1:]):
        gap_seconds = (current - previous).total_seconds()
        if gap_seconds > max_gap_seconds:
          max_gap_seconds = gap_seconds
          max_gap_start = previous
          max_gap_end = current

    reason_codes: List[str] = []
    if not raw_times:
      reason_codes.append("NO_TICK_DATA")
    if invalid_timestamp_count:
      reason_codes.append("INVALID_TICK_TIMESTAMPS")
    if (
      len(continuous_times) < _T_TRADE_REPLAY_MIN_CONTINUOUS_TICKS_PER_DAY
    ):
      reason_codes.append("TICK_COUNT_TOO_LOW")

    tolerance = _T_TRADE_REPLAY_SESSION_EDGE_TOLERANCE
    morning_start = datetime.combine(trading_date, _T_TRADE_REPLAY_CONTINUOUS_SESSIONS[0][0])
    morning_end = datetime.combine(trading_date, _T_TRADE_REPLAY_CONTINUOUS_SESSIONS[0][1])
    afternoon_start = datetime.combine(
      trading_date, _T_TRADE_REPLAY_CONTINUOUS_SESSIONS[1][0]
    )
    afternoon_end = datetime.combine(
      trading_date, _T_TRADE_REPLAY_CONTINUOUS_SESSIONS[1][1]
    )
    if not morning_times or morning_times[0] > morning_start + tolerance:
      reason_codes.append("SESSION_OPEN_NOT_COVERED")
    if not morning_times or morning_times[-1] < morning_end - tolerance:
      reason_codes.append("MORNING_CLOSE_NOT_COVERED")
    if not afternoon_times or afternoon_times[0] > afternoon_start + tolerance:
      reason_codes.append("AFTERNOON_OPEN_NOT_COVERED")
    if not afternoon_times or afternoon_times[-1] < afternoon_end - tolerance:
      reason_codes.append("SESSION_CLOSE_NOT_COVERED")
    if max_gap_seconds > _T_TRADE_REPLAY_MAX_CONTINUOUS_GAP.total_seconds():
      reason_codes.append("CONTINUOUS_SESSION_GAP_TOO_LARGE")

    statistics = {
      "record_count": len(raw_times),
      "continuous_session_record_count": len(continuous_times),
      "invalid_timestamp_count": invalid_timestamp_count,
      "first_continuous_time": (
        continuous_times[0].isoformat() if continuous_times else None
      ),
      "last_continuous_time": (
        continuous_times[-1].isoformat() if continuous_times else None
      ),
      "morning_last_time": morning_times[-1].isoformat() if morning_times else None,
      "afternoon_first_time": (
        afternoon_times[0].isoformat() if afternoon_times else None
      ),
      "max_continuous_gap_seconds": max_gap_seconds,
      "max_continuous_gap_start": (
        max_gap_start.isoformat() if max_gap_start else None
      ),
      "max_continuous_gap_end": max_gap_end.isoformat() if max_gap_end else None,
      "minimum_record_count": _T_TRADE_REPLAY_MIN_CONTINUOUS_TICKS_PER_DAY,
      "maximum_gap_seconds": _T_TRADE_REPLAY_MAX_CONTINUOUS_GAP.total_seconds(),
      "session_edge_tolerance_seconds": tolerance.total_seconds(),
    }
    if not reason_codes:
      return {
        **base,
        "complete": True,
        "classification": "COMPLETE",
        "reason_codes": [],
        "message": "Tick 交易时段覆盖与连续性校验通过",
        "statistics": statistics,
      }

    classification = "MISSING" if "NO_TICK_DATA" in reason_codes else "PARTIAL"
    return {
      **base,
      "complete": False,
      "classification": classification,
      "reason_codes": reason_codes,
      "message": "Tick 交易时段覆盖、记录数或连续性未达到回放最低完整性要求",
      "statistics": statistics,
    }

  @staticmethod
  def _contains_partial_market_data(missing: Dict[str, Dict[str, Any]]) -> bool:
    return any(
      str(issue.get("classification") or "") != "MISSING"
      for info in missing.values()
      for issue in list(info.get("quality_issues") or [])
    )

  def _has_kline_data(
    self,
    service: HistoricalMarketDataService,
    instrument: str,
    period: str,
    start_time: datetime,
    end_time: datetime,
  ) -> bool:
    """检查指定周期的K线数据是否存在"""
    measurement = f"kline_{period.lower()}"
    period_lower = period.lower()
    window_days = 30 if period_lower in {"1d", "1w", "1mon", "1q", "1hy", "1y"} else 7

    if period_lower in {"1d", "1w", "1mon", "1q", "1hy", "1y"}:
      start_time = start_time.replace(hour=0, minute=0, second=0, microsecond=0)
      end_time = end_time.replace(hour=23, minute=59, second=59, microsecond=0)

    def _fetch(window_start: datetime, window_end: datetime):
      return service.kline_repo.find_all(
        measurement=measurement,
        filters={"stock_code": instrument},
        start_time=window_start,
        end_time=window_end,
        fields=["time"],
        limit=1,
        order_by="time DESC",
      )

    return self._has_data_in_windows(
      fetcher=_fetch,
      start_time=start_time,
      end_time=end_time,
      window_days=window_days,
      label=f"K线数据 {instrument} {period}",
    )

  def _has_tick_data(
    self,
    service: HistoricalMarketDataService,
    instrument: str,
    start_time: datetime,
    end_time: datetime,
  ) -> bool:
    """检查tick数据是否存在"""
    # 对齐到交易时段，避免无效时间段查询
    start_time = start_time.replace(hour=9, minute=30, second=0, microsecond=0)
    end_time = end_time.replace(hour=15, minute=30, second=0, microsecond=0)

    def _fetch(window_start: datetime, window_end: datetime):
      return service.tick_repo.find_all(
        filters={"stock_code": instrument},
        start_time=window_start,
        end_time=window_end,
        fields=["time"],
        limit=1,
        order_by="time DESC",
      )

    return self._has_data_in_windows(
      fetcher=_fetch,
      start_time=start_time,
      end_time=end_time,
      window_days=3,
      label=f"tick数据 {instrument}",
    )

  def _has_data_in_windows(
    self,
    fetcher,
    start_time: datetime,
    end_time: datetime,
    window_days: int,
    label: str,
  ) -> bool:
    """分段检查数据存在性，避免一次扫描过多文件"""
    if not start_time or not end_time:
      return False

    if end_time < start_time:
      end_time = start_time

    current_end = end_time
    window_days = max(1, int(window_days))

    while current_end >= start_time:
      window_start = max(start_time, current_end - timedelta(days=window_days))
      try:
        records = fetcher(window_start, current_end)
      except Exception as exc:
        self.logger.warning(
          f"查询历史{label}失败: {exc} (window={window_start}~{current_end})"
        )
        if window_days > 1:
          window_days = max(1, window_days // 2)
          continue
        return False

      if hasattr(records, "empty"):
        if not records.empty:
          return True
      elif records:
        return True

      current_end = window_start - timedelta(seconds=1)

    return False

  def _format_missing_data(self, missing: Dict[str, Dict[str, Any]]) -> str:
    """格式化缺失数据明细"""
    parts: List[str] = []
    for instrument, info in missing.items():
      segments: List[str] = []
      if info["klines"]:
        segments.append(f"K线[{', '.join(sorted(info['klines']))}]")
      if info["tick"]:
        segments.append("tick")
      dates = sorted(info.get("dates", []))
      if dates:
        if len(dates) == 1:
          preview = dates[0].strftime("%Y-%m-%d")
        elif len(dates) == 2:
          preview = f"{dates[0].strftime('%Y-%m-%d')}, {dates[1].strftime('%Y-%m-%d')}"
        else:
          preview = (
            f"{dates[0].strftime('%Y-%m-%d')} ... "
            f"{dates[-1].strftime('%Y-%m-%d')}"
          )
        segments.append(f"日期[{preview}]")
      issues = list(info.get("quality_issues") or [])
      if issues:
        issue_preview = []
        for issue in issues[:3]:
          reason_codes = "/".join(issue.get("reason_codes") or ["UNKNOWN"])
          issue_preview.append(f"{issue.get('date', '?')}:{reason_codes}")
        if len(issues) > 3:
          issue_preview.append(f"另 {len(issues) - 3} 日")
        segments.append("质量[" + ", ".join(issue_preview) + "]")
      parts.append(f"{instrument}: {', '.join(segments)}")
    return "; ".join(parts)

  async def restart_strategy(self, run_id: str) -> bool:
    """重启已终止的回测策略运行。"""
    return await self._restart_strategy_impl(run_id)

  async def _restart_strategy_impl(self, run_id: str) -> bool:
    """内部实现重启逻辑"""
    import os
    run_info = None
    
    # 1. DB 操作：获取并重置
    async for db in get_async_db():
      repo = StrategyRunRepository(db)
      run = await repo.find_run_by_id(run_id)
      if not run:
        raise ValueError(f"未找到策略运行实例: {run_id}")
      
      if run.mode != StrategyRunMode.BACKTEST:
        raise ValueError("仅支持重启回测模式的策略")
        
      allowed_statuses = {
        StrategyRunStatus.COMPLETED,
        StrategyRunStatus.STOPPED,
        StrategyRunStatus.ERROR,
        StrategyRunStatus.FAILED
      }
      status_value = (
        run.status.value if isinstance(run.status, StrategyRunStatus) else run.status
      )
      if status_value not in {status.value for status in allowed_statuses}:
        raise ValueError(f"当前状态 {run.status} 不允许重启，仅支持终止状态")
      
      # 提取重建 Context 需要的信息
      strategy_class_name = run.strategy.class_name
      strategy_file_path = run.strategy.file_path
      
      parameters = run.parameters
      if isinstance(parameters, str):
        parameters = json.loads(parameters)
        
      instruments = run.instruments or []
      initial_capital = run.initial_capital or 1000000.0
      
      # 清理日志
      log_file = f"logs/strategy/{run_id}.jsonl"
      if os.path.exists(log_file):
        try:
          os.remove(log_file)
        except Exception:
          pass

      # 重置状态
      run.status = StrategyRunStatus.PENDING
      run.metrics = None
      run.error_message = None
      run.start_time = None
      run.stop_time = None
      
      await db.commit()
      
      # 保存信息用于后续重建
      run_info = {
        "id": run.id,
        "name": run.name,
        "strategy_id": run.strategy_id,
        "class_name": strategy_class_name,
        "file_path": strategy_file_path,
        "parameters": parameters,
        "instruments": instruments,
        "initial_capital": initial_capital,
        "mode": run.mode
      }
      break
      
    if not run_info:
        return False
        
        # 2. 内存操作：重建 Runtime
    try:
        if self.executor.get(run_id):
            await self.executor.delete(run_id)

        strategy_class = strategy_registry.get_strategy_class(
            run_info["class_name"], run_info["file_path"]
        )
        
        # 尝试从 parameters 中恢复回测时间范围
        params = run_info["parameters"] or {}
        
        context = StrategyContext(
          run_id=run_info["id"],
          mode=run_info["mode"],
          instruments=run_info["instruments"],
          parameters=params,
          initial_capital=run_info["initial_capital"],
          # 尝试从参数恢复时间，如果不存在则为 None
          backtest_start_time=None, 
          backtest_end_time=None,
        )

        await self.executor.create(
            run_id=run_info["id"],
            name=run_info["name"],
            strategy_id=run_info["strategy_id"],
            strategy_class=strategy_class,
            context=context
        )

        # 3. 启动
        await self.start_strategy(run_id)
        return True

    except Exception as e:
        self.logger.error(f"重建策略运行时失败: {e}")
        # 此时 DB 已经是 PENDING，但内存没起来，用户可以再次点击 Start
        return False

  async def clone_strategy(
    self,
    source_run_id: str,
    target_mode: StrategyRunMode,
    parameter_overrides: Optional[Dict[str, Any]] = None
  ) -> str:
    """
    克隆策略运行实例（例如：将回测克隆为模拟盘）

    Args:
        source_run_id: 源运行实例ID
        target_mode: 目标运行模式
        parameter_overrides: 可选的参数覆盖

    Returns:
        new_run_id: 新创建的运行实例ID
    """
    # 1. 获取源运行实例信息
    async for db in get_async_db():
      repo = StrategyRunRepository(db)
      source_run = await repo.find_run_by_id(source_run_id)
      if not source_run:
        raise ValueError(f"未找到源策略运行实例: {source_run_id}")

      strategy_id = source_run.strategy_id
      parameters = dict(source_run.parameters) if source_run.parameters else {}
      if isinstance(parameters, str):
        parameters = json.loads(parameters)

      if parameter_overrides:
        parameters.update(parameter_overrides)

      instruments = list(source_run.instruments) if source_run.instruments else []

      if target_mode == StrategyRunMode.PAPER:
        initial_capital = self._resolve_initial_capital(
          parameters,
          fallback=source_run.initial_capital,
        )
        parameters["initial_capital"] = initial_capital
        parameters["_paper_account"] = {
          "model": "isolated_snapshot",
          "source_run_id": source_run_id,
          "created_at": time_utils.now().isoformat(),
        }

      try:
        strategy_class = strategy_registry.get_strategy_class(
          source_run.strategy.class_name, source_run.strategy.file_path
        )
      except Exception as e:
        raise ValueError(f"无法加载策略类: {e}")

      timestamp = time_utils.now().strftime("%Y%m%d%H%M")
      mode_suffix = (
        "Paper"
        if target_mode == StrategyRunMode.PAPER
        else "Live"
        if target_mode == StrategyRunMode.LIVE
        else "Backtest"
      )
      new_name = f"{source_run.name}-Clone-{mode_suffix}-{timestamp}"
      break

    return await self.run_strategy(
      strategy_id=strategy_id,
      strategy_class=strategy_class,
      mode=target_mode,
      instruments=instruments,
      parameters=parameters,
      name=new_name,
      auto_start=False,
    )

  def _extract_supported_sync_periods(
    self, missing: Dict[str, Dict[str, Any]]
  ) -> Set[str]:
    """提取可以通过 daily-market-data-sync 补齐的数据周期"""
    sync_periods: Set[str] = set()
    if any("1m" in info["klines"] for info in missing.values()):
      sync_periods.add("1m")
    if any("1d" in info["klines"] for info in missing.values()):
      sync_periods.add("1d")
    if any(info["tick"] for info in missing.values()):
      sync_periods.add("tick")
    return sync_periods

  def _collect_unsupported_periods(
    self, missing: Dict[str, Dict[str, Any]]
  ) -> Set[str]:
    """收集当前流程无法自动补齐的周期"""
    supported_kline_periods = {"1m", "1d"}
    unsupported: Set[str] = set()
    for info in missing.values():
      for period in info["klines"]:
        if period not in supported_kline_periods:
          unsupported.add(period)
    return unsupported

  async def stop_strategy(self, run_id: str, *, force: bool = False) -> bool:
    """
    停止策略运行（委托给 Executor）

    Args:
        run_id: 运行实例ID
        force: 仅允许做 T 历史回放跳过模拟退出计划保护

    Returns:
        bool: 是否停止成功
    """
    runtime = self.executor.get(run_id)
    if runtime is None:
      return await self._stop_persisted_strategy(run_id)

    if force and not (
      runtime.context.mode == StrategyRunMode.BACKTEST
      and runtime.context.parameters.get("t_trade_replay")
    ):
      self.logger.warning("拒绝强制停止非做 T 历史回放运行: %s", run_id)
      return False

    # 委托给 Executor 停止
    success = await self.executor.stop(run_id, force=force)

    if success:
      # 获取最终指标并更新到数据库
      if runtime and runtime.metrics:
        await self._update_runtime_metrics(run_id, runtime.metrics)

      # 更新数据库状态
      await self._update_runtime_status(run_id, "STOPPED")
      self.logger.info(f"停止策略运行: {run_id}")

    return success

  async def _stop_persisted_strategy(self, run_id: str) -> bool:
    """幂等停止未恢复到当前 Executor 的持久化运行。

    Engine 重启、策略代码加载失败或恢复中断后，数据库中的运行记录可能仍然
    存在，但当前进程没有对应的内存运行态。此时实际执行循环已经不存在，
    停止请求必须收敛持久化状态，避免记录在后续重启时再次被恢复。
    """
    terminal_statuses = {
      StrategyRunStatus.STOPPED,
      StrategyRunStatus.COMPLETED,
      StrategyRunStatus.ERROR,
    }
    async for db in get_async_db():
      repo = StrategyRunRepository(db)
      run = await repo.find_run_by_id(run_id)
      if run is None:
        self.logger.warning("停止策略运行失败，运行不存在: %s", run_id)
        return False

      status_value = getattr(run.status, "value", run.status)
      status_key = str(status_value or "").lower()
      terminal = next(
        (
          candidate
          for candidate in terminal_statuses
          if status_key in {candidate.value.lower(), candidate.name.lower()}
        ),
        None,
      )
      if terminal is not None:
        self.logger.info(
          "策略运行已处于持久化终态，无需重复停止: %s (%s)",
          run_id,
          terminal.value,
        )
        return True

      await repo.update_run(
        run_id,
        {
          "status": StrategyRunStatus.STOPPED,
          "stop_time": time_utils.now(),
        },
      )
      self.logger.warning(
        "策略运行未恢复到当前执行器，已收敛持久化状态为 STOPPED: %s",
        run_id,
      )
      return True

    return False

  async def pause_strategy(self, run_id: str) -> bool:
    """
    暂停策略运行（委托给 Executor）

    Args:
        run_id: 运行实例ID

    Returns:
        bool: 是否暂停成功
    """
    success = await self.executor.pause(run_id)

    if success:
      await self._update_runtime_status(run_id, "PAUSED")
      self.logger.info(f"暂停策略运行: {run_id}")

    return success

  async def resume_strategy(self, run_id: str) -> bool:
    """
    恢复策略运行（委托给 Executor）

    Args:
        run_id: 运行实例ID

    Returns:
        bool: 是否恢复成功
    """
    success = await self.executor.resume(run_id)

    if success:
      await self._update_runtime_status(run_id, "RUNNING")
      self.logger.info(f"恢复策略运行: {run_id}")

    return success

  def get_run(self, run_id: str) -> Optional[StrategyRuntime]:
    """
    获取策略运行信息（从 Executor 获取）

    Args:
        run_id: 运行实例ID

    Returns:
        Optional[StrategyRuntime]: 运行时对象，不存在时返回 None
    """
    return self.executor.get(run_id)

  async def reconcile_run_instruments(
    self,
    run_id: str,
    instruments: List[str],
    *,
    instrument_metadata: Optional[Dict[str, Dict[str, Any]]] = None,
  ) -> Dict[str, List[str]]:
    """调整动态标的池，并把最新快照持久化到 StrategyRun。"""

    runtime = self.executor.get(run_id)
    if runtime is None:
      raise ValueError(f"策略运行不存在: {run_id}")
    previous = list(runtime.context.instruments or [])
    result = await self.executor.reconcile_instruments(
      run_id,
      instruments,
      instrument_metadata=instrument_metadata,
    )
    try:
      async for db in get_async_db():
        await StrategyRunRepository(db).update_run(
          run_id, {"instruments": list(result["instruments"])}
        )
        break
    except Exception:
      await self.executor.reconcile_instruments(run_id, previous)
      raise
    return result

  async def update_run_parameters(
    self, run_id: str, parameters: Dict[str, Any]
  ) -> None:
    """原地更新运行参数，供动态持仓策略应用新的全局设置。"""

    normalized = dict(parameters or {})
    runtime = self.executor.get(run_id)
    previous = dict(runtime.context.parameters or {}) if runtime else None
    if runtime:
      runtime.context.parameters = normalized
    try:
      async for db in get_async_db():
        run = await StrategyRunRepository(db).update_run(
          run_id, {"parameters": normalized}
        )
        if run is None:
          raise ValueError(f"策略运行不存在: {run_id}")
        break
    except Exception:
      if runtime and previous is not None:
        runtime.context.parameters = previous
      raise

  def get_all_runs(self) -> List[StrategyRuntime]:
    """
    获取所有策略运行（从 Executor 获取）

    Returns:
        List[StrategyRuntime]: 所有运行时对象列表
    """
    return self.executor.get_all()

  def get_runs_by_status(self, status: ExecutionStatus) -> List[StrategyRuntime]:
    """
    根据状态获取策略运行（从 Executor 获取）

    Args:
        status: 执行状态

    Returns:
        List[StrategyRuntime]: 指定状态的运行时对象列表

    Note:
        参数使用 ExecutionStatus 而非 StrategyRunStatus
    """
    return [runtime for runtime in self.executor.get_all() if runtime.status == status]

  async def _on_run_task_done(
    self,
    run_id: str,
    task: asyncio.Task,
    *,
    executor: Optional[StrategyExecutor] = None,
  ) -> None:
    """策略运行任务结束回调"""
    try:
      if self._shutdown_in_progress or (
        executor is not None and executor is not self.executor
      ):
        # A process-level Engine stop only tears down in-memory resources.  The
        # persisted RUNNING/PAUSED/PENDING status is the recovery intent for the
        # next supervised attempt.  Late callbacks from a retired executor must
        # never overwrite that intent with STOPPED/COMPLETED.
        self.logger.info(
          "Engine 停机或旧执行器回调，保留策略运行持久化状态: %s",
          run_id,
        )
        return

      # task.exception() 会在取消任务上抛 CancelledError，必须先判断。
      if task.cancelled():
        self.logger.warning(f"策略运行任务被取消: {run_id}")
        return

      # 检查是否有异常
      exc = task.exception()
      if exc:
        self.logger.error(f"策略运行任务异常结束: {run_id}, {exc}")
        await self._update_runtime_status(run_id, "ERROR", str(exc))
        return
      
      # 正常结束
      # 检查 runtime 状态，确定是否是自然完成
      runtime = self.executor.get(run_id)
      status = "COMPLETED"
      if runtime and runtime.status == ExecutionStatus.COMPLETED:
        status = "COMPLETED"
      elif runtime and runtime.status == ExecutionStatus.ERROR:
        status = "ERROR"
        error_message = runtime.error_message or "策略运行异常结束"
        if runtime.context.backtest_id:
          await self._mark_backtest_error_safely(
            runtime.context.backtest_id,
            error_message,
          )
      elif runtime and runtime.status == ExecutionStatus.STOPPED:
         status = "STOPPED"
      
      if status == "ERROR":
        await self._update_runtime_status(run_id, status, error_message)
      else:
        await self._update_runtime_status(run_id, status)
      self.logger.info(f"策略运行任务正常结束: {run_id}, 最终状态: {status}")

    except Exception as e:
      self.logger.error(f"处理策略运行任务结束回调失败: {run_id}, {e}")

  async def _save_runtime_to_db(
    self, runtime: StrategyRuntime, strategy_id: int, name: Optional[str] = None
  ):
    """
    保存实例到数据库

    Args:
        runtime: 运行时对象
        strategy_id: 策略模板ID
    """
    async for db in get_async_db():
      repo = StrategyRunRepository(db)

      run_name = name or f"{strategy_id}-{runtime.run_id[:8]}"
      run_data = {
        "id": runtime.run_id,
        "name": run_name,
        "strategy_id": strategy_id,
        "parameters": json.dumps(runtime.context.parameters),
        "status": runtime.status.value,
        "start_time": time_utils.now(),
        "mode": runtime.context.mode.value,
        "instruments": runtime.context.instruments,
        "initial_capital": runtime.context.initial_capital,
        "user_id": "system",
      }

      await repo.create_strategy_run(run_data)

  async def _update_runtime_status(
    self, run_id: str, status: str, error_message: Optional[str] = None
  ):
    """
    更新实例状态到数据库

    Args:
        run_id: 运行实例ID
        status: 状态字符串
        error_message: 错误消息（可选）
    """
    status_value = getattr(status, "value", status)
    status_key = str(status_value).lower()
    status_to_store = status
    for candidate in StrategyRunStatus:
      if status_key in {candidate.value.lower(), candidate.name.lower()}:
        status_to_store = candidate
        break

    async for db in get_async_db():
      repo = StrategyRunRepository(db)

      update_data = {"status": status_to_store}
      if error_message:
        update_data["error_message"] = error_message
      if status_to_store == StrategyRunStatus.STOPPED:
        update_data["stop_time"] = time_utils.now()

      await repo.update_run(run_id, update_data)

    runtime = self.executor.get(run_id)
    parameters = dict(runtime.context.parameters or {}) if runtime else {}
    if parameters.get("t_trade_replay"):
      account_id = str(parameters.get("account_id") or "").strip()
      if account_id:
        normalized_status = str(
          getattr(status_to_store, "value", status_to_store) or status_key
        ).upper()
        terminal = normalized_status in {"COMPLETED", "ERROR", "STOPPED"}
        try:
          await t_trade_replay_projection_service.update(
            run_id=run_id,
            account_id=account_id,
            status=normalized_status,
            progress_pct=100.0 if normalized_status == "COMPLETED" else None,
            processed_until=runtime.context.current_time,
            kind=(
              TTradeReplayUpdateKind.RESULT_READY
              if terminal
              else TTradeReplayUpdateKind.STATUS_CHANGED
            ),
          )
        except Exception:
          self.logger.exception("更新做 T 回放生命周期投影失败: %s", run_id)

  async def _update_runtime_metrics(self, run_id: str, metrics: ExecutionMetrics):
    """
    更新运行时指标到数据库

    Args:
        run_id: 运行实例ID
        metrics: 执行指标对象
    """
    async for db in get_async_db():
      repo = StrategyRunRepository(db)
      await repo.update_run(run_id, {"metrics": metrics.model_dump(mode="json")})


# 创建全局单例
strategy_manager = StrategyManager()

