"""Tests for shared A-share trading domain behavior."""

import asyncio
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from quantx_domain.brokers.backtest import BacktestBroker
from quantx_domain.brokers.base import (
  OrderRequest,
  OrderStatus,
  OrderType,
  PriceType,
  TradeRecord,
)
from quantx_domain.strategies.base import (
  TradeIntent,
  TradeIntentDirection,
  TradeIntentType,
)
from quantx_domain.trading import (
  AshareDataContextProvider,
  AShareMarketRules,
  BucketLedger,
  ContextRiskLayer,
  DecisionTrace,
  DecisionTraceLogger,
  EnvironmentLayer,
  InstrumentMaster,
  MarketDataSnapshot,
  OrderSizer,
  PositionAdjustmentLayer,
  PositionProfileName,
  RiskAction,
  TradingRiskChecker,
)
from quantx_infrastructure.core.runtime_state_manager import RuntimeStateManager

pytestmark = pytest.mark.unit


def test_ashare_volume_normalization():
  rules = AShareMarketRules()

  assert rules.normalize_buy_volume(299) == 200
  assert rules.normalize_buy_volume(99) == 0
  assert rules.normalize_sell_volume(50, 50) == 50
  assert rules.normalize_sell_volume(50, 150) == 0
  assert rules.normalize_sell_volume(150, 150) == 150


def test_tick_stock_status_three_remains_tradeable():
  tick = SimpleNamespace(
    stock_code="688552.SH",
    time=datetime(2026, 5, 19, 10, 47, 58),
    last_price=36.31,
    open=36.0,
    high=36.5,
    low=36.0,
    volume=18_000,
    amount=65_000_000,
    stock_status=3,
    bid_price=[36.3],
    ask_price=[36.31],
    bid_vol=[10],
    ask_vol=[12],
  )

  market = MarketDataSnapshot.from_tick(tick)

  assert market.is_trading is True
  assert market.suspended is False
  assert AShareMarketRules().check_trading_status(market).ok is True


def test_tick_stock_status_one_marks_suspended():
  tick = SimpleNamespace(
    stock_code="688552.SH",
    time=datetime(2026, 5, 19, 10, 47, 58),
    last_price=36.31,
    stock_status=1,
  )

  market = MarketDataSnapshot.from_tick(tick)

  assert market.is_trading is False
  assert market.suspended is True
  result = AShareMarketRules().check_trading_status(market)
  assert result.ok is False
  assert result.code == "suspended"


def test_runtime_state_t1_and_reservations():
  state = RuntimeStateManager(run_id="run", persist_enabled=False, enable_reserve=True)
  state.update_account(cash=100_000, frozen_cash=0, total_asset=100_000)

  assert state.reserve_cash("order-buy", 10_005)
  state.apply_trade(
    TradeRecord(
      trade_id="t1",
      order_id="order-buy",
      instrument_code="000001.SZ",
      trade_type=OrderType.BUY,
      price=100.0,
      volume=100,
      amount=10_000,
      commission=5,
      trade_time=datetime(2024, 1, 2, 10, 0),
    )
  )

  position = state.get_position("000001.SZ")
  assert position["long_volume"] == 100
  assert position["available_volume"] == 0
  assert position["today_buy_volume"] == 100

  state.settle_trading_day(date(2024, 1, 3))
  assert state.get_position("000001.SZ")["available_volume"] == 100

  assert state.reserve_position("order-sell", "000001.SZ", 100)
  assert state.get_position("000001.SZ")["available_volume"] == 0
  state.release_order_resources("order-sell")
  assert state.get_position("000001.SZ")["available_volume"] == 100


def test_runtime_state_restores_order_reservation_indexes():
  state = RuntimeStateManager(run_id="run", persist_enabled=False, enable_reserve=True)
  state.update_account(cash=100_000, frozen_cash=0, total_asset=100_000)
  assert state.reserve_cash("intent-buy", 10_005)
  state.transfer_reservation("intent-buy", "broker-buy")

  restored = RuntimeStateManager(
    run_id="run",
    persist_enabled=False,
    enable_reserve=True,
  )
  restored._state = {
    **state._state,
    "custom": dict(state._state["custom"]),
  }
  restored._restore_reservation_state()

  assert restored.get_reserved_amount("broker-buy") == 10_005
  assert restored.release_cash("broker-buy") is True
  assert restored.get_account_quota()["available_cash"] == 100_000


