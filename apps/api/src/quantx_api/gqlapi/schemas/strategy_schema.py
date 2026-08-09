from datetime import datetime
from typing import List, Optional

import strawberry
from strawberry.scalars import JSON

from ..resolvers.strategies import StrategyResolver
from ..types import (
  BucketLedgerView,
  ExecutionTraceView,
  MessageResponse,
  OperationResult,
  Strategy,
  StrategyApprovalIntent,
  StrategyBacktest,
  StrategyDecision,
  StrategyDefinition,
  StrategyExitPlanView,
  StrategyGridBook,
  StrategyGridBookUpdateInput,
  StrategyInstance,
  StrategyInstanceCreateInput,
  StrategyInstanceParameterUpdateInput,
  StrategyLogPage,
  StrategyPerformance,
  StrategyRun,
  StrategyRunInput,
  StrategyRunMode,
  StrategyRunUpdateInput,
)


@strawberry.type(description="策略相关查询")
class StrategyQuery:
  @strawberry.field(description="获取策略模板列表")
  async def strategies(self) -> List[Strategy]:
    return await StrategyResolver.get_strategies()

  @strawberry.field(description="获取单个策略模板")
  async def strategy(self, strategy_id: int) -> Optional[Strategy]:
    return await StrategyResolver.get_strategy(strategy_id)

  @strawberry.field(description="获取策略运行列表")
  async def strategy_runs(self) -> List[StrategyRun]:
    return await StrategyResolver.get_strategy_runs()

  @strawberry.field(description="获取单个策略运行")
  async def strategy_run(self, run_id: str) -> Optional[StrategyRun]:
    return await StrategyResolver.get_strategy_run(run_id)

  @strawberry.field(description="获取策略运行的回测历史")
  async def backtest_history(self, run_id: str) -> List[StrategyBacktest]:
    return await StrategyResolver.get_backtest_history(run_id)

  @strawberry.field(description="获取策略库定义列表")
  async def strategy_definitions(self) -> List[StrategyDefinition]:
    return await StrategyResolver.get_strategy_definitions()

  @strawberry.field(description="获取策略实例列表")
  async def strategy_instances(
    self,
    status: Optional[str] = None,
    strategy_key: Optional[str] = None,
    instrument_code: Optional[str] = None,
  ) -> List[StrategyInstance]:
    return await StrategyResolver.get_strategy_instances(
      status=status,
      strategy_key=strategy_key,
      instrument_code=instrument_code,
    )

  @strawberry.field(description="获取单个策略实例")
  async def strategy_instance(self, id: str) -> Optional[StrategyInstance]:
    return await StrategyResolver.get_strategy_instance(id)

  @strawberry.field(description="获取策略运行中等待人工确认的交易意图")
  async def strategy_pending_trade_intents(
    self,
    run_id: str,
  ) -> List[StrategyApprovalIntent]:
    return await StrategyResolver.get_strategy_pending_trade_intents(run_id)

  @strawberry.field(description="获取策略运行的统一自动退出计划")
  async def strategy_exit_plans(
    self,
    run_id: str,
  ) -> List[StrategyExitPlanView]:
    return await StrategyResolver.get_strategy_exit_plans(run_id)

  @strawberry.field(description="获取策略实例决策审计历史")
  async def strategy_decision_history(
    self,
    instance_id: str,
    cursor: Optional[str] = None,
    limit: int = 50,
    backtest_id: Optional[str] = None,
  ) -> List[StrategyDecision]:
    return await StrategyResolver.get_strategy_decision_history(
      instance_id=instance_id,
      cursor=cursor,
      limit=limit,
      backtest_id=backtest_id,
    )

  @strawberry.field(description="获取策略实例执行跟踪")
  async def strategy_execution_trace(
    self,
    instance_id: str,
    decision_id: Optional[str] = None,
    backtest_id: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = 50,
  ) -> List[ExecutionTraceView]:
    return await StrategyResolver.get_strategy_execution_trace(
      instance_id=instance_id,
      decision_id=decision_id,
      backtest_id=backtest_id,
      cursor=cursor,
      limit=limit,
    )

  @strawberry.field(description="获取策略实例三仓归因")
  async def strategy_bucket_ledger(self, instance_id: str) -> BucketLedgerView:
    return await StrategyResolver.get_strategy_bucket_ledger(instance_id)

  @strawberry.field(description="获取 Pullback Grid 网格簿")
  async def strategy_grid_book(
    self,
    instance_id: str,
    backtest_id: Optional[str] = None,
    version: Optional[int] = None,
  ) -> StrategyGridBook:
    return await StrategyResolver.get_strategy_grid_book(
      instance_id,
      backtest_id=backtest_id,
      version=version,
    )

  @strawberry.field(description="获取策略绩效")
  async def strategy_performance(
    self,
    run_id: str,
    backtest_id: Optional[str] = None,
    benchmark_code: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = 2000,
  ) -> StrategyPerformance:
    return await StrategyResolver.get_strategy_performance(
      run_id=run_id,
      backtest_id=backtest_id,
      benchmark_code=benchmark_code,
      cursor=cursor,
      limit=limit,
    )

  @strawberry.field(description="分页读取策略执行日志文件")
  async def strategy_execution_logs(
    self,
    run_id: str,
    backtest_id: Optional[str] = None,
    version: Optional[int] = None,
    cursor: Optional[int] = None,
    limit: int = 200,
    before: bool = False,
    tail: bool = True,
  ) -> StrategyLogPage:
    return await StrategyResolver.get_strategy_execution_logs(
      run_id=run_id,
      backtest_id=backtest_id,
      version=version,
      cursor=cursor,
      limit=limit,
      before=before,
      tail=tail,
    )


