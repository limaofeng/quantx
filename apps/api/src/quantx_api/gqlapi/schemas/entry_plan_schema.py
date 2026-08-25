"""Public GraphQL surface for managed position-building plans."""

from __future__ import annotations

from typing import AsyncIterator, List, Optional

import strawberry
from quantx_infrastructure.services.runtime_subscription_bridge import (
  runtime_subscription_bridge,
)

from quantx_api.auth.errors import forbidden
from quantx_api.gqlapi.resolvers.entry_plans import EntryPlanResolver
from quantx_api.gqlapi.security import principal_from_context
from quantx_api.gqlapi.trade_approval import (
  STRATEGY_TRADE_INTENT_APPROVAL,
  TradeApprovalChallengeError,
  TradeApprovalChallengeService,
)
from quantx_api.gqlapi.types.entry_plan_types import (
  CreateEntryPlanInput,
  EntryAutomationStatus,
  EntryIntent,
  EntryIntentPreview,
  EntryPlan,
  EntryPlanAuthorizationConfirmationInput,
  EntryPlanAuthorizationPreview,
  EntryPlanAuthorizationPreviewInput,
  EntryPlanAuthorizationResult,
  EntryPlanCapabilities,
  EntryPlanEvent,
  EntryPlanMutationResult,
  UpdateEntryPlanInput,
)


def _single_entry_account(info: strawberry.types.Info) -> str:
  principal = principal_from_context(info.context)
  if len(principal.authorized_account_ids) != 1:
    raise forbidden("建仓/加仓托管只允许当前唯一资金账户")
  return principal.require_account()


@strawberry.type
class EntryPlanQuery:
  @strawberry.field(description="查询当前唯一账户的建仓/加仓托管计划")
  async def entry_plans(
    self,
    info: strawberry.types.Info,
    instrument_code: str = "",
    statuses: Optional[List[str]] = None,
  ) -> List[EntryPlan]:
    return await EntryPlanResolver.list(
      _single_entry_account(info),
      instrument_code=instrument_code,
      statuses=statuses,
    )

  @strawberry.field(description="查询单个建仓/加仓托管计划")
  async def entry_plan(
    self,
    info: strawberry.types.Info,
    plan_id: strawberry.ID,
  ) -> Optional[EntryPlan]:
    return await EntryPlanResolver.get(
      _single_entry_account(info), str(plan_id)
    )

  @strawberry.field(description="查询卡片编辑器的唯一规则与字段能力契约")
  async def entry_plan_capabilities(
    self, info: strawberry.types.Info
  ) -> EntryPlanCapabilities:
    _single_entry_account(info)
    return await EntryPlanResolver.capabilities()

  @strawberry.field(description="查询计划的可读审计事件")
  async def entry_plan_events(
    self,
    info: strawberry.types.Info,
    plan_id: strawberry.ID,
    limit: int = 100,
  ) -> List[EntryPlanEvent]:
    return await EntryPlanResolver.events(
      _single_entry_account(info), str(plan_id), limit
    )

  @strawberry.field(description="查询仍等待逐笔确认的买入意图")
  async def pending_entry_intents(
    self,
    info: strawberry.types.Info,
    instrument_code: str = "",
  ) -> List[EntryIntent]:
    return await EntryPlanResolver.pending_intents(
      _single_entry_account(info), instrument_code
    )

  @strawberry.field(description="查询账户级自动买入安全门")
  async def entry_automation_status(
    self, info: strawberry.types.Info
  ) -> EntryAutomationStatus:
    return await EntryPlanResolver.automation_status(_single_entry_account(info))


