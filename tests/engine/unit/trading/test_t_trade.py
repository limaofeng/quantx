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
  assert calculate_target_trade_volume(
    entry_price=10.0,
    available_volume=2000,
  ).volume == 1000
  assert calculate_target_trade_volume(
    entry_price=32.0,
    available_volume=2000,
  ).volume == 300
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