@strawberry.type(description="策略相关变更")
class StrategyMutation:
  # === 策略运行管理（新 API）===
  @strawberry.field(description="运行策略（创建并启动）")
  def run_strategy(
    self, input: StrategyRunInput, auto_start: bool = True
  ) -> StrategyRun:
    """
    运行策略

    创建策略运行实例并启动（默认）
    设置 auto_start=false 可延迟启动
    """
    return StrategyResolver.run_strategy(input, auto_start)

  @strawberry.field(description="更新策略运行配置")
  def update_strategy_run(
    self, run_id: str, run_update: StrategyRunUpdateInput
  ) -> Optional[StrategyRun]:
    return StrategyResolver.update_strategy_run(run_id, run_update)

  @strawberry.field(description="删除策略运行")
  def delete_strategy_run(self, run_id: str) -> MessageResponse:
    return StrategyResolver.delete_strategy_run(run_id)

  # === 策略运行控制（新 API）===
  @strawberry.field(description="启动或重启策略")
  def start_strategy(self, run_id: str) -> OperationResult:
    return StrategyResolver.start_strategy(run_id)

  @strawberry.field(description="停止策略")
  def stop_strategy(self, run_id: str) -> OperationResult:
    return StrategyResolver.stop_strategy(run_id)

  @strawberry.field(description="暂停策略")
  def pause_strategy(self, run_id: str) -> OperationResult:
    return StrategyResolver.pause_strategy(run_id)

  @strawberry.field(description="恢复策略")
  def resume_strategy(self, run_id: str) -> OperationResult:
    return StrategyResolver.resume_strategy(run_id)

  @strawberry.field(description="重启策略(仅限回测)")
  def restart_strategy(self, run_id: str) -> OperationResult:
    return StrategyResolver.restart_strategy(run_id)

  @strawberry.field(description="在当前回测实例下新增一个回测版本并启动")
  async def rerun_backtest_version(
    self,
    run_id: str,
    backtest_start_time: Optional[datetime] = None,
    backtest_end_time: Optional[datetime] = None,
  ) -> StrategyBacktest:
    return await StrategyResolver.rerun_backtest_version(
      run_id=run_id,
      backtest_start_time=backtest_start_time,
      backtest_end_time=backtest_end_time,
    )

  @strawberry.field(description="删除指定回测版本")
  async def delete_backtest_version(
    self,
    run_id: str,
    backtest_id: str,
  ) -> OperationResult:
    return await StrategyResolver.delete_backtest_version(
      run_id=run_id,
      backtest_id=backtest_id,
    )

  @strawberry.field(description="克隆策略运行(转模拟盘/实盘)")
  def clone_strategy(
    self,
    run_id: str,
    target_mode: StrategyRunMode,
    parameter_overrides: Optional[JSON] = None,
  ) -> OperationResult:
    return StrategyResolver.clone_strategy(
      run_id,
      target_mode,
      parameter_overrides=parameter_overrides,
    )

  @strawberry.field(description="创建策略实例")
  async def create_strategy_instance(
    self,
    input: StrategyInstanceCreateInput,
    auto_start: bool = True,
  ) -> StrategyInstance:
    return await StrategyResolver.create_strategy_instance(input, auto_start)

  @strawberry.field(description="更新策略实例参数")
  async def update_strategy_instance_parameters(
    self,
    instance_id: str,
    input: StrategyInstanceParameterUpdateInput,
  ) -> Optional[StrategyInstance]:
    return await StrategyResolver.update_strategy_instance_parameters(instance_id, input)

  @strawberry.field(description="更新 Pullback Grid 网格簿")
  async def update_strategy_grid_book(
    self,
    instance_id: str,
    input: StrategyGridBookUpdateInput,
  ) -> StrategyGridBook:
    return await StrategyResolver.update_strategy_grid_book(instance_id, input)

  @strawberry.field(description="暂停策略实例")
  async def pause_strategy_instance(self, instance_id: str) -> OperationResult:
    return await StrategyResolver.pause_strategy_instance(instance_id)

  @strawberry.field(description="恢复策略实例")
  async def resume_strategy_instance(self, instance_id: str) -> OperationResult:
    return await StrategyResolver.resume_strategy_instance(instance_id)

  @strawberry.field(description="确认一个等待人工授权的策略交易意图")
  async def approve_strategy_trade_intent(
    self,
    run_id: str,
    intent_id: str,
  ) -> OperationResult:
    return await StrategyResolver.approve_strategy_trade_intent(run_id, intent_id)

  @strawberry.field(description="拒绝一个等待人工授权的策略交易意图")
  async def reject_strategy_trade_intent(
    self,
    run_id: str,
    intent_id: str,
    reason: str = "USER_REJECTED",
  ) -> OperationResult:
    return await StrategyResolver.reject_strategy_trade_intent(
      run_id,
      intent_id,
      reason,
    )

  @strawberry.field(description="归档策略实例")
  async def archive_strategy_instance(self, instance_id: str) -> Optional[StrategyInstance]:
    return await StrategyResolver.archive_strategy_instance(instance_id)

  @strawberry.field(description="复制策略实例并绑定新标的")
  async def clone_strategy_instance(
    self,
    source_id: str,
    instrument_code: str,
  ) -> StrategyInstance:
    return await StrategyResolver.clone_strategy_instance(source_id, instrument_code)

  # === 向后兼容的旧 API（已废弃）===
  @strawberry.field(description="@deprecated 使用 run_strategy() 替代")
  def create_strategy_run(self, input: StrategyRunInput) -> StrategyRun:
    return StrategyResolver.create_strategy_run(input)

  @strawberry.field(description="@deprecated 使用 start_strategy() 替代")
  def start_strategy_run(self, run_id: str) -> OperationResult:
    return StrategyResolver.start_strategy_run(run_id)

  @strawberry.field(description="@deprecated 使用 stop_strategy() 替代")
  def stop_strategy_run(self, run_id: str) -> OperationResult:
    return StrategyResolver.stop_strategy_run(run_id)

  @strawberry.field(description="@deprecated 使用 pause_strategy() 替代")
  def pause_strategy_run(self, run_id: str) -> OperationResult:
    return StrategyResolver.pause_strategy_run(run_id)

  @strawberry.field(description="@deprecated 使用 resume_strategy() 替代")
  def resume_strategy_run(self, run_id: str) -> OperationResult:
    return StrategyResolver.resume_strategy_run(run_id)