def test_trade_intent_status_accumulates_partial_fill_volume():
  state = RuntimeStateManager(run_id="run", persist_enabled=False, enable_reserve=True)
  intent_id = "intent-partial-fill"
  state._state.setdefault("trade_intents", {})[intent_id] = {
    "id": intent_id,
    "status": "SUBMITTED",
  }

  asyncio.run(
    state.update_trade_intent_status(
      intent_id,
      "PARTIAL_FILLED",
      executed_price=10.0,
      executed_volume=600,
      accumulate_executed_volume=True,
    )
  )
  asyncio.run(
    state.update_trade_intent_status(
      intent_id,
      "FILLED",
      executed_price=10.2,
      executed_volume=400,
      accumulate_executed_volume=True,
    )
  )

  intent = state._state["trade_intents"][intent_id]
  assert intent["status"] == "FILLED"
  assert intent["executed_volume"] == 1000
  assert intent["executed_price"] == pytest.approx(10.08)


def test_bucket_ledger_applies_t1_substitution_without_mutating_real_position():
  ledger = BucketLedger(run_id="run")
  ledger.sync_position(
    "000001.SZ",
    {
      "long_volume": 1000,
      "available_volume": 1000,
      "long_avg_price": 10.0,
      "last_price": 10.0,
      "market_value": 10_000.0,
    },
  )
  assert ledger.reserve_order(
    "buy-swing",
    instrument_code="000001.SZ",
    order_type=OrderType.BUY,
    bucket="swing",
    volume=800,
    price=10.0,
    metadata={"bucket": "swing"},
  )
  ledger.apply_trade(
    TradeRecord(
      trade_id="tb",
      order_id="buy-swing",
      instrument_code="000001.SZ",
      trade_type=OrderType.BUY,
      price=10.0,
      volume=800,
      amount=8_000,
      commission=5,
      trade_time=datetime(2024, 1, 2, 10, 0),
    )
  )
  plan = {
    "enabled": True,
    "requested_bucket": "swing",
    "sell_from_buckets": [{"bucket": "core", "volume": 800}],
    "reattribute_buy_to_bucket": "core",
    "volume": 800,
    "reason": "unit_substitution",
  }
  assert ledger.reserve_order(
    "sell-swing",
    instrument_code="000001.SZ",
    order_type=OrderType.SELL,
    bucket="swing",
    volume=800,
    price=10.0,
    metadata={"bucket": "swing"},
    substitution_plan=plan,
  )
  patch = ledger.apply_trade(
    TradeRecord(
      trade_id="ts",
      order_id="sell-swing",
      instrument_code="000001.SZ",
      trade_type=OrderType.SELL,
      price=10.0,
      volume=800,
      amount=8_000,
      commission=5,
      trade_time=datetime(2024, 1, 2, 14, 0),
    )
  )

  buckets = patch.changed_buckets
  assert buckets["core"]["total_volume"] == 1000
  assert buckets["core"]["available_volume"] == 200
  assert buckets["core"]["today_buy_volume"] == 800
  assert buckets["swing"]["total_volume"] == 0
  assert "sell-swing" not in ledger.snapshot().pending_substitutions


def test_bucket_ledger_rollback_releases_reserved_bucket_inventory():
  ledger = BucketLedger(run_id="run")
  ledger.sync_position("000001.SZ", {"long_volume": 500, "available_volume": 500})
  assert ledger.reserve_order(
    "sell-core",
    instrument_code="000001.SZ",
    order_type=OrderType.SELL,
    bucket="core",
    volume=200,
    price=10.0,
    metadata={"bucket": "core"},
  )
  assert ledger.decorate_position("000001.SZ", {"long_volume": 500})[
    "core_available_volume"
  ] == 300
  patch = ledger.rollback_order("sell-core", reason="cancelled")
  assert patch.changed_buckets["core"]["available_volume"] == 500
  assert patch.changed_buckets["core"]["frozen_volume"] == 0


def test_bucket_ledger_snapshot_roundtrip_preserves_bucket_attribution():
  ledger = BucketLedger(run_id="run")
  ledger.sync_position("000001.SZ", {"long_volume": 1000, "available_volume": 1000})
  assert ledger.reserve_order(
    "buy-swing",
    instrument_code="000001.SZ",
    order_type=OrderType.BUY,
    bucket="swing",
    volume=300,
    price=10.0,
    metadata={"bucket": "swing"},
  )
  ledger.apply_trade(
    TradeRecord(
      trade_id="tb",
      order_id="buy-swing",
      instrument_code="000001.SZ",
      trade_type=OrderType.BUY,
      price=10.0,
      volume=300,
      amount=3_000,
      commission=5,
      trade_time=datetime(2024, 1, 2, 10, 0),
    )
  )

  restored = BucketLedger.from_dict(ledger.to_dict())
  buckets = restored.to_dict()["instruments"]["000001.SZ"]

  assert buckets["core"]["total_volume"] == 1000
  assert buckets["swing"]["total_volume"] == 300
  assert restored.validate_invariants(
    {"000001.SZ": {"long_volume": 1300, "available_volume": 1000}}
  ) == []


