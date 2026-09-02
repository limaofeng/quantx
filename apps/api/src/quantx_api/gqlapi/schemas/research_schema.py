"""GraphQL queries for finalized offline research results."""

from typing import Optional

import strawberry
from anyio import to_thread

from quantx_api.gqlapi.security import principal_from_context
from quantx_api.gqlapi.types.indicator_research_types import (
  IndicatorReportDetail,
  StockIndicatorReportMatch,
  StockIndicatorReportRequestInput,
)
from quantx_api.gqlapi.types.research_types import (
  ResearchLifecycleRun,
  ResearchLifecycleRunConnection,
  ResearchLifecycleRunFilter,
  ResearchRunDetail,
  ResearchRunPage,
  ResearchRunSummary,
)
from quantx_api.indicator_research_artifacts import IndicatorResearchArtifactStore
from quantx_api.research_artifacts import ResearchArtifactStore
from quantx_api.research_run_index import (
  list_research_lifecycle_runs,
  paginate_research_lifecycle_runs,
  validate_research_lifecycle_query,
)


@strawberry.type(description="离线指标研究结果查询")
class ResearchQuery:
  @strawberry.field(description="批量匹配单指标及当前条件交集的历史研究报告")
  async def stock_indicator_report_matches(
    self,
    requests: list[StockIndicatorReportRequestInput],
  ) -> list[StockIndicatorReportMatch]:
    records = await to_thread.run_sync(
      lambda: IndicatorResearchArtifactStore().match_reports(
        [item.as_request() for item in requests]
      )
    )
    return [StockIndicatorReportMatch.from_record(item) for item in records]

  @strawberry.field(description="只读打开一份指标研究报告；不会启动分析命令")
  async def indicator_report(
    self, run_key: str, report_id: str
  ) -> Optional[IndicatorReportDetail]:
    record = await to_thread.run_sync(
      lambda: IndicatorResearchArtifactStore().get_indicator_report(run_key, report_id)
    )
    return IndicatorReportDetail.from_record(record) if record is not None else None

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

  @strawberry.field(description="跨研究类型读取统一生命周期运行连接")
  async def research_lifecycle_runs(
    self,
    info: strawberry.types.Info,
    filter: Optional[ResearchLifecycleRunFilter] = None,
    limit: int = 50,
    offset: int = 0,
  ) -> ResearchLifecycleRunConnection:
    principal_from_context(info.context)
    query = validate_research_lifecycle_query(
      study_id=filter.study_id if filter is not None else None,
      stages=filter.stages if filter is not None else None,
      statuses=filter.statuses if filter is not None else None,
      date_from=filter.date_from if filter is not None else None,
      date_to=filter.date_to if filter is not None else None,
      search=filter.search if filter is not None else None,
      limit=limit,
      offset=offset,
    )
    records = await list_research_lifecycle_runs(query)
    items, total = paginate_research_lifecycle_runs(
      records,
      limit=limit,
      offset=offset,
    )
    return ResearchLifecycleRunConnection(
      items=[ResearchLifecycleRun.from_record(item) for item in items],
      total=total,
      limit=limit,
      offset=offset,
    )