@strawberry.type
class EntryPlanMutation:
  @strawberry.mutation(description="创建固定单标的建仓/加仓托管计划")
  async def create_entry_plan(
    self,
    info: strawberry.types.Info,
    input: CreateEntryPlanInput,
  ) -> EntryPlanMutationResult:
    principal = principal_from_context(info.context)
    return await EntryPlanResolver.create(
      _single_entry_account(info), principal.user_id, input
    )

  @strawberry.mutation(description="原子更新不存在待收敛订单的托管计划")
  async def update_entry_plan(
    self,
    info: strawberry.types.Info,
    input: UpdateEntryPlanInput,
  ) -> EntryPlanMutationResult:
    principal = principal_from_context(info.context)
    return await EntryPlanResolver.update(
      _single_entry_account(info), principal.user_id, input
    )

  @strawberry.mutation(description="启动或暂停计划的新触发")
  async def set_entry_plan_enabled(
    self,
    info: strawberry.types.Info,
    plan_id: strawberry.ID,
    enabled: bool,
    config_version: int,
  ) -> EntryPlanMutationResult:
    principal = principal_from_context(info.context)
    return await EntryPlanResolver.set_enabled(
      _single_entry_account(info),
      principal.user_id,
      str(plan_id),
      enabled,
      config_version,
    )

  @strawberry.mutation(description="取消计划；可选择请求撤销工作中买单")
  async def cancel_entry_plan(
    self,
    info: strawberry.types.Info,
    plan_id: strawberry.ID,
    config_version: int,
    cancel_working_order: bool = False,
  ) -> EntryPlanMutationResult:
    principal = principal_from_context(info.context)
    return await EntryPlanResolver.cancel(
      _single_entry_account(info),
      principal.user_id,
      str(plan_id),
      config_version,
      cancel_working_order,
    )

  @strawberry.mutation(description="用最新权威快照立即重新检查计划")
  async def evaluate_entry_plan_now(
    self,
    info: strawberry.types.Info,
    plan_id: strawberry.ID,
  ) -> EntryPlanMutationResult:
    return await EntryPlanResolver.evaluate_now(
      _single_entry_account(info), str(plan_id)
    )

  @strawberry.mutation(description="触发计划中指定的人工买入规则并重新执行全部风控")
  async def trigger_entry_plan_manual_rule(
    self,
    info: strawberry.types.Info,
    plan_id: strawberry.ID,
    rule_id: strawberry.ID,
  ) -> EntryPlanMutationResult:
    principal = principal_from_context(info.context)
    principal.require_permission("trade:approve")
    return await EntryPlanResolver.trigger_manual_rule(
      _single_entry_account(info),
      principal.user_id,
      str(plan_id),
      str(rule_id),
    )

  @strawberry.mutation(description="暂停或恢复账户级自动买入；不会补发历史触发")
  async def set_entry_automation_paused(
    self,
    info: strawberry.types.Info,
    paused: bool,
    reason: str,
  ) -> EntryAutomationStatus:
    principal = principal_from_context(info.context)
    return await EntryPlanResolver.set_automation_paused(
      _single_entry_account(info), principal.user_id, paused, reason
    )

  @strawberry.mutation(description="预览设备绑定的限时自动建仓授权")
  async def preview_entry_plan_authorization(
    self,
    info: strawberry.types.Info,
    input: EntryPlanAuthorizationPreviewInput,
  ) -> EntryPlanAuthorizationPreview:
    principal = principal_from_context(info.context)
    if not principal.is_native_session:
      raise ValueError("实盘自动建仓授权只能由本机原生会话确认")
    return await EntryPlanResolver.preview_authorization(
      _single_entry_account(info),
      principal.user_id,
      principal.device_session_id,
      input,
    )

  @strawberry.mutation(description="确认设备绑定的限时自动建仓授权")
  async def confirm_entry_plan_authorization(
    self,
    info: strawberry.types.Info,
    input: EntryPlanAuthorizationConfirmationInput,
  ) -> EntryPlanAuthorizationResult:
    principal = principal_from_context(info.context)
    if not principal.is_native_session:
      return EntryPlanAuthorizationResult(
        success=False,
        code="NATIVE_SESSION_REQUIRED",
        message="实盘自动建仓授权只能由本机原生会话确认",
        authorization_state="REQUIRED",
      )
    return await EntryPlanResolver.confirm_authorization(
      _single_entry_account(info),
      principal.user_id,
      principal.device_session_id,
      input,
    )

  @strawberry.mutation(description="逐笔确认前按最新行情和风控重新预览")
  async def preview_entry_intent(
    self,
    info: strawberry.types.Info,
    plan_id: strawberry.ID,
    intent_id: strawberry.ID,
  ) -> EntryIntentPreview:
    principal = principal_from_context(info.context)
    principal.require_permission("trade:approve")
    account_id = _single_entry_account(info)
    plan = await EntryPlanResolver._require_plan(account_id, str(plan_id))
    preview = await EntryPlanResolver.preview_intent(
      account_id, str(plan_id), str(intent_id)
    )
    if not preview.valid:
      return preview
    challenge = await TradeApprovalChallengeService.issue(
      principal=principal,
      action=STRATEGY_TRADE_INTENT_APPROVAL,
      account_id=account_id,
      run_id=str(plan.run_id),
      intent_id=str(intent_id),
    )
    preview.challenge_id = challenge.challenge_id
    preview.confirmation_token = challenge.confirmation_token
    preview.challenge_expires_at = challenge.challenge_expires_at.isoformat()
    preview.warnings = list(challenge.warnings)
    return preview

  @strawberry.mutation(description="确认一条买入意图并重新执行全部下单风控")
  async def confirm_entry_intent(
    self,
    info: strawberry.types.Info,
    plan_id: strawberry.ID,
    intent_id: strawberry.ID,
    confirmation_token: str,
  ) -> EntryPlanMutationResult:
    principal = principal_from_context(info.context)
    principal.require_permission("trade:approve")
    account_id = _single_entry_account(info)
    plan = await EntryPlanResolver._require_plan(account_id, str(plan_id))
    try:
      challenge_id = await TradeApprovalChallengeService.consume(
        principal=principal,
        action=STRATEGY_TRADE_INTENT_APPROVAL,
        account_id=account_id,
        run_id=str(plan.run_id),
        intent_id=str(intent_id),
        confirmation_token=confirmation_token,
      )
    except TradeApprovalChallengeError as exc:
      return EntryPlanMutationResult(False, exc.code, exc.message)
    return await EntryPlanResolver.confirm_intent(
      account_id,
      str(plan_id),
      str(intent_id),
      actor_user_id=principal.user_id,
      device_session_id=principal.device_session_id,
      challenge_id=challenge_id,
    )

  @strawberry.mutation(description="拒绝本次买入意图，不取消整个计划")
  async def reject_entry_intent(
    self,
    info: strawberry.types.Info,
    plan_id: strawberry.ID,
    intent_id: strawberry.ID,
  ) -> EntryPlanMutationResult:
    return await EntryPlanResolver.reject_intent(
      _single_entry_account(info), str(plan_id), str(intent_id)
    )


