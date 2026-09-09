from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from quantx_application.t_trade_v3.live_bucket_projection import (
  LiveAttributedFill,
  replay_live_bucket_projection,
)

NOW = datetime(2026, 9, 9, 2, tzinfo=UTC)
CODE = "600000.SH"


def seed():
  return {
    CODE: {
      name: dict(total_volume=qty, today_buy_volume=0)
      for name, qty in [("locked_core", 200), ("core", 600), ("swing", 200)]
    }
  }


def fill(side="BUY", **changes):
  return replace(
    LiveAttributedFill(
      "fill-1",
      "order-1",
      CODE,
      side,
      100,
      Decimal(10),
      NOW - timedelta(seconds=1),
      "swing",
    ),
    **changes,
  )


def replay(fills=(), total=1000, free=1000, **changes):
  values = dict(
    seed=seed(),
    seed_as_of=NOW - timedelta(minutes=5),
    as_of=NOW,
    fills=fills,
    broker_positions={CODE: dict(volume=total, can_use_volume=free)},
  )
  values.update(changes)
  return replay_live_bucket_projection(**values)


def test_buy_stays_t1_locked_and_replay_is_deterministic():
  result = replay((fill(),), total=1100)
  assert result.instruments[CODE]["swing"] == dict(
    total_volume=300, today_buy_volume=100, available_volume=200
  )
  assert replay((fill(),), total=1100) == result


def test_unknown_freeze_never_invents_bucket_availability():
  result = replay(free=900).instruments[CODE]
  assert [
    result[key]["available_volume"] for key in ("locked_core", "core", "swing")
  ] == [100, 500, 100]
  assert sum(bucket["available_volume"] for bucket in result.values()) <= 900


def test_substitution_uses_old_core_and_reattributes_new_swing():
  plan = dict(
    enabled=True,
    requested_bucket="swing",
    reattribute_buy_to_bucket="core",
    volume=100,
    sell_from_buckets=[dict(bucket="core", volume=100)],
  )
  result = replay(
    (
      fill(occurred_at=NOW - timedelta(seconds=2)),
      fill("SELL", fill_id="sell", client_order_id="sell", substitution_plan=plan),
    ),
    free=900,
  )
  assert result.instruments[CODE]["core"] == dict(
    total_volume=600, today_buy_volume=100, available_volume=500
  )
  assert result.instruments[CODE]["swing"]["total_volume"] == 200


def test_partial_substitution_consumes_original_legs_only_once():
  plan = dict(
    enabled=True,
    requested_bucket="swing",
    reattribute_buy_to_bucket="core",
    volume=200,
    sell_from_buckets=[dict(bucket="core", volume=200)],
  )
  fills = (
    fill(volume=200, occurred_at=NOW - timedelta(seconds=3)),
    fill(
      "SELL",
      fill_id="sell1",
      client_order_id="sell",
      occurred_at=NOW - timedelta(seconds=2),
      substitution_plan=plan,
    ),
    fill("SELL", fill_id="sell2", client_order_id="sell", substitution_plan=plan),
  )
  result = replay(fills, free=800)
  assert result.instruments[CODE]["core"]["today_buy_volume"] == 200


@pytest.mark.parametrize(
  "changes,reason",
  [
    ({"total": 999}, "BROKER_TOTAL_CONFLICT"),
    ({"fills": (fill(),), "total": 1100, "free": 1100}, "BROKER_SETTLEMENT_CONFLICT"),
    (
      {"fills": (fill("SELL", bucket="locked_core"),), "total": 900, "free": 900},
      "SELL_EXCEEDS_ATTRIBUTION",
    ),
    ({"fills": (fill(), fill()), "total": 1200}, "FILL_INVALID"),
    (
      {"fills": (fill(), fill("SELL", fill_id="sell", client_order_id="sell"))},
      "AMBIGUOUS_FILL_ORDER",
    ),
  ],
)
def test_unproven_attribution_is_rejected(changes, reason):
  with pytest.raises(ValueError, match=reason):
    replay(**changes)


def test_next_trading_day_settlement_unlocks_buys_without_changing_buckets():
  result = replay((fill(),), total=1100, free=1100, as_of=NOW + timedelta(days=1))
  assert result.instruments[CODE]["swing"] == dict(
    total_volume=300, today_buy_volume=0, available_volume=300
  )


@pytest.mark.parametrize(
  "legs,reason",
  [
    (
      [dict(bucket="core", volume=100), dict(bucket="core", volume=100)],
      "DUPLICATE_LEG",
    ),
    ([dict(bucket="locked_core", volume=100)], "SUBSTITUTION_INVALID"),
  ],
)
def test_substitution_cannot_duplicate_or_sell_protected_legs(legs, reason):
  plan = dict(
    enabled=True,
    requested_bucket="swing",
    reattribute_buy_to_bucket="core",
    volume=100,
    sell_from_buckets=legs,
  )
  with pytest.raises(ValueError, match=reason):
    replay(
      (
        fill(occurred_at=NOW - timedelta(seconds=2)),
        fill("SELL", fill_id="sell", client_order_id="sell", substitution_plan=plan),
      )
    )
