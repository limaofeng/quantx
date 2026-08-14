from datetime import date, datetime
from types import SimpleNamespace

from quantx_infrastructure.services.intraday_volume_scanner import (
  IntradayVolumeScanner,
)
from quantx_infrastructure.services.limit_up_radar import (
  LimitUpRadarBuilder,
  _resolved_listing_history_days,
  select_latest_intraday_projection,
)


def test_listing_history_falls_back_to_conservative_open_date_tenure() -> None:
  established = SimpleNamespace(
    day_count_from_ipo=0,
    open_date=date(2020, 1, 1),
  )
  recent = SimpleNamespace(
    day_count_from_ipo=None,
    open_date=date(2026, 6, 1),
  )

  assert _resolved_listing_history_days(established, date(2026, 8, 14)) > 120
  assert _resolved_listing_history_days(recent, date(2026, 8, 14)) < 120


def test_listing_history_prefers_qmt_trading_day_counter() -> None:
  instrument = SimpleNamespace(
    day_count_from_ipo=121,
    open_date=date(2026, 6, 1),
  )

  assert _resolved_listing_history_days(instrument, date(2026, 8, 14)) == 121


def baseline(name: str = "雷达科技"):
  return {
    "code": "000001.SZ",
    "name": name,
    "industry": "计算机",
    "instrument_type": "stock",
    "avg_volume_20": 2400.0,
    "avg_amount_20": 24000.0,
    "float_volume": 8_000_000.0,
  }


def tick(
  price: float,
  timestamp: str,
  *,
  upper_limit: float = 11.0,
  ask_price=None,
  open_price: float = 10.1,
  high_price: float | None = None,
  low_price: float = 10.0,
  price_tick: float | None = 0.01,
  stock_status: int = 0,
):
  value = {
    "lastPrice": price,
    "preClose": 10.0,
    "open": open_price,
    "high": high_price if high_price is not None else price,
    "low": low_price,
    "upperLimit": upper_limit,
    "stockStatus": stock_status,
    "volume": 2600,
    "amount": 30000,
    "transactionNum": 108,
    "bidPrice": [price, price - 0.01, price - 0.02],
    "askPrice": ask_price if ask_price is not None else [price + 0.01],
    "bidVol": [3000, 2000, 1000, 500, 200],
    "askVol": [300, 200, 100, 50, 20],
    "timetag": timestamp,
  }
  if price_tick is not None:
    value["priceTick"] = price_tick
  return value


def build(builder, scanner, now):
  base = [baseline()]
  intraday = scanner.screen(base, stale_after_seconds=10**9, limit=20000)
  return builder.build(
    baselines=base,
    states=scanner.snapshot_states(),
    intraday_items=intraday["items"],
    scanner_running=True,
    now=now,
  )


def test_limit_up_radar_tracks_seal_break_and_reseal():
  scanner = IntradayVolumeScanner()
  builder = LimitUpRadarBuilder()

  scanner.update_tick("000001.SZ", tick(10.8, "20260810 10:00:00"))
  result = build(builder, scanner, datetime(2026, 8, 10, 10, 0))
  assert result["items"][0]["stage"] == "SURGING"

  scanner.update_tick("000001.SZ", tick(10.95, "20260810 10:01:00"))
  result = build(builder, scanner, datetime(2026, 8, 10, 10, 1))
  assert result["items"][0]["stage"] == "NEAR_LIMIT"

  scanner.update_tick(
    "000001.SZ",
    tick(11.0, "20260810 10:02:00", ask_price=[0, 0, 0, 0, 0]),
  )
  result = build(builder, scanner, datetime(2026, 8, 10, 10, 2))
  assert result["items"][0]["stage"] == "SEALED"
  assert result["items"][0]["can_create_instance"] is False

  scanner.update_tick("000001.SZ", tick(10.9, "20260810 10:03:00"))
  result = build(builder, scanner, datetime(2026, 8, 10, 10, 3))
  assert result["items"][0]["stage"] == "BROKEN"
  assert result["items"][0]["break_count"] == 1

  scanner.update_tick(
    "000001.SZ",
    tick(11.0, "20260810 10:04:00", ask_price=[0, 0, 0, 0, 0]),
  )
  result = build(builder, scanner, datetime(2026, 8, 10, 10, 4))
  item = result["items"][0]
  assert item["stage"] == "RESEALED"
  assert [event["stage"] for event in item["events"]][:3] == [
    "RESEALED",
    "BROKEN",
    "SEALED",
  ]
  penalty = next(
    factor for factor in item["score_breakdown"] if factor["code"] == "BREAK_PENALTY"
  )
  assert penalty["score"] == -8


