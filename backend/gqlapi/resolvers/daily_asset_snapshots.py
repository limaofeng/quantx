from datetime import date
from typing import List, Optional

from services.daily_asset_snapshot_service import DailyAssetSnapshotService

from ..types.portfolio_types import DailyAssetSnapshot


class DailyAssetSnapshotResolver:
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
