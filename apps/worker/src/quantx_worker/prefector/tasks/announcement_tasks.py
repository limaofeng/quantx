"""上市公司公告与回购 Prefect tasks。"""

from typing import Any, Dict, List, Optional

from prefect import get_run_logger, task
from quantx_infrastructure.database.connection import get_async_db
from quantx_infrastructure.models.position import Position
from quantx_infrastructure.models.watchlist_item import WatchlistItem
from quantx_infrastructure.services.announcement_provider import unique_stock_codes
from quantx_infrastructure.services.announcement_sync_service import (
  AnnouncementSyncService,
)
from sqlalchemy import select


@task(
  name="收集公告同步标的",
  description="从显式参数、持仓和自选股中收集需要同步公告/回购的 A 股代码",
  retries=1,
  retry_delay_seconds=15,
)
async def collect_disclosure_sync_symbols(
  stock_codes: Optional[List[str]] = None,
  limit: Optional[int] = None,
) -> List[str]:
  symbols = (
    unique_stock_codes(stock_codes)
    if stock_codes is not None
    else await _load_portfolio_and_watchlist_symbols()
  )
  if limit:
    symbols = symbols[: max(1, int(limit))]
  return symbols


@task(
  name="同步单票公告与回购",
  description="调用 AkShare 同步单个标的的上市公司公告和股票回购事件",
  retries=1,
  retry_delay_seconds=120,
)
async def sync_stock_disclosures_task(
  stock_code: str,
  force: bool = False,
) -> Dict[str, Any]:
  logger = get_run_logger()
  logger.info(f"同步公告与回购: {stock_code}")

  result = await AnnouncementSyncService().refresh_stock_disclosures(
    stock_code,
    force=force,
  )
  return {
    "announcement_count": result.announcement_count,
    "error_message": result.error_message,
    "finished_at": result.finished_at,
    "message": result.message,
    "repurchase_count": result.repurchase_count,
    "source_status": result.source_status,
    "started_at": result.started_at,
    "stock_code": result.stock_code,
    "success": result.success,
  }


async def _load_portfolio_and_watchlist_symbols() -> List[str]:
  values: List[str] = []
  async for db in get_async_db():
    position_result = await db.execute(select(Position.stock_code))
    watchlist_result = await db.execute(select(WatchlistItem.stock_code))
    values.extend(str(item) for item in position_result.scalars().all() if item)
    values.extend(str(item) for item in watchlist_result.scalars().all() if item)
    break
  return unique_stock_codes(values)