def test_runtime_state_applies_corporate_action_to_positions_and_buckets():
  state = RuntimeStateManager(run_id="run", persist_enabled=False)
  state.update_account(cash=10_000, frozen_cash=0, total_asset=20_000)
  state.update_position(
    "000001.SZ",
    long_volume=1000,
    available_volume=1000,
    long_avg_price=10.0,
    last_price=10.0,
    market_value=10_000,
  )
  assert state.reserve_bucket_order(
    "buy-swing",
    OrderRequest(
      instrument_code="000001.SZ",
      order_type=OrderType.BUY,
      price_type=PriceType.LIMIT,
      volume=200,
      price=10.0,
      metadata={"bucket": "swing"},
    ),
  )
  state.apply_trade(
    TradeRecord(
      trade_id="tb",
      order_id="buy-swing",
      instrument_code="000001.SZ",
      trade_type=OrderType.BUY,
      price=10.0,
      volume=200,
      amount=2_000,
      commission=0,
      trade_time=datetime(2024, 1, 2, 10, 0),
      metadata={"bucket": "swing"},
    )
  )

  patch = state.apply_corporate_action(
    "000001.SZ",
    volume_factor=2.0,
    cash_dividend_per_share=0.5,
    action_id="2024-bonus",
    ex_date=date(2024, 1, 3),
  )
  position = state.get_position("000001.SZ")
  buckets = position["bucket_ledger"]

  assert patch["events"][-1]["event"] == "corporate_action_applied"
  assert position["long_volume"] == 2400
  assert position["available_volume"] == 2000
  assert position["today_buy_volume"] == 400
  assert position["long_avg_price"] == pytest.approx(4.5)
  assert buckets["core"]["total_volume"] == 2000
  assert buckets["swing"]["total_volume"] == 400
  assert state.get_account()["cash"] == pytest.approx(8600)

  skipped = state.apply_corporate_action(
    "000001.SZ",
    volume_factor=2.0,
    action_id="2024-bonus",
  )
  assert skipped["events"][0]["event"] == "corporate_action_skipped"


def test_decision_trace_logger_records_full_audit_shape():
  logger = DecisionTraceLogger(max_records=2)
  trace = DecisionTrace.from_decision(
    run_id="run",
    strategy_id="strategy",
    instrument_code="000001.SZ",
    input_summary={"trace_id": "trace-1"},
    environment={"market_state": "NORMAL"},
    risk_caps={"allow_buy": True},
    trade_intents=[{"intent_id": "i1"}],
    order_draft={"sized_volume": 100},
    risk_decision={"action": "ALLOW"},
    broker_report={"status": "FILLED"},
  )
  logger.record(trace)
  record = logger.to_list()[0]
  assert record["trace_id"] == trace.trace_id
  assert record["environment"]["market_state"] == "NORMAL"
  assert record["broker_report"]["status"] == "FILLED"


def test_risk_checker_rejects_limit_and_t1_sell():
  checker = TradingRiskChecker(strict_limit_data=True)
  account = {"available_cash": 100_000, "total_asset": 100_000}
  market = MarketDataSnapshot(
    instrument_code="000001.SZ",
    timestamp=datetime(2024, 1, 2, 10, 0),
    price=110.0,
    limit_up=110.0,
    limit_down=90.0,
  )

  buy = OrderRequest(
    instrument_code="000001.SZ",
    order_type=OrderType.BUY,
    price_type=PriceType.LIMIT,
    volume=100,
    price=111.0,
  )
  result = asyncio.run(
    checker.validate_order(buy, account=account, position={}, market_data=market)
  )
  assert not result.ok
  assert result.code == "above_limit_up"

  sell = OrderRequest(
    instrument_code="000001.SZ",
    order_type=OrderType.SELL,
    price_type=PriceType.LIMIT,
    volume=100,
    price=100.0,
  )
  result = asyncio.run(
    checker.validate_order(
      sell,
      account=account,
      position={"long_volume": 100, "available_volume": 0},
      market_data=MarketDataSnapshot(
        instrument_code="000001.SZ",
        timestamp=datetime(2024, 1, 2, 10, 0),
        price=100.0,
        limit_up=110.0,
        limit_down=90.0,
      ),
    )
  )
  assert not result.ok
  assert result.code == "insufficient_position"


