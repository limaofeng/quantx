"""Dedicated ASGI process for the QMT whole-market WebSocket."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from quantx_contracts.market_health import MarketGatewayHealth, MarketHealthReason
from quantx_infrastructure.core.data.market_stream_transport import (
  market_stream_store,
)
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.services.trading_time_service import TradingTimeService

from quantx_api.agent_api import active_market_stream_id, market_agent_router

MARKET_GATEWAY_READINESS_TIMEOUT_SECONDS = 1.0
_trading_time = TradingTimeService()


@asynccontextmanager
async def lifespan(_: FastAPI):
  try:
    yield
  finally:
    await market_stream_store.close()


app = FastAPI(
  title="QuantX Market Gateway",
  docs_url=None,
  redoc_url=None,
  openapi_url=None,
  lifespan=lifespan,
)
app.include_router(market_agent_router)


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
  values = {
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
  try:
    trading = await asyncio.wait_for(
      _trading_time.is_trading_hours("SH", time_utils.now()),
      timeout=MARKET_GATEWAY_READINESS_TIMEOUT_SECONDS,
    )
  except Exception:
    return unavailable(MarketHealthReason.CALENDAR_UNAVAILABLE)
  values["trading_session"] = trading
  # Read the expiring lease last, so a slow calendar read cannot preserve it.
  try:
    state, lease = await asyncio.wait_for(
      market_stream_store.state_with_freshness(),
      timeout=MARKET_GATEWAY_READINESS_TIMEOUT_SECONDS,
    )
  except Exception:
    return unavailable(MarketHealthReason.REDIS_UNAVAILABLE)
  if active_market_stream_id() != stream_id:
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
