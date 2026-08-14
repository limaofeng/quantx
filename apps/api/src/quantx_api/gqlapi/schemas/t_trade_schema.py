"""GraphQL fields for the existing-position intraday T assistant."""

from datetime import datetime
from typing import List, Optional

import strawberry

from ..resolvers.t_trade import TTradeResolver
from ..security import authorized_account_id, principal_from_context
from ..t_trade_control import (
  TTradeControlChallengeService,
  normalize_t_trade_control_request,
)
from ..trade_approval import (
  T_TRADE_ENTRY_APPROVAL,
  TradeApprovalChallengeError,
  TradeApprovalChallengeService,
)
from ..types.t_trade_types import (
  OperationalAlert,
  TTradeBatch,
  TTradeBatchEvent,
  TTradeBatchEventPage,
  TTradeBatchPage,
  TTradeControlConfirmationInput,
  TTradeControlConfirmationResult,
  TTradeControlPreview,
  TTradeControlPreviewInput,
  TTradeControlPreviewResult,
  TTradeExternalEntryInput,
  TTradeGlobalMonitor,
  TTradeGlobalMutationResult,
  TTradeGlobalSettingsInput,
  TTradeImportedEntry,
  TTradeLiveReadiness,
  TTradeMutationResult,
  TTradeOperationsMutationResult,
  TTradeReadinessCheck,
  TTradeReplay,
  TTradeReplayCyclePage,
  TTradeReplayMutationResult,
  TTradeReplayPreparation,
  TTradeReplayStartInput,
  TTradeRolloutTarget,
  TTradeSession,
  TTradeSignalHistoryEntry,
  TTradeSignalHistoryPage,
  TTradeStartInput,
)
from ..types.trade_approval_types import (
  TradeApprovalConfirmationResult,
  TradeApprovalPreview,
  TradeApprovalPreviewResult,
)


