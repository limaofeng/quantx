"""
卖出管理与统一退出计划的 GraphQL 查询和变更定义
"""

import logging
from typing import List, Optional

import strawberry

from quantx_api.auth.errors import AuthError, forbidden

from ..exit_plan_authorization import (
  ExitPlanAuthorizationChallengeService,
  normalize_exit_plan_authorization_request,
)
from ..liquidation_approval import (
  LiquidationChallengeService,
  normalize_liquidation_request,
)
from ..resolvers.liquidation import LiquidationResolver
from ..security import authorized_account_id, principal_from_context
from ..trade_approval import (
  EXIT_PLAN_SELL_APPROVAL,
  TradeApprovalChallengeError,
  TradeApprovalChallengeService,
)
from ..types import MessageResponse
from ..types.liquidation_types import (
  ConditionalLiquidationEvaluationResult,
  ConditionalLiquidationOrder,
  ConditionalLiquidationOrderInput,
  CreateManualExitPlanInput,
  ExitPlanAuthorizationConfirmationInput,
  ExitPlanAuthorizationConfirmationResult,
  ExitPlanAuthorizationPositionSnapshot,
  ExitPlanAuthorizationPreview,
  ExitPlanAuthorizationPreviewInput,
  ExitPlanAuthorizationPreviewResult,
  ExitPlanCapabilities,
  ExitPlanEventView,
  ExitPlanHoldingCapacity,
  ExitPlanView,
  LiquidateAllPositionsInput,
  LiquidatePositionInput,
  LiquidatePositionsInput,
  LiquidationCompletionStrategy,
  LiquidationConfirmationInput,
  LiquidationConfirmationResult,
  LiquidationConflictPreview,
  LiquidationConflictStrategy,
  LiquidationExecutionMode,
  LiquidationGroupResult,
  LiquidationItemPreview,
  LiquidationOrder,
  LiquidationPlanResult,
  LiquidationPreview,
  LiquidationPreviewInput,
  LiquidationPreviewResult,
  LiquidationResult,
  LiquidationScope,
  LiquidationSummary,
  PositionLiquidationResult,
  RedeemPositionInput,
  RedemptionRecord,
  RedemptionResult,
  UpdateManualExitPlanInput,
)
from ..types.trade_approval_types import (
  TradeApprovalConfirmationResult,
  TradeApprovalPreview,
  TradeApprovalPreviewResult,
)

logger = logging.getLogger(__name__)


def _require_legacy_web_liquidation_session(
  info: strawberry.types.Info,
) -> None:
  """Keep native clients on the snapshot-bound liquidation contract."""

  principal = principal_from_context(info.context)
  if principal.active_account_id is not None:
    raise forbidden(
      "原生设备会话必须使用 previewLiquidation/confirmLiquidation 清仓"
    )
  principal.require_permission("orders:write")


def _native_liquidation_preview(data) -> LiquidationPreview:
  items = [
    LiquidationItemPreview(
      instrument_code=item.instrument_code,
      instrument_name=item.instrument_name,
      total_volume=item.total_volume,
      available_volume=item.available_volume,
      frozen_volume=item.frozen_volume,
      t1_unavailable_volume=item.t1_unavailable_volume,
      protected_volume=item.protected_volume,
      pending_sell_volume=item.pending_sell_volume,
      max_protected_volume=item.max_protected_volume,
      included=item.included,
      reason_code=item.reason_code,
      reason_detail=item.reason_detail,
      position_updated_at=item.position_updated_at,
      conflicts=[
        LiquidationConflictPreview(
          plan_id=conflict.plan_id,
          source_type=conflict.source_type,
          status=conflict.status,
          remaining_volume=conflict.remaining_volume,
          config_version=conflict.config_version,
          pending=conflict.pending,
        )
        for conflict in item.conflicts
      ],
    )
    for item in data.snapshot.items
  ]
  return LiquidationPreview(
    challenge_id=data.challenge_id,
    confirmation_token=data.confirmation_token,
    group_id=data.group_id,
    account_id=data.request.account_id,
    scope=LiquidationScope(data.request.scope),
    instrument_codes=list(data.request.instrument_codes),
    completion_strategy=LiquidationCompletionStrategy(
      data.request.completion_strategy
    ),
    conflict_strategy=LiquidationConflictStrategy(data.request.conflict_strategy),
    execution_mode=LiquidationExecutionMode(data.request.execution_mode),
    idempotency_key=data.request.idempotency_key,
    snapshot_version=data.snapshot.snapshot_version,
    account_updated_at=data.snapshot.account_updated_at,
    rollout_snapshot_id=data.snapshot.rollout_snapshot_id,
    rollout_snapshot_hash=data.snapshot.rollout_snapshot_hash,
    challenge_expires_at=data.challenge_expires_at,
    included_count=sum(1 for item in items if item.included),
    skipped_count=sum(1 for item in items if not item.included),
    items=items,
    warnings=list(data.snapshot.warnings),
  )


