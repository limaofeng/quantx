import pytest
from quantx_domain.trading.t_trade import (
  SignalPolicy,
  TickSample,
  TrailingProfitPolicy,
  calculate_target_trade_volume,
  calculate_trailing_floor_pct,
  estimate_net_profit_pct,
  evaluate_intraday_t_signal,
)


def test_target_amount_sizing_uses_lots_and_available_inventory():
  assert (
    calculate_target_trade_volume(
      entry_price=10.0,
      available_volume=2000,
    ).volume
    == 1000
  )
  assert (
    calculate_target_trade_volume(
      entry_price=32.0,
      available_volume=2000,
    ).volume
    == 300
  )
  limited = calculate_target_trade_volume(
    entry_price=20.0,
    available_volume=400,
  )
  assert limited.volume == 400
  assert limited.estimated_amount == 8000.0
  assert limited.reason == "LIMITED_BY_AVAILABLE_VOLUME"


def test_target_amount_sizing_allows_one_lot_only_within_hard_cap():
  one_lot = calculate_target_trade_volume(
    entry_price=110.0,
    available_volume=400,
  )
  blocked = calculate_target_trade_volume(
    entry_price=130.0,
    available_volume=400,
  )

  assert one_lot.volume == 100
  assert one_lot.estimated_amount == 11_000.0
  assert one_lot.reason == "MINIMUM_ONE_LOT"
  assert blocked.volume == 0
  assert blocked.reason == "ONE_LOT_EXCEEDS_MAX_AMOUNT"


def test_net_profit_uses_round_trip_costs():
  gross_profit_pct = 2.0
  net_profit_pct = estimate_net_profit_pct(
    entry_price=10.0,
    exit_price=10.2,
    volume=1000,
  )

  assert 0 < net_profit_pct < gross_profit_pct


def test_trailing_floor_arms_at_target_and_never_moves_down():
  policy = TrailingProfitPolicy()

  first_floor = calculate_trailing_floor_pct(
    peak_profit_pct=2.0,
    policy=policy,
  )
  raised_floor = calculate_trailing_floor_pct(
    peak_profit_pct=4.0,
    previous_floor_pct=first_floor,
    policy=policy,
  )
  preserved_floor = calculate_trailing_floor_pct(
    peak_profit_pct=3.0,
    previous_floor_pct=raised_floor,
    policy=policy,
  )

  assert first_floor == 0.5
  assert raised_floor == 2.0
  assert preserved_floor == raised_floor


def test_signal_requires_pullback_stabilization_and_rebound():
  samples = [
    TickSample(0, 100.0, bid_price=99.99, ask_price=100.0),
    TickSample(60_000, 99.0, bid_price=98.99, ask_price=99.0),
    TickSample(
      80_000,
      99.3,
      bid_price=99.29,
      ask_price=99.3,
      cumulative_amount=995_000,
      cumulative_volume=10_000,
    ),
  ]

  signal = evaluate_intraday_t_signal(
    samples,
    policy=SignalPolicy(
      pullback_threshold_pct=0.8,
      rebound_threshold_pct=0.2,
      stabilization_seconds=15,
    ),
  )

  assert signal.triggered is True
  assert signal.reason == "PULLBACK_REBOUND_CONFIRMED"
  assert signal.pullback_pct == 1.0
  assert signal.rebound_pct > 0.2


def test_signal_requires_the_high_to_precede_the_low():
  samples = [
    TickSample(0, 99.0, bid_price=98.99, ask_price=99.0),
    TickSample(60_000, 100.0, bid_price=99.99, ask_price=100.0),
    TickSample(80_000, 99.3, bid_price=99.29, ask_price=99.3),
  ]

  signal = evaluate_intraday_t_signal(samples)

  assert signal.triggered is False
  assert signal.reason == "PULLBACK_TOO_SMALL"


