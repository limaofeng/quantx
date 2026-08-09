"""Redis-backed latest-quote cache populated by the Engine.

The cache is a transient read projection. PostgreSQL broker snapshots remain the
source of truth for positions and Redis failures must never block portfolio
reads.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, Iterable, List, Optional

from quantx_infrastructure.database.redis_pubsub import redis_pubsub
from quantx_infrastructure.models.tick import Tick

logger = logging.getLogger(__name__)

QUOTE_KEY_PREFIX = "market:latest-quote:"
QUOTE_TTL_SECONDS = 14 * 24 * 60 * 60
FLUSH_DELAY_SECONDS = 0.05


def _normalize_symbol(value: object) -> str:
  return str(value or "").strip().upper()


def _number(value: object, default: float = 0.0) -> float:
  try:
    return float(value or default)
  except (TypeError, ValueError):
    return default


class LatestMarketQuoteCache:
  """Batch writes Engine ticks and exposes one-roundtrip multi-symbol reads."""

  def __init__(self) -> None:
    self._pending: Dict[str, Tick] = {}
    self._flush_task: Optional[asyncio.Task] = None

  @staticmethod
  def _key(stock_code: str) -> str:
    return f"{QUOTE_KEY_PREFIX}{_normalize_symbol(stock_code)}"

  @staticmethod
  def _payload(tick: Tick) -> dict:
    tick_time = getattr(tick, "time", None)
    return {
      "stock_code": _normalize_symbol(getattr(tick, "stock_code", "")),
      "period": str(getattr(tick, "period", "tick") or "tick"),
      "time": tick_time.isoformat() if isinstance(tick_time, datetime) else str(tick_time),
      "last_price": _number(getattr(tick, "last_price", 0.0)),
      "open": _number(getattr(tick, "open", 0.0)),
      "high": _number(getattr(tick, "high", 0.0)),
      "low": _number(getattr(tick, "low", 0.0)),
      "last_close": _number(getattr(tick, "last_close", 0.0)),
      "amount": _number(getattr(tick, "amount", 0.0)),
      "volume": _number(getattr(tick, "volume", 0.0)),
      "pvolume": _number(getattr(tick, "pvolume", 0.0)),
      "tickvol": _number(getattr(tick, "tickvol", 0.0)),
      "stock_status": int(getattr(tick, "stock_status", 0) or 0),
      "open_int": int(getattr(tick, "open_int", 0) or 0),
      "last_settlement_price": _number(
        getattr(tick, "last_settlement_price", 0.0)
      ),
      "settlement_price": _number(getattr(tick, "settlement_price", 0.0)),
      "transaction_num": int(getattr(tick, "transaction_num", 0) or 0),
      "ask_price": list(getattr(tick, "ask_price", []) or []),
      "bid_price": list(getattr(tick, "bid_price", []) or []),
      "ask_vol": list(getattr(tick, "ask_vol", []) or []),
      "bid_vol": list(getattr(tick, "bid_vol", []) or []),
    }

  @staticmethod
  def _tick(payload: dict) -> Optional[Tick]:
    try:
      payload = dict(payload)
      payload["time"] = datetime.fromisoformat(
        str(payload["time"]).replace("Z", "+00:00")
      )
      return Tick(**payload)
    except (KeyError, TypeError, ValueError) as exc:
      logger.warning("Discarding invalid latest quote cache row: %s", exc)
      return None

  def stage_tick(self, tick: Tick) -> None:
    """Queue a tick for a short batched Redis flush without blocking callbacks."""
    stock_code = _normalize_symbol(getattr(tick, "stock_code", ""))
    if not stock_code:
      return
    self._pending[stock_code] = tick
    if self._flush_task is not None and not self._flush_task.done():
      return
    try:
      loop = asyncio.get_running_loop()
    except RuntimeError:
      return
    self._flush_task = loop.create_task(self._flush_after_delay())

  async def _flush_after_delay(self) -> None:
    await asyncio.sleep(FLUSH_DELAY_SECONDS)
    pending = self._pending
    self._pending = {}
    if not pending:
      return
    try:
      redis = await redis_pubsub.get_redis()
      async with redis.pipeline(transaction=False) as pipeline:
        for stock_code, tick in pending.items():
          await pipeline.set(
            self._key(stock_code),
            json.dumps(self._payload(tick), ensure_ascii=False),
            ex=QUOTE_TTL_SECONDS,
          )
        await pipeline.execute()
    except Exception as exc:
      logger.warning("Latest quote cache flush failed: %s", exc)

  async def get_ticks(self, stock_codes: Iterable[str]) -> List[Tick]:
    symbols = list(dict.fromkeys(
      _normalize_symbol(code) for code in stock_codes if _normalize_symbol(code)
    ))
    if not symbols:
      return []
    try:
      redis = await redis_pubsub.get_redis()
      values = await redis.mget([self._key(symbol) for symbol in symbols])
    except Exception as exc:
      logger.warning("Latest quote cache read failed: %s", exc)
      return []

    ticks: List[Tick] = []
    for raw in values:
      if raw is None:
        continue
      try:
        payload = json.loads(raw)
      except (json.JSONDecodeError, TypeError):
        continue
      tick = self._tick(payload)
      if tick is not None:
        ticks.append(tick)
    return ticks


latest_market_quote_cache = LatestMarketQuoteCache()