def _exit_plan_authorization_preview(data) -> ExitPlanAuthorizationPreview:
  plan = dict(data.plan_binding or {})
  template = dict(plan.get("template") or {})
  position = dict(data.safety_subject.get("position") or {})
  protections = [
    LiquidationConflictPreview(
      plan_id=str(item.get("plan_id") or ""),
      source_type=str(item.get("source_type") or ""),
      status=str(item.get("status") or ""),
      remaining_volume=int(item.get("remaining_volume") or 0),
      config_version=int(item.get("config_version") or 0),
      pending=bool(item.get("pending")),
    )
    for item in list(data.safety_subject.get("other_protections") or [])
  ]
  return ExitPlanAuthorizationPreview(
    challenge_id=data.challenge_id,
    confirmation_token=data.confirmation_token,
    account_id=data.request.account_id,
    plan_id=data.request.plan_id,
    instrument_code=str(plan.get("instrument_code") or ""),
    bucket=str(plan.get("bucket") or ""),
    source_type=str(plan.get("source_type") or ""),
    execution_mode=str(plan.get("execution_mode") or ""),
    config_version=int(plan.get("config_version") or 0),
    protected_volume=int(plan.get("protected_volume") or 0),
    exited_volume=int(plan.get("exited_volume") or 0),
    remaining_volume=int(plan.get("remaining_volume") or 0),
    rules=list(template.get("rules") or []),
    t1_policy=str(template.get("t1_policy") or ""),
    execution_policy=dict(template.get("execution") or {}),
    position=ExitPlanAuthorizationPositionSnapshot(
      total_volume=int(position.get("total_volume") or 0),
      available_volume=int(position.get("available_volume") or 0),
      frozen_volume=int(position.get("frozen_volume") or 0),
      yesterday_volume=int(position.get("yesterday_volume") or 0),
      t1_unavailable_volume=int(position.get("t1_unavailable_volume") or 0),
      position_updated_at=data.position_updated_at,
    ),
    other_protections=protections,
    readiness=dict(data.readiness or {}),
    authorization_fingerprint=data.authorization_fingerprint,
    authorization_expires_at=data.authorization_expires_at,
    challenge_expires_at=data.challenge_expires_at,
    warnings=[
      "确认仅授权该计划当前版本和固定安全快照，不创建委托或成交",
      "规则、数量、配置版本、持仓/T+1、冲突或待成交 SELL 变化后授权失效",
      "授权有效期为服务端固定 7 天；到期后 SELL 意图降级为逐次人工确认",
      "每次触发仍重新经过实时风控、实盘开关、对账和唯一 QMT Agent 门禁",
    ],
  )