def test_order_sizer_builds_order_draft_with_traceable_size_reason():
  intent = TradeIntent(
    strategy_id="s",
    run_id="r",
    instrument_code="000001.SZ",
    direction=TradeIntentDirection.BUY,
    bucket="core",
    reason="unit_buy",
    target_volume=250,
  )
  draft = OrderSizer().draft_intent(
    intent,
    OrderType.BUY,
    price=10.0,
    account={"available_cash": 100_000, "total_asset": 100_000},
  )

  assert intent.intent_type == TradeIntentType.TARGET_VOLUME
  assert draft.intent_id == intent.intent_id
  assert draft.raw_target_volume == 250
  assert draft.sized_volume == 200
  assert "BUY_LOT_NORMALIZED" in draft.size_reason_codes


def test_order_sizer_rejects_minimum_lot_above_risk_budget_without_rounding_up():
  intent = TradeIntent(
    strategy_id="s",
    run_id="r",
    instrument_code="688001.SH",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="first_board_risk_budget",
    target_position_pct=0.01,
  )

  draft = OrderSizer().draft_intent(
    intent,
    OrderType.BUY,
    price=120.0,
    account={"available_cash": 100_000, "total_asset": 100_000},
  )

  assert draft.raw_target_amount == 1_000
  assert draft.sized_volume == 0
  assert "MIN_LOT_EXCEEDS_RISK_BUDGET" in draft.size_reason_codes


def test_order_sizer_applies_liquidity_participation_before_lot_normalization():
  intent = TradeIntent(
    strategy_id="s",
    run_id="r",
    instrument_code="000001.SZ",
    direction=TradeIntentDirection.BUY,
    bucket="swing",
    reason="first_board_liquidity_budget",
    target_position_pct=0.02,
    metadata={"liquidity_cap_amount": 1_250.0},
  )

  draft = OrderSizer().draft_intent(
    intent,
    OrderType.BUY,
    price=10.0,
    account={"available_cash": 100_000, "total_asset": 100_000},
  )

  assert draft.raw_target_amount == 1_250
  assert draft.sized_volume == 100
  assert draft.sized_amount == 1_000
  assert draft.metadata["uncapped_target_amount"] == 2_000
  assert "LIQUIDITY_PARTICIPATION_CAP" in draft.size_reason_codes


def test_order_risk_decision_caps_buy_by_context_caps():
  checker = TradingRiskChecker()
  request = OrderRequest(
    instrument_code="000001.SZ",
    order_type=OrderType.BUY,
    price_type=PriceType.LIMIT,
    volume=1000,
    price=10.0,
  )
  decision = asyncio.run(
    checker.evaluate_order(
      request,
      account={"available_cash": 100_000, "total_asset": 100_000},
      position={"long_volume": 3000, "available_volume": 3000},
      market_data=MarketDataSnapshot(
        instrument_code="000001.SZ",
        timestamp=datetime(2024, 1, 2, 10, 0),
        price=10.0,
        limit_up=11.0,
        limit_down=9.0,
      ),
      risk_caps={
        "max_position_pct": 0.35,
        "risk_tags": ["position_cap"],
      },
    )
  )

  assert decision.allowed
  assert decision.action == RiskAction.CAP
  assert decision.original_volume == 1000
  assert decision.final_volume == 500
  assert decision.reason_code == "POSITION_LIMIT_CAP"
  assert decision.metadata["cap_source"] == "MAX_POSITION_PCT"
  assert "position_cap" in decision.risk_tags


def test_order_risk_decision_rejects_buy_when_only_reduce():
  checker = TradingRiskChecker()
  request = OrderRequest(
    instrument_code="000001.SZ",
    order_type=OrderType.BUY,
    price_type=PriceType.LIMIT,
    volume=100,
    price=10.0,
  )
  decision = asyncio.run(
    checker.evaluate_order(
      request,
      account={"available_cash": 100_000, "total_asset": 100_000},
      position={},
      market_data=MarketDataSnapshot(
        instrument_code="000001.SZ",
        timestamp=datetime(2024, 1, 2, 10, 0),
        price=10.0,
        limit_up=11.0,
        limit_down=9.0,
      ),
      risk_caps={"only_reduce_position": True},
    )
  )

  assert not decision.allowed
  assert decision.action == RiskAction.REJECT
  assert decision.reason_code == "ONLY_REDUCE_POSITION"


def test_order_risk_layer_rejects_missing_market_data_when_strict():
  request = OrderRequest(
    instrument_code="000001.SZ",
    order_type=OrderType.BUY,
    price_type=PriceType.LIMIT,
    volume=100,
    price=10.0,
  )

  decision = asyncio.run(
    TradingRiskChecker(strict_market_data=True).evaluate_order(
      request,
      account={"available_cash": 20_000},
      position={},
      market_data=None,
    )
  )

  assert decision.allowed is False
  assert decision.reason_code == "MISSING_MARKET_DATA"