def test_limit_up_radar_score_is_explainable_and_bounded():
  scanner = IntradayVolumeScanner()
  builder = LimitUpRadarBuilder()
  scanner.update_tick("000001.SZ", tick(10.95, "20260810 10:00:00"))

  item = build(builder, scanner, datetime(2026, 8, 10, 10, 0))["items"][0]

  assert 0 <= item["radar_score"] <= 100
  assert item["score_version"] == "limit-up-radar-v1"
  assert {factor["code"] for factor in item["score_breakdown"]} == {
    "PROXIMITY",
    "PRICE_ACCELERATION_5M",
    "AMOUNT_PACE",
    "VOLUME_5M",
    "TURNOVER",
    "DEPTH",
    "INDUSTRY_HEAT",
  }
  assert all(factor["explanation"] for factor in item["score_breakdown"])


def test_discovery_starts_at_thirty_percent_without_a_momentum_gate() -> None:
  scanner = IntradayVolumeScanner()
  builder = LimitUpRadarBuilder()
  quiet = baseline()
  quiet.update(
    {
      "history_trading_days": 300,
      "price_position_252": 0.4,
      "ma20_deviation_pct": 2.0,
      "prior_20d_return_pct": 3.0,
    }
  )
  value = tick(10.3, "20260810 10:00:00")
  value["amount"] = 1_000
  value["volume"] = 1_000
  scanner.update_tick("000001.SZ", value)
  intraday = scanner.screen([quiet], stale_after_seconds=10**9, limit=20000)

  result = builder.build(
    baselines=[quiet],
    states=scanner.snapshot_states(),
    intraday_items=intraday["items"],
    scanner_running=True,
    now=datetime(2026, 8, 10, 10, 0),
  )

  assert result["summary"]["discovered_count"] == 1
  assert result["items"][0]["normalized_limit_progress"] >= 0.30


def test_chain_snapshot_version_ignores_wall_clock_when_state_is_unchanged() -> None:
  scanner = IntradayVolumeScanner()
  builder = LimitUpRadarBuilder()
  scanner.update_tick("000001.SZ", tick(10.95, "20260810 10:00:00"))

  first = build(builder, scanner, datetime(2026, 8, 10, 10, 0))
  second = build(builder, scanner, datetime(2026, 8, 10, 10, 0, 30))

  assert first["chain"]["snapshot_version"] == second["chain"]["snapshot_version"]


def test_limit_up_radar_blocks_one_word_and_excludes_st():
  scanner = IntradayVolumeScanner()
  builder = LimitUpRadarBuilder()
  scanner.update_tick(
    "000001.SZ",
    tick(
      11.0,
      "20260810 10:00:00",
      ask_price=[0, 0, 0, 0, 0],
      open_price=11.0,
      high_price=11.0,
      low_price=11.0,
    ),
  )
  intraday = scanner.screen([baseline()], stale_after_seconds=10**9, limit=20000)
  result = builder.build(
    baselines=[baseline()],
    states=scanner.snapshot_states(),
    intraday_items=intraday["items"],
    scanner_running=True,
    now=datetime(2026, 8, 10, 10, 0),
  )
  assert result["items"][0]["one_word_limit_up"] is True
  assert "ONE_WORD_LIMIT_UP" in result["items"][0]["blocked_reasons"]

  excluded = LimitUpRadarBuilder().build(
    baselines=[baseline("ST雷达")],
    states=scanner.snapshot_states(),
    intraday_items=intraday["items"],
    scanner_running=True,
    now=datetime(2026, 8, 10, 10, 0),
  )
  assert excluded["items"] == []
  assert excluded["summary"]["excluded_count"] == 1


