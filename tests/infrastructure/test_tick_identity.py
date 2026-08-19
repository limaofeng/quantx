from datetime import datetime, timedelta

import pytest
from quantx_contracts import HISTORICAL_TICK_ORDINALS_PER_MILLISECOND
from quantx_infrastructure.core.data.tick_identity import (
  merge_ticks_losslessly,
  normalize_ticks_losslessly,
  tick_query_end_time,
  tick_snapshot_identity,
  tick_source_time_ms,
)
from quantx_infrastructure.models.tick import Tick

SOURCE_TIME_MS = 1_786_671_000_123
SOURCE_TIME = datetime(2026, 8, 14, 9, 30, 0, 123000)


def _tick(
  *,
  transaction_num: int,
  last_price: float,
  tickvol: float,
  source_time_ms: int = SOURCE_TIME_MS,
  ordinal: int = 0,
) -> Tick:
  return Tick(
    stock_code="601318.SH",
    period="tick",
    time=SOURCE_TIME + timedelta(microseconds=ordinal),
    source_time_ms=source_time_ms,
    tick_ordinal=ordinal,
    last_price=last_price,
    volume=1000.0 + transaction_num,
    amount=10_000.0 + transaction_num,
    transaction_num=transaction_num,
    tickvol=tickvol,
    pvolume=999_999.0,
    stock_status=0,
  )


def test_merge_keeps_history_a_and_b_and_drops_warm_b_overlap() -> None:
  historical_a = _tick(transaction_num=1, last_price=10.0, tickvol=10.0)
  historical_b = _tick(
    transaction_num=2,
    last_price=10.1,
    tickvol=20.0,
    ordinal=1,
  )
  warm_b = _tick(transaction_num=2, last_price=10.1, tickvol=999.0)

  merged = merge_ticks_losslessly(
    [historical_a, historical_b],
    [warm_b],
  )

  assert len(merged) == 2
  assert [tick.transaction_num for tick in merged] == [1, 2]
  assert [tick.tick_ordinal for tick in merged] == [0, 1]
  assert merged[1].tickvol == 20.0
  assert merged[0] is not historical_a
  assert merged[1] is not historical_b


def test_normalize_preserves_authoritative_same_identity_ordinals() -> None:
  first = _tick(transaction_num=1, last_price=10.0, tickvol=10.0)
  second = _tick(
    transaction_num=1,
    last_price=10.0,
    tickvol=20.0,
    ordinal=1,
  )

  assert tick_snapshot_identity(first) == tick_snapshot_identity(second)

  normalized = normalize_ticks_losslessly([first, second])

  assert len(normalized) == 2
  assert [tick.tickvol for tick in normalized] == [10.0, 20.0]
  assert [tick.tick_ordinal for tick in normalized] == [0, 1]


def test_normalize_restores_authoritative_ordinal_order_from_desc_input() -> None:
  first = _tick(transaction_num=1, last_price=10.0, tickvol=10.0)
  second = _tick(
    transaction_num=1,
    last_price=10.0,
    tickvol=20.0,
    ordinal=1,
  )

  normalized = normalize_ticks_losslessly([second, first])

  assert [tick.tickvol for tick in normalized] == [10.0, 20.0]
  assert [tick.tick_ordinal for tick in normalized] == [0, 1]


def test_normalize_keeps_distinct_warm_snapshots_in_same_millisecond() -> None:
  later = _tick(transaction_num=2, last_price=10.1, tickvol=2.0)
  earlier = _tick(transaction_num=1, last_price=10.0, tickvol=1.0)

  normalized = normalize_ticks_losslessly([later, earlier])

  assert len(normalized) == 2
  assert [tick.transaction_num for tick in normalized] == [1, 2]
  assert [tick.tick_ordinal for tick in normalized] == [0, 1]
  assert normalized[1].time - normalized[0].time == timedelta(microseconds=1)
  assert {tick.source_time_ms for tick in normalized} == {SOURCE_TIME_MS}


