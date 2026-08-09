"""GraphQL resolvers for stock announcements."""

from quantx_infrastructure.services.announcement_sync_service import (
  AnnouncementSyncService,
)

from ..types.announcement_types import AnnouncementSyncStatus, StockDisclosureSummary


class AnnouncementResolver:
  @staticmethod
  async def get_stock_disclosure_summary(
    stock_code: str,
    limit: int = 20,
  ) -> StockDisclosureSummary:
    service = AnnouncementSyncService()
    summary = await service.get_summary(stock_code, limit=limit)
    return StockDisclosureSummary.from_data(summary)

  @staticmethod
  async def refresh_stock_disclosures(
    stock_code: str,
    force: bool = False,
  ) -> AnnouncementSyncStatus:
    service = AnnouncementSyncService()
    result = await service.refresh_stock_disclosures(stock_code, force=force)
    return AnnouncementSyncStatus.from_result(result)
