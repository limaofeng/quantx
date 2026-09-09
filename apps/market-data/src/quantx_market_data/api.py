"""Historical control API: persist requests/actions and expose progress only."""

from __future__ import annotations

import hmac
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query
from fastapi.responses import Response
from quantx_contracts.daily_snapshot_read import DailySnapshotRead, DailySnapshotResult
from quantx_contracts.divid_factor_read import DividFactorRead, DividFactorWindow
from quantx_contracts.history_collection_api import (
  MAX_HISTORY_RESULT_BYTES,
  HistoryCollectionAccepted,
  HistoryCollectionResult,
  HistoryCollectionSubmission,
)
from quantx_contracts.instrument_details import (
  INSTRUMENT_CODE_PATTERN,
  InstrumentDetailSnapshot,
)
from quantx_contracts.market_data_service import (
  HistoryDemand,
  HistoryDemandAccepted,
  HistoryDemandStatus,
  HistoryPage,
  HistoryRead,
  ResumeHistory,
)
from quantx_infrastructure.services.local_divid_factor_reader import (
  LocalDividFactorReader,
)
from quantx_infrastructure.services.local_history_reader import (
  HistoryReadBusy,
  LocalHistoryReader,
  PublishedHistoryReader,
)
from quantx_infrastructure.services.market_data_demand_store import (
  MarketDataDemandCapacity,
  MarketDataDemandStore,
)
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker


