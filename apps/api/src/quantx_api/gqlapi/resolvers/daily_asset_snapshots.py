from datetime import date
from typing import List, Optional

from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.repositories.daily_asset_snapshot_repository import (
  DailyAssetSnapshotRepository,
)
from quantx_infrastructure.services.daily_asset_snapshot_service import (
  DailyAssetSnapshotService,
)

from ..types.common_types import PageInfo
from ..types.portfolio_types import DailyAssetSnapshot, DailyAssetSnapshotPage
from ..utils.cursor import decode_date_cursor, encode_cursor


class DailyAssetSnapshotResolver:
  @staticmethod
  async def get_daily_asset_snapshots_page(
    *,
    account_id: Optional[str] = None,
    strategy_run_id: Optional[str] = None,
    scope_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    first: int = 60,
    after: Optional[str] = None,
  ) -> DailyAssetSnapshotPage:
    cursor_date = None
    cursor_id = None
    if after:
      cursor_date, cursor_id = decode_date_cursor(after)
    async for db in get_async_db():
      rows, has_next_page = await DailyAssetSnapshotRepository(db).find_page(
        account_id=account_id,
        strategy_run_id=strategy_run_id,
        scope_type=scope_type,
        start_date=_parse_date(start_date),
        end_date=_parse_date(end_date),
        cursor_date=cursor_date,
        cursor_id=cursor_id,
        first=first,
      )
      cursors = [encode_cursor(row.trade_date, row.id) for row in rows]
      return DailyAssetSnapshotPage(
        items=[DailyAssetSnapshot.from_model(row) for row in rows],
        page_info=PageInfo(
          has_next_page=has_next_page,
          has_previous_page=bool(after),
          start_cursor=cursors[0] if cursors else None,
          end_cursor=cursors[-1] if cursors else None,
        ),
      )
    raise RuntimeError("资产快照数据库不可用")

  @staticmethod
  async def get_daily_asset_snapshots(
    account_id: Optional[str] = None,
    strategy_run_id: Optional[str] = None,
    scope_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 366,
  ) -> List[DailyAssetSnapshot]:
    service = DailyAssetSnapshotService()
    snapshots = await service.get_snapshots(
      account_id=account_id,
      strategy_run_id=strategy_run_id,
      scope_type=scope_type,
      start_date=_parse_date(start_date),
      end_date=_parse_date(end_date),
      limit=limit,
    )
    return [DailyAssetSnapshot.from_model(snapshot) for snapshot in snapshots]


def _parse_date(value: Optional[str]) -> Optional[date]:
  if not value:
    return None
  return date.fromisoformat(value)