def test_order_risk_layer_rejects_missing_limit_data_when_strict():
  request = OrderRequest(
    instrument_code="000001.SZ",
    order_type=OrderType.BUY,
    price_type=PriceType.LIMIT,
    volume=100,
    price=10.0,
  )
  market_data = MarketDataSnapshot(
    instrument_code="000001.SZ",
    price=10.0,
    close=10.0,
  )

  strict_decision = asyncio.run(
    TradingRiskChecker(
      strict_market_data=True,
      strict_limit_data=True,
    ).evaluate_order(
      request,
      account={"available_cash": 20_000},
      position={},
      market_data=market_data,
    )
  )
  compatible_decision = asyncio.run(
    TradingRiskChecker(
      strict_market_data=True,
      strict_limit_data=False,
    ).evaluate_order(
      request,
      account={"available_cash": 20_000},
      position={},
      market_data=market_data,
    )
  )

  assert strict_decision.allowed is False
  assert strict_decision.reason_code == "MISSING_LIMIT_DATA"
  assert compatible_decision.allowed is True


def test_context_risk_layer_builds_risk_off_caps():
  caps = ContextRiskLayer().build_caps(
    portfolio_state={"account": {"total_asset": 100_000}, "positions": {}},
    market_context={"market_state": "RISK_OFF"},
    parameters={},
    instrument_code="000001.SZ",
  ).to_dict()

  assert caps["risk_mode"] == "RISK_REDUCED"
  assert caps["max_position_pct"] == 0.50
  assert caps["max_new_buy_pct_today"] == 0.04
  assert caps["max_new_buy_amount_today"] == 4_000
  assert caps["min_cash_buffer_pct"] == 0.30
  assert caps["allow_intraday_swing_buy"] is False
  assert "RISK_CONTEXT_CAP" in caps["reason_codes"]
  assert "market_risk_off" in caps["risk_tags"]


def test_environment_layer_detects_market_panic():
  snapshot = EnvironmentLayer().build_snapshot(
    instrument_code="000001.SZ",
    timestamp=datetime(2024, 1, 2, 10, 0),
    parameters={
      "environment_context": {
        "market_return_1d": -0.052,
        "market_amount_ratio": 1.65,
        "advancing_count": 300,
        "declining_count": 4400,
        "limit_down_count": 120,
      }
    },
  ).to_dict()

  assert snapshot["market_state"] == "PANIC"
  assert snapshot["breadth_state"] == "EXTREME_NEGATIVE"
  assert snapshot["context_score"] <= -0.35
  assert "market_panic" in snapshot["risk_tags"]
  assert snapshot["data_quality"] == "OK"


def test_environment_layer_detects_sector_strength_and_accumulation():
  snapshot = EnvironmentLayer().build_snapshot(
    instrument_code="000001.SZ",
    timestamp=datetime(2024, 1, 2, 10, 0),
    parameters={
      "environment_context": {
        "market_state": "RISK_ON",
        "breadth_state": "POSITIVE",
        "sector_return_20d": 0.08,
        "market_return_20d": 0.01,
        "sector_price": 105.0,
        "sector_ema60": 100.0,
        "volume_ratio": 1.55,
        "price_position": 0.20,
        "price_return_1d": 0.012,
      }
    },
  ).to_dict()

  assert snapshot["sector_state"] == "STRONG"
  assert snapshot["industry_state"] == "STRONG"
  assert snapshot["volume_structure"] == "ACCUMULATION"
  assert snapshot["context_score"] > 0


def test_environment_layer_marks_missing_market_index_insufficient_when_required():
  snapshot = EnvironmentLayer().build_snapshot(
    instrument_code="000001.SZ",
    timestamp=datetime(2024, 1, 2, 10, 0),
    market_data=MarketDataSnapshot(
      instrument_code="000001.SZ",
      timestamp=datetime(2024, 1, 2, 10, 0),
      price=10.0,
      close=10.0,
      volume=100_000,
      amount=1_000_000,
    ),
    parameters={"require_market_index": True},
  ).to_dict()

  assert snapshot["data_quality"] == "INSUFFICIENT"
  assert "missing_market_index" in snapshot["risk_tags"]

  caps = ContextRiskLayer().build_caps(
    portfolio_state={"account": {"total_asset": 100_000}, "positions": {}},
    market_context=snapshot,
    parameters={},
    instrument_code="000001.SZ",
  ).to_dict()
  assert caps["risk_mode"] == "RISK_REDUCED"


