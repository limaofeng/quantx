import strawberry

from quantx_api.gqlapi.resolvers.announcements import AnnouncementResolver
from quantx_api.gqlapi.types.announcement_types import (
  AnnouncementSyncStatus,
  StockDisclosureSummary,
)


@strawberry.type(description="上市公司公告查询")
class AnnouncementQuery:
  @strawberry.field(description="获取单票公告与回购摘要")
  async def stock_disclosure_summary(
    self,
    stock_code: str,
    limit: int = 20,
  ) -> StockDisclosureSummary:
    return await AnnouncementResolver.get_stock_disclosure_summary(
      stock_code=stock_code,
      limit=limit,
    )


@strawberry.type(description="上市公司公告同步")
class AnnouncementMutation:
  @strawberry.mutation(description="刷新单票公告与回购数据")
  async def refresh_stock_disclosures(
    self,
    stock_code: str,
    force: bool = False,
  ) -> AnnouncementSyncStatus:
    return await AnnouncementResolver.refresh_stock_disclosures(
      stock_code=stock_code,
      force=force,
    )
