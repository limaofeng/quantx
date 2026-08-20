"""GraphQL schema surface for the account-level board assistant."""

from datetime import datetime

import strawberry

from quantx_api.gqlapi.resolvers.limit_up_board_assistant import (
  LimitUpBoardAssistantResolver,
)
from quantx_api.gqlapi.resolvers.limit_up_board_replay import (
  LimitUpBoardReplayResolver,
)
from quantx_api.gqlapi.security import authorized_account_id, principal_from_context
from quantx_api.gqlapi.types.limit_up_board_assistant_types import (
  FirstBoardCandidatePreferenceInput,
  LimitUpBoardAssistant,
  LimitUpBoardAssistantMutationResult,
  LimitUpBoardAssistantSettingsInput,
  LimitUpBoardCandidateActionInput,
)
from quantx_api.gqlapi.types.limit_up_board_replay_types import (
  LimitUpBoardReplay,
  LimitUpBoardReplayCurvePage,
  LimitUpBoardReplayMutationResult,
  LimitUpBoardReplayPreparation,
  LimitUpBoardReplayScenarioProfile,
  LimitUpBoardReplayStartInput,
  LimitUpBoardReplayTradePage,
)


@strawberry.type
class LimitUpBoardAssistantQuery:
  @strawberry.field(description="查询账户级打板助手")
  async def limit_up_board_assistant(
    self,
    info: strawberry.types.Info,
    account_id: str,
  ) -> LimitUpBoardAssistant:
    return await LimitUpBoardAssistantResolver.get(
      authorized_account_id(info, account_id)
    )

  @strawberry.field(description="检查账户级打板助手历史回放启动条件")
  async def limit_up_board_replay_preparation(
    self,
    info: strawberry.types.Info,
    account_id: str,
    start_time: datetime,
    end_time: datetime,
    scenario_profile: LimitUpBoardReplayScenarioProfile = (
      LimitUpBoardReplayScenarioProfile.STANDARD_V1
    ),
  ) -> LimitUpBoardReplayPreparation:
    authorized = authorized_account_id(info, account_id)
    return await LimitUpBoardReplayResolver.prepare(
      authorized,
      start_time,
      end_time,
      scenario_profile.value,
    )

  @strawberry.field(description="查询单个账户级打板助手历史回放")
  async def limit_up_board_replay(
    self,
    info: strawberry.types.Info,
    job_id: str,
  ) -> LimitUpBoardReplay | None:
    owner_account_id = await LimitUpBoardReplayResolver.replay_account_id(job_id)
    if owner_account_id is None:
      return None
    authorized_account_id(info, owner_account_id)
    return await LimitUpBoardReplayResolver.get(job_id)

  @strawberry.field(description="查询账户级打板助手历史回放记录")
  async def limit_up_board_replay_history(
    self,
    info: strawberry.types.Info,
    account_id: str,
    limit: int = 20,
  ) -> list[LimitUpBoardReplay]:
    return await LimitUpBoardReplayResolver.history(
      authorized_account_id(info, account_id),
      limit,
    )

  @strawberry.field(description="分页查询打板助手历史回放成交明细")
  async def limit_up_board_replay_trades(
    self,
    info: strawberry.types.Info,
    job_id: str,
    scenario_id: str,
    offset: int = 0,
    limit: int = 100,
  ) -> LimitUpBoardReplayTradePage:
    owner_account_id = await LimitUpBoardReplayResolver.replay_account_id(job_id)
    if owner_account_id is None:
      raise ValueError("打板历史回放任务不存在")
    authorized_account_id(info, owner_account_id)
    return await LimitUpBoardReplayResolver.trades(
      job_id,
      scenario_id,
      offset,
      limit,
    )

  @strawberry.field(description="分页查询打板助手历史回放权益曲线")
  async def limit_up_board_replay_curve(
    self,
    info: strawberry.types.Info,
    job_id: str,
    scenario_id: str,
    offset: int = 0,
    limit: int = 2_000,
  ) -> LimitUpBoardReplayCurvePage:
    owner_account_id = await LimitUpBoardReplayResolver.replay_account_id(job_id)
    if owner_account_id is None:
      raise ValueError("打板历史回放任务不存在")
    authorized_account_id(info, owner_account_id)
    return await LimitUpBoardReplayResolver.curve(
      job_id,
      scenario_id,
      offset,
      limit,
    )