@strawberry.type
class EntryPlanSubscription:
  @strawberry.subscription(description="订阅单个建仓/加仓计划的权威投影更新")
  async def entry_plan_updated(
    self,
    info: strawberry.types.Info,
    plan_id: strawberry.ID,
  ) -> AsyncIterator[EntryPlan]:
    account_id = _single_entry_account(info)
    plan_id_value = str(plan_id)
    current = await EntryPlanResolver.get(account_id, plan_id_value)
    if current is None:
      raise ValueError("建仓/加仓计划不存在或不属于当前账户")
    signature = repr(current)
    yield current
    async for _wake_up in runtime_subscription_bridge.stream(
      "strategy-events",
      run_id=str(current.run_id),
    ):
      updated = await EntryPlanResolver.get(account_id, plan_id_value)
      if updated is None:
        return
      updated_signature = repr(updated)
      if updated_signature == signature:
        continue
      signature = updated_signature
      yield updated

  @strawberry.subscription(description="订阅单个建仓/加仓计划的待确认买入意图更新")
  async def entry_intent_updated(
    self,
    info: strawberry.types.Info,
    plan_id: strawberry.ID,
  ) -> AsyncIterator[List[EntryIntent]]:
    account_id = _single_entry_account(info)
    plan_id_value = str(plan_id)
    plan = await EntryPlanResolver.get(account_id, plan_id_value)
    if plan is None:
      raise ValueError("建仓/加仓计划不存在或不属于当前账户")

    async def projection() -> List[EntryIntent]:
      return [
        intent
        for intent in await EntryPlanResolver.pending_intents(account_id)
        if str(intent.plan_id) == plan_id_value
      ]

    current = await projection()
    signature = repr(current)
    yield current
    async for _wake_up in runtime_subscription_bridge.stream(
      "strategy-events",
      run_id=str(plan.run_id),
    ):
      updated = await projection()
      updated_signature = repr(updated)
      if updated_signature == signature:
        continue
      signature = updated_signature
      yield updated