def test_limit_up_radar_uses_actual_twenty_percent_limit_and_restores_events():
  scanner = IntradayVolumeScanner()
  builder = LimitUpRadarBuilder()
  builder.restore(
    [
      SimpleNamespace(
        event_id="event-1",
        instrument_code="000001.SZ",
        stage="BROKEN",
        occurred_at=datetime(2026, 8, 10, 9, 55),
        score=65,
      )
    ]
  )
  scanner.update_tick(
    "000001.SZ",
    tick(
      11.9,
      "20260810 10:00:00",
      upper_limit=12.0,
      open_price=10.2,
    ),
  )

  item = build(builder, scanner, datetime(2026, 8, 10, 10, 0))["items"][0]

  assert item["limit_up_price"] == 12.0
  assert item["stage"] == "BROKEN"
  assert item["break_count"] == 1


def test_limit_up_radar_excludes_suspended_and_missing_exchange_fields():
  for invalid_tick in (
    tick(10.8, "20260810 10:00:00", stock_status=1),
    tick(10.8, "20260810 10:00:00", upper_limit=0),
    tick(10.8, "20260810 10:00:00", price_tick=None),
  ):
    scanner = IntradayVolumeScanner()
    scanner.update_tick("000001.SZ", invalid_tick)

    result = build(
      LimitUpRadarBuilder(),
      scanner,
      datetime(2026, 8, 10, 10, 0),
    )

    assert result["items"] == []
    assert result["summary"]["excluded_count"] == 1


def test_limit_up_radar_marks_stale_and_missing_factor_data_fail_closed():
  scanner = IntradayVolumeScanner()
  builder = LimitUpRadarBuilder()
  scanner.update_tick("000001.SZ", tick(10.95, "20260810 10:00:00"))
  missing_industry = {**baseline(), "industry": None, "float_volume": None}
  intraday = scanner.screen(
    [missing_industry],
    stale_after_seconds=1,
    limit=20000,
  )

  result = builder.build(
    baselines=[missing_industry],
    states=scanner.snapshot_states(),
    intraday_items=intraday["items"],
    scanner_running=True,
    now=datetime(2026, 8, 10, 10, 0),
  )

  item = result["items"][0]
  assert item["is_stale"] is True
  assert item["can_create_instance"] is False
  assert "STALE_MARKET_DATA" in item["blocked_reasons"]
  assert "MISSING_INDUSTRY" in item["quality_tags"]
  assert "MISSING_FLOAT_VOLUME" in item["quality_tags"]
  industry_factor = next(
    factor
    for factor in item["score_breakdown"]
    if factor["code"] == "INDUSTRY_HEAT"
  )
  assert industry_factor["score"] == 0


def test_intraday_projection_preserves_last_real_snapshot_during_agent_outage():
  current = {
    "items": [{"code": "000001.SZ", "change_pct": 1.2}],
    "total": 1,
    "updated_at": "2026-08-13T14:48:30+08:00",
    "is_scanner_running": True,
    "warnings": [],
  }
  empty_after_restart = {
    "items": [],
    "total": 0,
    "updated_at": None,
    "is_scanner_running": False,
    "warnings": ["QMT Agent 离线"],
  }

  selected = select_latest_intraday_projection(current, empty_after_restart)

  assert selected["items"] == current["items"]
  assert selected["updated_at"] == current["updated_at"]
  assert selected["is_scanner_running"] is False
  assert selected["retained_snapshot"] is True
  assert "QMT Agent 离线" in selected["warnings"]


def test_intraday_projection_replaces_retained_snapshot_with_newer_market_data():
  current = {
    "items": [{"code": "000001.SZ"}],
    "total": 1,
    "updated_at": "2026-08-12T15:00:00+08:00",
  }
  candidate = {
    "items": [{"code": "000002.SZ"}],
    "total": 1,
    "updated_at": "2026-08-13T09:30:05+08:00",
  }

  assert select_latest_intraday_projection(current, candidate) is candidate