@pytest.mark.parametrize(
  ("ask_price", "expected_ticks", "expected_triggered", "expected_reason"),
  [
    (44.59, 3, True, "PULLBACK_REBOUND_CONFIRMED"),
    (44.60, 4, False, "SPREAD_TOO_WIDE"),
  ],
)
def test_pullback_signal_uses_integer_price_ticks_for_spread_boundary(
  ask_price: float,
  expected_ticks: int,
  expected_triggered: bool,
  expected_reason: str,
):
  samples = [
    TickSample(0, 45.0),
    TickSample(60_000, 44.4),
    TickSample(
      80_000,
      44.59,
      bid_price=44.56,
      ask_price=ask_price,
      cumulative_amount=447_000,
      cumulative_volume=10_000,
    ),
  ]

  signal = evaluate_intraday_t_signal(
    samples,
    policy=SignalPolicy(max_spread_ticks=3, momentum_enabled=False),
  )

  assert signal.triggered is expected_triggered
  assert signal.reason == expected_reason
  assert signal.spread_ticks == expected_ticks


@pytest.mark.parametrize(
  ("bid_price", "ask_price", "price_tick"),
  [
    (float("nan"), 44.59, 0.01),
    (44.56, float("inf"), 0.01),
    (44.56, 44.59, float("nan")),
    (44.60, 44.59, 0.01),
  ],
)
def test_pullback_signal_fails_closed_for_untrustworthy_order_book(
  bid_price: float,
  ask_price: float,
  price_tick: float,
):
  signal = evaluate_intraday_t_signal(
    [
      TickSample(0, 45.0),
      TickSample(60_000, 44.4),
      TickSample(
        80_000,
        44.59,
        bid_price=bid_price,
        ask_price=ask_price,
        cumulative_amount=447_000,
        cumulative_volume=10_000,
      ),
    ],
    policy=SignalPolicy(price_tick=price_tick),
  )

  assert signal.triggered is False
  assert signal.reason == "ORDER_BOOK_UNAVAILABLE"
  assert signal.spread_ticks == 0


def test_momentum_signal_detects_early_acceleration_without_future_ticks():
  # Recorded from 300917.SZ (特发服务) on 2026-08-12.  The last sample is
  # 13:49:57; no observation from the later 14:02 vertical move is present.
  samples = [
    TickSample(0, 27.35, cumulative_amount=139_550_205.23, cumulative_volume=5_151_984),
    TickSample(
      60_000, 27.41, cumulative_amount=140_158_282.23, cumulative_volume=5_174_184
    ),
    TickSample(
      120_000, 27.50, cumulative_amount=146_892_465.23, cumulative_volume=5_418_784
    ),
    TickSample(
      180_000, 27.58, cumulative_amount=150_553_455.23, cumulative_volume=5_551_684
    ),
    TickSample(
      240_000, 27.53, cumulative_amount=152_226_340.73, cumulative_volume=5_612_473
    ),
    TickSample(
      300_000, 27.53, cumulative_amount=154_724_134.73, cumulative_volume=5_703_073
    ),
    TickSample(
      360_000,
      27.78,
      bid_price=27.75,
      ask_price=27.80,
      cumulative_amount=161_167_439.73,
      cumulative_volume=5_936_073,
    ),
  ]

  signal = evaluate_intraday_t_signal(samples)

  assert signal.triggered is True
  assert signal.signal_type == "MOMENTUM_ACCELERATION"
  assert signal.reason == "MOMENTUM_ACCELERATION_CONFIRMED"
  assert signal.momentum_rise_pct == pytest.approx(0.9081002543)
  assert signal.momentum_move_seconds == 60
  assert signal.momentum_amount_velocity_ratio == pytest.approx(2.1231497748)
  assert signal.vwap == pytest.approx(27.1505151183)
  assert signal.vwap_premium_pct == pytest.approx(2.3185006948)
  assert signal.spread_ticks == pytest.approx(5)


