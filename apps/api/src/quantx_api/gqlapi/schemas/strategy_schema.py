import json
from datetime import datetime
from typing import List, Optional

import strawberry
from strawberry.scalars import JSON

from ..resolvers.strategies import StrategyResolver
from ..security import authorized_account_id, principal_from_context
from ..strategy_control import (
  StrategyControlChallengeService,
  normalize_strategy_control_request,
)
from ..trade_approval import (
  STRATEGY_TRADE_INTENT_APPROVAL,
  TradeApprovalChallengeError,
  TradeApprovalChallengeService,
)
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
  StrategyInstanceMobileParameters,
  StrategyInstanceParameterUpdateInput,
  StrategyLogPage,
  StrategyPerformance,
  StrategyRun,
  StrategyRunInput,
  StrategyRunMode,
  StrategyRunUpdateInput,
)
from ..types.strategy_types import (
  StrategyControlConfirmationInput,
  StrategyControlConfirmationResult,
  StrategyControlPreview,
  StrategyControlPreviewInput,
  StrategyControlPreviewResult,
  StrategyControlReadinessCheck,
)
from ..types.trade_approval_types import (
  TradeApprovalConfirmationResult,
  TradeApprovalPreview,
  TradeApprovalPreviewResult,
)


def _native_account_id(info: strawberry.types.Info) -> Optional[str]:
  """Return the personal account for a native session."""

  principal = principal_from_context(info.context)
  if not principal.is_native_session:
    return None
  return principal.require_account()


async def _authorize_native_strategy_run(
  info: strawberry.types.Info,
  run_id: str,
) -> None:
  account_id = _native_account_id(info)
  if account_id is None:
    return
  bound_account_id = await StrategyResolver.strategy_run_account_id(run_id)
  if bound_account_id != account_id:
    # Use the shared authorization error rather than revealing whether a
    # cross-account run identifier exists.
    principal_from_context(info.context).require_account(bound_account_id)


