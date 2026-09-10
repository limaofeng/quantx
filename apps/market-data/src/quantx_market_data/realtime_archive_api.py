"""Authenticated bounded acceptance and read-only archive status."""

import asyncio
import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request
from pydantic import ValidationError
from quantx_contracts.realtime_archive import (
  MAX_ARCHIVE_REQUEST_BYTES,
  ArchiveAccepted,
  ArchiveRecoveryScope,
  ArchiveRevision,
  ArchiveStatus,
)
from quantx_infrastructure.services.realtime_archive_store import (
  ArchiveCapacity,
  ArchiveRejected,
  RealtimeArchiveStore,
)
from sqlalchemy.exc import SQLAlchemyError


async def authorize(request: Request, authorization: str = Header(default="")):
  if not hmac.compare_digest(
    authorization.encode(), ("Bearer " + request.app.state.token).encode()
  ):
    raise HTTPException(401, "Market data authentication required")


router = APIRouter(
  prefix="/market-data/internal/v1/archives", dependencies=[Depends(authorize)]
)


async def _body(request, model):
  body = bytearray()
  try:
    async with asyncio.timeout(3):
      async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_ARCHIVE_REQUEST_BYTES:
          raise HTTPException(413, "ARCHIVE_REQUEST_TOO_LARGE")
        body.extend(chunk)
    return model.model_validate_json(body)
  except (ValidationError, ValueError):
    raise HTTPException(422, "ARCHIVE_REQUEST_INVALID") from None
  except TimeoutError:
    raise HTTPException(408, "ARCHIVE_REQUEST_TIMEOUT") from None


@router.post("/scopes", status_code=202, response_model=ArchiveRecoveryScope)
async def register_scope(request: Request):
  value = await _body(request, ArchiveRecoveryScope)
  try:
    return await RealtimeArchiveStore(request.app.state.store.engine).register_scope(
      value
    )
  except ArchiveRejected as exc:
    raise HTTPException(409, str(exc)) from None
  except ArchiveCapacity:
    raise HTTPException(429, "ARCHIVE_SCOPE_CAPACITY") from None
  except (SQLAlchemyError, TimeoutError, RuntimeError):
    raise HTTPException(503, "ARCHIVE_STORAGE_UNAVAILABLE") from None


@router.post("", status_code=202, response_model=ArchiveAccepted)
async def submit(request: Request):
  value = await _body(request, ArchiveRevision)
  try:
    identity = await RealtimeArchiveStore(request.app.state.store.engine).submit(value)
  except ArchiveRejected as exc:
    raise HTTPException(409, str(exc)) from None
  except ArchiveCapacity:
    raise HTTPException(429, "ARCHIVE_PENDING_CAPACITY") from None
  except (SQLAlchemyError, TimeoutError):
    raise HTTPException(503, "ARCHIVE_STORAGE_UNAVAILABLE") from None
  return ArchiveAccepted(request_id=identity)


@router.get("/{request_id}", response_model=ArchiveStatus)
async def status(
  request: Request, request_id: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")]
):
  try:
    result = await RealtimeArchiveStore(request.app.state.store.engine).status(
      request_id
    )
  except (SQLAlchemyError, ValueError, TimeoutError):
    raise HTTPException(503, "ARCHIVE_STORAGE_UNAVAILABLE") from None
  if result is None:
    raise HTTPException(404, "ARCHIVE_REQUEST_NOT_FOUND")
  return result
