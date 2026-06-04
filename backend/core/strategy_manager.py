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
from datetime import date, datetime, timedelta, time
from typing import Any, Dict, List, Optional, Set, Type

from database.connection import get_async_db, redis_client
from models import ExecutionMetrics
from models.enums import StrategyRunStatus
from prefector.flows.daily_market_data_sync_flow import daily_market_data_sync_flow
from repositories import StrategyRunRepository
from services.historical_market_data_service import HistoricalMarketDataService
from services.trading_time_service import TradingDateHelper

from .config import COMMON_PARAMETER_SCHEMAS, ParameterManager
from .strategies.base import StrategyBase, StrategyContext, StrategyRunMode
from .strategy_executor import ExecutionStatus, StrategyExecutor, StrategyRuntime
from .strategy_reconciler import StrategyReconciler
from .strategy_registry import strategy_registry
from core.utils import time_utils


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

  async def start(self):
    """启动策略管理器服务"""
    if self.running:
      return

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

    # 停止所有运行中的策略并关闭执行器（释放线程池，避免进程无法退出）
    await self.executor.shutdown()

    self.logger.info("策略管理器服务已停止")

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
              from models.strategy import Strategy
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

            # 创建策略上下文
            context = StrategyContext(
              run_id=run.id,
              mode=run.mode,
              instruments=run.instruments or [],
              parameters=parameters or {},
              initial_capital=run.initial_capital or 1000000.0,
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
            if run.status == StrategyRunStatus.RUNNING:
              # 只有 RUNNING 状态的实例需要自动启动
              await self.start_strategy(run.id)
              self.logger.info(f"策略运行 {run.id} 恢复并启动成功")
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
    run_id = str(uuid.uuid4())

    # TODO: 添加参数验证逻辑
    # schema = strategy_class.get_parameter_schema()
    # validate_parameters(parameters, schema)

    backtest_id = None
    if mode == StrategyRunMode.BACKTEST:
      backtest_id = str(uuid.uuid4())

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
      from repositories.backtest_repository import BacktestRepository
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
  ) -> str:
    """Create a new StrategyBacktest version for an existing backtest run.

    重跑语义：
    - 重用网格计划参数（网格等级、基准价、模板版本）；
    - 不重用历史网格库存（inventory_lots），即默认从零建仓与重建初始仓位。
    """
    from core.grid_book import (
      grid_book_levels_to_parameters,
      grid_book_to_template_snapshot,
      normalize_grid_book,
    )
    from core.backtest_result_storage import BacktestResultStorage
    from repositories.backtest_repository import BacktestRepository
    from repositories.strategy_grid_book_snapshot_repository import (
      StrategyGridBookSnapshotRepository,
    )

    run_info = None
    backtest_id = str(uuid.uuid4())

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
      await backtest_repo.update_backtest_start(backtest.id, time_utils.now())

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

    success = await self.start_strategy(run_id)
    if not success:
      async for db in get_async_db():
        backtest_repo = BacktestRepository(db)
        await backtest_repo.update_backtest_status(
          backtest_id=run_info["backtest_id"],
          status="ERROR",
          error_message="启动新回测版本失败",
          end_time=time_utils.now(),
        )
        break
      raise RuntimeError("启动新回测版本失败")

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
        self.logger.error(f"回测数据准备失败: {e}")
        await self._update_runtime_status(run_id, "ERROR", str(e))
        return False

    success = await self.executor.start(run_id)

    if success:
      # 注册任务完成回调
      runtime = self.executor.get(run_id)
      if runtime and runtime.task:
        # 注意: add_done_callback 是同步调用的，且不能直接 await
        # 我们在这里调度一个异步任务来处理 DB 更新
        runtime.task.add_done_callback(
          lambda t: asyncio.create_task(self._on_run_task_done(run_id, t))
        )

      # 更新数据库状态
      await self._update_runtime_status(run_id, "RUNNING")
      self.logger.info(f"启动策略运行: {run_id}")
    else:
      if runtime and runtime.error_message:
        await self._update_runtime_status(run_id, "ERROR", runtime.error_message)

    return success

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

    missing_before = await self._find_missing_backtest_data(
      service=service,
      instruments=runtime.instruments,
      start_time=start_time,
      end_time=end_time,
      required_kline_periods=required_kline_periods,
      require_tick=require_tick,
    )

    self.logger.info(
      f"回测历史数据检查完成: {runtime.run_id}, "
      f"缺失标的数量: {len(missing_before)}"
    )

    if not missing_before:
      return

    missing_desc = self._format_missing_data(missing_before)
    self.logger.info(
      f"回测前历史数据缺失: {missing_desc}，即将调用 daily-market-data-sync 补全数据"
    )

    sync_periods = self._extract_supported_sync_periods(missing_before)
    unsupported_periods = self._collect_unsupported_periods(missing_before)

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

      self.logger.info(
        f"补齐回测历史数据: {runtime.run_id}, 标的={instrument}, "
        f"日期={start_day}~{end_day}, periods={sorted(periods)}"
      )
      result = await daily_market_data_sync_flow(
        stock_list=[instrument],
        start_time=start_day,
        end_time=end_day,
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
    keys.append(
      f"daily_market_data_sync_complete:{instrument}:{start_day}-{end_day}:{period_key}"
    )

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
  ) -> Dict[str, Dict[str, Any]]:
    """逐日统计每个标的缺失的历史数据（按策略数据需求检查）"""
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

      for trading_date in trading_dates:
        day_missing = False
        day_start = datetime.combine(trading_date, time(00, 00, 00))
        day_end = datetime.combine(trading_date, time(23, 59, 59))

        if require_tick and not await asyncio.to_thread(
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

    return missing

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
      parts.append(f"{instrument}: {', '.join(segments)}")
    return "; ".join(parts)

  async def restart_strategy(self, run_id: str) -> bool:
    """
    重启策略运行（仅限回测模式）

    1. 验证状态（必须是终止状态）
    2. 清理资源（日志、Metrics）
    3. 重置状态（PENDING）
    4. 重新启动

    Args:
        run_id: 运行实例ID

    Returns:
        bool: 是否重启成功
    """
    try:
      # 1. 获取并验证当前运行状态
      async for db in get_async_db():
        repo = StrategyRunRepository(db)
        run = await repo.find_run_by_id(run_id)
        if not run:
          raise ValueError(f"未找到策略运行实例: {run_id}")

        if run.mode != StrategyRunMode.BACKTEST:
          raise ValueError("仅支持重启回测模式的策略")

        # 允许重启的状态
        allowed_statuses = {
          StrategyRunStatus.COMPLETED,
          StrategyRunStatus.STOPPED,
          StrategyRunStatus.ERROR,
          StrategyRunStatus.FAILED
        }
        if run.status not in allowed_statuses:
          raise ValueError(f"当前状态 {run.status} 不允许重启，仅支持: {allowed_statuses}")
        
        # 2. 清理资源
        # 清理日志文件
        log_file = f"logs/strategy/{run_id}.jsonl"
        if os.path.exists(log_file):
          try:
            os.remove(log_file)
            self.logger.info(f"已清理日志文件: {log_file}")
          except Exception as e:
            self.logger.warning(f"清理日志文件失败: {e}")

        # 3. 重置数据库状态
        # 重置 Metrics 为 None (SQLAlchemy 需要处理 JSON null)
        # 注意: 这里我们只重置必要字段
        run.status = StrategyRunStatus.PENDING
        run.metrics = None
        run.error_message = None
        run.start_time = None
        run.stop_time = None
        
        await db.commit()
        await db.refresh(run)
        
        # 4. 重新加载到 Executor (如果之前已从 Executor 移除)
        # 先确保 Executor 中没有旧实例
        if self.executor.get(run_id):
          await self.executor.delete(run_id)
          
        # 重新创建运行时
        # 载入运行参数
        parameters = run.parameters
        if isinstance(parameters, str):
          parameters = json.loads(parameters)

        try:
          strategy_class = strategy_registry.get_strategy_class(
            run.strategy.class_name, run.strategy.file_path
          )
        except Exception as e:
          raise ValueError(f"无法加载策略类: {e}")

        context = StrategyContext(
          run_id=run.id,
          mode=run.mode,
          instruments=run.instruments or [],
          parameters=parameters or {},
          initial_capital=run.initial_capital or 1000000.0,
          backtest_start_time=run.context.get("backtest_start_time") if hasattr(run, "context") else None, # 注意: DB model 可能没有 context 字段
          backtest_end_time=run.context.get("backtest_end_time") if hasattr(run, "context") else None,
        )
        # 补充：从 parameters 尝试恢复回测时间（通常 parameters 会保存这些信息吗？或者需要从 run 字段恢复）
        # StrategyRun 模型没有直接存储 backtest_start_time，通常在 parameters 中或者 context 中
        # 查看 StrategyManager.run_strategy，backtest_start_time 是传入 context 的
        # 但是 StrategyRun table 似乎没有 context 列? 
        # 重新检查 run_strategy: context 是用于创建 runtime 的，DB 中只存了 parameters。
        # 这是一个潜在问题：如果 backtest 时间没有存在 parameters 里，重启时会丢失时间范围。
        # 让我们假设它在 parameters 中，或者我们需要读取之前的 context (如果 run 有的话)
        # 检查 StrategyRun model definition... 
        
        # 暂时使用 parameters 中的配置, 如果没有则无法恢复准确时间
        # 修正: context 构造需要检查 parameters
        
        pass

      # 由于上面是在 async for db 内部，需要跳出后继续执行还是可以在内部执行？
      # 这种模式下 db session 作用域在循环内。
      # 获取完 run 信息后，我们可以继续执行。
      pass

    except Exception as e:
      self.logger.error(f"重启策略失败: {e}")
      raise e
    
    # 由于 async for 的限制，上面的逻辑比较零散，重新组织一下
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
      if run.status not in allowed_statuses and run.status.value not in [s.value for s in allowed_statuses]:
         # 兼容枚举值比较
         pass
         # 严格来说应该比较值
      
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

  async def stop_strategy(self, run_id: str) -> bool:
    """
    停止策略运行（委托给 Executor）

    Args:
        run_id: 运行实例ID

    Returns:
        bool: 是否停止成功
    """
    # 委托给 Executor 停止
    success = await self.executor.stop(run_id)

    if success:
      # 获取最终指标并更新到数据库
      runtime = self.executor.get(run_id)
      if runtime and runtime.metrics:
        await self._update_runtime_metrics(run_id, runtime.metrics)

      # 更新数据库状态
      await self._update_runtime_status(run_id, "STOPPED")
      self.logger.info(f"停止策略运行: {run_id}")

    return success

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

  async def _on_run_task_done(self, run_id: str, task: asyncio.Task) -> None:
    """策略运行任务结束回调"""
    try:
      # 检查是否有异常
      exc = task.exception()
      if exc:
        self.logger.error(f"策略运行任务异常结束: {run_id}, {exc}")
        await self._update_runtime_status(run_id, "ERROR", str(exc))
        return
      
      # 检查是否被取消
      if task.cancelled():
        self.logger.warning(f"策略运行任务被取消: {run_id}")
        return

      # 正常结束
      # 检查 runtime 状态，确定是否是自然完成
      runtime = self.executor.get(run_id)
      status = "COMPLETED"
      if runtime and runtime.status == ExecutionStatus.COMPLETED:
        status = "COMPLETED"
      elif runtime and runtime.status == ExecutionStatus.STOPPED:
         status = "STOPPED"
      
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