def create_app(*, store=None, token: str | None = None, reader=None) -> FastAPI:
  @asynccontextmanager
  async def lifespan(app):
    resolved_token = token or os.environ.get("QUANTX_MARKET_DATA_INTERNAL_TOKEN", "")
    if not resolved_token or (
      token is None and (len(resolved_token) < 32 or "CHANGE_ME" in resolved_token)
    ):
      raise RuntimeError("Market Data API requires an internal service token")
    app.state.token = resolved_token
    app.state.store = store if store is not None else MarketDataDemandStore()
    from quantx_infrastructure.config.settings import settings

    app.state.reader = (
      reader
      if reader is not None
      else PublishedHistoryReader(async_sessionmaker(app.state.store.engine))
      if settings.environment == "development"
      else LocalHistoryReader()
    )
    app.state.factor_reader = LocalDividFactorReader(
      getattr(app.state.store, "engine", None)
    )
    try:
      yield
    finally:
      if store is None:
        await app.state.store.close()
        from quantx_infrastructure.database.relational_connection import close_database

        await close_database()
      if reader is None:
        from quantx_infrastructure.database.timeseries import shutdown_timeseries

        shutdown_timeseries()

  app = FastAPI(title="QuantX Market Data", lifespan=lifespan)
  from .agent_upload import agent_router

  app.include_router(agent_router)
  from .collection_receipts import router as collection_receipts_router

  app.include_router(collection_receipts_router)
  from .history_session import router as history_session_router

  app.include_router(history_session_router)
  from .development_history import router as development_history_router

  app.include_router(development_history_router)

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
        await connection.execute(
          text("SELECT demand_id FROM market_data_demand LIMIT 0")
        )
    except Exception:
      raise HTTPException(503, "MARKET_DATA_STORAGE_UNAVAILABLE") from None
    return {"status": "ready", "capability": "request-storage"}

  @app.get("/health/worker", dependencies=[Depends(authorize)])
  async def worker_health():
    try:
      async with app.state.store.engine.connect() as connection:
        epoch = await connection.scalar(
          text("""
          SELECT epoch FROM market_data_worker_lease
          WHERE id=1 AND expires_at > clock_timestamp()
        """)
        )
    except SQLAlchemyError:
      raise HTTPException(503, "MARKET_DATA_STORAGE_UNAVAILABLE") from None
    if epoch is None:
      raise HTTPException(503, "MARKET_DATA_WORKER_UNAVAILABLE")
    return {"status": "ready", "capability": "worker-lease", "epoch": epoch}

  @app.get(
    "/market-data/internal/v1/reference/divid-factors",
    response_model=DividFactorWindow,
    dependencies=[Depends(authorize)],
  )
  async def factor_window(request: Annotated[DividFactorRead, Query()]):
    try:
      return await app.state.factor_reader.read(request)
    except HistoryReadBusy:
      raise HTTPException(429, "FACTOR_READ_CAPACITY") from None
    except (ValueError, TimeoutError, SQLAlchemyError):
      raise HTTPException(503, "FACTOR_READ_UNAVAILABLE") from None

  @app.post(
    "/market-data/internal/v1/demands",
    status_code=202,
    response_model=HistoryDemandAccepted,
    dependencies=[Depends(authorize)],
  )
  async def submit(demand: HistoryDemand):
    try:
      identity = await app.state.store.submit_history_demand(demand)
    except MarketDataDemandCapacity:
      raise HTTPException(429, "HISTORY_DEMAND_CAPACITY") from None
    except SQLAlchemyError:
      raise HTTPException(503, "MARKET_DATA_STORAGE_UNAVAILABLE") from None
    return {"demand_id": identity}

  @app.get(
    "/market-data/internal/v1/demands/{demand_id}",
    response_model=HistoryDemandStatus,
    dependencies=[Depends(authorize)],
  )
  async def demand_status(demand_id: str):
    try:
      value = await app.state.store.history_demand(demand_id)
    except SQLAlchemyError:
      raise HTTPException(503, "MARKET_DATA_STORAGE_UNAVAILABLE") from None
    if value is None:
      raise HTTPException(404, "HISTORY_DEMAND_NOT_FOUND")
    return value

  @app.post(
    "/market-data/internal/v1/history/latest-daily",
    response_model=DailySnapshotResult,
    dependencies=[Depends(authorize)],
  )
  async def latest_daily(request: DailySnapshotRead):
    try:
      return await app.state.reader.read_latest_daily(request)
    except HistoryReadBusy:
      raise HTTPException(
        429, "HISTORY_READ_CAPACITY", headers={"Retry-After": "1"}
      ) from None
    except Exception:
      raise HTTPException(503, "HISTORY_READ_UNAVAILABLE") from None

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
    "/market-data/internal/v1/requests/{request_id}/instruments/{code}",
    dependencies=[Depends(authorize)],
    response_model=InstrumentDetailSnapshot,
  )
  async def instrument_snapshot(
    request_id: Annotated[str, Path(min_length=1, max_length=36)],
    code: Annotated[str, Path(pattern=INSTRUMENT_CODE_PATTERN)],
  ):
    from quantx_infrastructure.services.instrument_detail_ingestion import (
      read_instrument_detail,
    )

    try:
      result = await read_instrument_detail(app.state.store.engine, request_id, code)
    except (ValueError, SQLAlchemyError):
      raise HTTPException(503, "INSTRUMENT_SNAPSHOT_UNAVAILABLE") from None
    if result is None:
      raise HTTPException(404, "INSTRUMENT_SNAPSHOT_NOT_VERIFIED")
    return result

  @app.post(
    "/market-data/internal/v1/requests",
    status_code=202,
    response_model=HistoryCollectionAccepted,
    dependencies=[Depends(authorize)],
  )
  async def collect(request: HistoryCollectionSubmission):
    try:
      identity = await app.state.store.create_market_data_request(
        request.payload,
        device_id=str(request.device_id) if request.device_id else None,
        required_capabilities=request.required_capabilities,
        idempotency_scope=request.idempotency_scope,
      )
    except ValueError:
      raise HTTPException(422, "HISTORY_REQUEST_INVALID") from None
    except (RuntimeError, SQLAlchemyError):
      raise HTTPException(503, "HISTORY_REQUEST_UNAVAILABLE") from None
    return {"request_id": identity}

  @app.get(
    "/market-data/internal/v1/requests/{request_id}/result",
    dependencies=[Depends(authorize)],
  )
  async def collection_result(request_id: str):
    value = await app.state.store.market_data_request(request_id)
    if value is None:
      raise HTTPException(404, "HISTORY_REQUEST_NOT_FOUND")
    if (
      value["status"] != "COMPLETED"
      or (value.get("ingestion_progress") or {}).get("phase") != "VERIFIED"
    ):
      raise HTTPException(409, "HISTORY_RESULT_NOT_VERIFIED")
    try:
      result = HistoryCollectionResult(
        request_id=request_id, result=value["ingestion_result"]
      )
      encoded = json.dumps(
        result.model_dump(mode="json"), allow_nan=False, separators=(",", ":")
      ).encode()
      if len(encoded) > MAX_HISTORY_RESULT_BYTES:
        raise ValueError("result too large")
    except (KeyError, ValueError):
      raise HTTPException(503, "HISTORY_RESULT_UNAVAILABLE") from None
    return Response(encoded, media_type="application/json")

  @app.get(
    "/market-data/internal/v1/requests/{request_id}", dependencies=[Depends(authorize)]
  )
  async def status(request_id: str):
    value = await app.state.store.market_data_request(request_id)
    if value is None:
      raise HTTPException(404, "HISTORY_REQUEST_NOT_FOUND")
    progress = value.get("ingestion_progress") or {}
    reason_code = progress.get("reason_code")
    if value["status"] == "FAILED":
      try:
        failure = json.loads(value.get("processing_error") or "null")
        code = failure["reason_code"]
        if isinstance(code, str) and code in {
          "XTDATA_UNAVAILABLE",
          "COLLECTION_RESULT_INVALID",
          "COLLECTION_NATIVE_FAILED",
          "DATA_UNAVAILABLE",
        }:
          reason_code = code
      except (ValueError, TypeError, KeyError):
        pass
    return {
      "request_id": request_id,
      "status": value["status"],
      "phase": progress.get("phase"),
      "reason_code": reason_code,
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
      progress = await app.state.store.resume_market_data_request(
        request_id, reason=body.reason
      )
    except RuntimeError:
      raise HTTPException(409, "HISTORY_REQUEST_NOT_RESUMABLE") from None
    return {
      "request_id": request_id,
      "status": progress["status"],
      "attempt": progress["attempt"],
    }

  return app


app = create_app()
