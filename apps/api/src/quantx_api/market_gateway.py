"""Dedicated ASGI process for the QMT whole-market WebSocket."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from quantx_contracts.market_health import (
  MARKET_GATEWAY_READINESS_TIMEOUT_SECONDS,
  MarketGatewayHealth,
  MarketHealthReason,
)
from quantx_infrastructure.core.data.market_stream_transport import (
  market_stream_store,
)
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.services.trading_time_service import TradingTimeService

from quantx_api.agent_api import active_market_stream_id, market_agent_router
from quantx_api.development_market_api import router as development_market_router

_trading_time = TradingTimeService()


@asynccontextmanager
async def lifespan(_: FastAPI):
  bridge = None
  if os.environ.get("ENV") == "development" and os.environ.get("QUANTX_MARKET_DATA_URL"):
    from quantx_infrastructure.services.development_market_bridge import run_bridge

    bridge = asyncio.create_task(run_bridge())
  try:
    yield
  finally:
    if bridge:
      bridge.cancel()
      await asyncio.gather(bridge, return_exceptions=True)
    await market_stream_store.close()


app = FastAPI(
  title="QuantX Market Gateway",
  docs_url=None,
  redoc_url=None,
  openapi_url=None,
  lifespan=lifespan,
)
app.include_router(market_agent_router)
app.include_router(development_market_router)


@app.get("/health/live")
async def health_live() -> dict[str, str]:
  return {"status": "alive", "component": "market-gateway"}


@app.get("/health/ready")
async def health_ready() -> JSONResponse:
  snapshot = await market_supply_health()
  return JSONResponse(
    status_code=200 if snapshot.status == "ready" else 503,
    content=snapshot.model_dump(mode="json", by_alias=True),
    headers={"Cache-Control": "no-store"},
  )


async def market_supply_health() -> MarketGatewayHealth:
  """Read bounded upstream facts; never ask Engine or account readiness."""
  stream_id = active_market_stream_id()
  remote_development = os.environ.get("ENV") == "development" and bool(os.environ.get("QUANTX_MARKET_DATA_URL"))
  if remote_development:
    remote_state = await market_stream_store.state()
    stream_id = remote_state.stream_id if remote_state and remote_state.status != "OFFLINE" else ""
  values = {
    "component": "market-gateway",
    "protocol": "quantx.market.v2",
    "connected_devices": int(bool(stream_id)),
    "sequence": 0,
    "instrument_count": 0,
    "universe_count": 0,
    "stream_age_seconds": None,
    "trading_session": None,
  }

  def unavailable(reason: MarketHealthReason) -> MarketGatewayHealth:
    return MarketGatewayHealth(status="not_ready", reason_code=reason, **values)

  if not stream_id:
    return unavailable(MarketHealthReason.STREAM_OFFLINE)
  deadline = (
    asyncio.get_running_loop().time() + MARKET_GATEWAY_READINESS_TIMEOUT_SECONDS
  )
  try:
    async with asyncio.timeout_at(deadline):
      trading = await _trading_time.is_trading_hours("SH", time_utils.now())
  except Exception:
    return unavailable(MarketHealthReason.CALENDAR_UNAVAILABLE)
  values["trading_session"] = trading
  # Read the expiring lease last, so a slow calendar read cannot preserve it.
  try:
    async with asyncio.timeout_at(deadline):
      state, lease = await market_stream_store.state_with_freshness()
  except Exception:
    return unavailable(MarketHealthReason.REDIS_UNAVAILABLE)
  if not remote_development and active_market_stream_id() != stream_id:
    values["connected_devices"] = int(bool(active_market_stream_id()))
    return unavailable(MarketHealthReason.STREAM_OFFLINE)
  if state is None or state.stream_id != stream_id or state.status == "OFFLINE":
    return unavailable(MarketHealthReason.STREAM_OFFLINE)
  age = (
    max(0.0, (datetime.now(timezone.utc) - state.updated_at).total_seconds())
    if state.updated_at is not None
    else None
  )
  values.update(
    sequence=state.sequence,
    instrument_count=state.instrument_count,
    universe_count=state.universe_count,
    stream_age_seconds=age,
  )
  if state.status != "READY" or state.sequence < 3 or state.commit_phase != "IDLE":
    return unavailable(MarketHealthReason.STREAM_SYNCING)
  if (
    state.universe_count <= 0
    or age is None
    or not 0.99 * state.universe_count <= state.instrument_count <= state.universe_count
  ):
    return unavailable(MarketHealthReason.SNAPSHOT_INCOMPLETE)
  if trading and (
    lease is None
    or lease.stream_id != state.stream_id
    or lease.sequence != state.sequence
  ):
    return unavailable(MarketHealthReason.STREAM_STALE)
  return MarketGatewayHealth(status="ready", reason_code=None, **values)


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
  return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/internal/development-data/health", include_in_schema=False)
async def development_data_health(request: Request) -> JSONResponse:
  if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
    raise HTTPException(403, "Local observation only")
  from quantx_infrastructure.database.connection import AsyncSessionLocal
  from sqlalchemy import text

  async with AsyncSessionLocal() as db:
    counts = dict((await db.execute(text("SELECT state,count(*) FROM development_data_export GROUP BY state"))).all())
  healthy = not counts.get("INCOMPLETE", 0)
  if os.environ.get("ENV") == "development" and os.environ.get("QUANTX_MARKET_DATA_URL"):
    state, lease = await market_stream_store.state_with_freshness()
    healthy = healthy and bool(state and state.status == "READY" and lease and lease.stream_id == state.stream_id and lease.sequence == state.sequence)
  return JSONResponse(status_code=200 if healthy else 503,
    content={"status": "healthy" if healthy else "degraded", "partitions": counts},
    headers={"Cache-Control": "no-store"})
