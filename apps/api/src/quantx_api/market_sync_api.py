"""Authenticated, paged market-sync evidence, including post-Flow convergence."""

from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from quantx_contracts.market_data_service import ResumeHistory
from quantx_infrastructure.auth.errors import AuthError
from quantx_infrastructure.services.local_market_data_client import (
  LocalMarketDataClient,
)
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


@router.post("/{run_id}/partitions/{request_id}/resume")
async def resume_partition(
  run_id: UUID,
  request_id: UUID,
  body: ResumeHistory,
  principal: Principal = Depends(_principal),
):
  try:
    principal.require_permission("operations:write")
  except AuthError as exc:
    raise HTTPException(403, "无权恢复同步请求") from exc
  if principal.is_native_session:
    raise HTTPException(403, "同步恢复仅供网页端操作")
  audit = MarketDataSyncAudit(str(run_id))
  try:
    if not await audit.contains_request(str(request_id)):
      raise HTTPException(404, "请求不属于当前同步任务")
  finally:
    await audit.close()
  client = None
  try:
    client = LocalMarketDataClient()
    return await client.resume_market_data_request(str(request_id), reason=body.reason)
  except httpx.HTTPStatusError as exc:
    if exc.response.status_code in {404, 409, 422}:
      raise HTTPException(409, "请求当前不可恢复，请刷新状态") from None
    raise HTTPException(503, "恢复结果未确认，请刷新状态后再操作") from None
  except (httpx.RequestError, ValueError, RuntimeError):
    raise HTTPException(503, "恢复结果未确认，请刷新状态后再操作") from None
  finally:
    if client is not None:
      await client.close()
