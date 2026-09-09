"""Authenticated, paged market-sync evidence, including post-Flow convergence."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from quantx_infrastructure.auth.errors import AuthError
from quantx_infrastructure.services.market_data_sync_audit import MarketDataSyncAudit

from quantx_api.auth.principal import Principal
from quantx_api.auth.router import _principal

router = APIRouter(prefix="/market-data-sync")


@router.get("/{run_id}/partitions")
async def partitions(
  run_id: UUID,
  offset: int = Query(default=0, ge=0),
  limit: int = Query(default=50, ge=1, le=200),
  principal: Principal = Depends(_principal),
):
  try:
    principal.require_permission("system-status:read")
  except AuthError as exc:
    raise HTTPException(status_code=403, detail="无权查看同步运行") from exc
  if principal.is_native_session:
    raise HTTPException(status_code=403, detail="同步运行证据仅供网页端查看")
  audit = MarketDataSyncAudit(str(run_id))
  try:
    return {
      "run_id": str(run_id),
      "offset": offset,
      "limit": limit,
      "counts": await audit.counts(),
      "items": await audit.page(offset=offset, limit=limit),
    }
  finally:
    await audit.close()
