"""Bounded historical close selection and authoritative current quote fencing."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from quantx_infrastructure.core.data.tick_identity import tick_storage_time
from quantx_infrastructure.core.data.whole_quote_hub import (
  WholeQuoteHub,
  WholeQuoteStatus,
)
from quantx_infrastructure.services.live_t_market_marks import LiveTMarketMarkReader

CUT = datetime(2026, 9, 9, 2, 0, tzinfo=UTC)
CLOSE = datetime(2026, 9, 8, 7, 0, tzinfo=UTC)
CODE = "600000.SH"


def hub_at(cut=CUT):
  hub = WholeQuoteHub()
  hub.status = WholeQuoteStatus.READY
  hub.stream_id, hub.generation, hub.sequence = "stream-1", 2, 10
  hub.last_captured_at = cut
  hub._latest = {
    CODE: {
      "source_time_ms": int(cut.timestamp() * 1000),
      "lastPrice": 10,
      "market_stream_id": "stream-1",
      "continuity_generation": 2,
      "market_stream_sequence": 9,
    }
  }
  return hub


def close_tick(at=CLOSE, ordinal=0, price=9):
  source_ms = int(at.timestamp() * 1000)
  return SimpleNamespace(
    stock_code=CODE,
    period="tick",
    source_time_ms=source_ms,
    tick_ordinal=ordinal,
    time=tick_storage_time(source_ms, ordinal),
    last_price=price,
  )


class History:
  def __init__(self, rows=()):
    self.rows, self.calls = rows, []

  def find_source_identity_page(self, **kwargs):
    self.calls.append(kwargs)
    return self.rows


async def read(hub, history, **changes):
  args = dict(
    as_of=CUT,
    previous_trading_day=CLOSE.date(),
    current_codes=[CODE],
    opening_codes=[CODE],
    max_age_seconds=30,
  )
  args.update(changes)
  return await LiveTMarketMarkReader(hub, tick_repository=history).read(**args)


async def test_source_bound_latest_close_and_exact_window():
  history = History(
    [
      close_tick(),
      close_tick(ordinal=1, price=9.1),
      close_tick(CLOSE + timedelta(seconds=2), price=9.2),
    ]
  )
  result = await read(hub_at(), history)
  assert result.current[CODE].price == 10
  assert result.opening[CODE].price == Decimal("9.2")
  assert result.sequence == 10 and result.generation == 2
  assert history.calls[0]["start_time"] == CLOSE
  assert history.calls[0]["end_time"] == CLOSE + timedelta(seconds=5)
  assert history.calls[0]["limit"] == 1000
  assert result.opening[CODE].source_id.startswith("persisted-close:")
  assert result == await read(hub_at(), history)


async def test_missing_close_is_not_replaced_by_last_close_field():
  hub = hub_at()
  hub._latest[CODE]["lastClose"] = 99
  result = await read(hub, History())
  assert result.opening == {}


@pytest.mark.parametrize(
  "damage,reason",
  [
    ("offline", "STREAM_NOT_READY"),
    ("stale_capture", "CAPTURE_STALE"),
    ("future_capture", "CAPTURE_STALE_OR_FUTURE"),
    ("missing", "CURRENT_REQUIRED"),
    ("stream", "STREAM_CONFLICT"),
    ("generation", "STREAM_CONFLICT"),
    ("sequence", "STREAM_CONFLICT"),
    ("source_future", "SOURCE_AFTER_CAPTURE"),
    ("stale_quote", "MARK_STALE"),
    ("nan", "PRICE_INVALID"),
    ("bad_identity", "IDENTITY_REQUIRED"),
  ],
)
async def test_current_source_failures_close_the_reader(damage, reason):
  hub = hub_at()
  raw = hub._latest[CODE]
  if damage == "offline":
    hub.status = WholeQuoteStatus.STALE
  elif damage == "stale_capture":
    hub.last_captured_at -= timedelta(seconds=30)
  elif damage == "future_capture":
    hub.last_captured_at += timedelta(seconds=1)
  elif damage == "missing":
    hub._latest.clear()
  elif damage == "stream":
    raw["market_stream_id"] = "foreign"
  elif damage == "generation":
    raw["continuity_generation"] = 1
  elif damage == "sequence":
    raw["market_stream_sequence"] = 11
  elif damage == "source_future":
    raw["source_time_ms"] += 1000
  elif damage == "stale_quote":
    raw["source_time_ms"] -= 31000
  elif damage == "nan":
    raw["lastPrice"] = "NaN"
  else:
    raw["source_time_ms"] = True
  with pytest.raises(ValueError, match=reason):
    await read(hub, History())


@pytest.mark.parametrize(
  "damage",
  [
    "duplicate",
    "reversed",
    "wrong_code",
    "wrong_time",
    "outside_window",
    "negative_ordinal",
    "truncated",
  ],
)
async def test_history_identity_and_bounded_read(damage):
  rows = [close_tick(), close_tick(ordinal=1)]
  if damage == "duplicate":
    rows[1] = close_tick()
  elif damage == "reversed":
    rows.reverse()
  elif damage == "wrong_code":
    rows[0].stock_code = "foreign"
  elif damage == "wrong_time":
    rows[0].time += timedelta(microseconds=1)
  elif damage == "outside_window":
    rows[0] = close_tick(CLOSE - timedelta(seconds=1))
  elif damage == "negative_ordinal":
    rows[0].tick_ordinal = -1
  else:
    rows = [close_tick()] * 1000
  with pytest.raises(ValueError, match="LIVE_MARK_CLOSE"):
    await read(hub_at(), History(rows))


async def test_stream_reset_while_loading_history_is_not_a_valid_cut():
  hub = hub_at()

  class ResetHistory(History):
    def find_source_identity_page(self, **kwargs):
      hub.generation += 1
      return []

  with pytest.raises(ValueError, match="STREAM_CHANGED_DURING_READ"):
    await read(hub, ResetHistory())


async def test_same_stream_progress_keeps_the_frozen_quote_cut():
  hub = hub_at()

  class AdvanceHistory(History):
    def find_source_identity_page(self, **kwargs):
      hub.sequence += 1
      hub._latest[CODE] = {**hub._latest[CODE], "lastPrice": 11}
      return []

  result = await read(hub, AdvanceHistory())
  assert result.current[CODE].price == 10 and result.sequence == 10
