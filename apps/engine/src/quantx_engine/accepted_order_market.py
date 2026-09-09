"""Strict full-book projection of an accepted hub Tick, shared by PAPER and LIVE."""

import math
from datetime import UTC, datetime
from types import SimpleNamespace

from quantx_contracts import ExecutionEnvironment
from quantx_domain.trading.market_rules import MarketDataSnapshot
from quantx_domain.trading.t_assistant_execution import stable_manifest_hash


def accepted_order_market(instrument_code, raw, *, now, environment):
  """Project the original hub book without inventing depth or price limits."""

  if environment not in {ExecutionEnvironment.PAPER, ExecutionEnvironment.LIVE}:
    raise ValueError("ACCEPTED_MARKET_ENVIRONMENT_REQUIRED")
  prefix = environment.value

  def required(*names):
    values = [raw[name] for name in names if name in raw and raw[name] is not None]
    if not values or any(value != values[0] for value in values):
      raise ValueError(f"{prefix}_MARKET_FIELD_REQUIRED_OR_CONFLICTING")
    return values[0]

  def number(*names, positive=False):
    value = required(*names)
    if (
      type(value) not in (int, float)
      or not math.isfinite(value)
      or value < 0
      or (positive and value == 0)
    ):
      raise ValueError(f"{prefix}_MARKET_NUMBER_INVALID")
    return value

  stream, generation = required("market_stream_id"), required("continuity_generation")
  source = required("source_time_ms")
  ordinal = required("tick_ordinal")
  fence = required("market_stream_sequence")
  if (
    not isinstance(stream, str)
    or not stream
    or any(
      type(value) is not int or value <= 0
      for value in (generation, source, ordinal, fence)
    )
  ):
    raise ValueError(f"{prefix}_MARKET_LINEAGE_REQUIRED")
  timestamp = datetime.fromtimestamp(source / 1000, tz=UTC)
  if now.tzinfo is None or timestamp > now:
    raise ValueError(f"{prefix}_MARKET_TIME_INVALID")
  tick = SimpleNamespace(
    code=instrument_code,
    time=timestamp,
    last_price=number("lastPrice", "last_price", positive=True),
    price_tick=number("priceTick", "PriceTick", "price_tick", positive=True),
    up_stop_price=number(
      "upperLimit", "upStopPrice", "UpStopPrice", "up_stop_price", positive=True
    ),
    down_stop_price=number(
      "lowerLimit", "downStopPrice", "DownStopPrice", "down_stop_price", positive=True
    ),
    stock_status=number("stockStatus", "stock_status"),
    volume=number("volume"),
    amount=number("amount"),
    bid_price=required("bidPrice", "bid_price"),
    ask_price=required("askPrice", "ask_price"),
    bid_vol=required("bidVol", "bid_vol"),
    ask_vol=required("askVol", "ask_vol"),
  )
  market = MarketDataSnapshot.from_tick(tick)
  market.source = f"{prefix}_ACCEPTED_WHOLE_QUOTE"
  for prices, volumes in (
    (market.bid_price, market.bid_vol),
    (market.ask_price, market.ask_vol),
  ):
    if len(prices) != 5 or len(volumes) != 5:
      raise ValueError(f"{prefix}_COMPLETE_FIVE_LEVEL_BOOK_REQUIRED")
    if any(
      type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in prices
    ) or any(
      type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in volumes
    ):
      raise ValueError(f"{prefix}_MARKET_NUMBER_INVALID")
  if (
    list(market.bid_price) != sorted(market.bid_price, reverse=True)
    or list(market.ask_price) != sorted(market.ask_price)
    or market.bid_price[0] > market.ask_price[0]
  ):
    raise ValueError(f"{prefix}_INVALID_BOOK_ORDER")
  identity = {
    "stream_id": stream,
    "generation": generation,
    "source_time_ms": source,
    "tick_ordinal": ordinal,
    "instrument_code": instrument_code,
  }
  return "hub:" + stable_manifest_hash(identity), market
