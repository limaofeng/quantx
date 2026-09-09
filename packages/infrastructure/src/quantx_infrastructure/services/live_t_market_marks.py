"""Source-bound LIVE valuation marks from the ordered hub and persisted close ticks."""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from quantx_application.t_trade_v3.daily_t_valuation import TValuationMark
from quantx_application.t_trade_v3.portfolio_reference import aware_time

from quantx_infrastructure.core.data.tick_identity import tick_storage_time

_EXCHANGE = ZoneInfo("Asia/Shanghai")
_CLOSE_LIMIT = 1000


def _positive_integer(value):
  if type(value) is not int or value <= 0:
    raise ValueError("LIVE_MARK_SOURCE_IDENTITY_REQUIRED")
  return value


def _price(value):
  if isinstance(value, bool):
    raise ValueError("LIVE_MARK_PRICE_INVALID")
  try:
    price = Decimal(str(value))
  except (InvalidOperation, ValueError) as exc:
    raise ValueError("LIVE_MARK_PRICE_INVALID") from exc
  if not price.is_finite() or price <= 0:
    raise ValueError("LIVE_MARK_PRICE_INVALID")
  return price


def _source_id(kind, material):
  digest = hashlib.sha256(
    json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
  ).hexdigest()
  return f"{kind}:{digest}"


@dataclass(frozen=True)
class LiveTMarketMarks:
  as_of: datetime
  stream_id: str
  generation: int
  sequence: int
  current: dict[str, TValuationMark]
  opening: dict[str, TValuationMark]


class LiveTMarketMarkReader:
  """No last-close field, Redis cache, or broker snapshot price fallback.

  Missing opening data is returned as missing: the valuation reader knows which
  batches actually carried inventory overnight and requires marks for those only.
  The frozen calendar supplies the previous trading day, never weekday arithmetic.
  """

  def __init__(self, quote_hub, *, tick_repository=None):
    self.hub = quote_hub
    self.tick_repository = tick_repository

  def _read_close(self, code, start, end):
    repository = self.tick_repository
    if repository is None:
      from quantx_infrastructure.repositories.tick_repository import TickRepository

      repository = TickRepository()
    return repository.find_source_identity_page(
      stock_code=code, start_time=start, end_time=end, limit=_CLOSE_LIMIT
    )

  async def read(
    self, *, as_of, previous_trading_day, current_codes, opening_codes, max_age_seconds
  ):
    cut = aware_time(as_of).astimezone(UTC)
    if (
      type(previous_trading_day) is not date
      or previous_trading_day >= cut.astimezone(_EXCHANGE).date()
      or type(max_age_seconds) is not int
      or not 0 < max_age_seconds <= 90
    ):
      raise ValueError("LIVE_MARK_DAY_OR_FRESHNESS_INVALID")
    current_codes, opening_codes = (
      tuple(sorted(set(current_codes))),
      tuple(sorted(set(opening_codes))),
    )
    if any(
      not isinstance(code, str) or not code for code in (*current_codes, *opening_codes)
    ):
      raise ValueError("LIVE_MARK_SYMBOL_REQUIRED")
    if not self.hub.is_ready:
      raise ValueError("LIVE_MARK_STREAM_NOT_READY")
    stream, generation, sequence = (
      self.hub.stream_id,
      self.hub.generation,
      self.hub.sequence,
    )
    if not isinstance(stream, str) or not stream:
      raise ValueError("LIVE_MARK_SOURCE_IDENTITY_REQUIRED")
    _positive_integer(generation)
    _positive_integer(sequence)
    captured = aware_time(self.hub.last_captured_at).astimezone(UTC)
    if captured > cut or (cut - captured).total_seconds() >= max_age_seconds:
      raise ValueError("LIVE_MARK_CAPTURE_STALE_OR_FUTURE")
    current = {}
    # No await until every current scalar is copied from the same hub cut.
    for code in current_codes:
      raw = self.hub.latest(code)
      if raw is None:
        raise ValueError("LIVE_MARK_CURRENT_REQUIRED")
      source_ms = _positive_integer(raw.get("source_time_ms", raw.get("time")))
      tick_sequence = _positive_integer(raw.get("market_stream_sequence"))
      if (
        raw.get("market_stream_id") != stream
        or raw.get("continuity_generation") != generation
        or tick_sequence > sequence
      ):
        raise ValueError("LIVE_MARK_STREAM_CONFLICT")
      source_at = datetime.fromtimestamp(source_ms / 1000, UTC)
      if source_at > captured:
        raise ValueError("LIVE_MARK_SOURCE_AFTER_CAPTURE")
      price = _price(raw.get("lastPrice", raw.get("last_price")))
      identity = _source_id(
        "live-quote", [code, stream, generation, tick_sequence, source_ms, str(price)]
      )
      mark = TValuationMark(code, price, source_at, identity)
      mark.validate(cut, max_age_seconds=max_age_seconds)
      current[code] = mark
    start = datetime.combine(previous_trading_day, time(15), _EXCHANGE)
    end = start + timedelta(seconds=min(5, max_age_seconds))
    opening = {}
    # Bounded per-symbol reads keep history work off the Engine event loop.
    for code in opening_codes:
      rows = await asyncio.to_thread(self._read_close, code, start, end)
      if len(rows) >= _CLOSE_LIMIT:
        raise ValueError("LIVE_MARK_CLOSE_WINDOW_TRUNCATED")
      previous = None
      for row in rows:
        source_ms = _positive_integer(row.source_time_ms)
        ordinal = row.tick_ordinal
        if type(ordinal) is not int or ordinal < 0:
          raise ValueError("LIVE_MARK_CLOSE_IDENTITY_INVALID")
        identity = source_ms, ordinal
        source_at = datetime.fromtimestamp(source_ms / 1000, UTC)
        # TickRepository uses exchange-local naive storage timestamps; aware
        # query results represent the same instant and must match the identity.
        stored = row.time
        if not isinstance(stored, datetime):
          raise ValueError("LIVE_MARK_CLOSE_TIME_INVALID")
        stored = stored.replace(tzinfo=_EXCHANGE) if stored.tzinfo is None else stored
        expected = tick_storage_time(source_ms, ordinal).replace(tzinfo=_EXCHANGE)
        if (
          row.stock_code != code
          or row.period != "tick"
          or not start <= source_at <= end
          or (previous is not None and identity <= previous)
          or stored != expected
        ):
          raise ValueError("LIVE_MARK_CLOSE_IDENTITY_CONFLICT")
        price = _price(row.last_price)
        mark_id = _source_id("persisted-close", [code, source_ms, ordinal, str(price)])
        opening[code] = TValuationMark(code, price, source_at, mark_id)
        previous = identity
    if (
      not self.hub.is_ready
      or self.hub.stream_id != stream
      or self.hub.generation != generation
    ):
      raise ValueError("LIVE_MARK_STREAM_CHANGED_DURING_READ")
    return LiveTMarketMarks(cut, stream, generation, sequence, current, opening)
