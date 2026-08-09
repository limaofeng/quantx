"""GraphQL queries for finalized offline research results."""

from typing import Optional

import strawberry
from anyio import to_thread

from quantx_api.gqlapi.types.research_types import (
  ResearchRunDetail,
  ResearchRunPage,
  ResearchRunSummary,
)
from quantx_api.research_artifacts import ResearchArtifactStore


@strawberry.type(description="离线因子研究结果查询")
class ResearchQuery:
  @strawberry.field(description="分页列出已完成的研究运行")
  async def research_runs(
    self,
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
  ) -> ResearchRunPage:
    items, total = await to_thread.run_sync(
      lambda: ResearchArtifactStore().list_runs(
        limit=limit,
        offset=offset,
        status=status,
      )
    )
    return ResearchRunPage(
      items=[ResearchRunSummary.from_record(item) for item in items],
      total=total,
      limit=limit,
      offset=offset,
    )

  @strawberry.field(description="按不透明稳定 key 获取一次研究运行详情")
  async def research_run(self, key: str) -> Optional[ResearchRunDetail]:
    record = await to_thread.run_sync(lambda: ResearchArtifactStore().get_run(key))
    return ResearchRunDetail.from_record(record) if record is not None else None
