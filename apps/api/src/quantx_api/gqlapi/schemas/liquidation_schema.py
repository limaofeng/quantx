"""
卖出管理与统一退出计划的 GraphQL 查询和变更定义
"""

from typing import List, Optional

import strawberry

from ..resolvers.liquidation import LiquidationResolver
from ..security import authorized_account_id, principal_from_context
from ..trade_approval import (
  EXIT_PLAN_SELL_APPROVAL,
  TradeApprovalChallengeError,
  TradeApprovalChallengeService,
)
from ..types import MessageResponse
from ..types.trade_approval_types import (
  TradeApprovalConfirmationResult,
  TradeApprovalPreview,
  TradeApprovalPreviewResult,
)
from ..types.liquidation_types import (
  ConditionalLiquidationEvaluationResult,
  ConditionalLiquidationOrder,
  ConditionalLiquidationOrderInput,
  CreateManualExitPlanInput,
  ExitPlanCapabilities,
  ExitPlanEventView,
  ExitPlanHoldingCapacity,
  ExitPlanView,
  LiquidateAllPositionsInput,
  LiquidatePositionInput,
  LiquidatePositionsInput,
  LiquidationGroupResult,
  LiquidationOrder,
  LiquidationResult,
  LiquidationSummary,
  PositionLiquidationResult,
  RedeemPositionInput,
  RedemptionRecord,
  RedemptionResult,
  UpdateManualExitPlanInput,
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
    return await LiquidationResolver.liquidate_positions(
      input, authorized_account_id(info, input.account_id)
    )

  @strawberry.mutation(description="一键清仓")
  async def liquidate_all_positions(
    self,
    info: strawberry.types.Info,
    input: LiquidateAllPositionsInput,
  ) -> LiquidationResult:
    return await LiquidationResolver.liquidate_all_positions(
      input,
      authorized_account_id(info, input.account_id),
    )

  @strawberry.mutation(description="个股清仓")
  async def liquidate_position(
    self,
    info: strawberry.types.Info,
    input: LiquidatePositionInput,
  ) -> PositionLiquidationResult:
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