@strawberry.type
class LimitUpBoardAssistantMutation:
  @strawberry.mutation(description="启动隔离的账户级打板助手历史回放")
  async def start_limit_up_board_replay(
    self,
    info: strawberry.types.Info,
    input: LimitUpBoardReplayStartInput,
  ) -> LimitUpBoardReplayMutationResult:
    authorized_account_id(info, input.account_id)
    return await LimitUpBoardReplayResolver.start(input)

  @strawberry.mutation(description="取消执行中的账户级打板助手历史回放")
  async def cancel_limit_up_board_replay(
    self,
    info: strawberry.types.Info,
    job_id: str,
  ) -> LimitUpBoardReplayMutationResult:
    owner_account_id = await LimitUpBoardReplayResolver.replay_account_id(job_id)
    if owner_account_id is None:
      return LimitUpBoardReplayMutationResult(
        success=False,
        code="REPLAY_NOT_FOUND",
        message="打板历史回放任务不存在",
      )
    authorized_account_id(info, owner_account_id)
    return await LimitUpBoardReplayResolver.cancel(job_id)

  @strawberry.mutation(description="保存并协调账户级打板助手")
  async def save_limit_up_board_assistant(
    self,
    info: strawberry.types.Info,
    input: LimitUpBoardAssistantSettingsInput,
  ) -> LimitUpBoardAssistantMutationResult:
    authorized_account_id(info, input.account_id)
    return await LimitUpBoardAssistantResolver.save(input)

  @strawberry.mutation(description="保存并协调首板晋级 V2 助手")
  async def save_first_board_assistant(
    self,
    info: strawberry.types.Info,
    input: LimitUpBoardAssistantSettingsInput,
  ) -> LimitUpBoardAssistantMutationResult:
    authorized_account_id(info, input.account_id)
    return await LimitUpBoardAssistantResolver.save(input)

  @strawberry.mutation(description="立即重新协调账户级打板助手")
  async def reconcile_limit_up_board_assistant(
    self,
    info: strawberry.types.Info,
    account_id: str,
  ) -> LimitUpBoardAssistantMutationResult:
    return await LimitUpBoardAssistantResolver.reconcile(
      authorized_account_id(info, account_id)
    )

  @strawberry.mutation(description="将雷达候选加入当日人工布防")
  async def arm_limit_up_board_candidate(
    self,
    info: strawberry.types.Info,
    input: LimitUpBoardCandidateActionInput,
  ) -> LimitUpBoardAssistantMutationResult:
    authorized_account_id(info, input.account_id)
    principal = principal_from_context(info.context)
    return await LimitUpBoardAssistantResolver.arm(
      input,
      actor_id=principal.user_id,
    )

  @strawberry.mutation(description="取消雷达候选的当日人工布防")
  async def disarm_limit_up_board_candidate(
    self,
    info: strawberry.types.Info,
    input: LimitUpBoardCandidateActionInput,
  ) -> LimitUpBoardAssistantMutationResult:
    authorized_account_id(info, input.account_id)
    principal = principal_from_context(info.context)
    return await LimitUpBoardAssistantResolver.disarm(
      input,
      actor_id=principal.user_id,
    )

  @strawberry.mutation(description="设置首板候选优先关注或忽略偏好")
  async def set_first_board_candidate_preference(
    self,
    info: strawberry.types.Info,
    input: FirstBoardCandidatePreferenceInput,
  ) -> LimitUpBoardAssistantMutationResult:
    authorized_account_id(info, input.account_id)
    principal = principal_from_context(info.context)
    return await LimitUpBoardAssistantResolver.set_preference(
      input,
      actor_id=principal.user_id,
    )