@strawberry.type(description="策略相关查询")
class StrategyQuery:
  @strawberry.field(description="获取策略模板列表")
  async def strategies(
    self,
    include_assistant_managed: bool = False,
  ) -> List[Strategy]:
    return await StrategyResolver.get_strategies(
      include_assistant_managed=include_assistant_managed
    )

  @strawberry.field(description="获取单个策略模板")
  async def strategy(self, strategy_id: int) -> Optional[Strategy]:
    return await StrategyResolver.get_strategy(strategy_id)

  @strawberry.field(description="获取策略运行列表")
  async def strategy_runs(
    self,
    info: strawberry.types.Info,
    include_assistant_managed: bool = False,
  ) -> List[StrategyRun]:
    return await StrategyResolver.get_strategy_runs(
      include_assistant_managed=include_assistant_managed,
      account_id=_native_account_id(info),
    )

  @strawberry.field(description="获取单个策略运行")
  async def strategy_run(
    self,
    info: strawberry.types.Info,
    run_id: str,
  ) -> Optional[StrategyRun]:
    await _authorize_native_strategy_run(info, run_id)
    return await StrategyResolver.get_strategy_run(run_id)

  @strawberry.field(description="获取策略运行的回测历史")
  async def backtest_history(
    self,
    info: strawberry.types.Info,
    run_id: str,
  ) -> List[StrategyBacktest]:
    await _authorize_native_strategy_run(info, run_id)
    return await StrategyResolver.get_backtest_history(run_id)

  @strawberry.field(description="获取策略库定义列表")
  async def strategy_definitions(
    self,
    include_assistant_managed: bool = False,
  ) -> List[StrategyDefinition]:
    return await StrategyResolver.get_strategy_definitions(
      include_assistant_managed=include_assistant_managed
    )

  @strawberry.field(description="获取策略实例列表")
  async def strategy_instances(
    self,
    info: strawberry.types.Info,
    status: Optional[str] = None,
    strategy_key: Optional[str] = None,
    instrument_code: Optional[str] = None,
    include_assistant_managed: bool = False,
  ) -> List[StrategyInstance]:
    return await StrategyResolver.get_strategy_instances(
      status=status,
      strategy_key=strategy_key,
      instrument_code=instrument_code,
      include_assistant_managed=include_assistant_managed,
      account_id=_native_account_id(info),
    )

  @strawberry.field(description="获取单个策略实例")
  async def strategy_instance(
    self,
    info: strawberry.types.Info,
    id: str,
  ) -> Optional[StrategyInstance]:
    await _authorize_native_strategy_run(info, id)
    return await StrategyResolver.get_strategy_instance(id)

  @strawberry.field(description="获取策略实例允许原生移动端修改的安全参数")
  async def strategy_instance_mobile_parameters(
    self,
    info: strawberry.types.Info,
    instance_id: str,
  ) -> StrategyInstanceMobileParameters:
    await _authorize_native_strategy_run(info, instance_id)
    return await StrategyResolver.get_strategy_instance_mobile_parameters(
      instance_id
    )

  @strawberry.field(description="获取策略运行中等待人工确认的交易意图")
  async def strategy_pending_trade_intents(
    self,
    info: strawberry.types.Info,
    run_id: str,
  ) -> List[StrategyApprovalIntent]:
    await _authorize_native_strategy_run(info, run_id)
    return await StrategyResolver.get_strategy_pending_trade_intents(run_id)

  @strawberry.field(description="获取策略运行的统一自动退出计划")
  async def strategy_exit_plans(
    self,
    info: strawberry.types.Info,
    run_id: str,
  ) -> List[StrategyExitPlanView]:
    await _authorize_native_strategy_run(info, run_id)
    return await StrategyResolver.get_strategy_exit_plans(run_id)

  @strawberry.field(description="获取策略实例决策审计历史")
  async def strategy_decision_history(
    self,
    info: strawberry.types.Info,
    instance_id: str,
    cursor: Optional[str] = None,
    limit: int = 50,
    backtest_id: Optional[str] = None,
  ) -> List[StrategyDecision]:
    await _authorize_native_strategy_run(info, instance_id)
    return await StrategyResolver.get_strategy_decision_history(
      instance_id=instance_id,
      cursor=cursor,
      limit=limit,
      backtest_id=backtest_id,
    )

  @strawberry.field(description="获取策略实例执行跟踪")
  async def strategy_execution_trace(
    self,
    info: strawberry.types.Info,
    instance_id: str,
    decision_id: Optional[str] = None,
    backtest_id: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = 50,
  ) -> List[ExecutionTraceView]:
    await _authorize_native_strategy_run(info, instance_id)
    return await StrategyResolver.get_strategy_execution_trace(
      instance_id=instance_id,
      decision_id=decision_id,
      backtest_id=backtest_id,
      cursor=cursor,
      limit=limit,
    )

  @strawberry.field(description="获取策略实例三仓归因")
  async def strategy_bucket_ledger(
    self,
    info: strawberry.types.Info,
    instance_id: str,
  ) -> BucketLedgerView:
    await _authorize_native_strategy_run(info, instance_id)
    return await StrategyResolver.get_strategy_bucket_ledger(instance_id)

  @strawberry.field(description="获取 Pullback Grid 网格簿")
  async def strategy_grid_book(
    self,
    info: strawberry.types.Info,
    instance_id: str,
    backtest_id: Optional[str] = None,
    version: Optional[int] = None,
  ) -> StrategyGridBook:
    await _authorize_native_strategy_run(info, instance_id)
    return await StrategyResolver.get_strategy_grid_book(
      instance_id,
      backtest_id=backtest_id,
      version=version,
    )

  @strawberry.field(description="获取策略绩效")
  async def strategy_performance(
    self,
    info: strawberry.types.Info,
    run_id: str,
    backtest_id: Optional[str] = None,
    benchmark_code: Optional[str] = None,
    cursor: Optional[str] = None,
    limit: int = 2000,
  ) -> StrategyPerformance:
    await _authorize_native_strategy_run(info, run_id)
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
    info: strawberry.types.Info,
    run_id: str,
    backtest_id: Optional[str] = None,
    version: Optional[int] = None,
    cursor: Optional[int] = None,
    limit: int = 200,
    before: bool = False,
    tail: bool = True,
  ) -> StrategyLogPage:
    await _authorize_native_strategy_run(info, run_id)
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
    info: strawberry.types.Info,
    instance_id: str,
    input: StrategyInstanceParameterUpdateInput,
  ) -> Optional[StrategyInstance]:
    principal = principal_from_context(info.context)
    await _authorize_native_strategy_run(info, instance_id)
    return await StrategyResolver.update_strategy_instance_parameters(
      instance_id,
      input,
      mobile_only=principal.is_native_session,
    )

  @strawberry.field(description="更新 Pullback Grid 网格簿")
  async def update_strategy_grid_book(
    self,
    instance_id: str,
    input: StrategyGridBookUpdateInput,
  ) -> StrategyGridBook:
    return await StrategyResolver.update_strategy_grid_book(instance_id, input)

  @strawberry.field(description="暂停策略实例")
  async def pause_strategy_instance(
    self,
    info: strawberry.types.Info,
    instance_id: str,
  ) -> OperationResult:
    await _authorize_native_strategy_run(info, instance_id)
    return await StrategyResolver.pause_strategy_instance(instance_id)

  @strawberry.field(description="恢复策略实例")
  async def resume_strategy_instance(
    self,
    info: strawberry.types.Info,
    instance_id: str,
  ) -> OperationResult:
    await _authorize_native_strategy_run(info, instance_id)
    principal = principal_from_context(info.context)
    if (
      principal.is_native_session
      and await StrategyControlChallengeService.instance_requires_confirmation(
        instance_id
      )
    ):
      return OperationResult(
        success=False,
        message=(
          "实盘策略恢复必须使用 previewStrategyControl / "
          "confirmStrategyControl 并逐次进行本机生物确认"
        ),
      )
    return await StrategyResolver.resume_strategy_instance(instance_id)

  @strawberry.field(description="预览实盘策略控制并签发设备绑定挑战")
  async def preview_strategy_control(
    self,
    info: strawberry.types.Info,
    input: StrategyControlPreviewInput,
  ) -> StrategyControlPreviewResult:
    try:
      principal = principal_from_context(info.context)
      account_id = authorized_account_id(info, input.account_id)
      request = normalize_strategy_control_request(
        account_id=account_id,
        instance_id=input.instance_id,
        action=input.action,
        expected_config_version=input.expected_config_version,
        idempotency_key=input.idempotency_key,
      )
      issued = await StrategyControlChallengeService.issue(
        principal=principal,
        request=request,
      )
      checks = [
        StrategyControlReadinessCheck(
          code=str(item.get("code") or ""),
          passed=bool(item.get("passed")),
          message=str(item.get("message") or ""),
        )
        for item in list(issued.readiness.get("checks") or [])
      ]
      return StrategyControlPreviewResult(
        success=True,
        code="STRATEGY_CONTROL_PREVIEW_READY",
        message="请核对策略、账户和实盘就绪快照后进行本机生物确认",
        preview=StrategyControlPreview(
          challenge_id=issued.challenge_id,
          confirmation_token=issued.confirmation_token,
          account_id=request.account_id,
          instance_id=request.instance_id,
          target_instance_id=issued.target_instance_id,
          action=request.action,
          current_mode=issued.current_mode,
          current_status=issued.current_status,
          config_version=issued.config_version,
          readiness_status=str(issued.readiness.get("status") or "UNKNOWN"),
          snapshot_id=(
            str(issued.readiness.get("snapshot_id"))
            if issued.readiness.get("snapshot_id")
            else None
          ),
          snapshot_at=issued.readiness.get("snapshot_at"),
          challenge_expires_at=issued.challenge_expires_at,
          checks=checks,
          warnings=[
            "确认只控制策略生命周期，不代表任何交易已报送或成交",
            "策略后续每个 TradeIntent 仍须经过统一 A 股合法性和实时风控",
            "实盘安全快照、配置版本或策略状态变化都会使本次确认失效",
          ],
        ),
      )
    except TradeApprovalChallengeError as exc:
      return StrategyControlPreviewResult(
        success=False,
        code=exc.code,
        message=exc.message,
      )

  @strawberry.field(description="消费设备绑定挑战并应用实盘策略控制")
  async def confirm_strategy_control(
    self,
    info: strawberry.types.Info,
    input: StrategyControlConfirmationInput,
  ) -> StrategyControlConfirmationResult:
    try:
      confirmed = await StrategyControlChallengeService.confirm(
        principal=principal_from_context(info.context),
        challenge_id=input.challenge_id,
        confirmation_token=input.confirmation_token,
      )
      return StrategyControlConfirmationResult(
        success=True,
        code="STRATEGY_CONTROL_APPLIED",
        message="Engine 已应用策略控制；请刷新策略投影确认当前状态",
        challenge_id=confirmed.challenge_id,
        instance_id=confirmed.instance_id,
        status=confirmed.status,
      )
    except TradeApprovalChallengeError as exc:
      return StrategyControlConfirmationResult(
        success=False,
        code=exc.code,
        message=exc.message,
      )

  @strawberry.field(description="确认一个等待人工授权的策略交易意图")
  async def approve_strategy_trade_intent(
    self,
    info: strawberry.types.Info,
    run_id: str,
    intent_id: str,
  ) -> OperationResult:
    principal = principal_from_context(info.context)
    account_id = await StrategyResolver.strategy_run_account_id(run_id)
    authorized_account_id(info, account_id)
    return await StrategyResolver.approve_strategy_trade_intent(
      run_id,
      intent_id,
      actor_id=principal.user_id,
      device_session_id=principal.device_session_id,
      approval_channel="GRAPHQL_LEGACY",
    )

  @strawberry.field(description="拒绝一个等待人工授权的策略交易意图")
  async def reject_strategy_trade_intent(
    self,
    info: strawberry.types.Info,
    run_id: str,
    intent_id: str,
    reason: str = "USER_REJECTED",
  ) -> OperationResult:
    principal = principal_from_context(info.context)
    account_id = await StrategyResolver.strategy_run_account_id(run_id)
    authorized_account_id(info, account_id)
    return await StrategyResolver.reject_strategy_trade_intent(
      run_id,
      intent_id,
      reason,
      actor_id=principal.user_id,
      device_session_id=principal.device_session_id,
      approval_channel="GRAPHQL_LEGACY",
    )

  @strawberry.mutation(description="生成一次策略买入意图的短时设备绑定确认预览")
  async def preview_strategy_trade_intent_approval(
    self,
    info: strawberry.types.Info,
    run_id: str,
    intent_id: str,
  ) -> TradeApprovalPreviewResult:
    principal = principal_from_context(info.context)
    principal.require_permission("trade:approve")
    try:
      account_id = await StrategyResolver.strategy_run_account_id(run_id)
      resolved_account_id = authorized_account_id(info, account_id)
      preview = await TradeApprovalChallengeService.issue(
        principal=principal,
        action=STRATEGY_TRADE_INTENT_APPROVAL,
        account_id=resolved_account_id,
        run_id=run_id,
        intent_id=intent_id,
      )
      return TradeApprovalPreviewResult(
        success=True,
        code="PREVIEW_READY",
        message="请核对交易信息并在凭据过期前完成本机认证",
        preview=TradeApprovalPreview.from_data(preview),
      )
    except TradeApprovalChallengeError as exc:
      return TradeApprovalPreviewResult(False, exc.code, exc.message)
    except ValueError as exc:
      return TradeApprovalPreviewResult(False, "VALIDATION_FAILED", str(exc))

  @strawberry.mutation(description="消费短时凭据并确认一个策略买入意图")
  async def confirm_strategy_trade_intent_approval(
    self,
    info: strawberry.types.Info,
    run_id: str,
    intent_id: str,
    confirmation_token: str,
  ) -> TradeApprovalConfirmationResult:
    principal = principal_from_context(info.context)
    principal.require_permission("trade:approve")
    try:
      account_id = await StrategyResolver.strategy_run_account_id(run_id)
      resolved_account_id = authorized_account_id(info, account_id)
      challenge_id = await TradeApprovalChallengeService.consume(
        principal=principal,
        action=STRATEGY_TRADE_INTENT_APPROVAL,
        account_id=resolved_account_id,
        run_id=run_id,
        intent_id=intent_id,
        confirmation_token=confirmation_token,
      )
      result = await StrategyResolver.approve_strategy_trade_intent(
        run_id,
        intent_id,
        actor_id=principal.user_id,
        device_session_id=principal.device_session_id,
        approval_channel="IOS_BIOMETRIC",
        challenge_id=challenge_id,
      )
      data = json.loads(result.data) if result.data else {}
      return TradeApprovalConfirmationResult(
        success=result.success,
        code=str(data.get("code") or ("APPROVED" if result.success else "FAILED")),
        message=result.message,
        challenge_id=challenge_id,
      )
    except TradeApprovalChallengeError as exc:
      return TradeApprovalConfirmationResult(False, exc.code, exc.message)
    except (ValueError, json.JSONDecodeError) as exc:
      return TradeApprovalConfirmationResult(False, "VALIDATION_FAILED", str(exc))

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
