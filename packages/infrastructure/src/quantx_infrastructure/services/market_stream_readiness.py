"""Authoritative whole-market readiness semantics shared by safety and health."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from typing import Any

from quantx_infrastructure.core.data.market_stream_transport import market_stream_store
from quantx_infrastructure.core.utils import time_utils
from quantx_infrastructure.services.trading_time_service import TradingTimeService

_AUTHORITY_TIMEOUT_SECONDS = 2.0


class MarketStreamReadinessStatus(str, Enum):
  PASSED = "PASSED"
  STANDBY = "STANDBY"
  FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class MarketStreamReadiness:
  status: MarketStreamReadinessStatus
  message: str
  converged: bool
  freshness_current: bool
  trading_session: bool
  stream_state: Any | None = None
  freshness_lease: Any | None = None
  engine_state: Any | None = None
  error_type: str | None = None

  @property
  def tradable_now(self) -> bool:
    return self.status is MarketStreamReadinessStatus.PASSED


def classify_authoritative_market_stream_readiness(
  *,
  stream_state: Any | None,
  freshness_lease: Any | None,
  engine_state: Any | None,
  trading_session: bool,
) -> MarketStreamReadiness:
  """Classify convergence separately from the trading-session freshness lease."""

  failure = ""
  if stream_state is None:
    failure = "API 全市场行情权威状态缺失"
  elif str(stream_state.status).upper() != "READY":
    failure = f"API 全市场行情状态为 {str(stream_state.status).upper()}"
  elif str(stream_state.commit_phase).upper() != "IDLE":
    failure = f"API 全市场行情提交阶段为 {str(stream_state.commit_phase).upper()}"
  elif int(stream_state.sequence) < 3:
    failure = "全市场行情尚未完成 sequence 3 权威就绪确认"
  elif engine_state is None:
    failure = "Engine 全市场行情水位缺失"
  elif str(engine_state.status).upper() != "READY":
    failure = f"Engine 全市场行情状态为 {str(engine_state.status).upper()}"
  elif str(engine_state.stream_id) != str(stream_state.stream_id):
    failure = "API 与 Engine 全市场行情 stream 水位不一致"
  elif int(engine_state.sequence) != int(stream_state.sequence):
    failure = "API 与 Engine 全市场行情 sequence 水位不一致"

  freshness_current = bool(
    stream_state is not None
    and freshness_lease is not None
    and str(freshness_lease.stream_id) == str(stream_state.stream_id)
    and int(freshness_lease.sequence) == int(stream_state.sequence)
  )
  if failure:
    return MarketStreamReadiness(
      status=MarketStreamReadinessStatus.FAILED,
      message=failure,
      converged=False,
      freshness_current=freshness_current,
      trading_session=trading_session,
      stream_state=stream_state,
      freshness_lease=freshness_lease,
      engine_state=engine_state,
    )
  if not trading_session:
    return MarketStreamReadiness(
      status=MarketStreamReadinessStatus.STANDBY,
      message="当前休市，Agent、API 与 Engine 权威水位已收敛，等待下一交易时段",
      converged=True,
      freshness_current=freshness_current,
      trading_session=False,
      stream_state=stream_state,
      freshness_lease=freshness_lease,
      engine_state=engine_state,
    )
  if not freshness_current:
    return MarketStreamReadiness(
      status=MarketStreamReadinessStatus.FAILED,
      message="交易时段全市场行情新鲜度租约缺失或水位不一致",
      converged=True,
      freshness_current=False,
      trading_session=True,
      stream_state=stream_state,
      freshness_lease=freshness_lease,
      engine_state=engine_state,
    )
  return MarketStreamReadiness(
    status=MarketStreamReadinessStatus.PASSED,
    message="",
    converged=True,
    freshness_current=True,
    trading_session=True,
    stream_state=stream_state,
    freshness_lease=freshness_lease,
    engine_state=engine_state,
  )


async def authoritative_market_stream_readiness() -> MarketStreamReadiness:
  """Read and classify the current API, Engine and market-session authority."""

  async def read_authority():
    return await asyncio.gather(
      market_stream_store.state_with_freshness(),
      market_stream_store.engine_state(),
      TradingTimeService().is_trading_hours("SH", time_utils.now()),
    )

  try:
    (stream_state, freshness_lease), engine_state, trading_session = (
      await asyncio.wait_for(
        read_authority(),
        timeout=_AUTHORITY_TIMEOUT_SECONDS,
      )
    )
  except Exception as exc:
    return MarketStreamReadiness(
      status=MarketStreamReadinessStatus.FAILED,
      message="无法读取全市场行情权威状态",
      converged=False,
      freshness_current=False,
      trading_session=False,
      error_type=exc.__class__.__name__,
    )
  return classify_authoritative_market_stream_readiness(
    stream_state=stream_state,
    freshness_lease=freshness_lease,
    engine_state=engine_state,
    trading_session=bool(trading_session),
  )


async def authoritative_market_stream_tradable() -> bool:
  """Require convergence, an active trading session and a current lease."""

  return (await authoritative_market_stream_readiness()).tradable_now


__all__ = [
  "MarketStreamReadiness",
  "MarketStreamReadinessStatus",
  "authoritative_market_stream_readiness",
  "authoritative_market_stream_tradable",
  "classify_authoritative_market_stream_readiness",
]
