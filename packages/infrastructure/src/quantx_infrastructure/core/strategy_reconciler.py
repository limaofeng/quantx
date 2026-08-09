"""
策略协调器 - StrategyManager 的内部组件
负责协调代码中的策略与数据库中的策略,确保两者保持一致
"""

import logging
from typing import Dict, List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from quantx_infrastructure.core.strategy_registry import StrategyMetadata
from quantx_infrastructure.database.relational import get_async_db
from quantx_infrastructure.models.enums import StrategyStatus
from quantx_infrastructure.models.strategy import Strategy
from quantx_infrastructure.models.strategy_run import StrategyRun


class ReconciliationResult:
  """策略协调结果"""

  def __init__(
    self,
    new: int = 0,
    updated: int = 0,
    deleted: int = 0,
    unchanged: int = 0,
    paused_runs: int = 0,
    stopped_runs: int = 0,
  ):
    self.new = new
    self.updated = updated
    self.deleted = deleted
    self.unchanged = unchanged
    self.paused_runs = paused_runs
    self.stopped_runs = stopped_runs

  def to_dict(self) -> Dict[str, int]:
    """转为字典"""
    return {
      "new": self.new,
      "updated": self.updated,
      "deleted": self.deleted,
      "unchanged": self.unchanged,
      "paused_runs": self.paused_runs,
      "stopped_runs": self.stopped_runs,
    }


