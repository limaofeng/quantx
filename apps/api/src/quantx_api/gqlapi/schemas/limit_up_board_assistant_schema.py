"""GraphQL schema surface for the account-level board assistant."""

import strawberry

from quantx_api.gqlapi.security import authorized_account_id, principal_from_context
from quantx_api.gqlapi.resolvers.limit_up_board_assistant import (
  LimitUpBoardAssistantResolver,
)
from quantx_api.gqlapi.types.limit_up_board_assistant_types import (
  LimitUpBoardAssistant,
  LimitUpBoardAssistantMutationResult,
  LimitUpBoardAssistantSettingsInput,
  LimitUpBoardCandidateActionInput,
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


@strawberry.type
class LimitUpBoardAssistantMutation:
  @strawberry.mutation(description="保存并协调账户级打板助手")
  async def save_limit_up_board_assistant(
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