def test_tick_identity_ignores_storage_and_known_unstable_fields() -> None:
  first = _tick(transaction_num=1, last_price=10.0, tickvol=1.0)
  second = _tick(
    transaction_num=1,
    last_price=10.0,
    tickvol=999.0,
    source_time_ms=SOURCE_TIME_MS + 1,
    ordinal=7,
  )
  second.pvolume = 1.0
  second.stock_status = -1

  assert tick_snapshot_identity(first) == tick_snapshot_identity(second)


def test_tick_identity_matches_real_history_and_warm_codec_shapes() -> None:
  historical = _tick(transaction_num=103, last_price=52.81, tickvol=2.0)
  historical.open = 52.8
  historical.high = 53.1
  historical.low = 52.5
  historical.last_close = 52.8
  historical.open_int = 0
  historical.ask_price = [52.81, 52.82, 52.83, 52.84, 52.85]
  historical.bid_price = [52.8, 52.79, 52.78, 52.77, 52.76]
  historical.ask_vol = [263.0, 100.0, 200.0, 300.0, 400.0]
  historical.bid_vol = [321.0, 110.0, 210.0, 310.0, 410.0]
  historical.pe = 7.23

  warm = _tick(transaction_num=103, last_price=52.81, tickvol=999.0)
  warm.open = 52.800000000000004
  warm.high = 53.1
  warm.low = 52.5
  warm.last_close = 52.800000000000004
  warm.open_int = 0
  warm.ask_price = list(historical.ask_price)
  warm.bid_price = list(historical.bid_price)
  warm.ask_vol = list(historical.ask_vol)
  warm.bid_vol = list(historical.bid_vol)
  warm.price_tick = 0.01
  warm.up_stop_price = 58.08
  warm.down_stop_price = 47.52

  assert tick_snapshot_identity(historical) == tick_snapshot_identity(warm)

  merged = merge_ticks_losslessly([historical], [warm])
  assert len(merged) == 1
  assert merged[0].pe == historical.pe
  assert merged[0].tickvol == historical.tickvol


def test_tick_source_time_ms_floors_legacy_tick_time() -> None:
  tick = _tick(transaction_num=1, last_price=10.0, tickvol=1.0)
  del tick.source_time_ms
  tick.time = SOURCE_TIME + timedelta(microseconds=987)

  assert tick_source_time_ms(tick) == SOURCE_TIME_MS


def test_tick_source_time_ms_falls_back_for_mixed_schema_nan() -> None:
  tick = _tick(transaction_num=1, last_price=10.0, tickvol=1.0)
  tick.source_time_ms = float("nan")
  tick.time = SOURCE_TIME + timedelta(microseconds=987)

  assert tick_source_time_ms(tick) == SOURCE_TIME_MS


@pytest.mark.parametrize(
  "source_time_ms",
  [True, float("inf"), SOURCE_TIME_MS + 0.5, "invalid"],
)
def test_tick_source_time_ms_rejects_non_integer_values(source_time_ms) -> None:
  tick = _tick(transaction_num=1, last_price=10.0, tickvol=1.0)
  tick.source_time_ms = source_time_ms

  with pytest.raises(ValueError, match="invalid Tick source_time_ms"):
    tick_source_time_ms(tick)


def test_normalize_rejects_more_than_supported_ordinals() -> None:
  ticks = [
    _tick(transaction_num=index, last_price=float(index), tickvol=float(index))
    for index in range(HISTORICAL_TICK_ORDINALS_PER_MILLISECOND + 1)
  ]

  with pytest.raises(ValueError, match="too many Tick occurrences"):
    normalize_ticks_losslessly(ticks)


def test_tick_query_end_time_includes_all_ordinals_in_source_millisecond() -> None:
  end_time = datetime(2026, 8, 14, 9, 30, 0, 123456)

  assert tick_query_end_time(end_time) == datetime(
    2026, 8, 14, 9, 30, 0, 123999
  )
  assert tick_query_end_time(None) is None
