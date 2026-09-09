from __future__ import annotations

from datetime import date

from fastapi import (
  APIRouter,
  Depends,
  HTTPException,
)
from fastapi.responses import FileResponse
from quantx_contracts.data_exchange import HistoryPartitionRequest
from quantx_infrastructure.services.data_exchange import (
  content_path,
  get_export,
  submit,
)

from .development_access import require_data_access

router = APIRouter(prefix="/market-data/v1")


@router.get("/reference/{instrument}", dependencies=[Depends(require_data_access)])
async def history_reference(instrument: str, as_of: date) -> dict:
  from quantx_infrastructure.services.data_exchange_reference import export_reference

  try:
    HistoryPartitionRequest(instrument=instrument, period="1d", trading_date=as_of)
    return await export_reference(instrument, as_of)
  except ValueError:
    raise HTTPException(422, "Reference data unavailable or invalid scope") from None


@router.get("/calendar/{year}", dependencies=[Depends(require_data_access)])
async def history_calendar(year: int) -> dict:
  from quantx_infrastructure.services.holiday_service import HolidayService

  if not 1990 <= year <= 2100:
    raise HTTPException(422, "Invalid calendar year")
  holidays = await HolidayService().get_holidays("SH", year)
  if not holidays:
    raise HTTPException(503, "Calendar unavailable")
  return {
    "year": year,
    "market": "SH",
    "holidays": [
      {"date": item.date.isoformat(), "description": item.description}
      for item in holidays
    ],
  }


@router.post("/history", dependencies=[Depends(require_data_access)], status_code=202)
async def request_history(request: HistoryPartitionRequest) -> dict:
  from datetime import datetime
  from zoneinfo import ZoneInfo

  current = datetime.now(ZoneInfo("Asia/Shanghai"))
  if request.trading_date > current.date() or (
    request.trading_date == current.date() and current.hour < 16
  ):
    raise HTTPException(422, "Only closed historical dates can be exported")
  try:
    return {"id": await submit(request)}
  except ValueError:
    raise HTTPException(429, "History queue capacity reached") from None


@router.get("/history/{identity}", dependencies=[Depends(require_data_access)])
async def history_status(identity: str) -> dict:
  result = await get_export(identity)
  if result is None:
    raise HTTPException(404, "Unknown export")
  return result


@router.post("/history/{identity}/retry", dependencies=[Depends(require_data_access)])
async def retry_history(identity: str) -> dict:
  from quantx_infrastructure.database.connection import AsyncSessionLocal
  from sqlalchemy import text

  async with AsyncSessionLocal() as db:
    result = await db.execute(
      text(
        "UPDATE development_data_export SET state='QUEUED',error=NULL WHERE id=:id AND state='INCOMPLETE'"
      ),
      {"id": identity},
    )
    await db.commit()
  return {"requeued": bool(result.rowcount)}


@router.get(
  "/history/{identity}/chunks/{digest}", dependencies=[Depends(require_data_access)]
)
async def history_chunk(identity: str, digest: str) -> FileResponse:
  from datetime import datetime, timezone

  result = await get_export(identity)
  if result is None:
    raise HTTPException(404, "Unknown export")
  if not result["expires_at"] or result["expires_at"] <= datetime.now(timezone.utc):
    raise HTTPException(410, "Export expired; submit again")
  if digest not in {
    item["checksum_sha256"] for item in (result["manifest"] or {}).get("chunks", [])
  }:
    raise HTTPException(404, "Unknown chunk")
  path = content_path(digest)
  if not path.is_file():
    raise HTTPException(410, "Export file unavailable; submit again")
  return FileResponse(
    path, media_type="application/gzip", headers={"Cache-Control": "private, no-store"}
  )
