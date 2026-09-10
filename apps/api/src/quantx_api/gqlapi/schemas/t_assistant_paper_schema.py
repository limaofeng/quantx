"""Authorized, read-only PAPER fact queries."""

import strawberry

from ..resolvers.t_assistant_paper import TAssistantPaperResolver
from ..security import authorized_account_id
from ..types.t_assistant_paper_types import (
  TAssistantPaperAllocation,
  TAssistantPaperExecution,
  TAssistantPaperExitPlan,
  TAssistantPaperOpportunity,
  TAssistantPaperOrder,
  TAssistantPaperPage,
  TAssistantPaperReason,
)


@strawberry.type
class TAssistantPaperQuery:
  @strawberry.field(description="分页读取授权账户的 PAPER 做 T 执行实例")
  async def t_assistant_paper_executions(
    self,
    info: strawberry.Info,
    account_id: str,
    first: int = 20,
    after: str | None = None,
  ) -> TAssistantPaperPage[TAssistantPaperExecution]:
    return await TAssistantPaperResolver.page(
      "executions", authorized_account_id(info, account_id), first=first, after=after
    )

  @strawberry.field(description="读取授权账户内指定的 PAPER 做 T 执行实例")
  async def t_assistant_paper_execution(
    self, info: strawberry.Info, account_id: str, execution_id: str
  ) -> TAssistantPaperExecution | None:
    return await TAssistantPaperResolver.execution(
      authorized_account_id(info, account_id), execution_id
    )

  @strawberry.field(description="分页读取指定 PAPER 做 T 执行的机会记录")
  async def t_assistant_paper_opportunities(
    self,
    info: strawberry.Info,
    account_id: str,
    execution_id: str,
    first: int = 20,
    after: str | None = None,
  ) -> TAssistantPaperPage[TAssistantPaperOpportunity]:
    return await TAssistantPaperResolver.page(
      "opportunities",
      authorized_account_id(info, account_id),
      execution_id,
      first,
      after,
    )

  @strawberry.field(description="分页读取指定 PAPER 做 T 执行的分配记录")
  async def t_assistant_paper_allocations(
    self,
    info: strawberry.Info,
    account_id: str,
    execution_id: str,
    first: int = 20,
    after: str | None = None,
  ) -> TAssistantPaperPage[TAssistantPaperAllocation]:
    return await TAssistantPaperResolver.page(
      "allocations", authorized_account_id(info, account_id), execution_id, first, after
    )

  @strawberry.field(description="分页读取指定 PAPER 做 T 执行的决策原因")
  async def t_assistant_paper_reasons(
    self,
    info: strawberry.Info,
    account_id: str,
    execution_id: str,
    first: int = 20,
    after: str | None = None,
  ) -> TAssistantPaperPage[TAssistantPaperReason]:
    return await TAssistantPaperResolver.page(
      "reasons", authorized_account_id(info, account_id), execution_id, first, after
    )

  @strawberry.field(description="分页读取指定 PAPER 做 T 执行的模拟订单")
  async def t_assistant_paper_orders(
    self,
    info: strawberry.Info,
    account_id: str,
    execution_id: str,
    first: int = 20,
    after: str | None = None,
  ) -> TAssistantPaperPage[TAssistantPaperOrder]:
    return await TAssistantPaperResolver.page(
      "orders", authorized_account_id(info, account_id), execution_id, first, after
    )

  @strawberry.field(description="分页读取指定 PAPER 做 T 执行的退出计划")
  async def t_assistant_paper_exit_plans(
    self,
    info: strawberry.Info,
    account_id: str,
    execution_id: str,
    first: int = 20,
    after: str | None = None,
  ) -> TAssistantPaperPage[TAssistantPaperExitPlan]:
    return await TAssistantPaperResolver.page(
      "exitPlans", authorized_account_id(info, account_id), execution_id, first, after
    )
