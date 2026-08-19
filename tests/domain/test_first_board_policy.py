from datetime import datetime

from quantx_domain.trading.first_board_policy import (
  FirstBoardEntryPolicy,
  FirstBoardExitPolicy,
  FirstBoardMarketSnapshot,
  build_first_board_exit_plan,
  evaluate_first_board_market_signal,
)


def test_first_board_market_signal_is_the_shared_market_only_contract() -> None:
  snapshot = FirstBoardMarketSnapshot(
    instrument_code="600000.SH",
    timestamp=datetime(2026, 8, 18, 10, 0),
    price=9.99,
    limit_up=10.0,
    price_tick=0.01,
    open=9.2,
    high=9.99,
    low=9.1,
    amount=100_000_000,
    bid1_volume=50_000,
  )

  decision = evaluate_first_board_market_signal(
    snapshot,
    FirstBoardEntryPolicy(entry_distance_ticks=1),
    promotion_eligible=True,
  )

  assert decision.eligible is True
  assert decision.reason == "ELIGIBLE"
  assert decision.distance_to_limit_ticks == 1.0


def test_first_board_market_signal_fails_closed_on_missing_depth_quality() -> None:
  snapshot = FirstBoardMarketSnapshot(
    instrument_code="600000.SH",
    timestamp=datetime(2026, 8, 18, 10, 0),
    price=9.99,
    limit_up=10.0,
    data_quality="MISSING_DEPTH",
  )

  decision = evaluate_first_board_market_signal(
    snapshot,
    FirstBoardEntryPolicy(),
  )

  assert decision.eligible is False
  assert decision.reason == "data_quality_not_ok"


def test_first_board_exit_factory_contains_production_rule_order() -> None:
  template = build_first_board_exit_plan(
    plan_id="plan-1",
    account_id="research",
    instrument_code="600000.SH",
    strategy_id="strategy",
    run_id="run",
    entry_trade_date="2026-08-18",
    signal_price=9.99,
    entry_limit_up=10.0,
    promotion_model_version="model-v2",
    exit_policy_version="exit-v2",
    cvar95_loss_pct=6.8,
    policy=FirstBoardExitPolicy(),
  )

  assert [rule.strategy for rule in template.rules] == [
    "LIMIT_UP_TOUCH",
    "HARD_STOP",
    "LIMIT_UP_BREAK",
    "TRAILING_PRICE_DRAWDOWN",
    "MAX_HOLDING_DAYS",
  ]
  assert all(rule.sizing.mode.value == "ALL_REMAINING" for rule in template.rules)
  assert template.metadata["promotion_model_version"] == "model-v2"
  assert template.metadata["exit_policy_version"] == "exit-v2"
  assert template.metadata["t_plus_one_locked"] is True