def test_data_context_provider_combines_environment_and_instrument_master():
  context = AshareDataContextProvider().build_context(
    instrument_code="000001.SZ",
    timestamp=datetime(2024, 1, 2, 10, 0),
    market_data=MarketDataSnapshot(
      instrument_code="000001.SZ",
      timestamp=datetime(2024, 1, 2, 10, 0),
      price=10.0,
      close=10.0,
      limit_up=11.0,
      limit_down=9.0,
      volume=100_000,
      amount=1_000_000,
    ),
    parameters={
      "calendar": {"is_trading_day": True},
      "sector": {"industry": "bank", "concepts": ["value"]},
      "environment_context": {"market_return_1d": -0.03},
    },
  ).to_dict()

  assert context["instrument_master"]["exchange"] == "SZ"
  assert context["market_context"]["instrument_master"]["industry"] == "bank"
  assert context["data_quality"] in {"OK", "DEGRADED"}
  assert context["source_fingerprint"]


def test_context_risk_layer_triggers_kill_switch_on_drawdown():
  caps = ContextRiskLayer().build_caps(
    portfolio_state={"account": {"total_asset": 100_000}, "positions": {}},
    market_context={"market_state": "NORMAL"},
    runtime_state={"drawdown_pct": 0.12},
    parameters={"max_drawdown_pct": 0.10},
    instrument_code="000001.SZ",
  ).to_dict()

  assert caps["kill_switch_active"] is True
  assert caps["allow_buy"] is False
  assert caps["only_reduce_position"] is True
  assert "KILL_SWITCH_TRIGGERED" in caps["reason_codes"]


def test_order_risk_layer_delays_swing_buy_in_panic():
  caps = ContextRiskLayer().build_caps(
    portfolio_state={"account": {"total_asset": 100_000}, "positions": {}},
    market_context={"market_state": "PANIC"},
    parameters={},
    instrument_code="000001.SZ",
  ).to_dict()
  request = OrderRequest(
    instrument_code="000001.SZ",
    order_type=OrderType.BUY,
    price_type=PriceType.LIMIT,
    volume=100,
    price=10.0,
    metadata={"bucket": "swing"},
  )

  decision = asyncio.run(
    TradingRiskChecker().evaluate_order(
      request,
      account={"available_cash": 100_000, "total_asset": 100_000},
      position={},
      market_data=MarketDataSnapshot(
        instrument_code="000001.SZ",
        timestamp=datetime(2024, 1, 2, 10, 0),
        price=10.0,
        limit_up=11.0,
        limit_down=9.0,
      ),
      risk_caps=caps,
    )
  )

  assert not decision.allowed
  assert decision.action == RiskAction.DELAY
  assert decision.reason_code == "RISK_CONTEXT_CAP"


def test_order_risk_layer_outputs_t1_substitution_plan():
  request = OrderRequest(
    instrument_code="000001.SZ",
    order_type=OrderType.SELL,
    price_type=PriceType.LIMIT,
    volume=800,
    price=10.0,
    metadata={"bucket": "swing"},
  )

  decision = asyncio.run(
    TradingRiskChecker().evaluate_order(
      request,
      account={"available_cash": 100_000, "total_asset": 100_000},
      position={
        "long_volume": 1000,
        "available_volume": 0,
        "swing_available_volume": 0,
        "core_available_volume": 800,
        "locked_core_available_volume": 500,
      },
      market_data=MarketDataSnapshot(
        instrument_code="000001.SZ",
        timestamp=datetime(2024, 1, 2, 10, 0),
        price=10.0,
        limit_up=11.0,
        limit_down=9.0,
      ),
      risk_caps={"allow_locked_core_substitution": False},
    )
  )

  assert decision.allowed
  assert decision.action == RiskAction.ALLOW
  assert decision.reason_code == "T1_SUBSTITUTION_APPLIED"
  assert decision.substitution_plan is not None
  assert decision.substitution_plan["sell_from_buckets"] == [
    {"bucket": "core", "volume": 800}
  ]


