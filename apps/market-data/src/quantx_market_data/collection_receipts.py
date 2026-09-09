"""History-scoped receipt ingress. Only the Worker can approve native work."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError
from quantx_contracts.collection_receipt import (
  CollectionReceipt,
  CollectionReceiptStatus,
)
from quantx_infrastructure.auth.agent_access import authenticate_agent_session
from quantx_infrastructure.auth.errors import AuthError
from quantx_infrastructure.config.settings import settings
from quantx_infrastructure.database.relational_connection import AsyncSessionLocal
from quantx_infrastructure.services.market_data_collection_receipt_store import (
  CollectionReceiptConflict,
  CollectionReceiptStore,
)
from sqlalchemy.exc import SQLAlchemyError

from .agent_upload import _bearer, _read_limited_body

router = APIRouter(prefix="/agent/market-data/collection", tags=["history-collection"])


async def _identity(request):
  async with AsyncSessionLocal() as db:
    try:
      session = await authenticate_agent_session(
        db, settings, token=_bearer(request), history=True
      )
      return session.device.id
    except AuthError as exc:
      raise HTTPException(exc.status_code, exc.message) from None


@router.post(
  "/{permit_id}/receipts", status_code=202, response_model=CollectionReceiptStatus
)
async def receive(permit_id: UUID, request: Request):
  try:
    device_id = await _identity(request)
    raw = await _read_limited_body(request, limit=4096)
    try:
      receipt = CollectionReceipt.model_validate_json(raw)
    except ValidationError:
      raise HTTPException(422, "COLLECTION_RECEIPT_INVALID") from None
    return await CollectionReceiptStore(request.app.state.store.engine).accept(
      permit_id=str(permit_id), device_id=device_id, receipt=receipt
    )
  except CollectionReceiptConflict:
    raise HTTPException(409, "COLLECTION_RECEIPT_CONFLICT") from None
  except KeyError:
    raise HTTPException(404, "COLLECTION_PERMIT_UNAVAILABLE") from None
  except SQLAlchemyError:
    raise HTTPException(503, "HISTORY_RECEIPT_STORAGE_UNAVAILABLE") from None


@router.get("/{permit_id}/receipts/{event}", response_model=CollectionReceiptStatus)
async def status(permit_id: UUID, event: Literal["START", "FINISH"], request: Request):
  try:
    device_id = await _identity(request)
    return await CollectionReceiptStore(request.app.state.store.engine).status(
      permit_id=str(permit_id), device_id=device_id, event=event
    )
  except KeyError:
    raise HTTPException(404, "COLLECTION_RECEIPT_UNAVAILABLE") from None
  except SQLAlchemyError:
    raise HTTPException(503, "HISTORY_RECEIPT_STORAGE_UNAVAILABLE") from None
