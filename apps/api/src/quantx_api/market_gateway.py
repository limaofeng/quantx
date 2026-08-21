"""Dedicated ASGI process for the QMT whole-market WebSocket."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from quantx_infrastructure.core.data.market_stream_transport import (
  market_stream_store,
)

from quantx_api.agent_api import market_agent_router


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


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
  return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