def test_order_risk_layer_does_not_substitute_when_exit_plan_forbids_it():
  request = OrderRequest(
    instrument_code="000001.SZ",
    order_type=OrderType.SELL,
    price_type=PriceType.LIMIT,
    volume=800,
    price=10.0,
    metadata={
      "bucket": "swing",
      "allow_t1_substitution": False,
      "t1_insufficient_action": "DELAY",
    },
  )

  decision = asyncio.run(
    TradingRiskChecker().evaluate_order(
      request,
      account={"available_cash": 100_000, "total_asset": 100_000},
      position={
        "long_volume": 1000,
        "available_volume": 0,
        "swing_available_volume": 0,
        "core_available_volume": 800,
      },
      market_data=MarketDataSnapshot(
        instrument_code="000001.SZ",
        timestamp=datetime(2024, 1, 2, 10, 0),
        price=10.0,
        limit_up=11.0,
        limit_down=9.0,
      ),
      risk_caps={},
    )
  )

  assert not decision.allowed
  assert decision.action == RiskAction.DELAY
  assert decision.reason_code == "T1_UNAVAILABLE"
  assert decision.substitution_plan is None
  assert decision.metadata["missing_volume"] == 800


def test_order_risk_layer_delays_t1_when_no_old_inventory():
  request = OrderRequest(
    instrument_code="000001.SZ",
    order_type=OrderType.SELL,
    price_type=PriceType.LIMIT,
    volume=800,
    price=10.0,
    metadata={"bucket": "swing"},
  )

  decision = asyncio.run(
    TradingRiskChecker().evaluate_order(
      request,
      account={"available_cash": 100_000, "total_asset": 100_000},
      position={
        "long_volume": 800,
        "available_volume": 0,
        "today_buy_volume": 800,
        "swing_available_volume": 0,
        "core_available_volume": 0,
      },
      market_data=MarketDataSnapshot(
        instrument_code="000001.SZ",
        timestamp=datetime(2024, 1, 2, 10, 0),
        price=10.0,
        limit_up=11.0,
        limit_down=9.0,
      ),
      risk_caps={"t1_insufficient_action": "DELAY"},
    )
  )

  assert not decision.allowed
  assert decision.action == RiskAction.DELAY
  assert decision.reason_code == "T1_UNAVAILABLE"
  assert decision.metadata["missing_volume"] == 800


def test_position_adjustment_selects_aggressive_and_respects_caps():
  profile = PositionAdjustmentLayer().build_profile(
    market_context={
      "market_state": "STABLE",
      "industry_state": "STRONG",
      "low_accumulation": True,
    },
    risk_caps={"max_position_pct": 0.6, "min_cash_buffer_pct": 0.25},
    portfolio_state={"account": {"total_asset": 100_000}, "positions": {}},
    instrument_code="000001.SZ",
  )

  assert profile.profile == PositionProfileName.AGGRESSIVE_ACCUMULATION.value
  assert profile.max_position_pct == 0.6
  assert profile.target_cash_buffer_pct == 0.25
  assert profile.allow_bucket_buy["core"] is True
  assert profile.bucket_caps["core"]["max_pct"] <= profile.max_position_pct


def test_position_adjustment_defensive_on_panic_blocks_swing_buy():
  profile = PositionAdjustmentLayer().build_profile(
    market_context={"market_state": "PANIC"},
    risk_caps={},
    portfolio_state={"account": {"total_asset": 100_000}, "positions": {}},
    instrument_code="000001.SZ",
  )

  assert profile.profile == PositionProfileName.DEFENSIVE.value
  assert profile.allow_swing_buy is False
  assert profile.allow_bucket_buy["swing"] is False
  assert profile.swing_max_pct == 0.0


def test_position_adjustment_allows_boolean_parameter_overrides():
  profile = PositionAdjustmentLayer().build_profile(
    market_context={"data_quality": "INSUFFICIENT"},
    risk_caps={},
    portfolio_state={"account": {"total_asset": 100_000}, "positions": {}},
    parameters={
      "position_profile_overrides": {
        "allow_swing_buy": True,
        "allow_swing_sell": False,
      }
    },
    instrument_code="000001.SZ",
  )

  assert profile.profile == PositionProfileName.CAUTIOUS.value
  assert profile.allow_swing_buy is True
  assert profile.allow_swing_sell is False
  assert profile.allow_bucket_buy["swing"] is True
  assert profile.allow_bucket_sell["swing"] is False


def test_position_adjustment_risk_caps_still_override_boolean_parameters():
  profile = PositionAdjustmentLayer().build_profile(
    market_context={"data_quality": "INSUFFICIENT"},
    risk_caps={"allow_buy": False},
    portfolio_state={"account": {"total_asset": 100_000}, "positions": {}},
    parameters={"position_profile_overrides": {"allow_swing_buy": True}},
    instrument_code="000001.SZ",
  )

  assert profile.allow_swing_buy is False
  assert profile.allow_bucket_buy["swing"] is False