@strawberry.type(description="卖出管理与统一退出计划查询")
class LiquidationQuery:
  @strawberry.field(description="统一退出计划列表")
  async def exit_plans(
    self,
    info: strawberry.types.Info,
    account_id: Optional[str] = None,
    instrument_code: Optional[str] = None,
    statuses: Optional[List[str]] = None,
    source_type: Optional[str] = None,
    limit: int = 200,
  ) -> List[ExitPlanView]:
    return await LiquidationResolver.get_exit_plans(
      authorized_account_id(info, account_id),
      instrument_code=instrument_code,
      statuses=statuses,
      source_type=source_type,
      limit=limit,
    )

  @strawberry.field(description="单个统一退出计划")
  async def exit_plan(
    self,
    info: strawberry.types.Info,
    plan_id: str,
  ) -> Optional[ExitPlanView]:
    owner = await LiquidationResolver.exit_plan_account_id(plan_id)
    return await LiquidationResolver.get_exit_plan(
      plan_id, authorized_account_id(info, owner)
    )

  @strawberry.field(description="退出计划规则、模式和冲突处理能力")
  def exit_plan_capabilities(self) -> ExitPlanCapabilities:
    return LiquidationResolver.get_exit_plan_capabilities()

  @strawberry.field(description="股票可供退出计划认领的持仓容量")
  async def exit_plan_holding_capacity(
    self,
    info: strawberry.types.Info,
    instrument_code: str,
    account_id: Optional[str] = None,
  ) -> ExitPlanHoldingCapacity:
    return await LiquidationResolver.get_exit_plan_holding_capacity(
      authorized_account_id(info, account_id), instrument_code
    )

  @strawberry.field(description="退出计划、规则、委托和成交时间线")
  async def exit_plan_events(
    self,
    info: strawberry.types.Info,
    plan_id: str,
    limit: int = 200,
  ) -> List[ExitPlanEventView]:
    owner = await LiquidationResolver.exit_plan_account_id(plan_id)
    return await LiquidationResolver.get_exit_plan_events(
      plan_id,
      authorized_account_id(info, owner),
      limit=limit,
    )

  @strawberry.field(description="获取清仓概况")
  async def liquidation_summary(
    self,
    info: strawberry.types.Info,
    account_id: Optional[str] = None,
  ) -> LiquidationSummary:
    return await LiquidationResolver.get_liquidation_summary(
      authorized_account_id(info, account_id)
    )

  @strawberry.field(description="获取条件清仓单列表")
  async def conditional_liquidation_orders(
    self,
    info: strawberry.types.Info,
    account_id: Optional[str] = None,
    stock_code: Optional[str] = None,
    include_cancelled: bool = False,
  ) -> List[ConditionalLiquidationOrder]:
    return await LiquidationResolver.get_conditional_liquidation_orders(
      authorized_account_id(info, account_id),
      stock_code,
      include_cancelled,
    )

  @strawberry.field(description="获取清仓订单列表")
  async def liquidation_orders(
    self,
    info: strawberry.types.Info,
    account_id: str,
    limit: int = 20,
    offset: int = 0,
  ) -> List[LiquidationOrder]:
    return await LiquidationResolver.get_liquidation_orders(
      authorized_account_id(info, account_id), limit, offset
    )

  @strawberry.field(description="获取单个清仓订单")
  async def liquidation_order(
    self, info: strawberry.types.Info, order_id: str, account_id: str
  ) -> LiquidationOrder:
    return await LiquidationResolver.get_liquidation_order(
      order_id, authorized_account_id(info, account_id)
    )

  @strawberry.field(description="获取赎回记录列表")
  async def redemption_records(
    self,
    info: strawberry.types.Info,
    account_id: str,
    stock_code: str = None,
    limit: int = 20,
    offset: int = 0,
  ) -> List[RedemptionRecord]:
    return await LiquidationResolver.get_redemption_records(
      authorized_account_id(info, account_id), stock_code, limit, offset
    )