@strawberry.type(description="持仓做 T 查询")
class TTradeQuery:
  @strawberry.field(description="游标分页查询持久化做 T 批次")
  async def t_trade_batches_page(
    self,
    info: strawberry.types.Info,
    account_id: str,
    status_group: Optional[str] = None,
    first: int = 30,
    after: Optional[str] = None,
  ) -> TTradeBatchPage:
    return await TTradeResolver.list_batches_page(
      authorized_account_id(info, account_id),
      status_group,
      first,
      after,
    )

  @strawberry.field(description="游标分页查询做 T 委托和成交事件")
  async def t_trade_batch_events_page(
    self,
    info: strawberry.types.Info,
    account_id: str,
    batch_id: Optional[str] = None,
    first: int = 30,
    after: Optional[str] = None,
  ) -> TTradeBatchEventPage:
    return await TTradeResolver.list_batch_events_page(
      authorized_account_id(info, account_id),
      batch_id,
      first,
      after,
    )

  @strawberry.field(description="游标分页查询做 T 信号历史")
  async def t_trade_signal_history_page(
    self,
    info: strawberry.types.Info,
    account_id: str,
    first: int = 30,
    after: Optional[str] = None,
  ) -> TTradeSignalHistoryPage:
    return await TTradeResolver.list_signal_history_page(
      authorized_account_id(info, account_id),
      first,
      after,
    )

  @strawberry.field(description="查询做 T 生产就绪和灰度状态")
  async def validate_t_trade_live_readiness(
    self,
    info: strawberry.types.Info,
    account_id: str,
  ) -> TTradeLiveReadiness:
    return await TTradeResolver.readiness(authorized_account_id(info, account_id))

  @strawberry.field(description="账户级实盘安全状态")
  async def live_safety_status(
    self,
    info: strawberry.types.Info,
    account_id: str,
  ) -> TTradeLiveReadiness:
    return await TTradeResolver.readiness(authorized_account_id(info, account_id))

  @strawberry.field(description="查询持久化运行告警")
  async def operational_alerts(
    self,
    info: strawberry.types.Info,
    account_id: str,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 100,
  ) -> List[OperationalAlert]:
    return await TTradeResolver.operational_alerts(
      authorized_account_id(info, account_id),
      status=status,
      severity=severity,
      limit=limit,
    )

  @strawberry.field(description="分页查询持久化做 T 批次")
  async def t_trade_batches(
    self,
    info: strawberry.types.Info,
    account_id: str,
    status_group: Optional[str] = None,
    offset: int = 0,
    limit: int = 100,
  ) -> List[TTradeBatch]:
    return await TTradeResolver.list_batches(
      authorized_account_id(info, account_id),
      status_group,
      offset,
      limit,
    )

  @strawberry.field(description="查询做 T 批次的委托和成交事件")
  async def t_trade_batch_events(
    self,
    info: strawberry.types.Info,
    account_id: str,
    batch_id: Optional[str] = None,
    limit: int = 100,
  ) -> List[TTradeBatchEvent]:
    return await TTradeResolver.list_batch_events(
      authorized_account_id(info, account_id),
      batch_id,
      limit,
    )

  @strawberry.field(description="查询已纳入自动卖出的来源成交台账")
  async def t_trade_imported_entries(
    self, info: strawberry.types.Info, account_id: str
  ) -> List[TTradeImportedEntry]:
    return await TTradeResolver.list_imported_entries(
      authorized_account_id(info, account_id)
    )

  @strawberry.field(description="查询账户级全局持仓做 T 监控")
  async def t_trade_global_monitor(
    self, info: strawberry.types.Info, account_id: str
  ) -> TTradeGlobalMonitor:
    return await TTradeResolver.get_global_monitor(
      authorized_account_id(info, account_id)
    )

  @strawberry.field(description="查询账户最近的做 T 买入确认信号历史")
  async def t_trade_signal_history(
    self,
    info: strawberry.types.Info,
    account_id: str,
    limit: int = 50,
  ) -> List[TTradeSignalHistoryEntry]:
    return await TTradeResolver.list_signal_history(
      authorized_account_id(info, account_id),
      limit,
    )

  @strawberry.field(description="查询单个做 T 会话")
  async def t_trade_session(
    self,
    info: strawberry.types.Info,
    run_id: str,
    stock_code: Optional[str] = None,
  ) -> Optional[TTradeSession]:
    owner_account_id = await TTradeResolver.session_account_id(run_id)
    authorized_account_id(info, owner_account_id)
    return await TTradeResolver.get_session(run_id, stock_code)

  @strawberry.field(description="查询做 T 会话列表")
  async def t_trade_sessions(
    self,
    info: strawberry.types.Info,
    account_id: Optional[str] = None,
    stock_code: Optional[str] = None,
    active_only: bool = False,
  ) -> List[TTradeSession]:
    return await TTradeResolver.list_sessions(
      authorized_account_id(info, account_id), stock_code, active_only
    )

  @strawberry.field(description="读取做 T 历史回放所需的初始账户快照")
  async def t_trade_replay_preparation(
    self,
    info: strawberry.types.Info,
    account_id: str,
    start_time: datetime,
  ) -> TTradeReplayPreparation:
    return await TTradeResolver.prepare_replay(
      authorized_account_id(info, account_id), start_time
    )

  @strawberry.field(description="查询单个做 T 历史回放")
  async def t_trade_replay(
    self,
    info: strawberry.types.Info,
    run_id: str,
  ) -> Optional[TTradeReplay]:
    owner_account_id = await TTradeResolver.replay_account_id(run_id)
    authorized_account_id(info, owner_account_id)
    return await TTradeResolver.get_replay(run_id)

  @strawberry.field(description="查询账户做 T 历史回放记录")
  async def t_trade_replay_history(
    self, info: strawberry.types.Info, account_id: str, limit: int = 20
  ) -> List[TTradeReplay]:
    return await TTradeResolver.replay_history(
      authorized_account_id(info, account_id), limit
    )

  @strawberry.field(description="分页查询做 T 历史回放批次")
  async def t_trade_replay_cycles(
    self,
    info: strawberry.types.Info,
    run_id: str,
    offset: int = 0,
    limit: int = 50,
  ) -> TTradeReplayCyclePage:
    owner_account_id = await TTradeResolver.replay_account_id(run_id)
    authorized_account_id(info, owner_account_id)
    return await TTradeResolver.replay_cycles(run_id, offset, limit)


