"""上市公司公告与回购日更流程。"""

from typing import Any, Dict, List, Optional

from prefect import flow, get_run_logger
from quantx_infrastructure.core.utils import time_utils

from quantx_worker.prefector.tasks.announcement_tasks import (
  collect_disclosure_sync_symbols,
  sync_stock_disclosures_task,
)


@flow(
  name="上市公司公告与回购同步",
  description="同步持仓股/自选股的上市公司公告与股票回购事件",
  retries=1,
  retry_delay_seconds=300,
)
async def announcement_sync_flow(
  stock_codes: Optional[List[str]] = None,
  force: bool = False,
  limit: Optional[int] = None,
) -> Dict[str, Any]:
  logger = get_run_logger()
  started_at = time_utils.now()

  symbols = await collect_disclosure_sync_symbols(stock_codes, limit)

  logger.info(f"准备同步公告标的数: {len(symbols)}")
  if not symbols:
    return {
      "status": "skipped",
      "reason": "未找到持仓股或自选股",
      "started_at": started_at,
      "finished_at": time_utils.now(),
    }

  results = []
  for symbol in symbols:
    results.append(await sync_stock_disclosures_task(symbol, force=force))

  success_count = sum(1 for item in results if item.get("success"))
  failed = [item for item in results if not item.get("success")]
  finished_at = time_utils.now()
  status = "success" if not failed else "partial"

  return {
    "status": status,
    "started_at": started_at,
    "finished_at": finished_at,
    "total": len(results),
    "success": success_count,
    "failed": len(failed),
    "failed_symbols": [item.get("stock_code") for item in failed],
  }
