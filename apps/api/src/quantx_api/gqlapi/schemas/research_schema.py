"""GraphQL queries for finalized offline research results."""

from typing import Optional

import strawberry
from anyio import to_thread

from quantx_api.factor_research_artifacts import FactorResearchArtifactStore
from quantx_api.gqlapi.types.factor_research_types import (
  FactorReportDetail,
  StockFactorReportMatch,
  StockFactorReportRequestInput,
)
from quantx_api.gqlapi.types.research_types import (
  ResearchRunDetail,
  ResearchRunPage,
  ResearchRunSummary,
)
from quantx_api.research_artifacts import ResearchArtifactStore


@strawberry.type(description="离线因子研究结果查询")
class ResearchQuery:
  @strawberry.field(description="批量匹配单因子及当前条件交集的历史研究报告")
  async def stock_factor_report_matches(
    self, requests: list[StockFactorReportRequestInput],
  ) -> list[StockFactorReportMatch]:
    records = await to_thread.run_sync(
      lambda: FactorResearchArtifactStore().match_reports([item.as_request() for item in requests])
    )
    return [StockFactorReportMatch.from_record(item) for item in records]

  @strawberry.field(description="只读打开一份因子研究报告；不会启动分析命令")
  async def factor_report(self, run_key: str, report_id: str) -> Optional[FactorReportDetail]:
    record = await to_thread.run_sync(
      lambda: FactorResearchArtifactStore().get_factor_report(run_key, report_id)
    )
    return FactorReportDetail.from_record(record) if record is not None else None

  @strawberry.field(description="分页列出已完成的研究运行")
  async def research_runs(
    self,
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
    study_id: Optional[str] = None,
  ) -> ResearchRunPage:
    items, total = await to_thread.run_sync(
      lambda: ResearchArtifactStore().list_runs(
        limit=limit,
        offset=offset,
        status=status,
        study_id=study_id,
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