@strawberry.type(description="卖出管理与统一退出计划变更")
class LiquidationMutation:
  @strawberry.mutation(description="预览既有 LIVE 退出计划的精确自动实盘授权")
  async def preview_exit_plan_authorization(
    self,
    info: strawberry.types.Info,
    input: ExitPlanAuthorizationPreviewInput,
  ) -> ExitPlanAuthorizationPreviewResult:
    try:
      principal = principal_from_context(info.context)
      request = normalize_exit_plan_authorization_request(
        account_id=authorized_account_id(info, input.account_id),
        plan_id=input.plan_id,
        expected_config_version=input.expected_config_version,
        idempotency_key=input.idempotency_key,
      )
      preview = await ExitPlanAuthorizationChallengeService.issue(
        principal=principal,
        request=request,
      )
      return ExitPlanAuthorizationPreviewResult(
        success=True,
        code="PREVIEW_READY",
        message="请核对规则、保护量、T+1、委托策略和 7 天授权期限后进行本机确认",
        preview=_exit_plan_authorization_preview(preview),
      )
    except TradeApprovalChallengeError as exc:
      return ExitPlanAuthorizationPreviewResult(False, exc.code, exc.message)
    except AuthError as exc:
      return ExitPlanAuthorizationPreviewResult(False, exc.code, exc.message)
    except Exception:
      logger.exception("退出计划自动实盘授权预览失败")
      return ExitPlanAuthorizationPreviewResult(
        False,
        "EXIT_PLAN_AUTHORIZATION_UNAVAILABLE",
        "授权预览暂不可用，请刷新退出计划和账户快照后重试",
      )

  @strawberry.mutation(description="确认精确计划版本的自动实盘退出授权")
  async def confirm_exit_plan_authorization(
    self,
    info: strawberry.types.Info,
    input: ExitPlanAuthorizationConfirmationInput,
  ) -> ExitPlanAuthorizationConfirmationResult:
    try:
      principal = principal_from_context(info.context)
      principal.require_permission("trade:approve")
      request = normalize_exit_plan_authorization_request(
        account_id=authorized_account_id(info, input.account_id),
        plan_id=input.plan_id,
        expected_config_version=input.expected_config_version,
        idempotency_key=input.idempotency_key,
      )
      result = await ExitPlanAuthorizationChallengeService.confirm(
        principal=principal,
        request=request,
        challenge_id=input.challenge_id,
        confirmation_token=input.confirmation_token,
      )
      return ExitPlanAuthorizationConfirmationResult(
        success=True,
        code="AUTHORIZED",
        message="该退出计划版本已获得精确自动实盘授权；本次确认未创建任何委托",
        challenge_id=result.challenge_id,
        plan_id=result.plan_id,
        config_version=result.config_version,
        authorized=True,
        authorization_expires_at=result.authorization_expires_at,
        audit_event_id=result.audit_event_id,
      )
    except TradeApprovalChallengeError as exc:
      return ExitPlanAuthorizationConfirmationResult(
        False,
        exc.code,
        exc.message,
      )
    except AuthError as exc:
      return ExitPlanAuthorizationConfirmationResult(
        False,
        exc.code,
        exc.message,
      )
    except Exception:
      logger.exception("退出计划自动实盘授权确认失败")
      return ExitPlanAuthorizationConfirmationResult(
        False,
        "EXIT_PLAN_AUTHORIZATION_REJECTED",
        "授权确认未能安全提交，请刷新退出计划后重试",
      )

  @strawberry.mutation(description="预览固定持仓快照并签发组级清仓确认挑战")
  async def preview_liquidation(
    self,
    info: strawberry.types.Info,
    input: LiquidationPreviewInput,
  ) -> LiquidationPreviewResult:
    try:
      principal = principal_from_context(info.context)
      request = normalize_liquidation_request(
        account_id=authorized_account_id(info, input.account_id),
        scope=input.scope,
        instrument_codes=list(input.instrument_codes or []),
        completion_strategy=input.completion_strategy,
        conflict_strategy=input.conflict_strategy,
        execution_mode=input.execution_mode,
        idempotency_key=input.idempotency_key,
      )
      preview = await LiquidationChallengeService.issue(
        principal=principal,
        request=request,
      )
      return LiquidationPreviewResult(
        success=True,
        code="PREVIEW_READY",
        message="请核对证券集合、最大保护量、冲突和执行模式后进行本机确认",
        preview=_native_liquidation_preview(preview),
      )
    except TradeApprovalChallengeError as exc:
      return LiquidationPreviewResult(
        success=False,
        code=exc.code,
        message=exc.message,
      )
    except AuthError as exc:
      return LiquidationPreviewResult(
        success=False,
        code=exc.code,
        message=exc.message,
      )
    except Exception:
      logger.exception("移动端清仓预览失败")
      return LiquidationPreviewResult(
        success=False,
        code="LIQUIDATION_PREVIEW_UNAVAILABLE",
        message="清仓预览暂不可用，请刷新账户快照后重试",
      )

  @strawberry.mutation(description="消费组级挑战并原子排队清仓 Engine 命令")
  async def confirm_liquidation(
    self,
    info: strawberry.types.Info,
    input: LiquidationConfirmationInput,
  ) -> LiquidationConfirmationResult:
    try:
      principal = principal_from_context(info.context)
      # Top-level authorization requires liquidation:control; confirmation
      # additionally requires the independent high-risk approval capability.
      principal.require_permission("trade:approve")
      result = await LiquidationChallengeService.confirm(
        principal=principal,
        challenge_id=input.challenge_id,
        confirmation_token=input.confirmation_token,
      )
      failed = result.status == "FAILED"
      result_items = list((result.result or {}).get("items") or [])
      plans = [
        LiquidationPlanResult(
          instrument_code=str(item.get("instrument_code") or ""),
          success=bool(item.get("success")),
          plan_id=str(item.get("plan_id") or "") or None,
          protected_volume=(
            int(item["protected_volume"])
            if item.get("protected_volume") is not None
            else None
          ),
          conflict_plan_ids=[
            str(value) for value in list(item.get("conflict_plan_ids") or [])
          ],
          error=str(item.get("error") or "") or None,
        )
        for item in result_items
      ]
      created_count = sum(1 for item in plans if item.success)
      failed_count = sum(1 for item in plans if not item.success)
      completed = result.status == "SUCCEEDED"
      group_success = bool((result.result or {}).get("success")) if completed else True
      code = "LIQUIDATION_FAILED" if failed else "LIQUIDATION_QUEUED"
      message = (
        result.error or "清仓计划创建失败"
        if failed
        else "清仓 Engine 命令已排队；计划、委托与成交状态将独立推进"
      )
      if completed:
        code = (
          "LIQUIDATION_CREATED"
          if group_success
          else "LIQUIDATION_PARTIAL"
          if created_count
          else "LIQUIDATION_REJECTED"
        )
        message = f"已创建 {created_count}/{len(plans)} 个固定快照清仓计划"
      return LiquidationConfirmationResult(
        success=not failed and group_success,
        code=code,
        message=message,
        challenge_id=result.challenge_id,
        group_id=result.group_id,
        command_id=result.command_id,
        status=result.status,
        created_count=created_count,
        failed_count=failed_count,
        plans=plans,
      )
    except TradeApprovalChallengeError as exc:
      return LiquidationConfirmationResult(
        success=False,
        code=exc.code,
        message=exc.message,
      )
    except AuthError as exc:
      return LiquidationConfirmationResult(
        success=False,
        code=exc.code,
        message=exc.message,
      )
    except Exception:
      logger.exception("移动端清仓确认排队失败")
      return LiquidationConfirmationResult(
        success=False,
        code="LIQUIDATION_REJECTED",
        message="清仓确认未能安全进入 Engine 队列，请刷新状态后重试",
      )

  @strawberry.mutation(description="预览并签发退出 SELL 意图确认挑战")
  async def preview_exit_intent(
    self,
    info: strawberry.types.Info,
    plan_id: str,
    intent_id: str,
  ) -> TradeApprovalPreviewResult:
    owner = await LiquidationResolver.exit_plan_account_id(plan_id)
    account_id = authorized_account_id(info, owner)
    try:
      preview = await TradeApprovalChallengeService.issue(
        principal=principal_from_context(info.context),
        action=EXIT_PLAN_SELL_APPROVAL,
        account_id=account_id,
        run_id=plan_id,
        intent_id=intent_id,
      )
      return TradeApprovalPreviewResult(
        success=True,
        code="PREVIEW_READY",
        message="请核对卖出意图后确认",
        preview=TradeApprovalPreview.from_data(preview),
      )
    except TradeApprovalChallengeError as exc:
      return TradeApprovalPreviewResult(False, exc.code, exc.message)
    except ValueError as exc:
      return TradeApprovalPreviewResult(False, "VALIDATION_FAILED", str(exc))

  @strawberry.mutation(description="确认退出 SELL 意图并重新进入统一风控")
  async def confirm_exit_intent(
    self,
    info: strawberry.types.Info,
    plan_id: str,
    intent_id: str,
    confirmation_token: str,
  ) -> TradeApprovalConfirmationResult:
    owner = await LiquidationResolver.exit_plan_account_id(plan_id)
    account_id = authorized_account_id(info, owner)
    try:
      challenge_id = await TradeApprovalChallengeService.consume(
        principal=principal_from_context(info.context),
        action=EXIT_PLAN_SELL_APPROVAL,
        account_id=account_id,
        run_id=plan_id,
        intent_id=intent_id,
        confirmation_token=confirmation_token,
      )
      await LiquidationResolver.confirm_exit_intent(
        plan_id=plan_id,
        intent_id=intent_id,
        account_id=account_id,
      )
      return TradeApprovalConfirmationResult(
        True,
        "APPROVED",
        "卖出意图已确认并重新进入下单风控",
        challenge_id,
      )
    except TradeApprovalChallengeError as exc:
      return TradeApprovalConfirmationResult(False, exc.code, exc.message)
    except (ValueError, RuntimeError) as exc:
      return TradeApprovalConfirmationResult(False, "EXECUTION_FAILED", str(exc))

  @strawberry.mutation(description="拒绝退出 SELL 意图")
  async def reject_exit_intent(
    self,
    info: strawberry.types.Info,
    plan_id: str,
    intent_id: str,
    reason: str = "USER_REJECTED",
  ) -> TradeApprovalConfirmationResult:
    owner = await LiquidationResolver.exit_plan_account_id(plan_id)
    account_id = authorized_account_id(info, owner)
    try:
      await LiquidationResolver.reject_exit_intent(
        plan_id=plan_id,
        intent_id=intent_id,
        reason=reason,
        account_id=account_id,
      )
      return TradeApprovalConfirmationResult(
        True,
        "REJECTED",
        "卖出意图已拒绝，计划恢复监控",
      )
    except (ValueError, RuntimeError) as exc:
      return TradeApprovalConfirmationResult(False, "REJECT_FAILED", str(exc))

  @strawberry.mutation(description="创建人工计划")
  async def create_manual_exit_plan(
    self,
    info: strawberry.types.Info,
    input: CreateManualExitPlanInput,
  ) -> ExitPlanView:
    return await LiquidationResolver.create_manual_exit_plan(
      input, authorized_account_id(info, input.account_id)
    )

  @strawberry.mutation(description="更新人工计划规则")
  async def update_manual_exit_plan(
    self,
    info: strawberry.types.Info,
    input: UpdateManualExitPlanInput,
  ) -> ExitPlanView:
    owner = await LiquidationResolver.exit_plan_account_id(input.plan_id)
    return await LiquidationResolver.update_manual_exit_plan(
      input, authorized_account_id(info, owner or input.account_id)
    )

  @strawberry.mutation(description="启用或暂停退出计划")
  async def set_exit_plan_enabled(
    self,
    info: strawberry.types.Info,
    plan_id: str,
    enabled: bool,
    config_version: int,
  ) -> ExitPlanView:
    owner = await LiquidationResolver.exit_plan_account_id(plan_id)
    return await LiquidationResolver.set_exit_plan_enabled(
      plan_id=plan_id,
      enabled=enabled,
      config_version=config_version,
      account_id=authorized_account_id(info, owner),
    )

  @strawberry.mutation(description="取消并释放退出计划保护数量")
  async def cancel_exit_plan(
    self,
    info: strawberry.types.Info,
    plan_id: str,
    config_version: int,
    reason: str = "USER_CANCELLED",
  ) -> ExitPlanView:
    owner = await LiquidationResolver.exit_plan_account_id(plan_id)
    return await LiquidationResolver.cancel_exit_plan(
      plan_id=plan_id,
      config_version=config_version,
      reason=reason,
      account_id=authorized_account_id(info, owner),
    )

  @strawberry.mutation(description="立即评估一个退出计划")
  async def evaluate_exit_plan_now(
    self,
    info: strawberry.types.Info,
    plan_id: str,
  ) -> ExitPlanView:
    owner = await LiquidationResolver.exit_plan_account_id(plan_id)
    return await LiquidationResolver.evaluate_exit_plan_now(
      plan_id=plan_id,
      account_id=authorized_account_id(info, owner),
    )

  @strawberry.mutation(description="按股票创建一组统一清仓计划")
  async def liquidate_positions(
    self,
    info: strawberry.types.Info,
    input: LiquidatePositionsInput,
  ) -> LiquidationGroupResult:
    _require_legacy_web_liquidation_session(info)
    return await LiquidationResolver.liquidate_positions(
      input, authorized_account_id(info, input.account_id)
    )

  @strawberry.mutation(
    description="为当前可退出的未归因持仓创建清仓计划；返回计划结果而非成交确认"
  )
  async def liquidate_all_positions(
    self,
    info: strawberry.types.Info,
    input: LiquidateAllPositionsInput,
  ) -> LiquidationResult:
    _require_legacy_web_liquidation_session(info)
    return await LiquidationResolver.liquidate_all_positions(
      input,
      authorized_account_id(info, input.account_id),
    )

  @strawberry.mutation(
    description="为指定股票当前可退出的未归因持仓创建清仓计划；不直接宣称成交"
  )
  async def liquidate_position(
    self,
    info: strawberry.types.Info,
    input: LiquidatePositionInput,
  ) -> PositionLiquidationResult:
    _require_legacy_web_liquidation_session(info)
    return await LiquidationResolver.liquidate_position(
      input,
      authorized_account_id(info, input.account_id),
    )

  @strawberry.mutation(description="创建或更新条件清仓单")
  async def upsert_conditional_liquidation_order(
    self,
    info: strawberry.types.Info,
    input: ConditionalLiquidationOrderInput,
  ) -> ConditionalLiquidationOrder:
    return await LiquidationResolver.upsert_conditional_liquidation_order(
      input,
      authorized_account_id(info, input.account_id),
    )

  @strawberry.mutation(description="启用或停用条件清仓单")
  async def set_conditional_liquidation_order_enabled(
    self,
    info: strawberry.types.Info,
    order_id: str,
    enabled: bool,
  ) -> Optional[ConditionalLiquidationOrder]:
    owner_account_id = await LiquidationResolver.conditional_order_account_id(order_id)
    return await LiquidationResolver.set_conditional_liquidation_order_enabled(
      order_id,
      enabled,
      authorized_account_id(info, owner_account_id),
    )

  @strawberry.mutation(description="取消条件清仓单")
  async def cancel_conditional_liquidation_order(
    self,
    info: strawberry.types.Info,
    order_id: str,
  ) -> Optional[ConditionalLiquidationOrder]:
    owner_account_id = await LiquidationResolver.conditional_order_account_id(order_id)
    return await LiquidationResolver.cancel_conditional_liquidation_order(
      order_id,
      authorized_account_id(info, owner_account_id),
    )

  @strawberry.mutation(description="立即评估条件清仓单")
  async def evaluate_conditional_liquidation_orders(
    self,
    info: strawberry.types.Info,
    account_id: Optional[str] = None,
    stock_code: Optional[str] = None,
  ) -> List[ConditionalLiquidationEvaluationResult]:
    return await LiquidationResolver.evaluate_conditional_liquidation_orders(
      authorized_account_id(info, account_id),
      stock_code,
    )

  @strawberry.mutation(description="已清仓股票资金赎回")
  async def redeem_cleared_position(
    self,
    info: strawberry.types.Info,
    input: RedeemPositionInput,
  ) -> RedemptionResult:
    return await LiquidationResolver.redeem_cleared_position(
      input,
      authorized_account_id(info),
    )

  @strawberry.mutation(description="取消清仓订单")
  async def cancel_liquidation_order(
    self, info: strawberry.types.Info, order_id: str, account_id: str
  ) -> MessageResponse:
    return await LiquidationResolver.cancel_liquidation_order(
      order_id, authorized_account_id(info, account_id)
    )