def test_position_adjustment_only_reduce_caps_to_current_position():
  profile = PositionAdjustmentLayer().build_profile(
    market_context={"market_state": "NORMAL"},
    risk_caps={"only_reduce_position": True},
    portfolio_state={
      "account": {"total_asset": 100_000},
      "positions": {"000001.SZ": {"market_value": 30_000}},
    },
    instrument_code="000001.SZ",
  )

  assert profile.allow_core_buy is False
  assert profile.allow_swing_buy is False
  assert profile.max_position_pct == 0.3


def test_instrument_master_marks_missing_limits_as_conservative_data_quality():
  snapshot = InstrumentMaster().build_snapshot(
    instrument_code="000001.SZ",
    trading_date=date(2024, 1, 2),
    market_data={"price": 10.0},
    calendar={"is_trading_day": True},
    sector={"industry": "bank", "concepts": ["value"]},
  ).to_dict()

  assert snapshot["exchange"] == "SZ"
  assert snapshot["data_quality"] == "INSUFFICIENT"
  assert "missing_limit_price" in snapshot["risk_tags"]
  assert snapshot["industry"] == "bank"


def test_backtest_broker_blocks_limit_locked_market_orders():
  broker = BacktestBroker(initial_capital=100_000)
  asyncio.run(
    broker.update_market_data(
      "000001.SZ",
      11.0,
      datetime(2024, 1, 2, 10, 0),
      market_data=MarketDataSnapshot(
        instrument_code="000001.SZ",
        timestamp=datetime(2024, 1, 2, 10, 0),
        price=11.0,
        close=11.0,
        limit_up=11.0,
        limit_down=9.0,
      ),
    )
  )
  order = asyncio.run(
    broker.place_order(
      OrderRequest(
        instrument_code="000001.SZ",
        order_type=OrderType.BUY,
        price_type=PriceType.MARKET,
        volume=100,
        price=11.0,
      )
    )
  )

  assert order.status == OrderStatus.REJECTED
  assert broker.get_constraint_statistics()["limit_up_buy_blocked"] == 1


def test_backtest_broker_expires_short_lived_limit_order_before_next_fill():
  broker = BacktestBroker(initial_capital=100_000)
  submitted_at = datetime(2024, 1, 2, 10, 0)
  asyncio.run(
    broker.update_market_data(
      "000001.SZ",
      10.0,
      submitted_at,
      market_data=MarketDataSnapshot(
        instrument_code="000001.SZ",
        timestamp=submitted_at,
        price=10.0,
        volume=10_000,
        ask_price=[10.01],
        ask_vol=[10_000],
        limit_up=11.0,
        limit_down=9.0,
      ),
    )
  )
  order = asyncio.run(
    broker.place_order(
      OrderRequest(
        instrument_code="000001.SZ",
        order_type=OrderType.BUY,
        price_type=PriceType.LIMIT,
        volume=100,
        price=10.01,
        metadata={"order_expire_at_ms": int(submitted_at.timestamp() * 1000) + 1000},
      )
    )
  )

  asyncio.run(
    broker.update_market_data(
      "000001.SZ",
      10.01,
      datetime(2024, 1, 2, 10, 0, 2),
      market_data=MarketDataSnapshot(
        instrument_code="000001.SZ",
        timestamp=datetime(2024, 1, 2, 10, 0, 2),
        price=10.01,
        volume=10_000,
        ask_price=[10.01],
        ask_vol=[10_000],
        limit_up=11.0,
        limit_down=9.0,
      ),
    )
  )

  assert order.status == OrderStatus.EXPIRED
  assert broker.trades == []
  assert broker.pending_orders == []
  assert broker.get_constraint_statistics()["expired_orders"] == 1


def test_backtest_broker_caps_fill_to_executable_book_depth():
  broker = BacktestBroker(
    initial_capital=100_000,
    book_depth_participation_pct=0.25,
  )
  order = asyncio.run(
    broker.place_order(
      OrderRequest(
        instrument_code="000001.SZ",
        order_type=OrderType.BUY,
        price_type=PriceType.LIMIT,
        volume=1000,
        price=10.01,
      )
    )
  )

  timestamp = datetime(2024, 1, 2, 10, 0)
  asyncio.run(
    broker.update_market_data(
      "000001.SZ",
      10.01,
      timestamp,
      market_data=MarketDataSnapshot(
        instrument_code="000001.SZ",
        timestamp=timestamp,
        price=10.01,
        volume=100_000,
        ask_price=[10.01, 10.02],
        ask_vol=[200, 10_000],
        limit_up=11.0,
        limit_down=9.0,
      ),
    )
  )

  assert order.status == OrderStatus.PARTIAL_FILLED
  assert order.filled_volume == 50
  assert broker.get_constraint_statistics()["book_depth_capped_orders"] == 1