@strawberry.type(description="持仓做 T 操作")
class TTradeMutation:
  @strawberry.mutation(description="确认已查看运行告警")
  async def acknowledge_operational_alert(
    self,
    info: strawberry.types.Info,
    id: strawberry.ID,
  ) -> OperationalAlert:
    principal = principal_from_context(info.context)
    alert_id = str(id)
    account_id = await TTradeResolver.operational_alert_account_id(alert_id)
    if account_id:
      principal.require_account(account_id)
    return await TTradeResolver.acknowledge_operational_alert(
      alert_id,
      actor_id=principal.user_id,
    )

  @strawberry.mutation(description="解决运行告警并记录处置结果")
  async def resolve_operational_alert(
    self,
    info: strawberry.types.Info,
    id: strawberry.ID,
    resolution: str,
  ) -> OperationalAlert:
    principal = principal_from_context(info.context)
    alert_id = str(id)
    account_id = await TTradeResolver.operational_alert_account_id(alert_id)
    if account_id:
      principal.require_account(account_id)
    return await TTradeResolver.resolve_operational_alert(
      alert_id,
      actor_id=principal.user_id,
      resolution=resolution,
    )

  @strawberry.mutation(description="保存并协调全局持仓做 T 监控")
  async def save_t_trade_global_monitor(
    self,
    info: strawberry.types.Info,
    input: TTradeGlobalSettingsInput,
  ) -> TTradeGlobalMutationResult:
    authorized_account_id(info, input.account_id)
    return await TTradeResolver.save_global_monitor(input)

  @strawberry.mutation(description="立即重新同步全局做 T 持仓")
  async def reconcile_t_trade_global_monitor(
    self,
    info: strawberry.types.Info,
    account_id: str,
  ) -> TTradeGlobalMutationResult:
    return await TTradeResolver.reconcile_global_monitor(
      authorized_account_id(info, account_id)
    )

  @strawberry.mutation(description="启动持仓做 T 会话")
  async def start_t_trade_session(
    self,
    info: strawberry.types.Info,
    input: TTradeStartInput,
  ) -> TTradeMutationResult:
    authorized_account_id(info, input.account_id)
    return await TTradeResolver.start_session(input)

  @strawberry.mutation(description="确认做 T 买入信号")
  async def approve_t_trade_entry(
    self,
    info: strawberry.types.Info,
    run_id: str,
    intent_id: str,
    expected_signal_version: int = 0,
    idempotency_key: str = "",
  ) -> TTradeMutationResult:
    owner_account_id = await TTradeResolver.session_account_id(run_id)
    authorized_account_id(info, owner_account_id)
    principal = principal_from_context(info.context)
    return await TTradeResolver.approve_entry(
      run_id,
      intent_id,
      expected_signal_version=expected_signal_version,
      idempotency_key=idempotency_key,
      actor_id=principal.user_id,
      device_session_id=principal.device_session_id,
      approval_channel="GRAPHQL_LEGACY",
    )

  @strawberry.mutation(description="生成一次做 T 买入确认的短时设备绑定预览")
  async def preview_t_trade_entry_approval(
    self,
    info: strawberry.types.Info,
    run_id: str,
    intent_id: str,
  ) -> TradeApprovalPreviewResult:
    principal = principal_from_context(info.context)
    principal.require_permission("trade:approve")
    try:
      owner_account_id = await TTradeResolver.session_account_id(run_id)
      resolved_account_id = authorized_account_id(info, owner_account_id)
      preview = await TradeApprovalChallengeService.issue(
        principal=principal,
        action=T_TRADE_ENTRY_APPROVAL,
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

  @strawberry.mutation(description="消费短时凭据并确认一个做 T 买入信号")
  async def confirm_t_trade_entry_approval(
    self,
    info: strawberry.types.Info,
    run_id: str,
    intent_id: str,
    confirmation_token: str,
  ) -> TradeApprovalConfirmationResult:
    principal = principal_from_context(info.context)
    principal.require_permission("trade:approve")
    try:
      owner_account_id = await TTradeResolver.session_account_id(run_id)
      resolved_account_id = authorized_account_id(info, owner_account_id)
      challenge_id = await TradeApprovalChallengeService.consume(
        principal=principal,
        action=T_TRADE_ENTRY_APPROVAL,
        account_id=resolved_account_id,
        run_id=run_id,
        intent_id=intent_id,
        confirmation_token=confirmation_token,
      )
      result = await TTradeResolver.approve_entry(
        run_id,
        intent_id,
        idempotency_key=challenge_id,
        actor_id=principal.user_id,
        device_session_id=principal.device_session_id,
        approval_channel="IOS_BIOMETRIC",
      )
      return TradeApprovalConfirmationResult(
        success=result.success,
        code=result.code,
        message=result.message,
        challenge_id=challenge_id,
      )
    except TradeApprovalChallengeError as exc:
      return TradeApprovalConfirmationResult(False, exc.code, exc.message)
    except ValueError as exc:
      return TradeApprovalConfirmationResult(False, "VALIDATION_FAILED", str(exc))

  @strawberry.mutation(
    name="previewTTradeControl",
    description="生成一次绑定设备、主账户与安全快照的做 T 控制确认",
  )
  async def preview_t_trade_control(
    self,
    info: strawberry.types.Info,
    input: TTradeControlPreviewInput,
  ) -> TTradeControlPreviewResult:
    principal = principal_from_context(info.context)
    try:
      request = normalize_t_trade_control_request(
        account_id=input.account_id,
        action=input.action,
        policy_version=input.policy_version,
        snapshot_id=input.snapshot_id,
        target_stage=input.target_stage,
        reason=input.reason,
        idempotency_key=input.idempotency_key,
      )
      preview = await TTradeControlChallengeService.issue(
        principal=principal,
        request=request,
      )
      readiness = dict(preview.readiness or {})
      checks = [
        TTradeReadinessCheck(
          code=str(item.get("code") or ""),
          passed=bool(item.get("passed")),
          message=str(item.get("message") or ""),
          scope=str(item.get("scope") or "AUTOMATION"),
        )
        for item in list(readiness.get("checks") or [])
      ]
      return TTradeControlPreviewResult(
        success=True,
        code="T_TRADE_CONTROL_PREVIEW_READY",
        message=(
          "请核对控制动作，并在 60 秒内完成本机认证"
          if preview.token_issued
          else "该幂等预览已存在；原始确认凭据不会再次返回"
        ),
        preview=TTradeControlPreview(
          challenge_id=strawberry.ID(preview.challenge_id),
          confirmation_token=preview.confirmation_token,
          token_issued=preview.token_issued,
          account_id=request.account_id,
          action=request.action,
          policy_version=request.policy_version,
          snapshot_id=request.snapshot_id,
          target_stage=request.target_stage,
          reason=request.reason,
          current_stage=preview.current_stage,
          readiness_status=str(readiness.get("status") or "UNKNOWN"),
          readiness_fingerprint=preview.readiness_fingerprint,
          challenge_expires_at=preview.challenge_expires_at,
          challenge_status=preview.challenge_status,
          operation_status=preview.operation_status,
          checks=checks,
          warnings=[
            "确认只应用账户级控制，不代表任何委托已报送或成交",
            "委托与成交终态必须以 QMT Agent 上报的券商回报为准",
          ],
        ),
      )
    except TradeApprovalChallengeError as exc:
      return TTradeControlPreviewResult(False, exc.code, exc.message)
    except ValueError as exc:
      return TTradeControlPreviewResult(False, "VALIDATION_FAILED", str(exc))

  @strawberry.mutation(
    name="confirmTTradeControl",
    description="消费一次性凭据并重新校验门禁后应用做 T 控制",
  )
  async def confirm_t_trade_control(
    self,
    info: strawberry.types.Info,
    input: TTradeControlConfirmationInput,
  ) -> TTradeControlConfirmationResult:
    principal = principal_from_context(info.context)
    try:
      result = await TTradeControlChallengeService.confirm(
        principal=principal,
        challenge_id=str(input.challenge_id),
        confirmation_token=input.confirmation_token,
      )
      return TTradeControlConfirmationResult(
        success=result.applied,
        code=result.operation_code,
        message=result.message,
        challenge_id=strawberry.ID(result.challenge_id),
        account_id=result.account_id,
        action=result.action,
        challenge_consumed=result.challenge_consumed,
        operation_status=result.operation_status,
        readiness=(
          TTradeResolver._readiness_type(result.readiness)
          if result.readiness is not None
          else None
        ),
      )
    except TradeApprovalChallengeError as exc:
      return TTradeControlConfirmationResult(
        success=False,
        code=exc.code,
        message=exc.message,
        challenge_id=strawberry.ID(str(input.challenge_id)),
        challenge_consumed=False,
        operation_status="NOT_CONSUMED",
      )
    except ValueError as exc:
      return TTradeControlConfirmationResult(
        success=False,
        code="VALIDATION_FAILED",
        message=str(exc),
        challenge_id=strawberry.ID(str(input.challenge_id)),
        challenge_consumed=False,
        operation_status="NOT_CONSUMED",
      )

  @strawberry.mutation(description="基于最新完整快照建立受控交易窗口")
  async def begin_t_trade_controlled_window(
    self,
    info: strawberry.types.Info,
    account_id: str,
    snapshot_id: str,
  ) -> TTradeOperationsMutationResult:
    resolved = authorized_account_id(info, account_id)
    principal = principal_from_context(info.context)
    principal.require_permission("trade:approve")
    return await TTradeResolver.begin_controlled_window(
      resolved,
      user_id=principal.user_id,
      snapshot_id=snapshot_id,
    )

  @strawberry.mutation(description="完成门禁检查并启用 Canary 或开发环境正式 LIVE")
  async def activate_t_trade_live(
    self,
    info: strawberry.types.Info,
    account_id: str,
    policy_version: int,
    target_stage: TTradeRolloutTarget = TTradeRolloutTarget.CANARY,
    confirmation: str = "",
  ) -> TTradeOperationsMutationResult:
    resolved = authorized_account_id(info, account_id)
    principal = principal_from_context(info.context)
    principal.require_permission("trade:approve")
    return await TTradeResolver.activate_live(
      resolved,
      user_id=principal.user_id,
      policy_version=policy_version,
      target_stage=target_stage,
      confirmation=confirmation,
    )

  @strawberry.mutation(description="停止做 T 新买入，继续保护已有批次")
  async def pause_t_trade_entries(
    self,
    info: strawberry.types.Info,
    account_id: str,
    reason: str = "manual pause",
  ) -> TTradeOperationsMutationResult:
    resolved = authorized_account_id(info, account_id)
    principal = principal_from_context(info.context)
    principal.require_permission("trade:approve")
    return await TTradeResolver.pause_entries(
      resolved,
      reason,
      user_id=principal.user_id,
    )

  @strawberry.mutation(description="触发做 T 紧急停止并转人工处置")
  async def trigger_t_trade_kill_switch(
    self,
    info: strawberry.types.Info,
    account_id: str,
    reason: str,
  ) -> TTradeOperationsMutationResult:
    resolved = authorized_account_id(info, account_id)
    principal = principal_from_context(info.context)
    principal.require_permission("trade:approve")
    return await TTradeResolver.trigger_kill_switch(
      resolved,
      reason,
      user_id=principal.user_id,
    )

  @strawberry.mutation(description="撤销当前仍可撤的做 T 委托")
  async def cancel_t_trade_order(
    self,
    info: strawberry.types.Info,
    account_id: str,
    client_order_id: str,
  ) -> TTradeOperationsMutationResult:
    return await TTradeResolver.cancel_order(
      authorized_account_id(info, account_id),
      client_order_id,
    )

  @strawberry.mutation(description="忽略做 T 买入信号")
  async def reject_t_trade_entry(
    self,
    info: strawberry.types.Info,
    run_id: str,
    intent_id: str,
  ) -> TTradeMutationResult:
    owner_account_id = await TTradeResolver.session_account_id(run_id)
    authorized_account_id(info, owner_account_id)
    return await TTradeResolver.reject_entry(run_id, intent_id)

  @strawberry.mutation(description="导入外部已成交买单并启用做 T 自动退出")
  async def import_t_trade_external_entry(
    self,
    info: strawberry.types.Info,
    input: TTradeExternalEntryInput,
  ) -> TTradeMutationResult:
    owner_account_id = await TTradeResolver.session_account_id(input.run_id)
    resolved_account_id = authorized_account_id(info, owner_account_id)
    if resolved_account_id != authorized_account_id(info, input.account_id):
      raise ValueError("做 T 会话与输入资金账户不一致")
    return await TTradeResolver.import_external_entry(input)

  @strawberry.mutation(description="读取 QMT Agent 已收敛的当日委托")
  async def sync_t_trade_source_orders(
    self,
    info: strawberry.types.Info,
    account_id: str,
  ) -> TTradeMutationResult:
    return await TTradeResolver.sync_source_orders(
      authorized_account_id(info, account_id)
    )

  @strawberry.mutation(description="安全停止做 T 会话")
  async def stop_t_trade_session(
    self,
    info: strawberry.types.Info,
    run_id: str,
  ) -> TTradeMutationResult:
    owner_account_id = await TTradeResolver.session_account_id(run_id)
    authorized_account_id(info, owner_account_id)
    return await TTradeResolver.stop_session(run_id)

  @strawberry.mutation(description="启动隔离的做 T 历史回放")
  async def start_t_trade_replay(
    self,
    info: strawberry.types.Info,
    input: TTradeReplayStartInput,
  ) -> TTradeReplayMutationResult:
    authorized_account_id(info, input.account_id)
    return await TTradeResolver.start_replay(input)

  @strawberry.mutation(description="取消正在执行的做 T 历史回放")
  async def cancel_t_trade_replay(
    self,
    info: strawberry.types.Info,
    run_id: str,
  ) -> TTradeReplayMutationResult:
    owner_account_id = await TTradeResolver.replay_account_id(run_id)
    authorized_account_id(info, owner_account_id)
    return await TTradeResolver.cancel_replay(run_id)