def test_momentum_signal_never_bypasses_invalid_order_book():
  samples = [
    TickSample(0, 27.35, cumulative_amount=139_550_205.23, cumulative_volume=5_151_984),
    TickSample(
      60_000, 27.41, cumulative_amount=140_158_282.23, cumulative_volume=5_174_184
    ),
    TickSample(
      120_000, 27.50, cumulative_amount=146_892_465.23, cumulative_volume=5_418_784
    ),
    TickSample(
      180_000, 27.58, cumulative_amount=150_553_455.23, cumulative_volume=5_551_684
    ),
    TickSample(
      240_000, 27.53, cumulative_amount=152_226_340.73, cumulative_volume=5_612_473
    ),
    TickSample(
      300_000, 27.53, cumulative_amount=154_724_134.73, cumulative_volume=5_703_073
    ),
    TickSample(
      360_000,
      27.78,
      bid_price=float("nan"),
      ask_price=27.80,
      cumulative_amount=161_167_439.73,
      cumulative_volume=5_936_073,
    ),
  ]

  signal = evaluate_intraday_t_signal(samples)

  assert signal.triggered is False
  assert signal.reason == "ORDER_BOOK_UNAVAILABLE"


@pytest.mark.parametrize(
  ("ask_price", "expected_ticks", "expected_triggered", "expected_reason"),
  [
    (44.59, 3, True, "MOMENTUM_ACCELERATION_CONFIRMED"),
    (44.60, 4, False, "MOMENTUM_SPREAD_TOO_WIDE"),
  ],
)
def test_momentum_signal_uses_integer_price_ticks_for_spread_boundary(
  ask_price: float,
  expected_ticks: int,
  expected_triggered: bool,
  expected_reason: str,
):
  samples = [
    TickSample(0, 44.20, cumulative_amount=100_000_000, cumulative_volume=2_300_000),
    TickSample(
      60_000, 44.25, cumulative_amount=100_300_000, cumulative_volume=2_306_000
    ),
    TickSample(
      120_000, 44.30, cumulative_amount=100_600_000, cumulative_volume=2_312_000
    ),
    TickSample(
      180_000, 44.35, cumulative_amount=100_900_000, cumulative_volume=2_318_000
    ),
    TickSample(
      240_000, 44.31, cumulative_amount=101_200_000, cumulative_volume=2_324_000
    ),
    TickSample(
      300_000, 44.19, cumulative_amount=101_500_000, cumulative_volume=2_330_000
    ),
    TickSample(
      360_000,
      44.59,
      bid_price=44.56,
      ask_price=ask_price,
      cumulative_amount=102_200_000,
      cumulative_volume=2_350_000,
    ),
  ]

  signal = evaluate_intraday_t_signal(
    samples,
    policy=SignalPolicy(momentum_max_spread_ticks=3),
  )

  assert signal.triggered is expected_triggered
  assert signal.reason == expected_reason
  assert signal.spread_ticks == expected_ticks


def test_momentum_signal_rejects_late_vertical_chase_above_vwap_band():
  # Recorded causal snapshots leading into 14:02:33.  The move is fast, but at
  # 29.20 the price is already 6.04% above VWAP and is no longer an early entry.
  samples = [
    TickSample(0, 28.00, cumulative_amount=220_000_000, cumulative_volume=8_000_000),
    TickSample(
      300_000, 28.27, cumulative_amount=246_846_410.37, cumulative_volume=8_928_632
    ),
    TickSample(
      330_000, 28.50, cumulative_amount=255_000_000, cumulative_volume=9_220_000
    ),
    TickSample(
      360_000,
      29.20,
      bid_price=29.20,
      ask_price=29.25,
      cumulative_amount=265_077_630.37,
      cumulative_volume=9_626_734,
    ),
  ]

  signal = evaluate_intraday_t_signal(samples)

  assert signal.triggered is False
  assert signal.reason == "MOMENTUM_VWAP_PREMIUM_TOO_HIGH"
  assert signal.vwap_premium_pct == pytest.approx(6.0446, abs=0.0001)
