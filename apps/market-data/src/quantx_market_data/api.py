"""Historical control API: persist requests/actions and expose progress only."""

from __future__ import annotations

import hmac
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from quantx_contracts import PROTOCOL_VERSION
from quantx_contracts.market_data_service import (
  HistoryDemand,
  HistoryPage,
  HistoryRead,
  ResumeHistory,
)
from quantx_infrastructure.runtime_store import DurableRuntimeStore
from quantx_infrastructure.services.local_history_reader import (
  HistoryReadBusy,
  LocalHistoryReader,
)
from sqlalchemy import text


def create_app(*, store=None, token: str | None = None, reader=None) -> FastAPI:
  @asynccontextmanager
  async def lifespan(app):
    resolved_token = token or os.environ.get("QUANTX_MARKET_DATA_INTERNAL_TOKEN", "")
    if not resolved_token:
      raise RuntimeError("Market Data API requires an internal service token")
    app.state.token = resolved_token
    app.state.store = store if store is not None else DurableRuntimeStore()
    app.state.reader = reader if reader is not None else LocalHistoryReader()
    try:
      yield
    finally:
      if store is None:
        await app.state.store.close()
      if reader is None:
        from quantx_infrastructure.database.timeseries import shutdown_timeseries

        shutdown_timeseries()

  app = FastAPI(title="QuantX Market Data", lifespan=lifespan)

  async def authorize(authorization: str = Header(default="")):
    expected = "Bearer " + app.state.token
    if not hmac.compare_digest(authorization.encode(), expected.encode()):
      raise HTTPException(401, "Market data authentication required")

  @app.get("/health/live")
  async def live():
    return {"status": "alive", "service": "market-data-api"}

  @app.get("/health/ready", dependencies=[Depends(authorize)])
  async def ready():
    try:
      async with app.state.store.engine.connect() as connection:
        await connection.execute(
          text(
            "SELECT ingestion_progress, processing_worker_epoch FROM market_data_request LIMIT 0"
          )
        )
        await connection.execute(
          text("SELECT epoch FROM market_data_worker_lease LIMIT 0")
        )
    except Exception:
      raise HTTPException(503, "MARKET_DATA_STORAGE_UNAVAILABLE") from None
    return {"status": "ready", "capability": "request-storage"}

  @app.post(
    "/market-data/internal/v1/requests",
    status_code=202,
    dependencies=[Depends(authorize)],
  )
  async def submit(demand: HistoryDemand):
    try:
      identity = await app.state.store.create_market_data_request(
        demand.agent_payload(),
        idempotency_scope=f"market-data-demand-v1:{PROTOCOL_VERSION}:none",
      )
    except RuntimeError:
      raise HTTPException(503, "HISTORY_SOURCE_UNAVAILABLE") from None
    return {"request_id": identity}

  @app.get(
    "/market-data/internal/v1/history",
    response_model=HistoryPage,
    dependencies=[Depends(authorize)],
  )
  async def history(query: Annotated[HistoryRead, Query()]):
    try:
      return await app.state.reader.read(query)
    except HistoryReadBusy:
      raise HTTPException(
        429, "HISTORY_READ_CAPACITY", headers={"Retry-After": "1"}
      ) from None
    except Exception:
      raise HTTPException(503, "HISTORY_READ_UNAVAILABLE") from None

  @app.get(
    "/market-data/internal/v1/requests/{request_id}", dependencies=[Depends(authorize)]
  )
  async def status(request_id: str):
    value = await app.state.store.market_data_request(request_id)
    if value is None:
      raise HTTPException(404, "HISTORY_REQUEST_NOT_FOUND")
    progress = value.get("ingestion_progress") or {}
    return {
      "request_id": request_id,
      "status": value["status"],
      "phase": progress.get("phase"),
      "reason_code": progress.get("reason_code"),
      "attempt": progress.get("attempt", 0),
      "executions": progress.get("executions", 0),
      "next_retry_at": progress.get("next_retry_at"),
      "last_progress_at": progress.get("last_progress_at"),
      "observed_at": datetime.now(timezone.utc).isoformat(),
      "received_chunks": value.get("received_chunks", 0),
      "expected_chunks": value.get("expected_chunks"),
      "records_verified": (value.get("ingestion_result") or {}).get("records_verified"),
    }

  @app.post(
    "/market-data/internal/v1/requests/{request_id}/resume",
    dependencies=[Depends(authorize)],
  )
  async def resume(request_id: str, body: ResumeHistory):
    try:
      progress = await app.state.store.resume_blocked_market_data_request(
        request_id, reason=body.reason
      )
    except RuntimeError:
      raise HTTPException(409, "HISTORY_REQUEST_NOT_RESUMABLE") from None
    return {
      "request_id": request_id,
      "status": "UPLOADED",
      "attempt": progress["attempt"],
    }

  return app


app = create_app()