class StrategyReconciler:
  """
  策略协调器 - 仅供 StrategyManager 内部使用

  负责协调代码中发现的策略与数据库中存储的策略,确保两者保持一致性:
  - 注册新策略
  - 更新已变更的策略
  - 标记已删除的策略
  - 管理受影响的运行实例
  """

  def __init__(self):
    self._logger = logging.getLogger(__name__)

  async def reconcile(
    self, discovered_strategies: List[StrategyMetadata]
  ) -> ReconciliationResult:
    """
    执行策略协调

    Args:
        discovered_strategies: 从代码中发现的策略列表

    Returns:
        协调结果统计
    """
    self._logger.info("开始协调策略...")

    code_strategies = {s.class_name: s for s in discovered_strategies}

    # 获取数据库会话
    result = ReconciliationResult()
    async for session in get_async_db():
      try:
        db_strategies = await self._get_all_db_strategies(session)
        db_strategy_map = {s.class_name: s for s in db_strategies}

        self._logger.info(f"数据库中已有 {len(db_strategies)} 个策略")
        self._logger.info(f"代码中发现 {len(code_strategies)} 个策略")

        # 分析差异
        code_class_names = set(code_strategies.keys())
        db_class_names = set(db_strategy_map.keys())

        new_strategies = code_class_names - db_class_names
        deleted_strategies = db_class_names - code_class_names
        existing_strategies = code_class_names & db_class_names

        self._logger.info(f"新策略: {new_strategies}")
        self._logger.info(f"已删除策略: {deleted_strategies}")
        self._logger.info(f"已存在策略: {existing_strategies}")

        # 处理新策略
        self._logger.info(f"开始处理 {len(new_strategies)} 个新策略...")
        for class_name in new_strategies:
          metadata = code_strategies[class_name]
          self._logger.info(f"正在注册策略: {metadata.name}")
          try:
            await self._register_new_strategy(session, metadata)
            result.new += 1
            self._logger.info(f"注册新策略成功: {metadata.name} v{metadata.version}")
          except Exception as e:
            self._logger.error(f"注册策略失败 {metadata.name}: {e}", exc_info=True)

        # 处理已删除的策略
        for class_name in deleted_strategies:
          db_strategy = db_strategy_map[class_name]
          stopped = await self._mark_strategy_as_deleted(session, db_strategy)
          result.deleted += 1
          result.stopped_runs += stopped
          self._logger.warning(
            f"策略已删除: {db_strategy.name}, 停止了 {stopped} 个运行实例"
          )

        # 处理已存在的策略
        for class_name in existing_strategies:
          code_metadata = code_strategies[class_name]
          db_strategy = db_strategy_map[class_name]

          if self._is_strategy_changed(code_metadata, db_strategy):
            paused = await self._update_strategy(session, db_strategy, code_metadata)
            result.updated += 1
            result.paused_runs += paused
            self._logger.info(
              f"策略已更新: {code_metadata.name} "
              f"{db_strategy.version} -> {code_metadata.version}, "
              f"暂停了 {paused} 个运行实例"
            )
          else:
            result.unchanged += 1

        await session.commit()

        self._logger.info(
          f"策略协调完成: 新增={result.new}, "
          f"更新={result.updated}, 删除={result.deleted}, "
          f"未变更={result.unchanged}, "
          f"暂停实例={result.paused_runs}, 停止实例={result.stopped_runs}"
        )

      finally:
        break  # 确保只执行一次

    return result

  async def _get_all_db_strategies(self, session: AsyncSession) -> List[Strategy]:
    """获取数据库中的所有策略"""
    result = await session.execute(select(Strategy))
    return list(result.scalars().all())

  async def _register_new_strategy(
    self, session: AsyncSession, metadata: StrategyMetadata
  ) -> Strategy:
    """注册新策略"""
    strategy = Strategy(
      name=metadata.name,
      description=metadata.description,
      file_path=metadata.file_path,
      class_name=metadata.class_name,
      parameter_schema=metadata.parameter_schema,  # TypeDecorator 自动处理序列化
      version=metadata.version,
      code_hash=metadata.code_hash,
      category=metadata.category,
      risk_level=metadata.risk_level,
      instrument_scope=metadata.instrument_scope,
      instrument_universe_mode=metadata.instrument_universe_mode,
      tags=metadata.tags if metadata.tags else [],  # ARRAY 字段直接传列表
      status=StrategyStatus.ACTIVE,
    )

    session.add(strategy)
    await session.flush()
    return strategy

  async def _update_strategy(
    self, session: AsyncSession, db_strategy: Strategy, metadata: StrategyMetadata
  ) -> int:
    """
    更新策略

    Returns:
        暂停的运行实例数量
    """
    # 更新策略信息
    db_strategy.name = metadata.name
    db_strategy.description = metadata.description
    db_strategy.file_path = metadata.file_path
    db_strategy.parameter_schema = metadata.parameter_schema  # TypeDecorator 自动处理
    db_strategy.version = metadata.version
    db_strategy.code_hash = metadata.code_hash
    db_strategy.category = metadata.category
    db_strategy.risk_level = metadata.risk_level
    db_strategy.instrument_scope = metadata.instrument_scope
    db_strategy.instrument_universe_mode = metadata.instrument_universe_mode
    db_strategy.tags = metadata.tags if metadata.tags else []  # ARRAY 字段直接传列表
    db_strategy.status = StrategyStatus.UPGRADING  # 标记为待升级状态

    await session.flush()

    # 暂停所有运行中的实例
    paused_count = await self._pause_running_instances(session, db_strategy.id)

    return paused_count

  async def _mark_strategy_as_deleted(
    self, session: AsyncSession, db_strategy: Strategy
  ) -> int:
    """
    标记策略为已删除

    Returns:
        停止的运行实例数量
    """
    db_strategy.status = StrategyStatus.DEPRECATED

    await session.flush()

    # 停止所有运行实例
    stopped_count = await self._stop_all_instances(session, db_strategy.id)

    return stopped_count

  async def _pause_running_instances(
    self, session: AsyncSession, strategy_id: int
  ) -> int:
    """
    暂停策略的所有运行实例

    Returns:
        暂停的实例数量
    """
    result = await session.execute(
      select(StrategyRun).filter(
        StrategyRun.strategy_id == strategy_id, StrategyRun.status == "RUNNING"
      )
    )
    runs = result.scalars().all()

    count = 0
    for run in runs:
      run.status = "PAUSED"
      run.upgrade_required = True
      count += 1

    await session.flush()
    return count

  @staticmethod
  def _normalize_instrument_scope(value):
    if value is None:
      return None
    return value.value if hasattr(value, "value") else value

  async def _stop_all_instances(self, session: AsyncSession, strategy_id: int) -> int:
    """
    停止策略的所有运行实例

    Returns:
        停止的实例数量
    """
    result = await session.execute(
      select(StrategyRun).filter(
        StrategyRun.strategy_id == strategy_id,
        StrategyRun.status.in_(["RUNNING", "PAUSED", "PENDING"]),
      )
    )
    runs = result.scalars().all()

    count = 0
    for run in runs:
      run.status = "STOPPED"
      run.error_message = "策略已被删除,自动停止运行"
      count += 1

    await session.flush()
    return count

  def _is_strategy_changed(
    self, code_metadata: StrategyMetadata, db_strategy: Strategy
  ) -> bool:
    """
    检查策略是否有变更

    Args:
        code_metadata: 代码中的策略元数据
        db_strategy: 数据库中的策略

    Returns:
        是否有变更
    """
    # 版本号变化
    if code_metadata.version != db_strategy.version:
      return True

    # 代码哈希变化
    if code_metadata.code_hash != db_strategy.code_hash:
      return True

    # 参数 schema 变化 - 统一转为 dict 比较
    code_schema_dict = code_metadata.parameter_schema  # 已经是 dict
    db_schema_dict = (
      db_strategy.parameter_schema.model_dump() if db_strategy.parameter_schema else {}
    )

    if code_schema_dict != db_schema_dict:
      return True

    if (
      self._normalize_instrument_scope(code_metadata.instrument_scope)
      != self._normalize_instrument_scope(db_strategy.instrument_scope)
    ):
      return True

    if (
      code_metadata.instrument_universe_mode
      != db_strategy.instrument_universe_mode
    ):
      return True

    return False
